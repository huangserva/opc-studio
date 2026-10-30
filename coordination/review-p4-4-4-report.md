# 4-4 Store 拆分独立复核

| 决定要点 | 结果 |
|---|---|
| 结论 | 通过（按主控方案 A）；逐条结论见下文 |
| 新增 H / M / L | 0 / 0 / 0 |
| server.py / SHA256 | `a29a9ef5a161e0f028402b6251df57546ee10a3e239f7ea7c4dcbe3328d23c16` |
| work_store.py / SHA256 | `c9f214e92e3aa8c43a583fa2fb0ffc9baeb47358f406e21325a60c911e12ad14` |
| 逐字与保护范围 | 32 方法、整个 Handler 逐字相同；552 tracked 仅 server.py 改动；29 保护文件相同 |
| 测试 | Python 3569 项 0F/1E/7S；UI 719P/3F/1S；与基线比较见第 6 项 |
| 浏览器 | 独立 profile、随机端口 56560；主链路 6 步、6 张截图，核对实际正文与底稿 v0002 |
| 正式服务 / 行数 | 8766 仅 GET，未重启；PID 12620；server.py 1332 行，获准例外原因属实 |

复核任务 `task_fbacea0e183e` / Dispatch `ctx_c23b145467f1`；被复核实施任务 `task_e14eef15f058`。按主控方案 A（`coordination/work-queue.md` 4-4 问答）验收，取消旧 `is` 要求。未改代码、既有测试或正式 workspace，未提交推送，未使用 computer use。

独立证据目录：`/tmp/review-p4-4-4/`；基线：`/tmp/impl-p4-4-4-baseline/`。复用了实施方探针脚本，但重新执行、检查断言，并独立补充源码对照、间接路径、策略矩阵、reference 内容验证和正式 CSP 请求。

## 1）逐字搬移：通过

从开工快照 `repo/server.py` 用 AST 定位，并取带装饰器、原缩进和换行的原始文本逐字比较，32/32 方法一致：28 个位于 WorkStoreBase，detail/save/reflow/compare 4 个保留在 server.Store。Handler 454–1314 的全部 861 行与快照逐字一致。其余 14 个原顶层函数/类（含 uid、Conflict 和 main）也逐字一致，见 other-functions.json。不是仅比较 AST 或函数数量。

证据：`source-audit.json`（逐方法目的地、相等结果和 SHA256）。`server.Store` 明确继承 `WorkStoreBase`；生产代码中基础类名称仅出现于定义、显式 import 和继承声明，没有单独实例化。

## 2）清洗 patch：通过

分别实际加载正常 `server` 和 `spec_from_file_location('studio', ...)` 的未注册模块，后者不进入 `sys.modules`。不改既有测试，观察保存文件或返回正文发生变化，并在撤销 patch 后确认恢复。

| 路径 | 实测 patch | 输出验证 |
|---|---|---|
| save | markdown_aware_html、MARKUP_LITERALS、MARKUP_LINE_RULES | manifest 正文实际变化并恢复 |
| reflow | markdown_aware_html、MARKUP_LITERALS、MARKUP_LINE_RULES | 重排后的 HTML 实际变化并恢复 |
| detail / compare | MARKUP_LITERALS、MARKUP_LINE_RULES | 响应 HTML / 对照内容实际变化并恢复 |
| WorkStoreBase.create → Store.detail | safe_html、MARKUP_LITERALS、MARKUP_LINE_RULES | 真实扫描/提交后返回正文变化；撤销 patch 再读取恢复 |

`detail/compare` 调用 safe_html，不走 markdown_aware_html；据实际调用链分别验证。独立 AST 调用检查加上 ContentMixin / PublishingMixin 源码阅读，WorkStoreBase 间接进入 server 清洗的路径只有 create→detail。为让空白 create 覆盖正文清洗，在探针中将其扫描输入固定为带字面量段落，仍执行真实 scan/commit/detail，没有替换产品实现。

例如 reflow 的正常输出 `<p>&#42;literal&#42;</p>`，patch MARKUP_LITERALS 后成为 `<p>*literal*</p>`，恢复后回到前者；行首字面量同理为 `&#45; literal` ↔ `- literal`。两个模块均通过。

