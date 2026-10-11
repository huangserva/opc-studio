"""R2/SigV4 transport tests: signing, object keys, idempotency, public checks, redaction.

The fake image host is a real local HTTP server on 127.0.0.1, so the request machinery
(methods, headers, status handling, HTTPError) runs for real. Only the public host name is
mapped onto that server; no real R2 bucket and no credentials are ever touched.
"""
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest
from datetime import datetime, timezone

from publishing.transports import r2_media

SECRET = 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY'
KEY_ID = 'AKIDEXAMPLE'
PUBLIC_HOST = 'media.example.com'


def load_host_fixture():
    spec = importlib.util.spec_from_file_location(
        'r2_fake_host', Path(__file__).resolve().parent / 'r2_fake_host.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host_fixture = load_host_fixture()
FakeR2 = host_fixture.FakeR2
PUBLIC_HOST = host_fixture.PUBLIC_HOST
start_fake_host = host_fixture.start_fake_host
public_to_local = host_fixture.public_to_local


class R2MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = start_fake_host()

    @classmethod
    def tearDownClass(cls):
        cls.host.shutdown()
        cls.host.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.host.objects.clear()
        self.host.requests.clear()
        self.host.fail_put.clear()
        self.host.html.clear()
        self.config = self.config_file({
            'account_id': 'testaccount', 'bucket': 'media',
            'access_key_id': KEY_ID, 'secret_access_key': SECRET,
            'public_base_url': 'https://' + PUBLIC_HOST,
            'endpoint': 'http://127.0.0.1:%d' % self.host.server_address[1],
        })

    def tearDown(self):
        self.temp.cleanup()

    def config_file(self, data, mode=0o600):
        path = self.root / 'r2.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        path.chmod(mode)
        return path

    def loaded(self):
        return r2_media.load_config(self.config)

    # --- signing --------------------------------------------------------------
    def test_sigv4_matches_an_independent_reference_implementation(self):
        """Cross-check against a reference signer written straight from the SigV4 spec.

        The widely published AWS "get-vanilla" vector signs only host;x-amz-date, while
        S3/R2 requests additionally sign x-amz-content-sha256, so that vector is not
        directly comparable here. Whether real R2 accepts the signature is left to the
        acceptance tester with a real bucket.
        """
        config = self.loaded()
        when = datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc)
        path = '/media/prefix/slug/v0001/' + 'c' * 64 + '.png'
        url = 'http://127.0.0.1:9999' + path
        body = b'\x89PNG body'
        headers = r2_media.signature_headers('PUT', url, body, config, when)

        def chain(key, message):
            return hmac.new(key, message.encode('utf-8'), hashlib.sha256).digest()

        payload_hash = hashlib.sha256(body).hexdigest()
        canonical_headers = ('host:127.0.0.1:9999\n'
                             'x-amz-content-sha256:' + payload_hash + '\n'
                             'x-amz-date:20261008T093000Z\n')
        canonical = '\n'.join(['PUT', path, '', canonical_headers,
                               'host;x-amz-content-sha256;x-amz-date', payload_hash])
        scope = '20261008/auto/s3/aws4_request'
        to_sign = '\n'.join(['AWS4-HMAC-SHA256', '20261008T093000Z', scope,
                             hashlib.sha256(canonical.encode('utf-8')).hexdigest()])
        signing = chain(chain(chain(chain(('AWS4' + SECRET).encode('utf-8'), '20261008'),
                                    'auto'), 's3'), 'aws4_request')
        self.assertEqual(
            headers['Authorization'],
            'AWS4-HMAC-SHA256 Credential=' + KEY_ID + '/' + scope
            + ', SignedHeaders=host;x-amz-content-sha256;x-amz-date'
            + ', Signature=' + hmac.new(signing, to_sign.encode('utf-8'), hashlib.sha256).hexdigest())
        self.assertEqual(r2_media._canonical_uri('/media/a b/c.png'), '/media/a%20b/c.png')
        self.assertEqual(r2_media._canonical_query('b=2&a=1&a=0'), 'a=0&a=1&b=2')

    def test_signature_is_bound_to_the_payload_and_object_path(self):
        config = self.loaded()
        when = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
        first = r2_media.signature_headers('PUT', r2_media.object_url(config, 'a/one.png'), b'body-one', config, when)
        again = r2_media.signature_headers('PUT', r2_media.object_url(config, 'a/one.png'), b'body-one', config, when)
        other_body = r2_media.signature_headers('PUT', r2_media.object_url(config, 'a/one.png'), b'body-two', config, when)
        other_key = r2_media.signature_headers('PUT', r2_media.object_url(config, 'a/two.png'), b'body-one', config, when)
        self.assertEqual(first, again)
        self.assertNotEqual(first['Authorization'], other_body['Authorization'])
        self.assertNotEqual(first['Authorization'], other_key['Authorization'])
        self.assertEqual(first['X-Amz-Content-Sha256'], hashlib.sha256(b'body-one').hexdigest())
        self.assertIn('/auto/s3/aws4_request', first['Authorization'])
        self.assertIn('SignedHeaders=host;x-amz-content-sha256;x-amz-date', first['Authorization'])

    def test_put_sends_signed_headers_and_content_type(self):
        config = self.loaded()
        body = b'\x89PNG fake image bytes'
        result = r2_media.put_object('article-studio/x/s/v1/abc.png', body, 'image/png', config)
        self.assertEqual(result['status'], 200)
        self.assertEqual(self.host.objects['article-studio/x/s/v1/abc.png'], body)
        put = [item for item in self.host.requests if item['method'] == 'PUT'][-1]
        self.assertTrue(put['headers']['authorization'].startswith('AWS4-HMAC-SHA256 Credential=' + KEY_ID))
        self.assertEqual(put['headers']['x-amz-content-sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(put['headers']['content-type'], 'image/png')
        self.assertEqual(put['path'], '/media/article-studio/x/s/v1/abc.png')

    def test_upload_failure_message_hides_credentials(self):
        config = self.loaded()
        self.host.fail_put.add('x.png')
        with self.assertRaises(ValueError) as caught:
            r2_media.put_object('x.png', b'data', 'image/png', config)
        message = str(caught.exception)
        self.assertIn('图床上传失败', message)
        self.assertNotIn(SECRET, message)
        self.assertNotIn('AWS4-HMAC-SHA256', message)
        self.assertNotIn(KEY_ID, message)

    # --- object keys ----------------------------------------------------------
    def test_object_key_shape_and_rejections(self):
        digest = 'a' * 64
        self.assertEqual(r2_media.object_key('article-studio/x', 'sidrune-thread-20261008', 'v0001', digest, 'images/figure-01.png'),
                         'article-studio/x/sidrune-thread-20261008/v0001/' + digest + '.png')
        self.assertEqual(r2_media.content_type_for('a.JPG'), 'image/jpeg')
        for bad in (('p', 'a/b/../c', 'v0001', digest, 'x.png'),
                    ('p', 'slug', 'v0001', 'nope', 'x.png'),
                    ('p', 'slug', 'v0001', digest, 'x.tiff')):
            with self.assertRaises(ValueError):
                r2_media.object_key(*bad)

    def test_head_reports_size_and_missing_objects(self):
        config = self.loaded()
        self.host.objects['k/one.png'] = b'12345'
        self.assertEqual(r2_media.head_object('k/one.png', config), {'status': 200, 'size': 5})
        self.assertEqual(r2_media.head_object('k/absent.png', config), {'status': 404, 'size': None})

    # --- config ---------------------------------------------------------------
    def test_missing_config_is_none_and_bad_config_names_the_problem(self):
        self.assertIsNone(r2_media.load_config(self.root / 'absent.json'))
        path = self.config_file({'account_id': 'a', 'bucket': 'b', 'public_base_url': 'https://x.example.com'})
        with self.assertRaises(ValueError) as caught:
            r2_media.load_config(path)
        self.assertIn('access_key_id', str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_world_readable_config_is_refused(self):
        path = self.config_file({'account_id': 'a', 'bucket': 'b', 'access_key_id': KEY_ID,
                                 'secret_access_key': SECRET, 'public_base_url': 'https://x.example.com'}, mode=0o644)
        with self.assertRaises(ValueError) as caught:
            r2_media.load_config(path)
        self.assertIn('权限过宽', str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_public_base_url_must_be_https_and_endpoint_must_not_be_a_remote_http_target(self):
        for bad in ({'public_base_url': 'http://media.example.com'},
                    {'public_base_url': 'https://user:pw@media.example.com'},
                    {'public_base_url': 'https://media.example.com/cdn'},
                    {'endpoint': 'http://someone-else.example.com'}):
            data = {'account_id': 'a', 'bucket': 'b', 'access_key_id': KEY_ID,
                    'secret_access_key': SECRET, 'public_base_url': 'https://media.example.com',
                    'endpoint': 'http://127.0.0.1:9'}
            data.update(bad)
            with self.assertRaises(ValueError):
                r2_media.R2Config(data)

    def test_environment_variable_selects_the_config_file(self):
        with patch.dict('os.environ', {'ARTICLE_STUDIO_R2_CONFIG': str(self.config)}):
            self.assertEqual(r2_media.config_path(), self.config)
            self.assertEqual(r2_media.load_config().bucket, 'media')
        with patch.dict('os.environ', {}, clear=True):
            self.assertTrue(str(r2_media.config_path()).endswith('ArticleStudio/r2-media.json'))

    # --- public reachability --------------------------------------------------
    def test_public_check_accepts_only_a_real_image_response(self):
        config = self.loaded()
        self.host.objects['p/ok.png'] = b'\x89PNG'
        self.host.objects['p/html.png'] = b'<html/>'
        self.host.html.add('p/html.png')
        with patch.object(r2_media, 'urlopen', public_to_local(self.host)):
            self.assertTrue(r2_media.public_reachable(r2_media.public_url(config, 'p/ok.png'))['ok'])
            missing = r2_media.public_reachable(r2_media.public_url(config, 'p/absent.png'))
            self.assertFalse(missing['ok'])
            self.assertIn('404', missing['reason'])
            wrong = r2_media.public_reachable(r2_media.public_url(config, 'p/html.png'))
            self.assertFalse(wrong['ok'])
            self.assertIn('text/html', wrong['reason'])
            insecure = r2_media.public_reachable('http://media.example.com/p/ok.png')
            self.assertFalse(insecure['ok'])
            self.assertIn('HTTPS', insecure['reason'])
        get = [item for item in self.host.requests if item['method'] == 'GET'][-1]
        self.assertNotIn('authorization', get['headers'])

    # --- client identity ------------------------------------------------------
    def test_the_client_agent_is_fixed_and_names_this_workbench(self):
        self.assertEqual(r2_media.USER_AGENT, 'ArticleStudio/1.0 (+r2-media)')
        self.assertNotIn('Mozilla', r2_media.USER_AGENT)   # not disguised as a browser
        self.assertFalse(r2_media.USER_AGENT.startswith('Python-urllib'))

    def test_every_request_carries_the_fixed_client_agent(self):
        config = self.loaded()
        body = b'\x89PNG fixed agent bytes'
        self.host.objects['article-studio/x/s/v1/agent.png'] = body
        with patch.object(r2_media, 'urlopen', public_to_local(self.host)):
            r2_media.put_object('article-studio/x/s/v1/agent.png', body, 'image/png', config)
            self.assertIsNotNone(r2_media.head_object('article-studio/x/s/v1/agent.png', config))
            self.assertTrue(r2_media.public_reachable(r2_media.public_url(config, 'article-studio/x/s/v1/agent.png'))['ok'])
        for kind in ('PUT', 'HEAD', 'GET'):
            sent = [item for item in self.host.requests if item['method'] == kind][-1]
            self.assertEqual(sent['headers'].get('user-agent'), r2_media.USER_AGENT, kind)
        put = [item for item in self.host.requests if item['method'] == 'PUT'][-1]
        signed = put['headers']['authorization'].split('SignedHeaders=')[1].split(',')[0]
        self.assertNotIn('user-agent', signed.split(';'))   # the agent is never signed
        self.assertNotIn('user-agent', r2_media.signature_headers('PUT', r2_media.object_url(config, 'k'), body, config))

    def test_public_check_passes_on_a_host_that_rejects_the_default_python_agent(self):
        config = self.loaded()
        self.host.objects['article-studio/x/s/v1/agent.png'] = b'\x89PNG'
        self.host.block_python_ua = True
        try:
            with patch.object(r2_media, 'urlopen', public_to_local(self.host)):
                url = r2_media.public_url(config, 'article-studio/x/s/v1/agent.png')
                with patch.object(r2_media, 'USER_AGENT', 'Python-urllib/3.14'):
                    refused = r2_media.public_reachable(url)   # the old behaviour, for contrast
                self.assertFalse(refused['ok'])
                self.assertIn('403', refused['reason'])
                self.assertTrue(r2_media.public_reachable(url)['ok'])
            gets = [item for item in self.host.requests if item['method'] == 'GET']
            self.assertEqual(gets[0]['headers']['user-agent'], 'Python-urllib/3.14')
            self.assertEqual(gets[1]['headers']['user-agent'], r2_media.USER_AGENT)
        finally:
            self.host.block_python_ua = False

    def test_public_https_rejects_private_and_malformed_targets(self):
        for url in ('http://x.example.com/a.png', 'https://127.0.0.1/a.png', 'https://localhost/a.png',
                    'https://10.1.2.3/a.png', 'https://192.168.0.9/a.png', 'https://internal.local/a.png',
                    'https://intranet/a.png', 'https://user:pw@x.example.com/a.png',
                    'https://x.example.com:8443/a.png', '', None):
            self.assertFalse(r2_media.is_public_https(url), url)
        self.assertTrue(r2_media.is_public_https('https://media.example.com/a.png'))

    # --- redaction ------------------------------------------------------------
    def test_redact_strips_signature_material_and_configured_keys(self):
        config = self.loaded()
        noisy = ('Authorization: AWS4-HMAC-SHA256 Credential=' + KEY_ID + '/20261008/auto/s3/aws4_request, '
                 'SignedHeaders=host, Signature=abc123 ' + SECRET + ' x-amz-security-token: tok3n')
        clean = r2_media.redact(noisy, config)
        for secret in (SECRET, KEY_ID, 'AWS4-HMAC-SHA256', 'abc123', 'tok3n'):
            self.assertNotIn(secret, clean)
        self.assertLessEqual(len(r2_media.redact('y ' * 2000)), 412)


if __name__ == '__main__':
    unittest.main()
