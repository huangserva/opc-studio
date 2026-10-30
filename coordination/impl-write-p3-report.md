# impl-write-p3 实施报告

## 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | 功能自测通过，交独立复核；不提交、不推送 |
| H / M / L | 功能 0H / 0M / 0L；过程另报 1M：服务环境凭据进入工具输出，见 §8 |
| 改动文件与 SHA256 | 18 个代码/测试文件，完整 SHA256 见 §6 和 `impl-write-p3-screenshots/modified-sha256.json` |
| 底稿可见耗时 | 正式 8766：504 ms；隔离：185 ms；均 ≤3 秒 |
| AI 起草耗时 | 正式库 30.319 秒；最终 3 个真实样本：31.912, 38.666, 23.416 秒 |
| 测试数字 | Python 83（原 60 + 新 23）；浏览器 127（素材库31 + 今日56 + 新隔离28 + 正式12）；真实模型样本3/3 |
| 8766 PID | 71674 → 37067 → 52117；最后一次重启后完成正式验收 |
| 真实发布记录 | 文件库 1 条 / 聚合接口 1 条 / 页面实际卡片 1 条；X 真实请求 0 次 |

## 1. 范围与批准偏差

- 实施 Dispatch：`task_b5e51fab7add / ctx_8ffb70979373`，工人 `term_02017284-4802-42f0-90ec-ab99b925b9ff`。
- 协调者通过 `msg_647e19502b64` 明确批准：保留 `Store.create()` 的空白 v0001；底稿实际 v0002，AI 初稿实际 v0003。AI 保存的 `base_version` 使用同步底稿返回的版本号，不写死；未改 `Store.create()`。
- `server.py` 只加新路由装配及 `/publish.html` 的 CSP 白名单。`items_store.py` 只加 `mark_used()`；二期 schema 本来已有 `used_work_id`，不需要迁移。`/use` 兼容接口保留，页面不再写剪贴板。
- 起草使用当前激活 profile 的现有 `Assistant.request_model()` 单次 Chat Completions 客户端，传空工具集、不调用 `assistant.start()`、不操作 `assistant.active`、没有 Agent 循环或自动重试；每个新稿一次调用，上限180秒。后台输入同时包含素材全文与起草模板。
- 同步底稿包含标题、来源链接、原文全文、长文/串推/引用补充、完整指标、为什么爆、可复制写法、切入角度和提纲。旧素材未存 `pattern` 时，尝试按同一素材 ID 从今日来源只读补回；仍缺则明确写「【待补：可复制写法】」。来源 JSON 与 `used_work_id` 在底稿完成后写入。
- 同一素材普通点击复用已有作品，Shift 点击新建；前端同步 `window.open('about:blank')`，建稿成功后定位 `/index.html?article=<slug>`。今日页和素材库同时显示起草状态、失败原因及冲突提示。
- 复盘聚合直接只读 `deliveries.json`、现有 SQLite 和 X 缓存，不启动旧存储写入器、不向平台请求。只纳入 `publication_metrics.PUBLISHED`；缺指标返回 null，页面显示「未知」和原因。发布时间缺失时明确标「登记时间（发布时间未知）」，按登记时间排序并计入7天，页上说明口径。
- 所有浏览器均为 `/tmp` 独立 profile + headless Playwright，隔离夹具随机端口；没有 computer use，没有操作黄总真实 Chrome 或桌面。

## 2. 反例 1–14 逐条自测

