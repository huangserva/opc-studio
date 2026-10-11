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
            rows = c.execute('SELECT s.*,COUNT(d.id) count,MAX(d.modified) latest FROM sources s LEFT JOIN documents d ON d.source=s.id GROUP BY s.id ORDER BY s.rowid').fetchall()
            sources = [{**json.loads(r['config']), **{k: r[k] for k in ('status', 'detail', 'synced_at', 'count', 'latest')}} for r in rows]
            dossiers = [dict(r) for r in c.execute('SELECT id,title,modified FROM dossiers ORDER BY modified DESC')]
            total = c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
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
        with self.db() as c:
            row = c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not row:
            raise ValueError('资料不存在，请重新检索')
        item = dict(row)
        item['tags'] = json.loads(item['tags'])
        item['provenance'] = json.loads(item['provenance'])
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
