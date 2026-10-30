# 全量测试基线修复复核报告

| 决定要点 | 结论 / 证据 |
|---|---|
| 结论 | 【不通过】存在 H1：终态遇忙锁时 HTTP 请求无界等待 |
| H / M / L | 1H / 0M / 1L；L1 为交付补丁格式损坏，不是业务阻断 |
| Python 门槛 | 3348 项：0F / 1E / 61S，唯一 ERROR 为受保护 video_lab |
| UI / 浏览器 | 723 项：719 pass / 3 个受保护视频 fail / 1 skip；隔离 Playwright 34/34 |
| AIsa 稳定性 | 20/20 个独立解释器通过，39 项 × 20 = 780 项；不覆盖 H1 的长期持锁边界 |
| 断言 / 夹具 | 抽查 5 文件，断言调用 776→783，0 新 skip；真实 51 账号 / 54 次调用 / 4 帖子子集核对通过 |
| 文件 / SHA | 交付 19 文件 SHA 全匹配；业务只改 start；collect_adapters.py SHA256：5a4f2cd1f8943d463aeff4f99ea0465411006737fd34afb355969a2e93c321d3 |

复核任务 `task_5eaea172d944` / Dispatch `ctx_a7d9d327ade9`；被复核实施任务 `task_588ab352a772`。只写本报告，不修改产品代码，不提交、不推送，不重启或访问 8766，不使用 computer use 或真实 Chrome profile。

## 逐条结论

| 要求 | 结论 | 依据 |
|---|---|---|
| 1a 终态 + 长期忙锁能有限时间返回 | **失败** | H1，真实随机端口 HTTP 请求被锁持续阻塞；代码没有超时/有限重试 |
| 1b fd 无重复关闭/完成后泄漏 | 通过（已测路径） | 28 次 open / 28 次 close；重复 close 0；完成/失败后存留 0 |
| 1c 不同时启动两个执行者 | 通过（代码与反例） | flock 持有至 _run_job 最终收尾；12 并发请求均复用，实际 transport 1 次，最大同时执行 1 |
| 2 G1/G2 反弱化与真实夹具 | 通过 | 5 份测试对 HEAD 抽查、原失败 ID 核对、来源 SHA 和逐字段子集核验均通过；下文说明两处测试边界变化 |
| 3 Python / UI 调整后门槛 | 通过 | 全量与 UI 只剩受保护视频失败；skip 原因已列 |
| 3 AIsa 连续 20 轮 | 通过 | 20/20 轮，780/780 项 |
| 4 范围与视频保护 | 通过 | 19 文件本单差分均在授权范围；29 个受保护文件与任务前留底逐字节相同 |

## H1：终态被误当成“锁一定马上释放”

位置：`collect_adapters.py:403–420`，尤其 `410–418`。

确定性反例直接使用独立副本的产品 `server.Handler`、`AIsaRadarAdapter` 和真实 SQLite，监听随机回环端口。先写一个旧 `done` 回执，再启动单独的外部 Python 进程持有同一工作区 `aisa/manual.lock`，它只在复核脚本明确通知后释放；向 `POST /api/collect/aisa/run` 提交另一个有效 request_id。

观察：2.025 秒后请求线程仍存活、结果数为 0，`job()` 已调用 988 次，transport 为 0。服务器线程栈是 `server.py:_post_routes → collect_adapters.py:start:417 → sleep(0.001)`。通知外部进程释放锁后，同一请求才返回 HTTP 202 并实际执行 1 次 transport。

有限时实测证明请求不能在持锁期间返回；结合 `while True` 中终态分支只有 sleep/continue、没有截止时间，可确定：只要持锁者不释放且最新 job 保持终态，请求就会无限等待。它不要求外部进程一定存在：旧执行者写完 done 后卡在最终返回，或新执行者拿锁后尚未提交 queued 时卡住，也满足相同状态。客户端超时不会替服务器线程执行取消逻辑。

原实现对此分支会快速抛出“已有 AIsa 采集正在启动，请稍后查看”；本次补丁将此情况改成了无界等待。现有新增测试主动 release 旧执行者，只证明锁最终释放后能交接，没有覆盖锁不释放。

返修要求：忙锁等待必须有界，超出后给可恢复的忙碌结果或明确错误；不能因为数据库显示终态而跳过真实文件锁。保留已有 done-before-close 交接反例，补长期持锁和拿锁尚未写 queued 的反例，验证接口能有限时间返回且始终单执行者。这里只提出行为要求，不替实施者改代码或指定具体超时常量。

