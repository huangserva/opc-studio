"""Bounded SSH queries on the configured GPU host; this is not a general shell."""
import codecs
from datetime import datetime, timezone
import ipaddress
import os
import posixpath
import re
import selectors
import shlex
import subprocess
import time


COMMANDS = ('nvidia-smi', 'ps', 'ls', 'pwd', 'uname', 'df', 'du', 'free', 'uptime',
            'stat', 'head', 'tail', 'cat', 'readlink', 'which', 'rg', 'find',
            'systemctl', 'journalctl')
MAX_COMMAND = 4096
RESTRICTIONS = [
    '仅在已配置的 SSH 主机执行列出的只读命令，不是无限制 Shell。',
    '不支持管道、重定向、命令替换、Shell 运算、环境变量、脚本或 sudo。',
    '拒绝明显的凭据路径、设备文件、进程文件描述符及已知二进制扩展名的内容读取。',
    '禁止 GPU 修改、服务修改、日志维护、持续跟随输出及会执行其他程序的查询选项。',
    '凭据路径按命令参数识别；本工具不提供独立于远端用户权限的文件系统隔离。',
]


def _fail(message):
    raise ValueError(message + '；此工具只支持已列出的只读 SSH 查询')


def _path(value, *, text=False):
    """Reject explicit credentials and special files, including normalized paths."""
    if not value or value == '-' or '://' in value or value.startswith('~'):
        _fail('请使用普通文件路径，不使用标准输入、URL 或波浪号展开')
    for candidate in (value, posixpath.normpath(value)):
        parts = candidate.lower().split('/')
        denied = {'.ssh', '.aws', '.azure', '.kube', '.gnupg', 'keyrings', '.netrc', '.npmrc', '.pypirc',
                  '.git-credentials', '.bash_history', '.zsh_history', 'secret',
                  'secrets', 'credentials', 'credential', 'auth.json', 'shadow',
                  'gshadow', 'id_rsa', 'id_ed25519', 'id_ecdsa', 'authorized_keys'}
        for part in parts:
            if part in denied or part == '.env' or part == '.envrc' or part.startswith('.env.'):
                _fail('不允许读取凭据目录、密钥、环境配置或登录记录')
            if re.search(r'(^|[._-])(tokens?|passwords?|credentials?|api[_-]?keys?|access[_-]?keys?)([._-]|$)', part):
                _fail('不允许读取明显的凭据文件')
            if part.endswith(('.pem', '.key')):
                _fail('不允许读取密钥文件')
            if part.startswith('ssh_host_') and part.endswith('_key'):
                _fail('不允许读取 SSH 主机私钥')
        normalized = candidate.lower().lstrip('/')
        if normalized == 'dev' or normalized.startswith('dev/'):
            _fail('不允许读取设备文件')
        if normalized.startswith('proc/') and any(part in {'fd', 'fdinfo', 'environ', 'mem', 'kcore', 'keys'} for part in parts):
            _fail('不允许读取进程凭据、内存或文件描述符')
        if '.config/gcloud' in normalized or '.docker/config.json' in normalized:
            _fail('不允许读取云服务或容器登录配置')
    if text and value.lower().endswith(('.safetensors', '.gguf', '.bin', '.pt', '.pth',
                                      '.ckpt', '.onnx', '.zip', '.gz', '.tar', '.7z',
                                      '.mp4', '.mov', '.mp3', '.wav', '.pdf', '.png',
                                      '.jpg', '.jpeg', '.webp', '.sqlite', '.db')):
        _fail('请用 ls 或 stat 查询二进制文件，不读取其内容')
    return value


def _number(value, maximum=100000):
    if not re.fullmatch(r'\d+', value) or not 0 <= int(value) <= maximum:
        _fail('查询数量须为有上限的非负整数')
    return value


def _word(value):
    if not re.fullmatch(r'[A-Za-z0-9_.,@:+*/?=-]+', value):
        _fail('查询参数格式不支持')
    return value


def _filename_pattern(value):
    # A find predicate receives this as one quoted argument, never shell code.
    if not value or len(value) > 1024:
        _fail('文件名匹配模式须为 1–1024 字符')
    return value


