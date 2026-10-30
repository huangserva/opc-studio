# R3-0 实施报告

任务 `task_e606f8d6b899`，Dispatch `ctx_2064edcd9928`。本报告仅含脱敏统计、代码位置与合成反例；不含真实聊天内容、成员昵称、微信号或微信会话标识。

## 决定要点

| 项目 | 结论 |
|---|---|
| 结论 | 本轮实施与真实路径自检通过；第二轮无最终错误，因预算耗尽而partial；24小时待见证 |
| H/M/L | H=0；M=模型请求偶发超时、68,928版本仍待处理；L=来源增量采用追加保存，显示部分已同步 |
| 根因 | URL 去尾标点后未去重，`radar_links(document_id,url)` 唯一键冲突发生于模型调用前 |
| 插件与 SHA256 | `wechat_radar_pipeline.py`：`6e6b58fc69a1bf31cff58cde6185f838f37d90bfddd8cf0a92b91a4bde4fae62`；`pi_executor.py`：`a00b4e2c51f309bdcae13122a14f54b270546cccef55d6019b29002266ae68b4` |
| 仓库与 SHA256 | `wechat_links.py`：`d4007c287d37181f1175dd6b3ace785c7b5965d74faa529b4063337c51776064`；测试文件见下表 |
| 测试 | 微信定向123项：122通过、1跳过；隔离浏览器6/6；旧行为反向验证能检出故障 |
| 真实运行记录 | 首轮 `6e78cd15ad9d4a7bb25a487fa3930ca2` partial、90条已提交；补超时后 `109dbe86cbbb405fb5e43ce6a0f3c5f6` partial/time_budget，797条29批，无最终错误 |
| 链接与历史 | 新增4814，24887→29701；最新2026-10-30T04:26:58Z；旧24887条完整行指纹全部相同；24小时待见证 |
| 服务 | 来源按两次明确批准重启至PID88663；8766按批准仅重启一次部署时至PID77037（后续B获批重启不属于本单）；未提交、未推送 |

## 根因与修复（观察 / 推断 / 未知）

【观察】复用 `coordination/diag-r3-0-report.md` 已确认的唯一键冲突根因，未重新发起诊断线。改前插件SHA为 `be4c35c54c039a0147fe5c7b8954d5fccb4169cbf5ea290a8a9e646e7b0bc1b0`，备份在 `/tmp/impl-r3-0-02017284/wechat_radar_pipeline.before.py`。

【观察】`publish_links()` 在尾标点规范化、URL安全校验和真实区间定位之后，按 `(document_id,url)` 显式去重，稳定保留首个可验证区间；跨消息同链接仍分别保留，`share_count` 按消息ID集合统计，DELETE/INSERT仍处于同一事务。新增失败回执包含异常类型、阶段、SQLite错误码和只含文件名/函数/行号的堆栈，`failures.error` 新记录为含 `message` 和同组诊断字段的JSON，保留历史记录原样；SQLite原异常文本不会进入日志/回执。

【观察】真实知识库只读、派生库仅在 `/tmp` 的两次发布均为4823个唯一消息/URL对，引用区间/原消息hash错误0，覆盖到2026-10-30T04:26:58Z；临时派生库已删除，仅保留统计。证据 `real-projection-results.json`。

【观察】W1反例在旧代码上失败：前后说明、代码块原可恢复，多段JSON原先用首个 `{` 到最后 `}` 拼接，产生 `JSONDecodeError`。主控明确扩权后改为逐个解码完整对象，仅接受唯一的雷达对象，多个有效雷达答案拒绝并走现有修复路径；不改变条目校验规则。三种包装均自测通过，二义性反例也会拒绝。证据 `w1-results.json` 和 `mutation-old.txt`。

### 真实第四次模型调用的独立超时

【观察】首轮真实运行05:04:10Z开始、05:06:56Z自然结束：90条已提交，4次批次模型尝试，3批有提交指标；第四次调用11863ms，`stop_reason=error`、`content_types=[]`，仅提取助手错误字段的原文为白名单静态消息 `Request timed out.`，上层错误为 `Pi 分析未返回有效正文，本批未推进：模型执行失败`。堆栈为 `analyze → call_pi → execute → run:119`，未到 `validate`。此超时与多段JSON无关，不是已观察到的空输出、超长或JSON解析故障。

【观察】该批执行回执本地 `timeout_seconds=180`、剩余总预算约1637秒；`worker.mjs` 的 in-memory provider 超时180000ms、`monitoredStream`180秒，Pi本地依赖会透传 `timeoutMs` 至 OpenAI SDK。没有发现本地12秒请求阈值，未修改180秒、Pi全局配置或模型服务配置。当前Node内置undici6.23.0连接默认10秒，OpenAI SDK会把原生连接超时归成同一个 `Request timed out.`。

【推断】11.863秒与原生连接超时的量级吻合。**未知：历史错误已没有底层cause，不能断言这次必然由undici连接超时或模型服务端导致。**

