# 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **通过**：M1 第 2 轮定向复验满足合同；无阻断项。 |
| H / M / L | **0 / 0 / 0**。精确 9 条仍显示 ≥9，按既定下界语义不记缺陷，理由见下。 |
| 改动文件 / SHA | 本轮审计只写本报告；被审 6 文件 SHA 均匹配，完整值见正文；`server.py` 相对开工副本只加启动一行。 |
| 完整性 | 微信、模型、视频、发布、抖音、鲸共 6 词，独立全表基准与接口条数、前 50 个 id 顺序全部一致。 |
| 首查 | 静置 306.007 秒；提示、工具、智能、微信各 3 次，最大 **0.346400 秒**，12/12 <1 秒。 |
| 构建 / 降级 | /tmp 正式快照 291,983 条；构建期间真实页面正确提示；完整构建及真实损坏后后台重建均通过。 |
| 增量 / trash / 只读 | 真实等待 61.010 秒后新增可见，61.010 秒后 trash 消失且目录标记为 1；四个只读窗口主文件属性不变，另有三个无 WAL 的落盘窗口补强通过。 |
| 回归 | Python **60/60**；素材库隔离 Playwright **31/31**；今日页隔离 Playwright **56/56**。 |

## 范围与证据

任务 `task_02ca3abbab52`，Dispatch `ctx_d661dd05021c`。本次只定向复验 `task_5a0741452378` 的 M1 及回归，不重开其余已验收项。所有新证据和脚本在 `/tmp/review-library-p2-m1b/`；未改业务代码、未提交、未推送、未重启正式服务，正式 8766 仅 GET，浏览器仅使用 /tmp 独立 profile 的 headless Playwright，无 computer use。

正式监听 PID 71674，启动于 2026-10-29 22:56:42，晚于后端文件最后修改（`library_search.py` 22:55:09，`items_store.py`、`server.py` 22:54:20）；新增返回字段 `catalog_state=ready` 可见。当前 HEAD `22218a71757b1217acce5491e754152e29031ab8`。本轮审计未修改受测文件。

`scope.json` 记录完整 SHA 及范围对照。把当前 tracked diff 与实施前 `/tmp/fix-library-p2-m1b/pre-tracked.diff` 比对，只有 `server.py` 区段发生变化；再与其开工文件逐字比对，仅增加 `__import__('library_search').start_catalog(store.workspace)` 一行。其余受测文件与开工快照之差均在合同允许范围：旁路模块、短词分派、提示与测试。未把全仓其他既有 dirty/untracked 项归到本次返修。

| 被审文件 | SHA256 |
|---|---|
| `library_search.py` | `dac3e5e3e3655fbdbc6f0547730c719ab230ae2750e5576ddb026c623324cba9` |
| `items_store.py` | `66377decb44f3c043d7e360432b276ee804316e9140ac396a5038fa4c51803ea` |
| `server.py` | `d2f565685baa4cc3db9e9d451996aa4209c69f8f6088bf97eb475777981d4b38` |
| `public/library.js` | `cbdfe71a441f9b2f7d40eedcbdf9bcdf88970f7b54d5942a126db444bc7b9835` |
| `tests/test_items_store.py` | `36f7fad014562b695e4d56927bff68de347bea18a3ae3b7729b6b3fe69ec3e8d` |
| `tests/e2e/library_page.cjs` | `517e833b6fd0cc9289c35aeb993fb11a677a219a95d343c2af69568e0a39a1dc` |

`knowledge_store.py` SHA `7c2231f892375dbc8b2b9afcec4094f9c9c9fed8781d654d3b12e5e5d50e84e4` 与开工一致。

## 全库完整性和排序

先完成正式首查计时，之后才通过 SQLite `mode=ro` 源连接备份正式 knowledge.db 为 /tmp 一致性快照。独立基准在该快照上运行，未通过标题目录统计：

```sql
SELECT count(*) FROM documents d
WHERE title LIKE ?
AND NOT EXISTS (SELECT 1 FROM document_trash t WHERE t.document_id=d.id);
```

再单独按 `d.rowid DESC LIMIT 50` 取 id，与正式 GET `/api/library/search` 前 50 条逐条比对；6 词全部相同，`partial=false,catalog_state=ready`。

| 词 | 全表命中 | 接口条数 | total_label |
|---|---:|---:|---|
| 微信 | 3937 | 50 | ≥50 |
| 模型 | 563 | 50 | ≥50 |
| 视频 | 281 | 50 | ≥50 |
| 发布 | 87 | 50 | ≥50 |
| 抖音 | 1141 | 50 | ≥50 |
| 鲸 | 9 | 9 | ≥9 |

前 3 条排序抽查如下。这里“新到旧”依照实施合同的入库 rowid 顺序，未把原始发布日期与入库顺序混同；所有 rowid 严格递减。

