# 中枢期 H1 实施报告

## 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **实施自测通过，待独立复核**；外部心跳与设置页总览均已装载 |
| H / M / L 自查 | 0 / 0 / 0；UI 既有测试清理介入单列披露 |
| 文件与 SHA256 | 10 份源码/测试 + 本报告；完整清单与改前/后 SHA 见下文 |
| Python 全量 | **3665 项，0F / 1E / 7S，486.779 秒**；唯一 E 与既有 video_lab 一致 |
| UI 全量 / H4 专项 | **723 项，719P / 3F / 1S**；H4 `test_studio_watchdog.py` **26/26** |
| 浏览器 | 新 H1 隔离 **14/14**；旧设置 **23/23**；正式 8766 只读 **8/8** |
| 正式装载 | 已按主控授权重启一次，PID **54621 → 93453**；调度器健康、缓存 9 项 |
| 1440 逾期演示截图 | `/tmp/impl-hub-h1/overdue-1440.png` |
| 390 逾期演示截图 | `/tmp/impl-hub-h1/overdue-390.png` |

## 全量回归与正式装载

主控 `msg_b1bebb328869` 释放全量后，在正式项目目录执行 `../article-importer/.venv/bin/python -m unittest discover -s tests`，随后执行 `npm run test:ui`，没有两份全量并发。后端重启按主控 ask 的部分释放授权先行执行；重启前确认 running_count=0，仅用约定的 kickstart，未打印 plist 或环境变量。

| 验证 | 结果 | 证据 |
|---|---|---|
| Python 正式全量 | 3665 项，0F/1E/7S；486.779 秒，退出码 1 来自既有 E | `python-full.log` / `python-full-summary.json` |
| 总数变化 | 3638 基线 + H4 M1 新增 6 项 + H1 新增 21 项 = 3665；H4 数量依据其实施报告 | `coordination/impl-hub-h4-m1-report.md`：原 20 + 新增 6；本单专项 21 |
| 唯一 Python E | `test_multi_segment_sequence_video_builds_director_timeline (test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline)`；`ValueError: 参考素材宽高须为 32 的倍数`，与 `/tmp/impl-hub-h2/full-python.log` 同 ID、同异常文字 | `python-full-summary.json`；没有改保护范围 |
| H4 M1 正式可比证据 | 已由本次 3665 项正式全量覆盖，未出现任何看门狗失败；另按主控要求单列专项 **26/26，0F/0E/0S，8.457 秒** | `watchdog-directed.log`；脚本及测试 SHA 与开工一致 |
| UI 正式全量 | 723 项，719P/3F/1S，取消 0，212448.274708ms；3 个失败名与一期 `ui-full.log` 完全相同，均位于 `test_lab_test_drama_ui.cjs:136/151/166` | `ui-full.log` / `ui-full-summary.json` |
| 新服务装载 | PID 93453，external_count=9，builtin_count=10，checked_at=2026-10-30T03:40:45.546943+00:00，health.ok=true | `live-loaded.json`；重启前 `live-before.json` 无 external 字段 |
| 正式浏览器 | 8/8，10 内置 + 9 外部/服务；只读零写请求、无脚本错误、390 无溢出、digest 待确认；缓存 HTTP 五次约 13–16ms | `live-browser.json` / `live-browser.log`；`live-1440.png` / `live-390.png` |

**UI 收尾介入（如实保留）：** 原始全量的 `test_radar_links_e2e.cjs` 节点与其 headless Chrome 迟迟未退出，未输出总汇。源码注册了先 `server.close()`、后 `browser.close()` 的清理钩子，可能有连接等待（推断）；本单未改该测试。实际观察：核实本次 UI 根进程→该测试节点→浏览器的父子关系，以及 `--headless` 和独立 `playwright_chromiumdev_profile-` 后，仅向该浏览器发 SIGTERM，随后全量自然汇总为 719P/3F/1S，雷达用例本身通过。动作与耗时记录在 `ui-browser-cleanup.json`，没有操作用户 Chrome、没有终止其他测试、没有修改断言或结果；不能把本轮描述为完全无需介入的退出。

