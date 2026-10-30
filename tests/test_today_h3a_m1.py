"""H3a M1：真实误杀正例、真实无关反例，以及词边界/已有补充内容。"""
import copy
import json
from pathlib import Path
import unittest

from scripts.radar_common import today_relevance_gate

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/today_h3a_m1.json').read_text())


class RealPostExamples(unittest.TestCase):
    pass


def real_case(row):
    def test(self):
        self.assertEqual(today_relevance_gate(row)['visible'], row['expected'], row['id'] + ' ' + row['title'])
    return test


for example in FIXTURES:
    setattr(RealPostExamples, 'test_' + ('keep_' if example['expected'] else 'hide_') + example['id'].replace(':', '_'), real_case(example))


class RelevanceBoundaries(unittest.TestCase):
    def item(self, text, **extra):
        return dict(title=text, full_text=text, sources=['A'], topic='', **extra)

    def test_ds_requires_version_and_ascii_word_boundaries(self):
        for text in ('DS 4.1pro 新版本', 'ＤＳ　４．１ｐｒｏ', 'DeepSeek新版本', 'DS-V4.1 Flash'):
            with self.subTest(text=text):
                self.assertTrue(today_relevance_gate(self.item(text))['visible'])
        for text in ('DSL 4.1 协议', 'ads 4.1 campaign', 'ads4.1pro campaign', 'DS 只是名字', 'DS4.1protocol', 'myworkbuddytools', 'seedancedance', 'higgsfieldsports', 'Duquesne Capital 金融交易'):
            with self.subTest(text=text):
                self.assertFalse(today_relevance_gate(self.item(text))['visible'])

    def test_normalization_case_fullwidth_and_claude_confusable(self):
        for text in ('Ｃｌａｕｄｅ 设置中文', 'CIaude 设置中文', 'cIaUdE 设置中文', 'ＣＩａｕｄｅ 设置中文', 'ＷｏｒｋＢｕｄｄｙ 升级', 'CIaude3.5 设置中文'):
            with self.subTest(text=text):
                self.assertTrue(today_relevance_gate(self.item(text))['visible'])

    def test_lexical_model_versions_and_existing_plural_forms_preserved(self):
        # M2主控明确授权：gpt保持原子串语义，合成chatgptfan改为保留。
        for text in ('Seedance2.5 新版', 'GPT6 发布', 'DeepSeekV4.1 新版', 'ChatGPT 使用教程', 'Agents for desktop', 'APIs for apps', 'OAI 的鸡蛋，你们领了吗？', 'chatgptfan 日常'):
            with self.subTest(text=text):
                self.assertTrue(today_relevance_gate(self.item(text))['visible'])
        for text in ('seedance2sports 车赛', 'ciaudefan 用户', 'higgsfielded 游戏', 'oaisports 赛事'):
            with self.subTest(text=text):
                self.assertFalse(today_relevance_gate(self.item(text))['visible'])
        row = self.item('so freaking cute.')
        row['topic'] = 'AIGC视频图像'
        self.assertTrue(today_relevance_gate(row)['visible'])

    def test_product_and_plan_context_does_not_admit_plain_ambiguous_words(self):
        for text in ('Pro 用量翻倍', '额度不砍了 Plus', 'When will this come to Plus tier?', 'notebook 挂进知识库提问', 'Muse 10.0 更新', 'Muse现在可以自己解决问题'):
            with self.subTest(text=text):
                self.assertTrue(today_relevance_gate(self.item(text))['visible'])
        for text in ('Pro 手机真好看', 'Plus 车展', 'Notebook 笔记本外壳', 'Muse 演唱会门票', 'amusement park', 'thsottiauxfan 的日常', 'daily ads campaign'):
            with self.subTest(text=text):
                self.assertFalse(today_relevance_gate(self.item(text))['visible'])

    def test_rt_quotes_and_existing_link_card_titles_each_reach_gate(self):
        payloads = [
            {'retweeted_status': {'full_text': 'Seedance 2.5 unlimited'}},
            {'quoted': {'text': 'WorkBuddy 推出 Buddy One'}},
            {'card': {'title': 'Higgsfield 发布新版本'}},
            {'link_card': {'title': 'NotebookLM 新功能'}},
            {'retweeted_status_result': {'result': {'legacy': {'full_text': 'DS 4.1pro'}}}},
            {'quoted_status': {'full_text': 'CIaude 中文设置'}},
            {'link_preview': {'title': 'Codex usage limits'}},
        ]
        for value in payloads:
            with self.subTest(value=value):
                item = self.item('无语凝噎', rich_sources=[value])
                before = copy.deepcopy(item)
                self.assertTrue(today_relevance_gate(item)['visible'])
                self.assertEqual(item, before)
                # 删除真正的补充文本应立即测出错误，不能靠外层文字假通过。
                self.assertFalse(today_relevance_gate(self.item('无语凝噎', rich_sources=[]))['visible'])

    def test_blacklist_still_precedes_product_hits_and_only_grok_interest_exempt(self):
        for title in ('亲哥欠钱10w怎么办', '被骗的钱追回来了', '不限次数的 seedance2.0'):
            row = dict(title=title, full_text=title, sources=['L'], topic='福利羊毛' if 'seedance' in title else '搞七捻三')
            self.assertFalse(today_relevance_gate(row)['visible'])
            self.assertTrue(today_relevance_gate(row)['reason'].startswith('分类黑名单：'))
        row = self.item('书法家的字')
        self.assertFalse(today_relevance_gate(row, channel='interest')['visible'])
        self.assertTrue(today_relevance_gate(dict(row, sources=['G']), channel='interest')['visible'])
        self.assertFalse(today_relevance_gate(dict(row, sources=['G', 'A']), channel='interest')['visible'])

    def test_configurable_interest_terms_obey_boundaries(self):
        self.assertTrue(today_relevance_gate(self.item('Maya 新插件'), ['新工具（Maya）'])['visible'])
        self.assertFalse(today_relevance_gate(self.item('mayasports club'), ['新工具（Maya）'])['visible'])


if __name__ == '__main__':
    unittest.main()
