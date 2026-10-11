"""fixes-b5：原文链接边界、过渡引号、礼貌前缀、同一响应停止。临时工作区，无外网。"""
import importlib
import json
import sys
import time
import unittest
from pathlib import Path

from assistant_policy import paste_links_allowed, paste_originals
from tests import test_assistant
from tests import test_fixes_b2 as fixtures


class QuoteAndPrefixTests(unittest.TestCase):
    def test_short_sentence_quotes_stay_transitions(self):
        body = '今天发布甲方案。'
        samples = [
            f'把下面贴文贴上，再补一句「下周见！」\n{body}',
            f'把下面贴文贴上，再补一句「还有疑问吗？」\n{body}',
            f'麻烦把下面贴文贴上，再补一句「下周见！」\n{body}',
            '把下面贴文贴上，再补一句「下周见！」原文：今天发布甲方案。',
        ]
        for message in samples:
            with self.subTest(message=message):
                found = paste_originals(message)
                self.assertIn(body, found)
                self.assertNotIn('下周见！', found)
                self.assertNotIn('还有疑问吗？', found)
        labelled = paste_originals('把下面贴文贴上，再补一句「下周见！」原文：今天发布甲方案。')
        self.assertEqual(labelled, ('原文：今天发布甲方案。', body))

    def test_body_like_quotes_still_fail_closed(self):
        long = '今天发布甲方案的完整长文。'
        self.assertGreater(len(long), 8)
        self.assertEqual(paste_originals(f'把下面贴文贴上，再补一句「{long}」\n短'), ())
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句「甲方案。后面还有正文」\n短'), ())
        nine = '一二三四五六七八。'
        self.assertEqual(len(nine), 9)
        self.assertEqual(paste_originals(f'把下面贴文贴上，再补一句「{nine}」\n短'), ())
        eight = '一二三四五六七。'
        self.assertEqual(len(eight), 8)
        self.assertEqual(paste_originals(f'把下面贴文贴上，再补一句「{eight}」\n短'), ('短',))
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句「!?」\n短'), ())

    def test_quote_without_sentence_punctuation_uses_twelve_characters(self):
        dropped = '今天发布甲方案已经上线并开始灰度'
        self.assertGreater(len(dropped), 12)
        self.assertNotRegex(dropped, r'[。！？!?]')
        self.assertEqual(paste_originals(f'再补一句「{dropped}」\n今天发布甲方案。'), ())
        english = 'ship the new plan'
        self.assertEqual(len(english), 17)
        self.assertEqual(paste_originals(f'再补一句「{english}」\n今天发布甲方案。'), ())
        twelve = '一二三四五六七八九十一二'
        thirteen = twelve + '三'
        self.assertEqual(len(twelve), 12)
        self.assertEqual(len(thirteen), 13)
        self.assertEqual(paste_originals(f'把下面贴文贴上，再补一句「{twelve}」\n短'), ('短',))
        self.assertEqual(paste_originals(f'把下面贴文贴上，再补一句「{thirteen}」\n短'), ())

    def test_polite_prefix_allows_pronoun_and_particle_only(self):
        body = '今天发布甲方案。'
        for message in [f'麻烦你把下面贴文贴上\n{body}',
                        f'麻烦您把下面贴文贴上\n{body}',
                        f'帮我把下面贴文贴上吧\n{body}',
                        f'请你把下面贴文贴上呢\n{body}',
                        f'请您把下面贴文贴上啊\n{body}',
                        f'麻烦直接贴上\n{body}']:
            with self.subTest(message=message):
                self.assertEqual(paste_originals(message), (body,))
        for message in ['麻烦把正文贴上作品里\n今天发布甲方案。',
                        '帮我贴一下下面贴文\n今天发布甲方案。',
                        '贴上这段：\n今天发布甲方案。',
                        '贴文：\n今天发布甲方案。',
                        '把下面贴文贴到第一段后面\n今天发布甲方案。',
                        '把下面贴文贴上。甲方案\n乙方案',
                        '麻烦原文：今天发布甲方案。',
                        '请把原文：今天发布甲方案。',
                        '麻烦你把甲方案贴上\n今天发布甲方案。',
                        '帮我把下面贴文贴上吧今天发布甲方案。',
                        '帮我把下面贴文贴上吧，今天发布甲方案']:
            with self.subTest(message=message):
                self.assertEqual(paste_originals(message), ())


