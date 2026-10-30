# H3b 微信线索晨报收编实施报告

| 决定要点 | 结果 |
|---|---|
| 结论 | 通过（实施自检）；真实Pi立即运行、运行记录、SQL与1440/390验收均完成，交A独立复核 |
| H / M / L 自查 | 0H / 0M / 0L；排队及发言人未知的事实边界见正文 |
| 改动文件与 SHA256 | [完整清单](evidence/impl-hub-h3b/file-sha256.json)；未改调度循环、server.py 或 H3a 闸门 |
| Python | 定向148项通过；全量3761项，0F/1E/7S，425.025秒；E为原有受保护视频测试 |
| UI / 浏览器 | UI723项719P/3F/1S，3F均为原有受保护lab-test；晨报合成12项、真实13项、今日页旧路径56项、H1总览21项通过 |
| 正式任务 | `ce31bd13dc704cbf9e4647867721d921`，kind=pi，每天08:30、全部执行日、启用；真实run `fe67fb7f730c472392979bc419ba2cbd` succeeded |
| 新 PID | `36018`（原`77037`），主控放行后仅kickstart一次，已确认新接口字段加载 |
| 证据 | [证据目录](evidence/impl-hub-h3b/) |

## 交付与口径

- `clue_morning.py` 提供生成、受控补写、今日文件读取与任务登记参数。任务通过既有 `/api/schedules/save` 登记；`scheduled_tasks.py` 无本单改动。
- 只读当前仓库 `workspace/schedules/tasks.db` 的最近24小时 `succeeded/partial` 微信任务（`wechat_clues/wechat_radar`）运行，取顶层 `output.clues`，按ID去重后查 `clues.db.threads`。遵照主控本轮补充，再按同一窗口内 `threads.created_at` 筛新建线索，旧线索被新运行引用不会冒充新增。
- 商单最前并全列，合作需求直接保留线索库摘要；只读微信证据明确的 `speaker/sender_name/author` 字段，无字段标「未知」，不把群名、原文第一处匹配或邮件作者冒充发言人。
- 总数超过50时，其余类别各展示最新8条，并报告剩余数量；总数及各类别计数仍按全量计算。多类别线索分别计入各类别，分类计数之和可以大于去重总数。
- 落文件 `workspace/clue-morning/YYYY-MM-DD.md` 与 `.json`（北京时间日期）；元数据包含窗口、去重线索ID、运行ID、总数、类别计数、生成时间与正文SHA256。JSON最后原子发布，读取时核对正文摘要，文件对不一致不展示为成功。
- Pi 第一步调用 `--prepare` 生成并只收到计数、摘要与路径；第二步按计数写一句整体判断，通过 `--finalize --expected-sha256 ... --judgment ...` 补写。此入口只替换最后一句，不接收模型提供的线索正文或计数；元数据记录 `judgment.source=pi` 和生成时间。无新增固定「今日无新增」、来源为 `rules`，不让模型编造内容。
- 今日页入口位于顶部状态栏，展开为全文阅读浮层；发现和兴趣通道都可读，不混入条目列表。纯文本渲染，不执行晨报中的HTML。
- H1总览登记晨报文件心跳，计划每天08:30、60分钟宽限；核验同日期文件对与生成时间，不能仅靠触碰空JSON冒充成功。既有任务可操作行保留，心跳行只读。

## 自测与回归

| 项目 | 证据与结论 |
|---|---|
| 新增单测 | 16项：时间边界、partial、去重、精确取顶层clues、多类别、旧线索复用排除、限长、无新增、发言人来源、损坏结果/缺失库/缺失ID不冒充成功、文件摘要、Pi受控补写与过期摘要拒绝、H1跨日心跳 |
| 定向 | `tests.test_clue_morning tests.test_external_tasks tests.test_today_view tests.test_today_h3a tests.test_today_h3a_m1 tests.test_interest_channel tests.test_scheduled_tasks`，148项通过；[日志](evidence/impl-hub-h3b/targeted.log) |
| Python全量 | 3761项、0F/1E/7S；唯一E=`test_video_lab...test_multi_segment_sequence_video_builds_director_timeline`，`参考素材宽高须为32的倍数`，与通知基线相同；[日志](evidence/impl-hub-h3b/python-full.log) |
| UI全量 | 723项、719P/3F/1S，15.285秒；3F均在受保护`tests/test_lab_test_drama_ui.cjs`，与通知基线一致；[日志](evidence/impl-hub-h3b/ui-full.log) |
| 数量差异 | 相比3697通知基线增加64项，本单新增16项，其余48项来自共享测试集已有增量，本单未修改它们 |
| 晨报浏览器 | /tmp独立headless profile，1440/390，12项通过；[结果](evidence/impl-hub-h3b/synthetic-e2e-results.json) |
| 今日页旧路径 | 未修改原测试，56项通过；[日志](evidence/impl-hub-h3b/today-regression-browser.log) |
| H1总览 | 随机端口、/tmp工作区，21项通过；[结果](evidence/impl-hub-h3b/h1-browser-results.json) |
| 断言增量 | 主控允许H1登记数9→10、扫描数6→7；e2e同样按新增行增加确切计数，未放宽断言；[差异说明](evidence/impl-hub-h3b/h1-assertion-diff.txt) |

曾抓到并修复的本单回归：独占一行的晨报入口使1440×900首屏完整条目从5降到4。仅在/tmp复制旧版页面做对照（[日志](evidence/impl-hub-h3b/today-baseline-browser.log)），确认后把入口移入原固定高度状态栏，旧测试恢复5条及全56项通过，没有改旧测试断言。

