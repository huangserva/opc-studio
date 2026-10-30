import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import linuxdo_source as linux
import linuxdo_report
import today_view as today
from scripts.radar_common import today_relevance_gate
from tests.test_linuxdo_source import rss

NOW = datetime(2026, 10, 30, 8, tzinfo=timezone.utc)


class H3aTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='h3a-', dir='/tmp')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {'GROK_BOT_REPORTS_DIR': str(self.root / 'reports')})
        env.start()
        self.addCleanup(env.stop)

    def item(self, source='L', **changes):
        row = linux.parse_rss(rss())[0]
        row.update(sources=[source])
        row.update(changes)
        return row

    def test_category_is_item_field_not_channel_or_description(self):
        body = rss('<p>福利羊毛</p>').replace(b'<channel>', '<channel><category>搞七捻三</category>'.encode())
        self.assertEqual(linux.parse_rss(body)[0]['topic'], '开发调优')
        wrong = self.item(topic='福利羊毛')
        self.assertFalse(today_relevance_gate(wrong)['visible'])
        self.assertTrue(today_relevance_gate(self.item())['visible'])

    def test_discourse_namespaced_pinned_is_per_item(self):
        body = rss().replace(b'<rss ', b'<rss xmlns:discourse="http://www.discourse.org/" ')
        body = body.replace(b'<channel>', b'<channel><discourse:topicPinned>true</discourse:topicPinned>')
        body = body.replace(b'<item><title>', b'<item><discourse:topicPinned>false</discourse:topicPinned><title>', 1)
        self.assertEqual(len(linux.parse_rss(body)), 1)
        # 同一条目内的 true 才应剔除；重复 URL 条目也置顶，避免假通过。
        body = body.replace(b'<discourse:topicPinned>false', b'<discourse:topicPinned>true')
        body = body.replace(b'<item><title>', b'<item><discourse:topicPinned>true</discourse:topicPinned><title>')
        with self.assertRaises(ValueError):
            linux.parse_rss(body)

    def test_blacklist_overrides_ai_even_tag_source(self):
        row = self.item(title='不限次数的 seedance2.0', topic='福利羊毛', rss_boards=['AI 标签'])
        verdict = today_relevance_gate(row)
        self.assertFalse(verdict['visible'])
        self.assertEqual(verdict['reason'], '分类黑名单：福利羊毛')

    def test_non_ai_all_sources_and_grok_only_interest_exception(self):
        for source in ('G', 'A', 'V', 'L'):
            row = self.item(source, title='亲哥欠钱10w怎么办', full_text='今天被骗的钱追回来了', topic='闲聊')
            self.assertFalse(today_relevance_gate(row)['visible'])
            verdict = today_relevance_gate(row, channel='interest')
            self.assertEqual(verdict['visible'], source == 'G')
            self.assertEqual(verdict['marked_only'], source == 'G')
        self.assertFalse(today_relevance_gate(dict(row, sources=['G', 'A']), channel='interest')['visible'])

    def test_custom_interest_terms_and_existing_ai_keywords(self):
        row = self.item('A', title='新一代 Maya 工具发布', full_text='附使用说明和接口参数', topic='')
        self.assertFalse(today_relevance_gate(row)['visible'])
        self.assertTrue(today_relevance_gate(row, ['新 API（Maya、APXInf）'])['visible'])
        self.assertTrue(today_relevance_gate(dict(row, title='3D 新作品'), ['3D / 游戏 / 硬件 Agent 落地'])['visible'])
        self.assertTrue(today_relevance_gate(self.item('V', title='Claude 新模型', topic=''))['visible'])

    def test_link_only_marks_and_existing_quote_expands(self):
        row = self.item('G', full_text='无语凝噎 https://t.co/abc https://t.co/xyz')
        self.assertTrue(today.prepare_content(row)['content_insufficient'])
        row['rich_sources'] = [{'quoted': {'text': 'Claude 发布新的 Agent 编程工具，提供本地运行方式与调用参数。'}}]
        self.assertFalse(today.prepare_content(row)['content_insufficient'])
        self.assertIn('Agent', row['expanded_text'])

    def test_per_source_counts_and_readable_short_names(self):
        rows = [self.item('G'), self.item('L', topic='搞七捻三'), self.item('A', title='今天天气', full_text='无关闲聊', topic='')]
        kept, audit = today.apply_gate(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(audit['total'], 2)
        self.assertEqual(audit['by_source']['linux.do']['filtered'], 1)
        self.assertEqual(audit['by_source']['AIsa 推特']['ratio'], 1)
        self.assertNotIn('G', audit['by_source'])
        self.assertTrue(all(row['url'].startswith('https://linux.do/') for row in audit['items']))

    def test_raw_aisa_dedup_uses_full_51_snapshots_not_bundle(self):
        folder = self.root / 'twitter-radar/snapshots/2026-10-30'
        folder.mkdir(parents=True)
        rows = [dict(tweet_id='1', text='Claude 新模型发布', published_at=NOW.isoformat()),
                dict(tweet_id='2', text='今天吃了什么', published_at=NOW.isoformat())]
        for name in ('a', 'b'):
            (folder / (name + '.json')).write_text(json.dumps(dict(target_count=51, tweets=rows)))
        stats, audit = today.aisa_full_stats(self.root, NOW - timedelta(hours=1), NOW)
        self.assertEqual((stats['total'], stats['filtered'], stats['ratio'], stats['snapshot_count']), (2, 1, .5, 2))
        self.assertEqual([p['id'] for p in audit if p['hidden']], ['x:2'])

    def test_inaccurate_cumulative_transition_idempotency_and_channel(self):
        row = self.item('L', topic='开发调优')
        today.accumulate_inaccurate(self.root, row, {}, 'inaccurate')
        today.accumulate_inaccurate(self.root, row, {'feedback': 'inaccurate'}, 'inaccurate')
        today.accumulate_inaccurate(self.root, row, {'feedback': 'inaccurate'}, 'clear')
        today.accumulate_inaccurate(self.root, row, {'feedback': 'accurate'}, 'inaccurate', channel='interest')
        data = json.loads(today.feedback_path(self.root).with_name('inaccurate-counts.json').read_text())
        self.assertEqual(data['counts'], {'linux.do': {'开发调优': 2}})

    def test_feedback_public_write_path_accumulates(self):
        row = self.item()
        with patch.object(today, 'item_view', return_value=row):
            for action in ('inaccurate', 'inaccurate', 'clear', 'inaccurate'):
                today.save_feedback(self.root, {'id': row['id'], 'action': action})
        data = json.loads(today.feedback_path(self.root).with_name('inaccurate-counts.json').read_text())
        self.assertEqual(data['counts']['linux.do']['开发调优'], 2)

    def test_insufficient_kept_last_and_excluded_from_top(self):
        short = self.item('G', id='x:1', full_text='https://t.co/abc', title='AI', fit_score=3, doable=3, tier='爆款')
        normal = self.item('G', id='x:2', full_text='Claude 新模型提供开源权重、本地推理方法与完整调用参数。', fit_score=3, doable=3, tier='爆款')
        for row in (short, normal):
            row['hot'] = False
            today._heat(row)
        with patch.object(today, 'snapshot', return_value=([short, normal], {})), patch.object(linux, 'refresh_background'):
            listing = today.page_view(self.root, {'range': ['48h']}, NOW)
        self.assertEqual([r['id'] for r in listing['items']], ['x:2', 'x:1'])
        self.assertEqual([r['id'] for r in listing['top']], ['x:2'])

    def test_feed_topic_id_merge_partial_failure_and_pinned(self):
        row = self.item()
        with patch.object(linux, 'fetch_rss', side_effect=[[row], ValueError(), [dict(row, url='https://linux.do/t/123')], [row]]):
            rows, errors = linux.fetch_feeds()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['rss_boards'], ['AI 标签', '日榜', '周榜'])
        self.assertEqual(len(errors), 1)
        body = rss().replace(b'<title>linux.do AI', b'<pinned>true</pinned><title>linux.do AI').replace(b'<title>\xe9\x87\x8d\xe5\xa4\x8d\xe9\x93\xbe\xe6\x8e\xa5', b'<pinned>true</pinned><title>\xe9\x87\x8d\xe5\xa4\x8d\xe9\x93\xbe\xe6\x8e\xa5')
        with self.assertRaises(ValueError):
            linux.parse_rss(body)

    def test_previous_reply_delta_and_report_six_hours(self):
        with patch.object(linux, 'fetch_feeds', return_value=([self.item()], [])):
            linux.refresh(self.root, NOW)
        increased = self.item()
        increased['metrics']['replies'] = 12
        with patch.object(linux, 'fetch_feeds', return_value=([increased], [])):
            linux.refresh(self.root, NOW + timedelta(hours=1))
            report = linuxdo_report.generate(self.root, NOW + timedelta(hours=1))
        self.assertIn('↑+5', report.read_text())
        self.assertIn('RSS 不提供浏览数', report.read_text())
        self.assertTrue((linux.directory(self.root) / 'previous.json').exists())
        self.assertEqual(linuxdo_report.schedule_payload(self.root)['minutes'], 360)


if __name__ == '__main__':
    unittest.main()