实际执行 `rg -n 'markdown_aware_html|MARKUP_|clean_html|SafeHTML|html_sanitize|safe_html|from server|import server' work_store.py`：退出 1，零命中；AST import 检查也无 server。证据：`extended/store-probe.json`、`policy-matrix.json`、`extra-policy.json`、`indirect-call-graph.json`；相应探针脚本和日志同目录。

## 3）T2 懒加载：通过

14 个子 store：knowledge、creative_intake、intake_cards、intel、collect、intel_recommend、feedback、clues、clue_pipeline、wechat_source、deals、deals_review、github、events。同进程首次各取一次，再逐个取 100 轮，1400 次 `is` 全成立；sqlite3.connect 累计调用数 10→10，重复阶段新增 0。lsof 中 .db fd 为 0→0，未将累计创建次数误报为常驻连接数。

另在探针进程内故意清除 events 缓存，负控确实以 `AssertionError: events` 退出 1，证明可检出重复实例化；未改任何产品文件。证据：`extended/store-probe.json`、`negative.log`。

## 4）T1/T3 导入、CSP 和 reference 钩子：通过

历史合同写 53 处；实际扫描 tests/scripts 中所有 100 条 from-server 导入语句，含 Store 的 88 条（测试 87、脚本 1），逐个 getattr 验证全部名字可导入，未只核对数量。明细在 `extended/store-probe.json` 的 imports。另查顶层及 publishing 的生产代码，额外验证 write_from_item 的 Conflict 和 publishing/__main__.py 的 Store 均可导入，见 production-imports.json。

正式 8766 只读 GET `/today.html`、`/library.html`、`/publish.html`，均 200，CSP 逐字等于开工快照实测头：三页 img-src 额外允许 https，其他策略相同；隔离浏览器另确认 index.html 保持原策略。`live-csp.json` 保存正式响应头，`user-path/results.json` 保存隔离头。复核前后 PID 都是 12620，未重启服务。

Handler 中 trusted/reply、POST 校验与 reference 钩子逐字未变。全量覆盖现有清洗/导入/reference/ItemsStore 回归；另独立以真实随机端口 HTTP POST reference 验证 201 / COLLECTED，知识库原记录和素材库镜像的标题、正文、收藏理由逐项一致（素材按 URL 规范化为 `url:1df66cd7ea2abd0a`）。只读镜像写入失败不影响原收藏的既有回归也通过。证据：`reference.json`、`reference.log`、`reference_probe.py`、最终 Python 日志。

## 5）T5 真实用户路径：通过

代码在 `/tmp/review-p4-4-4/repo`，workspace `/tmp/p4-store-path-BmCUyR/workspace`，headless Playwright 独立 profile `/tmp/p4-store-path-BmCUyR/profile`，随机端口 `http://127.0.0.1:56560`。使用固定假模型，未调用真实模型；loaded-paths.json 核对核心模块来自隔离副本。

| 步骤 | 实际内容 | 截图（user-path/） |
|---|---|---|
| 今日 | 真实显示固定素材 x:94444；详情全文逐字相等 | 01-today.png |
| 存 | 真 POST feedback 成功，ItemsStore 全文与原文相等；页面出现“已保存” | 02-saved.png |
| 素材库 | 点击导航进入，真实显示 1 条，详情全文相等 | 03-library.png |
| 拿去写 | 点击真实按钮，出现起草状态并打开带 article 参数的新页 | 04-use.png |
| 创作台底稿 | `article-20261030-ad737133`；页面与 API 都显示底稿 v0002，保留来源链接和完整原文 | 05-base-draft.png |
| 发布复盘 | 页和真实接口都为 0 条，页面正确显示尚无发布记录的空态 | 06-publish.png |

已逐张查看 6 张截图。释放假模型后任务状态 done，素材关联作品 ID 正确；pageerror、console.error、外网请求均为 0。未把发布空态当作已有发布内容。证据：`user-path/results.json`、6 张截图、`user-path/server.log`。

## 6）门槛与范围：通过（保留基线既有失败）

