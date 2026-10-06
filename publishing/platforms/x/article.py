"""X long articles use the existing browser draft workflow, never Buffer posts."""
from pathlib import Path
from .article_workflow import account_name

def capability(kind):
    return {'adapter':'x.article','transport':'browser_article_draft',
            'stage':'browser_handoff','label':'X 长文章 · 浏览器草稿导入'}

def prepare(snapshot, options):
    account=str(options.get('account') or snapshot['target'].get('account') or '').strip()
    blocking=[]
    if account:
        account_name(account)
    else:
        blocking.append('需指定并核对 X 目标账号')
    return {**capability('article'),'status':'blocked' if blocking else 'handoff_ready',
            'blocking':blocking,'notes':['沿用已登录浏览器，导入后核验全文、图片及草稿保存状态。'],
            'handoff':{'package':str(snapshot['package']), 'account':account,
                       'workflow':'publishing/platforms/x/article_workflow.py',
                       'editor_helpers':'publishing/platforms/x/editor_helpers.mjs',
                       'skill':'article-importer','stop_at':'verified_draft'}}
