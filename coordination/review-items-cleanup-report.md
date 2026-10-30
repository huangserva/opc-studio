# 素材库清理 + E2E 写入防护复核

| 决定要点 | 结果 |
|---|---|
| 结论 | **不通过**：清理链路通过，测试防护仍有文件截断绕过；本单未修代码 |
| H / M / L | **0H / 1M / 1L**；M=数字open标志遗漏O_TRUNC；L=实施备份方法缺历史留档 |
| 数据 | 改动恰为指定39 ID，仅trashed_at改变；其余68行SHA不变；清理/浏览器核对时107/活跃67/回收40；末次111=旧107不变+3正常新增+1外部探测观察 |
| SHA / 范围 | 11/11测试SHA一致；本单patch只涉及允许测试，run.cjs仅3处接线；按主控给定mtime窗口未发现额外产品文件 |
| 正常与真实路径 | 独立默认159/159、helper定向7/7；真实只读浏览器44/44（含39次UI搜索）、零页面错误，前后107行摘要一致 |
| 自造反例与负对照 | 独立网络/路径27项26通过1失败；扩展3种open均失败、2个helper入口通过；去除隔离副本相应防护后27项全F，6个POST到达私有伪生产；5脚本私有--real各拦3写通道 |
| H5 / 迁移 / 恢复 | 导出/tmp预演67；迁移副本重跑39项仍回收；独立恢复副本仅restore这39项，导出106、其余68行不变；真实库未restore |
| 证据 / 后续 | `/tmp/review-items-cleanup/`；实施证据`coordination/items-cleanup-evidence/`；主控已同意M及L口径，返修统一派A，清理无需重做 |

对象：实施A `task_1dc1233a6d12`、交付`msg_81bb3576c061`；本工人B `task_ca3082ceeccc`。按review-items-cleanup-contract.md第1–7项复核；未更改正式代码、数据、配置、调度，未重启、未提交推送。手工只新增本报告；旧测试运行还更新了6个既有生成证据文件，详“复核执行偏差”，私有数据副本及探针在/tmp。

## 合同逐项结果

| 项 | 结果 | 独立证据与边界 |
|---|---|---|
| 1 数据/39 ID/其余68 | **通过** | candidates中classification=疑似测试且原trashed_at空的集合恰39，与备份→当前完整行SHA变化集合精确相等，39行仅trashed_at变；5不确定、62真实、原回收1条合计68行全部不变，清理及浏览器阶段107无新增/缺失；交付前后到4新增按主控口径单列观察，旧107逐字段仍不变。备份107行SHA与candidates及before-rows.json均一致；39份回执均via=product_api且时间戳与当前一致，未见越界/绕过产品动作痕迹；历史HTTP动作非本工人现场见证，不伪称重新执行清理 |
| 2 列表/搜索/回收站/H5/迁移/恢复 | **通过** | 真实列表分页API共67、指定39均无；真实UI39次标题查询均不出现原ID；回收站40并含指定39。H5只输出/tmp为67。迁移只跑副本，39仍回收；独立恢复副本按rollback清单调用产品ItemsStore.action(id,'restore')39次，导出106，未动其他68。真实库不做恢复 |
| 3 SQLite备份方法/WAL/正式导出 | **未知（备份方法历史）；内容和导出通过，L** | 备份quick_check=ok、107行逐行与原记录/候选一致、可读可恢复；正式导出SHA与备份副本均为30e67c19…9ca4。A无备份脚本/命令记录，无法从.db文件逆推出当时使用backup API或是否带入所有WAL提交，不能把声明当独立证明；按主控答复记未知/L，不判数据失败。复核自己的副本用SQLite backup API，不冒充A的历史证据 |
| 4 防护所有通道/参数 | **失败（M）** | 别名、保留端口、base路径/尾斜杠、真实workspace符号链接归一、context.request/独立APIRequestContext、Node fetch/http、浏览器fetch、307主窗/弹窗重定向、route.continue改POST均拦截；两个helper在回调前拒绝。**数字O_RDONLY\|O_TRUNC绕过openSync/open/fs.promises.open保护并把私有仿正式文件截为0，故不能宣称所有写通道安全** |
| 5 去防护同组必须F | **通过（证明测试可检错）** | 只在/tmp复制guard，关闭ENABLED并移除redirect的剩余防护；同一27项反例0通过/27F，私有伪生产收到6个POST及重定向落地，正式服务未收到测试写。两个helper去防护后分别进入1/5个test回调，原“回调前拒绝”断言均F。已存在的数字截断缺陷在正常guard也F，不能把它当作仅变异时才出现 |
| 6 正常用例/产品零改/run接线 | **通过所核范围** | 默认5脚本159、父套件定向helper7全部通过；改前通过数与library旧导航失败日志保留，详下表。task-diff.patch只改8个允许既有测试，另3个新测试/guard文件有SHA；run.cjs仅安装guard、断言临时workspace、登记app/model随机origin。按主控指定07:12:40.412582Z–07:28:55.257641Z mtime扫描，排除数据/依赖/缓存/报告后仅11个允许测试，无额外产品文件；全仓有其他历史diff，不直接归给本单 |
| 7 SHA | **通过** | 11/11与sha256.json逐项相同，交付前再核；完整SHA见末表 |

## M：数值open标志绕过受保护目录检查

