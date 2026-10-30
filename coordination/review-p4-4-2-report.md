# 4-2 独立复核报告

| 决定要点 | 结果 |
|---|---|
| 结论 | 通过；七项要求均通过，未发现新增阻断项 |
| H / M / L | 0 / 0 / 0 |
| `server.py` SHA256 | `89b55addff523efd42ac40cae32eba8cc422271ba6891ea4a0b4a8967c5c12c1` |
| `html_sanitize.py` SHA256 | `1d86f4bc2c5d17b2343e5b4038ee4d5cf998ccfb2d6b23b286e4dc832dbb40d6` |
| `server_boot.py` SHA256 | `8ef917ea7325cd72718ebba370371cda1cda9ba4191bf4d76e6e243d35eb1d81` |
| `tests/test_server_split_imports.py` SHA256 | `c2e78d70d8bb807e1122f71e904c0ed2b2f9a10ef0f7f90bdba8c5523abbe858` |
| 测试 | Python 3569 项，0F/1E/7S（379.507s）；UI 719P/3F/1S；192 份输出逐字一致；35 个 is 成立 |
| 8766 | PID 72282，2026-10-30 02:38:56 启动；本复核未重启 |

任务 `task_29095ce19b94`，Dispatch `ctx_9ddc1e2959e0`；复核实施任务 `task_90b1791e8067`。依据合同通用规则、通用反例、4-2 节及工作队列三处裁定。只写本报告，未修改产品代码或测试，未提交、推送、调用真实模型/采集/X/Buffer/R2。证据根目录：`/tmp/review-p4-4-2/`。

## 1）S3 安全等价：通过

独立编写 16 个样例，对开工快照 `/tmp/impl-p4-4-2-baseline/repo/server.py` 和当前隔离副本分别运行；没有复用实施者的 10 个样例脚本。每例检查 `SafeHTML` 的 HTML/文本/双回执、`clean_html_full`、`clean_html`、`safe_html`、`SafeHTML_collect_rich`、`rich_dropped_of`、`markdown_aware_html`、`markdown_aware_html_full`，并覆盖后两者 `from_editor=True`、再次清洗、`html_formats`。每例 12 份输出，共 192 份；两个 JSON 文件 `cmp` 逐字一致。

| 独立样例 | 实际内容核查 |
|---|---|
| 嵌套 script | 通过；不保留 script 或脚本内容 |
| onclick / onmouseover / img onerror | 通过；事件属性及 img 被清除 |
| 混合大小写 javascript: | 通过；输出 `<a>链接</a>` |
| 实体编码 javascript: | 通过；实体先解码，恶意 href 被清除 |
| 换行混淆 scheme | 通过；换行混淆 href 被清除 |
| data: HTML 链接 | 通过；data: href 被清除 |
| object / iframe / style 嵌套 | 通过；只剩 `<p>保留</p>` |
| SVG onload 与 foreignObject | 通过；SVG/事件标签被清除，留下安全段落 |
| 实体编码标签与 Markdown 字面量 | 通过；标签仍为 `&lt;script&gt;`，Markdown 标记仍为数字实体 |
| span/br 传播行首、无序/有序列表及分隔线 | 通过；清洗入口实体化行首标记；Markdown 入口形成 ul/ol/hr |
| 标题与 li 内的字面量边界 | 通过；h2 的 01. 和 li 的 - 保持正文含义，字面字符实体化 |
| pre/code 与普通数字/连字符 | 通过；代码块保留 Markdown 字符，96.5GB/MoE-混合不受损 |
| Markdown 混排脚本和恶意链接 | 通过；保留正常标题/粗体；脚本删除、恶意链接只剩文本 |
| 重复 style、data 优先和事件属性 | 通过；data-align=right 保留；重复/非法样式及事件有丢弃回执 |
| MARKUP_LITERALS 全字符及行首组合 | 通过；各字面字符及行首规则输出与开工一致 |
| 正常 HTTPS 链接及固定安全 rel | 通过；保留 HTTPS，rel 固定为 noopener noreferrer |

全部返回通道的原文见 `injection-before.json` / `injection-after.json`；独立脚本 `injections.py`。现有安全回归随最终 Python 全量执行，未新增失败。

## 2）patch 传播与依赖方向：通过

独立探针 `patch_runtime.py` 共 18 项公开入口 patch/恢复检查：

