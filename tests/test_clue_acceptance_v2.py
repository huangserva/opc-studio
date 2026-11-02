"""Reproducible real-size read-only HTTP benchmark; all files are explicit /tmp paths."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import urllib.request


def benchmark(db, out):
    root=Path(__file__).resolve().parents[1]
    code='''import sys,threading\nsys.path.insert(0,sys.argv[1])\nfrom server import Handler,ThreadingHTTPServer\nfrom pathlib import Path\nfrom types import SimpleNamespace\ns=ThreadingHTTPServer(("127.0.0.1",0),Handler);s.store=SimpleNamespace(workspace=Path(sys.argv[2]));print(s.server_port,flush=True);s.serve_forever()'''
    # API expects workspace/clues/clues.db; a read-only symlink is sufficient.
    fixture=out/'http-workspace';(fixture/'clues').mkdir(parents=True,exist_ok=True)
    link=fixture/'clues/clues.db'
    if not link.exists():link.symlink_to(db.resolve())
    def start():
        p=subprocess.Popen([sys.executable,'-u','-c',code,str(root),str(fixture)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        port=p.stdout.readline().strip();assert port.isdigit();return p,'http://127.0.0.1:'+port
    def get(base,path):
        t=time.perf_counter()
        with urllib.request.urlopen(base+path,timeout=10) as r:value=json.load(r)
        return (time.perf_counter()-t)*1000,value
    cold=[]
    for _ in range(20):
        p,base=start()
        try:
            ms,v=get(base,'/api/clue-inbox');assert v['ready'] and len(v['items'])>=5;cold.append(ms)
        finally:p.terminate();p.wait();p.stdout.close()
    p,base=start();hot=[];details=[]
    try:
        get(base,'/api/clue-inbox')
        for _ in range(20):
            ms,v=get(base,'/api/clue-inbox');assert len(v['items'])>=5;hot.append(ms)
        for source in ['bookmarks','linuxdo','legacy']:
            _,v=get(base,'/api/clue-inbox?source='+source);cid=v['items'][0]['id'];ms,d=get(base,'/api/clue-objects/'+cid)
            assert d['channels'] and d['observations'] and d['evidence'];details.append(dict(source=source,clue_id=cid,canonical_key=d['canonical_key'],ms=ms,fields=sorted(d)))
    finally:p.terminate();p.wait();p.stdout.close()
    def stats(values):return dict(n=len(values),p50_ms=statistics.median(values),p95_ms=sorted(values)[18],max_ms=max(values),samples_ms=values)
    result=dict(cold=stats(cold),hot=stats(hot),details=details,cold_definition='20 fresh HTTP server processes, each first request and new SQLite connection; OS file cache not purged',hot_definition='same server after one warmup, 20 requests; new read-only SQLite connection per request')
    assert result['cold']['p95_ms']<=300 and result['hot']['p95_ms']<=300
    (out/'api-performance.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:{a:b for a,b in v.items() if a!='samples_ms'} for k,v in result.items() if k in ['cold','hot']}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--out',required=True);a=p.parse_args();out=Path(a.out);assert str(out).startswith('/tmp/k1/');out.mkdir(parents=True,exist_ok=True);benchmark(Path(a.db),out)