位置：`tests/e2e/real_write_guard.cjs:33–34`。两个包装均以 `typeof flags==='number' ? (flags&3)!==0 : /[wa+]/.test(flags)` 决定是否调用writable。数值`O_RDONLY | O_TRUNC`的读写访问位为只读，但截断位仍会修改文件；现逻辑跳过writable并继续真实fs.open。

独立探针先将**原封不动的guard**放入`/tmp/review-items-cleanup/probe-guarded2/tests/e2e/`，让其ROOT指向私有仿项目；同构的`ROOT/workspace/sentinel.txt`为保护根。保护已安装，文件13字节，执行数值标志open后无guard异常，文件变0字节。没有拿真实workspace文件做试写，更没有靠禁用防护才复现。

| 接口 / 私有受保护文件 | 期望 | 实际 | 结果 |
|---|---|---|---|
| fs.openSync，O_RDONLY\|O_TRUNC | 拒绝、保留13字节 | 未拒绝、0字节 | 失败 |
| fs.open回调式，同标志 | 拒绝、保留13字节 | 未拒绝、0字节 | 失败 |
| fs.promises.open，同标志 | 拒绝、保留13字节 | 未拒绝、0字节 | 失败 |

证据：`independent-guarded2.json/log`（27项中此项F）、`fs-helper-guarded.json/log`（扩展三路均F）。主控在本次ask答复中同意记M；返修建议是把**所有可能创建/追加/截断或读写的数值标志**纳入写判定，三种open共享相同规则，并用以上反例验证“异常发生在文件改动前”。这是测试防护修复，不需要产品代码改动或服务重启。其余已测网络/重定向能力不用推倒重做。

## 独立防护反例与变异结果

所有网络写探针指向/tmp内的随机端口伪生产；生产别名/8766/3000只调用guard的预检或登记检查，不向这些端口发送探针写请求。真实workspace符号链接只做realpath/assertSandbox拒绝测试，不执行fs写。文件截断只发生在仿项目保护根。

| 本工人另造反例 | 正常防护 | 对应证据 |
|---|---|---|
| localhost、[::1]、0.0.0.0、127.0.0.1 ×8766/3000；POST带/base/path/ | 8/8预检拒绝 | independent-guarded2.json前8组中的check项 |
| 同8组合带/nested/登记可写origin | 8/8拒绝登记 | 同JSON register项；无真实网络写 |
| 私有保护workspace符号链接、真实workspace符号链接指向/nested | 2/2拒绝 | 同JSON；未尝试正式目录写入 |
| context.request直接POST、独立APIRequestContext POST | 2/2拦截，目标无请求 | 同JSON及private_requests=[] |
| Node fetch、Node http POST | 2/2拦截，目标无请求 | 同上 |
| 页面fetch POST | 拦截，目标无请求 | 同上 |
| 307主窗导航跨随机端口到伪生产，目标另已allowRead | 在落地前拒绝 | 同上；不能靠“生产读取未登记”掩盖重定向漏洞 |
| window.open打开307重定向页到伪生产 | 在落地前拒绝 | 同上；新独立page，目标无请求 |
| page.route continue把GET改写成伪生产POST | 拦截，目标无请求 | 同上 |
| 数值O_TRUNC截断 | **失败** | 27项总26通过/1失败；三种open扩展均F |
| knowledge_first/batch_delete单独接localhost:8766/path/ | 2/2回调前拒绝 | fs-helper-guarded.json，callbacks=0 |

额外对**5份实际脚本**逐个运行`--real --base=<随机伪生产>/some/path/ --workspace <指向真实workspace的符号链接> --safety-probe`：使用本工人新建的私有fixture身份与独立items副本，每个脚本exit0、明确只读GET，各有3次阻断（context.request/Node fetch/页面fetch），伪生产107行SHA不变。验证的是参数被规范化后仍只读、外部workspace参数不会让脚本在正式目录写；不是依赖脚本名或只检查源代码字符串。证据`five-real-probes.json`、5份`*-independent-real.log`、`five-real-probes.py`。

去防护有两版，均保留不混淆：第一次只改ENABLED=false，redirect函数仍有无条件跨端口拒绝，因此27项中25F/2通过（`independent-mutant2.json`）；为验证重定向同样可检错，第二版同时去掉该剩余防护，**27/27全部F**（`independent-mutant3.json`），私有目标确收6个POST。未修改正式guard，也未更改正常防护的断言。helper变异在`fs-helper-mutant.json`，两个入口失去前置拒绝并进入test回调，2项均F。

首次独立浏览器harness复用被阻断导航的page，遇到chrome-error迟到导航与下一次goto竞争；原日志`independent-first-harness-failure.log`保留。改为各危险导航用新page后完整跑到27项；该harness竞争不计为产品缺陷，也未删除任何保护断言。

## 数据、导出和回滚证据

