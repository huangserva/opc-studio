"""Private pseudo-production server; never starts schedulers or touches real data."""
import argparse,json,sys
from pathlib import Path
from http.server import ThreadingHTTPServer
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--port',type=int,required=True);p.add_argument('--nonce',required=True);a=p.parse_args()
root=Path(a.root).resolve();assert root.is_relative_to(Path('/tmp').resolve())
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import server,linuxdo_source
linuxdo_source.refresh_background=lambda root:None
class Handler(server.Handler):
 def do_GET(self):
  if self.path=='/__guard_fixture_identity':
   body=json.dumps(dict(nonce=a.nonce,workspace=str(root))).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
  return super().do_GET()
s=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);s.store=server.Store(root);s.serve_forever()
