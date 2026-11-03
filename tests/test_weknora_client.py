import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from weknora_client import WeKnoraClient, WeKnoraConfig, WeKnoraError

FAKE='unit-test-credential-only'
CONFIG=WeKnoraConfig('http://127.0.0.1:18580/api/v1',FAKE,'10000','kb-one')

class Response:
    status=200
    def __init__(self, raw=b'{"success":true,"data":{}}'):self.raw=raw
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return self.raw

class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client=WeKnoraClient(CONFIG)
        patcher=patch.object(self.client._opener,'open',return_value=Response())
        self.mock=patcher.start()
        self.addCleanup(patcher.stop)
    def request(self):
        req=self.mock.call_args.args[0]
        return req, json.loads(req.data) if req.data else None
    def test_all_operations_have_both_headers(self):
        calls=[lambda:self.client.list_tags(),lambda:self.client.create_tag('知识'),
               lambda:self.client.create_manual('title','body',['one']),
               lambda:self.client.update_manual('knowledge','title','whole'),
               lambda:self.client.replace_tags('knowledge',['one']),
               lambda:self.client.get_knowledge('knowledge'),
               lambda:self.client.list_knowledge(),lambda:self.client.hybrid_search('query'),
               lambda:self.client.delete_knowledge('knowledge')]
        for call in calls:
            with self.subTest(call=call):
                call();req,_=self.request()
                self.assertEqual(req.get_header('X-api-key'),FAKE)
                self.assertEqual(req.get_header('X-tenant-id'),'10000')
    def test_manual_update_publishes_full_content_without_tags(self):
        body='# heading\n\n<script>原文</script>\n结束'
        self.client.update_manual('doc','title',body)
        req,payload=self.request()
        self.assertEqual(req.method,'PUT');self.assertTrue(req.full_url.endswith('/knowledge/manual/doc'))
        self.assertEqual(payload['content'],body);self.assertEqual(payload['status'],'publish')
        self.assertFalse(payload['process_config']['summary_enabled']);self.assertNotIn('tag_ids',payload)
    def test_create_publishes_and_tags(self):
        self.client.create_manual('title','body',['a','b','a'],kb_id='testkb')
        req,payload=self.request();self.assertEqual(payload['tag_ids'],['a','b'])
        self.assertEqual(payload['channel'],'api');self.assertEqual(payload['status'],'publish')
        self.assertIn('/testkb/knowledge/manual',req.full_url)
    def test_replace_is_whole_set_including_empty(self):
        for tags in [['x'],[]]:
            self.client.replace_tags('doc',tags,'testkb');req,payload=self.request()
            self.assertEqual(req.method,'PUT');self.assertTrue(req.full_url.endswith('/knowledge/tags'))
            self.assertEqual(payload,{'kb_id':'testkb','knowledge_ids':['doc'],'tag_ids':tags})
    def test_search_uses_actual_v09_fields_and_both_engines(self):
        self.client.hybrid_search('query',knowledge_ids=['doc'],tag_ids=['tag'],match_count=3)
        _,payload=self.request();self.assertEqual(payload['query_text'],'query')
        self.assertEqual(payload['knowledge_ids'],['doc']);self.assertEqual(payload['match_count'],3)
        self.assertFalse(payload['disable_keywords_match']);self.assertFalse(payload['disable_vector_match'])
    def test_not_found_never_creates(self):
        self.mock.side_effect=HTTPError('url',404,'bad',{},io.BytesIO(b'not found'))
        with self.assertRaises(WeKnoraError) as cm:self.client.update_manual('gone','title','body')
        self.assertEqual(cm.exception.status,404);self.assertEqual(self.mock.call_count,1)
    def test_transport_errors_do_not_expose_server_body_or_credentials(self):
        for code in [302,409,500]:
            self.mock.side_effect=HTTPError('url',code,FAKE,{},io.BytesIO(FAKE.encode()))
            with self.assertRaises(WeKnoraError) as cm:self.client.list_tags()
            self.assertNotIn(FAKE,str(cm.exception));self.assertEqual(cm.exception.status,code)
        self.mock.side_effect=URLError(FAKE)
        with self.assertRaises(WeKnoraError) as cm:self.client.list_tags()
        self.assertNotIn(FAKE,str(cm.exception))
    def test_non_json_or_rejected_responses_fail_safely(self):
        for value in [FAKE.encode(),b'[]',b'{"success":false}']:
            self.mock.return_value=Response(value)
            with self.assertRaises(WeKnoraError) as cm:self.client.list_tags()
            self.assertNotIn(FAKE,str(cm.exception))
    def test_identifiers_cannot_inject_path_or_query(self):
        for value in ['', '../knowledge', 'a?x=y', 'a/b', 'a%2Fb', 'x'*129]:
            with self.subTest(value=value),self.assertRaises(ValueError):self.client.get_knowledge(value)
        self.mock.assert_not_called()
    def test_invalid_pagination_and_search_rejected_before_request(self):
        for kwargs in [dict(page=0),dict(page_size=51),dict(page=True),dict(page_size='3')]:
            with self.assertRaises(ValueError):self.client.list_tags(**kwargs)
        for kwargs in [dict(query=''),dict(query='q',match_count=31),dict(query='q',knowledge_ids=['a']*21)]:
            with self.assertRaises(ValueError):self.client.hybrid_search(**kwargs)
        self.mock.assert_not_called()
    def test_empty_content_and_invalid_tag_type_rejected(self):
        for title,content in [('', 'body'),('title',''),('title',None),('title','x'*200001)]:
            with self.assertRaises(ValueError):self.client.create_manual(title,content)
        with self.assertRaises(ValueError):self.client.replace_tags('doc','tag')
        self.mock.assert_not_called()
    def test_config_repr_redacts_key(self):
        self.assertNotIn(FAKE,repr(CONFIG));self.assertNotIn(FAKE,repr(self.client))

