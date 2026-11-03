"""M1/M2 regressions. Only synthetic credentials; failure messages stay fixed."""
import io
from pathlib import Path
import tempfile
import threading
import traceback
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from weknora_client import WeKnoraConfig, WeKnoraClient, WeKnoraError, resource_url
from test_weknora_client import CONFIG, Response

PUBLIC='https://leaselitetest.huoyanrent.com/weknora/api/v1'
# Deliberately shaped like a credential; not derived from a real configuration.
PREFIX='sk-weknora-test-0123456789abcdef'
KEY=PREFIX+'-test-only'
BAD=KEY+'中文'


def configured(base=PUBLIC, key=KEY):
    return WeKnoraConfig(base,key,'10000','kb-one')


def safe_exception(test, factory, expected):
    error=None
    try:
        factory()
    except Exception as exc:
        error=exc
    # Assert outside the except block so a test failure cannot chain the raw
    # credential-bearing exception into unittest's own failure traceback.
    if error is None:
        test.fail('Expected an exception')
    test.assertTrue(isinstance(error, expected), 'Wrong sanitized exception type')
    forms=(str(error),repr(error),''.join(traceback.format_exception(type(error),error,error.__traceback__)))
    test.assertTrue(all(PREFIX not in value and KEY not in value and BAD not in value for value in forms),
                    'Credential present in exception presentation')
    return error


