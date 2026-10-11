"""仅供 E2E 的进程入口：生产 server.main，外部边界替换为脱敏夹具。"""
import json, os, socket, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
ALLOWED={int(os.environ['E2E_APP_PORT']),int(os.environ['E2E_MODEL_PORT'])}
LOG=Path(os.environ['E2E_NETWORK_LOG'])
original=socket.socket.connect
original_ex=socket.socket.connect_ex
original_dns=socket.getaddrinfo
def dns(host,*args,**kwargs):
    if host not in ('localhost','127.0.0.1','::1',None):
        with LOG.open('a') as f: f.write(json.dumps({'blocked_dns':str(host)})+'\n')
        raise OSError('E2E 禁止解析非夹具域名')
    return original_dns(host,*args,**kwargs)
socket.getaddrinfo=dns
def checked(method):
    def connect(self,address):
        if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1','localhost') or address[1] not in ALLOWED:
            with LOG.open('a') as f: f.write(json.dumps({'blocked':str(address)})+'\n')
            raise OSError('E2E 禁止连接非夹具地址')
        return method(self,address)
    return connect
socket.socket.connect=checked(original)
socket.socket.connect_ex=checked(original_ex)
import knowledge_store, intake_store, intel_store, intel_wechat_mp
init=knowledge_store.Knowledge.__init__
def knowledge_init(self,store,sources=None):
    init(self,store,sources=[])
knowledge_store.Knowledge.__init__=knowledge_init
SOURCE=('E2E灯塔取景：黄昏用水面倒影复制天空，人物只留背影，渡船只留灯线。'
        '这是一篇只用于本机自动回归的公开网页夹具，不含私人资料。')
class FixtureWeb:
    def fetch(self,args,job):
        if not args['url'].startswith('https://example.com/e2e-'):
            raise ValueError('E2E 夹具未登记此网页')
        start=args.get('start',0); text=SOURCE
        return {'url':args['url'],'title':'E2E灯塔取景','markdown':text[start:],
                'start':start,'total_chars':len(text),'truncated':False,'next_start':None}
intake_store.WebTools=FixtureWeb
BIZ='MzB5NTkwMDAwMDE=='
LINK='https://mp.weixin.qq.com/s?__biz=MzB5NTkwMDAwMDE%3D%3D&mid=123&idx=1&sn=fixture&chksm=fixture'
class Article:
    quota=type('Quota',(),{'cooldown_until':lambda s:0,'strike_count':lambda s:0,'state':lambda s:{'strikes':0,'cooldown_until':0,'cooling':False}})()
    def credentials_ready(self): raise intel_wechat_mp.CredentialsMissing('E2E 不使用后台凭证')
    def fetch_article(self,url):
        if intel_store.Intel.complete_article_link(url,BIZ)!=url:
            raise ValueError('E2E 未登记此文章')
        raw=(ROOT/'tests/fixtures/mp_article_with_album.html').read_text()
        return {**intel_wechat_mp.parse_article(raw,url),'raw':raw}
    def appmsg_list(self,*a,**k): raise AssertionError('不允许后台列表请求')
class Public:
    def album_page(self,*a,**k):
        time.sleep(4)
        return {'title':'E2E合集','article_count':1,'nickname':'占位公众号','continue_flag':False,
                'items':[{'title':'E2E合集文章','link':LINK,'published_at':1700000000,'msgid':'123','itemidx':'1'}]}
    def sogou_search(self,name):
        return [{'account':name,'title':'E2E搜狗仅标题','sogou_link':'fixture','published_at':1700000001}]
    def sogou_resolve(self,url):
        return ''  # 搜狗返回标题，但没有拿到真实文章链接
intel_init=intel_store.Intel.__init__
def intel_fixture(self,store,client_factory=None,public_factory=None):
    intel_init(self,store,client_factory=lambda _:Article(),public_factory=lambda _:Public())
intel_store.Intel.__init__=intel_fixture
# 冷却/状态等路径也不能读取系统钥匙串。
def no_key(*a,**k): raise intel_wechat_mp.CredentialsMissing('E2E 禁止读取凭证')
intel_wechat_mp.KeychainCredentials.load=no_key
# 微信状态是另一项外部服务边界；不让默认状态探测尝试生产 3000。
from wechat_source_client import WechatSourceClient
def source_fixture(self,path,payload=None):
    if payload is not None or path not in ('status','clue-status'):
        raise ValueError('E2E 不允许微信执行请求')
    return {'schema':'wechat-source/v1','owner':'wechat-assistant','available':False,
            'busy':False,'running':False,'processed_versions':0,'pending_versions':0,
            'has_pending':False,'detail':'E2E 未配置真实微信来源'}
WechatSourceClient.request=source_fixture
# article_importer 会把正式工作台目录插到 sys.path 最前。助手是按文件路径加载的，
# 但它 import 的 assistant_policy 会因此落到正式目录。先把副本模块放进 sys.modules。
import assistant_policy
if Path(assistant_policy.__file__).resolve().parent!=ROOT:
    raise RuntimeError('E2E 助手策略没有来自副本：'+assistant_policy.__file__)
import server
if os.environ.get('E2E_STATUS_SUMMARY') == '1':
    from status_summary_fixture import install
    install(sys.argv[sys.argv.index('--workspace') + 1])
from cards_fixture import seed
seed(server.Store(sys.argv[sys.argv.index('--workspace') + 1]))
server.main()
