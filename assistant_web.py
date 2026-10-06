"""Bounded, cancellable public-web tools backed by the user's remio service."""
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


def public_url(value, resolve=False):
    if not isinstance(value,str) or len(value)>2048 or re.search(r'[\s\x00-\x1f\\]',value):
        raise ValueError('网页地址格式错误')
    try:
        url=urlsplit(value)
        host=(url.hostname or '').rstrip('.').lower()
        port=url.port
    except ValueError:
        raise ValueError('网页地址格式错误') from None
    if url.scheme not in ('http','https') or not host or url.username is not None or url.password is not None:
        raise ValueError('只能读取不含账号凭据的 HTTP/HTTPS 公开网页')
    if port not in (None,80,443):raise ValueError('网页读取仅支持标准 HTTP/HTTPS 端口')
    if host=='localhost' or host.endswith(('.localhost','.local','.internal','.lan','.home','.test','.invalid')):
        raise ValueError('网页工具不读取本机或局域网地址')
    try:
        address=ipaddress.ip_address(host)
    except ValueError:
        if '.' not in host:raise ValueError('网页工具不读取本机或局域网地址')
    else:
        if not address.is_global:raise ValueError('网页工具不读取本机或局域网地址')
    if resolve:
        try:addresses=socket.getaddrinfo(host,port or (443 if url.scheme=='https' else 80),type=socket.SOCK_STREAM)
        except OSError:raise ValueError('网页域名暂时无法解析，请核对地址或稍后重试') from None
        if not addresses or any(not ipaddress.ip_address(item[4][0].split('%')[0]).is_global for item in addresses):
            raise ValueError('网页工具不读取本机或局域网地址')
    return value


