| 决定要点 | 独立复核结果 |
|---|---|
| 结论 | **通过，带 1 项非阻断 L 建议**；11 项均完成独立复现 |
| H / M / L | 本轮功能 **0 / 0 / 1**；L1 为模板句式重复，见第 6 项；实施报告已披露的凭据输出事件不重判、不重现 |
| 文件与 SHA | 实施 18/18 SHA256 与当前磁盘一致；禁止改动 36/36 与前后清单及 Git HEAD 均一致；明细 `/tmp/review-write-p3/sha-check.json` |
| 测试数字 | Python **83/83**；隔离浏览器 **31/31 + 56/56 + 28/28**；另有独立补验脚本与主路径实测 |
| 主路径耗时 | 实施套件独立复跑 **322 ms**；另写主路径 **162 ms**；均为点击至指定作品正文可见 |
| 正式复盘 | 文件 / API / 页面均 **1 条**；近 7 天 **0 / 0**；5 次 GET **71.2 / 20.9 / 20.2 / 21.5 / 20.6 ms** |
| 服务与外部调用 | 正式 PID **52117**，本轮未重启、仅 GET；**真实模型 0 次、真实 X 0 次** |
| 本轮改动 | 只新增本报告与 `/tmp/review-write-p3/` 证据；不改业务代码，不提交、不推送 |

对象：`task_b5e51fab7add`，本轮复核 `task_6feccec3d089 / ctx_2705643d20a7`。依据 `coordination/review-write-p3-contract.md`、实施合同及批准的底稿 v0002 / 初稿 v0003 偏差。以下结论由本轮执行、读实际正文和看截图取得，未把实施报告自述当通过依据。

全部写入测试使用 `/tmp/review-write-p3/repo` 代码副本、隔离 workspace、随机端口和 headless Playwright 独立 profile。三个既有 e2e 只改临时目录前缀；未使用 `--real`，未运行会对正式库建稿的验收脚本。正式 8766 的自写浏览器脚本拦截全部非 GET 及 `/api/x-official` 请求；未触发任何被拦截的正式写请求。服务检查只读 `launchctl list` 的 PID 和 `ps -o pid,lstart`，没有读取或输出服务环境变量。

1. **SHA 与范围：通过。** 对 18 个文件重新计算 SHA256，全部匹配；36 个保护文件除与实施前后清单相等，还逐个和 `git show HEAD:<file>` 的实际字节比较，全部相同。实施增量补丁在 `/tmp` 副本执行 `git apply --reverse --check` 成功；逐段读完，9 个既有文件均在合同放行范围，`items_store.py` 只加 `mark_used`，`today_view.py` 没改。`server.py` 的本期增量只有 4 个 API 能力的路由装配（列表和详情共用分支）及 `/publish.html` CSP 放行。当前整个共享工作区有其他任务的未提交改动，不能把整个 `git diff` 算作本期；这里只对已核验的本期补丁与 18 文件作范围结论。

   二期 e2e 差异逐条核对：`library_page.cjs` 把剪贴板全文断言改成实际底稿全文 + used + popup slug 和 `used_work_id` 相同；旧版入口数量从 3 改 5，对应两页迁入旧版；夹具改用真实 Store 以支持建稿，旧资料仍走只读夹具。`today_page.cjs` 把剪贴板的全文、URL、why、角度、提纲断言迁到真实作品正文；保留 used 落盘断言并增加 `used_work_id`；截图改拍创作台底稿。其余断言保留，没有用按钮存在代替原来的内容断言。详见原补丁及 `/tmp/review-write-p3/protected-vs-head.json`、`sha-check.json`。

2. **助手锁：通过。** 独立复跑新增浏览器套件：模型被 gate 阻塞、状态 pending、`active_job=null` 时，打开创作台真实聊天框发送，接口 202，页面出现指定聊天回答。Python 同时验证已有聊天 active 时仍可完成起草且 active 保持原值。读 `write_from_item.py` 确认只走 `request_model(... allowed_tools=())`，没有调用 `assistant.start()`，也未给 `assistant.active` 赋值。证据 `write-publish.log`、`python.log`。

