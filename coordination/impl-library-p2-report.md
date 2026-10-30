# impl-library-p2 实施报告

## 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **通过（实施者自测，待独立复核）**；合同 0.1 与反例 1–16 已逐条验证。 |
| H / M / L | **0 / 0 / 0 未关闭缺陷**；两次部署期间首查超时的原始失败与处理见第 5 节，不隐去。 |
| 文件与 SHA256 | 13 个代码/测试文件，逐份完整 SHA256 见第 2 节；另交付本报告与 3 张截图。范围外 tracked 增量 0。 |
| 迁移 | 人存资料 59 + 卡片 19 + 缺失原文占位父素材 1 = **79 条**；18 个回收项全部合并；0 跳过；重复迁移逐条 hash 相同。 |
| 性能 | 正式首屏 **0.25735, 0.03348, 0.0291 秒**；两列表各 5 次均 <1 秒，4 个合同词各 3 次均 <1 秒，完整数字见第 5 节。 |
| 测试 | Python **50/50**（素材 19、迁移 8、今日 23）；素材库隔离浏览器 **28/28**；正式页面 **18/18**；1 期浏览器 **56/56**；旧页定向 **4/4**；拖放 **12/12**。 |
| 运行 | 8766 最终 PID **18529**，启动时间见第 6 节；已先重启加载代码，再用 `/tmp` 独立 profile 的 headless Playwright 验收。 |
| 交付边界 | **未提交、未推送**；不操作黄总桌面或 Chrome；不写 Buffer/X/R2；三个旧库在实际迁移与新接口验收窗口 SHA256 均不变。 |

## 1. 实际实现

- `items_store.py` 独立管理 `workspace/items/items.db`，人工素材与 29 万份检索语料分开。完整原文、长文、串推、引用、媒体、分析、附件路径及 metadata 均保留；sources / legacy_refs 合并，人写的 note/status/feedback 不被迁移或重复收藏覆盖。
- items FTS5 使用 trigram，覆盖标题、正文、长文、作者、收藏理由、标签；SQL 触发器维护。来源、标签、状态、回收状态有索引；另外用 `item_short_terms` 的主键索引支持 1/2 字短词，避免 trigram 对短词恒无结果，也不做全表 LIKE。写事务 `BEGIN IMMEDIATE`，同 id 并发不丢来源。
- `/api/items*` 提供摘要分页、全文、收藏、回收站/恢复、彻底删除；只有回收站内条目可彻底删除。cards 子条目随父素材处理；被其他素材引用时明确拒绝。补充 `/note` 与 `/use` 动作支持页面编辑收藏理由和记录拿去写。所有写路径沿用同 Origin + `X-Article-Studio: 1`；DELETE 单独同样校验。
- 资料检索只读连接旧库，长词走 `document_fts MATCH + snippet()`；不投影 body、不 COUNT 全库。短词只用标题 LIKE，优先新 rowid、LIMIT 51（第 51 条仅用于判断下一页，返回最多 50）。结果排除 document_trash，总数显示 `≥50` 等下界，未伪造精确总量。
- 素材库「我的素材 / 资料检索」共享 list-reader 与 J/K；搜索 300ms 防抖；来源/类型/标签/状态筛选；编辑理由、拿去写、回收站、恢复、二次确认彻底删除；资料只提供「存为素材」。所有标题、正文、note、snippet 使用文本节点；snippet 仅内部标记转换为 `mark`，绝不注入 HTML。
- 今日「存 / 拿去写」写入 items，同时保留原反馈文件链路。收藏权重封顶 3，原高收藏条件/未知收藏倍率/同热度时间顺序不变。
- `POST /api/knowledge/reference` 的原成功分支未改；只在成功响应装配处追加一行 best-effort 镜像调用，镜像失败仅记录日志，不影响原回执。`server.py` 其余增量只有 items/library 路由、DELETE 装配及 library 同 today 的 https 图片 CSP 例外。
- 导航「今日」之后新增「素材库」，knowledge/intake/cards 保留原路径，移入底部「旧版」分组。

## 2. 文件与范围

基线：Git HEAD `22218a71757b1217acce5491e754152e29031ab8`，开工时已有多条其他任务的未提交修改，未覆盖。开工前的完整 tracked diff 与 server/nav/today/test_today 原副本：`/tmp/library-p2-baseline/`。对比开工 diff，除本合同 server/nav 以外的 tracked diff 块全部相同。`list-reader.js` 新增可选 `renderDetail` 参数并用它渲染，另支持动作回执 notice（用于准确提示取消删除）；今日默认仍为原渲染器与原成功文案；item-card.js、today.js 无改动。