def _options(args, flags=(), values=None, *, clusters=''):
    """Return positional arguments; unknown flags and missing values are rejected."""
    values = values or {}
    positional, used, index, ended = [], {}, 0, False
    while index < len(args):
        token = args[index]
        if not ended and token == '--':
            ended = True
        elif not ended and token.startswith('-') and token != '-':
            key, equal, value = token.partition('=')
            if key in values:
                if not equal:
                    index += 1
                    if index >= len(args):
                        _fail('查询选项缺少参数')
                    value = args[index]
                values[key](value)
                used.setdefault(key, []).append(value)
            elif not equal and token in flags:
                used.setdefault(token, []).append(True)
            elif not equal and len(token) > 2 and token.startswith('-') and not token.startswith('--') and token[1:2] in {k[1:] for k in values if len(k) == 2}:
                key, value = token[:2], token[2:]
                values[key](value)
                used.setdefault(key, []).append(value)
            elif not equal and len(token) > 2 and not token.startswith('--') and ('-' + token[-1]) in values and all(char in clusters for char in token[1:-1]):
                # Conventional combinations such as ps -eo pid,comm,args.
                key = '-' + token[-1]
                index += 1
                if index >= len(args):
                    _fail('查询选项缺少参数')
                value = args[index]
                values[key](value)
                used.setdefault(key, []).append(value)
            elif not equal and len(token) > 1 and not token.startswith('--') and all(char in clusters for char in token[1:]):
                used.setdefault(token, []).append(True)
            else:
                _fail('不支持该查询选项：' + key)
        else:
            positional.append(token)
        index += 1
    return positional, used


def _files(args, flags=(), values=None, *, clusters='', text=False, minimum=0):
    paths, used = _options(args, flags, values, clusters=clusters)
    if len(paths) < minimum:
        _fail('请指定要查询的普通文件')
    for path in paths:
        _path(path, text=text)
    return used


def _systemctl(args):
    properties = {'Id', 'Names', 'Description', 'LoadState', 'ActiveState', 'SubState',
                  'UnitFileState', 'Result', 'MainPID', 'ExecMainPID', 'ExecMainCode',
                  'ExecMainStatus', 'NRestarts', 'FragmentPath', 'MemoryCurrent',
                  'MemoryPeak', 'CPUUsageNSec', 'TasksCurrent', 'ActiveEnterTimestamp',
                  'InactiveEnterTimestamp', 'StateChangeTimestamp', 'User', 'Group',
                  'WorkingDirectory', 'ControlGroup', 'InvocationID'}

    def property_value(value):
        if not value or any(item not in properties for item in value.split(',')):
            _fail('show 只允许查询服务状态属性，不读取 Environment 或凭据')

    positional, used = _options(args, {'--no-pager', '--plain', '--full', '--all',
        '--no-legend', '--no-ask-password', '--failed', '--value', '--user', '--system', '-l', '-a'},
        {'--type': _word, '-t': _word, '--state': _word, '--property': property_value, '-p': property_value})
    if not positional or positional[0] not in {'status', 'is-active', 'is-enabled', 'show', 'list-units', 'list-unit-files'}:
        _fail('systemctl 只允许查询状态，不允许启动、停止或修改服务')
    if positional[0] == 'show' and not ({'--property', '-p'} & set(used)):
        _fail('systemctl show 请用 --property 明确选择服务状态属性')
    for unit in positional[1:]:
        if not re.fullmatch(r'[A-Za-z0-9_.*?@:-]+', unit):
            _fail('请使用服务名称查询，不指定其他主机或路径')


