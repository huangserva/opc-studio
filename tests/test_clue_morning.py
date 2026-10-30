"""H3b 合成 SQLite 夹具；不写真实调度库、聊天或系统配置。"""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import clue_morning as morning
from external_tasks import ExternalTasks

NOW = datetime.fromisoformat('2026-10-30T08:30:00+08:00')


class MorningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='h3b-unit-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = self.root / 'workspace'
        for folder in ('schedules', 'clues'):
            (self.ws / folder).mkdir(parents=True)
        with self.db('schedules/tasks.db') as db:
            db.execute('CREATE TABLE runs(id TEXT, snapshot TEXT, output TEXT, status TEXT, started_at TEXT)')
        with self.db('clues/clues.db') as db:
            db.executescript('CREATE TABLE threads(id TEXT, data TEXT,created_at TEXT); CREATE TABLE entries(thread_id TEXT, data TEXT, entry_type TEXT)')

    def db(self, path):
        db = sqlite3.connect(self.ws / path)
        self.addCleanup(db.close)
        return db

    def run_row(self, identity, ids, status='succeeded', at=NOW, kind='wechat_clues', output=None):
        with self.db('schedules/tasks.db') as db:
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?)', (identity, json.dumps({'kind': kind}),
                       json.dumps({'clues': ids}) if output is None else output, status, at.isoformat()))

    def clue(self, identity, categories, summary='合成合作需求', evidence=None, at=NOW):
        with self.db('clues/clues.db') as db:
            db.execute('INSERT INTO threads VALUES(?,?,?)', (identity, json.dumps(dict(title='合成线索 ' + identity,
                       summary=summary, categories=categories)), at.isoformat()))
            db.execute('INSERT INTO entries VALUES(?,?,?)', (identity, json.dumps({'evidence': evidence or []}), 'evidence'))

    def test_recent_success_partial_dedup_exact_keys_and_multicategory(self):
        self.clue('a', ['technology', 'business'])
        self.clue('b', ['topics'])
        self.run_row('one', [], output=json.dumps({'unrelated': {'clues': ['wrong']}, 'clues': ['a', 'b', 'a']}))
        self.run_row('two', ['a'], status='partial')
        path, meta = morning.generate(self.root, NOW)
        self.assertEqual(meta['clue_count'], 2)
        self.assertEqual(set(meta['clue_ids']), {'a', 'b'})
        self.assertEqual(meta['category_counts'], dict(business=1, technology=1, topics=1, projects=0, other=0))
        content = path.read_text()
        self.assertLess(content.index('## 商单'), content.index('## 新技术'))
        self.assertIn('合作需求（线索摘要，待核实）：合成合作需求', content)
        self.assertIn('多类别线索在各类别分别计数', content)

    def test_window_boundary_failed_future_old_and_other_source_excluded(self):
        self.clue('a', ['projects'])
        self.run_row('boundary', ['a'], at=NOW-timedelta(hours=24))
        for identity, status, at, kind in [('old','succeeded',NOW-timedelta(hours=24,seconds=1),'wechat_clues'),
            ('future','succeeded',NOW+timedelta(seconds=1),'wechat_clues'), ('failed','failed',NOW,'wechat_clues'),
            ('github','succeeded',NOW,'github_clues'), ('script','succeeded',NOW,'script')]:
            self.run_row(identity, ['missing'], status, at, kind)
        self.assertEqual(morning.generate(self.root,NOW)[1]['run_ids'], ['boundary'])

    def test_no_new_and_no_clues_radar_is_honest(self):
        self.run_row('radar', [], kind='wechat_radar', output='{"processed":2}')
        path, meta = morning.generate(self.root,NOW)
        self.assertEqual(meta['clue_count'],0)
        self.assertEqual(sum(meta['category_counts'].values()),0)
        self.assertIn('今日无新增',path.read_text())

    def test_speaker_only_from_named_wechat_provenance(self):
        evidence = [dict(kind='mail',provenance={'author':'合成邮件作者'}),
            dict(kind='wechat',title='合成群名',excerpt='合成原文',provenance={'source_locator':'合成定位', 'sender_name':'合成发言人'}),
            dict(kind='wechat',provenance={'sender_name':'合成发言人'})]
        self.clue('a',['business'],evidence=evidence)
        self.run_row('r',['a'])
        content=morning.generate(self.root,NOW)[0].read_text()
        self.assertIn('发言人：合成发言人\n',content)
        self.assertNotIn('合成邮件作者',content)
        self.assertNotIn('合成原文',content)
        self.assertNotIn('合成群名',content)

    def test_missing_speaker_not_inferred_and_unknown_category_is_other(self):
        self.clue('a',['business','future-category'])
        self.clue('b',[])
        self.run_row('r',['a','b'])
        path,meta=morning.generate(self.root,NOW)
        self.assertIn('发言人：未知（原始证据未提供具名字段）',path.read_text())
        self.assertEqual(meta['category_counts']['other'],1)

    def test_missing_clue_is_failure_not_zero(self):
        self.run_row('r',['missing'])
        with self.assertRaisesRegex(ValueError,'线索不存在'):
            morning.generate(self.root,NOW)
        self.assertFalse((self.ws/'clue-morning').exists())

    def test_recent_run_old_clue_not_counted_as_new(self):
        self.clue('old',['technology'],at=NOW-timedelta(days=2))
        self.clue('new',['technology'])
        self.run_row('r',['old','new'])
        self.assertEqual(morning.generate(self.root,NOW)[1]['clue_ids'],['new'])

    def test_large_report_keeps_all_business_limits_other_categories(self):
        for i in range(55):
            self.clue(str(i),['business'] if i<20 else ['technology'])
        self.run_row('r',[str(i) for i in range(55)])
        path,meta=morning.generate(self.root,NOW)
        self.assertEqual(meta['clue_count'],55)
        self.assertEqual(meta['category_counts']['technology'],35)
        self.assertEqual(path.read_text().count('线索编号：'),28)
        self.assertIn('其余 27 条',path.read_text())

    def test_bad_output_is_failure_and_preserves_existing_report(self):
        path,meta=morning.generate(self.root,NOW)
        before=path.read_bytes(),path.with_suffix('.json').read_bytes()
        self.run_row('r',[],output='{"clues": "bad"}')
        with self.assertRaisesRegex(ValueError,'格式异常'):
            morning.generate(self.root,NOW)
        self.assertEqual(before,(path.read_bytes(),path.with_suffix('.json').read_bytes()))

    def test_readonly_missing_database_not_created(self):
        path=self.ws/'schedules/tasks.db'
        path.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            morning.generate(self.root,NOW)
        self.assertFalse(path.exists())

    def test_beijing_date_snapshot_integrity_and_expiry(self):
        at=datetime.fromisoformat('2026-10-29T16:30:00+00:00')
        path,meta=morning.generate(self.root,at)
        self.assertEqual(path.name,'2026-10-30.md')
        self.assertEqual(morning.snapshot(self.ws,at)['status'],'ok')
        self.assertEqual(morning.snapshot(self.root,at+timedelta(days=1))['status'],'missing')
        self.assertEqual(meta['markdown_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_text('合成篡改')
        self.assertEqual(morning.snapshot(self.root,at)['status'],'error')

    def test_snapshot_missing_pair_and_symlink_fail_closed(self):
        path,meta=morning.generate(self.root,NOW)
        path.with_suffix('.json').unlink()
        self.assertEqual(morning.snapshot(self.root,NOW)['status'],'missing')
        path.unlink();path.symlink_to(self.ws/'clues/clues.db')
        self.assertEqual(morning.snapshot(self.root,NOW)['status'],'error')

    def test_h1_valid_pair_previous_day_and_broken_pair(self):
        path,meta=morning.generate(self.root,NOW)
        for file in (path,path.with_suffix('.json')):
            os.utime(file,(NOW.timestamp(),NOW.timestamp()))
        clock=[NOW.timestamp()+60]
        heart=ExternalTasks(self.ws,self.root/'reports',clock=lambda:clock[0],service_reader=lambda:{})
        def row():
            heart.refresh(force=True)
            return next(r for r in heart.snapshot()['tasks'] if r['id']=='wechat-clue-morning')
        self.assertEqual(row()['status'],'ok')
        clock[0]=(NOW+timedelta(days=1,minutes=-10)).timestamp()
        self.assertEqual(row()['status'],'ok')
        clock[0]=(NOW+timedelta(days=1,minutes=61)).timestamp()
        self.assertEqual(row()['status'],'overdue')
        path.write_text('合成篡改')
        self.assertEqual(row()['status'],'missing')

    def test_pi_task_has_command_and_no_raw_content_output(self):
        payload=morning.schedule_payload(self.root,'synthetic-profile')
        self.assertEqual((payload['kind'],payload['at'],payload['weekdays']),('pi','08:30',list(range(7))))
        self.assertTrue(payload['allow_commands'])
        self.assertEqual(payload['profile_id'],'synthetic-profile')
        self.assertNotIn('Documents/Codex',payload['body'])
        with patch('sys.argv',['clue_morning.py','--root',str(self.root)]),patch('builtins.print') as output:
            self.assertEqual(morning.main(),0)
        result=json.loads(output.call_args.args[0])
        self.assertEqual(set(result),{'status','path','clue_count','category_counts','markdown_sha256'})

    def test_pi_judgment_changes_only_last_line_and_metadata(self):
        self.clue('a',['business'])
        self.run_row('r',['a'])
        path,meta=morning.generate(self.root,NOW,require_judgment=True)
        before=path.read_text().rpartition('\n当日判断：')[0]
        self.assertEqual(morning.snapshot(self.root,NOW)['status'],'pending')
        path,after=morning.finalize(self.root,'合成判断：先核实商单。',meta['markdown_sha256'],NOW)
        self.assertEqual(path.read_text().rpartition('\n当日判断：')[0],before)
        self.assertEqual(after['category_counts'],meta['category_counts'])
        self.assertEqual(after['clue_ids'],meta['clue_ids'])
        self.assertEqual(after['judgment'],{'source':'pi','generated_at':NOW.isoformat()})
        self.assertEqual(morning.snapshot(self.root,NOW)['status'],'ok')
        with self.assertRaisesRegex(ValueError,'已变化'):
            morning.finalize(self.root,'合成旧版本判断',meta['markdown_sha256'],NOW)

    def test_judgment_no_new_and_multiline_rejected(self):
        path,meta=morning.generate(self.root,NOW,require_judgment=True)
        self.assertEqual(meta['judgment']['source'],'rules')
        with self.assertRaisesRegex(ValueError,'无新增'):
            morning.finalize(self.root,'合成判断',meta['markdown_sha256'],NOW)
        self.clue('a',['other']);self.run_row('r',['a'])
        path,meta=morning.generate(self.root,NOW,require_judgment=True)
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'一行'):
            morning.finalize(self.root,'合成判断\n篡改条数',meta['markdown_sha256'],NOW)
        self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':
    unittest.main()
