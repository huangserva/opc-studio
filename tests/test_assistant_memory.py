from tests.test_assistant_backend import wait_for_background
"""Memory registration, grants and receipts without modifying personal notes."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from assistant_agent import Assistant, LABELS, TOOLS
from assistant_memory import MemoryTools, MEMORY_READ_TOOLS, MEMORY_WRITE_TOOLS
from assistant_policy import policy_for
from server import Store, content


class MemoryPolicyTests(unittest.TestCase):
    def test_reads_are_registered_and_writes_are_absent_from_queries(self):
        schema = {t['function']['name']: t['function'] for t in TOOLS}
        for name in MEMORY_READ_TOOLS | MEMORY_WRITE_TOOLS:
            self.assertIn(name, LABELS)
            self.assertFalse(schema[name]['parameters']['additionalProperties'])
            self.assertNotIn('request_id', schema[name]['parameters']['properties'])
        queries = ['如何保证Pi有remio和MemOS工具？', '把 remio 接到 Pi', '把 MemOS 状态查一下',
                   '将 remio 的资料按时间排序', '把 remio 的更新记录查一下',
                   '将 MemOS 记录按时间倒序展示', '把remio笔记中的补充说明读出来',
                   '把 MemOS 更新日志列出来', '更新 remio 接入功能', '追加 remio 工具到注册表',
                   '不要保存到MemOS', '之前保存到remio了', '他说保存到MemOS',
                   '例子：“记住我的偏好”', '```保存到MemOS```', '> 记住这个偏好']
        for text in queries:
            with self.subTest(text=text):
                granted = set(policy_for(text)['allowed_tools'])
                self.assertFalse(granted & MEMORY_WRITE_TOOLS)
                self.assertTrue(MEMORY_READ_TOOLS <= granted)

    def test_explicit_memory_destination_and_default_do_not_grant_article_edits(self):
        cases = [('记住我的偏好', {'remio_create_note'}),
                 ('请保存到remio笔记：测试偏好', {'remio_create_note'}),
                 ('帮我创建一份remio笔记', {'remio_create_note'}),
                 ('把我的偏好存到MemOS', {'memos_write'}),
                 ('记住我的偏好，保存到 MemOS', {'memos_write'}),
                 ('请给我更新remio笔记', {'remio_update_note'}),
                 ('把这条偏好追加到remio笔记', {'remio_update_note'})]
        for text, expected in cases:
            with self.subTest(text=text):
                granted = set(policy_for(text)['allowed_tools'])
                self.assertEqual(granted & MEMORY_WRITE_TOOLS, expected)
                self.assertNotIn('update_text', granted)
                self.assertNotIn('generate_media', granted)


class MemoryDispatchTests(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryTools.__new__(MemoryTools)
        self.memory.remio = Mock(spec=['status', 'search', 'read', 'create', 'update'])
        self.memory.memos = Mock(spec=['status', 'search', 'write', 'sensitive_values'])
        self.memory.memos.sensitive_values = ()
        self.job = {'id': 'a' * 32, 'conversation_id': 'b' * 32,
                    'model_config': {'api_key': 'fixture-model-secret'},
                    'policy': policy_for('记住测试偏好')}

    def test_unknown_inputs_never_reach_backends(self):
        for args in (None, [], {'command': 'whoami'}, {'query': 'hello', 'endpoint': 'http://remote'},
                     {'query': 'fixture-model-secret'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.memory.execute('remio_search', args, self.job)
        self.memory.remio.search.assert_not_called()

    def test_read_must_be_delivered_before_an_append_is_authorized(self):
        self.job['policy'] = policy_for('更新remio笔记')
        args = {'note_id': 'fixture-note', 'append_text': 'fixture addition'}
        with self.assertRaisesRegex(ValueError, '必须先'):
            self.memory.execute('remio_update_note', args, self.job)
        self.memory.remio.read.return_value = {'ok': True, 'content': 'x' * 4000}
        self.job['_knowledge_output_chars'] = 13000
        with self.assertRaisesRegex(ValueError, '预算不足'):
            self.memory.execute('remio_read', {'note_id': 'fixture-note'}, self.job)
        self.assertNotIn('fixture-note', self.job.get('_remio_read_notes', set()))
        self.memory.remio.update.assert_not_called()
        self.job['_knowledge_output_chars'] = 0
        self.memory.remio.read.return_value = {'ok': True, 'content': 'fixture original'}
        self.memory.execute('remio_read', {'note_id': 'fixture-note'}, self.job)
        self.memory.remio.update.return_value = {'saved': True, 'noteId': 'fixture-note'}
        self.assertTrue(self.memory.execute('remio_update_note', args, self.job)['saved'])

    def test_write_grant_and_receipt_budget_are_checked_before_mutation(self):
        args = {'title': 'fixture', 'content': 'fixture'}
        self.job['policy'] = policy_for('查询之前的偏好')
        with self.assertRaisesRegex(ValueError, '没有授权'):
            self.memory.execute('remio_create_note', args, self.job)
        self.job['policy'] = policy_for('记住测试偏好')
        self.job['_knowledge_output_chars'] = 14000
        with self.assertRaisesRegex(ValueError, '保存凭据'):
            self.memory.execute('remio_create_note', args, self.job)
        self.memory.remio.create.assert_not_called()

    def test_backend_provides_stable_id_and_results_share_retrieval_budget(self):
        self.memory.remio.create.return_value = {'saved': True, 'noteId': 'fixture-note',
                                                 'url': 'http://127.0.0.1:17624/notes/fixture-note'}
        args = {'title': 'fixture', 'content': 'fixture'}
        for _ in range(2):
            result = self.memory.execute('remio_create_note', args, self.job)
            self.assertTrue(result['saved'])
        keys = [c.args[0]['request_id'] for c in self.memory.remio.create.call_args_list]
        self.assertEqual(keys[0], keys[1])
        self.assertGreater(result['retrieval_budget']['used_chars'], 0)
        next_job = {**self.job, 'id': 'c' * 32}
        self.assertNotEqual(keys[0], self.memory._request_id('remio_create_note', args, next_job))
        self.assertEqual(self.memory._request_id('remio_update_note', {'note_id': 'one'}, self.job),
                         self.memory._request_id('remio_update_note', {'note_id': 'two'}, self.job))

    def test_memos_receives_secret_before_passage_selection_without_changing_shared_provider(self):
        class ContextSpy:
            sensitive_values = ()
            contexts = []
            def search(self, query, limit):
                self.contexts.append(self.sensitive_values)
                return {'count': 1, 'results': [{'text': 'fixture-model-secret'}]}
        self.memory.memos = ContextSpy()
        result = self.memory.execute('memos_search', {'query': 'fixture'}, self.job)
        self.assertNotIn('fixture-model-secret', json.dumps(result))
        self.assertEqual(self.memory.memos.sensitive_values, ())
        self.assertEqual(self.memory.memos.contexts, [('fixture-model-secret',)])

    def test_service_failure_is_not_zero_results(self):
        self.memory.remio.status.return_value = {'connected': False, 'summary': 'not connected'}
        result = self.memory.execute('remio_status', {}, self.job)
        self.assertFalse(result['connected'])
        self.assertNotIn('results', result)


class MemoryPiProtocolTests(unittest.TestCase):
    def run_pi(self, folder, prompt, calls, configure):
        root = Path(folder)
        config = root / 'model.json'
        config.write_text(json.dumps({'endpoint': 'https://example.invalid/chat/completions',
                                     'model': 'mock-chat', 'api_key': 'mock-key'}))
        agent = Assistant(Store(root / 'workspace'), content, config)
        configure(agent)
        received, advertised = {}, set()
        pending = list(calls)
        def model(messages, **kwargs):
            advertised.update(kwargs.get('allowed_tools', []))
            for message in messages:
                if message.get('role') == 'tool':
                    received[message['tool_call_id']] = json.loads(message['content'])
            if pending:
                name, args = pending.pop(0)
                return {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': name, 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(args)}}]}
            return {'role': 'assistant', 'content': 'fixture verified memory result'}
        agent.client = model
        result = agent.start({'message': prompt, 'request_id': 'd' * 32,
                              'context': {'unsaved': True}})
        wait_for_background(self, agent, result['id'])
        if agent.active:
            agent.stop(result['id'])
        job = agent.public_job(result['id'])
        self.assertEqual(job['status'], 'done', job)
        return agent, job, received, advertised

    def test_native_pi_reads_results_and_does_not_receive_write_tools(self):
        def configure(agent):
            agent.memory_tools.remio.status = Mock(return_value={'connected': True, 'summary': 'fixture ready'})
            agent.memory_tools.remio.search = Mock(return_value={'ok': True, 'results': [{'noteId': 'fixture-note'}]})
            agent.memory_tools.remio.read = Mock(return_value={'ok': True, 'noteId': 'fixture-note', 'content': 'fixture fact'})
            agent.memory_tools.memos.search = Mock(return_value={'count': 1, 'results': [{'chunk_id': 'fixture-memory'}]})
        calls = [('remio_status', {}), ('memos_search', {'query': 'fixture'}),
                 ('remio_search', {'query': 'fixture'}), ('remio_read', {'note_id': 'fixture-note'})]
        with tempfile.TemporaryDirectory() as folder:
            agent, job, receipts, advertised = self.run_pi(folder, '查以前的fixture决定', calls, configure)
            self.assertTrue(MEMORY_READ_TOOLS <= advertised)
            self.assertFalse(MEMORY_WRITE_TOOLS & advertised)
            self.assertEqual(receipts['remio_read']['content'], 'fixture fact')
            self.assertEqual(receipts['memos_search']['count'], 1)
            self.assertEqual(len(job['actions']), 4)

    def test_native_pi_can_save_explicit_note_without_an_article_context(self):
        def configure(agent):
            agent.memory_tools.remio.create = Mock(return_value={'saved': True, 'noteId': 'fixture-note',
                'url': 'http://127.0.0.1:17624/notes/fixture-note', 'summary': 'fixture saved'})
        with tempfile.TemporaryDirectory() as folder:
            agent, job, receipts, advertised = self.run_pi(folder, '记住我的测试偏好',
                [('remio_create_note', {'title': 'fixture', 'content': 'fixture preference'})], configure)
            self.assertTrue(receipts['remio_create_note']['saved'])
            self.assertIn('remio_create_note', advertised)
            self.assertNotIn('memos_write', advertised)
            self.assertEqual([a['name'] for a in job['actions']], ['remio_create_note'])
            agent.memory_tools.remio.create.assert_called_once()


if __name__ == '__main__':
    unittest.main()
