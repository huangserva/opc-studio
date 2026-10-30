# 合同：review-p4-4-1（Codex 复核 · Orca · 只读）

对象：4-1 实施 `task_d4368dc998ad`，报告 `coordination/impl-p4-4-1-report.md`，合同 `coordination/impl-finish-p4-contract.md` 的「通用规则」和「4-1」两节。主控在实施过程中的答复也是验收依据：overview 只读快照，不调用 collect/intel status，不触发连接器；后台服务时间只 stat 真实 StandardOut/ErrPath；logs 接口只开 3 个白名单 name；test:ui 旧导航断言允许改为新结构；`test_write_from_item` 的 L1 旧断言允许改；Python 按测试 ID 子集判定（既有 89 个失败或错误 ID 另由基线修复单处理）。
产出：只写 `coordination/review-p4-4-1-report.md` 与 `/tmp/review-p4-4-1/` 证据；开头放决定要点表（≤10 行）。

## 规则
只用隔离 Playwright，禁止 computer use。正式 8766（PID 2027）只做 GET。暂停、恢复、切换模型等写操作只在 /tmp 副本 + 随机端口做。不得输出服务环境变量：禁止 `launchctl print`、`ps eww`、`env`、`cat *.plist`，plist 只允许用 plutil 或 python 取 StandardOutPath 和 StandardErrorPath 两个键。不改业务代码，不提交不推送。每项都要独立复现，看实际内容。

## 复核项
1. **SHA 与范围**：15 个文件的 SHA 与磁盘一致；tracked 增量只落在允许范围；server.py 只增加 2 行 GET 装配；`tests/test_workspace_nav_ui.cjs`、`tests/test_write_from_item.py` 的改动逐条核对，只能是新结构或 L1 带来的必要修改，断言不能变弱。
2. **只读与零调用**：读代码确认 overview 不调用 collect、intel 的 status，不触发连接器、不恢复任务，SQLite 用 mode=ro；在 /tmp 副本上给连接器打桩计数，调用 overview 前后计数都是 0。
3. **来源数据真实性（G1）**：正式 overview 的 7 个来源里，逐个对照数据源核对 last_success / last_error 是否正确。主控抽检到 3 个疑点，请逐一判断：① 推特雷达 last_success 显示为 `2026-10-29T22`，被截断了，没有分和秒；② AIsa 显示 10-13，但今日页一直有 AIsa 来源的帖子，读的数据源可能不对（今日页的 AIsa 数据实际从哪里来？）；③ 微信雷达、微信来源两个 launchd 的最近日志停在 10-23，判断是日志路径不对，还是服务真的停了（只看文件 mtime 和 launchctl list 的状态列，不读环境）。
4. **日志接口安全**：P1 路径穿越、lines 越界返回 400；P2 在 /tmp 副本里放入凭据样例后，接口和页面都看不到原值；P3 200MB 日志 1 秒内返回，内存稳定。
5. **模型 key**：页面 DOM 和接口响应里都搜不到 key 片段（/tmp 副本里放假 key 验证）；「切换模型」走现有 select-model 接口。
6. **暂停/恢复**：在 /tmp 副本上从设置页暂停一个任务，旧 schedules.html 显示同样的状态，恢复后也一致。
7. **导航**：16 个页面导航一致，主导航正好 6 项、顺序正确，「工具」「旧版」分组和原路径都能打开；events.html 直接打开可用；clues 页和助手面板里看不到事件入口，其他功能不回退。
8. **文档**：README、product-scope 与实际页面结构一致，写明了 2026-11-29 撤除旧版，过时的说法已删除。
9. **起草模板 L1**：`prompts/draft-template.md` 每条改动都注明依据；同一句式最多 2 次、占位合并的规则写进了模板；不编造亲测经历的底线还在。
10. **回归**：独立跑专项测试（Python 11、UI 33、Playwright 23）；Python 全量按 ID 子集判定，新增为 0，附差集；test:ui 只有 4 项批准的既有失败。

H = 触发采集或连接器 / 泄露凭据 / 路径穿越 / 主路径不通；M = 数据显示错误或合同未达成；L = 文案或格式瑕疵。完成后 `orca orchestration send --type worker_done`，payload 带 reportPath。
