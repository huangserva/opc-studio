# R3-0 独立只读复核

任务 `task_46eea108169b` / Dispatch `ctx_6b1254ac5bfe`，对象 `task_e606f8d6b899`。时间均为 UTC；北京时间加 8 小时。本次仅写本报告，正式代码、配置、数据库未修改；没有重启、立即运行、提交或推送。所有合成执行与浏览器使用独立 `/tmp` 目录和 headless profile，没有使用 computer use 或用户 Chrome profile。

## 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | 代码、数据保护与本次自动同步复核通过；24h 连续运行见证尚未完成 |
| H/M/L | 当前未检出本次改动的 H/M 缺陷；1L：真实知识库状态 GET 曾在 10 秒内未响应，原因未知，不把它归因于本次改动 |
| SHA | 交付 7 文件全部匹配；插件 `6e6b58fc…` / `a00b4e2c…`，适配器 `d4007c28…`；完整值见 `sha.json` |
| 定向测试 | 123 项：122 通过、1 跳过；真实隔离浏览器合成路径 6/6；额外组合故障路径 2/2 |
| 旧版反证 | 同一 3 项反例得到 2F/2E：唯一键冲突、多段 JSON、缺少超时退避重试，均能检出旧故障 |
| 历史保护 | 24887 条全量完整行指纹全部一致，缺失 0、改写 0；总数 29701，新增 4814 |
| 新增数据 | 下载/图片链接 0、私聊 0、wxid 开头标题 0；公众号文章 463 条保留；标题 2467/1774/573 与交付一致 |
| 运行账本 | `109dbe86…` partial/time_budget、797 条/29 批；`2fc30256…` 为工作台重启中断，来源独立任务继续提交并最终partial/time_budget |
| 隐私 | W3 密钥模式零命中；真实正文前 80 字精确比对零命中，标识词法命中均为普通代码/哈希误报，未确认泄漏 |

## 1. 范围、SHA 与代码审查

【通过】7 个交付文件 SHA256 与实施报告逐项相同，包括两个插件文件、`wechat_links.py`、3 个 Python 测试和 E2E 脚本。插件逐字对照 `/tmp/impl-r3-0-02017284/` 改前备份：

- `publish_links` 仅增加规范化、真实区间核对后的 `(document_id,url)` 去重，并以有序去重代替原无序集合；跨消息不合并，事务 DELETE/INSERT 保持原边界。
- `validate` 增加逐段完整 JSON 对象解码，仅接受唯一雷达对象，二义答案拒绝；未放松证据、字段或条目规则。
- 异常增加类型、阶段、SQLite 错误码和无局部变量/源码的堆栈位置；SQLite 原错误值不落回执。超时标记 partial、保留未处理批，不隔离。
- `pi_executor` 只增加超时分类、一次可取消的 1 秒退避与剩余预算检查；与空输出共用最多 2 次尝试，其他错误不新增重试。

【通过】180 秒本地请求设置未改：雷达预算相关代码无 diff；独立读取第二轮 29 份真实 `execution-receipt.json`，`timeout_seconds` 全为 180。真实超时一批的尝试状态为 `model_timeout → returned`，1 秒退避、重试数 1；耗时分别 11596/18775ms。该观察证明重试曾实际恢复，不能证明上游超时根因。

【通过】新链接适配器固定只读当前 workspace 的 `radar-analysis/assistant.db.radar_links`；私聊过滤、按 host/path 边界过滤下载、既有分享只查存在不 `_write`，返回非完整快照以阻止历史删除。标题使用投影、消息卡片、正文开头、无标题的既定顺序，不抓外网。保留旧归因参数规范化规则及消息/URL 去重身份。

## 2. 测试与故意恢复旧版

【通过】执行 `PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python -m unittest discover -s tests -p 'test_wechat*.py'`：123 项，122 通过、1 跳过（8.639s）。未跑并发全量。

【通过】另用正式8766页面、独立 headless profile 进行只读导航：微信分享来源与结果均为29701，零写请求、零 pageerror；没有真实页面截图。证据 `live-result.json`。

