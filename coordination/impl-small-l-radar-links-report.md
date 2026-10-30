# 小单 L：radar-links 清理阶段挂起修复

## 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **本单实施及自测通过，待独立复核**；未提交、未推送 |
| 根因一句话 | `after` 先等待 `server.close()`，后关闭浏览器；未发送 HTTP 请求的已建立连接不能被 idle 清理，导致前一钩子等待、后一钩子无法执行 |
| H / M / L | 本单自测未发现新增问题；非独立审计评级 |
| 改动文件 | `tests/test_radar_links_e2e.cjs`；本报告；未改共享辅助或业务代码 |
| 测试文件 SHA256 | `0e04ae58d78f6b5c743e97939643e2170abf95cf2ce416f0ffc27f7a1fe87cac` |
| 连续单跑 | **20/20** 通过并自行退出；每次 **0.988–2.576 秒**，无超时、无人工终止 |
| UI 全量 | **723 项，719P / 3F / 1S**，**17.649 秒自行退出**；与基线完全一致，3F 均为受保护 lab-test |
| 反例与异常退出 | 零字节连接：修前复现阻塞、修后退出；故意断言失败/启动前失败均正确报失败且退出，**2/2** |
| 证据 | `/tmp/small-l-radar-iqb57grf/`；正式目录仅本测试和本报告为本单写入 |

## 根因证据与确定性边界

环境：Node **v22.22.1**，独立 Playwright headless Chrome、自动临时 profile、静态服务监听 `127.0.0.1:0`。所有探针和公共静态快照副本在 `/tmp`，不使用真实 8766。

1. **观察：清理按注册顺序执行。** 原测试先注册 `t.after(() => new Promise(resolve => server.close(resolve)))`，随后注册 `t.after(() => browser.close())`。`natural-1.log` 至 `natural-3.log` 均显示 `server.close begin → server.close done → browser.close begin → browser.close done`，普通单跑不能保证出现故障连接。
2. **观察：零字节已建立连接稳定卡住原钩子。** 在 `/tmp/small-l-radar-iqb57grf/tests/diagnostic.cjs` 的真实浏览器断言执行完后，用 `node:net` 向同一随机端口创建一个连接，并等服务端确认接受。`zero-byte-before.log` 显示测试正文已结束，服务端连接 `bytesRead=0, bytesWritten=0, destroyed=false`；进入 `server.close begin` 后 **1.5 秒仍未回调**，也没有进入 `browser.close begin`。当时资源包含 **2 个 TCPSocketWrap、1 个 ProcessWrap**，活动句柄含 Chromium ChildProcess **PID 2448**。这是本次诊断自身的 PID，不是当前仍运行进程。
3. **观察：释放连接直接解除等待。** 同一日志随后仅销毁本诊断创建的客户端 socket，立即出现 `server.close done → browser.close begin → browser.close done`，测试退出。未杀浏览器、未杀其他窗口或真实服务，未用强制进程退出伪装通过。
4. **观察：本机 Node 实现与现象一致。** `node-http-close-source.txt` 从当前 Node 内嵌 `_http_server` 源码导出：`close()` 只先调用 `closeIdleConnections()`，然后调用 `net.Server.close()`；`closeAllConnections()` 才销毁全部已登记连接。零字节连接的实际探针表明它未被 idle 清理回收。
5. **推断与未知：** 已证实本测试存在上述可复现的清理依赖阻塞。H4 历史报告记录挂起时仅余一条随机端口已建立连接，与本机制吻合；但历史现场未保留 `bytesRead`，**不能断言那两次连接一定由 Chrome 预连接产生**。本次额外尝试 `<link rel=preconnect>` 的独立 Chrome 页面未产生目标连接，见 `chrome-preconnect.log`；本报告不把此尝试当作自然复现证据。合成零字节连接用于证明故障机制，与修后全量不挂起的实测分别列明。

