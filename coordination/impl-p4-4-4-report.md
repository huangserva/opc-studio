# 4-4 Store 拆分交付报告

| 决定要点 | 结果 |
|---|---|
| 结论 | 通过（实施自检；按主控方案 A；行数目标未达，原因如下） |
| 新增 H / M / L | 0 / 0 / 0；保留既有 Python 1E、UI 3F、today 凌晨边界失败 |
| 修改 server.py / SHA256 | `a29a9ef5a161e0f028402b6251df57546ee10a3e239f7ea7c4dcbe3328d23c16` |
| 新增 work_store.py / SHA256 | `c9f214e92e3aa8c43a583fa2fb0ffc9baeb47358f406e21325a60c911e12ad14` |
| 拆分范围 | 28 方法迁入 WorkStoreBase；4 方法原留；32 方法正文逐字一致；未改既有测试 |
| 测试 | Python 前后3569项0F/1E/7S；UI前后719P/3F/1S；雷达34/34；Grok30/30；定向247通过 |
| 行数 | 1693 → 1332；要到 <900 还差433行，不额外搬移 |
| 8766 PID | 32836 → 12620；PID STARTED；12620 Fri Oct 30 03:54:38 2026；重启后16页均200、0 pageerror、0 console.error |

任务 `task_e14eef15f058`，Dispatch `ctx_1ba57245f50c`；本报告为实施自检，独立复核另由主控安排。未提交、未推送；未操作黄总真实桌面或浏览器。证据根目录 `/tmp/impl-p4-4-4-baseline/`、`/tmp/impl-p4-4-4-after/`。

## 开工视频选择与主控裁定

开工先写明：**Store 中涉及视频展示/创建的代码选择逐字搬移、行为不变**；generation/视频/lab 的 Handler 路由原地不动。29个保护文件逐项SHA256一致，包括未跟踪的 generation/aicg3d_compiler.py 和相关模板、lab-test、视频测试。Store 32方法逐字对照、整个Handler逐字对照都通过；没有修复保护代码既有失败。

开工用rg和AST列全补丁与源码断言后，发现动态未注册studio模块的清洗补丁无法靠普通搬移保留，**先ask、未直接写代理**。主控明确裁定采用 A：work_store.py 定义 WorkStoreBase，server.Store 继承它，detail/save/reflow/compare 四方法原留；取消 `server.Store is work_store.Store`，改为验证所有原导入、清洗名零引用、基础类不单独实例化。完整答复由Orca ask持久记录，已在开工报告先写裁定。

## 实现与行数

`work_store.py` 承载懒加载、导入扫描、版本提交/恢复、作品创建/列表、合集及只读Markdown迹象检查。原 importer/content 模块装载与 uid/Conflict 随基础业务搬入并由 server 显式导入，保证主类与基础类使用同一对象；没有全局转发代理、循环import或改写函数globals。

四个原留方法共255行，Handler共861行，另有原留清洗兼容入口、imports和启动装配；其中generation/视频/lab的26个原分支共103行。Handler中的CSP、trusted/reply、POST校验、reference副作用钩子和页面CSP特例整体逐字未变。总行数1332，达到<900还需再减433行；依照主控边界如实保留，不为行数额外搬移。

源码审计：`/tmp/impl-p4-4-4-after/source-audit.json`（脚本 `/tmp/impl-p4-4-4-baseline/source_audit.py`）。保护分支逐条定位 `/tmp/impl-p4-4-4-after/protected-handler-blocks.json`。

## T1 导入与补丁兼容：通过（主控新口径）

合同的53处是历史数量；本次实际发现**88处含Store的from-server导入（测试87、脚本1）**，总计100处from-server语句。每个语句的每个名字都实际getattr校验可导入；server.Store仍为正常公开类，继承WorkStoreBase。明细 `/tmp/impl-p4-4-4-after/store-probe.json` 的 imports；开工rg全量 `/tmp/impl-p4-4-4-baseline/full-server-refs.txt`，补丁集中清单 `focused-patches.txt`，AST清单 `test-dependencies.json`。

基础类没有独立实例化：生产rg命中仅work_store类声明、server显式import和Store继承声明。`rg 'markdown_aware_html|MARKUP_|clean_html|SafeHTML' work_store.py` 返回1、零命中，基础类不偷偷导入清洗名。全部校验保存在source-audit.json。

