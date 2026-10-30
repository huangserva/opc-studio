# A1-b 雷达后端地基返修交付

## 决定要点

| 项 | 结果／证据 |
|---|---|
| 结论 | **实施自检通过，可交独立复核；尚未部署。** 已在 Pi A1 基础上返修，没有推倒重写；真实冒烟低于覆盖门槛，正确不发布。 |
| H1-a/b/c | **逐项通过**：业务失败／形状错误拒绝、partial 口径明确；公开 bundle 为唯一权威提交点；四阶段真 SIGKILL 后次轮清理及索引恢复通过。 |
| M9/M10 | **逐项通过**：守护线程按整轮截止返回，区分在途／未发起并保留调用数；北京时间自然日保留，日期目录符号链接与目录外文件不删。 |
| M2/L2/类型溯源 | **逐项通过**：本人非转推粉丝归属；采样完成时间与生成时间分离，真实隔离浏览器确认；逐帖新增最小 type_evidence。 |
| 复核方门槛 | **45/45 场景**（36+8+1），page.cjs **4/4**，既有页面套件 **35/35**，另 L2 浏览器专项通过；页面错误／外站请求均 0。 |
| Python 测试 | 原 **29/29**＋新增 **16/16**，全新副本合计 **45/45**；仅一条旧 deadline 状态断言经协调者批准改为更强的新状态检查，原文与 diff 留档。 |
| 真实冒烟 | run `20261015-082418-f618d977`：**43/51、84.31%、54 次调用、8 次 timeout、920 帖、未发布、exit=1**；没有自行重跑第二轮。 |
| 包与 SHA | `/tmp/radar-a1b-deliver/`，累计 **14 文件**，含完整 A1；补丁 SHA256 `96f4a1e30d96d4d2499699a9c3117b146657c68810471ef00dcbd478fd0daf86`；完整 SHA 见 `sha256.json`。 |
| 范围与后续 | 正式源码、名单、4 支停用脚本、B1/B2、页面与 8766 均未修改／重启；本次正式目录仅新增本报告。先独立复核，再做 B1 适配及原有上线验收。 |

## 基线、目录与证据

- Task `task_48acce1a665b`；Dispatch `ctx_8be3f3ed00be`，本窗口临时实施授权来自本单合同及黄总明确指令。
- 基线完整采用 `/tmp/radar-a1-deliver/radar-a1.patch`，SHA256 `474f896b4bc5354f6d22e0aa5989e4551d45bf6852478f7cf0e0f4be750f1bdb`；应用前检查通过。
- 实施副本 `/tmp/radar-a1b-work-lfpri5i7/article-studio`；最终验证副本 `/tmp/radar-a1b-verify-yv_6ucka/article-studio`，两者均从正式当前源码复制，未复制正式 workspace、Git 数据和依赖缓存。
- 最终交付包 `/tmp/radar-a1b-deliver/`：`files/` 完整文件、`radar-a1b.patch` 累计补丁、`sha256.json`、`files-list.json`、`review-tests/`、`evidence/`。
- `evidence/final-verification/validation.json` 记录准确命令、退出码及耗时；`04-directed.log` 是 45 项 Python 测试逐条输出，`05`–`07` 是复核方 45 场景，`08`／`09` 是 4／35 页面场景，`10` 是 L2 浏览器专项，`11` 是反向检查。
- 相对 Pi 基线，实际返修 6 个源文件及原测试 1 处断言，另新增 1 个测试文件；`radar_targets.json`、`radar_retired.py` 和 4 支停用脚本均逐字节保留 Pi 版本。累计包包含它们是为了相对正式目录一次应用得到完整 A1-b。

## 八项修复与实测输出

以下新增测试均位于 `tests/test_twitter_radar_a1b.py:A1bTests`；输出摘自最终新副本 `evidence/final-verification/04-directed.log`。

### 1. H1-a：业务失败、形状与第二页 partial

`success:false` 优先失败，即使带 code=0 或真实帖子；code 的数字／字符串形式只接受零，200 也不再当业务成功。缺少 data.tweets、null、非数组均失败，只有明确 `tweets: []` 才为空成功。HTTP 非 200 仍是 HTTP 失败。

