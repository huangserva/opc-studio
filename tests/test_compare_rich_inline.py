"""版本对比：格式化文本内逐字标记与白名单安全。"""
import copy
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

import server


class Fragment(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.stack, self.data, self.marks, self.attrs = [], [], [], []
        self.feed(markup)
        self.close()
        if self.stack:
            raise AssertionError('未闭合标签：' + str(self.stack))

    def handle_starttag(self, tag, attrs):
        self.attrs.extend((tag, name, value) for name, value in attrs)
        if tag not in ('br', 'hr'):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise AssertionError('标签错嵌套：' + tag)

    def handle_data(self, text):
        self.data.append(text)
        for tag in ('ins', 'del'):
            if tag in self.stack:
                self.marks.append((tag, text))

    @property
    def text(self):
        return ''.join(self.data)


class CompareRichInlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = server.Store(self.temp.name)
        self.slug = self.store.create({'title': '对比测试', 'type': 'article'})['slug']

    def save(self, blocks, title='对比测试'):
        return self.store.save(self.slug, {'base_version': self.store.meta(self.slug)['head'],
            'title': title, 'blocks': blocks, 'summary': '测试保存'})['version']

    def compare(self, before, after):
        a = self.save([{'kind': 'text', 'html': before}])
        b = self.save([{'kind': 'text', 'html': after}])
        return self.store.compare(self.slug, a, b)['changes'][0]

    def rich(self, change):
        return Fragment(change['rich_inline_before']), Fragment(change['rich_inline_after'])

    def test_plain_text_keeps_legacy_fields_and_word_marks(self):
        c = self.compare('<p>甲旧乙</p>', '<p>甲新乙</p>')
        self.assertEqual(c['inline_before'], '甲<del>旧</del>乙')
        self.assertEqual(c['inline_after'], '甲<ins>新</ins>乙')
        a, b = self.rich(c)
        self.assertEqual(a.marks, [('del', '旧')])
        self.assertEqual(b.marks, [('ins', '新')])

    def test_text_to_red_yellow_center_preserves_all_formats(self):
        c = self.compare('<p>甲旧乙</p>', '<p data-align="center"><span data-color="red" data-hl="yellow">甲新乙</span></p>')
        a, b = self.rich(c)
        self.assertEqual(a.text, '甲旧乙')
        self.assertEqual(b.text, '甲新乙')
        self.assertIn(('p', 'data-align', 'center'), b.attrs)
        self.assertIn(('span', 'data-color', 'red'), b.attrs)
        self.assertIn(('span', 'data-hl', 'yellow'), b.attrs)
        self.assertEqual(b.marks, [('ins', '新')])

    def test_format_only_red_to_blue_has_no_text_changes(self):
        c = self.compare('<p><span data-color="red">同一段</span></p>', '<p><span data-color="blue">同一段</span></p>')
        a, b = self.rich(c)
        self.assertTrue(c['format_changed'])
        self.assertEqual(c['inline_before'], c['inline_after'])
        self.assertEqual(a.marks + b.marks, [])
        self.assertIn(('span', 'data-color', 'red'), a.attrs)
        self.assertIn(('span', 'data-color', 'blue'), b.attrs)

    def test_entities_angles_ampersand_are_not_cut_into_markup(self):
        c = self.compare('<p>&lt;甲&gt; &amp; &#169; 旧</p>', '<p><span data-hl="yellow">&lt;甲&gt; &amp; &#169; 新</span></p>')
        a, b = self.rich(c)
        self.assertEqual(a.text, '<甲> & © 旧')
        self.assertEqual(b.text, '<甲> & © 新')
        self.assertEqual(b.marks, [('ins', '新')])
        self.assertNotIn('<甲>', c['rich_inline_after'])

    def test_links_inline_code_keep_structure_when_diff_crosses_nodes(self):
        c = self.compare('<p><a href="https://example.com/?a=1&amp;b=2">甲旧</a><code>&lt;旧&gt;</code>尾</p>',
                         '<p><a href="https://example.com/?a=1&amp;b=2">甲新</a><code>&lt;新&gt;</code>尾</p>')
        a, b = self.rich(c)
        self.assertEqual(a.text, '甲旧<旧>尾')
        self.assertEqual(b.text, '甲新<新>尾')
        self.assertEqual(b.marks, [('ins', '新'), ('ins', '新')])
        self.assertIn(('a', 'href', 'https://example.com/?a=1&b=2'), b.attrs)
        self.assertIn('<code>', c['rich_inline_after'])

    def test_nested_spans_split_marks_at_tag_boundaries(self):
        c = self.compare('<p><span data-color="red">甲<strong>旧文</strong>尾</span></p>',
                         '<p><span data-color="blue">甲<strong><span data-hl="pink">新文</span></strong>尾</span></p>')
        a, b = self.rich(c)
        self.assertEqual(b.text, '甲新文尾')
        self.assertEqual(b.marks, [('ins', '新')])
        self.assertIn(('span', 'data-hl', 'pink'), b.attrs)

    def test_break_and_paragraph_offsets_preserve_marked_characters(self):
        c = self.compare('<p>头<br>旧</p><p>尾旧</p>', '<p>头<br><span data-color="red">新</span></p><p>尾新</p>')
        a, b = self.rich(c)
        self.assertEqual(b.marks, [('ins', '新'), ('ins', '新')])
        self.assertIn('<br>', c['rich_inline_after'])

    def test_adjacently_changed_text_nodes_never_cross_tags(self):
        c = self.compare('<p><span>甲</span><strong>乙</strong></p>', '<p><span>丙</span><strong>丁</strong></p>')
        a, b = self.rich(c)
        self.assertEqual(a.marks, [('del', '甲'), ('del', '乙')])
        self.assertEqual(b.marks, [('ins', '丙'), ('ins', '丁')])

    def test_insert_delete_no_empty_marks(self):
        c = self.compare('<p>甲乙</p>', '<p>甲新乙</p>')
        a, b = self.rich(c)
        self.assertEqual(a.marks, [])
        self.assertEqual(b.marks, [('ins', '新')])
        self.assertNotIn('<del></del>', c['rich_inline_before'])
        self.assertNotIn('<ins></ins>', c['rich_inline_after'])

    def test_legacy_manifest_is_cleaned_again_before_rich_diff(self):
        a = self.save([{'kind': 'text', 'html': '<p>旧</p>'}])
        b = self.save([{'kind': 'text', 'html': '<p>新</p>'}])
        original = self.store.info
        def unsafe(slug, rev):
            manifest, text, package = original(slug, rev)
            manifest = copy.deepcopy(manifest)
            if rev == b:
                manifest['blocks'][0]['html'] = '<p onclick="alert(1)" style="position:fixed"><span data-color="red" data-hl="neon">新</span><a href="javascript:alert(1)">链</a><script>恶意</script><img onerror="alert(1)"></p>'
            return manifest, text, package
        with mock.patch.object(self.store, 'info', side_effect=unsafe):
            c = self.store.compare(self.slug, a, b)['changes'][0]
        left, right = self.rich(c)
        self.assertEqual(right.text, '新链')
        self.assertIn(('span', 'data-color', 'red'), right.attrs)
        self.assertFalse(any(name in ('onclick', 'style', 'onerror', 'href') or name == 'data-hl' for _, name, _ in right.attrs))
        self.assertNotIn('恶意', right.text)

    def test_legacy_unclosed_spans_are_balanced_in_comparison(self):
        c = self.compare('<p>旧</p>', '<p><span data-color="red">新')
        left, right = self.rich(c)
        self.assertEqual(right.text, '新')
        self.assertEqual(right.marks, [('ins', '新')])

    def test_insert_delete_title_cover_studio_contract_stays_unchanged(self):
        a = self.save([{'kind': 'text', 'html': '<p>保留</p>'}])
        b = self.save([{'index': 0}, {'kind': 'text', 'html': '<p data-align="center">新增</p>'}], title='新标题')
        c = self.store.compare(self.slug, a, b)
        self.assertTrue(c['title_changed'])
        self.assertFalse(c['cover_changed'])
        self.assertIn('studio_before', c)
        self.assertEqual(c['changes'][0]['type'], 'insert')
        self.assertNotIn('rich_inline_after', c['changes'][0])
        self.assertIn('data-align="center"', c['changes'][0]['after'][0]['html'])
        deleted = self.store.compare(self.slug, b, a)['changes'][0]
        self.assertEqual(deleted['type'], 'delete')
        self.assertNotIn('rich_inline_before', deleted)
