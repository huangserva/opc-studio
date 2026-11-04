"""隔离浏览器夹具服务；不启动生产调度，不读取真实Grok文件。"""
import argparse,json,sys
from pathlib import Path
from types import SimpleNamespace
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
import publish_review as P
import grok_daily_review as D
from test_grok_daily_review import fixture,NOW

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--port-file',type=Path,required=True);a=p.parse_args();reports=fixture(a.root);P.GROK_REPORTS_ROOT=reports
 base=a.root/'workspace';(base/'library').mkdir()
 def scenario(name):
  folder=D.output_folder(a.root,True)
  for path in folder.glob('????-??-??.*'):path.unlink()
  if name=='empty':return
  # 正常与异常各重建同一批合成轮次，绝不写真实源目录。
  if name=='normal':
   for path in D.R.round_paths(reports):path.unlink()
   import tempfile,shutil
   with tempfile.TemporaryDirectory(dir='/tmp') as temp:
    other=fixture(Path(temp));
    for path in D.R.round_paths(other):shutil.copy2(path,reports/'radar'/path.name)
  else:
   for path in D.R.round_paths(reports):path.unlink()
  _,meta=D.generate(a.root,NOW,reports)
  if name!='normal':D.finalize(a.root,'雷达轮次缺失，请恢复规定轮次。','请恢复06–22每两小时一次的雷达轮次，并补齐名单覆盖。',meta['markdown_sha256'],NOW)
 scenario('normal')
 class Handler(SimpleHTTPRequestHandler):
  def __init__(self,*args,**kw):super().__init__(*args,directory=str(ROOT/'public'),**kw)
  def log_message(self,*args):pass
  def reply(self,status,data):
   raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def do_GET(self):
   u=urlsplit(self.path)
   if u.path=='/__test/scenario':scenario(parse_qs(u.query)['name'][0]);return self.reply(200,{'ok':True})
   if u.path=='/api/publish/review':
    try:return P.get_route(self,u,parse_qs(u.query))
    except ValueError:return self.reply(400,{'error':'无效参数'})
   return super().do_GET()
  def do_POST(self):return self.reply(405,{'error':'只读验收服务'})
  do_PUT=do_POST;do_DELETE=do_POST
 http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.store=SimpleNamespace(workspace=base,library=base/'library');a.port_file.write_text(json.dumps({'port':http.server_port}));http.serve_forever()
if __name__=='__main__':main()
