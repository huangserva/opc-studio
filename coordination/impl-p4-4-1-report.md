# 4-1 实施报告

| 决定要点 | 结果 |
| --- | --- |
| 结论 | 实施自测通过；仅交付 4-1，待独立复核 |
| H / M / L | 0H / 0M / 0L（本单实现自测）；既有失败与随机断言误报另列 |
| 范围与 SHA256 | 15 个交付文件，完整 SHA256 见文末；server.py 相对开工仅加 2 行 GET 装配 |
| 定向检查 | Python 11/11；导航与 clues UI 33/33；隔离 Playwright 23/23 |
| 全量检查 | Python 见下方原文；失败/错误新增 ID 0；UI 723 项：718 通过、4 既有失败、1 跳过 |
| 真实浏览器 | 8766 只读设置页 5 次 <1 秒，7 来源 / 9 任务 / 5 服务，页面错误 0 |
| 8766 PID | 2027，已在最后后端改动后重启、核验新 PID 与实际接口 |
| 提交与推送 | 未提交、未推送；4-2 及以后未实施 |

## 交付与实际行为

- 主导航：今日、素材库、创作台（`/`）、发布复盘、商单、设置。工具两项，旧版九项，原地址保留。事项与日程只移除导航，`/events.html` 及数据接口保留。
- 设置五块：来源快照、任务及最近运行、模型 profile 及切换、白名单脱敏日志、后台服务标签与日志时间。暂停/恢复及切换均走原接口，没有新增写接口或服务启停按钮。
- `clues.js` 使用 `EVENTS_ENABLED=false`，`assistant.js` 使用 `ASSISTANT_EVENTS_ENABLED=false`，保留原代码并隐藏入口。
- README 与 product-scope 更新六页、主链路、设置及旧版保留至 2026-11-29 的约定。未移除旧页、迁移 clues 或删除 events。
- 起草模板允许具体步骤与非“我”开头的句子，全文同一句式最多两次，实测占位集中在文末一处；不编造实测经历或数字，每项规则注明出处。本次真实模型调用 **0 次**。

## 主控确认的合同澄清

1. `orca ask` 回答允许只读快照：`collect.status()` 会请求微信客户端，`intel.status()` 会恢复任务，均不用于 overview；只读 SQLite `mode=ro`、雷达文件以及已实例化的 collect 缓存。未知项写明原因，并链接原来源页查看实时状态。
2. 主控消息 `msg_08c983d875aa`：允许更新旧导航断言文件，保留强度；lab-test 3 项和 radar_links 1 项按既有失败处理。
3. 主控消息 `msg_896969844d48`：允许更新 `tests/test_write_from_item.py` 的 L1 旧模板断言；其余 Python 失败/错误按 ID 集合子集校验。
4. 日志路径问询后，最新消息 `msg_a1621081f89e` 明确：overview 对真实日志固定路径只做 stat；logs 接口仍使用合同的三个文件，不放开软链。已按最新澄清实现。曾在问询中误称 workspace/logs 为软链，已发消息更正；实查它是实际目录。

## 通用反例 G1–G4

| 编号 | 结果与证据 |
| --- | --- |
| G1 | 通过。8766 页面实际 7 张来源卡、9 行任务、5 个服务；与 overview、schedules 的真实数量一致。隔离页面核验具体 Grok 报告时间、任务名、模型名、原文与操作后状态。 |
| G2 | 通过。发现“复用 status”与只读/禁调用冲突即询问主控；没有用空数据、200 状态或单看性能代替可用性。 |
| G3 | 通过。来源状态接口副作用、旧断言、日志实际路径均先问询并按回复处理。未自行修复雷达或媒体测试。 |
| G4 | 通过。对开工 tracked 文件 SHA256 逐文件比较，允许范围外新增变化 0；新增/未跟踪交付文件均在允许清单及主控补充授权内。既有工作区改动保留。 |

## 4-1 反例 P1–P8

