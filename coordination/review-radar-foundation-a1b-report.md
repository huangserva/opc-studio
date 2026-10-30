# A1-b 雷达地基只读复验报告

## 决定要点

| 项 | 结论／证据 |
|---|---|
| 结论与风险 | **【通过】，可合入；0H / 0M / 1L。** 上轮阻断项全部关闭；L1 是历史冒烟缺少请求级计时与版本指纹，无法事后精确区分 AIsa／代理节点。未部署。 |
| 包与 SHA | **14/14 文件＋补丁 SHA 一致**；补丁 `96f4a1e30d96d4d2499699a9c3117b146657c68810471ef00dcbd478fd0daf86`；当前正式源码新副本应用／编译／测试后反向检查均通过。 |
| 问题关闭 | H1-a/b/c、M9、M10、M2、L2、类型溯源均关闭；公开 bundle 内嵌 manifest 为权威，partial 算覆盖成功、pages_complete=false。 |
| 测试数字 | 原29＋新16 **45/45**；本人原探针批准适配后 **45/45**；page.cjs **4/4**、页面 **35/35**；本轮新增独立反例 **9/9**。 |
| 批准适配 | 仅 K 四阶段语义、原29 deadline 状态断言、JSON default=str 三项；其他断言不变。另跑旧 K 断言版为32/36，四项均为已批准变更的旧语义，未隐瞒。 |
| 8次超时与对照 | 原首批8号均为网络读取 TimeoutError，非整轮 deadline；紧挨两轮 Pi **51/51、54次、21.608秒**，A1-b **51/51、54次、21.213秒**；两版每次实际 timeout 均20秒，未见截短／并发计时回归。历史具体慢点仍不能断言。 |
| 重试建议 | 建议另单增加“每号最多一次、共享剩余整轮预算”的瞬态网络失败重试；本轮无重试、仅授权两轮共108次，未超预算。不要重试鉴权／限频／业务或形状错误。 |
| B1与合入前后 | 合入前无代码阻断；B1须适配权威 snapshot_file/SHA、radar-1、finished_at、对象名单、平铺作者、kind、empty/partial 状态及计数，之后完成正式调度联调；不得以目录最新快照或派生索引代替权威读源。 |

## 对象、隔离与文件核验

复核 Task `task_b4ed4e9ea7db`，Dispatch `ctx_1a9b8343dd99`；对象 `/tmp/radar-a1b-deliver/`，实施方 term_2619a6ec。先完整阅读本单合同、返修合同、初始 A1 合同／报告、本人前轮报告和独立脚本；真实调用遵守本地 [aisa-api/SKILL.md](/Users/huangzongning/.codex/skills/aisa-api/SKILL.md) 的凭据、目的地址及输出约束。

证据根目录：`/tmp/review-radar-a1b-p8kifgks/`。从当前正式工作区复制源码，排除 workspace、Git、依赖缓存、coordination 与浏览器 profile，在 `article-studio/` 一次应用累计补丁；没有就地替换正式文件。Pi 对照副本 `pi-article-studio/` 使用同一当前工作区基础，再逐字节覆盖 Pi 原交付的13文件。正式目录仅新增本报告；未重启／访问 8766，未用 computer use，未修改 B1/B2 或页面。浏览器为独立 headless Playwright context、随机回环端口、拦截外站。

`setup.json`、`final-validation.json` 保存 apply --check → apply → 13份 Python py_compile → 定向验证 → apply -R --check 的退出码、完整 SHA 与范围；测试后14文件 SHA 再次一致。初次复制遇旧 `shot/` 浏览器 profile 的失效链接，随后新建副本并排除 profile，未把失败复制目录用于验收。

| 文件 | SHA256 |
|---|---|
| scripts/refresh_twitter_radar.py | `4680b37def299715b8033e941cc9c29f70c9fe47e4e295706b121698e61c9bc9` |
| scripts/radar_common.py | `cf07893e9dadcbcef152855dbdbe9df9037315a64eef4d8a81ddc0e47cbf8120` |
| scripts/radar_client.py | `bc1fd5fc1b443a2ccd6e3e121834ce526f4401a0261c884376e481de4db0c026` |
| scripts/radar_collect.py | `4d4fc352c64844391463f63132d0e026b59284065124dab907bb00643e435958` |
| scripts/radar_bundle.py | `69b96326dacc43c035e65981e4cab1207b3f7faa7a8a5adf498cd17d11e7fed5` |
| scripts/radar_store.py | `28a69151faa73c3e2fac789503d443b56f4d84ac5962eee672b481eca8116fa2` |
| scripts/radar_retired.py | `fb6dcc9c989fa896486f644069f83ae4a275e48bfc6bba8abb916cdfc906fde0` |
| scripts/radar_targets.json | `9d8295ef6941a83e94de5bf7708f924e84ae3ac704021369c57b56164d22ba8b` |
| scripts/sync_all_radar_live.py | `ec1b4fd6a493f0ad952b099771bc18bcb8ac2ed88e9cebdce2e1f58968c30074` |
| scripts/build_fresh_radar_bundle.py | `c14fd8c887ea70772bae7c9955fb21f71f104e7c85a41e71097e91e041f7fe1d` |
| scripts/batch_scan_150_followings.py | `b09bcc4345649c8de6f588a1f605b25025b3fb9f31d3384cb76ffef066c4dd60` |
| scripts/deep_scan_batch_2.py | `28cb33b6fd34f192940c79ab647228e2192f4dc1f4bcd3bd346909c80fded479` |
| tests/test_twitter_radar_foundation.py | `10d159c04cdb98760724a8f7ad47f75d12fb6fea1962777d9c3f2fdff5041f79` |
| tests/test_twitter_radar_a1b.py | `8268244bef10af7dd5f11b736f5b9b9c7cadd972ddeb07a176fa1b4ffcb08769` |

