"""Real HTTP entry points on random ports and synthetic /tmp databases only."""
from contextlib import closing
from datetime import datetime,timezone
import json
import os
import grok_bot_radar as G
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request,urllib.error
from server import Handler,ThreadingHTTPServer
import clue_store_v2 as S
import wechat_tools as W
import wechat_tools_config as C
import wechat_tools_view as V
from test_wechat_tools import TOPICS,message,checked

class HTTPTest(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix='r3-http-',dir='/tmp');self.addCleanup(t.cleanup);self.root=Path(t.name);(self.root/'clues').mkdir();db=self.root/'clues/clues.db';__import__('sqlite3').connect(db).close();S.schema(db,'seed',TOPICS);(self.root/'wechat-tools').mkdir();(self.root/'wechat-tools/interest-topics.json').write_text(json.dumps(TOPICS));W.run(self.root,{'2026-10-28'},'r3',rows=[message()],title_check=checked)
        reports=self.root/'reports';reports.mkdir();(reports/'interest').mkdir();(reports/'interest-topics.md').write_text('# 兴趣\n\n## 当前方向\n'+''.join('- '+t+'\n' for t in TOPICS))
        self.enterContext(patch.dict(os.environ,{'GROK_BOT_REPORTS_DIR':str(reports)}));self.enterContext(patch.object(G,'BUNDLE_PATH',self.root/'bundle.json'));(self.root/'bundle.json').write_text('{"tweets":[],"vip_tweets":[]}')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.store=SimpleNamespace(workspace=self.root);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close);self.base='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,data=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-Article-Studio':'1','Origin':self.base})
        try:
            with urllib.request.urlopen(req,timeout=3) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as r:
            with r:return r.code,json.load(r)
    def test_settings_read_save_conflict_and_invalid(self):
        status,v=self.request('/api/settings/wechat-tools');self.assertEqual(status,200)
        status,saved=self.request('/api/settings/wechat-tools',dict(groups=[C.DEFAULT_GROUP,'合成精选新群'],revision=v['revision']));self.assertEqual(status,200)
        self.assertEqual(self.request('/api/settings/wechat-tools',dict(groups=[],revision=v['revision']))[0],409)
        self.assertEqual(self.request('/api/settings/wechat-tools',dict(groups=['\u200b'+C.DEFAULT_GROUP],revision=saved['revision']))[0],400)
    def test_collection_invalid_values_are_400_not_full_table(self):
        for query in ('collection=','collection=bad','collection=tools&collection=','collection=x%27%20OR%201=1--','collection=tools&limit=no'):
            self.assertEqual(self.request('/api/clue-inbox?'+query)[0],400)
        code,v=self.request('/api/clue-inbox?collection=tools');self.assertEqual(code,200);self.assertEqual(len(v['items']),1)
    def test_detail_feedback_keep_and_references(self):
        item=V.cards(self.root)[0];code,d=self.request('/api/clue-objects/'+item['clue_id']);self.assertEqual(code,200);evidence=d['evidence']
        self.assertFalse((self.root/'items/items.db').exists());code,v=self.request('/api/today/feedback',dict(id=item['id'],channel='interest',action='keep'));self.assertEqual(code,200);self.assertEqual(v['status'],'kept');code,d=self.request('/api/clue-objects/'+item['clue_id']);self.assertEqual(d['evidence'],evidence)
        self.assertTrue(any(r['relation_type']=='additional_destination' for r in d['relations']))
    def test_cross_source_interest_actual_http(self):
        now=datetime.now(timezone.utc).isoformat();grok=dict(id='x:123',kind='post',sources=['G'],channel='interest',title='合成X工具',published_at=now,fetched_at=now,author=dict(name='合成作者',handle='synthetic'),full_text='AI Agent工具',content_insufficient=False,topic=TOPICS[4],metrics={},hot=False,tier='',fit_score=None,doable=None,content_complete=True,status='new',feedback=None,relevance_note=None,rss_boards=[],reply_change=None,what_new='合成',usable=[],relevance=3,incomplete_reasons=[])
        # Patch in context only; no leaked patches, no Grok real-file writes.
        with patch('interest_channel.snapshot',return_value=([grok],{'G':dict(updated_at=now,error=None)})),patch('interest_projection.schedule'):
            # 模拟正式安装的离线预建；无缓存/准备中另有专门反例，既有HTTP断言保持不变。
            from interest_projection import publish
            publish(self.root)
            code,v=self.request('/api/today?channel=interest&range=today');self.assertEqual(code,200);self.assertEqual({s for i in v['items'] for s in i['sources']},{'G','W'});self.assertEqual(v['total'],2)
            tool=next(p for p in v['items'] if p['kind']=='tool')
            self.assertEqual(self.request('/api/today/feedback',dict(id=tool['id'],channel='interest',action='inaccurate'))[0],200)
            code,detail=self.request('/api/today/item?channel=interest&id='+tool['id']);self.assertEqual(code,200);self.assertEqual(detail['feedback'],'inaccurate')
            self.assertEqual(self.request('/api/today/feedback',dict(id=tool['id'],channel='interest',action='keep'))[0],200)
            code,detail=self.request('/api/today/item?channel=interest&id='+tool['id']);self.assertEqual(code,200);self.assertEqual(detail['status'],'kept')
            code,latest=self.request('/api/today?channel=interest&range=today');self.assertEqual(code,200);self.assertEqual(next(p for p in latest['items'] if p['id']==tool['id'])['status'],'kept')
    def test_private_reference_is_the_only_tool_evidence(self):
        _,v=self.request('/api/clue-inbox?collection=tools');_,d=self.request('/api/clue-objects/'+v['items'][0]['id']);self.assertEqual(d['evidence'][0]['reference'],dict(namespace='knowledge:wechat',message_id='m1'));self.assertEqual(d['content_versions'],[])

if __name__=='__main__':unittest.main()
