# 实施合同 A1-b：雷达后端地基返修（1H3M1L）

- 派给：Codex term_2619a6ec（黄总 2026-10-15 16:15：CodeBuddy 因读系统代理持续 502，A1-b 改派 Codex 2619a6ec；该窗口未复核过 A1，A1 复核方是 c75fec01）
- 原派 CodeBuddy term_6710141e 的 task_6d23e6ef39dd 已取消，CodeBuddy 未产出任何改动。
- 基线：Pi 的 A1 交付 /tmp/radar-a1-deliver/（`files/` 下 13 份完整文件，补丁 `radar-a1.patch` SHA256 `474f896b…`）。在它的基础上改，不推倒重写。
- 必读（按顺序）：
  1. coordination/review-radar-foundation-a1-report.md（**本单依据**：H1、M9、M10、M2、L2 的复现、行号、证据）
  2. coordination/impl-radar-foundation-a1-contract.md（原合同：必做 1–10 与 15 条反例**仍全部有效**）
  3. coordination/impl-radar-foundation-a1-report.md（Pi 的实现说明）
  4. 复核方独立测试：/tmp/review-radar-a1-dzz49m8e/（independent.py、extra.py、business_false_pipeline.py、page.cjs 及 evidence/）
- 仓库：/Users/huangzongning/development/article-studio（分支 droid/updates-2026-10-10）。正式目录已合入 A2 页面（js `dc6e1367…`、html `2e842814…`）。只在 /tmp 独立副本里做。
- 并行：B2-b 改 public/*、server.py；B2-a2 改 scripts/radar_feishu.py；B1 有 scripts/radar_monitor.py。**这些都不是你的文件，不碰。**

## 必修
1. **H1-a 业务状态与数据形状**（radar_client.py）：
   - `success:false` → 一律业务失败，不管 data 里有没有帖；`code` 非 0（数字或字符串）→ 业务失败。
   - `data` 缺 `tweets`、`tweets` 为 null 或不是数组 → 形状错误（失败），不能当「空成功」；只有明确的 `tweets: []` 才是空成功。
   - 首页成功、第 2 页失败 → 该号状态 `partial`：首页帖照常有效，run 记录里单列 `partial` 计数，快照里该号标 `pages_complete=false`；计入覆盖率成功（写进报告，作为口径）。
   - 复核的 51 号全「HTTP 200 + success:false」离线链路 → success=0、不发布、manifest 与公开 bundle 都不变、退出码非 0。
2. **H1-b 唯一切换点**：页面直接读 `public/twitter-radar-bundle.json`，所以**以公开 bundle 作为唯一权威切换点**：
   - 公开 bundle 内嵌 `manifest` 段（run_id、快照相对路径、快照 SHA256、版本 bundle 文件名）。
   - 发布顺序：快照 → 版本 bundle → 公开 bundle（切换点）→ 最后写 manifest.json（派生索引）。
   - 每轮开始（拿到锁后）若 manifest.json 与公开 bundle 内嵌段不一致，以公开 bundle 为准修复 manifest.json，并记日志。
   - 「上一轮成功快照」一律从公开 bundle 的内嵌段找，不从 manifest.json 找。在报告里写清楚，B1 联调按这个读。
3. **H1-c 崩溃垃圾回收**：每轮拿到锁后，清理本模块自己命名规则的临时文件（只在快照目录、版本 bundle 目录、public 下 bundle/manifest 同目录，不跟随符号链接，只删匹配本模块临时文件命名的文件）。复核的四阶段真 SIGKILL 之后，下一轮结束时 0 残留。
4. **M9 整轮绝对截止**：
   - 每次请求的超时 = min(单请求超时, 整轮剩余时间)；
   - 到截止时间不等卡住的线程（不能用会阻塞等待的 `with ThreadPoolExecutor`；用守护线程或 `shutdown(wait=False, cancel_futures=True)`，并确保进程能退出）；落盘一条失败 run 记录后退出，退出码非 0；
   - 调用计数在**发起请求时**就加 1；截止时还没返回的号状态记 `deadline_inflight`，没发起的记 `not_started`，两者都算失败。
   - 复核的 D01（fetch 永久卡住，deadline=0.1s）→ 进程在 deadline + 2 秒内自己退出，有失败 run 记录；D02 → api_calls=1。
5. **M10 清理越界**：快照清理跳过符号链接的日期目录（`date_dir.is_symlink()`），并校验 `resolve()` 后仍在快照根目录内；外部文件一个都不能删。保留口径按北京时间自然日 30 天，写进报告。
6. **M2 粉丝归属**：账号粉丝只从「作者 == 查询账号」且不是转推的记录里取最新；查询账号自己没有帖时为 null（未知）。复核的 Audit/Other 反例 → Audit 粉丝 = 500。
7. **L2 采集时间**：bundle `updated_at` 改为本轮采样完成时间（run.finished_at 转北京时间，保持当前页面能解析的格式）；另加 `generated_at`、`collected_at`（UTC ISO，带 Z）。用复核方 page.cjs 与 tests/e2e/twitter_radar_page.cjs（35 场景）确认 A2 页面显示「采集时间」= 采样完成时间。
8. **类型溯源**：快照逐帖增加小字段 `type_evidence`（原始响应里的 isQuote/quoted 帖 ID/retweeted 帖 ID/in_reply_to ID 等判别字段，不含正文与凭据），用于以后核对类型。

## 已知弱点（原按 CodeBuddy 写；改派 Codex 后同样适用——Codex 历史实施弱点是整条链路与并发状态遗漏、继承旧代码风险）与必须逐条自测的反例
CodeBuddy 历史弱点：**预算/截止/取消状态未贯通**（intel-lease 4 次 3 次不通过）、**外部源安全与资源上限失守**（aisa-radar-source 6 连败后换人）、**报告声称与实测不符**（自评失真率 68%）。本单 M9 截止、H1 失败口径正是这两类。必须：
1. 复核方 /tmp/review-radar-a1-dzz49m8e/ 的 independent.py、extra.py、business_false_pipeline.py **全部通过**（45 场景 45/45）、page.cjs 4/4。只允许改复核报告里已说明的夹具路径问题，**不得改断言**；如必须改，逐条说明并给原断言原文。
2. Pi 原 29 项测试全绿（改断言须逐条说明）。
3. 你自己为 1–8 每条新增测试，其中真 SIGKILL（父进程杀子进程，不是抛异常）、真实耗时断言（用 `time.monotonic()` 量，不是只看标记）、符号链接目录各至少 1 个。
4. tests/e2e/twitter_radar_page.cjs 35/35（用你的 bundle 在隔离浏览器里跑）。
5. 产物 `rg -n 'sk-aisa'` 0 命中。
6. 报告里每条「已修」都附测试名和实测输出片段；没有实测输出的条目视为未修。

## 允许改动的文件（唯一归属）
- scripts/refresh_twitter_radar.py、scripts/radar_common.py、radar_client.py、radar_collect.py、radar_bundle.py、radar_store.py、radar_retired.py、scripts/radar_targets.json（只读，除非复核点名）、4 支停用脚本（不动）、tests/test_twitter_radar_*.py
- 禁止：public/*、server.py、scripts/radar_monitor.py、scripts/radar_feishu.py、radar_page_api.py、generation/*、视频实验室文件、其他任何文件。

## 验证与交付
- Python 用 `../article-importer/.venv/bin/python`；只跑定向测试；全量 discover 要跑先 `orca orchestration ask` 问协调者。
- 真实冒烟：允许在 /tmp 副本跑 1 轮真实采集（约 55 次 AIsa 调用）验证 type_evidence 与 L2；Key 在子 shell 读：`AISA_API_KEY="$(plutil -extract EnvironmentVariables.AISA_API_KEY raw -o - ~/Library/LaunchAgents/com.servasyy.article-studio.plist)"`，不得明文出现在命令、脚本、日志、报告里。快照与 bundle 只写在副本内。
- 不改正式目录、不重启 8766、不用 computer use；浏览器只用隔离 Playwright（独立 profile、随机端口）。
- 交付到 /tmp/radar-a1b-deliver/：改动后完整文件、统一补丁 `radar-a1b.patch`（相对正式工作区当前内容，包含 A1 全部内容，即一次应用得到最终版）及 SHA256、新 /tmp 副本 apply --check → 应用 → py_compile → 定向测试 → 复核方 45 场景 → page.cjs → 35 场景 → apply -R --check 记录。
- 报告：coordination/impl-radar-foundation-a1b-report.md，开头「决定要点」表（≤10 行）：结论、H1-a/b/c、M9、M10、M2、L2、类型溯源逐条结果、复核方 45 场景结果、29 项结果、文件与 SHA256、真实冒烟 run_id/覆盖率/调用数。
- 完成后用 Orca 发 worker_done。**未逐条自测、没有实测输出的交付直接退回，不派复核。**
