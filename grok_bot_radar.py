"""Grok Bot 本地报告只读聚合，以及工作台反馈双份原子写入。"""
from __future__ import annotations

import fcntl
import hashlib
import uuid
import json
import math
import os
import re
import stat
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
MAX_BYTES = 5 * 1024 * 1024
ID_RE = re.compile(r'[0-9]{1,25}')
URL_RE = re.compile(r'https://(?:x\.com|twitter\.com)/[A-Za-z0-9_]{1,15}/status/([0-9]{1,25})')
KINDS = {'daily': '日报', 'alerts': '快报', 'weekly': '周报'}
VERDICTS = ('worth', 'not_worth', 'used')
POST_KEYS = ('posts', 'entries', 'items')
_LOCK = threading.RLock()
_EPOCH = uuid.uuid4().hex
_REVISIONS = {}


def reports_dir():
    return Path(os.environ.get('GROK_BOT_REPORTS_DIR', '~/grok-bot-reports')).expanduser()


def feedback_path(root):
    from radar_page_api import _monitor_dir
    return _monitor_dir(root).parent / 'grok-bot' / 'feedback.json'


def stamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.endswith(' Asia/Shanghai'):
        value = value.removesuffix(' Asia/Shanghai') + '+08:00'
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.replace(tzinfo=BEIJING) if result.tzinfo is None else result
    except ValueError:
        return None


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and 0 <= result <= 2**53 - 1 else None


def text(value):
    return value if isinstance(value, str) else ''


def safe_url(value):
    return value if isinstance(value, str) and URL_RE.fullmatch(value) else None


def _read_json(path):
    # O_NOFOLLOW also closes the symlink-swap race between inspection and open.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError('文件类型或大小不支持')
        raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('文件过大')
        return json.loads(raw), info.st_mtime


