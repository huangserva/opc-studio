import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
import uuid
from datetime import datetime,timedelta,timezone
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import Mock,patch
from server import Store,Handler
from knowledge_store import Knowledge
from deals_store import Deals,DealConflict,amount

class DealsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.store._knowledge=Knowledge(self.store,sources=[]);self.deals=self.store.deals
    def tearDown(self):self.tmp.cleanup()
    def key(self):return uuid.uuid4().hex
    def create(self,**kw):
        return self.deals.save(dict(title='合成合作',customer='合成客户',request_key=self.key(),**kw))
    def receipt(self,d,amount='100',currency='CNY',paid_on='2026-10-01',**kw):
        return self.deals.child(d['id'],'receipt',dict(revision=d['revision'],request_key=self.key(),amount=amount,currency=currency,paid_on=paid_on,**kw))
    def edit(self,d,**kw):return self.deals.save(dict(id=d['id'],revision=d['revision'],request_key=self.key(),**kw))
    def test_payment_stage_and_fulfillment_are_independent(self):
        d=self.create(stage='writing',quote='1000',currency='CNY',quote_confirmed=True)
        d=self.receipt(d,'400');self.assertEqual(d['cash']['status'],'partial');self.assertEqual(d['stage'],'writing')
        d=self.edit(d,stage='published');self.assertEqual(d['cash']['outstanding'],'600.00')
        d=self.receipt(d,'600');self.assertEqual(d['cash']['status'],'settled')
        self.assertEqual(d['cash']['totals']['CNY'],'1000.00');self.assertEqual(d['stage'],'published')
    def test_business_progress_counts_keep_existing_waiting_writing_and_do_not_invent_cash(self):
        self.create(stage='writing');self.create(stage='in_progress');self.create(stage='payment_pending')
        self.create(stage='cancelled');self.create(stage='ready',archived=True)
        listing=self.deals.listing(stage='payment_pending',q='合作')
        self.assertEqual([d['stage'] for d in listing['deals']],['payment_pending'])
        self.assertEqual(listing['stage_counts']['writing'],1);self.assertEqual(listing['stage_counts']['in_progress'],1)
        self.assertEqual(listing['stage_counts']['cancelled'],1);self.assertEqual(listing['stage_counts']['ready'],0)
        self.assertEqual(listing['summary']['received'],{'CNY':'0.00','USD':'0.00'})
        self.assertEqual(self.deals.options()['stages']['writing'],'待写')
        self.assertEqual(self.deals.options()['stage_groups']['发布结账'][-2:],('payment_pending','settled'))
    def test_closed_business_progress_requires_cash_confirmation_and_void_reopens_payment(self):
        d=self.create(stage='payment_pending',quote='100',currency='CNY',quote_confirmed=True)
        with self.assertRaisesRegex(ValueError,'确认全部到账'):self.edit(d,stage='settled')
        d=self.receipt(d);d=self.edit(d,stage='settled')
        d=self.deals.child(d['id'],'void',dict(revision=d['revision'],request_key=self.key(),kind='receipt',child_id=d['receipts'][0]['id'],reason='录入错误'))
        self.assertEqual(d['stage'],'payment_pending');self.assertEqual(d['cash']['status'],'unpaid')
        declared=self.create(stage='settled',settled=True)
        self.assertFalse(declared['receipts']);self.assertEqual(self.deals.listing(month='2026-10')['summary']['receipt_count'],0)
    def test_months_currency_and_archive_never_erase_cash(self):
        d=self.create(stage='closed',signed_on='2026-09-30',completed_on='2026-10-02')
        d=self.receipt(d,'120',paid_on='2026-09-30');d=self.receipt(d,'350',currency='USD');d=self.receipt(d,'80',paid_on='2026-10-31')
        d=self.edit(d,archived=True)
        s=self.deals.listing(month='2026-10')['summary']
        self.assertEqual(s['received'],{'CNY':'80.00','USD':'350.00'});self.assertEqual(s['signed'],0);self.assertEqual(s['closed'],1)
        self.assertEqual(self.deals.listing(month='2026-09')['summary']['received']['CNY'],'120.00')
        self.assertEqual(len(self.deals.listing()['deals']),0)
    def test_unknowns_and_declared_settlement_do_not_invent_income(self):
        d=self.create(stage='draft',settled=True)
        s=self.deals.listing(month='2026-10')['summary']
        self.assertEqual(s['signed'],0);self.assertEqual(s['received'],{'CNY':'0.00','USD':'0.00'})
        self.assertIsNone(d['cash']['outstanding']);self.assertEqual(d['cash']['status'],'settled')
    def test_exchange_is_not_silently_converted(self):
        d=self.create(stage='signed',quote='100',currency='USD',quote_confirmed=True)
        d=self.receipt(d,'700','CNY');self.assertTrue(d['cash']['mixed_currency']);self.assertIsNone(d['cash']['outstanding'])
        self.assertEqual(self.deals.listing()['summary']['outstanding']['USD'],'0.00')
        d=self.edit(d,settled=True);self.assertEqual(d['cash']['status'],'settled')
    def test_void_keeps_audit_and_reverses_total(self):
        d=self.create();d=self.receipt(d,'500');r=d['receipts'][0]
        d=self.deals.child(d['id'],'void',dict(revision=d['revision'],request_key=self.key(),kind='receipt',child_id=r['id'],reason='合成输入错误'))
        self.assertEqual(d['receipts'][0]['amount'],'500.00');self.assertTrue(d['receipts'][0]['voided_at'])
        self.assertEqual(d['cash']['totals']['CNY'],'0.00');self.assertEqual(self.deals.listing()['summary']['received']['CNY'],'0.00')
        with self.deals.db() as c:self.assertEqual(json.loads(c.execute('SELECT data FROM audit WHERE deal_id=? AND revision=2',(d['id'],)).fetchone()[0])['receipts'][0]['amount'],'500.00')
    def test_retries_and_conflicts(self):
        p=dict(title='同名',customer='品牌',request_key=self.key());d=self.deals.save(p)
        self.assertEqual(self.deals.save(p)['id'],d['id']);other=self.deals.save({**p,'request_key':self.key()});self.assertNotEqual(other['id'],d['id'])
        p=dict(revision=d['revision'],request_key=self.key(),amount='100',currency='CNY',paid_on='2026-10-01')
        d=self.deals.child(d['id'],'receipt',p);retry=self.deals.child(d['id'],'receipt',{**p,'revision':d['revision']})
        self.assertEqual(len(retry['receipts']),1)
        with self.assertRaises(DealConflict):self.deals.child(d['id'],'receipt',{**p,'amount':'101'})
        with self.assertRaises(DealConflict):self.edit({**d,'revision':1},stage='closed')
    def test_money_and_date_validation(self):
        for v in [True,False,-1,1.2,'NaN','1e3','-5','1.001',{},[]]:
            with self.subTest(v=v),self.assertRaises(ValueError):amount(v)
        for kw in [dict(quote='50'),dict(quote_confirmed=True),dict(quote='50',currency='EUR'),dict(signed_on='2026-02-30'),dict(expected_deliverables=True)]:
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.create(**kw)
        d=self.create()
        for kw in [dict(paid_on=''),dict(paid_on='2026-02-30'),dict(amount='0')]:
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.receipt(d,**kw)
    def test_initial_import_preserves_raw_and_has_no_receipts(self):
        seed={'rows':[
            {'title':'合成多篇项目','text':'合成测试：计划三篇','group':'未付','count':3},
            {'title':'合成固定报价','text':'合成测试报价','group':'未付','quote':'1200','currency':'CNY','stage':'writing'},
            {'title':'合成待定报价','text':'合成测试：报价待定','group':'洽谈'},
            {'title':'合成已付声明','text':'合成测试：仅记录客户声称付款','group':'已付'},
            {'title':'合成接洽项目','text':'合成测试：初步联系','group':'洽谈','stage':'contact'}
        ],'unassigned':['合成未分配备注'],'source_url':'https://example.test/ledger'}
        path=Path(self.tmp.name)/'synthetic-seed.json';path.write_text(json.dumps(seed,ensure_ascii=False))
        with patch.dict(os.environ,{'ARTICLE_STUDIO_DEALS_SEED':str(path)}):
            r=self.deals.import_initial();self.assertEqual(self.deals.import_initial()['ids'],r['ids'])
        ds=self.deals.listing()['deals'];self.assertEqual(len(ds),5)
        self.assertTrue(all(d['needs_review'] and not d['receipts'] and not d['signed_on'] and not d['quote_confirmed'] for d in ds))
        self.assertEqual(next(d for d in ds if d['title']=='合成多篇项目')['expected_deliverables'],3)
        self.assertEqual(next(d for d in ds if d['title']=='合成固定报价')['quote'],'1200.00')
        self.assertIsNone(next(d for d in ds if d['title']=='合成待定报价')['quote'])
        self.assertEqual(next(d for d in ds if d['title']=='合成已付声明')['stage'],'unknown')
        self.assertEqual(next(d for d in ds if d['title']=='合成已付声明')['cash']['status'],'reported_paid')
        self.assertEqual(next(d for d in ds if d['title']=='合成接洽项目')['stage'],'contact')
        self.assertTrue(r['configured'] and r['imported']);self.assertEqual(r['unassigned'],seed['unassigned'])
        self.assertEqual(self.deals.listing()['summary']['received']['CNY'],'0.00')
    def test_initial_import_without_configuration_does_not_claim_a_ledger(self):
        with patch.dict(os.environ,{'ARTICLE_STUDIO_DEALS_SEED':''}),patch.dict(sys.modules,{'deals_seed':None}):r=self.deals.import_initial()
        self.assertFalse(r['configured']);self.assertFalse(r['imported']);self.assertEqual(r['count'],0)
        self.assertEqual(self.deals.listing()['deals'],[])
        with self.deals.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM imports').fetchone()[0],0)
    def test_private_deals_seed_module_is_used_when_no_env(self):
        module=types.ModuleType('deals_seed')
        module.ROWS=[{'group':'未付','title':'合成私有种子项目','text':'合成测试：私有模块','quote':'900','currency':'CNY','count':2}]
        module.UNASSIGNED=['合成私有未分配']
        with patch.dict(os.environ,{'ARTICLE_STUDIO_DEALS_SEED':''}),patch.dict(sys.modules,{'deals_seed':module}):
            r=self.deals.import_initial();self.assertEqual(self.deals.import_initial()['ids'],r['ids'])
        self.assertTrue(r['configured'] and r['imported']);self.assertEqual(r['count'],1)
        ds=self.deals.listing()['deals'];self.assertEqual([d['title'] for d in ds],['合成私有种子项目'])
        self.assertEqual(ds[0]['expected_deliverables'],2);self.assertEqual(ds[0]['quote'],'900.00')
        self.assertEqual(r['unassigned'],['合成私有未分配'])
    def test_initial_import_validates_all_rows_before_writing(self):
        path=Path(self.tmp.name)/'invalid-seed.json'
        path.write_text(json.dumps({'rows':[{'title':'合成有效记录'},{'title':'合成无效记录','stage':'invalid'}]}))
        with patch.dict(os.environ,{'ARTICLE_STUDIO_DEALS_SEED':str(path)}),self.assertRaises(ValueError):self.deals.import_initial()
        self.assertEqual(self.deals.listing()['deals'],[])
    def test_multi_delivery_fixed_version_and_publication_metrics(self):
        w=self.store.create({'title':'合成作品','type':'post'});slug=w['slug'];delivery=self.store.record_delivery(slug,{'platform':'x','version':'v0001','url':'https://x.com/test/status/123'})
        d=self.create(expected_deliverables=3)
        d=self.deals.child(d['id'],'deliverable',dict(title='第一篇',slug=slug,version='v0001',revision=d['revision'],request_key=self.key()))
        self.store.save(slug,{'base_version':'v0001','title':'第二版'})
        self.assertEqual(d['deliverables'][0]['version'],'v0001')
        d=self.deals.child(d['id'],'publication',dict(slug=slug,delivery_id=delivery['id'],published_on='2026-10-03',revision=d['revision'],request_key=self.key()))
        self.assertEqual(d['publications'][0]['version'],'v0001');self.assertIsNone(d['publications'][0]['feedback'])
        t=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        snap=self.store.feedback.import_snapshot(dict(slug=slug,delivery_id=delivery['id'],platform='x',version='v0001',collected_at=t,range={'kind':'cumulative','end':t,'label':'合成累计数据'},metrics={'浏览量':'1.2万','点赞':0},provenance={'type':'manual','url':'https://analytics.x.com/content','note':'合成测试'}))
        feedback=self.deals.detail(d['id'])['publications'][0]['feedback'];self.assertEqual(feedback['id'],snap['id']);self.assertEqual(feedback['metrics']['浏览量'],'1.2万')
        other=self.create()
        with self.assertRaises(DealConflict):self.deals.child(other['id'],'publication',dict(platform='x',url='https://twitter.com/test/status/123',account='test',revision=other['revision'],request_key=self.key()))
        d=self.deals.child(d['id'],'publication-edit',dict(child_id=d['publications'][0]['id'],published_on='2026-09-30',quality_note='待对比，不能仅按阅读量判断写作质量',revision=d['revision'],request_key=self.key()))
        self.assertEqual(self.deals.listing(month='2026-10')['summary']['published'],0)
        self.assertEqual(self.deals.listing(month='2026-09')['summary']['published'],1)
    def test_manual_publication_inherits_delivery_version_and_is_ready_for_metrics(self):
        w=self.store.create({'title':'合成图文','type':'post'});slug=w['slug'];d=self.create()
        d=self.deals.child(d['id'],'deliverable',dict(title='第一篇',slug=slug,version='v0001',revision=d['revision'],request_key=self.key()))
        p=dict(platform='x',url='https://x.com/test/status/98765',account='test',deliverable_id=d['deliverables'][0]['id'],revision=d['revision'],request_key=self.key())
        d=self.deals.child(d['id'],'publication',p);post=d['publications'][0]
        self.assertEqual(post['slug'],slug);self.assertEqual(post['version'],'v0001');self.assertTrue(post['delivery_id'])
        binding=self.store.feedback._binding(slug,post['delivery_id']);self.assertEqual(binding['account'],'test')
        self.deals.child(d['id'],'publication',p);self.assertEqual(len(self.store.delivery_records(slug)),1)
        self.assertEqual(self.deals.listing()['summary']['published'],0)
        self.assertIn('登记时间不是实际发布时间',self.store.feedback.capabilities()['notice'])

    def test_publication_retry_recovers_a_record_written_before_failed_ledger_commit(self):
        w=self.store.create({'title':'合成原帖','type':'post'});d=self.create()
        p=dict(platform='x',url='https://x.com/test/status/456',account='test',slug=w['slug'],version='v0001',revision=d['revision'],request_key=self.key())
        with patch.object(self.deals,'_write',side_effect=OSError('合成磁盘故障')):
            with self.assertRaises(OSError):self.deals.child(d['id'],'publication',p)
        self.assertEqual(self.deals.detail(d['id'])['publications'],[])
        result=self.deals.child(d['id'],'publication',p)
        self.assertEqual(len(result['publications']),1);self.assertEqual(len(self.store.delivery_records(w['slug'])),1)

    def test_external_publication_can_later_bind_a_fixed_work_without_changing_original_post(self):
        d=self.create();d=self.deals.child(d['id'],'publication',dict(platform='x',url='https://x.com/test/status/789',account='test',revision=d['revision'],request_key=self.key()))
        self.assertEqual(d['publications'][0]['delivery_id'],'')
        w=self.store.create({'title':'补齐固定作品','type':'post'})
        d=self.deals.child(d['id'],'publication-edit',dict(child_id=d['publications'][0]['id'],slug=w['slug'],version='v0001',published_on='2026-10-04',quality_note='补齐记录',revision=d['revision'],request_key=self.key()))
        self.assertEqual(d['publications'][0]['url'],'https://x.com/test/status/789')
        self.assertTrue(d['publications'][0]['delivery_id'])
        self.assertEqual(self.deals.listing(month='2026-10')['summary']['published'],1)
        self.store.save(w['slug'],{'base_version':'v0001','title':'新版本'})
        with self.assertRaisesRegex(ValueError,'固定作品版本'):
            self.deals.child(d['id'],'publication-edit',dict(child_id=d['publications'][0]['id'],slug=w['slug'],version='v0002',revision=d['revision'],request_key=self.key()))

    def test_export_is_not_publication(self):
        w=self.store.create({'title':'合成','type':'post'});export=self.store.export_delivery(w['slug'],{'platform':'x','version':'v0001'})
        d=self.create()
        with self.assertRaises(ValueError):self.deals.child(d['id'],'publication',dict(slug=w['slug'],delivery_id=export['url'].split('/')[-1][:-4],revision=d['revision'],request_key=self.key()))
    def test_manual_clue_conversion_idempotent_and_not_auto_paid(self):
        clue=dict(id='a'*32,revision=2,categories=['business'],title='客户意向',summary='需要联系，金额待核对')
        self.store._clues=Mock();self.store._clues.detail.return_value=clue
        p=dict(clue_id=clue['id'],title=clue['title'],customer='客户',request_key=self.key())
        d=self.deals.from_clue(p);self.assertEqual(d['sources'][0]['revision'],2);self.assertFalse(d['quote_confirmed'])
        self.assertEqual(self.deals.from_clue({**p,'request_key':self.key()})['id'],d['id'])
        other=self.create()
        with self.assertRaises(DealConflict):self.deals.from_clue(dict(clue_id=clue['id'],deal_id=other['id'],revision=other['revision'],request_key=self.key()))
        self.store._clues.detail.return_value={**clue,'categories':['technology']}
        with self.assertRaises(ValueError):self.deals.from_clue(p)
    def test_read_options_never_imports_inbox(self):
        self.store.listing=Mock(side_effect=AssertionError('must not scan'))
        self.assertEqual(self.deals.options()['works'],[])

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.http.store=Store(self.tmp.name)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.http.server_port)
    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.tmp.cleanup()
    def request(self,path,body=None,origin=None):
        headers={'Origin':origin or self.base,'X-Article-Studio':'1','Content-Type':'application/json'}
        try:
            with urlopen(Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)) as r:return r.status,json.load(r)
        except HTTPError as e:
            with e:return e.code,json.load(e)
    def test_routes_protect_mutations_and_conflicts(self):
        p=dict(title='合成',customer='测试',request_key=uuid.uuid4().hex)
        self.assertEqual(self.request('/api/deals',p,origin='https://other.example')[0],403)
        code,d=self.request('/api/deals',p);self.assertEqual(code,201)
        self.assertEqual(self.request('/api/deals/'+d['id'])[1]['title'],'合成')
        self.assertEqual(self.request('/api/deals',{'id':d['id'],'revision':0,'request_key':uuid.uuid4().hex,'title':'更改'})[0],409)
        self.assertEqual(self.request('/api/deals/options')[0],200)
        self.assertEqual(self.request('/api/deals?month=invalid')[0],400)
        self.assertEqual(self.request('/api/deals',{'execute':'rm -rf','request_key':uuid.uuid4().hex})[0],400)

if __name__=='__main__':unittest.main()
