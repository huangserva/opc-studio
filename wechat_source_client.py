"""Workbench adapter. All WeChat execution is remote, via the Radar API."""
import json
import re
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
    def radar_quarantine(self,limit=50):
        # Read-only view of radar batches held out of the hourly replay.
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200:raise ValueError('隔离列表数量无效')
        return self.request('radar-quarantine?'+urllib.parse.urlencode({'limit':limit}))
    def retry_radar_quarantined(self,document_ids):
        # Re-queue only; the source keeps the original text and existing results.
        if not isinstance(document_ids,(list,tuple)) or not 1<=len(document_ids)<=200 or not all(
                isinstance(x,str) and x and len(x)<=200 for x in document_ids):raise ValueError('重试编号无效')
        return self.request('radar-quarantine/retry',{'document_ids':list(document_ids)})
    def analyze(self,kind,run_id,task,cancel,config,deadline):
        # Config (including credentials) deliberately never enters the request.
        result=self.request('run',{'kind':kind,'run_id':run_id,'task_id':task['id']})
        rid=result.get('run_id')
        if result.get('executor')!='wechat-source-daemon' or not isinstance(rid,str) or len(rid)!=32:
            raise ValueError('微信雷达未返回独立执行回执')
        while result['status'] in ('queued','running'):
            if cancel.is_set() or time.monotonic()>=deadline:
                reason='cancelled' if cancel.is_set() else 'timeout'
                result=self.request('jobs/'+rid+'/cancel',{})
                # Cancellation is asynchronous. Give the source a bounded grace
                # period to persist its final receipt, without submitting again.
                until=time.monotonic()+5
                while result.get('status') in ('queued','running') and time.monotonic()<until:
                    time.sleep(.2)
                    try:result=self.request('jobs/'+rid)
                    except ValueError:break  # Preserve an honest pending receipt if the source disconnects.
                if result.get('run_id') == rid and result.get('status') not in ('queued','running') and result.get('output'):
                    return reason,None,result['output']
                if kind=='clues' and result.get('run_id')==rid and result.get('status') in ('queued','running'):
                    # A slow model shutdown must not erase already committed
                    # batches. Only accept durable progress bound to this job.
                    try:
                        progress=self.clue_status().get('progress') or {}
                        ids=progress.get('clue_ids')
                        if (progress.get('run_id')==rid and type(progress.get('processed')) is int and progress['processed']>=0
                                and type(progress.get('skipped')) is int and progress['skipped']>=0
                                and type(progress.get('has_more')) is bool and isinstance(ids,list)
                                and all(isinstance(i,str) and re.fullmatch('[a-f0-9]{32}',i) for i in ids)):
                            saved={k:progress[k] for k in ('processed','skipped','has_more','errors','deferred','capture','reviewed','daily_review','format') if k in progress}
                            saved.update(clues=ids,receipt_pending=True,source_run_id=rid)
                            return reason,None,json.dumps(saved,ensure_ascii=False)
                    except ValueError:pass
                return reason,None,json.dumps({'source_run_id':rid,'state':'来源任务已请求停止；最终回执尚未返回，已保存检查点保留'},ensure_ascii=False)
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
