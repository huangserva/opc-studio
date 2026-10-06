# OPC Studio · 内容工作台

面向个人的本地优先知识与内容工作台：把多源资料组织起来，查证与问答，形成有依据的创作，再把作品、发布表现和复盘结果回收到知识库。

产品按 remio 式个人知识工作台定义展开。当前仓库来自实际使用的本地工作台，正在去除个人配置耦合、整理可复现运行边界；这不代表已经实现完整的 remio 功能对等。

两条核心链路：

- 资料 → 线索 → 事件 → 事项 → 日程。
- 资料 → 主题资料夹 → 有依据的创作 → 发布版本 → 平台表现 → 复盘回库。

商单管理保留独立入口，处理履约与收款。后台运行脚本或 Pi 的定时任务有自己的运行记录与失败处理。导航如何分组仍在讨论，不是本次仓库整理的既定改版。

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
