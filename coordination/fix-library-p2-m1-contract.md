# 合同：fix-library-p2-m1（Codex 实施返修 · Orca）

上游：2 期 `task_0506b954b31d`，实施合同 `coordination/impl-library-p2-contract.md`，复核报告 `coordination/review-library-p2-report.md`（0H1M0L）。只修 M1，其余 10 项已通过，不得回退。

## M1
正式 8766 资料检索短词「微信」本轮首查 4.507 秒（后两次 0.341 / 0.280 秒），合同要求含首查每次 <1 秒。热查也要 0.3 秒，说明短词标题 LIKE 在 29 万行上扫得太多，冷页一多就超时。原始证据：`/tmp/review-library-p2/formal-get.json`、`real-get.cjs`。

## 要求
- 任意未查过的短词（1–2 字）首查和长词一样 <1 秒；结果最多 50 条、总数只显示下界，规则不变。
- knowledge.db 仍只读：不改表结构、不写、不加索引，`knowledge_store.py` 不改。
- 可选做法（自选，报告写清理由）：①在 `workspace/items/` 下建只属于素材库的旁路标题索引（从 knowledge.db 只读增量同步，按 rowid 水位，后台或首次查询时建，不阻塞页面）；②分块按 rowid 倒序扫描，设时间预算（如 0.7 秒）到点即停，返回已找到的部分，并在页面如实提示「只搜了最近 N 条资料，换长一点的词可全库搜」。不允许只靠启动预热或缓存结果过关（空闲后页缓存会丢）。
- 允许改动：`items_store.py`（或新增 `library_search.py`）、`server.py` 中 `/api/library/search` 装配行、`public/library.js`（仅提示文案）、`tests/test_items_store.py`、`tests/e2e/library_page.cjs`、报告。其他文件不动。

## 该工人已知弱点与反例（逐条自测，报告给结果）
1. 只测热查：必须重启 8766 后等待至少 5 分钟、不预热，依次首查 5 个从未查过的短词（如 微信、抖音、模型、发布、视频）各 3 次，报原始数字，每次都 <1 秒。
2. 只测命中多的词：加 1 个几乎不命中的短词（如「鲸」）首查也要 <1 秒，且返回结果不能是假的空页（部分结果要有提示）。
3. 旁路索引若采用：新写入 knowledge.db 的文档在下次同步后能搜到；同步失败只记日志，不影响页面；不得把索引写进 knowledge.db。
4. 1 期 56 项、素材库 28 项、Python 50 项不回退。
5. 后端改动后用 launchctl 重启并确认新 PID 加载新代码，再做浏览器验收；只用隔离 Playwright，禁 computer use；不提交不推送。

报告：`coordination/fix-library-p2-m1-report.md`，开头放决定要点表（≤10 行）。完成后 `orca orchestration send --type worker_done`。
