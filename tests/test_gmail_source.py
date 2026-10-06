import base64
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from gmail_source import GmailCache, BODY_CHARS, MAIL_BYTES


class GmailCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = GmailCache(self.root)
        self.account = 'demo.user@gmail.com'

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def part(content='', mime='text/plain', encoded=False, **fields):
        body = {'size': len(content.encode('utf-8'))}
        if encoded:
            body['base64_url_content'] = base64.urlsafe_b64encode(content.encode()).decode().rstrip('=')
        else:
            body['content'] = content
        return {'mime_type': mime, 'headers': [], 'body': body, **fields}

    def message(self, mid='aa11', content='演示邮件正文', **fields):
        part = self.part(content)
        part['headers'] = [
            {'name': 'Subject', 'value': '=?UTF-8?B?5Lya6K6u5a6J5o6S?='},
            {'name': 'From', 'value': 'Demo Sender <sender@example.test>'},
            {'name': 'To', 'value': self.account},
            {'name': 'Message-ID', 'value': '<demo-' + mid + '@example.test>'},
            {'name': 'Date', 'value': 'Sat, 3 Oct 2026 16:00:00 +0800'},
        ]
        return {'id': mid, 'thread_id': 'bb22', 'label_ids': ['INBOX', 'Label_3'],
            'snippet': '这只是演示预览', 'history_id': '1234', 'internal_date': '1791014400000',
            'payload': part, 'size_estimate': 500, **fields}

    def import_batch(self, messages, **fields):
        return self.cache.import_messages({'account': self.account, 'query': 'after:2026/10/01',
            'messages': messages, 'has_more': True, 'captured_at': '2026-10-04T12:00:00+08:00', **fields})

    def test_headers_times_account_link_and_query_scope_are_preserved(self):
        status = self.import_batch([self.message()], account='DEMO.USER@gmail.com')
        record = self.cache.records(self.account)[0]
        self.assertEqual(record['account'], self.account)
        self.assertEqual(record['subject'], '会议安排')
        self.assertEqual(record['body'], '演示邮件正文')
        self.assertEqual(record['date_sent'], 'Sat, 3 Oct 2026 16:00:00 +0800')
        self.assertEqual(record['rfc_message_id'], '<demo-aa11@example.test>')
        self.assertEqual(record['modified'], '2026-10-03T08:00:00.000+00:00')
        url = urlsplit(record['source_url'])
        self.assertEqual((url.scheme, url.hostname, url.fragment), ('https', 'mail.google.com', 'all/aa11'))
        self.assertEqual(parse_qs(url.query)['authuser'], [self.account])
        self.assertEqual(status['query'], 'after:2026/10/01')
        self.assertTrue(status['has_more'])
        self.assertIn('不代表全邮箱覆盖', status['scope'])
        self.assertEqual(record['content_scope']['query'], status['query'])

    def test_connector_nullable_metadata_does_not_block_real_mime_records(self):
        message = self.message()
        message['classification_label_values'] = None
        message['payload']['read_attachment_supported'] = None
        message['payload']['parts'] = None
        self.import_batch([message])
        self.assertEqual(self.cache.records(self.account)[0]['body'], '演示邮件正文')
        message['payload']['read_attachment_supported'] = 'invalid'
        with self.assertRaises(ValueError):
            self.import_batch([message])

    def test_rfc_header_names_accept_underscores_and_reject_delimiters(self):
        message = self.message()
        message['payload']['headers'].append({'name': 'sesame_open', 'value': 'custom header'})
        self.import_batch([message])
        self.assertEqual(self.cache.records(self.account)[0]['body'], '演示邮件正文')
        before = self.cache.records(self.account)
        for name in ('', 'bad:name', 'bad name', 'bad\tname', 'bad\r\nname', 'bad\x00name', '非ASCII'):
            malformed = self.message('aa12')
            malformed['payload']['headers'].append({'name': name, 'value': 'invalid'})
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.import_batch([malformed])
            self.assertEqual(self.cache.records(self.account), before)

    def test_plain_alternative_is_preferred_and_quoted_mail_kept(self):
        message = self.message()
        message['payload']['mime_type'] = 'multipart/alternative'
        message['payload']['body'] = {'size': 0}
        quoted = '最新答复\n\n> 之前的邮件：请周二确认\n> 原内容保留'
        message['payload']['parts'] = [self.part('<p>重复HTML版本</p>', 'text/html'), self.part(quoted, encoded=True)]
        self.import_batch([message])
        record = self.cache.records(self.account)[0]
        self.assertEqual(record['body'], quoted)
        self.assertEqual(record['content_scope']['body_source'], 'text/plain')
        self.assertFalse(record['content_scope']['truncated'])

    def test_html_entities_scripts_style_and_blockquotes_are_handled_as_text(self):
        message = self.message()
        message['payload']['mime_type'] = 'text/html'
        message['payload']['body'] = {'content': '<style>.secret {display:none}</style><script>sendAllMail()</script>'
            '<div>明天&nbsp;10点 &amp; 确认</div><blockquote><p>原邮件：项目 &#x1f600;</p></blockquote>'}
        self.import_batch([message])
        record = self.cache.records(self.account)[0]
        self.assertIn('明天 10点 & 确认', record['body'])
        self.assertIn('原邮件：项目 😀', record['body'])
        self.assertNotIn('sendAllMail', record['body'])
        self.assertNotIn('.secret', record['body'])
        self.assertNotIn('<blockquote>', record['body'])
        self.assertEqual(record['content_scope']['body_source'], 'text/html')

    def test_text_attachment_is_not_mistaken_for_body_and_missing_is_explicit(self):
        message = self.message()
        message['payload']['mime_type'] = 'multipart/mixed'
        message['payload']['body'] = {'size': 0}
        attachment = self.part('不能成为邮件正文的附件内容', filename='demo.txt')
        attachment['body']['attachment_id'] = 'demo-attachment'
        message['payload']['parts'] = [attachment]
        self.import_batch([message])
        record = self.cache.records(self.account)[0]
        self.assertEqual(record['body'], '')
        self.assertEqual(record['snippet'], '这只是演示预览')
        self.assertTrue(record['content_scope']['missing'])
        self.assertTrue(record['content_scope']['attachments_not_read'])
        self.assertIn('附件未读取', record['content_scope']['detail'])
        self.assertIn('预览不作为完整正文', record['content_scope']['detail'])

    def test_direct_content_and_base64_charset_fallback(self):
        direct = self.message('aa01', content='直接内容优先')
        direct['payload']['body']['base64_url_content'] = base64.urlsafe_b64encode('次选内容'.encode()).decode()
        encoded = self.message('aa02')
        encoded['payload']['headers'].append({'name': 'Content-Type', 'value': 'text/plain; charset=gb18030'})
        encoded['payload']['body'] = {'content': '', 'base64_url_content': base64.urlsafe_b64encode('中文编码'.encode('gb18030')).decode().rstrip('=')}
        self.import_batch([direct, encoded])
        records = {r['message_id']: r for r in self.cache.records(self.account)}
        self.assertEqual(records['aa01']['body'], '直接内容优先')
        self.assertEqual(records['aa02']['body'], '中文编码')

    def test_truncation_is_explicit_and_source_content_is_not_modified(self):
        message = self.message(content='正文证据' * 30000)
        original = copy.deepcopy(message)
        self.import_batch([message])
        record = self.cache.records(self.account)[0]
        self.assertEqual(len(record['body']), BODY_CHARS)
        self.assertTrue(record['content_scope']['truncated'])
        self.assertIn('100,000', record['content_scope']['detail'])
        self.assertEqual(message, original)

    def test_idempotent_append_preserves_old_batches_and_account_isolation(self):
        self.import_batch([self.message()])
        self.import_batch([self.message(content='更新后的同一封邮件正文')], query='label:work', has_more=False)
        self.assertEqual(self.cache.status(self.account)['count'], 1)
        self.assertEqual(self.cache.records(self.account)[0]['body'], '更新后的同一封邮件正文')
        self.import_batch([self.message('aa12')], query='newer_than:1d')
        self.assertEqual(self.cache.status(self.account)['count'], 2)
        self.import_batch([self.message()], account='other@example.test', query='in:inbox')
        self.assertEqual(self.cache.status('other@example.test')['count'], 1)
        self.assertEqual(self.cache.status(self.account)['count'], 2)
        self.import_batch([], query='after:2099/01/01', has_more=False)
        self.assertEqual(self.cache.status(self.account)['count'], 2)
        self.assertEqual(self.cache.status(self.account)['query'], 'after:2099/01/01')
        self.assertEqual({r['content_scope']['query'] for r in self.cache.records(self.account)}, {'label:work', 'newer_than:1d'})

    def test_bad_later_mail_leaves_existing_records_and_scope_unchanged(self):
        self.import_batch([self.message()])
        before_records = self.cache.records(self.account)
        before_status = self.cache.status(self.account)
        bad = self.message('aa14')
        bad['payload']['body'] = {'base64_url_content': '%%%not-base64%%%'}
        with self.assertRaises(ValueError):
            self.import_batch([self.message('aa13'), bad], query='different-query', has_more=False)
        self.assertEqual(self.cache.records(self.account), before_records)
        self.assertEqual(self.cache.status(self.account), before_status)

    def test_database_failure_rolls_back_every_mail_and_batch_metadata(self):
        self.import_batch([self.message()])
        before_records = self.cache.records(self.account)
        before_status = self.cache.status(self.account)
        with closing(sqlite3.connect(self.cache.path)) as c:
            c.execute("CREATE TRIGGER test_failure BEFORE INSERT ON messages WHEN new.message_id='ee02' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
            c.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.import_batch([self.message('ee01'), self.message('ee02')], query='failed-query')
        self.assertEqual(self.cache.records(self.account), before_records)
        self.assertEqual(self.cache.status(self.account), before_status)

    def test_strict_ids_types_time_and_labels_and_bounded_size(self):
        for account in ['no-at-sign', 'person@example', 'a..b@gmail.com', 'a@gmail.com\n', 'a@gmail.com?authuser=x']:
            with self.subTest(account=account), self.assertRaises(ValueError):
                self.import_batch([], account=account)
        variants = [{'id': '../source'}, {'thread_id': 'not-hex'}, {'internal_date': '-1'},
            {'internal_date': 1791014400000}, {'internal_date': '253402300800000'},
            {'label_ids': ['INBOX\nFAKE']}, {'label_ids': 'INBOX'}, {'size_estimate': True},
            {'size_estimate': MAIL_BYTES + 1}, {'history_id': 'bad-history'}]
        for fields in variants:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.import_batch([self.message(**fields)])
        for fields in [{'has_more': 'false'}, {'captured_at': '2026-10-04T10:00:00'},
                {'captured_at': '2026-02-30T10:00:00Z'}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.import_batch([], **fields)
        with self.assertRaises(ValueError):
            self.import_batch([self.message()] * 101)
        with self.assertRaises(ValueError):
            self.import_batch([self.message(content='x' * MAIL_BYTES)])
        with self.assertRaises(ValueError):
            self.import_batch([self.message(mid=hex(i + 1)[2:], content='x' * 1800000) for i in range(6)])
        self.assertEqual(self.cache.status(self.account)['count'], 0)

    def test_invalid_header_date_is_not_fabricated_and_internal_time_remains_available(self):
        message = self.message()
        for header in message['payload']['headers']:
            if header['name'] == 'Date':
                header['value'] = 'Sat, 3 Oct 2026 08:00:00'
        self.import_batch([message])
        record = self.cache.records(self.account)[0]
        self.assertEqual(record['date_sent'], '')
        self.assertEqual(record['date_header'], 'Sat, 3 Oct 2026 08:00:00')
        self.assertTrue(record['content_scope']['date_sent_missing_or_invalid'])
        self.assertTrue(record['modified'].endswith('+00:00'))

    def test_private_cache_and_restart_preserve_exact_evidence(self):
        self.assertIn('不代表邮箱为空', self.cache.status(self.account)['scope'])
        self.import_batch([self.message()])
        self.assertEqual(GmailCache(self.root).records(self.account), self.cache.records(self.account))
        self.assertEqual(self.cache.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.cache.path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
