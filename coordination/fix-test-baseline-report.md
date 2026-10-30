# 全量测试基线修复报告

| 决定要点 | 结论 / 证据 |
|---|---|
| 结论 | 【通过】按主控调整后的门槛；保护视频项另列，未冒充原门槛全绿 |
| 风险 | H/M/L：0H / 0M / 0L（本单调整范围内）；保护视频线仍有 1 个 Python ERROR、3 个 UI FAIL，未验收 |
| Python 开工 | 3560 项，4F / 86E，7 skipped；90 条失败记录对应 35 个方法 |
| Python 交付 | 共享3561项：0F/1E/7S；干净克隆3348项：0F/1E/61S，唯一ERROR均为保护video_lab；范围内0F0E |
| UI / 浏览器 | 正式目录与干净副本均 723 项：719 pass、3 个受保护视频 fail、1 个既有 skip；页面 e2e 两处均 34/34 |
| 业务修复 | 经主控 ask 明确放开 collect_adapters.py 的 start / _run_job 收尾；实际只改 start，旧代码确定性 ERROR → 新代码通过；39 项 × 50 轮全过 |
| 文件 / SHA | 1 个授权业务文件、11 个测试文件、7 个自带夹具/来源文件；完整 SHA 见 fix-test-baseline-evidence/sha256.json；本轮补丁 SHA256 f6d5779a6b146003db5aa3948c1996ac6cf43e6b3c4afc8a46c9756435d1eb18 |
| 边界 | 视频测试恢复原字节；不提交、不推送、不碰 8766 / 用户桌面 / 真实 Chrome profile，不输出服务环境变量 |

本单 Task `task_588ab352a772`，Dispatch `ctx_199ff7b07bf7`。报告、证据和补丁为本单新增；未覆盖协调者的队列或台账，也未把工作区其他人的修改算成本单。

## 授权与门槛变更

原合同仅允许 `tests/**`。发现真实 AIsa 竞态后，按第 3 条先通过 `orca orchestration ask` 报主控。主控回复：允许修改 `collect_adapters.py` 中 `_run_job` 和 `start` 的锁与状态收尾，必须先证明确定性反例在旧代码失败、新代码通过，再完整循环跑 50 次 `test_aisa_live_chain`。实际改动只落在 `start` 第 403–422 行，未移动终态写入、未提前释放工作锁。

第二次 ask 后，主控明确暂停视频相关修改：`public/lab-test.*`、`generation/*`、`tests/test_*lab*` 是他人在途工作，保持原样；门槛调整为“除受保护的视频项外，全量 0F0E”。本单曾临时核对 lab-test 的新断言，收到裁定后完整撤回。`tests/test_lab_test_drama_ui.cjs` 与任务前保留副本字节相同，`test_video_lab.py`、其他 lab 测试及视频业务文件没有本单增量。

夹具原始历史轮次已经丢失，主控同意从正式库只读导出真实样本，保留 54 次精确断言。实际选择现存的 2026-10-16 03:00 UTC 真实轮次，不伪称 10 月 15 日旧交付原件；来源、完整原始 SHA、裁剪规则在 `tests/fixtures/radar_monitor/README.md`。

## 根因分类和修法

