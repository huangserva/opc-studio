"""intake_cards HTTP routes, extracted without changing business behavior.

The entry point keeps security, body parsing, dispatch order and fallback.
"""
from urllib.parse import parse_qs, unquote
import re


def get_route(handler, parts):
    self = handler
    segments = [unquote(x) for x in parts.path.split('/') if x]
    query = parse_qs(parts.query)
    store = self.server.store
    if parts.path == '/api/intake':
        params = parse_qs(parts.query, keep_blank_values=True)
        if set(params) - {'limit'} or len(params.get('limit', [])) > 1:
            raise ValueError('回收站列表只接受一个可选的 limit 参数')
        return self.reply(200, store.creative_intake.listing(int(params['limit'][0]) if params.get('limit') else 80))
    if parts.path == '/api/intake/analysis-status':
        # 回收站页面用它判断这个后端是否支持即时分析。旧版本没有这个接口（404），
        # 页面就如实显示「服务未就绪」，不再给出误导性的「点重新分析」。
        cards = store.intake_cards
        try:
            configured = bool(cards.analysis_model())
        except (OSError, ValueError, KeyError):
            configured = False
        return self.reply(200, {'schema': 'intake-analysis-status/v1', 'supported': True,
                                'model_configured': configured, 'working': len(cards.analysis_workers),
                                'note': '即时分析只复用「创意提炼」已配置的模型，不新增服务、不读密钥'})
    if len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'file':
        return self.serve_file(store.creative_intake.file_path(segments[2]))
    if len(segments) == 3 and segments[:2] == ['api', 'intake']:
        return self.reply(200, store.creative_intake.detail(segments[2]))
    if parts.path == '/api/cards':
        params = parse_qs(parts.query, keep_blank_values=True)
        if set(params) - {'status', 'q', 'limit', 'offset', 'kind', 'view'} or any(len(values) > 1 for values in params.values()):
            raise ValueError('提示词卡列表只接受 status、q、limit、offset、kind、view 各一个')
        return self.reply(200, store.intake_cards.listing(
            params.get('status', [''])[0], params.get('q', [''])[0],
            int(params['limit'][0]) if params.get('limit') else 60,
            int(params['offset'][0]) if params.get('offset') else 0,
            params.get('kind', [''])[0], params.get('view', ['source'])[0] or 'source'))
    if len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] == 'prompt':
        return self.reply(200, store.intake_cards.prompt_card(segments[2]))
    if len(segments) == 3 and segments[:2] == ['api', 'cards']:
        return self.reply(200, store.intake_cards.detail(segments[2]))
    return None


def post_route(handler, segments, payload):
    self = handler
    if len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'resolve':
        if payload:
            raise ValueError('解析回收项不接受额外字段')
        result = self.server.store.creative_intake.resolve(segments[2])
        # 入库成功就排队一次即时分析；模型调用在后台线程，不拖住这次响应。
        if result.get('status') == 'resolved':
            try:
                result['analysis'] = self.server.store.intake_cards.request_analysis(segments[2])
            except ValueError as exc:
                result['analysis'] = {'status': 'failed', 'error': str(exc)[:300], 'retryable': True}
        return self.reply(200, result)
    if len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'analyze':
        # 手动重跑（失败后可用，也可用于媒体识别完成后刷新分析）。
        if payload:
            raise ValueError('重新分析不接受额外字段')
        return self.reply(200, self.server.store.intake_cards.request_analysis(segments[2], force=True))
    if len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'delete':
        # 删除：回收项 + 即时分析 + （最后一条引用时）知识库正文 + 本机媒体文件。
        # 已生成的提示词卡不删，卡库会显示「来源已删除」。
        if payload:
            raise ValueError('删除回收项不接受额外字段')
        return self.reply(200, self.server.store.creative_intake.delete(segments[2]))
    if segments == ['api', 'intake', 'delete-preview']:
        # 批量删除前的预览：只读，给确认框用（条数、类型分布、正文删几篇留几篇、涉及几张卡）。
        if not isinstance(payload, dict) or set(payload) - {'ids'} or not isinstance(payload.get('ids'), list):
            raise ValueError('批量删除预览只接受 ids 列表')
        return self.reply(200, self.server.store.creative_intake.delete_preview(payload['ids']))
    if segments == ['api', 'intake', 'delete-many']:
        # 批量删除：逐条走单条删除的同一套规则，一次最多 50 条，逐条回执。
        if not isinstance(payload, dict) or set(payload) - {'ids'} or not isinstance(payload.get('ids'), list):
            raise ValueError('批量删除只接受 ids 列表')
        return self.reply(200, self.server.store.creative_intake.delete_many(payload['ids']))
    if len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] in ('confirm', 'reject', 'restore'):
        if set(payload) - {'reason'}:
            raise ValueError('卡片确认只接受 reason')
        return self.reply(200, self.server.store.intake_cards.decide(segments[2], segments[3],
                                                                    payload.get('reason', '')))
    if len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] == 'note':
        if set(payload) - {'note'}:
            raise ValueError('卡片备注只接受 note')
        return self.reply(200, self.server.store.intake_cards.save_note(segments[2], payload.get('note', '')))
    if len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] == 'delete':
        if payload:
            raise ValueError('删除卡片不接受额外字段')
        return self.reply(200, self.server.store.intake_cards.delete_card(segments[2]))
    if segments == ['api', 'cards', 'cleanup']:
        if not isinstance(payload, dict) or set(payload) - {'drop_id'}:
            raise ValueError('清理卡片只接受 drop_id')
        return self.reply(200, self.server.store.intake_cards.delete_deleted_source(payload.get('drop_id', '')))
    return None
