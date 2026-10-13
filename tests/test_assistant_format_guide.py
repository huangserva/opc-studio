"""创作助手「格式说明书」BASE 段 + 保存回读的回归测试。

依据 coordination/prep-assistant-format-guide.md §1（BASE 逐字稿）、§2（保存回读）、
§3（测试清单）。STYLE 段（样式积木、format_text）属 fixes-rich-style，本文件不覆盖。

BASE 期可用的只有 §1.8 的 ①②③⑦ 四类：加粗、改标题层级、合并列表、通篇重排；
④⑤⑥（居中/标红/缩进）依赖样式积木，此时的正确行为是**助手直说目前不支持**。
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from assistant_policy import policy_for, READ_TOOLS

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('studio', ROOT / 'server.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)


class CleanReportsTests(unittest.TestCase):
    """清洗层必须如实报出被丢掉的格式（prep §2.2）。"""

    def test_style_attribute_is_reported_not_silently_dropped(self):
        # M2：能映射的内联 style 会被规范化成积木，属「已生效」，不得谎报未生效。
        rich, _, dropped = studio.clean_html('<p style="font-size:24px"><strong>粗</strong></p>')
        self.assertIn('<p data-size="l"><strong>粗</strong></p>', rich, '能保留的格式必须保留并规范化')
        self.assertEqual(dropped, [], '规范化成功的内联 style 不算未生效')
        # 映射不了的 style 仍要拦住并如实上报（保留门禁），hint 要带具体原因。
        rich, _, dropped = studio.clean_html('<p style="position:fixed"><strong>粗</strong></p>')
        self.assertIn('<strong>粗</strong>', rich, '能保留的格式必须保留')
        reasons = {item['reason'] for item in dropped}
        self.assertIn('attribute_not_allowed', reasons)
        entry = next(item for item in dropped if item['reason'] == 'attribute_not_allowed')
        self.assertEqual(entry['requested'], 'style="position:fixed"')
        self.assertIn('position', entry['hint'], 'hint 要带具体没生效的原因')
        self.assertIn('strong', entry['hint'], 'hint 要指向能支持的写法')

    def test_disallowed_tag_and_bad_href_are_reported(self):
        _, _, dropped = studio.clean_html('<p><mark data-hl="purple">高亮</mark><a href="javascript:x()">链</a></p>')
        reasons = {item['reason'] for item in dropped}
        self.assertEqual(reasons, {'tag_not_allowed', 'href_rejected'})
        self.assertTrue(all(item['hint'] for item in dropped), '每条 dropped 都要有 hint')

    def test_script_is_blocked_and_reported(self):
        rich, _, dropped = studio.clean_html('<p>正文</p><script>alert(1)</script>')
        self.assertNotIn('script', rich)
        self.assertIn('tag_blocked', {item['reason'] for item in dropped})

    def test_clean_text_reports_nothing(self):
        rich, plain, dropped = studio.clean_html('<p>这是一段普通正文。</p>')
        self.assertEqual((rich, plain), ('<p>这是一段普通正文。</p>', '这是一段普通正文。'))
        self.assertEqual(dropped, [])

    def test_applied_only_lists_user_perceivable_formats(self):
        self.assertEqual(studio.html_formats('<p>普通段落</p>'), [], 'p 是结构，不进 applied')
        self.assertEqual(studio.html_formats('<h2>标题</h2><ul><li>项</li></ul><p><strong>粗</strong></p>'),
                         ['bold', 'h2', 'list'])
        self.assertEqual(studio.html_formats('<a href="https://x">链</a><em>斜</em><s>删</s>'),
                         ['italic', 'strike', 'link'])

    def test_save_receipt_carries_applied_and_dropped(self):
        store = studio.Store(tempfile.mkdtemp())
        slug = store.create({'title': '回执', 'type': 'article'})['slug']
        result = store.save(slug, {'base_version': 'v0001', 'title': '回执', 'blocks': [
            {'kind': 'text', 'html': '<p style="color:red"><strong>关键结论</strong>：<mark>标红</mark></p>'}]})
        fmt = result['format']
        self.assertIn({'index': 0, 'kind': 'bold'}, fmt['applied'])
        self.assertEqual({item['reason'] for item in fmt['dropped']},
                         {'attribute_not_allowed', 'tag_not_allowed'})
        self.assertEqual(fmt['truncated'], False)
        # 非法 href 也要能报出来
        again = store.save(slug, {'base_version': result['version'], 'title': '回执', 'blocks': [
            {'index': 0}, {'kind': 'text', 'html': '<p><a href="javascript:alert(1)">点我</a></p>'}]})
        self.assertIn('href_rejected', {item['reason'] for item in again['format']['dropped']})


class GuideWordingTests(unittest.TestCase):
    """说明书只讲现在真能做的事：prompt 与工具描述逐字对齐。"""

    @classmethod
    def setUpClass(cls):
        import assistant_agent
        cls.agent = assistant_agent
        cls.tools = {tool['function']['name']: tool['function']['description'] for tool in assistant_agent.TOOLS}
        cls.tools_spec = assistant_agent.TOOLS

    def test_system_prompt_lists_exactly_the_current_whitelist(self):
        system = self.agent.SYSTEM
        for tag in ('p', 'h1', 'h2', 'h3', 'strong', 'em', 'u', 's', 'br', 'blockquote', 'pre', 'code', 'a', 'ul', 'ol', 'li'):
            self.assertIn(tag, system, '白名单标签没写进系统提示：' + tag)
        self.assertIn('加粗一律用 strong', system)
        self.assertIn('斜体一律用 em', system)
        self.assertIn('不要用 b、i、del', system)

    def test_system_prompt_states_what_is_not_supported(self):
        system = self.agent.SYSTEM
        for phrase in ('任意字号 px', '任意颜色值', '不能新建表格', 'X 短推和串文只带纯文本'):
            self.assertIn(phrase, system, '不支持项必须写明：' + phrase)
        self.assertIn('不要用全角空格冒充首行缩进', system)
        self.assertIn('用破折号冒充分隔线', system)
        self.assertIn('分隔线用 <hr>，会随正文存下来', system, 'B 的 M-1 修好后这句必须说真话')
        self.assertIn('用 h1 冒充大字号', system)

    def test_system_prompt_requires_honest_reporting_of_dropped(self):
        system = self.agent.SYSTEM
        self.assertIn('保存回执会返回 format.applied 和 format.dropped', system)
        self.assertIn('dropped 非空时必须照实告诉用户', system)
        self.assertIn('只有 dropped 为空才可以按用户原话逐条确认', system)

    def test_system_prompt_points_the_model_at_format_text(self):
        # M3：光有 applied/dropped 还不够，模型会猜错；提示要它照 format_text 说。
        system = self.agent.SYSTEM
        self.assertIn('format_text', system)
        self.assertIn('已生效格式', system)
        self.assertIn('未生效格式', system)
        self.assertIn('不要自己推断', system)

    def test_update_text_description_is_the_base_verbatim(self):
        text = self.tools['update_text']
        for phrase in ('edits[].html 是这一整块的新 HTML', '加粗用 strong 不用 b', '斜体用 em 不用 i',
                       '标题用 h1–h3', '不要写 style、class、id、target、任意颜色或 px',
                       'edits 只提交要改的块，每项给 index 和这一整块的完整 html'):
            self.assertIn(phrase, text, phrase)
        # 复核 A-M2：schema 的 required 是 ['index','html']，说明里让模型
        # 交 {'index': n} 原样引用项，执行层直接取 edit['html'] 会 KeyError。
        self.assertNotIn("其余块用 {'index': n} 原样引用", text)
        self.assertIn('没改的块不要提交', text)
        self.assertIn('后端会原样保留未提交的块', text)
        schema = next(t for t in self.tools_spec if t['function']['name'] == 'update_text')
        self.assertEqual(schema['function']['parameters']['properties']['edits']['items']['required'],
                         ['index', 'html'])

    def test_edit_work_description_covers_merging_into_a_list(self):
        text = self.tools['edit_work']
        self.assertIn('set_text 的 html 同样受正文白名单约束', text)
        self.assertIn('先 set_text 把第一段换成完整的 <ul>…</ul>', text)
        self.assertIn('table 块不能删除、移动或改写', text)

    def test_style_segment_is_not_written_yet(self):
        # STYLE 段属 rich-style：现在写进去会诱导模型用被静默丢弃的样式。
        system = self.agent.SYSTEM
        for phrase in ('data-color', 'data-hl', 'data-align', 'data-indent', 'data-space', '样式积木'):
            self.assertNotIn(phrase, system, 'BASE 段不应出现样式积木：' + phrase)
        self.assertNotIn('format_text', ' '.join(self.tools), 'format_text 属 STYLE，现在还不存在')


class BaseBehaviourTests(unittest.TestCase):
    """§1.8 的 ①②③⑦ 四类中文指令 → 工具调用与回执断言。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = studio.Store(self.temp.name)
        config = Path(self.temp.name) / 'model.json'
        config.write_text(json.dumps({'endpoint': 'https://example.invalid/chat/completions',
                                      'api_key': 'secret-test-value', 'model': 'test-model'}))
        self.agent = studio.assistant_module.Assistant(self.store, studio.content, config)
        self.slug = self.store.create({'title': '格式说明书', 'type': 'article'})['slug']
        self.store.save(self.slug, {'base_version': 'v0001', 'title': '格式说明书', 'blocks': [
            {'kind': 'text', 'html': '<p>关键结论：单卡 48GB 跑 96.5GB 模型靠的是分层卸载。</p>'},
            {'kind': 'text', 'html': '<p>模型与量化配方</p>'},
            {'kind': 'text', 'html': '<p>体量：96.5GB</p>'},
            {'kind': 'text', 'html': '<p>显存：48GB</p>'},
            {'kind': 'text', 'html': '<p>方案：分层卸载</p>'}]})

    def tearDown(self):
        self.temp.cleanup()

    def job(self, message):
        return {'read_versions': {}, 'read_blocks': {}, 'changed': [], 'cancel': False,
                'attachments': [], 'policy': policy_for(message, work_open=True)}

    def read_all(self, job):
        self.agent.execute('read_work', {'slug': self.slug, 'start': 0, 'limit': 30}, job)

    def blocks(self):
        return self.store.detail(self.slug)['blocks']

    # ① 「把『关键结论』加粗」
    def test_bold_a_phrase_uses_update_text_and_reports_applied(self):
        job = self.job('把「关键结论」加粗')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head, 'summary': '关键结论加粗',
            'edits': [{'index': 0, 'html': '<p><strong>关键结论</strong>：单卡 48GB 跑 96.5GB 模型靠的是分层卸载。</p>'}]}, job)
        self.assertIn({'index': 0, 'kind': 'bold'}, result['format']['applied'])
        self.assertEqual(result['format']['dropped'], [])
        self.assertEqual(result['version'], 'v0003')
        self.assertIn('<strong>关键结论</strong>', self.blocks()[0]['html'])
        self.assertNotIn('<b>', self.blocks()[0]['html'], '说明书要求用 strong 不用 b')
        self.assertEqual(len(self.store.detail(self.slug)['versions']), 3, '旧版本必须保留')

    # ② 「第二个小标题改成二级标题」
    def test_heading_level_change_keeps_the_text_verbatim(self):
        job = self.job('第二个小标题改成二级标题')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head, 'summary': '小标题改 h2',
            'edits': [{'index': 1, 'html': '<h2>模型与量化配方</h2>'}]}, job)
        self.assertIn({'index': 1, 'kind': 'h2'}, result['format']['applied'])
        read_back = self.blocks()[1]['html']
        self.assertEqual(read_back, '<h2>模型与量化配方</h2>', '回读仍是 h2，文字一字未改')
        self.assertEqual(self.blocks()[1]['text'], '模型与量化配方')

    # ③ 「这三段改成列表」——一次 edit_work 带齐 set_text + 两个 delete_block
    def test_merge_three_paragraphs_into_one_list(self):
        job = self.job('这三段改成列表')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': head,
            'summary': '三段合并为无序列表', 'operations': [
                {'op': 'set_text', 'index': 2, 'html': '<ul><li>体量：96.5GB</li><li>显存：48GB</li><li>方案：分层卸载</li></ul>'},
                {'op': 'delete_block', 'index': 3},
                {'op': 'delete_block', 'index': 4}]}, job)
        self.assertEqual(result['version'], 'v0003', '一次调用只产生一个新版本')
        html = self.blocks()[2]['html']
        self.assertIn('<ul>', html)
        self.assertEqual(html.count('<li>'), 3)
        self.assertEqual(len(self.blocks()), 3, '块数从 5 减到 3')
        self.assertIn({'index': 2, 'kind': 'list'}, result['format']['applied'])
        self.assertEqual(len(self.store.detail(self.slug)['versions']), 3)

    # ⑦ 「通篇按 Markdown 重新排版」
    def test_whole_article_reflow_uses_reflow_markdown_not_update_text(self):
        # 这篇要有真 Markdown 才排得出东西，否则 reflow 会如实报「没有标记」。
        slug = self.store.create({'title': '待重排', 'type': 'article'})['slug']
        # 模拟历史作品：正文里的 ## 和 ** 是字面量，要靠 reflow_markdown 才排得出来。
        # 修复前的清洗器既不转换也不转义字面量，所以两样都要关掉；只关转换的话，
        # 存进去的是 `&#35;&#35;`/`&#42;&#42;` 实体形态，那不是「历史作品」，
        # reflow 也认不出（复核 H-1a 之后踩过一次）。
        from unittest import mock
        with mock.patch.object(studio, 'markdown_aware_html', studio.clean_html), \
                mock.patch.object(studio, 'MARKUP_LITERALS', ''), \
                mock.patch.object(studio, 'MARKUP_LINE_RULES', ()):
            self.store.save(slug, {'base_version': 'v0001', 'title': '待重排', 'blocks': [
                {'kind': 'text', 'html': '<p>## 一、结论</p>'},
                {'kind': 'text', 'html': '<p>关键结论：**要加粗**</p>'}]})
        job = self.job('通篇按 Markdown 重新排版')
        job['policy'] = policy_for('通篇按 Markdown 重新排版', work_open=True)
        self.agent.execute('read_work', {'slug': slug, 'start': 0, 'limit': 30}, job)
        self.slug = slug
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('reflow_markdown', {'slug': self.slug, 'base_version': head,
                                                        'summary': '按 Markdown 重新排版整篇'}, job)
        self.assertIn('reflowed_blocks', result)
        self.assertNotIn('update_text', [action['name'] for action in job.get('actions', [])])
        self.assertEqual(result['version'], 'v0003')
        self.assertEqual(len(self.store.detail(self.slug)['versions']), 3)

    # 回执透传 + 照实说明
    def test_dropped_formats_reach_the_model_and_are_summarised(self):
        job = self.job('把这块重点改成红色高亮')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        # 夹具换成**真正不在白名单**的属性：样式积木落地后，`data-color` 这类
        # rich_text 白名单内的 data-* 是合法载体并被保留（rich-style 1c），
        # 拿它断言「属性被丢弃」会与那条结论互斥。白名单之外的 `data-foo` 与
        # `onclick` 才是本用例要测的东西——行为不降级，断言反而更准。
        result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head, 'summary': '试试不支持的样式',
            'edits': [{'index': 0, 'html': '<p><span data-foo="bar" onclick="x()">关键结论</span>：<mark>标红</mark></p>'}]}, job)
        reasons = {item['reason'] for item in result['format']['dropped']}
        self.assertIn('attribute_not_allowed', reasons, '模型必须能看见不在白名单的属性被清掉了')
        self.assertIn('tag_not_allowed', reasons)
        self.assertIn('格式未生效', result['summary'], '摘要要提醒模型有格式没生效')
        self.assertTrue(all(item['hint'] for item in result['format']['dropped']))

    def test_whitelisted_style_blocks_are_kept_and_not_reported(self):
        """样式积木的白名单 data-* 是合法载体：保留、且**不进**丢弃回执。

        与上一条互为对照：不在白名单的属性进回执，在白名单里的不进。
        回执只说「真被清掉了的东西」，否则模型会以为红色没存上。
        """
        rich, plain, dropped = studio.clean_html('<p><span data-color="red">关键结论</span></p>')
        self.assertIn('<span data-color="red">', rich)
        self.assertEqual(dropped, [], '合法积木不该被当成被丢弃的格式')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = studio.Store(temp.name)
        slug = store.create({'title': '积木回执'})['slug']
        store.save(slug, {'base_version': 'v0001', 'title': '积木回执',
                          'blocks': [{'kind': 'text', 'html': '<p><span data-color="red">关键结论</span></p>'}],
                          'summary': '积木'})
        saved = store.detail(slug)['blocks'][0]['html']
        self.assertIn('<span data-color="red">', saved)
        self.assertEqual(store.save(slug, {'base_version': store.meta(slug)['head'], 'title': '积木回执',
                                           'blocks': [{'kind': 'text', 'html': saved}], 'summary': '再存'})['dropped_formats'], [])

    # M2b M1：同一标签写两个 style，只有第一个会被采用；后者若没生效必须如实报出。
    def test_duplicate_style_reports_the_one_that_did_not_take_effect(self):
        job = self.job('把字标红再换底色')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head,
            'summary': '重复 style',
            'edits': [{'index': 0, 'html': '<p><span style="color:red" style="background:yellow">关键结论</span></p>'}]}, job)
        saved = self.blocks()[0]['html']
        self.assertIn('data-color="red"', saved, '第一个 style 生效')
        self.assertNotIn('yellow', saved, '第二个 style 未被采用')
        dropped = result['format']['dropped']
        self.assertTrue(dropped, '未被采用的第二个 style 必须进回执，不能静默丢弃')
        self.assertTrue(any('background:yellow' in item['requested'] for item in dropped),
                        '回执要指向没生效的那一个 style')
        self.assertIn('格式未生效', result['summary'])
        self.assertTrue(all(item['hint'] for item in dropped))

    # 重复但效果相同：第二个 style 没有丢失任何东西，不得谎报未生效。
    def test_duplicate_style_with_the_same_effect_is_not_reported(self):
        job = self.job('把字标红')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head,
            'summary': '等价重复 style',
            'edits': [{'index': 0, 'html': '<p><span style="color:red" style="color:red">关键结论</span></p>'}]}, job)
        self.assertIn('data-color="red"', self.blocks()[0]['html'])
        self.assertEqual(result['format']['dropped'], [], '效果一致的重复 style 不算未生效')
        self.assertNotIn('格式未生效', result['summary'])

    # M2b M2：无值 / 空值 / 不完整声明必须能正常保存，并给出真实原因，不能抛异常。
    def test_valueless_or_incomplete_style_saves_and_reports_a_reason(self):
        for html in ('<p style>关键结论</p>', '<p style="">关键结论</p>',
                     '<p style=";;">关键结论</p>', '<p style="color">关键结论</p>',
                     '<p style="color:">关键结论</p>'):
            with self.subTest(html=html):
                job = self.job('给这段加样式')
                self.read_all(job)
                head = self.store.meta(self.slug)['head']
                result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head,
                    'summary': '异常 style', 'edits': [{'index': 0, 'html': html}]}, job)
                self.assertEqual(self.blocks()[0]['html'], '<p>关键结论</p>', '正文照常保存，不因坏 style 崩掉')
                dropped = result['format']['dropped']
                self.assertTrue(dropped, '不能生效的 style 要如实报出')
                self.assertTrue(all(item['hint'] for item in dropped), '每条都要带真实原因')
                self.assertIn('格式未生效', result['summary'])

    # M2c M1：回执必须以清洗器**实际落盘的积木**为准，而不是首个 style 的理论映射。
    def test_data_priority_receipt_matches_what_was_saved(self):
        cases = [
            # data 蓝 + 首项红 + 次项蓝：实际蓝；次项蓝与 data 等价不得报，真正没实现的是首项红。
            ('<p><span data-color="blue" style="color:red" style="color:blue">关键结论</span></p>',
             ['data-color="blue"'], ['style="color:red"'], ['style="color:blue"']),
            # data 蓝 + 两个 red：实际蓝，两个 red 都没实现，都要报。
            ('<p><span data-color="blue" style="color:red" style="color:red">关键结论</span></p>',
             ['data-color="blue"'], ['style="color:red"'], []),
            # data 蓝 + 红 + 蓝 + 黄高亮：实际蓝，报首项红与黄高亮，不报次项蓝。
            ('<p><span data-color="blue" style="color:red" style="color:blue" style="background:yellow">关键结论</span></p>',
             ['data-color="blue"'], ['style="color:red"', 'style="background:yellow"'], ['style="color:blue"']),
            # 单条 style 被 data 覆盖：红没实现，要报。
            ('<p><span data-color="blue" style="color:red">关键结论</span></p>',
             ['data-color="blue"'], ['style="color:red"'], []),
            # data 与 style 等价：不报。
            ('<p><span data-color="red" style="color:red">关键结论</span></p>',
             ['data-color="red"'], [], ['style="color:red"']),
        ]
        for html, saved_has, must_report, must_not_report in cases:
            with self.subTest(html=html):
                job = self.job('给这段加颜色')
                self.read_all(job)
                head = self.store.meta(self.slug)['head']
                result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head,
                    'summary': '颜色与高亮', 'edits': [{'index': 0, 'html': html}]}, job)
                saved = self.blocks()[0]['html']
                for needle in saved_has:
                    self.assertIn(needle, saved, '落盘要与实际采用的积木一致')
                dropped = result['format']['dropped']
                for needle in must_report:
                    self.assertTrue(any(needle in item['requested'] for item in dropped),
                                    '真正没实现的效果要报：' + needle)
                for needle in must_not_report:
                    self.assertFalse(any(needle in item['requested'] for item in dropped),
                                     '与实际落盘等价的请求不得谎报：' + needle)
                self.assertTrue(all(item['hint'] for item in dropped), '每条都要带具体原因')

    # M2c 要求 2：重复 data-* 与重复 style 同理，只报没被采用的那一项。
    def test_repeated_data_attribute_reports_only_the_unused_one(self):
        job = self.job('给这段加颜色')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head,
            'summary': '重复 data-color',
            'edits': [{'index': 0, 'html': '<p><span data-color="red" data-color="blue">关键结论</span></p>'}]}, job)
        self.assertIn('data-color="red"', self.blocks()[0]['html'])
        dropped = result['format']['dropped']
        self.assertTrue(any('data-color="blue"' in item['requested'] for item in dropped),
                        '没被采用的第二个 data-color 要报')
        self.assertFalse(any('data-color="red"' in item['requested'] for item in dropped),
                         '被采用的首项不得报')
        self.assertIn('格式未生效', result['summary'])

        # 两个相同值：等价，不报。
        job = self.job('给这段加颜色')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        again = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head,
            'summary': '等价重复 data-color',
            'edits': [{'index': 0, 'html': '<p><span DATA-COLOR="red" data-color="red">关键结论</span></p>'}]}, job)
        self.assertIn('data-color="red"', self.blocks()[0]['html'])
        self.assertEqual(again['format']['dropped'], [], '等价的重复 data-* 不算未生效')
        self.assertNotIn('格式未生效', again['summary'])

    # 第 13 轮 M3：工具结果必须让模型分清哪些格式真生效，不能只丢一句「N 项未生效」。
    def test_tool_result_spells_out_applied_and_dropped_formats(self):
        job = self.job('把这段改成红字黄底居中')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head,
            'summary': '红字黄底居中',
            'edits': [{'index': 0, 'html': '<p style="text-align:center">'
                       '<span style="color:red;background-color:yellow;position:absolute" style="color:blue">'
                       '关键结论</span></p>'}]}, job)
        text = result.get('format_text')
        self.assertIsInstance(text, str, '工具结果必须给出可读的格式讲稿')
        self.assertIn('已生效格式', text)
        self.assertIn('未生效格式', text)
        applied, dropped = text.split('未生效格式', 1)
        # 落盘真生效的三项都要进「已生效」。
        for label in ('红色文字', '黄色高亮', '居中'):
            self.assertIn(label, applied, '落盘生效的格式必须列进已生效：' + label)
        # 真没生效的只报 position 与未采用的第二个 style；黄底/红字不得被说成未生效。
        self.assertIn('position', dropped)
        self.assertNotIn('高亮', dropped, '黄底真生效，不得出现在未生效段')
        self.assertNotIn('黄色', dropped)
        self.assertIn('只采用第一个 style', dropped)
        self.assertEqual(self.blocks()[0]['html'],
                         '<p data-align="center"><span data-color="red" data-hl="yellow">关键结论</span></p>')
        # format.dropped 的结构语义一个字没动：仍是两条 {requested,reason,hint}。
        self.assertEqual(len(result['format']['dropped']), 2)
        self.assertEqual({item['reason'] for item in result['format']['dropped']},
                         {'attribute_not_allowed'})

    def test_format_text_is_carried_by_edit_work_and_reflow(self):
        job = self.job('把正文里的 **粗体** 排版好')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': head, 'summary': '排版',
            'operations': [{'op': 'set_text', 'index': 0,
                            'html': '<p><span data-color="blue">关键结论</span><mark>红</mark></p>'}]}, job)
        self.assertIn('蓝色文字', result.get('format_text') or '', 'edit_work 也要给出格式讲稿')
        job = self.job('通篇按 Markdown 重新排版')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        again = self.agent.execute('reflow_markdown', {'slug': self.slug, 'base_version': head,
                                                       'summary': '再排一次'}, job)
        self.assertIn('已生效格式', again.get('format_text') or '', 'reflow_markdown 也要给出格式讲稿')

    def test_format_text_says_none_dropped_when_everything_landed(self):
        job = self.job('把字标红')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('update_text', {'slug': self.slug, 'base_version': head,
            'summary': '标红', 'edits': [{'index': 0, 'html': '<p><span data-color="red">关键结论</span></p>'}]}, job)
        self.assertEqual(result['format_text'], '已生效格式：红色文字；未生效格式：无')
        self.assertNotIn('格式未生效', result['summary'])

    def test_receipt_passes_through_edit_work_and_reflow(self):
        job = self.job('把正文里的 **粗体** 排版好')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('edit_work', {'slug': self.slug, 'base_version': head, 'summary': '排版',
            'operations': [{'op': 'set_text', 'index': 0, 'html': '<p><strong>关键结论</strong>：<mark>红</mark></p>'}]}, job)
        self.assertIn('format', result, 'edit_work 回执也要透传 format')
        self.assertIn('tag_not_allowed', {item['reason'] for item in result['format']['dropped']})
        self.assertIn('格式未生效', result['summary'])

        job = self.job('通篇按 Markdown 重新排版')
        self.read_all(job)
        head = self.store.meta(self.slug)['head']
        again = self.agent.execute('reflow_markdown', {'slug': self.slug, 'base_version': head,
                                                       'summary': '再排一次'}, job)
        self.assertIn('format', again, 'reflow_markdown 回执也要透传 format')

    # BASE 期遇到样式说法：工具可用，但说明书告诉模型直说不支持
    def test_style_requests_are_not_blocked_but_the_guide_says_unsupported(self):
        for text in ('这段居中', '这个词标红', '全文首行缩进'):
            with self.subTest(text=text):
                p = policy_for(text, work_open=True)
                self.assertIn('update_text', p['allowed_tools'], 'BASE 不拦，只是能力还没有')
        import assistant_agent
        self.assertIn('任意字号 px、任意颜色值', assistant_agent.SYSTEM)
        self.assertNotIn('format_text', ' '.join(t['function']['name'] for t in assistant_agent.TOOLS))


