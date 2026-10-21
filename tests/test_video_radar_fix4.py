"""C1-fix4：连续否定片段不能残留为独立正向AI词。"""
import copy
import unittest
from test_video_radar import CONFIG, AT, load
from test_video_radar_fix3 import SKIPS
import video_radar as v

MUST_DROP = (
    '城市风光 不是AI 不是AI',
    '城市风光 不是AI\n并非AI',
    '城市风光（不是AI 不是AI）',
    '城市风光，不是AI 不是AI！',
    '城市风光 不是AI\u200b不是AI',
    '城市风光 不是AI\t未用AI',
    '城市风光 不是AI不是AI',
    '城市风光 并非AI 不是AI 未用AI',
)
CONTROLS = (
    ('城市风光（不是AI；不是AI）',None),
    ('城市风光，不是AI；不是AI！',None),
    ('城市风光，不是AI。不是AI',None),
    ('不是AI 不是AI。AI教程','relevant'),
    ('不是AI 不是AI；Sora对比','relevant'),
    ('不是AI 未用AI生成',None),
    ('这不是AI 不是AI的错','relevant'),
)


class Fix4Tests(unittest.TestCase):
    def test_required_multiple_negations_are_dropped(self):
        for text in MUST_DROP:
            with self.subTest(text=text):
                self.assertEqual(v.classify(text,CONFIG),(None,'否定AI制作且无技术语义'))
                raw=load('dy_board_video_1h_p1')['data']['data']['objs'][0];raw['item_title']=text
                self.assertIsNone(v.normalize(raw,'dy_board_video_1h',AT,CONFIG))

    def test_controls_and_mixed_discussion_keep_original_results(self):
        for text,expected in CONTROLS:
            with self.subTest(text=text):self.assertEqual(v.classify(text,CONFIG)[0],expected)
        for text in ('这不是AI 的错','这不是AI的错','不是AI 不是AI。#AI创作大赛','不是AI 不是AI。大模型教程'):
            self.assertEqual(v.classify(text,CONFIG)[0],'relevant')

    def test_same_configured_prefix_rule_applies_to_all_separators(self):
        for sep in ('',)+SKIPS+(''.join(SKIPS),):
            for prefix in CONFIG['negative_ai_prefixes']:
                with self.subTest(sep=repr(sep),prefix=prefix):
                    self.assertIsNone(v.classify('风景 不是AI'+sep+prefix+'AI',CONFIG)[0])
        cfg=copy.deepcopy(CONFIG);cfg['negative_ai_prefixes']=['未借助']
        self.assertIsNone(v.classify('未借助AI\u200b未借助AI',cfg)[0])
        self.assertEqual(v.classify('不是AI 不是AI',cfg)[0],'relevant')
        # 自定义前缀逐字面匹配，不赋予正则元字符特殊含义。
        cfg['negative_ai_prefixes']=['[未用]']
        self.assertIsNone(v.classify('[未用]AI [未用]AI',cfg)[0])

    def test_next_fragment_uses_same_ai_word_boundary(self):
        for suffix in ('不是AIGC','不是AIR','不是AI的错','不是AIのせい'):
            with self.subTest(suffix=suffix):self.assertEqual(v.classify('不是AI '+suffix,CONFIG)[0],'relevant')
        self.assertEqual(v.classify('不是AI 不是AIGC',CONFIG)[1],'工具或技术词：AIGC')

    def test_end_only_mode_and_extensions_do_not_block_fragment_detection(self):
        cfg=copy.deepcopy(CONFIG);cfg['negative_ai_boundary']={'mode':'end_only','extra_word_chars':['不']}
        for sep in ('', ' ', '\u200b'):
            self.assertIsNone(v.classify('不是AI'+sep+'不是AI',cfg)[0])
        self.assertEqual(v.classify('不是AI 不是AI）',cfg)[0],'relevant')
        self.assertEqual(v.classify('这不是AI 的错',cfg)[0],'relevant')

    def test_removal_has_no_denied_ai_left_and_long_chain_is_iterative(self):
        from video_radar_relevance import _remove_negations,_boundary_config
        import re
        pattern='(?:'+'|'.join(re.escape(p) for p in CONFIG['negative_ai_prefixes'])+r')\s*ai(?![a-zA-Z])'
        text='城市风光（不是AI 不是AI）'
        remaining,count=_remove_negations(text,pattern,CONFIG['negative_ai_production_terms'],CONFIG['negative_ai_real_capture_terms'],_boundary_config(CONFIG))
        self.assertEqual(count,2);self.assertNotIn('AI',remaining)
        self.assertEqual(v.classify(('不是AI\u200b'*10000)+'不是AI',CONFIG),(None,'否定AI制作且无技术语义'))


if __name__=='__main__':unittest.main()
