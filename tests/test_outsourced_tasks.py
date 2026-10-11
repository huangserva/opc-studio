"""外包定时任务：WorkBuddy 优先交付，本机 Pi 兜底。

交接文件约定见 `outsourced_tasks.py` 顶部的 SCHEMA 说明。核心不变式：

* 判定一律看「文件/模块是否存在或内容是否合法」，不猜；
* 有合法文件时**绝不**触发兜底（那是黄总的免费额度）；
* 兜底产物与 WorkBuddy 产物**同一种格式**，只是 producer 换成 local-pi；
* 同一文件重复收取不重复导入，url 去重。

全程假 pi 执行器、临时 outbox：不联网、不调真实模型、不读密钥。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import outsourced_tasks as ot
from clues_store import Clues


def payload(slug='github-trending-ai', date='2026-10-10', **over):
    data = {'schema': 'article-studio.outsourced.v1', 'task': slug, 'date': date,
            'producer': 'workbuddy', 'started_at': '2026-10-10T09:00:00+08:00',
            'finished_at': '2026-10-10T09:20:00+08:00', 'status': 'ok', 'error': '',
            'items': [{'title': 'A 开源推理框架', 'url': 'https://github.com/a/framework',
                       'summary': '一个轻量推理框架', 'tags': ['python']}]}
    data.update(over)
    return data


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outbox = Path(self.tmp.name)
        self.date = '2026-10-10'
        # 把交接根目录钉在临时目录：验收要求「不在真实 outbox 里写文件」。
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def write(self, data, slug='github-trending-ai', date=None, name=None):
        date = date or self.date
        folder = self.outbox / slug
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name or f'{date}.json')
        path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
        return path

    def ok(self, **over):
        return ot.validate(self.write(payload(**over)), 'github-trending-ai', self.date)

    # ── 正常 ──
    def test_valid_file_is_accepted_and_normalised(self):
        data = self.ok()
        self.assertEqual(data['task'], 'github-trending-ai')
        self.assertEqual(data['date'], self.date)
        self.assertEqual(data['producer'], 'workbuddy')
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['warnings'], [])

    def test_status_empty_and_failed_are_all_accepted(self):
        # fixes-outsourced-tasks-2 / M1 改动（唯一一条与新规则冲突的既有断言）：
        # 原断言用默认 payload（带 1 条 item）去验 status=empty，现在 empty+非空 items
        # 被判为状态矛盾并拒收（见 PromptDateTests 之外新增的
        # FailedAndEmptyTests.test_empty_status_with_items_is_rejected）。
        # 这里把 empty 的样本改成 items=[]，其余 ok/failed 的覆盖不变。
        for status in ('ok', 'failed'):
            data = self.ok(status=status)
            self.assertEqual(data['status'], status)
        self.assertEqual(self.ok(status='empty', items=[])['status'], 'empty')
        self.assertEqual(self.ok(status='failed', error='抓取失败')['error'], '抓取失败')

    # ── 拒收：每种都要有明确原因 ──
    def reject(self, data, slug='github-trending-ai', date=None, name=None):
        path = self.write(data, slug, date, name)
        with self.assertRaises(ValueError) as caught:
            ot.validate(path, slug, date or self.date)
        return str(caught.exception)

    def test_rejects_bad_schema(self):
        self.assertIn('schema', self.reject(payload(schema='article-studio.outsourced.v2')))

    def test_rejects_task_or_date_mismatch(self):
        self.assertIn('task', self.reject(payload(task='other-task')))
        self.assertIn('date', self.reject(payload(date='2026-10-11')))

    def test_rejects_broken_json(self):
        self.assertTrue(self.reject('{"schema": "article-studio.outsourced.v1",'))

    def test_rejects_too_many_items(self):
        many = payload(items=[{'title': f'T{i}', 'url': f'https://e.com/{i}',
                               'summary': 's', 'tags': []} for i in range(51)])
        self.assertIn('条', self.reject(many))

    def test_rejects_oversized_file(self):
        # 真造一个超过 1 MiB 的文件（而不是靠 items 条数触发，那是另一条拒收理由）
        folder = self.outbox / 'github-trending-ai'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{self.date}.json'
        blob = json.dumps(payload())
        path.write_text(blob[:-1] + (' ' * (1024 * 1024 + 10)) + '}')
        with self.assertRaises(ValueError) as caught:
            ot.validate(path, 'github-trending-ai', self.date)
        self.assertIn('过大', str(caught.exception))

    def test_rejects_illegal_slug_and_path_traversal(self):
        self.assertIn('slug', self.reject(payload(), slug='../escape'))
        self.assertIn('slug', self.reject(payload(), slug='Bad_Slug'))
        self.assertIn('slug', self.reject(payload(), slug='a' * 41))
        # 文件真的在 outbox 之外
        outside = Path(tempfile.mkdtemp()) / 'evil.json'
        outside.write_text(json.dumps(payload()))
        with self.assertRaises(ValueError) as caught:
            ot.validate(outside, 'github-trending-ai', self.date)
        self.assertIn('slug', str(caught.exception))

    def test_rejects_item_with_non_http_url(self):
        self.assertIn('url', self.reject(payload(items=[
            {'title': '本地文件', 'url': 'file:///etc/passwd', 'summary': 's', 'tags': []}])))

    def test_rejects_item_with_bad_field_types(self):
        self.assertIn('title', self.reject(payload(items=[{'title': '', 'url': 'https://e.com/a', 'summary': 's', 'tags': []}])))
        self.assertIn('tags', self.reject(payload(items=[{'title': 't', 'url': 'https://e.com/a', 'summary': 's', 'tags': 'python'}])))

    def test_truncates_overlong_fields_with_a_warning_instead_of_rejecting(self):
        data = self.ok(items=[{'title': 'T' * 300, 'url': 'https://e.com/a',
                               'summary': 'S' * 1500, 'tags': []}])
        self.assertEqual(len(data['items'][0]['title']), 200)
        self.assertEqual(len(data['items'][0]['summary']), 1000)
        self.assertTrue(data['warnings'], '截断必须留一条警告，不能静默')

    def test_missing_file_is_reported_as_missing(self):
        with self.assertRaises(FileNotFoundError):
            ot.validate(self.outbox / 'github-trending-ai' / '1999-01-01.json',
                        'github-trending-ai', '1999-01-01')


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.task = {'slug': 'github-trending-ai', 'name': 'GitHub Trending AI 日报',
                     'body': '抓取 GitHub Trending 里 AI 相关的仓库，挑 5 个值得看的。'}

    def prompt(self, producer='workbuddy', outbox=None):
        return ot.prompt_for(self.task, producer=producer,
                             outbox=outbox or Path(self.tmp.name) / 'outbox')

    def test_prompt_states_the_absolute_output_path(self):
        # H3 改动（唯一一条与 H3 冲突的既有断言）：原来这里断言 workbuddy 提示词里
        # 出现写死的 `2026-10-10.json`。那与「复制一次、之后天天自动跑」的用法冲突
        # —— 日期写死就等于每天覆盖同一个文件名，工作台按当天日期找不到文件。
        # 改为断言绝对目录 + 日期占位符。
        # 顺带修掉一个时间炸弹：原断言硬编码 `2026-10-10`，只有恰好当天跑才通过。
        outbox = Path(self.tmp.name) / 'outbox'
        text = self.prompt(outbox=outbox)
        self.assertIn('github-trending-ai', text)
        self.assertIn(str(outbox.resolve() / 'github-trending-ai'), text)
        self.assertIn('YYYY-MM-DD.json', text)

    def test_prompt_documents_every_field_and_the_tmp_rename(self):
        text = self.prompt()
        for token in ('schema', 'article-studio.outsourced.v1', 'producer', 'started_at',
                      'finished_at', 'status', 'error', 'items', 'title', 'url', 'summary', 'tags'):
            self.assertIn(token, text, '提示词必须说明字段 %s' % token)
        self.assertIn('.json.tmp', text, '必须写明先写 .tmp 再改名')
        self.assertIn('failed', text, '必须写明失败也要落文件')

    def test_prompt_carries_the_task_body_and_the_right_producer(self):
        self.assertIn(self.task['body'], self.prompt('workbuddy'))
        self.assertIn('workbuddy', self.prompt('workbuddy'))
        self.assertIn('local-pi', self.prompt('local-pi'))

    def test_prompt_rejects_illegal_slug(self):
        with self.assertRaises(ValueError):
            ot.prompt_for({'slug': '../x', 'body': 'b'}, outbox=Path(self.tmp.name))


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from types import SimpleNamespace
        self.store = SimpleNamespace(workspace=Path(self.tmp.name))
        self.clues = Clues(self.store)

    def result(self, **over):
        data = ot.validate_from_mapping(payload(**over))
        return ot.ingest(self.clues, 'github-trending-ai', data)

    def test_ingest_creates_one_clue_per_item(self):
        out = self.result()
        self.assertEqual(out['imported'], 1)
        self.assertEqual(out['skipped_duplicates'], 0)
        listing = self.clues.listing()
        self.assertEqual(listing['total'], 1)
        clue = self.clues.detail(out['clue_ids'][0])
        self.assertEqual(clue['title'], 'A 开源推理框架')
        self.assertIn('WorkBuddy', clue['relevance'])

    def test_ingest_is_idempotent_for_the_same_file(self):
        first = self.result()
        second = self.result()
        self.assertEqual(first['imported'], 1)
        self.assertEqual(second['imported'], 0, '同一文件重复收取不得重复导入')
        self.assertEqual(second['skipped_duplicates'], 1)
        self.assertEqual(self.clues.listing()['total'], 1)

    def test_url_dedup_across_different_files(self):
        self.result(date='2026-10-10')
        out = self.result(date='2026-10-11')
        self.assertEqual(out['imported'], 0)
        self.assertEqual(out['skipped_duplicates'], 1)
        self.assertEqual(self.clues.listing()['total'], 1)

    def test_local_pi_producer_is_labelled(self):
        out = self.result(producer='local-pi')
        clue = self.clues.detail(out['clue_ids'][0])
        self.assertIn('本机 Pi', clue['relevance'])

    def test_failed_file_imports_nothing_but_is_accepted(self):
        out = self.result(status='failed', error='抓取失败', items=[])
        self.assertEqual(out['imported'], 0)
        self.assertEqual(self.clues.listing()['total'], 0)


class FallbackTests(unittest.TestCase):
    """到点执行：有合法文件不兜底；没有或不合法才兜底。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outbox = Path(self.tmp.name) / 'outbox'
        (self.outbox / 'github-trending-ai').mkdir(parents=True)
        from scheduled_tasks import ScheduledTasks
        self.tasks = ScheduledTasks(Path(self.tmp.name) / 'ws', autostart=False)
        self.addCleanup(self.tasks.close)
        from types import SimpleNamespace
        from clues_store import Clues
        self.clues = Clues(SimpleNamespace(workspace=Path(self.tmp.name)))
        self.tasks.outsourced_clues = self.clues
        self.pi_calls = []

    def fake_pi(self, run_id, task, cancel, config, deadline):
        """假的本机 Pi 兜底执行器：把约定格式的文件写到 outbox，不调任何模型。"""
        self.pi_calls.append(task.get('slug'))
        target = Path(task['cwd'])
        target.mkdir(parents=True, exist_ok=True)
        data = payload(slug=task['slug'], producer='local-pi', date=ot.today(),
                       items=[{'title': 'Pi 兜底交付', 'url': 'https://github.com/pi/fallback',
                               'summary': '本机补做的结果', 'tags': []}])
        tmp = target / f'{ot.today()}.json.tmp'
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(target / f'{ot.today()}.json')
        return 'succeeded', 0, '已写入交接文件'

    def task(self, **over):
        base = {'name': 'GitHub Trending AI 日报', 'kind': 'outsourced',
                'body': '抓 GitHub Trending 的 AI 仓库', 'cwd': str(self.outbox / 'github-trending-ai'),
                'frequency': 'daily', 'minutes': 60, 'at': '09:30', 'weekdays': [0, 1, 2, 3, 4, 5, 6],
                'timeout': 600, 'enabled': True, 'allow_commands': False,
                'slug': 'github-trending-ai', 'fallback': 'pi'}
        base.update(over)
        return base

    def run_once(self, task, date=None, pi_runner=None):
        with ot.outbox_root_override(self.outbox):
            return self.tasks._run_outsourced('run-test', task, threading.Event(), {},
                                              pi_runner=pi_runner or self.fake_pi,
                                              date=date)

    def test_existing_valid_file_never_triggers_fallback(self):
        folder = self.outbox / 'github-trending-ai'
        folder.mkdir(parents=True, exist_ok=True)
        # payload() 的默认日期是写死的 2026-10-10；调度路径按 ot.today() 找当天文件，
        # 两边不一致会被判成「date 与文件名日期不符」而误判为不合格。
        # 北京时间一旦跨过 10-10 就会引爆（2026-10-11 夜里真炸过一次）。
        (folder / f'{ot.today()}.json').write_text(
            json.dumps(payload(date=ot.today()), ensure_ascii=False))
        out = self.run_once(self.task())
        self.assertEqual(self.pi_calls, [], '有合法文件时绝不能烧本机 token')
        self.assertEqual(out['imported'], 1)
        self.assertEqual(out['producer'], 'workbuddy')

    def test_missing_file_triggers_pi_fallback(self):
        out = self.run_once(self.task())
        self.assertEqual(self.pi_calls, ['github-trending-ai'])
        self.assertEqual(out['producer'], 'local-pi')
        self.assertEqual(out['imported'], 1)

    def test_invalid_file_triggers_pi_fallback(self):
        folder = self.outbox / 'github-trending-ai'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{ot.today()}.json').write_text('{坏的 json')
        out = self.run_once(self.task())
        self.assertEqual(self.pi_calls, ['github-trending-ai'])
        self.assertTrue(out['warnings'], '兜底原因要写进 warnings')
        self.assertEqual(out['producer'], 'local-pi')

    def test_fallback_none_fails_with_a_clear_reason(self):
        out = self.run_once(self.task(fallback='none'))
        self.assertEqual(self.pi_calls, [])
        self.assertEqual(out['status'], 'failed')
        self.assertIn('未交付', out['error'])

    def test_fallback_that_writes_nothing_is_reported_not_claimed(self):
        # 假执行器报告成功却没留下交接文件 —— 不能顺着它说成功
        out = self.run_once(self.task(), pi_runner=lambda *a, **k: ('succeeded', 0, '我没写文件'))
        self.assertEqual(out['status'], 'failed')
        self.assertIn('没有', out['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

class TaskValidationTests(unittest.TestCase):
    """outsourced 任务类型在 ScheduledTasks.validate 里的约束。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outbox = Path(self.tmp.name) / 'outbox'
        (self.outbox / 'github-trending-ai').mkdir(parents=True)
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        from scheduled_tasks import ScheduledTasks
        self.tasks = ScheduledTasks(Path(self.tmp.name) / 'ws', autostart=False)
        self.addCleanup(self.tasks.close)
        self.tasks.outsourced_clues = None

    def payload(self, **over):
        base = {'name': 'GitHub Trending AI 日报', 'kind': 'outsourced',
                'body': '抓 GitHub Trending 的 AI 仓库', 'cwd': 'ignored',
                'frequency': 'daily', 'minutes': 60, 'at': '09:30',
                'weekdays': [0, 1, 2, 3, 4, 5, 6], 'timeout': 600,
                'enabled': True, 'allow_commands': False,
                'slug': 'github-trending-ai', 'fallback': 'pi'}
        base.update(over)
        return base

    def test_valid_task_gets_cwd_from_the_outbox_and_defaults_to_pi_fallback(self):
        # fallback=pi 需要一个可用的模型配置（兜底要用），这里直接桩掉模型解析
        self.tasks.model_config = lambda identity: {'model': 'synthetic', 'timeout_seconds': 100}
        task = self.tasks.validate(self.payload(profile_id='p'))
        self.assertEqual(task['slug'], 'github-trending-ai')
        self.assertEqual(task['fallback'], 'pi')
        self.assertEqual(task['cwd'], str((self.outbox / 'github-trending-ai').resolve()))

    def test_rejects_illegal_slug_and_fallback(self):
        for slug in ('../escape', 'Bad_Slug', '', 'x' * 41):
            with self.assertRaises(ValueError, msg=slug):
                self.tasks.validate(self.payload(slug=slug))
        with self.assertRaises(ValueError):
            self.tasks.validate(self.payload(fallback='manual'))

    def test_rejects_commands_and_explicit_clue_target(self):
        self.tasks.model_config = lambda identity: {'model': 'synthetic', 'timeout_seconds': 100}
        with self.assertRaises(ValueError):
            self.tasks.validate(self.payload(allow_commands=True, profile_id='p'))
        with self.assertRaises(ValueError):
            self.tasks.validate(self.payload(clue_id='a' * 32, profile_id='p'))

    def test_fallback_none_needs_no_model_configuration(self):
        task = self.tasks.validate(self.payload(fallback='none'))
        self.assertEqual(task['fallback'], 'none')


class PromptRouteTests(unittest.TestCase):
    """页面上「复制 WorkBuddy 提示词」走的那条只读入口。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outbox = Path(self.tmp.name) / 'outbox'
        (self.outbox / 'github-trending-ai').mkdir(parents=True)
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        from scheduled_tasks import ScheduledTasks
        self.tasks = ScheduledTasks(Path(self.tmp.name) / 'ws', autostart=False)
        self.addCleanup(self.tasks.close)
        self.tasks.model_config = lambda identity: {'model': 'synthetic', 'timeout_seconds': 100}
        task = self.tasks.validate({'name': 'GitHub Trending AI 日报', 'kind': 'outsourced',
                                    'body': '抓 GitHub Trending 的 AI 仓库', 'cwd': 'ignored',
                                    'frequency': 'daily', 'minutes': 60, 'at': '09:30',
                                    'weekdays': [0, 1, 2, 3, 4, 5, 6], 'timeout': 600, 'enabled': True,
                                    'allow_commands': False, 'profile_id': 'p',
                                    'slug': 'github-trending-ai', 'fallback': 'pi'})
        self.task_id = self.tasks.save(task)['id']

    def test_returns_a_prompt_with_the_absolute_path(self):
        result = self.tasks.outsourced_prompt(self.task_id)
        self.assertEqual(result['slug'], 'github-trending-ai')
        self.assertIn('github-trending-ai', result['prompt'])
        self.assertIn(str(Path(self.outbox).resolve() / 'github-trending-ai'), result['prompt'])
        self.assertIn('.json.tmp', result['prompt'])

    def test_rejects_a_non_outsourced_task(self):
        from scheduled_tasks import ScheduledTasks
        other = self.tasks.validate({'name': '普通脚本', 'kind': 'script', 'body': 'echo hi',
                                     'cwd': self.tmp.name, 'frequency': 'interval', 'minutes': 60,
                                     'at': '', 'weekdays': [], 'timeout': 60, 'enabled': True,
                                     'allow_commands': True})
        other_id = self.tasks.save(other)['id']
        with self.assertRaises(ValueError):
            self.tasks.outsourced_prompt(other_id)


# ── fixes-outsourced-tasks-1：上线就会出问题的三处 ─────────────────────────
class OutboxDirectoryTests(unittest.TestCase):
    """交接目录必须由建任务时建出来，不能要求黄总先手工建好。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 交接根目录**故意不存在**：黄总还没手工建过
        self.outbox = Path(self.tmp.name) / 'workbuddy-outbox'
        self.addCleanup(self.assertEqual, self.outbox.exists(), False,
                        '测试不能把目录留在真实 outbox 里')
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        from scheduled_tasks import ScheduledTasks
        self.tasks = ScheduledTasks(Path(self.tmp.name) / 'ws', autostart=False)
        self.addCleanup(self.tasks.close)
        self.tasks.model_config = lambda identity: {'model': 'synthetic', 'timeout_seconds': 100}

    def payload(self, **over):
        base = {'name': 'GitHub Trending AI 日报', 'kind': 'outsourced',
                'body': '抓 GitHub Trending 的 AI 仓库', 'cwd': 'ignored',
                'frequency': 'daily', 'minutes': 60, 'at': '09:30',
                'weekdays': [0, 1, 2, 3, 4, 5, 6], 'timeout': 600, 'enabled': True,
                'allow_commands': False, 'profile_id': 'p',
                'slug': 'github-trending-ai', 'fallback': 'pi'}
        base.update(over)
        return base

    def test_task_creation_builds_the_missing_outbox_subdirectory(self):
        self.assertFalse(self.outbox.exists(), '前置：交接根目录不存在')
        task = self.tasks.validate(self.payload())
        self.assertTrue(task['cwd'])
        folder = Path(task['cwd'])
        self.assertTrue(folder.is_dir(), '建外包任务时应顺手把交接子目录建出来')
        self.assertEqual(folder, (self.outbox / 'github-trending-ai').resolve())
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700, '交接目录应只对本机可读写')

    def test_second_task_reuses_the_same_directory_without_failing(self):
        first = self.tasks.validate(self.payload())
        second = self.tasks.validate(self.payload(name='另一个名字'))
        self.assertEqual(first['cwd'], second['cwd'])
        self.assertTrue(Path(second['cwd']).is_dir())

    def test_symlinked_slug_pointing_outside_the_root_is_rejected(self):
        # 先把根建出来，再把子目录做成指向根外的软链
        (self.outbox).mkdir(parents=True)
        outside = Path(self.tmp.name) / 'outside' / 'escaped'
        outside.parent.mkdir(parents=True)
        (self.outbox / 'github-trending-ai').symlink_to(outside)
        with self.assertRaises(ValueError) as caught:
            self.tasks.validate(self.payload())
        # 报错要说清是「slug 指到交接根之外」，不能被说成「无法创建目录」
        self.assertIn('之内', str(caught.exception))
        self.assertFalse(outside.exists(), '拒绝之后绝不能在根外建出目录')

    def test_symlink_is_rejected_before_any_directory_is_created(self):
        # 根也不存在、软链直接指向一个不存在的根外位置 —— 先建再建就会在根外留下目录
        outside = Path(self.tmp.name) / 'outside' / 'escaped'
        self.assertFalse(outside.exists())
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        self.outbox.mkdir(parents=True)
        (self.outbox / 'github-trending-ai').symlink_to(outside)
        with self.assertRaises(ValueError) as caught:
            self.tasks.validate(self.payload())
        # 悬空软链下 mkdir 会抛 FileExistsError；若先建后判，这里报出来的会是
        # 「无法创建交接目录」，把逃逸说成了权限问题 —— 所以要盯住报错内容。
        self.assertIn('之内', str(caught.exception))
        self.assertFalse(outside.exists(), '不得在交接根目录之外创建任何目录')


class UnconnectedStoreTests(unittest.TestCase):
    """线索存储没接上时要如实记失败，不能静默当成功。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outbox = Path(self.tmp.name) / 'outbox'
        (self.outbox / 'github-trending-ai').mkdir(parents=True)
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        from scheduled_tasks import ScheduledTasks
        self.tasks = ScheduledTasks(Path(self.tmp.name) / 'ws', autostart=False)
        self.addCleanup(self.tasks.close)
        self.tasks.outsourced_clues = None   # 模拟生产没装配
        self.day = ot.today()

    def write_delivery(self):
        data = payload(date=self.day)
        ot.write_atomically(self.outbox / 'github-trending-ai', self.day, data)
        return data

    def test_missing_store_fails_the_run_with_a_clear_reason(self):
        self.write_delivery()
        with ot.outbox_root_override(self.outbox):
            out = self.tasks._run_outsourced('run-test', {
                'slug': 'github-trending-ai', 'fallback': 'pi',
                'cwd': str(self.outbox / 'github-trending-ai'), 'timeout': 60},
                threading.Event(), {})
        self.assertEqual(out['status'], 'failed', '没导入就不能记成功')
        self.assertIn('线索存储入口未连接', out['error'])

    def test_delivery_file_is_kept_for_the_next_attempt(self):
        self.write_delivery()
        with ot.outbox_root_override(self.outbox):
            self.tasks._run_outsourced('run-test', {
                'slug': 'github-trending-ai', 'fallback': 'pi',
                'cwd': str(self.outbox / 'github-trending-ai'), 'timeout': 60},
                threading.Event(), {})
        kept = self.outbox / 'github-trending-ai' / f'{self.day}.json'
        self.assertTrue(kept.is_file(), '交付文件要留着，下次还能再收')

    def test_connected_store_still_succeeds(self):
        from types import SimpleNamespace
        from clues_store import Clues
        self.tasks.outsourced_clues = Clues(SimpleNamespace(workspace=Path(self.tmp.name)))
        self.write_delivery()
        with ot.outbox_root_override(self.outbox):
            out = self.tasks._run_outsourced('run-test', {
                'slug': 'github-trending-ai', 'fallback': 'pi',
                'cwd': str(self.outbox / 'github-trending-ai'), 'timeout': 60},
                threading.Event(), {})
        self.assertEqual(out['imported'], 1)
        self.assertNotIn('error', out)


class ProductionWiringTests(unittest.TestCase):
    """生产装配：server.py 的 main() 必须把线索存储接到 outsourced_clues。

    仓库里没有现成的「装配测试」写法（`main()` 会直接 serve_forever，起不来），
    所以这里从 `main()` 的语法树里**抽出真正的那条装配语句再执行**，
    而不是手抄一份 —— 手抄的断言测不到生产代码。
    """

    def assembly_statement(self):
        import ast, pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'server.py').read_text(encoding='utf-8'))
        main = next((node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == 'main'), None)
        self.assertIsNotNone(main, 'server.py 里找不到 main()')
        for node in ast.walk(main):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute) and target.attr == 'outsourced_clues'
                        and isinstance(target.value, ast.Attribute) and target.value.attr == 'schedules'
                        and isinstance(target.value.value, ast.Name)
                        and target.value.value.id == 'server'):
                    return node
        return None

    def test_main_assigns_the_clue_store_to_the_scheduler(self):
        import ast
        statement = self.assembly_statement()
        self.assertIsNotNone(statement,
                             'server.py 的 main() 里没有 server.schedules.outsourced_clues 装配，'
                             '上线后每次到点都会静默不导入')
        self.assertIsInstance(statement.value, ast.Attribute,
                              '装配右边应取线索存储的属性')
        self.assertEqual(statement.value.attr, 'clues', '必须接 store.clues')

    def test_wired_object_is_the_clues_instance_ingest_actually_accepts(self):
        import ast
        from types import SimpleNamespace
        statement = self.assembly_statement()
        self.assertIsNotNone(statement)
        from server import Store
        store = Store(self.__class__.workspace())
        self.addCleanup(store.knowledge.stop_event.set)   # Store 没有 close，只把线程停掉
        from scheduled_tasks import ScheduledTasks
        server = SimpleNamespace(schedules=ScheduledTasks(store.workspace, autostart=False))
        self.addCleanup(server.schedules.close)
        # 执行生产里真正的那一行
        module = ast.Module(body=[ast.fix_missing_locations(statement)], type_ignores=[])
        exec(compile(module, 'server.py:main', 'exec'), {'server': server, 'store': store})
        self.assertIs(server.schedules.outsourced_clues, store.clues,
                      '装配后应当就是 store.clues 那个实例')
        self.assertIsInstance(server.schedules.outsourced_clues, Clues)
        # 确认 ingest 真能用这个对象写入（这正是 ot.ingest 需要的东西）
        result = ot.ingest(server.schedules.outsourced_clues, 'github-trending-ai',
                           ot.validate_from_mapping(payload()))
        self.assertEqual(result['imported'], 1, '装配对象要真能导入线索')

    @classmethod
    def workspace(cls):
        if not hasattr(cls, '_workspace'):
            cls._workspace = tempfile.TemporaryDirectory()
        return cls._workspace.name


