"""外包定时任务：WorkBuddy 优先交付，本机 Pi 兜底。

黄总的目标是让 WorkBuddy（免费额度）跑定时任务、写成中间文件，内容工作台按时收取，
不花我们自己的 token；万一 WorkBuddy 哪天不免费或没交付，本机 Pi 用**同一份提示词**
补做，产出同样格式的文件。

交接文件约定（v1）。这一份是实现与提示词共同遵守的唯一标准：

* 根目录：环境变量 ``ARTICLE_STUDIO_OUTBOX``，默认 ``~/workbuddy-outbox``；
  每个任务一个子目录 ``<slug>/``，slug 只允许 ``[a-z0-9-]{1,40}``。
* 文件：``<slug>/YYYY-MM-DD.json``（当天日期，本机时区）。先写 ``YYYY-MM-DD.json.tmp``
  再改名，避免工作台读到半个文件。
* 内容（UTF-8 JSON，≤ 1 MiB）::

      {"schema": "article-studio.outsourced.v1", "task": "<slug>", "date": "YYYY-MM-DD",
       "producer": "workbuddy", "started_at": "...", "finished_at": "...",
       "status": "ok | empty | failed", "error": "",
       "items": [{"title": "", "url": "https://...", "summary": "", "tags": []}]}

* items 最多 50 条；url 必须 http(s)；title ≤ 200 字、summary ≤ 1000 字
  （超出截断并记一条警告，**不整份拒收**）。
* 同一目录可以另有一份 ``YYYY-MM-DD.md`` 给人看，工作台不解析。

判定一律看文件/内容是否合法，不猜；读不到就是读不到，不伪装成「没交付」。
"""
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager

from clues_store import TECH_TAGS
from datetime import datetime
from pathlib import Path

TZ_NAME = 'Asia/Shanghai'
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(TZ_NAME)
except Exception:  # pragma: no cover - 缺 tzdata 时退回本机时区
    TZ = None

SCHEMA = 'article-studio.outsourced.v1'
PRODUCERS = ('workbuddy', 'local-pi')
STATUSES = ('ok', 'empty', 'failed')
SLUG_PATTERN = re.compile(r'^[a-z0-9-]{1,40}$')
MAX_BYTES = 1024 * 1024
MAX_ITEMS = 50
MAX_TAGS = 12
MAX_TITLE = 200
MAX_SUMMARY = 1000
MAX_TAG = 40          # 单个自由标签的展示上限；超过会截断并留警告
MAX_RAW_TAG = 200     # provenance 里保留的原标签上限
SUMMARY_LIMIT = 4000  # clues_store 的 summary 上限，拼入外包标签后不得越过
PRODUCER_LABEL = {'workbuddy': 'WorkBuddy', 'local-pi': '本机 Pi（兜底）'}

_OUTBOX_STACK = threading.local()


class _Normalised(dict):
    """模块私有的「已经校验/规范化过」标记类型。

    输出里的 `normalised: true` 只是写给人看的，从 JSON 里谁都能伪造，所以不能当信任依据；
    这个类不导出到模块外，`isinstance(data, _Normalised)` 才是 adopt 唯一认的身份。
    `dict(data)`、`{**data}`、JSON 往返得到的都是普通 dict，一律重新校验。
    """


def today(now=None):
    """北京时间的当天日期（YYYY-MM-DD）。"""
    moment = now or datetime.now(TZ)
    return moment.strftime('%Y-%m-%d')


def outbox_root(explicit=None):
    """交接根目录：显式参数 > 线程内暂存 > 环境变量 > ~/workbuddy-outbox。"""
    if explicit is not None:
        return Path(explicit).expanduser()
    staged = getattr(_OUTBOX_STACK, 'root', None)
    if staged is not None:
        return Path(staged)
    return Path(os.environ.get('ARTICLE_STUDIO_OUTBOX') or '~/workbuddy-outbox').expanduser()


@contextmanager
def outbox_root_override(path):
    """测试与兜底执行期间把交接根目录钉住，别让环境变量飘。"""
    previous = getattr(_OUTBOX_STACK, 'root', None)
    _OUTBOX_STACK.root = str(Path(path).expanduser())
    try:
        yield _OUTBOX_STACK.root
    finally:
        _OUTBOX_STACK.root = previous


def _check_slug(slug):
    if not isinstance(slug, str) or not SLUG_PATTERN.fullmatch(slug):
        raise ValueError('slug 只允许 1 至 40 位小写字母、数字与连字符')
    return slug