## 锁与描述符核验

在仓库执行 `git grep -n 'manual\.lock'`，并用 `rg --hidden --no-ignore` 扫描 Python / Shell / JS 源码、排除依赖和数据目录，均只有两处：

- `collect_adapters.py:359`：`job()` 检查孤儿 queued/running 回执；其临时 fd 由 finally 关闭。
- `collect_adapters.py:401`：`start()` 获取工作锁，并将成功拿到的 fd 交给 `_run_job`，后者在最终写状态后 finally 关闭。

没有发现仓库中另一个定时采集入口直接拼用 `manual.lock`；这不代表仓库外进程无法持锁，本次反例已经真实持锁。

复核只替换模块自己的 os 绑定，记录每次 open 对应的 fd 生命周期，没有替换 flock、DB 或 transport 的互斥机制。覆盖终态等待后释放、12 并发复用 running、线程启动失败：28 open / 28 close，0 重复 close，结束后 0 存留。无限等待期间会一直占用本请求的 1 个 fd 和请求线程；它是 H1 的资源占用，不应被“释放后无泄漏”掩盖。

单执行者验证：首个 transport 人为保持运行，12 个并发 start 全部复用其 job_id，transport 共 1 次、最大并发 1。静态检查确认拿不到 flock 不会进入插入 queued/启动 worker；`_run_job` 未改、没有提前关锁。因此本轮发现的是无界等待，没有发现绕锁并发执行。

## G1/G2：五份测试与断言边界

基准 HEAD 为 `22218a71757b1217acce5491e754152e29031ab8`。对以下 5 份修改测试逐一查看 `git diff HEAD`，并独立做 AST 断言调用、测试方法和 skip/expectedFailure 比较：

| 文件 / 抽查用例 | 断言调用 HEAD→当前 | 测试方法数 | 复核结论 |
|---|---:|---:|---|
| `test_aisa_source.py` / 代理网络护栏 | 142→143 | 61→61 | 保留空返回和 AssertionError，额外锁定访问 127.0.0.1:9；重新构造 opener 避免捕获宿主代理 |
| `test_radar_feishu.py` / 累计预算 | 148→152 | 32→32 | 固定每次 .16 秒成本、总 .28 秒预算；恰好两次尝试、同一绝对截止、首 10 sent / 次 10 sending / 第 21 条不尝试 |
| `test_twitter_radar_foundation.py` / C14 | 183→184 | 29→29 | 固定 run_id 含 233；空列表、0 计数、51 empty 均保留；展示内容禁词仍检查 |
| `test_radar_monitor_b1c.py` / 原始回执字段 | 52→52 | 18→18 | 只改夹具位置；`assertEqual(result['api_calls'],54)` 原样保留，变异校验仍在 |
| `test_intel_status.py` / 链接不可落库 | 251→252 | 41→41 | 只固定 UUID；仍扫描整行所有列检查 deadbeef/chksm，新增精确 UUID 断言 |

5 文件合计 776→783 个静态断言调用；没有新增 skip/expectedFailure，没有删测试方法，没有把预期值替换成读取实现结果。

不能把“断言 AST 没删”写成绝对事实：Feishu 的 `len(server.requests) <= 2` 确实被替换为 `len(attempts) == 2`，并增加相同截止和落账状态检查。该用例由真实延迟 HTTP 改为模块专属虚拟时钟验证累计预算；真实 HTTP 的 drip / headers deadline、响应上限、锁等待仍由同文件未改用例覆盖。该拆分消除了子进程冷启动导致第一张卡都未完成的噪声，没有减掉累计预算的行为约束。

C14 的检查对象从整个 JSON 收窄到 `tweets / vip_tweets / topic_clusters`，是合同已明确允许的“只检查帖子和话题字段”修法；随机 run_id/SHA 不属于展示内容。原来的具体禁词、空列表、零计数断言均保留，并固定碰撞反例。这里是有依据的边界修正，不是把期望值改成跟随实现。

开工 90 条失败记录对应的 35 个方法在最终全量中全部实际执行：34 OK、1 个受保护 video_lab ERROR；0 个跳过、0 个缺失。没有用增加 skip 隐藏原失败。

## 真实夹具与 SHA

只读读取 README 指定的正式历史 JSON，未访问外网、未写正式数据；逐字段验证夹具是原件子集：51 个账号、4 条帖子，`api_calls=54`，各账号 calls 合计也为 54。