名单、四支停用脚本与 radar_retired.py 均保留 Pi 版本。本单复用上一轮已关闭的调用影响面证据，不无故重开；本轮原 extra 探针中的停用脚本验证也通过。

## 本人原脚本与三处批准适配

使用本人 `/tmp/review-radar-a1-dzz49m8e/` 的原件复制到本次 `probes/`，从原件构造批准适配版本；没有直接运行实施方改过的副本。脚本以自身目录定位测试代码和证据，因此为新目录建了源码链接／路径替换。原件 SHA：

- independent.py：`7c3bd26a525f3710e0ddfb0eec604382366a1ae2620323d401326c0f431ef237`
- extra.py：`7e0f696d2ac0cd77e5abbd085d77b0d896bc0f2504fa7bc61cfef77ead96d702`
- business_false_pipeline.py：`9d58c1258ec641120d78b9e3bef1112b25734f17a416166aefcb5624cc4f991d`
- page.cjs：`f72af91cdb79d31bc77ee1200ee62a7ea781af3858a14bbfa2da3821268fc93f`

逐段比对结果：

1. **K 四阶段**：改动只在 killed() 函数。前三个替换前仍强制公开包和派生索引均为旧字节；四阶段都验证公开包内嵌 run_id、磁盘快照存在及 SHA 一致。manifest 阶段允许公开包已提交新版、派生索引仍旧；允许被杀当下出现 tmp。新增下一独立进程完整 no-publish 轮，强制临时文件清零、派生索引修复、上一成功快照来自公开引用。这保持了批准后的要求，并增加恢复检查。
2. **原29 deadline 断言**：仅一个 hunk。保留 deadline_hit、success、failed 原检查；将旧 `any(status=="deadline")` 替换为同时存在 deadline_inflight／not_started、全部 ok=false、failed 等于这些账号数量、api_calls=1、attempted=1。比旧断言更强。
3. **default=str**：只改结果 JSON 序列化，不改 case 判断。

程序化验证：从本人原文仅替换 K 函数和序列化后，与实施方对应全文一致；extra/business 两份交付脚本与本人原件逐字节相同；page.cjs 仅 BASE 路径不同，其余全文相同。`independent-delivery.diff`、`original29-adaptation.diff`、`final-validation.json` 留证。

另在 `raw-probes/` 仅允许输出序列化适配、保留全部旧业务断言实跑：**36中32通过，恰好 K-snapshot/K-bundle/K-public/K-manifest 四个旧语义失败**。批准适配版 **36/36**，不是把旧断言宣称为完全未改。extra 初跑缺历史 real-snapshot.json 夹具，补入本人原先留档后8/8，未改断言或产品。

## 测试与问题逐条关闭

| 分组 | 本次结果／证据 |
|---|---|
| 原29＋实施新增16 | 45/45，0.960秒；directed45.log |
| 本人主探针 | 批准适配后36/36；independent36.log、probes/evidence/independent-results.json |
| 本人补充基线／类型／停用 | 8/8；extra8.log |
| 本人51号全业务失败链 | 1/1：success=0、failed=51、api_calls=51、published=false、入口exit=1；false1.log |
| 本人 page.cjs | 4/4，pageerror=0、外网=0；page4.log、probes/evidence/page-results.json |
| 既有页面套件 | 35/35，pageerror=0、console.error=0、外网=0；page35.log、page35/results.json |
| 本轮新独立探针 | 9/9，0.979秒；new-probes.log、new-observations.json |

