import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
import threading
import io
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('studio',Path(__file__).resolve().parents[1]/'server.py')
studio=importlib.util.module_from_spec(spec);spec.loader.exec_module(studio)

class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=studio.Store(self.root/'workspace')
        self.article=self.store.create({'title':'测试稿'})
        self.slug=self.article['slug']
        self.store.save(self.slug,{'base_version':'v0001','title':'测试稿','blocks':[{'kind':'text','html':'<p>原来的文字</p>'}]})
        config=self.root/'model.json';config.write_text(json.dumps({'endpoint':'https://example.invalid/chat/completions','api_key':'secret-test-value','model':'test-model'}))
        self.agent=studio.assistant_module.Assistant(self.store,studio.content,config)
        self.job={'read_versions':{},'read_blocks':{},'changed':[],'cancel':False,'policy':{'allowed_tools':[t['function']['name'] for t in studio.assistant_module.TOOLS],'allow_insert':True}}

    def tearDown(self):self.temp.cleanup()

    def test_read_before_write_and_concurrent_version_guard(self):
        args={'slug':self.slug,'base_version':'v0002','edits':[{'index':0,'html':'<p>改过的文字</p>'}],'summary':'润色'}
        with self.assertRaises(ValueError):self.agent.execute('update_text',args,self.job)
        self.agent.execute('read_work',{'slug':self.slug},self.job)
        result=self.agent.execute('update_text',args,self.job)
        self.assertEqual(result['version'],'v0003')
        self.assertEqual(self.store.detail(self.slug,'v0002')['blocks'][0]['text'],'原来的文字')
        with self.assertRaises(ValueError):self.agent.execute('update_text',args,self.job)

    def test_generate_card_and_insert_is_a_snapshot(self):
        self.agent.execute('read_work',{'slug':self.slug},self.job)
        result=self.agent.execute('create_card',{'title':'内容工作台','subtitle':'图文与视频统一管理','theme':'blue','slug':self.slug,'base_version':'v0002','insert':True},self.job)
        self.assertTrue(result['image_url'].endswith('.png'))
        self.assertEqual(result['version'],'v0003')
        self.assertEqual(self.store.detail(self.slug)['image_count'],1)
        self.assertEqual(self.store.detail(self.slug,'v0002')['image_count'],0)

    def test_collection_tool_preserves_other_memberships_and_content(self):
        original=self.store.save_collection({'name':'原有合集','add':[self.slug]})
        target=self.store.save_collection({'name':'AI工具'})
        result=self.agent.execute('organize_collection',{'id':target['id'],'add':[self.slug]},self.job)
        self.assertEqual(result['articles'],[self.slug])
        memberships={c['id']:c['articles'] for c in self.store.collections()}
        self.assertIn(self.slug,memberships[original['id']])
        self.assertEqual(self.store.meta(self.slug)['head'],'v0002')
        self.agent.execute('organize_collection',{'id':target['id'],'remove':[self.slug]},self.job)
        self.assertEqual(self.store.detail(self.slug)['blocks'][0]['text'],'原来的文字')

    def test_real_protocol_loop_with_fake_model_no_credentials_in_status(self):
        calls=[{'role':'assistant','content':None,'tool_calls':[{'id':'read1','type':'function','function':{'name':'read_work','arguments':json.dumps({'slug':self.slug})}}]},
               {'role':'assistant','content':None,'tool_calls':[{'id':'edit1','type':'function','function':{'name':'update_text','arguments':json.dumps({'slug':self.slug,'base_version':'v0002','edits':[{'index':0,'html':'<p>模型修改</p>'}],'summary':'修改一段'})}}]},
               {'role':'assistant','content':'已保存 v0003。'}]
        self.agent.client=lambda messages,**kwargs:calls.pop(0)
        result=self.agent.start({'message':'修改第一段','request_id':'a'*32,'context':{'slug':self.slug,'version':'v0002'}})
        deadline=time.time()+3
        while self.agent.active and time.time()<deadline:time.sleep(.01)
        job=self.agent.public_job(result['id'])
        self.assertEqual(job['status'],'done');self.assertEqual(len(job['actions']),2)
        self.assertEqual(self.store.detail(self.slug)['blocks'][0]['text'],'模型修改')
        self.assertNotIn('secret-test-value',json.dumps(self.agent.status()))
        self.assertNotIn('secret-test-value',self.agent.history_path.read_text())
        self.assertEqual(self.agent.start({'message':'修改第一段','request_id':'a'*32})['id'],result['id'])
        reloaded=studio.assistant_module.Assistant(self.store,studio.content,self.agent.config_path)
        self.assertEqual(len(reloaded.status()['messages']),1)

    def test_cancel_and_unsaved_guard(self):
        with self.assertRaises(ValueError):self.agent.start({'message':'修改','unsaved':True})
        entered=threading.Event();release=threading.Event()
        def model(messages,**kwargs):
            entered.set();release.wait(2)
            return {'role':'assistant','content':'不应继续','tool_calls':[{'id':'bad','type':'function','function':{'name':'organize_collection','arguments':'{"name":"不应创建"}'}}]}
        self.agent.client=model
        result=self.agent.start({'message':'归档','request_id':'b'*32})
        self.assertTrue(entered.wait(5));self.agent.stop(result['id']);release.set()
        deadline=time.time()+3
        while self.agent.active and time.time()<deadline:time.sleep(.01)
        self.assertEqual(self.agent.public_job(result['id'])['status'],'cancelled')
        self.assertEqual(self.store.collections(),[])
        with self.assertRaises(ValueError):self.agent.execute('unknown',{},self.job)

    def wait_job(self, result):
        deadline=time.time()+3
        while self.agent.active and time.time()<deadline:time.sleep(.01)
        self.assertIsNone(self.agent.active)
        return self.agent.public_job(result['id'])

    def test_greeting_cannot_resume_failed_archive_even_if_model_would(self):
        old={'id':'old','prompt':'把测试稿加入旧合集','answer':'模型超时','status':'error','time':'2026-10-01T00:00:00+00:00','actions':[],
             'model_messages':[{'role':'user','content':'把测试稿加入旧合集'}]}
        self.agent.turns=[old]
        def forbidden(*args,**kwargs):raise AssertionError('Greeting must not contact model')
        self.agent.client=forbidden
        result=self.wait_job(self.agent.start({'message':'你好','unsaved':True}))
        self.assertEqual(result['status'],'done');self.assertEqual(result['actions'],[])
        self.assertEqual(result['answer'],'用户，您好！')
        self.assertEqual(self.store.collections(),[])
        self.assertEqual(self.store.meta(self.slug)['head'],'v0002')

    def test_read_only_request_blocks_model_write_before_any_execution(self):
        self.agent.client=lambda messages,**kwargs:{'role':'assistant','tool_calls':[
            {'id':'a','function':{'name':'search_works','arguments':'{}'}},
            {'id':'b','function':{'name':'organize_collection','arguments':'{"name":"意外合集"}'}}]}
        result=self.wait_job(self.agent.start({'message':'请查找测试稿'}))
        self.assertEqual(result['status'],'error');self.assertEqual(result['actions'],[])
        self.assertEqual(self.store.collections(),[])

    def test_tool_gate_checks_latest_policy_and_preview_only(self):
        from assistant_policy import policy_for
        self.job['policy']=policy_for('你好')
        for name,args in [('search_works',{}),('organize_collection',{'name':'越权'})]:
            with self.assertRaises(ValueError):self.agent.execute(name,args,self.job)
        self.job['policy']=policy_for('生成一张封面，只预览，不插入作品')
        with self.assertRaises(ValueError):self.agent.execute('create_card',{'title':'标题','insert':True},self.job)
        self.assertFalse((self.agent.folder/'images').exists())

    def test_historical_commands_are_background_and_explicit_new_archive_works(self):
        self.agent.turns=[{'id':'old','prompt':'修改正文','status':'error','answer':'超时','actions':[],'time':'2026-10-01T00:00:00+00:00'}]
        observed=[]
        calls=[{'role':'assistant','tool_calls':[{'id':'a','function':{'name':'organize_collection','arguments':'{"name":"新合集"}'}}]}, {'role':'assistant','content':'已创建合集。'}]
        def model(messages,**kwargs):observed.append((messages.copy(),kwargs));return calls.pop(0)
        self.agent.client=model
        result=self.wait_job(self.agent.start({'message':'请创建新合集'}))
        self.assertEqual(result['status'],'done')
        self.assertEqual(len(self.store.collections()),1)
        self.assertEqual(len([m for m in observed[0][0] if m['role']=='user']),2)  # Pi retains genuine conversation roles
        self.assertNotIn('update_text',observed[0][1]['allowed_tools'])

    def test_policy_does_not_treat_advice_quotes_or_negation_as_write(self):
        from assistant_policy import policy_for
        writes={'update_text','organize_collection','create_card','generate_media'}
        for text in ['你好！','谢谢','好的','上次归档失败了','我只说了你好，它为什么归档了？','你能修改文字吗？','怎么修改标题？','不要修改正文','如果要归档应该怎么做','归档建议','他说“请修改正文”']:
            self.assertFalse(set(policy_for(text)['allowed_tools'])&writes,text)
        self.assertIn('update_text',policy_for('你好，帮我润色开头')['allowed_tools'])
        self.assertIn('organize_collection',policy_for('请把这篇加入“AI工具”合集')['allowed_tools'])
        self.assertTrue(policy_for('生成一张文字封面，然后插入当前作品')['allow_insert'])

    def test_real_media_policy_and_submit_only_once(self):
        from assistant_policy import policy_for
        from types import SimpleNamespace
        observed=[]
        def start(payload, **kwargs):
            observed.append(payload)
            return {'id':'media-test','status':'connecting','model_name':'Qwen Image 2.1'}
        self.agent.generation=SimpleNamespace(start=start)
        self.job['policy']=policy_for('请生成一张猫咪图片，只预览')
        self.assertNotIn('create_card',self.job['policy']['allowed_tools'])
        result=self.agent.execute('generate_media',{'model':'qi21','prompt':'小猫'},self.job)
        self.assertEqual(result['status'],'connecting')
        self.assertEqual(result['media_job'],'media-test')
        with self.assertRaises(ValueError):self.agent.execute('generate_media',{'model':'qi21','prompt':'又一张'},self.job)
        self.assertEqual(len(observed),1)
        self.job['policy']=policy_for('你好')
        with self.assertRaises(ValueError):self.agent.execute('generate_media',{'model':'qi21','prompt':'旧任务'},self.job)
        self.job['policy']=policy_for('生成一段视频')
        self.assertEqual(self.job['policy']['media_kinds'],['video'])
        with self.assertRaises(ValueError):self.agent.execute('generate_media',{'model':'qi21','prompt':'图'},self.job)

    def test_explicit_krea_model_is_required(self):
        from assistant_policy import policy_for
        from types import SimpleNamespace
        seen=[]
        self.agent.generation=SimpleNamespace(start=lambda payload, **kwargs:(seen.append(payload) or {'id':'krea-job','status':'connecting','model_name':'Krea2 Turbo'}))
        self.job['policy']=policy_for('请用 Krea2 生成一张猫咪图片，只预览')
        self.assertEqual(self.job['policy']['requested_media_model'],'krea2')
        self.assertIn('generate_media',self.job['policy']['allowed_tools'])
        with self.assertRaisesRegex(ValueError,'指定'):self.agent.execute('generate_media',{'model':'qi21','prompt':'cat'},self.job)
        result=self.agent.execute('generate_media',{'model':'krea2','prompt':'cat'},self.job)
        self.assertEqual(result['media_job'],'krea-job')
        self.assertEqual(seen[0]['model'],'krea2')
        self.assertNotIn('generate_media',policy_for('不要用 Krea2 生成图片')['allowed_tools'])

    def test_media_submission_receipt_needs_no_second_model_call(self):
        from types import SimpleNamespace
        calls=[]
        def client(*args,**kwargs):
            calls.append(1)
            if len(calls)>1:raise ValueError('unexpected slow second request')
            return {'tool_calls':[{'id':'media-call','type':'function','function':{'name':'generate_media','arguments':json.dumps({'model':'qi21','prompt':'小猫'})}}]}
        self.agent.client=client
        self.agent.generation=SimpleNamespace(start=lambda payload, **kwargs:{'id':'media-job','status':'connecting','model_name':'Qwen Image 2.1'})
        result=self.wait_job(self.agent.start({'message':'请生成一张猫咪图片'}))
        self.assertEqual(len(calls),1)
        self.assertEqual(result['status'],'done')
        self.assertIn('已提交',result['answer'])
        self.assertEqual(result['actions'][0]['media_job'],'media-job')

    def test_settings_roundtrip_secret_preserved_private_and_no_echo(self):
        public=self.agent.settings()
        self.assertTrue(public['has_api_key']);self.assertNotIn('api_key',public)
        public.update(model='next-model',api_key='',context_tokens=65536,max_output_tokens=2048,temperature=0.5,timeout_seconds=45)
        result=self.agent.save_settings(public)
        self.assertEqual(result['model'],'next-model')
        self.assertNotIn('secret-test-value',json.dumps(result))
        self.assertEqual(self.agent.config()['api_key'],'secret-test-value')
        self.assertEqual(self.agent.config_path.stat().st_mode&0o777,0o600)
        restored=studio.assistant_module.Assistant(self.store,studio.content,self.agent.config_path)
        self.assertEqual(restored.status()['max_output_tokens'],2048)
        self.agent.active='busy'
        with self.assertRaises(ValueError):self.agent.save_settings(public)

    def test_new_endpoint_cannot_reuse_secret_or_save_invalid_values(self):
        original=self.agent.config_path.read_bytes()
        for payload in [{'endpoint':'https://another.example/v1/chat/completions'},
                        {'temperature':float('nan')},{'context_tokens':0},
                        {'endpoint':'http://public.example/v1/chat/completions','api_key':'new'},
                        {'endpoint':'https://user:password@example.org/v1/chat/completions','api_key':'new'}]:
            with self.assertRaises(ValueError):self.agent.save_settings(payload)
            self.assertEqual(self.agent.config_path.read_bytes(),original)
        result=self.agent.save_settings({'endpoint':'http://127.0.0.1:8000/v1/chat/completions','no_api_key':True})
        self.assertFalse(result['has_api_key']);self.assertEqual(self.agent.config()['api_key'],'')

    def test_connection_probe_does_not_save_and_request_uses_parameters(self):
        before=self.agent.config_path.read_bytes()
        c=self.agent.settings();c.update(model='new-model',max_output_tokens=1000,temperature=0.7,timeout_seconds=45)
        with patch('assistant_agent.urllib.request.build_opener') as make:
            make.return_value.open.return_value=io.BytesIO(json.dumps({'model':'actual-model','choices':[{'message':{'role':'assistant','content':'OK'}}]}).encode())
            result=self.agent.test_settings(c)
            request=make.return_value.open.call_args.args[0]
            body=json.loads(request.data)
            self.assertEqual(body['model'],'new-model');self.assertEqual(body['temperature'],0.7)
            self.assertEqual(body['max_tokens'],64);self.assertNotIn('tools',body)
            self.assertEqual(make.return_value.open.call_args.kwargs['timeout'],30)
            self.assertEqual(result['model'],'actual-model')
        self.assertEqual(self.agent.config_path.read_bytes(),before)

    def test_each_turn_records_model_and_keeps_private_config_out_of_history(self):
        self.agent.client=lambda messages,**kw:{'role':'assistant','content':'已回答','_response_model':'served-model'}
        result=self.wait_job(self.agent.start({'message':'解释一下什么是上下文'}))
        self.assertEqual(result['model'],'served-model')
        self.assertEqual(self.agent.status()['messages'][-1]['model'],'served-model')
        self.assertNotIn('model_config',result)
        self.assertNotIn('secret-test-value',self.agent.history_path.read_text())

    def test_profiles_migrate_add_select_and_preserve_independent_secrets(self):
        original=self.agent.config_path.read_bytes()
        first=self.agent.settings()
        self.assertEqual(first['active_profile_id'],'default')
        self.assertEqual(self.agent.config_path.read_bytes(),original)
        second=self.agent.save_settings({'profile_id':None,'display_name':'第二个模型','model':'second-model',
            'endpoint':'https://second.example/v1/chat/completions','api_key':'second-secret'})
        second_id=second['active_profile_id']
        self.assertEqual(len(second['profiles']),2)
        self.assertEqual(self.agent.config()['api_key'],'second-secret')
        self.agent.select_model({'profile_id':'default'})
        self.assertEqual(self.agent.config()['model'],'test-model')
        self.assertEqual(self.agent.config()['api_key'],'secret-test-value')
        self.agent.save_settings({'profile_id':second_id,'model':'second-updated','api_key':''})
        self.assertEqual(self.agent.config()['api_key'],'second-secret')
        restored=studio.assistant_module.Assistant(self.store,studio.content,self.agent.config_path)
        self.assertEqual(restored.config()['model'],'second-updated')
        self.assertEqual(restored.settings()['active_profile_id'],second_id)
        for public in [restored.settings(),restored.status()]:
            self.assertNotIn('second-secret',json.dumps(public))
            self.assertNotIn('secret-test-value',json.dumps(public))
            self.assertNotIn('api_key',public)
            for profile in public['profiles']:self.assertNotIn('api_key',profile)

    def test_new_profile_cannot_borrow_key_and_invalid_selection_is_atomic(self):
        original=self.agent.config_path.read_bytes()
        with self.assertRaises(ValueError):
            self.agent.save_settings({'profile_id':None,'model':'other','endpoint':self.agent.config()['endpoint']})
        for identity in ['missing',None,{},['default']]:
            with self.assertRaises(ValueError):self.agent.select_model({'profile_id':identity})
        self.assertEqual(self.agent.config_path.read_bytes(),original)
        self.agent.active='busy'
        with self.assertRaises(ValueError):self.agent.select_model({'profile_id':'default'})
        self.assertEqual(self.agent.config_path.read_bytes(),original)

    def test_selected_profile_used_for_request_and_job_snapshot(self):
        c=self.agent.save_settings({'profile_id':None,'model':'second-model',
            'endpoint':'https://second.example/v1/chat/completions','api_key':'second-secret'})
        self.agent.select_model({'profile_id':'default'})
        with patch('assistant_agent.urllib.request.build_opener') as make:
            make.return_value.open.return_value=io.BytesIO(json.dumps({'choices':[{'message':{'role':'assistant','content':'OK'}}]}).encode())
            self.agent.request_model([{'role':'user','content':'解释一下'}])
            req=make.return_value.open.call_args.args[0]
            self.assertEqual(json.loads(req.data)['model'],'test-model')
            self.assertEqual(req.get_header('Authorization'),'Bearer secret-test-value')
        self.agent.select_model({'profile_id':c['active_profile_id']})
        observed=[]
        def client(messages,**kwargs):
            observed.append(kwargs['model_config'])
            return {'role':'assistant','content':'回答'}
        self.agent.client=client
        self.wait_job(self.agent.start({'message':'解释一下模型上下文'}))
        self.assertEqual(observed[0]['model'],'second-model')
        self.assertEqual(observed[0]['api_key'],'second-secret')

if __name__=='__main__':unittest.main()
