# 4-2 富文本清洗与启动装配拆分交付报告

| 决定要点 | 结果 |
|---|---|
| 结论 | 通过；本次未新增失败，S2 的四个显式包装/子类例外已由主控批准 |
| H / M / L | 本次新增 0 / 0 / 0；既有失败单列于下文 |
| `server.py` SHA256 | `89b55addff523efd42ac40cae32eba8cc422271ba6891ea4a0b4a8967c5c12c1` |
| `html_sanitize.py` SHA256 | `1d86f4bc2c5d17b2343e5b4038ee4d5cf998ccfb2d6b23b286e4dc832dbb40d6` |
| `server_boot.py` SHA256 | `8ef917ea7325cd72718ebba370371cda1cda9ba4191bf4d76e6e243d35eb1d81` |
| `tests/test_server_split_imports.py` SHA256 | `c2e78d70d8bb807e1122f71e904c0ed2b2f9a10ef0f7f90bdba8c5523abbe858` |
| 测试 | 原3565项Python结果一致，新增4项通过；定向339/339及接线6/6；UI 719P/3F/1S；雷达34/34；Grok30/30；Markdown e2e通过 |
| 8766 PID | `35884 → 57462 → 72282`；最终启动时间见S4 |

任务：`task_90b1791e8067`；Dispatch：`ctx_a8bf8ce51eec`。本次为实施自检，仍交主控按项目流程派独立复核。不提交、不推送。

## 实际范围与经主控确认的装配方式

新增 `html_sanitize.py`：搬入清洗器主体、格式识别及回执合并；新增 `server_boot.py`：搬入 seed、wire、账本及启动装配。`server.py` 从开工 **2394 行**降为 **1890 行**；`html_sanitize.py` 342 行，`server_boot.py` 210 行。

开工发现既有测试在未注册到 `sys.modules` 的独立 `studio` 模块上 monkeypatch。简单 re-export 会破坏补丁语义，模块代理方案被主控明确否决，随后通过两次 `orca orchestration ask` 获得以下明确裁定：

- `server.SafeHTML` 只保留原样 `_escape_data`，继承 `html_sanitize.SafeHTML` 的其余原方法；`MARKUP_LITERALS`、`MARKUP_LINE_RULES`、`_MARKUP_HASH`、`_literal_ref`、`_escape_line_start` 原地保留。
- `clean_html_full(value)`、`SafeHTML_collect_rich(value)` 保持对外原名、原签名，薄包装显式注入 `server.SafeHTML`。底层 `parser_class` 必填，无默认值。
- `clean_html`、`safe_html`、`rich_dropped_of`、`markdown_aware_html`、`markdown_aware_html_full` 原地保留，原调用链不改。
- `server.main()` 保持无参数入口，调用时把当前 `ROOT / Store / Handler / assistant_module / content` 显式传给 `server_boot.main`。后者增加必填依赖参数；outsourced_clues的一条赋值原样留在server.main的嵌套回调，boot在原顺序位置调用它。seed/wire 与账本不改名、正文逐字搬移。

没有代理、运行时转发或 `html_sanitize/server_boot → server` 循环导入。保留原启动命令，`python server.py --help` 正常；launchd 已按合同 kickstart。

源码逐字证据：`/tmp/impl-p4-4-2-after/source-audit.json`。其中 `Store`、整个 `Handler`（覆盖视频拼接相关分支）、全部 SafeHTML 原有方法（分别比较子类或基类）、原地保留函数、独立清洗函数、seed/wire/账本均逐字一致。main正文只把一处原赋值换为必填回调调用，原赋值留在server.main并真实执行；除此之外逐字一致。两处 parser 构造函数按主控裁定增加必填参数并把 `SafeHTML()` 改为 `parser_class()`。

## S1 全量回归：通过（保留基线的受保护异常）

三次 Python 全量（开工、首次拆分、接线修正后）均提前 `orca orchestration ask` 登记独占，结束后发送释放消息。命令均为 `../article-importer/.venv/bin/python -m unittest discover -s tests`。

