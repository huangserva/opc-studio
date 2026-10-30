# 4-3 按领域拆路由交付报告

| 决定要点 | 结果 |
|---|---|
| 结论 | 通过（实施自检；既有受保护失败保持不变） |
| 新增 H / M / L | 0 / 0 / 0 |
| 实际范围 | 五块 GET/POST；generation、视频、lab 原地保留；无提交、无推送 |
| server.py SHA256 | `9457cf2fe1f7b3f4df5195d58a14521dceb7aa3a8b6bc6787d35f9db1ca64526` |
| 五个领域模块 SHA256 | `intel_collect_api.py` `6a4cec417ccbaf5ac2b55b201c2497884e9695ad70a3b4f78e536de0033ed4eb`<br>`deals_api.py` `40c9e9eb03b60a974b114b4f11234958acf323221176ce5a7fa2ffda046030a6`<br>`intake_cards_api.py` `9c2ee81142c0bc44186aabb96d673eea0e525010e1e91dd1488c4ba216960205`<br>`feedback_api.py` `0212b7d239c77e81eacb8ed3b0657f84878b27ef629b34b7dc7cafb38796eaae`<br>`schedules_assistant_api.py` `067e2b16589789bc53808ab2b77a069f981f91d8278e313c4a1ffd349c6e17b7` |
| 新增探针 SHA256 | `tests/server_domain_route_probe.py` `6b4b2f6c7d9a2490c0fc8fcd4e8125900d4f823638981d0f342e25dc5d3f7fb7` |
| 测试 | Python 开工3569项0F/1E/7S，最终3569项0F/1E/7S；UI前后719P/3F/1S；雷达34/34；Grok30/30；R1 116条+243条负例全同；R2 116/116 |
| 8766 PID | 72282 → 32836；`32836 Fri Oct 30 03:11:37 2026` |
| server.py 行数 | 1890 → **1693**（本单未拆 Store、清洗兼容入口及 generation） |

任务 `task_0c6345c83d5a`，Dispatch `ctx_a70524e8a0fa`。本报告为实施自检，独立复核由主控另派。报告之外共涉及七个代码/探针文件（修改server.py，新增六个文件），没有改既有测试。

## 范围与主控裁定

- 新增 `intel_collect_api.py`、`deals_api.py`、`intake_cards_api.py`、`feedback_api.py`、`schedules_assistant_api.py`；每个模块提供 `get_route(handler, parts)` 与 `post_route(handler, segments, payload)`。
- 开工先向主控确认：`intake/drop`、`intake/stage` 是流式上传，原位留在 server.py 的通用32MB闸门前；`assistant-images` 文件GET随assistant搬移，路径校验、Content-Type和缓存行为保留。generation、视频、lab所有分支完全原留。
- 第二次向主控明确：原 `reply()` 返回None，逐字搬移的命中分支同样返回None，不能用返回值判定命中。主控明确接受「原位置保留精确条件，条件命中后直接return子路由」，与现有items_store/publish_review/x_official装配一致；**没有改reply，没有代理、包装或魔法转发**。子模块未命中返回None，入口不拿返回值判断命中。
- 按主控追加门槛：新增探针校验77个分发条件与模块分支按顺序一一对应，并覆盖每条路由多一段、少一段、交换GET/POST；响应正文必须非空。未命中路径继续使用原来的错误/静态文件兜底。
- 77个业务分支只变缩进，正文逐字相同，AST完全相同；其余Handler方法、Store、清洗兼容函数、main源码逐字相同。两个修改方法移除搬出/装配分支后AST完全相同，覆盖安全入口、上传和所有受保护分支。证据：`/tmp/impl-p4-4-3-after/source-audit.json`，脚本 `/tmp/impl-p4-4-3-baseline/source_audit.py`；分发对照 `/tmp/impl-p4-4-3-after/dispatch-audit.json`；28个涉及generation/video/lab/upload的原if源码逐字保留证据 `/tmp/impl-p4-4-3-after/protected-source.json`。

## R1：逐路由对照，通过

开工源码快照 `/tmp/impl-p4-4-3-baseline/repo/server.py`；拆分后源码为正式工作目录；两边各起独立 `ThreadingHTTPServer(('127.0.0.1', 0), Handler)`。所有写入都在每次探针创建的 `/tmp/p4-route-workspace-*` 内；本地固定后端逐次记录被调用对象、参数、返回正文，**不调用采集、模型、AIsa、X、Buffer或R2**。

