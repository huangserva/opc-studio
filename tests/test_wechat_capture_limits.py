"""只启动临时worker，验证文件上限及原参数/锁描述符继承。"""
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import unittest
from wechat_capture import worker_command

LIMITS_WORKER = "import json,resource; print(json.dumps(resource.getrlimit(resource.RLIMIT_NOFILE)))"

# 在子 shell 里把 soft 降到 argv[2]（hard 不动），再由它启动 worker_command；
# 这样能观察 bootstrap 对「父 soft」的处理，且不改动测试进程自己的上限。
PARENT_SOFT_HELPER = (
    "import resource,json,subprocess,sys;"
    "hard=resource.getrlimit(resource.RLIMIT_NOFILE)[1];"
    "resource.setrlimit(resource.RLIMIT_NOFILE,(int(sys.argv[2]),hard));"
    "print(json.dumps({'parent':list(resource.getrlimit(resource.RLIMIT_NOFILE)),"
    "'child':json.loads(subprocess.check_output(json.loads(sys.argv[1]),text=True))}),end='')")


class CaptureLimitTests(unittest.TestCase):
    def expected_child_soft(self, parent_soft, hard):
        return max(parent_soft, 4096 if hard == resource.RLIM_INFINITY else min(4096, hard))

    def run_with_parent_soft(self, soft):
        hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
        if hard != resource.RLIM_INFINITY and hard < soft:
            self.skipTest(f'hard limit {hard} below requested parent soft {soft}')
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / 'worker.py'
            worker.write_text(LIMITS_WORKER)
            command = worker_command(sys.executable, worker)
            helper = subprocess.run([sys.executable, '-c', PARENT_SOFT_HELPER, json.dumps(command), str(soft)],
                                    capture_output=True, text=True)
            self.assertEqual(helper.returncode, 0, helper.stderr)
            return json.loads(helper.stdout)

    def test_child_limit_and_arguments_without_changing_parent(self):
        original = resource.getrlimit(resource.RLIMIT_NOFILE)
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / 'worker.py'
            worker.write_text("import json,os,resource,sys; print(json.dumps({'limits':resource.getrlimit(resource.RLIMIT_NOFILE),'args':sys.argv,'fd':os.fstat(int(sys.argv[1])).st_mode}))")
            fd = os.open(worker, os.O_RDONLY)
            try:
                result = json.loads(subprocess.check_output(worker_command(sys.executable, worker, str(fd), 'synthetic'), pass_fds=(fd,), text=True))
            finally:
                os.close(fd)
            soft, hard = result['limits']
            self.assertEqual(soft, self.expected_child_soft(original[0], hard))
            self.assertLessEqual(soft, hard)
            self.assertEqual(result['args'], [str(worker), str(fd), 'synthetic'])
            self.assertTrue(result['fd'])
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), original)

    def test_small_hard_limit_is_respected(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / 'worker.py'
            worker.write_text('import resource,json; print(json.dumps(resource.getrlimit(resource.RLIMIT_NOFILE)))')
            command = worker_command(sys.executable, worker)
            # Only this new helper child lowers its hard limit; parent stays intact.
            helper = "import resource,subprocess,json,sys;resource.setrlimit(resource.RLIMIT_NOFILE,(64,64));print(subprocess.check_output(json.loads(sys.argv[1]),text=True),end='')"
            result = json.loads(subprocess.check_output([sys.executable, '-c', helper, json.dumps(command)], text=True))
            self.assertEqual(result, [64, 64])

    def test_bootstrap_raises_a_low_parent_soft_limit(self):
        # 删掉 bootstrap 里的 setrlimit 时，子进程只会继承 256，本用例必定失败。
        report = self.run_with_parent_soft(256)
        parent_soft, parent_hard = report['parent']
        child_soft, child_hard = report['child']
        self.assertEqual(parent_soft, 256)
        self.assertEqual(child_soft, self.expected_child_soft(parent_soft, parent_hard))
        self.assertGreater(child_soft, parent_soft)
        self.assertLessEqual(child_soft, child_hard)
        self.assertEqual(child_hard, parent_hard)

    def test_bootstrap_never_lowers_a_higher_parent_soft_limit(self):
        # 去掉 max(soft, …) 时，子 soft 会被调低到 4096，本用例必定失败。
        report = self.run_with_parent_soft(8192)
        parent_soft, parent_hard = report['parent']
        child_soft, child_hard = report['child']
        self.assertEqual(child_soft, parent_soft)
        self.assertGreater(child_soft, 4096)
        self.assertLessEqual(child_soft, child_hard)
        self.assertEqual(child_hard, parent_hard)

    def test_worker_script_directory_is_the_import_root(self):
        # 与直接 `python worker.py` 等价：脚本目录是 sys.path[0]，同级模块可直接 import。
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary).resolve()
            (folder / 'sibling_marker.py').write_text("MARK = 'sibling-import-ok'\n")
            worker = folder / 'worker.py'
            worker.write_text("import json,sys,sibling_marker; print(json.dumps({'path0':sys.path[0],'mark':sibling_marker.MARK}))")
            result = json.loads(subprocess.check_output(worker_command(sys.executable, worker), text=True))
            self.assertEqual(result['path0'], str(folder))
            self.assertEqual(result['mark'], 'sibling-import-ok')
