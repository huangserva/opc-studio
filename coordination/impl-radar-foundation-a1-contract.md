# 实施合同 A1：推特雷达后端地基修复 + 每轮快照

- 派给：Pi（term_3b90b5c2）
- 先读：coordination/decision-viral-monitor.md（策略与阈值）、coordination/review-twitter-radar-baseline-report.md（复核报告，本单按其「必须修」逐条落实）
- 仓库：/Users/huangzongning/development/article-studio（分支 droid/updates-2026-10-10，HEAD 89277b7；雷达脚本为未跟踪文件，以工作区当前内容为基线）
- 后续：A2（页面：刷新回执、链接/handle 注入、改读 JSON）另派；B（每小时定时、判定规则、飞书、线索中心）在 A1+A2 通过后派。本单**不做**定时任务、不做推送、不改页面。

## 目标
让「一轮采集」的结果可信、可留档、可做小时差值：失败就是失败，旧值不冒充新值，每条帖子有自己的采样时间，51 个特关博主一个不漏。

## 必做（对应复核编号）
1. **H1 采样状态与原子发布**
   - 区分：HTTP 错误 / HTTP 200 但业务错误（如 `{"code":401,...}`）/ 空成功（真的没帖）/ 超时 / 解析失败。每号记录状态、页数、调用次数。
   - 每轮生成 run 记录：run_id、开始/结束（UTC）、attempted/success/failed、实际调用数、schema_version。
   - 覆盖率 <95% 的轮次标为「不完整」，不得作为新快照发布为成功；失败账号的旧数据如需展示必须显式标旧（带它自己的旧 fetched_at），不得换上新时间。
   - 原始快照完整写盘后再发布派生 bundle：单实例锁 + 临时文件 + `os.replace`；用一个 manifest（含 run_id 与 bundle 文件名/SHA）作为唯一切换点。
   - 整轮设绝对截止时间（例如 10 分钟），子进程也设 timeout。
2. **快照存档**：每轮原始采样写 `workspace/twitter-radar/snapshots/YYYY-MM-DD/<run_id>.json`（workspace/ 已被 git 忽略），保留 30 天，超期自动清理（只删本目录下符合命名规则的文件）。逐帖字段：字符串 tweet_id、author 规范化 handle 与 user_id（若有）、类型（原创/引用/转推/回复）及被引用帖 ID、published_at（UTC，未舍入）、views/likes/retweets/replies（缺失记为 null，**不得默认 0**）、fetched_at（该账号这次成功响应的时间）。
3. **M7 唯一名单**：新建受 git 跟踪的 `scripts/radar_targets.json`，内容为雷达页当前 51 个 vip_bloggers 的 handle（从 public/twitter-radar.js 的 DATA_BUNDLE.vip_bloggers 提取一次即可）。采集只读这份名单；覆盖率分母固定为 51；不得按爆款动态追加。复核列出的 8 个漏扫账号必须在内：gosailglobal、hitw93、jinchenma_ai、lianyanshe、lifesinger、ooeli_eth、shitunote、xiaomovps（以名单文件实际大小写为准）。
4. **M2 粉丝数**：账号级 followers 取本轮该号帖子里的实时 `author.followers`（同号多条取最新），记录来源与 followers_fetched_at；拿不到则为「未知」，未知不得进入低粉榜；**删除对 servasyy_ai 的硬编码下限**，改用通用规则后黄总账号仍须正确（约 4.1 万粉，不进低粉）。
5. **M6 去重**：采样、快照、bundle 一律按原始 tweet_id 一对一；正文前缀相似去重只能作为话题聚合的显示层，不得删帖或合并不同 ID 的指标。
6. **M3 旧脚本**：`batch_scan_150_followings.py`、`deep_scan_batch_2.py` 停用——运行时打印「已停用，请用 scripts/refresh_twitter_radar.py」并以非 0 退出，不再读写任何数据。唯一入口为 `scripts/refresh_twitter_radar.py`。转推不得冒充查询账号的原创。
7. **翻页**：每号首页；若第 20 条仍在 24 小时内，再翻 1 页（用接口返回的 cursor/has_next_page），最多 2 页。
8. **M8 话题摘要**：删除 build 脚本里硬编码的 10 个话题标题/摘要/数字。话题摘要只能由当前证据生成（列支持帖 ID、生成时间）；无证据时不输出带数字的摘要。
9. **L1 时间窗**：判窗用未舍入的 published_at 与本轮统一时间基准；48h 窗口就是 48h（不是 47.9h）；未知或未来时间不算新帖；舍入只用于展示。
10. **派生 bundle**：继续输出 `public/twitter-radar-bundle.json`（字段兼容现有页面：tweets/vip_tweets/vip_bloggers/topic_clusters/updated_at/today_virals/h48_virals），新增 run_id、coverage、每帖 fetched_at/is_stale。**本单不再改写 public/twitter-radar.js**（停止重写内嵌 DATA_BUNDLE；页面改读 JSON 在 A2 做）。零值如实输出。