| 文件 | SHA256 |
|---|---|
| `items_store.py` | `e3c5b9193d55483c7553d7d2c6ff2511cdc04fca4addb61b83381956401133ee` |
| `scripts/migrate_to_items.py` | `c0808ec13775eba3f7e5a6b88ffe2aa647f6226d1204f86198c884ef1fdc94a4` |
| `public/library.html` | `4c23e64c78b25b1432fc444a446b6a9304b1a71abb117d848c2bc370f47830b1` |
| `public/library.js` | `ae24e4f1f050c654ef878b74d25692a3df792de7cb7a3a9129914974617462cc` |
| `public/library.css` | `d5c67727f0499ae2428d08b116ad2d70e5b8a74450e781f92d7db53babd847ae` |
| `public/components/list-reader.js` | `146e4266822e54346c11b4c36e62f1f955f85ec262ec5661ee17038d176b6b34` |
| `today_view.py` | `93346a2c14e9e6166ec758399bdea97c980f750a811812987b82945b62d3de86` |
| `server.py` | `9e99206604c7fe4669cebaf46a98c108f31992349821112c7beabce51c38a2a3` |
| `public/workspace-nav.js` | `5f544aad2e324cc2911cc130700cba441c25e294635ba9323ea35130a0fa1781` |
| `tests/test_items_store.py` | `cd38c390a748f49009de2119cc13ed9f03c2ef17b52b54a784eebdc470988dcb` |
| `tests/test_migrate_to_items.py` | `397e2e1610056c528a1f864f1031d8e7b0eb6a01e791d5d11eda170fafc77b21` |
| `tests/test_today_view.py` | `cd25dd6d316c0482d21e174b683f75df0265fdd2a3a929df0015c61db9bfea75` |
| `tests/e2e/library_page.cjs` | `ed0eea4162d2af2029bfd4db3d1ff91bceb440eaedc164b7952a3b8036811072` |

保护文件（开工与交付一致）：

```text
7c2231f892375dbc8b2b9afcec4094f9c9c9fed8781d654d3b12e5e5d50e84e4  knowledge_store.py
46abe88b501643998c648a5da327521046bc423618626b84274c3bdb30ceeeb6  intake_store.py
9e9351f7ef84e00442bfc16261c492cba80c081d8af92b84adba27af787cb69b  intake_cards.py
6701a586e1714f7129ccd76a22ea0d7c48df342fc1baf37271ce4c2c8fbe8b84  clues_store.py
```

`tests/test_today_view.py` 的唯一既有断言变更：`test_case15_heat_beats_newer_timestamp_and_bookmark_weight` 的热度期望从 `2594.05 * 5.12` 改为 `2594.05 * 3`，因为合同要求倍率封顶。其余 22 项既有测试保留；新加反例 16，使今日测试总数为 23。

## 3. 迁移与对账

先复制真实 knowledge/intake/cards 到 `/tmp/library-p2-realcopy/workspace/`，只读这些副本并迁移到其 items 库。清空的是该 `/tmp` 自建 items 库，未就地覆盖共享目录。副本连续两次：79 条、每个 item 内容 hash 完全相同，汇总 hash 均为：

`16f8178b3589c4db8e950dcd7ca78b5d60b16c7148bd93f73de13ffd88d63c55`

正式运行前 items.db **不存在**，已记录“不存在，无需备份”；记录文件 `/tmp/library-p2-baseline/real-items-backup.json`。随后对真实 workspace 运行一次迁移，结果与干净副本相同：

| 旧来源 | 旧条数 | 新增素材 | 合并 | 跳过 |
|---|---:|---:|---:|---:|
| external-references | 19 | 19 | 0 | 0 |
| articles | 19 | 19 | 0 | 0 |
| manual | 10 | 10 | 0 | 0 |
| remio | 11 | 11 | 0 | 0 |
| intake | 18 | 0 | 18 | 0 |
| cards | 19 | 19 | 0 | 0 |
| missing-card-parents | 1 | 1 | 0 | 0 |

补充说明：intake 18 条都与已有 external-reference 文档共用素材，18 个 intake legacy_refs 全保留。卡片 19 张独立保留。每个来源的 `旧=新+合并+跳过`，0 跳过。迁移脚本保留来源之外的数据在旧库，未搬入微信、projects、GitHub、gmail 机器语料。`--dry-run` 在 `/tmp` 临时 items 库计算对账，不写指定 workspace。