首页有效但翻页失败：保留首页采样，账号状态为 `partial`、`pages_complete=false`；run 单列 partial 数量，**partial 计入 success 与覆盖率**，不等同全部页成功。完整页状态常量仍保留 ok/empty，collect 对 partial 单独计入账号覆盖成功。

测试：`test_h1a_false_code_and_shapes`、`test_h1a_partial_preserves_page1_and_counts_coverage`、`test_h1a_all_51_false_keeps_public_and_index_bytes`。

```text
H1-a business/shape: 7 rejected, explicit [] empty
H1-a partial: success=2 partial=2 calls=4 coverage=1.0 pages_complete=false
H1-a full pipeline: success=0 failed=51 api_calls=51 exit=1 public/index bytes unchanged
```

最后一项走真实 client → collect → 入口 → 存储，51 个 HTTP200/success:false 全失败，manifest 与公开 bundle 字节均不变，不只检查返回标记。

### 2. H1-b：公开 bundle 是唯一权威提交点

实际顺序为：入口写完整快照 → 写版本 bundle → 原子替换公开 bundle → 最后写派生 manifest.json。公开 bundle 内嵌 manifest，包含 run_id、快照相对路径／SHA256、版本 bundle 文件名、schema、采样开始时间等。

`public_manifest(root, repo_root)` 读取公开包内嵌段；`load_previous_snapshot()` **忽略旧接口的 manifest 参数**，只从公开包引用的快照读取，并检查路径在根目录内、文件类型、SHA 与 run_id。拿到单实例锁后先回收本模块临时文件，再按公开包修复损坏或落后的派生索引，修复有日志。

派生 manifest.json 与公开内嵌段的各权威字段一致，另外增加从公开包原始字节计算的 `bundle_sha256`；该额外字段可重算，不嵌入自身字节，避免自引用 SHA。版本 bundle 与公开 bundle 字节一致。底层直接构造旧包的测试夹具如没有原始快照，内嵌 snapshot_file/SHA 为 null，不伪造采样证据，也不会被继承为成功快照；**正常入口发布前必已写出真实快照**。

公开包替换已成功、仅派生索引写入遇到普通 I/O 错误时，不谎报“公开版本已回滚”；下轮从公开权威数据修复索引。

测试：`test_h1b_public_is_authority_and_index_repair`、`test_h1b_snapshot_hash_and_escape_fail_closed`。

```text
H1-b wrong index ignored/repaired; public manifest and snapshot SHA verified
```

### 3. H1-c：四阶段真 SIGKILL 与崩溃垃圾回收

只清理快照日期目录、版本 bundle 目录、公开包同目录和 manifest 同目录中本模块精确命名的临时普通文件；不递归扫描其他目录，不清理无关 tmp，不跟随日期目录或文件符号链接。

测试：`test_h1c_four_sigkills_next_round_recovers`、`test_h1c_cleanup_ownership_and_symlink_boundaries`；复核方 K 四项另在独立进程跑完整下一轮（离线采集、no-publish），确认清理、修复和上一轮快照读取。

```text
H1-c SIGKILL snapshot: exit=-9 next_round_tmp=0 index=public
H1-c SIGKILL bundle: exit=-9 next_round_tmp=0 index=public
H1-c SIGKILL public: exit=-9 next_round_tmp=0 index=public
H1-c SIGKILL manifest: exit=-9 next_round_tmp=0 index=public
H1-c cleanup scope: owned removed; unrelated/symlink/outside preserved
```

前三个提交前阶段保留旧公开包／旧索引；manifest 阶段公开包已经是完整新版，允许索引仍旧，下一轮修复。任何成功指针都检查磁盘快照与 SHA，没有用“抛异常清理”代替真杀进程。

### 4. M9：绝对截止、请求预算与发起时计数

移除会在退出时等待卡死线程的 ThreadPoolExecutor，改为受条件变量保护的守护线程队列。截止时冻结结果，后返回的请求不再修改已产出的 run；CLI 可以自行退出，并先保存失败 run。实际客户端每页发起时回调计数，请求 timeout 裁为 min(配置值, 整轮剩余时间)。

