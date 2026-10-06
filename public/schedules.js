'use strict';
const $ = selector => document.querySelector(selector);
const form = $('#task-form'), field = name => form.elements.namedItem(name);
const labels = {running:'运行中',succeeded:'成功',partial:'部分完成',failed:'失败',timeout:'超时',cancelled:'已停止',interrupted:'重启中断'};
const toolNames = {workspace:'工作台查询',memory:'记忆检索',ssh:'SSH 只读查询',web:'联网检索'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let state = {tasks:[],models:[],clue_threads:[]}, editing = null, runs = [];
let batchMode = false, batchBusy = false, filterTask = null, runPage = 1;
let listingRequest = 0, runsRequest = 0, searchTimer = null;
const selected = new Map();
function dateOf(value) {
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return value !== null && value !== undefined && value !== '' && Number.isFinite(date.getTime()) ? date : null;
}
function when(value) {
  const date = dateOf(value);
  return date ? date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}) : '—';
}
function exactTime(value) { return when(value) + (dateOf(value) ? '（北京时间）' : ''); }
function relative(value) {
  const date = dateOf(value);
  if (!date) return '尚无下次时间';
  const seconds = Math.ceil((date.getTime() - Date.now()) / 1000);
  if (seconds <= 0) return '已到计划时间';
  if (seconds < 60) return seconds + ' 秒后';
  if (seconds < 3600) return Math.ceil(seconds / 60) + ' 分钟后';
  if (seconds < 86400) return Math.ceil(seconds / 3600) + ' 小时后';
  return Math.ceil(seconds / 86400) + ' 天后';
}
function duration(run) {
  const start = dateOf(run?.started_at), end = dateOf(run?.finished_at);
  if (!start || (!end && run?.status !== 'running')) return '—';
  const elapsed = Math.max(0,(end ? end.getTime() : Date.now()) - start.getTime());
  if (elapsed > 0 && elapsed < 1000) return '<1 秒';
  const seconds = Math.floor(elapsed / 1000);
  if (seconds < 60) return seconds + ' 秒';
  if (seconds < 3600) return Math.floor(seconds / 60) + ' 分 ' + seconds % 60 + ' 秒';
  return Math.floor(seconds / 3600) + ' 小时 ' + Math.floor(seconds % 3600 / 60) + ' 分';
}
function triggerLabel(run) {
  return run.trigger === 'manual' ? '手动运行' : run.trigger === 'scheduled' ? '自动运行' : '来源未知';
}
function excerpt(value, limit = 240) {
  const characters = [...String(value ?? '').trim().replace(/\s+/g,' ')];
  return characters.slice(0,limit).join('') + (characters.length > limit ? '…' : '');
}
function retryDate(value) {
  if (typeof value === 'number') return Number.isFinite(value) && value > 0 ? dateOf(value) : null;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const calendar = dateOf(value.slice(0,10) + 'T00:00:00Z');
  return calendar?.toISOString().slice(0,10) === value.slice(0,10) ? dateOf(value) : null;
}
function wechatResult(run) {
  if (run.snapshot?.kind !== 'wechat_clues' || typeof run.output !== 'string' || run.output.length > 65536) return null;
  try {
    const data = JSON.parse(run.output);
    if (!data || typeof data !== 'object' || Array.isArray(data) || !Number.isSafeInteger(data.processed) || data.processed < 0 ||
        !Array.isArray(data.clues) || !data.clues.every(id => typeof id === 'string' && /^[a-f0-9]{32}$/i.test(id)) ||
        (data.skipped !== undefined && (!Number.isSafeInteger(data.skipped) || data.skipped < 0)) ||
        (data.has_more !== undefined && typeof data.has_more !== 'boolean')) return null;
    let invalidMetadata = false;
    const errors = [];
    if (data.errors !== undefined) {
      if (!Array.isArray(data.errors)) invalidMetadata = true;
      else for (const error of data.errors) {
        if (!error || typeof error !== 'object' || Array.isArray(error) || typeof error.message !== 'string' || !error.message.trim()) { invalidMetadata = true; continue; }
        const validCount = value => Number.isSafeInteger(value) && value >= 0;
        const nextRetry = retryDate(error.next_retry_at);
        const retryState = ['waiting','needs_review'].includes(error.state) ? error.state : null;
        if (error.attempts !== undefined && !validCount(error.attempts) || error.documents !== undefined && !validCount(error.documents) || error.next_retry_at !== undefined && error.next_retry_at !== null && !nextRetry) invalidMetadata = true;
        if (error.state !== undefined && !retryState || retryState === 'needs_review' && nextRetry) invalidMetadata = true;
        errors.push({message:excerpt(error.message),attempts:validCount(error.attempts) ? error.attempts : null,
          documents:validCount(error.documents) ? error.documents : null,next_retry_at:retryState === 'needs_review' ? null : nextRetry,state:retryState});
      }
    }
    const deferred = Number.isSafeInteger(data.deferred) && data.deferred >= 0 ? data.deferred : null;
    if (data.deferred !== undefined && deferred === null) invalidMetadata = true;
    const capture = data.capture && typeof data.capture === 'object' && !Array.isArray(data.capture) ? data.capture : null;
    const captureWarning = typeof capture?.warning === 'string' && capture.warning.trim() ? excerpt(capture.warning) : '';
    return {reviewed:Number.isSafeInteger(data.reviewed)&&data.reviewed>=0?data.reviewed:0,review_pending:Number.isSafeInteger(data.daily_review?.pending_versions)&&data.daily_review.pending_versions>=0?data.daily_review.pending_versions:null,processed:data.processed,skipped:data.skipped,clues:[...new Set(data.clues.map(id => id.toLowerCase()))],has_more:data.has_more,
      errors,deferred,capture_warning:captureWarning,capture_cached:capture?.fresh === false,invalid_metadata:invalidMetadata};
  } catch (error) { return null; }
}
function runResultSummary(run) {
  if (run.snapshot?.kind === 'github_clues') {
    try {
      const d=JSON.parse(run.output || '{}');
      if (!Number.isInteger(d.processed) || !Array.isArray(d.clues)) return '';
      const ids=d.clues.filter(id=>typeof id==='string' && /^[a-f0-9]{32}$/.test(id));
      return '<p class="run-result-summary">GitHub 原文增量 · 本轮分析 '+d.processed+' 份 · '+ids.length+' 条待核对线索'+(d.has_more?' · 仍有待分析或待重试资料':'')+'</p>'+
        '<div class="run-result-links">'+ids.map((id,i)=>'<a class="run-clue" href="/clues.html?clue='+encodeURIComponent(id)+'">线索 '+(i+1)+' ↗</a>').join('')+'</div>'+
        (Array.isArray(d.errors)?d.errors.slice(0,5).map(e=>'<p class="run-failure-summary">'+esc(String(e).slice(0,500))+'</p>').join(''):'');
    } catch { return ''; }
  }
  const specialized = run.snapshot?.kind === 'wechat_clues', data = wechatResult(run);
  let result = '';
  if (data) {
    const incremental = run.snapshot.wechat_mode === 'incremental';
    const incomplete = ['partial','failed','timeout','interrupted','cancelled'].includes(run.status) || data.errors.length || data.deferred > 0;
    const remaining = data.has_more === true ? '仍有待分析消息，尚未全部处理' : incomplete ? '本轮未全部完成' : data.has_more === false ?
      (data.processed === 0 && !data.skipped && !data.clues.length ? '本轮没有待分析消息' : '本轮待分析消息已处理完') : '剩余情况未记录';
    result = '<p class="run-result-summary">' + (incremental ? '持续增量（本机最近 7 天消息）' : '缓存试跑（本机最近 7 天缓存，最多 30 条）') + ' · 本轮分析 ' + data.processed + ' 条' +
      (data.reviewed ? ' · 复查 ' + data.reviewed + ' 个消息版本' : '') + (data.review_pending !== null ? ' · 待复查 ' + data.review_pending + ' 个版本' : '') +
      (data.skipped ? ' · 跳过 ' + data.skipped + ' 条' : '') + ' · ' + data.clues.length + ' 条待核对线索 · ' + remaining + '</p>';
    if (data.clues.length) {
      const link = (id,index) => '<a class="run-clue" href="/clues.html?clue=' + encodeURIComponent(id) + '">线索 ' + (index + 1) + ' ↗</a>';
      result += '<div class="run-result-links">' + data.clues.slice(0,6).map(link).join('') +
        (data.clues.length > 6 ? '<details class="run-clue-more"><summary>其余 ' + (data.clues.length - 6) + ' 条线索</summary>' + data.clues.slice(6).map((id,index) => link(id,index+6)).join('') + '</details>' : '') + '</div>';
    }
    if (data.capture_cached || data.capture_warning) result += '<p class="run-capture-warning">' +
      (data.capture_cached ? '本次未获得新采集结果，仅处理已有成功缓存。' : '采集提示：') + esc(data.capture_warning) + '</p>';
    if (data.deferred > 0) result += '<p class="run-retry-summary">待重试或核对 ' + data.deferred + ' 条消息。</p>';
    const errorRow = error => '<div class="run-error-item"><p class="run-failure-summary">分析错误' +
      (error.documents === null ? '' : ' · ' + error.documents + ' 条消息') + (error.attempts === null ? '' : ' · 已尝试 ' + error.attempts + ' 次') + '：' + esc(error.message) + '</p>' +
      (error.state === 'needs_review' ? '<p class="run-retry-summary">多次失败，待核对；立即运行可手动重试。</p>' :
        error.next_retry_at ? '<p class="run-retry-summary">可重试时间：' + esc(exactTime(error.next_retry_at.toISOString())) + '</p>' : '') + '</div>';
    if (data.errors.length) result += '<div class="run-errors">' + data.errors.slice(0,2).map(errorRow).join('') +
      (data.errors.length > 2 ? '<details class="run-errors-more"><summary>其余 ' + (data.errors.length - 2) + ' 条错误</summary>' + data.errors.slice(2).map(errorRow).join('') + '</details>' : '') + '</div>';
    if (data.invalid_metadata) result += '<p class="run-failure-summary">部分错误或重试信息格式无效，请打开详情核对；以上已保存结果保留。</p>';
    if (incomplete && !data.errors.length && !data.invalid_metadata && run.status !== 'running') result += '<p class="run-failure-summary">本轮未全部完成，已完成的进度与线索如上；具体原因请查看详情。</p>';
  } else if (specialized && run.status === 'succeeded') {
    result = '<p class="run-result-summary">本轮结果未能解析，请打开详情核对分析范围与线索。</p>';
  }
  if (['partial','failed','timeout','interrupted','cancelled'].includes(run.status) && !data && typeof run.output === 'string' && run.output.trim()) {
    const text = run.output.trim();
    let structured = /^[{\[]/.test(text);
    try { const parsed = JSON.parse(text); structured = parsed !== null && typeof parsed === 'object'; } catch (error) {}
    result += '<p class="run-failure-summary">' + (structured ? '运行结果格式异常，请打开详情核对已保存进度和错误。' : esc(excerpt(text))) + '</p>';
  }
  return result;
}
function frequency(task) {
  if (task.frequency === 'interval') {
    const minutes = Number(task.minutes);
    if (minutes >= 1440 && minutes % 1440 === 0) return minutes === 1440 ? '每 1 天' : '每 ' + minutes / 1440 + ' 天';
    if (minutes >= 60) return '每 ' + Math.floor(minutes / 60) + ' 小时' + (minutes % 60 ? ' ' + minutes % 60 + ' 分钟' : '');
    return '每 ' + minutes + ' 分钟';
  }
  const days = [...new Set(task.weekdays || [])].sort((a,b) => a-b);
  const prefix = days.length === 7 ? '每天' : String(days) === '0,1,2,3,4' ? '工作日' : String(days) === '5,6' ? '周末' : days.map(day => '周' + '一二三四五六日'[day]).join('、');
  return prefix + ' ' + (task.at || '—') + '（北京时间）';
}
function executor(task) {
  return task.executor_name || (task.kind === 'script' ? 'Bash 脚本' : task.kind === 'wechat_radar' ? '微信雷达分析（Pi）' : task.kind === 'wechat_clues' ? '微信线索分析（Pi）' : task.kind === 'github_clues' ? 'GitHub 线索分析（Pi）' : task.assistant_id === 'workbench' ? '工作台助手（Pi）' : '独立 Pi');
}
function modelName(task) {
  if (task.kind === 'script') return '';
  return task.model_name || state.models.find(model => model.profile_id === task.profile_id)?.display_name || '模型配置已移除';
}
function project(task) {
  return String(task.cwd || '').replace(/[\\/]+$/,'').split(/[\\/]/).pop() || '默认工作目录';
}
function message(text, tone = 'error') {
  $('#message').textContent = text || '';
  $('#message').dataset.tone = tone;
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求失败');
  return result;
}
const safely = (fn, clear = true) => async (...args) => {
  if (clear) message('');
  try { return await fn(...args); } catch (error) { message(error.message || '请求失败'); }
};
function visibleTasks() {
  const query = $('#search').value.trim().toLowerCase(), status = $('#task-filter').value;
  return state.tasks.filter(task => {
    const searchable = [task.name,task.body,executor(task),modelName(task),project(task)].join(' ').toLowerCase();
    return searchable.includes(query) && (status === 'all' || status === 'enabled' && task.enabled || status === 'paused' && !task.enabled || status === 'running' && task.running);
  });
}
function updateSelection() {
  const visible = visibleTasks(), count = visible.filter(task => selected.has(task.id)).length;
  $('#batch-toolbar').hidden = !batchMode;
  $('#batch-mode').setAttribute('aria-pressed', String(batchMode));
  $('#batch-mode').textContent = batchMode ? '退出批量选择' : '批量选择';
  $('#selection-count').textContent = '已选 ' + selected.size + ' 个';
  $('#select-all').checked = visible.length > 0 && count === visible.length;
  $('#select-all').indeterminate = count > 0 && count < visible.length;
  $('#select-all').disabled = batchBusy || !visible.length;
  for (const id of ['batch-resume','batch-pause']) $( '#' + id).disabled = batchBusy || !selected.size;
  $('#clear-selection').disabled = batchBusy || !selected.size;
  $('#batch-mode').disabled = batchBusy;
}
function renderTasks() {
  const openMenus = new Set([...$('#tasks').querySelectorAll('details[open]')].map(node => node.dataset.taskMenu));
  const active = document.activeElement;
  const focusId = active?.dataset?.selectTask, focusAction = active?.dataset?.action, focusTask = active?.dataset?.id;
  const tasks = visibleTasks();
  $('#task-count').textContent = tasks.length + ' / ' + state.tasks.length + ' 个任务';
  $('#tasks').innerHTML = tasks.length ? tasks.map(task => {
    const id = esc(task.id), last = task.last_run, model = modelName(task);
    const next = task.running ? '当前正在运行' : task.waiting_for_model ? '等待同模型任务完成' : !task.enabled ? '已暂停' : task.next_run ? '下次 ' + relative(task.next_run) : '已到期';
    return '<article class="task-row' + (batchMode ? ' has-selection' : '') + (selected.has(task.id) ? ' selected' : '') + '" data-task-id="' + id + '">' +
      (batchMode ? '<label class="task-selection"><input type="checkbox" data-select-task="' + id + '" aria-label="选择 ' + esc(task.name) + '" ' + (selected.has(task.id) ? 'checked ' : '') + (batchBusy ? 'disabled' : '') + '></label>' : '') +
      '<div class="task-info"><div class="task-title"><strong>' + esc(task.name) + '</strong>' + (task.running ? '<span class="pill state-pill" data-status="running">运行中</span>' : '') + '</div><div class="task-tags"><span class="pill">' + esc(executor(task)) + '</span>' + (model ? '<span class="pill model-pill" title="' + esc(model) + '">' + esc(model) + '</span>' : '') + '</div><div class="task-project">项目 · ' + esc(project(task)) + '</div></div>' +
      '<div class="task-schedule"><strong>' + esc(frequency(task)) + '</strong><small title="' + esc(exactTime(task.next_run)) + '">' + esc(next) + '</small></div>' +
      '<div class="task-last">' + (last ? '<span class="pill state-pill" data-status="' + esc(labels[last.status] ? last.status : 'unknown') + '">最近 · ' + esc(labels[last.status] || last.status) + '</span><small title="' + esc(exactTime(last.started_at)) + '">' + esc(when(last.started_at)) + ' · 耗时 ' + esc(duration(last)) + '</small>' : '<span class="muted">尚无运行记录</span>') + '</div>' +
      '<div class="task-actions"><button class="button" data-action="run" data-id="' + id + '" ' + (task.running || batchBusy ? 'disabled' : '') + '>立即运行</button><button class="button" data-action="' + (task.enabled ? 'pause' : 'resume') + '" data-id="' + id + '" ' + (batchBusy ? 'disabled' : '') + '>' + (task.enabled ? '暂停' : '启用') + '</button>' +
      '<details class="task-more" data-task-menu="' + id + '"' + (openMenus.has(task.id) ? ' open' : '') + '><summary aria-label="' + esc(task.name) + '的更多操作">更多</summary><div class="task-menu"><button data-action="detail" data-id="' + id + '">任务详情</button><button data-action="edit" data-id="' + id + '">编辑</button><button data-action="history" data-id="' + id + '">运行记录</button><button class="danger" data-action="delete" data-id="' + id + '" ' + (task.running || batchBusy ? 'disabled' : '') + '>删除任务</button></div></details></div></article>';
  }).join('') : '<div class="empty">' + (state.tasks.length ? '没有符合当前搜索或筛选的任务。' : '暂无定时任务。添加后由工作台后台按计划执行。') + '</div>';
  if (focusId) [...$('#tasks').querySelectorAll('[data-select-task]')].find(node => node.dataset.selectTask === focusId)?.focus({preventScroll:true});
  if (focusAction) [...$('#tasks').querySelectorAll('[data-action]')].find(node => node.dataset.action === focusAction && node.dataset.id === focusTask)?.focus({preventScroll:true});
  updateSelection();
}
function renderHealth() {
  const health = state.scheduler_health;
  $('#scheduler-status').textContent = health ? health.running ? '调度器运行中' : '调度器已停止' : '调度状态未知';
  $('#scheduler-status').dataset.state = health ? health.running ? 'running' : 'stopped' : 'unknown';
  $('#scheduler-status').title = health?.last_tick ? '最近检查 ' + exactTime(health.last_tick) : '尚无调度检查时间';
  const concurrency = state.concurrency;
  $('#concurrency-status').textContent = concurrency ? '并发 ' + concurrency.active + ' / ' + concurrency.max : '';
  $('#concurrency-status').title = concurrency ? '剩余可运行 ' + concurrency.available + ' 个任务' : '';
  $('#health-error').textContent = health?.last_error || state.last_error || '';
  $('#health-error').hidden = !$('#health-error').textContent;
}
async function refresh() {
  const request = ++listingRequest;
  let next;
  try { next = await api('/api/schedules'); }
  catch (error) { if (request === listingRequest) throw error; return; }
  if (request !== listingRequest) return;
  state = {...state,...next};
  for (const id of selected.keys()) if (!state.tasks.some(task => task.id === id)) selected.delete(id);
  renderTasks(); renderHealth();
  if (!state.cluesLoaded) {
    try { state.clue_threads = (await api('/api/clues?view=all')).items || []; state.cluesLoaded = true; }
    catch (error) { message('线索清单读取失败：' + error.message); }
  }
  if (!$('#run-view').hidden) await loadRuns();
}
function options() {
  const kind = field('kind').value, specialized = ['wechat_clues','github_clues','wechat_radar'].includes(kind), pi = kind !== 'script';
  const workbench = kind === 'pi' && field('assistant_id').value === 'workbench';
  if (specialized) {
    field('result_destination').value = 'runs'; field('allow_commands').checked = false;
    field('clue_id').value = ''; field('assistant_id').value = 'isolated';
  }
  field('result_destination').disabled = specialized;
  field('allow_commands').disabled = specialized;
  field('assistant_id').disabled = specialized || !pi;
  $('#pi-options').hidden = !pi;
  $('#assistant-select-label').hidden = specialized;
  $('#tool-group-options').hidden = !workbench;
  for (const checkbox of form.querySelectorAll('[name=tool_groups]')) {
    checkbox.disabled = !workbench;
    if (!workbench) checkbox.checked = false;
  }
  $('#body-label span').textContent = pi ? '任务提示词' : 'Bash 命令';
  field('body').placeholder = pi ? '说明任务目标、查询范围与希望得到的结果' : '填写完整命令、解释器和参数';
  $('#radar-since-label').hidden = kind !== 'wechat_radar';
  field('radar_since').required = kind === 'wechat_radar';
  $('#wechat-mode-label').hidden = kind !== 'wechat_clues';
  $('#wechat-review-label').hidden = kind !== 'wechat_clues' || field('wechat_mode').value !== 'incremental';
  $('#assistant-help').textContent = kind === 'wechat_radar' ? '选择模型，按起点之后的缓存持续分析热点、技术知识和本人画像。人数与群数由本机引用计算；原文可核对，不自动确认待办，不推送飞书。' : kind === 'github_clues' ? '先采集已配置的 GitHub 仓库，再用 Pi 增量分析；原文和更新历史保存在本机。仅写带引用的待核对线索。' : specialized ? '持续增量先完成本机采集与索引，再用所选模型分批分析；无每小时条数配额，按检查点续跑。只写带引用的待核对线索。' : workbench ? '每次单独执行所填任务，查询工具按勾选范围开放；记忆只读。本机命令仍需单独勾选授权。' : '每次独立 Pi 会话，不继承工作台工具；本机命令仍需单独勾选授权。';
  $('#interval-options').hidden = field('frequency').value !== 'interval';
  $('#daily-options').hidden = field('frequency').value !== 'daily';
  $('#result-options').hidden = field('result_destination').value !== 'clues';
}
function openEditor(task) {
  editing = task || null; form.reset(); $('#advanced-settings').open = false;
  field('radar_since').value = task?.radar_since ? new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(task.radar_since)).replace(' ','T') : new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())+'T00:00';
  $('#form-error').textContent = ''; $('#editor-title').textContent = task ? '编辑定时任务' : '添加定时任务';
  const defaults = {name:'',body:'',kind:'pi',wechat_review:task?.wechat_review ?? false,wechat_mode:task?.wechat_mode || (task ? 'once' : 'incremental'),assistant_id:task ? 'isolated' : 'workbench',cwd:state.default_cwd || '',frequency:'interval',minutes:60,at:'08:00',timeout:300,enabled:false,allow_commands:false,result_destination:'runs',result_category:'other',clue_id:''};
  const values = {...defaults,...task,assistant_id:task ? task.assistant_id || 'isolated' : 'workbench'};
  for (const [key,value] of Object.entries(values)) {
    if (key === 'tool_groups' || key === 'profile_id' || key === 'radar_since') continue;
    const element = field(key);
    if (!element || element.nodeType !== 1) continue;
    if (element.type === 'checkbox') element.checked = !!value;
    else if (key === 'ends_at') element.value = dateOf(value) ? new Date(dateOf(value).getTime()+8*3600000).toISOString().slice(0,16) : '';
    else element.value = value ?? '';
  }
  const groups = task?.tool_groups || [];
  for (const checkbox of form.querySelectorAll('[name=tool_groups]')) checkbox.checked = groups.includes(checkbox.value);
  field('profile_id').innerHTML = state.models.filter(model => model.configured).map(model => '<option value="' + esc(model.profile_id) + '">' + esc(model.display_name) + '</option>').join('');
  const profile = task?.profile_id || state.default_profile_id;
  if (profile && !state.models.some(model => model.configured && model.profile_id === profile)) field('profile_id').insertAdjacentHTML('beforeend','<option value="' + esc(profile) + '">原模型已不可用，请重新选择</option>');
  if (!field('profile_id').options.length) field('profile_id').innerHTML = '<option value="">暂无可用模型</option>';
  if (profile) field('profile_id').value = profile;
  field('clue_id').innerHTML = '<option value="">这个任务自己的结果线索</option>' + state.clue_threads.map(clue => '<option value="' + esc(clue.id) + '">' + esc(clue.title) + '</option>').join('');
  if (task?.clue_id) {
    if (!state.clue_threads.some(clue => clue.id === task.clue_id)) field('clue_id').insertAdjacentHTML('beforeend','<option value="' + esc(task.clue_id) + '">现有关联线索</option>');
    field('clue_id').value = task.clue_id;
  }
  $('#weekdays').innerHTML = [...'一二三四五六日'].map((day,index) => '<label><input type="checkbox" value="' + index + '" ' + ((task?.weekdays || [0,1,2,3,4,5,6]).includes(index) ? 'checked' : '') + '>' + day + '</label>').join('');
  options(); $('#editor').showModal();
}
function metadata(target, pairs) {
  $(target).innerHTML = pairs.map(([label,value]) => '<dt>' + esc(label) + '</dt><dd>' + esc(value ?? '—') + '</dd>').join('');
}
function openTaskDetail(task) {
  $('#task-detail-title').textContent = task.name;
  metadata('#task-meta', [['助手',executor(task)],['模型',modelName(task) || '—'],['频率',frequency(task)],['下次执行',exactTime(task.next_run)],['工作目录',task.cwd],['工具范围',(task.tool_groups || []).map(group => toolNames[group] || group).join('、') || '无'],['本机命令',task.allow_commands ? '已允许' : '未允许'],['运行上限',task.timeout + ' 秒'],...(task.kind==='wechat_radar'?[['分析起点',exactTime(task.radar_since)],['微信范围','按会话增量续跑；无每小时条数配额，原文和版本核对后保存']]:[]),...(task.kind==='wechat_clues'?[['会话日复查',task.wechat_review?'已启用；对照已有线索，未完成范围下轮续查':'未启用'],['微信范围',task.wechat_mode==='incremental'?'先采集与索引，再增量分析；无每小时条数配额':'缓存试跑，最多30条']]:[]),['截止时间',exactTime(task.ends_at)],['结果归属',['wechat_clues','github_clues','wechat_radar'].includes(task.kind) ? '原文引用与增量分析的专用流程' : task.result_destination === 'clues' ? '线索中心（待核对）' : '仅运行记录'],['版本',task.revision]]);
  $('#task-body').textContent = task.body; $('#task-detail').showModal();
}
function renderRuns(result) {
  const currentTask = state.tasks.find(task => task.id === filterTask);
  $('#run-filter-note').textContent = filterTask ? '任务 · ' + (currentTask?.name || '指定任务') : '全部任务的运行记录';
  $('#all-runs').hidden = !filterTask;
  $('#runs').innerHTML = runs.length ? runs.map(run => {
    const snapshot = run.snapshot || {}, status = labels[run.status] || run.status, id = esc(run.id);
    const model = snapshot.model_name || snapshot.model || '', badge = snapshot.executor_name || executor(snapshot);
    return '<article class="run-row"><div class="task-info run-info"><div class="task-title"><strong>' + esc(snapshot.name || '未命名任务') + '</strong><span class="pill state-pill" data-status="' + esc(labels[run.status] ? run.status : 'unknown') + '">' + esc(status) + '</span></div><div class="task-tags"><span class="pill">' + esc(badge) + '</span>' + (model ? '<span class="pill model-pill">' + esc(model) + '</span>' : '') + '</div><p title="' + esc(exactTime(run.started_at)) + '">' + esc(when(run.started_at)) + ' · 耗时 ' + esc(duration(run)) + ' · ' + triggerLabel(run) + ' · 版本 ' + esc(snapshot.revision || '—') + (run.exit_code !== null && run.exit_code !== undefined ? ' · 退出码 ' + esc(run.exit_code) : '') + '</p>' +
      runResultSummary(run) +
      (run.publication_error ? '<p class="run-publication-error">线索写入失败：' + esc(run.publication_error) + '</p>' : '') +
      (run.clue_id ? '<a class="run-clue" href="/clues.html?clue=' + encodeURIComponent(run.clue_id) + '">查看结果线索 ↗</a>' : '') + '</div><div class="run-actions">' +
      (run.status === 'succeeded' && !['wechat_clues','github_clues','wechat_radar'].includes(snapshot.kind) && !run.clue_id && typeof run.output === 'string' && run.output.trim() ? '<button class="button" data-publish="' + id + '">收集到线索中心</button>' : '') +
      (run.status === 'running' ? '<button class="button" data-cancel="' + id + '">停止</button>' : '') + '<button class="button" data-run="' + id + '">详情</button></div></article>';
  }).join('') : '<div class="empty">' + ($('#search').value.trim() || $('#run-status').value || filterTask ? '没有符合当前搜索或筛选的运行记录。' : '暂无运行记录。') + '</div>';
  $('#run-count').textContent = '共 ' + (result.total ?? runs.length) + ' 条记录';
  $('#page-label').textContent = '第 ' + runPage + ' 页';
  $('#previous-page').disabled = runPage <= 1;
  $('#next-page').disabled = !result.has_more;
}
async function loadRuns(page = runPage) {
  const request = ++runsRequest;
  const parameters = new URLSearchParams({task_id:filterTask || '',q:$('#search').value.trim(),status:$('#run-status').value,page:String(page),page_size:'50'});
  let result;
  try { result = await api('/api/schedules/runs?' + parameters.toString()); }
  catch (error) { if (request === runsRequest) throw error; return; }
  if (request !== runsRequest) return;
  runs = result.runs || []; runPage = Number(result.page) || page;
  renderRuns(result);
}
function tab(showRuns) {
  $('#task-view').hidden = showRuns; $('#run-view').hidden = !showRuns;
  $('#task-tab').classList.toggle('primary',!showRuns); $('#run-tab').classList.toggle('primary',showRuns);
  $('#task-tab').setAttribute('aria-pressed',String(!showRuns)); $('#run-tab').setAttribute('aria-pressed',String(showRuns));
  $('#search').placeholder = showRuns ? '搜索任务内容或运行输出' : '搜索任务、助手或模型';
}
function openRun(run) {
  const snapshot = run.snapshot || {};
  metadata('#run-meta',[['任务',snapshot.name],['助手',snapshot.executor_name || executor(snapshot)],['模型',snapshot.model_name || snapshot.model || '—'],['状态',labels[run.status] || run.status],['开始时间',exactTime(run.started_at)],['结束时间',exactTime(run.finished_at)],['耗时',duration(run)],['工作目录',snapshot.cwd],['版本',snapshot.revision],['退出码',run.exit_code ?? '—']]);
  $('#run-body').textContent = snapshot.body || '';
  $('#run-output').textContent = run.output || (run.status === 'running' ? '正在执行，完成后记录输出。' : '无输出');
  $('#run-detail').showModal();
}
async function batch(action) {
  if (!selected.size || batchBusy) return;
  batchBusy = true; renderTasks();
  const tasks = [...selected].map(([id,revision]) => ({id,revision}));
  try {
    await api('/api/schedules/batch',{action,tasks});
    selected.clear(); message(action === 'pause' ? '所选任务已暂停；正在运行的任务未中断。' : '所选任务已启用。','success');
    await refresh();
  } catch (error) {
    try {
      const previousSelection = selected.size;
      await refresh();
      for (const [id,revision] of selected) {
        if (state.tasks.find(task => task.id === id)?.revision !== revision) selected.delete(id);
      }
      if (selected.size < previousSelection) error.message += '；已移除过期选择，请重新选择。';
    } catch (refreshError) { error.message += '；刷新失败：' + refreshError.message; }
    throw error;
  } finally { batchBusy = false; renderTasks(); }
}
$('#new-task').onclick = () => openEditor();
$('#refresh').onclick = safely(refresh);
$('#search').oninput = () => {
  if ($('#run-view').hidden) return renderTasks();
  ++runsRequest; clearTimeout(searchTimer);
  searchTimer = setTimeout(safely(() => loadRuns(1)),180);
};
$('#task-filter').onchange = renderTasks;
$('#run-status').onchange = safely(() => loadRuns(1));
$('#task-tab').onclick = () => { ++runsRequest; clearTimeout(searchTimer); tab(false); renderTasks(); };
$('#run-tab').onclick = safely(async () => { tab(true); filterTask = null; await loadRuns(1); });
$('#all-runs').onclick = safely(async () => { filterTask = null; await loadRuns(1); });
$('#previous-page').onclick = safely(async () => { if (runPage > 1) await loadRuns(runPage - 1); });
$('#next-page').onclick = safely(() => loadRuns(runPage + 1));
$('#batch-mode').onclick = () => { if (batchBusy) return; batchMode = !batchMode; if (!batchMode) selected.clear(); renderTasks(); };
$('#select-all').onchange = event => { for (const task of visibleTasks()) if (event.target.checked) { if (!selected.has(task.id)) selected.set(task.id,task.revision); } else selected.delete(task.id); renderTasks(); };
$('#clear-selection').onclick = () => { selected.clear(); renderTasks(); };
$('#batch-resume').onclick = safely(() => batch('resume'));
$('#batch-pause').onclick = safely(() => batch('pause'));
for (const name of ['kind','frequency','result_destination','assistant_id','wechat_mode']) field(name).onchange = options;
for (const button of document.querySelectorAll('[data-close]')) button.onclick = () => button.closest('dialog').close();
form.onsubmit = async event => {
  event.preventDefault();
  const submit = form.querySelector('[type=submit]'); submit.disabled = true; $('#form-error').textContent = '';
  try {
    const payload = {};
    for (const name of ['name','body','kind','cwd','frequency','at','profile_id','result_destination','result_category','clue_id']) payload[name] = field(name).value;
    for (const name of ['minutes','timeout']) payload[name] = Number(field(name).value);
    for (const name of ['enabled','allow_commands']) payload[name] = field(name).checked;
    if (payload.kind === 'wechat_radar') payload.radar_since = field('radar_since').value + ':00+08:00';
    if (payload.kind === 'wechat_clues') {payload.wechat_mode = field('wechat_mode').value; payload.wechat_review = payload.wechat_mode === 'incremental' && field('wechat_review').checked;}
    payload.weekdays = [...$('#weekdays').querySelectorAll('input:checked')].map(element => Number(element.value));
    payload.ends_at = field('ends_at').value ? field('ends_at').value + ':00+08:00' : null;
    payload.assistant_id = payload.kind === 'pi' ? field('assistant_id').value : 'isolated';
    payload.tool_groups = payload.kind === 'pi' && payload.assistant_id === 'workbench' ? [...form.querySelectorAll('[name=tool_groups]:checked')].map(element => element.value) : [];
    if (payload.kind !== 'script' && !state.models.some(model => model.configured && model.profile_id === payload.profile_id)) throw new Error('请选择已配置的聊天模型；原模型不可用时需重新选择。');
    if (editing) Object.assign(payload,{id:editing.id,revision:editing.revision});
    await api('/api/schedules/save',payload);
    $('#editor').close(); await refresh();
  } catch (error) { $('#form-error').textContent = error.message || '保存失败'; }
  finally { submit.disabled = false; }
};
$('#tasks').onchange = event => {
  const checkbox = event.target.closest('[data-select-task]');
  if (!checkbox || batchBusy) return;
  const task = state.tasks.find(task => task.id === checkbox.dataset.selectTask);
  if (!task) return;
  if (checkbox.checked) selected.set(task.id,task.revision); else selected.delete(task.id);
  checkbox.closest('.task-row').classList.toggle('selected',checkbox.checked); updateSelection();
};
$('#tasks').onclick = safely(async event => {
  const button = event.target.closest('[data-action]');
  if (!button || button.disabled) return;
  const task = state.tasks.find(task => task.id === button.dataset.id); if (!task) return;
  const action = button.dataset.action;
  if (action === 'edit') return openEditor(task);
  if (action === 'detail') return openTaskDetail(task);
  if (action === 'history') { tab(true); filterTask = task.id; return loadRuns(1); }
  if (batchBusy) return;
  if (action === 'delete' && !confirm('删除此任务？运行记录仍会保留。')) return;
  button.disabled = true;
  try { await api('/api/schedules/' + encodeURIComponent(task.id) + '/' + action,{}); await refresh(); }
  finally { if (button.isConnected) button.disabled = false; }
});
$('#runs').onclick = safely(async event => {
  const button = event.target.closest('button'); if (!button || button.disabled) return;
  if (button.dataset.run) {
    const listed = runs.find(run => run.id === button.dataset.run);
    const actual = await api('/api/schedules/run/' + encodeURIComponent(button.dataset.run));
    return openRun(actual?.snapshot ? actual : listed);
  }
  button.disabled = true;
  try {
    if (button.dataset.publish) {
      const result = await api('/api/schedules/' + encodeURIComponent(button.dataset.publish) + '/publish',{});
      message(result.duplicate ? '相同结果已收集，不重复建立线索。' : result.skipped ? result.reason : '已收集为待核对线索。','success');
      state.cluesLoaded = false; await loadRuns();
    }
    if (button.dataset.cancel) { await api('/api/schedules/' + encodeURIComponent(button.dataset.cancel) + '/cancel',{}); await loadRuns(); }
  } finally { if (button.isConnected) button.disabled = false; }
});
safely(async () => {
  await refresh();
  const identity = new URL(location.href).searchParams.get('run');
  if (identity) { tab(true); await loadRuns(); openRun(await api('/api/schedules/run/' + encodeURIComponent(identity))); }
})();
setInterval(() => { if (!document.hidden) safely(refresh,false)(); },10000);