class ConfigTests(unittest.TestCase):
    def parse(self,text):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'.env.local';p.write_text(text);return WeKnoraConfig.from_env_file(p)
    def text(self):
        return 'OTHER=ignored\nWEKNORA_BASE_URL=http://127.0.0.1:18580/api/v1\nWEKNORA_API_KEY="'+FAKE+'" # note\nexport WEKNORA_TENANT_ID=10000\nWEKNORA_KB_ID=kb-one\n'
    def test_only_named_variables_parsed_with_quotes(self):
        c=self.parse(self.text());self.assertEqual(c,CONFIG)
    def test_missing_duplicate_or_malformed_config(self):
        for text in [self.text().replace('WEKNORA_TENANT_ID=10000',''),self.text()+'WEKNORA_KB_ID=two\n',self.text().replace('" # note',' # note')]:
            with self.assertRaises(ValueError) as cm:self.parse(text)
            self.assertNotIn(FAKE,str(cm.exception))
    def test_configuration_blocks_unsafe_origin(self):
        for url in ['http://example.com/api/v1','http://user:pass@127.0.0.1/api/v1','http://127.0.0.1/api/v1?key=x','http://127.0.0.1/not-api','file:///api/v1']:
            with self.assertRaises(ValueError):WeKnoraConfig(url,FAKE,'10000','kb')
    def test_no_environment_interpolation(self):
        c=self.parse(self.text().replace(FAKE,'${OTHER}'));self.assertEqual(c.api_key,'${OTHER}')

class RealLoopbackTests(unittest.TestCase):
    def test_redirect_does_not_forward_credentials(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302);self.send_header('Location','/redirect-target');self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            config=WeKnoraConfig(f'http://127.0.0.1:{server.server_port}/api/v1',FAKE,'10000','kb')
            with self.assertRaises(WeKnoraError) as cm:WeKnoraClient(config).list_tags()
            self.assertEqual(cm.exception.status,302);self.assertEqual(len(seen),1)
        finally:server.shutdown();server.server_close();worker.join()

if __name__=='__main__':unittest.main()