3. **底稿内容：通过。** 原套件的长原文加上独立补验的 **2495 字符**原文均实际比对。独立补验直接断言原文正文块等于标题加换行加原文，保留 emoji、换行和注入标签；另逐个验证标题、来源链接、浏览2400、赞0、收藏未知、每小时100、why、写法、角度和提纲。HTML 中的 script/img 均为实体转义，创作台没有对应执行节点，`window.REVIEW_INJECTED` 未定义。默认弹窗策略下底稿可见 322 ms；另写主路径 162 ms。看过本轮底稿截图。证据 `supplement-results.json`、`path-results.json` 及隔离 `01-base.png`。

4. **AI 初稿与版本保护：通过。** 浏览器实际建底稿后通过保存接口写入「人工修改必须保留」，再放开模型：状态 conflict，人工正文仍在，`ai-draft.md` 包含新初稿和实测占位，素材库卡片及详情显示「AI 初稿已另存，没有覆盖你的修改」。模型故意失败后 failed、原因可读、v0002 保留。另写独立 HTTP/浏览器补验，把隔离 writer 时限缩至 0.08 秒，等待失败，确认错误为「AI 起草超过时限，底稿保留」；再放行晚到结果，正文仍 v0002，素材库真实可见超时原因。读代码和单测同时确认构造时限 `min(timeout, 180)`、客户端配置上限180秒；未让正式服务等待180秒。证据 `supplement.log`、`timeout.png`、隔离 `03-conflict.png`、`python.log`。

5. **重复建稿：通过。** 浏览器对同一素材再点击3次，三个窗口都复用同一 slug，作品目录仍1个；Shift 点击得到不同 slug。另写三个并发 HTTP POST，均返回 `article-20261029-40e6cd22`，作品数为1；不只依赖同一进程的函数级单测。证据 `write-publish.log`、`supplement-results.json`。

6. **风格与不编造：通过，附 L1。** 逐段读正式作品 `article-20261029-c3ef26da/versions/v0003/body-text.txt`，以及真实样本 `fd6c10c0`、`50a1f16f`、`e4a12100` 的完整正文和对应素材。另核对三个临时库实际 v0003 文件与持久证据 JSON 正文逐字相同，未只读统计摘要。四份均无禁词/emoji标号；引用分别带 `@sudoingX`、`@dotey`、`@eternityspring`；没有声称黄总已经完成亲测、性能提升或耗时，提到的具体数字属于来源转述、参数计划或待验证条件。正文加入核验步骤与对照计划，既不是原文整段复制，也不是英推逐句翻译；存在的小标题均另起一行，短段串文样本没有标题挤入正文的问题。已读模板，创作、证据、引用与长度规范均标出 AGENTS、content-profile §1/§5 或本期合同 §3 来源；JSON 格式和指令隔离属于实现协议。

   **L1：模板把第一人称收窄到几种计划句式，容易机械重复。** 最终四份正文的「我准备」实际分别 **6 / 3 / 4 / 2 次**，未复现主控所说某篇9次；正式样本还有6次相同实测占位。模板明确写「第一人称句子只用『我准备…』『我想核对…』『我还没…』『我会先…』」，与重复有直接关联。没有虚构经历，初稿可继续编辑，因此属非阻断文案瑕疵；建议后续保留事实边界，允许不以“我”开头的具体步骤，减少句式和占位的重复。本轮未改模板。证据 `style-counts.json`、`real-sample-disk.json`。

7. **来源链路：通过。** 实际读取隔离作品的 `source-item.json.item_id` 和素材 `used_work_id`，互相对应；在隔离库通过真实 `/api/record/<slug>` 手动登记，再通过真实快照导入和备注接口补数据。发布页新增1条，实际显示素材标题和来源 G 图标；点击来源链接跳到素材库该 ID，全文逐字等于原素材。正式库未登记任何发布。证据本轮隔离套件 `results.json`、`04-publish.png`。