【通过】`node tests/e2e/wechat_links_r3.cjs` 使用随机端口、合成数据库、独立 headless profile：页面更新来源、保留旧历史、下载过滤及计数、来源范围、wxid 标题回落、原消息区间引用、二次同步幂等，6/6。截图仅合成数据，证据 `/tmp/wechat-links-r3-e2e-SzJN6K/`。

【通过】在 `/tmp/review-r3-0-46eea108/old/` 复制插件，并仅恢复两个改前备份，用相同的归一化碰撞、多段 JSON、超时重试反例运行：3 项总体失败（2F/2E，子测试可同时贡献 F/E）。冲突明确为 SQLite UNIQUE；多段对象为 JSON Extra data；超时未调用退避。正式代码的同组测试已包含在上述全绿结果内。

| 故障/边界 | 观察结果 |
|---|---|
| 本机模型不可达 | 额外组合测试使用合成 stdio worker 实际访问已关闭的本机端口，经过 PiExecutor→RadarPipeline，failed/model_failed，attempts=1，analyzed=0、quarantined=0、pending=1 |
| 超时重试后仍超时 | 同一路径合成超时事件，attempts=2，partial/model_timeout；analyzed=0、quarantined=0、pending=1 |
| 空批 | 定向测试 succeeded、processed=0、batches=0，不调用模型 |
| 坏批 | 两次验证无效后记录 quarantined，下一批继续并新增 analyzed；不是整个任务崩溃 |
| 非 JSON 返回 | 有界修复后可诊断隔离，保留失败类型与记录；与传输失败不隔离的行为区分清楚 |
| 投影写入异常 | 合成 trigger 故障可分类，原投影事务回滚；合成敏感错误文本不进入结果/日志 |

额外测试只使用合成消息和本机关闭端口，没有修改真实模型配置、停止模型服务或发送真实聊天给测试模型；mock 均在上下文内，退避只替换该次 cancel Event 的 `wait`。

## 3. 真实数据只读核对

【通过】真实库连接均使用 SQLite URI `mode=ro` 和 `PRAGMA query_only=ON`。复用实施前 `history-hashes.json` 的完整历史行哈希基线，重新逐条查询全部旧 ID；每行用 `sha256(json.dumps(dict(row), sort_keys=True, ensure_ascii=False))`，包括 title/body/hash/provenance 等所有列。基线 24887 条，整体基线摘要 `e1f076ceccd54878ef5ebf01ce7d5c1e44b2d96824600b7bb7cb3c8307bf5a07`；本次全量比较 unchanged=24887、missing=0、changed=0。基线形成于实施前，无法倒推证明更早历史状态。

【通过】06:04:05Z 当前总量 29701、新增 4814、最新修改时间 `2026-10-30T04:26:58+00:00`。新增按实际 provenance.title_basis 分组：untitled=2467、message_excerpt=1774、projection=573，总和 4814。逐条检查新增 URL：文件/图片规则命中 0，私聊 0，wxid 开头标题 0，`mp.weixin.qq.com` 463 条。

【通过】真实新投影 4823 条，重复 `(document_id,url)` 0、引用正文区间/hash 不匹配 0、私聊 0。导入器已落库新增条目比投影少的数量，符合报告所列重复/无效URL/原消息匹配拒绝；本轮没有写入或补导。

## 4. runs 与重启归因

仅白名单提取任务账本元数据，不输出任务正文、模型输出正文或聊天字段。

| run_id | started_at | finished_at | 工作台 status | error_kind | processed / batches |
|---|---|---|---|---|---|
| 109dbe86cbbb405fb5e43ce6a0f3c5f6 | 05:20:48.716039Z | 05:45:25.166702Z | partial | 缺省 | 797 / 29 |
| 2fc30256f4b5451f8a2c142e02ded78e | 05:45:25.928555Z | 05:46:37.450577Z | interrupted | 无 JSON 回执 | 未知 / 未知 |