异常原始数据单列（协调者通过 Orca ask 明确同意）：

| 卡片旧 id | 缺失 document_id | 保留方式 |
|---|---|---|
| `460440ed2f62497f9f30370a04e0e4d0` | `87a22e6c8d6745e2bbbe11283d75b09d` | 父素材 `kb:87a22e6c8d6745e2bbbe11283d75b09d`，标题/正文开头明确“原资料已缺失，仅保留引用片段”，content_complete=false，legacy_refs 仅指真实 cards 行，缺失 id 写 analysis。 |

实际 SQL 核实为 **1 张**缺失父文档卡片。首次提问误写“2 张”，收到授权后已向协调者补正为 1 张，未造出第二条。全部 97 个迁移 legacy_refs（加正式实测收藏后 98 个）均反查到真实旧行；19 个 cards document 引用均有素材映射（包括上述显式不完整父素材），0 问题。合成回归验证“缺失父文档卡片保留 + 重复迁移不生成重复父素材”。

正式页面实测额外将 projects 的一条资料存为素材，id 为 `kb:30b165ca9f2ffcf5c3ace0d5de5e6a41`，原文和 knowledge legacy_ref 均保留。实测回收后已恢复，最终素材总数为 80（79 迁移素材 + 1 条实测收藏），未把实测收藏混入迁移对账。

## 4. 全部反例自测结果

| # | 结论 | 具体证据 |
|---|---|---|
| 1 旧库不写 | **通过** | 合成迁移、真实 `/tmp` 副本迁移、正式迁移、新接口/正式浏览器验收各窗口三个旧库 SHA 均不变；下附正式 SHA。新路径用 mode=ro，连接级 mmap 不写数据库。 |
| 2 幂等 | **通过** | 真实副本两次均 79 条，逐条 item_hashes 与汇总 hash 完全相同；合成测试逐条比对。 |
| 3 旧 id 与 card 父素材 | **通过** | 真实迁移 97 个 legacy_refs 全反查（加实测收藏后 98 个）、19 个 card 引用全映射，0 问题；唯一旧原文缺失按协调者授权显式保留片段父素材，重复迁移仍一条。 |
| 4 规范 id 冲突 | **通过** | 合成 external-reference `x.com/.../status/100` 与 intake `twitter.com/.../status/100?s=20` 只生成 `x:100`，knowledge/intake 两 refs、两个来源都在；原文不被空 intake 正文覆盖。 |
| 5 原文不截断 | **通过** | 合成 2700+ 字中文 + emoji + 换行/尾部空白，迁移逐字相同，浏览器 `.full-text.textContent` 全等；资料正文与旧全文接口全等。 |
| 6 注入/链接 | **通过** | 标题、正文、note 包含 script/img/svg/onerror，DOM 无对应注入节点；javascript 链接无 href；恶意 snippet 用 alert 关键词强制命中，高亮为 mark，script/img 标签仍文本；新窗口链接 noopener noreferrer。 |
| 7 性能/查询计划 | **通过** | 正式两个列表各 5 次 <1s，4 个指定词各 3 次 <1s，数字见第 5 节；长词 EXPLAIN 为 FTS 虚表索引 + documents rowid 主键查找，非 documents 全表扫描。 |
| 8 短词 | **通过** | 资料 AI 返回 short_query=true，3 次 <1s；微信短词按标题查询，最终 3 次均 <1s。素材短词另走 item_short_terms 主键索引，AI 可查 40 条，非 LIKE。 |
| 9 删除边界 | **通过** | 未进回收站 DELETE 拒绝；被非 card parent_id 引用拒绝；card 子条目随父回收/恢复/彻底删除；恢复后整条素材相同；删除后 FTS 查询 0；浏览器取消不落盘并准确提示“已取消”，确认彻底删除收到 2 次确认。 |
| 10 来源校验 | **通过** | POST items、trash、restore、note、use、DELETE 每条均测缺 Origin / 错 Origin / 缺 X-Article-Studio，共 18 个 403；未创建 items.db。 |
| 11 并发 | **通过** | 单元和 HTTP 两并发 POST 同 id 的 A/G 合并为一条；不同 id 都保留，均返回 201。 |
| 12 镜像故障不干扰 | **通过** | `/tmp` items.db chmod 0444 后，真实原 reference API 仍 201/COLLECTED，原知识库全文可读；捕获镜像 ERROR 日志，items 原数据未变。 |
| 13 今日不回退 | **通过** | 1 期独立浏览器 56/56；新增浏览器从今日点“存”，再打开素材库按 `x:12345678` 找到逐字全文；保留原反馈文件；拿去写复制全文并记录 used。 |
| 14 范围 | **通过** | 开工 tracked diff 与交付比对，合同外 tracked diff 增量 0；4 个保护模块 SHA 一致；无提交/推送。测试证据仅写本轮 /tmp 与 coordination。 |
| 15 旧页可用 | **通过** | 隔离 headless 真浏览器：09 回收删除、10 卡片分组筛选删除、11 知识库独立收藏、29 知识库软删除全部通过；拖放独立套件 12/12。没有既有失败需要放宽。 |
| 16 收藏权重封顶 | **通过** | R9330/B未知 的 `x:9330` 排在 R2594/B412 的 `x:2594` 前；相同 R1000 时 B412 的 `x:2594` 排在 B111 的 `x:9330` 前。真实今日页 `x:2115591064073413118` 在 `x:2115655889428623568` 前，后者 heat=7782.15=2594.05×3；前者最新采样 R≈8305.96，收藏未知。 |