| 编号 | 结果与证据 |
| --- | --- |
| P1 | 通过。路径穿越、编码斜线、99999、-1 均真实 HTTP 400；单测断言拒绝前没有调用 os.open。重复、空值和未知参数也拒绝。 |
| P2 | 通过。隔离日志 Authorization / AISA_API_KEY / cookie 的假值在 API、DOM 中均不可见；正常日志保留。额外覆盖 token、secret、长行截断，避免截断后丢失敏感字段名而露出值。 |
| P3 | 通过。实际写入 209797134 字节假日志；最后一次 0.0235 秒，ru_maxrss 峰值增加 3178496 bytes。最多读末尾 512KiB，不整读；最多返回 500 行。 |
| P4 | 通过。合成密钥的片段在 assistant/settings、overview 及设置页 DOM 中均不存在；页面只显示配置状态，切换到第二个 profile 后接口返回 active_profile_id=two。 |
| P5 | 通过。设置页暂停与恢复均真实 POST 既有接口，随后旧 schedules.html 的同一 task id 展示对应操作与状态；只写 /tmp 合成任务。 |
| P6 | 通过。隔离真实浏览器逐一打开 18 个加载共用导航的页面（超过合同 16 页），每页六项顺序准确，旧版九个路径及 events.html 均 HTTP 200；events 实际视图按钮仍在。工具与旧版组、当前项展开与高亮另有 UI 断言。 |
| P7 | 通过。clues 里的从事项导入、建立待确认事项不可见；追加依据真实打开编辑框。助手合成事件回执可见，事件跳转按钮隐藏。随后在 /tmp 用保留的 promote API 建立关联，验证「关联事项」链接也隐藏，events 详情 API 仍返回原数据。clues 相关 UI 测试全部通过，原逻辑保留。 |
| P8 | 通过。隔离页面五次 [0.0706, 0.0226, 0.0243, 0.0234, 0.0479] 秒；8766 五次 [0.9138, 0.226, 0.2121, 0.2143, 0.2102] 秒。7 个来源与 9 个任务分别显示，不混淆口径；overview 的 schedule_task_count 与实际任务列表一致。 |

## 来源数据依据

所有 SQLite 均 `mode=ro`、短超时、只读查询；不初始化 collect/intel、调用状态接口或恢复任务。缺少可靠记录时显示“未知”，悬停说明数据出处和限制。

| 来源 | 启用、成功和错误依据 |
| --- | --- |
| 推特雷达 | schedules 关联脚本/任务；scripts/radar_monitor_config.json 的显式 enabled（当前无此字段则未知）；workspace/twitter-radar/monitor/state.json 的成功轮次与 fault_active |
| Grok Bot | schedules 关联任务；grok_bot_radar.reports_dir() 各类别最近 JSON 报告的 collected_at/generated_at，不把文件 mtime 当成功时间 |
| AIsa | schedules 关联任务；workspace/aisa/aisa.db meta.last_synced；已存在 collect 缓存的状态（没有则不新建） |
| 视频榜 | schedules 关联任务；workspace/video-radar/page.json sources 的 last_success_at、status、note |
| 微信雷达 | schedules 的 wechat_radar/wechat_clues 任务及成功/失败运行记录；既有 collect 的 wechat_msg 状态缓存；任务成功不冒充采集成功，页面明确提示 |
| GitHub | schedules 的 github_clues；workspace/github/github.db state 中 last_capture/error；既有 collect 缓存 |
| intel | schedules 的 intel_wechat_mp；workspace/intel/intel.db jobs 的完成/失败记录；既有 collect 缓存，绝不调用 intel.status() |

单测将 collect.status、intel.status 替换成抛错 Mock，5 次 overview 调用前后两者调用次数都是 0；另用抛错属性验证未初始化连接器。数据库 SHA256 不变。固定七个来源是产品来源维度，任务总数单独返回，关联任务按真实 id 列示。

## 日志边界与后台服务路径

logs 白名单：service → workspace/logs/service.log；service-error → workspace/logs/service-error.log；video-radar → workspace/video-radar-launchd.log。只允许原始固定路径，文件或父目录软链均拒绝，不允许路径穿越。敏感行用 *** 截断敏感字段后的内容，保留正常行；超长单行的开头片段会丢弃。后台服务时间只 stat 下表固定路径，不读取 plist 的环境字段，不输出服务环境变量。

| 服务 | 真实日志路径（只 stat） |
| --- | --- |
| com.servasyy.article-studio | ~/Library/Logs/ArticleStudio/service.log、service-error.log |
| com.servasyy.radar-monitor | /tmp/com.servasyy.radar-monitor.stdout.log、.stderr.log |
| com.servasyy.video-radar | workspace/video-radar-launchd.log |
| com.servasyy.wechat-radar | workspace/wechat-source/radar-service.log、radar-service-error.log |
| com.servasyy.wechat-source | workspace/wechat-source/service.log、service-error.log |

