# T5 独立复核报告

## 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **不通过**：回复数解析存在独立复现的错误，需修复 M1 |
| H / M / L | **0 / 1 / 0** |
| 关键证据 | `linuxdo_source.py:89` 在正文首次匹配“100 个帖子”，尾部实际 8 个帖子时错误显示回复 99，预期 7 |
| SHA 与服务 | 12/12 文件 SHA256 与实施报告吻合；报告 SHA256 `5c20891d1fb14b9a6d5210987520d1599cf7b70ebbd4c19c3efcfd1cf8b0b569`；8766 PID 61473 与报告一致 |
| 定向及独立探针 | 单元 **87/87**；独立探针 **27 通过 / 1 失败**（含负对照，失败即 M1） |
| 浏览器 | 隔离夹具 **25/25**；真实只读路径 **6/6**；1440/390 四来源长作者布局各 5 项检查全通过 |
| 全量 | Python **3581：3573P / 0F / 1E / 7S**；UI **723：719P / 3F / 1S**，与既有失败基线一致 |
| 正式改动 | 仅本报告；未改正式代码、未提交、未推送；证据在 `/tmp/review-t5/` |

复核任务 `task_7c49d6bba3ae`，Dispatch `ctx_98316d97d3ab`。与实施窗口不同；全程只用独立 Chromium profile / context 的 headless Playwright，未使用 computer use，未操作黄总桌面或真实 Chrome。正式 8766 仅访问只读页面和 GET API；存、反馈、拿去写全部发生在 `/tmp` 工作区的随机端口服务，未生成真实 AI 写作任务。

## 阻断项 M1：正文数字被误当成 RSS 回复统计

**失败。** `linuxdo_source.py:89` 使用 `re.search(r'(\d+)\s*个帖子', plain)`，会读取整个清洗后正文中的第一个匹配，而非 Discourse RSS 尾部统计。

独立输入：

```html
<p>以前积累了 100 个帖子，这次分享一下。</p>
<p>8 个帖子 - 3 位参与者</p>
```

`parse_rss(rss(description))[0]['metrics']['replies']` 实际为 **99**，预期为 **7**。删除第二行尾部统计后，实际仍是 **99**，预期应为 **None**。这会把文章正文中的自然语言数字写进统一条目及收藏素材的回复数；不影响火爆判断或现有热度排序（它们不使用回复数）。

建议只识别 RSS 约定的尾部统计结构，例如结尾的“帖子数 - 参与者数”区域；不以正文中任意同形短语兜底，缺统计保持 None。补这两个反例，并保留首帖扣 1、单帖 0、缺统计 None 的原断言。证据：`/tmp/review-t5/probes.py`、`probes.json` 最后一项；此问题已通过 Orca 单独通知主控。本复核未修代码。

## 合同逐项结果

### 1. 火爆阈值与组合筛选：通过

- 代码差异确认后端只有 `HOT_VIEWS=100000` 判定入口，摘要/详情都有 `hot`；`_tier` 保持原样。
- 独立夹具验证 `99999=False`、`100000=True`、`None=False`、字符串 `"100000"=True`、视频 `plays=150000=True`；卡片、阅读器有火爆文字并保留原档位。
- 组合“48h + 火爆 + G + Agent + 正文关键词”准确仅返回两条；无效 source/tier 被拒绝；linux.do 叠加火爆为空。
- 浏览器故意提供 `hot=False, views=200000` 不显示徽标；`hot=True, views=None` 显示徽标，证明前端消费布尔值。前端代码也没有重复计算十万阈值。
- 真实 48h 火爆返回 20 条，全部 `hot=True` 且浏览数 ≥100000。

证据：`probes.json`、`layout.json`、`browser.log`、`real-readonly.json`、`code.diff`。

### 2. 来源图标与共享组件：通过

- G/A/V/L 都是 DOM 创建的本地内联 SVG，无外部图片或字体；title 与 aria-label 分别为“Grok Bot 来源 / AIsa 来源 / 视频榜来源 / linux.do 社区来源”。卡片右上角及阅读器标题区均存在。
- 独立构造四来源并排、极长作者及长标题，在 1440/390 两宽度检查图标、标题和动作栏边界，全部通过，无横向溢出。截图已实际打开检查。
- Playwright 记录并拦截跨源请求：夹具路径及真实只读路径均为 **0 次外部资源请求**。四来源 SVG 没有 href/src 外链。
- 未知来源 manual 仍保留文字和名称；素材库实际打开收藏条目、全文、“拿去写”成功，未发现共享阅读器回归。素材库原有导航断言的已知失败未被本次放宽（详见第 6 项）。

