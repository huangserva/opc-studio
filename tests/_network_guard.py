"""测试专用网络护栏：共享安装，按活跃作用域交集约束，最后退出时还原。

安装顺序与退出顺序独立。只允许字面回环主机；端口约束由每个作用域决定。
"""
import socket
import threading

_LOCAL_HOSTS = (None, '', '127.0.0.1', '::1', 'localhost')
_lock = threading.RLock()
_rules = {}
_original = None


def _deny(rule, address):
    rule.blocked.append(address)
    raise rule.error(rule.message)


def _check_address(address):
    host = address[0] if isinstance(address, tuple) and address else None
    port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
    with _lock:
        for rule in _rules.values():
            if (not isinstance(address, tuple) or host not in _LOCAL_HOSTS
                    or port in rule.blocked_ports
                    or (rule.allowed_ports is not None
                        and (host not in ('127.0.0.1', '::1')
                             or port not in rule.allowed_ports))):
                _deny(rule, address)


def _install():
    global _original
    real_socket, real_dns = socket.socket, socket.getaddrinfo
    _original = (real_socket, real_dns)

    class Guard(real_socket):
        def connect(self, address):
            _check_address(address)
            return super().connect(address)

        def connect_ex(self, address):
            _check_address(address)
            return super().connect_ex(address)

    def guarded_dns(host, *args, **kwargs):
        with _lock:
            if host not in _LOCAL_HOSTS:
                for rule in _rules.values():
                    _deny(rule, (host, args[0] if args else None))
        return real_dns(host, *args, **kwargs)

    socket.socket = Guard
    socket.getaddrinfo = guarded_dns


class NetworkGuard:
    """可重复进入；每次进入拥有独立 token，移除只影响自己的作用域。"""

    def __init__(self, *, allowed_ports=None, blocked_ports=(), error=AssertionError,
                 message='ZERO_EXTERNAL_NETWORK'):
        self.allowed_ports = None if allowed_ports is None else tuple(allowed_ports)
        self.blocked_ports = tuple(blocked_ports)
        self.error = error
        self.message = message
        self.blocked = []
        self._tokens = []

    def __enter__(self):
        with _lock:
            if not _rules:
                _install()
            token = object()
            _rules[token] = self
            self._tokens.append(token)
        return self

    def __exit__(self, *exc):
        global _original
        with _lock:
            token = self._tokens.pop()
            del _rules[token]
            if not _rules:
                socket.socket, socket.getaddrinfo = _original
                _original = None
        return False


def offline_network():
    """AIsa：允许回环测试服务，拒绝假代理端口 9，禁止域名解析外网。"""
    return NetworkGuard(blocked_ports=(9,))
