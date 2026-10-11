"""X榜单（xbangdan.com）选题雷达：只读抓取，把三张选题榜送进线索中心。

设计约束（与 collect_core 一致）：
- **一次请求拿三张榜**。话题榜、低粉爆款、海外信息差都在 `/words/` 一个页面上，
  不为每张榜各发一次请求。
- **不碰 collect_core.PLATFORMS**。xbangdan 是聚合站不是平台，塞进平台清单会污染语义。
- **不调用模型**。抓到什么写什么，筛选和判断交给黄总。
- **礼貌抓取**：单次请求间隔 ≥60 秒、单日 ≤20 次、命中 429/403 当天停。
  遵守 robots.txt，被 disallow 就如实报错，不绕过。

方法论见 coordination/xbangdan-选题方法论.md。

## 结构健康与快照（22f）
站方随时可能改版。`_collect` 在解析后立刻算一份 `health` 落进 `runs`：
命中任何一条实测基线外的判据，就把 `runs.error` 写满、`status()` 降级为不可用并给出
`warning`，同时用线索中心写一条 `important` 告警。**只降级报警，不写坏数据进线索**：
解析结果照常落 `items`、页面原文照常存快照，但 `publish()` 会拒绝写入。
人因此能回看当时的页面，而不是只看到一句「今天没数据」。

判据分两层：**行级**（摘要、状态标签、发帖柱、data-s，看的是 `hwe-row` 内部的字段）
与**榜级**（三张榜各自解析出多少条、侧栏容器还在不在）。两层都要：站方改个类名就会出现
「容器还在、某一榜整个空掉」，只盯行级信号会完全看不见这种形态（复核 M1）。

## robots.txt 策略（显式，M1/M2/M3）
- **UA 组只认 `*`**（M3）：站方 robots 里另有 `User-agent: ShapBot` 的全站禁止，与我们无关；
  我们只用真实 Chrome UA 请求，按通行口径取 `*` 组。这是有意的取舍，不是遗漏。
- **结果缓存 24 小时**（内存 + 落表 `robots`）：robots 原文一天最多读一次，
  不再每次抓取都白烧一个请求。
- **robots 请求计入同一张配额表**（M1）：与页面请求同一个 `quota` 行、同样受单日 20 次上限约束。
  最小间隔按「一次抓取算一次」：`_guard()` 在一次 `_fetch` 开头只查一次，
  robots 与页面各消耗一个单日额度，但不会因为刚读完 robots 就把同一次抓取的页面请求也拦掉。
- **读取失败：先用缓存；没有缓存才 fail-open**（M2，显式策略）。
  fail-open 的那次一定会写进 `runs.error` 并在 `status()` 里以 `warning` 暴露，不静默。

## 冷却可以跨天（M11）
命中 429/403 后冷却 6 小时（`COOLDOWN_SECONDS`）。刻意**不**改成「到当日 24:00」：
429/403 是站方在告诉我们「现在别来」，冷却到站方恢复比日历日边界更贴合实际。
代价是 22:00 命中会挡到次日 04:00 —— 但站方 08:30 前后才刷新榜，
这段时间本来也抓不到新东西，可以接受。

## transport 分支为什么跳过 robots（M9）
注入 transport 的调用方（测试）根本不会向站方发请求，
对它查 robots 只是自我阻塞、没有保护意义。所以 transport 分支直接跳过，
代价是 robots 逻辑在 transport 路径上零覆盖 —— 用 `robots_allows` 的直接单测补上。
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, urlopen

BASE = 'https://xbangdan.com'
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# 单次请求间隔与单日上限。抓取是补充信息源，宁可慢也不要给对方添麻烦。
MIN_INTERVAL = 60.0
DAILY_LIMIT = 20
COOLDOWN_SECONDS = 6 * 3600

# robots 原文缓存一天（内存 + 落表）。站方 robots 变动极慢，重读没有意义。
ROBOTS_TTL = 24 * 3600
# 页面快照保留天数。每天一页，gzip 后几十 KB，90 天完全放得下。
SNAPSHOT_KEEP_DAYS = 90

# 结构健康判据（基线来自 coordination/research/xradar/snapshots/ 下 14 份真实页面，
# 见 research-xradar-history-report.md §2.3）。任一命中即判异常。
ROW_MIN, ROW_MAX = 12, 32          # 实测 16–25
SUMMARY_RATIO_MIN = 0.9            # hwe-s 出现数 / 行数，实测 12 天 = 1.0
STATE_RATIO_MIN = 0.5              # 带 up/peak/down 状态标签的行占比，实测最低 0.52
BARS_PER_ROW = 24                  # 每条话题的 24 根发帖柱，实测恒为 24
KNOWN_STATES = {'', 'up', 'peak', 'down'}   # data-s 的取值集合，实测 13 天稳定

BOARDS = (
    {'id': 'topics', 'label': '话题榜', 'hint': '今天什么在火，带生命周期状态'},
    {'id': 'lowfan', 'label': '低粉爆款', 'hint': '粉丝不足 1 万但单条曝光过 2 万'},
    {'id': 'global', 'label': '海外信息差', 'hint': '海外在火，中文区还没人聊'},
)

# 站方赛道 → 我们的线索分类。站方的 AI 科技在中文语境里多指工具/技术动态。
CATEGORY = {'ai': ['topics'], 'money': ['topics'], 'society': ['topics'],
            'ent': ['topics'], 'xcircle': ['topics']}

HEAT = {'up': 'rising', 'peak': 'peak', 'down': 'fading'}
HEAT_LABEL = {'rising': '上升中', 'peak': '见顶', 'fading': '退烧'}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def plain(value, limit=200):
    """去标签、去实体、统一空白。解析失败返回空串，不抛异常。"""
    if not value:
        return ''
    try:
        text = html.unescape(re.sub(r'<[^>]+>', '', value))
    except Exception:  # noqa: BLE001 - 任何解析意外都退化成空串
        return ''
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text


def views(value):
    """把 '636.5万' / '9824' / '海外 492.3万 曝光' 统一成整数。

    解析不了就返回 None，绝不编造数字 —— 曝光数是要写进线索当依据的，宁缺勿错。
    """
    if not value:
        return None
    raw = plain(value, 40).replace(',', '').replace('曝光', '').strip()
    # 站方会在数字前后挂「海外」「单条」这类前缀词，只取第一个数字。
    match = re.search(r'([\d]+(?:\.\d+)?)\s*(亿|万|w|k)?', raw, re.I)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    factor = {'': 1, '万': 10_000, 'w': 10_000, '亿': 100_000_000, 'k': 1_000}.get(
        (match.group(2) or '').lower())
    if factor is None:
        return None
    return int(number * factor)


def format_views(number):
    """整数还原成站方那种写法，用于人读。只在有值时用。"""
    if number is None:
        return ''
    if number >= 100_000_000:
        return f'{number / 100_000_000:.1f}亿'
    if number >= 10_000:
        return f'{number / 10_000:.1f}万'
    return str(number)


def fid(prefix, *parts):
    material = '␟'.join(str(p) for p in parts)
    return f'{prefix}-' + hashlib.sha256(material.encode()).hexdigest()[:16]


# ── 解析 ────────────────────────────────────────────────────────────────────
ROW = re.compile(r'<details class="hwe-row[^"]*"([^>]*)>(.*?)</details>', re.S)
LOW = re.compile(
    r'<a href="(https://x\.com/i/status/\d+)"[^>]*>.*?<b>@([\w_]+)</b>\s*<small>'
    r'(\d+)\s*粉\s*→\s*<strong>([^<]+)</strong>\s*曝光\s*·\s*([^<]+)</small>', re.S)
OVERSEAS = re.compile(r'<a href="(/global/\?ev=\d+)">([^<]+)</a>\s*<small>([^<]+)</small>', re.S)

# 字段类名实测 13 天不变（L2）。集中在正则常量里，同一模式只求值一次。
TOPIC_TITLE = re.compile(r'class="hwe-t">(.*?)</span>', re.S)
TOPIC_SUMMARY = re.compile(r'class="hwe-s">(.*?)</span>', re.S)
TOPIC_META = re.compile(r'class="hwe-m">(.*?)</span>', re.S)
TOPIC_VIEWS = re.compile(r'class="hwe-r"><b>(.*?)</b>', re.S)
STATE_ATTR = re.compile(r'data-s="([^"]*)"')
CATEGORY_ATTR = re.compile(r'data-c="([^"]*)"')
BAR = re.compile(r'<rect\b')
LOWFAN_BLOCK = re.compile(r'<ul class="hwe-ll">(.*?)</ul>', re.S)
OVERSEAS_BLOCK = re.compile(r'<ul class="hwe-gl">(.*?)</ul>', re.S)
# 页头口径行 `hw-sub`：当天页写「最近 24 小时…」，历史页写「北京时间 X 月 X 日全天…」。
# 两类页面含义不同，必须分开存（research-xradar-history-report.md §1.3 / §2.3）。
PAGE_SCOPE = re.compile(r'class="hw-sub">(.*?)</p>', re.S)
SCOPE_ROLLING, SCOPE_FULLDAY, SCOPE_UNKNOWN = 'rolling_24h', 'full_day', 'unknown'


def parse_exposure(raw):
    """把一格的曝光文案拆成 (整数或 None, 原文)。

    解析不出来时整数为 None、**原文照抄**（M5）。站方改文案后这条依据仍可复核，
    否则「不知道是多少」和「没有曝光」就再也分不开了。
    """
    return views(raw), (plain(raw, 40) if raw else '')


def parse_topics(markup):
    """话题榜。每条带生命周期状态 up/peak/down —— 站方直接给在 data-s 上。"""
    rows = []
    for attrs, body in ROW.findall(markup):
        cat = CATEGORY_ATTR.search(attrs)
        state = STATE_ATTR.search(attrs)
        hit = TOPIC_TITLE.search(body)
        title = plain(hit.group(1), 200) if hit else ''
        if not title:
            continue
        hit = TOPIC_META.search(body)
        meta = plain(hit.group(1), 200) if hit else ''
        hit = TOPIC_SUMMARY.search(body)
        summary = plain(hit.group(1), 400) if hit else ''
        hit = TOPIC_VIEWS.search(body)
        reach, raw_views = parse_exposure(hit.group(1) if hit else '')
        days = re.search(r'连续 (\d+) 天上榜', meta)
        posts = re.search(r'· (\d+) 帖', meta)
        talkers = re.search(r'· (\d+) 人在说', meta)
        from clues_heat import svg_features
        bars=[float(v) for v in re.findall(r'<rect\b[^>]*height="([\d.]+)"',body)]
        first=re.search(r'<div class="hwe-trail"[^>]*>(.*?)</div>',body,re.S)
        first_body=first.group(1) if first else ''
        first_url=re.search(r'href="(https://x\.com/(?:i|[A-Za-z0-9_]+)/status/\d+)"',first_body)
        hottest=re.search(r'<a[^>]*class="hwe-sam"[^>]*href="([^"]+)"',body)
        rows.append({
            **svg_features(bars),
            'angles':[plain(v,400) for v in re.findall(r'<li>(.*?)</li>',body,re.S)],
            'first_post_url':first_url.group(1) if first_url else '',
            'first_post_note':plain(first_body,200),
            'hottest_post_url':hottest.group(1) if hottest else '',
            'board': 'topics',
            'key': fid('t', cat.group(1) if cat else '', title),
            'category': cat.group(1) if cat else '',
            'heat': HEAT.get(state.group(1) if state else '', ''),
            'title': title,
            'summary': summary,
            'meta': meta,
            'views': reach,
            'raw_views': raw_views,
            'days': int(days.group(1)) if days else 0,
            'posts': int(posts.group(1)) if posts else 0,
            'talkers': int(talkers.group(1)) if talkers else 0,
            'url': BASE + '/words/',
        })
    return rows


def parse_lowfan(markup):
    """低粉爆款。格式固定：原文链接 + @handle + 粉丝数 + 曝光 + 所属话题。"""
    block = LOWFAN_BLOCK.search(markup)
    rows = []
    for url, handle, fans, reach_raw, topic in LOW.findall(block.group(1) if block else ''):
        count = int(fans) if fans.isdigit() else None
        reach, raw_views = parse_exposure(reach_raw)
        rows.append({
            'board': 'lowfan', 'key': fid('l', handle, url), 'handle': handle,
            'fans': count, 'views': reach, 'raw_views': raw_views,
            'topic': plain(topic, 200), 'url': url,
            # 放大倍数是这个榜存在的全部意义，算不出来就留空。
            'multiple': round(reach / count, 1) if count and reach and count < reach else None,
        })
    return rows


def parse_overseas(markup):
    """海外信息差。中文区尚无人聊的选题，带站方事件页链接。"""
    block = OVERSEAS_BLOCK.search(markup)
    rows = []
    for href, title, reach_raw in OVERSEAS.findall(block.group(1) if block else ''):
        title = plain(title, 200)
        if not title:
            continue
        reach, raw_views = parse_exposure(reach_raw)
        rows.append({'board': 'global', 'key': fid('g', href, title), 'title': title,
                     'views': reach, 'raw_views': raw_views, 'url': BASE + href})
    return rows


def parse(markup):
    """一次请求，三张榜。任一张解析不到就少一张，不影响另外两张。"""
    return {'topics': parse_topics(markup), 'lowfan': parse_lowfan(markup),
            'global': parse_overseas(markup)}


def page_scope(markup):
    """这一页说的是「最近 24 小时」还是「某一天全天」。两类页面不能混着比。"""
    hit = PAGE_SCOPE.search(markup)
    text = plain(hit.group(1), 120) if hit else ''
    if '最近 24 小时' in text:
        return SCOPE_ROLLING
    if '全天' in text:
        return SCOPE_FULLDAY
    return SCOPE_UNKNOWN


def health(markup, boards):
    """解析后立刻体检。返回一份可落库、可读的字典。

    判据全部来自 14 天实测基线（coordination/research/xradar/structure.json 与
    snapshots/ 下 14 份真实页面），见 research-xradar-history-report.md §2.3。
    任一异常即 `ok=False`，调用方据此降级报警，**但仍把解析结果照常落库**——
    快照在，人可以回看当时页面到底是什么样子。

    判据分两层（复核 M1）：
    - **行级**：看 `hwe-row` 里的字段（摘要、状态、发帖柱、data-s）；
    - **榜级**：看三张榜**各自**解析出了多少条、侧栏容器还在不在。
      行级信号再全，也盖不住「容器还在、某一榜整个空掉」——站方改个类名就会这样，
      而这恰恰是原复核 H2 列的 #2/#3/#5/#7。
    """
    rows = ROW.findall(markup)
    row_count = len(rows)
    counts = {board: len(items) for board, items in boards.items()}
    states = set()
    labelled = 0
    for attrs, _ in rows:
        found = STATE_ATTR.search(attrs)
        value = found.group(1) if found else ''
        states.add(value)
        labelled += 1 if value in ('up', 'peak', 'down') else 0
    summaries = sum(1 for _, body in rows if TOPIC_SUMMARY.search(body))
    bars = sorted({len(BAR.findall(body)) for _, body in rows})
    total = sum(counts.values())
    report = {
        'rows': row_count,
        'counts': counts,
        'lowfan_block': bool(LOWFAN_BLOCK.search(markup)),
        'overseas_block': bool(OVERSEAS_BLOCK.search(markup)),
        'summary_ratio': round(summaries / row_count, 3) if row_count else 0.0,
        'state_ratio': round(labelled / row_count, 3) if row_count else 0.0,
        'bars_per_row': bars,
        'states': sorted(states),
        'unknown_states': sorted(states - KNOWN_STATES),
        # 「行容器命中了、但解析不出条目」的真实缺失率。
        # 早先这里统计的是「已解析出的条目里有没有空标题」，那个值恒为 0：
        # `parse_topics` 对没标题的行直接 `continue`，能进 boards 的行必然有标题，
        # 于是这个「字段缺失率」是个结构性死指标，只会给人假安全感（复核 M1）。
        'missing_ratio': (round((row_count - counts.get('topics', 0)) / row_count, 3)
                          if row_count else 0.0),
        'scope': page_scope(markup),
    }
    problems = []
    if not row_count and not total:
        problems.append('页面里三张榜的容器都没命中，解析结果全空')
    elif row_count < ROW_MIN or row_count > ROW_MAX:
        problems.append('话题行数 %d 落在实测区间 %d-%d 之外' % (row_count, ROW_MIN, ROW_MAX))
    if row_count and report['summary_ratio'] < SUMMARY_RATIO_MIN:
        problems.append('摘要字段覆盖率 %s 低于 %s'
                        % (report['summary_ratio'], SUMMARY_RATIO_MIN))
    if row_count and report['state_ratio'] < STATE_RATIO_MIN:
        problems.append('状态标签占比 %s 低于 %s' % (report['state_ratio'], STATE_RATIO_MIN))
    if bars and bars != [BARS_PER_ROW]:
        problems.append('每行发帖柱数 %s 不是恒定的 %d' % (bars, BARS_PER_ROW))
    if report['unknown_states']:
        problems.append('data-s 出现新取值 %s' % (report['unknown_states'],))
    problems.extend(_board_problems(report))
    report['problems'] = problems
    report['ok'] = not problems
    return report


def _board_problems(report):
    """榜级判据：容器还在、某张榜却整个空掉（复核 M1）。

    阈值全部来自 14 天实测，不是拍脑袋：
    - `hwe-row` 容器**就是**话题榜的容器：命中了 N 行却一条都解析不出来，
      说明标题 span 之类换类名了，解析器跟不上（实测 14 天 rows 恒等于 topics）。
    - 低粉爆款侧栏 `hwe-ll`：**14/14 天都在**（历史页与当天页都有），
      缺了、或者在但解析出 0 条，都是改版。
    - 海外信息差侧栏 `hwe-gl`：**只有当天页（`scope=rolling_24h`）有**，
      12/12 天的历史页都没有。所以「该有却没有」只在当天页判，
      否则每个历史页都会误报；历史页 `global=0` 是常态，不算异常。
    - 三张榜全空：不分页面类型，任何一页出现都说明解析器跟不上了。
    """
    counts = report['counts']
    problems = []
    if not any(counts.values()):
        problems.append('三张榜解析结果全空（行容器仍在，说明是解析器跟不上而不是页面空）')
    if report['rows'] and not counts.get('topics'):
        problems.append('话题榜：行容器命中 %d 行却一条都没解析出来' % report['rows'])
    if not report['lowfan_block']:
        problems.append('低粉爆款侧栏容器 hwe-ll 不存在（实测 14/14 天都有）')
    elif not counts.get('lowfan'):
        problems.append('低粉爆款侧栏容器在，但一条都没解析出来（实测每天 3-4 条）')
    if not report['overseas_block']:
        if report['scope'] == SCOPE_ROLLING:
            problems.append('当天页缺少海外信息差侧栏容器 hwe-gl（实测当天页都有）')
    elif not counts.get('global'):
        problems.append('海外信息差侧栏容器在，但一条都没解析出来（实测当天页 4 条）')
    return problems


def read_robots():
    """真读一次站方 robots.txt 原文。读不到就抛异常，交给调用方决定怎么降级。"""
    with urlopen(Request(BASE + '/robots.txt', headers={'User-Agent': USER_AGENT}),
                 timeout=10) as response:
        return response.read().decode('utf-8', 'replace')


def robots_allows(target='/words/', user_agent='*', body=None, opener=None):
    """判断 robots.txt 是否允许抓 target（纯判定；除非没给 body，否则不联网）。

    - `body` 直接给原文（单测用）；不给就用 `opener` 取，默认 `read_robots`。
    - **UA 组只认 `*`**（M3）：站方另有 `User-agent: ShapBot` 的全站禁止，与我们无关。
      这是有意取舍，理由见模块 docstring。
    - 读失败时**抛异常**，由调用方 `Radar._robots_body` 决定走缓存还是 fail-open 并记账（M2）。
      本函数不吞异常，免得把「读不到」和「允许」混成同一个 True。
    """
    body = (opener or read_robots)() if body is None else body
    groups, applies = [], False
    for line in body.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(':')
        key, value = key.strip().lower(), value.strip()
        if key == 'user-agent':
            applies = value == user_agent
        elif key in ('allow', 'disallow') and applies:
            groups.append((key, value))
    if not groups:
        return True
    # 最长匹配优先；同长度 allow 胜出（robots 标准的通行做法）。
    # 只在**真正匹配上**的规则里比长度：先按全局长度取 max 再看有没有命中，
    # 会因为站方某条无关的长规则而把所有路径都判成允许（/api/ 就是这么漏出去的）。
    matched = [(kind, rule) for kind, rule in groups if rule and target.startswith(rule)]
    if not matched:
        return True
    best = max(len(rule) for _, rule in matched)
    return 'allow' in {kind for kind, rule in matched if len(rule) == best}


class Radar:
    def __init__(self, store, transport=None):
        self.store = store
        self.transport = transport
        self.root = Path(store.workspace) / 'xradar'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / 'xradar.db'
        self.lock = threading.RLock()
        self._robots = {'at': 0.0, 'body': '', 'ok': False}
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, path TEXT,
              fetched_at TEXT, counts TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS items(day TEXT, board TEXT, key TEXT, data TEXT NOT NULL,
              clue_id TEXT, PRIMARY KEY(day, board, key));
            CREATE TABLE IF NOT EXISTS briefs(day TEXT PRIMARY KEY,data TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quota(id INTEGER PRIMARY KEY CHECK(id=1), day TEXT,
              count INTEGER, last_at REAL, cooldown_until REAL);
            CREATE TABLE IF NOT EXISTS robots(id INTEGER PRIMARY KEY CHECK(id=1),
              fetched_at REAL, body TEXT);
            ''')
            self._migrate(db)
        self.path.chmod(0o600)

    def _migrate(self, db):
        """给既有 xradar.db 补列。加列用 ALTER，重复执行安全。"""
        columns = {row['name'] for row in db.execute('PRAGMA table_info(runs)')}
        for name, kind in (('health', 'TEXT'), ('page_hash', 'TEXT'),
                           ('page_bytes', 'INTEGER'), ('page_path', 'TEXT')):
            if name not in columns:
                db.execute(f'ALTER TABLE runs ADD COLUMN {name} {kind}')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # ── 配额 ────────────────────────────────────────────────────────────────
    def _quota(self):
        with self.db() as db:
            row = db.execute('SELECT * FROM quota WHERE id=1').fetchone()
        today = datetime.now(timezone.utc).date().isoformat()
        data = dict(row) if row else {'day': today, 'count': 0, 'last_at': 0, 'cooldown_until': 0}
        if data['day'] != today:
            data.update(day=today, count=0)
        return data

    def _spend(self, rate_limited=False):
        with self.db() as db:
            row = db.execute('SELECT * FROM quota WHERE id=1').fetchone()
            today = datetime.now(timezone.utc).date().isoformat()
            data = dict(row) if row else {'day': today, 'count': 0, 'last_at': 0, 'cooldown_until': 0}
            if data['day'] != today:
                data.update(day=today, count=0)
            data['count'] += 1
            data['last_at'] = time.time()
            if rate_limited:
                data['cooldown_until'] = time.time() + COOLDOWN_SECONDS
            db.execute('INSERT OR REPLACE INTO quota VALUES(1,?,?,?,?)',
                       (data['day'], data['count'], data['last_at'], data['cooldown_until']))
        return data

    def _cool_down(self, seconds=COOLDOWN_SECONDS):
        """命中限流后单独开冷却。已经计数过一次，这里不再重复计数。

        返回**写入后的**冷却截止时间戳（L1：原来返回的是更新前的旧值）。
        """
        until = time.time() + seconds
        with self.db() as db:
            db.execute('UPDATE quota SET cooldown_until=? WHERE id=1', (until,))
        return int(until)

    def _guard(self):
        data = self._quota()
        if data['cooldown_until'] > time.time():
            until = datetime.fromtimestamp(data['cooldown_until'], timezone.utc).isoformat()
            raise ValueError(f'xbangdan 限流冷却中，恢复时间 {until}')
        if data['count'] >= DAILY_LIMIT:
            raise ValueError(f'今日已请求 {data["count"]} 次，达到单日上限 {DAILY_LIMIT}')
        wait = MIN_INTERVAL - (time.time() - (data['last_at'] or 0))
        if wait > 0:
            raise ValueError(f'距上次请求不足 {MIN_INTERVAL:.0f} 秒，请 {wait:.0f} 秒后再试')

    # ── 抓取 ────────────────────────────────────────────────────────────────
    def _robots_body(self):
        """取 robots.txt 原文，带 24 小时缓存（内存 + 落表）。

        显式策略（M2），三档：
        1. 内存缓存新鲜 → 直接用；
        2. 落表缓存新鲜 → 读上来并回填内存；
        3. 都没有 → 真读一次。读失败时若还有**过期**缓存就用它（宁可按旧规则也别当没规则），
           连过期缓存都没有才 fail-open，并且这两种降级都要写进 `runs.error`。

        真的发出去的那一次 robots 请求**同样计入单日配额**（M1）：它也是打向站方的请求，
        不算进去就等于把「≤20 次」的实际上限变成 40 次。
        """
        now = time.time()
        if self._robots['body'] and now - self._robots['at'] < ROBOTS_TTL:
            return self._robots['body'], ''
        with self.db() as db:
            row = db.execute('SELECT fetched_at,body FROM robots WHERE id=1').fetchone()
        stored = (row['body'], row['fetched_at']) if row and row['body'] else ('', 0)
        if stored[0] and now - (stored[1] or 0) < ROBOTS_TTL:
            self._robots = {'at': now, 'body': stored[0], 'ok': True}
            return stored[0], ''
        try:
            self._spend()               # 这一行真的打向站方，记一次配额
            body = read_robots()
        except (HTTPError, URLError, OSError) as exc:
            stale = self._robots['body'] or stored[0]
            note = f'robots.txt 读取失败（{type(exc).__name__}）'
            if stale:
                self._robots = {'at': now, 'body': stale, 'ok': False}
                return stale, note + '，已按缓存里的旧 robots 继续'
            self._robots = {'at': 0.0, 'body': '', 'ok': False}
            return '', note + '，且没有任何缓存，本次按允许处理（fail-open）'
        self._robots = {'at': now, 'body': body, 'ok': True}
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO robots VALUES(1,?,?)', (now, body))
        return body, ''

    def _allowed(self, path):
        """按 robots 判定 path。返回 (是否允许, 需要记进 runs.error 的说明)。"""
        body, note = self._robots_body()
        return robots_allows(path, body=body), note

    def _fetch(self, path):
        """一次抓取。限流时记冷却并如实抛错，不吞掉。

        配额与限速对注入的 transport 同样生效 —— 我们对自己有礼貌，
        不能因为换了 transport 就绕过频率控制。transport 分支跳过 robots（M9，
        理由见模块 docstring）：不发真实请求就没有 robots 可守。
        """
        self._guard()
        if self.transport:
            self._spend()
            return self.transport(path), ''
        allowed, note = self._allowed(path)
        if not allowed:
            raise ValueError('robots.txt 不允许抓取该路径')
        self._spend()
        request = Request(BASE + path, headers={'User-Agent': USER_AGENT,
                                                'Accept-Language': 'zh-CN,zh;q=0.9'})
        try:
            with urlopen(request, timeout=30) as response:
                markup = response.read().decode('utf-8', 'replace')
        except HTTPError as exc:
            if exc.code in (429, 403):
                self._cool_down()
            raise ValueError(f'xbangdan 返回 {exc.code}') from exc
        except (URLError, OSError) as exc:
            raise ValueError(f'网络失败：{type(exc).__name__}') from exc
        return markup, note

    def _keep_snapshot(self, day, markup, fetched):
        """把这一页原样存下来（§6.1）。人在报警时才能回看当时页面长什么样。"""
        folder = self.root / 'pages' / day
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder.chmod(0o700)
        digest = hashlib.sha256(markup.encode('utf-8')).hexdigest()
        name = 'words-%s-%s.html.gz' % (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                                        digest[:8])
        target = folder / name
        with gzip.open(target, 'wb') as fh:
            fh.write(markup.encode('utf-8'))
        target.chmod(0o600)
        return digest, len(markup.encode('utf-8')), str(target.relative_to(self.root))

    def _prune_snapshots(self, today=None):
        """只保留最近 SNAPSHOT_KEEP_DAYS 天。每天一页，删过期目录就够。"""
        today = today or datetime.now(timezone.utc).date()
        pages = self.root / 'pages'
        if not pages.is_dir():
            return 0
        cutoff = today - timedelta(days=SNAPSHOT_KEEP_DAYS)
        removed = 0
        for folder in pages.iterdir():
            if not folder.is_dir():
                continue
            try:
                stamp_day = date.fromisoformat(folder.name)
            except ValueError:
                continue          # 不是我们建的目录，不动
            if stamp_day < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        return removed

    def sources(self):
        return [{'id': b['id'], 'label': b['label'], 'hint': b['hint'], 'url': BASE + '/words/'}
                for b in BOARDS]

    def latest(self):
        """抓今天。三张榜一次拿全。"""
        return self._collect('/words/')

    def history(self, day):
        """回补某天。日期必须是真实存在的 YYYY-MM-DD。

        只用正则校验的话 `2026-13-40` 会蒙混过关、拼出一个无效 URL 还白烧一次请求，
        所以这里用 datetime 真解析一遍（L5）。
        """
        text = str(day or '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
            raise ValueError('日期格式应为 YYYY-MM-DD')
        try:
            datetime.strptime(text, '%Y-%m-%d')
        except ValueError as exc:
            raise ValueError(f'日期不存在：{text}') from exc
        return self._collect(f'/words/{quote(text)}/')

    def _collect(self, path):
        # 复核 L7 指出这里把同一个 re.search 写了两遍；本期合同只列了 M1/L1/L4/L5，
        # 按「不许顺手做」留着原样，等下一期一并收拾。
        day = (re.search(r'/words/(\d{4}-\d{2}-\d{2})/', path).group(1)
               if re.search(r'/words/(\d{4}-\d{2}-\d{2})/', path)
               else datetime.now(timezone.utc).date().isoformat())
        with self.lock:
            markup, robots_note = self._fetch(path)
            fetched = stamp()
            # 先存快照再解析：解析器一旦跟不上新结构，原文还在，人能回看当时页面。
            page_hash, page_bytes, page_path = self._keep_snapshot(day, markup, fetched)
            boards = parse(markup)
            report = health(markup, boards)
            # 结构异常时写一条 important 告警线索；解析结果照常落 items，
            # 但 publish 会因 health.ok=False 拒绝写坏数据进线索（见 publish 文档）。
            # 告警要在写 runs 之前写：**写失败本身也是这次抓取的结果**，
            # 得跟着 error 一起落库，否则「告警没发出去」就彻底静默了（复核 L5）。
            alert_error = ''
            warned = None
            if not report['ok']:
                warned = self._warn(day, report, robots_note)
                if warned.get('error'):
                    alert_error = '告警线索写入失败：%s' % (warned.get('detail') or '未知原因')
            error = '; '.join(([robots_note] if robots_note else [])
                              + report['problems'] + ([alert_error] if alert_error else []))
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                for board, rows in boards.items():
                    for row in rows:
                        # M7：重抓同一天走 UPSERT，只更新 data，**保留 items.clue_id**。
                        # 原来 INSERT OR REPLACE 会把 clue_id 冲成 NULL，
                        # 线索关联就断了（status 的 published_clues 也会归零）。
                        db.execute('INSERT INTO items VALUES(?,?,?,?,NULL)'
                                   ' ON CONFLICT(day,board,key) DO UPDATE SET data=excluded.data',
                                   (day, board, row['key'], json.dumps(row, ensure_ascii=False)))
                db.execute('INSERT INTO runs(day,path,fetched_at,counts,error,health,'
                           'page_hash,page_bytes,page_path) VALUES(?,?,?,?,?,?,?,?,?)',
                           (day, path, fetched,
                            json.dumps({k: len(v) for k, v in boards.items()}, ensure_ascii=False),
                            error or None, json.dumps(report, ensure_ascii=False),
                            page_hash, page_bytes, page_path))
            self._prune_snapshots()
            return {'day': day, 'path': path, 'fetched_at': fetched,
                    'counts': {k: len(v) for k, v in boards.items()},
                    'health': report, 'snapshot': page_path,
                    'snapshot_hash': page_hash, 'warning': error or '',
                    'alert_clue_id': (warned or {}).get('id', ''), **boards}

    # ── 写线索中心 ──────────────────────────────────────────────────────────
    @staticmethod
    def _fact(row):
        """把一条榜单记录拼成可核对的事实文本。热度和曝光必须在里面，不能只留标题。"""
        parts = []
        raw = row.get('raw_views') or ''
        if row['board'] == 'topics':
            if row.get('days', 0) > 1:
                parts.append(f"站方标注：连续 {row['days']} 天上榜")
            heat = HEAT_LABEL.get(row['heat'], '')
            parts.append(f"热度状态：{heat}" if heat else '')
            if row['meta']:
                parts.append(f"站方原始描述：{row['meta']}")
            if row['views']:
                parts.append(f"24 小时曝光约 {format_views(row['views'])}")
            elif raw:
                # M5：解析不出数字时把原文写进去，别让这条依据凭空少一块。
                parts.append(f"站方原始曝光文案：{raw}（未能解析成数字）")
            if row['posts']:
                parts.append(f"相关帖子 {row['posts']} 条，{row['talkers']} 个账号在讨论")
            parts.append(f"榜单地址：{row['url']}")
        elif row['board'] == 'lowfan':
            parts.append(f"账号：@{row['handle']}（粉丝 {row['fans']}）")
            if row['views']:
                parts.append(f"单条曝光约 {format_views(row['views'])}")
            elif raw:
                parts.append(f"站方原始曝光文案：{raw}（未能解析成数字）")
            if row['multiple']:
                parts.append(f"放大倍数约 {row['multiple']} 倍")
            if row['topic']:
                parts.append(f"所属话题：{row['topic']}")
            parts.append(f"原文：{row['url']}")
        else:
            if row['views']:
                parts.append(f"海外曝光约 {format_views(row['views'])}")
            elif raw:
                parts.append(f"站方原始曝光文案：{raw}（未能解析成数字）")
            parts.append(f"站方标注：海外在火，中文区尚未有人讨论")
            parts.append(f"事件页：{row['url']}")
        return '\n'.join(p for p in parts if p)

    def _payload(self, row):
        if row['board'] == 'topics':
            title = row['title']
            relevance = ('站方标注「%s」。上升中的话题时效性强，见顶后只适合做复盘，'
                         '退烧后不建议再写。' % HEAT_LABEL.get(row['heat'], '未标注状态'))
        elif row['board'] == 'lowfan':
            # L4：views 解析不出来时不要留个「曝光 」的空尾巴，那是在假装有数字。
            reach = f' 曝光 {format_views(row["views"])}' if row.get('views') else ''
            title = f"【低粉爆款】@{row['handle']}{reach}"
            relevance = ('粉丝不足 1 万但单条曝光远超粉丝数，是可复用的选题模板；'
                         '结构为「一张图/一段视频 + 一句话 + 零上下文」。')
        else:
            title = f"【信息差】{row['title']}"
            relevance = '海外已在传播、中文区尚无人讨论，具备时间差红利，适合抢发。'
        # 22h：正式 heat 字段同步站方状态，manual 优先由 clues_store 兜底。
        return {'title': title[:200], 'summary': self._fact(row),
                'relevance': relevance,
                'categories': CATEGORY.get(row.get('category'), ['topics']),
                'kind': 'information', 'priority': 'normal',
                **({'heat':row['heat'],'heat_source':'site'} if row.get('heat') else {})}

    @staticmethod
    def _provenance(row, board, day, page):
        """线索的依据。字段要够让人独立复核：稳定 id、原始曝光文案、逐日量、快照路径与哈希。"""
        from collect_core import provenance
        return {
            'marker': provenance('xradar', board, row.get('key', '')),
            'board': board,
            'day': day,
            'stable_id': row.get('key', ''),
            'heat': row.get('heat', ''),
            'views': row.get('views'),
            'raw_views': row.get('raw_views', ''),
            'days': row.get('days', 0),
            'posts': row.get('posts', 0),
            'talkers': row.get('talkers', 0),
            'page_snapshot': page,
        }

    def _warn(self, day, report, robots_note=''):
        """结构异常时写一条 important 告警线索，让黄总在现有线索列表里就能看到。

        **只降级报警，不写坏数据进线索**（合同明确要求）：本次抓取解析出的条目照常落
        items 表、快照照常存，但 publish 会因为 health.ok=False 而拒绝写入。
        告警本身也是一条普通线索，走同样的 producer_key 幂等。
        """
        fact = self._fact
        reasons = '；'.join(report['problems']) or '结构异常'
        counts = report.get('counts', {})
        snapshot = self._latest_snapshot(day)
        summary = ('xbangdan 页面结构疑似变化，本次解析结果**未**写入线索。\n'
                   f'判定依据：{reasons}\n'
                   f'命中数：话题 {report.get("rows", 0)} 行 / 低粉 {counts.get("lowfan", 0)} 条'
                   f' / 海外 {counts.get("global", 0)} 条\n'
                   f'字段缺失率：{report.get("missing_ratio", 0)}；'
                   f'页面口径：{report.get("scope", "")}\n'
                   f'页面快照：pages/{day}/{snapshot or "(未落盘)"}\n'
                   + (f'备注：{robots_note}\n' if robots_note else '')
                   + '处理：解析结果已落在 items 表，快照已存档；'
                   '确认站方是否改版后再决定要不要重新回补。')
        return self._save_clue({
            'title': f'【xbangdan 结构变化告警】{day} 话题榜 {report.get("rows", 0)} 行'
                     + (f'（{reasons[:40]}）' if reasons else ''),
            'summary': summary,
            'relevance': '站方改版会让选题雷达静默失效，这条告警的作用是让人知道'
                         '「榜单没数据」不是「今天没热点」。',
            'categories': ['topics'], 'kind': 'information', 'priority': 'important',
        }, [{
            'kind': 'xradar', 'source': 'xradar', 'title': f'xbangdan 结构体检 {day}',
            'hash': hashlib.sha256(summary.encode()).hexdigest(),
            'start': 0, 'end': len(summary), 'excerpt': summary,
            'url': BASE + '/words/', 'captured_at': stamp(), 'origin_url': BASE + '/words/',
            'provenance': {'day': day, 'health': report, 'robots_note': robots_note},
        }], f'xradar:health:{day}')

    def _latest_snapshot(self, day):
        folder = self.root / 'pages' / day
        if not folder.is_dir():
            return ''
        files = sorted(folder.glob('*.html.gz'))
        return files[-1].name if files else ''

    def _save_clue(self, payload, snapshots, producer_key):
        """写一条线索。写不进去也不能拖垮抓取，但**必须把原因带回去**（复核 L5）。

        真 `clues_store._save` 会做 `_fields` 校验，payload 一旦漂移（比如标题超长、
        分类改名）就直接 `ValueError`。原来这里吞掉异常只回 `{'error': True}`，
        `_collect` 又不检查 —— 结果结构异常时既没告警、也没任何痕迹。
        """
        try:
            return self.store.clues._save(payload, snapshots, producer_key)
        except Exception as exc:  # noqa: BLE001 - 告警写不进去也不能拖垮抓取
            return {'id': '', 'duplicate': False, 'error': True,
                    'detail': f'{type(exc).__name__}: {exc}'}

    def publish(self, day=None, board=None, limit=None):
        with self.lock:return self._publish(day,board,limit)

    def _topic_thread(self,row,day):
        # Gold-only fallback: the corpus has almost no first-post URLs; inferred
        # title anchors alone have not met the zero-false-merge acceptance gate.
        if row.get('days',0)<=1 or not row.get('first_post_url'):return None
        from datetime import timedelta
        cutoff=(datetime.fromisoformat(day)-timedelta(days=7)).date().isoformat()
        with self.db() as db:
            prior=db.execute("SELECT data,clue_id FROM items WHERE board='topics' AND day>=? AND day<? AND clue_id IS NOT NULL ORDER BY day DESC",(cutoff,day)).fetchall()
        candidates=[]
        for record in prior:
            old=json.loads(record['data'])
            if old.get('category')!=row.get('category'):continue
            if old.get('first_post_url')!=row['first_post_url']:continue
            # Anchor check is conservative and never overrides exact URL evidence.
            from difflib import SequenceMatcher
            match=SequenceMatcher(None,old['title'],row['title'],autojunk=False).find_longest_match()
            if match.size<4:continue
            candidates.append(record['clue_id'])
        unique=set(candidates)
        return next(iter(unique)) if len(unique)==1 else None

    def _publish(self, day=None, board=None, limit=None):
        """把抓到的条目写进线索中心。

        22h：不同日/槽 evidence 使用独立 producer_key，duplicate 语义保持不变。
        跨天仅在 days>1、同榜近7天锚点及首发URL完全相同的可证身份下复用
        thread+revision；其余保持分离（低覆盖降级，不猜标题）。单进程源锁防
        并发选错revision；多实例写入仍不在支持范围。
        """
        day = day or datetime.now(timezone.utc).date().isoformat()
        clauses, args = ['day=?'], [day]
        if board:
            if board not in {b['id'] for b in BOARDS}:
                raise ValueError('未知榜单')
            clauses.append('board=?')
            args.append(board)
        with self.lock, self.db() as db:
            rows = db.execute('SELECT * FROM items WHERE ' + ' AND '.join(clauses)
                              + ' ORDER BY board,rowid', args).fetchall()
            blocked = None
            run = db.execute('SELECT error,health,page_path,page_hash,fetched_at FROM runs'
                             ' WHERE day=? ORDER BY id DESC LIMIT 1', (day,)).fetchone()
            if run is not None and run['health']:
                report = json.loads(run['health'])
                if not report.get('ok', True):
                    blocked = '；'.join(report.get('problems') or ['结构异常'])
            page = {'path': run['page_path'], 'hash': run['page_hash']} if run else {}
        if blocked:
            # 只降级报警，不写坏数据进线索。
            return {'day': day, 'total': len(rows), 'published': [],
                    'blocked': blocked,
                    'detail': '本次页面结构异常，解析结果未写入线索（已存快照，可回看）'}
        from clues_heat import capture_bucket
        captured=(run['fetched_at'] if run else None) or day+'T00:00:00+00:00'
        bucket=capture_bucket(captured) if captured[:10]==day else day+'T00'
        published = []
        for record in rows:
            if limit is not None and len(published) >= limit:
                break
            row = json.loads(record['data'])
            key = f"xradar:{record['board']}:{record['key']}:{bucket}:{hashlib.sha256(record['data'].encode()).hexdigest()}"
            fact = self._fact(row)
            payload=self._payload(row)
            thread=record['clue_id'] or (self._topic_thread(row,day) if record['board']=='topics' else None)
            if thread:
                before=self.store.clues.detail(thread)
                payload.update(id=thread,revision=before['revision'],status=before['status'],tracking=before['tracking'])
            obs=dict(source='xradar',bucket=bucket,mentions=row.get('posts',0),talkers=row.get('talkers',0),
                     views=row.get('views'),site_state=row.get('heat',''),captured_at=captured,
                     **{k:row.get(k) for k in ('bars_recent6','bars_prev6','ratio6_site')})
            try:
                result = self.store.clues._save(payload, [{
                    'kind': 'xradar', 'source': 'xradar', 'title': row.get('title', ''),
                    'hash': hashlib.sha256(fact.encode()).hexdigest(),
                    'start': 0, 'end': len(fact), 'excerpt': fact,
                    'url': row['url'], 'captured_at': captured,
                    'origin_url': row['url'],
                    'provenance': {**self._provenance(row, record['board'], record['day'], page),'bucket':bucket},
                }], key, observations=[obs])
            except Exception as exc:  # noqa: BLE001 - 单条失败不拖垮整批，如实记账
                published.append({'board': record['board'], 'ok': False,
                                  'error': f'{type(exc).__name__}: {exc}'})
                continue
            with self.db() as db:
                db.execute('UPDATE items SET clue_id=? WHERE day=? AND board=? AND key=?',
                           (result['id'], record['day'], record['board'], record['key']))
            published.append({'board': record['board'], 'clue_id': result['id'],
                              'duplicate': result.get('duplicate', False),
                              'title': row.get('title', '')})
        self.brief(day)
        return {'day': day, 'total': len(rows), 'published': published}

    def brief(self,day):
        """Local deterministic editorial summary, never a clue or model output."""
        from collections import Counter
        groups={'rising':[],'peak':[],'fading':[],'':[]}
        nine=('机制拆解/揭黑','专业科普/硬指标','实操教程/避坑','格局博弈/利益方与影响推演','纵向复盘/横向对标','情绪与群体心态','算账量化','求证辟谣/溯源','法律合规/监管维权')
        with self.lock,self.db() as db:
            rows=[json.loads(r[0]) for r in db.execute("SELECT data FROM items WHERE day=? AND board='topics' ORDER BY rowid",(day,))]
            for row in rows:
                heat=row.get('heat','');angle_names={'rising':(nine[2],nine[6],nine[7]),'peak':(nine[0],nine[3],nine[4]),'fading':(nine[7],nine[8]),'':(nine[1],)}[heat]
                groups[heat].append({'title':row['title'],'heat':heat,'ratio6_site':row.get('ratio6_site'),
                    'angles':list(angle_names),'site_angles':row.get('angles',[]),
                    'evidence':{'first_post':{'url':row.get('first_post_url',''),'note':row.get('first_post_note',''),'url_available':bool(row.get('first_post_url'))},
                                'hottest_post':{'url':row.get('hottest_post_url','')},'source_url':row['url']}})
            counts=Counter(r.get('heat','') for r in rows)
            champion=max(rows,key=lambda r:r.get('views') or 0,default=None)
            sentences=[f"今日在榜 {len(rows)} 个话题，上升 {counts['rising']} / 见顶 {counts['peak']} / 退烧 {counts['fading']}（未知 {counts['']}）。",
                       (f"曝光冠军是「{champion['title']}」；曝光第一不等于最近6小时发帖增长最快。" if champion else '没有可用榜单，不推断热度。'),
                       '上升抢窗口，见顶做深度，退烧仅在有新增事实时求证；首发帖与最热帖分开列示。']
            result={'day':day,'sentences':sentences,'groups':groups,'angle_categories':list(nine),'created_at':stamp()}
            db.execute('INSERT INTO briefs VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET data=excluded.data,created_at=excluded.created_at',(day,json.dumps(result,ensure_ascii=False),result['created_at']))
            return result

    def status(self):
        quota = self._quota()
        with self.db() as db:
            last = db.execute('SELECT day,fetched_at,counts,error,health,page_path,page_hash'
                              ' FROM runs ORDER BY id DESC LIMIT 1').fetchone()
            stored = {r['board']: r['n'] for r in db.execute(
                'SELECT board,COUNT(*) n FROM items WHERE day=? GROUP BY board', (last['day'],))} if last else {}
            published = db.execute('SELECT COUNT(*) n FROM items WHERE clue_id IS NOT NULL').fetchone()[0]
            recent_brief=db.execute('SELECT data FROM briefs ORDER BY day DESC LIMIT 1').fetchone()
        counts = json.loads(last['counts']) if last and last['counts'] else {}
        report = json.loads(last['health']) if last and last['health'] else {}
        error = (last['error'] if last else '') or ''
        cooling = (quota['cooldown_until'] or 0) > time.time()
        # M6：抓到但三榜全 0、或结构体检不过，都**不算可用**。
        # 原来判据是 `bool(counts)`，而 {'topics': 0, ...} 是非空 dict → truthy → 报正常。
        empty = not any(counts.values())
        # robots 降级（fail-open 或靠旧缓存）也是「这次抓取不完全可信」，同样不算可用。
        degraded = bool(report) and not report.get('ok', True)
        available = bool(counts) and not empty and not cooling and not degraded and not error
        if cooling:
            detail = '限流冷却中，暂不可抓取'
        elif error:
            detail = f'xbangdan 页面结构异常：{error}'
        elif not counts:
            detail = '还没有成功抓取过'
        elif empty:
            detail = '最近一次抓取三张榜都是 0 条，疑似站方改版'
        else:
            detail = 'xbangdan 选题雷达正常'
        return {'available': available,
                'warning': error,
                'detail': detail,
                'count': sum(counts.values()),
                'boards': {b['id']: {'label': b['label'], 'hint': b['hint']} for b in BOARDS},
                'extra': {'last_day': last['day'] if last else '',
                          'last_fetched_at': last['fetched_at'] if last else '',
                          'last_counts': counts, 'stored': stored,
                          'health': report,
                          'page_snapshot': (last['page_path'] if last else '') or '',
                          'page_hash': (last['page_hash'] if last else '') or '',
                          'published_clues': published,
                          'latest_brief':json.loads(recent_brief[0]) if recent_brief else None,
                          'today_requests': quota['count'], 'daily_limit': DAILY_LIMIT,
                          'min_interval': MIN_INTERVAL, 'cooling': cooling,
                          'cooldown_until': int(quota['cooldown_until'] or 0),
                          'home': BASE + '/words/'}}