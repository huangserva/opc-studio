# H3b 复核合同（复核窗口 A term_02017284，实施在 B）
对象：task_805a7748c9fa，报告 coordination/impl-hub-h3b-report.md，证据 coordination/evidence/impl-hub-h3b/（file-sha256.json 为改动清单）。原合同 impl-hub-h3-contract.md「B. 微信线索晨报」段 + 主控补充（Pi 只补判断句不改计数；24h 只计 threads.created_at 新建；>50 时商单全列、其他每类最新 8 条；发言人无字段标未知）。正式任务 ce31bd13dc704cbf9e4647867721d921（kind=pi，每天 08:30），真实 run fe67fb7f730c472392979bc419ba2cbd；8766 PID 36018。
必查：
1. SHA 与清单一致；未改 scheduled_tasks.py 循环、server.py、scripts/radar_common.py、today_filter_config.py（H3a 闸门归 A 的 M2）；H1 计数断言 9→10、扫描 6→7 只是增量（看 diff，W4）。
2. 定向测试全过（报告命令）；不跑全量（实施已跑 3761 0F/1E/7S、UI 723 719P/3F/1S）。
3. 真实对账：只读 SQL 复算 workspace/clue-morning/2026-10-30.json 的 clue_count=971 与 category_counts（business20/technology403/topics141/projects73/other357），窗口按 json 的 window_start/end；markdown_sha256 与 .md 一致。
4. finalize 防篡改：/tmp 副本里让「模型」传入改过的计数或正文，finalize 应拒绝；expected-sha256 不符应拒绝；无新增时为「今日无新增」且 judgment.source=rules，不调用模型。用 /tmp 改回旧行为证明测试会 F。
5. W1：商单发言人只取明确字段，不取群名/第一处匹配；构造反例。W2：测试不写真实 workspace/clue-morning、tasks.db、clues.db。W3：grep 报告/证据/运行记录无 Key。隐私：报告与证据里无真实聊天原文、昵称、微信号（晨报文件本身是产品内容，不算）。
6. 今日页：隔离 Playwright 只读访问真实 8766，顶部状态栏晨报入口可展开全文、纯文本渲染（构造含 <script> 的合成晨报在 /tmp 副本验证不执行）；首屏 1440×900 仍 5 条；1440/390 截图存 /tmp/review-hub-h3b/。
7. 08:30 撞车：看报告对同模型排队的说明与建议，判断是否需要错开时间并写进你的报告。
约束：只读；不重启；不点正式任务立即运行（如必须先问主控）；禁止 launchctl print / ps eww / env / 打印 plist；视频实验室保护边界不动；不提交不推送。
交付：coordination/review-hub-h3b-report.md，决定要点表，worker_done。