## 允许改动的文件（唯一归属）
- scripts/refresh_twitter_radar.py、scripts/sync_all_radar_live.py、scripts/build_fresh_radar_bundle.py、scripts/batch_scan_150_followings.py、scripts/deep_scan_batch_2.py
- 新建：scripts/radar_*.py（公共模块）、scripts/radar_targets.json、tests/test_twitter_radar_*.py
- 禁止：public/*（含 twitter-radar.html/js）、server.py、generation/*、视频实验室相关文件、任何其他文件。

## 该工人已知弱点（worker-dispatch-profiles）与必须逐条自测的反例
Pi 历史弱点：输入边界漏网、**并发/清理/回滚自证不足**（trash-timeout 6 次 5 次不通过、merge-script 4 连败）、自评失真 60%。下列反例每条都要写成离线单元测试（mock AIsa 响应，不联网），报告里逐条给「测试名 + 结果」：
1. HTTP 200 + `{"code":401,"message":"unauthorized"}` → 该号记为业务错误，不计成功。
2. 51 号中 1 号超时 → run 记录 failed=1；该号旧帖保留旧 fetched_at 并标 is_stale，不换新时间。
3. 51 号中 4 号失败（覆盖率 <95%）→ 本轮标不完整，manifest 不切换到本轮。
4. 发布中途抛异常（模拟写到一半）→ 旧 manifest/bundle 完好可读，无截断 JSON，无残留临时文件。
5. 两个刷新进程并发 → 第二个拿不到锁直接退出，不串档。
6. 关注缓存 1,000 粉、帖内 `author.followers=40,000` → 用 40,000，不进低粉；粉丝未知 → 不进低粉；servasyy_ai 无硬编码时仍 ≈4.1 万不进低粉。
7. 同一作者两条不同 tweet_id、前 40 字相同 → 两条都保留，指标不串。
8. `RT @original: ...` → 标为转推，不作为查询账号原创进榜。
9. 发布 47.95h 前的帖 → 仍在 48h 窗口；24.04h → 不算今日；未来时间 → 不算新帖。
10. views 字段缺失 → 记 null，不当 0 参与统计。
11. 第 20 条仍在 24h 内 → 翻第 2 页且最多 2 页；调用数正确计入。
12. 快照清理：31 天前的快照被删，30 天内的保留，目录外/不符命名的文件不动。
13. 旧脚本运行 → 非 0 退出，不写任何文件。
14. 全部空数据 → 不产出带具体曝光数字的话题摘要。
15. 凭证：交付物、日志、快照、报告中 `rg -n 'sk-aisa'` 无结果。

## 工作方式与验证
- 只在 /tmp 独立副本（如 `/tmp/radar-a1-<随机>/`，复制整个仓库工作区）里改；**不改正式目录**，不重启服务。
- 取 Key（仅真实冒烟用）：`AISA_API_KEY="$(plutil -extract EnvironmentVariables.AISA_API_KEY raw -o - ~/Library/LaunchAgents/com.servasyy.article-studio.plist)" ../article-importer/.venv/bin/python scripts/refresh_twitter_radar.py`；不得把 Key 写进命令行明文、脚本或输出。
- 真实冒烟：在副本里跑 1 轮真实采集（约 55 次 AIsa 调用），快照与 bundle 写在副本内；报告给覆盖率、调用数、run_id，并抽 3 条帖子与 x.com 原帖对照浏览量量级（只读，用隔离 Playwright 或接口数据，不用 computer use）。
- 测试：Python 用 `../article-importer/.venv/bin/python`；新增测试全绿；定向跑 tests/test_twitter_radar_*；全量 discover 前先发 Orca 消息给协调者登记排队，得到答复再跑（只在副本里跑，排除视频实验室在途改动造成的失败需在报告里归因）。
- 交付物（放 /tmp/radar-a1-deliver/）：
  1. 每个改动/新建文件的完整替代版本；
  2. 相对正式工作区当前内容的统一补丁 `radar-a1.patch`（新文件也包含在内）及其 SHA256；
  3. 在正式工作区的新 /tmp 副本上 `git apply --check` 通过、应用后编译（`python -m py_compile`）通过、定向测试通过、`git apply -R --check` 通过的记录。

## 交付
- 报告：coordination/impl-radar-foundation-a1-report.md，开头「决定要点」表（≤10 行）：结论、15 条反例自测逐条结果（可附表）、改动文件与 SHA256、测试数字、真实冒烟覆盖率与调用数。
- 用 Orca 发 worker_done。未逐条自测的交付直接退回。
