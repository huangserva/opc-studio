# OPC Studio · 内容工作台

本地优先的个人内容工作台。主链路是：**今日 → 素材库 → 拿去写 → 发布复盘**。从值得关注的内容中保存素材，生成可继续编辑的底稿，在创作台完成作品，再查看发布记录与复盘。

主导航固定 6 页：

| 页面 | 用途 |
| --- | --- |
| 今日 | 阅读已有来源的候选内容，查看全文并保存素材 |
| 素材库 | 管理收藏、检索资料，用「拿去写」建立底稿 |
| 创作台 | 编辑作品与版本；沿用原「我的作品」入口 `/` |
| 发布复盘 | 汇总已发布作品、来源、表现快照与复盘备注 |
| 商单 | 管理合作、履约与收款 |
| 设置 | 集中查看来源、定时任务、模型、脱敏日志和后台服务 |

「工具」分组保留图片生成、视频生成。「旧版」分组保留线索中心、情报采集、GitHub 来源、定时任务、发布反馈、X 官方接口、知识库、创意回收、提示词卡，原路径不变。旧入口保留至 **2026-11-29**，届时另开单决定是否撤除，不提前删除数据。事项与日程已从导航移除，`/events.html` 和原接口仍保留；线索中心与助手的事件入口暂时关闭。

设置页显示本地只读快照，未知信息会明确标注；来源实时状态需进入原来源页查看。暂停/恢复任务和切换模型复用既有接口，完整任务编辑仍在旧定时任务页。日志只开放三个白名单文件，最多 500 行并脱敏；后台服务仅展示标签与最近日志时间。

模型、媒体生成和平台发布等外部集成需要对应环境，不承诺所有连接器开箱即用。助手生成的初稿仍需人工核对事实，不能把第三方素材写成自己的亲测经历。

产品范围和术语见 [docs/product-scope.md](docs/product-scope.md)。

## 技术与目录

- Python HTTP 服务、SQLite 与本地文件存储。
- Vanilla JavaScript 页面，无前端框架或打包步骤。
- Node.js 22.19.0 或更新版本；Pi SDK 通过私有 stdio 与 Python 交换请求。

| 路径 | 用途 |
| --- | --- |
| `server.py`、`*_store.py` | 本地接口与业务数据 |
| `public/` | 页面、样式与交互 |
| `pi-runtime/` | 助手运行层与创作工作流 |
| `publishing/` | 作品包、投递与发布适配 |
| `generation/` | 可选媒体后台适配 |
| `tests/`、`scripts/` | 合成数据测试与检查入口 |
| `workspace/` | 本机运行数据，不纳入 Git |

## 安装与检查

需要 Python 3.14、Node.js 22.19.0 或更新版本。离线媒体测试还需要 PATH 中的 `ffmpeg` 和 `ffprobe`，中文封面需要中文字体（macOS 使用系统黑体，可运行 `brew install ffmpeg`；Ubuntu 可运行 `sudo apt-get install ffmpeg fonts-noto-cjk`，CI 已配置安装）。具体依赖以仓库锁定文件为准。在仓库根目录执行：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
npm ci --ignore-scripts
npm ci --prefix pi-runtime --ignore-scripts
node pi-runtime/prepare.mjs
```

执行核心离线检查（包含 Python 与 UI），再检查空资料库 HTTP 接口：

```sh
python scripts/check_offline.py
python scripts/smoke_local.py
```

只检查界面时可执行 `npm run test:ui`。范围与外部依赖见 [docs/offline-checks.md](docs/offline-checks.md)。

离线检查只证明其列出的测试范围。依赖真实连接器、外部部署或私人环境的完整检查另用 `python scripts/check_offline.py --full`；条件不满足时，应保留失败或缺失说明，不能声称完整通过。

## 本地运行

首次运行使用新的空目录：

```sh
python server.py --workspace ./workspace --port 8766
```

打开 [本地工作台](http://127.0.0.1:8766)。服务只监听回环地址。`Ctrl+C` 停止服务，运行数据保留在指定目录。

正式启动会启动后台来源监视与定时调度，因此不能用正式启动命令代替离线检查。接入来源或导入旧工作空间前，先核对本机配置与数据路径。

模型服务地址、模型名称与密钥在本机配置。remio、微信来源、MemOS、媒体后台等集成按需配置；缺少对应服务时不代表这些集成已经可用。旧媒体桥接所需的额外依赖见 `requirements-bridge.txt`。

可选本机配置：

| 环境变量 | 用途 |
| --- | --- |
| `ARTICLE_STUDIO_MODEL_CONFIG` | 仓库外的私有模型配置文件 |
| `ARTICLE_STUDIO_NODE` | Node 可执行文件路径 |
| `ARTICLE_STUDIO_USER_NAME` | 助手称呼，默认“用户” |
| `ARTICLE_STUDIO_REMIO` | remio CLI 路径，默认查找 PATH 或 `~/.local/bin/remio` |
| `ARTICLE_STUDIO_DEALS_SEED` | 显式指定本机商单导入清单；默认不加载历史商单 |
| `ARTICLE_STUDIO_DEAL_ALIASES` | 本机 JSON 对象：商单名称对应别名字符串列表；未配置时以客户名称查询证据 |

商单清单、别名与模型配置文件留在仓库外；仓库不提供真实数据样本。

## 协作与数据

问题先进入 Issue，实现与修复通过 PR 审查。提交方式见 [CONTRIBUTING.md](CONTRIBUTING.md)，Agent 工作规则见 [AGENTS.md](AGENTS.md)。

真实工作空间、聊天与运行历史、密钥、Cookie、邮件或微信原文、商单与收款数据不得进入 Git、Issue 或 PR 附件。复现使用合成数据，截图与日志先脱敏。当前 GitHub 仓库公开可见；代码导出不包含这些私人运行数据。

## 许可

本项目尚未选择开源许可证，不应据此视为已授权开源再分发。`pi-runtime/vendor/openpi/` 中的上游代码保留独立的 [MIT 许可证](pi-runtime/vendor/openpi/LICENSE)及 [来源与适配记录](pi-runtime/vendor/openpi/UPSTREAM.json)。
