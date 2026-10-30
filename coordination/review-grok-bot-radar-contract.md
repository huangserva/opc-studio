# 合同：review-grok-bot-radar（Codex 复核 · Orca）：推特雷达接入 Grok Bot 精选（v2 混排）

## 对象
- 实施合同 `coordination/impl-grok-bot-radar-contract.md`（含第 0 节 v2：不做独立标签，并入「特别关注博主爆款」「低粉逆袭单篇榜」与 AIsa 混排，按 id 去重，小图标标来源；内容完整第一）。
- 实施报告 `coordination/impl-grok-bot-radar-report.md`（task_5fb69819e395，Codex term_02017284 交付，按严格门槛自报 failed：旧 Python 109/110）。
- 改动文件与实施方给的 SHA256：
  - `grok_bot_radar.py` `b86757cb54299fb31ee7ece43042289f1ea34c0099638089bcb2e81bef02f2c0`
  - `server.py` `c4bf018bd94604d7889602fe51f326da0abf6fbe9f176679447a5c5b1ace2e20`
  - `public/twitter-radar.html` `a63e8dcbf59d969ebcd244b22d8487fb6120f8a3e2df6590c5e5dd118c507e43`
  - `public/twitter-radar.js` `e769f6c7008a1474fcdf1d4d3be86d08af3cadadaa27fba5c2b055039d139b5b`
  - `tests/test_grok_bot_radar.py` `8078cb678e1234ef783079528ad411f248a930bf3247a38e66d88dddbe37d111`
  - `tests/e2e/twitter_radar_grok_bot.cjs` `98971e7c274df2e9ceafe8e736dfcb4e0516d38f74b31335f0142032f7de85d6`
- 8766 当前 PID 67239（20:57 启动，已加载新代码）。主控抽检：`GET /api/radar/grok-bot?hours=48` 200、0.04 秒、13 条；criscxuan 卡片显示「原文不完整」。

## 实施方已知弱点（来自 worker-dispatch-profiles，Codex 历史实施）
- 输入/授权边界：曾出现原文截断与贴文歧义 → 本单重点查正文保真、链接白名单、反馈接口来源校验。
- 整条链路与并发状态遗漏 → 重点查反馈双文件写入的并发与撤销、跨来源合并取数规则。
- 只读脚本保护与继承风险 → 查测试有没有写真实 workspace 或 `~/grok-bot-reports` 未恢复。

## 复核项（逐条给 通过/失败/未知 + 证据）
1. **SHA 与范围**：6 个文件 SHA 与上面一致；用实施方的 `/tmp/grok-radar-baseline/preexisting.diff` 对照，允许文件以外的已有 diff 未被改动；`server.py` 本单新增仅 grok-bot 路由（报告称 GET 8 行、POST 11 行），未覆盖用户已有改动。
2. **v2 口径**：页面没有独立 Grok 标签/横幅/筛选；Grok 帖进特关或低粉榜，与 AIsa 同 id 只出一张卡、两个来源徽标；数字取最新采样来源。
3. **内容完整**：抽 3 条真实 Grok 帖（含 criscxuan 2115715632532713672、eternityspring 2115655889428623568、双来源 2115634000937340955），页面正文、why、pattern 与 `~/grok-bot-reports` 原文逐字一致；不完整条目有显式提示；正文无截断（找 slice/substring/line-clamp）。
4. **安全**：正文用 textContent，无 innerHTML 注入；原帖链接只放行 x.com/twitter.com status 结构；外链 noopener。自己构造至少 2 个反例（例如 `https://x.com.evil.com/a/status/1`、正文含 `<img onerror>`）验证。
5. **反馈接口**：缺 Origin / 错 Origin / 缺 `X-Article-Studio` 均 403 且不落盘；两份文件原子写；再次点击可撤销。只在隔离副本或随机端口实例上测写入，不写真实 workspace 与真实 `~/grok-bot-reports`（若必须在真实 8766 点按钮，测完恢复并给前后 SHA）。
6. **测试没有被改弱**：查 `tests/` 本单 diff，新增断言真实检验业务语义；未修改已有测试断言。
7. **既有失败 1L**：独立复现 `tests/test_twitter_radar_foundation.py` 的 `C02TimeoutKeepsOldTimestamps`（StopIteration）在改前副本也失败，判断与本单无关是否成立。
8. **性能**：新接口 5 次 <1 秒；雷达页首屏在隔离 Playwright 下的加载时间给数字。
9. **复用性**：`renderPostCard` / `renderPostDetail` / `renderRichContent` 是否不依赖雷达页全局状态、可被新页面直接引用（后续 1 期「今日」页要复用，见 `coordination/redesign-plan.md` 第 6 节）。给出依赖的全局变量清单。

## 约束
- 只读，不改任何仓库文件（产出只有报告和 /tmp 证据）。不调 `/api/collect/*`，不调 AIsa，不触发采集。
- 只用隔离 Playwright（独立 profile、/tmp 副本或随机端口）。**禁止 computer use 操作黄总真实桌面或 Chrome。**
- Python 用 `../article-importer/.venv/bin/python`。

## 交付
报告 `coordination/review-grok-bot-radar-report.md`，开头放「决定要点」表（≤10 行：结论通过/不通过、H/M/L、核对的 SHA、抽查条数、测试数字、复用性结论）。发一条 `worker_done`。
