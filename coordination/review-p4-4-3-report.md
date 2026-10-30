# 4-3 按领域拆路由独立复核报告

| 决定要点 | 结果 |
|---|---|
| 结论 | **通过**；六项逐条通过，受保护既有失败不增加 |
| H / M / L | **0 / 0 / 0** |
| server.py SHA256 | `9457cf2fe1f7b3f4df5195d58a14521dceb7aa3a8b6bc6787d35f9db1ca64526` |
| 五个模块 SHA256 | `intel_collect_api.py` `6a4cec417ccbaf5ac2b55b201c2497884e9695ad70a3b4f78e536de0033ed4eb`<br>`deals_api.py` `40c9e9eb03b60a974b114b4f11234958acf323221176ce5a7fa2ffda046030a6`<br>`intake_cards_api.py` `9c2ee81142c0bc44186aabb96d673eea0e525010e1e91dd1488c4ba216960205`<br>`feedback_api.py` `0212b7d239c77e81eacb8ed3b0657f84878b27ef629b34b7dc7cafb38796eaae`<br>`schedules_assistant_api.py` `067e2b16589789bc53808ab2b77a069f981f91d8278e313c4a1ffd349c6e17b7` |
| 探针 SHA256 | `tests/server_domain_route_probe.py` `6b4b2f6c7d9a2490c0fc8fcd4e8125900d4f823638981d0f342e25dc5d3f7fb7` |
| 路由与安全 | 210个路径条件、77个搬移分支；116正例+376边界/错误方法，174次403；33MB真实上传2/2 |
| 回归 | Python 3569项，0F/1E/7S，380.187秒；UI 719P/3F/1S，与基线相同 |
| 真实页面与PID | 5页只读操作，0页面异常/控制台错误；PID 32836，未重启 |
| 本复核改动 | 仅本报告；无代码改动、提交、推送 |

任务 `task_bd8256b73d2f`，Dispatch `ctx_d95c7fd40447`；被复核实施任务 `task_0c6345c83d5a`。遵循合同通用规则、G1–G4及4-3，采用主控对generation原留、两条上传原留、assistant-images搬移及显式条件分发的四项裁定。证据根目录：`/tmp/review-p4-4-3/`。

## 1）死区及路由清单：通过

独立脚本 `audit.py` 从开工 `server.py` 的 `Handler.do_GET/_post_routes` 递归抽取全部涉及 parts/segments/route 的路径判断，得到210条（含前缀内的业务分支），清单见本报告附录及 `route-inventory.md`；未直接采信实施者route-plan。逐个比对五模块77个顶层分支：每条分发条件与对应模块条件AST完全相等，模块内顺序相同。`order_diff.py` 将每处分发还原成该处分发条件对应的原业务分支，完整两个方法的AST及全局顺序均恢复相同；两个 `*-order.diff` 为空。对同一模块分布在多处的装配，按该处条件展开，未把一个模块的所有分支重复塞到每处。

复用实施探针的确定性本地业务替身，独立增加47条手写路径，每条GET/POST/PUT共141种组合；与原负例去重后376条边界请求。类别包括尾斜杠、重复斜杠、多一段、少一段、大小写、查询串、百分号编码及错误方法。开工快照与当前隔离副本各用真实ThreadingHTTPServer随机端口发送：116正例+376边界请求，全部状态码、响应正文SHA256、解析内容、关键响应头及业务调用一致；断言全部正文非空，无RemoteDisconnected。PUT按原行为返回501，未将它误判成路由失败。

`route_probe.py`、`routes-before/after.json`及对应日志可复跑；该脚本在/tmp扩展，未修改正式探针。业务替身用于隔离分发验证，未调用采集、模型、AIsa、X、Buffer或R2；真实数据和上传另做实测。负控 `negative_control.py` 在/tmp分别把deals列表200改201、把模块条件改成不可命中，探针均退出1，前者定位正文/状态差异，后者触发dispatch/module deadzone断言。两份mutant日志及 `negative-control.json` 留存。

## 2）安全顺序与长度门槛：通过

源码核对 `_post_routes` 只有一次 Origin/X-Article-Studio校验，位于读取长度、上传及所有子路由之前；完整方法还原AST证明与开工顺序一致。五模块 `rg` 搜索 rfile、Origin、X-Article-Studio以及反向import server均0命中（`module-forbidden-symbols.txt`为空）；GET入口没有新增来源校验。

