"""Synthetic account decisions, isolated settings and loopback HTTP only."""
from contextlib import closing
import copy
from datetime import datetime,timezone
import hashlib
import http.client
import json
import logging
from pathlib import Path
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import official_accounts as O
import today_view as T
from server import Handler,ThreadingHTTPServer

class OfficialTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir='/tmp',prefix='official-test-');self.addCleanup(temp.cleanup);self.root=Path(temp.name)/'workspace';self.root.mkdir()
        self.rules=dict(include=[],exclude=[])
    def item(self,**extra):
        result=dict(id='x:1',author=dict(handle='fixture',followers=200000),metrics=dict(views=150000),author_type='official');result.update(extra);return result
    def decide(self,item=None,profile=None):return O.decision(item or self.item(),self.rules,profile or {})
    def test_breakout_requires_both_conditions_and_known_followers(self):
        for views,followers,expected in [(100000,99999,True),(99999,1,False),(150000,200000,False),(100000,100000,False),(200000,None,False),(None,100,False),(100000,0,True)]:
            with self.subTest(views=views,followers=followers):
                item=self.item(author=dict(handle='fixture',followers=followers),metrics=dict(views=views));d=self.decide(item);self.assertEqual(d['hot'],expected)
                if followers is None:self.assertIn('粉丝数缺失',d['eligibility'])
    def test_force_exclude_overrides_organization_and_grok_official(self):
        self.rules['exclude']=['fixture']
        for profile in ({'verification_type':'organization'},{}):
            d=self.decide(profile=profile);self.assertFalse(d['is_official']);self.assertEqual(d['source'],'settings_exclude')
    def test_precedence_and_author_type_case_missing_unknown(self):
        self.rules['include']=['fixture'];self.assertEqual(self.decide(self.item(author_type='person'))['source'],'settings_include')
        self.rules['include']=[];self.assertEqual(self.decide(self.item(author_type='person'),{'verification_type':'organization'})['source'],'fxtwitter_organization')
        for value,expected in [('OFFICIAL',True),(' Official ',True),('PERSON',False),(None,False),('organization',False),(42,False)]:self.assertEqual(self.decide(self.item(author_type=value))['is_official'],expected)
        self.assertFalse(self.decide(self.item(id='tool:x'))['is_official'])
    def test_rules_normalize_reject_duplicates_whitespace_and_illegal_input(self):
        original=O.read_rules(self.root);self.assertFalse(O.path_for(self.root).exists())
        saved=O.save_rules(self.root,dict(include=['@MistralAI'],exclude=['@FactoryAI'],revision=original['revision']))
        self.assertEqual(saved['include'],['mistralai']);self.assertEqual(saved['exclude'],['factoryai']);self.assertTrue(saved['history'])
        for include in ([' x'],['x '],['x\u200b'],['x/y'],['@@x'],[''],['a'*16],['@X','x']):
            with self.subTest(include=include),self.assertRaises(ValueError):O.save_rules(self.root,dict(include=include,exclude=[],revision=saved['revision']))
    def test_atomic_conflict_permissions_other_settings_and_history(self):
        folder=self.root/'settings';folder.mkdir();other=folder/'other.json';other.write_bytes(b'{"preserve":"unchanged"}')
        first=O.read_rules(self.root);saved=O.save_rules(self.root,dict(include=first['include']+['mistralai'],exclude=[],revision=first['revision']));path=O.path_for(self.root);path.chmod(0o640)
        with self.assertRaises(O.Conflict):O.save_rules(self.root,dict(include=[],exclude=[],revision=first['revision']))
        final=O.save_rules(self.root,dict(include=first['include'],exclude=[],revision=saved['revision']))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o640);self.assertEqual(other.read_bytes(),b'{"preserve":"unchanged"}');self.assertEqual(final['history'][-1]['action'],'remove_include');self.assertIn('at',final['history'][-1])
    def test_profile_read_is_nonblocking_rate_limited_deduplicated_and_expiring(self):
        release=threading.Event();entered=threading.Event();calls=[]
        def fetch(account):calls.append((account,time.monotonic()));entered.set();release.wait(2);return dict(verification_type='organization',followers=123)
        cache=O.Profiles(self.root,fetcher=fetch,network=True);self.addCleanup(release.set)
        with patch.object(O,'RATE_SECONDS',.03):
            began=time.monotonic()
            for _ in range(20):self.assertEqual(cache.lookup('fixture')['state'],'unknown')
            self.assertLess(time.monotonic()-began,.2);self.assertTrue(entered.wait(1));release.set();cache.worker.join(3)
            self.assertEqual(len(calls),1);self.assertEqual(cache.lookup('fixture')['followers'],123)
            reloaded=O.Profiles(self.root,fetcher=fetch,network=True);self.assertEqual(reloaded.lookup('fixture')['verification_type'],'organization');self.assertEqual(len(calls),1)
            cache.lookup('second');cache.lookup('third');cache.worker.join(3);self.assertGreaterEqual(calls[-1][1]-calls[-2][1],.02)
            cache.records['fixture']['expires']=0;cache.lookup('fixture');cache.worker.join(3);self.assertEqual(len(calls),4)
        self.assertEqual(set(json.loads(cache.path.read_text())['fixture']),{'state','verification_type','followers','checked_at','expires'})
    def test_profile_failures_unknown_logged_backoff_and_no_exception_secrets(self):
        for error in (TimeoutError('synthetic-hidden'),__import__('urllib.error',fromlist=['HTTPError']).HTTPError('https://api.fxtwitter.com/fixture',500,'hidden',{},None),__import__('urllib.error',fromlist=['HTTPError']).HTTPError('https://api.fxtwitter.com/fixture',429,'hidden',{},None)):
            def fetch(_):raise error
            cache=O.Profiles(self.root,fetcher=fetch,network=True);cache.records={}
            with self.assertLogs(O.LOG,level='WARNING') as logs,patch.object(O,'RATE_SECONDS',0):cache.lookup('fixture');cache.worker.join(3)
            self.assertEqual(cache.lookup('fixture')['state'],'unknown');self.assertIsNone(cache.worker);self.assertNotIn('synthetic-hidden',' '.join(logs.output));self.assertNotIn('https://',' '.join(logs.output))
    def test_profile_field_paths_not_tweet_author_or_verified_boolean(self):
        payload={'code':200,'user':{'screen_name':'Fixture','followers':123,'verification':{'type':'organization','verified':False}},'tweet':{'author':{'followers':999999,'verification':{'type':'individual'}}}}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return json.dumps(payload).encode()
        opener=SimpleNamespace(open=lambda request,timeout:Response())
        with patch.object(O.urllib.request,'build_opener',return_value=opener):self.assertEqual(O.fetch_profile('fixture'),dict(verification_type='organization',followers=123))
    def test_today_flow_counts_top_interest_unchanged_and_live_override(self):
        from test_today_view import TodayTests,NOW
        fixture=TodayTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        item=fixture.row('61');item.update(handle='FactoryAI',author_type='person',fit_score=3,doable=3,views=150000,followers=200000);fixture.write([item])
        hot=T.page_view(fixture.workspace,{'range':['7d']},NOW);self.assertNotIn('x:61',{p['id'] for p in hot['items']+hot['top']});self.assertEqual(hot['filtering']['official_diverted'],1)
        official=T.page_view(fixture.workspace,{'range':['7d'],'channel':['official']},NOW);self.assertEqual([p['id'] for p in official['items']],['x:61']);self.assertEqual(official['items'][0]['official']['source'],'settings_include');self.assertEqual(official['top'],[])
        rules=O.read_rules(fixture.workspace);O.save_rules(fixture.workspace,dict(include=rules['include'],exclude=['@FactoryAI'],revision=rules['revision']))
        hot=T.page_view(fixture.workspace,{'range':['7d']},NOW);self.assertIn('x:61',{p['id'] for p in hot['items']});self.assertIn('x:61',{p['id'] for p in hot['top']});self.assertEqual(hot['filtering']['official_diverted'],0)
        self.assertEqual(T.page_view(fixture.workspace,{'range':['7d'],'channel':['official']},NOW)['total'],0)
    def test_interest_keeps_official_and_grok_hint_in_official_tab(self):
        from test_interest_channel import InterestTests,row,NOW
        fixture=InterestTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        a=row('701');a.update(author_handle='unknownco',author_type='OFFICIAL');fixture.write([a])
        listing=T.page_view(fixture.root,{'range':['7d'],'channel':['interest']},NOW);self.assertEqual(listing['total'],1);self.assertTrue(listing['items'][0]['official']['is_official']);self.assertEqual(listing['items'][0]['official']['source'],'grok_author_type')
        detail=T.item_view(fixture.root,{'id':['x:701'],'channel':['interest']});self.assertTrue(detail['official']['is_official'])
        listing=T.page_view(fixture.root,{'range':['7d'],'channel':['official']},NOW);self.assertIn('x:701',{p['id'] for p in listing['items']})
        selected=next(p for p in listing['items'] if p['id']=='x:701');self.assertEqual(selected['channel'],'interest')
        detail=T.item_view(fixture.root,{'id':['x:701'],'channel':['official']});self.assertTrue(detail['official']['is_official']);self.assertEqual(detail['full_text'],a['full_text'])

class HTTPTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='official-http-',dir='/tmp');self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        class Quiet(Handler):
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Quiet);self.server.store=SimpleNamespace(workspace=self.root);self.server.daemon_threads=True;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2)
    def request(self,body=None,**headers):
        port=self.server.server_port;c=http.client.HTTPConnection('127.0.0.1',port,timeout=2);self.addCleanup(c.close)
        h={'Origin':'http://127.0.0.1:'+str(port),'X-Article-Studio':'1','Content-Type':'application/json',**headers}
        c.request('GET' if body is None else 'POST','/api/settings/official-accounts',body=body,headers=h);r=c.getresponse();return r.status,json.loads(r.read())
    def test_today_http_does_not_wait_for_profile_hang_or_failure(self):
        from test_today_view import TodayTests
        fixture=TodayTests();fixture.setUp();self.addCleanup(fixture.tearDown);self.server.store.workspace=fixture.workspace
        release=threading.Event();entered=threading.Event()
        def hung(account):entered.set();release.wait(30);raise TimeoutError('never log this detail')
        cache=O.Profiles(fixture.workspace,fetcher=hung,network=True)
        with patch.dict(O._profiles,{str(O.workspace(fixture.workspace)):cache}),patch.object(O,'RATE_SECONDS',0),self.assertLogs(O.LOG,level='WARNING') as logs:
            try:
                began=time.monotonic();c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2);self.addCleanup(c.close);c.request('GET','/api/today?range=7d');response=c.getresponse();data=json.loads(response.read())
                self.assertEqual(response.status,200);self.assertLess(time.monotonic()-began,1);self.assertTrue(data['items']);self.assertTrue(entered.wait(1));self.assertEqual(next(p for p in data['items'] if p['id'].startswith('x:'))['official']['profile_state'],'unknown')
            finally:
                release.set()
                if cache.worker:cache.worker.join(3)
        self.assertNotIn('never log this detail',' '.join(logs.output))

    def test_get_readonly_cross_origin_bad_json_oversize_and_invalid_account(self):
        code,data=self.request();self.assertEqual(code,200);self.assertFalse(O.path_for(self.root).exists())
        valid=json.dumps(dict(include=['fixture'],exclude=[],revision=data['revision']))
        self.assertEqual(self.request(valid,Origin='https://foreign.invalid')[0],403)
        self.assertEqual(self.request(b'',**{'Content-Length':'32000001'})[0],413)
        self.assertEqual(self.request('{')[0],400)
        self.assertEqual(self.request(json.dumps(dict(include=['bad/path'],exclude=[],revision=data['revision'])))[0],400)
        self.assertEqual(self.request(valid)[0],200);snapshot=O.path_for(self.root).read_bytes();self.assertEqual(self.request()[0],200);self.assertEqual(snapshot,O.path_for(self.root).read_bytes())

if __name__=='__main__':unittest.main()