正式迁移窗口 SHA：

```json
{
  "before": {
    "workspace/knowledge/knowledge.db": "28c60bc65d99f8f1875cd2983944eaabad52eece4b73063231c7385fa5e8af99",
    "workspace/inspiration/intake.db": "154788f58cabf55a6da59e9618b208b1606fc16356ff1942fae6beb598054c91",
    "workspace/inspiration/cards.db": "5be22f41771100b3541dc81069c8ce3791845dd068a1384f88d0fa68557c28d7"
  },
  "after": {
    "workspace/knowledge/knowledge.db": "28c60bc65d99f8f1875cd2983944eaabad52eece4b73063231c7385fa5e8af99",
    "workspace/inspiration/intake.db": "154788f58cabf55a6da59e9618b208b1606fc16356ff1942fae6beb598054c91",
    "workspace/inspiration/cards.db": "5be22f41771100b3541dc81069c8ce3791845dd068a1384f88d0fa68557c28d7"
  },
  "unchanged": true
}
```

正式浏览器所有新接口操作窗口 SHA：

```json
{
  "before": {
    "/Users/huangzongning/development/article-studio/workspace/knowledge/knowledge.db": "28c60bc65d99f8f1875cd2983944eaabad52eece4b73063231c7385fa5e8af99",
    "/Users/huangzongning/development/article-studio/workspace/inspiration/intake.db": "154788f58cabf55a6da59e9618b208b1606fc16356ff1942fae6beb598054c91",
    "/Users/huangzongning/development/article-studio/workspace/inspiration/cards.db": "5be22f41771100b3541dc81069c8ce3791845dd068a1384f88d0fa68557c28d7"
  },
  "after": {
    "/Users/huangzongning/development/article-studio/workspace/knowledge/knowledge.db": "28c60bc65d99f8f1875cd2983944eaabad52eece4b73063231c7385fa5e8af99",
    "/Users/huangzongning/development/article-studio/workspace/inspiration/intake.db": "154788f58cabf55a6da59e9618b208b1606fc16356ff1942fae6beb598054c91",
    "/Users/huangzongning/development/article-studio/workspace/inspiration/cards.db": "5be22f41771100b3541dc81069c8ce3791845dd068a1384f88d0fa68557c28d7"
  },
  "unchanged": true
}
```

开工到交付的整个长时段，knowledge.db 的总 SHA 曾因既有后台采集而变化；已只读对照副本，观察到微信文档 291546→291983、sources.synced_at 更新及新微信行。那不是本轮新模块写入。未关停用户采集来制造“整段无变化”；上述严格迁移/API窗口及副本窗口保持字节不变，单独留证。

## 5. 性能与失败记录

正式运行（秒；已加载最终后端、items 索引就绪）：

| 路径 / 查询词 | 连续测量 | 结果 |
|---|---|---|
| `4090` | 0.02056, 0.02241, 0.01973 | 通过 |
| `/api/items` | 0.00806, 0.00779, 0.00572, 0.00663, 0.00745 | 通过 |
| `/api/library/search?q=agent` | 0.01501, 0.01579, 0.01418, 0.01277, 0.01394 | 通过 |
| `agent` | 0.01375, 0.01570, 0.01357 | 通过 |
| `微信` | 0.25804, 0.30441, 0.32928 | 通过 |
| `MiniMax H3` | 0.01164, 0.01253, 0.01058 | 通过 |
| `AI` | 0.00646, 0.00783, 0.00548 | 通过 |

