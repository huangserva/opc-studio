# 小单 H1-L1 独立复核报告

| 决定要点 | 结果 |
|---|---|
| 结论 | **通过**；合同必查项全部完成，无阻断项 |
| H / M / L | **0 / 0 / 0** |
| 版本核对 | 六文件中四个原始 SHA 精确一致；另两个仅有已登记 H3b 增量，逆向移除后 SHA 精确恢复 H1-L1 交付值 |
| 差异边界 | scheduled_tasks.py 相对开工基线只改变 tick/external refresh 异常边界；完整 diff 见下文 |
| 定向测试 | Python **82/82**；H4 **26/26**；隔离浏览器 **21/21** |
| 故障与检错 | /tmp 当前副本 **3/3**；只恢复旧循环边界的副本 **2F/0E、1P**，符合预期 |
| 真实浏览器 | 8766、PID **36018**；1440/390 正常，13 内置/10 外部，零写请求、零 pageerror、无横向溢出 |
| 改动与证据 | 本窗口仅新增本报告；证据 `/tmp/review-small-h1-l1/`，未重启、不提交、不推送 |

## 版本与范围

合同对应 task_0203dd828eaa / ctx_3b4297ffd1b9，复核对象为 task_d1ed0df1102f。任务补充说明当前 8766 已由 B 为 H3b 重启，故以实际 PID 36018 为准，没有沿用合同旧 PID。`lsof -nP -iTCP:8766 -sTCP:LISTEN -t` 实测为 36018；页面读取到 external.last_error / last_error_at 两个新字段且为空，health.ok=true，HTTP settings.js SHA 与交付值一致。

| 文件 | 本轮实际 SHA256 | 与 H1-L1 交付比较 |
|---|---|---|
| scheduled_tasks.py | 7c18b2febf4fa2fbabfb4e35d795d541a02f0adc573c28d566ac15e46e7ae502 | 一致 |
| external_tasks.py | 87619f8f882e955fc7ca846b0c3c6dc7566807feba6bd55ce35620dbfe3ff8c2 | H3b 后续增量；归因通过 |
| public/settings.js | c8f25ca700cf40583b99066b3aa851b51a8110939b0b400395d727c05543b612 | 一致 |
| tests/test_external_refresh_isolation.py | a548d35b7fdbd4a8d20ab4ec1b0f3c52e75a58b26f276ac7855339951a514638 | 一致 |
| tests/e2e/external_tasks.cjs | f41b2baabfe82357c614cce754569647296a47f6fe573933d25b16d5e4d7e1e4 | H3b 后续增量；归因通过 |
| tests/e2e/external_tasks_fixture.py | 6d08d6db3884b4c16e7b0866919d358b3625aba5ee6d9f03aff3b093207bb5c1 | 一致 |

两个不同文件的当前 SHA 同时匹配 `coordination/evidence/impl-hub-h3b/file-sha256.json`。仅在 /tmp 去掉 H3b 已登记的晨报任务、晨报快照读取，以及浏览器登记数量 9→10/标签增量，分别精确恢复 H1-L1 原 SHA `2d0ee0ee02f8271bba870409ef46b7171b810e78572de85a11ab1aea05c17fa7`、`1b752dafa19ded3508780baecd0a3f1a0f1a2d1d06ab3a6e678c0227541556b7`。没有未归因的差异。证据 `probes.py`、`sha-attribution.json` 和两份 `*.h1-reconstructed.*`。

开工基线 `/tmp/impl-small-h1-l1/scheduled_tasks.py` SHA 为 `bbceca9704416fb165bcbf89995cda2efe47e0c6abf07b932a4aaa932919a5c5`，与实施前 `before.sha` 一致。与当前文件全部差异如下，没有其他变更：

```diff
@@ -461,9 +461,13 @@
         while not self.stop_event.is_set():
             try:
                 self.tick();self.last_tick=stamp();self.last_error=None
-                self.external_tasks.refresh()
             except Exception:
                 self.last_error='调度检查失败，请检查工作台日志与任务数据库；任务没有被标记成功'
+            else:
+                try:
+                    self.external_tasks.refresh()
+                except Exception:
+                    self.external_tasks.mark_refresh_failed()
             self.stop_event.wait(2)
```

源码复核：外部刷新以局部 rows 构建，整轮成功后在锁内替换 cache/refreshed 并清除错误；失败只写固定外部错误与带时区时间，旧 cache 不变。有错误时绕过正常 300 秒缓存，下一循环可以恢复。设置页用 textContent 显示错误时间与“保留上次快照”，异常原文没有进入 API。

## 测试与反例

Python 使用指定 `../article-importer/.venv/bin/python`，禁写字节码，不跑全量 discover。定向命令：

