# 决定要点

| 项目 | 结论 |
|---|---|
| 结论 | **通过（实施自测，待独立复核）**：今日页、15 条反例、追加排序和筛选竞态均通过。 |
| H/M/L | 新增 **0H / 0M / 0L**；既有 **1L**：旧 A2 页 E2E 四项失败，改前副本同样失败，主控已豁免。 |
| 文件与 SHA256 | 10 个代码/测试文件，完整 SHA 清单见文末；`twitter-radar.js` 保持 `e769f6c7008a1474fcdf1d4d3be86d08af3cadadaa27fba5c2b055039d139b5b`。 |
| 新增测试 | Python **22/22**；隔离 Playwright **56/56**；正式 8766 隔离 Playwright **23/23**，pageerror=0。 |
| 既有回归 | Python **144/144**；Grok E2E **30/30**；监控旧页 **43/43**；视频旧页 **60/60**；A2 静态旧页 **30/34**（已豁免）。 |
| 首屏 | 1440×900 完整可见 **5 条**；3 次首卡 **178.3 / 260.6 / 97.2 ms**。 |
| 接口耗时 | 三接口各连续 5 次均 <1s；最慢列表 **0.04699s**、详情 **0.07762s**、反馈 **0.06715s**。 |
| 部署与范围 | 8766 最终 PID **52356**，启动 **2026-10-29 21:45:20**；server 新增 9 行装配，另有主控批准的 1 行 CSP 例外；范围外 tracked 增量 0；未提交、未推送。 |
| 真实写入恢复 | 准→撤销、拿去写、5 次 POST 测完后，4 份反馈文件前后 SHA 逐字一致；详见下表。 |

任务：`task_61609dae2ac7 / ctx_bf693f1b99a8`。本报告是实施交付，不替代其他窗口独立复核。

## 15 条反例逐项自测

| # | 结果 | 证据 |
|---|---|---|
| 1 原文截断 | **通过** | 2700 字以上中文、emoji、多行、末尾全角空格/制表符与源逐字相同；Python `test_long_text_exact_and_summary_no_rich_fields`、E2E「2400字以上正文含emoji多行逐字一致」。摘要 ≤120 个 Unicode 字符，详情不截。 |
| 2 注入 | **通过** | `<script>`、`<img onerror>`、`&amp;` 原样 textContent；正文对应 script/img 节点为 0。 |
| 3 链接白名单 | **通过** | javascript、伪 x.com 域、空串、协议相对 URL 四反例都无原帖链接；合法 X 链接正控制可用；poster 只接受 http(s)，浏览器文档策略进一步只放 HTTPS。 |
| 4 合并取数 | **通过** | 双来源同 id 只一行；最新 AIsa 样本 12000 浏览压过旧样本；正文选较长原文，两来源 rich 均保留。Python 与 UI 双重断言。 |
| 5 并发反馈 | **通过** | /tmp 随机端口两个 HTTP 并发 POST 不丢 id；accurate→clear 无记录；Grok 本地与 Bot 镜像逐字一致；Python 同时验证 kept/used 和视频写入/撤销。 |
| 6 来源校验 | **通过** | 缺 Origin / 错 Origin / 缺 X-Article-Studio 各 403，授权写入前 4 个反馈目标都不存在；沿用 server 原校验。 |
| 7 坏数据 | **通过** | Grok 目录真正删除、半写 JSON，G/A/V 的 `1.2万` / NaN / 布尔，缺 AIsa、坏视频快照：来源有 error，其他来源照常显示；合法 JSON 响应不携带 NaN。22 项 Python 与状态行展开 E2E。 |
| 8 不凑数 | **通过** | 原始报告无评分时 top=[]，显示「等 Grok Bot 按画像打分后出现」；有评分但可做分不足时显示「今天没有值得做的」。真实报告当前无画像评分，未代填。 |
| 9 首屏 | **通过** | 真 8766 最终三次 178.3/260.6/97.2ms，可见完整卡片 5 条；夹具也 ≥5 条。测量从导航开始至首卡 visible。 |
| 10 隔离 | **通过** | 所有浏览器 headless、独立 /tmp profile；夹具 workspace/reports/随机端口均 /tmp；正式 POST 只做合同指定动作并恢复原始字节，4 份 SHA 见下表；无 computer use，无真实 Chrome profile。 |
| 11 范围 | **通过** | 开工 git diff、状态和关键 SHA 在 `/tmp/impl-today-p1-baseline/`；范围外 tracked diff 分块比较结果为空；旧 twitter JS SHA 不变；唯一额外 server 行由主控明确批准。 |
| 12 旧页 | **通过（含主控豁免）** | Grok30/30、监控43/43、视频60/60，真实旧页可打开。静态 A2 四项旧失败在改前导航副本复现，主控消息 `msg_f4b9ab9c25c1` 已豁免为既有1L。 |
| 13 卡片独立复用 | **通过** | 空白页只 import item-card，直接传 `/api/today` 双来源摘要：徽标 A/G、准/不准/存/拿去写，显式 feedback 参数和点击回调生效；window.grokState=undefined，无页面可变全局依赖。 |
| 14 统一详情 rich | **通过** | 空白页直接传 `/api/today/item`：Grok/AIsa 两边的长文、串推、引用逐项断言，3 张媒体（两来源图片+视频封面）保留，不依赖 `_grok/_sources`。 |
| 15 热度排序 | **通过** | 同每小时浏览的夹具中，412 收藏较早帖在 111 收藏较晚帖前；热度相等才按时间倒序；收藏/赞 ≥1 和视频速率回退也有 Python 断言。真实两个指定 id 的先后顺序已由 UI 和 API 验证，见下文。 |

