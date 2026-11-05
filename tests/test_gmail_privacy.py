import copy,hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from server import Store
from knowledge_store import Knowledge,identifier
from gmail_view import GmailService
from gmail_sync_store import SyncStore,mail_document
from gmail_auth import GmailError
from tests.test_gmail_auth import MemoryVault,Provider,CLIENT,ACCOUNT,FAKE_ACCESS
from tests import test_gmail_source as cache_fixtures

class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.store=Store(Path(self.t.name)/'studio');self.k=Knowledge(self.store,sources=[]);self.store._knowledge=self.k;self.fixture=cache_fixtures.GmailCacheTests();self.fixture.account=ACCOUNT;self.payload=dict(account=ACCOUNT,query='synthetic',has_more=False,messages=[self.fixture.message(content='SYNTHETIC_PRIVATE_NEEDLE')]);self.provider=Provider();self.v=MemoryVault();self.service=GmailService(self.store,lambda backend:self.v,self.provider);self.session=dict(owner=True,nonce='synthetic-nonce',expires=9999999999)
    def ingest(self):
        self.k.ingest_gmail(self.payload)
        with self.k.gmail_owner():return self.k.search('SYNTHETIC_PRIVATE_NEEDLE',kind='mail')['results'][0]['id']
    def test_default_search_fts_detail_stats_and_direct_sql_export_all_hide(self):
        ident=self.ingest()
        for mode in ('','fts'):self.assertEqual(self.k.search('SYNTHETIC_PRIVATE_NEEDLE',mode=mode)['total'],0)
        self.assertEqual(self.k.status()['total'],0);self.assertEqual(self.k.status()['sources'],[])
        with self.assertRaises(ValueError):self.k.document(ident)
        self.assertEqual(self.k.document_hashes([ident]),{})
        with sqlite3.connect(self.k.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],0);self.assertEqual(db.execute('SELECT COUNT(*) FROM document_fts').fetchone()[0],0)
        with self.k.gmail_owner():
            self.assertEqual(self.k.search('SYNTHETIC_PRIVATE_NEEDLE',kind='mail',mode='fts')['total'],1);self.assertIn('SYNTHETIC_PRIVATE_NEEDLE',self.k.document(ident)['body'])
        self.assertEqual(self.k.search('',source='gmail-'+identifier('gmail',ACCOUNT)[:12])['total'],0)
    def test_legacy_95_index_quarantine_preserves_cache_id_and_old_assertions(self):
        cache=SyncStore(self.store.workspace).cache
        mids=[format(i+1,'x') for i in range(95)];messages=[self.fixture.message(mid=i,content='SYNTHETIC_PRIVATE_NEEDLE') for i in mids];cache.import_messages(dict(self.payload,messages=messages));before=hashlib.sha256(cache.path.read_bytes()).hexdigest();sid='gmail-'+identifier('gmail',ACCOUNT)[:12]
        with self.k.db() as db:
            db.execute('INSERT INTO sources VALUES(?,?,?,?,?)',(sid,json.dumps(dict(id=sid,kind='gmail_cache',account=ACCOUNT)),'ready','synthetic',None));db.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            for m in messages:self.k._write(db,sid,m['id'],'mail','SYNTHETIC_SUBJECT','SYNTHETIC_PRIVATE_NEEDLE')
        from library_search import build_catalog
        build_catalog(self.store.workspace)
        catalogue=self.store.workspace/'items/research_catalog.db';self.assertTrue(catalogue.exists())
        sidecar=self.store.workspace/'raw-materials-meta/counts.json';sidecar.parent.mkdir(exist_ok=True);sidecar.write_text('SYNTHETIC_PRIVATE_STALE_COUNTS')
        self.k._isolate_gmail_index()
        self.assertFalse(catalogue.exists());self.assertFalse(sidecar.exists())
        self.assertEqual(len(cache.records(ACCOUNT)),95);self.assertEqual(hashlib.sha256(cache.path.read_bytes()).hexdigest(),before)
        with self.k.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],0)
        self.assertEqual(self.k._gmail_index().search()['total'],95)
        with self.k.gmail_owner():self.assertEqual(self.k.document(identifier(sid,mids[0]))['title'],'SYNTHETIC_SUBJECT')
    def test_owner_saved_private_dossier_never_public_snapshot(self):
        ident=self.ingest()
        with self.k.gmail_owner():
            r=self.k.document(ident);d=self.k.save_dossier(dict(title='SYNTHETIC_PRIVATE_DOSSIER',items=[dict(id=ident,hash=r['hash'],start=0,end=10)]));self.assertEqual(len(d['items']),1)
        with self.assertRaises(ValueError):self.k.dossier(d['id'])
        with self.k.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM dossiers').fetchone()[0],0)
    def test_session_nonce_origin_host_and_client_cannot_self_promote(self):
        sid=self.service.new_session();session=self.service.sessions[sid]
        headers={'Cookie':'gmail_session='+sid,'Host':'127.0.0.1:12345','Origin':'http://127.0.0.1:12345','X-Article-Studio':'1','X-Gmail-Nonce':session['nonce']};h=SimpleNamespace(headers=headers,command='POST',server=SimpleNamespace(server_port=12345))
        self.assertIs(self.service.session(h),session)
        for field,value in [('Origin','https://evil.test'),('Host','evil.test'),('X-Gmail-Nonce','forged'),('X-Article-Studio','0'),('Sec-Fetch-Site','cross-site')]:
            with self.subTest(field=field):
                bad=copy.copy(h);bad.headers=dict(headers,**{field:value})
                with self.assertRaises(GmailError):self.service.session(bad)
        with self.assertRaises(GmailError):self.service.action('sync',{'private':True},session)
        with self.assertRaises(GmailError):self.service.action('client',{'client':CLIENT},session)
        self.service.action('unlock',{},session);self.assertTrue(session['owner'])
    def test_locked_keychain_static_error_no_fallback_and_no_leaked_value(self):
        self.v.locked=True
        with self.assertRaises(GmailError) as caught:self.service.action('client',dict(client=CLIENT,backend='keychain'),self.session)
        self.assertEqual(str(caught.exception),'无法读取本机凭据');self.assertNotIn(FAKE_ACCESS,str(caught.exception));self.assertIsNone(self.service.sync_store.state())
    def test_workspace_contains_no_token_or_client_secret(self):
        self.service.action('client',dict(client=CLIENT,backend='keychain'),self.session)
        for path in self.store.workspace.rglob('*'):
            if path.is_file():
                data=path.read_bytes();self.assertNotIn(CLIENT['installed']['client_secret'].encode(),data);self.assertNotIn(FAKE_ACCESS.encode(),data)
        self.assertNotIn('client_secret',json.dumps(self.service.status()))
    def test_synthetic_provider_exception_safe_response(self):
        self.service.action('client',dict(client=CLIENT,backend='keychain'),self.session)
        with patch.object(self.v,'get',side_effect=RuntimeError(FAKE_ACCESS+' SYNTHETIC_PRIVATE_NEEDLE')):
            # OAuth's provider exceptions are translated by its boundary; the HTTP view also has a fixed fallback.
            from gmail_view import post_route
            sid=self.service.new_session();self.service.sessions[sid]['owner']=True
            h=SimpleNamespace(server=SimpleNamespace(server_port=12345,gmail_service=self.service),command='POST',headers={'Cookie':'gmail_session='+sid,'Host':'127.0.0.1:12345','Origin':'http://127.0.0.1:12345','X-Article-Studio':'1','X-Gmail-Nonce':self.service.sessions[sid]['nonce']},reply=lambda code,value:(code,value))
            code,value=post_route(h,{'expected_account':ACCOUNT},SimpleNamespace(path='/api/gmail/connect'));self.assertGreaterEqual(code,400);self.assertNotIn(FAKE_ACCESS,json.dumps(value));self.assertNotIn('SYNTHETIC_PRIVATE_NEEDLE',json.dumps(value))

    def test_saved_event_snapshot_details_evidence_and_every_count_are_private(self):
        from events_store import Events
        ident=self.ingest();events=Events(self.store)
        with self.k.gmail_owner():
            r=self.k.document(ident);event=events.save_event({'title':'SYNTHETIC_PRIVATE_EVENT','items':[dict(id=ident,hash=r['hash'],start=0,end=len(r['body']))]})
            events.save_action(event['id'],dict(summary='SYNTHETIC_PRIVATE_TASK',owner='synthetic',owner_is_self=True,due_at='2026-11-05T10:00:00+08:00',status='open',evidence_ids=[event['evidence'][0]['id']]))
            self.assertEqual(events.list_events()['counts']['total'],1)
        with self.assertRaises(ValueError):events.event(event['id'])
        with events.db() as db:
            with self.assertRaises(ValueError):events._evidence(db,event['id'])
        for view in ['all','important','mine','waiting','confirm','calendar']:
            result=events.list_events(view=view);self.assertEqual(result['events'],[]);self.assertTrue(all(n==0 for n in result['counts'].values()));self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(result))

    def test_session_expiry_and_unexpected_endpoint_fields_never_elevate(self):
        sid=self.service.new_session();s=self.service.sessions[sid];s['expires']=0
        h=SimpleNamespace(headers={'Cookie':'gmail_session='+sid,'Host':'127.0.0.1:12345','X-Gmail-Nonce':s['nonce']},command='GET',server=SimpleNamespace(server_port=12345))
        with self.assertRaises(GmailError):self.service.session(h)
        for payload in [dict(private=True),dict(token_uri='https://evil.test'),dict(endpoint='https://evil.test')]:
            with self.assertRaises(GmailError):self.service.action('connect',payload,self.session)

