"""C1-fix2：否定AI后接闭括号/引号/省略号/破折号/#/emoji 等一律丢弃。

黄总口径（coordination/decision-video-viral-monitor.md「相关性口径」）：
明确声明「不是AI / 非AI画面 / 实拍无AI」的文案算不相关。fix1 只枚举了
`，。！？；、,.!?;：` 少数终止标点，闭括号、引号、省略号、破折号漏判；
fix2 改成通用规则：否定前缀+AI 之后是文本结尾，或任何不是字母/数字/汉字的字符。
"""
import copy
import json
from pathlib import Path
import unittest

from test_video_radar import CONFIG
from video_radar_relevance import classify

# 复验报告 C1-fix1 的 9 条反例 + 合同追加的 3 条 + 全角空格结尾
MUST_DROP = (
    '城市风光（不是AI）',
    '城市风光(不是AI)',
    '城市风光「不是AI」',
    '城市风光【不是AI】',
    '城市风光[不是AI]',
    '城市风光“不是AI”',
    '城市风光"不是AI"',
    '城市风光，不是AI…',
    '城市风光，不是AI—',
    '城市风光 不是AI #风景',
    '城市风光，不是AI😂',
    '城市风光，并非AI》',
    '城市风光（不是AI）　',
)

# 紧跟汉字/字母/数字时本规则不命中，交给原有分支（这些必须与 fix1 一致）
UNCHANGED = (
    ('这不是AI的错', 'relevant'),
    ('不是AI教程', 'relevant'),
    ('不是AI2', 'relevant'),
    ('不是AI生成', None),
    ('不是AI的错，是AI的责任', 'relevant'),
    ('没有AI我怎么办', 'relevant'),
    ('AI并非人类', 'relevant'),
    ('Aijing', None),
    ('dazai', None),
    ('Thai', None),
    ('said', None),
    ('AIR', None),
    ('Mai', None),
    ('chair', None),
    ('email', None),
)

# 否定片段之外还有独立正向语义 → 必须保留
KEEP = (
    '不是AI。AI教程在这里',
    '不是AI，而是ChatGPT的界限问题',
    '非AI画面，但后半段AI画面教学',
    '不是AI替代人，而是人用AI',
    '马斯克再痛批唱跳机器人！动作再炫也不是AI，只是提前背剧本 #AI #人工智能',
    '城市风光（不是AI），但这期用Sora做了对比',
    '城市风光（不是AI）#AI短片',
    '城市风光（不是AI）大模型教程',
)


class NegationBoundaryTests(unittest.TestCase):
    def test_closing_punctuation_and_symbols_are_dropped(self):
        for text in MUST_DROP:
            with self.subTest(text=text):
                self.assertEqual(classify(text, CONFIG), (None, '否定AI制作且无技术语义'))

    def test_every_non_word_character_is_covered_by_the_rule(self):
        """逐字符扫一遍常见收尾符号，全部走否定分支（证明不是逐个补名单）。"""
        chars = '）)」』】]｝}…—～~、。·・!?,.:;|#@$%^*+=/\\|<>"\'`'
        for char in chars:
            text = '城市风光（不是AI' + char
            with self.subTest(char=char):
                self.assertIsNone(classify(text, CONFIG)[0], char)
        # emoji（代理对）与零宽字符同样不是词字符
        for text in ('城市风光（不是AI😂', '城市风光（不是AI👨‍👩‍👧', '城市风光（不是AI'):
            with self.subTest(text=text):
                self.assertIsNone(classify(text, CONFIG)[0])

    def test_word_characters_do_not_trigger_the_rule(self):
        for text, expected in UNCHANGED:
            with self.subTest(text=text):
                self.assertEqual(classify(text, CONFIG)[0], expected)

    def test_independent_positive_semantics_outside_negation_still_kept(self):
        for text in KEEP:
            with self.subTest(text=text):
                self.assertEqual(classify(text, CONFIG)[0], 'relevant', text)

    def test_boundary_is_config_driven_not_a_list(self):
        cfg = copy.deepcopy(CONFIG)
        self.assertEqual(cfg['negative_ai_boundary']['mode'], 'end_or_non_word')
        self.assertEqual(cfg['negative_ai_boundary']['extra_word_chars'], [])
        # mode=end_only：只认文本结尾 → 括号型否定不再命中
        cfg['negative_ai_boundary'] = {'mode': 'end_only'}
        self.assertEqual(classify('城市风光（不是AI）', cfg)[0], 'relevant')
        # end_only 下，真正的句尾否定仍然命中
        self.assertIsNone(classify('特斯拉 跑车，真的会飞！不是AI', cfg)[0])
        # 扩展逐字面字符：把闭括号视为词字符，保留原配置改变边界的语义。
        cfg2 = copy.deepcopy(CONFIG)
        cfg2['negative_ai_boundary'] = {'mode': 'end_or_non_word', 'extra_word_chars': ['）']}
        self.assertEqual(classify('城市风光（不是AI）', cfg2)[0], 'relevant')
        # 未知 mode 必须报错，不静默退化
        cfg3 = copy.deepcopy(CONFIG)
        cfg3['negative_ai_boundary'] = {'mode': 'typo_mode'}
        with self.assertRaises(ValueError):
            classify('城市风光（不是AI）', cfg3)
        cfg4 = copy.deepcopy(CONFIG)
        cfg4['negative_ai_boundary'] = {'mode': 'end_or_non_word', 'word_chars': ''}
        with self.assertRaises(ValueError):
            classify('城市风光（不是AI）', cfg4)
        # 缺配置项时用内置默认，行为与随配置一致
        cfg5 = copy.deepcopy(CONFIG)
        del cfg5['negative_ai_boundary']
        self.assertIsNone(classify('城市风光（不是AI）', cfg5)[0])

    def test_no_id_or_title_whitelist_in_source(self):
        source = Path('scripts/video_radar_config.json').read_text(encoding='utf-8')
        self.assertNotIn('城市风光', source)
        self.assertNotIn('douyin:', source)
        self.assertNotIn('xiaohongshu:', source)
        # 旧枚举字段已下线，配置里只留通用边界
        self.assertNotIn('negative_ai_terminal_chars', source)
        self.assertNotIn('，。！？', json.dumps(CONFIG, ensure_ascii=False))

    def test_reclassify_cached_drops_the_new_shapes(self):
        """缓存重分类走同一套规则：新形状的旧「相关」条目会被撤掉。"""
        import video_radar as v
        original = {'key': 'douyin:paren', 'platform': 'douyin', 'id': 'paren',
                    'title': '城市风光（不是AI）', 'relevance': 'relevant',
                    'relevance_reason': '独立AI词'}
        self.assertIsNone(v.reclassify_cached(original, CONFIG))
        original['relevance_reason'] = '工具或技术词：Sora'
        self.assertIsNotNone(v.reclassify_cached(original, CONFIG))
        self.assertEqual(v.reclassify_cached(original, CONFIG)['relevance_reason'], '工具或技术词：Sora')


if __name__ == '__main__':
    unittest.main()