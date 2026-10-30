# 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **不通过**：11 项复核已完成；第 7 项正式“微信”首查未达到性能合同。 |
| H / M / L | **0 / 1 / 0**；M1：本轮首查 **4.506915 秒**，后两次 0.341007 / 0.280078 秒。 |
| SHA / 范围 | 实施报告 13 个 SHA256 全部一致；相对开工 tracked diff 仅 `server.py`、`public/workspace-nav.js` 增量；4 个保护模块 SHA 不变。完整 SHA 见后。 |
| 迁移 | 独立真实旧库副本两次均 **79 条**，逐条 hash 一致；97 个旧引用可反查；5 条真实全文、8 个附件路径/meta 对照一致。 |
| 自动测试 | Python **50/50**；素材库原浏览器套件 **28/28**；今日原浏览器套件 **56/56**；补充完整路径套件 **40/40（含原28项，新增12项）**；另结构化内容 **4/4**。 |
| 正式性能 | 素材首屏5次与两列表各5次均 <1秒；四词各3次中仅“微信”第1次超标。 |
| 正式运行 | PID **18529**，启动于 2026-10-29 22:25:03；后端文件最后修改早于启动；本次只 GET、未重启。 |
| 本次改动 | 仅本报告与 `/tmp/review-library-p2/` 证据；未改业务代码/测试/真实数据，未提交、未推送、未用 computer use。 |

## 1. 阻断项 M1：正式“微信”本轮首查超过 1 秒

复现起点：2026-10-29T14:34:42.335Z（北京时间22:34:42），服务已运行约9分钟。本次此前未访问正式资料检索、不预热该词；按 agent → 微信 → MiniMax H3 → 4090 顺序，逐词连续三次 GET，完整读取 JSON 后计时。

`GET http://127.0.0.1:8766/api/library/search?q=%E5%BE%AE%E4%BF%A1` 返回200、50条、`short_query=true`，但三次分别为 **4.506915292 / 0.341007166 / 0.280078250 秒**。合同明确要求包含首查的每次都小于1秒，故不能以热查通过关闭该项。较慢首次查询的底层原因尚未定位；不能单凭这些数字断言是磁盘、缓存或后台采集导致。

这次未重启服务、未清系统缓存，也无法排除其他用户同时读取服务；“首查”严格指本复核窗口中未预热的首次查询，而非操作系统冷启动。该结果足以否定“本轮各3次全部小于1秒”。原始证据：`/tmp/review-library-p2/formal-get.json` 的 `started` 与 `perf.微信`；复现脚本 `real-get.cjs`。修复后应在服务空闲、尚未查过该词的检查窗口重新测含首查的三次，不得先热身再报三次。

## 2. 11 项独立复核

| # | 结论 | 独立复现及证据 |
|---|---|---|
| 1 SHA与范围 | 通过 | 13/13 SHA与报告相同；`pre.diff`与当前完整`git diff`逐文件块比较，增量只有server/nav；server仅新增items GET/POST/DELETE装配、reference成功回执镜像一行、library CSP例外。今日原22项保留，旧case15唯一期望2594.05×5.12改为×3；新增case16具体id排序。`static.json`、4份`.diff`。 |
| 2 knowledge只读 | 通过 | `readonly()`以`mode=ro`打开，fallback仍是`mode=ro&immutable=1`；mmap是连接级设置。新检索与资料收藏读取无旧库DML/DDL；knowledge_store SHA不变。真实旧库的/tmp副本迁移、实际Handler HTTP检索/收藏/动作前后三库SHA均不变。`migration-review.json`、`http-review.json`。 |
| 3 迁移正确性 | 通过 | 独立复制真实三库，不带正式items/今日反馈，调用迁移脚本实际`migrate()`两次：79/79、逐条hash全同；59人存+19卡+1占位，intake18全部合并，0跳过。97 refs逐个反查；19卡父存在。缺失卡460440ed…对应kb:87a22e6c…为false且正文明确原资料已缺失。真实5条逐字全文、8条附件meta/path一致；六类规范id合成独立期望全对。`migration-first.json`、`migration-second.json`、`migration-review.json`。 |
| 4 人写字段 | 通过 | 真实迁移副本选一条改note、status=used、feedback=accurate，再迁移且POST等价upsert新来源/引用；三个人写字段仍原值，原sources/legacy_refs全保留且新增合并。`migration-review.json.human_fields`；对应50项单测也全过。 |
| 5 并发与删除 | 通过 | 实际随机端口Handler：同id并发两个201且A/G都在；不同id两个201。未回收DELETE400；被非card parent_id引用DELETE400并说明引用；恢复整条全等；card随父回收/恢复/删除；FTS查不到被删除内容。6类写动作×3种非法来源/头=18个403。`http-review.json`；单测还覆盖真实reference API镜像库只读仍201/COLLECTED。 |
| 6 安全 | 通过 | 真实隔离浏览器：恶意标题/全文/note/snippet保持文本，无img/script/svg注入节点，危险javascript无href；高亮为mark。额外脚本哨兵未执行。正式GET逐字核对7页CSP：只有today/library带https，twitter-radar/video-radar/index/cards/intel保持原串，不加http。`library-e2e-CfO5Ft/results.json`、`library-e2e-jA5h9C/results.json`、`formal-get.json.csp`。 |
| 7 资料检索 | **失败 M1** | 长词真实副本EXPLAIN为FTS虚表索引+documents主键查找，snippet不投影body/COUNT；短词标题LIKE、SQL LIMIT51用于探测下一页、返回最多50且total_label为≥50；document_trash排除单测通过。性能原始数字见下，微信首查4.506915秒失败。 |
| 8 今日页 | 通过 | 独立断言9330/未知在2594/412前；同R1000时412在111前，具体id序列见`http-review.json`。正式48h接口dotey id…13118索引1、heat8305.962570，在eternityspring id…23568索引2、heat7782.15前。keep/use写items且原feedback仍写；原浏览器56/56。 |
| 9 用户路径 | 通过 | 额外隔离浏览器按同一个x:12345678走今日存→素材库→改理由→拿去写复制全文且used→回收→恢复→再回收→两次确认删除→GET400。随后资料agent→存为素材→我的素材按id/全文找到。J/K按具体id切换；来源/类型/标签/状态按结果id断言；末词输入后308.135ms发一次请求、180ms时无请求。`library-e2e-jA5h9C/results.json`与`extra-flow.log`。 |
| 10 导航 | 通过 | 正式隔离浏览器导航顺序today/library；knowledge/intake/cards在底部details旧版组，href仍原路径；三个正式旧页GET打开body可见、标题正确。`formal-get.json.nav/screens`与首屏截图。 |
| 11 测试强度 | 通过（有性能覆盖局限） | Python50/50、原素材库28/28、原今日56/56均本窗口独立运行；检查代码确有逐字全文、具体id先后、403、落盘内容、来源并集等断言。额外补同条完整用户链及筛选/防抖12项、结构化长文/串推/引用/媒体4项。原素材库合成数据很小；--real脚本在多次读取后才测搜索，不能替代未预热正式首查；本次另写GET脚本发现M1。 |

