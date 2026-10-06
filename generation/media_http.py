"""HTTP transport for the 4090 media API.

Media jobs, reference uploads, progress and results travel over one HTTPS entry
point (media.example.com/media/v1 -> FRP STCP visitor -> 127.0.0.1:8200).
This module deliberately has **no SSH fallback**: a transport failure is an
unknown state, never a licence to submit the same work twice over another path.

Configuration (server side only; never sent to the browser):
  MEDIA_API_BASE       e.g. https://media.example.com/media/v1
  MEDIA_API_KEY_FILE   600 file holding the bearer token
  MEDIA_API_CONFIG     optional json {'base': ..., 'key_file': ...}
TLS verification is always on; plaintext is only accepted for loopback tests.
"""
import hashlib
import http.client
import json
import os
import socket
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The workbench keeps its short model ids; the media API exposes explicit names.
MODEL_IDS = {'qi21': 'qwen-image-2.1', 'h3': 'minimax-h3'}
API_MODEL_IDS = {value: key for key, value in MODEL_IDS.items()}


class MediaApiError(ValueError):
    """The API answered and rejected the request (or returned a non-2xx we can read)."""


class MediaApiUnknown(MediaApiError):
    """Transport failed or the server answered 5xx: the job state is unknown.

    Callers must keep the idempotency key and reconcile the same API job later;
    they must never resubmit blindly and must never fall back to SSH.
    """

    def __init__(self, message, state='unknown'):
        super().__init__(message)
        self.state = state


def _loopback(host):
    return host in ('127.0.0.1', 'localhost', '::1')


def resolve_config(base=None, key_file=None):
    """Resolve base url + bearer token. Missing configuration is an explicit error."""
    if not base:
        base = os.environ.get('MEDIA_API_BASE', '').strip()
    if not key_file:
        key_file = os.environ.get('MEDIA_API_KEY_FILE', '').strip()
    if not base or not key_file:
        path = os.environ.get('MEDIA_API_CONFIG', '').strip() or str(
            Path.home() / '.config' / 'article-studio' / 'media_api.json')
        try:
            document = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            document = {}
        if not isinstance(document, dict):
            document = {}
        base = base or str(document.get('base', '')).strip()
        key_file = key_file or str(document.get('key_file', '')).strip()
    if not base:
        raise MediaApiError(
            '未配置媒体服务地址（MEDIA_API_BASE）。媒体任务不再通过 SSH 提交，'
            '请先配置 HTTPS 入口，例如 https://media.example.com/media/v1')
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == 'http' and not _loopback(parts.hostname or ''):
        raise MediaApiError('MEDIA_API_BASE 非本机地址时必须使用 https')
    if parts.scheme not in ('https', 'http') or not parts.netloc:
        raise MediaApiError('MEDIA_API_BASE 不是合法的 http/https 地址')
    base = base.rstrip('/')
    if not key_file:
        key_file = str(Path.home() / '.config' / 'article-studio' / 'media-api.key')
    path = Path(key_file)
    try:
        mode = path.stat().st_mode
    except OSError:
        raise MediaApiError(f'未找到媒体服务密钥文件 {key_file}（权限须为 600）') from None
    if mode & 0o077:
        raise MediaApiError(f'密钥文件 {key_file} 权限过宽，须为 600 后才能使用')
    try:
        token = path.read_text().strip()
    except OSError:
        raise MediaApiError(f'无法读取媒体服务密钥文件 {key_file}') from None
    if not token:
        raise MediaApiError(f'媒体服务密钥文件 {key_file} 为空')
    return base, token


