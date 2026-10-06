/* Keep the existing editor and conversation DOM alive while changing the layout. */
(() => {
  const body = document.body;
  const panels = {
    navigation: body.querySelector(':scope > .sidebar'),
    content: body.querySelector(':scope > .main'),
    assistant: document.getElementById('assistant-panel'),
  };
  if (!panels.navigation || !panels.content) return;
  const names = {navigation: '左侧导航', content: '内容区', assistant: 'AI 助手'};
  const symbols = {navigation: '☰', content: '▤', assistant: '✦'};
  const storageKey = 'studio-panel-layout-v1';
  const narrow = window.matchMedia('(max-width: 760px)');
  const collapsed = {navigation: narrow.matches, content: false, assistant: !panels.assistant || panels.assistant.hidden};
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    // Only navigation width is a durable preference. Hiding the current
    // content must never hide every other page on the next navigation.
    for (const key of ['navigation']) {
      if (typeof saved?.[key] === 'boolean') collapsed[key] = saved[key];
    }
    if (narrow.matches) collapsed.navigation = true;
  } catch {}
  let maximized = null;
  let assistantWasOpen = false;
  let previousFocus = null;
  const rails = {}, controls = {};
  const icon = expanded => expanded
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3v6H3m12-6v6h6M9 21v-6H3m12 6v-6h6"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg>';
  function button(label, markup, action) {
    const node = document.createElement('button');
    node.type = 'button'; node.className = 'panel-control';
    node.title = label; node.setAttribute('aria-label', label);
    node.innerHTML = markup; node.addEventListener('click', action);
    return node;
  }
  function save() {
    try { localStorage.setItem(storageKey, JSON.stringify({navigation: collapsed.navigation})); } catch {}
  }
  function widthLimit() {
    if (maximized === 'assistant' || collapsed.content || narrow.matches) return Math.max(320, window.innerWidth - (maximized ? 0 : 48));
    const nav = collapsed.navigation ? 48 : window.innerWidth <= 1150 ? 220 : 254;
    return Math.max(320, window.innerWidth - nav - 320);
  }
  function render() {
    const reading = body.classList.contains('reader');
    body.classList.toggle('studio-panels', !reading);
    body.dataset.hasAssistant = String(!!panels.assistant);
    body.style.setProperty('--assistant-size-limit', widthLimit() + 'px');
    const handle = document.getElementById('assistant-resize');
    if (handle) {
      handle.setAttribute('aria-valuemax', String(widthLimit()));
      const preferred = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--assistant-width')) || 440;
      handle.setAttribute('aria-valuenow', String(Math.round(Math.min(preferred, widthLimit()))));
    }
    if (maximized && !reading) body.dataset.maximized = maximized;
    else delete body.dataset.maximized;
    for (const [key, panel] of Object.entries(panels)) {
      if (!panel) continue;
      const folded = collapsed[key] && maximized !== key;
      body.dataset[key + 'Collapsed'] = String(folded);
      // A maximized sibling is inert, but retains its DOM, input and scroll position.
      panel.hidden = reading ? key !== 'content' : folded;
      panel.inert = reading ? key !== 'content' : folded || !!(maximized && maximized !== key);
      rails[key].hidden = reading || !!maximized || !folded;
      controls[key].wrap.hidden = reading;
      controls[key].maximize.innerHTML = icon(maximized === key);
      const label = (maximized === key ? '还原' : '最大化') + names[key];
      controls[key].maximize.title = label;
      controls[key].maximize.setAttribute('aria-label', label);
      controls[key].maximize.setAttribute('aria-pressed', String(maximized === key));
      rails[key].querySelector('[data-panel-expand]').setAttribute('aria-expanded', String(!folded));
    }
    const history = document.getElementById('history');
    if (history) history.inert = !!maximized || collapsed.content || reading;
    document.dispatchEvent(new CustomEvent('studio-layout-change'));
  }
  function focusVisible(key) {
    const target = collapsed[key] && !maximized
      ? rails[key].querySelector('[data-panel-expand]')
      : controls[key].maximize;
    target?.focus({preventScroll: true});
  }
  function restore() {
    if (!maximized) return;
    const key = maximized;
    maximized = null;
    if (key === 'assistant' && !assistantWasOpen) {
      window.closeAssistant?.();
    }
    render();
    if (previousFocus?.isConnected && !previousFocus.closest('[hidden], [inert]')) previousFocus.focus({preventScroll: true});
    else focusVisible(key);
    previousFocus = null;
  }
  function fold(key, value) {
    if (!panels[key]) return;
    if (maximized) restore();
    if (key === 'assistant') {
      if (value) window.closeAssistant?.();
      else window.openAssistant?.();
    } else collapsed[key] = value;
    body.classList.remove('library-open');
    save(); render(); focusVisible(key);
  }
  function maximize(key) {
    if (maximized === key) { restore(); return; }
    if (maximized) restore();
    previousFocus = document.activeElement;
    assistantWasOpen = !collapsed.assistant;
    maximized = key;
    // Loading a closed assistant is necessary, but its prior collapsed state is restored afterwards.
    if (key === 'assistant' && collapsed.assistant) window.openAssistant?.();
    render(); focusVisible(key);
  }
  function resetLayout() {
    if (maximized) restore();
    collapsed.navigation = narrow.matches;
    collapsed.content = false;
    body.classList.remove('library-open');
    save(); render(); focusVisible('content');
  }
  for (const [key, panel] of Object.entries(panels)) {
    if (!panel) continue;
    panel.dataset.studioPanel = key;
    if (!panel.id) panel.id = 'studio-' + key + '-panel';
    const wrap = document.createElement('div');
    wrap.className = 'panel-controls'; wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', names[key] + '布局');
    const collapse = button('收起' + names[key], '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>', () => fold(key, true));
    collapse.setAttribute('aria-controls', panel.id);
    const max = button('最大化' + names[key], icon(false), () => maximize(key));
    wrap.append(collapse, max); controls[key] = {wrap, maximize: max};
    wrap.append(button('恢复默认布局', '↺', resetLayout));
    const header = key === 'assistant' ? panel.querySelector('.assistant-head') : key === 'content' ? panel.querySelector('.topbar') : null;
    if (header) header.append(wrap); else panel.prepend(wrap);
    const rail = document.createElement('aside');
    rail.className = 'panel-rail'; rail.dataset.panelRail = key;
    rail.setAttribute('aria-label', names[key] + '已收起');
    const expand = button('展开' + names[key], symbols[key], () => fold(key, false));
    expand.dataset.panelExpand = key; expand.setAttribute('aria-controls', panel.id);
    const label = document.createElement('span'); label.className = 'panel-rail-label'; label.textContent = names[key];
    const railMax = button('最大化' + names[key], icon(false), () => maximize(key));
    rail.append(expand, label, railMax); rail.hidden = true;
    rail.append(button('恢复默认布局', '↺', resetLayout));
    panel.after(rail); rails[key] = rail;
  }
  // Replace the old assistant close control with the same collapse affordance as the other panels.
  const oldClose = document.getElementById('assistant-close');
  if (oldClose) oldClose.hidden = true;
  const libraryToggle = document.getElementById('library-toggle');
  if (libraryToggle) libraryToggle.onclick = () => fold('navigation', !collapsed.navigation);
  window.StudioPanels = {
    assistantChanged(open) {
      collapsed.assistant = !open;
      if (!open && maximized === 'assistant') { maximized = null; previousFocus = null; }
      if (open && maximized && maximized !== 'assistant') { maximized = null; previousFocus = null; }
      if (open && narrow.matches) collapsed.navigation = true;
      render();
    },
    readingChanged: render,
    assistantWidthLimit: widthLimit,
    contentRequested() {
      if (maximized === 'navigation') restore();
      collapsed.content = false;
      if (narrow.matches) collapsed.navigation = true;
      save(); render();
    },
  };
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented || !maximized) return;
    if (document.querySelector('dialog[open], #model-picker-menu:not([hidden]), #chat-tool-menu:not([hidden])')) return;
    event.preventDefault(); restore();
  });
  document.addEventListener('click', event => {
    if (maximized === 'content' && event.target.closest('#history-toggle')) restore();
  });
  narrow.addEventListener('change', () => { if (narrow.matches) collapsed.navigation = true; render(); });
  window.addEventListener('resize', render);
  save(); // Discard legacy global content-collapse state after migration.
  render();
})();