本次真实缓存快照：日报正常、快报逾期、兴趣通道正常、周报缺失、digest 待确认、推特雷达正常、看门狗等待中、两个微信常驻服务运行中。这是上述检查时点的文件/PID 事实；没有把兴趣报告正文、真实收藏链路或外部任务执行质量纳入本单验收。

## 变更与口径

- 新增 `external_tasks.py`：7 项固定登记（Grok 日报/快报/兴趣/周报/收藏 digest、推特雷达、看门狗）和 2 项 launchd 常驻服务。Grok 目录调用现有 `grok_bot_radar.reports_dir()`，不猜 digest 路径。
- `ScheduledTasks` 持有独立内存快照，后台调度循环每 5 分钟刷新；复用 `/api/schedules` 增加 `external` 字段，保留既有内置 `tasks`。HTTP 只复制缓存，不扫描文件、不执行 launchctl、不创建外部任务或运行记录。
- 北京时间计划，文件名完整匹配和 mtime 判定；文件是空 JSON/0 字节仍可作为产出心跳。周日周报跨周、兴趣通道跨北京时间零点均按当前计划周期计算；未来 mtime 不计入。
- 按主控 ask 回复：以最近两次计划和产出历史推导，不持久化观察起点；目录不存在/从未产出显示「缺失 · 从未产出」，有旧产出但上一周期也错过显示「缺失 · 上一周期也没产出」。上一周期有产出时，本周期先等待中，超过宽限逾期，再跨到下一周期缺失；补当前周期文件恢复正常。重建实例结果相同。
- 日志心跳使用普通文件 mtime，并确认文件非空且以完整换行结束；不返回日志正文。常驻服务只调用固定 `/bin/launchctl list`，只保留两个标签的 PID/退出码，不读取 plist，不回传完整命令输出或错误原文。
- 设置页同一张表呈现内置与外部；外部标「外部 · 只读」，显示计划、最后产出、颜色与文字状态。内置暂停/恢复、立即运行、运行记录保留；390 宽度用同表的纵向行展示，没有页面横向溢出。时间统一显式使用 Asia/Shanghai。
- `server.py` 无改动，无新增路由；`settings_view.py`、`interest_channel.py`、H4 两个文件与开工基线逐字一致。`schedules.html` 无改动。不改 Grok Bot 设置、飞书或 AIsa 采集，不提交、不推送。

## 定向与浏览器证据

- 首轮相关 Python 78/78：external_tasks、scheduled_tasks、schedules_http、schedules_management、settings_view、video_radar_schedule_h2；耗时 8.323 秒。最终新增专项 21/21（包含新增的 Grok 目录解析器复用验证），全量验证覆盖最终代码。
- `tests/e2e/external_tasks.cjs`：隔离 /tmp 工作区、随机端口、独立 Chromium profile，14/14。真实 HTTP 的总览包含内置行 + 7 个登记任务 + 2 个服务；外部无操作按钮；digest 待确认；常驻 PID/退出码；逾期颜色与文字；390 无溢出；只读加载零 POST；内置恢复/暂停/立即运行真实脚本并显示成功运行记录；兴趣清单仍可读；无页面脚本错误。
- 旧 `tests/e2e/settings_page.cjs`：23/23。经主控 `msg_34b463828355` 授权，只改原有一处总数断言：分别精确检查内置数、外部只读 9 行、外部零可写按钮、总数两者之和；完整 diff 在 `/tmp/impl-hub-h1/settings_page.cjs.diff`。没有放宽既有断言。首次复跑暴露新增运行按钮与旧暂停按钮共用 data-task 导致选择器不唯一，已将新按钮改用独立 data-run-task，保留旧测试选择器并复跑通过。
- `mutation-check.log`：只在内存把 `_latest` 故意改成始终有当前产出，状态链测试按预期失败 1/1；正式源码未改，证明等待反例能抓住假正常。
- 截图已经打开核查：`/tmp/impl-hub-h1/overdue-1440.png`、`/tmp/impl-hub-h1/overdue-390.png`。均为真实隔离 HTTP 设置页的任务总览，兴趣通道明确显示「逾期」。