| 开工补丁/源码依赖 | 逐项保留方式与证据 |
|---|---|
| content_markdown:157–159：studio.markdown_aware_html、MARKUP_LITERALS、MARKUP_LINE_RULES | 四方法及所有清洗入口原留；未注册studio模块与server各自依赖自己的全局名；定向与全量覆盖 |
| assistant_format_guide:236–238、545–546、638–640：同上 | 不替换测试；原方法逐字不动；247项定向全过 |
| e2e/content_markdown_fixture:39–40：server.MARKUP_* | 源码未变，公用补丁链仍在原模块；额外探针覆盖同样字面量策略 |
| server_split_imports:82：server.markdown_aware_html | full-wrapper及Store调用仍在server；既有入口测试通过 |
| server_split_imports:89 / today、Grok e2e：server.ROOT | main未动；ROOT仍由main传入server_boot；Grok30/30与今日开工/最终同失败点 |
| server_boot.main / ThreadingHTTPServer：89、101 | main与server_boot未改；原依赖注入测试通过 |
| Handler._post_routes / _drop_ref / _log_resolve / __new__：intake_resolve_logging:234、279、293、295、312 | 整个Handler逐字未变；全量覆盖 |
| self.server.assistant.request_model：write_from_item:111 | server实例assistant对象和请求模型入口未动；定向/全量覆盖 |
| outsourced_tasks:558–596：main的AST接线 | main逐字未变，原AST与执行接线测试全过 |
| twitter_radar_monitor_page:607–616：server.py雷达路由源码 | 雷达/feedback路由仍原位原文；Handler完整对照通过；这项不依赖Store位置 |
| server_domain_route_probe：Handler的AST分发 | Handler逐字未变；116条正例、243邻近负例、116安全负例响应正文/调用/头前后一致 |
| 其他普通导入及spec_from_file_location | 完整名单见100条导入和开工全文；没有额外server.Store补丁或Store源码AST断言 |

额外补丁探针共12组：分别对正常server与未注册studio实例，验证save/reflow受markdown_aware_html影响，detail/compare受MARKUP_LITERALS影响，save另受MARKUP_LINE_RULES影响；每组断言输出实际变化、撤销后回原输出，不只检查mock.called。开工与拆后均通过：`store-probe.json`；脚本 `/tmp/impl-p4-4-4-baseline/store_probe.py`。

## T2 懒加载单例与连接：通过

同进程14子store：knowledge, creative_intake, intake_cards, intel, collect, intel_recommend, feedback, clues, clue_pipeline, wechat_source, deals, deals_review, github, events。首轮建立后，连取100轮所有对象均为is同一对象；sqlite3.connect累计创建调用数保持**10 → 10**，重复阶段新增0；lsof的.db打开项**0 → 0**。这里的10是首轮累计创建次数，不冒称10个常驻连接。

负控仅在探针内故意清掉events缓存，测试退出1并报 `AssertionError: events`，证明能抓到重复实例化。`/tmp/impl-p4-4-4-baseline/store-negative.log`；正式代码未被负控修改。前后探针结果均在store-probe.json，完整日志store-probe.log。

## T3 CSP与reference钩子：通过

- **真实HTTP头**：/today.html、/library.html、/publish.html的CSP仅允许原来的https图片特例，/index.html仍为旧CSP；T5脚本逐字断言，头全文保存在user-path/results.json。
- trusted/reply、knowledge/reference入库及副作用钩子、其余CSP代码都随整个Handler逐字保留。
- 定向 `tests.test_server_split_imports test_studio test_content test_content_markdown test_assistant_format_guide test_compare_rich_inline test_knowledge_reference_legacy test_items_store`：**247/247通过**，涵盖旧reference回执、素材镜像副作用、作品保存/恢复和补丁。
- `tests/server_domain_route_probe.py` 对开工与拆后比较116正例+243相邻路径反例+116缺Origin/头安全反例，响应正文/调用/响应头全同，上传闸门仍413。证据两侧routes.json、最终routes.log；不是把假后端对照当成外部服务连通性。

## T4 全量与基线：通过（保留既有失败）

| 检查 | 开工 | 拆后 |
|---|---|---|
| Python unittest discover -s tests | 3569项0F/1E/7S，378.862秒 | 3569项0F/1E/7S，382.905秒 |
| npm run test:ui | 719P/3F/1S | 719P/3F/1S |
| 雷达浏览器 | 34/34 | 34/34 |
| Grok浏览器 | 30/30 | 30/30 |
| today浏览器 | 43项通过后反例15失败 | 43项通过后反例15失败 |

