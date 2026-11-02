"""Exact group allowlist, revision checks, permission-preserving atomic saves."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unicodedata

DEFAULT_GROUP = '👂小耳Omia / FM / 审美👂'

class Conflict(ValueError):
    pass

def workspace(root):
    root = Path(root)
    return root / 'workspace' if (root / 'workspace').is_dir() else root

def path_for(root):
    return workspace(root) / 'wechat-tools/whitelist.json'

def validate_group(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 120 or value != value.strip()
            or any(unicodedata.category(c) in ('Cc', 'Cf', 'Zl', 'Zp') for c in value)
            or ' · ' in value or 'wxid_' in value or '@chatroom' in value):
        raise ValueError('群名须为完整名称，不能含首尾空格、隐藏字符、成员信息或微信号')
    return value

def read(root):
    path = path_for(root)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('白名单不能是符号链接')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return dict(groups=[DEFAULT_GROUP], revision='missing')
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
            raise ValueError('白名单文件无效')
        raw = stream.read(32769)
    data = json.loads(raw)
    if set(data) != {'groups'} or not isinstance(data['groups'], list) or len(data['groups']) > 30:
        raise ValueError('白名单格式无效')
    groups = [validate_group(g) for g in data['groups']]
    if len(set(groups)) != len(groups):
        raise ValueError('群名不能重复')
    return dict(groups=groups, revision=str(info.st_mtime_ns)+':'+hashlib.sha256(raw).hexdigest())

def save(root, payload):
    if not isinstance(payload, dict) or set(payload) != {'groups', 'revision'}:
        raise ValueError('只允许 groups 和 revision')
    groups = payload['groups']
    if not isinstance(groups, list) or len(groups) > 30:
        raise ValueError('最多30个精选群')
    groups = [validate_group(g) for g in groups]
    if len(set(groups)) != len(groups):
        raise ValueError('群名不能重复')
    path = path_for(root)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('白名单不能是符号链接')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = os.open(path.parent/'.whitelist.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = read(root)
        if before['revision'] != payload['revision']:
            raise Conflict('白名单已被修改，请刷新后重试')
        fd, name = tempfile.mkstemp(prefix='.whitelist-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
                stream.write((json.dumps({'groups': groups}, ensure_ascii=False, indent=2)+'\n').encode())
                stream.flush(); os.fsync(stream.fileno())
            if read(root)['revision'] != before['revision']:
                raise Conflict('白名单已被修改，请刷新后重试')
            os.replace(name, path)
            parent = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(parent)
            finally: os.close(parent)
        finally:
            if os.path.exists(name): os.unlink(name)
    finally:
        os.close(lock)
    return read(root)

def curated(group, groups):
    # The title prefix is split at the metadata delimiter before this check.
    # Never strip/casefold/NFKC invisible or lookalike group names into trust.
    return isinstance(group, str) and group in groups
