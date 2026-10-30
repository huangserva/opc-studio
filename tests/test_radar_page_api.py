"""B2-b radar_page_api 离线测试：合同反例 1/2/6/7/8/9 与后端校验口径。

不启动 8766、不调用 AIsa、不连飞书；只用临时目录与临时文件。
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('radar_page_api', ROOT / 'radar_page_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)


def iso(moment):
    return moment.isoformat()


class MonitorViewTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='b2b-api-', dir='/tmp')
        self.root = Path(self.dir)
        self.monitor = self.root / 'workspace/twitter-radar/monitor'
        self.monitor.mkdir(parents=True)
        self.page_file = self.monitor / 'page-hourly.json'

    def write(self, payload):
        self.page_file.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

    def base(self, **over):
        data = {'schema': 1, 'run_id': 'run-1', 'generated_at': iso(NOW - timedelta(minutes=10)),
                'tweets': {'2110000000000000001': {'hourly_delta': 1234.5, 'views_delta': 2000,
                                                  'interval_hours': 1.62, 'views': 52000,
                                                  'fetched_at': iso(NOW), 'is_stale': False,
                                                  'hit': 'lowfan_burst', 'pushed': True}}}
        data.update(over)
        return data

    def test_07_missing_file(self):
        self.assertFalse(self.page_file.exists())
        view = api.monitor_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'missing')
        self.assertEqual(view['tweets'], {})

    def test_07_corrupt_file(self):
        self.page_file.write_text('{ not json', encoding='utf-8')
        self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], 'invalid')

    def test_07_wrong_schema(self):
        self.write(self.base(schema=2))
        self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], 'invalid')

    def test_07_stale(self):
        self.write(self.base(generated_at=iso(NOW - timedelta(hours=3))))
        view = api.monitor_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'stale')
        self.assertIn('2110000000000000001', view['tweets'], '过期也要带上数据，页面才知道不是缺失')

    def test_07_stale_boundary_150_minutes(self):
        """2 小时间隔 + 最长 10 分钟采集 + 余量：150 分钟内 ok，超过即 stale。"""
        for age, expect in ((timedelta(hours=2, minutes=10), 'ok'),
                            (timedelta(minutes=150), 'ok'),
                            (timedelta(minutes=150, seconds=1), 'stale'),
                            (timedelta(hours=2, minutes=31), 'stale')):
            with self.subTest(age=str(age)):
                self.write(self.base(generated_at=iso(NOW - age)))
                self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], expect)

    def test_07_ok(self):
        self.write(self.base())
        view = api.monitor_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'ok')
        self.assertEqual(view['tweets']['2110000000000000001']['hit'], 'lowfan_burst')
        self.assertEqual(view['tweets']['2110000000000000001']['views_delta'], 2000)

    def test_08_good_numeric_values(self):
        tweets = {'1': {'hourly_delta': None}, '2': {'hourly_delta': 0},
                  '3': {'hourly_delta': -5}, '4': {'hourly_delta': 1e12},
                  '5': {'hourly_delta': '6000'}, '6': {}}
        self.write(self.base(tweets=tweets))
        rows = api.monitor_view(self.root, now=NOW)['tweets']
        self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], 'ok')
        self.assertIsNone(rows['1']['hourly_delta'])
        self.assertEqual(rows['2']['hourly_delta'], 0)
        self.assertIsNone(rows['3']['hourly_delta'], '负数归一成未知')
        self.assertEqual(rows['4']['hourly_delta'], 1e12)
        self.assertEqual(rows['5']['hourly_delta'], 6000, '数字字符串要能解析')
        self.assertIsNone(rows['6']['hourly_delta'])

    def test_08_bad_numeric_makes_whole_package_invalid(self):
        # B2-b2/M5：数值不可信时整包判 invalid，不再逐字段悄悄归 None
        for field, value in [('hourly_delta', 'abc'), ('hourly_delta', True),
                             ('hourly_delta', 'Infinity'), ('hourly_delta', float('inf')),
                             ('hourly_delta', float('nan')), ('views_delta', 'nan'),
                             ('views', 10 ** 20), ('interval_hours', '1e400'),
                             ('hourly_delta', [1]), ('hourly_delta', {'a': 1})]:
            with self.subTest(field=field, value=repr(value)):
                self.write(self.base(tweets={'1': {field: value}}))
                view = api.monitor_view(self.root, now=NOW)
                self.assertEqual(view['status'], 'invalid')
                self.assertEqual(view['tweets'], {})

    def test_non_dict_row_is_invalid(self):
        self.write(self.base(tweets={'1': ['not', 'an', 'object']}))
        self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], 'invalid')

    def test_09_unknown_hit(self):
        self.write(self.base(tweets={'1': {'hourly_delta': 5, 'hit': 'brand_new_kind'}}))
        row = api.monitor_view(self.root, now=NOW)['tweets']['1']
        self.assertIsNone(row['hit'], '未知 hit 必须归一成 None')

    def test_rows_with_bad_tweet_id_dropped(self):
        self.write(self.base(tweets={'abc': {'hourly_delta': 1}, '12a': {'hourly_delta': 1},
                                    '1': {'hourly_delta': 1}}))
        rows = api.monitor_view(self.root, now=NOW)['tweets']
        self.assertEqual(list(rows), ['1'])

    def test_feedback_is_returned(self):
        api.save_feedback(self.root, {'tweet_id': '1', 'verdict': 'worth'}, now=NOW)
        self.write(self.base())
        view = api.monitor_view(self.root, now=NOW)
        self.assertEqual(view['feedback']['1']['verdict'], 'worth')

    def test_corrupt_feedback_does_not_crash(self):
        (self.monitor / 'feedback.json').write_text('nope', encoding='utf-8')
        self.write(self.base())
        self.assertEqual(api.monitor_view(self.root, now=NOW)['feedback'], {})


class SaveFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='b2b-fb-', dir='/tmp')
        self.root = Path(self.dir)
        (self.root / 'workspace/twitter-radar/monitor').mkdir(parents=True)
        self.file = self.root / 'workspace/twitter-radar/monitor/feedback.json'

    def stored(self):
        return json.loads(self.file.read_text(encoding='utf-8'))

    def test_accepts_valid(self):
        api.save_feedback(self.root, {'tweet_id': '2110000000000000001', 'verdict': 'worth'}, now=NOW)
        self.assertEqual(self.stored()['2110000000000000001']['verdict'], 'worth')

    def test_02_rejects_invalid_payloads(self):
        bad = [
            {'tweet_id': '12a', 'verdict': 'worth'},
            {'tweet_id': '../x', 'verdict': 'worth'},
            {'tweet_id': 12345, 'verdict': 'worth'},
            {'tweet_id': '1' * 30, 'verdict': 'worth'},
            {'tweet_id': '', 'verdict': 'worth'},
            {'tweet_id': '1'},
            {'tweet_id': '1', 'verdict': 'WORTH'},
            {'tweet_id': '1', 'verdict': 'yes'},
            {'tweet_id': '1', 'verdict': ['worth']},
            {'tweet_id': '1', 'verdict': 'worth', 'extra': 1},
            {'tweet_id': '1', 'verdict': 'worth', 'note': 'x'},
            [],
            'string',
            None,
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    api.save_feedback(self.root, payload)
        self.assertFalse(self.file.exists(), '任何非法 payload 都不能写出文件')

    def test_verdict_null_removes(self):
        api.save_feedback(self.root, {'tweet_id': '1', 'verdict': 'worth'}, now=NOW)
        api.save_feedback(self.root, {'tweet_id': '1', 'verdict': 'not_worth'}, now=NOW)
        self.assertEqual(self.stored()['1']['verdict'], 'not_worth')
        api.save_feedback(self.root, {'tweet_id': '1', 'verdict': None}, now=NOW)
        self.assertNotIn('1', self.stored())

    def test_snapshot_recorded(self):
        api.save_feedback(self.root, {'tweet_id': '1', 'verdict': 'worth'}, now=NOW,
                          snapshot={'hit': 'new_fast', 'pushed': True})
        record = self.stored()['1']
        self.assertEqual(record['hit'], 'new_fast')
        self.assertTrue(record['pushed'])
        self.assertEqual(record['updated_at'], iso(NOW))

    def test_06_concurrent_writers(self):
        errors = []

        def write(tweet_id):
            try:
                for _ in range(5):
                    api.save_feedback(self.root, {'tweet_id': tweet_id, 'verdict': 'worth'}, now=NOW)
            except Exception as exc:      # pragma: no cover - 只在出错时触发
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(str(number),)) for number in range(1, 9)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        stored = self.stored()
        self.assertEqual(sorted(stored), [str(n) for n in range(1, 9)])
        self.assertTrue(all(row['verdict'] == 'worth' for row in stored.values()))

    def test_no_temp_files_left(self):
        api.save_feedback(self.root, {'tweet_id': '1', 'verdict': 'worth'}, now=NOW)
        leftovers = [p.name for p in (self.root / 'workspace/twitter-radar/monitor').iterdir()
                     if '.tmp-' in p.name]
        self.assertEqual(leftovers, [], '原子写不应留下临时文件')

    def test_accepts_workspace_path_as_root(self):
        api.save_feedback(self.root / 'workspace', {'tweet_id': '1', 'verdict': 'worth'}, now=NOW)
        self.assertEqual(self.stored()['1']['verdict'], 'worth')


class M1FeedbackCorruptTests(unittest.TestCase):
    """B2-b2/M1：反馈文件损坏必须降级读取并拒绝写入，原文件一字节不动。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='b2b2-m1-', dir='/tmp'))
        self.monitor = self.root / 'workspace/twitter-radar/monitor'
        self.monitor.mkdir(parents=True)
        self.feedback = self.monitor / 'feedback.json'
        self.hourly = self.monitor / 'page-hourly.json'
        self.hourly.write_text(json.dumps(
            {'schema': 1, 'run_id': 'r', 'generated_at': iso(NOW - timedelta(minutes=5)),
             'tweets': {'2110000000000000001': {'hourly_delta': 10, 'views': 100}}}), encoding='utf-8')

    def test_missing_status(self):
        view = api.monitor_view(self.root, now=NOW)
        self.assertEqual(view['feedback_status'], 'missing')
        self.assertEqual(view['feedback'], {})

    def test_corrupt_degrades_get(self):
        for raw in ['{"1": ', '[]', '"text"', 'null', 'not json']:
            with self.subTest(raw=raw):
                self.feedback.write_text(raw, encoding='utf-8')
                view = api.monitor_view(self.root, now=NOW)
                self.assertEqual(view['status'], 'ok', '监控包本身好，仍要正常返回')
                self.assertEqual(view['feedback_status'], 'invalid')
                self.assertEqual(view['feedback'], {})

    def test_corrupt_blocks_write_and_keeps_bytes(self):
        raw = '{"2110000000000000001": {"verdict": "wor'
        self.feedback.write_text(raw, encoding='utf-8')
        with self.assertRaises(api.FeedbackCorrupt):
            api.save_feedback(self.root, {'tweet_id': '2110000000000000001', 'verdict': 'worth'}, now=NOW)
        self.assertEqual(self.feedback.read_text(encoding='utf-8'), raw, '损坏文件一字节都不能动')
        self.assertEqual([p.name for p in self.monitor.iterdir() if '.tmp-' in p.name], [])

    def test_empty_object_is_writable(self):
        self.feedback.write_text('{}', encoding='utf-8')
        api.save_feedback(self.root, {'tweet_id': '2110000000000000001', 'verdict': 'worth'}, now=NOW)
        self.assertEqual(json.loads(self.feedback.read_text(encoding='utf-8'))['2110000000000000001']['verdict'], 'worth')

    def test_invalid_entries_block_write_without_mutation(self):
        # B2-b3/M1: malformed entries block the whole write; do not silently
        # discard the damaged row.
        for damaged in ['not-a-record', [], None, [[{'broken': []}]]]:
            with self.subTest(damaged=damaged):
                self.feedback.write_text(json.dumps({'1': damaged,
                                                     '2': {'verdict': 'worth'}}), encoding='utf-8')
                before = self.feedback.read_bytes()
                inode = self.feedback.stat().st_ino
                view = api.monitor_view(self.root, now=NOW)
                self.assertEqual(view['feedback_status'], 'invalid')
                self.assertEqual(view['feedback'], {})
                with self.assertRaises(api.FeedbackCorrupt):
                    api.save_feedback(self.root, {'tweet_id': '3', 'verdict': 'worth'}, now=NOW)
                self.assertEqual(self.feedback.read_bytes(), before)
                self.assertEqual(self.feedback.stat().st_ino, inode)
                self.assertEqual(list(self.monitor.glob('feedback.json.*')), [])