两次全量都先经Orca ask登记独占，跑完发status释放。没有改既有测试、没有跳过/屏蔽失败。Python唯一E两侧都是 `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，`ValueError: 参考素材宽高须为 32 的倍数`。UI三个失败两侧逐名相同：
- 4 幕导演台：初始载入 4 幕卡片且包含默认极客荒岛剧本
- 4 幕导演台：切换题材预设（赛博追杀）即时同步 4 幕剧本
- 4 幕导演台：生成单幕 (15s) 采用 Singularity + Turbo 4步 + 345帧参数

这四项属于受保护视频。today反例15的8小时前/4小时前样本在北京时间凌晨落在昨日，被默认“今天”筛掉；开工快照与最终同样复现，未扩围修复。ResourceWarning与临时目录后台线程的既有警告保留原日志，不隐藏。

各日志为两证据目录的python.log、ui.log、radar.log、grok.log、today.log；逐项回归比较 `/tmp/impl-p4-4-4-after/regression-compare.json`。

## T5 真实用户路径：通过

在 `/tmp/impl-p4-4-4-after/repo` 代码副本上启动随机端口 **http://127.0.0.1:52956**，workspace **/tmp/p4-store-path-FKcwNd/workspace**；独立headless Playwright profile **/tmp/p4-store-path-FKcwNd/profile**。起草使用固定假模型，真实Handler、Store、ItemsStore与版本文件；不调用真实模型或采集。额外loaded-paths.json断言server/work_store/items_store/write_from_item均来自隔离代码副本。

| 步骤 | 实际内容断言 | 截图（/tmp/impl-p4-4-4-after/user-path/） |
|---|---|---|
| 今日 | 1条固定素材x:94444，详情原文逐字相等 | 01-today.png |
| 存 | 真实POST今日反馈返回200；ItemsStore实际保存，full_text逐字相等 | 02-saved.png |
| 素材库 | 点击真实导航，看到1条，API total=1，打开详情原文相等 | 03-library.png |
| 拿去写 | 点击后真实弹窗定位具体article，素材关联作品 | 04-use.png |
| 创作台底稿 | 作品 `article-20261030-103f59ea`，真实v0002，页面与API正文均含完整原文 | 05-base-draft.png |
| 发布复盘 | 页面实际打开，真实API total=0、卡片0，空态正确，不伪造已发布内容 | 06-publish.png |

6步骤全部截图；固定模型释放后起草状态done；pageerror=0、console.error=0、外网请求=0。结果 `/tmp/impl-p4-4-4-after/user-path/results.json`；脚本 `/tmp/impl-p4-4-4-baseline/user_path.cjs`。开工同一路径也通过，证据baseline/user-path/。

## 正式重启与16页

按派工授权用launchctl kickstart重启8766，仅通过允许的launchctl list和ps pid,lstart确认新PID；未使用launchctl print、ps eww、env、未打印服务环境变量。PID证据 `/tmp/impl-p4-4-4-after/pid.txt`。

之后独立headless Playwright context检查正式8766，所有16页HTTP200、主导航6项、0控制台error、0pageerror，并逐页截图。真实服务只允许GET；外网/非GET/collect、intel、clues-wechat连接器请求由浏览器隔离，避免真实采集；不把被隔离请求当成外部连通性证据。知识库与线索页等待实际数据加载完成；部分页把DOM条数/正文与真实API逐项核对。

| 页面 | HTTP | 导航 | 正文字符数 | 控制台/页面异常 |
|---|---|---|---|---|
| today.html | 200 | 6 | 322 | 0/0 |
| library.html | 200 | 6 | 7230 | 0/0 |
| index.html | 200 | 6 | 4725 | 0/0 |
| publish.html | 200 | 6 | 278 | 0/0 |
| deals.html | 200 | 6 | 6808 | 0/0 |
| settings.html | 200 | 6 | 2600 | 0/0 |
| lab.html | 200 | 6 | 56931 | 0/0 |
| clues.html | 200 | 6 | 11141 | 0/0 |
| intel.html | 200 | 6 | 1144 | 0/0 |
| github.html | 200 | 6 | 1294 | 0/0 |
| schedules.html | 200 | 6 | 1442 | 0/0 |
| feedback.html | 200 | 6 | 443 | 0/0 |
| knowledge.html | 200 | 6 | 17127 | 0/0 |
| intake.html | 200 | 6 | 27977 | 0/0 |
| cards.html | 200 | 6 | 18039 | 0/0 |
| events.html | 200 | 6 | 1273 | 0/0 |

DOM/API实际内容核对：

- library.html：{"total": 79, "visible": "79 条", "cards": 50}
- publish.html：{"total": 1, "cards": 1}
- clues.html：{"total": 5203, "visible": "5203 条线索", "cards": 30}
- knowledge.html：{"total": 292885, "visible": "292,885 份资料 · 本机索引", "sources": 14}

完整页面结果与截图 `/tmp/impl-p4-4-4-after/pages/`；脚本 `/tmp/impl-p4-4-4-baseline/pages.cjs`。开工16页截图baseline/pages/。

## G1–G4

| 反例 | 结果 | 证据 |
|---|---|---|
| G1 不只看数字 | 通过 | T5全文/版本/真实存储逐字断言；正式library/clues/knowledge/publish的DOM与API内容数量对照，不以加载壳冒充数据 |
| G2 不钻合同漏洞 | 通过 | 发现补丁与is要求冲突先ask；主控裁定A后再实施；<900未达如实说明，未额外搬移 |
| G3 不自行扩围 | 通过 | 只搬基础业务，四清洗方法与Handler原留；已知视频/时间问题未修；不改测试 |
| G4 文件范围 | 通过 | 开工552个tracked逐文件SHA对照，仅允许server.py有增量；新增work_store.py和本报告；29保护文件全不变 |

最终SHA与范围证据 `/tmp/impl-p4-4-4-after/sha256.json`、source-audit.json。所有产品文件改动均在本次Orca任务范围内；未提交、未推送。无本单待修问题；独立复核由主控另派。