- `data-audit.py/json`：全部真实库连接mode=ro。candidate集合39与backup→current行差集精确相同；只trashed_at变、其他68不变、总107/活跃67/回收40。11项SHA在同一记录核对。
- `provenance-audit.json`：备份107行与candidates.row_sha256一致，before-rows.json的107项row_sha256也逐项比对；动作清单39项via=product_api、ID集合/时间戳与当前一致。未发现直接SQL绕过的额外变更，不能仅凭结果逆推所有历史调用。
- `private-data.py / private-data-results.json`：当前真实items通过只读连接backup到/tmp；H5调用export_library显式指定/tmp输出，得67。另一个恢复副本按rollback.json的39个id逐个调用现有产品ItemsStore.action(id,'restore')，得106，原已回收1项仍回收，其余68行与恢复前完全不变；**未使用回滚清单中的正式base发请求**。
- 迁移使用独立/tmp副本的items及A已冻结的原始knowledge/intake/cards副本，在副本调用真实migrate函数；禁用linux.do后台刷新，未执行真实任务。39条均保持回收。副本增为109，新增`url:22e4a68ec2840e9d`、`url:51aa516e0d64747d`，与实施证据一致，都是旧库迁移额外对象，不影响正式107；恢复演练用另一份107副本，避免将109误报成应导出106。
- 正式`~/grok-bot-reports/library-export.json`与备份副本SHA均为`30e67c19b7750d548c3b8c36a3607eb0276391c40c106d88605591124b569ca4`，没有触发H5导出任务或覆盖正式导出。

## 真实用户路径与不误伤

真实8766用独立/tmp Chromium profile。页面路由只准同源GET，其他方法/外网均abort；API访问也仅显式GET。我的素材API遍历两页合67条且不含39个目标；真实页面逐条填写39个标题查询，等实际响应/DOM完成后检查不含原id；回收站40且39卡片均存在，零pageerror，合计**44项通过**。只点击切换回收站，没有点恢复、删除、收藏或拿去写。

已目视`real-mine-masked.png`和`real-trash-masked.png`，67/40标题计数与页面切换正确；列表/阅读器区域主动遮罩隐私，条目ID检查在DOM/API做，不靠遮罩截图推断内容。前后真实107行全量摘要SHA完全一致：`98b66eecb1febbe531f855df928d7335b12e2236fc9399754e1248d141fb7114`（`real-invariance.json`）。本次没有遇到A重启导致的API失败，不需要重试服务或重启。该SHA是浏览器验证阶段107行的前后不变证据；末次全库已111行，但取原107 ID子集的SHA仍完全相同，不能据此把后到新增说成不存在。

| 默认脚本 | A改前通过/失败证据 | 本轮独立通过/失败 |
|---|---:|---:|
| library_page | 3/1 | 31/0 |
| today_page | 56/0 | 56/0 |
| today_t5 | 22/0 | 22/0 |
| interest_channel | 22/0 | 22/0 |
| write_and_publish | 28/0 | 28/0 |

本轮默认合159/159，命令`node tests/e2e/<脚本>.cjs`，日志`*-default.log`；父套件另用`E2E_ONLY=11,12,15,16,17,18,19 node tests/e2e/run.cjs`，7/7，通过日志`helpers-default.log`，该次父套件生成证据现已移至`/tmp/review-items-cleanup/helper-generated-evidence/`。没有跑全量discover，也没有调用其他人的测试进程。

library原日志在第4项“旧版5入口”终止，只能记3通过/1失败，不能声称旧完整套件通过。核对任务diff，该断言改为**9个准确legacy id的完整顺序+独立工具组2项**，符合当前workspace-nav.js实际内容，并非把导航断言删除或改成“至少一个”。其他默认脚本通过数与基线相同。

## 交付前新增4行的独立观察（不计A清理越界，不在本单处理）

最终复查全库111条，原107 ID完整、逐字段与清理后基线一致、无缺失；新增4条都是后到记录，见`late-real-drift.json`：

| 新增ID | 创建时间UTC | 主控给定归因 / 确定性 |
|---|---|---|
| x:2115935879017701581 | 08:15:40.436517Z | 主控只读核对为Grok Bot 16:06起势快报的正常镜像收藏 |
| x:2115931101688737907 | 08:16:42.729750Z | 同上，正常镜像收藏 |
| x:2115869765176815909 | 08:16:42.784237Z | 同上，正常镜像收藏 |
| url:3c953d1a497eb157 | 08:16:27.299830Z | **观察：外部探测数据**；主控推断与同次Grok运行探测收藏接口有关，完整来源未确认，不是本清理单或A操作 |

主控通过本轮ask明确要求按“107旧行不变+3正常新增+1外部探测数据（推断来源Grok Bot，归因未完全确认）”交付。B不读正文、不回退、不回收该探测条目；如何处理由主控另报黄总。此项不冒充已完全归因的正常用户收藏，也不把外部后到写入误判为本轮guard网络绕过。清理后67活跃/40回收和H5=67对应早先固定核对时点，后到新增不应强求实时仍67。

## L：备份方法留痕不足与范围核对

主控本轮ask明确回复：只有task-diff.patch、sha256.json、cleanup-results.json、precheck.json及备份目录before-rows/schedules-before/rollback/导出副本；没有backup脚本/命令留档和开工git status基线。按其裁定，**备份方法未知记L，不因缺留档判备份内容失败**。建议后续备份留最小命令、时间/源路径/目标SHA及成功回执，不打印正文或环境凭据。

产品范围采用主控指定替代办法：before-rows.json mtime **07:12:40.412582Z**至final-audit.json mtime **07:28:55.257641Z**，`rg --files --hidden --no-ignore`枚举，排除workspace/knowledge数据、依赖、缓存、downloads及coordination文档证据，只看源码类仓库文件。该窗口内只有11个本合同允许测试文件；未发现其他产品文件。M3允许文件表已列入分类，但没有额外命中。`mtime-window.json`保存全部11路径/时刻。当前mtime不能重建已被后续改写的历史，所以此结论限于该替代检查与本单完整patch，不把全仓git diff当作本单来源证明。

