# T5 实施报告

## 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **通过**（实施自测；待独立复核） |
| H / M / L 自查 | 功能自查 0 / 0 / 0；既有失败另列 |
| 改动文件与 SHA256 | 12 份代码/测试文件的 SHA256 见末尾；另交本报告 |
| 定向测试 | 87 项通过（含今日页 24、linux.do 11，及素材库/链接前置组合） |
| 浏览器 | 新 T5 夹具 22、原今日页 56、真实服务 16、写作 28 项通过 |
| 全量 | Python 3581 项：3573P / 0F / 1E / 7S；UI 723 项：719P / 3F / 1S，均无新增失败 |
| 服务 | 后端已重启加载新代码，新 PID **61473**（初始 12620） |
| 截图/证据 | `/tmp/impl-t5/`，原今日页证据 `/tmp/today-e2e-raLT0a/` |

任务：`task_3ce4b8625815` / Dispatch `ctx_f50d87a4097c`。未提交、未推送；未改 `server.py`、`list-reader.js`、视频实验室或 generation 保护文件。

## 实现与合同变更

- 后端唯一阈值 `today_view.HOT_VIEWS = 100000`，摘要及详情下发 `hot`。前端只消费布尔字段，卡片和阅读器同时显示“火爆”，现有档位规则保留。热度/来源均加入白名单校验，可组合时间、来源、方向、搜索。
- G/A/V/L 使用本地内联 SVG，位于卡片右上角、阅读器标题区；均有 title 和 aria-label，多来源并排。其他来源名保留，兼容共享组件的素材库来源。
- `linuxdo_source.py` 抓公开 `top.rss?period=daily`，缓存 `workspace/linuxdo/top-daily.json`，状态存 `status.json`，`.tmp` 后原子替换，跨进程 flock + 进程内 pending 去重。今日 API 启动后台线程后立即只读快照；刷新中前端每 1.2 秒更新列表，结束即停止。
- 初版禁止代理在本机直连超时，正常本地代理则 200 / 1.127 秒。现使用无凭证系统代理；拒绝包含用户名/密码的代理配置。抓取调用本机 **curl 8.7.1**，`-q` 禁止 curlrc、仅 HTTPS、不跟随重定向、不带 Cookie/来源身份凭据；`--max-time 9.5` + 子进程总 timeout 10 秒，`--max-filesize 2097152` 提供未知 Content-Length 的流式上限。本机 manual 已确认 8.4 起支持该流式上限，且用本地 TLS 夹具实测。
- **主控本轮明确调整原合同节流要求**：失败约 10 分钟退避，成功才按 1 小时节流。已用 `RETRY_SECONDS=600` / `REFRESH_SECONDS=3600` 实现并验证 9:59 不重试、10:00 重试、成功后 59:59 不刷新、60:00 刷新。主控另允许仅清除本次初版失败状态一次以复验；已留存 `/tmp/impl-t5/first-failed-status.json`，快照未手动改动。
- RSS 正文通过现有 `html_sanitize.SafeHTML` 清洗为纯文本，再用 DOM textContent 渲染。详情、收藏与拿去写复用今日页/ItemsStore/ItemWriter，使用与素材库一致的 canonical URL ID，避免另存一份不同 ID。
- 回复数采用 `max(0, N 个帖子 - 1)`，即可见帖子总数扣除首帖；缺统计保持 None，不使用参与者人数。Discourse 官方代码的评论统计同样扣除首帖，可参见 [官方 WP 插件](https://github.com/discourse/wp-discourse/blob/main/lib/discourse-comment.php) 与 [Topic 模型](https://github.com/discourse/discourse/blob/main/app/models/topic.rb)。真实验收条目“28 个帖子”映射回复 27；浏览数恒为 None。正文是 RSS 提供的首帖正文，不采集后续全部讨论回复。

## G1–G6 逐项自测

| 条目 | 结果与证据 |
|---|---|
| G1 外部源安全与资源上限 | 通过。真实 curl + 临时自签 CA 的本地 TLS 服务器验证：未知长度超大 body、超大 Content-Length 均被拒绝；真实 0.3 秒子进程超时及时终止；403 挑战页、非法 XML 被拒绝；正常 RSS 成功。另测挑战 HTML 伪装 200、XML 实体声明、script/onerror/javascript: 描述。异常来源状态变红且旧快照字节不变；浏览器不能执行脚本、没有危险链接，403 后旧全文仍可打开。见 `/tmp/impl-t5-transport.log`、`/tmp/impl-t5-directed-final.log`、fixture-results.json。 |
| G2 凭证与日志 | 通过。请求无 Cookie/Key/来源身份头；curlrc 禁用，带口令代理拒绝；错误只记录 HTTP 状态或固定中文原因，不持久化响应体/请求头/异常原文。夹具中的伪 secret 未进入来源快照；未使用 launchctl print / ps eww / env / cat plist。启动 PID 仅用合同指定 launchctl list。 |
| G3 阈值边界 | 通过。99999=False、100000=True、None=False、字符串“100000”按 grok.number=True；视频 plays=150000=True。火爆与爆款/低粉爆款并存；火爆 + linux.do 为空；48h/来源组合正常。 |
| G4 测试网络隔离 | 通过。新增 Python 用 NetworkGuard；实际运输层测试仅 HTTPS 回环端口并用临时 CA 验证证书。新增夹具 e2e 后端替换 RSS 且套 NetworkGuard，浏览器拒绝所有外部请求（结果 network=[]）。原今日页及素材库/写作 e2e 的隔离后端禁用自动 RSS 刷新。只有明确 `--real` 的合同真实验收及其定位诊断访问真实 RSS。 |
| G5 首屏性能 | 通过。RSS 夹具延迟 8 秒，首屏 142.6 ms，期间 RSS 尚未结束，完成后列表自动出现 L。真实服务首屏 211.1 ms；原今日页 56 项也继续通过 <1 秒检查。 |
| G6 既有边界 | 已核对并遵守。没有修改反例 15 的相对时间夹具；本次原今日页 56 项（含反例15）通过。运行在北京时间 08 点以后，不声称已修复或覆盖原有 00–08 点时间边界。 |

## 浏览器真实路径与副作用

`/today.html` → 48 小时 → 热度“火爆” → 检查所有条目浏览 ≥10 万/卡片与阅读器徽标 → 来源 linux.do 叠加火爆为空 → 清除火爆 → L 列表 → 全文 → 准/不准/存 → 素材库按标题搜索命中。1440/390 均有来源图标，无标题/动作遮挡或横向溢出。

当前真实“今天”没有十万浏览条目，“48 小时”有；筛选符合数据，而非凑出火爆条目。真实 RSS 缓存 30 条，其中当天 2 条。真实验收按照合同保存了一条 linux.do 素材及反馈；没有生成真实 AI 写作任务。隔离夹具另验证了“拿去写”底稿全文与链接均保留。

截图：`real-1440-hot.png`、`real-1440-linuxdo.png`、`real-library-saved.png`、`real-390-list.png`、`real-390-reader.png`，及同名 `fixture-*`，均在 `/tmp/impl-t5/`。已实际打开核对桌面和手机截图。

## 全量与既有失败

- 最终 Python：`../article-importer/.venv/bin/python -m unittest discover -s tests -v`，**3581 项，3573P / 0F / 1E / 7S**，394.845 秒。唯一 E 为受保护的 `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，原文为“参考素材宽高须为 32 的倍数”，与合同基线一致。本次新增 12 项均通过。
- UI：`npm run test:ui`，**723 项，719P / 3F / 1S**，15.261 秒。三项失败均在受保护 `tests/test_lab_test_drama_ui.cjs`：默认四幕预设、切换赛博预设、生成单幕参数（hybrid 与 singularity），与合同基线一致。
- 定向组合：`../article-importer/.venv/bin/python -m unittest tests.test_items_store tests.test_link_index_orphan tests.test_linuxdo_source tests.test_today_view -v`，**87 项通过**。
- 浏览器：`node tests/e2e/today_t5.cjs` 22 项；`node tests/e2e/today_t5.cjs --real` 16 项；`node tests/e2e/today_page.cjs` 56 项；`node tests/e2e/write_and_publish.cjs` 28 项。
- 已先向主控登记取得全量独占许可；最终全量结束后已通过 Orca 通知释放独占。
- 最终日志：`/tmp/impl-t5-python-accepted.log`、`/tmp/impl-t5-ui-full.log`、`/tmp/impl-t5-directed-final.log`、`/tmp/impl-t5-new-e2e.log`、`/tmp/impl-t5-real-e2e.log`、`/tmp/impl-t5-existing-e2e.log`、`/tmp/impl-t5-write-e2e.log`。

额外运行的 `tests/e2e/library_page.cjs` 在旧导航断言中失败：期待旧版分组 5 项，而现有 `public/workspace-nav.js` 为 9 项（该文件开工前已有未提交改动，本次未改）。这是额外验收的既有夹具不匹配，没有为本任务修改导航或放宽该断言；本次独立真实素材库搜索路径已通过。

前两轮全量出现的新增 1F 已定位为测试 mock 泄漏：素材库旧用例手动调用 TodayTests.setUp/tearDown，绕过 addCleanup。已在 tearDown 显式停止补丁，并保留 setup 异常 cleanup；组合复验 87 项通过。失败记录保留在 `/tmp/impl-t5-python-full.log`、`/tmp/impl-t5-python-final.log`，不冒充最终验收结果。

## 文件 SHA256

| 文件 | SHA256 |
|---|---|
| `linuxdo_source.py` | `f3265049aabdd587c6d88d39c45a9f9ecf751f4fe34f6154ba7d999cbebf36ef` |
| `today_view.py` | `3d19ea2b422c6a21e72e872d8621ee08c6c3bcd3296451321edcd7f278c64a59` |
| `public/today.html` | `def176ab55d6eb00e34d1d1159ef70ed39b6646515bfb4448fa230be526da808` |
| `public/today.js` | `c89ee976790854fc6748c4d94f645ad9545e1bd3499e642373a863cadd5fb42b` |
| `public/today.css` | `57464e7c80b86b0fc748619fd7422cb96f84fde8ad1c4e34e92a087a0e1fc383` |
| `public/components/item-card.js` | `d2e1727b6cf6d954fcc9e98931ee94353e64ad90aa14d13e246eedf9956c2262` |
| `tests/test_linuxdo_source.py` | `db5ea74e94cc6aa5eb97b0c8966a78fe8e9727240e00956765e5aafb346c7a6e` |
| `tests/test_today_view.py` | `a12c808a4eb859f5a53b1ea5d30a65e34d00c88b31eb609098cdc1090eeeac6d` |
| `tests/e2e/today_t5.cjs` | `903265d3ba8136b1726b35ee87f71e72b9f01e5938fd7023663a3205ab635712` |
| `tests/e2e/today_page.cjs` | `dde63822e7926cc171c96d97e31befd03c3e9e205f27fb108d11a65d1a8ab0e1` |
| `tests/e2e/library_page.cjs` | `dc4505266cf7db70165a42474be82593c195b826cb4466f9c93894cdb5c1b4ff` |
| `tests/e2e/write_and_publish.cjs` | `c15b0fe2f310d98608c50f1709bef533685573198d239b38116867fc38c4a168` |


## 返修 M1

任务 `task_bd01b4cbcde2` / Dispatch `ctx_a5049d4896d7`，仅修复复核报告中的正文数字误算回复与 RSS 尾段清理。不提交、不推送；代码仅改 `linuxdo_source.py`、`tests/test_linuxdo_source.py`，另按要求追加本节。视频实验室及 generation 保护范围未改。

### 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **通过**（M1 实施自测；待独立复核） |
| H / M / L 自查 | 0 / 0 / 0（原 M1 已修复；既有全量失败另列） |
| `linuxdo_source.py` SHA256 | `63c3845c1796e113511ab78a985c83905c514fadfed4826b9f490db1ecf92855` |
| `tests/test_linuxdo_source.py` SHA256 | `274a1c0b4870e6cd68d2a10e0d3ececdc3536e43e31b46e12fa0f7ad53c4cc8d` |
| 负对照 | 新增 6 个测试在旧代码下全部失败，含子测试共 8 处失败断言 |
| 定向测试 | linux.do 17 + 今日页 24 = **41/41** |
| 隔离浏览器 | T5 **22/22**；原今日页 **56/56**；真实只读 **8/8** |
| Python 全量 | **3587 项：3579P / 0F / 1E / 7S**，395.358 秒，唯一 E 与既有保护范围基线一致 |
| 8766 新 PID | **13942**，重启前 **61473**；真实浏览器验收针对新 PID |

### 修复及反例自测

- 仅识别全文最后一行的完整 `N 个帖子 - M 位参与者` 统计，允许空白和尾部“阅读完整话题”入口；没有完整尾部统计时 `replies=None`。不从正文数字或参与者人数猜回复数。
- 删除匹配的尾部统计段及末尾独立“阅读完整话题”入口，正文含“个帖子”的原句、此前出现的历史统计保留。为避免 HTML 紧邻的内联链接与下一段统计合并，仅在 RSS 清洗子类补回 `p` 起始段落边界，公共清洗器未改。
- 新增 6 个测试覆盖：正文 100 + 尾部 8 → 回复 7 且正文原样保留；只有正文数字 → None；尾部 1 帖 → 0；正常 28 帖 → 27（含空白及 small）；多处统计仅取末尾且保留前面正文；正文中统计/句尾帖子数字不能冒充尾部统计。既有安全映射夹具继续通过。
- 负对照先只加测试、保持旧代码，执行这 6 个测试得到 `Ran 6 tests / FAILED (failures=8)`，包括明确的 `99 != 7` 和 `99 is not None`；之后才改实现。旧文件副本及日志在 `/tmp/impl-t5-m1/`，未在共享目录切换旧版本。

### 部署与真实只读验收

`launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio` 后，仅用 `launchctl list` 核对 PID 由 61473 变为 13942。未读取含凭据的 launchd 配置或环境。

旧快照仍在一小时节流期内，先经 Orca 向主控确认，获准仅调用一次正常 `linuxdo_source.refresh` 走真实抓取与原子写入。本次在独立 Python 进程临时设置运行期 `REFRESH_SECONDS=0`，使用真实当前时间执行一次 refresh；不删状态、不手改快照、不改生产节流常量。快照从 `2026-10-30T00:43:26.030413+00:00` 更新为 `2026-10-30T01:10:39.519359+00:00`，30 条，错误为空。执行该次抓取的原始 RSS 留在 `/tmp/impl-t5-m1/real-rss.xml`。

隔离 headless Playwright 使用 `/tmp/t5-m1-profile-*` 独立 profile，对 8766 仅 GET。走 `/today.html` → 48 小时 → linux.do → 打开真实条目 `https://linux.do/t/topic/3092762`；原始 RSS 的末尾 small 为 **55 个帖子 - 36 位参与者**，API/列表/阅读器均为 **回复 54**。全文 501 字符，与从原始 RSS 去除尾部两段后清洗的正文逐字一致，末尾为 GitHub 开源链接文本，未残留统计及“阅读完整话题”。浏览器写请求 0、外部资源请求 0；未点击反馈、收藏、拿去写或原帖。截图 `real-reader.png` 与 `real-reader-tail.png` 已实际打开核对。

### 验证命令与证据

- `../article-importer/.venv/bin/python -m unittest tests.test_linuxdo_source tests.test_today_view -v`：41/41，`directed.log`。
- `node tests/e2e/today_t5.cjs`：22/22，`today-t5-e2e.log`；最终隔离目录 `/tmp/today-t5-Bx31Ux`。
- `node tests/e2e/today_page.cjs`：56/56，`today-page-e2e.log`；隔离目录 `/tmp/today-e2e-tnDGTu`。此次未修改时间夹具或既有断言。
- `node /tmp/impl-t5-m1/real-readonly.cjs`：8/8，`real-readonly.log/json`、`real-expected.json`、`real-reader-tail.png`。
- `../article-importer/.venv/bin/python -m unittest discover -s tests -v`：经主控允许取得独占后启动，`python-full.log`；**3587 项，3579P / 0F / 1E / 7S，395.358 秒**。唯一 E 是受保护的 `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，原文 `ValueError: 参考素材宽高须为 32 的倍数`，名称和异常均与前次实施、独立复核基线一致，无新增失败。结束后已通过 Orca 通知主控释放独占，未触碰该范围。
- 负对照 `negative-old.log`，代码差异 `source.diff/tests.diff`，SHA `sha.json`，单次刷新前后状态 `refresh.json`；上述证据除另外列明的 e2e 隔离目录外，均在 `/tmp/impl-t5-m1/`。