class LinkBoundaryB5Tests(unittest.TestCase):
    def allowed(self, href, original):
        return paste_links_allowed(f'<a href="{href}">{href}</a>', original)

    def test_trailing_ascii_and_fullwidth_punctuation_leave_the_url(self):
        clean = 'https://example.com/a'
        rows = [
            '见 https://example.com/a。',
            'see https://example.com/a.',
            'see https://example.com/a, and more',
            'see https://example.com/a!',
            'see (https://example.com/a)',
            '见（https://example.com/a）',
            '见 https://example.com/a——后面',
            '见 https://example.com/a【说明】',
            '见 https://example.com/a……',
            '见 https://example.com/a《说明》',
            '见 https://example.com/a’',
        ]
        for original in rows:
            with self.subTest(original=original):
                self.assertTrue(self.allowed(clean, original))
                self.assertFalse(self.allowed(clean + '。', original))
                self.assertFalse(self.allowed(clean + '.', original))
        self.assertFalse(self.allowed('https://example.com/a）', '见（https://example.com/a）'))
        self.assertFalse(self.allowed('https://example.com/a)', 'see (https://example.com/a)'))
        paired = 'https://example.com/wiki/A_(b)'
        self.assertTrue(self.allowed(paired, f'见 {paired}。'))
        self.assertFalse(self.allowed('https://example.com/wiki/A_(b', f'见 {paired}'))

    def test_scheme_boundary_includes_dot_underscore_and_hyphen(self):
        evil = 'https://evil.example/x'
        for original in ['见 NOT-HTTPS://evil.example/x',
                         '见 not_https://evil.example/x',
                         '见 not.https://evil.example/x',
                         '见 nothttps://evil.example/x']:
            with self.subTest(original=original):
                self.assertFalse(self.allowed(evil, original))
        self.assertTrue(self.allowed('https://evil.example/x', '见不https://evil.example/x'))
        self.assertTrue(self.allowed('https://example.com/a', '见https://example.com/a'))

    def test_default_port_and_empty_path_normalize_only_those_forms(self):
        self.assertTrue(self.allowed('https://example.com/a', '见 https://example.com:443/a'))
        self.assertTrue(self.allowed('https://example.com:443/a', '见 https://example.com/a'))
        self.assertTrue(self.allowed('http://example.com/a', '见 http://example.com:80/a'))
        self.assertTrue(self.allowed('http://example.com:80/a', '见 http://example.com/a'))
        self.assertFalse(self.allowed('https://example.com/a', '见 https://example.com:8443/a'))
        self.assertFalse(self.allowed('https://example.com/a', '见 https://example.com:80/a'))
        self.assertTrue(self.allowed('https://example.com/', '见 https://example.com'))
        self.assertTrue(self.allowed('https://example.com', '见 https://example.com/'))
        self.assertFalse(self.allowed('https://example.com/a', '见 https://example.com/a/'))
        self.assertFalse(self.allowed('https://example.com/a/', '见 https://example.com/a'))

    def test_unescape_is_comparison_only(self):
        original = '见 https://example.com/post?x=1&amp;y=2'
        self.assertTrue(self.allowed('https://example.com/post?x=1&y=2', original))
        self.assertTrue(paste_links_allowed(
            '<a href="https://example.com/post?x=1&amp;y=2">x</a>', original))
        self.assertFalse(self.allowed('https://example.com/post?x=1&y=3', original))

    def test_entity_is_decoded_once(self):
        once = '见 https://example.com/post?x=1&amp;y=2'
        twice = '见 https://example.com/post?x=1&amp;amp;y=2'
        href = 'https://example.com/post?x=1&y=2'
        self.assertTrue(self.allowed(href, once))
        self.assertFalse(self.allowed(href, twice))
        self.assertFalse(paste_links_allowed(
            '<a href="https://example.com/post?x=1&amp;y=2">x</a>', twice))
        self.assertTrue(paste_links_allowed(
            '<a href="https://example.com/post?x=1&amp;amp;y=2">x</a>', twice))

    def test_mailto_local_part_stays_case_sensitive(self):
        original = '见 MAILTO:A@B.C'
        self.assertFalse(self.allowed('mailto:a@b.c', original))
        self.assertTrue(self.allowed('mailto:A@b.c', original))
        self.assertTrue(self.allowed('mailto:a@b.c', '见 MAILTO:a@B.C'))

    def test_empty_href_is_refused(self):
        original = '见 https://example.com/a'
        self.assertFalse(paste_links_allowed('<a href="">x</a>', original))
        self.assertFalse(paste_links_allowed('<a href="https://example.com/a"></a><a href=""></a>', original))
        self.assertTrue(paste_links_allowed('<a>x</a>', original))
        self.assertTrue(self.allowed('https://example.com/a', original))