/tmp 或系统临时目录中的工作区，以及复制到临时目录的模块，只 stat 副本内文件，不回落读取上述真实路径；单测覆盖。日志更新时间不表示服务运行正常，没有启停按钮。

## 测试与基线差集

- 开工快照：`/tmp/impl-p4-4-1-baseline/`，git.diff、tracked-hashes.json、git-status.txt、17 页截图、python.log、ui.log。截图禁止外网/POST，避免调用 collect/intel 等连接器；主要保留页面与导航外观。
- Python 开工记录：3545 tests，4 failures、86 errors、7 skipped；日志含 90 个唯一失败/错误 ID。**其中模板断言在基线长跑期间读到了本次修改后的模板，因此不把这一项算成既有缺陷**。已用开工模板快照 + 原测试在独立进程实际运行，该测试 1/1 通过，证据 `/tmp/impl-p4-4-1-baseline/l1-original-single-test.log`。其余原有雷达夹具缺失、AIsa proxy guard、video lab 等不在本单修复。
- Python 最终记录：`/tmp/impl-p4-4-1-final-python.log`。原文结果：

```text
Ran 3556 tests in 377.095s
FAILED (failures=3, errors=86, skipped=7)
```

- 失败/错误 ID 数：开工 90，最终 89；新增差集 `[]`，移除差集见附录。判定按完整 ID（含 subTest 标签），不按总数抵消。
- `npm run test:ui`：开工 723 / 711 pass / 11 fail / 1 skip；最终 723 / 718 pass / 4 fail / 1 skip。原 7 条导航失败已修，剩下 lab-test 三项和 tests/test_radar_links_e2e.cjs 与开工一致；日志 `/tmp/impl-p4-4-1-final-ui.log`。
- 定向：`../article-importer/.venv/bin/python -m unittest tests.test_settings_view` 11/11；`node --test tests/test_workspace_nav_ui.cjs tests/test_clues_ui.cjs` 33/33。
- 隔离 E2E：`node tests/e2e/settings_page.cjs` 23/23，结果与 18 页截图：`/tmp/settings-p4-e2e-zG6psZ`；桌面和 390px 窄屏截图已查看，布局无溢出，page-errors.json 为 0 错误。
- 真实 8766：只读 GET，不改真实任务与模型；`/tmp/impl-p4-4-1-real/results.json`、settings-production.png。来源卡数 7、任务行数 9、服务数 5；5 次加载均 <1 秒，外部/越权请求 0，脚本错误 0。

### 既有测试断言的授权更新

`tests/test_workspace_nav_ui.cjs`：保留原 12 页无 JS 静态兜底检查；运行时改验六项主导航 + 两个工具 + 九个旧版入口，新增全部共享导航页面、顺序/分组/路径、events 不在导航、当前页展开与高亮的具体断言。对应原失败七项：图标唯一、ITEMS 清单、全页渲染、lab query 高亮、作品根路径、旧导航重建、未知页容错。图标数量由 13 变 17，正则支持 group 与带连字符 id，所有图标仍检查唯一/BMP/非 emoji。没有降低路径、图标、高亮和二级导航检查。

`tests/test_write_from_item.py`：仅将旧结构句断言改为新结构句，并增加同句式最多两次、文末一处占位、只保留一处、去掉第一人称固定白名单等断言；仍验证单次假模型调用、当前 profile、180 秒上限与 allowed_tools=()。真实生成约束不靠模板文本冒充全部模型输出保证，仍需作者核对最终初稿。

## 中间轮次的随机测试误报

第一轮交付全量（3554 tests）的 C14NoFabricatedTopics 断言失败：测试禁止整个 JSON 出现字符串 233，随机 run_id 后缀恰好是 c187c233，帖子/话题数组实际为空。未修改该测试或雷达代码；在独立进程将 make_run_id 替换为同一合法 ID，故意反例稳定复现了 1 failure / 0 errors。原日志 `/tmp/impl-p4-4-1-intermediate-python.log`，反例证据 `/tmp/impl-p4-4-1-c14-flake-proof.txt`。随后对最终代码重新完整跑全量，最终 ID 差集按前述清单判断；中间误报不隐去。

## 保护边界与限制

