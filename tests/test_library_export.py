import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts import library_export as exporter

ITEM_KEYS = {'id', 'title', 'url', 'platform', 'source', 'tags', 'saved_at'}
PRIVATE = 'sk-fixture-private-content-93841'


class LibraryExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='library-export-test-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = self.root / 'items.db'
        self.output = self.root / 'reports'
        self.target = self.output / 'library-export.json'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('''CREATE TABLE items(id TEXT PRIMARY KEY,title TEXT,url TEXT,source TEXT,
                          sources_json TEXT,tags_json TEXT,created_at TEXT,status TEXT,
                          full_text TEXT,article_text TEXT,note TEXT,summary TEXT,comments TEXT,
                          trashed_at TEXT DEFAULT '')''')

    def insert(self, identifier='x:1', **changes):
        row = dict(id=identifier, title='合成标题', url='https://example.test/public', source='manual',
                   sources_json='["manual","intake"]', tags_json='["Agent","本地"]',
                   created_at='2026-10-30T08:00:00+08:00', status='kept',
                   full_text=PRIVATE, article_text=PRIVATE, note=PRIVATE, summary=PRIVATE, comments=PRIVATE)
        row.update(changes)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO items (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))

    def export(self):
        return exporter.export_library(self.db, self.output)

    def test_exact_field_whitelist_and_private_content_never_exported(self):
        self.insert()
        document = self.export()
        self.assertEqual(set(document), {'generated_at', 'count', 'items'})
        self.assertEqual(set(document['items'][0]), ITEM_KEYS)
        self.assertEqual(document['count'], 1)
        self.assertNotIn(PRIVATE, self.target.read_text())
        self.assertEqual(json.loads(self.target.read_text()), document)
        self.assertIsNotNone(datetime.fromisoformat(document['generated_at']).utcoffset())

    def test_only_kept_used_and_sort_by_creation_instant(self):
        self.insert('x:old', created_at='2026-10-30T08:00:00+08:00')
        self.insert('github:new', status='used', created_at='2026-10-30T01:00:00Z')
        for state in ('new', 'dismissed', 'other', '', None):
            self.insert('web:' + str(state), status=state)
        rows = self.export()['items']
        self.assertEqual([row['id'] for row in rows], ['github:new', 'x:old'])
        self.assertEqual([row['platform'] for row in rows], ['github', 'x'])

    def test_metadata_json_null_empty_malformed_and_scalar(self):
        cases = [
            (None, []), ('', []), ('broken-json', []), ('null', []),
            ('[]', []), ('"literal"', ['literal']),
            ('["原样 空格 ",null,1,{},"", "ok"]', ['原样 空格 ', 'ok']),
            ('{}', []), ('123', []),
        ]
        for number, (value, expected) in enumerate(cases):
            self.insert('x:' + str(number), source=None, sources_json=value, tags_json=value)
        rows = {row['id']: row for row in self.export()['items']}
        for number, (_, expected) in enumerate(cases):
            self.assertEqual(rows['x:' + str(number)]['source'], expected)
            self.assertEqual(rows['x:' + str(number)]['tags'], expected)

    def test_trashed_kept_and_used_excluded_null_and_empty_kept(self):
        self.insert('x:empty', trashed_at='')
        self.insert('x:null', trashed_at=None)
        self.insert('x:deleted-kept', trashed_at='2026-10-30T01:00:00Z')
        self.insert('x:deleted-used', status='used', trashed_at='2026-10-30T01:00:00Z')
        self.assertEqual({row['id'] for row in self.export()['items']}, {'x:empty', 'x:null'})

    def test_sources_union_preserves_strings_and_deduplicates(self):
        self.insert(source=' manual ', sources_json='["manual","intake","intake"," manual "]')
        self.assertEqual(self.export()['items'][0]['source'], [' manual ', 'manual', 'intake'])

    def test_string_list_accepts_injected_list_and_rejects_non_lists(self):
        self.assertEqual(exporter.string_list(['one', None, False, 'two']), ['one', 'two'])
        self.assertEqual(exporter.string_list({'private': PRIVATE}), [])

    def test_saved_at_always_has_timezone_and_preserves_aware_instant(self):
        self.insert('x:naive', created_at='2026-10-30 08:00:00')
        self.insert('x:aware', created_at='2026-10-30T02:00:00+02:00')
        for row in self.export()['items']:
            moment = datetime.fromisoformat(row['saved_at'])
            self.assertIsNotNone(moment.utcoffset())
            self.assertEqual(moment.timestamp(), 1793318400)

    def test_invalid_saved_time_fails_without_overwriting(self):
        self.insert(created_at='bad-time-' + PRIVATE)
        self.output.mkdir()
        self.target.write_text('old')
        with self.assertRaisesRegex(ValueError, '素材存入时间无效'):
            self.export()
        self.assertEqual(self.target.read_text(), 'old')
        self.assertEqual(list(self.output.glob('.library-export-*')), [])

    def test_empty_library(self):
        self.assertEqual(self.export()['items'], [])
        self.assertEqual(json.loads(self.target.read_text())['count'], 0)

    def test_default_reports_dir_can_be_injected_without_real_writes(self):
        with patch.object(exporter.grok_bot_radar, 'reports_dir', return_value=self.output) as reports_dir:
            exporter.export_library(self.db)
        reports_dir.assert_called_once_with()
        self.assertTrue(self.target.is_file())

    def test_database_connection_is_readonly_and_database_unchanged(self):
        self.insert()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        connect = sqlite3.connect
        with patch.object(exporter.sqlite3, 'connect', wraps=connect) as spy:
            self.export()
        self.assertTrue(spy.call_args.args[0].endswith('?mode=ro'))
        self.assertTrue(spy.call_args.kwargs['uri'])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_missing_database_not_created_and_old_export_unchanged(self):
        missing = self.root / 'missing.db'
        self.output.mkdir()
        self.target.write_text('old')
        with self.assertRaises(sqlite3.OperationalError):
            exporter.export_library(missing, self.output)
        self.assertFalse(missing.exists())
        self.assertEqual(self.target.read_text(), 'old')

    def test_existing_permissions_preserved_and_new_is_0644(self):
        self.export()
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o644)
        for mode in (0o600, 0o644):
            self.target.chmod(mode)
            self.export()
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), mode)

    def test_atomic_replace_failure_keeps_old_file_and_cleans_temp(self):
        self.export()
        before = self.target.read_bytes()
        with patch.object(exporter.os, 'replace', side_effect=OSError('synthetic replace failure')):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.output.glob('.library-export-*')), [])

    def test_fsync_failure_keeps_old_file_and_cleans_temp(self):
        self.export()
        before = self.target.read_bytes()
        with patch.object(exporter.os, 'fsync', side_effect=OSError('synthetic fsync failure')):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.output.glob('.library-export-*')), [])

    def test_atomic_replace_uses_same_directory_after_fsync(self):
        calls = []
        replace = os.replace
        fsync = os.fsync
        def observe_sync(fd):
            calls.append('fsync')
            return fsync(fd)
        def observe_replace(source, target):
            self.assertEqual(Path(source).parent, Path(target).parent)
            self.assertEqual(calls, ['fsync'])
            self.assertEqual(json.loads(Path(source).read_text())['count'], 0)
            return replace(source, target)
        with patch.object(exporter.os, 'fsync', side_effect=observe_sync), patch.object(exporter.os, 'replace', side_effect=observe_replace):
            self.export()

    def test_unchanged_content_refreshes_generated_at(self):
        first = self.export()
        second = self.export()
        self.assertGreater(second['generated_at'], first['generated_at'])
        self.assertEqual(second['items'], first['items'])

    def test_1000_items_under_one_second(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executemany('INSERT INTO items(id,title,url,source,sources_json,tags_json,created_at,status) VALUES(?,?,?,?,?,?,?,?)',
                           [('x:' + str(n), '合成', 'https://example.test/' + str(n), 'manual', '[]', '[]', '2026-10-30T00:00:00Z', 'kept') for n in range(1000)])
        start = time.perf_counter()
        self.assertEqual(self.export()['count'], 1000)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1)
        print(f'1000 条导出耗时：{elapsed:.4f} 秒')

    def test_cli_success_and_failure_logs_do_not_expose_content(self):
        self.insert()
        args = [sys.executable, str(Path(exporter.__file__)), '--db', str(self.db), '--reports-dir', str(self.output)]
        success = subprocess.run(args, capture_output=True, text=True, check=False)
        self.assertEqual(success.returncode, 0)
        self.assertIn('1 条', success.stdout)
        self.assertNotIn(PRIVATE, success.stdout + success.stderr + self.target.read_text())
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('UPDATE items SET created_at=?', (PRIVATE,))
        before = self.target.read_bytes()
        failure = subprocess.run(args, capture_output=True, text=True, check=False)
        self.assertEqual(failure.returncode, 1)
        self.assertNotIn(PRIVATE, failure.stdout + failure.stderr)
        self.assertEqual(self.target.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
