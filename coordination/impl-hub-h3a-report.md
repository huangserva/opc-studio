# H3a 实施报告

| 决定要点 | 结果 |
|---|---|
| 结论 | 返修 M2 实施自测通过，待独立复核；六条跨日误杀恢复，语义未知项单列 |
| H/M/L 自查 | 0H / 0M / 1L（剩余语义待主控判断，不冒充已确认非AI；原L1反例已处理、L2性能承诺撤回） |
| 改动文件与 SHA256 | M2最新8文件见 evidence/impl-hub-h3a/m2/changed-sha256.json；M1与主交付索引为历史版本 |
| Python / UI | M2定向126/126；旧隔离E2E 56+22+22；真实44/44；本轮不重复全量 |
| 新 PID | 21782（M2经主控批准在空档仅重启一次，调度health正常） |
| 证据 | M2：coordination/evidence/impl-hub-h3a/m2/；下文主交付/M1数字为历史版本 |
| 授权与范围 | M2仅闸门辅助/配置/tests；主控修订短词分组与两项合成预期、批准空闲重启；前端/server/调度不改，不提交不推送 |

## 交付内容

- linux.do 主源为人工智能标签 RSS，开发调优版块为补充候选，日榜/周榜补充；以话题数字 id 合并。分类从每个 item 的 category 读取，置顶从 `{http://www.discourse.org/}topicPinned` 读取。置顶字段经过真实 RSS 核验，非标题推断。
- 显示闸门只有 `scripts.radar_common.today_relevance_gate`：linux.do 分类黑名单优先，然后原有 `is_ai_related` 或当前兴趣方向里的具体词命中；不改 `is_ai_related` 的既有行为。兴趣通道只有纯 Grok 来源豁免隐藏，仍输出相关性标记，混合来源不豁免。
- 黑名单是 `today_filter_config.py::LINUXDO_CATEGORY_BLACKLIST`，初版搞七捻三/福利羊毛。所有来源的过滤详情均保留标题、可读来源、原因与原帖，页面顶部可展开核对。
- 内容只用已有 quoted/card/article/thread 展开，正文及补充剥离 URL 后有效字符均不足 16 时标「内容不足」，列表后排，不进入该做的 3 条；不足条目不会被当作不相关而直接删除。
- AIsa 51 人统计读取 `workspace/twitter-radar/snapshots/` 原始采样，不拿派生 bundle 作分母；按所选发布时间窗口、tweet_id 去重，报告当天及最近七天。各来源可重合，来源计数不能直接相加为列表总数。
- 「不准」累计文件：`workspace/today/inaccurate-counts.json`，schema=today-inaccurate/v1，`counts[可读来源][分类]` 保存次数。进入不准状态才加一，重复请求幂等，撤销不清历史；发现、兴趣都接线。文件供未来周复盘只读使用，不自动改黑名单。
- 新 CLI：`../article-importer/.venv/bin/python linuxdo_report.py --root <仓库>`。保留上一轮 `workspace/linuxdo/previous.json`，回复涨跌保存在条目 `reply_delta/reply_change`；RSS 无浏览数，不比较浏览。每六小时的 script 任务「linuxdo 热帖报告」已登记，输出 `workspace/linuxdo/reports/ai_hot-YYYYMMDD-HHMM.md`。

## RSS 实测

- AI 标签 200 / 30 条，开发调优 `/c/develop/4.rss` 200 / 25 条；试探 `/c/ai/111.rss` 404，未纳入配置。
- item 中 category 和 Discourse 命名空间 topicPinned 均真实存在，证据 `rss-field-probe.json`。标签源也混入黑名单分类，因此同样执行黑名单。
- 保留 T5 无凭证 curl、单源 2MiB、超时、无重定向、失败十分钟/成功一小时节流与跨进程锁。多源结果成功但部分源失败时显示原因并十分钟后重试；单源失败不丢其他有效结果。首次切换多源版本会触发一次后台刷新。

## 过滤前后（2026-10-30 12:29 左右，北京时间）

| 来源 | 过滤前 | 隐藏 | 展示 | 内容不足 |
|---|---:|---:|---:|---:|
| AIsa 推特 | 5 | 0 | 5 | 0 |
| Grok | 22 | 0 | 22 | 2 |
| 视频榜 | 1 | 0 | 1 | 0 |
| linux.do | 53 | 29 | 24 | 2 |

AIsa 51 人原始采样：151 → 103，隐藏 48（31.8%）；这组数字独立于上表的派生 AIsa 列表。来源重合按各来源分别计，过滤面板隐藏条目按 id 去重，总计 77 条。

三条硬性样例均在过滤区标出「分类黑名单：搞七捻三/福利羊毛」，均不在今日主列表。截图：`real-1440-filtered-1/2/3.png` 与 `real-390-filtered-1/2/3.png`；手机图第 2 张同时展示三条及其原因。

非 linux.do 隐藏条目逐条列于 [non-linuxdo-filtered.md](evidence/impl-hub-h3a/non-linuxdo-filtered.md)，共 48 条，含标题、来源、原因、原帖；由主控判断是否误杀。Grok 的短链接条目保留并后排，真实页面已验证内容不足标记。

## AIsa 最近七天回放

| 日期 | 采回去重条数 | 隐藏 | 保留 | 隐藏比例 |
|---|---:|---:|---:|---:|
| 2026-10-30 | 151 | 48 | 103 | 31.8% |
| 2026-10-29 | 327 | 86 | 241 | 26.3% |
| 2026-10-28 | 361 | 110 | 251 | 30.5% |
| 2026-10-27 | 196 | 57 | 139 | 29.1% |
| 2026-10-26 | 393 | 93 | 300 | 23.7% |
| 2026-10-25 | 301 | 80 | 221 | 26.6% |
| 2026-10-24 | 312 | 90 | 222 | 28.8% |

口径为各日北京时间发布窗口内、当时已有 51 人采集任务落盘的原始推文，跨轮按 tweet_id 去重；今天尚未结束，旧日回放截止当日 23:59:59。不是估计账号当天所有未采回推文的数量。

## 旧来源对照

只读旧文件 09:47 版共 148 话题；当前多 RSS 合并 111 话题，重合 5 条（旧集合覆盖 3.4%，RSS 集合重合 4.5%）。旧报告 Top 8 与新报告 Top 8 重合 0 条；完整旧报告内链接与新报告重合 1 条。

L：旧文件按 AI热度/AI最新/Claude/OpenAI/ChatGPT/Gemini 等 JSON 标签热度取数；本单按要求改为公开标签 RSS + 版块 + 全站补充，采样时间也不同。上述低重合是实测结果，不能声称新旧热榜等价。详情与 id 列表见 `old-comparison.json`。

## 测试与部署证据

