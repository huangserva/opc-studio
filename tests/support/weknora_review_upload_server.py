"""Local-only M2 acceptance fixture. No scheduler/watchers or real API client."""
import sys,json,sqlite3,argparse
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from server import Store,Handler
from knowledge_store import Knowledge
from weknora_upload import UploadService,KB
from tests.test_weknora_upload import DDL
from tests.test_weknora_review_upload import ReviewFake


def setup(root):
    root=Path(root);assert str(root.resolve()).startswith(('/tmp/','/private/tmp/'));root.mkdir(parents=True,exist_ok=True)
    store=Store(root/'workspace');store._knowledge=Knowledge(store,sources=[])
    tags={'knowledge_base_id':KB,'tags':{'隐私-公开':'public-tag','类型-长文·作品':'work-tag','类型-长文·外部参考':'ref-tag','隐私-内部':'internal-tag','类型-知识':'knowledge-tag'}};tags['knowledge_bases']={'public':{'knowledge_base_id':KB,'tags':tags['tags']}}
    (root/'coordination').mkdir();(root/'coordination/weknora-tags.json').write_text(json.dumps(tags,ensure_ascii=False))
    (root/'workspace/weknora').mkdir()
    with sqlite3.connect(root/'workspace/weknora/ledger.db') as db:db.execute(DDL)
    works=[]
    for i in (1,2):
        work=store.create({'title':f'【验收合成】蓝杉复盘回流记录{i}','type':'post'});store.record_delivery(work['slug'],{'platform':'x','version':work['head'],'url':f'https://x.com/synthetic/status/{10000+i}'})
        works.append(work['slug'])
    return store,works


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();store,works=setup(a.root);c=ReviewFake()
    s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.store=store;s.weknora_upload_service=UploadService(a.root,client_factory=lambda:c)
    print(json.dumps({'port':s.server_port,'works':works}),flush=True);s.serve_forever()
