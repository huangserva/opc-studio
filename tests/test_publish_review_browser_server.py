"""隔离浏览器/API验收服务：仅绑定随机loopback端口，复用正式get_route，不启动业务后台。"""
import argparse,json,sys,threading
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import publish_review as P

def fixture(root, daily):
    reports=root/'reports';workspace=root/'workspace';library=workspace/'library';posts=reports/'posts';posts.mkdir(parents=True,exist_ok=True);library.mkdir(parents=True,exist_ok=True)
    today=datetime.now(P.BEIJING).date();day=today.isoformat()
    def post(id,kind='主帖',**more):return {'id':id,'url':'https://x.com/servasyy_ai/status/'+id,'type':kind,'created_at':day+'T07:00:00+08:00','text':'亲手测试的同一段文字\n真实数据第二行\n第三行在列表折叠','metrics':{'views':100,'likes':10,'bookmarks':5,'retweets':1,'replies':2,'quotes':3},**more}
    a=post('9001',from_radar={'report':'daily/'+day+'.json','url':'https://x.com/origin/status/88','title':'明确的雷达原帖'});b=post('9002','长文');c=post('9003','引用');d=post('9004','回复');e=post('9005','回复');f=post('9006')
    second=[{**p,'metrics':{**p['metrics'],'views':250,'likes':20,'bookmarks':15}} for p in (a,b,d,e,f)]
    (posts/(day+'.json')).write_text(json.dumps({'date':day,'snapshots':[{'captured_at':day+'T08:00:00+08:00','type_counts':{'主帖':3,'长文':1,'引用':1,'回复':2},'posts':[a,b,c,d,e,f]},{'captured_at':day+'T10:00:00+08:00','posts':second}]},ensure_ascii=False))
    folder=library/'fixture-work';folder.mkdir(exist_ok=True);(folder/'article.json').write_text('{"title":"工作台登记作品"}');(folder/'source-item.json').write_text('{"item_id":"x:77","sources":["G"]}');(folder/'deliveries.json').write_text(json.dumps([{'id':'published-fixture','kind':'published_manual','platform':'x','url':b['url'],'created_at':day+'T07:00:00+08:00'}]))
    (workspace/'today').mkdir(exist_ok=True);(workspace/'today/feedback.json').write_text('{"x:77":{"status":"used"}}')
    weekly=reports/'weekly';weekly.mkdir(exist_ok=True);week=today.strftime('%G-W%V');(weekly/(week+'.json')).write_text(json.dumps({'week':week,'generated_at':day+'T11:00:00+08:00','output_review':{'结论':['Grok 原样结论第一段。','Grok 第二段：保留 <img src=x onerror=alert(1)> 字面量。'],'下周建议':'只说原话，不由工作台改写。'}},ensure_ascii=False))
    (weekly/(week+'.md')).write_text('## 本周产出复盘\n\nGrok 原样结论第一段。\n\nGrok 第二段：保留 <img src=x onerror=alert(1)> 字面量。\n\n只说原话，不由工作台改写。\n')
    if daily:
        (posts/('review-'+day+'.json')).write_text(json.dumps({'date':day,'generated_at':day+'T11:00:00+08:00','summary':['今天把实测参数写清楚的帖子更好。','这句由 Grok 给出，工作台不改写。'],'best':[{'id':'9001','why':'读者能照着参数做。'}],'worst':[{'id':'9003','why':'只有一次采样，暂不猜增长。'}],'notes':['<script>window.__injected=1</script>']},ensure_ascii=False))
    return reports,workspace

def main():
    a=argparse.ArgumentParser();a.add_argument('--fixture-root',type=Path);a.add_argument('--daily',action='store_true');a.add_argument('--port-file',type=Path,required=True);a.add_argument('--workspace',type=Path);a.add_argument('--reports-root',type=Path);args=a.parse_args()
    if args.fixture_root:reports,workspace=fixture(args.fixture_root,args.daily);P.GROK_REPORTS_ROOT=reports
    else:
        workspace=args.workspace
        if args.reports_root:P.GROK_REPORTS_ROOT=args.reports_root
    store=SimpleNamespace(workspace=workspace,library=workspace/'library')
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'public'),**kw)
        def log_message(self,*args):pass
        def reply(self,status,data):
            raw=json.dumps(data,ensure_ascii=False,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            parts=urlsplit(self.path)
            if parts.path=='/__test/clear-cache':P._REPORT_CACHE.clear();return self.reply(200,{'cleared':True})
            if parts.path=='/api/publish/review' or parts.path.startswith('/api/publish/review/'):
                try:return P.get_route(self,parts,parse_qs(parts.query))
                except ValueError as e:return self.reply(400,{'error':str(e)})
                except Exception:return self.reply(500,{'error':'隔离服务异常'})
            if parts.path.startswith('/api/'):return self.reply(404,{'error':'验收服务未提供该接口'})
            return super().do_GET()
        def do_POST(self):return self.reply(405,{'error':'只读验收服务'})
        do_PUT=do_POST
        do_DELETE=do_POST
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.store=store;http.port_file=args.port_file
    args.port_file.write_text(json.dumps({'port':http.server_port,'root':str(ROOT)}));http.serve_forever()
if __name__=='__main__':main()