| 分类 | 失败项 | 已验证根因 | 本单处理 |
|---|---|---|---|
| ① 测试自身 | b1b2 的 11 方法 / 66 个子测试 ERROR；b1b 的 1 FAIL | 写死 `/tmp/viral-b2b-deliver`、`b2b2-deliver`，重启后不存在 | 用可追溯已合入 Git 419dbc2 完整消费端与当前正式消费端做双版本边界/投影一致性测试；原具体数值、只读 SHA 检查和两端断言全保留 |
| ① 测试自身 | b1c 的 18 ERROR | 所有 setUp 读取已清理的 round-1 真实证据 | 自带真实快照子集：51 个真实账号、54 次调用、4 条真实帖子；身份、指标、时间不造值；变异只在临时目录，按裁剪后字节重算测试封装 SHA |
| ① 测试自身 | FeishuSenderTests.test_16_integration_with_b1_monitor | 历史 B1 `/tmp` 交付已丢失，原模拟刷新器也不产出当前 A1 权威 manifest | 复制当前正式 B1 源码到独立根，使用既有 commit_fixture 封装模拟采集输入；走真实 B1→发送器子进程→随机回环假飞书，原送达/不重发/账本断言均保留 |
| ① 测试自身，克隆新增暴露 | intel_job_ownership 回滚组及变异红例 | 旧 intel_store 和真实回滚脚本藏在不入库的 coordination 中；缺脚本退出码2还可能被误当作正确拒绝 | 两份真实原文件逐字节归档到 tests/fixtures/intel_ownership，记录来源SHA；仅改测试路径、保留原断言，新增脚本存在的前置断言；相关8项定向通过 |
| ① 测试自身 | C02TimeoutKeepsOldTimestamps | 2026-10-15 固定帖子相对于真实今天已超 48 小时，首轮 vip_tweets 为空，next 抛 StopIteration | 只替换 entry.now_utc 为该夹具的 BASE；旧帖 fetched_at、stale、覆盖率、两轮原子提交的原断言不动 |
| ① 测试自身 | C14NoFabricatedTopics 随机 233 | 对整包 JSON 查子串，随机 run_id / SHA 可合法包含 233 | 禁词检查精确覆盖帖子/特关帖/话题展示内容；固定 run_id 含 c187c233 并断言其真实出现，确保反例不再靠概率复现；所有空列表/零计数原断言保留 |
| ① 测试自身 | AlbumSearchTests.test_the_link_itself_is_never_stored 随机 deadbeef 隐患 | 全列字符串扫描包括 UUID，uuid4 可能碰中 URL 中的 chksm 值 | 仅替换 intel_store 的 uuid 模块绑定，固定生成 f×32；仍扫描整行所有列，保留 deadbeef/chksm 原禁词，并新增精确 UUID 断言 |
| ① 测试自身 | ProxyNetworkGuardTests | 模块导入时构造的 opener 已捕获宿主代理；运行中 patch 环境并不替换旧 ProxyHandler，实际抛 URLError | 在假代理环境中重建相同的不跟随重定向 opener，强制不 bypass；真实 transport 触达护栏，新增断言恰好检查 127.0.0.1:9，保留 AssertionError 原断言 |
| ① 测试自身 | RepairBoundaryTests.test_h1_cumulative_budget_and_unsent_remainder | 0.28 秒预算还要负担 Python 子进程冷启动，第一张 .16 秒延迟响应也可能未完成，0 结果并非累计预算失效 | 用模块专属虚拟 monotonic 与明确 .16 秒传输代价验证累计预算；每张卡真实规划/写账本仍走产品代码；恰好两次传输、同一截止 1000.28、首 10 sent / 次 10 sending / 第 21 条未尝试。实际网络截止仍由原 drip/headers/lock 测试覆盖 |
| ① 测试自身 | test_radar_links_e2e.cjs | 顶层 require 缺少 playwright，依赖正式 8766 / 真实数据、无时钟固定，原来空数组也可能过关 | 与既有 e2e 一样使用运行时 Playwright；随机端口、独立 headless context、只读内嵌真实快照、冻结采样时钟、外站 abort；三个视图必须真实出现链接，每个原帖/作者主页都与真实源行精确匹配；保留六个旧坏 ID 禁令，检查 pageerror=0 |
| ① 测试自身 | 页面 e2e M3a–M3d | 仍要求历史内嵌快照在“今日”榜非空，但 NOW=真实今天 | NOW 绑定所加载内嵌快照的 updated_at；34 场景的原断言原样保留。缺列表/错类型仍要求整页退回旧数据、不显示新 run、不混用新覆盖率 |
| ② 真实业务竞态 | AIsa ReworkB 刷新偶发 | 89277b7 引入：终态先提交数据库，finally 随后才 close 锁 FD；下一 start 看见 done，却因忙锁报错 | 经授权在 start 忙锁分支区分“仍运行→复用”与“终态收尾→重试拿真实锁”；锁交接前绝不启动第二执行者；异常路径关闭本次 FD |
| ③ 断言过时，受保护 | lab-test 的剧本两项与默认配置一项 | 当前预设已改为结构化中文荒岛/极客实验室，默认变为 Hybrid/none/5 秒；旧断言仍要求英文荒岛/赛博追杀、默认 Singularity/ref4 | 主控裁定保留，不修改、不 skip、不放宽 |
| ③ 断言过时/夹具缺字段，受保护 | VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline | 模拟素材缺 width/height，现行原生解析器先报“参考素材宽高须为 32 的倍数”；原 Director engine / graph['5'] timeline 形状也已被 AICG3D 原生段链取代 | 主控裁定保留，不改测试或业务来掩盖 |
| ② 真实边界矛盾，受保护 | 15 秒选项补充核对 | lab-test.js getSelectedFramesForSeconds(15)=362；video_options.py H3_FRAMES_MAX=345，源码说明 4090 显存边界。默认配置 FAIL 之外另有真实配置矛盾 | 先报主控，未擅自把 345 断言改成 362；主控另报黄总决定，本单不修 |

