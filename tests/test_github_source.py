import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import io
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs

from server import Store
from knowledge_store import Knowledge
from github_source import GitHub, repository, sid, OfficialRedirects
from clue_pipeline import CluePipeline
from clues_store import source_clock


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.store._knowledge=Knowledge(self.store,sources=[])
        self.items=[self.issue()];self.calls=[];self.fail=False
        self.gh=GitHub(self.store,self.transport);self.store._github=self.gh
        self.source=self.gh.configure({'repository':'owner/repo','watch':['issues','pulls']})
    def tearDown(self):self.tmp.cleanup()
    def issue(self, **fields):
        return {'id':10,'number':1,'title':'Evidence memory update','body':'Added accurate evidence citation support.',
                'html_url':'https://github.com/owner/repo/issues/1','user':{'login':'owner'},'state':'open',
                'updated_at':'2026-10-06T00:00:00Z',**fields}
    def transport(self,path):
        self.calls.append(path)
        if path=='/repos/owner/repo':return {'full_name':'owner/repo'}
        if path.startswith('/repos/owner/repo/issues?'):
            if self.fail:raise ValueError('synthetic network failure')
            return self.items
        if path=='/repos/owner/repo/pulls/1':return {'merged_at':'2026-10-06T01:00:00Z','state':'closed'}
        raise AssertionError(path)
    def answer(self,**fields):
        return json.dumps({'clues':[{'title':'引用系统更新','summary':'原文说明增加引用支持，待核对。','relevance':'与记忆系统有关',
                                    'categories':['technology'],'kind':'information','priority':'normal','quotes':[{'ref':'e0'}],**fields}]},ensure_ascii=False)
    def run_analysis(self,response=None,trigger='manual'):
        with patch.object(CluePipeline,'call_pi',return_value=self.answer() if response is None else response):
            return self.gh.analyze('a'*32,{'body':'记忆系统','execution_trigger':trigger},threading.Event(),{'model':'synthetic'},time.monotonic()+30)
    def test_repository_urls_and_rejection(self):
        for value in ['owner/repo','https://github.com/owner/repo.git','git@github.com:owner/repo.git']:
            self.assertEqual(repository(value),'owner/repo')
        for value in ['https://other.example/owner/repo','https://github.com@other.example/a/b','a/b/../../x','https://github.com/a/b?token=x','a/b;echo x',None]:
            with self.assertRaises(ValueError):repository(value)
        self.assertEqual(sid('Owner/Repo'),sid('owner/repo'))
    def test_invalid_watch_and_switch(self):
        for payload in [{'watch':[{}]},{'watch':['issues','issues']},{'watch':[]},{'enabled':'yes'}]:
            with self.assertRaises(ValueError):self.gh.configure({'repository':'owner/repo',**payload})
    def test_redirect_cannot_send_credentials_elsewhere(self):
        with self.assertRaises(ValueError):OfficialRedirects().redirect_request(None,None,302,'',{},'https://evil.example/token')
    def test_raw_capture_and_index_no_extra_requests(self):
        self.gh.collect(self.source);doc=self.gh.pending()[0]
        self.assertIn(self.items[0]['body'],doc['body']);self.assertEqual(doc['url'],self.items[0]['html_url'])
        self.assertEqual(doc['provenance']['repository'],'owner/repo')
        count=len(self.calls);self.store.knowledge.sync(self.source['id']);self.assertEqual(len(self.calls),count)
        self.assertEqual(self.gh.state(self.source['id'])['status'],'ready')
    def test_exact_snapshot_and_no_duplicate_on_repeat(self):
        result=self.run_analysis();data=json.loads(result[2]);self.assertEqual(result[0],'succeeded');self.assertEqual(data['processed'],1)
        clue=self.store.clues.detail(data['clues'][0]);self.assertEqual(clue['status'],'candidate')
        e=clue['entries'][0]['evidence'][0];self.assertEqual(e['excerpt'],self.gh.records(self.source['id'])[0]['body'])
        self.assertEqual(e['analysis']['model'],'synthetic');self.assertIn('knowledge.html?document=',e['origin_url'])
        self.assertEqual(json.loads(self.run_analysis()[2])['processed'],0);self.assertEqual(self.store.clues.listing()['total'],1)
    def test_changed_issue_appends_to_original_clue(self):
        first=json.loads(self.run_analysis()[2])['clues'][0]
        self.items=[self.issue(body='Now supports multi-source evidence.',state='closed',updated_at='2026-10-06T02:00:00Z')]
        second=json.loads(self.run_analysis()[2])['clues'][0];self.assertEqual(first,second)
        clue=self.store.clues.detail(first);self.assertEqual(clue['revision'],2);self.assertEqual(clue['entries_total'],2)
        self.assertIn('Added accurate',clue['entries'][1]['evidence'][0]['excerpt'])
    def test_raw_failure_keeps_last_good_data_and_cursor(self):
        self.gh.collect(self.source);before=self.gh.pending()[0]['hash'];since=self.gh.state(self.source['id'])['since']
        self.fail=True
        with self.assertRaises(ValueError):self.gh.collect(self.source)
        self.assertEqual(self.gh.pending()[0]['hash'],before);self.assertEqual(self.gh.state(self.source['id'])['since'],since)
        self.assertTrue(self.gh.state(self.source['id'])['error']);self.assertEqual(self.gh.state(self.source['id'])['status'],'error')
    def test_invalid_json_and_fake_reference_never_advance(self):
        for response in ['invalid',self.answer(quotes=[{'ref':'invented'}])]:
            data=json.loads(self.run_analysis(response)[2]);self.assertTrue(data['errors']);self.assertEqual(data['processed'],0)
            self.assertEqual(self.store.clues.listing()['total'],0)
        self.assertTrue(self.gh.pending(manual=True));self.assertFalse(self.gh.pending())
    def test_three_failures_hold_for_manual_retry(self):
        for _ in range(3):self.run_analysis('invalid')
        self.assertFalse(self.gh.pending());self.assertEqual(len(self.gh.pending(manual=True)),1)
        self.assertEqual(json.loads(self.run_analysis()[2])['processed'],1);self.assertEqual(self.gh.status()['retry_documents'],0)
    def test_invalid_priority_gets_one_pi_repair_with_same_evidence_and_deadline(self):
        with patch.object(CluePipeline,'call_pi',side_effect=[self.answer(priority='high'),self.answer(priority='important')]) as call:
            result=self.gh.analyze('r'*32,{'body':'推理循环'},threading.Event(),{'model':'synthetic'},time.monotonic()+30)
        self.assertEqual(json.loads(result[2])['processed'],1);self.assertEqual(call.call_count,2)
        first,repair=call.call_args_list
        self.assertEqual(repair.args[1],first.args[1]+'-repair');self.assertEqual(first.args[5],repair.args[5])
        self.assertIn(first.args[2],repair.args[2]);self.assertIn('priority 只允许 normal 或 important',first.args[2])
        clue=self.store.clues.detail(json.loads(result[2])['clues'][0]);self.assertEqual(clue['priority'],'important')
        self.assertEqual(clue['entries'][0]['evidence'][0]['analysis']['model'],'synthetic')
    def test_repair_still_rejects_fake_reference_and_does_not_checkpoint(self):
        with patch.object(CluePipeline,'call_pi',side_effect=[self.answer(priority='high'),self.answer(quotes=[{'ref':'invented'}])]) as call:
            result=self.gh.analyze('r'*32,{'body':'技术'},threading.Event(),{'model':'synthetic'},time.monotonic()+30)
        self.assertEqual(call.call_count,2);self.assertEqual(json.loads(result[2])['processed'],0)
        self.assertEqual(self.store.clues.listing()['total'],0);self.assertTrue(self.gh.pending(manual=True))
        with self.gh.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM analyzed').fetchone()[0],0)
    def test_invalid_repair_is_bounded_and_never_coerces_priority(self):
        with patch.object(CluePipeline,'call_pi',return_value=self.answer(priority='high')) as call:
            result=self.gh.analyze('r'*32,{'body':'技术'},threading.Event(),{'model':'synthetic'},time.monotonic()+30)
        self.assertEqual(call.call_count,2);self.assertEqual(json.loads(result[2])['processed'],0)
        self.assertEqual(self.store.clues.listing()['total'],0)
        with self.gh.db() as db:self.assertEqual(db.execute('SELECT attempts FROM retries').fetchone()[0],1)
    def test_model_transport_failure_does_not_trigger_format_repair(self):
        with patch.object(CluePipeline,'call_pi',side_effect=ValueError('模型连接失败')) as call:
            result=self.gh.analyze('r'*32,{'body':'技术'},threading.Event(),{'model':'synthetic'},time.monotonic()+30)
        self.assertEqual(call.call_count,1);self.assertEqual(json.loads(result[2])['processed'],0)
    def test_empty_clues_is_real_receipt(self):
        self.assertEqual(json.loads(self.run_analysis('{"clues":[]}')[2])['processed'],1)
        self.assertFalse(self.gh.pending());self.assertEqual(self.store.clues.listing()['total'],0)
    def test_pull_merge_status_uses_pull_endpoint(self):
        self.items=[self.issue(pull_request={'url':'unused'},state='closed')]
        self.gh.collect(self.source);record=self.gh.records(self.source['id'])[0]
        self.assertEqual(record['provenance']['item_kind'],'pulls');self.assertIn('merged_at',record['body'])
        self.assertIn('/repos/owner/repo/pulls/1',self.calls)
    def test_source_clock_is_upstream_time(self):
        self.gh.collect(self.source);record=self.gh.records(self.source['id'])[0]
        clock,label=source_clock(record);self.assertEqual(clock,'2026-10-06T00:00:00+00:00');self.assertIn('GitHub',label)
    def test_paused_source_no_collection_or_analysis(self):
        self.store.knowledge.configure_source({'id':self.source['id'],'enabled':False});count=len(self.calls)
        self.assertEqual(json.loads(self.run_analysis()[2])['processed'],0);self.assertEqual(len(self.calls),count)
    def test_source_mutation_during_analysis_does_not_checkpoint(self):
        def changed(*args,**kwargs):
            self.items=[self.issue(body='Changed while analyzing')];self.gh.collect(self.source);return self.answer()
        with patch.object(CluePipeline,'call_pi',side_effect=changed):
            result=self.gh.analyze('b'*32,{'body':'memory'},threading.Event(),{'model':'synthetic'},time.monotonic()+30)
        self.assertTrue(json.loads(result[2])['errors']);self.assertEqual(self.store.clues.listing()['total'],0)
    def test_saved_publication_recovers_if_receipt_missing(self):
        first=json.loads(self.run_analysis()[2])['clues'][0]
        with self.gh.db() as db:db.execute('DELETE FROM analyzed');db.execute('DELETE FROM threads')
        self.assertEqual(json.loads(self.run_analysis()[2])['clues'],[first]);self.assertEqual(self.store.clues.detail(first)['revision'],1)
    def test_large_body_not_silently_truncated_and_processed(self):
        self.items=[self.issue(body='a'*100001)];data=json.loads(self.run_analysis()[2]);self.assertEqual(data['processed'],0)
        self.assertTrue(data['errors']);self.assertEqual(len(self.gh.pending(manual=True)),1)
    def test_pages_resume_after_bound_without_losing_old_evidence(self):
        pages=[]
        def transport(path):
            if path=='/repos/owner/repo':return {'full_name':'owner/repo'}
            page=int(parse_qs(urlsplit(path).query)['page'][0]);pages.append(page)
            if page>4:return []
            return [self.issue(id=page*100+i,number=page*100+i) for i in range(100)]
        self.gh.transport=transport
        self.gh.collect(self.source);self.assertEqual(pages,[1,2,3]);self.assertTrue(self.gh.state(self.source['id'])['has_more'])
        self.gh.collect(self.source);self.assertEqual(pages,[1,2,3,4,5]);self.assertFalse(self.gh.state(self.source['id'])['has_more'])
        self.assertEqual(len(self.gh.records(self.source['id'])),400)

    def test_full_long_body_reaches_model_and_quote_keeps_exact_end_span(self):
        self.items=[self.issue(body='a'*14500+'exact ending')]
        prompts=[]
        def answer(*args,**kwargs):
            prompts.append(args[2]);return self.answer(quotes=[{'ref':'e14'}])
        with patch.object(CluePipeline,'call_pi',side_effect=answer):
            result=self.gh.analyze('d'*32,{'body':'memory'},threading.Event(),{'model':'synthetic'},time.monotonic()+30)
        self.assertIn('exact ending',prompts[0]);self.assertEqual(json.loads(result[2])['processed'],1)
        clue=self.store.clues.detail(json.loads(result[2])['clues'][0]);self.assertIn('exact ending',clue['entries'][0]['evidence'][0]['excerpt'])

    def test_changed_watch_resets_capture_cursor_and_reanalysis_scope(self):
        self.gh.collect(self.source)
        self.gh.configure({'repository':'owner/repo','watch':['releases']})
        self.assertIsNone(self.gh.state(self.source['id'])['since']);self.assertFalse(self.gh.pending(manual=True))

    def comment(self,**fields):
        return {'id':100,'user':{'login':'reviewer'},'body':'Verified off; recovery itself remains untested.\r\n原话 😀',
                'html_url':'https://github.com/owner/repo/issues/1#issuecomment-100',
                'created_at':'2026-10-06T02:00:00Z','updated_at':'2026-10-06T02:00:00Z',**fields}

    def setup_comments(self,numbers=None):
        self.reply_items=[self.comment()]
        base=self.transport
        def transport(path):
            if path.startswith('/repos/owner/repo/issues/1/comments?'):return self.reply_items
            if path=='/repos/owner/repo/issues/1':return self.items[0]
            return base(path)
        self.gh.transport=transport
        self.source=self.gh.configure({'repository':'owner/repo','watch':['issues','pulls','comments'],'threads':numbers or []})

    def test_pinned_old_pull_and_replies_are_not_limited_to_lookback(self):
        self.items=[self.issue(updated_at='2020-01-01T00:00:00Z',pull_request={'url':'unused'})]
        self.setup_comments([1]);self.gh.collect(self.source)
        d=self.gh.pending()[0];self.assertEqual(d['provenance']['item_kind'],'pulls')
        c=d['provenance']['comments'][0]
        self.assertEqual(d['body'][c['start']:c['end']],self.reply_items[0]['body'])
        self.assertEqual(c['author'],'reviewer');self.assertEqual(c['url'],self.reply_items[0]['html_url'])
        self.assertEqual(d['modified'],'2026-10-06T02:00:00Z');self.assertTrue(d['provenance']['comments_complete'])
        self.assertFalse(any('/issues?' in p for p in self.calls))

    def test_new_edit_and_deleted_reply_keep_same_clue_and_exact_history(self):
        self.setup_comments([1]);first=json.loads(self.run_analysis()[2])['clues'][0]
        self.reply_items=[self.comment(body='Edited, not a confirmed fix.',updated_at='2026-10-06T03:00:00Z')]
        second=json.loads(self.run_analysis()[2])['clues'][0];self.assertEqual(first,second)
        self.assertEqual(json.loads(self.run_analysis()[2])['processed'],0)
        old=self.store.clues.detail(first)['entries'][1]['evidence'][0]
        self.assertIn('recovery itself remains untested',old['excerpt'])
        self.reply_items=[];third=json.loads(self.run_analysis()[2])['clues'][0]
        self.assertEqual(first,third);self.assertEqual(self.gh.pending(manual=True),[])
        self.assertEqual(self.store.clues.detail(first)['revision'],3)
        self.assertNotIn('Edited, not a confirmed fix.',self.gh.records(self.source['id'])[0]['body'])

    def test_reply_pagination_resumes_and_never_claims_complete_early(self):
        self.setup_comments([1]);base=self.gh.transport;pages=[]
        def transport(path):
            if '/comments?' in path:
                page=int(parse_qs(urlsplit(path).query)['page'][0]);pages.append(page)
                return [self.comment(id=page*100+i) for i in range(100)] if page<=4 else []
            return base(path)
        self.gh.transport=transport;self.gh.collect(self.source)
        d=self.gh.pending()[0];self.assertFalse(d['provenance']['comments_complete']);self.assertEqual(len(d['provenance']['comments']),300)
        self.assertTrue(self.gh.state(self.source['id'])['has_more']);self.assertEqual(pages,[1,2,3])
        self.gh.collect(self.source);d=self.gh.pending()[0]
        self.assertTrue(d['provenance']['comments_complete']);self.assertEqual(len(d['provenance']['comments']),400)
        self.assertEqual(pages,[1,2,3,4,5]);self.assertFalse(self.gh.state(self.source['id'])['has_more'])

    def test_comment_failure_keeps_index_and_resumes_from_last_successful_page(self):
        self.setup_comments([1]);self.gh.collect(self.source);old=self.gh.pending()[0]['hash']
        base=self.gh.transport;pages=[];failed=[True]
        def transport(path):
            if '/comments?' in path:
                page=int(parse_qs(urlsplit(path).query)['page'][0]);pages.append(page)
                if page==1:return [self.comment(id=100+i) for i in range(100)]
                if failed[0]:raise ValueError('synthetic comment failure')
                return []
            return base(path)
        self.gh.transport=transport
        with self.assertRaises(ValueError):self.gh.collect(self.source)
        self.assertEqual(self.gh.pending()[0]['hash'],old);failed[0]=False
        self.gh.collect(self.source);self.assertEqual(pages,[1,2,2])
        self.assertEqual(len(self.gh.pending()[0]['provenance']['comments']),100)

    def test_invalid_pins_and_incompatible_scope_fail_before_network(self):
        before=len(self.calls)
        for nums in [[True],[0],[-1],['1'],[1.0],[1,1],list(range(1,22))]:
            with self.assertRaises(ValueError):self.gh.configure({'repository':'owner/repo','watch':['issues'],'threads':nums})
        for watch in [['comments'],['issues','commits'],['pulls','releases']]:
            with self.assertRaises(ValueError):self.gh.configure({'repository':'owner/repo','watch':watch,'threads':[1]})
        self.assertEqual(len(self.calls),before)

    def test_changed_pins_exclude_old_thread_from_analysis(self):
        self.setup_comments([1]);self.gh.collect(self.source)
        self.gh.configure({'repository':'owner/repo','watch':['issues','pulls','comments'],'threads':[2]})
        self.assertIsNone(self.gh.state(self.source['id'])['progress']);self.assertFalse(self.gh.pending(manual=True))

    def test_repository_reply_discovery_refreshes_old_parent_not_in_issue_page(self):
        self.setup_comments();self.items=[]
        base=self.gh.transport
        def transport(path):
            if path.startswith('/repos/owner/repo/issues/comments?'):
                return [self.comment(issue_url='https://api.github.com/repos/owner/repo/issues/1')]
            if path=='/repos/owner/repo/issues/1':return self.issue(updated_at='2020-01-01T00:00:00Z')
            return base(path)
        self.gh.transport=transport
        # Repository-wide comment discovery uses the shared Issues comments API.
        self.gh.collect(self.source)
        self.assertIn('recovery itself remains untested',self.gh.pending()[0]['body'])

    def test_http_rate_limit_defers_without_disclosing_token(self):
        self.gh.transport=None
        exc=HTTPError('https://api.github.com/repos/owner/repo',403,'Forbidden',{'X-RateLimit-Remaining':'0','X-RateLimit-Reset':str(int(time.time()+300))},io.BytesIO(b'private response'))
        with patch('github_source.local_token',return_value='synthetic-secret'),patch('github_source.build_opener') as opener:
            opener.return_value.open.side_effect=exc
            with self.assertRaises(ValueError) as e:self.gh.request('/repos/owner/repo')
            self.assertNotIn('synthetic-secret',str(e.exception));self.assertNotIn('private response',str(e.exception))
            self.assertGreater(self.gh.settings()['retry_at'],time.time())
            with self.assertRaises(ValueError):self.gh.request('/repos/owner/repo')
            self.assertEqual(opener.return_value.open.call_count,1)
        self.assertNotIn('synthetic-secret',json.dumps(self.gh.settings()))

    def test_etag_304_uses_successful_cached_body(self):
        self.gh.transport=None
        class Response(io.BytesIO):
            headers={'ETag':'"version-1"','X-RateLimit-Remaining':'50'}
            def geturl(self):return 'https://api.github.com/test'
        response=Response(b'{"ok":true}')
        not_modified=HTTPError('https://api.github.com/test',304,'Not Modified',{},io.BytesIO())
        with patch('github_source.local_token',return_value=None),patch('github_source.build_opener') as opener:
            opener.return_value.open.side_effect=[response,not_modified]
            self.assertEqual(self.gh.request('/test'),{'ok':True});self.assertEqual(self.gh.request('/test'),{'ok':True})
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('If-none-match'),'"version-1"')

if __name__=='__main__':unittest.main()
