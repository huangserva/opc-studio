"""样式积木（fixes-rich-style 第一段 + 1b 返工）的往返、清洗与出口一致性测试。

变异敏感性：
- 放开任意属性/任意 style → test_arbitrary_styles_and_events_are_stripped 变红；
- 去掉 data-* 取值校验 → test_invalid_block_values_are_dropped 变红；
- 去掉纯 hr 块保留 → test_pure_hr_block_survives_save 变红；
- 公众号导出丢积木 → test_wechat_export_maps_blocks_to_inline_styles 变红；
- X 缺降级提示 → test_x_post_notes_unsupported_formats 变红；
- 1b：字号声明缺分号 → test_m1_*；mark 闭标签丢失 → test_m2_*；
  li 积木不换算 → test_m3_*；strong 主题色盖用户色 → test_m4_*；
  style 映射绕过白名单 → test_m5_*；x/article 缺提示 → test_m6_*；
  unchanged 丢原因 → test_m7_*；离线段距与页面不一致 → test_l1_*；
- 1c：a 标签去掉校验记录调用 → test_a_* / test_unchanged_save_on_link_*。
"""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

import sys
ROOT = Path(__file__).resolve().parents[1]


def _publishing_modules():
    return {n: m for n, m in sys.modules.items()
            if n == 'publishing' or n.startswith('publishing.')}


# **先把 publishing.* 从 sys.modules 里清掉，再从本副本导入**。
# server.py 装载 article-importer 兼容入口时会把正式工作台插到 sys.path 最前
# （副本里 article-importer 是指向正式目录的符号链接）。unittest discover 同一进程里
# 前面若有别的测试模块加载过 server.py，`publishing.*` 早就被解析到正式目录并缓存进
# sys.modules；此时光 `sys.path.insert(0, ROOT)` 不够——import 直接命中缓存，
# 于是 `RICH_PREVIEW_CSS` 报 ImportError，测的其实是正式目录而不是这次合并的产物。
_saved_publishing = _publishing_modules()
for _name in _saved_publishing:
    del sys.modules[_name]
sys.path.insert(0, str(ROOT))
import publishing.core.article_package as article_package
import publishing.platforms.wechat as wechat
import publishing.platforms.x.post as x_post
import publishing.platforms.x.article as x_article
from publishing.core.article_package import RICH_PREVIEW_CSS

for _mod in (article_package, wechat, x_post, x_article):
    assert Path(_mod.__file__).resolve().is_relative_to(ROOT.resolve()), (
        'publishing 模块解析到了副本之外：%s' % _mod.__file__)

_own_publishing = _publishing_modules()