class MediaApi:
    """Small, explicit HTTP client. One instance per workbench process."""

    def __init__(self, base=None, key_file=None, timeout=20, opener=None):
        self.base, self.token = resolve_config(base, key_file)
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self._last_error = None
        self._checked_at = None

    # ---------- plumbing ----------
    def headers(self, extra=None, auth=True):
        values = {'Accept': 'application/json', 'User-Agent': 'article-studio/1.0'}
        if auth:
            values['Authorization'] = 'Bearer ' + self.token
        values.update(extra or {})
        return values

    def _call(self, method, path, body=None, headers=None, timeout=None, max_bytes=None):
        """One request. Reading the body is inside the same error classification, so a
        connection lost after the response headers is an unknown state, not a failure."""
        url = self.base + path
        request = urllib.request.Request(url, data=body, method=method,
                                         headers=self.headers(headers))
        try:
            response = self.opener.open(request, timeout=timeout or self.timeout)
            try:
                status, message = response.status, response.headers
                length = int(message.get('Content-Length') or 0)
                if max_bytes is not None and length and length > max_bytes:
                    raise MediaApiUnknown(f'媒体服务响应体超出上限（{length} 字节）', state='unknown')
                payload = response.read(max_bytes) if max_bytes else response.read()
            except MediaApiUnknown:
                raise
            except (http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                    ssl.SSLError) as exc:
                raise MediaApiUnknown(
                    f'响应接收不完整（{exc.__class__.__name__}）；服务器可能已处理该请求',
                    state='unknown') from None
            finally:
                response.close()
        except urllib.error.HTTPError as exc:
            raw = b''
            try:
                raw = exc.read(4000)
            except Exception:
                pass
            try:
                exc.close()          # release the error body instead of leaking it to GC
            except Exception:
                pass
            message = ''
            try:
                message = str(json.loads(raw).get('error', ''))[:400]
            except ValueError:
                message = raw.decode(errors='replace')[:200]
            if exc.code in (408, 429) or exc.code >= 500:
                # the request may have been executed; treat it as unknown state
                raise MediaApiUnknown(
                    f'媒体服务暂时不可用（HTTP {exc.code}）{("：" + message) if message else ""}；'
                    '已提交的任务不会自动重复提交', state='unknown') from None
            self._last_error = f'HTTP {exc.code}'
            raise MediaApiError(message or f'媒体服务返回 HTTP {exc.code}') from None
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                ssl.SSLError) as exc:
            reason = getattr(exc, 'reason', exc)
            raise MediaApiUnknown(
                f'无法确认媒体服务是否收到请求（{reason}）；已提交的任务不会自动重复提交',
                state='unknown') from None
        return status, message, payload

    def get_json(self, path, params=None, timeout=None):
        query = ('?' + urllib.parse.urlencode(params)) if params else ''
        status, _, payload = self._call('GET', path + query, timeout=timeout)
        try:
            return json.loads(payload)
        except ValueError:
            raise MediaApiUnknown(f'媒体服务 {path} 返回了无法解析的响应', state='unknown') from None

    def post_json(self, path, payload, headers=None, timeout=None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        values = {'Content-Type': 'application/json'}
        values.update(headers or {})
        status, _, raw = self._call('POST', path, body=body, headers=values, timeout=timeout)
        try:
            return status, json.loads(raw) if raw else {}
        except ValueError:
            raise MediaApiUnknown(f'媒体服务 {path} 的响应无法解析', state='unknown') from None

    # ---------- read-only contracts ----------
    def liveness(self):
        """Unauthenticated process liveness only; never implies GPU or model readiness."""
        return self.get_json('/health', timeout=10)

    def status(self):
        return self.get_json('/status', timeout=45)

    def models(self):
        return self.get_json('/models', timeout=30)

    def backends(self, action='health', model=None):
        """model is a workbench label (qi21/h3/krea2) or None for all fixed services."""
        params = {}
        if model:
            params['model'] = model
        return self.get_json('/backends/' + action, params or None, timeout=60)

    def job(self, api_job_id):
        return self.get_json('/jobs/' + api_job_id, timeout=20)

    def jobs(self):
        return self.get_json('/jobs', timeout=30).get('jobs', [])

    def find_by_request(self, request_id):
        """Adopt an API job whose submit response was lost. Never creates a job."""
        if not request_id:
            return None
        for item in self.jobs():
            if item.get('request_id') == request_id:
                return item
        return None

    def cancel(self, api_job_id):
        return self.post_json('/jobs/' + api_job_id + '/cancel', {}, timeout=30)[1]

    # ---------- references ----------
    def upload(self, path, kind, name, meta=None, timeout=180):
        """Raw-body upload of one normalised local asset; returns the API file record."""
        source = Path(path)
        length = source.stat().st_size
        if length <= 0:
            raise MediaApiError('参考素材为空，未上传')
        digest = hashlib.sha256()
        with source.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        headers = {'X-File-Kind': kind,
                   'X-File-Name': urllib.parse.quote(str(name)[:120]),
                   'X-Sha256': digest.hexdigest(),
                   'Content-Type': 'application/octet-stream',
                   'Content-Length': str(length)}
        if meta:
            headers['X-File-Meta'] = json.dumps(meta, ensure_ascii=False)
        status, record = self._upload_stream(source, headers, timeout)
        if not record.get('file_id'):
            raise MediaApiUnknown('素材上传响应缺少 file_id，未确认登记', state='unknown')
        return record

    def _upload_stream(self, source, headers, timeout):
        request = urllib.request.Request(self.base + '/files', method='POST',
                                         headers=self.headers(headers))
        # urllib's Request.data setter drops any Content-length header it holds (issue
        # 16464), which silently downgraded this request to Transfer-Encoding: chunked.
        # The 4090 API sizes the upload from Content-Length, so attach the body first and
        # declare the length afterwards; the file still streams in blocks on the wire.
        handle = source.open('rb')
        try:
            request.data = handle
            request.add_unredirected_header('Content-Length', headers['Content-Length'])
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = b''
            try:
                raw = exc.read(2000)
            except Exception:
                pass
            try:
                exc.close()
            except Exception:
                pass
            try:
                message = str(json.loads(raw).get('error', ''))[:400]
            except ValueError:
                message = raw.decode(errors='replace')[:200]
            if exc.code >= 500 or exc.code in (408, 429):
                raise MediaApiUnknown(f'素材上传失败（HTTP {exc.code}）{("：" + message) if message else ""}',
                                      state='unknown') from None
            raise MediaApiError(message or f'素材上传失败（HTTP {exc.code}）') from None
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                ssl.SSLError) as exc:
            raise MediaApiUnknown(f'素材上传中断（{getattr(exc, "reason", exc)}），生成尚未提交',
                                  state='unknown') from None
        finally:
            handle.close()
        try:
            return status, json.loads(raw) if raw else {}
        except ValueError:
            raise MediaApiUnknown('素材上传响应无法解析，未确认登记', state='unknown') from None

    # ---------- jobs ----------
    def submit(self, params, request_id, timeout=45):
        """Create one API job. A stable request_id makes retries and restarts safe."""
        payload = dict(params)
        payload['request_id'] = request_id
        status, job = self.post_json('/jobs', payload,
                                     headers={'Idempotency-Key': str(request_id)}, timeout=timeout)
        if not job.get('id'):
            raise MediaApiUnknown('任务提交响应缺少任务编号，未确认是否已排队', state='unknown')
        return job

    def result_meta(self, api_job_id):
        """HEAD the result route: content type, server sha256 and byte count."""
        _, message, _ = self._call('HEAD', '/jobs/' + api_job_id + '/result', timeout=30)
        try:
            size = int(message.get('Content-Length') or 0)
        except ValueError:
            size = 0
        return {'content_type': message.get('Content-Type') or '',
                'sha256': (message.get('X-Content-Sha256') or '').lower(), 'bytes': size}

    def download_result(self, api_job_id, destination, expect_sha256=None, expect_bytes=None,
                        timeout=600, max_bytes=512 * 1024 * 1024):
        """Stream the API output to a local file: atomic write + sha256 verification."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / ('.' + destination.name + '-' + os.urandom(8).hex())
        digest = hashlib.sha256()
        received = 0
        claimed = ''
        try:
            request = urllib.request.Request(self.base + '/jobs/' + api_job_id + '/result',
                                             headers=self.headers({'Accept': 'application/octet-stream'}))
            try:
                response = self.opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                try:
                    exc.close()
                except Exception:
                    pass
                if exc.code >= 500 or exc.code in (408, 429):
                    raise MediaApiUnknown(f'结果下载失败（HTTP {exc.code}）', state='unknown') from None
                raise MediaApiError(f'结果下载失败（HTTP {exc.code}）') from None
            except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError,
                    OSError, ssl.SSLError) as exc:
                raise MediaApiUnknown(f'结果下载中断（{getattr(exc, "reason", exc)}）', state='unknown') from None
            try:
                with response:
                    # email.message headers are case-insensitive, so a lowercase
                    # x-content-sha256 from any proxy is still found.
                    claimed = (response.headers.get('X-Content-Sha256') or '').lower()
                    with temporary.open('wb') as handle:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > max_bytes:
                                raise MediaApiUnknown('结果体积超出上限，未标记完成', state='unknown')
                            handle.write(chunk)
                            digest.update(chunk)
            except MediaApiUnknown:
                raise
            except (http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                    ssl.SSLError) as exc:
                raise MediaApiUnknown(
                    f'结果下载在响应头之后中断（{exc.__class__.__name__}）', state='unknown') from None
            if claimed and claimed != digest.hexdigest():
                raise MediaApiUnknown('结果下载校验不一致（传输损坏），未标记完成', state='unknown')
            if expect_sha256 and expect_sha256.lower() != digest.hexdigest():
                raise MediaApiUnknown('结果与服务器记录的 sha256 不一致，未标记完成', state='unknown')
            if expect_bytes not in (None, received):
                raise MediaApiUnknown('结果字节数与服务器记录不一致，未标记完成', state='unknown')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return {'bytes': received, 'sha256': digest.hexdigest(), 'content_sha256': claimed}

    def events(self, api_job_id, since, on_event, stop, max_seconds=3600):
        """Consume the API's SSE progress stream. Returns the last seen event id."""
        last = since
        deadline = time.monotonic() + max_seconds
        query = '?' + urllib.parse.urlencode({'since': last})
        request = urllib.request.Request(self.base + '/jobs/' + api_job_id + '/events' + query,
                                         headers=self.headers({'Accept': 'text/event-stream'}))
        try:
            response = self.opener.open(request, timeout=60)
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, OSError,
                ssl.SSLError) as exc:
            raise MediaApiUnknown(f'进度流无法建立（{getattr(exc, "reason", exc)}）', state='unknown') from None
        pending = {}
        try:
            while time.monotonic() < deadline and not stop.is_set():
                try:
                    raw = response.readline()
                except (http.client.HTTPException, socket.timeout, TimeoutError, OSError) as exc:
                    # A dropped stream is not a lost job: report it and let the caller
                    # reconnect with the last acknowledged event id.
                    raise MediaApiUnknown(f'进度流中断（{exc.__class__.__name__}）', state='unknown') from None
                if not raw:
                    break
                line = raw.decode(errors='replace').rstrip('\r\n')
                if not line:
                    # blank line terminates one SSE event record
                    if pending.get('data') is None:
                        pending = {}
                        continue
                    try:
                        envelope = json.loads(pending['data'])
                    except ValueError:
                        pending = {}
                        continue
                    seq = envelope.get('seq') or pending.get('id') or last
                    try:
                        last = int(seq)
                    except (TypeError, ValueError):
                        pass
                    on_event({'type': pending.get('event') or envelope.get('type'),
                              'seq': last, 'ts': envelope.get('ts'), 'data': envelope.get('data') or {}})
                    pending = {}
                    continue
                if line.startswith(': '):
                    continue
                field, _, value = line.partition(': ')
                if field in ('id', 'event'):
                    pending[field] = value.strip()
                elif field == 'data':
                    pending['data'] = value
        finally:
            response.close()
        return last