| 检查 | 开工 | 拆分后 | 对比 |
|---|---|---|---|
| Python 全量 | 3565项，0F/1E/7S | 3569项，0F/1E/7S | 既有3565项无新增失败，新增4项兼容测试通过 |
| `npm run test:ui` | 723项：719P/3F/1S | 723项：719P/3F/1S | 失败名称、数量相同 |
| `node tests/e2e/twitter_radar_page.cjs` | 34/34 | 34/34 | pageerror、console.error、外网请求均0 |
| 清洗/装配定向模块 | — | 339/339 | 包含既有Markdown、格式回执、intake、seed、删除账本测试及新增4项；接线兼容修正后另跑相关6项通过，最终全量覆盖最终代码 |

初次拆分全量曾出现2个新增失败：`test_outsourced_tasks.ProductionWiringTests` 直接读取server.py的main语法树，要求真实接线赋值留在main内。已先向主控ask并按裁定修正：回调是必填依赖，保留原语句并在原装配位置执行；既有测试不改，相关6项复验通过，再经独占全量确认0个新增失败。第一次失败完整日志为 `/tmp/impl-p4-4-2-after/python-first-failed.log`，未隐藏。

唯一 Python E 两侧均为 `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，异常均为 `ValueError: 参考素材宽高须为 32 的倍数`。受保护视频代码和测试未动。

UI 三个既有失败：
- 4 幕导演台：初始载入 4 幕卡片且包含默认极客荒岛剧本
- 4 幕导演台：切换题材预设（赛博追杀）即时同步 4 幕剧本
- 4 幕导演台：生成单幕 (15s) 采用 Singularity + Turbo 4步 + 345帧参数

附加真实浏览器自检：Grok 30/30；Markdown e2e通过（包括旧字面量夹具、按钮重排、助手修改、版本回看、0 pageerror）。`today_page.cjs` 前43项通过后，在反例15失败；已将**开工server快照及其Python模块副本**放在 `/tmp/impl-p4-4-2-baseline/repo` 并用原测试复现同一失败（同样前43项通过）。原测试不变；公共目录等未改的资源在副本中只读复用。

该额外失败的具体原因：运行约北京时间02:29，原测试向默认“今天”列表加入8小时前和4小时前的两帖，两者均早于当天00:00，根本没有进入待排序列表。开工与拆分后各自的夹具发帖时间、当日截止线和 `in_today=false` 对照在 `/tmp/impl-p4-4-2-after/today-existing-boundary.json`；对应代码是 `today_view.py` 的默认 today 零点截止线和测试108行的排序断言。未修改测试或扩围修复。

Python 两侧还都出现清理临时目录时的 `library_search.build_catalog` 后台线程 `sqlite3.OperationalError: unable to open database file` 警告；不属于新增失败，完整日志留证。

## S2 re-export 与 patch 兼容：通过（明确列出四个批准例外）

开工及最终均执行合同要求的 `rg 'from server import|import server' tests scripts *.py`；原始完整输出附在报告末尾，最终输出另存 `/tmp/impl-p4-4-2-after/reexports-rg.txt`。

所有外部 `from server import` 目标共9个：`Conflict, Handler, Store, ThreadingHTTPServer, assistant_module, content, seed_intel_schedule, wire_intake_analysis, wire_intake_links`。模块访问名另有：`BaseHTTPRequestHandler, Conflict, Handler, ROOT, SafeHTML, Store, ThreadingHTTPServer, assistant_module, clean_html, clean_html_full, content, html_formats, importer, json, main, markdown_aware_html, markdown_aware_html_full, safe_html`。逐个动态 `from server import <name>` 并与 `getattr(server, name)` 做 `is`；所有直接 re-export 再与定义模块对象做 `is`，合计35项判断全真：

| 名字 | 比较对象 | `is` |
|---|---|---|
| `BaseHTTPRequestHandler` | `server . BaseHTTPRequestHandler` | True |
| `Conflict` | `server . Conflict` | True |
| `Handler` | `server . Handler` | True |
| `ROOT` | `server . ROOT` | True |
| `SafeHTML` | `server . SafeHTML` | True |
| `Store` | `server . Store` | True |
| `ThreadingHTTPServer` | `server . ThreadingHTTPServer` | True |
| `assistant_module` | `server . assistant_module` | True |
| `clean_html` | `server . clean_html` | True |
| `clean_html_full` | `server . clean_html_full` | True |
| `content` | `server . content` | True |
| `html_formats` | `server . html_formats` | True |
| `importer` | `server . importer` | True |
| `json` | `server . json` | True |
| `main` | `server . main` | True |
| `markdown_aware_html` | `server . markdown_aware_html` | True |
| `markdown_aware_html_full` | `server . markdown_aware_html_full` | True |
| `safe_html` | `server . safe_html` | True |
| `seed_intel_schedule` | `server . seed_intel_schedule` | True |
| `wire_intake_analysis` | `server . wire_intake_analysis` | True |
| `wire_intake_links` | `server . wire_intake_links` | True |
| `CODE_TAGS` | `html_sanitize . CODE_TAGS` | True |
| `LINE_BREAKING_TAGS` | `html_sanitize . LINE_BREAKING_TAGS` | True |
| `WHOLE_LINE_TAGS` | `html_sanitize . WHOLE_LINE_TAGS` | True |
| `INTERNAL_REL` | `html_sanitize . INTERNAL_REL` | True |
| `_FORMAT_TAG` | `html_sanitize . _FORMAT_TAG` | True |
| `FORMAT_RECEIPT_LIMIT` | `html_sanitize . FORMAT_RECEIPT_LIMIT` | True |
| `html_formats` | `html_sanitize . html_formats` | True |
| `format_receipt` | `html_sanitize . format_receipt` | True |
| `merge_dropped` | `html_sanitize . merge_dropped` | True |
| `seed_intel_schedule` | `server_boot . seed_intel_schedule` | True |
| `wire_intake_analysis` | `server_boot . wire_intake_analysis` | True |
| `wire_intake_media` | `server_boot . wire_intake_media` | True |
| `_IntakeLedger` | `server_boot . _IntakeLedger` | True |
| `wire_intake_links` | `server_boot . wire_intake_links` | True |

四个不要求与拆出模块对象 `is` 相同的例外（主控明确允许）：

| 名字 | 原因与保留的契约 |
|---|---|
| `SafeHTML` | 显式子类，只保留原样 `_escape_data` 读取 server 的可patch字面量策略；其余方法继承，基类身份逐项验证 |
| `clean_html_full(value)` | 薄包装注入当前 `server.SafeHTML`，原签名保持 |
| `SafeHTML_collect_rich(value)` | 同上；底层 parser_class 无默认值 |
| `main()` | 薄包装读取调用时的 server.ROOT 等依赖，原无参数入口保持 |

其他原地保留的函数没有第二份提供者，调用方取得的是本模块同一个对象。`rg -n 'html_sanitize\.(clean_html|SafeHTML_collect_rich)' --glob '*.py' .` 仅命中 `server.py` 两个包装调用；没有业务调用方绕过包装。

| patch目标 | 既有位置 | 生效证据 |
|---|---|---|
| `MARKUP_LITERALS` | test_content_markdown.py:158；test_assistant_format_guide.py:237/545/638；e2e/content_markdown_fixture.py:39 | 原测试通过；新增测试分别在 server 与未注册 studio 上从每个清洗入口验证输出随patch改变并恢复 |
| `MARKUP_LINE_RULES` | test_content_markdown.py:159；test_assistant_format_guide.py:238/639；e2e/content_markdown_fixture.py:40 | 同上；行首减号实体与字面量前后对照 |
| `markdown_aware_html` | test_content_markdown.py:157；test_assistant_format_guide.py:236/546/640 | 原测试通过；新增测试证明 full 包装确实调用被patch入口，恢复后继续转换Markdown |
| `ROOT`（直接赋值） | e2e/today_page.cjs:25；e2e/twitter_radar_grok_bot.cjs:44 | 启动注入单测证明使用当前ROOT；Grok全30项及today前43项都从真实夹具启动通过 |

接线回调的必要性：赋值目标 `server.schedules` 要等boot内部创建HTTPServer、assistant和ScheduledTasks后才存在；仅把Store提前到server.main创建，仍无法在保持原顺序下直接赋值。因此按主控条件使用必填回调，没有增加死代码或重复接线。运行时证据 `/tmp/impl-p4-4-2-after/runtime-wiring.log`：`all_attribute_writes=2`（其中构造器None初始化1次）、`clues_wiring_count=1`、`is_store_clues=true`、`same_scheduler=true`。探针走真实server.main、真实Store和ScheduledTasks、临时workspace、随机端口，只关闭调度执行和后台watch/catalog线程，并在serve_forever入口验证后退出；未启动真实采集。

测试新增4项：provider对象身份、每个公开清洗入口的patch与恢复、Markdown三值入口到四值包装的patch传播、启动当前依赖注入。`SafeHTML_collect_rich / rich_dropped_of` 只返回样式拒绝原因，字面量策略不应改变这个返回通道；测试同时捕获其实际parser转义产物，证明patch生效、恢复正确、样式回执保持原义。

## S3 富文本安全与10个注入样例：通过

开工快照副本输出保存在 `/tmp/impl-p4-4-2-baseline/injection-before.json`，拆分后为 `/tmp/impl-p4-4-2-after/injection-after.json`。同一脚本分别从两个根目录加载server；对每个样例运行 `clean_html_full` 和 `markdown_aware_html_full`，包含HTML、纯文本、格式丢弃与样式拒绝所有返回通道。两个JSON经 `cmp` **逐字一致**。

| 样例 | 结果 |
|---|---|
| script脚本 | 前后逐字一致 |
| img onerror | 前后逐字一致 |
| javascript: 链接 | 前后逐字一致 |
| data: 链接 | 前后逐字一致 |
| onclick事件属性 | 前后逐字一致 |
| iframe嵌入 | 前后逐字一致 |
| svg/onload与嵌套script | 前后逐字一致 |
| 非法position样式、合法color及非法data-color混合 | 前后逐字一致 |
| 实体编码script文本混合Markdown | 前后逐字一致 |
| pre/code内注入文本及正常HTTPS链接 | 前后逐字一致 |

既有安全/Markdown/格式回执测试均包含在339项定向全绿中；全量也未引入清洗回归。

## S4 重启与16页隔离浏览器：通过

执行 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`；用合同允许的 `launchctl list | awk` 与 `ps -o pid,lstart -p 72282` 验证新PID。未读取/输出服务环境变量。PID证据：

