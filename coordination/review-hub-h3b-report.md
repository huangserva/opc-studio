# H3b 微信线索晨报独立复核报告

| 决定要点 | 结果 |
|---|---|
| 结论 | **通过**；第4、6条按主控本次明确口径验收，无本单阻断项 |
| H / M / L | **0 / 0 / 0**；两项既有/改进观察不计缺陷 |
| 文件与 SHA | 交付清单 **11/11** 精确一致；本窗口只新增本报告，未改实现或测试 |
| 定向与反例 | Python **148/148**；独立探针 **6/6**；CLI拒绝 **4/4**；故障副本分别 **3F、1F**，符合检错预期 |
| 浏览器 | 真实8766与合成独立检查 **20项通过、1项真实首屏观察**；原隔离今日页回归 **56/56**；原夹具首屏5条 |
| 真实对账 | **971**；商单20 / 新技术403 / 热点141 / 项目进展73 / 其他357；ID集合、正文计数与SHA一致 |
| 运行与服务 | 正式Pi任务每天08:30启用；指定run成功、判断来源Pi；只读确认PID **36018**，本窗口未重启、未点立即运行 |
| 隐私与范围 | 37份文本报告/证据及正式task/run凭据模式0命中，真实线索标题/摘要/原文精确匹配0；受保护文件版本保持一致 |
| 证据 | `/tmp/review-hub-h3b/`；交付SHA见 [实施清单](evidence/impl-hub-h3b/file-sha256.json) |

复核任务 `task_e3e1f9f531c3` / Dispatch `ctx_b19bd5abe03a`，对象 `task_805a7748c9fa`。依据 [复核合同](review-hub-h3b-contract.md)、[原合同B段](impl-hub-h3-contract.md) 与主控对两处验收口径的回复。本窗口没有重启服务、修改正式任务或产品数据，没有提交或推送；浏览器均为 `/tmp` 独立headless profile，真实8766只发GET，未使用真实桌面或Chrome profile。

## 1. 文件范围与W4

11个文件的实际SHA全部匹配实施清单：`clue_morning.py`、`external_tasks.py`、`today_view.py`、`public/today.html/js/css`、`tests/test_clue_morning.py`、`tests/test_external_tasks.py`、`tests/e2e/clue_morning.cjs`、`tests/e2e/external_tasks.cjs`、`tests/h3b_verify_sql.py`。证据 [sha.json](/tmp/review-hub-h3b/sha.json)。

- `scripts/radar_common.py`、`today_filter_config.py` 精确匹配H3a-M1交付SHA；H3b未改变H3a闸门。H3a-M2仍归其原任务，本报告不替它验收。
- `scheduled_tasks.py` SHA为 `7c18b2febf4fa2fbabfb4e35d795d541a02f0adc573c28d566ac15e46e7ae502`，与H1-L1交付一致；`server.py` SHA为 `728aa7b491bec880ffb3794a7149df695024afab5a43e4212cc6f28d4db7ea28`，与H1-L1独立复核快照一致。本单没有调度循环或server改动。
- 在内存中只逆转 `test_external_tasks.py` 的三处登记数10→9、一处扫描次数7→6，即精确恢复H1原始交付SHA `03b5c1eb49bf274d3889a78983d3909acdf5ed5303a0f0ed40cd652b5839664a`；证明没有其他改动或放宽断言。
- e2e相对H1-L1复核保留的副本仅变更登记9→10、总行数+9→+10及对应标签。该副本SHA精确匹配H1-L1原始交付 `1b752dafa19ded3508780baecd0a3f1a0f1a2d1d06ab3a6e678c0227541556b7`，没有删除旧行为断言。
- 对H3b前的 `today_view.py`、`external_tasks.py`、`public/today.*` 副本核diff：仅接入晨报快照、增加晨报心跳与入口/样式；未借此修改过滤或旧列表逻辑。

证据：[scope.json](/tmp/review-hub-h3b/scope.json)、[单测diff](/tmp/review-hub-h3b/h1-unit.diff)、[e2e diff](/tmp/review-hub-h3b/h1-e2e.diff)、[today_view diff](/tmp/review-hub-h3b/today_view.py.diff)、[external_tasks diff](/tmp/review-hub-h3b/external_tasks.py.diff)。

## 2. 定向测试与W2

按实施报告模块复跑，使用指定Python，并通过 `/tmp` 审计包装限制父进程的文件写入、SQLite连接及清理操作；正确处理了 `dir_fd` 相对清理路径。最终 **148项、0F/0E**，监测到2495次允许的写事件，全部位于临时目录（或 `/dev/null`），越界写0次。源码检查也确认新增单测均用 `/tmp` SQLite与受控patch，不写正式 `workspace/clue-morning`、`tasks.db`、`clues.db`。

