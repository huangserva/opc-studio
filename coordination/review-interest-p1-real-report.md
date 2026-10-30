# 1 期兴趣通道真实文件补验报告

## 决定要点

| 项目 | 决定 |
|---|---|
| 结论 | **通过本次真实文件补验**；原复核第 9 项「真实文件验收待补」已有实证 |
| H / M / L | 本次新增 0 / 0 / 0；原报告其他建议不在本次复验范围 |
| 真实文件 | `2026-10-30-1038.json`：12 条、12 个唯一 id、27 字段每条齐全、坏条目 0 |
| 独立浏览器 | 16/16 检查通过；12 条 API 全文与文件一致，所选条目浏览器全文逐字一致 |
| 真实写入 id | **`x:2115862466068623609`**（@rapidmlx）；两次点存、数据库仅 1 条、`status=kept` |
| 性能 | 5 次 187.1 / 48.1 / 54.9 / 63.5 / 33.6 ms；均 <1 秒 |
| 服务 | 开始及结束 `launchctl list` 均为 PID **93453**；本次未改后端、未重启 |
| 截图 | `/tmp/review-interest-p1-real/`：兴趣列表／全文／素材库各有 1440、390 两种宽度 |
| 正式文件改动 | 仅新增本报告；未改代码、未提交、未推送 |

## 逐项验收

| 合同项 | 结果 | 证据 |
|---|---|---|
| 1 文件结构与坏条目统计 | **通过** | 文件声明 count=12，items=12，唯一 id=12；27 个约定字段各出现 12/12，字段缺失 0，独立校验坏条目 0。接口 `report_count=1 / bad_files=0 / bad_entries=0 / duplicates=0`。详见 `file-evidence.json`。 |
| 2 今日→兴趣列表 | **通过** | 在真实 8766 的隔离 Chromium 中打开今日页、点击兴趣；页面 12 张卡片与接口 12 条的 id 集合完全一致，均为兴趣层级，无爆款混排。 |
| 2 全文保真 | **通过** | 12 条详情 API 的 `full_text` 均逐字等于原始 JSON；打开所选 @rapidmlx 条目，浏览器 `.full-text.textContent` 与原文件严格相等（Python 294 个 Unicode 码点；JS 296 个 UTF-16 码元，差异来自原文字符编码计数方式）。页面同时展示可用链接及串推补充。 |
| 2 收藏及素材库搜索 | **通过** | 收藏前只读 SQL 查询确认真实报告全部 12 个 id 均不在 items；只对 `x:2115862466068623609` 点存两次，两次 POST 都返回 200 与 `status=kept`。详情 API、素材库搜索接口及真实素材库搜索界面均找到该条；最终只读 SQL 查得该 id 恰好 1 条、状态 kept，其余 11 个真实报告 id 仍无记录。 |
| 3 1440 / 390 截图 | **通过** | 六张截图均已查看，卡片、全文阅读区、素材库结果可见；390 下列表、阅读区、素材库均无横向溢出，页面脚本错误 0。截图是滚动容器当时位置，全文逐字验证依据 DOM，不把截图当作全文覆盖证明。 |
| 4 接口耗时 5 次 | **通过** | 真实 `GET /api/today?channel=interest&range=today`，187.1 / 48.1 / 54.9 / 63.5 / 33.6 ms，均值 77.4 ms、中位 54.9 ms、最大 187.1 ms；测量含本机 HTTP 往返和 JSON 解码。 |
| 5 写入边界 | **通过** | 浏览器仅允许该 id 的两次 keep POST，实际捕获恰好这两次请求；未点击准／不准，未访问设置写入。原 JSON、同名 MD、interest-topics.md 的 SHA256、大小、mtime_ns 前后全部一致。 |

## 字段统计

字段口径依据实施合同引用的 `twitter-intel-handoff-r1.md` A.一.4。下列字段均为 **12/12 存在**；允许为空的补充正文及转推字段不按缺字段计数。

| 字段 | 非空／类型及校验 |
|---|---|
| id、url、author_handle | 各 12 个有效字符串；数字 id 唯一，规范 https://x.com/作者/status/id 与 id、作者逐条一致 |
| published_at、collected_at | 各 12 个带时区且可解析的时间 |
| topic、what_new、why、full_text | 各 12 个非空字符串 |
| usable | 12 个非空列表，共 21 个资源；每项有 type/url/note，type 合规，链接均 http/https |
| relevance | 12 个整数，均在 0–3 |
| author_followers、views、likes、bookmarks、retweets、replies | 各 12 个原始非负整数 |
| content_complete、is_retweet | 各 12 个布尔值；content_complete 全部 true |
| article、quoted、incomplete_reason | 各 12 个 null；未提供这类补充或不完整原因，当前 12 条均标完整 |
| thread | 1 个非空列表，11 个 null |
| media | 12 个列表，其中 8 个非空 |
| original_author、original_author_followers | 各 12 个 null，当前文件未提供原作者信息 |
| also_posted_by | 12 个列表 |

资源分类：weights 3、tutorial 4、other 4、repo 9、api 1。这里只核对文件结构与展示一致性，未访问外部资源验证其可用性，也未向 X 核实原帖事实。

真实 JSON 大小 23694 字节，SHA256：`08c3b375769383d39b05cc344ace41c71016421bb5b7789c68efc8d08816831f`。统计与逐条错误数组保存在 `file-evidence.json`，未把帖子全文复制进报告。

## 证据与执行边界

证据目录：`/tmp/review-interest-p1-real/`。

- `probe.py`、`file-evidence.json`：独立结构校验、字段统计、收藏前只读数据库状态、受保护文件摘要与 mtime。
- `browser.cjs`、`browser.log`、`browser-evidence.json`：真实页面的 16 项检查、5 次耗时、两次 keep 的请求及响应、页面错误计数。
- `final-evidence.json`：2026-10-30 11:53:51 +08:00 的最终只读 SQL 结果、原始文件／兴趣清单前后不变证明。
- `interest-1440.png`、`interest-390.png`：兴趣列表。
- `detail-1440.png`、`detail-390.png`：真实全文阅读区；390 图有已保存状态。
- `library-1440.png`、`library-390.png`：真实素材库搜索结果，仅该条，标已收藏。

浏览器采用 `/tmp/review-interest-p1-real-profile-*` 独立 profile，headless Chromium 145.0.7632.6，外部网络请求由路由拦截。首次使用本机 Chrome channel 未完成启动，关闭本次自建浏览器后改用已安装的 Chromium headless shell；首次没有完成检查或写入（`browser-failure.json` 的 checks/mutations 均为空），失败记录保留，成功记录另存 `browser-evidence.json`。未使用 computer use，也未操作黄总真实桌面或真实 Chrome profile。

未运行全量 Python／UI，不与 H1 全量争用资源；本次 Python 工具使用 `../article-importer/.venv/bin/python`。收藏的后台流程按既有实现同步了该条的反馈及 items 状态，属于合同授权的一条收藏写入；未额外发送反馈、改兴趣清单、改 Grok Bot 设置、触碰飞书或执行提交推送。
