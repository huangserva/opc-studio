"""schedules_assistant HTTP routes, extracted without changing business behavior.

The entry point keeps security, body parsing, dispatch order and fallback.
"""
from urllib.parse import parse_qs, unquote
import re


def get_route(handler, parts):
    self = handler
    segments = [unquote(x) for x in parts.path.split('/') if x]
    query = parse_qs(parts.query)
    store = self.server.store
    if len(segments)==2 and segments[0]=='chat-attachments' and re.fullmatch(r'[a-f0-9]{32}\.png',segments[1]):
        return self.serve_file(self.server.assistant.attachment_path(segments[1][:-4]))
    if parts.path == '/api/schedules':
        return self.reply(200, {**self.server.schedules.listing(),
                                'external': self.server.schedules.external_tasks.snapshot()})
    if parts.path == '/api/schedules/health':
        return self.reply(200, self.server.schedules.health())
    if len(segments)==4 and segments[:3]==['api','schedules','run']:
        run=self.server.schedules.get_run(segments[3])
        if 'event' in query:
            if set(query)-{'event','offset'} or any(len(v)!=1 for v in query.values()):raise ValueError('事件输出参数无效')
            if run['snapshot'].get('kind')!='source_events':raise ValueError('此运行不是来源事件任务')
            return self.reply(200,store.wechat_source.event_outputs(segments[3],int(query['event'][0]),int(query.get('offset',['0'])[0])))
        return self.reply(200,run)
    if len(segments)==4 and segments[:2]==['api','schedules'] and segments[3]=='outsourced_prompt':
        # 只读：按任务定义拼一份可直接粘给 WorkBuddy 的提示词（impl-outsourced-tasks 新增的唯一路由）。
        return self.reply(200,self.server.schedules.outsourced_prompt(segments[2]))
    if parts.path == '/api/schedules/runs':
        parameters = parse_qs(parts.query, keep_blank_values=True)
        allowed = {'task_id', 'q', 'status', 'page', 'page_size'}
        if set(parameters) - allowed or any(len(values) != 1 for values in parameters.values()):
            raise ValueError('运行记录查询参数无效或重复')
        page = int(parameters.get('page', ['1'])[0])
        page_size = int(parameters.get('page_size', ['50'])[0])
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError('页码至少为 1，每页记录数必须为 1–100')
        return self.reply(200, self.server.schedules.runs(
            task_id=parameters.get('task_id', [None])[0] or None,
            q=parameters.get('q', [''])[0],
            status=parameters.get('status', [None])[0] or None,
            page=page, page_size=page_size))
    if parts.path == '/api/assistant/settings':
        return self.reply(200, self.server.assistant.settings())
    if parts.path == '/api/assistant':
        return self.reply(200, self.server.assistant.status())
    if parts.path == '/api/assistant/capabilities':
        parameters = parse_qs(parts.query, keep_blank_values=True)
        if set(parameters) - {'kind'} or len(parameters.get('kind', [])) > 1:
            raise ValueError('能力查询仅接受一个可选的 kind 参数')
        return self.reply(200, self.server.assistant.capabilities(parameters.get('kind', ['all'])[0]))
    if parts.path == '/api/assistant/ssh':
        if parts.query:
            raise ValueError('SSH 连接测试不接受主机、命令或其他参数')
        return self.reply(200, self.server.assistant.ssh.status())
    if parts.path == '/api/assistant/memory':
        if parts.query:
            raise ValueError('本机记忆连接测试不接受地址、命令或检索参数')
        return self.reply(200, self.server.assistant.memory_tools.status())
    if len(segments) == 4 and segments[:3] == ['api', 'assistant', 'job']:
        return self.reply(200, self.server.assistant.public_job(segments[3]))
    if len(segments) == 2 and segments[0] == 'assistant-images' and re.fullmatch(r'[a-f0-9]{32}\.png', segments[1]):
        return self.serve_file(store.workspace / 'assistant' / 'images' / segments[1])
    return None


def post_route(handler, segments, payload):
    self = handler
    if segments == ['api','assistant','attachments']:
        return self.reply(201,self.server.assistant.upload_chat_image(payload))
    if segments == ['api', 'schedules', 'save']:
        return self.reply(200, self.server.schedules.save(payload))
    if segments == ['api', 'schedules', 'batch']:
        if isinstance(payload,dict) and 'source_action' in payload:
            from source_orchestra import presets
            options=dict(payload);options['action']=options.pop('source_action')
            return self.reply(200,presets(self.server.schedules,options))
        return self.reply(200, self.server.schedules.batch(payload))
    if len(segments) == 4 and segments[:2] == ['api', 'schedules']:
        if segments[3] == 'publish':
            return self.reply(201,self.server.schedules.deliver_result(segments[2]))
        if segments[3] == 'cancel':
            return self.reply(200, self.server.schedules.cancel(segments[2]))
        return self.reply(200, self.server.schedules.control(segments[2], segments[3]))
    if segments == ['api', 'assistant', 'settings']:
        return self.reply(200, self.server.assistant.save_settings(payload))
    if segments == ['api', 'assistant', 'select-model']:
        return self.reply(200, self.server.assistant.select_model(payload))
    if segments == ['api', 'assistant', 'test-settings']:
        return self.reply(200, self.server.assistant.test_settings(payload))
    if len(segments)==5 and segments[:3]==['api','assistant','created-work'] and segments[4] in ('undo','restore'):
        if payload != {}:raise ValueError('撤销或恢复只接收作品编号')
        return self.reply(200,self.server.store.archive_assistant_creation(segments[3],segments[4]=='undo'))
    if segments == ['api', 'assistant', 'send']:
        return self.reply(202, self.server.assistant.start(payload))
    if len(segments)==5 and segments[:3]==['api','assistant','workflows'] and segments[4]=='save':
        return self.reply(200,self.server.assistant.workflows.save(segments[3]))
    if segments == ['api', 'assistant', 'stop']:
        return self.reply(200, self.server.assistant.stop(payload['id']))
    if segments == ['api', 'assistant', 'new-chat']:
        return self.reply(200, self.server.assistant.new_chat())
    return None
