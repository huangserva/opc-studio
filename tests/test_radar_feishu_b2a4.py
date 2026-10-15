"""B2-a4: actual card/wire URL regressions for raw versus encoded delimiters."""
import ast,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).parent))
from test_radar_feishu import load_module,viral_event,FakeFeishu,FEISHU_PATH,isolate_feishu
DEFAULT='http://127.0.0.1:8766'
def actions(card):return [a for e in card['card']['elements'] if e.get('tag')=='action' for a in e['actions']]
class B2a4Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='b2a4-',dir='/tmp');self.addCleanup(self.tmp.cleanup);self.feishu_path=isolate_feishu(self,Path(self.tmp.name));self.m=load_module(self.feishu_path);self.m.ROOT=Path(self.tmp.name);self.event=viral_event(1)
  self.env=patch.dict(os.environ,{'FEISHU_RADAR_WEBHOOK':'','FEISHU_RADAR_SECRET':'','FEISHU_RADAR_LEDGER':str(Path(self.tmp.name)/'ledger.json'),'NO_PROXY':'*'});self.env.start();self.addCleanup(self.env.stop)
 def check_card(self,base,want):
  with patch.dict(os.environ,{'RADAR_PUBLIC_BASE':base}):card=self.m.build_viral_card([self.event])
  expected={'打开原帖':'https://x.com/author_1/status/2110000000000000001','查看线索':want+'/clues.html?clue=clue-1001','去雷达页标记':want+'/twitter-radar.html#tweet=2110000000000000001'}
  # Labels are a UI detail; assert the exact ordered URL list as well as both targets.
  self.assertEqual([a['url'] for a in actions(card)],list(expected.values()))
  clue,radar=[a['url'] for a in actions(card)][1:]
  self.assertEqual(urlsplit(clue).path,urlsplit(want).path+'/clues.html');self.assertEqual(urlsplit(clue).query,'clue=clue-1001');self.assertEqual(urlsplit(clue).fragment,'')
  self.assertEqual(urlsplit(radar).path,urlsplit(want).path+'/twitter-radar.html');self.assertEqual(urlsplit(radar).query,'');self.assertEqual(urlsplit(radar).fragment,'tweet=2110000000000000001')
  return card
 def test_original_four_bare_delimiters_both_buttons(self):
  for suffix in ['/?','/#','/studio?','/studio#']:
   with self.subTest(suffix=suffix):self.check_card('https://workbench.example'+suffix,DEFAULT)
  print('B2a4 original4: both internal URLs exactly default; paths/query/hash correct')
 def test_middle_and_combined_raw_delimiters(self):
  bases=['https://workbench.example/stu?dio','https://workbench.example/stu#dio','https://workbench.example/?x=1/path','https://workbench.example/#x/path','https://workbench.example/?#','https://workbench.example/#?','https://workbench.example/studio?#','https://workbench.example/studio#?','https://workbench.example?x=%3F','https://workbench.example#x=%23']
  for base in bases:
   with self.subTest(base=base):self.check_card(base,DEFAULT)
  print('B2a4 raw middle/combined: 10/10 both buttons fallback')
 def test_fragment_without_path_and_fragment_only(self):
  for base in ['https://workbench.example#','https://workbench.example?','https://workbench.example?#','#','?','?#']:
   with self.subTest(base=base):self.check_card(base,DEFAULT)
  print('B2a4 no-path/fragment-only: 6/6 both buttons fallback')
 def test_encoded_delimiters_are_literal_path_data(self):
  for suffix in ['/studio%3F','/studio%23/','/stu%3Fdio%23','/studio%3f/','/studio%23%3F','/studio%253F','/%23']:
   base='https://workbench.example'+suffix
   with self.subTest(base=base):self.check_card(base,base.rstrip('/'))
  print('B2a4 percent encoding: 7/7 preserved literally, no query/fragment contamination')
 def test_valid_ipv6_prefix_trailing_slash_unchanged(self):
  for base in ['http://[::1]:12345','http://[::1]:12345/','https://[2001:db8::1]:443/studio/','https://workbench.example/studio','https://workbench.example/studio/','https://workbench.example']:
   with self.subTest(base=base):self.check_card(base,base.rstrip('/'))
  print('B2a4 valid IPv6/prefix/trailing slash: 6/6 exact URLs unchanged')
 def test_all_internal_card_exits_share_one_guard(self):
  sentinel='https://guard.example/prefix'
  with patch.object(self.m,'_public_base',return_value=sentinel) as gate:card=self.m.build_viral_card([self.event])
  self.assertEqual(gate.call_count,2);self.assertEqual([a['url'] for a in actions(card)][1:],[sentinel+'/clues.html?clue=clue-1001',sentinel+'/twitter-radar.html#tweet=2110000000000000001'])
  tree=ast.parse(FEISHU_PATH.read_text());callers=[]
  for node in tree.body:
   if isinstance(node,ast.FunctionDef) and any(isinstance(c,ast.Call) and isinstance(c.func,ast.Name) and c.func.id=='_public_base' for c in ast.walk(node)):callers.append(node.name)
  self.assertEqual(sorted(callers),['_clue_url','_radar_url']);print('B2a4 internal exit inventory: _clue_url + _radar_url -> _public_base; original X URL independent')
 def test_actual_loopback_wire_uses_same_validated_base(self):
  fake=FakeFeishu();self.addCleanup(fake.close)
  bases=['https://workbench.example/?','https://workbench.example/#','https://workbench.example/studio?','https://workbench.example/studio#','https://workbench.example/studio%3F/','http://[::1]:12345/studio/']
  for index,base in enumerate(bases):
   with self.subTest(base=base),patch.dict(os.environ,{'RADAR_PUBLIC_BASE':base}):
    e=viral_event(index+20);before=len(fake.requests);out=self.m.send_events([e],webhook=fake.webhook,secret='');self.assertEqual([r['status'] for r in out],['sent']);self.assertEqual(len(fake.requests),before+1)
    urls=[a['url'] for a in actions(fake.requests[-1]['json'])];want=DEFAULT if index<4 else base.rstrip('/');self.assertEqual(urls[1:],[want+'/clues.html?clue='+e['clue_id'],want+'/twitter-radar.html#tweet='+e['tweet']['tweet_id']])
  print('B2a4 loopback wire: 6 requests sent; 4 raw fallback + encoded path + IPv6 exact')
if __name__=='__main__':unittest.main()