- 微信：`11b7dd4486633a6a729bf575b2703ca5`（rowid 137975，modified `2026-10-05T17:07:11+00:00`） → `b427521755befbc79cfc76c96a53ed63`（rowid 137974，modified `2026-10-05T16:52:21+00:00`） → `ada1198b61e6f8d26805cd3741263b3d`（rowid 137970，modified `2026-10-05T16:46:34+00:00`）。
- 模型：`386fb908097aaba9aef19fb0025f2125`（rowid 246232，modified `2026-10-19T23:24:01+00:00`） → `b4b50c835dafe6483e8407bc168e0de4`（rowid 239648，modified `2026-10-17T17:47:35+00:00`） → `a261dac064b1e6325967574bf76aea79`（rowid 239647，modified `2026-10-17T17:47:29+00:00`）。
- 视频：`5679672ab93eb60a14a42a182cc58f12`（rowid 266202，modified `2026-10-26T02:19:04+00:00`） → `feac43e51320be2563e8adc194acfb4d`（rowid 266090，modified `2026-10-26T02:02:42+00:00`） → `716986a69b7e24da4a3af31c16b4b3e7`（rowid 235017，modified `2026-10-16T13:15:53+00:00`）。
- 发布：`0caa9514e6a51054ec45e499ecfe47eb`（rowid 157831，modified `2026-10-09T08:29:34.657577+00:00`） → `9573d4090b1524e3f46d883068dfd0b1`（rowid 137629，modified `2026-10-05T11:38:14+00:00`） → `38ed6f21c6442942afbd9da9504ed1f6`（rowid 136921，modified `2026-10-05T00:27:46+00:00`）。
- 抖音：`9b35ed06f8adb9069cc348ce6f983b6c`（rowid 291181，modified `2026-10-29T10:35:47+00:00`） → `9bfe66483d790f398ec17f805731769b`（rowid 291180，modified `2026-10-29T10:35:40+00:00`） → `c9428c0a085df05ab0eedb2bcb2d8798`（rowid 288778，modified `2026-10-29T06:48:12+00:00`）。
- 鲸：`37ccba65751551fa4119c3968bdcc805`（rowid 282764，modified `2026-10-28T14:48:01+00:00`） → `571c8f1afefc272fb71f9ec4834d3e5b`（rowid 275098，modified `2026-10-28T05:11:02+00:00`） → `3627ccd5dd1123022a74500964c977cd`（rowid 274293，modified `2026-10-28T03:25:30+00:00`）。

原始基准、id、modified 与计时见 `copy-proof.json` 的 `baseline`。

## 未预热首查

第二次静置从 `2026-10-29T15:10:51.000Z` 起，到首个正式检索前 306.007 秒；期间本窗口不请求正式资料检索、不扫正式 knowledge.db、不构建正式目录。三个新词为「提示」「工具」「智能」，本轮此前未查过，另加「微信」；顺序逐词各查三次。每次从浏览器发起 fetch 至 JSON 完整读取结束计时，单位秒：

| 词 | 首次 | 第二次 | 第三次 |
|---|---:|---:|---:|
| 提示 | 0.346400000 | 0.274200000 | 0.253300000 |
| 工具 | 0.297200000 | 0.281500000 | 0.271500000 |
| 智能 | 0.170300000 | 0.170400000 | 0.172300000 |
| 微信 | 0.252800000 | 0.249800000 | 0.260900000 |

12 次均 HTTP 200、ready、partial=false，均 <1 秒。原始证据 `performance.json`、`performance.log`、`performance.cjs`。首次测时脚本将原生 fetch 的 `status` 属性误当方法，首个「推特」请求后脚本失败；该次不计验收，错误保留在 `performance-attempt1.log`，修正脚本后重新完整静置并换新词，未改产品代码。

边界：本次没有重启正式服务或清 OS 页缓存；静置保证的是本复验窗口无检索/预热操作，无法证明其他用户或后台任务完全无活动，不把它称为物理冷盘测试。

## 全新构建、损坏重建与页面

使用正式 knowledge.db 一致性快照，/tmp 目录初始不存在 research_catalog.db；启动实际 HTTP Handler 于随机端口 `http://127.0.0.1:58564`，显式触发同一 `start_catalog()` 启动钩子。只对副本构建设置门闩，保留构建中状态以供真实浏览器观察，截图后释放，不伪造接口返回。

- 页面输入「微信」，HTTP 200、`catalog_state=building,partial=true`；可见“标题目录正在后台构建”“结果可能不完整”，与接口一致。`copy-building.json`、`copy-building.png`；另有 8300 行夹具的 `regression-building.png`。
- 释放门闩后，291,983 条标题完整发布，微信返回 50、ready、partial=false；构建主体记录 0.758092 秒，目录 31,858,688 字节。该时间是本次副本缓存环境的结果，不冒充正式冷启动耗时。
- 向 /tmp 目录写入真实破损 SQLite 字节，实际 GET 自动触发后台重建，首个降级响应 HTTP 200、building、partial=true；最终 ready，微信仍完整 50 条。此路径未 mock Thread 或返回值。
- 正式页面真实输入「微信」「鲸」均看到结果，ready 提示“已搜索全库标题”；截图 `formal-微信.png`、`formal-鲸.png`，原始 DOM/响应为 `screens.json`。