比较116条方法+路径，此外243条邻近/方法负例；两边状态码、完整正文（因此也包括结构）、后端调用与关键响应头逐字相同。每条HTTP响应都非空。此项证明分发等价，不把本地后端替身当作外部服务连通性证据；真实业务语义由既有全量与页面/上传实测补充。

- 脚本：[tests/server_domain_route_probe.py](../tests/server_domain_route_probe.py)；结果 `/tmp/impl-p4-4-3-baseline/routes-final.json`、`/tmp/impl-p4-4-3-after/routes-final.json`，日志 `/tmp/impl-p4-4-3-after/routes-final.log`。
- 负控一：仅在/tmp快照把deals列表200故意改201，探针退出1，定位 `routes[1]` 和相应负例；`/tmp/impl-p4-4-3-baseline/mutant-final.log`。
- 负控二：仅在/tmp副本让server命中deals却让模块不命中，旧版动态探针报RemoteDisconnected，最终版在静态一一对照提前退出1；`/tmp/impl-p4-4-3-baseline/dispatch-deadzone.log`、`dispatch-deadzone-final.log`。
- `chat-attachments` 同时列出常规路径（原有404）和原正则实际命中的编码路径，保留既有行为；未修既有正则问题。

复跑命令：
```sh
../article-importer/.venv/bin/python tests/server_domain_route_probe.py /tmp/impl-p4-4-3-baseline/repo /tmp/impl-p4-4-3-baseline/routes-final.json
../article-importer/.venv/bin/python tests/server_domain_route_probe.py . /tmp/impl-p4-4-3-after/routes-final.json /tmp/impl-p4-4-3-baseline/routes-final.json
```

完整方法/路径表在文末；243条负例各自的路径、状态、正文与调用保存在上述JSON的negative数组，全部一致。

## R2：来源与头校验，通过

58个拆出POST动作，每个分别缺Origin、缺X-Article-Studio，共116次，前后均403，正文均为“请求来源不受支持”，后端调用数组均为空。Origin/头校验仍在 `_post_routes` 入口且只做一次；子路由均不接触 `rfile`。原do_POST日志生命周期及Handler日志方法完整保留。证据：两份routes-final.json的security数组；source-audit.json。

## R3：上传顺序和32MB限制，通过

- `intake/drop`、`intake/stage` 上传原代码、位置、顺序原样保留；x-official/generation/upload其他上传入口也逐字原留。
- 本地固定后端用33,000,000的Content-Length证明上传仍在通用闸门前命中，均201；同长度非上传 `/api/deals` 返回413且后端不执行。
- **实际存储实测**：开工快照与拆分后各在/tmp真实Store/Knowledge/Intake上，通过随机端口给上述两条上传路由发送33,000,000字节合成文件，各次201并完整落盘，文件SHA256均为 `1b78e591f68b34852047700bc65a73982136270801d55015933f6f995b752754`，两份结果逐字相同。只将ffprobe依赖替成“不可用”，不伪造读写结果；不验证视频解码或画面。脚本 `/tmp/impl-p4-4-3-baseline/real_upload_probe.py`，结果 `/tmp/impl-p4-4-3-baseline/real-uploads.json`、`/tmp/impl-p4-4-3-after/real-uploads.json`。

## R4：全量回归，通过（基线异常保留）

| 检查 | 开工 | 拆分后 |
|---|---|---|
| Python `../article-importer/.venv/bin/python -m unittest discover -s tests` | 3569项，0F/1E/7S，380.902秒 | 3569项，0F/1E/7S，378.531秒 |
| `npm run test:ui` | 719P / 3F / 1S | 719P / 3F / 1S |
| 雷达真实浏览器e2e | 派工基线34/34 | 34/34；pageerror=0、console.error=0、外网请求=0 |
| Grok真实浏览器e2e | 派工基线30/30 | 30/30 |
| today e2e | 派工已明确反例15在北京时间0–8点失败 | 本次43项通过后反例15失败，与已知边界一致 |

两次Python全量均先ask主控登记独占，开工跑完已发送释放消息；最终跑完已发送释放消息。全量日志：`/tmp/impl-p4-4-3-baseline/python.log`、`/tmp/impl-p4-4-3-after/python.log`；UI与e2e日志在对应目录。未修改既有测试。