`test_write_from_item` 旧 L1 断言已由前一轮更新，本次开工全量已通过，未再改。

## 验证与证据

- Python：命令均为 `../article-importer/.venv/bin/python -m unittest discover -s tests -v`；完整原日志在 `/tmp/fix-test-baseline-588ab/`，开工/交付/克隆的全部测试 ID 清单和原失败到当前状态映射保存在本报告旁的 `fix-test-baseline-evidence/`。
- 干净副本：`/tmp/fix-test-baseline-588ab/clean/article-studio`，真实 `git clone --local --no-hardlinks` 后覆盖当前代码清单（含本单及他人已有未提交代码），不带正式 workspace、coordination、work 或历史 /tmp 交付。复制 article-importer 的 7 行兼容入口，仅链接 JS node_modules、Pi node_modules 和既有 Python 虚拟环境；不把依赖目录当测试数据。首次把 importer 整目录链接导致来源哨兵发现正式源码串入，该次结果作废并仅停止本单进程；修正后模块来源哨兵通过，再从头跑最终全量。606个代码文件与共享工作区逐字节SHA一致，清单归档到 `fix-test-baseline-evidence/clone-code-sha256.json`。
- UI：原开工 723 项 718 pass / 4 fail / 1 skip；交付及干净副本均 719 pass / 3 fail / 1 skip，失败集合精确等于主控视频例外。
- 隔离 Playwright 页面：工作区 34/34、克隆 34/34；pageerror=0、console.error=0、外网请求=0。截图与 results.json 分别在 `/tmp/fix-test-baseline-588ab/e2e-after/`、`clone-e2e/`。
- AIsa：`lock-regression-before.log` 旧代码确定性 1 ERROR，原因“已有 AIsa 采集正在启动，请稍后查看”；新代码同一测试 1/1。50 个独立 Python 进程每轮完整 39 项，合计 1950 项通过（约 248 秒），见 `aisa-50.log`。原全局单飞测试继续通过；新增反例在旧执行者被刻意卡在终态收尾时确认 transport 仍只有 1 次，解除后下一请求成功且总共 2 次。
- 共享目录全量后，最后只对 AlbumSearch UUID 固定、真实雷达夹具进一步缩减、所有权历史脚本夹具归档作了测试自身调整，分别定向 1/1、18/18、8/8 通过；克隆最终全量覆盖这两项的最终字节，未重复跑无关模块。
- 本单不部署、不重启正式服务。循环的每个新解释器和 HttpChainTests 的随机端口后端均重新加载新 collect_adapters.py；没有拿旧常驻服务的结果充当新代码验证。

