import json
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parent))
from _network_guard import NetworkGuard
import linuxdo_source as source
import today_view as today

NOW = datetime(2026, 10, 30, 8, tzinfo=timezone.utc)
DESCRIPTION = '<p>完整正文 &amp; 内容</p><script>window.PWNED=1</script><img src=x onerror="alert(1)"><a href="javascript:alert(1)">危险链接</a><p>8 个帖子 - 3 位参与者</p>'


def rss(description=DESCRIPTION):
    return ('''<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>热门</title>
    <item><title>linux.do AI 主题</title><dc:creator>测试作者</dc:creator><category>开发调优</category><link>https://linux.do/t/topic/123</link><pubDate>Fri, 30 Oct 2026 07:00:00 GMT</pubDate><description><![CDATA[''' + description + ''']]></description></item>
    <item><title>重复链接</title><link>https://linux.do/t/topic/123</link><pubDate>Fri, 30 Oct 2026 07:00:00 GMT</pubDate><description>重复</description></item></channel></rss>''').encode()


class LinuxdoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='linuxdo-unit-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.guard = NetworkGuard()
        self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)

    def seed(self):
        with patch.object(source, 'fetch_feeds', return_value=(source.parse_rss(rss()), [])):
            source.refresh(self.root, NOW)
        return (source.directory(self.root) / 'top-daily.json').read_bytes()

    def test_mapping_dedup_and_safe_plain_text(self):
        rows = source.parse_rss(rss())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['sources'], ['L'])
        self.assertEqual(row['author']['name'], '测试作者')
        self.assertEqual(row['topic'], '开发调优')
        self.assertEqual(row['metrics'], dict(views=None, replies=7))
        self.assertEqual(row['published_at'], '2026-10-30T07:00:00+00:00')
        self.assertIn('完整正文 & 内容', row['full_text'])
        for bad in ('<script', 'window.PWNED', 'onerror=', 'javascript:'):
            self.assertNotIn(bad, row['full_text'])
        from items_store import canonical_id
        self.assertEqual(canonical_id(row), row['id'])

    def test_no_reply_count_guessed_and_one_post_has_zero_replies(self):
        self.assertIsNone(source.parse_rss(rss('<p>没有统计</p>'))[0]['metrics']['replies'])
        self.assertEqual(source.parse_rss(rss('1 个帖子 - 1 位参与者'))[0]['metrics']['replies'], 0)

    def test_reply_count_uses_footer_and_preserves_body_post_count(self):
        text = '以前积累了 100 个帖子，这次分享一下。'
        row = source.parse_rss(rss(f'<p>{text}</p><p>8 个帖子 - 3 位参与者</p><p><a href="https://linux.do/t/topic/123">阅读完整话题</a></p>'))[0]
        self.assertEqual(row['metrics']['replies'], 7)
        self.assertEqual(row['full_text'], text)

    def test_body_post_count_without_footer_is_unknown(self):
        text = '以前积累了 100 个帖子，这次分享一下。'
        row = source.parse_rss(rss(f'<p>{text}</p><p>阅读完整话题</p>'))[0]
        self.assertIsNone(row['metrics']['replies'])
        self.assertEqual(row['full_text'], text)

    def test_one_post_footer_is_removed_and_has_zero_replies(self):
        row = source.parse_rss(rss('<p>完整正文</p><p>1 个帖子 - 1 位参与者</p><p>阅读完整话题</p>'))[0]
        self.assertEqual(row['metrics']['replies'], 0)
        self.assertEqual(row['full_text'], '完整正文')

    def test_normal_footer_with_whitespace_is_removed(self):
        row = source.parse_rss(rss('<p>完整正文 &amp; 内容</p><p>\n<small>28 个帖子 - 12 位参与者</small>\n</p><p>阅读完整话题</p>'))[0]
        self.assertEqual(row['metrics']['replies'], 27)
        self.assertEqual(row['full_text'], '完整正文 & 内容')

    def test_only_last_footer_is_used_and_earlier_statistics_are_preserved(self):
        text = '旧榜统计：\n100 个帖子 - 20 位参与者\n这次分享一下。'
        row = source.parse_rss(rss(f'<p>{text}</p><p>8 个帖子 - 3 位参与者</p>'))[0]
        self.assertEqual(row['metrics']['replies'], 7)
        self.assertEqual(row['full_text'], text)

    def test_statistics_in_body_or_sentence_are_not_a_footer(self):
        for text in ('100 个帖子 - 20 位参与者\n后面仍有正文。',
                     '以前积累了 100 个帖子 - 20 位参与者',
                     '以前积累了 100 个帖子'):
            with self.subTest(text=text):
                row = source.parse_rss(rss(f'<p>{text}</p>'))[0]
                self.assertIsNone(row['metrics']['replies'])
                self.assertEqual(row['full_text'], text)

    def test_transport_has_no_credentials_and_hard_limits(self):
        from types import SimpleNamespace
        with patch.object(source, 'getproxies', return_value={'https':'http://127.0.0.1:7897'}), patch.object(source.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=rss()+b'\n200')) as run:
            self.assertEqual(len(source.fetch_rss()), 1)
            args = run.call_args.args[0]
            self.assertEqual(args[:2], ['curl','-q'])
            self.assertEqual(args[-1], source.RSS_URL)
            for flag,value in [('--max-time','9.5'),('--max-filesize',str(source.MAX_BYTES)),('--proto','=https'),('--max-redirs','0'),('--proxy','http://127.0.0.1:7897')]:
                self.assertEqual(args[args.index(flag)+1], value)
            self.assertNotIn('--location',args)
            self.assertNotIn('--cookie',args)
            self.assertNotIn('--insecure',args)
            self.assertEqual(run.call_args.kwargs['timeout'],10)
            self.assertEqual(run.call_args.kwargs['stderr'], subprocess.DEVNULL)
            self.assertFalse({'http_proxy','https_proxy','all_proxy','no_proxy'} & {k.lower() for k in run.call_args.kwargs['env']})
        with patch.object(source, 'getproxies', return_value={'https':'http://user:secret@127.0.0.1:7897'}), patch.object(source.subprocess,'run') as run:
            with self.assertRaises(ValueError):source.fetch_rss()
            run.assert_not_called()

    def test_transport_oversize_timeout_http_error_and_redirect(self):
        from types import SimpleNamespace
        for code,output,error in [(63,b'\n200',ValueError),(0,b'x'*(source.MAX_BYTES+1)+b'\n200',ValueError),(28,b'\n000',TimeoutError),(22,b'\n403',HTTPError),(0,b'\n302',ValueError),(0,b'<html>challenge</html>\n200',ValueError)]:
            with patch.object(source, 'getproxies', return_value={}), patch.object(source.subprocess, 'run', return_value=SimpleNamespace(returncode=code,stdout=output)) as run:
                with self.assertRaises(error):source.fetch_rss()
                self.assertIn('--noproxy', run.call_args.args[0])
        with patch.object(source.subprocess, 'run', side_effect=subprocess.TimeoutExpired('curl',10)):
            with self.assertRaises(TimeoutError):source.fetch_rss()

    def test_real_transport_local_tls_enforces_size_timeout_and_error_handling(self):
        import ssl
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        cert, key = self.root/'fixture-cert.pem', self.root/'fixture-key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-keyout',str(key),'-out',str(cert),'-subj','/CN=127.0.0.1',
                        '-addext','subjectAltName=IP:127.0.0.1'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        gate = threading.Event()
        paths = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def do_GET(self):
                paths.append(self.path)
                self.send_response(403 if self.path=='/challenge' else 200)
                if self.path=='/length':self.send_header('Content-Length',str(source.MAX_BYTES+1))
                self.end_headers()
                try:
                    if self.path in ('/oversize','/length'):self.wfile.write(b'x'*(source.MAX_BYTES+65536))
                    elif self.path=='/timeout':self.wfile.flush();gate.wait(3)
                    elif self.path=='/invalid':self.wfile.write(b'<rss><channel>')
                    elif self.path=='/challenge':self.wfile.write(b'<html>Cloudflare challenge</html>')
                    else:self.wfile.write(rss())
                except (BrokenPipeError,ConnectionResetError,ssl.SSLError):pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key)
        server.socket=tls.wrap_socket(server.socket,server_side=True)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with patch.object(source,'getproxies',return_value={}), patch.dict(source.os.environ,{'CURL_CA_BUNDLE':str(cert)}):
                for path,error in [('/oversize',ValueError),('/length',ValueError),('/challenge',HTTPError),('/invalid',source.ET.ParseError)]:
                    with patch.object(source,'RSS_URL',f'https://127.0.0.1:{server.server_port}'+path):
                        with self.assertRaises(error):source.fetch_rss()
                with patch.object(source,'RSS_URL',f'https://127.0.0.1:{server.server_port}/timeout'), patch.object(source,'TIMEOUT',.3):
                    started=time.monotonic()
                    with self.assertRaises(TimeoutError):source.fetch_rss()
                    self.assertLess(time.monotonic()-started,1)
                with patch.object(source,'RSS_URL',f'https://127.0.0.1:{server.server_port}/ok'):
                    self.assertEqual(len(source.fetch_rss()),1)
            self.assertEqual(paths,['/oversize','/length','/challenge','/invalid','/timeout','/ok'])
        finally:
            gate.set();server.shutdown();server.server_close();worker.join(2)

    def test_failures_keep_snapshot_and_show_source_error_without_secrets(self):
        old = self.seed()
        errors = [TimeoutError('Cookie=secret API_KEY=secret'), HTTPError(source.RSS_URL,403,'Cloudflare',{},None), ValueError('secret')]
        for i, exc in enumerate(errors, 1):
            with patch.object(source, 'fetch_feeds', side_effect=exc):
                source.refresh(self.root, NOW + timedelta(hours=i))
            rows, state = source.snapshot(self.root)
            self.assertEqual(len(rows), 1)
            self.assertTrue(state['error'])
            self.assertEqual((source.directory(self.root) / 'top-daily.json').read_bytes(), old)
            for path in source.directory(self.root).glob('*.json'):
                self.assertNotIn('secret', path.read_text())
                self.assertNotIn('Cookie', path.read_text())

    def test_challenge_malformed_xml_and_entities_rejected(self):
        for body in (b'<html>Just a moment Cloudflare challenge</html>', b'<rss><channel>', b'<!DOCTYPE rss [<!ENTITY x "bad">]><rss><channel/></rss>'):
            with self.assertRaises((ValueError, source.ET.ParseError)):
                source.parse_rss(body)
            old = self.seed()
            with patch.object(source, 'fetch_feeds', side_effect=lambda: (source.parse_rss(body), [])):
                source.refresh(self.root, NOW + timedelta(hours=2))
            self.assertTrue(source.snapshot(self.root)[1]['error'])
            self.assertEqual((source.directory(self.root) / 'top-daily.json').read_bytes(), old)
            # 每轮复位，不让上一轮小时节流掩盖错误。
            (source.directory(self.root) / 'status.json').unlink()

    def test_failed_refresh_retries_after_ten_minutes_success_waits_hour(self):
        with patch.object(source, 'fetch_feeds', side_effect=TimeoutError) as fetch:
            source.refresh(self.root, NOW)
            source.refresh(self.root, NOW + timedelta(minutes=9, seconds=59))
            self.assertEqual(fetch.call_count, 1)
            source.refresh(self.root, NOW + timedelta(minutes=10))
            self.assertEqual(fetch.call_count, 2)
        with patch.object(source, 'fetch_feeds', return_value=(source.parse_rss(rss()), [])) as fetch:
            source.refresh(self.root, NOW + timedelta(minutes=20))
            source.refresh(self.root, NOW + timedelta(minutes=79, seconds=59))
            self.assertEqual(fetch.call_count, 1)
            source.refresh(self.root, NOW + timedelta(minutes=80))
            self.assertEqual(fetch.call_count, 2)

    def test_same_hour_success_and_atomic_cache(self):
        with patch.object(source, 'fetch_feeds', return_value=(source.parse_rss(rss()), [])) as fetch:
            source.refresh(self.root, NOW)
            source.refresh(self.root, NOW + timedelta(seconds=1))
            self.assertEqual(fetch.call_count, 1)
        self.assertFalse(list(source.directory(self.root).glob('*.tmp')))

    def test_background_eight_second_fetch_does_not_block_snapshot(self):
        entered, done = threading.Event(), threading.Event()
        def slow():
            entered.set()
            time.sleep(8)
            done.set()
            return source.parse_rss(rss()), []
        with patch.object(source, 'fetch_feeds', side_effect=slow) as fetch:
            started = time.monotonic()
            source.refresh_background(self.root)
            rows, state = source.snapshot(self.root)
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(state['refreshing'])
            self.assertEqual(rows, [])
            self.assertTrue(entered.wait(5), repr((source.refresh_background, threading.Thread, source._PENDING, source.snapshot(self.root))))
            source.refresh_background(self.root)
            self.assertTrue(done.wait(9))
            deadline = time.monotonic() + 1
            while source.snapshot(self.root)[1]['refreshing'] and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(len(source.snapshot(self.root)[0]), 1)

    def test_cached_linuxdo_today_feedback_keep_and_full_text(self):
        self.seed()
        with patch.object(source, 'refresh_background'), patch.object(today.grok, 'radar_view', side_effect=ValueError), patch.object(today.video, 'page_view', return_value={'status':'missing','lists':{}}):
            result = today.page_view(self.root, {'range':['7d'],'source':['L']}, NOW)
            self.assertEqual(result['total'], 1)
            item = result['items'][0]
            self.assertFalse(item['hot'])
            self.assertEqual(today.page_view(self.root, {'range':['7d'],'source':['L'],'tier':['火爆']}, NOW)['total'], 0)
            for action in ('accurate','inaccurate','keep','use','clear'):
                today.save_feedback(self.root, {'id':item['id'], 'action':action})
            from items_store import ItemsStore
            self.assertEqual(ItemsStore(self.root).detail(item['id'])['full_text'], source.parse_rss(rss())[0]['full_text'])


if __name__ == '__main__':
    unittest.main()