```sh
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python /tmp/review-hub-h3b/targeted.py
```

模块：`tests.test_clue_morning tests.test_external_tasks tests.test_today_view tests.test_today_h3a tests.test_today_h3a_m1 tests.test_interest_channel tests.test_scheduled_tasks`。

证据：[targeted.log](/tmp/review-hub-h3b/targeted.log)、[test-safety.json](/tmp/review-hub-h3b/test-safety.json)、[包装脚本](/tmp/review-hub-h3b/targeted.py)。首版包装误把带目录fd的临时清理操作当作仓库相对路径，另误拒 `/dev/null`，修正包装后全绿；该首跑不作为产品缺陷。本窗口未跑全量，沿用实施的全量证据和已知受保护视频测试基线。

## 3. 真实SQL、文件与正式运行

只读连接两库，以产品JSON中的窗口复算；未调用生成器改写真实晨报：

- 窗口 `2026-10-29T05:55:34.489249+00:00` 至 `2026-10-30T05:55:34.489249+00:00`。
- 只取窗口内开始、状态`succeeded/partial`、kind为`wechat_clues/wechat_radar`的顶层`output.clues`；去重后再筛窗口内 `threads.created_at`。
- SQL ID集合与JSON完全一致，共971条；类别为20 / 403 / 141 / 73 / 357。多类别分别计数，类别和可以超过去重总数。
- Markdown头部总数和各类计数一致；`markdown_sha256`匹配实际正文；真实API全文与本地Markdown逐字一致。
- 正式任务 `ce31bd13dc704cbf9e4647867721d921` 为启用的daily Pi任务，08:30、全部七个执行日；指定run `fe67fb7f730c472392979bc419ba2cbd` 为`succeeded`，开始05:54:46.103061Z，结束05:55:54.970062Z；判断来源`pi`、生成时间05:55:52.464747Z。以上只读查询原记录，没有再次执行任务。

```sh
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python tests/h3b_verify_sql.py 2026-10-30
```

证据：[sql.json](/tmp/review-hub-h3b/sql.json)、[real-run-check.json](/tmp/review-hub-h3b/real-run-check.json)。

## 4. finalize防篡改、空报与W1

在 `/tmp` 合成SQLite上独立跑6项探针：

1. 模型给finalize额外传 `clue_count`、`category_counts`、`content`、`markdown` 均被拒绝，两个产物文件保持不变；CLI对应四种未知参数均退出2，未创建输出目录。
2. 改正文里的计数后，使用原expected-sha256补写被拒；传完全错误expected-sha256也被拒，拒绝时不改文件。
3. 正常补写只改末尾判断；既有单测验证分类、ID集合、正文前缀不变。
4. 无新增时 `clue_count=0`、类别全0、固定「当日判断：今日无新增」、`judgment.source=rules`；即使模拟Pi继续调用finalize，也报错且 `.md/.json` 字节不变。
5. 发言人反例：把合成群名、正文首个“昵称”、source_locator、邮件author放在前面，均不冒认为微信发言人；仅微信证据provenance里的speaker/sender_name/author可入结果；缺字段为空并呈现未知。
6. 当天运行引用两天前创建的线索，不计为新增，只保留同窗口新建项。

负对照只改 `/tmp` 模块副本：去掉摘要校验与创建时间过滤，原6项探针产生预期 **3F**；另仅去掉空报finalize拒绝分支，产生预期 **1F**。证明这些测试能抓到错误，而不是只验证正常返回。正式源文件未被替换。

证据：[probes.py](/tmp/review-hub-h3b/probes.py)、[正例日志](/tmp/review-hub-h3b/probes.log)、[CLI拒绝](/tmp/review-hub-h3b/cli-rejections.json)、[负对照3F](/tmp/review-hub-h3b/negative.log)、[空报负对照1F](/tmp/review-hub-h3b/empty-negative.log)。

主控对第4条明确回复：**“不调用模型”指不让模型补写判断或内容，不要求整个run零模型请求**；Pi执行prepare的首次模型请求可接受。当前空报路径据此通过，不声称整个Pi任务不占模型。

## 5. W3凭据与隐私

独立扫描实施报告、证据目录全部37份非图片文本文件，以及正式task和指定run的snapshot/output：常见Key/token凭据模式0命中；将真实晨报涉及线索的完整标题、摘要、微信证据原文作精确匹配，也为0命中，未输出匹配词或原文。本批真实微信证据没有具名发言人字段值，报告中不据此伪造姓名。实施运行记录与本次报告只写结构、计数与路径。

