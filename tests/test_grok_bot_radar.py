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


    def test_items_reports_all_kinds_and_empty(self):
        for kind in api.KINDS:
            with self.subTest(kind=kind):
                self.write(kind + '/items.json', [self.row('7')], key='items')
        result = api.load_reports()
        self.assertEqual(result['report_count'], 3)
        self.assertEqual(result['bad_files'], 0)
        self.assertEqual([p['id'] for p in result['posts']], ['7'])
        self.assertEqual(len(result['posts'][0]['sources']), 3)
        self.write('daily/empty.json', [], key='items')
        self.assertEqual(api.load_reports()['report_count'], 4)

    def test_multi_key_same_ids_do_not_duplicate_or_change_precedence(self):
        path = self.write(rows=[self.row('1', views=11)])
        data = json.loads(path.read_text())
        data.update(entries=[self.row('1', views=22), self.row('2')],
                    items=[self.row('1', views=33), dict(self.row('1'), id=1),
                           {'url': 'https://x.com/test/status/2'}, self.row('3')])
        path.write_text(json.dumps(data))
        result = api.load_reports()
        self.assertEqual((result['report_count'], result['bad_files'], result['skipped_entries']), (1, 0, 0))
        posts = {p['id']: p for p in result['posts']}
        self.assertEqual(set(posts), {'1', '2', '3'})
        self.assertEqual(posts['1']['views'], 11)
        self.assertTrue(all(len(p['sources']) == 1 for p in posts.values()))

    def test_bad_items_reject_even_with_valid_posts(self):
        path = self.write()
        for invalid in ('text', {}, 1, None, True):
            for other_keys in ({}, {'posts': [self.row()]}, {'entries': []}):
                with self.subTest(items=invalid, keys=list(other_keys)):
                    path.write_text(json.dumps(dict(other_keys, items=invalid)))
                    result = api.load_reports()
                    self.assertEqual((result['bad_files'], result['report_count'], result['posts']), (1, 0, []))
        path.write_text('{"notes":"missing all post keys"}')
        self.assertEqual(api.load_reports()['bad_files'], 1)
        self.write(rows=[self.row('8'), {}, None], key='items')
        self.assertEqual(api.load_reports()['skipped_entries'], 2)

    def test_today_todo_missing_empty_and_notes(self):
        day = '2026-11-02'
        self.write('daily/2026-11-01.json')
        self.assertIsNone(api.load_today_todo(day))
        self.assertEqual(api.load_today_todo(day, with_notes=True), {'items': None, 'notes': None})
        path = self.write('daily/' + day + '.json', [], key='items')
        data = {'items': [], 'today_todo': [], 'notes': '今天没有：没有同时达到适配度和可执行门槛的帖子。'}
        path.write_text(json.dumps(data, ensure_ascii=False))
        before = path.read_bytes(), path.stat().st_mtime_ns
        self.assertEqual(api.load_today_todo(day), [])
        self.assertEqual(api.load_today_todo(day, with_notes=True), {'items': [], 'notes': data['notes']})
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_today_todo_original_order_and_all_fields(self):
        rows = [dict(self.row(id), handle='原作者', label='起势', fit_score=95-i,
                     doable=8+i, my_angle='我的角度', why='为什么', future_field={'nested':[i]})
                for i, id in enumerate(('9', '2', '7'))]
        path = self.write('daily/2026-11-02.json', rows, key='items')
        path.write_text(json.dumps({'items': rows, 'today_todo': rows, 'notes': ['原始 notes']}, ensure_ascii=False))
        self.assertEqual(api.load_today_todo('2026-11-02', self.reports), rows)
        self.assertEqual(api.load_today_todo('2026-11-02', self.reports, with_notes=True), {'items': rows, 'notes': ['原始 notes']})
        self.assertEqual([row['id'] for row in api.load_today_todo('2026-11-02')], ['9', '2', '7'])

    def test_today_todo_bad_data_does_not_become_missing_or_empty(self):
        path = self.write('daily/2026-11-02.json')
        for data in ({}, [], {'today_todo':None}, {'today_todo':{}}, {'today_todo':'bad'}, {'today_todo':[None]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                path.write_text(json.dumps(data))
                api.load_today_todo('2026-11-02')
        path.write_text('{"today_todo":[')
        with self.assertRaises(ValueError):
            api.load_today_todo('2026-11-02')
        path.write_text('{"today_todo":[]}')
        self.assertEqual(api.load_today_todo('2026-11-02', with_notes=True), {'items': [], 'notes': None})

    def test_today_todo_strict_date_and_symlink_protection(self):
        for day in ('../2026-11-02', '2026-2-01', '2026-02-30', '2026-11-02T00:00:00Z', None):
            with self.subTest(day=day), self.assertRaises(ValueError):
                api.load_today_todo(day)
        path = self.write('daily/2026-11-02.json')
        target = self.base / 'outside-todo.json'
        target.write_text('{"today_todo":[]}')
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(OSError):
            api.load_today_todo('2026-11-02')
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            api.load_today_todo('2026-11-02')


    def test_auxiliary_prefixes_skip_without_parsing_and_count(self):
        self.write()
        prefixes = ('knowledge_payloads', 'knowledge_post_results', '_post_results')
        count = 0
        for kind in ('daily', 'alerts'):
            for prefix in prefixes:
                path = self.reports / kind / (prefix + '_x.json')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('not valid JSON')
                count += 1
                result = api.load_reports()
                self.assertEqual(result['skipped_aux_files'], count)
                self.assertEqual(result['ignored_files'], count)
                self.assertEqual((result['bad_files'], result['report_count']), (0, 1))
                self.assertEqual([p['id'] for p in result['posts']], ['1'])
        # A parseable auxiliary payload cannot introduce posts or latest timestamps.
        self.write('alerts/knowledge_payloads_valid.json', [self.row('99')], when='2099-01-01T00:00:00Z')
        result = api.load_reports()
        self.assertEqual(result['skipped_aux_files'], 7)
        self.assertEqual(result['latest_report']['file'], 'daily/a.json')
        self.assertEqual([p['id'] for p in result['posts']], ['1'])
        (self.reports / 'alerts/knowledge_payloads_valid.json').unlink()
        self.assertEqual(api.load_reports()['skipped_aux_files'], 6)

    def test_actual_bad_reports_and_middle_prefix_still_bad(self):
        self.write(rows=[self.row()])
        names = ('daily/2026-11-02.json', 'alerts/2026-11-02-1200.json',
                 'daily/random.json', 'daily/2026-11-02-knowledge.json',
                 'alerts/before_knowledge_payloads_x.json', 'daily/before_post_results_x.json')
        for name in names:
            path = self.reports / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('broken JSON')
        (self.reports / 'daily/knowledge_payloads_x.json').write_text('broken auxiliary')
        (self.reports / 'daily/knowledge_payloads_x.txt').write_text('not JSON')
        result = api.load_reports()
        self.assertEqual(result['bad_files'], len(names))
        self.assertEqual(result['skipped_aux_files'], 1)
        self.assertEqual(result['ignored_files'], 2)
        self.assertEqual(result['report_count'], 1)
        for name in names:
            (self.reports / name).write_text('{"notes":"missing all post keys"}')
        self.assertEqual(api.load_reports()['bad_files'], len(names))

    def test_weekly_with_post_keys_remains_a_post_report(self):
        for i, key in enumerate(('posts', 'entries', 'items'), 1):
            self.write('weekly/' + key + '.json', [self.row(str(i))], key=key)
        result = api.load_reports()
        self.assertEqual({p['id'] for p in result['posts']}, {'1', '2', '3'})
        self.assertEqual((result['report_count'], result['bad_files'], result['skipped_aux_files']), (3, 0, 0))
        path = self.reports / 'weekly/items.json'
        for invalid in ('bad', {}, None):
            with self.subTest(items=invalid):
                path.write_text(json.dumps({'items': invalid}))
                result = api.load_reports()
                self.assertEqual((result['report_count'], result['bad_files'], result['skipped_aux_files']), (2, 1, 0))

    def test_weekly_without_post_keys_counts_as_auxiliary(self):
        self.write()
        folder = self.reports / 'weekly'
        folder.mkdir()
        (folder / '2026-W44.json').write_text('{"summary":"周报","notes":"无帖子键"}')
        result = api.load_reports()
        self.assertEqual((result['report_count'], result['bad_files'], result['skipped_aux_files']), (1, 0, 1))
        (folder / 'another.json').write_text('{}')
        self.assertEqual(api.load_reports()['skipped_aux_files'], 2)
        # Unparseable JSON and non-object data are still bad: no valid summary schema.
        (folder / 'broken.json').write_text('{')
        (folder / 'array.json').write_text('[]')
        result = api.load_reports()
        self.assertEqual((result['bad_files'], result['skipped_aux_files']), (2, 2))


if __name__ == '__main__':
    unittest.main()
