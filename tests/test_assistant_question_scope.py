"""24i-B：关联疑问只撤回它所询问的那条指令组（方案 B）。

规范来源：coordination/design-24i-report.md 的 46 句决策表与
coordination/prep-24i-b-report.md §1/§2/§4。全部是纯函数断言：不调用模型、
不联网、不读写作品。每句都断言完整权限字段，防止只删 edit_work 而 update_text
等残余授权还在。
"""
import unittest

from assistant_policy import READ_TOOLS, policy_for

# (句子, 写工具, edit_grants, edit_targets, allow_insert, media_kinds,
#  requested_media_model, text_source, composed_insert)
MATRIX = [
    ('删除第二段，？', [], [], None, False, [], None, None, False),
    ('删除第二段,?', [], [], None, False, [], None, None, False),
    ('删除第二段，然后？', [], [], None, False, [], None, None, False),
    ('删除第二段然后？', [], [], None, False, [], None, None, False),
    ('删除第二段\n吗？', [], [], None, False, [], None, None, False),
    ('删除第二段\n吗', [], [], None, False, [], None, None, False),
    ('删除第二段，可以吗？', [], [], None, False, [], None, None, False),
    ('把第二段删了，可以吗？', [], [], None, False, [], None, None, False),
    ('把第二段删了，好吗？', [], [], None, False, [], None, None, False),
    ('删除第二段，行不行？', [], [], None, False, [], None, None, False),
    ('删除第二段', ['edit_work', 'update_text'], ['delete_block', 'set_text'],
     {'delete_block': ['text']}, False, [], None, None, False),
    ('请删除第二段！', ['edit_work', 'update_text'], ['delete_block', 'set_text'],
     {'delete_block': ['text']}, False, [], None, None, False),
    ('把第二段删了。', ['edit_work'], ['delete_block'], {'delete_block': ['text']},
     False, [], None, None, False),
    ('把当前作品正文加粗', [], [], None, False, [], None, None, False),
    ('bold current work text', ['update_text'], ['set_text'], None, False, [], None, None, False),
    ('rewrite current work text', ['update_text'], ['set_text'], None, False, [], None, None, False),
    ('删除第二段，再把当前作品标题加粗。', [], [], None, False, [], None, None, False),
    ('删除第二段。怎么查看版本？', ['edit_work', 'update_text'], ['delete_block', 'set_text'],
     {'delete_block': ['text']}, False, [], None, None, False),
    ('把当前作品加粗；如何下载？', [], [], None, False, [], None, None, False),
    ('删除第二段？', [], [], None, False, [], None, None, False),
    ('删除第二段！？', [], [], None, False, [], None, None, False),
    ('删除第二段\n？', [], [], None, False, [], None, None, False),
    ('删除第二段：？', [], [], None, False, [], None, None, False),
    ('可以删除第二段吗？', [], [], None, False, [], None, None, False),
    ('怎么删除第二段？', [], [], None, False, [], None, None, False),
    ('要不要把当前作品加粗？', [], [], None, False, [], None, None, False),
    ('请不要删除第二段', [], [], None, False, [], None, None, False),
    ('删除第二段 https://example.test/path?', [], [], None, False, [], None, None, False),
    ('删除第二段 https://example.test/path?x=1', ['edit_work', 'update_text'],
     ['delete_block', 'set_text'], {'delete_block': ['text']}, False, [], None, None, False),
    ('删除第二段，参考 https://example.test/path?x=1', ['edit_work', 'update_text'],
     ['delete_block', 'set_text'], {'delete_block': ['text']}, False, [], None, None, False),
    ('把当前作品正文加粗 https://example.test/path?', [], [], None, False, [], None, None, False),
    ('把当前作品正文加粗 https://example.test/path?x=1', [], [], None, False, [], None, None, False),
    ('删除第二段？然后把当前作品标题加粗。', [], [], None, False, [], None, None, False),
    ('把当前作品标题加粗，删除第二段？', [], [], None, False, [], None, None, False),
    ('请 delete 第二段？', [], [], None, False, [], None, None, False),
    ('please bold current work text?', [], [], None, False, [], None, None, False),
    ('把 current work 正文加粗，可以吗？', [], [], None, False, [], None, None, False),
    ('删除第二段，OK?', [], [], None, False, [], None, None, False),
    ('删除第二段，please confirm?', [], [], None, False, [], None, None, False),
    ('删除第二段，thanks!', ['edit_work', 'update_text'], ['delete_block', 'set_text'],
     {'delete_block': ['text']}, False, [], None, None, False),
    ('记住我的偏好，？', [], [], None, False, [], None, None, False),
    ('生成一张图片，可以吗？', [], [], None, False, [], None, None, False),
    ('新建一个合集，好吗？', [], [], None, False, [], None, None, False),
    ('删除第二段，今天天气如何？', ['edit_work', 'update_text'], ['delete_block', 'set_text'],
     {'delete_block': ['text']}, False, [], None, None, False),
    ('把当前作品加粗。删除第二段，可以吗？', [], [], None, False, [], None, None, False),
    ('把当前作品加粗并删除第二段，可以吗？', [], [], None, False, [], None, None, False),
]

