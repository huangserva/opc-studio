| 决定要点 | 结果 |
| --- | --- |
| 结论 | **通过（全量既有偶发获主控明确批准例外）**；M1–M3、文档日期、只读和专项通过 |
| H / M / L | **0H / 0M / 0L** |
| 范围与 SHA | 5/5 与返修报告一致；相对实施开工 baseline 仅许可内 5 文件变化；本轮复验未改业务代码 |
| M1 / M2 | 推特等于成功快照 finished_at；AIsa 与今日页按秒相等；缺快照真实 DOM 显示未知 |
| M3 / reason | 正式显示 10-11 的 200013；合成 8 场景通过；七来源悬停 title 与 reason 一致 |
| 专项独立复跑 | Python **15/15**；UI **33/33**；隔离 Playwright **23/23** |
| Python 全量 | Ran 3560 tests in 375.424s；FAILED (failures=4, errors=86, skipped=7)；唯一 ID 90，新增 **1 个随机 SHA 测试误报**；定向 1/1，主控批准收口 |
| UI 全量 | **723**：718 通过 / 4 个批准的既有失败 / 1 跳过；新增失败 0 |
| 保护与产物 | 正式 8766 全部 GET；连接器 0 调用；SQLite mode=ro；仅本报告及 /tmp 证据，未提交推送 |

任务 `task_a089660351c6` / Dispatch `ctx_51740fe0939e`。按 `coordination/review-p4-4-1-fix-contract.md` 定向复验 `task_1dc48ac12cb1`，不重开上一轮已验收范围。证据根目录 `/tmp/review-p4-4-1-fix/`。

## 1. SHA 与范围：通过

独立对照返修报告计算 5 个 SHA256，全部一致；按实施开工 `/tmp/fix-p4-4-1/baseline-hashes.json` 逐文件比较，变化恰为 README、product-scope、settings_view、Python 专项、Playwright 脚本，许可外 tracked 增量 0。复验结束再次比对本轮开工 hashes，tracked 变化 0。`public/settings.js` 未改。原始结果：`scope-sha.json`、`review-end-scope.json`。

## 2. M1 推特时间：通过

独立读取正式 `workspace/twitter-radar/monitor/state.json`，按槽位排序找到最近成功 run `20261029-140008-f6d22d61`，再定位 `workspace/twitter-radar/snapshots/2026-10-29/20261029-140008-f6d22d61.json`。该快照实际是顶层 run 字段结构；`run_id` 一致，`finished_at=2026-10-29T14:00:38.594Z`，SHA256 为 `b29efccdc734d9bad122ccc1942423298cd971dd56104827eafed7b0f0b64c9d`。正式 overview 的 twitter last_success 与其逐字相等；页面显示北京时间 `2026/10/29 22:00:38`，没有继续用小时槽位 `2026-10-29T22`。

独立随机端口服务只放成功 state、缺少对应快照，HTTP last_success 为 null，隔离浏览器真实 DOM 显示“最近成功：未知”，reason 说明快照缺失，没有补 `22:00:00`。专项还覆盖最新失败轮次、错误 run_id、无时区、小时槽位及快照缺失。证据：`production-truth.json`、`synthetic-results.json`、`browser-results.json`、`synthetic-missing.png`。

## 3. M2 AIsa 来源：通过

正式 overview、bundle.collected_at 都是 `2026-10-29T14:00:38.594Z`；GET `/api/today?source=A&range=48h` 的 `sources.A.updated_at=2026-10-29 22:00:38`。将后者按 Asia/Shanghai 解析后，二者 epoch 秒均为 **1793282438**，符合主控已批准的同秒等价口径。

读代码确认 `settings_view._aisa_success` 与 `today_view.snapshot` 的 A 来源都使用 `grok_bot_radar._read_json` 和正式 `grok.BUNDLE_PATH`（`public/twitter-radar-bundle.json`）。settings_view 已不读取 aisa.db；collect 缓存仅允许微信、GitHub、intel，旧 AIsa 缓存不能覆盖本来源。专项放入 2099 年旧库/旧缓存反例仍返回 bundle 时间。证据：`static-paths.txt`、`production-overview.json`、`production-today.json`、`production-truth.json`、`python-specific.log`。

## 4. M3 intel 错误：通过

独立合成 SQLite 每次只放一条 job，通过随机端口 HTTP overview 取结果：

| status | reason / detail | 实际 last_error |
| --- | --- | --- |
| rate_limited | 命中后台限流（200013） | 有，包含时间、状态及原文 |
| partial | 正文读取失败 | 有，包含时间、状态及原文 |
| partial | 后台限流（200013），未取得列表 | 有，包含时间、状态及原文 |
| partial | reason 为后台 failed，detail 为空 | 有，包含时间、状态及原文 |
| partial | 已保存 3 篇，剩余正文待确认 | null |
| partial | 保存 3 篇，失败 0 篇，未处理 2 篇 | null |
| partial | 已保存 3 篇，没有失败；其余待确认 | null |
| done | 成功保存 3 篇 | null |

以上错误样本的时间均为 `2026-10-29T14:00:38Z`，均检查具体原文而非仅判断非空。专项另覆盖较新正常 partial 不遮旧错误。

