#!/usr/bin/env python3
"""雷达落盘与发布（H1：快照先写，公开 bundle 为唯一权威提交点）。

目录（默认 <repo>/workspace/twitter-radar，workspace/ 已被 git 忽略）：
  refresh.lock                     单实例锁
  snapshots/YYYY-MM-DD/<run_id>.json  每轮原始采样，保留 30 天
  bundles/<run_id>.json            每轮派生 bundle（不可变，按 run_id 命名）
  manifest.json                    唯一切换点：run_id + bundle 文件名 + SHA256
写文件一律：同目录临时文件 → fsync → os.replace；异常时清掉临时文件。
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import tempfile
import re
import threading
import queue
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar_common import SNAPSHOT_DATE_RE, SNAPSHOT_NAME_RE, parse_utc

DEFAULT_KEEP_DAYS = 30
PUBLIC_BUNDLE_RELPATH = ("public", "twitter-radar-bundle.json")
_ACTIVE_RECEIPT_ROOT = ContextVar("radar_receipt_root", default=None)


class LockHeld(RuntimeError):
    """另一个刷新进程正持有单实例锁。"""


def workspace_root(repo_root) -> Path:
    return Path(repo_root) / "workspace" / "twitter-radar"


def public_bundle_path(repo_root) -> Path:
    return Path(repo_root).joinpath(*PUBLIC_BUNDLE_RELPATH)


def lock_path(root) -> Path:
    return Path(root) / "refresh.lock"


def manifest_path(root) -> Path:
    return Path(root) / "manifest.json"


def snapshots_dir(root) -> Path:
    return Path(root) / "snapshots"


def bundles_dir(root) -> Path:
    return Path(root) / "bundles"


def snapshot_path(root, run_id: str, started_at: str) -> Path:
    moment = parse_utc(started_at)
    day = moment.astimezone(timezone(timedelta(hours=8))).date().isoformat() if moment else ""
    if not SNAPSHOT_DATE_RE.fullmatch(day):
        raise ValueError(f"started_at 不是 ISO 日期：{started_at!r}")
    return snapshots_dir(root) / day / f"{run_id}.json"


def bundle_path(root, run_id: str) -> Path:
    return bundles_dir(root) / f"{run_id}.json"


class SingleInstanceLock:
    """整轮单实例锁（flock，进程退出自动释放）。拿不到就直接失败，不排队串档。"""

    def __init__(self, path):
        self.path = Path(path)
        self._fd = None
        self._receipt_token = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise LockHeld(f"另一个刷新进程正在运行：{self.path}") from exc
            raise
        os.ftruncate(self._fd, 0)
        os.write(self._fd, f"{os.getpid()}\n".encode())
        if self.path.name == "refresh.lock":
            self._receipt_token = _ACTIVE_RECEIPT_ROOT.set(self.path.parent)
            try:
                prune_request_receipts(self.path.parent, keep_days=None)
            except Exception:
                self.release()
                raise
        return self

    def release(self):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
                if self._receipt_token is not None:
                    _ACTIVE_RECEIPT_ROOT.reset(self._receipt_token)
                    self._receipt_token = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def encode_json(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes_atomic(path, data: bytes) -> Path:
    """同目录临时文件 + fsync + os.replace；任何异常都清掉临时文件，不留截断产物。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return path


def write_json_atomic(path, payload) -> Path:
    return write_bytes_atomic(path, encode_json(payload))


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_manifest(root):
    path = manifest_path(root)
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (ValueError, OSError):
        return None


def _contained_file(root, relative):
    """Only ordinary files below the intended root, never a symlink escape."""
    root = Path(root)
    if not isinstance(relative, str) or Path(relative).is_absolute():
        return None
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root.resolve())
        if candidate.is_symlink() or not candidate.is_file():
            return None
        return candidate
    except (OSError, ValueError):
        return None