`task-diff.patch`的8个既有文件均在允许集合，3个新guard/fixture/test文件由SHA补充；父run仅三处接线，server_fixture.py未改，未见本单改产品代码。真实数据变更仅39项回收，已与测试代码修改分开核对。

## 本轮复核执行偏差（不计A缺陷）

运行默认`write_and_publish.cjs`前未发现其固定输出到正式coordination目录；该脚本在08:03:53Z–08:03:57Z更新了`coordination/impl-write-p3-screenshots/isolated/`下 **01-base.png、02-ai.png、03-conflict.png、04-publish.png、results.json、server.log** 六个既有生成文件。它们是隔离测试截图/结果/日志，产品代码和真实workspace数据未变；但超出了本轮“正式目录只交复核报告”的预期边界。这是B的执行偏差，不能归为A产品缺陷或隐去不报。

父`run.cjs`另生成本轮唯一时间戳目录`coordination/evidence/e2e/2026-10-30T08-06-46-710Z-85913-84a24c/`，已整体移到`/tmp/review-items-cleanup/helper-generated-evidence/`，移除本轮额外正式目录产物且保留证据。上述6个既有文件没有改前副本，不从Git或猜测版本覆盖、不盲删；已复制至`/tmp/review-items-cleanup/write-generated-evidence/`留档，路径/mtime见`generated-artifacts.json`，在worker_done的files-modified明确列入。已向主控说明该偏差，后续只用/tmp改写输出路径的副本运行这类旧脚本。

## SHA清单与复核边界

| 文件 | SHA256 |
|---|---|
| `tests/e2e/library_page.cjs` | `2f6ff97f62010be94f86ac074f5110ed46f4c73145b1593bdc3b73ea848842ad` |
| `tests/e2e/today_page.cjs` | `f6313b1087d3b122e4cc25e47784ea3ee636ca25ded7a75f9547986db484c5e3` |
| `tests/e2e/today_t5.cjs` | `fe8f208a840e5274088f2816a1f2ae50ec0947020fdfef534fd2d1793dccff3c` |
| `tests/e2e/write_and_publish.cjs` | `a6b57d40bfe8df6b860fd75fd14d91a58e76d5591dbf1362ab86afd4a9b37f19` |
| `tests/e2e/interest_channel.cjs` | `315ce49a14491e1e51d8d9f01584cfb5e9ef3cb94478429ec842825df9bf6464` |
| `tests/e2e/knowledge_first.cjs` | `7a4d4ca42f2101d4bcb491e2be55a44c0653f5806054c630fad98627a8ec2d98` |
| `tests/e2e/batch_delete.cjs` | `34b65ec075043c6d60534307acab8692987f80b26bcde9de7f729e8d2ee6ece7` |
| `tests/e2e/run.cjs` | `ccf81111d7f77ac81939ab5cc53906d50800cba40b5b29e1f5ccb67aee972220` |
| `tests/e2e/real_write_guard.cjs` | `7a64a0fc7c2cfc887e1a16110b63ee5716055d7582f692e4bfdadd0d571858c4` |
| `tests/e2e/real_write_guard.test.cjs` | `ab58659b26866c71181a2c71050772e8a24b911a6e009e45bd7b5ff1d65dab69` |
| `tests/e2e/real_write_guard_fixture.py` | `33b736ce547731cd3757b553233903a0661db484e16f63497fa0be7eb464458b` |

复核期间未修正式文件，数字标志M和备份留痕L均已报主控并获明确口径。建议只返修guard数值标志及相应防护测试；清理39 ID与其他已通过数据链路复用证据，不在真实库重做回收/restore。修复后复核三种open必须在文件变化前拦截，并保留默认用例、网络/重定向负对照能力。

本报告和安全文本证据做凭据模式扫描，结果`report-validation.json`；数据库/私人原文副本仅存/tmp，不抄入报告。扫描不是对未知真实Key逐值比对。交付只代表本单复核完成，产品防护仍未验收通过，不结算在途H3a M3。


## 返修 1 复核

| 决定要点 | 结果 |
|---|---|
| 结论 | **通过（限定本合同的5个E2E+2个helper及既有调用链）**；原O_TRUNC M已修，非通用文件系统沙箱 |
| H/M/L | **0H / 0M / 2L**：L1递归cpSync目标内符号链接旁路；L2包含保护根的祖先目录rename旁路；均未在指定脚本中发现可达的正式写路径，按主控裁定非阻断 |
| SHA与范围 | guard与新增fs测试2/2符合报告；原5脚本、2helper、run、网络测试/fixture的旧SHA相同；产品代码未见本单改动；H3a并行M4单列归因 |
| 原M与正常回归 | 原27反例27/27；O_TRUNC三路及helper补充5/5；默认159/159、helper7/7；共用网络28/28、五入口去防护F=5 |
| 文件系统反例 | A新增140项+继承stdout1项均通过，去防护F=141；B独立新增21修改场景：18正常阻断、1阻断但进程异常退出、2旁路；3放行控制通过；同组去防护F=21 |
| 两条probe与回滚 | 素材111条、回收41；目标trashed_at为08:52:58Z；知识documents302977行不硬删、仅目标trash标记新增；真实页面只读5/5；A原回滚命令在副本完整行2/2、标记复原1/1 |
| 留痕与证据 | 开工git status/SHA、备份脚本原文存在；上轮6个生成证据SHA未变；本复核证据`/tmp/review-items-cleanup-fix1/`，仅追加本节 |