目视检查实施的真实晨报展开截图与运行记录截图，晨报正文已遮挡、运行截图仅为成功状态行；本次真实截图额外遮挡卡片标题、作者、阅读器与晨报正文。合成脚本反例只含明确合成文本。产品晨报本身按合同是正式内容，未复制到本次报告或临时证据。

证据：[privacy.json](/tmp/review-hub-h3b/privacy.json)、[扫描脚本](/tmp/review-hub-h3b/privacy.py)。结论限于所用模式、精确匹配及截图目视检查，不把模式扫描表述成对所有可能凭据格式的证明。

## 6. 浏览器、纯文本与首屏口径

独立Playwright检查使用1440×900及390×844，真实8766全程只GET：顶部状态栏入口可展开完整晨报；两个宽度均与API正文逐字一致、没有子标签DOM、没有横向溢出、不混入列表或阅读器；兴趣通道也能展开。未触发正式任务运行。独立随机端口服务 `/tmp` public副本，从 `/tmp/synthetic.md` 加载含 `<script>` 和img/onerror的合成晨报；标签原样显示、没有script/img节点，标记变量未执行。页面脚本错误0、写请求0。

本次独立检查20项通过，另有一条真实首屏绝对条数观察；原 `tests/e2e/today_page.cjs` **56项通过**，原夹具1440×900首屏完整5条，没有修改断言。该脚本首跑有一次1099.6ms触碰原1秒阈值；当时另有独立浏览器并行，未确认因果。其余浏览器关闭后单独复跑，首条三次160.5 / 110.1 / 85.6ms，56项全过，未放宽阈值。

**真实8766首屏只有4条完整卡片，不能写“真实已达5条”。** 固定同一批真实API数据，用隔离路由恢复 `/tmp/h3b-today-*-before.*` 的H3b前HTML/CSS/JS后，仍为4条；前后header都65px、列表top都228px，前7张卡片坐标逐项相等。真实前7张卡片均高122.375px，第5张底边955.875px，超过900px视口，因此不算完整可见。该占高在H3b前已存在，晨报入口没有新增挤占。

主控裁定第6条按 **“原夹具5条 + 真实布局不退化”** 验收，故本项通过；真实4条记为既有观察，不计本单H/M/L，是否另立项由主控决定。

证据：[browser-results.json](/tmp/review-hub-h3b/browser-results.json)、[layout.json](/tmp/review-hub-h3b/layout.json)、[原回归56项日志](/tmp/review-hub-h3b/today-regression-rerun.log)。截图：

- 真实 [1440折叠](/tmp/review-hub-h3b/real-1440-collapsed.png)、[1440展开](/tmp/review-hub-h3b/real-1440-expanded.png)、[390折叠](/tmp/review-hub-h3b/real-390-collapsed.png)、[390展开](/tmp/review-hub-h3b/real-390-expanded.png)。
- 同数据对照 [H3b前](/tmp/review-hub-h3b/layout-before.png)、[H3b后](/tmp/review-hub-h3b/layout-after.png)。
- 合成纯文本 [1440](/tmp/review-hub-h3b/synthetic-1440-expanded.png)、[390](/tmp/review-hub-h3b/synthetic-390-expanded.png)。

## 7. 08:30碰撞判断与后续观察

同模型计划经只读SQL复核，与实施说明一致：08:30另一日任务是script；09:30有intake_cards；wechat_clues、wechat_radar、github_clues、deals_review是间隔任务。同模型被占用时，`claim`先抛ModelBusy，不创建run，`tick`保留原到期时间后续重试；手动立即运行则返回忙，需要稍后重试。当前数据不足以证明08:30是固定冲突点，固定挪后15分钟也不能保证避开间隔任务的漂移相位。

**建议本轮保留08:30**，沿用原合同连续3个自然日巡检，记录计划时间、实际开始/生成时间与排队延迟；若超过心跳60分钟宽限，再依据实际空闲窗口调整。不要以“定时08:30”等同“文件08:30准时生成”。

另记主控要求的改进观察：空报虽然禁止模型补写，整个Pi任务仍会先占一次模型，也可能与deals_review排队；以后若要消除此占用，可另立项让确定性预检查先判断有无新增，空报直接规则落盘、有新增才交Pi补判断。本单未实现、也不计H/M。证据 [schedule-collision.json](/tmp/review-hub-h3b/schedule-collision.json)。

本轮复核已完成；连续3天巡检、是否优化真实首屏及空报模型占用由主控后续决定，不在本次只读复核中改动。
