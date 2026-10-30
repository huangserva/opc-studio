# 第 3 轮 R3-0：微信雷达连续失败修复 + 链接雷达停更调查（实施合同）

派单：主控 term_b884acc7（run_17dc13f37a10）；实施 Codex term_02017284；复核 Codex term_57dd3162（不同窗口）。
依据：coordination/twitter-intel-plan.md v0.6 §3.9「现状」与 §4「微信修复小单」；coordination/twitter-intel-handoff-r3.md B 段 R3-0；排期与文件归属 coordination/round3-4-dispatch-plan.md。

## 现状（主控只读核实，10-30 11:40）
- 内置调度任务 `71fc0f788604414a996ef1fdd567f29f`「微信雷达 · 热点、知识与本人画像 · Qwen3.8」（kind=wechat_radar，启用），最近 19 次运行全部失败：`"error_kind": "analysis_error"`、`"雷达分析异常，本批未推进"`、`processed: 0`、`has_more: true`。实现主要在 wechat_radar_pipeline.py（调度入口在 scheduled_tasks.py）。
- 知识库里 `source='wechat-links'` 的最新时间停在 `2026-10-05T18:56`；相关代码 wechat_links.py、collect_core.py、clues_store.py。另有常驻 launchd 服务 com.servasyy.wechat-radar、com.servasyy.wechat-source（只用 `launchctl list` 看 PID/退出码，不读 plist 内容）。
- 同一调度里还有「微信采集与重要线索 · Qwen3.8」(5a81d1175ce6…，wechat_clues) 可作对照：它是否成功、用的是否同一模型配置。

## 前置：根因诊断已由检查窗口并行完成（黄总 10-30 补充第 2 点）
检查窗口 term_57dd3162 先只读查根因，报告 coordination/diag-r3-0-report.md。本单开工先读它，按其根因修；对诊断结论有异议，用证据说明并向主控报告，不另起炉灶重查。

## 要做
1. **先查根因再修，不要只加重试。**（以诊断报告为准，只补核实缺口） 从运行记录拿 19 次失败的完整错误链（异常类型、原文、发生在哪一步：取批、拼提示词、调本机模型、解析输出、写回），定位到代码行。逐条标「观察 / 推断 / 未知」，观察要附证据（运行记录 id、日志行、复现命令）。常见怀疑方向仅供参考、不得当结论：本机模型服务不可达或换了模型名、上下文超长、输出 JSON 解析失败、批次游标卡在同一坏批（has_more 一直 true 且 processed=0 说明可能每次都在同一批上失败）。
2. **修复**：针对根因修；若是坏批卡死，要有「坏批隔离/跳过并记录」而不是无限重试同一批；不得丢弃或改写微信原始数据（知识库只读，雷达自己的游标/结果表可写）。修后在工作台「立即运行」一次真实任务，成功（processed>0 或明确的「无新数据」）并给出运行记录 id。
3. **链接雷达停更**：查清 10-05 之后不更新的原因（谁负责写 source='wechat-links'、触发方式、10-05 前后发生了什么变化——git log、调度记录、launchd 状态）。能恢复就恢复并补跑 10-05 之后的增量（只读微信原始数据），给出恢复后最新时间；若判断不再需要它，写明理由与替代（例如 R3-1 工具卡会覆盖哪部分），交主控决定，不要擅自删除任务或数据。
4. 收口条件「连续 24 小时运行成功」：本单交付时给出修复后的首批成功运行记录；24 小时见证由主控巡检承接，报告里写「待见证」，不得把未来写成已发生。

## 允许改动
wechat_radar_pipeline.py、wechat_links.py、collect_core.py（仅链接雷达相关部分）、clues_store.py（如根因在此）、对应 tests/test_wechat_* 新增或修改用例。**scheduled_tasks.py 归 H1 所有，必须改时先向主控申请**。不改 settings_view.py、public/settings.*、today_view.py、public/today.*、item-card.js、server.py。调度任务配置如需调整（如超时、批大小），通过现有 /api/schedules 接口，报告写明前后值。

