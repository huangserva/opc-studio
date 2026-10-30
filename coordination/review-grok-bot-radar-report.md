# 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **不通过**：当前雷达混排主路径通过，但合同第 9 项“可被新页面直接引用”未达到；需修复下列两项 M。 |
| H/M/L | **本单新增 0H / 2M / 0L**；另有既有 Python 测试错误 **1L**，已独立在改前副本复现，不归因本单。 |
| SHA 核对 | 6/6 与复核合同完全一致，复核结束未变化；完整 SHA 见下文。 |
| 范围 | 允许文件外的 tracked diff 与开工基线逐文件完全一致；server 本单增量仅 GET 8 行、POST 11 行。 |
| 真实数据与抽查 | 48h 原始 Grok 13 条、按现有内容过滤入榜 11 条；指定 3 条均抽查，双来源 1 条只出一张卡、两个来源徽标、最新数字正确。 |
| 测试数字 | 新 Python 12/12；原新增隔离 E2E 30/30；补充撤销 E2E 32/32（包含前述 30 项，不相加）；旧 Python 109/110；旧 E2E 43/43，pageerror=0。 |
| 性能 | 新接口 5 次 0.42450 / 0.02505 / 0.02612 / 0.11001 / 0.02272 秒；真实页 DOMContentLoaded 76.6ms，数据就绪 347.5ms。 |
| 复用性 | `renderRichContent` 无可变雷达状态依赖；`renderPostDetail` 对统一双来源输入漏 Grok rich 内容；`renderPostCard` 反馈依赖雷达全局状态。 |
| 保护边界 | 本复核只读源码与真实服务；所有反馈 POST 在 /tmp 夹具随机端口；真实页仅 GET，未触发采集、AIsa、真实账号或桌面操作。仓库只产出本报告。 |

复核对象 task_5fb69819e395；复核任务 task_87a9c4663438 / ctx_405c30455d6b。以下结论基于当前六文件 SHA，不扩大为其他版本保证。

## 阻断项

### M1：独立页面的卡片反馈依赖 `grokState`，无法直接依靠帖子数据渲染

位置：`public/twitter-radar.js:2180` 的 `renderPostCard` → `buildVerdictButtons`（1422）→ `feedbackTable` / `grokPostById`（1991–1992）。

把当前完整脚本注入空白隔离页面，传入符合后端统一结构的双来源帖子（`sources=['AIsa','Grok']`、`grok` 原始记录），`renderPostCard(post)` 只生成 **“值得跟 / 不值得”两个旧选项**，缺少 **“准·值得学 / 不准 / 已用来写”** 的 Grok 三选项。即使补齐 `_grok`、`_sources` 适配字段，仍然错误；必须额外往雷达页的 `grokState.posts` 注入该帖才变成正确三选项。

这证明函数读了页面外的可变状态，单纯传入帖子对象不足以正确渲染。后续“今日”页不能直接引用。建议让组件从明确的帖子来源和传入的反馈状态决定按钮，动作通过显式回调/依赖注入接入；不要求本轮建设整个“今日”页。

独立反例与正控制：`/tmp/review-grok-bot-radar/reuse-review.cjs`、`reuse-review.json`：

```json
{
  "direct": {"buttons":["值得跟","不值得"],"article":false},
  "adapted": {"buttons":["值得跟","不值得"],"article":true},
  "seeded": {"buttons":["准·值得学","不准","已用来写"],"article":false},
  "richControl": true
}
```

### M2：详情组件接收统一双来源对象时，Grok 独有长文丢失

位置：`public/twitter-radar.js:2161`、2163–2164。

统一后端对象是 `post.aisa`、`post.grok`、`post.sources`；当前详情先渲染 `post.aisa || post._grok || post`，而补渲染 Grok 仅识别 `post._grok && post._sources.includes('AIsa')`。因此对直接输入的统一双来源对象，Grok 独有 `article` 不显示，虽然同一对象的 why/pattern 已通过 `post.grok` 正常显示。

同一夹具只添加 `_grok` 和 `_sources` 两个旧视图适配字段，长文立即出现；直接 `renderRichContent` 也能显示该内容，证明问题是详情入口的字段分支，非测试数据或 rich 渲染器不支持。当前雷达视图先经 `radarTweets` 生成下划线字段，所以已有夹具测试没有暴露这个问题。建议统一支持后端结构，补一条空白页面直接用 `merged_posts` 对象渲染的测试，断言 Grok 独有长文、串推、引用与媒体均不丢。

两项均属于复核合同明确的可复用要求，不是新增“今日”页面范围。

## 合同逐项复核

