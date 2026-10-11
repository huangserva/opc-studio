"""Synthetic start -> Assistant.run -> workflow.stage routing; no model transport."""
from isolation_patches import module_thread
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from assistant_agent import Assistant, LABELS
from assistant_knowledge import KnowledgeTools
from assistant_workflow import ContentWorkflow, RESULT_TOOL
import content_store
from knowledge_context import freeze_context
from knowledge_store import Knowledge
from server import Store


class InlineThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class KnowledgeStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'workspace')
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.selected_doc = self.store.knowledge.add_note({'title': '合成选定来源',
            'body': 'SYNTHETIC_UNSELECTED_PREFIX。原始会议证据，负责人尚待确认。SYNTHETIC_UNSELECTED_TAIL'})
        self.outside_doc = self.store.knowledge.add_note({'title': '清单外合成记录',
            'body': 'SYNTHETIC_OUTSIDE_SELECTED_DOCUMENT'})
        start = self.selected_doc['body'].index('原始会议')
        end = self.selected_doc['body'].index('SYNTHETIC_UNSELECTED_TAIL')
        self.dossier = self.store.knowledge.save_dossier({'title': '合成会议资料夹', 'items': [{
            'id': self.selected_doc['id'], 'hash': self.selected_doc['hash'], 'start': start, 'end': end}]})
        self.owner = Assistant.__new__(Assistant)
        self.owner.store = self.store
        self.owner.content = content_store
        self.owner.folder = self.root / 'assistant'
        self.owner.folder.mkdir()
        self.owner.lock = threading.RLock()
        self.owner.active = None
        self.owner.jobs, self.owner.turns = {}, []
        self.owner.history_path = self.owner.folder / 'history.json'
        self.owner.pending_path = self.owner.folder / 'pending.json'
        self.owner.conversation_id = 'synthetic-start-conversation'
        self.owner.direct_media = lambda payload: None
        self.owner.resolve_attachments = lambda attachments: []
        self.owner.media_preferences = lambda: {}
        self.owner.tool_labels = LABELS
        self.owner.config = lambda: {'model': 'synthetic-fixture', 'endpoint': 'https://example.test/v1/chat/completions',
            'api_key': 'synthetic-fixture-only', 'max_output_tokens': 2048, 'context_tokens': 32768, 'timeout_seconds': 10}
        self.owner.knowledge_tools = KnowledgeTools(self.store)
        self.calls = []
        self.owner.runtime = SimpleNamespace(available=lambda: True, run=self.fake_runtime)
        self.owner.workflows = ContentWorkflow(self.owner)
        self.owner.workflows.run = self.drive_workflow

    def tearDown(self):
        self.temp.cleanup()

    def start(self, **fields):
        payload = {'message': '基于合成会议资料调研写稿', 'workflow': 'research-write', **fields}
        with module_thread('assistant_agent', InlineThread):
            result = self.owner.start(payload)
        return result, self.owner.jobs[result['id']]

    def drive_workflow(self, parent, system, tools):
        inputs = {}
        for stage in ('research', 'draft', 'review'):
            result = self.owner.workflows.stage({'stage': stage, 'prompt': parent['prompt'], 'inputs': inputs}, parent, system, tools)
            if not result['ok']:
                raise ValueError(result['error'])
            inputs = result['structured']
        parent['workflow']['status'] = 'done'
        parent['workflow']['result'] = parent['workflow']['stages'][-1]['result']
        self.owner.workflows.persist(parent)
        return {'status': 'done', 'answer': '合成工作流完成'}

    def fake_runtime(self, child, system, tools):
        names = {tool['function']['name'] for tool in tools}
        self.calls.append({'stage': child['_workflow_stage'], 'names': names,
            'allowed': set(child['policy']['allowed_tools']), 'context': copy.deepcopy(child['context'])})
        self.assertIn('get_workspace_context', names)
        context = self.owner.execute('get_workspace_context', {}, child)['context']
        self.assertNotIn('excerpt', json.dumps(context))
        self.assertNotIn('SYNTHETIC_UNSELECTED', json.dumps(context))
        frozen = child.get('_knowledge_context')
        if frozen:
            self.assertIn('read_knowledge', names, 'real start lost fixed-reading tool registration')
            self.assertNotIn('search_knowledge', names)
            item = frozen['items'][0]
            with self.assertRaises(ValueError):
                self.owner.execute('read_knowledge', {'id': self.outside_doc['id']}, child)
            with self.assertRaises(ValueError):
                self.owner.execute('read_knowledge', {'id': item['id'], 'hash': item['hash'], 'start': item['start'] - 1, 'max_chars': 1}, child)
            read = self.owner.execute('read_knowledge', {'id': item['id'], 'hash': item['hash'],
                'start': item['start'], 'max_chars': item['end'] - item['start']}, child)
            self.assertEqual(read['text'], self.dossier['items'][0]['excerpt'])
            url = read['url']
        else:
            self.assertNotIn('read_knowledge', names, 'unselected workflow exposes live source body')
            self.assertNotIn('read_knowledge', child['policy']['allowed_tools'])
            with self.assertRaisesRegex(ValueError, '未授权'):
                self.owner.execute('read_knowledge', {'id': self.outside_doc['id']}, child)
            url = 'https://example.com/synthetic-fetched-source'
            child['_workflow_sources'].append({'url': url, 'title': '合成公开原文'})
        if child['_workflow_stage'] == 'research':
            args = {'summary': '已读取限定合成原文', 'evidence': [{'claim': '合成会议资料中负责人待确认', 'url': url}]}
        else:
            args = {'title': '合成会议写作', 'markdown': '合成会议负责人尚待确认。[合成来源](' + url + ')'}
            if child['_workflow_stage'] == 'review':
                args.update(verdict='passed', notes=['保留合成来源'], unresolved=[])
        self.owner.execute(RESULT_TOOL, args, child)
        return {'status': 'done', 'answer': '合成阶段完成'}

    def make_draft(self, items=None, **extra):
        draft = self.store.create({'title': '合成知识底稿'})
        path = self.store.article(draft['slug']) / 'knowledge-sources.json'
        path.write_text(json.dumps({'version': draft['head'], 'items': items or self.dossier['items'], **extra}), encoding='utf-8')
        return draft, path

    def test_explicit_dossier_start_registers_real_stage_tools_and_never_injects_text(self):
        result, job = self.start(knowledge_context={'kind': 'dossier', 'id': self.dossier['id']})
        self.assertEqual(result['status'], 'done', self.calls)
        self.assertEqual([call['stage'] for call in self.calls], ['research', 'draft', 'review'])
        self.assertEqual(job['_knowledge_context']['items'][0]['excerpt'], self.dossier['items'][0]['excerpt'])
        self.assertEqual(job['context']['knowledge']['kind'], 'dossier')
        self.assertNotIn('_knowledge_context', result)
        self.assertNotIn('原始会议证据', self.owner.history_path.read_text())
        self.assertIsNone(self.owner.active)

    def test_current_draft_manifest_is_automatically_frozen_with_actual_version(self):
        draft, _ = self.make_draft()
        result, job = self.start(context={'slug': draft['slug'], 'version': draft['head']})
        self.assertEqual(result['status'], 'done', self.calls)
        self.assertEqual(job['context']['knowledge']['version'], draft['head'])
        self.assertEqual(job['context']['knowledge']['slug'], draft['slug'])
        self.assertFalse(job['context']['knowledge']['legacy_binding_unverified'])

    def test_unselected_workflow_never_gets_live_read_knowledge(self):
        result, job = self.start()
        self.assertEqual(result['status'], 'done', self.calls)
        self.assertNotIn('_knowledge_context', job)
        self.assertNotIn('knowledge', job['context'])

    def test_auto_mismatched_manifest_degrades_without_granting_live_read(self):
        draft, path = self.make_draft()
        later = self.store.save(draft['slug'], {'base_version': draft['head'], 'title': '合成后续稿件'})
        result, job = self.start(context={'slug': draft['slug'], 'version': later['version']})
        self.assertEqual(result['status'], 'done', self.calls)
        self.assertNotIn('_knowledge_context', job)
        self.assertEqual(json.loads(path.read_text())['version'], draft['head'])

    def test_explicit_missing_or_mismatched_selection_fails_before_runtime_or_job_creation(self):
        draft, _ = self.make_draft()
        later = self.store.save(draft['slug'], {'base_version': draft['head'], 'title': '合成后续稿件'})
        requests = [{'kind': 'dossier', 'id': 'f' * 32},
            {'kind': 'draft', 'slug': draft['slug'], 'version': later['version']}]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.start(knowledge_context=request)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.owner.jobs, {})
        self.assertIsNone(self.owner.active)

    def test_read_work_keeps_explicit_binding_when_other_draft_has_different_sources(self):
        other_dossier = self.store.knowledge.save_dossier({'title': '合成另一资料夹', 'items': [{
            'id': self.outside_doc['id'], 'hash': self.outside_doc['hash'], 'start': 0, 'end': len(self.outside_doc['body'])}]})
        draft, _ = self.make_draft(items=other_dossier['items'])
        bound = freeze_context(self.store, {'kind': 'dossier', 'id': self.dossier['id']})
        job = {'cancel': False, 'actions': [], 'changed': [], 'read_versions': {}, 'read_blocks': {},
            'policy': {'allowed_tools': ['read_work', 'read_knowledge']},
            '_knowledge_context': copy.deepcopy(bound), 'context': {'knowledge': copy.deepcopy(bound['descriptor'])}}
        result = self.owner.execute('read_work', {'slug': draft['slug']}, job)
        self.assertEqual(result['knowledge'], bound['descriptor'])
        self.assertTrue(result.get('knowledge_warning'))
        self.assertEqual(job['_knowledge_context'], bound)
        with self.assertRaises(ValueError):
            self.owner.execute('read_knowledge', {'id': self.outside_doc['id']}, job)


if __name__ == '__main__':
    unittest.main()
