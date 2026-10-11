"""Adapter for the official Buffer CLI.

Local validation (`validate`, dry-run only), channel lookup, post submission and
status read-back. Every invocation uses an argument list — never a shell — sends
input on stdin, is bounded by a timeout, and parses JSON output. Error text is
truncated and scrubbed of credential-shaped strings before it can reach a record.
"""
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 60
MAX_DETAIL_CHARS = 400
SUCCESS_TYPENAMES = frozenset({'PostActionSuccess'})
# A transport-level or 5xx style API failure may already have created the post,
# so it is never treated as a clean failure.
MAYBE_CREATED_PATTERN = re.compile(
    r'(?i)(timed?\s*out|timeout|socket hang ?up|econn|enotfound|eai_again|'
    r'\b5\d\d\b|server error|overloaded|unavailable|temporar|network|dns)')
POST_ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{8,40}')
CHANNEL_FIELDS = 'id,name,service'
POST_FIELDS = 'id,status,dueAt,sentAt,externalLink,channelService,channelId,error.message'

_SECRET_LABEL = re.compile(
    r'(?i)\b(api[-_ ]?key|apikey|access[-_ ]?token|auth[-_ ]?token|client[-_ ]?secret|'
    r'secret|password|token|authorization|bearer)\b(["\']?\s*[:=]\s*)[^\s,;}"\']+')
_SECRET_PREFIXED = re.compile(r'(?i)\b(?:sk|pk|ghp|xox[baprs]|buf)[-_][A-Za-z0-9_-]{10,}\b')
_SECRET_VALUE = re.compile(r'\b[A-Za-z0-9+/_]{32,}={0,2}\b')


def redact(value):
    """Truncate and hide anything that could be a credential."""
    text = str(value if value is not None else '')
    text = _SECRET_LABEL.sub(lambda m: m.group(1) + m.group(2) + '‹已隐藏›', text)
    text = _SECRET_PREFIXED.sub('‹已隐藏›', text)
    text = _SECRET_VALUE.sub('‹已隐藏›', text)
    if len(text) > MAX_DETAIL_CHARS:
        text = text[:MAX_DETAIL_CHARS] + '…（已截断）'
    return ' '.join(text.split())


def cli_command(command=None):
    """Resolve the CLI invocation; it must always be a parameter list."""
    if command is None:
        command = ['buffer'] if shutil.which('buffer') else ['npx', '--yes', '@bufferapp/cli@0.5.2']
    if isinstance(command, str) or not isinstance(command, (list, tuple)) or not command \
            or not all(isinstance(part, str) and part for part in command):
        raise ValueError('CLI 命令必须是参数列表')
    return list(command)


def normalize_account(value):
    return str(value or '').strip().lstrip('@').lower()


def _invoke(arguments, payload=None, timeout=DEFAULT_TIMEOUT, command=None):
    argv = [*cli_command(command), *arguments]
    stdin = json.dumps(payload, ensure_ascii=False) if payload is not None else ''
    try:
        finished = subprocess.run(argv, input=stdin, text=True, capture_output=True,
                                  timeout=timeout, check=False)
    except FileNotFoundError:
        return {'ran': False, 'reason': 'missing_cli', 'returncode': None, 'stdout': '', 'stderr': ''}
    except subprocess.TimeoutExpired:
        return {'ran': False, 'reason': 'timeout', 'returncode': None, 'stdout': '', 'stderr': ''}
    return {'ran': True, 'reason': None, 'returncode': finished.returncode,
            'stdout': finished.stdout or '', 'stderr': finished.stderr or ''}


def _transport_message(result):
    if result.get('reason') == 'missing_cli':
        return '未找到官方 Buffer CLI，未发出任何请求'
    if result.get('reason') == 'timeout':
        return 'Buffer CLI 调用超时，结果无法确认'
    return 'Buffer CLI 调用未完成'


def _parse_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _error_detail(result):
    """Return {'code','message'} only for a structured CLI error envelope."""
    for stream in (result.get('stderr', ''), result.get('stdout', '')):
        parsed = _parse_json(stream)
        if isinstance(parsed, dict) and (parsed.get('message') or parsed.get('error')):
            code = parsed.get('code') or parsed.get('error') or ''
            return {'code': redact(code), 'message': redact(parsed.get('message') or parsed.get('error'))}
        if isinstance(parsed, dict) and parsed.get('__typename'):
            error = parsed.get('error')
            detail = error.get('message') if isinstance(error, dict) else parsed.get('message')
            return {'code': redact(parsed.get('__typename')), 'message': redact(detail or '')}
    return None