def load_reports(directory=None):
    base = Path(directory) if directory is not None else reports_dir()
    merged, latest, latest_alert = {}, None, None
    bad_files = skipped_entries = ignored_files = skipped_aux_files = report_count = 0
    for kind, label in KINDS.items():
        folder = base / kind
        if folder.is_symlink():
            bad_files += 1
            continue
        try:
            files = sorted(folder.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for path in files:
            # Intermediate knowledge/fetch results are not post reports.
            # Keep malformed files outside these exact prefixes as bad_files.
            if path.suffix == '.json' and path.name.startswith(
                ('knowledge_payloads', 'knowledge_post_results', '_post_results')
            ):
                ignored_files += 1
                skipped_aux_files += 1
                continue
            if path.suffix != '.json':
                ignored_files += 1
                continue
            try:
                data, mtime = _read_json(path)
                if not isinstance(data, dict):
                    raise ValueError('报告必须是对象')
                # Legacy weekly files with post keys remain post reports.
                # Only parsed summary objects without any post key are auxiliary.
                if kind == 'weekly' and not any(key in data for key in POST_KEYS):
                    ignored_files += 1
                    skipped_aux_files += 1
                    continue
                if any(key in data and not isinstance(data[key], list) for key in POST_KEYS):
                    raise ValueError('posts/entries/items 必须是数组')
                if not any(key in data for key in POST_KEYS):
                    raise ValueError('缺少 posts/entries/items')
                rows = [row for key in POST_KEYS for row in data.get(key, [])]
                moment = stamp(data.get('collected_at')) or stamp(data.get('generated_at')) or datetime.fromtimestamp(mtime, timezone.utc)
                source = {'file': kind + '/' + path.name, 'kind': kind, 'type': label, 'collected_at': moment.isoformat()}
                order = (moment.timestamp(), mtime, source['file'])
                report_count += 1
                if latest is None or order > latest[0]:
                    latest = (order, source)
                if kind == 'alerts' and (latest_alert is None or moment > latest_alert):
                    latest_alert = moment
                for row in rows:
                    if not isinstance(row, dict):
                        skipped_entries += 1
                        continue
                    url = safe_url(row.get('url'))
                    tweet_id = row.get('id', row.get('tweet_id'))
                    if isinstance(tweet_id, int) and not isinstance(tweet_id, bool):
                        tweet_id = str(tweet_id)
                    if not isinstance(tweet_id, str) or not ID_RE.fullmatch(tweet_id):
                        tweet_id = URL_RE.fullmatch(url).group(1) if url else None
                    if tweet_id is None:
                        skipped_entries += 1
                        continue
                    record = {key: text(row.get(key)) for key in ('author', 'handle', 'text', 'title_zh', 'list', 'topic', 'lang', 'why', 'pattern')}
                    record.update({key: row.get(key) for key in ('full_text', 'article', 'thread', 'quoted', 'media', 'card', 'content_complete', 'is_ai', 'content_filtered', 'excluded') if key in row})
                    record.update({key: number(row.get(key)) for key in ('followers', 'views', 'likes', 'bookmarks', 'retweets', 'replies', 'views_per_hour', 'hours_since_created')})
                    record.update(id=tweet_id, url=url, label=text(row.get('label')) or text(row.get('type')),
                                  created_at=stamp(row.get('created_at')).isoformat() if stamp(row.get('created_at')) else None)
                    previous = merged.get(tweet_id)
                    if previous is None:
                        merged[tweet_id] = {'record': record, 'order': order, 'first': moment, 'last': moment, 'sources': [source]}
                    else:
                        previous['first'] = min(previous['first'], moment)
                        previous['last'] = max(previous['last'], moment)
                        if source not in previous['sources']:
                            previous['sources'].append(source)
                        if order > previous['order']:
                            previous.update(record=record, order=order)
            except (OSError, ValueError, UnicodeError, RecursionError):
                bad_files += 1
    posts = []
    for value in merged.values():
        posts.append(dict(value['record'], first_discovered_at=value['first'].isoformat(),
                          last_updated_at=value['last'].isoformat(), sources=sorted(value['sources'], key=lambda x: (x['collected_at'], x['file']))))
    return {'posts': posts, 'latest_report': latest[1] if latest else None,
            'latest_alert_at': latest_alert.isoformat() if latest_alert else None,
            'bad_files': bad_files, 'skipped_entries': skipped_entries, 'ignored_files': ignored_files, 'skipped_aux_files': skipped_aux_files, 'report_count': report_count}


def load_today_todo(day, directory=None, *, with_notes=False):
    """只读指定北京日期（YYYY-MM-DD）的日报，保留 today_todo 顺序和原字段。

    默认返回 None（文件未出）或列表（[] 表示今天没有）；with_notes=True
    返回 {"items": 同一结果, "notes": 日报原 notes 或 None}，供页面解释空态。
    报告损坏或缺少/错误的 today_todo 抛出 ValueError；读取失败原样抛出，
    不把故障误报为“日报未出”或“今天没有”。不回退读取其它日期。
    """
    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise ValueError('day 必须是北京日期 YYYY-MM-DD')
    date.fromisoformat(day)
    base = Path(directory) if directory is not None else reports_dir()
    folder = base / 'daily'
    if folder.is_symlink():
        raise ValueError('日报目录不允许符号链接')
    try:
        data, _ = _read_json(folder / (day + '.json'))
    except FileNotFoundError:
        items, notes = None, None
    else:
        if not isinstance(data, dict) or not isinstance(data.get('today_todo'), list):
            raise ValueError('today_todo 必须是数组')
        items, notes = data['today_todo'], data.get('notes')
        if any(not isinstance(item, dict) for item in items):
            raise ValueError('today_todo 每条必须是对象')
    return {'items': items, 'notes': notes} if with_notes else items


class FeedbackCorrupt(ValueError):
    pass


def _load_feedback(root):
    path = feedback_path(root)
    try:
        records, _ = _read_json(path)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise FeedbackCorrupt('Grok Bot 反馈文件损坏，已停止写入') from exc
    if not isinstance(records, dict) or any(
        not ID_RE.fullmatch(key) or not isinstance(record, dict) or record.get('verdict') not in VERDICTS
        for key, record in records.items()
    ):
        raise FeedbackCorrupt('Grok Bot 反馈文件损坏，已停止写入')
    return records


def _revision(root, records):
    signature = hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = str(feedback_path(root).resolve())
    previous, revision = _REVISIONS.get(key, (None, 0))
    if previous != signature:
        revision += 1
        _REVISIONS[key] = (signature, revision)
    return _EPOCH + ':' + str(revision)


def load_feedback(root):
    with _LOCK:
        try:
            records = _load_feedback(root)
            return {'feedback': records, 'feedback_status': 'ok', 'feedback_revision': _revision(root, records)}
        except FeedbackCorrupt:
            return {'feedback': {}, 'feedback_status': 'invalid'}


def radar_view(root, hours=48, now=None):
    if str(hours) not in ('24', '48', '168'):
        raise ValueError('时间范围只能是 24、48 或 168 小时')
    now = now or datetime.now(timezone.utc)
    result = load_reports()
    cutoff = now - timedelta(hours=int(hours))
    result['posts'] = [p for p in result['posts'] if stamp(p['created_at']) is not None and cutoff <= stamp(p['created_at']) <= now]
    result['posts'].sort(key=lambda p: (p['first_discovered_at'], p['id']), reverse=True)
    latest = stamp(result['latest_alert_at']) or (stamp(result['latest_report']['collected_at']) if result['latest_report'] else None)
    result.update(count=len(result['posts']), hours=int(hours), stale_hours=max(0, (now - latest).total_seconds() / 3600) if latest else None)
    result['merged_posts'] = merge_posts(result['posts'], _bundle())
    result.update(load_feedback(root))
    return result


def _atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise OSError('反馈路径不允许符号链接')
    fd, name = tempfile.mkstemp(prefix='.studio-feedback-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _feedback_lock(root):
    path = feedback_path(root).parent / '.feedback.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def save_feedback(root, payload):
    if not isinstance(payload, dict) or set(payload) != {'tweet_id', 'verdict'}:
        raise ValueError('只允许 tweet_id 与 verdict 两个字段')
    tweet_id, verdict = payload['tweet_id'], payload['verdict']
    if not isinstance(tweet_id, str) or not ID_RE.fullmatch(tweet_id):
        raise ValueError('tweet_id 必须是 1–25 位纯数字字符串')
    if verdict is not None and (not isinstance(verdict, str) or verdict not in VERDICTS):
        raise ValueError('verdict 只能是 worth、not_worth、used 或 null')
    snapshot = next((post for post in load_reports()['posts'] if post['id'] == tweet_id), None)
    if snapshot is None:
        raise ValueError('该推文不在 Grok Bot 报告中')
    with _feedback_lock(root):
        records = _load_feedback(root)
        if verdict is None:
            records.pop(tweet_id, None)
        else:
            records[tweet_id] = {'tweet_id': tweet_id, 'verdict': verdict, 'updated_at': datetime.now(timezone.utc).isoformat(), 'post': snapshot}
        raw = (json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        # 每份文件通过同目录临时文件 + rename 发布，锁覆盖两份写入，避免并发丢失。
        _atomic_write(reports_dir() / 'feedback' / 'studio-feedback.json', raw)
        _atomic_write(feedback_path(root), raw)
        return dict(records.get(tweet_id, {'tweet_id': tweet_id, 'verdict': None}), feedback_revision=_revision(root, records))


def normalize_post(row, source):
    """与布局无关的统一帖子：作者/正文/媒体/指标/来源/所属视图。"""
    result = dict(row)
    result.update(id=text(row.get('id')) or text(row.get('tweet_id')),
                  author=text(row.get('author')) or text(row.get('name')),
                  handle=text(row.get('handle')) or text(row.get('user')),
                  followers=number(row.get('followers', row.get('fc'))),
                  created_at=text(row.get('created_at')) or text(row.get('published_at')),
                  collected_at=text(row.get('last_updated_at')) or text(row.get('fetched_at')),
                  sources=[source], text=text(row.get('text')),
                  full_text=text(row.get('full_text')), url=safe_url(row.get('url')))
    for field in ('views', 'likes', 'bookmarks', 'retweets', 'replies', 'views_per_hour', 'hours_since_created'):
        result[field] = number(row.get(field))
    return result


def merge_posts(posts, bundle):
    """统一合并层，不依赖 HTML、标签节点或浏览器状态。"""
    from scripts.radar_common import content_filter, classify_track
    merged = {}
    for group, key in (('vip', 'vip_tweets'), ('feed', 'tweets')):
        for row in bundle.get(key, []) if isinstance(bundle.get(key), list) else []:
            if not isinstance(row, dict):
                continue
            post = normalize_post(row, 'AIsa')
            if not ID_RE.fullmatch(post['id']):
                continue
            previous = merged.get(post['id'])
            groups = set(previous.get('view_groups', [])) if previous else set()
            groups.add(group)
            if previous and (stamp(previous['collected_at']) or datetime.min.replace(tzinfo=timezone.utc)) > (stamp(post['collected_at']) or datetime.min.replace(tzinfo=timezone.utc)):
                post = previous
            post.update(view_groups=sorted(groups), aisa=row)
            merged[post['id']] = post
    for row in posts:
        post = normalize_post(row, 'Grok')
        content = post['full_text'] or post['text']
        eligible, reason = content_filter(content, is_target_vip=True)
        post.update(content_eligible=eligible and row.get('is_ai') is not False and not row.get('excluded') and not row.get('content_filtered'), filter_reason=reason)
        words = '\n'.join([text(row.get('topic')), text(row.get('list')), content])
        # classify_track 与既有五赛道共用；无对应关键词的帖子仅进入「全部」。
        mapped = classify_track(words)
        keywords = ['虚拟卡','stripe','订阅','支付','封号','绑卡','美区','出海基建','收款','税','llc','mercury','x pass','github','开源','repo','star','仓库','arxiv','scion','代码管理','claude','cursor','codex','agent','mcp','windsurf','trae','vibe','写代码','编程','opus','sonnet','skill','cli','变现','赚钱','商业','营收','arr','mrr','独立开发','出海','获客','增长','矩阵','起号','副业','转化','付费','月入','美金','流量','模型','gpt','gemini','deepseek','qwen','千问','豆包','kling','可灵','seedance','sora','生图','视频生成','评测','推理','蒸馏','训练','算力','显卡','本地部署','coding','工具工作流','aigc','图像']
        if not any(word in words.lower() for word in keywords):
            mapped = None
        post['track'] = mapped
        post['track_label'] = row.get('topic') or row.get('list') or ''
        prior = merged.get(post['id'])
        if prior:
            newest = (stamp(post['collected_at']) or datetime.min.replace(tzinfo=timezone.utc)) >= (stamp(prior['collected_at']) or datetime.min.replace(tzinfo=timezone.utc))
            combined = dict(prior, **post)
            if not newest:
                for field in ('views', 'likes', 'bookmarks', 'retweets', 'replies', 'views_per_hour', 'hours_since_created', 'collected_at', 'followers'):
                    combined[field] = prior.get(field)
            combined.update(sources=['AIsa', 'Grok'], aisa=prior.get('aisa'))
            prior_text = prior['full_text'] or prior['text']
            grok_text = post['full_text'] or post['text']
            combined['text'] = max((prior_text, grok_text), key=len)
            combined['full_text'] = combined['text']
            # 保留两边的结构化补充内容，前端详情渲染器按来源使用。
            post = combined
        post.update(grok=row, content_eligible=eligible and row.get('is_ai') is not False and not row.get('excluded') and not row.get('content_filtered'))
        lowfan = row.get('label') == '低粉爆款' or (row.get('followers') is not None and row['followers'] <= 10000 and row.get('label') == '爆款')
        post['view_groups'] = ['feed' if lowfan else 'vip']
        merged[post['id']] = post
    return list(merged.values())


BUNDLE_PATH = Path(__file__).parent / 'public' / 'twitter-radar-bundle.json'


def _bundle():
    try:
        bundle, _ = _read_json(BUNDLE_PATH)
        return bundle if isinstance(bundle, dict) else {}
    except (OSError, ValueError, UnicodeError, RecursionError):
        return {}
