"""仓库级静态守护：tests/ 里不许再把时钟补丁打在共享的标准库 time 对象上。

依据 review-test-isolation-3 的中 2。调用点一旦写回全局写法（把补丁打在模块手里那个
共享 time 对象或它的函数上），isolation_patches 里的守护根本不参与，它只在 module_time
内部跑；缺陷是真实的：共享 time 被换掉后，别的线程会提前消耗被测模块的有限时钟序列，
被测代码随后拿到 StopIteration。这里用源码扫描把"已经清干净"这个结果守住。

白名单只有 isolation_patches.py：它换的是被测模块自己的 time 名字绑定，不碰共享对象。

本文件扫自己，所以违规样例用 QUOTE / OBJ_* 拼出来，源码里不留完整的违规字面量。
"""
import os
import re
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ALLOWED = {'isolation_patches.py'}

# 只在测试样例里拼引号和对象名，避免本文件扫自己时误判。
QUOTE = chr(39)
OBJ_TIME = 'time'
OBJ_MODULE_TIME = 'executor_module.time'
OBJ_DAEMON_TIME = 'self.daemon.time'
OBJ_MODULE_ATTR_TIME = 'module.time'

PATTERNS = (
    ('共享 time 上的函数被换掉：全局写法打在某模块 time 的函数上',
     r"patch\(\s*['\"][\w.]*\.time\.(monotonic|sleep|time|perf_counter|time_ns)['\"]"),
    ('模块手里的整个 time 名字被换掉：全局写法整份替换 time',
     r"patch\(\s*['\"][\w.]*\.time['\"]"),
    ('直接打标准库 time 上的函数',
     r"patch\(\s*['\"]time\.(monotonic|sleep|time|perf_counter|time_ns)['\"]"),
    ('直接替换标准库 time 模块',
     r"patch\(\s*['\"]time['\"]"),
    ('patch.object 打在共享 time 对象上',
     r"patch\.object\(\s*[\w.]+\.time\s*,"),
    ('patch.object 直接打在标准库 time 上',
     r"patch\.object\(\s*time\s*,"),
    ('直接改共享 time 对象的属性',
     r"setattr\(\s*(?:[\w.]+\.)?time\s*,"),
    ('清空或改写共享 time 对象',
     r"patch\.dict\(\s*['\"]?[\w.]*time\.__dict__"),
)

# 每个禁用模式都要能命中自己的违规样例，防止正则写错导致扫描空跑。
# 装饰器写法（@patch 后面接括号）和 with 写法共用同一套模式，因为都含 patch 加左括号。
VIOLATIONS = {
    r"patch\(\s*['\"][\w.]*\.time\.(monotonic|sleep|time|perf_counter|time_ns)['\"]": (
        "with patch(%sassistant_remio.time.monotonic%s, side_effect=[100, 200]):" % (QUOTE, QUOTE),
        "@patch(%sgeneration.service.time.sleep%s)" % (QUOTE, QUOTE),
        "with mock.patch(%swechat_source_client.time.time%s, return_value=5.0):" % (QUOTE, QUOTE),
    ),
    r"patch\(\s*['\"][\w.]*\.time['\"]": (
        "with patch(%sassistant_remio.time%s):" % (QUOTE, QUOTE),
        "@patch(%spublishing.local_jobs.time%s)" % (QUOTE, QUOTE),
    ),
    r"patch\(\s*['\"]time\.(monotonic|sleep|time|perf_counter|time_ns)['\"]": (
        "with patch(%stime.sleep%s):" % (QUOTE, QUOTE),
        "@patch(%stime.monotonic%s, return_value=5.0)" % (QUOTE, QUOTE),
    ),
    r"patch\(\s*['\"]time['\"]": (
        "with patch(%stime%s) as fake_time:" % (QUOTE, QUOTE),
    ),
    r"patch\.object\(\s*[\w.]+\.time\s*,": (
        "with patch.object(%s, %smonotonic%s):" % (OBJ_MODULE_TIME, QUOTE, QUOTE),
        "@patch.object(%s, %stime%s, return_value=1001)" % (OBJ_DAEMON_TIME, QUOTE, QUOTE),
    ),
    r"patch\.object\(\s*time\s*,": (
        "with patch.object(%s, %ssleep%s):" % (OBJ_TIME, QUOTE, QUOTE),
    ),
    r"setattr\(\s*(?:[\w.]+\.)?time\s*,": (
        "setattr(%s, %smonotonic%s, lambda: 5.0)" % (OBJ_TIME, QUOTE, QUOTE),
        "setattr(%s, %ssleep%s, skip_sleep)" % (OBJ_MODULE_ATTR_TIME, QUOTE, QUOTE),
    ),
    r"patch\.dict\(\s*['\"]?[\w.]*time\.__dict__": (
        "with patch.dict(%sgeneration.service.time.__dict__%s, {'sleep': skip_sleep}):" % (QUOTE, QUOTE),
    ),
}

