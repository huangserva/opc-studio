"""Bounded local retrieval for Pi; evidence previews never write to the index."""
import copy
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit


RETRIEVAL_TOOLS = {'search_knowledge', 'read_knowledge', 'search_events', 'read_event', 'propose_event'}
OUTPUT_BUDGET = 16000
MAX_READ = 4000
SOURCE_FIELDS = ('ownership', 'author', 'author_handle', 'author_basis', 'platform', 'published_on',
                 'sourceUrl', 'content_scope', 'captured_at', 'discovered_via', 'intake_channel',
                 'source_locator', 'local_path', 'localPath', 'relative_path', 'project_name',
                 'slug', 'version', 'chatroom_id', 'local_id', 'timestamp', 'noteId',
                 'noteType', 'noteSource', 'account', 'message_id', 'thread_id',
                 'rfc_message_id', 'from', 'to', 'date_sent', 'original_message_url', 'reader_note')


def bounded_string(value, maximum=240):
    return str(value or '')[:maximum]


def integer(value, default, minimum, maximum, label):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{label}必须为 {minimum}–{maximum} 的整数')
    return value


def clean_id(value, document=False):
    pattern = r'[a-f0-9]{32}' if document else r'[A-Za-z0-9_.:-]{1,128}'
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError('无效资料编号' if document else '无效事件编号')
    return value


def local_or_source_url(value):
    value = bounded_string(value, 2048)
    if value.startswith(('/?article=', '/knowledge.html?', '/events.html?')):
        return value
    try:
        parts = urlsplit(value)
        if parts.scheme in ('https', 'http') and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return ''