## G1–G4 自测与边界

- G1：记录开工 3560 个 ID，逐个列出原 90 条失败记录对应的 35 个方法，交付状态与修法见 `original-failure-status.md`；不只报数字。新增确定性竞态方法单列。
- G2：没有新增 skip、expectedFailure、吞异常、存在即通过。Python AST 断言比较见 `assertion-audit.json`：只有 Feishu 的 `assertLessEqual(len(server.requests), 2)` 被替换为 `assertEqual(len(attempts), 2)` 并增补截止和账本状态检查，其余旧断言 AST 全保留；删除/弱化断言为 0。C14 仅修正被检查对象的业务边界，不删除空值、零值或禁词规则。页面 e2e 只改时钟，所有断言不动；链接脚本原退出检查改为可计数的 node:test 断言并增加非空和真实映射检查。
- G3：真实来源和 SHA 写入夹具 README；没有编造失落轮 1，也没有手写 54 次的假历史记录。重新生成的封装摘要明确标成测试摘要。所有权旧实现/回滚脚本也按原文件SHA逐字复制。旧 B2 两份未合入版本未冒名恢复，替代版本明确是 Git419dbc2 与当前模块。
- G4：修复范围测试在干净副本运行，不依赖正式数据、旧交付目录或外网；只使用随机回环测试服务和独立 profile。固定浏览器时钟不是改产物时间。已有缺本机外部契约而 skip 的用例完整列在 `fix-test-baseline-evidence/skipped-tests.md` 与 `skipped-tests.json`，不新增 skip 来隐藏失败。克隆因既有模块级守卫少执行213项，覆盖不同；原失败35个方法均实际执行且未被跳过。
- 视频保护核验：`test_lab_test_drama_ui.cjs` 与任务前 `/tmp/review-write-p3/repo/tests/test_lab_test_drama_ui.cjs` `cmp` 相同；未改 `test_video_lab.py` 本单增量。完整工作区原本有大量他人在途文件，本单补丁由开工快照逐文件差分生成，只包含本单范围。

## 完整清单

- [干净克隆全部测试 ID](fix-test-baseline-evidence/clone-python-ids.txt)
- [完整测试统计](fix-test-baseline-evidence/test-summaries.json)
- [跳过原因与覆盖差异](fix-test-baseline-evidence/skipped-tests.md)
- [共享目录交付全部测试 ID](fix-test-baseline-evidence/python-after-ids.txt)
- [每个原失败的状态与修法](fix-test-baseline-evidence/original-failure-status.md)
- [开工全部测试 ID](fix-test-baseline-evidence/python-before-ids.txt)
- [本轮补丁](fix-test-baseline-evidence/task.patch)
- [工作区浏览器结果](fix-test-baseline-evidence/e2e-workspace-results.json) / [克隆浏览器结果](fix-test-baseline-evidence/e2e-clone-results.json)
- [所有权回滚定向8项](fix-test-baseline-evidence/ownership-fixture-after.log)
- [文件 SHA256](fix-test-baseline-evidence/sha256.json)
- [断言 AST 自查](fix-test-baseline-evidence/assertion-audit.json)
- [随机子串扫描](fix-test-baseline-evidence/random-substring-audit.json)
- [旧代码竞态反例](fix-test-baseline-evidence/lock-regression-before.log) / [新代码](fix-test-baseline-evidence/lock-regression-after.log)
- [完整模块 50 轮](fix-test-baseline-evidence/aisa-50.log)

尚未提交或推送任何代码，未替主控声明整条项目已验收或部署。


## 返修