MEMORY_TOOLS = {'remio_create_note', 'remio_update_note', 'memos_write'}


def writes(policy):
    return set(policy['allowed_tools']) - READ_TOOLS


class QuestionScopeMatrixTests(unittest.TestCase):
    """46 句全字段对照：B 撤回 18 条，#45 只留加粗，其余保持。"""

    def test_46_sentence_matrix(self):
        writable = 0
        for text, tools, grants, targets, insert, media, model, source, composed in MATRIX:
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(writes(p), set(tools), p['allowed_tools'])
                self.assertEqual(sorted(p['edit_grants']), grants)
                self.assertEqual(p.get('edit_targets'), targets)
                self.assertEqual(p['allow_insert'], insert)
                self.assertEqual(p.get('media_kinds', []), media)
                self.assertEqual(p.get('requested_media_model'), model)
                self.assertEqual(p.get('text_source'), source)
                self.assertEqual(p.get('composed_insert', False), composed)
                if tools:
                    writable += 1
        # 46 句里 10 句保留写权限；其余 36 句无写，其中 26 句是复核 M-4 收权新增的
        # 「当前作品/正文/标题」这类无指代指代（协调者选定的 B 口径）。
        self.assertEqual(writable, 10)

    def test_revoked_turns_keep_read_tools(self):
        for text, tools, *_ in MATRIX:
            if tools:
                continue
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertIn('read_work', p['allowed_tools'])
                self.assertIn('search_works', p['allowed_tools'])


class ConfirmationTailTests(unittest.TestCase):
    def test_confirmation_tail_revokes_only_related_command(self):
        # 尾部只回挂最近一条指令；句号前的独立命令不受牵连。
        # 「把Rea项目加粗」是指明目标的说法，M-4 的 B 口径下照旧授权，
        # 因此这一句真正验的是「只撤回被问的那条」，而不是无指代总收权。
        p = policy_for('把Rea项目加粗。删除第二段，可以吗？')
        self.assertEqual(writes(p), {'update_text'})
        self.assertEqual(p['edit_grants'], ['set_text'])
        self.assertNotIn('delete_block', p['edit_grants'])
        p = policy_for('删除第二段，可以吗？')
        self.assertEqual(writes(p), set())
        self.assertEqual(p['edit_grants'], [])
        # 「可以」出现在长句里不是尾部，不牵连任何命令。
        # 「可以把当前作品标题加粗」里的「当前作品」在没打开作品时没有指代对象，
        # 整句的改稿工具按 M-4 的 B 口径一律不授予（保守：宁可少授权，不可改错篇）。
        for text, grant in (('删除第二段，可以把当前作品标题加粗', None),
                            ('删除第二段，可以参考 https://example.test/a?x=1', 'delete_block')):
            with self.subTest(text=text):
                grants = policy_for(text)['edit_grants']
                self.assertIn(grant, grants) if grant else self.assertEqual(grants, [])

    def test_isolated_tail_after_sentence_dot_does_not_revoke(self):
        # 协调者定案：句号/分号之后孤立的「可以吗？」不跨句回挂。
        for text, grant in (('删除第二段。可以吗？', 'delete_block'),
                            ('删除第二段；可以吗？', 'delete_block'),
                            ('删除第二段.可以吗？', 'delete_block'),
                            ('把Rea项目加粗！可以吗？', 'set_text'),
                            ('把Rea项目加粗。可以吗？', 'set_text')):
            with self.subTest(text=text):
                self.assertIn(grant, policy_for(text)['edit_grants'])

    def test_confirmation_tail_revokes_all_write_tool_families(self):
        for text in ('把第二段移到第一段前，可以吗？',      # move_block
                     '插入一段文字，可以吗？',              # insert_text
                     '把截图插入第一段后，可以吗？',        # insert_image
                     '生成一段视频，好吗？',                # generate_media/video
                     '用Krea2生成一张图片，可以吗？',       # requested_media_model
                     '生成一张文字卡片，可以吗？',          # create_card
                     '新建一个合集，好吗？',                # organize_collection
                     '记住我的偏好，？',                    # remio_create_note
                     '更新到remio笔记，可以吗？',           # remio_update_note
                     '保存到MemOS，可以吗？'):              # memos_write
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(writes(p), set(), p['allowed_tools'])
                self.assertEqual(p['edit_grants'], [])

    def test_explicit_command_without_tail_keeps_grants(self):
        for text, tool in (('把第二段移到第一段前', 'edit_work'),
                           ('插入一段文字', 'edit_work'),
                           ('把截图插入第一段后', 'edit_work'),
                           ('生成一段视频', 'generate_media'),
                           ('用Krea2生成一张图片', 'generate_media'),
                           ('生成一张文字卡片', 'create_card'),
                           ('新建一个合集叫测试', 'organize_collection'),
                           ('更新到remio笔记', 'remio_update_note'),
                           ('保存到MemOS我的偏好', 'memos_write')):
            with self.subTest(text=text):
                self.assertIn(tool, policy_for(text)['allowed_tools'])

    def test_thanks_and_politeness_are_not_questions(self):
        for text in ('删除第二段，thanks!', '删除第二段，谢谢！', '删除第二段，辛苦了'):
            with self.subTest(text=text):
                self.assertIn('delete_block', policy_for(text)['edit_grants'])