## 追加排序公式

排序键为：**档位升序（爆款→起势→低粉爆款→其他），热度降序，发布时间降序，id**。置顶 3 条仍单独按 fit_score+doable 选，不受本次热度加权替代。

- 基础速率 `R`：优先用最新样本 `views_per_hour`；没有时用最新样本浏览量 ÷ 该样本的发帖小时数。小时数优先 `hours_since_created`，否则用同次 `fetched_at - published_at`。数据不足时用浏览量，无浏览量为 0。
- 视频基础速率同样使用播放量 ÷ 同次采样发帖小时数；无法计算时用播放量；不拿点赞速率冒充播放速率。
- `B=bookmarks`、`L=likes`。若 `B≥100` 或 `B>0 且 L 已知且 B≥L`，权重 `W=1+B/100`；否则 `W=1`。L=0 且 B>0 归入高收藏，避免除零。热度 `H=R×W`。
- 缺收藏保留 null，不从旧来源借数字；列表显示「收藏 未知」。真实 AIsa 未知收藏文案已验证。

| 指定真实帖子 | 收藏 | 发布时间 UTC | R | H | 48h接口位置（从1计） |
|---|---|---|---|---|---|
| `x:2115655889428623568` | 412 | 2026-10-29T04:04:05+00:00 | 2594.05 | 13281.5360 | 3 |
| `x:2115715632532713672` | 111 | 2026-10-29T08:01:29+00:00 | 1953.71 | 4122.3281 | 7 |

## 两项额外修正与授权

**筛选竞态**：列表 fetch 期间将旧列表设为 inert，响应到达后才恢复交互；替换列表时取消旧详情并重置阅读器。250ms 人为延迟的隔离 E2E 验证旧列表不能点、随后视频可打开，不再停在加载中。作者列表读现有 30 天数据并翻页取齐，不发起采集。

**图片 CSP 例外**：亲测发现原 server 全站 `img-src 'self' data: blob:` 把外图直接以 `requestfailed=csp` 拦截，并非 CDN 故障。经 Orca ask，主控明确批准只改 server 原 CSP 装配的 1 行：当 `urlsplit(self.path).path == '/today.html'` 时，仅 img-src 加 `https:`；其他页面及 default/script/connect/object/frame 指令、no-referrer 一字不动。

改前响应头实存 `/tmp/impl-today-p1-baseline/csp-before.json`。新增反例实测三个旧页面 CSP 与改前完全相同、HTTP 图片仍被 CSP 拦截且有占位、HTTPS 夹具图片正常解码。最终正式视频封面 **HTTP 200 / naturalWidth=524**；截图可见实际封面，未通过移除测试 CSP 来凑通过。

## 接口连续五次（秒）

| 接口 | 第1 / 2 / 3 / 4 / 5次 |
|---|---|
| GET /api/today | 0.04208 / 0.04189 / 0.03963 / 0.04699 / 0.04327 |
| GET /api/today/item | 0.05421 / 0.07762 / 0.05281 / 0.05343 / 0.05625 |
| POST /api/today/feedback | 0.06715 / 0.05567 / 0.05888 / 0.05232 / 0.05309 |

## 反馈恢复 SHA256（最终正式验收）

| 文件 | 前 | 后 |
|---|---|---|
| `workspace/today/feedback.json` | `d9d38068d461dd1fe5998023d609578e621a3e8ec62db6e4e7f7bb849ba175cb` | `d9d38068d461dd1fe5998023d609578e621a3e8ec62db6e4e7f7bb849ba175cb` |
| `workspace/twitter-radar/grok-bot/feedback.json` | `9de8f46e6c04349996784b23388e613d7d4b298c72342ecff1018fe2a60d17de` | `9de8f46e6c04349996784b23388e613d7d4b298c72342ecff1018fe2a60d17de` |
| `~/grok-bot-reports/feedback/studio-feedback.json` | `9de8f46e6c04349996784b23388e613d7d4b298c72342ecff1018fe2a60d17de` | `9de8f46e6c04349996784b23388e613d7d4b298c72342ecff1018fe2a60d17de` |
| `workspace/video-radar/feedback.json` | `absent` | `absent` |

