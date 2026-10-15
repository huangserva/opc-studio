"""B1-c2 path boundary: whole Monitor runs, preserved inode/bytes, no real network."""
import copy,json,os,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent))
from scripts import radar_monitor as code
from test_radar_monitor import when,tweet
from test_radar_monitor_a1_fixture import commit_fixture

class PathBoundaryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='b1c2-case-',dir='/tmp');self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name);self.root=self.base/'project';(self.root/'scripts').mkdir(parents=True)
  for n in ['radar_common.py','radar_monitor_config.json']:shutil.copy2(ROOT/'scripts'/n,self.root/'scripts'/n)
  (self.root/'scripts/radar_targets.json').write_text(json.dumps(['test_user']));self.collected=0;self.sent=[]
  def collector(root,timeout,excluded,started):self.collected+=1;return code.read_snapshot(root,excluded,started)
  self.mon=code.Monitor(self.root,collector=collector,sender=lambda es:self.sent.extend(es) or [],clue_writer=lambda e:{'id':'fake'})
  self.publish(6,100);self.assertEqual(self.mon.run_once(when(6))['status'],'baseline');self.samples=copy.deepcopy(self.mon.load()[0]['samples']);self.page=self.mon.directory/'page-hourly.json';self.state=self.mon.directory/'state.json';self.before_page=(self.page.read_bytes(),self.page.stat().st_ino);self.before_state=(self.state.read_bytes(),self.state.stat().st_ino);self.publish(7,9000)
 def publish(self,h,views):
  source={'schema_version':1,'run_id':'case-'+str(h),'started_at':when(h).isoformat(),'ended_at':when(h,1).isoformat(),'attempted':1,'success':1,'failed':0,'api_calls':1,'accounts':[{'handle':'test_user','status':'success','followers':20000}], 'tweets':[tweet(hour=h,views=views)]}
  commit_fixture(self.root,source);self.snapshot=next((self.root/'workspace/twitter-radar/snapshots').rglob('case-'+str(h)+'.json'))
 def divert(self,path,*,inside=False,relative=False):
  destination=(self.root if inside else self.base)/('relocated-'+path.name);shutil.move(path,destination)
  target=os.path.relpath(destination,path.parent) if relative else destination
  path.symlink_to(target,target_is_directory=destination.is_dir());return destination
 def assert_fails_preserving_page(self,*,no_storage_writes=False):
  self.assertEqual(self.mon.run_once(when(7))['status'],'failed');self.assertEqual(self.mon.load()[0]['samples'],self.samples);self.assertEqual((self.page.read_bytes(),self.page.stat().st_ino),self.before_page);self.assertEqual(self.sent,[])
  if no_storage_writes:self.assertEqual((self.state.read_bytes(),self.state.stat().st_ino),self.before_state)
 def test_workspace_directory_symlink(self):
  self.divert(self.root/'workspace');self.assert_fails_preserving_page(no_storage_writes=True);self.assertEqual(self.collected,1);print('B1c2 workspace link: failed calls=baseline-only samples same page bytes+inode same')
 def test_source_roots_symlink(self):
  # Separate instances for each ancestor, preserving the original test setup.
  for ancestor in ['snapshots','twitter-radar']:
   with self.subTest(ancestor=ancestor):
    self.before_state=(self.state.read_bytes(),self.state.stat().st_ino)
    path=self.root/'workspace/twitter-radar';path=path/'snapshots' if ancestor=='snapshots' else path;target=self.divert(path)
    self.assert_fails_preserving_page(no_storage_writes=ancestor=='twitter-radar');path.unlink();shutil.move(target,path)
  print('B1c2 snapshot/store roots: both failed, samples/page bytes+inode same; redirected state untouched')
 def test_date_directory_symlink_regression(self):
  self.divert(self.snapshot.parent)
  with self.assertRaises(code.PathBoundaryError):code.read_snapshot(self.root)
  self.assert_fails_preserving_page();print('B1c2 date ancestor link: read refused, failed samples/page bytes+inode same')
 def test_inside_workspace_symlink_also_rejected(self):
  self.divert(self.root/'workspace/twitter-radar/snapshots',inside=True,relative=True);self.assert_fails_preserving_page()
  with self.assertRaises(code.PathBoundaryError):code.read_snapshot(self.root)
  print('B1c2 intra-project relative link: rejected despite resolve containment')
 def test_tmp_alias_and_explicit_root_link_are_not_false_rejected(self):
  self.assertTrue(str(self.root).startswith('/tmp/'));self.assertEqual(self.mon.run_once(when(7))['status'],'success');self.assertEqual(code.read_snapshot(self.root)['run_id'],'case-7')
  # The explicitly trusted project root may be named through a link; policy starts below root.resolve().
  alias=self.base/'trusted-project-alias';alias.symlink_to(self.root,target_is_directory=True);self.assertEqual(code.read_snapshot(alias)['run_id'],'case-7');print('B1c2 /tmp -> /private/tmp and explicit trusted root alias: normal read/run accepted')
 def test_monitor_directory_state_and_page_ancestor_symlink(self):
  self.divert(self.mon.directory);self.assert_fails_preserving_page(no_storage_writes=True);self.assertEqual(self.collected,1);print('B1c2 monitor directory link: state/page ancestor blocked before writes or collection')
 def test_state_leaf_symlink_never_read_or_rebuilt(self):
  self.divert(self.state);self.assert_fails_preserving_page(no_storage_writes=True);self.assertTrue(self.mon.load()[1]);print('B1c2 state leaf link: cached trusted samples only; no rebuild, bytes+inode same')
 def test_page_leaf_symlink_prevents_state_commit(self):
  self.divert(self.page);self.assert_fails_preserving_page(no_storage_writes=True)
  modified=copy.deepcopy(self.samples);st=copy.deepcopy(self.mon.load()[0]);st['samples']['1']['views']=999
  with self.assertRaises(code.PathBoundaryError):self.mon.commit(st)
  self.assertEqual((self.state.read_bytes(),self.state.stat().st_ino),self.before_state);print('B1c2 page leaf link: failed and direct commit refused before state write')
 def test_feedback_log_lock_outbox_shadow_daily_leaf_symlinks(self):
  names=['feedback.json','monitor.log','monitor.lock','outbox.jsonl','shadow.jsonl','daily-2026-10-15.json']
  for name in names:
   with self.subTest(name=name):
    path=self.mon.directory/name
    if not path.exists():path.write_text('{}')
    target=self.divert(path);before=(target.read_bytes(),target.stat().st_ino);self.assert_fails_preserving_page(no_storage_writes=True);self.assertEqual((target.read_bytes(),target.stat().st_ino),before);path.unlink();shutil.move(target,path)
  print('B1c2 feedback/log/lock/outbox/shadow/daily links: 6/6 refused, targets unchanged')
 def test_cold_instance_does_not_read_redirected_state(self):
  self.divert(self.mon.directory);cold=code.Monitor(self.root,collector=lambda *a:(_ for _ in ()).throw(AssertionError('collector must not run')))
  with patch.object(Path,'read_text',side_effect=AssertionError('redirected storage read')):
   st,damaged=cold.load();result=cold.run_once(when(7))
  self.assertEqual(st['samples'],{});self.assertTrue(damaged);self.assertEqual(result['status'],'failed');self.assertEqual((self.state.read_bytes(),self.state.stat().st_ino),self.before_state);print('B1c2 cold redirected storage: zero read_text, empty diagnostic state, no writes')
 def test_clue_database_ancestor_symlink(self):
  clues=self.root/'workspace/clues';clues.mkdir();(clues/'clues.db').write_bytes(b'protected database');target=self.divert(clues);before=(target/'clues.db').read_bytes();self.assert_fails_preserving_page(no_storage_writes=True)
  with self.assertRaises(code.PathBoundaryError):code.persist_clue(self.root,{'tweet':{}})
  self.assertEqual((target/'clues.db').read_bytes(),before);print('B1c2 clue database ancestor: run and direct persist refused before SQLite creation')
 def test_rejected_source_retains_normal_failure_accounting(self):
  self.divert(self.root/'workspace/twitter-radar/snapshots');self.assert_fails_preserving_page()
  for hour in (8,9):self.assertEqual(self.mon.run_once(when(hour))['status'],'failed')
  st=self.mon.load()[0];self.assertEqual(st['consecutive_failures'],3);self.assertTrue(st['fault_active']);self.assertEqual(sum(e['type']=='monitor_fault' for e in st['events']),1);self.assertEqual(st['samples'],self.samples);self.assertEqual((self.page.read_bytes(),self.page.stat().st_ino),self.before_page);print('B1c2 unsafe source: existing 3-failure accounting/fault event preserved; samples/page unchanged')
 def test_atomic_write_uses_same_root_boundary(self):
  folder=self.root/'workspace/redirect';folder.mkdir();outside=self.divert(folder)
  with self.assertRaises(code.PathBoundaryError):code.atomic_write(folder/'state.json','unsafe',root=self.root)
  self.assertEqual(list(outside.iterdir()),[]);print('B1c2 atomic writer: ancestor link refused, zero target/temp creations')
 def test_public_and_targets_source_symlinks(self):
  for path in [self.root/'public/twitter-radar-bundle.json',self.root/'scripts/radar_targets.json']:
   with self.subTest(path=path.name):
    target=self.divert(path)
    with self.assertRaises(code.PathBoundaryError):code.read_snapshot(self.root)
    path.unlink();shutil.move(target,path)
  print('B1c2 public bundle and target-list sources: linked leaf refused')
if __name__=='__main__':unittest.main()
