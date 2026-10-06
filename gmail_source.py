"""Bounded, local Gmail MIME evidence cache. No network or credential access."""
import base64
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode


MAIL_BYTES = 2 * 1024 * 1024
BATCH_BYTES = 10 * 1024 * 1024
BODY_CHARS = 100000
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def account_address(value):
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", value):
        raise ValueError('Gmail 账号须为有效邮箱地址')
    local, domain = value.rsplit('@', 1)
    if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local or any(len(label) > 63 for label in domain.split('.')):
        raise ValueError('Gmail 账号格式无效')
    return value.lower()


def mail_id(value, name):
    if not isinstance(value, str) or not re.fullmatch('[a-fA-F0-9]{1,64}', value):
        raise ValueError(f'{name}须为 Gmail 十六进制编号')
    return value.lower()


def bounded_text(value, label, maximum, optional=False):
    if optional and value is None:
        return ''
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{label}须为文字，最多 {maximum} 字')
    return value


def captured_time(value):
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    bounded_text(value, '观察时间', 50)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('观察时间须为含时区的 ISO 日期时间')
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('观察日期时间无效') from None
    if date.utcoffset() is None:
        raise ValueError('观察时间须包含时区')
    return date.isoformat()


def integer(value, label, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f'{label}无效或超出上限')
    return value