- 原始 snapshot SHA256：`e8f367740a31b90c922bbec36c8a557e1a5378c655906b6f3fe0a37cc7d7c337`。
- 原始 bundle SHA256：`dc4dd4e15319f21b1d84369c7a6189df80e7d764c331e107ce9378adf8fffb40`。
- 历史消费端夹具与 `git show 419dbc2:radar_page_api.py` 逐字相同，SHA256 均为 `4c2b1e52baf5c6f1040f66913bff1da9dc39f3c4a46a05d6cbf7b7817b0ff9ca`。
- 两份所有权源码夹具分别与其 README 所列 coordination 原件逐字相同：`ce87b0cc702023f22cec55362995f4c61e3d3b508acea5931614a5b188f219f1`、`b58409680896268f740706f34aa62d5a9cafee368d2073ae8507f0051042a25b`。

54 是被严格断言的 API 调用总数，不是该测试文件的静态 assert 数；未将它改成“非零”或从实现返回值生成期望。

## 独立执行记录

副本：`/tmp/review-fix-test-baseline-5eaea/article-studio`。使用本地 `git clone --local --no-hardlinks`，覆盖交付的 606 个代码文件，全部 SHA 与交付 manifest 相同；没有带入正式 workspace、coordination、work 或历史交付数据。依赖仅链接 node_modules、Pi SDK 和指定 Python venv，article-importer 兼容入口复制到副本兄弟目录。

先通过本 Dispatch 的 `orca orchestration ask` 申请全量独占，主控明确回复“已登记独占，现在可以运行”。最终全量完成后已通过 heartbeat 通知主控释放独占。