证据：`fixture-390-list.png`、`fixture-390-reader.png`、`four-sources-390.png`、`four-sources-1440.png`、`real-390.png`、`layout.json`、`fixture-results.json`（network=[]）、`real-readonly.json`（external=[]）。

### 3. linux.do 抓取安全：安全项通过；字段解析因 M1 失败

- 抓取 URL 是模块常量；HTTP 查询参数没有设置 RSS_URL 或代理的入口，唯一外部配置是系统代理。curl 使用参数数组启动，未设置 `shell=True`；`--proxy` 的值独占一个 argv。带 shell 字符的回环代理独立实测不执行命令，不产生标记文件；用户名/口令代理、换行代理、file 代理均在运行 curl 前被拒。
- 真实 curl + 临时 CA + 回环 TLS 服务独立验证：302 不跟随、HTTP 不接受、未知长度 chunked >2 MiB 拒绝、已知长度 >2 MiB 拒绝、403 拒绝、200 挑战 HTML 拒绝、非法 XML 拒绝。原定向测试另实测 subprocess 超时 0.3 秒及时结束、正常 RSS 成功。实现总 timeout=10 秒、curl max-time=9.5 秒。
- 在临时 CURL_HOME 放入注入 Cookie/Authorization 与 location 的 `.curlrc`，实际服务端均未收到凭证头，重定向目标未被访问；**负对照移除 `-q` 后服务端确实收到伪 Cookie**，证明检测能抓到此错误。未使用真实凭证。
- 独立 SVG/onload/script/实体编码 javascript 链接清洗后不含执行内容；浏览器 script/onerror/javascript 链接为 0，window.PWNED 未出现。全文由 textContent 渲染，输入中的标签不会重新解释为 HTML。
- 失败异常使用固定错误文案；独立将伪 Cookie 标记放入异常原文后，status 和快照均不包含标记，旧快照字节未变。未打印系统环境、plist、Key 或 Cookie。
- 正常 creator/category/pubDate/正文/URL 映射、同链接去重、None 浏览数通过；回复数尾部提取不可靠，见 M1。

证据：`probes.py`、`probes.json`、`probes.log`、`directed.log`。TLS 夹具中 curl 提前拒绝超大响应导致服务端 BrokenPipe，属于预期断连接收现象，不是产品异常。NetworkGuard 只拦 Python socket，不会拦 curl 子进程；因此本项实际运输测试显式把 RSS_URL 指向回环 TLS，且代理为空，而不是把 NetworkGuard 当作 curl 的网络隔离证明。

### 4. 节流、并发与失败保留：通过

- 定向实测失败 599 秒不重试、600 秒重试；成功后 3599 秒不刷新、3600 秒刷新，符合主控追加的 10 分钟退避 / 1 小时节流。
- 独立 **8 进程同时 refresh**，记录到一次 fetch；**100 个并发 refresh_background 调用**同样只 fetch 一次，覆盖 flock 与 pending 两层去重。
- 写入先落同目录 `.json.tmp` 再 replace；成功后没有遗留 tmp。跨进程锁覆盖 fetch 与快照/status 写入，pending 在 finally 清除。
- 失败后的旧快照字节不变，浏览器异常状态可见，旧全文仍可打开。

证据：`directed.log`、`probes.json`、`browser.log`。

### 5. 首屏与轮询结束：通过

- RSS fetch 人为阻塞 8 秒，隔离浏览器首屏 **141.7 ms**，此时 RSS 已开始、尚未完成；完成后自动出现 L 条目。
- 等刷新终态稳定后再观察 4 秒（超过三个 1.2 秒轮询周期），`/api/today` 请求计数保持 **12 → 12**，没有无限轮询。
- 真实 8766 首屏 **248.4 ms**。服务 PID 与实施重启后的 PID 61473 一致，真实 API 下发 hot / L，确认验收针对已加载 T5 的服务。

证据：`fixture-results.json`、`browser.log`、`real-readonly.json`。

### 6. 测试差异与 mock 生命周期：通过

主控提供 T5 开工前副本 `/tmp/impl-p4-4-4-after/repo/tests/`，已独立逐行 diff 四个既有测试文件，差异保存在 `tests.diff`：

