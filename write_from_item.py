"""素材到作品：同步底稿、独立单次起草、版本冲突保护。"""
from __future__ import annotations

import json
import re
import threading
import time
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from items_store import ItemsStore, now

TEMPLATE = Path(__file__).resolve().parent / 'prompts/draft-template.md'
TIMEOUT = 180
BANNED = ('🔥', '🚀', '炸裂', '最狠', '说句大实话', '兄弟们直接冲')
PLACEHOLDER = '【待补：我的实测数据】'
_SERVICE_LOCK = threading.Lock()


def text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) if value is not None else ''


def blocks_for(item):
    """全文保持字节对应的字符，不经过 Markdown 解释器。"""
    def block(title, body):
        return {'kind': 'text', 'html': '<h2>' + escape(title) + '</h2><p>' + escape(text(body)).replace('\n', '<br>') + '</p>'}
    url = item.get('url') or ''
    try:
        parts = urlsplit(url)
        safe = parts.scheme in ('http', 'https') and parts.hostname and not parts.username and not parts.password
    except ValueError:
        safe = False
    origin = '<a href="' + escape(url, quote=True) + '">' + escape(url) + '</a>' if safe else escape(url)
    remote = any(r.get('store')=='weknora' for r in item.get('legacy_refs',[]) if isinstance(r,dict))
    blocks = [{'kind': 'text', 'html': '<h2>来源链接</h2><p>' + origin + '</p>'}, block('检索片段（非全文）' if remote else '原文全文', item.get('full_text') or item.get('article_text') or '')]
    if item.get('article_text') and item.get('article_text') != item.get('full_text'):
        blocks.append(block('长文全文', item['article_text']))
    # 保留双来源的长文、串推和引用，避免只把摘要送进底稿。
    for key in ('rich_sources', 'thread', 'quoted'):
        if item.get(key):
            blocks.append(block({'rich_sources': '来源补充全文', 'thread': '原串推', 'quoted': '原文引用'}[key], item[key]))
    if remote:
        blocks.append(block('资料出处与隐私', {'notice':item.get('incomplete_reason'), 'references':item.get('legacy_refs',[])}))
    metrics = item.get('metrics') or {}
    labels = {'views':'浏览', 'likes':'赞', 'bookmarks':'收藏', 'retweets':'转发', 'replies':'回复', 'views_per_hour':'每小时浏览', 'likes_per_hour':'每小时赞'}
    data = '\n'.join(labels.get(k,k) + '：' + (text(v) if v is not None else '未知') for k,v in metrics.items())
    return blocks + [block('数据', data or '浏览、赞、收藏、每小时：未知'),
                     block('为什么爆', item.get('why_viral') or item.get('why')),
                     block('可复制写法', item.get('pattern') or (item.get('analysis') or {}).get('pattern') or '【待补：可复制写法】'),
                     block('我的切入角度', item.get('my_angle')),
                     block('提纲', item.get('outline') or '真实问题\n\n我亲手跑流程\n\n工具、参数、链接')]


def draft_kind(item):
    return 'thread' if item.get('kind') == 'post' or item.get('thread') else 'article'


def parse_answer(answer, item):
    raw = answer.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
    try:
        result = json.loads(raw)
        parts = result['parts']
        title = result['title']
    except (ValueError, TypeError, KeyError):
        raise ValueError('模型未返回有效的初稿格式，底稿保留') from None
    if not isinstance(title,str) or not title.strip() or len(title)>500 or not isinstance(parts,list) or any(not isinstance(p,str) or not p.strip() for p in parts):
        raise ValueError('AI 初稿标题或正文为空，底稿保留')
    handle = str((item.get('author') or {}).get('handle') or item.get('author_handle') or '').lstrip('@')[:80]
    citation = '@' + handle if handle else '【待补：原作者 @handle】'
    if citation not in '\n'.join(parts):
        parts[-1] += '\n\n参考素材：' + citation + (' · ' + item['url'] if item.get('url') else '')
    if draft_kind(item) == 'thread':
        if not 3 <= len(parts) <= 8:
            raise ValueError('AI 串文须为 3–8 条，底稿保留')
    elif len(parts) != 1 or not 300 <= len(parts[0]) <= 800:
        raise ValueError('AI 单篇须为 300–800 字，底稿保留')
    body = '\n\n'.join(parts)
    combined = title + '\n' + body
    if any(t in combined for t in BANNED) or re.search(r'[0-9#*]\ufe0f?\u20e3', combined):
        raise ValueError('AI 初稿含禁用文案，底稿保留')
    if PLACEHOLDER not in body:
        raise ValueError('AI 初稿缺少亲测数据占位，底稿保留')
    # 输入没有黄总的实测证据，不能把他人经验改成第一人称已完成经历。
    for sentence in re.split(r'[。！？\n]', combined):
        if re.search(r'(?:我(?:们)?|本人|笔者)[^，：、。！？\n]{0,24}(?:验证过|亲测过|实测过|测过|跑过|试过|用过|搭过|踩过|做过|走过|花了|耗时|用了|跑了|测了|试了|做了|搭了|验证了|发现|以为|遇到过)|我(?:们)?(?:亲手|亲自|早期|之前|已经|曾经|当时|第一次|刚开始)|我的(?:流程|做法|实测|经验|结论)', sentence):
            if not re.search(r'准备|计划|打算|待补|待验证|待实测|想先|需要|我会|我想|等我|待我|还没|尚未|没有', sentence):
                raise ValueError('AI 初稿含无证据的第一人称经历，底稿保留')
    original = item.get('full_text') or ''
    if original.strip() and (body.strip() == original.strip() or len(original.strip()) >= 80 and original.strip() in body):
        raise ValueError('AI 初稿复制了原文，底稿保留')
    return title.strip(), parts


