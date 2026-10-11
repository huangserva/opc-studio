import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from assistant_web import WebTools,public_url
from assistant_policy import policy_for


class WebToolsTests(unittest.TestCase):
    def setUp(self):
        self.web=WebTools();self.job={'cancel':False,'model_config':{'api_key':'private-model-key'}}

    def test_web_reads_are_allowed_without_granting_mutations(self):
        policy=policy_for('为什么不去查一下官网？')
        self.assertIn('web_search',policy['allowed_tools']);self.assertIn('web_fetch',policy['allowed_tools'])
        self.assertNotIn('update_text',policy['allowed_tools']);self.assertNotIn('generate_media',policy['allowed_tools'])

    def test_rejects_local_files_private_addresses_and_credentials(self):
        for url in ('file:///etc/passwd','http://127.0.0.1','http://[::1]','http://10.1.2.3','http://localhost',
                    'https://name:secret@example.com','https://docs.local/a','https://example.com:8766/a'):
            with self.subTest(url=url),self.assertRaises(ValueError):public_url(url)
        with patch('assistant_web.socket.getaddrinfo',return_value=[(socket.AF_INET,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):public_url('https://public.example.com',resolve=True)

    def test_search_scopes_domain_normalizes_actual_results_and_bounds_text(self):
        data={'count':5,'results':[{'title':'Official docs','url':'https://example.com/docs','description':'x'*4000},
                                 {'title':'unrelated','url':'https://other.com/docs','description':'not official'},
                                 {'title':'private','url':'http://127.0.0.1','description':'hidden'}]}
        with patch.object(self.web,'call',return_value=data) as call:
            result=self.web.search({'query':'Pi search','domain':'example.com','count':5},self.job)
        self.assertEqual(call.call_args.args[0],['web_search','--query','Pi search site:example.com','--count','5','--region','us'])
        self.assertEqual(len(result['results']),1);self.assertEqual(len(result['results'][0]['description']),1000)
        self.assertEqual(result['sources'][0]['url'],'https://example.com/docs')
        self.assertIn('找到 1 条',result['summary'])

    def test_fetch_pagination_and_actual_source_are_returned(self):
        data={'url':'https://example.com/docs','title':'Docs','markdown':'0123456789'*300}
        with patch('assistant_web.socket.getaddrinfo',return_value=[(socket.AF_INET,1,6,'',('93.184.216.34',443))]),patch.object(self.web,'call',return_value=data):
            result=self.web.fetch({'url':data['url'],'start':1000,'max_chars':1000},self.job)
        self.assertEqual(result['markdown'],data['markdown'][1000:2000]);self.assertEqual(result['next_start'],2000)
        self.assertTrue(result['truncated']);self.assertEqual(result['sources'][0]['title'],'Docs')

    def test_model_key_is_not_sent_to_search_or_page_fetch(self):
        with patch.object(self.web,'call') as call:
            with self.assertRaises(ValueError):self.web.search({'query':'search private-model-key'},self.job)
            call.assert_not_called()

    def test_missing_service_and_provider_errors_are_explicit_without_leaking_details(self):
        self.web.command=None
        with self.assertRaisesRegex(ValueError,'联网服务未就绪'):self.web.call(['web_search'],self.job)
        with tempfile.TemporaryDirectory() as d:
            script=Path(d)/'remio-fixture'
            script.write_text('#!/usr/bin/env python3\nimport json\nprint(json.dumps({"ok":False,"error":"secret provider-token https://private-endpoint/"}))\n')
            script.chmod(0o700);self.web.command=str(script)
            with self.assertRaises(ValueError) as error:self.web.call(['web_search'],self.job)
            self.assertNotIn('provider-token',str(error.exception));self.assertNotIn('private-endpoint',str(error.exception))

    def test_stop_interrupts_running_cli_instead_of_waiting_for_service(self):
        with tempfile.TemporaryDirectory() as d:
            script=Path(d)/'remio-fixture'
            script.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(20)\n');script.chmod(0o700)
            self.web.command=str(script);errors=[]
            def run():
                try:self.web.call(['web_search'],self.job)
                except ValueError as error:errors.append(str(error))
            thread=threading.Thread(target=run);thread.start();time.sleep(.15)
            start=time.monotonic();self.job['cancel']=True;thread.join(2)
            self.assertFalse(thread.is_alive());self.assertLess(time.monotonic()-start,2)
            self.assertEqual(errors,['任务已停止'])

    def test_timeout_and_per_turn_limit_are_enforced(self):
        self.job['web_calls']=8
        # hygiene-4：次数上限检查在 available() 之后，干净克隆里 self.web.command 为 None，
        # 会先报「联网服务未就绪」而够不到「上限」那条分支。这是前置条件没给好，不是外部依赖，
        # 所以按同文件既有写法先摆一个假命令（临时目录脚本 + chmod 0o700），断言内容不变。
        with tempfile.TemporaryDirectory() as d:
            script=Path(d)/'remio-fixture'
            script.write_text('#!/usr/bin/env python3\nimport json\nprint(json.dumps({"ok":True,"result":"x"}))\n')
            script.chmod(0o700);self.web.command=str(script)
            with self.assertRaisesRegex(ValueError,'上限'):self.web.call(['web_search'],self.job)
        self.job['web_calls']=0
        with tempfile.TemporaryDirectory() as d:
            script=Path(d)/'remio-fixture'
            script.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(20)\n');script.chmod(0o700)
            self.web.command=str(script);self.web.timeout=.1
            with self.assertRaisesRegex(ValueError,'响应超时'):self.web.call(['web_search'],self.job)


if __name__=='__main__':unittest.main()
