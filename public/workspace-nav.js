/* 全站左侧主导航。条目与顺序只有这一份数据源；页面里 HTML 的静态清单是无 JS 时的兜底。
   页面专属的二级导航（线索视图、订阅号、卡片视图、合集、图片/视频控制）不在这里，保持各页自己的。 */
(() => {
  const ITEMS = [
    { id: 'works', href: '/', icon: '▤', label: '我的作品' },
    { id: 'image', href: '/lab.html?kind=image', icon: '▧', label: '图片生成' },
    { id: 'video', href: '/lab.html?kind=video', icon: '▷', label: '视频生成' },
    { id: 'knowledge', href: '/knowledge.html', icon: '▥', label: '知识库' },
    { id: 'intake', href: '/intake.html', icon: '◈', label: '创意回收' },
    { id: 'cards', href: '/cards.html', icon: '✦', label: '提示词卡' },
    { id: 'clues', href: '/clues.html', icon: '⌁', label: '线索中心' },
    { id: 'events', href: '/events.html', icon: '◷', label: '事项与日程' },
    { id: 'feedback', href: '/feedback.html', icon: '↗', label: '发布反馈' },
    { id: 'deals', href: '/deals.html', icon: '▣', label: '商单管理' },
    { id: 'intel', href: '/intel.html', icon: '☰', label: '情报采集' },
    { id: 'github', href: '/github.html', icon: '⑂', label: 'GitHub 来源' },
    { id: 'schedules', href: '/schedules.html', icon: '◴', label: '定时任务' },
  ];

  // 阅读页（/read/…）没有侧栏，但同一份数据仍把「我的作品」当作当前项。
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
  for (const item of ITEMS) {
    const link = document.createElement('a');
    link.href = item.href;
    link.dataset.navItem = item.id;
    link.textContent = item.icon + ' ' + item.label;
    if (item.id === selected) {
      link.className = 'selected';
      link.setAttribute('aria-current', 'page');
    }
    nav.appendChild(link);
  }
})();