账号区分 `deadline_inflight` 和 `not_started`，两者均失败；api_calls 保留已经发起但没完成的调用。run 触及整轮截止时 complete=false，即使部分覆盖已达比例，也不会作为完整轮发布。另保留最多两页的硬上限，零请求预算不能冒充成功。

测试：`test_m9_daemon_hung_fetch_process_exits_and_records_failure`、`test_m9_request_timeout_remaining_and_start_accounting`、`test_m9_inflight_second_page_calls_already_two`、`test_maximum_two_pages_and_zero_budget_never_success`。

```text
M9 hung process: elapsed=0.176s exit=1 calls=1 failed=2 snapshot=1
M9 request timeout=min(20, remaining): 0.10s -> 0.01s; calls=2 at launch
M9 deadline page2: calls=2 status=deadline_inflight; late result cannot mutate run
M9 boundary: reported deadline -> not_started; return at deadline still incomplete
```

第一项为真实子进程中 fetch 永久卡住、deadline=0.1 秒；父测试仅设 2.1 秒看门狗，子进程自己 exit=1，没有靠父进程杀掉来冒充按时退出。复核方 D01/D02 同时通过，D02 保留已发起的 1 次调用。

### 5. M10：清理边界与北京时间自然日

跳过符号链接日期目录，并检查 resolve 后仍在快照根内；单文件链接和同名目录也不删除。归档日期与保留阈值均按北京时间自然日计算，阈值当天保留，早于阈值的合法快照才删；这不是精确到秒的 30×24 小时语义。

测试：`test_m10_beijing_retention_directory_symlink`，以及复核方 P-dir-symlink／P-file-symlink／P-named-directory。

```text
M10 Beijing 10/16 cutoff=9/16: 9/15 deleted, 9/16 kept, outside preserved
```

### 6. M2：粉丝只属于被查询账号

只从作者明确等于 queried_handle、且非 retweet 的记录取实时粉丝，再按 fetched_at／published_at 取最新。作者缺失后使用查询账号作展示回退，不再被当作明确作者来源；只有 Other／转推／作者缺失时，查询账号粉丝为未知。

测试：`test_m2_author_ownership_non_retweet_unknown`，复核方 F-query-author-ownership。

```text
M2 Audit=500; Other/RT/missing-author => unknown
```

### 7. L2：采集时间来自 finished_at

`updated_at` 为 run.finished_at 转北京时间的既有页面可解析格式；`collected_at` 为同一采样完成时间的 UTC ISO/Z；`generated_at` 独立记录生成时间，不再用生成时间冒充采集。

测试：`test_l2_finished_time_separate_generation_utc`；另 `evidence/l2-page.json` 是真实隔离浏览器专项。

```text
L2 updated_at=13:50:45 collected_at=05:50:45.505Z generated_at=06:47:28.000Z
浏览器：采集时间：2026-10-15 13:50（北京时间）
```

浏览器处于洛杉矶时区，使用此前真实采样经本轮 builder 重新生成的包；实际采样完成为 05:50:45.505Z、生成于 06:47:28.327Z，回执显示 13:50，不显示旧错误的 14:47。没有修改页面实现。

### 8. 类型溯源

逐帖 type_evidence 仅保留 isQuote/isRetweet、RT 前缀判定布尔值、quoted／retweeted 对象中的 ID 及回复 ID；嵌套对象不整包保存，ID 只保留有界数字字符串，不含原始正文、请求头或凭据。

测试：`test_type_evidence_minimal_flags_ids_only`；真实本轮 920 帖全部带该对象，样本见 `evidence/smoke-evidence.json`。

```text
TYPE evidence: isQuote=true quoted_tweet_id=1234 reply_id=4567; no raw text/token
```

同时针对真实网络入口增加拒绝鉴权跨源与不跟随重定向、响应大小限制；测试 `test_credential_transport_rejects_redirect_and_other_origins` 输出 `redirect denied, foreign origin rejected before request`。这是现有凭据保护边界，未改网关或鉴权方案。

## 测试断言调整：授权、原文与差异

未私自把失败改成通过。两次 Orca ask 分别获得 K 四场景调整，以及旧 deadline／序列化适配批准；第二项也通过消息 `msg_20642c66ab74`（2026-10-15T08:30:23Z）确认“其余断言继续禁止改动”。

