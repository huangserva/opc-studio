from datetime import datetime
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


class WechatLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.raw=self.root/'collector.db'
        self.radar=self.root/'radar.db'
        self.ts=int(datetime(2026,10,3,9,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        with sqlite3.connect(self.raw) as c:
            c.execute('CREATE TABLE messages(chatroom_id,local_id,sender,content,msg_time,msg_type)')
            c.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?)',[
                ('g',1,'群友','https://example.test/a?utm_source=group 分享',self.ts,1),
                ('other',2,'群友','https://example.test/a 同一资源另一次分享',self.ts+86400,1)])
        with sqlite3.connect(self.radar) as c:
            c.execute('CREATE TABLE message_links(chatroom_id,local_id,date,sender,time,timestamp,url,canonical_url,title,description,domain,source,raw_kind,confidence,created_at,PRIMARY KEY(chatroom_id,local_id,canonical_url))')
        self.share('g',1,self.ts)
        self.share('other',2,self.ts+86400)
        self.store=Store(self.root/'studio')
        self.k=Knowledge(self.store,[{'id':'wechat','name':'原消息','kind':'wechat','backend':'collector','path':str(self.raw)},
            {'id':'wechat-links','name':'分享链接','kind':'wechat_links','message_source':'wechat','path':str(self.radar)}])
        self.store._knowledge=self.k

    def tearDown(self):self.temp.cleanup()

    def share(self,chat,local,ts,**fields):
        values={'chatroom_id':chat,'local_id':local,'date':'2026-10-03','sender':'群友','time':'09:00','timestamp':ts,
            'url':'https://example.test/a?utm_source=group','canonical_url':'https://example.test/a','title':'分享时的合成卡片',
            'description':'仅是卡片描述，未读取网页','domain':'example.test','source':'wechat_raw','raw_kind':'appmsg_url','confidence':1,'created_at':self.ts}
        values.update(fields)
        with sqlite3.connect(self.radar) as c:
            c.execute('INSERT INTO message_links('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',list(values.values()))

    def test_existing_share_records_are_readonly_versioned_and_not_page_content(self):
        before=[p.read_bytes() for p in [self.raw,self.radar]]
        with patch('urllib.request.urlopen',side_effect=AssertionError('不得抓网页')):
            self.k.sync();self.k.sync('wechat-links')
        self.assertEqual(before,[p.read_bytes() for p in [self.raw,self.radar]])
        results=self.k.search(source='wechat-links')['results']
        self.assertEqual(len(results),2)
        self.assertNotEqual(results[0]['id'],results[1]['id'])
        doc=self.k.document(results[0]['id'])
        self.assertIn('外部网页正文未读取',doc['body'])
        self.assertIn('分享不代表本人认可或需要行动',doc['body'])
        self.assertEqual(doc['kind'],'wechat_links')
        self.assertNotIn('utm_source',doc['body'])
        p=doc['provenance'];record_hash=hashlib.sha256(json.dumps(p['source_record'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        self.assertEqual(p['source_record_hash'],record_hash)
        self.assertIn(record_hash,doc['body'])
        ref=p['original_message_reference'];raw=self.k.document(ref['id'])
        self.assertEqual(ref['hash'],raw['hash'])
        self.assertEqual(ref['end'],len(raw['body']))
        self.assertNotIn('source_record',source_metadata(p))
        self.assertIn('外部网页正文未读取',source_metadata(p)['content_scope'])
        self.assertEqual(source_metadata(p)['original_message_url'],p['original_message_url'])
        self.assertFalse(self.store.events.list_events()['events'])

    def test_scope_follows_indexed_messages_and_does_not_merge_same_urls(self):
        self.k.sync()
        self.k.configure_source({'id':'wechat','chatroom_ids':['g'],'date_from':'2026-10-03','date_to':'2026-10-03'})
        self.k.sync()
        self.assertEqual(self.k.search(source='wechat-links')['total'],1)
        doc=self.k.document(self.k.search(source='wechat-links')['results'][0]['id'])
        self.assertEqual(doc['provenance']['chatroom_id'],'g')
        self.assertEqual(doc['provenance']['original_message_reference']['id'],identifier('wechat','g:1'))

    def test_unmatched_wrong_time_and_non_share_resources_do_not_claim_a_message(self):
        self.share('missing',4,self.ts)
        self.share('g',1,self.ts+1,canonical_url='https://example.test/mismatch')
        self.share('g',1,self.ts,canonical_url='https://example.test/search',source='public_search')
        self.k.sync()
        self.assertEqual(self.k.search(source='wechat-links')['total'],2)
        source=next(s for s in self.k.status()['sources'] if s['id']=='wechat-links')
        self.assertIn('未匹配 2 条',source['detail'])
        self.assertIn('其它来源 1 条未导入',source['detail'])

    def test_pause_or_missing_source_preserves_last_success_and_saved_evidence(self):
        self.k.sync();before=self.k.search(source='wechat-links')['results']
        doc=self.k.document(before[0]['id'])
        dossier=self.k.save_dossier({'title':'合成分享资料','items':[{'id':doc['id'],'hash':doc['hash'],'start':0,'end':len(doc['body'])}]})
        self.k.configure_source({'id':'wechat','enabled':False})
        with patch('wechat_links.source_db',side_effect=AssertionError('暂停父来源时不读雷达')):self.k.sync('wechat-links')
        self.assertEqual(self.k.search(source='wechat-links')['results'],before)
        self.k.configure_source({'id':'wechat','enabled':True});self.k.sync('wechat')
        self.radar.rename(self.root/'missing.db');self.k.sync('wechat-links')
        self.assertEqual(self.k.search(source='wechat-links')['results'],before)
        self.assertEqual(self.k.dossier(dossier['id'])['items'][0]['excerpt'],doc['body'])

    def test_changed_source_row_changes_version_and_keeps_old_snapshot(self):
        self.k.sync()
        doc=self.k.document(self.k.search(source='wechat-links')['results'][0]['id'])
        dossier=self.k.save_dossier({'title':'旧分享快照','items':[{'id':doc['id'],'hash':doc['hash'],'start':0,'end':len(doc['body'])}]})
        with sqlite3.connect(self.radar) as c:
            c.execute('UPDATE message_links SET url=? WHERE chatroom_id=?',('https://example.test/a?utm_source=changed',doc['provenance']['chatroom_id']))
        self.k.sync('wechat-links')
        self.assertNotEqual(self.k.document(doc['id'])['hash'],doc['hash'])
        snapshot=self.k.dossier(dossier['id'])['items'][0]
        self.assertEqual(snapshot['hash'],doc['hash']);self.assertEqual(snapshot['excerpt'],doc['body'])

    def test_invalid_share_url_or_schema_rolls_back_the_entire_source(self):
        self.k.sync();before=self.k.search(source='wechat-links')['results']
        for url in ['javascript:alert(1)','https://name:password@example.test/a','http://example.test:99999/a']:
            with self.subTest(url=url):
                with sqlite3.connect(self.radar) as c:c.execute('UPDATE message_links SET canonical_url=? WHERE chatroom_id=?',(url,'g'))
                self.k.sync('wechat-links')
                self.assertEqual(self.k.search(source='wechat-links')['results'],before)
                self.assertEqual(next(s for s in self.k.status()['sources'] if s['id']=='wechat-links')['status'],'error')
        with sqlite3.connect(self.radar) as c:c.execute('ALTER TABLE message_links RENAME TO old_links')
        self.k.sync('wechat-links');self.assertEqual(self.k.search(source='wechat-links')['results'],before)


if __name__=='__main__':unittest.main()
