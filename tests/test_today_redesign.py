import copy
import unittest
from unittest.mock import patch
import today_view as today
import test_today_view as fixture

class RedesignTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.TodayTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown)
    def interest(self,rows,**query):
        import interest_channel
        data=dict(items=rows,configured_topics=[],sources={},top=[],total=len(rows),page=1,filtering={},_official_hints={},daily_counts={},sort_policy='原轮排')
        with patch.object(interest_channel,'cross_source_page',return_value=copy.deepcopy(data)):
            return today.page_view(self.fixture.workspace,{'channel':['interest'],**{k:[v] for k,v in query.items()}},fixture.NOW)
    def row(self,i,**extra):
        data=dict(id='x:'+str(i),channel='interest',sources=['G'],kind='post',title='Agent '+str(i),full_text='正文',author={'name':'作者','handle':'fixture','followers':1000},published_at=fixture.NOW.isoformat(),fetched_at=fixture.NOW.isoformat(),tier='其他',topic='',interest_matches=[],metrics={'views':100,'likes':1,'bookmarks':0},relevance=1,content_insufficient=False)
        data.update(extra);return data
    def test_interest_tier_before_pagination(self):
        rows=[self.row(i,tier='其他' if i<60 else '爆款') for i in range(80)]
        result=self.interest(rows,tier='爆款');self.assertEqual(20,result['total']);self.assertTrue(all(p['tier']=='爆款' for p in result['items']))
        self.assertEqual(60,self.interest(rows,tier='其他')['total'])
        self.assertEqual(0,self.interest(rows,tier='起势')['total'])
    def test_interest_default_preserves_original_order(self):
        rows=[self.row(3,relevance=0),self.row(1,relevance=3),self.row(2,relevance=2)]
        self.assertEqual(['x:3','x:1','x:2'],[p['id'] for p in self.interest(rows)['items']])
    def test_explicit_sort_three_modes_and_ties(self):
        rows=[self.row(1,relevance=3,metrics={'views':50,'views_per_hour':10},published_at='2026-10-31T01:00:00+00:00'),self.row(2,relevance=1,metrics={'views':500,'views_per_hour':100},published_at='2026-10-31T03:00:00+00:00'),self.row(3,relevance=2,metrics={'views':100,'views_per_hour':30},published_at='2026-10-31T02:00:00+00:00')]
        for mode,expected in [('relevance',[1,3,2]),('heat',[2,3,1]),('time',[2,3,1])]:
            with self.subTest(mode=mode):self.assertEqual(['x:'+str(i) for i in expected],[p['id'] for p in self.interest(rows,sort=mode)['items']])
    def test_invalid_sort_and_duplicate_rejected(self):
        for value in [['random'],['time','heat']]:
            with self.assertRaises(ValueError):today.page_view(self.fixture.workspace,{'sort':value},fixture.NOW)
        with self.assertRaises(ValueError):self.interest([],tier='随便')
    def test_discovery_time_and_heat_after_gate(self):
        for mode in ['time','heat','relevance']:
            d=today.page_view(self.fixture.workspace,{'range':['7d'],'sort':[mode]},fixture.NOW)
            self.assertEqual(mode,d['selected_sort']);self.assertTrue(d['items'])
            if mode=='heat':self.assertEqual(sorted([p['metrics']['heat'] for p in d['items']],reverse=True),[p['metrics']['heat'] for p in d['items']])
    def test_top_membership_kept_but_explicit_heat_order_applied(self):
        self.fixture.write([self.fixture.row('1',views_per_hour=1,fit_score=3,doable=3),self.fixture.row('2',views_per_hour=100,fit_score=2,doable=2)])
        d=today.page_view(self.fixture.workspace,{'range':['7d'],'tier':['top'],'sort':['heat']},fixture.NOW)
        self.assertEqual(['x:2','x:1'],[p['id'] for p in d['items']])
    def test_official_sort_modes(self):
        from datetime import timedelta
        a=self.fixture.row('1',author_type='official',views_per_hour=1,fit_score=3,doable=3)
        b=self.fixture.row('2',author_type='official',views_per_hour=100,fit_score=2,doable=2)
        a['created_at']=(fixture.NOW-timedelta(hours=2)).isoformat();self.fixture.write([a,b])
        for mode,ids in [('heat',['x:2','x:1']),('time',['x:2','x:1']),('relevance',['x:1','x:2'])]:
            with self.subTest(mode=mode):
                d=today.page_view(self.fixture.workspace,{'channel':['official'],'range':['7d'],'sort':[mode]},fixture.NOW)
                self.assertEqual(ids,[p['id'] for p in d['items']])
    def test_interest_summary_uses_detail_merged_numbers_only(self):
        import sqlite3,json
        from interest_projection import path
        database=path(self.fixture.workspace);database.parent.mkdir(exist_ok=True)
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE cards(period TEXT,id TEXT,summary TEXT,detail TEXT)')
            db.execute('INSERT INTO cards VALUES(?,?,?,?)',('7d','x:1',json.dumps({'metrics':{'views':13294,'likes':215,'bookmarks':233,'retweets':31,'replies':13,'quotes':1,'unknown':None,'bad':True},'full_text':'正文不能进入列表'}),'不得读取detail正文'))
        original=self.row(1,metrics={'views':None},title='今日手动来源',full_text='原列表占位')
        d=self.interest([original])['items'][0]
        self.assertEqual({'views':13294,'likes':215,'bookmarks':233,'retweets':31,'replies':13,'quotes':1},d['metrics'])
        self.assertNotIn('full_text',d);self.assertEqual('今日手动来源',d['title']);self.assertEqual({'views':None},original['metrics'])
    def test_merged_metrics_sort_before_pagination(self):
        import sqlite3,json
        from interest_projection import path
        database=path(self.fixture.workspace);database.parent.mkdir(exist_ok=True)
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE cards(period TEXT,id TEXT,summary TEXT)')
            for i in range(70):db.execute('INSERT INTO cards VALUES(?,?,?)',('7d','x:'+str(i),json.dumps({'metrics':{'views':i,'views_per_hour':i}})))
        d=self.interest([self.row(i,metrics={}) for i in range(70)],sort='heat')
        self.assertEqual('x:69',d['items'][0]['id']);self.assertEqual(50,len(d['items']));self.assertEqual(69,d['items'][0]['metrics']['views'])
    def test_thresholds_come_from_constant(self):
        with patch.object(today,'HOT_VIEWS',123456):
            for q in [{},{'channel':['official']},{'channel':['interest']}]:
                d=self.interest([]) if q.get('channel')==['interest'] else today.page_view(self.fixture.workspace,q,fixture.NOW)
                self.assertEqual(123456,d['heat_thresholds']['hot_views']);self.assertIn('来源',d['heat_thresholds']['tiers']['起势'])
    def test_regular_filter_excludes_all_existing_hot_tiers(self):
        self.assertFalse(today.matches_tier({'tier':'低粉爆款'},'其他'));self.assertTrue(today.matches_tier({'tier':'兴趣'},'其他'))

if __name__=='__main__':unittest.main()
