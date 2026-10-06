import json
from html import escape
from pathlib import Path
import sqlite3
import tempfile
import unittest
import threading
import time
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from server import Store
from server import Handler
from knowledge_store import Knowledge


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'studio')
        self.radar = self.root / 'radar.db'
        with sqlite3.connect(self.radar) as c:
            c.execute('CREATE TABLE messages(chatroom_id,local_id,sender,content,time,timestamp,type,date)')
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', ('g', 1, '小明', '😀微信取钥修复与 V100 实测', '08:00', 1790985600, 'text', '2026-10-03'))
        self.sources = [{'id':'articles','name':'作品','kind':'articles'}, {'id':'wechat','name':'微信','kind':'wechat','path':str(self.radar)}]
        self.k = Knowledge(self.store, self.sources)

    def tearDown(self):
        self.temp.cleanup()

    def test_idempotent_update_delete_and_chinese_search(self):
        self.k.sync()
        self.k.sync()
        self.assertEqual(self.k.status()['total'], 1)
        self.assertEqual(self.k.search('取钥')['total'], 1)
        self.assertEqual(self.k.search('V100 微信')['total'], 1)
        self.assertEqual(self.k.search('V100 缺失')['total'], 0)
        with sqlite3.connect(self.radar) as c:
            c.execute('UPDATE messages SET content=?', ('更新后的项目复盘',))
        self.k.sync('wechat')
        self.assertEqual(self.k.search('V100')['total'], 0)
        self.assertEqual(self.k.search('项目复盘')['total'], 1)
        with sqlite3.connect(self.radar) as c:
            c.execute('DELETE FROM messages')
        self.k.sync('wechat')
        self.assertEqual(self.k.status()['total'], 0)

    def test_failed_import_keeps_previous_searchable_copy_and_source_read_only(self):
        original = self.radar.read_bytes()
        self.k.sync()
        self.assertEqual(original, self.radar.read_bytes())
        self.radar.rename(self.root / 'unavailable.db')
        self.k.sync('wechat')
        self.assertEqual(self.k.search('V100')['total'], 1)
        source = next(s for s in self.k.status()['sources'] if s['id']=='wechat')
        self.assertEqual(source['status'], 'error')

    def test_fixed_excerpt_and_draft_lineage_no_model_call(self):
        self.k.sync()
        d = self.k.search('微信')['results'][0]
        provenance = {'chatroom_id': 'g', 'local_id': 1, 'timestamp': 1790985600, 'database_path': str(self.radar.resolve()), 'source_locator': d['url']}
        self.assertEqual(d['provenance'], provenance)
        self.assertEqual(self.k.document(d['id'])['provenance'], provenance)
        dossier = self.k.save_dossier({'title':'微信经验','items':[{'id':d['id'],'start':1,'end':7,'hash':d['hash']}]})
        self.assertEqual(dossier['items'][0]['excerpt'], '微信取钥修复')
        self.assertEqual(dossier['items'][0]['provenance'], provenance)
        with patch.object(self.k, 'start_sync'):
            draft = self.k.draft({'id':dossier['id'],'purpose':'写一篇经验分享'})
        article = self.store.detail(draft['slug'])
        text = '\n'.join(b.get('text','') for b in article['blocks'])
        self.assertIn('微信取钥修复', text)
        self.assertNotIn('V100 实测', text)
        self.assertIn('查看原资料', text)
        refs = json.loads((self.store.article(draft['slug']) / 'knowledge-sources.json').read_text())
        self.assertEqual(refs['items'], dossier['items'])
        reopened = Knowledge(self.store, self.sources)
        self.assertEqual(reopened.dossier(dossier['id'])['items'], dossier['items'])
        reopened.sync('articles')
        indexed = reopened.search('微信取钥', kind='articles')
        self.assertEqual(indexed['total'], 1)
        self.assertEqual(indexed['results'][0]['provenance'], {'slug': draft['slug'], 'version': draft['version'], 'source_locator': indexed['results'][0]['url']})

    def test_legacy_index_and_dossier_migrate_without_rewriting_saved_evidence(self):
        self.k.sync('wechat')
        original = self.k.document(self.k.search('V100')['results'][0]['id'])
        # Fingerprint from this fixture's index before provenance was introduced.
        legacy_hash = '24fe3a2ce630f09647fef16754ebeb5fc8123e4869d8d9cc2b19021a00a006db'
        self.assertEqual(original['hash'], legacy_hash)
        dossier = self.k.save_dossier({'title': '旧资料夹', 'items': [{'id': original['id'], 'start': 1, 'end': 7}]})
        legacy_items = [{k: v for k, v in item.items() if k != 'provenance'} for item in dossier['items']]
        with self.k.db() as c:
            c.execute('ALTER TABLE documents DROP COLUMN provenance')
            c.execute('UPDATE dossiers SET content=? WHERE id=?', (json.dumps(legacy_items), dossier['id']))
        migrated = Knowledge(self.store, self.sources)
        restored = migrated.document(original['id'])
        self.assertEqual(restored['provenance'], {})
        self.assertEqual({k: v for k, v in restored.items() if k != 'provenance'}, {k: v for k, v in original.items() if k != 'provenance'})
        self.assertEqual(migrated.search('V100')['results'][0]['provenance'], {})
        self.assertEqual(migrated.dossier(dossier['id'])['items'][0]['provenance'], {})
        with patch.object(migrated, 'start_sync'):
            draft = migrated.draft({'id': dossier['id']})
        refs = json.loads((self.store.article(draft['slug']) / 'knowledge-sources.json').read_text())
        self.assertEqual(refs['items'][0]['provenance'], {})
        self.assertEqual(refs['items'][0]['excerpt'], '微信取钥修复')
        migrated.sync('wechat')
        self.assertEqual(migrated.document(original['id'])['provenance']['local_id'], 1)
        self.assertEqual(migrated.document(original['id'])['hash'], legacy_hash)
        self.assertEqual(migrated.dossier(dossier['id'])['items'][0]['provenance'], {})
        saved = migrated.save_dossier({'id': dossier['id'], 'title': dossier['title'], 'items': legacy_items})
        self.assertEqual(saved['items'][0]['hash'], legacy_hash)
        self.assertEqual(saved['items'][0]['excerpt'], '微信取钥修复')

    def test_provenance_only_change_updates_index_without_invalidating_saved_picks(self):
        self.k.sync('wechat')
        original = self.k.document(self.k.search('V100')['results'][0]['id'])
        dossier = self.k.save_dossier({'title': '已有资料夹', 'items': [{'id': original['id'], 'start': 1, 'end': 7, 'hash': original['hash']}]})
        moved = self.root / 'moved-radar.db'
        self.radar.rename(moved)
        with self.k.db() as c:
            config = json.loads(c.execute("SELECT config FROM sources WHERE id='wechat'").fetchone()[0])
            config['path'] = str(moved)
            c.execute("UPDATE sources SET config=? WHERE id='wechat'", (json.dumps(config),))
        self.k.sync('wechat')
        current = self.k.document(original['id'])
        self.assertEqual(current['provenance']['database_path'], str(moved.resolve()))
        self.assertEqual(current['hash'], original['hash'])
        self.assertEqual(self.k.dossier(dossier['id'])['items'], dossier['items'])
        saved = self.k.save_dossier({'id': dossier['id'], 'title': dossier['title'], 'items': dossier['items']})
        self.assertEqual(saved['items'][0]['excerpt'], '微信取钥修复')
        self.assertEqual(saved['items'][0]['provenance'], current['provenance'])

    def test_changed_original_does_not_silently_rewrite_saved_evidence(self):
        self.k.sync()
        d = self.k.search('微信')['results'][0]
        dossier = self.k.save_dossier({'title':'原始依据','items':[{'id':d['id'],'start':1,'end':7,'hash':d['hash']}]})
        with sqlite3.connect(self.radar) as c:
            c.execute('UPDATE messages SET content=?', ('完全不同的新消息',))
        self.k.sync('wechat')
        with self.assertRaisesRegex(ValueError,'已更新'):
            self.k.save_dossier({'id':dossier['id'],'title':dossier['title'],'items':dossier['items']})
        self.assertEqual(self.k.dossier(dossier['id'])['items'][0]['excerpt'], '微信取钥修复')

    def test_saved_old_snapshot_can_update_brief_and_build_draft_without_recapturing(self):
        self.k.sync()
        d = self.k.search('微信')['results'][0]
        dossier = self.k.save_dossier({'title': '原始依据', 'items': [{'id': d['id'], 'start': 1, 'end': 7, 'hash': d['hash']}]})
        with sqlite3.connect(self.radar) as c:
            c.execute('UPDATE messages SET content=?', ('完全不同的新消息',))
        self.k.sync('wechat')
        revised = self.k.save_dossier({'id': dossier['id'], 'title': '旧片段复盘', 'purpose': '根据保存的依据复盘'})
        self.assertEqual(revised['items'], dossier['items'])
        with patch.object(self.k, 'start_sync'):
            draft = self.k.draft({'id': dossier['id']})
        article = self.store.detail(draft['slug'])
        self.assertIn('微信取钥修复', str(article['blocks']))
        self.assertIn('knowledge.html?', str(article['blocks']))
        refs = json.loads((self.store.article(draft['slug']) / 'knowledge-sources.json').read_text())
        self.assertEqual(refs['version'], draft['version'])
        self.assertEqual(refs['items'], dossier['items'])

    def test_draft_visibly_preserves_escaped_project_locator_and_fixed_article_version(self):
        folder = self.root / 'project<&"test>'
        folder.mkdir()
        file = folder / 'notes<proof>&.md'
        file.write_text('项目实测结论。其余资料', encoding='utf-8')
        k = Knowledge(self.store, [{'id': 'project', 'name': '项目', 'kind': 'projects', 'paths': [str(folder)]}])
        k.sync('project')
        project = k.search('项目实测', source='project')['results'][0]
        created = self.store.create({'title': '可靠文章依据'})
        saved = self.store.save(created['slug'], {'base_version': created['head'], 'title': created['title'], 'blocks': [{'kind': 'text', 'html': '<p>可靠文章依据</p>'}]})
        k.sync('articles')
        article = k.search('可靠文章', source='articles')['results'][0]
        dossier = k.save_dossier({'title': '可追溯底稿', 'items': [{'id': item['id'], 'start': 0, 'end': 6, 'hash': item['hash']} for item in (project, article)]})
        self.store.save(created['slug'], {'base_version': saved['version'], 'title': created['title'], 'blocks': [{'kind': 'text', 'html': '<p>后来的修改</p>'}]})
        with patch.object(k, 'start_sync'):
            draft = k.draft({'id': dossier['id']})
        blocks = self.store.detail(draft['slug'])['blocks']
        html = '\n'.join(block.get('html', '') for block in blocks)
        text = '\n'.join(block.get('text', '') for block in blocks)
        self.assertIn('来源定位：' + str(file.resolve()), text)
        self.assertIn('原文片段：第 1–6 字', text)
        self.assertIn(escape(str(file.resolve())), html)
        self.assertNotIn('<proof>', html)
        self.assertNotIn('file://', html)
        self.assertIn('href="' + escape('http://127.0.0.1:8766' + article['url'], quote=True) + '"', html)
        refs = json.loads((self.store.article(draft['slug']) / 'knowledge-sources.json').read_text())
        self.assertEqual(refs['items'][0]['provenance'], project['provenance'])
        self.assertEqual(refs['items'][1]['provenance']['version'], saved['version'])
        self.assertEqual(refs['items'][0]['start'], 0)
        self.assertEqual(refs['items'][0]['end'], 6)

    def test_budget_and_range_reject_without_partial_dossier(self):
        d = self.k.add_note({'title':'长资料','body':'文'*12001,'url':'javascript:alert(1)'})
        self.assertEqual(d['url'],'')
        for start,end in [(0,12001),(-1,12),(1,12002)]:
            with self.assertRaises(ValueError):
                self.k.save_dossier({'title':'资料夹','items':[{'id':d['id'],'start':start,'end':end}]})
        self.assertEqual(self.k.status()['dossiers'], [])

    def test_source_filter_pagination_tags_and_literal_query(self):
        for i in range(32):
            self.k.add_note({'title':f'项目资料{i}','body':'CPU 100%_safe 正文','tags':'Agent,项目'})
        self.k.sync('wechat')
        self.assertEqual(self.k.search('100%_safe')['total'],32)
        self.assertEqual(self.k.search('%_')['total'],32)
        self.assertEqual(self.k.search('Agent',tag='Agent')['total'],0)
        result=self.k.search('项目资料',source='manual',tag='Agent')
        self.assertEqual(result['total'],32)
        self.assertEqual(result['next_offset'],30)
        self.assertEqual(len(self.k.search('项目资料',source='manual',offset=30)['results']),2)
        self.assertEqual(self.k.search('项目资料',kind='wechat')['total'],0)

    def test_project_documents_exclude_dependencies_hidden_files_and_secret_names(self):
        folder=self.root/'project';folder.mkdir()
        (folder/'README.md').write_text('项目决策：本地优先')
        for directory in ['node_modules','.git','private','work']:
            (folder/directory).mkdir();(folder/directory/'hidden.md').write_text('不应收集')
        (folder/'secret.txt').write_text('不应收集')
        (folder/'outside.md').symlink_to(self.root/'nonexistent.md')
        config={'id':'project','name':'项目','kind':'projects','paths':[str(folder)]}
        k=Knowledge(self.store,[config]);k.sync('project')
        indexed=k.search('本地优先')
        self.assertEqual(indexed['total'],1)
        file=folder/'README.md'
        self.assertEqual(indexed['results'][0]['provenance'], {'local_path': str(file.resolve()), 'relative_path': 'README.md', 'project_name': 'project', 'project_path': str(folder.resolve()), 'file_mtime': file.stat().st_mtime, 'source_locator': str(file.resolve())})
        self.assertEqual(k.document(indexed['results'][0]['id'])['provenance'], indexed['results'][0]['provenance'])
        self.assertEqual(k.search('不应收集')['total'],0)

    def test_remio_provenance_and_type_filter_are_preserved_separately(self):
        file = {'noteId': 'remio-note', 'title': '原始研究', 'noteType': 'Note', 'noteSource': 'Manual', 'sourceUrl': 'https://example.com/research', 'localPath': str(self.root/'research.md'), 'modified': '2026-10-03T10:00:00+08:00', 'collections': ['研究']}
        excluded = [{'noteId': t, 'title': t, 'noteType': t} for t in ('Mail', 'Collection', 'People', 'MAIL')]
        excluded += [
            {'noteId': 'audio', 'title': '音频转写', 'noteType': 'Note', 'noteSource': 'LocalAudioTranscript'},
            {'noteId': 'template', 'title': '预定义模板', 'noteType': 'Note', 'noteSource': 'PreDefinedMeetingMinutesTemplate'},
            {'noteId': 'unknown', 'title': '来源未标注', 'noteType': 'Note'},
        ]
        mail = {'noteId': 'remio-mail', 'title': '项目讨论', 'noteType': 'Mail', 'noteSource': 'IMAP', 'sourceUrl': 'javascript:alert(1)', 'modified': '2026-10-03T10:00:00+08:00', 'collections': ['项目']}
        k=Knowledge(self.store,[{'id': 'remio', 'name': 'remio资料', 'kind': 'notes'}, {'id': 'mail', 'name': '邮箱', 'kind': 'mail'}])
        def remio(args):
            if args[0] == 'search_notes':
                return {'results': [mail] if args[-1] == 'mail' else [file, *excluded]}
            self.assertIn(args[1], ('remio-note', 'remio-mail'))
            return {'content': '来源原文'}
        with patch.object(k, 'remio', side_effect=remio) as reads:
            k.sync('remio')
            reads.assert_any_call(['search_notes', '--limit', '-1', '--type', 'note'])
            reads.assert_any_call(['read_note', 'remio-note', '--format', 'md'])
            self.assertEqual(reads.call_count, 2)
            k.sync('mail')
        for record, source in [(file, 'remio'), (mail, 'mail')]:
            indexed=k.search(source=source)
            self.assertEqual(indexed['total'],1)
            doc=indexed['results'][0]
            expected={field: record.get(field, '') for field in ('noteId','noteType','noteSource','localPath')}
            expected.update({'sourceUrl': record['sourceUrl'] if source=='remio' else '', 'collections': record['collections'], 'source_locator': doc['url']})
            self.assertEqual(doc['provenance'], expected)
            self.assertEqual(k.document(doc['id'])['provenance'], expected)
        source = next(x for x in k.status()['sources'] if x['id'] == 'remio')
        self.assertIn('仅同步 remio 手记', source['detail'])
        self.assertIn('文件/网页等其它来源尚未接入', source['detail'])

    def test_empty_remio_mail_is_honest_and_no_network_model_is_used(self):
        k=Knowledge(self.store,[{'id':'mail','name':'邮箱','kind':'mail'}])
        with patch.object(k,'remio',return_value={'results':[],'total':0}) as remio:
            k.sync('mail')
        source=next(x for x in k.status()['sources'] if x['id']=='mail')
        self.assertEqual(source['count'],0)
        self.assertEqual(source['status'],'empty')
        self.assertIn('连接邮箱',source['detail'])
        remio.assert_called_once_with(['search_notes','--limit','-1','--type','mail'])

    def test_remio_generated_frontmatter_removed_but_markdown_body_kept(self):
        body = '# 研究结论\n\n项目正文\n\n---\n正文中的分隔线\n'
        plain = '---\n这是一段普通 Markdown\n---\n应保留全部正文\n'
        records = [{'noteId': key, 'title': key, 'noteType': 'Note', 'noteSource': 'Manual'} for key in ('generated', 'plain')]
        contents = {'generated': '---\nid: generated\ntitle: 研究结论\nnoteType: Note\nnoteSource: Manual\ncreatedAt: 2026-10-04T22:32:00+08:00\nupdatedAt: 2026-10-04T22:32:00+08:00\n---\n\n' + body, 'plain': plain}
        k=Knowledge(self.store,[{'id': 'remio', 'name': 'remio手记', 'kind': 'notes'}])
        def remio(args):
            return {'results': records} if args[0] == 'search_notes' else {'content': contents[args[1]]}
        with patch.object(k, 'remio', side_effect=remio):
            k.sync('remio')
        indexed = k.search(source='remio')['results']
        documents = {item['title']: k.document(item['id']) for item in indexed}
        self.assertEqual(documents['generated']['body'], body)
        self.assertEqual(documents['plain']['body'], plain)
        self.assertEqual(documents['generated']['provenance']['noteSource'], 'Manual')
        self.assertEqual(k.search('updatedAt', source='remio')['total'], 0)
        self.assertEqual(k.search('2026-10-04T22:32', source='remio')['total'], 0)

    def test_empty_remio_listing_does_not_claim_whole_knowledge_base_is_empty(self):
        k=Knowledge(self.store,[{'id':'remio','name':'remio资料','kind':'notes'}])
        with patch.object(k,'remio',return_value={'results':[],'total':0}) as remio:
            k.sync('remio')
        remio.assert_called_once_with(['search_notes','--limit','-1','--type','note'])
        source=next(x for x in k.status()['sources'] if x['id']=='remio')
        self.assertIn('本次手记列表未返回资料',source['detail'])
        self.assertIn('不代表 remio 知识库为空',source['detail'])
        self.assertIn('仅同步 remio 手记',source['detail'])

    def test_reads_remain_available_while_sync_writes(self):
        self.k.sync('wechat')
        opened=threading.Event()
        release=threading.Event()
        def slow_import(c,source):
            c.execute("UPDATE documents SET body='新内容' WHERE source='wechat'")
            opened.set()
            release.wait(2)
            raise ValueError('模拟同步失败')
        with patch.object(self.k,'_import',side_effect=slow_import):
            worker=threading.Thread(target=self.k.sync,args=('wechat',));worker.start()
            self.assertTrue(opened.wait(1))
            started=time.monotonic()
            self.assertEqual(self.k.search('V100')['total'],1)
            self.assertLess(time.monotonic()-started,.5)
            release.set();worker.join(2)
            self.assertFalse(worker.is_alive())

    def test_http_local_origin_and_dossier_to_versioned_draft(self):
        self.store._knowledge=self.k
        self.k.sync('wechat')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.store=self.store
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def post(route,data,origin=None):
            request=Request(base+route,data=json.dumps(data).encode(),headers={'Origin':origin or base,'Content-Type':'application/json','X-Article-Studio':'1'})
            return json.load(urlopen(request,timeout=3))
        try:
            with self.assertRaises(HTTPError) as error:
                post('/api/knowledge/dossier',{},'https://example.com')
            self.assertEqual(error.exception.code,403)
            error.exception.close()
            d=json.load(urlopen(base+'/api/knowledge/search?q=V100'))['results'][0]
            saved=post('/api/knowledge/dossier',{'title':'V100资料','purpose':'写一篇实测复盘','items':[{'id':d['id'],'start':1,'end':7,'hash':d['hash']}]})
            self.assertEqual(saved['purpose'],'写一篇实测复盘')
            with patch.object(self.k,'start_sync'):
                draft=post('/api/knowledge/draft',{'id':saved['id']})
            detail=json.load(urlopen(base+'/api/article/'+draft['slug']))
            text='\n'.join(b.get('text','') for b in detail['blocks'])
            self.assertIn('写一篇实测复盘',text)
            self.assertIn('微信取钥修复',text)
            self.assertNotIn('V100 实测',text)
            self.assertEqual(detail['head'],draft['version'])
        finally:
            server.shutdown();server.server_close();worker.join(2)


if __name__ == '__main__':
    unittest.main()