【观察】经主控第二次扩权，`pi_executor.py` 仅新增 `ModelTimeoutError(failure_kind=model_timeout)` 和同一批最多一次有界重试：1秒可取消退避，剩余预算必须足够再容纳一次配置请求及5秒余量，否则不再发送；超时重试与空输出重试共享最多2次总尝试。回执分别计 `model_timeout_retries`、`empty_output_retries` 和每次尝试/退避，不记录服务原诊断文本。其他错误行为不变；超时重试仍失败时雷达回执partial/model_timeout，保持待处理，不隔离、不写该批analyzed。改前备份 `pi_executor.before.py` SHA `5d2b80cd505825b7f6d3e9ff584dda089c8dbef0fa97ef96eabd5d09537c278a`。

## 链接改读与历史保护

【观察】最初按合同只读核对发现新旧覆盖/字段不同，已向主控停下报告。主控转达黄总明确决定接受缩小到群消息，追加保留历史，不复活旧TS链。新适配器固定读取当前workspace的 `radar-analysis/assistant.db.radar_links`；旧 `radar.db` 不写、不删、不恢复旧同步。

| 字段 | 映射/处理 |
|---|---|
| 身份、时间与证据 | `document_id/chatroom_id/local_id/hash/start/end/text` 来自投影，并与当前原消息ID、hash、timestamp及真实区间逐项一致才导入；`timestamp`从有时区的`modified`推导 |
| url / canonical_url | `url`保留投影来源值；canonical按旧`message-links.ts normalizeUrl`的归因参数移除规则在Python本地移植，HTTP(S)校验；10项合成向量与旧函数结果一致 |
| 标题 | 已有投影标题有效则用；wxid/空/主机名占位标题回落原消息现有卡片title/des/digest，再取普通正文开头并截断160字，否则「无标题」；不抓外网 |
| description / raw_kind / confidence | 无描述记空字符串；固定`radar_projection`只说明数据通道；confidence未知记null，不编造 |
| created_at | 本次新增写入时间；不是旧分享时间 |
| 来源定位 | 表名`radar_links`，键`[document_id,url]`；原消息引用精确到真实链接区间，保留源记录指纹与标题依据 |

【观察】去重键仍是 `json.dumps([chatroom_id,local_id,canonical_url], ensure_ascii=False, separators=(',',':'))` 后使用同一知识库identifier。已有文档只查存在，不执行 `_write`；同批canonical碰撞本地再去重；返回非完整快照，阻止 `Knowledge.sync()` 的删除分支。历史新旧不因投影窗口缩小或来源暂停而清理。来源detail与新增文档scope标明「新增仅群消息、自radar_since起；历史分享文档原样保留」，实际任务起点 `2026-10-05T16:00:00Z`（北京时间10-06 00:00）。

【观察】`DOWNLOAD_RULES`为可维护host/path规则常量：微信文件域、qpic/qlogo图片域及微信下载路径按解析后的主机边界和路径段匹配；不按URL子串匹配。含该域名的外站路径、query、伪装子域以及mp.weixin.qq.com文章均有反例。真实新投影4823候选的处理结果：

| 指标 | 数量 |
|---|---:|
| 新增 | 4814 |
| 重复 | 4 |
| 无效HTTP(S)候选 | 4 |
| 原消息身份/hash/时间/区间不匹配 | 1 |
| 私聊 / 敏感参数 / 文件 / 图片过滤 | 0 / 0 / 0 / 0 |
| 标题：正文回落 / 已有投影 / 无标题 | 1774 / 573 / 2467 |

【观察】原有24887条完整行先逐条哈希形成基线，导入后及第二次页面同步后24887条全部一致，缺失0、改写0（强于抽样）。总量24887→29701，最新由2026-10-05T18:56:29Z推进到2026-10-30T04:26:58Z。二次页面同步新增0、重复4818，总量仍29701。`partial`表示追加保留的来源不是全量替代快照，不等同失败。旧来源配置中的历史path不参与读取，实际源定位在新增文档provenance中指向新库。

## 自测与部署证据

