# 4-1 设置页返修报告

| 决定要点 | 结果 |
| --- | --- |
| 结论 | 自检通过；M1–M3 已修，待协调者独立复核 |
| H / M / L | 0H / 0M / 0L（本轮自检未发现剩余问题） |
| 改动与 SHA | 5 个许可文件，完整 SHA256 见文末；许可外 tracked 增量 0 |
| 专项 | Python 15/15（原 11 + 新增 4）；UI 33/33；隔离 Playwright 23/23 |
| 全量 Python | Ran 3560 tests in 374.744s；FAILED (failures=3, errors=86, skipped=7)；新增失败 ID 0 |
| 全量 UI | 723 项：718 通过 / 4 个批准的既有失败 / 1 跳过，和上轮独立复核一致 |
| 正式验证 | 7 来源逐项核对悬停 reason；5 次完整加载均小于 1 秒；脚本错误 0；全部 GET |
| 服务与保护 | launchctl 重启后最终 PID 72793；未输出环境变量、未使用 computer use、未采集、未提交、未推送 |

任务 `task_1dc48ac12cb1` / Dispatch `ctx_908c6178acd2`。合同 `coordination/fix-p4-4-1-contract.md`，本轮证据目录 `/tmp/fix-p4-4-1/`，最终隔离浏览器证据 `/tmp/settings-p4-e2e-iLquBx/`。

## 修复与口径

- M1：不再把 `state.runs` 的小时键当成功时间。只选择最新成功 run_id，对应读取 `twitter-radar/snapshots/YYYY-MM-DD/<run_id>.json` 的 `run.finished_at`；核对 run_id，并要求带秒和时区。快照缺失、轮次不符、无时区或只有小时槽位均返回未知，reason 解释原因，不补零、不拿旧时间顶替。
- M2：不再读取 `aisa.db meta.last_synced`，也不使用旧 AIsa collect 缓存覆盖真实雷达时间。与今日页使用同一个 `grok_bot_radar._read_json` 读取器及 bundle 路径，返回 `collected_at`。协调者通过 Orca ask 明确采用 ISO 带时区结果，和今日页 `updated_at` 按 Asia/Shanghai 解析后比较同一秒；未改 today_view 或其函数签名。
- M3：intel jobs 查询纳入 `rate_limited`，并筛选 detail/reason 明确含失败、限流、200013、超时等证据的 `partial`；最近错误同时显示记录时间、状态及原因。正常 partial（包括“失败 0 篇”及“没有失败”）保持 last_error 为空；较新的正常 partial 不遮住较旧、已确认的错误。
- 文档：两处原本已有 **2026-11-29**，本轮仅将该日期一句统一为“届时另开单决定是否撤除”，不提前承诺删除。上轮报告 0L，无其他 L 待修。
- reason：每个来源都有具体文件/表依据，Grok 增加实际报告目录，微信无任务时也说明查询的是 schedules/tasks.db；既有前端 title 已支持悬停显示，无需改 public/settings.js。

## 反例 1：推特正式快照真值

```json
{
  "overview": "2026-10-29T14:00:38.594Z",
  "run_id": "20261029-140008-f6d22d61",
  "snapshot": "workspace/twitter-radar/snapshots/2026-10-29/20261029-140008-f6d22d61.json",
  "finished_at": "2026-10-29T14:00:38.594Z",
  "snapshot_sha256": "b29efccdc734d9bad122ccc1942423298cd971dd56104827eafed7b0f0b64c9d",
  "manifest_sha256": "b29efccdc734d9bad122ccc1942423298cd971dd56104827eafed7b0f0b64c9d"
}
```

快照 SHA 与正式 bundle.manifest SHA 一致。设置页显示北京时间 `2026/10/29 22:00:38`，后端保留原始毫秒及 Z 时区。合成测试还覆盖最新失败轮次不能替代成功轮次、快照缺失、错误 run_id、无时区、小时槽位。

## 反例 2：AIsa 与今日页同一秒

```json
{
  "overview": "2026-10-29T14:00:38.594Z",
  "today_updated_at": "2026-10-29 22:00:38",
  "bundle_updated_at": "2026-10-29 22:00:38",
  "bundle_collected_at": "2026-10-29T14:00:38.594Z"
}
```

`2026-10-29T14:00:38.594Z` 转 Asia/Shanghai 是 `2026-10-29 22:00:38.594+08:00`；按秒与今日页的 `2026-10-29 22:00:38` 相等。Python 专项及隔离浏览器均执行了解析比较断言；正式只读探针也比较了解析后的 epoch 秒，不声称两种原始字符串相等。

## 反例 3：/tmp 合成 SQLite 原始数据

| status | reason / detail 原文 | last_error |
| --- | --- | --- |
| rate_limited | 命中后台限流（200013） | 2026-10-29T14:00:38Z · rate_limited · 命中后台限流（200013） |
| partial | 已保存 3 篇，剩余正文待确认 | null |
| partial | 后台：命中公众号后台限流（200013），没有取到列表 | 2026-10-29T14:00:38Z · partial · 后台：命中公众号后台限流（200013），没有取到列表 |
| partial | 正文读取失败 | 2026-10-29T14:00:38Z · partial · 正文读取失败 |
| partial | 保存 3 篇，失败 0 篇，未处理 2 篇；正文未请求 | null |
| partial | 已保存 3 篇，没有失败；余下待确认 | null |

每例只保留该一条 jobs 记录；finished_at 均为 `2026-10-29T14:00:38Z`。原始连接 URI、SQLite 前后 SHA、输入和实际输出见 `synthetic-results.json`；各 SHA 前后一致，连接均为 `mode=ro, uri=True`，collect.status / intel.status 调用次数都是 0。单测另覆盖英语 failed 及较新正常 partial 不遮旧错误。

