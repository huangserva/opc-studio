"""创意回收工厂 · 第一期：投喂 → 解析 → 入库。

铁律：原样存，不判断、不改写、不补全。收藏理由与原文分开保存，绝不混进正文。
链接抓取复用 assistant_web（remio 后端）的 call/fetch 边界；抓不到就如实记失败，
text 保持为空，不产生半截正文。图片与视频第一版只存本机文件与元数据，
原始正文继续明示「未识别／未读取」；识别结果存独立字段，入库后异步处理。
"""
import hashlib
import json
import math
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from assistant_web import WebTools, public_url
import collect_links
from external_reference import existing_legacy_document

KINDS = ('link', 'text', 'image', 'video')
INTAKE_CHANNEL = '创意回收工厂'
MAX_TEXT = 100_000
MAX_REASON = 2_000
MAX_TITLE = 500
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_VIDEO_BYTES = 1024 ** 3
IMAGE_FORMATS = ('PNG', 'JPEG', 'WEBP')
IMAGE_SUFFIXES = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}
VIDEO_SUFFIXES = ('.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi')
# One fetch page is what assistant_web already allows; the page count stays inside
# the same per-round call ceiling that WebTools.call enforces.
PAGE_CHARS = 16_000
MAX_PAGES = 6
ID_PATTERN = re.compile(r'[a-f0-9]{32}')
# delete() 的 ledger_live 默认值：未指定时按单条删除现算链接快照；批量删除会显式传快照。
_LEDGER_LIVE_UNSET = object()


def model_busy_class():
    """ModelBusy 住在调度器里；延迟导入，不让回收站模块去拉整份调度依赖。"""
    from scheduled_tasks import ModelBusy
    return ModelBusy

# 选了文件先暂存，点「收下」时和链接/文字一起变成同一条回收项；没提交的暂存文件按时间清理。
STAGE_PREFIX = '.stage-'
STAGE_TTL_SECONDS = 6 * 3600


def fetch_receipt(error):
    """把联网后端的失败说成人话。

    remio 桌面端没运行是现在最常见的失败，比平台风控更靠前，直说比原样丢出
    命令行文案有用。认不出的失败原样保留，不编造原因。
    """
    text = str(error or '').strip()
    if not text:
        return ''
    if any(mark in text for mark in ('未就绪', '未完成查询', '未返回有效结果', '无法启动联网服务')):
        return '联网服务未就绪（remio 未运行）'
    if '响应超时' in text:
        return '联网服务响应超时'
    if '没有返回可读正文' in text:
        return '页面没有返回可读正文'
    return text


def now():
    return datetime.now(timezone.utc).isoformat()


def readable_size(value):
    try:
        size = int(value)
    except (TypeError, ValueError):
        return '大小未知'
    if size >= 1024 ** 2:
        return f'{size / 1024 ** 2:.1f} MB'
    if size >= 1024:
        return f'{size / 1024:.1f} KB'
    return f'{size} B'


