# H3a 返修 M4（实施窗口 A term_02017284，排在清理返修 1 之后）

依据：B 复核 task_7f49a40de867 不通过 0H/1M/3L（coordination/review-hub-h3a-report.md「返修 M3 复验」节第 390 行起，证据 /tmp/review-hub-h3a-m3/，关键 final-probes.json、negative.log）。M3 合同 impl-hub-h3a-m3-spec.md 与主控驼峰裁定继续有效。

## 要修的（M1，阻断）
- 现象：`OpenAPIs`、`myMCPs`、`newLLMs`、`localREPOs`、`theirAPIs` 在 M2 保留、M3 拒绝。原因：复数 s 只在 `word[:-1].isupper()` 时剥离，混排词被切成 `Open/AP/Is`，API 整段丢失。
- 修法：驼峰切段与缩写复数统一处理（例如切段时把「全大写段 + 小写 s 结尾」识别为缩写复数），**不得为这五个样本逐个加白名单**。
- 必须继续成立：APIs、LLMs、MCPs 独立复数保留；MCPServer、APIGateway、OpenAIClient、GenAI、xAI 保留；RAPIDServer、CAPITALOne、MAILBox、CAPITAL、REPORT、CHAIR 拒绝；MAILs、RAPIDs、CAPITALs 等普通词复数拒绝；上轮 14 探针 8 拒绝 + 6 保留；B 的 82 项独立探针中归 M1 的 5 例转为通过，其余结果不变。

## 不改的（黄总 10-30 16:5x 定）
- 即梦、可灵保持无语境产品组，不加 AI 语境条件（七天真实数据全是 AI 内容；加语境会误杀「可灵 2.5 出了」这类真帖，误杀代价大于误放）。日常句子被放行记为已知 L，以后今日页或「不准」反馈真出现再只对单独出现的词补语境。
- 7 条语义未知、Muse bot、OpenPencil 等不扩词。

## 自测（该工人已知弱点，skill worker-dispatch-profiles：Codex 历史实施）
- **整条链路 / 回归**（本线 M2→M3 就是修一处坏另一处）：上面「必须继续成立」逐条给通过/失败；在 /tmp 的 M3 副本跑新正例应报 F。
- **输入边界**：再自造至少 5 个混排复数边界（如 `GPUs`、`myGPTs`、`HTTPs`、`newMAILs`、`OpenAPIsLab`），写明期望与理由。
- 七天 2,079 条（截止 2026-10-30T06:31:24Z）重放：给 M3→M4 逐条差异；除含混排缩写复数的条目外不应有变化，有其他变化逐条解释。
- Python 定向全部通过（原 240 + 新增）；旧隔离 E2E 照跑。真实 8766 浏览器验收只在重启后做。

## 重启
需重启 8766 一次加载新代码：先登记主控批准；条件同 M3（全部任务 running=false、最近 next_run ≥300 秒，等自然空档，不取消任务）；报新 PID、health、重启 UTC 时间。

## 约束
只改 scripts/radar_common.py（today_relevance_gate 及辅助）、today_filter_config.py 和相关测试；不改旧断言；不碰清理单文件。隔离 Playwright，禁止 computer use；**禁止 launchctl print / ps eww / env / 打印或检索输出 plist 内容（上一轮 L3）**；证据输出到 /tmp 或 coordination/evidence/impl-hub-h3a/m4/，不覆盖既有文件；开工记 git status 与 7 文件 SHA；Python 用 `../article-importer/.venv/bin/python`；不提交不推送。

## 交付
impl-hub-h3a-report.md 末尾追加「返修 M4」节，决定要点表（≤10 行）+ 逐条自测，完成后 worker_done。复核派 B。
