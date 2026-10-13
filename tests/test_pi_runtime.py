from tests.test_assistant_backend import wait_for_background
"""Exercise the installed Pi SDK and its real OpenAI streaming adapter over local HTTP."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Store, content, assistant_module
from generation.service import Generation

class PiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.requests = []
        self.respond = lambda body: {'content': '已回答。'}
        self.hold = threading.Event()
        test = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                test.requests.append({'path': self.path, 'body': body,
                                      'auth': self.headers.get('Authorization')})
                result = test.respond(body)
                if result.get('http_error'):
                    self.send_response(result['http_error']); self.end_headers()
                    self.wfile.write(b'{"error":{"message":"unavailable"}}'); return
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                def chunk(delta, reason=None, usage=None):
                    packet = {'id': 'test-chat', 'model': 'fixture', 'object': 'chat.completion.chunk',
                              'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
                    if usage: packet['usage'] = usage
                    self.wfile.write(('data: ' + json.dumps(packet,ensure_ascii=False) + '\n\n').encode())
                    self.wfile.flush()
                try:
                    chunk({'role': 'assistant'})
                    if result.get('partial'): chunk({'content': result['partial']})
                    if result.get('hold'): test.hold.wait(8)
                    if result.get('tool') or result.get('tools'):
                        calls=result.get('tools') or [dict(name=result['tool'],args=result.get('args',{}))]
                        chunk({'tool_calls': [{'index': i, 'id': 'call-'+str(len(test.requests))+'-'+str(i), 'type': 'function',
                            'function': {'name': call['name'], 'arguments': json.dumps(call.get('args', {}))}} for i,call in enumerate(calls)]})
                    elif result.get('thinking'):
                        chunk({'reasoning_content': result['thinking']})
                    else: chunk({'content': result.get('content', '')})
                    chunk({}, result.get('reason') or ('tool_calls' if result.get('tool') or result.get('tools') else 'stop'),
                          {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120})
                    self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError): pass
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.config = {'endpoint': f'http://127.0.0.1:{self.http.server_port}/v1/chat/completions',
                       'model': 'fixture', 'api_key': 'fixture-private-key', 'context_tokens': 65536,
                       'max_output_tokens': 2000, 'temperature': .25, 'timeout_seconds': 10}
        self.config_path = self.root/'config.json'
        self.config_path.write_text(json.dumps(self.config))
        self.store = Store(self.root/'workspace')
        self.agent = self.make_agent()

    def test_blocked_tool_name_reaches_history_without_arguments(self):
        self.respond=lambda body: {'tool':'update_text','args':{'html':'PRIVATE_SYNTHETIC_HTML','api_key':'DO_NOT_RECORD'}}
        result=self.ask('请查询当前作品')
        self.assertEqual(result['status'],'error')
        self.assertIn('本轮未执行修改',result['answer'])
        # 追加2：改稿类工具打开作品时本来就授权，拦截不再拿示例句教用户重说。
        self.assertNotIn('请明确作品和要修改的文字',result['answer'])
        self.assertNotIn('例如“',result['answer'])
        history=json.loads(self.agent.history_path.read_text()) if hasattr(self.agent,'history_path') else self.agent.history
        receipt=history[-1]['runtime_result']
        self.assertEqual(receipt['blocked_tool_names'],['update_text'])
        self.assertEqual(receipt['error_kind'],'tool_policy')
        self.assertNotIn('PRIVATE_SYNTHETIC_HTML',json.dumps(receipt))
        self.assertNotIn('DO_NOT_RECORD',json.dumps(receipt))

    def test_blocked_names_are_deduplicated_bounded_and_validated(self):
        calls=[dict(name='blocked_'+str(i),args={'html':'PRIVATE_SYNTHETIC_HTML'}) for i in range(20)]
        calls.insert(1,dict(name='blocked_0'))
        self.respond=lambda body:{'tools':calls}
        self.ask('查询当前作品')
        receipt=json.loads(self.agent.history_path.read_text())[-1]['runtime_result']
        self.assertEqual(receipt['blocked_tool_names'],['blocked_'+str(i) for i in range(16)]+['[truncated]'])
        self.assertLessEqual(len(receipt['blocked_tool_names']),17)
        self.agent.new_chat()
        self.respond=lambda body:{'tool':'x'*80,'args':{'html':'PRIVATE_SYNTHETIC_HTML'}}
        self.ask('查询当前作品')
        receipt=json.loads(self.agent.history_path.read_text())[-1]['runtime_result']
        self.assertEqual(receipt['blocked_tool_names'],['[invalid_tool_name]'])

    # 追加2（黄总 17:4x「不给 AI 限制，给 AI 帮助」）：改稿类工具打开作品时每轮都授权，
    # 所以拦截路径只保留给「真正不存在的工具名」，提示里不再有策略拒绝模板，
    # 也不再拿示例句把用户的话术框死。原 24d L4 的示例句授权断言按新语义整体改写。
    def test_blocked_path_is_only_for_unknown_tool_names(self):
        from assistant_policy import policy_for
        source=(Path(__file__).resolve().parents[1]/'pi-runtime'/'worker.mjs').read_text()
        self.assertNotIn('TOOL_POLICY_HINTS', source, '改稿/生成/笔记类工具不再按类别给示例句')
        for retired in ('请明确作品和要修改的文字','请明确作品和要调整的结构','请说明要进行的具体修改'):
            self.assertNotIn(retired, source, '模板拒绝文案必须删掉：'+retired)
        # 打开着作品时，黄总这几句都拿得到改稿工具，且不再有任何子句闸门。
        opened=policy_for('随便问一句', work_open=True)['allowed_tools']
        for text in ('看一下这个文章，很多地方本来是要加粗的…是 Markdown 格式，我们这边没有做好，要改一改，标题也应该是标题的格式',
                     '你要通篇看文章，然后处理啊…是要你自己判断啊',
                     '这段太啰嗦了帮我精简','语气再口语一点','标题改得吸引人些','你自己看着改',
                     '通篇润色一遍','这样写好吗？','要不要加粗？'):
            with self.subTest(text=text):
                granted=policy_for(text, work_open=True)['allowed_tools']
                self.assertLessEqual(set(opened), set(granted), '改稿工具不该被任何句子收回：'+text)
                for tool in ('update_text','edit_work','reflow_markdown'):
                    self.assertIn(tool, granted, text)
        # 真的不存在的工具名仍然被拦，且提示不含工具名与参数。
        self.agent.new_chat()
        self.respond=lambda body:{'tool':'zzz_not_a_tool','args':{'html':'PRIVATE_SYNTHETIC_HTML'}}
        result=self.ask('查一下最近的作品')
        self.assertIn('本轮未执行修改',result['answer'])
        self.assertNotIn('zzz_not_a_tool',result['answer'])
        self.assertNotIn('PRIVATE_SYNTHETIC_HTML',result['answer'])

    def test_bold_saves_and_later_violation_reports_saved_version(self):
        article=self.store.create({'title':'合成稿'});slug=article['slug']
        self.store.save(slug,{'base_version':'v0001','title':'合成稿','blocks':[{'kind':'text','html':'<p>合成文字</p>'}]})
        def response(body):
            results=[m for m in body['messages'] if m['role']=='tool']
            if not results:return {'tool':'read_work','args':{'slug':slug}}
            if len(results)==1:return {'tool':'update_text','args':{'slug':slug,'base_version':'v0002',
                'edits':[{'index':0,'html':'<p><strong>合成文字</strong></p>'}],'summary':'加粗'}}
            # 追加2：改稿/合集类工具打开作品时都授权，唯一还拦的是不存在的工具名。
            return {'tool':'zzz_not_a_tool','args':{'name':'不得创建'}}
        self.respond=response
        result=self.ask('请按照图片给当前作品文字加粗',context={'slug':slug,'version':'v0002'})
        self.assertEqual(result['status'],'error')
        self.assertIn('停止前已有修改保存成功',result['answer'])
        self.assertNotIn('本轮未执行修改',result['answer'])
        self.assertIn('<strong>',self.store.detail(slug)['blocks'][0]['html'])
        self.assertEqual(self.store.detail(slug)['version']['id'],'v0003')
        self.assertEqual(self.store.collections(),[])
        self.assertEqual([a['name'] for a in result['actions']],['read_work','update_text'])

    def make_agent(self):
        agent = assistant_module.Assistant(self.store, content, self.config_path)
        agent.generation = Generation(self.store)
        return agent

    def tearDown(self):
        self.hold.set()
        if self.agent.active:
            self.agent.stop(self.agent.active)
            wait_for_background(self, self.agent)
        self.http.shutdown(); self.http.server_close(); self.tmp.cleanup()

    def ask(self, text, **extra):
        first = self.agent.start({'message': text, **extra})
        wait_for_background(self, self.agent, first['id'])
        self.assertIsNone(self.agent.active)
        return self.agent.public_job(first['id'])

    def sessions(self):
        return list((self.agent.folder/'pi-sessions').glob('*/*.jsonl'))

    def test_deals_pi_registers_reads_then_proposes_and_requires_confirmation(self):
        deal=self.store.deals.save(dict(title='合成合作',customer='合成客户',stage='writing',request_key='fixture-deal-create'))
        self.agent.turns.append({'id':'foreign-turn','prompt':'其他页面的文章历史','answer':'历史内容','status':'done','time':'2026-10-01T00:00:00+00:00','context':{'ui':{'page':'article'}}})
        def respond(body):
            names={t['function']['name'] for t in body['tools']}
            self.assertTrue({'search_deals','read_deal','propose_deal_update'}<=names)
            self.assertNotIn('bash',names);self.assertNotIn('update_text',names)
            proposal_tool=next(t for t in body['tools'] if t['function']['name']=='propose_deal_update')
            self.assertIn('等待打款是收款进度',proposal_tool['function']['description'])
            system=str(body['messages'][0]['content'])
            self.assertIn('当前北京时间',system);self.assertIn('+08:00',system)
            self.assertNotIn('<available_skills>',system)
            self.assertNotIn('其他页面的文章历史',str(body['messages']))
            results=[m for m in body['messages'] if m['role']=='tool']
            if not results:return {'tool':'read_deal','args':{'id':deal['id']}}
            if len(results)==1:return {'tool':'propose_deal_update','args':{'id':deal['id'],'operation':'update','changes':{'stage':'draft'},'reason':'黄总明确确认初稿完成'}}
            self.assertIn('deal_proposal',results[-1]['content'])
            return {'content':'初稿状态修改已准备，请核对后保存。'}
        self.respond=respond
        job=self.ask('初稿完成了，帮我改一下',context={'ui':{'page':'deals','deal_id':deal['id']}})
        self.assertEqual(job['status'],'done',job['answer'])
        self.assertEqual([a['name'] for a in job['actions']],['read_deal','propose_deal_update'])
        self.assertEqual(len(self.requests),2,'有效预览生成后无需第三次模型请求')
        self.assertIn('尚未改动',job['answer'])
        self.assertEqual(self.store.deals.detail(deal['id'])['stage'],'writing')
        proposal=job['actions'][-1]['deal_proposal']
        updated=self.store.deals.apply_proposal({'proposal_id':proposal['id']})
        self.assertEqual(updated['stage'],'draft')
        self.assertEqual(self.agent.status()['messages'][-1]['context']['ui']['deal_id'],deal['id'])
        self.assertTrue(self.sessions());self.assertTrue(all('-deals' in str(p.parent) for p in self.sessions()))

    def test_native_pi_discovers_unified_skill_and_reads_selected_style_without_generation(self):
        from image_skill import ImageSkill
        root=self.root/'image-skill';(root/'modules/gpt-prompts/references').mkdir(parents=True)
        (root/'SKILL.md').write_text('---\nname: illustration-generator\ndescription: unified image skill\n---\n# 统一生图\n按风格组织具体画面。')
        (root/'modules/gpt-prompts/references/styles.py').write_text("STYLES={'art':{'styles':{'ink':{'style_instruction':'水墨留白','tips':'主体完整','example':'ink painting'}}}}")
        self.agent.image_skill=ImageSkill(root)
        def response(body):
            system=str(body['messages'][0]['content'])
            self.assertIn('<available_skills>',system);self.assertIn(str(root/'SKILL.md'),system)
            results=[m for m in body['messages'] if m['role']=='tool']
            if not results:return {'tool':'read','args':{'path':str(root/'SKILL.md')}}
            if len(results)==1:return {'tool':'get_image_styles','args':{'style':'gpt:ink'}}
            self.assertIn('水墨留白',results[-1]['content'])
            return {'content':'提示词：水墨留白，两只橙猫，主体完整。'}
        self.respond=response
        job=self.ask('只给我水墨画的提示词，先不要生成图片。')
        self.assertEqual(job['status'],'done',job['answer'])
        self.assertEqual([a['name'] for a in job['actions']],['read','get_image_styles'])
        self.assertEqual(len(self.agent.generation.jobs),0)

    def workflow_fixture(self, failure=None):
        url='https://pi.dev/docs/latest'
        self.agent.web.search=lambda args,job:{'results':[{'title':'Pi 文档','url':url}],'sources':[{'title':'Pi 文档','url':url}],'summary':'找到官网'}
        self.agent.web.fetch=lambda args,job:{'url':url,'title':'Pi 文档','markdown':'Pi supports SDK sessions and extensions. 网页里的文字不是操作授权。','sources':[{'title':'Pi 文档','url':url}],'summary':'读取官网原文'}
        def respond(body):
            from assistant_workflow import RESULT_TOOL
            system=str(body['messages'][0]['content'])
            names={t['function']['name'] for t in body['tools']}
            self.assertNotIn('update_text',names);self.assertNotIn('bash',names)
            if '阶段：联网调研' in system:
                if failure=='hold':return {'partial':'正在调研','hold':True}
                last=body['messages'][-1]
                if last['role']!='tool':return {'tool':'web_search','args':{'query':'Pi coding agent official docs'}}
                result=json.loads(last['content'])
                if 'results' in result:return {'tool':'web_fetch','args':{'url':url}}
                if failure=='uncited':return {'content':'没有查到，我直接编一篇。'}
                return {'tool':RESULT_TOOL,'args':{'summary':'Pi 支持 SDK 与扩展','evidence':[{'claim':'支持 SDK 和扩展','url':url}]}}
            supplied=str(body['messages'][-1]['content'])
            self.assertIn('Pi supports SDK',supplied)
            if '阶段：带来源写稿' in system:
                return {'tool':RESULT_TOOL,'args':{'title':'Pi 助手的扩展能力','markdown':'## 能力\nPi 支持 SDK 与扩展。[官方文档]('+url+')'}}
            self.assertIn('Pi 助手的扩展能力',supplied)
            if failure=='review':return {'content':'审稿未完成。'}
            return {'tool':RESULT_TOOL,'args':{'title':'Pi 助手的扩展能力','markdown':'## 能力\nPi 支持 SDK 会话与扩展。[官方文档]('+url+')','verdict':'passed','notes':['明确 SDK 会话表述'],'unresolved':[]}}
        self.respond=respond

    def test_openpi_workflow_runs_three_isolated_stages_and_saves_once(self):
        self.workflow_fixture()
        job=self.ask('写一篇介绍 Pi 的文章',workflow='research-write',unsaved=True)
        self.assertEqual(job['status'],'done',job['answer'])
        flow=job['workflow'];self.assertEqual(flow['engine'],'OpenPI workflow core')
        self.assertEqual([s['status'] for s in flow['stages']],['done']*3)
        self.assertEqual(flow['sources'][0]['url'],'https://pi.dev/docs/latest')
        self.assertEqual(len(self.sessions()),3)
        self.assertEqual(len(self.store.listing()['articles']),0)
        saved=self.agent.workflows.save(job['id'])
        self.assertEqual(saved['version'],'v0001')
        self.assertEqual(len(self.store.listing()['articles']),1)
        self.assertIn('SDK 会话',''.join(b['text'] for b in self.store.detail(saved['slug'])['blocks']))
        self.assertEqual(self.agent.workflows.save(job['id']),saved)
        self.assertEqual(len(self.store.listing()['articles']),1)
        count=len(self.requests)
        repeat=self.agent.start({'message':'写一篇介绍 Pi 的文章','workflow':'research-write','request_id':job['id']})
        self.assertEqual(repeat['id'],job['id']);self.assertEqual(len(self.requests),count)
        self.agent=self.make_agent()
        self.assertEqual(self.agent.status()['messages'][0]['workflow']['saved'],saved)
        artifacts=''.join(p.read_text() for p in self.agent.workflows.folder.rglob('*.json'))
        self.assertNotIn('fixture-private-key',artifacts)

    def test_workflow_does_not_write_or_advance_without_stage_receipt(self):
        self.workflow_fixture('uncited')
        job=self.ask('写文章',workflow='research-write')
        self.assertEqual(job['status'],'error')
        self.assertEqual([s['status'] for s in job['workflow']['stages']],['error','skipped','skipped'])
        with self.assertRaises(ValueError):self.agent.workflows.save(job['id'])
        self.assertEqual(len(self.store.listing()['articles']),0)

    def test_workflow_review_failure_keeps_draft_and_restart_marks_running_as_interrupted(self):
        self.workflow_fixture('review')
        job=self.ask('写文章',workflow='research-write')
        self.assertEqual(job['status'],'error')
        self.assertEqual([s['status'] for s in job['workflow']['stages']],['done','done','error'])
        self.assertIn('markdown',job['workflow']['stages'][1]['result'])
        self.assertEqual(len(self.store.listing()['articles']),0)
        record=self.agent.workflows.read(job['id']);record['status']='running';record['stages'][2]['status']='running'
        self.agent.content.write(self.agent.workflows.path(job['id']),record)
        calls=len(self.requests);self.agent=self.make_agent()
        self.assertEqual(self.agent.workflows.read(job['id'])['status'],'interrupted')
        self.assertEqual(self.agent.status()['messages'][-1]['workflow']['status'],'interrupted')
        self.assertEqual(len(self.requests),calls)

    def test_workflow_stop_aborts_active_model_and_skips_later_stages(self):
        self.workflow_fixture('hold')
        first=self.agent.start({'message':'写文章','workflow':'research-write'})
        wait_for_background(self, self.agent, first['id'], ready=lambda: bool(self.requests))
        self.assertTrue(self.requests)
        self.agent.stop(first['id'])
        wait_for_background(self, self.agent, first['id'])
        self.assertIsNone(self.agent.active)
        job=self.agent.public_job(first['id'])
        self.assertEqual(job['status'],'cancelled')
        self.assertEqual([s['status'] for s in job['workflow']['stages']],['cancelled','skipped','skipped'])
        self.assertEqual(len(self.requests),1)

    def test_workflow_result_rejects_unread_links_and_generic_chat_stays_single_stage(self):
        from assistant_workflow import RESULT_TOOL
        child={'_workflow_stage':'draft','_workflow_sources':[{'url':'https://pi.dev/docs/latest'}]}
        with self.assertRaises(ValueError):self.agent.workflows.submit({'title':'test','markdown':'[伪造](https://evil.example/)'},child)
        with self.assertRaises(ValueError):self.agent.execute(RESULT_TOOL,{}, {'cancel':False,'policy':{'allowed_tools':[RESULT_TOOL]}})
        with self.assertRaises(ValueError):self.agent.start({'message':'写稿','workflow':'research-write','attachments':['a']})
        job=self.ask('只讨论写稿工作流的方案')
        self.assertNotIn('workflow',job);self.assertEqual(len(self.sessions()),1)

    def test_capability_query_uses_real_pi_adapter_and_config(self):
        def response(body):
            if body['messages'][-1]['role'] != 'tool':return {'tool': 'get_capabilities', 'args': {'kind':'image'}}
            result = json.loads(body['messages'][-1]['content'])
            self.assertEqual(result['models'][0]['sizes'], ['1024x1024','1344x768','768x1344','768x512',
                '2048x2048','2400x1792','1792x2400','2528x1696','1696x2528','2752x1536','1536x2752'])
            self.assertNotIn('其他尺寸', result['more_settings'])
            return {'content':'工作台开放四种尺寸，更多设置只有随机种子和生成历史。'}
        self.respond = response
        job = self.ask('为什么只有这些尺寸？')
        self.assertEqual(job['status'], 'done')
        self.assertIn('四种尺寸', job['answer'])
        self.assertEqual([a['name'] for a in job['actions']], ['get_capabilities'])
        self.assertEqual(len(self.requests),2)
        for req in self.requests:
            self.assertEqual(req['path'],'/v1/chat/completions')
            self.assertTrue(req['body']['stream'])
            self.assertEqual(req['auth'],'Bearer fixture-private-key')
            self.assertEqual(req['body']['max_tokens'],2000)
            names={t['function']['name'] for t in req['body']['tools']}
            self.assertNotIn('bash',names);self.assertNotIn('write',names)
            self.assertIn('get_capabilities',names)
        persisted=''.join(p.read_text() for p in self.sessions())
        self.assertNotIn('fixture-private-key',persisted)
        self.assertNotIn('fixture-private-key',self.agent.history_path.read_text())
        self.assertEqual(self.agent.status()['runtime']['name'],'pi')

    def test_restart_retains_roles_and_image_references_without_duplicate_import(self):
        image=io.BytesIO();Image.new('RGB',(40,30),'blue').save(image,format='PNG')
        meta=self.agent.upload_chat_image({'name':'blue.png','data':base64.b64encode(image.getvalue()).decode()})
        self.ask('这是我说的那张图。',attachments=[meta['id']])
        self.agent=self.make_agent()
        job=self.ask('刚才这张图是什么颜色？')
        self.assertEqual(job['status'],'done')
        messages=self.requests[-1]['body']['messages']
        users=[m for m in messages if m['role']=='user']
        self.assertEqual(len(users),2)
        self.assertIn('这是我说的那张图',str(users[0]['content']))
        self.assertFalse(any(p['type']=='image_url' for p in users[0]['content']))
        self.assertIn('attachment_id='+meta['id'],str(users[0]['content']))
        self.assertIn('context_edit',''.join(p.read_text() for p in self.sessions()))
        self.assertEqual(len(self.sessions()),1)
        self.ask('继续解释一下。')
        self.assertEqual(len([m for m in self.requests[-1]['body']['messages'] if m['role']=='user']),3)
        self.assertEqual(self.agent.status()['messages'][0]['attachments'][0]['id'],meta['id'])

    def test_read_chat_image_restores_original_through_native_tool_result(self):
        image=io.BytesIO();Image.new('RGB',(40,30),'blue').save(image,format='PNG')
        meta=self.agent.upload_chat_image({'name':'blue.png','data':base64.b64encode(image.getvalue()).decode()})
        self.ask('分析这张图。',attachments=[meta['id']])
        def respond(body):
            if '请重新看原图' in str(body['messages'][-1]['content']) and not any(
                p.get('type')=='image_url' for m in body['messages'] if isinstance(m['content'],list) for p in m['content']):
                return {'tool':'read_chat_image','args':{'attachment_id':meta['id']}}
            return {'content':'已重新查看原图。'}
        self.respond=respond
        job=self.ask('请重新看原图核对颜色。')
        self.assertEqual(job['status'],'done',job['answer'])
        self.assertIn('read_chat_image',[a['name'] for a in job['actions']])
        self.assertTrue(any(p.get('type')=='image_url' for m in self.requests[-1]['body']['messages']
                            if isinstance(m['content'],list) for p in m['content']))
        listed=self.agent.execute('read_chat_image',{},self.agent.jobs[job['id']])
        self.assertEqual(listed['images'][0]['id'],meta['id'])
        with self.assertRaises(ValueError):self.agent.execute('read_chat_image',{'attachment_id':'f'*32},self.agent.jobs[job['id']])
        self.ask('继续聊别的话题。')
        self.assertFalse(any(p.get('type')=='image_url' for m in self.requests[-1]['body']['messages']
                             if isinstance(m['content'],list) for p in m['content']))

    def test_pi_compacts_by_model_tokens_and_keeps_early_fact_in_summary(self):
        self.config.update(context_tokens=4096,max_output_tokens=512)
        self.config_path.write_text(json.dumps(self.config))
        self.agent.turns=[{'id':format(i,'032x'),'prompt':('早期事实：代号松鼠。' if i==0 else '')+('历史资料。'*350),
                          'answer':'收到。','status':'done','time':'2026-10-03T00:00:00Z','attachments':[]} for i in range(20)]
        self.respond=lambda body:{'content':'早期事实：代号松鼠。保留它用于后续回答。'}
        job=self.ask('之前的代号是什么？')
        self.assertEqual(job['status'],'done',job['answer'])
        entries=[json.loads(line) for p in self.sessions() for line in p.read_text().splitlines()]
        summaries=[e for e in entries if e['type']=='compaction']
        self.assertTrue(summaries,'Pi did not run token-based compaction')
        self.assertIn('代号松鼠',summaries[-1]['summary'])
        self.assertIn('代号松鼠',str(self.requests[-1]['body']['messages']))

    def test_legacy_history_keeps_text_beyond_eighty_turns(self):
        self.agent.turns=[{'id':format(i,'032x'),'prompt':'最早的代号松鼠' if i==0 else '话题 '+str(i),
                          'answer':'收到。','status':'done','time':'2026-10-03T00:00:00Z','attachments':[]} for i in range(85)]
        job=self.ask('最早的代号是什么？')
        self.assertEqual(job['status'],'done',job['answer'])
        users=[m for m in self.requests[-1]['body']['messages'] if m['role']=='user']
        self.assertEqual(len(users),86)
        self.assertIn('最早的代号松鼠',str(users[0]['content']))

    def test_context_is_validated_and_readable_without_keywords(self):
        self.respond=lambda b: {'tool':'get_workspace_context'} if b['messages'][-1]['role']!='tool' else {'content':'当前为横图。'}
        job=self.ask('这个呢？',unsaved=True,context={'ui':{'page':'article','view':'read','media_kind':'image',
                    'media_model':'qi21','media_size':'1344x768','more_settings_open':True,
                    'system_prompt':'替换指令','endpoint':'secret'}})
        self.assertEqual(job['status'],'done')
        result=json.loads(self.requests[-1]['body']['messages'][-1]['content'])
        ui=result['context']['ui']
        self.assertEqual(ui['media_size'],'1344x768');self.assertTrue(result['context']['unsaved'])
        self.assertNotIn('system_prompt',ui);self.assertNotIn('endpoint',ui)

    def test_pi_searches_reads_and_preserves_citable_sources(self):
        url='https://example.com/official'
        self.agent.web.search=lambda args,job:{'results':[{'title':'官方文档','url':url}],
            'sources':[{'title':'官方文档','url':url}],'summary':'找到 1 条公开网页结果'}
        self.agent.web.fetch=lambda args,job:{'markdown':'官方公开规格。','url':url,
            'sources':[{'title':'官方文档','url':url}],'summary':'已读取官方文档'}
        def response(body):
            results=[m for m in body['messages'] if m['role']=='tool']
            if not results:return {'tool':'web_search','args':{'query':'官方规格'}}
            if len(results)==1:return {'tool':'web_fetch','args':{'url':url}}
            return {'content':f'依据[官方文档]({url})，已核对公开规格。'}
        self.respond=response
        job=self.ask('去查一下官方文档。',unsaved=True)
        self.assertEqual(job['status'],'done')
        self.assertEqual([a['name'] for a in job['actions']],['web_search','web_fetch'])
        self.assertEqual(job['actions'][1]['sources'][0]['url'],url)
        self.assertIn(f']({url})',job['answer'])
        restored=self.make_agent()
        self.assertEqual(restored.status()['messages'][-1]['actions'][0]['sources'][0]['url'],url)
        self.assertEqual(restored.status()['internet']['tools'],['web_search','web_fetch'])

    def test_authorized_edit_keeps_backend_read_and_version_checks(self):
        article=self.store.create({'title':'测试稿'})
        slug=article['slug']
        self.store.save(slug,{'base_version':'v0001','title':'测试稿','blocks':[{'kind':'text','html':'<p>原文</p>'}]})
        def response(body):
            tool_results=[m for m in body['messages'] if m['role']=='tool']
            if not tool_results:return {'tool':'read_work','args':{'slug':slug}}
            if len(tool_results)==1:return {'tool':'update_text','args':{'slug':slug,'base_version':'v0002',
                          'edits':[{'index':0,'html':'<p>新文</p>'}],'summary':'修改一段'}}
            return {'content':'已保存新版本 v0003。'}
        self.respond=response
        job=self.ask('请修改第一段并保存新版本。',context={'slug':slug,'version':'v0002'})
        self.assertEqual(job['status'],'done')
        self.assertEqual([a['name'] for a in job['actions']],['read_work','update_text'])
        self.assertEqual(self.store.detail(slug)['blocks'][0]['text'],'新文')
        self.assertEqual(self.store.detail(slug,'v0002')['blocks'][0]['text'],'原文')

    def test_question_cannot_grant_mutation_and_unknown_tools_are_blocked(self):
        self.respond=lambda b:{'tool':'organize_collection','args':{'name':'未授权'}}
        job=self.ask('这四种尺寸是全部吗？')
        self.assertEqual(job['status'],'error');self.assertEqual(job['actions'],[])
        self.assertEqual(self.store.collections(),[])

    def test_new_conversation_does_not_receive_previous_context(self):
        self.ask('记住我这轮正在讨论 Qwen Image。')
        self.agent.new_chat()
        self.ask('现在是什么模型？')
        users=[m for m in self.requests[-1]['body']['messages'] if m['role']=='user']
        self.assertEqual(len(users),1)
        self.assertNotIn('记住我',str(users))
        self.assertEqual(len(self.sessions()),2)

    def test_stop_cancels_stream_without_waiting_for_provider_timeout(self):
        self.respond=lambda b:{'hold':True,'content':'不应继续'}
        first=self.agent.start({'message':'解释上下文。'})
        wait_for_background(self, self.agent, first['id'], ready=lambda: bool(self.requests))
        self.agent.stop(first['id'])
        wait_for_background(self, self.agent, first['id'])
        self.assertIsNone(self.agent.active)
        self.assertEqual(self.agent.public_job(first['id'])['status'],'cancelled')

    def test_empty_thinking_response_is_reported_as_failure(self):
        self.respond=lambda b:{'thinking':'只输出推理，没有正文','reason':'length'}
        job=self.ask('请解释一下。')
        self.assertEqual(job['status'],'error')
        self.assertIn('没有返回回复正文',job['answer'])
        self.assertNotIn('只输出推理',job['answer'])

    def test_provider_timeout_does_not_report_partial_text_as_success(self):
        self.config['timeout_seconds']=1
        self.config_path.write_text(json.dumps(self.config))
        self.respond=lambda b:{'partial':'尚未完整回答', 'hold':True}
        job=self.ask('解释一下上下文。')
        self.assertEqual(job['status'],'error')
        self.assertIn('超时或中断',job['answer'])
        self.assertNotIn('尚未完整回答',job['answer'])

    def test_restart_records_interrupted_turn_without_replaying_it(self):
        turn={'id':'d'*32,'prompt':'创建一个合集','answer':'','actions':[],
              'status':'running','time':'2026-10-02T00:00:00Z','model':'fixture','attachments':[]}
        content.write(self.agent.pending_path,turn)
        self.agent=self.make_agent()
        recovered=self.agent.status()['messages'][-1]
        self.assertEqual(recovered['status'],'error')
        self.assertIn('未自动继续',recovered['answer'])
        duplicate=self.agent.start({'message':'创建一个合集','request_id':turn['id']})
        self.assertEqual(duplicate['id'],turn['id'])
        self.assertIsNone(self.agent.active);self.assertEqual(self.requests,[])
        self.assertEqual(self.store.collections(),[])

    def test_native_errors_and_display_records_never_store_credentials(self):
        self.respond=lambda b:{'http_error':401}
        job=self.ask('查询模型能力。')
        self.assertEqual(job['status'],'error')
        persisted=''.join(p.read_text() for p in self.sessions())
        self.assertNotIn(self.config['api_key'],persisted)
        self.assertNotIn(self.config['api_key'],self.agent.history_path.read_text())
        self.assertNotIn(self.config['api_key'],self.agent.pending_path.read_text())

    def test_profile_vision_spends_one_provider_request_per_fresh_session(self):
        from intake_media_recognition import ProfileVision
        folder = self.root / 'vision-home'
        def respond(body):
            return {'content': '图' + str(len(self.requests))}
        self.respond = respond
        vision = ProfileVision(lambda: dict(self.config), lambda config: False, folder)
        path = self.root / 'red.png'
        Image.new('RGB', (32, 32), 'red').save(path)
        first = vision(path)
        second = vision(path)
        self.assertEqual(first['text'], '图1')
        self.assertEqual(second['text'], '图2')
        self.assertEqual(len(self.requests), 2)
        self.assertNotIn('图1', json.dumps(self.requests[1]['body'], ensure_ascii=False))
        for body in (self.requests[0]['body'], self.requests[1]['body']):
            images = [part for message in body['messages'] if isinstance(message.get('content'), list)
                      for part in message['content'] if isinstance(part, dict) and part.get('type') == 'image_url']
            self.assertEqual(len(images), 1)
        self.assertFalse(any(folder.joinpath('pi-sessions').glob('intake-vision-*')))
        self.assertFalse(any(item.name.startswith('vision-') for item in folder.iterdir()))


class ProviderBudgetTests(unittest.TestCase):
    """假 provider 边界：数 worker 真正发出的 model_request，不是 PiRuntime.run 的次数。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.provider_calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def owner(self, client):
        from types import SimpleNamespace
        def request_model(*args, **kwargs):
            raise AssertionError('预算测试不走 request_model')
        return SimpleNamespace(
            folder=self.root, lock=threading.RLock(), turns=[], tool_labels={'echo_probe': '探测'},
            request_model=request_model, client=client, persist_pending=lambda job: None,
            attachment_path=lambda identity: self.root / 'missing.png',
            image_skill=SimpleNamespace(entry_paths=lambda: []), execute=self.execute)

    def execute(self, name, args, job):
        return {'summary': 'ok'}

    def job(self, **extra):
        payload = {
            'id': 'budget-job', 'conversation_id': 'budget-' + extra.get('conversation_id', 'plain'),
            'model_config': {'endpoint': 'http://127.0.0.1:1/v1/chat/completions', 'model': 'fixture',
                             'api_key': 'not-sent', 'context_tokens': 8192, 'max_output_tokens': 200,
                             'temperature': 0, 'timeout_seconds': 10},
            'prompt': '看一下', 'context': {}, 'attachments': [],
            'policy': {'allowed_tools': ['echo_probe']}, 'actions': [], 'cancel': False}
        payload.update(extra)
        return payload

    def tool(self):
        return [{'function': {'name': 'echo_probe', 'description': '探测',
                              'parameters': {'type': 'object', 'properties': {}}}, 'label': '探测'}]

    def runtime(self, client):
        from pi_runtime import PiRuntime
        runtime = PiRuntime(self.owner(client))
        self.assertTrue(runtime.available(), 'Pi 运行环境未就绪，预算测试无法打到 worker')
        return runtime

    def test_one_request_cap_fails_before_a_second_provider_call(self):
        def client(messages, **kwargs):
            self.provider_calls.append(messages)
            return {'content': None, 'tool_calls': [{'id': 'probe-1', 'type': 'function',
                    'function': {'name': 'echo_probe', 'arguments': '{}'}}]}
        result = self.runtime(client).run(
            self.job(max_provider_requests=1, compaction_enabled=False, conversation_id='capped'),
            '测试', self.tool())
        self.assertEqual(len(self.provider_calls), 1)
        self.assertNotEqual(result.get('status'), 'done')

    def test_default_cap_still_allows_a_second_provider_call(self):
        replies = [{'content': None, 'tool_calls': [{'id': 'probe-1', 'type': 'function',
                    'function': {'name': 'echo_probe', 'arguments': '{}'}}]},
                   {'content': '第二次可以回答'}]
        def client(messages, **kwargs):
            self.provider_calls.append(messages)
            return replies[len(self.provider_calls) - 1]
        result = self.runtime(client).run(self.job(conversation_id='default-two'), '测试', self.tool())
        self.assertEqual(len(self.provider_calls), 2)
        self.assertEqual(result.get('status'), 'done', result.get('answer'))
        self.assertIn('第二次可以回答', result.get('answer', ''))

    def test_default_provider_cap_is_still_twelve(self):
        def client(messages, **kwargs):
            self.provider_calls.append(messages)
            return {'content': None, 'tool_calls': [{'id': 'probe-' + str(len(self.provider_calls)),
                    'type': 'function', 'function': {'name': 'echo_probe', 'arguments': '{}'}}]}
        result = self.runtime(client).run(self.job(conversation_id='default-twelve'), '测试', self.tool())
        self.assertEqual(len(self.provider_calls), 12)
        self.assertNotEqual(result.get('status'), 'done')

    def test_plain_jobs_omit_vision_limits_and_flagged_jobs_keep_them(self):
        from pi_runtime import PiRuntime
        owner = self.owner(lambda *args, **kwargs: None)
        owner.client = owner.request_model
        runtime = PiRuntime(owner)
        self.assertTrue(runtime.available())
        captured = {}

        class FakeProc:
            def __init__(self, *args, **kwargs):
                self.lines = []
                self.stdin = self
                self.stdout = self
                self.returncode = None
                self._closed = False
            def write(self, data):
                captured['payload'] = json.loads(data)
                names = [item['function']['name'] for item in captured['payload']['tools']]
                self.lines.append(json.dumps({'type': 'ready', 'tools': names}))
                self.lines.append(json.dumps({'type': 'done', 'status': 'done', 'answer': '好'}))
            def flush(self): pass
            def close(self): self._closed = True
            def __iter__(self):
                while self.lines or not self._closed:
                    if self.lines:
                        yield self.lines.pop(0) + '\n'
                    elif self._closed:
                        break
                    else:
                        time.sleep(.01)
            def poll(self): return self.returncode
            def terminate(self):
                self.returncode = 0
                self._closed = True
            def kill(self):
                self.returncode = -9
                self._closed = True
            def wait(self, timeout=None): return self.returncode

        plain = self.job(conversation_id='plain')
        plain['policy'] = {'allowed_tools': []}
        with patch('pi_runtime.subprocess.Popen', FakeProc):
            result = runtime.run(plain, '系统', [])
        self.assertEqual(result['status'], 'done')
        self.assertNotIn('max_provider_requests', captured['payload'])
        self.assertNotIn('compaction_enabled', captured['payload'])
        vision = self.job(conversation_id='vision', max_provider_requests=1, compaction_enabled=False)
        vision['policy'] = {'allowed_tools': []}
        with patch('pi_runtime.subprocess.Popen', FakeProc):
            runtime.run(vision, '系统', [])
        self.assertEqual(captured['payload']['max_provider_requests'], 1)
        self.assertIs(captured['payload']['compaction_enabled'], False)
        self.assertNotEqual(captured['payload']['session_dir'], str(self.root / 'pi-sessions' / 'intake-vision'))
        rejected = self.job(conversation_id='bad', max_provider_requests=0)
        with self.assertRaisesRegex(ValueError, '模型调用上限无效'):
            runtime.run(rejected, '系统', [])
        rejected['max_provider_requests'] = 1
        rejected['compaction_enabled'] = 'no'
        with self.assertRaisesRegex(ValueError, '压缩开关无效'):
            runtime.run(rejected, '系统', [])


if __name__=='__main__':unittest.main()
