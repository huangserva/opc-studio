"""GET /api/schedules/<id>/outsourced_prompt：路由本身（第 9 轮浏览器验收发现 404）。

现有测试只直接调 `schedules.outsourced_prompt()`，cjs 里又是 mock，所以 server.py 里
`segments[:3]==['api','schedules']` 写错（应为 `[:2]`）没有被拦住，页面上「复制 WorkBuddy
提示词」按钮拿到的是「页面不存在」。这一份起真实 HTTP 服务走完整路由：命中 handler、返回
提示词正文；未知 id 由 handler 如实报错，而不是落到路由兜底的 404。

全程临时工作区 + 临时交接目录：不联网、不调模型、不写正式库。
"""
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import outsourced_tasks as ot
from scheduled_tasks import ScheduledTasks
from server import Handler, Store

SLUG = 'github-trending-ai'


class PromptRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / 'ws'
        self.outbox = root / 'outbox'
        (self.outbox / SLUG).mkdir(parents=True)
        context = ot.outbox_root_override(self.outbox)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.tasks = ScheduledTasks(root, autostart=False)
        self.addCleanup(self.tasks.close)
        self.tasks.model_config = lambda identity: {'model': 'synthetic', 'timeout_seconds': 100}
        task = self.tasks.validate({'name': 'GitHub Trending AI 日报', 'kind': 'outsourced',
                                    'body': '抓 GitHub Trending 的 AI 仓库', 'cwd': 'ignored',
                                    'frequency': 'daily', 'minutes': 60, 'at': '09:30',
                                    'weekdays': [0, 1, 2, 3, 4, 5, 6], 'timeout': 600, 'enabled': True,
                                    'allow_commands': False, 'profile_id': 'p',
                                    'slug': SLUG, 'fallback': 'pi'})
        self.task_id = self.tasks.save(task)['id']
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.store = Store(str(root))
        self.httpd.schedules = self.tasks
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_port

    def shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def get(self, path):
        try:
            with urlopen(Request(self.base + path), timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            raw = exc.read().decode('utf-8', 'replace')
            exc.close()
            return exc.code, json.loads(raw) if raw else {}

    def test_the_prompt_route_is_reachable_over_http(self):
        status, body = self.get('/api/schedules/%s/outsourced_prompt' % self.task_id)
        self.assertEqual(status, 200, body)
        self.assertIn(str((self.outbox / SLUG).resolve()), body['prompt'])
        self.assertEqual(body['slug'], SLUG)
        self.assertIn('.json.tmp', body['prompt'])

    def test_an_unknown_task_id_reaches_the_handler_not_the_page_fallback(self):
        status, body = self.get('/api/schedules/%s/outsourced_prompt' % ('0' * 32))
        self.assertEqual(status, 400, body)
        self.assertEqual(body.get('error'), '任务不存在')
        self.assertNotEqual(body.get('error'), '页面不存在')

    def test_a_non_outsourced_task_is_refused_by_the_handler(self):
        other = self.tasks.validate({'name': '普通脚本', 'kind': 'script', 'body': 'echo hi',
                                     'cwd': self.tmp.name, 'frequency': 'interval', 'minutes': 60,
                                     'at': '', 'weekdays': [], 'timeout': 60, 'enabled': True,
                                     'allow_commands': True})
        other_id = self.tasks.save(other)['id']
        status, body = self.get('/api/schedules/%s/outsourced_prompt' % other_id)
        self.assertEqual(status, 400, body)
        self.assertIn('只有外包任务', body.get('error', ''))

    def test_the_run_route_still_wins_for_a_run_id(self):
        """相邻路由不受影响：/api/schedules/run/<id> 仍走 get_run，不会被提示词路由抢走。"""
        status, body = self.get('/api/schedules/run/%s' % ('1' * 32))
        self.assertEqual(status, 400, body)
        self.assertEqual(body.get('error'), '运行记录不存在')


if __name__ == '__main__':
    unittest.main()
