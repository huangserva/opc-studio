# 4-1 独立复核报告

| 决定要点 | 结果 |
| --- | --- |
| 结论 | **不通过**；第 3 项来源数据真实性存在 3 个 M，需返修 |
| H / M / L | **0H / 3M / 0L**；微信日志停更不判为服务停止 |
| 范围与 SHA | 15/15 与实施报告一致；tracked 许可外增量 0；server.py 仅增加 2 行 GET 装配 |
| 专项独立复跑 | Python 11/11；导航及 clues UI 33/33；隔离 Playwright 23/23 |
| 全量独立复跑 | Python 3556：3F / 86E / 7S，新增 ID 0；首轮既有竞态已披露；UI 723：718 通过 / 4 批准的既有失败 / 1 跳过 |
| 真实浏览器 | 8766 五次加载 0.862 / 0.217 / 0.211 / 0.216 / 0.215 秒；错误 0；全部 GET |
| 服务与保护 | PID 2027；未重启、未写真实任务/模型、未调用采集、未输出服务环境变量 |
| 本单产物 | 仅本报告及 `/tmp/review-p4-4-1/`；未改业务代码、未提交、未推送 |

复核任务 `task_cd2e8ccf5375` / `ctx_604fb7981ad5`；对象 `task_d4368dc998ad`。按复核合同逐项独立执行，未沿用实施者的“通过”结论。现有专项测试全过仍不能替代来源真值核对。

## 阻断项（集中反馈）

- **M1：推特“最近成功”取了小时槽位，丢失分、秒、时区。** `settings_view.py` 在 twitter 分支把 `state.runs` 的键直接加入 successes。正式接口与真实 DOM 均为 `2026-10-29T22`，不是前端省略显示：上游槽位由 `strftime('%Y-%m-%dT%H')` 生成。对应成功 run_id 是 `20261029-140008-f6d22d61`；公开 bundle 的 manifest 指向快照，已核 SHA 相符，快照 `finished_at=2026-10-29T14:00:38.594Z`（北京时间 22:00:38.594）。应沿成功 run_id 读取权威完成时间；不能补 `:00:00` 冒充实测完成时间。证据：`overview.json`、`production-results.json`、`twitter-authority.json`、`source-probe.txt`。
- **M2：AIsa 数据源选错，展示的是旧线索库同步时间。** 正式 overview 取 `workspace/aisa/aisa.db meta.last_synced=2026-10-13T13:04:18.345171+00:00`，查询确有此值；今日页的 AIsa 帖子却由 `today_view.snapshot → grok.radar_view → grok._bundle / BUNDLE_PATH` 从 `public/twitter-radar-bundle.json` 读取，不来自该数据库。当前 bundle `run_id=20261029-140008-f6d22d61`，`collected_at=2026-10-29T14:00:38.594Z`，含 tweets 10 / vip_tweets 119；这是今日页 AIsa 来源的输入，不把该数量冒称最终去重展示数。另只读 GET `/api/today?source=A&range=48h`，实际返回 113 条、首页 50 条，sources.A.updated_at 为 `2026-10-29 22:00:38`，首页每个 ID 均在 bundle 中；默认 today 因已过零点返回 0，未拿 0 条掩盖源核对。应展示实际雷达来源最新成功；如保留旧库口径，必须明确区分，不可用一个“AIsa 最近成功”替代两条链路。证据：`source-probe.txt`、`today-aisa-48h-proof.json`、`today-aisa-proof.json`、`today_view.py` 约 203–218 行、`grok_bot_radar.py` 329–336 行。
- **M3：intel 漏报已有失败/限流记录。** 正式接口 `last_error=null`，页面“最近错误：未知”；真实 jobs 最近非成功记录为 `partial / 2026-10-11T03:14:43.044017+00:00`，detail 明确后台限流 200013、没有取到列表；另有 `rate_limited / 2026-10-09T12:32:04.315718+00:00` 的明确限流原因。实现只查询 `status IN ('error','failed')`，漏掉实际终态。独立 /tmp 合成库只放 `rate_limited` + 原因，HTTP overview 也返回 null，稳定复现。应按 intel 实际状态/结果字段提取可确认的失败原因，不能把所有 partial 一概视为失败，也不能全部遗漏。证据：`source-details.json`、`independent_probes.py`、`independent-probes.txt`；状态语义见 `intel_store.py` 155、962、1875、2169 行附近。

## 1. SHA 与改动范围：通过

