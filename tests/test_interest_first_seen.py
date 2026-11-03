"""首次抵达语义：重复同步、跨日、修复回填、重建和真实分页路径。"""
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import grok_bot_radar as G
import interest_channel as I
import interest_sources as S
import interest_match as M
import today_view as T

DAY1 = datetime(2026, 11, 2, 4, tzinfo=timezone.utc)
DAY2 = DAY1 + timedelta(days=1)


class FirstSeenTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='first-seen-unit-', dir='/tmp')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base/'workspace'; self.root.mkdir()
        self.reports = self.base/'reports'; self.reports.mkdir()
        (self.reports/'interest-topics.md').write_text('# 兴趣\n## 当前方向\n- Agent 编程工具（Claude Code、Codex、MCP）\n## 说明\n')
        self.bundle = self.base/'bundle.json'; self.bundle.write_text('{"tweets":[],"vip_tweets":[]}')
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.dict(os.environ, {'GROK_BOT_REPORTS_DIR':str(self.reports)}))
        stack.enter_context(patch.object(G, 'BUNDLE_PATH', self.bundle))

    def sync(self, when, ids=('303',), filename=None):
        p = self.reports/'bookmarks'/(filename or when.date().isoformat()+'.json')
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(dict(exported_at=when.isoformat(),items=[dict(id=id,url='https://x.com/fixture/status/'+id,created_at='2026-09-21T13:25:57Z',full_text='Codex MCP 编程工具的完整教程和使用说明。',suggested_topic='Agent 编程工具',content_complete=True,views=100 if when==DAY1 else 200) for id in ids])))
        return p

    def build(self, now):
        return S.build(self.root,now,self.reports)

    def listing(self, data, now, period='today', **query):
        cache=dict(ready=True,partial=False,refreshing=False,error=None,built_at=data['built_at'],build_ms=data['build_ms'])
        with patch.object(S,'candidates',return_value=(data,cache)):
            return T.page_view(self.root,dict(channel=['interest'],range=[period],**query),now)

    def test_repeat_sync_preserves_first_seen_and_updates_metrics(self):
        self.sync(DAY1); a=self.build(DAY1)
        self.sync(DAY2,filename=DAY1.date().isoformat()+'.json'); b=self.build(DAY2)
        self.assertEqual(a['items'][0]['fetched_at'],DAY1.isoformat())
        self.assertEqual(b['items'][0]['fetched_at'],DAY2.isoformat())
        self.assertEqual(b['items'][0].get('first_seen_at'),DAY1.isoformat())
        self.assertEqual(b['items'][0]['last_seen_at'],DAY2.isoformat())
        self.assertEqual(b['items'][0]['metrics']['views'],200)

    def test_next_day_old_gone_new_present_counts_and_filter_context(self):
        self.sync(DAY1);self.build(DAY1)
        self.sync(DAY2,('303','404')); data=self.build(DAY2)
        today=self.listing(data,DAY2); week=self.listing(data,DAY2,'7d')
        self.assertEqual([p['id'] for p in today['items']],['x:404'])
        self.assertEqual(today['daily_counts'],{'2026-11-03':1})
        self.assertEqual(week['daily_counts'],{'2026-11-02':1,'2026-11-03':1})
        self.assertEqual(today['filter_context']['range_total'],1)
        self.assertEqual(len(week['items']),2)

    def test_midnight_beijing_boundary(self):
        before=datetime(2026,11,2,15,59,tzinfo=timezone.utc)
        after=before+timedelta(minutes=1)
        self.sync(before); self.build(before)
        self.sync(after,filename=before.date().isoformat()+'.json')
        self.assertEqual(self.listing(self.build(after),after)['total'],0)

    def test_legacy_cache_history_recovers_before_prune(self):
        self.sync(DAY1);row=S.parse_file(self.reports/'bookmarks/2026-11-02.json','report:bookmarks')[0]
        target=S.filecache_path(self.root);target.parent.mkdir(parents=True)
        with closing(sqlite3.connect(target)) as db,db:
            db.execute('CREATE TABLE files(path TEXT PRIMARY KEY,digest TEXT,kind TEXT,data TEXT)')
            db.execute('INSERT INTO files VALUES(?,?,?,?)',('/retired/file.json','old','2:report:bookmarks',json.dumps([row])))
        self.sync(DAY2,filename='2026-11-02.json')
        data=self.build(DAY2)
        self.assertEqual(data['items'][0]['first_seen_at'],DAY1.isoformat())
        self.assertEqual(self.listing(data,DAY2)['total'],0)

    def test_rebuild_and_new_process_keep_first_seen_after_raw_history_lost(self):
        self.sync(DAY1);self.build(DAY1)
        self.sync(DAY2,filename='2026-11-02.json');self.build(DAY2)
        for p in [S.cache_path(self.root),S.filecache_path(self.root)]:
            p.unlink(missing_ok=True)
        S.FILE_CACHE.clear();S.SIGNATURE_CACHE.clear()
        code="import json,sys;from pathlib import Path;from datetime import datetime;import interest_sources as S;d=S.build(Path(sys.argv[1]),datetime.fromisoformat(sys.argv[2]));print(json.dumps({'first':d['items'][0]['first_seen_at'],'latest':d['items'][0]['fetched_at']}))"
        run=subprocess.run([sys.executable,'-B','-c',code,str(self.root),DAY2.isoformat()],cwd=Path(S.__file__).parent,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(run.stdout),{'first':DAY1.isoformat(),'latest':DAY2.isoformat()})

    def test_old_history_outside_rolling_window_survives_first_migration(self):
        old=DAY2-timedelta(days=20);self.sync(old);self.sync(DAY2)
        data=self.build(DAY2)
        self.assertEqual(data['items'][0]['first_seen_at'],old.isoformat())
        self.assertEqual(self.listing(data,DAY2,'7d')['total'],0)

    def test_unknown_legacy_without_history_explicit_repair_epoch(self):
        import interest_first_seen as F
        row=S.item(dict(id='505',url='https://x.com/a/status/505',text='Codex MCP'), 'X 书签','x:bookmarks',DAY1.isoformat())
        target=S.cache_path(self.root);target.parent.mkdir(parents=True);target.write_text(json.dumps({'items':[row]}))
        F.apply(self.root,[row],DAY2,self.reports)
        self.assertEqual(row['first_seen_at'],DAY2.isoformat())
        self.assertEqual(row['first_seen_basis'],'since_repair_no_history')
        row['fetched_at']=(DAY2+timedelta(days=1)).isoformat()
        F.apply(self.root,[row],DAY2+timedelta(days=1),self.reports)
        self.assertEqual(row['first_seen_at'],DAY2.isoformat())

    def test_cross_source_same_identity_uses_earliest_before_filter(self):
        import interest_first_seen as F
        a=S.item(dict(id='303',url='https://x.com/a/status/303',text='Codex MCP'),'X 书签','x:bookmarks',DAY1.isoformat())
        b=S.item(dict(id='303',url='https://x.com/a/status/303',text='Codex MCP'),'AIsa X','A',DAY2.isoformat())
        F.apply(self.root,[a,b],DAY2,self.reports)
        self.assertEqual(a['first_seen_at'],b['first_seen_at'])
        merged=M.deduplicate([a,b])[0]
        self.assertEqual(merged['first_seen_at'],DAY1.isoformat())
        self.assertEqual(merged['fetched_at'],DAY2.isoformat())
        self.assertEqual(set(merged['input_sources']),{'X 书签','AIsa X'})

    def test_original_interest_snapshot_latest_does_not_reset_arrival(self):
        from tests.test_interest_channel import row
        folder=self.reports/'interest';folder.mkdir()
        for when in [DAY1,DAY2]:
            record=row();record['collected_at']=when.isoformat();record['published_at']='2026-09-21T13:25:57Z'
            (folder/when.astimezone(G.BEIJING).strftime('%Y-%m-%d-%H%M.json')).write_text(json.dumps([record]))
        data=self.build(DAY2)
        self.assertEqual(self.listing(data,DAY2)['total'],0)
        self.assertEqual(self.listing(data,DAY2,'7d')['daily_counts'],{'2026-11-02':1})

    def test_k1_identity_merge_retains_old_alias_ledger(self):
        import interest_first_seen as F
        row=dict(id='x:303',canonical_key='x:303',fetched_at=DAY1.isoformat())
        F.apply(self.root,[row],DAY1,self.reports)
        row=dict(id='x:303',canonical_key='tool:merged',fetched_at=DAY2.isoformat())
        F.apply(self.root,[row],DAY2,self.reports)
        self.assertEqual(row['first_seen_at'],DAY1.isoformat())

    def test_legacy_cached_rows_without_optional_shape_still_migrate(self):
        import interest_first_seen as F
        target=S.filecache_path(self.root);target.parent.mkdir(parents=True)
        with closing(sqlite3.connect(target)) as db,db:
            db.execute('CREATE TABLE files(path,digest,kind,data)')
            db.execute('INSERT INTO files VALUES(?,?,?,?)',('old','hash','1:legacy',json.dumps([dict(id='x:303',fetched_at=DAY1.isoformat())])))
        row=dict(id='x:303',canonical_key='x:303',fetched_at=DAY2.isoformat())
        F.apply(self.root,[row],DAY2,self.reports)
        self.assertEqual(row['first_seen_at'],DAY1.isoformat())

    def test_database_created_time_is_recovery_evidence_not_updated_time(self):
        dbpath=self.root/'clues/clues.db';dbpath.parent.mkdir()
        with closing(sqlite3.connect(dbpath)) as db,db:
            db.executescript('CREATE TABLE threads(id,data,created_at,updated_at);CREATE TABLE entries(thread_id,data);')
            db.execute('INSERT INTO threads VALUES(?,?,?,?)',('t1',json.dumps(dict(title='MCP 教程',summary='MCP 工具接入说明')),DAY1.isoformat(),DAY2.isoformat()))
            db.execute('INSERT INTO entries VALUES(?,?)',('t1',json.dumps(dict(evidence=[dict(kind='wechat',document_id='private',excerpt='PRIVATE_NOT_READ')]))))
        data=self.build(DAY2)
        self.assertEqual(data['items'][0]['first_seen_at'],DAY1.isoformat())
        self.assertEqual(data['items'][0]['first_seen_basis'],'source_created_at')
        self.assertNotIn('PRIVATE_NOT_READ',json.dumps(data))

    def test_earlier_observation_basis_is_not_relabelled_as_later_k1_time(self):
        import interest_first_seen as F
        self.sync(DAY1)
        dbpath=self.root/'clues/clues.db';dbpath.parent.mkdir()
        with closing(sqlite3.connect(dbpath)) as db,db:
            db.executescript('CREATE TABLE clue_objects(id,canonical_key,platform,first_seen_at);CREATE TABLE clue_channels(clue_id,channel);')
            db.execute('INSERT INTO clue_objects VALUES(?,?,?,?)',('c1','x:303','x',DAY2.isoformat()))
        row=dict(id='x:303',canonical_key='x:303',fetched_at=DAY2.isoformat())
        F.apply(self.root,[row],DAY2,self.reports)
        self.assertEqual(row['first_seen_at'],DAY1.isoformat())
        self.assertEqual(row['first_seen_basis'],'earliest_retained_observation')

    def test_future_observation_does_not_poison_ledger(self):
        import interest_first_seen as F
        row=S.item(dict(id='606',url='https://x.com/a/status/606',text='Codex MCP'),'X 书签','x:bookmarks',(DAY2+timedelta(days=1)).isoformat())
        F.apply(self.root,[row],DAY2,self.reports)
        with closing(sqlite3.connect(F.path(self.root))) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM first_seen').fetchone()[0],0)

    def test_ledger_symlink_is_rejected(self):
        import interest_first_seen as F
        p=F.path(self.root);p.parent.mkdir(parents=True);outside=self.base/'outside';outside.write_text('protected');p.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'first_seen_link'):F.apply(self.root,[],DAY2,self.reports)
        self.assertEqual(outside.read_text(),'protected')

    def test_first_seen_sort_day_not_latest_sync_day(self):
        rows=[]
        for id,first in [('old',DAY1),('new',DAY2)]:
            rows.append(dict(canonical_key=id,fetched_at=(DAY2+timedelta(hours=id=='old')).isoformat(),first_seen_at=first.isoformat(),content_insufficient=False,interest_rank=dict(trust=4,direction=2),interest_matches=[dict(direction='codex')]))
        self.assertEqual([p['canonical_key'] for p in M.balanced_sort(rows,G.BEIJING,G.stamp)],['new','old'])

if __name__=='__main__':unittest.main()