## 3. 正式原始性能（秒）

| 查询 / 路径 | 顺序测量（含本轮首查） | 结果 |
|---|---|---|
| agent | 0.064148, 0.015406, 0.012595 | 通过 |
| 微信 | 4.506915, 0.341007, 0.280078 | **失败（首查）** |
| MiniMax H3 | 0.045076, 0.013083, 0.015215 | 通过 |
| 4090 | 0.037733, 0.023572, 0.023632 | 通过 |
| AI | 0.008749, 0.012859, 0.007813 | 通过 |
| `/api/items` | 0.020410, 0.006829, 0.008134, 0.007261, 0.005339 | 通过 |
| `/api/library/search?q=agent` | 0.016797, 0.013025, 0.012507, 0.011982, 0.014853 | 通过 |

正式素材库首屏从goto前到首条出现且aria-busy=false，五次：0.099298, 0.040366, 0.040018, 0.041608, 0.036291 秒。截图 `/tmp/review-library-p2/formal-firstscreen.png` 已实际查看。

长词副本查询计划（agent / MiniMax H3 / 4090相同）：

```text
SCAN document_fts VIRTUAL TABLE INDEX 0:M2
SEARCH d USING INTEGER PRIMARY KEY (rowid=?)
CORRELATED SCALAR SUBQUERY
SEARCH t USING COVERING INDEX sqlite_autoindex_document_trash_1 (document_id=?)
```

这里FTS虚表SCAN是MATCH索引访问。短词按合同例外对标题LIKE并倒序LIMIT，不能把它称为同样走trigram索引。

## 4. 迁移抽查与字段保护

初次与二次迁移的汇总hash均为 `16f8178b3589c4db8e950dcd7ca78b5d60b16c7148bd93f73de13ffd88d63c55`。每个来源旧/新/合并/跳过与实施报告一致：external-references 19/19/0/0、articles 19/19/0/0、manual 10/10/0/0、remio 11/11/0/0、intake 18/0/18/0、cards 19/19/0/0、missing-card-parents 1/1/0/0。

| 旧文档id | 来源 | 正文字数 | 对照 |
|---|---|---:|---|
| `018f952926e10259a856506cbc63879d` | external-references | 39131 | 逐字一致 |
| `19c9d132241881d0b79ccbe347b8d772` | external-references | 39119 | 逐字一致 |
| `bd8aca1fc9dcb950db909e1a8deb8cad` | articles | 38935 | 逐字一致 |
| `0caa9514e6a51054ec45e499ecfe47eb` | manual | 847 | 逐字一致 |
| `f3676b05ac4e941faae6bb385afb52fa` | remio | 18953 | 逐字一致 |

另遍历真实8条带file_path的drop，逐条比较完整附件路径与解析后的file_meta字典，全等。人工59条旧文档中没有可确认的结构化串推样本，因此未冒称真实旧库含串推；合成独立HTTP与浏览器另外验证article_text、thread对象/字符串、quoted、media完整落盘和逐字显示，4/4通过，证据 `structured.json`。原今日56项还覆盖Grok/AIsa长文、串推、引用内容的显示。

