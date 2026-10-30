# H4 M1 独立复验报告

| 决定要点 | 结果 |
|---|---|
| 结论 | **M1 修复通过；完整回归门槛未知**：本轮全量副本资源不齐，不能视为正式基线通过 |
| H / M / L | **0 / 0 / 0**（本次未发现 H4 新代码缺陷）；全量证据限制另列，不隐藏为通过 |
| 关键反例 | 原 **9/9** + 新增 **15/15** 全部 invalid_health；完整正常响应 ok；独立检查合计 **33/33** |
| 定向与检错 | 原版 **26/26**；故意移除计数校验的副本 **2 项、8F/0E（预期）** |
| 真实入口 | 安装 plist 对应 `/bin/sh` + wrapper 实跑一次；日志 **6→7 行**，新增行 conclusion=ok；**42** 个保护对象不变 |
| 全量一次 | `/tmp` 快照 **3431 项、47F/18E/54S，431.926 秒**；初始结果与正式基线不可比 |
| 逐项归因 | 新增 **47F+17E** 补齐依赖/夹具后全部通过；既有 video_lab **1E** 保留；补遗漏/受影响路径 **261/261**；未发现指向 H1 的新增失败 |
| 版本与范围 | 两个返修文件 SHA 与实施报告一致、收尾未漂移；生产差异仅脚本校验与测试增量，详见下文；本窗口仅写本报告 |
| 证据 | `/tmp/article-studio-h4-m1-review/`；未重启/停止 8766、未改 plist、未提交推送；可比全量待协调者统一证据 |

## 合同逐项结论

| 项目 | 结论 | 证据 |
|---|---|---|
| 1 六字段/非对象响应独立反例 | **通过** | `independent.py` 与 `independent-results.json`：9 个原反例、15 个新增反例全部异常且含 invalid_health；完整响应仍 ok。每次走随机端口真实 HTTP，调用 check_once，核对最后一行 JSON 与结果完全一致。 |
| 2 去重与恢复 | **通过** | 同错误 3599 秒 suppressed、3600 秒 sent；不同错误立即 sent、切回同码继续抑制；恢复只通知一次。独立序列 sent/suppressed/sent/suppressed/sent/recovery_sent/none，实际 4 次通知。坏/空/非对象状态不崩。 |
| 2 日志轮转与隐私 | **通过** | 独立构造 1 MiB+1 字节旧日志和旧备份，轮转后单份备份 1048577 字节，当前一行 JSON；私有哨兵未进入日志/状态/通知；凭据扫描计数见末节。 |
| 2 只报告不改动 | **通过** | `real-run.json`、`protected-snapshot.json`：真实 tasks.db、LaunchAgents plist、Grok 报告目录/文件，共 42 对象，执行前后 mtime/size 无变化；没有新增/删除保护路径。源码写入仍仅 log_dir 下日志/状态/锁/备份，外部 subprocess 仍仅固定 osascript 通知。 |
| 3 真实运行 | **通过** | `real-run.py` 只提取安装 plist 非敏感 ProgramArguments.0/.1，确认后按原入口执行；退出 0、耗时 0.062 秒、日志恰加一行，HTTP 200、errors=[]、conclusion=ok、notification=none。未卸载/改 plist。 |
| 3 真实浏览器路径 | **通过** | `browser.cjs`、`browser.json`：独立临时 profile、headless Chrome 访问真实 8766 健康接口，GET 200、无自定义鉴权头、六字段完整且 ok=true；未用 computer use、未碰用户桌面/现有浏览器。 |
| 4 返修差异范围 | **通过（按交付前副本与已核对文件）** | `studio_watchdog.diff` / `test_studio_watchdog.diff`：前副本 SHA 分别与上一轮复核的 90174bab… / cb92c748… 一致；仅新增 timestamp 函数、age/count/六字段校验和 6 个测试方法，其他行为未改。wrapper、plist 模板、server.py 与上轮 SHA 相同。共享区 scheduled_tasks.py / schedules_assistant_api.py 属 H1 在途，不能以其相对上轮不同误判 M1 越界。 |
| 5 Python 一次全量与归因 | **未知（正式可比全量门槛）** | 本单完成一次 discover 并自然退出，原始 3431/47F/18E/54S 必须保留；补测已逐项解除 64 个新增异常，但不能将拆分补测合成为一次 3644 项全量通过。应复用协调者后续统一全量覆盖本次看门狗 26 项；本窗口未再启动第二次 discover 执行。 |