- 全量 Python：`../article-importer/.venv/bin/python -m unittest discover -s tests`，3697 项，0F/1E/7S，416.574 秒。唯一 E 为受保护 `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，实际异常 `ValueError: 参考素材宽高须为 32 的倍数`。错误全文在 `python-full.log`。相对通知基线 +32 项，其中本单在该轮收集时新增 10 项，其他 22 项为共享工作区测试集差异，未在本单修改。
- 全量之后的必要增量：补了反馈累计公开路径、后排/Top 排除、真实 Discourse 命名空间置顶反例共 3 项；最终定向 `tests.test_today_h3a tests.test_today_view tests.test_interest_channel tests.test_linuxdo_source` 为 82/82（本单新增现共 13 项），见 `targeted-final.log`。
- UI 全量：`npm run test:ui`，723 项，719P/3F/1S，19.146 秒；3F 全在受保护 `tests/test_lab_test_drama_ui.cjs` 的既有三项。`radar_links_e2e` 本次未挂起。
- 原今日页隔离浏览器：56/56；兴趣隔离浏览器：22/22；T5 最终隔离浏览器：22/22。所有既有断言保留。
- 最终 8766 重启 PID 89345；之后独立 Chromium profile 对真实 8766 端到端 17/17。覆盖三条隐藏及原因、原帖安全链接、按来源/51人统计、1440/390、无横向溢出、内容不足真实卡片后排、AI标签/周榜标注、页面立即运行与成功记录。
- 「linuxdo 热帖报告」任务 id `46c7af7e56a44b70bd040840842f1f43`，最终手动 run `1652e2a24082451083cc29f1d69caf9f`，状态 `succeeded`；开始 `2026-10-30T04:29:20.120394+00:00`，结束 `2026-10-30T04:29:20.624348+00:00`，输出 `/Users/huangzongning/development/article-studio/workspace/linuxdo/reports/ai_hot-20261030-1229.md`。任务 kind=script，minutes=360，enabled=true。

## 改动 SHA256

| 文件 | SHA256 |
|---|---|
| `linuxdo_source.py` | `dcc73ba2a10f596e4c2282bbbedd954bf4563b665ccd8ee37ac632f531ca0fce` |
| `linuxdo_report.py` | `2a8e612e28b7442523057c06baaad498f90b60eb0bc0acfa6ebde06fcb887738` |
| `today_filter_config.py` | `9d627e65a707b41cd6e51fc83b96c6d104da4794f0fe0e31f0af7b07b7988269` |
| `scripts/radar_common.py` | `51c614b92cba33abe8dff43af86ca301f494f436721ccbacd39d998200f0ec64` |
| `today_view.py` | `66ea2c08e896c34a087d82a0e887c7b3ec2b8a6dd6f40ca4785e6c9b89eab856` |
| `interest_channel.py` | `910279a88990f86acd2e504681de8ecac5f49620ac1185734d18d8ee4a014824` |
| `public/today.js` | `64623bbfc75426abc3ca3ae150d6d20ba56310c0736631784cd94eed0b76fdb2` |
| `public/today.html` | `594d252d9e51b2e49105030677870d3554529ec66eec2386530774d1589ac376` |
| `public/today.css` | `32ce506f007ec5f44c4086efad4dcca3eedaed3154878a4561892bee712f5c00` |
| `public/components/item-card.js` | `4821eb6953beaf903d76e1be38e88ff39923be44044fb287fc905374c0abf55c` |
| `tests/test_linuxdo_source.py` | `20bc5dab1b51f3ec7d97832077fd9dbcccf1d30cb8a775914c654a2c782d741d` |
| `tests/test_today_h3a.py` | `1ec9d2820ddf0c5fd276cfd5655eed7cf6a8be50a8e94163aa042027931ff8a5` |
| `tests/e2e/today_t5.cjs` | `7e03ff311b64e70e147d55ddccbb4a4621be15df9e51914897169713449afafd` |
| `tests/e2e/today_h3a.cjs` | `783dc43657ad5c4710a3e455787c6f6e69fd90e9e3e483682cb463d9040b2dc0` |

## Codex 弱点逐条自测

- W1：频道级 category/topicPinned 与条目内同名字段故意冲突，确认取 item 内字段；正文数字和尾部统计的既有反例保留。真实 RSS 还核对了扩展命名空间。
- W2：新增测试均用 /tmp 临时根与 GROK_BOT_REPORTS_DIR，patch 用上下文管理器或 addCleanup；不写真实 reports、LaunchAgents、tasks.db。真实采样只读，正式任务登记是本单明确交付。
- W3：收尾凭据模式扫描结果见 credential-scan.json；不打印 plist、环境、异常原文或 Key。
- W4：既有今日页断言没有放宽。主控同意的 T5 迁移只换 mock 边界与返回 tuple，完整 diff 见 t5-mock.diff、python-mock.diff；/tmp 副本去掉 8 秒延迟后原「8秒RSS仍在后台执行」断言报 F，见 t5-negative.log。

## 范围与交接

H3b 微信线索晨报未实施。本轮没有改 server.py、platform_registry.py、受保护视频实验室、采集调度或 Grok Bot 设置；没有提交或推送。自然日连续三天的调度运行记录留给主控后续巡检。

## 返修 M1：相关性闸门误杀 AI 帖

本节为 M1 最终结果；上文过滤数量、PID、SHA 与截图属于主交付历史版本，当前以本节及决定要点为准。

### 修复与范围

- `is_ai_related` 原行为未改，AST 比较确认完全一致；原模块既有函数只有 `today_relevance_gate` 修改，新增三个仅供它使用的辅助函数。扩展词、套餐/产品语境规则、合法版本名/复数形式集中在 `today_filter_config.py`。
- 判断前做 NFKC、大小写折叠和 Claude 的局部形近字归一化（CIaude/c1aude），按英文 ASCII 词边界匹配；DS 必须带版本号，不匹配 DSL、ads、DS4.1protocol。保留 GPT6、DeepSeekV4.1、Seedance2.5、Agents/APIs 等合法形式，避免收紧边界时再误杀旧有产品写法。
- 递归纳入已有 RT、引用、卡片标题及 legacy/result 嵌套正文，不只看外层短文；剔除 URL 随机串对词表的误命中，不新增原帖或链接抓取。M1 没有硬编码任何帖子 id。
- `public/today.js` 仅改 `renderFiltering`。主控通过 Orca ask 明确同意最小扩围、UI 全量、不重跑 Python 全量；页面区分「主列表候选过滤明细」和「AIsa 51 人原始采样统计」，后者明示「原始采样统计，不属于主列表候选」。
- 主控确认不把 51 人原始推文补进主列表；主列表仍以派生 bundle 等既有输入为准。因此下面分开写原始统计恢复与主列表实际恢复，避免把统计保留冒充列表新增。

### Muse 核实依据

观察：Meta 官方 [产品介绍](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/) 和 [Muse 产品页](https://ai.meta.com/muse/) 都明确把 Muse 定义为个人 AI agent，可执行任务并在后台继续工作。

推断：两条样本的「收到更新」「自己解决问题、不用事事问我」语境与该 AI 产品相符，按 AI 相关保留；Muse 采用产品/版本/自主任务语境规则，普通同名乐队演唱会不因此通行。未知：没有独立核实帖子所述「10.0」版本号或其发布说明，不把该版本号当作已验证新闻事实。证据 `muse-verification.json`。

### 先证明旧闸门能测出错，再验证新闸门

- 19 条真实样本来自已落盘原文（12 个 X 正例、1 个 linux.do 正例、6 个 X 反例），夹具 `tests/fixtures/today_h3a_m1.json`。
- `/tmp` 独立旧模块副本测试 19 项：13 个正例全部 F、6 个反例 P，符合预期反证。完整失败日志 `old-gate-real-cases.log`，旧源副本 `old-radar-common.py`，没有就地换回共享代码。
- 新闸门真实样本 19/19 全过；追加边界、全半角/形近字、产品重名、合法模型版本、RT/引用/卡片、分类黑名单与纯 Grok 兴趣豁免反例后，本单新测试 26/26。每种补充正文测试均同时移除正文以证明会变 F；没有借外层文字假通过。

### 过滤前后（同一时间窗口及同一批已有数据）

| 来源 | 候选数 | M1 前隐藏 | M1 后隐藏 | M1 前保留 | M1 后保留 |
|---|---:|---:|---:|---:|---:|
| AIsa 推特 | 5 | 0 | 0 | 5 | 5 |
| Grok | 22 | 0 | 0 | 22 | 22 |
| 视频榜 | 1 | 0 | 0 | 1 | 1 |
| linux.do | 53 | 29 | 27 | 24 | 26 |

AIsa 51 人原始采样（独立统计分母）：151 条；隐藏 48（31.8%）→ 33（21.9%），保留 103 → 118。

原始采样统计：由隐藏改为保留 **17 条**；主列表实际新增展示 **2 条 linux.do**（tibo 套餐用量，以及正文谈 WorkBuddy 的「403是不是意味着被腾讯封杀了？」）。主控列的全部正例均通过闸门；6 条无关反例均继续隐藏。

最新非 linux.do 隐藏清单共 **33 条**，逐条含标题、来源、原因与原帖：[non-linuxdo-filtered.md](evidence/impl-hub-h3a/m1/non-linuxdo-filtered.md)。恢复清单分两栏：[原始采样恢复保留 / 主列表实际恢复](evidence/impl-hub-h3a/m1/restored.md)，JSON 同时保留完整 id。

### AIsa 最近七天隐藏比例重算

| 日期 | 原始去重数 | M1 前隐藏 | M1 前比例 | M1 后隐藏 | M1 后比例 |
|---|---:|---:|---:|---:|---:|
| 2026-10-30 | 151 | 48 | 31.8% | 33 | 21.9% |
| 2026-10-29 | 327 | 86 | 26.3% | 88 | 26.9% |
| 2026-10-28 | 361 | 110 | 30.5% | 107 | 29.6% |
| 2026-10-27 | 196 | 57 | 29.1% | 58 | 29.6% |
| 2026-10-26 | 393 | 93 | 23.7% | 109 | 27.7% |
| 2026-10-25 | 301 | 80 | 26.6% | 76 | 25.2% |
| 2026-10-24 | 312 | 90 | 28.8% | 92 | 29.5% |

回放只读既有原始采样，按北京时间各日发布窗口、tweet_id 去重；今天未结束。旧、新闸门使用同一个回放时刻，既包含扩展词找回，也包含词边界/URL 随机串修正，所以并非每一天的隐藏比例都下降。复现脚本 `tests/h3a_m1_replay.py`，逐日数据 `seven-days.json`。

### 额外隐藏项（未冒充人工确认无关）

词边界与 URL 随机串修正后，今天额外隐藏 2 条，均属于 51 人原始统计，主列表没有新增隐藏：

| id | 标题 | 判定与剩余未知 |
|---|---|---|
| x:2115896335681323499 | RT @Xian0063: ❤️ 终于把【春日续卷】上线了～ 230 位市井人物，每一位都会动—— 有人拽绳吊粮，有人扛包码垛，有人掀帘卖花，河上还有船娘弹琵琶 后面我会出另一种【上河新风格】欢迎关注哦～ https://t.co/1iH3 | 正文描述人物动画，是否 AI 制作未知，留给主控判断；旧匹配来自短链随机串中的 H3 |
| x:2115883934332367296 | 曾联手索罗斯击溃英格兰银行、30 年从不亏损的华尔街宏观之神，Stanley Freeman Druckenmiller，把 X 上的第一条推特献给了一个链上衍生品协议， 德鲁肯米勒今天公开宣布入局 https://t.co/ieH3h3s | 金融衍生品正文；旧 is_ai_related 将 Capital 中的 api 子串误命中，新闸门词边界正确拒绝（消融证据 url-ablation.json） |

没有把「人物会动」直接等同于 AI，没有只凭账号身份放行；新增隐藏项及原帖同时在恢复清单末尾供复核。

### 测试、重启与真实页面证据

- Python 定向：108/108，覆盖 M1 26 项、H3a、今日页、兴趣、linux.do。日志 `targeted.log`。Python 全量按主控明确回复不重复跑，沿用主交付 3697 项 0F/1E/7S 的历史证据。
- UI 全量：723 项，719P/3F/1S；3F 仍全在受保护 `tests/test_lab_test_drama_ui.cjs`。日志 `ui-full.log`。
- 原有隔离浏览器回归：今日页 56/56、兴趣 22/22、T5 22/22，原断言和 mock 接线均未在本次改变。
- 后端重启后的服务 PID **23580**；真实 8766 只读隔离 Chromium 31/31。覆盖每个 X 正反例在原始统计的实际状态、linux.do 正例回列表、两组不混排、提示语、三条黑名单原帖及原因、1440/390、无横向溢出和脚本错误。
- M1“真实接口五次均<1秒”的性能承诺撤回：独立复核曾出现>1秒请求，不能泛化；本项非功能硬门槛。M2仅保存串行GET原始耗时，见末尾。
- 截图：`real-1440/390-candidates.png`、`real-1440/390-aisa-raw.png`；三条硬性样例各有 `real-1440/390-blacklist-1/2/3.png`。
- W1：DS/DSL/ads 与产品边界、CIaude、每种结构化补充内容均有正反例；W2：用 /tmp 副本和上下文 patch，不写真实 reports/LaunchAgents/反馈/tasks.db；W3：本节最终报告和日志的凭据扫描见 `credential-scan.json`；W4：scope-audit.json 证明原有 H3a 文件除本次获准的 3 个生产文件外 SHA 全部未变，today.js 也只有 renderFiltering 改动。

### M1 最新文件 SHA256

| 文件 | SHA256 |
|---|---|
| `scripts/radar_common.py` | `c35d7cfc7e9c6e8749d641ea114bf8ca85551b80f4e91a28f2f2800f527d1419` |
| `today_filter_config.py` | `b4e5dbba1d9481348e5a9e251e9d1a3a2db15a3ef6bc6f23639386c2e72d55d8` |
| `public/today.js` | `13f04ea957163fe38f5ee7dfbf2ef04943dd881318f6c5596c325e5ed0db1a2b` |
| `tests/test_today_h3a_m1.py` | `28f6043a968b271b2aa26c8498d8038fcda98058fe3d308c439dccba289c33c2` |
| `tests/fixtures/today_h3a_m1.json` | `f89c4b1e443ff44f15fa20247e3013f0f748dbfe5cfa1eaa5e00439304e03dd8` |
| `tests/h3a_m1_replay.py` | `be6d75bfa00df37d7d5d68042a6300d744c95a321856fad736de32580e832bb9` |
| `tests/e2e/today_h3a_m1.cjs` | `9537037f458b20f8e037f18521b2e7649f95b4279da40db684f3c44e75b30f86` |

未提交、未推送。待主控复核恢复清单及额外隐藏项，再交独立复核窗口。

## 返修 M2：跨日词边界误杀

### 范围与主控修订口径

本轮任务 `task_e2596909afb1` / Dispatch `ctx_4641294c0385`，接 M1 `task_47efe67580c5`。本轮只改 `scripts/radar_common.py` 的显示闸门/辅助、`today_filter_config.py` 和tests；`is_ai_related` 函数AST原样不变，不动采集、调度循环、server或今日页前端。

原“所有旧词一律子串超集”在实施问答中经主控明确改为两类结构规则，因为 `ai` 会命中daily/campaign/said/email、`api`会命中capital、`repo`会命中report。最新口径为：

- 安全子串类：中文旧词全部保持原子串；gpt、claude、agent、prompt、cursor、codex、skill、deepseek、qwen、gemini、openai、anthropic、grok、github、token、lora及4090沿用子串。配置逐词注明理由，集合覆盖所有旧关键词，且两类不重叠。
- 短词类：ai/api/mcp/llm/repo。独立词（紧邻中文或下划线可作为分隔）、去URL原文词内出现全大写缩写、已证实AI复合词白名单，任一命中即可。英文普通词内部仅小写片段不放行。先做NFKC再剥URL，短词大写判断保留原文大小写，中文/Claude形近字规范化保留。
- 白名单genai、xai、openai、aigc注明样本或既有产品依据；GrokBot与agentic由安全子串自然命中，未逐个补派生名。明确的「超级智能」「具身智能」加入增量词。
- 主控明确授权chatgptfan与Claudette两个合成预期改为保留；daily ads campaign继续拒绝，其他旧预期不变。裁定摘要见 `m2/acceptance-amendment.md`。

### 六条跨日误杀与扩展验证

10-26六条正例分别是 `x:2114535980132950057`（GenAI）、`x:2114612630098944012`（Siri/GrokBot）、`x:2114628016710279629`（Grokbot任务）、`x:2114691110727553094`（xAI超算）、`x:2114722478958444920`（agentic software development）、`x:2114719413538890201`（超级智能）。完整已有正文及链接存 `tests/fixtures/today_h3a_m2.json`，各自独立命名正例，全部通过。

/tmp隔离副本放回M1的代码和配置，运行同一组六个新正例，结果6F/0E；正式源码未被就地换回。日志 `m1-six-positive-negative.log`。另有旧M1上的新增边界探针首跑失败记录 `m1-negative.log`，不能把它冒充本轮代码失败。

### 短词七天依据与逐条人工核查

审计口径：**去URL后，旧关键词命中集合恰为该短词，且没有独立词或原文全大写，只剩词内小写命中**。包括复合词白名单已经恢复的帖子，不按M2是否隐藏筛样；按tweet_id去重。种子固定20261030起逐词+1；总体不足30条全部检查，不能称抽满30条。

| 词 | 七天符合条件总体 | 实查 | 依据与边界 |
|---|---:|---:|---|
| ai | 25 | 25 | 10条现有文字非AI、6条明确AI/自动化、9条主题或制作方式未知；6条AI/自动化均保留 |
| api | 1 | 1 | Capital金融衍生品帖子，非AI，继续隐藏 |
| mcp | 0 | 0 | 主控指定短词类；无符合“仅词内小写”条件的真实样本，不编造经验验证 |
| llm | 0 | 0 | 同上，独立/大写LLM不会落入这个排除总体 |
| repo | 0 | 0 | 同上；report片段用合成反例证明拒绝，不冒充七天真实分布 |

26条逐条人工判断、理由、原帖链接、词内命中及M2状态见 [short-word-audit.md](evidence/impl-hub-h3a/m2/short-word-audit.md)，全文和固定种子见同名JSON。没有将“未知”标成“非AI”，也未用账号身份猜主题。

AI样本中，小写aigc以 `x:2115758761919017347` 补实证；Higgsfield两条由已有扩展词保留。Muse英文自主创建播放列表、Bot自主登录/取验证码、与Bot对话生成成果页三条，补明确产品操作语境；**没有把airtight、Gmail或用户名加入AI白名单**，相关单独普通词反例仍隐藏。七天逐条对照还发现 `x:2116032103100088509` 明确Vibe Coder，补Vibe Coder/VibeCoder/Vibe Coding，不靠短链随机串保留。

### 七天回放与“旧保留→新隐藏”全量清单

回放固定时刻及每一天窗口均在 `m2/as-of.txt`、`seven-days.json`，主控要求的10-24～10-30七天全部覆盖。统计从51人原始采样读取，按发布时间窗口、tweet_id去重；不是派生bundle或主列表新增数。旧列是**M1前**闸门，不是将M1误杀后的数据当基线。

| 日期 | 原始总数 | M1前隐藏 | M2隐藏 | M2隐藏比例 | 新增隐藏 | 恢复 |
|---|---:|---:|---:|---:|---:|---:|
| 2026-10-24 | 312 | 90 | 88 | 28.2% | 3 | 5 |
| 2026-10-25 | 301 | 80 | 72 | 23.9% | 5 | 13 |
| 2026-10-26 | 393 | 93 | 95 | 24.2% | 9 | 7 |
| 2026-10-27 | 196 | 57 | 56 | 28.6% | 1 | 2 |
| 2026-10-28 | 361 | 110 | 100 | 27.7% | 3 | 13 |
| 2026-10-29 | 327 | 86 | 84 | 25.7% | 5 | 7 |
| 2026-10-30 | 189 | 59 | 43 | 22.8% | 2 | 18 |

“新增隐藏”与“恢复”各自计数，不能用新旧隐藏数净差代替。全部条目逐条清单含标题、来源、原因和链接，见 [seven-days-newly-hidden.md](evidence/impl-hub-h3a/m2/seven-days-newly-hidden.md)；JSON保留已有全文便于独立判断。每条排除理由区分短词词内小写/URL剥离，没有未归因的机器差异。

本轮仍有28条旧保留→新隐藏，均按最新短词/URL结构规则逐条解释，并未把原“接近0”的措辞当成改数字的理由。`春日续卷`（x:2115896335681323499）、`GEO出单`（x:2115759808968266130）、`Atlas拖影描述`（x:2114346487882662303）等，现有文字不足以确认制作方式或缩写含义；保留在逐条清单交主控判断，不冒充已确认非AI，也不据此报告0%误杀率。作为1L语义观察，不是已确认的M级误杀。

### L1/L2处理、旧断言变更

- L1两个复核自造反例：`Pro 流量套餐用量翻倍`、`Muse 10.0 演唱会门票`均拒绝；Pro用量语境排除明确流量套餐，Muse不再单靠版本号通过。原真实Muse更新及自行解决问题仍保留。该结果限于所测语境，不声称所有同名歧义已经消除。
- L2：已撤回本报告M1节“真实接口五次均<1秒”的承诺；本轮保存可复现的串行GET原始耗时，不将偶然小于1秒作为通用性能结论或功能硬门槛。
- 修改过的旧断言只一处仓库列表迁移：`tests/test_today_h3a_m1.py` 的合成 `chatgptfan 日常` 从False移到True，按主控对gpt安全子串的明确授权。复核自造 `Claudette 的烹饪日记` 在新增M2测试按授权改为True（原复核脚本在/tmp，未改它的旧文件）。这两个预期翻转均明确是**合成样本、超集口径调整**。其余旧断言改动0；原13正例、6真实反例全过，自造五例按最新裁定为4拒绝/1保留，未伪称五例全拒绝。

### 已知弱点逐条自测

| 弱点/反例 | 结果与证据 |
|---|---|
| 只调当天词、跨日误杀 | 七天逐日全量回放并列出所有旧保留→新隐藏；六条10-26正例全部恢复；/tmp M1副本6F证明检错；未知内容单列 |
| 大小写边界 | gRoKbOt长任务保留，Duquesne CAPital金融交易隐藏；短词独立/原文大写/白名单分别验证 |
| 全角/半角 | ＧｅｎＡＩ保留、ＤＳＬ拒绝；全角URL规范化后不凭随机AI片段放行 |
| 形近字 | ＣＩａｕｄｅ设置保留，ＣＩａｕｄｅｆａｎ日常拒绝；未全局将I/1转换成l |
| URL与短链 | 正文GenAI加短链仍保留；只有旅行+含AI/gpt的短链拒绝；已有卡片/引用原文仍按原测试接入 |
| 英文词内子串 | GenAI/agentic/GrokBot保留；Capital/daily/report拒绝；Capital正文另含GenAI则仍保留，不做整帖否决 |
| 整链路遗漏 | 真实8766逐来源计数与回放一致：AIsa候选8→7（滤1）、Grok22→22、视频1→1、linux.do53→29（滤24）；51人原始189→146（滤43，22.8%）。页面summary为隐藏ID去重后的67，候选与原始组同ID可各自出现，不能相加冒充列表条数。1440/390截图及44项真实断言见real-e2e-results.json |
| 改旧断言掩盖回归 | 上节逐条披露仅两项授权合成预期调整；其他旧断言0；旧今日/兴趣/T5浏览器脚本未修改 |
| mock泄漏/真实写入 | 单测使用/tmp与上下文patch；回放只读快照并禁用linuxdo刷新；真实浏览器只GET，不点反馈/立即运行、不改真实来源或任务 |
| 凭据外露 | 对本轮代码、文本证据与最终报告做凭据模式扫描，结果见credential-scan.json；未打印密钥、环境、plist或任务正文，回放证据专门去掉H3b的clue_morning字段 |

### 测试、重启与真实浏览器

- Python定向：`../article-importer/.venv/bin/python -m unittest tests.test_today_h3a_m1 tests.test_today_h3a_m2 tests.test_today_h3a tests.test_today_view tests.test_interest_channel tests.test_linuxdo_source`，**126/126**；日志 `targeted.log`。全量不在本轮重复跑，沿用已交付证据。
- 旧隔离浏览器：今日56/56、兴趣22/22、T5 22/22，全部/tmp工作区/独立profile，旧脚本断言不改。
- 经主控附条件批准，06:31:15Z重启前GET确认9项启用任务无一运行（全部任务running也为空），最近next_run还有850.538秒；仅kickstart一次，PID **36018→21782**。06:31:17Z `/api/schedules/health` 为ok=true、running_count=0，无任务被本次重启打断。证据 `restart.json`。
- 新进程真实8766隔离Chromium **44/44**，来源/原始采样两组计数与回放一致，六条跨日正例七天原始统计不再隐藏，三条硬性linux.do样例仍按黑名单隐藏，1440/390无横向溢出和脚本错误。已目视1440候选与390黑名单第3张；截图 `real-1440/390-candidates.png`、`real-1440/390-aisa-raw.png`、`real-1440/390-blacklist-1/2/3.png`。
- 串行五次真实GET原始耗时（ms）：828.9 / 748.9 / 781.6 / 696.9 / 709.4；由新脚本保存 `real-timings.json`，只陈述此组观测，不宣称普遍每次<1秒。新M2脚本首次沿用“金融帖只在原始组”的旧静态数据假设失败，原日志保留 `real-first-failure.log`；当前候选池8条已含同一金融帖，改为对照API两组输入分别精确核对出现次数及隐藏状态后44项通过。旧M1验收脚本未改，也未削弱分组/黑名单条件。

### M2改动文件与SHA256

| 文件 | SHA256 |
|---|---|
| `scripts/radar_common.py` | `32fb099a6ddbdb1c830ae1682182fc0f725392e00afc39f0a5e92d22d3ecdb77` |
| `today_filter_config.py` | `526997013811332f798ab797ba4523e8015aa8a8611026c4ad34661b2e8d89ae` |
| `tests/test_today_h3a_m1.py` | `3fe3bf91cffcbeda2dace5bc975957f813798c08b9da17f8713fff5d3d1b5966` |
| `tests/test_today_h3a_m2.py` | `3b5e064fc9463bc7dc86b6ccc0159b05ce630ff2baed305e133d16d2552f7b81` |
| `tests/fixtures/today_h3a_m2.json` | `3bdbcc1559bf98d07434866d2b93d9003a647b957197c8d5dcf27bffcd976a4b` |
| `tests/h3a_m1_replay.py` | `f293393b753239ec407386e1685042c7c88828e96a86c90392c80300ad1ddd11` |
| `tests/h3a_m2_word_audit.py` | `0ff99e7d6681a083dd0295d1109f7cb37f32c4738c547a1482a15fbf62e77250` |
| `tests/e2e/today_h3a_m2.cjs` | `fdd49b7cf93d18ce24d8c1faa9725e25efccbb1728c01611ba6368683cd24b8f` |

范围证据 `before-sha256.json`、`scope-audit.json`：`is_ai_related` AST不变；旧函数仅`_today_content_text`与`today_relevance_gate`变更，新增`_today_legacy_ai_related`；与本轮无关的server、调度循环、今日页HTML/JS/CSS保持本轮开工SHA。不提交、不推送。


## 返修 M3

| 决定要点 | 结果 |
|---|---|
| 结论 | 实施自检通过，交 B 独立复核 |
| H/M/L | 0H / 0M / 2L：7条语义未知；一次检索范围偏差（只输出仓库模板plist的Label一行，详下） |
| 修复与范围 | 驼峰整段修复全大写误放；15个新增产品名（含主控授权0样本项和Opus）；仅7个允许源码/测试文件，完整SHA见下 |
| Python/负对照 | 旧126+新增114＝240通过；M2副本两条真实正例2F/0E；原14探针在M2为8F/0E，M3为14/14 |
| 浏览器 | 旧隔离56+22+22通过；真实8766 29项通过，三硬例均在输入且仍过滤，未知0；无脚本错误 |
| 七天 | 固定2,079条：M2→M3新恢复19、新隐藏14，两向逐条清单已交；逐产品抽样54项 |
| 重启 | 已获主控批准，UTC 2026-10-30T08:36:02.013472+00:00，一次kickstart；PID 21782→33302，health.ok=True |
| 隔离与交付 | 旧断言修改0，素材库清理单文件SHA不变；无采集/调度/前端产品改动；不提交、不推送 |

任务 `task_d4221c6ad7c8` / Dispatch `ctx_9e27780fc711`。按 M3 合同和主控本次 ask 补充裁定；历史 M1/M2 内容原样保留。完整 [补充裁定](evidence/impl-hub-h3a/m3/acceptance-amendment.md)。本节的「通过」限实施自检，最终由 B 独立复核。

### M：全大写普通词与驼峰

删除 `term.upper() in original`。仅对同时含大小写的英文词做驼峰切段，目标缩写须整段大写命中；全大写词不切分，只按独立词匹配。`MCPServer→MCP+Server`、`APIGateway→API+Gateway`、`GenAI→Gen+AI`、`xAI→x+AI`。`RAPIDServer→RAPID+Server`、`CAPITALOne→CAPITAL+One`、`MAILBox→MAIL+Box`均无AI/API整段。沿用既有缩写复数的通用尾缀处理（LLMs/MCPs→缩写+s），未给MCPServer加单词特例。genai/xai/openai/aigc白名单继续不区分大小写。

B原14项全部纳入fixture并通过；另加整句大写普通新闻3反例、混排3正例及主控新增3正3反。M2隔离副本同14项仍8F/0E，保留原始失败日志 [m2-fourteen-negative.log](evidence/impl-hub-h3a/m3/m2-fourteen-negative.log)。

| 需边界词 | 三个全大写反例 | 两个混排正例 |
|---|---|---|
| ai | `CHAIR`, `MAIL`, `RAIN`，3/3拒绝 | `someAITool`, `myAIHelper`，2/2保留 |
| api | `CAPITAL`, `CAPITOL`, `CAPITALISM`，3/3拒绝 | `myAPIClient`, `dataAPIService`，2/2保留 |
| mcp | `SCULPTINGMCPIECE`, `PREFIXMCPORT`, `TEAMMCPIECE`，3/3拒绝 | `newMCPClient`, `myMCPBridge`，2/2保留 |
| llm | `BELLMORE`, `WALLMOUNT`, `ALLMAILED`，3/3拒绝 | `myLLMTool`, `newLLMService`，2/2保留 |
| repo | `REPORT`, `REPORTER`, `PREPOSITION`，3/3拒绝 | `myREPOIndex`, `localREPOView`，2/2保留 |

上述25项逐条输出及其他全部合成结果见 [self-check-cases.json](evidence/impl-hub-h3a/m3/self-check-cases.json)。

### 常见产品名与七天依据

冻结输入沿用 M2 截止 `2026-10-30T06:31:24.406605+00:00`，北京时间10-24～10-30。对51人原始采样按发布时间、tweet_id去重，2,079条；兴趣方向同样冻结。M2源码与配置副本随证据交付，重跑先逐条核验M2基线，再算M3，避免把没有传兴趣方向的裸闸门结果混成页面统计。

| 词 | 分类 | 去URL正文出现 / 该词规则匹配 | 新恢复 | 抽样实查 |
|---|---|---:|---:|---:|
| 通义 | 产品边界/版本（中文子串） | 0 / 0 | 0 | 0 |
| 千问 | 产品边界/版本（中文子串） | 5 / 5 | 0 | 5 |
| 智谱 | 产品边界/版本（中文子串） | 7 / 7 | 1 | 5 |
| glm | 产品边界/版本（中文子串） | 23 / 23 | 0 | 5 |
| 可灵 | 产品边界/版本（中文子串） | 1 / 1 | 0 | 1 |
| kling | 产品边界/版本（中文子串） | 8 / 8 | 0 | 5 |
| 即梦 | 产品边界/版本（中文子串） | 2 / 2 | 0 | 2 |
| minimax | 产品边界/版本（中文子串） | 26 / 26 | 1 | 5 |
| opus | 同句AI语境/明确产品格式 | 113 / 108 | 9 | 5 |
| 海螺 | 同句AI语境/明确产品格式 | 0 / 0 | 0 | 0 |
| kimi | 同句AI语境/明确产品格式 | 86 / 71 | 11 | 5 |
| 豆包 | 同句AI语境/明确产品格式 | 30 / 18 | 1 | 5 |
| 元宝 | 同句AI语境/明确产品格式 | 1 / 1 | 0 | 1 |
| 文心 | 同句AI语境/明确产品格式 | 2 / 2 | 0 | 2 |
| 混元 | 同句AI语境/明确产品格式 | 3 / 2 | 0 | 3 |
| deepseek | 既有安全子串 | 96 / 96 | 0 | 5 |

每词依据ID、分类理由已写入 `today_filter_config.py` 注释；[product-audit.json](evidence/impl-hub-h3a/m3/product-audit.json) 保留种子、样本原文与判定输入。[每词新恢复逐条列表](evidence/impl-hub-h3a/m3/product-recoveries.md) 全量列出ID、原帖、日期与摘要；同帖可能匹配多个产品，不能将各词恢复数相加当成总恢复数。

- Kimi 只在同句AI语境、K3.1等明确产品版本或正文 `@Kimi_Moonshot` 时放行；不看作者身份，不放行裸人名。`x:2115350337179807903` 实际正文有官方handle，恢复。
- 豆包须同句AI语境或明确的「豆包工作」产品复合名；工作餐不算。「豆包工作的运营」真实帖 `x:2114559061371064705` 恢复。
- 元宝、海螺只认同句AI语境；文心也须AI/Unlimited OCR语境，混元须AI/3D语境，避免文学与太极误命中。句号、换行等阻断跨句借用语境，小数版本点不拆句。
- 通义、海螺七天各0命中，**无噪音样本**；依主控明确补充授权，前者按低歧义产品加入，后者保留AI语境条件并补海边捡海螺反例。没有把0样本写成真实验证通过。
- Opus在驼峰收紧后会新丢失明确的Opus5.5视频帖；依候选「不限于」范围补产品规则并获主控明确同意：须小数版本或同句AI语境，裸Opus和音乐会拒绝。七天113条依据，新恢复逐条列出，不将用户名加入白名单。
- DeepSeek已有旧安全子串，96条原文出现，未重复新增。

逐词固定种子抽最多5条，不足5条全查，共54个词×样本核查（同帖可重复），均为产品/模型相关，未发现面食、钱币、F1人名、贝壳等同名日常内容。[逐条人工判断](evidence/impl-hub-h3a/m3/product-sample-judgments.md) 区分帖子相关性与其营销数字真实性；不据这组样本推断全量误命中率。

日常反例：`早餐吃豆沙包和豆包`、`压岁钱换成金元宝`、`F1车手 Kimi Räikkönen 赢得比赛`、`海边捡海螺`全部拒绝；另测文心雕龙、混元太极、豆包工作餐、sparkling和myminimaximal等词内子串，均拒绝。两条真实恢复例在 `/tmp/h3a-m3-negative-guib7zzx` 的完整M2源码/配置副本上运行相同新正例，**2F/0E**，见 [负对照日志](evidence/impl-hub-h3a/m3/m2-new-positive-negative.log)。正式源码未被就地替换。

### 七天两向变化与隐藏比例

| 日期 | 总数 | M2隐藏 | M3隐藏 | M3比例 | M2保留→M3隐藏 | M2隐藏→M3保留 |
|---|---:|---:|---:|---:|---:|---:|
| 2026-10-24 | 312 | 88 | 75 | 24.0% | 1 | 14 |
| 2026-10-25 | 301 | 72 | 73 | 24.3% | 1 | 0 |
| 2026-10-26 | 393 | 95 | 97 | 24.7% | 4 | 2 |
| 2026-10-27 | 196 | 56 | 57 | 29.1% | 2 | 1 |
| 2026-10-28 | 361 | 100 | 102 | 28.3% | 3 | 1 |
| 2026-10-29 | 327 | 84 | 86 | 26.3% | 3 | 1 |
| 2026-10-30 | 189 | 43 | 43 | 22.8% | 0 | 0 |

[七天两向逐条清单](evidence/impl-hub-h3a/m3/seven-days-deltas.md) 按天分别列出全部新隐藏与恢复，不用净差代替。所有14条新隐藏都来自正文转推账号 `@AYi_AInotes` 的旧大写片段例外；驼峰是 `A+Inotes`，没有AI整段。7条可据现有文字判为非AI，7条语义未知，不据账号推断内容。

| 新隐藏原帖 | 日期 | 人判 | 依据 |
|---|---|---|---|
| [x:2113878917895631281](https://x.com/AYi_AInotes/status/2113878917895631281) | 2026-10-24 | 非AI | X原创奖励/收款操作，只有转推账号字符串命中旧大写例外 |
| [x:2114282884454404518](https://x.com/AYi_AInotes/status/2114282884454404518) | 2026-10-25 | 非AI | GeekCon黑客赛与网安执法事件，未见明确AI词 |
| [x:2114459135601447090](https://x.com/AYi_AInotes/status/2114459135601447090) | 2026-10-26 | 未知 | 马斯克太空/访谈短摘要，不能据账号推断AI |
| [x:2114484416307171733](https://x.com/AYi_AInotes/status/2114484416307171733) | 2026-10-26 | 未知 | 未来工作与园艺比喻可能涉AI，现有文字没有明确产品/技术词 |
| [x:2114553419562438809](https://x.com/AYi_AInotes/status/2114553419562438809) | 2026-10-26 | 非AI | 癌细胞研究新闻，未见AI方法说明 |
| [x:2114706641002307620](https://x.com/AYi_AInotes/status/2114706641002307620) | 2026-10-26 | 未知/L | 戴尔超级计算机商业故事可能涉AI基础设施，但不能仅按行业猜测 |
| [x:2114904444915622121](https://x.com/AYi_AInotes/status/2114904444915622121) | 2026-10-27 | 未知 | Dan Koe长文/视频，未说明AI制作 |
| [x:2114941750829883624](https://x.com/AYi_AInotes/status/2114941750829883624) | 2026-10-27 | 非AI | X产品负责人及团队管理访谈 |
| [x:2115128545932702072](https://x.com/AYi_AInotes/status/2115128545932702072) | 2026-10-28 | 未知 | 仅转推账号和短链，正文不可判 |
| [x:2115380537372168509](https://x.com/AYi_AInotes/status/2115380537372168509) | 2026-10-28 | 非AI | Wise/Stripe跨境收款流程，未见明确AI词 |
| [x:2115402313460269325](https://x.com/AYi_AInotes/status/2115402313460269325) | 2026-10-28 | 非AI | 字节创始人财富与商业格局评论 |
| [x:2115673606353039464](https://x.com/AYi_AInotes/status/2115673606353039464) | 2026-10-29 | 未知 | 仅转推账号和短链，正文不可判 |
| [x:2115724000819380551](https://x.com/AYi_AInotes/status/2115724000819380551) | 2026-10-29 | 未知 | 仅转推账号和短链，正文不可判 |
| [x:2115691794688065854](https://x.com/AYi_AInotes/status/2115691794688065854) | 2026-10-29 | 非AI | A股/国运宏观投资评论 |

**1L语义边界**：戴尔超级计算机、未来工作比喻等7条仍是未知，不能说「全部正确过滤」，也未为了维持数字而补账号白名单。逐条机器依据与人工说明见 [newly-hidden-judgments.json](evidence/impl-hub-h3a/m3/newly-hidden-judgments.json)。本轮未发现安全旧词实际命中却被新闸门隐藏，`is_ai_related` AST原样。

### 真实8766、重启窗口和硬例

主控批准的条件是：等待本次wechat_radar自然结束后，所有启用任务running=false、最近next_run≥300秒，只重启1次。任务 `532ba5c03aa2431a8bc456bcf45ff9e0` 自然开始 `2026-10-30T07:45:27.038254+00:00`，自然结束（status=partial）`2026-10-30T08:09:09.591903+00:00`；未取消任务。

执行前 UTC `2026-10-30T08:36:01.972323+00:00`，9项启用任务均未运行，全部任务running列表为空，最近next_run剩 `565.063` 秒。**实际kickstart UTC `2026-10-30T08:36:02.013472+00:00`**，PID **21782→33302**；就绪 UTC `2026-10-30T08:36:03.569326+00:00`，health.ok=true、running_count=0。只调用一次kickstart，未改调度或plist。该UTC时间供R3-0的24h见证判读重启相关interrupted；本报告不宣称免除任何实际业务失败。[restart.json](evidence/impl-hub-h3a/m3/restart.json) 与预检分别留存。

真实页面按当时输入和离线同口径回放一致（时间 `2026-10-30T08:38:18.220173+00:00`）：

| 来源 | 原始候选 | 保留 | 已过滤 |
|---|---:|---:|---:|
| AIsa 推特 | 12 | 11 | 1 |
| Grok | 25 | 25 | 0 |
| 视频榜 | 1 | 1 | 0 |
| linux.do | 54 | 30 | 24 |

AIsa 51人实时原始统计：225→171，过滤54（24.0%），与离线脚本精确一致。该实时窗口和上面冻结七天表分开，不强行对齐成旧189条。页面按候选/原始组逐ID比较次数，两个组清楚分开；金融intc样例在两组API各唯一1条、都隐藏、DOM各准确1次。面板总隐藏78按ID去重：候选隐藏25、原始隐藏54、交集1，不能直接相加。1440/390各有计数、候选及原始组截图；已目视390计数与1440第三条硬例，条数及分类原因可读，布局无横向溢出，页面错误0。

硬例当前输入3例均存在，黑名单与DOM准确次数全部通过；未知0项。详情 [hard-samples-input.json](evidence/impl-hub-h3a/m3/hard-samples-input.json) 与 [real-e2e-results.json](evidence/impl-hub-h3a/m3/real-e2e-results.json)。有输入的样例必须在黑名单和DOM准确一次；缺输入的写明未知，不冒充通过。三条旧真实硬例已从M2证据纳入本轮Python回归，分类黑名单优先级3/3通过。

重启前真实7d API仍隐藏Kimi/豆包两条，见 [live-before-restart.json](evidence/impl-hub-h3a/m3/live-before-restart.json)；重启后两条均不再隐藏，Python原文正例同时通过，证明新规则已加载。无写反馈、立即运行或真实素材库动作。重启前后111条素材逐行SHA、反馈与兴趣文件全部一致，见 `protected-data-result.json`。相对上一单交付时107条，多出的4条external-references创建于08:15～16 UTC，在M3重启之前；原107行SHA不变，未改动这些新行，具体创建者未知并已报主控。

### 已知弱点逐条自测

| 已知弱点 | 逐条结果 |
|---|---|
| 单日调词造成跨日误杀 | **通过**：七天每日两向差异全部逐条列出，2079条与M2逐条核验；14新隐藏、19恢复不混成净差 |
| 只照字面大写规则 | **通过**：五个短词各3大写反例、2驼峰正例全部逐条通过；B14项全过；MCPServer等不靠单独特例 |
| 大小写 | gRoKbOt长任务保留；CAPITAL spending拒绝 |
| 全角/半角 | ＧｅｎＡＩ创作保留；ＣＡＰＩＴＡＬ财经拒绝 |
| 形近字 | ＣＩａｕｄｅ设置中文保留；ＣＩａｕｄｅｆａｎ早餐拒绝 |
| URL/短链随机串 | 正文GenAI+短链保留；仅旅行+含AI/gpt短链拒绝（全角URL同样拒绝） |
| 词内子串 | GenAI创作保留；report on daily email拒绝；sparkling和myminimaximal也拒绝 |
| 中文日常词 | 豆包工作运营保留；面食、钱币元宝、海边海螺拒绝；Kimi F1人名拒绝，AI语境正例通过 |
| 整条链路遗漏 | **通过**：真实来源计数、AIsa51人原始组、DOM逐ID次数、七天表、硬例输入存在性逐项留证；旧浏览器100项通过 |
| 改旧断言掩盖回归 | **通过**：旧测试与清理单所有受保护文件开工/交付SHA完全一致，旧断言修改0；新增114例独立文件 |

### 测试与范围证据

- `../article-importer/.venv/bin/python -m unittest tests.test_today_h3a_m3 tests.test_today_h3a_m1 tests.test_today_h3a_m2 tests.test_today_h3a tests.test_today_view tests.test_interest_channel tests.test_linuxdo_source`：240/240，日志 `targeted.log`。
- 旧隔离浏览器只执行、不修改：today_page 56、today_t5 22、interest_channel 22；profile、workspace均隔离。
- `node tests/e2e/today_h3a_m3.cjs --real`：29项通过，未知0，错误0。
- 七天复现：`../article-importer/.venv/bin/python tests/h3a_m3_replay.py --out coordination/evidence/impl-hub-h3a/m3`；固定pool、兴趣方向、M2两份源码均随证据交付。
- 范围AST：旧函数仅 `_today_legacy_ai_related`、`today_relevance_gate` 变更，新增 `_today_product_related`；`is_ai_related` AST不变。[scope-audit.json](evidence/impl-hub-h3a/m3/scope-audit.json)。素材库清理单5脚本、real_write_guard、run、knowledge_first/batch_delete SHA不变，server/today_view及M1/M2旧测试不变。
- 初始M3测试先在尚未修复的大写规则上运行，97项出现28F，原始日志 `initial-m3.log` 保留；随后按主控驼峰裁定调整本轮新合成命名，未改任何旧测试。旧14项在M2额外验证仍8F。

| 改动文件 | SHA256 |
|---|---|
| `scripts/radar_common.py` | `9c4fc3c06c2b6b643853c29b0fac7d69097bb4df0f7216292a1524e7a56dc254` |
| `today_filter_config.py` | `fe1f902b90fd5e96f37f20f56470b63aa014ea984256e65022ba888a28f7a543` |
| `tests/test_today_h3a_m3.py` | `a2470f1a51032738920f2f4cc3aab345cb4ecfabc8e99bb3ee6367445a81c23d` |
| `tests/fixtures/today_h3a_m3.json` | `c4f72d80c3cb2be09dea6855b124dfea3e005824c884d63d596d3f36d3b919b3` |
| `tests/h3a_m3_replay.py` | `676355492298499345955a23f7fae36fb37af0f95652dcf1179f7c42235fffbb` |
| `tests/h3a_m3_live.py` | `5bd63c480202e0d20d338291b793d5d28cc4f9e99e3380793a2be0defae671db` |
| `tests/e2e/today_h3a_m3.cjs` | `c4d5c2c9bfbecde6d630aef795b69e503ac967ea5a0926e6277090d8952f9869` |

流程偏差（L2）：定位服务标签时，`rg` 的 scripts 检索范围未排除plist，输出了仓库模板 `scripts/launchd/com.servasyy.article-studio-watchdog.plist` 中一行公开服务Label。合同禁止打印plist，这次范围控制不够精确；未读取真实LaunchAgents plist，未输出环境或凭据。该行不可撤回，明确披露交B判定；后续仅使用已知服务名执行批准的kickstart。

报告只追加本节，既有前文前缀SHA记录 `report-prefix-sha256.txt`。不提交、不推送；不触碰视频实验室保护边界。待B独立复核。


## 返修 M4

| 决定要点 | 结果 |
|---|---|
| 结论 | 自测通过，提交 B 复核；混排缩写复数 M1 已修，未逐样本加白名单 |
| H/M/L | 0H / 0M / 2L（承接既有即梦/可灵日常歧义、7条语义未知及产品覆盖边界；本轮无新增） |
| 改动与 SHA | 仅改 `scripts/radar_common.py` 的 `_today_legacy_ai_related`，SHA256 `2c19c9bdff5d3f1099170986ed66b19ad85f1cae65d2611b6dacf6ab6340e462`；新增 4 个 M4 测试/夹具/回放文件，完整 SHA 见下表 |
| Python / 负对照 | 原 240 + 新增 49 = 289/289；M3完整旧副本跑五条新正例 5F/0E |
| 独立探针 | B 原 82 项：五条 M1 均恢复，其余 77 项结果不变；80/82 符合原期望，余 2 条为黄总决定保留的即梦/可灵已知日常误放 |
| 七天回放 | 冻结 2079 条，截止 2026-10-30T06:31:24.406605Z；M3→M4 每条完整闸门结果相同，双向差异均 0 |
| 浏览器 | 旧隔离 E2E 56+22+22=100/100；重启后真实 8766 29/29，1440/390 无横向溢出，三硬例均在输入且仍被隐藏 |
| 重启 | 已获主控批准，UTC `2026-10-30T09:38:31.600795+00:00` 只 kickstart 一次，PID 33302→35891，health.ok=true，无新增 interrupted |
| 保护与证据 | 原 7 文件中仅 radar_common.py 改变；旧断言 0 改动、清理单文件 SHA 不变；111 条素材与反馈/兴趣/导出文件 SHA 不变；证据 `coordination/evidence/impl-hub-h3a/m4/` |

任务 `task_96fce4e8b40c` / Dispatch `ctx_9b4c408be893`。本节追加原实施报告；前述轮次记录不重写。本轮无提交、推送；没有操作真实桌面或用户 Chrome。

### 修复规则与改动范围

原 M3 只有当整词去尾 s 后全部大写才识别缩写复数，因而 `OpenAPIs` 走普通驼峰切段，错误得到 `Open/AP/Is`。M4 使用同一分段规则：**先匹配“全大写段 + 小写 s”，仅在词尾、后接新的大写驼峰段或数字时认作完整缩写复数，再匹配其余普通驼峰段，最后统一剥离缩写段的 s**。因此 `OpenAPIs→Open/API`、`ourMCPsHub→our/MCP/Hub`，而 `MAILs→MAIL`、`RAPIDs→RAPID`、`CAPITALs→CAPITAL`，普通词不会抽出内部 AI/API。

规则没有写入五条样本名，也没有修改相关词表或即梦/可灵条件。`today_filter_config.py` SHA 完全不变；七条语义未知、Muse bot、OpenPencil 未扩词。`scope-audit.json` 对比完整 AST，只有 `_today_legacy_ai_related` 改变，`is_ai_related` 与其余源文件 AST 原样；M1/M2/M3 旧断言 SHA 原样。

| 文件 | 状态 | SHA256 |
|---|---|---|
| `scripts/radar_common.py` | 修改 | `2c19c9bdff5d3f1099170986ed66b19ad85f1cae65d2611b6dacf6ab6340e462` |
| `today_filter_config.py` | 未改 | `fe1f902b90fd5e96f37f20f56470b63aa014ea984256e65022ba888a28f7a543` |
| `tests/test_today_h3a_m4.py` | 新增 | `6e1ce0bc9297b72ca3ef3930f249980ea2346216bb3fe322a87b007ad99ecb61` |
| `tests/fixtures/today_h3a_m4.json` | 新增 | `835b3496719c1eb5977a01042a1bf24a204e3cd41e6eb1a598e03f947530724e` |
| `tests/h3a_m4_replay.py` | 新增 | `3142cf91cd1d7dc3727e5e1d1237219587ee0e8af8b77ee133aa62770aeb2c75` |
| `tests/e2e/today_h3a_m4.cjs` | 新增 | `daabebe258969aa01ae96e214ed53b9a619e6d66613fdce80d6e5872069a3e68` |

开工 `git status --short`、7文件及保护文件 SHA 均在 `git-status-before.txt`、`before-sha256.json`；7文件基线与 M3 交付 SHA 全部一致。未改清理单任何正式脚本；旧 E2E 仅在 /tmp 复制版本中改 ROOT 引用和证据输出位置，所有断言保持原样，截图均落新临时目录。

### 必须继续成立及新增输入边界：逐条自测

以下均执行真实 `today_relevance_gate`；“新增输入边界”13条均给期望理由。B 原14探针仍按8拒绝+6保留，保持原期望。

| 类别 | 输入 | 期望 | 实际 | 结果 / 理由 |
|---|---|---|---|---|
| 阻断五例 | `OpenAPIs` | 保留 | 保留 | 通过；混排前缀后的完整缩写复数，应保持缩写整段 |
| 阻断五例 | `myMCPs` | 保留 | 保留 | 通过；混排前缀后的完整缩写复数，应保持缩写整段 |
| 阻断五例 | `newLLMs` | 保留 | 保留 | 通过；混排前缀后的完整缩写复数，应保持缩写整段 |
| 阻断五例 | `localREPOs` | 保留 | 保留 | 通过；混排前缀后的完整缩写复数，应保持缩写整段 |
| 阻断五例 | `theirAPIs` | 保留 | 保留 | 通过；混排前缀后的完整缩写复数，应保持缩写整段 |
| 必须保持正例 | `APIs` | 保留 | 保留 | 通过；既有独立缩写复数、驼峰整段或复合词保持 |
| 必须保持正例 | `LLMs` | 保留 | 保留 | 通过；既有独立缩写复数、驼峰整段或复合词保持 |
| 必须保持正例 | `MCPs` | 保留 | 保留 | 通过；既有独立缩写复数、驼峰整段或复合词保持 |
| 必须保持正例 | `MCPServer` | 保留 | 保留 | 通过；既有独立缩写复数、驼峰整段或复合词保持 |
| 必须保持正例 | `APIGateway` | 保留 | 保留 | 通过；既有独立缩写复数、驼峰整段或复合词保持 |
| 必须保持正例 | `OpenAIClient` | 保留 | 保留 | 通过；既有独立缩写复数、驼峰整段或复合词保持 |
| 必须保持正例 | `GenAI` | 保留 | 保留 | 通过；既有独立缩写复数、驼峰整段或复合词保持 |
| 必须保持正例 | `xAI` | 保留 | 保留 | 通过；既有独立缩写复数、驼峰整段或复合词保持 |
| 必须保持反例 | `RAPIDServer` | 拒绝 | 拒绝 | 通过；完整分段为普通词，不把内部子串当缩写 |
| 必须保持反例 | `CAPITALOne` | 拒绝 | 拒绝 | 通过；完整分段为普通词，不把内部子串当缩写 |
| 必须保持反例 | `MAILBox` | 拒绝 | 拒绝 | 通过；完整分段为普通词，不把内部子串当缩写 |
| 必须保持反例 | `CAPITAL` | 拒绝 | 拒绝 | 通过；完整分段为普通词，不把内部子串当缩写 |
| 必须保持反例 | `REPORT` | 拒绝 | 拒绝 | 通过；完整分段为普通词，不把内部子串当缩写 |
| 必须保持反例 | `CHAIR` | 拒绝 | 拒绝 | 通过；完整分段为普通词，不把内部子串当缩写 |
| 必须保持反例 | `MAILs` | 拒绝 | 拒绝 | 通过；完整分段为普通词，不把内部子串当缩写 |
| 必须保持反例 | `RAPIDs` | 拒绝 | 拒绝 | 通过；完整分段为普通词，不把内部子串当缩写 |
| 必须保持反例 | `CAPITALs` | 拒绝 | 拒绝 | 通过；完整分段为普通词，不把内部子串当缩写 |
| 新增输入边界 | `myAPIs` | 保留 | 保留 | 通过；小写前缀+API复数 |
| 新增输入边界 | `ourMCPsHub` | 保留 | 保留 | 通过；复数缩写之后是新大写驼峰段 |
| 新增输入边界 | `newREPOsIndex` | 保留 | 保留 | 通过；REPO复数后接Index |
| 新增输入边界 | `OpenAPIsLab` | 保留 | 保留 | 通过；API复数处于中间整段 |
| 新增输入边界 | `myLLMs2` | 保留 | 保留 | 通过；缩写复数后数字段 |
| 新增输入边界 | `HTTPs` | 拒绝 | 拒绝 | 通过；HTTP不在AI相关词表，不能只因全大写复数放行 |
| 新增输入边界 | `newMAILs` | 拒绝 | 拒绝 | 通过；MAIL整段为普通单词，不是AI缩写 |
| 新增输入边界 | `myRAPIDs` | 拒绝 | 拒绝 | 通过；RAPID整段不能抽出API |
| 新增输入边界 | `localCAPITALs` | 拒绝 | 拒绝 | 通过；CAPITAL整段不能抽出API |
| 新增输入边界 | `myAPIslab` | 拒绝 | 拒绝 | 通过；s后紧接小写lab，不是复数边界 |
| 新增输入边界 | `myMCPss` | 拒绝 | 拒绝 | 通过；复数仅单个词尾s，不允许重复s |
| 新增输入边界 | `ＷｅｂＡＰＩｓ` | 保留 | 保留 | 通过；NFKC规范后仍为混排缩写复数 |
| 新增输入边界 | `ＡＰＩｓＬａｂ` | 保留 | 保留 | 通过；全角规范后保留中间API复数段 |
| B原14项 | `DAILY EMAIL REPORT: CAPITAL SPENDING` | 拒绝 | 拒绝 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `Duquesne CAPITAL investment report` | 拒绝 | 拒绝 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `Update the REPORT tonight.` | 拒绝 | 拒绝 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `We played CHAIR games at the party.` | 拒绝 | 拒绝 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `I said the daily email report covered capital spending.` | 拒绝 | 拒绝 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `Use GenAI and xAI today.` | 保留 | 保留 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `AI API MCP LLM REPO` | 保留 | 保留 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `LOCAL NEWS: RAIN DELAYS CAPITAL CITY TRAINS` | 拒绝 | 拒绝 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `MAIL` | 拒绝 | 拒绝 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `RAIN` | 拒绝 | 拒绝 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `PAID` | 拒绝 | 拒绝 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `AI AGENT` | 保留 | 保留 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `NEW API` | 保留 | 保留 | 通过；复用B原期望，不修改旧断言 |
| B原14项 | `OPENAI` | 保留 | 保留 | 通过；复用B原期望，不修改旧断言 |

共49/49；其中阻断五例5/5、必须保持正例8/8、必须保持反例9/9、新输入边界13/13、B原14项14/14。原14项中8拒绝+6保留，没有把14项全改为拒绝。

M3负对照：完整旧源码和配置复制至 `negative-result.json` 指向的 `/tmp/h3a-m4-m3-negative-*/`，加载相同新增测试中的五条阻断正例，实得 **5F/0E**（不是导入失败或 mock 返回失败）。未就地替换共享源文件。命令与日志：

```sh
../article-importer/.venv/bin/python coordination/evidence/impl-hub-h3a/m4/negative.py
```

`negative.log`、`negative-result.json` 保存每项 F 和准确临时路径。

### B的82项独立探针回归

完整 B 输入原样复制为 `b82-input.json`；未改任何 expected。M3→M4 只有下面五条改变，全部从误杀转为保留；其余77条的实际布尔值不变。

| 输入 | M3 | M4 | 判定 |
|---|---|---|---|
| `OpenAPIs` | 拒绝 | 保留 | 通过，M1已修 |
| `myMCPs` | 拒绝 | 保留 | 通过，M1已修 |
| `newLLMs` | 拒绝 | 保留 | 通过，M1已修 |
| `localREPOs` | 拒绝 | 保留 | 通过，M1已修 |
| `theirAPIs` | 拒绝 | 保留 | 通过，M1已修 |

总计80/82符合B原期望。另两条“昨夜入睡即梦见故乡…”、“这孩子可灵了…”仍保留，属于黄总决定保持的无语境产品组歧义；没有修改探针期望来凑82全绿。`可灵 2.5 出了`、`即梦`同样保留，见 `known-product-rules.json`。完整82条逐项结果及是否改变见 `b82-results.json`。

### 七天2079条：M3→M4逐条差异

冻结输入复用 M3 的 `pool.json` 与同一份 `interest-topics.json`，不是重新采集、也不是拿当前增长后的输入作七天基线。逐ID比较完整返回对象（visible、reason、marked_only），不是只比统计。2079条、2079个唯一ID，每条均一致；以下每一天两个方向都无差异，因此没有可列出的变更ID。完整逐条前后结果在 `verdicts.json`，按天双向清单在 `seven-days-deltas.md`。

| 日期 | 总数 | M3隐藏 | M4隐藏 | M3保留→M4隐藏逐项 | M3隐藏→M4保留逐项 |
|---|---:|---:|---:|---|---|
| 2026-10-24 | 312 | 75 | 75 | 无 | 无 |
| 2026-10-25 | 301 | 73 | 73 | 无 | 无 |
| 2026-10-26 | 393 | 97 | 97 | 无 | 无 |
| 2026-10-27 | 196 | 57 | 57 | 无 | 无 |
| 2026-10-28 | 361 | 102 | 102 | 无 | 无 |
| 2026-10-29 | 327 | 86 | 86 | 无 | 无 |
| 2026-10-30 | 189 | 43 | 43 | 无 | 无 |

合计隐藏 533/2079，保留 1546；本次冻结真实输入没有因混排缩写复数改变闸门结果，也没有任何其他类别变化。此处“无变化”不把此前7条未知变成语义正确；旧未知及产品覆盖边界继续按 M3 记录。

```sh
../article-importer/.venv/bin/python tests/h3a_m4_replay.py --out coordination/evidence/impl-hub-h3a/m4
```

### Python和旧隔离浏览器

```sh
../article-importer/.venv/bin/python -m unittest tests.test_today_h3a_m4 tests.test_today_h3a_m3 tests.test_today_h3a_m1 tests.test_today_h3a_m2 tests.test_today_h3a tests.test_today_view tests.test_interest_channel tests.test_linuxdo_source -v
```

289/289（原240+新增49），日志 `targeted.log`。最初仅选 H3a 模块的快速子集220也通过；最终门槛使用包含 today_view、interest_channel、linuxdo_source 的289项，不把220当成覆盖原240。

旧隔离真实浏览器：today_page 56/56、today_t5 22/22、interest_channel 22/22，共100/100。正式旧测试未改；新 /tmp 副本使用正式只读源码 + 自有 /tmp 工作区、随机端口、独立 profile；输出路径统一改到新临时目录，没有覆盖旧截图。准备/运行原文 `isolated-e2e.py`，目录与各退出码 `old-e2e-results.json`，日志 `today_page-old-e2e.log`、`today_t5-old-e2e.log`、`interest_channel-old-e2e.log`。

### 批准、自然空档和一次重启

主控通过本Dispatch的 `orca orchestration ask` 明确批准：旧E2E全绿、全部任务running=false、最近next_run≥300秒，`com.servasyy.article-studio`只kickstart一次；不取消任务。完整批准原文 `approval.json`。

最初09:37:22Z观察到 github_clues 在运行，未重启；它自然结束后进入空档。重启前 `2026-10-30T09:38:31.600370+00:00` 保存全部 **13项任务（其中9项启用）** 的id/kind/enabled/running/next_run/last_run安全字段，而非仅聚合数字；所有running=false，最近next_run还剩 **416.355655秒**。

实际一次kickstart UTC **2026-10-30T09:38:31.600795+00:00**，PID **33302→35891**；就绪UTC `2026-10-30T09:38:36.305671+00:00`，`health.ok=true`、`running_count=0`，无新增interrupted/aborted。`restart.py` 在写入重启意图后禁止再次执行，完整命令、前后预检及health在 `restart.py`、`restart-preflight.json`、`restart-postflight.json`、`restart.json`。此UTC已另外发 status 通知主控用于R3-0的24h见证判读，不宣称因此免除任何实际失败。

### 重启后真实8766：29/29

仅在新PID、health正常后执行 `node tests/e2e/today_h3a_m4.cjs --real`；该脚本使用新 /tmp headless Playwright profile，同源GET，只读核对页面与当前源码离线回放。复用未改的 `tests/h3a_m3_live.py --out <m4证据目录>`，输出落本轮目录。结果29/29，unknown=0、errors=0；实时来源计数与回放一致。

| 来源 | 输入 | 显示 | 已过滤 |
|---|---:|---:|---:|
| AIsa 推特 | 12 | 11 | 1 |
| Grok | 25 | 25 | 0 |
| 视频榜 | 1 | 1 | 0 |
| linux.do | 64 | 28 | 36 |
| AIsa 51人原始全量 | 225 | 171 | 54 |

AIsa全量比例24.0%；候选与原始采样分别按ID出现次数精确匹配，金融硬例两组各一次；三条linux.do硬例当前输入各一条，黑名单原因及DOM隐藏均通过。Kimi、豆包七天真实正例仍不被隐藏。1440/390无横向溢出。截图 [1440计数](evidence/impl-hub-h3a/m4/real-1440-counts.png)、[390计数](evidence/impl-hub-h3a/m4/real-390-counts.png)、[候选组](evidence/impl-hub-h3a/m4/real-1440-candidates.png)、[原始采样组](evidence/impl-hub-h3a/m4/real-1440-aisa-raw.png)；逐项结果 `real-e2e-results.json`、同口径计数 `live-parity.json`。

111条真实素材逐行SHA前后不变，无增删；保护的feedback、兴趣方向及library-export文件SHA也不变，见 `protected-data-result.json`。没有触发导出、写反馈、点击真实收藏或任务立即运行。

### 该工人已知弱点与逐条自测

| 弱点 / 约束 | 结果 |
|---|---|
| 整条链路 / 修一处坏另一处 | 通过：必须保持的17项及原14探针逐项结果已列；五个新正例在M3旧副本5F/0E，现版5/5；B82只有指定5项变化，其余77不变；原240+新49、旧浏览器100及真实29全通过 |
| 输入边界 | 通过：新增13个边界含中间复数、数字后缀、全角、HTTPs、newMAILs、myRAPIDs、myAPIslab等；每条给期望与理由；没有添加五个回归词的白名单 |
| 单日样本调词 / 跨日误杀 | 通过：固定2079条逐ID及完整gate返回对比，七天双向差异逐天均为无，无其他类别变化 |
| 产品边界不擅改 | 通过：today_filter_config SHA不变；即梦、可灵按裁定裸词保持，2条日常误放明确记既有L；7未知、Muse bot、OpenPencil不扩词 |
| 调度与部署 / 历史L3 | 通过：批准后等自然空档，仅一次重启并给全部任务安全字段、新旧PID/UTC/health；本轮未读取、打印或检索输出任何plist内容，未调用launchctl print、ps eww或env；历史M3的L3不从旧报告撤销 |
| 范围与留痕 | 通过：开工状态、7文件SHA及清理单保护文件SHA留存；正式源文件只动指定辅助函数，旧断言0改动、清理单文件不动；证据新目录；不提交不推送 |
