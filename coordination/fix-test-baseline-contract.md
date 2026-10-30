# 合同：fix-test-baseline（Codex 实施 · Orca）：全量测试基线修复

派工条件：4-1 收口后、4-2 开工前派。4-2 到 4-4 拆分 server.py 要靠全量测试兜底，现在基线上有 90 多项失败，会把真正的回退掩盖掉。
基线来源：`/tmp/impl-p4-4-1-baseline/python.log`（3545 项：4 failures、86 errors、7 skipped）、`/tmp/impl-p4-4-1-baseline/ui.log`（test:ui 723 项：11 fail，其中 7 项旧导航断言由 4-1 处理）。开工后先重跑一次全量，以最新结果为准。

## 已知失败分布（2026-10-30 00:45）
- `test_radar_monitor_b1b2` 66 项、`test_radar_monitor_b1c` 18 项 ERROR：测试读取 `/tmp/viral-b1c-deliver/evidence/live/round-1-bundle.json` 等 /tmp 下的历史交付证据，文件早已被清理（FileNotFoundError）。
- FAIL 各 1 项：`test_radar_monitor_b1b`、`test_radar_feishu`、`test_aisa_source`（AIsa proxy guard）、`test_write_from_item`（L1 模板旧断言，4-1 会处理，这里只核对）。
- ERROR 各 1 项：`test_video_lab`、`test_twitter_radar_foundation`（C02TimeoutKeepsOldTimestamps）。
- test:ui：lab-test 3 项、radar_links 1 项；e2e `tests/e2e/twitter_radar_page.cjs` 的 M3a–M3d。
- **随机误报**：`C14NoFabricatedTopics` 断言「整个 JSON 里不能出现 233」，随机 run_id 后缀 `c187c233` 正好命中，导致偶发失败（证据 `/tmp/impl-p4-4-1-intermediate-python.log`）。修法：断言改成只检查帖子和话题字段，或者把随机数固定。另外 grep 全部测试，找出其他对整个 JSON 做子串断言、可能被随机 id 撞上的地方，一并修。
- **时序竞争（偶发）**：`test_aisa_live_chain.ReworkBTests.test_manual_title_survives_second_and_third_refresh` 在全量里偶发失败，单独跑能通过。堆栈显示：上一轮 done 已经写入，但 manual.lock 还没释放，下一轮就启动了，发生竞争（4-1 复核 10-30 发现）。先判断这是测试时序问题，还是业务代码锁释放的真 bug（真 bug 按第 3 条先报主控）。修好后用循环跑 50 次证明稳定。

## 要求
1. 每项先查清根因，分三类，写进报告：①**测试自身问题**（依赖 /tmp 证据、时间、网络、执行顺序）；②**业务代码真回退**；③**需求已经变了，断言过时**。
2. ① 类：改成自带夹具，证据最小化后放进 `tests/fixtures/<模块>/`，脱敏，不放任何凭据。不允许改成 skip，也不允许删测试。原来断言的具体值要保留；夹具必须是原证据的真实子集，不能编造，并注明来源。
3. ② 类：**先停下来**，用 `orca orchestration ask` 报主控，写明哪个提交或哪次改动造成的、影响什么。主控确认后再修业务代码。
4. ③ 类：按现行合同或规划更新断言，写明依据是哪份文件的哪一节，新断言不能比旧的弱。
5. 允许改动：`tests/**`、`tests/fixtures/**`。业务代码只有在主控按第 3 条确认后才能改。

## 已知弱点与反例（逐条自测）
- G1 只看数字：报告附开工和交付两份全量的测试 ID 清单，每个原失败 ID 写明现在的状态和修法。
- G2 钻漏洞：把测试改成 skip、`expectedFailure`、`try/except pass`、放宽成「存在即可」，都算不通过。用 `git diff tests/` 自查，并在报告里列出所有删掉或弱化的 assert（应为 0）。
- G3 夹具造假：每个夹具都写明从哪份原证据裁出来的。原证据已丢失的，说明用什么真实数据重建（例如正式库只读导出），不许手写假数据冒充。
- G4 隔离：测试不得读取 /tmp 下的历史证据、真实 workspace 或网络；在干净的 /tmp 克隆副本里跑全量也要通过。
- 不得输出服务环境变量（禁止 `launchctl print`、`ps eww`、`env`、`cat *.plist`）。只用隔离 Playwright，禁 computer use。不提交不推送。

## 交付门槛
- Python 全量：0 failures、0 errors（skipped 列明原因）。在共享工作区和干净克隆副本里各跑一次。
- test:ui 全过；`tests/e2e/twitter_radar_page.cjs` 全过。
- 报告 `coordination/fix-test-baseline-report.md`，开头放决定要点表（≤10 行）。完成后 `orca orchestration send --type worker_done`。
