# 合同：review-library-p2（Codex 复核 · Orca · 只读）

对象：2 期「素材库」实施 `task_0506b954b31d`，实施报告 `coordination/impl-library-p2-report.md`，实施合同 `coordination/impl-library-p2-contract.md`（含第 0.1 节与反例 1–16）。
产出：只写 `coordination/review-library-p2-report.md` 与 `/tmp/review-library-p2/` 证据；不改业务代码、测试、真实数据、服务进程。报告开头放「决定要点」表（≤10 行：结论通过/不通过、H/M/L、SHA、测试数字）。

## 规则
- 只用隔离 Playwright（独立 profile、/tmp 副本或随机端口），禁止 computer use，不碰黄总的真实桌面和 Chrome。
- 正式 8766（PID 18529，22:25:03 启动）只做 GET；任何写入（收藏、回收、彻底删除、note、use、今日页「存/拿去写」）只在 `/tmp` 副本 + 随机端口做。
- 不提交不推送。不调用采集、AIsa、X、Buffer、R2。
- 不信实施报告的自述：每项自己复现，给原始数字和证据路径。

## 复核项
1. **SHA 与范围**：报告第 2 节 13 个文件 SHA 与磁盘一致；对开工基线的 tracked 增量只落在合同第 1 节允许文件；`server.py` 只多了 `/api/items*`、`/api/library/search` 装配行、reference 镜像一行、library.html CSP 例外；`tests/test_today_view.py` 的改动逐条说明是否只因权重封顶（合同 0.1 要求）。
2. **knowledge.db 只读**：读代码确认资料检索用只读连接（`mode=ro` 或等价），无 INSERT/UPDATE/DELETE/DDL；`knowledge_store.py` 未改。不要用整库 SHA 判断（后台采集在持续写），改为代码审 + 在 /tmp 副本上调新接口前后比 SHA。
3. **迁移正确性**：在 /tmp 副本上独立重跑 `scripts/migrate_to_items.py` 两次：条数 79（59 人存 + 19 卡片 + 1 占位父素材）、第二次逐条 hash 不变；抽 5 条对照旧库原文逐字一致（含长文/串推/附件 meta）；规范 id 规则（x:/wechat:/github:/url:/kb:/card:）各抽 1 条核对；缺失父文档的卡片 460440ed… 的父素材 `kb:87a22e6c…` 为 content_complete=false 且标明「原资料已缺失」。
4. **人写字段不被覆盖**：/tmp 上改 note/status 后重跑迁移与重复收藏，note/status/feedback 不变，sources/legacy_refs 合并不丢。
5. **并发与删除**：同 id 并发 upsert 不丢来源；只有回收站内条目可彻底删除；被引用的父素材拒绝删除；写接口缺 `X-Article-Studio: 1` 或跨 Origin 返回 403（含 DELETE）。
6. **安全**：note、标题、正文、snippet 注入 `<img src=x onerror=...>` 等，浏览器里无注入节点、无脚本执行；CSP 只 today.html、library.html 带 `https:`，其他页（twitter-radar、video-radar、index、cards、intel）不变。
7. **资料检索**：长词走 FTS + snippet、短词标题 LIKE 且最多 50 条、总数显示下界不伪造；排除 document_trash；正式 8766 上 4 个合同词各 3 次（含**首查**）均 <1 秒，素材库首屏、两个列表各 5 次 <1 秒。首查要在服务刚空闲后测，报原始数字。
8. **今日页**：权重封顶 3 生效（反例 16：R=9330/B 未知 排在 R=2594/B=412 前；R=1000 时 B=412 仍排在 B=111 前），断言具体 id 先后；用正式 48h 接口核对 dotey 2115591064073413118 与 eternityspring 2115655889428623568 的实际先后；「存」「拿去写」写入 items 且仍写原反馈文件；1 期 56 项浏览器测试不回退。
9. **真实用户路径**：隔离浏览器走一遍：今日页点「存」→ 素材库能看到 → 编辑收藏理由 → 拿去写（复制全文、status=used）→ 移入回收站 → 恢复 → 再回收 → 彻底删除（二次确认）；资料检索搜一个词 → 存为素材 → 我的素材出现。J/K 键、筛选、300ms 防抖可用。
10. **导航**：「素材库」在「今日」之后；「知识库」「创意回收」「提示词卡」在底部「旧版」分组，原路径可打开。
11. **测试强度**：实施方新增测试是否断言具体值（id 先后、逐字全文、403、落盘），而不是只查按钮存在；独立跑 Python 50 项、素材库 28 项、今日页 56 项。

H = 数据丢失/覆盖人写字段/写了 knowledge.db/安全注入/用户主路径走不通；M = 合同要求未达成但有绕路；L = 体验瑕疵。
完成后 `orca orchestration send --type worker_done`，payload 带 reportPath。
