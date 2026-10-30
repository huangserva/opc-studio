# 今日页小单 T5：火爆标记 + 右上角来源图标 + 接入 linux.do（实施合同）

派单人：主控 term_b884acc7（run_17dc13f37a10）；实施：Codex term_02017284；复核：Codex term_57dd3162（不同窗口）。
仓库 /Users/huangzongning/development/article-studio，Python 用 ../article-importer/.venv/bin/python。8766 服务由 launchd 管：`launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`；查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`，禁止 launchctl print / ps eww / env / cat plist（会把 Key 打进输出）。

## 黄总原话
「我想加一个火爆，100K阅读才叫做火爆。还有右上角加一个图标，就是来源。比如grok bot，AIsa等。并且我们本来有linux do的来源，你知道吗？我看到没加进来」

## 现状（主控已核实）
- 今日页：public/today.html/js/css，组件 public/components/item-card.js（sourceBadges 目前是卡片 meta 行最左的字母 G/A/V）、list-reader.js；后端 today_view.py（_tier：爆款/起势/低粉爆款/其他；METRICS 含 views；视频条目 views=plays）。
- linux.do 从未接入：原 WorkBuddy 任务「linuxdo AI 热帖监控」已暂停、其目录不存在，~/workbuddy-outbox 为空（coordination/audit-workbuddy-tasks-report.md）。
- 主控实测（2026-10-30 早）：`https://linux.do/top.json?period=daily`、`/latest.json` 直连和走 7897 代理均 403（Cloudflare 挑战页）；`https://linux.do/top.rss?period=daily` 与 `/latest.rss` 直连 200，30 条 item，含 title、dc:creator、category、link、pubDate、description（全文 HTML，末尾带「N 个帖子 - M 位参与者」）。RSS **没有浏览数**。

## 要做
1. **火爆**：浏览数（X 的 views；视频的 plays，即 metrics.views）≥ 100000 的条目标「火爆」。
   - 卡片和右侧阅读器都显示「火爆」文字徽标（不用 emoji），与现有档位（爆款等）并存，不改现有 _tier 规则。
   - 热度下拉加「火爆」选项（筛 metrics.views ≥ 100000，可与来源/时间范围组合）；后端查询参数沿用现有校验风格（白名单）。
   - 浏览数缺失（如 linux.do）一律不算火爆，不当作 0 以外的值猜测。
   - 阈值 100000 定义为一个常量，前后端一致（后端算出布尔字段如 `hot` 下发，前端不自己再算阈值）。
2. **右上角来源图标**：每张卡片右上角显示来源图标（Grok Bot、AIsa、视频榜、linux.do），悬停有中文全名 title 和 aria-label；多来源并排；阅读器标题区同样显示。替换 meta 行里的字母徽标（来源下拉不变）。图标用本地内联 SVG 或本地静态文件，不加载任何外部图片/字体。窄屏（390px）不遮挡标题和动作栏。
3. **接入 linux.do 来源**（来源代号 `L`，中文名 linux.do）：
   - 新模块（建议 `linuxdo_source.py`）抓 `https://linux.do/top.rss?period=daily`：超时 ≤10s，响应体上限（如 2 MiB），不带 Cookie、不登录、不绕 Cloudflare；遇到 403/挑战页/解析失败记来源状态为异常并保留上次快照。
   - 快照缓存到 workspace 下（如 workspace/linuxdo/top-daily.json，先写 .tmp 再改名），同一小时内最多刷新一次；今日页首屏不得因抓取阻塞（首屏 ≤1 秒的既有指标不能退化）——用后台刷新或独立刷新入口，自行选择最简单可靠的做法并在报告说明。
   - 字段映射：title、author=dc:creator、published_at=pubDate、topic=category、url=link、正文=description 经现有 html_sanitize.py 清洗后的文本/安全 HTML（必须防 XSS：script、on* 属性、javascript: 链接）；metrics.replies 取「N 个帖子」减 1 或按你核实的语义，并在报告写清；views=None。
   - 进入今日页统一列表、来源下拉加「linux.do」、来源状态栏显示它的状态；准/不准/存/拿去写对它同样可用（存进素材库、拿去写带全文）。
   - 去重：同一 link 只出现一次。

## 允许改动
today_view.py、新建 linuxdo_source.py、public/today.html、public/today.js、public/today.css、public/components/item-card.js、public/components/list-reader.js（确有需要才改）、新增/修改 tests/ 下今日页与 linux.do 相关测试及其 e2e。如必须改 server.py 装配，只限一行级接线并在报告说明。其他文件不动；视频实验室保护边界（public/lab-test.*、generation/*、server.py 视频/generation 路由、tests/test_*lab*）绝对不动。

## 该工人已知弱点与必测反例（交付前逐条自测，报告逐条给结果）
- G1 外部源安全与资源上限（历史 AIsa 线教训）：用夹具测 ① 超大响应被截断/拒绝 ② 超时 ③ 403 挑战页 HTML ④ 非法 XML ⑤ description 含 `<script>`、`onerror=`、`javascript:` 链接——页面里均不得执行或出现可点的 javascript: 链接；异常时来源状态显示异常且旧快照仍可读。
- G2 凭证与日志：任何输出、日志、快照里不得出现 Key/Cookie；不得用 launchctl print 等命令。
- G3 阈值边界：views=99999 不火爆、100000 火爆、None 不火爆、字符串数字（如 "100000"）按现有 grok.number 规则处理；视频 plays=150000 火爆。
- G4 测试网络隔离：单元测试与 e2e 一律用夹具，不得访问真实 linux.do（沿用现有 test network guard）。
- G5 首屏性能：linux.do 抓取慢（夹具里人为延迟 8 秒）时，今日页首屏仍 ≤1 秒出列表。
- G6 既有边界：today e2e 反例 15 在北京时间 0–8 点会因相对时间夹具失败（既有，证据 /tmp/impl-p4-4-2-after/today-existing-boundary.json），若你的改动碰到该夹具可顺手改成固定时间，不碰就不改，报告注明。

## 验收
- 定向测试全绿；全量 Python 和 UI 测试跑前向主控登记（回复我即可），基线：Python 3569 项 0F/1E/7S（E=受保护 video_lab），UI 719P/3F/1S（3F=受保护 lab-test），不得新增失败。
- 重启 8766 确认新 PID，用**隔离 Playwright**（独立 profile、随机端口或真实 8766 只读访问）走真实路径：打开 /today.html → 看到右上角来源图标 → 热度选「火爆」只剩 ≥10 万浏览条目 → 来源选 linux.do 能看到真实条目（真实网络这一步允许访问 linux.do RSS）→ 点开看全文 → 点「存」后在素材库能搜到。截图 1440 与 390 两种宽度存 /tmp/impl-t5/。禁止 computer use，不操作黄总的真实桌面或 Chrome。
- 不提交、不推送。

## 报告
写 coordination/impl-t5-report.md，开头「决定要点」表（≤10 行：结论、H/M/L 自查、改动文件与 SHA256、测试数字、新 PID、截图路径），然后 G1–G6 逐条结果。完成后 worker_done 回主控。
