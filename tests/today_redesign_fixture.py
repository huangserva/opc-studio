from pathlib import Path
import sys,os,json,urllib.parse,time,hashlib
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
out=Path(sys.argv[1]).resolve();root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));os.environ['GROK_BOT_REPORTS_DIR']=str(out/'reports')
import today_view as today;today.linuxdo.refresh_background=lambda root:None
(out/'runtime-code.json').write_text(json.dumps({'module':today.__file__,'sha256':hashlib.sha256(Path(today.__file__).read_bytes()).hexdigest()},indent=2))
class Handler(SimpleHTTPRequestHandler):
 protocol_version="HTTP/1.1"
 def reply_json(self,status,data):
  raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers()
  try:self.wfile.write(raw)
  except (BrokenPipeError,ConnectionResetError):pass  # 导航取消的旧请求不再尝试第二次写响应。
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(root/'public'),**kw)
 def do_GET(self):
  u=urllib.parse.urlsplit(self.path)
  if u.path=='/favicon.ico':self.send_response(204);self.end_headers();return
  if u.path.startswith('/api/'):
   try:
    q=urllib.parse.parse_qs(u.query,keep_blank_values=True)
    if u.path=='/api/today':data=today.page_view(root/'workspace',q)
    elif u.path=='/api/today/item':data=today.item_view(root/'workspace',q)
    else:data={}
    self.reply_json(200,data)
   except (BrokenPipeError,ConnectionResetError):pass
   except Exception as e:self.reply_json(400,{'error':str(e)});print(type(e).__name__,str(e),flush=True)
   return
  super().do_GET()
 def do_POST(self):self.send_response(405);self.send_header('Content-Length','0');self.end_headers()
 def log_message(self,*a):pass
class FixtureServer(ThreadingHTTPServer):
 request_queue_size=128
 daemon_threads=True
server=FixtureServer(('127.0.0.1',0),Handler);(out/'port.txt').write_text(str(server.server_port));print(server.server_port,flush=True);server.serve_forever()