独立计算 15 个交付文件 SHA256，全与实施报告一致（完整列表见文末）。逐文件对比 `/tmp/impl-p4-4-1-baseline/tracked-hashes.json`：变化仅 README、product-scope、assistant.js、clues.js、workspace-nav.js、server.py、test_workspace_nav_ui.cjs，许可外 tracked 增量为空。使用独立 GIT_INDEX_FILE 重建开工快照，不覆盖共享工作区。server.py 的差分只有两个 GET 分支；clues/assistant 只加入口开关和 hidden。

逐条核对导航测试：保留原 12 页静态兜底的完整链接/文字/图标、唯一高亮及二级导航检查；运行时新增到全部 18 页，断言六主项、两工具、九旧版、顺序、路径、分组展开、events 无入口，改为 events 等非导航页无高亮。正则增加连字符与 group 与新结构对应，未删行为断言。

`test_write_from_item.py` 原副本由主控提供 `/tmp/review-write-p3/repo/tests/test_write_from_item.py`，独立 SHA 为 `2fc239ec1c05cef179cc9b61f75328d0004e04c596b71779dcfd0a33c405c932`，与三期交付登记一致。差分仅原 116 行的模板句断言替换并加 L1 文本规则；单次调用、当前模型配置、180 秒、allowed_tools=() 均保留。证据：`scope-probe.txt`、各 `*.delta`、`test-write.delta`。

## 2. 只读与零调用：通过

代码没有调用 collect/intel status、初始化连接器或恢复任务；SQLite URI 明确 `mode=ro`，只 SELECT。将当前 settings_view 复制到 `/tmp/review-p4-4-1/settings_view_copy.py`，独立随机端口 HTTP 服务用合成 store，collect/intel 属性访问即抛错、status 使用计数 Mock，Grok 报告目录改为临时目录。实际调用 overview 5 次，前后计数均 **0/0**；专项同时验证 SQLite SHA 不变。证据：`independent_probes.py`、`independent-probes.txt`、`python-specific.log`。

## 3. 来源数据真实性：不通过

正式 GET 与本地只读文件/SQLite 逐源核对如下；“任务成功”与“采集成功”的口径提示页面存在，未将未知启用状态误判故障。

| 来源 | 独立核对结果 |
| --- | --- |
| 推特雷达 | **失败 M1**：小时键 `2026-10-29T22` 替代实际完成时间；fault_active 无活动故障，last_error 未提供；不由此声称历史无错误 |
| Grok Bot | 通过：daily 最新报告 12:42、alerts 最新 20:13，均为 10-29 Asia/Shanghai；overview 20:13 对应实际最新 JSON；报告未提供独立错误记录，显示未知合理 |
| AIsa | **失败 M2**：10-13 是旧库时间；今日页实际读 10-29 的公开雷达 bundle |
| 视频榜 | 通过：max(last_success_at) 为小红书 `2026-10-29T21:20:48.044120+08:00`；两抖音源 partial 的 note 与接口错误逐字对应 |
| 微信雷达 | 通过（任务口径）：两个任务启用；最近任务成功 `2026-10-29T04:57:20.800673+00:00`，错误分别 failed `15:29:39.680208+00:00`、interrupted `16:08:17.385270+00:00`，与 runs 一致；不是采集健康证明 |
| GitHub | 通过：四条 state 的最新 last_capture `2026-10-29T15:29:02.292139+00:00`；最近任务失败 `15:29:02.516401+00:00` 与接口一致，两者可同时成立 |
| intel | 成功时间 `2026-10-11T03:20:00.248555+00:00` 与 done job 一致；**错误字段失败 M3**，漏 partial 限流及 rate_limited |

### 微信两服务日志的疑点：路径正确，服务并未被证明停止

只用 Python 从已安装 plist 提取 StandardOutPath/StandardErrorPath，并 stat 对照；没有输出其他 plist 字段。

| label | 实际 StandardOutPath | stdout mtime（UTC） | launchctl list PID / Status |
| --- | --- | --- | --- |
| com.servasyy.wechat-radar | `workspace/wechat-source/radar-service.log` | 2026-10-23T09:15:51.519345+00:00 | 4042 / 0 |
| com.servasyy.wechat-source | `workspace/wechat-source/service.log` | 2026-10-23T09:15:45.316588+00:00 | 4070 / 0 |

stderr 也对上真实路径，分别停在 10-06 和 10-10；overview 取两者 max 与真实 mtime 一致。**能确认日志停更、launchd 有运行 PID，不能确认业务健康或停止原因。** 不重启、不调用连接器，不把静默日志等同服务已停。其他三服务实际日志路径与 overview 也均一致；详情 `source-probe.txt`。