| 项 | 结论 | 证据与边界 |
|---|---|---|
| 1 SHA 与范围 | **通过** | 六 SHA 完全相同；按 `diff --git` 分块比较 `/tmp/grok-radar-baseline/preexisting.diff` 与当前 diff，非允许文件变化集合为空；server 对快照只新增两个 GET 和一个 POST 路由，见 `server-increment.diff`。复核前后六 SHA、全部 tracked diff 均未变。 |
| 2 v2 混排 | **通过** | 无独立 Grok 标签/筛选/横幅，保留四标签；11 条符合内容过滤的真实 Grok 帖全部在特关或低粉榜可见；双来源 2115634000937340955 在两榜合计只出现一次，徽标 AIsa/Grok。来源规则差异实施报告已明确，没有为本单修改既有采集规则。 |
| 3 内容完整 | **通过，含下述双来源说明** | criscxuan 40 字、eternityspring 157 字正文逐字等于原报告；三条 why/pattern 全部逐字一致；双来源采用 AIsa 的较完整 984 字，而非 Grok 的 165 字摘要，正文逐字等于 AIsa 原始快照。criscxuan 有明确不完整提示。夹具 2400 字以上正文、多行、emoji、HTML 字面量、长文、串推、引用、图片、视频、外链均通过。 |
| 4 安全 | **通过** | 自建 `<img src=x onerror=...>`、`<script>`、`&amp;`、`<b>` 正文逐字显示，没有生成注入节点；伪域 `https://x.com.evil.com/a/status/1`、javascript 协议、尾换行链接均拒绝；合法 x.com 链接作为正控制通过；页面新标签链接含 noopener。三条原帖均实际点击并新开正确 URL，由隔离路由本地 fulfill，不向 X 发请求。 |
| 5 反馈接口 | **通过（逐文件原子发布）** | 随机端口夹具中：缺 Origin、错 Origin、缺 X-Article-Studio 均 403 且未落盘；并发两 ID 都保留、双文件相同；改选、再次点选撤销、刷新后保持均实测。代码为线程锁+flock 包住读改写，每份 mkstemp/fsync/os.replace。两目录并非事务，未把进程崩溃时跨文件同时一致当作已验证能力。 |
| 6 测试未改弱 | **通过** | 旧 tests 的 diff 与开工基线相同，本单只新增两份测试；断言检验正文、链接、过滤、去重、最新数字、并发、拒写、热加载等语义。原新增 E2E 的“复用”只用已初始化雷达状态及 `radarTweets` 适配数据，覆盖不足见 M1/M2，不能据此声称独立复用通过。 |
| 7 既有失败 | **通过归因；全绿门槛仍失败** | 当前 110 项只有 C02TimeoutKeepsOldTimestamps 错误；改前副本同一测试独立运行也在第 229 行 StopIteration。相关测试及 5 个采集/存储脚本 SHA 在改前副本与当前逐一相同；副本没有 grok_bot_radar.py，server 也无新路由。本单无关判断成立。是否豁免旧错误由主控决定。 |
| 8 性能 | **通过** | 真实 GET 连测五次全部 <1 秒，最慢 0.42450 秒；真实页新隔离 profile 首次导航 DOMContentLoaded=76.6ms，等待 `!grokState.loading && DATA_SOURCE.kind==='json'` 为 347.5ms。测量不含人为操作与页面截图；不是内容首卡像素曝光指标。 |
| 9 复用性 | **失败** | 有纯 helper 可复用，但完整卡片依赖可变雷达状态，双来源详情还依赖下划线适配字段；空白页正反控制已复现 M1/M2。 |

### 真实双来源帖的正文与数字

`2115634000937340955`：Grok 报告正文 165 字；AIsa 原始正文 984 字；页面显示的 984 字逐字等于 AIsa。初次按“必须字面等于 Grok text”做的机械断言得到 false，保留在 `browser-review.json`，不是截断或篡改证据；独立核实较完整来源后，按 v2“内容完整优先”的要求认定通过，见 `dual-source-assessment.json`。

AIsa 采样 `2026-10-29T12:00:25.321Z`（北京时间 20:00），新于 Grok `2026-10-29T12:42:00+08:00`。页面/合并数据取 AIsa 的浏览 63,052、点赞 31；AIsa 未提供收藏，所以显示未知，不冒用较旧 Grok 收藏 20。这个结果与“数字取最新来源”一致。

### 渲染函数的全局依赖清单

- `renderRichContent`：依赖 DOM `document`，以及 `grokNode`、`isObject`、`safeText`、`safeHttpUrl`、`appendSafeLink`、`grokOriginalUrl` 等 helper；**不依赖可变雷达状态**。
- `renderPostDetail`：上述 helper，再加 `incompleteReasons`、`numOr`、`safeNum`、`formatBeijingTime`；时间 helper 间接依赖 `parseStampMs`、`parseNaiveMs`、`BEIJING_OFFSET_MS` 常量。无可变雷达状态读取，但存在 M2 的输入结构依赖。
- `renderPostCard`：上述函数、`tweetIdOf`、`buildVerdictButtons`；后者经 `feedbackTable` / `grokPostById` 读取 **`grokState.posts`、`grokState.feedback`、`monitorState.feedback`、`VERDICT_LABEL`**。若要求反馈可操作，还需现有委托监听、`onVerdictClick`、`queueVerdict`、`verdictQueues`、`feedbackOrder`、`feedbackReadFence`、`postVerdict`、状态刷新和 toast 等页面设施；卡片自身不会安装这些行为。
- 三函数仍在整份 `twitter-radar.js`，没有单独可引用的组件入口；整份文件还注册 DOMContentLoaded 并初始化雷达页面。此次空白页测试刻意在文档加载完成后注入脚本，避免把启动副作用误当作上述两个反例的原因。

