"""黄总C1-fix1口径：活动即相关、泛标签弱、句尾否定排除且保留AI议题。"""
import copy
import json
from pathlib import Path
import unittest
from test_video_radar import CONFIG, item
import video_radar as v


class Fix1Tests(unittest.TestCase):
    def test_all_ten_required_guards_remain_relevant(self):
        for text in ('AI不是万能的','并非AI取代人类','没有AI的时代怎么活','无AI不创业',
                     '这不是AI生成，是Sora实拍对比','不是AI，而是ChatGPT的界限问题',
                     '不是AI。AI教程在这里','不是AI替代人，而是人用AI','这不是AI的错',
                     '马斯克再痛批唱跳机器人！动作再炫也不是AI，只是提前背剧本 #AI #人工智能'):
            with self.subTest(text=text):self.assertEqual(v.classify(text,CONFIG)[0],'relevant')

    def test_negative_controls_and_ambiguous_self_blame(self):
        for text in ('特斯拉 跑车，真的会飞！不是AI','特斯拉Roadster真的会飞！非AI画面！',
                     '专业安全实拍无AI','不是AI，是我的错','不是AI。','不是ai!'):
            with self.subTest(text=text):self.assertIsNone(v.classify(text,CONFIG)[0])

    def test_every_configured_creation_activity_relevant_exact_reason(self):
        for pattern in CONFIG['ai_creation_activity_patterns']:
            with self.subTest(pattern=pattern):
                self.assertEqual(v.classify('剧情正文 #'+pattern,CONFIG),('relevant','AI创作活动标签'))
        cfg=copy.deepcopy(CONFIG);cfg['ai_creation_activity_patterns']=[]
        self.assertNotEqual(v.classify('剧情正文 #AI创作浪潮计划',cfg)[1],'AI创作活动标签')

    def test_generic_hashtag_weak_semantic_ai_relevant(self):
        for text in ('去找他别找我 #cortis#赵雨凡#james#ai','#AI','普通剧情 #Ai #宠物'):
            with self.subTest(text=text):self.assertEqual(v.classify(text,CONFIG),('weak','仅泛AI标签'))
        for text in ('我用AI时的样子 #AI','#AI生成','#AI短片','不是AI。AI教程在这里'):
            with self.subTest(text=text):self.assertEqual(v.classify(text,CONFIG)[0],'relevant')

    def test_latin_word_boundary_regressions(self):
        for text in ('Aijing','dazai','Thai','said','AIR','Mai','chair','email'):
            with self.subTest(text=text):self.assertIsNone(v.classify(text,CONFIG)[0])

    def test_cached_reclassification_never_changes_metrics_or_times(self):
        original=item();original.update(title='剧情 #即梦AI创作者成长计划',relevance='weak',relevance_reason='仅活动或运营标签')
        result=v.reclassify_cached(original,CONFIG)
        self.assertEqual(result['relevance'],'relevant');self.assertEqual(result['relevance_reason'],'AI创作活动标签')
        for field in original.keys()-{'relevance','relevance_reason'}:self.assertEqual(original[field],result[field])
        self.assertEqual(original['relevance'],'weak')
        original.update(title='特斯拉真的会飞！不是AI',relevance='relevant',relevance_reason='独立AI词')
        self.assertIsNone(v.reclassify_cached(original,CONFIG))
        original['relevance_reason']='工具或技术词：Sora'
        self.assertIsNotNone(v.reclassify_cached(original,CONFIG))

    def test_terminal_branch_is_config_driven(self):
        # fix2：否定分支的「AI 之后可以跟什么」改由 negative_ai_boundary 驱动
        # （mode + extra_word_chars），不再枚举终止标点。两条原有断言保留，另加两条更严的。
        cfg=copy.deepcopy(CONFIG);cfg['negative_ai_boundary']={'mode':'end_only'}
        self.assertEqual(v.classify('不是AI，是我的错',cfg)[0],'relevant')
        self.assertIsNone(v.classify('不是AI，是我的错',CONFIG)[0])
        # extra_word_chars 也驱动：把中文逗号算作词字符，否定分支同样不命中。
        cfg2=copy.deepcopy(CONFIG)
        cfg2['negative_ai_boundary']={'mode':'end_or_non_word','extra_word_chars':['，']}
        self.assertEqual(v.classify('不是AI，是我的错',cfg2)[0],'relevant')
        self.assertIsNone(v.classify('不是AI，是我的错',CONFIG)[0])
        # 旧键不再被读取：即使把老字段塞成空，行为也不变（防止两套口径并存）。
        cfg3=copy.deepcopy(CONFIG);cfg3['negative_ai_terminal_chars']=''
        self.assertIsNone(v.classify('不是AI，是我的错',cfg3)[0])


if __name__=='__main__':unittest.main()