## 4. 日志接口安全：通过

P1 四组路径穿越/行数越界，独立 HTTP 均 400，专项覆盖在打开文件前拒绝；额外覆盖重复参数、空值、未知参数、文件/父目录软链。

P2 实际隔离日志放 Authorization、AISA_API_KEY、cookie 假值，接口与浏览器 DOM 均不含原值，正常行保留。P3 独立 HTTP 读取 **209,780,843 字节**假日志，耗时 **0.0287 秒**，本进程 ru_maxrss 峰值增量 **4,866,048 bytes**；结果仅尾部，尾部凭据仍被脱敏。专项另跑 209,797,134 字节 / 0.0267 秒 / 2,375,680 bytes。只读末尾 512KiB、最多 500 行，与实现一致。证据：`independent-probes.txt`、`python-specific.log`、`e2e.log`。

## 5. 模型 key：通过

隔离服务使用合成 key `[已脱敏的测试夹具假值]`，模型接口、overview、页面 DOM 搜索完整值及片段均不可见；实际显示“密钥已配置”。点击第二模型，浏览器等待既有 `/api/assistant/select-model` 的成功响应，回读 active_profile_id=two。未搜/打印真实 key；未对正式模型写操作。证据：`e2e.log`，隔离结果与完整运行目录已移入本单证据目录。

## 6. 暂停/恢复：通过

隔离合成 GitHub 任务从设置页实际暂停，回读 schedules enabled=false，打开旧 schedules.html 出现“已暂停”及恢复按钮；设置页恢复后 enabled=true，旧页显示暂停按钮且无“已暂停”。不是只看 POST 200。正式 8766 无 POST。证据：`e2e.log` 与 `isolated-e2e/results.json`。

## 7. 导航与 events：通过

独立复跑隔离 Playwright 逐一实际打开 **18 个**共享导航页面，均为今日、素材库、创作台、发布复盘、商单、设置；六项顺序正确。两工具及九旧版链接路径/文字由 UI 精确断言；旧页九条和 events 地址 200，events 页面真实视图按钮存在。clues 实际选中合成线索，建立事项与从事项导入入口不可见，追加依据弹窗可打开；再用保留 API 建立关联，关联事项入口也不可见，events 详情仍返回原标题。助手回执可见但事件建议跳转不可见。33 项导航及 clues UI 全过。桌面及 390px 截图已人工查看，设置页无横向溢出。证据：`ui-specific.log`、`e2e.log`、`isolated-e2e/`。

## 8. 文档：通过

逐段核对 README、product-scope 的开工差分：六页名称、主链路、五块设置、工具/旧版清单、events 保留路径均与实际页面一致；删除“仍在讨论”的旧说法，两处均明确 2026-11-29。复核时通过 Orca ask 专门确认“撤除日期”与“另行评估”的差异，主控明确批准口径为“旧版保留到 2026-11-29，届时另开单决定是否撤除”，不记 M；仅缺日期才记 L。两处均明确日期，故本项通过；没有把本单当成已撤除旧页。证据：`README.md.delta`、`docs_product-scope.md.delta`。

## 9. 起草模板 L1：通过

与开工 `draft-template.md` 逐条 diff：四条修改均附合同/画像出处；允许不以“我”开头的具体步骤、同句式全文最多两次、文末合并且只保留一处实测占位；不编造数字/截图/经历、不把第三方经历写成黄总亲测、引用来源规则保留。固定样本及故意重复六次的反例均在专项中执行，相关 write 测试保留单次假模型调用断言。真实模型调用 0 次。仅确认模板约束及样本测试，不把它扩展成所有模型输出都保证不重复。证据：`template.delta`、`python-specific.log`、`test-write.delta`。

## 10. 回归与差集：最终通过（保留首轮竞态记录）