## 工人弱点逐条自测

| 项目 | 结果与证据 |
|---|---|
| W1 解析取错位置 | 通过：严格 fullmatch 文件名；无效日期、前置数字、缺后缀时段、错误扩展名不计入；日期使用明确前 10 位且做日历校验；actual mtime 与计划日对应。`test_daily_exact_date_filename_only`、`test_alert_pattern_date_and_mtime_not_first_number`、跨零点/跨周反例通过。 |
| W2 mock 泄漏与真实数据写边界 | 通过：新增单测全部 /tmp TemporaryDirectory + addCleanup，patch 只用上下文管理器；E2E root/reports/tasks.db/profile 均落 /tmp。没有向真实报告目录写测试数据，没有写真实 tasks.db 的任务/运行记录；正式重启仅正常服务装载。 |
| W3 凭据外露 | 通过：缓存只返回固定登记元数据、时间、状态、固定服务 PID/退出码，不返回日志内容、文件正文、异常原文或 launchctl 全输出。`credential-rg.json` 用 rg 仅输出命中文件名；新增源与日志无密钥形态匹配。旧 settings E2E 的合成密钥形态与改前逐字相同，新增匹配为 0。 |

## 范围与验收边界

这是产出心跳，不把文件 mtime 或服务 PID 等同于内容质量、采集成功或 HTTP 健康；看门狗的健康判断由 H4 负责。按主控要求，心跳判定无状态且以 mtime/文件名为证据，因此人工 touch 可以改变文件心跳时间；本单不解析外部报告正文。收藏 digest 的产出路径仍待确认，界面保留待确认，不伪造「正常」。

真实服务验收仅 GET/HEAD 读取设置与接口，不对真实任务点立即运行、暂停、恢复，不对真实兴趣清单保存；可写路径均在隔离副本验证。没有使用 computer use，没有操作真实桌面或用户 Chrome profile。

## 证据索引

`/tmp/impl-hub-h1/`：`before/` 开工备份、`baseline-sha.json`、`changed-sha.json`、`scheduled_tasks.py.diff`、`settings_page.cjs.diff`、`directed.log`、`external-final.log`、`mutation-check.log`、`browser.log`、`browser-results.json`、`browser-final-evidence.json`、`settings-existing-e2e.log`、`credential-scan.json`、`credential-rg.json`、`live-before.json`；全量与正式装载证据见本报告相应段落。

## 文件与版本

settings 三文件开工 SHA 与一期实施报告一致。`scheduled_tasks.py` 仅增加 ExternalTasks 初始化两行及调度循环刷新一行；未触及 H2 任务逻辑。

| 文件 | 改后 SHA256 |
|---|---|
| `external_tasks.py` | `bbaf596464519344239c58fc73e82d20bdcfd2d9acd32d5d171d1532273af6e9` |
| `scheduled_tasks.py` | `bbceca9704416fb165bcbf89995cda2efe47e0c6abf07b932a4aaa932919a5c5` |
| `schedules_assistant_api.py` | `84fc57e833591af696e77c3044a2cecfd21af5e03a5c6e089e563bbf05f05879` |
| `public/settings.html` | `c8a1ed39e77c015cd4693402687423e3d96991fbf2229b44548c8b9ad499e8ee` |
| `public/settings.js` | `299c8e4c7d5294efdec4ddf8142b1bd59f561d6d120e95c0fe03eca645203028` |
| `public/settings.css` | `ba4352c45145bc46781efda973b9c03d7bbc5dbe238f5d0dc54c19316d101368` |
| `tests/test_external_tasks.py` | `03b5c1eb49bf274d3889a78983d3909acdf5ed5303a0f0ed40cd652b5839664a` |
| `tests/e2e/external_tasks.cjs` | `4c71b0b6ed40ebfc7cfba129f617c59400d9b4025d9480c45d57b8f9608dd234` |
| `tests/e2e/external_tasks_fixture.py` | `f9bedaf4873d5d1791564967c6823c9c0a0a6090bc9fbe003e68a151dae169a2` |
| `tests/e2e/settings_page.cjs` | `e1bf361542f4cd6462eff8519818322605d7ad8e85dd7479f6cb9c6002a16160` |

