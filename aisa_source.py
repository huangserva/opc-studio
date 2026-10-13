"""AIsa 全网情报雷达独立接入模块。

单 Key 全能网关（``https://api.aisa.one``）：一个 ``AISA_API_KEY`` 直连推特与
国内社媒（内置 TikHub 引擎）。本模块只负责取数与转成线索候选，不写线索中心。

凭证隔离铁律（红线）：
- Key 只从环境变量 ``AISA_API_KEY`` 读取，绝不写入代码、日志、SQLite、异常回执
  或前端数据。
- 脱敏只在 ``_scrub`` 一处实现，并挂在每个对外/落库出口上：取数走 ``_finalize``、
  线索走 ``to_clue_item``、状态走 ``status``、落库走 ``_remember``。新增出口时挂
  ``_finalize``/``_scrub``，不靠逐字段记忆。
- 脱敏范围是完整 Key **加上 Key 的连续片段**（前 16、后 16、中间切片等，见
  ``_credential_pattern``）：上游回显截断值也算泄漏。
- 未配置环境变量时优雅回退为不可用，任何取数失败（含解析、缓存读写）都不抛出未捕获异常。

资源上限：响应体最多 ``MAX_RESPONSE_BYTES`` 字节、单次取 ``READ_CHUNK_BYTES`` 字节、
**整次请求**（DNS/连接/TLS/状态行/响应头/chunk 长度行/正文，成功与 HTTPError 两条
路径）由单一墙钟 deadline ``MAX_TOTAL_SECONDS`` 约束；越线一律降级为安全回执。

边界如实说明（只写已被测试证明的部分）：
- 重定向：``urllib_transport`` 用自带 opener 禁掉自动跟随，301/302/303/307/308 直接按错误
  返回；跳主机/跳端口/跳协议都不会向跳转目标发第二个请求，Authorization 不会外带。
- 请求在受控 daemon 工作线程中执行，主线程 ``join(剩余时间)``；到点立即返回
  ``ResponseTooSlow``，并 ``shutdown``+``close`` 已登记/已捕获的连接，迟到的数据不写入
  返回值、不落缓存。已证明边界约为 **总预算 + 小量调度误差**（本机亚秒级预算的 9 种协议
  变体：慢响应头、慢 chunk 长度行、chunked 正文滴流、EOF 结束、HTTPError 体滴流等，
  均在预算附近返回）。
- 连接阶段（DNS/连接/TLS/状态行/响应头，urlopen 尚未返回）由自定义连接类在 connect
  成功时登记底层 socket，超时即可被关，工作线程在有界时间内退出；连续慢头/慢体超时后
  线程数与 FD 回到基线（本机 16 连测）。
- 在途后台请求（主调用已超时返回、worker 仍未退出）上限 ``MAX_LINGERING_REQUESTS``；
  超过后新请求直接降级为「AIsa 请求繁忙，稍后再试」，不再新建线程。
- 拿不到底层 socket 的注入假流仍走 ``read(n)`` + 前后 deadline 检查；其真实边界是
  「总预算 + 一次阻塞 read」，不做封顶承诺（仅测试注入路径会遇到）。

离线可测：``transport`` 可注入自定义函数，单元测试全程不打真实网络。
"""
import json
import os
import re
import hashlib
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlsplit
import socket
import sqlite3
import ssl
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

from collect_core import provenance

DEFAULT_BASE_URL = 'https://api.aisa.one'
DEFAULT_TIMEOUT = 30.0
MAX_COUNT = 100
# 凭据片段脱敏下限（复核 M3）：Key 里任意位置的连续片段，只要长度 >= 这个值都要脱敏。
# 短于它的片段不处理：6 个字符以下（例如 "sk-a"）会和普通文字撞车，脱敏反而破坏正文。
SCRUB_FRAGMENT_CHARS = 6
# 额外显式覆盖的前/后「已知截断长度」片段：日志与上游常按这些长度截断显示 Key。
SCRUB_EDGE_CHARS = (6, 8, 12, 16, 24, 32)
# n-gram 数量保护：异常长的 Key 不致于编出上万个候选串（真实网关 Key 远小于此）。
SCRUB_MAX_KEY_CHARS = 256
# 复核 M2：这个上限不是「超过就静默少洗一段」的软限，而是受支持长度的硬上限。
# 长度超过它的凭据在发第一个请求前就被拒（见 _key_config_error）：产品不接受
# 一份自己宣称保护不了、实际又静默失去中段片段保护的配置。
KEY_TOO_LONG_ERROR = (f'AIsa Key 长度异常，超过 {SCRUB_MAX_KEY_CHARS} 字符，已拒绝使用；'
                      '请检查环境变量 AISA_API_KEY 是否被拼接或污染')
# 响应体硬上限：成功与错误两条路径都只读这么多字节，防止无界内存占用。
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# 单次 read 的分块大小：保证任何一次读取都不会一次性吞下整个响应体。
READ_CHUNK_BYTES = 256 * 1024
# 传输总时限：socket 超时只约束单次网络操作，持续慢速传输仍可能无限拖下去，
# 这里给整个响应读取阶段一个绝对时限，在分块循环里逐块核对。
MAX_TOTAL_SECONDS = 30.0
# 在途后台请求上限：主调用方已超时返回、但工作线程仍未退出的请求数量上限。
# 超过此数的新请求直接安全降级（不新建线程），避免慢滴流场景下线程/FD 无界累积。
MAX_LINGERING_REQUESTS = 8

