# 素材库清理 + E2E 写入防护 复核合同（复核窗口 B term_57dd3162）

对象：A 的交付 task_1dc1233a6d12（worker_done msg_81bb3576c061），报告 `coordination/impl-items-cleanup-report.md`，SHA 清单 `coordination/items-cleanup-evidence/sha256.json`。实施合同 `coordination/impl-items-cleanup-contract.md`。

## 必查
1. 数据：回收的正好是 candidates.json 中「疑似测试且原 trashed_at 为空」的 39 个 ID；5 条不确定、62 条真实、原已回收 1 条的行 SHA 与备份 `/tmp/items-cleanup-20261030T071240Z/items.before.db` 对照不变；总数 107（或新增可归因）。没有 UPDATE/DELETE 绕过产品动作的痕迹。
2. 链路：「我的素材」列表与搜索不再出现这 39 条；回收站 40 条；H5 导出预演计数 67（输出到 /tmp，不碰正式导出）；/tmp 副本重跑迁移不恢复；回滚清单可用（在 /tmp 副本上演练 restore 并恢复导出预演为 106，**不在真实库上演练**）。
3. 备份：是 SQLite backup API 的一致快照，含 WAL 数据；正式 `library-export.json` SHA 未变。
4. 防护：5 个脚本 + knowledge_first/batch_delete 在任何参数下都不能写真实 8766 或正式 workspace。自己另造反例（不只复跑 A 的）：localhost / [::1] / 0.0.0.0 / 带路径和尾斜杠的 base、3000 端口、符号链接 workspace、`context.request` 直接 POST、页面重定向或 `window.open` 到生产端口、Node 端 fetch/http；全部要被拦。
5. 测得出错：在 /tmp 副本去掉或改坏防护，你的反例必须报 F。
6. 不误伤：不带 `--real` 的原有用例照常通过（对比改动前通过数）；产品代码零改动（`git diff --stat` 只涉及允许的测试文件）；`run.cjs` 的改动只为接入防护。
7. SHA：11 个测试文件与 sha256.json 一致。

## 约束
只读复核，不改正式文件和数据；不在真实库做 restore 或任何写；不重启 8766；隔离 Playwright，禁止 computer use；禁止 launchctl print / ps eww / env / 打印 plist；不提交不推送。

## 交付
`coordination/review-items-cleanup-report.md`，开头「决定要点」表（≤10 行：结论 通过/不通过、H/M/L、SHA 核对、测试数字、证据路径），完成后 worker_done。