- test_today_view 新增阈值测试、后台刷新 patch，以及显式 tearDown.stop；未删除/放宽既有断言。
- today_page 仅在隔离后端禁自动 RSS，并把原字母 textContent 来源定位切到 `dataset.source`；仍精确检查 A/G 来源与原有内容。反例 15 时间夹具、排序断言未改。
- library_page、write_and_publish 只在隔离服务禁自动 RSS，没有改断言。library_page 仍要求旧版分组 5 项，未为规避当前导航 9 项而放宽。
- ItemsStore 旧测试确实手调 TodayTests.setUp/tearDown，绕过 unittest 的 addCleanup；显式 stop 是针对这个调用方式恢复 mock 的有效修复。独立先手调再检查 `refresh_background is 原函数` 通过，再执行 cleanup 仍通过；未绕过生产 fetch 逻辑，生产逻辑由本复核运输/并发/浏览器测试覆盖。
- 仍不声称解决北京时间 00–08 点的既有相对时间夹具边界；本次运行在北京时间 08 点后。

### 7. 存 / 拿去写与 canonical ID：通过

隔离真实 Handler + SQLite + 浏览器走 linux.do → 全文 → 准/不准 → 存 → 素材库搜索标题，唯一命中同一 id，再点拿去写打开具体作品，底稿逐字保留完整正文及原帖 URL。模型执行在夹具中替换，未访问真实模型或发布服务。另对同 URL 连续 upsert 3 次，数据库仅 1 条；解析产生的 URL ID 与 `items_store.canonical_id` 一致。

证据：`browser.log`、`fixture-library-saved.png`、`probes.json`；隔离工作区 `/tmp/today-t5-u6mozc/workspace`。

### 8. 早上“今天 2 条 / 48 小时 30 条”：符合当前定义

真实 GET 再现今天 **2**、48h **30**；列表使用 RSS `pubDate` 作为 `published_at`，按北京时间当天零点至当前时间筛选。RSS 的 daily 表示“日榜”，不会保证上榜帖都是今天发表，因此早晨看到很多昨天的帖子属于正常结果。

**建议继续按发帖时间筛选，不改成 fetched_at。** 抓取时间只表示本地更新时刻；若用于“今天”，昨日旧帖每次刷新都会重新算今日内容，并造成同页面不同来源的时间语义不一致。若黄总希望默认看到完整日榜，可以单列“linux.do 日榜”视图或解释“今天按发帖时间，完整日榜看 48 小时”，这是产品选择，本复核不改代码。

证据：`real-readonly.json` 保存的 30 个 published_at 及来源 updated_at；`today_view.py` 的 cutoff 条件。

## 全量与验证命令

