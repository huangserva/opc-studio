"""msg_02226d5fc24b 修订：热点宝近1小时视频榜为主，multi仅补充。"""
import contextlib
import copy
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from test_video_radar import ROOT, CONFIG, AT, FixtureClient, load
import video_radar as v


class HourlyBoardTests(unittest.TestCase):
    def setUp(self):
        FixtureClient.fail,FixtureClient.requests,FixtureClient.at=set(),[],AT

    def collect(self,root,mode='hourly',client=FixtureClient):
        with mock.patch.object(v,'now',return_value=FixtureClient.at),contextlib.redirect_stdout(io.StringIO()):
            return v.run(root,mode,client)

    def test_hourly_three_board_requests_two_pages_50_window1_and_daily_window24(self):
        with tempfile.TemporaryDirectory() as temp:
            page=self.collect(Path(temp),'auto')
        boards=[q for q in FixtureClient.requests if 'fetch_hot_total' in q[1] and q[2]['date_window']==1]
        self.assertEqual(len(boards),3)
        self.assertEqual([q[2]['page'] for q in boards],[1,2,1])
        self.assertTrue(all(q[2]['page_size']==50 and q[2]['keyword']=='AI' for q in boards))
        self.assertEqual([q[2].get('sub_type') for q in boards],[1001,1001,None])
        daily=[q for q in FixtureClient.requests if 'fetch_hot_total' in q[1] and q[2]['date_window']==24]
        self.assertEqual(len(daily),4)
        self.assertEqual(page['today']['api_calls'],15)
        self.assertEqual(page['sources']['douyin_realtime']['status'],'ok')
        self.assertIn('排除图集/非视频',page['sources']['douyin_realtime']['note'])

    def test_board_metrics_real_values_zero_is_real_and_nonvideo_old_filtered(self):
        raw=copy.deepcopy(load('dy_board_video_1h_p1')['data']['data']['objs'][0])
        raw['publish_time']=(AT-dt.timedelta(hours=1)).timestamp()
        self.assertIsNone(v.normalize(raw,'dy_board_video_1h',AT,CONFIG))  # 首条实测标题否认AI，新口径必须剔除。
        raw['item_title']='AI生成的测试视频'  # 同一真实指标/URL，独立验证指标与0值，断言不变。
        result=v.normalize(raw,'dy_board_video_1h',AT,CONFIG)
        self.assertEqual(result['plays'],raw['play_cnt']);self.assertEqual(result['author_fans'],raw['fans_cnt'])
        self.assertEqual(result['url'],'https://www.douyin.com/video/'+raw['item_id'])
        self.assertNotIn('item_url',result);self.assertNotIn(raw['item_url'],json.dumps(result))
        raw.update(fans_cnt=0,play_cnt=0)
        result=v.normalize(raw,'dy_board_video_1h',AT,CONFIG)
        self.assertEqual((result['plays'],result['author_fans']),(0,0))
        raw['media_type']=2;self.assertIsNone(v.normalize(raw,'dy_board_video_1h',AT,CONFIG))
        raw['media_type']=4;raw['publish_time']=(AT-dt.timedelta(hours=25)).timestamp()
        self.assertIsNone(v.normalize(raw,'dy_board_video_1h',AT,CONFIG))

    def test_multi_cannot_erase_same_id_board_metrics_sources_merge_and_lowfan_flag(self):
        raw=next(copy.deepcopy(r) for r in load('dy_board_video_1h_p1')['data']['data']['objs'] if r['media_type']==4 and v.normalize(r,'dy_board_video_1h',AT,CONFIG))
        class MergedClient(FixtureClient):
            def request(self,method,path,params,query=False):
                response,at=super().request(method,path,params,query)
                if 'multi_search' in path:
                    row={'aweme_id':raw['item_id'],'desc':raw['item_title'],'create_time':raw['publish_time'],
                         'aweme_type':0,'media_type':4,'statistics':{'digg_count':raw['like_cnt']+10,'play_count':999999},
                         'author':{'nickname':raw['nick_name'],'follower_count':999999}}
                    response={'code':200,'data':[{'status_code':0,'business_data':[{'data':{'aweme_info':row}}]}]}
                return response,at
        with tempfile.TemporaryDirectory() as temp:
            self.collect(Path(temp),client=MergedClient)
            state=json.loads((Path(temp)/'workspace/video-radar/state.json').read_text())
        row=state['items']['douyin:'+raw['item_id']]
        self.assertIn('dy_multi',row['sources']);self.assertIn('dy_board_video_1h',row['sources'])
        self.assertEqual(row['likes'],raw['like_cnt']+10)
        self.assertEqual(row['plays'],raw['play_cnt']);self.assertEqual(row['author_fans'],raw['fans_cnt'])
        low=next(copy.deepcopy(r) for r in load('dy_board_low_fan_1h_p1')['data']['data']['objs'] if v.normalize(r,'dy_board_low_fan_1h',AT,CONFIG))
        self.assertIn('low_fan_hot',v.normalize(low,'dy_board_low_fan_1h',AT,CONFIG)['flags'])

    def test_no_signed_media_item_url_anywhere_in_page(self):
        media={r['item_url'] for name in ('dy_board_video_1h_p1','dy_board_video_1h_p2','dy_board_low_fan_1h_p1') for r in load(name)['data']['data']['objs']}
        with tempfile.TemporaryDirectory() as temp:
            page=self.collect(Path(temp),'auto')
            text=(Path(temp)/'workspace/video-radar/page.json').read_text()
        self.assertNotIn('item_url',text)
        for url in media:self.assertNotIn(url,text)
        self.assertTrue(any('dy_board_' in s for i in page['lists']['douyin_realtime'] for s in i['sources']))

    def test_observed_nonvideo_removes_previously_tracked_video(self):
        xhs_id=load('xhs_search')['data']['data']['items'][0]['note']['id']
        dy_id=next(r['item_id'] for r in load('dy_board_video_1h_p1')['data']['data']['objs'] if v.normalize(r,'dy_board_video_1h',AT,CONFIG))
        class ChangedType(FixtureClient):
            def request(self,method,path,params,query=False):
                response,at=super().request(method,path,params,query)
                if 'search_notes' in path:
                    for row in response['data']['data']['items']:
                        if row['note']['id']==xhs_id:row['note']['type']='normal'
                if 'fetch_hot_total' in path:
                    for row in response['data']['data']['objs']:
                        if row['item_id']==dy_id:row['media_type']=2
                return response,at
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.collect(root)
            first=json.loads((root/'workspace/video-radar/state.json').read_text())['items']
            self.assertIn('xiaohongshu:'+xhs_id,first);self.assertIn('douyin:'+dy_id,first)
            self.collect(root,client=ChangedType)
            after=json.loads((root/'workspace/video-radar/state.json').read_text())['items']
        self.assertNotIn('xiaohongshu:'+xhs_id,after);self.assertNotIn('douyin:'+dy_id,after)

    def test_same_round_first_seen_is_earliest_observation_last_seen_is_latest(self):
        class MovingClock(FixtureClient):
            counter=0
            def request(self,method,path,params,query=False):
                response,_=super().request(method,path,params,query)
                self.counter+=1
                return response,AT+dt.timedelta(seconds=self.counter)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.collect(root,client=MovingClock)
            state=json.loads((root/'workspace/video-radar/state.json').read_text())['items']
        ident=load('xhs_search')['data']['data']['items'][0]['note']['id']
        self.assertEqual(state['xiaohongshu:'+ident]['first_seen_at'],v.stamp(AT+dt.timedelta(seconds=1)))
        self.assertEqual(state['xiaohongshu:'+ident]['last_seen_at'],v.stamp(AT+dt.timedelta(seconds=6)))

    def test_board_failure_is_partial_other_parts_continue_and_old_success_time_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            old=self.collect(Path(temp))
            FixtureClient.at+=dt.timedelta(hours=1)
            FixtureClient.fail={'fetch_hot_total_video_list'}
            page=self.collect(Path(temp))
            stat=json.loads((Path(temp)/'workspace/video-radar/daily-2026-10-16.json').read_text())
        self.assertEqual(page['sources']['douyin_realtime']['status'],'partial')
        self.assertEqual(page['sources']['douyin_realtime']['last_success_at'],old['sources']['douyin_realtime']['last_success_at'])
        self.assertEqual(stat['runs'][-1]['sources']['douyin_realtime']['api_calls'],4)
        self.assertEqual(stat['runs'][-1]['sources']['douyin_realtime']['successes'],2)
        self.assertGreater(len(page['lists']['douyin_realtime']),0)


if __name__=='__main__':unittest.main()