58条拆出POST动作，每条分别缺Origin、伪造Origin=`http://evil.invalid`、缺X-Article-Studio，合计174次，全部403，实际错误内容为“请求来源不受支持”，业务调用列表全部为空。下表每模块至少2条均包含三种情况：

| 模块 | 已实际覆盖的两个POST示例 | 三类请求结论 |
|---|---|---|
| intel_collect | `/api/collect/aisa/run`、`/api/intel/run` | 全部403、无业务调用 |
| deals | `/api/deals`、`/api/deals/from-clue` | 全部403、无业务调用 |
| intake_cards | `/api/intake/fixture/resolve`、`/api/cards/fixture/confirm` | 全部403、无业务调用 |
| feedback | `/api/feedback/import`、`/api/feedback/observe` | 全部403、无业务调用 |
| schedules_assistant | `/api/schedules/save`、`/api/assistant/send` | 全部403、无业务调用 |

两条intake上传及其他流式上传均仍在通用32,000,000字节检查之前，intake/drop、intake/stage原分支源码逐字保留。随机端口发送33,000,000字节给真实Store/Knowledge/Intake，两条均201、实际落盘各33,000,000字节，SHA256均为 `1b78e591f68b34852047700bc65a73982136270801d55015933f6f995b752754`，与开工实测一致。只关闭ffprobe依赖，没有替换文件读写；不宣称验证视频解码。同长度非上传 `/api/deals` 为413、正文“请求过大或为空”、业务未执行。证据 `real-uploads.json/log`及routes JSON的uploads/too_large。

## 3）逐字搬移与导入：通过

`audit.py` 对五模块77个业务if逐条在开工方法中找唯一完整AST匹配，并去除行首缩进后逐行比较源码，全部相同。没有修改业务条件、状态码、字段、异常处理或调用参数。模块只新增显式handler别名、与原入口一致的segments/query/store局部准备、未命中None；入口遵循已批准的显式条件分发，不依赖reply()的None返回判定命中。

移除搬移/分发节点后两个原方法AST相同，其他Handler方法逐字相同；再按原条件还原77分支后两个完整方法AST相同，证明安全、fallback、generation/video/lab及上传等未搬分支及顺序均保留。五个模块各在独立Python进程中单独import成功，且sys.modules中没有server；无模块反向导入server，无循环。逐项源码位置、SHA与import结果见 `source-scope.json`。

## 4）G4范围、视频与测试保护：通过

开工tracked哈希552份逐文件核对，只有server.py有增量，另551份不变。开工repo中75份可核验源码文件只有server.py变化。新增五模块及一致性探针符合允许范围。

特别核查 `radar_page_api.py`、`video_radar_page_api.py`、`tests/test_radar_page_api.py`、`tests/test_video_radar_page_api.py` 与开工SHA完全相同；它们相对HEAD为M，但本单增量为0，不能把历史脏改算成本单越界。29个视频保护文件逐项SHA全部相同，server内28个generation/video/lab/上传相关if的完整源码另逐字比对，全部相同（protected-branches.json）；完整方法还原也验证顺序不变。

开工tracked哈希覆盖的既有测试无变化；另外与上一轮已验收4-2的隔离测试快照比对335份测试/夹具文件，全部逐字节相同（`previous-tests.json`）。该补充覆盖原来尚未tracked的test_server_split_imports、test_settings_view等；不声称4-3 tracked哈希本身覆盖了这些untracked测试。正式目录源码SHA与复核开工副本最终再次一致。本复核仅新增指定报告。

## 5）S/R4全量门槛：通过

开跑前通过Orca ask登记Python全量独占，主控答复“可以开始，已登记独占。跑完发消息释放”。在/tmp隔离副本执行指定虚拟环境Python和npm test:ui；真实服务没有重启。

| 检查 | 开工基线 | 独立复核 |
|---|---|---|
| Python全量 | 3569项，0F/1E/7S | 3569项，0F/1E/7S，380.187秒 |
| test:ui | 719P/3F/1S | 719P/3F/1S，723项 |

唯一Python错误ID与开工完全相同：`test_multi_segment_sequence_video_builds_director_timeline (test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline)`；异常仍为 `ValueError: 参考素材宽高须为 32 的倍数`，受保护视频既有错误。UI三个失败名称、编号与开工完全相同：

- not ok 563 - 4 幕导演台：初始载入 4 幕卡片且包含默认极客荒岛剧本
- not ok 564 - 4 幕导演台：切换题材预设（赛博追杀）即时同步 4 幕剧本
- not ok 565 - 4 幕导演台：生成单幕 (15s) 采用 Singularity + Turbo 4步 + 345帧参数

