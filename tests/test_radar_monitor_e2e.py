"""Isolated browser: actual collector subprocess -> adapter -> Clues API -> UI.

No production service/profile, no gateway or Feishu network. A1 and B2 external
boundaries are offline fixtures; all existing Clues/backend/frontend code is real.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.radar_monitor import Monitor
from test_radar_monitor import when, tweet


class BrowserTests(unittest.TestCase):
    def test_real_clue_browser_path_after_run_once(self):
        from server import Store, Handler
        with tempfile.TemporaryDirectory(prefix='b1-browser-', dir='/tmp') as folder:
            root = Path(folder)
            (root/'scripts').mkdir()
            for filename in ('radar_monitor_config.json', 'radar_common.py'):
                shutil.copy2(ROOT/'scripts'/filename, root/'scripts'/filename)
            (root/'scripts/radar_targets.json').write_text(json.dumps(['test_user']))
            # Fake only remote acquisition. Use the real process runner and A1 adapter.
            (root/'scripts/refresh_twitter_radar.py').write_text(
                "from pathlib import Path\nimport json\nr=Path(__file__).resolve().parents[1]\n"
                "v=json.loads((r/'collector-input.json').read_text())\n"
                "p=r/'workspace/twitter-radar/snapshots/2026-10-15'\np.mkdir(parents=True,exist_ok=True)\n"
                f"import sys\nsys.path.insert(0,{str(ROOT / 'tests')!r})\n"
                "from test_radar_monitor_a1_fixture import commit_fixture\ncommit_fixture(r,v)\n")
            (root/'scripts/radar_feishu.py').write_text(
                "def send_events(events):\n return [{'event_id':e['event_id'],'status':'sent'} for e in events]\n")
            monitor = Monitor(root)
            for hour in (6, 7):
                raw = {'schema_version': 1, 'run_id': f'fixture-{hour}',
                       'started_at': when(hour).isoformat(), 'ended_at': when(hour,1).isoformat(),
                       'attempted': 1, 'success': 1, 'failed': 0, 'api_calls': 1,
                       'accounts': [{'handle': 'test_user', 'status': 'success', 'followers': 20000}],
                       'tweets': [] if hour == 6 else [{**tweet(views=5000), 'author': {'handle':'test_user'}}]}
                (root/'collector-input.json').write_text(json.dumps(raw))
                self.assertIn(monitor.run_once(when(hour))['status'], ('baseline', 'success'))
            state = json.loads((monitor.directory/'state.json').read_text())
            self.assertEqual(state['events'][0]['status'], 'sent')
            store = Store(root/'workspace')
            clue = store.clues.listing(source='爆款监控')['items'][0]
            self.assertEqual(clue['source_time_label'], '推特发布时间')
            class QuietHandler(Handler):
                def log_message(self, *args):
                    pass
            http = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
            http.store = store
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{http.server_port}'
                self.assertNotEqual(http.server_port, 8766)
                evidence = os.environ.get('RADAR_MONITOR_E2E_EVIDENCE', '')
                script = r'''
const assert=require('node:assert/strict'),path=require('node:path'),os=require('node:os');
let pw;try{pw=require('playwright');}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}
(async()=>{
 const base=process.argv[1],id=process.argv[2],evidence=process.argv[3];
 const browser=await pw.chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking','--disable-component-update']});
 try{
  const context=await browser.newContext({viewport:{width:1280,height:960}});
  const page=await context.newPage(),errors=[],outside=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',route=>{
   const u=new URL(route.request().url());
   if(u.origin!==base){outside.push(u.origin);return route.abort();}
   const fixtures={'/api/schedules':{tasks:[],models:[]},'/api/knowledge':{sources:[]},'/api/clues/wechat/status':{configured:false,progress:{run_id:null}}};
   if(fixtures[u.pathname])return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fixtures[u.pathname])});
   return route.continue();
  });
  await page.goto(base+'/clues.html');
  await page.waitForFunction(()=>document.querySelectorAll('[data-clue]').length===1);
  await page.selectOption('#cl-source','爆款监控');
  await page.locator('[data-clue="'+id+'"]').waitFor();
  assert.match(await page.locator('#cl-list').textContent(),/爆款监控/);
  await page.locator('[data-clue="'+id+'"]').click();
  await page.locator('#cl-detail .cl-evidence a').waitFor();
  assert.equal(await page.locator('#cl-detail .cl-evidence a').getAttribute('href'),'https://x.com/test_user/status/1');
  assert.match(await page.locator('#cl-detail').textContent(),/new_fast/);
  assert.match(await page.locator('#cl-detail').textContent(),/浏览 5000/);
  assert.match(await page.locator('#cl-detail').textContent(),/本小时增量/);
  assert.match(await page.locator('#cl-detail').textContent(),/推特发布时间/);
  // Real user edit, then reload: monitor evidence and manual title stay readable.
  await page.locator('#cl-detail [data-detail="edit"]').click();
  await page.fill('#cl-title','人工整理：爆款候选');
  await page.fill('#cl-reason','浏览器验收整理');
  await page.click('#cl-form button[type=submit]');
  await page.locator('#cl-detail h2').filter({hasText:'人工整理：爆款候选'}).waitFor();
  await page.reload();
  await page.locator('[data-clue="'+id+'"]').click();
  await page.locator('#cl-detail h2').filter({hasText:'人工整理：爆款候选'}).waitFor();
  const response=await page.request.get(base+'/api/clues?source='+encodeURIComponent('爆款监控'));
  assert.equal((await response.json()).total,1);
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  if(evidence)await page.screenshot({path:path.join(evidence,'clue-browser.png'),fullPage:true});
  console.log(JSON.stringify({passed:true,isolated:true,real_backend:true,real_browser:true,external_requests:outside.length,page_errors:errors.length,checks:12}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
'''
                result = subprocess.run(['node', '-e', script, base, clue['id'], evidence],
                                        cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertTrue(data['passed'])
                if evidence:
                    Path(evidence).mkdir(parents=True, exist_ok=True)
                    (Path(evidence)/'browser-result.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
                # Duplicate monitor ingest after manual edit must preserve the user's title.
                from scripts.radar_monitor import persist_clue
                persist_clue(root, state['events'][0])
                self.assertEqual(store.clues.detail(clue['id'])['title'], '人工整理：爆款候选')
            finally:
                http.shutdown()
                http.server_close()
                thread.join(5)


if __name__ == '__main__':
    unittest.main()