| 验证 | 实测结果 |
|---|---|
| `../article-importer/.venv/bin/python -m unittest discover -s tests -v` | 3348 项 / 361.781 秒，0 failures / 1 error / 61 skipped；唯一 ERROR：`test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，报“参考素材宽高须为 32 的倍数” |
| `npm run test:ui` | 723 项，719 pass / 3 fail / 1 skip；3 fail 均为 `test_lab_test_drama_ui.cjs` 的荒岛、赛博追杀、默认 singularity 旧断言 |
| 隔离页面 `tests/e2e/twitter_radar_page.cjs` | 34/34；pageerror=0，console.error=0，外网请求=0，随机端口 64393；浏览器独立 context / headless |
| 连续 20 个独立解释器的 `test_aisa_live_chain` | 20/20 轮全部通过，每轮完整 39 项，合计 780 项，0 failures / 0 errors；自然耗时 99.536 秒 |

首轮试跑确实得到 3348 项 / 1F1E61S：助手取消用例在 30 秒内未到模型请求；该轮启动时 Pi SDK 链接尚未全部准备，因此不作为最终门槛。依赖齐全后该用例定向 1/1 通过；重新校验 606 个代码 SHA 不变后从头执行最终全量。保留原日志，未将该失败划入视频例外，也未声称单凭定向通过就证明其唯一根因。

最终 61 条 skip 与实施者干净克隆的 skip ID 集合完全相同，0 个新增。原因分类：微信来源插件缺失 32；私有 fixture/协调工具/迁移工具/证据/工作副本缺失 19；外部 H3 schema 1；MEDIA_API_SOURCE 相关 5；私有 work/x-dom-test 下 jsdom 缺失 3；外部微信 helpers 契约 1。副本依照既有守卫跳过这些本机依赖，不能据此声称它们也已实际覆盖。

## G3/G4：改动归属与视频保护

完整共享工作区 `git diff --stat` 有大量开工前的其他任务改动，不能全部归到本单。独立比较 `/tmp/fix-test-baseline-588ab/originals` 的 12 个原件，结合 7 个新增夹具/来源文件，确认本单 19 文件均在 `tests/**` 和经授权的 `collect_adapters.py:start`；AST 比较该 adapter 的所有方法，只有 start 变化，`_run_job` 完全相同。

受保护的 generation、public/lab-test.*、tests/test_*lab* 共 29 个交付代码文件全部与 `/tmp/review-write-p3/repo` 任务前副本逐字节相同；其中 8 个已有 tracked 差分也与开工 `initial.patch` 完全相同。全部 29 个当前 SHA 与交付 manifest 一致。没有把 HEAD 中已有的视频改动误报为本单越界。

复核生成的独立可解析差分统计为 19 文件 / 3122 insertions / 98 deletions（包含真实历史源码夹具）。本报告是复核唯一新增的共享文件。

## L1：交付 task.patch 格式损坏（非业务阻断）

`git apply --stat coordination/fix-test-baseline-evidence/task.patch` 返回 exit 128：`error: corrupt patch at line 175`。原因在原 `test_radar_feishu.py` 无末尾换行，组装差分把 `-    unittest.main(verbosity=2)` 与 `+    unittest.main(verbosity=2)` 接在同一行，缺少正确的 no-newline 标记。补丁 SHA 与实施报告一致，所以不是传输后被改；应由交付者重新生成可解析补丁。

本次仍通过 19 个文件 SHA、12 个开工原件和独立重新生成的差分完成范围核对；没有替实施者修补正式交付文件。

## 证据位置

原始复核产物均保留在 `/tmp/review-fix-test-baseline-5eaea/`，关键事实和 SHA 已写入本报告，避免只依赖临时目录：

- `lock_probe.py`、`lock-probe.json`、`lock-probe.stderr`：真实 HTTP 持锁反例、线程栈、fd 和并发记录。
- `static_audit.py`、`static-audit.json`、`review-summary.json`：5 份测试、真实夹具子集、原件 SHA。
- `python-full.log`（准备不完整的试跑）、`assistant-cancel-rerun.log`、`python-full-ready.log`（最终门槛）、`original-failure-recheck-final.json`。
- `ui.log`、`browser-e2e.log`、`browser-e2e/results.json` 与截图。
- `aisa-20-summary.json`、`aisa-20/`：20 次原始独立进程输出。
- `protected-audit.json`、`originals-comparison.json`、`adapter-method-audit.json`、`snapshot-manifest.json`。
- `workspace-git-diff-stat.txt`、`task-scope-stat.txt`、`delivered-patch-check.txt`、`review-generated-task.patch`。

复核结论为不通过；先修 H1，再复验有界返回、旧终态交接、跨进程单飞及 fd 收尾。已核实通过的夹具/断言/范围证据可复用，除实际改动或新失败外不需要重新扩大复核范围。


## 定向复验

| 决定要点 | 结论 / 证据 |
|---|---|
| 结论 | 【通过】本轮限定的 6 项均通过，H1、L1 关闭 |
| H / M / L | 本轮新增 0H / 0M / 0L；不扩大到原全量或视频例外 |
| H1 真实 HTTP | 外部进程持续持锁且 job=done，200/200 次在 0.501～0.509 秒返回 HTTP400；释放后重试202并完成 |
| 原竞态 | 原确定性交接用例连续20次通过；旧锁释放前无第二次采集，释放后新任务启动 |
| fd / 单执行者 | `/dev/fd` 9→9；229 open / 229 close，重复关闭0、最终存留0；12并发请求实际执行1次、最大并发1 |
| AIsa 定向模块 | 20/20轮，39+4项/轮，合计860项：0F / 0E / 0S；135.996秒 |
| L1 补丁 | 干净基线 `git apply --check` exit0；实际应用后20文件字节全匹配 |
| 范围 / SHA | 本轮2文件，161增/10删；业务方法仅start，另加0.5秒常量；29视频保护文件不变；业务SHA `7fa4e5ae8b53d3888e3fdd850556f7513f1edc0aafa390756e799b08399fd2dd` |
| 真实页面 | 随机端口59389，忙碌提示0.539秒；释放后202、页面新增1条；pageerror=0、外网请求=0 |

复验任务 `task_3d21ca480392` / Dispatch `ctx_f1bbd06773a3`；返修任务 `task_0f4063da0a0f`，对应原复核 `task_5eaea172d944`。记录时间：2026-10-30T02:18:56+08:00。仅追加本节，不改产品代码、不提交不推送、不访问8766、不使用computer use或真实Chrome profile。

### 逐条结论与独立证据

1. **H1：通过。** 独立副本 `/tmp/review-aisa-lock-3d21ca/article-studio` 使用实际 `server.Handler`、`AIsaRadarAdapter`、临时SQLite及随机HTTP端口59074。先写旧done回执，再由另一个Python进程真实持有 `aisa/manual.lock`，该进程只接受探针明确通知后释放。持锁期间连续200次POST `/api/collect/aisa/run`，均在固定0.8秒验收上限内返回400，平均0.504673秒，最慢0.509499秒；错误原文均为“已有 AIsa 采集正在启动，请稍后查看”。每次返回时外部持锁者仍活着，transport始终0次。释放后以相同request_id重试返回202，真实执行1次并到done，无遗留锁fd。这里使用原反例的真实内核flock，没有模拟锁或替换互斥行为。

2. **原竞态：通过。** `test_aisa_live_chain.ReworkBTests.test_terminal_job_allows_next_start_before_final_writer_returns` 在20轮中逐轮实际执行并通过：旧执行者写done后保持锁，新请求发起；释放旧锁前transport仍仅1次，解除后新request_id成功启动且最终总调用2次。原模块SHA `14f9cdb89db56dd60a543e9938d93d445e2ad52e069422929d52ef23e041500a` 与返修前相同，未改动该反例。

3. **fd及单执行者：通过。** 同一常驻测试服务、同一个外部持锁进程和相同存活管道下，对200次真实HTTP忙碌请求前后执行GC并读取本进程 `/dev/fd`，计数9→9；每次请求返回后，被测模块均无存留锁fd。只替换 `collect_adapters.os` 的代理绑定记录真实open/close，未替换fcntl或SQLite；整份探针另覆盖释放后成功执行、12并发请求复用、线程启动失败，累计229次open/229次close、重复关闭0、结束存留0。12个并发start均复用同一running job，transport实际1次、最大同时执行1；线程启动失败后回执error、锁fd为0。没有把“最终无泄漏”代替HTTP有界返回，两项分别断言。

4. **L1：通过。** 依据原开工12个原件在新的干净目录构建补丁目标，其余新增路径原先不存在；独立执行 `git apply --check coordination/fix-test-baseline-evidence/task.patch` 返回0，再实际应用并与当前交付20个文件逐字节比较，20/20相同。补丁SHA256 `d1310d374a4b508c7a0d0caf5dd83e8b59854ccbc96c8d799bcc257967a138cf`，可解析统计为20文件、3272新增/97删除；这些包含上一轮交付，不当成本轮新增范围。

5. **范围：通过。** 以返修前留底（SHA与上轮交付相符）构建独立Git暂存基线，`git diff --stat` 为：

   ```text
   collect_adapters.py          |  19 +++---
   tests/test_aisa_lock_wait.py | 152 +++++++++++++++++++++++++++++++++++++++++++
   2 files changed, 161 insertions(+), 10 deletions(-)
   ```

   AST逐方法比较只有 `AIsaRadarAdapter.start` 变化；去掉该方法及新增 `AISA_START_LOCK_WAIT_SECONDS = 0.5` 后，模块其余AST完全相同，`_run_job`未变。原交付其余18文件SHA不变，新增测试SHA `507b826102572527e15668f41021c065df45f9e41c0f9f4438892b36d64d91ac`。29个视频保护文件同时核对返修前SHA及已有任务前副本，差异0。共享目录整体原有多线改动，不能把整体45文件diff归到本单；以上统计专指本轮返修差分。

6. **20轮：通过。** 每轮分别启动新的指定venv解释器，运行 `-m unittest discover -s tests -p test_aisa_live_chain.py -v`（39项）及 `-p test_aisa_lock_wait.py -v`（4项）；20轮全部0F0E0S，共860项，耗时135.996秒。未跑Python全量，没有发现要求扩大影响面复核的证据。

真实浏览器补验使用headless Playwright独立context，新后端在返修后启动并加载当前代码：打开 `/clues.html` → “AIsa手动采集” → 选择小红书 → “立即采集”，仍持外部锁时400并显示原中文提示、按钮恢复；明确释放锁后再次点击202，页面显示完成/新增1条并出现1张AIsa线索卡。请求只允许随机回环来源；外部请求0、pageerror0。网关transport为离线合成回执，未访问真实账号或付费服务。

### 证据与执行边界

复验607个代码文件逐字节复制到独立副本，最终再次核对共享源文件及副本manifest，源文件没有中途变化。依赖仅复用指定venv和node_modules；首次准备漏复制兄弟article-importer的兼容入口，导致两个脚本在导入阶段报FileNotFoundError，未执行产品反例；保留 `preparation-probe.log`、`preparation-loop.log` 后补齐独立兼容入口，从头运行本节全部最终测试，没有改产品或放宽断言。

本轮原始证据保留在 `/tmp/review-aisa-lock-3d21ca/`：`probe.py` / `probe.json` / `probe.log`（200次HTTP、重试、fd、并发）；`loop.py` / `loop-summary.json` / `round-01`至`round-20`两个模块日志（860项完整ID）；`static.py` / `static.json` / `rework-stat.txt` / `patch-stat.txt` / `patch-check/`；`browser.cjs` / `browser_server.py` / `browser-results.json` / `browser-busy.png` / `browser-retry.png`；`snapshot.json`。关键数值与SHA已写入本节，不以临时目录作为唯一结论依据。

本节只关闭本次限定的H1/L1及相关回归，不宣称正式服务已部署或原有视频例外已修复。