- `MARKUP_LITERALS` 改为仅 `*`：从 8 个清洗/收集入口调用；HTML 实际转义产物中 #、~ 的行为随之改变，退出补丁后返回原值。仅返回样式回执的两个入口，通过捕获真实 parser 的 `_escape_data` 产物验证，回执通道本身保持原义。
- `MARKUP_LINE_RULES` 改为空：`<p><span>+ alpha</span><br>3) beta</p>` 的行首实体化被关闭，8 个入口均生效且恢复一致。
- `markdown_aware_html` 分别替换为支持 `from_editor` 的函数及仅一个参数的旧函数：`markdown_aware_html_full` 实际返回被替换的 HTML/文本/回执；恢复后重新生成 `<strong>`。
- `ROOT` 在真实 `server.main → server_boot.main` 启动链中替换，未传 `--workspace`：真实 Store 的 workspace 变为替换 ROOT 下的 workspace；恢复后再次真实启动，返回隔离副本原 ROOT。真实 Store、Assistant、ScheduledTasks、wire/seed 函数均执行，仅关闭后台生产线程/调度启动，并在 `serve_forever` 入口验证退出。模型配置指向临时不存在文件，不读取真实密钥。

`rg` 结果：

```text
html_sanitize.py:277:def clean_html_full(value, parser_class):
html_sanitize.py:284:    parser = parser_class()
html_sanitize.py:291:def SafeHTML_collect_rich(value, parser_class):
html_sanitize.py:293:    parser = parser_class()
server.py:128:    return html_sanitize.clean_html_full(value, SafeHTML)
server.py:132:    return html_sanitize.SafeHTML_collect_rich(value, SafeHTML)
```

两个底层入口的 `parser_class` 经 `inspect.signature` 检查均无默认值；省略参数实际抛出 TypeError。全仓 Python 搜索只找到 server.py 两处生产调用，结果见 `required-parser-callers.txt`。`rg` 检查两模块无 `import server` / `from server`；未采用全局代理。原始 patch/恢复值及启动记录见 `patch-runtime.json`、`patch-runtime.log`。

## 3）S2 re-export：通过（四个批准例外）

完整 `rg` 清单：`reexports-rg-complete.txt`，扫描 tests、scripts 和根目录 *.py；AST/模块引用名与每项身份结果见 `exports.json`。

直接导入的 9 个名字：`Conflict, Handler, Store, ThreadingHTTPServer, assistant_module, content, seed_intel_schedule, wire_intake_analysis, wire_intake_links`。

模块访问的 18 个名字：`BaseHTTPRequestHandler, Conflict, Handler, ROOT, SafeHTML, Store, ThreadingHTTPServer, assistant_module, clean_html, clean_html_full, content, html_formats, importer, json, main, markdown_aware_html, markdown_aware_html_full, safe_html`。

对每个名字实际执行 `from server import <name>`，与模块当前公开对象逐项 `is`；对拆出提供者的对象另逐项 `is`，总计 35 个成立。留在原模块的函数不杜撰第二个提供者。提供者身份检查的四个不相同项恰为：

| 例外 | 实际核查及合理性 |
|---|---|
| SafeHTML | 直接基类正是 html_sanitize.SafeHTML；子类仅定义 _escape_data，其正文与开工逐字相同，用于读取 server 当前可 patch 策略 |
| clean_html_full | 原签名 `(value)`；薄包装只向底层注入当前 SafeHTML，parser_class 必填 |
| SafeHTML_collect_rich | 同样保留原 `(value)` 签名并注入当前 SafeHTML |
| main | 原无参数签名；调用时读取 server 当前 ROOT/Store/Handler/assistant_module/content，并注入真实接线回调 |

四例外均与主控裁定及实施报告一致；没有第五个例外。

## 4）G2 outsourced_clues 生产接线：通过

真实启动运行两次（ROOT 被 patch、恢复后各一次），用 ScheduledTasks.__setattr__ 记录赋值，用 Python trace 记录接线回调实际调用栈。两次结果相同：

```text
outsourced_clues 属性总写入 = 2
其中构造器 None 初始化 = 1
实际 store.clues 接线 = 1
server.schedules.outsourced_clues is store.clues = True
赋值目标调度器 is 当前 HTTPServer.schedules = True
wire_outsourced_clues 回调调用 = 1
调用者 = server_boot.py 的 main
```

源码上，server.py:1882 的赋值位于 main 内真实回调；server.py:1885 将其作为必填参数传入；server_boot.py:185 在原接线顺序位置调用，前面已经创建 ScheduledTasks，后面继续设置各 analyzer。不是 AST 死代码，也未重复接线。证据同 `patch-runtime.json`。

## 5）逐字搬移：通过

独立 `source_scope.py` 用开工 server.py 对照当前模块，52 项全部一致：