未调用采集、AIsa、X、Buffer、R2；没有 computer use、真实桌面或 Chrome profile 操作。只用 headless 隔离 Playwright。未提交推送，未改 coordination 台账、其他子单或许可外 tracked 文件。服务重启仅依合同执行，最终 PID 2027。

静态 HTML 的无 JS 兜底原导航在允许清单之外，本单保持原样；JS 正常运行时按统一六项重建。原有失败不代表本单全量全绿，须由主控安排独立基线修复与独立复核。

## SHA256（15 个交付文件）

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

## 附录：Python 测试 ID 集合

### 开工记录（含已说明的模板重叠项）

```text
test_01_real_generated_bundle_raw_fields (test_radar_monitor_b1c.A1bAdapterTests.test_01_real_generated_bundle_raw_fields)
test_02_snapshot_sha_tamper (test_radar_monitor_b1c.A1bAdapterTests.test_02_snapshot_sha_tamper)
test_03_snapshot_missing (test_radar_monitor_b1c.A1bAdapterTests.test_03_snapshot_missing)
test_04_embedded_manifest_missing_even_index_valid (test_radar_monitor_b1c.A1bAdapterTests.test_04_embedded_manifest_missing_even_index_valid)
test_05_newer_directory_snapshot_and_wrong_index_ignored (test_radar_monitor_b1c.A1bAdapterTests.test_05_newer_directory_snapshot_and_wrong_index_ignored)
test_06_run_id_and_manifest_start_mismatch (test_radar_monitor_b1c.A1bAdapterTests.test_06_run_id_and_manifest_start_mismatch)
test_07_path_escape_and_symlink (test_radar_monitor_b1c.A1bAdapterTests.test_07_path_escape_and_symlink)
test_08_mixed_empty_partial_failure_not_started (test_radar_monitor_b1c.A1bAdapterTests.test_08_mixed_empty_partial_failure_not_started)
test_09_every_run_count_checked (test_radar_monitor_b1c.A1bAdapterTests.test_09_every_run_count_checked)
test_10_account_ok_calls_and_duplicates_checked (test_radar_monitor_b1c.A1bAdapterTests.test_10_account_ok_calls_and_duplicates_checked)
test_10_read_only_b2_monitor_view_matches_projection (test_radar_monitor_b1b.B1bTests.test_10_read_only_b2_monitor_view_matches_projection)
test_11_author_never_replaced_by_queried_handle (test_radar_monitor_b1c.A1bAdapterTests.test_11_author_never_replaced_by_queried_handle)
test_12_shared_content_filter_and_snapshot_kind (test_radar_monitor_b1c.A1bAdapterTests.test_12_shared_content_filter_and_snapshot_kind)
test_13_corrupt_authority_fails_preserves_page_and_baseline (test_radar_monitor_b1c.A1bAdapterTests.test_13_corrupt_authority_fails_preserves_page_and_baseline)
test_14_real_a1_low_coverage_does_not_publish_old_bundle (test_radar_monitor_b1c.A1bAdapterTests.test_14_real_a1_low_coverage_does_not_publish_old_bundle)
test_15_zero_exit_old_bundle_no_summary_rejected (test_radar_monitor_b1c.A1bAdapterTests.test_15_zero_exit_old_bundle_no_summary_rejected)
test_16_integration_with_b1_monitor (test_radar_feishu.FeishuSenderTests.test_16_integration_with_b1_monitor)
test_16_zero_exit_summary_unpublished_not_success (test_radar_monitor_b1c.A1bAdapterTests.test_16_zero_exit_summary_unpublished_not_success)
test_17_real_timeout_kills_child_group (test_radar_monitor_b1c.A1bAdapterTests.test_17_real_timeout_kills_child_group)
test_18_excluded_old_and_future_finished_cannot_refresh_old_start (test_radar_monitor_b1c.A1bAdapterTests.test_18_excluded_old_and_future_finished_cannot_refresh_old_start)
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='arabic')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='ascii25')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='ascii26')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='empty')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='fullwidth')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='newline')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='bool')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='huge')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='inf')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='max')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='nan')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='negative')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='none')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='over')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='string')
test_id_exact_ascii_length_and_rejection_counts (test_radar_monitor_b1b2.BoundaryTests.test_id_exact_ascii_length_and_rejection_counts)
test_legacy_state_without_page_field_is_not_damaged (test_radar_monitor_b1b2.BoundaryTests.test_legacy_state_without_page_field_is_not_damaged)
test_model_once_active_profile_timeout_and_no_assistant_start (test_write_from_item.WriteTests.test_model_once_active_profile_timeout_and_no_assistant_start)
test_multi_segment_sequence_video_builds_director_timeline (test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline)
test_negative_increment_remains_null (test_radar_monitor_b1b2.BoundaryTests.test_negative_increment_remains_null)
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='bool')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='huge')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='inf')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='negative')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='over')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='string')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='bool')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='huge')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='inf')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='negative')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='over')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='string')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='bool')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='huge')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='inf')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='negative')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='over')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='string')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='bool')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='huge')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='inf')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='negative')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='over')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='string')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='extra_field')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='fetched_at_naive')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='generated_at_number')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='hit_array')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='hit_unknown')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='id_26')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='id_newline')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='id_unicode')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='is_stale_number')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='missing_views')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='page_array')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='page_null')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='pushed_number')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='row_array')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='run_id_number')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='tweets_array')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='bool')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='float')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='null')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='str')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='two')
test_proxy_environment_cannot_escape_socket_guard (test_aisa_source.ProxyNetworkGuardTests.test_proxy_environment_cannot_escape_socket_guard)
test_rate_overflow_only_nulls_rate_keeps_valid_increment_and_interval (test_radar_monitor_b1b2.BoundaryTests.test_rate_overflow_only_nulls_rate_keeps_valid_increment_and_interval)
test_real_unchanged_snapshot_adapter_handles_enormous_integer (test_radar_monitor_b1b2.BoundaryTests.test_real_unchanged_snapshot_adapter_handles_enormous_integer)
test_timeout_marks_failed_and_keeps_old_fetched_at (test_twitter_radar_foundation.C02TimeoutKeepsOldTimestamps.test_timeout_marks_failed_and_keeps_old_fetched_at)
test_zero_increment_is_zero_and_valid (test_radar_monitor_b1b2.BoundaryTests.test_zero_increment_is_zero_and_valid)
```

