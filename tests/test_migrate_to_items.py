import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from scripts.migrate_to_items import migrate
from items_store import ItemsStore
from tests.test_items_store import knowledge_fixture,LONG


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='items-migrate-',dir='/tmp');self.root=Path(self.temp.name)
        self.kb=knowledge_fixture(self.root)
        c=sqlite3.connect(self.kb)
        c.execute('CREATE TABLE sources(id TEXT PRIMARY KEY)')
        c.execute("UPDATE documents SET source='external-references'")
        for ident,source,url,body in [('b'*32,'wechat','https://example.com/machine','机器语料'),('c'*32,'manual','','无链接手存'),('d'*32,'articles','/?article=own','自己的作品')]:
            c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)',(ident,source,'article',source,body,url,'2026-10-29','[]','hash','{}'))
        c.commit();c.close()
        self.intake=self.root/'inspiration/intake.db';self.intake.parent.mkdir(parents=True)
        c=sqlite3.connect(self.intake)
        c.execute('CREATE TABLE drops(id TEXT PRIMARY KEY,kind TEXT,title TEXT,text TEXT,url TEXT,document_id TEXT,reason TEXT,file_path TEXT,file_meta TEXT,meta TEXT,received_at TEXT,recognition TEXT)')
        c.execute('INSERT INTO drops VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('drop1','link','同一X','', 'https://twitter.com/fixture/status/100?s=20','a'*32,'回收理由','media/image.png','{"mime":"image/png"}','{}','2026-10-29','{}'))
        c.commit();c.close()
        self.cards=self.root/'inspiration/cards.db';c=sqlite3.connect(self.cards)
        c.execute('CREATE TABLE cards(id TEXT PRIMARY KEY,document_id TEXT,drop_id TEXT,prompt TEXT,prompt_sha TEXT,scene TEXT,note TEXT,refs TEXT,model TEXT,status TEXT,reason TEXT,created_at TEXT,updated_at TEXT)')
        refs=json.dumps([{'document_id':'a'*32,'text':LONG[:80]},{'document_id':'c'*32,'text':'无链接手存'}])
        c.execute('INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('card1','a'*32,'drop1','prompt完整😀','abc','画面','卡片笔记',refs,'model','confirmed','','2026-10-29','2026-10-29'))
        c.commit();c.close();self.store=ItemsStore(self.root)
    def tearDown(self):self.temp.cleanup()
    def hashes(self):return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.kb,self.intake,self.cards)}
    def test_old_three_databases_never_written(self):
        before=self.hashes();migrate(self.root);self.assertEqual(self.hashes(),before)
    def test_two_runs_identical_count_and_every_row_hash(self):
        a=migrate(self.root);b=migrate(self.root);self.assertEqual(a['total'],b['total']);self.assertEqual(a['item_hashes'],b['item_hashes']);self.assertEqual(a['content_hash'],b['content_hash'])
    def test_collision_merges_refs_and_full_text_exact(self):
        migrate(self.root);item=self.store.detail('x:100');self.assertEqual(item['full_text'],LONG+' agent source')
        self.assertEqual(item['legacy_refs'],[{'id':'a'*32,'store':'knowledge'},{'id':'drop1','store':'intake'}]);self.assertEqual(set(item['sources']),{'external-references','intake'})
        self.assertEqual(item['attachments'],[{'meta':{'mime':'image/png'},'path':'media/image.png'}])
    def test_only_human_sources_migrate(self):
        report=migrate(self.root);self.assertNotIn('wechat',report['counts'])
        with self.store.db() as c:self.assertFalse(c.execute('SELECT 1 FROM item_sources WHERE source=?',('wechat',)).fetchone())
    def test_legacy_refs_all_resolve_and_card_parent(self):
        migrate(self.root)
        connections={name:sqlite3.connect(path) for name,path in [('knowledge',self.kb),('intake',self.intake),('cards',self.cards)]}
        tables={'knowledge':'documents','intake':'drops','cards':'cards'}
        try:
            with self.store.db() as c:
                for r in c.execute('SELECT * FROM items'):
                    for ref in json.loads(r['legacy_refs']):self.assertTrue(connections[ref['store']].execute(f'SELECT 1 FROM {tables[ref["store"]]} WHERE id=?',(ref['id'],)).fetchone())
                    if r['kind']=='card':self.assertEqual(r['parent_id'],'x:100');self.assertTrue(c.execute('SELECT 1 FROM items WHERE id=?',(r['parent_id'],)).fetchone())
        finally:
            for c in connections.values():c.close()
    def test_dry_run_no_items_db_and_legacy_unchanged(self):
        before=self.hashes();result=migrate(self.root,True);self.assertTrue(result['dry_run']);self.assertFalse(self.store.path.exists());self.assertEqual(before,self.hashes())
    def test_missing_parent_keeps_card_evidence_and_is_idempotent(self):
        c=sqlite3.connect(self.cards);refs=json.dumps([{'document_id':'missing','text':'仅有的旧引用片段😀'}]);c.execute('UPDATE cards SET document_id=?,refs=? WHERE id=?',('missing',refs,'card1'));c.commit();c.close()
        a=migrate(self.root);b=migrate(self.root);parent=self.store.detail('kb:missing');card=self.store.detail('card:abc')
        self.assertEqual(card['parent_id'],parent['id']);self.assertFalse(parent['content_complete']);self.assertIn('原资料已缺失，仅保留引用片段',parent['full_text']);self.assertIn('仅有的旧引用片段😀',parent['full_text'])
        self.assertEqual(parent['legacy_refs'],[{'store':'cards','id':'card1'}]);self.assertEqual(parent['analysis']['missing_document_id'],'missing');self.assertEqual(a['item_hashes'],b['item_hashes']);self.assertEqual(len(a['missing_documents']),1)
    def test_human_notes_status_feedback_are_preserved_on_replay(self):
        migrate(self.root);self.store.action('x:100','note',{'note':'迁移后本人修改'});self.store.action('x:100','use');migrate(self.root)
        item=self.store.detail('x:100');self.assertEqual(item['note'],'迁移后本人修改');self.assertEqual(item['status'],'used')

if __name__=='__main__':unittest.main()
