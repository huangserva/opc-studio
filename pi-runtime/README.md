# 工作台 Pi 运行层

使用本地 Pi SDK 1.0.0，Node.js 22.19 或更新版本。安装与恢复：

```sh
npm ci --ignore-scripts
node prepare.mjs
```

Pi 管理模型/工具循环、流式回复、图片上下文、会话恢复和上下文压缩。Python 通过私有 stdio 调用业务工具，继续管理作品版本、修改校验、生成任务和结果保存。没有新增对外控制端口。

会话保存到 `workspace/assistant/pi-sessions/<conversation_id>/`。`history.json` 是界面显示记录，旧记录及手动生成任务通过记录 ID 导入 Pi 一次；Pi 原生会话不会从该记录重复重建。新对话创建独立的会话目录。

只加载工作台提供的工具与提示，不发现用户全局 Pi 配置、技能、凭据或通用文件/终端工具。模型配置仅通过 stdin 传入内存，不写入 Pi 会话。写入权限在 Python 再校验；阅读后修改、版本冲突、未保存修改保护继续生效。只读能力、界面状态、生成状态查询不需要关键词。

媒体任务由原有持久化后端负责。Pi 提交任务后返回提交回执，进度由生成卡片显示；中止聊天不会假装取消已经提交的 GPU 任务。

上游 SDK 发布包的 shrinkwrap 捆绑了 brace-expansion 5.0.9。`prepare.mjs` 移除这一旧副本，令 minimatch 使用本项目固定的 5.0.12（满足其 ^5.0.2 范围）。每次安装后都需要运行准备脚本；Python 初始化也会自动执行它。不要只看 npm audit 的锁文件结果，需核对真实模块解析版本。

验证：

```sh
cd ..
python3 -m unittest discover -s tests
```

`tests/test_pi_runtime.py` 使用真实 SDK 和本地 HTTP/SSE 接口验证工具协议、持久会话、图片、写入保护、中止与空回复处理。实际 Qwen 接口另做只读对话验证。

联网业务工具位于 `assistant_web.py`。`web_search` / `web_fetch` 通过 remio CLI 私有进程调用现有服务，单次 45 秒超时，每轮最多 8 次；可中止等待并限制输出大小。限定域名在工作台再次过滤结果；网址先校验 HTTP/HTTPS、凭据、标准端口及公开地址，正文提取与内部跳转由 remio 提供。来源投影到聊天操作卡片并保存。通用 Shell、文件工具和 Pi 自动扩展发现仍不开放。

## 内容创作工作流

“调研写稿”显式选择启动 research-write：联网调研 → 写稿 → 审稿与修订。真实 OpenPI `runWorkflowSandbox` / `RunController` 管理阶段依赖、调用上限与取消；源码选取自固定提交，保存在 `vendor/openpi`，MIT 许可、原始哈希和仅改变 limits 导入的适配说明见 `UPSTREAM.json`。未安装整套 OpenPI，也不加载其 TUI、全局设置、普通编码工具或动态模型生成的脚本。

工作流脚本固定在 `workflow.mjs`。调度器用私有 stdio 请求 Python 启动各阶段，每阶段通过现有 Pi SDK 创建独立持久会话，不导入原聊天历史。第一版并发为 1，最多 3 个阶段；调研最多 8 次联网调用，后两阶段只允许结构化提交，不能修改作品或访问系统。每阶段必须成功调用 `submit_workflow_stage`；普通文本回复、失败和停止均不会推进下一阶段。

调研证据的 URL 必须来自实际 `web_fetch`，最多 32K 字符网页摘录传给写稿与审稿。草稿和修订稿的 Markdown 链接必须属于这些来源，图片不在此流程中生成。审稿是模型根据有限证据进行的检查，不构成独立事实认证；重要未核实内容用 needs_review 标记。

结果保存在 `workspace/assistant/workflows/<id>/run.json`，原文证据单独保存到 `research-evidence.json`，Pi 会话仍在原私有会话目录。刷新能继续查看；重启标记中断并保留阶段结果，不自动重放。点击“保存为新作品”才提交独立的 v0001，固定 slug 与保存回执保证重复点击和保存回执丢失重试不重复创建。历史作品不受写稿阶段影响。

工作流随现有聊天任务在服务器线程运行；第一版助手执行期间仍一次处理一个请求，关闭面板不停止，停止按钮取消当前阶段和后续阶段。媒体后台任务保持原机制。使用启动脚本选取的 article-importer/.venv Python 环境（含 markdown-it-py）运行与验证。
