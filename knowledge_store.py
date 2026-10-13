"""Local, source-aware knowledge index and reusable research dossiers.

Adapters read originals; only the workbench index and explicit drafts are written.
No model or remote API is invoked by indexing, searching, or collecting evidence.
"""
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, urlencode
import uuid
from contextlib import contextmanager
from wechat_knowledge import source_db, collector_rows, radar_rows, collector_available


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(source, key):
    return hashlib.sha256((source + ':' + str(key)).encode()).hexdigest()[:32]


def safe_url(value):
    value = str(value or '')
    return value if value.startswith('/?article=') or urlsplit(value).scheme in ('http', 'https') else ''


# 退出正常检索的统一口径：手动移到「已删除」的正文仍在 documents 里（历史引用照常读原文），
# 但常规检索、分页计数和来源统计一律不数它。过滤条件只有这一份，别处不要另写一套。
NOT_TRASHED = 'NOT EXISTS(SELECT 1 FROM document_trash t WHERE t.document_id=d.id)'
# 跨库引用统计：只数有现成批量接口、且读得到的那几类。作品/线索/事项没有批量接口，
# 一律留在 unknown 里如实写「未统计」，不为了好看的 0 去全盘扫描。
# 元组：键、unknown 标签、库文件名、表名、refs JSON 列（无则 None）。提示词卡额外从 refs 里数引用。
CROSS_REFERENCE_TABLES = (('recycle_drops', '回收项', 'intake.db', 'drops', None),
                         ('prompt_cards', '提示词卡', 'cards.db', 'cards', 'refs'))
UNKNOWN_REFERENCE_LABELS = ('作品与底稿引用', '线索与事项证据')
TRASH_LIMIT = 50


