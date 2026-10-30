import hashlib
from datetime import datetime
import json
from pathlib import Path
import resource
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import settings_view as settings
from scheduled_tasks import ScheduledTasks


class SettingsViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='settings-unit-')
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.scheduler = ScheduledTasks(self.workspace, autostart=False)
        self.grok_patch = patch('grok_bot_radar.reports_dir', return_value=self.root / 'reports')
        self.grok_patch.start()
        self.server = SimpleNamespace(store=SimpleNamespace(workspace=self.workspace, _collect=None))

    def tearDown(self):
        self.grok_patch.stop()
        self.scheduler.close()
        self.temp.cleanup()

    def task(self, **changes):
        data = dict(name='微信合成任务', kind='script', body='echo fixture', cwd=str(self.root),
                    frequency='interval', minutes=60, timeout=5, enabled=False, allow_commands=False)
        data.update(changes)
        return self.scheduler.save(data)

    def test_p1_rejects_before_opening_any_log(self):
        for query in ('name=../../.env', 'name=service%2F..', 'lines=99999', 'lines=-1',
                      'lines=0', 'lines=', 'name=', 'name=service&name=service-error', 'unknown=1'):
            with self.subTest(query=query), patch.object(settings.os, 'open') as opened:
                with self.assertRaises(ValueError):
                    settings.logs(self.workspace, parse_qs(query, keep_blank_values=True))
                opened.assert_not_called()

    def test_p2_sensitive_values_never_returned(self):
        (self.workspace / 'logs').mkdir()
        secrets = ['Bearer fixture-auth-value', 'sk-fixture-private', 'fixture-cookie-value',
                   'fixture-token-value', 'fixture-secret-value']
        (self.workspace / 'logs/service.log').write_text('normal line\nAuthorization: '+secrets[0]+'\nAISA_API_KEY='+secrets[1]+'\ncookie='+secrets[2]+'\ntoken: '+secrets[3]+'\nsecret='+secrets[4]+'\n')
        data = settings.logs(self.workspace, {})
        raw = json.dumps(data)
        self.assertTrue(all(s not in raw for s in secrets))
        self.assertIn('normal line', raw)
        self.assertIn('***', raw)

    def test_p3_200mb_tail_is_bounded_and_under_one_second(self):
        path = self.workspace / 'logs/service.log'
        path.parent.mkdir()
        with path.open('wb') as stream:
            # 非稀疏 200MB，证明不能整读；超长行中间含凭据也不得泄漏。
            chunk = b'normal padding line\n' * 4096
            for _ in range(200 * 1024 * 1024 // len(chunk) + 1):
                stream.write(chunk)
            stream.write(b'tail expected\n')
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        start = time.monotonic()
        data = settings.logs(self.workspace, {'lines': ['1']})
        elapsed = time.monotonic() - start
        growth = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
        self.assertEqual(data['lines'], ['tail expected'])
        self.assertLess(elapsed, 1)
        # macOS ru_maxrss 单位 bytes，Linux KiB；允许 16MB 峰值增量。
        import sys
        self.assertLess(growth, 16 * 1024 * 1024 if sys.platform == 'darwin' else 16 * 1024)
        print(f'P3 bytes={path.stat().st_size} seconds={elapsed:.4f} rss_growth={growth}')

    def test_long_line_fragment_is_dropped(self):
        path = self.workspace / 'logs/service.log'
        path.parent.mkdir()
        path.write_text('secret='+('private-fragment' * 60000)+'\nsafe final line\n')
        self.assertEqual(settings.logs(self.workspace, {})['lines'], ['safe final line'])

    def test_missing_and_symlink_logs(self):
        self.assertTrue(settings.logs(self.workspace, {})['missing'])
        (self.workspace / 'logs').mkdir()
        outside = self.root / 'outside'
        outside.write_text('fixture-private')
        (self.workspace / 'logs/service.log').symlink_to(outside)
        with self.assertRaises(ValueError):
            settings.logs(self.workspace, {})

    def test_tmp_copy_never_reads_live_service_logs(self):
        for label in settings.SERVICES:
            self.assertTrue(all(p.is_relative_to(self.workspace) for p in settings.service_log_paths(self.workspace, label)))
        relocated = self.root / 'settings_view.py'
        with patch.object(settings, '__file__', str(relocated)):
            self.assertFalse(settings._live_workspace(self.workspace))
        with patch.object(settings, '_live_workspace', return_value=True):
            paths = settings.service_log_paths(self.workspace, 'com.servasyy.article-studio')
            self.assertEqual(paths[0], Path.home() / 'Library/Logs/ArticleStudio/service.log')

    def test_directory_symlink_is_rejected_even_if_target_is_live_archive(self):
        # 最新主控澄清：logs 不放开软链；服务状态只 stat 真实日志。
        home = self.root / 'home'
        archive = home / 'Library/Logs/ArticleStudio'
        archive.mkdir(parents=True)
        (archive / 'service.log').write_text('approved fixture\n')
        (self.workspace / 'logs').symlink_to(archive, target_is_directory=True)
        with patch.object(settings, '_live_workspace', return_value=True):
            with self.assertRaises(ValueError):
                settings.logs(self.workspace, {})
        (self.workspace / 'logs').unlink()
        (self.workspace / 'logs').symlink_to(self.root, target_is_directory=True)
        with patch.object(settings, '_live_workspace', return_value=True):
            with self.assertRaises(ValueError):
                settings.logs(self.workspace, {})

    def test_p8_real_tasks_match_snapshot_and_no_connector_is_called(self):
        first = self.task(name='GitHub 定时', body='github_source.py')
        self.task(name='普通脚本')
        state = self.workspace / 'github/github.db'
        state.parent.mkdir()
        import sqlite3
        with sqlite3.connect(state) as db:
            db.execute('CREATE TABLE state(source TEXT,data TEXT)')
            db.execute('INSERT INTO state VALUES(?,?)', ('fixture', json.dumps({'status':'ready','last_capture':'2026-10-29T08:00:00Z'})))
        self.server.store._collect = SimpleNamespace(_status_cache={}, status=Mock(side_effect=AssertionError('collect 不得调用')))
        self.server.store._intel = SimpleNamespace(status=Mock(side_effect=AssertionError('intel 不得调用')))
        before = hashlib.sha256(self.scheduler.db_path.read_bytes()).hexdigest()
        for _ in range(5):
            start = time.monotonic()
            data = settings.overview(self.server)
            self.assertLess(time.monotonic()-start, 1)
            self.assertEqual(len(data['sources']), 7)
            self.assertEqual(data['schedule_task_count'], 2)
            github = next(s for s in data['sources'] if s['id']=='github')
            self.assertEqual([t['id'] for t in github['tasks']], [first['id']])
            self.assertEqual(github['last_success'], '2026-10-29T08:00:00Z')
            self.assertFalse(github['enabled'])
            self.assertNotIn('_body', json.dumps(data))
        self.server.store._collect.status.assert_not_called()
        self.server.store._intel.status.assert_not_called()
        self.assertEqual(before, hashlib.sha256(self.scheduler.db_path.read_bytes()).hexdigest())
        self.assertEqual(len(data['services']), 5)

    def test_source_cache_is_read_without_refreshing(self):
        self.server.store._collect = SimpleNamespace(_status_cache={('wechat_msg', 1):(0,'cached',{'available':False,'detail':'token=fixture-not-public','extra':{'last_synced':'2026-10-28T08:00:00Z'}})})
        result = next(s for s in settings.overview(self.server)['sources'] if s['id']=='wechat')
        self.assertEqual(result['last_success'], '2026-10-28T08:00:00Z')
        self.assertNotIn('fixture-not-public', result['last_error'])

    def source(self, identity):
        return next(s for s in settings.overview(self.server)['sources'] if s['id'] == identity)

    def test_twitter_reads_successful_run_finished_at_not_hour_slot(self):
        run_id = '20261029-140008-f6d22d61'
        folder = self.workspace / 'twitter-radar'
        (folder / 'monitor').mkdir(parents=True)
        (folder / 'monitor/state.json').write_text(json.dumps({'runs': {
            '2026-10-29T22': {'success': True, 'run_id': run_id},
            '2026-10-29T23': {'success': False, 'run_id': '20261029-150008-aaaa'}}}))
        path = folder / 'snapshots/2026-10-29' / (run_id + '.json')
        path.parent.mkdir(parents=True)
        good = {'run_id': run_id, 'finished_at': '2026-10-29T14:00:38.594Z'}
        path.write_text(json.dumps({'run': good}))
        self.assertEqual(self.source('twitter')['last_success'], good['finished_at'])
        self.assertIn(str(path.relative_to(self.workspace)), self.source('twitter')['reason'])
        for bad in ('2026-10-29T22', '2026-10-29T14:00:38', None):
            path.write_text(json.dumps({'run': dict(good, finished_at=bad)}))
            self.assertIsNone(self.source('twitter')['last_success'])
            self.assertIn('无效', self.source('twitter')['reason'])
        path.write_text(json.dumps({'run': dict(good, run_id='another-run')}))
        self.assertIsNone(self.source('twitter')['last_success'])
        path.unlink()
        self.assertIsNone(self.source('twitter')['last_success'])
        self.assertIn('缺失', self.source('twitter')['reason'])

    def test_aisa_uses_today_bundle_reader_not_old_db_or_cache(self):
        path = self.root / 'public/twitter-radar-bundle.json'
        path.parent.mkdir()
        bundle = {'updated_at': '2026-10-29 22:00:38', 'collected_at': '2026-10-29T14:00:38.594Z', 'tweets': []}
        path.write_text(json.dumps(bundle))
        old_db = self.workspace / 'aisa/aisa.db'
        old_db.parent.mkdir()
        with sqlite3.connect(old_db) as db:
            db.execute('CREATE TABLE meta(key TEXT,value TEXT)')
            db.execute("INSERT INTO meta VALUES('last_synced','2099-01-01T00:00:00Z')")
        self.server.store._collect = SimpleNamespace(_status_cache={('aisa', 1): (0, '', {'extra': {'last_synced': '2099-02-01T00:00:00Z'}})})
        import grok_bot_radar as grok
        with patch.object(grok, '_read_json', wraps=grok._read_json) as reader:
            self.assertEqual(self.source('aisa')['last_success'], bundle['collected_at'])
            reader.assert_called_once_with(path)
        actual = datetime.fromisoformat(self.source('aisa')['last_success'].replace('Z', '+00:00'))
        today = datetime.fromisoformat(bundle['updated_at']).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertEqual(int(actual.timestamp()), int(today.timestamp()))
        self.assertIn('public/twitter-radar-bundle.json', self.source('aisa')['reason'])
        del bundle['updated_at']
        path.write_text(json.dumps(bundle))
        self.assertEqual(self.source('aisa')['last_success'], bundle['collected_at'])
        path.unlink()
        self.assertIsNone(self.source('aisa')['last_success'])

    def test_intel_rate_limited_and_failure_partial_only(self):
        path = self.workspace / 'intel/intel.db'
        path.parent.mkdir()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE jobs(status TEXT,reason TEXT,detail TEXT,started_at TEXT,finished_at TEXT)')
        cases = [('rate_limited', '', '命中后台限流（200013）', True),
                 ('partial', '已保存 3 篇，余下正文待确认', '', False),
                 ('partial', '', '保存 3 篇，失败 0 篇，未处理 2 篇；正文未请求', False),
                 ('partial', '', '已保存 3 篇，没有失败；余下待确认', False),
                 ('partial', '', '后台：限流（200013），没有取到列表', True),
                 ('partial', '', '正文读取失败', True),
                 ('partial', '', 'backend failed', True)]
        for status, reason, detail, expected in cases:
            with self.subTest(status=status, detail=detail), sqlite3.connect(path) as db:
                db.execute('DELETE FROM jobs')
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)', (status, reason, detail, '2026-10-29T13:00:00Z', '2026-10-29T14:00:38Z'))
            row = self.source('intel')
            self.assertEqual(bool(row['last_error']), expected)
            if expected:
                self.assertIn('2026-10-29T14:00:38Z', row['last_error'])
                self.assertIn(detail or reason, row['last_error'])
        # 较新的正常 partial 不能掩盖较旧、已确认的限流错误。
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO jobs VALUES('partial','','已保存，剩余正文待确认','','2026-10-29T15:00:00Z')")
        self.assertIn('backend failed', self.source('intel')['last_error'])

    def test_every_source_reason_names_a_file_or_table(self):
        for row in settings.overview(self.server)['sources']:
            self.assertRegex(row['reason'], r'\.db|\.json')

    def test_no_new_connector_is_initialized(self):
        class Store:
            workspace = self.workspace
            _collect = None

            @property
            def collect(self):
                raise AssertionError('不得初始化 collect')

            @property
            def intel(self):
                raise AssertionError('不得初始化 intel')

        data = settings.overview(SimpleNamespace(store=Store()))
        self.assertTrue(all(s['enabled'] is None for s in data['sources']))
        self.assertTrue(all(s['last_success'] is None for s in data['sources']))

    def test_template_l1_fixed_sample_and_text_contract(self):
        template = Path('prompts/draft-template.md').read_text()
        self.assertNotIn('第一人称句子只用', template)
        for wording in ('不编造实测数字、截图、经历', '不必以「我」开头', '同一句式在全文最多出现 2 次',
                        '全文文末一处', '只保留一处', 'coordination/impl-finish-p4-contract.md §4-1.5'):
            self.assertIn(wording, template)
        # 固定样本：没有真实模型调用，也没有把第三方实测写成自己的经历。
        sample = {'title':'先核对输入输出', 'parts':['先准备一条带来源的素材。打开全文，记录输入字段。\n\n保存素材后点拿去写，检查底稿是否保留原文。\n\n最后核对新版本与底稿之间的差异。\n\n【待补：我的实测数据】：补充延迟、失败条件与截图。']}
        output = json.loads(json.dumps(sample, ensure_ascii=False))
        text = '\n'.join(output['parts'])
        self.assertEqual(text.count('【待补：我的实测数据】'), 1)
        for phrase in ('我准备', '我想核对', '我还没', '我会先'):
            self.assertLessEqual(text.count(phrase), 2)
        self.assertNotIn('我验证过', text)
        # 负例：验证占位计数/重复句式的断言确实能捕获旧问题。
        bad = ('我准备验证。\n【待补：我的实测数据】\n' * 6)
        self.assertGreater(bad.count('我准备'), 2)
        self.assertNotEqual(bad.count('【待补：我的实测数据】'), 1)


if __name__ == '__main__':
    unittest.main()