class RepairBoundaryTests(unittest.TestCase):
    setUp=PrivacyTests.setUp
    ingest=PrivacyTests.ingest

    def test_private_draft_rejected_before_work_write_and_public_reindex(self):
        ident=self.ingest()
        with self.k.gmail_owner():
            public=self.k.add_note(dict(title='SYNTHETIC_PUBLIC_NOTE',body='public note'))
            row=self.k.document(ident)
            dossier=self.k.save_dossier(dict(title='SYNTHETIC_PRIVATE_DRAFT',items=[dict(id=ident,hash=row['hash'],start=0,end=len(row['body'])),dict(id=public['id'],hash=public['hash'],start=0,end=6)]))
            with patch.object(self.store,'create',wraps=self.store.create) as create,patch.object(self.k,'start_sync') as sync:
                with self.assertRaisesRegex(ValueError,'私有邮件'):
                    self.k.draft(dict(id=dossier['id']))
                create.assert_not_called();sync.assert_not_called()
            renamed=self.k.save_dossier(dict(id=dossier['id'],title='SYNTHETIC_PRIVATE_RENAMED',purpose='SYNTHETIC_PRIVATE_PURPOSE'))
            self.assertEqual(renamed['title'],'SYNTHETIC_PRIVATE_RENAMED')
            with self.assertRaisesRegex(ValueError,'私有邮件'):self.k.draft(dict(id=dossier['id']))
        with self.assertRaises(ValueError):self.k.dossier(dossier['id'])
        self.assertEqual(self.store.listing()['articles'],[])
        with self.k.db() as db:
            db.execute('INSERT OR IGNORE INTO sources(id,config) VALUES(?,?)',('articles',json.dumps(dict(id='articles',kind='articles'))))
        self.k.sync('articles')
        self.assertEqual(self.k.search('SYNTHETIC_PRIVATE')['total'],0)
        with sqlite3.connect(self.k.path) as db:
            for table in ('documents','dossiers','document_trash'):
                self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(db.execute('SELECT * FROM '+table).fetchall()))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents WHERE kind='articles'").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM document_fts WHERE document_fts MATCH 'SYNTHETIC_PRIVATE'").fetchone()[0],0)

    def test_owner_private_and_public_preview_delete_list_restore(self):
        private=self.ingest();public=self.k.add_note(dict(title='SYNTHETIC_PUBLIC_DELETE',body='ordinary note'));ids=[private,public['id']]
        with self.k.gmail_owner():
            preview=self.k.trash_preview(ids);self.assertEqual([x['status'] for x in preview['items']],['ready','ready'])
            picks=[dict(id=x['id'],expected_hash=x['hash']) for x in preview['items']]
            deleted=self.k.trash_many(picks,confirm=True);self.assertEqual(deleted['summary']['deleted'],2)
            self.assertEqual(self.k.trash_many(picks,confirm=True)['summary']['already_trashed'],2)
            self.assertEqual(self.k.search('SYNTHETIC_PRIVATE_NEEDLE',kind='mail')['total'],0)
            self.assertEqual(self.k.trash_list()['total'],2)
            self.assertTrue(self.k.document(private)['trashed'])
        # No private ID/title/hash/deletion metadata in the ordinary database/list.
        self.assertEqual(self.k.trash_list()['total'],1)
        with sqlite3.connect(self.k.path) as db:
            rows=db.execute('SELECT * FROM document_trash').fetchall();self.assertEqual(len(rows),1);self.assertNotIn(private,json.dumps(rows));self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(rows))
        self.k.ingest_gmail(self.payload)
        self.assertEqual(self.k._gmail_index().search()['total'],0)
        # Exercise the actual outbox reindexer too: remote observation cannot restore local deletion.
        sync=SyncStore(self.store.workspace)
        with sync.cache.db(write=True) as db:db.execute('INSERT INTO gmail_outbox VALUES(?,?,1)',(ACCOUNT,self.payload['messages'][0]['id']))
        sync.drain(ACCOUNT);self.assertEqual(sync.index.search()['total'],0)
        with self.k.gmail_owner():
            restored=self.k.restore_many(ids);self.assertEqual(restored['summary']['restored'],2)
            self.assertEqual(self.k.trash_list()['total'],0)
            self.assertEqual(self.k.search('SYNTHETIC_PRIVATE_NEEDLE',kind='mail')['total'],1)
            self.assertFalse(self.k.document(private)['trashed'])
        self.assertEqual(self.k.search('SYNTHETIC_PRIVATE_NEEDLE')['total'],0)

    def _late_read(self,actions,*,fail=False):
        import threading
        from tests.test_gmail_sync import SyncTests
        f=SyncTests();f.setUp();self.addCleanup(f.doCleanups);f.add();entered=threading.Event();release=threading.Event();errors=[]
        def hook(method,path):
            if path=='/messages/aa01':
                entered.set();release.wait(5)
                if fail:raise RuntimeError('SYNTHETIC_FAILURE')
        f.provider.hook=hook
        def run():
            try:f.adapter.run()
            except Exception as error:errors.append(type(error).__name__)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(5));service=GmailService(SimpleNamespace(workspace=f.root),lambda backend:f.v,f.provider,f.clock);service._auth=f.auth;service._backend='keychain'
            for action in actions:
                if action=='reconnect':f.store.update(invalidate=True,connected=True,paused=False,status='connected',history_id='SYNTHETIC_NEW_CURSOR',retry_pending=None)
                else:service.action(action,{},dict(owner=True))
            expected=f.store.state()
        finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,['GmailError']);self.assertEqual(f.store.state(),expected)
        self.assertEqual(f.count(),0)
        with f.store.cache.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM gmail_outbox').fetchone()[0],0)
        self.assertEqual(f.store.index.search()['total'],0)

    def test_disconnect_fences_late_message_commit(self):self._late_read(['disconnect'])
    def test_pause_fences_late_message_commit(self):self._late_read(['pause'])
    def test_pause_resume_cannot_revive_old_run(self):self._late_read(['pause','resume'])
    def test_reconnect_cannot_revive_old_run(self):self._late_read(['disconnect','reconnect'])
    def test_late_failure_cannot_overwrite_disconnected(self):self._late_read(['disconnect'],fail=True)

    def test_stale_control_save_is_rejected_in_transaction(self):
        from tests.test_gmail_sync import SyncTests
        f=SyncTests();f.setUp();self.addCleanup(f.doCleanups);old=f.store.state();f.store.update(invalidate=True,paused=True,status='paused');expected=f.store.state()
        with self.assertRaises(GmailError):f.adapter._save(old)
        self.assertEqual(f.store.state(),expected)

    def test_local_delete_wins_over_inflight_private_reindex(self):
        import threading
        ident=self.ingest();sync=SyncStore(self.store.workspace);entered=threading.Event();release=threading.Event();errors=[]
        with sync.cache.db(write=True) as db:db.execute('INSERT INTO gmail_outbox VALUES(?,?,1)',(ACCOUNT,self.payload['messages'][0]['id']))
        original=sync.index.put
        def delayed(record):entered.set();release.wait(5);return original(record)
        def replay():
            try:sync.drain(ACCOUNT)
            except Exception as error:errors.append(type(error).__name__)
        with patch.object(sync.index,'put',side_effect=delayed):
            thread=threading.Thread(target=replay);thread.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.k.gmail_owner():
                    doc=self.k.document(ident);self.assertEqual(self.k.trash_many([dict(id=ident,expected_hash=doc['hash'])],confirm=True)['summary']['deleted'],1)
            finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(sync.index.search()['total'],0)
        with self.k.gmail_owner():
            self.assertTrue(self.k.document(ident)['trashed']);self.assertEqual(self.k.restore_many([ident])['summary']['restored'],1);self.assertEqual(self.k.search('SYNTHETIC_PRIVATE_NEEDLE',kind='mail')['total'],1)