1. **K 四场景原断言**：`check(manifest_same and pub_same and not tmp, 'SIGKILL检查失败 '+json.dumps(d))`。它把“被杀当下零 tmp、公开包仍旧”作为四阶段统一要求，与新合同的公开包提交顺序／次轮回收冲突。批准后改为：快照／版本 bundle／公开包替换前仍强制旧公开包与旧索引合法完整；manifest 阶段强制完整新版公开包、内嵌 run_id 与磁盘快照／SHA 自洽；四项都跑下一独立进程完整轮次后断言零 tmp、派生索引等于公开权威字段及可重算包 SHA、上一轮快照来自公开包引用。旧断言原文和完整 diff 在 `evidence/approved-independent-test.diff`。
2. **原 29 项旧断言**：`self.assertTrue(any(a["status"] == "deadline" for a in run["accounts"].values()))`。按新合同改为同时有 deadline_inflight 和 not_started、至少一个截止账号、这些账号全不成功、failed 等于截止账号数、api_calls=1、attempted=1；原有 deadline_hit／success／failed 检查保留。完整 diff 在 `evidence/approved-original29-test.diff`，比旧断言更强。
3. **输出序列化夹具**：S11 修复后返回 detail 中含原有 datetime，探针写结果 JSON 时触发 TypeError；仅在写结果 JSON 的 json.dumps 增加 `default=str`，未修改任何业务判断。

原始断言运行记录全部保留：`original-tests.log` 为原 29 项 28 过／1 个旧状态断言失败；`review-original-independent.log` 是原探针未经上述调整的四个 K 失败及序列化错误；`review-original-assertions.log` 仅适配输出序列化，业务断言仍原样，36 场景 **32 过／4 个 K 失败**。按明确批准的新合同语义适配后才得到 36/36；不能把这两套语义混写成“原断言完全未动”。

`extra.py`、`business_false_pipeline.py`、page.cjs 四场景和既有 35 场景未修改断言；复制时只调整副本路径。页面验证使用此前真实 1080 帖快照经本轮 builder 重建的包，保留其真实采样／生成时刻；本次新采集低覆盖的 run 未被冒充成功发布包。

## 全新副本验证

相对正式当前内容生成累计补丁，另从正式源码创建 `/tmp/radar-a1b-verify-yv_6ucka/article-studio`，严格执行：

```text
apply --check        exit=0
apply                exit=0
py_compile (13 py)  exit=0
Python directed      Ran 45 tests ... OK（原29＋新增16）
independent.py       total=36 passed=36 failed=[]
extra.py             8/8
business_false       success=0 failed=51 api_calls=51 published=false exit=1（探针 exit=0）
page.cjs             total=4 passed=4 pageErrors=[] externalRequests=[]
twitter_radar_page   通过35/35 pageerror=0 console.error=0 外网请求=0
L2 专项浏览器         passed=true
apply -R --check     exit=0
```

应用前后每个完整文件逐字节比对一致，测试结束后再次核对源码 SHA 一致。只运行本单定向测试，没有运行全量 discover。浏览器均为隔离 headless／新 context／随机回环端口，没有控制黄总真实桌面或 Chrome，也没有重启 8766。

## 真实冒烟与未通过的线上门槛

本单授权的一轮真实采集在实施副本执行，Key 只由子 shell 从既有服务 plist 读取到环境和请求头，没有打印或写入配置／证据。没有自动重试失败账号，也没有再跑第二轮。

| 指标 | 实测 |
|---|---|
| run_id | `20261015-082418-f618d977` |
| 开始／完成 | `2026-10-15T08:24:18.147Z` → `2026-10-15T08:24:57.594Z`，39.447 秒 |
| 覆盖 | 43/51，84.31%；failed=8，partial=0，complete=false |
| 调用／成功页 | api_calls=54，pages_fetched=46；3 个账号翻到第 2 页 |
| 失败 | 8 个 timeout；本轮未凭现象推定具体上游根因 |
| 原始帖子 | 920；original=363、quote=436、retweet=121，全部有 type_evidence |
| 发布 | published=false、入口 exit=1，正确拒绝低于 95% 的轮次 |

