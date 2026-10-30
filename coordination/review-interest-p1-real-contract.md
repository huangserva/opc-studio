# 1 期兴趣通道 真实文件补验（复核合同）

派单：主控 term_b884acc7（run_17dc13f37a10）；复核 Codex term_57dd3162。依据：coordination/review-interest-p1-report.md 第 9 项「真实文件验收待补」。
真实文件：~/grok-bot-reports/interest/2026-10-30-1038.json（及同名 .md），Grok 10:38 写出。只读该文件，不改它。

## 要做（逐条 通过/失败/未知 + 证据）
1. 文件结构：items 条数、每条必需字段是否齐（id、url、full_text、what_new、usable、作者、时间等，以 1 期实施合同 G 项为准）；坏条目计数；给字段统计，不贴全文。
2. 用隔离 Playwright（临时 profile）对真实 8766：今日页 → 「兴趣」标签 → 能看到该文件条目（条数与接口一致）→ 打开一条看全文（逐字与文件一致）→ 点「存进素材库」→ 素材库搜到该条。**本单允许的真实写入只有这一条收藏**，报告写明 id 与 items 记录状态（status=kept）。再点一次「存」验证不重复。
3. 截图 1440 / 390 存 /tmp/review-interest-p1-real/：兴趣列表、全文阅读区、素材库搜索结果。
4. 性能：真实兴趣接口耗时 5 次。
5. 不点「准 / 不准」（会写真实 feedback），除非只读演示不可行；不改兴趣清单。

## 规则
只读复核，不改正式代码。访问前确认 PID（实施窗口可能刚为 H1 重启过 8766）。禁止 computer use。不动 Grok Bot 设置、不碰飞书。不提交不推送。

## 报告
coordination/review-interest-p1-real-report.md，开头「决定要点」表（≤10 行：结论 通过/不通过、H/M/L、写入的 id、截图路径、关键数字）。完成 worker_done。
