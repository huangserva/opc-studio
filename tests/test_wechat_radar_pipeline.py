import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from wechat_radar_pipeline import RadarPipeline
from clue_pipeline import CluePipeline

class Knowledge:
    def __init__(self,path):
        self.path=path
        with self.db() as db:db.execute('CREATE TABLE documents(id TEXT PRIMARY KEY,hash TEXT,source TEXT,kind TEXT,title TEXT,body TEXT,modified TEXT,provenance TEXT,url TEXT)')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

class RadarTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=SimpleNamespace(workspace=Path(self.temp.name),knowledge=Knowledge(Path(self.temp.name)/'kb.db'))
        self.cp=CluePipeline.__new__(CluePipeline);self.cp.store=self.store;self.cp.conversation_context=lambda docs:[]
        self.store.clue_pipeline=self.cp;self.pipeline=RadarPipeline(self.store);self.pipeline.since='2026-10-06T00:00:00+08:00'
        self.doc=self.add('a','你好，模型工作流需要原文引用。 https://example.org/news',True)
    def add(self,identity,body,own=False,modified='2026-10-06T02:00:00+00:00'):
        provenance={'chatroom_id':'g@chatroom','local_id':1,'sender':'__self__' if own else '他人','is_self':own,'conversation_type':'group'}
        doc={'id':identity,'hash':identity*64,'source':'wechat','kind':'wechat','title':'群 · 作者 · 日期','body':body,'modified':modified,'provenance':provenance,'url':''}
        with self.store.knowledge.db() as db:db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)',(*[doc[k] for k in ['id','hash','source','kind','title','body','modified']],json.dumps(provenance),''))
        return doc
    def result(self,refs):
        return json.dumps({'topics':[{'title':'模型引用工作流','summary':'讨论消息引用方法','refs':refs}], 'knowledge':[{'title':'原文引用方法','summary':'保留引用位置以便核对','tags':['引用'],'refs':refs}],'profile':[{'title':'引用偏好','summary':'本片段显示重视原文引用','dimension':'tech_preferences','refs':refs}]},ensure_ascii=False)
    def test_evidence_unicode_profile_and_atomic_checkpoint(self):
        material,refs,catalog=self.pipeline.prepare([self.doc])
        findings=self.pipeline.validate(self.result(['e0']),refs,catalog,{'a'})
        self.assertEqual(findings[0]['evidence'][0]['text'],self.doc['body'])
        self.assertIn('document=a',findings[0]['evidence'][0]['url'])
        self.pipeline.commit([self.doc],findings,'run','qwen');self.pipeline.commit([self.doc],findings,'run2','qwen')
        state=self.pipeline.publish();self.assertEqual(state['processed_total'],1);self.assertEqual(state['findings'],3)
        with self.pipeline.db() as db:
            topic=db.execute('SELECT * FROM trending_topics').fetchone();self.assertEqual(topic['total_mentions'],1);self.assertEqual(topic['groups_count'],1)
            self.assertEqual(db.execute('SELECT share_count FROM trending_urls').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM todos').fetchone()[0],0)
        profile=json.loads((self.pipeline.root/'profile/demo_account_profile.json').read_text());self.assertEqual(profile['dimensions']['tech_preferences']['conclusions'][0]['evidence'][0]['hash'],self.doc['hash'])
    def test_invalid_reference_or_non_owner_does_not_advance(self):
        _,refs,catalog=self.pipeline.prepare([self.doc])
        with self.assertRaises(ValueError):self.pipeline.validate(self.result(['missing']),refs,catalog,{'a'})
        other=self.add('b','技术意见',False);_,refs,catalog=self.pipeline.prepare([other])
        result=self.pipeline.validate(self.result(['e0']),refs,catalog,{'b'})
        self.assertEqual([f['kind'] for f in result],['topics','knowledge'])
        self.assertEqual(self.pipeline.rejected_profiles,1)
        with self.pipeline.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM analyzed').fetchone()[0],0)
        self.pipeline.commit([other],result,'valid-siblings','qwen')
        with self.pipeline.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM findings WHERE kind='profile'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT non_owner_profiles FROM validation_warnings').fetchone()[0],1)
    def test_source_change_rolls_back_entire_batch(self):
        _,refs,catalog=self.pipeline.prepare([self.doc]);findings=self.pipeline.validate(self.result(['e0']),refs,catalog,{'a'})
        with self.store.knowledge.db() as db:db.execute("UPDATE documents SET hash='changed' WHERE id='a'")
        with self.assertRaises(ValueError):self.pipeline.commit([self.doc],findings,'run','qwen')
        with self.pipeline.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0],0)
    def test_no_implicit_earlier_history_or_context_checkpoint(self):
        old=self.add('b','昨天的资料',False,'2026-10-05T03:00:00+00:00')
        self.cp.conversation_context=lambda docs:[old]
        material,refs,catalog=self.pipeline.prepare([self.doc]);self.assertNotIn('b',catalog)
        self.pipeline.commit([self.doc],[],'run','qwen')
        self.assertEqual(self.pipeline.pending({'radar_since':self.pipeline.since},'qwen'),[])
    def test_worker_failure_is_not_success_and_retries_same_input(self):
        self.cp.call_pi=lambda *args:'invalid json'
        task={'radar_since':self.pipeline.since,'body':'test'};config={'model':'qwen','context_tokens':229368,'max_output_tokens':32768}
        status,code,output=self.pipeline.analyze('run',task,threading.Event(),config,time.monotonic()+30)
        self.assertEqual(status,'failed');self.assertTrue(json.loads(output)['has_more']);self.assertTrue(self.pipeline.pending(task,'qwen'))

    def test_busy_service_waits_without_sending_source_or_advancing_checkpoint(self):
        self.cp.call_pi=lambda *args:self.fail('busy backend must not receive source text')
        cancel=threading.Event();cancel.wait=lambda seconds:cancel.set()
        task={'radar_since':self.pipeline.since,'body':'test'}
        with patch('wechat_radar_pipeline.backend_slots',return_value='busy'):
            state,code,out=self.pipeline.analyze('run',task,cancel,{'model':'qwen'},time.monotonic()+30)
        self.assertEqual(state,'cancelled');self.assertEqual(json.loads(out)['processed'],0)
        self.assertTrue(self.pipeline.pending(task,'qwen'))
    def test_xml_auth_not_sent_and_no_fabricated_url_counts(self):
        doc=self.add('b','<msg><appmsg><title>公开标题</title><url>https://example.org/a</url><authkey>secret-value</authkey></appmsg></msg>')
        material,refs,catalog=self.pipeline.prepare([doc]);self.assertNotIn('secret-value',json.dumps(material));self.assertNotIn('https://example.org/a',json.dumps(material))
        self.pipeline.publish_links()
        with self.pipeline.db() as db:self.assertEqual(db.execute("SELECT share_count FROM trending_urls WHERE url='https://example.org/a'").fetchone()[0],1)

    def test_local_link_projection_preserves_encoded_xml_and_rejects_secret_urls(self):
        doc=self.add('b','<msg><appmsg><title>原始分享标题</title><url>https://example.org/a?x=1&amp;y=2</url><shareurl>https://example.org/private?token=secret</shareurl></appmsg></msg>')
        self.pipeline.publish_links()
        with self.pipeline.db() as db:
            rows=db.execute('SELECT * FROM radar_links').fetchall()
            row=next(r for r in rows if r['document_id']=='b')
            self.assertEqual(row['url'],'https://example.org/a?x=1&y=2')
            self.assertEqual(row['title'],'原始分享标题')
            self.assertEqual(doc['body'][row['start']:row['end']],row['text'])
            self.assertEqual(row['hash'],doc['hash'])
            self.assertFalse(any('token=' in r['url'] for r in rows))

    def test_digest_counts_same_message_once_for_multiple_findings(self):
        _,refs,catalog=self.pipeline.prepare([self.doc]);findings=self.pipeline.validate(self.result(['e0']),refs,catalog,{'a'})
        second={**findings[1],'title':'同一消息的另一知识点'};findings.append(second)
        self.pipeline.commit([self.doc],findings,'run','qwen');self.pipeline.publish()
        with self.pipeline.db() as db:
            digest=json.loads(db.execute('SELECT content FROM digests').fetchone()[0])
            self.assertEqual(len(digest['groups'][0]['items']),2)
            self.assertEqual(digest['groups'][0]['useful_messages'],1)

if __name__=='__main__':unittest.main()
