#!/usr/bin/env python3
"""AIsa 推特取数客户端（H1：把采样状态分清楚）。

状态分类：ok / empty（真的没帖）/ http_error / business_error（HTTP 200 但业务失败）/
timeout / parse_error / deadline / partial（首页有效但翻页不完整）。每个账号返回状态、页数、实际调用次数与本次成功响应时间。
凭证只进请求头；错误文本一律脱敏，绝不带出 Key。
"""
from __future__ import annotations

import json
import errno
import random
import re
import http.client
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from radar_common import (
    STATUS_BUSINESS, STATUS_DEADLINE, STATUS_EMPTY, STATUS_HTTP, STATUS_OK,
    STATUS_PARSE, STATUS_TIMEOUT, STATUS_PARTIAL, normalize_handle, published_at_of, iso_utc,
)

API_URL = "https://api.aisa.one/apis/v1/twitter/user/last_tweets"
MAX_RESPONSE_BYTES = 4_194_304
DEFAULT_TIMEOUT = 20.0
MAX_PAGES = 2
PAGE2_WINDOW_HOURS = 24.0
PAGE2_TRIGGER_INDEX = 19  # 第 20 条
CURSOR_PARAM = "cursor"


def build_url(handle: str, cursor: str | None = None, cursor_param: str = CURSOR_PARAM) -> str:
    url = f"{API_URL}?userName={urllib.parse.quote(str(handle))}"
    if cursor:
        url += f"&{cursor_param}={urllib.parse.quote(str(cursor))}"
    return url


def sanitize_error(message: str, api_key: str = "") -> str:
    text = str(message or "").replace("\n", " ").replace("\r", " ").strip()
    if api_key:
        text = text.replace(api_key, "[redacted]")
    text = re.sub(r"(?i)(?:https?|socks[45]h?)://[^/\s]*@", "[redacted-proxy]@", text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^,;\s]+", r"\1[redacted]", text)
    if len(api_key) >= 16:
        text = text.replace(api_key[:12], "[redacted]").replace(api_key[-8:], "[redacted]")
    return text[:200]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_opener(url: str, api_key: str, timeout: float):
    """真实网络：返回 (payload, http_status)。异常向上抛，由 fetch_account 分类。"""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "api.aisa.one":
        raise ValueError("鉴权仅允许 AIsa 网关")
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "article-studio-radar/1.0"},
    )
    with urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout) as res:
        status = getattr(res, "status", None) or res.getcode()
        body = res.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("响应体超过大小限制")
    return json.loads(body.decode("utf-8")), status


def business_error(payload) -> str | None:
    """HTTP 200 里的业务失败：{"code":401,...} 这类必须算失败，不算成功采样。"""
    if not isinstance(payload, dict):
        return "响应不是 JSON 对象"
    if payload.get("success") is False:
        return "业务 success=false"
    code = payload.get("code")
    if code is not None:
        from decimal import Decimal, InvalidOperation
        try:
            zero = not isinstance(code, bool) and Decimal(str(code).strip()) == 0
        except (InvalidOperation, ValueError):
            zero = False
        if not zero:
            return f"业务 code={code}"
    status = payload.get("status")
    if status is not None and str(status).lower() not in ("success", "ok"):
        return f"业务 status={status}"
    data = payload.get("data")
    if data is None:
        return "响应缺少 data"
    if not isinstance(data, dict):
        return "data 不是对象"
    tweets = data.get("tweets")
    if "tweets" not in data or not isinstance(tweets, list):
        return "data.tweets 不是列表"
    return None


def _is_timeout(exc: Exception) -> bool:
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return True
    return False



def proxy_used() -> bool:
    """Only a boolean leaves this boundary, never a proxy URL or userinfo."""
    return bool(urllib.request.getproxies().get("https")) and not urllib.request.proxy_bypass("api.aisa.one")