class SavedPasteTests(unittest.TestCase):
    setUp = fixtures.PasteFixTests.setUp
    tearDown = fixtures.PasteFixTests.tearDown
    turn = fixtures.PasteFixTests.turn
    prepare = fixtures.PasteFixTests.prepare
    edit = fixtures.PasteFixTests.edit

    def test_fullwidth_paren_href_is_refused_and_clean_href_saves(self):
        self.prepare('把下面贴文贴上\n见（https://example.com/a）')
        with self.assertRaisesRegex(ValueError, '原文里没有的链接'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste',
                        'html': '<p>见（<a href="https://example.com/a）">https://example.com/a）</a></p>'}])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        saved = self.edit([{'op': 'insert_text', 'text_source': 'paste',
                            'html': '<p>见（<a href="https://example.com/a">https://example.com/a</a>）</p>'}])
        self.assertEqual(saved['version'], 'v0003')

    def test_trimmed_url_saves_the_submitted_html_unchanged(self):
        markup = '<p>see <a href="https://example.com/a">https://example.com/a</a>.</p>'
        self.prepare('把下面贴文贴上\nsee https://example.com/a.')
        saved = self.edit([{'op': 'insert_text', 'text_source': 'paste', 'html': markup}])
        self.assertEqual(saved['version'], 'v0003')
        stored = self.store.detail(self.slug)['blocks'][-1]['html']
        self.assertIn('href="https://example.com/a"', stored)
        self.assertNotIn('href="https://example.com/a."', stored)
        self.assertIn('</a>.', stored)
        self.assertEqual(self.store.detail(self.slug)['blocks'][-1]['text'].strip(), 'see https://example.com/a.')


class RuntimePathTests(unittest.TestCase):
    def test_runtime_dir_is_inserted_only_when_it_is_not_already_first(self):
        import assistant_agent
        root = str(Path(assistant_agent.__file__).resolve().parent)
        before = list(sys.path)
        try:
            self.assertEqual(sys.path[0], root)
            count = sys.path.count(root)
            sys.path.insert(0, root)
            importlib.reload(assistant_agent)
            self.assertEqual(sys.path.count(root), count + 1)
            sys.path.insert(0, '/tmp')
            importlib.reload(assistant_agent)
            self.assertEqual(sys.path[0], root)
            self.assertEqual(sys.path[1], '/tmp')
        finally:
            sys.path[:] = before
            importlib.reload(assistant_agent)


