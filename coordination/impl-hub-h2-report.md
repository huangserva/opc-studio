# H2 与附加小修 L1 实施报告

| 决定要点 | 结果 |
|---|---|
| 结论 | **通过（实施自检）**；已迁移、已重启，待独立复核；**21:20 待今晚主控见证** |
| H / M / L | 0 / 0 / 0；未来时间点尚未发生，不冒充已验收 |
| 生产任务 | `e49a042aa2ff48379c7aa6bba6af1f6a`；每日北京时间 21:20；启用；900 秒 |
| 立即运行 | `9e0d79dabfe1468788b50fcda4148343`；succeeded / exit 0；154.377 秒；三个来源全 ok |
| 改动文件 SHA256 前 12 位 | interest_channel.py `d24a59d1524e`；tests/test_interest_channel.py `d79e6f3d0b39`；tests/test_video_radar_schedule_h2.py `81cc91f71500`；另新增本报告 |
| 测试 | Python 全量 **3638 项，0F/1E/7S，414.828 秒**（E 精确对应既有 video_lab）；定向 51/51 + 时钟修正7/7；隔离浏览器29/29 |
| 部署 | 旧 launchd bootout/disable 均 exit 0，plist 保留改名；新任务 next_run=`1793366400`（2026-10-30 21:20 +08:00） |
| 服务与保护 | PID **29588 → 54621**；健康接口 ok；真实兴趣文件内容/权限/mtime、推特雷达与 H4 看门狗 plist 未变 |
| 证据 | `/tmp/impl-hub-h2/`；正式稳定日志仍为 `workspace/video-radar-launchd.log`；不提交、不推送 |

## 实际改动与调度交接

通过 `POST /api/schedules/save` 创建「视频雷达」script 任务，初始暂停。cwd 为正式仓库，正文如下：

```bash
set -o pipefail
/bin/zsh scripts/launchd/run-video-radar.sh 2>&1 | /usr/bin/tee -a workspace/video-radar-launchd.log
```

只调用既有脚本并追加原巡检日志。脚本未改，仍从服务 plist 在进程内部读取 Key；Key 不进入任务正文、库、运行输出或报告。pipefail 保留采集进程失败状态。191 条历史结果最长耗时为 600.211 秒，新任务设 900 秒。

2026-10-30 10:19:48，从 `/tmp` 临时 Playwright profile 的工作台任务页点击「立即运行」，10:22:23 成功。产出对照后，10:23:16.995 bootout 成功；10:23:17.000 disable 成功，plist 改名；10:23:17.018 前 API resume 并核对今日 21:20 到期。交接先停旧服务，再启新调度，距晚间触发约 11 小时。

保留文件：`~/Library/LaunchAgents/com.servasyy.video-radar.plist.disabled-2026-10-30`，改名前后 SHA256、mtime 相同。`launchctl list` 已无 `com.servasyy.video-radar`；命令、返回码和时间见 `handoff.json`。没有改 scheduled_tasks.py、server.py 或 run-video-radar.sh，没有手改真实 tasks.db；新增/启用均经 API。

## 新旧产出对照

| 核对项 | 最近 launchd：10-29 21:20 | 工作台：10-30 10:19 | 判断 |
|---|---|---|---|
| 主页面 | workspace/video-radar/page.json | 相同 | 路径一致 |
| 快照、状态、账本 | snapshots/YYYY-MM-DD/HHMM.json、state.json、daily-YYYY-MM-DD.json | 相同目录和命名规则 | 一致，时间戳随执行变化 |
| schema / 顶层字段 | video-radar-page/v1；generated_at、keyword、lists、schema、sources、today | 相同 | 集合断言通过 |
| 三个列表条目字段 | 按 source 汇总实际字段集合 | 相同 | 每个 source 逐集合断言通过 |
| 小红书 / 实时 / 日榜条数 | 16 / 9 / 50 | 14 / 30 / 50 | 数量级一致，榜单随时间变化 |
| api_calls | 15（6 / 4 / 5） | 45（36 / 4 / 5） | 口径相同；本轮小红书含 6 次搜索、30 次详情复采 |
| 三个来源失败数 | 0 / 3 / 1 | 0 / 0 / 0 | 外部接口结果变化，无新增失败 |
| 实时榜 parts | 两个 video 页、low_fan、multi；三项 failed | 同四项，全 ok | 字段、调用对象一致 |
| 耗时 | 133.733 秒 | 154.377 秒 | 小于 900 秒 |
| 巡检日志 | 每轮一行 summary JSON | 同日志追加一行，末行与本次 output 的 JSON 完全相等 | 一致 |

