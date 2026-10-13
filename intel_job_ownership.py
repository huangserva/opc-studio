"""任务所有权：持续持有的 flock handle 与身份辅助。

本模块只做锁 handle 与身份校验，不碰数据库、网络或业务。一个 LockHandle 对应一次任务
认领：服务端生成 token，锁文件在 ``<intel>/locks/<token>.lock``（目录 0700、普通文件 0600），
以 O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC 打开并 LOCK_EX|LOCK_NB 持续持有到任务结束。
同一 open file description 只有一个关闭责任者（close 幂等）；活跃锁绝不 unlink/rename/重建。

恢复（另一实例或同一实例下次检查）只做非阻塞探测：文件身份（st_dev/st_ino）匹配且能取到
锁 → 原所有权已结束；文件缺失/身份不符/符号链接/无权限 → unknown，绝不按年龄回收。

纯 fork 子进程只 close 继承来的 FD，不能 LOCK_UN（共享 open file description 解锁会伤父进程）；
exec 由 CLOEXEC 挡住继承。调用方不得把锁 FD 放进 pass_fds。

除每个任务的 token 锁外，还有一把固定的全局清理期闸门 ``gate.lock``：任何一次认领先非阻塞
取到它并持有到同一次认领的 FD 关闭，因此「终态已写、旧 FD 仍持锁」的清理期对其他进程同样
可见——第二个进程取不到闸门就不会认领。

身份归类只有一处：``classify()``（Python）与 ``LEGACY_WHERE``（SQL 谓词）必须给出同一个
legacy/v1/unknown 判定。只有严格 legacy（version 0 且 owner 三件套全空）可以按年龄回收；
v1 与一切身份残缺/未知行都保持闸门，不因类型畸形而抛异常。
"""
import errno
import fcntl
import os
import re
import secrets
import stat
import threading
from pathlib import Path

# 锁文件里的 token：32 位十六进制（服务端生成，绝不来自 payload）。
TOKEN_RE = re.compile(r'^[0-9a-f]{32}$')

# 恢复探测的状态。
HELD = 'held'          # 原任务锁仍被持有：保持 active，无论年龄。
ACQUIRED = 'acquired'  # 原任务锁无人持有且身份匹配：可以 CAS 收口。
UNKNOWN = 'unknown'    # 缺失/替换/符号链接/无权/残缺身份：保持闸门，不自动修复。

# 身份安全归类的结果（classify）。
LEGACY = 'legacy'      # version 0 且 token/dev/ino 三件套全空：旧协议行。
V1 = 'v1'              # version 1 且 token/dev/ino 全部合法：所有权协议行。

# 全局清理期闸门：与每个 token 锁分开的固定名文件。任何一次认领（手动/定时）先非阻塞
# 取到它、并持续持有到同一次认领的 FD 关闭；因此「终态已写、旧 FD 仍持锁」的清理期
# 对其他进程同样可见，第二个进程拿不到闸门就不会认领。
GATE_NAME = 'gate.lock'

# 当前用的所有权协议版本。与 intel_store.OWNERSHIP_VERSION 保持一致。
OWNERSHIP_VERSION = 1

# SQL 侧的严格 legacy 谓词：必须是 ``classify() == LEGACY`` 的同一判定，不能各写一套。
# 年龄清理、短宽限排除、缺 owner 的写入守卫与订阅回读都用它，因此「公开投影说是 unknown」
# 的行绝不会在 SQL 里被当成 legacy 回收或放行。IFNULL 只把 NULL 当空，与 _zero_number/_text_blank
# 一致；BLOB/文本/浮点等非零值在 SQL 与 Python 两侧都不算空。
LEGACY_WHERE = ("IFNULL(ownership_version,0)=0 AND IFNULL(owner_token,'')='' "
                "AND IFNULL(owner_dev,0)=0 AND IFNULL(owner_ino,0)=0")
# 严格 legacy 的补集：v1 与一切身份残缺/未知行都落在这一侧，必须保持闸门。
NOT_LEGACY_WHERE = f"NOT ({LEGACY_WHERE})"