spec = importlib.util.spec_from_file_location('studio', ROOT / 'server.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)

import rich_text

# **把自己这份 publishing.* 收进私有命名空间，再把 sys.modules 还给原来那份。**
# discover 是「先 import 全部测试模块、再跑」，所以本模块 import 发生时，
# 同进程里别的测试模块（test_local_publishing 等）已经各自 import 过 publishing.* 了。
# 若把本副本那份留在 sys.modules 里，全进程就只剩一份 publishing，
# 别的模块再 import 会拿到本副本的对象，于是 isolation_patches.module_time
# 打在旧模块对象上、而被测代码读的是新对象，monotonic 补丁静默失效
# （实测 test_local_publishing.test_connection_expires… 变红）。
# 还原 sys.modules 之后：别的测试继续用它们自己那份，本模块用上面已绑定的私有引用。
for _name, _mod in _saved_publishing.items():
    sys.modules[_name] = _mod
for _name in [n for n in _own_publishing if n not in _saved_publishing]:
    del sys.modules[_name]
_publishing = _own_publishing


class SanitizeTests(unittest.TestCase):
    def test_every_block_roundtrips_and_is_idempotent(self):
        cases = [
            '<span data-color="red">关键结论</span>',
            '<mark>重点</mark>',
            '<span data-hl="pink">粉底</span>',
            '<p data-size="xl">大字</p>',
            '<p data-align="center">居中</p>',
            '<p data-indent="2">缩进</p>',
            '<p data-space="loose">疏朗</p>',
            '<hr>',
            '<blockquote data-align="right" data-indent="2" data-space="tight" data-size="l">引用</blockquote>',
            '<li data-align="center">列表项</li>',
        ]
        for markup in cases:
            rich, plain, dropped = rich_text.sanitize(markup)
            self.assertEqual(dropped, [], markup)
            again, _, _ = rich_text.sanitize(rich)
            self.assertEqual(again, rich, markup)

    def test_common_inline_styles_map_to_blocks(self):
        rich, _, dropped = rich_text.sanitize('<span style="color:red">甲</span>')
        self.assertEqual(rich, '<span data-color="red">甲</span>')
        self.assertEqual(dropped, [])
        rich, _, _ = rich_text.sanitize('<p style="text-align:center;text-indent:2em">乙</p>')
        self.assertIn('data-align="center"', rich)
        self.assertIn('data-indent="2"', rich)
        rich, _, _ = rich_text.sanitize('<span style="background-color:#ffc0cb">丙</span>')
        self.assertEqual(rich, '<span data-hl="pink">丙</span>')
        rich, _, _ = rich_text.sanitize('<p style="font-size:24px">丁</p>')
        self.assertEqual(rich, '<p data-size="l">丁</p>')

    def test_b_and_i_normalized(self):
        rich, _, _ = rich_text.sanitize('<p><b>粗</b><i>斜</i></p>')
        self.assertEqual(rich, '<p><strong>粗</strong><em>斜</em></p>')

    def test_arbitrary_styles_and_events_are_stripped(self):
        rich, _, _ = rich_text.sanitize(
            '<p onclick="x()" style="position:fixed;width:10px">正文'
            '<script>alert(1)</script><a href="javascript:x()">链接</a>'
            '<span data-evil="1" style="font-family:serif">坏</span></p>')
        self.assertNotIn('onclick', rich)
        self.assertNotIn('script', rich)
        self.assertNotIn('alert', rich)
        self.assertNotIn('javascript', rich)
        self.assertNotIn('style=', rich)
        self.assertNotIn('position', rich)
        self.assertNotIn('font-family', rich)
        self.assertNotIn('data-evil', rich)
        safe, _, _ = rich_text.sanitize('<a href="https://example.com">链</a>')
        self.assertEqual(safe, '<a href="https://example.com" rel="noopener noreferrer">链</a>')

    def test_invalid_block_values_are_dropped(self):
        rich, _, dropped = rich_text.sanitize(
            '<span data-color="purple" data-hl="neon" data-size="3em">甲</span>'
            '<p data-align="up" data-indent="5" data-space="wild" data-size="3em">乙</p>')
        self.assertEqual(rich, '<span>甲</span><p>乙</p>')
        self.assertIn('data-color 的取值 purple 不受支持', dropped)
        self.assertIn('data-indent 的取值 5 不受支持', dropped)
        rich, _, dropped = rich_text.sanitize('<p data-color="red">丙</p><span data-align="center">丁</span>')
        self.assertNotIn('data-', rich)
        self.assertTrue(any('不能用在' in d for d in dropped))

    def test_wechat_export_maps_blocks_to_inline_styles(self):
        def render(blocks):
            return wechat.render({'studio': {'type': 'article'}, 'target': {'title': '标题'},
                                  'manifest': {'title': '标题', 'blocks': blocks},
                                  'slug': 's', 'version': 'v0001', 'package': '/nonexistent'})
        out = render([{'kind': 'text', 'html': '<p data-align="center">居中</p>'}])
        self.assertIn('text-align:center', out)
        self.assertNotIn('text-align:justify', out)
        out = render([{'kind': 'text', 'html': '<p data-indent="2" data-space="loose">缩进</p>'}])
        self.assertIn('text-indent:2em', out)
        self.assertIn('margin:30px 0', out)
        out = render([{'kind': 'text', 'html': '<p data-size="xl">大字</p>'}])
        self.assertIn('font-size:26px', out)
        out = render([{'kind': 'text', 'html': '<span data-color="red">红</span>'}])
        self.assertIn('color:#d63031', out)
        out = render([{'kind': 'text', 'html': '<mark>高亮</mark><span data-hl="green">绿底</span>'}])
        self.assertIn('background-color:#fff3ad', out)
        self.assertIn('background-color:#d3f2d9', out)
        self.assertNotIn('<mark', out)  # mark 降为 span
        out = render([{'kind': 'text', 'html': '<hr>'}])
        self.assertIn('<hr style="border:none', out)

    def test_x_post_notes_unsupported_formats(self):
        snapshot = {'studio': {'type': 'post'}, 'text': '正文',
                    'target': {}, 'manifest': {'title': '标题', 'blocks': [
                        {'kind': 'text', 'html': '<p data-color="red"><span data-hl="yellow">甲</span></p>',
                         'text': '甲', 'display_text': '甲'},
                        {'kind': 'text', 'html': '<hr>', 'text': '', 'display_text': ''}],
                    'body_media': []}}
        result = x_post.prepare(snapshot, {'channel_id': '123', 'mode': 'draft'})
        note = ''.join(result['notes'])
        self.assertIn('以下格式 X 不支持', note)
        self.assertIn('颜色', note)
        self.assertIn('高亮', note)
        self.assertIn('分隔线', note)
        self.assertNotIn(note, result['blocking'])  # 只提示，不阻止
        self.assertEqual(result['status'], 'payload_ready')
        clean = dict(snapshot, manifest={'title': '标题', 'blocks': [
            {'kind': 'text', 'html': '<p>甲</p>', 'text': '甲', 'display_text': '甲'}], 'body_media': []})
        result = x_post.prepare(clean, {'channel_id': '123', 'mode': 'draft'})
        self.assertFalse(any('X 不支持' in n for n in result['notes']))


class StoreRoundTripTests(unittest.TestCase):
    RICH_BLOCKS = [
        {'kind': 'text', 'html': '<p><span data-color="red">关键结论</span>与<span data-hl="yellow">高亮</span></p>'},
        {'kind': 'text', 'html': '<p data-align="center" data-indent="2" data-space="loose" data-size="l">整段积木</p>'},
        {'kind': 'text', 'html': '<h2>小标题</h2>'},
        {'kind': 'text', 'html': '<p>正文前</p>'},
        {'kind': 'text', 'html': '<hr>'},
        {'kind': 'text', 'html': '<p>正文后</p>'},
    ]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = studio.Store(self.temp.name)
        created = self.store.create({'title': '样式积木作品'})
        self.slug = created['slug']

    def tearDown(self):
        self.temp.cleanup()

    def test_save_read_restore_roundtrip(self):
        saved = self.store.save(self.slug, {'base_version': 'v0001', 'title': '样式积木作品',
                                            'blocks': self.RICH_BLOCKS, 'summary': '积木'})
        self.assertEqual(saved['version'], 'v0002')
        self.assertEqual(saved['dropped_formats'], [])
        blocks = self.store.detail(self.slug)['blocks']
        expected = [rich_text.sanitize(b['html'])[0] for b in self.RICH_BLOCKS]
        self.assertEqual([b['html'] for b in blocks if b['kind'] == 'text'], expected)
        restored_back = self.store.restore(self.slug, {'base_version': 'v0002', 'version': 'v0001'})
        self.assertEqual(restored_back['version'], 'v0003')
        self.assertEqual(self.store.detail(self.slug)['blocks'], [])
        again = self.store.restore(self.slug, {'base_version': 'v0003', 'version': 'v0002'})
        self.assertEqual(again['version'], 'v0004')
        blocks = self.store.detail(self.slug)['blocks']
        self.assertIn('<span data-color="red">', blocks[0]['html'])
        self.assertIn('data-align="center"', blocks[1]['html'])
        self.assertEqual(blocks[4]['html'], '<hr>')

    def test_pure_hr_block_survives_save(self):
        saved = self.store.save(self.slug, {'base_version': 'v0001', 'title': '样式积木作品',
                                            'blocks': [{'kind': 'text', 'html': '<hr>'}], 'summary': 'hr'})
        self.assertEqual(saved['version'], 'v0002')
        blocks = self.store.detail(self.slug)['blocks']
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['html'], '<hr>')

    def test_save_receipt_reports_dropped_formats(self):
        saved = self.store.save(self.slug, {'base_version': 'v0001', 'title': '样式积木作品', 'blocks': [
            {'kind': 'text', 'html': '<p style="font-family:serif">正文</p>'},
            {'kind': 'text', 'html': '<span data-color="purple">坏色</span>'}], 'summary': '丢弃'})
        self.assertEqual(saved['version'], 'v0002')
        self.assertIn('样式 font-family 不支持用于 p', saved['dropped_formats'])
        self.assertIn('data-color 的取值 purple 不受支持', saved['dropped_formats'])


