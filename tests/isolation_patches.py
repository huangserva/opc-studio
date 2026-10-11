"""把线程补丁限制到模块绑定或目标 worker，并在补丁期间验证无关线程。

module_time 只替换被测模块自己的 time 绑定。标准库 time 保持原函数。

跨模块的时钟要分别打：facade 不会跨模块传播。
module_time('clue_pipeline', ...) 换的只是 clue_pipeline 这个模块自己手里的 time
名字，被它调用的 source_pi_executor、generation.service 等别的模块仍读真实时钟。
一条调用链跨几个模块、几个模块各自读时钟，就要对每个模块各打一次 module_time；
漏打的那一侧不会报错，只会静默用真实时间。
"""
import importlib
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── 公开克隆守卫（hygiene-4）────────────────────────────────────────────────
# 公开仓库不提交下列本机资源：coordination/（私有协调证据）、work/（.gitignore
# 第 13 行）、workspace/（本机数据）。依赖它们的用例在干净克隆里必然失败。
# 判定一律用「文件/模块是否存在」，不用环境变量猜；存在时行为与从前逐字一致。

REPLAY_TOOL = REPO_ROOT / 'coordination' / 'tools' / 'replay_xradar_heat.py'
WORK_BACKEND = REPO_ROOT / 'work' / 'image-reference-fix' / 'backend'

REPLAY_TOOL_REASON = (
    '需要本机协调工具 coordination/tools/replay_xradar_heat.py；'
    'coordination/ 是私有协调证据，不进公开仓库（参见 load-manifest-v2 §3.2）。')
WORK_BACKEND_REASON = (
    '需要本机工作副本 work/image-reference-fix/backend/；'
    '/work/ 被 .gitignore 排除，不进公开仓库。')
MIGRATOR_REASON = (
    '需要本机迁移脚本 coordination/migrate-knowledge-link-host.py；'
    'coordination/ 是私有协调证据，不进公开仓库（load-manifest-v2 §3.2）。')
RECOVERY_SCRIPT_REASON = (
    '需要本机恢复脚本 coordination/restore_clues_retry_20s.py；'
    'coordination/ 是私有协调证据，不进公开仓库（load-manifest-v2 §3.2）。')
WECHAT_SOURCE_REASON = (
    '需要微信来源插件（ARTICLE_STUDIO_WECHAT_MODULE 或 workspace/sources/wechat.json）；'
    '两者都是本机资源，不进公开仓库，干净克隆里没有该模块。')


def replay_tool_available():
    """协调工具是否在本机。只看文件在不在，不 import，避免拉起副作用。"""
    return REPLAY_TOOL.is_file()


def work_backend_available():
    """work/image-reference-fix/backend/ 是否在本机。"""
    return WORK_BACKEND.is_dir()


def wechat_source_available():
    """微信来源插件能否真的加载出来。

    直接问 loader 而不是自己判断配置文件：loader 的判定口径（环境变量 →
    workspace/sources/wechat.json → module_dir 下有没有 wechat_source/__init__.py）
    才是产品真实在用的那套，复制一份只会漂移。
    """
    try:
        from wechat_source_loader import source_module
        source_module('clue_pipeline')
    except Exception:
        return False
    return True


def require_replay_tool():
    if not replay_tool_available():
        raise unittest.SkipTest(REPLAY_TOOL_REASON)


def require_work_backend():
    if not work_backend_available():
        raise unittest.SkipTest(WORK_BACKEND_REASON)


def require_wechat_source():
    if not wechat_source_available():
        raise unittest.SkipTest(WECHAT_SOURCE_REASON)

# 模块导入时先记下标准库原函数。守护用身份断言比对，不看返回值像不像合理时钟，
# 所以哪怕外层已经存在一个返回合理浮点的全局 time 补丁，也会在这里被抓到。
_ORIG_CLOCKS = {'monotonic': time.monotonic, 'time': time.time, 'sleep': time.sleep}
_CLOCK_NAMES = ('monotonic', 'time', 'sleep')

# clock_sequence 给序列函数挂上这个属性，守护据此确认序列没被别处消耗。
_SEQUENCE_ATTR = '_isolation_clock_sequence'


def check_unrelated_thread():
    done = threading.Event()
    worker = threading.Thread(target=done.set, name='isolation-unrelated')
    worker.start()
    worker.join(2)
    assert done.is_set() and not worker.is_alive(), '无关线程应正常启动和结束'


class ThreadingBinding:
    def __init__(self, original, constructor):
        self.original = original
        self.Thread = constructor

    def __getattr__(self, name):
        return getattr(self.original, name)