Python唯一E：`test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，`ValueError: 参考素材宽高须为 32 的倍数`，属于受保护代码。UI三个既有失败都在受保护lab-test：初始载入4幕默认极客荒岛剧本、切换赛博追杀预设同步4幕、生成单幕采用Singularity/Turbo4步/345帧。today反例15为本地凌晨“过去8小时/4小时”样本落在昨日，被默认今日筛掉，按派工要求如实列出，不扩围修复。另有既有ResourceWarning及清理临时目录后台线程警告，未隐去日志。

## 测试依赖开工清单及保留方式

开工先执行rg全文搜索，随后AST补充核对。原始rg输出 `/tmp/p4-4-3-test-server-refs.txt`、`/tmp/p4-4-3-focused-refs.txt`；结构化清单 `/tmp/impl-p4-4-3-baseline/test-dependencies.json`。未发现 `patch('server.xxx')` / `patch("server.xxx")` 字符串目标。

| patch/源码依赖 | 位置 | 拆后保证 |
|---|---|---|
| server/studio.MARKUP_LITERALS、MARKUP_LINE_RULES、markdown_aware_html | content_markdown:157–159；assistant_format_guide:236–238、545–546、638–640；content_markdown_fixture:39–40；server_split_imports:82 | 对象定义与使用链都原地保留且源码逐字相同；全量覆盖既有补丁测试 |
| server.ROOT | test_server_split_imports:89；today/Grok e2e启动直接赋值 | main依赖注入与ROOT用法未动；既有补丁测试及Grok真实启动通过 |
| server_boot.main / ThreadingHTTPServer | test_server_split_imports:89、101 | boot及main未动，原补丁对象保持；既有测试覆盖 |
| Handler._post_routes | test_intake_resolve_logging:234 | 方法名字、do_POST调用与finally原留；仅该方法里的业务if正文迁出；patch仍会在原调用点抛错并记日志 |
| Handler._drop_ref / _log_resolve / __new__ | test_intake_resolve_logging:279、293、295、312 | 对应方法逐字原留；日志回归由全量覆盖 |
| self.server.assistant.request_model | test_write_from_item:111 | server实例与assistant对象未更换；子路由仍从同一个handler.server读取 |
| server.py main的AST接线断言 | test_outsourced_tasks:558–596 | main源码逐字未动，真实outsourced_clues赋值仍在原回调 |
| server.py源码中 radar/monitor、api/radar/feedback | e2e/twitter_radar_monitor_page.cjs:607–616 | 这两条原地未动；非本单feedback模块范围，仍能被源码正则读到 |
| Handler.do_POST=Handler.do_GET | test_video_radar:346 | 此Handler是测试内自定义HTTP假网关，不是server.Handler；本单无影响 |
| 其他from server import / spec_from_file_location加载 | test-dependencies.json完整列出 | server所有原公开名字保留；未建立子模块到server循环导入，未改变未注册studio的补丁语义 |

## 重启与16页隔离浏览器

按本次派工授权执行 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`，新PID32836，启动时间见决定要点及 `/tmp/impl-p4-4-3-after/pid.txt`；此后所有16页重新访问新进程。只用允许的launchctl list/ps pid,lstart，**未读取或输出服务环境变量**。

单独启动headless Chrome、独立Playwright context；不操作黄总真实桌面/Chrome。真实8766只允许GET；外网、非GET和collect/intel/clues-wechat连接器路径用本地空JSON响应隔离，避免触发真实采集；这部分不当成采集联通性验证。除隔离请求外页面真实加载线上本地数据。16页均HTTP200、主导航正确6项、正文非空、pageerror=0、console.error=0，截图全部在 `/tmp/impl-p4-4-3-after/pages/`。脚本 `/tmp/impl-p4-4-3-baseline/pages.cjs`；开工相同隔离策略结果 `/tmp/impl-p4-4-3-baseline/pages-clean/results.json`，最终 `/tmp/impl-p4-4-3-after/pages/results.json`。

