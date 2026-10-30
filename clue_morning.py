"""微信线索晨报：只读运行记录和线索库，生成本地 Markdown 与可核验元数据。"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import tempfile
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
CATEGORIES = {'business': '商单', 'technology': '新技术', 'topics': '热点', 'projects': '项目进展', 'other': '其他'}


def workspace(root):
    root = Path(root)
    return root if root.name == 'workspace' else root / 'workspace'


def read_db(path):
    return closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10))


def collect(root, now):
    """以运行开始时间取最近24小时；只收微信任务，partial 也保留已落库线索。"""
    base = workspace(root)
    cutoff = now - timedelta(hours=24)
    ids, run_ids = set(), []
    with read_db(base / 'schedules/tasks.db') as db:
        runs = db.execute("""SELECT id, output FROM runs
            WHERE status IN ('succeeded','partial')
              AND julianday(started_at) >= julianday(?) AND julianday(started_at) <= julianday(?)
              AND json_extract(snapshot,'$.kind') IN ('wechat_clues','wechat_radar')
            ORDER BY started_at,id""", (cutoff.isoformat(), now.isoformat())).fetchall()
        for run_id, output in runs:
            try:
                result = json.loads(output)
                clues = result.get('clues', [])
                if not isinstance(clues, list) or any(not isinstance(c, str) or not c for c in clues):
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise ValueError('微信运行结果格式异常，未生成晨报') from None
            ids.update(clues)
            run_ids.append(run_id)
    rows = []
    with read_db(base / 'clues/clues.db') as db:
        for clue_id in sorted(ids):
            row = db.execute('SELECT data,created_at FROM threads WHERE id=?', (clue_id,)).fetchone()
            if row is None:
                raise ValueError('运行引用的线索不存在，未生成晨报')
            created = datetime.fromisoformat(row[1].replace('Z', '+00:00'))
            if not cutoff <= created <= now:
                continue
            data = json.loads(row[0])
            categories = [c for c in CATEGORIES if c in data.get('categories', [])] or ['other']
            speakers = set()
            # 只取微信证据的具名字段，不把标题、聊天群名或第一段原文当发言人。
            for entry, in db.execute("SELECT data FROM entries WHERE thread_id=? AND entry_type='evidence' ORDER BY rowid", (clue_id,)):
                for evidence in json.loads(entry).get('evidence', []):
                    if evidence.get('kind') not in ('wechat', 'wechat_links'):
                        continue
                    p = evidence.get('provenance') or {}
                    for key in ('speaker', 'sender_name', 'author'):
                        value = p.get(key)
                        if isinstance(value, str) and value.strip():
                            speakers.add(value.strip())
            rows.append(dict(id=clue_id, created_at=row[1], title=data.get('title', ''), summary=data.get('summary', ''),
                             categories=categories, speakers=sorted(speakers)))
    rows.sort(key=lambda r: (r['created_at'], r['id']), reverse=True)
    return rows, run_ids, cutoff


def atomic_write(path, data):
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def generate(root, now=None, require_judgment=False):
    now = now or datetime.now(timezone.utc)
    rows, run_ids, cutoff = collect(root, now)
    day = now.astimezone(TZ).date().isoformat()
    counts = {c: sum(c in row['categories'] for row in rows) for c in CATEGORIES}
    lines = ['# 微信线索晨报 · ' + day, '', '生成时间：' + now.astimezone(TZ).isoformat(),
             '统计窗口：' + cutoff.astimezone(TZ).isoformat() + ' 至 ' + now.astimezone(TZ).isoformat(),
             f'线索共 {len(rows)} 条（按 ID 去重；仅计窗口内新建线索；多类别线索在各类别分别计数）。',
             '分类计数：' + '、'.join(f'{label} {counts[c]}' for c, label in CATEGORIES.items()), '']
    if not rows:
        lines += ['今日无新增', '', '当日判断：今日无新增']
    else:
        for category, label in CATEGORIES.items():
            group = [r for r in rows if category in r['categories']]
            if not group:
                continue
            lines += [f'## {label}（{len(group)} 条）', '']
            selected = group if len(rows) <= 50 or category == 'business' else group[:8]
            for row in selected:
                lines += ['### ' + row['title'], '', '线索编号：' + row['id']]
                if category == 'business':
                    lines += ['发言人：' + ('、'.join(row['speakers']) or '未知（原始证据未提供具名字段）'),
                              '合作需求（线索摘要，待核实）：' + (row['summary'] or '未提供，待核实')]
                else:
                    lines += [row['summary'] or '线索暂无摘要，待核实']
                lines.append('')
            if len(selected) < len(group):
                lines += [f'本类仅展示最新 {len(selected)} 条，其余 {len(group)-len(selected)} 条请到线索中心查看。', '']
        lines += [f'当日判断：本轮共 {len(rows)} 条线索，其中商单 {counts["business"]} 条；' +
                  ('优先核实合作需求与发言人。' if counts['business'] else '可按新技术、热点和项目进展继续筛选。')]
    content = ('\n'.join(lines) + '\n').encode('utf-8')
    metadata = dict(date=day, generated_at=now.isoformat(), window_start=cutoff.isoformat(), window_end=now.isoformat(),
                    clue_count=len(rows), category_counts=counts, clue_ids=[r['id'] for r in rows], run_ids=run_ids,
                    markdown_sha256=hashlib.sha256(content).hexdigest(),
                    judgment=dict(source='pending' if require_judgment and rows else 'rules', generated_at=now.isoformat()))
    folder = workspace(root) / 'clue-morning'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (day + '.md')
    atomic_write(path, content)
    # JSON 最后发布，读取者用摘要核对，避免读取两次生成混合的文件对。
    atomic_write(path.with_suffix('.json'), json.dumps(metadata, ensure_ascii=False, indent=2).encode('utf-8'))
    return path, metadata


def finalize(root, judgment, expected_sha256, now=None):
    """Pi 只可替换最后判断一句；不接收模型提供的 Markdown 或任何计数。"""
    now = now or datetime.now(timezone.utc)
    path = workspace(root) / 'clue-morning' / (now.astimezone(TZ).date().isoformat() + '.md')
    from interest_channel import _read
    raw, _ = _read(path)
    meta_raw, _ = _read(path.with_suffix('.json'))
    meta = json.loads(meta_raw)
    if hashlib.sha256(raw).hexdigest() != expected_sha256 or meta['markdown_sha256'] != expected_sha256:
        raise ValueError('晨报已变化，拒绝补写')
    if not isinstance(judgment, str) or not 1 <= len(judgment.strip()) <= 300 or any(c in judgment for c in '\n\r\x00'):
        raise ValueError('当日判断须为一行1至300字')
    if not meta['clue_count']:
        raise ValueError('无新增时使用固定判断，不调用模型补写')
    text = raw.decode('utf-8')
    head, marker, _ = text.rpartition('\n当日判断：')
    if not marker:
        raise ValueError('晨报缺少判断段落')
    content = (head + marker + judgment.strip() + '\n').encode('utf-8')
    meta.update(markdown_sha256=hashlib.sha256(content).hexdigest(),
                judgment=dict(source='pi', generated_at=now.isoformat()))
    atomic_write(path, content)
    atomic_write(path.with_suffix('.json'), json.dumps(meta, ensure_ascii=False, indent=2).encode('utf-8'))
    return path, meta


def snapshot(root, now=None):
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(TZ).date().isoformat()
    path = workspace(root) / 'clue-morning' / (day + '.md')
    try:
        if path.parent.is_symlink():
            raise ValueError()
        from interest_channel import _read
        raw, _ = _read(path)
        meta_raw, _ = _read(path.with_suffix('.json'))
        meta = json.loads(meta_raw)
        if meta['date'] != day or meta['markdown_sha256'] != hashlib.sha256(raw).hexdigest():
            raise ValueError()
        if meta.get('judgment', {}).get('source') == 'pending':
            return dict(status='pending', date=day, content='', message='今日晨报正在补写当日判断')
        return dict(status='ok', date=day, content=raw.decode('utf-8'), generated_at=meta['generated_at'],
                    clue_count=meta['clue_count'], category_counts=meta['category_counts'])
    except FileNotFoundError:
        return dict(status='missing', date=day, content='', message='今日晨报尚未生成，每天 08:30 运行')
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return dict(status='error', date=day, content='', message='今日晨报文件异常，请查看任务运行记录')


def schedule_payload(root, profile_id=None):
    root = Path(root).resolve()
    command = shlex.join([str(root.parent / 'article-importer/.venv/bin/python'), str(root / 'clue_morning.py'), '--root', str(root)])
    body = ('生成微信线索晨报。第一步必须使用 run_command 执行下面这一条本机命令，不能只回复计划：\n' + command + ' --prepare' +
            '\n命令只读当前仓库 workspace/schedules/tasks.db 最近24小时 succeeded/partial 的微信任务 runs.clues，'
            '按去重 ID 查询 workspace/clues/clues.db，仅计同一24小时窗口内新建线索的真实分类和摘要。'
            '商单最前且全列，总数超过50时其他类别各展示最新8条及剩余计数；标注具名发言人字段及合作需求，'
            '最后一句当日判断；无新线索写「今日无新增」，不得编造。'
            '必须生成 workspace/clue-morning/<北京时间日期>.md 与同名 .json（总数、分类计数、生成时间）。'
            '命令输出仅含路径、计数与 markdown_sha256，不要读取或转述真实聊天、成员昵称、微信号，不调用联网。'
            '若 clue_count 为0，生成器已写固定判断「今日无新增」，不要再编造判断。'
            '若 clue_count 大于0，第二步：根据这些计数写一句克制的当日判断（只判断优先级，不推测具体线索内容），'
            '通过 run_command 执行下面命令，将 <摘要> 替换成第一步返回的 markdown_sha256，将 <一句判断> 替换为你写的判断：\n' +
            command + ' --finalize --expected-sha256 <摘要> --judgment \'<一句判断>\'\n'
            '此入口只允许补写判断，不能改条数、类别计数或线索正文；不得另写文件。'
            '任一步命令失败则如实报告失败，不得声称已生成；补写成功后只回复已生成及条数和文件路径。')
    return dict(name='微信线索晨报', kind='pi', body=body, cwd=str(root), frequency='daily', at='08:30',
                weekdays=list(range(7)), timeout=300, enabled=True, allow_commands=True,
                assistant_id='isolated', tool_groups=[], profile_id=profile_id, result_destination='runs')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--finalize', action='store_true')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--judgment')
    args = parser.parse_args()
    try:
        path, meta = finalize(args.root, args.judgment, args.expected_sha256) if args.finalize else generate(args.root, require_judgment=args.prepare)
        print(json.dumps(dict(status='awaiting_judgment' if meta['judgment']['source']=='pending' else 'generated',
                              path=str(path), clue_count=meta['clue_count'], category_counts=meta['category_counts'],
                              markdown_sha256=meta['markdown_sha256']), ensure_ascii=False))
    except Exception:
        print('微信线索晨报生成失败；请检查本地运行记录与线索库，未确认成功。')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