@contextmanager
def module_thread(module_name, constructor=None):
    """仅替换被测模块绑定；Event、锁及其他模块的 Thread 保持真实。"""
    module = importlib.import_module(module_name) if isinstance(module_name, str) else module_name
    constructor = MagicMock() if constructor is None else constructor
    original = module.threading
    with patch.object(module, 'threading', ThreadingBinding(original, constructor)):
        check_unrelated_thread()
        assert module.threading.Event is original.Event
        assert module.threading.RLock is original.RLock
        yield constructor
        if isinstance(constructor, MagicMock):
            assert constructor.called, "被测模块应使用被替换的 Thread 构造器"


@contextmanager
def target_start(predicate, failure):
    """目标可按归属、target 或名称匹配，其他线程透传原 start。"""
    original = threading.Thread.start

    def start(worker):
        if predicate(worker):
            if isinstance(failure, BaseException):
                raise failure
            return failure(worker)
        return original(worker)

    with patch.object(threading.Thread, 'start', start):
        check_unrelated_thread()
        yield original


def skip_sleep(*_args, **_kwargs):
    """被测模块的 time.sleep 立即返回。"""
    return None


def clock_sequence(values):
    """有限序列只在被测模块调用时推进，其他线程的标准库时钟不会消耗它。

    序列用尽时抛 AssertionError 指向测试，不让 StopIteration 钻进产品代码里
    变成一句和测试无关的报错。
    """
    values = list(values)
    state = {'calls': 0}

    def clock():
        if state['calls'] >= len(values):
            raise AssertionError(
                '时钟序列只有 %d 个值，已被第 %d 次调用用尽' % (len(values), state['calls'] + 1)
            )
        value = values[state['calls']]
        state['calls'] += 1
        return value

    setattr(clock, _SEQUENCE_ATTR, {'total': len(values), 'state': state})
    return clock


class TimeBinding:
    def __init__(self, original, overrides):
        self._original = original
        self._overrides = overrides

    def __getattr__(self, name):
        try:
            return self._overrides[name]
        except KeyError:
            return getattr(self._original, name)


def _sequence_progress(overrides):
    """序列型覆写当前消耗到第几格；不是 clock_sequence 的覆写记为 None。"""
    progress = {}
    for name, fake in overrides.items():
        info = getattr(fake, _SEQUENCE_ATTR, None)
        progress[name] = None if info is None else (info['total'], info['state']['calls'])
    return progress


def _read_stdlib_clocks():
    """在另一个函数里读标准库时钟，等价于被测模块之外的调用点（含其他线程）。"""
    for _ in range(8):
        time.monotonic()
        time.time()
        time.sleep(0)


def _stdlib_clock_still_real(overrides, stage):
    """补丁期间标准库 monotonic、time 和 sleep 仍是导入时记下的原函数。"""
    for name in _CLOCK_NAMES:
        assert getattr(time, name) is _ORIG_CLOCKS[name], '%s：标准库 time.%s 不应被替换' % (stage, name)
    started = time.monotonic()
    assert isinstance(started, float), '%s：标准库 time.monotonic 应返回真实浮点时钟' % stage
    time.sleep(0)
    assert time.monotonic() >= started, '%s：标准库 time.sleep(0) 应仍是真实等待' % stage
    wall = time.time()
    assert isinstance(wall, float) and wall > 1000000000, '%s：标准库 time.time 应仍是真实墙钟' % stage
    # 有限序列不被别处消耗：上面三个名字刚确认还是标准库原函数，序列只挂在 facade 上，
    # 别处的 time.monotonic() 拿不到它。这里再走一遍标准库路径实测，序列一格都不该动。
    # 不起线程，所以没有 join 超时那种偶发点。
    before = _sequence_progress(overrides)
    _read_stdlib_clocks()
    assert _sequence_progress(overrides) == before, \
        '%s：其他代码调用标准库时钟不应消耗被测模块的时钟序列' % stage


@contextmanager
def module_time(module_name, **overrides):
    """只替换被测模块的 time 绑定；monotonic、time、sleep 可单独覆写，其余透传。"""
    if not overrides or set(overrides) - set(_CLOCK_NAMES):
        raise ValueError('module_time 只覆写 monotonic、time 或 sleep')
    module = importlib.import_module(module_name) if isinstance(module_name, str) else module_name
    binding = TimeBinding(module.time, overrides)
    with patch.object(module, 'time', binding):
        _stdlib_clock_still_real(overrides, '进入')
        for name, fake in overrides.items():
            assert getattr(module.time, name) is fake
        yield binding
        _stdlib_clock_still_real(overrides, '退出')
