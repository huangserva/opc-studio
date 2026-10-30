# 小单 H1-L1 复核合同（复核窗口 = A term_02017284，实施在 B）
对象：task_d1ed0df1102f，报告 coordination/impl-small-h1-l1-report.md；改动 scheduled_tasks.py（仅循环异常边界）、external_tasks.py、public/settings.js、tests/test_external_refresh_isolation.py、tests/e2e/external_tasks.cjs、tests/e2e/external_tasks_fixture.py。8766 当前 PID 77037（已加载）。
必查：1) 六文件 SHA 与报告一致；scheduled_tasks.py 相对开工基线只动了 tick/external refresh 的异常边界（diff 给出）；2) 定向测试全过；3) 反例：/tmp 副本让 external refresh 连续抛 RuntimeError → health.ok 仍 true、tick 照常、外部快照保留旧值、独立错误与时间有值；恢复后清空；tick 自身抛异常 → 原 last_error 行为不变；把隔离改回旧写法，新测试应 F；4) H4 看门狗相关文件与测试 SHA 未变，H4 定向测试仍过；5) 隔离 Playwright 只读看真实 8766 设置页总览 1440/390 正常。
约束：只读；不重启；不改正式文件；禁止 launchctl print / ps eww / env / 打印 plist；不提交不推送。
交付：coordination/review-small-h1-l1-report.md，决定要点表，worker_done。