B3 = chr(96) * 3


class ReviewRoundCTests(unittest.TestCase):
    """复核 content-markdown-b / guide-a 打回来的 A 侧四项：M1、M2、L1、L2。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = studio.Store(self.temp.name)
        self.slug = self.store.create({'title': '回执', 'type': 'article'})['slug']
        self.store.save(self.slug, {'base_version': 'v0001', 'title': '回执', 'blocks': [
            {'kind': 'text', 'html': '<p>第一段正文。</p>'}, {'kind': 'text', 'html': '<p>第二段正文。</p>'}],
            'summary': '初稿'})
        import assistant_agent
        self.module = assistant_agent

    def assistant(self, message):
        """带助手与假模型的最小现场，复用 BASE 用例的脚手架。"""
        case = BaseBehaviourTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        job = case.job(message)
        job['prompt'] = message
        case.read_all(job)
        return case, job

    # --- A-M1 Markdown 路径不得漏报被清洗的用户格式 ---

    def test_markdown_path_reports_the_dropped_format_of_the_original_input(self):
        # `<p style="color:red">**重点**</p>`：红色没存上，但转换后已看不到 style。
        rich, plain, dropped = studio.markdown_aware_html('<p style="color:red">**重点**</p>')
        self.assertNotIn('color', rich)
        self.assertIn('<strong>重点</strong>', rich)
        self.assertEqual([item['requested'] for item in dropped], ['style="color:red"'],
                         '走 Markdown 那条路也必须报原输入被清洗掉的格式')
        self.assertEqual(dropped[0]['reason'], 'attribute_not_allowed')
        self.assertTrue(dropped[0]['hint'])

    def test_dropped_reaches_every_write_path(self):
        raw = '<p style="color:red">**重点**</p>'
        cases = [('update_text', {'edits': [{'index': 0, 'html': raw}]}),
                 ('update_text', {'append_text': [raw]}),
                 ('edit_work', {'operations': [{'op': 'set_text', 'index': 0, 'html': raw}]})]
        for name, extra in cases:
            with self.subTest(tool=name, keys=sorted(extra)):
                case, job = self.assistant('把关键结论加粗')
                args = dict(slug=case.slug, base_version=case.store.meta(case.slug)['head'],
                            summary='独立探针', **extra)
                result = case.agent.execute(name, args, job)
                requested = [item['requested'] for item in result['format']['dropped']]
                self.assertIn('style="color:red"', requested, name + ' 这条路径漏报了')
                self.assertIn('格式未生效', result['summary'], name + ' 的摘要没提醒')

    def test_reflow_does_not_drop_the_first_pass_record(self):
        # 历史作品里的明文 Markdown（清洗层改成实体之前存的）。围栏转换时
        # markdown-it 会自动补 class="language-…"：重排前那次清洗必须报出来，
        # 之后 save 只看得到已经转成 HTML 的 rich，补不回来（复核 A-M1）。
        with mock.patch.object(studio, 'MARKUP_LITERALS', ''), \
                mock.patch.object(studio, 'markdown_aware_html', studio.clean_html):
            first = self.store.save(self.slug, {'base_version': self.store.meta(self.slug)['head'],
                                                'title': '回执',
                                                'blocks': [{'kind': 'text', 'html': '```python\nprint(1)\n```'}]})
        result = self.store.reflow(self.slug, {'base_version': first['version']})
        self.assertFalse(result['unchanged'])
        dropped = [(item['index'], item['requested'], item['reason']) for item in result['format']['dropped']]
        self.assertIn((0, 'class="language-python"', 'attribute_not_allowed'), dropped,
                      '重排前的清洗记录丢了，模型会把没生效的格式说成已生效')
        self.assertIn('格式未生效', self.module.format_receipt_suffix(result['format']))

    def test_merge_does_not_manufacture_false_failures(self):
        # 已排好版的正文回读一遍必须零记录：合并两遍 dropped 不得凭空多出条目。
        for raw in ('<p>已经排好版的 <strong>正文</strong>。</p>',
                    '<p><a href="https://example.com">链接</a></p>',
                    '<p>普通正文。</p>'):
            with self.subTest(raw=raw):
                rich, _, dropped = studio.markdown_aware_html(raw)
                self.assertEqual(studio.markdown_aware_html(rich)[2], [], '回读自己的输出不该报格式失败')
        # 围栏语言标记是真的丢了（解析器补的 class 不保留），照实报出来。
        _, _, dropped = studio.markdown_aware_html('```python\nprint(1)\n```')
        self.assertEqual([item['requested'] for item in dropped], ['class="language-python"'])

    # --- A-M2 update_text 说明改为 schema 合法 ---

    def test_index_only_edit_is_rejected_with_an_actionable_message(self):
        case, job = self.assistant('把关键结论加粗')
        with self.assertRaises(ValueError) as caught:
            case.agent.execute('update_text', {'slug': case.slug,
                                               'base_version': case.store.meta(case.slug)['head'],
                                               'edits': [{'index': 0, 'html': '<p>改好的第一段</p>'},
                                                         {'index': 1}],
                                               'summary': '独立探针'}, job)
        self.assertNotIsInstance(caught.exception, KeyError)
        self.assertIn('没改的块不要提交', str(caught.exception))

    def test_unchanged_blocks_are_kept_when_only_changed_blocks_are_submitted(self):
        case, job = self.assistant('把关键结论加粗')
        before = case.blocks()
        case.agent.execute('update_text', {'slug': case.slug,
                                           'base_version': case.store.meta(case.slug)['head'],
                                           'edits': [{'index': 0, 'html': '<p>只改第一段</p>'}],
                                           'summary': '独立探针'}, job)
        after = case.blocks()
        self.assertEqual(len(after), len(before), '没提交的块必须原样保留')
        self.assertEqual(after[0]['html'], '<p>只改第一段</p>')
        self.assertEqual([b['html'] for b in after[1:]], [b['html'] for b in before[1:]],
                         '未修改块逐字不变')

    # --- A-L1 内部 rel 不算用户格式失败 ---

    def test_internal_rel_is_not_reported_as_dropped(self):
        raw = '<p><a href="https://example.com" rel="noopener noreferrer">链接</a></p>'
        rich, _, dropped = studio.markdown_aware_html(raw)
        self.assertEqual(rich, raw, '回读自己的输出必须逐字不变')
        self.assertEqual(dropped, [], '清洗器自己补的 rel 不是用户格式失败')
        self.assertEqual(studio.markdown_aware_html(raw)[2], [])

    # --- A-L2 无变化重排回执完整 ---

    def test_reflow_without_changes_still_returns_a_uniform_format(self):
        result = self.store.reflow(self.slug, {'base_version': self.store.meta(self.slug)['head']})
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['format']['applied'], [])
        self.assertEqual(result['format']['dropped'], [])
        self.assertFalse(result['format']['truncated'])

    def test_truncated_receipt_says_at_least(self):
        dropped = [{'requested': 'style="%d"' % i, 'reason': 'attribute_not_allowed', 'hint': 'x'}
                   for i in range(25)]
        suffix = self.module.format_receipt_suffix({'dropped': dropped[:20], 'truncated': True})
        self.assertEqual(suffix, '；其中至少 20 项格式未生效')
        exact = self.module.format_receipt_suffix({'dropped': dropped[:3], 'truncated': False})
        self.assertEqual(exact, '；其中 3 项格式未生效')
        self.assertEqual(self.module.format_receipt_suffix({'dropped': []}), '')
        self.assertEqual(self.module.format_receipt_suffix(None), '')


    # --- A-L1（新）：截断判定要在裁剪前比 > 20 ---

    def test_truncated_flag_is_computed_before_clipping(self):
        """19/20/21 块围栏重排回执必须是 (19,false)/(20,false)/(20,true)。

        刚好 20 条时一条没丢，标成 truncated 会让摘要说「至少 20 项」，
        把没丢的格式报少了（复核 A-L1）。
        """
        for count, expected_truncated in ((19, False), (20, False), (21, True)):
            with self.subTest(blocks=count):
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                store = studio.Store(temp.name)
                slug = store.create({'title': '回执', 'type': 'article'})['slug']
                with mock.patch.object(studio, 'MARKUP_LITERALS', ''), \
                        mock.patch.object(studio, 'MARKUP_LINE_RULES', ()), \
                        mock.patch.object(studio, 'markdown_aware_html', studio.clean_html):
                    first = store.save(slug, {'base_version': 'v0001', 'title': '回执', 'blocks': [
                        {'kind': 'text', 'html': B3 + 'python\nx=1\n' + B3} for _ in range(count)]})
                result = store.reflow(slug, {'base_version': first['version']})
                self.assertEqual(len(result['format']['dropped']), min(count, 20))
                self.assertEqual(result['format']['truncated'], expected_truncated)
                suffix = studio.assistant_module.format_receipt_suffix(result['format'])
                if expected_truncated:
                    self.assertEqual(suffix, '；其中至少 20 项格式未生效')
                else:
                    self.assertEqual(suffix, '；其中 %d 项格式未生效' % count)
                    self.assertNotIn('至少', suffix, '一条没丢就不能说「至少」')


class ReviewRound13BTests(unittest.TestCase):
    """复核 13 轮返修 b 打回的两条反例：M2 重复请求、M3 讲稿固定版本。

    走真实 `Assistant.execute → Store.save → 回读`，不接任何真实模型
    （假模型现场：只有本地 Store 与助手执行层）。修前红、修后绿。
    证据口径对应 coordination/evidence/review-round13-accept-style/
    的 m3-extra.json（repeat=true）与 concurrent-receipt.json。
    """

    def assistant(self, message):
        case = BaseBehaviourTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        job = case.job(message)
        job['prompt'] = message
        case.read_all(job)
        return case, job

    def _update(self, case, job, html, summary='红字黄底居中'):
        return case.agent.execute('update_text', {'slug': case.slug,
            'base_version': case.store.meta(case.slug)['head'], 'summary': summary,
            'edits': [{'index': 0, 'html': html}]}, job)

    # --- M2：重复提交（unchanged=true）不得笼统报「已生效格式：无」 ---
    def test_repeat_request_reports_the_requested_styles_as_already_effective(self):
        case, job = self.assistant('把这段改成红字黄底居中')
        html = ('<p style="text-align:center">'
                '<span style="color:red;background-color:yellow;position:absolute" style="color:blue">'
                '关键结论</span></p>')
        first = self._update(case, job, html)
        self.assertEqual(first['version'], 'v0003')
        for label in ('红色文字', '黄色高亮', '居中'):
            self.assertIn(label, first['format_text'], '首存就要报对：' + label)
        self.assertEqual(case.blocks()[0]['html'],
                         '<p data-align="center"><span data-color="red" data-hl="yellow">关键结论</span></p>')

        # 同一请求原样再提交：Store 返回 unchanged，落盘仍是红字黄底居中。
        repeat_job = case.job('把这段改成红字黄底居中')
        case.read_all(repeat_job)
        again = self._update(case, repeat_job, html)
        self.assertTrue(again.get('unchanged'), '同一 HTML 二次提交必须走 unchanged 分支')
        self.assertEqual(again['version'], 'v0003', 'unchanged 不产生新版本')
        self.assertEqual(case.blocks()[0]['html'],
                         '<p data-align="center"><span data-color="red" data-hl="yellow">关键结论</span></p>')
        applied, dropped = again['format_text'].split('未生效格式', 1)
        for label in ('红色文字', '黄色高亮', '居中'):
            self.assertIn(label, applied, '重复提交时已在落盘生效的格式必须列进已生效：' + label)
        self.assertNotIn('已生效格式：无', again['format_text'],
                         'unchanged 不等于「没有生效格式」，这正是 M2 的误报')
        self.assertNotIn('高亮', dropped, '黄底真在落盘，不得出现在未生效段')
        self.assertIn('position', dropped, '真正没生效的 position 仍要报')
        # 回执结构语义没动：仍是两条 {requested,reason,hint}。
        self.assertEqual({item['reason'] for item in again['format']['dropped']}, {'attribute_not_allowed'})

    def test_repeat_receipt_does_not_mix_in_unrelated_blocks_styles(self):
        case, job = self.assistant('把这段改成红字黄底居中')
        html = '<p style="text-align:center"><span style="color:red;background-color:yellow">关键结论</span></p>'
        self._update(case, job, html)
        # 另一块被加上蓝色文字：它不在本次请求的目标块里，回读不得把它算进来。
        other_job = case.job('把另一段标蓝')
        case.read_all(other_job)
        other = case.agent.execute('update_text', {'slug': case.slug,
            'base_version': case.store.meta(case.slug)['head'], 'summary': '另一段标蓝',
            'edits': [{'index': 1, 'html': '<p><span data-color="blue">模型与量化配方</span></p>'}]}, other_job)
        self.assertEqual(other['version'], 'v0004')
        repeat_job = case.job('把这段改成红字黄底居中')
        case.read_all(repeat_job)
        again = self._update(case, repeat_job, html)
        self.assertTrue(again.get('unchanged'))
        applied, _ = again['format_text'].split('未生效格式', 1)
        self.assertIn('红色文字', applied)
        self.assertIn('黄色高亮', applied)
        self.assertNotIn('蓝色文字', applied, '无关块的原有样式不得混进本次请求的回执')

    # --- M3：讲稿必须绑定本轮保存的版本，不能读并发之后的 head ---
    def test_receipt_is_pinned_to_the_version_this_request_saved(self):
        case, job = self.assistant('把这句标红')
        real_save = case.store.save
        rounds = []

        def racing_save(slug, payload, source='工作台编辑'):
            result = real_save(slug, payload, source)
            # 本轮已落盘、讲稿还没算：另一请求在这期间合法保存 v0004 蓝字。
            rounds.append(result['version'])
            if len(rounds) == 1:
                real_save(slug, {'base_version': result['version'], 'title': payload.get('title') or '格式说明书',
                                 'blocks': [{'kind': 'text', 'html': '<p><span data-color="blue">后续另一次合法保存</span></p>'}]
                                           + [{'index': i} for i in range(1, 5)],
                                 'summary': '并发另一请求'})
            return result

        case.store.save = racing_save
        try:
            result = self._update(case, job, '<p><span data-color="red">本轮红色</span></p>', summary='标红')
        finally:
            case.store.save = real_save
        self.assertEqual(result['version'], 'v0003')
        self.assertEqual(case.store.meta(case.slug)['head'], 'v0004', '现场必须真的发生并发保存')
        self.assertIn('红色文字', result['format_text'], '讲稿要讲本轮保存的 v0003 红字')
        self.assertNotIn('蓝色文字', result['format_text'], '不得讲成并发请求的 v0004 蓝字')
        self.assertEqual(result['format']['dropped'], [])


class ReviewRound13CTests(unittest.TestCase):
    """复核 13 轮返修 b 打回的 edit_work 重复内联 style 回执（M1）。

    走真实 `Assistant.execute → Store.save → 固定版本回读`，不接真实模型。
    b 版代码把 specs 里的**请求原文**当成目标块，内联 style 已被规范化成 data-*，
    于是重复请求被误报成「已生效格式：无」。修前红、修后绿。
    证据口径对应 coordination/evidence/review-round13-accept-style-b/extra.json
    的 edit_work repeat=true 与 update_text 对照。
    """

    # 目标块请求：红字黄底居中，另有一条 position 与第二个 style 没生效。
    REQUEST = ('<p style="text-align:center">'
               '<span style="color:red;background-color:yellow;position:absolute" style="color:blue">'
               '第13轮样式验收：红字黄底居中</span></p>')
    LANDED = ('<p data-align="center"><span data-color="red" data-hl="yellow">'
              '第13轮样式验收：红字黄底居中</span></p>')
    # 规范化后等价的写法：直接 data-*、以及换个声明顺序的内联 style。
    EQUIVALENT = (LANDED,
                  '<p style="text-align:center"><span style="background-color:yellow;color:red">'
                  '第13轮样式验收：红字黄底居中</span></p>')
    STYLES = ('红色文字', '黄色高亮', '居中')

    def setUp(self):
        self.case = self.fresh()

    def fresh(self):
        case = BaseBehaviourTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        return case

    def _edit(self, case, html, index=0, message='把第一段改成红字黄底居中'):
        job = case.job(message)
        job['prompt'] = message
        case.read_all(job)
        return case.agent.execute('edit_work', {'slug': case.slug,
            'base_version': case.store.meta(case.slug)['head'], 'summary': message,
            'operations': [{'op': 'set_text', 'index': index, 'html': html}]}, job)

    def _blue(self, case):
        """给**另一块**加蓝字：它不在本次请求的目标块里，回读不得把它算进来。"""
        message = '把第二段标蓝'
        job = case.job(message)
        job['prompt'] = message
        case.read_all(job)
        return case.agent.execute('update_text', {'slug': case.slug,
            'base_version': case.store.meta(case.slug)['head'], 'summary': message,
            'edits': [{'index': 1, 'html': '<p><span data-color="blue">模型与量化配方</span></p>'}]}, job)

    def _repeat(self, case, html):
        """首存（v0003）+ 无关块加蓝（v0004）之后，原样再提交一次同一请求。"""
        first = self._edit(case, self.REQUEST)
        self.assertEqual(first['version'], 'v0003')
        for label in self.STYLES:
            self.assertIn(label, first['format_text'], '首存就要报对：' + label)
        self.assertEqual(case.blocks()[0]['html'], self.LANDED)
        self.assertEqual(self._blue(case)['version'], 'v0004')
        return self._edit(case, html)

    def test_edit_work_repeat_inline_style_reports_the_styles_already_in_effect(self):
        again = self._repeat(self.case, self.REQUEST)
        self.assertTrue(again.get('unchanged'), '同一 HTML 二次提交必须走 unchanged 分支')
        self.assertEqual(again['version'], 'v0004', 'unchanged 不产生新版本')
        self.assertEqual(self.case.blocks()[0]['html'], self.LANDED, '落盘仍是红字黄底居中')
        applied, dropped = again['format_text'].split('未生效格式', 1)
        for label in self.STYLES:
            self.assertIn(label, applied, '重复提交时落盘已有的格式必须列进已生效：' + label)
        self.assertIn('已存在且有效', applied, 'unchanged 的「已生效」要注明格式原来就在')
        self.assertNotIn('已生效格式：无', again['format_text'], '这正是 M1 的误报')
        self.assertNotIn('蓝色文字', applied, '无关块的原有样式不得混进本次请求的回执')
        self.assertIn('position', dropped, '真正没生效的 position 仍要报')
        self.assertIn('只采用第一个 style', dropped)
        # 回执结构语义没动：仍是两条 {requested,reason,hint}。
        self.assertEqual(len(again['format']['dropped']), 2)
        self.assertEqual({item['reason'] for item in again['format']['dropped']},
                         {'attribute_not_allowed'})

    def test_edit_work_repeat_of_equivalent_input_reports_what_landed(self):
        """规范化后等价的写法（直接 data-*、换个声明顺序）同样按落盘讲。"""
        for html in self.EQUIVALENT:
            with self.subTest(html=html):
                case = self.fresh()
                again = self._repeat(case, html)
                self.assertTrue(again.get('unchanged'), '等价写法应当命中 unchanged')
                self.assertEqual(case.blocks()[0]['html'], self.LANDED)
                applied, dropped = again['format_text'].split('未生效格式', 1)
                for label in self.STYLES:
                    self.assertIn(label, applied, '落盘已有的格式必须列进已生效：' + label)
                self.assertIn('已存在且有效', applied)
                self.assertNotIn('已生效格式：无', again['format_text'], '这正是 M1 的误报')
                self.assertNotIn('蓝色文字', applied, '无关块的原有样式不得混进本次请求的回执')
                self.assertNotIn('高亮', dropped)


class ReviewRound13DTests(unittest.TestCase):
    """复核 13 轮返修 c 打回的反例（13d）：内容相同的重复段落让 edit_work 认错目标块。

    走真实 `Assistant.execute('edit_work') → Store.save → 固定版本回读`，不接真实模型。
    目标块按**编辑计划的位置**认领，不拿内容当身份：两个 HTML 完全相同的段落也各自
    对号入座（第 0 块的目标落在第 0 格，第 1 块的目标落在第 1 格）。修前按内容找锚点，
    后面的同内容块会把目标位置认领走，讲稿误报「已生效格式：无」——修前红、修后绿。
    证据口径对应 coordination/evidence/review-round13-accept-style-c/new-extra-final.json
    的 duplicate-target / duplicate-before。
    """

    # 目标块请求：红字黄底居中，另有一条 position 与第二个 style 没生效。
    REQUEST = ('<p style="text-align:center">'
               '<span style="color:red;background-color:yellow;position:absolute" style="color:blue">'
               '第13轮样式验收：红字黄底居中</span></p>')
    # 清洗后落盘的规范化 HTML（与请求等价，提交后 unchanged）。
    LANDED = ('<p data-align="center"><span data-color="red" data-hl="yellow">'
              '第13轮样式验收：红字黄底居中</span></p>')
    BLUE = '<p><span data-color="blue">无关蓝字</span></p>'
    PLAIN = '<p>无关蓝字</p>'
    STYLES = ('红色文字', '黄色高亮', '居中')

    def setUp(self):
        self.case = self.fresh()

    def fresh(self, tail=None):
        """复用一个独立临时库，并把正文换成「第 0、1 块 HTML 完全相同」的重复段落夹具。"""
        case = BaseBehaviourTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        case.store.save(case.slug, {'base_version': case.store.meta(case.slug)['head'],
            'title': '格式说明书',
            'blocks': [{'kind': 'text', 'html': self.LANDED},
                       {'kind': 'text', 'html': self.LANDED},
                       {'kind': 'text', 'html': tail or self.BLUE}]})
        return case

    def edit(self, case, index=0, message='把第一段改成红字黄底居中', operations=None):
        job = case.job(message)
        job['prompt'] = message
        case.read_all(job)
        ops = operations if operations is not None else [
            {'op': 'set_text', 'index': index, 'html': self.REQUEST}]
        return case.agent.execute('edit_work', {'slug': case.slug,
            'base_version': case.store.meta(case.slug)['head'], 'summary': message,
            'operations': ops}, job)

    def assert_styles_reported(self, case, result):
        applied, dropped = result['format_text'].split('未生效格式', 1)
        for label in self.STYLES:
            self.assertIn(label, applied, '落盘已有的格式必须列进已生效：' + label)
        self.assertIn('已存在且有效', applied, 'unchanged 的「已生效」要注明格式原来就在')
        self.assertNotIn('已生效格式：无', result['format_text'],
                         '重复段落认错目标位置时才会报「无」，这正是 13c-M1 的误报')
        self.assertNotIn('蓝色文字', applied, '无关块的原有样式不得混进本次请求的回执')
        self.assertIn('position', dropped, '真正没生效的 position 仍要报')

    def test_duplicate_target_maps_the_edited_block_by_plan_position(self):
        """改第 0 块：plan 第 0 项就是目标，不因为后面有一模一样的第 1 块而认错。"""
        result = self.edit(self.case, index=0)
        self.assertTrue(result.get('unchanged'), '同一 HTML 二次提交必须走 unchanged 分支')
        self.assertEqual([b['html'] for b in self.case.blocks()],
                         [self.LANDED, self.LANDED, self.BLUE])
        self.assert_styles_reported(self.case, result)

    def test_duplicate_before_maps_the_edited_block_by_plan_position(self):
        """改第 1 块：目标落在计划第 1 格，前面同内容块不被当成本次目标。"""
        result = self.edit(self.case, index=1, message='把第二段改成红字黄底居中')
        self.assertTrue(result.get('unchanged'))
        self.assertEqual([b['html'] for b in self.case.blocks()],
                         [self.LANDED, self.LANDED, self.BLUE])
        self.assert_styles_reported(self.case, result)

    def test_duplicate_blocks_with_delete_and_paste_insert_still_map_the_target(self):
        """重复块 + 删除一块 + 粘贴插入等价一块：净结果逐块相同，目标仍按计划位置认领。"""
        case = self.fresh(tail=self.PLAIN)
        message = '补一段「无关蓝字」'
        result = self.edit(case, message=message, operations=[
            {'op': 'set_text', 'index': 0, 'html': self.REQUEST},
            {'op': 'delete_block', 'index': 2},
            {'op': 'insert_text', 'after': 1, 'text_source': 'paste', 'html': self.PLAIN}])
        self.assertTrue(result.get('unchanged'), '删除与等价插入相抵，正文与上一版逐块相同')
        self.assertEqual([b['html'] for b in case.blocks()],
                         [self.LANDED, self.LANDED, self.PLAIN])
        self.assert_styles_reported(case, result)


if __name__ == '__main__':
    unittest.main()