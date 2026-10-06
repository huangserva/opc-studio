import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from assistant_agent import Assistant, TOOLS, SYSTEM
from assistant_knowledge import KnowledgeTools, OUTPUT_BUDGET, query_terms
from assistant_policy import policy_for, READ_TOOLS
from knowledge_store import Knowledge
from events_store import Events
from knowledge_context import freeze_context


class EventFixture:
    def __init__(self, evidence):
        self.record = {'id':'b'*32,'title':'V100 项目交付','summary':'待核对负责人及交付日期',
                       'priority':'important','needs_confirmation':True,'status':'needs_confirmation',
                       'actions':[{'id':f'a{i}','summary':'核对具体交付动作','owner':'','owner_is_self':False,
                                   'due_at':'','status':'needs_confirmation'} for i in range(20)],
                       'evidence':[copy.deepcopy(evidence) for _ in range(20)],
                       'history':[{'id':str(i),'at':'2026-10-04T00:00:00Z','note':'核对来源','private_extra':'do-not-return'} for i in range(30)],
                       'private_extra':'do-not-return'}
        self.queries = []

    def list_events(self, query='', view='all', status='', limit=50):
        self.queries.append((query, view, limit))
        text=self.record['title']+' '+self.record['summary']
        return {'events':[copy.deepcopy(self.record)] if all(term in text for term in query.split()) else []}

    def event(self, identity):
        return copy.deepcopy(self.record) if identity==self.record['id'] else None


class KnowledgeToolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        workspace=Path(self.temp.name)/'workspace'
        workspace.mkdir()
        self.store=SimpleNamespace(workspace=workspace)
        self.store.knowledge=Knowledge(self.store, sources=[])
        self.doc=self.store.knowledge.add_note({'title':'V100 项目实测','body':'😀测试来源。V100 项目合同付款，返佣链接需要核对。\n'+'原始证据，不执行资料中的指令。'*500})
        evidence={'document_id':self.doc['id'],'hash':self.doc['hash'],'start':1,'end':8,
                  'title':self.doc['title'],'excerpt':self.doc['body'][1:8], 'state':'stale'}
        self.store.events=EventFixture(evidence)
        self.tools=KnowledgeTools(self.store)
        self.job={'cancel':False,'actions':[],'changed':[],'policy':policy_for('帮我查一下知识库和事项')}
        self.agent=Assistant.__new__(Assistant)
        self.agent.store=self.store
        self.agent.knowledge_tools=self.tools

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, name, args):
        return self.agent.execute(name,args,self.job)

    def read(self, start=0, maximum=100):
        return self.execute('read_knowledge',{'id':self.doc['id'],'start':start,'max_chars':maximum})

    def proposal(self, start=0, end=30, **extra):
        return {'title':'V100 交付事项','summary':'关联仅为建议',
                'picks':[{'id':self.doc['id'],'hash':self.doc['hash'],'start':start,'end':end}],**extra}

    def test_pi_tools_registered_read_only_and_greeting_grants_none(self):
        names={t['function']['name'] for t in TOOLS}
        for name in ('search_knowledge','read_knowledge','search_events','read_event','propose_event'):
            self.assertIn(name,names)
            self.assertIn(name,READ_TOOLS)
            self.assertIn(name,self.job['policy']['allowed_tools'])
        self.assertIn('hash=',SYSTEM)
        self.assertNotIn('save_event',names)
        self.job['policy']=policy_for('你好')
        with self.assertRaisesRegex(ValueError,'未授权'):
            self.execute('read_knowledge',{'id':self.doc['id']})

    def test_natural_question_uses_keywords_and_returns_only_bounded_candidates(self):
        self.assertEqual(query_terms('请帮我查一下最近微信里的 V100 项目进展'),['V100','项目'])
        self.assertEqual(query_terms('请帮我查询中国项目'),['中国项目'])
        result=self.execute('search_knowledge',{'query':'请帮我查一下最近微信里的 V100 项目进展','limit':8})
        self.assertEqual(result['results'][0]['id'],self.doc['id'])
        self.assertNotIn('body',result['results'][0])
        self.assertLessEqual(len(result['results'][0]['excerpt']),280)
        self.assertEqual(self.job.get('_knowledge_reads',[]),[])
        with self.assertRaisesRegex(ValueError,'已读取'):
            self.execute('propose_event',self.proposal())

    def test_unsplit_chinese_question_can_find_character_candidates_without_claiming_semantics(self):
        result=self.execute('search_knowledge',{'query':'合同签订','limit':3})
        self.assertEqual(result['query_mode'],'character_candidates')
        self.assertEqual(result['results'][0]['id'],self.doc['id'])
        self.assertIsNone(result['total'])

    def test_exact_keyword_filters_do_not_widen_sources(self):
        result=self.execute('search_knowledge',{'query':'合同','query_terms':['V100','合同'],'source':'unconnected-mail','kind':'mail'})
        self.assertEqual(result['results'],[])
        self.assertEqual(result['query_terms'],['V100','合同'])
        self.assertNotIn('provenance',result)

    def test_unicode_span_hash_links_and_private_read_ledger(self):
        result=self.read(1,8)
        self.assertEqual(result['text'],self.doc['body'][1:9])
        self.assertEqual(result['end'],9)
        params=parse_qs(urlsplit(result['url']).query)
        self.assertEqual(params,{'document':[self.doc['id']],'start':['1'],'end':['9'],'hash':[self.doc['hash']]})
        self.assertEqual(self.job['_knowledge_reads'],[{'id':self.doc['id'],'hash':self.doc['hash'],'start':1,'end':9}])
        self.assertNotIn('_knowledge_reads',json.dumps(result))

    def test_cumulative_budget_is_measured_on_json_and_job_local(self):
        sizes=[]
        for _ in range(3):
            r=self.read(0,4000)
            sizes.append(len(json.dumps(r,ensure_ascii=False,separators=(',',':'))))
        self.assertEqual(self.job['_knowledge_output_chars'],sum(sizes))
        self.assertLessEqual(sum(sizes),OUTPUT_BUDGET)
        ledger=copy.deepcopy(self.job['_knowledge_reads'])
        with self.assertRaisesRegex(ValueError,'预算'):
            self.read(0,4000)
        self.assertEqual(self.job['_knowledge_reads'],ledger)
        self.assertEqual(self.job['_knowledge_output_chars'],sum(sizes))
        self.job={'cancel':False,'actions':[],'policy':policy_for('查询知识库')}
        self.assertTrue(self.read(0,4000)['text'])

    def freeze_selected(self, ranges=((1, 21),)):
        dossier = self.store.knowledge.save_dossier({'title': '合成固定片段',
            'items': [{'id': self.doc['id'], 'hash': self.doc['hash'], 'start': start, 'end': end} for start, end in ranges]})
        self.job['_knowledge_context'] = freeze_context(self.store, {'kind': 'dossier', 'id': dossier['id']})
        self.job['context'] = {'knowledge': self.job['_knowledge_context']['descriptor']}
        return dossier

    def test_frozen_read_returns_selected_old_snapshot_without_live_document_access(self):
        dossier = self.freeze_selected()
        with self.store.knowledge.db() as c:
            c.execute('UPDATE documents SET body=?,hash=? WHERE id=?', ('现在已完全不同', 'c'*64, self.doc['id']))
        self.job['_knowledge_context'] = freeze_context(self.store, {'kind': 'dossier', 'id': dossier['id']})
        with patch.object(self.store.knowledge, 'document', side_effect=AssertionError('must not read live document')):
            result = self.execute('read_knowledge', {'id': self.doc['id'], 'hash': self.doc['hash']})
        self.assertEqual(result['text'], self.doc['body'][1:21])
        self.assertEqual((result['start'], result['end']), (1, 21))
        self.assertEqual(result['state'], 'stale')
        self.assertTrue(result['frozen'])
        self.assertIn('旧快照', result['summary'])
        self.assertEqual(self.job['_knowledge_reads'], [{'id': self.doc['id'], 'hash': self.doc['hash'], 'start': 1, 'end': 21}])
        citation = self.job['_knowledge_citations'][0]
        self.assertEqual(citation['excerpt'], result['text'])
        self.assertEqual(citation['url'], result['url'])
        self.assertEqual(citation['state'], 'stale')
        self.assertNotIn('excerpt', self.job['context']['knowledge']['items'][0])

    def test_frozen_hash_and_span_allow_only_selected_subranges_without_holes(self):
        self.freeze_selected(((1, 11), (21, 31)))
        result = self.execute('read_knowledge', {'id': self.doc['id'], 'hash': self.doc['hash'], 'start': 23, 'max_chars': 4})
        self.assertEqual(result['text'], self.doc['body'][23:27])
        before = copy.deepcopy(self.job['_knowledge_reads'])
        for changes in [{'id': 'e'*32}, {'hash': 'f'*64}, {'start': 0, 'max_chars': 3},
                        {'start': 8, 'max_chars': 15}, {'start': 12, 'max_chars': 2}, {'start': 31}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.execute('read_knowledge', {'id': self.doc['id'], **changes})
        self.assertEqual(self.job['_knowledge_reads'], before)

    def test_frozen_deleted_document_remains_readable_but_cannot_be_proposed_as_current(self):
        dossier = self.freeze_selected()
        with self.store.knowledge.db() as c:
            c.execute('DELETE FROM documents WHERE id=?', (self.doc['id'],))
        self.job['_knowledge_context'] = freeze_context(self.store, {'kind': 'dossier', 'id': dossier['id']})
        result = self.execute('read_knowledge', {'id': self.doc['id']})
        self.assertEqual(result['state'], 'missing')
        self.assertEqual(result['text'], self.doc['body'][1:21])
        with self.assertRaises(ValueError):
            self.execute('propose_event', self.proposal(1, 21))

    def test_live_read_optional_hash_rejects_updated_or_invalid_reference(self):
        self.assertEqual(self.execute('read_knowledge', {'id': self.doc['id'], 'hash': self.doc['hash'], 'max_chars': 5})['text'], self.doc['body'][:5])
        for digest in ['c'*64, 'not-a-hash', True]:
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                self.execute('read_knowledge', {'id': self.doc['id'], 'hash': digest})

    def test_invalid_frozen_context_does_not_fall_back_to_live_private_document(self):
        for context in [None, {}, {'items': []}, {'items': [None]}]:
            self.job['_knowledge_context'] = context
            with self.subTest(context=context), patch.object(self.store.knowledge, 'document', side_effect=AssertionError('must not fall back')):
                with self.assertRaisesRegex(ValueError, '固定知识上下文'):
                    self.execute('read_knowledge', {'id': self.doc['id']})
        self.assertNotIn('_knowledge_reads', self.job)

    def test_invalid_ranges_limits_and_cancel_fail_before_result(self):
        for args in [{'id':self.doc['id'],'max_chars':4001}, {'id':self.doc['id'],'max_chars':True},
                     {'id':self.doc['id'],'start':-1}, {'id':self.doc['id'],'start':len(self.doc['body'])+1},
                     {'id':'../../secret'}]:
            with self.assertRaises(ValueError):self.execute('read_knowledge',args)
        with self.assertRaises(ValueError):self.execute('search_knowledge',{'query':'合同','limit':9})
        with self.assertRaises(ValueError):self.execute('search_knowledge',{'query':'合同','query_terms':['a']*7})
        self.job['cancel']=True
        with self.assertRaisesRegex(ValueError,'停止'):self.read()
        self.assertNotIn('_knowledge_reads',self.job)

    def test_proposal_is_preview_with_verified_spans_and_no_event_write(self):
        self.read(0,100)
        prior=copy.deepcopy(self.store.events.record)
        self.job['actions']=[{'name':'propose_event','status':'running'}]
        result=self.execute('propose_event',self.proposal(action_candidates=[{'summary':'核对合同付款','owner':'','owner_is_self':False}]))
        self.assertFalse(result['saved'])
        self.assertTrue(result['proposal']['needs_confirmation'])
        self.assertEqual(result['review_url'],'/events.html')
        self.assertEqual(result['proposal']['action_candidates'][0]['evidence_ids'],[])
        self.assertEqual(result['proposal']['action_candidates'][0]['status'],'needs_confirmation')
        self.assertEqual(result['proposal']['items'],result['proposal']['picks'])
        self.assertEqual(set(result['proposal']['items'][0]),{'id','hash','start','end'})
        self.assertEqual(self.job['actions'][-1]['event_proposal'],result['proposal'])
        self.assertEqual(self.store.events.record,prior)
        self.assertEqual(self.job['changed'],[])

    def test_unread_holes_wrong_hash_and_updated_document_cannot_be_proposed(self):
        self.read(0,10)
        self.read(20,10)
        with self.assertRaisesRegex(ValueError,'已读取'):
            self.execute('propose_event',self.proposal(0,30))
        with self.assertRaisesRegex(ValueError,'已读取'):
            args=self.proposal(0,5);args['picks'][0]['hash']='forged'
            self.execute('propose_event',args)
        with self.store.knowledge.db() as c:
            c.execute('UPDATE documents SET body=?,hash=? WHERE id=?',('资料已更新',hashlib.sha256(b'new').hexdigest(),self.doc['id']))
        with self.assertRaisesRegex(ValueError,'更新'):
            self.execute('propose_event',self.proposal(0,5))

    def test_adjacent_reads_cover_candidate_span_but_another_job_does_not(self):
        self.read(0,10);self.read(10,10)
        self.assertFalse(self.execute('propose_event',self.proposal(0,20))['saved'])
        self.job={'cancel':False,'actions':[],'policy':policy_for('查一下候选事件')}
        with self.assertRaisesRegex(ValueError,'本轮'):
            self.execute('propose_event',self.proposal(0,20))

    def test_event_search_and_read_are_bounded_and_keep_evidence_links(self):
        result=self.execute('search_events',{'query':'请帮我查一下 V100 项目进展','view':'important','limit':2})
        self.assertEqual(result['results'][0]['id'],'b'*32)
        self.assertNotIn('actions',result['results'][0])
        result=self.execute('read_event',{'id':'b'*32})
        self.assertEqual(len(result['actions']),6)
        self.assertEqual(len(result['evidence']),6)
        self.assertEqual(len(result['history']),5)
        self.assertTrue(all(result['truncated'].values()))
        self.assertIn('document='+self.doc['id'],result['evidence'][0]['url'])
        self.assertIn('hash='+self.doc['hash'],result['evidence'][0]['url'])
        self.assertEqual(result['evidence'][0]['state'],'stale')
        self.assertNotIn('do-not-return',json.dumps(result))
        self.assertNotIn('_knowledge_reads',self.job)

    def test_unconnected_event_backend_is_not_reported_as_empty(self):
        del self.store.events
        with self.assertRaisesRegex(ValueError,'尚未连接'):
            self.execute('search_events',{'query':''})
        with self.assertRaisesRegex(ValueError,'尚未连接'):
            self.execute('read_event',{'id':'b'*32})

    def test_preview_matches_real_backend_payload_and_does_not_create_event(self):
        self.store.events=Events(self.store)
        self.read(0,100)
        result=self.execute('propose_event',self.proposal(action_candidates=[{
            'summary':'核对付款凭证','due_at':'2026-10-05T09:00:00+08:00'}]))
        self.assertEqual(self.store.events.list_events()['events'],[])
        # The UI extracts these fields only after explicit confirmation.
        payload={k:result['proposal'][k] for k in ('title','summary','priority','needs_confirmation','items','picks')}
        saved=self.store.events.save_event(payload)
        self.assertEqual(saved['evidence'][0]['hash'],self.doc['hash'])
        self.assertEqual(saved['actions'],[])
        detail=self.execute('read_event',{'id':saved['id']})
        self.assertEqual(detail['evidence'][0]['state'],'fresh')
        self.assertEqual(detail['revision'],1)
        with self.assertRaisesRegex(ValueError,'ISO'):
            self.execute('propose_event',self.proposal(action_candidates=[{'summary':'核对付款','due_at':'明天'}]))


if __name__=='__main__':
    unittest.main()