- 已通过 Orca 向主控登记并获得独占许可，然后启动本轮全量，未终止他人进程。
- `../article-importer/.venv/bin/python -m unittest tests.test_items_store tests.test_link_index_orphan tests.test_linuxdo_source tests.test_today_view -v`：87/87。
- 独立 Python 反例脚本 `/tmp/review-t5/probes.py`：28 项，27P/1F，唯一 F 为 M1（两个输入变体）。
- 隔离 Playwright `/tmp/review-t5/browser.cjs`：25/25；`real-readonly.cjs`：6/6；`layout.cjs`：1440 与 390 各 5 项全通过。
- `../article-importer/.venv/bin/python -m unittest discover -s tests -v`：**3581 项，3573P / 0F / 1E / 7S**，396.988 秒。唯一错误为受保护的 `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，异常“参考素材宽高须为 32 的倍数”，与实施最终基线同名、同异常；日志 `python-full.log`。
- `npm run test:ui`：**723 项，719P / 3F / 1S**，15.493 秒。3 个失败均为受保护 `tests/test_lab_test_drama_ui.cjs` 的默认四幕剧本、切换赛博预设、单幕 Singularity 参数，逐一与实施最终日志核对名称一致；日志 `ui-full.log`。
- 全量结束已通过 Orca 通知主控释放独占；结尾重新计算 12 份代码/测试 SHA，仍 12/12 匹配，见 `sha-final.json`。没有因为既有保护范围失败而改代码或放宽测试。

## 证据索引与边界

`/tmp/review-t5/sha.json`、`code.diff`、`tests.diff`、`directed.log`、`probes.py/json/log`、`browser.cjs/log`、`fixture-results.json`、`real-readonly.cjs/json/log`、`layout.cjs/json/log` 及截图。正式代码未改；报告之外的验证脚本和输入均在 /tmp。复核结论为不通过，不代表任务未执行：已完成的必测项如上，需由实施窗口修复 M1 后对受影响项复验。

## M1 复验

### 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **通过（原 M1 关闭，留 1 个非阻断 L）**；本节替代上轮 M1 不通过结论，不抹去历史记录 |
| H / M / L | **0 / 0 / 1**：全角空格统计未识别，详见 L1 |
| SHA | 返修 2/2 与实施报告一致；T5 基线 12 文件中仅这 2 份变化，其余 10/10 不变，`/tmp/review-t5-m1/sha.json` |
| 独立反例 | **17/18 通过**，唯一失败为全角空格统计；旧实现负对照 **2/2 测出错误**，`probes.py/json/log` |
| 定向测试 | **41/41**，`directed.log`；原例正文 100 + 尾部 8 → 回复 **7**，无尾统计 → **None** |
| 浏览器 | 隔离 T5 **22/22**、真实只读 **9/9**；真实 RSS **55 帖 → 54 回复**，501 字正文逐字相同 |
| 服务与范围 | 8766 PID **13942**，与实施重启记录一致；正式代码未改，仅追加本报告，无提交推送、无全量重跑 |
| 证据 | `/tmp/review-t5-m1/`；真实 RSS 原件复用 `/tmp/impl-t5-m1/real-rss.xml`，不重新刷新正式快照 |

本次任务 `task_95fc588d5811` / Dispatch `ctx_c6cc44368873`。复验日期为 2026-10-30（精确起止未单独计时，不据此计算耗时）。全程只用 headless Playwright 独立 profile；真实 8766 只 GET，隔离随机端口服务承接反馈、收藏、拿去写。没有 computer use，没有操作黄总桌面或真实 Chrome。

### 1. 返修 SHA、范围与测试差异：通过（限可比较基线）

- `linuxdo_source.py` SHA256：`63c3845c1796e113511ab78a985c83905c514fadfed4826b9f490db1ecf92855`。
- `tests/test_linuxdo_source.py` SHA256：`274a1c0b4870e6cd68d2a10e0d3ececdc3536e43e31b46e12fa0f7ad53c4cc8d`。
- 以上与实施返修报告及 `/tmp/impl-t5-m1/sha.json` 一致，复验结束再次计算仍一致。与本人上一轮 `/tmp/review-t5/sha.json` 比较，T5 的 12 文件仅上述 2 份变化；其余 10 份（今日后端、前端、共享组件与原 e2e 等）字节不变。
- 独立阅读返修前后 source/tests diff：生产变化只在 RSS 子类补 `p` 段落边界，以及末尾入口/统计提取；既有测试断言未删除或放宽，新增 6 个回归测试。运输、节流、并发、安全等上一轮通过证据复用。
- **范围说明**：共享工作区本来就有大量其他任务的未提交及未跟踪文件，且这两个文件在 Git 中也未跟踪，不能靠 `git diff` 空输出声称整个仓库干净。可独立确认的是 T5 的 12 文件基线仅 2 份返修变化；缺少全仓库返修前快照，不能把其他共享改动归因或排除归因于此次实施。本复核没有更改任何正式代码。

### 2. 原反例与独立边界：原 M1 通过，L1 单列

`probes.py` 独立构造输入，精确比较 `metrics.replies` 与完整 `full_text`，不是只检查字段存在：

| 输入或边界 | 结果 | 观察 |
|---|---|---|
| 正文“以前积累了 100 个帖子，这次分享一下。”+ 尾部 8 帖 / 3 参与者 | 通过 | 回复 7；正文逐字保留 |
| 同一正文无尾部统计 | 通过 | 回复 None；正文逐字保留 |
| 尾部统计后多余空格、Tab、CRLF、空行 | 通过 | 回复 7；正文最后一段保留 |
| 正文末句仅“100 个帖子”，无参与者；及带句首文字变体 | 通过 | 回复 None；两段全文均保留 |
| HTML `p/small` 统计 + `p/a` 阅读入口 | 通过 | 回复 7；仅删 RSS 尾段 |
| 统计内部及行首使用 U+3000 全角空格 | **失败（L1）** | 回复 None；统计段残留，无正文丢失 |
| 正文里有全角空格，统计仍为普通空格 | 通过 | 正文全角空格原样保留，回复 7 |
| 只有“阅读完整话题”，无统计 | 通过 | 回复 None；仅删独立末尾入口，正文末段保留 |
| 正文含“- 3 位参与者”字样；含“100 个帖子 - 3 位参与者”的自然语言末句 | 通过 | 正文逐字保留，不误当统计 |
| 正文历史整行统计 + 后续末段 + 真尾统计 | 通过 | 只取真尾部 8 帖，历史统计和末段均保留 |
| 正文整行统计之后仍有正文，无真尾统计 | 通过 | 回复 None；不误删末段 |
| 最后正文段以内联链接结尾，紧邻统计 `p` | 通过 | 末段及链接文字保留，统计分段并删除 |
| 统计中使用 Tab；单帖；统计后还有正文；无参与者但有入口 | 通过 | 各为 7 / 0 / None / None，全文均与预期一致 |

共 18 组，17P/1F；表中部分行合并同类变体。另从 `/tmp/impl-t5-m1/linuxdo_source.old.py` 动态加载旧实现，只在 /tmp 运行，原两反例都测出旧值 99，证明探针确实能捕获 M1，未替换共享源文件。

**L1：全角空格统计的兼容性缺口。** `linuxdo_source.py:97` 的统计内部和行首只允许 `[ \t]*`；输入 `<p>　8　个帖子　-　3　位参与者　</p>`（空白均为 U+3000）时不匹配，回复为 None，统计保留在 full_text。若要容忍该格式，应限制在单行的前提下允许相应 Unicode 空白，并保留 `\Z` 和整行结构；不能简单放宽成任意正文匹配。本次原始真实 RSS 的 30/30 尾段都使用普通空格，没有该变体，实际页面回复正确；此边界没有恢复原先把正文 100 误算为 99 的错误，也没有删掉用户正文。因此判 **L，非阻断兼容性建议**，不要求为了本轮收口扩展修复范围，是否后续支持由主控决定。

### 3. 定向回归与真实用户路径：通过

- `PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python -m unittest tests.test_linuxdo_source tests.test_today_view -v`：**41/41**，9.204 秒，`directed.log`。
- 从正式 `tests/e2e/today_t5.cjs` 复制到 `/tmp/review-t5-m1/today_t5.cjs`，仅调整 ROOT 和证据输出目录，测试逻辑不改；运行 **22/22**，随机端口隔离工作区 `/tmp/today-t5-CylbnH`，`e2e.log`、`e2e/fixture-results.json`。覆盖火爆边界、慢 RSS 首屏、来源图标、linux.do 全文、准/不准、存后素材库搜索、拿去写带全文/链接、390 宽、403 旧快照。没有对真实条目执行写动作。
- 未复跑全量；复用上一轮独立全量证据及此次实施全量报告，不把实施自报数当成本次独立复跑结果。上一轮已验收运输/凭证/并发等项关闭，代码差异没有触及这些逻辑，没有新的实际回归证据。

### 4. 真实 linux.do RSS → 今日页 → 阅读器：通过

原始 XML 复用实施此次正常刷新保留的 `/tmp/impl-t5-m1/real-rss.xml`，SHA256 `94813911f28fe4cb78c53eb1986ac5cdbf1cd0ad214c356e5b828d25e3d91355`。本人 `rss_expected.py` **不调用被测 `parse_rss`**，直接在 XML description 的原始 HTML 尾部定位 `p/small` 统计与 `p/a` 阅读入口；从剩余 HTML 独立生成正文预期，覆盖全部 30 个尾统计的格式检查。所核一条：

- URL `https://linux.do/t/topic/3092762`，ID `url:12a1e46a03fe35c8`。
- 原始尾部 **55 个帖子 - 36 位参与者**，列表 API、详情 API 和浏览器阅读器均为 **回复 54**。
- API 与浏览器 full_text 均与独立预期逐字一致，**501 字符**；最后一段 `开源地址:GitHub - paipiange/nvzhuang · GitHub` 保留，无 RSS 统计及阅读入口残留。
- 独立 profile 真实浏览器走今日页 → 48 小时 → linux.do → 点开该卡片；真实只读 **9/9**，首屏 **166.6 ms**；火爆返回 20 条均满足阈值，linux.do 30 条浏览未知且不火爆；390 无横向溢出，写请求 0，外部请求 0，脚本错误 0。
- `real-1440.png`、`real-390.png` 已实际打开检查；桌面图清楚显示正文末段、GitHub 链接及“回复 54”。证据 `real.cjs/json/log`、`real-expected.json`、`rss_expected.py`。
- 首次浏览器探针曾将开源链接尾文错误写成字面量“开源链接”，该断言失败；原始 XML 显示实际尾文为具体 GitHub 链接。已纠正 /tmp 探针为独立预期的末段精确比较后复跑，全程未改产品代码；初次日志保留为 `real-first-attempt.log`，不计作产品失败。

原 M1 的错计数和正文误匹配已消除，可关闭；L1 如上保留，不声称所有 Unicode 统计格式均通过。
