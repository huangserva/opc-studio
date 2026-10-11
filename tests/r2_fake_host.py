"""Shared fake image host for the R2 tests: a real local HTTP server, no real bucket.

Not named test_* so the unittest loader ignores it; test modules load it by file path.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PUBLIC_HOST = 'media.example.com'


class FakeR2(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _key(self):
        if self.command == 'GET':
            return self.path.lstrip('/')
        return self.path.split('/', 2)[2] if self.path.count('/') >= 2 else self.path.lstrip('/')

    def _record(self):
        self.server.requests.append({'method': self.command, 'path': self.path,
                                     'headers': {k.lower(): v for k, v in self.headers.items()}})

    def _blocked_agent(self):
        """Opt-in behaviour copied from the real CDN: the default Python agent gets 403."""
        return getattr(self.server, 'block_python_ua', False) and \
            (self.headers.get('User-Agent') or '').startswith('Python-urllib')

    def _refuse_agent(self):
        payload = b'<html><body>error code: 1010</body></html>'
        self.send_response(403)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_PUT(self):
        self._record()
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        key = self._key()
        if key in self.server.fail_put:
            payload = ('<Error><Message>Access Denied; authorization='
                       + (self.headers.get('Authorization') or '') + '</Message></Error>').encode()
            self.send_response(403)
            self.send_header('Content-Type', 'application/xml')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.server.objects[key] = body
        self.send_response(200)
        self.send_header('ETag', '"etag"')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_HEAD(self):
        self._record()
        if self._blocked_agent():
            self.send_response(403)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        key = self._key()
        if key in self.server.objects:
            self.send_response(200)
            self.send_header('Content-Length', str(len(self.server.objects[key])))
        else:
            self.send_response(404)
            self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        self._record()
        if self._blocked_agent():
            self._refuse_agent()
            return
        key = self._key()
        if key in self.server.html:
            payload = b'<html>not an image</html>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
        elif key in self.server.objects:
            payload = self.server.objects[key]
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
        else:
            payload = b'gone'
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_fake_host():
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeR2)
    server.objects, server.requests, server.fail_put, server.html = {}, [], set(), set()
    server.block_python_ua = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def public_to_local(server):
    """Map the public CDN host onto the local fake host; everything else is untouched."""
    from urllib.request import urlopen as real_urlopen
    port = server.server_address[1]

    def call(request, timeout=60):
        parts = urlsplit(request.full_url)
        if parts.netloc == PUBLIC_HOST:
            request.full_url = 'http://127.0.0.1:%d%s' % (port, parts.path)
        return real_urlopen(request, timeout=timeout)
    return call


def methods(server, kind):
    return [item for item in server.requests if item['method'] == kind]