class PrefixTests(unittest.TestCase):
    def test_wire_request_preserves_prefix_once(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                received.append(self.path)
                self.send_response(200);self.end_headers()
                self.wfile.write(b'{"success":true,"data":[]}')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for suffix in ['/api/v1','/weknora/api/v1','/weknora/api/v1/']:
                client=WeKnoraClient(configured(f'http://127.0.0.1:{server.server_port}'+suffix))
                client.list_knowledge()
            self.assertEqual(received,[
                '/api/v1/knowledge-bases/kb-one/knowledge?page=1&page_size=100',
                '/weknora/api/v1/knowledge-bases/kb-one/knowledge?page=1&page_size=100',
                '/weknora/api/v1/knowledge-bases/kb-one/knowledge?page=1&page_size=100'])
        finally:server.shutdown();server.server_close();worker.join()

    def test_public_loopback_and_trailing_slash_request_paths(self):
        cases=[(PUBLIC,'/weknora/api/v1'),
               ('http://127.0.0.1:18580/api/v1','/api/v1'),
               (PUBLIC+'/','/weknora/api/v1'),
               ('http://127.0.0.1:18580/api/v1/','/api/v1')]
        for base,expected in cases:
            with self.subTest(base=base):
                client=WeKnoraClient(configured(base))
                with patch.object(client._opener,'open',return_value=Response()) as opened:
                    client.list_knowledge()
                    req=opened.call_args.args[0]
                    self.assertEqual(urlsplit(req.full_url).path,expected+'/knowledge-bases/kb-one/knowledge')
                    self.assertEqual(urlsplit(req.full_url).query,'page=1&page_size=100')
                    self.assertEqual(urlsplit(req.full_url).path.count('/weknora'),expected.count('/weknora'))
    def test_multi_segment_prefix_is_preserved(self):
        client=WeKnoraClient(configured('https://example.org/team/app/api/v1'))
        with patch.object(client._opener,'open',return_value=Response()) as opened:
            client.get_knowledge('doc')
            self.assertEqual(urlsplit(opened.call_args.args[0].full_url).path,'/team/app/api/v1/knowledge/doc')
    def test_existing_origin_restrictions_apply_with_prefix(self):
        bad=['http://example.org/weknora/api/v1',
             'https://user:pass@example.org/weknora/api/v1',
             'https://example.org/weknora/api/v1?x=y',
             'https://example.org/weknora/api/v1#frag',
             'https://example.org/weknora/api/v1?',
             'https://example.org/weknora/api/v1#',
             'https://example.org/../weknora/api/v1',
             'https://example.org/weknora//api/v1',
             'https://example.org/weknora/api/v2',
             'https://example.org/weknora/api/v1\n']
        for index,base in enumerate(bad):
            with self.subTest(case=index),self.assertRaises(ValueError):configured(base)
    def test_loopback_http_can_have_prefix(self):
        self.assertEqual(configured('http://127.0.0.1:18580/weknora/api/v1').tenant_id,'10000')


class ResourceURLTests(unittest.TestCase):
    def test_relative_resource_uses_origin_and_deployment_prefix(self):
        self.assertEqual(resource_url(configured(),'/r/x'),
                         'https://leaselitetest.huoyanrent.com/weknora/r/x')
    def test_root_deployment_uses_origin_without_extra_prefix(self):
        self.assertEqual(resource_url(CONFIG,'/r/x'),'http://127.0.0.1:18580/r/x')
    def test_trailing_slash_and_query_are_preserved(self):
        self.assertEqual(resource_url(configured(PUBLIC+'/'),'/r/x?download=1#part'),
                         'https://leaselitetest.huoyanrent.com/weknora/r/x?download=1#part')
    def test_complete_urls_are_unchanged(self):
        for url in ['https://leaselitetest.huoyanrent.com/weknora/r/x','https://cdn.example.org/r/x','http://example.org/r/x']:
            self.assertEqual(resource_url(configured(),url),url)
    def test_unrelated_or_already_prefixed_values_are_unchanged(self):
        for value in ['/weknora/r/x','/other/x','//example.org/r/x','r/x',None,'']:
            self.assertEqual(resource_url(configured(),value),value)
    def test_nested_prefix_is_preserved(self):
        self.assertEqual(resource_url(configured('https://example.org/team/app/api/v1'),'/r/x'),
                         'https://example.org/team/app/r/x')


class CredentialBoundaryTests(unittest.TestCase):
    def test_unicode_suffix_rejected_without_any_key_presentation(self):
        exc=safe_exception(self,lambda:configured(key=BAD),ValueError)
        self.assertEqual(str(exc),'Invalid WEKNORA_API_KEY')
    def test_control_characters_and_whitespace_rejected_safely(self):
        for code in list(range(33))+[127,128,159]:
            with self.subTest(code=code):
                exc=safe_exception(self,lambda:configured(key=KEY+chr(code)),ValueError)
                self.assertEqual(str(exc),'Invalid WEKNORA_API_KEY')
    def test_nonstring_or_empty_key_rejected_with_fixed_message(self):
        for case in [None,b'not-a-string',123,'']:
            exc=safe_exception(self,lambda:configured(key=case),ValueError)
            self.assertEqual(str(exc),'Invalid WEKNORA_API_KEY')
    def test_env_file_unicode_key_is_rejected_at_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'.env.local'
            path.write_text('WEKNORA_BASE_URL='+PUBLIC+'\nWEKNORA_API_KEY='+BAD+
                            '\nWEKNORA_TENANT_ID=10000\nWEKNORA_KB_ID=kb-one\n')
            safe_exception(self,lambda:WeKnoraClient.from_env_file(path),ValueError)
    def test_request_constructor_encoding_error_is_sanitized(self):
        client=WeKnoraClient(configured())
        raw=UnicodeEncodeError('ascii',BAD,0,len(BAD),'test encoding failure')
        with patch('weknora_client.Request',side_effect=raw):
            exc=safe_exception(self,client.list_tags,WeKnoraError)
        self.assertEqual(str(exc),'WeKnora request encoding failed')
        self.assertTrue(exc.__suppress_context__);self.assertIsNone(exc.__cause__)
    def test_send_encoding_error_is_sanitized(self):
        client=WeKnoraClient(configured())
        raw=UnicodeEncodeError('latin-1',BAD,0,len(BAD),'test header encoding failure')
        with patch.object(client._opener,'open',side_effect=raw):
            exc=safe_exception(self,client.list_tags,WeKnoraError)
        self.assertEqual(str(exc),'WeKnora request encoding failed')
        self.assertTrue(exc.__suppress_context__);self.assertIsNone(exc.__cause__)
    def test_body_encoding_error_is_sanitized(self):
        client=WeKnoraClient(configured())
        exc=safe_exception(self,lambda:client.create_manual('title',KEY+'\ud800'),WeKnoraError)
        self.assertEqual(str(exc),'WeKnora request encoding failed')


class CommonErrorRegressionTests(unittest.TestCase):
    def probe(self,error,expected_status=None):
        client=WeKnoraClient(configured())
        with patch.object(client._opener,'open',side_effect=error) as opened:
            exc=safe_exception(self,client.list_tags,WeKnoraError)
        self.assertEqual(exc.status,expected_status)
        self.assertEqual(opened.call_count,1)
        self.assertTrue(exc.__suppress_context__);self.assertIsNone(exc.__cause__)
    def http(self,status):
        self.probe(HTTPError('https://example.org',status,KEY,{},io.BytesIO(KEY.encode())),status)
    def test_http_401(self):self.http(401)
    def test_http_409(self):self.http(409)
    def test_http_404(self):self.http(404)
    def test_http_500(self):self.http(500)
    def test_timeout(self):self.probe(TimeoutError(KEY))
    def test_connection_refused(self):self.probe(ConnectionRefusedError(KEY))
    def test_connection_reset(self):self.probe(ConnectionResetError(KEY))


if __name__=='__main__':unittest.main()