**改前/改后（共享调度文件）：**

- `scheduled_tasks.py`：`a80ae8333fbf05a39d31d42b0679ec3a5b2b54b02aed7e857d92af4406f1679e` → `bbceca9704416fb165bcbf89995cda2efe47e0c6abf07b932a4aaa932919a5c5`。
- `schedules_assistant_api.py`：`40ada06e080c9c7f09b5738794e7d671fedbae5b205df726062d97d0ca61bc1a` → `84fc57e833591af696e77c3044a2cecfd21af5e03a5c6e089e563bbf05f05879`。

**未改文件（收尾逐字核对开工副本）：**

- `server.py`：`728aa7b491bec880ffb3794a7149df695024afab5a43e4212cc6f28d4db7ea28`。
- `interest_channel.py`：`d24a59d1524e845e5a1c791965500a09da9c90711d4f33dac61cbfb95a49c7b4`。
- `scripts/studio_watchdog.py`：`098f0322cec6c25a29cdf447e6eddc2ff75dc96010f4869d2e6c5ae36f8c1f7c`。
- `tests/test_studio_watchdog.py`：`9ea9871c1bfef5efaf33d70eecba3919dd8f570e3b49c080dfd8856023268e65`。
- `settings_view.py`：`7a25c1081c614435143740a7f2d390bb1abf81ffa9bc297b16c729c3deccabdd`。

旧设置 E2E 唯一授权断言 diff：

```diff
--- before/tests/e2e/settings_page.cjs
+++ after/tests/e2e/settings_page.cjs
@@ -40,7 +40,7 @@
  for(let i=0;i<5;i++){const start=performance.now();await page.goto(base+'/settings.html');await ready(page);timings.push(Number(((performance.now()-start)/1000).toFixed(4)))}
  check('P8 设置页加载5次均小于1秒',timings.every(t=>t<1),JSON.stringify(timings));
  const overview=(await api('/api/settings/overview')).body,schedules=(await api('/api/schedules')).body;
- check('G1 7个真实来源及定时任务数量一致',overview.sources.length===7&&overview.schedule_task_count===schedules.tasks.length&&await page.locator('#sources .card').count()===7&&await page.locator('#tasks tr').count()===schedules.tasks.length);
+ check('G1 7个真实来源及定时任务数量一致',overview.sources.length===7&&overview.schedule_task_count===schedules.tasks.length&&await page.locator('#sources .card').count()===7&&await page.locator('#tasks [data-task-row]').count()===schedules.tasks.length&&await page.locator('#tasks [data-external-row]').count()===9&&await page.locator('#tasks [data-external-row] [data-task],#tasks [data-external-row] button').count()===0&&await page.locator('#tasks tr').count()===schedules.tasks.length+9);
  check('来源实际成功时间与夹具一致',(await page.locator('[data-source=grok]').innerText()).includes('2026')&&overview.sources.find(s=>s.id==='grok').last_success==='2026-10-29T08:00:00Z');
  assert.equal(overview.sources.find(s=>s.id==='twitter').last_success,'2026-10-29T14:00:38.594Z');
  assert.equal(overview.sources.find(s=>s.id==='aisa').last_success,'2026-10-29T14:00:38.594Z');
```