| 决定要点 | 结论 / 证据 |
|---|---|
| 结论 | 【自检通过，待独立复核】H1 有界等待、L1 补丁损坏均已修复 |
| H / M / L | 本轮自检 0H / 0M / 0L；受保护视频例外仍保留 |
| 业务变化 | 顶部 `AISA_START_LOCK_WAIT_SECONDS=0.5`；仅 `start` 方法变化，`_run_job` 未变 |
| 旧→新真实 HTTP | 旧：2.005 秒仍阻塞、924 次 job 读取；新：约 0.509 秒 HTTP400，持锁期间零采集 |
| AIsa 连续 50 轮 | 每轮原模块39项＋新增4项，2150/2150通过；338.777秒 |
| Python 全量 | 3565项 / 378.226秒：0F / 1E / 7S；仅受保护 video_lab，skip集合与上轮相同 |
| 浏览器用户路径 | 随机端口54233，忙锁0.549秒提示可恢复；释放锁后重试202，页面真实新增1条；pageerror=0、外网=0 |
| 文件 / SHA | 本轮代码2文件；collect_adapters.py：`7fa4e5ae8b53d3888e3fdd850556f7513f1edc0aafa390756e799b08399fd2dd`；测试SHA见下方清单 |
| 补丁 | 20文件完整补丁，`git apply --check` exit0；应用后20文件字节全匹配；SHA256 `d1310d374a4b508c7a0d0caf5dd83e8b59854ccbc96c8d799bcc257967a138cf` |

返修 Task `task_0f4063da0a0f` / Dispatch `ctx_df91171ccd58`，对应复核 H1/L1。前文保留为上一轮交付记录；本节及更新后的 SHA 清单、task.patch 为当前交付状态。没有提交、推送、触碰8766、访问真实桌面或真实Chrome profile。

### H1 修复与反例

修改前先增加固定0.8秒验收上限的回归测试。外部独立Python进程真实持有同一临时工作区的`aisa/manual.lock`，数据库已有done回执；旧代码在0.800秒仍有start线程存活，测试明确FAIL，解除锁后才清理。另直接运行复核员的真实`server.Handler` HTTP探针，旧代码2.005秒未返回，栈停在start的sleep，924次读job，transport=0。原始脚本、失败日志与HTTP结果均已归档，未仅凭测试数字推断根因。

新代码用monotonic计算0.5秒绝对截止；只有终态才在剩余预算内以最多10ms间隔重试真实flock。每次失败都先读取job：queued/running立即复用；终态超过截止或尚无回执时，抛出原有`ValueError('已有 AIsa 采集正在启动，请稍后查看')`。成功拿到真实锁后才建新任务，没有提前释放旧执行者的锁。所有未移交worker的路径统一通过finally关闭fd，删除分支内提前close，避免重复关闭。

新增4项：①done＋外部长期持锁的有界错误及fd精确关闭；②等待期间其他执行者发布queued时复用、不采集；③拿锁后尚无queued回执时立即返回忙碌；④真实随机端口HTTP在持锁者尚未释放时返回400。原来的`test_terminal_job_allows_next_start_before_final_writer_returns`原字节不变，持续覆盖done已写、旧执行者很快释放锁后新请求能启动。

新HTTP探针0.509秒返回400，36次job读取、transport=0；fd审计28open/28close、重复关闭0、最终存留0。12个并发请求均复用同一running job，实际transport1次、最大并发1；worker启动失败也无fd残留。50轮中的新增HTTP请求耗时范围0.502～0.509秒，全部在固定0.8秒测试上限内。

### 验收与独占

