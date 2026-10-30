/* 全站左侧主导航。条目与顺序只有这一份数据源；页面里 HTML 的静态清单是无 JS 时的兜底。
   页面专属的二级导航（线索视图、订阅号、卡片视图、合集、图片/视频控制）不在这里，保持各页自己的。 */
(() => {
  const ITEMS = [
    { id: 'today', href: '/today.html', icon: '◷', label: '今日' },
    { id: 'library', href: '/library.html', icon: '▥', label: '素材库' },
    { id: 'works', href: '/', icon: '▤', label: '创作台' },
    { id: 'publish', href: '/publish.html', icon: '↗', label: '发布复盘' },
    { id: 'deals', href: '/deals.html', icon: '▣', label: '商单' },
    { id: 'settings', href: '/settings.html', icon: '⚙', label: '设置' },
    { id: 'image', href: '/lab.html?kind=image', icon: '▧', label: '图片生成', group: 'tools' },
    { id: 'video', href: '/lab.html?kind=video', icon: '▷', label: '视频生成', group: 'tools' },
    { id: 'clues', href: '/clues.html', icon: '⌁', label: '线索中心', group: 'legacy' },
    { id: 'intel', href: '/intel.html', icon: '☰', label: '情报采集', group: 'legacy' },
    { id: 'github', href: '/github.html', icon: '⑂', label: 'GitHub 来源', group: 'legacy' },
    { id: 'schedules', href: '/schedules.html', icon: '◴', label: '定时任务', group: 'legacy' },
    { id: 'feedback', href: '/feedback.html', icon: '◈', label: '发布反馈', group: 'legacy' },
    { id: 'x-official', href: '/x-official.html', icon: 'Ⅹ', label: 'X 官方接口', group: 'legacy' },
    { id: 'knowledge', href: '/knowledge.html', icon: '▦', label: '知识库', group: 'legacy' },
    { id: 'intake', href: '/intake.html', icon: '◇', label: '创意回收', group: 'legacy' },
    { id: 'cards', href: '/cards.html', icon: '✦', label: '提示词卡', group: 'legacy' },
  ];

  // 阅读页（/read/…）没有侧栏，但同一份数据仍把「创作台」当作当前项。
  function pathKey(item) {
    return item.href.split('?')[0];
  }
  function currentId() {
    let path = location.pathname || '/';
    if (path === '/index.html') path = '/';
    if (path.startsWith('/read/')) path = '/';
    if (path !== '/' && !path.endsWith('.html')) path += (/\/$/.test(path) ? '' : '.html');
    const here = ITEMS.filter(item => pathKey(item) === path);
    if (!here.length) return null;
    if (here.length > 1) {
      // lab 页按 query 区分当前项：只有 ?kind=video 算「视频生成」，其余按「图片生成」。
      const kind = new URLSearchParams(location.search).get('kind');
      const wanted = kind === 'video' ? 'video' : 'image';
      return here.some(item => item.id === wanted) ? wanted : here[0].id;
    }
    return here[0].id;
  }

  const nav = document.getElementById('workspace-nav');
  if (!nav) return;
  const selected = currentId();
  nav.classList.add('workspace-nav');
  nav.setAttribute('aria-label', nav.getAttribute('aria-label') || '工作台栏目');
  nav.textContent = '';
  const groups = {};
  for (const item of ITEMS) {
    if (item.group && !groups[item.group]) {
      const group = document.createElement('details');
      group.dataset.navGroup = item.group;
      group.open = ITEMS.some(p => p.group === item.group && p.id === selected);
      const title = document.createElement('summary');
      title.textContent = item.group === 'tools' ? '工具' : '旧版';
      group.append(title); nav.append(group); groups[item.group] = group;
    }
    const link = document.createElement('a');
    link.href = item.href;
    link.dataset.navItem = item.id;
    link.textContent = item.icon + ' ' + item.label;
    if (item.id === selected) {
      link.className = 'selected';
      link.setAttribute('aria-current', 'page');
    }
    (item.group ? groups[item.group] : nav).appendChild(link);
  }
})();