def public_manifest(root, repo_root=None):
    repo_root = Path(repo_root) if repo_root is not None else Path(root).parents[1]
    try:
        raw = public_bundle_path(repo_root).read_bytes()
        doc = json.loads(raw)
        manifest = doc.get("manifest")
        if (not isinstance(manifest, dict) or not manifest.get("run_id")
                or manifest.get("run_id") != doc.get("run_id")):
            return None
        # bundle_sha256 is derived from authoritative public bytes; it cannot be
        # embedded into those same bytes without a self-referential hash.
        return {**manifest, "bundle_sha256": sha256_bytes(raw)}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def publish_bundle(root, repo_root, run_id: str, bundle: dict, *, started_at: str, coverage: dict,
                   now: datetime | None = None) -> dict:
    """Snapshot -> immutable version -> public COMMIT -> derived manifest index."""
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    versioned = bundle_path(root, run_id)
    snapshot = snapshot_path(root, run_id, started_at)
    # Entry always writes the real run first. Library fixtures may seed an old
    # public bundle without a snapshot; such a pointer is never used as evidence.
    snapshot_sha = sha256_bytes(snapshot.read_bytes()) if snapshot.is_file() else None
    embedded = {
        "schema_version": bundle.get("schema_version"), "run_id": run_id,
        "started_at": started_at,
        "snapshot_file": str(snapshot.relative_to(root)) if snapshot_sha else None, "snapshot_sha256": snapshot_sha,
        "bundle_file": str(versioned.relative_to(root)),
        "public_bundle": "/".join(PUBLIC_BUNDLE_RELPATH), "coverage": coverage,
        "published_at": now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    published = {**bundle, "run_id": run_id, "manifest": embedded}
    data = encode_json(published)
    digest = sha256_bytes(data)
    write_bytes_atomic(versioned, data)
    if sha256_bytes(versioned.read_bytes()) != digest:
        raise RuntimeError("bundle 校验失败")
    write_bytes_atomic(public_bundle_path(repo_root), data)
    manifest = {**embedded, "bundle_sha256": digest}
    try:
        write_json_atomic(manifest_path(root), manifest)
    except OSError:
        # The public commit already happened; never claim that it rolled back.
        print("公开 bundle 已提交，派生 manifest 索引待下一轮修复")
    return manifest


def cleanup_temporary_files(root, repo_root):
    """Sweep only this writer's exact names in explicitly owned directories."""
    root = Path(root)
    deleted = []
    directories = []
    snap_root = snapshots_dir(root)
    if snap_root.is_dir() and not snap_root.is_symlink():
        for date_dir in snap_root.iterdir():
            if (date_dir.is_symlink() or not date_dir.is_dir()
                    or not SNAPSHOT_DATE_RE.fullmatch(date_dir.name)):
                continue
            try:
                date_dir.resolve().relative_to(snap_root.resolve())
            except ValueError:
                continue
            directories.append((date_dir, r"\.\d{8}-\d{6}-[0-9a-f]{8}\.json\.[a-z0-9_]{8}\.tmp"))
    directories += [
        (bundles_dir(root), r"\.\d{8}-\d{6}-[0-9a-f]{8}\.json\.[a-z0-9_]{8}\.tmp"),
        (root, r"\.manifest\.json\.[a-z0-9_]{8}\.tmp"),
        (public_bundle_path(repo_root).parent, r"\.twitter-radar-bundle\.json\.[a-z0-9_]{8}\.tmp"),
    ]
    for directory, pattern in directories:
        if directory.is_symlink() or not directory.is_dir():
            continue
        for item in directory.iterdir():
            if item.is_symlink() or not item.is_file() or not re.fullmatch(pattern, item.name):
                continue
            try:
                item.unlink()
                deleted.append(str(item))
            except OSError:
                pass
    return deleted


def recover_publication(root, repo_root, log=print):
    removed = cleanup_temporary_files(root, repo_root)
    if removed:
        log(f"回收本模块崩溃临时文件 {len(removed)} 个")
    authority = public_manifest(root, repo_root)
    if authority is not None and read_manifest(root) != authority:
        write_json_atomic(manifest_path(root), authority)
        log("已按公开 bundle 内嵌 manifest 修复派生索引")
    return authority


def load_previous_snapshot(root, manifest=None, *, repo_root=None):
    """Ignore the legacy manifest argument: only public embedded metadata is authoritative."""
    authority = public_manifest(root, repo_root)
    if not authority or not authority.get("snapshot_sha256"):
        return None
    path = _contained_file(root, authority.get("snapshot_file"))
    if path is None:
        return None
    try:
        raw = path.read_bytes()
        if sha256_bytes(raw) != authority["snapshot_sha256"]:
            return None
        snapshot = json.loads(raw)
        return snapshot if snapshot.get("run_id") == authority["run_id"] else None
    except (ValueError, OSError, AttributeError):
        return None


def prune_snapshots(root, now: datetime, keep_days: int = DEFAULT_KEEP_DAYS):
    """按北京时间自然日保留30天；仅清理快照根内合法普通文件，跳过链接。"""
    root = Path(root)
    prune_request_receipts(root, now=now, keep_days=keep_days)
    snap_root = snapshots_dir(root)
    if not snap_root.exists() or snap_root.is_symlink():
        return {"deleted": [], "kept": 0, "skipped": []}
    cutoff = (now.astimezone(timezone(timedelta(hours=8))) - timedelta(days=keep_days)).date()
    deleted, kept, skipped = [], 0, []
    for date_dir in sorted(snap_root.iterdir()):
        if date_dir.is_symlink() or not date_dir.is_dir() or not SNAPSHOT_DATE_RE.fullmatch(date_dir.name):
            skipped.append(str(date_dir.relative_to(snap_root)))
            continue
        try:
            day = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
        except ValueError:
            skipped.append(str(date_dir.relative_to(snap_root)))
            continue
        try:
            date_dir.resolve().relative_to(snap_root.resolve())
        except ValueError:
            skipped.append(str(date_dir.relative_to(snap_root)))
            continue
        for entry in sorted(date_dir.iterdir()):
            rel = str(entry.relative_to(snap_root))
            if entry.is_symlink() or not entry.is_file() or not SNAPSHOT_NAME_RE.fullmatch(entry.name):
                skipped.append(rel)
                continue
            if day >= cutoff:
                kept += 1
                continue
            try:
                entry.unlink()
                deleted.append(rel)
            except OSError:
                skipped.append(rel)
    return {"deleted": deleted, "kept": kept, "skipped": skipped}


# A1-c receipts are deliberately separate from run/snapshot/bundle/manifest data.
_RECEIPT_TMP_RE = re.compile(r"^\.receipt\.json\.[A-Za-z0-9_-]{8}\.tmp$")


def prune_request_receipts(root, now=None, keep_days=DEFAULT_KEEP_DAYS):
    """Clean only owned run receipt files; never follow links or foreign files."""
    runs = Path(root) / "runs"
    if not runs.is_dir() or runs.is_symlink():
        return
    now = now or datetime.now(timezone.utc)
    cutoff = (now.astimezone(timezone(timedelta(hours=8))).date() - timedelta(days=max(0, int(keep_days)))
              if keep_days is not None else None)
    for day in runs.iterdir():
        if day.is_symlink() or not day.is_dir() or not SNAPSHOT_DATE_RE.fullmatch(day.name):
            continue
        try:
            old = cutoff is not None and datetime.strptime(day.name, "%Y-%m-%d").date() < cutoff
        except ValueError:
            continue
        for run_dir in day.iterdir():
            if run_dir.is_symlink() or not run_dir.is_dir() or not SNAPSHOT_NAME_RE.fullmatch(run_dir.name + ".json"):
                continue
            if not run_dir.resolve().is_relative_to(runs.resolve()):
                continue
            for item in run_dir.iterdir():
                if item.is_symlink() or not item.is_file():
                    continue
                if _RECEIPT_TMP_RE.fullmatch(item.name) or old and item.name == "receipt.json":
                    try:
                        item.unlink()
                    except OSError:
                        pass
            if old:
                try:
                    run_dir.rmdir()
                except OSError:
                    pass
        if old:
            try:
                day.rmdir()
            except OSError:
                pass


class RequestReceipt:
    """One safe row per admitted request, frozen when collection returns.

    File IO occurs outside the memory lock. A revision check before rename
    discards a slow writer, so it cannot overwrite a final deadline receipt.
    """
    def __init__(self, run_id, started_at, *, directory=None, clock=time.monotonic):
        root = _ACTIVE_RECEIPT_ROOT.get()
        self.path = None
        if directory is not None:
            self.path = Path(directory) / "receipt.json"
        elif root is not None and SNAPSHOT_NAME_RE.fullmatch(run_id + ".json"):
            moment = parse_utc(started_at)
            day = moment.astimezone(timezone(timedelta(hours=8))).date().isoformat()
            self.path = Path(root) / "runs" / day / run_id / "receipt.json"
        self.run_id, self.started_at, self.clock = run_id, started_at, clock
        self._rows, self._revision, self._closed = {}, 0, False
        self._lock = threading.Lock()
        self._finished_at = None
        self._api_calls = None
        self._pending = queue.Queue(maxsize=1)
        if self.path is not None:
            threading.Thread(target=self._writer, daemon=True, name="radar-receipt").start()

    def _writer(self):
        while True:
            item = self._pending.get()
            if item is None:
                return
            document, revision = item
            try:
                self._persist(document, revision)
            except OSError:
                # Final synchronous flush retries and propagates a persistent error.
                pass

    def _enqueue(self, item):
        if self.path is None:
            return
        while True:
            try:
                self._pending.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._pending.get_nowait()
                except queue.Empty:
                    pass

    def _document(self):
        rows = [{k: v for k, v in row.items() if not k.startswith("_")}
                for _, row in sorted(self._rows.items())]
        retried = {r["handle"] for r in rows if r["is_retry"]}
        recovered = {r["handle"] for r in rows if r["is_retry"] and r["result"] == "ok"}
        return {"run_id": self.run_id, "started_at": self.started_at,
                "finished_at": self._finished_at, "closed": self._closed,
                "api_calls": len(rows) if self._api_calls is None else self._api_calls,
                "retried": len(retried), "recovered": len(recovered), "requests": rows}

    def _persist(self, document, revision):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".receipt.json.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encode_json(document))
                stream.flush()
                os.fsync(stream.fileno())
            with self._lock:
                if revision == self._revision:
                    os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def start(self, handle, index):
        from radar_common import iso_utc
        # Target handles are trusted config, but do not serialize arbitrary URLs.
        safe_handle = str(handle) if re.fullmatch(r"[A-Za-z0-9_]{1,64}", str(handle)) else "invalid_handle"
        token = (safe_handle, index)
        with self._lock:
            if self._closed:
                return token
            self._rows[token] = {"handle": safe_handle, "request_index": index,
                "page": None, "attempt": index, "is_retry": False,
                "started_at": iso_utc(datetime.now(timezone.utc)), "finished_at": None,
                "elapsed_seconds": 0.0, "result": None, "http_status": None,
                "proxy_used": False, "timeout_seconds": None,
                "deadline_inflight": False, "_begun": self.clock()}
            self._revision += 1
            revision, document = self._revision, self._document()
        self._enqueue((document, revision))
        return token

    def event(self, event):
        token = event.get("token")
        with self._lock:
            if self._closed or token not in self._rows:
                return
            row = self._rows[token]
            # No exception text, response body, request headers or proxy URL.
            for key in ("page", "attempt", "is_retry", "started_at", "timeout_seconds", "proxy_used"):
                if key in event:
                    row[key] = event[key]
            if event.get("phase") == "finish":
                for key in ("elapsed_seconds", "result", "http_status", "finished_at"):
                    row[key] = event.get(key)
            self._revision += 1
            revision, document = self._revision, self._document()
        self._enqueue((document, revision))

    def close(self, run):
        from radar_common import iso_utc
        with self._lock:
            self._closed = True
            self._finished_at = run["finished_at"]
            self._api_calls = run["api_calls"]
            for row in self._rows.values():
                if row["finished_at"] is None:
                    row.update(result="timeout", deadline_inflight=True,
                               elapsed_seconds=max(0.0, self.clock() - row["_begun"]),
                               finished_at=iso_utc(datetime.now(timezone.utc)))
            self._revision += 1
            revision, document = self._revision, self._document()
        self._persist(document, revision)
        self._enqueue(None)
        return document
