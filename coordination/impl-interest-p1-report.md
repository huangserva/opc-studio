# 推特情报 1 期兴趣通道 · 实施报告

## 决定要点

| 项目 | 结论 |
|---|---|
| 实施自查 | 通过；真实文件验收待补，不能据此关闭 1 期真实验收 |
| H / M / L | 自查 0 / 0 / 0；待独立复核 |
| 变更文件与 SHA256 | 13 个源码/测试文件，见下方完整清单；另新增本报告 |
| 定向测试 | Python 106 / 106 通过（含兴趣新增 26 项） |
| 全量回归 | 最终 Python 3613 项：3605P / 0F / 1E / 7S；UI 723 项：719P / 3F / 1S，均无新增失败 |
| 隔离浏览器 | 兴趣 22 项、原今日页 22 项、设置页 23 项通过；真实 8766 设置/空态 6 项通过；素材库旧E2E被陈旧导航断言中断（另列） |
| 服务 | 最终 PID 99710；原 PID 13942，中间 PID 86720，后端最终小修后再次重启 |
| 截图 | `/tmp/impl-interest-p1/`，1440 / 390，见证据索引 |
| 真实文件 | 截至 2026-10-30 10:04:11 北京时间，interest/*.json 为 0；首轮计划 10:30 尚未到达 |

## 交付范围与行为

- 新增 `interest_channel.py`：从 `grok_bot_radar.reports_dir()` 下只读 `interest/*.json`，按文件名读取最近 7 个自然日，限制单文件 2 MiB；支持顶层数组以及 posts / entries / items 列表；按帖子 id 合并、选择最新 collected_at 并合并 also_posted_by。
- 今日页新增独立「兴趣」标签，接口走 `channel=interest`，不调用爆款聚合及 linux.do 刷新，也不和原列表混排。兴趣按报告收集时间筛选，昨日发布、今日发现的帖子可以出现在今天。
- 卡片与阅读器显示新内容说明、资源链接中文类型、0–3 相关度、作者/时间、完整正文与 article / thread / quoted / media；右上角复用 T5 Grok SVG 来源图标。兴趣卡片与阅读器只有「存进素材库、准、不准」三个动作，没有「拿去写」。
- 兴趣读列表、读全文、准/不准均不入库；只有「存」才写 ItemsStore，使用现有 x:<帖子id> 规则并保持幂等。兴趣反馈在今日反馈中以 `interest:x:<id>` 分开记录，Grok 双份反馈带 `channel=interest`，保留其他帖子的既有反馈。
- 设置页维护「兴趣清单」。读取真实 md 文件，以 mtime_ns + 内容 SHA256 作版本；每次保存只替换「当前方向」的列表行，其他段落及空行逐字保留，同目录临时文件、fsync 后原子替换，失败清理临时文件。页面打开后文件变化时返回 409「文件已被修改，请刷新」。缺文件时显示约定的 5 个默认方向，首次保存建立同格式文件。
- 未修改 Grok Bot 设置、飞书、AIsa 推特采集及视频实验室保护范围；未提交、未推送。初始工作区本身有大量既有改动和未跟踪文件，本报告只列本单 13 个文件的增量，不将整个 git diff 归为本单。

## G1–G8 逐项自测

| 项 | 结果与证据 |
|---|---|
| G1 规范位置与链接反例 | 通过。`test_G1_urls` 明确验证规范 URL 为可点击链接；正文先出现 9999、URL handle 为数字仍只从 status 后取 id；id/url 不一致、x.com.evil.com、twitter.com、http://x.com、带 query 均保留条目但清空原帖链接并标内容不完整。 |
| G2 mock 与真实目录 | 通过。所有 patch 用 addCleanup 或 with；测试 GROK_BOT_REPORTS_DIR 为 /tmp。`test_G2_real_reports_unchanged` 在隔离读、反馈和收藏前后比较真实 Grok 目录文件清单与 mtime_ns，完全一致；全量没有新增 mock 泄漏。 |
| G3 写边界与原子保存 | 通过。`test_G3_allowed_report_writes_only` 比较 Grok 临时目录前后，只新增 interest-topics.md 与 feedback/studio-feedback.json；`test_G3_atomic_failure_cleans_temp` 模拟 replace 中断，原文件字节未变且不留临时文件。未在真实 interest/ 创建文件。 |
| G4 六类反例及 XSS | 通过。缺字段、坏链接、重复 id、非 x.com 链接、空文件、文件写到一半均有独立测试；另测 [] /「本次无」、超限文件、symlink、作者/方向/新内容说明缺失、javascript/data/带凭据资源链接。重复 id 取最新 collected_at，also_posted_by 合并去重。JSON 半文件和 .tmp 记异常来源计数，不使页面崩溃。full_text 中 script/onerror 用 textContent 按字面显示；article HTML 先经 html_sanitize 再显示；隔离浏览器确认无脚本执行/事件属性/危险 href，article/thread/quoted 均可读。 |
| G5 凭证 | 通过。新增模块仅访问约定的报告/清单/反馈与 items，不读取或打印配置凭据；页面使用既有脱敏设置接口。截图、报告、测试日志没有输出真实 Key/Cookie；浏览器外网请求被隔离拦截。 |
| G6 性能 | 通过。7天×2份×15条共210条，再加超2MiB文件，Python page_view 约6.7ms；隔离真实HTTP测试共212条（含2条安全反例）+超限/半写文件，5次均<1秒，最终实测 43.0ms, 21.1ms, 15.4ms, 21.2ms, 25.0ms；见 fixture-evidence.json。 |
| G7 日报/快报新字段兼容 | 通过。`test_G7_new_fields_daily_alert_types` 给 daily/alerts 加 is_retweet、original_author、original_author_followers、bookmarks、collected_at、crowding；覆盖字符串数字、null、crowding 对象/字符串/数组，grok_bot_radar 和 today_view 正常读取，bookmarks 数字/未知保持一致。未改 grok_bot_radar.py。 |
| G8 格式保真与冲突 | 通过。采用 Grok 现有清单副本增项、删项、再保存，整份字节恢复；额外 CRLF、多空行、前言和其他段落保真。模拟 mtime 改变及写临时文件时外部修改，均拒绝覆盖。真实8766设置页增删后整份md与操作前备份逐字一致，旧revision实际HTTP返回409。 |

## 测试与浏览器证据

- 定向：`../article-importer/.venv/bin/python -m unittest tests.test_interest_channel tests.test_today_view tests.test_settings_view tests.test_grok_bot_radar tests.test_items_store -v`，106项通过；`/tmp/impl-interest-p1/directed.log`。
- 首轮全量：3612项，3604P / 0F / 1E / 7S，408.129秒；`python-full-first.log`。最终代码增补一个元数据缺失反例后重新全量：**3613项，3605P / 0F / 1E / 7S，398.200秒**；`python-full.log`。唯一E为受保护的 `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，错误原文「参考素材宽高须为 32 的倍数」，与合同基线一致。主控已允许独占，跑完已发 status 释放。
- UI：`npm run test:ui`，723项，719P / 3F / 1S；`ui-full.log`。三个失败全部是 `test_lab_test_drama_ui.cjs` 的默认四幕、赛博预设和单幕参数，和合同基线一致；没有修改保护范围。
- `node tests/e2e/interest_channel.cjs`：随机端口 + /tmp 工作区 + 独立 Chromium profile，22项通过；从今日页切兴趣、看全文、准/不准不入库、重复收藏、去素材库搜索，再走设置增删与版本冲突。测试脚本可在真实文件到达后使用 `--real` 完成正式链路。
- `node tests/e2e/today_t5.cjs`：22项通过；`e2e-today-compat.log`，保留热度筛选、Grok/视频/linux.do 来源、全文、403旧快照等原路径。
- `node tests/e2e/settings_page.cjs`：23项通过；`e2e-settings-compat.log`。
- 额外尝试旧 `node tests/e2e/library_page.cjs`：3项通过后被「五个旧页保留在旧版分组」断言中断。该脚本第46行硬编码所有 details 下仅5个链接，而本单未改的现有 workspace-nav.js 包含2个工具链接+9个旧版链接，实际11个；属于旧E2E断言与现行导航不一致，本单没有改其脚本或导航。日志 `e2e-library-compat.log`；不能宣称旧素材库整套E2E通过。本单要求的兴趣→收藏→素材库搜索已由22项专项浏览器验收通过。
- 真实8766：只用隔离 Chromium，`live-settings-evidence.json` 6项通过；新增临时方向后确认文件同步、说明不变、陈旧版本409，再删除临时项并比较整份文件。恢复后清单 SHA256 `54554667a5b25a88e7c4042dad99e8ab160120f7d6452c2820a9054b07b95a99`；备份 `live-topics-before.md`。最终小修后再次重启并核验 PID / 新接口；`final-service-probe.json`：PID99710，兴趣接口200约27.9ms、清单接口200约2.7ms，所有源码SHA匹配。

截图（均在 `/tmp/impl-interest-p1/`）：

- `fixture-interest-1440.png`、`fixture-interest-390.png`：独立兴趣标签、卡片、来源图标、资源和动作。
- `fixture-detail-1440.png`、`fixture-detail-390.png`：全文、恶意字符串字面显示、补充长文/串推/引用、收藏状态。
- `fixture-library-1440.png`：素材库搜索到收藏的 x:101。
- `fixture-settings-1440.png`、`fixture-settings-390.png`：设置新增方向、同步保存。
- `live-empty-1440.png`、`live-empty-390.png`：真实服务兴趣空态。
- `live-settings-1440.png`、`live-settings-390.png`：真实服务设置页；最终清单已恢复。

## server.py 增量

本单只新增3行接线，未改其他路由：

- 第734行：GET /api/settings/interest-topics -> interest_channel.topics_route。
- 第1022–1023行：POST /api/settings/interest-topics -> interest_channel.topics_route，置于已有 X-Article-Studio / Origin 校验之后。

## 真实文件验收边界

**真实文件验收待补。** 本单最终检查（2026-10-30 10:04:11 北京时间）时 Grok `interest/*.json` 尚无文件；没有创建或改造任何真实报告来代替。当前证明的是最终代码的夹具全链路、真实服务路由与真实清单保存，不能声称第一份真实兴趣报告已验收；待10:30文件出现后，复核窗口可运行 `node tests/e2e/interest_channel.cjs --real`，检查真实帖子、收藏入库和截图。本单不扩大到2期，也不将「黄总连续3天存过东西」记为已完成。

## 变更文件 SHA256

| 文件 | SHA256 |
|---|---|
| `interest_channel.py` | `07ab4820e19940630f217be8b9bbd4f215cf2b7d152a54e7a37f3a0eaf849f2e` |
| `today_view.py` | `0bf85a2288198810919ae9c60eb8dcfc1662ab17b8e3740458e06bf9b87b9e9b` |
| `server.py` | `8ddb12c597db7acd25d5739530d6e8f2bf7c915fa4d4f4245e53c6dc38cd0f18` |
| `public/today.html` | `86cec6bd32907b063608d2503095bbe69e85da7a71d1f17b550a73b4af11fe3e` |
| `public/today.js` | `34ede1677e6e01d5c89177e32d8fc4a58b8f2a89719c898d10459f8936efe00f` |
| `public/today.css` | `366e74d8238ebd4da8a4919f85fd357c0cca9a173102ba69318cddc91858602d` |
| `public/settings.html` | `7e2b49cb5e3b612a132881e2cc35d641eeaeb522a76aa3c489b01f9e9cdc1448` |
| `public/settings.js` | `468fa0cb7c31ab5e579916fe383a37d5785ca87a68a11467b3a1f487c5334a58` |
| `public/settings.css` | `b877fc9e6835faff4222175f8855a437b36164cc1b4bc2b68e502aeec35caccf` |
| `public/components/item-card.js` | `1b1b9b21ea488c7c1caac0cc5d58d93b0f970abe29aba1fe4b4890b13f7c604d` |
| `public/components/list-reader.js` | `5915e40a87c3b59e25319b53c1bec9578edf9d6e92a3bc5d6b87d9efb22c443b` |
| `tests/test_interest_channel.py` | `6a15014d900a3441c713e6d306493edb6a6685b9bd8ffd08cb5fcf272be0bb18` |
| `tests/e2e/interest_channel.cjs` | `309e42353aed72641f2664b567e58e28a2d2f6c247fcb1e56d39203506945a6e` |
