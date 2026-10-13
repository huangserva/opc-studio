import unittest
from assistant_policy import policy_for, READ_TOOLS

class FormatPolicyTests(unittest.TestCase):
    def test_explicit_format_commands_grant_only_text(self):
        for text in ('请按照图片给Rea项目文字加粗','给Rea项目加粗','我保存了，给Rea项目加粗','为Rea项目加粗',
                     '把Rea项目文字加粗','将Rea项目取消加粗','请把Rea项目的加粗取消',
                     '请把Rea项目文字加粗','bold current work text'):
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
            ('把Rea项目加粗并插入一段文字',{'update_text','edit_work'},{'set_text','insert_text'}),
            ('把Rea项目加粗并插入一张图片',{'update_text','edit_work'},{'set_text','insert_image'}),
            ('把Rea项目加粗并删除第一段',{'update_text','edit_work'},{'set_text','delete_block'}),
            ('把Rea项目加粗并新建一个合集',{'update_text','organize_collection'},{'set_text'}),
            ('把Rea项目加粗并移到第二段之后',{'update_text','edit_work'},{'set_text','move_block'}),
            ('把Rea项目加粗并贴一段原文',{'update_text','edit_work'},{'set_text','insert_text'})):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertEqual(tools,set(p['allowed_tools'])&{'update_text','edit_work','organize_collection',
                                                                'create_card','generate_media'})
                self.assertTrue(grants<=set(p['edit_grants']),p['edit_grants'])
        p=policy_for('把Rea项目加粗并插入一段文字')
        self.assertTrue(p.get('composed_insert'))
        p=policy_for('把Rea项目加粗并删除第一段')
        self.assertEqual(p.get('edit_targets'),{'delete_block':['text']})

    def test_compound_clause_memory_intent_survives(self):
        for text in ('记住以后都给当前作品加粗','记住当前作品加粗这个偏好'):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertIn('remio_create_note',p['allowed_tools'])
                self.assertNotIn('update_text',p['allowed_tools'])   # 该子句没有祈使格式命令，与 24c 之前一致

    def test_compound_clause_does_not_widen_unrelated_tools(self):
        for text,denied in (('把Rea项目加粗并生成一张图',{'generate_media','create_card'}),
                            ('给当前作品加粗并创建文字卡片',{'create_card','generate_media'}),
                            ('把Rea项目加粗并记住这个偏好',{'remio_create_note','remio_update_note','memos_write'}),
                            ('把Rea项目加粗并插入一段文字',{'create_card','generate_media','organize_collection'})):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertFalse(set(p['allowed_tools'])&denied)
                self.assertFalse(p['allow_insert'])

    # 24d L1：疑问/建议/历史只按当前子句判定，不牵连同一句里的明确命令。
    def test_question_scope_is_per_clause(self):
        for text in ('这个要不要加粗？另外把Rea项目加粗','今天天气怎么样？把Rea项目加粗',
                     '上次说的加粗，另外把Rea项目加粗','「给Rea项目加粗」，另外把Rea项目加粗',
                     ):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertIn('update_text',p['allowed_tools'])
                self.assertEqual(set(p['allowed_tools'])&{'edit_work','create_card','generate_media',
                                                          'organize_collection','remio_create_note'},set())
    def test_question_clause_itself_still_denied(self):
        for text in ('把Rea项目加粗吗','这个要不要加粗','当前作品加粗了吗','怎么给当前作品正文加粗',
                     '把Rea项目加粗吗？另外上次说的加粗'):
            with self.subTest(text=text):
                self.assertNotIn('update_text',policy_for(text)['allowed_tools'])

    def test_question_terminators_do_not_add_format_grants(self):
        for text,tools,grants in (
            ('把Rea项目加粗？',set(),set()),
            ('bold current work text?',set(),set()),
            ('给当前作品加粗?',set(),set()),
            ('把Rea项目加粗并插入一段文字？',set(),set()),
            ('把Rea项目加粗并新建一个合集？',set(),set()),
            ('bold current text? generate image',{'generate_media'},set())):
            with self.subTest(text=text):
                p=policy_for(text)
                self.assertEqual(set(p['allowed_tools'])-READ_TOOLS,tools)
                self.assertEqual(set(p['edit_grants']),grants)
        for text in ('这个要不要加粗？另外把Rea项目加粗',
                     'bold current text? 给Rea项目加粗'):
            with self.subTest(text=text):
                self.assertIn('update_text',policy_for(text)['allowed_tools'])

    def test_sentence_period_preserves_dot_tokens(self):
        from assistant_policy import _command_clauses
        self.assertIn('update_text',policy_for('这个要不要加粗.把Rea项目加粗')['allowed_tools'])
        for token in ('v2.0','a.md','x.com','2.5','https://x.com/a.md?v=2.0'):
            with self.subTest(token=token):
                text='查询 '+token+' 的内容'
                self.assertEqual(list(_command_clauses(text)),[(text,False)])
        self.assertEqual(list(_command_clauses('查询 https://x.com/path. 然后把Rea项目加粗')),
                         [('查询 https://x.com/path. ',False),('把Rea项目加粗',False)])

        self.assertIn('update_text',policy_for('查询 https://x.com/a.md，把Rea项目加粗')['allowed_tools'])
        self.assertIn('update_text',policy_for('这个要不要加粗. bold current text')['allowed_tools'])


