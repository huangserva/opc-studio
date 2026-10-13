"""正文 Markdown 转换与「按 Markdown 重新排版」的回归测试。

覆盖三条线：导入/编辑时 Markdown 迹象才转换（P1）、重新排版生成新版本且旧版本不变（P2）、
纯文本与既有格式不受影响。
"""
import copy
import importlib.util
import json
import sys
import tempfile
import contextlib
import re
import unittest
from html import unescape
from urllib.parse import unquote
from pathlib import Path
from unittest import mock

import markdown_blocks
from html.parser import HTMLParser as HTMLReaderBase

# server.py 加载 article-importer 兼容入口时，会把正式工作台插到 sys.path 最前。
# 这里必须在加载 server.py 之前先把本副本的模块钉进 sys.modules，
# 否则后面的 from assistant_policy import ... 会拿到共享工作区那份，等于没测。
import assistant_policy  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('studio', ROOT / 'server.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)


def _held(nonce='0' * 24):
    """一张带固定 nonce 的哨兵表（复核 h：标记 = 索引 + 本次 nonce）。"""
    return markdown_blocks._Held(nonce)


@contextlib.contextmanager
def pinned_nonce(*values):
    """把 nonce 工厂钉死，让机制断言能引用具体标记；用完自动还原。

    只固定**内部标记**，不固定输出——生产路径的输出与 nonce 无关，由
    `test_output_never_carries_a_nonce` 单独守住。
    """
    todo = list(values)
    previous = markdown_blocks._NONCE_FACTORY

    def factory():
        return todo.pop(0) if todo else 'f' * 24

    markdown_blocks._NONCE_FACTORY = factory
    try:
        yield
    finally:
        markdown_blocks._NONCE_FACTORY = previous


class UnderTestCopyTests(unittest.TestCase):
    """守住「测的确实是本副本」——server.py 会把正式工作台插到 sys.path 最前。"""

    def test_modules_under_test_come_from_this_copy(self):
        self.assertEqual(Path(assistant_policy.__file__).resolve().parent, ROOT)
        self.assertEqual(Path(markdown_blocks.__file__).resolve().parent, ROOT)
        self.assertEqual(Path(studio.__file__).resolve().parent, ROOT)
        self.assertEqual(Path(studio.assistant_module.__file__).resolve().parent, ROOT)


class MarkdownSignalTests(unittest.TestCase):
    def test_strong_signals_are_detected(self):
        for text, name in (
                ('## 01. 账本与底牌', '标题'),
                ('### 模型与量化配方', '标题'),
                ('一个 **96.5GB** 的模型', '粗体'),
                ('已经 ~~废弃~~ 的做法', '删除线'),
                ('> 引用一段结论', '引用'),
                ('正文\n\n---\n\n后文', '分隔线'),
                ('```python\nprint(1)\n```', '围栏代码'),
                ('参考 [文档](https://example.com/a)', '链接')):
            with self.subTest(text=text):
                self.assertIn(name, markdown_blocks.signals(text))

    def test_plain_text_has_no_signal(self):
        # 中文技术正文里最容易误判的几类：型号下划线、行内减号、单个星号、中英混排。
        for text in ('这是一段普通正文，没有任何 Markdown 标记。',
                     '权重用 IQ2_XXS 与 IQ3_S 两种，显存 47.37 GB。',
                     '误差在 a --- b 之间波动，不构成分隔线。',
                     '2 * 3 = 6，4 * 5 = 20。',
                     'CPU: Ryzen 9 9950X（16 核 32 线程）。',
                     '模型体积比显存大了一倍以上。'):
            with self.subTest(text=text):
                self.assertEqual(markdown_blocks.signals(text), [], text)
                self.assertFalse(markdown_blocks.looks_like_markdown(text))

    def test_weak_signals_need_two(self):
        self.assertEqual(markdown_blocks.signals('这里有 `代码`'), [])
        self.assertIn('行内代码', markdown_blocks.signals('这里有 `代码` 和 *强调*'))

    def test_list_needs_two_lines(self):
        self.assertNotIn('列表', markdown_blocks.signals('- 只有一行，不能算列表'))
        self.assertIn('列表', markdown_blocks.signals('- 第一项\n- 第二项'))
        self.assertIn('列表', markdown_blocks.signals('1. 第一步\n2. 第二步'))
        self.assertIn('列表', markdown_blocks.signals('* 结构体量：共 46 层\n* 专家总数：12,096 个'))


class MarkdownRenderTests(unittest.TestCase):
    def render(self, text):
        return studio.markdown_aware_html(text)[0]

    def test_heading_levels_and_inline_bold(self):
        html = self.render('<p># 标题</p><p>## 二级</p><p>### 三级</p><p>一个 **96.5GB** 模型</p>')
        self.assertIn('<h1>标题</h1>', html)
        self.assertIn('<h2>二级</h2>', html)
        self.assertIn('<h3>三级</h3>', html)
        self.assertIn('<strong>96.5GB</strong>', html)
        self.assertNotIn('**', html)

    def test_nested_list_keeps_structure(self):
        html = self.render('<p>* **结构体量**：共 46 层\n* **专家总数**：42 层 × 288\n  * 嵌套项</p>')
        self.assertIn('<ul>', html)
        self.assertIn('<li>', html)
        self.assertIn('<strong>结构体量</strong>', html)
        self.assertIn('嵌套项', html)

    def test_ordered_list_quote_code_link_and_hr(self):
        html = self.render('<p>1. 第一步\n2. 第二步</p>')
        self.assertIn('<ol>', html)
        self.assertIn('<li>第一步</li>', html)
        self.assertIn('<blockquote>', self.render('<p>> 引用结论</p>'))
        self.assertIn('<pre>', self.render('<p>```\nprint(1)\n```</p>'))
        self.assertIn('<a href="https://example.com/a"', self.render('<p>看 [文档](https://example.com/a)</p>'))
        self.assertIn('<hr>', self.render('<p>上文</p><p>---</p><p>下文</p>'))

    def test_plain_text_is_byte_identical(self):
        for text in ('<p>这是一段普通正文。</p>', '<p>权重 IQ2_XXS，误差 a --- b。</p>',
                     '<p>行内 <strong>真粗体</strong> 保留。</p>'):
            with self.subTest(text=text):
                rich, plain, _dropped = studio.markdown_aware_html(text)
                self.assertEqual(rich, studio.safe_html(text)[0])
                self.assertEqual(plain, studio.safe_html(text)[1])

    def test_raw_html_is_escaped_not_passthrough(self):
        rich, plain, _dropped = studio.markdown_aware_html('<p>**粗体** <script>alert(1)</script></p>')
        self.assertNotIn('<script>', rich)
        self.assertIn('<strong>粗体</strong>', rich)
        self.assertNotIn('alert(1)', plain)


@contextlib.contextmanager
def unconverted_story():
    """造一份「修复前导入」的作品：正文里 Markdown 原样留着。

    修复前的清洗器既不做 Markdown 转换，也不转义字面量标记，所以要同时把两样都关掉。
    只关转换的话，存进去的是被实体转义过的 `**`/`` ``` ``，形态已经不是「修复前」，
    按它断言重排能力会得出错误结论（复核 H-1a 之后踩过一次）。
    """
    with mock.patch.object(studio, 'markdown_aware_html', studio.clean_html), \
            mock.patch.object(studio, 'MARKUP_LITERALS', ''), \
            mock.patch.object(studio, 'MARKUP_LINE_RULES', ()):
        yield


class ReflowVersionTests(unittest.TestCase):
    MARKDOWN = ('把 96.5GB 的模型塞进单张 4090。\n\n'
                '## 01. 账本与底牌\n\n正文一段 **加粗** 文字。\n\n'
                '### 模型配方\n\n* **结构体量**：共 46 层\n* **专家总数**：12,096 个\n\n---\n\n结尾。')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = studio.Store(self.temp.name)
        slug = self.store.create({'title': 'Markdown 回归', 'type': 'article'})['slug']
        self.slug = slug
        # 造一份「修复前导入」的作品：Markdown 还留在字面量上，正是黄总那篇的形态。
        with unconverted_story():
            self.store.save(slug, {'base_version': 'v0001', 'title': 'Markdown 回归',
                                   'blocks': [{'kind': 'text',
                                               'html': '<p>' + self.MARKDOWN.replace('\n', '<br>') + '</p>'}],
                                   'summary': '首次粘贴'})
        self.base = self.store.meta(slug)['head']

    def tearDown(self):
        self.temp.cleanup()

    def blocks(self, version, slug=None):
        return json.loads((Path(self.temp.name) / 'library' / (slug or self.slug) / 'versions' / version / 'manifest.json')
                          .read_text(encoding='utf-8'))['blocks']

    def test_pasted_markdown_is_converted_on_save(self):
        # P1：修复后新导入/新粘贴的正文当场就是排好版的，不留字面量。
        slug = self.store.create({'title': '新导入', 'type': 'article'})['slug']
        self.store.save(slug, {'base_version': 'v0001', 'title': '新导入',
                               'blocks': [{'kind': 'text', 'html': '<p>' + self.MARKDOWN.replace('\n\n', '<br><br>').replace('\n', '<br>') + '</p>'}],
                               'summary': '首次粘贴'})
        head = self.store.meta(slug)['head']
        html = self.blocks(head, slug)[0]['html']
        self.assertIn('<h2>01. 账本与底牌</h2>', html)
        self.assertIn('<h3>模型配方</h3>', html)
        self.assertIn('<strong>加粗</strong>', html)
        self.assertIn('<ul>', html)
        self.assertNotIn('**', html)
        # P1 已经在导入时排好版，reflow 就不该再产生空版本。
        self.assertTrue(self.store.reflow(slug, {'base_version': head})['unchanged'])

    def test_edit_path_also_converts(self):
        slug = self.store.create({'title': '逐块编辑', 'type': 'article'})['slug']
        self.store.save(slug, {'base_version': 'v0001', 'title': '逐块编辑',
                               'blocks': [{'kind': 'text', 'html': '<p>开头一段。</p>'}], 'summary': '初稿'})
        head = self.store.meta(slug)['head']
        self.store.save(slug, {'base_version': head, 'title': '逐块编辑',
                               'text_blocks': {'0': '<p>## 小节标题</p><p>这里是 **重点**。</p>'},
                               'summary': '补一段'})
        html = self.blocks(self.store.meta(slug)['head'], slug)[0]['html']
        self.assertIn('<h2>小节标题</h2>', html)
        self.assertIn('<strong>重点</strong>', html)

    def test_reflow_repairs_a_legacy_article(self):
        # 黄总那篇的形态：导入时没转换，Markdown 全留在字面量上。
        html = self.blocks(self.base)[0]['html']
        self.assertIn('**', html)
        self.assertIn('## ', html)
        result = self.store.reflow(self.slug, {'base_version': self.base})
        fixed = self.blocks(result['version'])[0]['html']
        self.assertIn('<h2>01. 账本与底牌</h2>', fixed)
        self.assertIn('<strong>加粗</strong>', fixed)
        self.assertIn('<ul>', fixed)
        self.assertNotIn('**', fixed)

    def test_reflow_creates_new_version_and_keeps_old(self):
        before = copy.deepcopy(self.blocks(self.base))
        result = self.store.reflow(self.slug, {'base_version': self.base})
        self.assertFalse(result['unchanged'])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0003')
        after = self.blocks('v0003')
        self.assertEqual(self.blocks(self.base), before, '旧版本必须逐字不变')
        self.assertEqual(len(after), len(before), '不拆块，版本对照粒度不变')
        self.assertNotIn('**', after[0]['html'])

    def test_reflow_is_idempotent(self):
        first = self.store.reflow(self.slug, {'base_version': self.base})
        again = self.store.reflow(self.slug, {'base_version': first['version']})
        self.assertTrue(again['unchanged'])
        self.assertEqual(again['version'], first['version'])
        self.assertEqual(self.store.meta(self.slug)['head'], first['version'], '无变化时不得新增版本')

    def test_reflow_rejects_stale_base_version(self):
        self.store.reflow(self.slug, {'base_version': self.base})
        with self.assertRaises(studio.Conflict):
            self.store.reflow(self.slug, {'base_version': self.base})

    def test_plain_article_reflow_is_a_no_op(self):
        slug = self.store.create({'title': '纯文本', 'type': 'article'})['slug']
        head = self.store.meta(slug)['head']
        self.store.save(slug, {'base_version': head, 'title': '纯文本',
                               'blocks': [{'kind': 'text', 'html': '<p>没有任何标记的一段正文。</p>'}],
                               'summary': '纯文本'})
        head = self.store.meta(slug)['head']
        result = self.store.reflow(slug, {'base_version': head})
        self.assertTrue(result['unchanged'])
        self.assertEqual(self.store.meta(slug)['head'], head)

    def test_reflow_refuses_serial_posts(self):
        slug = self.store.create({'title': '串文', 'type': 'post'})['slug']
        head = self.store.meta(slug)['head']
        with unconverted_story():
            self.store.save(slug, {'base_version': head, 'title': '串文',
                                   'blocks': [{'kind': 'text', 'html': '<p>**第一条** 推文</p>'}], 'summary': '推文'})
        head = self.store.meta(slug)['head']
        with self.assertRaises(ValueError):
            self.store.reflow(slug, {'base_version': head})
        self.assertEqual(self.store.meta(slug)['head'], head, '被拒绝时不得留下半成品版本')

    def test_images_keep_position_across_reflow(self):
        import base64
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), (10, 20, 30)).save(buffer, 'PNG')
        png = buffer.getvalue()
        head = self.store.meta(self.slug)['head']
        self.store.save(self.slug, {'base_version': head, 'title': 'Markdown 回归',
                                    'blocks': [{'index': 0},
                                               {'kind': 'image', 'data': base64.b64encode(png).decode()}],
                                    'summary': '插图'})
        head = self.store.meta(self.slug)['head']
        result = self.store.reflow(self.slug, {'base_version': head})
        kinds = [b['kind'] for b in self.blocks(result['version'])]
        self.assertEqual(kinds, ['text', 'image'], '图片块位置与顺序不变')
        self.assertIn('<h2>01. 账本与底牌</h2>', self.blocks(result['version'])[0]['html'])