全量前通过本Dispatch的`orca orchestration ask`申请，主控回复“已登记独占，现在可以运行。复核窗口空闲，没有其他全量在跑。”执行`../article-importer/.venv/bin/python -m unittest discover -s tests -v`，3565项0F/1E/7S。唯一E为`test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，仍报“参考素材宽高须为32的倍数”；7个skip的ID和原因与上轮共享目录全量完全相同，没有新增。结束后用status消息通知主控释放独占。

50轮每轮启动两个独立解释器：`test_aisa_live_chain.py`39项和`test_aisa_lock_wait.py`4项，共2150项，50轮全部0F0E。每轮完整测试ID与原始日志留档。Python全量与稳定性循环为本Dispatch执行，未同时运行第二份全量。

真实页面验收使用新启动的随机端口后端和隔离headless Playwright context，实际打开`/clues.html`，点击“AIsa手动采集”→选择小红书→“立即采集”，在外部进程仍持锁时得到400并显示原中文忙碌错误，按钮恢复可点击；释放锁后再次点击得到202，随后页面出现新增1条及1张AIsa线索卡片。后台transport仅使用离线合成网关回执；没有访问8766或真实账号。新解释器与后端均在修改后启动，确实加载新代码。

### L1 补丁与范围

重新用Git原生差分生成`task.patch`，不再手工拼接无末尾换行的文件。完整补丁基于原任务开工留底的12个原文件，新夹具/测试在基线中不存在；在`/tmp/fix-test-baseline-rework-0f406/patch-check`构建干净目标文件副本，`git apply --check`退出0，实际应用后20个文件与交付文件逐字节一致。`git apply --stat`可解析：20文件、3272行新增、97行删除。副本没有修改共享目录或受保护文件。

本轮相对于返修前的`git diff --stat`为：

```text
 collect_adapters.py          |  19 +++---
 tests/test_aisa_lock_wait.py | 152 +++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 161 insertions(+), 10 deletions(-)
```

AST比较确认业务方法仅start变化；唯一方法外新增为明确要求放在模块顶部的等待常量。原`tests/test_aisa_live_chain.py`无本轮增量，29个受保护文件逐字节SHA相同。完整补丁含上一轮已交付的测试/夹具，不能把它的20文件误作本轮新增范围。

### 已知弱点逐条自测

| 项 | 结果 | 实际证据 |
|---|---|---|
| G1 不能只看数字 | 通过 | 旧真实HTTP2.005秒仍阻塞，新HTTP0.509秒返回；真实页面点击路径0.549秒提示忙碌且可重试 |
| G2 不调大超时/skip/放宽断言 | 通过 | 新测试固定0.8秒；生产等待上限0.5秒；旧测试字节不变，0新增skip，7个既有skip集合不变；初次新增测试对idle空回执写错预期，按真实接口精确修正为status=idle/available=True，未削弱产品行为要求 |
| G3 最小业务范围 | 通过 | 仅start及顶部等待常量；_run_job未变，其他业务文件未改；29个保护文件SHA一致 |
| G4 差分证明范围 | 通过 | 本轮git diff --stat：2文件161新增/10删除；完整交付patch：20文件，可干净应用并逐文件核验 |

本轮证据位于[rework目录](fix-test-baseline-evidence/rework/)：

- [旧代码失败](fix-test-baseline-evidence/rework/regression-before.log) / [新代码4项](fix-test-baseline-evidence/rework/regression-after.log) / [旧HTTP](fix-test-baseline-evidence/rework/http-before.json) / [新HTTP与fd审计](fix-test-baseline-evidence/rework/fd-after.json)。
- [50轮汇总](fix-test-baseline-evidence/rework/aisa-50-summary.json) / [50轮完整日志](fix-test-baseline-evidence/rework/aisa-50-details.log)。
- [全量汇总及skip](fix-test-baseline-evidence/rework/python-full-summary.json) / [全量完整日志](fix-test-baseline-evidence/rework/python-full.log)。
- [真实浏览器结果](fix-test-baseline-evidence/rework/browser-results.json) / [忙碌截图](fix-test-baseline-evidence/rework/browser-busy.png) / [重试截图](fix-test-baseline-evidence/rework/browser-retry.png)。
- [干净应用校验](fix-test-baseline-evidence/rework/patch-check.json) / [本轮差分统计](fix-test-baseline-evidence/rework/rework-stat.txt) / [业务与保护范围](fix-test-baseline-evidence/rework/scope-check.json) / [当前20文件SHA](fix-test-baseline-evidence/sha256.json)。
