"""所有输入/反馈均使用临时目录；HTTP 安全与浏览器另有隔离 E2E。"""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import grok_bot_radar as api

NOW = datetime(2026, 10, 29, 13, tzinfo=timezone.utc)
LONG_TEXT = '第一行😀 <script>alert(1)</script> &amp; <b>原文</b>\n\n' + '完整长文' * 600


class GrokBotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='grok-radar-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.reports = self.base / 'reports'
        self.workspace = self.base / 'workspace'
        self.workspace.mkdir()
        self.env = patch.dict(os.environ, {'GROK_BOT_REPORTS_DIR': str(self.reports)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.bundle_patch = patch.object(api, 'BUNDLE_PATH', self.base / 'bundle.json')
        self.bundle_patch.start()
        self.addCleanup(self.bundle_patch.stop)

    def write(self, name='daily/a.json', rows=None, when=None, key='posts'):
        path = self.reports / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {key: rows if rows is not None else [self.row()], 'collected_at': when or NOW.isoformat()}
        path.write_text(json.dumps(data, ensure_ascii=False))
        return path

    def row(self, id='1', **kw):
        return dict({'id': id, 'text': LONG_TEXT, 'url': 'https://x.com/test/status/' + id, 'likes': 5, 'created_at': '2026-10-29T00:00:00Z'}, **kw)

    def test_full_original_and_numbers(self):
        self.write(rows=[self.row(views='8202', bookmarks='1.2万', followers=True, replies='NaN')])
        post = api.radar_view(self.workspace, now=NOW)['posts'][0]
        self.assertEqual(post['text'], LONG_TEXT)
        self.assertEqual(post['views'], 8202)
        self.assertIsNone(post['bookmarks'])
        self.assertIsNone(post['followers'])
        self.assertIsNone(post['replies'])

    def test_safe_links(self):
        for url in ['javascript:alert(1)', 'http://evil.com/x.com/status/1', 'https://x.com.evil.com/a/status/1', '', 'https://x.com/a/status/1?x=2', 'https://x.com/a/status/1\n']:
            with self.subTest(url=url):
                self.write(rows=[self.row(url=url)])
                self.assertIsNone(api.load_reports()['posts'][0]['url'])
        self.assertEqual(api.safe_url('https://twitter.com/a/status/123'), 'https://twitter.com/a/status/123')

    def test_bad_files(self):
        self.write()
        folder = self.reports / 'daily'
        for name, content in [('empty.json', ''), ('partial.json', '{"posts":['), ('object.json', '{"posts":{}}'), ('entries.json', '{"entries":42}'), ('huge.json', ' ' * (api.MAX_BYTES + 1))]:
            (folder / name).write_text(content)
        (folder / 'ignored.txt').write_text('not json')
        outside = self.base / 'outside.json'
        outside.write_text('{"posts":[]}')
        (folder / 'symlink.json').symlink_to(outside)
        self.write('alerts/b.json', rows=[{}, None, {'text': 'missing id'}, {'id': '2'}])
        result = api.load_reports()
        self.assertEqual(result['bad_files'], 6)
        self.assertEqual(result['ignored_files'], 1)
        self.assertEqual(result['skipped_entries'], 3)
        self.assertEqual(len(result['posts']), 2)

    def test_missing_empty(self):
        self.assertEqual(api.radar_view(self.workspace, now=NOW)['count'], 0)
        self.reports.mkdir()
        self.assertIsNone(api.radar_view(self.workspace, now=NOW)['latest_report'])

    def test_merge_latest_and_url_fallback(self):
        self.write(rows=[self.row(views=1)], when='2026-10-29 12:00 Asia/Shanghai')
        self.write('alerts/b.json', [self.row(views=20)], '2026-10-29 20:00 Asia/Shanghai', 'entries')
        self.write('alerts/c.json', [self.row(views=30), {'url': 'https://x.com/a/status/2'}], '2026-10-29 20:30 Asia/Shanghai', 'entries')
        posts = {p['id']: p for p in api.load_reports()['posts']}
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts['1']['views'], 30)
        self.assertEqual(len(posts['1']['sources']), 3)
        self.assertEqual(posts['1']['first_discovered_at'], '2026-10-29T12:00:00+08:00')
        self.assertEqual(posts['1']['last_updated_at'], '2026-10-29T20:30:00+08:00')

    def test_time_boundary_and_stale(self):
        self.write(rows=[self.row(created_at=(NOW - timedelta(hours=24)).isoformat())], when=(NOW - timedelta(hours=24)).isoformat())
        self.write('daily/b.json', [self.row('2', created_at=(NOW - timedelta(hours=24, seconds=1)).isoformat())], (NOW - timedelta(hours=24, seconds=1)).isoformat())
        result = api.radar_view(self.workspace, hours=24, now=NOW)
        self.assertEqual([p['id'] for p in result['posts']], ['1'])
        self.assertEqual(result['stale_hours'], 24)
        self.assertEqual(api.stamp('2026-10-29 20:13 Asia/Shanghai').astimezone(timezone.utc).hour, 12)
        self.assertEqual(api.stamp('2026-10-29T00:00:00Z').astimezone(api.BEIJING).hour, 8)

    def test_feedback_concurrent_and_mirror(self):
        self.write(rows=[self.row('1'), self.row('2')])
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda id: api.save_feedback(self.workspace, {'tweet_id': id, 'verdict': 'worth'}), ['1','2']))
        local = api.feedback_path(self.workspace)
        mirror = self.reports / 'feedback/studio-feedback.json'
        self.assertEqual(local.read_bytes(), mirror.read_bytes())
        self.assertEqual(set(json.loads(local.read_text())), {'1','2'})
        api.save_feedback(self.workspace, {'tweet_id': '1', 'verdict': 'used'})
        self.assertEqual(api.load_feedback(self.workspace)['feedback']['1']['verdict'], 'used')
        api.save_feedback(self.workspace, {'tweet_id': '1', 'verdict': None})
        self.assertEqual(set(api.load_feedback(self.workspace)['feedback']), {'2'})
        self.assertFalse(list(local.parent.glob('.studio-feedback-*')))
        self.assertFalse(list(mirror.parent.glob('.studio-feedback-*')))

    def test_feedback_payload_and_corruption(self):
        self.write()
        for payload in [None, [], {}, {'tweet_id': '9', 'verdict': 'worth'}, {'tweet_id': 1, 'verdict': 'worth'}, {'tweet_id': '1'*26, 'verdict': 'worth'}, {'tweet_id': '1', 'verdict': []}, {'tweet_id': '1', 'verdict': 'a'*10000}, {'tweet_id': '1', 'verdict': True}, {'tweet_id': '1', 'verdict': 'worth', 'extra': 1}]:
            with self.subTest(payload=str(payload)[:80]), self.assertRaises(ValueError):
                api.save_feedback(self.workspace, payload)
        path = api.feedback_path(self.workspace)
        path.parent.mkdir(parents=True)
        path.write_text('broken')
        with self.assertRaises(api.FeedbackCorrupt):
            api.save_feedback(self.workspace, {'tweet_id': '1', 'verdict': 'worth'})
        self.assertEqual(path.read_text(), 'broken')
        self.assertEqual(api.load_feedback(self.workspace)['feedback_status'], 'invalid')

    def test_hot_load_half_written_and_new_report(self):
        partial = self.write()
        self.assertEqual(api.radar_view(self.workspace, now=NOW)['count'], 1)
        partial.write_text('{"posts":[')
        self.assertEqual(api.radar_view(self.workspace, now=NOW)['bad_files'], 1)
        self.write('alerts/new.json', rows=[self.row('2')])
        self.assertEqual(api.radar_view(self.workspace, now=NOW)['posts'][0]['id'], '2')
        self.write()
        self.assertEqual(api.radar_view(self.workspace, now=NOW)['count'], 2)

    def test_unified_merge_metrics_and_content(self):
        grok = self.row('1', author='作者', handle='a', followers=2000, label='爆款', views=900, bookmarks=111, last_updated_at='2026-10-29T12:00:00Z', article={'title': '标题', 'body': LONG_TEXT}, topic='Agent与Coding')
        aisa = {'id':'1', 'user':'a', 'name':'作者', 'fc':2000, 'text':'AIsa 摘要', 'views':100, 'fetched_at':'2026-10-29T10:00:00Z', 'published_at':grok['created_at']}
        merged = api.merge_posts([grok], {'vip_tweets':[aisa], 'tweets':[aisa]})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['sources'], ['AIsa','Grok'])
        self.assertEqual(merged[0]['views'], 900)
        self.assertEqual(merged[0]['view_groups'], ['feed'])
        self.assertEqual(merged[0]['article']['body'], LONG_TEXT)
        self.assertEqual(merged[0]['track'], 'claude_code')
        aisa['fetched_at'] = '2026-10-29T13:00:00Z'
        self.assertEqual(api.merge_posts([grok], {'vip_tweets':[aisa]})[0]['views'], 100)

    def test_rich_fields_survive_and_filters_match_existing(self):
        rich = dict(full_text=LONG_TEXT, article={'title':'长文', 'body':LONG_TEXT}, thread=[{'text':'串推'}], quoted={'author':'引用者','text':'引用'}, media=[{'type':'photo','url':'https://example.com/a.png'}], card={'title':'外链','url':'https://example.com/'}, content_complete=False)
        self.write(rows=[self.row(**rich)])
        result = api.load_reports()['posts'][0]
        for key, value in rich.items():
            self.assertEqual(result[key], value)
        blocked = self.row('2', text='AI 量化 合约', label='观察')
        self.assertFalse(api.merge_posts([blocked], {})[0]['content_eligible'])
        unknown = self.row('3', text='今天写了一封信。', label='观察')
        self.assertIsNone(api.merge_posts([unknown], {})[0]['track'])

    def test_latest_alert_staleness_and_hours_validation(self):
        self.write(when=NOW.isoformat())
        self.write('alerts/a.json', when=(NOW-timedelta(hours=7)).isoformat())
        self.assertEqual(api.radar_view(self.workspace, now=NOW)['stale_hours'], 7)
        for hours in [1, 'bad', None]:
            with self.assertRaises(ValueError):
                api.radar_view(self.workspace, hours, NOW)


if __name__ == '__main__':
    unittest.main()
