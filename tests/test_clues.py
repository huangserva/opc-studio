from datetime import datetime
import hashlib
import sqlite3
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clues_store import Clues,source_clock
from events_store import Events,EventConflict
from scheduled_tasks import ScheduledTasks

class KnowledgeFake:
    def __init__(self,docs):self.docs=docs
    @contextmanager
    def db(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.execute('CREATE TABLE documents(id TEXT,hash TEXT)')
        db.executemany('INSERT INTO documents VALUES(?,?)',[(d['id'],d['hash']) for d in self.docs.values()])
        try:yield db
        finally:db.close()
    def document(self,key):
        if key not in self.docs:raise ValueError('missing')
        return self.docs[key].copy()

class ClueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.doc_id='a'*32
        doc={'id':self.doc_id,'source':'wechat','kind':'wechat','title':'合成项目讨论','body':'😀甲项目预计延期；具体日期尚待确认。','hash':'b'*64,'url':'http://example.test/message','modified':'2026-10-05T02:00:00+00:00','provenance':{'timestamp':datetime.fromisoformat('2026-10-05T23:00:00+08:00').timestamp()}}
        self.knowledge=KnowledgeFake({self.doc_id:doc});self.store=SimpleNamespace(workspace=Path(self.tmp.name),knowledge=self.knowledge)
        self.store.events=Events(self.store);self.store.clues=Clues(self.store);self.store.schedules=ScheduledTasks(self.tmp.name,autostart=False);self.store.schedules.publisher=self.store.clues.from_run;self.clues=self.store.clues
    def tearDown(self):self.store.schedules.close();self.tmp.cleanup()
    def pick(self):
        d=self.knowledge.docs[self.doc_id];return {'id':self.doc_id,'hash':d['hash'],'start':0,'end':len(d['body'])}
    def create(self,**kw):
        p={'title':'合成项目进展','summary':'延期候选','relevance':'正在关注的甲项目','picks':[self.pick()]};p.update(kw)
        return self.clues.save(p)['id']
    def test_requires_real_snapshot_and_unknown_category_falls_back_other(self):
        with self.assertRaises(ValueError):self.clues.save({'title':'没有出处'})
        identity=self.create();d=self.clues.detail(identity)
        self.assertEqual(d['categories'],['other']);self.assertEqual(d['status'],'candidate');self.assertEqual(d['entries'][0]['evidence'][0]['excerpt'],self.knowledge.docs[self.doc_id]['body'])
        self.assertEqual(self.store.events.list_events()['events'],[])
    def test_cross_categories_and_literal_search_in_frozen_history(self):
        identity=self.create(categories=['business','projects','other']);d=self.clues.detail(identity)
        self.assertEqual(d['categories'],['business','projects'])
        self.clues.save({'id':identity,'revision':d['revision'],'categories':['technology'],'summary':'新的摘要','reason':'重新分类'})
        result=self.clues.listing(query='预计延期',category='technology');self.assertEqual(result['total'],1)
        self.assertEqual(self.clues.listing(query='%')['total'],0)
        self.assertEqual(len(self.clues.detail(identity)['entries']),2)
    def test_revision_conflicts_leave_old_data_and_sources_unchanged(self):
        identity=self.create();before=self.clues.detail(identity)
        with self.assertRaises(EventConflict):self.clues.save({'id':identity,'revision':0,'summary':'覆盖','reason':'bad'})
        self.assertEqual(self.clues.detail(identity)['summary'],before['summary'])
        self.knowledge.docs[self.doc_id]['hash']='c'*64
        with self.assertRaises(EventConflict):self.clues.save({'id':identity,'revision':1,'picks':[{'id':self.doc_id,'hash':'b'*64,'start':0,'end':3}]})
        d=self.clues.detail(identity);self.assertEqual(d['entries'][0]['evidence'][0]['state'],'changed');self.assertEqual(d['entries'][0]['evidence'][0]['hash'],'b'*64)
    def test_append_and_read_old_revision_do_not_mark_new_update_read(self):
        identity=self.create();self.clues.seen(identity,1)
        self.clues.save({'id':identity,'revision':1,'summary':'第二条依据','picks':[self.pick()],'note':'补充消息'})
        self.clues.seen(identity,1);self.assertTrue(self.clues.detail(identity)['unread'])
        self.assertEqual(self.clues.listing(view='unread')['total'],1)
        self.clues.seen(identity,2);self.assertFalse(self.clues.detail(identity)['unread'])
    def test_annual_uses_beijing_source_time_and_omits_metadata_edits(self):
        identity=self.create();self.clues.save({'id':identity,'revision':1,'tracking':True,'reason':'持续关注'})
        rows=self.clues.listing(view='year',year='2026')['timeline'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['month'],'10');self.assertEqual(rows[0]['year'],'2026')
        self.knowledge.docs[self.doc_id]['provenance']={};unknown=self.create(title='未知时间')
        self.assertEqual(self.clues.listing(view='year',year='unknown')['total'],1)
        self.assertEqual(self.clues.detail(unknown)['entries'][0]['clock'],'')
    def test_mail_without_timezone_and_modified_does_not_invent_occurrence(self):
        snapshot={'kind':'mail','modified':'2026-10-05T00:00:00Z','provenance':{'date_sent':'Mon, 05 Oct 2026 08:00:00'}}
        self.assertEqual(source_clock(snapshot)[0],'')
        snapshot['provenance']['date_sent']='Mon, 05 Oct 2026 08:00:00 +0800';self.assertTrue(source_clock(snapshot)[0])
    def test_filters_source_tracking_and_discussion_with_pagination(self):
        self.create(kind='discussion',tracking=True);self.create(title='另一条合成信息')
        self.assertEqual(self.clues.listing(view='discussion')['total'],1);self.assertEqual(self.clues.listing(view='tracking')['total'],1)
        self.assertEqual(self.clues.listing(source='wechat')['total'],2);r=self.clues.listing(limit=1);self.assertEqual(r['next_offset'],1)
    def test_promote_creates_candidate_event_never_personal_action_or_calendar(self):
        identity=self.create(categories=['projects']);result=self.clues.promote(identity,1);event=self.store.events.event(result['event_id'])
        self.assertTrue(event['needs_confirmation']);self.assertEqual(event['actions'],[])
        self.assertEqual(self.store.events.list_events(view='calendar')['events'],[])
        self.assertEqual(self.clues.detail(identity)['revision'],2)
        self.assertEqual(self.clues.promote(identity,2)['event_id'],result['event_id'])
        self.assertEqual(len(self.store.events.list_events()['events']),1)
    def test_import_event_is_idempotent_and_keeps_existing_actions_unconfirmed(self):
        event=self.store.events.save_event({'title':'已有合成事件','summary':'待核对','picks':[self.pick()],'needs_confirmation':True})
        a=self.clues.import_event(event['id']);b=self.clues.import_event(event['id']);self.assertEqual(a['id'],b['id']);self.assertTrue(b['duplicate'])
        self.assertEqual(self.clues.detail(a['id'])['event_id'],event['id']);self.assertEqual(self.clues.detail(a['id'])['status'],'candidate')
    def run_task(self,body="printf synthetic-result",**kw):
        p=dict(name='合成任务',kind='script',body=body,cwd=self.tmp.name,frequency='interval',minutes=60,timeout=5,enabled=False,allow_commands=False,result_destination='clues',result_category='technology');p.update(kw)
        identity=self.store.schedules.save(p)['id'];run_id=self.store.schedules.control(identity,'run')['id'];self.wait_run(run_id);return identity,run_id
    def wait_run(self,identity):
        for _ in range(100):
            r=self.store.schedules.get_run(identity)
            if r['status']!='running' and identity not in self.store.schedules.active:return r
            time.sleep(.03)
        self.fail('task did not finish publishing')
    def test_successful_script_delivers_candidate_and_duplicate_output_is_quiet(self):
        task_id,run_id=self.run_task();first=self.store.schedules.get_run(run_id)
        self.assertTrue(first['clue_id']);d=self.clues.detail(first['clue_id']);self.assertEqual(d['status'],'candidate');self.assertEqual(d['categories'],['technology'])
        self.assertEqual(self.clues.listing(view='year',year='unknown')['total'],1)
        second_id=self.store.schedules.control(task_id,'run')['id'];second=self.wait_run(second_id)
        self.assertEqual(first['clue_id'],second['clue_id']);self.assertEqual(self.clues.detail(first['clue_id'])['revision'],1)
        self.assertEqual(self.clues.listing()['total'],1)
    def test_changed_task_results_append_only_same_task_producer(self):
        task_id,run_id=self.run_task();thread=self.store.schedules.get_run(run_id)['clue_id'];task=self.store.schedules.listing()['tasks'][0]
        task['body']='printf different-result';self.store.schedules.save(task)
        second=self.wait_run(self.store.schedules.control(task_id,'run')['id'])
        self.assertEqual(second['clue_id'],thread);self.assertEqual(self.clues.detail(thread)['revision'],2)
        _,third_id=self.run_task(body='printf different-result');self.assertNotEqual(self.store.schedules.get_run(third_id)['clue_id'],thread)
    def test_failed_or_blank_runs_are_not_fake_clues_and_cannot_promote_model_output(self):
        _,run_id=self.run_task(body='exit 1');self.assertEqual(self.clues.listing()['total'],0)
        with self.assertRaises(ValueError):self.clues.from_run(run_id)
        _,empty=self.run_task(body='true');self.assertIsNone(self.store.schedules.get_run(empty)['clue_id']);self.assertEqual(self.clues.listing()['total'],0)
        _,valid=self.run_task();d=self.clues.detail(self.store.schedules.get_run(valid)['clue_id'])
        with self.assertRaisesRegex(ValueError,'原文'):self.clues.promote(d['id'],d['revision'])
    def test_publication_failure_retains_successful_run_for_retry(self):
        _,run_id=self.run_task(body="printf 'x'",clue_id='f'*32)
        r=self.store.schedules.get_run(run_id);self.assertEqual(r['status'],'succeeded');self.assertIn('不存在',r['publication_error']);self.assertEqual(self.clues.listing()['total'],0)
    def test_archived_clue_stays_searchable_but_not_latest(self):
        identity=self.create();self.clues.save({'id':identity,'revision':1,'status':'archived','reason':'先归档'})
        self.assertEqual(self.clues.listing(query='合成')['total'],1);self.assertEqual(self.clues.listing(view='latest')['total'],0)
    def test_snapshot_security_and_validation(self):
        self.assertEqual(self.clues.path.stat().st_mode&0o777,0o600);self.assertEqual(self.clues.root.stat().st_mode&0o777,0o700)
        with self.assertRaises(ValueError):self.create(categories=['bad'])
        with self.assertRaises(ValueError):self.create(occurred_at='2026-10-05')

    def test_restart_recovers_delivery_without_executing_task_again(self):
        self.store.schedules.publisher=None
        _,run_id=self.run_task()
        with self.store.schedules.db() as db:db.execute('UPDATE runs SET publication_error=NULL WHERE id=?',(run_id,))
        self.store.schedules.close();self.store.schedules=ScheduledTasks(self.tmp.name,autostart=False)
        self.store.schedules.publisher=self.clues.from_run;self.store.schedules.start()
        for _ in range(100):
            if self.store.schedules.get_run(run_id)['clue_id']:break
            time.sleep(.03)
        self.assertTrue(self.store.schedules.get_run(run_id)['clue_id'])
        self.assertEqual(len(self.store.schedules.runs()['runs']),1)

    def test_updated_source_retains_old_quote_and_promotes_current_evidence(self):
        identity=self.create()
        self.knowledge.docs[self.doc_id].update(hash='c'*64,body='项目时间已经核对，新版本仍保留依据。')
        self.clues.save({'id':identity,'revision':1,'picks':[self.pick()],'note':'追加当前版本'})
        detail=self.clues.detail(identity)
        self.assertEqual(detail['entries'][1]['evidence'][0]['state'],'changed')
        self.assertEqual(self.clues.listing(query='预计延期')['total'],1)
        event=self.store.events.event(self.clues.promote(identity,2)['event_id'])
        self.assertEqual(len(event['evidence']),1)
        self.assertEqual(event['evidence'][0]['hash'],'c'*64)

    def test_technical_tags_filter_all_pages_and_allow_manual_override(self):
        a=self.create(title='MCP Agent 自动化',categories=['technology'])
        b=self.create(title='KV 显存推理优化',categories=['technology'])
        self.create(title='Agent 商务合作',categories=['business'])
        result=self.clues.listing(category='technology',tag='agents',limit=1)
        self.assertEqual([r['id'] for r in result['items']],[a])
        self.assertEqual(result['total'],1)
        self.assertEqual(result['tag_counts']['inference'],1)
        self.assertEqual(self.clues.detail(a)['tags_source'],'suggested')
        self.clues.save({'id':a,'revision':1,'tags':['tools'],'reason':'人工校准技术方向'})
        self.assertEqual(self.clues.listing(category='technology',tag='agents')['total'],0)
        self.assertEqual(self.clues.detail(a)['tags'],['tools'])
        self.clues.save({'id':a,'revision':2,'summary':'新增会话内容','picks':[self.pick()]})
        self.assertEqual(self.clues.detail(a)['tags'],['tools'])
        self.clues.save({'id':a,'revision':3,'tags':None,'reason':'恢复建议标签'})
        self.assertIn('agents',self.clues.detail(a)['tags'])
        self.assertEqual(self.clues.listing(category='technology',tag='inference')['items'][0]['id'],b)

    def test_source_and_import_times_sort_independently_with_unknown_last(self):
        old=self.create(title='更早发生但最后导入',occurred_at='2026-10-01T08:00:00+08:00')
        recent=self.create(title='最近原文',occurred_at='2026-10-05T01:00:00+00:00')
        self.knowledge.docs[self.doc_id]['provenance']={}
        unknown=self.create(title='未知发生时间')
        with self.clues.db() as db:
            for i,key in enumerate([recent,unknown,old]):db.execute('UPDATE threads SET updated_at=? WHERE id=?',(f'2026-10-06T00:00:0{i}+00:00',key))
        self.assertEqual(self.clues.listing(sort='updated',limit=1)['items'][0]['id'],old)
        self.assertEqual([r['id'] for r in self.clues.listing(sort='source_desc')['items']],[recent,old,unknown])
        self.assertEqual([r['id'] for r in self.clues.listing(sort='source_asc')['items']],[old,recent,unknown])
        self.assertEqual(self.clues.listing(sort='source_desc',offset=1,limit=1)['items'][0]['id'],old)

    def test_importance_orders_entire_result_before_pagination(self):
        important=self.create(title='优先关注的早期线索',priority='important',occurred_at='2026-10-01T00:00:00+08:00')
        self.create(title='普通新线索',priority='normal',occurred_at='2026-10-05T00:00:00+08:00')
        self.assertEqual(self.clues.listing(sort='priority',limit=1)['items'][0]['id'],important)
        self.assertEqual(self.clues.listing(priority='important')['total'],1)

    def test_recent_range_uses_source_time_and_excludes_unknown(self):
        from clues_store import ZONE
        today=self.create(occurred_at=datetime.now(ZONE).isoformat())
        self.create(title='历史原文',occurred_at='2020-01-01T00:00:00+00:00')
        self.knowledge.docs[self.doc_id]['provenance']={};self.create(title='未知原文')
        self.assertEqual([r['id'] for r in self.clues.listing(period='today')['items']],[today])
        self.assertEqual(self.clues.listing(period='7d')['total'],1)
        self.assertEqual(self.clues.listing(period='30d')['total'],1)
        for kwargs in [{'sort':'bad'},{'tag':'bad'},{'period':'bad'},{'priority':'bad'}]:
            with self.assertRaises(ValueError):self.clues.listing(**kwargs)
        with self.assertRaises(ValueError):self.create(tags=['bad'])


class TimeRangeTests(ClueTests):
    """时间范围：昨天、最近三天。口径与现有 today / 7d 一致（按原文时间）。"""

    def at(self, **delta):
        from datetime import timedelta
        from clues_store import ZONE
        return (datetime.now(ZONE)-timedelta(**delta)).isoformat()

    def midnight(self, days=0):
        """北京时间某天的零点。测试要锚在零点而不是「现在」——
        否则凌晨 0–1 点跑的时候，「昨天此刻再往前推 N 小时」会跨过日界，
        用例在不同钟点结果不同，那是测试自己的定时炸弹。"""
        from datetime import timedelta
        from clues_store import ZONE
        return datetime.now(ZONE).replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days)

    def clock(self, days=0, hours=0, minutes=0):
        from datetime import timedelta
        return (self.midnight(days)+timedelta(hours=hours,minutes=minutes)).isoformat()

    def ids(self, **kw):
        return sorted(r['id'] for r in self.clues.listing(**kw)['items'])

    def test_yesterday_is_the_full_previous_beijing_day(self):
        now=self.create(title='此刻')
        yesterday=self.create(title='昨天白天',occurred_at=self.clock(1,10))
        yesterday_late=self.create(title='昨天深夜',occurred_at=self.clock(1,23,59))
        two_days=self.create(title='前天',occurred_at=self.clock(2,10))
        self.assertEqual(self.ids(period='yesterday'),sorted([yesterday,yesterday_late]))
        self.assertNotIn(now,self.ids(period='yesterday'))
        self.assertNotIn(two_days,self.ids(period='yesterday'))

    def test_yesterday_window_is_lower_inclusive_and_upper_exclusive(self):
        from datetime import timedelta
        # 上界 = 今天 00:00，恰好落在 00:00:00 的线索不算昨天
        exactly_midnight=self.create(title='今天零点整',occurred_at=self.clock(0))
        just_after=self.create(title='今天零点刚过',occurred_at=(self.midnight(0)+timedelta(seconds=1)).isoformat())
        yesterday=self.create(title='昨天零点整',occurred_at=self.clock(1))
        self.assertEqual(self.ids(period='yesterday'),[yesterday])
        self.assertNotIn(exactly_midnight,self.ids(period='yesterday'),'上界不含：今天 00:00 不能算昨天')
        self.assertNotIn(just_after,self.ids(period='yesterday'))

    def test_yesterday_spans_the_whole_day_even_when_now_is_early(self):
        # 00:30 时「昨天」仍然应包含昨天 00:00–23:59 的全部内容
        from datetime import timedelta
        late_yesterday=(self.midnight(0)-timedelta(minutes=1)).isoformat()
        clue=self.create(title='昨天最后一分钟',occurred_at=late_yesterday)
        self.assertIn(clue,self.ids(period='yesterday'))

    def test_yesterday_excludes_clues_with_unknown_source_time(self):
        self.create(title='昨天有原文',occurred_at=self.clock(1,12))
        self.knowledge.docs[self.doc_id]['provenance']={}
        self.create(title='原文时间未知')
        self.assertEqual(self.clues.listing(period='yesterday')['total'],1)

    def test_yesterday_window_is_the_same_instant_written_in_any_timezone(self):
        """同一个时刻写成 +08:00 还是 +00:00，归属必须一致。
        真实线索的 clock 不统时区（邮件/GitHub 直接存 +00:00），
        只测 +08:00 写法测不出「把 UTC 写法当成另一个时刻」这类错。"""
        from datetime import timedelta, timezone
        def utc_form(dt):
            return dt.astimezone(timezone.utc).isoformat()
        midnight_today=self.midnight(0)
        beijing_edge=self.create(title='今天零点 +08',occurred_at=midnight_today.isoformat())
        utc_edge=self.create(title='今天零点 UTC写法',occurred_at=utc_form(midnight_today))
        beijing_last=self.create(title='昨天最后一分钟 +08',occurred_at=(midnight_today-timedelta(minutes=1)).isoformat())
        utc_last=self.create(title='昨天最后一分钟 UTC写法',occurred_at=utc_form(midnight_today-timedelta(minutes=1)))
        got=self.ids(period='yesterday')
        self.assertEqual(got,sorted([beijing_last,utc_last]),'上界两侧各取一种写法：两种写法都得跟着同一个时刻走')
        self.assertNotIn(beijing_edge,got);self.assertNotIn(utc_edge,got)

    def test_three_days_uses_the_same_rolling_cutoff_as_seven_and_thirty(self):
        inside=self.create(title='两天前',occurred_at=self.clock(2,10))
        outside=self.create(title='很久以前',occurred_at='2020-01-01T00:00:00+00:00')
        self.assertEqual(self.ids(period='3d'),[inside])
        self.assertIn(inside,self.ids(period='7d'),'3d 比 7d 更窄但同向')
        self.assertIn(inside,self.ids(period='30d'))
        self.assertNotIn(outside,self.ids(period='3d'))

    def test_three_days_is_narrower_than_seven_days(self):
        three=self.create(title='三天内',occurred_at=self.clock(2,10))
        self.create(title='五天前',occurred_at=self.clock(5))
        self.assertEqual(self.ids(period='3d'),[three])
        self.assertEqual(self.clues.listing(period='7d')['total'],2)

    def test_every_range_ignores_unknown_source_time(self):
        # 一次性建好「原文时间未知」的线索，再逐个范围查 —— setUp 是按方法走的，
        # subTest 之间共享同一批数据，所以这里不能每个 subTest 各建一条。
        self.knowledge.docs[self.doc_id]['provenance']={}
        self.create(title='原文时间未知')
        for period in ('today','yesterday','3d','7d','30d'):
            with self.subTest(period=period):
                self.assertEqual(self.clues.listing(period=period)['total'],0,
                                 '原文时间未知的线索在任何时间范围下都不该出现')

    def test_unknown_period_still_raises_the_same_message(self):
        for bad in ('bad','yesterda','2d','1d','Yesterday','YESTERDAY','3D','0d','-1d','昨天'):
            with self.subTest(period=bad):
                with self.assertRaises(ValueError) as caught:
                    self.clues.listing(period=bad)
                self.assertEqual(str(caught.exception),'时间范围无效')

if __name__=='__main__':unittest.main()