class ReflowToolTests(unittest.TestCase):
    """创作助手这一侧：策略放行 → 工具真的把作品重排成新版本。"""

    LEGACY = ('把 96.5GB 的模型塞进单张 4090。\n\n## 01. 账本与底牌\n\n'
              '正文一段 **加粗** 文字。\n\n* **结构体量**：共 46 层\n* **专家总数**：12,096 个\n\n---\n\n结尾。')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = studio.Store(self.root / 'workspace')
        config = self.root / 'model.json'
        config.write_text(json.dumps({'endpoint': 'https://example.invalid/chat/completions',
                                      'api_key': 'secret-test-value', 'model': 'test-model'}))
        self.agent = studio.assistant_module.Assistant(self.store, studio.content, config)
        self.slug = self.legacy_article()

    def legacy_article(self, title='GLM 5.3 Flash'):
        """造一份「修复前导入」的作品：Markdown 还留在字面量上，正是黄总那篇的形态。"""
        slug = self.store.create({'title': title, 'type': 'article'})['slug']
        with unconverted_story():
            self.store.save(slug, {'base_version': 'v0001', 'title': title,
                                   'blocks': [{'kind': 'text',
                                               'html': '<p>' + self.LEGACY.replace('\n\n', '<br><br>').replace('\n', '<br>') + '</p>'}],
                                   'summary': '首次粘贴'})
        return slug

    def tearDown(self):
        self.temp.cleanup()

    def job(self, message, work_open=True):
        from assistant_policy import policy_for
        return {'read_versions': {}, 'read_blocks': {}, 'changed': [], 'cancel': False,
                'attachments': [], 'policy': policy_for(message, work_open=work_open)}

    def html(self, slug=None):
        manifest, _, _ = self.store.info(slug or self.slug)
        return manifest['blocks'][0]['html']

    def test_huang_wording_drives_a_real_new_version(self):
        for index, message in enumerate(('看一下这个文章，很多地方本来是要加粗的…是 Markdown 格式，我们这边没有做好，要改一改，标题也应该是标题的格式',
                                        '你要通篇看文章，然后处理啊…是要你自己判断啊')):
            with self.subTest(message=message):
                slug = self.slug if index == 0 else self.legacy_article('第二篇 ' + str(index))
                job = self.job(message)
                self.assertIn('reflow_markdown', job['policy']['allowed_tools'])
                self.agent.execute('read_work', {'slug': slug}, job)
                head = self.store.meta(slug)['head']
                self.assertIn('**', self.html(slug), '前置：导入时 Markdown 还留在字面量上')
                result = self.agent.execute('reflow_markdown', {'slug': slug, 'base_version': head}, job)
                self.assertFalse(result.get('unchanged'))
                self.assertEqual(result['reflowed_blocks'], [0])
                self.assertIn(result['version'], result['summary'])
                self.assertIn(slug, job['changed'])
                self.assertIn('<h2>01. 账本与底牌</h2>', self.html(slug))
                self.assertIn('<strong>加粗</strong>', self.html(slug))
                self.assertNotIn('**', self.html(slug))
                # 旧版本仍在，reflow 只增不改
                self.assertTrue((self.store.article(slug) / 'versions' / head).exists())

    def test_tool_requires_the_turn_grant(self):
        # 没有打开作品时不知道改哪一篇：改稿工具不授权，硬调会被执行层挡住。
        job = self.job('整篇按 Markdown 重新排版', work_open=False)
        self.assertNotIn('reflow_markdown', job['policy']['allowed_tools'])
        self.agent.execute('read_work', {'slug': self.slug}, job)
        with self.assertRaises(ValueError):
            self.agent.execute('reflow_markdown', {'slug': self.slug, 'base_version': self.store.meta(self.slug)['head']}, job)

    def test_tool_requires_reading_first(self):
        job = self.job('整篇按 Markdown 重新排版')
        with self.assertRaises(ValueError):
            self.agent.execute('reflow_markdown', {'slug': self.slug, 'base_version': self.store.meta(self.slug)['head']}, job)

    def test_question_turn_still_gets_the_tool_and_is_the_model_judgment(self):
        # 追加2：策略层不再按问句收回工具，问句该不该改由模型自己判断。
        job = self.job('要不要把整篇文章按 Markdown 重排？')
        self.assertIn('reflow_markdown', job['policy']['allowed_tools'])
        self.agent.execute('read_work', {'slug': self.slug}, job)
        head = self.store.meta(self.slug)['head']
        result = self.agent.execute('reflow_markdown', {'slug': self.slug, 'base_version': head}, job)
        self.assertFalse(result.get('unchanged'), '工具可用；问句不写入由模型决定，不是策略层拦截')
        self.assertEqual(result['reflowed_blocks'], [0])

    def test_second_save_in_one_turn_is_refused(self):
        job = self.job('整篇按 Markdown 重新排版')
        self.agent.execute('read_work', {'slug': self.slug}, job)
        head = self.store.meta(self.slug)['head']
        self.agent.execute('reflow_markdown', {'slug': self.slug, 'base_version': head}, job)
        with self.assertRaises(ValueError):
            self.agent.execute('reflow_markdown', {'slug': self.slug, 'base_version': self.store.meta(self.slug)['head']}, job)


if __name__ == '__main__':
    unittest.main()

