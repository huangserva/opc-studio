import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from isolation_patches import module_time
from publishing.x_official import XOfficial, XError, private_write, remote


class XOfficialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = XOfficial(self.temp.name, Path(self.temp.name) / 'private/config.json')
        self.credentials = {'client_id': 'fixture-client', 'access_token': 'test-placeholder-access',
                            'refresh_token': 'test-placeholder-refresh', 'expires_at': time.time() + 7200,
                            'account': {'id': '111', 'username': 'fixture_user'}}
        private_write(self.service.config_path, self.credentials)

    def tearDown(self):
        self.temp.cleanup()

    def review(self, **kwargs):
        return self.service.review({'mode': 'post', 'parts': ['测试正文'], **kwargs})

    def job(self, review):
        return {'id': 'fixture', 'review_id': review['review_id'], 'status': 'queued',
                'created_at': time.time(), 'posts': [], 'mode': review['mode'], 'account': 'fixture_user'}

    def test_source_delivery_is_version_bound_and_idempotent(self):
        import server
        store = server.Store(self.temp.name)
        article = store.create({'title': '固定作品', 'type': 'post'})
        self.service.store = store
        source = {'slug': article['slug'], 'version': article['version']['id']}
        review = self.review(source=source)
        job = self.job(review)
        job['source'] = source
        with patch('publishing.x_official.remote', return_value={'data': {'id': '1234'}}):
            self.service.run(job, review)
        self.service.sync_deliveries(job)
        records = store.delivery_records(article['slug'])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['kind'], 'published_x_api')
        self.assertEqual(records[0]['version'], source['version'])
        self.assertEqual(records[0]['url'], 'https://x.com/fixture_user/status/1234')

    def test_credentials_never_returned_and_file_private(self):
        self.assertNotIn('test-placeholder-access', json.dumps(self.service.status()))
        self.assertNotIn('test-placeholder-refresh', json.dumps(self.service.status()))
        self.assertEqual(self.service.config_path.stat().st_mode & 0o777, 0o600)

    def test_oauth_pkce_and_callback_single_use(self):
        result = self.service.authorize('http://127.0.0.1:8766/api/x-official/callback')
        query = parse_qs(urlsplit(result['url']).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertIn('media.write', query['scope'][0])
        with self.assertRaises(ValueError):
            self.service.callback({'state': ['wrong'], 'code': ['code']})
        with patch('publishing.x_official.remote', side_effect=[
                {'access_token': 'replacement', 'refresh_token': 'refresh', 'expires_in': 7200},
                {'data': {'id': '222', 'username': 'new_account'}}]) as call:
            result = self.service.callback({'state': query['state'], 'code': ['code']})
            self.assertEqual(result['account']['username'], 'new_account')
            self.assertEqual(call.call_args_list[0].kwargs['form']['client_id'], 'fixture-client')
        with self.assertRaises(ValueError):
            self.service.callback({'state': query['state'], 'code': ['code']})

    def test_refresh_token_kept_and_confidential_client_auth(self):
        self.credentials.update(expires_at=0, client_secret='test-placeholder-client-secret')
        private_write(self.service.config_path, self.credentials)
        with patch('publishing.x_official.remote', return_value={'access_token': 'new', 'expires_in': 7200}) as call:
            self.assertEqual(self.service.token(), 'new')
            self.assertEqual(call.call_args.kwargs['basic'], 'fixture-client:test-placeholder-client-secret')
            self.assertEqual(self.service.config()['refresh_token'], 'test-placeholder-refresh')

    def test_unconnected_review_fails_without_remote_writes(self):
        self.service.configure({'client_id': 'other'})
        with patch('publishing.x_official.remote') as call:
            with self.assertRaises(ValueError):
                self.review()
            call.assert_not_called()

    def test_preview_and_media_validation(self):
        with patch('publishing.x_official.remote') as call:
            self.review()
            call.assert_not_called()
        for values in ({'parts': []}, {'mode': 'reply', 'reply_to': 'bad'},
                       {'mode': 'thread', 'parts': ['a', '']},
                       {'mode': 'article_draft', 'title': ''}, {'media': ['../../secret']}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.review(**values)

    def test_confirmation_idempotency_and_account_binding(self):
        review = self.review()
        with self.assertRaises(ValueError):
            self.service.submit({'review_id': review['review_id'], 'confirm_account': 'other'})
        with patch('publishing.x_official.threading.Thread') as worker:
            one = self.service.submit({'review_id': review['review_id'], 'confirm_account': 'fixture_user'})
            two = self.service.submit({'review_id': review['review_id'], 'confirm_account': 'fixture_user'})
            self.assertEqual(one['id'], two['id'])
            worker.assert_called_once()
        same = self.review()
        with self.assertRaises(ValueError):
            self.service.submit({'review_id': same['review_id'], 'confirm_account': 'fixture_user'})

    def test_changed_account_rejects_review(self):
        review = self.review()
        self.credentials['account']['id'] = '999'
        private_write(self.service.config_path, self.credentials)
        with self.assertRaises(ValueError):
            self.service.submit({'review_id': review['review_id'], 'confirm_account': 'fixture_user'})

    def test_thread_parent_chain_and_partial_failure(self):
        review = self.review(mode='thread', parts=['第一条', '第二条', '第三条'])
        job = self.job(review)
        with patch('publishing.x_official.remote', side_effect=[{'data': {'id': '123'}}, XError('拒绝')]) as call:
            self.service.run(job, review)
        self.assertEqual(job['status'], 'partial')
        self.assertEqual(job['posts'][0]['url'], 'https://x.com/i/status/123')
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[1].args[3]['reply']['in_reply_to_tweet_id'], '123')

    def test_uncertain_publish_and_restart_never_retry(self):
        review = self.review()
        job = self.job(review)
        with patch('publishing.x_official.remote', side_effect=XError('中断', True)) as call:
            self.service.run(job, review)
        self.assertEqual(job['status'], 'unknown')
        call.assert_called_once()
        job['status'] = 'running'
        self.service.save_job(job)
        reloaded = XOfficial(self.temp.name, self.service.config_path)
        self.assertEqual(reloaded.jobs['fixture']['status'], 'unknown')

    def test_confirm_not_sent_is_explicit_and_keeps_audit(self):
        review = self.review(mode='reply', reply_to='123')
        with patch('publishing.x_official.threading.Thread'):
            old = self.service.submit({'review_id': review['review_id'], 'confirm_account': 'fixture_user'})
        old.update(status='unknown')
        self.service.jobs[old['id']] = old
        same = self.review(mode='reply', reply_to='123')
        with self.assertRaises(ValueError):
            self.service.submit({'review_id': same['review_id'], 'confirm_account': 'fixture_user'})
        for payload in ({'job_id':old['id']}, {'job_id':old['id'],'confirm_not_sent':True,'confirm_account':'other'}):
            with self.assertRaises(ValueError): self.service.confirm_not_sent(payload)
        with patch('publishing.x_official.remote') as remote_call:
            result = self.service.confirm_not_sent({'job_id':old['id'],'confirm_not_sent':True,'confirm_account':'fixture_user'})
            remote_call.assert_not_called()
        self.assertEqual(result['previous_status'], 'unknown')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('user_confirmed_not_sent_at', result)
        with patch('publishing.x_official.threading.Thread') as worker:
            new = self.service.submit({'review_id':same['review_id'],'confirm_account':'fixture_user'})
            worker.assert_called_once()
        self.assertNotEqual(new['id'], old['id'])
        self.assertEqual(self.service.submit({'review_id':same['review_id'],'confirm_account':'fixture_user'})['id'],new['id'])
        new.update(status='published',posts=[{'id':'456'}])
        with self.assertRaises(ValueError):
            self.service.confirm_not_sent({'job_id':new['id'],'confirm_not_sent':True,'confirm_account':'fixture_user'})

    def test_article_draft_then_publish(self):
        review = self.review(mode='article_draft', title='标题', parts=['段落一\n\n段落二'])
        job = self.job(review)
        with patch('publishing.x_official.remote', return_value={'data': {'id': '321'}}) as call:
            self.service.run(job, review)
        self.assertEqual(call.call_args.args[1], '/2/articles/draft')
        self.assertEqual(len(call.call_args.args[3]['content_state']['blocks']), 2)
        self.assertEqual(job['status'], 'draft')
        published = self.review(mode='article_publish', parts=[], article_id='321')
        job = self.job(published)
        with patch('publishing.x_official.remote', return_value={'data': {'post_id': '456'}}):
            self.service.run(job, published)
        self.assertEqual(job['status'], 'published')

    def test_media_upload_chunks_processing_then_publish(self):
        path = self.service.root / 'media'
        path.mkdir()
        key = 'a' * 32 + '.mp4'
        (path / key).write_bytes(b'fixture-video')
        review = self.review(media=[key])
        job = self.job(review)
        slept = []
        with patch('publishing.x_official.remote', side_effect=[
                {'data': {'id': '789'}}, {}, {'data': {'processing_info': {'state': 'pending'}}},
                {'data': {'processing_info': {'state': 'succeeded'}}}, {'data': {'id': '456'}}]) as call, \
                module_time('publishing.x_official', sleep=slept.append):
            self.service.run(job, review)
        self.assertEqual(slept, [3])
        self.assertEqual(job['status'], 'published')
        self.assertEqual(call.call_args_list[-1].args[3]['media']['media_ids'], ['789'])
        self.assertEqual(call.call_args_list[1].args[3]['segment_index'], 0)

    def test_failed_media_never_creates_post(self):
        folder = self.service.root / 'media'
        folder.mkdir()
        key = 'a' * 32 + '.mp4'
        (folder / key).write_bytes(b'fixture-video')
        review = self.review(media=[key])
        job = self.job(review)
        with patch('publishing.x_official.remote', side_effect=[{'data': {'id': '789'}}, {},
                {'data': {'processing_info': {'state': 'failed'}}}]) as call:
            self.service.run(job, review)
        self.assertEqual(job['status'], 'failed')
        self.assertFalse(any(c.args[1] == '/2/tweets' for c in call.call_args_list))

    def test_upload_invalid_incomplete_and_image(self):
        from PIL import Image
        for name, data, length in [('x.txt', b'x', 1), ('x.png', b'bad', 3), ('x.mp4', b'x', 2)]:
            with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                self.service.upload_local(io.BytesIO(data), length, name)
        buffer = io.BytesIO()
        Image.new('RGB', (10, 10), 'red').save(buffer, format='PNG')
        data = buffer.getvalue()
        value = self.service.upload_local(io.BytesIO(data), len(data), '参考.png')
        self.assertTrue(self.service.media_path(value['id']).exists())

    def test_large_image_compressed_to_x_limit(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (20, 20), 'red').save(buffer, format='PNG')
        data = buffer.getvalue() + b'\0' * (5 * 1024**2)
        value = self.service.upload_local(io.BytesIO(data), len(data), '大图.png')
        self.assertTrue(value['compressed'])
        self.assertLessEqual(value['size'], 5 * 1024**2)
        self.assertTrue(value['id'].endswith('.jpg'))

    def test_article_cover_matches_official_schema(self):
        folder = self.service.root / 'media'
        folder.mkdir()
        key = 'b' * 32 + '.jpg'
        (folder / key).write_bytes(b'fixture')
        review = self.review(mode='article_draft', title='封面测试', media=[key])
        job = self.job(review)
        with patch.object(self.service, 'upload_x', return_value='789'), \
                patch('publishing.x_official.remote', return_value={'data': {'id': '321'}}) as call:
            self.service.run(job, review)
        self.assertEqual(call.call_args.args[3]['cover_media'], {'media_category': 'tweet_image', 'media_id': '789'})
        self.assertNotIn('cover_media_id', call.call_args.args[3])

    def test_comments_one_page_no_profile_expansion(self):
        with patch('publishing.x_official.remote', return_value={'data': [], 'meta': {}}) as call:
            self.service.comments({'mode': 'mentions', 'next_token': 'page'})
            path = call.call_args.args[1]
            self.assertIn('/2/users/111/mentions?', path)
            self.assertIn('pagination_token=page', path)
            self.assertNotIn('expansions', path)
            self.service.comments({'mode': 'archive', 'post_id': '123', 'next_token': 'next'})
            self.assertIn('/2/tweets/search/all?', call.call_args.args[1])
            self.assertIn('conversation_id%3A123', call.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