class QuestionStructureTests(unittest.TestCase):
    def test_question_terminator_never_grants_writes(self):
        for text in ('删除第二段?', '把Rea项目加粗并删除第一段?',
                     '把第二段移到第一段之后?', '把当前作品的截图插到第一段之后?',
                     '把Rea项目加粗并插入一段文字？', '生成一张封面?',
                     '记住我喜欢短段落?', '新建一个合集叫测试?',
                     '生成文字卡片?', '保存到memos我的偏好?', '更新remio笔记?',
                     'rewrite current text?', 'generate image?', 'remember short paragraphs?'):
            with self.subTest(text=text):
                p = policy_for(text)
                self.assertEqual(set(p['allowed_tools']) - READ_TOOLS, set())
                self.assertEqual(p['edit_grants'], [])
                self.assertFalse(p['allow_insert'])

    def test_other_command_clause_still_grants(self):
        p = policy_for('删除第二段? 另外把Rea项目加粗')
        self.assertEqual(set(p['allowed_tools']) - READ_TOOLS, {'update_text'})
        self.assertEqual(p['edit_grants'], ['set_text'])
        self.assertFalse(p['allow_insert'])
        p = policy_for('这个要不要删？删除第二段')
        self.assertIn('delete_block', p['edit_grants'])
        self.assertIn('edit_work', p['allowed_tools'])

    def test_sentence_dot_preserves_downstream_writes(self):
        for text, tool in (('把Rea项目加粗.生成一张封面', 'generate_media'),
                           ('把Rea项目加粗.记住我喜欢短段落', 'remio_create_note'),
                           ('把Rea项目加粗.新建一个合集叫测试', 'organize_collection')):
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
        p = policy_for('删除第二段！？ 另外把Rea项目加粗')
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
        for text in ('要求：删除第二段', '删除第二段\n把Rea项目加粗',
                     '删除第二段：把Rea项目加粗', '删除第二段！'):
            with self.subTest(text=text):
                self.assertIn('delete_block', policy_for(text)['edit_grants'])
        p = policy_for('删除第二段\n？另外把Rea项目加粗')
        self.assertEqual(set(p['allowed_tools']) - READ_TOOLS, {'update_text'})
        self.assertEqual(p['edit_grants'], ['set_text'])

    # 24h-T（评审 M-1）：换行边界后有实际文字，问号就不挂接到前一条命令，
    # 前一条命令的授权必须保留。锁 assistant_policy.py 的间隔文字检查
    # （text[delimiters[last].end():following.start()].strip()）。
    # 只断言「保留授权」，不断言精确集合：越权残留以后可能改成撤回（评审 L-1）。
    def test_gap_text_before_question_run_keeps_earlier_grant(self):
        for text,grant in (('删除第二段\n把Rea项目加粗？','delete_block'),
                           ('删除第二段\n把Rea项目加粗\n？','delete_block'),
                           ('删除第二段\n\n把Rea项目加粗?','delete_block'),
                           ('把Rea项目加粗\n删除第二段？','set_text')):
            with self.subTest(text=text):
                self.assertIn(grant, policy_for(text)['edit_grants'])

    # 24i-B：换行/冒号边界后有实际文字就是新的一组，礼貌确认尾部只回挂那一组，
    # 不牵连前面那条命令。
    def test_confirmation_tail_stops_at_gap_text(self):
        for text,grants in (('删除第二段\n把Rea项目加粗，可以吗？',{'delete_block','set_text'}),
                            ('删除第二段\n\n把Rea项目加粗，可以吗？',{'delete_block','set_text'}),
                            ('删除第二段：把Rea项目加粗，好吗？',{'delete_block','set_text'})):
            with self.subTest(text=text):
                self.assertEqual(set(policy_for(text)['edit_grants']), grants)