## 改动及断言完整性

将两个清理钩子合成一个：先 `await browser.close()`，再在 `finally` 中调用 `server.close(callback)`，随后 `server.closeAllConnections()`，最后等待 close 回调。先停止接收，再释放现存连接；浏览器启动失败或测试断言失败也会走服务端清理，关闭错误仍使测试失败。

保留原 **30 秒**测试超时，没有 `process.exit`、强制成功、skip、重试或放宽链接断言。`context` 创建至测试末尾与本单接手版本逐字一致，三个视图、链接非空、作者与帖子 ID 精确对应、格式、六个旧坏 ID 和页面错误检查均保留。

接手时该测试已有前序未提交的隔离改造。本单以接手内容为基线（SHA256 `720997598cd4580eeb82c82064404dcc8f2fd14426a90b5f6b1569ab4989ac61`），没有覆盖为 Git HEAD。仅本单增量见 `/tmp/small-l-radar-iqb57grf/fix.patch`；原始副本 `/tmp/small-l-radar-iqb57grf/tests/original.cjs`。

## 验证

| 验证 | 实际结果与证据 |
|---|---|
| 修前普通单跑 | 3/3；`natural-1.log`、`natural-2.log`、`natural-3.log`，用于确认钩子顺序，不冒充挂起复现 |
| 修前零字节探针 | 阻塞到诊断主动释放自己的 socket；`zero-byte-before.log`、`tests/diagnostic.cjs` |
| 修后相同零字节探针 | 浏览器关闭后仍有零字节 socket，服务端照常关闭；`zero-byte-after.log`、`tests/fixed-diagnostic.cjs`；`beforeExit` 仅剩标准输出的 PipeWrap，无 TCP/ProcessWrap |
| 错误断言检错能力 | `/tmp` 将原帖集合检查故意改为 false，得到预期 `ERR_ASSERTION`、0P/1F、退出码 1；两端清理完成，1.464 秒正常退出；`assertion-failure.log` |
| 启动前失败 | `/tmp` 在 launch 前注入 `INJECTED_LAUNCH_FAILURE`，0P/1F、退出码 1；服务端仍关闭，0.241 秒正常退出；`launch-failure.log` |
| 正式文件连续 20 次 | 每次执行 `node --test tests/test_radar_links_e2e.cjs`；20 次均 1P/0F、退出码 0；`single-results.json` 与 `single-01.log` 至 `single-20.log`；串行经过时间合计 26.423 秒 |
| 获批 UI 全量 | `npm run test:ui`，2026-10-30 **12:38:05–12:38:22 北京时间**；`ui-full.log`、`ui-full-result.json`；退出码 **1**，因为仍有基线 3F，进程自行退出，无清理干预 |

全量资源先以 `msg_f0e32a94e2dd` 登记，主控以 **`msg_c13dda9efd07` 明确批准**后启动；结束后 `msg_eecda67c6874` 告知释放。未与其他窗口并跑 UI 全量。

全量目标测试为 **第 664 项**，**2045.736 ms 通过**。既有三项失败精确位于 `tests/test_lab_test_drama_ui.cjs`：136 行“初始载入 4 幕”、151 行“切换题材预设”、166 行“生成单幕参数”，与合同基线相同；本单未改或重试这些受保护测试。

## 保护边界与交付范围

本窗口未重启 8766；未操作用户桌面或真实 Chrome profile；未向真实 workspace、`~/grok-bot-reports`、真实 tasks.db 写入；未修改 `public/lab-test.*`、`generation/*`、`tests/test_*lab*`。只使用测试自身随机端口静态服务与隔离 Playwright。没有提交、推送或改动共享测试辅助。

后续仅需按原流程独立复核；本单不追加其他修复。有限的 20 次单跑和一次全量不能证明未来所有环境永不挂起，但已同时消除已验证的清理阻塞机制并满足本单规定的重复与全量门槛。
