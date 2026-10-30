# 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **不通过合同的“旧回归全绿”门槛**；v2 实现、夹具浏览器与真实服务路径通过。1 项既有测试失败已用改前副本复现，交主控决定是否豁免。 |
| H/M/L | 本单新增已知阻断 0H/0M；既有回归失败 1L，不修改范围外采集代码。 |
| 改动文件与 SHA256 | `grok_bot_radar.py`：`b86757cb54299fb31ee7ece43042289f1ea34c0099638089bcb2e81bef02f2c0`<br>`server.py`：`c4bf018bd94604d7889602fe51f326da0abf6fbe9f176679447a5c5b1ace2e20`<br>`public/twitter-radar.html`：`a63e8dcbf59d969ebcd244b22d8487fb6120f8a3e2df6590c5e5dd118c507e43`<br>`public/twitter-radar.js`：`e769f6c7008a1474fcdf1d4d3be86d08af3cadadaa27fba5c2b055039d139b5b`<br>`tests/test_grok_bot_radar.py`：`8078cb678e1234ef783079528ad411f248a930bf3247a38e66d88dddbe37d111`<br>`tests/e2e/twitter_radar_grok_bot.cjs`：`98971e7c274df2e9ceafe8e736dfcb4e0516d38f74b31335f0142032f7de85d6` |
| 测试数字 | 新增 Python 12/12；新增隔离浏览器 30/30；真实 8766 浏览器 18/18；既有 Python 109/110；既有浏览器 43/43、pageerror 0。 |
| 服务 | 已按合同 kickstart，运行 PID **67239**；新接口 200，连续五次 0.0349 / 0.0256 / 0.0276 / 0.0237 / 0.0187 秒。 |
| 真实卡片 | 48h Grok 原始去重 **13** 条；按既有内容规则入榜 **11** 条；双来源 **1** 条；Grok 独立标签 **0** 个。 |
| 边界与交付 | 未提交、未推送；只修改 6 个允许的源码/测试文件，另按合同保存本报告与截图；测试反馈已恢复。 |

## 实现范围与 v2 口径

以合同第 0 节和主控后续“可复用渲染”补充为准，删除了独立 Grok 标签、独立筛选和横幅，保持原来的四标签与默认话题视图。Grok 帖子进入特别关注或低粉榜，沿用已有时间、赛道、搜索、层级与排序控件；观察/推流在爆款、起势之后。新鲜度、报告类型、原始 48h 数量和坏文件数并入原数据回执行，过期时行内黄色提示。

后端 `normalize_post` / `merge_posts` 产出统一帖子结构：id、author、handle、followers、text/full_text、created_at/collected_at、各项数字、rich 字段、sources、view_groups，附 aisa/grok 原始信息。按 id 跨来源去重，数字比较各自真实采样时间取最新；正文保留更完整来源。`merged_posts` 由两榜共同读取，不把合并逻辑写在标签布局里。最新 AIsa 数字缺失时显示未知，不冒用较旧数字。

前端 `renderPostCard(post)` / `renderPostDetail(post)` / `renderRichContent(...)` 可直接复用；旧卡片通过 `decorateRadarCards` 挂接同一详情渲染。正文、why/pattern 和补充内容全部通过 textContent 写入，保留换行，不截断。支持全文、X 长文、串推、引用嵌套卡、图片缩略图、视频封面及“播放原帖”、外链卡。没有的内容不会编造补全；符合启发式条件时明确显示“原文不完整，点开原帖看全文”。

反馈复用原 `.verdict-group` 与原来的意图队列、回执版本排序机制。AIsa 独有卡仍用旧接口及旧两选项；带 Grok 来源的同一组按钮为“准·值得学 / 不准 / 已用来写”，走新接口，工作台与 Bot 文件各自临时文件 + fsync + rename，线程锁和 flock 包住读改写。再次点当前选项可撤销。两文件是分别原子发布，没有宣称跨目录事务。

### 规则差异（未悄悄放宽）

- 时间统一按**推文发帖时间**，保持前端原有 24h/48h，最大 48h；未知/未来时间不冒充新帖。原合同独立 7 天控件已被 v2 作废；后端仍接受 hours=24/48/168 作接口查询兼容。
- 原 AIsa 特关展示仍要求浏览 ≥50,000 且赞 ≥300。Grok 按黄总 v2 指定的报告标签入选（包括低浏览、高收藏爆款与起势/推流/观察），不额外套用 AIsa 数值门槛；卡片信号明确说明按 Grok 报告入选，不谎称达到 AIsa 门槛。
- `label=低粉爆款` 优先入低粉榜；否则粉丝 ≤10,000 且 label=爆款进入低粉榜，其余入特关；不是因为“未知粉丝=0”而误入低粉榜。
- 内容过滤直接复用 `scripts/radar_common.py:content_filter`，与 bundle 派生规则相同。真实 13 条中 `Lonely__MH / 2115647845160145405` 因正文命中 noise 被排除，`openclaw / 2115636811594915985` 英文未命中 AI 关键词、返回 not_ai 被排除；这两条在 API 原始 posts 保留但不进入榜单。
- 原有规则允许特关中文非关键词内容，英文仍须 AI 相关；本单复用规则，没有引入新的政治/情绪分类器。

