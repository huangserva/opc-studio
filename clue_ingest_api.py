"""K1 read-only routes. Reads never initialise schema, import files or schedule work."""
from pathlib import Path
from urllib.parse import unquote, parse_qs
from clue_store_v2 import inbox, detail


def get_route(handler, parts, query):
    path = Path(handler.server.store.workspace)/'clues/clues.db'
    if parts.path == '/api/clue-inbox':
        collection_query = parse_qs(parts.query, keep_blank_values=True)
        if 'collection' in collection_query:
            query = collection_query
        if set(query)-{'limit','cursor','source','channel','status','collection'} or any(len(v)!=1 for v in query.values()):
            raise ValueError('线索收件箱参数无效')
        value = lambda k: query.get(k,[None])[0]
        if value('collection') is not None:
            from wechat_tools_view import collection_inbox
            return handler.reply(200,collection_inbox(path,collection=value('collection'),limit=int(value('limit') or 30),cursor=value('cursor'),source=value('source'),channel=value('channel'),status=value('status')))
        return handler.reply(200,inbox(path,limit=int(value('limit') or 30),cursor=value('cursor'),source=value('source'),channel=value('channel'),status=value('status')))
    segments = parts.path.split('/')
    if len(segments)!=4 or not segments[3] or query:
        raise ValueError('线索详情路径或参数无效')
    result = detail(path,unquote(segments[3]))
    return handler.reply(200,result) if result else handler.reply(404,{'error':'线索对象不存在'})
