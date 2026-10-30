# 合同：impl-finish-p4（Codex 实施 · Orca）：重新设计 4 期「收尾」

上游：`coordination/redesign-plan.md` 第 3、7 节。依据：explorer 2026-10-29 23:45 只读调查（要点抄在第 0 节）。冲突时以本合同为准。
派工条件：3 期（task_b5e51fab7add，改 server.py / workspace-nav.js / list-reader.js / items_store.py）复核收口后才派。4 期分 4 个子单，**每个子单单独派、单独复核、收口后再派下一个**：4-1 → 4-2 → 4-3 → 4-4。

## 0. 现状要点（行号为 23:45 版本，3 期合并后可能偏移，开工先重新定位）
- 18 个页面；导航 `public/workspace-nav.js:5-19`：今日 → 素材库 → 我的作品 → 图片生成 → 视频生成 → 线索中心 → 事项与日程 → 发布反馈 → 商单管理 → 情报采集 → GitHub 来源 → 定时任务，底部「旧版」：知识库、创意回收、提示词卡（3 期会加「发布复盘」，并把「发布反馈」「X 官方接口」移进旧版）。
- 设置类功能分散：定时任务 `scheduled_tasks.py`（`workspace/schedules/tasks.db`，GET `/api/schedules`、`/api/schedules/runs`，POST `schedules/save|batch|<id>/<action>`）；模型设置在助手面板 `public/assistant.js:137-179`（GET `/api/assistant/settings`，POST `assistant/settings|select-model|test-settings`）；采集状态 `/api/collect/platforms|status`、`/api/intel/status`；雷达配置 `scripts/radar_monitor_config.json`；launchd 5 个（article-studio、radar-monitor、video-radar、wechat-radar、wechat-source）；日志 `workspace/logs/service.log`、`service-error.log`、`workspace/video-radar-launchd.log`，**没有读日志的接口**。
- events：`workspace/events/events.db` 只有 3 条事件，10-09 后没有再写入。依赖它的地方有 `public/clues.js:79,99`、`public/assistant.js:57`、`public/chat-links.js:6`，Python 侧还有 clues_store / github_source / scheduled_tasks。
- clues：threads 4944 条，雷达 `scripts/radar_monitor.py:351` 等仍在持续写入。**本期不删、不迁移**（留到两周指标复盘后再定）。
- server.py 共 2383 行，路由是一长串 if（`do_GET` 1399–1685，`do_POST`→`_post_routes` 1700 起）。拆分范例：`radar_page_api.py`、`video_radar_page_api.py`、`items_store.get_route/post_route`。测试里有 53 处 `from server import Store`、39 处自建 `ThreadingHTTPServer(Handler)`，另外还 import `content`、`assistant_module`、`wire_intake_links`、`wire_intake_analysis`、`seed_intel_schedule`。
- 「撤 /legacy/」**本期不做**：旧页 10-29 才移进「旧版」分组，规划写明要保留一个月，满一个月（2026-11-29）后另开单处理。