class RichStyle1bTests(unittest.TestCase):
    """1b 返工：review-rich-style-1 的 M1–M7 + L1，每项至少一个变异红例。"""

    def render(self, blocks):
        return wechat.render({'studio': {'type': 'article'}, 'target': {'title': '标题'},
                              'manifest': {'title': '标题', 'blocks': blocks},
                              'slug': 's', 'version': 'v0001', 'package': '/nonexistent'})

    # M1：字号与颜色/高亮组合，每条声明必须以分号结尾，字号只出现一次。
    def test_m1_size_plus_color_keeps_semicolon(self):
        out = self.render([{'kind': 'text', 'html': '<span data-color="red" data-size="xl">大红字</span>'}])
        self.assertIn('font-size:26px;color:#d63031;', out)
        self.assertNotIn('26pxcolor', out)
        out = self.render([{'kind': 'text', 'html': '<span data-hl="pink" data-size="l">粉底大字</span>'}])
        self.assertIn('font-size:20px;background-color:#ffd9e2;', out)
        self.assertNotIn('20pxbackground', out)
        out = self.render([{'kind': 'text', 'html': '<p data-size="xl">大段</p>'}])
        self.assertEqual(out.count('font-size:26px'), 1)

    # M2：mark 结束标签必须跟着改写成 </span>，高亮不得外溢。
    def test_m2_mark_endtag_closes_highlight(self):
        out = self.render([{'kind': 'text', 'html': '<p><mark>高亮</mark>普通尾文</p>'}])
        self.assertIn('</span>普通尾文', out)
        self.assertNotIn('<mark', out)
        out = self.render([{'kind': 'text',
                            'html': '<p><mark>甲</mark>中<mark>乙</mark>尾</p>'}])
        self.assertIn('</span>中<span style="background-color:#fff3ad;padding:0 4px;">乙</span>尾', out)

    # M3：li 的四项块级积木都要换算成内联 style。
    def test_m3_li_blocks_map_to_inline_styles(self):
        out = self.render([{'kind': 'text',
                            'html': '<ul><li data-align="right" data-indent="2" data-space="loose" data-size="xl">列表</li></ul>'}])
        self.assertIn('<li style="text-align:right;text-indent:2em;margin:30px 0;font-size:26px;">', out)
        self.assertNotIn('<li>列表</li>', out)

    # M4：用户显式颜色作用范围内 strong 不套主题强调色；范围外保持主题色。
    def test_m4_strong_theme_color_yields_to_user_color(self):
        out = self.render([{'kind': 'text',
                            'html': '<p><span data-color="red"><strong>红色粗体</strong></span></p>'}])
        self.assertIn('<strong style="font-weight:bold;">', out)
        self.assertNotIn('#d4237a', out)
        # 反向嵌套：strong 在外、span 显式红在内，span 自己的颜色声明生效。
        out = self.render([{'kind': 'text',
                            'html': '<p><strong><span data-color="red">红</span></strong></p>'}])
        self.assertIn('<strong style="color:#d4237a;">', out)
        self.assertIn('<span style="color:#d63031;">', out)
        # 同级相邻：颜色作用域结束后的 strong 恢复主题色。
        out = self.render([{'kind': 'text',
                            'html': '<p><span data-color="red">红</span><strong>普通粗体</strong></p>'}])
        self.assertIn('<strong style="color:#d4237a;">', out)
        # 嵌套退出后恢复主题色。
        out = self.render([{'kind': 'text',
                            'html': '<p><span data-color="red">红<strong>内粗</strong></span><strong>外粗</strong></p>'}])
        self.assertEqual(out.count('<strong style="font-weight:bold;">'), 1)
        self.assertIn('<strong style="color:#d4237a;">外粗</strong>', out)

    # M5：style→积木映射与直接 data-* 走同一道标签白名单，且清洗幂等。
    def test_m5_style_mapping_respects_tag_whitelist(self):
        rich, _, dropped = rich_text.sanitize('<strong style="font-size:20px">字</strong>')
        self.assertEqual(rich, '<strong>字</strong>')
        self.assertIn('data-size 不能用在 strong 上', dropped)
        again, _, _ = rich_text.sanitize(rich)
        self.assertEqual(again, rich)
        rich, _, dropped = rich_text.sanitize('<mark style="color:red">标</mark>')
        self.assertEqual(rich, '<mark>标</mark>')
        self.assertIn('颜色 不支持用于 mark', dropped)
        rich, _, dropped = rich_text.sanitize('<hr style="font-size:20px">')
        self.assertEqual(rich, '<hr>')
        self.assertTrue(any('data-size' in d for d in dropped))
        # 合法路径不受影响：span 上的字号仍然映射。
        rich, _, dropped = rich_text.sanitize('<span style="font-size:20px">字</span>')
        self.assertEqual(rich, '<span data-size="l">字</span>')
        self.assertEqual(dropped, [])

    # M6：X 长文章准备路径也报告不支持格式；阻断规则维持原样。
    def test_m6_x_article_notes_unsupported_formats(self):
        snapshot = {'studio': {'type': 'article'}, 'target': {'account': 'test_account'},
                    'package': '/tmp/nonexistent', 'manifest': {'title': '标题', 'blocks': [
                        {'kind': 'text', 'html': '<span data-color="red">红字</span><hr>',
                         'text': '红字', 'display_text': '红字'}], 'body_media': []}}
        result = x_article.prepare(snapshot, {})
        self.assertEqual(result['status'], 'handoff_ready')
        note = ''.join(result['notes'])
        self.assertIn('以下格式 X 不支持', note)
        self.assertIn('颜色', note)
        self.assertIn('分隔线', note)
        clean = dict(snapshot, manifest={'title': '标题', 'blocks': [
            {'kind': 'text', 'html': '<p>正文</p>', 'text': '正文', 'display_text': '正文'}],
            'body_media': []})
        result = x_article.prepare(clean, {})
        self.assertEqual(result['status'], 'handoff_ready')
        self.assertFalse(any('X 不支持' in n for n in result['notes']))