| # | 结果 | 实际内容与证据 |
|---|---|---|
| 1 助手锁 | 通过 | 隔离浏览器底稿起草 pending 时，创作台真实聊天框发送成功（202），收到「聊天正常开始，起草没有占用助手锁」；起草前 `active_job=null`。单测另外验证聊天有 active 时起草仍完成、active 不变。 |
| 2 人工修改 | 通过 | 底稿后调用真实保存接口保存「人工修改必须保留」，释放模型后状态 conflict；人工正文仍在，`ai-draft.md` 真含初稿与亲测占位；素材库卡片和详情显示「AI 初稿已另存，没有覆盖你的修改」。截图 `isolated/03-conflict.png`。 |
| 3 失败/超时 | 通过 | 假模型抛异常后 failed、底稿仍为v0002、页面原因可读；单测用0.04秒可控时限验证超时失败，晚到模型结果不再覆盖；生产上限由 `min(timeout,180)` 固定。真实模型产生无据经历也被拒绝，底稿保留。 |
| 4 重复建稿 | 通过 | 3 并发请求只创建1作品；浏览器同一条再点3次仍同一slug、作品数1；Shift点击新slug。两类验证都检查作品目录数量。 |
| 5 长原文 | 通过 | 超过2400字的中文原文含emoji和多行，底稿正文包含逐字相同全文；单测检查来源字段和2400浏览、0赞、未知收藏、100每小时；浏览器检查渲染的原文可见内容。 |
| 6 注入 | 通过 | 原文 `<script>`、`<img onerror>` 在底稿HTML中转义，创作台正文无script/img执行节点，`window.INJECTED` 未定义；模型正文也转义后保存。 |
| 7 风格 | 通过 | 最终3真实素材实际调用当前模型，全为v0003，禁词/emoji标号0命中、均含亲测占位、正文不同于原文，逐段人工读过，无已完成亲测经历，均有原作者handle；检查脚本见§4，证据 `real-models/final-style-check.json`。 |
| 8 来源链路 | 通过 | `source-item.json.item_id` 与素材 `used_work_id` 双向核对；隔离库通过真实手动登记接口新增已发布项，复盘页真显示原素材标题及来源图标，点链接定位素材库全文。正式库不做发布登记。 |
| 9 指标缺失 | 通过 | 无曝光字段显示「曝光 未知」，悬停说明未提供；0收藏保留0；NaN、Infinity、负数、布尔、非数字字符串显示未知，普通数字字符串可读，不产生非有限JSON数值。 |
| 10 弹窗策略 | 通过 | 没有授予弹窗或剪贴板权限的隔离profile中，真实用户点击能打开指定底稿，含原文全文。 |
| 11 来源校验 | 通过 | 缺Origin、错Origin、缺X-Article-Studio均403；检查前后真实作品总数相同，没有建稿。 |
| 12 范围与回归 | 通过 | 36 个禁止改动文件SHA256前后一致；二期Python60、素材库31、今日56全过。原验收断言替换列表见§3。`today_view.py` 没改。 |
| 13 X计费 | 通过 | 页面加载和全部验收 X 远端调用0；隔离测试后端 `remote()` 设拦截计数，浏览器也拦截外域；仅点击同步按钮才出现1次feedback-sync请求（由Playwright本地回应，没有转发X）。正式验收从不点同步。 |
| 14 内容而非数字 | 通过 | 底稿实际全文、来源、指标及提纲均读取比对；AI正文实际是新的中文待实测初稿；正式库已发布1、接口1、页面1。真实发布记录为既有「带图与助手合并验收 20261008」，浏览123、其他缺失显示未知，未伪造新发布项充数。 |

通用证据：`impl-write-p3-screenshots/isolated/results.json`（28项）、`real/results.json`（12项）、`python-all.log`、`python-new.log`、`protected-sha256.json`。本轮所有必测反例均已自测；整体是否验收由协调者独立复核决定。

## 3. 原 1、2 期 e2e 的必要修改

| 文件与旧断言 | 替换及原因 |
|---|---|
| `library_page.cjs`「拿去写复制全文并记录used」 | 改为建底稿后通过真实作品接口核对全文、status=used、used_work_id与弹窗article参数一致；本期明确取消剪贴板流程。 |
| `library_page.cjs`「三个旧页保留在旧版分组」 | 改为5个旧入口：新增移入的发布反馈、X官方接口，加原知识库/创意回收/提示词卡。 |
| `library_page.cjs` 隔离服务假 Store | 改为真实Store，旧知识读取仍用只读夹具；使新建作品链路能实际执行，而非模拟返回。 |
| `today_page.cjs`「复制全文链接why角度提纲且打开创作台」 | 改为底稿正文真包含这些内容并打开指定作品。 |
| `today_page.cjs`「剪贴板保留切入角度和提纲实际值」 | 改为实际底稿正文保留角度、提纲。 |
| `today_page.cjs`「拿去写记录used」 | 增加items.used_work_id与实际slug一致的断言。 |
| `today_page.cjs` 剪贴板截图 | 改为创作台底稿截图 `today-draft.png`。 |

两个旧套件仍分别31/31、56/56，其他断言保留。原用例本轮运行日志位于 `p2-library.log`、`p1-today.log`。

## 4. 真实模型风格检查与已纠正的问题

