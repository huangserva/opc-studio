"""B1-d: real zsh/plutil -> isolated child; synthetic plists, no network or secrets."""
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / 'scripts/launchd/run-radar-monitor.sh'
KEYS = ('FEISHU_RADAR_WEBHOOK', 'FEISHU_RADAR_SECRET')
# Exact B1-c2 launcher, retained as an independent compatibility oracle.
BASELINE = '''#!/bin/zsh
# Template: set ARTICLE_STUDIO_ROOT / ARTICLE_STUDIO_PYTHON before installation.
set -eu
umask 077
export TZ=Asia/Shanghai
TASK_ROOT="${ARTICLE_STUDIO_ROOT:-/Users/huangzongning/development/article-studio}"
TASK_PYTHON="${ARTICLE_STUDIO_PYTHON:-${TASK_ROOT}/../article-importer/.venv/bin/python}"
TASK_SERVICE_PLIST="${ARTICLE_STUDIO_SERVICE_PLIST:-${HOME}/Library/LaunchAgents/com.servasyy.article-studio.plist}"
# No tracing, no plaintext credentials, no child output containing API errors.
AISA_API_KEY="$(/usr/bin/plutil -extract EnvironmentVariables.AISA_API_KEY raw -o - "$TASK_SERVICE_PLIST")"
export AISA_API_KEY
cd "$TASK_ROOT"
exec "$TASK_PYTHON" scripts/radar_monitor.py --root "$TASK_ROOT"
'''


class FeishuLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='radar-b1d-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.plist = self.folder / 'service.plist'
        self.capture = self.folder / 'child.json'
        self.baseline = self.folder / 'before.sh'
        self.baseline.write_text(BASELINE)
        self.child = self.folder / 'python-probe'
        self.child.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
from pathlib import Path
keys = ('FEISHU_RADAR_WEBHOOK', 'FEISHU_RADAR_SECRET')
record = {'argv': sys.argv[1:], 'env': {k: os.environ[k] for k in keys if k in os.environ},
          'aisa': os.environ.get('AISA_API_KEY')}
if os.environ.get('B1D_PROBE_CONFIG') == '1':
    sys.path.insert(0, os.getcwd())
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('Network forbidden in launcher probe')
    from scripts import radar_feishu
    socket.socket = forbidden
    record['config'] = list(radar_feishu._config()[:2])
    record['executable'] = sys.executable