class Knowledge:
    def __init__(self, store, sources=None):
        self.store = store
        self.root = store.workspace / 'knowledge'
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.path = self.root / 'knowledge.db'
        self.sync_lock = threading.Lock()
        self.stop_event = threading.Event()
        # 删掉正文后回调（可为空）。情报侧用它按文档精确清推荐来源行 ——
        # 来源粒度是文档，而链接粒度是 URL，同一 URL 有多份文档时两者对不上。
        # 默认 None：不接线就什么也不做，模块之间不互相依赖。
        self.document_removed_hook = None
        home = Path.home()
        projects = home / 'Documents/Codex/2026-10-03/b-q/work/wechat-source'
        defaults = [
            {'id': 'articles', 'name': '我的文案与作品', 'kind': 'articles'},
            {'id': 'projects', 'name': '项目资料', 'kind': 'projects', 'paths': [str(Path(__file__).parent), str(projects / 'wechat-assistant'), str(projects / 'wechat-radar')]},
            {'id': 'wechat', 'name': '本机微信', 'kind': 'wechat', 'backend': 'collector', 'path': str(home / 'Library/Application Support/WeChatRadar/wechat-assistant/collector.db'), 'names_path': str(home / 'Library/Application Support/WeChatRadar/wechat-assistant/collector.db')},
            {'id': 'mail', 'name': '邮箱 · remio', 'kind': 'mail'},
            {'id': 'remio', 'name': 'remio 资料', 'kind': 'notes'},
            {'id': 'wechat-links', 'name': '微信分享链接 · 雷达', 'kind': 'wechat_links', 'message_source': 'wechat', 'path': str(home / 'Library/Application Support/WeChatRadar/radar/radar.db')},
        ]
        with self.db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, config TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '', synced_at TEXT);
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, url TEXT NOT NULL, modified TEXT NOT NULL, tags TEXT NOT NULL, hash TEXT NOT NULL, provenance TEXT NOT NULL DEFAULT '{}', link_host TEXT GENERATED ALWAYS AS (CASE WHEN url LIKE '%mp.weixin.qq.com%' THEN 'mp.weixin.qq.com' ELSE NULL END) VIRTUAL);
                CREATE INDEX IF NOT EXISTS documents_source ON documents(source);
                CREATE INDEX IF NOT EXISTS documents_source_date ON documents(source,modified);
                CREATE INDEX IF NOT EXISTS documents_kind ON documents(kind);
                CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(title,body,content='documents',content_rowid='rowid',tokenize='trigram');
                CREATE TRIGGER IF NOT EXISTS document_insert AFTER INSERT ON documents BEGIN INSERT INTO document_fts(rowid,title,body) VALUES(new.rowid,new.title,new.body); END;
                CREATE TRIGGER IF NOT EXISTS document_delete AFTER DELETE ON documents BEGIN INSERT INTO document_fts(document_fts,rowid,title,body) VALUES('delete',old.rowid,old.title,old.body); END;
                CREATE TRIGGER IF NOT EXISTS document_update AFTER UPDATE ON documents BEGIN INSERT INTO document_fts(document_fts,rowid,title,body) VALUES('delete',old.rowid,old.title,old.body); INSERT INTO document_fts(rowid,title,body) VALUES(new.rowid,new.title,new.body); END;
                CREATE TABLE IF NOT EXISTS dossiers(id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL, modified TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS document_trash(document_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL, deleted_hash TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS document_trash_recent ON document_trash(deleted_at DESC, document_id);
            ''')
            if 'purpose' not in {r['name'] for r in c.execute('PRAGMA table_info(dossiers)')}:
                c.execute("ALTER TABLE dossiers ADD COLUMN purpose TEXT NOT NULL DEFAULT ''")
            if 'provenance' not in {r['name'] for r in c.execute('PRAGMA table_info(documents)')}:
                c.execute("ALTER TABLE documents ADD COLUMN provenance TEXT NOT NULL DEFAULT '{}'")
            # link_host 是新增的生成列：新表由上面的 CREATE TABLE 带上，旧表没有。
            # 旧库不能在这里补列（那是一次迁移，交给 coordination/migrate-knowledge-link-host.py 人工做）；
            # 只有列确实存在时才建它的索引，缺列就不建，检索侧会退回 LIKE。
            if 'link_host' in {r[1] for r in c.execute('PRAGMA table_xinfo(documents)')}:
                c.execute('CREATE INDEX IF NOT EXISTS documents_link_host ON documents(link_host)')
            for source in defaults if sources is None else sources:
                c.execute('INSERT OR IGNORE INTO sources(id,config) VALUES(?,?)', (source['id'], json.dumps(source, ensure_ascii=False)))
            if sources is None:
                legacy = c.execute("SELECT config FROM sources WHERE id='wechat'").fetchone()
                if legacy:
                    config = json.loads(legacy[0])
                    legacy_path = str(home / 'Library/Application Support/WeChatRadar/radar/radar.db')
                    collector = defaults[2]
                    if config.get('path') == legacy_path and not config.get('backend') and collector_available(collector['path']):
                        config.update(backend='collector', path=collector['path'], names_path=collector['names_path'])
                        paused = config.get('enabled') is False
                        c.execute("UPDATE sources SET config=?,status=?,detail=? WHERE id='wechat'", (json.dumps(config, ensure_ascii=False), 'paused' if paused else 'pending', '已暂停；入口接至 wechat-assistant，现有索引保留' if paused else '微信入口已接至 wechat-assistant；等待同步采集快照'))
            c.execute("UPDATE sources SET status='error',detail='上次同步被中断，请重新同步' WHERE status='syncing'")
        os.chmod(self.path, 0o600)
        from wechat_capture import WechatCapture
        self.capture = WechatCapture(self)
        source_config = store.workspace / 'sources/wechat.json'
        if source_config.is_file() and json.loads(source_config.read_text()).get('service_url'):
            from wechat_source_client import WechatSourceClient, RemoteCapture
            self.capture = RemoteCapture(WechatSourceClient(store))

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def status(self):
        with self.db() as c:
            rows = c.execute('SELECT s.*,COUNT(d.id) count,MAX(d.modified) latest FROM sources s LEFT JOIN documents d ON d.source=s.id AND ' + NOT_TRASHED + ' GROUP BY s.id ORDER BY s.rowid').fetchall()
            sources = [{**json.loads(r['config']), **{k: r[k] for k in ('status', 'detail', 'synced_at', 'count', 'latest')}} for r in rows]
            dossiers = [dict(r) for r in c.execute('SELECT id,title,modified FROM dossiers ORDER BY modified DESC')]
            # total 是「还能被检索到多少」，与左侧来源计数同一口径；已删除的正文只出现在已删除视图里。
            total = c.execute('SELECT COUNT(*) FROM documents d WHERE ' + NOT_TRASHED).fetchone()[0]
        for source in sources:
            if source.get('id') == 'wechat' and source.get('backend') == 'collector':
                source['capture'] = self.capture.status()
        return {'sources': sources, 'dossiers': dossiers, 'total': total, 'syncing': self.sync_lock.locked()}

    def _write(self, c, source, key, kind, title, body, url='', modified='', tags=(), provenance=None):
        body, title = str(body or ''), str(title or '')[:500]
        if len(body) > 2_000_000:
            raise ValueError('单份资料超过 200 万字，请拆分后导入')
        item = {'id': identifier(source, key), 'source': source, 'kind': kind, 'title': title, 'body': body, 'url': safe_url(url), 'modified': str(modified or now()), 'tags': json.dumps(list(tags), ensure_ascii=False), 'provenance': json.dumps(provenance or {}, ensure_ascii=False, sort_keys=True)}
        # Preserve the original fingerprint contract used by saved excerpt picks.
        fingerprint = {k: v for k, v in item.items() if k != 'provenance'}
        item['hash'] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        c.execute('''INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance) VALUES(:id,:source,:kind,:title,:body,:url,:modified,:tags,:hash,:provenance)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,url=excluded.url,modified=excluded.modified,tags=excluded.tags,hash=excluded.hash,provenance=excluded.provenance WHERE documents.hash != excluded.hash OR documents.provenance != excluded.provenance''', item)
        c.execute('INSERT OR IGNORE INTO seen VALUES(?)', (item['id'],))

    def sync(self, source_id=None, reserved=False):
        if not reserved and not self.sync_lock.acquire(blocking=False):
            raise ValueError('资料正在同步，请稍候')
        try:
            with self.db() as c:
                sources = [json.loads(r[0]) for r in c.execute('SELECT config FROM sources') if source_id is None or json.loads(r[0])['id'] == source_id]
            if not sources:
                raise ValueError('资料来源不存在')
            for source in sources:
                sid = source['id']
                if source['kind'] == 'manual' or source.get('enabled') is False:
                    continue
                with self.db() as c:
                    c.execute("UPDATE sources SET status='syncing',detail='正在更新本机索引' WHERE id=?", (sid,))
                try:
                    with self.db() as c:
                        c.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
                        detail, complete = self._import(c, source)
                        if complete:
                            c.execute('DELETE FROM documents WHERE source=? AND id NOT IN (SELECT id FROM seen)', (sid,))
                        count = c.execute('SELECT COUNT(*) FROM documents WHERE source=?', (sid,)).fetchone()[0]
                        state = 'ready' if count and complete else 'empty' if complete else 'partial'
                        c.execute('UPDATE sources SET status=?,detail=?,synced_at=? WHERE id=?', (state, detail, now(), sid))
                except Exception as exc:
                    # Failed imports roll back, preserving the last successful searchable copy.
                    with self.db() as c:
                        c.execute("UPDATE sources SET status='error',detail=? WHERE id=?", (str(exc)[:300], sid))
            return self.status()
        finally:
            self.sync_lock.release()

    def start_sync(self, source_id=None):
        if source_id and source_id not in {s['id'] for s in self.status()['sources']}:
            raise ValueError('资料来源不存在')
        if not self.sync_lock.acquire(blocking=False):
            return self.status()
        threading.Thread(target=self.sync, args=(source_id, True), daemon=True).start()
        return self.status()

    def configure_source(self, payload):
        if isinstance(payload, dict) and payload.get('id') == 'wechat' and self.capture.status().get('busy'):
            raise ValueError('微信正在采集，请结束后再修改范围')
        if not self.sync_lock.acquire(blocking=False):
            raise ValueError('来源正在同步，请完成后再修改范围')
        try:
            self._configure_source(payload)
        finally:
            self.sync_lock.release()
        return self.status()

    def ingest_gmail(self, payload):
        """Import connector results into the local cache; fetching is external."""
        from gmail_source import GmailCache
        if not self.sync_lock.acquire(blocking=False):
            raise ValueError('资料正在同步，请完成后再导入邮件')
        sid = None
        delegated = False
        try:
            cache = GmailCache(self.store.workspace)
            result = cache.import_messages(payload)
            account = result['account']
            sid = 'gmail-' + identifier('gmail', account)[:12]
            with self.db() as c:
                old = c.execute('SELECT config FROM sources WHERE id=?', (sid,)).fetchone()
                config = json.loads(old[0]) if old else {'id': sid, 'kind': 'gmail_cache', 'enabled': True}
                config.update(name='Gmail · ' + account, account=account, backend='connector_cache', query=result['query'], captured_at=result['captured_at'], has_more=result['has_more'])
                c.execute('INSERT INTO sources(id,config,status,detail) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config,status=excluded.status,detail=excluded.detail', (sid, json.dumps(config, ensure_ascii=False), 'pending' if config.get('enabled', True) else 'paused', '已接收邮件快照；等待索引更新'))
            if config.get('enabled', True):
                delegated = True
                status = self.sync(sid, reserved=True)
            else:
                status = self.status()
        finally:
            if not delegated:
                self.sync_lock.release()
        return {'source': sid, 'cache': result, **self.status()}

    def _configure_source(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get('id'), str):
            raise ValueError('请选择资料来源')
        with self.db() as c:
            row = c.execute('SELECT config FROM sources WHERE id=?', (payload['id'],)).fetchone()
            if not row:
                raise ValueError('资料来源不存在')
            config = json.loads(row[0])
            if config['kind'] == 'manual':
                raise ValueError('手动笔记没有自动同步范围')
            if 'enabled' in payload:
                if not isinstance(payload['enabled'], bool):
                    raise ValueError('同步开关格式错误')
                config['enabled'] = payload['enabled']
            if config['kind'] == 'wechat':
                if 'capture_enabled' in payload:
                    if type(payload['capture_enabled']) is not bool:
                        raise ValueError('新消息采集开关格式错误')
                    if payload['capture_enabled'] and (config.get('backend') != 'collector' or not self.capture.status().get('available')):
                        raise ValueError('请先配置本机 wechat-assistant 采集')
                    config['capture_enabled'] = payload['capture_enabled']
                for key in ('date_from','date_to'):
                    if key in payload:
                        value = payload[key]
                        if not isinstance(value,str) or (value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value)):
                            raise ValueError('日期请使用 YYYY-MM-DD')
                        if value:
                            datetime.fromisoformat(value)
                        config[key] = value
                if config.get('date_from') and config.get('date_to') and config['date_from'] > config['date_to']:
                    raise ValueError('起始日期不能晚于结束日期')
                if 'chatroom_ids' in payload:
                    chats = payload['chatroom_ids']
                    if not isinstance(chats,list) or len(chats)>500 or any(not isinstance(v,str) or not v.strip() or len(v)>200 or any(ord(ch)<32 for ch in v) for v in chats):
                        raise ValueError('会话范围格式错误，最多 500 个会话编号')
                    config['chatroom_ids'] = list(dict.fromkeys(v.strip() for v in chats))
            c.execute('UPDATE sources SET config=?,status=?,detail=? WHERE id=?', (json.dumps(config,ensure_ascii=False), 'pending' if config.get('enabled',True) else 'paused', '来源范围已更新，请同步' if config.get('enabled',True) else '已暂停自动同步；现有索引和引用保留',payload['id']))
        return self.status()

    def start_watch(self):
        def watch():
            next_index = time.monotonic() + 600
            while not self.stop_event.wait(60):
                self.capture.maybe_start()
                if time.monotonic() >= next_index:
                    if any(s['synced_at'] for s in self.status()['sources']):
                        self.start_sync()
                    next_index = time.monotonic() + 600
        threading.Thread(target=watch, daemon=True).start()

    def start_wechat_capture(self):
        self.capture.start()
        return self.status()

    def _import(self, c, source):
        sid, kind = source['id'], source['kind']
        if kind == 'github':
            return self.store.github.import_cache(self, c, source)
        if kind == 'wechat_links':
            from wechat_links import import_links
            return import_links(self, c, source, identifier)
        if kind == 'gmail_cache':
            from gmail_source import GmailCache
            cache = GmailCache(self.store.workspace)
            for r in cache.records(source['account']):
                provenance = {key: r.get(key) for key in ('message_id', 'thread_id', 'account', 'from', 'to', 'date_sent', 'date_header', 'rfc_message_id', 'captured_at', 'content_scope')}
                provenance['source_locator'] = r['source_url']
                self._write(c, sid, r['message_id'], 'mail', r['subject'] or '无主题邮件', r['body'], r['source_url'], r['modified'], r.get('labels', []), provenance)
            current = cache.status(source['account'])
            detail = f"Gmail 本机快照：最近查询 {current['query']}；累计缓存 {current['count']} 封，最近采集于 {current['captured_at']}；" + ('本次列表仍有后续页。' if current['has_more'] else '本次查询列表已取完。') + ' 更新索引不等于抓取新邮件；附件尚未读取。'
            # Never remove earlier snapshots when a later connector query narrows.
            return detail, False
        if kind == 'articles':
            records = self.store.listing()
            for a in records['articles']:
                d = self.store.detail(a['slug'])
                body = '\n\n'.join(b.get('text', '') for b in d['blocks'] if b['kind'] == 'text')
                body += '\n' + d['studio'].get('script', '') + '\n' + d['studio'].get('subtitles', '')
                tags = [g['name'] for g in records['collections'] if a['slug'] in g['articles']]
                url = '/?article=' + quote(a['slug']) + '&version=' + a['head']
                self._write(c, sid, a['slug'], kind, a['title'], body.strip(), url, a['updated_at'], tags, {'slug': a['slug'], 'version': a['head'], 'source_locator': url})
            return '当前作品版本；保存新版本后同步即可复用', True
        if kind == 'wechat':
            path = Path(source['path'])
            if not path.is_file():
                raise ValueError('未找到微信雷达的本机数据库')
            names = {}
            names_path = Path(source.get('names_path', ''))
            if names_path.is_file():
                names_db = sqlite3.connect(names_path.resolve().as_uri() + '?mode=ro', uri=True)
                try:
                    names = dict(names_db.execute('SELECT chatroom_id,chatroom_name FROM watched_chats'))
                finally:
                    names_db.close()
            collector = source.get('backend') == 'collector'
            count, first, last = 0, None, None
            with source_db(path) as db:
                for r in (collector_rows(db, source) if collector else radar_rows(db, source)):
                    chat, local, sender, body, clock, ts, typ, date, raw_local = r
                    modified = datetime.fromtimestamp(ts, timezone.utc).isoformat()
                    group = names.get(chat) or chat
                    clock = str(clock or '')
                    display_time = clock if clock.startswith(date) else f'{date} {clock}'
                    title = f'{group} · {sender} · {display_time}'
                    url = 'http://127.0.0.1:3000/groups/' + quote(chat, safe='') + '?date=' + quote(date) + '&local_id=' + quote(str(local))
                    provenance = {'chatroom_id': chat, 'local_id': local, 'timestamp': ts, 'database_path': str(path.resolve()), 'source_locator': url}
                    if collector:
                        provenance.update(raw_local_id=raw_local, backend='collector', sender=sender,
                            is_self=sender=='__self__' if sender else None,
                            conversation_type='group' if str(chat).endswith('@chatroom') else 'private' if str(chat).startswith('wxid_') else 'unknown',
                            content_scope='采集文本；上游单条最多 2,000 字，媒体未转录')
                    self._write(c, sid, f'{chat}:{local}', kind, title, body, url, modified, [date, group, typ], provenance)
                    count += 1
                    first = date if first is None or date<first else first
                    last = date if last is None or date>last else last
            label = 'wechat-assistant 采集快照' if collector else '微信雷达已有快照'
            return f'{label}：{first or "无"} 至 {last or "无"}，{count:,} 条；新消息依赖上游采集同步', True
        if kind == 'projects':
            count = 0
            excluded = {'node_modules', 'vendor', 'work', 'workspace', 'assets', 'uploads', 'output', 'outputs', 'dist', 'build', '__pycache__', 'inbox', 'library', 'pi-runtime', 'private', 'secrets', 'credentials'}
            for path in source['paths']:
                root = Path(path).resolve()
                if not root.is_dir():
                    raise ValueError(f'项目目录不存在：{root.name}')
                for directory, dirs, files in os.walk(root, followlinks=False):
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in excluded and not (Path(directory) / d).is_symlink()]
                    for filename in sorted(files):
                        f = Path(directory) / filename
                        if f.suffix.lower() not in ('.md', '.txt', '.rst') or f.is_symlink() or filename.startswith('.') or re.search(r'(secret|password|credential|token|keys|private)', filename, re.I):
                            continue
                        stat = f.stat()
                        if stat.st_size > 512_000:
                            continue
                        rel = str(f.relative_to(root))
                        self._write(c, sid, str(f), kind, f'{root.name} / {rel}', f.read_text(encoding='utf-8'), modified=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), tags=[root.name], provenance={'local_path': str(f), 'relative_path': rel, 'project_name': root.name, 'project_path': str(root), 'file_mtime': stat.st_mtime, 'source_locator': str(f)})
                        count += 1
                        if count >= 1000:
                            return '本次已同步 1,000 份项目文档；请缩小目录范围', False
            return f'{len(source["paths"])} 个项目目录 · 仅同步 Markdown、TXT、RST 文档', True
        if kind in ('mail', 'notes'):
            args = ['search_notes', '--limit', '-1', '--type', 'mail' if kind == 'mail' else 'note']
            data = self.remio(args)
            records = [r for r in data.get('results', []) if kind == 'mail' or (str(r.get('noteType', '')).lower() not in ('mail', 'collection', 'people') and str(r.get('noteSource', '')).lower() == 'manual')]
            for r in records[:1000]:
                note = self.remio(['read_note', r['noteId'], '--format', 'md'])
                body = str(note['content'])
                lines = body.splitlines(keepends=True)
                if lines and lines[0].rstrip('\r\n').rstrip(' \t') == '---':
                    end = next((i for i in range(1, len(lines)) if lines[i].rstrip('\r\n').rstrip(' \t') == '---'), None)
                    if end is not None and any(re.match(r'^(?:id|noteId|noteType|noteSource|createdAt|updatedAt)\s*:', line) for line in lines[1:end]):
                        body = ''.join(lines[end + 1:])
                        # Remove the renderer's separator, preserving the note body.
                        if body.startswith('\r\n'):
                            body = body[2:]
                        elif body.startswith('\n'):
                            body = body[1:]
                url = 'http://127.0.0.1:17624/notes/' + quote(r['noteId'], safe='')
                provenance = {field: r.get(field, '') for field in ('noteId', 'noteType', 'noteSource', 'localPath')}
                provenance.update({'sourceUrl': safe_url(r.get('sourceUrl')), 'collections': r.get('collections') or [], 'source_locator': url})
                self._write(c, sid, r['noteId'], kind, r['title'], body, url, r.get('modified'), r.get('collections') or [], provenance)
            if not records:
                return ('remio 暂无已同步邮件，请先在 remio 连接邮箱' if kind == 'mail' else '本次手记列表未返回资料，不代表 remio 知识库为空；仅同步 remio 手记，文件/网页等其它来源尚未接入'), True
            detail = f'通过本机 remio 同步；保留原笔记链接，共 {len(records)} 份'
            if kind == 'notes':
                detail += '；仅同步 remio 手记，文件/网页等其它来源尚未接入'
            return detail, len(records) <= 1000
        raise ValueError('不支持的来源类型')

    @staticmethod
    def remio(args):
        binary = os.environ.get('ARTICLE_STUDIO_REMIO', str(Path.home() / '.local/bin/remio'))
        result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=60)
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise ValueError('无法连接本机 remio，请确认 remio 已打开')
        if not data.get('ok'):
            raise ValueError(data.get('error') or 'remio 读取失败')
        return data['data']

    def search(self, query='', kind='', tag='', offset=0, source='', mode=''):
        query = str(query).strip()[:200]
        offset = max(0, min(int(offset), 100000))
        terms = re.findall(r'[^\s]+', query)[:12]
        conditions, args = [], []
        join, order = '', 'd.modified DESC,d.id'
        long = [t for t in terms if len(t) >= 3]
        if long:
            join = 'JOIN document_fts ON document_fts.rowid=d.rowid'
            conditions.append('document_fts MATCH ?')
            args.append(' AND '.join('"' + t.replace('"', '""') + '"' for t in long))
            order = 'bm25(document_fts),d.modified DESC,d.id'
        if mode == 'fts':
            # 只走 document_fts：不叠加 LIKE 全表扫描。没有 ≥3 字词就没有 FTS 可走，如实返回空。
            if not long:
                return {'results': [], 'total': 0, 'offset': offset, 'next_offset': None}
        else:
            for t in terms:
                conditions.append("(d.title LIKE ? ESCAPE '\\' OR d.body LIKE ? ESCAPE '\\')")
                pattern = '%' + t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                args += [pattern, pattern]
        conditions.append(NOT_TRASHED)
        if kind:
            conditions.append('d.kind=?')
            args.append(kind)
        if source:
            conditions.append('d.source=?')
            args.append(source)
        if not kind and not source:
            order = "CASE d.kind WHEN 'articles' THEN 0 WHEN 'projects' THEN 1 WHEN 'notes' THEN 2 WHEN 'mail' THEN 3 ELSE 4 END," + order
        if tag:
            conditions.append('EXISTS(SELECT 1 FROM json_each(d.tags) WHERE value=?)')
            args.append(tag)
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self.db() as c:
            total = c.execute('SELECT COUNT(*) FROM documents d ' + join + where, args).fetchone()[0]
            rows = c.execute('SELECT d.* FROM documents d ' + join + where + ' ORDER BY ' + order + ' LIMIT 30 OFFSET ?', [*args, offset]).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            body = item.pop('body')
            starts = [body.lower().find(t.lower()) for t in terms]
            at = max(0, min((x for x in starts if x >= 0), default=0) - 80)
            item['excerpt'] = body[at:at+550]
            item['excerpt_start'] = at
            item['tags'] = json.loads(item['tags'])
            item['provenance'] = json.loads(item['provenance'])
            results.append(item)
        return {'results': results, 'total': total, 'offset': offset, 'next_offset': offset + 30 if offset + 30 < total else None}

    def document(self, doc_id):
        """单条正文照旧可读 —— 已有回收项、资料夹、卡片、线索都靠它读原文。

        软删除只加两个只读事实（trashed / deleted_at），不改 hash、不改正文、不改出处：
        旧引用因此不会因为用户把知识移到「已删除」就断掉。
        """
        with self.db() as c:
            row = c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
            trash = c.execute('SELECT deleted_at FROM document_trash WHERE document_id=?', (doc_id,)).fetchone()
        if not row:
            raise ValueError('资料不存在，请重新检索')
        item = dict(row)
        item['tags'] = json.loads(item['tags'])
        item['provenance'] = json.loads(item['provenance'])
        item['trashed'] = bool(trash)
        item['deleted_at'] = trash['deleted_at'] if trash else ''
        return item

    def delete_document(self, doc_id, keep_if_referenced_by=None, require_intake_channel=''):
        """Remove one indexed document, its search index entry and its fingerprint.

        只删这一条：documents 上的触发器会把 document_fts 里的对应条目一起删掉，
        所以检索不会再命中已删正文。资料夹（dossiers）里存的是独立快照，不动。
        不存在时如实返回 removed=False，不谎报成删掉了。

        keep_if_referenced_by=(库路径, 表名) + require_intake_channel：先 ATTACH 引用库，
        再显式 BEGIN IMMEDIATE，然后把「还有引用吗」与「归属仍是回收站吗」写进同一条
        条件 DML 的 WHERE 里：不是先 COUNT 再 DELETE，独立连接没法在两者之间交进新引用
        或改回归属。条件不满足就一行也不删，并如实返回保留原因。
        守卫跑不成（接不上库等）也照样不删：宁可留正文，不误删别人的收藏。
        """
        if not isinstance(doc_id, str) or not re.fullmatch(r'[a-f0-9]{32}', doc_id):
            raise ValueError('资料编号格式无效')
        with self.db() as c:
            row = c.execute('SELECT id,rowid,source,kind,title,url,hash FROM documents WHERE id=?', (doc_id,)).fetchone()
            if not row:
                return {'id': doc_id, 'removed': False, 'title': '', 'source': '', 'kind': '', 'indexed': 0}
            if keep_if_referenced_by:
                path, table = keep_if_referenced_by
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', str(table)):
                    raise ValueError('引用表名无效')
                try:
                    c.execute('ATTACH DATABASE ? AS ref', (str(path),))
                    c.execute('BEGIN IMMEDIATE')
                    owner_guard = ''
                    args = [doc_id]
                    if require_intake_channel:
                        owner_guard = " AND json_extract(provenance,'$.intake_channel')=?"
                        args.append(require_intake_channel)
                    gone = c.execute('DELETE FROM documents WHERE id=?' + owner_guard
                                     + ' AND NOT EXISTS (SELECT 1 FROM ref."%s" WHERE document_id=?)' % table,
                                     [*args, doc_id])
                except sqlite3.Error as exc:
                    return {'id': doc_id, 'removed': False, 'title': row['title'], 'source': row['source'],
                            'kind': row['kind'], 'indexed': 0, 'referenced': None,
                            'reason': '守卫没跑成，正文保留：%s' % str(exc)[:120]}
                if not gone.rowcount:
                    return self._delete_kept(c, doc_id, row, table, require_intake_channel)
            else:
                c.execute('DELETE FROM documents WHERE id=?', (doc_id,))
            # 触发器负责同步删 document_fts；这里只核实一遍，确保检索里不留悬空条目。
            indexed = c.execute('SELECT COUNT(*) FROM document_fts WHERE rowid=?', (row['rowid'],)).fetchone()[0]
        removed_result = {'id': doc_id, 'removed': True, 'title': row['title'], 'source': row['source'],
                          'kind': row['kind'], 'indexed': indexed, 'referenced': 0}
        # 正文真删掉了才通知：情报侧按这个文档 id 精确清它的推荐来源行。
        # 钩子失败不推翻删除结果 —— 正文已经删了，如实带回错误让上层记着。
        hook = self.document_removed_hook
        if hook is not None:
            try:
                hook(doc_id)
            except Exception as exc:  # noqa: BLE001 - 附带的清理不该改写删除本身的结果
                removed_result['hook_error'] = f'{type(exc).__name__}: {exc}'[:200]
        return removed_result

    def _delete_kept(self, c, doc_id, row, table, require_intake_channel):
        """条件删除一行没删成：在同一事务里查清原因，不拿旧判断猜。"""
        current = c.execute("SELECT json_extract(provenance,'$.intake_channel') AS channel FROM documents WHERE id=?",
                            (doc_id,)).fetchone()
        base = {'id': doc_id, 'removed': False, 'title': row['title'], 'source': row['source'], 'kind': row['kind'],
                'indexed': 0}
        if not current:
            return {**base, 'referenced': 0, 'reason': '这篇正文已不在知识库（可能刚被其他入口删掉）'}
        referenced = c.execute('SELECT COUNT(*) FROM ref."%s" WHERE document_id=?' % table, (doc_id,)).fetchone()[0]
        if require_intake_channel and str(current['channel'] or '') != require_intake_channel:
            return {**base, 'referenced': referenced, 'channel': current['channel'] or '',
                    'reason': '知识库已收回归属或改由其他入口登记（出处已不是回收站渠道），未删'}
        return {**base, 'referenced': referenced, 'reason': '还有 %d 行引用这篇正文，未删' % referenced}

    # ── 手动移到「已删除」（可恢复软删除）────────────────────────────────────
    #
    # 这一段只写 document_trash 一张独立的标记表，永远不删 documents 行，也不调用
    # document_removed_hook：正文还在，原文件/remio 笔记、回收项、提示词卡、作品和
    # 已保存证据都不动。真正的硬删除仍是 delete_document（回收项自动清理走那条路）。
    TRASH_SEMANTICS = ('移到已删除后：这篇不再出现在常规检索和来源计数里，原文件与 remio 笔记不动；'
                       '回收项、提示词卡、作品与已保存证据都保留，历史引用仍可打开原文。'
                       '预览里的引用计数：提示词卡按「引用该文档的卡片数」计，同一张卡的主资料引用与 '
                       'refs 证据引用指向同一篇时只算一张。'
                       '可在「已删除」里恢复；这不是彻底抹除，文本仍留在本机。')

    @staticmethod
    def _document_ids(ids):
        """数量与编号格式在这里一次挡住；整体非法直接抛错，调用方不留半个改动。"""
        if not isinstance(ids, list) or not 1 <= len(ids) <= TRASH_LIMIT:
            raise ValueError('请选择 1–%d 条知识' % TRASH_LIMIT)
        if any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value) for value in ids):
            raise ValueError('资料编号格式无效')
        return list(dict.fromkeys(ids))

    def _cross_reference_counts(self, filename, table, wanted, refs_column=None):
        """数一处跨库引用（回收项 / 提示词卡）。读不到就是读不到：返回 None，不拿 0 冒充没有。"""
        path = self.store.workspace / 'inspiration' / filename
        if not path.is_file():
            return None
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
            try:
                if refs_column:
                    return self._card_reference_counts(connection, table, refs_column, wanted)
                marks = ','.join('?' * len(wanted))
                rows = connection.execute('SELECT document_id,COUNT(*) n FROM "%s" WHERE document_id IN (%s) GROUP BY document_id'
                                          % (table, marks), wanted).fetchall()
            finally:
                connection.close()
        except (sqlite3.Error, OSError):
            return None
        return {row[0]: row[1] for row in rows}

    def _card_reference_counts(self, connection, table, refs_column, wanted):
        """提示词卡：主资料列与 refs JSON 里的 document_id 都算，单位是「引用该文档的卡片数」。

        同一张卡既在主资料列又出现在 refs 里指向同一篇时只算一张（按 rowid 去重）。
        refs 是坏 JSON、不是数组、或数组里不是带 document_id 的对象时，整类返回 None ——
        读不懂的引用不能静默当 0。
        """
        def broken(where):
            return connection.execute('SELECT COUNT(*) FROM "%s" AS t WHERE %s' % (table, where)).fetchone()[0]
        if broken('t.%s IS NULL OR json_valid(t.%s)=0' % (refs_column, refs_column)):
            return None
        if broken("json_type(t.%s)<>'array'" % refs_column):
            return None
        if connection.execute('SELECT COUNT(*) FROM "%s" AS t,json_each(t.%s) AS item'
                              " WHERE json_type(item.value)<>'object' OR json_extract(item.value,'$.document_id') IS NULL"
                              % (table, refs_column)).fetchone()[0]:
            return None
        marks = ','.join('?' * len(wanted))
        rows = connection.execute(
            'SELECT doc,COUNT(DISTINCT card) n FROM ('
            ' SELECT t.rowid card,t.document_id doc FROM "%s" AS t WHERE t.document_id IN (%s)'
            ' UNION'
            ' SELECT t.rowid card,json_extract(item.value,\'$.document_id\') doc'
            ' FROM "%s" AS t,json_each(t.%s) AS item WHERE json_extract(item.value,\'$.document_id\') IN (%s)'
            ') GROUP BY doc' % (table, marks, table, refs_column, marks),
            list(wanted) + list(wanted)).fetchall()
        return {row[0]: row[1] for row in rows}

    def _dossier_reference_counts(self, c, wanted):
        """资料夹里存的是独立快照，删正文不动它；这里只数「快照里还留着几处原文定位」。"""
        try:
            marks = ','.join('?' * len(wanted))
            rows = c.execute("SELECT json_extract(item.value,'$.id') AS doc,COUNT(*) n FROM dossiers,json_each(dossiers.content) item"
                             " WHERE json_extract(item.value,'$.id') IN (%s) GROUP BY doc" % marks, wanted).fetchall()
        except sqlite3.Error:
            return None
        return {row['doc']: row['n'] for row in rows}

    def trash_references(self, doc_ids):
        """每篇已知引用分类计数；数不了的那类给 None，并在 unknown 里点名说没统计。

        只数能证实的（回收项、资料夹快照、提示词卡）。作品/线索/事项没有现成批量接口，
        如实留在 unknown 里 —— 不能因为数不到就显示 0，也不能合计成「只有 N 处在用」。
        """
        wanted = [doc for doc in dict.fromkeys(doc_ids) if isinstance(doc, str) and re.fullmatch(r'[a-f0-9]{32}', doc or '')]
        if not wanted:
            return {}
        counted, unknown = {}, list(UNKNOWN_REFERENCE_LABELS)
        for key, label, filename, table, refs_column in CROSS_REFERENCE_TABLES:
            counts = self._cross_reference_counts(filename, table, wanted, refs_column)
            counted[key] = counts
            if counts is None:
                # 缺库 / 坏库 / 缺表 / 读不懂的 refs：这一类没统计到，如实点名，不能显示 0。
                unknown.append(label)
        with self.db() as c:
            dossiers = self._dossier_reference_counts(c, wanted)
        counted['dossier_snapshots'] = dossiers
        if dossiers is None:
            unknown.append('资料夹快照')
        return {doc: {'counted': {key: (counts.get(doc, 0) if counts is not None else None)
                                  for key, counts in counted.items()},
                      'unknown': list(unknown),
                      'units': {'prompt_cards': '引用该文档的卡片数'}} for doc in wanted}

    def trash_preview(self, ids):
        """只读预览：这些知识能不能删、删了会怎样。不写任何东西，也不构成授权。"""
        wanted = self._document_ids(ids)
        references = self.trash_references(wanted)
        marks = ','.join('?' * len(wanted))
        with self.db() as c:
            rows = {row['id']: row for row in c.execute(
                'SELECT id,title,source,hash FROM documents WHERE id IN (' + marks + ')', wanted)}
            trashed = {row['document_id']: row for row in c.execute(
                'SELECT document_id,deleted_at,deleted_hash FROM document_trash WHERE document_id IN (' + marks + ')', wanted)}
        items = []
        for doc in wanted:
            row, mark = rows.get(doc), trashed.get(doc)
            if row:
                status, label, deleted_at = 'ready', '可移到已删除', ''
                title, digest, source = row['title'], row['hash'], row['source']
                if mark:
                    # 正文还在但已被移到已删除：如实报「已在已删除中」，并给出删除当时的指纹。
                    status, label, deleted_at = 'trashed', '已在已删除中', mark['deleted_at']
                    digest = mark['deleted_hash']
            elif mark:
                status, label, deleted_at = 'trashed', '已在已删除中（正文已不在索引）', mark['deleted_at']
                title, digest, source = '', mark['deleted_hash'], ''
            else:
                status, label, deleted_at = 'missing', '知识库里没有这条', ''
                title, digest, source = '', '', ''
            items.append({'id': doc, 'title': title or '', 'hash': digest or '', 'source': source or '',
                          'status': status, 'status_label': label, 'deleted_at': deleted_at,
                          'references': references.get(doc, {'counted': {}, 'unknown': list(UNKNOWN_REFERENCE_LABELS),
                                                             'units': {'prompt_cards': '引用该文档的卡片数'}})})
        return {'schema': 'knowledge-trash-preview/v1', 'items': items,
                'summary': {'requested': len(items),
                            'ready': sum(1 for i in items if i['status'] == 'ready'),
                            'already_trashed': sum(1 for i in items if i['status'] == 'trashed'),
                            'missing': sum(1 for i in items if i['status'] == 'missing')},
                'semantics': self.TRASH_SEMANTICS}

    def trash_many(self, items, confirm=False):
        """把选中的知识移到「已删除」：一个事务里重核存在与 hash，再写标记。

        confirm 与 expected_hash 都必须由请求带上来 —— 预览不是授权令牌，写入前一律重核。
        单条也走这个接口；重复点击同一个编号幂等（已在已删除里 → already_trashed）。
        """
        if confirm is not True:
            raise ValueError('请在确认后提交删除')
        if not isinstance(items, list) or not 1 <= len(items) <= TRASH_LIMIT:
            raise ValueError('请选择 1–%d 条知识' % TRASH_LIMIT)
        for item in items:
            if not isinstance(item, dict) or set(item) - {'id', 'expected_hash'} or set(item) != {'id', 'expected_hash'}:
                raise ValueError('删除请求包含无效字段')
            if not isinstance(item['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', item['id']):
                raise ValueError('资料编号格式无效')
            if not isinstance(item['expected_hash'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['expected_hash']):
                raise ValueError('资料版本信息缺失或无效，请重新预览后再删除')
        marks = ','.join('?' * len(items))
        current = {}
        deleted, already, missing, conflicts, errors = [], [], [], [], []
        with self.db() as c:
            # 显式写锁：与同步共用同一条 SQLite 写锁纪律，删除和同步不会互相插进半途。
            c.execute('BEGIN IMMEDIATE')
            for row in c.execute('SELECT id,title,source,hash FROM documents WHERE id IN (' + marks + ') AND NOT EXISTS(SELECT 1 FROM document_trash t WHERE t.document_id=documents.id)',
                                 [item['id'] for item in items]):
                current[row['id']] = row
            trashed = {row['document_id']: row for row in c.execute(
                'SELECT document_id,deleted_at,deleted_hash,title,source FROM document_trash WHERE document_id IN (' + marks + ')',
                [item['id'] for item in items])}
            stamp = now()
            in_batch = set()
            for item in items:
                doc, expected = item['id'], item['expected_hash']
                if doc in trashed:
                    # 已在已删除里，或同一请求里重复提交：幂等，不重复写。
                    mark = trashed[doc]
                    already.append({'id': doc, 'title': mark['title'], 'source': mark['source'], 'deleted_at': mark['deleted_at'],
                                    'reason': '同一请求里重复提交' if doc in in_batch else '已在已删除中'})
                    continue
                row = current.get(doc)
                if not row:
                    # 正文已经不在索引里就别造一个没有意义的已删除记录。
                    missing.append({'id': doc, 'reason': '知识库里没有这条，可能已被其他入口删掉'})
                    continue
                if row['hash'] != expected:
                    conflicts.append({'id': doc, 'title': row['title'], 'expected_hash': expected,
                                      'current_hash': row['hash'], 'reason': '预览之后正文已更新，请重新预览'})
                    continue
                try:
                    c.execute('INSERT INTO document_trash(document_id,deleted_at,deleted_hash,title,source) VALUES(?,?,?,?,?)',
                              (doc, stamp, row['hash'], row['title'], row['source']))
                except sqlite3.Error:
                    errors.append({'id': doc, 'reason': '这一条没有写成功，其余条目照常处理'})
                    continue
                deleted.append({'id': doc, 'title': row['title'], 'source': row['source'], 'hash': row['hash'], 'deleted_at': stamp})
                in_batch.add(doc)
                trashed[doc] = {'deleted_at': stamp, 'deleted_hash': row['hash'], 'title': row['title'], 'source': row['source']}
        # 走到这里事务已提交：报告里写 deleted 的每一条都真的在已删除表里。
        return {'schema': 'knowledge-trash/v1', 'deleted': deleted, 'already_trashed': already,
                'missing': missing, 'conflicts': conflicts, 'errors': errors,
                'summary': {'requested': len(items), 'deleted': len(deleted), 'already_trashed': len(already),
                            'missing': len(missing), 'conflicts': len(conflicts), 'errors': len(errors)}}

    def trash_list(self, offset=0, limit=30):
        """已删除列表：有界分页，并把「来源已不在索引」如实标出来。只读本机库，不读原来源。"""
        offset = max(0, min(int(offset), 100000))
        limit = max(1, min(int(limit), TRASH_LIMIT))
        with self.db() as c:
            total = c.execute('SELECT COUNT(*) FROM document_trash').fetchone()[0]
            rows = c.execute('SELECT t.document_id,t.deleted_at,t.deleted_hash,t.title,t.source,d.hash AS current_hash'
                             ' FROM document_trash t LEFT JOIN documents d ON d.id=t.document_id'
                             ' ORDER BY t.deleted_at DESC,t.document_id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        items = []
        for row in rows:
            available = row['current_hash'] is not None
            items.append({'id': row['document_id'], 'title': row['title'] or '', 'source': row['source'] or '',
                          'deleted_at': row['deleted_at'], 'hash': row['deleted_hash'] or '',
                          'body_available': available,
                          'content_changed': bool(available and row['current_hash'] != row['deleted_hash']),
                          'status': 'trashed' if available else 'source_gone',
                          'status_label': '已移出检索' if available else '来源已不在索引，解除隐藏后等待该来源同步'})
        return {'results': items, 'total': total, 'offset': offset, 'limit': limit,
                'next_offset': offset + limit if offset + limit < total else None,
                'semantics': self.TRASH_SEMANTICS}

    def restore_many(self, ids):
        """解除隐藏：只删 document_trash 里的标记，正文、hash、出处一字不改。幂等。"""
        wanted = self._document_ids(ids)
        marks = ','.join('?' * len(wanted))
        restored, unhidden, not_found = [], [], []
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            trashed = {row['document_id']: row for row in c.execute('SELECT document_id,deleted_hash FROM document_trash WHERE document_id IN (' + marks + ')', wanted)}
            rows = {row['id']: row for row in c.execute('SELECT id,title,source,hash FROM documents WHERE id IN (' + marks + ')', wanted)}
            for doc in wanted:
                if doc not in trashed:
                    not_found.append({'id': doc, 'reason': '这条不在已删除里，可能已经恢复过'})
                    continue
                c.execute('DELETE FROM document_trash WHERE document_id=?', (doc,))
                row = rows.get(doc)
                if row:
                    restored.append({'id': doc, 'title': row['title'], 'source': row['source'], 'hash': row['hash'],
                                     'content_changed': row['hash'] != trashed[doc]['deleted_hash']})
                else:
                    # 来源已被完整同步清掉：解除抑制成功，但没有正文可恢复，不能谎报已恢复。
                    unhidden.append({'id': doc, 'title': '', 'source': '',
                                     'note': '来源已不在索引，解除隐藏后等待该来源同步'})
        return {'schema': 'knowledge-restore/v1', 'restored': restored, 'unhidden': unhidden, 'not_found': not_found,
                'summary': {'requested': len(wanted), 'restored': len(restored), 'unhidden': len(unhidden),
                            'not_found': len(not_found)}}

    def document_provenance_facts(self, document_ids):
        """按编号取删除判断要用的出处字段（一次查完，不给每行单独进库）。

        只读 provenance 里的 intake_channel / drop_id：删不删知识库正文要看这篇是谁建的，
        不能只看回收项自己身上的 duplicate 标记。
        """
        wanted = [doc for doc in dict.fromkeys(document_ids) if isinstance(doc, str) and doc]
        if not wanted:
            return {}
        marks = ','.join('?' * len(wanted))
        with self.db() as c:
            rows = c.execute('SELECT id,json_extract(provenance,\'$.intake_channel\') AS channel,'
                             'json_extract(provenance,\'$.drop_id\') AS drop_id'
                             ' FROM documents WHERE id IN (' + marks + ')', wanted).fetchall()
        return {row['id']: {'intake_channel': row['channel'] or '', 'drop_id': row['drop_id'] or ''}
                for row in rows}

    def document_hashes(self, document_ids):
        """一次批量取正文指纹：只读 id,hash，不取正文（列表/提炼去重一次查完，避免逐条进库）。"""
        wanted = [doc for doc in dict.fromkeys(document_ids) if isinstance(doc, str) and doc]
        if not wanted:
            return {}
        marks = ','.join('?' * len(wanted))
        with self.db() as c:
            rows = c.execute('SELECT id,hash FROM documents WHERE id IN (' + marks + ')', wanted).fetchall()
        return {row['id']: row['hash'] for row in rows}

    def add_project(self, payload):
        path = Path(str(payload.get('path', ''))).expanduser()
        if not path.is_absolute() or not path.is_dir() or not path.resolve().is_relative_to(Path.home()) or path.resolve() == Path.home():
            raise ValueError('请选择本机用户目录下的具体项目文件夹')
        config = {'id': 'project-' + identifier('project', str(path.resolve()))[:12], 'name': str(payload.get('name') or path.name)[:80], 'kind': 'projects', 'paths': [str(path.resolve())]}
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO sources(id,config) VALUES(?,?)', (config['id'], json.dumps(config, ensure_ascii=False)))
        self.start_sync(config['id'])
        return config

    def add_note(self, payload, provenance=None, verbatim=False):
        # provenance is optional and only adds facts; omitting it keeps the old record byte-for-byte.
        # verbatim is for entries that must keep the text exactly as typed (leading/trailing blanks
        # and newlines included); the default path still trims, exactly as before.
        provenance = self._extra_provenance(provenance)
        title, raw_body = str(payload.get('title', '')).strip(), str(payload.get('body', ''))
        body = raw_body.strip()
        if not title or not body or len(body) > 100000:
            raise ValueError('请填写标题和正文，正文最多 10 万字')
        if verbatim:
            body = raw_body
            if len(body) > 100000:
                raise ValueError('请填写标题和正文，正文最多 10 万字')
        key = uuid.uuid4().hex
        tags = [x.strip()[:80] for x in str(payload.get('tags', '')).split(',') if x.strip()][:12]
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO sources(id,config,status) VALUES(?,?,?)', ('manual', json.dumps({'id': 'manual', 'name': '手动收集', 'kind': 'manual'}, ensure_ascii=False), 'ready'))
            c.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            self._write(c, 'manual', key, 'notes', title, body, payload.get('url', ''), now(), tags, provenance)
            c.execute("UPDATE sources SET detail='手动收集的笔记与资料',synced_at=? WHERE id='manual'", (now(),))
        return self.document(identifier('manual', key))

    def add_reference(self, payload, provenance=None):
        from external_reference import metadata
        collected_at = now()
        values, record = metadata(payload, collected_at)
        # Optional facts added by the collecting entry (e.g. 创意回收工厂). Validation,
        # the canonical url and the author fields stay exactly as metadata() decided them.
        provenance = {**record, **self._extra_provenance(provenance)}
        sid, key = 'external-references', provenance['canonical_url']
        ident = identifier(sid, key)
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM documents WHERE id=?', (ident,)).fetchone():
                result = self.document(ident)
                result['duplicate'] = True
                return result
            config = {'id': sid, 'name': '外部文章收藏', 'kind': 'manual', 'document_kind': 'external'}
            c.execute('INSERT OR IGNORE INTO sources(id,config,status) VALUES(?,?,?)', (sid, json.dumps(config, ensure_ascii=False), 'ready'))
            c.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            self._write(c, sid, key, 'external', values['title'], values['body'], values['url'], collected_at,
                        [t.strip()[:80] for t in values['tags'].split(',') if t.strip()][:12], provenance)
            c.execute("UPDATE sources SET detail='他人作品与出处；收藏理由和原文分开保存，链接收藏不表示已导入全文',synced_at=? WHERE id=?", (collected_at, sid))
        return self.document(ident)

    @staticmethod
    def _extra_provenance(value):
        if value is None:
            return {}
        if not isinstance(value, dict) or any(not isinstance(key, str) or not key for key in value):
            raise ValueError('资料出处补充信息格式无效')
        if set(value) & {'sourceUrl', 'canonical_url', 'platform', 'ownership', 'author', 'author_handle',
                         'author_basis', 'captured_at'}:
            raise ValueError('资料出处补充信息不能覆盖出处与作者字段')
        return {key: val for key, val in value.items()}

    def dossier(self, dossier_id):
        with self.db() as c:
            row = c.execute('SELECT * FROM dossiers WHERE id=?', (dossier_id,)).fetchone()
        if not row:
            raise ValueError('资料夹不存在')
        items = json.loads(row['content'])
        for item in items:
            item.setdefault('provenance', {})
        return {**dict(row), 'items': items}

    def save_dossier(self, payload):
        title = str(payload.get('title', '')).strip()[:200]
        purpose = str(payload.get('purpose', '')).strip()[:2000]
        if payload.get('id') and 'items' not in payload:
            if not isinstance(payload['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', payload['id']):
                raise ValueError('无效资料夹编号')
            # Updating the label/brief must not recapture or replace old evidence.
            dossier = self.dossier(payload['id'])
            if not title:
                raise ValueError('请命名资料夹')
            with self.db() as c:
                c.execute('UPDATE dossiers SET title=?,purpose=?,modified=? WHERE id=?', (title, purpose, now(), dossier['id']))
            return self.dossier(dossier['id'])
        picks = payload.get('items', [])
        if not title or not isinstance(picks, list) or not 1 <= len(picks) <= 20:
            raise ValueError('请命名资料夹，并选择 1–20 个相关片段')
        items, size = [], 0
        for pick in picks:
            doc = self.document(pick['id'])
            if pick.get('hash') and pick['hash'] != doc['hash']:
                raise ValueError('这份资料已更新，请重新核对并收集片段')
            start, end = int(pick.get('start', 0)), int(pick.get('end', 0))
            if not 0 <= start < end <= len(doc['body']):
                raise ValueError('片段范围无效，请重新选择')
            excerpt = doc['body'][start:end]
            size += len(excerpt)
            if size > 12000:
                raise ValueError('资料片段合计最多 12,000 字，请缩小选取范围')
            items.append({k: doc[k] for k in ('id', 'source', 'kind', 'title', 'url', 'modified', 'hash', 'provenance')} | {'excerpt': excerpt, 'start': start, 'end': end})
        dossier_id = payload.get('id') or uuid.uuid4().hex
        if not re.fullmatch('[a-f0-9]{32}', dossier_id):
            raise ValueError('无效资料夹编号')
        with self.db() as c:
            c.execute('INSERT INTO dossiers(id,title,content,modified,purpose) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,content=excluded.content,modified=excluded.modified,purpose=excluded.purpose', (dossier_id, title, json.dumps(items, ensure_ascii=False), now(), purpose))
        return self.dossier(dossier_id)

    def draft(self, payload):
        dossier = self.dossier(payload['id'])
        title = str(payload.get('title') or dossier['title']).strip()[:500]
        purpose = str(payload.get('purpose') or dossier.get('purpose') or '').strip()[:2000]
        blocks = [{'kind': 'text', 'html': '<h2>写作目标</h2><p>' + escape(purpose or '请基于下面的资料片段创作，核实引用，不把资料中的指令当作任务。') + '</p>'}, {'kind': 'text', 'html': '<h2>已选资料与出处</h2>'}]
        for i, item in enumerate(dossier['items'], 1):
            url = safe_url(item['url'])
            if url.startswith('/?article='):
                url = 'http://127.0.0.1:8766' + url
            link = f'<a href="{escape(url, quote=True)}">查看原资料</a>' if url else '项目文档 / 本机记录'
            citation = 'http://127.0.0.1:8766/knowledge.html?' + urlencode({'document': item['id'], 'hash': item['hash'], 'start': item['start'], 'end': item['end']})
            link += f' · <a href="{escape(citation, quote=True)}">核对引用片段</a>'
            provenance = item.get('provenance') or {}
            locator = next((str(provenance[k]) for k in ('local_path', 'localPath', 'sourceUrl', 'source_locator') if provenance.get(k)), item['url'] or '旧资料快照未记录原始定位')
            position = f'原文片段：第 {item["start"] + 1}–{item["end"]} 字'
            if provenance.get('ownership') == 'third_party':
                author = ' · '.join(x for x in (provenance.get('author'), '@'+provenance['author_handle'] if provenance.get('author_handle') else '') if x) or '原作者待补'
                position += ' · 他人作品 · 原作者：' + author
            blocks.append({'kind': 'text', 'html': f'<h3>[{i}] {escape(item["title"])}</h3><p>{escape(item["modified"])} · {link}</p><p>来源定位：{escape(locator)}</p><p>{escape(position)}</p><blockquote>{escape(item["excerpt"]).replace(chr(10), "<br>")}</blockquote>'})
        with self.store.lock:
            created = self.store.create({'title': title})
            saved = self.store.save(created['slug'], {'base_version': created['head'], 'title': title, 'blocks': blocks, 'summary': '从知识库资料夹建立写作底稿'})
            refs = self.store.article(created['slug']) / 'knowledge-sources.json'
            refs.write_text(json.dumps({'dossier': dossier['id'], 'version': saved['version'], 'items': dossier['items'], 'created_at': now()}, ensure_ascii=False, indent=2))
            os.chmod(refs, 0o600)
        self.start_sync('articles')
        return {'slug': created['slug'], 'version': saved['version'], 'url': '/?article=' + quote(created['slug']), 'sources': len(dossier['items'])}