真实失败轮见 `smoke-summary.json`、`smoke-snapshot.json`、`smoke.log`；类型判别字段样本见 `smoke-evidence.json`。此证据证明本轮采样、计数和失败门槛按实际结果工作，**不能作为线上成功采集或完整日运行通过的证据**。真实类型原字段可回溯，不等于对每条分类和浏览量绝对值已做外部独立认证。

## 给 B1 联调的接口说明

- 上一轮成功快照必须从公开 bundle 内嵌 manifest 找，不从派生 manifest.json 或“目录最新文件”猜；可调用 `radar_store.public_manifest()`／`load_previous_snapshot()`，后者验证快照 SHA、路径和 run_id。
- 正常入口公开包为权威；派生 manifest.json 只补充从公开字节计算的 bundle_sha256，其余权威字段与内嵌段相同，可在下轮修复。
- partial 计入账号覆盖成功；对外 `accounts[handle].status=partial`、`ok=true`、`pages_complete=false`，run.success 包括这些号，另有 run.partial。B1 需显式适配此口径。
- schema 仍为 `radar-1`，结束字段仍为 finished_at，targets 仍是对象数组、帖子作者为平铺字段、类型为 kind、空成功为 empty；本单没有擅改 B1，前轮列出的适配差异仍应在 B1 联调中解决。
- 新增 collected_at/type_evidence 及截止状态；原始 fetched_at 不从公开包更新时间补造。

## 文件 SHA256 与保护边界

| 文件 | SHA256 |
|---|---|
| `scripts/refresh_twitter_radar.py` | `4680b37def299715b8033e941cc9c29f70c9fe47e4e295706b121698e61c9bc9` |
| `scripts/radar_common.py` | `cf07893e9dadcbcef152855dbdbe9df9037315a64eef4d8a81ddc0e47cbf8120` |
| `scripts/radar_client.py` | `bc1fd5fc1b443a2ccd6e3e121834ce526f4401a0261c884376e481de4db0c026` |
| `scripts/radar_collect.py` | `4d4fc352c64844391463f63132d0e026b59284065124dab907bb00643e435958` |
| `scripts/radar_bundle.py` | `69b96326dacc43c035e65981e4cab1207b3f7faa7a8a5adf498cd17d11e7fed5` |
| `scripts/radar_store.py` | `28a69151faa73c3e2fac789503d443b56f4d84ac5962eee672b481eca8116fa2` |
| `scripts/radar_retired.py` | `fb6dcc9c989fa896486f644069f83ae4a275e48bfc6bba8abb916cdfc906fde0` |
| `scripts/radar_targets.json` | `9d8295ef6941a83e94de5bf7708f924e84ae3ac704021369c57b56164d22ba8b` |
| `scripts/sync_all_radar_live.py` | `ec1b4fd6a493f0ad952b099771bc18bcb8ac2ed88e9cebdce2e1f58968c30074` |
| `scripts/build_fresh_radar_bundle.py` | `c14fd8c887ea70772bae7c9955fb21f71f104e7c85a41e71097e91e041f7fe1d` |
| `scripts/batch_scan_150_followings.py` | `b09bcc4345649c8de6f588a1f605b25025b3fb9f31d3384cb76ffef066c4dd60` |
| `scripts/deep_scan_batch_2.py` | `28cb33b6fd34f192940c79ab647228e2192f4dc1f4bcd3bd346909c80fded479` |
| `tests/test_twitter_radar_foundation.py` | `10d159c04cdb98760724a8f7ad47f75d12fb6fea1962777d9c3f2fdff5041f79` |
| `tests/test_twitter_radar_a1b.py` | `8268244bef10af7dd5f11b736f5b9b9c7cadd972ddeb07a176fa1b4ffcb08769` |

`evidence/scope-and-hygiene.json`：正式基线 13 文件逐项 SHA 未变，正式页面 JS/HTML 与实施输入相同，新测试不在正式目录；包内凭据前缀扫描无命中。没有修改 B1/B2、server.py、页面或其他在途文件，没有提交、推送、加载调度或部署。

独立复核通过后再按原门槛合入；本次真实覆盖率未过线、B1 接口尚需适配，均不得被本报告的离线全绿关闭。