def _journalctl(args):
    values = {'--unit': _word, '-u': _word, '--user-unit': _word, '--priority': _word,
              '-p': _word, '--identifier': _word, '-t': _word, '--lines': _number,
              '-n': _number, '--since': lambda v: v, '-S': lambda v: v,
              '--until': lambda v: v, '-U': lambda v: v, '--output': _word,
              '-o': _word, '--output-fields': _word, '--cursor': _word,
              '--after-cursor': _word, '--file': _path, '--directory': _path, '-D': _path}
    flags = {'--no-pager', '--no-hostname', '--utc', '--quiet', '-q', '--all', '-a',
             '--reverse', '-r', '--show-cursor', '--list-boots', '--disk-usage',
             '--user', '--system', '--kernel', '-k', '--boot', '-b'}
    # Boot IDs may be supplied with '='; the bare form queries the current boot.
    args = list(args)
    for index, value in enumerate(args):
        if value.startswith('--boot='):
            _word(value.split('=', 1)[1])
            args[index] = '--boot'
    matches, _ = _options(args, flags, values)
    for match in matches:
        if not re.fullmatch(r'[A-Z_][A-Z_0-9]*=[A-Za-z0-9_.@:+-]+', match):
            _fail('journalctl 只允许日志查询条件，不执行维护动作')


def _find(args):
    # find's '--' terminates leading options, not its executable predicates.
    if '--' in args:
        _fail('find 不支持 --；请直接使用路径和允许的查询条件')
    flags = {'-H', '-L', '-P', '-xdev', '-mount', '-depth', '-ignore_readdir_race',
             '-print', '-print0', '-ls', '-prune', '-quit', '-empty', '-readable',
             '-writable', '-executable', '-a', '-and', '-o', '-or', '-not', '!'}
    values = {key: _word for key in ('-path', '-ipath', '-type',
              '-xtype', '-user', '-group', '-size', '-mtime', '-mmin', '-atime',
              '-amin', '-ctime', '-cmin', '-uid', '-gid')}
    values.update({'-name': _filename_pattern, '-iname': _filename_pattern})
    values.update({'-maxdepth': lambda v: _number(v, 1000), '-mindepth': lambda v: _number(v, 1000),
                   '-newer': _path, '-anewer': _path, '-cnewer': _path, '-newermt': lambda v: v})
    paths, _ = _options(args, flags, values)
    for path in paths:
        _path(path)