最终日志 `python-final.log`、`ui-fixed.log`。补充逐项对照上一轮已验收4-2的完整verbose日志：3569个测试ID及状态全部相同（3561通过、7跳过、1错误），差异0；335份测试/夹具源码也已核对不变，证据test-statuses.json与compare_statuses.py。Python既有ResourceWarning保留，没有隐藏失败。today e2e反例15的北京时间0–8点边界按派工基线记录，本次未修改或额外重跑。

隔离环境修正：首轮副本漏带pi-runtime/node_modules、work/私有coordination夹具，且外部article-importer兼容入口会把真实仓库插入sys.path；发现后中断该轮，保留 `python-incomplete-dependencies-aborted.log`。将兼容入口复制进/tmp并让其相对目录指向本隔离repo，补齐上一轮已验收的私有夹具/依赖；UnderTestCopyTests所在content_markdown模块123项全过，确认模块来自副本；随后完整一轮因还缺微信插件定位配置，实际3356项0F/1E/39S（python-missing-wechat-config.log），补齐上一轮已验收副本的workspace/sources/wechat.json后再完整重跑最终全量。首轮UI缺依赖产生718P/4F/1S，其中多出的错误为缺pi运行依赖，补齐后完整复跑恢复719P/3F/1S；初轮日志保留 `ui.log`，不计为产品回归。未修改测试断言或产品逻辑。

## 6）8766真实只读路径：通过

使用headless Chrome + Playwright独立context，未用computer use、未连接真实Chrome profile；仅允许127.0.0.1:8766的GET，其他方法和外网均拦截。五页真实API未造假/未替换响应，实际blocked列表为空；页面全部HTTP200，实际API无>=400，0 pageerror、0 console.error。已查看五页截图，并逐项核对以下内容：

| 页面 | 实际只读操作与数据 | 结论 |
|---|---|---|
| 商单 | 输入无匹配查询再清除；再切全部商单和表格，34笔/API34条/DOM34行；真实标题“波场生态”在页面存在 | 通过 |
| 情报采集 | 勾选只看当前订阅号再恢复；19篇/API articles19条/DOM19卡片，订阅号“数字生命卡兹克”；首文“开源「活人感写作.skill」，只为帮你写出没有AI味的文字。”真实显示 | 通过 |
| 定时任务 | 全部9/9，筛选已启用5/9，再恢复；API tasks9条；真实任务“创意提炼·每日”“商单分析·Pi”等可见 | 通过 |
| 发布反馈 | 切换数据复盘只读视图；API绑定1项、快照1份；实际展示“H3复刻视频帖·X原始表现（版本待关联）”及平台采集资料 | 通过 |
| 设置 | 点击刷新状态只读按钮；7个真实来源卡片/API7个、9个任务/API9个，有当前模型及真实运行记录 | 通过 |

证据 `pages/results.json`、`pages/g1.json`、五页DOM文本和PNG、`pages.cjs`、`pages-g1.cjs`。初轮诊断把intel响应数组误读为items，以及统计商单默认“正在跟进”范围9条，后续按真实articles字段、全部商单范围分别验证为19、34，原诊断记录未删，不把脚本字段/范围误判成产品问题。

PID两次用 `ps -o pid,lstart -p 32836` 确认仍为32836，启动时间2026-10-30 03:11:37；server.py及五模块mtime均为03:08:24，早于启动。未读取或输出服务环境变量；未点击采集、生成、发布、保存、暂停/恢复等写按钮。

## 通用反例逐项

| 项目 | 结论 | 实际证据 |
|---|---|---|
| G1 内容而非数字 | 通过 | 路由完整正文SHA/调用、真实上传落盘SHA、商单/文章标题与DOM/API计数对应、五页截图 |
| G2 不钻字面漏洞 | 通过 | 按批准显式分发验证77条件及完整顺序；200改201、模块死区两种故意改错均测出 |
| G3 不自行扩围 | 通过 | 四项裁定落实，generation与上传原留；只读复核、仅写报告 |
| G4 范围 | 通过 | 552tracked、75源码、29视频保护、335既有测试/夹具逐文件比对 |

## 附录：独立生成的210条路径条件清单

该清单从开工server.py生成；“server.py”表示条件在入口保留，“模块名”表示完整分支在相应模块可找到。前缀内嵌套的业务条件同时列出，避免只检查外层分发遗漏内层。