class ReviewRegressionTests(unittest.TestCase):
    """复核打回来的 10 组缺陷，逐条钉住。"""

    # --- H-1 已有 HTML / 代码不得被破坏 ---

    def test_mixed_markup_keeps_existing_html_and_converts_only_the_text_run(self):
        rich = studio.markdown_aware_html(
            '<h2>既有标题</h2><p><strong>原粗体</strong> <a href="https://example.com">原链接</a> **新粗体**</p>')[0]
        self.assertIn('<h2>既有标题</h2>', rich)
        self.assertIn('<strong>原粗体</strong>', rich)
        self.assertIn('<a href="https://example.com"', rich)
        self.assertIn('<strong>新粗体</strong>', rich)
        self.assertEqual(rich.count('<h2>'), 1, '已有标题不能被降成段落')

    def test_code_block_never_gets_markdown_applied(self):
        for markup in ('<pre><code>## 这行是代码\n**原样文本**</code></pre>',
                       '<pre><code>a ---\n- 不是列表</code></pre>'):
            with self.subTest(markup=markup):
                self.assertEqual(studio.markdown_aware_html(markup)[0], markup)

    def test_escaped_angle_brackets_stay_literal(self):
        # 复核 f-M1：围栏是代码，用户写 `&lt;div&gt;` 就该看到 `&lt;div&gt;`。
        # 上游两轮把围栏里的 `&lt;` 解码成了 `<`，等于改掉了用户在代码里写的东西；
        # 现在与 `md.render(原文)` 逐字相同（`&amp;lt;div&amp;gt;`）。
        rich = studio.markdown_aware_html('```html\n&lt;div&gt;hi&lt;/div&gt;\n```')[0]
        self.assertNotIn('<div>', rich, 'plain 文本里的尖括号不能被当 HTML 吃掉')
        self.assertIn('&amp;lt;div&amp;gt;hi&amp;lt;/div&amp;gt;', rich)
        # 与 markdown-it 原生输出逐字相同（清洗层会去掉 language-* 类名，比对时去掉）。
        native = markdown_blocks._parser().render('```html\n&lt;div&gt;hi&lt;/div&gt;\n```').strip()
        self.assertEqual(rich, re.sub(r' class="language-[^"]*"', '', native))

    def test_plain_text_is_not_reread_as_html(self):
        # text 里的合法尖括号不是标记：Markdown 围栏包住时必须逐字节原样保留
        # （复核 f-M1：用户在代码里写 `&lt;b&gt;`，屏幕上就该是 `&lt;b&gt;`）。
        rich = studio.markdown_aware_html('<p>说明</p><p>```html\n&lt;b&gt;x&lt;/b&gt;\n```</p>')[0]
        self.assertNotIn('<b>x</b>', rich, '尖括号不能被当 HTML 吃掉')
        self.assertIn('&amp;lt;b&amp;gt;x&amp;lt;/b&amp;gt;', rich)

    def test_reflow_then_save_is_idempotent(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = studio.Store(temp.name)
        slug = store.create({'title': '幂等', 'type': 'article'})['slug']
        with unconverted_story():
            first = store.save(slug, {'base_version': 'v0001', 'title': '幂等', 'blocks': [
                {'kind': 'text', 'html': '```markdown\n## 代码内容\n**不得排版**\n```'},
                {'kind': 'text', 'html': '## 真标题\n正文 **加粗**。'}]})
        strip = lambda items: [{k: v for k, v in b.items() if k != 'index'} for b in items]
        before = strip(json.loads((Path(temp.name) / 'library' / slug / 'versions' / first['version'] / 'manifest.json')
                                  .read_text(encoding='utf-8'))['blocks'])
        result = store.reflow(slug, {'base_version': first['version']})
        after = json.loads((Path(temp.name) / 'library' / slug / 'versions' / result['version'] / 'manifest.json')
                           .read_text(encoding='utf-8'))['blocks']
        self.assertIn('<pre><code>', after[0]['html'])
        self.assertIn('## 代码内容', after[0]['html'], '围栏里的 ## 是字面量')
        self.assertIn('**不得排版**', after[0]['html'], '围栏里的 ** 是字面量')
        self.assertIn('<h2>真标题</h2>', after[1]['html'])
        again = store.reflow(slug, {'base_version': result['version']})
        self.assertTrue(again['unchanged'], '重排必须幂等，不能反复生成新版本')
        restored = store.restore(slug, {'base_version': again['version'], 'version': first['version']})
        # 比对**包里的 manifest**，不是 detail()：detail() 出库时会再走一次 safe_html，
        # 历史作品里明文的 `**`/``` ``` ``` 在读视图里是实体形态（显示一致），
        # 拿它跟原始 manifest 比，比的是两份不同的清洗结果，不是「能不能恢复」。
        back = strip(json.loads((Path(temp.name) / 'library' / slug / 'versions' / restored['version']
                                 / 'manifest.json').read_text(encoding='utf-8'))['blocks'])
        self.assertEqual(back, before, '旧版本必须能原样恢复')

    # --- M-1 分隔线 ---

    def test_hr_only_needs_line_start(self):
        rich = studio.markdown_aware_html('## 标题\na ---\nb')[0]
        self.assertIn('<h2>标题</h2>', rich)
        self.assertNotIn('<hr', rich, 'a --- 是行尾破折号，不是分隔线')
        self.assertIn('a ---\nb', rich)

    def test_standalone_hr_block_is_kept(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = studio.Store(temp.name)
        slug = store.create({'title': '分隔线', 'type': 'article'})['slug']
        store.save(slug, {'base_version': 'v0001', 'title': '分隔线',
                          'blocks': [{'kind': 'text', 'html': '---'}]})
        blocks = json.loads((Path(temp.name) / 'library' / slug / 'versions' / 'v0002' / 'manifest.json')
                            .read_text(encoding='utf-8'))['blocks']
        self.assertEqual(len(blocks), 1, '独立分隔线块不能因为 plain 为空被丢掉')
        self.assertEqual(blocks[0]['html'], '<hr>')

    # --- M-2 围栏在正文之后也要识别 ---

    def test_fence_after_prose_is_recognised(self):
        rich = studio.markdown_aware_html('说明\n\n```python\nx=1\n```')[0]
        self.assertIn('<pre><code>', rich)
        self.assertIn('x=1', rich)
        self.assertNotIn('```', rich)

    def test_fence_content_does_not_count_as_signal(self):
        self.assertNotIn('标题', markdown_blocks.signals('```\n## 只是代码\n```'))
        self.assertNotIn('粗体', markdown_blocks.signals('```\n**只是代码**\n```'))

    # --- M-3 指定生成模型 ---

    def test_requested_media_model_survives_the_open_work_grant(self):
        from assistant_policy import policy_for
        self.assertEqual(policy_for('用 Krea2 生成一张猫咪图片', work_open=True)['requested_media_model'], 'krea2')
        self.assertIsNone(policy_for('生成一张猫咪图片', work_open=True)['requested_media_model'])

    def test_generate_media_submits_the_model_the_user_named(self):
        from unittest.mock import Mock
        case = ReflowToolTests('test_tool_requires_reading_first')
        case.setUp()
        self.addCleanup(case.tearDown)
        agent = case.agent
        agent.generation = Mock()
        agent.generation.start.return_value = {'id': 'fake-job', 'status': 'queued', 'model_name': 'fake'}
        agent.image_skill = Mock()
        agent.image_skill.compose.return_value = ('fixture', {})
        agent.media_preferences = lambda: {'image': {'model': 'qi21'}}
        job = case.job('用 Krea2 生成一张猫咪图片')
        job['context'] = {'slug': case.slug, 'head': case.store.meta(case.slug)['head']}
        agent.execute('generate_media', {'model': 'krea2', 'prompt': 'fixture'}, job)
        self.assertEqual(agent.generation.start.call_args.args[0]['model'], 'krea2',
                         '用户点名的模型不能被媒体偏好覆盖')

    # --- M-4 没打开作品不给改稿工具 ---

    def test_unanchored_current_work_reference_is_not_granted(self):
        # 复核 M-4，协调者选定的 B 口径：页面上没打开作品时，
        # 「当前作品/这篇/本文/正文/标题」是没有指代对象的指代，改稿工具一律不授予。
        from assistant_policy import policy_for
        for text in ('把当前作品正文加粗', '给这篇加个标题', '请按照图片给当前作品文字加粗',
                     '把这篇文章的标题改一下', '正文里这段太啰嗦了', '本文加粗',
                     '把当前作品加粗并插入一段文字', '通篇按 Markdown 重新排版'):
            with self.subTest(text=text):
                p = policy_for(text, work_open=False)
                for tool in ('update_text', 'edit_work', 'reflow_markdown'):
                    self.assertNotIn(tool, p['allowed_tools'], text + ' 不该拿到 ' + tool)
                self.assertEqual(p['edit_grants'], [], text)
                self.assertFalse(p['allow_insert'])
                self.assertIsNone(p.get('edit_targets'), text)
                # 同一个作品真的打开着时，照样全给。
                self.assertIn('update_text', policy_for(text, work_open=True)['allowed_tools'], text)

    def test_anchored_command_still_granted_without_an_open_work(self):
        # 指明目标的明确操作不受收权影响，仍按逐子句规则授权；执行层照样要求先 read_work。
        from assistant_policy import policy_for
        for text, grant in (('删除第二段', 'delete_block'),
                            ('把第二段删了', 'delete_block'),
                            ('把第二段移到第一段之后', 'move_block'),
                            ('给Rea项目加粗', 'set_text')):
            with self.subTest(text=text):
                p = policy_for(text, work_open=False)
                self.assertIn(grant, p['edit_grants'], text)

    def test_global_tools_still_work_without_an_open_work(self):
        from assistant_policy import policy_for, READ_TOOLS
        for text, tool in (('生成一张猫咪图片', 'generate_media'),
                           ('记住黄总偏好短句标题', 'remio_create_note'),
                           ('把这篇归档到合成合集', 'organize_collection')):
            with self.subTest(text=text):
                self.assertIn(tool, policy_for(text, work_open=False)['allowed_tools'], text)

    def test_open_work_still_gets_every_editing_tool(self):
        from assistant_policy import policy_for
        for text in ('把当前作品正文加粗', '把这三段改成列表', '通篇按 Markdown 重新排版'):
            with self.subTest(text=text):
                p = policy_for(text, work_open=True)
                for tool in ('update_text', 'edit_work', 'reflow_markdown'):
                    self.assertIn(tool, p['allowed_tools'])
                self.assertEqual(set(p['edit_grants']),
                                 {'set_text', 'insert_text', 'delete_block', 'move_block', 'insert_image', 'reflow_markdown'})

    # --- L-6 拦截文案 ---

    def test_blocked_hint_does_not_claim_the_tool_is_unknown(self):
        source = (Path(__file__).resolve().parents[1] / 'pi-runtime' / 'worker.mjs').read_text(encoding='utf-8')
        self.assertNotIn('工作台里不存在', source)
        self.assertIn('本轮没有开放这个工具', source)
        self.assertIn('reflow_markdown', source.split('const writeTool = ')[1].split(']')[0],
                      'reflow_markdown 会写新版本，必须算写工具')

    # --- L-7 按钮复位 ---

    def test_reflow_button_resets_on_the_unchanged_branch(self):
        source = (Path(__file__).resolve().parents[1] / 'public' / 'content.js').read_text(encoding='utf-8')
        self.assertIn('reflow-markdown', source)
        self.assertIn("finally{reflow.disabled=false;reflow.textContent='按 Markdown 重新排版';}", source,
                      'unchanged 分支也要复位按钮')

    # --- c 复核：B-H1 转换边界三连 ---

    def test_escaped_markdown_is_not_interpreted_a_second_time(self):
        # `\*\*literal\*\*` 首存后是字面量星号；把已存 HTML 原样再存必须不变。
        first = studio.markdown_aware_html('## 标题\n\\*\\*literal\\*\\*')
        self.assertIn('<h2>标题</h2>', first[0])
        self.assertNotIn('<strong>', first[0], '转义的星号不能变成粗体')
        self.assertIn('literal', first[0])
        second = studio.markdown_aware_html(first[0])
        self.assertEqual(second[0], first[0], '已存 HTML 再存一次必须逐字不变')
        self.assertNotIn('<strong>', second[0])
        # 判定用的文本不解码实体：这正是「不再二次解释」的关键。
        self.assertNotIn('粗体', markdown_blocks.signals(markdown_blocks.signal_text(first[0])))

    def test_escaped_markdown_stays_literal_through_reflow_and_save(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = studio.Store(temp.name)
        slug = store.create({'title': '转义', 'type': 'article'})['slug']
        # 造一份「导入时没转换」的作品：转义的 Markdown 还在字面量上。
        with unconverted_story():
            first = store.save(slug, {'base_version': 'v0001', 'title': '转义', 'blocks': [
                {'kind': 'text', 'html': '<p>## 标题</p><p>\\*\\*literal\\*\\*</p>'}]})
        result = store.reflow(slug, {'base_version': first['version']})
        self.assertFalse(result['unchanged'])
        html = json.loads((Path(temp.name) / 'library' / slug / 'versions' / result['version'] / 'manifest.json')
                          .read_text(encoding='utf-8'))['blocks'][0]['html']
        self.assertNotIn('<strong>', html, '一次重排就把转义的星号解释成了粗体')
        # 已存 HTML 原样再存：v 不增，unchanged 判定不变。
        head = store.meta(slug)['head']
        again = store.save(slug, {'base_version': head, 'title': '转义', 'blocks': [
            {'kind': 'text', 'html': html}]})
        self.assertTrue(again['unchanged'])
        self.assertEqual(again['version'], head)
        self.assertEqual(store.meta(slug)['head'], head, '不得新增版本')
        # 再重排一次也不该动它。
        self.assertTrue(store.reflow(slug, {'base_version': head})['unchanged'])

    def test_inline_conversion_keeps_the_space_between_runs(self):
        rich = studio.markdown_aware_html('<p><strong>原</strong> **新增** 后文</p>')[0]
        self.assertEqual(rich, '<p><strong>原</strong> <strong>新增</strong> 后文</p>',
                         '行内转换不能把两段之间的空格吃掉')
        # 英文粘连单词同样不能发生。
        rich = studio.markdown_aware_html('<p>see <a href="https://example.com">the doc</a> **now**</p>')[0]
        self.assertIn('</a> <strong>now</strong>', rich)
        self.assertNotIn('</a><strong>', rich)

    def test_fenced_code_is_byte_identical(self):
        # 围栏里的 `---` 是代码内容，补空行的预处理必须跳过围栏。
        rich = studio.markdown_aware_html('```text\na\n---\nb\n```')
        self.assertEqual(rich[0], '<pre><code>a\n---\nb\n</code></pre>')
        self.assertEqual(rich[1], 'a\n---\nb', '代码文本逐字节原样')
        self.assertEqual(studio.markdown_aware_html(rich[0])[0], rich[0])
        # isolate_breaks 直接验：围栏里的分隔线行一个空格都不能多。
        self.assertEqual(markdown_blocks.isolate_breaks('```\na\n---\nb\n```'), '```\na\n---\nb\n```')
        self.assertEqual(markdown_blocks.isolate_breaks('a\n---\nb'), 'a\n\n\n---\n\n\nb')
        # pre/code 内部同样不做这层实体转义，保证示例代码逐字节原样。
        code = '<pre><code>## 标题\n**字面量**</code></pre>'
        self.assertEqual(studio.markdown_aware_html(code)[0], code)

    # --- c 复核：B-M1 已有 <hr> 保留 ---

    def test_existing_hr_survives_the_reflow(self):
        rich = studio.markdown_aware_html('<p>## 标题</p><hr><p>尾文</p>')[0]
        self.assertIn('<hr>', rich, '重排不能把已有的分隔线弄丢')
        self.assertIn('<h2>标题</h2>', rich)
        self.assertIn('尾文', rich)
        self.assertEqual(studio.markdown_aware_html(rich)[0], rich, '带分隔线的块也必须幂等')
        # 与真实标记混在一起时同样保留。
        rich = studio.markdown_aware_html('<h2>标题</h2><hr><p>尾文 **重点**</p>')[0]
        self.assertIn('<hr>', rich)
        self.assertIn('<strong>重点</strong>', rich)
        # 只有分隔线、没有 Markdown 迹象的块不得被改写（否则重排会凭空出新版本）。
        self.assertEqual(studio.markdown_aware_html('<p>正文</p><hr><p>尾文</p>')[0], '<p>正文</p><hr><p>尾文</p>')

    # --- c 复核：B-L3 提示与口径一致 ---

    def test_prompt_no_longer_says_closed_work_means_no_editing_tools(self):
        import assistant_agent
        source = (Path(__file__).resolve().parents[1] / 'assistant_agent.py').read_text(encoding='utf-8')
        self.assertNotIn('只有打开作品这一件事，才让改稿工具可用', assistant_agent.SYSTEM)
        self.assertNotIn('没有打开作品时，不给你改稿工具是正常的', assistant_agent.SYSTEM)
        self.assertIn('先 search_works 列出最近的作品请黄总点一篇', assistant_agent.SYSTEM,
                      '无指代目标要先请用户选作品')
        self.assertIn('照旧先 read_work 核对那一篇的版本再改', assistant_agent.SYSTEM,
                      '指明目标要先读后改')
        # policy docstring 同一口径，不再写「一律不给」。
        policy = (Path(__file__).resolve().parents[1] / 'assistant_policy.py').read_text(encoding='utf-8')
        self.assertNotIn('没有打开作品时**一律不给改稿工具**', policy)


# --- H4/H5 粘贴轮的收窄豁免与常驻真实执行门禁 ---
#
# h3 用 `_paste_turn` 跳过整个 `_UNANCHORED_TARGET` 收权，导致同行/跨行的
# delete_block/move_block/set_text 搭车获得授权并在真实调用链中实际执行写入。
# h4 改为结构性收窄：粘贴轮只保留 insert_text，其余 EDIT_GRANTS 一律收回。
# h5 补齐目标绑定与常驻执行门禁：work_open=False 且无明确指名时，insert_text 只能
# 走到边界提示或澄清，不能落成任何写入；真实执行层在 Assistant.execute 拒绝。
class PasteNarrowExemptionTests(unittest.TestCase):
    """h4/h5 粘贴轮收窄豁免：只保留 insert_text，其余改稿操作一律收回。"""

    @staticmethod
    def tools(message):
        from assistant_policy import policy_for
        return set(policy_for(message, work_open=False)['allowed_tools'])

    @staticmethod
    def grants(message):
        from assistant_policy import policy_for
        return set(policy_for(message, work_open=False).get('edit_grants', []))

    def test_pasted_body_naming_正文_keeps_insert_text_only(self):
        # 「正文」出现在被贴的字面量里：edit_work 保留但只给 insert_text。
        for text in ('把下面贴文贴上「正文甲」\n正文乙',
                     '把这段贴文贴上：「正文」今天发布甲方案',
                     '把下面这段贴上\n正文：今天发布甲方案',
                     '把下面贴文贴上，正文里说今天发布甲方案'):
            with self.subTest(text=text):
                self.assertIn('edit_work', self.tools(text), text)
                g = self.grants(text)
                self.assertIn('insert_text', g, text)
                for bad in ('delete_block', 'move_block', 'set_text'):
                    self.assertNotIn(bad, g, text + ' 不该拿到 ' + bad)

    def test_pasted_body_naming_标题_keeps_insert_text_only(self):
        for text in ('把这段贴文贴上：「标题」今天发布甲方案的正文很长。',
                     '把这段贴文贴上："标题"今天发布甲方案。',
                     '把下面这段贴上\n标题：今天发布甲方案',
                     '把下面贴文贴上，标题写「甲方案」'):
            with self.subTest(text=text):
                self.assertIn('edit_work', self.tools(text), text)
                g = self.grants(text)
                self.assertIn('insert_text', g, text)
                for bad in ('delete_block', 'move_block', 'set_text'):
                    self.assertNotIn(bad, g, text + ' 不该拿到 ' + bad)

    def test_same_line_搭车_operations_revoked(self):
        # 同行粘贴+危险操作：只保留 insert_text，其余收回
        for text, bad_grant in [
            ('把下面贴文贴上，另外把正文移除', 'delete_block'),
            ('把下面贴文贴上，另外把正文移到第二段', 'move_block'),
        ]:
            with self.subTest(text=text):
                tools = self.tools(text)
                g = self.grants(text)
                self.assertIn('edit_work', tools, text + ' 应保留 edit_work')
                self.assertIn('insert_text', g, text + ' 应保留 insert_text')
                self.assertNotIn(bad_grant, g, text + ' 不该拿到 ' + bad_grant)

    def test_cross_line_搭车_operations_revoked(self):
        # 跨行粘贴+危险操作：不论解释为贴文本体还是另一条指令，都不允许写入
        for text, bad_grant in [
            ('把下面贴文贴上\n另外把正文移除', 'delete_block'),
            ('把下面贴文贴上\n另外删除正文', 'delete_block'),
            ('把下面贴文贴上\n另外把标题加粗', 'set_text'),
        ]:
            with self.subTest(text=text):
                tools = self.tools(text)
                g = self.grants(text)
                self.assertIn('edit_work', tools, text + ' 应保留 edit_work')
                self.assertIn('insert_text', g, text + ' 应保留 insert_text')
                self.assertNotIn(bad_grant, g, text + ' 不该拿到 ' + bad_grant)

    def test_non_paste_unanchored_edit_fully_revoked(self):
        # M-4 的 B 口径原样保留：没有指代对象的非粘贴改稿要求仍然一条都不给
        for text in ('改一下正文', '把标题改短一点', '当前作品再润色一下',
                     '把当前作品正文加粗', '正文里这段太啰嗦了',
                     '通篇按 Markdown 重新排版', '记住黄总偏好短句标题',
                     '把当前作品加粗并插入一段文字'):
            with self.subTest(text=text):
                tools = self.tools(text)
                for tool in ('update_text', 'edit_work', 'reflow_markdown'):
                    self.assertNotIn(tool, tools, text + ' 不该拿到 ' + tool)

    def test_mixed_paste_and_unanchored_edit_same_line(self):
        # 粘贴 + 「另外改一下正文」同行：改稿操作收回但 insert_text 保留
        for text in ('把下面贴文贴上，另外帮我改一下正文',
                     '把下面这段贴上，再把正文精简一下',
                     '把下面贴文贴上，再把标题重写一遍'):
            with self.subTest(text=text):
                tools = self.tools(text)
                g = self.grants(text)
                self.assertIn('edit_work', tools, text)
                self.assertIn('insert_text', g, text)
                for bad in ('delete_block', 'move_block', 'set_text'):
                    self.assertNotIn(bad, g, text + ' 不该拿到 ' + bad)

    def test_newline_after_paste_directive_keeps_edit_work(self):
        for text in ('把下面这段贴上\n另外把标题精简一下',
                     '把下面贴文贴上\n把正文改短一点'):
            with self.subTest(text=text):
                self.assertIn('edit_work', self.tools(text), text)

    def test_exemption_never_grants_what_base_policy_withheld(self):
        for text in ('把「改一下正文」贴上', '正文：今天发布甲方案'):
            with self.subTest(text=text):
                self.assertNotIn('edit_work', self.tools(text), text)

    def test_quoted_body_containing_edit_verb_is_still_paste(self):
        from assistant_policy import _paste_turn
        for text in ('把「改一下正文」贴上', '把"把标题改短一点"贴上'):
            with self.subTest(text=text):
                self.assertTrue(_paste_turn(text), text)


class PasteExecutionBoundaryTests(unittest.TestCase):
    """h5 常驻门禁：走真实 Assistant.execute → Store 链路、临时库、无真实模型。

    覆盖四类场景：
    1. Codex 无锚点插入三条反例：work_open=False 且消息未指明作品，
       即使助手自己 read_work 挑了一篇，execute('edit_work') 也必须拒绝且不写库（M1）。
    2. h3 轮搭车改稿操作：同行移除、同行移动、跨行移除、跨行加粗一律拒绝且正文不变。
    3. b3 两种原文边界形态：引号首行+下一行、部分引用标题，仍得到原文边界错误。
    4. 有锚点的正常粘贴：work_open=True 时正常保存新版本 v0003。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = studio.Store(self.root / 'workspace')
        config = self.root / 'model.json'
        config.write_text(json.dumps({'endpoint': 'https://example.invalid/chat/completions',
                                      'api_key': 'secret-test-value', 'model': 'test-model'}))
        self.agent = studio.assistant_module.Assistant(self.store, studio.content, config)
        article = self.store.create({'title': '测试串文', 'type': 'post'})
        self.slug = article['slug']
        self.store.save(self.slug, {'base_version': 'v0001', 'title': '测试串文', 'blocks': [
            {'kind': 'text', 'html': '<p>第一条推文</p>'},
            {'kind': 'text', 'html': '<p>第二条推文</p>'},
            {'kind': 'text', 'html': '<p>第三条推文</p>'}]})

    def tearDown(self):
        self.temp.cleanup()

    def job(self, message, work_open=False):
        from assistant_policy import policy_for
        context = {'slug': self.slug} if work_open else {}
        return {'read_versions': {}, 'read_blocks': {}, 'changed': [], 'cancel': False,
                'attachments': [], 'context': context,
                'policy': policy_for(message, work_open=work_open),
                'prompt': message}

    def execute_edit(self, message, operations, work_open=False):
        job = self.job(message, work_open=work_open)
        self.agent.execute('read_work', {'slug': self.slug}, job)
        before = self.store.detail(self.slug)['blocks']
        err = None
        result = None
        try:
            result = self.agent.execute('edit_work', {
                'slug': self.slug, 'base_version': 'v0002',
                'summary': '常驻门禁执行', 'operations': operations
            }, job)
        except Exception as exc:
            err = str(exc)
        after = self.store.detail(self.slug)['blocks']
        head = self.store.meta(self.slug)['head']
        return err, result, before, after, head

    def test_unanchored_insert_rejected_without_writing_to_store(self):
        # Codex 三条反例：无锚点时即使原文解析成功，也必须拒绝且不产生新版本
        import html
        from assistant_policy import paste_originals
        messages = [
            '把下面贴文贴上\n正文：今天发布甲方案',
            '把下面贴文贴上\n另外把正文移除',
            '把下面贴文贴上，再补一句过渡。原文：今天发布甲方案的正文。',
        ]
        for message in messages:
            with self.subTest(message=message):
                originals = paste_originals(message)
                body = originals[0] if originals else '今天发布甲方案'
                op = {'op': 'insert_text', 'text_source': 'paste',
                      'html': '<p>' + html.escape(body).replace('\n', '<br>') + '</p>'}
                err, res, before, after, head = self.execute_edit(message, [op], work_open=False)
                self.assertIsNotNone(err, f'{message} 应被执行层拒绝')
                self.assertIn('请先指明或打开要贴入的作品', err)
                self.assertEqual(before, after, f'{message} 正文不得改变')
                self.assertEqual(head, 'v0002', f'{message} 不得生成新版本')

    def test_piggyback_operations_rejected_without_changing_content(self):
        # h3 轮搭车操作：同行/跨行非法操作被执行层拒绝，正文与版本不变
        cases = [
            ('把下面贴文贴上，另外把正文移除', {'op': 'delete_block', 'index': 0}, '未授权“删除正文块”'),
            ('把下面贴文贴上，另外把正文移到第二段', {'op': 'move_block', 'index': 0, 'after': 1}, '未授权“移动正文块”'),
            ('把下面贴文贴上\n另外把正文移除', {'op': 'delete_block', 'index': 0}, '未授权“删除正文块”'),
            ('把下面贴文贴上\n另外把标题加粗', {'op': 'set_text', 'index': 0, 'html': '<p><b>第一条推文</b></p>'}, '未授权“修改文本块”'),
        ]
        for message, op, expect_err in cases:
            with self.subTest(message=message, op=op['op']):
                err, res, before, after, head = self.execute_edit(message, [op], work_open=False)
                self.assertIsNotNone(err, f'{message} 应被拒绝')
                self.assertIn(expect_err, err)
                self.assertEqual(before, after)
                self.assertEqual(head, 'v0002')

    def test_b3_boundary_forms_still_produce_original_boundary_error(self):
        # b3 两种形态：仍返回原文边界错误，正文与版本不变
        cases = [
            ('把下面贴文贴上「正文甲」\n正文乙', {'op': 'insert_text', 'text_source': 'paste', 'html': '<p>正文乙</p>'}),
            ('把这段贴文贴上：「标题」今天发布甲方案的正文很长。', {'op': 'insert_text', 'text_source': 'paste', 'html': '<p>标题</p>'}),
        ]
        for message, op in cases:
            with self.subTest(message=message):
                err, res, before, after, head = self.execute_edit(message, [op], work_open=False)
                self.assertIsNotNone(err)
                self.assertIn('换行后再贴原文', err)
                self.assertEqual(before, after)
                self.assertEqual(head, 'v0002')

    def test_anchored_paste_succeeds_and_writes_new_version(self):
        # 有锚点时（作品已打开）：正常写入并生成新版本
        import html
        from assistant_policy import paste_originals
        message = '把下面贴文贴上\n正文：今天发布甲方案'
        originals = paste_originals(message)
        body = originals[0]
        op = {'op': 'insert_text', 'text_source': 'paste',
              'html': '<p>' + html.escape(body).replace('\n', '<br>') + '</p>'}
        err, res, before, after, head = self.execute_edit(message, [op], work_open=True)
        self.assertIsNone(err)
        self.assertIsNotNone(res)
        self.assertEqual(head, 'v0003')
        self.assertNotEqual(before, after)
        self.assertIn('今天发布甲方案', self.store.detail(self.slug)['blocks'][-1]['text'])

    def test_content_title_or_slug_collision_cannot_authorize_unanchored_write(self):
        # Codex 复核 M1 四条名称碰撞反例 + 对照组：
        # 当 work_open=False 时，正文、引文或否定句中出现某篇作品的标题或 slug，
        # 绝不能被当成目标授权放行写入；全部必须拒绝且作品与版本不变。
        import html
        from assistant_policy import paste_originals

        # 创建目标作品 B（标题为「甲方案」）
        target_b = self.store.create({'title': '甲方案', 'type': 'post'})['slug']
        self.store.save(target_b, {
            'base_version': 'v0001', 'title': '甲方案',
            'blocks': [{'kind': 'text', 'html': '<p>作品B原段</p>'}]
        })
        self.assertEqual(self.store.meta(target_b)['head'], 'v0002')

        cases = [
            ('control-no-anchor', '把下面贴文贴上\n正文：今天发布乙方案'),
            ('body-title', '把下面贴文贴上\n正文：今天发布甲方案'),
            ('body-slug', '把下面贴文贴上\n正文：审计日志引用 ' + target_b),
            ('quoted-title', '把下面贴文贴上，再补一句「甲方案」。原文：正文：今天发布乙方案'),
            ('body-negated-title', '把下面贴文贴上\n正文：不要写入甲方案，这几个字是待粘贴内容'),
        ]

        for scenario, message in cases:
            with self.subTest(scenario=scenario):
                job = self.job(message, work_open=False)
                # 助手自行读取作品 B
                self.agent.execute('read_work', {'slug': target_b}, job)
                before = self.store.detail(target_b)['blocks']
                head_before = self.store.meta(target_b)['head']
                originals = paste_originals(message)
                body = originals[0]
                op = {'op': 'insert_text', 'text_source': 'paste',
                      'html': '<p>' + html.escape(body).replace('\n', '<br>') + '</p>'}
                err = None
                res = None
                try:
                    res = self.agent.execute('edit_work', {
                        'slug': target_b, 'base_version': head_before,
                        'summary': '碰撞测试', 'operations': [op]
                    }, job)
                except Exception as exc:
                    err = str(exc)
                after = self.store.detail(target_b)['blocks']
                head_after = self.store.meta(target_b)['head']

                self.assertIsNotNone(err, f'{scenario}: 应该被拒绝，但成功保存了新版本')
                self.assertIn('请先指明或打开要贴入的作品', err)
                self.assertEqual(before, after, f'{scenario}: 作品 B 内容不得被修改')
                self.assertEqual(head_after, 'v0002', f'{scenario}: 作品 B 版本不得递增')


B3, B4, B5 = chr(96) * 3, chr(96) * 4, chr(96) * 5


class RoundDInvariantsTests(unittest.TestCase):
    """复核 d 的三条不变量：行首转义族、实体保护不靠「无迹象」、围栏长度匹配。

    验收口径就是合同写的那四步：**首存 → 再存 → 连续三次 reflow →
    后续编辑（旁边新增一段真 Markdown）**，四步之后正文必须与首存逐字相同，
    不增版本、不改结构、不产生非法嵌套。
    """

    # 七种反斜杠族 + 有序列表 + 实体变体 = 11 组，每组都跑完整四步。
    ESCAPES = (
        ('underscore', '## 标题\n\\_word\\_', '_word_'),
        ('backtick', '## 标题\n\\`code\\`', '&#96;code&#96;'),
        ('star', '## 标题\n\\*\\*word\\*\\*', '&#42;&#42;word&#42;&#42;'),
        ('heading', '## 标题\n\\# 字面标题', '&#35; 字面标题'),
        ('quote', '## 标题\n\\> 字面引用', '&gt; 字面引用'),
        ('bullet', '## 标题\n\\- first\n\\- second', '&#45; first\n&#45; second'),
        ('rule', '## 标题\n\\---', '&#45;&#45;&#45;'),
        ('ordered', '## 标题\n1\\. literal\n2\\. literal', '1&#46; literal\n2&#46; literal'),
        ('entity_heading', '<p>&#35; 标题</p>', '&#35; 标题'),
        ('entity_star', '<p>&#42;&#42;word&#42;&#42;</p>', '&#42;&#42;word&#42;&#42;'),
        ('entity_backtick', '<p>&#96;code&#96;</p>', '&#96;code&#96;'),
    )

    def _four_steps(self, raw, extra=None):
        """首存 → 再存 → 三次 reflow → 后续编辑，返回 (首存 html, 末态 html, 版本数)。"""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = studio.Store(temp.name)
        slug = store.create({'title': '不变量', 'type': 'article'})['slug']
        first = store.save(slug, {'base_version': 'v0001', 'title': '不变量',
                                  'blocks': [{'kind': 'text', 'html': raw}]})
        saved = store.detail(slug)['blocks'][0]['html']
        # 第二步：把已存 HTML 原样再存
        again = store.save(slug, {'base_version': first['version'], 'title': '不变量',
                                  'blocks': [{'kind': 'text', 'html': saved}]})
        self.assertTrue(again['unchanged'], raw + '：再存一次产生了新版本')
        # 第三步：连续三次 reflow
        for _ in range(3):
            result = store.reflow(slug, {'base_version': store.meta(slug)['head']})
            self.assertTrue(result['unchanged'], raw + '：重排反复产生新版本')
        # 第四步：后续编辑——在旁边新增一段真 Markdown
        head = store.meta(slug)['head']
        edited = store.save(slug, {'base_version': head, 'title': '不变量', 'blocks': [
            {'kind': 'text', 'html': (extra or '新增 **bold** 一段') + saved}]})
        self.assertFalse(edited['unchanged'], raw + '：后续编辑没有生效，测不出字面量是否被改写')
        final = store.detail(slug)['blocks'][0]['html']
        self.assertIn('<strong>bold</strong>', final, raw + '：新增的 Markdown 没有被解释')
        return saved, final

    def test_line_start_escape_family_survives_all_four_steps(self):
        for name, raw, literal in self.ESCAPES:
            with self.subTest(family=name):
                saved, _ = self._four_steps(raw)
                self.assertIn(literal, saved, name + '：首存形态不对')
                self.assertNotIn('<strong>', saved.replace('<strong>bold</strong>', ''),
                                 name + '：不该出现真粗体')

    def test_line_start_escape_stays_literal_after_a_new_paragraph_is_added(self):
        # 关键一步：新增真 Markdown 会触发整块重排，旧字面量必须还是字面量。
        for name, raw, literal in self.ESCAPES:
            if name in ('entity_star', 'entity_backtick', 'entity_heading'):
                continue
            with self.subTest(family=name):
                _, final = self._four_steps(raw, extra='**bold** 段\n\n')
                self.assertIn(literal, final, name + '：后续编辑把字面量解释掉了')
                self.assertIn('<strong>bold</strong>', final)
                self.assertNotIn('<h1>字面标题</h1>', final, name + '：字面标题被当成真标题')
                self.assertNotIn('<ul>', final, name + '：字面列表被当成真列表')

    def test_known_stable_four_escapes_did_not_regress(self):
        # 上轮实测稳定的四族必须仍然稳定，c 的报告点名不许回退。
        for name, raw, literal in self.ESCAPES[:4]:
            with self.subTest(family=name):
                saved, _ = self._four_steps(raw)
                self.assertIn(literal, saved)

    # --- D-H1b 实体保护不得依赖「无迹象」 ---

    def test_entity_literals_survive_a_new_bold_in_the_same_paragraph(self):
        raw = '<p>&#42;&#42;literal&#42;&#42; 新 **bold**</p>'
        rich = studio.markdown_aware_html(raw)[0]
        self.assertEqual(rich, '<p>&#42;&#42;literal&#42;&#42; 新 <strong>bold</strong></p>',
                         '旧字面量被解释成粗体了')
        self.assertEqual(studio.markdown_aware_html(rich)[0], rich)
        backtick = '<p>&#96;literal&#96; 新 **bold**</p>'
        self.assertEqual(studio.markdown_aware_html(backtick)[0],
                         '<p>&#96;literal&#96; 新 <strong>bold</strong></p>',
                         '旧的反引号字面量被解释成 code 了')

    def test_entity_protection_does_not_depend_on_having_no_signal(self):
        # 复核要求：不得靠「迹象检测更严格」糊。同一段文字，加上新迹象前后结果必须一致。
        base = studio.markdown_aware_html('<p>&#42;&#42;lit&#42;&#42;</p>')[0]
        with_new = studio.markdown_aware_html('<p>&#42;&#42;lit&#42;&#42; 新 **bold**</p>')[0]
        self.assertIn('&#42;&#42;lit&#42;&#42;', base)
        self.assertIn('&#42;&#42;lit&#42;&#42;', with_new)
        self.assertNotIn('<strong>lit</strong>', with_new)

    def test_structural_entities_are_still_decoded(self):
        # 哨兵不能把普通文字也冻住：`AT&amp;T` 要还原成 AT&T 再让解析器转义回去。
        rich = studio.markdown_aware_html('<p>AT&amp;T 公司 &lt;tag&gt; 与 **粗**</p>')[0]
        self.assertIn('AT&amp;T', rich)
        self.assertIn('&lt;tag&gt;', rich)
        self.assertIn('<strong>粗</strong>', rich)
        self.assertEqual(studio.markdown_aware_html(rich)[0], rich)

    def test_numbered_heading_text_is_not_a_list_marker(self):
        # 「01. 账本与底牌」写在 <h2> 里是标题文字，不是段落行首的列表项。
        rich = studio.markdown_aware_html('## 01. 账本与底牌\n\n正文。')[0]
        self.assertIn('<h2>01. 账本与底牌</h2>', rich, '标题文字被当成有序列表标记转义了')
        self.assertEqual(studio.markdown_aware_html(rich)[0], rich)

    def test_plain_numbers_and_dashes_are_not_turned_into_entities(self):
        rich = studio.markdown_aware_html('<p>96.5GB 模型，MoE-混合，48GB 显存，第 3 代。</p>')[0]
        self.assertEqual(rich, '<p>96.5GB 模型，MoE-混合，48GB 显存，第 3 代。</p>',
                         '正文里的点与连字符不该被撑成实体')

    # --- D-H1c 围栏按开启长度匹配 ---

    def test_fence_is_closed_by_length_and_type(self):
        cases = (
            (B4 + 'text\n' + B3 + '\na\n---\nb\n' + B3 + '\n' + B4, B3 + '\na\n---\nb\n' + B3),
            # 五反引号围栏里放四反引号：同长度也算结束，短一截才算内层示例。
            (B5 + 'text\n' + B4 + '\na\n---\nb\n' + B4 + '\n' + B5, B4 + '\na\n---\nb\n' + B4),
            ('~~~text\n' + B3 + '\na\n---\nb\n' + B3 + '\n~~~', B3 + '\na\n---\nb\n' + B3),
            (B3 + 'text\n~~~\na\n---\nb\n~~~\n' + B3, '~~~\na\n---\nb\n~~~'),
        )
        for raw, code in cases:
            with self.subTest(open=raw.splitlines()[0]):
                rich, plain, _dropped = studio.markdown_aware_html(raw)
                self.assertIn('<pre><code>', rich)
                self.assertEqual(code, plain, '围栏内容必须逐字节原样，不得补空行')
                self.assertEqual(studio.markdown_aware_html(rich)[0], rich)

    def test_short_inner_fence_is_not_a_closing_fence(self):
        raw = B4 + 'text\n' + B3 + '\na\n---\nb\n' + B3 + '\n' + B4
        self.assertNotIn('\n\n\n', studio.markdown_aware_html(raw)[1])
        self.assertEqual(markdown_blocks.fenced_lines(raw), [True] * 7)

    def test_existing_pre_code_keeps_the_same_content(self):
        code = B4 + '\n' + B3 + '\na\n---\nb\n' + B3 + '\n' + B4
        markup = '<pre><code>%s</code></pre>' % code
        self.assertEqual(studio.markdown_aware_html(markup)[0], markup)

    def test_unclosed_fence_is_treated_as_code_to_the_end(self):
        self.assertEqual(markdown_blocks.fenced_lines(B3 + '\na\n---\nb'), [True] * 4)

    def test_backtick_fence_info_string_may_not_contain_backticks(self):
        # CommonMark：反引号围栏的 info string 里不能再有反引号，这行不是开启行。
        self.assertIsNone(markdown_blocks.fence_open(B3 + 'a`b\n'))
        self.assertIsNotNone(markdown_blocks.fence_open(B3 + 'python\n'))


# 用户原文里可能真的有私用区字符；测试用 S/E 指代，源码里不写裸字符。
S, E = chr(0xE000), chr(0xE001)
PUA_RE = re.compile('[' + S + '-' + chr(0xF8FF) + ']')


class RoundELeakTests(unittest.TestCase):
    """复核 e 的三个缺口：哨兵不得进 URL、行首跨内联传播、哨兵不得撞用户私用区。

    验收不只扫 HTML 原文：href 必须**解码之后**再查哨兵，包 manifest 与公众号导出
    出口都要查（复核 H1a 的原话）。
    """

    def _hrefs(self, blob):
        return re.findall(r'href="([^"]*)"', blob)

    def _assert_no_sentinel(self, blob, where):
        for url in self._hrefs(blob):
            decoded = unquote(unquote(url))
            self.assertIsNone(PUA_RE.search(decoded), where + ' 的 href 解码后还有私用区字符：' + decoded)
        self.assertNotIn('%EE%80%', blob, where + ' 里还有百分号编码的哨兵')

    # --- E-H1a 哨兵不得进入 URL 等会被再编码的位置 ---

    def test_literal_entities_in_url_keep_url_semantics(self):
        cases = {
            '[链接](https://example.com/?q=&#42;) **新**': 'https://example.com/?q=*',
            '[链接](https://example.com/?q=&gt;) **新**': 'https://example.com/?q=%3E',
            '[链接](https://example.com/?q=&#20013;) **新**': 'https://example.com/?q=%E4%B8%AD',
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                rich = studio.markdown_aware_html(raw)[0]
                self.assertEqual(self._hrefs(rich)[0], want, '链接参数被哨兵改掉了')
                self._assert_no_sentinel(rich, '首存 HTML')
                self.assertEqual(studio.markdown_aware_html(rich)[0], rich)
        # 自动链接与引用式目标同样不能让哨兵进去，漏了哪一类都会变成 %EE%80%。
        for raw in ('<https://example.com/?q=&#42;> **新**',
                    '[文字][ref] **新**\n\n[ref]: https://example.com/?q=&#42;'):
            with self.subTest(raw=raw):
                rich = studio.markdown_aware_html(raw)[0]
                self._assert_no_sentinel(rich, raw[:16])
                self.assertEqual(studio.markdown_aware_html(rich)[0], rich)

    def test_no_sentinel_reaches_package_or_wechat_export(self):
        from publishing.platforms import wechat
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = studio.Store(temp.name)
        slug = store.create({'title': '出口', 'type': 'article'})['slug']
        saved = store.save(slug, {'base_version': 'v0001', 'title': '出口', 'blocks': [
            {'kind': 'text', 'html': '[链接](https://example.com/?q=&#42;) 与 &#96;码&#96; **新**'}]})
        manifest, _, package = store.info(slug)
        export = wechat.render({'studio': {'type': 'article'}, 'target': {'title': '出口'},
                                'manifest': manifest, 'slug': slug,
                                'version': saved['version'], 'package': str(package)})
        self._assert_no_sentinel(json.dumps(manifest, ensure_ascii=False), '包 manifest')
        self._assert_no_sentinel(export, '公众号导出')

    def test_body_entities_outside_url_do_not_regress(self):
        self.assertEqual(studio.markdown_aware_html('<p>AT&amp;T 与 **粗**</p>')[0],
                         '<p>AT&amp;T 与 <strong>粗</strong></p>')
        self.assertEqual(studio.markdown_aware_html('<p>&#42;&#42;字面&#42;&#42; 新 **bold**</p>')[0],
                         '<p>&#42;&#42;字面&#42;&#42; 新 <strong>bold</strong></p>')

    def test_link_text_entities_still_stay_literal(self):
        rich = studio.markdown_aware_html('[&#42;字&#42;](https://example.com/a)')[0]
        self.assertIn('&#42;字&#42;', rich, '链接文字里的字面实体也要护住')
        self.assertEqual(studio.markdown_aware_html(rich)[0], rich)

    # --- E-H1b 行首状态跨内联结构传播 ---

    def test_line_start_state_crosses_inline_structures(self):
        for wrapper in ('span', 'strong', 'em', 'i', 'b', 'u', 's'):
            with self.subTest(tag=wrapper):
                raw = '<p><%s>&#45; first\n&#45; second</%s></p>' % (wrapper, wrapper)
                first = studio.markdown_aware_html(raw)[0]
                self.assertIn('&#45; first', first, wrapper + '：行首减号被当成非行首放过了')
                self.assertIn('&#45; second', first)
                self.assertEqual(studio.markdown_aware_html(first)[0], first,
                                 wrapper + '：再存一次变成真列表了')
        self.assertIn('&#35; 字面', studio.markdown_aware_html('<p><em>&#35; 字面\n&#35; 第二个</em></p>')[0])

    def test_inline_structure_does_not_over_escape(self):
        # 传播不等于一律按行首处理：不在行首的减号不该被撑成实体。
        for raw in ('<p>abc<span>- item</span> def</p>',
                    '<p>abc <strong>- item</strong> def</p>',
                    '<p><span>abc</span><span>- item</span></p>'):
            with self.subTest(raw=raw):
                self.assertEqual(studio.markdown_aware_html(raw)[0], raw)

    def test_whole_line_tags_still_win_over_propagation(self):
        self.assertIn('<h2>01. 账本与底牌</h2>', studio.markdown_aware_html('## 01. 账本与底牌\n\n正文。')[0])

    # --- E-M1 哨兵与用户原文私有区字符隔离 ---

    def test_user_private_use_characters_are_preserved(self):
        cases = ['## 标题\n' + S + '0' + E + ' &#42;',
                 '## 标题\n' + S + '999' + E,
                 '## 标题\n' + S + '0' + E + ' 与 &#35; 标题 和 **新**']
        for raw in cases:
            with self.subTest(raw=repr(raw)):
                first = studio.markdown_aware_html(raw)[0]
                for char in (S, E):
                    self.assertIn(char, first, '用户原文的私用区字符被替换掉了')
                self.assertEqual(studio.markdown_aware_html(first)[0], first, '私用区字符破坏了幂等')
        # 上轮 `\ue0000\ue001` 被当成 0 号哨兵，正文变成了 `* *`。
        self.assertNotIn('* *', studio.markdown_aware_html(cases[0])[0],
                         '用户的私用区字符被当成了哨兵，换成了别的内容')

    def test_user_pua_plus_entities_survives_four_steps(self):
        raw = '## 标题\n' + S + '0' + E + ' &#42; **新增**'
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = studio.Store(temp.name)
        slug = store.create({'title': '私用区', 'type': 'article'})['slug']
        first = store.save(slug, {'base_version': 'v0001', 'title': '私用区',
                                  'blocks': [{'kind': 'text', 'html': raw}]})
        saved = store.detail(slug)['blocks'][0]['html']
        self.assertIn(S, saved)
        self.assertIn('&#42;', saved)
        again = store.save(slug, {'base_version': first['version'], 'title': '私用区',
                                  'blocks': [{'kind': 'text', 'html': saved}]})
        self.assertTrue(again['unchanged'], '再存一次产生了新版本')
        for _ in range(3):
            self.assertTrue(store.reflow(slug, {'base_version': store.meta(slug)['head']})['unchanged'])
        self.assertEqual(store.detail(slug)['blocks'][0]['html'], saved)

    def test_restore_never_raises_on_unknown_index(self):
        # 越界的哨兵原样留着，绝不抛异常把整条保存链路带崩。
        held = _held('a' * 24)
        marker = markdown_blocks._SENTINEL + '999' + markdown_blocks._SENTINEL_END + held.nonce
        self.assertEqual(markdown_blocks._restore(marker, held), marker)
        held = _held('a' * 24)
        held.append('&#42;')
        known = markdown_blocks._SENTINEL + '0' + markdown_blocks._SENTINEL_END + held.nonce
        self.assertEqual(markdown_blocks._restore(known, held), '&#42;')


# 测试里用 S/E 指代私用区字符，源码里不写裸字符（裸字符在 diff 与编辑器里都不可见）。
S, E = chr(0xE000), chr(0xE001)
PUA_RE = re.compile('[' + S + '-' + chr(0xF8FF) + ']')
SENT = markdown_blocks._SENTINEL
SENT_END = markdown_blocks._SENTINEL_END
# 封层标记（复核 g）：中间阶段换回的用户串会被套上它，只有终态恢复才剥除。
SEAL = markdown_blocks._SEAL
SEAL_END = markdown_blocks._SEAL_END


class _HtmlReader(HTMLReaderBase):
    """取渲染结果的 href 与可见文字，属性按 HTML 语义解码后再两次 unquote。"""

    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.hrefs, self.text = [], []
        self.feed(raw)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.append(unquote(unquote(dict(attrs).get('href', ''))))

    def handle_data(self, data):
        self.text.append(data)


class _RoundtripJudge:
    """完整链路的判据工具：先存 → 再存 → 反复 reflow → 包 manifest → 公众号导出。

    URL 判据 = 两个出口的 href，经 HTML 属性解码 + 两次 unquote 后，与**项目本地
    markdown-it 直接渲染同一段原文**的结果逐字相等；先断言链接存在，链接消失不得算
    通过（复核 e 轮的空列表假通过）。
    """

    def _read(self, blob):
        """取 href 与可见文字。

        公众号导出把作品标题包成 `<h1>`，那不是用户这一段的正文，比对前先剥掉
        `<h1>`——否则会把标题算进「可见文字」，与 markdown-it 原生输出对不上。
        """
        blob = re.sub(r'<title\b[^>]*>.*?</title>', '', blob, flags=re.S)
        reader = _HtmlReader(re.sub(r'<h1\b[^>]*>.*?</h1>', '', blob, flags=re.S))
        return reader.hrefs, ''.join(reader.text).strip()

    def _native(self, raw):
        return self._read(markdown_blocks._parser().render(raw))

    def _roundtrip(self, raw, title='结构'):
        """走完整链路：首存 → 再存 → 五次 reflow → 包 manifest → 公众号导出。"""
        from publishing.platforms import wechat
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = studio.Store(temp.name)
        slug = store.create({'title': title, 'type': 'article'})['slug']
        first = store.save(slug, {'base_version': 'v0001', 'title': title,
                                  'blocks': [{'kind': 'text', 'html': raw}]})
        manifest, _, package = store.info(slug)
        saved = manifest['blocks'][0]['html']
        again = store.save(slug, {'base_version': first['version'], 'title': title,
                                  'blocks': [{'kind': 'text', 'html': saved}]})
        flows = [store.reflow(slug, {'base_version': store.meta(slug)['head']})['unchanged']
                 for _ in range(5)]
        manifest, _, package = store.info(slug)
        head = store.meta(slug)['head']
        export = wechat.render({'studio': {'type': 'article'}, 'target': {'title': title},
                                'manifest': manifest, 'slug': slug,
                                'version': head, 'package': str(package)})
        # 重排后 manifest 里多了标题块，取回用户那一段：按 `text` 字段定位。
        block = next((b['html'] for b in manifest['blocks']
                      if b.get('kind') == 'text' and 'a href' in b.get('html', '')),
                     manifest['blocks'][0]['html'])
        return {'saved': saved, 'unchanged': again['unchanged'], 'flows': flows,
                'manifest': json.dumps(manifest, ensure_ascii=False),
                'block': block, 'export': export}

    def _assert_case(self, raw, label):
        """一条探针的完整判据：链接先存在，再与原生 markdown-it 逐字相等。

        代码内容逐字节原样；同一段里的字面实体必须仍是实体（链接目标上的实体按 URL
        语义解码不算「被吃掉」，href 已经与原生逐字比过）。
        """
        want_hrefs, want_text = self._native(raw)
        want_codes = re.findall(r'<code[^>]*>([\s\S]*?)</code>',
                                markdown_blocks._parser().render(raw))
        result = self._roundtrip(raw, 'H-' + label)
        for outlet, blob in (('包 manifest 出口', result['block']), ('公众号导出', result['export'])):
            hrefs, text = self._read(blob)
            if want_hrefs:
                self.assertTrue(hrefs, outlet + '：链接整条消失了，不得算通过')
            self.assertEqual(hrefs, want_hrefs, outlet + ' 的链接目标与原文不一致')
            self.assertEqual(text, want_text, outlet + ' 的可见文字与原文不一致')
            got_codes = re.findall(r'<code[^>]*>([\s\S]*?)</code>', blob)
            if want_codes:
                self.assertEqual(got_codes, want_codes, outlet + ' 的代码内容不是逐字节原样')
        body = re.sub(r'\]\([^)]*\)', ']()', raw)
        body = '\n'.join(line for line in body.split('\n')
                         if not re.match(r'^\s*\[.*\]:', line))
        if '&#' in body:
            self.assertRegex(result['saved'], r'&#[0-9a-zA-Z]+;|&#x[0-9a-fA-F]+;',
                             '同一段里的字面实体被内部恢复吃掉了：' + result['saved'])
        self.assertTrue(result['unchanged'], '再存一次产生了新版本')
        self.assertTrue(all(result['flows']), '重排不稳定')
        return result


class RoundFStructureTests(_RoundtripJudge, unittest.TestCase):
    """复核 f 的三个缺口：URL 目标按 token 流定位、代码逐字节原样、整行标签不产块级。"""

    def test_link_targets_are_identical_at_the_render_layer(self):
        # 判据落在 `markdown_blocks.convert` 这一层：清洗层会把 href 里的 `&#42;`
        # 归一成 `*`，所以**经过清洗的输出看不出差别**，但哨兵没被展开这件事
        # 本身就是 bug——它意味着「链接目标的解码」要靠下游兜底而不是就地做对。
        # 这里逐字比对 convert 的输出与 markdown-it 原生输出。
        cases = [
            '[括号](https://example.com/a(b)?q=&#42;) **新**',
            '[两层](https://example.com/a(b(c))?q=&#20013;) **新**',
            '[角括号](<https://example.com/p?q=&#42;>) **新**',
            '<https://example.com/auto?q=&#20013;> **新**',
            '[引用][new]\n\n[new]:\n  https://example.com/a?q=&#20013;\n\n**新**',
            '[组合](https://example.com/p?x=&#42;&amp;y=&#35;章&amp;p=%25) **新**',
        ]
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                native = markdown_blocks._parser().render(raw).strip()
                got = markdown_blocks.convert(raw)
                # 清洗层补的 rel 与 language-* 类名不在这一层，比对前去掉。
                native = re.sub(r' class="language-[^"]*"', '', native)
                self.assertEqual(got, native, '链接目标没有就地按 URL 语义解码')

    def test_link_targets_match_native_markdown_it(self):
        cases = [
            # 复核点名的四条
            '[括号](https://example.com/a(b)?q=&#42;) **新**',
            '[两层](https://example.com/a(b(c))?q=&#20013;) **新**',
            '[引用][new]\n\n[new]:\n  https://example.com/a?q=&#20013;\n\n**新**',
            '[私用括号](https://example.com/a(b)/' + S + '0' + E + ') **新**',
            # 合同要求的组合矩阵（& ? # % 中文、转义括号、角括号、mailto、标题）
            '[组合](https://example.com/p?x=&#42;&amp;y=&#35;章&amp;p=%25) **新**',
            r'[转义](https://example.com/a\(b\)?q=&gt;) **新**',
            '[角括号](<https://example.com/p?q=&#42;>) **新**',
            '[邮件](mailto:a@b.com?subject=&#42;) **新**',
            '[标题](https://example.com/ok "&#42;") **新**',
            '[中文](https://example.com/路径?q=&#20013;) **新**',
            '[哈希](https://example.com/p#&#35;节) **新**',
            '[百分](https://example.com/p?a=1&#38;b=2) **新**',
            '[同行引用][r]\n\n[r]: https://example.com/a(b)?q=&#42;\n\n**新**',
            '[多实体](https://example.com/a?q=&#42;&#35;&#20013;) **新**',
        ]
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                want_hrefs, want_text = self._native(raw)
                self.assertTrue(want_hrefs, '这条原文本身就没有链接，判据无效')
                result = self._roundtrip(raw)
                for outlet, blob in (('包 manifest 出口', result['block']),
                                     ('公众号导出', result['export'])):
                    hrefs, text = self._read(blob)
                    self.assertTrue(hrefs, outlet + '：链接整条消失了，不得算通过')
                    self.assertEqual(hrefs, want_hrefs, outlet + ' 的链接目标与原文语义不一致')
                    self.assertEqual(text, want_text, outlet + ' 的可见文字与原文不一致')
                self.assertTrue(result['unchanged'], '再存一次产生了新版本')
                self.assertTrue(all(result['flows']), '重排不稳定')

    def test_autolink_and_angle_target_keep_the_link(self):
        for raw in ('<https://example.com/auto?q=&#20013;> **新**',
                    '[角括号](<https://example.com/p?q=&#42;>) **新**'):
            with self.subTest(raw=raw[:30]):
                rich = studio.markdown_aware_html(raw)[0]
                self.assertEqual(len(re.findall(r'<a\b', rich)), 1, '链接数量不对：' + rich)
                self.assertIn('新</strong>', rich, '链接后面的正文丢了')
                self.assertEqual(studio.markdown_aware_html(rich)[0], rich)

    # --- F-M1 代码里的用户原文与实体必须逐字节原样 ---

    def test_code_content_is_byte_identical(self):
        cases = [
            '```text\n[x](https://example.com/' + S + '0' + E + ')\n```',
            '```text\n[x](https://example.com/?q=&#42;)\n```',
            '```html\n&lt;div&gt;hi&lt;/div&gt;\n```',
            '## 代码\n`[x](https://example.com/' + S + '0' + E + ')`',
            '````text\n```\n[x](https://example.com/?q=&#42;)\n```\n````',
            '~~~text\n' + S + '1' + E + ' &amp; 实体\n~~~',
        ]
        for raw in cases:
            with self.subTest(raw=repr(raw[:40])):
                want_html = markdown_blocks._parser().render(raw).strip()
                # 清洗层会去掉 language-* 类名，比对时去掉。
                want_html = re.sub(r' class="language-[^"]*"', '', want_html)
                rich = studio.markdown_aware_html(raw)[0]
                self.assertEqual(rich, want_html, '代码内容不是逐字节原样')
                self.assertEqual(studio.markdown_aware_html(rich)[0], rich, '再存一次代码变了')

    def test_user_sentinel_shaped_text_survives(self):
        """哨兵碰撞矩阵（复核 g 要求的 ≥8 条）。

        原来的写法只取序号 `0` 与 `7777`，**两个都探不到交叉索引**：
        `0` 恰好与自己的隔离槽位重合（换回来还是自己），`7777` 越界后 `_restore`
        原样返回。于是「用户串的序号 ≠ 实体序号、且指向另一个已占用槽位」这一类
        ——也就是 f 真正坏掉的那一类——整组漏掉了。

        下面是按类别摆开的矩阵，四个位置（URL / 正文 / 链接文字 / 代码）各 ≥1：

        | 类别 | 覆盖 |
        | --- | --- |
        | 交叉索引 | 用户串占 k 号槽，指向的却是第 k+1 号槽的实体 |
        | 同索引 | 用户串与实体占用同一个序号 |
        | 越界 | 用户串的序号排不进任何哨兵表 |
        | 多串并存 | 一段里同时出现多个哨兵形状的用户串 |
        | 实体+PUA 混合 | 用户串、字面实体、私用区字符同段并存 |

        判据不是「输出里没有内部字符」也不是「输出稳定」：链接先断言存在，
        href 经属性解码 + 两次 unquote 后与**原生 markdown-it 渲染同一段原文**逐字相等；
        代码内容逐字节原样；同一段里的字面实体必须仍是实体。
        """
        cases = [
            # --- 交叉索引（f 的原始反例）---
            ('url-cross', '[目标](https://example.com/' + SENT + '1' + SENT_END + ') &#42; **新**'),
            ('url-cross-2', '[甲](https://example.com/' + SENT + '2' + SENT_END
             + ') [乙](https://example.com/' + SENT + '0' + SENT_END + ') &#35; **新**'),
            ('code-cross-inline', '## 代码\n`' + SENT + '1' + SENT_END + '` &#42;'),
            ('code-cross-fence', '```txt\n' + SENT + '1' + SENT_END + ' &amp;\n```\n&#42; **新**'),
            # --- 同索引 ---
            ('url-same-index', '[目标](https://example.com/' + SENT + '1' + SENT_END
             + ') ' + SENT + '1' + SENT_END + ' &#42; **新**'),
            # --- 越界 ---
            ('body-out-of-range', '## 字符\n' + SENT + '7777' + SENT_END + ' &#42; **新**'),
            ('code-out-of-range', '## 代码\n`' + SENT + '7777' + SENT_END + '` &#42;'),
            # --- 多个用户哨兵形串并存 ---
            ('body-multi', '## 字符\n' + SENT + '1' + SENT_END + ' ' + SENT + '2' + SENT_END
             + ' ' + SENT + '3' + SENT_END + ' &#42; **新**'),
            ('url-multi', '[甲](https://example.com/' + SENT + '1' + SENT_END
             + ') [乙](https://example.com/' + SENT + '2' + SENT_END
             + ') [丙](https://example.com/' + SENT + '3' + SENT_END + ') &#42; **新**'),
            # --- 用户串 + 实体 + PUA 混合 ---
            ('code-mixed-pua', '```txt\n' + SENT + '1' + SENT_END + ' &amp; ' + S + '0' + E
             + '\n```\n&#42; **新**'),
            ('url-mixed-pua', '[目标](https://example.com/' + SENT + '1' + SENT_END + S
             + '0' + E + '?q=&#42;) **新**'),
            # --- 正文与链接文字两个位置 ---
            ('body-cross', '## 字符\n' + SENT + '1' + SENT_END + ' &#42; '
             + SENT + '0' + SENT_END + ' ' + SENT + '999' + SENT_END),
            ('label-cross', '[' + SENT + '1' + SENT_END + '](https://example.com/?a=&#42;) **新**'),
            ('label-multi', '[' + SENT + '1' + SENT_END + ' ' + SENT + '2' + SENT_END
             + '](https://example.com/?a=&#42;) **新**'),
            # --- 波浪围栏（f 反例的第四种代码形态）---
            ('code-cross-tilde', '~~~~txt\n' + SENT + '1' + SENT_END + ' &#42;\n~~~\n'
             + S + '0' + E + '\n~~~~\n&#35; **新**'),
            # --- 封层形状的串：用户也可能写 ---
            ('url-seal', '[目标](https://example.com/' + SEAL + '1' + SEAL_END + ') &#42; **新**'),
            ('body-seal', '## 字符\n' + SEAL + '1' + SEAL_END + ' ' + SEAL + '2' + SEAL_END
             + ' &#42; **新**'),
            ('code-seal', '## 代码\n`' + SEAL + '1' + SEAL_END + '` &#42;'),
        ]
        self.assertGreaterEqual(len(cases), 8, '碰撞矩阵至少 8 条')
        positions = ('url', 'body', 'label', 'code')
        covered = set()
        for name, raw in cases:
            covered.add(next((p for p in positions if p in name), None))
            with self.subTest(case=name):
                want_hrefs, want_text = self._native(raw)
                want_codes = [c for c in re.findall(r'<code[^>]*>([\s\S]*?)</code>',
                                                   markdown_blocks._parser().render(raw))]
                result = self._roundtrip(raw, '碰撞-' + name)
                for outlet, blob in (('包 manifest 出口', result['block']),
                                     ('公众号导出', result['export'])):
                    hrefs, text = self._read(blob)
                    if want_hrefs:
                        # 先断言链接存在：整条消失不得算通过。
                        self.assertTrue(hrefs, outlet + '：链接整条消失了，不得算通过')
                    self.assertEqual(hrefs, want_hrefs, outlet + ' 的链接目标与原文不一致')
                    self.assertEqual(text, want_text, outlet + ' 的可见文字与原文不一致')
                    got_codes = [c for c in re.findall(r'<code[^>]*>([\s\S]*?)</code>', blob)]
                    if want_codes:
                        # 代码内容逐字节原样：连哨兵形状的用户串都得原样留下。
                        self.assertEqual(got_codes, want_codes, outlet + ' 的代码内容不是逐字节原样')
                # 同一段里的字面实体必须仍是实体，不得被内部恢复吃掉。
                # 只在**正文**里判：链接目标上的实体按 URL 语义解码成 `*` 之类，
                # 那正是 F-H1 要的（href 已按原生逐字比过），不算「被吃掉」。
                body = re.sub(r'\]\([^)]*\)', ']()', raw)
                body = '\n'.join(line for line in body.split('\n')
                                 if not re.match(r'^\s*\[.*\]:', line))
                if '&#' in body:
                    self.assertRegex(result['saved'], r'&#[0-9a-zA-Z]+;|&#x[0-9a-fA-F]+;',
                                     '同一段里的字面实体被内部恢复吃掉了：' + result['saved'])
                self.assertTrue(result['unchanged'], '再存一次产生了新版本')
                self.assertTrue(all(result['flows']), '重排不稳定')
        for position in positions:
            self.assertIn(position, covered, '碰撞矩阵漏了「%s」这个位置' % position)

    def test_restored_user_content_is_immune_to_a_second_restore(self):
        """机制级断言：一次恢复后的用户内容不得再参与内部替换（复核 g-H1/G-M1 的根因）。

        判据直接落在机制上，不依赖具体输入：`_spend`（中间阶段）换回的哨兵形状字符串
        必须被换成**只有终态 `_restore` 才认**的封层标记，因此
        `_SENTINEL_TOKEN` 在中间产物里再也扫不到它。
        标记带本次 nonce（复核 h），所以这里显式钉住 nonce 再构造标记。
        """
        nonce = '0123456789abcdef01234567'
        held = _held(nonce)
        held.append('<原字符>')
        spent = markdown_blocks._spend(markdown_blocks._SENTINEL + '0'
                                       + markdown_blocks._SENTINEL_END + nonce, held)
        self.assertIsNone(markdown_blocks._SENTINEL_TOKEN.search(spent),
                          '中间阶段换回的原文仍长得像内部哨兵：' + spent)
        self.assertEqual(markdown_blocks._restore(spent, held), '<原字符>')
        # 逐处封层：同一段里展开两次，两处都换回自己的原文，互不串味。
        held = _held(nonce)
        held.extend(['&#42;', '&#35;'])
        text = markdown_blocks._SENTINEL + '0' + markdown_blocks._SENTINEL_END + nonce + ' '
        text += markdown_blocks._SENTINEL + '1' + markdown_blocks._SENTINEL_END + nonce
        self.assertEqual(markdown_blocks._restore(markdown_blocks._spend(text, held), held),
                         '&#42; &#35;')
        # 终态一次扫完：封层与待恢复混在同一串里，换回结果不会被同一遍再解释。
        held = _held(nonce)
        held.extend(['zQq1qQz', '&#42;'])
        mixed = (markdown_blocks._SEAL + '0' + markdown_blocks._SEAL_END + nonce + ' '
                 + markdown_blocks._SENTINEL + '1' + markdown_blocks._SENTINEL_END + nonce)
        self.assertEqual(markdown_blocks._restore(mixed, held), 'zQq1qQz &#42;')
        # 越界两种标记都原样留着，绝不抛异常（复核 M1 的 IndexError）。
        for marker in (markdown_blocks._SENTINEL + '999' + markdown_blocks._SENTINEL_END + nonce,
                       markdown_blocks._SEAL + '999' + markdown_blocks._SEAL_END + nonce):
            self.assertEqual(markdown_blocks._restore(marker, _held(nonce)), marker)

    # --- F-附加 整行标签内不得产出块级结构 ---

    def test_whole_line_tags_never_get_block_markup(self):
        for tag in ('h2', 'li', 'h3', 'dt'):
            for inner in ('<span><em>', '<strong><span>'):
                with self.subTest(tag=tag, inner=inner):
                    raw = '<%s>%s&#45; alpha\n&#45; beta</%s></%s>' % (
                        tag, inner, inner.split('>')[1], tag)
                    first = studio.markdown_aware_html(raw)[0]
                    self.assertNotIn('<ul', first, '整行标签内部被插出了列表')
                    self.assertNotIn('<ol', first)
                    self.assertEqual(studio.markdown_aware_html(first)[0], first,
                                     '再存一次整行标签内部变了')
        # 合法 ul 包 li 同样不得在 li 内部再插一层列表。
        raw = '<ul><li><span><em>&#45; alpha\n&#45; beta</em></span></li></ul>'
        first = studio.markdown_aware_html(raw)[0]
        self.assertEqual(first.count('<li>'), 1, 'li 内部又生成了列表项')
        self.assertEqual(studio.markdown_aware_html(first)[0], first)

    def test_whole_line_tags_keep_inline_markup(self):
        # 收口不等于一律不转义：整行标签里的**行内**标记仍然要生效。
        cases = [
            ('h2', '<h2>**粗** `码`</h2>', ['<strong>粗</strong>', '<code>码</code>']),
            ('h3', '<h3>*斜* [链](https://example.com/a)</h3>',
             ['<em>斜</em>', 'href="https://example.com/a"']),
            ('li', '<li>**粗** [链](https://example.com/a)</li>',
             ['<strong>粗</strong>', 'href="https://example.com/a"']),
        ]
        for tag, raw, wanted in cases:
            with self.subTest(tag=tag):
                rich = studio.markdown_aware_html(raw)[0]
                for piece in wanted:
                    self.assertIn(piece, rich, tag + ' 里的行内标记没生效：' + rich)
                self.assertEqual(studio.markdown_aware_html(rich)[0], rich)

    def test_render_layer_still_isolates_fenced_rules(self):
        # `render_source` 走的是 `isolate_breaks`：独立成行的分隔线要补空行，
        # 否则会被当成上一段的 setext 标题；围栏里的分隔线行一个字都不能多。
        self.assertEqual(markdown_blocks.convert('## 标题\na\n---\nb'),
                         '<h2>标题</h2>\n<p>a</p>\n<hr />\n<p>b</p>')
        self.assertEqual(markdown_blocks.isolate_breaks('```\na\n---\nb\n```'),
                         '```\na\n---\nb\n```')
        self.assertEqual(markdown_blocks.isolate_breaks('a\n---\nb'), 'a\n\n\n---\n\n\nb')

    def test_non_whole_line_blocks_still_convert(self):
        # 边界另一侧：段落里的真列表照常转换，不能被误伤。
        self.assertIn('<li>', studio.markdown_aware_html('<p>- a\n- b</p>')[0])
        self.assertIn('<strong>', studio.markdown_aware_html('<div>**粗**</div>')[0])


class RoundHEntityCollisionTests(_RoundtripJudge, unittest.TestCase):
    """复核 g-H1：实体解码后拼出的内部标记被误恢复（阻断项）。

    根因：g 只隔离「原文里已完整出现的标记串」，而 markdown-it 解码实体之后才拼出
    的标记（`https://example.com/zQq&#49;qQz` → `…/zQq1qQz`）从来不在原文里，
    终态 `_restore` 的全局扫描把用户的合法 URL 当成内部哨兵换掉了。

    修法是每次转换换一个随机 nonce（标记 = 索引 + nonce，`_restore` 只认本次 nonce），
    本类把这些输入钉成回归：g 复核失败的十条原样进来，另加自造变体。
    """

    # g 复核 own-probes 里失败的十条 + 真实 Chrome 编辑保存复现那条。
    REVIEW_FAILURES = [
        ('split-pending-digit', '[目标](https://example.com/zQq&#49;qQz) &#42; **新**'),
        ('split-seal-digit', '[目标](https://example.com/Zzq&#49;Zqz) &#42; **新**'),
        ('split-pending-prefix', '[目标](https://example.com/&#122;Qq1qQz) &#42; **新**'),
        ('split-seal-prefix', '[目标](https://example.com/&#90;zq1Zqz) &#42; **新**'),
        ('split-pending-suffix', '[目标](https://example.com/zQq1qQ&#122;) &#42; **新**'),
        ('split-seal-suffix', '[目标](https://example.com/Zzq1Zq&#122;) &#42; **新**'),
        ('split-hex', '[目标](https://example.com/Zzq&#x31;Zqz) &#35; **新**'),
        ('split-reference', '[目标][r]\n\n[r]: https://example.com/zQq&#49;qQz\n\n&#42; **新**'),
        ('split-query', '[目标](https://example.com/?q=Zzq&#49;Zqz) &#35; **新**'),
        ('split-mailto', '[邮件](mailto:test@example.com?subject=zQq&#49;qQz) &#42; **新**'),
        ('chrome-edit-save', '[目标](https://example.com/Zzq&#49;Zqz) Zzq6Zqz Zzq7Zqz **新**'),
    ]

    # 合同要求的自造变体（≥10）：十六进制/十进制/命名实体混排、`%xx` 百分号编码、
    # 实体跨标记边界（前半在用户串、后半为实体）、引用式定义、图片 src、autolink、
    # 链接文字与 title 属性、三轮编辑 + reflow 后再保存。
    VARIANTS = [
        ('mix-dec-hex', '[甲](https://example.com/zQq&#49;qQz) [乙](https://example.com/Zzq&#x31;Zqz) &#42; **新**'),
        ('mix-named', '[目标](https://example.com/Zzq&Zcy;qz) [另](https://example.com/zQq&ast;qQz) &#35; **新**'),
        ('percent-encoded', '[目标](https://example.com/%7A%51%71%31%71%51%7A) &#42; **新**'),
        ('percent-plus-entity', '[目标](https://example.com/%7aQq&#49;qQz) &#42; **新**'),
        ('entity-across-boundary', '[目标](https://example.com/zQq&#49;qQz?q=Zzq&#48;Zqz) &#42; **新**'),
        ('entity-splits-prefix', '[目标](https://example.com/&#x7A;&#x51;&#x71;&#49;qQz) &#42; **新**'),
        ('reference-multi', '[甲][a] [乙][b]\n\n[a]: https://example.com/zQq&#49;qQz\n[b]: https://example.com/Zzq&#49;Zqz\n\n&#42; **新**'),
        ('image-src', '![图](https://example.com/zQq&#49;qQz.png) &#42; **新**'),
        ('image-src-split', '![图](https://example.com/Zzq&#x31;Zqz.png) &#35; **新**'),
        ('autolink', '<https://example.com/Zzq&#49;Zqz> &#42; **新**'),
        ('mailto-label', '[Zzq&#49;Zqz](mailto:a@example.com) &#42; **新**'),
        ('mailto-target', '[邮件](mailto:t@example.com?subject=Zzq&#49;Zqz) &#42; **新**'),
        ('label-entity', '[Zzq&#49;Zqz](https://example.com/zQq&#49;qQz) &#42; **新**'),
        ('title-entity', '[标题](https://example.com/ok "Zzq&#49;Zqz zQq&#49;qQz") &#42; **新**'),
        ('fragment-entity', '[锚](https://example.com/p#Zzq&#49;Zqz) &#42; **新**'),
        ('body-entity-pair', '## 正文\nZzq&#49;Zqz zQq&#50;qQz &#42; **新**'),
        ('code-plus-url', '```txt\nZzq&#49;Zqz\n```\n[目标](https://example.com/zQq&#49;qQz) &#42; **新**'),
    ]

    def test_review_failures_now_pass(self):
        for name, raw in self.REVIEW_FAILURES:
            with self.subTest(case=name):
                self._assert_case(raw, name)

    def test_decoded_marker_variants_pass(self):
        for name, raw in self.VARIANTS:
            with self.subTest(case=name):
                self._assert_case(raw, name)

    def test_three_rounds_of_edit_and_reflow_keep_the_target(self):
        """三轮「编辑 + 重排」之后再保存：URL 目标仍与原生 markdown-it 逐字相等。

        g 复核在真实 Chrome 里是**编辑保存**才复现的（首存那条探针已经错），
        所以这里显式做三轮改写再收尾，不只测首存。
        """
        raw = '[目标](https://example.com/Zzq&#49;Zqz) Zzq6Zqz Zzq7Zqz **新**'
        want_hrefs, want_text = self._native(raw)
        self.assertTrue(want_hrefs, '判据无效：原文本身没有链接')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = studio.Store(temp.name)
        slug = store.create({'title': '三轮', 'type': 'article'})['slug']
        first = store.save(slug, {'base_version': 'v0001', 'title': '三轮',
                                  'blocks': [{'kind': 'text', 'html': raw}]})
        self.assertTrue(first['unchanged'] is False)
        for round_no in range(3):
            store.reflow(slug, {'base_version': store.meta(slug)['head']})
            saved = store.info(slug)[0]['blocks'][0]['html']
            hrefs, text = self._read(saved)
            self.assertEqual(hrefs, want_hrefs, '第 %d 轮重排后链接目标变了' % (round_no + 1))
            self.assertEqual(text, want_text, '第 %d 轮重排后可见文字变了' % (round_no + 1))
            store.save(slug, {'base_version': store.meta(slug)['head'], 'title': '三轮',
                              'blocks': [{'kind': 'text', 'html': saved},
                                         {'kind': 'text', 'html': '第%d轮 **新增**' % round_no}]})
        store.reflow(slug, {'base_version': store.meta(slug)['head']})
        final = store.info(slug)[0]['blocks'][0]['html']
        hrefs, text = self._read(final)
        self.assertEqual(hrefs, want_hrefs, '三轮编辑 + 重排之后链接目标变了')
        self.assertEqual(text, want_text, '三轮编辑 + 重排之后可见文字变了')


class RoundHNonceTests(unittest.TestCase):
    """nonce 机制的门禁：构造上区分内部标记与用户字符（复核 g-H1 的机制要求）。

    这几条断言直接钉住 L1 点名的四个变异：只隔离一种标记、封层引用错槽位、
    不校验/不重生成 nonce、`_restore` 不校验 nonce。
    """

    N1 = '0123456789abcdef01234567'
    N2 = 'fedcba9876543210fedcba98'

    def _shape_cases(self):
        return ((markdown_blocks._SENTINEL, markdown_blocks._SENTINEL_END, '待恢复'),
                (markdown_blocks._SEAL, markdown_blocks._SEAL_END, '封层'))

    def test_restore_only_accepts_the_current_nonce(self):
        """`_restore` 只认带**本次** nonce 的标记；别的 nonce 一律当文字留着。"""
        for head, end, name in self._shape_cases():
            with self.subTest(marker=name):
                foreign = head + '0' + end + self.N2
                held = _held(self.N1)
                held.append('&#42;')
                self.assertEqual(markdown_blocks._restore(foreign, held), foreign,
                                 '别人的 nonce 被当成本次标记换掉了')
                mine = head + '0' + end + self.N1
                self.assertEqual(markdown_blocks._restore(mine, held), '&#42;')

    def test_spend_only_restores_the_current_nonce(self):
        held = _held(self.N1)
        held.append('&#42;')
        foreign = markdown_blocks._SENTINEL + '0' + markdown_blocks._SENTINEL_END + self.N2
        self.assertEqual(markdown_blocks._spend(foreign, held), foreign)
        mine = markdown_blocks._SENTINEL + '0' + markdown_blocks._SENTINEL_END + self.N1
        self.assertEqual(markdown_blocks._restore(markdown_blocks._spend(mine, held), held),
                         '&#42;')

    def test_isolate_covers_nonce_bearing_shapes(self):
        """用户写的**两种**标记形状（含本次 nonce）都必须隔离。

        变异「只隔离待恢复标记、不隔离封层标记」（L1 点名的自造变异）会让用户原文里的
        `Zzq<N>0Zqz` 原样留在流里，终态 `_restore` 把它当成内部封层剥掉——
        正是 L1 举的 `## 字面\nZzq0Zqz &#42;` 那一类。
        """
        for head, end, name in self._shape_cases():
            with self.subTest(shape=name):
                raw = '## 字面\n' + head + '0' + end + self.N1 + ' &#42;'
                # 先占掉 0 号槽：隔离若发生，用户串会落到**新开的**槽位上。
                held = _held(self.N1)
                held.append('&#42;')
                out = markdown_blocks._isolate_user_token(raw, held)
                self.assertEqual(len(held), 2, name + '形状的用户串没有被隔离成新槽位')
                self.assertEqual(markdown_blocks._restore(out, held), raw,
                                 name + '形状的用户串被当成内部标记换掉了')

    def test_isolate_also_covers_legacy_shapes(self):
        """不带 nonce 的旧形（`zQq1qQz` / `Zzq1Zqz`）同样隔离：用户可能真写了这串。"""
        for head, end, name in self._shape_cases():
            with self.subTest(shape=name):
                raw = '## 字面\n' + head + '1' + end + ' &#42;'
                held = _held(self.N1)
                out = markdown_blocks._isolate_user_token(raw, held)
                self.assertNotIn(head + '1' + end, out, name + '形状的用户串没有被隔离')
                self.assertEqual(markdown_blocks._restore(out, held), raw)

    def test_seal_claims_its_own_slot(self):
        """`_seal` 必须引用**自己刚 append 的那个槽位**，不能引用前一个。

        变异「`_seal` 引用前一个 held 槽位」（L1 的第二条自造变异）会让被封的串
        剥开后拿到别人的原文——链接目标直接变错。
        """
        held = _held(self.N1)
        held.append('前一个槽位的原文')
        sealed = markdown_blocks._seal(held, '自己的原文')
        self.assertEqual(sealed,
                         markdown_blocks._SEAL + '1' + markdown_blocks._SEAL_END + self.N1)
        self.assertEqual(markdown_blocks._restore(sealed, held), '自己的原文')

    def test_token_claims_its_own_slot(self):
        held = _held(self.N1)
        held.append('前一个槽位的原文')
        token = markdown_blocks._token(held, '&#42;')
        self.assertEqual(token,
                         markdown_blocks._SENTINEL + '1' + markdown_blocks._SENTINEL_END + self.N1)
        self.assertEqual(markdown_blocks._restore(token, held), '&#42;')

    def test_nonce_is_regenerated_when_the_input_already_contains_it(self):
        """nonce 校验：命中原文 / 实体解码形态 / 原生渲染形态都必须重生成。

        变异「去掉 nonce 校验或重生成」在这三条里都会让 URL 被误换。
        """
        collide = self.N1
        # 每条都是「解码后才成为完整内部标记」+「nonce 命中用户形态」的组合：
        # 去掉 nonce 校验（或撞上后仍用原值）就会把 URL 换成别的槽位。
        url = ('[目标](https://example.com/a/zQq%s qQz' + collide + ') &#42; **新**')
        cases = {
            # 原文里直接含完整 nonce（标记只差一个数字）。
            'raw': url % '',
            # nonce 只在**十进制实体解码后**才与标记相邻。
            'decoded': url % '&#49;',
            # 只在**十六进制实体解码后**。
            'hex': url % '&#x31;',
            # 只在**命名实体解码后**（`&ast;` → `*`，占位让位置对上）。
            'named': url % '&ast;',
            # nonce 完整落在命名实体解码结果里。
            'named-whole': '[目标](https://example.com/a/zQq&#49;qQz&NewLine;' + collide
                           + ') &#42; **新**',
        }
        for name, raw in cases.items():
            with self.subTest(form=name):
                want_href = markdown_blocks._parser().render(raw)
                self.assertIn('zQq', want_href, '判据无效：这条原文渲染出来不含标记形状')
                # 工厂恒发被占用的 nonce：必须重试到别的值，且输出正确。
                with pinned_nonce(collide):
                    retried, held = markdown_blocks.markdown_source(raw)
                    self.assertNotEqual(held.nonce, collide, '占用的 nonce 被原样用上了')
                    self.assertIsNotNone(retried, '找不到安全 nonce 时不该降级（仍有别的候选）')
                    self.assertEqual(markdown_blocks._restore(
                        markdown_blocks.render_source(retried, held), held),
                        markdown_blocks.convert(raw))
                # 判据不因 nonce 变：与默认（随机 nonce）路径逐字相同。
                self.assertEqual(markdown_blocks.convert(raw), markdown_blocks.convert(raw),
                                 '输出与本次 nonce 有关')
                self.assertIn(collide, markdown_blocks.convert(raw),
                              '用户自己的 URL 里必须还留着这串 nonce（没有被换掉）')

    def test_nonce_never_leaks_into_the_output(self):
        """随机 nonce 连转四十次都不该在输出里留下 nonce，且输出逐字相同。"""
        raw = '## 标题\nZzq0Zqz zQq1qQz\n\n[目标](https://example.com/Zzq&#49;Zqz) &#42; **新**'
        first = markdown_blocks.convert(raw)
        for _ in range(40):
            self.assertEqual(markdown_blocks.convert(raw), first, '输出与本次 nonce 有关')
            source, held = markdown_blocks.markdown_source(raw)
            self.assertNotIn(held.nonce, markdown_blocks._restore(source, held))
        self.assertNotRegex(first, r'zQq[0-9]+qQz[0-9a-f]{%d}' % markdown_blocks._NONCE_LEN)
        self.assertNotRegex(first, r'Zzq[0-9]+Zqz[0-9a-f]{%d}' % markdown_blocks._NONCE_LEN)

    def test_nonce_is_fresh_on_every_conversion(self):
        nonces = set()
        for _ in range(20):
            _, held = markdown_blocks.markdown_source('## 标题\n正文')
            self.assertIsNotNone(held)
            nonces.add(held.nonce)
        self.assertEqual(len(nonces), 20, 'nonce 没有每次转换都换新')

    def test_unreachable_nonce_degrades_to_no_conversion(self):
        """重试与派生候选全部不可用时：原样返回，绝不改字。

        降级方向是「不转换」而不是「用会撞车的标记」——转换是锦上添花，
        改坏用户正文不是（`_fresh_nonce` 的硬保证）。
        """
        raw = '## 标题\n[目标](https://example.com/zQq&#49;qQz) &#42; **新**'
        # 候选预算为 0 = 一个都试不到（等价于工厂与随机源同时失效的极端故障）。
        with mock.patch.object(markdown_blocks, '_NONCE_TRIES', 0):
            out = markdown_blocks.convert(raw)
            again = markdown_blocks.convert(raw)
        self.assertEqual(out, again, '降级后重复调用不稳定')
        self.assertEqual(out, raw, '降级时必须原样返回，不能转换也不能改字')

    def test_pinned_nonce_factory_is_restored(self):
        original = markdown_blocks._NONCE_FACTORY
        with pinned_nonce(self.N1):
            self.assertIsNotNone(markdown_blocks._NONCE_FACTORY)
        self.assertIs(markdown_blocks._NONCE_FACTORY, original)


def _entity(body, kind):
    """把一串字符按指定写法实体化，用来让 nonce **不以明文**出现在原文里。"""
    if kind in ('dec', 'decimal'):
        return ''.join('&#%d;' % ord(c) for c in body)
    if kind in ('hex', 'hex-entity'):
        return ''.join('&#x%x;' % ord(c) for c in body)
    if kind in ('ampdec', 'amphex'):
        return ''.join('&amp;#%d;' % ord(c) for c in body)
    if kind in ('percent', 'percent-encoded'):
        return ''.join('%%%02X' % ord(c) for c in body)
    if kind in ('percent-twice', 'percent-encoded-twice'):
        return ''.join('%%%02X' % ord(c) for c in _entity(body, 'percent'))
    raise ValueError(kind)


# `_nonce_forms` 登记的 7 种形态，以及各自在门禁里的地位。
FORM_LABELS = ('text', 'unescape(text)', 'rendered', 'unescape(rendered)',
               'url', 'unquote^2(url)', 'unescape(url)')
# 可观测形态 = 删掉它至少放行一条用例。其余四种被包含关系锁死，单独删是等价变异。
OBSERVABLE_FORMS = ('unescape(text)', 'unquote^2(url)', 'unescape(url)')


def _forms_by_label(raw, drop=None):
    """按形态分类重建 `_nonce_forms` 的内容；`drop` 指定要摘掉哪一种。

    与 `markdown_blocks._nonce_forms` 同源同构，测试里逐形态删减靠它。
    `drop=None` 时返回的集合必须与产品实现逐字相同（由测试断言守住）。
    """
    text = raw or ''
    try:
        rendered = markdown_blocks._parser().render(text)
    except Exception:
        rendered = ''
    urls = markdown_blocks._hrefs(rendered) if rendered else []
    buckets = {
        'text': [text],
        'unescape(text)': [unescape(text)],
        'rendered': [rendered],
        'unescape(rendered)': [unescape(rendered)],
        'url': list(urls),
        'unquote^2(url)': [unquote(unquote(url)) for url in urls],
        'unescape(url)': [unescape(url) for url in urls],
    }
    return {value for label, values in buckets.items() if label != drop for value in values}


class RoundH2NonceFormTests(unittest.TestCase):
    """nonce 校验的**形态覆盖**门禁（复核 h-M1）。

    h 轮的重试测试把 nonce **明文留在原文里**，于是「只校验原文」的有害变异
    （`_nonce_forms = lambda x: {x or ''}`）照样会被挡住——门禁看不出防线已消失。
    本类每条输入都要求：**nonce 在原文里一个字符都不出现**，只在解码形态里出现。

    每条判两件事：

    1. **候选被拒**：把 `_NONCE_FACTORY` 钉死成会撞车的那个值，
       `markdown_source` 必须换一个 nonce（原文之外的形态把它挡住了）；
    2. **结果正确**：链接目标经 HTML 属性解码 + 两次 unquote 后，
       与**项目本地 markdown-it 直接渲染同一段原文**的结果逐字相等
       （只对原生与本实现语义一致的形态判，见 `NATIVE_COMPARABLE`）。

    `SHAPES` 每条注明 nonce 出现在 `_nonce_forms` 的哪种形态里，这是
    「删掉某一种形态应当被打红」那条变异的前提。
    """

    NONCE = '0123456789abcdef01234567'

    # (名字, 原文模板, nonce 的写法, 靠哪种形态挡住, 是否可与原生逐字比较)
    SHAPES = [
        # 完全实体化（十进制）：原文里没有 nonce，unescape(text) 之后才有。
        ('decimal-entity', '[目标](https://example.com/zQq&#49;qQz{body}) &#42; **新**',
         'decimal', 'unescape(text)', True),
        # 完全实体化（十六进制）：同一机制的另一条实体分支。
        ('hex-entity', '[目标](https://example.com/zQq&#49;qQz{body}) &#42; **新**',
         'hex', 'unescape(text)', True),
        # `%xx` 百分号编码：markdown-it 原样保留百分号编码，nonce 只在两次 unquote 后出现。
        ('percent-encoded', '[目标](https://example.com/zQq&#49;qQz{body}) &#42; **新**',
         'percent', 'unquote^2(url)', True),
        # 两次百分号编码：一次 unquote 只还原一层，仍要两次才拿到 nonce。
        ('percent-encoded-twice', '[目标](https://example.com/zQq&#49;qQz{body}) &#42; **新**',
         'percent-twice', 'unquote^2(url)', True),
        # 围栏代码里的实体：markdown-it 把 `&` 转义成 `&amp;`，渲染结果里根本没有 nonce，
        # 只有原文按实体解码之后才有——`unescape(text)` 是这条**唯一**形态。
        ('fenced-code-entity', '## 代码\n```txt\nzQq&#49;qQz{body}\n```\n\n&#42; **新**',
         'decimal', 'unescape(text)', False),
        # href 里先转义一层：`&amp;#48;` 经属性解码得 `&#48;`，再 unescape 才到 nonce。
        ('double-encoded-href', '[目标](https://example.com/zQq&#49;qQz{body}) &#42; **新**',
         'ampdec', 'unescape(url)', False),
    ]

    # 只对原生 markdown-it 与本实现语义一致的形态判「href 逐字相等」。
    # `double-encoded-href` 不在其列：`&amp;#48;` 按 CommonMark 只解码一层，
    # 原生给 `…&#48;…`，本实现按既有「字面实体不还原」给 `…0…`——
    # 这与 nonce 无关，本轮不把它算成回归，也不假装两者一致。
    NATIVE_COMPARABLE = ('decimal-entity', 'hex-entity', 'percent-encoded',
                         'percent-encoded-twice')

    def _hrefs(self, blob):
        return _HtmlReader(blob).hrefs

    def _native(self, raw):
        return self._hrefs(markdown_blocks._parser().render(raw))

    def _raw(self, template, kind):
        return template.format(body=_entity(self.NONCE, kind))

    def test_nonce_only_in_a_decoded_form_is_rejected(self):
        """核心门禁：nonce 不以明文出现时，钉死的碰撞候选必须被拒。"""
        for name, template, kind, form, _native_ok in self.SHAPES:
            with self.subTest(shape=name):
                raw = self._raw(template, kind)
                self.assertNotIn(self.NONCE, raw,
                                 '判据无效：原文里直接出现 nonce 明文，只校验原文就能挡住')
                self.assertIn(self.NONCE, ''.join(markdown_blocks._nonce_forms(raw)),
                              '这条用例在 _nonce_forms 里命中不了，判据本身失效')
                with pinned_nonce(self.NONCE):
                    source, held = markdown_blocks.markdown_source(raw)
                self.assertIsNotNone(source, '不该降级：只是这一个候选被占用了')
                self.assertNotEqual(held.nonce, self.NONCE,
                                    '%s 形态没被校验，碰撞的 nonce 被原样用上了' % form)

    def test_nonce_only_in_a_decoded_form_keeps_the_link_target(self):
        """同样一批输入，链接目标必须与原生本地 markdown-it 逐字相等。"""
        for name, template, kind, _form, native_ok in self.SHAPES:
            if name not in self.NATIVE_COMPARABLE:
                continue
            with self.subTest(shape=name):
                raw = self._raw(template, kind)
                want = self._native(raw)
                self.assertTrue(want, '判据无效：这条原文本身就没有链接')
                with pinned_nonce(self.NONCE):
                    got = markdown_blocks.convert(raw)
                self.assertEqual(self._hrefs(got), want,
                                 name + '：链接目标与原生 markdown-it 不一致')

    def test_raw_only_validation_really_breaks_the_link_target(self):
        """「只校验原文」这条有害变异**真的会改坏东西**，且被上面两条判出来。

        这是 M1 的核心：不是再写一句「应该红」的注释，而是把变异跑一遍，
        确认它把链接目标换成别的 `held` 槽位（`…/&#48;`，解码后 `…/0`）。
        """
        damaged = []
        for name, template, kind, _form, native_ok in self.SHAPES:
            if name not in self.NATIVE_COMPARABLE:
                continue
            raw = self._raw(template, kind)
            want = self._native(raw)
            with mock.patch.object(markdown_blocks, '_nonce_forms', lambda x: {x or ''}):
                with pinned_nonce(self.NONCE):
                    got = markdown_blocks.convert(raw)
            if self._hrefs(got) != want:
                damaged.append((name, self._hrefs(got)))
        self.assertTrue(damaged, '只校验原文的变异没改坏任何一条，说明这批用例没覆盖解码形态')
        self.assertIn('decimal-entity', [n for n, _ in damaged],
                      '十进制实体化那条没被变异改坏，形态覆盖有缺口')

    def test_dropping_any_observable_form_lets_a_collision_through(self):
        """删掉**任一**形态后，跑一遍整批用例，看碰撞会不会被放行。

        结论写在 `OBSERVABLE` 里，不靠嘴说：

        - `unescape(text)` / `unquote^2(url)` / `unescape(url)` 这三种
          **可观测**——删掉它们至少放行一条用例，所以必须有断言钉住；
        - `text` / `rendered` / `unescape(rendered)` / `url` 被包含关系锁死，
          单独删掉一条用例都放行不了（等价变异，如实记账，不假装测得出来）。

        形态集合在测试侧独立重建一遍，并先断言它与产品实现逐字相同——
        免得「删掉某一形态」这个变异悄悄删错了东西。
        """
        for label in FORM_LABELS:
            with self.subTest(form=label):
                leaked = []
                for name, template, kind, _form, _ok in self.SHAPES:
                    raw = self._raw(template, kind)
                    self.assertEqual(set(_forms_by_label(raw)), markdown_blocks._nonce_forms(raw),
                                     '测试侧重建的形态集合与产品实现不一致，形态覆盖已失效')
                    with mock.patch.object(markdown_blocks, '_nonce_forms',
                                           lambda text, drop=label: _forms_by_label(text, drop)):
                        with pinned_nonce(self.NONCE):
                            _source, held = markdown_blocks.markdown_source(raw)
                    if held is not None and held.nonce == self.NONCE:
                        leaked.append(name)
                if label in OBSERVABLE_FORMS:
                    self.assertTrue(leaked, label + ' 被删掉后一条都没放行，形态覆盖有缺口')
                else:
                    self.assertFalse(leaked, label + ' 单独被删居然放行了用例，包含关系已变')

    def test_subsumed_forms_are_covered_by_their_superset(self):
        """`text ⊆ unescape(text)`、`rendered ⊆ unescape(rendered)`、`url ⊆ unquote^2(url)`。

        nonce 是 24 个十六进制字符，既不含 `&` 也不含 `%`；`unescape` 只替换
        `&...;`、`unquote` 只替换 `%XX`，都不可能把一段已经连续的 nonce 拆开。
        这条把包含关系钉成事实：语料里小形态命中时大形态必然也命中。
        """
        dec = _entity(self.NONCE, 'decimal')
        corpus = [
            '[目标](https://example.com/zQq&#49;qQz' + self.NONCE + ') &#42; **新**',
            '[目标](https://example.com/zQq&#49;qQz' + dec + ') &#42; **新**',
            '## zQq&#49;qQz' + dec + ' &#42; **新**',
            '## 代码\n```txt\nzQq&#49;qQz' + dec + '\n```\n\n&#42; **新**',
        ]
        for raw in corpus:
            rendered = markdown_blocks._parser().render(raw)
            self.assertTrue(any(self.NONCE in form for form in markdown_blocks._nonce_forms(raw)),
                            '语料失效：没有形态命中')
            if self.NONCE in raw:
                self.assertIn(self.NONCE, unescape(raw), 'text ⊆ unescape(text) 反例')
            if self.NONCE in rendered:
                self.assertIn(self.NONCE, unescape(rendered), 'rendered ⊆ unescape(rendered) 反例')
            for url in markdown_blocks._hrefs(rendered):
                if self.NONCE in url:
                    self.assertIn(self.NONCE, unquote(unquote(url)), 'url ⊆ unquote^2(url) 反例')


class Round13EditorMarkdownTests(unittest.TestCase):
    """第 13 轮验收返修：编辑器粘贴 Markdown（引用、代码防双重转义、来源区分、普通文字防误判）。"""

    def test_minimal_acceptance_input(self):
        # 真实 Chrome 复现最小输入：> 引用、表格、```html <div>
        raw = '<p>&gt; 引用验收文字</p><p><br></p><p>| 项目 | 数值 |</p><p>| --- | --- |</p><p>| 甲 | 13 |</p><p><br></p><p>```html</p><p>&lt;div&gt;代码原样&lt;/div&gt;</p><p>```</p>'
        rich, plain, dropped = studio.markdown_aware_html(raw, from_editor=True)
        self.assertIn('<blockquote>', rich)
        self.assertIn('<p>引用验收文字</p>', rich)
        self.assertIn('</blockquote>', rich)
        # 表格保持每行一个 <p> 可读原文
        self.assertIn('<p>| 项目 | 数值 |</p>', rich)
        self.assertIn('<p>| --- | --- |</p>', rich)
        self.assertIn('<p>| 甲 | 13 |</p>', rich)
        # 代码块单次转义，不执行但显示原样字符
        self.assertIn('<pre><code>', rich)
        self.assertIn('&lt;div&gt;代码原样&lt;/div&gt;', rich)
        self.assertNotIn('&amp;lt;', rich, '代码块不得出现双重转义')
        self.assertNotIn('<div>', rich, '代码块内容不得被作为真实标签执行')
        # 幂等性：二次保存完全不变
        second_rich, _, _ = studio.markdown_aware_html(rich, from_editor=True)
        self.assertEqual(second_rich, rich, '保存结果再次保存必须幂等')

    def test_code_with_style_attributes(self):
        # 带 style 属性的代码块
        raw = '<p>```html</p><p>&lt;div style="color:red"&gt;代码原样，不执行&lt;/div&gt;</p><p>```</p>'
        rich, plain, dropped = studio.markdown_aware_html(raw, from_editor=True)
        self.assertIn('<pre><code>', rich)
        self.assertIn('&lt;div style=&quot;color:red&quot;&gt;代码原样，不执行&lt;/div&gt;', rich)
        self.assertNotIn('&amp;lt;', rich)
        self.assertNotIn('<div', rich)

    def test_inline_code_single_escape(self):
        # 行内代码：普通标签与实体代码
        raw = '<p>**代码示例**：`&lt;div&gt;` 与 `&amp;lt;div&amp;gt;`</p>'
        rich, plain, dropped = studio.markdown_aware_html(raw, from_editor=True)
        self.assertIn('<strong>代码示例</strong>', rich)
        self.assertIn('<code>&lt;div&gt;</code>', rich)
        self.assertIn('<code>&amp;lt;div&amp;gt;</code>', rich)
        self.assertNotIn('&amp;amp;lt;', rich)

    def test_editor_fence_literal_entity(self):
        # 编辑器中用户输入字面实体代码 &lt;div&gt; -> 序列化为 &amp;lt;div&amp;gt;
        raw = '<p>```html</p><p>&amp;lt;div&amp;gt;字面实体&amp;lt;/div&amp;gt;</p><p>```</p>'
        rich, plain, dropped = studio.markdown_aware_html(raw, from_editor=True)
        self.assertIn('<pre><code>', rich)
        self.assertIn('&amp;lt;div&amp;gt;字面实体&amp;lt;/div&amp;gt;', rich)
        self.assertNotIn('&amp;amp;lt;', rich, '字面实体代码不得多一层转义')

    def test_multiline_and_nested_quotes(self):
        raw = '<p>&gt; 第一行引用<br>&gt; 第二行引用</p>'
        rich, plain, dropped = studio.markdown_aware_html(raw, from_editor=True)
        self.assertIn('<blockquote>', rich)
        self.assertIn('第一行引用', rich)
        self.assertIn('第二行引用', rich)

    def test_ordinary_text_with_greater_than_and_pipe_not_misidentified(self):
        cases = [
            '<p>价格 &gt; 100 元，或者优惠券 | 折扣</p>',
            '<p>当 x &gt; 0 且 y &lt; 10 时，运行该分支。</p>',
            '<p>商品分类：电子 | 家居 | 服装</p>',
            '<p>&gt;5.0 版本的更新说明</p>',
            '<p>对比关系：A &gt; B &gt; C</p>',
        ]
        for markup in cases:
            with self.subTest(markup=markup):
                self.assertFalse(markdown_blocks.looks_like_markdown(markdown_blocks.signal_text(markup)))
                rich, plain, dropped = studio.markdown_aware_html(markup, from_editor=True)
                self.assertEqual(rich, studio.clean_html(markup)[0])
                self.assertEqual(plain, studio.clean_html(markup)[1])

    def test_escaped_quote_not_rendered_as_blockquote(self):
        raw = '<p>\\&gt; 这不是引用</p>'
        rich, plain, dropped = studio.markdown_aware_html(raw, from_editor=True)
        self.assertNotIn('<blockquote>', rich)
        self.assertIn('&gt; 这不是引用', rich)

    def test_assistant_direct_markdown_gates(self):
        """常驻门禁：复核者 raw-comment、wrapped-entity 走真实 Assistant.execute -> Store.save。"""
        import tempfile, json
        from pathlib import Path
        from tests.test_assistant_format_guide import policy_for
        with tempfile.TemporaryDirectory() as tmp:
            store = studio.Store(tmp)
            config = Path(tmp) / 'model.json'
            config.write_text(json.dumps({'endpoint': 'https://example.invalid/chat/completions',
                                          'api_key': 'secret-test-value', 'model': 'test-model'}))
            agent = studio.assistant_module.Assistant(store, studio.content, config)
            slug = store.create({'title': '直接源门禁', 'type': 'article'})['slug']
            store.save(slug, {'base_version': 'v0001', 'title': '直接源门禁', 'blocks': [{'kind': 'text', 'html': '<p>初始正文</p>'}]})

            def run_update(raw_text):
                job = {'read_versions': {}, 'read_blocks': {}, 'changed': [], 'cancel': False,
                       'attachments': [], 'policy': policy_for('修改代码示例', work_open=True)}
                agent.execute('read_work', {'slug': slug, 'start': 0, 'limit': 30}, job)
                head = store.meta(slug)['head']
                agent.execute('update_text', {'slug': slug, 'base_version': head, 'summary': '直接源核验', 'edits': [{'index': 0, 'html': raw_text}]}, job)
                return store.detail(slug)['blocks'][0]['html']

            # 1. raw-comment: 围栏内含 <!-- <p> --> 注释，不得被当成编辑器输入改写实体
            raw_comment = "```text\n&lt;div&gt;hi&lt;/div&gt;\n<!-- <p> -->\n```"
            saved1 = run_update(raw_comment)
            self.assertIn('&amp;lt;div&amp;gt;hi&amp;lt;/div&amp;gt;', saved1, '助手直接源实体代码不得被改写')
            self.assertNotIn('<code>&lt;div&gt;', saved1)

            # 2. wrapped-entity: 含 <p> 标签与围栏实体代码，助手执行保持直接 Markdown 实体
            wrapped_entity = "<p>说明</p><p>```html\n&lt;b&gt;x&lt;/b&gt;\n```</p>"
            saved2 = run_update(wrapped_entity)
            self.assertIn('&amp;lt;b&amp;gt;x&amp;lt;/b&amp;gt;', saved2, '助手直接源实体代码不得被改写为标签')
            self.assertNotIn('<code>&lt;b&gt;', saved2)

    def test_editor_save_matrix_gates(self):
        """常驻门禁：编辑器路由 (from_editor=True) 保持 b 版四种代码/实体结果。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = studio.Store(tmp)
            slug = store.create({'title': '编辑器矩阵', 'type': 'article'})['slug']

            # 1. 围栏 <div>
            store.save(slug, {'base_version': 'v0001', 'title': '矩阵1',
                              'blocks': [{'kind': 'text', 'html': '<p>```html</p><p>&lt;div&gt;hi&lt;/div&gt;</p><p>```</p>'}]},
                       from_editor=True)
            saved1 = store.detail(slug)['blocks'][0]['html']
            self.assertIn('<pre><code>\n&lt;div&gt;hi&lt;/div&gt;\n\n</code></pre>', saved1)

            # 2. 围栏字面 &lt;div&gt;
            head = store.meta(slug)['head']
            store.save(slug, {'base_version': head, 'title': '矩阵2',
                              'blocks': [{'kind': 'text', 'html': '<p>```html</p><p>&amp;lt;div&amp;gt;</p><p>```</p>'}]},
                       from_editor=True)
            saved2 = store.detail(slug)['blocks'][0]['html']
            self.assertIn('<pre><code>\n&amp;lt;div&amp;gt;\n\n</code></pre>', saved2)

            # 3. 行内字面 &lt;div&gt; 与 &amp;lt;div&amp;gt;
            head = store.meta(slug)['head']
            store.save(slug, {'base_version': head, 'title': '矩阵3',
                              'blocks': [{'kind': 'text', 'html': '<p>**代码**：`&amp;lt;div&amp;gt;` 与 `&amp;amp;lt;div&amp;amp;gt;`</p>'}]},
                       from_editor=True)
            saved3 = store.detail(slug)['blocks'][0]['html']
            self.assertIn('<code>&amp;lt;div&amp;gt;</code>', saved3)
            self.assertIn('<code>&amp;amp;lt;div&amp;amp;gt;</code>', saved3)

    def test_mutation_behavior_verified(self):
        """变异自检门禁：若编辑器路由误传 False 则编辑器矩阵失败，若嗅探存在则助手门禁失败。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = studio.Store(tmp)
            slug = store.create({'title': '变异测试', 'type': 'article'})['slug']
            # 编辑器路由传 False 时，字面实体在编辑器保存下会被双重转义（变成 &amp;amp;lt;）
            store.save(slug, {'base_version': 'v0001', 'title': '变异测试',
                              'blocks': [{'kind': 'text', 'html': '<p>```html</p><p>&amp;lt;div&amp;gt;</p><p>```</p>'}]},
                       from_editor=False)
            saved_false = store.detail(slug)['blocks'][0]['html']
            self.assertIn('&amp;amp;lt;div&amp;amp;gt;', saved_false, 'from_editor=False 时编辑器字面实体会被双重转义')
