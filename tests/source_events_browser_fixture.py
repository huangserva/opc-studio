"""Two isolated HTTP services and a deterministic model; never import live data."""
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
from datetime import datetime,timezone
from types import SimpleNamespace
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT.parent/'wechat-assistant'
sys.path[:0]=[str(PACKAGE),str(ROOT)]
import server
from knowledge_store import Knowledge
from scheduled_tasks import ScheduledTasks
from wechat_source_client import WechatSourceClient
from wechat_source.clue_pipeline import CluePipeline
from wechat_source.wechat_radar_pipeline import RadarPipeline
from wechat_source.service import WechatSource
from wechat_source.event_processors import Processors
from wechat_source.daemon import Jobs,Handler
import wechat_source.wechat_radar_pipeline as radar_module


def main(folder):
    folder=Path(folder);root=folder/'workspace';root.mkdir()
    store=server.Store(root);store._knowledge=Knowledge(store,sources=[])
    now=datetime.now(timezone.utc).isoformat()
    with store.knowledge.db() as db:
        db.execute('INSERT INTO sources(id,config,status,synced_at) VALUES(?,?,?,?)',('wechat','{"id":"wechat","kind":"wechat"}','ready',now))
        db.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
        for key,chat,sender,body in [('group','fixture@chatroom','__self__','引用校验方法 https://example.invalid/group'),('private','wxid_fixture','fixture-peer','品牌甲：请为品牌甲写一条推文，合作报价请沟通。 https://example.invalid/private')]:
            p={'chatroom_id':chat,'sender':sender,'is_self':sender=='__self__','conversation_type':'group' if key=='group' else 'private','local_id':1,'timestamp':time.time()}
            store.knowledge._write(db,'wechat',key,'wechat','品牌甲 · 作者 · 日期',body,modified=now,provenance=p)
    import clue_store_v2
    clue_store_v2.schema(store.clues.path,'browser-fixture',list(clue_store_v2.DIRECTION_KEYS))
    runtime=SimpleNamespace(workspace=root,knowledge=store.knowledge,clues=store.clues,events=store.events)
    runtime.clue_pipeline=CluePipeline(runtime);runtime.radar_pipeline=RadarPipeline(runtime)
    capture_calls=[];model_calls=[]
    def capture(cancel,deadline):
        capture_calls.append(1);(folder/'capture-count.json').write_text(json.dumps(len(capture_calls)))
        return {'status':'done','fresh':True,'index_status':'ready'}
    runtime.clue_pipeline.prepare_cache=capture
    def model(owner,identity,prompt,cancel,config,deadline,system_prompt=None):
        model_calls.append(identity);(folder/'model-count.json').write_text(json.dumps(len(model_calls)))
        if '<source_material>' in prompt:
            rows=json.JSONDecoder().raw_decode(prompt.split('<source_material>',1)[1])[0]
            doc=next((r for r in rows if '请为品牌甲写一条推文' in r['body'] and not r['context']),None)
            if not doc or '本次已授权事件：仅识别business' not in prompt:return '{"clues":[]}'
            ref=doc['evidence'][0]['ref']
            return json.dumps({'clues':[{'title':'合成品牌推文邀约','summary':'品牌甲提出推文合作，待确认','relevance':'明确邀约','categories':['business'],'kind':'information','priority':'important','quotes':[{'ref':ref}], 'business_assessment':{'scope':'owner_invitation','counterparty':'品牌甲','request':'请为品牌甲写一条推文','refs':[ref]}}]})
        rows=json.JSONDecoder().raw_decode(prompt.rsplit('资料:',1)[1])[0]
        if not rows:return '{"topics":[],"knowledge":[],"profile":[]}'
        ref=rows[0]['evidence'][0]['ref']
        return json.dumps({'topics':[{'title':'引用讨论','summary':'讨论引用方法','refs':[ref]}], 'knowledge':[{'title':'引用方法','summary':'保留原文位置便于核验','tags':['引用'],'refs':[ref]}],'profile':[]})
    runtime.source_model_executor=model;radar_module.backend_slots=lambda settings:'idle'
    runtime.source=WechatSource(runtime,runtime.clue_pipeline,runtime.radar_pipeline)
    runtime.orchestration_processors=Processors(runtime);runtime.event_processors=runtime.orchestration_processors.mapping()
    config={'model':'fixture','endpoint':'http://127.0.0.1:1/v1/chat/completions','no_api_key':True,'timeout_seconds':30}
    assistant=SimpleNamespace(model_document=lambda:{'profiles':[{'id':'fixture','config':config}]},settings=lambda:{'active_profile_id':'fixture','profiles':[{'profile_id':'fixture','display_name':'合成模型','configured':True}]})
    scheduler=ScheduledTasks(root,assistant,autostart=False)
    jobs=Jobs(runtime,folder/'unused-config');jobs.model=lambda task:config
    source=ThreadingHTTPServer(('127.0.0.1',0),Handler);source.jobs=jobs
    threading.Thread(target=source.serve_forever,daemon=True).start()
    client=WechatSourceClient(store,base='http://127.0.0.1:'+str(source.server_port));store._wechat_source=client
    scheduler.source_analyzer=client.analyze_events
    scheduler.external_tasks.snapshot=lambda:{'tasks':[],'checked_at':now}
    for kind,enabled in [('wechat_clues',True),('wechat_radar',False),('script',True)]:
        data={'name':{'wechat_clues':'微信采集与重要线索','wechat_radar':'微信雷达','script':'微信工具卡'}[kind],
              'kind':kind,'body':'python /tmp/wechat_tools.py' if kind=='script' else '合成旧任务','cwd':str(folder),
              'frequency':'interval','minutes':60,'at':'08:00','weekdays':list(range(7)),'timeout':1800,'enabled':enabled,
              'allow_commands':False,'profile_id':None if kind=='script' else 'fixture','radar_since':'2026-10-01T00:00:00+08:00'}
        scheduler.save(data)
    app=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);app.store=store;app.schedules=scheduler;app.assistant=assistant;store.schedules=scheduler
    (folder/'ready.json').write_text(json.dumps({'base':'http://127.0.0.1:'+str(app.server_port),'source':'http://127.0.0.1:'+str(source.server_port)}))
    try:app.serve_forever()
    finally:
        scheduler.close();app.server_close();source.shutdown();source.server_close();jobs.close()


if __name__=='__main__':main(sys.argv[1])
