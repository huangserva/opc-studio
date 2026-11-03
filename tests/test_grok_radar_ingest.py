import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import grok_radar_ingest as R
import today_view as T
import grok_bot_radar as G
import interest_sources as S
import interest_channel as I

FIXTURES = Path(__file__).parent/'fixtures/grok_radar'
NOW = datetime(2026,11,3,12,tzinfo=timezone.utc)

class GrokRadarTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='grok-radar-test-',dir='/tmp'); self.root=Path(self.temp.name)
        self.workspace=self.root/'workspace'; self.workspace.mkdir(); self.reports=self.root/'reports'; self.radar=self.reports/'radar'; self.radar.mkdir(parents=True)
        self.env=patch.dict(os.environ,{'GROK_BOT_REPORTS_DIR':str(self.reports)});self.env.start();self.addCleanup(self.env.stop);self.addCleanup(self.temp.cleanup)
        self.bundle=self.root/'bundle.json';self.bundle.write_text('{"tweets":[],"vip_tweets":[]}')
        self.patch=patch.object(G,'BUNDLE_PATH',self.bundle);self.patch.start();self.addCleanup(self.patch.stop)
        self.linux=patch.object(T.linuxdo,'refresh_background');self.linux.start();self.addCleanup(self.linux.stop)
    def put(self,name):
        path=self.radar/name;shutil.copyfile(FIXTURES/name,path);return path
    def rounds(self):
        self.put('2026-11-03-0800-baseline.json');return self.put('2026-11-03-1000-valid.json')
    def load(self):return R.load(self.workspace,NOW)
    def mutate(self,path,fn):
        data=json.loads(path.read_text());fn(data);path.write_text(json.dumps(data));return data
    def test_waiting_and_auxiliary_files(self):
        for name in ('state.json','targets-copy.json','manifest.json','2026-11-03-1800-half.json.tmp'):self.put(name)
        (self.radar/'2026-11-03-1000-run-shard-1.json').write_text('{')
        (self.radar/'2026-11-03-1000-manifest.json').write_text('{')
        rows,status=self.load();self.assertEqual(rows,[]);self.assertEqual(status['status'],'waiting');self.assertEqual(status['bad_files'],0)
    def test_valid_rules_null_partial_duplicate_bad_schema(self):
        for p in FIXTURES.iterdir():self.put(p.name)
        rows,status=self.load();by={r['tweet_id']:r for r in rows}
        self.assertEqual(len(by),3);self.assertEqual(by['90001']['events'],['new_fast']);self.assertIsNone(by['90002']['views']);self.assertIsNone(by['90002']['retweets'])
        self.assertEqual(by['90003']['events'],[]);self.assertEqual(status['duplicate_slots'],1);self.assertEqual(status['status'],'error');self.assertEqual(status['round_count'],4)
        self.assertIn('轮次不完整',status['error']);self.assertNotIn('state.json',status['error']);self.assertNotIn('half',status['error'])
    def test_single_round_is_silent_baseline(self):
        self.put('2026-11-03-1000-valid.json');rows,status=self.load();self.assertEqual(rows[0]['events'],[]);self.assertIn('本地规则',status['error'])
    def retry(self,path,*,complete,started,views,name):
        data=json.loads(path.read_text())
        data.update(run_id=name,round_started_at=started,finished_at=started,events=[],baseline=False,
                    status='incomplete' if complete is False else 'ok',all_groups_complete=complete is not False)
        for account in data['accounts']:account['truncated']=1 if complete is None else False
        data['observations'][0].update(views=views,fetched_at=started)
        target=self.radar/('2026-11-03-1000-'+name+'.json');target.write_text(json.dumps(data));return target
    def test_M1_success_unknown_retry_beats_earlier_incomplete(self):
        path=self.rounds();path.unlink();template=FIXTURES/'2026-11-03-1000-valid.json'
        self.retry(template,complete=False,started='2026-11-03T02:00:00Z',views=11111,name='incomplete')
        self.retry(template,complete=None,started='2026-11-03T02:01:00Z',views=99999,name='unknown')
        rows,state=self.load();row=next(r for r in rows if r['tweet_id']=='90001')
        self.assertEqual(row['views'],99999);self.assertEqual(row['events'],[])
        self.assertEqual(state['status'],'ok');self.assertIsNone(state['complete_coverage'])
        self.assertEqual(state['note'],'截断情况未知');self.assertIsNone(state['error'])
        self.assertEqual(state['duplicate_slots'],1)
    def test_M1_complete_beats_newer_unknown_and_failed(self):
        path=self.rounds();self.mutate(path,lambda d:d.update(events=[]))
        self.retry(path,complete=None,started='2026-11-03T02:01:00Z',views=99999,name='unknown')
        self.retry(path,complete=False,started='2026-11-03T02:02:00Z',views=11111,name='failed')
        rows,state=self.load();self.assertEqual(rows[0]['views'],8000)
        self.assertTrue(state['complete_coverage']);self.assertEqual(state['note'],'');self.assertEqual(state['status'],'ok')
    def test_M1_newer_retry_wins_within_each_completeness_tier(self):
        template=FIXTURES/'2026-11-03-1000-valid.json'
        for complete in (True,None,False):
            with self.subTest(complete=complete):
                for path in self.radar.glob('*.json'):path.unlink()
                self.retry(template,complete=complete,started='2026-11-03T02:00:00Z',views=11111,name='older')
                self.retry(template,complete=complete,started='2026-11-03T02:01:00Z',views=99999,name='newer')
                rows,state=self.load();self.assertEqual(rows[0]['views'],99999);self.assertEqual(state['duplicate_slots'],1)
    def interest_page(self):
        with patch.object(I,'topics',return_value={'topics':[]}):
            data=S.build(self.workspace,NOW)
            cache=dict(ready=True,partial=False,refreshing=False,error=None,built_at=NOW.isoformat(),build_ms=0)
            with patch.object(S,'candidates',return_value=(data,cache)):
                return I._cross_source_page(self.workspace,{'range':['today']},NOW)
    def test_M2_interest_exit_reports_waiting_ok_unknown_and_error(self):
        for condition in ('waiting','ok','unknown','error'):
            with self.subTest(condition=condition):
                for path in self.radar.glob('*.json'):path.unlink()
                if condition in ('ok','unknown'):
                    path=self.put('2026-11-03-0800-baseline.json')
                    if condition=='unknown':self.mutate(path,lambda d:[a.update(truncated=1) for a in d['accounts']])
                if condition=='error':self.put('2026-11-03-1600-bad.json')
                page=self.interest_page();state=page['sources']['grok:radar']
                self.assertEqual(state,self.load()[1]);self.assertEqual(state,T.snapshot(self.workspace,NOW)[1]['grok:radar'])
                self.assertEqual(state['status'],'ok' if condition=='unknown' else condition)
                if condition=='unknown':self.assertIsNone(state['complete_coverage']);self.assertEqual(state['note'],'截断情况未知')
                if condition in ('ok','unknown'):self.assertIsNotNone(state['updated_at'])
    def test_M2_bad_radar_leaves_other_interest_sources_unchanged(self):
        (self.reports/'daily').mkdir();(self.reports/'daily/2026-11-03-1000.json').write_text(json.dumps(dict(collected_at='2026-11-03T02:00:00Z',posts=[])))
        before=self.interest_page()['sources'];self.put('2026-11-03-1600-bad.json');after=self.interest_page()['sources']
        self.assertEqual(after['grok:radar']['status'],'error')
        self.assertEqual({k:v for k,v in before.items() if k not in ('grok:radar','跨来源索引')},
                         {k:v for k,v in after.items() if k not in ('grok:radar','跨来源索引')})
    def test_schema_failures_are_isolated(self):
        path=self.rounds();base=json.loads(path.read_text())
        changes=[lambda d:d.update(target_list_sha='bad'),lambda d:d.update(accounts=[]),lambda d:d['observations'][0].update(views='8K'),lambda d:d['observations'][0].update(views=True),lambda d:d['observations'][0].update(views=2**53),lambda d:d['observations'][0].update(fetched_at='2026-11-03 02:00:00'),lambda d:d['observations'][0].update(tweet_id='１２３')]
        for change in changes:
            with self.subTest(change=change):
                data=copy.deepcopy(base);change(data);path.write_text(json.dumps(data));rows,status=self.load();self.assertEqual(status['status'],'error');
                if change == changes[1]:self.assertTrue(rows[0]['is_stale'])
                else:self.assertFalse(rows)
        path.write_text(json.dumps(base));self.assertEqual(self.load()[1]['status'],'ok')
    def test_author_mismatch_does_not_trigger_event(self):
        path=self.rounds();self.mutate(path,lambda d:d['observations'][0].update(queried_handle='vista8'));rows,status=self.load();self.assertEqual(rows[0]['events'],[])
    def test_local_rules_boundaries(self):
        path=self.rounds();base=json.loads(path.read_text());base['events']=[]
        cases=[({'views':2500,'published_at':'2026-11-03T01:45:00Z'},'new_fast'),({'views':2499,'published_at':'2026-11-03T01:45:00Z'},None),({'views':50000,'followers':10000,'likes':200,'published_at':'2026-11-02T20:00:00Z'},'lowfan_burst'),({'views':50000,'followers':10001,'likes':200,'published_at':'2026-11-02T20:00:00Z'},'new_fast'),({'views':50000,'followers':10000,'likes':199,'published_at':'2026-11-02T20:00:00Z'},'new_fast'),({'views':50000,'published_at':'2026-11-03T03:00:00Z'},None),({'views':50000,'is_stale':True},None),({'views':None},None)]
        for fields,expected in cases:
            with self.subTest(fields=fields):
                data=copy.deepcopy(base);data['observations'][0].update(fields);path.write_text(json.dumps(data));events=self.load()[0][0]['events'];self.assertEqual(events,[expected] if expected else [])
    def test_cross_round_delta_falling_and_no_resurrection(self):
        path=self.rounds();self.mutate(path,lambda d:(d.update(events=[]),d['observations'][0].update(views=1000)))
        data=json.loads(path.read_text());data.update(run_id='r2',scheduled_slot='2026-11-03T04:00:00Z',round_started_at='2026-11-03T04:00:00Z',finished_at='2026-11-03T04:05:00Z');data['observations'][0].update(fetched_at='2026-11-03T04:00:00Z',views=11000)
        target=self.radar/'2026-11-03-1200-delta.json';target.write_text(json.dumps(data));rows,_=self.load();self.assertEqual(rows[0]['events'],['hourly_delta']);self.assertEqual(rows[0]['hourly_delta'],5000)
        data['observations'][0]['views']=999;target.write_text(json.dumps(data));self.assertEqual(self.load()[0][0]['events'],[])
    def test_sent_claim_does_not_authorize_upgrade(self):
        path=self.rounds();self.mutate(path,lambda d:d['events'][0].update(event_type='upgrade_100k',delivery_status='sent'));rows,status=self.load();self.assertNotIn('upgrade_100k',rows[0]['events']);self.assertIn('本地规则',status['error'])
    def test_real_slot_count_misfill_and_second_precision_tolerance(self):
        path=self.put('2026-11-03-1000-valid.json');data=json.loads(path.read_text())
        data.update(scheduled_slot='06',baseline=True,events=[])
        for account in data['accounts']:account['truncated']=9
        data['observations'][0]['fetched_at']='2026-11-03T02:05:00.838Z'
        path.unlink();path=self.radar/'06-2026-11-03T06-baseline-run.json';path.write_text(json.dumps(data))
        rows,status=self.load();self.assertEqual(len(rows),1);self.assertEqual(status['status'],'ok');self.assertIsNone(status['complete_coverage']);self.assertEqual(status['note'],'截断情况未知');self.assertEqual(rows[0]['events'],[])
        data['observations'][0]['fetched_at']='2026-11-03T02:05:01.001Z';path.write_text(json.dumps(data));self.assertEqual(self.load()[1]['status'],'error')
    def test_verified_2500_event_bypasses_legacy_metric_gate(self):
        path=self.rounds();self.mutate(path,lambda d:d['observations'][0].update(views=2500,likes=1,published_at='2026-11-03T01:45:00Z'))
        page=T.page_view(self.workspace,{'range':['today']},NOW)
        self.assertIn('x:90001',[r['id'] for r in page['items']]);self.assertEqual(page['items'][0]['radar_events'],['new_fast'])
    def test_baseline_metric_gate_and_lowfan_120_limit(self):
        path=self.put('2026-11-03-1000-valid.json');data=json.loads(path.read_text());row=data['observations'][0];data.update(baseline=True,events=[])
        data['observations']=[]
        for i in range(121):
            tid=str(1000+i);data['observations'].append(dict(row,tweet_id=tid,url='https://x.com/dotey/status/'+tid,views=5000+i,likes=1,followers=10000))
        path.write_text(json.dumps(data));rows,status=self.load();selected=R.discovery_rows(rows)
        self.assertEqual(len(selected),120);self.assertNotIn('1000',[r['tweet_id'] for r in selected]);self.assertFalse(any(r['events'] for r in selected))
        row.update(views=14999,likes=99,followers=10001);data['observations']=[row];path.write_text(json.dumps(data));self.assertEqual(R.discovery_rows(self.load()[0]),[])
        row['likes']=100;path.write_text(json.dumps(data));self.assertEqual(len(R.discovery_rows(self.load()[0])),1)
    def test_interest_matches_old_aisa_nonstale_observation_scope(self):
        path=self.rounds();data=json.loads(path.read_text());row=data['observations'][0]
        data['observations']=[dict(row,type='reply'),dict(row,tweet_id='99991',url='https://x.com/dotey/status/99991',is_stale=True)]
        data['events']=[];path.write_text(json.dumps(data));items=S.parse_file(path,'grok:radar')
        self.assertEqual([r['id'] for r in items],['x:90001'])
    def test_local_sent_allows_upgrade_and_pending_does_not(self):
        path=self.rounds();self.mutate(path,lambda d:(d.update(events=[]),d['observations'][0].update(views=100000)))
        old=self.workspace/'twitter-radar/monitor/state.json';old.parent.mkdir(parents=True)
        event=dict(event_id='radar:tweet:90001:initial',type='new_fast',status='sent',tweet={'tweet_id':'90001'},created_at='2026-11-03T01:00:00Z')
        data=dict(initialized=True,samples={},events=[event],pushed={'90001':{'upgraded':False}});old.write_text(json.dumps(data))
        self.assertIn('upgrade_100k',self.load()[0][0]['events'])
        data['events'][0]['status']='pending';old.write_text(json.dumps(data));self.assertNotIn('upgrade_100k',self.load()[0][0]['events'])
    def test_G_filter_includes_radar_and_null_stays_unknown(self):
        self.rounds();self.put('2026-11-03-1200-null.json')
        page=T.page_view(self.workspace,{'range':['today'],'source':['G']},NOW)
        row=next(r for r in page['items'] if r['id']=='x:90002');self.assertIsNone(row['metrics']['views']);self.assertIsNone(row['author']['followers']);self.assertEqual(row['channels'],['grok'])
    def test_first_arrival_and_cross_source_merge(self):
        self.rounds();(self.reports/'daily').mkdir();(self.reports/'daily/2026-11-02-0800.json').write_text(json.dumps(dict(collected_at='2026-11-02T00:00:00Z',posts=[dict(id='90001',handle='dotey',text='AI Agent 旧日报保留正文与日期',created_at='2026-11-01T23:00:00Z',url='https://x.com/dotey/status/90001',views=100)])))
        self.bundle.write_text(json.dumps(dict(tweets=[],vip_tweets=[dict(id='90001',user='dotey',text='AI AIsa 历史观察',url='https://x.com/dotey/status/90001',published_at='2026-11-01T23:00:00Z',fetched_at='2026-11-02T00:00:00Z',views=11000)])))
        rows,states=T.snapshot(self.workspace,NOW);row=next(r for r in rows if r['id']=='x:90001');self.assertEqual(len([r for r in rows if r['id']=='x:90001']),1);self.assertEqual(row['sources'],['A','G','grok:radar']);self.assertEqual(row['metrics']['views'],8000);self.assertEqual(row['first_seen_at'],'2026-11-02T00:00:00+00:00')
        today=T.page_view(self.workspace,{'range':['today']},NOW);self.assertNotIn('x:90001',[r['id'] for r in today['items']]);self.assertEqual(states['grok:radar']['status'],'ok')
        rows,_=T.snapshot(self.workspace,NOW+timedelta(days=1));self.assertEqual(next(r for r in rows if r['id']=='x:90001')['first_seen_at'],row['first_seen_at'])
    def test_today_uses_arrival_and_interest_ingests(self):
        path=self.rounds();self.mutate(path,lambda d:d['observations'][0].update(published_at='2026-11-02T23:00:00Z'))
        page=T.page_view(self.workspace,{'range':['today']},NOW);self.assertIn('x:90001',[r['id'] for r in page['items']]);self.assertEqual(page['sources']['A']['status'],'disabled')
        items=S.build(self.workspace,NOW)['items'];row=next(r for r in items if r['id']=='x:90001');self.assertEqual(row['platform'],'x');self.assertIn('grok',row['channels']);self.assertEqual(row['author']['handle'],'dotey');self.assertEqual(row['metrics']['views'],8000)
    def test_bad_round_does_not_break_daily_or_interest(self):
        self.put('2026-11-03-1600-bad.json');(self.reports/'daily').mkdir();(self.reports/'daily/2026-11-03-1000.json').write_text(json.dumps(dict(collected_at='2026-11-03T02:00:00Z',posts=[dict(id='7',handle='dotey',full_text='Claude Agent 开源自动化，这是正常日报来源。',created_at='2026-11-03T01:00:00Z',url='https://x.com/dotey/status/7')])));
        rows,states=T.snapshot(self.workspace,NOW);self.assertIn('x:7',[r['id'] for r in rows]);self.assertEqual(states['grok:radar']['status'],'error');self.assertIn('x:7',[r['id'] for r in S.build(self.workspace,NOW)['items']])

if __name__=='__main__':unittest.main()