def _raw_tail(result):
    """A short, scrubbed fragment for diagnosis when no envelope was returned."""
    tail = str(result.get('stderr') or result.get('stdout') or '').strip()
    return redact(tail[-160:]) if tail else ''


def _post_fields(post):
    keep = ('id', 'status', 'dueAt', 'sentAt', 'externalLink', 'channelService', 'channelId')
    record = {key: post.get(key) for key in keep if post.get(key) is not None}
    error = post.get('error')
    if isinstance(error, dict) and error.get('message'):
        record['error_message'] = redact(error.get('message'))
    return record


def validate(payload, command=None):
    if payload.get('saveToDraft') is not True:
        raise ValueError('当前适配器仅接受明确的草稿请求')
    argv = ['posts', 'create', '--input', '-', '--dry-run', '--output', 'json', '--quiet']
    result = _invoke(argv, payload, 30, command)
    if not result['ran']:
        if result['reason'] == 'missing_cli':
            raise ValueError('未找到官方 Buffer CLI，请配置 @bufferapp/cli') from None
        raise ValueError('Buffer 本地校验超时，未提交在线请求') from None
    if result['returncode']:
        raise ValueError('Buffer 本地校验未通过，请检查请求格式与 CLI 版本')
    return {'status': 'validated_locally', 'published': False, 'remote_request_sent': False}


def channels_list(command=None, timeout=DEFAULT_TIMEOUT):
    """Read-only channel list. Returns [{'id','name','service'}...]."""
    result = _invoke(['channels', 'list', '--fields', CHANNEL_FIELDS, '--output', 'json', '--quiet'],
                     None, timeout, command)
    if not result['ran']:
        raise ValueError('读取 Buffer 渠道失败：' + _transport_message(result))
    if result['returncode']:
        detail = _error_detail(result)
        raise ValueError('读取 Buffer 渠道失败：' + ((detail or {}).get('message') or 'CLI 返回错误'))
    parsed = _parse_json(result['stdout'])
    if not isinstance(parsed, list):
        raise ValueError('Buffer 渠道列表无法解析，未做任何提交')
    channels = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        entry = {key: item.get(key) for key in ('id', 'name', 'service')}
        if all(isinstance(value, str) and value for value in entry.values()):
            channels.append(entry)
    return channels


def match_channel(channels, account):
    """Only a twitter-service channel whose name equals the target account may be used."""
    target = normalize_account(account)
    if not target:
        raise ValueError('请先在 X 平台填写发布账号，再核对 Buffer 渠道')
    twitter = [channel for channel in channels if channel.get('service') == 'twitter']
    if not twitter:
        raise ValueError('Buffer 没有已连接的 X（twitter）渠道，无法提交；请先在 Buffer 连接该账号')
    matches = [channel for channel in twitter if normalize_account(channel.get('name')) == target]
    if not matches:
        names = '、'.join(sorted({str(channel.get('name')) for channel in twitter}))
        raise ValueError('Buffer 渠道中没有与目标账号 @' + target + ' 一致的 X 渠道；可用渠道：'
                         + names + '。账号名称不能代替渠道 ID，也不能手填渠道 ID 绕过核对')
    if len(matches) > 1:
        raise ValueError('Buffer 中有 ' + str(len(matches)) + ' 个渠道都叫 @' + target
                         + '，无法确定目标；请先在 Buffer 处理重复渠道')
    return matches[0]


def resolve_channel(account, command=None, timeout=DEFAULT_TIMEOUT):
    return match_channel(channels_list(command, timeout), account)


def _failure_reason(payload, typename):
    error = payload.get('error')
    message = ''
    if isinstance(error, dict):
        message = str(error.get('message') or error.get('rawError') or '')
    return redact(message or typename or 'Buffer 返回失败类型')


