# T5 复核合同（今日页：火爆标记、右上角来源图标、linux.do 来源）

派单：主控 term_b884acc7（run_17dc13f37a10）；复核：Codex term_57dd3162（实施是 term_02017284，不同窗口）。
仓库 /Users/huangzongning/development/article-studio，Python ../article-importer/.venv/bin/python。查 8766 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`，禁止 launchctl print / ps eww / env / cat plist。

## 要读
- 实施合同 coordination/impl-t5-contract.md（含 G1–G6 弱点反例），主控追加要求：失败 10 分钟退避、成功 1 小时节流。
- 实施报告 coordination/impl-t5-report.md（报告 SHA256 5c20891d…b569，末尾 12 份文件 SHA256）。先核对 SHA 与工作区一致。

## 复核重点（逐条给 通过/失败/未知 与证据）
1. 火爆：后端唯一阈值 HOT_VIEWS=100000，`hot` 下发；边界 99999/100000/None/字符串数字/视频 plays；筛选与时间、来源、方向、搜索组合；前端没有自算阈值。
2. 来源图标：卡片右上角与阅读器标题区，G/A/V/L 本地内联 SVG，无外部资源请求（用 Playwright 记录网络请求证明），title/aria-label 中文全名，多来源并排，390px 不遮挡标题和动作栏。素材库共享组件未回归。
3. linux.do 抓取安全（重点，独立构造反例，不只跑工人的测试）：curl 子进程参数是否真的防命令注入（URL/代理来源是否可被外部输入影响）、-q 禁 curlrc、仅 HTTPS、不跟随重定向、不带 Cookie；带用户名密码的代理被拒；超大响应/未知长度/403 挑战页/非法 XML/超时；description 含 script、on* 属性、javascript: 链接时页面不执行、无可点 javascript: 链接；日志、快照、status.json 无凭证。
4. 节流与并发：失败 600 秒退避、成功 3600 秒；flock + pending 去重在并发请求下只发一次抓取；.tmp 原子替换；抓取失败旧快照仍可读、来源状态显示异常。
5. 首屏：抓取慢（≥8 秒延迟）时今日页首屏 ≤1 秒；后台刷新中前端轮询在刷新结束后确实停止（无无限轮询）。
6. 测试质量：查测试 diff，确认没有放宽既有断言；工人自述「ItemsStore 旧用例手动调用 TodayTests.setUp/tearDown 导致 mock 泄漏」的修复是否正确、不掩盖问题。
7. 存/拿去写：linux.do 条目「存」后素材库能搜到，「拿去写」带全文；canonical URL ID 与素材库一致，不重复入库。
8. 主控观察：现在（北京时间早上）「今天」范围只显示 2 条 linux.do，48 小时为 30 条（日榜多为昨天发的帖）。判断这是否符合预期，或是否应让 linux.do 日榜在「今天」里按抓取时间显示——只给结论和建议，不改代码。

## 规则
- 只读复核，不改正式工作区代码；需要改动验证时在 /tmp 独立副本做。
- 浏览器只用隔离 Playwright（独立 profile、随机端口或对 8766 只读访问），禁止 computer use，不操作黄总真实桌面或 Chrome。
- 跑全量前先向主控登记。不提交不推送。视频实验室保护边界不动。

## 报告
coordination/review-t5-report.md，开头「决定要点」表（≤10 行：结论 通过/不通过、H/M/L 数、关键证据、测试数字），然后逐条结果。完成 worker_done。