| 问题 | 关闭依据 |
|---|---|
| H1-a 业务／形状 | success:false、非零数字／字符串 code、缺失／null／非数组 tweets 均拒绝；明确 [] 才空成功。本人51号链未发布，旧公开包／索引保留。 |
| H1-a partial | 首页保留、第二页失败为 partial，pages_complete=false；本次完整入口再验 run.partial=1、success=1、coverage=1、complete=true、2次调用、快照20帖，公开 coverage.partial=1。该口径已获合同明确批准。 |
| H1-b 权威切换 | 公开包内嵌段为唯一权威；快照→版本包→公开包→派生索引。下一轮不从过时 manifest 找 previous；SHA／路径／run_id 校验失败返回 None。 |
| H1-c 崩溃恢复 | 本人四阶段真 SIGKILL，均 child=-9；被杀当下允许 tmp，下一完整轮 tmp=0，索引等于公开权威信息；manifest阶段正确继承已经提交的新run。 |
| M9 截止／计数 | 本人 D01 自行退出、D02保留调用数；新增真实进程 deadline=.08秒，0.1439秒自行exit=1，api_calls=1，失败状态为在途／未开始；退出后再等.7秒无延迟写文件。活进程中的迟返结果也不改已返回run或磁盘。 |
| M10 清理 | 日期目录／文件符号链接与外部文件均保护；北京时间自然日30天边界通过，不宣称精确30×24小时。 |
| M2 粉丝 | Audit本人=500，Other／转推／缺明确作者不覆盖，未知保持null；原归属反例通过。 |
| L2 采集时间 | rebuilt bundle 的 collected_at=finished_at；真实隔离浏览器回执显示13:50，而生成时间为14:47，不再混用。 |
| 类型溯源 | 最终版真实1080帖全部有最小 type_evidence；original461／quote486／retweet133，字段仅布尔／有界ID／null，无嵌套正文／凭据。只证明判别输入留存与内部一致，不声称外部独立认证全部真实类型。 |

本轮新探针 #04–#08 专查公开包引用快照被删／被改：load_previous_snapshot 均拒绝坏证据；下一失败轮 exit=1、公开包字节不变；下一成功轮可发布合法新包，previous_run_id=null，不继承污染正文。故不会从派生索引或目录其他快照悄悄选旧数据。它不会自动修复丢失的历史原始快照；下一成功轮之前页面仍展示旧的公开派生内容，这是保留上次公开版本的既定行为。

## 真实冒烟8次超时：证据、归因与不足

### 原留档能确认什么

run `20261015-082418-f618d977`，08:24:18.147Z→08:24:57.594Z，39.447秒；43/51，54次调用，8次失败，deadline_seconds=600、deadline_hit=false，正确不发布。8个失败**恰好是名单首批8个并发号**：gkxspace、lxfater、dotey、Khazix0918、vista8、gefei55、lijigang、xiaohu。

各号错误均为 `TimeoutError: The read operation timed out`，不是 deadline_inflight/not_started，也不是业务错误。可定位到网络读取未及时完成；不能把该信息直接等同于 AIsa 服务端慢，更不能据此认定新计时算法错误。原 run／日志没有每请求耗时、实际 timeout 或 DNS/TLS/响应头阶段信息，没有足够数据复原具体慢点。

另一个留档差异：原8个失败账号 pages_complete=true，而最终交付 client 的首页失败返回 false（本次离线直接验证）；因此旧冒烟也不能单独当作最终14文件字段的完整验收证据。本轮对最终校验通过的源码重新真实采集，补齐了这一证据。历史源码指纹不足，无法再重建当时的精确中间版本。

### 授权的两轮紧邻对照

仅各一轮，没有重试和第三轮，合计 **108次真实AIsa调用**。Key 在子 shell 从指定 plist 读入环境，仅经鉴权头使用，未写代码／参数／证据；两轮输出的完整Key内存扫描均0命中。请求目的限定官方 AIsa origin；Pi版补装拒绝跳转 opener，仅收紧凭据保护，不改变请求 timeout／队列／分页；两版均记录 DNS、TCP、TLS和响应头阶段，未保存请求头或原始错误正文。

| 指标 | Pi原版A1 | A1-b最终版 |
|---|---|---|
| run_id | 20261015-084417-50ae1296 | 20261015-084439-5bc38405 |
| UTC起止 | 08:44:17.573→08:44:39.088 | 08:44:39.254→08:45:00.365 |
| 执行墙钟 | 21.6081秒 | 21.2130秒 |
| 账号／调用／帖子 | 51/51、54次、1080帖 | 51/51、54次、1080帖 |
| 超时／异常 | 0／0 | 0／0 |
| 实际传入请求timeout | 全部20.0秒 | 全部20.0秒 |
| 请求中位数／P95／最大 | 2.953／3.639／3.670秒 | 2.982／3.660／3.971秒 |
| 截止／并发 | 600秒／8 workers | 600秒／8 workers |
| 发布 | 副本内发布，exit=0 | 副本内发布，exit=0 |