| 页面 | HTTP | 主导航数 | 正文字符数 | 控制台/页面异常 |
|---|---|---|---|---|
| `today.html` | 200 | 6 | 322 | 0 / 0 |
| `library.html` | 200 | 6 | 7230 | 0 / 0 |
| `index.html` | 200 | 6 | 4725 | 0 / 0 |
| `publish.html` | 200 | 6 | 278 | 0 / 0 |
| `deals.html` | 200 | 6 | 6808 | 0 / 0 |
| `settings.html` | 200 | 6 | 2600 | 0 / 0 |
| `lab.html` | 200 | 6 | 56931 | 0 / 0 |
| `clues.html` | 200 | 6 | 475 | 0 / 0 |
| `intel.html` | 200 | 6 | 1144 | 0 / 0 |
| `github.html` | 200 | 6 | 1294 | 0 / 0 |
| `schedules.html` | 200 | 6 | 1443 | 0 / 0 |
| `feedback.html` | 200 | 6 | 443 | 0 / 0 |
| `knowledge.html` | 200 | 6 | 641 | 0 / 0 |
| `intake.html` | 200 | 6 | 27977 | 0 / 0 |
| `cards.html` | 200 | 6 | 18039 | 0 / 0 |
| `events.html` | 200 | 6 | 1273 | 0 / 0 |

## G1–G4逐条自检

- **G1 通过**：R1比较完整正文与实际后端调用，不只比较数字；R2同时验证403正文与业务零调用；R3逐字节比对实际落盘SHA。16页可见内容非空，人工查看回收/商单截图：回收18条、商单34条；随后真实GET /api/intake返回items=18、GET /api/deals返回total=34/deals=34，数字与页面一致；cards接口19条且counts合计19。证据 `/tmp/impl-p4-4-3-after/g1-data.json` 与截图。
- **G2 通过**：reply返回None的真实契约与“None表示未命中”的表述冲突，先向主控问清并获得显式条件分发裁定；另做200→201和分发死区两次负控，证明一致性探针测得出错。
- **G3 通过**：上传分支不适合迁出且会触碰rfile约束，先ask确认原留；generation范围依派工缩到完全不拆；未改逻辑、未改既有测试、未添加代理转发。
- **G4 通过**：与开工tracked SHA快照逐文件对比，唯一有增量的tracked文件为server.py；新增仅五个领域模块、一份路由探针及本报告。`generation/*`、public、原tests、文档与其他既有脏文件均无本单增量。证据source-audit.json与tracked-hashes.json；开工Git差异为 `/tmp/impl-p4-4-3-baseline/git.diff`。

## R1完整方法/路径对照

以下每行“前后状态”相等，完整body、字段结构、调用参数、关键响应头也相同。固定后端中的value/fixture/items是测试数据标记；文件路径列出Content-Type。未知路径仍照原逻辑返回400/404，不用统一404改变原行为。

