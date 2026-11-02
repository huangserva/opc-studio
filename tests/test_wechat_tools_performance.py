"""Serial cold/hot HTTP benchmark. Child servers never initialise a workspace."""
import argparse,json,pathlib,subprocess,sys,time,urllib.request,statistics

def measure(base,route):
 start=time.perf_counter()
 with urllib.request.urlopen(base+route,timeout=10) as response:value=json.load(response)
 assert value['ready'] and len(value['items'])>=6
 return (time.perf_counter()-start)*1000

def benchmark(workspace,out):
 root=pathlib.Path(__file__).resolve().parents[1]
 code='''import sys\nfrom pathlib import Path\nfrom types import SimpleNamespace\nsys.path.insert(0,sys.argv[1])\nfrom server import Handler,ThreadingHTTPServer\ns=ThreadingHTTPServer(("127.0.0.1",0),Handler);s.store=SimpleNamespace(workspace=Path(sys.argv[2]));print(s.server_port,flush=True);s.serve_forever()'''
 def start():
  process=subprocess.Popen([sys.executable,'-u','-c',code,str(root),str(workspace)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
  port=process.stdout.readline().strip();assert port.isdigit();return process,'http://127.0.0.1:'+port
 def close(p):p.terminate();p.wait();p.stdout.close()
 result={}
 for route in ('/api/clue-inbox','/api/clue-inbox?collection=tools'):
  cold=[];hot=[]
  for _ in range(20):
   p,base=start()
   try:cold.append(measure(base,route))
   finally:close(p)
  p,base=start()
  try:
   measure(base,route)
   for _ in range(20):hot.append(measure(base,route))
  finally:close(p)
  def stats(a):return dict(n=20,p50_ms=statistics.median(a),p95_ms=sorted(a)[18],samples_ms=a)
  result[route]=dict(cold=stats(cold),hot=stats(hot));assert max(result[route][k]['p95_ms'] for k in ('cold','hot'))<=300
 result['definition']='cold:20 fresh isolated HTTP server processes first request/new SQLite connection; hot:20 requests after warmup; OS page cache not purged'
 out.mkdir(parents=True,exist_ok=False);(out/'performance.json').write_text(json.dumps(result,indent=2));print(json.dumps({route:{k:v['p95_ms'] for k,v in value.items()} for route,value in result.items() if route!='definition'}))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--out',required=True);a=p.parse_args();out=pathlib.Path(a.out);assert str(out).startswith('/tmp/r3-1/');benchmark(pathlib.Path(a.workspace),out)
