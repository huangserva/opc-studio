# 中枢期 H2 视频雷达收编 + L1 权限保真 复核合同

派单：主控 term_b884acc7（run_17dc13f37a10）；复核 Codex term_57dd3162（实施 term_02017284，task_f1edd322ae61）。
仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`。**禁止** launchctl print / ps eww / env / cat 或 plutil 打印任何 plist 内容（会把 AISA_API_KEY 打进输出）；只用 `plutil -extract <非密钥键> raw` 或 `plutil -lint`。

## 要读
实施合同 coordination/impl-hub-h2-contract.md（含「附加小修 L1」）；实施报告 coordination/impl-hub-h2-report.md（先核对 SHA）。

## 复核重点（逐条 通过/失败/未知 + 证据；独立构造反例）
1. 调度任务 e49a042aa2ff48379c7aa6bba6af1f6a：kind=script、启用、每日 21:20（北京时间）、超时 900 秒是否够（对照 launchd 历史耗时，见 workspace/video-radar-launchd.log 各行 elapsed_s）；任务正文只调用 scripts/launchd/run-video-radar.sh；任务库、运行记录、日志、报告里没有 Key（W3：只输出匹配计数，不打印内容）。
2. 立即运行记录 9e0d79dabfe1 与 launchd 版最近一次产出对照：产出位置、字段结构、条目数量级、api_calls、失败项；workspace/video-radar-launchd.log 末行由本次运行追加且结构一致（主控巡检依赖）。
3. 恰好一次：旧 com.servasyy.video-radar 已 bootout+disable（launchctl list 无此 label；plist 改名 .disabled-2026-10-30 存在；回滚命令可行性看文字即可，不执行）。读 scheduled_tasks.py 确认：服务重启/错过 21:20 后的补跑只补一次；21:20 当分钟服务重启不会跑两次；立即运行不会推掉或重复当天 21:20（立即运行发生在上午，今晚 21:20 应照常一次）。用隔离副本（/tmp 下的 tasks.db 副本 + 假时钟）实测至少「错过补跑一次」与「同日不重复」两个反例。
4. 脚本失败：隔离副本里让脚本路径无效 → 运行记录标失败、有错误原文、不影响其他任务。
5. L1：interest_channel.py 保存时保留原 mode（0644、0640 各测一次，/tmp 目录）；新建文件仍可保存；真实 ~/grok-bot-reports/interest-topics.md 权限/内容/mtime 未被本单改动。
6. 测试质量（W2）：tests/test_video_radar_schedule_h2.py 时钟替换用 tests.isolation_patches.module_time，守卫未被放宽（对照守卫源码）；patch 有清理；测试不写真实 tasks.db、~/grok-bot-reports、~/Library/LaunchAgents。用故意改错的 /tmp 副本证明测得出错。
7. 回归：Python 全量一次（已登记给本单；基线 3638 项 0F/1E/7S，E=既有 video_lab）。UI 全量不必跑（本单无前端改动），隔离浏览器只读抽查调度页能看到「视频雷达」任务即可。
8. 不重复执行真实视频雷达（会消耗 API）；不点真实「立即运行」。

## 规则
只读复核，不改正式代码；验证改动在 /tmp 副本做。禁止 computer use，浏览器只用隔离 Playwright。不停/不重启真实 8766，不改调度任务，不动看门狗、推特雷达 com.servasyy.radar-monitor、Grok Bot 设置、飞书。不提交不推送。

## 报告
coordination/review-hub-h2-report.md，开头「决定要点」表（≤10 行：结论 通过/不通过、H/M/L、关键证据、测试数字）。完成 worker_done。
