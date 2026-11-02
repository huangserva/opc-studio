import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from clue_store_v2 import *
from clue_ingest import import_document, ingest

TOPICS=['测试方向'+str(i) for i in range(9)]
ROW=dict(id='123456789',url='https://x.com/test/status/123456789',full_text='公开 AI 实测',content_complete=True,views=9007199254740993,likes=None,created_at='2026-10-29T03:00:00Z')
META=dict(collected_at='2026-10-30T03:00:00Z',items=[ROW])

class StoreV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='clue-v2-',dir='/tmp');self.root=Path(self.tmp.name);self.path=self.root/'clues.db'
        assert_write_path(self.path);self.db=sqlite3.connect(self.path)
        self.db.executescript('CREATE TABLE threads(id TEXT PRIMARY KEY,data TEXT,revision INT,seen_revision INT,created_at TEXT,updated_at TEXT); CREATE TABLE entries(id TEXT PRIMARY KEY,thread_id TEXT,data TEXT,created_at TEXT,clock TEXT,entry_type TEXT,producer_key TEXT); CREATE TABLE observations(id TEXT);')
        self.db.close();schema(self.path,'a',TOPICS)
    def tearDown(self):self.tmp.cleanup()
    def count(self,table):
        with connect(self.path,True) as db:return db.execute('SELECT count(*) FROM '+table).fetchone()[0]
    def load(self,source='daily',data=None,file='fixture.json',batch='a',fault=None):
        data=data or copy.deepcopy(META)
        return import_document(self.path,source,file,data,digest(data),batch,fault=fault)
    def test_schema_seed_and_idempotence(self):
        schema(self.path,'a',TOPICS)
        self.assertEqual(self.count('topics'),10)
        with connect(self.path,True) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM topics WHERE kind='direction'").fetchone()[0],9)
            self.assertEqual(db.execute("SELECT kind FROM topics WHERE id='collection:tools'").fetchone()[0],'collection')
        self.assertGreaterEqual(len(COLUMNS),21)
    def test_four_channels_one_object_raw_metrics(self):
        for s,value in [('daily',100),('alerts',200),('interest',10),('bookmarks',5)]:
            data=copy.deepcopy(META);data['items'][0]['views']=value;self.load(s,data)
        d=detail(self.path,'x:123456789')
        self.assertEqual(self.count('clue_objects'),1);self.assertEqual(len(d['channels']),4)
        self.assertEqual(sorted(x['raw_metrics']['views'] for x in d['observations']),[5,10,100,200])
        self.assertEqual(len(d['content_versions']),4)
    def test_unknown_metrics_null_reasons(self):
        self.load();d=detail(self.path,'x:123456789');o=d['observations'][0]
        self.assertIsNone(o['raw_metrics']['likes']);self.assertEqual(o['missing_reasons']['likes'],'not_provided')
    def test_large_integer_decimal_wire(self):
        self.load();d=detail(self.path,'x:123456789');self.assertEqual(d['observations'][0]['raw_metrics']['views'],'9007199254740993')
        with connect(self.path,True) as db:self.assertEqual(json.loads(db.execute('SELECT raw_metrics_json FROM metric_observations').fetchone()[0])['views'],9007199254740993)
    def test_raw_author_followers_survive_public_metrics_container(self):
        data=copy.deepcopy(META);data['items'][0].update(author_followers=9007199254740995,public_metrics={'impression_count':15,'like_count':2})
        self.load('bookmarks',data);value=detail(self.path,'x:123456789')['observations'][0]['raw_metrics'];self.assertEqual(value['followers'],'9007199254740995');self.assertEqual(value['views'],15)
        self.assertEqual(raw_metrics({'author_fans':0})[0]['followers'],0)

    def test_missing_bookmark_time_not_export_time(self):
        data=copy.deepcopy(META);data['exported_at']='2026-10-30T15:00:00+08:00';self.load('bookmarks',data)
        self.assertIsNone(detail(self.path,'x:123456789')['channels'][0]['bookmarked_at'])
    def test_multiple_folders_topic_suggested(self):
        data=copy.deepcopy(META);data['items'][0].update(folders=['one','two'],suggested_topic=TOPICS[0]);self.load('bookmarks',data)
        d=detail(self.path,'x:123456789');self.assertEqual(len(d['folders']),2);self.assertEqual(d['topic_assignments'][0]['state'],'suggested')
    def test_replay_no_duplicates(self):
        self.load();counts={t:self.count(t) for t in COLUMNS};r=self.load();self.assertEqual(r['skipped'],1)
        self.assertEqual(self.count('clue_objects'),counts['clue_objects']);self.assertEqual(self.count('metric_observations'),counts['metric_observations'])
    def test_file_rewrite_preserves_human_state_and_content(self):
        self.load()
        with transaction(self.path) as db:db.execute("UPDATE clue_objects SET triage_state='discarded',verification_state='verified',title='手改',revision=8")
        changed=copy.deepcopy(META);changed['items'][0]['full_text']='新的公开正文';changed['items'][0]['views']=123;self.load(data=changed)
        d=detail(self.path,'x:123456789');self.assertEqual(d['title'],'手改');self.assertEqual(d['triage_state'],'discarded');self.assertEqual(d['revision'],8);self.assertEqual(len(d['content_versions']),2)
    def test_x_source_cannot_accept_conflicting_platform_url(self):
        for source in ('daily','alerts','interest','bookmarks'):
            with self.subTest(source=source):
                data=copy.deepcopy(META);data['items'][0]['url']='https://github.com/example/project'
                result=self.load(source,data,file=source+'-conflict.json');self.assertEqual(result['bad'],1);self.assertEqual(self.count('clue_objects'),0)

    def test_bad_sibling_quarantine_continue(self):
        data=copy.deepcopy(META);data['items'] += ['bad',dict(id='999',url='https://x.com/i/status/888'),dict(ROW,id='777',url='https://twitter.com/i/status/777')]
        r=self.load(data=data);self.assertEqual(r['bad'],2);self.assertEqual(self.count('clue_objects'),2);self.assertEqual(self.count('source_receipts'),4)
    def test_failure_checkpoint_and_rows_rollback(self):
        def fail(_):raise RuntimeError('simulated_crash')
        with self.assertRaises(RuntimeError):self.load(fault=fail)
        self.assertEqual(self.count('ingest_checkpoints'),0);self.assertEqual(self.count('source_receipts'),0);self.assertEqual(self.count('clue_objects'),0)
        self.load();self.assertEqual(self.count('ingest_checkpoints'),1)
    def test_unknown_observation_time_null_idempotent(self):
        data={'items':[ROW]};self.load(data=data);self.load(data={**data,'rewritten':True});self.assertEqual(self.count('metric_observations'),1)
        self.assertIsNone(detail(self.path,'x:123456789')['observations'][0]['observed_at'])
    def test_crosspost_only_proposal(self):
        self.load();data=copy.deepcopy(META);data['items'][0].update(id='456',url='https://x.com/a/status/456');self.load(data=data)
        with transaction(self.path) as db:
            ids=[r[0] for r in db.execute('SELECT id FROM clue_objects')]
            pid=propose_merge(db,ids,.97,'两个独立帖子描述相同版本发布，来源待人工确认','a')
            p=db.execute('SELECT * FROM merge_proposals WHERE id=?',(pid,)).fetchone();self.assertEqual(p['confidence'],.97);self.assertTrue(p['suggested_at']);self.assertTrue(p['reason'])
        self.assertEqual(self.count('content_events'),0);self.assertEqual(self.count('event_members'),0);self.assertEqual(self.count('clue_objects'),2)
    def test_rejected_proposal_not_reopened(self):
        self.test_crosspost_only_proposal()
        with transaction(self.path) as db:
            p=db.execute('SELECT * FROM merge_proposals').fetchone();ids=json.loads(p['member_ids_json'])
            write(db,'merge_decisions','decision1',dict(proposal_id=p['id'],decision='rejected',actor='owner',decided_at=now(),before_json='{}',after_json='{}',revision=1),'a')
            self.assertIsNone(propose_merge(db,ids,.99,'重报','a'))
    def test_low_confidence_not_pushed(self):
        with transaction(self.path) as db:
            with self.assertRaises(ValueError):propose_merge(db,['a','b'],.6,'不确定','a')
    def test_rollback_only_batch_old_tables_unchanged(self):
        with connect(self.path,True) as db:before={t:db.execute('SELECT * FROM '+t).fetchall() for t in ['threads','entries','observations']}
        self.load();r=rollback(self.path,'a');self.assertEqual(r['retained'],[]);self.assertEqual(self.count('clue_objects'),0)
        with connect(self.path,True) as db:self.assertEqual(before,{t:db.execute('SELECT * FROM '+t).fetchall() for t in before})
        schema(self.path,'b',TOPICS);self.load(batch='b');self.assertEqual(self.count('clue_objects'),1)
    def test_rollback_retains_later_human_state(self):
        self.load()
        with transaction(self.path) as db:db.execute("UPDATE clue_objects SET triage_state='discarded',revision=2")
        r=rollback(self.path,'a');self.assertTrue(r['retained']);self.assertEqual(detail(self.path,'x:123456789')['triage_state'],'discarded')
    def test_rollback_retains_later_other_batch(self):
        self.load();schema(self.path,'b',TOPICS);self.load('alerts',batch='b');r=rollback(self.path,'a')
        self.assertEqual(len(detail(self.path,'x:123456789')['channels']),1);self.assertEqual(detail(self.path,'x:123456789')['channels'][0]['channel'],'grok:alerts');self.assertTrue(r['retained'])
    def test_real_path_guard_blocks_before_connection(self):
        # Mutation must fail before any actual real-store connection can be made.
        with patch('clue_store_v2.connect',side_effect=RuntimeError('UNSAFE_CONNECTION_ATTEMPT')) as mocked:
            try:
                with transaction(REAL_DB):pass
            except ValueError as exc:
                self.assertIn('K1_ISOLATION',str(exc))
            except RuntimeError:
                self.fail('F: 去掉 tmp 隔离后触达了数据库连接')
            else:
                self.fail('F: 真实路径未被拦截')
            mocked.assert_not_called()
    def test_symlink_escape_rejected(self):
        link=self.root/'escape';link.symlink_to(ROOT/'workspace',target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'K1_ISOLATION'):assert_write_path(link/'clues/clues.db')
    def test_safe_links_and_secret_redaction(self):
        for url in ['https://site.test/?api_key=synthetic_key_value','https://site.test/login','https://user:pass@site.test/a']:
            self.assertIsNone(safe_url(url))
        self.assertNotIn('synthetic_key_value',safe_text('a https://site.test/a?token=synthetic_key_value b'))
        self.assertEqual(safe_url('https://site.test/a?version=2&utm_source=x#h'),'https://site.test/a?version=2')
    def test_public_body_preserves_normal_urls_verbatim(self):
        body='正文 https://example.test/a/?utm_source=x&version=2#section 及 https://x.com/a/status/123?s=20 完整保留。'
        self.assertEqual(safe_text(body),body)
        data=copy.deepcopy(META);data['items'][0]['full_text']=body;data['items'][0]['article']={'body':body};self.load('bookmarks',data)
        value=detail(self.path,'x:123456789')['content_versions'][0];self.assertEqual(value['body'],body);self.assertEqual(value['rich']['article']['body'],body)

    def test_utc_does_not_invent_timezone(self):
        self.assertIsNone(utc('2026-10-30T12:00:00'));self.assertEqual(utc('2026-10-30T12:00:00+08:00'),'2026-10-30T04:00:00+00:00')
    def test_metrics_reject_float_bool_and_formatted(self):
        raw,reasons=raw_metrics(dict(views=1.5,likes=True,bookmarks='1.2K',replies=0))
        self.assertIsNone(raw['views']);self.assertIsNone(raw['likes']);self.assertIsNone(raw['bookmarks']);self.assertEqual(raw['replies'],0);self.assertIn('views',reasons)
    def test_canonical_platform_variants(self):
        self.assertEqual(canonical(dict(url='https://twitter.com/i/status/123'))[0],'x:123')
        self.assertEqual(canonical(dict(url='https://x.com/i/status/123'))[0],'x:123')
        self.assertEqual(canonical(dict(url='https://linux.do/t/topic/123'))[0],'linuxdo:123')
        for url,wanted in [('https://github.com/A/B','github:a/b:repo'),('https://github.com/A/B/releases/tag/v2','github:a/b:release:v2'),('https://github.com/A/B/issues/3','github:a/b:issue:3'),('https://github.com/A/B/pull/3','github:a/b:pr:3')]:self.assertEqual(canonical(dict(url=url))[0],wanted)
    def test_video_two_boards_platforms(self):
        row=dict(id='123',platform='douyin',title='AI',url='https://www.douyin.com/video/123',likes=12)
        data=dict(generated_at='2026-10-30T00:00:00Z',lists={'daily':[row],'realtime':[row],'xhs':[dict(id='abcdef123456789012345678',platform='xiaohongshu',url='https://www.xiaohongshu.com/explore/abcdef123456789012345678')]})
        self.load('video',data);self.assertEqual(self.count('clue_objects'),2);self.assertEqual(len(detail(self.path,'douyin:123')['channels']),2);self.assertIsNotNone(detail(self.path,'xhs:abcdef123456789012345678'))
    def test_first_and_last_seen_keep_distinct_source_times(self):
        row=dict(id='99',platform='douyin',url='https://www.douyin.com/video/99',first_seen_at='2026-10-01T00:00:00Z',last_seen_at='2026-10-30T04:00:00Z',title='AI')
        self.load('video',dict(lists={'daily':[row]}));value=detail(self.path,'douyin:99')
        self.assertEqual(value['first_seen_at'],'2026-10-01T00:00:00+00:00');self.assertEqual(value['last_seen_at'],'2026-10-30T04:00:00+00:00');self.assertEqual(value['channels'][0]['last_seen_at'],value['last_seen_at'])
        row['last_seen_at']='2026-10-30T05:00:00Z';self.load('video',dict(lists={'daily':[row]}));value=detail(self.path,'douyin:99');self.assertEqual(value['first_seen_at'],'2026-10-01T00:00:00+00:00');self.assertEqual(value['last_seen_at'],'2026-10-30T05:00:00+00:00')

    def test_linux_rss_observation_metadata(self):
        self.load('linuxdo',dict(updated_at='2026-10-30T00:00:00Z',items=[dict(id='url:old',topic_id='44',title='Claude AI 实测',url='https://linux.do/t/44',full_text='AI',metrics={'replies':3},rss_boards=['AI'],reply_delta=2,sources=['L'])]))
        d=detail(self.path,'linuxdo:44');self.assertEqual(d['incomplete_reason'],'rss_excerpt_only');self.assertEqual(d['observations'][0]['raw_metrics']['replies'],3);self.assertIn('relevance_gate',d['channels'][0]['metadata']);self.assertIn('url:old',[x['alias'] for x in d['aliases']])
    def test_questions_cluster_no_frequency_multiplication(self):
        row=dict(cluster_id='c1',question='怎么配置 Agent?',parent_post_url='https://x.com/a/status/123',frequency=99,replies=[dict(id='1',url='https://x.com/a/status/1'),dict(id='1',url='https://x.com/a/status/1')])
        self.load('questions',dict(items=[row]));d=detail(self.path,'question:c1');self.assertEqual(self.count('clue_objects'),1);self.assertEqual(d['content_versions'][0]['rich']['frequency'],99);self.assertEqual(len(d['content_versions'][0]['rich']['replies']),1)
    def test_questions_generated_cluster_stable(self):
        from clue_sources.questions import parse
        a=dict(question=' HOW  Agent? ',parent_post_url='https://twitter.com/a/status/123',frequency=1)
        b={**a,'question':'how agent?','parent_post_url':'https://x.com/a/status/123'}
        self.assertEqual(parse(a,{})['canonical_key'],parse(b,{})['canonical_key'])
    def test_legacy_private_no_body_copy(self):
        private='私密原文不可复制的合成片段'
        e=dict(id='e1',thread_id='t1',data=dumps(dict(evidence=[dict(kind='wechat',document_id='doc1',hash='h',start=0,end=10,excerpt=private,title=private,provenance={'text':private})])),created_at=now(),clock=now(),entry_type='evidence',producer_key=None)
        t=dict(id='t1',data=dumps(dict(title=private,summary=private,status='verified',tracking=True,event_id='old-task')),revision=7,seen_revision=2,created_at=now(),updated_at=now(),entries=[e])
        self.load('legacy',dict(items=[t]));d=detail(self.path,'legacy:t1');self.assertNotIn(private,dumps(d));self.assertEqual(d['content_versions'],[])
        state=d['legacy_links'][0]['legacy_state'];self.assertEqual(state['revision'],7);self.assertEqual(state['seen_revision'],2);self.assertEqual(state['legacy_task_event_id'],'old-task');self.assertTrue(state['unread'])
    def test_inbox_cursor_filters(self):
        for n in range(5):
            data=copy.deepcopy(META);data['items'][0].update(id=str(n+1),url='https://x.com/a/status/'+str(n+1));self.load(data=data,file=str(n))
        a=inbox(self.path,limit=2,source='daily');b=inbox(self.path,limit=2,cursor=a['next_cursor']);self.assertEqual(len(a['items']),2);self.assertFalse({x['id'] for x in a['items']}&{x['id'] for x in b['items']})
        self.assertEqual(inbox(self.path,channel='bad')['items'],[])
        with self.assertRaises(ValueError):inbox(self.path,cursor='badcursor')
    def test_each_source_rewrite_preserves_human_tombstone(self):
        from clue_sources import legacy
        legacy_row=dict(id='old1',data=dumps(dict(title='old',status='verified')),revision=3,seen_revision=1,created_at=now(),updated_at=now(),entries=[])
        question=dict(cluster_id='q1',question='如何用 Agent',parent_post_url='https://x.com/a/status/1',frequency=2)
        documents={source:copy.deepcopy(META) for source in ('daily','alerts','interest','bookmarks')}
        documents['linuxdo']=dict(items=[dict(topic_id='8',title='AI',full_text='Claude AI',url='https://linux.do/t/8',metrics={'replies':1})])
        documents['video']=dict(lists={'daily':[dict(id='9',platform='douyin',title='AI',url='https://www.douyin.com/video/9')]})
        documents['questions']=dict(items=[question]);documents['legacy']=dict(items=[legacy_row])
        for source,data in documents.items():
            with self.subTest(source=source):
                self.load(source,data,file=source+'.json')
                with transaction(self.path) as db:db.execute("UPDATE clue_objects SET triage_state='discarded',verification_state='verified',revision=44")
                count=self.count('clue_objects');data['file_rewritten']=True;result=self.load(source,data,file=source+'.json')
                self.assertEqual(self.count('clue_objects'),count);self.assertEqual(result['bad'],0)
                with connect(self.path,True) as db:self.assertEqual(db.execute("SELECT count(*) FROM clue_objects WHERE triage_state!='discarded' OR revision!=44 OR verification_state!='verified'").fetchone()[0],0)
    def test_bad_file_quarantine_no_checkpoint(self):
        folder=self.root/'reports/daily';folder.mkdir(parents=True);(folder/'bad.json').write_text('{broken')
        r=ingest(self.path,self.root/'reports',self.root/'workspace','a',sources=['daily'])
        self.assertEqual(r['daily']['bad_files'],1);self.assertEqual(self.count('source_receipts'),1);self.assertEqual(self.count('ingest_checkpoints'),0)

    def _snapshot_fixture(self):
        stamp='2026-10-30T00:00:00+00:00'
        with sqlite3.connect(self.path) as db:db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?)',('old',dumps(dict(title='合成旧线索',status='candidate')),1,0,stamp,stamp))
        snap=self.root/'frozen.db';src=connect(self.path,True);dest=sqlite3.connect(snap);src.backup(dest);src.close();dest.close();return snap
    def test_snapshot_concurrent_insert_then_catchup(self):
        import threading
        from clue_sources.legacy import snapshot
        snap=self._snapshot_fixture();data=snapshot(self.path);started=threading.Event();errors=[]
        def writer():
            try:
                with sqlite3.connect(self.path,timeout=3) as db:
                    started.set();db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?)',('new',dumps(dict(title='并发新增',status='candidate')),1,0,now(),now()))
            except Exception as exc:errors.append(type(exc).__name__)
        thread=threading.Thread(target=writer)
        def concurrent(_):thread.start();self.assertTrue(started.wait(2))
        import_document(self.path,'legacy','snapshot.json',data,digest(data),'a',fault=concurrent);thread.join(3);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        guard=verify_snapshot_rows(self.path,snap,['threads','entries','observations'],include_new_keys=['threads']);self.assertTrue(guard['equal']);self.assertEqual(guard['tables']['threads']['snapshot_count'],1);self.assertEqual(guard['tables']['threads']['new_keys'],[{'id':'new'}]);self.assertEqual(self.count('legacy_object_links'),1)
        catchup=dict(items=[r for r in snapshot(self.path)['items'] if r['id']=='new']);import_document(self.path,'legacy','catchup.json',catchup,digest(catchup),'a');self.assertEqual(self.count('legacy_object_links'),2);self.assertTrue(verify_snapshot_rows(self.path,snap,['threads'])['equal'])
    def test_snapshot_existing_modification_is_failure(self):
        snap=self._snapshot_fixture()
        with sqlite3.connect(self.path) as db:db.execute('UPDATE threads SET revision=revision+1 WHERE id=?',('old',))
        guard=verify_snapshot_rows(self.path,snap,['threads']);self.assertFalse(guard['equal']);self.assertNotEqual(guard['tables']['threads']['snapshot_sha256'],guard['tables']['threads']['protected_sha256'])
    def test_snapshot_precomputed_manifest_still_checks_current_rows(self):
        snap=self._snapshot_fixture();guard=verify_snapshot_rows(self.path,snap,['threads']);old=guard['tables']['threads'];manifest={'threads':{'count':old['snapshot_count'],'sha256':old['snapshot_sha256']}}
        self.assertTrue(verify_snapshot_rows(self.path,snap,['threads'],snapshot_manifest=manifest)['equal'])
        with sqlite3.connect(self.path) as db:db.execute('UPDATE threads SET seen_revision=1 WHERE id=?',('old',))
        self.assertFalse(verify_snapshot_rows(self.path,snap,['threads'],snapshot_manifest=manifest)['equal'])

    def test_snapshot_existing_deletion_is_failure(self):
        snap=self._snapshot_fixture()
        with sqlite3.connect(self.path) as db:db.execute('DELETE FROM threads WHERE id=?',('old',))
        guard=verify_snapshot_rows(self.path,snap,['threads']);self.assertFalse(guard['equal']);self.assertEqual(guard['tables']['threads']['protected_count'],0)

    def test_missing_source_honest_empty(self):
        r=ingest(self.path,self.root/'reports',self.root/'workspace','a',sources=['questions']);self.assertEqual(r['questions']['read'],0);self.assertEqual(r['questions']['files'],0)

if __name__=='__main__':unittest.main()
