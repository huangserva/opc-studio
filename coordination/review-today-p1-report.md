# 决定要点

| 项目 | 结论 |
|---|---|
| 结论 | **通过**：11 项逐项复核通过；没有新增阻断项，保留下文明确列出的实测边界。 |
| H/M/L | 新增 **0H / 0M / 0L**；既有 **1L**：A2 静态旧页相同四项失败，独立复现为基线已有。 |
| SHA 与范围 | 报告 10 个 SHA256 全部与磁盘一致；`twitter-radar.js=e769f6c7008a…`；范围外 tracked 增量 0，既有测试增量 0。 |
| 排序 | 反例15、同热度按时间均通过；公式 `H=R×(高收藏时1+B/100，否则1)`，未知收藏不扣分，未发现独立的 AIsa 降权系数。 |
| M1 / M2 | **均通过**：真实双来源摘要/详情可在空白页直接传入；另用独立双来源 API 夹具逐项验证两边丰富内容。 |
| 测试数字 | 新 Python 22/22、实施方夹具浏览器56/56；独立浏览器35项通过、补测12项通过；旧A2基线与当前均30/34。 |
| 首屏与性能 | 1440×900完整可见5条；首卡313.3/110.8/106.2ms；正式GET列表/详情各5次最慢100.4/52.1ms；隔离POST5次最慢5.6ms。 |
| 运行与边界 | 正式PID52356，21:45:20启动；真实8766仅GET；全部写入在`/tmp`随机端口54775，未调采集/AIsa，未用computer use。 |

任务：`task_bc48f2043509 / ctx_e7a2c1fa6a40`。对象：`task_61609dae2ac7` 实施交付。复核时间：2026-10-29。只产出本报告与 `/tmp/review-today-p1/` 证据，没有修改业务代码、测试、真实反馈或服务进程。

## 1. SHA 与范围：通过

独立从实施报告的「文件 SHA256」节提取10项，与磁盘重新计算的摘要逐一比较，10/10一致。完整结果：`/tmp/review-today-p1/audit.json` 的 `sha`。

`twitter-radar.js` 当前完整摘要仍为 `e769f6c7008a1474fcdf1d4d3be86d08af3cadadaa27fba5c2b055039d139b5b`。对开工快照 `/tmp/impl-today-p1-baseline/before.diff` 与当前 `git diff` 按文件逐块比较，允许范围之外 tracked 增量为空。这里的“0增量”指相对开工基线，仓库本来已有大量未提交变更，并非声称工作区干净。

对开工保存的 `server.py` 与当前文件独立生成差异：只有3条路由各3行装配，共9行，外加批准的CSP一行替换。导航只在最前增加今日，其余项和顺序不变。证据：`/tmp/review-today-p1/server-increment.diff`、`audit.json` 的 `outside_tracked_increment=[]`。

## 2. CSP 例外：通过

正式GET比对 `/twitter-radar.html`、`/video-radar.html`、`/index.html`、`/cards.html`、`/intel.html` 五页：响应CSP均逐字等于开工版 `server.py` 的统一策略。前三页也与开工保存的响应头一致；后两页依据开工版统一装配策略核对，并没有冒称存在其改前响应头快照。

只有 `/today.html` 的 `img-src` 增加 `https:`，其余指令不变，保留 `Referrer-Policy: no-referrer`。独立浏览器给阅读器传 `http://images.test/insecure.png`，即使测试路由可提供图片，浏览器仍受CSP拦截，图片 `naturalWidth=0` 且隐藏，出现「封面暂不可用，请播放原帖」。安全封面方案和危险协议反例也由56项夹具测试覆盖。证据：`browser.log`、`browser-results.json`、`e2e.log`。

## 3. 排序：通过

实际代码：`today_view.py:118–136`、`:275–276`。