对「鲸」全量恰为 9 条、无下一页却显示“≥9”：**不记 L**。实施合同明确要求短词“总数显示下界”，≥9 对全量 9 条也为真，没有伪造更多结果，且页面明确已搜索全库标题；显示精确 9 更直接，但属于非阻断文案优化，不改写既定验收标准。

## 增量、trash 与 knowledge.db 只读

仅向 `/tmp/review-library-p2-m1b/copy/workspace/knowledge/knowledge.db` 插入测试文档 `review-m1b-new-document`（标题“麒麟复验增量”）。同步前搜索「麒」无该 id；真实等待 61.010082 秒后实际 HTTP 查询找到该 id。随后仅在副本 document_trash 插入该 id，真实等待 61.010092 秒后查询消失，同时直接检查旁路 titles 新行的 `trashed=1`，证明不只是 hydration 临时过滤。

源库读路径代码审查：`items_store.readonly()` 使用 `mode=ro`（只读 immutable 回退）；旁路模块对 source 只有 SELECT、只读事务 BEGIN 和只读查询的 progress handler，连接级 mmap 设置不写源文件。DML/DDL 目标均为独立旁路库/构建临时库，没有向 knowledge.db 写入的 DML/DDL/ATTACH。新 rowid 按 512 一批、0.15 秒预算、水位与目录写入同事务提交；trash 比对真实 rowid 成员，恢复/同数量替换由回归覆盖。

四个只读窗口分别在故意插入/trash 之前或之后重新立基准，**没有跨有意写入比较 SHA**；每个窗口前后 SHA256、大小、mtime_ns 完全相同：

| 只读窗口 | 前后相同 SHA256 | 字节数 | mtime_ns |
|---|---|---:|---:|
| 构建与查询 | `394e8b5799306c6151a2b979a6552d3180268383985703c501508b69601d001c` | 1235132416 | 1793286962356247592 |
| 损坏重建 | `394e8b5799306c6151a2b979a6552d3180268383985703c501508b69601d001c` | 1235132416 | 1793286962356247592 |
| 增量同步 | `394e8b5799306c6151a2b979a6552d3180268383985703c501508b69601d001c` | 1235132416 | 1793286962356247592 |
| trash 同步 | `394e8b5799306c6151a2b979a6552d3180268383985703c501508b69601d001c` | 1235132416 | 1793286962356247592 |

原始快照保留 WAL 模式，因此上表主文件 SHA 在测试写入之间也相同：写入首先落在 WAL，进程关闭后才 checkpoint；**这四组主文件属性不能单独证明 WAL 无变化**。为补强证据，待原测试进程退出后，仅对 /tmp 副本 checkpoint 并切到 DELETE journal，再以三个独立读窗口复验；窗口之间故意插入文档、插入 trash，各自产生不同主文件 SHA（证明检测可观察写入），每个读窗口内 SHA/大小/mtime 不变且全程无 WAL 文件。此补验只把副本目录的 last_sync 置零来命中同步分支，真实超过 60 秒的自动同步仍以此前 HTTP 验证为证，未重复或替代其计时。

| 补强只读窗口 | 前后相同 SHA256 | 字节数 | mtime_ns |
|---|---|---:|---:|
| 落盘副本构建与查询 | `f8fa678083198fcefec3fbd323f7b861f84c9fa211cc98ae2d532071013260b5` | 1235132416 | 1793287186895644714 |
| 落盘副本增量同步 | `d5ea79d17e708a8af95ca38c7f714555645f214cc37b255e15887b415663a08d` | 1235132416 | 1793287201329299211 |
| 落盘副本trash同步 | `5508a5a0f18e8c41491c9cd74c8483a68b96d8c7434db2986ea938daa4587e98` | 1235132416 | 1793287202356591601 |

补强原始证据：`readonly-proof.py`、`readonly-proof.json`、`readonly-proof.log`。补强脚本最初的测试插入漏填源库必填 kind 字段而失败，已补齐测试数据后重跑通过，产品代码未改。

`copy-proof.py`、`copy-proof.json`、`copy-proof.log` 保存全部响应、间隔与前后属性。

## 独立回归与结论边界

- `PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python -m unittest tests.test_items_store tests.test_migrate_to_items tests.test_today_view -v`：60/60，1.392 秒，日志 `python-tests.log`。
- `node tests/e2e/library_page.cjs`：31/31，随机端口与 /tmp profile；日志 `library-e2e.log`，原始结果归档 `library-results.json`，原始运行目录 `/tmp/library-e2e-7PHHeJ/`。
- `node tests/e2e/today_page.cjs`：56/56，随机端口与 /tmp profile；日志 `today-e2e.log`，原始结果归档 `today-results.json`，原始运行目录 `/tmp/today-e2e-3pFHec/`。

以上测试运行时未使用 `--real`；正式页面另做只读 GET 浏览器检查。没有跑全仓 discover、未终止他人进程。本次范围内无未解决 H/M/L。已有 rowid 的原地标题修改不会自动增量更新，按已约定边界需重建目录；本次不扩为新要求。