### 五赛道映射

复用既有 `classify_track` 的优先级，输入为 topic + list + 正文：

| 优先级 | 赛道 | 关键词例子 |
|---|---|---|
| 1 | overseas_infra | Stripe、订阅、支付、虚拟卡、出海基建、收款、税 |
| 2 | github_open | GitHub、开源、repo、star、仓库、arxiv |
| 3 | claude_code | Agent、Claude、Codex、MCP、cursor、skill、编程 |
| 4 | money_indie | 变现、赚钱、营收、ARR/MRR、独立开发、获客、增长 |
| 5 | ai_models | 模型、GPT、Gemini、DeepSeek、推理、训练、显卡、本地部署，以及 AIGC/图像等模型工具主题 |

完全映射不上的帖子 track=null，仅出现在“全部 AI 赛道”，不据此删除。

## 第 3 节反例逐项自测

| 反例 | 结果与证据 |
|---|---|
| 1 原文保真 | **通过**。2400 字以上中文长文 + emoji + 多行 + `<script>alert(1)</script>` + `&amp;` + `<b>`，浏览器 textContent 与夹具全文逐字一致，未生成正文内 script/b 节点；正文无行数限制。真实三卡逐字比对通过。 |
| 2 链接安全 | **通过**。javascript、伪 x.com 路径、x.com.evil.com、空串均没有原帖链接；原帖仅放行 https x.com/twitter.com 的 handle/status/数字完整结构，外链/图片只允许 http(s)，链接统一 noopener noreferrer。真实点击生成新标签，导航目标为正确 x.com URL；隔离路由本地 fulfill 空文档，未向外网发请求。 |
| 3 坏文件与数值 | **通过**。空/半写/错误数组类型/超 5MB/外部符号链接计为 6 个坏文件；非 JSON 忽略并计 ignored_files；无 id 条目跳过并计 skipped_entries；8202 字符串转数字，1.2万/NaN/布尔数字显示未知，接口不 500。 |
| 4 不存在/空目录 | **通过**。Python 两场景与浏览器删除夹具目录后接口 200、count=0，原数据回执行显示“Grok Bot 还没有产出报告”。 |
| 5 去重与兜底 | **通过**。同 id 日报 + 两份快报输出一条、三来源，最新数字 30；无 id 用安全 URL status 数字兜底；再缺失跳过并计数。 |
| 6 时区与边界 | **通过**。UTC ISO 和 Asia/Shanghai 采集时间可解析；created_at 24h 等号纳入，多 1 秒排除；页面发帖时间使用北京时间。 |
| 7 并发与校验 | **通过**。半写文件不炸接口；两个并发 HTTP POST 返回 200、两 id 均在、两份 JSON 相同。12 项 Python 中覆盖未知 id、非对象/数组、超长 id/枚举、布尔/数组 verdict、多余字段及坏反馈拒写。 |
| 8 来源校验 | **通过**。缺 Origin、错误 Origin、缺 X-Article-Studio 三种均 403，反馈不落盘；新路由在原 _post_routes 来源校验之后注册。 |
| 9 热加载 | **通过**。服务运行中新增 alerts/new.json 立即可读，页面“读取最新数据”出现新卡；无需重启。半写文件同时存在仍正常显示。 |
| 10 旧行为回归 | **未通过全绿门槛：109/110**。6 个既有 Python 模块共 110 项，仅 C02TimeoutKeepsOldTimestamps 为既有 StopIteration；改前副本同测同错。旧隔离浏览器 **43/43**、pageerror=0，原四标签、监控、旧反馈、深链与慢请求排序路径通过。未为凑绿改范围外测试/采集代码。 |
| 11 AIsa + Grok 同 id | **通过**。夹具 101 只有一张卡、AIsa/Grok 两徽标、最新浏览 9,000；统一层另测 AIsa 采样更新时取 AIsa 数字。真实双来源 id=2115634000937340955。 |
| 12 短文高收藏 | **通过**。无 full_text、短正文、收藏 111 的夹具与真实 criscxuan / 2115715632532713672 均显示指定不完整提示。 |

## 既有回归失败证据

运行命令（指定 Python）：