8. **发布复盘：通过。** 自写脚本从正式 `workspace/library/*/deliveries.json` 按4种已发布 kind 独立计数，只有 `images-acceptance-20261008` 的「带图与助手合并验收 20261008」1条；接口及真实 DOM 也是1条，delivery ID 相同。页面显示浏览123，其余缺失指标包括曝光均为「未知」，不是0。按记录日期独立重算正式近7天0/0；隔离新增来源发布重算1/1，页面吻合。另在隔离快照写入 `Views='1,234'`、`Likes='bad'`、`Replies=NaN`、`Bookmarks=0`，API和实际页面分别显示1234/未知/未知/0，详情时间序列表也显示原值无效，没有崩溃。正式最后一组5次 GET 为71.2/20.9/20.2/21.5/20.6 ms，全部小于1秒；第一组也均小于1秒。同步按钮的原隔离套件拦截 `/feedback-sync` 本地返回，页面加载时0，点击后1，未转发X。看过正式与异常指标截图。证据 `supplement-results.json`、`formal-publish.png`、`invalid-metrics.png`、`write-publish.log`。

9. **安全：通过。** 缺Origin、错误Origin、缺 `X-Article-Studio` 的实际 POST 均403，前后作品数相同。正式只读 GET 和隔离回归均验证只有 today/library/publish 三页允许 `img-src https:`；index/twitter-radar/video-radar 保持原 CSP，http 图片仍不放行。输入注入的执行验证见第3项。证据 `write-publish.log`、`today.log`、`supplement-results.json`。

10. **弹窗和真实用户路径：通过。** 除28项套件外另写一次独立页面流程，不授予弹窗或剪贴板权限：今日页点击 x:8003「拿去写」，新窗口打开 `article-20261029-591ff716` 且正文可见；父页卡片先显示「AI 正在起草 · 0 秒」，放行模型后真实 DOM 自动变为「AI 初稿已保存，打开作品查看最新版本」。再打开素材库同条目，卡片显示「已拿去写」，实际 API status=used，`used_work_id` 与弹窗 slug 一致。不是只检查 API 状态。证据 `path.cjs`、`path-results.json`、`path-done.png`。

11. **回归与测试强度：通过。** 指定 Python 解释器独立执行83项全部通过；素材库31、今日56、新增28项均独立执行通过。读过新增两个 Python 文件及 e2e：验证正文内容、版本号、人工修改保留、目录数量、来源双向绑定、真实快照/备注、未知指标与零值、来源跳转和请求次数；不是只验证按钮存在。原83项过程中出现一条目录构建后台降级日志（SQLite unable to open database file），测试仍83/83；素材库真实搜索回归31/31，未观察到本期搜索回归。

   原新增套件后端只替换 `x_official.remote`，对已导入 `x_feedback.remote` 的别名覆盖不完整；本轮独立补验同时拦截这两个 remote，并阻止所有非本机 socket。实际计数 `x_remote=0 / external_socket=0`，独立主路径亦为0；浏览器 X 请求与正式非GET计数都是0。该测试盲点已由独立补验证据补齐，不据此制造功能阻断。

复现入口与证据：

- `/tmp/review-write-p3/python.log`、`library.log`、`today.log`、`write-publish.log`：指定回归结果；复制代码为 `/tmp/review-write-p3/repo`。
- `/tmp/review-write-p3/repo/coordination/impl-write-p3-screenshots/isolated/results.json`：本轮重新跑出的28项结果；此路径在临时副本内，不是正式目录的实施者原证据。
- `/tmp/review-write-p3/supplement.cjs`、`supplement-server.py`、`supplement-results.json`：独立正式只读及隔离并发、异常指标、超时补验。
- `/tmp/review-write-p3/path.cjs`、`path-results.json`：独立默认弹窗和 pending→done→素材已用的真实 DOM 路径。
- `/tmp/review-write-p3/sha-check.json`、`protected-vs-head.json`、`style-counts.json`、`real-sample-disk.json`：文件与正文验证。

补验脚本第一次使用 `x:parallel` / `x:timeout` 搭配同一数字推文URL，ItemsStore按URL归一化成 x:123，导致测试请求「素材不存在」。已保留 `supplement-fixture-error.*`，更正为不同数字ID及对应URL后全过；这是自写夹具错误，没有改业务代码或降低断言。真实模型调用预算使用0/2；本轮没有新生成真实模型样本，风格结论限定为合同指定四份现有真实成稿，不宣称未来模型输出永不编造。真实 X 同步计费和线上权限未执行。
