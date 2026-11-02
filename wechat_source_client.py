"""Workbench adapter. All WeChat execution is remote, via the Radar API."""
import json
import http.client
import threading
import copy
import socket
import re
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request


REQUEST_TIMEOUT_SECONDS = 6
ERROR_STAGES = {'workbench_to_radar', 'radar_to_source', 'radar_relay', 'source_read', 'source_light_status'}


class SourceUnavailable(ValueError):
    def __init__(self, stage='workbench_to_radar', kind='unavailable'):
        self.stage = stage if stage in ERROR_STAGES else 'radar_relay'
        self.kind = kind if kind in ('timeout', 'network', 'unavailable', 'invalid_response') else 'unavailable'
        super().__init__('微信来源接口暂不可用（阶段：' + self.stage + '）；旧结果保留，不能据此判断没有微信信息')


class _RequestDeadline:
    """One wall-clock budget, including headers and a slow-drip response body."""
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds
        self.expired = threading.Event()
        self.lock = threading.Lock()
        self.socket = None
        self.timer = threading.Timer(seconds, self.abort)
        self.timer.daemon = True

    def __enter__(self):
        self.timer.start()
        return self

    def check(self):
        if self.expired.is_set() or time.monotonic() >= self.end:
            self.abort()
            raise TimeoutError()

    def watch(self, sock):
        # Keep a dup after urllib hands the original socket to HTTPResponse.
        # shutdown wakes an in-flight read/readline; close alone may not do so.
        with self.lock:
            self.socket = sock.dup()
        self.check()

    def abort(self):
        self.expired.set()
        with self.lock:
            if self.socket is not None:
                try: self.socket.shutdown(socket.SHUT_RDWR)
                except OSError: pass

    def __exit__(self, *args):
        self.timer.cancel()
        with self.lock:
            if self.socket is not None:
                self.socket.close()
                self.socket = None
        self.timer.join(.1)


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def http_open(self, request):
        budget = self.deadline
        class Connection(http.client.HTTPConnection):
            def connect(self):
                budget.check()
                self.timeout = max(.001, budget.end - time.monotonic())
                super().connect()
                budget.watch(self.sock)
        return self.do_open(Connection, request)


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
        self._last_status = None
        self._last_clue_status = None
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        self.redirect_handler=NoRedirect

    def request(self,path,payload=None):
        with _RequestDeadline(REQUEST_TIMEOUT_SECONDS) as budget:
            try:
                return self._request(path,payload,budget)
            except (ValueError,OSError,http.client.HTTPException):
                if budget.expired.is_set() or time.monotonic() >= budget.end:
                    raise SourceUnavailable(kind='timeout') from None
                raise

    def _request(self,path,payload,budget):
        headers={'Accept':'application/json'}
        if payload is not None:headers.update({'Origin':self.base,'X-Article-Studio':'1','X-Wechat-Source':'1','Content-Type':'application/json'})
        request=urllib.request.Request(self.base+'/api/source/'+path,
            data=None if payload is None else json.dumps(payload).encode(),headers=headers)
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),self.redirect_handler,_DeadlineHTTPHandler(budget))
            with opener.open(request,timeout=max(.001,budget.end-time.monotonic())) as response:
                data=json.loads(response.read(8*1024*1024))
                budget.check()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 403):
                exc.close()
                raise ValueError('微信来源请求被拒绝（阶段：radar_relay）；未重新提交作业') from None
            try:
                error=json.loads(exc.read(8192))
                budget.check()
            except (OSError, ValueError,http.client.HTTPException): error={}
            finally: exc.close()
            if not isinstance(error,dict):error={}
            raise SourceUnavailable(error.get('error_stage', 'radar_relay'), error.get('error_kind', 'unavailable')) from None
        except (TimeoutError, socket.timeout):
            raise SourceUnavailable(kind='timeout') from None
        except urllib.error.URLError as exc:
            raise SourceUnavailable(kind='timeout' if isinstance(exc.reason, (TimeoutError, socket.timeout)) else 'network') from None
        except (OSError,http.client.HTTPException):
            raise SourceUnavailable(kind='network') from None
        except ValueError:
            raise SourceUnavailable(kind='invalid_response') from None
        if not isinstance(data,dict) or (not path.startswith('capture') and (data.get('schema')!='wechat-source/v1' or data.get('owner')!='wechat-assistant')):raise ValueError('微信雷达接口格式无效')
        return data

    def status(self):
        try:
            data=self.request('status')
            self._last_status=copy.deepcopy(data)
            return data
        except ValueError as exc:
            stage=getattr(exc,'stage','workbench_to_radar')
            kind=getattr(exc,'kind','invalid_response')
            data=copy.deepcopy(self._last_status) or {'schema':'wechat-source/v1','owner':'wechat-assistant','stages':{}}
            data.update(available=False,error='微信轻状态暂不可用，旧结果保留；不能据此判断没有微信信息',
                        error_stage=stage,error_kind=kind)
            data['coverage']={**data.get('coverage',{}),'stale':True,'error':data['error'],'phase':stage}
            for name in ('clues','radar'):
                saved=data['stages'].get(name) or {'processed_versions':None,'pending_versions':None,'has_pending':None}
                data['stages'][name]={**saved,'available':False,'coverage_stale':True,'error':data['error']}
            return data
    def clue_status(self):
        try:
            data=self.request('clue-status')
            self._last_clue_status=copy.deepcopy(data)
            return data
        except ValueError as exc:
            stage=getattr(exc,'stage','workbench_to_radar')
            data=copy.deepcopy(self._last_clue_status) or {
                'schema':'wechat-source/v1','owner':'wechat-assistant',
                'processed_versions':None,'pending_versions':None,'has_pending':None,'coverage_as_of':None}
            data.update(available=False,coverage_stale=True,error_stage=stage,
                        error_kind=getattr(exc,'kind','invalid_response'),
                        error='微信线索状态暂不可用，显示最近已知值；不能据此判断没有微信信息')
            data['coverage']={**data.get('coverage',{}),'stale':True,'error':data['error'],'phase':stage}
            return data
    def results(self,kind='clues',query='',category='',offset=0,limit=30):
        if kind not in ('clues','topics','knowledge','profile') or category not in ('','business','technology','topics','projects','other') or not isinstance(query,str) or len(query)>200:
            raise ValueError('微信来源查询参数无效')
        try:offset,limit=int(offset),int(limit)
        except (TypeError,ValueError):raise ValueError('微信来源分页无效') from None
        if not 0<=offset<=1000000 or not 1<=limit<=100 or (category and kind!='clues'):raise ValueError('微信来源分页或分类无效')
        return self.request('results?'+urllib.parse.urlencode({'kind':kind,'q':query,'category':category,'offset':offset,'limit':limit}))

    def analyze_clues(self,*args):return self.analyze('clues',*args)
    def analyze_radar(self,*args):return self.analyze('radar',*args)
    def analyze_events(self,*args):return self.analyze('source_events',*args)
    def event_receipt(self,rid,task_id,kind='source_events'):
        if not re.fullmatch('[a-f0-9]{32}',rid) or not re.fullmatch('[a-f0-9]{32}',task_id):raise ValueError('来源回执编号无效')
        if kind not in ('source_events','clues','radar'):raise ValueError('来源回执类型无效')
        result=self.request('jobs/'+rid)
        if result.get('run_id')!=rid or result.get('task_id')!=task_id or result.get('kind')!=kind or result.get('executor')!='wechat-source-daemon':raise ValueError('来源回执绑定不一致')
        return result
    def event_outputs(self,rid,event,offset=0):
        if not isinstance(rid,str) or not re.fullmatch('[a-f0-9]{32}',rid) or type(event) is not int or event not in range(1,11) or type(offset) is not int or not 0<=offset<=1000000:raise ValueError('事件输出参数无效')
        return self.request('jobs/'+rid+'?'+urllib.parse.urlencode({'event':event,'offset':offset}))
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
        try:
            result=self.request('run',{'kind':kind,'run_id':run_id,'task_id':task['id']})
        except SourceUnavailable as exc:
            if kind in ('clues','radar'):
                return self._pending_receipt(run_id, kind, exc=exc)
            raise
        rid=result.get('run_id')
        if kind=='source_events' and (rid!=run_id or result.get('task_id')!=task['id'] or result.get('kind')!=kind):raise ValueError('来源回执绑定不一致')
        if result.get('executor')!='wechat-source-daemon' or not isinstance(rid,str) or not re.fullmatch('[a-f0-9]{32}',rid):
            raise ValueError('微信雷达未返回独立执行回执')
        if kind in ('clues','radar') and (result.get('task_id',task['id'])!=task['id'] or result.get('kind',kind)!=kind):
            raise ValueError('来源回执绑定不一致')
        while result['status'] in ('queued','running'):

            if cancel.is_set() or time.monotonic()>=deadline:
                reason='cancelled' if cancel.is_set() else 'timeout'
                try: result=self.request('jobs/'+rid+'/cancel',{})
                except SourceUnavailable as exc:
                    if kind in ('clues','radar'):return self._pending_receipt(rid,kind,result,exc,reason)
                    raise
                # Cancellation is asynchronous. Give the source a bounded grace
                # period to persist its final receipt, without submitting again.
                until=time.monotonic()+5
                while result.get('status') in ('queued','running') and time.monotonic()<until:
                    time.sleep(.2)
                    try:result=self.request('jobs/'+rid)
                    except ValueError:break  # Preserve an honest pending receipt if the source disconnects.
                bound=(result.get('run_id')==rid and result.get('task_id',task['id'])==task['id'] and
                       result.get('kind',kind)==kind and result.get('executor','wechat-source-daemon')=='wechat-source-daemon')
                if bound and result.get('status') not in ('queued','running') and result.get('output'):
                    return reason,None,result['output']
                if kind=='source_events':
                    pending={'source':'wechat','source_run_id':rid,'receipt_pending':True,'events':[]}
                    if result.get('run_id')==rid and result.get('task_id')==task['id'] and result.get('kind')==kind:
                        try:
                            data=json.loads(result.get('output',''))
                            if isinstance(data.get('events'),list) and len(data['events'])<=13:pending.update(data)
                        except (ValueError,TypeError,AttributeError):pass
                    return reason,None,json.dumps(pending,ensure_ascii=False)
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
                if kind in ('clues','radar'):return self._pending_receipt(rid,kind,result,status=reason)
                return reason,None,json.dumps({'source_run_id':rid,'state':'来源任务已请求停止；最终回执尚未返回，已保存检查点保留'},ensure_ascii=False)
            cancel.wait(min(1,max(0,deadline-time.monotonic())))
            try: result=self.request('jobs/'+rid)
            except SourceUnavailable as exc:
                if kind in ('clues','radar'):return self._pending_receipt(rid,kind,result,exc)
                raise
            if kind in ('clues','radar') and (result.get('run_id')!=rid or result.get('task_id',task['id'])!=task['id'] or
                    result.get('kind',kind)!=kind or result.get('executor','wechat-source-daemon')!='wechat-source-daemon'):
                raise ValueError('来源回执绑定不一致')
            if kind=='source_events' and (result.get('run_id')!=rid or result.get('task_id')!=task['id'] or result.get('kind')!=kind):raise ValueError('来源回执绑定不一致')
        return result['status'],result.get('exit_code'),result.get('output','')


    @staticmethod
    def _pending_receipt(rid,kind,result=None,exc=None,status='partial'):
        data={}
        try:
            saved=json.loads((result or {}).get('output',''))
            if isinstance(saved,dict):data.update(saved)
        except (ValueError,TypeError):pass
        data.update(source='wechat',source_run_id=rid,source_kind=kind,receipt_pending=True,
                    source_receipt_final=False,state='最终回执待只读对账，来源可能仍在运行；未重新提交作业')
        if exc:data.update(error_stage=exc.stage,error_kind=exc.kind)
        return status,None,json.dumps(data,ensure_ascii=False)


class RemoteCapture:
    def __init__(self,client):self.client=client
    def status(self):
        try:return self.client.request('capture')
        except ValueError:return {'available':False,'status':'unavailable','detail':'微信雷达独立采集服务暂不可用；旧缓存保留'}
    def start(self):return self.client.request('capture',{})
    def maybe_start(self):pass  # Independent source owns background acquisition.
    def close(self):pass        # Closing the workbench must not stop the source.