- 档位顺序：爆款、起势、低粉爆款、其他；同档位按 `H` 降序，`H` 相同按发布时间倒序，再按id稳定排序。
- `R` 优先取 `views_per_hour`；无该值时，取浏览量除以同次采样的发帖小时数。小时数优先 `hours_since_created`，其次 `(fetched_at-published_at)/3600`；无法得到小时数时回退浏览量，无速率无浏览量则0。
- `B=bookmarks`，`L=likes`；`B≥100`，或 `B>0` 且 `L` 已知且 `B≥L`，属于高收藏。此时 `W=1+B/100`，否则 `W=1`；`H=R×W`。`L=0` 且 `B>0` 同样加权，不除零。
- 视频用播放量和采样时差算速率，无时差回退播放量，没有拿点赞速率冒充播放速率。

独立夹具：同速率1000，较早的收藏412条目排在较晚的收藏111条目前；把收藏改为相同后，较晚条目排前。断言分别为 `x:21,x:22` 与 `x:22,x:21`，不是只检查结果中存在两个id。证据：`audit.py`、`audit.json`。原22项Python还覆盖收藏/赞比例和视频回退。

AIsa未知收藏评估：**符合第6节已授权的“高收藏额外加权”，未发现未知收藏被直接扣分，因此不记M。** 相同速率1000下，`bookmarks=None` 与0的热度均为1000；把相同指标的sources在G/A间切换，热度完全一致。以下两组真实例子来自正式48h接口：

| 比较组 | 来源 / 作者 / id | 每小时浏览R | 收藏B | 权重W | 热度H | 同档位位置 |
|---|---|---:|---:|---:|---:|---:|
| 1 | A / dotey / 2115591064073413118 | 9329.67 | 未知 | 1 | 9329.67 | 4 |
| 1 | G / eternityspring / 2115655889428623568 | 2594.05 | 412 | 5.12 | 13281.54 | 3 |
| 2 | A / AYi_AInotes / 2115404584160018851 | 5172.82 | 未知 | 1 | 5172.82 | 5 |
| 2 | G / criscxuan / 2115715632532713672 | 1953.71 | 111 | 2.11 | 4122.33 | 7 |

第一组解释了主控抽检疑点：加权确实足以让较低浏览速率的G帖排前。第二组说明A帖不会被一律放在G帖后；另有A来源Khazix0918以33818.41热度居同档位第1。该规则会让有高收藏证据的条目获得优势，这是合同明定的行为。**AIsa真实收藏值未知，无法验证其“如果知道收藏本应排第几”**；不能据此推断其实际质量更低，也不能仅凭跨来源速率逆序断言算法违约。

当前额外加权幅度无上限，合同没有规定上限；若黄总希望浏览速率更主导，需要另外确定权重目标，不能把新的倍率偏好追溯为本次缺陷。列表未知收藏已显示「收藏 未知」。原始响应与数字：`real-list.json`、`audit.log`。

## 4. 「该做的3条」不凑数：通过

无评分时 `top=[]`，页面显示「等 Grok Bot 按画像打分后出现」，正式首屏截图也可见。有评分但doable不足时显示「今天没有值得做的」，原Python测试覆盖。

独立夹具构造4条合格数据，分数和分别4、7、5、8，另有fit高但doable=1的反例；结果严格只出 `x:14,x:12,x:13`，即8、7、5分。另一套通过真实HTTP的独立浏览器夹具结果 `x:704,x:702,x:703` 同样按8、7、5分排列。没有依靠相同分数的测试来代替排序验证。证据：`audit.json`、`browser.log`。

## 5. 内容完整与安全：通过

独立构造含emoji、多行、全角空格、制表符、`&amp;`、`<script>`、`<img onerror>` 的中文长文，通过真实隔离API取出并在阅读器校验 `textContent===源字符串`。正文生成的对应script/img节点为0，注入标记未执行。不是只测字符串包含或长度。

原帖链接四反例 `javascript:alert(1)`、`https://x.com.evil.com/a/status/1`、空串、`//evil.com` 均不生成链接；合法 `https://x.com/a/status/1` 的正控制生成链接。媒体封面只接收http(s)，http进一步被CSP拦截并占位。

