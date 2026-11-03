"""Recursive exception-chain regression tests. Synthetic credentials only."""
import io
import threading
import traceback
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from weknora_client import WeKnoraClient, WeKnoraConfig, WeKnoraError
from test_weknora_client import Response

PREFIX='sk-chain-test-0123456789abcdef'
KEY=PREFIX+'-synthetic-only'
BAD=KEY+'中文'


def config(base='http://127.0.0.1:18580/api/v1'):
    return WeKnoraConfig(base,KEY,'10000','kb-one')


def snapshot(operation):
    """Return only booleans/status; never expose an exception to unittest."""
    try:
        operation()
    except Exception as error:
        stack=[error];seen=set();safe=True
        while stack:
            node=stack.pop()
            if id(node) in seen:continue
            seen.add(id(node))
            forms=[str(node),repr(node),repr(node.args),repr(vars(node)),
                   ''.join(traceback.format_exception(type(node),node,node.__traceback__))]
            safe=safe and all(PREFIX not in value and KEY not in value and BAD not in value for value in forms)
            for next_node in [node.__cause__,node.__context__]:
                if next_node is not None:stack.append(next_node)
        frames_clean=True
        tb=error.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_name=='_request':
                # The transport frame must not retain request/header objects,
                # raw response/JSON bodies, or the client/config reference.
                fields=tb.tb_frame.f_locals
                frames_clean=frames_clean and not any(name in fields for name in
                    ['request','body','raw','payload','query','self','url','result'])
            tb=tb.tb_next
        return {'raised':True,'type_ok':isinstance(error,WeKnoraError),
                'safe':safe,'cause_none':error.__cause__ is None,
                'context_none':error.__context__ is None,
                'status':getattr(error,'status',None),'transport_frames_clean':frames_clean}
    return {'raised':False}


class ChainTests(unittest.TestCase):
    def check(self,operation,status=None):
        self.check_result(snapshot(operation),status)

    def check_result(self,result,status=None):
        # Assertions are outside the catch and contain no raw exception/value.
        self.assertTrue(result.get('raised',False),'Expected sanitized failure')
        self.assertTrue(result.get('type_ok',False),'Unexpected exception type')
        self.assertTrue(result.get('safe',False),'Exception chain contains synthetic credential')
        self.assertTrue(result.get('cause_none',False),'Cause is still attached')
        self.assertTrue(result.get('context_none',False),'Context is still attached')
        self.assertTrue(result.get('transport_frames_clean',False),'Transport traceback retains request references')
        self.assertTrue(result.get('status')==status,'Incorrect sanitized HTTP status')

    def test_encoding_request_constructor_chain(self):
        client=WeKnoraClient(config())
        raw=UnicodeEncodeError('ascii',BAD,0,len(BAD),'synthetic encoding fault')
        with patch('weknora_client.Request',side_effect=raw):self.check(client.list_tags)

    def test_encoding_opener_send_chain(self):
        client=WeKnoraClient(config())
        raw=UnicodeEncodeError('latin-1',BAD,0,len(BAD),'synthetic header fault')
        with patch.object(client._opener,'open',side_effect=raw):self.check(client.list_tags)

    def test_encoding_real_urllib_header_chain(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                received.append(True);self.send_response(200);self.end_headers()
                self.wfile.write(b'{"success":true,"data":[]}')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            cfg=config(f'http://127.0.0.1:{server.server_port}/api/v1')
            # Deliberately bypass the first guard in a local fake object to
            # exercise the real second guard; never touch actual credentials.
            object.__setattr__(cfg,'api_key',BAD)
            self.check(WeKnoraClient(cfg).list_tags)
            self.assertTrue(len(received)==0,'Malformed header reached HTTP server')
        finally:server.shutdown();server.server_close();worker.join()

    def http(self,status):
        client=WeKnoraClient(config())
        error=HTTPError('http://127.0.0.1',status,KEY,{},io.BytesIO(KEY.encode()))
        with patch.object(client._opener,'open',side_effect=error):self.check(client.list_tags,status)
    def test_http_401_chain(self):self.http(401)
    def test_http_409_chain(self):self.http(409)
    def test_http_404_chain(self):self.http(404)
    def test_http_500_chain(self):self.http(500)

    def network(self,error):
        client=WeKnoraClient(config())
        with patch.object(client._opener,'open',side_effect=error):self.check(client.list_tags)
    def test_timeout_chain(self):
        self.network(TimeoutError(KEY));self.network(URLError(TimeoutError(KEY)))
    def test_refused_connection_chain(self):
        self.network(ConnectionRefusedError(KEY));self.network(URLError(ConnectionRefusedError(KEY)))
    def test_reset_connection_chain(self):
        self.network(ConnectionResetError(KEY));self.network(URLError(ConnectionResetError(KEY)))

    def test_nested_raw_cause_and_context_are_removed(self):
        error=ConnectionResetError('synthetic network fault')
        error.__cause__=RuntimeError(KEY)
        error.__context__=ValueError(KEY)
        self.network(error)

    def test_body_encoding_chain(self):
        client=WeKnoraClient(config())
        self.check(lambda:client.create_manual('title',KEY+'\ud800'))

    def test_invalid_json_chain(self):
        client=WeKnoraClient(config())
        with patch.object(client._opener,'open',return_value=Response(KEY.encode())):
            self.check(client.list_tags,200)

    def test_invalid_utf8_json_chain(self):
        client=WeKnoraClient(config())
        with patch.object(client._opener,'open',return_value=Response(KEY.encode()+b'\xff')):
            self.check(client.list_tags,200)

    def test_rejected_response_drops_body_references(self):
        client=WeKnoraClient(config())
        import json
        body=json.dumps({'success':False,'message':KEY}).encode()
        with patch.object(client._opener,'open',return_value=Response(body)):
            self.check(client.list_tags,200)

    def test_response_reference_is_released_on_failure(self):
        import json
        cases=[('invalid-json',KEY.encode()),
               ('rejected-response',json.dumps({'success':False,'message':KEY}).encode())]
        for name,raw in cases:
            with self.subTest(case=name):
                client=WeKnoraClient(config());found=False;retained=False
                with patch.object(client._opener,'open',return_value=Response(raw)):
                    try:client.list_tags()
                    except WeKnoraError as error:
                        tb=error.__traceback__
                        while tb is not None:
                            if tb.tb_frame.f_code.co_name=='_request':
                                found=True
                                retained=retained or 'response' in tb.tb_frame.f_locals
                            tb=tb.tb_next
                self.assertTrue(found,'Expected a transport failure frame')
                self.assertTrue(not retained,'Transport traceback retains response reference')

    def test_ambient_caller_exception_is_not_attached(self):
        client=WeKnoraClient(config())
        with patch.object(client._opener,'open',side_effect=ConnectionResetError(KEY)):
            try:
                raise RuntimeError(KEY)
            except RuntimeError:
                result=snapshot(client.list_tags)
            # Do not attach the ambient fake error to a test assertion either.
            self.check_result(result)


class EmptyUserinfoTests(unittest.TestCase):
    def test_empty_username_is_rejected(self):
        rejected=False
        try:config('https://@example.org/weknora/api/v1')
        except ValueError:rejected=True
        self.assertTrue(rejected,'Empty userinfo must be rejected')
    def test_empty_username_and_password_are_rejected(self):
        rejected=False
        try:config('https://:@example.org/weknora/api/v1')
        except ValueError:rejected=True
        self.assertTrue(rejected,'Empty userinfo delimiters must be rejected')


if __name__=='__main__':unittest.main()
