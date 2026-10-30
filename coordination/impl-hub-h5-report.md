# 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **通过（实施自检，待独立复核）**；素材库导出脚本、唯一平台登记模块及真实调度完成 |
| H/M/L 自查 | 0H / 0M / 0L；合同字段计数已由协调者确认修正为 7，排除回收站的追加口径已实现 |
| 改动文件与 SHA256 | `platform_registry.py`：`92fa85d6d129c15ae128051b4d6262a239d6cd90badaefad5903cf33139fc7d5`<br>`scripts/library_export.py`：`014789e0daaf7dab3cf40f40e793b431702b6faff452fdc2c11dac1250764193`<br>`tests/test_library_export.py`：`d3921fe71fc0659083fac811b50d1807d97c766a362e11a372252406c7ea39d5`<br>`tests/test_platform_registry.py`：`4a4dfc61fbfa3386b2a5bfa233dad43ba74aa980267c1f93cb8bb0c8e9d99f10` |
| 测试数字 | 定向 Python **22P / 0F / 0E**（0.150s）；1000 条导出 0.0188s；隐私变异测试预期 **1F**；隔离浏览器真实路径通过 |
| 调度任务 id | `65dd58b0d5c54db28cb360580f83ada0`；script，每天北京时间 08:30，每周 7 天均启用，timeout=120s |
| 立即运行记录 id | `4492f1d4c3d74235935cd21cdc368522`；succeeded / exit_code=0 |
| 真实导出条数 | **101**；排除回收站 **1** 条；文件权限 0644 |
| 真实文件 SHA256 | `30e67c19b7750d548c3b8c36a3607eb0276391c40c106d88605591124b569ca4` |

## 范围与最终口径

- 任务 `task_2249a9e3c751` / Dispatch `ctx_bbd500ec2bb0`，实施终端 `term_02017284-4802-42f0-90ec-ab99b925b9ff`。
- 仅新增上述 4 个代码/测试文件及本报告；通过现有 API 建调度并写出 `/Users/huangzongning/grok-bot-reports/library-export.json`。不提交、不推送、不重启 8766，未修改 scheduled_tasks.py、server.py、items 存储代码或业务数据。
- 协调者消息 `msg_74a775f8a2cc` 确认：合同白名单实际上为 **7 个字段**（id/title/url/platform/source/tags/saved_at），不能为满足“8 个”笔误新增字段。
- 协调者消息 `msg_07ee0f596ed7` 追加排除回收站。真实 schema 为 `trashed_at TEXT NOT NULL DEFAULT ''`，实施用 `status IN ('kept','used') AND COALESCE(trashed_at, '') = ''`，兼容 NULL/空串，排除非空。已通过 `msg_ffa81528070b` 回报这一实现口径。

## 实现

`platform_registry.py` 提供唯一的 `derive_platform(item_id)`，登记 x/douyin/xhs/wechat/github/linuxdo/web 的 key、display_name，icon/color 预留为 None。支持前缀大小写，未知、缺冒号、空 id/后缀回落 web；R4 应直接复用并扩展此模块。本次仓库 Python 检索 `def derive_platform` 只有本模块一处；items_store.canonical_id 属于生成素材 ID，未修改。

`scripts/library_export.py` 使用 sqlite URI `mode=ro` 连接真实 `workspace/items/items.db`，显式 SELECT 元数据白名单，不读取正文/笔记。sources_json/tags_json 解析为字符串列表，坏 JSON/null/空串回落空列表，source 原值合并去重，不改写来源名称；saved_at 取 created_at，已有时区保留，无时区历史值按北京时间解释，按实际存入时间倒序。无效存入时间则失败并保留旧导出，不伪造时间。

输出目录默认调用 `grok_bot_radar.reports_dir()`，测试可注入；目标同目录 mkstemp，fchmod 保留旧权限（新建 0644），写 JSON、flush、fsync、os.replace，finally 清除临时文件。即使条目无变化也重新写 generated_at。CLI 仅打印成功条数或固定失败说明，不输出素材字段和原始异常。

## 验证与反例

命令：`../article-importer/.venv/bin/python -W error::ResourceWarning -m unittest tests.test_platform_registry tests.test_library_export -v`。

| 合同/风险点 | 自测结果 |
|---|---|
| W1 元数据 JSON 字符串、null、空串、坏 JSON、非字符串元素 | 通过；source 合并去重、原字符串保留；tags 列表 |
| W1 平台大小写、缺冒号、空 id、未知前缀 | 通过；6 平台及 web 登记，未识别值回落 web |
| W2 只写 /tmp，patch 清理，真实库只读 | 通过；测试数据库及报告全部在 TemporaryDirectory，mock context 自动撤销；missing DB 不创建、数据库字节哈希不变、连接 URI 检查 mode=ro |
| W3 不泄露 Key | 通过；合成私密正文/笔记不进入导出或 CLI 成功/失败日志；真实文件常见 sk-/Bearer/API_KEY 格式检查无命中，运行日志只有条数 |
| 字段隐私及变异 | 通过；准确断言每条仅 7 字段；/tmp 副本故意加入 summary，原测试报 1F 并指明 summary，正式文件未改 |
| 状态及回收站 | 通过；new/dismissed/其他状态不出现，kept/used 非空 trashed_at 不出现，NULL/空串正常保留 |
| 原子写/权限/失败 | 通过；replace 失败和 fsync 失败都保持旧文件且无临时残留，同目录替换在 fsync 后，0644/0600 均保持，新建 0644 |
| 空库及新鲜度 | 通过；count=0/items=[]，无变化仍刷新 generated_at |
| 时间与性能 | 通过；混合时区按真实时刻排序，带时区 ISO；1000 条 0.0188 秒，小于 1 秒 |

