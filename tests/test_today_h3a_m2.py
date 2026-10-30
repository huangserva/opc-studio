"""M2：跨日真阳性与原始子串超集；所有输入均来自只读快照或明确合成反例。"""
import json
from pathlib import Path
import unittest

from scripts import radar_common as common

ROWS = json.loads((Path(__file__).parent / 'fixtures/today_h3a_m2.json').read_text())


def item(text):
    return dict(title=text, full_text=text, sources=['A'], topic='')


class CrossDayPositiveExamples(unittest.TestCase):
    pass


def positive_case(row):
    def test(self):
        self.assertTrue(common.today_relevance_gate(row)['visible'], row['id'])
    return test


for row in ROWS:
    setattr(CrossDayPositiveExamples, 'test_restore_' + row['id'].replace(':', '_'), positive_case(row))


class LegacySupersetAndInputBoundaries(unittest.TestCase):
    def test_original_keywords_partition_and_safe_substrings(self):
        from today_filter_config import TODAY_AI_SUBSTRING_TERMS, TODAY_AI_BOUNDARY_TERMS
        self.assertEqual(set(common.AI_KEYWORDS), set(TODAY_AI_SUBSTRING_TERMS) | set(TODAY_AI_BOUNDARY_TERMS))
        self.assertFalse(set(TODAY_AI_SUBSTRING_TERMS) & set(TODAY_AI_BOUNDARY_TERMS))
        for word in TODAY_AI_SUBSTRING_TERMS:
            text = 'prefix' + word + 'suffix'
            with self.subTest(word=word):
                self.assertTrue(common.is_ai_related(text))
                self.assertTrue(common.today_relevance_gate(item(text))['visible'])

    def test_compounds_and_chinese_intelligence(self):
        for text in ['GenAI 创作', 'GrokBot 长任务', 'Grokbot 状态回调', 'xAI 超算',
                     'agentic software development', '马斯克讨论超级智能', '具身智能落地']:
            with self.subTest(text=text):
                self.assertTrue(common.today_relevance_gate(item(text))['visible'])

    def test_case_positive_and_negative(self):
        self.assertTrue(common.today_relevance_gate(item('gRoKbOt 长任务'))['visible'])
        self.assertFalse(common.today_relevance_gate(item('Duquesne CAPital 金融交易'))['visible'])

    def test_fullwidth_positive_and_negative(self):
        self.assertTrue(common.today_relevance_gate(item('ＧｅｎＡＩ 创作'))['visible'])
        self.assertFalse(common.today_relevance_gate(item('ＤＳＬ 网络协议'))['visible'])

    def test_confusable_positive_and_negative(self):
        self.assertTrue(common.today_relevance_gate(item('ＣＩａｕｄｅ 设置中文'))['visible'])
        self.assertFalse(common.today_relevance_gate(item('ＣＩａｕｄｅｆａｎ 烹饪日记'))['visible'])

    def test_url_random_substring_positive_and_negative(self):
        self.assertTrue(common.today_relevance_gate(item('GenAI 作品 https://t.co/AbAIgpt'))['visible'])
        for text in ['旅行 https://t.co/AbAIgpt', '旅行 ｈｔｔｐｓ：／／ｔ．ｃｏ／ＡｂＡＩｇｐｔ']:
            with self.subTest(text=text):
                self.assertFalse(common.today_relevance_gate(item(text))['visible'])

    def test_inside_word_positive_and_evidence_based_negative(self):
        self.assertTrue(common.today_relevance_gate(item('GenAI 作品'))['visible'])
        self.assertFalse(common.today_relevance_gate(item('Duquesne Capital 金融交易'))['visible'])
        # 定点去除词不能否决整条：同一正文明确提到AI时仍应保留。
        self.assertTrue(common.today_relevance_gate(item('Capital 投资 GenAI'))['visible'])

    def test_short_terms_independent_uppercase_compounds_and_lowercase_noise(self):
        for text in ['AI工具', 'API调用', 'MCP服务', 'LLM推理', 'repo代码',
                     'GenAI', 'xAI', 'OpenAI', 'AIGC', 'LLMs', 'MCPs',
                     'genai', 'xai', 'openai', 'aigc', 'MCPServer']:
            with self.subTest(text=text):
                self.assertTrue(common.today_relevance_gate(item(text))['visible'])
        for text in ['daily', 'campaign', 'said', 'email', 'capital', 'report', 'sculptingmcpiece', 'bellmore']:
            with self.subTest(text=text):
                self.assertFalse(common.today_relevance_gate(item(text))['visible'])

    def test_review_five_examples_under_coordinator_superset_amendment(self):
        for text in ['ads 投放报告', 'DSL 4.1 网络配置', 'Pro 手机购物清单',
                     'amusement park 欢乐旅行']:
            with self.subTest(text=text):
                self.assertFalse(common.today_relevance_gate(item(text))['visible'])
        # 主控M2补充明确授权：claude为子串安全类，此合成样本改为保留。
        self.assertTrue(common.today_relevance_gate(item('Claudette 的烹饪日记'))['visible'])

    def test_ambiguous_context_does_not_accept_explicit_carrier_or_concert(self):
        for text in ['Pro 流量套餐用量翻倍', 'Muse 10.0 演唱会门票']:
            with self.subTest(text=text):
                self.assertFalse(common.today_relevance_gate(item(text))['visible'])
        for text in ['Pro 用量翻倍', 'Muse 10.0 更新', 'Muse 演唱会宣传图用 GenAI 制作']:
            with self.subTest(text=text):
                self.assertTrue(common.today_relevance_gate(item(text))['visible'])

    def test_short_word_audit_ai_context_does_not_whitelist_noise_words(self):
        for text in ['Muse even created a playlist, playing it on Spotify from my airtight homelab.',
                     '网络管家 Bot 登录后台，直接登录并从 Gmail 拿到验证码，不用自己操作。',
                     '和 Bot 一起做成果页，聊天里提要求，页面里看成果。', '最懂aigc创意']:
            with self.subTest(text=text):
                self.assertTrue(common.today_relevance_gate(item(text))['visible'])
        for text in ['airtight box', 'Gmail 邮箱通知', '用户 0xlangeai',
                     'Muse created a playlist for concert tickets', 'Bot 玩具打折']:
            with self.subTest(text=text):
                self.assertFalse(common.today_relevance_gate(item(text))['visible'])

    def test_replay_vibe_coder_real_post_not_kept_only_by_url(self):
        self.assertTrue(common.today_relevance_gate(item('【信息差】Vibe Coder们，分享一个小红书新活动机会。'))['visible'])
        self.assertFalse(common.today_relevance_gate(item('Vibe 车展日常'))['visible'])


if __name__ == '__main__':
    unittest.main()