# 合规写法不能被误判。
COMPLIANT = (
    "with module_time(%sassistant_remio%s, monotonic=clock_sequence([100, 200])):" % (QUOTE, QUOTE),
    "with module_time(_clue_source, time=lambda: 1000):",
    "with patch.object(module, %stime%s, binding):" % (QUOTE, QUOTE),
    "with patch.object(executor_module, %sbackend_slots%s, return_value=%sbusy%s):" % (QUOTE, QUOTE, QUOTE, QUOTE),
    "with patch(%swechat_radar_pipeline.backend_slots%s, return_value=%sready%s):" % (QUOTE, QUOTE, QUOTE, QUOTE),
    "with patch.dict(os.environ, {'ARTICLE_STUDIO_R2_CONFIG': path}):",
    "setattr(module, %sthreading%s, ThreadingBinding(original, constructor))" % (QUOTE, QUOTE),
)


class NoGlobalTimePatchTests(unittest.TestCase):
    def _python_files(self):
        for root, dirs, files in os.walk(TESTS_DIR):
            dirs[:] = sorted(d for d in dirs if d != '__pycache__')
            for name in sorted(files):
                if name.endswith('.py') and name not in ALLOWED:
                    yield os.path.join(root, name)

    def test_scan_actually_covers_tests_tree(self):
        names = {os.path.relpath(path, TESTS_DIR) for path in self._python_files()}
        self.assertGreater(len(names), 100, '扫描应覆盖 tests/ 下全部 .py')
        for expected in ('test_clue_retry.py', 'test_media_http_transport.py',
                         'test_schedule_stability.py', 'e2e/server_fixture.py'):
            self.assertIn(expected, names)
        self.assertNotIn('isolation_patches.py', names, '白名单文件不参与扫描')

    def test_patterns_reject_violations(self):
        for pattern, samples in VIOLATIONS.items():
            for sample in samples:
                self.assertRegex(sample, pattern)

    def test_patterns_accept_compliant_forms(self):
        for line in COMPLIANT:
            for label, pattern in PATTERNS:
                self.assertNotRegex(line, pattern, '%s 误判合规写法：%s' % (label, line))

    def test_no_global_time_patch_in_tests(self):
        offenders = []
        for path in sorted(self._python_files()):
            with open(path, encoding='utf-8') as handle:
                source = handle.read()
            for label, pattern in PATTERNS:
                for match in re.finditer(pattern, source):
                    line = source.count('\n', 0, match.start()) + 1
                    offenders.append('%s:%d %s -> %s' % (
                        os.path.relpath(path, TESTS_DIR), line, label, match.group(0)))
        self.assertEqual([], offenders,
                         'tests/ 里不允许把时钟补丁打在共享标准库 time 上，改用 '
                         'isolation_patches.module_time：\n' + '\n'.join(offenders))


if __name__ == '__main__':
    unittest.main()
