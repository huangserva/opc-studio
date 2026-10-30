# 小单 L 复核合同（复核窗口 = A term_02017284，因实施在 B）
对象：task_c2cda14140e7，报告 coordination/impl-small-l-radar-links-report.md，只改 tests/test_radar_links_e2e.cjs（SHA256 0e04ae58d78f6b5c743e97939643e2170abf95cf2ce416f0ffc27f7a1fe87cac），增量 /tmp/small-l-radar-iqb57grf/fix.patch。
必查：1) SHA 一致；增量只在清理钩子，context 创建到测试末尾的断言与接手基线（/tmp/small-l-radar-iqb57grf/tests/original.cjs）逐字一致（W4）；2) 单跑 5 次全过且自行退出；3) 在 /tmp 副本用零字节连接探针复现：旧版阻塞、新版退出；4) /tmp 故意改错一条断言，新版仍报 F 且正常退出。不跑 UI 全量（实施已跑 723 719P/3F/1S）。
约束：只读，不改正式文件；隔离 Playwright；不重启 8766；不提交不推送；不动视频实验室保护边界。
交付：coordination/review-small-l-radar-links-report.md，开头决定要点表，worker_done。