TWITTER_TWEETS_PATH = '/apis/v1/twitter/user/last_tweets'
DOUYIN_HOT_PATH = '/apis/v1/tikhub/douyin/app/v3/fetch_hot_search_list'
XHS_INSPIRATION_PATH = '/apis/v1/tikhub/xiaohongshu/app_v2/get_creator_hot_inspiration_feed'

MAX_UNWRAP_DEPTH = 8
TITLE_CLIP = 80


class ResponseTooLarge(Exception):
    """响应体超过 MAX_RESPONSE_BYTES。由 _call 统一降级为安全回执。"""


class ResponseTooSlow(Exception):
    """响应读取超过 MAX_TOTAL_SECONDS 总时限。由 _call 统一降级为安全回执。"""


class ResponseBusy(Exception):
    """在途后台请求已达上限。由 _call 统一降级为安全回执，不新建线程。"""


def _effective_timeout(value):
    """任何缺失或非法的 timeout 都回落到 DEFAULT_TIMEOUT，绝不向 urlopen 传 None。

    正数也要截到 DEFAULT_TIMEOUT 以内：单次 socket 操作的时间预算不得大于默认值，
    否则调用方传 timeout=300 就会把单次阻塞读的等待放大。
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if not seconds > 0:
        return DEFAULT_TIMEOUT
    return min(seconds, DEFAULT_TIMEOUT)


def _underlying_socket(stream):
    """沿 ``.fp`` 链找底层 socket（HTTPResponse 与 HTTPError 都能找到）。

    找不到（注入的假流、BytesIO 等）返回 None，调用方据此走退化读取路径。
    """
    node = stream
    for _ in range(6):
        raw = getattr(node, 'raw', None)
        found = getattr(raw, '_sock', None)
        if found is not None:
            return found
        nxt = getattr(node, 'fp', None)
        if nxt is None or nxt is node:
            break
        node = nxt
    return None


def _retune_socket(sock, seconds):
    """每次读取前把底层 socket 超时调到剩余预算内；无 socket 或失败就跳过。"""
    if sock is None:
        return
    try:
        sock.settimeout(seconds)
    except OSError:
        pass


# ── 连接阶段登记（超时后能真正关掉 urlopen 未返回时的连接）─────────────────
# urlopen 返回前（DNS/连接/TLS/状态行/响应头阶段）holder 里既没有响应对象也没有
# socket，_abort 无从下手，工作线程只能等自身 socket 超时；慢滴流下每个字节都落在
# socket 超时内，线程会长期滞留。这里在 connect 成功那一刻把底层 socket 记进
# 「本线程」的 holder，超时即可 shutdown+close 让阻塞 recv 立即出错。
#
# 用线程局部 + 透传包装实现，不改默认 opener（避免影响其他模块的代理/重定向语义）：
# 只有本请求 worker 线程会设置 holder，其他线程/其他模块完全走原函数，行为不变。
_CONNECT_HOLDER = threading.local()
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_WRAP_SOCKET = ssl.SSLContext.wrap_socket


def _record_connected_socket(sock):
    holder = getattr(_CONNECT_HOLDER, 'holder', None)
    if holder is not None and sock is not None:
        holder['sock'] = sock


def _registering_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                                   source_address=None, **kwargs):
    """包装 socket.create_connection：连接成功后把底层 socket 记进本线程 holder。

    对未登记 holder 的线程（其他模块、其他请求）完全透传。
    """
    sock = _REAL_CREATE_CONNECTION(address, timeout, source_address, **kwargs)
    _record_connected_socket(sock)
    return sock


def _registering_wrap_socket(context, sock, *args, **kwargs):
    """包装 SSLContext.wrap_socket：TLS 包装后登记真正在用的 SSLSocket。

    裸 socket 在 wrap 时已被 detach，只有登记包装后的对象，HTTPS 慢响应头阶段才能被关。
    """
    wrapped = _REAL_WRAP_SOCKET(context, sock, *args, **kwargs)
    _record_connected_socket(wrapped)
    return wrapped


socket.create_connection = _registering_create_connection
ssl.SSLContext.wrap_socket = _registering_wrap_socket


# 在途请求计数：已获准但 worker 尚未退出的请求数（活动 + 主调用方已超时返回的迟到
# worker 都算）。准入在新建 worker 之前用同一把锁原子检查并预占，worker 的 finally 释放。
_LINGERING_LOCK = threading.Lock()
_LINGERING = {'count': 0}


def _lingering_count():
    with _LINGERING_LOCK:
        return _LINGERING['count']


def _reserve_request():
    """原子检查并预占一个在途名额；已满返回 False（调用方据此直接降级，不建线程）。"""
    with _LINGERING_LOCK:
        if _LINGERING['count'] >= MAX_LINGERING_REQUESTS:
            return False
        _LINGERING['count'] += 1
        return True


def _release_request():
    """释放一个在途名额；worker 无论正常、异常还是迟到完成都在 finally 调用一次。"""
    with _LINGERING_LOCK:
        _LINGERING['count'] = max(0, _LINGERING['count'] - 1)


def _read_capped(stream, deadline=None, holder=None):
    """循环读取：每次只取「当前可用数据」，累计超上限或越过总时限立刻中断。

    两条路径（成功响应 / HTTPError）都走这里，因此「读多少字节」只在这一处决定。

    每次读取前把 socket 超时收到剩余预算内（首轮用调用方算好的初始剩余，后续轮用上一轮
    读后观测值），读后再核对 deadline：单次读取不会晚于 deadline 返回。能拿到底层 socket
    时用 ``read1``（一次最多一个底层 recv）；拿不到 socket 时退化为 ``read(n)``，只用读后的
    deadline 检查兜底（其真实边界见模块 docstring）。每轮只在读后读一次时钟。
    """
    sock = _underlying_socket(stream)
    if holder is not None and sock is not None:
        # 不覆盖已登记的真实 socket：拿不到 socket 的假流不该把登记值清成 None。
        holder['sock'] = sock
    reader = getattr(stream, 'read1', None) if sock is not None else None
    single = reader is not None
    if reader is None:
        reader = stream.read
    chunks = []
    total = 0
    # 首轮读前的剩余预算：由调用方传入，避免为首轮 retune 再读一次时钟。
    budget = holder.get('remaining') if holder is not None else None
    while True:
        if deadline is not None:
            if budget is None:
                budget = deadline - time.monotonic()
            if budget <= 0:
                raise ResponseTooSlow(f'响应传输超过 {MAX_TOTAL_SECONDS} 秒总时限')
            _retune_socket(sock, min(budget, DEFAULT_TIMEOUT))
        try:
            chunk = reader(READ_CHUNK_BYTES)
        except (socket.timeout, TimeoutError):
            # 单次读取超时：若已越 deadline 就是总时限问题，否则如实上抛网络异常。
            if deadline is not None and time.monotonic() >= deadline:
                raise ResponseTooSlow(f'响应传输超过 {MAX_TOTAL_SECONDS} 秒总时限') from None
            raise
        if deadline is not None:
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise ResponseTooSlow(f'响应传输超过 {MAX_TOTAL_SECONDS} 秒总时限')
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ResponseTooLarge(f'响应体超过 {MAX_RESPONSE_BYTES} 字节上限')
        chunks.append(chunk)
        # read(n) 短块即流尾；read1 短块只是「本次可用数据就这么多」，只有空块才是尾。
        if not single and len(chunk) < READ_CHUNK_BYTES:
            break
    return b''.join(chunks)


def _force_close(target):
    """先 ``shutdown`` 再 ``close``：让本地阻塞 recv 与对端立刻出错，worker 尽快退出。

    不做 socket 探测（不调用 ``_underlying_socket``）：超时路径上只处理已登记对象，
    避免额外副作用；对响应对象只 ``close()``（其 close 会一并关掉底层 socket）。
    """
    shutdown = getattr(target, 'shutdown', None)
    if callable(shutdown):
        try:
            shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        target.close()
    except Exception:  # noqa: BLE001 - 关闭失败不影响已定的超时结论
        pass


def _abort(holder):
    """超时后关闭已登记的底层 socket / 已捕获的响应，让阻塞 recv 尽快出错。

    关闭必须在独立 daemon 线程里做：某些平台上 `close()` 会阻塞到另一线程的
    recv 返回，若在主线程关闭就会把「到点即返回」拖成阻塞。这里 fire-and-forget，
    主调用方立即返回 ResponseTooSlow。
    """
    targets = []
    for key in ('sock', 'stream'):
        target = holder.get(key)
        if target is not None and target not in targets:
            targets.append(target)
    if not targets:
        return

    def closer():
        for target in targets:
            _force_close(target)

    threading.Thread(target=closer, name='aisa-abort', daemon=True).start()


class RedirectRefused(error.HTTPError):
    """3xx 跳转：不跟随，也不向跳转目标发第二个请求。"""


class _RefuseRedirect(request.HTTPRedirectHandler):
    """把标准库的自动跟随重定向换成「立即按错误返回」。

    默认 ``urlopen`` 会跟随跳主机、跳端口、跳协议的 301/302/303/307/308，并且
    ``redirect_request`` 只剔掉 Content-* 请求头——**Authorization 原样带到跳转目标**。
    单 Key 网关不允许这种外带：只要网关（或中间设备）回一个跳转，凭据就送到别人那里。

    ``build_opener`` 见到本类实例就不再装默认的 ``HTTPRedirectHandler``；两个处理器万一
    共存时，较低的 ``handler_order``（新旧属性名都写）也保证先调我们。
    """

    handler_order = 400
    _handler_order = 400

    def _refuse(self, req, fp, code, msg, headers):
        # 先关掉原响应再抛错：任何路径都不会向 Location 目标发出第二个请求。
        if fp is not None:
            try:
                fp.close()
            except Exception:  # noqa: BLE001 - 关闭失败不改变「不跟随」的结论
                pass
        raise RedirectRefused(req.full_url, code, msg, headers, None)

    http_error_301 = http_error_302 = http_error_303 = http_error_307 = http_error_308 = _refuse


# 产品自己的 opener：``request.urlopen`` 的全局 opener 会跟随重定向，这里换成不跟随的。
_NO_REDIRECT_OPENER = request.build_opener(_RefuseRedirect())


def _open(req, timeout):
    """产品唯一的 HTTP 发起点：走不跟随重定向的 opener。测试在这里注入网络层。"""
    return _NO_REDIRECT_OPENER.open(req, timeout=timeout)


def _perform_request(method, url, headers, body, socket_timeout, deadline, holder):
    """真正发请求 + 读响应体。在受控工作线程里执行，成功与 HTTPError 都覆盖。

    请求期间把本线程的 holder 登记进 ``_CONNECT_HOLDER``，让自定义连接类在 connect
    成功那一刻就把底层 socket 记进 holder（urlopen 尚未返回时的响应头阶段也能被关）。
    """
    data = body.encode('utf-8') if body is not None else None
    req = request.Request(url, data=data, headers=dict(headers), method=method)
    _CONNECT_HOLDER.holder = holder
    try:
        try:
            resp = _open(req, timeout=socket_timeout)
        except error.HTTPError as exc:
            holder['stream'] = exc
            try:
                payload = _read_capped(exc, deadline, holder).decode('utf-8', 'replace')
            except (ResponseTooLarge, ResponseTooSlow):
                raise
            except Exception:  # noqa: BLE001 - 错误响应体读失败不影响状态码
                payload = ''
            return exc.code, payload
        holder['stream'] = resp
        try:
            raw = _read_capped(resp, deadline, holder)
            return resp.status, raw.decode('utf-8', 'replace')
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        _CONNECT_HOLDER.holder = None


def urllib_transport(method, url, headers, body, timeout):
    """默认 transport：标准库 urllib.request，但走**不跟随重定向**的 opener。返回 (status, text)。

    非 2xx 不抛异常，返回状态码让上层统一降级；网络错误仍抛出，由上层捕获。

    重定向边界（复核 H1）：301/302/303/307/308 一律不跟随，直接按该状态码返回错误回执；
    跳主机、跳端口、跳协议都不会向 Location 目标发出第二个请求，鉴权头也就不会外带。
    其他 3xx（300/304/305/306）没有跳转语义，由默认错误处理抛 HTTPError，同样不触达目标。

    整次请求（DNS/连接/TLS/状态行/响应头/chunk 长度行/正文）由**一个**墙钟 deadline
    约束：请求在受控 daemon 工作线程里执行，主线程 `join(剩余时间)`，到点立即返回
    ResponseTooSlow 并 shutdown+close 已登记/已捕获的连接（含 urlopen 未返回的响应头
    阶段）；迟到的数据不会写入返回值，也不会触发缓存。准入在新建线程之前原子预占
    在途名额（活动 + 迟到 worker 合计上限 ``MAX_LINGERING_REQUESTS``），满则直接
    ResponseBusy，不新建线程；名额在任何路径都由 worker 的 finally 释放。
    传给 urlopen 的 timeout 永远是一个正数，且被截到总时限剩余时间以内。
    """
    if not _reserve_request():
        raise ResponseBusy('AIsa 请求繁忙，稍后再试')
    seconds = _effective_timeout(timeout)
    # 固定总 deadline：不被调用参数放大（timeout=300 也不会放宽到 300 秒）。
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    remaining = max(0.0, deadline - time.monotonic())
    socket_timeout = min(seconds, remaining) if remaining > 0 else seconds
    holder = {}
    # 初始剩余预算交给读取层做首轮 socket 超时收紧，避免为首轮 retune 再读一次时钟。
    holder['remaining'] = remaining
    result = {}
    state = {'finished': False, 'finished_at': None}

    def worker():
        try:
            result['value'] = _perform_request(method, url, headers, body,
                                               socket_timeout, deadline, holder)
        except BaseException as exc:  # noqa: BLE001 - 原样带回主线程再决定降级
            result['error'] = exc
        finally:
            # 完成时刻在真实完成点（解码/关闭之后）单独读模块时钟，不复用读取阶段观测。
            finished_at = time.monotonic()
            with _LINGERING_LOCK:
                state['finished_at'] = finished_at
                state['finished'] = True
                _LINGERING['count'] = max(0, _LINGERING['count'] - 1)

    # 预占之后、worker 接管名额之前的整段（准备、构造、start）都在释放保护区内：
    # 名额只能有一个归还责任者——start 成功前由此处归还，start 成功后由 worker finally 归还。
    try:
        thread = threading.Thread(target=worker, name='aisa-transport', daemon=True)
        thread.start()
    except BaseException:
        # 构造或 start 失败：名额必须当场归还，不能泄漏。
        _release_request()
        raise
    thread.join(timeout=remaining if remaining > 0 else 0.0)
    with _LINGERING_LOCK:
        finished = state['finished']
        finished_at = state['finished_at']
    if not finished:
        # 到点即判超时：不再读取任何迟到数据，也不落缓存。
        _abort(holder)
        raise ResponseTooSlow(f'响应传输超过 {MAX_TOTAL_SECONDS} 秒总时限')
    if finished_at is not None and finished_at >= deadline:
        # 期限附近完成竞态：恰好在 deadline 或更晚完成的结果一律丢弃，不当成功返回。
        _abort(holder)
        raise ResponseTooSlow(f'响应传输超过 {MAX_TOTAL_SECONDS} 秒总时限')
    error = result.get('error')
    if error is not None:
        raise error
    return result['value']


def _text(value):
    if value is None:
        return ''
    return value.strip() if isinstance(value, str) else str(value).strip()


def _int(value, default=0):
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        # 1e309 经 json.loads 变 inf，int(inf) 抛 OverflowError，必须一起降级。
        return default


def _clip(text, limit):
    text = _text(text)
    return text if len(text) <= limit else text[:limit] + '…'


def _safe_id(value):
    """编号里不能带冒号（provenance 会拒绝），统一替换成连字符并限长。"""
    text = _text(value).replace(':', '-').replace('/', '-')
    return text[:200]


def _bounded_count(value, default=20):
    number = _int(value, default)
    if number <= 0:
        return default
    return min(number, MAX_COUNT)


def _heat_label(value):
    number = _int(value)
    if number >= 10000:
        # 整数除法，绝不转 float：超大热度（如 10**1000）不会溢出。
        return f'{number // 10000}万'
    return str(number)


def _first_list(payload, keys, depth=0):
    """从常见包裹结构里挖出第一个列表。找不到返回 None。深度受限，防递归爆栈。"""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict) or depth >= MAX_UNWRAP_DEPTH:
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for container in ('data', 'result', 'response'):
        nested = payload.get(container)
        if isinstance(nested, (dict, list)):
            found = _first_list(nested, keys, depth + 1)
            if found is not None:
                return found
    return None


def _utc(value):
    try:
        text = str(value or '')
        try:
            stamp = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            stamp = parsedate_to_datetime(text)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return ''
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return ''


def _web_url(value, hosts=()):
    text = _text(value)[:2000]
    try:
        parts = urlsplit(text)
        return text if (parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
                    and (not hosts or any(parts.hostname == host or parts.hostname.endswith('.' + host) for host in hosts))) else ''
    except ValueError:
        return ''


def _container(payload, field):
    node = payload
    for _ in range(MAX_UNWRAP_DEPTH):
        if not isinstance(node, dict):
            return {}
        if field in node:
            return node
        node = node.get('data')
    return {}


def _media_urls(row):
    raw = row.get('media_urls')
    if not isinstance(raw, list):
        return []
    urls = []
    for entry in raw:
        if isinstance(entry, str) and entry:
            urls.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get('url'), str) and entry['url']:
            urls.append(entry['url'])
    return urls


def _key_config_error(key):
    '''凭据本身不合法时给出中文配置错误；空值不算配置错误（按未配置优雅降级）。

    复核 M2：长度超过 ``SCRUB_MAX_KEY_CHARS`` 的凭据一律拒绝使用。选拒绝使用而不是
    「放宽滑窗继续用」的理由：片段脱敏的候选数量随 Key 长度线性增长，滑窗再放大既
    拖慢每一个出口的脱敏，也没人验证过真实网关会发这么长的 Key；与其接受这份配置后
    静默失去中段保护，不如在第一个请求前就报错。调用方据此不发任何请求，状态显示配置错误。
    返回空串表示凭据长度合法。
    '''
    if key and len(key) > SCRUB_MAX_KEY_CHARS:
        return KEY_TOO_LONG_ERROR
    return ''


def _credential_pattern(key):
    """把 Key 编成「完整值 + 任意位置的连续片段 + 前/后已知长度片段」的匹配式。

    策略（复核 M3）：
    - 完整 Key 一定脱敏；
    - Key 里**任意位置**、长度 ``>= SCRUB_FRAGMENT_CHARS`` 的连续切片都要脱敏——
      实现上取 Key 的全部 ``SCRUB_FRAGMENT_CHARS`` 长度 n-gram，任何更长的连续片段
      必然包含其中一个，所以一条匹配式就覆盖了前 16、后 16、中间切片等；
    - 再显式补上前/后 ``SCRUB_EDGE_CHARS`` 这些「已知截断显示」长度（短于 n-gram 下限
      时才有额外作用），以及短 Key（长度不足 n-gram 下限）的整段前后缀；
    - 短于下限的片段不处理，避免把普通正文误伤。
    - 长度超过 ``SCRUB_MAX_KEY_CHARS`` 的 Key 不会经由这条链路对外发请求：
      ``_key_config_error`` 已在第一个请求前拒绝使用。这里仍保留完整值与首尾已知长度
      片段，作为「这份字符串从别处涌进正文」时的兼底，但不为超长 Key 扩大滑窗。
    只编译一次并按 Key 缓存；匹配到就整段换成 ``***``。
    """
    if not key:
        return None
    pieces = {key}
    span = min(SCRUB_FRAGMENT_CHARS, len(key))
    if len(key) <= SCRUB_MAX_KEY_CHARS:
        pieces.update(key[start:start + span]
                      for start in range(len(key) - span + 1))
    for length in SCRUB_EDGE_CHARS:
        if 0 < length < len(key):
            pieces.add(key[:length])
            pieces.add(key[-length:])
    ordered = sorted(pieces, key=lambda piece: (-len(piece), piece))
    return re.compile('|'.join(re.escape(piece) for piece in ordered))


class AIsaSource:
    """AIsa 网关取数器。所有失败都降级为空结果，不抛出未捕获异常。"""

    platform = 'aisa'

    def __init__(self, store=None, transport=None, api_key=None, base_url=DEFAULT_BASE_URL):
        self.store = store
        self.transport = transport or urllib_transport
        self.api_key = api_key if api_key is not None else os.environ.get('AISA_API_KEY')
        # 超长凭据（复核 M2）：不扩大滑窗、不发请求，只把实例标成配置错误；
        # _call / status / 适配器 start 都会把这份中文错误回执给出去。
        self.config_error = _key_config_error(self.api_key)
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip('/')
        self.timeout = DEFAULT_TIMEOUT
        self.last_error = ''
        self.cache_error = ''
        # 脱敏匹配式按 Key 缓存：``_scrub`` 每个出口都会调用，不能每次重新编译。
        self._pattern_key = None
        self._pattern = None
        self._db_path = self._resolve_db_path(store)
        if self._db_path is not None:
            self._init_db()

    # ── 缓存库 ─────────────────────────────────────────────────────────────
    @staticmethod
    def _resolve_db_path(store):
        workspace = getattr(store, 'workspace', None) if store is not None else None
        if not workspace:
            return None
        try:
            root = Path(workspace) / 'aisa'
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(root, 0o700)
        except OSError:
            # 工作区不可写：缓存整体停用，取数本身照常降级运行，不让构造失败。
            return None
        return root / 'aisa.db'

    def _init_db(self):
        try:
            with closing(sqlite3.connect(self._db_path, timeout=30)) as connection:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.executescript('''
                    CREATE TABLE IF NOT EXISTS items(
                        producer_key TEXT PRIMARY KEY, platform TEXT NOT NULL,
                        item_id TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                ''')
                connection.commit()
            self._secure()
        except Exception as exc:  # noqa: BLE001 - 建库失败只降级缓存，不影响取数与其他源
            self._db_path = None
            self._cache_failed(exc)

    def _secure(self):
        for suffix in ('', '-wal', '-shm'):
            try:
                os.chmod(str(self._db_path) + suffix, 0o600)
            except FileNotFoundError:
                pass

    @contextmanager
    def db(self, write=False):
        if self._db_path is None:
            raise RuntimeError('AIsa 缓存未配置工作区')
        connection = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        if not write:
            connection.execute('PRAGMA query_only=ON')
        try:
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
            self._secure()

    def _cache_failed(self, exc):
        """缓存故障只留安全摘要：异常类型名进回执，异常文本本身绝不外传。"""
        self.cache_error = type(exc).__name__
        self.last_error = self._redact(f'缓存访问失败：{type(exc).__name__}')

    def _remember(self, items):
        """按 producer_key 幂等落库，返回新入库条数。落库前再脱敏一次作兜底。"""
        if self._db_path is None or not items:
            return 0
        stamp = datetime.now(timezone.utc).isoformat()
        added = 0
        with self.db(write=True) as connection:
            for item in items:
                item = self._scrub(item)
                platform = item.get('platform') or ''
                item_id = self._item_id(item, platform)
                if not item_id:
                    continue
                key = f'aisa:{platform}:{item_id}'
                payload = self._redact(json.dumps(item, ensure_ascii=False))
                cursor = connection.execute(
                    'INSERT OR IGNORE INTO items(producer_key,platform,item_id,captured_at,payload)'
                    ' VALUES(?,?,?,?,?)',
                    (self._redact(key), self._redact(platform), self._redact(item_id), stamp, payload))
                added += cursor.rowcount
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('last_synced',?)"
                ' ON CONFLICT(key) DO UPDATE SET value=excluded.value', (stamp,))
        return added

    def _finalize(self, items):
        """取数的唯一出口：统一脱敏 + 幂等落库，缓存失败不影响本次返回与其他源。"""
        safe = [self._scrub(item) for item in items]
        try:
            self._remember(safe)
        except Exception as exc:  # noqa: BLE001 - 缓存故障不得影响其他源与本次数据
            self._cache_failed(exc)
        return safe

    def count(self):
        if self._db_path is None:
            return 0
        try:
            with self.db() as connection:
                return connection.execute('SELECT COUNT(*) FROM items').fetchone()[0]
        except Exception as exc:  # noqa: BLE001 - 读缓存失败降级为 0，不外抛
            self._cache_failed(exc)
            return 0

    def last_synced(self):
        if self._db_path is None:
            return ''
        try:
            with self.db() as connection:
                row = connection.execute("SELECT value FROM meta WHERE key='last_synced'").fetchone()
        except Exception as exc:  # noqa: BLE001 - 读缓存失败降级为空，不外抛
            self._cache_failed(exc)
            return ''
        return self._redact(row['value']) if row else ''

    # ── 网关调用 ───────────────────────────────────────────────────────────
    def _headers(self):
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if self.api_key and not self.config_error:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    def _scrub_pattern(self):
        key = self.api_key
        if not key:
            return None
        if self._pattern_key != key:
            self._pattern_key = key
            self._pattern = _credential_pattern(key)
        return self._pattern

    def _scrub(self, value):
        """递归脱敏任意结构里的 Key 明文**及其片段**。所有对外/落库出口统一走这里。

        只替换完整 Key 不够：上游或日志可能回显截断值（前 16、后 16、中间切片），
        片段同样算凭据泄漏。片段策略见 ``_credential_pattern``。
        """
        pattern = self._scrub_pattern()
        if pattern is None:
            return value
        if isinstance(value, str):
            return pattern.sub('***', value)
        if isinstance(value, list):
            return [self._scrub(entry) for entry in value]
        if isinstance(value, tuple):
            return tuple(self._scrub(entry) for entry in value)
        if isinstance(value, dict):
            return {self._scrub(k): self._scrub(v) for k, v in value.items()}
        return value

    def _redact(self, text):
        """任何要对外或落库的文本都先脱敏，防止 Key 泄漏。"""
        return self._scrub(str(text))

    def _call(self, method, path, body=None):
        """返回 (ok, data)。失败只记 last_error，绝不抛出。"""
        if self.config_error:
            # 超长凭据：在调用 transport 之前就返回，一个字节也不往外发。
            self.last_error = self.config_error
            return False, None
        if not self.api_key:
            self.last_error = '未配置 AISA_API_KEY 环境变量'
            return False, None
        url = self.base_url + path
        text = json.dumps(body, ensure_ascii=False) if body is not None else None
        try:
            status, raw = self.transport(method, url, self._headers(), text, self.timeout)
        except ResponseTooLarge:
            self.last_error = f'AIsa 响应体超过 {MAX_RESPONSE_BYTES} 字节上限，已丢弃'
            return False, None
        except ResponseTooSlow:
            self.last_error = f'AIsa 响应超过 {MAX_TOTAL_SECONDS} 秒总时限，已中断'
            return False, None
        except ResponseBusy:
            self.last_error = 'AIsa 请求繁忙，稍后再试'
            return False, None
        except Exception as exc:  # noqa: BLE001 - 超时/网络异常一律降级
            self.last_error = self._redact(f'网络异常：{type(exc).__name__}')
            return False, None
        code = _int(status, -1)
        if 300 <= code < 400:
            # 跳转一律按错误处理：不跟随，也就不会把 Authorization 带到第二个地址。
            self.last_error = self._redact(f'AIsa 返回跳转（{code}），已拒绝跟随重定向，请核对网关地址')
            return False, None
        if code in (401, 403):
            self.last_error = self._redact(f'AIsa 鉴权失败（{code}）')
            return False, None
        if code == 429:
            self.last_error = 'AIsa 限流（429），请稍后重试'
            return False, None
        if not 200 <= code < 300:
            self.last_error = self._redact(f'AIsa 返回异常状态 {code}')
            return False, None
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError, RecursionError):
            # 深层嵌套 JSON 会触发 RecursionError，同样归为不可解析。
            self.last_error = 'AIsa 返回非 JSON 数据'
            return False, None
        if isinstance(data, dict):
            code = data.get('code')
            inner = data.get('data')
            if (code is not None and str(code) not in ('0', '200')
                    or data.get('status') in ('error', 'failed')
                    or isinstance(inner, dict) and (inner.get('success') is False
                        or inner.get('status_code') not in (None, 0))):
                self.last_error = 'AIsa 接口返回失败状态，请稍后核对'
                return False, None
        self.last_error = ''
        return True, data

    # ── 三大取数方法 ───────────────────────────────────────────────────────
    def get_twitter_tweets(self, username, count=20):
        username = _text(username).lstrip('@')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', username):
            self.last_error = '推特账号格式无效'
            return []
        limit = _bounded_count(count)
        ok, data = self._call('GET', TWITTER_TWEETS_PATH + '?' + urlencode({'userName': username}))
        if not ok:
            return []
        rows = _first_list(data, ('tweets', 'list', 'items', 'results'))
        if rows is None:
            self.last_error = '推特接口未返回推文列表'
            return []
        items = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            tweet_id = _safe_id(row.get('tweet_id') or row.get('id') or row.get('id_str'))
            if not tweet_id:
                continue
            items.append({
                'platform': 'twitter', 'username': username, 'tweet_id': tweet_id,
                'text': _text(row.get('text') or row.get('full_text')),
                'created_at': _utc(row.get('createdAt') or row.get('created_at')),
                'likes': _int(row.get('likeCount', row.get('likes', row.get('favorite_count')))),
                'retweets': _int(row.get('retweetCount', row.get('retweets', row.get('retweet_count')))),
                'replies': _int(row.get('replyCount', row.get('reply_count'))),
                'views': _int(row.get('viewCount', row.get('view_count'))),
                'url': _web_url(row.get('url') or row.get('twitterUrl'), ('x.com', 'twitter.com')),
                'link_label': '查看原帖', 'media_urls': _media_urls(row),
            })
        if rows and not items:
            self.last_error = '推特返回内容格式无法解析'
        return self._finalize(items)

    def fetch_douyin_hot_searches(self, limit=20):
        limit = _bounded_count(limit)
        ok, data = self._call('GET', DOUYIN_HOT_PATH + '?board_type=0')
        if not ok:
            return []
        rows = _first_list(data, ('word_list', 'list', 'items', 'results'))
        if rows is None:
            self.last_error = '抖音接口未返回实时热词列表'
            return []
        container = _container(data, 'word_list')
        share = container.get('share_info') or {}
        url = _web_url(share.get('share_url'), ('douyin.com', 'iesdouyin.com')) if isinstance(share, dict) else ''
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            word = _text(row.get('word') or row.get('sentence') or row.get('title'))
            if not word:
                continue
            rank = row.get('position', row.get('rank'))
            rank = _int(rank, 0) if rank is not None else None
            items.append({
                'platform': 'douyin', 'word': word,
                'sentence_id': _safe_id(row.get('sentence_id') or row.get('id')),
                'hot_value': _int(row.get('hot_value')), 'view_count': _int(row.get('view_count')),
                'rank': rank if rank and rank > 0 else None, 'label': _text(row.get('label')),
                'url': _web_url(row.get('url'), ('douyin.com', 'iesdouyin.com')) or url,
                'link_label': '查看榜单（非单条原帖）', 'created_at': '',
            })
        items.sort(key=lambda entry: (-entry['hot_value'], entry['rank'] or MAX_COUNT + 1))
        # 非空却一条都解析不出来 = 上游换了结构，不能当成「今天 0 条热词」的成功空结果；
        # 合法的空 word_list 走不到这里（rows 为空），仍按 0 条成功展示（复核 M2）。
        if rows and not items:
            self.last_error = '抖音返回内容格式无法解析'
        return self._finalize(items[:limit])

    def fetch_xiaohongshu_inspirations(self, limit=20):
        limit = _bounded_count(limit)
        ok, data = self._call('GET', XHS_INSPIRATION_PATH)
        if not ok:
            return []
        rows = _first_list(data, ('inspirations', 'list', 'items', 'results'))
        if rows is None:
            self.last_error = '小红书接口未返回灵感列表'
            return []
        items = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            title = _text(row.get('title'))
            topic_id = _safe_id(row.get('topic_id') or row.get('id'))
            if not topic_id:
                continue
            items.append({
                'platform': 'xiaohongshu', 'title': title, 'topic_id': topic_id,
                'hot_id': _safe_id(row.get('hot_id')),
                'heat_score': _int(row.get('score', row.get('heat_score'))),
                'score_text': _text(row.get('score_text')),
                'view_count': _int(row.get('view_count')),
                'url': '', 'created_at': '',
                'deeplink': _text(row.get('deeplink'))[:4000],
                'link_label': '仅 App 创作入口，无公开原文链接',
            })
        if rows and not items:
            self.last_error = '小红书返回内容格式无法解析'
        return self._finalize(items)

    # ── 线索转化 ───────────────────────────────────────────────────────────
    @staticmethod
    def _item_id(item, platform):
        if platform == 'twitter':
            return _safe_id(item.get('tweet_id'))
        if platform == 'douyin':
            return _safe_id(item.get('sentence_id')) or _safe_id(item.get('word'))
        if platform == 'xiaohongshu':
            return _safe_id(item.get('topic_id') or item.get('title'))
        return ''

    def to_clue_item(self, item, platform=None):
        """把素材转成工作台线索候选字典。producer_key 用于幂等去重。"""
        if not isinstance(item, dict):
            raise ValueError('素材须为字典')
        platform = platform or item.get('platform') or ''
        item_id = self._item_id(item, platform)
        if not item_id:
            raise ValueError('素材缺少编号，无法转化为线索')
        if platform == 'twitter':
            username = _text(item.get('username'))
            title = f'【推特】@{username}: {_clip(item.get("text"), TITLE_CLIP)}'
            summary = (f'@{username} 于 {_text(item.get("created_at")) or "时间未知"} 发布；'
                       f'点赞 {_int(item.get("likes"))} · 转发 {_int(item.get("retweets"))}；'
                       f'{_clip(item.get("text"), 3000)}')
        elif platform == 'douyin':
            word = _text(item.get('word'))
            title = f'【抖音全网热搜】#{word} (热度 {_heat_label(item.get("hot_value"))})'
            summary = (f'排名 {item.get("rank") if item.get("rank") else "未提供（特殊榜项）"}；热度 {_int(item.get("hot_value"))}；'
                       f'标签 {_text(item.get("label")) or "无"}')
        elif platform == 'xiaohongshu':
            title = f'【小红书灵感】{_text(item.get("title"))} (热度 {_int(item.get("heat_score"))})'
            summary = (f'话题 {_text(item.get("topic_id"))}；热度 {_int(item.get("heat_score"))}；'
                       f'{_text(item.get("score_text")) or "热度文案未提供"}；'
                       f'{_text(item.get("link_label"))}')
        else:
            raise ValueError('未知平台，无法转化为线索')
        clue = {
            'title': title,
            'summary': summary,
            'categories': ['topics'],
            'heat': '',
            'producer_key': f'aisa:{platform}:{item_id}',
            'provenance': provenance('aisa', platform, item_id),
        }
        return self._scrub(clue)

    # ── 状态 ───────────────────────────────────────────────────────────────
    def status(self):
        """状态出口也走统一脱敏；缓存读失败已在 count/last_synced 内部降级。"""
        detail = (self.config_error or 'AIsa 雷达网关就绪') if self.api_key else '未配置 AISA_API_KEY 环境变量'
        return self._scrub({
            'available': bool(self.api_key) and not self.config_error,
            'detail': detail,
            'last_synced': self.last_synced(),
            'count': self.count(),
            'cache_error': self.cache_error,
        })