class OwnershipError(Exception):
    """锁目录/文件不满足安全前提：拒绝认领，不降级为无锁。"""


class OwnershipBusy(OwnershipError):
    """清理期闸门已被占用（另一任务在跑或正在收尾）：本轮不认领，交由调用方决定复用/跳过。"""


# 进程内所有活着的 handle。纯 fork 的子进程用它只 close 继承 FD，不 unlock。
_OPEN_HANDLES = set()
_HANDLES_LOCK = threading.Lock()


def new_token():
    """服务端唯一 owner token；格式与 TOKEN_RE 一致。"""
    return secrets.token_hex(16)


def _as_int(value):
    """严格整数解析：只接受真正能当整数用的值，其余一律 None，绝不抛出。

    故意比 ``int(x)`` 严格：无限/NaN/非整数浮点会抛 OverflowError 或悄悄截断，BLOB 更不
    是合法身份列。数据库里存着什么类型都可能（SQLite 动态类型），所以坏值必须归类而不是抛。
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, (bytes, bytearray)):
        return None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _token_ok(token):
    """token 必须是服务端生成的 32 位十六进制**字符串**；BLOB/None/其他类型一律不合格。"""
    return isinstance(token, str) and TOKEN_RE.match(token) is not None


def _text_blank(value):
    """文本列的「空」：只认 None 与空串；BLOB（包括 b''）、'0' 等都不算空。

    与 SQL 里的 ``IFNULL(owner_token,'')=''`` 同义（SQLite 里 BLOB 永远不等于 TEXT）。
    """
    return value is None or value == ''


def _zero_number(value):
    """数值列的「空/0」：None，或能无损解析成整数 0 的值（'0'、0.0）。

    BLOB、空串、非整数、无限都不算 0；与 SQL 里的 ``IFNULL(col,0)=0`` 同义（那些值
    存进 INTEGER 列后仍不是数字 0）。四列都是 NOT NULL，None 只是防御性处理。
    """
    return value is None or _as_int(value) == 0


def is_legacy_identity(version, token, dev, ino):
    """严格 legacy 判定：version 恰为 0，且 token/dev/ino 三件套全空（旧协议行）。

    这是 ``classify()`` 与 SQL 谓词 ``LEGACY_WHERE`` 的唯一判定来源：任何一侧改了另一侧
    也要跟着变，测试会逐元组对照两者。v0 却混入 owner 字段的行不是 legacy，
    不能按年龄回收。
    """
    return (_zero_number(version) and _text_blank(token)
            and _zero_number(dev) and _zero_number(ino))


def safe_version(value):
    """对外展示用的安全版本号：严格整数原样给（未知版本如 9 仍给 9），坏值给 0。

    权威判定永远是 ``classify()``/``ownership_state``；这个字段只为页面渲染服务，
    不能反过来把非法 version 伪装成 legacy。
    """
    parsed = _as_int(value)
    return parsed if parsed is not None else 0


def classify(version, token, dev, ino):
    """把一条任务的 (version, token, dev, ino) 安全归类，绝不抛异常。

    返回 LEGACY / V1 / UNKNOWN 之一：
    - LEGACY：``is_legacy_identity`` 成立（version 0 且 token/dev/ino 三件套全空）；
    - V1：version 1 且 token 合法、dev/ino 是正整数（所有权协议行）；
    - UNKNOWN：其余一切（version 残缺/非法/未知、token 缺失/非法/非字符串、
      dev/ino 非法或非正、version 0 却混入非空 owner 字段）。调用方据此保持闸门并给安全提示。

    先用原始四元组校验类型与完整整数值，再做任何转换：无限数、非整数、BLOB、
    非法 version 都只能落进 UNKNOWN，不能先降级成 0/1 再归类。
    """
    if is_legacy_identity(version, token, dev, ino):
        return LEGACY
    if _as_int(version) != OWNERSHIP_VERSION:
        return UNKNOWN
    if not _token_ok(token):
        return UNKNOWN
    d, i = _as_int(dev), _as_int(ino)
    if d is None or i is None or d <= 0 or i <= 0:
        return UNKNOWN
    return V1


def normalize_root(workspace):
    """规范化 workspace（store.workspace/intel）为真实路径，锁目录固定在其下。"""
    return Path(workspace).resolve()


def locks_dir(root):
    return Path(root) / 'locks'


def _check_dir(path):
    """目录必须是普通目录、属于当前用户、不是符号链接；否则拒绝。"""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise OwnershipError(f'锁目录不可访问：{exc}') from exc
    if stat.S_ISLNK(st.st_mode):
        raise OwnershipError('锁目录是符号链接，拒绝使用')
    if not stat.S_ISDIR(st.st_mode):
        raise OwnershipError('锁目录不是普通目录，拒绝使用')
    if st.st_uid != os.getuid():
        raise OwnershipError('锁目录不属于当前用户，拒绝使用')
    return st


def ensure_locks_dir(root):
    """规范化锁目录：创建（0700）并校验身份。异常一律抛 OwnershipError。"""
    path = locks_dir(root)
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise OwnershipError(f'锁目录不可创建：{exc}') from exc
    _check_dir(path)
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise OwnershipError(f'锁目录不可写：{exc}') from exc
    return path


class LockHandle:
    """一次任务认领持有的同一 FD。close 幂等；job_id 由调用方回填。

    同时持有清理期闸门 FD（gate_fd，可空——恢复探测得到的 handle 不带闸门）：
    close() 是唯一的关闭责任者，两个 FD 一起关闭，闸门因此一直保持到同一次认领结束。
    """

    def __init__(self, fd, token, dev, ino, path, gate_fd=None):
        self.fd = fd
        self.gate_fd = gate_fd
        self.token = token
        self.dev = int(dev)
        self.ino = int(ino)
        self.path = Path(path)
        self.job_id = ''
        self._closed = False
        with _HANDLES_LOCK:
            _OPEN_HANDLES.add(self)

    @property
    def identity(self):
        return (self.dev, self.ino)

    def close(self):
        """唯一关闭责任者。幂等；只 close，绝不 LOCK_UN 别人可能共享的锁。"""
        if self._closed:
            return
        self._closed = True
        with _HANDLES_LOCK:
            _OPEN_HANDLES.discard(self)
        for fd in (self.fd, self.gate_fd):
            if fd is None:
                continue
            try:
                os.close(fd)
            except OSError:
                pass

    @property
    def closed(self):
        return self._closed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _open_locked(dir_fd, name, flags, mode=0o600):
    """在固定 dir_fd 下打开锁文件并校验身份/权限/链接。失败抛 OwnershipError。"""
    try:
        fd = os.open(name, flags, mode, dir_fd=dir_fd)
    except FileExistsError as exc:
        raise OwnershipError('锁文件已存在，拒绝复用') from exc
    except OSError as exc:
        raise OwnershipError(f'锁文件不可打开：{exc}') from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OwnershipError('锁文件不是普通文件')
        if st.st_nlink != 1:
            raise OwnershipError('锁文件有异常硬链接')
        if st.st_uid != os.getuid():
            raise OwnershipError('锁文件不属于当前用户')
        # 路径身份必须与 FD 身份一致：防止创建后被替换。
        try:
            pst = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError as exc:
            raise OwnershipError(f'锁文件路径不可核对：{exc}') from exc
        if (pst.st_dev, pst.st_ino) != (st.st_dev, st.st_ino):
            raise OwnershipError('锁文件路径与 FD 身份不一致')
        os.set_inheritable(fd, False)
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def _open_gate(dir_fd):
    """在固定 dir_fd 下打开并占用清理期闸门锁。被占用时抛 OwnershipBusy。"""
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(GATE_NAME, flags, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        raise OwnershipError(f'清理期闸门锁不可打开：{exc}') from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OwnershipError('清理期闸门锁不是普通文件')
        if st.st_nlink != 1:
            raise OwnershipError('清理期闸门锁有异常硬链接')
        if st.st_uid != os.getuid():
            raise OwnershipError('清理期闸门锁不属于当前用户')
        os.set_inheritable(fd, False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise OwnershipBusy('已有任务在进行或正在收尾') from exc
            raise OwnershipError(f'清理期闸门锁不可用：{exc}') from exc
    except BaseException:
        os.close(fd)
        raise
    return fd


def acquire(root, token):
    """创建并持续持有锁与清理期闸门。成功返回 LockHandle；绝不降级无锁。

    先非阻塞取全局清理期闸门（跨进程可见），再建并持有本任务的 token 锁；两个 FD 由
    同一个 handle 持有到结束。闸门被占用（另一任务在跑/收尾）时抛 OwnershipBusy，
    调用方据此复用现有任务或跳过本轮，绝不排队。
    """
    if not _token_ok(token):
        raise OwnershipError('owner token 格式非法')
    path = ensure_locks_dir(root)
    dir_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    gate_fd = None
    fd = None
    st = None
    name = f'{token}.lock'
    try:
        # 固定 dir_fd：路径替换风险被约束在已校验的目录句柄内。
        gate_fd = _open_gate(dir_fd)
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd, st = _open_locked(dir_fd, name, flags)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OwnershipError(f'锁文件被占用：{exc}') from exc
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if gate_fd is not None:
            try:
                os.close(gate_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(dir_fd)
    return LockHandle(fd, token, st.st_dev, st.st_ino, path / name, gate_fd=gate_fd)


def probe(root, token, dev, ino):
    """恢复探测：不 O_CREAT 打开原锁文件，核身份并尝试 LOCK_EX|LOCK_NB。

    返回 (state, handle)：ACQUIRED 时 handle 有效，调用方负责 close；HELD/UNKNOWN 时 handle 为 None。
    文件缺失、身份不符、符号链接、无权限、token/dev/ino 残缺一律 UNKNOWN，绝不按年龄回收，
    也绝不因单条坏数据抛异常。
    """
    if not _token_ok(token):
        return UNKNOWN, None
    d, i = _as_int(dev), _as_int(ino)
    if d is None or i is None or d <= 0 or i <= 0:
        return UNKNOWN, None
    path = locks_dir(root)
    try:
        _check_dir(path)
    except OwnershipError:
        return UNKNOWN, None
    try:
        dir_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return UNKNOWN, None
    name = f'{token}.lock'
    try:
        try:
            pst = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError:
            return UNKNOWN, None  # 缺失或不可访问
        if stat.S_ISLNK(pst.st_mode) or not stat.S_ISREG(pst.st_mode):
            return UNKNOWN, None
        if (pst.st_dev, pst.st_ino) != (d, i):
            return UNKNOWN, None  # 被替换/inode 分叉
        try:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
        except OSError:
            return UNKNOWN, None
        try:
            fst = os.fstat(fd)
            if (fst.st_dev, fst.st_ino) != (d, i):
                os.close(fd)
                return UNKNOWN, None
            os.set_inheritable(fd, False)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    return HELD, None
                return UNKNOWN, None
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        return ACQUIRED, LockHandle(fd, token, fst.st_dev, fst.st_ino, path / name)
    finally:
        os.close(dir_fd)


def _after_fork_child():
    """纯 fork 子进程：只 close 继承的 FD，绝不 LOCK_UN，并重建进程内互斥与 registry。

    多线程父进程可能在另一线程持 `_HANDLES_LOCK` 时 fork；子进程继承的是一把永远不会由
    它释放的 Lock，若不重建，子进程之后 acquire/close 会死等。这里把 registry 锁换成新的
    （已解锁）实例并清空继承的 registry；两个 FD（token 锁与清理期闸门）都只 close，不 LOCK_UN。
    """
    global _HANDLES_LOCK
    for handle in list(_OPEN_HANDLES):
        handle._closed = True
        for attr in ('fd', 'gate_fd'):
            fd = getattr(handle, attr, None)
            if fd is None:
                continue
            try:
                os.close(fd)
            except OSError:
                pass
    _OPEN_HANDLES.clear()
    _HANDLES_LOCK = threading.Lock()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork_child)
