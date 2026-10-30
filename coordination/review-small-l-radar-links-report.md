# 小单 L：radar-links 清理修复独立复核

## 决定要点

| 项目 | 结论 |
|---|---|
| 复核结论 | **通过**；合同 4 项全部满足 |
| H / M / L | **0 / 0 / 0** |
| 被审文件及 SHA256 | `tests/test_radar_links_e2e.cjs`；`0e04ae58d78f6b5c743e97939643e2170abf95cf2ce416f0ffc27f7a1fe87cac` |
| 增量与 W4 | 与交付 patch 完全一致，仅清理相关改动；context 创建至文件末尾逐字一致 |
| 连续单跑 | **5/5**，各 1P/0F、退出码 0，1.091–1.808 秒自行退出 |
| 故障机制对照 | 旧版 close 阻塞 1500ms，释放探针后继续；新版无需释放探针自行退出 |
| 故意断言失败 | **0P/1F、ERR_ASSERTION、退出码 1**，0.922 秒自行退出 |
| 写入与边界 | 正式目录仅新增本报告；未改被审文件、未重启 8766、未提交推送、未跑 UI 全量 |
| 独立证据 | `/tmp/review-small-l-radar-azum06ft/` |

## 范围与方法

按 `coordination/review-small-l-radar-links-contract.md`，复核实施 task_c2cda14140e7 的交付。本次复核任务 `task_03a100107537`，Dispatch `ctx_7fc3116ea911`；实施和复核分别在不同窗口进行。

运行环境为 Node v22.22.1。5 次执行正式测试命令 `node --test tests/test_radar_links_e2e.cjs`；测试使用 headless Chrome、Playwright 自动独立临时 profile、127.0.0.1 随机端口，未操作黄总桌面或真实 Chrome profile。反例使用 `/tmp` 下复制的 public 静态资源及测试副本，均保留真实三视图浏览器路径与原断言。

## 逐项验收

| 合同项 | 判断 | 独立观察与证据 |
|---|---|---|
| SHA、清理增量及 W4 | 通过 | 被审 SHA 与合同一致；基线 SHA 为 `720997598cd4580eeb82c82064404dcc8f2fd14426a90b5f6b1569ab4989ac61`；独立生成 unified diff 与实施 `fix.patch` 完全相同；改动段之前逐字相同，`const context = await browser.newContext();` 至文件末尾逐字相同。见 `static.json`、`independent.patch` |
| 单跑 5 次且自行退出 | 通过 | 退出码全为 0，每次 1P/0F，无人工终止。墙钟分别 1.808、1.440、1.263、1.159、1.091 秒。见 `singles.json`、`single-1.log` 至 `single-5.log` |
| 零字节连接旧阻塞、新退出 | 通过 | 相同探针在浏览器全部原断言结束后建立 TCP 连接，确认服务端接受且读写均为 0；旧版等待 1500ms 时 close 未回调、浏览器清理尚未开始，销毁本探针客户端后 close 于 1501ms 回调，随后浏览器关闭；新版先关闭浏览器，再在仍有读写为 0 且未销毁的服务端连接时开始 close，回调耗时 0ms，无探针主动释放，1.053 秒自行退出。见 `probe-original.log`、`probe-fixed.log` 及 `tests/probe-*.cjs` |
| 故意改错仍失败并退出 | 通过 | 仅在副本把 `assert.ok(expected.has(href), ...)` 改成 `assert.ok(false /* REVIEW_INJECTED_ASSERTION */, ...)`；真实页面运行至该原帖链接断言后报 `ERR_ASSERTION`，0P/1F、退出码 1，0.922 秒自然退出。见 `tests/failure.cjs`、`failure.log` |

## 判断依据与边界

观察：修复把两个串行注册的清理钩子合并为一个，先关闭浏览器，finally 中停止服务接入并释放现存 HTTP 连接，仍等待 close 回调，关闭错误继续传递。未修改测试 30 秒超时，未引入 skip、重试、强制成功或 `process.exit`；全部链接非空、作者/帖子 ID、格式、六个坏 ID 和页面错误断言保持不变。

推断：旧清理顺序可被已接受但未发 HTTP 请求的连接阻塞，本修复消除了本次探针验证的阻塞路径。未知：历史挂起连接是否确由 Chrome 预连接产生，当前证据不足；本次不把合成探针视为历史现场复现，也不把有限次数通过扩展为所有环境永不挂起。

复核探针首轮使用一次 setImmediate 等待服务端接受连接，旧版正常复现，新版出现探针自身的接受事件时序断言失败；原始日志保留为 `probe-*-first-attempt.log`。随后仅将 `/tmp` 探针等待改为有界等待服务端接受事件，旧新版采用完全相同的探针再跑，得到上述有效对照；该首轮问题属于复核夹具，不归因于被审文件。

未复跑 UI 全量；实施报告中的 723 项、719P/3F/1S 属其既有证据，不计入本次独立测试数字。未改视频实验室文件、共享辅助、业务代码或真实作品数据；未启动/重启正式服务，未提交或推送。被审文件交付前再次核对 SHA 一致；本合同无剩余未验证项。
