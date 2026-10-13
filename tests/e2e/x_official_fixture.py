"""Isolated X integration browser fixture: never uses production credentials/network."""
import sys
import socket
# Hard-stop fixture backend egress, including DNS, independent of browser mocks.
def guarded_connect(sock, address):
    raise OSError('Fixture outbound connections disabled, including local proxies')
def guarded_connect_ex(sock, address):
    raise OSError('Fixture outbound connections disabled, including local proxies')
def guarded_getaddrinfo(host, *args, **kwargs):
    raise OSError('Fixture outbound DNS disabled')
socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
socket.getaddrinfo = guarded_getaddrinfo
import tempfile
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, parse_qs
from pathlib import Path
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server
import publishing.x_official as x

temp = tempfile.TemporaryDirectory()
store = server.Store(temp.name)
article = store.create({'title': 'X 源作品隔离测试', 'type': 'post'})
store.save(article['slug'], {'base_version': article['version']['id'], 'title': article['title'],
    'blocks': [{'kind': 'text', 'html': '<p>源作品第一条</p>'}, {'kind': 'text', 'html': '<p>源作品第二条</p>'}],
    'studio': {'type': 'post', 'platforms': {'x': {'account': '@another_account'}}}})
http = ThreadingHTTPServer(('127.0.0.1', 8789), server.Handler)
http.store = store
service = x.XOfficial(store.workspace, Path(temp.name) / 'private/config.json', store=store)
x._services[id(http)] = service
counter = 1000
def remote(method, path, token='', payload=None, **kwargs):
    global counter
    if path == '/2/oauth2/token':
        return {'access_token': 'test-placeholder-access', 'refresh_token': 'test-placeholder-refresh', 'expires_in': 7200}
    if path == '/2/users/me':
        return {'data': {'id': '111', 'username': 'fixture_user', 'name': '隔离测试账号'}}
    route = urlsplit(path).path
    query = parse_qs(urlsplit(path).query)
    now = datetime.now(timezone.utc)
    def tweet(key, text, author='111', hours=1, parent=None, root=None):
        value = {'id':str(key),'text':text,'author_id':author,'created_at':(now-timedelta(hours=hours)).isoformat(),
            'conversation_id':str(root or key),'public_metrics':{'reply_count':22,'like_count':8,'retweet_count':2}}
        if parent:value['referenced_tweets']=[{'type':'replied_to','id':str(parent)}]
        return value
    users=[{'id':'111','username':'fixture_user','name':'测试发布账号'},
           {'id':'222','username':'reader','name':'林同学'}]
    if route == '/2/users/111/tweets':
        return {'data':[tweet(40001+i,'反馈设计测试原帖 '+str(i),hours=1+i) for i in range(13)],
                'includes':{'users':users},'meta':{}}
    if route == '/2/users/111/mentions':
        return {'data':[tweet(555,'<script>评论测试</script>','222',0.5,parent=40001,root=40001)],
                'includes':{'users':users,'tweets':[tweet(40001,'原帖上下文')]},'meta':{}}
    if route.startswith('/2/tweets/search/'):
        root=query.get('query',['conversation_id:40001'])[0].split(':')[-1]
        second='next_token' in query
        return {'data':[tweet(8001+i+int(second)*20,'讨论回复 '+str(i+int(second)*20),'222',0.2+i/200,
                       parent=8000 if i==0 else root,root=root) for i in range(2 if second else 20)],
                'includes':{'users':users,'tweets':[tweet(root,'反馈设计测试原帖'),tweet(8000,'这条评论提出了关键问题','222',0.3,parent=root,root=root)]},
                'meta':{} if second else {'next_token':'feedback-page-2'}}
    if route == '/2/tweets' and method == 'GET':
        return {'data':[tweet(k,'被回复内容') for k in query['ids'][0].split(',')],'includes':{'users':users}}
    if route.startswith('/2/tweets/') and method == 'GET':
        return {'data':tweet(route.rsplit('/',1)[-1],'反馈设计测试原帖'), 'includes':{'users':users}}
    if path == '/2/tweets':
        time.sleep(0.5)
        if payload['text'] == '失败测试':
            raise x.XError('X API：账户权限或回复条件不满足（HTTP 403）')
        if payload['text'] == '中断测试':
            raise x.XError('写入结果待核实', True)
        counter += 1
        return {'data': {'id': str(counter)}}
    if path == '/2/articles/draft':
        return {'data': {'id': '777'}}
    if path == '/2/articles/777/publish':
        return {'data': {'post_id': '778'}}
    if path == '/2/media/upload/initialize':
        return {'data': {'id': '888'}}
    if '/2/media/upload/888/' in path:
        return {}
    raise AssertionError('Unexpected fixture request ' + path)
x.remote = remote
print('X fixture ready', flush=True)
http.serve_forever()