模板每条规范注明 AGENTS.md 或 `coordination/content-profile.md` §1、§5；事实/引用/长度规则同时标明本期合同§3。判断：`kind=post` 或有 thread 的素材生成3–8条串文，其他生成单篇300–800字；并不把串文总长度限制为800字。无亲测证据时第一人称写问题、计划和待验证步骤，不能从黄总人设推断已完成经历。

初次真实输出虽有占位，仍出现「我验证过」，我读实际截图后判不合格，随后增加提示和已完成经历校验；又修正一次误把「按我的账号情况确认……Codex是否可用」当经历的误拦。对应故意错误输入及正常句子反例已入单测。历史试验保留为 `initial-results-before-experience-guard.json`、`guard-trial-results.json`、`before-false-positive-fix-results.json`；最终结果仅以 `real-models/results.json` 和 `final-style-check.json` 为准。

最终真实样本（当前激活模型 `qwen3.8-flash-next-iq3_s`，独立临时库；每份新稿一次客户端调用）：

| 素材ID | 类型/正文条数 | AI耗时 | 结果 |
|---|---|---|---|
| `x:2115602263540470125` | 串文 / 6条 | 31.912秒 | v0003，禁词0、亲测占位、原作者标注、正文重写，通过 |
| `x:2115591064073413118` | 串文 / 6条 | 38.666秒 | v0003，禁词0、亲测占位、原作者标注、正文重写，通过 |
| `x:2115655889428623568` | 串文 / 5条 | 23.416秒 | v0003，禁词0、亲测占位、原作者标注、正文重写，通过 |

检查脚本（已对最终3份运行；除机械检查外逐段阅读正文）：

```python
from pathlib import Path
import json, re
from write_from_item import parse_answer, PLACEHOLDER, BANNED
p = Path('coordination/impl-write-p3-screenshots/real-models')
for row in json.loads((p / 'results.json').read_text())['items']:
    d = json.loads((p / (row['slug'] + '.json')).read_text())
    parse_answer((p / (row['id'] + '-raw-answer.txt')).read_text(), d['material'])
    body = '\n\n'.join(b.get('text', '') for b in d['detail']['blocks'])
    assert row['state']['status'] == 'done' and d['detail']['version']['id'] == 'v0003'
    assert PLACEHOLDER in body and not any(t in body for t in BANNED)
    assert not re.search(r'[0-9#*]\ufe0f?\u20e3', body)
    assert body != d['material']['full_text']
```

常见无据经历、空初稿、原文复制、禁用词及缺占位的故意错误输入均先确认被拒绝。事实校验不靠“有占位就算过”；本轮四份最终成稿（3份样本+正式验收1份）均实际阅读正文。

## 5. 正式服务与截图

- 重启前确认助手和定时任务没有活跃任务；两个实际重启记录见 `restart.json`。最终PID 52117，新路由200之后才运行正式浏览器验收。
- 最终保留测试作品：`article-20261029-c3ef26da`，标题前缀「【验收】」，底稿v0002 → AI v0003，30.319秒；可以打开 `http://127.0.0.1:8766/?article=article-20261029-c3ef26da`。
- 前一轮正式测试作品 `article-20261029-53847ca9` 也按合同保留，其旧AI输出在加强防编造前生成，不作为最终通过证据，不建议采用其初稿。没有删除任何黄总既有作品。
- 3份最终样本的临时库为 `/private/tmp/write-p3-real-models-5xanja6s/workspace`；完整素材、正文、版本、状态另存在持久证据JSON，不依赖临时目录才能复核。
- 正式库已发布记录1条；页面显示1条；7天发布0、其中来自今日/素材库0。来源素材显示与登记后复盘路径在隔离库实际验证（来源1条），没有向X/Buffer/R2写状态。
- 复盘接口正式5次耗时：[14.6, 10.2, 14.2, 12.5, 10.4] ms；隔离5次：[2.5, 2, 2, 1.9, 1.8] ms。
- 截图：`real/01-base.png`、`real/02-ai.png`、`real/04-publish.png`；冲突 `isolated/03-conflict.png`。截图已实际打开检查。

复现命令：

```sh
../article-importer/.venv/bin/python -m unittest tests.test_items_store tests.test_migrate_to_items tests.test_today_view tests.test_write_from_item tests.test_publish_review
node tests/e2e/library_page.cjs
node tests/e2e/today_page.cjs
node tests/e2e/write_and_publish.cjs
node tests/e2e/write_and_publish.cjs --real
```