def create_post(payload, command=None, timeout=DEFAULT_TIMEOUT):
    """Submit one post/thread. Never retries: an unclear result stays unclear."""
    if not isinstance(payload, dict):
        raise ValueError('Buffer 请求必须是对象')
    for key in ('channelId', 'mode', 'schedulingType'):
        if not isinstance(payload.get(key), str) or not payload.get(key):
            raise ValueError('Buffer 请求缺少 ' + key)
    result = _invoke(['posts', 'create', '--input', '-', '--output', 'json', '--quiet'],
                     payload, timeout, command)
    if not result['ran']:
        if result['reason'] == 'missing_cli':
            return {'outcome': 'failed', 'retryable': True, 'post': None,
                    'message': '未找到官方 Buffer CLI，未发出任何请求'}
        return {'outcome': 'unknown', 'retryable': False, 'post': None,
                'message': 'Buffer 提交超时，无法确认是否已创建帖子；请到 Buffer 核实后再操作，系统不会自动重试'}
    if result['returncode'] == 0:
        parsed = _parse_json(result['stdout'])
        if isinstance(parsed, dict):
            typename = parsed.get('__typename')
            post = parsed.get('post') if isinstance(parsed.get('post'), dict) else None
            if typename in SUCCESS_TYPENAMES:
                if post and isinstance(post.get('id'), str) and post['id']:
                    return {'outcome': 'submitted', 'retryable': False, 'post': _post_fields(post),
                            'message': 'Buffer 已接收该帖子'}
                return {'outcome': 'unknown', 'retryable': False, 'post': None,
                        'message': 'Buffer 返回成功类型但没有帖子 ID，无法确认结果；未自动重试，请到 Buffer 核实'}
            if typename:
                return {'outcome': 'failed', 'retryable': True, 'post': None,
                        'message': 'Buffer 明确拒绝该请求：' + _failure_reason(parsed, typename)}
            return {'outcome': 'unknown', 'retryable': False, 'post': None,
                    'message': 'Buffer 返回内容无法判定结果，未自动重试；请到 Buffer 核实'}
        return {'outcome': 'unknown', 'retryable': False, 'post': None,
                'message': 'Buffer 返回内容无法解析，未自动重试；请到 Buffer 核实'}
    detail = _error_detail(result)
    if not detail:
        tail = _raw_tail(result)
        return {'outcome': 'unknown', 'retryable': False, 'post': None,
                'message': 'Buffer 提交失败但没有可判定的错误信息，未自动重试；请到 Buffer 核实'
                           + ('（' + tail + '）' if tail else '')}
    message = detail['message'] or detail['code'] or 'Buffer 返回错误'
    if MAYBE_CREATED_PATTERN.search(message):
        return {'outcome': 'unknown', 'retryable': False, 'post': None,
                'message': 'Buffer 返回服务异常，可能已创建帖子，未自动重试：' + message}
    return {'outcome': 'failed', 'retryable': True, 'post': None, 'message': 'Buffer 拒绝该请求：' + message}


def get_post(post_id, command=None, timeout=DEFAULT_TIMEOUT):
    """Read back one post's status. Read-only, so a failure is simply a failure."""
    ident = str(post_id or '')
    if not POST_ID_PATTERN.fullmatch(ident):
        raise ValueError('Buffer 帖子 ID 格式无效')
    result = _invoke(['posts', 'get', '--id', ident, '--fields', POST_FIELDS,
                      '--output', 'json', '--quiet'], None, timeout, command)
    if not result['ran']:
        return {'outcome': 'unknown', 'post': None, 'message': _transport_message(result)}
    if result['returncode']:
        detail = _error_detail(result)
        return {'outcome': 'failed', 'post': None,
                'message': '读取 Buffer 帖子状态失败：' + ((detail or {}).get('message') or 'CLI 返回错误')}
    parsed = _parse_json(result['stdout'])
    if not isinstance(parsed, dict) or not isinstance(parsed.get('id'), str) or not parsed.get('id'):
        return {'outcome': 'unknown', 'post': None, 'message': 'Buffer 状态返回无法解析，未改动记录'}
    return {'outcome': 'ok', 'post': _post_fields(parsed), 'message': 'Buffer 状态已读取'}


def tweet_link(value):
    """Return {'account','id','url'} for a real tweet URL, otherwise None."""
    try:
        parsed = urlsplit(str(value or ''))
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.port:
            return None
        if parsed.query or parsed.fragment:
            return None
        if parsed.hostname not in ('x.com', 'twitter.com', 'www.x.com', 'www.twitter.com', 'mobile.twitter.com'):
            return None
        match = re.fullmatch(r'([A-Za-z0-9_]{1,15})/status/(\d+)', parsed.path.strip('/'))
    except ValueError:
        return None
    if not match:
        return None
    return {'account': match.group(1), 'id': match.group(2),
            'url': 'https://x.com/' + match.group(1) + '/status/' + match.group(2)}


