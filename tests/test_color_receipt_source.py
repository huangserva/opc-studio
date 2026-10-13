"""R06：颜色回执的「未生效」说明必须说对**获胜来源**。

`server.SafeHTML._lost_detail` 过去只看属性在不在，不看它是否真的落盘。于是
`<span data-color="purple" data-color="blue" style="color:red" style="color:blue">`
里，首项 data 的 purple 非法被丢、红色其实来自首个 style，回执却写
「data-color=blue 被 data-color=red 覆盖」——凭空造出一个输入里并不存在的
`data-color=red`（coordination/review-format-receipt-m2c-report.md 的 L1）。

正文实际保留的颜色（红）本来就是对的，这里只修提示的来源表述。
"""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# server.py 会加载 article-importer 兼容入口，而它把**正式工作台**插到 sys.path 最前
# （article-importer/scripts/article_importer.py:5）。测试副本里必须先把本副本的模块
# 钉进 sys.modules，再加载 server.py，并在加载后把那条污染从 sys.path 里撤掉；否则
# 同批其它测试文件之后 import 到的是共享工作区那份，等于没测
# （tests/test_content_markdown.py 的 UnderTestCopyTests 就是守这条）。
import assistant_policy  # noqa: E402,F401
import markdown_blocks  # noqa: E402,F401

_snapshot = list(sys.path)

spec = importlib.util.spec_from_file_location('studio', ROOT / 'server.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)

for _entry in [p for p in sys.path if p not in _snapshot and Path(p).resolve() != ROOT.resolve()]:
    sys.path.remove(_entry)

assistant_spec = importlib.util.spec_from_file_location('assistant_agent', ROOT / 'assistant_agent.py')
assistant = importlib.util.module_from_spec(assistant_spec)
assistant_spec.loader.exec_module(assistant)


def receipt(html):
    """清洗一遍，返回 (落盘 HTML, 该 html 里 requested 命中的一条 dropped)。"""
    rich, _, dropped, _ = studio.clean_html_full(html)
    return rich, dropped


def detail_for(dropped, requested):
    """取某条 requested 的 hint，只留「具体原因：」之后的说明部分。"""
    entry = next(item for item in dropped if item['requested'] == requested)
    hint = entry['hint']
    return hint.split('具体原因：', 1)[1] if '具体原因：' in hint else hint


class LostStyleSourceTests(unittest.TestCase):
    """获胜积木来自哪里，回执就得说哪里（R06）。"""

    def test_invalid_first_data_color_is_not_blamed_for_the_win(self):
        """首项 data 非法、红色其实来自首个 style：不得说成「被 data-color=red 覆盖」。"""
        html = '<p><span data-color="purple" data-color="blue" style="color:red" style="color:blue">字</span></p>'
        rich, dropped = receipt(html)
        self.assertEqual(rich, '<p><span data-color="red">字</span></p>',
                         '正文实际保留的颜色不变')
        detail = detail_for(dropped, 'style="color:blue"')
        self.assertNotIn('被 data-color=red 覆盖', detail,
                         '输入里根本没有合法的 data-color=red：' + detail)
        self.assertIn('data-color=red', detail, '要说清实际落盘的是什么：' + detail)
        self.assertIn('style', detail, '要说清红色来自 style：' + detail)

    def test_invalid_first_data_with_repeated_decl_in_one_style(self):
        """同一条 style 内重复声明：获胜者同样来自 style，不是 data 覆盖。"""
        html = '<p><span data-color="purple" style="color:red;color:blue">字</span></p>'
        rich, dropped = receipt(html)
        self.assertEqual(rich, '<p><span data-color="red">字</span></p>')
        detail = detail_for(dropped, 'style="color:red;color:blue"')
        self.assertNotIn('被 data-color=red 覆盖', detail, detail)
        self.assertIn('data-color=red', detail, detail)
        self.assertIn('style', detail, detail)

    def test_block_level_align_source_follows_the_winner(self):
        """段落积木同理：非法 data-align 首项不能冒充居中效果的来源。"""
        html = '<p data-align="wide" style="text-align:center" style="text-align:right">字</p>'
        rich, dropped = receipt(html)
        self.assertEqual(rich, '<p data-align="center">字</p>')
        detail = detail_for(dropped, 'style="text-align:right"')
        self.assertNotIn('被 data-align=center 覆盖', detail, detail)
        self.assertIn('data-align=center', detail, detail)
        self.assertIn('style', detail, detail)

    def test_each_lost_effect_names_its_own_winner_source(self):
        """同一标签里高亮真被 data 覆盖、颜色来自 style：两条来源要各说各的。"""
        html = ('<p><span data-hl="green" data-color="purple" '
                'style="color:red;background:yellow" style="color:blue;background:pink">字</span></p>')
        rich, dropped = receipt(html)
        self.assertEqual(rich, '<p><span data-color="red" data-hl="green">字</span></p>',
                         'data 优先与 style 补位的落盘策略不变')
        detail = detail_for(dropped, 'style="color:blue;background:pink"')
        self.assertIn('data-color=blue 未采用', detail, detail)
        self.assertNotIn('data-color=blue 被 data-color=red 覆盖', detail,
                         '红色来自 style，不能说成 data 覆盖：' + detail)
        self.assertIn('data-hl=pink 被 data-hl=green 覆盖', detail,
                      '高亮确实被合法的 data-hl 首项覆盖：' + detail)

    def test_legitimate_data_attribute_still_reports_data_coverage(self):
        """首项 data 合法并真的落盘时，「被 data-* 覆盖」的说法保持原样。"""
        html = '<p><span data-color="red" style="color:blue">字</span></p>'
        rich, dropped = receipt(html)
        self.assertEqual(rich, '<p><span data-color="red">字</span></p>')
        detail = detail_for(dropped, 'style="color:blue"')
        self.assertIn('data-color=blue 被 data-color=red 覆盖', detail, detail)

    def test_effect_with_no_winner_at_all_still_says_unlanded(self):
        """该积木根本没落盘（首个 style 里没有同类声明）：仍说「未落盘」，不编来源。"""
        html = '<p><span style="font-size:20px" style="color:blue">字</span></p>'
        rich, dropped = receipt(html)
        self.assertEqual(rich, '<p><span data-size="l">字</span></p>')
        detail = detail_for(dropped, 'style="color:blue"')
        self.assertIn('data-color=blue 未落盘', detail, detail)
        self.assertNotIn('来自', detail, detail)

    def test_user_visible_format_text_states_the_real_source(self):
        """模型照抄的 format_text 也要说对来源，同时把真生效的红字列进已生效。"""
        html = '<p><span data-color="purple" data-color="blue" style="color:red" style="color:blue">字</span></p>'
        rich, dropped = receipt(html)
        text = assistant.format_text_receipt(
            {'applied': [], 'dropped': dropped}, [], [{'kind': 'text', 'html': rich}])
        applied, dropped_part = text.split('未生效格式', 1)
        self.assertIn('红色文字', applied, '真生效的红字必须列进已生效：' + text)
        self.assertIn('style="color:blue"', dropped_part)
        self.assertNotIn('被 data-color=red 覆盖', dropped_part, dropped_part)
        self.assertIn('data-color=red', dropped_part, dropped_part)


if __name__ == '__main__':
    unittest.main()