对象：实施`task_21d2e843d536`，本复核`task_f8ddd1cdea7c / ctx_4d74eb40042c`。主控补充裁定`msg_622db293d33a`（2026-10-30T09:35:31Z）：防护目标是指定5个E2E和2个helper的任何参数路径，不是通用沙箱；新旁路只有在该调用链可落正式workspace时记M，否则记L并给修法。下面保留通用反例的失败，不把其改成“全部通过”。

### 合同逐项判定

| 项 | 判定 | 验证结果与证据 |
|---|---|---|
| 1 SHA与边界 | **通过，附并行变更说明** | 两个交付SHA一致；旧10个测试/fixture文件SHA与上一轮一致；server_fixture未改，mtime为10-10；server/today_view与M3前保护清单一致；返修1时间窗内代码mtime只见guard及新增fs测试。当前radar_common另有09:35Z的已知M4改动，不归入清理返修1 |
| 2 原M及27反例 | **通过** | 原独立27/27；O_TRUNC同步/回调/promises三路均E2E_GUARD、原13字节不变；两个helper未登记base均拒绝且回调0次；`runners/independent-guard/independent-guarded.json`、`fs-helper-probes/fs-helper-guarded.json` |
| 3 覆盖完整性与新增反例 | **通过本轮调用链门槛，附2L** | 运行时独立枚举100个fs导出函数、31个promises函数、21个FileHandle原型名称逐一分类；21个新增修改组合逐条实跑，找到cpSync/祖先rename两处旁路。实际7脚本及run/fixture无通往正式目录的该操作路径，依主控裁定记L；child_process边界另列，不宣称通用隔离 |
| 4 放行与负对照 | **通过** | O_RDONLY与r读取4/4（A套件），B的只读与/tmp写控制3/3；默认159、helper7；去防护文件系统同组141F、B追加21F，网络五入口各F；初次复制网络套件的ROOT未改全而ENOENT，修/tmp副本后28/28，原失败日志保留 |
| 5 两库、产品机制、真实页与回滚 | **通过** | 两份历史快照逐表/逐行独立核对，素材仅指定ID的trashed_at、知识仅指定document_trash标记变化；文档302977原行无改动；真实两页只读5项；A原rollback.py以随机端口调用副本，完整原始行2/2且trash标记恢复为空 |
| 6 留痕与证据覆盖 | **通过（可核范围）** | A开工git status/SHA时间08:46:13Z先于08:52:20Z备份动作；备份执行脚本与证据原文字节相等；旧6截图/结果SHA均相同；本复核开工冻结coordination文件mtime/大小/SHA，输出路径全部改至本轮/tmp。没有A开工前整个coordination递归清单，不能据此证明其历史所有文件绝对未覆盖 |

### SHA、正式文件及并行变更

| 本次交付文件 | SHA256 |
|---|---|
| `tests/e2e/real_write_guard.cjs` | `01ecc1aa03a344beab4ea6c11d57ddae50a19acd2ca8b0189b1071038232cc64` |
| `tests/e2e/real_write_guard_fs.test.cjs` | `00e9fb00181dff29a5adc30e5cb1bea1b8475602b816bc09efb9e22df57d3418` |

`cleanup-protected-sha.json`独立对上轮交付清单：library_page、today_page、today_t5、interest_channel、write_and_publish、knowledge_first、batch_delete、run、real_write_guard.test、real_write_guard_fixture共10文件相同；`server_fixture.py` SHA为`e717d3e9a094c81ff03ac889ba15b71c3c1310c6294b1863517ff17111b174b8`，mtime为2026-10-10T01:11:39Z，不在返修1窗口。此前B副本未留这一文件的独立原SHA，故用当前SHA+旧mtime交叉佐证，不伪称有不存在的前轮原始摘要。

`server.py`当前SHA与B的M3前保护快照相等；`knowledge_store.py`mtime为10-11，两个产品机制文件都早于本单。按08:46:13–09:05:00Z检查代码文件mtime，仅guard和新增fs测试；mtime不是独立版本控制日志，范围判断同时结合交付diff/旧SHA。`scripts/radar_common.py`当前与M3版有缩写复数切分diff，mtime09:35:12Z，属于合同已告知A在做的H3a M4；`today_filter_config.py`仍与M3相同，不把M4当本单越界或在本单验收M4。

### 独立Node文件系统API清单

先取当前Node v22.22.1实际导出及FileHandle原型（`node-api-inventory.json`），再按修改语义分类，最后对照A表；完整逐函数清单`node-api-classification.json`覆盖100+31+21个名称，包括读取/遍历/资源释放项，没有只照抄A的覆盖表。以下列出全部修改入口类别；存在同步/回调/promises变体的均逐一归类：