本轮每日榜成功；原脚本 mode=auto 有「当日 daily 已成功则不重复采 daily」逻辑，今晚仍执行一轮雷达，daily 来源可能按既有语义复用当天成功结果。没有为测试额外重复运行真实采集。证据：comparison.json、page-before.json、page-after.json、manual-run.json。

## 今日 21:20 的边界

已观察：旧 label 已卸载，disable 返回成功，plist 已退出 `.plist` 文件名；新任务唯一、启用、下次为今日 21:20。服务重启后仅保留上午 manual succeeded 历史，没有重跑，next_run 仍是今日 21:20。

**待今晚见证**：截至交付时 21:20 尚未发生，不能声称真实晚间已恰好执行一次。主控通过本 Dispatch 的 ask 明确答复「可重启，晚间主控见证」，承接晚间巡检。届时按 task_id 查 runs，确认 trigger=scheduled、当地 started_at 为今日 21:20 后的记录恰好一条，再核对原日志新增 summary 和旧 label 仍不存在；上午合同要求的 manual 不计为晚间重复。

补跑已在 `/tmp` 调度库实测：持久化旧 due，重建调度器，模拟今日 21:25，连续三个 tick 只生成一个 scheduled succeeded；next_run 为明日 21:20，再次重建后也不重跑。没有篡改生产 due 制造补跑。

## 附加小修 L1

仅增加两行，目标存在时在临时文件 fd 上沿用原权限，新建保持原行为：

```diff
             with os.fdopen(fd, 'wb') as stream:
+                if revision != 'missing':
+                    os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
                 stream.write(output.encode('utf-8'))
```

新增 2 个 unittest 方法：0644 / 0640 两组 subTest 验证保存后权限不变；不存在时验证可保存且为 0600。interest 模块 **28/28**，H2 调度 **3/3**，既有调度 **20/20**，合计 **51/51**。在 `/tmp` 加载改前模块跑新权限测试，两个断言都能检出旧实现收紧为 0600，负对照符合预期。

隔离兴趣浏览器流程使用随机端口和 `/tmp` 数据，先设副本 0640，通过真实设置页添加、保存、删除，验证权限不变、版本冲突及原格式恢复等，**23 项通过**。真实 `~/grok-bot-reports/interest-topics.md` 前后 SHA256、0600 权限、mtime_ns 完全相同。

主控确认重启窗口后，我执行一次 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`，PID 29588 → **54621**。重启后健康接口 ok、last_error=null、running_count=0；新进程加载当前源文件。另用全新 `/tmp` Playwright profile 对真实 8766 仅 GET/HEAD 抽查设置读取和调度状态，**6 项通过**。没有让只读复核窗口执行重启，没有动 H4 看门狗。

## 必测反例与已知弱点

| 项目 | 自测结果 |
|---|---|
| W1 解析位置 | 通过；API 按精确任务/运行 id；字段按 source 逐集合；浏览器定位 data-task-id，未取第一条冒充目标 |
| W2 mock / 文件污染 | 通过；TemporaryDirectory(/tmp)、patch 上下文与 addCleanup；模拟 plist、脚本、库均在 /tmp；真实兴趣文件和保护 plist 哈希/mtime 不变 |
| W3 凭证 | 通过；grep 模式仅经 stdin，静默匹配，不在 argv 或输出回显；扫描真实库、巡检日志、运行输出及证据/报告均无 Key；credential-scan.json |
| 错过 21:20 | 通过；重建调度器、连续 tick、再重建只补跑一次，next_run 为次日 21:20 |
| 脚本失败 | 通过；隔离副本 Python 路径无效，failed / 127，保留原始 no such file or directory 与路径；tee 日志一致；其他任务正常 |
| pipefail 负对照 | 通过；临时去掉 pipefail，测试确实抓到 succeeded 掩盖脚本失败；正式任务未改变 |
| 全量边界 | 主控确认 H4 全量结束后，本单登记并获准独占重跑 Python 全量；未重跑 UI 全量 |

## 全量复核触发的测试修正

H4 复核并行全量发现本单新增测试两处对共享 time.time 的 patch，触发现有静态守卫，造成 1F。已改为仓库 tests.isolation_patches.module_time，仅替换 scheduled_tasks 模块自己的 time 绑定，未修改或放宽守卫；修正后 H2 三项与守卫四项合计 **7/7**。主控确认全量槽空闲并授权后，重跑 `../article-importer/.venv/bin/python -m unittest discover -s tests`，**3638 项，0 failures、1 error、7 skipped，414.828 秒**，退出码 1 仅由既有 error 导致。唯一 error 是 `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，栈进入 generation/native_h3.py，原文 `ValueError: 参考素材宽高须为 32 的倍数`；已逐项对照 `/tmp/article-studio-h4-review/python-full.log`，测试名称和错误原文一致，属于合同保护的既有问题，没有新增失败。证据 `full-python.log`、`full-python-summary.json`。此修正仅影响测试，不需要再次重启生产服务。