### 最终失败/错误

```text
test_01_real_generated_bundle_raw_fields (test_radar_monitor_b1c.A1bAdapterTests.test_01_real_generated_bundle_raw_fields)
test_02_snapshot_sha_tamper (test_radar_monitor_b1c.A1bAdapterTests.test_02_snapshot_sha_tamper)
test_03_snapshot_missing (test_radar_monitor_b1c.A1bAdapterTests.test_03_snapshot_missing)
test_04_embedded_manifest_missing_even_index_valid (test_radar_monitor_b1c.A1bAdapterTests.test_04_embedded_manifest_missing_even_index_valid)
test_05_newer_directory_snapshot_and_wrong_index_ignored (test_radar_monitor_b1c.A1bAdapterTests.test_05_newer_directory_snapshot_and_wrong_index_ignored)
test_06_run_id_and_manifest_start_mismatch (test_radar_monitor_b1c.A1bAdapterTests.test_06_run_id_and_manifest_start_mismatch)
test_07_path_escape_and_symlink (test_radar_monitor_b1c.A1bAdapterTests.test_07_path_escape_and_symlink)
test_08_mixed_empty_partial_failure_not_started (test_radar_monitor_b1c.A1bAdapterTests.test_08_mixed_empty_partial_failure_not_started)
test_09_every_run_count_checked (test_radar_monitor_b1c.A1bAdapterTests.test_09_every_run_count_checked)
test_10_account_ok_calls_and_duplicates_checked (test_radar_monitor_b1c.A1bAdapterTests.test_10_account_ok_calls_and_duplicates_checked)
test_10_read_only_b2_monitor_view_matches_projection (test_radar_monitor_b1b.B1bTests.test_10_read_only_b2_monitor_view_matches_projection)
test_11_author_never_replaced_by_queried_handle (test_radar_monitor_b1c.A1bAdapterTests.test_11_author_never_replaced_by_queried_handle)
test_12_shared_content_filter_and_snapshot_kind (test_radar_monitor_b1c.A1bAdapterTests.test_12_shared_content_filter_and_snapshot_kind)
test_13_corrupt_authority_fails_preserves_page_and_baseline (test_radar_monitor_b1c.A1bAdapterTests.test_13_corrupt_authority_fails_preserves_page_and_baseline)
test_14_real_a1_low_coverage_does_not_publish_old_bundle (test_radar_monitor_b1c.A1bAdapterTests.test_14_real_a1_low_coverage_does_not_publish_old_bundle)
test_15_zero_exit_old_bundle_no_summary_rejected (test_radar_monitor_b1c.A1bAdapterTests.test_15_zero_exit_old_bundle_no_summary_rejected)
test_16_integration_with_b1_monitor (test_radar_feishu.FeishuSenderTests.test_16_integration_with_b1_monitor)
test_16_zero_exit_summary_unpublished_not_success (test_radar_monitor_b1c.A1bAdapterTests.test_16_zero_exit_summary_unpublished_not_success)
test_17_real_timeout_kills_child_group (test_radar_monitor_b1c.A1bAdapterTests.test_17_real_timeout_kills_child_group)
test_18_excluded_old_and_future_finished_cannot_refresh_old_start (test_radar_monitor_b1c.A1bAdapterTests.test_18_excluded_old_and_future_finished_cannot_refresh_old_start)
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='arabic')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='ascii25')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='ascii26')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='empty')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='fullwidth')
test_each_id_input_direct_to_both_consumers (test_radar_monitor_b1b2.BoundaryTests.test_each_id_input_direct_to_both_consumers) (label='newline')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='bool')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='huge')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='inf')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='max')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='nan')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='negative')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='none')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='over')
test_each_raw_metric_boundary_and_consumer_compatibility (test_radar_monitor_b1b2.BoundaryTests.test_each_raw_metric_boundary_and_consumer_compatibility) (value='string')
test_id_exact_ascii_length_and_rejection_counts (test_radar_monitor_b1b2.BoundaryTests.test_id_exact_ascii_length_and_rejection_counts)
test_legacy_state_without_page_field_is_not_damaged (test_radar_monitor_b1b2.BoundaryTests.test_legacy_state_without_page_field_is_not_damaged)
test_multi_segment_sequence_video_builds_director_timeline (test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline)
test_negative_increment_remains_null (test_radar_monitor_b1b2.BoundaryTests.test_negative_increment_remains_null)
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='bool')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='huge')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='inf')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='negative')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='over')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='hourly_delta', value='string')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='bool')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='huge')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='inf')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='negative')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='over')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='interval_hours', value='string')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='bool')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='huge')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='inf')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='negative')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='over')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views', value='string')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='bool')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='huge')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='inf')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='negative')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='over')
test_page_state_all_numeric_fields_strict_safe_range (test_radar_monitor_b1b2.BoundaryTests.test_page_state_all_numeric_fields_strict_safe_range) (field='views_delta', value='string')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='extra_field')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='fetched_at_naive')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='generated_at_number')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='hit_array')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='hit_unknown')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='id_26')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='id_newline')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='id_unicode')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='is_stale_number')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='missing_views')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='page_array')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='page_null')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='pushed_number')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='row_array')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='run_id_number')
test_page_state_containers_ids_required_fields_and_markers (test_radar_monitor_b1b2.BoundaryTests.test_page_state_containers_ids_required_fields_and_markers) (label='tweets_array')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='bool')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='float')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='null')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='str')
test_page_state_schema_exact_integer_one (test_radar_monitor_b1b2.BoundaryTests.test_page_state_schema_exact_integer_one) (label='two')
test_proxy_environment_cannot_escape_socket_guard (test_aisa_source.ProxyNetworkGuardTests.test_proxy_environment_cannot_escape_socket_guard)
test_rate_overflow_only_nulls_rate_keeps_valid_increment_and_interval (test_radar_monitor_b1b2.BoundaryTests.test_rate_overflow_only_nulls_rate_keeps_valid_increment_and_interval)
test_real_unchanged_snapshot_adapter_handles_enormous_integer (test_radar_monitor_b1b2.BoundaryTests.test_real_unchanged_snapshot_adapter_handles_enormous_integer)
test_timeout_marks_failed_and_keeps_old_fetched_at (test_twitter_radar_foundation.C02TimeoutKeepsOldTimestamps.test_timeout_marks_failed_and_keeps_old_fetched_at)
test_zero_increment_is_zero_and_valid (test_radar_monitor_b1b2.BoundaryTests.test_zero_increment_is_zero_and_valid)
```

### 新增差集

```json
[]
```

### 移除差集

```text
test_model_once_active_profile_timeout_and_no_assistant_start (test_write_from_item.WriteTests.test_model_once_active_profile_timeout_and_no_assistant_start)
```