- SafeHTML 每个方法逐字比较：_escape_data 对照薄子类，其余方法对照新基类；类定义移除 _escape_data 后再做源码 diff，差异仅空行，类属性/装饰器/文档串结构一致。
- 独立清洗、格式识别、回执函数、原地保留的 Markdown 函数及常量赋值均逐字相同。
- Store 和整个 Handler 的源码片段逐字相同，覆盖受保护的视频路由。
- seed_intel_schedule、wire_intake_analysis/media/links、_IntakeLedger 正文逐字相同。
- 两个 parser 函数仅把参数列表增加必填 parser_class、`SafeHTML()` 改为 `parser_class()`。
- main 仅增加显式依赖参数，并把原 outsourced_clues 赋值位置改为调用必填回调；原赋值留在 server.main 的回调中。除此以外启动正文逐字相同。

`source-scope.json` 包含逐项结果；`source-differences.diff` 为空（已扣除批准的注入转换），`safehtml-class.diff` 只显示空行差异。

## 6）S1/S4 全量与真实浏览器：通过

Python 全量开跑前通过 `orca orchestration ask` 登记独占，主控答复“可以开始，已登记独占”。最终在 `/tmp/review-p4-4-2/repo` 执行：

```sh
/Users/huangzongning/development/article-importer/.venv/bin/python -m unittest discover -s tests -v
npm run test:ui
```

| 检查 | 开工基线 | 独立复核最终结果 |
|---|---|
| Python | 3565 项，0F/1E/7S | 3569 项，0F/1E/7S；新增 4 项通过，379.507s |
| UI | 719P/3F/1S | 719P/3F/1S，723 项；失败名称逐项相同 |

唯一 Python E：`test_multi_segment_sequence_video_builds_director_timeline (test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline)`；异常仍为 `ValueError: 参考素材宽高须为 32 的倍数`，与开工相同，属于受保护视频线。UI 三个既有失败：

- 4 幕导演台：初始载入 4 幕卡片且包含默认极客荒岛剧本
- 4 幕导演台：切换题材预设（赛博追杀）即时同步 4 幕剧本
- 4 幕导演台：生成单幕 (15s) 采用 Singularity + Turbo 4步 + 345帧参数

最终 Python 7 个跳过项及原始理由（保留实际测试 ID，避免仅看数量）：

- `test_graph_input_names_and_link_types_match_the_deployed_schema (test_h3_inputs.NativeH3GraphTests.test_graph_input_names_and_link_types_match_the_deployed_schema)`：'External contract requires ARTICLE_STUDIO_H3_SCHEMA; no deployed schema is bundled'
- `test_catalogs_agree (test_h3_video_controls.ApiParityTest.test_catalogs_agree)`：'需设置 MEDIA_API_SOURCE 指向本机媒体 API 源码'
- `test_duration_rules_agree (test_h3_video_controls.ApiParityTest.test_duration_rules_agree)`：'需设置 MEDIA_API_SOURCE 指向本机媒体 API 源码'
- `test_measured_evidence_lists_agree (test_h3_video_controls.ApiParityTest.test_measured_evidence_lists_agree)`：'需设置 MEDIA_API_SOURCE 指向本机媒体 API 源码'
- `test_validation_rules_agree (test_h3_video_controls.ApiParityTest.test_validation_rules_agree)`：'需设置 MEDIA_API_SOURCE 指向本机媒体 API 源码'
- `test_h3_semantic_params_are_accepted_by_the_deployed_api_contract (test_media_http_transport.TransportTests.test_h3_semantic_params_are_accepted_by_the_deployed_api_contract)`：'MEDIA_API_SOURCE 未指向 media-api-source 检出'
- `test_total_deadline_reaches_existing_child_cleanup_instead_of_orphaning_decrypt (test_wechat_capture.DeadlineTests.test_total_deadline_reaches_existing_child_cleanup_instead_of_orphaning_decrypt)`：'External cleanup contract requires ARTICLE_STUDIO_WECHAT_HELPERS'

隔离环境修正记录（不计为产品回归）：首轮副本漏带 pi-runtime/work/私有 coordination 资源，完整日志 `python-incomplete-dependencies.log` 为 3356 项 25F/5E/54S；修正期间还中止了 importer 路径泄漏及缺少 14 天私有 HTML 夹具的两轮，日志分别为 `python-importer-leak-aborted.log`、`python-missing-private-fixtures-aborted.log`。importer 兼容脚本已复制进 /tmp，并让其相对路径指向隔离 article-studio；`UnderTestCopyTests` 实测通过，server/assistant_policy/markdown_blocks/pi_runtime/publishing 的 __file__ 均在副本。最后完整一轮才用于上表。未修改任何测试断言或产品逻辑。UI 初次漏依赖及一次自有隔离浏览器挂起的未完成日志也保留在 `ui-incomplete-dependencies.log`；补齐后最终 `npm run test:ui` 完整结束。

8766 验收使用隔离 Playwright headless Chrome、独立 browser context，未操作真实桌面或真实 Chrome profile。所有页面只允许本机 GET；16 页首轮 0 pageerror、0 console.error、0 HTTP >=400，主导航均为 6 项。实际页内容如下：