class OpenWorkGrantTests(unittest.TestCase):
    """打开着作品就全量授权：不再用关键词闸门判断是不是命令/是不是提问（黄总 17:4x）。"""

    EDIT_TOOLS = ('update_text', 'edit_work', 'reflow_markdown')
    EDIT_GRANTS = {'set_text', 'insert_text', 'delete_block', 'move_block', 'insert_image', 'reflow_markdown'}

    def test_opened_work_always_grants_every_editing_tool(self):
        # 黄总两句原话 + 追加1 点名的五种说法 + 问句：一句都不能把工具收回去。
        for text in ('看一下这个文章，很多地方本来是要加粗的…是 Markdown 格式，我们这边没有做好，要改一改，标题也应该是标题的格式',
                     '你要通篇看文章，然后处理啊…是要你自己判断啊',
                     '这段太啰嗦了帮我精简',
                     '语气再口语一点',
                     '标题改得吸引人些',
                     '你自己看着改',
                     '通篇润色一遍',
                     '帮我把开头重写一下',
                     '加粗',
                     '这样写好吗？',
                     '要不要加粗？',
                     '先不要改'):
            with self.subTest(text=text):
                p = policy_for(text, work_open=True)
                for tool in self.EDIT_TOOLS:
                    self.assertIn(tool, p['allowed_tools'], '%s 不该被 %r 收回' % (tool, text))
                self.assertLessEqual(self.EDIT_GRANTS, set(p['edit_grants']), text)
                self.assertTrue(p['allow_insert'])
                self.assertEqual(sorted(p['media_kinds']), ['image', 'video'])

    def test_opened_work_also_hands_over_media_notes_and_collections(self):
        # 「不给 AI 限制，给 AI 帮助」：这些同样交给模型判断，不再按关键词设闸门。
        for text in ('随便一句话', '生成一张封面图', '把作品归档到合集', '记住黄总偏好短句'):
            with self.subTest(text=text):
                granted = set(policy_for(text, work_open=True)['allowed_tools'])
                for tool in ('generate_media', 'create_card', 'organize_collection',
                             'remio_create_note', 'memos_write', 'read_work'):
                    self.assertIn(tool, granted, '%s 不该被 %r 收回' % (tool, text))

    def test_grant_is_identical_whichever_wording_is_used(self):
        # 不再有子句闸门：打开作品时不同措辞拿到的名单必须一模一样。
        base = policy_for('x', work_open=True)['allowed_tools']
        for text in ('这段太啰嗦了帮我精简', '要不要加粗？', '通篇润色一遍', '先不要改'):
            with self.subTest(text=text):
                self.assertEqual(policy_for(text, work_open=True)['allowed_tools'], base, text)

    def test_without_an_open_work_nothing_is_writable(self):
        # 没有打开作品时不知道改哪一篇，改稿工具不授权，只读工具照旧。
        for text in ('这段太啰嗦了帮我精简', '通篇润色一遍', '你自己看着改', '要不要加粗？'):
            with self.subTest(text=text):
                p = policy_for(text, work_open=False)
                self.assertNotIn('update_text', p['allowed_tools'])
                self.assertNotIn('edit_work', p['allowed_tools'])
                self.assertNotIn('reflow_markdown', p['allowed_tools'])
                self.assertEqual(p['edit_grants'], [])
                self.assertIn('search_works', p['allowed_tools'], '要能列出最近作品让用户选')

    def test_publish_and_whole_article_delete_go_through_confirmation_not_a_gate(self):
        import assistant_agent
        names = {tool['function']['name'] for tool in assistant_agent.TOOLS}
        self.assertFalse([n for n in names if 'publish' in n or 'sync' in n],
                         '助手没有发布工具：发布只能走对话确认，工具层无从拦截')
        self.assertFalse([n for n in names if 'delete' in n],
                         '助手没有删除工具：删除整篇作品只能走对话确认')
        system = assistant_agent.SYSTEM
        for phrase in ('发布或同步到外部平台', '删除整篇作品', '确认'):
            self.assertIn(phrase, system, '确认规则必须写在系统提示里：'+phrase)

    def test_greeting_still_short_circuits(self):
        p = policy_for('你好', work_open=True)
        self.assertEqual(p['allowed_tools'], [])
        self.assertIn('reply', p)
