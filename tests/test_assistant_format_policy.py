import unittest
from assistant_policy import policy_for, READ_TOOLS

class FormatPolicyTests(unittest.TestCase):
    def test_explicit_format_commands_grant_only_text(self):
        for text in ('请按照图片给当前作品文字加粗','给Rea项目加粗','我保存了，给Rea项目加粗','为当前作品标题加粗',
                     '把这篇文章文字加粗','将正文取消加粗','取消当前作品文字加粗',
                     '请加粗当前作品文字','bold current work text'):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertIn('update_text',p['allowed_tools'])
                self.assertEqual(p['edit_grants'],['set_text'])
                self.assertFalse(p['allow_insert'])
                self.assertNotIn('generate_media',p['allowed_tools'])
    def test_non_commands_and_unknown_objects_do_not_grant(self):
        for text in ('这个要不要加粗？','上次说的加粗','不要给当前作品文字加粗',
                     '建议给当前作品文字加粗','“给当前作品文字加粗”','```给当前作品文字加粗```',
                     '给这个加粗','给项目加粗','给文字加粗','给某个项目加粗','给一个项目加粗','加粗','bold it','请修改当前作品文字加粗吗？',
                     '如果给当前作品文字加粗','之前让你给当前作品文字加粗'):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertNotIn('update_text',p['allowed_tools'])
                self.assertEqual(p['edit_grants'],[])

    # 24d M1：格式意图不能吞掉同一子句里的其他意图。期望值取自 24c 之前的口径，
    # 再并上格式意图本身只多 update_text。
    def test_compound_clause_keeps_the_other_intent(self):
        for text,tools,grants in (
            ('把当前作品加粗并插入一段文字',{'update_text','edit_work'},{'set_text','insert_text'}),
            ('把当前作品加粗并插入一张图片',{'update_text','edit_work'},{'set_text','insert_image'}),
            ('把当前作品加粗并删除第一段',{'update_text','edit_work'},{'set_text','delete_block'}),
            ('把当前作品加粗并新建一个合集',{'update_text','organize_collection'},{'set_text'}),
            ('把当前作品加粗并移到第二段之后',{'update_text','edit_work'},{'set_text','move_block'}),
            ('把当前作品加粗并贴一段原文',{'update_text','edit_work'},{'set_text','insert_text'})):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertEqual(tools,set(p['allowed_tools'])&{'update_text','edit_work','organize_collection',
                                                                'create_card','generate_media'})
                self.assertTrue(grants<=set(p['edit_grants']),p['edit_grants'])
        p=policy_for('把当前作品加粗并插入一段文字')
        self.assertTrue(p.get('composed_insert'))
        p=policy_for('把当前作品加粗并删除第一段')
        self.assertEqual(p.get('edit_targets'),{'delete_block':['text']})

    def test_compound_clause_memory_intent_survives(self):
        for text in ('记住以后都给当前作品加粗','记住当前作品加粗这个偏好'):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertIn('remio_create_note',p['allowed_tools'])
                self.assertNotIn('update_text',p['allowed_tools'])   # 该子句没有祈使格式命令，与 24c 之前一致

    def test_compound_clause_does_not_widen_unrelated_tools(self):
        for text,denied in (('把当前作品加粗并生成一张图',{'generate_media','create_card'}),
                            ('给当前作品加粗并创建文字卡片',{'create_card','generate_media'}),
                            ('把当前作品加粗并记住这个偏好',{'remio_create_note','remio_update_note','memos_write'}),
                            ('把当前作品加粗并插入一段文字',{'create_card','generate_media','organize_collection'})):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertFalse(set(p['allowed_tools'])&denied)
                self.assertFalse(p['allow_insert'])

    # 24d L1：疑问/建议/历史只按当前子句判定，不牵连同一句里的明确命令。
    def test_question_scope_is_per_clause(self):
        for text in ('这个要不要加粗？另外把当前作品正文加粗','今天天气怎么样？把当前作品正文加粗',
                     '上次说的加粗，另外把当前作品正文加粗','「给当前作品加粗」，另外把当前作品正文加粗',
                     '能不能加粗标题？把当前作品正文加粗'):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertIn('update_text',p['allowed_tools'])
                self.assertEqual(set(p['allowed_tools'])&{'edit_work','create_card','generate_media',
                                                          'organize_collection','remio_create_note'},set())
    def test_question_clause_itself_still_denied(self):
        for text in ('把当前作品正文加粗吗','这个要不要加粗','当前作品加粗了吗','怎么给当前作品正文加粗',
                     '把当前作品正文加粗吗？另外上次说的加粗'):
            with self.subTest(text=text):
                self.assertNotIn('update_text',policy_for(text)['allowed_tools'])

    def test_question_terminators_do_not_add_format_grants(self):
        for text,tools,grants in (
            ('把当前作品正文加粗？',set(),set()),
            ('bold current work text?',set(),set()),
            ('给当前作品加粗?',set(),set()),
            ('把当前作品加粗并插入一段文字？',set(),set()),
            ('把当前作品加粗并新建一个合集？',set(),set()),
            ('bold current text? generate image',{'generate_media'},set())):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertEqual(set(p['allowed_tools'])-READ_TOOLS,tools)
                self.assertEqual(set(p['edit_grants']),grants)
        for text in ('这个要不要加粗？另外把当前作品正文加粗',
                     'bold current text? 把当前作品正文加粗'):
            with self.subTest(text=text):
                self.assertIn('update_text',policy_for(text)['allowed_tools'])

    def test_sentence_period_preserves_dot_tokens(self):
        from assistant_policy import _command_clauses
        self.assertIn('update_text',policy_for('这个要不要加粗.把当前作品正文加粗')['allowed_tools'])
        for token in ('v2.0','a.md','x.com','2.5','https://x.com/a.md?v=2.0'):
            with self.subTest(token=token):
                text='查询 '+token+' 的内容'
                self.assertEqual(list(_command_clauses(text)),[(text,False)])
        self.assertEqual(list(_command_clauses('查询 https://x.com/path. 然后把正文加粗')),
                         [('查询 https://x.com/path. ',False),('把正文加粗',False)])

        self.assertIn('update_text',policy_for('查询 https://x.com/a.md，把当前作品正文加粗')['allowed_tools'])
        self.assertIn('update_text',policy_for('这个要不要加粗. bold current text')['allowed_tools'])