人写字段独立验证结果：note=`复核本人理由😀`、status=`used`、feedback=`accurate`，迁移与重复收藏后均保留，新增review-extra来源及review/extra引用存在。正式库未作这些改动。

## 5. 13 个实施文件 SHA256

| 文件 | 本次磁盘SHA256 | 与实施报告 |
|---|---|---|
| `items_store.py` | `e3c5b9193d55483c7553d7d2c6ff2511cdc04fca4addb61b83381956401133ee` | 一致 |
| `scripts/migrate_to_items.py` | `c0808ec13775eba3f7e5a6b88ffe2aa647f6226d1204f86198c884ef1fdc94a4` | 一致 |
| `public/library.html` | `4c23e64c78b25b1432fc444a446b6a9304b1a71abb117d848c2bc370f47830b1` | 一致 |
| `public/library.js` | `ae24e4f1f050c654ef878b74d25692a3df792de7cb7a3a9129914974617462cc` | 一致 |
| `public/library.css` | `d5c67727f0499ae2428d08b116ad2d70e5b8a74450e781f92d7db53babd847ae` | 一致 |
| `public/components/list-reader.js` | `146e4266822e54346c11b4c36e62f1f955f85ec262ec5661ee17038d176b6b34` | 一致 |
| `today_view.py` | `93346a2c14e9e6166ec758399bdea97c980f750a811812987b82945b62d3de86` | 一致 |
| `server.py` | `9e99206604c7fe4669cebaf46a98c108f31992349821112c7beabce51c38a2a3` | 一致 |
| `public/workspace-nav.js` | `5f544aad2e324cc2911cc130700cba441c25e294635ba9323ea35130a0fa1781` | 一致 |
| `tests/test_items_store.py` | `cd38c390a748f49009de2119cc13ed9f03c2ef17b52b54a784eebdc470988dcb` | 一致 |
| `tests/test_migrate_to_items.py` | `397e2e1610056c528a1f864f1031d8e7b0eb6a01e791d5d11eda170fafc77b21` | 一致 |
| `tests/test_today_view.py` | `cd25dd6d316c0482d21e174b683f75df0265fdd2a3a929df0015c61db9bfea75` | 一致 |
| `tests/e2e/library_page.cjs` | `ed0eea4162d2af2029bfd4db3d1ff91bceb440eaedc164b7952a3b8036811072` | 一致 |

4个保护模块的完整SHA在 `static.json.protected`，与实施报告全部一致。开工`pre.diff`只可证明tracked增量范围；对开工时尚未跟踪文件，使用实施报告SHA和已有开工副本对照，不虚称Git能还原全部untracked历史。本次结束再核13个SHA仍全部一致，HEAD仍 `22218a71757b1217acce5491e754152e29031ab8`。

## 6. 命令与原始证据

所有脚本在 `/tmp/review-library-p2/`。Python均使用 `../article-importer/.venv/bin/python`，设置 `PYTHONDONTWRITEBYTECODE=1`。复制浏览器套件只替换REPO/ROOT的绝对路径及临时目录前缀；原业务断言不改，未使用`--real`写正式服务。

```sh
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python /tmp/review-library-p2/run-tests.py
node /tmp/review-library-p2/library_page.cjs
node /tmp/review-library-p2/today_page.cjs
node /tmp/review-library-p2/extra-flow.cjs
node /tmp/review-library-p2/structured.cjs
node /tmp/review-library-p2/real-get.cjs
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python /tmp/review-library-p2/migration-review.py
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python /tmp/review-library-p2/http-review.py
```

- `python-tests.log`：50项逐名结果，19素材+8迁移+23今日，全部OK。
- `library-e2e.log`、`library-e2e-CfO5Ft/results.json`：28项原素材套件。
- `today-e2e.log`、`today-e2e-krMu4o/results.json`：56项原今日套件。
- `extra-flow.log`、`library-e2e-jA5h9C/results.json`：含原28项+新增12项的40项。
- `structured.log`、`structured.json`：额外结构化内容4项。
- `formal-get.json`：全部正式测量、CSP、具体今日id次序、导航和浏览器请求方法；所有正式请求均GET。
- `migration-first/second.json`、`migration-review.json`：两次迁移逐条hash、真实全文抽样、8附件、97 refs、三库SHA、规范id和EXPLAIN。
- `http-review.json`：实际Handler随机端口并发、403、删除、只读旧库SHA、今日排序与feedback。
- `static.json`、`server.py.diff`、`today_view.py.diff`、`test_today_view.py.diff`、`workspace-nav.js.diff`：范围及SHA。
- `formal-process.txt`：PID及启动时间，本次未重启。

保留一项复核脚本自身的首轮失败：`structured-harness-first-failure.log`。初版用JSON字符串比较对象，键排序不同导致假失败；改为深值比较后4/4，未改业务实现，也未放宽字段值或文本断言。原始失败与最终脚本均保留。

本轮只读复核完成；需实施者修复M1后，针对该性能项复验。其他已通过项可复用本报告证据，除非返修影响到相应行为。
