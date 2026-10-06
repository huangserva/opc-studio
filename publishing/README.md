# 发布后端

所有发布准备、平台路由、导出、发布记录、X 文章草稿流程统一放在这里。工作台和 AI 助手调用统一服务；平台差异写在各自适配器里。

```text
publishing/
├── service.py                 # 固定版本 → 发布计划 / ZIP / 发布记录
├── registry.py                # 平台 × 作品类型路由与能力状态
├── __main__.py                # 统一本地命令入口
├── core/
│   ├── article_package.py     # JSON / Markdown / Word → 文章包、检查与对比
│   └── common.py              # 文件、时间、摘要等共用处理
├── transports/
│   └── buffer_cli.py          # 官方 Buffer CLI 本地 dry-run 校验
└── platforms/
    ├── x/
    │   ├── post.py            # 普通推文 / 串文 → Buffer 请求
    │   ├── gallery.py         # X 图片帖 → Buffer 请求
    │   ├── video.py           # X 视频帖 → Buffer 请求
    │   ├── article.py         # 长文章 → 浏览器草稿任务交接
    │   ├── article_workflow.py# 草稿绑定、恢复计划、证据核验
    │   └── editor_helpers.mjs # X 编辑器 DOM 辅助函数
    ├── xiaohongshu/           # 小红书独立适配入口
    ├── douyin/                # 抖音独立适配入口
    ├── channels/              # 视频号独立适配入口
    └── manual.py              # 尚未接通平台共用本地导出流程
```

## 当前接入范围

| 平台与类型 | 路径 | 当前状态 |
|---|---|---|
| X 普通推文、串文、图片帖、视频帖 | Buffer CLI | 请求转换、本地校验；尚未连接 Buffer 账号和远程提交 |
| X 长文章 | 本机 Chrome 扩展 / article-importer | 上传前提示并拦截已确认的图片超限，支持图文草稿上传及续传，保存重载核验后由用户确认发布 |
| 小红书 | 独立适配目录 | 导出包、手动登记；在线适配待接 |
| 抖音 | 独立适配目录 | 导出包、手动登记；在线适配待接 |
| 视频号 | 独立适配目录 | 导出包、手动登记；在线适配待接 |

抖音没有映射为 TikTok；长文章没有降级成普通推文。其他平台即使使用同一种视频文件，也需要独立的账号、素材和文案规则。

## 统一入口

在 `article-studio` 目录运行，Python 沿用 `../article-importer/.venv/bin/python`。

```bash
python -m publishing capabilities
python -m publishing plan --slug <作品ID> --version v0001 --platform x
python -m publishing plan --slug <作品ID> --version v0001 --platform x \
  --channel-id <Buffer的X渠道ID> --media-urls <URL映射.json> \
  --buffer-input-out <新请求.json>
python -m publishing buffer-check --input <新请求.json>
python -m publishing article check <文章包目录>
```

所有这些命令只做本地准备和校验，不会新建在线草稿或公开发布。生成的 Buffer 请求固定 `saveToDraft: true`；真正提交还需要接通目标 Buffer 账号并核对 X 渠道。当前没有远程执行接口，不把 dry-run 当作已提交。

Buffer 的 `media_urls` 将快照内的 `images/...` / `media/...` 文件名映射到公开 HTTPS 地址。系统不会擅自把本机素材上传到第三方图床。只做 URL 格式检查，不承诺远端可访问；实际抓取和平台限制由 Buffer 验证。

串文按照正文文本块保留顺序，图片属于前一条，`metadata.twitter.thread` 包括首条。有单条平台文案覆盖多条串文、有独立封面或无法转换的素材时给出阻塞原因，避免静默丢内容。

HTTP 入口：

- `GET /api/publishing/capabilities`：平台与类型能力。
- `POST /api/publication-plan/<slug>`：固定 `version`、`platform` 和可选 `options` 生成计划。
- 原 `POST /api/export/<slug>`、`POST /api/record/<slug>` 仍通过统一服务处理。
- 前端「平台准备 → 检查发布方式」读取相同发布计划。ZIP 的 publish.json 也包含该计划。

## 兼容与扩展

原 `article-importer/scripts/article_importer.py` 与 `x_editor_helpers.mjs` 保留为兼容入口，真正实现已迁至本目录。以后维护本目录中的实现；不要编辑兼容入口另造一份逻辑。`article-importer` 和 `article-studio` 必须保持同级目录，Python 环境暂仍复用 importer。

