from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
import json
import shutil
from unittest.mock import patch
from zoneinfo import ZoneInfo

from server import Store
from knowledge_store import Knowledge, identifier


class CollectorKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.collector = self.root/'collector.db'
        self.store = Store(self.root/'studio')
        self.ts = int(datetime(2026,10,3,9,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        with sqlite3.connect(self.collector) as c:
            c.execute('CREATE TABLE messages(chatroom_id,local_id,sender,content,msg_time,msg_type)')
            c.execute('CREATE TABLE watched_chats(chatroom_id,chatroom_name)')
            c.execute('INSERT INTO watched_chats VALUES(?,?)',('g@chatroom','项目组'))
            c.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?)',[
                ('g@chatroom','1','小明','😀周五交付',self.ts,1),
                ('dm','2','__self__','周一再核对',self.ts+86400,1)])
        self.k = Knowledge(self.store,[{'id':'wechat','name':'微信','kind':'wechat','backend':'collector','path':str(self.collector),'names_path':str(self.collector)}])

    def tearDown(self):
        self.temp.cleanup()

    def test_direct_collector_idempotent_readonly_and_preserves_message_identity(self):
        before = self.collector.read_bytes()
        self.k.sync();self.k.sync()
        self.assertEqual(before,self.collector.read_bytes())
        self.assertEqual(self.k.status()['total'],2)
        d = self.k.document(identifier('wechat','g@chatroom:1'))
        self.assertEqual(d['body'],'😀周五交付')
        self.assertIn('2026-10-03 09:00:00',d['title'])
        self.assertEqual(d['provenance']['raw_local_id'],'1')
        self.assertEqual(d['provenance']['timestamp'],self.ts)
        self.assertEqual(d['provenance']['backend'],'collector')
        self.assertEqual(d['provenance']['sender'],'小明')
        self.assertIs(d['provenance']['is_self'],False)
        self.assertEqual(d['provenance']['conversation_type'],'group')
        own=self.k.document(identifier('wechat','dm:2'))
        self.assertIs(own['provenance']['is_self'],True)
        self.assertEqual(own['provenance']['conversation_type'],'unknown')
        self.assertIn('2,000',d['provenance']['content_scope'])

    def test_structured_speaker_metadata_does_not_change_original_fingerprint(self):
        self.k.sync()
        original=self.k.document(identifier('wechat','g@chatroom:1'))
        with self.k.db() as db:
            p={k:v for k,v in original['provenance'].items() if k not in ('sender','is_self','conversation_type')}
            db.execute('UPDATE documents SET provenance=? WHERE id=?',(json.dumps(p),original['id']))
        self.k.sync('wechat')
        current=self.k.document(original['id'])
        self.assertEqual(current['hash'],original['hash'])
        self.assertEqual(current['provenance']['sender'],'小明')

    def test_scope_is_inclusive_by_shanghai_day_and_bound_to_chat_ids(self):
        self.k.configure_source({'id':'wechat','date_from':'2026-10-03','date_to':'2026-10-03','chatroom_ids':['g@chatroom']})
        self.k.sync('wechat')
        self.assertEqual(self.k.status()['total'],1)
        self.assertEqual(self.k.search('周一')['total'],0)
        self.k.configure_source({'id':'wechat','date_from':'','date_to':'','chatroom_ids':['dm']})
        self.k.sync('wechat')
        self.assertEqual(self.k.status()['total'],1)
        self.assertEqual(self.k.search('周一')['total'],1)

    def test_pause_freezes_index_and_resume_refreshes(self):
        self.k.sync()
        self.k.configure_source({'id':'wechat','enabled':False})
        with sqlite3.connect(self.collector) as c:
            c.execute('UPDATE messages SET content=? WHERE local_id=?',('已取消交付','1'))
        self.k.sync()
        self.assertEqual(self.k.search('周五')['total'],1)
        self.assertEqual(self.k.status()['sources'][0]['status'],'paused')
        self.k.configure_source({'id':'wechat','enabled':True})
        self.k.sync()
        self.assertEqual(self.k.search('周五')['total'],0)
        self.assertEqual(self.k.search('取消')['total'],1)

    def test_invalid_scope_does_not_change_saved_config(self):
        original = self.k.status()['sources'][0]
        for data in [{'date_from':'2026-10-32'},{'date_from':'2026-10-05','date_to':'2026-10-03'},{'chatroom_ids':'g@chatroom'},{'enabled':'false'}]:
            with self.assertRaises(ValueError):
                self.k.configure_source({'id':'wechat',**data})
        self.assertEqual(self.k.status()['sources'][0],original)
        self.k.configure_source({'id':'wechat','chatroom_ids':["g@chatroom') OR 1=1 --"]})
        self.k.sync()
        self.assertEqual(self.k.status()['total'],0)

    def test_bad_collector_rows_roll_back_instead_of_erasing_index(self):
        self.k.sync()
        with sqlite3.connect(self.collector) as c:
            c.execute('UPDATE messages SET content=? WHERE local_id=?',(b'undecoded','1'))
        self.k.sync()
        self.assertEqual(self.k.status()['sources'][0]['status'],'error')
        self.assertEqual(self.k.search('周五')['total'],1)

    def prepare_legacy(self,paused=False,empty=False):
        self.k.sync()
        collector=self.root/'Library/Application Support/WeChatRadar/wechat-assistant/collector.db'
        collector.parent.mkdir(parents=True)
        shutil.copyfile(self.collector,collector)
        if empty:
            with sqlite3.connect(collector) as c:c.execute('DELETE FROM messages')
        legacy={'id':'wechat','name':'微信','kind':'wechat','path':str(self.root/'Library/Application Support/WeChatRadar/radar/radar.db'),'enabled':not paused}
        with self.k.db() as c:c.execute("UPDATE sources SET config=?,status=? WHERE id='wechat'",(json.dumps(legacy),'paused' if paused else 'ready'))
        return collector

    def test_empty_collector_does_not_auto_migrate_or_clear_previous_index(self):
        self.prepare_legacy(empty=True)
        with patch('knowledge_store.Path.home',return_value=self.root):reopened=Knowledge(self.store)
        source=next(s for s in reopened.status()['sources'] if s['id']=='wechat')
        self.assertNotEqual(source.get('backend'),'collector')
        self.assertEqual(reopened.status()['total'],2)

    def test_migration_preserves_pause_and_old_document_ids(self):
        collector=self.prepare_legacy(paused=True)
        with patch('knowledge_store.Path.home',return_value=self.root):reopened=Knowledge(self.store)
        source=next(s for s in reopened.status()['sources'] if s['id']=='wechat')
        self.assertEqual(source['backend'],'collector');self.assertEqual(source['status'],'paused')
        self.assertEqual(source['path'],str(collector))
        self.assertEqual(reopened.document(identifier('wechat','g@chatroom:1'))['body'],'😀周五交付')

if __name__=='__main__':
    unittest.main()