【通过】PID36018 的操作系统启动时间为 05:46:24Z；`2fc30256…` finished_at 在其后约 13 秒，output 含静态重启标记。`scheduled_tasks.py:63` 启动初始化会把旧 running 行改为 interrupted 并追加该标记。以上共同支持这是本次 8766 重启的账本中断，不是从 error_kind 推测根因。

【观察】独立来源 PID88663 于05:20:16Z启动，早于新运行。06:04Z `wechat-source/jobs.db` 中 `2fc30256…` 仍 running，analyzed 已有该 run 的 282 条。工作台 interrupted 与独立来源运行终态不同；不能据此称来源已停止，也不能把 282 条算作本次工作台的最终 processed 回执。没有取消、重跑或修改该任务。

## 5. 隐私与证据范围

【通过】扫描本轮源码/测试/报告、实施证据顶层 JSON/TXT/Python/JS 与本次证据文本：常见 Key、Bearer、云凭据模式零命中。以真实原消息 provenance 的标识集合和正文前80字作不输出原值的精确比对：正文零命中；7 个不同标识词法命中经遮盖上下文核对，均落在普通英文代码词、类名、注释或行哈希内，没有实际会话/聊天语境。报告和夹具人工核查只含合成消息、统计、路径、代码/任务ID和哈希。

扫描不把正常运行时模型会话缓存或真实数据库当成可交付报告/夹具；没有把它们复制或分发。精确字串扫描不能证明所有昵称/改写片段都绝不存在，本次未观察到此类泄漏。证据 `privacy-results.json` 给出扫描数量、原始误报与人工结论。

## 24h 见证当前进度

- 协调者台账指定起点：2026-10-30 05:20Z；最早满24小时：2026-10-31 05:20Z（北京时间13:20）。
- 已见证自然结束的一次来源运行：partial/time_budget，797条29批，无最终 error_kind；第二次来源运行于06:09:08.790599Z自然结束，partial/time_budget、362条20批、无最终error_kind；工作台账本因重启保留interrupted。
- **24h 未满，连续成功未知**；本报告不以测试全绿、局部推进或工作台interrupted代替全天结论。保留实施报告所述积压和偶发上游超时的运行限制。

## 证据索引

本次 `/tmp/review-r3-0-46eea108/`：`sha.json`、`mutation-old.txt`、`extra-checks.json`、`projection-check.json`、`snapshot-*.json`、`source-jobs.json`、`real-timeout-receipts.json`、`privacy-results.json`，以及对应只读/合成复核脚本。实施自动同步证据复用 `/tmp/impl-r3-0-02017284/auto-sync-witness.json`，历史行哈希基线只在原临时目录读取。浏览器合成截图目录见第2节。

## 自动同步当前观察

【通过（自动触发归因为推断）】本次没有发出同步请求，独立浏览器全部非 GET 请求阻断且实际 blocked_writes=0。来源 `synced_at` 从06:04Z读取的05:26:45.458400Z，推进到06:11Z读取的06:07:59.086068Z；总量29701、全部行摘要 `cc12132b44bee492f7f18902c80b4e311ce052b5cbd5f61bf6e2b7b2975cd0cf` 完全相同，旧历史缺失/改写仍为0。来源在05:46:24Z重启后，watch的10分钟周期又受前序来源同步耗时影响；未持久化触发者，故「自动触发」仍是代码周期、来源时间推进及本工人零写请求共同支持的推断，不冒称有触发者审计字段。

【观察】06:11Z 另测 `2fc30256…` 的 analyzed 已由282增至362。最终再查来源jobs，确认于06:09:08.790599Z自然结束，partial/time_budget=true、processed=362、batches=20、has_more=true，无最终error_kind、dropped_items为空；与362条analyzed吻合。工作台interrupted不等于数据丢失或来源停止，双账本状态须分别读取。证据 `source-final.json` / `source-final-output.json`。

最终只读检查截止 `2026-10-30T06:12:22.090336+00:00`，24h 起点以来尚不足1小时。自动同步已在本次复核期间推进且行数、全量行摘要不变；24h仍待协调者后续见证。复核结束7文件SHA再次核对无漂移。