## 原反例与新增边界逐项记录

以下 25 行每行均使用独立构造的 HTTP 响应；期望异常时同时检查 invalid_health、异常结论、无崩溃与日志正常落盘。NaN/inf 以原始 JSON 非有限数形态到达 Python JSON 解码路径，确认被数值校验拒绝。

| 场景 | 结论 | 实测 |
|---|---|---|
| 原反例 last_tick 缺失 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 原反例 last_tick 数组 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 原反例 last_tick false | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 原反例 now 缺失 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 原反例 now 数组 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 原反例 now false | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 原反例 running_count 缺失 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 原反例 running_count 数组 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 原反例 running_count false | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 tick_age true | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 tick_age NaN | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 tick_age inf | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 tick_age -1 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 running_count 1.5 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 running_count 字符串0 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 running_count true | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 running_count -1 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 last_tick null | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 last_tick 无时区 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 last_tick 乱码 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 now 缺失 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 顶层数组 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 顶层null | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 新增 顶层数字 | 通过 | invalid_health；异常；JSON 日志落盘，无崩溃 |
| 完整正常响应 | 通过 | ok，errors=[] |

另 8 项为通知序列、5 个坏状态、轮转、私有哨兵，共 33/33。定向测试命令：`/Users/huangzongning/development/article-importer/.venv/bin/python -m unittest discover -s tests -p test_studio_watchdog.py -v`（cwd 为隔离 repo）；26/26，0F/0E/0S，7.494 秒，见 `targeted.log`。最初搭副本时曾因缺相邻 article-importer 依赖导致接口测试无法导入，补齐脚本副本后才得到这里的有效结果。

W2 检错实验仅修改 `/tmp` 脚本副本：删除 `or not valid_count`，运行原 9 反例测试与 running_count 类型边界测试，2 个测试方法出现预期 8 个失败子测试、0E，见 `mutation.log` / `mutation.json`。patch 用上下文管理器恢复；测试服务 finally 关闭，临时目录有清理；未改正式源码。

## 全量原始结果、资源遗漏与证据边界

本单唯一一次全量执行：`/Users/huangzongning/development/article-importer/.venv/bin/python -m unittest discover -s tests`，cwd `/tmp/article-studio-h4-m1-review/repo`；完整日志 `python-full.log`，自然退出码 1，3431 项、47F/18E/54S、431.926 秒。**隔离副本准备遗漏由本复核产生，不能把这些异常归给 H1 或 H4。**

- Pi 运行依赖未复制：47F+11E。补齐原有 `pi-runtime/node_modules` 后，仅定向重跑异常项；58/58 通过，覆盖模型请求未发生、索引为空、runtime_result 缺失、工作流启动拒绝等具体表现。原始 `failure-recheck.log` 同时包含尚未补齐热度快照的 6E，故其自身计数是 64 项 0F/6E；没有改写为全绿日志。
- 热度回放只复制了工具，漏带 `coordination/research/xradar/snapshots`：6E 均在 replay 中因空比较集除零。补齐快照后，这 6 个确切测试全部通过，见 `supplemental.json`。
- 既有 video_lab：1E，`test_multi_segment_sequence_video_builds_director_timeline`，原文 `ValueError: 参考素材宽高须为 32 的倍数`；调用链 generation/models → video_options → native_h3，与上一轮报告既有 E 一致；不属于本次脚本变动，不擅自修复或重复跑。
- 还漏带微信插件路径配置、部分本机夹具与 jsdom：影响测试加载和跳过数。补齐后只加载 suite、不执行全量，计数由 3431 变为 **3644**（14 个跳过占位被 227 个真实测试替代）；这与 3638 基线加本次 6 个测试方法的数量相符。这里只复制微信插件源码和一份只含 module_dir 的 `/tmp` 配置，未复制凭据配置。
- 依赖和夹具补齐后的遗漏/受影响项定向执行 **261 项、0F/0E/0S，15.700 秒**，详见 `supplemental.log` / `supplemental.json`；不是第二次全量，也不构造“3644 项全量通过”的合成数字。没有本单产生的失败指向 H1；这不代表 H1 自身验收已通过。

