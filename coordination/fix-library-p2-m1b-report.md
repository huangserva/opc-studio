# 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **通过（实施自测，待独立复核）**；短词已改为全库标题检索，6 个词各 3 次含首查均 <1 秒。 |
| H / M / L | 自检 **0 / 0 / 0**；反例 1–6 全部通过。 |
| 改动 / SHA | 新增 `library_search.py`，改 `items_store.py`、`server.py` 启动一行、`public/library.js` 提示、两份测试；逐文件 SHA256 见下文。 |
| 检索完整性 | 微信 3937、模型 563、视频 281、发布 87、抖音 1141；接口均返回 50 条；鲸基准与接口均为 9 条。 |
| 正式首查 | 新进程静置 **305.011 秒**；18 次最大 **0.388000 秒**，微信首查 **0.321500 秒**。 |
| 全量构建 | 正式 291,983 条；构建主体 **15.543286 秒**，目录 **31,858,688 字节（30.38 MiB）**，后台原子发布。 |
| 自动回归 | Python **60/60**（原 54 + 新 6）；素材库隔离浏览器 **31/31**（原 30 + 新 1）；今日页 **56/56**。 |
| 只读与上线 | /tmp 三个只读窗口 SHA/大小/mtime 全不变；正式 launchctl 重启两次，最终 PID **71674**；未提交、未推送、未用 computer use。 |

## 实现与范围

`workspace/items/research_catalog.db` 的 `titles` 只存 rowid、source、title、modified、trashed，不存正文、链接或文档 id。metadata 保存版本、源文件身份、rowid 水位、trash 成员水位及同步时间。所有知识库连接均复用 `readonly()` 的 `mode=ro` 路径；无知识库 DML/DDL、ATTACH 写入、索引或迁移。

启动一行触发 daemon 后台构建；新库先写同目录 `.building-*` 临时文件，在完整事务结束并关闭连接后 `os.replace` 原子发布。短词查询也可触发缺失/损坏目录的后台重建；失败只记录日志并退回上一轮 0.25 秒 / 8192 条扫描。构建中及不可用状态均有明确提示，限时扫描不再承担正常短词检索。

就绪后，短词用旁路库的标题 LIKE 检索全库，按 rowid 新到旧排序，每页最多 50 条，加取 1 条判断下一页，总数显示下界。只向知识库按命中的最多 51 个 rowid 取摘要元数据，并再次排除当前 trash；不读取 body。长词 FTS + snippet 保持原路径。

查询时距上次完成同步超过 60 秒，就同步 `rowid > watermark` 的新行，512 条一批、0.15 秒预算；水位与插入同事务提交，未追上时下次查询继续。trash 比对真实 rowid 成员，能识别删除、恢复及同数量替换；同步失败保留现有目录。目录读写连接均显式关闭。

本次 `server.py` 相对于开工副本仅增加 `__import__('library_search').start_catalog(store.workspace)` 一行；原有其他在途改动保留。`knowledge_store.py` 未改。没有改今日页、采集器、模型或发布逻辑，也未提交、推送。

## 反例 1：正式结果与只读全表基准

首查测完后才运行全表基准，避免本窗口提前用基准查询预热。SQL 在 `mode=ro` 连接的读事务中执行：

```sql
SELECT count(*) FROM documents d
WHERE title LIKE ?
AND NOT EXISTS(SELECT 1 FROM document_trash t WHERE t.document_id=d.id)
```

| 词 | 全表命中 | 接口三次返回数 | 页面下界 |
|---|---:|---|---|
| 微信 | 3937 | 50 / 50 / 50 | ≥50 |
| 模型 | 563 | 50 / 50 / 50 | ≥50 |
| 视频 | 281 | 50 / 50 / 50 | ≥50 |
| 发布 | 87 | 50 / 50 / 50 | ≥50 |
| 抖音 | 1141 | 50 / 50 / 50 | ≥50 |
| 鲸 | 9 | 9 / 9 / 9 | ≥9 |

五个常用词均符合 ≥50 时返回 50 的验收规则；稀有「鲸」9 条完全一致。每次响应 `catalog_state=ready, partial=false`。正式页实际输入微信和鲸，确认具体结果与「已搜索全库标题」提示，截图已查看。原始数据：`formal.json`、`baseline.json`，脚本 `formal.cjs`、`baseline.py`。