|方法|开工行号|路径条件|当前去向|
|---|---|---|---|
|do_GET|1093|`parts.path == '/api/settings/overview'`|server.py|
|do_GET|1094|`parts.path == '/api/settings/logs'`|server.py|
|do_GET|1095|`parts.path.startswith('/api/x-official/')`|server.py|
|do_GET|1098|`len(segments) == 4 and segments[:2] == ['api', 'items'] and (segments[3] == 'draft-status')`|server.py|
|do_GET|1101|`parts.path == '/api/publish/review' or parts.path.startswith('/api/publish/review/')`|server.py|
|do_GET|1104|`parts.path == '/api/items' or parts.path.startswith('/api/items/') or parts.path == '/api/library/search'`|server.py|
|do_GET|1107|`parts.path == '/api/today'`|server.py|
|do_GET|1110|`parts.path == '/api/today/item'`|server.py|
|do_GET|1113|`parts.path == '/api/radar/grok-bot'`|server.py|
|do_GET|1118|`parts.path == '/api/radar/grok-bot/feedback'`|server.py|
|do_GET|1121|`parts.path == '/api/radar/monitor'`|server.py|
|do_GET|1125|`parts.path == '/api/video-radar/page'`|server.py|
|do_GET|1129|`parts.path == '/api/sources/wechat/status'`|server.py|
|do_GET|1131|`parts.path == '/api/sources/wechat/results'`|server.py|
|do_GET|1135|`parts.path == '/api/sources/wechat/radar-quarantine'`|server.py|
|do_GET|1141|`parts.path == '/api/deals'`|deals_api|
|do_GET|1143|`parts.path == '/api/deals/proposals/status'`|deals_api|
|do_GET|1147|`parts.path == '/api/deals/options'`|deals_api|
|do_GET|1149|`parts.path == '/api/deals/review'`|deals_api|
|do_GET|1151|`parts.path == '/api/deals/review/report'`|deals_api|
|do_GET|1156|`parts.path == '/api/intake'`|intake_cards_api|
|do_GET|1161|`parts.path == '/api/intake/analysis-status'`|intake_cards_api|
|do_GET|1172|`len(segments) == 4 and segments[:2] == ['api', 'intake'] and (segments[3] == 'file')`|intake_cards_api|
|do_GET|1174|`len(segments) == 3 and segments[:2] == ['api', 'intake']`|intake_cards_api|
|do_GET|1176|`parts.path == '/api/cards'`|intake_cards_api|
|do_GET|1185|`len(segments) == 4 and segments[:2] == ['api', 'cards'] and (segments[3] == 'prompt')`|intake_cards_api|
|do_GET|1187|`len(segments) == 3 and segments[:2] == ['api', 'cards']`|intake_cards_api|
|do_GET|1189|`len(segments) == 3 and segments[:2] == ['api', 'deals']`|deals_api|
|do_GET|1191|`parts.path == '/api/collect/platforms'`|intel_collect_api|
|do_GET|1193|`parts.path == '/api/collect/aisa/job'`|intel_collect_api|
|do_GET|1195|`parts.path == '/api/collect/status'`|intel_collect_api|
|do_GET|1197|`parts.path == '/api/intel/recommendations'`|intel_collect_api|
|do_GET|1199|`parts.path == '/api/intel/status'`|intel_collect_api|
|do_GET|1201|`parts.path == '/api/intel/subscriptions'`|intel_collect_api|
|do_GET|1203|`parts.path == '/api/intel/jobs'`|intel_collect_api|
|do_GET|1205|`parts.path == '/api/intel/articles'`|intel_collect_api|
|do_GET|1212|`parts.path == '/api/intel/accounts'`|intel_collect_api|
|do_GET|1216|`len(segments) == 4 and segments[:3] == ['api', 'intel', 'articles']`|intel_collect_api|
|do_GET|1218|`len(segments) == 4 and segments[:3] == ['api', 'intel', 'jobs']`|intel_collect_api|
|do_GET|1220|`parts.path == '/api/feedback'`|feedback_api|
|do_GET|1222|`parts.path == '/api/feedback/observations'`|feedback_api|
|do_GET|1224|`parts.path == '/api/feedback/reviews'`|feedback_api|
|do_GET|1226|`parts.path == '/api/feedback/overviews'`|feedback_api|
|do_GET|1228|`len(segments) == 4 and segments[:3] == ['api', 'feedback', 'review']`|feedback_api|
|do_GET|1230|`len(segments) == 4 and segments[:3] == ['api', 'feedback', 'observation']`|feedback_api|
|do_GET|1232|`parts.path == '/api/feedback/snapshots'`|feedback_api|
|do_GET|1234|`len(segments) == 4 and segments[:3] == ['api', 'feedback', 'snapshot']`|feedback_api|
|do_GET|1236|`parts.path == '/api/clues/wechat/status'`|server.py|
|do_GET|1238|`parts.path == '/api/clues'`|server.py|
|do_GET|1240|`len(segments) == 3 and segments[:2] == ['api', 'clues']`|server.py|
|do_GET|1242|`parts.path == '/api/events'`|server.py|
|do_GET|1244|`len(segments) == 3 and segments[:2] == ['api', 'events']`|server.py|
|do_GET|1246|`parts.path == '/api/github'`|server.py|
|do_GET|1248|`parts.path == '/api/knowledge'`|server.py|
|do_GET|1250|`parts.path == '/api/knowledge/search'`|server.py|
|do_GET|1252|`len(segments) == 4 and segments[:3] == ['api', 'knowledge', 'document']`|server.py|
|do_GET|1259|`parts.path == '/api/knowledge/trash'`|server.py|
|do_GET|1261|`len(segments) == 4 and segments[:3] == ['api', 'knowledge', 'dossier']`|server.py|
|do_GET|1263|`len(segments) == 2 and segments[0] == 'chat-attachments' and re.fullmatch('[a-f0-9]{32}\\.png', segments[1])`|schedules_assistant_api|
|do_GET|1265|`len(segments) == 2 and segments[0] == 'generation-references'`|server.py|
|do_GET|1267|`len(segments) == 4 and segments[:3] == ['api', 'generation', 'reference']`|server.py|
|do_GET|1269|`parts.path == '/api/generation'`|server.py|
|do_GET|1272|`parts.path in ('/api/generation/backend-health', '/api/generation/backend-inventory')`|server.py|
|do_GET|1282|`parts.path == '/api/generation/lab'`|server.py|
|do_GET|1287|`parts.path == '/api/generation/lab/aicg3d-presets'`|server.py|
|do_GET|1290|`len(segments) == 4 and segments[:3] == ['api', 'generation', 'job']`|server.py|
|do_GET|1292|`len(segments) == 2 and segments[0] == 'generation-assets'`|server.py|
|do_GET|1294|`len(segments) == 2 and segments[0] == 'generation-thumbnails'`|server.py|
|do_GET|1296|`len(segments) == 3 and segments[:2] == ['api', 'wechat-preview']`|server.py|
|do_GET|1298|`parts.path == '/api/local-publishing/connection'`|server.py|
|do_GET|1300|`parts.path == '/api/local-publishing/jobs'`|server.py|
|do_GET|1302|`parts.path == '/api/local-publishing/preflight'`|server.py|
|do_GET|1304|`len(segments) == 4 and segments[:3] == ['api', 'local-publishing', 'bundle']`|server.py|
|do_GET|1306|`len(segments) == 4 and segments[:3] == ['api', 'local-publishing', 'job']`|server.py|
|do_GET|1308|`parts.path == '/api/publishing/capabilities'`|server.py|
|do_GET|1311|`parts.path == '/api/schedules'`|schedules_assistant_api|
|do_GET|1313|`len(segments) == 4 and segments[:3] == ['api', 'schedules', 'run']`|schedules_assistant_api|
|do_GET|1315|`len(segments) == 4 and segments[:2] == ['api', 'schedules'] and (segments[3] == 'outsourced_prompt')`|schedules_assistant_api|
|do_GET|1318|`parts.path == '/api/schedules/runs'`|schedules_assistant_api|
|do_GET|1332|`parts.path == '/api/assistant/settings'`|schedules_assistant_api|
|do_GET|1334|`parts.path == '/api/assistant'`|schedules_assistant_api|
|do_GET|1336|`parts.path == '/api/assistant/capabilities'`|schedules_assistant_api|
|do_GET|1341|`parts.path == '/api/assistant/ssh'`|schedules_assistant_api|
|do_GET|1345|`parts.path == '/api/assistant/memory'`|schedules_assistant_api|
|do_GET|1349|`len(segments) == 4 and segments[:3] == ['api', 'assistant', 'job']`|schedules_assistant_api|
|do_GET|1351|`len(segments) == 2 and segments[0] == 'assistant-images' and re.fullmatch('[a-f0-9]{32}\\.png', segments[1])`|schedules_assistant_api|
|do_GET|1353|`len(segments) == 3 and segments[0] == 'uploads'`|server.py|
|do_GET|1356|`len(segments) == 2 and segments[0] == 'exports' and re.fullmatch('[a-f0-9]{32}\\.zip', segments[1])`|server.py|
|do_GET|1358|`parts.path == '/api/library'`|server.py|
|do_GET|1360|`len(segments) == 3 and segments[:2] == ['api', 'article']`|server.py|
|do_GET|1362|`len(segments) == 3 and segments[:2] == ['api', 'compare']`|server.py|
|do_GET|1364|`segments and segments[0] == 'assets' and (len(segments) >= 4)`|server.py|
|do_GET|1371|`parts.path in ('/', '/index.html') or parts.path.startswith('/read/')`|server.py|
|do_GET|1342|`parts.query`|schedules_assistant_api|
|do_GET|1346|`parts.query`|schedules_assistant_api|
|_post_routes|1404|`segments == ['api', 'x-official', 'upload']`|server.py|
|_post_routes|1412|`segments == ['api', 'generation', 'reference-upload']`|server.py|
|_post_routes|1421|`len(segments) == 3 and segments[:2] == ['api', 'upload']`|server.py|
|_post_routes|1430|`segments == ['api', 'intake', 'drop']`|server.py|
|_post_routes|1446|`segments == ['api', 'intake', 'stage']`|server.py|
|_post_routes|1463|`segments == ['api', 'today', 'feedback']`|server.py|
|_post_routes|1466|`segments == ['api', 'radar', 'grok-bot', 'feedback']`|server.py|
|_post_routes|1477|`segments == ['api', 'radar', 'feedback']`|server.py|
|_post_routes|1492|`segments == ['api', 'video-radar', 'feedback']`|server.py|
|_post_routes|1505|`segments == ['api', 'collect', 'aisa', 'run']`|intel_collect_api|
|_post_routes|1507|`segments[:2] == ['api', 'x-official']`|server.py|
|_post_routes|1510|`segments == ['api', 'sources', 'wechat', 'run']`|server.py|
|_post_routes|1518|`segments == ['api', 'sources', 'wechat', 'radar-quarantine', 'retry']`|server.py|
|_post_routes|1526|`segments[:2] == ['api', 'deals']`|deals_api|
|_post_routes|1544|`len(segments) == 4 and segments[:2] == ['api', 'intake'] and (segments[3] == 'resolve')`|intake_cards_api|
|_post_routes|1555|`len(segments) == 4 and segments[:2] == ['api', 'intake'] and (segments[3] == 'analyze')`|intake_cards_api|
|_post_routes|1560|`len(segments) == 4 and segments[:2] == ['api', 'intake'] and (segments[3] == 'delete')`|intake_cards_api|
|_post_routes|1566|`segments == ['api', 'intake', 'delete-preview']`|intake_cards_api|
|_post_routes|1571|`segments == ['api', 'intake', 'delete-many']`|intake_cards_api|
|_post_routes|1576|`len(segments) == 4 and segments[:2] == ['api', 'cards'] and (segments[3] in ('confirm', 'reject', 'restore'))`|intake_cards_api|
|_post_routes|1581|`len(segments) == 4 and segments[:2] == ['api', 'cards'] and (segments[3] == 'note')`|intake_cards_api|
|_post_routes|1585|`len(segments) == 4 and segments[:2] == ['api', 'cards'] and (segments[3] == 'delete')`|intake_cards_api|
|_post_routes|1589|`segments == ['api', 'cards', 'cleanup']`|intake_cards_api|
|_post_routes|1593|`segments == ['api', 'intel', 'recommendations', 'scan']`|intel_collect_api|
|_post_routes|1601|`len(segments) == 4 and segments[:3] == ['api', 'intel', 'recommendations']`|intel_collect_api|
|_post_routes|1618|`segments[:2] == ['api', 'intel']`|intel_collect_api|
|_post_routes|1643|`segments == ['api', 'github', 'connect']`|server.py|
|_post_routes|1645|`segments == ['api', 'github', 'source']`|server.py|
|_post_routes|1647|`segments == ['api', 'github', 'run']`|server.py|
|_post_routes|1657|`segments == ['api', 'feedback', 'import']`|feedback_api|
|_post_routes|1659|`len(segments) == 5 and segments[:3] == ['api', 'feedback', 'review'] and (segments[4] == 'notes')`|feedback_api|
|_post_routes|1661|`len(segments) == 5 and segments[:3] == ['api', 'feedback', 'review'] and (segments[4] == 'knowledge')`|feedback_api|
|_post_routes|1663|`segments == ['api', 'feedback', 'overview']`|feedback_api|
|_post_routes|1667|`segments == ['api', 'feedback', 'observe']`|feedback_api|
|_post_routes|1669|`len(segments) == 5 and segments[:3] == ['api', 'feedback', 'observation'] and (segments[4] == 'knowledge')`|feedback_api|
|_post_routes|1671|`len(segments) == 5 and segments[:3] == ['api', 'feedback', 'observation'] and (segments[4] == 'bind')`|feedback_api|
|_post_routes|1673|`len(segments) == 5 and segments[:3] == ['api', 'feedback', 'snapshot'] and (segments[4] == 'knowledge')`|feedback_api|
|_post_routes|1675|`segments[:2] == ['api', 'clues']`|server.py|
|_post_routes|1700|`segments == ['api', 'events'] or (len(segments) == 4 and segments[:2] == ['api', 'events'] and (segments[3] == 'actions'))`|server.py|
|_post_routes|1707|`segments == ['api', 'knowledge', 'trash', 'preview']`|server.py|
|_post_routes|1709|`segments == ['api', 'knowledge', 'trash', 'restore']`|server.py|
|_post_routes|1711|`segments == ['api', 'knowledge', 'trash']`|server.py|
|_post_routes|1713|`segments == ['api', 'knowledge', 'sync']`|server.py|
|_post_routes|1715|`segments == ['api', 'knowledge', 'wechat', 'capture']`|server.py|
|_post_routes|1717|`segments == ['api', 'knowledge', 'source']`|server.py|
|_post_routes|1719|`segments == ['api', 'knowledge', 'gmail', 'import']`|server.py|
|_post_routes|1721|`segments == ['api', 'knowledge', 'project']`|server.py|
|_post_routes|1723|`segments == ['api', 'knowledge', 'note']`|server.py|
|_post_routes|1725|`len(segments) == 4 and segments[:2] == ['api', 'items'] and (segments[3] == 'draft')`|server.py|
|_post_routes|1728|`segments == ['api', 'items'] or segments[:2] == ['api', 'items']`|server.py|
|_post_routes|1731|`segments == ['api', 'knowledge', 'reference']`|server.py|
|_post_routes|1752|`segments == ['api', 'knowledge', 'dossier']`|server.py|
|_post_routes|1754|`segments == ['api', 'knowledge', 'draft']`|server.py|
|_post_routes|1756|`segments == ['api', 'assistant', 'attachments']`|schedules_assistant_api|
|_post_routes|1758|`segments == ['api', 'generation', 'preferences']`|server.py|
|_post_routes|1760|`segments == ['api', 'generation', 'start']`|server.py|
|_post_routes|1762|`segments == ['api', 'generation', 'references']`|server.py|
|_post_routes|1764|`segments == ['api', 'generation', 'reference-from-job']`|server.py|
|_post_routes|1768|`segments == ['api', 'generation', 'manage']`|server.py|
|_post_routes|1770|`segments == ['api', 'generation', 'lab', 'start']`|server.py|
|_post_routes|1772|`segments == ['api', 'generation', 'lab', 'concat']`|server.py|
|_post_routes|1774|`segments == ['api', 'generation', 'lab', 'aicg3d-compile']`|server.py|
|_post_routes|1785|`segments == ['api', 'generation', 'lab', 'aicg3d-parse']`|server.py|
|_post_routes|1792|`segments == ['api', 'generation', 'health']`|server.py|
|_post_routes|1794|`len(segments) == 4 and segments[:3] == ['api', 'generation', 'save']`|server.py|
|_post_routes|1796|`len(segments) == 5 and segments[:3] == ['api', 'generation', 'job'] and (segments[4] == 'cancel')`|server.py|
|_post_routes|1800|`len(segments) == 5 and segments[:3] == ['api', 'generation', 'job'] and (segments[4] == 'reconcile')`|server.py|
|_post_routes|1804|`segments == ['api', 'schedules', 'save']`|schedules_assistant_api|
|_post_routes|1806|`segments == ['api', 'schedules', 'batch']`|schedules_assistant_api|
|_post_routes|1808|`len(segments) == 4 and segments[:2] == ['api', 'schedules']`|schedules_assistant_api|
|_post_routes|1814|`segments == ['api', 'assistant', 'settings']`|schedules_assistant_api|
|_post_routes|1816|`segments == ['api', 'assistant', 'select-model']`|schedules_assistant_api|
|_post_routes|1818|`segments == ['api', 'assistant', 'test-settings']`|schedules_assistant_api|
|_post_routes|1820|`segments == ['api', 'assistant', 'send']`|schedules_assistant_api|
|_post_routes|1822|`len(segments) == 5 and segments[:3] == ['api', 'assistant', 'workflows'] and (segments[4] == 'save')`|schedules_assistant_api|
|_post_routes|1824|`segments == ['api', 'assistant', 'stop']`|schedules_assistant_api|
|_post_routes|1826|`segments == ['api', 'assistant', 'new-chat']`|schedules_assistant_api|
|_post_routes|1828|`segments == ['api', 'local-publishing', 'connection']`|server.py|
|_post_routes|1830|`len(segments) == 4 and segments[:3] == ['api', 'local-publishing', 'start']`|server.py|
|_post_routes|1832|`len(segments) == 4 and segments[:3] == ['api', 'local-publishing', 'adapt']`|server.py|
|_post_routes|1834|`len(segments) == 4 and segments[:3] == ['api', 'local-publishing', 'job']`|server.py|
|_post_routes|1836|`len(segments) == 3 and segments[:2] == ['api', 'publication-plan']`|server.py|
|_post_routes|1840|`len(segments) == 4 and segments[:3] == ['api', 'buffer', 'channels']`|server.py|
|_post_routes|1842|`len(segments) == 4 and segments[:3] == ['api', 'buffer', 'submit']`|server.py|
|_post_routes|1844|`len(segments) == 4 and segments[:3] == ['api', 'buffer', 'status']`|server.py|
|_post_routes|1846|`len(segments) == 4 and segments[:3] == ['api', 'buffer', 'media']`|server.py|
|_post_routes|1849|`len(segments) == 3 and segments[:2] == ['api', 'export']`|server.py|
|_post_routes|1851|`len(segments) == 3 and segments[:2] == ['api', 'record']`|server.py|
|_post_routes|1853|`segments == ['api', 'collection']`|server.py|
|_post_routes|1855|`segments == ['api', 'create']`|server.py|
|_post_routes|1857|`segments == ['api', 'scan']`|server.py|
|_post_routes|1860|`len(segments) == 3 and segments[:2] == ['api', 'save']`|server.py|
|_post_routes|1862|`len(segments) == 3 and segments[:2] == ['api', 'reflow']`|server.py|
|_post_routes|1864|`len(segments) == 3 and segments[:2] == ['api', 'markdown-signals']`|server.py|
|_post_routes|1866|`len(segments) == 3 and segments[:2] == ['api', 'restore']`|server.py|
|_post_routes|1809|`segments[3] == 'publish'`|schedules_assistant_api|
|_post_routes|1811|`segments[3] == 'cancel'`|schedules_assistant_api|
|_post_routes|1530|`segments == ['api', 'deals']`|deals_api|
|_post_routes|1531|`segments == ['api', 'deals', 'from-clue']`|deals_api|
|_post_routes|1532|`segments == ['api', 'deals', 'apply-proposal']`|deals_api|
|_post_routes|1533|`len(segments) == 4 and segments[3] == 'scan-wechat'`|deals_api|
|_post_routes|1536|`len(segments) == 4 and segments[3] == 'evidence'`|deals_api|
|_post_routes|1537|`segments == ['api', 'deals', 'import-initial']`|deals_api|
|_post_routes|1540|`len(segments) == 4 and segments[3] in ('receipt', 'deliverable', 'publication', 'publication-edit', 'void')`|deals_api|
|_post_routes|1621|`segments == ['api', 'intel', 'subscriptions']`|intel_collect_api|
|_post_routes|1623|`segments == ['api', 'intel', 'identify']`|intel_collect_api|
|_post_routes|1627|`segments == ['api', 'intel', 'run']`|intel_collect_api|
|_post_routes|1629|`segments == ['api', 'intel', 'unsubscribe']`|intel_collect_api|
|_post_routes|1633|`len(segments) == 5 and segments[:3] == ['api', 'intel', 'articles'] and (segments[4] == 'intake')`|intel_collect_api|
|_post_routes|1678|`segments == ['api', 'clues']`|server.py|
|_post_routes|1679|`segments == ['api', 'clues', 'import-event']`|server.py|
|_post_routes|1680|`segments == ['api', 'clues', 'from-run']`|server.py|
|_post_routes|1681|`segments == ['api', 'clues', 'wechat', 'run']`|server.py|
|_post_routes|1696|`len(segments) == 4 and segments[3] == 'seen'`|server.py|
|_post_routes|1697|`len(segments) == 4 and segments[3] == 'promote'`|server.py|
