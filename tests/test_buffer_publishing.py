"""X posts/threads → Buffer: request shape, channel check, dedup, unclear results.

Every test drives a fake `buffer` executable in a temporary directory. Nothing here
reaches the network or a real Buffer account; the fake records argv and stdin so the
outgoing request can be inspected byte for byte.
"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('studio', Path(__file__).resolve().parents[1] / 'server.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)
from publishing.transports import buffer_cli

FAKE = '''#!/usr/bin/env python3
import json, os, sys, time
LOG = __LOG__
SCRIPT = __SCRIPT__
DELIVERIES = __DELIVERIES__
calls = json.load(open(LOG, encoding='utf-8')) if os.path.exists(LOG) else []
entry = {'argv': sys.argv[1:], 'stdin': sys.stdin.read()}
if os.path.exists(DELIVERIES):
    entry['held'] = [r.get('kind') for r in json.load(open(DELIVERIES, encoding='utf-8'))
                     if str(r.get('kind', '')).startswith('buffer_')]
calls.append(entry)
json.dump(calls, open(LOG, 'w', encoding='utf-8'), ensure_ascii=False)
steps = json.load(open(SCRIPT, encoding='utf-8'))['steps']
step = steps[min(len(calls) - 1, len(steps) - 1)]
if step.get('sleep'):
    time.sleep(step['sleep'])
if step.get('stdout') is not None:
    sys.stdout.write(step['stdout'])
if step.get('stderr') is not None:
    sys.stderr.write(step['stderr'])
sys.exit(step.get('returncode', 0))
'''

# Synthetic test constants, assembled at run time so the public source tree never
# holds a credential-shaped literal. CHANNEL_ID is a stand-in for a Buffer channel
# id and carries no secret of its own.
CHANNEL_ID = 'facade' * 4
POST_ID = 'a1b2c3d4e5f6' + '0718293a4b5c'
TEXTS = ['第一条：Claude 封号故事 🚀', '第二条：Sidrune AI 怎么接手', '第三条：完整方法 “引号” & 与号']


def channels(*names, service='twitter'):
    return json.dumps([{'id': CHANNEL_ID if index == 0 else 'ch' + str(index) + 'x' * 21,
                        'name': name, 'service': service} for index, name in enumerate(names)])


def created(status='draft', link=None, typename='PostActionSuccess'):
    post = {'id': POST_ID, 'status': status, 'channelService': 'twitter'}
    if link:
        post['externalLink'] = link
    return json.dumps({'__typename': typename, 'post': post})


class BufferPublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / 'workspace'
        self.store = studio.Store(str(self.workspace))
        self.log = root / 'calls.json'
        self.script = root / 'script.json'
        fake = root / 'buffer'
        fake.write_text(FAKE.replace('__LOG__', repr(str(self.log)))
                            .replace('__SCRIPT__', repr(str(self.script)))
                            .replace('__DELIVERIES__', repr('')), encoding='utf-8')
        fake.chmod(0o755)
        self.fake = fake
        self.store.buffer_cli_command = [str(fake)]

    def tearDown(self):
        self.temp.cleanup()

    def steps(self, *steps):
        self.script.write_text(json.dumps({'steps': list(steps)}), encoding='utf-8')

    def calls(self):
        return json.loads(self.log.read_text(encoding='utf-8')) if self.log.exists() else []

    def thread(self, texts=TEXTS, account='servasyy_ai'):
        created_article = self.store.create({'title': '串文', 'type': 'post'})
        slug = created_article['slug']
        raw = copy.deepcopy(created_article['studio'])
        raw['platforms']['x'] = {'account': account, 'caption': '', 'title': '串文',
                                 'topics': '', 'status': 'draft'}
        self.store.save(slug, {'base_version': created_article['version']['id'], 'title': '串文',
                               'studio': raw,
                               'blocks': [{'kind': 'text', 'html': '<p>' + text + '</p>'} for text in texts]})
        return slug, self.store.meta(slug)['head']

    def deliveries(self, slug):
        return json.loads((self.store.article(slug) / 'deliveries.json').read_text(encoding='utf-8'))

    def watch_deliveries(self, slug):
        fake = self.fake
        fake.write_text(FAKE.replace('__LOG__', repr(str(self.log)))
                            .replace('__SCRIPT__', repr(str(self.script)))
                            .replace('__DELIVERIES__', repr(str(self.store.article(slug) / 'deliveries.json'))),
                        encoding='utf-8')
        fake.chmod(0o755)

    def submit(self, slug, version, mode='draft', **extra):
        return self.store.buffer_submit(slug, {'version': version, 'platform': 'x',
                                               'mode': mode, 'confirmed': True, **extra})

    # --- 1. request shape -----------------------------------------------------
    def test_draft_request_is_a_queue_draft_with_the_exact_thread(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0})
        result = self.submit(slug, version, 'draft')
        self.assertEqual(result['outcome'], 'submitted')
        calls = self.calls()
        self.assertEqual(calls[0]['argv'], ['channels', 'list', '--fields', 'id,name,service',
                                            '--output', 'json', '--quiet'])
        self.assertEqual(calls[1]['argv'], ['posts', 'create', '--input', '-', '--output', 'json', '--quiet'])
        body = json.loads(calls[1]['stdin'])
        self.assertEqual(body['saveToDraft'], True)
        self.assertEqual(body['schedulingType'], 'automatic')
        self.assertEqual(body['mode'], 'addToQueue')
        self.assertEqual(body['channelId'], CHANNEL_ID)
        thread = body['metadata']['twitter']['thread']
        self.assertEqual([item['text'] for item in thread], TEXTS)
        self.assertEqual(body['text'], thread[0]['text'])
        self.assertEqual(len(thread), 3)
        record = result['record']
        self.assertEqual(record['kind'], 'buffer_draft')
        self.assertEqual(record['post_id'], POST_ID)
        self.assertEqual(record['channel_id'], CHANNEL_ID)
        self.assertEqual(record['channel_name'], 'servasyy_ai')
        self.assertEqual(record['version'], version)
        self.assertEqual(record['submission_mode'], 'draft')

    def test_publish_now_request_uses_share_now_and_keeps_the_thread(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created('sent'), 'returncode': 0})
        result = self.submit(slug, version, 'publish_now')
        body = json.loads(self.calls()[1]['stdin'])
        self.assertEqual(body['saveToDraft'], False)
        self.assertEqual(body['schedulingType'], 'automatic')
        self.assertEqual(body['mode'], 'shareNow')
        self.assertEqual([item['text'] for item in body['metadata']['twitter']['thread']], TEXTS)
        self.assertEqual(body['text'], TEXTS[0])
        self.assertEqual(result['record']['kind'], 'buffer_submitted')
        self.assertEqual(result['record']['post_id'], POST_ID)

    def test_single_tweet_has_no_thread_metadata(self):
        slug, version = self.thread(['只有一条推文'])
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0})
        self.submit(slug, version, 'draft')
        body = json.loads(self.calls()[1]['stdin'])
        self.assertEqual(body['text'], '只有一条推文')
        self.assertNotIn('metadata', body)

    # --- 2. channel matching --------------------------------------------------
    def test_channel_name_mismatch_blocks_before_any_create(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('someone_else')})
        with self.assertRaises(ValueError) as caught:
            self.submit(slug, version, 'draft')
        self.assertIn('servasyy_ai', str(caught.exception))
        self.assertEqual([c['argv'][0:2] for c in self.calls()], [['channels', 'list']])

    def test_multiple_channels_with_the_same_name_block(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai', 'servasyy_ai')})
        with self.assertRaises(ValueError) as caught:
            self.submit(slug, version, 'draft')
        self.assertIn('2 个渠道', str(caught.exception))
        self.assertEqual(len(self.calls()), 1)

    def test_non_twitter_channel_is_not_accepted(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai', service='facebook')})
        with self.assertRaises(ValueError):
            self.submit(slug, version, 'draft')
        self.assertEqual(len(self.calls()), 1)

    def test_channel_matching_ignores_case_and_at_sign(self):
        found = buffer_cli.match_channel([{'id': CHANNEL_ID, 'name': 'Servasyy_AI', 'service': 'twitter'}],
                                         ' @servasyy_ai ')
        self.assertEqual(found['id'], CHANNEL_ID)
        with self.assertRaises(ValueError):
            buffer_cli.match_channel([], 'servasyy_ai')
        with self.assertRaises(ValueError):
            buffer_cli.match_channel([{'id': CHANNEL_ID, 'name': 'servasyy_ai', 'service': 'twitter'}], '')

    def test_channels_endpoint_reports_channel_and_posts_without_creating(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')})
        check = self.store.buffer_channels(slug, {'version': version, 'platform': 'x'})
        self.assertTrue(check['resolved'])
        self.assertEqual(check['channel']['id'], CHANNEL_ID)
        self.assertEqual([post['text'] for post in check['posts']], TEXTS)
        self.assertEqual(len(self.calls()), 1)

    # --- 2b. the saved version owns the target account -------------------------
    def test_request_body_cannot_name_or_replace_the_target_account(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai', 'someone_else')}, {'stdout': created(), 'returncode': 0})
        # A normal submit first, so the status call has a stored post to read.
        self.submit(slug, version, 'draft')
        self.assertEqual(self.calls()[1]['argv'][0:2], ['posts', 'create'])
        attempts = (
            ('channels', lambda: self.store.buffer_channels(
                slug, {'version': version, 'platform': 'x', 'account': 'someone_else'})),
            ('submit', lambda: self.submit(slug, version, 'publish_now', account='someone_else')),
            ('status', lambda: self.store.buffer_refresh(
                slug, {'version': version, 'platform': 'x', 'account': 'someone_else'})),
        )
        for name, attempt in attempts:
            with self.subTest(interface=name):
                with self.assertRaises(ValueError) as caught:
                    attempt()
                self.assertIn('account', str(caught.exception))
        # Nothing after the legitimate submit reached the CLI: no second create, no
        # shareNow under a foreign account, no status read for a foreign account.
        self.assertEqual([call['argv'][0:2] for call in self.calls()],
                         [['channels', 'list'], ['posts', 'create']])
        self.assertEqual(self.deliveries(slug)[0]['kind'], 'buffer_draft')
        self.assertEqual(self.deliveries(slug)[0]['account'], 'servasyy_ai')

    def test_empty_or_null_account_in_the_body_is_also_refused(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')})
        for value in ('', None, 'servasyy_ai'):
            with self.subTest(account=value):
                with self.assertRaises(ValueError):
                    self.store.buffer_channels(slug, {'version': version, 'platform': 'x', 'account': value})
        self.assertEqual(self.calls(), [])

    def test_channel_is_resolved_from_the_version_account_when_two_channels_exist(self):
        slug, version = self.thread()
        other, mine = 'someone' + '0' * 17, 'servasyy' + '1' * 16
        listing = json.dumps([{'id': other, 'name': 'someone_else', 'service': 'twitter'},
                              {'id': mine, 'name': 'servasyy_ai', 'service': 'twitter'}])
        self.steps({'stdout': listing}, {'stdout': created(), 'returncode': 0})
        result = self.submit(slug, version, 'publish_now')
        self.assertEqual(result['record']['channel_name'], 'servasyy_ai')
        self.assertEqual(result['record']['account'], 'servasyy_ai')
        body = json.loads(self.calls()[1]['stdin'])
        self.assertEqual(body['channelId'], mine)
        self.assertNotEqual(body['channelId'], other)

    # --- 3. confirmation and mode are mandatory -------------------------------
    def test_submit_without_confirmation_or_mode_is_refused_before_any_cli_call(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0})
        with self.assertRaises(ValueError):
            self.store.buffer_submit(slug, {'version': version, 'platform': 'x', 'mode': 'draft'})
        with self.assertRaises(ValueError):
            self.store.buffer_submit(slug, {'version': version, 'platform': 'x',
                                            'confirmed': True, 'mode': 'share_now'})
        with self.assertRaises(ValueError):
            self.store.buffer_submit(slug, {'version': version, 'platform': 'x',
                                            'confirmed': True, 'mode': 'scheduled'})
        self.assertEqual(self.calls(), [])

    def test_long_article_and_other_platforms_are_not_accepted(self):
        article = self.store.create({'title': '长文', 'type': 'article'})
        version = article['version']['id']
        self.steps({'stdout': channels('servasyy_ai')})
        with self.assertRaises(ValueError):
            self.store.buffer_submit(article['slug'], {'version': version, 'platform': 'x',
                                                       'mode': 'draft', 'confirmed': True})
        slug, post_version = self.thread()
        with self.assertRaises(ValueError):
            self.store.buffer_submit(slug, {'version': post_version, 'platform': 'xiaohongshu',
                                            'mode': 'draft', 'confirmed': True})
        self.assertEqual(self.calls(), [])

    # --- 4. duplicate protection ----------------------------------------------
    def test_second_submit_is_refused_and_cli_ran_once(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0})
        self.submit(slug, version, 'draft')
        with self.assertRaises(ValueError) as caught:
            self.submit(slug, version, 'draft')
        self.assertIn('Buffer', str(caught.exception))
        self.assertEqual(len([c for c in self.calls() if c['argv'][0:2] == ['posts', 'create']]), 1)
        self.assertEqual(len([c for c in self.calls() if c['argv'][0:2] == ['channels', 'list']]), 1)

    def test_in_progress_record_exists_before_the_cli_runs(self):
        slug, version = self.thread()
        self.watch_deliveries(slug)
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0})
        self.submit(slug, version, 'draft')
        create_call = [c for c in self.calls() if c['argv'][0:2] == ['posts', 'create']][0]
        self.assertEqual(create_call['held'], ['buffer_in_progress'])
        self.assertEqual(self.deliveries(slug)[0]['kind'], 'buffer_draft')

    def test_unclear_record_keeps_the_version_locked(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': 'not json at all', 'returncode': 0})
        result = self.submit(slug, version, 'draft')
        self.assertEqual(result['record']['kind'], 'buffer_unknown')
        self.assertFalse(result['retryable'])
        with self.assertRaises(ValueError):
            self.submit(slug, version, 'publish_now')
        self.assertEqual(len([c for c in self.calls() if c['argv'][0:2] == ['posts', 'create']]), 1)

    def test_a_different_version_may_still_be_submitted(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0},
                   {'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0})
        self.submit(slug, version, 'draft')
        self.store.save(slug, {'base_version': version, 'title': '串文',
                               'studio': copy.deepcopy(self.store.detail(slug)['studio']),
                               'blocks': [{'kind': 'text', 'html': '<p>改写后的第一条</p>'}]})
        newer = self.store.meta(slug)['head']
        self.submit(slug, newer, 'draft')
        self.assertEqual(len([c for c in self.calls() if c['argv'][0:2] == ['posts', 'create']]), 2)

    # --- 5. outcome classification --------------------------------------------
    def test_timeout_is_unknown_and_is_never_retried(self):
        slug, version = self.thread()
        self.store.buffer_cli_timeout = 1
        self.steps({'stdout': channels('servasyy_ai')}, {'sleep': 5, 'returncode': 0})
        result = self.submit(slug, version, 'draft')
        self.assertEqual(result['record']['kind'], 'buffer_unknown')
        self.assertIn('超时', result['record']['message'])
        self.assertFalse(result['retryable'])
        self.assertEqual(len([c for c in self.calls() if c['argv'][0:2] == ['posts', 'create']]), 1)

    def test_unparseable_success_output_is_unknown(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': '<<<not json>>>', 'returncode': 0})
        self.assertEqual(self.submit(slug, version, 'draft')['record']['kind'], 'buffer_unknown')

    def test_success_typename_without_post_id_is_unknown(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')},
                   {'stdout': json.dumps({'__typename': 'PostActionSuccess', 'post': {}}), 'returncode': 0})
        self.assertEqual(self.submit(slug, version, 'draft')['record']['kind'], 'buffer_unknown')

    def test_failure_typename_is_a_clean_failure_and_allows_resubmit(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')},
                   {'stdout': json.dumps({'__typename': 'PostActionFailure',
                                          'error': {'message': 'Text is too long'}}), 'returncode': 0},
                   {'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0})
        first = self.submit(slug, version, 'draft')
        self.assertEqual(first['record']['kind'], 'buffer_failed')
        self.assertTrue(first['retryable'])
        second = self.submit(slug, version, 'draft')
        self.assertEqual(second['record']['kind'], 'buffer_draft')
        self.assertEqual(len([c for c in self.calls() if c['argv'][0:2] == ['posts', 'create']]), 2)

    def test_structured_cli_error_is_a_clean_failure(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')},
                   {'stderr': json.dumps({'error': 'API_ERROR', 'code': 3,
                                          'message': 'Post text exceeds the channel limit'}),
                    'returncode': 3})
        result = self.submit(slug, version, 'draft')
        self.assertEqual(result['record']['kind'], 'buffer_failed')
        self.assertTrue(result['retryable'])

    def test_server_side_error_stays_unknown(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')},
                   {'stderr': json.dumps({'error': 'API_ERROR', 'code': 3,
                                          'message': '503 server overloaded, try again later'}),
                    'returncode': 3})
        result = self.submit(slug, version, 'draft')
        self.assertEqual(result['record']['kind'], 'buffer_unknown')
        self.assertFalse(result['retryable'])

    def test_non_zero_exit_without_structured_error_is_unknown(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stderr': 'killed by signal', 'returncode': 1})
        self.assertEqual(self.submit(slug, version, 'draft')['record']['kind'], 'buffer_unknown')

    def test_error_detail_is_truncated_and_scrubbed(self):
        secret = 'sk-' + 'Zk9mQx7Lp2Vn4Rt8Yb3Hc6Fd'
        self.assertNotIn(secret, buffer_cli.redact('api_key: ' + secret))
        noisy_text = ('y' * 3 + ' ') * 2000
        self.assertLessEqual(len(buffer_cli.redact(noisy_text)), buffer_cli.MAX_DETAIL_CHARS + 12)
        slug, version = self.thread()
        noisy = json.dumps({'error': 'API_ERROR', 'code': 3,
                            'message': 'rejected, token=abcdefghij' + 'K' * 40 + ' tail ' + noisy_text})
        self.steps({'stdout': channels('servasyy_ai')}, {'stderr': noisy, 'returncode': 3})
        record = self.submit(slug, version, 'draft')['record']
        self.assertNotIn('abcdefghij' + 'K' * 40, record['message'])
        self.assertLessEqual(len(record['message']), 450)
        self.assertIn('Buffer 拒绝该请求', record['message'])

    # --- 6. status refresh ----------------------------------------------------
    def test_refresh_records_the_published_tweet_link(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0},
                   {'stdout': json.dumps({'id': POST_ID, 'status': 'sent',
                                          'externalLink': 'https://x.com/servasyy_ai/status/1912345678901234567'}),
                    'returncode': 0})
        self.submit(slug, version, 'draft')
        result = self.store.buffer_refresh(slug, {'version': version, 'platform': 'x'})
        record = result['record']
        self.assertEqual(record['kind'], 'buffer_published')
        self.assertEqual(record['url'], 'https://x.com/servasyy_ai/status/1912345678901234567')
        self.assertEqual(record['buffer_status'], 'sent')
        self.assertEqual(self.deliveries(slug)[0]['url'], record['url'])

    def test_refresh_rejects_a_link_belonging_to_another_account(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0},
                   {'stdout': json.dumps({'id': POST_ID, 'status': 'sent',
                                          'externalLink': 'https://twitter.com/someone_else/status/123'}),
                    'returncode': 0})
        self.submit(slug, version, 'draft')
        record = self.store.buffer_refresh(slug, {'version': version, 'platform': 'x'})['record']
        self.assertNotEqual(record['kind'], 'buffer_published')
        self.assertIsNone(record['url'])
        self.assertIn('someone_else', record['message'])

    def test_refresh_without_a_link_keeps_the_status_only(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0},
                   {'stdout': json.dumps({'id': POST_ID, 'status': 'draft'}), 'returncode': 0})
        self.submit(slug, version, 'draft')
        record = self.store.buffer_refresh(slug, {'version': version, 'platform': 'x'})['record']
        self.assertEqual(record['kind'], 'buffer_draft')
        self.assertEqual(record['buffer_status'], 'draft')
        self.assertIsNone(record['url'])

    def test_buffer_publish_error_is_a_failed_record_with_details(self):
        slug, version = self.thread()
        self.steps({'stdout':channels('servasyy_ai')},{'stdout':created(),'returncode':0},
                   {'stdout':json.dumps({'id':POST_ID,'status':'failed','error':{'message':'Image too large'}}),'returncode':0})
        self.submit(slug,version,'draft')
        record=self.store.buffer_refresh(slug,{'version':version,'platform':'x'})['record']
        self.assertEqual(record['kind'],'buffer_failed')
        self.assertEqual(record['error_message'],'Image too large')
        self.assertIn('Image too large',record['message'])
        self.assertIsNotNone(record['observed_at'])

    def test_refresh_failure_does_not_change_the_record_kind(self):
        slug, version = self.thread()
        self.steps({'stdout': channels('servasyy_ai')}, {'stdout': created(), 'returncode': 0},
                   {'stderr': json.dumps({'error': 'API_ERROR', 'code': 3, 'message': 'Post not found'}),
                    'returncode': 3})
        self.submit(slug, version, 'draft')
        result = self.store.buffer_refresh(slug, {'version': version, 'platform': 'x'})
        self.assertEqual(result['outcome'], 'failed')
        self.assertEqual(result['record']['kind'], 'buffer_draft')

    def test_refresh_needs_a_stored_post_id(self):
        slug, version = self.thread()
        with self.assertRaises(ValueError):
            self.store.buffer_refresh(slug, {'version': version, 'platform': 'x'})
        self.assertEqual(self.calls(), [])

    def test_post_id_is_validated_before_the_cli_runs(self):
        slug, version = self.thread()
        from publishing.core.common import write_json
        write_json(self.store.article(slug) / 'deliveries.json',
                   [{'id': 'x' * 32, 'kind': 'buffer_draft', 'platform': 'x', 'version': version,
                     'account': 'servasyy_ai', 'post_id': '../../etc/passwd', 'url': None,
                     'submission_mode': 'draft', 'created_at': '2026-10-08T00:00:00+00:00'}])
        self.steps({'stdout': '{}', 'returncode': 0})
        with self.assertRaises(ValueError):
            self.store.buffer_refresh(slug, {'version': version, 'platform': 'x'})
        self.assertEqual(self.calls(), [])

    def test_tweet_link_parsing_accepts_only_real_tweet_urls(self):
        for url in ('https://x.com/servasyy_ai/status/123', 'https://twitter.com/servasyy_ai/status/123',
                    'https://www.x.com/servasyy_ai/status/123'):
            self.assertEqual(buffer_cli.tweet_link(url)['account'], 'servasyy_ai')
        for url in ('http://x.com/servasyy_ai/status/123', 'https://x.com/servasyy_ai/status/abc',
                    'https://x.com/servasyy_ai/status/123?a=1#/x', 'https://evil.example/x.com/a/status/1',
                    'https://x.com/compose/post', 'https://x.com/a/b/status/1', '', None):
            self.assertIsNone(buffer_cli.tweet_link(url))

    # --- 7. transport hygiene -------------------------------------------------
    def test_cli_is_always_an_argument_list_never_a_shell_string(self):
        for bad in ('buffer; rm -rf /', '', ['buffer', 5], [], {'a': 1}):
            with self.assertRaises(ValueError):
                buffer_cli.cli_command(bad)
        self.assertEqual(buffer_cli.cli_command(['/bin/buffer']), ['/bin/buffer'])
        self.assertTrue(buffer_cli.cli_command(None)[0])

    def test_missing_cli_never_reports_a_submission(self):
        result = buffer_cli.create_post({'channelId': CHANNEL_ID, 'mode': 'shareNow',
                                         'schedulingType': 'automatic', 'text': 'x'},
                                        command=[str(Path(self.temp.name) / 'definitely-missing')])
        self.assertEqual(result['outcome'], 'failed')
        self.assertIn('未找到', result['message'])

    def test_channel_list_failure_is_reported_without_submitting(self):
        slug, version = self.thread()
        self.steps({'stderr': json.dumps({'error': 'AUTH_ERROR', 'code': 4, 'message': 'authentication failed'}),
                    'returncode': 4})
        with self.assertRaises(ValueError) as caught:
            self.store.buffer_channels(slug, {'version': version, 'platform': 'x'})
        self.assertIn('读取 Buffer 渠道失败', str(caught.exception))
        self.assertEqual(len(self.calls()), 1)

    def test_validate_still_only_dry_runs(self):
        self.steps({'stdout': '{}', 'returncode': 0})
        result = buffer_cli.validate({'saveToDraft': True, 'text': 'a; $(echo bad)'}, [str(self.fake)])
        self.assertFalse(result['remote_request_sent'])
        self.assertEqual(self.calls()[0]['argv'][0:4], ['posts', 'create', '--input', '-'])
        self.assertIn('--dry-run', self.calls()[0]['argv'])


if __name__ == '__main__':
    unittest.main()
