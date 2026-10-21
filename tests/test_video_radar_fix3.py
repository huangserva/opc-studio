"""C1-fix3：跳过字符、Unicode 字母数字与严格字面配置的行为回归。"""
import copy
import unittest
from test_video_radar import CONFIG, AT, load
import video_radar as v

SKIPS = (' ', '\t', '\n', '\r\n', '\u3000', '\u00a0', '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff')
LETTERS = ('的错', '取代人类', '2.0', 'のせい', 'です', '때문에', '아니다', 'é', 'Я', '𠀀', '²', 'Ⅳ', '١')


class Fix3Tests(unittest.TestCase):
    def test_skips_never_become_terminal_boundaries(self):
        for skip in SKIPS + (''.join(SKIPS),):
            for ending in LETTERS:
                with self.subTest(skip=repr(skip), ending=ending):
                    self.assertEqual(v.classify('这不是AI' + skip + ending, CONFIG)[0], 'relevant')
        for text in ('并非AI 取代人类', '不是AI 2.0', '这不是AI的错', '不是AI2.0', '不是AIGC'):
            self.assertEqual(v.classify(text, CONFIG)[0], 'relevant')
        self.assertEqual(v.classify('不是AIGC', CONFIG)[1], '工具或技术词：AIGC')

    def test_unicode_alphanumeric_including_non_bmp_is_protected(self):
        for ending in LETTERS:
            with self.subTest(ending=ending):
                self.assertTrue(ending[0].isalnum())
                self.assertEqual(v.classify('不是AI' + ending, CONFIG)[0], 'relevant')

    def test_all_unicode_whitespace_and_format_skips(self):
        # Python 的全部 Unicode 空白，不依赖一份手写白名单。
        whitespace = [chr(i) for i in range(0x110000) if chr(i).isspace()]
        for char in whitespace + list('\u200b\u200c\u200d\u2060\ufeff'):
            with self.subTest(codepoint=hex(ord(char))):
                self.assertEqual(v.classify('不是AI'+char+'的错', CONFIG)[0], 'relevant')
                self.assertIsNone(v.classify('不是AI'+char, CONFIG)[0])
                self.assertIsNone(v.classify('不是AI'+char+'）', CONFIG)[0])

    def test_skips_then_symbols_or_end_are_denied(self):
        for skip in SKIPS + (''.join(SKIPS),):
            for tail in ('', '）', '…', '#风景', '😂', '_', '\u0301'):
                with self.subTest(skip=repr(skip), tail=tail):
                    self.assertIsNone(v.classify('城市风光，不是AI'+skip+tail, CONFIG)[0])

    def test_positive_semantics_outside_multiple_negations_survive(self):
        for text in ('不是AI ）不是AI … AI教程', '不是AI\u200b）#AI创作大赛', '不是AI\u2060。Sora对比', '不是AI生成，是Sora实拍对比'):
            self.assertEqual(v.classify(text, CONFIG)[0], 'relevant')
        self.assertIsNone(v.classify('不是AI\u200b）不是AI\u2060…', CONFIG)[0])

    def test_end_only_mode_and_literal_extensions(self):
        cfg=copy.deepcopy(CONFIG);cfg['negative_ai_boundary']={'mode':'end_only'}
        for skip in SKIPS:
            self.assertIsNone(v.classify('不是AI'+skip,cfg)[0])
            self.assertEqual(v.classify('不是AI'+skip+'）',cfg)[0],'relevant')
            self.assertEqual(v.classify('不是AI'+skip+'的错',cfg)[0],'relevant')
        cfg['negative_ai_boundary']={'extra_word_chars':[']','-','^','\\','，']}
        for char in cfg['negative_ai_boundary']['extra_word_chars']:
            self.assertEqual(v.classify('不是AI'+char,cfg)[0],'relevant')
            self.assertIsNone(v.classify('不是AI'+char,CONFIG)[0])
        self.assertIsNone(v.classify('不是AI）',cfg)[0])
        del cfg['negative_ai_boundary']
        self.assertIsNone(v.classify('不是AI）',cfg)[0])

    def test_invalid_configuration_raises_even_without_negation(self):
        invalid = [None, 3, [], '', False]
        invalid += [{'mode':x} for x in ('TYPO',None,3,False,[],{})]
        invalid += [{'word_chars':x} for x in ('0-9]', '', None,False,3,'z-a')]
        invalid += [{'extra_word_chars':x} for x in (None,False,3,'，',{},[''],['ab'],[3],[None],[' '],['\t'],['\u3000'],['\u00a0'],['\u200b'],['\ufeff'])]
        invalid += [{'unknown':[]}, {'mode':'end_only','unknown':3}, {'mode':'end_only','extra_word_chars':[' ']}]
        for boundary in invalid:
            cfg=copy.deepcopy(CONFIG);cfg['negative_ai_boundary']=boundary
            for text in ('城市风光（不是AI）', '普通风景'):
                with self.subTest(boundary=boundary, text=text):
                    with self.assertRaises(ValueError):v.classify(text,cfg)

    def test_normalize_and_cached_reclassification_use_same_boundary(self):
        raw=load('dy_board_video_1h_p1')['data']['data']['objs'][0]
        for skip in SKIPS:
            raw['item_title']='这不是AI'+skip+'的错'
            normalized=v.normalize(raw,'dy_board_video_1h',AT,CONFIG)
            self.assertIsNotNone(normalized)
            self.assertEqual(v.reclassify_cached(normalized,CONFIG)['relevance'],'relevant')
            raw['item_title']='城市风光（不是AI'+skip+'）'
            self.assertIsNone(v.normalize(raw,'dy_board_video_1h',AT,CONFIG))
            normalized.update(title=raw['item_title'],relevance_reason='独立AI词')
            self.assertIsNone(v.reclassify_cached(normalized,CONFIG))


if __name__=='__main__':unittest.main()