```text
PID STARTED
72282 Fri Oct 30 02:38:56 2026
```

浏览器为单独启动的 headless Chrome / 独立 Playwright context，没有操作黄总桌面或现有Chrome。新PID启动后全部16页重新打开并截图；每页HTTP200、主导航顺序都是 today/library/works/publish/deals/settings，页面文字真实非空，pageerror与基线相同（均0）。浏览器阻断所有外部请求和非GET请求，并拦截 collect/intel/wechat状态路径，避免页面状态读取触发连接器。

| 页面 | HTTP | 主导航数 | 正文字符数 | 截图 |
|---|---|---|---|---|
| `today.html` | 200 | 6 | 322 | `/tmp/impl-p4-4-2-after/pages/today.html.png` |
| `library.html` | 200 | 6 | 7230 | `/tmp/impl-p4-4-2-after/pages/library.html.png` |
| `index.html` | 200 | 6 | 225 | `/tmp/impl-p4-4-2-after/pages/index.html.png` |
| `publish.html` | 200 | 6 | 278 | `/tmp/impl-p4-4-2-after/pages/publish.html.png` |
| `deals.html` | 200 | 6 | 6808 | `/tmp/impl-p4-4-2-after/pages/deals.html.png` |
| `settings.html` | 200 | 6 | 2599 | `/tmp/impl-p4-4-2-after/pages/settings.html.png` |
| `lab.html` | 200 | 6 | 56931 | `/tmp/impl-p4-4-2-after/pages/lab.html.png` |
| `clues.html` | 200 | 6 | 490 | `/tmp/impl-p4-4-2-after/pages/clues.html.png` |
| `intel.html` | 200 | 6 | 993 | `/tmp/impl-p4-4-2-after/pages/intel.html.png` |
| `github.html` | 200 | 6 | 1294 | `/tmp/impl-p4-4-2-after/pages/github.html.png` |
| `schedules.html` | 200 | 6 | 1444 | `/tmp/impl-p4-4-2-after/pages/schedules.html.png` |
| `feedback.html` | 200 | 6 | 443 | `/tmp/impl-p4-4-2-after/pages/feedback.html.png` |
| `knowledge.html` | 200 | 6 | 641 | `/tmp/impl-p4-4-2-after/pages/knowledge.html.png` |
| `intake.html` | 200 | 6 | 27977 | `/tmp/impl-p4-4-2-after/pages/intake.html.png` |
| `cards.html` | 200 | 6 | 18039 | `/tmp/impl-p4-4-2-after/pages/cards.html.png` |
| `events.html` | 200 | 6 | 1273 | `/tmp/impl-p4-4-2-after/pages/events.html.png` |

