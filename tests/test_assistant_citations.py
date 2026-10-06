"""Read provenance survives chat reload; all model responses are local fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from assistant_agent import Assistant
from knowledge_store import Knowledge
from server import Store, content


class AssistantCitationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'workspace')
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.doc = self.store.knowledge.add_note({'title':'合成来源', 'body':'😀合成原文：会议时间尚未确定，负责人也没有说明。'})
        self.config = self.root/'model.json'
        self.config.write_text(json.dumps({'endpoint':'https://example.invalid/chat/completions', 'model':'test-model', 'api_key':'synthetic-key'}))
        self.agent = Assistant(self.store, content, self.config)
        self.agent.image_skill.entry_paths = lambda: []

    def tearDown(self):
        self.temp.cleanup()

    def test_only_actual_reads_survive_restart_with_exact_old_text_and_hash(self):
        calls = []
        def model(messages, **kwargs):
            calls.append(messages)
            if len(calls)==1:
                return {'role':'assistant','tool_calls':[{'id':'search','type':'function','function':{
                    'name':'search_knowledge','arguments':json.dumps({'query':'合成原文','limit':1})}}]}
            if len(calls) in (2,3):
                return {'role':'assistant','tool_calls':[{'id':'read'+str(len(calls)),'type':'function','function':{
                    'name':'read_knowledge','arguments':json.dumps({'id':self.doc['id'],'hash':self.doc['hash'],'start':1,'max_chars':12})}}]}
            return {'role':'assistant','content':'合成界面验收：时间和负责人待确认。'}
        self.agent.client = model
        result = self.agent.start({'message':'请在知识库查询合成原文，并读取对应资料。'})
        deadline = time.monotonic()+8
        while self.agent.active and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertIsNone(self.agent.active)
        result = self.agent.public_job(result['id'])
        self.assertEqual(result['status'],'done')
        self.assertEqual(len(result['citations']),1)
        citation = result['citations'][0]
        self.assertEqual(citation['excerpt'],self.doc['body'][1:13])
        self.assertEqual(citation['hash'],self.doc['hash'])
        self.assertEqual((citation['start'],citation['end']),(1,13))
        self.assertIn('hash='+self.doc['hash'],citation['url'])
        self.assertNotIn('citations',self.agent.runtime.history()[0])
        with self.store.knowledge.db() as c:
            c.execute('UPDATE documents SET body=?,hash=? WHERE id=?',('后来原文已修改','c'*64,self.doc['id']))
        loaded = Assistant(self.store,content,self.config)
        self.assertEqual(loaded.status()['messages'][-1]['citations'],[citation])
        self.assertEqual(loaded.start({'message':'同一个请求','request_id':result['id']})['citations'],[citation])
        self.assertEqual(json.loads(loaded.history_path.read_text())[-1]['citations'],[citation])

    def test_workflow_keeps_read_sources_even_if_a_later_stage_has_not_read_them(self):
        parent = {'id':'a'*32,'workflow':{},'citations':[]}
        self.agent.jobs[parent['id']] = parent
        seen = []
        self.agent.workflows.persist = lambda job: None
        self.agent.persist_pending = lambda job: seen.append(copy.deepcopy(job))
        citation = {'url':'/knowledge.html?document='+self.doc['id'],'excerpt':'合成片段'}
        child = {'_workflow_parent_id':parent['id'],'citations':[citation]}
        self.agent.workflows.progress(child)
        self.agent.workflows.progress(child)
        self.agent.workflows.progress({'_workflow_parent_id':parent['id'],'citations':[]})
        self.assertEqual(parent['citations'],[citation])
        self.assertEqual(seen[-1]['citations'],[citation])

    def test_search_candidates_do_not_gain_a_read_card(self):
        job = {'cancel':False}
        self.agent.knowledge_tools.search_knowledge({'query':'合成原文'},job)
        self.assertNotIn('citations',job)


if __name__=='__main__':
    unittest.main()
