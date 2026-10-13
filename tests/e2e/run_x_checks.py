"""Run isolated browser checks with fresh fake-X state, never production credentials."""
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

root = Path(__file__).resolve().parents[2]
for scripts in [('x_official.cjs', 'x_integrated.cjs'), ('feedback_redesign.cjs',)]:
    proc = subprocess.Popen([sys.executable, str(root / 'tests/e2e/x_official_fixture.py')], cwd=root)
    try:
        for _ in range(40):
            if proc.poll() is not None:
                raise RuntimeError('Isolated fixture exited')
            try:
                urllib.request.urlopen('http://127.0.0.1:8789/api/x-official/status', timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError('Isolated fixture unavailable')
        for script in scripts:
            subprocess.run(['node', script], cwd=root / 'tests/e2e', check=True)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
