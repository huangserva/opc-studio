import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('receipt_recovery',Path(__file__).resolve().parents[1]/'scripts/recover_wechat_receipts.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class RecoveryTests(unittest.TestCase):
    def test_restores_exact_terminal_receipt_with_backup_and_preserves_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'schedules').mkdir();(root/'wechat-source').mkdir()
            rid='a'*32;tid='b'*32
            original=json.dumps({'source_run_id':rid,'state':'来源任务停止'})
            receipt=json.dumps({'processed':274,'clues':['c'*32],'has_more':True})
            with sqlite3.connect(root/'schedules/tasks.db') as db:
                db.execute('CREATE TABLE runs(id,task_id,status,output,snapshot)')
                db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(rid,tid,'timeout',original,json.dumps({'kind':'wechat_clues'})))
            with sqlite3.connect(root/'wechat-source/jobs.db') as db:
                db.execute('CREATE TABLE jobs(id,task_id,kind,status,output)')
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',(rid,tid,'clues','cancelled',receipt))
            self.assertEqual(module.recover(root)[0]['processed'],274)
            self.assertEqual(list((root/'schedules').glob('*.json')),[])
            module.recover(root,True)
            with sqlite3.connect(root/'schedules/tasks.db') as db:
                self.assertEqual(db.execute('SELECT status,output FROM runs').fetchone(),('timeout',receipt))
            backup=next((root/'schedules').glob('receipt-recovery-*.json'))
            self.assertEqual(json.loads(backup.read_text())[0]['original_output'],original)
            self.assertEqual(backup.stat().st_mode & 0o777,0o600)
            self.assertEqual(module.recover(root,True),[])

    def test_wrong_task_or_unfinished_source_never_replaces_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'schedules').mkdir();(root/'wechat-source').mkdir()
            with sqlite3.connect(root/'schedules/tasks.db') as db:
                db.execute('CREATE TABLE runs(id,task_id,status,output,snapshot)')
                for rid in ('a','b'):
                    db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(rid*32,'c'*32,'timeout',json.dumps({'source_run_id':rid*32,'state':'stopped'}),json.dumps({'kind':'wechat_clues'})))
            with sqlite3.connect(root/'wechat-source/jobs.db') as db:
                db.execute('CREATE TABLE jobs(id,task_id,kind,status,output)')
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',('a'*32,'d'*32,'clues','cancelled',json.dumps({'processed':2})))
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',('b'*32,'c'*32,'clues','running',json.dumps({'processed':2})))
            self.assertEqual(module.recover(root,True),[])
