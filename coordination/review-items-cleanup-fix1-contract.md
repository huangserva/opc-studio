# 素材库清理返修 1 复核（复核窗口 B term_57dd3162）

对象：实施 A task_21d2e843d536，合同 coordination/impl-items-cleanup-fix1-contract.md，报告 coordination/impl-items-cleanup-report.md 末尾「返修 1」节，备份 /tmp/items-probe-cleanup-20261030T085220Z/。你上一轮的复核报告 review-items-cleanup-report.md 与证据 /tmp/review-items-cleanup/ 可直接复用。实施自评 0H/0M/0L。

## 必查
1. SHA：`tests/e2e/real_write_guard.cjs`、新增 `tests/e2e/real_write_guard_fs.test.cjs` 与报告一致；5 个 E2E 脚本、两个 helper、run.cjs/server_fixture.py、H3a 文件 SHA 未变（对照你上一轮记录）。
2. 原 M：你上一轮的 27 项独立反例在新版本上 27/27 通过；O_TRUNC 同步/回调/Promise 三路被拒且文件字节不变。
3. 覆盖完整性（重点）：不看 A 的表，自己逐个列 Node `fs`、`fs/promises`、`FileHandle` 里会改文件系统的 API，与 A 的覆盖表对照，找遗漏。至少自造 10 个 A 没列的反例（例如 cpSync/cp 目标、writev、rmSync 递归、mkdtemp/mkdir 到受保护目录、renameSync 移入或移出、createWriteStream 带 `r+`、FileHandle.writeFile/appendFile、utimes/chmod、经硬链接或符号链接指回受保护目录、相对路径与 `..` 绕回），逐条通过/失败。child_process 间接写（如 `sh -c '> file'`）若防护不覆盖，查这 5 个脚本和 helper 是否有这类调用：没有则记观察，有则记 M。
4. 误拦：只读 open（O_RDONLY、`r`）、/tmp 副本写入照常放行；默认 E2E 159、helper 7 独立重跑全过。去掉防护后同组反例报 F。
5. probe 两条：素材库 `url:3c953d1a497eb157` 在回收站（主控抽检 trashed_at=08:52:58Z、回收站 41）；原始资料 `6d7b145d038ef16e9acf4fa3dc21e7dd` 的 `document_trash` 标记——**核对这是产品既有的删除/回收机制（给出代码位置和调用的接口），不是 A 新造的表或直接 INSERT**；两库其余行不变（期间正常新增要能归因）；在 /tmp 副本上用 A 的回滚命令恢复，两条完整行与原状态一致。隔离 Playwright 只读看真实素材库与原始资料页面不再出现该条。
6. 留痕：证据里有开工 git status、相关文件 SHA、备份命令原文；没有覆盖既有证据文件（对照 git status 与目录 mtime）。

## 约束
只读复核，不改正式文件和数据，不点真实页面的写操作；隔离 Playwright，禁止 computer use；禁止 launchctl print / ps eww / env / 打印或检索输出 plist；证据一律输出到 /tmp/review-items-cleanup-fix1/，跑任何会落证据的脚本前先改输出目录；不重启 8766（期间 A 可能为 H3a M4 经主控批准重启一次，真实页面检查遇到重启就稍后重试）；Python 用 `../article-importer/.venv/bin/python`；不提交不推送。

## 交付
review-items-cleanup-report.md 末尾追加「返修 1 复核」节，决定要点表（≤10 行：结论、H/M/L、SHA、测试数字、证据路径），完成后 worker_done。