新增平台：增加 `platforms/<平台>/`，实现 `capability(kind)` 与 `prepare(snapshot, options)`，然后在 registry 注册。平台有不同内容流程时继续拆分 `post.py`、`article.py`、`video.py` 等；共用的 CLI/API 连接放 transports。新增远程提交时再实现明确的任务状态、固定版本、目标账号核对、幂等与超时对账，不能自动重试未知结果的公开发布。

官方参考：[Buffer CLI](https://developers.buffer.com/guides/cli.html)、[串文结构](https://developers.buffer.com/examples/create-threaded-post.html)、[草稿创建](https://developers.buffer.com/examples/create-draft-post.html)。

本次发现 npm 上 `@bufferapp/cli@1.2.2` 与 `1.2.1` 的依赖含未展开的 `catalog:`，当前 npm 无法安装。未发现全局 buffer 命令时，本地校验固定使用 `npx --yes @bufferapp/cli@0.5.2`，避免跟随损坏的最新版；后续升级需重新核对 schema。没有写入 Buffer 登录凭据。

## 微信公众号

`wechat` 为微信公众号图文入口，与 `channels`（视频号）分开。按已安装的 `wechat-article-formatter` 规范输出段落、标题、引用和表格内联样式，保留指定版本的图文顺序，不再根据章节重复插图。平台标题用于公众号标题；平台文案作为摘要，正文始终沿用作品内容。

「平台准备 → 微信公众号 → 公众号排版预览」读取固定版本。发布包额外包含 `wechat.html`（内嵌配图、可离线打开）及公众号使用说明；原图片、封面也包含在包中。复制后必须在公众号编辑器上传并核验图片，封面单独设置。`GET /api/wechat-preview/<slug>?version=v0001` 只读，不生成导出或发布记录。

当前实际找到的是排版 skill，未找到其提到的 `wechat-publisher`。能力状态为 `formatter_ready`，不表示在线草稿或公开发布已接通。视频作品会提示使用图文作品或视频号。公众号已发布链接仅允许 `mp.weixin.qq.com` 域名。

## 本机 Chrome 发布入口（开发联调版）

平台准备 → X → 本机 X 发布。独立本机扩展源码位于 `publishing/local-extension`，
在 Chrome 的 `chrome://extensions` 中加载该已解压目录后，刷新工作台和 X 页面。
该入口复用本机 Chrome 的登录，不读取 Cookie、密码、个人资料或私有接口。
不依赖另一台电脑、Buffer 或 Codex 当前对话持续运行。

本地 API：

- `POST /api/local-publishing/start/<slug>`：固定已保存版本与目标账号，重复点击返回同一任务。
- `GET /api/local-publishing/jobs?slug=...&version=...`：任务历史与上传进度。
- `GET /api/local-publishing/bundle/<id>`：固定版本的正文与配图，仅本机页面交给扩展。
- `POST /api/local-publishing/job/<id>`：绑定草稿、上传检查点、预览确认和发布状态。
- `GET /api/local-publishing/preflight?slug=...&version=...&account=...`：根据该账号实际观察到的 X 限制检查配图数量。
- `POST /api/local-publishing/adapt/<slug>`：保留的历史分篇接口，上传页面不再提供入口；不能用于实现一篇完整文章的目标。

当前范围为 X 长文章。普通推文、线程和视频仍保留原准备/导出流程，不能声称已接通发送。
任务落盘到 `workspace/publishing/jobs`；已绑定草稿不更换，公开发布请求先标记尝试，
缺少实际链接时进入结果待核验，禁止自动重复点击发布。

扩展 0.1.33 在上传前显示原稿图片数、该账号已实测单篇上限和超出数量，前后端均拦截已确认超限的上传与继续操作。未验证的账号明确提示，其他账号的实测值仅作为带来源的参考。遇到 X 首次媒体超限报错立即停止，不自动拆篇或省略图片。
此前 0.1.32 的真实 X 草稿验证覆盖独立四图文章、新建五图长文，以及封面加 24 张正文配图的长文，包含连续配图、首尾位置及富文本正文。这些分篇记录不代表原长文作为一篇完整文章上传成功。
标题、正文、封面和正文配图均由插件提交，重新加载后检查段落、图片身份、顺序和位置；
通过后进入“等待预览确认”，仍需用户查看原生完整预览。公开发布测试与草稿上传验收分开。
已有历史配图没有本任务身份记录时停止核对；控件无法唯一识别、登录验证或实际媒体限制会保留现场并提示原因。
操作、恢复、超限提示及验证范围见 [本机扩展说明](local-extension/README.md)。
