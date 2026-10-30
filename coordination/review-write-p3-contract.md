# 合同：review-write-p3（Codex 复核 · Orca · 只读）

对象：3 期「拿去写 + 发布复盘」实施 `task_b5e51fab7add`，实施报告 `coordination/impl-write-p3-report.md`，实施合同 `coordination/impl-write-p3-contract.md`（第 0.1 节与反例 1–14）。主控批准的偏差：底稿 v0002、AI 初稿 v0003（Store.create 自带空白 v0001），AI 保存时 base_version 取底稿版本号。
产出：只写 `coordination/review-write-p3-report.md` 与 `/tmp/review-write-p3/` 证据；报告开头放决定要点表（≤10 行）。

## 规则
- 只用隔离 Playwright，禁止 computer use。正式 8766（PID 52117）只做 GET；建稿、起草、冲突、同步之类的写操作只在 /tmp 副本 + 随机端口做。
- 不调用真实 X 接口（拦截证明调用次数为 0）。真实模型最多调用 2 次，用于复现风格检查，报告写明调用次数。
- **不得输出服务环境变量**：确认 PID 只用 `launchctl list | awk '/com.servasyy.article-studio$/{print $1}'` 或 `ps -o pid,lstart -p <pid>`，禁止 `launchctl print`、`ps eww`、`env`、`cat *.plist`。
- 不改业务代码，不提交不推送。每项都要自己复现，不采信实施报告的自述；看实际内容，不只看状态码或耗时。

## 复核项
1. **SHA 与范围**：18 个文件的 SHA 与磁盘一致；tracked 增量只落在合同允许范围内；「不改」清单里的文件 SHA 不变；server.py 只增加 4 条路由装配和 CSP 写法。2 期的 e2e（library_page、today_page）改动要逐条说明，只能是流程替换导致的必要修改，断言不能变弱。
2. **助手锁**：起草进行中，助手聊天仍能正常开始，`assistant.active` 没有被占用。读代码确认没有调用 `assistant.start`。
3. **底稿内容**：在 /tmp 副本上用一条 2400 字带 emoji 的素材建稿，底稿里原文逐字一致；标题、链接、数据、为什么爆、写法、角度、提纲都在；`<script>`、`<img onerror>` 被转义，创作台打开不执行；点击到底稿可见 ≤3 秒。
4. **AI 初稿**：冲突时（底稿生成后手动保存一次）不覆盖，生成 ai-draft.md，状态为 conflict，页面有提示；模型失败或超时时状态为 failed、原因可读、底稿还在；180 秒上限有效。
5. **重复建稿**：连点 3 次只建 1 个作品，Shift 点击才新建；并发 3 个请求也只建 1 个。
6. **风格与不编造**：读正式库验收作品 `article-20261029-c3ef26da` 的 v0003（`workspace/library/<slug>/versions/v0003/body-text.txt`）和实施报告里另外 3 个真实样本：没有禁用词；没有无依据的亲测经历或数字；引用原作者带 @handle；不是原文翻译照搬；小标题单独成行。核对 `prompts/draft-template.md` 每条规则都注明了出处。主控抽检时发现「我准备…」在一篇里出现 9 次，判断是否属于模板问题，记 L 或不记，并写明理由。
7. **来源链路**：`source-item.json` 与 `items.used_work_id` 双向一致；在 /tmp 副本上手动登记一条发布，发布复盘页能显示来源素材，点开能跳到素材库对应条目。
8. **发布复盘页**：真实库已发布 1 条，接口和页面都是 1 条，要自己数；没有曝光数据的显示「未知」而不是 0；NaN 或字符串指标不报错；近 7 天的两个统计数字正确；「同步 X 数据」只在点击时调用（拦截证明）；`GET /api/publish/review` 5 次都 <1 秒。
9. **安全**：新 POST 接口缺 Origin、Origin 错误、缺 `X-Article-Studio` 时都返回 403，且不建作品；CSP 只在需要的页面带 `https:`，其他页面不变。
10. **弹窗与真实路径**：隔离浏览器按默认弹窗策略，从今日页点「拿去写」→ 新窗口打开作品 → 状态从 pending 变为 done → 素材库里同一条显示已用。
11. **回归与测试强度**：独立跑 Python 83 项、浏览器测试（素材库 31、今日 56、新增 28）；检查新测试断言的是具体内容，不只是按钮存在。

H = 覆盖人工修改 / 占助手锁 / 注入执行 / 产生 X 调用 / 用户主路径不通；M = 合同要求未达成但有绕路；L = 体验或文案瑕疵。完成后 `orca orchestration send --type worker_done`，payload 带 reportPath。
