import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest

from server import Store
from publication_metrics import PublicationMetrics, SOURCE
from knowledge_store import Knowledge


class PublicationMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
        self.work=self.store.create({'title':'合成发布复盘','type':'post'});self.slug=self.work['slug']
        self.delivery=self.store.record_delivery(self.slug,{'platform':'x','version':'v0001','url':'https://x.com/test/status/123'})
        self.store._knowledge=Knowledge(self.store,sources=[])
        self.metrics=PublicationMetrics(self.store)
        self.when=datetime.now(timezone.utc)-timedelta(hours=1)

    def tearDown(self):self.temp.cleanup()

    def payload(self):
        return {'slug':self.slug,'delivery_id':self.delivery['id'],'platform':'x','version':'v0001',
                'collected_at':self.when.isoformat(),
                'range':{'kind':'interval','start':(self.when-timedelta(days=1)).isoformat(),'end':self.when.isoformat(),
                         'label':'合成数据；平台原始曝光与互动次数，区间统计'},
                'metrics':{'impressions':1200,'likes':0,'engagement_rate':'1.25%'},
                'provenance':{'type':'manual','url':'https://analytics.x.com/content','note':'仅合成测试，不是真实账号统计'}}

    def csv_payload(self):
        payload=self.payload();del payload['metrics']
        raw='Post permalink,impressions,likes,Notes\r\nhttps://twitter.com/test/status/123,"1,200",0,"两行说明\r\n合成数据"\r\n'
        payload['provenance']={'type':'csv','filename':'真实结构的合成.csv','text':raw,'row':1,
                               'url_column':'Post permalink','metric_columns':['impressions','likes']}
        return payload

    def test_empty_and_export_only_library_has_no_fake_published_item(self):
        other=Store(self.temp.name+'/empty');service=PublicationMetrics(other)
        self.assertEqual(service.bindings()['bindings'],[]);self.assertEqual(service.listing()['snapshots'],[])
        exported=self.store.export_delivery(self.slug,{'platform':'x','version':'v0001'})
        self.assertEqual(len(self.metrics.bindings()['bindings']),1)
        payload=self.payload();payload['delivery_id']=exported['url'].split('/')[-1][:-4]
        with self.assertRaisesRegex(ValueError,'导出记录'):self.metrics.import_snapshot(payload)

    def test_fixed_publication_version_not_latest_version_and_idempotent_import(self):
        self.store.save(self.slug,{'base_version':'v0001','title':'已修改为第二版'})
        result=self.metrics.import_snapshot(self.payload())
        self.assertEqual(result['binding']['version'],'v0001');self.assertEqual(result['binding']['title'],'合成发布复盘')
        self.assertEqual(result['metrics'],self.payload()['metrics']);self.assertFalse(result['platform_verified'])
        self.assertNotIn('published_at',result);self.assertIn('登记时间不是实际发布时间',result['notice'])
        self.assertEqual(self.metrics.import_snapshot(self.payload())['id'],result['id'])
        self.assertEqual(self.metrics.listing()['total'],1)
        for changes in [{'version':'v0002'},{'platform':'wechat'},{'delivery_id':'a'*32},{'version':None},{'slug':'../bad'}]:
            payload=self.payload();payload.update(changes)
            with self.assertRaises((ValueError,OSError)):self.metrics.import_snapshot(payload)
        self.assertEqual(self.metrics.listing()['total'],1)

    def test_csv_preserves_raw_file_hash_rows_and_original_metric_strings(self):
        payload=self.csv_payload();original=payload['provenance']['text'];snapshot=self.metrics.import_snapshot(payload)
        origin=snapshot['provenance'];self.assertEqual(origin['text'],original)
        self.assertEqual(origin['sha256'],hashlib.sha256(original.encode()).hexdigest())
        self.assertEqual((origin['line_start'],origin['line_end']),(2,3))
        self.assertEqual(snapshot['metrics'],{'impressions':'1,200','likes':'0'})
        listing=self.metrics.listing()['snapshots'][0]
        self.assertNotIn('text',listing['provenance']);self.assertNotIn('selected_row',listing['provenance'])
        self.assertEqual(self.metrics.snapshot(snapshot['id'])['provenance']['text'],original)
        self.assertEqual(os.stat(self.metrics.path).st_mode&0o777,0o600)

    def test_csv_cannot_guess_binding_override_metrics_or_silently_drop_columns(self):
        changes=[{'text':self.csv_payload()['provenance']['text'].replace('/123','/456')},
                 {'metric_columns':['missing']},{'url_column':'Notes'},{'row':2},
                 {'filename':'../stats.csv'},{'text':'url,url\nhttps://x.com/test/status/123,0'},
                 {'text':'Post permalink,impressions,likes\nhttps://x.com/test/status/123,0'},
                 {'row':True},{'metric_columns':['likes','likes']}]
        for change in changes:
            payload=self.csv_payload();payload['provenance'].update(change)
            with self.assertRaises(ValueError):self.metrics.import_snapshot(payload)
        payload=self.csv_payload();payload['metrics']={'fake':9}
        with self.assertRaisesRegex(ValueError,'替代值'):self.metrics.import_snapshot(payload)
        self.assertEqual(self.metrics.listing()['total'],0)

    def test_stats_time_window_metrics_and_source_validation(self):
        invalid=[{'collected_at':'2026-10-01'}, {'collected_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()},
                 {'metrics':{}},{'metrics':{'like':None}},{'metrics':{'like':True}},{'metrics':{'like':float('nan')}},
                 {'metrics':{'like':-10**1000}},{'metrics':{'like':10**1000}}, {'metrics':{'like':{'nested':2}}},
                 {'provenance':{'type':'manual','url':'https://evil.example/analytics'}},
                 {'provenance':{'type':'manual','url':'https://name:secret@x.com/analytics'}},
                 {'provenance':{'type':'manual','url':'https://x.com/analytics?token=secret'}},
                 {'range':{'kind':'interval','end':self.when.isoformat(),'label':'区间'}},
                 {'range':{'kind':'cumulative','end':(self.when+timedelta(hours=1)).isoformat(),'label':'累计'}}]
        for change in invalid:
            payload=self.payload();payload.update(change)
            with self.assertRaises(ValueError):self.metrics.import_snapshot(payload)
        payload=self.payload();payload['range']={'kind':'cumulative','start':'','end':self.when.isoformat(),'label':'累计；实际起点未提供'}
        result=self.metrics.import_snapshot(payload);self.assertEqual(result['range']['start'],'')
        self.assertEqual(result['metrics']['likes'],0)
        payload=self.payload();payload['metrics']={'净增粉丝':-3}
        self.assertEqual(self.metrics.import_snapshot(payload)['metrics'],{'净增粉丝':-3})

    def test_wechat_csv_links_must_keep_article_identifying_query(self):
        delivery=self.store.record_delivery(self.slug,{'platform':'wechat','version':'v0001',
            'url':'https://mp.weixin.qq.com/s?__biz=abc&mid=123&idx=1&sn=xyz'})
        payload=self.csv_payload();payload.update(platform='wechat',delivery_id=delivery['id'])
        payload['provenance']['text']='url,阅读人数\nhttps://mp.weixin.qq.com/s?sn=xyz&idx=1&mid=123&__biz=abc,56\n'
        payload['provenance'].update(url_column='url',metric_columns=['阅读人数'])
        self.assertEqual(self.metrics.import_snapshot(payload)['metrics'],{'阅读人数':'56'})
        payload['provenance']['text']=payload['provenance']['text'].replace('mid=123','mid=456')
        with self.assertRaisesRegex(ValueError,'不一致'):self.metrics.import_snapshot(payload)

    def test_knowledge_export_is_explicit_idempotent_and_has_traceable_original_metrics(self):
        snapshot=self.metrics.import_snapshot(self.csv_payload())
        self.assertEqual(self.store.knowledge.status()['total'],0)
        result=self.metrics.export_to_knowledge(snapshot['id']);again=self.metrics.export_to_knowledge(snapshot['id'])
        self.assertEqual(result,again);self.assertEqual(self.store.knowledge.status()['total'],1)
        doc=self.store.knowledge.document(result['document_id'])
        self.assertEqual(doc['source'],SOURCE);self.assertEqual(doc['provenance']['version'],'v0001')
        self.assertIn('impressions：1,200',doc['body']);self.assertIn(snapshot['provenance']['sha256'],doc['body'])
        self.assertIn('数据行：1',doc['body']);self.assertIn('未向平台校验',doc['body'])
        self.assertIn(snapshot['url'],doc['body']);self.assertIn('hash='+doc['hash'],result['url'])
        self.assertNotIn('合成数据\"',doc['body']);self.assertEqual(self.metrics.snapshot(snapshot['id'])['knowledge_id'],doc['id'])
        self.store.knowledge.sync();self.assertEqual(self.store.knowledge.status()['total'],1)

    def test_concurrent_repeat_import_is_one_immutable_snapshot(self):
        ids=[];errors=[]
        def run():
            try:ids.append(self.metrics.import_snapshot(self.payload())['id'])
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=run) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join(3)
        self.assertFalse(errors);self.assertEqual(len(ids),4);self.assertEqual(len(set(ids)),1)
        self.assertEqual(self.metrics.listing()['total'],1)
        with self.assertRaises(ValueError):self.metrics.snapshot('../bad')
        with self.assertRaisesRegex(ValueError,'不存在'):self.metrics.snapshot('f'*32)


if __name__=='__main__':unittest.main()
