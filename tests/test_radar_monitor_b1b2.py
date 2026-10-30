"""B1-b2 boundary/recovery probes; actual B2 consumers, all mutable data in /tmp."""
import copy
from datetime import timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import test_radar_monitor as b1
from scripts import radar_monitor as code

ROOT = Path(__file__).resolve().parents[1]
# 可追溯的已合入消费端与当前消费端；不再依赖已清理的交付目录。
API_PATHS = (ROOT / 'tests/fixtures/radar_monitor/radar_page_api_419dbc2.py',
             ROOT / 'radar_page_api.py')


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='b1b2-case-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'scripts').mkdir()
        shutil.copy2(ROOT/'scripts/radar_monitor_config.json', self.root/'scripts')
        self.current = b1.snapshot(6)
        self.collections = 0
        def collect(*args):
            self.collections += 1
            return copy.deepcopy(self.current)
        self.m = code.Monitor(self.root, collector=collect, sender=lambda events: [],
                              clue_writer=lambda e: {'id': 'offline'}, ai_filter=lambda t: True)
        self.m.run_once(b1.when(6))
        self.path = self.m.directory/'state.json'

    def runround(self, h, tweets):
        self.current = b1.snapshot(h, tweets)
        return self.m.run_once(b1.when(h))

    def state(self):
        return json.loads(self.path.read_text())

    def page(self):
        return json.loads((self.m.directory/'page-hourly.json').read_text())

    def record(self, name, **values):
        result = {'case': name, **values}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        evidence = os.environ.get('RADAR_B1B2_BOUNDARY_EVIDENCE')
        if evidence:
            with Path(evidence).open('a') as stream:
                stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False)+'\n')

    def consumers(self, now):
        page = self.page()
        outputs = []
        for path in API_PATHS:
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            spec = importlib.util.spec_from_file_location('consumer_boundary', path)
            api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(api)
            response = api.monitor_view(self.root, now=now)
            self.assertEqual(response['status'], 'ok')
            self.assertEqual(response['tweets'], page['tweets'])
            self.assertEqual(len(response['tweets']), len(page['tweets']))
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            outputs.append({'version': path.parent.name, 'status': response['status'],
                            'rows': len(response['tweets'])})
        return outputs

    def test_id_exact_ascii_length_and_rejection_counts(self):
        ids = ['7'*25, '7'*26, '١٢٣', '１２３', '123\n', '', 123, True, ' 123']
        self.runround(7, [b1.tweet(tid, views=1) for tid in ids])
        self.assertEqual(list(self.page()['tweets']), ['7'*25])
        log = (self.m.directory/'monitor.log').read_text()
        self.assertIn('拒绝数=8', log)
        for reason in ('长度不在1至25位', '含非 ASCII 数字字符', '类型非字符串'):
            self.assertIn(reason, log)
        self.assertEqual(set(self.page()), {'schema','run_id','generated_at','tweets'})
        self.record('ID矩阵', accepted=list(self.page()['tweets']), rejected=8,
                    consumers=self.consumers(b1.when(7)))

    def test_each_id_input_direct_to_both_consumers(self):
        cases = [('ascii25','7'*25,True), ('ascii26','7'*26,False),
                 ('arabic','١٢٣',False), ('fullwidth','１２３',False),
                 ('newline','123\n',False), ('empty','',False)]
        for label, tid, accepted in cases:
            with self.subTest(label=label):
                self.path.unlink()
                self.runround(7, [b1.tweet(tid,views=1)])
                self.assertEqual(len(self.page()['tweets']), int(accepted))
                self.record('单独ID_'+label, accepted=accepted,
                            consumers=self.consumers(b1.when(7)))

    def test_each_raw_metric_boundary_and_consumer_compatibility(self):
        values = [('max', 2**53-1), ('over', 2**53), ('huge', 10**400),
                  ('inf', float('inf')), ('string', '1e3'), ('bool', True),
                  ('negative', -1), ('nan', float('nan')), ('none', None)]
        # 5 raw metrics must all normalize; only views is written in page rows.
        for label, value in values:
            with self.subTest(value=label):
                self.current = b1.snapshot(7, [b1.tweet('1', **{k:value for k in
                    ('views','likes','retweets','replies','followers')})])
                self.path.unlink()
                self.assertEqual(self.m.run_once(b1.when(7))['status'], 'baseline')
                expected = 2**53-1 if label == 'max' else None
                sample = self.state()['samples']['1']
                for k in ('views','likes','retweets','replies','followers'):
                    self.assertEqual(sample[k], expected)
                self.assertEqual(self.page()['tweets']['1']['views'], expected)
                self.record('原始数值_'+label, views=expected,
                            consumers=self.consumers(b1.when(7)))

    def test_rate_overflow_only_nulls_rate_keeps_valid_increment_and_interval(self):
        self.path.unlink()
        self.runround(6, [b1.tweet(hour=6, views=0)])
        self.runround(7, [b1.tweet(views=2**53-1,
                       fetched_at=(b1.when(6)+timedelta(microseconds=1)).isoformat())])
        row = self.page()['tweets']['1']
        self.assertIsNone(row['hourly_delta'])
        self.assertEqual(row['views_delta'], 2**53-1)
        self.assertGreater(row['interval_hours'], 0)
        self.record('比率超界单字段降级', row=row, consumers=self.consumers(b1.when(7)))

    def test_negative_increment_remains_null(self):
        self.runround(7, [b1.tweet(views=6000)])
        self.runround(8, [b1.tweet(hour=8, views=5900)])
        row = self.page()['tweets']['1']
        for k in ('hourly_delta','views_delta','interval_hours'):
            self.assertIsNone(row[k])
        self.record('负增量', row=row, consumers=self.consumers(b1.when(8)))

    def test_zero_increment_is_zero_and_valid(self):
        self.runround(7, [b1.tweet(views=6000)])
        self.runround(8, [b1.tweet(hour=8, views=6000)])
        row = self.page()['tweets']['1']
        self.assertEqual((row['hourly_delta'],row['views_delta'],row['interval_hours']), (0,0,1))
        self.record('零增量', row=row, consumers=self.consumers(b1.when(8)))

    def corrupt_rebuild(self, name, change):
        self.runround(7, [b1.tweet(views=1)])
        state = self.state()
        change(state)
        # Intentional bad JSON constants are loaded as bad state, never re-exported.
        self.path.write_text(json.dumps(state))
        before = self.collections
        result = self.m.run_once(b1.when(7))
        self.assertEqual(result['status'], 'baseline')
        self.assertEqual(self.collections-before, 1)
        self.assertIs(type(self.page()['schema']), int)
        code.validate_page_hourly(self.page())
        self.assertEqual(self.page()['tweets']['1']['views'], 1)
        self.assertEqual(self.state()['events'], [])
        self.assertIn('状态损坏', (self.m.directory/'monitor.log').read_text())
        self.record('状态损坏_'+name, result=result['status'], resample=1,
                    consumers=self.consumers(b1.when(7)))

    def test_page_state_schema_exact_integer_one(self):
        for label, value in [('bool', True), ('float', 1.0), ('str', '1'), ('two', 2), ('null', None)]:
            with self.subTest(label=label):
                self.corrupt_rebuild('schema_'+label, lambda s: s['page_hourly'].update(schema=value))

    def test_page_state_containers_ids_required_fields_and_markers(self):
        changes = [
            ('page_array', lambda s:s.update(page_hourly=[])),
            ('page_null', lambda s:s.update(page_hourly=None)),
            ('tweets_array', lambda s:s['page_hourly'].update(tweets=[])),
            ('row_array', lambda s:s['page_hourly']['tweets'].update({'1':[]})),
            ('id_26', lambda s:s['page_hourly']['tweets'].update({'7'*26:s['page_hourly']['tweets'].pop('1')})),
            ('id_unicode', lambda s:s['page_hourly']['tweets'].update({'١٢٣':s['page_hourly']['tweets'].pop('1')})),
            ('id_newline', lambda s:s['page_hourly']['tweets'].update({'1\n':s['page_hourly']['tweets'].pop('1')})),
            ('missing_views', lambda s:s['page_hourly']['tweets']['1'].pop('views')),
            ('extra_field', lambda s:s['page_hourly'].update(extra=1)),
            ('run_id_number', lambda s:s['page_hourly'].update(run_id=1)),
            ('generated_at_number', lambda s:s['page_hourly'].update(generated_at=1)),
            ('fetched_at_naive', lambda s:s['page_hourly']['tweets']['1'].update(fetched_at='2026-10-15T07:00:00')),
            ('is_stale_number', lambda s:s['page_hourly']['tweets']['1'].update(is_stale=1)),
            ('pushed_number', lambda s:s['page_hourly']['tweets']['1'].update(pushed=1)),
            ('hit_array', lambda s:s['page_hourly']['tweets']['1'].update(hit=[])),
            ('hit_unknown', lambda s:s['page_hourly']['tweets']['1'].update(hit='unknown')),
        ]
        for label, change in changes:
            with self.subTest(label=label):self.corrupt_rebuild(label, change)

    def test_page_state_all_numeric_fields_strict_safe_range(self):
        for field in ('views','views_delta','hourly_delta','interval_hours'):
            for label, value in [('over',2**53),('huge',10**400),('inf',float('inf')),
                                 ('string','1e3'),('bool',True),('negative',-1)]:
                with self.subTest(field=field,value=label):
                    self.corrupt_rebuild(field+'_'+label,
                        lambda s:s['page_hourly']['tweets']['1'].update({field:value}))

    def test_legacy_state_without_page_field_is_not_damaged(self):
        self.runround(7, [b1.tweet(views=1)])
        state = self.state();state.pop('page_hourly');self.path.write_text(json.dumps(state))
        loaded, damaged = self.m.load()
        self.assertFalse(damaged)
        self.assertNotIn('page_hourly', loaded)
        before = self.collections
        self.assertEqual(self.m.run_once(b1.when(7))['status'], 'already_run')
        self.assertEqual(self.collections, before)
        self.runround(8, [b1.tweet(hour=8,views=2)])
        self.record('旧状态兼容', damaged=damaged, same_slot_resample=0,
                    consumers=self.consumers(b1.when(8)))

    def test_real_unchanged_snapshot_adapter_handles_enormous_integer(self):
        (self.root/'scripts/radar_targets.json').write_text('["test_user"]')
        directory = self.root/'workspace/twitter-radar/snapshots/2026-10-15'
        directory.mkdir(parents=True)
        row = b1.tweet(views=10**400);row['author']={'handle':'test_user'}
        raw = {'schema_version':1,'run_id':'huge-adapter','started_at':b1.when(7).isoformat(),
               'ended_at':b1.when(7).isoformat(),'attempted':1,'success':1,'failed':0,'api_calls':1,
               'accounts':[{'handle':'test_user','status':'success','followers':20000}],'tweets':[row]}
        from test_radar_monitor_a1_fixture import commit_fixture
        commit_fixture(self.root, raw)
        self.m.collector=lambda root,timeout,excluded,started:code.read_snapshot(root,excluded,started)
        result = self.m.run_once(b1.when(7))
        self.assertEqual(result['status'], 'success')
        self.assertIsNone(self.page()['tweets']['1']['views'])
        self.record('真实快照适配_极大整数', result=result['status'], views=None,
                    consumers=self.consumers(b1.when(7)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