## 回滚

先暂停新任务并确认该任务没有 running；若正在运行，等待本轮结束再恢复旧调度。旧 plist 的 RunAtLoad=false，bootstrap 不会立即补跑。当日 21:20 已跑过时，不手动 kickstart 或点击立即运行，以免重复。

```bash
/usr/bin/curl --fail --silent --show-error -X POST \
  -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:8766' \
  -H 'X-Article-Studio: 1' -d '{}' \
  'http://127.0.0.1:8766/api/schedules/e49a042aa2ff48379c7aa6bba6af1f6a/pause'
# 核对 /api/schedules/runs?task_id=e49a042aa2ff48379c7aa6bba6af1f6a 没有 running 后：
mv "$HOME/Library/LaunchAgents/com.servasyy.video-radar.plist.disabled-2026-10-30" \
   "$HOME/Library/LaunchAgents/com.servasyy.video-radar.plist"
launchctl enable gui/$(id -u)/com.servasyy.video-radar
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.servasyy.video-radar.plist"
```

保留新任务历史便于审计，回滚不删除产出。L1 若独立回退，只撤销以上两行，不覆盖整个文件，经主控协调重启后验收。

## 证据索引

根目录 `/tmp/impl-hub-h2/`：

- task-create.json、manual-start.json、manual-run.json：生产 API 和真实 UI 立即运行。
- comparison.json、launchd-before.json、page-before.json、page-after.json：新旧对照。
- handoff.json、launchd-before-state.json、post-restart.json、protected-state.json：交接、重启、保护对象。
- targeted-tests.log（31）、scheduler-regression.log（20）、clock-fix-tests.log（7）、full-python.log / full-python-summary.json（3638，0F/1E/7S）、l1-negative-control.log、h2-negative-control.log。
- l1-interest-browser.cjs、l1-browser.log、interest-ui/fixture-evidence.json（23）；post-restart-browser.cjs、post-restart-browser.json（6）。
- run-real-browser.cjs 使用 `launchPersistentContext(fs.mkdtempSync('/tmp/h2-browser-profile-'), {headless:true, channel:'chrome'})`。另外两个流程各用 `/tmp` 新 profile；兴趣写入验收在随机端口，重启后真实 8766 仅 GET/HEAD；没有 computer use 或用户真实 Chrome/profile。
- task-paused.png、task-running.png、post-restart-schedules.png、post-restart-interest-settings.png；兴趣截图在 interest-ui/。
- interest_channel.py.diff、test_interest_channel.py.diff、source-sha256.json、credential-scan.json。

完整源文件 SHA256：

| 文件 | SHA256 |
|---|---|
| interest_channel.py | d24a59d1524e845e5a1c791965500a09da9c90711d4f33dac61cbfb95a49c7b4 |
| tests/test_interest_channel.py | d79e6f3d0b393cb15542565ccbd130d60f5fe64bfb5ec6daf956496efe4a34d2 |
| tests/test_video_radar_schedule_h2.py | 81cc91f7150055d2775f128fe78c2e262c1a0d3d1c89343e00da42f3b24bef67 |
