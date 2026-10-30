# H5 复核合同：素材库每日导出 + platform_registry.py（复核窗口 B）

## 对象
实施单 task_2249a9e3c751，报告 coordination/impl-hub-h5-report.md（实施 term_02017284）。原合同 coordination/impl-hub-h5-contract.md（字段数以 7 为准：id/title/url/platform/source/tags/saved_at + 顶层 generated_at；合同写 8 是笔误）。口径：status IN ('kept','used') 且 COALESCE(trashed_at,'')=''。
改动文件：platform_registry.py、scripts/library_export.py、tests/test_library_export.py、tests/test_platform_registry.py；真实产物 ~/grok-bot-reports/library-export.json；调度任务 65dd58b0d5c54db28cb360580f83ada0（每天北京时间 08:30），立即运行记录 4492f1d4c3d74235935cd21cdc368522。

## 必查
1. SHA256 与报告一致（4 个文件）。
2. 跑定向：`../article-importer/.venv/bin/python -W error::ResourceWarning -m unittest tests.test_platform_registry tests.test_library_export -v`，应 22P。不跑全量。
3. 真实口径对账：sqlite3 -readonly 对 workspace/items/items.db 直接 SQL 数 kept/used 未回收站条数，应等于导出文件条数（101）；回收站那条不在导出里；new/dismissed 不在。
4. 字段隐私：导出每条恰好 7 键，没有正文/笔记/summary；grep 导出文件与调度运行记录无 sk-/Bearer/API_KEY。
5. W1 反例：sources_json/tags_json 为 null、空串、坏 JSON、JSON 字符串（非列表，如 "\"Grok\""——确认不会被拆成单字符）、含非字符串元素；id 大小写前缀、无冒号、空。注意真实库里有原值 ["G"]、["L"]（兴趣栏简写）：确认导出原样保留是合同允许的行为（合同要求不改写来源名称），写进报告供主控判断即可，不算缺陷。
6. W2：测试不写真实 ~/grok-bot-reports、真实 items.db、真实 tasks.db；patch 全部撤销。可在 /tmp 副本做一次变异（删掉回收站条件），确认测试能报 F。
7. 原子写：读脚本确认同目录临时文件 + fsync + os.replace + 失败保留旧文件；可在 /tmp 注入失败验证旧文件不变。
8. 全仓唯一：rg 'def derive_platform' 只有 platform_registry.py 一处。
9. 隔离 Playwright：在真实 8766 设置页/调度页能看到该任务与运行记录 succeeded（只读访问，不点立即运行以免覆盖；如必须点，先问主控）。截图存 /tmp/review-hub-h5/。

## 约束
不改任何文件（复核只读，产物只写 /tmp 与报告）；不提交不推送；不重启 8766；只用隔离 Playwright，禁止 computer use；禁止 launchctl print / ps eww / env / 打印 plist。视频实验室保护边界不动。
## 交付
coordination/review-hub-h5-report.md，开头「决定要点」表（≤10 行：结论 通过/不通过、H/M/L 列表、核对 SHA、测试数字、证据路径），完成后 worker_done。