完整正式工作区与本隔离快照的回归等价性仍是 **未知**：初始 skip54 不能与正式 skip7 相混，后续不同测试进程无法证明单次全量的跨测试隔离。已通过 Orca 告知协调者需要正式可比全量证据，建议复用 H1 的后续统一全量；协调者已告知 H1 也在排队，本窗口不重复抢占全量资源。当前 M1 代码/独立反例通过与这个证据限制分别成立。协调者于 2026-10-30T03:43:00Z 通过 Orca `msg_2f82f71b33a6` 明确同意：正式可比全量由 H1 统一覆盖（包含 H4 的 26 项），本单按现状收尾；本窗口据此不追加全量。

### 65 个原始失败/错误逐项归因

每条已解除异常的准确测试 ID、重测状态及证据文件也保存为 `failure-attribution-final.json`。Pi 归因的证据是相同代码、相同测试只补 node_modules 即通过；热度归因的证据是补回该工具实际读取的快照后通过。表中不把异常提示文案本身当成根因。

| # | 原结果 | 测试 ID | 已验证归因 | 重测/处理 |
|---|---|---|---|---|
| 1 | ERROR | `test_assistant.AssistantTests.test_selected_profile_used_for_request_and_job_snapshot` | 隔离副本漏带 Pi node_modules | 通过 |
| 2 | ERROR | `tests.test_assistant.AssistantTests.test_selected_profile_used_for_request_and_job_snapshot` | 隔离副本漏带 Pi node_modules | 通过 |
| 3 | ERROR | `test_chat_attachments.ChatImageTests.test_real_image_parts_sent_history_stores_only_references` | 隔离副本漏带 Pi node_modules | 通过 |
| 4 | ERROR | `test_clues_heat.ClockLabelTests.test_fourteen_day_offline_acceptance_and_gold_coverage` | 隔离副本漏带热度回放快照 | 通过 |
| 5 | ERROR | `test_clues_heat.EvidenceKeyTests.test_fourteen_day_offline_acceptance_and_gold_coverage` | 隔离副本漏带热度回放快照 | 通过 |
| 6 | ERROR | `test_clues_heat.HeatDryRunTests.test_fourteen_day_offline_acceptance_and_gold_coverage` | 隔离副本漏带热度回放快照 | 通过 |
| 7 | ERROR | `test_clues_heat.HeatRevision3Tests.test_fourteen_day_offline_acceptance_and_gold_coverage` | 隔离副本漏带热度回放快照 | 通过 |
| 8 | ERROR | `test_clues_heat.HeatTests.test_fourteen_day_offline_acceptance_and_gold_coverage` | 隔离副本漏带热度回放快照 | 通过 |
| 9 | ERROR | `test_clues_heat.ManualHeatGuardTests.test_fourteen_day_offline_acceptance_and_gold_coverage` | 隔离副本漏带热度回放快照 | 通过 |
| 10 | ERROR | `test_pi_runtime.PiIntegrationTests.test_blocked_names_are_deduplicated_bounded_and_validated` | 隔离副本漏带 Pi node_modules | 通过 |
| 11 | ERROR | `test_pi_runtime.PiIntegrationTests.test_new_conversation_does_not_receive_previous_context` | 隔离副本漏带 Pi node_modules | 通过 |
| 12 | ERROR | `test_pi_runtime.PiIntegrationTests.test_openpi_workflow_runs_three_isolated_stages_and_saves_once` | 隔离副本漏带 Pi node_modules | 通过 |
| 13 | ERROR | `test_pi_runtime.PiIntegrationTests.test_profile_vision_spends_one_provider_request_per_fresh_session` | 隔离副本漏带 Pi node_modules | 通过 |
| 14 | ERROR | `test_pi_runtime.PiIntegrationTests.test_workflow_does_not_write_or_advance_without_stage_receipt` | 隔离副本漏带 Pi node_modules | 通过 |
| 15 | ERROR | `test_pi_runtime.PiIntegrationTests.test_workflow_review_failure_keeps_draft_and_restart_marks_running_as_interrupted` | 隔离副本漏带 Pi node_modules | 通过 |
| 16 | ERROR | `test_pi_runtime.PiIntegrationTests.test_workflow_stop_aborts_active_model_and_skips_later_stages` | 隔离副本漏带 Pi node_modules | 通过 |
| 17 | ERROR | `test_scheduled_pi_tools.ScheduledPiToolsTests.test_scheduled_preparation_obeys_deadline_and_kills_process` | 隔离副本漏带 Pi node_modules | 通过 |
| 18 | ERROR | `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline` | 既有 video_lab；参考素材宽高须为 32 的倍数 | 基线既有 E，未重复跑 |
| 19 | FAIL | `test_assistant.AssistantTests.test_cancel_and_unsaved_guard` | 隔离副本漏带 Pi node_modules | 通过 |
| 20 | FAIL | `test_assistant.AssistantTests.test_each_turn_records_model_and_keeps_private_config_out_of_history` | 隔离副本漏带 Pi node_modules | 通过 |
| 21 | FAIL | `test_assistant.AssistantTests.test_historical_commands_are_background_and_explicit_new_archive_works` | 隔离副本漏带 Pi node_modules | 通过 |
| 22 | FAIL | `test_assistant.AssistantTests.test_media_submission_receipt_needs_no_second_model_call` | 隔离副本漏带 Pi node_modules | 通过 |
| 23 | FAIL | `test_assistant.AssistantTests.test_real_protocol_loop_with_fake_model_no_credentials_in_status` | 隔离副本漏带 Pi node_modules | 通过 |
| 24 | FAIL | `test_assistant_backend_protocol.AssistantBackendProtocolTests.test_failed_gpu_and_successful_jump_diagnosis_remain_distinct_across_native_pi` | 隔离副本漏带 Pi node_modules | 通过 |
| 25 | FAIL | `test_assistant_backend_protocol.AssistantBackendProtocolTests.test_read_only_deployment_tools_reach_model_without_any_generation` | 隔离副本漏带 Pi node_modules | 通过 |
| 26 | FAIL | `test_assistant_backend_protocol.AssistantBackendProtocolTests.test_real_ssh_tool_names_and_command_output_cross_pi_protocol` | 隔离副本漏带 Pi node_modules | 通过 |
| 27 | FAIL | `test_assistant_citations.AssistantCitationTests.test_only_actual_reads_survive_restart_with_exact_old_text_and_hash` | 隔离副本漏带 Pi node_modules | 通过 |
| 28 | FAIL | `test_assistant_edit_insert.EditInsertTests.test_full_turn_through_the_agent_loop_inserts_and_reports_the_version` | 隔离副本漏带 Pi node_modules | 通过 |
| 29 | FAIL | `test_assistant_memory.MemoryPiProtocolTests.test_native_pi_can_save_explicit_note_without_an_article_context` | 隔离副本漏带 Pi node_modules | 通过 |
| 30 | FAIL | `test_assistant_memory.MemoryPiProtocolTests.test_native_pi_reads_results_and_does_not_receive_write_tools` | 隔离副本漏带 Pi node_modules | 通过 |
| 31 | FAIL | `tests.test_assistant.AssistantTests.test_cancel_and_unsaved_guard` | 隔离副本漏带 Pi node_modules | 通过 |
| 32 | FAIL | `tests.test_assistant.AssistantTests.test_each_turn_records_model_and_keeps_private_config_out_of_history` | 隔离副本漏带 Pi node_modules | 通过 |
| 33 | FAIL | `tests.test_assistant.AssistantTests.test_historical_commands_are_background_and_explicit_new_archive_works` | 隔离副本漏带 Pi node_modules | 通过 |
| 34 | FAIL | `tests.test_assistant.AssistantTests.test_media_submission_receipt_needs_no_second_model_call` | 隔离副本漏带 Pi node_modules | 通过 |
| 35 | FAIL | `tests.test_assistant.AssistantTests.test_real_protocol_loop_with_fake_model_no_credentials_in_status` | 隔离副本漏带 Pi node_modules | 通过 |
| 36 | FAIL | `test_assistant_paste_v2.PasteOriginalTests.test_same_rejection_twice_stops_before_the_quota_with_a_chinese_reason` | 隔离副本漏带 Pi node_modules | 通过 |
| 37 | FAIL | `test_fixes_b5.SameResponseStopTests.test_cross_work_save_with_equal_version_numbers_is_reported` | 隔离副本漏带 Pi node_modules | 通过 |
| 38 | FAIL | `test_fixes_b5.SameResponseStopTests.test_paste_original_keeps_the_review_whitespace_probe` | 隔离副本漏带 Pi node_modules | 通过 |
| 39 | FAIL | `test_fixes_b5.SameResponseStopTests.test_repeated_out_of_range_delete_does_not_use_the_paste_stop` | 隔离副本漏带 Pi node_modules | 通过 |
| 40 | FAIL | `test_fixes_b5.SameResponseStopTests.test_stop_after_a_real_save_says_the_version_was_kept` | 隔离副本漏带 Pi node_modules | 通过 |
| 41 | FAIL | `test_fixes_b5.SameResponseStopTests.test_two_errors_and_a_later_legal_paste_do_not_save` | 隔离副本漏带 Pi node_modules | 通过 |
| 42 | FAIL | `test_fixes_b5.SameResponseStopTests.test_unchanged_update_text_then_two_edit_errors_did_not_save` | 隔离副本漏带 Pi node_modules | 通过 |
| 43 | FAIL | `test_pi_runtime.PiIntegrationTests.test_authorized_edit_keeps_backend_read_and_version_checks` | 隔离副本漏带 Pi node_modules | 通过 |
| 44 | FAIL | `test_pi_runtime.PiIntegrationTests.test_blocked_path_is_only_for_unknown_tool_names` | 隔离副本漏带 Pi node_modules | 通过 |
| 45 | FAIL | `test_pi_runtime.PiIntegrationTests.test_blocked_tool_name_reaches_history_without_arguments` | 隔离副本漏带 Pi node_modules | 通过 |
| 46 | FAIL | `test_pi_runtime.PiIntegrationTests.test_bold_saves_and_later_violation_reports_saved_version` | 隔离副本漏带 Pi node_modules | 通过 |
| 47 | FAIL | `test_pi_runtime.PiIntegrationTests.test_capability_query_uses_real_pi_adapter_and_config` | 隔离副本漏带 Pi node_modules | 通过 |
| 48 | FAIL | `test_pi_runtime.PiIntegrationTests.test_context_is_validated_and_readable_without_keywords` | 隔离副本漏带 Pi node_modules | 通过 |
| 49 | FAIL | `test_pi_runtime.PiIntegrationTests.test_deals_pi_registers_reads_then_proposes_and_requires_confirmation` | 隔离副本漏带 Pi node_modules | 通过 |
| 50 | FAIL | `test_pi_runtime.PiIntegrationTests.test_empty_thinking_response_is_reported_as_failure` | 隔离副本漏带 Pi node_modules | 通过 |
| 51 | FAIL | `test_pi_runtime.PiIntegrationTests.test_legacy_history_keeps_text_beyond_eighty_turns` | 隔离副本漏带 Pi node_modules | 通过 |
| 52 | FAIL | `test_pi_runtime.PiIntegrationTests.test_native_pi_discovers_unified_skill_and_reads_selected_style_without_generation` | 隔离副本漏带 Pi node_modules | 通过 |
| 53 | FAIL | `test_pi_runtime.PiIntegrationTests.test_pi_compacts_by_model_tokens_and_keeps_early_fact_in_summary` | 隔离副本漏带 Pi node_modules | 通过 |
| 54 | FAIL | `test_pi_runtime.PiIntegrationTests.test_pi_searches_reads_and_preserves_citable_sources` | 隔离副本漏带 Pi node_modules | 通过 |
| 55 | FAIL | `test_pi_runtime.PiIntegrationTests.test_provider_timeout_does_not_report_partial_text_as_success` | 隔离副本漏带 Pi node_modules | 通过 |
| 56 | FAIL | `test_pi_runtime.PiIntegrationTests.test_read_chat_image_restores_original_through_native_tool_result` | 隔离副本漏带 Pi node_modules | 通过 |
| 57 | FAIL | `test_pi_runtime.PiIntegrationTests.test_restart_retains_roles_and_image_references_without_duplicate_import` | 隔离副本漏带 Pi node_modules | 通过 |
| 58 | FAIL | `test_pi_runtime.PiIntegrationTests.test_stop_cancels_stream_without_waiting_for_provider_timeout` | 隔离副本漏带 Pi node_modules | 通过 |
| 59 | FAIL | `test_pi_runtime.PiIntegrationTests.test_workflow_result_rejects_unread_links_and_generic_chat_stays_single_stage` | 隔离副本漏带 Pi node_modules | 通过 |
| 60 | FAIL | `test_pi_runtime.ProviderBudgetTests.test_default_cap_still_allows_a_second_provider_call` | 隔离副本漏带 Pi node_modules | 通过 |
| 61 | FAIL | `test_pi_runtime.ProviderBudgetTests.test_default_provider_cap_is_still_twelve` | 隔离副本漏带 Pi node_modules | 通过 |
| 62 | FAIL | `test_pi_runtime.ProviderBudgetTests.test_one_request_cap_fails_before_a_second_provider_call` | 隔离副本漏带 Pi node_modules | 通过 |
| 63 | FAIL | `test_pi_runtime.ProviderBudgetTests.test_plain_jobs_omit_vision_limits_and_flagged_jobs_keep_them` | 隔离副本漏带 Pi node_modules | 通过 |
| 64 | FAIL | `test_scheduled_pi_tools.ScheduledPiToolsTests.test_native_pi_protocol_calls_scoped_tools_without_parent_chat` | 隔离副本漏带 Pi node_modules | 通过 |
| 65 | FAIL | `test_scheduled_tasks.SchedulerTests.test_real_pi_protocol_uses_fake_transport_and_runs_only_opted_in_tool` | 隔离副本漏带 Pi node_modules | 通过 |

