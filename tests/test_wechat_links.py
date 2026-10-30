from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from server import Store
from knowledge_store import Knowledge, identifier
from assistant_knowledge import source_metadata
from wechat_links import FIELDS, canonical_url, download_reason, share_title, web_url


class WechatLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.raw=self.root/'collector.db'
        self.ts=int(datetime(2026,10,3,9,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        with sqlite3.connect(self.raw) as c:
            c.execute('CREATE TABLE messages(chatroom_id,local_id,sender,content,msg_time,msg_type)')
            c.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?)',[
                ('g@chatroom',1,'合成成员','https://example.test/a?utm_source=group 合成分享内容',self.ts,1),
                ('other@chatroom',2,'合成成员','https://example.test/a 同一资源另一次合成分享',self.ts+86400,1)])
        self.store=Store(self.root/'studio')
        self.radar=self.store.workspace/'radar-analysis/assistant.db';self.radar.parent.mkdir()
        with sqlite3.connect(self.radar) as c:
            c.execute('CREATE TABLE radar_links(document_id,url,date,title,chatroom_id,chat_name,sender,local_id,modified,hash,start,end,text,source_url,PRIMARY KEY(document_id,url))')
        # Obsolete path must never be opened by the new adapter.
        self.old=self.root/'old-radar.db';self.old.write_bytes(b'not a database')
        self.k=Knowledge(self.store,[{'id':'wechat','name':'原消息','kind':'wechat','backend':'collector','path':str(self.raw)},
            {'id':'wechat-links','name':'分享链接','kind':'wechat_links','message_source':'wechat','path':str(self.old)}])
        self.store._knowledge=self.k;self.k.sync('wechat')
        self.share('g@chatroom',1,self.ts,url='https://example.test/a?utm_source=group')
        self.share('other@chatroom',2,self.ts+86400)

    def share(self,chat,local,ts,**fields):
        with self.k.db() as c:doc=c.execute('SELECT * FROM documents WHERE id=?',(identifier('wechat',f'{chat}:{local}'),)).fetchone()
        url=fields.pop('url','https://example.test/a')
        body=doc['body'] if doc else url
        start=body.find(url)
        values={'document_id':identifier('wechat',f'{chat}:{local}'),'url':url,'date':'2026-10-03','title':'合成分享标题',
            'chatroom_id':chat,'chat_name':'合成群','sender':'合成成员','local_id':local,
            'modified':datetime.fromtimestamp(ts,timezone.utc).isoformat(),'hash':doc['hash'] if doc else 'missing',
            'start':max(start,0),'end':max(start,0)+len(url),'text':url,'source_url':''}
        values.update(fields)
        with sqlite3.connect(self.radar) as c:c.execute('INSERT INTO radar_links('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',list(values.values()))

    def add(self,chat,local,body,ts=None,title='合成标题',url=None):
        ts=self.ts if ts is None else ts
        with sqlite3.connect(self.raw) as c:c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(chat,local,'合成成员',body,ts,1))
        self.k.sync('wechat');self.share(chat,local,ts,title=title,url=url or body.split()[0])

    def details(self):
        with self.k.db() as c:return c.execute("SELECT detail FROM sources WHERE id='wechat-links'").fetchone()[0]

    def metrics(self):return json.loads(self.details().split('；')[-1])

    def test_existing_share_records_are_readonly_versioned_and_not_page_content(self):
        before=[p.read_bytes() for p in [self.raw,self.radar,self.old]]
        with patch('urllib.request.urlopen',side_effect=AssertionError('不得抓网页')):
            self.k.sync('wechat-links');self.k.sync('wechat-links')
        self.assertEqual(before,[p.read_bytes() for p in [self.raw,self.radar,self.old]])
        results=self.k.search(source='wechat-links')['results'];self.assertEqual(len(results),2)
        self.assertNotEqual(results[0]['id'],results[1]['id'])
        doc=self.k.document(results[0]['id']);self.assertEqual(doc['kind'],'wechat_links')
        self.assertIn('外部网页正文未读取',doc['body']);self.assertIn('分享不代表本人认可或需要行动',doc['body']);self.assertIn('新增仅群消息',self.details())
        self.assertNotIn('utm_source',doc['body'])
        p=doc['provenance'];record_hash=hashlib.sha256(json.dumps(p['source_record'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        self.assertEqual(p['source_record_hash'],record_hash);self.assertIn(record_hash,doc['body'])
        ref=p['original_message_reference'];raw=self.k.document(ref['id'])
        self.assertEqual(ref['hash'],raw['hash']);self.assertEqual(raw['body'][ref['start']:ref['end']],p['source_record']['text'])
        self.assertNotIn('source_record',source_metadata(p));self.assertFalse(self.store.events.list_events()['events'])
        self.assertEqual(self.metrics()['counts']['duplicate'],2)

    def test_history_is_unchanged_and_same_share_key_is_not_rewritten(self):
        key=json.dumps(['g@chatroom',1,'https://example.test/a'],ensure_ascii=False,separators=(',',':'))
        with self.k.db() as c:
            c.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            self.k._write(c,'wechat-links',key,'wechat_links','旧标题','旧正文','https://example.test/a','2026-10-03T01:00:00+00:00',[],{'legacy':True})
            self.k._write(c,'wechat-links','unrelated-legacy','wechat_links','旧私聊标题','旧私聊正文','https://old.test/share','2026-10-01T01:00:00+00:00',[],{'legacy':True})
            before=[dict(r) for r in c.execute("SELECT * FROM documents WHERE source='wechat-links' ORDER BY id")]
        self.k.sync('wechat-links');self.k.sync('wechat-links')
        with self.k.db() as c:
            for old in before:self.assertEqual(dict(c.execute('SELECT * FROM documents WHERE id=?',(old['id'],)).fetchone()),old)
        self.assertEqual(self.k.search(source='wechat-links')['total'],3)
        self.assertEqual(self.metrics()['counts']['duplicate'],2)

    def test_scope_and_superseded_projection_never_delete_existing_history(self):
        self.k.sync('wechat-links');before=self.k.search(source='wechat-links')['results']
        self.k.configure_source({'id':'wechat','chatroom_ids':['g@chatroom']});self.k.sync('wechat');self.k.sync('wechat-links')
        self.assertEqual(self.k.search(source='wechat-links')['results'],before)
        self.assertEqual(self.metrics()['counts']['unmatched'],1)
        with sqlite3.connect(self.radar) as c:c.execute('DELETE FROM radar_links')
        self.k.sync('wechat-links');self.assertEqual(self.k.search(source='wechat-links')['results'],before)

    def test_unmatched_hash_time_span_identity_and_private_are_counted(self):
        self.share('missing@chatroom',9,self.ts)
        self.add('private',3,'https://private.test/a')
        self.add('hash@chatroom',4,'https://example.test/hash')
        self.add('time@chatroom',5,'https://example.test/time')
        self.add('span@chatroom',6,'https://example.test/span')
        with sqlite3.connect(self.radar) as c:
            c.execute("UPDATE radar_links SET hash='stale' WHERE local_id=4")
            c.execute('UPDATE radar_links SET modified=? WHERE local_id=5',(datetime.fromtimestamp(self.ts+1,timezone.utc).isoformat(),))
            c.execute('UPDATE radar_links SET end=end+1 WHERE local_id=6')
        self.k.sync('wechat-links')
        self.assertEqual(self.k.search(source='wechat-links')['total'],2)
        self.assertEqual(self.metrics()['counts']['unmatched'],4);self.assertEqual(self.metrics()['counts']['private'],1)

    def test_pause_or_missing_projection_preserves_saved_evidence(self):
        self.k.sync('wechat-links');before=self.k.search(source='wechat-links')['results'];doc=self.k.document(before[0]['id'])
        dossier=self.k.save_dossier({'title':'合成分享资料','items':[{'id':doc['id'],'hash':doc['hash'],'start':0,'end':len(doc['body'])}]})
        self.k.configure_source({'id':'wechat','enabled':False})
        with patch('wechat_links.source_db',side_effect=AssertionError('暂停父来源时不读雷达')):self.k.sync('wechat-links')
        self.assertEqual(self.k.search(source='wechat-links')['results'],before)
        self.k.configure_source({'id':'wechat','enabled':True});self.k.sync('wechat');self.radar.rename(self.root/'missing.db');self.k.sync('wechat-links')
        self.assertEqual(self.k.search(source='wechat-links')['results'],before)
        self.assertEqual(self.k.dossier(dossier['id'])['items'][0]['excerpt'],doc['body'])

    def test_invalid_candidates_are_counted_and_invalid_schema_preserves_history(self):
        self.k.sync('wechat-links');before=self.k.search(source='wechat-links')['results']
        for url in ['javascript:alert(1)','https://name:password@example.test/a','http://example.test:99999/a']:
            with self.subTest(url=url):
                with sqlite3.connect(self.radar) as c:c.execute('UPDATE radar_links SET url=? WHERE local_id=1',(url,))
                self.k.sync('wechat-links');self.assertEqual(self.k.search(source='wechat-links')['results'],before)
                self.assertEqual(self.metrics()['counts']['invalid_url'],1)
        with sqlite3.connect(self.radar) as c:c.execute('ALTER TABLE radar_links RENAME TO old_links')
        self.k.sync('wechat-links');self.assertEqual(self.k.search(source='wechat-links')['results'],before)
        self.assertEqual(next(s for s in self.k.status()['sources'] if s['id']=='wechat-links')['status'],'error')

    def test_download_filter_matches_host_and_path_boundaries(self):
        blocked=['https://wxapp.tc.qq.com/file','https://a.qpic.cn/picture','https://support.weixin.qq.com/cgi-bin/mmsupport-bin/stodownload/123']
        allowed=['https://mp.weixin.qq.com/s/article','https://wxapp.tc.qq.com.evil.test/file','https://example.test/wxapp.tc.qq.com/stodownload',
                 'https://example.test/?next=https://wxapp.tc.qq.com/file','https://support.weixin.qq.com/cgi-bin/mmsupport-bin/stodownload-example']
        for url in blocked:self.assertIsNotNone(download_reason(web_url(url)))
        for url in allowed:self.assertIsNone(download_reason(web_url(url)))
        for n,url in enumerate(blocked+allowed,10):self.add('filters@chatroom',n,url,url=url)
        self.k.sync('wechat-links');counts=self.metrics()['counts']
        self.assertEqual((counts['wechat_file'],counts['wechat_image']),(2,1))
        self.assertEqual(self.k.search(source='wechat-links')['total'],2+len(allowed))

    def test_wxid_or_empty_titles_use_existing_card_then_excerpt_then_untitled(self):
        cases=[('wxid_synthetic','<msg><title>现有网页标题</title></msg>','现有网页标题','message_card'),
            ('','合成正文开头 https://example.test/a','合成正文开头','message_excerpt'),
            ('wxid_synthetic','https://example.test/a','无标题','untitled'),
            ('','', '无标题','untitled'),('正常标题','合成正文','正常标题','projection'),
            ('WXID_SYNTHETIC','<msg><title>wxid_example</title><des>卡片已有摘要</des></msg>','卡片已有摘要','message_card')]
        for title,body,want,basis in cases:
            with self.subTest(title=title):self.assertEqual(share_title({'title':title,'url':'https://example.test/a'},body),(want,basis))
        self.add('titles@chatroom',20,'正文回落 https://example.test/title',url='https://example.test/title',title='wxid_synthetic')
        self.k.sync('wechat-links')
        self.assertEqual(self.metrics()['titles']['message_excerpt'],1)
        with self.k.db() as c:self.assertEqual(c.execute("SELECT title FROM documents WHERE source='wechat-links' AND url='https://example.test/title'").fetchone()[0],'正文回落')

    def test_canonical_rules_preserve_old_share_keys(self):
        self.assertEqual(canonical_url('https://Example.TEST/a?utm_source=x&b=2&a=1#fragment'),'https://example.test/a?a=1&b=2')
        self.assertEqual(canonical_url('https://mp.weixin.qq.com/s/slug?scene=1&x=2'),'https://mp.weixin.qq.com/s/slug')
        self.assertEqual(canonical_url('https://mp.weixin.qq.com/s?sn=abc&scene=1&mid=2&__biz=b&idx=1'),'https://mp.weixin.qq.com/s?__biz=b&mid=2&idx=1&sn=abc')

    def test_tracking_variants_in_one_message_insert_one_share(self):
        first='https://example.test/one';second=first+'?utm_source=synthetic'
        self.add('same@chatroom',40,first+' '+second,url=first)
        self.share('same@chatroom',40,self.ts,url=second)
        self.k.sync('wechat-links')
        self.assertEqual(self.k.search(source='wechat-links')['total'],3)
        self.assertEqual(self.metrics()['counts']['duplicate'],1)


if __name__=='__main__':unittest.main()
