"""Dedicated, bounded remio note tools; no arbitrary CLI or syscall is exposed."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import quote


DEFAULT_REMIO = shutil.which('remio') or str(Path.home()/'.local/bin/remio')
OUTPUT_LIMIT = 8000
RAW_OUTPUT_LIMIT = 1024 * 1024
CLIENT_ID = 'article-studio-pi'


class RemioError(ValueError):
    """Only fixed, non-sensitive diagnostics may cross the tool boundary."""
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def integer(value, default, minimum, maximum, label):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{label}必须为 {minimum}–{maximum} 的整数')
    return value


def identity(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError(f'{label}格式无效')
    return value


def text_value(value, maximum, label, empty=False, multiline=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f'{label}须为{("最多" if empty else " 1–")}{maximum} 个字符')
    allowed = '\n\r\t' if multiline else ''
    if any((ord(char) < 32 or ord(char) == 127) and char not in allowed for char in value):
        raise ValueError(f'{label}不能包含控制字符')
    return value


class RemioTools:
    def __init__(self, workspace, session_id=None, command=None):
        self.workspace = Path(workspace).resolve()
        self.request_db = self.workspace / 'remio-requests.sqlite3'
        self.session_id = session_id
        # An absolute default also works when launchd has a minimal PATH.
        self.command = command or os.environ.get('ARTICLE_STUDIO_REMIO') or DEFAULT_REMIO
        self.timeout = 30

    def available(self):
        return bool(isinstance(self.command, str) and os.path.isabs(self.command)
                    and os.path.isfile(self.command) and os.access(self.command, os.X_OK))

    def capabilities(self):
        """Installation metadata only; connection state requires status()."""
        return {'provider': 'remio', 'installed': self.available(),
                'tools': ['remio_status', 'remio_search', 'remio_read', 'remio_create_note', 'remio_update_note'],
                'scope': '按问题检索知识库，分页读取指定笔记；明确授权后新建或追加笔记',
                'requires': 'remio 桌面应用运行；连接须由实时工具调用验证',
                'write_context': '稳定会话标识和持久化请求去重'}

    @staticmethod
    def _args(args, allowed):
        if not isinstance(args, dict) or any(key not in allowed for key in args):
            raise ValueError('remio 工具参数无效')

    @staticmethod
    def _protect(value, job):
        key = (job.get('model_config') or {}).get('api_key')
        if isinstance(key, str) and key and key in value:
            raise ValueError('remio 请求不能包含模型密钥')

    @staticmethod
    def _redact(value, job):
        key = (job.get('model_config') or {}).get('api_key')
        if isinstance(key, str) and key:
            return value.replace(key, '[凭据已隐藏]')
        return value

    def _call(self, args, job):
        if not self.available():
            raise RemioError('remio CLI 未就绪，请检查本机安装', 'unavailable')
        if job.get('cancel'):
            raise RemioError('任务已停止', 'cancelled')
        count = job.get('remio_calls', 0) + 1
        if count > 12:
            raise RemioError('本轮 remio 查询已到上限，请下一条消息继续', 'call_limit')
        job['remio_calls'] = count
        process = None
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen([self.command, *args], shell=False,
                                           stdin=subprocess.DEVNULL, stdout=output,
                                           stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + self.timeout
                while process.poll() is None:
                    if job.get('cancel'):
                        raise RemioError('任务已停止', 'cancelled')
                    if time.monotonic() >= deadline:
                        raise RemioError('remio 响应超时，请稍后核对服务', 'timeout')
                    if os.fstat(output.fileno()).st_size > RAW_OUTPUT_LIMIT:
                        raise RemioError('remio 返回内容过大，请缩小读取范围', 'output_limit')
                    time.sleep(.05)
                if process.returncode != 0:
                    raise RemioError('remio 服务调用失败，请检查桌面应用连接', 'exit_error')
                output.seek(0)
                raw = output.read(RAW_OUTPUT_LIMIT + 1)
                if len(raw) > RAW_OUTPUT_LIMIT:
                    raise RemioError('remio 返回内容过大，请缩小读取范围', 'output_limit')
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise RemioError('remio 未返回有效 JSON，请检查桌面应用连接', 'invalid_json') from None
                if not isinstance(result, dict) or result.get('ok') is not True:
                    raise RemioError('remio 服务未完成请求，请检查桌面应用连接', 'service_error')
                if not isinstance(result.get('data'), dict):
                    raise RemioError('remio 返回格式异常，请稍后重试', 'invalid_data')
                return result['data']
            except OSError:
                raise RemioError('无法启动 remio CLI，请检查本机安装', 'start_error') from None
            finally:
                if process and process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                            process.wait(timeout=1)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    except OSError:
                        pass

    @staticmethod
    def _url(note_id):
        return 'http://127.0.0.1:17624/notes/' + quote(note_id, safe='')

    @staticmethod
    def _size(result):
        return len(json.dumps(result, ensure_ascii=False, separators=(',', ':')))

    def status(self, args=None, job=None):
        self._args(args if args is not None else {}, set())
        result = {'ok': False, 'provider': 'remio', 'available': False, 'query_performed': False,
                  'installed': self.available(), 'connected': False,
                  'scope': '按问题搜索知识库、分页读取指定笔记；明确要求保存时才写入',
                  'write_context': '稳定会话标识和持久化请求去重',
                  'summary': 'remio CLI 未就绪'}
        if not result['installed']:
            return result
        try:
            # This probe returns no private note, account or model configuration.
            self._call(['syscall', 'get_local_time', '{}'], job if job is not None else {})
        except RemioError as error:
            result['error_code'] = error.code
            result['summary'] = str(error)
        else:
            result['ok'] = True
            result['available'] = True
            result['connected'] = True
            result['summary'] = 'remio 桌面服务已通过实时只读探测'
        return result

    def search(self, args, job):
        self._args(args, {'query', 'limit'})
        query = text_value(args.get('query'), 1200, '搜索词').strip()
        limit = integer(args.get('limit'), 8, 1, 8, '搜索结果数')
        self._protect(query, job)
        # Deliberately no type filter: files, mail, Slack and notes are searchable.
        data = self._call(['search_notes', '--query', query, '--limit', str(limit)], job)
        if data.get('query_search_method') == 'failed':
            raise RemioError('remio 语义检索失败，不能将此结果视为没有资料', 'search_failed')
        entries = data.get('results')
        if not isinstance(entries, list):
            raise RemioError('remio 搜索返回格式异常', 'invalid_data')
        results = []
        for entry in entries[:limit]:
            if not isinstance(entry, dict):
                raise RemioError('remio 搜索返回格式异常', 'invalid_data')
            try:
                note_id = identity(entry.get('noteId'), '笔记编号')
            except ValueError:
                raise RemioError('remio 搜索返回笔记编号异常', 'invalid_data') from None
            self._protect(note_id, job)
            if any(entry.get(key) is not None and not isinstance(entry.get(key), str)
                   for key in ('title', 'preview')):
                raise RemioError('remio 搜索返回片段格式异常', 'invalid_data')
            title = self._redact(str(entry.get('title') or note_id), job)[:160]
            preview = self._redact(str(entry.get('preview') or ''), job)[:1000]
            results.append({'noteId': note_id, 'title': title, 'url': self._url(note_id),
                            'preview': preview})
        sources = [{'noteId': row['noteId'], 'title': row['title'][:120], 'url': row['url']}
                   for row in results]
        total = data.get('total')
        result = {'ok': True, 'provider': 'remio', 'available': True, 'query_performed': True,
                  'query': query, 'results': results, 'sources': sources,
                  'returned': len(results), 'empty': not results,
                  'summary': f'找到 {len(results)} 条 remio 资料；仅返回片段，需读取指定笔记核实'}
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            result['total'] = total
        while self._size(result) > OUTPUT_LIMIT:
            largest = max(results, key=lambda row: len(row['preview']), default=None)
            if not largest or not largest['preview']:
                raise RemioError('remio 搜索元数据超过输出上限，请减少结果数', 'output_limit')
            overflow = self._size(result) - OUTPUT_LIMIT
            largest['preview'] = largest['preview'][:-max(1, overflow)]
            result['truncated'] = True
        return result

    def read(self, args, job):
        self._args(args, {'note_id', 'offset', 'limit', 'max_chars'})
        note_id = identity(args.get('note_id'), '笔记编号')
        self._protect(note_id, job)
        offset = integer(args.get('offset'), 1, 1, 100000, '起始行')
        limit = integer(args.get('limit'), 80, 1, 200, '读取行数')
        max_chars = integer(args.get('max_chars'), 4000, 1, 8000, '读取字符数')
        data = self._call(['read_note', note_id, '--format', 'md', '--offset', str(offset),
                           '--limit', str(limit)], job)
        if (data.get('noteId') != note_id or not isinstance(data.get('content'), str)
                or data.get('title') is not None and not isinstance(data.get('title'), str)):
            raise RemioError('remio 笔记读取返回格式异常', 'invalid_data')
        title = self._redact(str(data.get('title') or note_id), job)[:160]
        content = self._redact(data['content'], job)
        url = self._url(note_id)
        result = {'ok': True, 'provider': 'remio', 'available': True, 'query_performed': True,
                  'noteId': note_id, 'title': title, 'url': url,
                  'content': '', 'offset': offset, 'limit': limit, 'lines_returned': 0,
                  'truncated': False, 'partial_line': False, 'next_offset': None,
                  'sources': [{'noteId': note_id, 'title': title[:120], 'url': url}],
                  'summary': f'已读取 remio 笔记「{title}」的指定行段'}
        allowed = min(max_chars, max(1, OUTPUT_LIMIT - self._size(result) - 80))
        selected = content[:allowed]
        char_truncated = len(selected) < len(content)
        if char_truncated:
            boundary = selected.rfind('\n')
            if boundary >= 0:
                selected = selected[:boundary + 1]
            else:
                result['partial_line'] = True
        # Escaped quotes/control characters also count in the JSON output budget.
        while self._size({**result, 'content': selected}) > OUTPUT_LIMIT:
            selected = selected[:-max(1, self._size({**result, 'content': selected}) - OUTPUT_LIMIT)]
            char_truncated = True
            result['partial_line'] = bool(selected and not selected.endswith('\n'))
        lines = len(selected.splitlines())
        more_lines = len(content.splitlines()) >= limit
        result.update(content=selected, lines_returned=lines,
                      truncated=char_truncated or more_lines)
        if char_truncated:
            # A partial long line has no reliable next line; do not skip its tail.
            result['next_offset'] = offset + selected.count('\n') if '\n' in selected else None
        elif more_lines:
            result['next_offset'] = offset + lines
        if result['partial_line']:
            result['summary'] = '所选行超过字符上限，仅返回部分内容；不能据此判断整篇笔记'
        return result

    def _session(self, job):
        value = job.get('conversation_id') or self.session_id
        if not value:
            raise ValueError('remio 写入需要稳定会话标识')
        return identity(value, '会话标识')

    def _connect_requests(self):
        connection = None
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.request_db), timeout=5)
            os.chmod(self.request_db, 0o600)
            connection.execute('CREATE TABLE IF NOT EXISTS remio_requests '
                               '(request_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, '
                               'state TEXT NOT NULL, result_json TEXT, updated REAL NOT NULL)')
            return connection
        except (OSError, sqlite3.Error):
            if connection is not None:
                connection.close()
            raise RemioError('无法建立 remio 写入去重记录，已停止写入', 'dedup_error') from None

    def _reserve(self, key, fingerprint):
        connection = self._connect_requests()
        try:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute('SELECT payload_hash,state,result_json FROM remio_requests '
                                          'WHERE request_key=?', (key,)).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise ValueError('同一 remio 写入请求编号不能用于不同内容')
                if existing[1] == 'done':
                    try:
                        result = json.loads(existing[2])
                    except (TypeError, ValueError):
                        raise RemioError('remio 写入去重记录无效，已停止写入', 'dedup_error') from None
                    if not isinstance(result, dict):
                        raise RemioError('remio 写入去重记录无效，已停止写入', 'dedup_error')
                    result['deduplicated'] = True
                    return result
                raise RemioError('此前 remio 写入结果尚未确认，已阻止重复提交；请先在 remio 核对',
                                 'write_unconfirmed')
            connection.execute('INSERT INTO remio_requests VALUES (?,?,?,NULL,?)',
                               (key, fingerprint, 'pending', time.time()))
            connection.commit()
            return None
        except (sqlite3.Error, ValueError) as error:
            if isinstance(error, ValueError):
                raise
            raise RemioError('remio 写入去重记录不可用，已停止写入', 'dedup_error') from None
        finally:
            connection.close()

    def _complete(self, key, result):
        connection = self._connect_requests()
        try:
            connection.execute('UPDATE remio_requests SET state=?,result_json=?,updated=? WHERE request_key=?',
                               ('done', json.dumps(result, ensure_ascii=False), time.time(), key))
            connection.commit()
        except sqlite3.Error:
            raise RemioError('remio 已响应但去重记录保存失败，请核对笔记后再继续', 'write_unconfirmed') from None
        finally:
            connection.close()

    def _write(self, action, payload, request_id, job):
        session = self._session(job)
        request_id = identity(request_id, '写入请求编号')
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        for value in payload.values():
            self._protect(value, job)
        self._protect(raw, job)
        if job.get('cancel'):
            raise RemioError('任务已停止', 'cancelled')
        key = hashlib.sha256((session + '\0' + request_id).encode()).hexdigest()
        fingerprint = hashlib.sha256((action + '\0' + raw).encode()).hexdigest()
        previous = self._reserve(key, fingerprint)
        if previous is not None:
            return previous
        # A reserved request remains pending after any uncertain result. Never
        # retry a potentially completed mutation automatically.
        data = self._call(['syscall', action, raw, '--client', CLIENT_ID,
                           '--client-session-id', session], job)
        try:
            note_id = identity(data.get('noteId'), '笔记编号')
        except ValueError:
            raise RemioError('remio 写入返回笔记编号异常，请核对笔记', 'write_unconfirmed') from None
        self._protect(note_id, job)
        if action == 'update_note' and note_id != payload['noteId']:
            raise RemioError('remio 写入返回笔记编号不匹配，请核对笔记', 'write_unconfirmed')
        if data.get('title') is not None and not isinstance(data.get('title'), str):
            raise RemioError('remio 写入返回标题异常，请核对笔记', 'write_unconfirmed')
        title = self._redact(str(data.get('title') or payload.get('title') or note_id), job)[:160]
        url = self._url(note_id)
        result = {'ok': True, 'provider': 'remio', 'available': True, 'query_performed': False,
                  'noteId': note_id, 'title': title, 'url': url,
                  'deduplicated': False, 'sources': [{'noteId': note_id, 'title': title, 'url': url}],
                  'summary': f'已{("保存" if action == "create_note" else "更新")} remio 笔记「{title}」'}
        self._complete(key, result)
        return result

    def create(self, args, job):
        self._args(args, {'title', 'content', 'request_id'})
        title = text_value(args.get('title'), 240, '笔记标题').strip()
        content = text_value(args.get('content'), 32000, '笔记正文', empty=True, multiline=True)
        if content.splitlines() and re.fullmatch(r'\s*#\s+' + re.escape(title) + r'\s*', content.splitlines()[0]):
            raise ValueError('笔记标题单独保存，正文不要重复以相同 H1 标题开头')
        return self._write('create_note', {'title': title, 'content': content}, args.get('request_id'), job)

    def update(self, args, job):
        self._args(args, {'note_id', 'append_text', 'request_id'})
        note_id = identity(args.get('note_id'), '笔记编号')
        append = text_value(args.get('append_text'), 32000, '追加正文', multiline=True)
        if append.lstrip().startswith('---\n') or append.lstrip().startswith('---\r\n'):
            raise ValueError('追加 remio 正文时不要包含 read_note 返回的 YAML 元数据')
        return self._write('update_note', {'noteId': note_id, 'append': append}, args.get('request_id'), job)