def transport_failure(exc):
    """Classify without logging raw reasons. Only a narrow transient allowlist retries."""
    reason = getattr(exc, "reason", exc)
    if _is_timeout(exc) or isinstance(reason, str) and "timed out" in reason.lower():
        return "timeout", True
    if isinstance(reason, socket.gaierror):
        return "conn_error", reason.errno == socket.EAI_AGAIN
    if isinstance(reason, (ConnectionError, http.client.IncompleteRead)):
        return "conn_error", True
    if isinstance(reason, OSError) and reason.errno in (errno.ECONNRESET, errno.ECONNREFUSED,
                                                       errno.ECONNABORTED, errno.EPIPE):
        return "conn_error", True
    if isinstance(reason, str) and "temporary failure in name resolution" in reason.lower():
        return "conn_error", True
    return "shape_error", False

def fetch_account(handle: str, api_key: str, *, opener=urllib_opener, timeout: float = DEFAULT_TIMEOUT,
                  max_pages: int = MAX_PAGES, deadline: float | None = None,
                  base_time: datetime | None = None, clock=time.monotonic,
                  page2_window_hours: float = PAGE2_WINDOW_HOURS,
                  cursor_param: str = CURSOR_PARAM, on_request=None, on_attempt=None,
                  retry_timeout: float | None = None, sleep=time.sleep, jitter=random.random) -> dict:
    """采集一个账号：首页必取；第 20 条仍在 24h 内且接口给了游标才翻第 2 页，最多 2 页。"""
    handle = str(handle).strip()
    base_time = base_time or datetime.now(timezone.utc)
    result = {
        "handle": handle,
        "handle_normalized": normalize_handle(handle),
        "status": STATUS_OK,
        "tweets": [],
        "pages": 0,
        "calls": 0,
        "http_status": None,
        "error": None,
        "fetched_at": None,
        "page_errors": [],
        "paginated": False,
        "pages_complete": True,
    }
    seen_ids: set[str] = set()
    cursor = None
    retries_used = 0
    retry_timeout = float(timeout if retry_timeout is None else retry_timeout)
    page_limit = min(MAX_PAGES, max(1, int(max_pages)))
    for page in range(1, page_limit + 1):
        if page > 1 and not cursor:
            break
        if deadline is not None and clock() >= deadline:
            if result["fetched_at"] is None:
                result["status"] = STATUS_DEADLINE
                result["error"] = "整轮截止时间已到，未发起请求"
            else:
                result["page_errors"].append({"page": page, "status": STATUS_DEADLINE, "error": "截止时间已到，未翻页"})
            break
        url = build_url(handle, cursor if page > 1 else None, cursor_param)
        remaining = deadline - clock() if deadline is not None else float(timeout)
        actual_timeout = min(float(timeout), remaining)
        if actual_timeout <= 0:
            if result["fetched_at"] is None:
                result["status"] = STATUS_TIMEOUT
                result["error"] = "没有可用的请求时间预算"
            else:
                result["page_errors"].append({"page": page, "status": STATUS_TIMEOUT, "error": "没有可用的请求时间预算"})
            break
        attempt = 1
        while True:
            remaining = deadline - clock() if deadline is not None else float(timeout)
            actual_timeout = min(float(timeout), remaining)
            if actual_timeout <= 0:
                failure = (STATUS_DEADLINE, "整轮截止时间已到，未发起请求")
                break
            proxied = proxy_used()
            remaining = deadline - clock() if deadline is not None else float(timeout)
            actual_timeout = min(float(timeout), remaining)
            if actual_timeout <= 0 or attempt > 1 and remaining < retry_timeout:
                # Proxy discovery can consume budget too. Preserve the previous
                # failure when abandoning a retry, without creating a call.
                if attempt == 1:
                    failure = (STATUS_DEADLINE, "整轮截止时间已到，未发起请求")
                break
            # Count only a request admitted by the round coordinator. A rejected
            # callback must not become a phantom API call or timing row.
            try:
                token = on_request() if on_request is not None else None
            except TimeoutError:
                failure = (STATUS_DEADLINE, "整轮截止时间已到，未发起请求")
                break
            result["calls"] += 1
            metadata = {"token": token, "handle": handle, "page": page,
                        "attempt": attempt, "request_index": result["calls"],
                        "is_retry": attempt > 1, "started_at": iso_utc(datetime.now(timezone.utc)),
                        "timeout_seconds": actual_timeout, "proxy_used": proxied}
            if on_attempt is not None:
                on_attempt({**metadata, "phase": "start"})
            begun = clock()
            category, status_code, transient = "shape_error", None, False
            failure = None
            try:
                payload, http_status = opener(url, api_key, actual_timeout)
                elapsed = max(0.0, clock() - begun)
                status_code = http_status
                if http_status != 200:
                    raise urllib.error.HTTPError(url, http_status, "HTTP failure", None, None)
                biz = business_error(payload)
                if biz:
                    failure = (STATUS_BUSINESS, sanitize_error(biz, api_key))
                else:
                    category = "ok"
            except urllib.error.HTTPError as exc:
                elapsed = max(0.0, clock() - begun)
                status_code = exc.code
                category = "http_5xx" if exc.code >= 500 else "http_4xx" if exc.code >= 400 else "shape_error"
                transient = exc.code in (502, 503, 504)
                failure = (STATUS_HTTP, "HTTP " + str(exc.code))
            except Exception as exc:
                elapsed = max(0.0, clock() - begun)
                category, transient = transport_failure(exc)
                failure = (STATUS_TIMEOUT if category == "timeout" else STATUS_PARSE,
                           sanitize_error(type(exc).__name__ + ": " + str(exc), api_key))
            if status_code is not None:
                result["http_status"] = status_code
            if on_attempt is not None:
                on_attempt({**metadata, "phase": "finish", "elapsed_seconds": elapsed,
                            "result": category, "http_status": status_code,
                            "finished_at": iso_utc(datetime.now(timezone.utc))})
            if failure is None:
                break
            if not transient or retries_used:
                break
            delay = min(1.0, 0.05 + 0.15 * max(0.0, min(1.0, float(jitter()))))
            # Do not start a retry unless the full configured request timeout
            # still fits after backoff. Initial requests retain the A1-b clip.
            if deadline is not None and deadline - clock() < retry_timeout + delay:
                break
            sleep(delay)
            if deadline is not None and deadline - clock() < retry_timeout:
                break
            retries_used += 1
            attempt += 1
        if failure is not None:
            kind, message = failure
            if page == 1:
                result["status"], result["error"] = kind, message
            else:
                result["page_errors"].append({"page": page, "status": kind, "error": message})
            break

        data = payload.get("data") or {}
        tweets = data.get("tweets") or []
        if not isinstance(tweets, list):
            tweets = []
        if result["fetched_at"] is None:
            result["fetched_at"] = datetime.now(timezone.utc)
        result["pages"] = page
        for raw in tweets:
            if not isinstance(raw, dict):
                continue
            tid = str(raw.get("id") or "").strip()
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            result["tweets"].append(raw)

        has_next = bool(payload.get("has_next_page") or data.get("has_next_page"))
        cursor = payload.get("next_cursor") or data.get("next_cursor") or payload.get("cursor")
        if page >= page_limit or not has_next or not cursor:
            break
        if not _should_page2(result["tweets"], base_time, page2_window_hours):
            break
        result["paginated"] = True

    if result["fetched_at"] is None:
        result["pages_complete"] = False
        return result
    if result["page_errors"]:
        result["status"] = STATUS_PARTIAL
        result["pages_complete"] = False
    elif not result["tweets"]:
        result["status"] = STATUS_EMPTY
    else:
        result["status"] = STATUS_OK
    return result


def _should_page2(tweets: list[dict], base_time: datetime, window_hours: float) -> bool:
    """翻页条件：第 20 条（索引 19）仍在窗口内，说明这一页可能没覆盖完近 24h。"""
    if len(tweets) <= PAGE2_TRIGGER_INDEX:
        return False
    probe = tweets[PAGE2_TRIGGER_INDEX]
    pub, _ = published_at_of(probe)
    if pub is None:
        return False
    return (base_time - pub).total_seconds() <= window_hours * 3600.0