class QuestionStructureTests(unittest.TestCase):
    def test_question_terminator_never_grants_writes(self):
        for text in ('删除第二段?', '把当前作品加粗并删除第一段?',
                     '把第二段移到第一段之后?', '把当前作品的截图插到第一段之后?',
                     '把当前作品加粗并插入一段文字？', '生成一张封面?',
                     '记住我喜欢短段落?', '新建一个合集叫测试?',
                     '生成文字卡片?', '保存到memos我的偏好?', '更新remio笔记?',
                     'rewrite current text?', 'generate image?', 'remember short paragraphs?'):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(set(p['allowed_tools']) - READ_TOOLS, set())
                self.assertEqual(p['edit_grants'], [])
                self.assertFalse(p['allow_insert'])

    def test_other_command_clause_still_grants(self):
        p = policy_for('删除第二段? 另外把正文加粗')
        self.assertEqual(set(p['allowed_tools']) - READ_TOOLS, {'update_text'})
        self.assertEqual(p['edit_grants'], ['set_text'])
        self.assertFalse(p['allow_insert'])
        p = policy_for('这个要不要删？删除第二段')
        self.assertIn('delete_block', p['edit_grants'])
        self.assertIn('edit_work', p['allowed_tools'])

    def test_sentence_dot_preserves_downstream_writes(self):
        for text, tool in (('把当前作品加粗.生成一张封面', 'generate_media'),
                           ('把当前作品加粗.记住我喜欢短段落', 'remio_create_note'),
                           ('把当前作品加粗.新建一个合集叫测试', 'organize_collection')):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertIn(tool, p['allowed_tools'])
                self.assertIn('set_text', p['edit_grants'])


class QuestionRunTests(unittest.TestCase):
    def test_question_in_terminator_run_denies_writes(self):
        for text in ('删除第二段！？', '删除第二段!?', '生成一张封面！？',
                     '新建一个合集叫测试！？', '删除第二段？！', '删除第二段？？',
                     '删除第二段！ ？', '删除第二段。 ？', '删除第二段; ?', '删除第二段！\n？'):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(set(p['allowed_tools']) - READ_TOOLS, set())
                self.assertEqual(p['edit_grants'], [])
                self.assertFalse(p['allow_insert'])

    def test_adjacent_command_survives_question_run(self):
        p = policy_for('删除第二段！？ 另外把正文加粗')
        self.assertEqual(set(p['allowed_tools']) - READ_TOOLS, {'update_text'})
        self.assertEqual(p['edit_grants'], ['set_text'])
        self.assertFalse(p['allow_insert'])

    def test_exclamations_are_still_commands(self):
        for text in ('删除第二段！', '删除第二段！！'):
            with self.subTest(text=text):
                self.assertIn('delete_block', policy_for(text)['edit_grants'])


class QuestionNewlineTests(unittest.TestCase):
    def test_question_after_empty_newline_or_colon_denies_writes(self):
        for text in ('删除第二段\n？', '删除第二段\n！？', '删除第二段\n\n？',
                     '删除第二段:？', '删除第二段：？', '删除第二段： \n ! ?',
                     '生成一张封面\n?', '记住我喜欢短段落：？'):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(set(p['allowed_tools']) - READ_TOOLS, set())
                self.assertEqual(p['edit_grants'], [])
                self.assertFalse(p['allow_insert'])

    def test_text_after_boundary_and_adjacent_commands_survive(self):
        for text in ('要求：删除第二段', '删除第二段\n把正文加粗',
                     '删除第二段：把正文加粗', '删除第二段！'):
            with self.subTest(text=text):
                self.assertIn('delete_block', policy_for(text)['edit_grants'])
        p = policy_for('删除第二段\n？另外把正文加粗')
        self.assertEqual(set(p['allowed_tools']) - READ_TOOLS, {'update_text'})
        self.assertEqual(p['edit_grants'], ['set_text'])

    # 24h-T（评审 M-1）：换行边界后有实际文字，问号就不挂接到前一条命令，
    # 前一条命令的授权必须保留。锁 assistant_policy.py 的间隔文字检查
    # （text[delimiters[last].end():following.start()].strip()）。
    # 只断言「保留授权」，不断言精确集合：越权残留以后可能改成撤回（评审 L-1）。
    def test_gap_text_before_question_run_keeps_earlier_grant(self):
        for text,grant in (('删除第二段\n把正文加粗？','delete_block'),
                           ('删除第二段\n把正文加粗\n？','delete_block'),
                           ('删除第二段\n\n把正文加粗?','delete_block'),
                           ('把正文加粗\n删除第二段？','set_text')):
            with self.subTest(text=text):
                self.assertIn(grant, policy_for(text)['edit_grants'])
