"""Real local snapshot tools and workflow controller with no model transport."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest

from assistant_agent import Assistant, TOOLS
from assistant_knowledge import KnowledgeTools, OUTPUT_BUDGET, document_link
from assistant_workflow import ContentWorkflow, RESULT_TOOL
import content_store
from knowledge_context import freeze_context
from knowledge_store import Knowledge
from server import Store


class WorkflowKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=Store(self.root/'workspace')
        self.store._knowledge=Knowledge(self.store,sources=[])
        self.doc=self.store.knowledge.add_note({'title':'Synthetic fixed evidence','body':'😀A\r\nB fixed evidence. SYNTHETIC_UNREAD_TAIL'})
        self.dossier=self.store.knowledge.save_dossier({'title':'Synthetic selected context','items':[{'id':self.doc['id'],'hash':self.doc['hash'],'start':1,'end':len(self.doc['body'])}]})
        self.frozen=freeze_context(self.store,{'kind':'dossier','id':self.dossier['id']})
        self.owner=Assistant.__new__(Assistant)
        self.owner.store=self.store;self.owner.content=content_store
        self.owner.folder=self.root/'assistant';self.owner.folder.mkdir()
        self.owner.lock=threading.RLock();self.owner.jobs={};self.owner.turns=[]
        self.owner.history_path=self.owner.folder/'history.json'
        self.owner.persist_pending=lambda job:None
        self.owner.knowledge_tools=KnowledgeTools(self.store)
        self.calls=[]
        self.owner.runtime=SimpleNamespace(node=shutil.which('node'),run=self.fake_runtime)
        self.workflow=ContentWorkflow(self.owner);self.owner.workflows=self.workflow
        self.job={'id':'f'*32,'prompt':'Synthetic bounded source writing','context':{'knowledge':copy.deepcopy(self.frozen['descriptor'])},
                  '_knowledge_context':copy.deepcopy(self.frozen),'actions':[],'cancel':False,'model_config':{'api_key':'fixture-private-key'},
                  '_knowledge_output_chars':OUTPUT_BUDGET,'_knowledge_reads':[{'id':'outside-parent-read'}]}
        self.owner.jobs[self.job['id']]=self.job
        self.workflow.accept(self.job)

    def tearDown(self):
        self.tmp.cleanup()

    def fake_runtime(self,child,system,tools):
        phase=child['_workflow_stage']
        self.calls.append({'stage':phase,'context':copy.deepcopy(child['context']),'prompt':child['prompt'],
                           'budget_before':child['_knowledge_output_chars'],'reads_before':copy.deepcopy(child['_knowledge_reads']),
                           'tools':{t['function']['name'] for t in tools},'snapshot':copy.deepcopy(child['_knowledge_context'])})
        item=child['_knowledge_context']['items'][0]
        offset={'research':0,'draft':8,'review':13}[phase]
        amount=8 if phase=='research' else 5
        result=self.owner.execute('read_knowledge',{'id':item['id'],'hash':item['hash'],'start':item['start']+offset,'max_chars':amount},child)
        self.assertEqual(result['text'],item['excerpt'][offset:offset+amount])
        sources=child['_workflow_sources']
        if phase=='research':
            outcome={'summary':'A fixed source was actually read','evidence':[{'claim':'Synthetic source fact','url':result['url']}]}
        else:
            markdown='Fixed evidence. [First read]('+sources[0]['url']+')\n\n[Current read]('+result['url']+')'
            outcome={'title':'Synthetic grounded draft','markdown':markdown}
            if phase=='review':outcome.update(verdict='passed',notes=['Retained exact fixed-source citations'],unresolved=[])
        self.workflow.submit(outcome,child)
        return {'status':'done','answer':'Synthetic stage completed'}

    def run_stages(self):
        inputs={}
        for phase in ('research','draft','review'):
            response=self.workflow.stage({'stage':phase,'prompt':self.job['prompt'],'inputs':inputs},self.job,'unused outer system',TOOLS)
            self.assertTrue(response['ok'],response)
            inputs=response['structured']
        self.job['workflow']['status']='done';self.job['workflow']['result']=self.job['workflow']['stages'][-1]['result']
        self.workflow.persist(self.job)

    def test_three_stages_keep_frozen_snapshot_and_each_has_a_fresh_bounded_budget(self):
        original=copy.deepcopy(self.frozen)
        with self.store.knowledge.db() as connection:
            connection.execute('DELETE FROM documents WHERE id=?',(self.doc['id'],))
        self.run_stages()
        self.assertEqual([call['stage'] for call in self.calls],['research','draft','review'])
        for call in self.calls:
            self.assertEqual(call['snapshot'],original)
            self.assertEqual(call['budget_before'],0)
            self.assertEqual(call['reads_before'],[])
            self.assertNotIn('excerpt',json.dumps(call['context']))
            self.assertNotIn(original['items'][0]['excerpt'],call['prompt'])
            self.assertTrue({'get_workspace_context','read_knowledge',RESULT_TOOL}<=call['tools'])
            self.assertNotIn('search_knowledge',call['tools'])
            self.assertNotIn('update_text',call['tools'])
        self.assertTrue({'web_search','web_fetch'}<=self.calls[0]['tools'])
        for call in self.calls[1:]:self.assertFalse({'web_search','web_fetch'}&call['tools'])
        sources=self.job['workflow']['sources']
        self.assertEqual(len(sources),3)
        self.assertTrue(all(source['kind']=='knowledge' and source['snapshot'] for source in sources))
        self.assertTrue(all(source['hash']==self.doc['hash'] for source in sources))
        self.assertNotIn('excerpt',json.dumps(sources))

    def test_selected_metadata_unread_ranges_and_forged_versions_do_not_authorize_citations(self):
        item=self.frozen['items'][0]
        url=document_link(item['id'],item['start'],item['start']+8,item['hash'])
        child={'_workflow_stage':'research','_workflow_sources':[{'kind':'knowledge','url':url}],
               '_workflow_knowledge_urls':[],'_knowledge_context':copy.deepcopy(self.frozen),'_knowledge_reads':[]}
        with self.assertRaisesRegex(ValueError,'尚未读取'):
            self.workflow.submit({'summary':'Synthetic fact','evidence':[{'claim':'Synthetic claim','url':url}]},child)
        result={'id':item['id'],'hash':item['hash'],'start':item['start'],'end':item['start']+8,'text':item['excerpt'][:8],'url':url}
        with self.assertRaisesRegex(ValueError,'成功读取'):self.workflow.record_knowledge_source(result,child)
        child['_knowledge_output_chars']=0
        read=self.owner.knowledge_tools.execute('read_knowledge',{'id':item['id'],'hash':item['hash'],'start':item['start'],'max_chars':8},child)
        for modified in ({**read,'url':url.replace(item['hash'],'0'*64)}, {**read,'url':document_link(item['id'],item['start'],item['end'],item['hash'])}):
            with self.assertRaisesRegex(ValueError,'链接'):self.workflow.record_knowledge_source(modified,child)
        self.workflow.record_knowledge_source(read,child)
        outside=document_link(item['id'],item['start']+8,item['start']+10,item['hash'])
        with self.assertRaisesRegex(ValueError,'实际读取'):
            self.workflow.submit({'summary':'Synthetic fact','evidence':[{'claim':'Unread claim','url':outside}]},child)
        self.workflow.submit({'summary':'Synthetic fact','evidence':[{'claim':'Read claim','url':read['url']}]},child)

    def test_emit_budget_failure_never_enters_the_workflow_source_ledger(self):
        item=self.frozen['items'][0]
        child={'cancel':False,'_workflow_stage':'research','_knowledge_context':copy.deepcopy(self.frozen),
               '_knowledge_output_chars':OUTPUT_BUDGET-20,'_knowledge_reads':[],'_workflow_sources':[]}
        with self.assertRaisesRegex(ValueError,'预算'):
            self.owner.knowledge_tools.execute('read_knowledge',{'id':item['id'],'hash':item['hash'],'start':item['start'],'max_chars':8},child)
        self.assertEqual(child['_knowledge_reads'],[])
        self.assertEqual(child['_workflow_sources'],[])

    def test_public_source_still_requires_a_fetched_ledger_and_local_host_is_not_a_web_source(self):
        child={'_workflow_stage':'research','_workflow_sources':[{'url':'https://example.com/read'}]}
        self.workflow.submit({'summary':'Synthetic public fact','evidence':[{'claim':'Fetched claim','url':'https://example.com/read'}]},child)
        with self.assertRaisesRegex(ValueError,'实际读取'):
            self.workflow.submit({'summary':'Synthetic public fact','evidence':[{'claim':'Search-only claim','url':'https://example.com/search-only'}]},child)
        for url in ('http://127.0.0.1/read','/knowledge.html?document='+self.doc['id']):
            child['_workflow_sources']=[{'url':url}]
            with self.assertRaisesRegex(ValueError,'尚未读取'):
                self.workflow.submit({'summary':'Synthetic fact','evidence':[{'claim':'Not a public read','url':url}]},child)

    def test_saved_work_contains_version_bound_snapshots_and_actual_read_source_manifest(self):
        self.run_stages()
        saved=self.workflow.save(self.job['id'])
        article=self.store.article(saved['slug'])
        refs=json.loads((article/'knowledge-sources.json').read_text())
        sources=json.loads((article/'workflow-sources.json').read_text())
        self.assertEqual(refs['version'],saved['version'])
        self.assertEqual(refs['items'],self.frozen['items'])
        self.assertEqual(refs['read_sources'],self.job['workflow']['sources'])
        self.assertEqual(sources['sources'],self.job['workflow']['sources'])
        self.assertEqual(os.stat(article/'knowledge-sources.json').st_mode&0o777,0o600)
        self.assertEqual(os.stat(article/'workflow-sources.json').st_mode&0o777,0o600)
        reopened=freeze_context(self.store,{'kind':'draft','slug':saved['slug'],'version':saved['version']})
        self.assertEqual(reopened['items'][0]['hash'],self.doc['hash'])
        self.assertEqual(reopened['items'][0]['excerpt'],self.frozen['items'][0]['excerpt'])
        self.assertEqual(self.workflow.save(self.job['id']),saved)
        self.assertEqual(len(self.store.listing()['articles']),1)

    def test_commit_before_receipt_recovery_repairs_missing_source_sidecars(self):
        self.run_stages()
        saved=self.workflow.save(self.job['id']);article=self.store.article(saved['slug'])
        (article/'knowledge-sources.json').unlink();(article/'workflow-sources.json').unlink()
        record=self.workflow.read(self.job['id']);record['saved']=None;content_store.write(self.workflow.path(self.job['id']),record)
        recovered=self.workflow.save(self.job['id'])
        self.assertEqual(recovered,saved)
        self.assertTrue((article/'knowledge-sources.json').is_file())
        self.assertEqual(json.loads((article/'knowledge-sources.json').read_text())['version'],saved['version'])
        self.assertEqual(len(self.store.listing()['articles']),1)

    def test_commit_recovery_does_not_bind_old_evidence_to_a_changed_head(self):
        self.run_stages()
        saved=self.workflow.save(self.job['id']);article=self.store.article(saved['slug'])
        self.store.save(saved['slug'],{'base_version':saved['version'],'title':'A later manual draft'})
        changed=self.store.meta(saved['slug'])['head']
        self.assertNotEqual(changed,saved['version'])
        for name in ('knowledge-sources.json','workflow-sources.json'):(article/name).unlink()
        record=self.workflow.read(self.job['id']);record['saved']=None;content_store.write(self.workflow.path(self.job['id']),record)
        with self.assertRaisesRegex(ValueError,'作品已更新'):
            self.workflow.save(self.job['id'])
        self.assertFalse((article/'knowledge-sources.json').exists())
        self.assertFalse((article/'workflow-sources.json').exists())
        self.assertEqual(self.store.meta(saved['slug'])['head'],changed)
        self.assertEqual(len(self.store.listing()['articles']),1)

    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_real_controller_carries_descriptors_without_loading_a_model(self):
        result=self.workflow.run(self.job,'unused outer system',TOOLS)
        self.assertEqual(result['status'],'done')
        self.assertEqual([call['stage'] for call in self.calls],['research','draft','review'])
        for call in self.calls:
            self.assertIn(self.doc['hash'],call['prompt'])
            self.assertNotIn(self.frozen['items'][0]['excerpt'],call['prompt'])
        self.assertEqual(self.job['workflow']['result']['verdict'],'passed')


if __name__=='__main__':
    unittest.main()