def validate_command(command):
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND:
        _fail('命令须为 1–4096 字符的非空字符串')
    if any(ord(char) < 32 for char in command) or re.search(r'[|;&<>`$()]', command):
        _fail('不支持 Shell 运算、管道、重定向或命令替换')
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        _fail('命令引号不完整')
    if not argv or argv[0] not in COMMANDS:
        _fail('命令不在只读查询清单中')
    name, args = argv[0], argv[1:]
    if name == 'nvidia-smi':
        values = {'--format': _word, '--id': _word, '-i': _word,
                  **{key: _word for key in ('--query-gpu', '--query-compute-apps', '--query-accounted-apps', '--query-supported-clocks')}}
        positional, _ = _options(args, {'-L', '--list-gpus', '--list-excluded-gpus', '--help', '--version'}, values)
        if positional:
            _fail('nvidia-smi 只允许列表和 --query 查询，不修改 GPU')
    elif name == 'ps':
        positional, _ = _options(args, {'--no-headers', '--headers', '--forest', '--everyone', '--deselect'},
            {'-o': _word, '--format': _word, '-p': _word, '--pid': _word, '-C': _word,
             '--sort': _word, '-u': _word, '-U': _word, '-g': _word, '-G': _word}, clusters='aAefxuwlHmdN')
        if any(not re.fullmatch(r'[aAefxuwlHmdN]+|\d+(?:,\d+)*', token) for token in positional):
            _fail('ps 只允许进程列表查询')
    elif name == 'systemctl':
        _systemctl(args)
    elif name == 'journalctl':
        _journalctl(args)
    elif name == 'find':
        _find(args)
    elif name == 'rg':
        positional, used = _options(args, {'--files', '--count', '--count-matches', '--files-with-matches',
            '--files-without-match', '--line-number', '--no-heading', '--ignore-case', '--fixed-strings',
            '--word-regexp', '--line-regexp', '--invert-match', '--quiet', '--stats', '--sort-files'},
            {'--glob': _word, '-g': _word, '--max-count': _number, '-m': _number,
             '--max-depth': _number, '--max-filesize': _word, '--context': _number, '-C': _number,
             '--after-context': _number, '-A': _number, '--before-context': _number, '-B': _number,
             '--regexp': lambda v: v, '-e': lambda v: v, '--type': _word, '-t': _word}, clusters='nicslLqFw xv'.replace(' ', ''))
        paths = positional if '--files' in used or {'--regexp', '-e'} & set(used) else positional[1:]
        if not positional and not ({'--files', '--regexp', '-e'} & set(used)):
            _fail('rg 请指定查询文本')
        for path in paths:
            _path(path, text=True)
    elif name in ('cat', 'head', 'tail'):
        flags = {'--number', '--number-nonblank', '--show-all', '--show-nonprinting', '--squeeze-blank'} if name == 'cat' else {'--quiet', '--silent', '--verbose', '-q', '-v'}
        values = {} if name == 'cat' else {'-n': _number, '--lines': _number, '-c': _number, '--bytes': _number}
        clusters = 'AbEenstTv' if name == 'cat' else 'qv'
        _files(args, flags, values, clusters=clusters, text=True, minimum=1)
    elif name == 'ls':
        _files(args, {'--all', '--almost-all', '--long', '--human-readable', '--directory', '--numeric-uid-gid',
                     '--recursive', '--reverse', '--full-time', '--group-directories-first'},
               {'--sort': _word, '--time': _word, '--color': _word}, clusters='alAhHdFnRrStU1ijkopqsuvx')
    elif name == 'pwd':
        positional, _ = _options(args, {'-L', '-P', '--logical', '--physical'})
        if positional:
            _fail('pwd 不接受文件或其他操作')
    elif name == 'uname':
        positional, _ = _options(args, {'--all', '--kernel-name', '--nodename', '--kernel-release', '--kernel-version',
                                        '--machine', '--processor', '--hardware-platform', '--operating-system'}, clusters='asnrvmpio')
        if positional:
            _fail('uname 只允许系统信息查询')
    elif name in ('df', 'du'):
        _files(args, {'--human-readable', '--si', '--total', '--all', '--local', '--summarize',
                     '--apparent-size', '--inodes', '--one-file-system'},
               {'-B': _word, '--block-size': _word, '--output': _word, '--max-depth': _number,
                '-d': _number, '-t': _word, '-x': _word, '--type': _word, '--exclude-type': _word}, clusters='ahHklmPTsxScb')
    elif name == 'free':
        positional, _ = _options(args, {'--bytes', '--kibi', '--mebi', '--gibi', '--tebi', '--pebi',
                    '--human', '--wide', '--total', '--si'}, clusters='bkmgthw')
        if positional:
            _fail('free 只允许一次内存查询')
    elif name == 'uptime':
        positional, _ = _options(args, {'-p', '-s', '--pretty', '--since'})
        if positional:
            _fail('uptime 只允许运行时间查询')
    elif name == 'stat':
        _files(args, {'--dereference', '--file-system', '--terse', '-L', '-f', '-t'},
               {'--format': lambda v: v, '-c': lambda v: v, '--printf': lambda v: v}, minimum=1)
    elif name == 'readlink':
        _files(args, {'--canonicalize', '--canonicalize-existing', '--canonicalize-missing', '--no-newline', '--quiet'},
               clusters='efmnqsv', minimum=1)
    elif name == 'which':
        positional, _ = _options(args, {'-a', '--all'})
        if not positional or any(not re.fullmatch(r'[A-Za-z0-9_.+-]+', value) for value in positional):
            _fail('which 请指定程序名称，不指定脚本路径')
    return argv


