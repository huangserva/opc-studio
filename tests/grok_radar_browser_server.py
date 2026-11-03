"""隔离浏览器专用：实际 server.Handler + Store，随机端口，不启动生产调度。"""
import hashlib,json,os,sys
from pathlib import Path
from http.server import ThreadingHTTPServer
out=Path(sys.argv[1]).resolve();root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));os.environ['GROK_BOT_REPORTS_DIR']=str(out/'reports')
from tests._network_guard import NetworkGuard
network_guard=NetworkGuard(blocked_ports={8766,3000,18580});network_guard.__enter__()
# /tmp 副本没有相邻 article-importer；仅替换等价兼容入口的装载位置。
import importlib.util
shim=out/'article_importer.py';shim.write_text('from publishing.core.article_package import *\n')
original_spec=importlib.util.spec_from_file_location
def local_spec(name, location, *args, **kwargs):
    if name=='importer' and str(location).endswith('article-importer/scripts/article_importer.py'):
        location=shim
    return original_spec(name, location, *args, **kwargs)
importlib.util.spec_from_file_location=local_spec
import server
importlib.util.spec_from_file_location=original_spec
import today_view
import grok_bot_radar
import interest_channel
import interest_projection
server.ROOT=root
grok_bot_radar.BUNDLE_PATH=out/'bundle.json'
from datetime import datetime, timezone
class FixtureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value=cls.fromisoformat(os.environ.get('GROK_RADAR_TEST_NOW','2026-11-03T12:00:00+00:00'))
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)
today_view.datetime=FixtureClock
interest_channel.datetime=interest_projection.datetime=FixtureClock
today_view.linuxdo.refresh_background=lambda _:None
http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
http.store=server.Store(out/'workspace')
(out/'runtime-code.json').write_text(json.dumps(dict(module=today_view.__file__,sha256=hashlib.sha256(Path(today_view.__file__).read_bytes()).hexdigest(),
    modules={name:dict(path=str(root/(name+'.py')),sha256=hashlib.sha256((root/(name+'.py')).read_bytes()).hexdigest())
             for name in ('grok_radar_ingest','interest_sources','interest_channel')}),indent=2))
(out/'port.txt').write_text(str(http.server_port));print(http.server_port,flush=True)
try:http.serve_forever()
finally:
    http.server_close()
    (out/'network-blocked.json').write_text(json.dumps(network_guard.blocked))
    network_guard.__exit__(None,None,None)
