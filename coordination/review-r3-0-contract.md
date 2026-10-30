# R3-0 复核合同（复核窗口 B term_57dd3162，实施在 A）
对象：task_e606f8d6b899，报告 coordination/impl-r3-0-report.md；诊断 coordination/diag-r3-0-report.md（你写的）。改动：插件 /Users/huangzongning/Documents/Codex/2026-10-03/b-q/work/wechat-source/wechat-assistant/wechat_source/wechat_radar_pipeline.py（SHA 6e6b58fc…，改前备份 /tmp/impl-r3-0-02017284/wechat_radar_pipeline.before.py）、同目录 pi_executor.py（a00b4e2c…）、仓库 wechat_links.py（d4007c28…）及测试（见报告表）。wechat-source PID 88663；8766 PID 36018。
黄总决定（必须符合）：链接只改读新投影 assistant.db.radar_links、只收群消息；约 2.5 万条历史 wechat-links 文档原样保留不删不改；微信文件/图片下载链接（wxapp.tc.qq.com、stodownload 等）按 host/path 规则不收；wxid/空标题回落网页标题或正文开头，否则「无标题」；私聊不做。
必查：
1. SHA 一致；插件改动只在 publish_links 去重、异常分类、validate 多段 JSON 取段、pi_executor 超时分类与 1 次退避重试（对照备份 diff）；本地超时 180 秒未改。
2. 定向测试全过；/tmp 旧副本：唯一键冲突反例、多段 JSON 反例、超时反例旧版应 F。
3. 反例：本机模型不可达→失败可诊断、游标不前进不丢数据；超时重试后仍超时→本轮 partial、该批保留不隔离；空批→成功 processed=0；坏批→跳过并记录；模型返回非 JSON→可诊断不崩。
4. 真实数据只读核对：知识库 wechat-links 现 29701 条、历史 24887 条完整行指纹与实施基线一致（抽样或全量，给方法）；新增条目无 wxapp.tc.qq.com/stodownload；mp.weixin.qq.com 文章未被误筛；无标题/回落标题计数与报告（无标题 2467、正文回落 1774、投影 573）一致；10 分钟自动 sync 后条数不减。
5. runs 只读：雷达任务 71fc0f788604414a996ef1fdd567f29f 自 10-30 05:20Z 起各次 status/error_kind 列表（不打印正文）；2fc30256 interrupted 是否确为 8766 重启时刻。
6. 隐私：报告、日志、夹具、证据无真实聊天原文、昵称、微信号、会话标识；W3 grep 无 Key。
约束：只读，不改正式文件与插件；不重启任何服务；不点立即运行；禁止 launchctl print / ps eww / env / 打印 plist；不提交不推送。
交付：coordination/review-r3-0-report.md，决定要点表（并单列「24h 见证当前进度」），worker_done。