显式 `content_complete=false` 的隔离条目正确显示不完整警告和原因；正式视频详情同样显示不完整提示。启发式短正文高收藏、t.co结尾、提到未提供的长文/串推/引用由源码核对及现有测试共同覆盖。证据：`browser.log`、`supplement.log`、`e2e.log`。

## 6. M1 / M2：通过

**M1**：实际正式条目 `x:2115634000937340955` 的 `/api/today/item` 返回 `sources=['A','G']`；从正式作者查询拿到其摘要。在空白页只动态引入 `components/item-card.js` 与 `list-reader.js`，直接传入这两个API对象。来源徽标A/G、按钮准/不准/存/拿去写均正确；显式feedback参数和点击回调产生clear；`window.grokState`、`window.state` 均undefined。没有先运行today.js或twitter-radar.js。

**M2**：当前这个线上双来源条目没有article/thread/quoted/media，不能用它证明线上丰富内容逐项齐全。因此另外独立构造G/A各自独有的长文、串推、引用、图片，通过隔离实例的真实 `/api/today` 与 `/api/today/item` 返回统一对象，再在没有页面脚本的空白页，仅引入上述两组件，直接传对象。逐一断言G/A的6个独有文本、两来源媒体数量2、全文逐字一致；全部通过。不是在前端手工拼接兼容字段。

源码证据：`item-card.js:135`读取参数 `rich_sources`（兼容参数grok/aisa），`:139–142`逐来源渲染；卡片与动作读取item/options，`list-reader.js:5`起的可变状态封装于实例闭包。执行：

```sh
rg -n 'grokState|_sources|_grok|\bstate\b' public/components/item-card.js public/components/list-reader.js
rg -n 'window|document' public/components/item-card.js public/components/list-reader.js
```

第一条只有 `rich_sources` 相关字段/注释，无 `_grok`、`_sources`、grokState或页面state读取；第二条仅DOM创建/事件与window.open等浏览器API，无页面级可变状态读取。证据：`components-globals-grep.txt`、`browser-results.json`、`supplement-results.json`、`blank-rich.png`。

## 7. 反馈写入：通过

写入全部发生于 `/tmp/today-unit-6eekwvkf/` 对应的隔离端口54775，没有向真实8766发送POST。

独立HTTP验证缺Origin、错Origin、缺X-Article-Studio均403，四个反馈目标均未生成；随后并发POST两个不同id，两条均保留。撤销后今日记录和Grok记录都移除；Grok工作台反馈与Bot镜像逐字节相同。V条目写入旧格式 `{verdict:'worth',updated_at:…}`，旧 `/video-radar.html` 刷新并切到「抖音日榜」后，同key“值得跟”按钮 `aria-pressed=true`；撤销再刷新变false，反馈文件无该key。

首轮独立脚本在旧视频页默认小红书标签等待抖音按钮而超时，原因是测试未切榜单；没有改产品代码，补测明确切抖音日榜后通过。该脚本前35项通过，后续12项补测日志单独保留，不把首轮称为全绿。

今日写入有线程锁+flock，原子写复用fsync/rename；来源反馈沿用各自写入器。没有宣称多文件具备跨文件崩溃事务。证据：`browser.log`、`supplement.log`、`fixture.json`、`today_view.py:295–327`。

## 8. 首屏与性能：通过（正式POST性能未测）

隔离headless、独立 `/tmp/review-today-p1/profile` 打开真实8766，1440×900首屏完整可见5条；导航至首卡visible三次为 **313.3 / 110.8 / 106.2ms**。截图已实际查看：`real-first.png`。正式详情可点开；窄屏390×844验证列表→全屏阅读器→返回，阅读器宽≥380px。