隐私变异证据：`/tmp/h5-mutation-l4tnlnqu/result.txt`（在独立源码副本运行，预期失败，不污染正式目录）。未跑全量 Python/UI，沿用合同基线。

## 真实浏览器与调度证据

使用 headless Playwright 的独立 `/tmp` profile，未使用 computer use 或黄总桌面/Chrome profile。真实浏览器打开 `http://127.0.0.1:8766/schedules.html`，表单创建任务（POST `/api/schedules/save`），核对任务列表，再点击该任务“立即运行”（POST `/api/schedules/{id}/run`），轮询该任务运行记录至 succeeded，并检查真实导出文件。页面 JS 错误为 0。

首次浏览器脚本在保存成功后错误地假设保存响应包含 kind，验证脚本断言失败；实际 API 仅回执 id，因此改为从任务列表读取完整任务并复用已建任务继续，无重复创建、无多余运行。该问题发生于验收脚本，不涉及产品或导出脚本。

- 调度 body：`/Users/huangzongning/development/article-studio/../article-importer/.venv/bin/python /Users/huangzongning/development/article-studio/scripts/library_export.py`，仅调用该脚本。
- cwd：`/Users/huangzongning/development/article-studio`；frequency=daily，at=08:30，weekdays=0..6，enabled=true，timeout=120。
- 下一次计划执行：`2026-10-31T08:30:00+08:00`。
- 立即运行开始：`2026-10-30T04:07:51.767146+00:00`；结束：`2026-10-30T04:07:52.231486+00:00`；输出：`素材库导出成功：101 条。`。
- 文件 generated_at：`2026-10-30T04:07:52.051995+00:00`；真实导出 ID 集合与数据库满足最终过滤条件的 ID 集合完全一致。
- 浏览器脚本：`/tmp/h5-browser-e2e.cjs`；结构化证据：`/tmp/h5-browser-waJ1GN/evidence.json`；调度行截图（运行前，已目视核对名称/08:30/启用）：`/tmp/h5-browser-waJ1GN/schedule.png`。

未来 08:30 自动触发尚未到时，当前已验证调度配置持久化、计算出的 next_run 和同一任务的真实手动执行链路。

## 真实分布与前 3 条样例

导出共 **101** 条；原 kept/used 共 102，排除回收站 1。按 platform 分布：

| platform | count |
|---|---:|
| web | 68 |
| x | 33 |

按 source 分布（多来源条目按来源分别计数，总数可大于条目数；名称不转换）：

| source | count |
|---|---:|
| G | 2 |
| L | 1 |
| articles | 19 |
| cards | 20 |
| external-references | 39 |
| intake | 16 |
| manual | 9 |
| projects | 1 |
| remio | 11 |

前 3 条（已按存入时间倒序）：

```json
[
  {
    "id": "x:2115862466068623609",
    "title": "Rapid-MLX 1.0：Mac 本地推理最高约 4× 加速；一行安装，OpenAI/Anthropic 兼容 API，可对接 OpenCode / Claude Code / Codex；含内置 Harness agent。",
    "url": "https://x.com/rapidmlx/status/2115862466068623609",
    "platform": "x",
    "source": [
      "G"
    ],
    "tags": [],
    "saved_at": "2026-10-30T03:53:41.105993+00:00"
  },
  {
    "id": "x:2115913639236665715",
    "title": "@openclaw：OpenClaw v2026.10.9 is out 🦞  🔧 Update r",
    "url": "https://x.com/openclaw/status/2115913639236665715",
    "platform": "x",
    "source": [
      "external-references"
    ],
    "tags": [
      "grok-bot",
      "起势",
      "Agent与Coding",
      "英推"
    ],
    "saved_at": "2026-10-30T01:10:52.641097+00:00"
  },
  {
    "id": "x:2115905733351120900",
    "title": "@openclaw：The number of agents just keeps growing",
    "url": "https://x.com/openclaw/status/2115905733351120900",
    "platform": "x",
    "source": [
      "external-references"
    ],
    "tags": [
      "grok-bot",
      "起势",
      "Agent与Coding",
      "英推"
    ],
    "saved_at": "2026-10-30T01:10:52.583565+00:00"
  }
]
```

报告生成：2026-10-30T04:09:33.342635+00:00。