最终验收前已有的反馈原样保留；仅恢复本轮快照，不把早先空状态冒作本轮基线。锁文件/空目录属运行时产物，不含测试反馈记录。反馈的多目标发布沿用各模块逐文件原子写，不宣称跨文件崩溃事务。

Grok 映射：准→worth、不准→not_worth、存→worth、拿去写→used；组合状态以 used 优先、其次 inaccurate 保持 not_worth。视频沿用 worth/not_worth，keep/use 映射 worth；clear 因视频旧公共 API 不接受 null，复用其 `_FEEDBACK_LOCK`、损坏校验和 `_atomic_write` 移除该 key。今日文件单独保留 status 与 feedback；没有新增表。

## 测试与证据索引

| 验证 | 结果 | 证据 |
|---|---|---|
| 新 Python | 22/22 | `/tmp/impl-today-p1-unit.log` |
| 新夹具 E2E | 56/56 | `/tmp/impl-today-p1-e2e.log`、`/tmp/today-e2e-uF96fs/results.json` |
| 正式 E2E | 23/23 | `/tmp/impl-today-p1-real.log`、`/tmp/today-e2e-Un7nYz/results.json` |
| 原 Python 11 模块 | 144/144 | `/tmp/impl-today-p1-regression.log`；未跑全量 discover；C02 不在这批模块，不伪称已修。 |
| 原 Grok E2E | 30/30 | `/tmp/impl-today-p1-old-grok.log` |
| 原监控 E2E | 43/43 | `/tmp/impl-today-p1-old-monitor.log`；完整 /tmp 代码副本，绝未就地覆盖真实 bundle。 |
| 原视频 E2E | 60/60 | `/tmp/impl-today-p1-old-video.log`；随机端口/临时 workspace。 |
| 原静态 A2 与基线 | 均30/34、同4失败 | `/tmp/impl-today-p1-old-twitter.log`、`/tmp/impl-today-p1-old-twitter-baseline.log`，失败 M3a/M3b/M3c/M3d 均「内嵌低粉榜为空」。 |
| 增量与 SHA | 范围外0；server9新增+1行例外 | `/tmp/impl-today-p1-baseline/before.diff`、`sha`、`server-increment.diff`、`scope.json`、`final-sha.json`、`real-rank.json` |

复跑命令：

```sh
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python -m unittest tests.test_today_view -v
node tests/e2e/today_page.cjs
node tests/e2e/today_page.cjs --real
```

最后一条会临时操作正式反馈并按快照恢复；独立复核可以只在夹具做写入、正式页只读。实现和测试没有调用采集、AIsa、发布、账号/云端写入，也没有操作黄总桌面。

截图（已实际查看）：

- [首屏](impl-today-p1-screenshots/today-first-screen.png)
- [视频与真实封面](impl-today-p1-screenshots/today-video.png)
- [不完整提示](impl-today-p1-screenshots/today-incomplete.png)
- [拿去写与剪贴板](impl-today-p1-screenshots/today-clipboard.png)

## 文件 SHA256

| 文件 | SHA256 |
|---|---|
| `today_view.py` | `773d70a5d8731654958320259c0a500be43445ea4f35f9b1b6a50612a89c166c` |
| `public/today.html` | `d4b4afc48da6bae57ebaa9ac7667d0d996d6f7fcc5b3489c358639fc2fd96f65` |
| `public/today.js` | `a86fec2229a7053b1a42499630ab01ff75b4c594fbac135ee2eb67595eacda90` |
| `public/today.css` | `2c956541c90b0267b115e4d5a4d6b590da6da3f90a35c2bc5a4476f894641daa` |
| `public/components/item-card.js` | `1226272b010c0258ca0e5122286173041b25ab4e833880b65f8a9bea0d81939c` |
| `public/components/list-reader.js` | `07bbf5dcc80c30ca788bc109bdb643e2ef9108d349274b762ccbac3dc090c035` |
| `server.py` | `cb09fa129d87de2cc738b209a2b8f4c08b5d41d44fe1be0312b3fa0d1afa8659` |
| `public/workspace-nav.js` | `7ef957d34981e9fd4b545ff0fd90be36f1d7c9828f2fca2f7601426b22aa667b` |
| `tests/test_today_view.py` | `60c51475404c46657196e9dd78a11c36ce500fb5513d015bd337ebec0620a9f9` |
| `tests/e2e/today_page.cjs` | `a84d1a384a7fadeed834f424e252d02842463cf8f2630db9106b81a5e81186bf` |

交付附带本报告与上述4张截图。开工 HEAD=`22218a71757b1217acce5491e754152e29031ab8`，未提交、未推送；原有未提交改动未覆盖。