```sh
../article-importer/.venv/bin/python -m unittest tests.test_grok_bot_radar -q
PYTHONPATH=tests ../article-importer/.venv/bin/python -m unittest test_twitter_radar_foundation test_twitter_radar_a1b test_twitter_radar_a1c test_radar_page_api test_radar_page_api_b2b3 test_radar_page_api_b2b4
node tests/e2e/twitter_radar_grok_bot.cjs
node tests/e2e/twitter_radar_grok_bot.cjs --real
RADAR_B2B_REPO=/tmp/grok-radar-old-e2e/repo RADAR_B2B_EVIDENCE=/tmp/grok-radar-old-e2e/evidence GROK_BOT_REPORTS_DIR=/tmp/grok-radar-old-e2e/empty-reports node tests/e2e/twitter_radar_monitor_page.cjs
```

既有失败：`tests/test_twitter_radar_foundation.py:229` 在 `next(t for t in first["vip_tweets"] if t["id"] == old_id)` 抛 StopIteration。用 `/tmp/grok-radar-prechange/` 的独立代码副本（没有本单新模块和前端、保留改前 server）运行该单项，同样 1 项错误。相关采集脚本均未被本单修改；本单不修范围外旧采集/测试。初次按包名直接运行还发现 A1c 的平级 import 路径问题，改用 PYTHONPATH=tests 后已消除；最终 110 项中只有上述 1 项失败。

临时日志：`/tmp/grok-radar-regression.log`、`/tmp/grok-radar-baseline-failure.log`、`/tmp/grok-radar-e2e.log`、`/tmp/grok-radar-real.log`、`/tmp/grok-radar-old-e2e.log`。关键结论与失败原文已在本报告保留，不依赖临时日志长期存在。

## 真实服务与页面证据

- 8766 PID 67239；已确认新路由 200、读取真实报告，5 次响应均 <1 秒。
- Grok 13 个原始 id / 11 个符合原内容规则的 id 全部分榜可见。真实抽查 id：2115634000937340955、2115715632532713672、2115655889428623568；正文和 why/pattern 逐字一致，统一指标采用最新采样来源。
- 实际点开 criscxuan 原帖在新标签请求 `https://x.com/criscxuan/status/2115715632532713672`；测试路由返回空页保持原目标地址，避免外网与账号状态变化。
- 实际点“准·值得学”，刷新仍选中，两份落盘成功；finally 恢复选中 id 的原反馈。验收后工作台 feedback.json 恢复不存在；Bot 镜像恢复验收前空对象，测试 id 不在镜像中。
- 所有浏览器使用 headless Playwright 独立临时 profile；新测试 profile 明确在 /tmp；旧测试副本/临时 workspace/随机端口隔离。未用 computer use，未操作真实桌面或真实 Chrome profile。

截图：

- [真实短文卡片：原文不完整、why/pattern、单组反馈](impl-grok-bot-radar-real-card.png)
- [真实低粉榜](impl-grok-bot-radar-real-feed.png)
- [真实特关榜](impl-grok-bot-radar-real-vip.png)
- [真实页面](impl-grok-bot-radar-real.png)

## AIsa 完整性检查（只报告）

前端正文渲染未发现 slice/substring 截断；现有 slice 用在分页或去重辅助键，不改变正文。`scripts/radar_bundle.py:143` 原样传 rec.text，`scripts/radar_common.py:286` 仅对上游 text 做 strip，没有截断。真实 bundle 122 个去重 id，最长正文 2880 字，无以省略号结尾的样本；所有样本均未带 full_text/article/thread/quoted/media/card 字段。

这只证明本地前端和 bundle 层没有主动截断正文，**不能证明上游 text 已包含 X 长文、串推、被引用帖或媒体**。采集归一化当前只保存 text 和引用 id 等元信息；不在本单范围修改。页面会对可疑样本显式提示不完整，但报告中不存在的真实正文/附件仍须点原帖读取。

## 变更范围与保护核验

以开工前 `/tmp/grok-radar-baseline/preexisting.diff` 与最终 git diff 按文件对比，允许文件以外的已有 diff **完全一致**。`server.py` 相对开工快照仅新增 GET 8 行、POST 11 行，未重排或覆盖用户已有改动（git 总 diff 另外含用户原有 23 行）。`radar_page_api.py`、采集配置、旧测试等预存改动未改。

当前 `git diff --stat -- server.py public/twitter-radar.html public/twitter-radar.js`：

```text
 public/twitter-radar.html |  17 +++
 public/twitter-radar.js   | 270 +++++++++++++++++++++++++++++++++++++++++-----
 server.py                 |  42 ++++++++
 3 files changed, 302 insertions(+), 27 deletions(-)
```

新增文件未被 git diff --stat 计入：grok_bot_radar.py、tests/test_grok_bot_radar.py、tests/e2e/twitter_radar_grok_bot.cjs。全部允许文件及完整 SHA256 见开头表。没有提交或推送。
