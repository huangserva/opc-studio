import json
import os
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import grok_bot_radar as grok
import today_view as today
import video_radar_page_api as video

NOW = datetime.now(timezone.utc)
LONG = '首行😀 <script>alert(1)</script> <img src=x onerror=alert(1)> &amp;\n\n' + '完整中文原文' * 450 + '\n末行　\t'


class TodayTests(unittest.TestCase):
    def setUp(self):
        self.linux_patch = patch.object(today.linuxdo, 'refresh_background')
        self.linux_patch.start()
        self.addCleanup(self.linux_patch.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='today-unit-', dir='/tmp')
        self.base = Path(self.temp.name)
        self.workspace = self.base / 'workspace'
        self.workspace.mkdir()
        self.reports = self.base / 'reports'
        (self.reports / 'daily').mkdir(parents=True)
        self.bundle = self.base / 'bundle.json'
        self.env = patch.dict(os.environ, {'GROK_BOT_REPORTS_DIR': str(self.reports)})
        self.env.start()
        self.bundle_patch = patch.object(grok, 'BUNDLE_PATH', self.bundle)
        self.bundle_patch.start()
        self.write_bundle([])
        self.write([self.row('1'), self.row('2')])
        self.write_video()

    def tearDown(self):
        # ItemsStore 的旧用例手动调用 setUp/tearDown，不会执行 unittest cleanup。
        self.linux_patch.stop()
        self.bundle_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def row(self, id='1', **extra):
        return dict(id=id, handle='fixture', author='夹具作者', text=LONG, url='https://x.com/fixture/status/' + id,
                    created_at=(NOW - timedelta(hours=1)).isoformat(), label='爆款', topic='Agent', views=99, likes=2, bookmarks=12, followers=20000, **extra)

    def write(self, rows, file='daily/a.json', when=None):
        path = self.reports / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(collected_at=(when or NOW).isoformat(), posts=rows), ensure_ascii=False))

    def write_bundle(self, rows):
        self.bundle.write_text(json.dumps(dict(updated_at=NOW.isoformat(), tweets=rows, vip_tweets=[])))

    def write_video(self, rows=None):
        directory = self.workspace / 'video-radar'
        directory.mkdir(exist_ok=True)
        row = dict(key='douyin:v1', id='v1', platform='douyin', sources=['daily'], title='AI 视频正文', author='视频作者', author_fans=2000,
                   published_at=(NOW-timedelta(hours=1)).isoformat(), last_seen_at=NOW.isoformat(), plays=900, likes=21, collects=3, flags=['low_fan_hot'],
                   cover='https://example.com/cover.jpg', url='https://www.douyin.com/video/v1', relevance='relevant', relevance_reason='AI')
        (directory / 'page.json').write_text(json.dumps(dict(schema=video.SCHEMA, generated_at=NOW.isoformat(), sources={}, lists={'douyin_daily': rows if rows is not None else [row]})))
        return row

    def detail(self, id='x:1'):
        return today.item_view(self.workspace, {'id': [id]})

    def test_hot_threshold_and_source_combination(self):
        rows=[]
        for i, views in enumerate([99999, 100000, None, "100000"], 31):
            row=self.row(str(i));row['views']=views;rows.append(row)
        self.write(rows)
        v=self.write_video();v['plays']=150000;self.write_video([v])
        listing=today.page_view(self.workspace, {'range':['48h']}, NOW)
        values={p['id']:p['hot'] for p in listing['items']}
        self.assertEqual(values, {'x:31':False,'x:32':True,'x:33':False,'x:34':True,'douyin:v1':True})
        hot=today.page_view(self.workspace, {'range':['48h'],'tier':['火爆'],'source':['G']}, NOW)
        self.assertEqual({p['id'] for p in hot['items']}, {'x:32','x:34'})
        self.assertTrue(all(p['tier']=='爆款' for p in hot['items']))
        self.assertTrue(self.detail('douyin:v1')['hot'])
        for query in ({'tier':['unknown']}, {'source':['unknown']}, {'tier':['火爆','爆款']}):
            with self.assertRaises(ValueError):today.page_view(self.workspace, query, NOW)

    def test_long_text_exact_and_summary_no_rich_fields(self):
        self.assertEqual(self.detail()['full_text'], LONG)
        listing = today.page_view(self.workspace, {'range': ['48h']}, NOW)
        p = next(p for p in listing['items'] if p['id'] == 'x:1')
        self.assertLessEqual(len(p['title']), 120)
        self.assertEqual(set(p), set(today.SUMMARY))
        self.assertNotIn('full_text', json.dumps(listing))

    def test_unified_dual_content_and_latest_metrics(self):
        g = self.row()
        g.update(text='AI 短摘要', article={'body':'G 长文'}, thread=['G 串推'], quoted={'text':'G 引用'}, media=[{'url':'https://example.com/g.png'}])
        self.write([g], when=NOW-timedelta(hours=1))
        a = dict(id='1', text=LONG, fetched_at=NOW.isoformat(), published_at=g['created_at'], views=1200, likes=33, article={'body':'A 长文'}, thread=['A 串推'])
        self.write_bundle([a])
        item = self.detail()
        self.assertEqual(item['sources'], ['A','G'])
        self.assertEqual(item['full_text'], LONG)
        self.assertEqual(item['metrics']['views'], 1200)
        self.assertIsNone(item['metrics']['bookmarks'])
        self.assertEqual({s['article']['body'] for s in item['rich_sources']}, {'A 长文','G 长文'})
        self.assertEqual(len([p for p in today.page_view(self.workspace, {'range':['48h']}, NOW)['items'] if p['id']=='x:1']),1)

    def test_scores_preserved_from_raw_and_top_three_only(self):
        rows=[]
        for i in range(1,7):
            row=self.row(str(i));row.update(fit_score=3 if i<5 else 1, doable=3 if i<5 else 1);rows.append(row)
        self.write(rows)
        result=today.page_view(self.workspace, {'range':['48h']},NOW)
        self.assertEqual(len(result['top']),3)
        self.assertTrue(all(p['fit_score']==3 and p['doable']==3 for p in result['top']))
        self.assertEqual(self.detail()['fit_score'],3)

    def test_no_scores_never_fill_top(self):
        result=today.page_view(self.workspace, {'range':['48h']}, NOW)
        self.assertEqual(result['top'],[])
        self.assertEqual(result['top_message'],'等 Grok Bot 按画像打分后出现')

    def test_scored_but_not_doable_empty_top(self):
        row=self.row();row.update(fit_score=3,doable=1);self.write([row])
        self.assertEqual(today.page_view(self.workspace, {'range':['48h']}, NOW)['top_message'],'今天没有值得做的')

    def test_sorting_tier_then_time(self):
        rows=[]
        for id,tier in [('1','起势'),('2','其他'),('3','低粉爆款'),('4','爆款')]:
            row=self.row(id);row['label']=tier;rows.append(row)
        self.write(rows)
        result=today.page_view(self.workspace, {'range':['48h'],'source':['G']}, NOW)
        self.assertEqual([p['id'] for p in result['items']],['x:4','x:1','x:3','x:2'])

    def test_concurrent_feedback_no_lost_records_and_clear(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda id: today.save_feedback(self.workspace, {'id':id,'action':'accurate'}), ['x:1','x:2']))
        self.assertEqual(set(json.loads(today.feedback_path(self.workspace).read_text())),{'x:1','x:2'})
        self.assertEqual(grok.feedback_path(self.workspace).read_bytes(),(self.reports/'feedback/studio-feedback.json').read_bytes())
        today.save_feedback(self.workspace,{'id':'x:1','action':'clear'})
        self.assertNotIn('x:1',json.loads(today.feedback_path(self.workspace).read_text()))
        self.assertNotIn('1',grok.load_feedback(self.workspace)['feedback'])

    def test_keep_use_and_inaccurate_mapping(self):
        for action in ('inaccurate','keep'):
            today.save_feedback(self.workspace,{'id':'x:1','action':action})
        self.assertEqual(self.detail()['status'],'kept')
        self.assertEqual(self.detail()['feedback'],'inaccurate')
        self.assertEqual(grok.load_feedback(self.workspace)['feedback']['1']['verdict'],'not_worth')
        today.save_feedback(self.workspace,{'id':'x:1','action':'use'})
        self.assertEqual(grok.load_feedback(self.workspace)['feedback']['1']['verdict'],'used')

    def test_video_adapter_and_clear(self):
        today.save_feedback(self.workspace,{'id':'douyin:v1','action':'accurate'})
        self.assertEqual(video.load_feedback(self.workspace)[0]['douyin:v1']['verdict'],'worth')
        today.save_feedback(self.workspace,{'id':'douyin:v1','action':'clear'})
        self.assertEqual(video.load_feedback(self.workspace)[0],{})
        self.assertFalse(self.detail('douyin:v1')['content_complete'])

    def test_bad_payload_no_writes(self):
        for payload in (None,{}, {'id':'x:1','action':True},{'id':'x:1','action':'drop'},{'id':'x:999','action':'accurate'},{'id':[],'action':'use'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):today.save_feedback(self.workspace,payload)
        self.assertFalse(today.feedback_path(self.workspace).exists())

    def test_corrupt_feedback_refuses_write_and_keeps_bytes(self):
        path=today.feedback_path(self.workspace);path.parent.mkdir(parents=True);path.write_text('{broken')
        with self.assertRaises(ValueError):today.save_feedback(self.workspace,{'id':'x:1','action':'accurate'})
        self.assertEqual(path.read_text(),'{broken')
        self.assertIn('feedback',today.page_view(self.workspace,now=NOW)['sources'])

    def test_missing_grok_keeps_other_sources(self):
        shutil.rmtree(self.reports)
        result=today.page_view(self.workspace, {'range':['48h']},NOW)
        self.assertTrue(result['sources']['G']['error'])
        self.assertTrue(any(p['kind']=='video' for p in result['items']))

    def test_half_written_and_bad_numbers_do_not_break_other_sources(self):
        (self.reports/'daily/half.json').write_text('{"posts":[')
        for value in ('1.2万',float('nan'),True):
            row=self.row();row['views']=value;self.write([row])
            result=today.page_view(self.workspace, {'range':['48h']},NOW)
            self.assertTrue(result['sources']['G']['error'])
            self.assertTrue(any(p['kind']=='video' for p in result['items']))
            json.dumps(result,allow_nan=False);json.dumps(self.detail(),allow_nan=False)

    def test_bad_numeric_values_mark_aisa_and_video(self):
        for value in ('1.2万', float('nan'), True):
            self.write_bundle([dict(id='99',text='AI Agent',views=value,published_at=(NOW-timedelta(hours=1)).isoformat())])
            row=self.write_video();row['likes']=value;self.write_video([row])
            result=today.page_view(self.workspace,{'range':['48h']},NOW)
            self.assertTrue(result['sources']['A']['error'])
            self.assertTrue(result['sources']['V']['error'])
            self.assertTrue(any(p['sources']==['G'] for p in result['items']))
            json.dumps(result, allow_nan=False)

    def test_missing_aisa_keeps_grok_and_video(self):
        self.bundle.unlink()
        result=today.page_view(self.workspace, {'range':['48h']},NOW)
        self.assertTrue(result['sources']['A']['error'])
        self.assertEqual(len(result['items']),3)

    def test_corrupt_video_keeps_posts(self):
        (self.workspace/'video-radar/page.json').write_text('{"schema":"video-radar-page/v1","lists":{},"n":NaN}')
        result=today.page_view(self.workspace, {'range':['48h']},NOW)
        self.assertTrue(result['sources']['V']['error'])
        self.assertEqual(len(result['items']),2)

    def test_30_day_author_uses_existing_history(self):
        rows=[self.row(str(i)) for i in range(1,4)]
        rows[1]['created_at']=(NOW-timedelta(days=20)).isoformat()
        rows[2]['created_at']=(NOW-timedelta(days=31)).isoformat()
        self.write(rows)
        result=today.page_view(self.workspace,{'author':['fixture']},NOW)
        self.assertEqual({p['id'] for p in result['items']},{'x:1','x:2'})

    def test_pagination_search_and_filters(self):
        self.write([self.row(str(i)) for i in range(1,58)])
        result=today.page_view(self.workspace,{'range':['48h'],'source':['G'],'page':['2']},NOW)
        self.assertEqual(result['total'],57);self.assertEqual(len(result['items']),7)
        self.assertEqual(today.page_view(self.workspace,{'range':['48h'],'q':['不匹配']},NOW)['total'],0)
        for query in ({'range':['30d']},{'page':['0']},{'range':['today','7d']},{'unexpected':['a']}):
            with self.assertRaises(ValueError):today.page_view(self.workspace,query,NOW)

    def test_case15_heat_beats_newer_timestamp_and_bookmark_weight(self):
        early=self.row('1');early.update(created_at=(NOW-timedelta(hours=8)).isoformat(),bookmarks=412,likes=322,views_per_hour=2594.05)
        late=self.row('2');late.update(created_at=(NOW-timedelta(hours=4)).isoformat(),bookmarks=111,likes=50,views_per_hour=1953.71)
        self.write([late,early])
        result=today.page_view(self.workspace,{'range':['48h'],'source':['G']},NOW)
        self.assertEqual([p['id'] for p in result['items']],['x:1','x:2'])
        self.assertAlmostEqual(result['items'][0]['metrics']['heat'],2594.05*3)
        # 相同每小时浏览时，高收藏加权也能压过发布时间。
        early['views_per_hour']=late['views_per_hour']=1000
        self.write([late,early])
        self.assertEqual(today.page_view(self.workspace,{'range':['48h'],'source':['G']},NOW)['items'][0]['id'],'x:1')
        # 权重同分之后才按发布时间。
        early['bookmarks']=late['bookmarks']=111
        self.write([early,late])
        self.assertEqual(today.page_view(self.workspace,{'range':['48h'],'source':['G']},NOW)['items'][0]['id'],'x:2')

    def test_case16_capped_bookmark_weight_preserves_rate_priority(self):
        fast=self.row('9330');fast.update(views_per_hour=9330,bookmarks=None)
        saved=self.row('2594');saved.update(views_per_hour=2594,bookmarks=412)
        self.write([saved,fast])
        ids=[p['id'] for p in today.page_view(self.workspace,{'range':['48h'],'source':['G']},NOW)['items']]
        self.assertEqual(ids,['x:9330','x:2594'])
        fast.update(views_per_hour=1000,bookmarks=111);saved['views_per_hour']=1000
        self.write([fast,saved])
        ids=[p['id'] for p in today.page_view(self.workspace,{'range':['48h'],'source':['G']},NOW)['items']]
        self.assertEqual(ids,['x:2594','x:9330'])

    def test_heat_ratio_and_aisa_hourly_and_unknown_bookmarks(self):
        early=self.row('1');early.update(bookmarks=5,likes=4,views_per_hour=1000)
        late=self.row('2');late.update(bookmarks=5,likes=6,views_per_hour=1000)
        self.write([late,early])
        result=today.page_view(self.workspace,{'range':['48h'],'source':['G']},NOW)
        self.assertEqual(result['items'][0]['id'],'x:1')
        self.write_bundle([dict(id='99',text='AI Agent',views=1200,likes=20,published_at=(NOW-timedelta(hours=3)).isoformat(),fetched_at=(NOW-timedelta(hours=1)).isoformat())])
        item=self.detail('x:99')
        self.assertEqual(item['metrics']['views_per_hour'],600)
        self.assertIsNone(item['metrics']['bookmarks'])

    def test_video_heat_plays_per_hour_then_plays_fallback(self):
        item=self.detail('douyin:v1')
        self.assertEqual(item['metrics']['views_per_hour'],900)
        row=self.write_video();row['published_at']='';self.write_video([row])
        self.assertEqual(self.detail('douyin:v1')['metrics']['views_per_hour'],900)
        row['published_at']=(NOW-timedelta(hours=3)).isoformat();self.write_video([row])
        self.assertEqual(self.detail('douyin:v1')['metrics']['views_per_hour'],300)

    def test_video_same_id_deduplicates_newest(self):
        row=self.write_video(); newer=dict(row,key='douyin:second',likes=999,last_seen_at=(NOW+timedelta(minutes=1)).isoformat())
        self.write_video([row,newer]);items=today.page_view(self.workspace, {'range':['48h'],'source':['V']},NOW)['items']
        self.assertEqual(len(items),1);self.assertEqual(items[0]['metrics']['likes'],999)

if __name__=='__main__':unittest.main()