| 页面 | 实际内容核查 |
|---|---|
| today.html | 通过；HTTP 200，今日及“今天该做的 3 条” |
| library.html | 通过；HTTP 200，素材库 79 条，有真实卡片标题与来源 |
| index.html | 通过；HTTP 200，全部作品与既有作品正文 |
| publish.html | 通过；HTTP 200，1 条已发布，浏览 123、缺失指标显示未知 |
| deals.html | 通过；HTTP 200，商单管理与商单助手内容 |
| settings.html | 通过；HTTP 200，7 个来源、9 个定时任务、模型和后台服务 |
| lab.html | 通过；HTTP 200，图片生成设置及已有内容；未发起生成 |
| clues.html | 通过；HTTP 200，等待加载后 5157 条线索、首屏 30 张真实卡片 |
| intel.html | 通过；HTTP 200，情报采集页面、订阅/状态内容 |
| github.html | 通过；HTTP 200，跟踪仓库页面与来源设置 |
| schedules.html | 通过；HTTP 200，9 / 9 个任务，含启停状态与运行摘要 |
| feedback.html | 通过；HTTP 200，发布反馈和近7天帖子视图 |
| knowledge.html | 通过；HTTP 200，等待加载后 292837 份资料、14 个来源（含“所有资料”共15按钮） |
| intake.html | 通过；HTTP 200，创意回收列表真实条目 |
| cards.html | 通过；HTTP 200，提示词卡真实候选标题 |
| events.html | 通过；HTTP 200，事项页面及原有入口内容 |

线索和知识库首轮短时截图尚在加载，因此另做等待真实内容的验收：分别 16.739s、17.734s 后看到上述内容，两个页面仍 0 pageerror/console.error。接口复核：`/api/clues?view=all` total=5157/items=30，`/api/knowledge` total=292837/sources=14，`/api/items?page=1` total=79/items=50，`/api/schedules` tasks=9，`/api/publish/review` total=1/items=1，均与页面一致。知识库单次读状态约 8.642s，本单没有性能改动；不把短时加载壳当完整通过。

页面截图与 DOM/请求/错误明细：`pages/*.png`、`pages/results.json`；等待内容的证据：`pages/ready-clues.html.png`、`pages/ready-knowledge.html.png`、`pages-ready.json`；数据源计数：`api-counts.json`。诊断脚本曾误探 `/api/library/items` 得到404，正确产品入口为 `/api/items`；这是诊断脚本的路径错误，不是16页面实际发出的请求，相关原日志未删除。

`python server.py --help` 正常（`entry-help.txt`）。本复核未重启8766；两次 ps 确认 PID 一直为72282，启动时间02:38:56；最终 server.py/server_boot.py mtime均为02:38:08，html_sanitize为02:23:20，早于该进程启动，与实施者最终重启记录一致。未输出服务环境变量。today 的已知凌晨边界失败按派工说明排除，未改该测试。

## 7）G4 范围与保护：通过

已保存完整 `git diff --stat` 到 `git-diff-stat.txt`。共享区相对 HEAD 存在多批历史变更，因此用开工快照逐文件哈希作增量判断：

- 开工 tracked 清单552个：仅 server.py 改变，其他551个字节不变，无缺失。
- 开工根目录Python副本73个：仅 server.py 改变。
- 新增模块为 html_sanitize.py、server_boot.py；新增兼容测试为 tests/test_server_split_imports.py；其SHA见首表，与实施报告一致。
- 视频保护清单29个：逐项SHA256全部一致，包括generation、public/lab-test及相关测试；server内的视频分支随整个Handler逐字对比确认不变。
- 本复核正式目录仅新增本报告；未修改代码、既有测试、工作队列或合同。

精确结果见 `source-scope.json`（保护项逐个布尔值）和 `review-sha256.json`。边界说明：开工tracked哈希并不覆盖所有历史untracked文件；对未进入tracked清单的旧根目录Python另有73文件快照、视频untracked另有保护SHA，不把仅有git diff的范围声明夸成“所有历史untracked文件都有开工哈希”。本单可核验的全部范围证据均通过。

## 通用反例复核

| 项目 | 结论 | 证据 |
|---|---|---|
| G1 看实际内容 | 通过 | 注入输出逐字审阅、真实页面/卡片及接口计数核对，慢页等待完成后再确认 |
| G2 真实接线 | 通过 | 回调实际运行一次、目标对象身份正确，真实boot调用栈可追溯 |
| G3 不扩围 | 通过 | 采用主控三处裁定；只读复核，无产品修复或额外范围 |
| G4 范围 | 通过 | 552 tracked、73根目录Python、29保护文件及完整Handler逐项比对 |

未发现待修复的本单缺陷；受保护视频既有失败保持原样。