16页开工截图对应 `/tmp/impl-p4-4-2-baseline/pages/`；最终截图和页面结果对应 `/tmp/impl-p4-4-2-after/pages/`。已人工视觉检查今日页全文和设置页截图；未把已有来源异常提示伪报为清洗/装配回归。

## 通用反例 G1–G4

| 反例 | 结果 | 证据 |
|---|---|---|
| G1 真实内容 | 通过 | 重启后真实今日48h页面显示42条，与该次API的items/top去重条目逐一ID一致，页头总数42一致；点开首条 `x:2115602263540470125` 全文，reader实际文本967字符。见 g1.json/g1.png；开工同样留有真实条目读取证据。动态来源未冻结，不声称开工与重启后的实时来源总数相同 |
| G2 不钻字面漏洞 | 通过 | 发现简单re-export破坏patch立即ask；AST装配测试也保留实际运行语句。运行时探针证明真实main启动链中，构造器先写None一次，随后实际clues接线恰1次，且server.schedules.outsourced_clues is store.clues为True；不以AST字面通过代替实际接线 |
| G3 前提冲突先确认 | 通过 | 所有兼容装配与全量独占均先ask确认；额外today失败先用开工快照复现并上报，不擅改测试 |
| G4 范围 | 通过 | 552个开工tracked文件逐文件SHA256比较，仅允许的server.py变化；其余551个零增量；受保护29文件哈希均未变。新增仅两模块、一个获准测试及本报告 |

