"""只修改当前隔离E2E工作区一条记录的时间，用来验证八天前筛选。"""
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
workspace = Path(sys.argv[1]).resolve()
assert workspace.name == 'workspace'
assert workspace.parent.name.startswith('article-studio-e2e-')
assert workspace.parent.parent == Path(tempfile.gettempdir()).resolve()
assert re.fullmatch('[a-f0-9]{32}', sys.argv[2])
with sqlite3.connect(workspace / 'inspiration' / 'intake.db') as connection:
    result = connection.execute('UPDATE drops SET received_at=? WHERE id=?',
                                ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(), sys.argv[2]))
    assert result.rowcount == 1
