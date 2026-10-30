# 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **通过**；合同 9 项已核验，无阻断项 |
| H/M/L | **0H / 0M / 0L**；101/106 为导出后新增数据的时间差，详见下文 |
| SHA 核对 | 4/4 与实施报告一致，复核结束再次核对无变化；完整 SHA 见下文 |
| 测试数字 | 定向 **22P / 0F / 0E**；回收站变异预期 **1F**；独立原子写故障注入 **2P**；元数据补充反例 **7P** |
| 真实数据 | 原导出 **101** 条、7 键白名单；当前只读 SQL **106** 条，新增 5 条均晚于导出；当前库导出至 /tmp 后 **106 条及 ID 集合完全一致** |
| 浏览器 | 隔离 Playwright 真实 8766 设置页→调度页→运行详情通过；运行 succeeded / exit_code=0；页面错误 0，写请求 0 |
| 证据路径 | `/tmp/review-hub-h5/`：unit.log、real-check.json、deep-check.json、mutation.log、browser.json、4 张截图、final-check.json |

## 角色与边界

复核任务 `task_499e82843210` / Dispatch `ctx_8256a074958b`，依据 `coordination/review-hub-h5-contract.md`，对象实施任务 `task_2249a9e3c751`。仅新增本报告，测试脚本、临时源码、测试数据库、浏览器 profile、截图及补充导出均写 `/tmp/review-hub-h5/`。未改业务代码、真实 items.db/tasks.db、真实导出或配置；未提交、推送、重启 8766、点击立即运行，未使用 computer use。

## 1. 文件与 SHA256：通过

| 文件 | SHA256 |
|---|---|
| platform_registry.py | `92fa85d6d129c15ae128051b4d6262a239d6cd90badaefad5903cf33139fc7d5` |
| scripts/library_export.py | `014789e0daaf7dab3cf40f40e793b431702b6faff452fdc2c11dac1250764193` |
| tests/test_library_export.py | `d3921fe71fc0659083fac811b50d1807d97c766a362e11a372252406c7ea39d5` |
| tests/test_platform_registry.py | `4a4dfc61fbfa3386b2a5bfa233dad43ba74aa980267c1f93cb8bb0c8e9d99f10` |

开始、结束均核对，4 文件一致。真实导出 SHA 始终为 `30e67c19b7750d548c3b8c36a3607eb0276391c40c106d88605591124b569ca4`，权限 0644。

## 2. 定向测试：通过

执行：

```sh
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python -W error::ResourceWarning -m unittest tests.test_platform_registry tests.test_library_export -v
```

22P，0F/0E，耗时 0.190 秒；1000 条导出 0.0530 秒。未跑全量。证据：`/tmp/review-hub-h5/unit.log`。

## 3. 真实口径对账：通过，存在已解释的时间差

直接用 `sqlite3 -readonly workspace/items/items.db` 查询：

```sql
SELECT COUNT(*) FROM items
WHERE status IN ('kept','used') AND COALESCE(trashed_at,'')='';
```

当前为 **106**（kept 未回收站 105，used 未回收站 1），kept 总数 106，其中回收站 1 条。原文件为 **101**，generated_at=`2026-10-30T04:07:52.051995+00:00`。原导出相对当前合格集合无多余 ID，缺少的恰为以下 5 条，其存入时间均在导出之后：

| ID | created_at（UTC） |
|---|---|
| x:2115860471215788262 | 2026-10-30T04:13:23.573098+00:00 |
| x:2115923819001192794 | 2026-10-30T04:14:24.582856+00:00 |
| x:2115928993623109648 | 2026-10-30T04:14:41.349529+00:00 |
| x:2115932308138287552 | 2026-10-30T04:14:41.403323+00:00 |
| x:2115864743210791063 | 2026-10-30T04:14:41.463711+00:00 |

不能宣称“当前 SQL=原文件 101”。事实支持：这是每日快照生成后新增的数据，未观察到过滤缺陷。为闭合当前口径，调用正式 `export_library(directory=/tmp/review-hub-h5/current-export)`：真实库连接仍为 mode=ro，输出仅到 /tmp，得到 106 条；再次直接 SQL 对比，条数及完整 ID 集合一致。

真实回收站那条不在原导出，也不在 /tmp 新导出。当前真实库没有 new/dismissed 样本，排除这些状态由合成夹具测试覆盖。101 条 saved_at 均有时区，按真实时间倒序。证据：sql-counts.txt、verify_real.py、real-check.json、current-export/library-export.json、final-check.json。

## 4. 隐私字段与凭据模式：通过