class M5ShapeTests(unittest.TestCase):
    """B2-b2/M5：schema 必须是整数 1，未来时间与不可解析时间一律 invalid，接口永远返回 JSON。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='b2b2-m5-', dir='/tmp'))
        self.hourly = self.root / 'workspace/twitter-radar/monitor/page-hourly.json'
        self.hourly.parent.mkdir(parents=True)

    def write(self, payload):
        self.hourly.write_text(json.dumps(payload), encoding='utf-8')

    def base(self, **over):
        data = {'schema': 1, 'run_id': 'r', 'generated_at': iso(NOW - timedelta(minutes=5)),
                'tweets': {'2110000000000000001': {'hourly_delta': 10, 'views': 100}}}
        data.update(over)
        return data

    def test_schema_bool_is_invalid(self):
        self.write(self.base(schema=True))
        self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], 'invalid')

    def test_schema_string_is_invalid(self):
        self.write(self.base(schema='1'))
        self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], 'invalid')

    def test_future_beyond_tolerance_is_invalid(self):
        self.write(self.base(generated_at=iso(NOW + timedelta(minutes=6))))
        self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], 'invalid')

    def test_small_skew_is_ok(self):
        self.write(self.base(generated_at=iso(NOW + timedelta(minutes=1))))
        self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], 'ok')

    def test_naive_timestamp_is_invalid(self):
        self.write(self.base(generated_at='2026-10-15T12:00:00'))
        self.assertEqual(api.monitor_view(self.root, now=NOW)['status'], 'invalid')

    def test_unreadable_bytes_do_not_raise(self):
        self.hourly.write_bytes(b'\xff\xfe\x00broken')
        view = api.monitor_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'invalid')
        self.assertEqual(view['tweets'], {})


class L1IdTests(unittest.TestCase):
    """B2-b2/L1：tweet_id 只收完整匹配的 ASCII 数字串，不做修剪。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='b2b2-l1-', dir='/tmp'))
        (self.root / 'workspace/twitter-radar/monitor').mkdir(parents=True)

    def test_rejects_non_ascii_and_padded(self):
        for bad in ['１２３', '١٢٣', '123\n', ' 123', '123 ', '1' * 26, '12a', '+1', '-1', '1e3', 123, True]:
            with self.subTest(value=repr(bad)):
                with self.assertRaises(ValueError):
                    api.save_feedback(self.root, {'tweet_id': bad, 'verdict': 'worth'}, now=NOW)

    def test_accepts_leading_zeros_and_25_digits(self):
        for good in ['0', '00123', '1' * 25]:
            with self.subTest(value=good):
                api.save_feedback(self.root, {'tweet_id': good, 'verdict': 'worth'}, now=NOW)

    def test_bad_id_rows_are_dropped_not_invalidated(self):
        path = self.root / 'workspace/twitter-radar/monitor/page-hourly.json'
        path.write_text(json.dumps({'schema': 1, 'run_id': 'r', 'generated_at': iso(NOW),
                                    'tweets': {'１': {'hourly_delta': 5}, '12a': {'hourly_delta': 5},
                                               '7': {'hourly_delta': 5}}}), encoding='utf-8')
        view = api.monitor_view(self.root, now=NOW)
        self.assertEqual(view['status'], 'ok')
        self.assertEqual(list(view['tweets']), ['7'])


if __name__ == '__main__':
    unittest.main(verbosity=2)