## 该工人已知弱点与必测反例（逐条自测，报告给结果）
- W1 解析取错位置（T5 教训：正则取第一处）：模型输出含多段 JSON / 前后有说明文字 / 代码块包裹时取对那一段；构造三种输出样例测。
- W2 测试 mock 泄漏：patch 一律 addCleanup/上下文管理器；测试不写真实知识库、真实 tasks.db、~/grok-bot-reports。
- W3 凭证外露：日志、报告、运行记录不得出现任何 Key/Token；grep 自证只给计数。
- 反例：本机模型不可达 → 运行记录标失败且错误原文清楚、游标不前进也不丢数据；单批坏数据 → 跳过并记录，下一批继续；空批 → 成功且 processed=0 不算失败；模型返回非 JSON → 失败可诊断不崩。
- 用故意改回旧行为的 /tmp 副本证明新测试测得出错。
- 隐私：报告、日志、测试夹具里不得出现真实聊天原文、成员昵称或微信号（夹具用合成数据；必须引用真实时只给条数和哈希）。

## 交付
coordination/impl-r3-0-report.md：开头「决定要点」表（≤10 行：结论、H/M/L 自查、根因一句话、改动文件与 SHA256、测试数字、修复后运行记录 id、链接雷达结论、新 PID）；正文根因链（观察/推断/未知）、修复说明、链接雷达调查与处理。完成 worker_done。

## 诊断结论与修复范围（主控据 coordination/diag-r3-0-report.md 更新，10-30 13:1x，以本节为准）
- 雷达根因（观察，已复现）：`publish_links()` 把 URL 去尾标点后未去重，触发 `radar_links(document_id,url)` 唯一键冲突（sqlite3.IntegrityError），启动投影先失败，没走到取批和模型调用。模型端点可达（/v1/models 200）。异常被统一回执吞掉。
- **真实实现不在本仓库**：工作台 wechat_radar_pipeline.py 只是兼容导入；实际代码在来源插件 `/Users/huangzongning/Documents/Codex/2026-10-03/b-q/work/wechat-source/wechat-assistant/wechat_source/wechat_radar_pipeline.py`（经 wechat_source_loader.py 加载，由独立来源 daemon 执行）。**本单授权改动该插件文件**（仅 publish_links 去重与异常分类相关部分），改前备份到 /tmp 并记 SHA。
- 修法（按诊断首选）：规范化后按 `(document_id,url)` 显式去重，保留一个真实存在的原文区间（`body[start:end] == text`），share_count 按消息 id 去重；内部异常保留类型与阶段写进 failures / 回执（不含聊天原文）。
- **链接雷达**：知识库 `source='wechat-links'` 仍读旧 `radar.db.message_links`（10-06 迁移后无人写入，增量候选约 4.16 万）。**主控倾向：让 wechat_links.py 改读新投影 `workspace/radar-analysis/assistant.db.radar_links`（每次雷达发布重建），不复活旧 TS 同步链**（旧 sync-collector.ts 会重建 mentions、输出真实会话标识，副作用大）。先只读核对 radar_links 字段与覆盖时间是否够用：够用就改读并补齐 10-06 之后；不够用则停下来报主控，附缺哪些字段。旧 radar.db 只读、不删。
- **服务重启**：插件改完需重启独立来源服务才会加载——授权 `launchctl kickstart -k gui/$(id -u)/com.servasyy.wechat-source` 一次（重启前向主控登记；只用 launchctl list 看 PID/退出码，不读 plist）。工作台 8766 如有改动另按通用段重启。
- 验收补充（诊断第 144 行）：零 analysis_error 不是唯一标准，须看到新增 analyzed、实际批次、pending 减少、链接最新时间向前推进。
- 允许改动（覆盖上文）：上述插件文件、本仓库 wechat_links.py、wechat_source_client.py（仅异常透传如需要）、对应 tests；scheduled_tasks.py 仍需先问主控。
## 通用
- 仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。8766 重启 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`，重启前向主控登记；查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`。**禁止** launchctl print / ps eww / env / cat 或 plutil 打印任何 plist 内容。
- 约束：不动 Grok Bot 设置；不碰飞书；微信数据只读；不停推特 AIsa 采集（com.servasyy.radar-monitor）；WorkBuddy 只读；视频实验室保护边界（public/lab-test.*、generation/*、server.py 视频/generation 路由、tests/test_*lab*）不动。
- 全量 Python 与 UI 跑前向主控登记。基线 Python 3638 项 0F/1E/7S（E=既有 video_lab），UI 723 项 719P/3F/1S（3F=受保护 lab-test）；H1 交付后数字会变，以主控通知为准。
- 浏览器只用隔离 Playwright（独立临时 profile；随机端口或对 8766 只读访问），禁止 computer use。不提交、不推送。
- 工人报「做不到 / 接口不可用」类结论时附错误原文与已试路线，主控会亲自复现核实。
