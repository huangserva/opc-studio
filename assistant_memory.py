"""Dedicated Mac memory tools exposed through the existing private Pi bridge."""
import hashlib
import copy

from assistant_knowledge import KnowledgeTools, OUTPUT_BUDGET
from assistant_remio import RemioTools
from assistant_memos import MemosTools


MEMORY_READ_TOOLS = {'remio_status', 'remio_search', 'remio_read', 'memos_status', 'memos_search'}
MEMORY_WRITE_TOOLS = {'remio_create_note', 'remio_update_note', 'memos_write'}
MEMORY_TOOLS = MEMORY_READ_TOOLS | MEMORY_WRITE_TOOLS


class MemoryTools:
    def __init__(self, workspace):
        self.remio = RemioTools(workspace)
        self.memos = MemosTools(workspace)

    def capabilities(self):
        return {
            'execution_host': '这台 Mac 的工作台后端',
            'tools': sorted(MEMORY_TOOLS),
            'remio': self.remio.capabilities(),
            'memos': self.memos.capabilities(),
            'connection_verified': False,
            'status_tools': ['remio_status', 'memos_status'],
            'retrieval': '按需关键词检索和限定片段读取；与工作台的部分来源索引分开',
            'output_budget_chars': 16000,
            'writes': '仅当前用户明确要求记住、保存或追加时开放；默认保存到 remio，明确指定 MemOS 时才写 MemOS',
            'update_note': '仅向本轮已读 remio 笔记追加内容，不覆盖原正文',
            'idempotency': '后端以当前请求编号去重；不确定写入不会自动重试',
            'write_batch': '每条消息每个保存工具只提交一份内容；多项事实先合并，保存后增补请下一条消息继续',
            'note': '静态接入声明不代表服务连通；实际可用性请查询 status 工具',
        }

    def status(self):
        """Fixed local connectivity probes; never search or modify user memories."""
        return {'execution_host': '这台 Mac 的工作台后端',
                'remio': self.remio.status(), 'memos': self.memos.status()}

    @staticmethod
    def _args(args, allowed, required=()):
        if not isinstance(args, dict) or not set(args) <= set(allowed) or not set(required) <= set(args):
            raise ValueError('记忆工具参数不符合声明；不能指定命令、主机、地址、凭据或请求编号')

    @staticmethod
    def _request_id(name, args, job):
        identity = job.get('id')
        if not isinstance(identity, str) or not identity:
            raise ValueError('记忆写入缺少本轮稳定请求编号')
        # Repeated tool calls, including a new tool-call id, retain the same key.
        key = identity + ':' + name
        return hashlib.sha256(key.encode()).hexdigest()

    def execute(self, name, args, job):
        KnowledgeTools.check_budget(job)
        if not isinstance(args, dict):
            raise ValueError('记忆工具参数必须是对象')
        if name in MEMORY_WRITE_TOOLS and name not in (job.get('policy') or {}).get('allowed_tools', []):
            raise ValueError('当前消息没有授权此记忆保存操作')
        if name in MEMORY_WRITE_TOOLS and job.get('_knowledge_output_chars', 0) > OUTPUT_BUDGET - 2500:
            raise ValueError('本轮剩余输出预算不足以返回保存凭据；请下一条消息再保存')
        secret = (job.get('model_config') or {}).get('api_key')
        if isinstance(secret, str) and secret and any(isinstance(value, str) and secret in value for value in (args or {}).values()):
            raise ValueError('记忆工具请求不能包含模型密钥')
        if name == 'remio_status':
            self._args(args, ())
            result = self.remio.status(job=job)
        elif name == 'remio_search':
            self._args(args, ('query', 'limit'), ('query',))
            result = self.remio.search(args, job)
        elif name == 'remio_read':
            self._args(args, ('note_id', 'offset', 'limit', 'max_chars'), ('note_id',))
            result = self.remio.read(args, job)
        elif name == 'remio_create_note':
            self._args(args, ('title', 'content'), ('title', 'content'))
            result = self.remio.create({**args, 'request_id': self._request_id(name, args, job)}, job)
        elif name == 'remio_update_note':
            self._args(args, ('note_id', 'append_text'), ('note_id', 'append_text'))
            if args['note_id'] not in job.get('_remio_read_notes', set()):
                raise ValueError('必须先在本轮 remio_read 核对这份笔记，再追加内容')
            result = self.remio.update({**args, 'request_id': self._request_id(name, args, job)}, job)
        elif name in ('memos_status', 'memos_search', 'memos_write'):
            # Pass secrets before the Node bridge selects/truncates passages.
            # Clone per call so another status probe cannot change this context.
            memos = copy.copy(self.memos)
            memos.sensitive_values = tuple(getattr(self.memos, 'sensitive_values', ())) + ((secret,) if isinstance(secret, str) and secret else ())
            if name == 'memos_status':
                self._args(args, ())
                result = memos.status()
            elif name == 'memos_search':
                self._args(args, ('query', 'limit'), ('query',))
                result = memos.search(args['query'], args.get('limit', 5))
            else:
                self._args(args, ('text',), ('text',))
                result = memos.write(args['text'], self._request_id(name, args, job),
                                          conversation_id=job.get('conversation_id'))
        else:
            raise ValueError('未知记忆工具')
        def redact(value):
            if isinstance(value, str) and isinstance(secret, str) and secret:
                return value.replace(secret, '[凭据已隐藏]')
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value
        emitted = KnowledgeTools.emit(redact(copy.deepcopy(result)), job)
        if name == 'remio_read' and emitted.get('ok'):
            job.setdefault('_remio_read_notes', set()).add(args['note_id'])
        return emitted