- 微信定向 `../article-importer/.venv/bin/python -m unittest discover -s tests -p 'test_wechat*.py'`：123项，122通过、1跳过；未并发/单独启动全量；不将受保护video_lab和lab-test的既有基线问题算本次回归。
- `node tests/e2e/wechat_links_r3.cjs`：随机端口、合成数据、独立临时headless profile，6/6通过，页面更新来源→列表→标题→打开出处→核对真实消息区间→再次同步；截图仅合成数据，证据目录 `/tmp/wechat-links-r3-e2e-EM1ojV`。
- W1：三种模型输出包装和URL host/path正反例通过；W2：所有patch上下文/cleanup，所有自动测试数据在临时目录，模型超时使用本地合成stdio worker；W3：37个改动文件/报告/证据文本文件的常见密钥模式扫描0命中，只报告计数，不打印匹配值。
- 旧行为反向：`/tmp`原插件跑同组雷达测试出现1F/4E（含新增包装与去重反例）；旧pi_executor跑5项超时测试2F/3E；将新链接适配器只改回旧全量清理行为，历史保护测试F。正式代码对应测试均通过。
- 来源首次重启4070→58090（装载去重/W1），第二次经新增明确批准58090→88663（装载超时分类/重试）；8766按批准仅一次23580→77037，均只用launchctl list看PID/退出码，未读取plist/env。
- B通知：第一次短句柄投递失败，随后通过 `orca terminal list` 获取完整句柄，`msg_6ef3bf4bbd49` 已入队通知B「8766已重启，可做H1-L1只读验收」；不声称对方已读。
- 真实工作台浏览器用独立`/tmp` profile，无computer use。首次立即运行run见上；第二次05:20:48Z开始、05:45:25Z自然结束，partial/time_budget=true、797条29批、error_kind缺省、无隔离与丢弃；任务有历史积压，不能写succeeded。链接补齐走真实知识库「更新这个来源」按钮，仅拦截页面自带的其它来源自动POST，目标同步接口未mock；未对真实页面截图/输出原文。

## 变更清单与回滚

| 文件 | SHA256 |
|---|---|
| `wechat_links.py` | `d4007c287d37181f1175dd6b3ace785c7b5965d74faa529b4063337c51776064` |
| `tests/test_wechat_links.py` | `67a1d3935978a59e4a1d6a39953125d98049b5e219daa5aa96b8e5ef915c385e` |
| `tests/test_wechat_radar_pipeline.py` | `f6b4a441086387245925d813a020d741610d023e7625282988869b387f5e97ed` |
| `tests/test_wechat_source_pi_timeouts.py` | `33198b96e8fc8a77565911cd85f5ca45a5daa42d612341fcd8b43f879db4aa84` |
| `tests/e2e/wechat_links_r3.cjs` | `f3e148fe1fc17e88f255818241db3dae985cfdff9ad39e16acce12d9aaf815a1` |
| `/Users/huangzongning/Documents/Codex/2026-10-03/b-q/work/wechat-source/wechat-assistant/wechat_source/wechat_radar_pipeline.py` | `6e6b58fc69a1bf31cff58cde6185f838f37d90bfddd8cf0a92b91a4bde4fae62` |
| `/Users/huangzongning/Documents/Codex/2026-10-03/b-q/work/wechat-source/wechat-assistant/wechat_source/pi_executor.py` | `a00b4e2c51f309bdcae13122a14f54b270546cccef55d6019b29002266ae68b4` |

插件两文件与 `wechat_links.py` 改前备份均在 `/tmp/impl-r3-0-02017284/`。代码回滚需恢复对应备份并按项目门槛登记重启；**不得回滚已新增的分享文档、改写历史、重置analyzed或清空failures**。恢复旧导入器后不可让其全量同步删除新增分享；如需回滚导入器应由主控另定保留历史/新文档的方案。

## 待收口

第二次真实雷达自然终态已见证：`109dbe86cbbb405fb5e43ce6a0f3c5f6` 于05:45:25Z自然结束，partial、time_budget=true、has_more=true、processed797、29批，无最终error_kind，无隔离、丢弃。独立只读检查：本轮新增analyzed797，累计88308→89105，pending69725→68928，减少797；两次真实运行合计推进887条。首轮真实失败记录和analyzed均保留，未重置游标或清空失败表。05:45Z发现终态后已发送 `msg_38930485f00c` 通知主控，可放行B重启8766；未自行再重启或启动新任务。05:36:51Z，第二轮在processed751的真实批次首次请求model_timeout，退避1秒后唯一一次重试returned，execution-receipt总耗时31528ms；该批30条已提交，累计781条27批。该观察证实新重试机制在真实模型超时时恢复，未隔离/丢弃原批。工作台自然自动同步见证：本工人最后一次页面同步完成为05:21:15Z，其后未再发同步请求；来源synced_at于05:26:45Z推进，总量仍29701，历史24887条完整行指纹仍全部一致、缺失0改写0。与Knowledge.start_watch的10分钟周期吻合，证据auto-sync-witness.json；没有独立持久化的触发者字段，因此触发者归因标为基于代码周期与无工人触发的推断。连续24小时运行成功：**待见证**，交主控巡检，不写未来结果。未提交、未推送。

## 证据索引

`/tmp/impl-r3-0-02017284/`：`tests-wechat.txt`、`tests-timeouts.txt`、`tests-links.txt`、`mutation-old.txt`、`mutation-timeouts.txt`、`mutation-links-pruning.txt`、`real-projection-results.json`、`w1-results.json`、`before-run.json`、`before-run2.json`、`receipt.json`、`model-error-class.json`、`run2-executor-receipts.json`、`receipt2.json`、`after-run2.json`、`auto-sync-witness.json`、`links-browser.json`、`links-browser-repeat.json`、`history-before.json`、`history-after.json`、`shas.json`。历史指纹明细只含文档ID与单行哈希；真实消息原文未复制进报告/测试夹具/日志。
