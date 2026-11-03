"""兴趣报告只读通道；方向清单采用版本检查及同目录原子替换。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import grok_bot_radar as grok

MAX_BYTES = 2 * 1024 * 1024
LOCK = threading.RLock()
POST_URL = re.compile(r'https://x\.com/[A-Za-z0-9_]{1,15}/status/([0-9]{1,25})')
DEFAULT_TOPICS = ['新模型发布（开源权重、量化版、MLX 版）', '本地部署与推理（llama.cpp、vLLM、MLX、4090 实测）',
                  'AI 视频与图像模型（H3、ComfyUI 工作流、LoRA）', '新 API / 推理引擎 / 模型网关', 'Agent 编程工具（Claude Code、Codex、MCP）']
DEFAULT_DOCUMENT = '# 兴趣通道方向清单\n\n工作台维护。Grok Bot 跑兴趣通道时：本文件存在则以它为准。\n\n## 当前方向\n' + ''.join('- ' + t + '\n' for t in DEFAULT_TOPICS) + '\n## 说明\n- 只收「新 + 沾边 + 拿来就能用」（权重 / API / 仓库 / 教程 / 参数）\n- 不要：纯新闻、纯观点、领福利、搞钱口号\n'


class Conflict(ValueError):
    pass


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError('文件类型或大小不支持')
        raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('文件过大')
        return raw, info


def _document(directory=None):
    path = (Path(directory) if directory is not None else grok.reports_dir()) / 'interest-topics.md'
    if path.parent.is_symlink():
        raise ValueError('兴趣目录不能是符号链接')
    try:
        raw, info = _read(path)
        revision = str(info.st_mtime_ns) + ':' + hashlib.sha256(raw).hexdigest()
        return raw.decode('utf-8'), revision
    except FileNotFoundError:
        return DEFAULT_DOCUMENT, 'missing'


def _section(document):
    lines = document.splitlines(keepends=True)
    headings = [i for i, line in enumerate(lines) if line.rstrip('\r\n') == '## 当前方向']
    if len(headings) != 1:
        raise ValueError('清单须包含唯一的「## 当前方向」段')
    start = headings[0] + 1
    end = next((i for i in range(start, len(lines)) if re.match(r'#{1,6} ', lines[i])), len(lines))
    bullets = [i for i in range(start, end) if lines[i].startswith('- ')]
    return lines, start, end, bullets


def topics(directory=None):
    document, revision = _document(directory)
    lines, _, _, bullets = _section(document)
    return {'topics': [lines[i][2:].rstrip('\r\n') for i in bullets], 'revision': revision}


def save_topics(payload):
    if not isinstance(payload, dict) or set(payload) != {'topics', 'revision'}:
        raise ValueError('只允许 topics 和 revision')
    rows = payload['topics']
    if not isinstance(rows, list) or len(rows) > 30:
        raise ValueError('最多 30 个方向')
    cleaned = []
    for row in rows:
        if not isinstance(row, str) or any(c in row for c in '\r\n\x00\u2028\u2029'):
            raise ValueError('方向须为不含换行的文字')
        row = row.strip()
        if not row or len(row) > 80 or row.startswith('#'):
            raise ValueError('方向须为 1–80 字，不能以 # 开头')
        if row not in cleaned:
            cleaned.append(row)
    with LOCK:
        document, revision = _document()
        if revision != payload['revision']:
            raise Conflict('文件已被修改，请刷新')
        lines, start, end, bullets = _section(document)
        newline = '\r\n' if '\r\n' in document else '\n'
        replacements = {i: '- ' + cleaned[n] + newline if n < len(cleaned) else '' for n, i in enumerate(bullets)}
        # 只替换列表行，其他文字、空行及段落的字节和顺序保持不变。
        extra = ''.join('- ' + row + newline for row in cleaned[len(bullets):])
        insert = bullets[-1] + 1 if bullets else start
        output = ''.join((extra if i == insert else '') + replacements.get(i, line) for i, line in enumerate(lines))
        if insert == len(lines):
            output += extra
        path = grok.reports_dir() / 'interest-topics.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.interest-topics-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                if revision != 'missing':
                    os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
                stream.write(output.encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            if _document()[1] != revision:
                raise Conflict('文件已被修改，请刷新')
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return topics()


def safe_url(value):
    if not isinstance(value, str) or re.search(r'[\s\x00-\x1f]', value):
        return None
    try:
        url = urlsplit(value)
        return value if url.scheme in ('http', 'https') and url.hostname and not url.username and not url.password else None
    except ValueError:
        return None


def _rich(value, depth=0):
    if depth > 8:
        return None
    if isinstance(value, list):
        return [_rich(v, depth + 1) for v in value]
    if isinstance(value, dict):
        result = {k: _rich(v, depth + 1) for k, v in value.items()}
        # HTML 补充正文先过共用清洗器，再交给已有 textContent 渲染器。
        html = value.get('html') or value.get('body_html')
        if isinstance(html, str):
            from html import escape
            from html_sanitize import clean_html_full, SafeHTML
            class PlainHTML(SafeHTML):
                def _escape_data(self, data, at_line_start=False):
                    return escape(data)
            result['full_text'] = clean_html_full(html, PlainHTML)[1]
            result.pop('html', None)
            result.pop('body_html', None)
        return result
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return None


def _post(row, moment):
    identifier = row.get('id')
    identifier = str(identifier) if isinstance(identifier, int) and not isinstance(identifier, bool) else identifier
    if not isinstance(identifier, str) or not grok.ID_RE.fullmatch(identifier):
        return None, 1
    match = POST_URL.fullmatch(grok.text(row.get('url')))
    valid_url = bool(match and match.group(1) == identifier)
    reasons, content_reasons = [], []
    for field, label in (('author_handle', '作者'), ('topic', '兴趣方向'), ('what_new', '新内容说明')):
        if not grok.text(row.get(field)).strip():
            reasons.append(label + '缺失')
    if not valid_url:
        reasons.append('原帖链接无效或与帖子 id 不一致')
    full = grok.text(row.get('full_text'))
    for field in ('content_complete', 'full_text', 'incomplete_reason'):
        expected = bool if field == 'content_complete' else str
        if field in row and not (field == 'incomplete_reason' and row[field] is None) and not isinstance(row[field], expected):
            reasons.append(field + '字段类型无效')
    if row.get('content_complete') is False or (isinstance(row.get('full_text', ''), str) and not full.strip()):
        content_reasons.append(grok.text(row.get('incomplete_reason')) or '来源未提供完整正文')
    relevance = grok.number(row.get('relevance'))
    if relevance not in (0, 1, 2, 3):
        relevance = None
        reasons.append('相关度缺失或无效')
    published = grok.stamp(row.get('published_at'))
    if not published:
        reasons.append('发布时间缺失或无效')
    usable, invalid_links = [], 0
    for resource in row.get('usable', []) if isinstance(row.get('usable'), list) else []:
        if not isinstance(resource, dict):
            invalid_links += 1
            continue
        url = safe_url(resource.get('url'))
        invalid_links += int(url is None)
        usable.append({'type': resource.get('type') if resource.get('type') in ('weights', 'api', 'repo', 'tutorial', 'paper') else 'other',
                       'url': url, 'note': grok.text(resource.get('note'))})
    also = row.get('also_posted_by', [])
    return dict(id='x:' + identifier, channel='interest', kind='post', sources=['G'],
                author={'name': '', 'handle': grok.text(row.get('author_handle')), 'followers': grok.number(row.get('author_followers'))},
                published_at=published.isoformat() if published else None, fetched_at=(grok.stamp(row.get('collected_at')) or moment).isoformat(),
                title=(grok.text(row.get('what_new')) or full or '待补充内容')[:120], what_new=grok.text(row.get('what_new')),
                full_text=full, url=row['url'] if valid_url else '', metrics={k: grok.number(row.get(k)) for k in ('views', 'likes', 'bookmarks', 'retweets', 'replies')},
                tier='兴趣', topic=grok.text(row.get('topic')), relevance=relevance, fit_score=None, doable=None, hot=False,
                why=_rich(row.get('why','')), usable=usable, doable_resource='\n'.join((r['note'] + ' ' + (r['url'] or '')).strip() for r in usable),
                also_posted_by=also if isinstance(also, list) else [],
                content_complete=not (reasons or content_reasons), incomplete_reason='；'.join(reasons + content_reasons),
                incomplete_reasons=reasons + content_reasons, content_notice_reasons=content_reasons,
                rich_sources=[_rich({k: row[k] for k in ('article', 'thread', 'quoted', 'media') if k in row})],
                status='new', feedback=None), int(bool(reasons)) + invalid_links


def snapshot(root, now=None):
    now = now or datetime.now(timezone.utc)
    folder = grok.reports_dir() / 'interest'
    merged, orders = {}, {}
    bad_files = bad_entries = duplicates = report_count = 0
    latest = None
    paths = [] if folder.is_symlink() else sorted(folder.glob('*.json'), reverse=True)
    if folder.is_symlink():
        bad_files += 1
    else:
        # 写到一半的临时文件只记异常，绝不尝试将它当成完整报告读取。
        for path in folder.glob('*.tmp'):
            try:
                day = datetime.strptime(path.name[:10], '%Y-%m-%d').date()
                if now.astimezone(grok.BEIJING).date() - timedelta(days=6) <= day <= now.astimezone(grok.BEIJING).date():
                    bad_files += 1
            except ValueError:
                continue
    for path in paths:
        try:
            date = datetime.strptime(path.stem, '%Y-%m-%d-%H%M').replace(tzinfo=grok.BEIJING)
            if not now.astimezone(grok.BEIJING).date() - timedelta(days=6) <= date.date() <= now.astimezone(grok.BEIJING).date():
                continue
            raw, _ = _read(path)
            data = json.loads(raw)
            if data == '本次无':
                rows = []
            elif isinstance(data, list):
                rows = data
            elif isinstance(data, dict):
                rows = next((data[k] for k in ('posts', 'entries', 'items') if isinstance(data.get(k), list)), None)
                if rows is None and data.get('message') == '本次无':
                    rows = []
                if rows is None:
                    raise ValueError('报告缺少条目列表')
            else:
                raise ValueError('报告格式异常')
            report_count += 1
            latest = max(latest, date) if latest else date
            for row in rows:
                if not isinstance(row, dict):
                    bad_entries += 1
                    continue
                post, bad = _post(row, date)
                bad_entries += bad
                if post is None:
                    continue
                identifier = post['id']
                order = (grok.stamp(post['fetched_at']).timestamp(), path.name)
                if identifier in merged:
                    duplicates += 1
                    also = merged[identifier]['also_posted_by'] + post['also_posted_by']
                    if order > orders[identifier]:
                        merged[identifier], orders[identifier] = post, order
                    merged[identifier]['also_posted_by'] = list({json.dumps(v, sort_keys=True, ensure_ascii=False): v for v in also}.values())
                else:
                    merged[identifier], orders[identifier] = post, order
        except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
            bad_files += 1
    from today_view import _load_feedback
    try:
        feedback = _load_feedback(root)
        feedback_error = None
    except ValueError as exc:
        feedback, feedback_error = {}, str(exc)
    from today_view import prepare_content
    for item in merged.values():
        prepare_content(item)
        record = feedback.get('interest:' + item['id'], {})
        item.update(status=record.get('status', 'new'), feedback=record.get('feedback'))
    incomplete_items = [dict(id=item['id'], title=item['title'], url=item['url'], reasons=item['content_notice_reasons'])
                        for item in merged.values() if item['content_notice_reasons']]
    state = dict(updated_at=latest.isoformat() if latest else None, bad_files=bad_files, bad_entries=bad_entries,
                 incomplete_entries=len(incomplete_items), incomplete_items=incomplete_items,
                 duplicates=duplicates, report_count=report_count,
                 error='；'.join(v for v in [f'{bad_files} 个异常文件，{bad_entries} 处条目或链接异常' if bad_files or bad_entries else None, feedback_error] if v) or None)
    return list(merged.values()), {'G': state}


def page_view(root, query, now=None, *, unpaged=False):
    get = lambda k, default='': query.get(k, [default])[0]
    if get('range', 'today') not in ('today', '48h', '7d'):
        raise ValueError('时间范围只能是 today、48h、7d')
    page = int(get('page', '1'))
    if page < 1:
        raise ValueError('页码至少为 1')
    items, states = snapshot(root, now)
    # 兴趣按首次抵达时间查看；重复同步不让旧内容重新进入今天。
    import interest_first_seen as first_seen
    first_seen.apply(root,items,now)
    now = now or datetime.now(timezone.utc)
    period = get('range', 'today')
    cutoff = now.astimezone(grok.BEIJING).replace(hour=0, minute=0, second=0, microsecond=0) if period == 'today' else now - timedelta(hours=48 if period == '48h' else 168)
    filtered = [p for p in items if grok.stamp(p.get('first_seen_at') or p['fetched_at']) >= cutoff
                and (not get('topic') or get('topic') == p['topic'])
                and (not get('author') or get('author').casefold() == p['author']['handle'].casefold())
                and get('q').casefold() in (p['title'] + p['full_text'] + str(p['author']) + p['topic']).casefold()]
    from today_view import SUMMARY, apply_gate
    filtered, filtering = apply_gate(filtered, channel='interest')
    filtered.sort(key=lambda p: (p['content_insufficient'], -grok.stamp(p.get('first_seen_at') or p['fetched_at']).timestamp(), p['id']))
    return dict(channel='interest', items=[{k: p.get(k) for k in (*SUMMARY, 'first_seen_at', 'last_seen_at', 'first_seen_basis', 'channel', 'what_new', 'usable', 'relevance', 'incomplete_reasons')} for p in (filtered if unpaged else filtered[(page-1)*50:page*50])],
                total=len(filtered), page=page, page_size=50, top=[], top_message='', sources=states, filtering=filtering,
                topics=sorted({p['topic'] for p in items if p['topic']}))


def topics_route(handler, payload=None):
    try:
        return handler.reply(200, topics() if payload is None else save_topics(payload))
    except Conflict as exc:
        return handler.reply(409, {'error': str(exc)})
    except (ValueError, UnicodeError) as exc:
        return handler.reply(400, {'error': str(exc)})
    except OSError:
        return handler.reply(500, {'error': '兴趣清单读写失败，请重试'})


# 安装前离线预建；正常请求只读摘要模型，整池匹配在后台执行。
def projection_path(root):
    from interest_projection import path
    return path(root)

def prebuild_projections(root,now=None):
    from interest_projection import publish
    return {**publish(root,now),'entries':3}

def cross_source_page(root,query,now=None):
    if now is not None:
        result=_cross_source_page(root,query,now);result.pop('_valid_until',None);return result
    from interest_projection import page
    return page(root,query)


def _cross_source_page(root, query, now=None):
    """读取全部输入的只读投影；去重、分页和筛选只在今日统一出口做一次。"""
    import copy
    from collections import Counter
    import interest_sources as sources
    import interest_match as matching
    from today_view import prepare_content, _load_feedback
    now = now or datetime.now(timezone.utc)
    get = lambda k, default='': query.get(k, [default])[0]
    if get('range','today') not in ('today','48h','7d'):
        raise ValueError('时间范围只能是 today、48h、7d')
    if int(get('page','1')) < 1:raise ValueError('页码至少为 1')
    cutoff = now.astimezone(grok.BEIJING).replace(hour=0,minute=0,second=0,microsecond=0) if get('range','today')=='today' else now-timedelta(hours=48 if get('range')=='48h' else 168)
    configured=topics()['topics'];directions=matching.compile_topics(configured);profile=matching.profile_key(configured)
    data,cache=sources.candidates(root,now)
    # 两条原有兴趣入口继续即时读，保留已有反馈/异常/逐条改报告的语义。
    from wechat_tools_view import cards
    current,current_states=snapshot(root,now)
    try:tool_cards=cards(root,include_filtered=True)
    except (OSError,ValueError,KeyError,TypeError,__import__('sqlite3').DatabaseError) as exc:
        __import__('logging').getLogger(__name__).warning('interest_tools_read_unavailable kind=%s',type(exc).__name__)
        tool_cards=[];current_states['W']=dict(updated_at=None,error='工具线索暂不可读，其他来源继续显示')
    live=[]
    for original in current+tool_cards:
        original=copy.deepcopy(original)
        original.update(canonical_key=original['id'],identity_state='k1' if original.get('clue_id') else 'pending_ingest',input_sources=['微信群工具' if original.get('kind')=='tool' else 'Grok 兴趣'],input_ids=[original['id']],source_topics=[original.get('topic','')],interest_original=True)
        live.append(original)
    try:sources.attach_k1(root,live)
    except (OSError,ValueError,KeyError,__import__('sqlite3').DatabaseError) as exc:
        __import__('logging').getLogger(__name__).warning('interest_k1_read_unavailable kind=%s',type(exc).__name__)
        current_states['K1 身份']=dict(updated_at=None,error='线索身份暂不可读，按已有来源规范身份显示')
    import interest_first_seen as first_seen
    all_versions=[p for p in data['items'] if not p.get('interest_original')]+live
    first_seen.apply(root,all_versions,now)
    valid_until=min(now+timedelta(minutes=5),(now.astimezone(grok.BEIJING)+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0))
    window=timedelta(hours=48 if get('range')=='48h' else 168)
    versions=[];matched_keys=set();groups={};channel_times={}
    for original in all_versions:
        moment=grok.stamp(first_seen.arrival(original))
        if moment:
            for event in (moment,moment+window):
                if event>now:valid_until=min(valid_until,event)
        # 原 Grok 兴趣入口沿用按抵达时间下界过滤的既有语义；新来源拒绝未来时间。
        if not moment or moment<cutoff or (moment>now and not original.get('interest_original')):continue
        from platform_registry import channel_key,CHANNELS
        for flag in original.get('sources',[]):
            key=channel_key(flag);label={'grok':'G','aisa':'V' if flag=='V' else 'A','wechat':'W','rss':'L'}.get(key,CHANNELS[key]['display_name'])
            observed=grok.stamp(original.get('last_seen_at') or original['fetched_at'])
            if label not in channel_times or observed>channel_times[label][0]:channel_times[label]=(observed,observed.isoformat())
        verdict=original.get('interest_matches',[]) if data.get('match_profile')==profile and not original.get('interest_original') else matching.classify(original,directions)
        value=dict(original,interest_matches=[] if original.get('filtered') else verdict)
        versions.append(value);groups.setdefault(value['canonical_key'],[]).append(value)
        if value['interest_matches']:matched_keys.add(value['canonical_key'])
    # 任一版本命中即保留这条线索的全部渠道，不能先丢掉未独立命中的版本。
    selected=matching.deduplicate(p for p in versions if p['canonical_key'] in matched_keys)
    for value in selected:prepare_content(value)
    counts={};audit=[]
    for key,members in groups.items():
        visible=key in matched_keys
        inputs=list(dict.fromkeys(s for value in members for s in value['input_sources']))
        for source in inputs:
            c=counts.setdefault(source,dict(total=0,visible=0,filtered=0,insufficient=0,ratio=0))
            c['total']+=1;c['visible']+=int(visible);c['filtered']+=int(not visible);c['insufficient']+=int(all(p.get('content_insufficient',False) for p in members))
        if not visible:
            first=members[0];audit.append(dict(id=key,title=first['title'],url=first.get('url',''),sources=inputs,hidden=True,reason='所有已有版本均未命中当前兴趣方向，或来源已标记过滤'))
    # 同步读取当前发现候选，复用其 gate / 官方号分流；不把旧缓存的官方名单当真。
    from today_view import snapshot as discovery_snapshot,apply_gate
    discoveries=set()
    # 只有兴趣报告的隔离 workspace 不读取仓库全局发现 bundle；本地有发现候选才对账。
    potential_hot=any(set(p['input_sources'])&{'Grok 日报','Grok 快报','Grok 周报','AIsa X','抖音视频榜','小红书灵感榜','linux.do'} for p in data['items'])
    try:visible,_=apply_gate(discovery_snapshot(root,now)[0]) if potential_hot else ([],{})
    except (OSError,ValueError,TypeError,KeyError) as exc:
        __import__('logging').getLogger(__name__).warning('interest_discovery_comparison_unavailable kind=%s',type(exc).__name__)
        visible=[]
    discovery_candidates={}
    for p in visible:
        moment=grok.stamp(p.get('published_at'))
        if moment:
            for event in (moment,moment+window,moment+timedelta(hours=168)):
                if event>now:valid_until=min(valid_until,event)
        if not moment or not cutoff<=moment<=now:continue
        try:key=sources.canonical(p,p.get('platform'))[0]
        except ValueError:key=p['id']
        discovery_candidates.setdefault(key,[]).append({k:p.get(k) for k in ('id','author','author_type','author_verification_type','metrics','followers','hot')})
        if not (p.get('official') or {}).get('is_official') or p['official']['hot']:discoveries.add(key)
    try:feedback=_load_feedback(root)
    except ValueError:
        feedback={};current_states['反馈']=dict(updated_at=None,error='反馈文件损坏，已保留原文件；浏览不受影响')
    output=[]
    for value in selected:
        if get('author') and get('author').casefold() not in (value['author'].get('name','').casefold(),value['author'].get('handle','').casefold()):continue
        if get('q').casefold() not in (value['title']+value['full_text']+str(value['author'])+' '.join(m['name'] for m in value['interest_matches'])).casefold():continue
        value['topic']=value['interest_matches'][0]['name']
        value['discovery_overlap']=value['canonical_key'] in discoveries
        value['_discovery_candidates']=discovery_candidates.get(value['canonical_key'],[])
        value['interest_rule_version']=matching.VERSION
        value['interest_rank']=dict(direction=max(m['weight'] for m in value['interest_matches']),trust=max(sources.SOURCE_TRUST.get(s,1) for s in value['input_sources']))
        record=feedback.get('interest:'+value['id'],{})
        # 工具显式分拣状态保持现有只读模型；普通候选只叠本地反馈。
        if record:value.update(status=record.get('status','new'),feedback=record.get('feedback'))
        value.setdefault('official',None)
        output.append(value)
    # 先按首次抵达自然日分组，再方向权重、来源可信度、时刻；新一天不会被旧日高权重淹没。
    output=matching.balanced_sort(output,grok.BEIJING,grok.stamp)
    for c in counts.values():c['ratio']=c['filtered']/c['total'] if c['total'] else 0
    states=copy.deepcopy(current_states)
    for key,(_,updated) in channel_times.items():states[key]={**states.get(key,dict(error=None)), 'updated_at':updated}
    states['跨来源索引']=dict(updated_at=cache['built_at'],error=cache['error'] or ('全来源索引正在首次建立，数量暂不完整' if cache['partial'] else '正在后台重建，暂显示上次完整索引' if cache['refreshing'] else '部分输入暂不可读：'+ '、'.join(data['errors']) if data.get('errors') else None))
    return dict(_valid_until=valid_until,channel='interest',items=output,total=len(output),range_total=len(selected),page=1,page_size=50,top=[],top_message='',sources=states,
                filtering=dict(by_source=counts,items=audit,total=len(audit),policy='按当前兴趣方向匹配；任一版本命中即保留整条线索和全部渠道。来源分别按参与的规范线索计数；官方号保留在兴趣栏。'),
                configured_topics=configured,_official_hints=__import__('official_accounts').interest_hints(),interest_cache=cache,daily_counts=dict(Counter(grok.stamp(first_seen.arrival(p)).astimezone(grok.BEIJING).date().isoformat() for p in output)),
                arrival_policy='按首次抵达时间统计；重复同步不计作新到达。缺少历史证据的存量从修复时点起算。',sort_policy='首次抵达日倒序 · 各方向按设置顺序加权轮流展示 · 来源可信度与时间优先；内容不足后排')


def save_feedback(root, payload):
    if set(payload) != {'id', 'action', 'channel'} or payload['action'] not in ('accurate', 'inaccurate', 'keep', 'clear'):
        raise ValueError('兴趣反馈只允许存、准、不准和撤销反馈')
    from today_view import item_view, _feedback_lock, _load_feedback, feedback_path
    item = item_view(root, {'id': [payload['id']], 'channel': ['interest']})
    identifier, action = item['id'], payload['action']
    with _feedback_lock(root):
        records = _load_feedback(root)
        key = 'interest:' + identifier
        record = dict(records.get(key, {'status': 'new', 'feedback': None}))
        previous = dict(record)
        if action in ('accurate', 'inaccurate'):
            record['feedback'] = action
        elif action == 'keep':
            record['status'] = 'kept'
        else:
            record['feedback'] = None
        record.update(channel='interest', updated_at=datetime.now(timezone.utc).isoformat())
        # 复用 Grok 写入器的锁、损坏检测和原子替换，保留爆款通道已有反馈。
        if any(source.startswith('Grok ') for source in item.get('input_sources',['Grok 兴趣'])):
            with grok._feedback_lock(root):
                entries = grok._load_feedback(root)
                tweet_id = identifier.removeprefix('x:')
                if record['feedback'] is None and record['status'] == 'new':
                    if entries.get(tweet_id, {}).get('channel') == 'interest':
                        entries.pop(tweet_id, None)
                else:
                    entries[tweet_id] = dict(tweet_id=tweet_id, channel='interest',
                        verdict='not_worth' if record['feedback'] == 'inaccurate' else 'worth',
                        updated_at=record['updated_at'], post=item)
                raw = (json.dumps(entries, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
                grok._atomic_write(grok.reports_dir() / 'feedback/studio-feedback.json', raw)
                grok._atomic_write(grok.feedback_path(root), raw)
        if action == 'keep':
            from items_store import ItemsStore
            store = ItemsStore(root)
            stored=store.upsert({**item, **record, 'why_viral': item['why'], 'analysis': {'channel':'interest','relevance':item['relevance'],'usable':item['usable'],'canonical_key':item.get('canonical_key',identifier),'clue_id':item.get('clue_id'),'input_sources':item.get('input_sources',[]),'interest_matches':item.get('interest_matches',[])},'legacy_refs':item.get('legacy_refs',[])+([{'store':'clue','id':item['clue_id']}] if item.get('clue_id') else [])})
            record['material_id']=stored['id']
        records[key] = record
        grok._atomic_write(feedback_path(root), (json.dumps(records, ensure_ascii=False, indent=2) + '\n').encode())
        from today_view import accumulate_inaccurate
        accumulate_inaccurate(root, item, previous, action, channel='interest')
    return dict(id=identifier, **record)