`--real` 始终以Shift新建带【验收】前缀的测试作品，避免复用旧版本冒充新实现；仅建作品，不真实发布、不同步X。后端模型测试失败会保留底稿并记录可读原因；不会为了过测试覆盖人工改动。

## 6. 改动文件与 SHA256

工作区开工前已有大量其他任务改动，未回退、提交或覆盖它们。本轮既有文件的精确差异另存 `task-delta.patch`；禁止改动清单共36项前后SHA一致，见 `protected-sha256.json`。

| 文件 | SHA256 |
|---|---|
| `write_from_item.py` | `2a3aab0bb672c1633719326e4d4424ad87e2893b921fdcdc6541a82165b6c472` |
| `prompts/draft-template.md` | `a500cac0c1a9885bce60d8cca67a58b03fd4485e2b263d777cd97ed6965b0944` |
| `publish_review.py` | `37e7f0ecc7ce28273befa5997ba675d922c474b80c9f01d9f352f1ed113e5260` |
| `public/publish.html` | `229389eaeeeb203c4fe1358600f1e2be5708af9405d0cdf892510a201ceb782d` |
| `public/publish.js` | `5fad4ba9b336de2a6489032c2110077e7758342399cf982e2330c8e184e46aba` |
| `public/publish.css` | `982f26121a737b09e9ecf853b5fdcb2056f9430b60a1cd4eea70a2dd740ff1b7` |
| `public/components/list-reader.js` | `5dccb17868a4f5c44db086580eaacbb74a6570bb6b4df475fcf804b1e0fc5de6` |
| `public/components/item-card.js` | `510b02e087488748c2e8a94843c6e7fe75a16929e7bd7f9796bf6eb1ddd1602b` |
| `items_store.py` | `ecc06e9f7385aa0616fb59b903cb0481b0df03eb642c1104a003e1bf12e5eaa8` |
| `server.py` | `6e6c225bd5fff3b42936a6b14259311f26b9c3db95f5c3c1417505f088039a7e` |
| `public/workspace-nav.js` | `fe46f9da48350b397f052e6262dea2f73690b6915fb56c7a1fbec9a9892ef53f` |
| `public/library.js` | `d72d362398b58626b2839e9156348674fa9115ece60ddf19a2e77f06b2ee19e6` |
| `public/today.js` | `b3239057a08fb230a1c00aecefb18942c7acff2c609f31fd87de99dfa3272647` |
| `tests/test_write_from_item.py` | `2fc239ec1c05cef179cc9b61f75328d0004e04c596b71779dcfd0a33c405c932` |
| `tests/test_publish_review.py` | `76e203d826682c0d0a31fd122d4d18d4682190e6baf82fce2b2661f6afeeba96` |
| `tests/e2e/write_and_publish.cjs` | `9b7c11845fa4f3dccd40a18697ba39c14ad76d9709c57bad6a4c737d4479e6df` |
| `tests/e2e/library_page.cjs` | `47f05428b705da1e7608420040371f6decbe5430dbe59919f69f6ffe2f195e0f` |
| `tests/e2e/today_page.cjs` | `214cf627a4b3a8edaea097339c95468087d336c965f669528bb73a3f1b4d348b` |

## 7. 回滚与剩余边界

- 已执行 `git apply --reverse --check coordination/impl-write-p3-screenshots/task-delta.patch`，检查通过，未真正应用。协调者需要回滚时可用本轮逆向补丁恢复9个既有入口文件并重启8766；新增模块/页面可暂留为未引用文件，不需要删除作品、回滚数据库或移除二期字段。
- 现有 review 只读展示，编辑仍去旧页；X发布/账号设置仍去旧页；未新增任何定时任务。
- 既有记录没有真实发布时间时只能明确按登记时间统计，页面已说明；缺失指标不会估算。未执行真实X同步，实际外部权限和计费结果不在本轮自测范围。
- 剩余工作：由另一检查窗口独立复核；未提交、未推送。没有维护协调者专属的总队列或整体验收台账。

## 8. 过程事件（已升级协调者）

读取服务信息时，我误将 `launchctl print` 的环境段送入工具输出，包含AIsa环境凭据；这是工人操作失误。没有把凭据写到代码、报告、截图或外部渠道，后续全部服务检查均在子进程内部提取PID、只输出PID。已通过Orca `msg_13338381afe2` 升级；是否轮换凭据由协调者/黄总决定，本报告不包含凭据值。
