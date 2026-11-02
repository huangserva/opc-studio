import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import today_view as T



class TodayTodoColumnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='today-todo-')
        self.root = Path(self.temp.name)
        self.reports = self.root / 'reports'
        (self.reports / 'daily').mkdir(parents=True)
        self.date = '2026-11-02'
        self.now = datetime(2026, 11, 2, 1, 19, tzinfo=timezone.utc)
        self.env = patch.dict(os.environ, GROK_BOT_REPORTS_DIR=str(self.reports))
        self.env.start()


    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def write(self, count=3, **extra):
        rows = [{'id': str(100 + i), 'handle': 'fixture', 'author': '作者',
                 'created_at': '2026-11-01T16:01:00Z', 'title_zh': '帖子 ' + str(i),
                 'url': 'https://x.com/fixture/status/' + str(100 + i),
                 'full_text': '原文正文', 'content_complete': True,
                 'views': 13000 + i, 'views_per_hour': 1200, 'likes': 0,
                 'label': '爆款', 'fit_score': 3, 'doable': 2, 'my_angle': '切入 ' + str(i)} for i in range(count)]
        todo = [{'id': row['id'], 'fit_score': 3, 'doable': 3, 'my_angle': '日报角度 ' + str(i)} for i, row in enumerate(reversed(rows))]
        data = {'date': self.date, 'collected_at': self.date + 'T09:20:00+08:00',
                'items': rows, 'today_todo': todo, **extra}
        path = self.reports / 'daily' / (self.date + '.json')
        path.write_text(json.dumps(data, ensure_ascii=False))
        return data

    def test_report_three_order_and_values(self):
        data = self.write(3)
        result = T.today_todo_view(self.root, self.now)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual([x['report_id'] for x in result['items']], [x['id'] for x in data['today_todo']])
        self.assertEqual([x['my_angle'] for x in result['items']], [x['my_angle'] for x in data['today_todo']])
        self.assertEqual(result['items'][0]['doable'], 3)
        self.assertEqual(result['items'][0]['metrics']['views'], 13002)
        self.assertEqual(result['items'][0]['metrics']['likes'], 0)
        self.assertEqual(result['items'][0]['full_text'], '原文正文')
        self.assertEqual(result['items'][0]['platform_info']['key'], 'x')

    def test_six_not_reselected_or_truncated(self):
        data = self.write(6)
        result = T.today_todo_view(self.root, self.now)
        self.assertEqual([x['id'] for x in result['items']], ['x:' + x['id'] for x in data['today_todo']])

    def test_empty_list_is_explicit_empty(self):
        self.write(0, notes='今天全是旧闻，不凑数。')
        result = T.today_todo_view(self.root, self.now)
        self.assertEqual((result['status'], result['items'], result['reason']), ('empty', [], '今天全是旧闻，不凑数。'))

    def test_report_says_none(self):
        self.write(0, today_todo=[], notes='今天没有值得做的，证据不足。')
        result = T.today_todo_view(self.root, self.now)
        self.assertEqual(result['status'], 'empty')
        self.assertIn('证据不足', result['reason'])

    def test_missing_report_does_not_borrow_yesterday(self):
        data = self.write(3)
        path = self.reports / 'daily' / (self.date + '.json')
        path.rename(path.with_name('2026-11-01.json'))
        result = T.today_todo_view(self.root, self.now)
        self.assertEqual((result['date'], result['status'], result['items']), (self.date, 'pending', []))
        self.assertEqual(result['reason'], '今天的日报还没出，09:00 后更新')

    def test_beijing_date_boundary(self):
        self.write(3)
        result = T.today_todo_view(self.root, datetime(2026, 11, 1, 16, 1, tzinfo=timezone.utc))
        self.assertEqual(result['date'], self.date)
        self.assertEqual(len(result['items']), 3)

    def test_wrong_report_date_not_empty(self):
        self.write(3, date='2026-11-01')
        result = T.today_todo_view(self.root, self.now)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['items'], [])

    def test_malformed_report_not_empty(self):
        self.write(3, today_todo=[{'id': '../invalid'}])
        self.assertEqual(T.today_todo_view(self.root, self.now)['status'], 'unavailable')

    def test_symlink_report_rejected(self):
        self.write(3)
        path = self.reports / 'daily' / (self.date + '.json')
        dest = path.with_name('source.json')
        path.rename(dest)
        path.symlink_to(dest)
        self.assertEqual(T.today_todo_view(self.root, self.now)['status'], 'unavailable')

    def test_missing_item_remains_in_report_order(self):
        self.write(0, today_todo=[{'id': '123', 'title': '日报给的标题', 'my_angle': '原样角度'}])
        result = T.today_todo_view(self.root, self.now)
        self.assertEqual(result['items'][0]['id'], 'x:123')
        self.assertEqual(result['items'][0]['my_angle'], '原样角度')
        self.assertFalse(result['items'][0]['content_complete'])

    def test_legacy_posts_and_object_references(self):
        data = self.write(3)
        self.write(0, posts=data['items'], today_todo=[{'id':'102'},{'id':'100'}])
        result = T.today_todo_view(self.root, self.now)
        self.assertEqual([x['id'] for x in result['items']], ['x:102', 'x:100'])
        self.assertEqual(result['items'][0]['metrics']['views'], 13002)

    def test_detail_fallback_for_not_in_current_list(self):
        self.write(3)
        with patch.object(T, 'snapshot', return_value=([], {})), patch.object(T, 'today_todo_view', wraps=lambda root: T_today(root, self.now)):
            detail = T.item_view(self.root, {'id': ['x:102'], 'today_todo': ['1']})
        self.assertEqual(detail['id'], 'x:102')
        self.assertEqual(detail['full_text'], '原文正文')

    def test_read_only_bytes_unchanged(self):
        self.write(6)
        before = {str(p): p.read_bytes() for p in self.reports.rglob('*') if p.is_file()}
        T.today_todo_view(self.root, self.now)
        after = {str(p): p.read_bytes() for p in self.reports.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_reader_failure_is_error_state_not_page_failure(self):
        with patch.object(T.grok, 'load_today_todo', side_effect=ValueError('日报损坏')):
            result = T.today_todo_view(self.root, self.now)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn(result['status'], ('empty', 'pending'))
        self.assertEqual(result['items'], [])

    def test_items_only_daily_restores_list_scores(self):
        self.write(3)
        posts = [{'id': '100', 'sources': [{'file': 'daily/' + self.date + '.json', 'collected_at': self.date + 'T09:20:00+08:00'}]}]
        extras, bad = T._report_extras(posts)
        self.assertFalse(bad)
        self.assertEqual(extras['100']['fit_score'], 3)
        self.assertEqual(extras['100']['doable'], 2)
        self.assertEqual(extras['100']['my_angle'], '切入 0')

    def test_existing_feedback_and_draft_state_retained(self):
        self.write(3)
        with patch.object(T, '_load_feedback', return_value={'x:102': {'status': 'used', 'feedback': 'accurate'}}):
            item = T.today_todo_view(self.root, self.now)['items'][0]
        self.assertEqual((item['status'], item['feedback']), ('used', 'accurate'))

    def test_invalid_number_is_not_displayed(self):
        data = self.write(1)
        data['items'][0]['views'] = float('inf')
        path = self.reports / 'daily' / (self.date + '.json')
        path.write_text(json.dumps(data))
        self.assertIsNone(T.today_todo_view(self.root, self.now)['items'][0]['metrics']['views'])


T_today = T.today_todo_view
if __name__ == '__main__':
    unittest.main()
