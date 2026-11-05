import copy,json,sqlite3,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from gmail_auth import OAuth,READ_SCOPE,GmailError
from gmail_sync_store import SyncStore
from gmail_sync import GmailAdapter,api_message
from tests.test_gmail_auth import Provider,MemoryVault,ACCOUNT,FAKE_ACCESS,FAKE_REFRESH

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name);self.now=[1793876400.];self.clock=lambda:self.now[0];self.store=SyncStore(self.root,self.clock);self.provider=Provider(self.clock);self.ref='a'*32;self.v=MemoryVault();self.v.put(self.ref,dict(access_token=FAKE_ACCESS,refresh_token=FAKE_REFRESH,scope=READ_SCOPE,expires_at=self.clock()+9999,client={},account=ACCOUNT));self.auth=OAuth(self.v,self.provider,self.clock);self.adapter=GmailAdapter(self.store,self.auth,self.clock)
        self.store.save(dict(ref=self.ref,connected=True,paused=False,status='connected',days=30,interval=60,labels=[],history_id='',phase='backfill'))
    def run_again(self):self.now[0]+=400;return self.adapter.run()
    def count(self):
        with self.store.cache.db() as db:return db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    def add(self,mid='aa01',**kwargs):self.provider.messages[mid]=self.provider.message(mid,**kwargs)
    def event(self,ids,watermark=None,next_page=None,key='messagesAdded'):
        page=dict(history=[dict(id='900719925474099312999',**{key:[dict(message=dict(id=i)) for i in ids]})],historyId=watermark or self.provider.watermark)
        if next_page:page['nextPageToken']=next_page
        return page
    def test_empty_and_101_first_pages_no_estimate_claim(self):
        self.adapter.run();self.assertEqual(self.count(),0);self.assertEqual(self.store.state()['history_id'],self.provider.watermark)
        self.store.update(history_id='',baseline='',phase='backfill')
        for i in range(101):self.add(format(i+1,'x'))
        result=self.run_again();self.assertEqual(self.count(),101);self.assertEqual(result['status'],'ready')
        self.assertTrue(all('labelIds' not in q for _,_,q in self.provider.calls));self.assertNotEqual(self.count(),999999)
    def test_huge_noncontinuous_string_watermark_and_duplicates(self):
        self.add();self.provider.events=[self.event(['aa01','aa01'],watermark='999999999999999999999999999999')];self.adapter.run();self.assertEqual(self.count(),1);self.assertEqual(self.store.state()['history_id'],'999999999999999999999999999999');self.run_again();self.assertEqual(self.count(),1)
    def test_last_page_network_does_not_advance_final_watermark(self):
        self.add();self.add('aa02');self.provider.events=[self.event(['aa01'],next_page='1'),self.event(['aa02'],watermark='99999999999999999999')]
        def hook(method,path):
            if path=='/history' and self.provider.calls[-1][2].get('pageToken')=='1':raise GmailError('network')
        self.provider.hook=hook
        with self.assertRaises(GmailError):self.adapter.run()
        self.assertEqual(self.store.state()['history_id'],'');self.assertEqual(self.count(),2)
        self.provider.hook=None;self.run_again();self.assertEqual(self.store.state()['history_id'],'99999999999999999999');self.assertEqual(self.count(),2)
    def test_page_invalid_restart_no_clear_and_partial_get_replay(self):
        for mid in ['aa01','aa02']:self.add(mid)
        self.provider.fail['/messages/aa02']='network'
        with self.assertRaises(GmailError):self.adapter.run()
        self.assertEqual(self.count(),0);self.assertEqual(self.store.state()['history_id'],'');self.assertTrue(self.store.state()['retry_pending'])
        self.provider.fail.clear();self.run_again();self.assertEqual(self.count(),2)
    def test_history_404_rebuild_preserves_old_and_stale_unknown(self):
        self.add();self.adapter.run();self.provider.messages={};self.provider.fail['/history']=['history'];self.run_again();self.assertEqual(self.count(),1)
        with self.store.cache.db() as db:self.assertEqual(db.execute('SELECT stale FROM gmail_marks').fetchone()[0],1)
    def test_outbox_and_message_transaction_atomic_under_sql_failure(self):
        self.add()
        with self.store.cache.db(write=True) as db:db.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON gmail_outbox BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(GmailError):self.adapter.run()
        self.assertEqual(self.count(),0);self.assertEqual(self.store.state()['history_id'],'')
        with self.store.cache.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM gmail_outbox').fetchone()[0],0)
    def test_index_failure_retry_and_source_tombstone_is_immediate(self):
        self.add()
        with patch.object(self.store.index,'put',side_effect=OSError('synthetic')):
            with self.assertRaises(GmailError):self.adapter.run()
        self.assertEqual(self.count(),1)
        with self.store.cache.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM gmail_outbox').fetchone()[0],1)
        self.run_again();self.assertEqual(self.store.index.search()['total'],1)
        self.provider.events=[self.event(['aa01'],key='messagesDeleted')]
        with patch.object(self.store.index,'put',side_effect=OSError('synthetic')):
            with self.assertRaises(GmailError):self.run_again()
        ident=self.store.index.search()['results'][0]['id'];self.assertTrue(self.store.is_hidden(ident))
    def test_labels_or_archive_trash_spam_restore_and_local_trash(self):
        self.adapter.configure(labels=['Label_A','Label_B']);self.add(labels=['Label_B']);self.add('aa02',labels=['Other']);self.adapter.run();self.assertEqual(self.count(),1)
        self.provider.messages['aa01']['labelIds']=['Label_A'];self.provider.events=[self.event(['aa01'],key='labelsRemoved')];self.run_again();self.assertEqual(self.store.index.search()['total'],1)
        self.provider.messages['aa01']['labelIds']=['TRASH','Label_A'];self.run_again();self.assertEqual(self.store.index.search()['total'],0)
        self.provider.messages['aa01']['labelIds']=['SPAM','Label_A'];self.run_again();self.assertEqual(self.store.index.search()['total'],0)
        self.provider.messages['aa01']['labelIds']=['Label_A'];self.run_again();self.assertEqual(self.store.index.search()['total'],1)
        r=self.store.index.get(self.store.index.search()['results'][0]['id']);r['trashed']=True;self.store.index.put(r);self.run_again();self.assertEqual(self.store.index.search()['total'],0)
    def test_get_404_and_5xx_are_not_permanent_deletion(self):
        self.add();self.adapter.run();self.provider.events=[self.event(['aa01'])]
        for code in ['page','network','rate','policy']:
            self.provider.fail['/messages/aa01']=code
            with self.assertRaises(GmailError):self.run_again()
            self.assertEqual(self.store.index.search()['total'],1)
    def test_budget_continuation_and_single_wakeup(self):
        for i in range(5):self.add(format(i+1,'x'))
        self.adapter.limit=2;self.adapter.run();self.assertEqual(self.count(),2);self.assertEqual(self.store.state()['history_id'],'')
        self.run_again();self.assertEqual(self.count(),4);self.run_again();self.assertEqual(self.count(),5);self.run_again();self.assertEqual(self.store.state()['phase'],'idle')
        self.now[0]+=86400;self.adapter.run_due();calls=len(self.provider.calls);self.adapter.run_due();self.assertEqual(len(self.provider.calls),calls)
    def test_large_history_event_resumes_without_starvation(self):
        self.adapter.run()
        for i in range(5):self.add(format(i+1,'x'))
        self.provider.events=[self.event(list(self.provider.messages),watermark='999999999999999999999')];self.adapter.limit=2
        self.run_again();self.assertNotEqual(self.store.state()['history_id'],'999999999999999999999');self.run_again();self.run_again();self.assertEqual(self.store.state()['history_id'],'999999999999999999999');self.assertEqual(self.count(),5)
    def test_manual_scheduled_lock_cooldown_pause_and_invalid_grant(self):
        self.add();entered=threading.Event();release=threading.Event()
        def hook(method,path):
            if path=='/profile':entered.set();release.wait(2)
        self.provider.hook=hook;errors=[]
        def run():
            try:self.adapter.run()
            except Exception as e:errors.append(type(e).__name__)
        thread=threading.Thread(target=run);thread.start();self.assertTrue(entered.wait(2))
        with self.assertRaises(GmailError):self.adapter.run()
        self.store.update(paused=True);release.set();thread.join();self.assertEqual(self.store.state()['status'],'paused')
        self.provider.hook=None;self.store.update(paused=False,connected=True);self.v.values[self.ref]['expires_at']=0;self.provider.fail['token']='auth'
        with self.assertRaises(GmailError):self.run_again()
        self.assertFalse(self.store.state()['connected']);self.assertEqual(self.adapter.run_due()['status'],'waiting')
    def test_mime_large_attachment_detached_body_and_no_download(self):
        self.add();m=self.provider.messages['aa01'];m['payload']=dict(mimeType='multipart/mixed',parts=[dict(mimeType='text/html',headers=[],body={'size':100,'data':__import__('base64').urlsafe_b64encode(b'<script>evil()</script><p>SAFE</p><img src="https://evil.test/x">').decode()}),dict(mimeType='application/zip',filename='synthetic.zip',partId='2',body={'size':500000000,'attachmentId':'attachment-fixture'})]);self.adapter.run();r=self.store.cache.records(ACCOUNT)[0];self.assertEqual(r['body'],'SAFE');self.assertEqual(r['attachments'][0]['size'],500000000);self.assertFalse(any('attachments' in path for _,path,_ in self.provider.calls))
        m['payload']=dict(mimeType='text/plain',body={'size':100,'attachmentId':'body-detached'});self.provider.events=[self.event(['aa01'])];self.run_again();r=self.store.cache.records(ACCOUNT)[0];self.assertIn('正文未取全',r['content_scope']['detail'])
    def test_range_shrink_does_not_clear_and_wrong_account_stops(self):
        self.add();self.adapter.run();self.adapter.configure(days=7);self.run_again();self.assertEqual(self.count(),1)
        self.provider.account='other@example.test'
        with self.assertRaises(GmailError):self.run_again()
        self.assertEqual(self.count(),1)

    def test_final_history_transaction_failure_never_commits_watermark(self):
        self.adapter.run();old=self.store.state()['history_id'];self.add();self.provider.events=[self.event(['aa01'],watermark='999999999999999999999')]
        with self.store.cache.db(write=True) as db:db.execute("CREATE TRIGGER reject_final BEFORE INSERT ON gmail_outbox BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(GmailError):self.run_again()
        self.assertEqual(self.store.state()['history_id'],old);self.assertEqual(self.count(),0)
    def test_cooldown_and_configuration_type_boundary(self):
        self.adapter.run()
        with self.assertRaises(GmailError):self.adapter.run()
        for settings in [dict(days=True),dict(interval=60.0),dict(labels='INBOX')]:
            with self.assertRaises(GmailError):self.adapter.configure(**settings)

    def test_network_timeout_backoff_without_immediate_retry(self):
        self.add();self.provider.fail['/messages/aa01']='network'
        with self.assertRaises(GmailError):self.adapter.run()
        self.assertEqual(sum(path=='/messages/aa01' for _,path,_ in self.provider.calls),1)
        before=len(self.provider.calls);self.adapter.run_due();self.assertEqual(len(self.provider.calls),before)

    def test_30_and_60_minute_adapter_intervals_no_threads_or_model_locks(self):
        for interval in (30,60):
            self.adapter.configure(interval=interval);self.run_again();self.assertEqual(self.store.state()['next_due']-self.clock(),interval*60)
        self.assertEqual(self.adapter.source,'gmail');self.assertEqual(self.adapter.event,'ingest_and_index')

    def test_repeated_observation_does_not_change_content_fingerprint(self):
        self.add();self.adapter.run();before=self.store.index.search()['results'][0]['hash'];self.provider.events=[self.event(['aa01'])];self.run_again();self.assertEqual(self.store.index.search()['results'][0]['hash'],before)
        self.assertEqual(self.adapter.registration()['source'],'gmail:'+self.ref);self.assertFalse(self.adapter.registration()['registered'])

    def test_actual_backfill_page_token_expiry_restarts_without_clearing(self):
        for i in range(3):self.add(format(i+1,'x'))
        self.adapter.limit=2;self.adapter.run();self.assertEqual(self.count(),2);self.assertEqual(self.store.state()['page_token'],'2')
        self.provider.fail['/messages']=['page']
        with self.assertRaises(GmailError):self.run_again()
        self.assertEqual(self.count(),2);self.assertEqual(self.store.state()['page_token'],'');self.assertEqual(self.store.state()['history_id'],'')
        self.run_again();self.run_again();self.run_again()
        self.assertEqual(self.count(),3);self.assertEqual(self.store.state()['history_id'],self.provider.watermark)

    def test_mail_arriving_during_backfill_is_caught_from_frozen_baseline(self):
        self.add();old=self.provider.watermark;new='999999999999999999999999999999'
        def arrive(method,path):
            if path=='/history' and 'aa02' not in self.provider.messages:
                self.add('aa02');self.provider.events=[self.event(['aa02'],watermark=new)];self.provider.watermark=new
        self.provider.hook=arrive;self.adapter.run()
        self.assertEqual(self.count(),2);self.assertEqual(self.store.state()['history_id'],new)
        self.assertEqual([q['startHistoryId'] for _,path,q in self.provider.calls if path=='/history'],[old])

    def test_backfill_epoch_window_stays_fixed_across_midnight_resume(self):
        start=int(self.clock());self.add();self.add('aa02');self.adapter.limit=1;self.adapter.run()
        state=self.store.state();self.assertEqual(state['lower'],start-30*86400);self.assertEqual(state['upper'],start+1)
        self.now[0]+=86400;self.adapter.run()
        queries=[q['q'] for _,path,q in self.provider.calls if path=='/messages']
        self.assertGreaterEqual(len(queries),2);self.assertEqual(len(set(queries)),1)
        self.assertIn('after:'+str(start-30*86400),queries[0]);self.assertIn('before:'+str(start+1),queries[0]);self.assertEqual(self.count(),2)
