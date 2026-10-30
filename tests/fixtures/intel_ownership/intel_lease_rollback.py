#!/usr/bin/env python3
"""intel-lease-b L2 回滚脚本：把已迁移的 intel.db 安全回退到 ce87 旧代码可运行的状态。

背景：阶段 L 给 jobs 表加了 ownership_version / owner_token / owner_dev / owner_ino 四列。
ce87 旧代码的 `_public_job` 用 ``SELECT *`` 组 dict、`interrupt_stale_jobs` 也不看
ownership_version，直接换回旧代码会（1）把 owner 原信息泄露到接口，（2）把 v1 任务按年龄
误回收。所以换回旧代码之前，必须先用本脚本把库结构恢复到 ce87 时的原样。

步骤（幂等，可重复执行）：
1. 读 jobs 表结构；四列已不存在 → 已是回滚后状态，直接成功退出（重复执行安全）。
2. 拒绝在有活跃 v1 任务时回滚：只要存在 ownership_version=1 且 status in (queued,running)
   的行就退出码 2 —— 请先停掉/排空所有 v1 执行者（等它自己收口，或由所有权恢复收口）再来。
3. 默认先备份库到 <db>.rollback-<UTC 时间戳>.bak（可用 --no-backup 跳过）。备份走 sqlite3 在线
   备份 API，不是 copy2 拍文件：服务还在跑时拍下的文件可能是不一致快照。
4. 把仍带所有权信息的行降级为 legacy 终态：ownership_version=0、owner_token=''、
   owner_dev=0、owner_ino=0（status 已是终态，保持不变；绝不动业务数据与计数）。
5. 用 ``ALTER TABLE ... DROP COLUMN`` 删除四列（SQLite >= 3.35）；老版本按 ce87 原 schema
   重建 jobs 表并拷贝数据，使库结构回到 ce87 原样。重建分支只能处理 ce87 已知的列：
   若 jobs 里存在 ce87 之后新增的列（例如阶段 B 计划的 discovery），脚本会明确报错退出，
   不会半改 schema，也不会丢数据（需人工处理）。
6. 提交。

用法：
    python intel-lease-b-L2-rollback.py <intel.db 路径> [--no-backup]
退出码：0 成功（含已是回滚后状态）；2 有活跃 v1 任务拒绝执行；1 其它错误（包括
老 SQLite 重建分支遇到 ce87 之后新增列，需人工处理）。
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

OWNER_COLUMNS = ('ownership_version', 'owner_token', 'owner_dev', 'owner_ino')

# ce87 旧代码里 jobs 表的原样 schema（不含 L 新增的四列）。DROP COLUMN 不可用时按它重建。
CE87_JOBS_SCHEMA = '''
CREATE TABLE jobs(
    id TEXT PRIMARY KEY, fakeid TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '', routes TEXT NOT NULL DEFAULT '', found INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0, bodies INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    UNIQUE(id));
'''

# 上面 schema 的列名（按顺序），用于判断库里是否存在 ce87 之后新增的列。
_CE87_JOBS_COLUMNS = (
    'id', 'fakeid', 'name', 'mode', 'status', 'reason', 'detail', 'routes', 'found', 'added',
    'bodies', 'pages', 'started_at', 'finished_at', 'outcome')


def _columns(connection, table='jobs'):
    return [row[1] for row in connection.execute(f'PRAGMA table_info({table})')]


def _has_drop_column():
    return sqlite3.sqlite_version_info >= (3, 35, 0)


def rollback(db_path, backup=True):
    path = Path(db_path)
    if not path.is_file():
        print(f'找不到数据库：{path}', file=sys.stderr)
        return 1

    connection = sqlite3.connect(str(path))
    try:
        columns = _columns(connection)
        present = [name for name in OWNER_COLUMNS if name in columns]
        if not present:
            print('jobs 表已无所有权列：已是回滚后状态，无需处理。')
            return 0
        # 活跃 v1 任务存在时拒绝回滚：先停/排空执行者。
        if 'ownership_version' in columns:
            active = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE ownership_version=1 AND status IN ('queued','running')"
            ).fetchone()[0]
            if active:
                print(f'仍有 {active} 条活跃 v1 任务（queued/running）：请先停掉并排空所有 v1 '
                      f'执行者（等它自己收口，或由所有权恢复收口）后再回滚。', file=sys.stderr)
                return 2
    finally:
        connection.close()

    if backup:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup_path = path.with_name(path.name + f'.rollback-{stamp}.bak')
        # 在线备份 API：得到一份事务一致的快照，而不是对活库拍文件。
        source = sqlite3.connect(str(path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        print(f'已备份数据库：{backup_path}')

    connection = sqlite3.connect(str(path))
    try:
        connection.execute('BEGIN IMMEDIATE')
        # 降级：把所有权信息清空、版本归零，使旧代码按 legacy 处理（不泄露、不误判 v1）。
        assignments = []
        for name, value in (('ownership_version', '0'), ('owner_token', "''"),
                            ('owner_dev', '0'), ('owner_ino', '0')):
            if name in present:
                assignments.append(f'{name}={value}')
        if assignments:
            connection.execute(f'UPDATE jobs SET {", ".join(assignments)}')
        if _has_drop_column():
            for name in present:
                connection.execute(f'ALTER TABLE jobs DROP COLUMN {name}')
        else:
            _rebuild_without_owner_columns(connection, present)
        connection.commit()
    except ValueError as exc:
        # 可预期的拒绝（例如重建分支遇到新增列）：回滚事务，给一句人话，不抛 traceback。
        connection.rollback()
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001
        connection.rollback()
        raise
    finally:
        connection.close()

    check = sqlite3.connect(str(path))
    try:
        remaining = [name for name in OWNER_COLUMNS if name in _columns(check)]
    finally:
        check.close()
    if remaining:
        print(f'回滚未完成，仍存在列：{remaining}', file=sys.stderr)
        return 1
    print('回滚完成：jobs 表已恢复到 ce87 原样，可以安全切回旧代码。')
    return 0


def _rebuild_without_owner_columns(connection, present):
    """SQLite 不支持 DROP COLUMN 时：按 ce87 原 schema 重建 jobs 表并拷贝数据。

    重建目标就是 ce87 的原样列。若库里还有 ce87 之后新增的列（例如阶段 B 的 discovery），
    旧代码本来也不认它们，本脚本不能猜着丢列或改表：直接报错退出，事务回滚，
    列与业务数据保持原样，交人工处理。
    """
    actual = [name for name in _columns(connection) if name not in OWNER_COLUMNS]
    keep = [name for name in actual if name in _CE87_JOBS_COLUMNS]
    extra = [name for name in actual if name not in _CE87_JOBS_COLUMNS]
    if extra:
        raise ValueError('jobs 表存在 ce87 之后新增的列：' + '、'.join(extra)
                         + '；本机 SQLite 不支持 DROP COLUMN，无法安全重建。'
                         '请先升级到 SQLite >= 3.35，或人工把该列一并处理后再回滚。')
    connection.execute('DROP TABLE IF EXISTS jobs_new')
    connection.execute(CE87_JOBS_SCHEMA.replace('CREATE TABLE jobs(', 'CREATE TABLE jobs_new(', 1))
    cols = ', '.join(keep)
    connection.execute(f'INSERT INTO jobs_new({cols}) SELECT {cols} FROM jobs')
    connection.execute('DROP TABLE jobs')
    connection.execute('ALTER TABLE jobs_new RENAME TO jobs')
    connection.execute('CREATE INDEX IF NOT EXISTS jobs_fakeid ON jobs(fakeid, started_at DESC)')


def main(argv=None):
    parser = argparse.ArgumentParser(description='intel-lease-b L2 回滚：恢复到 ce87 可运行的库结构')
    parser.add_argument('db', help='intel.db 路径')
    parser.add_argument('--no-backup', action='store_true', help='不备份库文件（默认先备份）')
    args = parser.parse_args(argv)
    return rollback(args.db, backup=not args.no_backup)


if __name__ == '__main__':
    sys.exit(main())