class MailHTML(HTMLParser):
    """Render HTML as text while keeping blockquotes and excluding active code."""
    BLOCKS = {'p', 'div', 'section', 'article', 'header', 'footer', 'table', 'tr', 'li',
        'ul', 'ol', 'blockquote', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden.append(tag)
        elif not self.hidden:
            if tag in self.BLOCKS or tag == 'br':
                self.fragments.append('\n')
            elif tag in ('td', 'th'):
                self.fragments.append('\t')

    def handle_startendtag(self, tag, attrs):
        if tag not in ('script', 'style'):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in self.BLOCKS:
            self.fragments.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.fragments.append(data)

    def text(self):
        text = ''.join(self.fragments).replace('\r\n', '\n').replace('\r', '\n').replace('\xa0', ' ')
        text = '\n'.join(line.strip() for line in text.split('\n'))
        return re.sub(r'\n{3,}', '\n\n', text).strip()


def headers(part):
    values = part.get('headers', [])
    if not isinstance(values, list) or len(values) > 200:
        raise ValueError('MIME 邮件头格式无效')
    result = {}
    for header in values:
        if not isinstance(header, dict) or set(header) - {'name', 'value'}:
            raise ValueError('MIME 邮件头字段无效')
        name = header.get('name')
        # RFC 5322 field-name allows printable ASCII except colon (ftext).
        if not isinstance(name, str) or not re.fullmatch('[!-9;-~]{1,78}', name):
            raise ValueError('邮件头名称无效')
        value = bounded_text(header.get('value'), '邮件头', 8192)
        result.setdefault(name.lower(), []).append(value)
    return result


def header_value(values, name, all_values=False):
    entries = values.get(name, [])
    return ', '.join(entries) if all_values else entries[0] if entries else ''


def decode_header_value(value):
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value


def render_body(payload):
    plain, html = [], []
    scope = {'body_source': 'missing', 'truncated': False, 'missing': True,
        'attachments_not_read': False, 'attachment_parts': 0, 'decoding_replaced': False}
    visited = 0

    def walk(part, depth=0):
        nonlocal visited
        visited += 1
        if not isinstance(part, dict) or depth > 30 or visited > 500:
            raise ValueError('MIME 结构过深、过多或无效')
        allowed = {'mime_type', 'headers', 'body', 'parts', 'filename', 'part_id', 'read_attachment_supported'}
        if set(part) - allowed:
            raise ValueError('MIME 包含未知字段')
        if part.get('read_attachment_supported') is not None and not isinstance(part['read_attachment_supported'], bool):
            raise ValueError('MIME 附件能力字段无效')
        raw_mime = bounded_text(part.get('mime_type', ''), 'MIME 类型', 150)
        mime = raw_mime.lower().split(';', 1)[0].strip()
        if mime and not re.fullmatch('[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+', mime):
            raise ValueError('MIME 类型无效')
        hs = headers(part)
        body = part.get('body')
        if body is None:
            body = {}
        if not isinstance(body, dict) or set(body) - {'size', 'base64_url_content', 'content', 'attachment_id'}:
            raise ValueError('MIME 正文字段无效')
        if 'size' in body:
            integer(body['size'], 'MIME 正文大小', MAIL_BYTES)
        for field in ('content', 'base64_url_content', 'attachment_id'):
            if field in body:
                bounded_text(body[field], 'MIME ' + field, MAIL_BYTES, optional=True)
        filename = bounded_text(part.get('filename', ''), '附件名称', 1000, optional=True)
        if 'part_id' in part:
            bounded_text(part['part_id'], 'MIME part_id', 100, optional=True)
        parts = part.get('parts')
        if parts is None:
            parts = []
        if not isinstance(parts, list) or len(parts) > 500:
            raise ValueError('MIME 子段格式无效')
        descriptor = Message()
        if header_value(hs, 'content-disposition'):
            descriptor['Content-Disposition'] = header_value(hs, 'content-disposition')
        if header_value(hs, 'content-type') or raw_mime:
            descriptor['Content-Type'] = header_value(hs, 'content-type') or raw_mime
        attachment = bool(filename or body.get('attachment_id') or descriptor.get_filename()
            or descriptor.get_content_disposition() == 'attachment')
        if attachment or (not parts and mime and mime not in ('text/plain', 'text/html') and not mime.startswith('multipart/')):
            scope['attachments_not_read'] = True
            scope['attachment_parts'] += 1
            return
        if parts:
            for child in parts:
                walk(child, depth + 1)
            return
        if mime not in ('text/plain', 'text/html'):
            return
        content = body.get('content')
        if not isinstance(content, str) or not content:
            encoded = body.get('base64_url_content')
            if not encoded:
                return
            if not re.fullmatch('[A-Za-z0-9_-]*={0,2}', encoded) or len(encoded.rstrip('=')) % 4 == 1:
                raise ValueError('MIME base64url 正文无效')
            try:
                raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
            except ValueError:
                raise ValueError('MIME base64url 正文解码失败') from None
            charset = descriptor.get_content_charset() or 'utf-8'
            try:
                content = raw.decode(charset)
            except (UnicodeError, LookupError):
                content = raw.decode('utf-8', errors='replace')
                scope['decoding_replaced'] = True
        if '\x00' in content:
            raise ValueError('邮件正文包含无效 NUL 字符')
        if mime == 'text/plain':
            plain.append(content.replace('\r\n', '\n').replace('\r', '\n'))
        else:
            parser = MailHTML()
            parser.feed(content)
            parser.close()
            html.append(parser.text())

    walk(payload)
    nonempty_plain = [p for p in plain if p.strip()]
    nonempty_html = [p for p in html if p.strip()]
    chosen = nonempty_plain or nonempty_html
    body = '\n\n'.join(chosen)
    scope['missing'] = not bool(body.strip())
    scope['body_source'] = 'text/plain' if nonempty_plain else 'text/html' if nonempty_html else 'missing'
    scope['truncated'] = len(body) > BODY_CHARS
    return body[:BODY_CHARS], scope


class GmailCache:
    def __init__(self, workspace):
        self.root = Path(workspace) / 'mail'
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.path = self.root / 'gmail.db'
        with closing(sqlite3.connect(self.path, timeout=30)) as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS messages(
                    account TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                    internal_date INTEGER NOT NULL, record TEXT NOT NULL,
                    PRIMARY KEY(account,message_id)
                );
                CREATE INDEX IF NOT EXISTS messages_date ON messages(account,internal_date DESC);
                CREATE TABLE IF NOT EXISTS accounts(
                    account TEXT PRIMARY KEY, query TEXT NOT NULL,
                    has_more INTEGER NOT NULL, captured_at TEXT NOT NULL
                );
            ''')
            c.commit()
        self._secure()

    def _secure(self):
        for suffix in ('', '-wal', '-shm'):
            try:
                os.chmod(str(self.path) + suffix, 0o600)
            except FileNotFoundError:
                pass

    @contextmanager
    def db(self, write=False):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        if not write:
            c.execute('PRAGMA query_only=ON')
        try:
            c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
            self._secure()

    @staticmethod
    def _normalize(message, account, query, has_more, captured):
        if not isinstance(message, dict) or set(message) - {'id', 'thread_id', 'label_ids', 'snippet', 'history_id', 'internal_date', 'payload', 'size_estimate', 'classification_label_values'}:
            raise ValueError('Gmail 邮件字段无效')
        mid = mail_id(message.get('id'), '邮件编号')
        thread = mail_id(message.get('thread_id'), '会话编号')
        date = message.get('internal_date')
        if not isinstance(date, str) or not re.fullmatch('[0-9]{1,15}', date):
            raise ValueError('Gmail internal_date 须为 Unix 毫秒字符串')
        milliseconds = int(date)
        if milliseconds > 253402300799999:
            raise ValueError('Gmail internal_date 超出日期范围')
        modified = (EPOCH + timedelta(milliseconds=milliseconds)).isoformat(timespec='milliseconds')
        labels = message.get('label_ids', [])
        if not isinstance(labels, list) or len(labels) > 100 or any(not isinstance(label, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', label) for label in labels):
            raise ValueError('Gmail 标签格式无效')
        snippet = bounded_text(message.get('snippet', ''), '邮件预览', 10000)
        history_id = message.get('history_id', '')
        if not isinstance(history_id, str) or history_id and not re.fullmatch('[0-9]{1,30}', history_id):
            raise ValueError('Gmail history_id 格式无效')
        if 'size_estimate' in message:
            integer(message['size_estimate'], '邮件估计大小', MAIL_BYTES)
        payload = message.get('payload')
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError('Gmail MIME payload 格式无效')
        hs = headers(payload)
        body, scope = render_body(payload)
        raw_date = header_value(hs, 'date')
        date_sent = ''
        if raw_date:
            try:
                parsed = parsedate_to_datetime(raw_date)
                if parsed.utcoffset() is not None:
                    date_sent = raw_date
            except (ValueError, TypeError, OverflowError):
                pass
        scope.update(query=query, has_more=has_more, date_sent_missing_or_invalid=not bool(date_sent))
        details = ['仅本次查询获取的邮件正文；缓存可能保留此前批次，未证明全邮箱已同步']
        if scope['missing']:
            details.append('正文缺失，预览不作为完整正文')
        elif scope['body_source'] == 'text/html':
            details.append('HTML 已转为文字，script/style 已去除')
        if scope['truncated']:
            details.append('正文已截断至 100,000 字')
        if scope['attachments_not_read']:
            details.append('附件未读取')
        if scope['decoding_replaced']:
            details.append('正文解码存在替换字符，请打开原邮件核对')
        if not date_sent:
            details.append('发送日期缺失或无有效时区；参考时间使用 Gmail internal_date')
        scope['detail'] = '；'.join(details)
        return {
            'message_id': mid, 'thread_id': thread, 'account': account,
            'subject': decode_header_value(header_value(hs, 'subject')),
            'from': decode_header_value(header_value(hs, 'from')),
            'to': decode_header_value(header_value(hs, 'to', all_values=True)),
            'date_sent': date_sent, 'date_header': raw_date,
            'rfc_message_id': header_value(hs, 'message-id'),
            'body': body, 'snippet': snippet, 'internal_date': date, 'history_id': history_id,
            'source_url': 'https://mail.google.com/mail/u/?' + urlencode({'authuser': account}) + '#all/' + mid,
            'modified': modified, 'captured_at': captured, 'content_scope': scope,
            'labels': list(dict.fromkeys(labels)),
        }

    def import_messages(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'account', 'query', 'messages', 'has_more', 'captured_at'}:
            raise ValueError('Gmail 缓存批次参数无效')
        account = account_address(payload.get('account'))
        query = bounded_text(payload.get('query'), 'Gmail 查询范围', 2000)
        if type(payload.get('has_more')) is not bool:
            raise ValueError('has_more 须明确为布尔值')
        has_more = payload['has_more']
        captured = captured_time(payload.get('captured_at'))
        messages = payload.get('messages')
        if not isinstance(messages, list) or len(messages) > 100:
            raise ValueError('每次最多缓存 100 封 Gmail 邮件')
        normalized, total = [], 0
        for message in messages:
            try:
                size = len(json.dumps(message, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'))
            except (TypeError, ValueError, UnicodeError):
                raise ValueError('Gmail MIME 须为有效 JSON 数据') from None
            if size > MAIL_BYTES:
                raise ValueError('单封邮件 MIME 超过 2 MiB')
            total += size
            if total > BATCH_BYTES:
                raise ValueError('本批邮件 MIME 超过 10 MiB')
            normalized.append(self._normalize(message, account, query, has_more, captured))
        with self.db(write=True) as c:
            for record in normalized:
                c.execute('''INSERT INTO messages(account,message_id,thread_id,internal_date,record) VALUES(?,?,?,?,?)
                    ON CONFLICT(account,message_id) DO UPDATE SET thread_id=excluded.thread_id,
                    internal_date=excluded.internal_date,record=excluded.record''',
                    (account, record['message_id'], record['thread_id'], int(record['internal_date']), json.dumps(record, ensure_ascii=False)))
            c.execute('''INSERT INTO accounts(account,query,has_more,captured_at) VALUES(?,?,?,?)
                ON CONFLICT(account) DO UPDATE SET query=excluded.query,has_more=excluded.has_more,captured_at=excluded.captured_at''',
                (account, query, int(has_more), captured))
        return self.status(account)

    def records(self, account):
        account = account_address(account)
        with self.db() as c:
            return [json.loads(r['record']) for r in c.execute(
                'SELECT record FROM messages WHERE account=? ORDER BY internal_date DESC,message_id', (account,))]

    def status(self, account):
        account = account_address(account)
        with self.db() as c:
            row = c.execute('SELECT * FROM accounts WHERE account=?', (account,)).fetchone()
            count = c.execute('SELECT COUNT(*) FROM messages WHERE account=?', (account,)).fetchone()[0]
        return {'account': account, 'count': count, 'query': row['query'] if row else '',
            'has_more': bool(row['has_more']) if row else False, 'captured_at': row['captured_at'] if row else '',
            'scope': '本机累计缓存；query/has_more 描述最近一次查询，不代表全邮箱覆盖' if row else '本机尚未导入该账号邮件，不代表邮箱为空'}