- Python 专项：`../article-importer/.venv/bin/python -m unittest tests.test_settings_view`，11/11。
- UI 专项：`node --test tests/test_workspace_nav_ui.cjs tests/test_clues_ui.cjs`，33/33。
- 隔离真实浏览器：`node tests/e2e/settings_page.cjs`，23/23，page-errors 为空。
- UI 全量：`npm run test:ui`，723 项，718 pass / 4 fail / 1 skip；失败恰为 lab-test 三条（初始四幕、切换赛博追杀、生成单幕参数）及 `tests/test_radar_links_e2e.cjs`，与批准集合一致。
- Python 首轮全量：`Ran 3556 tests in 377.179s`，3 failures / 87 errors / 7 skipped，90 个唯一 ID；相对开工 90 ID，新增 `test_manual_title_survives_second_and_third_refresh (test_aisa_live_chain.ReworkBTests.test_manual_title_survives_second_and_third_refresh)`，移除本次已批准 L1 模板旧断言。第一轮不满足新增为 0，未按总数相抵。
- 该新增错误原文为 `ValueError: 已有 AIsa 采集正在启动，请稍后查看`，定向单跑 1/1 通过。已在原三期独立副本上，只用线程事件暂停“最终 done 入库后、释放 manual.lock 前”这个现有时序窗口，再启动下一任务，稳定重现同一异常。`collect_adapters.py`、`aisa_source.py`、该测试三份文件的当前 SHA、三期副本 SHA、四期开工 SHA 均相同，证明这是本次未改动代码中的既有竞态；没有修改测试消除错误。证据 `aisa-race-proof.txt`、`aisa-race-sha.json`、`aisa-new-id-single.log`、`python-id-diff.json`。
- 因首轮新增失败未满足 ID 子集门槛，第二次全量在无其他 discover 并发时独立执行，最终：`Ran 3556 tests in 375.573s`，`FAILED (failures=3, errors=86, skipped=7)`，唯一失败/错误 ID **89**；相对开工 **new_ids=[]**，removed_ids 仅上述已批准 L1 旧断言，与实施最终 89 个 ID 集合完全一致。证据 `python-full-rerun.log`、`python-id-diff-rerun.json`。本项按最终完整运行满足 ID 子集判定；没有把首轮异常删除或声称全量全绿。
- 测试结束后再次独立核对 15 个交付 SHA，全部与实施报告一致。
- 正式设置页隔离 headless Playwright 五次实际完整加载均小于 1 秒，来源 7 / 任务 9 / 服务 5；无 pageerror、无越权请求。PID 2027 开始于 00:12:00，server.py mtime 00:04:19；已通过正式 GET 观察到新装配与页面。未擅自再次重启。

证据归档说明：专项脚本最初打印的 `/tmp/settings-p4-e2e-StHqur` 已整体移入 `/tmp/review-p4-4-1/isolated-e2e/`，包括全部 18 页截图、结果与独立 profile；日志中旧路径仅为该次脚本原始输出。

## SHA256

```text
c8a9e58f66171086ee65ca6f3fa7f163538c34a1a6286ce6f3a632a578cc4309  settings_view.py
87deb273662712eeffcb5599cc14839dc2a8d563ce4ec1442fb8529dadce3835  public/settings.html
31f1587507e24ead95793334e8b80afd756db639635550f72414eccb3f094322  public/settings.js
db665d25edcaa1b384bae3baea39675fa99d64f9faebc0c2322b9f74f4d37dbf  public/settings.css
3f2e5c1ad3c1180b973ce0515c7ed9e648478645b3eba8f0bbcf3d4d1a33b194  server.py
dfde6ad1e77a92c74bbc5e7e8649083d70ec6032057a2724bdfb6eb514a73fa0  public/workspace-nav.js
beb320d416dc3db91cf719a93a5ff7e91372fccf09c867b711899f6edb544cf4  public/clues.js
775acb8074a14971a16b09e209cd5b48c3394db635c06a9dd0d29b2347e770e5  public/assistant.js
d689a2d6be82aff1b2a1dc4a8eb8c9fa6451652796af6a67400c983c683dbe8d  README.md
81bb3dcff087fc5ec2b0b1493504a054d8d0e6343fc2cd49ecdd3594f8db0ccf  docs/product-scope.md
87121350f1e58f4f8f7b636beef1ad9e90068804e14ad2bbcc9ccef298c254d5  tests/test_settings_view.py
d4ebd35aa44141a655320d6cd47633b95d8690ec7dac10ad8e33eb378c63e634  tests/e2e/settings_page.cjs
e4ce2d78ad4b7cbd3066a16cb10a59775e00561fe2f5784659803ba5368223a9  prompts/draft-template.md
891d27438d7863ee04212914888eaa4cfc097d0bab08f5bd62963120cfc5e7f4  tests/test_workspace_nav_ui.cjs
b8638e78ad2b8fdb2f76a5d1e3cf4d98abe559e6b5c4a15419728f227a0105b1  tests/test_write_from_item.py
```