class SSHRemote:
    def __init__(self):
        self.host_alias = os.environ.get('ARTICLE_STUDIO_GPU_HOST', 'newgpu')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', self.host_alias):
            raise ValueError('已配置的 SSH 主机别名无效')

    @staticmethod
    def capabilities():
        return {'transport': 'ssh', 'read_only': True, 'commands': list(COMMANDS),
                'scope': '已配置 GPU 主机上的有限只读命令；不接受其他主机、凭据或任意 Shell',
                'limits': {'command_chars': MAX_COMMAND, 'timeout_seconds': {'min': 1, 'max': 30, 'default': 20},
                           'max_output_chars': {'min': 1, 'max': 16000, 'default': 12000}},
                'restrictions': list(RESTRICTIONS),
                'systemctl_show': '须用 --property 选择服务状态属性；不读取 Environment 或凭据',
                'connection_status': '固定执行 SSH true；失败时从可信 SSH 配置读取跳板别名，对跳板连接及回环转发端口进行只读诊断，不生成或修改媒体',
                'connection_diagnostics': '只支持配置中的单个 ProxyJump 别名及回环目标；跳板连通不代表 GPU 连通，端口监听不代表隧道健康'}

    def status(self):
        result = self._run(['true'], 10, 2000)
        result['connected'] = result['exit_code'] == 0 and not result['timed_out'] and not result['connection_failed']
        if not result['connected']:
            result['diagnostic'] = self._diagnose_jump()
            result['summary'] += '；' + result['diagnostic']['summary']
        return result

    def _resolved_route(self):
        # Inspect only the existing local configuration; never return its full
        # output, which may contain identity paths or other private settings.
        config = self._run_process(['ssh', '-G', '--', self.host_alias], 3, 16000)
        if config['exit_code'] != 0 or config['timed_out'] or config['truncated']:
            raise ValueError('无法完整解析本机 SSH 路由；附加诊断未执行')
        values = {}
        for line in config['stdout'].splitlines():
            key, separator, value = line.partition(' ')
            if key in {'hostname', 'port', 'proxyjump'} and separator:
                if key in values:
                    raise ValueError('SSH 路由字段重复；附加诊断未执行')
                values[key] = value.strip()
        host, port, jump = (values.get(key, '') for key in ('hostname', 'port', 'proxyjump'))
        if host not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('此路由不是回环转发目标；附加诊断未执行')
        if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
            raise ValueError('SSH 转发端口无法确认；附加诊断未执行')
        if jump == 'none' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', jump) or jump == self.host_alias:
            raise ValueError('此路由没有可诊断的单个 ProxyJump 别名；附加诊断未执行')
        return {'jump_host_alias': jump, 'forwarding_host': host, 'forwarding_port': int(port)}

    def _diagnose_jump(self):
        try:
            route = self._resolved_route()
        except ValueError as error:
            return {'stage': 'route_unknown', 'summary': str(error)}
        diagnostic = {**route, 'port_listening': None, 'listen_addresses': []}
        jump = self._run(['true'], 8, 2000, host_alias=route['jump_host_alias'])
        diagnostic['jump_probe'] = jump
        if jump['exit_code'] != 0 or jump['timed_out'] or jump['connection_failed']:
            diagnostic.update(stage='jump_unreachable', summary='跳板 SSH 探测失败；尚未确认转发端口状态')
            return diagnostic
        listener = self._run(['ss', '-H', '-ltn', f'sport = :{route["forwarding_port"]}'],
                             8, 2000, host_alias=route['jump_host_alias'])
        diagnostic['listener_probe'] = listener
        if listener['exit_code'] != 0 or listener['timed_out'] or listener['truncated']:
            diagnostic.update(stage='listener_unknown', summary='跳板已连通；转发端口监听查询失败或不完整，状态未知')
            return diagnostic
        if not listener['stdout'].strip():
            diagnostic.update(port_listening=False, stage='forwarding_port_not_listening',
                              summary=f'跳板已连通，但 TCP {route["forwarding_port"]} 端口没有监听；尚未连接到 GPU，不能据此断定 GPU SSH 服务故障')
            return diagnostic
        for line in listener['stdout'].splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[0] == 'LISTEN' and fields[1].isdigit() and fields[2].isdigit():
                address, separator, port = fields[3].rpartition(':')
                if separator and port == str(route['forwarding_port']) and address:
                    if address != '*':
                        try:
                            ipaddress.ip_address(address.strip('[]'))
                        except ValueError:
                            continue
                    diagnostic['listen_addresses'].append(fields[3])
        if diagnostic['listen_addresses']:
            diagnostic.update(port_listening=True, stage='forwarding_port_listening_target_failed',
                              summary=f'跳板已连通且 TCP {route["forwarding_port"]} 端口有监听，但 GPU SSH 仍未连通；监听地址与转发终点需继续核查')
        else:
            diagnostic.update(stage='listener_unknown', summary='跳板已连通；端口查询未返回可识别的监听记录，状态未知')
        return diagnostic

    def execute(self, command, timeout_seconds=20, max_output_chars=12000):
        for value, maximum, label in ((timeout_seconds, 30, '超时秒数'), (max_output_chars, 16000, '输出字符数')):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f'{label}须为 1–{maximum} 的整数')
        return self._run(validate_command(command), timeout_seconds, max_output_chars)

    def _run(self, argv, timeout_seconds, max_output_chars, *, host_alias=None):
        host_alias = self.host_alias if host_alias is None else host_alias
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', host_alias):
            raise ValueError('内部 SSH 主机别名无效')
        command = shlex.join(argv)
        result = {'checked_at': None, 'transport': 'ssh', 'host_alias': host_alias,
                  'command': command, 'exit_code': None, 'stdout': '', 'stderr': '',
                  'timed_out': False, 'truncated': False, 'connection_failed': False}
        ssh = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
               '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ClearAllForwardings=yes', '--', host_alias, command]
        result.update(self._run_process(ssh, timeout_seconds, max_output_chars))
        result['checked_at'] = datetime.now(timezone.utc).isoformat()
        if result['connection_failed']:
            summary = 'SSH 连接失败，命令未执行确认：' + command
        elif result['timed_out']:
            summary = 'SSH 命令超时，无法确认远端完成：' + command
        else:
            summary = f'SSH 已执行 {command}；退出码 {result["exit_code"]}'
        result['summary'] = summary + ('；输出已截断' if result['truncated'] else '')
        return result

    @staticmethod
    def _run_process(argv, timeout_seconds, max_output_chars, *, cancel_check=None):
        result = {'exit_code': None, 'stdout': '', 'stderr': '', 'timed_out': False,
                  'truncated': False, 'connection_failed': False}
        deadline = time.monotonic() + timeout_seconds
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, shell=False)
        except OSError:
            result['connection_failed'] = True
            message = '无法启动本机 SSH 客户端'
            result['stderr'] = message[:max_output_chars]
            result['truncated'] = len(message) > max_output_chars
        else:
            selector = selectors.DefaultSelector()
            decoders = {name: codecs.getincrementaldecoder('utf-8')('replace') for name in ('stdout', 'stderr')}
            chunks = {'stdout': [], 'stderr': []}
            remaining = max_output_chars

            def capture(name, binary, final=False):
                nonlocal remaining
                value = decoders[name].decode(binary, final=final)
                if len(value) > remaining:
                    result['truncated'] = True
                if remaining:
                    chunks[name].append(value[:remaining])
                    remaining -= min(remaining, len(value))

            try:
                for name in ('stdout', 'stderr'):
                    pipe = getattr(process, name)
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, name)
                while selector.get_map():
                    if cancel_check and cancel_check():
                        raise ValueError('定时任务已停止或超过运行上限')
                    wait = deadline - time.monotonic()
                    if wait <= 0:
                        result['timed_out'] = True
                        process.kill()
                        break
                    for key, _ in selector.select(min(wait, .2)):
                        try:
                            binary = os.read(key.fileobj.fileno(), 65536)
                        except BlockingIOError:
                            continue
                        if binary:
                            capture(key.data, binary)
                        else:
                            capture(key.data, b'', final=True)
                            selector.unregister(key.fileobj)
                if not result['timed_out']:
                    while process.poll() is None:
                        if cancel_check and cancel_check():
                            raise ValueError('定时任务已停止或超过运行上限')
                        wait = deadline - time.monotonic()
                        if wait <= 0:
                            result['timed_out'] = True
                            process.kill()
                            break
                        try:
                            process.wait(timeout=min(.2, wait))
                        except subprocess.TimeoutExpired:
                            pass
                if result['timed_out']:
                    process.wait(timeout=1)
                else:
                    result['exit_code'] = process.returncode
                result['stdout'] = ''.join(chunks['stdout'])
                result['stderr'] = ''.join(chunks['stderr'])
                result['connection_failed'] = result['exit_code'] == 255
            finally:
                selector.close()
                for name in ('stdout', 'stderr'):
                    getattr(process, name).close()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=1)
        return result