## 通用规则（4 个子单都适用）
- 只用隔离 Playwright（独立 profile、/tmp 目录或随机端口）。禁止 computer use 操作黄总的真实桌面或 Chrome。
- 后端改完用 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio` 重启，确认新 PID 已加载新代码，再做浏览器验收。
- Python 用 `../article-importer/.venv/bin/python`。全量测试 `-m unittest discover -s tests` 和 `npm run test:ui`，结果要和开工前的基线逐项一致。既有失败只允许 `tests/e2e/twitter_radar_page.cjs` 的 M3a–M3d，以及 `C02TimeoutKeepsOldTimestamps`。
- 开工先存基线：`git diff` 快照、全量测试结果、各页面截图，放在 `/tmp/impl-p4-<子单>-baseline/`。
- 不提交，不推送。不调用采集、AIsa、X、Buffer、R2。
- **不得输出服务环境变量**：确认 PID 只能用 `launchctl list | awk '/com.servasyy.article-studio$/{print $1}'` 或 `ps -o pid,lstart -p <pid>`，禁止使用 `launchctl print`、`ps eww`、`env`、`cat *.plist`，否则会把 AISA_API_KEY 等凭据打进工具输出（3 期 10-29 23:5x 发生过一次）。
- 报告 `coordination/impl-p4-<子单>-report.md`，开头放决定要点表（≤10 行：结论、H/M/L、改动文件与 SHA256、测试数字、8766 PID），之后逐条给出本子单反例的结果，以及下面通用反例的结果。

### 通用反例（该工人已知弱点，逐条自测）
- G1 **只看数字不看功能**（2 期教训：速度达标，搜出来 0 条）：每个验收项都要检查实际内容（页面上真实显示的条目、数量与数据源对得上），不能只看状态码或耗时。
- G2 **钻合同漏洞**：合同给的选项如果字面达标、实际不好用，要停下来问主控，不要按字面交付。
- G3 **假设与现实不符就自行扩围**（3 期 v0001 问题）：发现合同的前提和代码现实不一致时，先用 `orca orchestration ask` 问，不要自己扩大改动范围。
- G4 **范围**：允许清单以外的 tracked 文件，增量必须为 0（和开工快照逐文件比对）。

---

## 4-1 设置页 + 导航收成 6 项 + events 下线 + 文档
### 允许改动
- 新增 `settings_view.py`（只读聚合，以及受限的日志读取）、`public/settings.html`、`public/settings.js`、`public/settings.css`
- `server.py`：只新增 `GET /api/settings/overview`、`GET /api/settings/logs` 两行装配
- `public/workspace-nav.js`、`public/clues.js`、`public/assistant.js`（只限 events 跳转相关的行）
- `README.md`、`docs/product-scope.md`
- 新增 `tests/test_settings_view.py`、`tests/e2e/settings_page.cjs`
- `prompts/draft-template.md`（只修 3 期复核的 L1，见要求 5）
### 要求
1. **导航**：主导航固定 6 项：今日、素材库、创作台（即现在的「我的作品」 index.html，名字改为「创作台」）、发布复盘、商单、设置。「工具」分组放图片生成、视频生成。「旧版」分组放线索中心、情报采集、GitHub 来源、定时任务、发布反馈、X 官方接口、知识库、创意回收、提示词卡，路径都不变。「事项与日程」从导航移除，events.html 保留可直接打开。
2. **设置页**分 5 块，编辑一律复用现有接口，不新增写接口：
   - 来源：列出各来源（推特雷达、Grok Bot、AIsa、视频榜、微信雷达、GitHub、intel）的启用状态、最近一次成功时间、最近一次错误。数据取自 schedules 的 enabled 字段、各 status 接口和 radar_monitor_config.json（只读）。有 schedules 任务的来源，可以直接用现有 `schedules/<id>/<action>` 暂停或恢复。
   - 定时任务：列表加最近运行记录（`/api/schedules`、`/api/schedules/runs`），暂停和恢复走现有接口。完整编辑放一个链接到旧 schedules.html。
   - 模型：显示当前 profile 和各 profile 的可用状态（`/api/assistant/settings`），「切换」走现有 `select-model`。key 一律不显示明文，只显示「已配置 / 未配置」。
   - 运行日志：`GET /api/settings/logs?name=<白名单>&lines=N`。name 只能是 service、service-error、video-radar 三个，N 不超过 500。从文件末尾读，不整读大文件。返回前脱敏：包含 key、token、secret、authorization、cookie 的行，把值替换成 `***`。
   - 后台服务：显示 5 个 launchd 服务的标签和最近日志时间。**不在页面里提供启停按钮**。
3. **events 下线**：clues.js 里「关联事项」「建立待确认事项」「从事项导入」的入口，以及 assistant.js 的事件建议跳转，改为不显示（代码保留，用开关关掉）。events 的数据和接口不删。
4. **文档**：README 和 product-scope 改写成新结构：6 页、今日 → 素材库 → 拿去写 → 发布复盘这条主链路、设置页、旧版分组和撤除日期（2026-11-29）。删掉「导航分组仍在讨论」一类过时说法。
5. **起草模板 L1**（3 期复核 `coordination/review-write-p3-report.md` 第 6 项）：模板目前规定第一人称只能用「我准备…」「我想核对…」「我还没…」「我会先…」几种句式，导致成稿机械重复（一篇里「我准备」出现 6 次，另有 6 处相同的实测占位）。修改方向：保留「不编造亲测经历和数字」这条底线，允许写不以「我」开头的具体步骤；同一句式在全文最多出现 2 次；「【待补：我的实测数据】」占位合并到文末一处，或每节最多一处。每条改动注明依据。验收：在 /tmp 副本上用假模型或固定样本做单测，检查模板文本；如需真实模型，最多调用 1 次，报告写明。
### 反例
- P1 日志路径穿越：`name=../../.env`、`name=service%2F..`、`lines=99999`、`lines=-1` 都要返回 400，不读文件。
- P2 脱敏：在 /tmp 副本的日志里放 `Authorization: Bearer abc`、`AISA_API_KEY=sk-xxx`、`cookie=...`，接口和页面都看不到原值。
- P3 大文件：200MB 的假日志，接口仍在 1 秒内返回，内存不暴涨（只读文件末尾）。
- P4 模型 key：页面 DOM 和接口响应里搜不到任何真实 key 片段（用 /tmp 副本里放的假 key 验证）。
- P5 暂停/恢复：在 /tmp 副本上从设置页暂停一个任务，旧 schedules.html 里显示同样的状态；恢复后也一致。
- P6 导航：16 个页面每个都加载导航，主导航正好 6 项、顺序正确；旧版各页路径能打开；events.html 直接打开仍可用。
- P7 events 入口：clues 页和助手面板里看不到事件相关入口，其他功能不回退（clues 相关 e2e 全过）。
- P8 设置页 GET 5 次都 <1 秒；数据真实（来源条数与 schedules 实际任务数对得上，见 G1）。

## 4-2 server.py 拆分（一）：富文本清洗与启动装配
- 把 `server.py` 开头的富文本清洗（SafeHTML、markdown_aware_html 等，约 1–520 行）移到 `html_sanitize.py`；把 `seed_intel_schedule`、`wire_intake_*`、`_IntakeLedger`、`main()`（约 2178–2383 行）移到 `server_boot.py`。
- `server.py` 要 re-export 所有被外部 import 过的名字：用 `rg "from server import|import server" tests scripts *.py` 列全，报告里附上清单。`python server.py` 和 launchd 的启动方式不变。
- 允许改动：`server.py`、新增 `html_sanitize.py`、`server_boot.py`，可新增 `tests/test_server_split_imports.py`。**不改任何既有测试**。
- 反例：S1 全量 Python 测试、test:ui 与基线逐项一致；S2 re-export 清单里的每个名字都 import 得到，而且和原对象是同一个对象（`is` 判断）；S3 富文本清洗的现有安全测试全过，另用 10 个注入样例对比拆分前后输出，逐字一致；S4 launchd 重启后 PID 变化、16 个页面都能打开。

## 4-3 server.py 拆分（二）：按领域拆路由（不含 3 期和 items 相关）
- 仿照 `radar_page_api.py`，把 generation、intel/collect、deals、intake/cards、feedback、schedules/assistant 六块的 GET/POST 分支移到各自的 `*_api.py`。每个模块提供 `get_route(handler, parts)` 和 `post_route(handler, segments, payload)`，返回 None 表示「不是我的路由」。
- **顺序不变**：Origin / `X-Article-Studio` 校验仍然只在 POST 入口做一次，所有子路由都在它之后；上传类路由仍在 32MB 长度检查之前；子路由不得自己读 rfile。
- 允许改动：`server.py`，以及新增上述 `*_api.py`，可新增路由一致性测试。不改既有测试。
- 反例：R1 路由对照表：拆分前后，每条路由（方法 + 路径）在隔离端口上的状态码和响应体结构一致，脚本附在报告里；R2 每个拆出去的 POST 路由，缺 Origin 或缺头时都返回 403；R3 上传类大文件路径照旧可用，超过 32MB 的非上传请求照旧被拒；R4 全量测试与基线一致。

## 4-4 server.py 拆分（三）：Store 与收尾
- 把 `Store`（子 store 懒加载和作品版本业务，约 521–1128 行）移到 `work_store.py`，`server.py` re-export `Store`。CSP、`trusted`、`reply`、`/api/knowledge/reference` 的副作用钩子、today/library/publish 的 CSP 特例，原样留在 Handler 里。拆完后 server.py 目标 <900 行（做不到就写明原因）。
- 反例：T1 53 处 `from server import Store` 都能用；T2 懒加载单例不重复实例化（同一进程内两次取到的是同一个对象，sqlite 连接数不增长）；T3 CSP 特例与 reference 副作用钩子的回归测试都过；T4 全量测试与基线一致；T5 真实用户路径：今日 → 存 → 素材库 → 拿去写 → 创作台出现底稿 → 发布复盘页能打开，用隔离 Playwright 截图。

完成每个子单后发 `worker_done` 给主控 `term_b884acc7-d613-41d8-b736-0e3e84a18b37`。