## 版本、凭据扫描与保护边界

| 返修文件 | SHA256（实施报告=复核副本=收尾正式文件） |
|---|---|
| scripts/studio_watchdog.py | `098f0322cec6c25a29cdf447e6eddc2ff75dc96010f4869d2e6c5ae36f8c1f7c` |
| tests/test_studio_watchdog.py | `9ea9871c1bfef5efaf33d70eecba3919dd8f570e3b49c080dfd8856023268e65` |

其他 H4 文件与 H1 快照 SHA 详见 `versions.json`、`end-version-drift.json`。收尾时 H4 两文件及 wrapper/plist 未漂移，H1 的 public/settings.js 已相对本次快照变化；本报告全量证据只覆盖快照版本，不冒称覆盖 H1 最终版本。安装 plist SHA 仍为 `cf95d559b51a465e349813c42b6919507b4c4ff5f2214fe0d8e56fc218b10a9e`。

W3 仅报告计数：扫描文件数 **42**，凭据特征命中 **0**。范围包含两份代码、返修/复核报告、模板、真实看门狗日志/状态、本轮和实施证据中的日志/状态/JSON；不输出命中原文、API Key 或完整 plist，计数见 `credential-scan.json`。

真实入口及浏览器验证发生在 2026-10-30T03:23:38Z 附近，早于协调者后续 H1 重启提醒；收到提醒后本窗口没有再访问真实 8766。未发出服务停止/重启/卸载命令、未改任何 plist、未改 Grok/飞书/雷达配置，未用 computer use、未提交/推送。正式目录唯一由本窗口新增文件为本报告，测试构造与补资源均在 `/tmp`；本单完成复验和事实报告，可比全量由协调者统一安排后关闭证据缺口。