```sh
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python -m unittest tests.test_external_refresh_isolation tests.test_external_tasks tests.test_scheduled_tasks tests.test_schedules_http tests.test_schedules_management tests.test_settings_view tests.test_video_radar_schedule_h2 -v
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python -m unittest tests.test_studio_watchdog -v
PYTHONDONTWRITEBYTECODE=1 H1_EVIDENCE_DIR=/tmp/review-small-h1-l1/browser node tests/e2e/external_tasks.cjs
```

分别 **82 项、8.092 秒；26 项、8.488 秒；21 项**，均通过。原始日志 `directed.log`、`h4-directed.log`、`browser.log`。

| 合同反例 | 实测结论 | 证据 |
|---|---|---|
| 连续两次 external refresh 抛 RuntimeError | 通过：3 轮 tick 都执行，前两轮 health.ok=true、调度 last_error 均为空，外部 tasks/checked_at 保留旧值，独立错误及带时区时间有值，私有异常哨兵不进入 API | /tmp 当前代码副本 `current-isolation.log`，首项 |
| 故障恢复 | 通过：第三轮清除外部 last_error/last_error_at；恢复刷新绕过缓存；浏览器失败提示随后消失 | 同上及 browser/browser-results.json |
| 局部扫描后 service_reader 再抛错 | 通过：连续两轮不发布半份 rows，旧快照与健康状态保留，恢复清错 | current-isolation.log，第二项 |
| tick 自身抛异常 | 通过：health.ok=false，原固定 last_error 不变、last_tick 不更新、外部 refresh 不执行 | current-isolation.log，第三项 |
| 只把循环隔离边界改回旧写法 | 通过检错：同一份新测试前两项失败于 health.ok 断言，tick 原行为项仍通过，合计 3 项、2F/0E/1P，退出码 1 | mutant-isolation.log；只改 /tmp/review-small-h1-l1/mutant/scheduled_tasks.py |

浏览器夹具使用随机端口 54544、`/tmp/h1-e2e-uNkRa6`、独立 headless Chrome profile。真实循环由临时控制文件触发故障，覆盖 1440/390 故障提示、旧快照与调度健康、恢复，以及原内置任务暂停/恢复/立即运行和记录路径。仅该临时服务收到 3 次 POST；正式服务未注入故障、未执行任务控制。

## H4 与正式页面

H4 文件逐一与原交付/返修报告及本单开工快照核对：

| 文件 | SHA256 | 结论 |
|---|---|---|
| scripts/studio_watchdog.py | 098f0322cec6c25a29cdf447e6eddc2ff75dc96010f4869d2e6c5ae36f8c1f7c | 未变 |
| tests/test_studio_watchdog.py | 9ea9871c1bfef5efaf33d70eecba3919dd8f570e3b49c080dfd8856023268e65 | 未变；26/26 |
| scripts/launchd/run-studio-watchdog.sh | 17c1e442fc29a45a46931aa95dc5b4e2d13212d3f139c7dcccf3e5b27c993e15 | 未变 |
| scripts/launchd/com.servasyy.article-studio-watchdog.plist | fb1c0e9e177de6985eec7283d93fd5ea35f43b8141d915ab83ea6e308846679c | 未变 |
| ~/Library/LaunchAgents/com.servasyy.article-studio-watchdog.plist | cf95d559b51a465e349813c42b6919507b4c4ff5f2214fe0d8e56fc218b10a9e | 未变；只计算 SHA，没有打印内容 |

server.py 同样保持实施前 SHA `728aa7b491bec880ffb3794a7149df695024afab5a43e4212cc6f28d4db7ea28`。本轮核对的 11 个仓库文件在收尾时 SHA 全部与复核开始一致，见 `sha-before.json`、`sha-final.json`。

正式路径由独立 /tmp profile 打开 `http://127.0.0.1:8766/settings.html` 并点击“刷新状态”，路由仅允许同源 GET。1440×1000 与 390×844 均显示 13 内置、10 外部任务，总览、状态和只读标识正常，document.scrollWidth 未超过 innerWidth；零 pageerror、零写请求。已目视检查真实两宽度截图及隔离故障移动端截图。证据 `live.cjs`、`live-results.json`、`live.log`、`live-1440.png`、`live-390.png`、`browser/refresh-failed-390.png`。

本次只判断 H1-L1 合同，不把页面上的既有任务失败/外部缺失状态当成此次缺陷，也不代替 H3b 自身验收或项目全量验收。没有使用 computer use、用户 Chrome profile、受禁命令或真实桌面；未重启服务、未修改正式源码/配置/业务数据，不提交不推送。