## 反例 2：静置后含首查延迟

最终重启时间 2026-10-29 22:56:42（Asia/Shanghai），PID 71133 → 71674；静置 305.011 秒后才打开素材库并依次执行下表请求。计时从隔离真实浏览器 fetch 开始到完整 JSON 读取结束；本窗口没有在此静置期间请求正式资料检索、运行正式全表基准或预热查询。没有清 OS 页缓存，也不能排除其他用户独立访问；这里的首查指本次新进程与本窗口该词的首次请求。

| 词 | 首查（秒） | 第 2 次（秒） | 第 3 次（秒） |
|---|---:|---:|---:|
| 微信 | 0.321500000 | 0.260500000 | 0.241900000 |
| 模型 | 0.245200000 | 0.249000000 | 0.230200000 |
| 视频 | 0.274400000 | 0.271100000 | 0.269500000 |
| 发布 | 0.322100000 | 0.280100000 | 0.276700000 |
| 抖音 | 0.192500000 | 0.185200000 | 0.182900000 |
| 鲸 | 0.388000000 | 0.343600000 | 0.320800000 |

18 次全部 <1 秒；最大 0.388 秒。基准全表 LIKE 的微信计时另为 3.454052 秒，这个慢查询只用于验收对照，不是用户接口路径。

## 反例 3：全新构建、提示与恢复

正式库首次测试前确认旁路库不存在，再执行 launchctl 重启：PID 52890 → 71133，启动 2026-10-29 22:56:25。后台从 291,983 行资料构建完整标题目录，rowid 水位 292695，trash 1 条（rowid 185037）。metadata 记录构建主体 15.543285834 秒；从重启到外部确认文件完整可见为 16.355 秒，包含服务启动和浏览器操作。原子发布后的文件为 31,858,688 字节。

构建期间真实正式 GET 返回 HTTP 200，0.254900 秒，`catalog_state=building, partial=true, scanned_count=4370`；此时微信返回 0 是明确标记的临时降级结果，构建完成后同词返回 50。`during-build.json` 的 data 为接口原始证据；该文件的 notice/首次 formal-build.png 采样早于输入 debounce 完成，不用它们证明提示。提示改由独立隔离浏览器真实请求验证：后台构建用测试门闩暂缓，输入鲸后明确显示「标题目录正在后台构建」「结果可能不完整」，释放门闩并完成构建后，同词查到扫描窗口之外的旧资料 `sparse-0`。截图 `/tmp/library-e2e-O8AqAi/library-building.png` 已查看，提示位于底部且可见。

损坏目录单元反例也通过：写入破损 SQLite 内容后，查询仍返回真实降级数据并触发重建，重建后 `catalog_state=ready`。同步故障反例证明已有目录结果继续可用。

## 反例 4：真实 60 秒间隔增量与 trash

使用正式 knowledge.db 的 `/tmp` APFS 独立副本；所有测试写入只发生在 `/tmp/fix-library-p2-m1b/copy/workspace/knowledge/knowledge.db`。

- 副本全量构建 0.539646291 秒（文件缓存状态与正式首次不同）。
- 新插入 `catalog-m1b-incremental-fixture`，标题「鲲鲲目录增量测试」；同步前 0.037792000 秒，0 条。
- 实等 61.010059625 秒后：0.237271625 秒，1 条，具体 id 正确。
- 将该文档加入副本 trash，再实等 61.003772500 秒后：0.238799000 秒，0 条。
- 额外单元反例覆盖：trash 数量不变但成员替换、从 trash 恢复、同步预算耗尽时保留水位且后续补齐、特殊 LIKE 字符字面匹配、分页顺序和来源筛选。

原始结果 `copy-proof.json`，执行脚本 `copy-proof.py`。

## 反例 5：只读保护

副本每个读窗口前后分别核对 SHA256、大小、mtime_ns。窗口之间的插入和 trash 是明确的测试写入；不能跨这两次有意写入比较 SHA。三个窗口均完全相同：

