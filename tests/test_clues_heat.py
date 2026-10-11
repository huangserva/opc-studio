"""Heat integration: temporary stores, synthetic evidence, no network."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from clues_heat import recompute_heat
from clues_store import Clues, source_clock
from tests.isolation_patches import REPLAY_TOOL_REASON, replay_tool_available
from xradar_source import Radar


def evidence(bucket='2026-10-10T06'):
    return {'kind':'xradar','source':'xradar','title':'synthetic','excerpt':'test',
            'provenance':{'bucket':bucket},'captured_at':'2026-10-10T07:00:00+00:00'}


def wechat_evidence():
    return {'kind':'wechat','source':'wechat','title':'w','excerpt':'t',
            'provenance':{'timestamp':1767225600.0}}


def observation(bucket='2026-10-10T06',mentions=10,site_state=''):
    return dict(source='xradar',bucket=bucket,mentions=mentions,talkers=2,views=None,
                site_state=site_state,captured_at='2026-10-10T07:00:00+00:00')


class HeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=SimpleNamespace(workspace=Path(self.tmp.name));self.store.clues=Clues(self.store)
        self.clues=self.store.clues

    def create(self,**kw):
        return self.clues._save({'title':'test',**kw},[evidence()],observations=[observation()])

    def test_observation_atomic_duplicate_unchanged_and_overwrite(self):
        p={'title':'test'}; a=self.clues._save(p,[evidence()],producer_key='once',observations=[observation()])
        self.clues._save(p,[evidence()],producer_key='once',observations=[observation(mentions=100)])
        self.assertEqual(self.clues.observations(a['id'])[0]['mentions'],10)
        self.clues._save({'id':a['id'],'revision':1,'reason':'noop'},[],observations=[observation(mentions=100)])
        self.assertEqual(self.clues.observations(a['id'])[0]['mentions'],10)
        self.clues._save({'id':a['id'],'revision':1},[evidence()],observations=[observation(mentions=20)])
        self.assertEqual(len(self.clues.observations(a['id'])),1)
        self.assertEqual(self.clues.observations(a['id'])[0]['mentions'],20)
        with self.assertRaises(ValueError):
            self.clues._save({'title':'bad'},[evidence()],observations=[observation(mentions=-1)])
        self.assertEqual(self.clues.listing()['total'],1)

    def test_heat_validation_manual_priority_and_facets(self):
        a=self.create(heat='rising',heat_source='site');d=self.clues.detail(a['id'])
        self.clues.save({'id':a['id'],'revision':d['revision'],'heat':'fading','heat_source':'manual','reason':'manual'})
        self.clues._save({'id':a['id'],'revision':2,'heat':'rising','heat_source':'site'},[evidence()])
        self.assertEqual(self.clues.detail(a['id'])['heat'],'fading')
        self.assertEqual(self.clues.listing(heat='fading')['total'],1)
        self.assertEqual(self.clues.listing()['heat_counts']['fading'],1)
        with self.assertRaises(ValueError):self.create(heat='rising')
        with self.assertRaises(ValueError):self.create(heat='wrong',heat_source='computed')

    def test_compute_thresholds_gaps_and_site(self):
        from clues_heat import compute_heat
        for recent,want in [(125,'rising'),(55,'fading'),(100,'peak')]:
            s=[observation('2026-10-10T00',100),observation('2026-10-10T06',recent)]
            self.assertEqual(compute_heat(s)[0],want)
        self.assertEqual(compute_heat([observation()])[0],'')
        self.assertEqual(compute_heat([observation('2026-10-09T00'),observation()])[0],'')
        self.assertEqual(compute_heat([observation(site_state='up')])[0],'rising')
        self.assertEqual(compute_heat([{**observation(), 'source':'github'}])[0],'')

    def test_recompute_expiry_and_edit_audit(self):
        from clues_heat import recompute_heat
        a=self.clues._save({'title':'test','heat':'peak','heat_source':'site'},[evidence()],observations=[{**observation(),'ratio6_site':1.,'bars_recent6':6.,'bars_prev6':6.}])
        self.clues._save({'id':a['id'],'revision':1},[evidence()],observations=[{**observation('2026-10-10T00',10),'ratio6_site':1.0,'bars_recent6':6.,'bars_prev6':6.}])
        recompute_heat(self.clues,today='2026-10-13')
        d=self.clues.detail(a['id']);self.assertEqual(d['heat'],'peak');self.assertEqual(d['heat_source'],'computed')
        self.assertEqual(d['entries'][0]['evidence'],[])
        revision=d['revision'];recompute_heat(self.clues,today='2026-10-13')
        self.assertEqual(self.clues.detail(a['id'])['revision'],revision)

    def test_xradar_clock(self):
        self.assertEqual(source_clock(evidence())[0],'2026-10-10T06:00:00+00:00')

    def test_three_days_one_thread_and_new_day_one_separate(self):
        radar=Radar(self.store)
        for n,title in enumerate(['合成火星探测器任务启动','合成火星探测器任务最新进展','合成火星探测器任务进入轨道'],1):
            row=dict(board='topics',key='k'+str(n),category='ai',heat='rising',title=title,
                     summary='test',meta='',views=100,raw_views='100',days=n,posts=10,talkers=2,url='https://xbangdan.com/words/',first_post_url='https://x.com/i/status/123456')
            day='2026-10-0'+str(n)
            with radar.db() as db:db.execute('INSERT INTO items VALUES(?,?,?,?,NULL)',(day,'topics',row['key'],json.dumps(row)))
            radar.publish(day)
        self.assertEqual(self.clues.listing()['total'],1)
        self.assertEqual(len(self.clues.detail(self.clues.listing()['items'][0]['id'])['entries']),3)
        row.update(key='new',days=1)
        with radar.db() as db:db.execute('INSERT INTO items VALUES(?,?,?,?,NULL)',('2026-10-04','topics','new',json.dumps(row)))
        radar.publish('2026-10-04');self.assertEqual(self.clues.listing()['total'],2)

    def test_manual_recompute_and_site_protect_from_computed_evidence(self):
        from clues_heat import recompute_heat
        a=self.create(heat='rising',heat_source='manual')
        self.clues._save({'id':a['id'],'revision':1},[evidence()],observations=[{**observation(),'ratio6_site':0.2}])
        recompute_heat(self.clues,today='2026-10-13')
        self.assertEqual(self.clues.detail(a['id'])['heat'],'rising')
        b=self.create(heat='peak',heat_source='site')
        self.clues._save({'id':b['id'],'revision':1,'heat':'fading','heat_source':'computed'},[evidence()])
        self.assertEqual(self.clues.detail(b['id'])['heat_source'],'site')
        self.assertEqual(self.clues.detail(b['id'])['heat'],'peak')

    def test_site_three_missing_calendar_days(self):
        from clues_heat import recompute_heat
        a=self.clues._save({'title':'test','heat':'rising','heat_source':'site'},[evidence()],
             observations=[{**observation(site_state='up'),'ratio6_site':2.}])
        for n in (11,12,13):
            d=self.clues.detail(a['id'])
            self.clues._save({'id':a['id'],'revision':d['revision']},[evidence()],
                observations=[{**observation(f'2026-10-{n}T06'),'ratio6_site':0.5}])
            recompute_heat(self.clues,today=f'2026-10-{n}')
            d=self.clues.detail(a['id'])
            self.assertEqual(d['heat_source'],'site' if n<13 else 'computed')
        self.assertEqual(d['heat'],'fading')

    def test_github_unique_comments_utc_date_and_unknown_authors(self):
        from clues_heat import github_observations
        c=dict(id=1,author='Ada',created_at='2026-10-11T01:00:00+08:00',updated_at='2026-10-12T00:00:00Z')
        data={'comments':[c,c,{**c,'id':2,'author':'ada'},{**c,'id':3,'author':'未知作者'},{**c,'id':4,'created_at':'bad'}]}
        obs=github_observations(data,'2026-10-13T00:00:00Z')
        self.assertEqual(len(obs),1);self.assertEqual(obs[0]['bucket'],'2026-10-10')
        self.assertEqual((obs[0]['mentions'],obs[0]['talkers']),(3,1))
        a=self.clues._save({'title':'github'},[{'kind':'github','excerpt':'test'}],observations=obs)
        self.assertEqual(self.clues.observations(a['id'])[0]['views'],None)

    def test_scheduler_local_only_idempotent_daily_and_run_audit(self):
        from scheduled_tasks import ScheduledTasks
        schedule=ScheduledTasks(self.store.workspace,autostart=False);self.addCleanup(schedule.close)
        seed=schedule.seed_heat_schedule(self.store.workspace)
        task=schedule.listing()['tasks'][0]
        self.assertEqual(task['at'],'02:10');self.assertEqual(task['kind'],'recompute_heat')
        self.assertEqual(task['model_name'],'');self.assertFalse(task['enabled']);self.assertIsNone(task['next_run'])
        self.assertEqual(schedule.seed_heat_schedule(self.store.workspace)['id'],seed['id'])
        schedule.heat_recomputer=lambda cancel,deadline:{'updated':0}
        run=schedule.claim(seed['id'],'manual',launch=False)
        schedule.execute(run['id'],task,schedule.active[run['id']],None)
        self.assertEqual(schedule.get_run(run['id'])['status'],'succeeded')
        bad={**task,'allow_commands':True}
        with self.assertRaises(ValueError):schedule.validate(bad)

    def test_heat_seed_preserves_user_controls_and_resume_at_shanghai_0210(self):
        from scheduled_tasks import ScheduledTasks
        from datetime import datetime
        from zoneinfo import ZoneInfo
        schedule=ScheduledTasks(self.store.workspace,autostart=False);self.addCleanup(schedule.close)
        seed=schedule.seed_heat_schedule(self.store.workspace)
        disabled=schedule.listing()['tasks'][0]
        self.assertFalse(disabled['enabled']);self.assertIsNone(disabled['next_run'])
        schedule.seed_heat_schedule(self.store.workspace)
        self.assertEqual(schedule.listing()['tasks'][0],disabled)
        schedule.control(seed['id'],'resume')
        enabled=schedule.listing()['tasks'][0]
        self.assertTrue(enabled['enabled']);self.assertIsNotNone(enabled['next_run'])
        due=datetime.fromtimestamp(enabled['next_run'],ZoneInfo('Asia/Shanghai'))
        self.assertEqual((due.hour,due.minute,due.second),(2,10,0))
        schedule.seed_heat_schedule(self.store.workspace)
        self.assertEqual(schedule.listing()['tasks'][0],enabled)

    def test_brief_local_store_evidence_and_nine_angles(self):
        from xradar_source import parse_topics
        from tests.test_xradar_source import real_page,REAL_TODAY
        radar=Radar(self.store);rows=parse_topics(real_page(REAL_TODAY))
        with radar.db() as db:
            for row in rows:db.execute('INSERT INTO items VALUES(?,?,?,?,NULL)',('2026-10-10','topics',row['key'],json.dumps(row)))
        brief=radar.brief('2026-10-10');self.assertEqual(len(brief['sentences']),3)
        self.assertEqual(len(brief['angle_categories']),9)
        self.assertIn('情绪与群体心态',brief['angle_categories'])
        self.assertIn('不等于最近6小时',brief['sentences'][1])
        for group in brief['groups'].values():
            for row in group:self.assertEqual(set(row['evidence']),{'first_post','hottest_post','source_url'})
        self.assertEqual(self.clues.listing()['total'],0)
        self.assertEqual(radar.status()['extra']['latest_brief']['day'],'2026-10-10')

    # hygiene-4：这条要 import 本机协调工具 coordination/tools/replay_xradar_heat.py，
    # 而 coordination/ 按 load-manifest-v2 §3.2 不进公开仓库。
    # 装饰器挂在**方法**上而不是类上 —— HeatTests 被 6 个子类继承，
    # 挂类会把这些子类里同名方法一并误伤/误跳过。
    @unittest.skipUnless(replay_tool_available(), REPLAY_TOOL_REASON)
    def test_fourteen_day_offline_acceptance_and_gold_coverage(self):
        from coordination.tools.replay_xradar_heat import replay
        result,states=replay()
        self.assertEqual(result['days'],14);self.assertEqual(result['state_n'],254)
        self.assertGreaterEqual(result['site_feature_agreement_pct'],92)
        self.assertEqual(result['live_merged_rows'],0)
        self.assertEqual(result['scored_false_merges'],0)
        self.assertIsNone(result['gold_recall_pct']) # no gold pairs, do not claim precision
        self.assertEqual(result['first_url_coverage_pct'],0)

    def test_gold_not_title_prevents_false_merge_and_days_one_never_merge(self):
        radar=Radar(self.store)
        row=dict(board='topics',key='a',category='ai',heat='',title='合成火星探测器任务启动',
                 summary='test',meta='',views=100,raw_views='100',days=1,posts=10,talkers=2,
                 url='https://xbangdan.com/words/',first_post_url='https://x.com/i/status/111')
        with radar.db() as db:db.execute('INSERT INTO items VALUES(?,?,?,?,NULL)',('2026-10-01','topics','a',json.dumps(row)))
        radar.publish('2026-10-01')
        for key,first,title in [('b','https://x.com/i/status/222',row['title']),('c','',row['title'])]:
            later={**row,'key':key,'days':2,'first_post_url':first,'title':title}
            with radar.db() as db:db.execute('INSERT INTO items VALUES(?,?,?,?,NULL)',('2026-10-02','topics',key,json.dumps(later)))
        radar.publish('2026-10-02')
        self.assertEqual(self.clues.listing()['total'],3)

    def test_legacy_json_defaults_and_schema_incremental(self):
        a=self.create()
        with self.clues.db() as db:
            fields=json.loads(db.execute('SELECT data FROM threads WHERE id=?',(a['id'],)).fetchone()[0])
            fields.pop('heat');fields.pop('heat_source')
            db.execute('UPDATE threads SET data=? WHERE id=?',(json.dumps(fields),a['id']))
        reopened=Clues(self.store)
        self.assertEqual(reopened.detail(a['id'])['heat'],'')
        self.assertEqual(len(reopened.observations(a['id'])),1)

    def test_manual_clear_is_explicit_but_automatic_clear_is_frozen(self):
        a=self.create(heat='rising',heat_source='manual')
        self.clues._save({'id':a['id'],'revision':1,'heat':'','heat_source':''},[evidence()])
        self.assertEqual(self.clues.detail(a['id'])['heat'],'rising')
        self.clues.save({'id':a['id'],'revision':2,'heat':'','reason':'人工清除热度'})
        self.assertEqual(self.clues.detail(a['id'])['heat'],'')
        self.assertEqual(self.clues.detail(a['id'])['heat_source'],'')


class HeatDryRunTests(HeatTests):
    """22h-2 M1：recompute_heat 的 dry-run 只报告、不改写。

    复核 M1：首个启用窗口在 299 条 xradar 线索上一次写 243 条 edit entry、
    把 243 条 heat 从 site 改写成 computed、并前进 243 个 revision。
    行为本身合规，但批量改动前应当能先看清会发生什么。
    """

    def seed(self, days=4, site_days=2):
        """播种一条 xradar 线索，复现 M1 的「首窗 site→computed 降级」前置。

        heat 摆成 site；观测里带 SVG 特征（没有 ratio6_site 的话 compute_heat 会把
        value 清空，那条分支与本测试无关）。最新槽的 site_state 留空 ——
        这正是 SITE_EXPIRY_DAYS 到期后站方标签失效、heat 该降级成 computed 的场景。
        """
        rows = []
        for n in range(days):
            site_state = 'peak' if n == days - site_days else ''
            rows.append({**observation(bucket='2026-10-%02dT00' % (7 + n), mentions=10 + n,
                                       site_state=site_state),
                         'ratio6_site': 1.6, 'bars_recent6': 8.0, 'bars_prev6': 5.0})
        saved = self.clues._save({'title': 'dr', 'summary': 'dry',
                                  'heat': 'peak', 'heat_source': 'site'}, [evidence()],
                                 observations=rows)
        return saved['id']

    def snapshot_db(self):
        """整库字节快照 + 条数，用来证明 dry-run 没写任何东西。"""
        path = Path(self.clues.path)
        entries_total = 0
        with self.clues.db() as db:
            entries_total = db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
        return path.read_bytes(), self.clues.listing()['total'], entries_total

    def test_dry_run_reports_without_writing(self):
        ident = self.seed()
        before = self.clues.detail(ident)
        self.assertEqual(before['heat_source'], 'site', '前置：应是 site')
        db_before, total_before, entries_before = self.snapshot_db()

        result = recompute_heat(self.clues, today='2026-10-12', dry_run=True)

        self.assertTrue(result.get('dry_run') is True, '返回值要标明是 dry-run：%r' % result)
        self.assertGreater(result.get('would_update'), 0,
                           '应当报出会改写的条数：%r' % result)
        db_after, total_after, entries_after = self.snapshot_db()
        self.assertEqual(db_after, db_before, 'dry-run 不得改变库文件任何一个字节')
        self.assertEqual(entries_after, entries_before, 'dry-run 不得写 edit entry')
        after = self.clues.detail(ident)
        self.assertEqual(after['heat'], before['heat'], 'dry-run 不得改 heat')
        self.assertEqual(after['heat_source'], before['heat_source'])
        self.assertEqual(after['revision'], before['revision'], 'dry-run 不得前进 revision')

    def test_dry_run_reports_site_to_computed_counts_and_samples(self):
        self.seed()
        result = recompute_heat(self.clues, today='2026-10-12', dry_run=True)
        self.assertIn('site_to_computed', result, '要报 site→computed 的条数：%r' % result)
        self.assertGreaterEqual(result['site_to_computed'], 0)
        self.assertIn('computed', result, '要报 computed 总数：%r' % result)
        self.assertIsInstance(result.get('samples'), list, '要给样例：%r' % result)
        for sample in result.get('samples', []):
            self.assertIn('id', sample)
            self.assertIn('from', sample)
            self.assertIn('to', sample)
            self.assertIn('reason', sample)

    def test_dry_run_and_real_run_agree_on_counts(self):
        """dry-run 报的数量应当等于真跑真会改的数量。"""
        self.seed()
        predicted = recompute_heat(self.clues, today='2026-10-12', dry_run=True)
        actual = recompute_heat(self.clues, today='2026-10-12')
        self.assertEqual(predicted['would_update'], actual['updated'],
                         'dry-run 预测 %s，实跑 %s' % (predicted['would_update'], actual['updated']))

    def test_default_behaviour_is_unchanged(self):
        """默认参数下必须真写 —— 加 dry_run 不能改变现行为。"""
        ident = self.seed()
        before = self.clues.detail(ident)
        entries_before = self.snapshot_db()[2]

        result = recompute_heat(self.clues, today='2026-10-12')

        self.assertEqual(result['updated'], 1, '默认应真改写：%r' % result)
        after = self.clues.detail(ident)
        self.assertEqual(after['revision'], before['revision'] + 1, '默认应前进 revision')
        self.assertGreater(self.snapshot_db()[2], entries_before, '默认应写 edit entry')


class ManualHeatGuardTests(HeatTests):
    """22h-2 L1：只给 heat 不给 heat_source 时，不得覆盖 manual 线索的 heat。

    复核 L1：_save 收到 {'heat': ...} 但没有 heat_source 时，会先注入
    heat_source='manual'，于是守卫 `payload.get('heat_source','manual')!='manual'`
    判为放行 —— manual 线索的 heat 被改掉，heat_source 却还挂着 manual。
    """

    def test_internal_save_with_heat_only_cannot_override_manual(self):
        saved = self.clues._save({'title': 't', 'summary': 's',
                                  'heat': 'rising', 'heat_source': 'manual'}, [evidence()])
        ident = saved['id']
        before = self.clues.detail(ident)
        self.assertEqual((before['heat'], before['heat_source']), ('rising', 'manual'))

        self.clues._save({'id': ident, 'revision': before['revision'],
                          'heat': 'fading', 'reason': '内部路径只带 heat'}, [])

        after = self.clues.detail(ident)
        self.assertEqual((after['heat'], after['heat_source']), ('rising', 'manual'),
                         'manual 冻结必须挡住只带 heat 的内部写入：%r' % after)

    def test_explicit_manual_write_still_works(self):
        """公开 save() 的人工覆盖能力不能被这次加固挡掉。"""
        saved = self.clues._save({'title': 't', 'summary': 's',
                                  'heat': 'rising', 'heat_source': 'manual'}, [evidence()])
        ident = saved['id']
        before = self.clues.detail(ident)

        self.clues.save({'id': ident, 'revision': before['revision'],
                         'heat': 'fading', 'reason': '人工改'})

        after = self.clues.detail(ident)
        self.assertEqual((after['heat'], after['heat_source']), ('fading', 'manual'),
                         '人工显式改 heat 必须仍然生效：%r' % after)

    def test_computed_cannot_override_manual_either(self):
        saved = self.clues._save({'title': 't', 'summary': 's',
                                  'heat': 'peak', 'heat_source': 'manual'}, [evidence()])
        ident = saved['id']
        before = self.clues.detail(ident)

        self.clues._save({'id': ident, 'revision': before['revision'], 'heat': '',
                          'heat_source': 'computed', 'reason': '内部重算'}, [])

        after = self.clues.detail(ident)
        self.assertEqual((after['heat'], after['heat_source']), ('peak', 'manual'))


class ClockLabelTests(HeatTests):
    """22h-2 M3：单一来源时 clock_label 用来源自己的说法。"""

    def test_xradar_clock_label_says_it_is_an_observation_slot(self):
        saved = self.clues._save({'title': 'x', 'summary': 's'}, [evidence()],
                                 observations=[observation()])
        label = self.clues.detail(saved['id'])['entries'][0]['clock_label']
        self.assertEqual(label, 'X 雷达观测槽时间（非首发时间）',
                         'xradar 线索必须显示这是观测槽时间而非话题首发时间，实际 %r' % label)

    def test_wechat_clock_label_says_wechat(self):
        saved = self.clues._save({'title': 'w', 'summary': 's'}, [wechat_evidence()])
        label = self.clues.detail(saved['id'])['entries'][0]['clock_label']
        self.assertEqual(label, '微信消息时间', '实际 %r' % label)

    def test_mixed_sources_keep_the_generic_label(self):
        saved = self.clues._save({'title': 'mix', 'summary': 's'},
                                 [evidence(), wechat_evidence()])
        label = self.clues.detail(saved['id'])['entries'][0]['clock_label']
        self.assertEqual(label, '本次依据时间（最近）',
                         '多来源时保持通用文案，实际 %r' % label)


class EvidenceKeyTests(HeatTests):
    """22h-2 L6：同槽不同数据必须产生第二条 evidence，观测覆盖不累加。"""

    def append(self, ident, key, mentions):
        """模拟 publish 第二次落同一条线索：带 id/revision，带新的 producer_key。"""
        before = self.clues.detail(ident)
        return self.clues._save({'id': ident, 'revision': before['revision'],
                                 'title': before['title'], 'summary': before['summary'],
                                 'reason': '同日同槽新数据'},
                                [evidence()], producer_key=key,
                                observations=[observation(mentions=mentions)])

    def test_same_bucket_different_data_makes_second_evidence(self):
        first = self.clues._save({'title': 's', 'summary': '1'}, [evidence()],
                                 producer_key='xradar:topics:t:2026-10-10T06:aaa',
                                 observations=[observation(mentions=10)])
        ident = first['id']
        entries_before = self.clues.detail(ident)['entries_total']

        # 22h 的 key：xradar:{board}:{stable_id}:{bucket}:{sha256(data)}
        second = self.append(ident, 'xradar:topics:t:2026-10-10T06:bbb', 59)

        self.assertEqual(second['id'], ident, '同一话题应落到同一条线索')
        self.assertNotEqual(second.get('duplicate'), True, 'hash 不同不得判 duplicate')
        self.assertEqual(self.clues.detail(ident)['entries_total'], entries_before + 1,
                         '应新增一条 evidence entry')
        types = [e['entry_type'] for e in self.clues.detail(ident)['entries']]
        self.assertEqual(types.count('evidence'), 2, '两条都是 evidence：%r' % types)

    def test_same_bucket_observation_is_overwritten_not_summed(self):
        first = self.clues._save({'title': 's', 'summary': '1'}, [evidence()],
                                 producer_key='xradar:topics:t:2026-10-10T06:aaa',
                                 observations=[observation(mentions=10)])
        ident = first['id']
        self.append(ident, 'xradar:topics:t:2026-10-10T06:bbb', 59)
        series = self.clues.observations(ident)
        self.assertEqual(len(series), 1, '同槽观测只有一行：%r' % series)
        self.assertEqual(series[0]['mentions'], 59, '取新值，不是 10+59 累加')

    def test_same_bucket_same_data_stays_duplicate(self):
        p = {'title': 's'}
        first = self.clues._save(p, [evidence()], producer_key='xradar:topics:t:2026-10-10T06:aaa',
                                 observations=[observation()])
        again = self.clues._save(p, [evidence()], producer_key='xradar:topics:t:2026-10-10T06:aaa',
                                 observations=[observation()])
        self.assertEqual(again.get('duplicate'), True, '同槽同数据必须仍判 duplicate')
        self.assertEqual(again['id'], first['id'])

    def test_duplicate_is_decided_by_full_key_including_hash(self):
        """锁住 evidence key 的 hash 后缀：同 bucket 不同 hash 必须开新 entry。"""
        first = self.clues._save({'title': 's', 'summary': '1'}, [evidence()],
                                 producer_key='xradar:topics:t:2026-10-10T06:aaa',
                                 observations=[observation(mentions=10)])
        ident = first['id']
        self.append(ident, 'xradar:topics:t:2026-10-10T06:bbb', 59)
        # 再用最早的 key 落一次：producer_key 命中 → duplicate，且不新增 entry
        again = self.clues._save({'title': 's', 'summary': '1'}, [evidence()],
                                 producer_key='xradar:topics:t:2026-10-10T06:aaa',
                                 observations=[observation()])
        self.assertTrue(again.get('duplicate'), '同 key 必须判 duplicate：%r' % again)
        self.assertEqual(self.clues.detail(ident)['entries_total'], 2,
                         'duplicate 不得新增 entry')


class HeatRevision3Tests(HeatTests):
    def test_downgrade_and_clear_counts_are_distinct(self):
        for ratio in (1.6, None):
            rows = [observation('2026-10-07T00', site_state='peak'), observation()]
            if ratio is not None:
                rows[-1].update(ratio6_site=ratio)
            self.clues._save({'title': 'mixed', 'heat': 'peak', 'heat_source': 'site'},
                             [evidence()], observations=rows)
        report = recompute_heat(self.clues, today='2026-10-12', dry_run=True)
        self.assertEqual(report['would_update'], 2)
        self.assertEqual(report['site_to_computed'], 1)
        self.assertEqual(report['site_cleared'], 1)
        self.assertEqual(report['computed'], 1)

    def label(self, snapshots):
        ident = self.clues._save({'title': 'clock'}, snapshots)['id']
        return self.clues.detail(ident)['entries'][0]

    def test_two_xradar_use_latest_slot_label(self):
        entry = self.label([evidence('2026-10-10T00'), evidence('2026-10-10T06')])
        self.assertEqual(entry['clock_label'], 'X 雷达观测槽时间（非首发时间）')
        self.assertIn('06:00:00', entry['clock'])

    def test_two_wechat_use_latest_message_label(self):
        first = wechat_evidence()
        second = {**first, 'provenance': {'timestamp': 1767225660.0}}
        entry = self.label([first, second])
        self.assertEqual(entry['clock_label'], '微信消息时间')
        self.assertIn('00:01:00', entry['clock'])

    def test_mixed_known_sources_remain_generic(self):
        self.assertEqual(self.label([evidence(), wechat_evidence()])['clock_label'],
                         '本次依据时间（最近）')

    def test_known_and_unknown_source_remain_generic(self):
        unknown = {**evidence(), 'kind': 'manual', 'source': 'manual', 'provenance': {}}
        self.assertEqual(self.label([evidence(), unknown])['clock_label'],
                         '本次依据时间（最近）')
