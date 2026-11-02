"""Synthetic material indexes; no real library, source endpoint, or model is used."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from server import Store
from knowledge_store import Knowledge, NOT_TRASHED
import raw_materials_meta as M


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='p0-meta-',dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'workspace')
        self.k=Knowledge(self.store,[dict(id='manual',name='合成手记',kind='manual'),dict(id='fixture',name='合成项目',kind='projects',paths=[str(self.root)]),dict(id='wechat',name='合成微信',kind='wechat',backend='collector',path=str(self.root/'source.db'))])
        self.k.capture=SimpleNamespace(status=Mock(return_value=dict(available=True,busy=False,status='idle')))
        self.meta=M.model(self.k);self.addCleanup(self.stop)
        self.meta.refresh()

    def stop(self):
        self.k.stop_event.set()
        if self.meta.worker:self.meta.worker.join(2)

    def note(self,title='合成笔记'):
        return self.k.add_note(dict(title=title,body='合成正文，不访问外部来源'))

    def until(self,predicate):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.01)
        self.fail('投影未按时更新')

    def legacy(self):
        with self.k.db() as db:
            rows=db.execute('SELECT s.*,COUNT(d.id) count,MAX(d.modified) latest FROM sources s LEFT JOIN documents d ON d.source=s.id AND '+NOT_TRASHED+' GROUP BY s.id ORDER BY s.rowid').fetchall()
            sources=[{**json.loads(r['config']),**{k:r[k] for k in ('status','detail','synced_at','count','latest')}} for r in rows]
            total=db.execute('SELECT COUNT(*) FROM documents d WHERE '+NOT_TRASHED).fetchone()[0]
            dossiers=[dict(r) for r in db.execute('SELECT id,title,modified FROM dossiers ORDER BY modified DESC')]
        return dict(sources=sources,total=total,dossiers=dossiers,syncing=self.k.sync_lock.locked())

    def test_cached_get_never_queries_sql_or_capture(self):
        with patch.object(self.meta,'_read',side_effect=AssertionError('GET executed SQL')):
            for _ in range(20):self.k.status(cached=True)
        self.k.capture.status.assert_not_called()

    def test_connection_cold_load_uses_persisted_watermark_without_aggregating(self):
        self.note();before=self.meta.read()
        with patch.object(M.MetadataCache,'_read',side_effect=AssertionError('cold GET aggregated')):
            cache=M.MetadataCache(self.k);loaded=cache.read()
        self.assertEqual(loaded['total'],before['total']);self.assertEqual(loaded['counts_as_of'],before['counts_as_of'])
        self.assertTrue(loaded['counts_stale'])

    def test_legacy_values_and_key_types_preserved(self):
        self.note();expected=self.legacy();actual=self.k.status()
        for key in expected:
            self.assertIs(type(actual[key]),type(expected[key]));self.assertEqual(actual[key] if key!='sources' else [{k:v for k,v in s.items() if k!='capture'} for s in actual[key]],expected[key])
        self.assertIsInstance(actual['counts_as_of'],str)
        self.assertEqual(actual['sources'][-1]['capture'],dict(available=True,busy=False,status='idle'))

    def test_legacy_capture_fields_preserved_while_new_health_is_allowlisted(self):
        original=dict(available=True,status='done',legacy_extra=[1,'old'],last_success=dict(captured_at='2026-01-01T00:00:00Z',legacy_extra=dict(n=2)))
        self.k.capture.status.return_value=original
        legacy=self.k.status()['sources'][-1]['capture']
        self.assertEqual(legacy,original)
        public=self.meta.health.snapshot()['capture']
        self.assertNotIn('legacy_extra',public);self.assertNotIn('legacy_extra',public['last_success'])

    def test_null_ids_orphans_and_trashed_latest_match_legacy(self):
        a=self.note('first');b=self.note('latest')
        with self.k.db() as db:
            db.execute("UPDATE documents SET modified='2026-01-01' WHERE id=?",(a['id'],))
            db.execute("UPDATE documents SET modified='2026-01-02' WHERE id=?",(b['id'],))
            db.execute("INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance) VALUES(NULL,'manual','notes','null','body','','2026-01-03','[]','null','{}')")
            db.execute("INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance) VALUES('orphan','unknown','notes','orphan','body','','2026-01-04','[]','orphan','{}')")
            db.execute('INSERT INTO document_trash(document_id,deleted_at) VALUES(?,?)',(b['id'],'2026-01-04'))
        expected=self.legacy();actual=self.meta.refresh()
        self.assertEqual(actual['total'],expected['total'])
        self.assertEqual(actual['sources'],expected['sources'][:-1]+[{**expected['sources'][-1],'capture':M.safe_health(None)}])
        self.assertEqual(next(s for s in actual['sources'] if s['id']=='manual')['latest'],'2026-01-03')

    def test_add_delete_restore_invalidation_and_background_counts(self):
        before=self.meta.read()
        with self.meta.refresh_lock:
            doc=self.note();stale=self.meta.read()
        self.assertTrue(stale['counts_stale']);self.assertEqual(stale['counts_as_of'],before['counts_as_of'])
        self.meta.start();self.until(lambda:self.meta.read()['total']==1)
        receipt=self.k.trash_many([dict(id=doc['id'],expected_hash=doc['hash'])],confirm=True)
        self.assertEqual(receipt['deleted'][0]['id'],doc['id']);self.until(lambda:self.meta.read()['total']==0)
        self.k.restore_many([doc['id']]);self.until(lambda:self.meta.read()['total']==1)
        self.assertEqual(self.k.document(doc['id'])['body'],doc['body'])

    def test_sync_completion_invalidates_after_commit(self):
        self.meta.start()
        def imported(db,source):
            self.k._write(db,'fixture','sync','projects','合成同步','合成同步正文');return '已同步',True
        with patch.object(self.k,'_import',side_effect=imported):self.k.sync('fixture')
        value=self.until(lambda:next((s for s in self.meta.read()['sources'] if s['id']=='fixture' and s['count']==1 and s['status']=='ready'),None))
        self.assertIsInstance(value['synced_at'],str)

    def test_rollback_does_not_invalidate_or_publish_success(self):
        before=self.meta.read()
        with self.assertRaises(RuntimeError):
            with self.k.db() as db:
                db.execute("UPDATE sources SET status='syncing'");raise RuntimeError('synthetic rollback')
        self.assertEqual(self.meta.read(),before)

    def test_background_validation_detects_memory_and_disk_difference(self):
        with self.meta.lock:self.meta.value['total']=999
        disk=json.loads(self.meta.sidecar.read_text());disk['total']=888;self.meta.sidecar.write_text(json.dumps(disk))
        with patch.object(M,'REFRESH_SECONDS',.05):
            self.meta.start();self.until(lambda:self.meta.read()['meta_validation']['difference'])
        value=self.meta.read();self.assertEqual(value['total'],0);self.assertTrue(value['meta_validation']['persisted_difference'])
        self.assertEqual(self.meta.sidecar.stat().st_mode&0o777,0o600)

    def test_cache_storage_failure_cannot_turn_actual_write_into_failure(self):
        with patch.object(self.meta,'_persist',side_effect=OSError('SYNTHETIC_PRIVATE_PATH')):
            doc=self.note()
            self.assertEqual(self.k.status()['total'],1)
        self.assertEqual(self.k.document(doc['id'])['body'],doc['body'])
        self.assertNotIn('SYNTHETIC_PRIVATE_PATH',json.dumps(self.meta.read()))

    def test_new_http_routes_are_readonly_validate_queries_and_preserve_rows(self):
        import http.client
        from server import Handler,ThreadingHTTPServer
        self.store._knowledge=self.k;self.note();self.meta.refresh()
        def snapshot():
            with self.k.db() as db:
                result={}
                for t in ('documents','document_trash','sources','document_fts_data','document_fts_idx','document_fts_docsize','document_fts_config'):
                    columns=list(db.execute('PRAGMA table_info('+t+')'));keys=[r[1] for r in sorted(columns,key=lambda r:r[5]) if r[5]]
                    order=','.join('"'+key+'"' for key in keys) or 'rowid'
                    result[t]=[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY '+order)]
                return result
        before=snapshot();server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for path,status in (('/api/knowledge/meta',200),('/api/knowledge/health',200),('/api/knowledge/meta?x=1',400),('/api/knowledge/health?path=/private',400)):
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
                try:
                    connection.request('GET',path);response=connection.getresponse();body=response.read();self.assertEqual(response.status,status)
                    self.assertNotIn(str(self.root).encode(),body)
                finally:connection.close()
        finally:server.shutdown();server.server_close()
        self.assertEqual(snapshot(),before)

    def test_read_sql_uses_read_only_connection(self):
        original=sqlite3.connect
        def connect(*args,**kwargs):
            self.assertIn('mode=ro',args[0]);self.assertTrue(kwargs.get('uri'));return original(*args,**kwargs)
        with patch.object(M.sqlite3,'connect',side_effect=connect):self.meta.refresh()

    def test_refresh_does_not_hold_snapshot_lock_or_block_gets(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set);read=self.meta._read
        def slow():entered.set();release.wait(2);return read()
        with patch.object(self.meta,'_read',side_effect=slow):
            t=threading.Thread(target=self.meta.refresh);t.start()
            try:
                self.assertTrue(entered.wait(1));started=time.monotonic();self.meta.read();self.assertLess(time.monotonic()-started,.05)
            finally:release.set();t.join(3)

    def test_commit_during_refresh_keeps_old_watermark_marked_stale(self):
        read=self.meta._read
        def concurrent():
            value=read();self.note();return value
        with patch.object(self.meta,'_read',side_effect=concurrent):value=self.meta.refresh()
        self.assertTrue(value['counts_stale']);self.assertEqual(value['total'],0)
        self.assertEqual(self.meta.refresh()['total'],1)

    def test_new_routes_reject_all_query_arguments_and_redact_addresses(self):
        self.note()
        self.meta.refresh()
        with self.meta.lock:
            self.meta.value['sources'][0].update(path='/private/synthetic-path',api_key='synthetic-hidden',detail='地址 http://private.example:1234/private 与 /private/synthetic-path')
        handler=SimpleNamespace(server=SimpleNamespace(store=SimpleNamespace(knowledge=self.k)),reply=lambda code,data:(code,data))
        for query in ('x=1','limit=1&limit=2','source=%27OR1%3D1','path=/private'):
            with self.assertRaises(ValueError):M.get_route(handler,urlsplit('/api/knowledge/meta?'+query))
        code,data=M.get_route(handler,urlsplit('/api/knowledge/meta'));self.assertEqual(code,200)
        encoded=json.dumps(data)
        for token in ('private.example','/private/synthetic-path','synthetic-hidden'):self.assertNotIn(token,encoded)


class HealthTests(unittest.TestCase):
    def test_slow_probe_timeout_is_bounded_and_single_inflight(self):
        release=threading.Event();entered=threading.Event();self.addCleanup(release.set)
        def slow():entered.set();release.wait(2);return dict(status='done',available=True)
        capture=SimpleNamespace(status=Mock(side_effect=slow));health=M.HealthCache(capture)
        begin=time.monotonic();value=health.request();elapsed=time.monotonic()-begin
        self.assertLess(elapsed,.3);self.assertEqual(value['state'],'unknown');self.assertTrue(entered.is_set())
        for _ in range(5):self.assertEqual(health.request(timeout=0)['state'],'unknown')
        self.assertEqual(capture.status.call_count,1);release.set()

    def test_last_known_status_keeps_original_as_of_when_new_probe_hangs(self):
        capture=SimpleNamespace(status=Mock(return_value=dict(status='done',available=True)));health=M.HealthCache(capture);known=health.request();release=threading.Event();self.addCleanup(release.set)
        capture.status.side_effect=lambda:(release.wait(2) or dict(status='running'))
        value=health.request(force=True);self.assertEqual(value['state'],'last_known');self.assertEqual(value['as_of'],known['as_of']);self.assertEqual(value['capture']['status'],'done');release.set()

    def test_failed_or_malformed_probe_preserves_last_known_and_backs_off(self):
        capture=SimpleNamespace(status=Mock(return_value={}))
        health=M.HealthCache(capture)
        self.assertEqual(health.request()['state'],'unknown')
        health.request();self.assertEqual(capture.status.call_count,1)
        capture.status.return_value=dict(available=True,status='done')
        previous=health.request(force=True)
        capture.status.side_effect=OSError('SYNTHETIC_PRIVATE_ENDPOINT')
        value=health.request(force=True)
        self.assertEqual(value['state'],'last_known');self.assertEqual(value['as_of'],previous['as_of'])
        self.assertEqual(value['capture']['status'],'done')
        self.assertEqual(M.public_text('GitHub owner/repo'),'GitHub owner/repo')

    def test_force_during_probe_marks_its_late_answer_as_last_known(self):
        release=threading.Event();done=threading.Event();self.addCleanup(release.set)
        def slow():release.wait(2);done.set();return dict(status='idle',available=True)
        capture=SimpleNamespace(status=Mock(side_effect=slow));health=M.HealthCache(capture)
        health.request(timeout=0);health.request(force=True,timeout=0);release.set();self.assertTrue(done.wait(1))
        deadline=time.monotonic()+1
        while health.inflight is not None and time.monotonic()<deadline:time.sleep(.001)
        self.assertEqual(health.snapshot()['state'],'last_known')
        capture.status.side_effect=None;capture.status.return_value=dict(status='running',available=True)
        self.assertEqual(health.request()['capture']['status'],'running')

    def test_remote_transport_failure_is_unknown_or_last_known_not_a_successful_probe(self):
        from wechat_source_client import RemoteCapture
        client=SimpleNamespace(request=Mock(side_effect=ValueError('synthetic transport timeout')))
        health=M.HealthCache(RemoteCapture(client))
        self.assertEqual(health.request()['state'],'unknown')
        client.request.side_effect=None;client.request.return_value=dict(available=True,status='done')
        previous=health.request(force=True)
        client.request.side_effect=ValueError('synthetic transport timeout')
        failed=health.request(force=True)
        self.assertEqual(failed['state'],'last_known');self.assertEqual(failed['as_of'],previous['as_of'])
        self.assertEqual(failed['capture']['status'],'done')

    def test_health_allowlist_drops_private_fields_and_sanitizes_errors(self):
        value=M.safe_health(dict(status='error',api_key='synthetic-hidden',endpoint='http://private.example',detail='api_key=synthetic-hidden /private/cache http://private.example',last_success=dict(captured_at='2026-01-01T00:00:00Z',source_path='/private/cache')))
        for token in ('synthetic-hidden','private.example','/private/cache','source_path'):self.assertNotIn(token,json.dumps(value))
        self.assertEqual(value['last_success']['captured_at'],'2026-01-01T00:00:00Z')


if __name__=='__main__':unittest.main()