| API族（含存在的Sync变体） | 实际防护/边界 |
|---|---|
| open、openSync、promises.open | 数字位从constants合成，任一O_WRONLY/O_RDWR/O_TRUNC/O_APPEND/O_CREAT/O_EXCL按写处理；字符串w/a/+按写处理；成功后登记fd；三路O_TRUNC实测通过 |
| writeFile、appendFile、truncate | 路径/fd/FileHandle检查；相对路径、Buffer、file URL归一；独立组合及A三变体通过 |
| write/writev、ftruncate、fchmod、fchown、futimes、fsync、fdatasync | 追溯已登记fd路径；未知普通文件fd拒绝；只读方式打开保护文件也不许调用修改方法 |
| copyFile | 检查目标路径，源只读；A三变体通过 |
| cp | 只检查传入顶层目标；回调/promises在本轮dereference:true组合经内层检查阻断；**cpSync递归目标内部链接仍会修改保护文件（L1）** |
| rename | 源和目标均检查；直接移入/移出拒绝；**祖先目录仍可被整体移走（L2）** |
| link、symlink | link检查源和目标，symlink检查新链接位置；已有符号链接realpath、已有硬链接inode回查分别阻断 |
| unlink、rm、rmdir | 直接保护路径拒绝；rmSync祖先递归经内层保护阻断；promises.rm祖先递归出现异步uncaught E2E_GUARD，保护字节未变但进程退出，另列观察 |
| mkdir、mkdtemp | 位置/前缀检查；保护目录内递归建子目录、promises.mkdtemp前缀均阻断 |
| chmod、chown、lchmod、lchown、utimes、lutimes | 路径检查；A的普通路径元数据反例和B的utimes(file URL)/chmod(Buffer)组合通过；当前运行时确实导出lchmod函数，A表“未提供”不准确，但代码已把存在的此入口纳入包装；本复核不据函数存在推断OS底层一定支持 |
| createWriteStream、WriteStream及旧别名FileWriteStream | 路径或options.fd检查；旧别名依底层open/write保护，本轮FileWriteStream r+也已被拦；r+普通写流组合被拦 |
| createReadStream、ReadStream及旧别名FileReadStream | 读取构造本身不修改；写flags仍须经open防护；A对前两种w flags反例通过，旧FileReadStream未单独实跑，不说三构造都已覆盖实测 |
| FileHandle.write、writev、writeFile、appendFile、truncate、chmod、chown、utimes、sync、datasync、createWriteStream | 共享原型逐项检查this.fd；A11方法通过，B另跑read-fd writeFile/appendFile组合均拒绝 |
| mkdtempDisposable（本Node无此导出） | 包装存在时接入；当前不适用，不计测试通过 |

其余实际导出（read/readv/readFile/readlink/readdir/realpath/stat/lstat/fstat/statfs/glob/opendir/openAsBlob/access/exists/watch/watchFile/unwatchFile/close/类型构造/纯转换等）属于读取、遍历、监听或资源释放；FileHandle读取方法/描述符信息同理。close和asyncDispose释放资源，本身无内容写入；open及读流因flags例外已单列。

### B独立新增组合反例逐条结果

所有场景在全新`/tmp/review-items-cleanup-fix1/fs-guarded-*`或`r2-fs/fs-guarded-*`仿正式project/workspace中运行；没有对真实目录发破坏性探针。与A按单个API直接保护路径的140项不同，本组新增了递归目标内部链接、祖先目录、路径类型与方法组合、相对路径及旧构造别名等组合。

| 组合反例 | 开启防护 | 保护状态 | 去防护同组 |
|---|---|---|---|
| cpSync-nested-destination-symlink | 失败（旁路已复现） | 被改写/原路径消失 | F |
| cp-callback-nested-destination-symlink | 通过 | 未变 | F |
| cp-promises-nested-destination-symlink | 通过 | 未变 | F |
| rmSync-ancestor-containing-workspace | 通过 | 未变 | F |
| rm-promises-ancestor-containing-workspace | 阻断（进程异常退出） | 未变 | F |
| renameSync-ancestor-containing-workspace | 失败（旁路已复现） | 被改写/原路径消失 | F |
| relative-dotdot-writeFile | 通过 | 未变 | F |
| writev-protected-read-fd | 通过 | 未变 | F |
| rmSync-protected-recursive | 通过 | 未变 | F |
| mkdir-protected-recursive | 通过 | 未变 | F |
| mkdtemp-promises-protected-prefix | 通过 | 未变 | F |
| renameSync-move-in | 通过 | 未变 | F |
| renameSync-move-out | 通过 | 未变 | F |
| createWriteStream-rplus | 通过 | 未变 | F |
| FileHandle-writeFile-readfd | 通过 | 未变 | F |
| FileHandle-appendFile-readfd | 通过 | 未变 | F |
| utimes-fileURL | 通过 | 未变 | F |
| chmod-Buffer | 通过 | 未变 | F |
| hardlink-appendFile | 通过 | 未变 | F |
| symlink-dotdot-truncate | 通过 | 未变 | F |
| FileWriteStream-rplus | 通过 | 未变 | F |

B新增修改场景21个：18个正常E2E_GUARD阻断；promises.rm祖先递归1个因E2E_GUARD未被内部Promise链捕获而子进程退出，退出后保护文件仍完整；2个旁路失败。另`tmp-cp-allowed`、同步O_RDONLY读取、promises的r读取3/3放行。去掉防护相同21个修改测试全报F，其中18个实际修改了保护状态；另外writev/两个FileHandle read-fd写在无防护时仍有原生EBADF，不冒充这些负对照都实际写成功。机器原始结果、每例独立目录、异常栈都保留，见`r2-fs/independent-fs-results.json`及`independent-fs-classification.json`。