def source_metadata(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in SOURCE_FIELDS:
        item = value.get(key)
        if isinstance(item, str):
            result[key] = local_or_source_url(item) if key in ('sourceUrl','original_message_url') else item[:500]
        elif isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
            result[key] = item
    # Bound arbitrary source metadata separately from the selected passage.
    while len(json.dumps(result, ensure_ascii=False)) > 1000:
        result.pop(next(reversed(result)))
    return result


def document_link(identity, start, end, digest):
    return '/knowledge.html?' + urlencode({'document': identity, 'start': start, 'end': end, 'hash': digest})


def query_terms(query, supplied=None):
    """Extract conservative keywords; caller-specified terms are preferred, not a semantic model."""
    if not isinstance(query, str) or len(query) > 500 or any(ord(c) < 32 for c in query):
        raise ValueError('检索问题最多 500 字，不能包含控制字符')
    if supplied is not None:
        if not isinstance(supplied, list) or not 1 <= len(supplied) <= 6:
            raise ValueError('query_terms 必须包含 1–6 个关键词')
        if any(not isinstance(t, str) or not t.strip() or len(t) > 80 or any(ord(c) < 32 for c in t) for t in supplied):
            raise ValueError('每个关键词为 1–80 字，不包含控制字符')
        return list(dict.fromkeys(t.strip() for t in supplied))
    text = re.sub(r'请帮我|帮我|请你|帮忙|麻烦|查一下|查下|查找|查询|搜索|看看|找一下|找找|列出|总结一下|分析一下', ' ', query)
    text = re.sub(r'有没有|有哪些|有什么|什么时候|什么时候|是谁|谁负责|是否|怎么|如何|为什么|哪些|什么|最近|之前|讨论过|讨论|提到过|提到|有关|关于|相关资料|相关事项|相关|资料|记录|情况|进展|一下|我们|我的|我想|微信里|微信中|邮箱里|邮件里|知识库里', ' ', text)
    # Do not remove individual Chinese characters from names such as 中国、参与。
    text = re.sub(r'(?<![\u3400-\u9fff])(?:的|吗|呢)|(?:的|吗|呢)(?![\u3400-\u9fff])', ' ', text)
    text = re.sub(r'(?<![\u3400-\u9fff])(?:里|中|和|与|及|了)(?![\u3400-\u9fff])', ' ', text)
    return list(dict.fromkeys(re.findall(r'[A-Za-z0-9][A-Za-z0-9_.+-]*|[\u3400-\u9fff]{2,}', text)))[:6]


def character_terms(terms):
    return list(dict.fromkeys(t[i:i+2] for t in terms if re.fullmatch(r'[\u3400-\u9fff]{4,}', t)
                              for i in range(len(t)-1)))[:12]


def candidate_due(value):
    if not value:
        return ''
    if len(value)>50 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('候选期限须为含时区的 ISO 日期时间；不能确认具体时间请留空')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('候选期限日期时间无效') from None
    return parsed.isoformat()


class KnowledgeTools:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def emit(result, job):
        used = job.get('_knowledge_output_chars', 0)
        result = copy.deepcopy(result)
        # Include the budget envelope in the measured JSON delivered to the model.
        size = 0
        for _ in range(4):
            result['retrieval_budget'] = {'limit_chars': OUTPUT_BUDGET, 'used_chars': used + size,
                                          'remaining_chars': max(0, OUTPUT_BUDGET - used - size)}
            new_size = len(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
            if new_size == size:
                break
            size = new_size
        if used + size > OUTPUT_BUDGET:
            raise ValueError(f'本轮资料输出预算不足（剩余 {max(0, OUTPUT_BUDGET-used)} 字）；请缩小读取范围或下一条消息继续')
        job['_knowledge_output_chars'] = used + size
        return result

    @staticmethod
    def check_budget(job):
        if job.get('cancel'):
            raise ValueError('任务已停止')
        if job.get('_knowledge_output_chars', 0) >= OUTPUT_BUDGET - 160:
            raise ValueError('本轮资料输出预算已用尽，请下一条消息继续')

    def execute(self, name, args, job):
        self.check_budget(job)
        if not isinstance(args, dict):
            raise ValueError('检索参数必须是对象')
        return getattr(self, name)(args, job)

    def search_knowledge(self, args, job):
        query = args.get('query', '')
        terms = query_terms(query, args.get('query_terms'))
        if query.strip() and not terms:
            raise ValueError('请提供具体主题关键词，或使用 query_terms 指定 1–6 个检索词')
        limit = integer(args.get('limit'), 5, 1, 8, '搜索结果数')
        filters = {}
        for key in ('source', 'kind'):
            value = args.get(key, '')
            if not isinstance(value, str) or len(value) > 80:
                raise ValueError('来源或资料类型格式错误')
            filters[key] = value
        knowledge = self.store.knowledge
        combined = ' '.join(terms)
        # The existing index accepts 200 characters; never silently lose terms.
        data = knowledge.search(combined, **filters) if len(combined)<=200 else {'results':[], 'total':None}
        entries = data.get('results', [])
        mode, exact_total = 'keyword_and', data.get('total', 0)
        if terms and not entries:
            mode, exact_total = 'keyword_candidates', None
            candidates = {}
            for term in terms:
                for entry in knowledge.search(term, **filters).get('results', []):
                    candidates.setdefault(entry['id'], entry)
            # Natural Chinese sentences may have an unsplit noun phrase. Fall
            # back to bounded character candidates, which must be read and verified.
            if not candidates and args.get('query_terms') is None:
                for gram in character_terms(terms):
                    for entry in knowledge.search(gram, **filters).get('results', []):
                        candidates.setdefault(entry['id'], entry)
                if candidates:
                    mode = 'character_candidates'
            entries = sorted(candidates.values(), key=lambda e: -sum(t.casefold() in (str(e.get('title', ''))+' '+str(e.get('excerpt', ''))).casefold() for t in terms))
        results = []
        for entry in entries[:limit]:
            start = max(0, int(entry.get('excerpt_start', 0)))
            excerpt = bounded_string(entry.get('excerpt'), 280)
            results.append({'id': entry['id'], 'title': bounded_string(entry.get('title'), 180),
                            'source': bounded_string(entry.get('source'), 80), 'kind': bounded_string(entry.get('kind'), 80),
                            'modified': bounded_string(entry.get('modified'), 50), 'hash': bounded_string(entry.get('hash'), 128),
                            'excerpt': excerpt, 'start': start, 'end': start+len(excerpt),
                            'source_url': local_or_source_url(entry.get('url')),
                            'url': document_link(entry['id'], start, start+len(excerpt), entry.get('hash', '')),
                            'provenance': source_metadata(entry.get('provenance'))})
        return self.emit({'query_terms': terms, 'query_mode': mode, 'total': exact_total, 'results': results,
                          'summary': f'找到 {len(results)} 条资料候选；关键词候选需 read_knowledge 核对原文，不能当已确认事件'}, job)

    def read_knowledge(self, args, job):
        identity = clean_id(args.get('id'), document=True)
        digest = args.get('hash')
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('资料 hash 格式无效')
        if '_knowledge_context' in job:
            return self.read_frozen_knowledge(identity, digest, args, job)
        doc = self.store.knowledge.document(identity)
        if digest is not None and digest != doc['hash']:
            raise ValueError('资料已更新，请重新核对 hash')
        body = doc['body']
        start = integer(args.get('start'), 0, 0, len(body), '起始字符位置')
        limit = integer(args.get('max_chars'), 2000, 1, MAX_READ, '读取字符数')
        end = min(start+limit, len(body))
        result = self.emit({'id': identity, 'title': bounded_string(doc.get('title'), 180),
                            'source': bounded_string(doc.get('source'), 80), 'kind': bounded_string(doc.get('kind'), 80),
                            'hash': doc['hash'], 'start': start, 'end': end, 'text': body[start:end],
                            'total_chars': len(body), 'next_start': end if end < len(body) else None,
                            'source_url': local_or_source_url(doc.get('url')),
                            'url': document_link(identity, start, end, doc['hash']),
                            'modified': bounded_string(doc.get('modified'), 50), 'provenance': source_metadata(doc.get('provenance')),
                            'summary': f'已读取资料第 {start}–{end} 字符；位置为 Unicode 字符、从 0 开始、结束位置不包含'}, job)
        if end > start:
            self.record_knowledge_read(doc, start, end, body[start:end], result['url'], job)
        return result

    @staticmethod
    def record_knowledge_read(doc, start, end, text, url, job):
        record = {'id': doc['id'], 'hash': doc['hash'], 'start': start, 'end': end}
        ledger = job.setdefault('_knowledge_reads', [])
        if record in ledger:
            return
        ledger.append(record)
        citation = {**record, 'title': bounded_string(doc.get('title'), 180), 'url': url,
                    'source_url': local_or_source_url(doc.get('url')),
                    'source': bounded_string(doc.get('source'), 80), 'kind': bounded_string(doc.get('kind'), 80),
                    'modified': bounded_string(doc.get('modified'), 50), 'state': doc.get('state', 'fresh'),
                    'read_at': datetime.now(timezone.utc).isoformat(),
                    'excerpt': text, 'provenance': source_metadata(doc.get('provenance'))}
        citations = job.setdefault('_knowledge_citations', [])
        citations.append(citation)
        # This is the actual read ledger, independent of the model's prose.
        # Keep the selected snapshot for later inspection after source changes.
        job['citations'] = copy.deepcopy(citations)

    def read_frozen_knowledge(self, identity, digest, args, job):
        context = job['_knowledge_context']
        if (not isinstance(context, dict) or not isinstance(context.get('items'), list)
                or not 1 <= len(context['items']) <= 20
                or any(not isinstance(item, dict) for item in context['items'])):
            raise ValueError('本轮固定知识上下文无效')
        candidates = [item for item in context['items'] if item.get('id') == identity
                      and (digest is None or item.get('hash') == digest)]
        if not candidates:
            raise ValueError('只能读取本轮来源清单中的固定资料与 hash')
        for item in candidates:
            if (type(item.get('start')) is not int or type(item.get('end')) is not int
                    or not isinstance(item.get('excerpt'), str)
                    or not 0 <= item['start'] < item['end']
                    or len(item['excerpt']) != item['end'] - item['start']):
                raise ValueError('本轮固定知识快照片段格式无效')
        start = integer(args.get('start'), candidates[0]['start'], 0, 2000000, '起始字符位置')
        limit = integer(args.get('max_chars'), 2000, 1, MAX_READ, '读取字符数')
        candidates = [item for item in candidates if item['start'] <= start < item['end']
                      and ('max_chars' not in args or start + limit <= item['end'])]
        if not candidates:
            raise ValueError('读取范围超出本轮已选知识片段，请按来源清单的 start/end 读取')
        item = candidates[0]
        end = min(start + limit, item['end'])
        text = item['excerpt'][start - item['start']:end - item['start']]
        state = item.get('state', 'fresh')
        state_text = {'fresh': '收集时原文与当前索引一致', 'stale': '原资料已更新；本次读取已选旧快照',
                      'missing': '当前索引已无此资料；本次读取已保存快照'}.get(state, '快照状态未知')
        result = self.emit({'id': identity, 'title': bounded_string(item.get('title'), 180),
                            'source': bounded_string(item.get('source'), 80), 'kind': bounded_string(item.get('kind'), 80),
                            'hash': item['hash'], 'start': start, 'end': end, 'text': text,
                            'total_chars': len(item['excerpt']), 'scope_start': item['start'], 'scope_end': item['end'],
                            'next_start': end if end < item['end'] else None,
                            'source_url': local_or_source_url(item.get('url')),
                            'url': document_link(identity, start, end, item['hash']),
                            'modified': bounded_string(item.get('modified'), 50),
                            'provenance': source_metadata(item.get('provenance')), 'state': state, 'frozen': True,
                            'summary': f'已读取固定片段第 {start}–{end} 字符（0 开始、结束不包含）；{state_text}；未读取清单外资料'}, job)
        self.record_knowledge_read(item, start, end, text, result['url'], job)
        return result

    def search_events(self, args, job):
        query = args.get('query', '')
        terms = query_terms(query, args.get('query_terms'))
        if query.strip() and not terms:
            raise ValueError('请提供具体事件关键词')
        limit = integer(args.get('limit'), 5, 1, 8, '搜索结果数')
        view = args.get('view', 'all')
        if view not in ('all', 'important', 'todos', 'mine', 'waiting', 'confirm', 'calendar'):
            raise ValueError('事件视图必须为 all/important/todos/mine/waiting/confirm/calendar')
        events = getattr(self.store, 'events', None)
        if events is None:
            raise ValueError('事件库尚未连接，不能据此判断没有事项')
        combined = ' '.join(terms)
        data = events.list_events(query=combined, view=view, limit=limit) if len(combined)<=200 else {'events':[]}
        entries = data.get('events', data.get('results', data.get('items', []))) if isinstance(data, dict) else data
        mode = 'keyword_phrase'
        if terms and not entries:
            mode = 'keyword_candidates'
            candidates = {}
            for term in terms:
                batch = events.list_events(query=term, view=view, limit=limit)
                batch = batch.get('events', batch.get('results', batch.get('items', []))) if isinstance(batch, dict) else batch
                for entry in batch:
                    candidates.setdefault(entry['id'], entry)
            if not candidates and args.get('query_terms') is None:
                for gram in character_terms(terms):
                    batch = events.list_events(query=gram, view=view, limit=limit)
                    batch = batch.get('events', []) if isinstance(batch, dict) else batch
                    for entry in batch:
                        candidates.setdefault(entry['id'], entry)
                if candidates:
                    mode = 'character_candidates'
            entries = sorted(candidates.values(), key=lambda e: -sum(t.casefold() in (str(e.get('title', ''))+' '+str(e.get('summary', ''))).casefold() for t in terms))
        results = [self.event_summary(entry) for entry in entries[:limit]]
        return self.emit({'results': results, 'query_terms': terms, 'query_mode': mode,
                          'summary': f'找到 {len(results)} 条事件候选；日程和任务以确认状态与证据为准'}, job)

    @staticmethod
    def event_summary(event):
        result = {key: bounded_string(event.get(key), maximum) for key, maximum in
                  [('id',128), ('title',180), ('summary',320), ('priority',30), ('status',40),
                   ('created_at',50), ('updated_at',50), ('event_at',50), ('due_at',50)] if key in event}
        if 'needs_confirmation' in event:
            result['needs_confirmation'] = bool(event['needs_confirmation'])
        if type(event.get('revision')) is int:
            result['revision'] = event['revision']
        result['url'] = '/events.html?' + urlencode({'event': result.get('id', '')})
        return result

    def read_event(self, args, job):
        identity = clean_id(args.get('id'))
        events = getattr(self.store, 'events', None)
        if events is None:
            raise ValueError('事件库尚未连接，不能据此判断没有事项')
        event = events.event(identity)
        if not event:
            raise ValueError('事件不存在，请重新检索')
        result = self.event_summary(event)
        result['summary'] = bounded_string(event.get('summary'), 1200)
        actions = event.get('actions', [])
        result['actions'] = []
        for action in actions[:6]:
            if not isinstance(action, dict):
                continue
            item = {key: bounded_string(action.get(key), 300 if key == 'summary' else 100)
                    for key in ('id', 'summary', 'owner', 'due_at', 'status') if key in action}
            item['owner_is_self'] = bool(action.get('owner_is_self'))
            item['evidence_ids'] = [ref for ref in action.get('evidence_ids', [])[:6]
                                    if isinstance(ref,str) and re.fullmatch('[a-f0-9]{32}',ref)]
            item['evidence_ids_truncated'] = len(action.get('evidence_ids', []))>6
            result['actions'].append(item)
        evidence = event.get('evidence', event.get('items', []))
        result['evidence'] = []
        for item in evidence[:6]:
            if not isinstance(item, dict):
                continue
            ref = {key: bounded_string(item.get(key), 180) for key in ('id', 'document_id', 'hash', 'title', 'source', 'kind') if key in item}
            if item.get('state') in ('fresh', 'stale', 'missing'):
                ref['state'] = item['state']
            for key in ('start', 'end'):
                if isinstance(item.get(key), int) and not isinstance(item[key], bool):
                    ref[key] = item[key]
            ref['excerpt'] = bounded_string(item.get('excerpt', item.get('quote')), 240)
            doc_id = item.get('document_id') or item.get('doc_id') or item.get('id')
            if isinstance(doc_id, str) and re.fullmatch('[a-f0-9]{32}', doc_id) and 'start' in ref and 'end' in ref:
                ref['url'] = document_link(doc_id, ref['start'], ref['end'], item.get('hash', ''))
            else:
                ref['url'] = local_or_source_url(item.get('url'))
            result['evidence'].append(ref)
        history = event.get('history', event.get('updates', []))
        result['history'] = [{key: bounded_string(item.get(key), 240 if key in ('summary', 'note', 'reason') else 80)
                              for key in ('id', 'at', 'created_at', 'status', 'summary', 'note', 'reason', 'actor') if key in item}
                             for item in history[-5:] if isinstance(item, dict)]
        result['truncated'] = {'actions': len(actions)>6, 'evidence': len(evidence)>6, 'history': len(history)>5}
        result['retrieval_notice'] = '已读取事件摘要与有限证据；事件证据的原文须 read_knowledge 核对。未经确认的关联是建议。'
        return self.emit(result, job)

    def propose_event(self, args, job):
        title, summary = args.get('title'), args.get('summary', '')
        if not isinstance(title, str) or not title.strip() or len(title)>200 or '\x00' in title or not isinstance(summary, str) or len(summary)>1200 or '\x00' in summary:
            raise ValueError('候选事件需要 1–200 字标题和不超过 1,200 字摘要')
        priority = args.get('priority', 'normal')
        if priority not in ('normal', 'important'):
            raise ValueError('候选优先级必须为 normal/important')
        picks = args.get('picks')
        if not isinstance(picks, list) or not 1 <= len(picks) <= 6:
            raise ValueError('候选事件需要 1–6 个已读取的原文片段')
        valid, items, total = [], [], 0
        for pick in picks:
            if not isinstance(pick, dict):
                raise ValueError('证据片段格式错误')
            identity = clean_id(pick.get('id'), document=True)
            start = integer(pick.get('start'), None, 0, 100000000, '证据起始位置')
            end = integer(pick.get('end'), None, 1, 100000000, '证据结束位置')
            if start is None or end is None or end <= start:
                raise ValueError('证据片段范围无效')
            digest = pick.get('hash')
            reads = [r for r in job.get('_knowledge_reads', []) if r['id']==identity and r['hash']==digest]
            cursor = start
            for record in sorted(reads, key=lambda r:r['start']):
                if record['start'] <= cursor < record['end']:
                    cursor = max(cursor, record['end'])
            if cursor < end:
                raise ValueError('候选证据必须来自本轮 read_knowledge 已读取的完整范围与 hash；搜索摘要不能直接入事件')
            doc = self.store.knowledge.document(identity)
            if doc['hash'] != digest or end > len(doc['body']):
                raise ValueError('原资料已更新，请重新读取核对候选证据')
            ref = {'id':identity, 'hash':digest, 'start':start, 'end':end}
            if ref in valid:
                continue
            if not doc['body'][start:end].strip():
                raise ValueError('候选证据不能是空白片段')
            total += end-start
            if total>12000:
                raise ValueError('候选证据片段合计最多 12,000 字')
            valid.append(ref)
            items.append({**ref, 'title':bounded_string(doc.get('title'),180),
                          'excerpt':doc['body'][start:min(end,start+240)],
                          'source':bounded_string(doc.get('source'),80),
                          'url':document_link(identity,start,end,digest)})
        candidates = args.get('action_candidates', [])
        if not isinstance(candidates,list) or len(candidates)>6:
            raise ValueError('行动候选最多 6 项')
        actions = []
        for candidate in candidates:
            if not isinstance(candidate,dict) or not isinstance(candidate.get('summary'),str) or not candidate['summary'].strip() or len(candidate['summary'])>500 or '\x00' in candidate['summary']:
                raise ValueError('行动候选需要具体动作摘要，最多 500 字')
            owner, due = candidate.get('owner',''), candidate.get('due_at','')
            if not isinstance(owner,str) or len(owner)>100 or '\x00' in owner or not isinstance(due,str) or len(due)>80 or not isinstance(candidate.get('owner_is_self',False),bool):
                raise ValueError('行动负责人或日期格式错误')
            actions.append({'summary':candidate['summary'], 'owner':owner, 'owner_is_self':candidate.get('owner_is_self',False),
                            'due_at':candidate_due(due), 'status':'needs_confirmation', 'evidence_ids':[]})
        proposal = {'title':title.strip(), 'summary':summary, 'priority':priority, 'needs_confirmation':True,
                    'picks':valid, 'items':copy.deepcopy(valid), 'action_candidates':actions}
        result = self.emit({'saved':False, 'proposal':proposal, 'review_url':'/events.html',
                            'evidence_preview':items,
                            'summary':'仅生成候选预览，尚未保存；关联、负责人和期限需用户核对，证据链接可回查原文'},job)
        # Pi's existing action projection persists these bounded, reviewed fields.
        actions_log = job.get('actions', [])
        if actions_log and actions_log[-1].get('name')=='propose_event' and actions_log[-1].get('status')=='running':
            actions_log[-1]['event_proposal'] = copy.deepcopy(proposal)
        return result
