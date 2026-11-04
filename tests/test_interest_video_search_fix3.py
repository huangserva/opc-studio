"""Current production 0/7/1 -> one 2/0/1 fallback; no legacy parameter fixture."""
import json,unittest
import test_interest_video_search_fix2 as historical
from scripts import interest_video_search as M

class Fix3Tests(unittest.TestCase):
 setUp=historical.Fix2Tests.setUp
 collect=historical.Fix2Tests.collect
 send_empty=historical.Fix2Tests.send_empty
 def triplet(self,p):return tuple(p[k] for k in ('sort_type','publish_time','content_type'))
 def assert_budget(self,count):
  budget=json.loads((M.storage(self.root)/'budget'/f'{self.at.date()}.json').read_text())
  self.assertEqual(len(budget['calls']),count)
  self.assertTrue(all(c['status'] in ('ok','failed') for c in budget['calls']))
 def test_current_all_words_primary_and_fallback_xhs_unchanged(self):
  words=[w for d in self.cfg['directions'] for w in d['keywords']];self.assertEqual(len(words),16)
  for word in words:
   for fallback,expected in [(False,('0','7','1')),(True,('2','0','1'))]:
    with self.subTest(word=word,fallback=fallback):
     method,path,p=M.request_spec('douyin',word,fallback)
     self.assertEqual((method,path),('POST','douyin/search/fetch_multi_search'))
     self.assertEqual(self.triplet(p),expected)
     self.assertEqual({k:v for k,v in p.items() if k not in ('sort_type','publish_time','content_type')},dict(keyword=word,cursor=0,filter_duration='0',search_id='',backtrace=''))
     self.assertEqual(M.request_spec('xhs',word,fallback),('GET','xiaohongshu/app_v2/search_notes',dict(keyword=word,page=1,sort_type='time_descending',note_type='视频笔记',time_filter='一天内')))
 def test_current_400_then_200_exact_sequence_and_reservations(self):
  self.sent=[];attempts={}
  def send(url,method,params,key,timeout):
   if params is not None:
    self.sent.append(dict(params));seen=attempts.setdefault(params['keyword'],[]);seen.append(self.triplet(params))
    if len(seen)==1:raise M.V.RadarError('http_400')
    self.assertEqual(len(seen),2)
    return {'code':200,'data':[{'status_code':0,'business_data':[]}]}
   return self.send_empty(url,method,params,key,timeout)
  r=self.collect(send)
  self.assertEqual(r['status'],'ok');self.assertEqual(len(r['calls']),48);self.assertEqual(r['fallback_count'],16);self.assertEqual(len(self.sent),48)
  self.assertEqual(len(attempts),16);self.assertTrue(all(v==[('0','7','1'),('2','0','1')] for v in attempts.values()));self.assert_budget(48)
 def test_current_400_then_400_stops_at_one_fallback(self):
  self.sent=[];attempts={}
  def send(url,method,params,key,timeout):
   if params is not None:
    self.sent.append(dict(params));attempts.setdefault(params['keyword'],[]).append(self.triplet(params));raise M.V.RadarError('http_400')
   return self.send_empty(url,method,params,key,timeout)
  r=self.collect(send)
  self.assertEqual(r['status'],'partial');self.assertEqual(len(r['calls']),48);self.assertEqual(len(self.sent),48);self.assertEqual(r['fallback_count'],16);self.assert_budget(48)
  self.assertEqual(len(attempts),16);self.assertTrue(all(v==[('0','7','1'),('2','0','1')] for v in attempts.values()))
  self.assertTrue(all(q['fallback']['status']=='failed' for q in r['queries'] if q['platform']=='douyin'))
 def test_current_daily_64_and_retries_hard_stop_80(self):
  self.sent=[];a=self.collect(self.send_empty);b=self.collect(self.send_empty)
  self.assertEqual((len(a['calls']),len(b['calls'])),(32,32));self.assertEqual(len(self.sent),64);self.assert_budget(64)
  self.assertTrue(all(p is None or self.triplet(p)==('0','7','1') for p in self.sent))
  def fail(url,method,params,key,timeout):
   self.sent.append(None if params is None else dict(params));raise M.V.RadarError('http_400')
  c=self.collect(fail);self.assertEqual(c['status'],'budget_exhausted');self.assertEqual(len(c['calls']),16);self.assertEqual(len(self.sent),80);self.assert_budget(80)
  self.assertGreater(c['fallback_count'],0);self.assertEqual(len(self.collect(fail)['calls']),0);self.assertEqual(len(self.sent),80);self.assert_budget(80)
 def test_current_complete_payload_matches_six_real_experiment_requests(self):
  fixture=json.loads((historical.ROOT/'tests/fixtures/interest_video_search/experiment_bc_requests.json').read_text())
  self.assertEqual(len(fixture['requests']),6)
  self.assertEqual([r['variant'] for r in fixture['requests']].count('b'),3)
  self.assertEqual([r['variant'] for r in fixture['requests']].count('c'),3)
  for recorded in fixture['requests']:
   with self.subTest(call=recorded['n'],variant=recorded['variant']):
    method,path,params=M.request_spec('douyin',recorded['word'],recorded['variant']=='b')
    self.assertEqual((method,path),('POST','douyin/search/fetch_multi_search'))
    self.assertEqual(params,recorded['params'])
if __name__=='__main__':unittest.main()