class RichStyle1cTests(unittest.TestCase):
    """1c 返工：a 标签丢弃回执漏口——href 照旧单独处理，但校验与回执对齐其它标签。"""

    # 变异红例：去掉 handle_starttag a 分支的 _rich_attrs 校验调用 → 本类全部变红。
    def test_a_style_mapping_reports_drop(self):
        rich, _, dropped = rich_text.sanitize('<a style="font-size:26px">字</a>')
        self.assertEqual(rich, '<a>字</a>')
        self.assertIn('data-size 不能用在 a 上', dropped)
        # style 映射的其它去向也照记。
        rich, _, dropped = rich_text.sanitize('<a style="font-family:serif">文</a>')
        self.assertEqual(rich, '<a>文</a>')
        self.assertIn('样式 font-family 不支持用于 a', dropped)

    def test_a_direct_data_size_reports_drop_and_keeps_href(self):
        rich, _, dropped = rich_text.sanitize('<a data-size="xl" href="https://example.com">链接正文</a>')
        self.assertEqual(rich, '<a href="https://example.com" rel="noopener noreferrer">链接正文</a>')
        self.assertIn('data-size 不能用在 a 上', dropped)
        # a 仍然不带任何积木属性；合法链接不受影响。
        safe, _, dropped = rich_text.sanitize('<a href="https://example.com">正常</a>')
        self.assertEqual(safe, '<a href="https://example.com" rel="noopener noreferrer">正常</a>')
        self.assertEqual(dropped, [])
        again, _, _ = rich_text.sanitize(rich)
        self.assertEqual(again, rich)


