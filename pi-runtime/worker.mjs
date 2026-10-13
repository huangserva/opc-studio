/* Pi owns model/tool turns and persistent context. Python owns business mutations. */
import fs from 'node:fs';
import {manageImageContext, monitoredStream} from './request-context.mjs';
import path from 'node:path';
import {
  createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager,
} from '@earendil-works/pi-coding-agent';
import {
  InMemoryCredentialStore, createAssistantMessageEventStream,
  getCurrentSystemPrompt, getCurrentTools,
} from '@earendil-works/pi-ai';
import { streamSimple } from '@earendil-works/pi-ai/api/openai-completions';

const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
// Stdout is a private framed protocol, never a log channel.
console.log = () => {};
let buffer = '', session, request, started = false, cancelled = false, calls = 0;
const pending = new Map();
function rpc(type, payload, signal) {
  if (signal?.aborted || cancelled) return Promise.reject(new Error('cancelled'));
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const abort = () => { pending.delete(id); reject(new Error('cancelled')); };
    signal?.addEventListener('abort', abort, { once: true });
    pending.set(id, { resolve, reject, cleanup: () => signal?.removeEventListener('abort', abort) });
    emit({ type, id, ...payload });
  });
}
const usage = () => ({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } });
function assistantMessage(model, content, stopReason = 'stop') {
  return { role: 'assistant', content, api: model.api, provider: model.provider, model: model.id,
    usage: usage(), stopReason, timestamp: Date.now() };
}
// Dependency injection for protocol tests; production always uses pi-ai's provider adapter.
function legacyMessages(context) {
  const result = [{ role: 'system', content: getCurrentSystemPrompt(context.messages) }];
  for (const m of context.messages) {
    if (m.role === 'system') continue;
    if (m.role === 'user') {
      const content = typeof m.content === 'string' ? m.content : m.content.map(p => p.type === 'image'
        ? { type: 'image_url', image_url: { url: `data:${p.mimeType};base64,${p.data}` } } : p);
      result.push({ role: 'user', content });
    } else if (m.role === 'assistant') {
      const tc = m.content.filter(p => p.type === 'toolCall');
      result.push({ role: 'assistant', content: m.content.filter(p => p.type === 'text').map(p => p.text).join(''),
        ...(tc.length ? { tool_calls: tc.map(p => ({ id: p.id, type: 'function',
          function: { name: p.name, arguments: JSON.stringify(p.arguments) } })) } : {}) });
    } else if (m.role === 'toolResult') {
      result.push({ role: 'tool', tool_call_id: m.toolCallId, content: m.content.map(p => p.text || '').join('') });
    }
  }
  return result;
}
function testStream(model, context, options) {
  const stream = createAssistantMessageEventStream();
  (async () => {
    try {
      const response = await rpc('model_request', { messages: legacyMessages(context),
        tools: getCurrentTools(context.messages).map(t => t.name) }, options?.signal);
      const content = response.content ? [{ type: 'text', text: response.content }] : [];
      for (const call of response.tool_calls || []) content.push({ type: 'toolCall', id: call.id,
        name: call.function.name, arguments: JSON.parse(call.function.arguments || '{}') });
      const m = assistantMessage(model, content, response.tool_calls?.length ? 'toolUse' : 'stop');
      stream.push({ type: 'start', partial: m });
      stream.push({ type: 'done', reason: m.stopReason, message: m });
    } catch {
      const m = assistantMessage(model, [], cancelled ? 'aborted' : 'error');
      m.errorMessage = cancelled ? '已停止' : '测试模型返回异常';
      stream.push({ type: 'start', partial: m });stream.push({ type: 'error', reason: m.stopReason, error: m });
    }
  })();
  return stream;
}
function modelFailure(message, seconds) {
  const detail = String(message?.errorMessage || '');
  const code = detail.match(/\b(400|401|403|404|413|415|422|429|500|502|503|504)\b/)?.[1];
  if (code) return `模型服务返回 HTTP ${code}，${['401','403'].includes(code) ? '请核对模型接口鉴权配置' : code === '429' ? '服务繁忙或请求已限流，请稍后重试' : '请核对模型服务状态与请求参数'}。已保存的操作仍保留。`;
  if (message?.stopReason === 'aborted') return `模型请求超时或中断（连续 ${seconds} 秒无响应），请稍后重试。已保存的操作仍保留。`;
  return '模型请求未完成，请检查模型服务后重试。';
}
// 模型调了本轮没有开放的工具。只按本轮实际开放的名字说话：
// 不说工具「没登记」——那是全局注册表的事，与本轮开不开放无关（复核 L-6）。
// 提示里不得出现参数、HTML、正文、图片和密钥，工具名本身也不写给用户。
const TOOL_POLICY_FALLBACK='本轮没有开放这个工具，所以没有执行这一步。';
function toolPolicyHint(names){
  const saved = names && names.savedBeforeStop;
  const lead = TOOL_POLICY_FALLBACK
    + (saved ? '' : '作品内容没有因此改变。')
    + '请确认页面上打开着要改的那一篇作品，再直接说要做什么。';
  return lead;
}
function importHistory(manager, history, model) {
  const imported = new Set(manager.getEntries().filter(e => e.type === 'custom' && e.customType === 'studio-turn').map(e => e.data.id));
  for (const turn of history || []) {
    if (imported.has(turn.id)) continue;
    manager.appendMessage({ role: 'user', content: [{ type: 'text', text: turn.prompt }, ...(turn.images || []),
      ...(turn.attachment_refs || []).map(ref=>({type:'text',text:`[聊天原图 attachment_id=${ref.id}，名称=${JSON.stringify(ref.name)}，可用 read_chat_image 重新查看。]`}))],
      timestamp: Date.parse(turn.time) || Date.now() });
    manager.appendMessage(assistantMessage(model, [{ type: 'text', text: turn.answer || '这条历史任务已结束。' }], turn.status === 'done' ? 'stop' : 'error'));
    manager.appendCustomEntry('studio-turn', { id: turn.id, status: turn.status });
  }
}
function optionalRunLimits(input) {
  // 缺省与改前一致：压缩开着，一轮最多 12 次 provider 请求。调用方显式给出才收紧。
  let maxProviderRequests = 12;
  if (input.max_provider_requests != null) {
    if (!Number.isInteger(input.max_provider_requests) || input.max_provider_requests < 1)
      throw new Error('模型调用上限无效');
    maxProviderRequests = input.max_provider_requests;
  }
  let compactionOn = true;
  if (input.compaction_enabled != null) {
    if (typeof input.compaction_enabled !== 'boolean') throw new Error('压缩开关无效');
    compactionOn = input.compaction_enabled;
  }
  return { maxProviderRequests, compactionOn };
}
async function run(input) {
  request = input;
  let timer, mediaReceipt, stageReceipt, dealReceipt, toolViolation, pasteStop;
  let blockedToolNames = [];
  let lastEditReject = '', editRejects = 0, savedBeforeStop = false;
  const workBaseVersions = new Map();
  try {
    const limits = optionalRunLimits(input);
    fs.mkdirSync(input.session_dir, { recursive: true, mode: 0o700 });
    const config = input.config;
    const settings = SettingsManager.inMemory({
      enableAnalytics: false, enableInstallTelemetry: false, cacheWarming: 'off',
      retry: { enabled: false, provider: { maxRetries: 0, timeoutMs: config.timeout_seconds * 1000 } },
      compaction: { enabled: limits.compactionOn, reserveTokens: Math.min(config.max_output_tokens + 2048,
        Math.floor(config.context_tokens / 3)), keepRecentTokens: Math.min(16000, Math.floor(config.context_tokens / 4)) },
      images: { autoResize: false },
    });
    const runtime = await ModelRuntime.create({ credentials: new InMemoryCredentialStore(), modelsPath: null,
      refreshOnCreate: false, allowModelNetwork: false });
    let requests = 0;
    runtime.registerProvider('studio', {
      baseUrl: config.endpoint.replace(/\/chat\/completions\/?$/, ''), api: 'openai-completions',
      apiKey: config.api_key || 'local-no-key', authHeader: !config.no_api_key,
      models: [{ id: config.model, name: config.model, input: ['text', 'image'], reasoning: /^qwen/i.test(config.model),
        contextWindow: config.context_tokens, maxTokens: config.max_output_tokens,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        compat: { supportsStore: false, supportsDeveloperRole: false, maxTokensField: 'max_tokens',
          ...(/^qwen/i.test(config.model) ? {thinkingFormat:'qwen-chat-template', supportsReasoningEffort:false} : {}) } }],
      streamSimple(model, context, options) {
        if (++requests > limits.maxProviderRequests) throw new Error('本轮已到模型调用上限');
        if (input.test_transport) return testStream(model, context, options);
        emit({type:'phase',phase:'模型正在读取上下文，长内容首字需要等待…'});
        let thinking = false;
        return monitoredStream(streamOptions => streamSimple(model, context, {
          ...streamOptions, temperature: config.temperature, maxTokens: config.max_output_tokens,
          onPayload: async (payload, model) => {
            const upstream = await options?.onPayload?.(payload, model);
            const prepared = upstream ?? payload;
            emit({type:'request_stats',messages:prepared.messages.length,
              images:prepared.messages.flatMap(m=>Array.isArray(m.content)?m.content:[]).filter(p=>p.type==='image_url').length});
            return prepared;
          },
        }), options, config.timeout_seconds, createAssistantMessageEventStream, event => {
          if (event.type === 'thinking_delta' && !thinking) {
            thinking = true;emit({type:'phase',phase:'模型正在思考…'});
          }
        }, error => {
          const message = assistantMessage(model, [], 'error');message.errorMessage = String(error.message);
          return {type:'error',reason:'error',error:message};
        });
      },
    });
    const model = runtime.getModel('studio', config.model);
    const manager = SessionManager.continueRecent(input.cwd, input.session_dir);
    importHistory(manager, input.history, model);
    manageImageContext(manager, input.image_catalog);
    // Mark before prompting so a killed worker cannot duplicate its native turn on resume.
    manager.appendCustomEntry('studio-turn', { id: input.job_id, status: 'accepted' });
    const customTools = input.tools.map(t => ({ name: t.function.name, label: t.label || t.function.name,
      description: t.function.description + (t.function.name === 'propose_deal_update' ? '\n商单进度 stage 使用 read_deal 返回的 stage_options。待写 writing 与撰写中 in_progress 不同。等待打款是收款进度，用 stage="payment_pending"（待结账）；承诺日期写 notes，不写交稿截止 deadline。确认合作全部到账并结清才用 stage="settled"，尚无实际明细时同时需要用户明确确认 settled=true；未实际到账不创建 receipt。' : ''), parameters: t.function.parameters, executionMode: 'sequential',
      async execute(toolCallId, params, signal) {
        if (pasteStop) {
          const error = '本轮已停止，未执行';
          return { content: [{ type: 'text', text: JSON.stringify({ error }) }], details: { error }, isError: true };
        }
        if (++calls > 24) throw new Error('本轮已到操作上限');
        const result = await rpc('tool', { call_id: toolCallId, name: t.function.name, args: params }, signal);
        const baseVersion = params && typeof params.base_version === 'string' ? params.base_version : '';
        const slug = typeof result.slug === 'string' && result.slug ? result.slug : params?.slug;
        const writeTool = ['edit_work', 'update_text', 'reflow_markdown', 'create_card', 'organize_collection'].includes(t.function.name);
        if (slug && !workBaseVersions.has(slug)) {
          if (t.function.name === 'read_work' && !result.error && result.version)
            workBaseVersions.set(slug, result.version);
          else if (writeTool && baseVersion) workBaseVersions.set(slug, baseVersion);
        }
        // 版本号只在同一作品内比较，不能把 B 的新版本与 A 的起始版本比较。
        const baseline = slug ? workBaseVersions.get(slug) : undefined;
        const versionMoved = Boolean(result.version && (!baseline || result.version !== baseline));
        if (writeTool && !result.error && !result.unchanged && versionMoved) savedBeforeStop = true;
        if (t.function.name === 'edit_work') {
          if (result.error) {
            const reason = String(result.error);
            if (reason && reason === lastEditReject) editRejects += 1;
            else { lastEditReject = reason; editRejects = 1; }
            if (editRejects >= 2) {
              const detail = reason.replace(/[。！？]/g, '；').replace(/\s+/g, ' ').trim();
              const saved = savedBeforeStop
                ? '停止前已有修改保存成功，请核对作品里的新版本。'
                : '停止前没有保存新版本。';
              const pasteValidation = /贴文|paste_original|原文范围/.test(reason);
              pasteStop = pasteValidation
                ? '贴文已因同一原因连续被拒两次，原因是' + detail + '已停止继续调用工具，请换行写清完整原文后再发，或让助手使用 paste_original 而不是抄写 html。' + saved
                : '同一修改连续两次失败，已停止：' + detail + (/[；。]$/.test(detail) ? '' : '。') + saved;
              try { session?.agent?.abort(); } catch {}
              setTimeout(() => { try { session?.agent?.abort(); } catch {} }, 0);
            }
          } else {
            lastEditReject = '';
            editRejects = 0;
          }
        }
        if (input.finish_after_media && t.function.name === 'generate_media' && result.media_job && !result.error)
          mediaReceipt = '已提交 4090 生成任务。完成后会在聊天中直接显示预览，生成结果不会自动插入作品。';
        if (input.workflow_stage && t.function.name === 'submit_workflow_stage' && result.workflow_stage && !result.error)
          stageReceipt = result.workflow_stage;
        if (t.function.name === 'propose_deal_update' && result.deal_proposal && !result.error)
          dealReceipt = '修改预览已准备，请核对后点击“确认保存修改”。台账尚未改动。';
        const {image_content, ...details} = result;
        return { content: [{ type: 'text', text: JSON.stringify(details) }, ...(image_content ? [image_content] : [])], details,
          ...(result.error ? { isError: true } : {}) };
      } }));
    const loader = new DefaultResourceLoader({ cwd: input.cwd, agentDir: path.join(input.session_dir, 'resources'),
      settingsManager: settings, systemPrompt: input.system,
      additionalSkillPaths: input.skill_paths || [],
      noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true });
    await loader.reload();
    ({ session } = await createAgentSession({ cwd: input.cwd, agentDir: path.join(input.session_dir, 'resources'),
      modelRuntime: runtime, model, thinkingLevel: 'off', settingsManager: settings, sessionManager: manager,
      resourceLoader: loader, tools: customTools.map(t => t.name), customTools }));
    const unsubscribe = session.subscribe(event => {
      if (event.type === 'message_end' && event.message.role === 'assistant') {
        if (event.message.errorMessage) event.message.errorMessage = String(event.message.errorMessage)
          .split(config.api_key || '\u0000').join('[redacted]').replace(/https?:\/\/[^\s]+/g,'[endpoint]');
        const allowed = new Set(input.tools.map(t => t.function.name));
        const blocked = event.message.content.filter(p => p.type === 'toolCall' && !allowed.has(p.name));
        if (blocked.length) {
          const names=blocked.map(p =>
            typeof p.name === 'string' && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(p.name)
              ? p.name : '[invalid_tool_name]');
          const seen=[...new Set([...blockedToolNames.filter(n=>n!=='[truncated]'), ...names])];
          blockedToolNames=[...seen.slice(0,16),
            ...(seen.length>16||blockedToolNames.includes('[truncated]')?['[truncated]']:[])];
          toolViolation = (savedBeforeStop
            ? '被拦的操作未执行；停止前已有修改保存成功，请核对作品新版本。'
            : '本轮未执行修改。') + toolPolicyHint({ savedBeforeStop, names: blockedToolNames });
          session.agent.abort();
        }
      }
      if (event.type === 'turn_end' && (mediaReceipt || stageReceipt || dealReceipt || pasteStop)) session.agent.abort();
      if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta')
        emit({ type: 'text', delta: event.assistantMessageEvent.delta });
      if (event.type === 'message_start' && event.message.role === 'assistant') emit({ type: 'text_start' });
      if (event.type === 'auto_compaction_start') emit({ type: 'phase', phase: '正在整理对话上下文…' });
    });
    timer = setTimeout(() => { cancelled = true; void session.abort(); }, config.timeout_seconds * 1000 * 12 + 30000);
    emit({ type: 'ready', runtime: 'pi', version: '1.0.0', tools: session.getActiveToolNames() });
    if (cancelled) await session.abort();
    else await session.prompt(input.text, { images: input.images || [], expandPromptTemplates: false });
    const last = [...session.messages].reverse().find(m => m.role === 'assistant');
    const text = session.getLastAssistantText();
    const empty = !mediaReceipt && !stageReceipt && !dealReceipt && !toolViolation && !pasteStop && !cancelled && !text?.trim();
    const error = !dealReceipt && !pasteStop && !toolViolation && (['error', 'aborted'].includes(last?.stopReason) || empty);
    if (mediaReceipt && !cancelled) manager.appendMessage(assistantMessage(model,[{type:'text',text:mediaReceipt}]));
    if (dealReceipt && !cancelled) manager.appendMessage(assistantMessage(model,[{type:'text',text:dealReceipt}]));
    emit({ type: 'done', status: cancelled ? 'cancelled' : (toolViolation || pasteStop) ? 'error' : (mediaReceipt || stageReceipt || dealReceipt) ? 'done' : error ? 'error' : 'done',
      answer: cancelled ? '已停止。已保存的修改仍保留。' : toolViolation || pasteStop || mediaReceipt || dealReceipt || (stageReceipt ? '阶段完成' : (last?.stopReason === 'aborted' ? modelFailure(last, config.timeout_seconds) : empty ? '模型没有返回回复正文。请检查输出预算和模型服务后重试。' : error ? modelFailure(last, config.timeout_seconds) : text)),
      session_id: manager.getSessionId(), usage: last?.usage,
      ...(toolViolation ? {blocked_tool_names: blockedToolNames, error_kind: 'tool_policy'} : {}),
      stop_reason:last?.stopReason,content_types:last?.content.map(p=>p.type),
      ...(error ? { diagnostic: String(last?.errorMessage || '').split(config.api_key || '\u0000').join('[redacted]').replace(/https?:\/\/[^\s]+/g,'[endpoint]').slice(0,600) } : {}),
      ...(error ? { error_kind: /image|vision|multimodal/i.test(last?.errorMessage || '') ? 'vision' : 'model' } : {}) });
    unsubscribe();
  } catch (error) {
    if (input.test_transport) emit({type:'diagnostic',message:String(error.message),stack:String(error.stack)});
    emit({ type: 'done', status: cancelled ? 'cancelled' : 'error',
      answer: cancelled ? '已停止。' : 'Pi 助手未完成本次请求，请检查运行日志或模型连接。', error_kind: 'runtime' });
  } finally {
    clearTimeout(timer); session?.dispose();
    for (const p of pending.values()) { p.cleanup();p.reject(new Error('closed')); }
    pending.clear();process.exitCode = 0;process.stdin.destroy();
  }
}
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  buffer += chunk;
  let end;
  while ((end = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, end);buffer = buffer.slice(end + 1);
    let message;try { message = JSON.parse(line); } catch { continue; }
    if (message.type === 'run' && !started) { started = true;void run(message); }
    else if (message.type === 'cancel') { cancelled = true;void session?.abort(); }
    else if (message.type === 'result') {
      const p = pending.get(message.id);if (p) { pending.delete(message.id);p.cleanup();p.resolve(message.result); }
    }
  }
});
process.stdin.on('end', () => { cancelled = true;void session?.abort(); });
