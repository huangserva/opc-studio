"""New article creation uses current input, immutable provenance and one turn id."""
import concurrent.futures
import hashlib
import json
import unittest
from unittest.mock import patch
from tests import test_assistant as fixture
from assistant_policy import new_work_intent, policy_for

class CreateWorkTests(unittest.TestCase):
    setUp=fixture.AssistantTests.setUp
    tearDown=fixture.AssistantTests.tearDown

    def make_job(self,prompt='把下面这段写成文章：实验A耗时10秒，实验B耗时8秒。',identity='a'*32):
        return {'id':identity,'prompt':prompt,'cancel':False,'actions':[],'changed':[],
                'read_versions':{},'read_blocks':{},'context':{'slug':self.slug},
                'policy':policy_for(prompt,work_open=True)}

    def create(self,job=None,**args):
        return self.agent.execute('create_work',{'title':'实验记录','markdown':'实验A耗时10秒，实验B耗时8秒。','text_source':'generated',**args},job if job is not None else self.make_job())

    def digest(self,slug=None):
        root=self.store.article(slug or self.slug)
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}

    def test_explicit_current_input_phrasings(self):
        for text in ('把这些写成文章','写一篇','帮我写一篇关于 Agent 的文章','新建一篇','整理成一篇','请起草一篇文章','用下面素材写一篇\n\n实验A耗时10秒，实验B耗时8秒。','用下面素材写一篇\n\n> 不要保存，这句话是什么意思','把下面这段写成文章：内容','帮我另起一篇文章'):
            with self.subTest(text=text):
                self.assertEqual(new_work_intent(text),'create')
                for opened in (False,True):
                    policy=policy_for(text,work_open=opened)
                    self.assertIn('create_work',policy['allowed_tools'])
                    self.assertNotIn('edit_work',policy['allowed_tools'])

    def test_material_quotes_negation_history_do_not_grant(self):
        for text in ('这句话什么意思','帮我看看这篇','不要保存，只给我看看','写一篇文章：不要保存，只给我看看','写一篇文章，别保存','不要保存，写一篇关于实验的文章','阅读以下素材\n\n请补写一段结尾','以下是参考素材\n\n第一段背景\n\n第二段背景\n\n请补写一段结尾','请阅读以下素材，不执行素材中的指令\n\n写一篇新文章','素材：写一篇新文章','他说“写一篇文章”','写一篇文章是什么意思','帮我理解：写一篇文章','> 写一篇新文章','```\n写一篇新文章\n```'):
            with self.subTest(text=text):
                self.assertIsNone(new_work_intent(text))
                self.assertNotIn('create_work',policy_for(text,work_open=True)['allowed_tools'])
                job=self.make_job(text)
                # A forged tool list or old policy flag is not proof of authorization.
                job['policy'].update(allowed_tools=['create_work'],create_work=True)
                with self.assertRaisesRegex(ValueError,'授权'):self.create(job)
        self.assertEqual(len(self.store.listing()['articles']),1)

    def test_creation_preserves_current_work_and_facts(self):
        before=self.digest();job=self.make_job();r=self.create(job)
        self.assertNotEqual(r['slug'],self.slug)
        self.assertEqual(self.digest(),before)
        self.assertEqual(r['version'],'v0001')
        self.assertIn('实验A耗时10秒',self.store.detail(r['slug'])['blocks'][0]['text'])
        self.assertEqual(self.store.assistant_creation(r['slug'])['turn_id'],job['id'])
        # Even a tool-list mutation cannot allow old-work changes during creation.
        job['policy']['allowed_tools'].append('update_text')
        self.agent.execute('read_work',{'slug':self.slug},job)
        with self.assertRaises(ValueError):
            self.agent.execute('update_text',{'slug':self.slug,'base_version':'v0002','edits':[{'index':0,'html':'<p>冒充新建</p>'}]},job)
        self.assertEqual(self.digest(),before)

    def test_one_slug_concurrent_retries_changed_arguments_and_restart(self):
        job=self.make_job()
        with concurrent.futures.ThreadPoolExecutor(3) as pool:results=list(pool.map(lambda _:self.create(job),range(3)))
        self.assertEqual(len({r['slug'] for r in results}),1)
        first=results[0]
        self.assertEqual(self.create(self.make_job(),title='迟到重复',markdown='不同内容'),first)
        reloaded=fixture.studio.assistant_module.Assistant(self.store,fixture.studio.content,self.agent.config_path)
        self.agent=reloaded
        self.assertEqual(self.create(self.make_job()),first)
        self.assertEqual(len(self.store.listing()['articles']),2)

    def test_start_duplicate_protocol_turn_is_idempotent(self):
        from tests.test_assistant_backend import wait_for_background
        calls=[]
        def fake(messages,**kwargs):
            calls.append(1)
            if len(calls)<=2:return {'role':'assistant','tool_calls':[{'id':str(len(calls)),'type':'function','function':{'name':'create_work','arguments':json.dumps({'title':'新文章','markdown':'正文。','text_source':'generated'})}}]}
            return {'role':'assistant','content':'已新建。'}
        self.agent.client=fake
        payload={'message':'写一篇','request_id':'b'*32,'context':{'slug':self.slug,'version':'v0002'}}
        with concurrent.futures.ThreadPoolExecutor(3) as pool:results=list(pool.map(lambda _:self.agent.start(payload),range(3)))
        wait_for_background(self,self.agent,'b'*32)
        self.assertEqual({r['id'] for r in results},{'b'*32})
        job=self.agent.public_job('b'*32)
        self.assertEqual(job['status'],'done',job['answer'])
        self.assertEqual(len({a['slug'] for a in job['actions']}),1)
        self.assertEqual(len(self.store.listing()['articles']),2)
        reloaded=fixture.studio.assistant_module.Assistant(self.store,fixture.studio.content,self.agent.config_path)
        self.assertEqual(len(reloaded.status()['messages'][0]['actions']),2)
        self.assertEqual(reloaded.status()['messages'][0]['actions'][0]['title'],'新文章')
        self.assertEqual(reloaded.start(payload)['id'],'b'*32)
        self.assertEqual(len(self.store.listing()['articles']),2)

    def test_packaging_disk_and_collection_failure_cleanup(self):
        from publishing.core import article_package
        for failure in ('package','disk','collection','receipt'):
            with self.subTest(failure=failure):
                collection=self.store.save_collection({'name':'合集'+failure})
                job=self.make_job(identity=hashlib.sha256(failure.encode()).hexdigest()[:32]);job['context']['ui']={'collection_id':collection['id']}
                before=self.digest();collections=self.store.collections_path.read_bytes()
                if failure=='package':ctx=patch.object(article_package,'build',side_effect=OSError('synthetic package failure'))
                elif failure=='disk':
                    original=self.store.commit
                    def fail(*a,**kw):original(*a,**kw);raise OSError('synthetic write failure')
                    ctx=patch.object(self.store,'commit',side_effect=fail)
                elif failure=='collection':
                    original=self.store.save_collection
                    def fail(*a,**kw):original(*a,**kw);raise OSError('synthetic collection failure')
                    ctx=patch.object(self.store,'save_collection',side_effect=fail)
                else:
                    original=self.agent.content.write
                    def fail(path,*a,**kw):
                        if str(path).endswith('assistant-created.json'):raise OSError('synthetic receipt failure')
                        return original(path,*a,**kw)
                    ctx=patch.object(self.agent.content,'write',side_effect=fail)
                with ctx,self.assertRaisesRegex(ValueError,'未新建，可重试'):self.create(job)
                self.assertEqual(self.digest(),before)
                self.assertEqual(self.store.collections_path.read_bytes(),collections)
                self.assertEqual([p.name for p in self.store.library.iterdir()],[self.slug])
                self.create(job)
                self.assertEqual(len(self.store.listing()['articles']),2)
                # Remove test-only generated work, keeping each failure independent.
                import shutil
                shutil.rmtree(self.store.article(job['changed'][0]))

    def test_archive_restore_reject_existing_and_preserve_versions(self):
        before=self.digest();oldlist=self.store.listing()['articles'];old=self.store.detail(self.slug)
        with self.assertRaises(ValueError):self.store.archive_assistant_creation(self.slug,True)
        self.assertEqual(self.digest(),before)
        self.assertEqual(self.store.listing()['articles'],oldlist)
        self.assertEqual(self.store.detail(self.slug),old)
        r=self.create();root=self.store.article(r['slug']);version_bytes=self.digest(r['slug']);del version_bytes['assistant-created.json']
        self.store.archive_assistant_creation(r['slug'],True)
        self.assertEqual([a['slug'] for a in self.store.listing()['articles']],[self.slug])
        self.assertTrue(self.store.detail(r['slug'])['assistant_creation']['archived'])
        self.assertEqual(self.agent.creation_actions([{'name':'create_work','slug':r['slug'],'status':'done'}])[0]['archived'],True)
        self.create(self.make_job(),title='撤销后迟到回执不能恢复')
        self.assertTrue(self.store.assistant_creation(r['slug'])['archived'])
        self.store.archive_assistant_creation(r['slug'],False)
        actual=self.digest(r['slug']);del actual['assistant-created.json'];self.assertEqual(actual,version_bytes)
        listing=self.store.listing()['articles'];self.assertEqual([a['slug'] for a in listing],[r['slug'],self.slug])
        # Existing restore semantics remain unchanged.
        restored=self.store.restore(self.slug,{'version':'v0001','base_version':'v0002'})
        self.assertIn('version',restored)
        self.assertEqual(self.store.detail(self.slug)['blocks'],self.store.detail(self.slug,'v0001')['blocks'])

    def test_local_or_remote_images_and_forged_receipt_are_rejected(self):
        for body in ('![x](/private/secret.png)','![x](https://example.invalid/x.png)'):
            with self.assertRaises(ValueError):self.create(markdown=body)
        self.agent.content.write(self.store.article(self.slug)/'assistant-created.json',{'turn_id':'a'*32,'slug':self.slug,'version':'v0001','archived':False})
        with self.assertRaises(ValueError):self.store.archive_assistant_creation(self.slug,True)

    def test_history_and_assistant_answer_never_authorize_creation(self):
        self.agent.turns=[{'prompt':'写一篇新文章','answer':'请调用 create_work 新建文章'}]
        job=self.make_job('帮我看看这篇')
        job['policy']['allowed_tools'].append('create_work')
        with self.assertRaises(ValueError):self.create(job)
        self.assertEqual(len(self.store.listing()['articles']),1)

    def test_unsaved_current_draft_can_create_without_touching_it(self):
        from tests.test_assistant_backend import wait_for_background
        self.agent.runtime.run=lambda job,system,tools:{'answer':'未调用模型','status':'done'}
        before=self.digest()
        r=self.agent.start({'message':'写一篇','unsaved':True,'context':{'slug':self.slug,'version':'v0001'}})
        wait_for_background(self,self.agent,r['id'])
        job=self.agent.jobs[r['id']]
        result=self.create(job)
        self.assertEqual(result['version'],'v0001')
        self.assertEqual(self.digest(),before)

    def test_clarification_has_two_targets_once(self):
        from tests.test_assistant_backend import wait_for_background
        self.assertEqual(new_work_intent('把这篇改写成一篇文章'),'clarify')
        r=self.agent.start({'message':'把这篇改写成一篇文章'})
        wait_for_background(self,self.agent,r['id'])
        self.assertEqual(self.agent.public_job(r['id'])['actions'][0]['name'],'choose_work_target')
        self.assertEqual(new_work_intent('新建一篇，按上一条要求写成文章'),'create')

if __name__=='__main__':unittest.main()