## 可复核证据与限制

- 开工根目录：`/tmp/impl-p4-4-2-baseline/`，含 opening.diff、server.py、repo快照、tracked/protected-sha256.json、Python/UI/雷达日志及页面截图。
- 最终根目录：`/tmp/impl-p4-4-2-after/`，含 incremental.diff、source-audit.json、scope-audit.json、exports.json、Python/UI/定向/Grok/Markdown/今日/雷达日志、PID和截图。
- Markdown浏览器完整证据目录：`coordination/evidence/content-markdown-e2e/20261029T182856-54158/`。
- 自检脚本：`/tmp/impl-p4-4-2-baseline/audit_source.py`、`check_exports.py`、`injection_compare.py`、`pages.cjs`、`g1.cjs`。
- 本次保留4个受保护失败（Python 1E、UI 3F）、原有skip及额外today测试既有时间边界失败；没有修复或隐藏这些问题。未主动调用真实采集、AIsa、X、Buffer、R2写接口。
- `server.py` 尚1890行；路由与Store拆分属于4-3/4-4，不在本次扩围。独立复核由主控后续派发。

## 附：开工 re-export 原始 rg 清单

```text
write_from_item.py:                from server import Conflict
scripts/smoke_local.py:from server import Handler, Store
tests/test_intel_status.py:from server import Store
tests/test_write_from_item.py:from server import Store
tests/test_clue_retry.py:from server import Store
tests/test_clues_api.py:from server import Handler,Store,ThreadingHTTPServer
tests/test_knowledge_reference_legacy.py:from server import Handler, Store
tests/test_intake_api.py:from server import Handler, Store, ThreadingHTTPServer
tests/test_media_conversation.py:from server import Store, content, assistant_module
tests/test_intake_media_recognition.py:from server import Store
tests/test_x_official.py:        import server
tests/test_perf_knowledge_paths.py:from server import Store
tests/test_outsourced_tasks.py:        from server import Store
tests/test_intel_deadline_http.py:from server import Store
tests/test_compare_rich_inline.py:import server
tests/test_h3_media_reuse.py:from server import Store, assistant_module, content
tests/test_knowledge_trash_api.py:import server as server_module
tests/test_knowledge_trash_api.py:from server import Handler, Store, ThreadingHTTPServer
tests/test_deals_review.py:from server import Handler, Store
tests/test_knowledge.py:from server import Store
tests/test_knowledge.py:from server import Handler
tests/test_workflow_knowledge.py:from server import Store
tests/test_schedules_http.py:from server import Handler,Store,ThreadingHTTPServer
tests/test_pi_runtime.py:from server import Store, content, assistant_module
tests/test_radar_monitor_e2e.py:        from server import Store, Handler
tests/test_krea_sizes.py:from server import Store,content,assistant_module
tests/test_published_states.py:from server import Store
tests/test_github_source.py:from server import Store
tests/test_wechat_source_client.py:        from server import Store
tests/test_media_http_errors.py:from server import Store
tests/test_reference_media.py:from server import Store
tests/test_intake_resolve_logging.py:from server import Handler, Store, ThreadingHTTPServer
tests/test_chat_attachments.py:from server import Store,content,assistant_module
tests/test_generation_progress.py:from server import Store
tests/test_assistant_memory_api.py:from server import Handler, Store
tests/test_intel_wechat_mp.py:from server import Store
tests/test_outsourced_prompt_route.py:from server import Handler, Store
tests/test_collect_core.py:from server import Handler, Store, seed_intel_schedule
tests/test_gmail_knowledge.py:from server import Store
tests/test_intake.py:from server import Store
tests/test_intel_article_url_edges.py:from server import Store
tests/test_wechat.py:from server import Store
tests/test_publication_reviews.py:from server import Store
tests/test_intel_wechat_mp_3.py:from server import Store
tests/test_generation_lab.py:from server import Store,content,assistant_module
tests/test_generation_thumbnail.py:from server import Store
tests/test_assistant_backend_protocol.py:from server import Store, assistant_module, content
tests/test_intake_shared_analysis_version.py:from server import Store
tests/test_items_store.py:        from server import Handler
tests/test_wechat_links.py:from server import Store
tests/test_link_index_orphan.py:from server import Store, wire_intake_links
tests/test_deals_evidence.py:from server import Store
tests/test_assistant_ssh_api.py:from server import Handler, Store
tests/test_generation.py:from server import Store
tests/test_intel_job_ownership.py:from server import Store
tests/test_intel_job_ownership.py:from server import Store
tests/test_intel_job_ownership.py:from server import Store
tests/test_generation_frame_from_library.py:from server import Handler, Store, ThreadingHTTPServer
tests/test_loras.py:from server import Store, content, assistant_module
tests/test_video_dual_sampling.py:from server import Store
tests/test_assistant_citations.py:from server import Store, content
tests/test_knowledge_start.py:from server import Store
tests/test_events_api.py:from server import Store,Handler
tests/test_publication_observations.py:from server import Store
tests/test_media_http_transport.py:from server import Store
tests/test_intel_discovery_integration.py:from server import Store
tests/test_wechat_capture_recovery.py:from server import Store
tests/test_publication_metrics.py:from server import Store
tests/test_aisa_live_chain.py:from server import Store
tests/test_aisa_live_chain.py:  from server import Handler
tests/test_aisa_live_chain.py:from server import Store
tests/test_intake_seen_accounting.py:from server import Store
tests/test_intake_cards.py:from server import Store
tests/test_deals.py:from server import Store,Handler
tests/test_assistant_memory.py:from server import Store, content
tests/test_intake_instant_analysis.py:from server import Handler, Store, ThreadingHTTPServer
tests/test_intake_instant_analysis.py:        from server import wire_intake_analysis
tests/test_intake_instant_analysis.py:        from server import wire_intake_analysis
tests/test_recommend_doc_granularity.py:from server import Store, wire_intake_links
tests/test_wechat_capture.py:from server import Store
tests/test_wechat_capture.py:        from server import Handler
tests/test_generation_management.py:from server import Store
tests/test_intake_inherit_seen.py:from server import Store
tests/test_intake_platform_link.py:from server import Store
tests/test_backend_query_api.py:from server import Handler, Store
tests/e2e/content_markdown_fixture.py:import server  # noqa: E402  产品 Store，E2E 走真实入库与版本
tests/e2e/aisa_live_chain_fixture.py:from server import Store,Handler
tests/test_video_lab.py:from server import Store
tests/test_intake_delete.py:from server import Handler, Store, ThreadingHTTPServer, wire_intake_links
tests/test_intake_delete.py:        from server import wire_intake_links
tests/test_intake_delete.py:        from server import wire_intake_links
tests/test_intake_delete.py:        from server import wire_intake_links
tests/test_knowledge_delete.py:from server import Store
tests/e2e/compare_rich_inline_fixture.py:import server
tests/e2e/settings_page.cjs: const code=`import sys,json,sqlite3;from pathlib import Path;sys.path.insert(0,${JSON.stringify(ROOT)});import server,settings_view,grok_bot_radar;from http.server import ThreadingHTTPServer;from scheduled_tasks import ScheduledTasks;from tests.test_clues import KnowledgeFake;from types import SimpleNamespace
tests/test_clue_pipeline.py:from server import Store
tests/test_intel_hit_counts.py:from server import Store
tests/e2e/x_official_fixture.py:import server
tests/test_clue_pipeline_locks.py:from server import Store
tests/test_external_reference.py:from server import Store, Handler
tests/test_wechat_knowledge.py:from server import Store
tests/test_scheduled_tasks.py:        from server import Store
tests/test_intake_batch_delete.py:from server import Handler, Store, ThreadingHTTPServer
tests/test_intake_batch_delete.py:        from server import wire_intake_links
tests/test_intake_batch_delete.py:        from server import wire_intake_links
tests/test_github_api.py:from server import Store, Handler, ThreadingHTTPServer
tests/e2e/server_fixture.py:import server
tests/e2e/write_and_publish.cjs: const run=`import sys,time,json,os;from pathlib import Path;sys.path.insert(0,${JSON.stringify(ROOT)});import server,grok_bot_radar,write_from_item,pi_runtime;from items_store import ItemsStore;from tests.test_write_from_item import answer;import publishing.x_official as xo;tmp=Path(${JSON.stringify(tmp)});root=Path(${JSON.stringify(workspace)});grok_bot_radar.BUNDLE_PATH=tmp/'bundle.json'\ndef no_remote(*a,**k):\n with (tmp/'remote-calls').open('a') as f:f.write('unexpected\\n')\n raise AssertionError('禁止真实 X 调用')\nxo.remote=no_remote\ndef fake(self,item):\n if item['id']=='x:9003':raise RuntimeError('模型不可用')\n while not (tmp/(item['id']+'.release')).exists():time.sleep(.02)\n if write_from_item.draft_kind(item)=='thread':return json.dumps({'title':'【验收】我准备把收藏接进写作流程','parts':['我的问题是收藏越来越多，却总要重新找原文。\\n\\n【待补：我的实测数据】','我准备先跑通从素材到底稿的流程，再记录输入、输出和冲突处理。现在还没有亲测结论。','工具和参数\\n\\n来源链接：'+item.get('url','')+'\\n\\n引用 @fixture 的思路，等我亲手跑完再补截图和数据。']},ensure_ascii=False)\n return answer(item)\nwrite_from_item.ItemWriter._model=fake\npi_runtime.PiRuntime.run=lambda self,job,system,tools:{'answer':'聊天正常开始，起草没有占用助手锁。','status':'done'}\ns=ItemsStore(root)\nfor key in ('9002','9003'):s.upsert({'id':'x:'+key,'title':'【验收】'+key+' 素材','kind':'article','full_text':'独立素材内容 '+key,'sources':['G']})\nsys.argv=['server.py','--workspace',str(root),'--port',str(${port})];server.main()`;
tests/e2e/today_page.cjs: const runner=`import sys;from pathlib import Path;sys.path.insert(0,${JSON.stringify(REPO)});import server,grok_bot_radar;server.ROOT=Path(${JSON.stringify(temp)});grok_bot_radar.BUNDLE_PATH=Path(${JSON.stringify(bundle)});sys.argv=['server.py','--workspace',${JSON.stringify(workspace)},'--port',sys.argv[1]];server.main()`;
tests/test_intel_outcome_migration.py:from server import Store
tests/e2e/twitter_radar_grok_bot.cjs:  const runner = `import sys; from pathlib import Path; sys.path.insert(0, ${JSON.stringify(REPO)}); import server, grok_bot_radar; server.ROOT=Path(${JSON.stringify(temp)}); grok_bot_radar.BUNDLE_PATH=Path(${JSON.stringify(bundleFile)}); sys.argv=['server.py','--workspace',${JSON.stringify(workspace)},'--port',sys.argv[1]]; server.main()`;
tests/test_intake_cards_api.py:from server import Handler, Store, ThreadingHTTPServer
tests/e2e/library_page.cjs: const run=`import sys;from pathlib import Path;from types import SimpleNamespace;from http.server import ThreadingHTTPServer;sys.path.insert(0,${JSON.stringify(ROOT)});import server,grok_bot_radar;grok_bot_radar.BUNDLE_PATH=Path(${JSON.stringify(path.join(tmp,'bundle.json'))});from items_store import readonly;import library_search,time;root=Path(${JSON.stringify(workspace)});original_build=library_search.build_catalog\ndef gated_build(root):\n while not Path(${JSON.stringify(path.join(tmp,'build-release'))}).exists():time.sleep(.01)\n return original_build(root)\nlibrary_search.build_catalog=gated_build\nclass Knowledge:\n def document(self,id):\n  c=readonly(root/'knowledge/knowledge.db');r=c.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone();c.close();return dict(r)\ns=ThreadingHTTPServer(('127.0.0.1',${port}),server.Handler);s.store=server.Store(root);s.store._knowledge=Knowledge();s.assistant=server.assistant_module.Assistant(s.store,server.content,root/'absent.json');s.serve_forever()`;
tests/test_intel_wechat_mp_2.py:from server import Store, seed_intel_schedule
tests/test_intel_wechat_mp_2.py:        from server import Handler
tests/test_round4_intel_rate_limit.py:from server import Store
```