class PromptDateTests(unittest.TestCase):
    """H3：WorkBuddy 那份提示词不能写死日期。

    黄总只复制一次粘进 WorkBuddy，之后每天自动跑。日期一旦写死，每天都会写进
    同一个文件名，工作台按当天日期找不到文件 —— 天天判「未交付」。
    """

    SENTINEL = '2026-10-10'      # 当哨兵用：workbuddy 版里绝不该出现这个日期

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outbox = Path(self.tmp.name) / 'outbox'
        # 任务正文里不带日期，否则测不出提示词自身有没有写死日期
        self.task = {'slug': 'github-trending-ai', 'name': 'GitHub Trending AI 日报',
                     'body': '抓取 GitHub Trending 里 AI 相关的仓库，挑 5 个值得看的。'}
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def prompt(self, producer):
        return ot.prompt_for(self.task, producer=producer, outbox=self.outbox, date=self.SENTINEL)

    def test_workbuddy_prompt_carries_no_concrete_date_at_all(self):
        import re
        text = self.prompt('workbuddy')
        self.assertNotIn(self.SENTINEL, text, '提示词里出现了具体日期，WorkBuddy 会天天写同一个文件名')
        self.assertIsNone(re.search(r'\d{4}-\d{2}-\d{2}', text),
                          '提示词里不应有任何 YYYY-MM-DD 形态的具体日期')
        self.assertNotIn(f'{self.SENTINEL}.json', text)

    def test_workbuddy_prompt_makes_the_executor_compute_the_run_day(self):
        text = self.prompt('workbuddy')
        self.assertIn('YYYY-MM-DD', text, '必须留出占位符让执行者自己填')
        self.assertIn('北京时间', text, '必须写明按北京时间的当天算')
        self.assertIn('YYYY-MM-DD.json', text, '最终文件路径要留日期占位符')
        self.assertIn('YYYY-MM-DD.json.tmp', text, '临时文件路径同样要留占位符')
        # 日期字段与文件名必须是同一个当天日期
        self.assertIn('同一个', text, '要说清 date 字段和文件名用同一个日期')

    def test_workbuddy_prompt_keeps_the_task_text_and_fields(self):
        text = self.prompt('workbuddy')
        self.assertIn(self.task['body'], text)
        self.assertIn('github-trending-ai', text)
        self.assertIn(str((self.outbox / 'github-trending-ai').resolve()), text)
        self.assertIn('article-studio.outsourced.v1', text)
        for token in ('started_at', 'finished_at', 'status', 'error', 'items', 'failed'):
            self.assertIn(token, text)

    def test_local_pi_prompt_keeps_the_concrete_date(self):
        text = self.prompt('local-pi')
        self.assertIn(self.SENTINEL, text, '本机兜底每次现生成，写死当天日期才是对的')
        self.assertIn(f'{self.SENTINEL}.json', text, '本机兜底要给出确切的目标路径')
        self.assertIn(f'{self.SENTINEL}.json.tmp', text)
        self.assertIn(f'今天是 {self.SENTINEL}', text)

    def test_local_pi_prompt_is_not_mistaken_for_the_copy_paste_one(self):
        # 同一份任务，只换 producer：兜底版带日期，WorkBuddy 版不带
        pi = self.prompt('local-pi')
        buddy = self.prompt('workbuddy')
        self.assertIn(self.SENTINEL, pi)
        self.assertNotIn(self.SENTINEL, buddy)

    def test_workbench_today_is_beijing_time_not_utc(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        # 北京时间 2026-10-11 00:30 时，UTC 还停在 10-10；工作台必须按 10-11 找文件
        moment = datetime(2026, 10, 11, 0, 30, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertEqual(moment.astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%d'), '2026-10-10',
                         '前置：这个时刻在 UTC 还是 10-10')
        self.assertEqual(ot.today(moment), '2026-10-11', '工作台的「当天」必须按北京时间')
        self.assertEqual(ot.TZ, ZoneInfo('Asia/Shanghai'))

    def test_workbench_today_uses_beijing_time_when_called_without_arguments(self):
        # 上一条只验了「传了时刻怎么格式化」；这条钉住不传参数时到底取哪个时区 ——
        # 取错成 UTC 的话，每天 00:00–08:00（北京时间）工作台会去读昨天的文件。
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo
        instant = real_datetime(2026, 10, 10, 16, 30, tzinfo=ZoneInfo('UTC'))   # 北京时间 10-11 00:30
        self.assertEqual(instant.astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%d'), '2026-10-10',
                         '前置：这个时刻按 UTC 还是 10-10')

        class Frozen(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz is not None else instant

        original = ot.datetime
        ot.datetime = Frozen
        self.addCleanup(setattr, ot, 'datetime', original)
        self.assertEqual(ot.today(), '2026-10-11', '不传参数时也必须按北京时间取当天')


# ── fixes-outsourced-tasks-2：M1 / M2 / M3 / M4 / N1 ─────────────────────────
class FailedAndEmptyTests(unittest.TestCase):
    """M1：交付方说失败，就一条都不能导入；状态自相矛盾的文件直接拒收。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from types import SimpleNamespace
        from clues_store import Clues
        self.store = SimpleNamespace(workspace=Path(self.tmp.name))
        self.clues = Clues(self.store)
        self.outbox = Path(self.tmp.name) / 'outbox'
        (self.outbox / 'github-trending-ai').mkdir(parents=True)
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        from scheduled_tasks import ScheduledTasks
        self.tasks = ScheduledTasks(Path(self.tmp.name) / 'ws', autostart=False)
        self.addCleanup(self.tasks.close)
        self.tasks.outsourced_clues = self.clues
        self.pi_calls = []
        self.day = ot.today()

    ITEM = {'title': '失败也带了一条', 'url': 'https://e.com/failed',
            'summary': '这条不该被导入', 'tags': []}

    def write(self, **over):
        ot.write_atomically(self.outbox / 'github-trending-ai', self.day,
                            payload(date=self.day, **over))

    def task(self, **over):
        base = {'slug': 'github-trending-ai', 'fallback': 'pi', 'timeout': 60,
                'body': '抓 AI 相关仓库', 'name': 'AI 日报',
                'cwd': str(self.outbox / 'github-trending-ai')}
        base.update(over)
        return base

    def run_once(self, task=None):
        with ot.outbox_root_override(self.outbox):
            return self.tasks._run_outsourced('run-test', task or self.task(),
                                              threading.Event(), {}, pi_runner=self.fake_pi)

    def fake_pi(self, run_id, task, cancel, config, deadline):
        self.pi_calls.append(task.get('slug'))
        data = payload(slug='github-trending-ai', producer='local-pi', date=self.day,
                       items=[{'title': 'Pi 补做', 'url': 'https://e.com/pi-done',
                               'summary': '兜底结果', 'tags': []}])
        ot.write_atomically(Path(task['cwd']), self.day, data)
        return 'succeeded', 0, '已写入'

    # ── failed ──
    def test_failed_file_imports_nothing_even_with_items(self):
        out = ot.ingest(self.clues, 'github-trending-ai',
                        ot.validate_from_mapping(payload(date=self.day, status='failed',
                                                         error='抓取失败', items=[self.ITEM])))
        self.assertEqual(out['imported'], 0, '交付方说失败，就一条都不能进线索中心')
        self.assertEqual(self.clues.listing()['total'], 0)

    def test_failed_file_warns_how_many_items_were_ignored(self):
        out = ot.ingest(self.clues, 'github-trending-ai',
                        ot.validate_from_mapping(payload(date=self.day, status='failed',
                                                         error='抓取失败', items=[self.ITEM])))
        self.assertTrue(any('交付状态为失败' in w and '1 条' in w for w in out['warnings']),
                        '要写清忽略了几条：%r' % out['warnings'])

    def test_failed_delivery_with_fallback_none_records_failed_with_the_file_error(self):
        self.write(status='failed', error='目标站点返回 503', items=[self.ITEM])
        out = self.run_once(self.task(fallback='none'))
        self.assertEqual(out['status'], 'failed')
        self.assertIn('目标站点返回 503', out['error'], '回执的 error 要用文件里的 error')
        self.assertEqual(out['imported'], 0)
        self.assertEqual(self.clues.listing()['total'], 0)
        self.assertEqual(self.pi_calls, [])

    def test_failed_delivery_with_pi_fallback_retries_locally(self):
        self.write(status='failed', error='目标站点返回 503', items=[self.ITEM])
        out = self.run_once(self.task(fallback='pi'))
        self.assertEqual(self.pi_calls, ['github-trending-ai'], 'failed 时应按设置兜底')
        self.assertEqual(out['producer'], 'local-pi')
        self.assertEqual(out['imported'], 1)
        self.assertEqual(self.clues.listing()['total'], 1, '失败的旧条目不能混进来')

    # ── empty ──
    def test_empty_status_with_items_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            ot.validate_from_mapping(payload(date=self.day, status='empty', items=[self.ITEM]))
        self.assertIn('矛盾', str(caught.exception))

    def test_empty_delivery_with_no_items_is_accepted_and_imports_nothing(self):
        out = ot.ingest(self.clues, 'github-trending-ai',
                        ot.validate_from_mapping(payload(date=self.day, status='empty', items=[])))
        self.assertEqual(out['imported'], 0)
        self.assertEqual(out['status'], 'succeeded')

    def test_empty_delivery_does_not_burn_local_tokens(self):
        self.write(status='empty', items=[])
        out = self.run_once(self.task(fallback='pi'))
        self.assertEqual(self.pi_calls, [], '「今天没有可交付」是合法答案，不该兜底')
        self.assertEqual(out['status'], 'succeeded')
        self.assertEqual(out['imported'], 0)

    def test_failed_delivery_keeps_the_file_until_it_is_replaced(self):
        self.write(status='failed', error='503', items=[])
        self.run_once(self.task(fallback='none'))
        self.assertTrue((self.outbox / 'github-trending-ai' / f'{self.day}.json').is_file())


class LongTagTests(unittest.TestCase):
    """M2：自由标签再长也不能让一条合法交付整个导入失败。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from types import SimpleNamespace
        from clues_store import Clues, CATEGORIES
        self.CATEGORIES = CATEGORIES
        self.clues = Clues(SimpleNamespace(workspace=Path(self.tmp.name)))

    def long_item(self, summary='摘要', tag_size=501, count=12, tail_at=None):
        # tail_at 决定「区分各标签的字符」出现在第几字。默认放在最前面；
        # 测 provenance 时要放到 MAX_TAG 之后 —— 否则展示用的 40 字截断后
        # 仍然可区分，测试就分不出 raw_tags 和 tags，等于白测。
        def make(i):
            tail = '尾%03d' % i
            if tail_at is None:
                return tail + '-' + '标' * tag_size
            return '标' * tail_at + tail + '标' * tag_size
        return {'title': '长标签条目', 'url': 'https://e.com/longtags',
                'summary': summary, 'tags': [make(i) for i in range(count)]}

    def test_summary_cap_is_enforced_directly(self):
        # 靠场景触发不了：标签已截到 40 字、摘要最多 1000 字，拼起来离 4000 还很远。
        # 所以直接测 fit_summary 这层硬兜底，别让它当死代码躺着。
        warnings = []
        base = '摘' * ot.SUMMARY_LIMIT
        fitted = ot.fit_summary(base, ['标签' * 30], warnings, 'https://e.com/x')
        self.assertEqual(len(fitted), ot.SUMMARY_LIMIT)
        self.assertTrue(warnings, '被截到上限必须留警告')
        self.assertIn('4000', warnings[0])
        self.assertEqual(ot.fit_summary('短摘要', [], warnings), '短摘要')
        self.assertEqual(ot.fit_summary('短摘要', ['a'], warnings), '短摘要\n\n外包标签：a')

    def test_very_long_tags_do_not_break_the_import(self):
        item = self.long_item(summary='摘' * 1000)
        data = ot.validate_from_mapping(payload(items=[item]))
        out = ot.ingest(self.clues, 'github-trending-ai', data)
        self.assertEqual(out['imported'], 1, '合法文件不能因为标签长就整条导入失败：%r' % out['warnings'])

    def test_each_tag_is_capped_and_a_warning_is_left(self):
        data = ot.validate_from_mapping(payload(items=[self.long_item(tag_size=120)]))
        for tag in data['items'][0]['tags']:
            self.assertLessEqual(len(tag), ot.MAX_TAG)
        self.assertTrue(any('标签' in w for w in data['warnings']), '截断标签必须留警告')

    def test_provenance_keeps_the_original_tags_with_their_own_cap(self):
        # 区分字符放在第 60 字之后：展示用的 40 字截断后 12 个标签会塌成同一个，
        # provenance 里的原标签（截到 200）才分得开 —— 这样才测得出两者不是同一份。
        data = ot.validate_from_mapping(payload(items=[self.long_item(tag_size=600, tail_at=60)]))
        clue_id = ot.ingest(self.clues, 'github-trending-ai', data)['clue_ids'][0]
        detail = self.clues.detail(clue_id)
        raw = detail['entries'][0]['evidence'][0]['provenance']['tags']
        self.assertEqual(len(raw), 12, '原标签要一条不少地留在 provenance 里')
        self.assertTrue(any('尾' in t for t in raw), 'provenance 要留展示截断丢掉的尾部')
        self.assertEqual(data['items'][0]['tags'], ['标' * 40] , '展示用的标签在 40 字处就该塌成一个')
        for tag in raw:
            self.assertLessEqual(len(tag), ot.MAX_RAW_TAG)

    def test_clue_summary_never_exceeds_the_store_limit(self):
        item = self.long_item(summary='摘' * 1000, tag_size=40, count=12)
        out = ot.ingest(self.clues, 'github-trending-ai',
                        ot.validate_from_mapping(payload(items=[item])))
        clue = self.clues.detail(out['clue_ids'][0])
        self.assertLessEqual(len(clue['summary']), 4000, '拼完标签也不能超线索中心的 summary 上限')
        if len(clue['summary']) >= 4000:
            self.assertTrue(any('summary' in w or '摘要' in w for w in out['warnings']),
                            '被截到上限就要留警告：%r' % out['warnings'])


class WarningPropagationTests(unittest.TestCase):
    """M3：validate 产生的警告要一路走到调度回执，不能在中途蒸发。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from types import SimpleNamespace
        from clues_store import Clues
        self.clues = Clues(SimpleNamespace(workspace=Path(self.tmp.name)))
        self.outbox = Path(self.tmp.name) / 'outbox'
        (self.outbox / 'github-trending-ai').mkdir(parents=True)
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        from scheduled_tasks import ScheduledTasks
        self.tasks = ScheduledTasks(Path(self.tmp.name) / 'ws', autostart=False)
        self.addCleanup(self.tasks.close)
        self.tasks.outsourced_clues = self.clues
        self.day = ot.today()

    def truncated_item(self):
        return {'title': 'T' * 300, 'url': 'https://e.com/trunc', 'summary': 'S' * 1500, 'tags': []}

    def test_validate_warnings_survive_ingest(self):
        data = ot.validate_from_mapping(payload(date=self.day, items=[self.truncated_item()]))
        self.assertGreaterEqual(len(data['warnings']), 2, 'validate 阶段就该有截断警告')
        out = ot.ingest(self.clues, 'github-trending-ai', data)
        self.assertGreaterEqual(len(out['warnings']), 2,
                                'ingest 把警告弄丢了：%r' % out['warnings'])

    def test_validate_warnings_reach_the_scheduler_receipt(self):
        ot.write_atomically(self.outbox / 'github-trending-ai', self.day,
                            payload(date=self.day, items=[self.truncated_item()]))
        with ot.outbox_root_override(self.outbox):
            out = self.tasks._run_outsourced('run-test', {
                'slug': 'github-trending-ai', 'fallback': 'pi', 'timeout': 60,
                'body': '抓 AI 相关仓库', 'name': 'AI 日报',
                'cwd': str(self.outbox / 'github-trending-ai')}, threading.Event(), {},
                pi_runner=lambda *a, **k: ('succeeded', 0, '不该被调用'))
        self.assertGreaterEqual(len(out['warnings']), 2,
                                '完整调度链上警告丢了：%r' % out['warnings'])

    def test_ingest_also_accepts_raw_data_and_normalises_once(self):
        out = ot.ingest(self.clues, 'github-trending-ai',
                        payload(date=self.day, items=[self.truncated_item()]))
        self.assertEqual(out['imported'], 1)
        self.assertGreaterEqual(len(out['warnings']), 2)


class FallbackAttributionTests(unittest.TestCase):
    """M4：走本机 Pi 兜底，producer 必须三处一致。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from types import SimpleNamespace
        from clues_store import Clues
        self.clues = Clues(SimpleNamespace(workspace=Path(self.tmp.name)))
        self.outbox = Path(self.tmp.name) / 'outbox'
        (self.outbox / 'github-trending-ai').mkdir(parents=True)
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        from scheduled_tasks import ScheduledTasks
        self.tasks = ScheduledTasks(Path(self.tmp.name) / 'ws', autostart=False)
        self.addCleanup(self.tasks.close)
        self.tasks.outsourced_clues = self.clues
        self.day = ot.today()

    def run_with(self, claimed):
        def fake_pi(run_id, task, cancel, config, deadline):
            ot.write_atomically(Path(task['cwd']), self.day,
                                payload(slug='github-trending-ai', producer=claimed, date=self.day,
                                        items=[{'title': '兜底结果', 'url': 'https://e.com/pi',
                                                'summary': '本机补做', 'tags': []}]))
            return 'succeeded', 0, '已写入'
        with ot.outbox_root_override(self.outbox):
            return self.tasks._run_outsourced('run-test', {
                'slug': 'github-trending-ai', 'fallback': 'pi', 'timeout': 60,
                'body': '抓 AI 相关仓库', 'name': 'AI 日报',
                'cwd': str(self.outbox / 'github-trending-ai')}, threading.Event(), {},
                pi_runner=fake_pi)

    def test_artifact_claiming_workbuddy_is_relabelled_everywhere(self):
        out = self.run_with('workbuddy')
        self.assertEqual(out['producer'], 'local-pi', '回执的 producer 必须是实际交付方')
        clue = self.clues.detail(out['clue_ids'][0])
        self.assertEqual(clue['entries'][0]['evidence'][0]['provenance']['producer'], 'local-pi',
                         '证据来源也必须是 local-pi，页面徽章才跟回执一致')
        self.assertTrue(any('local-pi' in w for w in out['warnings']), '要留一条归属说明')
        self.assertFalse(any('已按实际交付方记为' in w and 'producer=workbuddy' in w
                             and 'local-pi' not in w for w in out['warnings']))

    def test_artifact_claiming_local_pi_stays_local_pi_without_a_reassignment_warning(self):
        out = self.run_with('local-pi')
        self.assertEqual(out['producer'], 'local-pi')
        self.assertEqual(
            self.clues.detail(out['clue_ids'][0])['entries'][0]['evidence'][0]['provenance']['producer'],
            'local-pi')
        self.assertFalse([w for w in out['warnings'] if '自称' in w],
                         '本来就写对了就不该说它自称错：%r' % out['warnings'])

    def test_relabelling_the_producer_does_not_drop_the_validation_warnings(self):
        """ot-3 L1 的 M3 兜底：改 producer 时不能把数据降级成普通 dict。

        改 producer 那一步若写成 `{**data, 'producer': ...}`，adopt 就不认这份数据，
        于是重新规范化 —— validate 记下的截断警告再次蒸发（就是 M3 那个 bug）。
        """
        def fake_pi(run_id, task, cancel, config, deadline):
            ot.write_atomically(Path(task['cwd']), self.day,
                                payload(slug='github-trending-ai', producer='workbuddy',
                                        date=self.day,
                                        items=[{'title': 'T' * 300, 'url': 'https://e.com/trunc',
                                                'summary': 'S' * 1500, 'tags': []}]))
            return 'succeeded', 0, '已写入'
        with ot.outbox_root_override(self.outbox):
            out = self.tasks._run_outsourced('run-test', {
                'slug': 'github-trending-ai', 'fallback': 'pi', 'timeout': 60,
                'body': '抓 AI 相关仓库', 'name': 'AI 日报',
                'cwd': str(self.outbox / 'github-trending-ai')}, threading.Event(), {},
                pi_runner=fake_pi)
        self.assertEqual(out['producer'], 'local-pi')
        self.assertGreaterEqual(len(out['warnings']), 2,
                                '改 producer 之后警告被弄丢了：%r' % out['warnings'])
        self.assertTrue(any('超过' in w for w in out['warnings']),
                        'validate 的截断警告要留在回执里：%r' % out['warnings'])


class ForgedNormalisedFlagTests(unittest.TestCase):
    """ot-3 L1：`normalised: true` 写在 JSON 里，任何人都能伪造，不能当信任依据。

    改前 `_looks_normalised` 只认 `normalised is True` + schema + task 三个字段，
    直接把 dict 递给 ingest 就能跳过全部校验（empty+非空 items、javascript URL
    都能混进线索中心）。改后 adopt 只认模块私有的 `_Normalised` 实例：
    普通 dict（包括 JSON 往返、`dict(data)` 复制出来的）一律重新校验。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from types import SimpleNamespace
        self.clues = Clues(SimpleNamespace(workspace=Path(self.tmp.name)))
        self.day = '2026-10-10'

    def forged(self, **over):
        data = payload(date=self.day, **over)
        data['normalised'] = True          # 从 JSON 里就能伪造的标记
        return data

    def bad_items(self):
        return [{'title': '伪造条目', 'url': 'javascript:alert(1)', 'summary': 'x', 'tags': []}]

    def test_forged_flag_cannot_skip_the_status_consistency_check(self):
        data = self.forged(status='empty', items=self.bad_items())
        with self.assertRaises(ValueError) as caught:
            ot.ingest(self.clues, 'github-trending-ai', data)
        self.assertIn('empty', str(caught.exception))
        self.assertEqual(self.clues.listing()['total'], 0, '拒收就不能留下任何线索')

    def test_forged_flag_cannot_smuggle_a_non_http_url(self):
        data = self.forged(items=self.bad_items())
        with self.assertRaises(ValueError) as caught:
            ot.ingest(self.clues, 'github-trending-ai', data)
        self.assertIn('http', str(caught.exception))
        self.assertEqual(self.clues.listing()['total'], 0)

    def test_wrong_task_slug_is_revalidated_despite_the_flag(self):
        data = self.forged(task='other-task')
        with self.assertRaises(ValueError) as caught:
            ot.ingest(self.clues, 'github-trending-ai', data)
        self.assertIn('task', str(caught.exception))

    def test_unknown_producer_is_revalidated_despite_the_flag(self):
        data = self.forged(producer='someone-else')
        with self.assertRaises(ValueError) as caught:
            ot.ingest(self.clues, 'github-trending-ai', data)
        self.assertIn('producer', str(caught.exception))

    def test_json_round_trip_loses_the_trust_and_is_revalidated(self):
        """真规范化过的数据经 JSON 往返后退回「不可信」，必须重新校验。"""
        good = ot.validate_from_mapping(payload(date=self.day))
        copied = json.loads(json.dumps(good))
        self.assertNotIsInstance(copied, type(good))
        copied['items'][0]['url'] = 'javascript:alert(1)'
        with self.assertRaises(ValueError) as caught:
            ot.ingest(self.clues, 'github-trending-ai', copied)
        self.assertIn('http', str(caught.exception))
        self.assertEqual(self.clues.listing()['total'], 0)

    def test_plain_dict_copy_of_valid_data_is_still_accepted_after_revalidation(self):
        """重新校验不等于拒收：普通 dict 的合法数据照样能导入。"""
        out = ot.ingest(self.clues, 'github-trending-ai',
                        dict(ot.validate_from_mapping(payload(date=self.day))))
        self.assertEqual(out['imported'], 1)

    def test_normalised_field_is_kept_in_the_output_but_is_not_the_trust(self):
        """字段继续写给人看，但信任已经改到类型上。"""
        data = ot.validate_from_mapping(payload(date=self.day))
        self.assertIs(data['normalised'], True)
        self.assertIn('"normalised": true', json.dumps(data, ensure_ascii=False))

    def test_adopt_returns_the_internal_type_for_normalised_data(self):
        """adopt 认的是类型，不是字段；警告照样带着（M3）。"""
        data = ot.validate_from_mapping(payload(date=self.day, items=[
            {'title': 'T' * 300, 'url': 'https://e.com/trunc', 'summary': 'S' * 1500,
             'tags': []}]))
        adopted = ot.adopt(data, 'github-trending-ai')
        self.assertGreaterEqual(len(adopted['warnings']), 2, 'adopt 把警告弄丢了：%r' % adopted)
        self.assertEqual(adopted['items'][0]['title'], 'T' * 200)
        # 普通 dict 版（哪怕字段一模一样）不会被当成已规范化
        plain = dict(data)
        self.assertNotEqual(ot.adopt(plain, 'github-trending-ai').get('warnings'),
                            adopted['warnings'],
                            '普通 dict 必须重新校验，警告应重新计算而不是照抄')

    def test_relabel_producer_keeps_the_trust_and_the_warnings(self):
        data = ot.validate_from_mapping(payload(date=self.day, items=[
            {'title': 'T' * 300, 'url': 'https://e.com/trunc', 'summary': 'S' * 1500,
             'tags': []}]))
        relabelled = ot.relabel_producer(data, 'local-pi')
        self.assertEqual(relabelled['producer'], 'local-pi')
        out = ot.ingest(self.clues, 'github-trending-ai', relabelled)
        self.assertEqual(out['producer'], 'local-pi')
        self.assertGreaterEqual(len(out['warnings']), 2,
                                '改 producer 后警告蒸发（M3 回归）：%r' % out['warnings'])
        with self.assertRaises(ValueError):
            ot.relabel_producer(dict(data), 'local-pi')


class PageSubmitPayloadTests(unittest.TestCase):
    """ot-3 L2：页面真正提交的那份 payload，直接送进后端 validate。

    改前：用正则从 schedules.js 里抓字段白名单，再手工补 slug/fallback 造 payload，
    还要求两个字段写在源码同一行 —— 那是文本形状测试，不是真实链路：
    格式等价重排会误红，字段表达式写错也可能被手工补值掩盖。

    改后（合同里的方案 A：真实链路）：让 jsdom 真点一次提交，把 fetch 实际收到的
    body 原样写到一个临时 JSON（见 tests/test_outsourced_tasks_ui.cjs 的 ot3-L2 用例），
    这里读回来送进 ScheduledTasks.validate()。字段怎么拼、按类型带哪几个，
    全由 schedules.js 自己决定，测试不替它补，也不再对源码格式提要求。
    """

    ROOT = Path(__file__).resolve().parents[1]
    UI_TEST = Path(__file__).resolve().parent / 'test_outsourced_tasks_ui.cjs'
    # jsdom 只装在工作区这个目录（仓库根没有 node_modules），cjs 侧靠 NODE_PATH 找到它。
    JSDOM_DIR = ROOT / 'work' / 'x-dom-test' / 'node_modules'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outbox = Path(self.tmp.name) / 'outbox'
        self.outbox.mkdir(parents=True, exist_ok=True)
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        from scheduled_tasks import ScheduledTasks
        self.tasks = ScheduledTasks(Path(self.tmp.name) / 'ws', autostart=False)
        self.addCleanup(self.tasks.close)
        self.tasks.model_config = lambda identity: {'model': 'synthetic', 'timeout_seconds': 100}

    def captured(self):
        """跑一次 jsdom 真实提交，返回 fetch 实际收到的两份 body。"""
        node = shutil.which('node')
        if node is None or not self.UI_TEST.is_file():
            self.skipTest('本机没有 node 或找不到 %s，跑不了真实提交链路' % self.UI_TEST)
        env = dict(os.environ, OT_CAPTURE_PAYLOAD=str(Path(self.tmp.name) / 'captured.json'),
                   OT_CAPTURE_CWD=str(self.outbox))
        env.setdefault('NODE_PATH', str(self.JSDOM_DIR))
        if not (Path(env['NODE_PATH']) / 'jsdom').is_dir():
            self.skipTest('找不到 jsdom（NODE_PATH=%s）' % env['NODE_PATH'])
        proc = subprocess.run([node, str(self.UI_TEST)], cwd=str(self.ROOT), env=env,
                              capture_output=True, text=True, timeout=180)
        base = Path(self.tmp.name) / 'captured.json'
        if not base.is_file() or not Path(str(base) + '.pi.json').is_file():
            self.fail('jsdom 没写出真实提交 payload（exit=%s）：\n%s\n%s'
                      % (proc.returncode, proc.stdout[-1500:], proc.stderr[-1500:]))
        self.assertEqual(proc.returncode, 0, 'cjs 用例失败：\n%s' % proc.stdout[-2000:])
        with base.open(encoding='utf-8') as fh:
            outsourced = json.load(fh)
        with Path(str(base) + '.pi.json').open(encoding='utf-8') as fh:
            plain = json.load(fh)
        return outsourced, plain

    def test_captured_outsourced_payload_validates_end_to_end(self):
        outsourced, _ = self.captured()
        self.assertEqual(outsourced.get('kind'), 'outsourced', '捕获的必须是外包任务那份')
        task = self.tasks.validate(outsourced)
        self.assertEqual(task['slug'], 'github-trending-ai')
        self.assertEqual(task['fallback'], 'pi')
        self.assertEqual(Path(task['cwd']), (self.outbox / 'github-trending-ai').resolve(),
                         '交接子目录由后端按 slug 推出来，不是页面填的 cwd')
        self.assertTrue(Path(task['cwd']).is_dir(), '交接子目录应顺手建好')

    def test_captured_payload_without_slug_is_rejected(self):
        outsourced, _ = self.captured()
        del outsourced['slug']
        with self.assertRaises(ValueError):
            self.tasks.validate(outsourced)

    def test_captured_pi_payload_carries_no_slug_and_validates_as_pi(self):
        """slug/fallback 只跟外包任务走：看的是真 payload，不是源码写法。"""
        _, plain = self.captured()
        self.assertEqual(plain.get('kind'), 'pi')
        self.assertNotIn('slug', plain, '非外包的 payload 不该带 slug：%r' % sorted(plain))
        self.assertNotIn('fallback', plain, '非外包的 payload 不该带 fallback：%r' % sorted(plain))
        task = self.tasks.validate(plain)
        self.assertEqual(task['kind'], 'pi')
        self.assertNotIn('slug', task)