class SameResponseStopTests(unittest.TestCase):
    setUp = test_assistant.AssistantTests.setUp
    tearDown = test_assistant.AssistantTests.tearDown

    def wait_job(self, result):
        deadline = time.time() + 45
        while self.agent.active and time.time() < deadline:
            time.sleep(0.05)
        self.assertIsNone(self.agent.active)
        return self.agent.public_job(result['id'])

    def tool(self, call_id, name, arguments):
        return {'id': call_id, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments)}}

    def test_two_errors_and_a_later_legal_paste_do_not_save(self):
        message = '把下面贴文贴上\n今天发布甲方案。'
        bad = {'slug': self.slug, 'base_version': 'v0002', 'summary': '抄写', 'operations': [
            {'op': 'insert_text', 'text_source': 'paste', 'html': '<p>贴文</p>'}]}
        legal = {'slug': self.slug, 'base_version': 'v0002', 'summary': '照原文', 'operations': [
            {'op': 'insert_text', 'text_source': 'paste_original'}]}
        calls = []

        def client(messages, **kwargs):
            calls.append(1)
            if len(calls) > 2:
                raise AssertionError('同一响应里已经停止，不应再请求模型')
            if len(calls) == 1:
                return {'role': 'assistant', 'tool_calls': [self.tool('read', 'read_work', {'slug': self.slug})]}
            return {'role': 'assistant', 'tool_calls': [
                self.tool('bad-1', 'edit_work', bad),
                self.tool('bad-2', 'edit_work', bad),
                self.tool('legal', 'edit_work', legal)]}

        self.agent.client = client
        job = self.wait_job(self.agent.start({'message': message, 'request_id': 'd' * 32}))
        self.assertEqual(len(calls), 2, job.get('answer'))
        self.assertIn('连续被拒', job['answer'])
        self.assertIn('没有保存新版本', job['answer'])
        self.assertNotIn('模型没有返回回复正文', job['answer'])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        edits = [action for action in job['actions'] if action['name'] == 'edit_work']
        self.assertEqual(len(edits), 2)
        self.assertTrue(all(action['status'] == 'error' for action in edits))
        self.assertNotIn('今天发布甲方案。', [block['text'] for block in self.store.detail(self.slug)['blocks']])

    def test_stop_after_a_real_save_says_the_version_was_kept(self):
        message = '把下面贴文贴上\n今天发布甲方案。'
        good = {'slug': self.slug, 'base_version': 'v0002', 'summary': '贴上', 'operations': [
            {'op': 'insert_text', 'text_source': 'paste', 'html': '<p>今天发布甲方案。</p>'}]}
        stale = {'slug': self.slug, 'base_version': 'v0002', 'summary': '再抄', 'operations': [
            {'op': 'insert_text', 'text_source': 'paste', 'html': '<p>贴文</p>'}]}
        late = {'slug': self.slug, 'base_version': 'v0002', 'summary': '迟到', 'operations': [
            {'op': 'insert_text', 'text_source': 'paste_original'}]}
        calls = []

        def client(messages, **kwargs):
            calls.append(1)
            if len(calls) > 2:
                raise AssertionError('停止后不应再请求模型')
            if len(calls) == 1:
                return {'role': 'assistant', 'tool_calls': [self.tool('read', 'read_work', {'slug': self.slug})]}
            return {'role': 'assistant', 'tool_calls': [
                self.tool('good', 'edit_work', good),
                self.tool('stale-1', 'edit_work', stale),
                self.tool('stale-2', 'edit_work', stale),
                self.tool('late', 'edit_work', late)]}

        self.agent.client = client
        job = self.wait_job(self.agent.start({'message': message, 'request_id': 'e' * 32}))
        self.assertEqual(len(calls), 2, job.get('answer'))
        self.assertIn('同一修改连续两次失败，已停止：', job['answer'])
        self.assertIn('本轮已保存新版本', job['answer'])
        self.assertNotIn('贴文', job['answer'])
        self.assertNotIn('paste_original', job['answer'])
        self.assertIn('停止前已有修改保存成功', job['answer'])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0003')
        edits = [action for action in job['actions'] if action['name'] == 'edit_work']
        self.assertEqual(len(edits), 3)
        self.assertEqual(edits[0]['status'], 'done')
        self.assertTrue(all(action['status'] == 'error' for action in edits[1:]))

    def test_paste_original_keeps_the_review_whitespace_probe(self):
        first = ' \t甲\u3000<&>\u3000\t '
        second = '\u3000乙\t丙\u3000 '
        probe = '把下面贴文贴上\n' + first + '\n\n' + second
        self.assertEqual(paste_originals(probe), (first + '\n\n' + second,))
        calls = []

        def client(messages, **kwargs):
            calls.append(1)
            if len(calls) > 3:
                raise AssertionError('贴文保存后不应继续请求模型')
            if len(calls) == 1:
                return {'role': 'assistant', 'tool_calls': [self.tool('read', 'read_work', {'slug': self.slug})]}
            if len(calls) == 2:
                return {'role': 'assistant', 'tool_calls': [self.tool('paste', 'edit_work', {
                    'slug': self.slug, 'base_version': 'v0002', 'summary': '贴空白',
                    'operations': [{'op': 'insert_text', 'text_source': 'paste_original'}]})]}
            return {'role': 'assistant', 'content': '已按原文插入。'}

        self.agent.client = client
        job = self.wait_job(self.agent.start({'message': probe, 'request_id': 'f' * 32}))
        self.assertEqual(job['prompt'], probe, job.get('answer'))
        self.assertEqual(job['status'], 'done', job.get('answer'))
        blocks = self.store.detail(self.slug)['blocks']
        self.assertEqual([block['text'] for block in blocks], ['原来的文字', first, second])
        self.assertIn('&lt;&amp;&gt;', blocks[1]['html'])
        self.assertNotIn('<&>', blocks[1]['html'])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0003')

    def test_repeated_out_of_range_delete_does_not_use_the_paste_stop(self):
        from assistant_policy import policy_for
        message = '删除第二个文字块'
        self.assertIn('delete_block', policy_for(message)['edit_grants'])
        bad = {'slug': self.slug, 'base_version': 'v0002', 'summary': '删除',
               'operations': [{'op': 'delete_block', 'index': 99}]}
        calls = []

        def client(messages, **kwargs):
            calls.append(1)
            if len(calls) > 2:
                raise AssertionError('同一越界错误已停止，不应再请求模型')
            if len(calls) == 1:
                return {'role': 'assistant', 'tool_calls': [self.tool('read', 'read_work', {'slug': self.slug})]}
            return {'role': 'assistant', 'tool_calls': [
                self.tool('del-1', 'edit_work', bad),
                self.tool('del-2', 'edit_work', bad)]}

        self.agent.client = client
        job = self.wait_job(self.agent.start({'message': message, 'request_id': '1' * 32}))
        self.assertEqual(len(calls), 2, job.get('answer'))
        self.assertIn('同一修改连续两次失败，已停止：', job['answer'])
        self.assertIn('index 需在 0\u20130', job['answer'])
        self.assertIn('停止前没有保存新版本', job['answer'])
        self.assertNotIn('贴文', job['answer'])
        self.assertNotIn('paste_original', job['answer'])
        self.assertNotIn('换行写清完整原文', job['answer'])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        edits = [action for action in job['actions'] if action['name'] == 'edit_work']
        self.assertEqual(len(edits), 2)
        self.assertTrue(all(action['status'] == 'error' for action in edits))

    def test_unchanged_update_text_then_two_edit_errors_did_not_save(self):
        message = '修改第一段文字，把下面贴文贴上\n甲'
        noop = {'slug': self.slug, 'base_version': 'v0002', 'summary': '不改内容',
                'edits': [{'index': 0, 'html': '<p>原来的文字</p>'}]}
        bad = {'slug': self.slug, 'base_version': 'v0002', 'summary': '探针', 'operations': [
            {'op': 'insert_text', 'text_source': 'paste', 'html': '<p>错</p>'}]}
        calls = []

        def client(messages, **kwargs):
            calls.append(1)
            if len(calls) > 2:
                raise AssertionError('同一响应已停止，不应再请求模型')
            if len(calls) == 1:
                return {'role': 'assistant', 'tool_calls': [self.tool('read', 'read_work', {'slug': self.slug})]}
            return {'role': 'assistant', 'tool_calls': [
                self.tool('noop', 'update_text', noop),
                self.tool('bad-1', 'edit_work', bad),
                self.tool('bad-2', 'edit_work', bad)]}

        self.agent.client = client
        job = self.wait_job(self.agent.start({'message': message, 'request_id': 'b' * 32}))
        self.assertEqual(len(calls), 2, job.get('answer'))
        self.assertIn('停止前没有保存新版本', job['answer'])
        self.assertNotIn('停止前已有修改保存成功', job['answer'])
        self.assertNotIn('本轮已保存新版本', job['answer'])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        update = next(action for action in job['actions'] if action['name'] == 'update_text')
        self.assertEqual(update['status'], 'done')
        self.assertIn('内容未改变', update['result'])
        edits = [action for action in job['actions'] if action['name'] == 'edit_work']
        self.assertEqual(len(edits), 2)
        self.assertTrue(all(action['status'] == 'error' for action in edits))
        self.assertTrue(all('本轮已保存新版本' not in action['result'] for action in edits))

    def test_cross_work_save_with_equal_version_numbers_is_reported(self):
        other = self.store.create({'title': '另一测试作品'})['slug']
        calls = []
        def client(messages, **kwargs):
            calls.append(1)
            if len(calls) > 2:
                raise AssertionError('连续失败停止后不应再请求模型')
            if len(calls) == 1:
                return {'role': 'assistant', 'tool_calls': [
                    self.tool('read-a', 'read_work', {'slug': self.slug}),
                    self.tool('read-b', 'read_work', {'slug': other})]}
            update = {'slug': other, 'base_version': 'v0001', 'summary': '修改B',
                      'edits': [], 'append_text': ['<p>B的新正文</p>']}
            bad = {'slug': other, 'base_version': 'v0002', 'summary': '重复修改',
                   'operations': [{'op': 'insert_text', 'text_source': 'paste', 'html': '<p>错</p>'}]}
            return {'role': 'assistant', 'tool_calls': [
                self.tool('save-b', 'update_text', update),
                self.tool('bad-1', 'edit_work', bad), self.tool('bad-2', 'edit_work', bad)]}
        self.agent.client = client
        job = self.wait_job(self.agent.start({
            'message': '修改第一段文字，把下面贴文贴上\n甲', 'request_id': '6' * 32}))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        self.assertEqual(self.store.meta(other)['head'], 'v0002')
        self.assertEqual(job['changed'], [other])
        self.assertIn('停止前已有修改保存成功', job['answer'])
        self.assertNotIn('停止前没有保存新版本', job['answer'])

    def test_actual_prompt_length_including_trailing_spaces_is_limited(self):
        with self.assertRaisesRegex(ValueError, '最多 24000 字'):
            self.agent.start({'message': '把下面贴文贴上\n甲' + ' ' * 30000})
        self.assertIsNone(self.agent.active)
        with self.assertRaisesRegex(ValueError, '请输入消息'):
            self.agent.start({'message': ' \t\u3000 '})

    def test_whitespace_only_original_line_separates_without_a_block(self):
        from assistant_policy import policy_for
        self.job['prompt'] = '把下面贴文贴上\n甲\n\u3000\n乙'
        self.job['policy'] = policy_for(self.job['prompt'])
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('edit_work', {
            'slug': self.slug, 'base_version': 'v0002', 'summary': '贴原文',
            'operations': [{'op': 'insert_text', 'text_source': 'paste_original'}]}, self.job)
        self.assertEqual(result['version'], 'v0003')
        self.assertEqual([b['text'] for b in self.store.detail(self.slug)['blocks']],
                         ['原来的文字', '甲', '乙'])
        self.assertEqual(len(result['placements']), 2)



