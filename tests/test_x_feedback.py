import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from publishing.x_official import XOfficial, private_write, XError
from publishing.x_feedback import CURSOR_LIMIT, XFeedback, cursor_signature

class XFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = XOfficial(self.temp.name, Path(self.temp.name) / 'private.json')
        private_write(self.service.config_path, {'access_token':'test-placeholder-token', 'expires_at':time.time()+7200,
            'account':{'id':'111','username':'owner'}})
        self.feedback = XFeedback(self.service)
        self.payload = {'mode':'discussion','account_id':'111','post_id':'444',
            'start_time':'2026-10-10T00:00:00Z','end_time':'2026-10-11T00:00:00Z'}
    def tearDown(self): self.temp.cleanup()
    def result(self):
        return {'data':[{'id':'555','text':'<script>untrusted</script>','author_id':'222','created_at':'2026-10-10T01:00:00Z',
            'conversation_id':'444','referenced_tweets':[{'type':'replied_to','id':'556'}]}],
            'includes':{'users':[{'id':'222','username':'reader','name':'读者'}]},'meta':{'next_token':'page2'}}
    def test_sync_cache_parent_context_and_no_automatic_next_page(self):
        def remote(method,path,token):
            if path.startswith('/2/tweets/search/'): return self.result()
            self.assertTrue(path.startswith('/2/tweets?'))
            return {'data':[{'id':'444','text':'原帖','author_id':'111'},{'id':'556','text':'被回复评论','author_id':'222'}]}
        with patch('publishing.x_feedback.remote', side_effect=remote) as call:
            value=self.feedback.sync(self.payload)
            self.assertEqual(call.call_count,2)
            self.assertEqual(value['page']['next_token'],'page2')
            local=self.feedback.local()
            self.assertEqual(call.call_count,2)
        self.assertEqual(local['comments'][0]['parent_id'],'556')
        self.assertEqual(local['comments'][0]['username'],'reader')
        self.assertEqual({p['id'] for p in local['posts']},{'444','555','556'})
        self.assertEqual(self.feedback.path('111').stat().st_mode&0o777,0o600)
    def test_cache_isolated_on_account_change(self):
        private_write(self.feedback.path('111'),{'posts':{'444':{'id':'444'}},'comments':{},'pages':{},'synced_at':None})
        private_write(self.service.config_path,{'account':{'id':'333','username':'another'}})
        self.assertEqual(self.feedback.local()['posts'],[])
    def test_wrong_account_and_invalid_range_never_request_x(self):
        with patch('publishing.x_feedback.remote') as call:
            for payload in [dict(self.payload,account_id='333'),dict(self.payload,start_time='invalid'),dict(self.payload,end_time='2026-10-09T00:00:00Z')]:
                with self.assertRaises(ValueError): self.feedback.sync(payload)
            call.assert_not_called()
    def test_failed_read_preserves_existing_cache(self):
        private_write(self.feedback.path('111'),{'posts':{'444':{'id':'444','text':'原帖'}},'comments':{},'pages':{},'synced_at':None})
        before=self.feedback.path('111').read_text()
        with patch('publishing.x_feedback.remote',side_effect=XError('X HTTP 429')):
            with self.assertRaises(XError):self.feedback.sync(self.payload)
        self.assertEqual(self.feedback.path('111').read_text(),before)
    def test_pagination_accumulates_without_duplicates(self):
        first=self.result();first['includes']['tweets']=[{'id':'444','text':'原帖'},{'id':'556','text':'父评论'}]
        with patch('publishing.x_feedback.remote',return_value=first):
            self.feedback.sync(self.payload)
            self.feedback.sync(dict(self.payload,next_token='page2'))
        page=next(iter(self.feedback.local()['pages'].values()))
        self.assertEqual(page['ids'],['555'])
    def test_local_load_never_calls_token_or_remote(self):
        with patch.object(self.service,'token',side_effect=AssertionError('network')),patch('publishing.x_feedback.remote') as call:
            self.feedback.local()
            call.assert_not_called()

    def test_long_text_and_cached_author_survive_missing_expansion(self):
        private_write(self.feedback.path('111'), {'posts':{'555':{'id':'555','username':'reader','name':'读者'}},'comments':{},'pages':{},'synced_at':None})
        response = self.result()
        response['data'][0]['note_tweet'] = {'text':'完整长帖正文'}
        response['includes'] = {'tweets':[{'id':'444','text':'原帖'},{'id':'556','text':'父评论'}]}
        with patch('publishing.x_feedback.remote',return_value=response):
            self.feedback.sync(self.payload)
        item = self.feedback.cached('111')['comments']['555']
        self.assertEqual(item['text'], '完整长帖正文')
        self.assertEqual(item['username'], 'reader')

    def test_remote_dates_use_x_supported_seconds_not_microseconds(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        payload = dict(self.payload, mode='timeline', start_time=(now-timedelta(days=1)).isoformat(), end_time=(now+timedelta(days=1)).isoformat())
        with patch('publishing.x_feedback.remote',return_value={'data':[]}) as call:
            self.feedback.sync(payload)
        params = parse_qs(urlsplit(call.call_args.args[1]).query)
        for key in ('start_time','end_time'):
            self.assertRegex(params[key][0], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        self.assertLess(datetime.fromisoformat(params['end_time'][0].replace('Z','+00:00')),now)

    def test_original_timeline_excludes_replies_and_separates_cursors(self):
        with patch('publishing.x_feedback.remote',return_value={'data':[], 'meta':{'next_token':'next'}}) as call:
            self.feedback.sync(dict(self.payload,mode='timeline',post_kind='original'))
            self.assertEqual(parse_qs(urlsplit(call.call_args.args[1]).query)['exclude'],['replies,retweets'])
            self.feedback.sync(dict(self.payload,mode='timeline',post_kind='replies'))
            self.assertEqual(parse_qs(urlsplit(call.call_args.args[1]).query)['exclude'],['retweets'])
        self.assertEqual({p['post_kind'] for p in self.feedback.local()['pages'].values()},{'original','replies'})

    def test_image_proxy_rejects_external_hosts_credentials_and_redirects(self):
        from publishing.x_feedback import image_response
        from unittest.mock import Mock
        urls = ['http://pbs.twimg.com/media/a.jpg','https://example.com/media/a.jpg','https://pbs.twimg.com.evil/media/a.jpg','https://secret@pbs.twimg.com/media/a.jpg','https://@pbs.twimg.com/media/a.jpg','https://:@pbs.twimg.com/media/a.jpg','https://pbs.twimg.com/other/a.jpg']
        with patch('urllib.request.build_opener') as opener:
            for value in urls:
                with self.assertRaises(ValueError):image_response(Mock(),{'url':[value]})
            opener.assert_not_called()

    def test_image_proxy_serves_image_without_bearer_token(self):
        from publishing.x_feedback import image_response
        from unittest.mock import Mock
        handler = Mock();response=Mock();response.headers.get_content_type.return_value='image/jpeg';response.read.return_value=b'image'
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__=Mock(return_value=response)
            opener.return_value.open.return_value.__exit__=Mock(return_value=False)
            image_response(handler,{'url':['https://pbs.twimg.com/media/test.jpg']})
            request=opener.return_value.open.call_args.args[0]
            self.assertIsNone(request.get_header('Authorization'))
            redirect=opener.call_args.args[0]
            self.assertIsNone(redirect.redirect_request(None,None,302,'',{},'https://example.com'))
        handler.send_response.assert_called_once_with(200)
        handler.wfile.write.assert_called_once_with(b'image')

    def test_legacy_mentions_and_discussion_pages_are_replaced_not_shadowed(self):
        import hashlib, json
        for mode in ('mentions','discussion'):
            payload = dict(self.payload,mode=mode)
            target = '' if mode=='mentions' else payload['post_id']
            parts=[mode,target,payload['start_time'],payload['end_time']]
            old_key=hashlib.sha256(json.dumps(parts).encode()).hexdigest()
            expanded_key=hashlib.sha256(json.dumps(parts+['']).encode()).hexdigest()
            old={'mode':mode,'post_id':target,'start_time':payload['start_time'],'end_time':payload['end_time'],'ids':['123'],'next_token':'legacy','synced_at':'2026-10-10T01:00:00Z'}
            newer=dict(old,ids=['124'],next_token='previous',synced_at='2026-10-10T02:00:00Z')
            private_write(self.feedback.path('111'),{'posts':{},'comments':{},'pages':{old_key:old,expanded_key:newer},'synced_at':None})
            with patch('publishing.x_feedback.remote',return_value={'data':[], 'meta':{'next_token':'fresh'}}):
                result=self.feedback.sync(payload)
            pages=self.feedback.cached('111')['pages']
            self.assertEqual(list(pages),[old_key])
            self.assertEqual(result['page']['ids'],[])
            self.assertEqual(result['page']['next_token'],'fresh')

    def test_legacy_duplicate_discussion_cursor_accumulates_newest_page(self):
        import hashlib,json
        parts=['discussion','444',self.payload['start_time'],self.payload['end_time']]
        key=hashlib.sha256(json.dumps(parts).encode()).hexdigest()
        old={'mode':'discussion','post_id':'444','start_time':self.payload['start_time'],'end_time':self.payload['end_time'],'ids':['123'],'synced_at':'2026-10-10T01:00:00Z'}
        newer=dict(old,ids=['124'],synced_at='2026-10-10T02:00:00Z')
        # 这一页的续页 token 是我们自己为这个查询发出的，登记进游标表后才能继续翻。
        cursors={cursor_signature('111',parts):'next'}
        private_write(self.feedback.path('111'),{'posts':{},'comments':{},'pages':{key:old,'duplicate':newer},'cursors':cursors,'synced_at':None})
        with patch('publishing.x_feedback.remote',return_value={'data':[{'id':'555','text':'next'}]}):
            result=self.feedback.sync(dict(self.payload,next_token='next'))
        self.assertEqual(result['page']['ids'],['124','555'])

    # ---------------- R22：分页 token 必须属于当前账号的当前查询 ----------------

    def issue(self, payload=None, token='page2'):
        """先读一页，拿到服务发出去的续页 token（本机假 remote，零外网）。"""
        with patch('publishing.x_feedback.remote', return_value=self.result()):
            self.assertEqual(self.feedback.sync(payload or self.payload)['page']['next_token'], token)
        return token

    def test_issued_token_still_paginates_the_same_query(self):
        token = self.issue()
        with patch('publishing.x_feedback.remote', return_value={'data': [{'id': '777', 'text': '第二页'}],
              'meta': {'next_token': 'page3'}}) as call:
            result = self.feedback.sync(dict(self.payload, next_token=token))
        self.assertEqual(result['page']['ids'], ['555', '777'])
        self.assertEqual(result['result_count'], 1)
        # token 原样发给 X；讨论模式另有一次有界的原帖补齐查询
        self.assertIn('next_token=page2', call.call_args_list[0].args[1])

    def test_token_from_another_post_window_mode_or_account_is_rejected_without_calling_x(self):
        token = self.issue()
        others = [
            dict(self.payload, post_id='999'),                                        # 换帖子
            dict(self.payload, start_time='2026-10-09T00:00:00Z'),                     # 换时间窗
            dict(self.payload, mode='mentions'),                                       # 换模式
            dict(self.payload, mode='timeline'),                                       # 换模式（原帖时间线）
            dict(self.payload, mode='timeline', post_kind='replies'),                  # 换帖子分类
        ]
        with patch('publishing.x_feedback.remote') as call:
            for payload in others:
                with self.subTest(payload=payload):
                    with self.assertRaisesRegex(ValueError, '分页参数与当前查询不匹配'):
                        self.feedback.sync(dict(payload, next_token=token))
            call.assert_not_called()          # 跨签名的 token 一次都不能发给 X
        # 另一个账号即使在同一个本机缓存里也认不出来
        parts = ['discussion', '444', self.payload['start_time'], self.payload['end_time']]
        private_write(self.feedback.path('111'), {'posts': {}, 'comments': {}, 'pages': {},
            'cursors': {cursor_signature('222', parts): token}, 'synced_at': None})
        with patch('publishing.x_feedback.remote') as call:
            with self.assertRaisesRegex(ValueError, '分页参数与当前查询不匹配'):
                self.feedback.sync(dict(self.payload, next_token=token))
            call.assert_not_called()

    def test_unknown_and_malformed_tokens_are_rejected_without_calling_x(self):
        self.issue()
        for token in ['page9', '', 'x' * 1001]:
            with self.subTest(token=token[:12]):
                with patch('publishing.x_feedback.remote') as call:
                    if len(token) > 1000:
                        with self.assertRaisesRegex(ValueError, '分页参数无效'):
                            self.feedback.sync(dict(self.payload, next_token=token))
                    else:
                        with self.assertRaisesRegex(ValueError, '分页参数与当前查询不匹配'):
                            self.feedback.sync(dict(self.payload, next_token=token))
                    call.assert_not_called()

    def test_legacy_page_token_is_accepted_once_then_registered(self):
        # 升级前写下的页面自带 next_token：它确实是这个查询发出去的，先收下并补登记，合法分页不中断。
        import hashlib, json
        parts = ['discussion', '444', self.payload['start_time'], self.payload['end_time']]
        signature = hashlib.sha256(json.dumps(parts).encode()).hexdigest()
        page = {'mode': 'discussion', 'post_id': '444', 'start_time': self.payload['start_time'],
                'end_time': self.payload['end_time'], 'ids': ['123'], 'next_token': 'legacy-token',
                'synced_at': '2026-10-10T01:00:00Z'}
        private_write(self.feedback.path('111'), {'posts': {}, 'comments': {}, 'pages': {signature: page},
            'synced_at': None})
        with patch('publishing.x_feedback.remote', return_value={'data': [{'id': '555', 'text': '接着读'}],
              'meta': {'next_token': 'page3'}}) as call:
            result = self.feedback.sync(dict(self.payload, next_token='legacy-token'))
        self.assertEqual(result['page']['ids'], ['123', '555'])
        self.assertIn('next_token=legacy-token', call.call_args_list[0].args[1])
        self.assertEqual(result['page']['next_token'], 'page3')
        # 读完之后新发出的 token 已经登记到这个查询上，合法分页继续可用
        self.assertEqual(self.feedback.cached('111')['cursors'][cursor_signature('111', parts)], 'page3')
        with patch('publishing.x_feedback.remote', return_value={'data': [{'id': '888'}]}) as call:
            self.feedback.sync(dict(self.payload, next_token='page3'))
        self.assertIn('next_token=page3', call.call_args_list[0].args[1])

    def test_token_stops_working_after_the_last_page_and_registry_stays_bounded(self):
        with patch('publishing.x_feedback.remote', return_value=self.result()):
            self.feedback.sync(self.payload)
        parts = ['discussion', '444', self.payload['start_time'], self.payload['end_time']]
        key = cursor_signature('111', parts)
        self.assertEqual(self.feedback.cached('111')['cursors'][key], 'page2')
        # 读完整页：这一页没有下一页，旧 token 立刻作废，不能再被拿去请求 X。
        with patch('publishing.x_feedback.remote', return_value={'data': []}) as call:
            self.feedback.sync(dict(self.payload, next_token='page2'))
        self.assertIn('next_token=page2', call.call_args_list[0].args[1])
        self.assertNotIn(key, self.feedback.cached('111').get('cursors', {}))
        with patch('publishing.x_feedback.remote') as call:
            with self.assertRaisesRegex(ValueError, '分页参数与当前查询不匹配'):
                self.feedback.sync(dict(self.payload, next_token='page2'))
            call.assert_not_called()
        # 连续翻很多个查询签名，游标表也不会无限长。
        for n in range(CURSOR_LIMIT + 10):
            payload = dict(self.payload, start_time='2026-10-%02dT00:00:00Z' % (1 + n % 9),
                           end_time='2026-10-%02dT01:00:00Z' % (1 + n % 9))
            payload['post_id'] = str(400 + n)
            with patch('publishing.x_feedback.remote', return_value=self.result()):
                self.feedback.sync(payload)
        self.assertLessEqual(len(self.feedback.cached('111')['cursors']), CURSOR_LIMIT)
