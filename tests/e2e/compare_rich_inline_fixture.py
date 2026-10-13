"""只启动隔离作品服务，不加载模型、凭据、调度或后台采集。"""
import json
import socket
import sys
import tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer

def no_egress(*args, **kwargs):
    raise OSError('对比夹具禁止外连')
socket.socket.connect = no_egress
socket.socket.connect_ex = no_egress
socket.getaddrinfo = no_egress
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server

with tempfile.TemporaryDirectory(prefix='compare-workspace-', dir='/tmp') as workspace:
    store = server.Store(workspace)
    article = store.create({'title': '版本格式隔离验收', 'type': 'article'})
    saved = store.save(article['slug'], {'base_version': article['version']['id'],
        'title': article['title'], 'blocks': [{'kind': 'text', 'html': '<p>甲旧乙</p>'}], 'summary': '改前正文'})
    http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    http.store = store
    print(json.dumps({'base': 'http://127.0.0.1:' + str(http.server_port), 'slug': article['slug'],
                      'before': saved['version'], 'workspace': workspace}), flush=True)
    try:
        http.serve_forever()
    finally:
        http.server_close()
