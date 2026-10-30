# 中枢期 H2：视频雷达收编进工作台调度（实施合同，H4 收口后做）

## 现状
- launchd `com.servasyy.video-radar`：每天 21:20 跑 `scripts/launchd/run-video-radar.sh`，脚本用 `plutil -extract EnvironmentVariables.AISA_API_KEY raw` 从服务 plist 读 Key 再 `exec python scripts/video_radar.py --root <repo> --mode auto`；主控巡检看 `workspace/video-radar-launchd.log` 末行判断抖音实时榜是否失败。
- 工作台内置调度（scheduled_tasks.py）支持 kind=script，每天定点、立即运行、运行记录、超时。

## 要做
1. 在工作台内置调度新增任务「视频雷达」（kind=script，每天 21:20，超时按 launchd 历史耗时留足余量），任务正文只调用 `scripts/launchd/run-video-radar.sh`，**Key 继续由脚本从服务 plist 读取，任务库、运行记录、日志里不得出现 Key**。通过现有 /api/schedules 接口创建（不手改 tasks.db），并在报告写明任务 id。
2. 保持 `workspace/video-radar-launchd.log` 每轮仍追加一行结果 JSON（主控巡检依赖它；可在任务正文里 tee/追加，或在报告给出新的稳定位置并说明）。
3. 先在工作台「立即运行」一次，成功后与 launchd 版最近一次产出对照：产出文件位置、字段结构、条目数量级、api_calls、失败项是否一致（数据内容随时间变化不要求相同）。对照表写进报告。
4. 对照一致后卸载 launchd：`launchctl bootout gui/$(id -u)/com.servasyy.video-radar` 与 `launchctl disable gui/$(id -u)/com.servasyy.video-radar`；plist 文件不删，改名为 `.plist.disabled-<日期>` 以便回滚，回滚命令写进报告。**注意 21:20 不能出现两边各跑一次或两边都不跑**：卸载与启用内置任务的先后要保证当天恰好一次。
5. 不收编推特雷达（com.servasyy.radar-monitor 一律不动）。

## 允许改动
通过 API 新增调度任务；scripts/launchd/run-video-radar.sh（仅为日志连续性做最小改动，如必要）；~/Library/LaunchAgents/com.servasyy.video-radar.plist 改名停用；新增 tests（如需）。不改 scheduled_tasks.py 以外的代码；如必须改 scheduled_tasks.py，先问主控（H4/H1 也会动它）。不改 server.py、settings、today 相关文件。

## 必测反例
- 运行记录与日志 grep 不到 Key（W3）。
- 服务重启后错过 21:20 的补跑行为：确认只补跑一次，不重复。
- 脚本失败（如临时让 python 路径无效的副本任务，在隔离副本里测）时运行记录标失败并有错误原文，不影响其他任务。

## 交付
coordination/impl-hub-h2-report.md：任务 id、立即运行记录 id、前后产出对照表、launchd 卸载证据（`launchctl list | grep video-radar` 无输出）、回滚命令。
## 附加小修 L1（与 H2 一起交付，来自 1 期复核 coordination/review-interest-p1-report.md）
- 问题：`interest_channel.py` 约 100–108 行保存 interest-topics.md 时用 tempfile.mkstemp（0600）写完直接 os.replace，没沿用原文件权限，0644 会被收紧成 0600。
- 要做：目标文件已存在时，在临时文件 fd 上 `os.fchmod` 成原文件的 `stat.S_IMODE` 再 replace；原文件不存在时保持现状。只改这一处，不动其他逻辑。
- 允许改动：interest_channel.py（仅此处）；对应测试文件里新增用例（在 /tmp 或临时目录构造 0644 / 0640 原文件，保存后权限不变；新建文件仍可保存）。测试不得写真实 ~/grok-bot-reports（W2）。
- 不要改真实 ~/grok-bot-reports/interest-topics.md 的权限（现为 0600，同用户可读，留给黄总定）。
- 报告里单列 L1 的 diff、测试数字。
## 通用
- 派单：主控 term_b884acc7（run_17dc13f37a10）；实施 Codex term_02017284；复核 Codex term_57dd3162（不同窗口）。
- 仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。8766 重启 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`；查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`。**禁止** launchctl print / ps eww / env / cat 或 plutil 打印任何 plist 内容（会把 AISA_API_KEY 打进输出）；需要确认 plist 结构时只用 `plutil -extract <键> raw` 取**非密钥**键，或 `plutil -lint`。
- 依据：coordination/twitter-intel-plan.md v0.4 第 3.7 节与第 4 节「中枢期」；coordination/twitter-intel-handoff-r2.md B 段。
- 约束：不动 Grok Bot 设置；不碰飞书；不停推特 AIsa 采集（com.servasyy.radar-monitor 不动）；WorkBuddy（~/.workbuddy-ai/）只读不写，本单不需要读；视频实验室保护边界（public/lab-test.*、generation/*、server.py 视频/generation 路由、tests/test_*lab*）不动。
- Codex 已知弱点（逐条自测并在报告给结果）：W1 解析取错位置（T5 教训：正则取第一处）；W2 测试 mock 泄漏（patch 一律 addCleanup/上下文管理器，测试不写真实 ~/grok-bot-reports、~/Library/LaunchAgents、真实 tasks.db）；W3 凭证外露（日志、报告、任务库、运行记录里不得出现 Key；用 grep 自证）。
- 全量 Python 与 UI 跑前向主控登记。基线：Python 以 1 期交付时数字为准（约 3612 项 0F/1E/7S，E=受保护 video_lab），UI 723 项 719P/3F/1S（3F=受保护 lab-test），不得新增失败。
- 浏览器只用隔离 Playwright（独立 profile；随机端口或对 8766 只读访问），禁止 computer use，不操作黄总真实桌面或 Chrome。不提交、不推送。
- 报告开头「决定要点」表（≤10 行：结论、H/M/L 自查、改动文件与 SHA256、测试数字、新 PID、证据路径），完成后 worker_done。