# --- read-only insights ---------------------------------------------------------------
# Buffer's CLI exposes one metrics command, and it aggregates a whole organization
# over a date range; there is no per-post metrics call. These helpers stay queries:
# they never create, edit or delete anything, and their output is labelled as an
# organization-wide figure so it can never be mistaken for one work's performance.
ACCOUNT_FIELDS = 'id,organizations.id,organizations.name,organizations.channelCount'
METRICS_FIELDS = 'metrics.name,metrics.type,metrics.value,metrics.unit,metricsUpdatedAt'
ORGANIZATION_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
WINDOW_DAY = re.compile(r'\d{4}-\d{2}-\d{2}')


def _query(arguments, timeout, command, label):
    result = _invoke([*arguments, '--output', 'json', '--quiet'], None, timeout, command)
    if not result['ran']:
        return {'outcome': 'unknown', 'message': label + '失败：' + _transport_message(result)}
    if result['returncode']:
        detail = _error_detail(result)
        return {'outcome': 'failed',
                'message': label + '失败：' + ((detail or {}).get('message') or 'CLI 返回错误')}
    parsed = _parse_json(result['stdout'])
    if not isinstance(parsed, dict):
        return {'outcome': 'unknown', 'message': label + '返回内容无法解析，未改动任何记录'}
    return {'outcome': 'ok', 'data': parsed}


def organizations(timeout=DEFAULT_TIMEOUT, command=None):
    """Read-only account lookup: [{'id','name','channel_count'}...] for this account."""
    result = _query(['account', '--fields', ACCOUNT_FIELDS], timeout, command, '读取 Buffer 账号')
    if result['outcome'] != 'ok':
        raise ValueError(result['message'])
    organizations_ = []
    for item in result['data'].get('organizations') or []:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            continue
        if not ORGANIZATION_ID.fullmatch(item['id']):
            continue
        organizations_.append({'id': item['id'], 'name': str(item.get('name') or '')[:120],
                               'channel_count': item.get('channelCount')})
    return organizations_


def window_day(value, label):
    """Accept a plain date or an ISO instant and normalise it to Buffer's UTC day."""
    text = str(value or '').strip()
    if WINDOW_DAY.fullmatch(text):
        return text + 'T00:00:00Z'
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(label + '须为 YYYY-MM-DD 或含时区的日期时间') from None
    if parsed.tzinfo is None:
        raise ValueError(label + '须带时区')
    return parsed.astimezone(timezone.utc).strftime('%Y-%m-%dT00:00:00Z')


def aggregated_metrics(organization_id, start, end, channel_ids=None,
                       timeout=DEFAULT_TIMEOUT, command=None):
    """Fetch the organization-wide post metrics for one date window. Read-only."""
    if not isinstance(organization_id, str) or not ORGANIZATION_ID.fullmatch(organization_id):
        raise ValueError('Buffer 组织编号无效')
    window = [window_day(start, '统计开始日期'), window_day(end, '统计结束日期')]
    if window[0] > window[1]:
        raise ValueError('统计开始日期不能晚于结束日期')
    channels = []
    if channel_ids is not None:
        if not isinstance(channel_ids, list) or len(channel_ids) > 20 or any(
                not isinstance(item, str) or not ORGANIZATION_ID.fullmatch(item) for item in channel_ids):
            raise ValueError('Buffer 渠道编号无效，最多 20 个')
        channels = list(channel_ids)
    arguments = ['aggregatedPostMetrics', '--json', json.dumps({
        'organizationId': organization_id, 'startDateTime': window[0], 'endDateTime': window[1],
        **({} if channel_ids is None else {'channelIds': channels})}), '--fields', METRICS_FIELDS]
    result = _query(arguments, timeout, command, '读取 Buffer 组织统计')
    if result['outcome'] != 'ok':
        return {'outcome': result['outcome'], 'message': result['message'], 'metrics': []}
    metrics = []
    for item in result['data'].get('metrics') or []:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name']:
            continue
        value = item.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        metrics.append({'name': str(item['name'])[:120], 'type': str(item.get('type') or '')[:60],
                        'unit': str(item.get('unit') or '')[:40],
                        'value': str(value)[:60] if isinstance(value, str) else value})
    return {'outcome': 'ok', 'metrics': metrics,
            'metrics_updated_at': str(result['data'].get('metricsUpdatedAt') or '')[:50],
            'window': {'start': window[0], 'end': window[1]},
            'message': 'Buffer 组织级统计已读取；这是整个组织在该时间窗的汇总，不属于单个作品'}