正式库独立 mode=ro 查询核对：overview last_error 指向 `partial / 2026-10-11T03:14:43.044017+00:00`，完整 detail 含“后台：命中公众号后台限流（200013），已停止本次抓取（未重试）”；与正式库对应 job 的时间和 detail 一致，真实 DOM 也含该时间和 200013。证据：`independent.py`、`synthetic-results.json`、`production-truth.json`、`browser-results.json`。

## 5. reason、文档、只读和服务：通过

- 正式浏览器逐一 hover 七来源的各个带 title 段落，title 与接口 reason 完全一致，每个 reason 均含具体 `.db` 或 `.json` 数据源。五次完整加载秒数为 **0.317 / 0.306 / 0.307 / 0.206 / 0.206**；pageerror=[]、被拦截请求=[]、实际请求方法全为 GET。截图已查看。证据：`browser-results.json`、`production-settings.png`。
- README 与 product-scope 均明确写出 **2026-11-29**，并保留“届时另开单决定是否撤除”的批准口径。证据：`static-paths.txt`。
- 独立合成 store 的 collect/intel 属性访问即抛错，status 调用即失败；overview 前后计数均 **[0,0]**。拦截记录的 **16 次 SQLite 连接**全部 `?mode=ro, uri=True`，八场景数据库 SHA 前后一致。代码全部查询经 `_rows`，不调用连接器或 status。证据：`synthetic-results.json`、`independent.py`。
- 正式服务仍为已重启的 PID **72793**，启动于 **2026-10-30 00:50:19**；正式响应和页面包含最终修复结果。未再次重启，未读取或输出服务环境变量，未使用 computer use。

## 6. 回归：专项通过；全量新增偶发已按主控批准例外收口

- `../article-importer/.venv/bin/python -m unittest tests.test_settings_view`：**15/15**；`node --test tests/test_workspace_nav_ui.cjs tests/test_clues_ui.cjs`：**33/33**。
- 同一份 `tests/e2e/settings_page.cjs` 只替换 ROOT 为实际项目路径、证据前缀为本单目录后运行，业务测试逻辑不变：**23/23**，隔离 workspace、随机端口及独立 headless Chrome profile。路径 `e2e-jB6OX9/`，含原始结果与截图。
- 经主控明确独占许可后运行 Python 全量 `../article-importer/.venv/bin/python -m unittest discover -s tests -v`：Ran 3560 tests in 375.424s；FAILED (failures=4, errors=86, skipped=7)。相对上一轮独立复核最终 89 个失败/错误 ID 集合，本次唯一 ID 90，**新增 1**：`test_empty_round_has_no_numbered_summaries (test_twitter_radar_foundation.C14NoFabricatedTopics.test_empty_round_has_no_numbered_summaries)`，removed_ids=[]。首次运行未满足原始“新增 0”门槛，保留完整结果，不声称全量全绿或新增 0。证据：`python-full.log`、`python-id-diff.json`。
- 新增项独立定位为**测试偶发误报**：第 681 行对整个 bundle JSON 断言不含 `233`，唯一命中位置是 `.manifest.snapshot_sha256=4db92f16ef2a1b36640902653310a7e19e1309e6262336818af4df7cd33b2a2a`，没有命中内容摘要。实际 topic_clusters、vip_tweets、tweets 均为空，today_virals、h48_virals 均为 0，前置语义断言全部通过。该测试及 radar_bundle、radar_collect、radar_common、radar_store、radar_client、refresh_twitter_radar 七文件 SHA 与实施开工 baseline 一致；相关文件本单未改。原样单独重跑该测试 **1/1 通过（0.014s）**。证据：`false-positive-proof.json`（逐字段命中及七文件前后 SHA）、`false-positive-single.log`。
- 已将新增 ID、原始命中、定向结果与 SHA 证据汇报主控。主控通过 Orca ask（`msg_9d4c0bb3edee`）明确答复：**“不用再跑第二次全量。根因已经查清（随机 sha 撞上 233，空数组和零计数断言都通过），按「已知偶发」记录：附上命中位置、单独重跑这一项的结果、旧文件 SHA，就可以定稿收口。这一项由 coordination/fix-test-baseline-contract.md 负责修。”** 本次据此按既有偶发例外收口，未改测试、未重复全量；该测试修复属于另单，不把它写成本单已修复。
- `npm run test:ui`：**723** 项，718 pass / 4 fail / 1 skip。四失败 ID 与上一轮批准集合逐个相同：lab-test 初始四幕、切换赛博追杀、生成单幕参数，以及 `tests/test_radar_links_e2e.cjs`。证据：`ui-full.log`、`ui-id-diff.json`。

## SHA256

| 文件 | SHA256 |
| --- | --- |
| settings_view.py | `7a25c1081c614435143740a7f2d390bb1abf81ffa9bc297b16c729c3deccabdd` |
| tests/test_settings_view.py | `08ec42c0f9755d95688b6020fb26d36e95996a0401bdac25f611ce1e49bdbb15` |
| tests/e2e/settings_page.cjs | `f4005b9f23b242cc1942aacfd9721d7393dd3d434794191268fccecc45b10ade` |
| README.md | `18538e122ec3b20dbdc5c99709eb2a958c35db9f5f19d27d91bb02215e061f6b` |
| docs/product-scope.md | `7c8f1685da0766d3f4df2c262fe1455c947f6637775b984b92e1400b713966a0` |
