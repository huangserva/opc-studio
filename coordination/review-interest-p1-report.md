# 1 期兴趣通道独立复核报告

## 决定要点

| 项目 | 决定 |
|---|---|
| 结论 | **不通过最终验收：真实文件验收待补；代码及夹具复核通过** |
| H / M / L | 0 / 0 / 1；L1 为原子保存不保留文件权限，非阻断建议 |
| 文件与 SHA | 开工13文件匹配；收尾server.py因并行健康接口增加1行，另外12文件仍匹配；本次只新增本报告 |
| 独立验证 | 定向 Python 106/106；另外独立反例 36/36；隔离浏览器夹具 23/23；真实 8766 只读浏览器 5/5 |
| 性能 | 独立 210 条 + 超 2 MiB 坏文件约 11.1ms；HTTP 5 次 68.7/36.8/40.0/37.3/41.4ms |
| 服务装载 | launchctl list 查得 PID 99710，与实施最终 PID 一致；真实兴趣/清单接口均 200 |
| 真实文件 | 最后检查见文末时间，北京时间，interest/*.json=0；未到 10:30，真实收藏链路未知 |
| 真实写入 id | 无；只在 /tmp 收藏 x:101、x:991，未对真实 8766 发收藏/反馈/设置保存 POST |
| 证据 | `/tmp/review-interest-p1/`；1440 / 390 截图、脚本及 JSON/日志，详见下文 |

## 逐项验收

| 合同项 | 结果 | 独立证据与边界 |
|---|---|---|
| 1 清单原文保真 | **通过当前文件保存保真；未知历史恢复** | 当前文件有标题、引言、5 个方向、说明 3 条，SHA256 为 `54554667a5b25a88e7c4042dad99e8ab160120f7d6452c2820a9054b07b95a99`，与实施操作前备份 `/tmp/impl-interest-p1/live-topics-before.md` 字节一致。用当前真实文件的 /tmp 副本增删一项后整份字节相同，CRLF/额外段落由定向测试覆盖；隔离设置页同样通过。未取得 Grok 09:38 原始文件或其摘要，不能证明与该原件逐字恢复。 |
| 1 方向验证与冲突 | **通过** | 独立验证空项、首字符 #、81 字、换行/Unicode 行分隔符、超过 30 项拒绝；重复项按实施合同去重为单项，并非报错拒绝。独立 mtime 纳秒改动拒绝覆盖；隔离 HTTP 陈旧 revision 返回 409，缺 X-Article-Studio 返回 403。 |
| 1 权限与读取 | **通过读取；L1** | 当前 UID=文件 owner UID=501，文件 0600，当前进程 os.access(R_OK)=True。/tmp 中从 0644 保存确实变 0600；代码未复制旧 mode，见 L1。不能从当前 stat 反推原件原来一定是 0644。 |
| 2 写边界 W2 | **通过** | 独立探针先配置 /tmp 的 GROK_BOT_REPORTS_DIR；目录差异只有清单及 feedback/studio-feedback.json（夹具报告本身由探针预先创建）。模拟 os.replace 失败后原文不变、无 .interest-topics-* 残留。真实报告目录的文件清单、大小、mtime_ns 在 36 项探针前后完全相同；106 项中另有真实目录无变化检查。 |
| 3 读取健壮性 | **通过** | 独立构造缺 id/字段、空对象、非对象、x.com.evil.com、twitter.com、http、query、fragment、id/url 不符；保留可识别不完整条目、移除可点原帖、累计异常，不影响有效条目。空文件与半写 JSON 标 bad_files；[]、items=[]、本次无正常空态；.tmp 跳过并计异常；>2MiB 跳过；重复 id 用 collected_at 而非文件名选新，合并 also_posted_by。详见 independent.json。 |
| 3 XSS 与危险链接 | **通过** | 独立探针验证 usable javascript:/data: 变为无链接；浏览器验证 full_text 的 script/onerror 作为字面文本，window.PWNED 未置位，无 script/onerror/危险 href 注入；article 清洗后保留正文，thread/quoted 可见，资源外链 rel=noopener noreferrer。 |
| 4 页面与按钮 | **通过** | 隔离浏览器实际点击今日→兴趣→全文。兴趣列表独立、卡片相关度/作者/发布时间/来源 SVG 可见，六类资源中文类型可见；全文逐字相同，坏条目标内容不完整；卡片三个按钮为存进素材库/准/不准，无拿去写。1440/390 已截图并查看，390 无横向溢出。异常数字在来源“异常”按钮弹窗中展示，接口同时给 bad_files/bad_entries。 |
| 5 收藏、反馈及兼容 | **通过夹具；真实收藏未知** | 读取/准/不准不建 items.db；重复存只有一条 x:991 且 status=kept；隔离浏览器 x:101 收藏后素材库搜索可见；反馈带 channel=interest。未改 grok_bot_radar.py 和 ItemsStore，爆款原路径定向回归通过；兴趣入口直接分流，不调用爆款 snapshot 或 linux.do 刷新。 |
| 6 新字段兼容 | **通过** | 独立 daily/alerts 夹具加入全部 6 个新字段，并给 bool 字段对象/字符串、原作者列表/null、粉丝字符串/无效值、bookmarks 数字字符串/null、collected_at 列表/null、crowding 对象/列表等；grok_bot_radar.load_reports 与 today_view.page_view 均正常。定向测试另覆盖 crowding 字符串。这里只证明容错，不表示界面展示了这些新增字段。 |
| 7 性能 | **通过** | 独立构造 7天×2份×15条及超限文件，读取约 11.1ms；随机端口实际 HTTP 212 条数据 5 次均低于 1 秒。真实空兴趣接口约85.6ms，清单约1.4ms；真实有条目时性能仍待真实文件复验。 |
| 8 测试质量 | **通过本单未放宽断言** | 与最终 T5 `/tmp/review-t5-m1/sha.json` 对比，test_linuxdo_source.py、test_today_view.py、today_t5.cjs、today_page.cjs、library_page.cjs、write_and_publish.cjs 6 文件 SHA 完全一致。设置/Grok/items 三个单测和设置 E2E 与既有 P4 副本一致。本单仅新增兴趣测试；未放宽旧断言。 |
| 8 素材库陈旧导航断言 | **通过归因** | tests/e2e/library_page.cjs:46 硬编码 details 下5条；真实隔离浏览器查得11条（工具2+旧版9）。workspace-nav.js 与本单前 P4 副本一致，素材库脚本 SHA 与最终 T5 一致，因此该失败不是本单引入。未为了绕过它修改正式脚本；本次兴趣收藏→素材库搜索单独通过。 |
| 9 真实文件端到端 | **未知：真实文件验收待补** | 真实目录无 JSON；未造真实报告、未把夹具当成真实验收。真实 8766 已走今日→兴趣空态及设置读取，不能替代看真实全文→存→素材库搜到。合同允许此时完成复核报告、由主控另派补验。 |

## 发现与建议

**L1：清单保存改变既有文件权限，建议保留旧 mode。** `interest_channel.py:100–108` 使用 tempfile.mkstemp（0600）后直接 replace，没有复制原文件 mode。独立 /tmp 复现 0644→0600；原子替换足以解释观察到的权限收紧，但缺少 09:38 原始 stat，无法确定真实文件曾经是0644，也不能精确归因是哪一次保存。

当前文件属主和读端运行用户相同的前提下，0600 不妨碍 Grok Bot 读取，当前用户实测可读；未检查或改动 Grok Bot 配置，因此不声称独立验证过 Bot 的运行身份。本项不阻断现有同用户部署。建议后续保存既有文件时在临时 fd 上沿用原 mode，新建文件仍可用0600；本次只读复核不修改代码或真实权限。

**历史原文证据边界：** 当前说明确实有3条，第三条是 Grok 建议方向交黄总决定；与实施保存前备份一致，且本次增删保真已验证。但两份文件均晚于原创建时刻，不能据此断言09:38原文完全恢复。若主控要关闭这一历史疑点，需要 Grok 初始内容或当时摘要；现有证据未观察到内容丢失。

## 执行与证据

复核 Python 均用 `../article-importer/.venv/bin/python`。没有运行全量，因此未占用全量 discover 资源；实施全量日志属于复用证据，不列为本次独立复跑。未重启真实服务（本次无后端修改），已用约定的 launchctl list 查询核对 PID99710，且真实新增接口200；未使用 computer use、真实桌面或用户 Chrome profile，未动 Grok Bot 设置、飞书、AIsa、提交或推送。

- `directed.log`：106 项定向 Python 全过。
- `independent.py` / `independent.json` / `independent.log`：36 项独立构造检查；曾因探针遗留同时间重复夹具导致自身预期失败，清空前一场景文件后再测通过，无正式代码修改。
- `browser.cjs` / `browser.log` / `fixture-evidence.json`：/tmp 工作区、随机端口、独立 profile 的23项端到端，基于现有兴趣 E2E 的 /tmp 副本增加六类资源检查，未覆盖既有证据目录。
- `fixture-interest-1440.png`、`fixture-interest-390.png`、`fixture-detail-1440.png`、`fixture-detail-390.png`、`fixture-library-1440.png`、`fixture-settings-1440.png`、`fixture-settings-390.png`：夹具截图。
- `live-readonly.cjs` / `live-browser.json` / `live-browser.log`：真实8766隔离浏览器5项只读检查；`live-empty-1440.png`、`live-empty-390.png`、`live-settings-390.png`。
- `live-probe.json`、`topics-real-metadata.json`：真实接口耗时和清单元数据。
- `t5-final-test-sha.json`：以最终 T5 为准的6项测试SHA核对。`t5-test-sha.json` 是对早一版T5的初查，linux.do测试的差异已由最终T5证据消除，不属于本单变化。
- `server.py.diff`：与既有 P4 源码副本相比只有约定的3行兴趣清单路由；另存有 today_view/item-card/list-reader 差异供查阅，该P4副本早于T5，不能将其中全部差异归因本单。

本次没有新增真实 items id。最终一期验收须在真实文件出现后，用隔离 Playwright 对真实8766执行兴趣全文→存→素材库搜索，记录真实写入id并补1440/390截图；此项未完成前，不把1期标成最终通过。

## 收尾版本边界

开工与测试期间 server.py SHA 为实施报告的 `8ddb12c597db7acd25d5739530d6e8f2bf7c915fa4d4f4245e53c6dc38cd0f18`；收尾在10:11:04发生并行改动，当前 SHA 为 `728aa7b491bec880ffb3794a7149df695024afab5a43e4212cc6f28d4db7ea28`。从既有源码加本单3行重建旧版并核验SHA成功，逐字差异只有 GET 路由白名单增加 `(parts.path == '/api/schedules/health') or` 一行，未触及兴趣路由或逻辑；其余12文件仍与实施报告一致。已通过Orca escalation/status通知主控；主控于10:12:16确认归属为授权的H4实施单 task_7897be008b4a（term_02017284），与兴趣通道无关，并明确按已验证版本交付、不重跑。本复核未修改它、未回退。

本次测试证据对应改动前的server版本；这项独立健康接口增量应由所属任务验收，不把本报告当作该增量的验收。证据为 `server-tested.py`、`server-concurrent.diff`、`source-sha-final.json`。

最后真实文件检查：2026-10-30T10:12:27.531824+08:00，JSON文件数=0。