初轮cp回调/promises使用默认dereference:false，在无防护时也被Node原生DIR_TO_NON_DIR错误拒绝，不能证明guard有效；保留初轮日志后，在新目录加dereference:true再跑，开启防护均拒绝、去防护实际修改。cpSync在两轮均逃逸。没有通过擦除首轮失败证明测试正确。

#### L1：cpSync顶层目标通过，但子项真实目标受保护

复现：普通`safe/src/sub/sentinel`拷到普通`safe/dst`；预先使`safe/dst/sub`是指向仿正式workspace的符号链接。`fs.cpSync(src,dst,{recursive:true})`无E2E_GUARD，workspace/sentinel由`original bytes`变`changed bytes`。当前guard在`checkArgs`仅检查args[1]（顶层dst），递归内部子项并非都经包装检查。

调用链核对：`today_page.cjs:20`确有cpSync，但目标固定为`mkdtempSync('/tmp/today-e2e-')`新目录下的public，目标不是参数、原先不存在目标子目录链接；复制源public也不等同可写目标。其他指定脚本/helper以及run/server_fixture/batch_fixture未见递归cp到可控已有目录。`--real`在文件顶部直接进入共用只读路径并return，不走该复制分支。因此依主控裁定本轮记L。建议后续对递归目标逐项realpath/inode检查（含已有子目录符号链接），不能只检查顶层目标；或仅允许复制到经证明全新且不可替换的目标树。

#### L2：rename可整体移动包含workspace的父目录

复现：`fs.renameSync(project,movedProject)`中project本身不是protectedRoots内部，检查通过，仿正式workspace原路径消失而位于movedProject下。源/目标各自“在保护根内部”的检查缺少“是保护根祖先”的反向包含关系。

调用链核对：唯一相近入口`today_page.cjs:35`是逐个反馈文件的临时文件rename，不是目录rename；该旧恢复函数还被`REAL=false`和顶部`--real`提前return隔开。其余指定7文件及run/fixture没有祖先目录rename入口或参数传递。建议后续rename源与目标同时检查是否等于/包含任一保护根；此条依主控裁定记L。祖先promises.rm只读保护失败方式为进程异常退出而非正常Promise拒绝，当前未见指定链路使用该模式；应避免同步throw穿透Node内部递归回调，但不把已阻止写入说成成功逃逸。

#### child_process边界（观察）

独立在/tmp用`execFileSync('/bin/sh',['-c','printf changed > "$1"',...,sentinel])`证明Node fs包装不约束子进程，开启guard时仍可改仿正式文件（`child-scope-probe.log`，不是正式目录）。指定脚本存在子进程，但未发现shell重定向或将参数直接指向真实workspace的同类调用：

- library_page:28/30/36的Python夹具和服务显式用新/tmp workspace；today_page:28、today_t5:32、interest_channel:25、write_and_publish:31的Python服务也用各自mkdtemp根，reports/model配置指向私有路径；--real均提前return。
- batch_delete:41只把父套件私有workspace交给batch_fixture.py；后者:9–12验证workspace名、article-studio-e2e前缀、temp根与32位ID后才更新副本。knowledge_first没有child_process写入口。
- run:459给server_fixture传内部mkdtemp workspace和随机端口；fixture把外网及配置换成夹具，batch_fixture和status_summary/cards fixture也沿该私有root。ROOT/cwd用于导入正式源码不等于正式Store路径；未见上述组合将生产ROOT拼成实际数据库目标。

因此不把“存在Python子进程”本身记M，也不宣称这是OS级沙箱。安全结论限于已检查脚本和既有固定路径；今后新增子进程、目录复制/rename接线需要重新检验。

### 两库独立核查与产品既有回收机制

备份`/tmp/items-probe-cleanup-20261030T085220Z/`中的before/after是SQLite一致视图。B全表读mode=ro：

- items：111→111，历史行变更ID严格仅`url:3c953d1a497eb157`；仅trashed_at从空变`2026-10-30T08:52:58.077181Z`；其他110行完整行摘要不变；回收40→41，活跃71→70。B当前快照111/41且与after全部业务行、表摘要相同。
- knowledge：documents302977→302977，逐行均相同；document_trash1→2，只有`6d7b145d038ef16e9acf4fa3dc21e7dd`新增标记、旧标记不变。没有硬删documents。
- items_fts_data/idx派生内部块变化已复现：items_store.py:145的items_au AFTER UPDATE触发器重建对应FTS记录，属于这次trashed_at更新的派生索引，并非额外业务素材变化；历史knowledge其他表都相同。

**document_trash是既有产品机制**：`knowledge_store.py:83–84`定义表/索引、:38的NOT_TRASHED控制常规检索，:545起注释解释“已删除（可恢复软删除）”；`server.py:1164–1169`接preview/restore/commit路由，:548–558将commit交给既有`knowledge.trash_many`，只有产品实现:734执行INSERT标记；:568–573调用`restore_many`，后者:771–783只删除标记而不改正文。当前knowledge_store.py早在10-11修改，before快照也已经有表及一条原标记，故不是A临时造表或在清理脚本直接INSERT。