class Store1cTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = studio.Store(self.temp.name)
        self.slug = self.store.create({'title': '1c 链接回执'})['slug']

    def tearDown(self):
        self.temp.cleanup()

    def test_unchanged_save_on_link_reports_dropped_reason(self):
        first = self.store.save(self.slug, {'base_version': 'v0001', 'title': '1c 链接回执',
                                            'blocks': [{'kind': 'text',
                                                        'html': '<p><a href="https://example.com">链接正文</a></p>'}],
                                            'summary': '首存'})
        self.assertEqual(first['version'], 'v0002')
        self.assertEqual(first['dropped_formats'], [])
        # 两入口：style 映射、直接 data-size，都是 unchanged 且回执带原因。
        for html in ('<p><a href="https://example.com" style="font-size:26px">链接正文</a></p>',
                     '<p><a href="https://example.com" data-size="xl">链接正文</a></p>'):
            again = self.store.save(self.slug, {'base_version': 'v0002', 'title': '1c 链接回执',
                                                'blocks': [{'kind': 'text', 'html': html}],
                                                'summary': '链接上加非法字号'})
            self.assertTrue(again['unchanged'], html)
            self.assertIn('data-size 不能用在 a 上', again['dropped_formats'], html)
        # 回读确认字号确实未落库，链接完好。
        block = self.store.detail(self.slug)['blocks'][0]
        self.assertEqual(block['html'],
                         '<p><a href="https://example.com" rel="noopener noreferrer">链接正文</a></p>')