| 接口 | 连续5次秒数 |
|---|---|
| 正式GET /api/today | 0.04550 / 0.10039 / 0.05182 / 0.09041 / 0.04454 |
| 正式GET /api/today/item | 0.05193 / 0.05027 / 0.05209 / 0.04669 / 0.04684 |
| 隔离POST /api/today/feedback | 0.00558 / 0.00430 / 0.00463 / 0.00420 / 0.00408 |

三接口各5次均<1s，但**正式POST耗时未知**：按本复核合同禁止对真实8766写入，不能把隔离POST数字冒称正式数据性能。35项独立浏览器断言通过部分没有pageerror；56项完整夹具也无pageerror。真实浏览器请求清单28条均GET，Python对8766也只用GET。

媒体测试为确保隔离，外网资源由路由提供测试响应，没有验证线上图片CDN当前可达性。CSP HTTP拦截是浏览器实际执行，未禁用CSP。证据：`audit.json`、`browser-results.json`、`e2e.log`。

## 9. 键盘与作者视图：通过

独立重跑的完整夹具E2E验证J换下一条、K返回原条目、Enter聚焦reader、O弹出正确原帖链接；作者点击能显示20天前同作者条目。Python作者边界测试同时验证1天/20天条目纳入、31天条目排除；读取已有数据，无采集。证据：`e2e.log`、`unit.log`，源代码 `list-reader.js:43–53`、`today_view.py` 的30天cutoff与 `today.js` 的作者翻页读取。

## 10. 测试未改弱：通过

对开工tracked diff逐文件比较，已有tests路径增量为空。新增测试检查全文精确相等、无注入节点、具体id先后、具体来源rich字段、403及落盘结果，并非只检查按钮/参数存在。独立补充了分数和各不相同的top排序、源标签G/A切换的热度等价、空白页两个组件实际接入、旧视频页反馈显示。

复跑：

```sh
PYTHONDONTWRITEBYTECODE=1 ../article-importer/.venv/bin/python -m unittest tests.test_today_view -v
node tests/e2e/today_page.cjs
```

结果Python22/22，浏览器56/56。未执行会写正式反馈的 `--real` 入口，真实页使用另写的只读脚本。没有跑全量discover，也不冒称本轮全量旧Python回归通过。

## 11. 既有1L：通过（确认为基线已有）

独立分别运行同一A2 E2E，页面目录指向开工副本与当前public；两次都 **30/34，pageerror=0、console.error=0、外网请求=0**。四个失败名称和原因一致：`M3a-缺tweets`、`M3b-缺vip_tweets`、`M3c-缺topic_clusters`、`M3d-字段类型错误`，均为回退内嵌低粉榜为空。当前twitter-radar.js又与开工SHA一致，证据支持这四项不是今日页本次增量造成。

```sh
RADAR_A2_DIR=/tmp/impl-today-p1-baseline/public RADAR_A2_EVIDENCE=/tmp/review-today-p1/a2-baseline node tests/e2e/twitter_radar_page.cjs
RADAR_A2_DIR=/Users/huangzongning/development/article-studio/public RADAR_A2_EVIDENCE=/tmp/review-today-p1/a2-current node tests/e2e/twitter_radar_page.cjs
```

证据：`/tmp/review-today-p1/a2-baseline.log`、`a2-current.log`。既有豁免沿用，没有声称四项已修。

## 证据入口与未验证边界

所有本次脚本/响应/日志/截图：`/tmp/review-today-p1/`；完整夹具E2E结果：`/tmp/today-e2e-V5ICea/results.json`。关键文件：`audit.json`、`real-list.json`、`real-dual-detail.json`、`browser-results.json`、`supplement-results.json`、`server-increment.diff`、`unit.log`、`e2e.log`、`real-first.png`、`real-video.png`、`blank-rich.png`。

正式POST性能、线上双来源缺失的丰富内容真值、外部CDN当前可用性均未直接验证，已分别在第6/8节写明。它们没有被计为已测通过。生产PID52356及启动时间已通过 `ps -p 52356 -o pid,lstart,command` 核实；本轮未重启正式服务，也没有读取任何协调者收件队列。
