"""Work-level publication review: metric series, peer comparison, review notes.

Every number here comes from a snapshot the user imported or from an explicitly
requested read-only Buffer query. Nothing is estimated, extrapolated or published.
The review note is the user's own conclusion; it reaches the knowledge base only
when the user clicks, and the organization-wide Buffer figures are labelled as
organization-wide so they can never be read as one work's performance.

Storage stays in the same local database as the snapshots, and the review is
always rebuilt from them, so a deleted snapshot cannot leave a stale figure.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import statistics
from urllib.parse import urlencode

from publication_metrics import now, payload_object, slug_value, text
from publishing.registry import PLATFORMS

SOURCE = 'publication_reviews'
REVIEW_NOTICE = ('复盘只使用用户导入的原始统计快照，未向平台校验；指标口径与时间窗口不同不能直接比较，'
                 '登记时间不是实际发布时间，当前数据不证明某种写法导致表现变化。')
PEER_NOTICE = '同类作品指同一平台、同一作品类型且已导入统计的其他作品；每个作品只取该指标最近一次数值，样本少时只能作参考。'
SERIES_NOTICE = '同一指标按采集时间从早到晚排列；区间统计与累计统计分开展示，差值只在同一口径且原值可解析为数值时给出。'
OVERVIEW_NOTICE = 'Buffer 只提供组织级汇总（该 CLI 没有按帖指标），不能归因到单个作品，只作同期背景。'
MAX_NOTES = 20000
MAX_POINTS = 100
MAX_PEERS = 20
DAY = re.compile(r'\d{4}-\d{2}-\d{2}')
METRIC_FIELDS = {
    'x': ['Views', 'Impressions', 'Engagements', 'Likes', 'Reposts', 'Replies', 'Bookmarks',
          'Quotes', 'Video views', 'Link clicks', 'Profile visits', 'Follows'],
    'wechat': ['阅读', '点赞', '在看', '转发', '收藏', '评论', '公众号关注', '分享'],
    'douyin': ['播放量', '点赞', '评论', '分享', '收藏', '完播率', '涨粉'],
    'xiaohongshu': ['浏览量', '点赞', '收藏', '评论', '转发', '新增关注'],
    'channels': ['观看量', '点赞', '转发', '收藏', '评论', '新增关注'],
}


def number(value):
    """Parse only unambiguous plain numbers; '1.2K' or '3万' stay unparsed on purpose."""
    if isinstance(value, bool):
        return None, ''
    if isinstance(value, (int, float)):
        return (float(value), '') if abs(value) <= 10 ** 18 else (None, '')
    if not isinstance(value, str):
        return None, ''
    cleaned = value.strip().replace(',', '').replace(' ', '').replace('\u3000', '')
    unit = ''
    if cleaned.endswith('%'):
        unit, cleaned = '%', cleaned[:-1]
    if re.fullmatch(r'[+-]?\d+(\.\d+)?', cleaned):
        return float(cleaned), unit
    return None, unit


def display(value):
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return ('%.4f' % value).rstrip('0').rstrip('.')
    return str(value)


def day(value, label):
    """Accept a plain date or an ISO instant and normalise it to a UTC date."""
    text_value = str(value or '').strip()
    if DAY.fullmatch(text_value):
        return text_value
    try:
        parsed = datetime.fromisoformat(text_value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(label + '须为 YYYY-MM-DD 或含时区的日期时间') from None
    if parsed.tzinfo is None:
        raise ValueError(label + '须带时区')
    return parsed.astimezone(timezone.utc).date().isoformat()


class PublicationReviews:
    def __init__(self, metrics):
        self.metrics = metrics
        self.store = metrics.store
        with metrics.db(write=True) as c:
            c.execute('''CREATE TABLE IF NOT EXISTS reviews(
                slug TEXT PRIMARY KEY, updated_at TEXT NOT NULL, data TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS overviews(
                id TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL)''')

    # --- stored state ---------------------------------------------------------------
    def _state(self, slug):
        with self.metrics.db() as c:
            row = c.execute('SELECT * FROM reviews WHERE slug=?', (slug,)).fetchone()
        if row is None:
            return {'notes': '', 'notes_updated_at': '', 'documents': []}
        return json.loads(row['data'])

    def _save_state(self, slug, state):
        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with self.metrics.lock, self.metrics.db(write=True) as c:
            c.execute('INSERT INTO reviews(slug,updated_at,data) VALUES(?,?,?) '
                      'ON CONFLICT(slug) DO UPDATE SET updated_at=excluded.updated_at,data=excluded.data',
                      (slug, state.get('notes_updated_at') or now(), encoded))

    # --- facts ----------------------------------------------------------------------
    def _work_type(self, slug, version):
        try:
            from content_store import studio_at
            _, _, package = self.store.info(slug, version)
            return str(studio_at(package).get('type') or '')[:40]
        except (ValueError, OSError, KeyError):
            return ''

    def works(self):
        groups = {}
        for binding in self.metrics.bindings()['bindings']:
            entry = groups.setdefault(binding['slug'], {'slug': binding['slug'], 'title': binding['title'],
                                                        'type': self._work_type(binding['slug'], binding['version']),
                                                        'platforms': [], 'deliveries': 0,
                                                        'work_url': binding['work_url']})
            entry['deliveries'] += 1
            if binding['platform'] not in entry['platforms']:
                entry['platforms'].append(binding['platform'])
        for entry in groups.values():
            entry['snapshots'] = self.metrics.listing(slug=entry['slug'], limit=1)['total']
            entry['note_updated_at'] = self._state(entry['slug']).get('notes_updated_at', '')
        return {'works': sorted(groups.values(), key=lambda item: item['slug']),
                'notice': REVIEW_NOTICE, 'metric_fields': METRIC_FIELDS}

    def _snapshots(self, slug):
        rows = self.metrics.listing(slug=slug, limit=MAX_POINTS)['snapshots']
        return sorted(rows, key=lambda row: (row['binding']['delivery_id'], row['collected_at'], row['id']))

    def _series(self, snapshots):
        grouped = {}
        for row in snapshots:
            key = (row['binding']['delivery_id'], row['range']['kind'])
            for label, value in row['metrics'].items():
                amount, unit = number(value)
                grouped.setdefault(key, {}).setdefault(label, []).append({
                    'collected_at': row['collected_at'], 'range_start': row['range']['start'],
                    'range_end': row['range']['end'], 'value': display(value), 'number': amount,
                    'unit': unit, 'provenance': row['provenance']['type'], 'snapshot_id': row['id'],
                    'knowledge_id': row['knowledge_id']})
        series = []
        for (delivery_id, kind), labels in grouped.items():
            for label, points in labels.items():
                points.sort(key=lambda point: (point['collected_at'], point['snapshot_id']))
                previous = None
                for point in points[:MAX_POINTS]:
                    if point['number'] is not None:
                        if previous is not None and point['unit'] == previous['unit']:
                            point['delta'] = point['number'] - previous['number']
                        previous = point
                    elif previous is not None:
                        # The earlier value was comparable but this one cannot be
                        # parsed, so the gap stays visibly unknown instead of 0.
                        point['delta'] = None
                series.append({'delivery_id': delivery_id, 'range_kind': kind, 'label': label,
                               'points': points[:MAX_POINTS]})
        series.sort(key=lambda item: (item['delivery_id'], item['range_kind'], item['label']))
        return series

    def _latest(self, series):
        latest = {}
        for item in series:
            for point in item['points']:
                current = latest.get(item['label'])
                if current is None or (point['collected_at'], item['delivery_id']) >= (current['collected_at'], current['delivery_id']):
                    latest[item['label']] = {'value': point['value'], 'number': point['number'], 'unit': point['unit'],
                                             'collected_at': point['collected_at'], 'range_kind': item['range_kind'],
                                             'delivery_id': item['delivery_id']}
        return latest

    def _peer_works(self, slug, platform, work_type):
        peers = []
        for binding in self.metrics.bindings()['bindings']:
            if binding['slug'] == slug or binding['platform'] != platform:
                continue
            if any(item['slug'] == binding['slug'] for item in peers):
                continue
            if work_type and self._work_type(binding['slug'], binding['version']) != work_type:
                continue
            peers.append({'slug': binding['slug'], 'title': binding['title'], 'version': binding['version']})
            if len(peers) >= MAX_PEERS:
                break
        return peers

    def _peers(self, slug, bindings, latest):
        result = []
        for platform in sorted({binding['platform'] for binding in bindings}):
            work_type = self._work_type(slug, next(b['version'] for b in bindings if b['platform'] == platform))
            peers = self._peer_works(slug, platform, work_type)
            values = {}
            for peer in peers:
                for label, row in self._latest(self._series(self._snapshots(peer['slug']))).items():
                    values.setdefault(label, []).append({'slug': peer['slug'], 'title': peer['title'], **row})
            metrics = []
            for label, row in sorted(latest.items()):
                others = [item for item in values.get(label, []) if item['number'] is not None]
                if not others:
                    continue
                amounts = [item['number'] for item in others]
                metrics.append({'label': label, 'value': row['value'], 'number': row['number'], 'unit': row['unit'],
                                'count': len(amounts), 'median': statistics.median(amounts),
                                'minimum': min(amounts), 'maximum': max(amounts),
                                'peers': [{'slug': item['slug'], 'title': item['title'], 'value': item['value']}
                                          for item in others]})
            result.append({'platform': platform, 'type': work_type, 'works': peers,
                           'metrics': metrics, 'notice': PEER_NOTICE})
        return result

    def review(self, slug):
        slug_value(slug)
        bindings = [binding for binding in self.metrics.bindings()['bindings'] if binding['slug'] == slug]
        if not bindings:
            raise ValueError('该作品还没有可核对的已登记发布项，不能生成复盘')
        snapshots = self._snapshots(slug)
        series = self._series(snapshots)
        latest = self._latest(series)
        observations = [item for item in self.metrics.observations.listing()['observations']
                        if (item.get('version_binding') or {}).get('slug') == slug]
        return {'slug': slug, 'title': bindings[0]['title'], 'type': self._work_type(slug, bindings[0]['version']),
                'work_url': bindings[0]['work_url'], 'platforms': sorted({b['platform'] for b in bindings}),
                'deliveries': [{'delivery_id': b['delivery_id'], 'platform': b['platform'], 'version': b['version'],
                                'url': b['url'], 'account': b['account'], 'registration_at': b['registration_at'],
                                'work_url': b['work_url'], 'kind': b['delivery_kind']} for b in bindings],
                'series': series, 'latest': latest, 'peers': self._peers(slug, bindings, latest),
                'observations': [{'id': item['id'], 'account': item['account'], 'post_url': item['post_url'],
                                  'source_url': item['source_url'], 'observed_at': item['observed_at'],
                                  'title': item['title'], 'metrics': item['metrics'],
                                  'knowledge_id': item['knowledge_id'], 'url': item['url']}
                                 for item in observations],
                'snapshots': len(snapshots), 'note': self._state(slug), 'overviews': self.overviews(),
                'notice': REVIEW_NOTICE, 'series_notice': SERIES_NOTICE,
                'metric_fields': METRIC_FIELDS.get(bindings[0]['platform'], [])}

    # --- review notes ---------------------------------------------------------------
    def save(self, slug, payload):
        slug_value(slug)
        payload_object(payload, ('notes',), '复盘笔记')
        notes = text(payload.get('notes', ''), '复盘笔记', MAX_NOTES)
        self.review(slug)
        state = self._state(slug)
        state['notes'] = notes
        state['notes_updated_at'] = now()
        self._save_state(slug, state)
        return {'slug': slug, 'notes': notes, 'notes_updated_at': state['notes_updated_at'],
                'documents': state['documents'], 'summary': '复盘笔记已保存在本机；尚未回库。'}

    def note_body(self, slug):
        review = self.review(slug)
        state = review['note']
        if not state.get('notes', '').strip():
            raise ValueError('请先写下复盘结论，再存为知识库笔记')
        lines = ['# ' + review['title'] + ' · 发布复盘', review['notice'],
                 '作品：' + review['slug'] + ' · 类型：' + (review['type'] or '未标注'),
                 '平台：' + '、'.join(PLATFORMS.get(item, item) for item in review['platforms']),
                 '复盘笔记更新时间：' + state.get('notes_updated_at', ''),
                 '\n## 复盘结论（用户本人撰写）', state['notes'].strip(),
                 '\n## 指标事实（用户导入的原始快照，未向平台校验）']
        for label, row in sorted(review['latest'].items()):
            lines.append('- ' + label + '：' + row['value'] + '（' +
                         ('累计' if row['range_kind'] == 'cumulative' else '区间') +
                         ' · 采集于 ' + row['collected_at'] + '）')
        lines.append('\n## 同类对比（' + PEER_NOTICE + '）')
        for group in review['peers']:
            lines.append('### ' + PLATFORMS.get(group['platform'], group['platform']) +
                         ' · ' + (group['type'] or '类型未标注') + ' · 同类 ' + str(len(group['works'])) + ' 个')
            for item in group['metrics']:
                lines.append('- ' + item['label'] + '：本作品 ' + item['value'] + ' / 同类中位 ' +
                             display(item['median']) + '（' + str(item['count']) + ' 个同类，' +
                             display(item['minimum']) + '–' + display(item['maximum']) + '）')
            if not group['metrics']:
                lines.append('- 同类作品还没有可比较的同一指标')
        lines.append('\n## 依据的发布项与快照')
        for delivery in review['deliveries']:
            lines.append('- ' + PLATFORMS.get(delivery['platform'], delivery['platform']) + ' · 固定版本 ' +
                         delivery['version'] + ' · ' + delivery['url'] + ' · 登记于 ' + delivery['registration_at'])
        for item in self.metrics.listing(slug=slug, limit=MAX_POINTS)['snapshots']:
            lines.append('  - 快照 ' + item['id'] + ' · ' + item['collected_at'] + ' · ' +
                         ('原始 CSV' if item['provenance']['type'] == 'csv' else '手动录入') +
                         ' · 指纹 ' + item['hash'] + ' · 本机 ' + item['url'])
        if review['observations']:
            lines.append('\n## 平台页面采集资料')
            for item in review['observations']:
                lines.append('- ' + item['title'] + ' · ' + item['post_url'] + ' · 采集于 ' + item['observed_at'] +
                             ' · 本机 ' + item['url'])
        if review['overviews']:
            lines.append('\n## 同期 Buffer 组织级汇总（不是本作品数据）')
            for item in review['overviews'][:3]:
                lines.append('- ' + item['window']['start'] + ' 至 ' + item['window']['end'] + '：' +
                             '、'.join(metric['name'] + ' ' + display(metric['value']) for metric in item['metrics'][:12]))
        lines.append('\n复盘结论与事实分开保存；结论由用户撰写，工作台不自动改写写作铁律或风格库。')
        return review, '\n\n'.join(lines)

    def export_note(self, slug):
        with self.metrics.lock:
            review, body = self.note_body(slug)
            digest = hashlib.sha256(body.encode()).hexdigest()
            state = self._state(slug)
            known = next((item for item in state['documents'] if item['hash'] == digest), None)
            if known:
                document = self.store.knowledge.document(known['document_id'])
                return {'slug': slug, 'document_id': known['document_id'], 'hash': digest, 'duplicate': True,
                        'url': '/knowledge.html?' + urlencode({'document': known['document_id'], 'start': 0,
                                                               'end': len(document['body']), 'hash': document['hash']}),
                        'summary': '同一份复盘笔记已在知识库，未重复写入。'}
            document = self.store.knowledge.add_note(
                {'title': review['title'] + ' · 发布复盘', 'body': body, 'url': review['work_url'],
                 'tags': '发布复盘,' + ','.join(review['platforms'])},
                {'slug': review['slug'], 'type': review['type'], 'review_updated_at': state['notes_updated_at'],
                 'platforms': review['platforms'], 'source_locator': '/feedback.html?' + urlencode({'review': slug}),
                 'delivery_ids': [item['delivery_id'] for item in review['deliveries']],
                 'review_body_sha256': digest})
            state['documents'].insert(0, {'document_id': document['id'], 'hash': digest, 'created_at': now()})
            state['documents'] = state['documents'][:20]
            self._save_state(slug, state)
            return {'slug': slug, 'document_id': document['id'], 'hash': digest, 'duplicate': False,
                    'url': '/knowledge.html?' + urlencode({'document': document['id'], 'start': 0,
                                                           'end': len(document['body']), 'hash': document['hash']}),
                    'summary': '复盘笔记已存为知识库笔记（候选资料）；未自动改写写作铁律或风格库。'}

    # --- organization-wide Buffer figures (read-only, explicit click) ---------------
    def overviews(self, limit=5):
        with self.metrics.db() as c:
            rows = c.execute('SELECT * FROM overviews ORDER BY julianday(fetched_at) DESC,created_at DESC LIMIT ?',
                             (limit,)).fetchall()
        return [json.loads(row['data']) for row in rows]

    def fetch_overview(self, payload):
        from publishing.transports import buffer_cli
        payload_object(payload, ('start', 'end', 'organization_id', 'channel_ids'), '组织级统计')
        window = {'start': day(payload.get('start'), '统计开始日期'), 'end': day(payload.get('end'), '统计结束日期')}
        if window['start'] > window['end']:
            raise ValueError('统计开始日期不能晚于结束日期')
        command = self.store.buffer_command()
        timeout = self.store.buffer_timeout()
        organization = payload.get('organization_id')
        if organization:
            if not isinstance(organization, str) or not buffer_cli.ORGANIZATION_ID.fullmatch(organization):
                raise ValueError('Buffer 组织编号无效')
            organization = {'id': organization, 'name': ''}
        else:
            listed = buffer_cli.organizations(command=command, timeout=timeout)
            if not listed:
                raise ValueError('Buffer 账号没有可用组织，未读取任何统计')
            if len(listed) > 1:
                raise ValueError('该 Buffer 账号有多个组织，请先选择：' +
                                 '、'.join((item['name'] or item['id']) for item in listed))
            organization = listed[0]
        result = buffer_cli.aggregated_metrics(organization['id'], window['start'], window['end'],
                                               payload.get('channel_ids'), command=command, timeout=timeout)
        if result['outcome'] != 'ok':
            raise ValueError(result['message'])
        data = {'schema_version': 1, 'scope': 'organization', 'organization_id': organization['id'],
                'organization_name': organization['name'], 'window': result['window'], 'metrics': result['metrics'],
                'metrics_updated_at': result['metrics_updated_at'], 'fetched_at': now(),
                'platform_verified': False, 'notice': OVERVIEW_NOTICE}
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.metrics.lock, self.metrics.db(write=True) as c:
            c.execute('INSERT OR IGNORE INTO overviews(id,fetched_at,created_at,data) VALUES(?,?,?,?)',
                      (digest[:32], data['fetched_at'], now(), encoded))
        return {**data, 'id': digest[:32], 'summary': result['message']}
