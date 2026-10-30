# 推特情报 1 期兴趣通道 复核合同

派单：主控 term_b884acc7（run_17dc13f37a10）；复核 Codex term_57dd3162（实施是 term_02017284）。
仓库 /Users/huangzongning/development/article-studio；查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`，禁止 launchctl print / ps eww / env / cat plist。

## 要读
实施合同 coordination/impl-interest-p1-contract.md（G1–G8）；实施报告 coordination/impl-interest-p1-report.md（13 个文件 SHA，先核对与工作区一致）；coordination/twitter-intel-handoff-r1.md A 段与「补充」节。

## 复核重点（逐条 通过/失败/未知 + 证据，独立构造反例，不只跑工人的测试）
1. interest-topics.md 保真：真实文件现为 10-30 09:52 mtime、权限 -rw-------（Grok 原建于 09:38，权限未知）。对照 Grok 原格式（标题+引言、`## 当前方向` 列表、`## 说明` 3 条）判断内容是否逐字恢复；权限从 644 变 600 是否是实施改的、是否影响 Grok Bot 读取（同用户应可读），给出是否需要保持原权限的建议。增删保存、mtime 冲突 409、换行/#/超长/重复项拒绝。
2. 写边界：工作台在 ~/grok-bot-reports 只写 interest-topics.md 与 feedback/studio-feedback.json；临时文件不残留；测试不写真实目录（W2）。
3. 读取健壮性：缺字段、坏链接、x.com.evil.com、twitter.com、http://、query、id 与 url 不符、重复 id 跨文件合并、空文件 / [] / items 为空（Grok 将对「本次无」写 items 为空的 json）、写一半的 JSON、.tmp、>2 MiB；usable 中 javascript:/data:；full_text 含 script/onerror。页面不崩、不执行脚本、坏条目计数显示。
4. 兴趣标签与爆款不混排；卡片字段（what_new、可用链接中文类型、相关度、作者/时间、来源图标）；阅读区全文与不完整标记；按钮仅 存/准/不准。
5. 入库：仅点「存」写 items（status=kept），按帖子 id 幂等，素材库可搜；准/不准落盘带 channel=interest；爆款通道自动入库未改。
6. 兼容新字段：日报/快报新增 is_retweet、original_author、original_author_followers、bookmarks、collected_at、crowding（含类型异常）时 grok_bot_radar.py 与 today_view.py 不报错。
7. 性能：接口 < 1 秒（G6 夹具量级）。
8. 测试质量：对照 /tmp 下 T5 收口基线或 git 状态查测试 diff，确认未放宽既有断言；「旧素材库 E2E 导航断言陈旧（期待 5 个、现 11 个）」属实与否、是否与本单无关。
9. **真实文件验收**：Grok 兴趣通道 10:30 跑，文件落 `~/grok-bot-reports/interest/<日期>-HHMM.json`。复核过程中若文件已出现（可能要等写入审批），用隔离 Playwright 对真实 8766 走：今日页 → 兴趣标签 → 看全文 → 存 → 素材库搜到（存这一步会真实写入 items，可接受；完成后在报告列出写入的 id），截图 1440/390 存 /tmp/review-interest-p1/。只读该文件不改它。若复核结束时仍未出现，报告标「真实文件验收待补」，主控另派。

## 规则
只读复核，不改正式代码；必要改动验证在 /tmp 副本做。全量如需跑先向主控登记。禁止 computer use。不动 Grok Bot 设置、不碰飞书、不停 AIsa 推特采集。不提交不推送。

## 报告
coordination/review-interest-p1-report.md，开头「决定要点」表（≤10 行：结论 通过/不通过、H/M/L、真实文件验收状态、关键证据、测试数字）。完成 worker_done。