class PasteWhitespaceCopyTests(unittest.TestCase):
    def test_tool_copy_names_kept_whitespace_and_empty_lines(self):
        from assistant_agent import SYSTEM, TOOLS, _original_lines
        description = next(item['function']['description'] for item in TOOLS
                           if item['function']['name'] == 'edit_work')
        for text in (description, SYSTEM):
            self.assertNotIn('照原样', text)
            self.assertIn('全角空格', text)
            self.assertIn('不单独成块', text)
        first = ' \t甲\u3000<&>\u3000\t '
        second = '\u3000乙\t丙\u3000 '
        self.assertEqual(_original_lines(first + '\r\n\r\n' + second + '\r'), [first, second])
        self.assertEqual(_original_lines('  \n\t\n\u3000'), [])


class UnchangedSaveTests(unittest.TestCase):
    setUp = test_assistant.AssistantTests.setUp
    tearDown = test_assistant.AssistantTests.tearDown

    def test_unchanged_update_text_is_not_a_saved_version(self):
        self.agent.execute('read_work', {'slug': self.slug}, self.job)
        result = self.agent.execute('update_text', {
            'slug': self.slug, 'base_version': 'v0002', 'summary': '不改内容',
            'edits': [{'index': 0, 'html': '<p>原来的文字</p>'}]}, self.job)
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['version'], 'v0002')
        self.assertEqual(result['summary'], '内容未改变')
        self.assertNotIn('_blocks_saved', self.job)
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        with self.assertRaises(ValueError) as caught:
            self.agent.execute('edit_work', {
                'slug': self.slug, 'base_version': 'v0002', 'summary': '探针',
                'operations': [{'op': 'insert_text', 'text_source': 'paste', 'html': '<p>错</p>'}]}, self.job)
        self.assertNotIn('本轮已保存新版本', str(caught.exception))