正式 intel 的真实错误：

```text
2026-10-11T03:14:43.044017+00:00 · partial · 入库 0 篇（没有取到列表），探测读取 0 篇，正文读取 0 篇，正文 0 篇待确认。合集：本地没有该号的完整文章链接，无法发现合集；可在回收站收下一篇该号文章后重试；搜狗：已关闭；后台：命中公众号后台限流（200013），已停止本次抓取（未重试）
```

## 反例 4：来源 reason 与悬停

正式隔离浏览器逐一 hover 7 张卡片，核对 title 与接口 reason 完全一致。原始 reason 全集位于 `production-proof.json` 的 all_reasons 和 `production-browser.json` 的 rows；合成浏览器也执行同样断言并保存 `source-evidence.json`。已人工查看正式完整页面截图和 390px 隔离窄屏截图，未见遮挡或横向溢出。

## 反例 5：只读与回归

- 全部 SQLite 读取仍走 `_rows` 的 `mode=ro`；不调用连接器、不调用 collect/intel status、不恢复任务。合成 store 的连接器属性访问即抛错；原 11 项中零调用与文件 SHA 不变断言继续通过。
- 原实现副本 SHA 与开工 settings_view.py 相同；用该副本运行本轮三项修复测试，三项全部失败，分别得到小时槽位、旧缓存时间、last_error 为空。`old-negative.log` 保留原始断言；证明新测试能检出旧缺陷。
- 专项命令：`../article-importer/.venv/bin/python -m unittest tests.test_settings_view`，15/15；`node --test tests/test_workspace_nav_ui.cjs tests/test_clues_ui.cjs`，33/33；`node tests/e2e/settings_page.cjs`，23/23。最终 E2E 包含 AIsa 与实际隔离 `/api/today?source=A&range=48h` 的秒级等价断言、七来源 title 悬停和 intel 200013 原因核对。
- Python 全量以开工前一轮已独立复核的 89 个失败 ID 为比较集合（`/tmp/review-p4-4-1/python-id-diff-rerun.json`）；已向协调者登记独占 discover，未并发全量。最终代码全量结果：Ran 3560 tests in 374.744s；FAILED (failures=3, errors=86, skipped=7)，唯一失败/错误 ID 89，new_ids=[]，removed_ids=[]。既有失败未伪称通过。
- UI 全量 `npm run test:ui`：723 项，718 通过 / 4 失败 / 1 跳过；4 条是 lab-test 初始四幕、切换赛博追杀、生成单幕参数及 `tests/test_radar_links_e2e.cjs`，与批准集合一致。
- 发现零失败计数误报后，仅对本单自己的第二次 discover PID 69689 发送 SIGINT 并确认退出（130），未动他人进程；中断过程保存在 `python-interrupted-before-zero-failure-fix.log`，之后顺序执行最终版全量。
- AIsa 口径确认前的一次全量作为过程记录留在 `python-before-aisa-confirmation.log`，不作为最终代码验收结果；最终结果为 `python-full.log`。
- 正式浏览器全部 GET，未调用 collect/intel 状态；加载耗时（秒）：[0.2518, 0.3135, 0.2106, 0.3091, 0.3143]，页面脚本错误 []。

## 反例 6：服务新代码

PID 由开工 `2027` 经第一次修复重启为 `57075`，AIsa 口径调整后重启为 `64423`，补齐零失败反例后再次用合同规定的 `launchctl kickstart -k` 重启为最终 `72793`。最终正式 GET 返回 ISO AIsa collected_at 与新的 intel 错误，真实浏览器显示最终内容，证明加载了新代码；仅使用 `launchctl list | awk` 取 PID 和 `ps -o pid,lstart`，未输出服务环境变量。AIsa 口径调整重启后立即探针曾遇启动窗口的 connection refused，服务就绪后原探针重跑通过，未增加额外重启。

## 范围、回滚与证据

开工保存 tracked SHA 和 git diff 到 `baseline-hashes.json` / `baseline.diff`，最终逐文件核对，变化仅许可内 5 文件；`public/settings.js` 未改。未动协调台账或无关工人的改动，未提交、未推送。回滚仅限本轮列明的改动并重启服务，不能回退共享工作区其他在途改动。

| 文件 | SHA256 |
| --- | --- |
| `settings_view.py` | `7a25c1081c614435143740a7f2d390bb1abf81ffa9bc297b16c729c3deccabdd` |
| `tests/test_settings_view.py` | `08ec42c0f9755d95688b6020fb26d36e95996a0401bdac25f611ce1e49bdbb15` |
| `tests/e2e/settings_page.cjs` | `f4005b9f23b242cc1942aacfd9721d7393dd3d434794191268fccecc45b10ade` |
| `README.md` | `18538e122ec3b20dbdc5c99709eb2a958c35db9f5f19d27d91bb02215e061f6b` |
| `docs/product-scope.md` | `7c8f1685da0766d3f4df2c262fe1455c947f6637775b984b92e1400b713966a0` |

主要原始证据：`production-proof.json`、`production-overview.json`、`today-source-A.json`、`production-browser.json`、`production-settings.png`、`synthetic-results.json`、`old-negative.log`、`python-specific.log`、`ui-specific.log`、`e2e-final.log`、`python-full.log`、`python-id-diff.json`、`ui-full.log`、`scope-diff.json`、`final-sha256.json`。所有路径均相对于 `/tmp/fix-p4-4-1/`；合成 Playwright 的结果/截图/profile 在 `/tmp/settings-p4-e2e-iLquBx/`。
