"""协调者批准的否定语义收窄：只排除明确否定AI制作且无技术词的文案。"""
import contextlib
import copy
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from test_video_radar import CONFIG, AT, FixtureClient, item
import video_radar as v
from video_radar_relevance import classify


class NegationTests(unittest.TestCase):
    def test_required_positive_counterexamples(self):
        for text in ('AI不是万能的','并非AI取代人类','没有AI的时代怎么活','无AI不创业','这不是AI生成，是Sora实拍对比'):
            with self.subTest(text=text):self.assertEqual(classify(text,CONFIG)[0],'relevant')

    def test_negative_needs_production_suffix_or_real_capture(self):
        for text in ('本视频并非AI生成','专业安全实拍无AI，请勿模仿','非AI画面','未用AI参与','不是 AI 合成','没有AI痕迹，真实拍摄','不是AI','并非AI'):
            with self.subTest(text=text):self.assertEqual(classify(text,CONFIG),(None,'否定AI制作且无技术语义'))
        for text in ('没有AI我怎么办','AI并非人类','无AI不创业'):
            with self.subTest(text=text):self.assertEqual(classify(text,CONFIG)[0],'relevant')

    def test_other_technical_terms_override_negative_context(self):
        for text in ('这不是AI生成，是Sora实拍对比','无AI画面，讲讲大模型','并非AI生成，ChatGPT只是写了提示词','未用AI制作，智能体负责整理','非AI画面 #AIGC'):
            with self.subTest(text=text):self.assertEqual(classify(text,CONFIG)[0],'relevant')

    def test_negative_patterns_are_config_driven(self):
        cfg=copy.deepcopy(CONFIG);cfg['negative_ai_prefixes']=[]
        self.assertEqual(classify('并非AI生成',cfg)[0],'relevant')

    def test_old_state_denial_removed_without_losing_description_only_technical_term(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);storage=root/'workspace/video-radar';storage.mkdir(parents=True)
            bad=item();bad.update(key='douyin:denial',id='denial',platform='douyin',title='专业实拍非AI画面',relevance_reason='独立AI词',sources=['dy_board_video_1h'])
            good=copy.deepcopy(bad);good.update(key='douyin:preserve',id='preserve',relevance_reason='工具或技术词：Sora')
            v.atomic_json(storage/'state.json',{'items':{bad['key']:bad,good['key']:good},'daily':{}})
            FixtureClient.fail,FixtureClient.requests,FixtureClient.at=set(),[],AT
            with mock.patch.object(v,'now',return_value=AT),contextlib.redirect_stdout(io.StringIO()):v.run(root,'hourly',FixtureClient)
            state=json.loads((storage/'state.json').read_text())['items']
            self.assertNotIn(bad['key'],state);self.assertIn(good['key'],state)


if __name__=='__main__':unittest.main()
