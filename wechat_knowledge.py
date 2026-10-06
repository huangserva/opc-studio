"""Read-only adapters for collector / radar message snapshots."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')
TYPES = {1:'文本',3:'图片',34:'语音',43:'视频',47:'表情',49:'链接/文件',10000:'系统',10002:'系统'}
WECOM_TYPES = {0:'文本',2:'文本',10:'邮件',31:'链接/文件',14:'链接/文件',132:'链接/文件',3:'图片',34:'语音',43:'视频',47:'表情',1011:'系统',80:'系统',1022:'系统',105:'系统',38:'系统',1001:'系统',1002:'系统',1003:'系统',1006:'系统',573:'系统'}


def local_number(value):
    value = str(value or '')
    try:
        return int(value or '0')
    except ValueError:
        return int(hashlib.md5(value.encode()).hexdigest()[:12], 16)


def scope_clause(source, column):
    clauses, values = [], []
    chats = source.get('chatroom_ids') or []
    if chats:
        clauses.append('chatroom_id IN (' + ','.join('?' for _ in chats) + ')')
        values.extend(chats)
    for key, operator in [('date_from','>='),('date_to','<')]:
        if source.get(key):
            day = datetime.fromisoformat(source[key]).replace(tzinfo=ZONE)
            if key == 'date_to':
                day += timedelta(days=1)
            clauses.append(column + operator + '?')
            values.append(int(day.timestamp()))
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), values


@contextmanager
def source_db(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError('未找到微信消息数据库；请核对采集入口')
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    db.execute('PRAGMA query_only=ON')
    try:
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def collector_rows(db, source):
    required = {'chatroom_id','local_id','sender','content','msg_time','msg_type'}
    if not required.issubset({r[1] for r in db.execute('PRAGMA table_info(messages)')}):
        raise ValueError('collector.db 消息结构不匹配，未更新旧索引')
    where, values = scope_clause(source, 'msg_time')
    for chat, raw_id, sender, body, ts, typ in db.execute('SELECT chatroom_id,local_id,sender,content,msg_time,msg_type FROM messages' + where + ' ORDER BY msg_time', values):
        if not isinstance(body, str) or not isinstance(ts, (int,float)) or ts <= 0 or not raw_id:
            raise ValueError('采集消息存在未解码正文或缺少编号/时间，未覆盖旧索引')
        local = local_number(raw_id)
        clock = datetime.fromtimestamp(ts, ZONE).strftime('%Y-%m-%d %H:%M:%S')
        mapping = WECOM_TYPES if str(chat).startswith('wecom:') else TYPES
        yield chat, local, sender or '', body.replace('\x00',''), clock, ts, mapping.get(int(typ or 0),str(typ or '')), clock[:10], str(raw_id)


def collector_available(path):
    try:
        with source_db(path) as db:
            required = {'chatroom_id','local_id','sender','content','msg_time','msg_type'}
            if not required.issubset({r[1] for r in db.execute('PRAGMA table_info(messages)')}):
                return False
            return db.execute("SELECT 1 FROM messages WHERE typeof(content)='text' AND msg_time>0 AND local_id IS NOT NULL AND local_id!='' LIMIT 1").fetchone() is not None
    except (OSError, sqlite3.Error, ValueError):
        return False


def radar_rows(db, source):
    where, values = scope_clause(source, 'timestamp')
    for r in db.execute('SELECT chatroom_id,local_id,sender,content,time,timestamp,type,date FROM messages' + where + ' ORDER BY timestamp', values):
        yield (*r, None)