## 测试与证据

全部 Python 使用指定解释器 `/Users/huangzongning/development/article-importer/.venv/bin/python`；设置 `PYTHONDONTWRITEBYTECODE=1`。不跑全量 discover、不调用采集接口、不调用 AIsa。

| 验证 | 实际结果 | 证据 |
|---|---|---|
| 新 Python | 12 项 OK | `/tmp/review-grok-bot-radar/python-new.log` |
| 原新增隔离 E2E | 30/30 | `e2e-new.log`；夹具 `/tmp/grok-radar-e2e-5igME3` |
| 加入撤销/刷新验证的 /tmp E2E 副本 | 32/32，包含原 30 项 | `e2e-withdraw.cjs`、`e2e-withdraw.log`；夹具 `/tmp/grok-radar-e2e-hJxbUx` |
| 既有 Python 6 模块 | 110 项，1 error | `python-old.log` |
| 改前单项 | 1 项，同样 StopIteration | `prechange.log`、`prechange-integrity.json` |
| 旧雷达隔离 E2E | 43/43，pageerror 0，外网请求 0 | `e2e-old.log`、`old-e2e/`；随机端口 58398 |
| 独立真实浏览器 | 三条正文/why/pattern、来源、11 条分榜、双来源去重、最新数字、链接与攻击反例逐项检查 | `browser-review.cjs`、`browser-review.json`；初次机械等于 Grok 的检查差异已在上文解释 |
| 独立复用正反控制 | M1/M2 均复现 | `reuse-review.cjs`、`reuse-review.json` |

除注明完整路径外，以上证据均在 `/tmp/review-grok-bot-radar/`。截图 `card-2115715632532713672.png`、`card-2115655889428623568.png`、`card-2115634000937340955.png`、`real-page.png`；已实际查看短文卡截图确认正文、提示、why/pattern、单组反馈可见。

旧 E2E 首次自建副本因缺相邻 `article-importer/scripts/article_importer.py` 启动失败，补齐 /tmp 相邻代码依赖后才完成 43/43；这是复核夹具配置问题，未改仓库或服务代码。

既有错误原文：

```text
ERROR: test_timeout_marks_failed_and_keeps_old_fetched_at
File "tests/test_twitter_radar_foundation.py", line 229
old_fetched = next(t for t in first["vip_tweets"] if t["id"] == old_id)["fetched_at"]
StopIteration
```

### AIsa 完整性边界

源码正文展示无 slice/substring 截断，现有 slice 用于去重键或分页；HTML 新样式明确撤销 `.tweet-text` 的行数/高度限制。`scripts/radar_bundle.py` 原样传递 `rec.text`，`radar_common.normalize_tweet` 仅 strip。真实 bundle 共 122 个去重 ID、最长正文 2880 字；这些条目均没有 full_text/article/thread/quoted/media/card 字段。因此可以确认本地展示和 bundle 层没有主动截断，**不能确认上游已取齐长文、串推、引用与媒体**；本次不改 AIsa 采集。

### 保护与服务状态

当前 8766 PID 67239，进程启动时间 2026-10-29 20:57:01，新 API 返回真实数据，符合已加载新代码的核验条件；本复核未重启生产服务。

真实页请求拦截只允许 GET，所有非本机 origin 均 abort 或本地 fulfill。没有运行实施方带真实反馈写入的 `--real` 模式。反馈写入全部在 /tmp reports/workspace 随机端口；真实工作台 feedback.json 当前不存在，Bot 镜像当前是空对象，SHA256 为 `ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356`。实施方曾运行 `--real` 并在 finally 按所选 ID 恢复，代码和当前空状态与其描述一致，但缺其运行前文件原始 SHA，**无法独立证明那次历史恢复逐字节一致**；不把它伪报为已验证。本复核本身没有真实反馈写入。

## SHA256

| 文件 | SHA256 |
|---|---|
| `grok_bot_radar.py` | `b86757cb54299fb31ee7ece43042289f1ea34c0099638089bcb2e81bef02f2c0` |
| `server.py` | `c4bf018bd94604d7889602fe51f326da0abf6fbe9f176679447a5c5b1ace2e20` |
| `public/twitter-radar.html` | `a63e8dcbf59d969ebcd244b22d8487fb6120f8a3e2df6590c5e5dd118c507e43` |
| `public/twitter-radar.js` | `e769f6c7008a1474fcdf1d4d3be86d08af3cadadaa27fba5c2b055039d139b5b` |
| `tests/test_grok_bot_radar.py` | `8078cb678e1234ef783079528ad411f248a930bf3247a38e66d88dddbe37d111` |
| `tests/e2e/twitter_radar_grok_bot.cjs` | `98971e7c274df2e9ceafe8e736dfcb4e0516d38f74b31335f0142032f7de85d6` |
