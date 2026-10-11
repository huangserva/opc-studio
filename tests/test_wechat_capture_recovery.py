"""Synthetic capture receipts: a confirmed stuck capture recovers on its own.

Nothing here touches a real collector database, index, cache or receipt folder.
"""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from isolation_patches import WECHAT_SOURCE_REASON, wechat_source_available
# hygiene-4：下面的 import 会加载微信来源插件（clue_pipeline 等在模块级调
# wechat_source_loader.source_module）。插件由 ARTICLE_STUDIO_WECHAT_MODULE 或
# workspace/sources/wechat.json 指定，两者都是本机资源，不进公开仓库；
# 干净克隆里没有它，这里直接跳过整个模块（unittest 会把 SkipTest 记成 skip
# 而不是 error）。本机配置齐全时行为与从前逐字一致。
if not wechat_source_available():
    raise unittest.SkipTest(WECHAT_SOURCE_REASON)

from clue_pipeline import CluePipeline
from knowledge_store import Knowledge
from server import Store
from wechat_refresh_worker import read_private, write_private


def stamp(seconds_ago=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class CaptureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.collector = self.root / 'collector.db'
        with sqlite3.connect(self.collector) as db:
            db.execute('CREATE TABLE messages(chatroom_id,local_id,sender,content,msg_time,msg_type)')
            db.execute('CREATE TABLE watched_chats(chatroom_id,chatroom_name)')
            db.execute("INSERT INTO watched_chats VALUES('g@chatroom','合成项目群')")
            db.execute("INSERT INTO messages VALUES('g@chatroom','1','小明','合成既有消息',1791018000,1)")
        helpers = self.root / 'helpers'
        helpers.mkdir()
        for name in ('refresh_snapshot.py', 'stage_collector.py'):
            (helpers / name).write_text('# synthetic dependency present\n')
        self.store = Store(self.root / 'studio')
        self.store._knowledge = Knowledge(self.store, [{'id': 'wechat', 'name': '合成微信', 'kind': 'wechat',
            'backend': 'collector', 'path': str(self.collector), 'names_path': str(self.collector)}])
        self.knowledge = self.store.knowledge
        self.knowledge.sync('wechat')
        self.capture = self.knowledge.capture
        write_private(self.capture.root / 'config.json', {'helper_dir': str(helpers),
            'capture_dir': str(self.root / 'captured'), 'collector_db': str(self.collector),
            'python': sys.executable, 'timeout_seconds': 30})

    def job(self, state):
        identifier = 'ab' * 16
        folder = self.capture.root / identifier
        folder.mkdir(mode=0o700, exist_ok=True)
        write_private(folder / 'state.json', state)
        write_private(self.capture.root / 'latest.json', {'id': identifier})
        return folder

    def stuck_state(self, **changes):
        state = {'status': 'needs_review', 'phase': 'captured', 'started_at': stamp(900),
            'captured_at': stamp(890), 'finished_at': stamp(880), 'messages_imported': True,
            'collector_messages': 1, 'new_messages': 1, 'max_msg_time': 1791018000,
            'commit_receipt_saved': True, 'index_status': 'pending',
            'detail': '采集回执或索引需要核对；旧资料保留'}
        state.update(changes)
        return state

    def test_confirmed_stuck_capture_recovers_without_rewriting_the_receipt(self):
        folder = self.job(self.stuck_state())
        before = read_private(folder / 'state.json')
        state = self.capture.status()
        self.assertEqual(state['status'], 'done')
        self.assertTrue(state['recovered'])
        self.assertEqual(state['index_status'], 'ready')
        self.assertEqual(datetime.fromisoformat(state['last_success']['finished_at']),
            datetime.fromisoformat(read_private(folder / 'state.json')['finished_at']))
        self.assertEqual(state['last_success']['index_status'], 'ready')
        self.assertIn('自动恢复', state['detail'])
        # The receipt on disk is left exactly as the worker wrote it.
        self.assertEqual(read_private(folder / 'state.json'), before)
        self.assertEqual(self.knowledge.status()['sources'][0]['status'], 'ready')

    def test_recovered_capture_is_allowed_to_run_again(self):
        self.job(self.stuck_state(started_at=stamp(7300), captured_at=stamp(7290), finished_at=stamp(7280)))
        self.capture._source = lambda: {'enabled': True, 'capture_enabled': True}
        self.capture.retry_after = 0
        calls = []
        self.capture.start = lambda: calls.append(True)
        self.capture.maybe_start()
        self.assertEqual(calls, [True])
        # An unrecovered receipt still blocks the automatic next capture.
        with self.knowledge.db() as db:
            db.execute("UPDATE sources SET synced_at=? WHERE id='wechat'", (stamp(9000),))
        calls.clear()
        self.capture.maybe_start()
        self.assertEqual(calls, [])

    def test_unconfirmed_capture_stays_needs_review(self):
        # The index refresh predates the capture, so completeness is unproven.
        with self.knowledge.db() as db:
            db.execute("UPDATE sources SET synced_at=? WHERE id='wechat'", (stamp(3600),))
        self.job(self.stuck_state())
        state = self.capture.status()
        self.assertEqual(state['status'], 'needs_review')
        self.assertNotIn('recovered', state)
        self.assertIn('需要核对', state['detail'])

    def test_unconfirmed_commit_never_reports_success(self):
        self.job(self.stuck_state(status='commit_unknown', messages_imported=False,
            detail='正式采集库提交结果需要核对，已停止自动重试'))
        state = self.capture.status()
        self.assertEqual(state['status'], 'commit_unknown')
        self.assertNotIn('recovered', state)
        self.assertNotIn('last_success', state)

    def test_live_capture_process_blocks_recovery(self):
        import fcntl
        import os
        self.job(self.stuck_state())
        path = self.capture.root / '.capture.lock'
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = self.capture.status()
            self.assertEqual(state['status'], 'needs_review')
            self.assertNotIn('recovered', state)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(self.capture.status()['status'], 'done')

    def test_recovered_capture_is_visible_in_the_analysis_receipt(self):
        self.job(self.stuck_state())
        result = CluePipeline(self.store).prepare_cache(threading.Event(), time.monotonic() + 30)
        self.assertTrue(result['fresh'])
        self.assertTrue(result['recovered'])
        self.assertIn('自动恢复', result['detail'])
        self.assertEqual(result['index_status'], 'ready')

    def test_pipeline_still_consumes_a_verified_cache_while_the_receipt_needs_review(self):
        pipeline = CluePipeline(self.store)
        verified = {'finished_at': stamp(60), 'index_status': 'ready', 'new_messages': 1}
        state = {'available': True, 'status': 'needs_review', 'busy': False, 'detail': '采集回执需要核对',
            'last_success': verified}
        self.knowledge.capture = SimpleNamespace(status=lambda: state, _source=lambda: {'enabled': True},
            retry_after=time.monotonic() + 100, start=lambda: None)
        result = pipeline.prepare_cache(threading.Event(), time.monotonic() + 30)
        self.assertFalse(result['fresh'])
        self.assertEqual(result['warning'], '采集回执需要核对')
        self.assertEqual(result['index_status'], 'ready')
        # While a capture may still be running, the old cache stays refused.
        state['busy'] = True
        with self.assertRaisesRegex(ValueError, '微信采集需要核对'):
            pipeline.prepare_cache(threading.Event(), time.monotonic() + 30)
        # And an unverified cache is never presented as usable.
        state.update(busy=False, last_success={'finished_at': stamp(60), 'index_status': 'error'})
        with self.assertRaisesRegex(ValueError, '未开始模型分析'):
            pipeline.prepare_cache(threading.Event(), time.monotonic() + 30)


if __name__ == '__main__':
    unittest.main()