| 读窗口 | SHA256（前后相同） | 大小（前后相同） | mtime_ns（前后相同） |
|---|---|---:|---:|
| 构建和初次查询 | `c52c918d512f9faa865b08117b1be663097001ee426358b08631f638bf72374d` | 1235132416 | 1793285786626419004 |
| 新增后的增量同步查询 | `c52c918d512f9faa865b08117b1be663097001ee426358b08631f638bf72374d` | 1235132416 | 1793285786626419004 |
| trash 后的同步查询 | `c52c918d512f9faa865b08117b1be663097001ee426358b08631f638bf72374d` | 1235132416 | 1793285786626419004 |

代码审查：`library_search.py` 对知识库只有 SELECT、读事务 BEGIN 和原有连接级 mmap 设置；SQLite 写连接的路径均为 `workspace/items/research_catalog.db` 或其构建临时文件。正式知识库可能被后台采集修改，本结论不依赖正式整库 SHA。`knowledge_store.py` SHA256 与开工一致：`7c2231f892375dbc8b2b9afcec4094f9c9c9fed8781d654d3b12e5e5d50e84e4`。

## 反例 6：回归与加载新代码

- `../article-importer/.venv/bin/python -m unittest tests.test_items_store tests.test_migrate_to_items tests.test_today_view -v`：**60/60**，1.312 秒。保留原 54 项（原限时扫描反例明确强制 fallback 后测试），新增 6 项覆盖本轮目录机制。
- `node tests/e2e/library_page.cjs`：**31/31**；最新证据 `/tmp/library-e2e-O8AqAi/results.json`，截图同目录。原 30 项中旧短词部分搜索预期更新为全库预期，额外新增构建期间真实提示断言。
- `node tests/e2e/today_page.cjs`：**56/56**，证据 `/tmp/today-e2e-Wnl46M/results.json`。
- 负向对照：在 `/tmp/fix-library-p2-m1b/negative/` 放入上一轮 `items_store.py`，运行「旧资料在扫描窗口之外」反例，预期 **1 失败**（旧实现返回 `[]`，正确应为 `['old']`）。新实现该反例通过。未在共享工作区切换文件。
- 后端源文件最后修改时间早于最终 PID 71674 的启动，正式接口返回本轮新增 `catalog_state=ready`，lsof 确认 71674 监听 8766。重启及首查记录见 `build-restart.json`、`performance-restart.json`、`formal.log`。
- Python/JavaScript 语法检查通过。全仓 `git diff --check` 只报告既有其他线文件末尾空行（`generation/native_h3.py`、`generation/service.py`、`generation/video_options.py`、`tests/test_video_lab.py`）；本轮未改这些文件。

所有浏览器均独立 `/tmp` profile、headless Playwright；夹具用随机端口，正式浏览器只发 GET。没有使用 computer use 或接管黄总真实 Chrome。

## 交付文件 SHA256

| 文件 | SHA256 |
|---|---|
| `library_search.py` | `dac3e5e3e3655fbdbc6f0547730c719ab230ae2750e5576ddb026c623324cba9` |
| `items_store.py` | `66377decb44f3c043d7e360432b276ee804316e9140ac396a5038fa4c51803ea` |
| `server.py` | `d2f565685baa4cc3db9e9d451996aa4209c69f8f6088bf97eb475777981d4b38` |
| `public/library.js` | `cbdfe71a441f9b2f7d40eedcbdf9bcdf88970f7b54d5942a126db444bc7b9835` |
| `tests/test_items_store.py` | `36f7fad014562b695e4d56927bff68de347bea18a3ae3b7729b6b3fe69ec3e8d` |
| `tests/e2e/library_page.cjs` | `517e833b6fd0cc9289c35aeb993fb11a677a219a95d343c2af69568e0a39a1dc` |

另交付本报告 `coordination/fix-library-p2-m1b-report.md`。开工快照、差异、原始日志及数据在 `/tmp/fix-library-p2-m1b/`；源 HEAD 保持 `22218a71757b1217acce5491e754152e29031ab8`。`items_store.py` 等原本就是 untracked，不把 untracked 状态误称为无改动。

已知能力边界：目录按合同仅增量同步新 rowid；既有 rowid 的原地标题修改不在此轮自动同步范围，需要删除旁路目录重建。trash/恢复按同步间隔更新，新增量很大时按请求预算分批追平；即时 hydration 仍排除当前 trash。初次构建或目录损坏重建时，临时结果可能不完整且会提示。未发现合同内未解决阻断项，待协调者安排独立复核。