101 条全部恰好为 `id/title/url/platform/source/tags/saved_at` 七键；顶层恰为 generated_at/count/items，count 与数组长度一致。源码显式 SELECT 元数据并按字段白名单构造，不读取正文/summary/笔记等；带私密正文、摘要、笔记、评论的合成测试通过。

对真实导出、该任务全部运行记录（只读 tasks.db 查询）及浏览器所见运行证据检查 `sk-|Bearer|API_KEY`（忽略大小写），命中均为 0，未打印任何凭据。运行输出仅“素材库导出成功：101 条。”。证据：real-check.json、secret-grep.txt（空文件）、full-run-scan.json（含 snapshot 在内的全部运行记录列，1 条记录、0 命中）。

## 5. W1 元数据及平台反例：通过

定向测试覆盖 sources_json/tags_json 的 NULL、空串、坏 JSON、JSON null、空列表、JSON 字符串、对象/数字、混合非字符串元素及原字符串空格；source 合并去重且不改写名称。独立补测 7 组反例，特别确认 `"Grok"` 解析为 `['Grok']`，不会拆为单字符。平台覆盖登记前缀大小写、无冒号、空 ID、空后缀、未知前缀和非字符串。

真实原值 `sources_json=["G"]`（x:2115862466068623609）、`["L"]`（url:484acacc93985e1f）分别导出 `['G']`、`['L']`。这是库中原值，符合“不改写来源名称”的合同，供主控判断，无须作为本单缺陷。证据：deep-check.json、real-check.json。

## 6. W2 测试隔离与变异：通过

逐行检查测试：数据库、输出与缺失数据库路径全部来自显式 `/tmp` TemporaryDirectory，CLI 子进程明确传入 `--db` 与 `--reports-dir`；默认目录测试先 patch reports_dir 到临时目录，没有写真实报告目录的路径。测试不引用 tasks.db、不启动调度。所有 patch 都使用 with 上下文，TemporaryDirectory 用 addCleanup 清理，没有未撤销的 start patch。

在 `/tmp/review-hub-h5/mutation/` 的独立副本删掉 `AND COALESCE(trashed_at, '') = ''`，只跑回收站测试，得到预期 **1F**、退出码 1，明确捕获 `x:deleted-kept` 与 `x:deleted-used`。正式文件 SHA 保持一致，未在共享工作区换版。证据：mutation.log、deep_check.py、final-check.json。

## 7. 原子写与失败保留：通过

源码为目标同目录 mkstemp→fchmod→json.dump→flush→fsync→关闭文件→os.replace，finally 清理临时文件；目标已有时保留权限，新建 0644。原测试验证同目录与调用顺序、0644/0600、replace/fsync 失败和坏时间保留旧导出。

独立在 /tmp 以旧内容 OLD、权限 0600，分别注入 fsync 与 replace 异常，两次均保持旧字节、旧权限且无 `.library-export-*` 残留。证据：deep-check.json、deep_check.py。

## 8. 平台推导唯一：通过

全仓执行 `rg '^def derive_platform'`，唯一命中 `platform_registry.py:def derive_platform(item_id):`；脚本调用该模块，无另一个平台推导定义。登记包含 x/douyin/xhs/wechat/github/linuxdo/web 与 display_name，icon/color 留空。证据：registry-unique.txt。

## 9. 真实浏览器路径：通过

使用 headless Chrome + Playwright 独立 profile `/tmp/review-hub-h5/profile`，真实访问 `http://127.0.0.1:8766/settings.html`，看到任务名称、08:30、周一至周日、已启用、最近成功；点击“完整编辑与运行记录”进入调度页，查看任务详情及该任务运行记录，再打开既有运行详情。截图已目视核对。

- 任务 ID：`65dd58b0d5c54db28cb360580f83ada0`，唯一同名任务；kind=script、frequency=daily、weekdays=0..6、enabled=true、at=08:30、timeout=120；命令只调用 library_export.py，cwd 正确。
- next_run=1793406600，即北京时间 2026-10-31 08:30。
- 运行 ID：`4492f1d4c3d74235935cd21cdc368522`，succeeded、exit_code=0；UTC 04:07:51.767146 开始、04:07:52.231486 完成，输出 101 条。
- 浏览器路由只放行本机 8766 GET；页面实际 25 次 GET，非 GET 尝试 0，页面 JS 错误 0；未点击运行、暂停、保存等写操作。

证据：browser.cjs、browser.json、settings-task.png、schedule-task.png、task-detail.png、run-detail.png，均在 `/tmp/review-hub-h5/`。

## 尚未发生的验证

下一次 08:30 自动触发尚未到时；本次验证配置持久化、next_run 和既有同任务手动成功记录，不宣称已验证未来自动触发。无须为本次复核重启服务或重写真实快照。