Pi 完成到 A1-b 开始约 **0.166秒**，环境相同；本机确有代理环境变量，两轮记录仅保存变量名，不保存值。原失败8号本次在Pi为2.336–3.478秒、A1-b为1.756–3.302秒，均成功。

**归因判断：没有证据支持“新代码把请求timeout截短、并发计时错误或整轮截止计算错误”。** 当前最终版实际每次20秒、整轮未近600秒，和Pi同级表现；默认值两版相同，剩余预算裁剪专项也通过。历史8次已确认是传输读取超时，首批集中及本次两版恢复更支持当时链路／上游瞬态异常；这是基于对照的判断，不是对历史 AIsa 服务端节点的直接证明。**AIsa端、代理隧道或其他读取阶段的进一步区分，留档不足，不能诚实地断言。**

L1 为这项历史诊断可观测性不足，非当前代码阻断。建议后续run保留：每页发起／结束时刻、实际timeout、耗时、异常类、必要的安全网络阶段及运行源码SHA；不记请求头／凭据。`historical-smoke-analysis.json`、`live-comparison.json`、`live/{pi,a1b}-requests.jsonl`／summary／execution、live-pi.log、live-a1b.log 留证。

### 一次重试是否必要

**建议增加，但另行实施与验收。** 51号、95%门槛至少需要49号成功，只容许2号失败；一次首批8号网络波动即可使整轮失去发布资格。当前修复保证如实失败，不等于采集可用性已足够稳定。

建议仅对 timeout／连接重置等瞬态传输错误，在每号最多一次、固定并发8、共享剩余整轮绝对预算内，短暂退避并加抖动后重试；每次timeout仍取 min(20秒, 剩余预算)，剩余不足就不发。首页成功而第二页失败时只重试失败页，不重抓成功页；重试发起即计api_calls，仍失败则保留partial／失败真实状态。不要因本次3秒左右样本而再缩短20秒timeout，不重试401/403/404/429、鉴权／限频／业务拒绝或形状错误。

该策略不能保证恢复同源相关故障。旧8号若各补一次，最多额外8次；极端51号都失败则额外最多51次，需要下一份合同明确调用预算。本次没有实施重试，也未使用额外真实调用测试建议。

## 给B1的最终字段与读源

| 项目 | A1-b最终字段／B1适配要求 |
|---|---|
| 权威入口 | 读取 `public/twitter-radar-bundle.json.manifest`；相对 workspace/twitter-radar 的 snapshot_file、snapshot_sha256、run_id 为原始快照定位依据，校验路径／SHA／run_id。不得按目录最新或派生manifest选择采样。 |
| manifest字段 | 内嵌 schema_version/run_id/started_at/snapshot_file/snapshot_sha256/bundle_file/public_bundle/coverage/published_at；public_manifest()额外计算bundle_sha256，后者不内嵌自身。manifest.json只是可修复派生索引。 |
| 快照版本／时间 | schema_version=`radar-1`，B1原版要求数字1；结束字段finished_at映射到ended_at。started_at/finished_at/fetched_at/published_at为UTC带时区，不用展示updated_at补造采样时间。 |
| 名单 | targets为含handle/name/track的对象数组；B1原字符串数组读取需显式映射handle，不改变51号固定集合。 |
| 作者／身份 | tweet_id保持字符串；author_handle/author_handle_normalized/author_user_id平铺，映射B1 author.handle/user_id；queried_handle另保留查询归属，不能替代真实作者。 |
| 类型／溯源 | kind映射type，original/quote/retweet/reply；type_evidence为最小审计字段，referenced_tweet_id保留引用关系。 |
| 账号状态 | ok、empty、partial均为覆盖成功；partial的ok=true但pages_complete=false，run.partial单列且已含于success；B1须适配empty与partial，否则误算覆盖率或把有效首页帖误标旧。 |
| 截止／计数 | deadline_inflight和not_started均失败，deadline_hit=true不发布；attempted只统计已发起账号，api_calls含在途和翻页，不能造齐51；run.success+failed=target_count。 |
| 指标／旧值 | 数值或null，账号followers含来源／采样时刻；fetched_at和is_stale按采样证据保留，不从页面更新时间“刷新”旧值。原始快照是全量采样，公开bundle帖子是过滤／限量展示，不能互换。 |
| 展示时间 | collected_at=finished_at的UTC/Z；generated_at是构包时间；updated_at是采集完成时间转北京时间的展示串。 |

合入前无本单代码阻断。合入后仍须按上述最终接口完成B1适配与唯一采集入口／调度联调，以及项目原有上线门槛；不能因两轮真实成功就宣称长期稳定、网络故障已消失或B1无需改动。正式服务、调度、名单及现有数据均未变更。
