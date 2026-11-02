"""筛选空结果与匹配基数分离；只写/tmp，保持原筛选结果不变。"""
from contextlib import ExitStack
from datetime import timedelta
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import grok_bot_radar as G
import interest_channel as I
import interest_projection as P
import interest_sources as S
import today_view as T
from tests.test_interest_all import fixture, NOW


class InterestEmptyFilterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='interest-filter-empty-', dir='/tmp')
        self.addCleanup(tmp.cleanup)
        self.root, self.reports = fixture(tmp.name)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.dict(os.environ, {'GROK_BOT_REPORTS_DIR': str(self.reports)}))
        stack.enter_context(patch.object(G, 'BUNDLE_PATH', Path(tmp.name) / 'bundle.json'))
        self.addCleanup(self.join)
        S.candidates(self.root, NOW)
        self.join()

    def join(self):
        for states in (S.STATES, P.STATES):
            for state in list(states.values()):
                if state.get('worker'):
                    state['worker'].join(10)

    def listing(self, **query):
        return T.page_view(self.root, {'channel': ['interest'], 'range': ['7d'],
                                     **{k: [v] for k, v in query.items()}}, NOW)

    def test_source_tier_zero_keeps_same_match_audit_and_base_count(self):
        whole = self.listing()
        zero = self.listing(tier='爆款', platform='x')
        self.assertGreater(whole['total'], 0)
        self.assertEqual(zero['total'], 0)
        self.assertEqual(zero['filtering'], whole['filtering'])
        self.assertTrue(zero['topic_options'])
        self.assertIn('filter_context', zero, '旧版没有报因数据，应报F')
        context = zero['filter_context']
        self.assertEqual(context['range_total'], whole['total'])
        self.assertEqual(context['active_filters'], ['平台：X', '只看爆款'])
        self.assertEqual(context['source_tier'], '爆款')
        self.assertEqual(self.listing()['total'], whole['total'])

    def test_query_author_filters_do_not_shrink_reported_base(self):
        whole = self.listing()
        for query in ({'q': 'NO_MATCH_91827'}, {'author': 'NO_AUTHOR_91827'},
                      {'topic': 'custom:missing', 'source': 'L'},
                      {'collection_channel': 'github', 'platform': 'x'}):
            with self.subTest(query=query):
                result = self.listing(**query)
                self.assertEqual(result['total'], 0)
                self.assertIn('filter_context', result)
                self.assertEqual(result['filter_context']['range_total'], whole['total'])
                self.assertTrue(result['filter_context']['active_filters'])

    def test_search_text_is_data_and_sort_is_not_a_filter(self):
        value = '<script>alert(1)</script>'
        result = self.listing(q=value)
        self.assertIn('filter_context', result)
        self.assertEqual(result['filter_context']['active_filters'], ['搜索：' + value])
        self.assertEqual(self.listing(sort='time')['filter_context']['active_filters'], [])

    def test_persisted_readmodel_and_direct_path_use_same_base(self):
        P.publish(self.root, NOW)
        direct = self.listing(q='NO_MATCH_91827', tier='爆款', platform='x')
        with patch.object(P, 'schedule'):
            stored = T.page_view(self.root, {'channel': ['interest'], 'range': ['7d'],
                                           'q': ['NO_MATCH_91827'], 'tier': ['爆款'], 'platform': ['x']})
        self.assertEqual(stored['total'], direct['total'])
        self.assertEqual(stored['filter_context'], direct['filter_context'])

    def test_real_empty_without_filters_has_zero_base_and_no_filter_reason(self):
        future = NOW + timedelta(days=8)
        S.candidates(self.root, future)
        self.join()
        result = T.page_view(self.root, {'channel': ['interest'], 'range': ['today']}, future)
        self.assertEqual(result['total'], 0)
        self.assertIn('filter_context', result)
        self.assertEqual(result['filter_context']['range_total'], 0)
        self.assertEqual(result['filter_context']['active_filters'], [])


if __name__ == '__main__':
    unittest.main()