## W1–W4 逐条结果

| 弱点 | 结果 |
|---|---|
| W1 取错位置 | 通过：嵌套干扰clues反例只认顶层；发言人只认微信具名字段，邮件作者、群名、正文均不冒认；文件名完整日期、正文摘要核验 |
| W2 mock泄漏/写真实数据 | 通过：新增单测全用/tmp SQLite、patch上下文与addCleanup；测试不写真实reports、LaunchAgents或tasks.db；正式任务登记与真实立即运行属于合同明确交付 |
| W3 凭据/隐私外露 | 源码与离线证据凭据模式扫描0命中；[结果](evidence/impl-hub-h3b/source-credential-scan.json)。正式task/run及Pi session凭据模式0命中；运行记录中线索完整标题/摘要0命中，[运行隐私扫描](evidence/impl-hub-h3b/runtime-privacy-scan.json)。Pi仅看计数和路径，真实晨报正文仅在产品文件；真实浏览器截图遮挡正文，报告和运行证据不贴聊天、昵称或微信号 |
| W4 放宽旧测试 | 通过：今日页原测试未改；H1固定计数因新增登记精确增加，相关diff见上，不删除或放宽旧行为断言 |

## 真实验收

主控在`msg_6031c138f377`放行后重启一次，PID从77037变为36018，真实`/api/today`已返回新`clue_morning`字段；[重启证据](evidence/impl-hub-h3b/restart.json)。

主控允许等待模型时先用CLI生成及预验：窗口`2026-10-29T05:50:18.434297Z`至`2026-10-30T05:50:18.434297Z`，新增971条，商单20、新技术403、热点141、项目进展73、其他357。独立SQL同时核对线索ID集合、总数、类别计数及正文计数，与JSON完全一致；[SQL证据](evidence/impl-hub-h3b/cli-preview-sql-verification.json)。预验的判断来源为`rules`，不把它冒充Pi交付。

重启后真实页面预验1440/390共10项通过（完整正文逐字相等、独立入口、无混排、无横向溢出、无脚本错误），截图遮挡聊天正文；[结果](evidence/impl-hub-h3b/real-cli-preview-e2e-results.json)。

模型空出后，从真实工作台按钮再次点击「立即运行」，run `fe67fb7f730c472392979bc419ba2cbd` 于`2026-10-30T05:54:46.103061Z`开始、`05:55:54.970062Z`结束，状态`succeeded`。Pi补写判断的时间为`05:55:52.464747Z`，元数据`judgment.source=pi`；[运行证据](evidence/impl-hub-h3b/real-run.json)。未换模型、未取消其他任务、未再次重启。

最终晨报窗口`2026-10-29T05:55:34.489249Z`至`2026-10-30T05:55:34.489249Z`，**971条；商单20、新技术403、热点141、项目进展73、其他357**。独立SQL再次核对ID集合、总数、各类别计数、正文与元数据，全部一致；[最终对账](evidence/impl-hub-h3b/sql-verification.json)。本报告只列条数与结构，不贴真实线索正文或发言人。

最终真实浏览器13项通过，包含页面运行记录与Pi来源验证；1440/390点开正文均与API全文逐字一致、无混排/横向溢出/脚本错误；[结果](evidence/impl-hub-h3b/real-e2e-results.json)、[运行记录截图](evidence/impl-hub-h3b/real-run.png)、[1440入口](evidence/impl-hub-h3b/real-1440-collapsed.png)、[390展开（隐私遮挡）](evidence/impl-hub-h3b/real-390-expanded.png)。已目视检查截图。正式H1缓存登记可查且`status=ok`，缓存最多5分钟更新，现场快照仍显示CLI预验时间，符合既有缓存规则；[心跳](evidence/impl-hub-h3b/real-heartbeat.json)。

### 模型排队与08:30计划

最初两次真实「立即运行」在claim前返回HTTP400，原文为「同一模型已有后台任务，排队等待，不重复占用推理服务」；没有生成晨报运行记录。只读确认是既有`deals_review`占用相同默认模型；[错误证据](evidence/impl-hub-h3b/initial-model-busy.log)。主控指示保持默认模型、不动4090，等商单结束再点；未取消或修改其他任务。

每天08:30是触发时间，不保证文件恰在08:30生成。同模型忙时，自动定时任务保留到期状态等待模型；手动按钮会提示忙，需稍后重试。H1心跳宽限为60分钟，文件实际生成时间在JSON中可查。

建议暂不改08:30：当前同日08:30的另一任务是script，不占模型；同模型日任务在09:30，其余主要是间隔任务，运行相位会随真实执行漂移，单纯固定后移15分钟不能保证消除碰撞。先复用合同要求的连续3天巡检，记录实际生成时间与排队延迟；若出现超过60分钟的延迟，再根据实际空闲窗口决定错开时间，本单不自行改时间或调度循环。[同模型计划只读快照](evidence/impl-hub-h3b/same-profile-schedules.json)。

## 边界与后续

- 不提交、不推送，不改H3a过滤/闸门文件，不动账号、飞书、Grok设置或其他采集任务。
- 连续3个自然日运行记录由主控巡检收集；本单不等待3天。
- 回滚仅移除本单入口/晨报模块/心跳登记并通过任务API暂停晨报，既有任务与线索库不变；线上回滚重启仍服从主控窗口安排。
