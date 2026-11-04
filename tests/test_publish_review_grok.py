import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
import publish_review as P

NOW=datetime(2026,11,4,12,tzinfo=P.BEIJING)
class GrokTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='publish-grok-test-',dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.reports=self.root/'grok';self.posts=self.reports/'posts';self.posts.mkdir(parents=True)
        self.reader=P.GrokReports(self.reports,NOW)
        P._REPORT_CACHE.clear()
    def write(self,name,data):
        path=self.reports/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False));return path
    def post(self,identifier='100',kind='主帖',**kwargs):
        return dict(id=identifier,type=kind,url='https://x.com/servasyy_ai/status/'+identifier,text='第一行\n第二行\n第三行',created_at='2026-11-03T08:00:00+08:00',metrics={'views':10,'likes':2,'bookmarks':1,'retweets':0,'replies':0,'quotes':0},**kwargs)
    def day(self,posts=None,day='2026-11-04',capture='2026-11-04T09:00:00+08:00'):
        return self.write('posts/'+day+'.json',{'date':day,'snapshots':[{'captured_at':capture,'posts':posts if posts is not None else [self.post()]}]})
    def test_multiple_days_and_rounds_are_chronological_not_summed(self):
        p=self.post();self.day([p],day='2026-11-03',capture='2026-11-03T10:00:00+08:00')
        q=self.post();q['metrics']['views']=80
        self.write('posts/2026-11-04.json',{'snapshots':[{'captured_at':'2026-11-04T11:00:00+08:00','posts':[q]},{'captured_at':'2026-11-04T09:00:00+08:00','posts':[p]}]})
        result=self.reader.listing();row=result['posts'][0];self.assertEqual(len(row['series']),3);self.assertEqual(row['metrics']['views'],80);self.assertEqual(result['week']['views'],80);self.assertEqual(result['week']['total'],1)
    def test_duplicate_ids_same_capture_last_wins(self):
        p=self.post();q=self.post();q['metrics']['views']=20;self.day([p,q]);r=self.reader.listing();self.assertEqual(len(r['posts']),1);self.assertEqual(len(r['posts'][0]['series']),1);self.assertEqual(r['posts'][0]['metrics']['views'],20)
    def test_metrics_missing_string_null_negative_and_nan(self):
        for raw in (None,'bad',{'views':-2,'likes':None,'bookmarks':'NaN','retweets':True,'replies':{},'quotes':'-1'}):
            p=self.post();p['metrics']=raw;self.day([p]);result=self.reader.listing();self.assertTrue(all(v is None for v in result['posts'][0]['metrics'].values()));self.assertIsNone(result['week']['views']);json.dumps(result,allow_nan=False)
    def test_no_files_header_counts_unknown_not_zero(self):
        r=self.reader.listing();self.assertTrue(all(v is None for v in r['week']['counts'].values()));self.assertIsNone(r['week']['views']);self.assertEqual(r['week']['missing_days'],7)
    def test_missing_metrics_key_is_unknown(self):
        p=self.post();del p['metrics'];self.day([p]);self.assertTrue(all(v is None for v in self.reader.listing()['posts'][0]['metrics'].values()))
    def test_later_round_unknown_capture_does_not_silently_reuse_old_values(self):
        p=self.post();q=self.post();q['metrics']={'views':50}
        self.write('posts/2026-11-04.json',{'snapshots':[{'captured_at':'2026-11-04T08:00:00+08:00','posts':[p]},{'captured_at':'bad','posts':[q]}]})
        row=self.reader.listing()['posts'][0];self.assertEqual(row['metrics']['views'],50);self.assertEqual(row['captured_at'],'');self.assertEqual(len(row['series']),2)
    def test_numeric_strings_and_zero(self):
        p=self.post();p['metrics']={'views':'1,200','likes':0};self.day([p]);r=self.reader.listing();self.assertEqual(r['posts'][0]['metrics']['views'],1200);self.assertEqual(r['posts'][0]['metrics']['likes'],0)
    def test_invalid_latest_does_not_fall_back_to_old_metric(self):
        p=self.post();q=self.post();q['metrics']=None
        self.write('posts/2026-11-04.json',{'snapshots':[{'captured_at':'2026-11-04T08:00:00+08:00','posts':[p]},{'captured_at':'2026-11-04T09:00:00+08:00','posts':[q]}]})
        self.assertIsNone(self.reader.listing()['posts'][0]['metrics']['views'])
    def test_equivalent_capture_formats_dedupe_same_instant(self):
        p=self.post(captured_at='2026-11-04 09:19 Asia/Shanghai');q=self.post(captured_at='2026-11-04T01:19:00Z');self.day([p,q]);self.assertEqual(len(self.reader.listing()['posts'][0]['series']),1)
    def test_empty_snapshots_missing_and_corrupt_are_not_zero(self):
        self.write('posts/2026-11-04.json',{'snapshots':[]});(self.posts/'2026-11-03.json').write_text('{bad');self.day(day='2026-11-02')
        r=self.reader.listing();days={d['date']:d for d in r['days']};self.assertEqual(days['2026-11-04']['status'],'empty');self.assertEqual(days['2026-11-03']['status'],'error');self.assertEqual(days['2026-11-01']['message'],'Grok 当天没抓');self.assertEqual(r['total'],1)
    def test_malformed_snapshot_and_posts_are_isolated(self):
        self.write('posts/2026-11-04.json',{'snapshots':[None,{'posts':'bad'},{'posts':[None,7,{},self.post()],'captured_at':None}]});r=self.reader.listing();self.assertEqual(r['total'],1);self.assertEqual(r['posts'][0]['captured_at'],'')
    def test_naive_created_at_is_unknown_not_local_timezone(self):
        p=self.post();p['created_at']='2026-11-04T01:00:00';self.day([p]);r=self.reader.listing();self.assertEqual(r['posts'][0]['created_at'],'');self.assertEqual(r['week']['total'],0)
    def test_explicit_shanghai_capture_format(self):
        p=self.post(captured_at='2026-11-04 09:19 Asia/Shanghai');self.day([p]);self.assertEqual(P.stamp(self.reader.listing()['posts'][0]['captured_at']),P.stamp('2026-11-04T09:19:00+08:00'))
    def test_reply_counts_aliases_and_unknown_are_separate(self):
        self.day([self.post(str(i),kind) for i,kind in enumerate(('post','long','quote','reply','video','retweet','bad'))]);r=self.reader.listing();self.assertEqual([p['type'] for p in r['posts']].count('主帖'),2);self.assertTrue(all(p['type'] in P.POST_TYPES[:3] for p in r['posts']));self.assertEqual(len(r['replies']),r['type_counts']['回复']);self.assertEqual(r['week']['counts']['回复'],1);self.assertEqual(len(r['other_posts']),2)
    def test_window_is_thirty_files_only(self):
        self.day(day='2026-10-05');self.day(day='2026-11-05');self.day();r=self.reader.listing();self.assertEqual(r['total'],1);self.assertEqual(len(r['days']),30)
    def test_oversize_file_not_opened(self):
        path=self.posts/'2026-11-04.json'
        with path.open('wb') as f:f.truncate(P.MAX_REPORT_BYTES+1)
        with patch('io.open',side_effect=AssertionError('must_not_open')):r=self.reader.listing()
        self.assertIn('文件过大',r['days'][-1]['message']);self.assertEqual(r['total'],0)
    def test_mtime_cache_reloads_only_changed_file_and_bad_file_cached(self):
        path=self.day();original=io.open;calls=[]
        def opening(p,*a,**kw):calls.append(str(p));return original(p,*a,**kw)
        with patch('io.open',side_effect=opening):self.reader.listing();self.reader.listing()
        self.assertEqual(calls.count(str(path)),1)
        path.write_text('{broken');os.utime(path,ns=(path.stat().st_atime_ns,path.stat().st_mtime_ns+1000000))
        calls=[]
        with patch('io.open',side_effect=opening):a=self.reader.listing();b=self.reader.listing()
        self.assertEqual(calls.count(str(path)),1);self.assertEqual(a['days'][-1]['status'],'error')
    def test_root_readonly_modes_and_symlink_rejection(self):
        self.day();outside=self.root/'secret.json';outside.write_text('{"snapshots":[]}');(self.posts/'2026-11-03.json').symlink_to(outside)
        original=io.open;opened=[]
        def guard(path,mode='r',*a,**kw):
            opened.append((str(path),mode));self.assertEqual(mode,'rb');self.assertTrue(Path(path).resolve().is_relative_to(self.reports));return original(path,mode,*a,**kw)
        before={str(p):p.read_bytes() for p in self.reports.rglob('*.json')}
        with patch('io.open',side_effect=guard):r=self.reader.listing()
        self.assertTrue(opened);self.assertIn('符号链接',r['days'][-2]['message']);self.assertEqual(before,{str(p):p.read_bytes() for p in self.reports.rglob('*.json')})
    def test_route_rejects_root_or_traversal_query_before_read(self):
        with patch.object(P.GrokReports,'listing',side_effect=AssertionError('no read')):
            for q in ({'root':['/tmp']},{'file':['../../etc/passwd']}):
                with self.assertRaises(ValueError):P.get_route(None,urlsplit('/api/publish/review'),q)
        self.assertEqual(self.reader._read('../secret.json')[1],'文件名无效')
    def test_from_radar_exact_field_precedes_local_no_similarity_guess(self):
        p=self.post(from_radar={'report':'daily/2026-11-03.json','url':'https://x.com/original/status/99','title':'明确来源'})
        similar=self.post('101');similar['text']=p['text'];self.day([p,similar]);r=self.reader.listing({'100':{'url':'/library.html?item=x%3A99','basis':'local'}})
        by={v['id']:v for v in r['posts']};self.assertEqual(by['100']['from_radar']['basis'],'Grok 明确标记');self.assertIsNone(by['101']['from_radar'])
    def test_from_radar_invalid_types_and_paths_are_ignored(self):
        for field in ([],True,{'report':'../../secret.json','url':'https://x.com/demo/status/1'},{'url':'javascript:alert(1)'},{'url':None}):
            self.day([self.post(from_radar=field)]);self.assertIsNone(self.reader.listing()['posts'][0]['from_radar'])
    def test_weekly_raw_nested_text_and_bad_latest_fallback(self):
        text={'结论':['原句\n第二段','<script>不能执行</script>'],'posts':[{'reason':'原样保留'}]};self.write('weekly/2026-W43.json',{'week':'2026-W43','generated_at':'2026-10-25T12:00:00+08:00','output_review':text});p=self.write('weekly/2026-W44.json',{});p.write_text('bad');r=self.reader.listing();self.assertEqual(r['weekly']['output_review'],text);self.assertTrue(r['weekly']['warnings'])
    def test_daily_missing_latest_date_and_malformed_fields(self):
        self.assertNotIn('date',self.reader.listing()['daily']);self.write('posts/review-2026-11-03.json',{'summary':['原句',None,{}],'best':[{'id':'100','why':'原话'},None],'worst':'bad','notes':['注意',7],'generated_at':'2026-11-03T20:00:00+08:00','extra':True});d=self.reader.listing()['daily'];self.assertEqual(d['date'],'2026-11-03');self.assertFalse(d['is_today']);self.assertEqual(d['summary'],['原句']);self.assertEqual(d['worst'],[])
    def test_daily_today_precedes_old_and_future_ignored(self):
        for day in ('2026-11-03','2026-11-04','2026-11-05'):self.write('posts/review-'+day+'.json',{'summary':[day]})
        self.assertTrue(self.reader.listing()['daily']['is_today']);self.assertEqual(self.reader.listing()['daily']['date'],'2026-11-04')
    def test_week_partial_unknown_explained_and_beijing_boundary(self):
        a=self.post('100');a['created_at']='2026-10-28T16:00:00Z';b=self.post('101');b['metrics']=None;c=self.post('102');c['created_at']='2026-10-28T15:59:59Z';self.day([a,b,c]);w=self.reader.listing()['week'];self.assertEqual(w['total'],2);self.assertEqual(w['views'],10);self.assertEqual(w['views_unknown_posts'],1)
    def test_local_chain_requires_today_use_and_exact_published_id(self):
        work=self.root/'workspace';library=work/'library';folder=library/'sample';folder.mkdir(parents=True)
        (folder/'article.json').write_text('{"title":"作品"}');(folder/'source-item.json').write_text('{"item_id":"x:99","sources":["G"]}')
        (folder/'deliveries.json').write_text(json.dumps([{'id':'pub','kind':'published_manual','platform':'x','url':'https://twitter.com/demo/status/100?x=1'}]))
        today=work/'today';today.mkdir();(today/'feedback.json').write_text('{"x:99":{"status":"used"}}');self.day()
        with patch.object(P,'GROK_REPORTS_ROOT',self.reports):
            result=P.PublishReview(SimpleNamespace(workspace=work,library=library)).listing()
            self.assertEqual(result['grok']['posts'][0]['from_radar']['url'],'/library.html?item=x%3A99');self.assertEqual(result['total'],1)
            (today/'feedback.json').write_text('{}');self.assertIsNone(P.PublishReview(SimpleNamespace(workspace=work,library=library)).listing()['grok']['posts'][0]['from_radar'])
if __name__=='__main__':unittest.main()