def _folder_for(root, slug):
    """解析出任务的交接子目录，并挡住路径穿越。"""
    _check_slug(slug)
    base = Path(root).expanduser().resolve()
    folder = (base / slug).resolve()
    if folder.parent != base:
        raise ValueError('slug 非法：不能指向交接目录之外')
    return folder


def resolved_inside(folder, path):
    """path 是否真的位于 folder 之下（解析符号链接之后再判，防 ../ 与软链绕过）。"""
    try:
        base = Path(folder).expanduser().resolve()
        target = Path(path).expanduser().resolve()
    except OSError:
        return False
    return base == target or base in target.parents


def file_for(slug, date, root=None):
    return _folder_for(root if root is not None else outbox_root(), slug) / f'{date}.json'


def normalise_item(raw, warnings, index):
    """把一条 items 规整成规范形态；字段非法抛错，超长截断并留警告。"""
    if not isinstance(raw, dict):
        raise ValueError('第 %d 条 items 不是对象' % (index + 1))
    title = raw.get('title', '')
    url = raw.get('url', '')
    summary = raw.get('summary', '')
    tags = raw.get('tags', [])
    if not isinstance(title, str) or not title.strip():
        raise ValueError('第 %d 条 items 的 title 缺失或不是文字' % (index + 1))
    if not isinstance(url, str) or not url.strip():
        raise ValueError('第 %d 条 items 的 url 缺失' % (index + 1))
    if not url.startswith(('http://', 'https://')):
        raise ValueError('第 %d 条 items 的 url 必须是 http(s)：%s' % (index + 1, url[:80]))
    if not isinstance(summary, str):
        raise ValueError('第 %d 条 items 的 summary 不是文字' % (index + 1))
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise ValueError('第 %d 条 items 的 tags 必须是文字数组' % (index + 1))
    if len(title) > MAX_TITLE:
        warnings.append('第 %d 条 items 的 title 超过 %d 字，已截断' % (index + 1, MAX_TITLE))
        title = title[:MAX_TITLE]
    if len(summary) > MAX_SUMMARY:
        warnings.append('第 %d 条 items 的 summary 超过 %d 字，已截断' % (index + 1, MAX_SUMMARY))
        summary = summary[:MAX_SUMMARY]
    # 自由标签长度不受约束，原样拼进摘要会把摘要顶穿线索中心的 4000 字上限，
    # 让一条本来合法的交付整个导入失败。这里展示用的截到 MAX_TAG，
    # 原始标签另存到 raw_tags 供 provenance 留证（各截到 MAX_RAW_TAG）。
    kept, raw = [], []
    for tag in tags:
        clean = tag.strip()
        if not clean:
            continue
        raw.append(clean[:MAX_RAW_TAG])
        if len(clean) > MAX_TAG:
            warnings.append('第 %d 条 items 的标签超过 %d 字，已截断：%s'
                            % (index + 1, MAX_TAG, clean[:MAX_TAG]))
        kept.append(clean[:MAX_TAG])
    return {'title': title.strip(), 'url': url.strip(), 'summary': summary.strip(),
            'tags': list(dict.fromkeys(kept))[:MAX_TAGS],
            'raw_tags': list(dict.fromkeys(raw))[:MAX_TAGS]}


def normalise(data, slug, date):
    """校验已解析的 JSON 并补上派生字段（warnings、item 数量）。"""
    _check_slug(slug)
    if not isinstance(data, dict):
        raise ValueError('交接文件不是 JSON 对象')
    if data.get('schema') != SCHEMA:
        raise ValueError('schema 必须是 %s' % SCHEMA)
    if data.get('task') != slug:
        raise ValueError('task 与目录 slug 不符：%s != %s' % (data.get('task'), slug))
    if data.get('date') != date:
        raise ValueError('date 与文件名日期不符：%s != %s' % (data.get('date'), date))
    producer = data.get('producer', '')
    if producer not in PRODUCERS:
        raise ValueError('producer 只能是 %s' % ' 或 '.join(PRODUCERS))
    status = data.get('status', '')
    if status not in STATUSES:
        raise ValueError('status 只能是 %s' % ' | '.join(STATUSES))
    error = data.get('error', '')
    if not isinstance(error, str):
        raise ValueError('error 必须是文字')
    items = data.get('items', [])
    if not isinstance(items, list):
        raise ValueError('items 必须是数组')
    if len(items) > MAX_ITEMS:
        raise ValueError('items 最多 %d 条，收到 %d 条' % (MAX_ITEMS, len(items)))
    if status == 'empty' and items:
        # 「没有可交付内容」和「有 1…%d 条内容」不能同时成立。
        # 状态自相矛盾的文件一律拒收，交给兜底或未交付逻辑，别猜它到底什么意思。
        raise ValueError('status=empty 与 %d 条 items 矛盾：没有可交付内容时 items 应为 []'
                         % len(items))
    warnings = []
    if status == 'failed' and items:
        # 交付方已经明说这次失败了，里面的 items 一条都不能进线索中心。
        warnings.append('交付状态为失败，已忽略其中 %d 条' % len(items))
    # normalised 标记：写给人看，也方便日志里一眼看出这是规范化后的形态；
    # adopt 的信任已经改到类型上（见 _Normalised），这个字段本身不再作为信任依据。
    return _Normalised({'normalised': True,
                        'schema': SCHEMA, 'task': slug, 'date': date, 'producer': producer,
                        'started_at': str(data.get('started_at', '')),
                        'finished_at': str(data.get('finished_at', '')),
                        'status': status, 'error': error,
                        'items': [normalise_item(raw, warnings, i) for i, raw in enumerate(items)],
                        'warnings': warnings})


