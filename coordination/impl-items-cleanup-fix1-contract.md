# 素材库清理单返修（实施窗口 A term_02017284）

依据：复核 B task_ca3082ceeccc 不通过 0H/1M/1L（coordination/review-items-cleanup-report.md，证据 /tmp/review-items-cleanup/），黄总 10-30 16:2x 关于 probe 条目的决定。原合同 coordination/impl-items-cleanup-contract.md 的约束全部继续有效。复核仍派 B（term_57dd3162）。

## 一、M：防护漏拦数字 O_TRUNC（必须修）
- 位置：`tests/e2e/real_write_guard.cjs:33–34`，数字 flags 只看 `(flags&3)!==0`。`fs.openSync(f, O_RDONLY|O_TRUNC)`、回调 `fs.open`、`fs.promises.open` 三路都能把受保护文件截断为 0。
- 修法要求：数字 flags 只要带任一会改文件或目录的位（O_WRONLY、O_RDWR、O_TRUNC、O_APPEND、O_CREAT、O_EXCL 等，用 `fs.constants` 取值，不写死数字）就按写处理；字符串 flags 覆盖 `r+`、`rs+`、`w`、`wx`、`w+`、`a`、`ax`、`a+` 等全部写模式。
- 整条链路：不只修 open。逐个列出 Node `fs` / `fs/promises` / `FileHandle` 里会改文件系统的 API（writeFile、appendFile、truncate、copyFile/cp 的目标、rename、unlink、rm、rmdir、mkdir、symlink、link、utimes、chmod、chown、createWriteStream 等），在报告里给一张表：是否被防护覆盖、怎么覆盖、反例结果。没覆盖的要补，确实不需要的写明理由。
- 反例（必须真跑，只在 /tmp 仿正式目录里跑）：O_TRUNC 三路、O_APPEND、O_CREAT 新建文件、字符串 `r+`、FileHandle.truncate、fs.truncateSync、通过符号链接指回受保护目录，各至少 1 条，全部被拦且文件字节不变；只读 open（O_RDONLY、`r`）照常放行。去掉防护后同一组反例必须报 F（给 F 的数量）。B 上一轮 27 项独立反例在新版本上应 27/27 通过。
- 正常模式：默认 E2E 159 项、helper 7 项照常通过，给修改前后数字。

## 二、probe 条目两库移入回收站（黄总定）
- 对象：素材库 `url:3c953d1a497eb157`（标题/标签/备注 probe，链接 https://example.com/probe-1606，作者 t，渠道「工作台收藏登记」）；原始资料（knowledge）文档 `6d7b145d038ef16e9acf4fa3dc21e7dd`。来源已查明：Grok Bot 16:06 起势快报运行时为验证接口写入（它已承认），与复核脚本无关。
- 先核对两条记录的字段与上面一致，不一致就停下问主控。
- 备份：SQLite backup API 快照存 /tmp/items-probe-cleanup-<UTC>/，**备份所用脚本或命令原文一并存进该目录和本单证据**；记录两条的完整原状态和行 SHA。
- 执行：走产品现有的回收动作（素材库用 trash 语义；原始资料若有对应回收/软删动作就用它）。**原始资料没有可恢复的回收动作时，停下问主控，不得硬删、不得直接 UPDATE/DELETE。**
- 验证：两库其余行 SHA 不变（期间正常新增要能归因）；隔离 Playwright 只读看素材库与原始资料页面不再出现该条、回收站里能看到；写清回滚命令，在 /tmp 副本上验证可恢复。

## 三、L：留痕（本单起执行）
- 开工时记录 `git status --short` 和本单相关文件 SHA，存进证据。
- 所有数据操作的命令或脚本原文存进证据目录。
- 跑任何会落证据的脚本前，把输出目录指到 /tmp 或本单证据目录 `coordination/items-cleanup-evidence/fix1/`，不得覆盖既有证据文件（上一轮复核曾覆盖 impl-write-p3-screenshots/isolated 下 6 个未跟踪文件）。

## 允许改动
- 文件：`tests/e2e/real_write_guard.cjs` 及其测试；确有必要时 5 个 E2E 脚本和两个 helper 的接线（报告单列 diff）；报告和证据。
- 数据：只对上面两条 probe 记录执行回收动作。
- 不碰：H3a 的 `scripts/radar_common.py`、`today_filter_config.py`、today 相关测试（M3 在复核）；任何产品代码、调度、plist；不重启 8766。

## 约束
隔离 Playwright（独立 profile、/tmp 副本或随机端口），禁止 computer use；禁止 launchctl print / ps eww / env / 打印 plist；报告不出现 Key 和私人素材正文；跨版本对照只在 /tmp 副本做；Python 用 `../article-importer/.venv/bin/python`；不提交不推送。

## 该工人已知弱点与自测（skill worker-dispatch-profiles：Codex 历史实施）
- **只读保护与继承风险**（本单上一轮正是漏了 O_TRUNC；更早 X 重定向漏拦、AIsa 继承旧鉴权头）：自测＝第一部分的 fs API 覆盖表 + 全部反例逐条「通过/失败」+ 去防护报 F 证据。
- **整条链路遗漏**：不能只修 open 一个入口，也不能只删素材库不管原始资料。自测＝两库各自的数据库、列表页、回收站、恢复预演各给证据。
- **输入/授权边界**：只动两条 probe 记录。自测＝执行前后列出被改动行 ID 集合，恰为这两条。

## 交付
在 `coordination/impl-items-cleanup-report.md` 末尾追加「返修 1」节，开头决定要点表（≤10 行：结论、H/M/L、改动文件与 SHA、测试数字、两条记录处理结果、备份与回滚路径），然后逐条自测结果。完成后 worker_done。