class Store1bTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = studio.Store(self.temp.name)
        self.slug = self.store.create({'title': '1b 回执'})['slug']

    def tearDown(self):
        self.temp.cleanup()

    # M7：unchanged 回执必须带上本次真实丢弃原因。
    def test_m7_unchanged_receipt_reports_dropped(self):
        first = self.store.save(self.slug, {'base_version': 'v0001', 'title': '1b 回执',
                                            'blocks': [{'kind': 'text', 'html': '<p>正文</p>'}],
                                            'summary': '首存'})
        self.assertEqual(first['version'], 'v0002')
        again = self.store.save(self.slug, {'base_version': 'v0002', 'title': '1b 回执',
                                            'blocks': [{'kind': 'text', 'html': '<p style="font-family:serif">正文</p>'}],
                                            'summary': '只换不支持的样式'})
        self.assertTrue(again['unchanged'])
        self.assertIn('样式 font-family 不支持用于 p', again['dropped_formats'])
        # 干净重存仍是空回执。
        clean = self.store.save(self.slug, {'base_version': 'v0002', 'title': '1b 回执',
                                            'blocks': [{'kind': 'text', 'html': '<p>正文</p>'}],
                                            'summary': '干净'})
        self.assertTrue(clean['unchanged'])
        self.assertEqual(clean['dropped_formats'], [])

    # L1：离线预览 CSS 与作品页 style.css 的段距/hr/mark 同值。
    def test_l1_offline_preview_css_matches_style_css(self):
        style = (ROOT / 'public' / 'style.css').read_text()
        for token in ('margin:.4em 0', 'margin:.9em 0', 'margin:1.6em 0', 'margin:30px 0'):
            self.assertIn(token, RICH_PREVIEW_CSS, token)
            self.assertIn(token, style, token)
        for token in ('padding:0 2px;border-radius:3px', 'border-top:2px solid #dfe4ee'):
            self.assertIn(token, RICH_PREVIEW_CSS, token)
            self.assertIn(token, style, token)
        # 不得再出现旧 px 段距。
        self.assertNotIn('margin:8px 0', RICH_PREVIEW_CSS)
        self.assertNotIn('margin:28px 0', RICH_PREVIEW_CSS)


class Round13M2Tests(unittest.TestCase):
    """第 13 轮 M2：同元素红字 + 黄底，高亮规则不得抢走文字颜色。

    浏览器里的计算颜色由 Chrome 抽查覆盖；这里守住样式表本身的两条不变量：
    高亮只给底色（不声明 color），而 mark 的 color:inherit 原行为一字不动。
    """

    def test_highlight_rules_do_not_declare_color(self):
        style = (ROOT / 'public' / 'style.css').read_text()
        for hl in ('yellow', 'green', 'pink'):
            rule = re.search(r'#paper \[data-hl=' + hl + r'\][^{]*\{([^}]*)\}', style)
            self.assertIsNotNone(rule, 'style.css 缺 [data-hl=%s] 规则' % hl)
            self.assertNotIn('color', rule.group(1),
                             '[data-hl=%s] 声明 color 会盖掉同元素的 data-color' % hl)
        rule = re.search(r'\[data-hl=yellow\]\{([^}]*)\}', RICH_PREVIEW_CSS)
        self.assertIsNotNone(rule, 'X 预览缺 [data-hl=yellow] 规则')
        self.assertNotIn('color', rule.group(1), 'X 预览同样不得声明 color')

    def test_mark_keeps_color_inherit_in_both_bundles(self):
        style = (ROOT / 'public' / 'style.css').read_text()
        self.assertRegex(style, r'(?m)^mark\{[^}]*color:inherit', 'mark 原行为必须保留')
        self.assertRegex(RICH_PREVIEW_CSS, r'mark\{[^}]*color:inherit')
        # 高亮积木不再和 mark 共用一条选择器。
        self.assertNotIn('mark,#paper [data-hl', style)
        self.assertNotIn('mark,[data-hl', RICH_PREVIEW_CSS)


if __name__ == '__main__':
    unittest.main()