Python 全量先通过 Orca ask 登记独占；最终在资源齐备的独立副本执行 `../article-importer/.venv/bin/python -m unittest discover -s tests -v`（实际使用同一 venv 的绝对路径）。UI 在同一副本执行 `npm run test:ui`。

| 全量 | 开工基线 | 独立复核 | 对照 |
|---|---|---|---|
| Python | 3569 项 0F/1E/7S | 3569 项 0F/1E/7S，379.827 秒 | error ID 一致；7 个 skip ID/原因在开工代码逐条复现后完全一致 |
| UI | 719P/3F/1S（723 项） | 719P/3F/1S，13.749 秒 | 3 个失败标题逐项一致 |

Python 唯一 E：`test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，`ValueError: 参考素材宽高须为 32 的倍数`；UI 3F 是初始荒岛剧本、切换赛博剧本、Singularity/Turbo 参数三项既有视频用例。无新增失败。7 个 skip 是 H3 schema 1 项、媒体 API 对照 5 项、微信外部 cleanup helper 1 项。缺 jsdom 时跳过的两项最终实际执行并通过。

原始最终日志 `python-verified.log`、`ui-final.log`；逐项结果 `python-compare.json`、`ui-compare.json`；基线 skip 独立复现 `baseline-skips.json` / `baseline-skips.log`。

复核环境的前三轮 Python 未用于验收：依次发现 pi-runtime/node_modules、coordination 私有夹具、work/x-dom-test/node_modules 未带入，造成失败或额外跳过，均只停止自有测试进程；最终遍历代码树补齐全部 4 处 Node 依赖及私有夹具后完整重跑。UI 早期同因依赖缺失为 718P/4F/1S，补齐后的完整一轮为 719P/3F/1S；相关原始日志全部保留，不掩盖无效轮次。UI 一次中间轮在测试清理阶段等待浏览器连接，只关闭本轮自有 headless Chrome；最终完整 UI 未做此干预。没有改测试来消除失败。

开工 552 个 tracked SHA 逐文件核对，只有 server.py 改动；另对快照副本中已有的未跟踪代码/资源逐文件对照，也只有 server.py 变化。在顶层 Python、tests/public/generation/publishing/scripts/pi-runtime/docs/prompts 检查新增文件，只有 work_store.py。既有测试无增量，本单探针位于 /tmp；实施报告属允许的报告增量。29 个视频保护文件全部 SHA 相同，包含未跟踪 aicg3d_compiler.py、模板和 lab-test 文件。证据：`source-audit.json`、`new-files.json`、`ui-compare.json`、最终全量对照。

额外重跑 today e2e：仍是 43 项通过后反例 15 失败，位于本次北京时间凌晨窗口；与已登记相对时间夹具问题相符，本单未修复。证据：`today.log`。

## 7）1332 行原因：通过（已获准的目标例外）

| 实际块 | 行数 |
|---|---:|
| 1–190：imports、策略常量、清洗兼容入口 | 190 |
| Store 类 191–451 | 261（其中 4 个方法正文 255） |
| 类间空行 452–453 | 2 |
| Handler 454–1314 | 861 |
| 1315–1332：启动装配与 main 尾部 | 18 |
| 合计 | 1332 |

Handler 中 do_GET 213 行、_post_routes 363 行、其他请求/文件/垃圾箱/回执/安全辅助和类常量等 285 行；并非只剩简单分发骨架。逐个对照实施方列出的 26 条 generation/视频相关条件及源码区间，去重后确为 103 行（含 2 条视频雷达条件及 1 个嵌套校验）。generation 是保留块之一，不能单独解释全部 433 行差额：即使减去它仍有 1229 行。实施报告同时列出 4 个原留方法和完整 Handler，整体原因属实，未声称只靠 generation 即可到 900 行。

要达到 `<900` 至少还需减少 433 行，超出本单已裁定的方案 A 和 Handler 原留范围；未为追行数扩围。证据：`line-count.json`、`handler-methods.json`、`source-audit.json`。

通用反例 G1–G4：实际正文/版本/镜像内容均有断言和目视证据；方案冲突已由主控裁定 A；本次未扩围；逐文件范围核对通过。