def validate(path, slug, date):
    """读取并验收一个交接文件；返回规范化数据，不合格一律抛带原因的 ValueError。

    文件必须真的落在 ``<outbox>/<slug>/`` 里 —— 只查 slug 合法是不够的，
    否则调用方可以拿任意路径来读，路径穿越就绕过去了。
    """
    target = Path(path)
    if target.suffix != '.json':
        raise ValueError('交接文件必须是 .json')
    folder = _folder_for(outbox_root(), slug)   # 先确认 slug 合法且不穿越
    if not resolved_inside(folder, target):
        raise ValueError('slug 非法：交接文件必须在 %s 之内' % folder)
    resolved = target.resolve()
    if not resolved.is_file():
        raise FileNotFoundError('交接文件不存在：%s' % target)
    size = resolved.stat().st_size
    if size > MAX_BYTES:
        raise ValueError('交接文件过大：%d 字节，上限 %d' % (size, MAX_BYTES))
    try:
        raw = json.loads(resolved.read_text(encoding='utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('交接文件不是合法 UTF-8 JSON：%s' % str(exc)[:120]) from None
    return normalise(raw, slug, date)


def validate_from_mapping(data, slug=None, date=None):
    """同一套校验，但直接吃已解析的 dict（兜底产物与测试用）。"""
    slug = slug or data.get('task')
    date = date or data.get('date')
    return normalise(data, slug, date)


def write_atomically(folder, date, data):
    """先写 .tmp 再改名，避免工作台读到半个文件。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{date}.json'
    tmp = folder / f'{date}.json.tmp'
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(target)
    return target


PROMPT_CONTRACT = """## 交接文件约定（必须严格照做，这是工作台唯一认的格式）

工作目录：{folder}
最终文件：{target}
{date_rule}
1. 先把完整内容写进 `{tmp_target}`，写完后**改名为** `{target}`。
   不要直接写最终文件——工作台会在你写的过程中读它，半个文件会被判为拒收。
2. 内容是 UTF-8 JSON，不超过 1 MiB，字段如下：

```json
{{
  "schema": "{schema}",
  "task": "{slug}",
  "date": "{date}",
  "producer": "{producer}",
  "started_at": "开始时间，ISO 8601 含时区",
  "finished_at": "结束时间，ISO 8601 含时区",
  "status": "ok | empty | failed",
  "error": "",
  "items": [
    {{"title": "标题，必填", "url": "https://...", "summary": "摘要", "tags": []}}
  ]
}}

3. 约束：
   - `items` 最多 {max_items} 条；没有可交付内容时 `items` 给 `[]` 且 `status` 写 `empty`。
   - `url` 必须是 http(s) 开头的真实地址，不能是 `file://` 或本地路径。
   - `title` 不超过 {max_title} 字，`summary` 不超过 {max_summary} 字；超了会被截断。
   - `tags` 是文字数组，没有就写 `[]`。
4. **失败也要写文件**：`status` 写 `failed`、`error` 写清原因、`items` 写 `[]`。
   不要因为失败就什么都不留——工作台靠这个文件判断「你没交付」还是「你交付了但是失败」。
5. `producer` 固定写 `{producer}`，不要改。"""

# WorkBuddy 那份是**复制一次、天天自动跑**的，所以绝不能把日期写死：
# 写死了每天都会覆盖同一个文件名，工作台按当天日期找不到文件，天天判「未交付」。
RUN_DAY_RULE = """
**下面所有 `YYYY-MM-DD` 都由你按这次运行的当天算出来**，工作台不会替你代填：
- 先取**北京时间（UTC+8）**的当天，按 `YYYY-MM-DD` 格式写成具体日期（四位年、两位月、两位日，月和日都补零）；
- 文件名里的日期、临时文件名里的日期、JSON 里 `date` 字段的值，**三处必须是同一个当天日期**；
- 不要用你收到这段提示词的那一天，也不要用 UTC 的日期 —— 工作台只认北京时间的当天。
- 这里**不举例日期**，就是为了避免你照抄示例里的那一天。宁可自己算，不要抄。
"""

RUN_DAY_HEADER = ('你是「%s」的执行者。\n'
                  '下面要用到的日期，一律按**你这次运行的北京时间当天**自己算，不要沿用任何写死的日期。\n\n')


def prompt_for(task, producer='workbuddy', outbox=None, date=None):
    """拼一份可直接复制给 WorkBuddy 的完整提示词。

    两份提示词只有日期写法不同，其余逐字相同，所以两条路产出的是同一种格式：

    * ``workbuddy`` —— 黄总只复制一次、WorkBuddy 之后每天自动跑，因此**不写任何
      具体日期**，全部留 `YYYY-MM-DD` 占位，由执行者按运行当天北京时间算。
      `date` 参数对这一份**无效**，即使传了也不会写进去。
    * ``local-pi`` —— 本机兜底，每次执行前现生成，写死当天的具体日期才是对的。
    """
    slug = _check_slug(task.get('slug', ''))
    if producer not in PRODUCERS:
        raise ValueError('producer 只能是 %s' % ' 或 '.join(PRODUCERS))
    body = task.get('body') or ''
    if not isinstance(body, str) or not body.strip():
        raise ValueError('任务提示词不能为空')
    base = outbox_root(outbox)
    folder = _folder_for(base, slug)
    if producer == 'workbuddy':
        day, target = None, f'{folder}/YYYY-MM-DD.json'
        tmp_target, date_value, date_rule = f'{target}.tmp', 'YYYY-MM-DD', RUN_DAY_RULE
        header = RUN_DAY_HEADER % (task.get('name') or slug,)
    else:
        day = date or today()
        target = folder / f'{day}.json'
        tmp_target, date_value, date_rule = f'{target}.tmp', day, ''
        header = '你是「%s」的执行者。今天是 %s（北京时间）。\n\n' % (task.get('name') or slug, day)
    contract = PROMPT_CONTRACT.format(folder=folder, target=target, tmp_target=tmp_target,
                                      date_rule=date_rule, schema=SCHEMA, slug=slug,
                                      date=date_value, producer=producer, max_items=MAX_ITEMS,
                                      max_title=MAX_TITLE, max_summary=MAX_SUMMARY)
    return f'{header}## 任务\n\n{body.strip()}\n\n{contract}\n\n' \
           '完成后只写上面那个 JSON 文件即可，不需要向用户解释过程。'

# ── 导入线索中心 ───────────────────────────────────────────────────────────
def _producer_key(slug, url):
    """按 url 去重：同一 slug 下同一个链接只导入一次。"""
    return 'outsourced:%s:%s' % (slug, hashlib.sha256(url.encode()).hexdigest()[:32])


def relabel_producer(data, producer):
    """在已规范化的交接数据上改 producer，**保持内部信任标记**。

    写成 `{**data, 'producer': ...}` 会得到一个普通 dict，adopt 就不认它，
    于是重新规范化 —— validate 记下的截断警告再次蒸发（就是 M3 那个 bug）。
    所以改 producer 必须走这里。
    """
    if not isinstance(data, _Normalised):
        raise ValueError('只能在已规范化的交接数据上改 producer')
    if producer not in PRODUCERS:
        raise ValueError('producer 只能是 %s' % ' 或 '.join(PRODUCERS))
    result = _Normalised(data)
    result['producer'] = producer
    result['warnings'] = list(data.get('warnings') or [])
    return result


def adopt(data, slug):
    """规范化只做一次，并且**带上已有的警告**。

    二次规范化会生成一份新的空 warnings：validate 记下的截断警告就这样蒸发，
    收条回执只剩一句「已导入」。所以已经规范化过的直接采用，只有原始数据才走 normalise。

    「已规范化」只认 ``_Normalised`` 这个模块私有类型。以前看的是
    `normalised is True` + schema + task 三个字段，而那份数据本来就是从 JSON 读进来的，
    谁都能写一个 `"normalised": true` 把校验整个跳过。类型在模块外拿不到，
    `dict(data)` 与 JSON 往返都会退回普通 dict，因此也就重新校验。
    """
    if isinstance(data, _Normalised):
        result = _Normalised(data)
        result['warnings'] = list(data.get('warnings') or [])
        return result
    return normalise(data, slug, (data or {}).get('date') or today())


def fit_summary(base, extras, warnings, url=''):
    """把外包标签拼进摘要，并保证结果不越过线索中心的 4000 字上限。

    标签已按 MAX_TAG 截断，正常情况下这里远到不了上限；这层是硬兜底，
    万一将来放宽了标签长度，也不至于让整条导入被 summary 上限拒掉。
    """
    summary = base
    if extras:
        summary = ('%s\n\n外包标签：%s' % (base, '、'.join(extras))).strip()
        if len(summary) > SUMMARY_LIMIT:
            warnings.append('%s：拼入外包标签后摘要超过 %d 字，已截断'
                            % (str(url)[:80], SUMMARY_LIMIT))
            summary = summary[:SUMMARY_LIMIT]
    return summary


def ingest(clues, slug, data):
    """把交接文件里的 items 导入线索中心。不调用模型。

    返回 ``{producer, file, imported, skipped_duplicates, warnings, clue_ids}``。
    同一文件重复收取、或跨文件出现同一 url，都不会重复导入。
    """
    _check_slug(slug)
    data = adopt(data, slug)          # 只规范化一次，validate 的警告随数据一起带过来
    warnings = list(data.get('warnings', []))
    delivered = data['status'] != 'failed'
    label = PRODUCER_LABEL.get(data['producer'], data['producer'])
    imported, duplicates, clue_ids = 0, 0, []
    # 交付方报 failed 时的 items 一条都不导入 —— 失败交付里的内容正是最不该进线索中心的。
    for item in (data['items'] if delivered else []):
        key = _producer_key(slug, item['url'])
        # 线索的 tags 是 9 项固定技术分类，而外包任务带回来的是任意自由标签。
        # 只把恰好命中分类的写进去，其余原样留在 summary 与 provenance 里，
        # 既不硬塞污染分类，也不把交付方的信息丢掉。
        matched = [t for t in item['tags'] if t in TECH_TAGS]
        extra = [t for t in item['tags'] if t not in TECH_TAGS]
        summary = fit_summary(item['summary'] or item['title'], extra, warnings, item['url'])
        payload = {
            'title': item['title'],
            'summary': summary,
            'relevance': '%s交付的外包任务结果，尚未核对业务原文' % label,
            'categories': ['technology'],
            'status': 'candidate',
            'kind': 'information',
            'tags': matched or None,
            'reason': '%s 交付，按约定导入线索中心' % label,
        }
        evidence = [{
            'kind': 'outsourced', 'source': 'outsourced-tasks',
            'title': item['title'],
            'hash': hashlib.sha256(item['summary'].encode()).hexdigest(),
            'start': 0, 'end': len(item['summary']),
            'excerpt': item['summary'] or item['title'],
            'origin_url': item['url'], 'url': item['url'],
            'modified': data.get('finished_at') or '',
            'captured_at': datetime.now(TZ).isoformat() if TZ else datetime.now().astimezone().isoformat(),
            # provenance 留原始标签（各截到 MAX_RAW_TAG），展示用的截断不丢证据
            'provenance': {'task': slug, 'producer': data['producer'], 'date': data['date'],
                           'tags': item.get('raw_tags') or item['tags']},
        }]
        try:
            result = clues._save(payload, evidence, key)
        except ValueError as exc:
            warnings.append('%s：%s' % (item['url'][:80], str(exc)[:120]))
            continue
        if result.get('duplicate'):
            duplicates += 1
            continue
        imported += 1
        clue_ids.append(result['id'])
    result = {'producer': data['producer'], 'file': '%s/%s.json' % (slug, data['date']),
              'status': 'succeeded' if delivered else 'failed',
              'imported': imported, 'skipped_duplicates': duplicates,
              'warnings': warnings, 'clue_ids': clue_ids}
    if not delivered:
        # 没导入就是没导入，回执要说实话；error 键只在真有错误时才出现。
        result['error'] = '交付方报告失败：%s' % (data.get('error') or '未说明原因')
    return result
