"""Workbench adapter. All WeChat execution is remote, via the Radar API."""
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request


class WechatSourceClient:
    def __init__(self, store, base=None):
        if base is None:
            path=Path(store.workspace)/'sources/wechat.json'
            config=json.loads(path.read_text()) if path.is_file() else {}
            base=config.get('service_url','http://127.0.0.1:3000')
        url=urllib.parse.urlsplit(base)
        if url.scheme!='http' or url.hostname!='127.0.0.1' or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
            raise ValueError('微信雷达接口必须是本机 HTTP 地址')
        self.base=base.rstrip('/')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect)

    def request(self,path,payload=None):
        headers={'Accept':'application/json'}
        if payload is not None:headers.update({'Origin':self.base,'X-Article-Studio':'1','Content-Type':'application/json'})
        request=urllib.request.Request(self.base+'/api/source/'+path,
            data=None if payload is None else json.dumps(payload).encode(),headers=headers)
        try:
            with self.opener.open(request,timeout=8) as response:
                data=json.loads(response.read(8*1024*1024))
        except (OSError,ValueError):raise ValueError('微信雷达接口不可用；未运行工作台内的备用微信分析，已保存结果与检查点保留') from None
        if not isinstance(data,dict) or (not path.startswith('capture') and (data.get('schema')!='wechat-source/v1' or data.get('owner')!='wechat-assistant')):raise ValueError('微信雷达接口格式无效')
        return data

    def status(self):return self.request('status')
    def clue_status(self):return self.request('clue-status')
    def results(self,kind='clues',query='',category='',offset=0,limit=30):
        if kind not in ('clues','topics','knowledge','profile') or category not in ('','business','technology','topics','projects','other') or not isinstance(query,str) or len(query)>200:
            raise ValueError('微信来源查询参数无效')
        try:offset,limit=int(offset),int(limit)
        except (TypeError,ValueError):raise ValueError('微信来源分页无效') from None
        if not 0<=offset<=1000000 or not 1<=limit<=100 or (category and kind!='clues'):raise ValueError('微信来源分页或分类无效')
        return self.request('results?'+urllib.parse.urlencode({'kind':kind,'q':query,'category':category,'offset':offset,'limit':limit}))

    def analyze_clues(self,*args):return self.analyze('clues',*args)
    def analyze_radar(self,*args):return self.analyze('radar',*args)
    def analyze(self,kind,run_id,task,cancel,config,deadline):
        # Config (including credentials) deliberately never enters the request.
        result=self.request('run',{'kind':kind,'run_id':run_id,'task_id':task['id']})
        rid=result.get('run_id')
        if result.get('executor')!='wechat-source-daemon' or not isinstance(rid,str) or len(rid)!=32:
            raise ValueError('微信雷达未返回独立执行回执')
        while result['status'] in ('queued','running'):
            if cancel.is_set() or time.monotonic()>=deadline:
                self.request('jobs/'+rid+'/cancel',{})
                return ('cancelled' if cancel.is_set() else 'timeout'),None,json.dumps({'source_run_id':rid,'state':'来源任务停止；已完成结果与检查点保留'},ensure_ascii=False)
            cancel.wait(min(1,max(0,deadline-time.monotonic())))
            result=self.request('jobs/'+rid)
        return result['status'],result.get('exit_code'),result.get('output','')


class RemoteCapture:
    def __init__(self,client):self.client=client
    def status(self):
        try:return self.client.request('capture')
        except ValueError:return {'available':False,'status':'unavailable','detail':'微信雷达独立采集服务暂不可用；旧缓存保留'}
    def start(self):return self.client.request('capture',{})
    def maybe_start(self):pass  # Independent source owns background acquisition.
    def close(self):pass        # Closing the workbench must not stop the source.