正式首屏 3 次：`[0.25735, 0.03348, 0.0291]`；独立合成首屏：`[0.11305, 0.02814, 0.02862]`。测量从 page.goto 前到真实列表出现且非 busy，不只是 HTTP 回执。截图经人工图像查看：列表留白、分栏、可见 5 条素材、资料高亮与阅读器正常。

长词查询计划（agent / MiniMax H3 / 4090 均如此）：

```text
SCAN document_fts VIRTUAL TABLE INDEX 0:M2
SEARCH d USING INTEGER PRIMARY KEY (rowid=?)
CORRELATED SCALAR SUBQUERY
SEARCH t USING COVERING INDEX sqlite_autoindex_document_trash_1 (document_id=?)
```

`SCAN ... VIRTUAL TABLE INDEX 0:M2` 是 FTS MATCH 索引访问，不是扫描 documents 正文表。短词标题 LIKE 是合同明确要求的例外：会按 rowid 倒序遍历标题并 LIMIT，未声称短词也能使用 trigram MATCH。

保留两次实际失败，不计入通过数字：

1. 优化前，真实“微信”首次查询 **3.31833s**（随后 0.26208/0.25662s），此前干净副本冷查也曾 1.777919s。修为短词优先新 rowid + 连接级只读 mmap，最终正式三次数字见上表。无关闭指标或降低断言。
2. 开发中为现有 items 增加 1/2 字索引，首次在 GET 中补建索引导致首屏 **1.87563s**，随后 0.03219/0.03015s。索引已建完，最终正式三次全部 <1s；从全新 items 迁移时，现版本迁移会在写入阶段预建该索引，部署应先跑迁移再开放页面。**旧版 items 缺索引时的首次就地升级耗时不是普通列表耗时**，不能把这个失败删掉或归成浏览器误差。

早期合成 J/K 检查也曾因断言未等待异步详情而失败；改为等待确切 item id 后双向切换断言，最终通过。未放宽实际切换要求。

## 6. 测试命令、运行与回滚

```sh
../article-importer/.venv/bin/python -m unittest tests.test_items_store tests.test_migrate_to_items tests.test_today_view -q
node tests/e2e/library_page.cjs
node tests/e2e/library_page.cjs --real
node tests/e2e/today_page.cjs
E2E_ONLY=09,10,11,29 node tests/e2e/run.cjs
node tests/e2e/intake_drop.cjs /tmp/library-p2-intake-drop
```

Python 50/50；素材库独立 28/28、正式 18/18；今日 56/56；旧页 4/4；拖放 12/12。未运行全量 discover，不占用全量假模型资源。

正式进程证据：

```text
18529 Thu Oct 29 22:25:03 2026     /opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python /Users/huangzongning/development/article-studio/server.py --port 8766
```

使用现有 launchd 服务 `com.servasyy.article-studio` 执行 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`。最终 PID 与原 PID52356 不同；最终 backend 改完后先重启，再做上述正式隔离浏览器验收。过程中因实际修正发生多次重启，未冒称只重启一次；本合同授权重启，未操作其他进程或账号。

回滚方式：按本轮文件增量恢复开工副本及 list-reader 的渲染器参数与 notice 增量，移走本轮新增页面/模块；旧库从未迁移写入，无需回滚旧库。items.db 初始不存在，应**先备份现库**再移走，避免丢掉验收后用户可能新增的收藏；绝不恢复旧库覆盖正在采集的数据。回滚后按同一 launchd 服务重启。没有实际执行回滚或删除正式实测素材。

## 7. 证据索引

- 报告：`coordination/impl-library-p2-report.md`。
- 正式截图：`coordination/impl-library-p2-screenshots/library-first-screen.png`、`library-research.png`、`library-restored.png`。
- 正式浏览器逐项结果：`/tmp/library-e2e-kKg11q/results.json`。
- 隔离浏览器逐项结果：`/tmp/library-e2e-oqQQ0A/results.json`。
- 迁移对账/每条 hash：`/tmp/library-p2-realcopy/migrate-clean1.json`、`migrate-clean2.json`，正式 `/tmp/library-p2-baseline/real-migration.json`。
- 范围、旧库 SHA、真实 card 映射、查询计划、Python/浏览器日志：`/tmp/library-p2-baseline/`。
- 今日 56 项：`/tmp/today-e2e-wltPOL/results.json`。
- 旧页定向最新证据：`/Users/huangzongning/development/article-studio/coordination/evidence/e2e/2026-10-29T14-27-30-129Z-22589-b97434`。
- 未提交、未推送，交付后停止本轮工作，交由另一窗口独立复核。