| 方法 | 路径 | 前后状态 | 正文顶层结构 |
|---|---|---|---|
| GET | `/chat-attachments/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa%5C.png` | 404 = 404 | `{error}` |
| GET | `/api/deals` | 200 = 200 | `{items, total, fixture}` |
| GET | `/api/deals/proposals/status?ids=fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/deals/options` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/deals/review` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/deals/review/report?week=2026-10-26` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/deals/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/intake` | 200 = 200 | `{items, total, fixture}` |
| GET | `/api/intake/analysis-status` | 200 = 200 | `{schema, supported, model_configured, working, note}` |
| GET | `/api/intake/fixture/file` | 200 = 200 | `{bytes_sha256}` |
| GET | `/api/intake/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/cards` | 200 = 200 | `{items, total, fixture}` |
| GET | `/api/cards/fixture/prompt` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/cards/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/collect/platforms` | 200 = 200 | `{items, total, fixture}` |
| GET | `/api/collect/aisa/job?id=fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/collect/status` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/intel/recommendations` | 200 = 200 | `{items}` |
| GET | `/api/intel/status` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/intel/subscriptions` | 200 = 200 | `{subscriptions}` |
| GET | `/api/intel/jobs` | 200 = 200 | `{jobs}` |
| GET | `/api/intel/articles` | 200 = 200 | `{items, total, fixture, albums, album_search}` |
| GET | `/api/intel/accounts?query=fixture` | 200 = 200 | `{candidates}` |
| GET | `/api/intel/articles/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/intel/jobs/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/feedback` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/feedback/observations` | 200 = 200 | `{items, total, fixture}` |
| GET | `/api/feedback/reviews` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/feedback/overviews` | 200 = 200 | `{overviews}` |
| GET | `/api/feedback/review/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/feedback/observation/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/feedback/snapshots` | 200 = 200 | `{items, total, fixture}` |
| GET | `/api/feedback/snapshot/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/schedules` | 200 = 200 | `{items, total, fixture}` |
| GET | `/api/schedules/run/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/schedules/fixture/outsourced_prompt` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/schedules/runs` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/assistant/settings` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/assistant` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/assistant/capabilities` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/assistant/ssh` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/assistant/memory` | 200 = 200 | `{fixture, value, items}` |
| GET | `/api/assistant/job/fixture` | 200 = 200 | `{fixture, value, items}` |
| GET | `/assistant-images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png` | 200 = 200 | `{bytes_sha256}` |
| GET | `/chat-attachments/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png` | 200 = 200 | `{bytes_sha256}` |
| GET | `/api/deals/proposals/status` | 400 = 400 | `{error}` |
| GET | `/api/deals/review/report` | 400 = 400 | `{error}` |
| GET | `/api/intake?limit=1&limit=2` | 400 = 400 | `{error}` |
| GET | `/api/cards?bad=1` | 400 = 400 | `{error}` |
| GET | `/api/intel/accounts` | 400 = 400 | `{error}` |
| GET | `/api/schedules/runs?page=0` | 400 = 400 | `{error}` |
| GET | `/api/assistant/capabilities?bad=1` | 400 = 400 | `{error}` |
| GET | `/api/assistant/ssh?bad=1` | 400 = 400 | `{error}` |
| GET | `/api/assistant/memory?bad=1` | 400 = 400 | `{error}` |
| GET | `/api/deals/fixture/no-route` | 404 = 404 | `{error}` |
| GET | `/api/assistant/no-route` | 404 = 404 | `{error}` |
| GET | `/api/intel/no-route` | 404 = 404 | `{error}` |
| GET | `/api/cards/fixture/no-route` | 404 = 404 | `{error}` |
| POST | `/api/collect/aisa/run` | 202 = 202 | `{fixture, value, items}` |
| POST | `/api/deals` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/deals/from-clue` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/deals/apply-proposal` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/deals/fixture/scan-wechat` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/deals/fixture/evidence` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/deals/import-initial` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/deals/fixture/receipt` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/deals/fixture/deliverable` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/deals/fixture/publication` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/deals/fixture/publication-edit` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/deals/fixture/void` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/deals/unknown` | 404 = 404 | `{error}` |
| POST | `/api/intake/fixture/resolve` | 200 = 200 | `{status, fixture, analysis}` |
| POST | `/api/intake/fixture/analyze` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/intake/fixture/delete` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/intake/delete-preview` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/intake/delete-many` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/cards/fixture/confirm` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/cards/fixture/reject` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/cards/fixture/restore` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/cards/fixture/note` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/cards/fixture/delete` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/cards/cleanup` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/intel/recommendations/scan` | 200 = 200 | `{scan, resolve, listing}` |
| POST | `/api/intel/recommendations/subscribe` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/intel/recommendations/ignore` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/intel/recommendations/unignore` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/intel/recommendations/unknown` | 400 = 400 | `{error}` |
| POST | `/api/intel/subscriptions` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/intel/identify` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/intel/run` | 202 = 202 | `{fixture, value, items}` |
| POST | `/api/intel/unsubscribe` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/intel/articles/fixture/intake` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/intel/unknown` | 404 = 404 | `{error}` |
| POST | `/api/feedback/import` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/feedback/review/fixture/notes` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/feedback/review/fixture/knowledge` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/feedback/overview` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/feedback/observe` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/feedback/observation/fixture/knowledge` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/feedback/observation/fixture/bind` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/feedback/snapshot/fixture/knowledge` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/assistant/attachments` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/schedules/save` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/schedules/batch` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/schedules/fixture/publish` | 201 = 201 | `{fixture, value, items}` |
| POST | `/api/schedules/fixture/cancel` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/schedules/fixture/run` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/schedules/fixture/pause` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/schedules/fixture/resume` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/assistant/settings` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/assistant/select-model` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/assistant/test-settings` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/assistant/send` | 202 = 202 | `{fixture, value, items}` |
| POST | `/api/assistant/stop` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/assistant/new-chat` | 200 = 200 | `{fixture, value, items}` |
| POST | `/api/assistant/workflows/fixture/save` | 200 = 200 | `{fixture, value, items}` |