class AttachmentTests(unittest.TestCase):
    def test_empty_comma_then_and_newline_question_attach(self):
        for text in ('删除第二段，？', '删除第二段,?', '删除第二段，然后？', '删除第二段然后？',
                     '删除第二段\n吗？', '删除第二段\n吗', '删除第二段，但是？',
                     '把当前作品正文加粗,?', '把当前作品正文加粗，然后？',
                     '生成一张图片，？', '新建一个合集，?', '记住我的偏好，然后？',
                     '把第二段移到第一段前，？'):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(writes(p), set(), p['allowed_tools'])
                self.assertEqual(p['edit_grants'], [])
                self.assertFalse(p['allow_insert'])

    def test_gap_text_after_boundary_is_its_own_group(self):
        # 边界后有实际文字就是新的一组，疑问只作用于这一组。
        p = policy_for('删除第二段，把Rea项目第二段改写，可以吗？')
        self.assertEqual(writes(p), {'edit_work', 'update_text'})
        self.assertEqual(sorted(p['edit_grants']), ['delete_block', 'set_text'])
        p = policy_for('删除第二段，可以吗？然后把Rea项目第二段改写。')
        self.assertEqual(writes(p), {'update_text'})
        self.assertEqual(p['edit_grants'], ['set_text'])
        # 无指代指代仍然整体收权（M-4 的 B 口径），与上面两条不冲突。
        for text in ('删除第二段，把当前作品标题加粗，可以吗？',
                     '删除第二段，可以吗？然后把当前作品标题加粗。'):
            with self.subTest(text=text):
                self.assertEqual(writes(policy_for(text)), set())
                self.assertEqual(policy_for(text)['edit_grants'], [])


class GroupScopeTests(unittest.TestCase):
    def test_compound_group_is_all_revoked(self):
        for text in ('把当前作品加粗并删除第二段，可以吗？',
                     '把当前作品加粗并插入一段文字，可以吗？',
                     '把当前作品加粗并新建一个合集，好吗？'):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(writes(p), set(), p['allowed_tools'])
                self.assertEqual(p['edit_grants'], [])
                self.assertFalse(p.get('composed_insert', False))

    def test_independent_command_survives_unrelated_question(self):
        for text, tools in (('删除第二段。怎么查看版本？', {'edit_work', 'update_text'}),
                            ('把Rea项目加粗；如何下载？', {'update_text'}),
                            ('删除第二段？然后把Rea项目第二段改写。', {'update_text'}),
                            ('把Rea项目第二段改写，删除第二段？', {'update_text'}),
                            ('删除第二段，今天天气如何？', {'edit_work', 'update_text'}),
                            ('删除第二段。今天天气如何？', {'edit_work', 'update_text'}),
                            # 换成「当前作品/正文」的无指代指代就整体收权，两条都留着。
                            ('把当前作品加粗；如何下载？', set()),
                            ('删除第二段？然后把当前作品标题加粗。', set()),
                            ('把当前作品标题加粗，删除第二段？', set())):
            with self.subTest(text=text):
                self.assertEqual(writes(policy_for(text)), tools)