class ItemWriter:
    def __init__(self, server, model=None, timeout=TIMEOUT):
        self.server = server
        self.store = server.store
        self.items = ItemsStore(self.store.workspace)
        self.lock = threading.RLock()
        self.live = set()
        self.model = model or self._model
        self.timeout = min(timeout, TIMEOUT)

    def _path(self, slug):
        return self.store.article(slug) / 'item-draft-status.json'

    def _write(self, slug, data):
        from content_store import write
        write(self._path(slug), data)

    def _model(self, item):
        # 只读取当前激活 profile；不调用 assistant.start、不写 active、不继承聊天工具。
        config = self.server.assistant.config()
        config['timeout_seconds'] = min(config.get('timeout_seconds', TIMEOUT), self.timeout)
        config['max_output_tokens'] = min(config.get('max_output_tokens', 32768), 8192)
        prompt = json.dumps({'evidence_policy':'本次只有第三方素材，没有黄总亲测证据。第一人称只写真实问题、计划、待验证动作。不得声称自己已经测过、用过、验证过，不得把内容画像当成亲测记录。', 'format': '3–8 条推文串' if draft_kind(item) == 'thread' else '单篇 300–800 字', 'source_item': item}, ensure_ascii=False)
        # 现有 Chat Completions 单次客户端：没有 Agent 循环、工具或压缩补调。
        reply = self.server.assistant.request_model([{'role':'system','content':TEMPLATE.read_text()}, {'role':'user','content':prompt}], allowed_tools=(), model_config=config)
        return reply.get('content') or ''

    def start(self, identifier, payload):
        if not isinstance(payload,dict) or set(payload)-{'force','from_today','title_prefix'} or any(type(payload.get(k,False)) is not bool for k in ('force','from_today')):
            raise ValueError('建稿参数无效')
        # 验收前缀只是标题，不接受任意系统提示词或正文覆盖。
        if payload.get('title_prefix','') not in ('','【验收】'):
            raise ValueError('标题前缀无效')
        started = time.monotonic()
        with self.lock:
            try:
                item = self.items.detail(identifier)
            except ValueError:
                if not payload.get('from_today'):
                    raise
                from today_view import item_view
                item = item_view(self.store.workspace, {'id':[identifier]})
                self.items.upsert({**item, 'why_viral':item.get('why',''), 'analysis':{**(item.get('analysis') or {}), 'pattern':item.get('pattern','')}})
                item = {**self.items.detail(identifier), **item}
            if item.get('trashed_at'):
                raise ValueError('请先恢复素材，再拿去写')
            prior = item.get('used_work_id')
            if prior and not payload.get('force'):
                self.store.meta(prior)  # 已有作品丢失时明确报错，不悄悄另建。
                return {**self.status(identifier), 'reused':True}
            if payload.get('from_today'):
                from today_view import save_feedback, item_view
                fresh = item_view(self.store.workspace, {'id':[identifier]})
                save_feedback(self.store.workspace, {'id':identifier,'action':'use'})
                item = {**item, **fresh}
            if not item.get('pattern') and not (item.get('analysis') or {}).get('pattern'):
                # 二期旧收藏没有独立 pattern 列；能按同一 ID 找回时补入底稿，找不到则明确占位。
                try:
                    from today_view import item_view
                    item['pattern'] = item_view(self.store.workspace, {'id':[identifier]}).get('pattern','')
                except (ValueError,OSError):
                    pass
            title = (payload.get('title_prefix','') + (item.get('title') or '素材写作底稿'))[:500]
            with self.store.lock:
                created = self.store.create({'title':title, 'type':'post'})
                slug = created['slug']
                saved = self.store.save(slug, {'base_version':created['head'], 'title':title, 'blocks':blocks_for(item), 'summary':'素材写作底稿'})
                from content_store import write
                write(self.store.article(slug) / 'source-item.json', {'item_id':identifier,'sources':item.get('sources',[]),'url':item.get('url',''),'legacy_refs':item.get('legacy_refs',[]),'created_at':now()})
                self.items.mark_used(identifier, slug)
                state = {'status':'pending','slug':slug,'item_id':identifier,'base_version':saved['version'],'created_at':now(),'elapsed':0,'error':'','title_prefix':payload.get('title_prefix',''),'notice':'底稿已就绪，AI 正在起草'}
                self._write(slug, state)
            if any(r.get('store')=='weknora' for r in item.get('legacy_refs',[]) if isinstance(r,dict)):
                state.update(status='source_only',notice='资料底稿已保存；请核对片段和隐私后手动创作，未自动发送给模型')
                self._write(slug,state)
                return {**state,'reused':False,'draft_seconds':round(time.monotonic()-started,3)}
            self.live.add(slug)
            threading.Thread(target=self._run, args=(item,state), daemon=True, name='item-draft-' + slug).start()
            return {**state, 'reused':False, 'draft_seconds':round(time.monotonic()-started,3)}

    def _run(self, item, state):
        started = time.monotonic()
        slug = state['slug']
        result = []
        finished = threading.Event()
        def invoke():
            try:
                result.append((self.model(item), None))
            except Exception as exc:
                # Runtime/transport 错误可能带凭据。只暴露分类与安全提示。
                result.append((None, '模型超时，底稿保留' if isinstance(exc,TimeoutError) else '模型调用失败，底稿保留；请检查当前模型配置或稍后重试'))
            finally:
                finished.set()
        threading.Thread(target=invoke, daemon=True, name='item-model-' + slug).start()
        try:
            if not finished.wait(self.timeout):
                raise ValueError('AI 起草超过时限，底稿保留')
            answer, error = result[0]
            if error:
                raise ValueError(error)
            title, parts = parse_answer(answer, item)
            if state.get('title_prefix') and not title.startswith(state['title_prefix']):
                title = (state['title_prefix'] + title)[:500]
            blocks = [{'kind':'text','html':'<p>'+escape(p).replace('\n','<br>')+'</p>'} for p in parts]
            try:
                saved = self.store.save(slug, {'base_version':state['base_version'],'title':title,'blocks':blocks,'summary':'AI 初稿'})
                state.update(status='done', version=saved['version'], notice='AI 初稿已保存，打开作品查看最新版本')
            except Exception as exc:
                from server import Conflict
                if not isinstance(exc, Conflict):
                    raise
                path = self.store.article(slug) / 'ai-draft.md'
                path.write_text('# ' + title + '\n\n' + '\n\n---\n\n'.join(parts))
                state.update(status='conflict', notice='AI 初稿已另存，没有覆盖你的修改')
        except ValueError as exc:
            state.update(status='failed', error=str(exc), notice=str(exc))
        except Exception:
            state.update(status='failed', error='AI 初稿未能保存，底稿保留', notice='AI 初稿未能保存，底稿保留')
        finally:
            state['elapsed'] = round(time.monotonic()-started,3)
            with self.lock:
                self._write(slug,state)
                self.live.discard(slug)

    def status(self, identifier):
        with self.lock:
            item = self.items.detail(identifier)
            slug = item.get('used_work_id')
            if not slug:
                return {'status':None,'slug':None,'elapsed':0,'notice':''}
            path = self._path(slug)
            if not path.exists():
                return {'status':'failed','slug':slug,'elapsed':0,'error':'未找到起草状态；已有作品保留','notice':'已有作品保留'}
            state = json.loads(path.read_text())
            if state['status'] == 'pending':
                if slug not in self.live:
                    state.update(status='failed',error='服务曾重启，AI 起草已停止，底稿保留',notice='服务曾重启，AI 起草已停止，底稿保留')
                    self._write(slug,state)
                else:
                    try:
                        from datetime import datetime,timezone
                        state['elapsed'] = round((datetime.now(timezone.utc)-datetime.fromisoformat(state['created_at'])).total_seconds(),3)
                    except ValueError:
                        pass
            return state


def service(server):
    with _SERVICE_LOCK:
        if not hasattr(server, '_item_writer'):
            server._item_writer = ItemWriter(server)
        return server._item_writer


def post_route(handler, identifier, payload):
    from urllib.parse import unquote
    return handler.reply(201, service(handler.server).start(unquote(identifier), payload))


def status_route(handler, identifier):
    return handler.reply(200, service(handler.server).status(identifier))