def readable_duration(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ''
    if not math.isfinite(seconds) or seconds <= 0:
        return ''
    minutes, rest = divmod(int(round(seconds)), 60)
    return f'{minutes} 分 {rest} 秒' if minutes else f'{rest} 秒'


def clean_text(value, name, limit, required=False):
    """Validate without rewriting.

    Emptiness is judged on the stripped value, but what we store keeps every
    leading/trailing space and newline the user typed (原样存).
    """
    if not isinstance(value, str) or '\x00' in value or len(value) > limit:
        raise ValueError(f'{name}格式无效，最多 {limit} 个字符')
    if required and not value.strip():
        raise ValueError('请填写' + name)
    return value


def safe_name(value):
    """Keep the user's file name as a label only; never accept a path from the client."""
    name = str(value or '')
    if '\x00' in name or any(sep in name for sep in ('/', '\\')):
        raise ValueError('文件名无效，只接受文件名本身')
    name = Path(name).name.strip()
    if not name or name in ('.', '..'):
        raise ValueError('请选择一个具体文件')
    return name[:200]


def ffprobe():
    path = shutil.which('ffprobe')
    if not path and Path('/opt/homebrew/bin/ffprobe').is_file():
        path = '/opt/homebrew/bin/ffprobe'
    return path


class Intake:
    """One local drop box feeding the existing knowledge base."""

    def __init__(self, knowledge, workspace):
        self.knowledge = knowledge
        self.root = Path(workspace) / 'inspiration'
        self.media_root = self.root / 'media'
        self.path = self.root / 'intake.db'
        self.lock = threading.RLock()
        self._ready = False
        self.media_recognizer = None
        self.recognition_workers = {}
        self.recognition_gate = threading.Semaphore(1)
        # 分折完成后由 IntakeCards 提供即时分析状态（收下即分析）；没接上时列表仍照旧可用。
        self.analysis_provider = None
        # 媒体识别完成后由 IntakeCards 排队一次分析；没接上时识别结果照旧保存，可手动重新分析。
        self.analysis_after_recognition = None
        # 识别收尾（成功/失败/不可用/中断）叫醒分析侧等识别的线程；没接上时分析侧照旧靠兜底轮询。
        self.recognition_wakers = []
        # 删除回收项后由 server 接线清掉情报侧的链接痕迹；没接上时只影响推荐计数，不影响删除本身。
        self.link_ledger = None

    @contextmanager
    def db(self):
        self._ensure()
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        # 删除会把卡库 ATTACH 进来一起提交；后台分析正在写时等一下，不报假失败。
        connection.execute('PRAGMA busy_timeout=8000')
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure(self):
        if self._ready:
            return
        with self.lock:
            if self._ready:
                return
            self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.root.chmod(0o700)
            self.media_root.mkdir(mode=0o700, exist_ok=True)
            self.media_root.chmod(0o700)
            self._sweep_stages()
            with sqlite3.connect(self.path) as connection:
                connection.executescript('''
                    CREATE TABLE IF NOT EXISTS drops(
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL, received_at TEXT NOT NULL,
                        url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                        text TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL DEFAULT '',
                        file_meta TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
                        document_id TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
                        meta TEXT NOT NULL DEFAULT '{}');
                    CREATE INDEX IF NOT EXISTS drops_received ON drops(received_at DESC);
                ''')
                columns={row[1] for row in connection.execute('PRAGMA table_info(drops)')}
                if 'recognition' not in columns:
                    connection.execute("ALTER TABLE drops ADD COLUMN recognition TEXT NOT NULL DEFAULT '{}'")
                connection.execute("UPDATE drops SET recognition=? WHERE json_extract(recognition,'$.status') IN ('running','queued')",(json.dumps({'status':'interrupted','error':'工作台重启中断媒体识别；可重新解析'},ensure_ascii=False),))
            self.path.chmod(0o600)
            self._ready = True

    # ── ① 投喂 ────────────────────────────────────────────────────────────────

    def receive(self, payload):
        """Store one drop exactly as given: 链接或文字，可带一个已暂存的图片/视频附件。

        「收下」一次只产生一条回收项：输入框里的链接或文字 + 收藏理由 + 选中的文件同一条入库。
        文件字节先走 stage_file 暂存，这里只按编号取用，客户端永远给不出本机路径。
        顺序是：先把整条请求校验完整（kind、理由/标题、链接/文字组合）→ 再领取 token
        → 同一把锁内查编号是否已存在→移动文件→插入。领取后的任何失败都统一清掉本次暂存。
        """
        if not isinstance(payload, dict) or set(payload) - {'id', 'kind', 'url', 'text', 'title', 'reason',
                                                            'file_token'}:
            raise ValueError('回收内容包含无效字段')
        drop_id = self._drop_id(payload.get('id'))
        url = payload.get('url', '')
        text = payload.get('text', '')
        token = payload.get('file_token', '')
        if not isinstance(url, str) or not isinstance(text, str) or not isinstance(token, str):
            raise ValueError('链接与文字内容格式无效')
        token = token.strip()
        if token and not ID_PATTERN.fullmatch(token):
            raise ValueError('附件编号格式无效')
        with self.lock:
            attachment = None
            try:
                # 先看清暂存文件（不消费），把整条请求校验完整才领取 token。
                attachment = self._peek_stage(token) if token else None
                kind = self._receive_kind(payload, url, text, attachment)
                reason = clean_text(payload.get('reason', ''), '收藏理由', MAX_REASON)
                reason = reason if reason.strip() else ''
                title = clean_text(payload.get('title', ''), '标题', MAX_TITLE).strip()
                stored_url, text = self._receive_body(kind, url, text)
                return self._commit_drop(drop_id, kind, stored_url, title, text, reason, attachment)
            except Exception:
                if attachment:
                    # 领取 token 之后的任何失败都统一清掉本次暂存：不留孤儿字节，也不留半条回收项。
                    # 前端仍留着选中的文件，再点一次「收下」会重新上传。
                    self._clean_stage(attachment['token'])
                raise

    def _receive_kind(self, payload, url, text, attachment):
        """kind 推断与组合校验（没给 token 时不拿附件当依据）。"""
        kind = payload.get('kind')
        if kind is None:
            # 没写 kind 就自己判：只链接→link，有文字→text，只有选中的文件→图片/视频。
            if url.strip() and not text.strip():
                kind = 'link'
            elif text.strip():
                kind = 'text'
            elif attachment:
                kind = attachment['kind']
            else:
                kind = 'text'
        if attachment and kind not in ('link', 'text', 'image', 'video'):
            raise ValueError('这里只接受链接、文字或已暂存的文件')
        if not attachment and kind not in ('link', 'text'):
            raise ValueError('这里只接受链接或文字；图片与视频请通过文件投喂')
        if attachment and kind in ('image', 'video') and (url.strip() or text.strip()):
            raise ValueError('只有文件的回收项不接受链接或文字')
        return kind

    def _receive_body(self, kind, url, text):
        """链接/文字正文本身的校验；返回（存下的链接，存下的文字）。"""
        if kind == 'link':
            stored_url = self._public_link(url)
            if text.strip():
                raise ValueError('链接回收项不接受粘贴正文，请只填链接或改用文字回收')
            return stored_url, ''
        if kind == 'text':
            return '', clean_text(text, '文字内容', MAX_TEXT, required=True)
        return '', ''

    def _commit_drop(self, drop_id, kind, url, title, text, reason, attachment):
        """在同一把锁内落一条回收项：先查这个编号是不是已经存在。

        已存在 → 只清掉本次暂存并返回原行：绝不把新文件移到原附件路径上，也绝不删原附件字节。
        不存在 → 把暂存文件移到还没被占用的目标路径，再插入这一行。
        插入失败就把已移动的文件清掉，不留下没有回收项对应的附件字节。
        """
        if attachment:
            self._stage_meta(attachment['token']).unlink(missing_ok=True)   # 领取：元数据到此消费
        stored = {'source': 'intake_page'}
        found = collect_links.identify(url) if kind == 'link' and url else None
        if found:
            # 链接是哪三家、哪条作品，在收下那一刻就记下来；短链只标出来，不猜编号。
            stored['source_platform'] = found['platform']
            stored['source_label'] = found['label']
            stored['source_item_id'] = found['item_id']
            stored['source_short_link'] = found['short_link']
        if attachment:
            stored['attachment'] = {'name': attachment['file'].get('name', ''), 'bytes': attachment['file'].get('bytes')}
        target = None
        try:
            with self.db() as connection:
                existing = connection.execute('SELECT * FROM drops WHERE id=?', (drop_id,)).fetchone()
                if existing:
                    if attachment:
                        self._clean_stage(attachment['token'])
                    return self._public_row(existing)
                if attachment:
                    # 图片后缀跟着附件的真实识别格式走，不跟着回收项 kind：
                    # 链接/文字带图时 kind 是 link/text，拿用户文件名当后缀会发错 Content-Type。
                    suffix = IMAGE_SUFFIXES[attachment['file']['format']] if attachment['kind'] == 'image' \
                        else Path(attachment['file']['name']).suffix.lower()
                    target = self._media_target(drop_id, suffix)
                    attachment['path'].rename(target)
                connection.execute('''INSERT INTO drops(id,kind,received_at,url,title,text,reason,file_path,
                    file_meta,status,meta) VALUES(?,?,?,?,?,?,?,?,?,'pending',?)''',
                    (drop_id, kind, now(), url, title, text, reason,
                     str(target.relative_to(self.root)) if target else '',
                     json.dumps(attachment['file'], ensure_ascii=False) if attachment else '',
                     json.dumps(stored, ensure_ascii=False)))
                return self._public_row(connection.execute('SELECT * FROM drops WHERE id=?', (drop_id,)).fetchone())
        except Exception:
            # 移动、插入、回读任一步失败：事务不提交，已移动的文件也清掉，
            # 不留没有回收项对应的附件字节；本次暂存由 receive 统一收尾。
            if target:
                target.unlink(missing_ok=True)
            raise

    def _media_target(self, drop_id, suffix):
        """给这条回收项一个还没被占用的附件路径；已存在就换带序号的名字，绝不覆盖既有字节。"""
        candidate = self.media_root / (drop_id + suffix)
        if not candidate.exists():
            return candidate
        for index in range(1, 50):
            alternate = self.media_root / ('%s-%d%s' % (drop_id, index, suffix))
            if not alternate.exists():
                return alternate
        raise ValueError('附件命名冲突太多，请重新选择文件')

    def receive_file(self, kind, filename, reason, stream, length):
        """旧用法：只投一个文件。仍然可用，等价于 stage_file + receive(file_token)。"""
        if kind not in ('image', 'video'):
            raise ValueError('文件投喂只接受图片或视频')
        drop_id = self._drop_id(None)
        name = safe_name(filename)
        reason = clean_text(reason, '收藏理由', MAX_REASON)
        reason = reason if reason.strip() else ''
        temporary = self.media_root / ('.drop-' + uuid.uuid4().hex)
        meta = self._accept_upload(kind, name, stream, length, temporary)
        # Store under our own name, using the format actually detected in the bytes.
        suffix = IMAGE_SUFFIXES[meta['format']] if kind == 'image' else Path(name).suffix.lower()
        with self.lock:
            target = self._media_target(drop_id, suffix)
            try:
                temporary.rename(target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            try:
                with self.db() as connection:
                    connection.execute('''INSERT INTO drops(id,kind,received_at,file_path,file_meta,reason,status,meta)
                        VALUES(?,?,?,?,?,?,'pending',?)''',
                        (drop_id, kind, now(), str(target.relative_to(self.root)), json.dumps(meta, ensure_ascii=False),
                         reason, json.dumps({'source': 'intake_page'}, ensure_ascii=False)))
                    return self._public_row(connection.execute('SELECT * FROM drops WHERE id=?', (drop_id,)).fetchone())
            except Exception:
                target.unlink(missing_ok=True)
                raise

    def stage_file(self, kind, filename, stream, length):
        """把选中的文件先收下暂存，等点「收下」时和链接/文字合成同一条回收项。

        暂存文件放在回收站媒体目录里，用 .stage- 前缀与编号命名；没提交的按时间自动清理，
        不产生半条回收项，也不接受客户端给的任何本机路径。
        """
        if kind not in ('image', 'video'):
            raise ValueError('文件投喂只接受图片或视频')
        name = safe_name(filename)
        self._ensure()
        self._sweep_stages()
        token = uuid.uuid4().hex
        temporary = self.media_root / (STAGE_PREFIX + token)
        meta = self._accept_upload(kind, name, stream, length, temporary)
        try:
            # 本体与元数据两步落盘：元数据写不成就把已写下的本体也清掉，不留只有字节的孤儿。
            self._stage_meta(token).write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
            self._stage_meta(token).chmod(0o600)
        except Exception:
            self._clean_stage(token)
            raise
        return {'schema': 'intake-stage/v1', 'token': token, 'kind': kind, 'file': meta,
                'note': '文件已暂存，点「收下」后与链接或文字一起成为同一条回收项；未提交的暂存文件会自动清理'}

    def _accept_upload(self, kind, name, stream, length, temporary):
        """写盘 + 探测格式，失败时把临时文件清掉。返回 file_meta（含 kind 与用户给的文件名）。"""
        maximum = MAX_IMAGE_BYTES if kind == 'image' else MAX_VIDEO_BYTES
        limit_name = '每张图片最多 10 MB' if kind == 'image' else '每个视频最大 1 GB'
        if not isinstance(length, int) or not 0 < length <= maximum:
            raise ValueError(limit_name)
        self._ensure()
        suffix = Path(name).suffix.lower()
        if kind == 'video' and suffix not in VIDEO_SUFFIXES:
            raise ValueError('视频支持 MP4、MOV、M4V、WebM、MKV、AVI')
        try:
            written = 0
            with temporary.open('wb') as output:
                while written < length:
                    chunk = stream.read(min(1024 * 1024, length - written))
                    if not chunk:
                        raise ValueError('上传中断，请重新选择文件')
                    written += len(chunk)
                    if written > maximum:
                        raise ValueError(limit_name)
                    output.write(chunk)
            meta = self._image_meta(temporary) if kind == 'image' else self._video_meta(temporary, suffix)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        meta['kind'] = kind
        meta['name'] = name
        return meta

    def _stage_meta(self, token):
        return self.media_root / (STAGE_PREFIX + token + '.json')

    def _clean_stage(self, token):
        """清掉一个 token 的暂存本体与元数据；不存在的当已清。"""
        (self.media_root / (STAGE_PREFIX + token)).unlink(missing_ok=True)
        self._stage_meta(token).unlink(missing_ok=True)

    def _peek_stage(self, token):
        """按编号看一份暂存文件（不消费）；过期或读不到就如实说，不猜路径。

        六小时 TTL 在领取时真检查，不只靠下一次上传触发的清扫；过期就把这个 token
        的本体与元数据一并清掉，不让人拿旧编号提交成功。
        只剩一半（本体在、元数据不在，或反过来）、JSON 坏了、字段不全的，
        也顺手把能清的都清掉，不留到 TTL。
        """
        path = self.media_root / (STAGE_PREFIX + token)
        meta_path = self._stage_meta(token)
        if not path.is_file() or not meta_path.is_file():
            self._clean_stage(token)
            raise ValueError('选中的文件已不在，请重新选择文件')
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            age = None
        if age is None or age > STAGE_TTL_SECONDS:
            self._clean_stage(token)
            raise ValueError('选中的文件已暂存超过 %d 小时，请重新选择文件' % (STAGE_TTL_SECONDS // 3600))
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self._clean_stage(token)
            raise ValueError('暂存文件读不到，请重新选择文件') from None
        if not isinstance(meta, dict) or meta.get('kind') not in ('image', 'video') or not meta.get('format'):
            self._clean_stage(token)
            raise ValueError('暂存文件信息不完整，请重新选择文件')
        return {'token': token, 'path': path, 'kind': meta['kind'], 'file': meta}

    def _sweep_stages(self, now_value=None):
        """清掉超时仍未提交的暂存文件（含元数据）。

        与领取用同一把锁：不会出现在另一线程已经把某个 token 取走提交时，
        清扫同时把它删了一半的窗口。
        """
        cutoff = (now_value if isinstance(now_value, (int, float)) else time.time()) - STAGE_TTL_SECONDS
        removed = 0
        with self.lock:
            try:
                for path in self.media_root.glob(STAGE_PREFIX + '*'):
                    try:
                        if path.stat().st_mtime < cutoff:
                            path.unlink(missing_ok=True)
                            removed += 1
                    except OSError:
                        continue
            except OSError:
                return 0
        return removed

    def _drop_id(self, value):
        if value in (None, ''):
            return uuid.uuid4().hex
        if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
            raise ValueError('回收项编号格式无效')
        return value

    @staticmethod
    def _public_link(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('请填写要收藏的链接')
        raw = value.strip()
        if raw.startswith(('/', '.', '~', 'file:', 'ftp:')) or re.match(r'^[A-Za-z]:[\\/]', raw):
            raise ValueError('不接收本机文件路径；图片与视频请通过文件投喂')
        # Reuse the existing boundary: public HTTP(S) only, no credentials, no
        # loopback or private ranges, no odd ports.
        return public_url(raw)

    def _image_meta(self, path):
        from PIL import Image, UnidentifiedImageError
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as source:
                    if source.format not in IMAGE_FORMATS:
                        raise ValueError('图片支持 PNG、JPEG 和 WebP')
                    image_format, width, height = source.format, source.size[0], source.size[1]
                    if width * height > MAX_PIXELS:
                        raise ValueError('图片像素过大，请缩小后重试')
                    source.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValueError('无法读取图片，请选择有效的 PNG、JPEG 或 WebP 文件') from None
        return {'format': image_format, 'width': width, 'height': height, 'bytes': path.stat().st_size}

    def _video_meta(self, path, suffix):
        meta = {'format': suffix.lstrip('.').upper(), 'bytes': path.stat().st_size, 'duration_seconds': None}
        executable = ffprobe()
        if not executable:
            meta['duration_note'] = '本机 ffprobe 不可用，时长未读取'
            return meta
        try:
            result = subprocess.run([executable, '-v', 'error', '-show_entries',
                                     'format=duration,format_name:stream=codec_type',
                                     '-of', 'json', str(path)], capture_output=True, check=True, timeout=30)
            info = json.loads(result.stdout)
            duration = float(info.get('format', {}).get('duration') or 0)
            meta['container'] = str(info.get('format', {}).get('format_name') or '')[:80]
            meta['has_audio'] = any(s.get('codec_type') == 'audio' for s in info.get('streams', []))
            if math.isfinite(duration) and duration > 0:
                meta['duration_seconds'] = round(duration, 2)
        except (subprocess.SubprocessError, OSError, ValueError, TypeError):
            meta['duration_note'] = '无法读取时长，请核对文件是否完整'
        return meta

    # ── ② 解析 + ③ 入库 ───────────────────────────────────────────────────────

    def resolve(self, drop_id):
        """Fetch what can be fetched, then file it into the existing knowledge base."""
        drop_id = self._drop_id(drop_id)
        with self.lock:
            raw = self._raw(drop_id)
            record = self._public_row(raw)
            if record['status'] == 'resolved':
                if self._media_kind(record):self._start_recognition(drop_id)
                return self.detail(drop_id)
            if record['kind'] == 'link':
                outcome, error = self._file_link(record, raw['text'])
            else:
                outcome, error = self._file_note(record, raw['text'])
            with self.db() as connection:
                connection.execute('''UPDATE drops SET status=?,error=?,document_id=?,text=?,meta=?,revision=revision+1
                    WHERE id=?''',
                    ('failed' if error else 'resolved', error, outcome['document_id'], outcome['text'],
                     json.dumps(outcome['meta'], ensure_ascii=False), drop_id))
            if self._media_kind(record):self._start_recognition(drop_id)
            return self.detail(drop_id)

    @staticmethod
    def _media_kind(record):
        """这条回收项带的附件是图片还是视频；没附件返回 ''。

        附件和回收项的 kind 分开看：一条链接回收项也可以同时带一张图。
        旧行 file_meta 里没有 kind，退回 drops.kind。
        """
        kind = (record.get('file') or {}).get('kind') or ''
        if kind in ('image', 'video'):
            return kind
        if record.get('file_path') and record.get('kind') in ('image', 'video'):
            return record['kind']
        return ''

    def _start_recognition(self,drop_id):
        if drop_id in self.recognition_workers:return
        with self.db() as db:
            db.execute('UPDATE drops SET recognition=? WHERE id=?',(json.dumps({'status':'queued','started_at':now()},ensure_ascii=False),drop_id))
        worker=threading.Thread(target=self._recognize,args=(drop_id,),daemon=True)
        self.recognition_workers[drop_id]=worker;worker.start()

    def _recognize(self,drop_id):
        from intake_media_recognition import MediaRecognizer,CapabilityUnavailable
        self.recognition_gate.acquire()
        try:
            with self.db() as db:db.execute('UPDATE drops SET recognition=? WHERE id=?',(json.dumps({'status':'running','started_at':now()},ensure_ascii=False),drop_id))
            record=self.detail(drop_id)
            recognizer=self.media_recognizer or MediaRecognizer()
            path=self.file_path(drop_id)
            result=(recognizer.recognize_image(path,record['file']) if self._media_kind(record)=='image'
                    else recognizer.recognize_video(path,record['file']))
            result={**result,'status':result.get('status','succeeded'),'finished_at':now()}
        except CapabilityUnavailable as exc:
            result={'status':'unavailable','error':str(exc),'finished_at':now()}
        except model_busy_class() as exc:
            # 模型正忙不是失败：如实说稍后重新解析，分析照常用理由/链接正文/文件名进行。
            result={'status':'unavailable','error':str(exc),'finished_at':now()}
        except Exception as exc:
            # Subprocess stderr may contain operational details; never echo it.
            result={'status':'failed','error':str(exc)[:500] if isinstance(exc,ValueError) else '媒体识别失败，原始文件和入库记录保留','finished_at':now()}
        with self.lock:
            try:
                with self.db() as db:db.execute('UPDATE drops SET recognition=? WHERE id=?',(json.dumps(result,ensure_ascii=False),drop_id))
            finally:
                self.recognition_workers.pop(drop_id,None)
                self.recognition_gate.release()
        # 不管成功、失败、不可用还是中断，识别已经收尾：先叫醒等它的分析线程，再考虑自动分析。
        self._wake_recognition_waiters(drop_id)
        if result.get('status') == 'succeeded':
            self._analyse_after_recognition(drop_id)

    def register_recognition_waker(self, callback):
        """分析侧登记一个「识别收尾」唤醒回调（只存引用，不改变识别行为）。"""
        if callback not in self.recognition_wakers:
            self.recognition_wakers.append(callback)

    def _wake_recognition_waiters(self, drop_id):
        """识别写回后叫醒等它的分析线程；唤醒失败不影响已保存的识别结果。"""
        for waker in list(self.recognition_wakers):
            try:
                waker(drop_id)
            except (OSError, ValueError, KeyError):
                pass

    def _analyse_after_recognition(self,drop_id):
        """图片描述/视频转写完成后自动排队一次分析（同一条最多两次模型调用，模型正忙则如实排队）。

        排队失败不抹掉已保存的识别结果，也不谎报成分析失败：页面会照旧显示可手动重新分析。
        no_audio / unavailable / failed 不带新正文，不自动花模型调用。
        """
        hook=self.analysis_after_recognition
        if not hook:return
        try:hook(drop_id)
        except (OSError,ValueError,KeyError):pass

    def _raw(self, drop_id):
        with self.db() as connection:
            row = connection.execute('SELECT * FROM drops WHERE id=?', (drop_id,)).fetchone()
        if not row:
            raise ValueError('回收项不存在，请刷新列表')
        return dict(row)

    def exists(self, drop_id):
        """这条回收项还在不在。后台的分析/识别收尾前都要过这一句，已删的不写回。"""
        if not isinstance(drop_id, str) or not ID_PATTERN.fullmatch(drop_id):
            return False
        with self.db() as connection:
            return connection.execute('SELECT 1 FROM drops WHERE id=?', (drop_id,)).fetchone() is not None

    def _shares(self, document_ids):
        """每篇知识库正文当前被几条回收项引用（共用正文的判断依据，含它自己）。"""
        wanted = [doc for doc in dict.fromkeys(document_ids) if doc]
        if not wanted:
            return {}
        with self.db() as connection:
            marks = ','.join('?' * len(wanted))
            return {row['document_id']: row['n'] for row in connection.execute(
                'SELECT document_id,COUNT(*) n FROM drops WHERE document_id IN (' + marks +
                ') GROUP BY document_id', wanted)}

    def _ownership(self, document_ids):
        """这些正文是不是回收站自己建的（看知识库 provenance，不看本条 drop 的 duplicate 标记）。

        知识库先收藏、回收站只是复用的那篇，永远不归回收站删。读不到出处时给 None：
        删除一律按「不删正文」处理，确认框也只说保守的话，不假装数过。
        第一条建、第二条复用、第一条先删：正文当时因还有引用不删；最后一条删时
        provenance 仍写着回收站渠道，所以不会漏下孤儿正文。
        """
        wanted = [doc for doc in dict.fromkeys(document_ids) if doc]
        if not wanted:
            return {}
        try:
            facts = self.knowledge.document_provenance_facts(wanted)
        except Exception:
            return {doc: None for doc in wanted}
        return {doc: (True if str((facts.get(doc) or {}).get('intake_channel') or '') == INTAKE_CHANNEL
                      else False if facts.get(doc) else None) for doc in wanted}

    def _fetch(self, url):
        """Bounded paging through the existing web tool. Never returns partial text on failure."""
        web = WebTools()
        job = {'cancel': False, 'web_calls': 0}
        pieces, pages, title, final, total = [], [], url, url, 0
        start = 0
        for _ in range(MAX_PAGES):
            page = web.fetch({'url': url, 'start': start, 'max_chars': PAGE_CHARS}, job)
            pieces.append(page['markdown'])
            pages.append({'start': page['start'], 'end': page['start'] + len(page['markdown'])})
            title, final, total = page['title'], page['url'], page['total_chars']
            start = page['next_start'] if page.get('next_start') is not None else len(''.join(pieces))
            if start >= total:
                break
        body = ''.join(pieces)
        complete = len(body) >= total
        return {'text': body, 'title': title, 'final_url': final, 'total_chars': total,
                'pages': pages, 'complete': complete, 'fetched_at': now()}

    def _attachment_note(self, record):
        """附件的一句话事实（名称/格式/大小），只写事实，不写本机路径。"""
        file_meta = record['file']
        kind = self._media_kind(record)
        label = '图片' if kind == 'image' else '视频'
        shape = (f'{file_meta.get("width", "?")}×{file_meta.get("height", "?")}' if kind == 'image'
                 else (readable_duration(file_meta.get('duration_seconds')) or '时长未读取'))
        return f'另存本机{label}：{file_meta.get("name", "未命名")}，{file_meta.get("format", "未知格式")}，{shape}，{readable_size(file_meta.get("bytes"))}'

    def _legacy_document_id(self, url):
        """旧口径（host+path+query）已经登记过这篇吗？登记过就返回那篇的编号。

        查找已经下沉到 `external_reference.existing_legacy_document`，知识库页面的收藏入口
        （POST /api/knowledge/reference）调的是同一个函数，两个入口不会分叉。这里只把本入口的
        knowledge 传进去；只查不写，不迁移、不回填旧库，查不到就返回 ''，照新口径入库。
        """
        return existing_legacy_document(url, self.knowledge)

    def _file_link(self, record, stored_text):
        meta = dict(record['meta'])
        text = ''
        fetched = None
        error = ''
        found = collect_links.identify(record['url'])
        if found:
            # 旧行没打标也照现读结果补上，不让一条已有回收项在列表里丢掉平台。
            meta.update({'source_platform': found['platform'], 'source_label': found['label'],
                         'source_item_id': found['item_id'], 'source_short_link': found['short_link']})
        try:
            fetched = self._fetch(record['url'])
            text = fetched['text']
        except (ValueError, OSError) as exc:
            error = str(exc)
        # 三家免登录抓回来的多半是登录墙或 JS 空壳。那些文字不能当作品正文入库：
        # 正文留空，只记下壳子的字数与指纹，回执说清「未读到作品正文」。
        wall = collect_links.body_not_read(found, text) if fetched else ''
        # 「接受了正文」才是 fetched：只命中壳子时没读到正文，标题与出处标记都要跟普通抓取失败一致。
        accepted = bool(fetched) and not wall
        if fetched and not wall:
            meta.update({'fetched': True, 'fetched_at': fetched['fetched_at'], 'final_url': fetched['final_url'],
                         'total_chars': fetched['total_chars'], 'pages': fetched['pages'],
                         'sha256': hashlib.sha256(text.encode()).hexdigest()})
            scope = (f'已抓取正文，共 {fetched["total_chars"]} 字' if fetched['complete']
                     else f'已抓取正文前 {len(text)} 字；原文共 {fetched["total_chars"]} 字，剩余未读取')
        elif found:
            reason = collect_links.receipt(found, wall or fetch_receipt(error))
            meta.update({'fetched': False, 'fetch_error': reason})
            if error:
                meta['fetch_error_raw'] = error[:500]
            if wall:
                meta['login_wall'] = True
                meta['shell_chars'] = fetched['total_chars']
                meta['shell_sha256'] = hashlib.sha256(text.encode()).hexdigest()
            text = ''
            error = reason
            scope = f'仅收藏链接；未读到作品正文（{reason}）'
        else:
            meta.update({'fetched': False, 'fetch_error': error})
            scope = f'仅收藏链接；原文正文未读取（抓取失败：{error}）'
        title = record['title'] or ((fetched or {}).get('title') if accepted else '') or ''
        title_pending = False
        if not title:
            title, title_pending = record['url'][:MAX_TITLE], True
        if self._media_kind(record):
            # 链接正文逐字存，附件只记事实；图片内容本身走识别字段，不塞进正文。
            scope = scope + '；' + self._attachment_note(record)
            meta['attachment'] = {'name': record['file'].get('name', ''), 'bytes': record['file'].get('bytes'),
                                  'kind': self._media_kind(record)}
        meta['content_scope'] = scope
        if title_pending:
            meta['title_pending'] = '标题待补'
        # 旧库衔接：这条链接在旧口径下已经存过一篇，就复用那篇并判为已存在，
        # 不因换了去重口径多出一篇。只在三家有去重地址时查；短链本来就用旧口径，不用查。
        legacy_id = self._legacy_document_id(record['url']) if found and found['dedupe_url'] else ''
        if legacy_id:
            meta['duplicate'] = True
            meta['legacy_document'] = True
            return ({'document_id': legacy_id, 'text': text, 'meta': meta}, error)
        document = self.knowledge.add_reference(
            {'title': title, 'url': record['url'], 'body': text, 'discovered_via': 'copied_link',
             'discovery_detail': f'{INTAKE_CHANNEL} · 回收项 {record["id"]}'},
            provenance={'intake_channel': INTAKE_CHANNEL, 'drop_id': record['id'],
                        'fetched': accepted, 'fetched_at': (fetched or {}).get('fetched_at', '') if accepted else '',
                        'content_scope': scope})
        if document.get('duplicate'):
            meta['duplicate'] = True
        return ({'document_id': document['id'], 'text': text, 'meta': meta}, error)

    def _file_note(self, record, stored_text):
        meta = dict(record['meta'])
        kind = record['kind']
        file_meta = record['file']
        if kind == 'text':
            body = stored_text
            scope = f'用户原文逐字保存，共 {len(body)} 字'
            if self._media_kind(record):
                # 原文逐字不动，附件另记一句事实；图片内容本身在识别字段里。
                scope = scope + '；' + self._attachment_note(record)
                meta['attachment'] = {'name': record['file'].get('name', ''), 'bytes': record['file'].get('bytes'),
                                      'kind': self._media_kind(record)}
            title = record['title'] or f'创意回收 · 文字 · {record["received_at"][:10]} 想法'
        elif kind == 'image':
            body = (f'本机图片：{file_meta.get("name", "")}，{file_meta.get("width", "?")}×{file_meta.get("height", "?")}，'
                    f'{file_meta.get("format", "未知格式")}，{readable_size(file_meta.get("bytes"))}。图片内容未识别。')
            scope = '仅保存本机图片与尺寸格式；图片内容未识别'
            title = f'创意回收 · 图片 · {file_meta.get("name", record["id"])}'
        else:
            duration = readable_duration(file_meta.get('duration_seconds')) or file_meta.get('duration_note', '时长未读取')
            body = (f'本机视频：{file_meta.get("name", "")}，{file_meta.get("format", "未知格式")}，{duration}，'
                    f'{readable_size(file_meta.get("bytes"))}。视频正文与语音未读取。')
            scope = '仅保存本机视频与大小格式时长；视频正文与语音未读取'
            title = f'创意回收 · 视频 · {file_meta.get("name", record["id"])}'
        meta['content_scope'] = scope
        document = self.knowledge.add_note(
            {'title': title[:MAX_TITLE], 'body': body},
            provenance={'intake_channel': INTAKE_CHANNEL, 'drop_id': record['id'],
                        'content_scope': scope, 'intake_reason': record['reason']},
            # 文字回收项连首尾空白一起逐字存；图片与视频的正文是服务自己写的事实句。
            verbatim=(kind == 'text'))
        return ({'document_id': document['id'], 'text': stored_text, 'meta': meta}, '')

    # ── 读取 ──────────────────────────────────────────────────────────────────

    def listing(self, limit=80):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError('列表数量为 1–200')
        with self.db() as connection:
            rows = [dict(row) for row in
                    connection.execute('SELECT * FROM drops ORDER BY received_at DESC LIMIT ?', (limit,)).fetchall()]
        states = self._analyses(rows)
        shares = self._shares([row['document_id'] for row in rows])
        owned = self._ownership([row['document_id'] for row in rows])
        return {'schema': 'intake/v1',
                'items': [self._public_row(row, states.get(row['id'], {}), shares.get(row['document_id'], 0),
                                           owned.get(row['document_id']))
                          for row in rows],
                'note': '回收站原样保存投喂内容；正文与理由分开保存，入库后仍可在知识库检索'}

    def detail(self, drop_id):
        drop_id = self._drop_id(drop_id)
        raw = self._raw(drop_id)
        return self._public_row(raw, shares=self._shares([raw['document_id']]).get(raw['document_id'], 0),
                                owned=self._ownership([raw['document_id']]).get(raw['document_id']))

    def file_path(self, drop_id):
        """Locate a stored file by its drop id; the client never supplies a path."""
        record = self.detail(drop_id)
        if not record['file_path']:
            raise ValueError('这条回收项没有本机文件')
        path = (self.root / record['file_path']).resolve()
        if not path.is_file() or not path.is_relative_to(self.media_root.resolve()):
            raise ValueError('文件不存在，可能已被移动')
        return path

    # ── ⑥ 删除 ────────────────────────────────────────────────────────────────

    CARDS_TABLES = ('analysis', 'seen', 'extraction_retry', 'extraction_epochs')

    def _delete_records(self, connection, drop_id):
        """把卡库的分析与提炼指纹 ATTACH 进来，和 drop 行同一次提交里消失。

        两个库在同一目录，所以不存在「回收项没了、分析还在」的半截状态；
        卡库还没建过（只投喂过、从未分析）时如实跳过，不为了删除去新建一个空库。
        """
        counts = {'attached': False, 'analysis': 0, 'seen': 0, 'error': ''}
        path = self.root / 'cards.db'
        if not path.is_file():
            return counts
        try:
            connection.execute('ATTACH DATABASE ? AS cards', (str(path),))
            counts['attached'] = True
            tables = {row[0] for row in connection.execute("SELECT name FROM cards.sqlite_master WHERE type='table'")}
            for table in self.CARDS_TABLES:
                if table in tables:
                    counts[table] = connection.execute('DELETE FROM cards.%s WHERE drop_id=?' % table,
                                                       (drop_id,)).rowcount
        except sqlite3.Error as exc:
            # 卡库正被后台写锁住等情况：不拿它弄挂整个删除，补一次单独提交（见 _detach_records）。
            counts['error'] = str(exc)[:200]
        return counts

    def _detach_records(self, drop_id):
        """ATTACH 没成时的兜底：另开一次提交清掉分析记录，不谎报成已清。"""
        counts = {'analysis': 0, 'seen': 0, 'error': ''}
        path = self.root / 'cards.db'
        if not path.is_file():
            return counts
        try:
            connection = sqlite3.connect(path)
            try:
                connection.execute('PRAGMA busy_timeout=8000')
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in self.CARDS_TABLES:
                    if table not in tables:continue
                    counts[table] = connection.execute('DELETE FROM %s WHERE drop_id=?' % table,
                                                       (drop_id,)).rowcount
                connection.commit()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            counts['error'] = str(exc)[:200]
        return counts

    def _delete_file(self, raw):
        """删本机媒体文件。只认 drops 里存的路径，客户端永远拿不到路径。"""
        stored = raw['file_path']
        if not stored:
            return {'file_removed': False, 'file_error': ''}
        try:
            path = (self.root / stored).resolve()
            if not path.is_relative_to(self.media_root.resolve()):
                raise ValueError('保存路径不在回收站媒体目录内')
            existed = path.is_file()
            path.unlink(missing_ok=True)
            return {'file_removed': existed, 'file_error': '' if existed else '本机文件已不在，可能已被移动'}
        except (OSError, ValueError) as exc:
            return {'file_removed': False, 'file_error': str(exc)[:200]}

    def delete(self, drop_id, ledger_live=_LEDGER_LIVE_UNSET):
        """删一条回收项：drop、即时分析/提炼指纹、本机媒体文件一起删。

        知识库正文只在两个条件同时满足时才删：没有其他回收项引用，而且这篇是回收站自己建的
        （provenance 里是回收站渠道）。知识库先收藏、回收站只是复用的那篇永远保留。
        删正文放在回收站这把锁里，并在知识库事务里再数一遍引用：
        另一条同链接正在解析时不会误删刚写下的正文。
        已生成的提示词卡不删，卡库自己会把它们标成「来源已删除」。
        进行中（分析/读取/识别）的条目也能删：后台收尾前会再查一次存在性，不会把已删的写回。

        ledger_live：批量删除时上层算好的「两库现存公众号链接」快照，透传给情报侧收尾，
        避免逐条重扫知识库；默认未指定（单条删除）时由收尾方现算一次。
        """
        drop_id = self._drop_id(drop_id)
        with self.lock:
            raw = self._raw(drop_id)
            document_id = raw['document_id']
            owner = self._ownership([document_id]).get(document_id) if document_id else None
            owned = owner is True
            with self.db() as connection:
                others = connection.execute('SELECT COUNT(*) FROM drops WHERE document_id=? AND id<>?',
                                            (document_id, drop_id)).fetchone()[0] if document_id else 0
                records = self._delete_records(connection, drop_id)
                connection.execute('DELETE FROM drops WHERE id=?', (drop_id,))
            # 引用计数与删正文在同一把锁里：resolve 也要这把锁，
            # 所以计数之后到删正文之间不会有另一条同链接提交新的引用。
            removed, keep_reason, delete_error, channel_now = {}, '', '', ''
            if document_id and others == 0 and owned:
                try:
                    # 归属与引用都在知识库那一次事务里再核一遍：这里只递上期望值，
                    # 真正的判断在条件 DML 里，不拿 Intake 锁读到的旧结论当真凭据。
                    removed = self.knowledge.delete_document(document_id,
                                                             keep_if_referenced_by=(str(self.path), 'drops'),
                                                             require_intake_channel=INTAKE_CHANNEL)
                    if not removed.get('removed'):
                        keep_reason = removed.get('reason') or '知识库未删正文，原因未记录'
                        channel_now = str(removed.get('channel') or '')
                except (ValueError, OSError) as exc:
                    delete_error = str(exc)[:200]
            elif document_id and owner is None:
                keep_reason = '知识库正文的出处读不到，按不删正文处理'
            elif document_id and not owned:
                keep_reason = '这篇正文不是回收站存的（知识库先收藏或其他入口），只删回收项，正文保留'
            elif document_id and others:
                keep_reason = f'还有 {others} 条回收项引用同一篇正文，未删'
        if not records['attached'] and records['error']:
            fallback = self._detach_records(drop_id)
            records['analysis'] += fallback['analysis']
            records['seen'] += fallback['seen']
            records['error'] = fallback['error'] or records['error']
        result = {'schema': 'intake-delete/v1', 'id': drop_id, 'deleted': True, 'document_id': document_id,
                  'document_shared_by': others, 'document_owned': owner,
                  'analysis_removed': records['analysis'],
                  'seen_removed': records['seen'], 'cards_db_attached': records['attached'],
                  'records_error': records['error'],
                  'document_removed': bool(removed.get('removed')), 'document_error': delete_error,
                  'document_kept_reason': keep_reason if not removed.get('removed') else '',
                  'document_channel_now': channel_now,
                  'cards_kept': True,
                  'file_removed': False, 'file_error': '', 'ledger_error': ''}
        result.update(self._delete_file(raw))
        if self.link_ledger:
            try:
                links = [raw['url']] if raw['url'] else []
                if ledger_live is _LEDGER_LIVE_UNSET:
                    self.link_ledger(links, drop_id)
                else:
                    self.link_ledger(links, drop_id, live=ledger_live)
            except Exception as exc:
                # 情报侧的链接痕迹没清干净不影响删除本身，但不能谎报成已清。
                result['ledger_error'] = str(exc)[:200]
        # 正文真删掉后，情报侧那条「按文档精确清来源行」的钩子可能失败。
        # 钩子异常已被 knowledge_store 收进 hook_error（正文已提交，不推翻删除），
        # 这里必须把它带到回执上：回收项路径以前没读这个字段，异常被静默吞掉，
        # 上层拿到一份「一切正常」的回执，而情报侧实际留着一条孤儿来源行。
        # 并入 ledger_error 而不是另开字段：两者都是「情报侧没清干净」，
        # 批末汇总只认这一处，合并后既能被逐条看到，也能被 summary 收到。
        # 用「已有内容 + 追加」而不是覆盖，两处错误同时发生时各自都还在。
        if removed.get('hook_error'):
            hook_error = str(removed['hook_error'])[:200]
            result['ledger_error'] = (result['ledger_error'] + '；情报侧文档钩子失败：' + hook_error
                                      if result['ledger_error']
                                      else '情报侧文档钩子失败：' + hook_error)
        return result

    # ── ⑦ 批量删除 ─────────────────────────────────────────────────────────

    MAX_BATCH_DELETE = 50

    @staticmethod
    def _display_kind(row):
        """列表与确认框里的类型：链接/文字带附件时算「组合」，与页面上的类型筛选同一口径。"""
        media = Intake._media_kind({'file': json.loads(row.get('file_meta') or '{}'),
                                    'file_path': row.get('file_path'), 'kind': row.get('kind')})
        return 'combo' if media and row.get('kind') in ('link', 'text') else row.get('kind')

    def _batch_ids(self, ids):
        """批量入口的入参闸门：1–50 个合法编号，重复的只处理第一次。

        编号格式不对或同一次里重复不报错，而是逐条如实说明——部分失败不回滚已成功的项，
        所以页面必须能一条一条对上原因。
        """
        if not isinstance(ids, list) or not 1 <= len(ids) <= self.MAX_BATCH_DELETE:
            raise ValueError(f'批量删除只接受 1–{self.MAX_BATCH_DELETE} 个编号')
        wanted, rejected = [], []
        for value in ids:
            if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
                rejected.append({'id': value if isinstance(value, str) else '', 'ok': False, 'reason': '编号无效，未处理'})
                continue
            if value in wanted:
                rejected.append({'id': value, 'ok': False, 'reason': '同一次里重复提交，未重复删除'})
                continue
            wanted.append(value)
        return wanted, rejected

    def _card_counts(self, drop_ids):
        """这些回收项已生成几张提示词卡（只读卡库，不改它；卡库还没建过时给 0）。"""
        counts, path = {}, self.root / 'cards.db'
        if not drop_ids or not path.is_file():
            return counts
        try:
            connection = sqlite3.connect(path)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA busy_timeout=8000')
                marks = ','.join('?' * len(drop_ids))
                for row in connection.execute('SELECT drop_id,COUNT(*) n FROM cards WHERE drop_id IN (' + marks +
                                              ') GROUP BY drop_id', drop_ids):
                    counts[row['drop_id']] = row['n']
            finally:
                connection.close()
        except sqlite3.Error:
            return counts
        return counts

    def _batch_facts(self, wanted):
        """确认框要的一句事实：这条是什么类型、带不带文件、正文归谁、几张卡。只读。"""
        with self.db() as connection:
            marks = ','.join('?' * len(wanted))
            found = {row['id']: dict(row) for row in
                     connection.execute('SELECT * FROM drops WHERE id IN (' + marks + ')', wanted)}
        shares = self._shares([row['document_id'] for row in found.values()])
        owned = self._ownership([row['document_id'] for row in found.values()])
        cards = self._card_counts(wanted)
        items = []
        for drop_id in wanted:
            row = found.get(drop_id)
            if not row:
                continue
            document_id = row['document_id']
            owner = owned.get(document_id) if document_id else None
            items.append({'id': drop_id, 'kind': row['kind'], 'display_kind': self._display_kind(row),
                          'received_at': row['received_at'], 'title': row['title'], 'url': row['url'],
                          'media_kind': self._media_kind(row), 'file': bool(row['file_path']),
                          'document_id': document_id, 'document_shares': shares.get(document_id, 0),
                          'document_owned': owner if isinstance(owner, bool) else None,
                          'cards': cards.get(drop_id, 0)})
        return items, [drop_id for drop_id in wanted if drop_id not in found]

    def delete_preview(self, ids):
        """批量删除前的如实预览：条数、类型分布、正文会删几篇为什么留几篇、涉及几张卡。

        只读，不改任何东西。判断口径与单条删除完全一样（provenance 归属 + 引用计数）：
        同一篇正文被这次选中的多条同时引用时，只有最后一条会真的删，所以按
        「选中的引用数 == 当前总引用数」算。真正删除时知识库事务里还会再核一遍。
        """
        wanted, rejected = self._batch_ids(ids)
        items, missing = self._batch_facts(wanted)
        kinds = {}
        for item in items:
            label = item['display_kind'] or item['kind']
            kinds[label] = kinds.get(label, 0) + 1
        selected_refs, owners, totals = {}, {}, {}
        for item in items:
            if not item['document_id']:
                continue
            selected_refs[item['document_id']] = selected_refs.get(item['document_id'], 0) + 1
            owners[item['document_id']] = item['document_owned']
            totals[item['document_id']] = item['document_shares']
        documents = {'total': len(selected_refs), 'would_remove': 0, 'kept': 0,
                     'kept_shared': 0, 'kept_not_owned': 0, 'kept_unknown': 0}
        for document_id, picked in selected_refs.items():
            owner = owners[document_id]
            if owner is True and picked >= totals[document_id]:
                documents['would_remove'] += 1
            elif owner is None:
                documents['kept_unknown'] += 1
            elif owner is False:
                documents['kept_not_owned'] += 1
            else:
                documents['kept_shared'] += 1
        documents['kept'] = documents['total'] - documents['would_remove']
        return {'schema': 'intake-delete-preview/v1', 'requested': len(ids), 'count': len(items),
                'kinds': kinds, 'files': sum(1 for item in items if item['file']),
                'documents': documents,
                'cards': {'drops': sum(1 for item in items if item['cards']),
                          'cards': sum(item['cards'] for item in items)},
                'missing': missing, 'rejected': rejected, 'items': items,
                'note': '预览只读，不改数据；正文删不删由出处与引用计数决定，删除时还会在知识库事务里再核一遍'}

    def delete_many(self, ids):
        """批量删除：逐条走单条 delete()，规则一模一样，不另写一套删除。

        一次最多 50 条；每条单独提交，失败的不回滚已成功的（合同要求），但每条结果都
        如实返回。编号无效、同一次重复、已不在列表都算「跳过」，不算失败也不假装删过。
        锁仍按单条粒度拿：两条之间另一条同链接提交新引用时，知识库那一次条件 DML
        会再数一遍引用，不会误删刚写下的正文。

        情报侧收尾要扫「两库现存的公众号链接」：这里先让接线方 prepare() 拿一份快照，
        再逐条透传给 delete()，把 N 次全表扫描压成 1 次；没接 prepare 的收尾方（如测试
        里的替身）照旧逐条现算，不改变其语义。批末再 finalize() 一次：删除前快照会把本批
        才删掉的链接也算成存在，留下 knowledge 来源与推荐计数残留，用新算的 live 对账清掉。
        """
        wanted, skipped = self._batch_ids(ids)
        prepare = getattr(self.link_ledger, 'prepare', None)
        ledger_live = prepare() if prepare else _LEDGER_LIVE_UNSET
        results = []
        for drop_id in wanted:
            try:
                receipt = self.delete(drop_id, ledger_live=ledger_live)
            except ValueError as exc:
                skipped.append({'id': drop_id, 'ok': False, 'reason': str(exc)})
                continue
            except (OSError, sqlite3.Error) as exc:
                skipped.append({'id': drop_id, 'ok': False, 'reason': '删除未完成：' + str(exc)[:200]})
                continue
            receipt['ok'] = True
            results.append(receipt)
        ledger_error = ''
        finalize = getattr(self.link_ledger, 'finalize', None)
        if finalize:
            try:
                finalize()
            except Exception as exc:
                # 批末对账失败不影响逐条删除回执；如实记在总回执里，不假装清干净了。
                ledger_error = str(exc)[:200]
        return {'schema': 'intake-delete-many/v1', 'requested': len(ids), 'deleted': results,
                'skipped': skipped, 'summary': self._batch_summary(results, skipped),
                'ledger_error': ledger_error,
                'note': '逐条删除，每条单独提交；已成功的不会因后面的失败回滚'}

    def _batch_summary(self, results, skipped):
        """把逐条回执汇成一句能直接给用户看的话，失败与保留都算进去。"""
        errors = [{'id': receipt['id'], 'error': part}
                  for receipt in results
                  for part in (receipt['document_error'], receipt['file_error'], receipt['records_error'],
                               receipt['ledger_error']) if part]
        involved = {receipt['document_id'] for receipt in results if receipt['document_id']}
        # 未删成功但仍存在的选中项也属于本次涉及范围；缺失/非法id没有正文可计。
        for entry in skipped:
            try:
                raw = self._raw(self._drop_id(entry['id']))
            except ValueError:
                continue
            if raw['document_id']:
                involved.add(raw['document_id'])
        removed = {receipt['document_id'] for receipt in results
                   if receipt['document_id'] and receipt['document_removed']}
        # 删除首条时的临时保留不等于批量结束后保留；只统计最终仍存在的唯一正文。
        # 查询失败记为未知：删除已经逐条提交，不能丢掉回执，也不能把未知写成 0。
        kept_count = 0
        if involved - removed:
            try:
                with self.knowledge.db() as connection:
                    wanted = sorted(involved - removed)
                    marks = ','.join('?' * len(wanted))
                    kept_count = len({row['id'] for row in connection.execute(
                        'SELECT id FROM documents WHERE id IN (' + marks + ')', wanted)})
            except (sqlite3.Error, OSError):
                kept_count = None
        return {'requested': len(results) + len(skipped), 'deleted': len(results), 'skipped': len(skipped),
                'documents_removed': len(removed),
                'documents_kept': kept_count,
                'files_removed': sum(1 for receipt in results if receipt['file_removed']),
                'analysis_removed': sum(receipt['analysis_removed'] for receipt in results),
                'seen_removed': sum(receipt['seen_removed'] for receipt in results),
                'cards_kept_drops': sum(1 for receipt in results if receipt['cards_kept']),
                'cards_kept': self._batch_card_count([receipt['id'] for receipt in results]),
                'partial': len(errors), 'errors': errors}

    def _batch_card_count(self, drop_ids):
        """删除未动卡库：按实际留下的卡计数；读取失败返回未知，不伪造为0。"""
        path = self.root / 'cards.db'
        if not drop_ids:
            return 0
        try:
            path.stat()
        except FileNotFoundError:
            return 0
        except OSError:
            return None
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            try:
                connection.execute('PRAGMA busy_timeout=8000')
                wanted = list(dict.fromkeys(drop_ids))
                marks = ','.join('?' * len(wanted))
                return connection.execute('SELECT COUNT(*) FROM cards WHERE drop_id IN (' + marks + ')',
                                          wanted).fetchone()[0]
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return None

    def _analyses(self, rows):
        """Ask the card store for this page's analysis states; a broken hook never hides the list."""
        if not self.analysis_provider:
            return {}
        try:
            states = self.analysis_provider(rows)
        except Exception:
            return {}
        return states if isinstance(states, dict) else {}

    def _public_row(self, row, analysis=None, shares=None, owned=None):
        item = dict(row)
        meta = json.loads(item['meta'] or '{}')
        file_meta = json.loads(item['file_meta'] or '{}')
        recognition = json.loads(item.get('recognition') or '{}')
        scope = meta.get('content_scope', '')
        return {'id': item['id'], 'kind': item['kind'], 'received_at': item['received_at'],
                'url': item['url'], 'title': item['title'], 'reason': item['reason'],
                'status': item['status'], 'error': item['error'], 'document_id': item['document_id'],
                'recognition':recognition,
                'analysis': self._analysis(item, analysis),
                'revision': item['revision'], 'file_path': item['file_path'], 'file': file_meta,
                'media_kind': self._media_kind({'file': file_meta, 'file_path': item['file_path'],
                                                'kind': item['kind']}),
                'display_kind': self._display_kind({'kind': item['kind'], 'file_path': item['file_path'],
                                                    'file_meta': item['file_meta']}),
                'text_chars': len(item['text'] or ''), 'content_scope': scope, 'meta': meta,
                'source_platform': meta.get('source_platform', ''), 'source_label': meta.get('source_label', ''),
                'outcome': self._outcome(item, meta),
                'state': self._state(item, meta, scope, recognition),
                'document_shares': shares if isinstance(shares, int) else 0,
                'document_owned': owned if isinstance(owned, bool) else None,
                'knowledge_url': ('/knowledge.html?document=' + item['document_id']) if item['document_id'] else ''}

    @staticmethod
    def _outcome(item, meta):
        if item['status'] == 'pending':
            return 'pending'
        if meta.get('duplicate'):
            return 'duplicate'
        if item['error']:
            return 'failed'
        return 'stored'

    def _analysis(self, item, analysis):
        if analysis is not None:
            return analysis if isinstance(analysis, dict) else {}
        wanted = {'id': item['id'], 'document_id': item['document_id']}
        return self._analyses([wanted]).get(item['id'], {})

    @staticmethod
    @staticmethod
    def _recognition_state(recognition, media_kind):
        """附件内容到底读没读出来，只看识别回执；没回执不谎报也不加戏。"""
        status = (recognition or {}).get('status', '')
        if media_kind == 'image':
            unread, read = '图片内容未识别', '图片内容已识别'
        else:
            unread, read = '视频正文与语音未读取', '视频语音已转写'
        if status == 'succeeded':
            return f'；{read}（AI 结果，未核实）'
        if status in ('failed', 'unavailable', 'interrupted'):
            reason = str((recognition or {}).get('error') or '').strip()
            return f'；{unread}（识别未完成' + (f'：{reason}' if reason else '') + '）'
        if status == 'no_audio':
            return '；视频无音轨，未生成语音转写'
        return f'；{unread}'

    @staticmethod
    def _state(item, meta, scope, recognition=None):
        """The honest one-liner the page shows; the four states never blend.

        附件与链接/文字同一条时两句都说清：正文存了什么、附件内容读没读出来。
        """
        if item['status'] == 'pending':
            return '已收下，尚未解析'
        media_kind = Intake._media_kind({'file': json.loads(item['file_meta'] or '{}'),
                                         'file_path': item['file_path'], 'kind': item['kind']})
        if item['kind'] == 'link':
            if item['error'] and not item['document_id']:
                return '抓取失败：' + item['error']
            if item['error']:
                # 三家链接要说清「只存了链接、没读到作品正文」，不能只说「仅链接」。
                tail = '；已存入知识库（仅链接，未读到作品正文）' if meta.get('source_platform') else '；已存入知识库（仅链接）'
                return '抓取失败：' + item['error'] + tail
            if meta.get('duplicate'):
                return '已存在，未重复导入'
            base = '已存入知识库，' + (scope or '原文正文未读取（仅链接）')
            return base + (Intake._recognition_state(recognition, media_kind) if media_kind else '')
        if meta.get('duplicate'):
            return '已存在，未重复导入'
        if item['kind'] == 'image':
            return '已存入知识库' + Intake._recognition_state(recognition, 'image')
        if item['kind'] == 'video':
            return '已存入知识库' + Intake._recognition_state(recognition, 'video')
        return '已存入知识库，原文逐字保存' + (
            Intake._recognition_state(recognition, media_kind) if media_kind else '')
