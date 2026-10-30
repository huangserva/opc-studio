"""H2: isolated daily catch-up and launch wrapper receipts; no live collection."""
from datetime import datetime
import json
import os
from pathlib import Path
import plistlib
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from scheduled_tasks import ScheduledTasks
from tests.isolation_patches import module_time


BODY = ('set -o pipefail\n/bin/zsh scripts/launchd/run-video-radar.sh 2>&1 '
        '| /usr/bin/tee -a workspace/video-radar-launchd.log')


class VideoRadarScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='h2-schedule-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'workspace').mkdir()
        (self.root / 'scripts/launchd').mkdir(parents=True)
        source = Path(__file__).resolve().parents[1] / 'scripts/launchd/run-video-radar.sh'
        shutil.copyfile(source, self.root / 'scripts/launchd/run-video-radar.sh')
        self.plist = self.root / 'synthetic-service.plist'
        self.plist.write_bytes(plistlib.dumps({'EnvironmentVariables': {'AISA_API_KEY': 'synthetic-h2-only'}}))
        self.scheduler = ScheduledTasks(self.root / 'workspace', autostart=False)
        self.addCleanup(lambda: self.scheduler.close())

    def save(self, body=BODY):
        return self.scheduler.save(dict(name='视频雷达隔离测试', kind='script', body=body,
            cwd=str(self.root), frequency='daily', at='21:20', minutes=60,
            weekdays=list(range(7)), timeout=5, enabled=True, allow_commands=False))['id']

    def wait(self, run_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = self.scheduler.get_run(run_id)
            if run['status'] != 'running':
                return run
            time.sleep(.02)
        self.fail('isolated script did not finish')

    def test_restart_after_2120_catches_up_once(self):
        identity = self.save("printf '补跑一次'")
        due = datetime.fromisoformat('2026-10-29T21:20:00+08:00').timestamp()
        with self.scheduler.db() as db:
            db.execute('UPDATE tasks SET next_run=? WHERE id=?', (due, identity))
        self.scheduler.close()
        self.scheduler = ScheduledTasks(self.root / 'workspace', autostart=False)
        resumed = datetime.fromisoformat('2026-10-30T21:25:00+08:00').timestamp()
        with module_time('scheduled_tasks', time=lambda: resumed):
            self.scheduler.tick()
            run_id = self.scheduler.runs()['runs'][0]['id']
            self.assertEqual(self.wait(run_id)['status'], 'succeeded')
            self.scheduler.tick()
            self.scheduler.tick()
        runs = self.scheduler.runs()['runs']
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['trigger'], 'scheduled')
        self.assertEqual(runs[0]['output'], '补跑一次')
        expected = datetime.fromisoformat('2026-10-31T21:20:00+08:00').timestamp()
        self.assertEqual(self.scheduler.listing()['tasks'][0]['next_run'], expected)
        self.scheduler.close()
        self.scheduler = ScheduledTasks(self.root / 'workspace', autostart=False)
        with module_time('scheduled_tasks', time=lambda: resumed):
            self.scheduler.tick()
        self.assertEqual(len(self.scheduler.runs()['runs']), 1)

    def test_bad_python_preserves_error_exit_and_other_tasks(self):
        identity = self.save()
        missing = str(self.root / 'python-does-not-exist')
        with patch.dict(os.environ, {'ARTICLE_STUDIO_ROOT': str(self.root),
                'ARTICLE_STUDIO_SERVICE_PLIST': str(self.plist), 'ARTICLE_STUDIO_PYTHON': missing}):
            run = self.wait(self.scheduler.control(identity, 'run')['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['exit_code'], 127)
        self.assertIn('no such file or directory', run['output'])
        self.assertIn(missing, run['output'])
        self.assertNotIn('synthetic-h2-only', run['output'])
        self.assertEqual((self.root / 'workspace/video-radar-launchd.log').read_text(), run['output'])
        other = self.save("printf '其他任务正常'")
        self.assertEqual(self.wait(self.scheduler.control(other, 'run')['id'])['status'], 'succeeded')

    def test_success_appends_one_json_and_receipt_without_key(self):
        fake_python = self.root / 'synthetic-python'
        fake_python.write_text('#!/bin/sh\nprintf \'{"synthetic":true}\\n\'\n')
        fake_python.chmod(0o700)
        log = self.root / 'workspace/video-radar-launchd.log'
        log.write_text('{"before":true}\n')
        identity = self.save()
        with patch.dict(os.environ, {'ARTICLE_STUDIO_ROOT': str(self.root),
                'ARTICLE_STUDIO_SERVICE_PLIST': str(self.plist), 'ARTICLE_STUDIO_PYTHON': str(fake_python)}):
            run = self.wait(self.scheduler.control(identity, 'run')['id'])
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual(run['exit_code'], 0)
        self.assertEqual(json.loads(run['output']), {'synthetic': True})
        self.assertEqual(log.read_text().splitlines(), ['{"before":true}', '{"synthetic":true}'])
        self.assertNotIn('synthetic-h2-only', log.read_text())
        self.assertNotIn('synthetic-h2-only', self.scheduler.db_path.read_bytes().decode(errors='ignore'))


if __name__ == '__main__':
    unittest.main()