class WebTools:
    def __init__(self):
        self.command=os.environ.get('ARTICLE_STUDIO_REMIO') or shutil.which('remio')
        self.timeout=45

    def available(self):
        return bool(self.command and os.path.isfile(self.command) and os.access(self.command,os.X_OK))

    def capabilities(self):
        return {'provider':'remio','installed':self.available(),'tools':['web_search','web_fetch'],
                'scope':'公开网页搜索与正文提取；不操作登录浏览器，不读取任意本机文件',
                'requires':'remio 桌面应用运行，且已配置可用搜索源；连接以工具执行结果为准'}

    @staticmethod
    def protect_input(value,job):
        key=(job.get('model_config') or {}).get('api_key')
        if key and key in value:raise ValueError('联网请求不能包含模型密钥')

    def call(self,args,job):
        if not self.available():raise ValueError('联网服务未就绪，请启动 remio 并检查 CLI 配置')
        if job.get('cancel'):raise ValueError('任务已停止')
        count=job.get('web_calls',0)+1
        if count>8:raise ValueError('本轮联网查询已到上限，请下一条消息继续')
        job['web_calls']=count
        process=None
        with tempfile.TemporaryFile() as output:
            try:
                process=subprocess.Popen([self.command,*args],stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.DEVNULL)
                deadline=time.monotonic()+self.timeout
                while process.poll() is None:
                    if job.get('cancel'):raise ValueError('任务已停止')
                    if time.monotonic()>deadline:raise ValueError('联网服务响应超时，请稍后重试')
                    if os.fstat(output.fileno()).st_size>4*1024*1024:raise ValueError('网页返回内容过大，请换更具体的文档页面')
                    time.sleep(.05)
                output.seek(0);raw=output.read(4*1024*1024+1)
                if len(raw)>4*1024*1024:raise ValueError('网页返回内容过大，请换更具体的文档页面')
                try:result=json.loads(raw)
                except (ValueError,UnicodeError):raise ValueError('联网服务未返回有效结果，请确认 remio 正在运行') from None
                if not isinstance(result,dict) or result.get('ok') is not True:
                    detail=str(result.get('error','')) if isinstance(result,dict) else ''
                    if 'No commercial search source configured' in detail:
                        raise ValueError('remio 尚未配置搜索源，请在 remio 搜索设置中启用搜索服务')
                    if 'credits exhausted' in detail:raise ValueError('remio 搜索额度已用完，请检查搜索服务配置')
                    raise ValueError('联网服务未完成查询，请核对网页地址及 remio 连接后重试')
                if not isinstance(result.get('data'),dict):raise ValueError('联网服务返回格式异常，请稍后重试')
                return result['data']
            except OSError:
                raise ValueError('无法启动联网服务，请检查 remio CLI') from None
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=1)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=1)

    def search(self,args,job):
        query=args.get('query','')
        if not isinstance(query,str) or not query.strip() or len(query)>1200 or any(ord(c)<32 for c in query):
            raise ValueError('搜索词需要 1–1200 个字符，不包含控制字符')
        self.protect_input(query,job)
        count=args.get('count',5)
        if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=8:raise ValueError('搜索结果数为 1–8')
        domain=args.get('domain')
        if domain:
            if not isinstance(domain,str) or not re.fullmatch(r'[A-Za-z0-9.-]+',domain):raise ValueError('限定域名格式错误')
            public_url('https://'+domain)
            query+=' site:'+domain
        command=['web_search','--query',query,'--count',str(count)]
        region=args.get('region') or ('us' if query.isascii() else None)
        if region:
            if not isinstance(region,str) or not re.fullmatch('[a-z]{2}',region):raise ValueError('搜索地区使用两位小写代码，如 us、cn')
            command+=['--region',region]
        freshness=args.get('freshness')
        if freshness:
            if freshness not in ('pd','pw','pm','py'):raise ValueError('搜索时间范围格式错误')
            command+=['--freshness',freshness]
        data=self.call(command,job)
        entries=data.get('results')
        if not isinstance(entries,list):raise ValueError('联网搜索返回格式异常，请稍后重试')
        results=[]
        for entry in entries[:count]:
            if not isinstance(entry,dict):continue
            try:url=public_url(entry.get('url'))
            except ValueError:continue
            if domain:
                host=(urlsplit(url).hostname or '').lower().rstrip('.')
                requested=domain.lower().rstrip('.')
                if host!=requested and not host.endswith('.'+requested):continue
            results.append({'title':str(entry.get('title') or url)[:240],'url':url,
                            'description':str(entry.get('description') or '')[:1000]})
        return {'query':query,'provider':'remio','domain':domain,'region':region,'results':results,
                'sources':[{'title':r['title'],'url':r['url']} for r in results],
                'retrieved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                'summary':f'找到 {len(results)} 条公开网页结果；摘要需打开原文核实'+('。限定域名无结果时可直接读取已知官网网址，不能据此断言资料不存在' if domain and not results else '')}

    def fetch(self,args,job):
        url=public_url(args.get('url'),resolve=True);self.protect_input(url,job)
        start=args.get('start',0);limit=args.get('max_chars',16000)
        if isinstance(start,bool) or not isinstance(start,int) or not 0<=start<=200000:raise ValueError('网页起始位置为 0–200000')
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1000<=limit<=32000:raise ValueError('每次读取长度为 1000–32000 字符')
        data=self.call(['web_get',url],job)
        final=public_url(data.get('url') or url)
        markdown=data.get('markdown')
        if not isinstance(markdown,str) or not markdown.strip():raise ValueError('网页没有返回可读正文，请换文档页或提供文字')
        self.protect_input(markdown,job)
        title=str(data.get('title') or final)[:240]
        end=min(start+limit,len(markdown))
        return {'url':final,'title':title,'markdown':markdown[start:end],
                'start':start,'total_chars':len(markdown),'truncated':end<len(markdown),
                'next_start':end if end<len(markdown) else None,
                'sources':[{'title':title,'url':final}],
                'retrieved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                'summary':f'已读取网页「{title}」'+('（正文较长，可继续分页）' if end<len(markdown) else '')}