A操作原文`probe-cleanup.py`已读：只读备份并核字段、preview hash，再仅调用`POST /api/items/url%3A3c953d1a497eb157/trash`和`POST /api/knowledge/trash`（confirm及expected_hash），没有直接业务SQL更新/删除。A备份副本原脚本与正式证据脚本字节相同；`verify-data.py`只读核对修正FTS断言，未重复生产回收动作。完整原记录保留在私有/tmp备份，不写报告正文。

**期间当前快照漂移已归因**：B当前documents仍302977，只有`3e616ac02a886a3409e143ba0faa634b`较A.after变化；provenance指向`coordination/impl-items-cleanup-report.md`（projects来源），新body逐字等于当前已授权追加返修1的实施报告，旧body无返修1节，modified为09:04:59Z，projects同步09:27:47Z。是项目文件正常索引更新，附带其FTS变化。sources只见synced_at及三个GitHub来源的同步detail元数据更新，config/status未变；无新文档、未改其他原正文。见`current-drift-attribution.json`。不把运行期间知识库镜像更新误记成清理越界。

正式导出SHA仍为`30e67c19b7750d548c3b8c36a3607eb0276391c40c106d88605591124b569ca4`，未触发导出。

### 真实页面及回滚命令独立预演

真实8766仅同源GET、隔离Playwright独立profile，5/5：素材probe搜索不含目标ID；素材回收站API和卡片包含目标；原始资料external搜索不含目标；“已删除”列表含目标且有恢复按钮。未点击真实恢复/删除按钮；截图对私人列表mask，已目视知识回收弹窗。证据`real-ui-result-r2.json`及`ui-egdHIL/`。

B从A.after两库只读backup建立`restore-private/workspace`，外网NetworkGuard、sources=[]、reports/model隔离，在随机端口`65004`启动临时Handler。实际原样执行A提供的：

```sh
../article-importer/.venv/bin/python /tmp/items-probe-cleanup-20261030T085220Z/rollback.py --base http://127.0.0.1:65004
```

两个restore请求均200；素材完整原行、原始资料完整原行与before/original-records逐字段一致（2/2）；目标document_trash复原为原先不存在（1/1）。不是只比较“正文还在”便声称恢复成功，也没有对真实8766运行该命令。预演子服务已由本脚本正常terminate，日志和server代码均在本轮/tmp，未重启正式服务。

### 正常回归、负对照及留痕

| 独立运行 | 通过数 |
|---|---|
| library_page | 31 |
| today_page | 56 |
| today_t5 | 22 |
| interest_channel | 22 |
| write_and_publish | 28 |
| helpers | 7 |

原独立27/27，数字O_TRUNC三路+helper5/5，网络28/28（去防护五入口各F，且私有数据库确实变化）；A新增fs套件本轮复跑140/140+继承stdout1/1，去防护同组F=141，只读4/4，见`fs-suite.log`。B追加21场景与3控制不混入140/141的数字。无需Python全量，未跑真实写请求。

正常E2E断言正文不变，仅改ROOT源码导入与输出路径。复核路径自纠：初跑library_page/today_page仍把截图留在各自新建的/tmp夹具目录（没有写正式目录）；发现后已在临时runner中将输出分别集中到本轮`runners/library-output-r2`、`today-output-r2`并重跑31+56项，全部通过，原日志保留。其他输出已在本轮指定/tmp目录。初次额外网络套件漏改ROOT导致找不到/tmp下的Python，`runners/network.log`原失败保留；修正临时副本导入后`network-r2.log`28项及mutation5项通过，无正式源码修补。上述复核执行纠偏单列，不计为实施方缺陷。

A开工`git-status-before.txt`/`sha-before.txt`在08:46:13Z，备份脚本在08:52:20Z；完整备份命令`../article-importer/.venv/bin/python coordination/items-cleanup-evidence/fix1/probe-cleanup.py`及verify-data命令都有原文，B没有重跑生产动作。B上轮意外覆盖的6个coordination/impl-write-p3-screenshots/isolated文件，当前SHA与上轮最终披露SHA全部相等，mtime仍属08:03Z旧运行，本次未再覆盖。

A的141覆盖表存在上述目录组合边界，不能据“API名字都包装”推出递归目标全部安全；其0L自评由本复核修正为2L。已知弱点逐条核对：只读保护/继承由27、3路O_TRUNC、140+stdout和B组合负例验证；整条链路由两库、两个真实页面、回滚全原行验证；输入授权边界由历史逐行SHA验证；留痕由开工文件/备份原文/上轮6文件摘要验证。没有因为原M已修就遗漏这些边界。

本复核仅追加本节，原报告前缀逐字节保留；数据核查连接mode=ro或读取/tmp快照；不改代码、正式数据、调度，不重启，不提交推送。主控可决定是否顺带修L1/L2；若今后脚本新增可控目录复制、祖先rename或子进程写接线，需重开相应验证，不沿用“通用防护完整”结论。

最终证据核验：开工时冻结的15,775个协调文件中，清理单既有证据和上轮6文件的SHA/mtime均未变化；其他既有变动只有在途H3a合同、H3a报告及主控work-queue，本复核未写它们。文本凭据模式扫描505文件的6处命中均为复制但未执行的settings_page.cjs固定脱敏测试字面量（夹具赋值及不泄露断言），没有发现真实凭据；不输出匹配值。原报告前缀仍逐字节相同。