class UrlTests(unittest.TestCase):
    def test_url_query_is_data_but_bare_question_is_ambiguous(self):
        p = policy_for('删除第二段 https://example.test/path?x=1')
        self.assertIn('delete_block', p['edit_grants'])
        p = policy_for('删除第二段，参考 https://example.test/path?x=1')
        self.assertIn('delete_block', p['edit_grants'])
        # 空查询符结尾的裸问号语义歧义：整组先确认，update_text 也不能残留。
        for text in ('删除第二段 https://example.test/path?', '把当前作品正文加粗 https://example.test/path?'):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(writes(p), set(), p['allowed_tools'])
                self.assertEqual(p['edit_grants'], [])
        # 已有漏授不借本批扩大。
        self.assertEqual(writes(policy_for('把当前作品正文加粗 https://example.test/path?x=1')), set())


class ResidueTests(unittest.TestCase):
    def test_no_residual_targets_media_or_paste_grants(self):
        for text in ('删除第二段，可以吗？', '把第二段删了，好吗？',
                     '把截图插入第一段后，可以吗？', '插入一段文字，可以吗？',
                     '用Krea2生成一张图片，可以吗？', '生成一段视频，好吗？',
                     '把当前作品加粗并插入一段文字，可以吗？'):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertIsNone(p.get('edit_targets'))
                self.assertEqual(p.get('media_kinds', []), [])
                self.assertIsNone(p.get('requested_media_model'))
                self.assertIsNone(p.get('text_source'))
                self.assertFalse(p.get('composed_insert', False))
                self.assertFalse(p['allow_insert'])


class MemoryDestinationTests(unittest.TestCase):
    def test_cross_clause_memory_destination_never_adds_permissions(self):
        # 被询问的 MemOS 子句撤回后，不得把默认 remio 目的地补成新的写工具。
        for text in ('记住我的偏好，保存到MemOS，可以吗？', '保存到MemOS我的偏好，可以吗？',
                     '记住我的偏好，可以吗？'):
            with self.subTest(text=text):
                self.assertFalse(writes(policy_for(text)) & MEMORY_TOOLS)
        # 没有被询问时，跨子句 MemOS 目的地覆盖保持原样。
        p = policy_for('记住我的偏好，保存到MemOS')
        self.assertEqual(writes(p) & MEMORY_TOOLS, {'memos_write'})


class FollowUpTests(unittest.TestCase):
    def test_followup_can_ok_confirm_never_resumes_history(self):
        for text in ('可以', '好的', '确认', '可以。', '收到', '行', 'OK'):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(writes(p), set(), p['allowed_tools'])
                self.assertEqual(p['edit_grants'], [])
                self.assertFalse(p['allow_insert'])


class QuoteTests(unittest.TestCase):
    def test_quotes_and_code_never_create_or_revoke_unrelated_grants(self):
        # 引号里的命令不授权；引号里的确认尾部不额外授权。
        for text in ('把Rea项目加粗，"可以吗？"', '把Rea项目加粗，「可以吗？」',
                     '删除第二段，"可以吗？"', '删除第二段，「这是什么？」'):
            with self.subTest(text=text):
                self.assertIn('set_text', policy_for(text)['edit_grants'],
                              '指明目标的说法不受引号内问句牵连')
        # 「当前作品/正文」在没打开作品时属于无指代指代，改稿工具一律不给（M-4 B 口径）。
        for text in ('把当前作品正文加粗，"可以吗？"', '把当前作品正文加粗，「可以吗？」',
                     '把正文加粗，"这是什么？"'):
            with self.subTest(text=text):
                self.assertEqual(policy_for(text)['edit_grants'], [])
        for text in ('「删除第二段」，可以吗？', '```删除第二段```，可以吗？'):
            with self.subTest(text=text):
                self.assertEqual(writes(policy_for(text)), set())


if __name__ == '__main__':
    unittest.main()