Path(os.environ['B1D_CAPTURE']).write_text(json.dumps(record, ensure_ascii=True))
sys.exit(int(os.environ.get('B1D_EXIT', '0')))
''')
        self.child.chmod(0o700)

    def run_launcher(self, values=None, *, inherited=None, aisa=True, missing=False,
                     exit_code=0, before=False, config=False, fmt=plistlib.FMT_XML):
        values = values or {}
        data = dict(values)
        if aisa:
            data['AISA_API_KEY'] = 'offline-b1d-aisa-fixture'
        if missing:
            self.plist.unlink(missing_ok=True)
        else:
            self.plist.write_bytes(plistlib.dumps({'EnvironmentVariables': data}, fmt=fmt))
        self.capture.unlink(missing_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in (*KEYS, 'AISA_API_KEY')}
        env.update(ARTICLE_STUDIO_ROOT=str(ROOT), ARTICLE_STUDIO_SERVICE_PLIST=str(self.plist),
                   ARTICLE_STUDIO_PYTHON=str(self.child), B1D_CAPTURE=str(self.capture),
                   B1D_EXIT=str(exit_code), B1D_PROBE_CONFIG=str(int(config)))
        env.update(inherited or {})
        result = subprocess.run(['/bin/zsh', str(self.baseline if before else LAUNCHER)],
                                env=env, capture_output=True, timeout=15)
        record = json.loads(self.capture.read_text()) if self.capture.exists() else None
        # Every execution checks logs and argv, including compatibility/error cases.
        forbidden = []
        def collect(value):
            if isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)
            elif value is not None and str(value):
                forbidden.append(str(value))
        collect(values)
        collect(inherited or {})
        for value in forbidden:
            encoded = value.encode()
            self.assertTrue(encoded not in result.stdout and encoded not in result.stderr,
                            'Synthetic configuration leaked to stdout/stderr')
            if record:
                self.assertTrue(all(value not in arg for arg in record['argv']),
                                'Synthetic configuration leaked to child argv')
        if record:
            self.assertEqual(record['argv'], ['scripts/radar_monitor.py', '--root', str(ROOT)])
            self.assertTrue(record['aisa'] == 'offline-b1d-aisa-fixture', 'AISA value changed')
        return result, record

    def assert_values(self, record, expected):
        self.assertIsNotNone(record, 'Child did not execute')
        # Do not include synthetic credentials in assertion output, even on failure.
        actual = {k: v.encode() for k, v in record['env'].items()}
        self.assertTrue(actual == {k: v.encode() for k, v in expected.items()},
                        'Credential presence or bytes differ')

    def special_values(self):
        return {key: 'fixture-' + str(i) + '-&=?%# 单 双\'"$`\\中文-' + '长' * 1600 + '-end'
                for i, key in enumerate(KEYS)}

    def test_01_both_strings_preserve_all_bytes(self):
        values = self.special_values()
        self.assertTrue(all(len(v.encode()) >= 4096 for v in values.values()))
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            with self.subTest(format=fmt.name):
                result, record = self.run_launcher(values, fmt=fmt)
                self.assertEqual(result.returncode, 0)
                self.assert_values(record, values)
                self.assertEqual(result.stderr, b'')

    def test_02_both_missing_child_exit_status(self):
        for code in (0, 37):
            with self.subTest(code=code):
                result, record = self.run_launcher(exit_code=code)
                self.assertEqual(result.returncode, code)
                self.assert_values(record, {})
                self.assertEqual(result.stderr, b'')

    def test_03_webhook_only(self):
        values = {KEYS[0]: 'fixture-webhook-only'}
        result, record = self.run_launcher(values)
        self.assertEqual(result.returncode, 0)
        self.assert_values(record, values)

    def test_04_empty_strings_are_not_exported(self):
        for values in ({k: '' for k in KEYS}, {}):
            result, record = self.run_launcher(values, inherited={k: '' for k in KEYS})
            self.assertEqual(result.returncode, 0)
            self.assert_values(record, {})
            self.assertEqual(result.stderr, b'')

    def test_05_wrong_types_warn_once_without_values(self):
        bad = (918273645, {'private': 'fixture-dict-value'}, ['fixture-array-value'], True)
        for key in KEYS:
            for value in bad:
                with self.subTest(key=key, kind=type(value).__name__):
                    result, record = self.run_launcher({key: value})
                    self.assertEqual(result.returncode, 0)
                    self.assert_values(record, {})
                    self.assertEqual(result.stderr.decode().splitlines(),
                                     ['radar-monitor: ' + key + ' 配置类型不是字符串，已忽略'])

    def test_06_inherited_nonempty_wins_empty_falls_back(self):
        values = {k: 'plist-fixture-' + str(i) for i, k in enumerate(KEYS)}
        inherited = {k: 'inherited-fixture-' + str(i) for i, k in enumerate(KEYS)}
        for overrides, expected in ((inherited, inherited), ({k: '' for k in KEYS}, values),
                                    ({KEYS[0]: inherited[KEYS[0]], KEYS[1]: ''},
                                     {KEYS[0]: inherited[KEYS[0]], KEYS[1]: values[KEYS[1]]})):
            result, record = self.run_launcher(values, inherited=overrides)
            self.assertEqual(result.returncode, 0)
            self.assert_values(record, expected)
        # An inherited credential also bypasses an invalid plist value without a warning.
        result, record = self.run_launcher({k: False for k in KEYS}, inherited=inherited)
        self.assertEqual(result.returncode, 0)
        self.assert_values(record, inherited)
        self.assertEqual(result.stderr, b'')

    def test_07_missing_aisa_matches_before(self):
        old, old_record = self.run_launcher(aisa=False, before=True)
        new, new_record = self.run_launcher(aisa=False)
        self.assertNotEqual(old.returncode, 0)
        self.assertEqual(new.returncode, old.returncode)
        self.assertIsNone(old_record)
        self.assertIsNone(new_record)

    def test_08_missing_plist_matches_before(self):
        old, old_record = self.run_launcher(missing=True, before=True)
        new, new_record = self.run_launcher(missing=True)
        self.assertNotEqual(old.returncode, 0)
        self.assertEqual(new.returncode, old.returncode)
        self.assertIsNone(old_record)
        self.assertIsNone(new_record)

    def test_09_no_trace_dump_or_credential_argv(self):
        script = LAUNCHER.read_text()
        self.assertIsNone(re.search(r'^\s*set\s+.*(?:-x|xtrace)', script, re.M))
        self.assertIsNone(re.search(r'^\s*(?:env|printenv)(?:\s|$)', script, re.M))
        self.assertIsNone(re.search(r'^\s*echo\b', script, re.M))
        # Also preserve leading/trailing spaces, tabs, CR, and trailing newlines.
        values = {KEYS[0]: ' fixture-line-one\nline-two\t\r\n\n',
                  KEYS[1]: ' fixture-secret-tail.\n\n'}
        result, record = self.run_launcher(values, fmt=plistlib.FMT_BINARY)
        self.assertEqual(result.returncode, 0)
        self.assert_values(record, values)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(result.stderr, b'')

    def test_10_real_python_sender_config_without_network(self):
        values = self.special_values()
        result, record = self.run_launcher(values, config=True)
        self.assertEqual(result.returncode, 0)
        self.assert_values(record, values)
        self.assertTrue(record['config'] == [values[k] for k in KEYS], 'Sender config differs')
        self.assertEqual(Path(record['executable']).absolute(), Path(sys.executable).absolute())

    def test_11_zsh_syntax_and_errexit_missing_optional_keys(self):
        result = subprocess.run(['/bin/zsh', '-n', str(LAUNCHER)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('set -eu', LAUNCHER.read_text())
        result, record = self.run_launcher()
        self.assertEqual(result.returncode, 0)
        self.assert_values(record, {})
        self.assertEqual(result.stderr, b'')


if __name__ == '__main__':
    unittest.main()
