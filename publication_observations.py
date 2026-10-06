"""Immutable platform observations with explicit, traceable version associations."""
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlencode

from publication_metrics import identity, now, payload_object, platform_url, text, timestamp

SOURCE = 'publication_observations'
NOTICE = '平台页面采集资料；尚未关联工作台发布版本，统计时间区间未提供，未通过平台 API 校验。'


class PublicationObservations:
    def __init__(self, metrics):
        self.metrics = metrics
        self.store = metrics.store
        with metrics.db(write=True) as c:
            c.execute('''CREATE TABLE IF NOT EXISTS observations(
                id TEXT PRIMARY KEY, observed_at TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE, data TEXT NOT NULL, knowledge_id TEXT, created_at TEXT
            )''')
            if 'created_at' not in {row['name'] for row in c.execute('PRAGMA table_info(observations)')}:
                c.execute('ALTER TABLE observations ADD COLUMN created_at TEXT')

    @staticmethod
    def _post(value, analytics=False, allow_tracking=False):
        parsed = urlsplit(platform_url(value, 'x'))
        match = re.fullmatch(r'/([A-Za-z0-9_]{1,15})/status/([0-9]{1,25})' +
                             (r'/analytics' if analytics else ''), parsed.path)
        if not match or parsed.hostname not in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com') or not allow_tracking and (parsed.query or parsed.fragment):
            raise ValueError('请提供具体 X 原帖或其 analytics 页面，不得包含登录参数')
        return match.group(1).lower(), match.group(2)

    def observe(self, payload):
        payload_object(payload, ('platform', 'account', 'post_url', 'source_url', 'observed_at',
                                'title', 'raw', 'metrics'), '平台采集资料')
        if payload.get('platform') != 'x':
            raise ValueError('当前平台页面采集仅接入 X')
        owner, post = self._post(payload.get('post_url'))
        if self._post(payload.get('source_url'), True) != (owner, post):
            raise ValueError('统计页面与原帖不是同一个账号和帖子')
        if payload.get('account') != owner:
            raise ValueError('采集账号与原帖作者不一致')
        observed, when = timestamp(payload.get('observed_at'), '页面采集时间')
        if when > datetime.now(timezone.utc):
            raise ValueError('页面采集时间不能晚于当前时间')
        title = text(payload.get('title'), '资料标题', 160, True)
        raw = payload.get('raw')
        if not isinstance(raw, str) or not raw or len(raw) > 32000 or '\x00' in raw:
            raise ValueError('请保留最多 32,000 字的原始统计页面文本')
        fields = payload.get('metrics')
        if not isinstance(fields, list) or not 1 <= len(fields) <= 40:
            raise ValueError('请提供 1–40 个具有原文位置的指标')
        entries, seen = [], set()
        for field in fields:
            payload_object(field, ('scope', 'label', 'value', 'start', 'end'), '原始指标')
            scope = text(field.get('scope'), '指标分组', 80, True)
            label = text(field.get('label'), '平台指标名称', 80, True)
            value = text(field.get('value'), '平台显示值', 200, True)
            start, end = field.get('start'), field.get('end')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw) or end-start > 1000:
                raise ValueError('指标原文位置无效')
            snippet = raw[start:end]
            if label not in snippet or value not in snippet:
                raise ValueError('指标名称或原始显示值不在指定原文片段内')
            if (scope, label) in seen:
                raise ValueError('同组指标名称不能重复')
            seen.add((scope, label))
            entries.append({'scope': scope, 'label': label, 'value': value, 'start': start, 'end': end})
        data = {'platform': 'x', 'account': owner, 'post_id': post, 'post_url': payload['post_url'],
                'source_url': payload['source_url'], 'observed_at': observed, 'title': title,
                'raw': raw, 'raw_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'metrics': entries,
                'version_binding': None, 'window': None, 'platform_verified': False, 'notice': NOTICE}
        return self._save(data)

    def _save(self, data):
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        ident = fingerprint[:32]
        with self.metrics.lock, self.metrics.db(write=True) as c:
            c.execute('INSERT OR IGNORE INTO observations(id,observed_at,fingerprint,data,created_at) VALUES(?,?,?,?,?)',
                      (ident, data['observed_at'], fingerprint, encoded, now()))
        return self.get(ident)

    def bind(self, ident, payload):
        payload_object(payload, ('slug', 'delivery_id', 'version'), '发布版本关联')
        if not isinstance(payload.get('version'), str):
            raise ValueError('请核对发布项的固定版本')
        with self.metrics.lock:
            parent = self.get(ident)
            binding = self.metrics._binding(payload.get('slug'), payload.get('delivery_id'), 'x', payload['version'])
            if self._post(binding['url'], allow_tracking=True) != (parent['account'], parent['post_id']):
                raise ValueError('发布登记链接与采集的原帖不一致，不能按标题关联')
            if binding['account'] and binding['account'].lower().lstrip('@') != parent['account']:
                raise ValueError('发布登记账号与原帖作者不一致')
            if parent['version_binding']:
                if parent['version_binding'] != binding:
                    raise ValueError('已关联资料不能改绑；请从原始采集资料核对')
                return parent
            data = {k: v for k, v in parent.items() if k not in ('id', 'hash', 'knowledge_id', 'url', 'created_at')}
            data.update(version_binding=binding, parent_id=parent['id'], parent_hash=parent['hash'],
                        title=parent['title'].removesuffix('（版本待关联）') + ' · ' + binding['version'],
                        notice='按已登记链接关联固定作品版本；统计时间区间未提供，远端内容未通过平台 API 复核。')
            linked = self._save(data)
            if parent['knowledge_id']:
                self.export_to_knowledge(linked['id'])
            return self.get(linked['id'])

    @staticmethod
    def _decode(row, full=True):
        result = json.loads(row['data'])
        if not full:
            result.pop('raw')
        return {**result, 'id': row['id'], 'hash': row['fingerprint'], 'knowledge_id': row['knowledge_id'], 'created_at': row['created_at'],
                'url': '/feedback.html?' + urlencode({'observation': row['id']})}

    def listing(self):
        with self.metrics.db() as c:
            total = c.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
            rows = c.execute('SELECT * FROM observations ORDER BY julianday(observed_at) DESC,id LIMIT 50').fetchall()
        return {'observations': [self._decode(row, False) for row in rows], 'total': total}

    def get(self, ident):
        identity(ident)
        with self.metrics.db() as c:
            row = c.execute('SELECT * FROM observations WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise ValueError('平台采集资料不存在')
        return self._decode(row)

    def export_to_knowledge(self, ident):
        with self.metrics.lock:
            item = self.get(ident)
            binding = item['version_binding']
            body = [item['notice'], '平台：X', '账号：@' + item['account'], '原帖：' + item['post_url'],
                    '统计来源：' + item['source_url'], '页面采集时间：' + item['observed_at'],
                    '工作台版本：' + (binding['slug'] + ' / ' + binding['version'] if binding else '待关联；没有按标题绑定到当前草稿'), '统计窗口：平台界面未显示',
                    '快照指纹：' + item['hash'], '原文 SHA-256：' + item['raw_sha256'],
                    '本机采集资料：' + item['url'], '\n## 原始显示值']
            if binding:
                body.extend(['固定版本入口：' + binding['work_url'], '发布登记编号：' + binding['delivery_id'],
                             '原始采集资料：/feedback.html?' + urlencode({'observation': item['parent_id']}),
                             '原始采集指纹：' + item['parent_hash']])
            body.extend(f"{m['scope']} / {m['label']}：{m['value']}" for m in item['metrics'])
            body.extend(['\n## 原始页面文本', item['raw'],
                         '\n本资料可作为主题资料夹依据。' + ('原帖的实际发布时间、远端内容是否有修改及统计窗口仍需核对；尚未生成模型复盘。' if binding else '原帖的实际发布时间、工作台版本及统计窗口仍需核对；尚未生成模型复盘。')])
            knowledge = self.store.knowledge
            from knowledge_store import identifier
            with knowledge.db() as c:
                c.execute('INSERT OR IGNORE INTO sources(id,config,status) VALUES(?,?,?)',
                          (SOURCE, json.dumps({'id': SOURCE, 'name': '平台发布资料', 'kind': 'manual'}, ensure_ascii=False), 'ready'))
                c.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
                knowledge._write(c, SOURCE, ident, 'notes', item['title'], '\n\n'.join(body),
                                 item['post_url'], item['observed_at'], ['发布表现', 'X', '已关联发布登记' if binding else '版本待关联'],
                                 {'source_locator': item['url'], 'sourceUrl': item['source_url'],
                                  'platform': 'x', 'account': item['account'], 'post_id': item['post_id'],
                                  'post_url': item['post_url'], 'observed_at': item['observed_at'],
                                  'observation_id': ident, 'observation_hash': item['hash'],
                                  'raw_sha256': item['raw_sha256'], 'version_binding': binding,
                                  'window': None, 'platform_verified': False,
                                  **({'slug': binding['slug'], 'version': binding['version'],
                                      'delivery_id': binding['delivery_id'], 'parent_id': item['parent_id'],
                                      'parent_hash': item['parent_hash']} if binding else {})})
                c.execute('UPDATE sources SET status=?,detail=?,synced_at=? WHERE id=?',
                          ('ready', '平台页面原始资料；保留原帖，工作台版本与统计窗口待核对', now(), SOURCE))
            document_id = identifier(SOURCE, ident)
            doc = knowledge.document(document_id)
            with self.metrics.db(write=True) as c:
                c.execute('UPDATE observations SET knowledge_id=? WHERE id=?', (document_id, ident))
            return {'observation_id': ident, 'document_id': document_id, 'hash': doc['hash'],
                    'url': '/knowledge.html?' + urlencode({'document': document_id, 'start': 0,
                                                          'end': len(doc['body']), 'hash': doc['hash']}),
                    'summary': ('平台原始资料及固定发布版本已回库；统计窗口仍待核对' if binding else
                                '平台原始资料已回库；工作台发布版本仍待关联')}
