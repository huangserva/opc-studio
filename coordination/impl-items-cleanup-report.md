# 素材库清理与 E2E 真实写入源头修复

| 决定要点 | 结果 |
|---|---|
| 结论 | 通过，交 B 独立复核；本报告不代替复核验收 |
| H/M/L | 自测 0H / 0M / 0L |
| 数据 | 39 个指定 ID 回收，跳过 0；总数 107，回收站 40，其余 68 行 SHA 不变 |
| 测试 | 默认 E2E 159 通过；专项 28 通过；去防护 5/5 报 F；helper 集成 7 通过；真实只读 5/5 |
| 文件与 SHA | 11 个测试文件，完整清单见下；[sha256.json](items-cleanup-evidence/sha256.json)，清单 SHA `903feca23e05a23768e8e8eccd424b2d01b935392f95a0ceaa55acf910f5d5bd` |
| 备份 | `/tmp/items-cleanup-20261030T071240Z/items.before.db`，SQLite 只读连接 backup API，含 WAL 一致视图 |
| 回滚 | 同目录 `rollback.json`，仅 39 个 ID 调用产品 restore，命令见下 |
| 部署与导出 | 未改产品代码、未重启 8766、未提交推送；正式导出文件 SHA 未变 |

任务 `task_1dc1233a6d12` / Dispatch `ctx_80d0b64c3292`。依据 `impl-items-cleanup-contract.md` 和 `check-items-test-data-report.md` 第 3、4 节。执行备份时间 2026-10-30 07:12:40 UTC；最终数据核对时间见 [final-audit.json](items-cleanup-evidence/final-audit.json)。含私人素材的数据库、全文副本与浏览器 profile 均只保存在本机 `/tmp`。

## 数据清理与逐项验证

| 合同项 | 结果与证据 |
|---|---|
| 调度检查 | 执行前 GET 调度：仅 `wechat_clues`（`5a81d117…`）运行；核对其受限分析执行入口不写 items，未取消任务。原始状态在备份目录 `schedules-before.json` |
| 备份与行级预检 | 一致 SQLite 备份、107 行原状态/完整行 SHA、正式导出副本、39 ID 回滚清单均保存；39/39 SHA 与 candidates 相等，差异 0 |
| 级联边界 | 父 card 级联预演没有 39 集合以外的子项；无越界回收。[precheck.json](items-cleanup-evidence/precheck.json) |
| 产品动作 | 逐项 POST 产品 `/api/items/<ID>/trash`；同批父项已级联回收的子项不重复覆盖时间戳。未执行 SQL UPDATE/DELETE |
| 最终边界 | 修改 ID 集合严格等于候选中「疑似测试且未回收」39 项；仅 `trashed_at` 改变；5 不确定、62 真实、原回收 1 条共 68 行完整 SHA 全部不变。总数 107，无新增。[final-audit.json](items-cleanup-evidence/final-audit.json) |
| 列表、搜索、回收站 | 我的素材 67，39 条全部隐藏；逐项标题查询 39 次无原 ID；回收站 40，包含本批 39。[browser-verification.json](items-cleanup-evidence/browser-verification.json) |
| H5 预演 | 导出脚本指定输出 `/tmp/.../export-preview`，候选 106→67；未触发产品导出任务 |
| 正式导出 | 全程保持 SHA `30e67c19b7750d548c3b8c36a3607eb0276391c40c106d88605591124b569ca4`；等待明早 08:30 定时导出 |
| 迁移与可恢复性 | `/tmp` 的 items 与旧库一致副本重跑迁移，39 条仍回收；随后仅在该副本 restore 39/39 成功。迁移副本总数 109：新增两个旧 articles 来源 ID `url:51aa516e0d64747d`、`url:22e4a68ec2840e9d`，未写正式库。正式库仍 107 |
| 真实 UI | 独立 headless Playwright profile，仅 GET 打开真实 8766 我的素材/回收站，未点恢复或其他写动作；脱敏截图 `/tmp/items-cleanup-20261030T071240Z/mine.png`、`trash.png` |

清理逐项 API/级联回执见 [cleanup-results.json](items-cleanup-evidence/cleanup-results.json)，迁移和预演见 [verify-data.json](items-cleanup-evidence/verify-data.json)。

## 防护设计与允许范围

五个脚本保留 `--real`，但在原模块和夹具初始化之前进入共用只读 GET 路径并返回；原流程 `REAL` 固定为 false，生产 start 分支删除。这样真实检查不需要「写后恢复」，也不会启动起草、收藏、反馈或兴趣保存动作。默认模式忽略外部 base/workspace 参数，只使用脚本自己创建的临时目录和随机端口。

共用 guard 对可写 origin 采用明确登记：临时目录先 realpath 校验，生产保留端口 8766/3000/18766 和额外测试生产 origin 拒绝登记。未登记 origin 的写请求直接失败。覆盖浏览器 context/page route（含 continue、fetch、fallback 改写）、context.request、独立 APIRequestContext、Node fetch/http/https；导航及 API 请求禁用自动重定向并在跟随前校验目标。Service Worker 关闭。另对正式 workspace 与 reports 的 Node 文件写入做 realpath 检查，包含符号链接、同步/异步写、open、流和重命名。

`knowledge_first`、`batch_delete` 在执行回调之前要求已登记的隔离 base；单独连接生产时即失败。父 `run.cjs` **仅 3 处接入变化**：安装 guard、断言临时 workspace、登记本轮 app/model 随机 origin；保留原系统临时目录约束，`server_fixture.py` 未改。五个主要脚本始终使用 `/tmp`。本任务差异见 [task-diff.patch](items-cleanup-evidence/task-diff.patch)。

## 测试与去防护反证

| 脚本 | 修改前默认通过 / 失败 | 修改后默认通过 / 失败 | 伪生产 `--real` | 去防护同组 F |
|---|---:|---:|---|---:|
| library_page | 3 / 1 | 31 / 0 | 通过，数据全部不变 | 1 |
| today_page | 56 / 0 | 56 / 0 | 通过，数据全部不变 | 1 |
| today_t5 | 22 / 0 | 22 / 0 | 通过，数据全部不变 | 1 |
| write_and_publish | 28 / 0 | 28 / 0 | 通过，数据全部不变 | 1 |
| interest_channel | 22 / 0 | 22 / 0 | 通过，数据全部不变 | 1 |

`library_page` 原基线在「五个旧页保留在旧版分组」失败：真实导航已是九个 legacy 入口和独立的两个工具入口。仅将测试改为核对九个准确 ID 的完整顺序及两个工具入口，未改产品导航。修改前日志保留，未把 3 项提前终止说成全绿。

`real_write_guard.test.cjs`：28 项通过。私有随机端口伪生产服务使用正式 items 的 SQLite backup 副本，同时复制作品目录、feedback、兴趣文件到 `/tmp`；每个脚本 `--real --base <origin>/extra/ --safety-probe` 分别走 context.request、Node fetch 和浏览器 fetch 的写探针，全部被拦并产生阻断日志，前后全行摘要与文件树 SHA 相等。探针需要私有夹具身份 nonce、临时 workspace 和非生产保留端口，真实 8766 不执行探针。

故意改坏的 guard 仅位于 `/tmp`（`ENABLED=true`→false），相同五份测试逐个产生 **1 个失败退出，共 F=5**；每个副本实际写入私有 items，且捕获到「3 个写通道逃逸」，不是仅靠异常文案判错。测试随后只重置私有副本。完整检查清单见 [guard-result.json](items-cleanup-evidence/guard-result.json)，各脚本 `*-guarded.log`、`*-mutant.log` 同目录。

| 绕过反例 | 结果 |
|---|---|
| 127.0.0.1、localhost、[::1]、0.0.0.0，分别 8766 与 3000，带路径/尾斜杠 | 8/8 拦截 |
| workspace 符号链接指回正式目录 | 拒绝登记；实际 Node fs 写同样拦截，未产生文件 |
| context.request 直接 POST | 拦截 |
| 独立 APIRequestContext 直接 POST | 拦截 |
| Node fetch、Node http 直接 POST | 2/2 拦截 |
| page.route continue 跨随机端口重定向 | 在生产落地前拦截，目标未收到浏览器请求 |
| page.route fallback 将 GET 改成生产 POST | 拦截 |
| 两个 helper 单独接入生产 base | 2/2 在执行任何回调前拒绝 |

父套件定向 11、12、15–19 共 7 项最终通过；16 的日期夹具仍使用系统临时目录约束。未跑并发全量 discover。默认五脚本的最终日志、父套件定向日志保存在证据目录；`parent-batch.log` 含中途临时目录不匹配的失败，最终 `parent-batch16.log` 已在保留原系统临时目录后通过，不隐去失败。

最终对真实 8766 执行五个 `--real`，5/5 exit=0 且明确输出「只读 GET」。前后 items 全 107 行 SHA、作品目录树、四处反馈文件、兴趣文件 SHA、正式导出 SHA 全部一致，无新增 ID，见 [final-real-result.json](items-cleanup-evidence/final-real-result.json)。

复跑命令（均无需重启）：

```sh
node tests/e2e/real_write_guard.test.cjs
node tests/e2e/library_page.cjs
node tests/e2e/today_page.cjs
node tests/e2e/today_t5.cjs
node tests/e2e/interest_channel.cjs
node tests/e2e/write_and_publish.cjs
E2E_ONLY=11,12,15,16,17,18,19 node tests/e2e/run.cjs
```

## 该工人已知弱点逐条自测

| 已知弱点 | 本次自测结果 |
|---|---|
| 输入/授权边界 | **通过**：精确39 ID集合相等，0跳过；其余68行完整SHA不变；仅trashed_at变化 |
| 整条链路与并发遗漏 | **通过**：调度执行入口核实、SQLite一致备份、级联预演、列表、39搜索、回收站、H5预演、迁移、私有副本restore、真实隔离UI、最终正式库107行一致性均有证据；无新增行 |
| 只读保护与继承风险 | **通过**：8个别名/端口组合、符号链接、浏览器route两条绕过、直接context.request、独立API请求、Node两通道、两个helper全部拦截；去防护五脚本均报F并证实私有库真实被写入 |

## 测试文件 SHA256

| 文件 | SHA256 |
|---|---|
| `tests/e2e/library_page.cjs` | `2f6ff97f62010be94f86ac074f5110ed46f4c73145b1593bdc3b73ea848842ad` |
| `tests/e2e/today_page.cjs` | `f6313b1087d3b122e4cc25e47784ea3ee636ca25ded7a75f9547986db484c5e3` |
| `tests/e2e/today_t5.cjs` | `fe8f208a840e5274088f2816a1f2ae50ec0947020fdfef534fd2d1793dccff3c` |
| `tests/e2e/write_and_publish.cjs` | `a6b57d40bfe8df6b860fd75fd14d91a58e76d5591dbf1362ab86afd4a9b37f19` |
| `tests/e2e/interest_channel.cjs` | `315ce49a14491e1e51d8d9f01584cfb5e9ef3cb94478429ec842825df9bf6464` |
| `tests/e2e/knowledge_first.cjs` | `7a4d4ca42f2101d4bcb491e2be55a44c0653f5806054c630fad98627a8ec2d98` |
| `tests/e2e/batch_delete.cjs` | `34b65ec075043c6d60534307acab8692987f80b26bcde9de7f729e8d2ee6ece7` |
| `tests/e2e/run.cjs` | `ccf81111d7f77ac81939ab5cc53906d50800cba40b5b29e1f5ccb67aee972220` |
| `tests/e2e/real_write_guard.cjs` | `7a64a0fc7c2cfc887e1a16110b63ee5716055d7582f692e4bfdadd0d571858c4` |
| `tests/e2e/real_write_guard.test.cjs` | `ab58659b26866c71181a2c71050772e8a24b911a6e009e45bd7b5ff1d65dab69` |
| `tests/e2e/real_write_guard_fixture.py` | `33b736ce547731cd3757b553233903a0661db484e16f63497fa0be7eb464458b` |

未编辑 H3a M2 的 `radar_common.py`、`today_filter_config.py`、`today_h3a_m2.cjs`；未改产品代码、调度、plist；未使用 computer use，未提交推送。

## 本机回滚（未在正式库执行）

仅需要恢复本批时运行以下命令；不覆盖数据库，不恢复整份备份，不改用户后续作品/字段：

```sh
../article-importer/.venv/bin/python - <<'RESTORE'
import json, urllib.parse, urllib.request
from pathlib import Path
p = Path('/tmp/items-cleanup-20261030T071240Z/rollback.json')
data = json.loads(p.read_text())
assert len(data['items']) == 39
for row in data['items']:
    assert row['trashed_at'] == ''
    url = data['base'] + '/api/items/' + urllib.parse.quote(row['id'], safe='') + '/restore'
    req = urllib.request.Request(url, data=b'{}', method='POST', headers={
        'Content-Type':'application/json', 'Origin':data['base'], 'X-Article-Studio':'1'})
    with urllib.request.urlopen(req) as reply:
        assert reply.status == 200
RESTORE
```

剩余步骤：B 按合同独立复核；本单未替主控更新整体台账或作最终验收决定。

## 本次回收 ID（39）

- `card:15b419e28242e90642b770d1a9335bff7e1a808fab1bd2dc934bf67ba14df2bc`
- `card:160720433b2efc2d526a79c00e38d1b78feb2c2b4d0e184ab918c9d643844a41`
- `card:1e19a8be961f2bb75fa86dde61259ead739817617cd06940864a258e9082f64f`
- `card:29516a8a23066d225ecc7e00f92d5bd08c2e2fe896ca80f77f59b8dd26de6629`
- `card:2e023f960a3e3fbf52311fe25c716591bfd73666c844fef19af8371010648b60`
- `card:4c0c8e6818ce5bef3e66971a0017151546844d2f0ed49d8cb520223702e6f874`
- `card:4ece508ef0c7e26f8832b1dff3f6cad6bebc03aa275bdee09941bfff7b90443f`
- `card:71b5268ffa279486873f4b66bda7738e2133097eebf92b374d646e0b20eec642`
- `card:730dc7c989fb0c609f74a1f8da7c067ede806b11b2bccb89d64473ae8ceda7bb`
- `card:85f1390ddff3b4f80a76d92929bd0fb71c70cac9598a3530c0919ce412e1e493`
- `card:a1b5688d9b938546f73fc75d6b4660f1ac4df89839d2760785103cf68fd229f7`
- `card:e052c0c0557e15d5eee7e9706c243422d72be9206425816ba245096c4eb3c241`
- `kb:145f653a161d4c0bd8ade131889a2564`
- `kb:244a84e4a487cd98ab10f865bf6b7026`
- `kb:38a1053dd8f1f5937bfb84c4fcd31c5e`
- `kb:5ddc5aaba66c4356698e037cf459b57c`
- `kb:6959c91091a727f83038b7da2d8dc870`
- `kb:85fedc2d3f5d7e232a883dd7b80ac252`
- `kb:87a22e6c8d6745e2bbbe11283d75b09d`
- `kb:b883badbbcf41d7d3cf0871112399239`
- `url:01e39e928694cd90`
- `url:25f3428972c9e142`
- `url:3554418dc37c54b7`
- `url:5011df1022c7dc5e`
- `url:5c9d71b0d5fb97d2`
- `url:66ea6dd9d56b03e6`
- `url:764d40b0e90c7d92`
- `url:7917590ddfc483ff`
- `url:914b32fb2e8de0db`
- `url:94cc083686f82e6f`
- `url:a91a5549eeae7c95`
- `url:b5be82428a91f9ec`
- `url:b7e249b600a85329`
- `url:bfc34abdee3fb9ef`
- `url:c02add554ed62b4c`
- `url:c6fc6c5ffdad15fa`
- `url:db540042d2ad37fb`
- `url:f5831450b5d5c13b`
- `url:f6388aae0220277d`


## 返修 1

| 决定要点 | 结果 |
|---|---|
| 结论 | 自测通过，提交 B 复核；原 M（O_TRUNC）与 L（操作留痕）已修 |
| H/M/L | 0H / 0M / 0L（本轮自测未发现遗留项） |
| 改动文件与 SHA256 | `tests/e2e/real_write_guard.cjs`：`01ecc1aa03a344beab4ea6c11d57ddae50a19acd2ca8b0189b1071038232cc64`；新增 `tests/e2e/real_write_guard_fs.test.cjs`：`00e9fb00181dff29a5adc30e5cb1bea1b8475602b816bc09efb9e22df57d3418` |
| 文件系统测试 | 140/140 修改反例 + 1/1 受保护标准输出反例；只读 4/4；去防护同组 F=141 |
| 原回归 | B 独立 27/27；O_TRUNC 三路及 helper 补充 5/5；共用网络防护 28/28、五入口去防护 F=5；默认 E2E 159/159、helper 7/7 |
| 两条 probe | 指定素材写 `trashed_at`；指定原始资料新增 `document_trash` 标记；其余业务行 SHA 不变 |
| 数据与恢复 | 素材 111→111、回收站 40→41；原始资料 302977→302977、已删除 1→2；真实页面只读 5/5，副本 UI 恢复 7/7、完整行恢复 2/2 |
| 备份与回滚 | `/tmp/items-probe-cleanup-20261030T085220Z`；备份执行原文、前后 SQLite 快照、原状态与 SHA、`rollback.py` 均已留存 |
| 范围 | 正式源码仅改 guard、新增其测试；五个 E2E、原网络测试及 H3a 文件 SHA 不变；未重启、未提交推送、未重跑导出 |

任务 `task_21d2e843d536` / Dispatch `ctx_3719e6d4cf39`。记录时间：2026-10-30T09:04:59.965344+00:00。Node `v22.22.1`；Python 使用 `../article-importer/.venv/bin/python`。本节只追加，不改原交付章节。

### 修复与 fs API 覆盖

数字写标志改为从 `fs.constants` 汇总 `O_WRONLY`、`O_RDWR`、`O_TRUNC`、`O_APPEND`、`O_CREAT`、`O_EXCL`，任一位命中即走写保护，不再使用硬编码访问位掩码。字符串识别全部带 `w`、`a`、`+` 的写模式；反例涵盖 `r+`、`rs+`、`w`、`wx`、`w+`、`wx+`、`a`、`ax`、`a+`、`ax+`、`as`、`as+`。

路径先 realpath 归一，同时检查词法路径；支持字符串、Buffer、file URL。记录 open 返回的 fd；未知的普通文件 fd 拒绝写入。标准输出若为普通文件，先以 dev/ino 检查是否属于保护目录，再允许日志；受保护 stdout 的继承反例也已验证。已有多链接普通文件还会检查 inode，阻断预先建立的硬链接逃逸（直接写入、数字 O_TRUNC、FileHandle 三路）。FileHandle 的修改方法统一检查 fd；同步 `fs` 与 `node:fs/promises` 共用保护，`syncBuiltinESMExports()` 同步内建 ESM 导出。五个入口已在最前面安装 guard，保持这个安装顺序。

以下同步、回调、promises 变体在当前 Node 存在时均接线；FileHandle 单列。反例未触及真实目录，统一在 `/tmp/e2e-fs-guard-NDGMTt/` 的仿正式 workspace 与假 HOME 执行。

| API | 覆盖方式 | 反例结果 |
|---|---|---|
| `open / openSync / promises.open` | 数字位与字符串模式在实际 open 前检查，成功后登记 fd | 48/48，含 O_TRUNC 三路 |
| `writeFile` | 路径、fd 或 FileHandle 检查 | 三种路径变体通过 |
| `appendFile` | 路径、fd 或 FileHandle 检查 | 三种路径变体通过 |
| `truncate` | 路径或 fd 检查 | 三种变体通过 |
| `copyFile` | 检查目标路径 | 三种变体通过 |
| `cp` | 检查目标路径 | 三种变体通过 |
| `rename` | 检查源和目标 | 三种变体通过 |
| `link` | 检查源和目标，防止硬链接输出保护文件 | 三种变体通过 |
| `symlink` | 检查新链接位置；经已有链接写入时 realpath 再检查 | 三种变体 + 链接逃逸通过 |
| `unlink` | 检查待删除路径 | 三种变体通过 |
| `rm` | 检查待删除路径 | 三种变体通过 |
| `rmdir` | 检查待删除目录 | 三种变体通过 |
| `mkdir` | 检查新目录路径 | 三种变体通过 |
| `mkdtemp` | 检查临时目录前缀位置 | 三种变体通过 |
| `utimes` | 检查路径 | 三种变体通过 |
| `lutimes` | 检查链接路径及其解析结果 | 三种变体通过 |
| `chmod` | 检查路径 | 三种变体通过 |
| `chown` | 检查路径 | 三种变体通过 |
| `lchown` | 检查链接路径及其解析结果 | 三种变体通过 |
| `lchmod` | 有此 API 时纳入路径包装 | 当前 Node 未提供，不适用 |
| `mkdtempDisposable` | 有此 API 时纳入路径包装；dispose 只删除已经验证的临时目录 | 当前 Node 未提供，不适用 |
| `fs.write` / `writeSync` | 检查 fd 对应路径，未知普通文件 fd 拒绝 | 同步与回调反例均通过 |
| `fs.writev` / `writevSync` | 检查 fd 对应路径，未知普通文件 fd 拒绝 | 同步与回调反例均通过 |
| `fs.ftruncate` / `ftruncateSync` | 检查 fd 对应路径，未知普通文件 fd 拒绝 | 同步与回调反例均通过 |
| `fs.fchmod` / `fchmodSync` | 检查 fd 对应路径，未知普通文件 fd 拒绝 | 同步与回调反例均通过 |
| `fs.fchown` / `fchownSync` | 检查 fd 对应路径，未知普通文件 fd 拒绝 | 同步与回调反例均通过 |
| `fs.futimes` / `futimesSync` | 检查 fd 对应路径，未知普通文件 fd 拒绝 | 同步与回调反例均通过 |
| `fs.fsync` / `fsyncSync` | 检查 fd 对应路径，未知普通文件 fd 拒绝 | 同步与回调反例均通过 |
| `fs.fdatasync` / `fdatasyncSync` | 检查 fd 对应路径，未知普通文件 fd 拒绝 | 同步与回调反例均通过 |
| `FileHandle.write` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.writev` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.writeFile` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.appendFile` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.truncate` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.chmod` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.chown` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.utimes` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.sync` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.datasync` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `FileHandle.createWriteStream` | 共享原型上的 fd 写检查，读取方式打开的保护文件同样拒绝修改 | 通过 |
| `fs.createWriteStream` / `WriteStream` | 检查路径或 options.fd，构造之前阻断 | 两种反例通过 |
| `fs.createReadStream` / `ReadStream` | 写模式 flags 也先检查，避免读流借写 flags 截断 | 两种反例通过 |
| 读 API、close / Symbol.asyncDispose、watch / unwatch、access / exists / stat / realpath / readdir、openAsBlob | 只读或释放资源，不是文件内容/目录修改 API；不施加写拦截 | `O_RDONLY` 与 `r` 的同步和 promises 读取 4/4；默认 E2E 正常 |

`fs/promises` 没有独立 write/writev/ftruncate/fchmod/fchown/futimes/fsync/fdatasync 接口，这些入口属于 FileHandle，已逐项覆盖。没有修改产品存储、服务、调度或系统配置。边界是五个 E2E 进程内的公共 fs API；入口必须先安装 guard，测试夹具的 Python 子进程继续使用私有工作区和既有网络隔离。

### 文件系统反例逐条结果

判定条件为“在修改之前被 E2E_GUARD 拒绝，并且文件内容及 mode/uid/gid/mtime/nlink/目录结构快照不变”；去防护同组没有拦截即判 F，不能拿 OS 自己报错冒充防护成功。每个反例独立目标文件，所有破坏仅发生在 /tmp 副本。

| 反例 | 防护开启 | 去掉防护 |
|---|---|---|
| `writeFile sync` | 通过（已拦，快照不变） | F |
| `writeFile callback` | 通过（已拦，快照不变） | F |
| `writeFile promise` | 通过（已拦，快照不变） | F |
| `appendFile sync` | 通过（已拦，快照不变） | F |
| `appendFile callback` | 通过（已拦，快照不变） | F |
| `appendFile promise` | 通过（已拦，快照不变） | F |
| `truncate sync` | 通过（已拦，快照不变） | F |
| `truncate callback` | 通过（已拦，快照不变） | F |
| `truncate promise` | 通过（已拦，快照不变） | F |
| `chmod sync` | 通过（已拦，快照不变） | F |
| `chmod callback` | 通过（已拦，快照不变） | F |
| `chmod promise` | 通过（已拦，快照不变） | F |
| `chown sync` | 通过（已拦，快照不变） | F |
| `chown callback` | 通过（已拦，快照不变） | F |
| `chown promise` | 通过（已拦，快照不变） | F |
| `utimes sync` | 通过（已拦，快照不变） | F |
| `utimes callback` | 通过（已拦，快照不变） | F |
| `utimes promise` | 通过（已拦，快照不变） | F |
| `lutimes sync` | 通过（已拦，快照不变） | F |
| `lutimes callback` | 通过（已拦，快照不变） | F |
| `lutimes promise` | 通过（已拦，快照不变） | F |
| `lchown sync` | 通过（已拦，快照不变） | F |
| `lchown callback` | 通过（已拦，快照不变） | F |
| `lchown promise` | 通过（已拦，快照不变） | F |
| `rename sync` | 通过（已拦，快照不变） | F |
| `rename callback` | 通过（已拦，快照不变） | F |
| `rename promise` | 通过（已拦，快照不变） | F |
| `copyFile sync` | 通过（已拦，快照不变） | F |
| `copyFile callback` | 通过（已拦，快照不变） | F |
| `copyFile promise` | 通过（已拦，快照不变） | F |
| `cp sync` | 通过（已拦，快照不变） | F |
| `cp callback` | 通过（已拦，快照不变） | F |
| `cp promise` | 通过（已拦，快照不变） | F |
| `link sync` | 通过（已拦，快照不变） | F |
| `link callback` | 通过（已拦，快照不变） | F |
| `link promise` | 通过（已拦，快照不变） | F |
| `symlink sync` | 通过（已拦，快照不变） | F |
| `symlink callback` | 通过（已拦，快照不变） | F |
| `symlink promise` | 通过（已拦，快照不变） | F |
| `mkdir sync` | 通过（已拦，快照不变） | F |
| `mkdir callback` | 通过（已拦，快照不变） | F |
| `mkdir promise` | 通过（已拦，快照不变） | F |
| `mkdtemp sync` | 通过（已拦，快照不变） | F |
| `mkdtemp callback` | 通过（已拦，快照不变） | F |
| `mkdtemp promise` | 通过（已拦，快照不变） | F |
| `unlink sync` | 通过（已拦，快照不变） | F |
| `unlink callback` | 通过（已拦，快照不变） | F |
| `unlink promise` | 通过（已拦，快照不变） | F |
| `rm sync` | 通过（已拦，快照不变） | F |
| `rm callback` | 通过（已拦，快照不变） | F |
| `rm promise` | 通过（已拦，快照不变） | F |
| `rmdir sync` | 通过（已拦，快照不变） | F |
| `rmdir callback` | 通过（已拦，快照不变） | F |
| `rmdir promise` | 通过（已拦，快照不变） | F |
| `open sync O_TRUNC` | 通过（已拦，快照不变） | F |
| `open sync O_APPEND` | 通过（已拦，快照不变） | F |
| `open sync O_CREAT` | 通过（已拦，快照不变） | F |
| `open sync O_EXCL` | 通过（已拦，快照不变） | F |
| `open sync r+` | 通过（已拦，快照不变） | F |
| `open sync rs+` | 通过（已拦，快照不变） | F |
| `open sync w` | 通过（已拦，快照不变） | F |
| `open sync wx` | 通过（已拦，快照不变） | F |
| `open sync w+` | 通过（已拦，快照不变） | F |
| `open sync wx+` | 通过（已拦，快照不变） | F |
| `open sync a` | 通过（已拦，快照不变） | F |
| `open sync ax` | 通过（已拦，快照不变） | F |
| `open sync a+` | 通过（已拦，快照不变） | F |
| `open sync ax+` | 通过（已拦，快照不变） | F |
| `open sync as` | 通过（已拦，快照不变） | F |
| `open sync as+` | 通过（已拦，快照不变） | F |
| `open callback O_TRUNC` | 通过（已拦，快照不变） | F |
| `open callback O_APPEND` | 通过（已拦，快照不变） | F |
| `open callback O_CREAT` | 通过（已拦，快照不变） | F |
| `open callback O_EXCL` | 通过（已拦，快照不变） | F |
| `open callback r+` | 通过（已拦，快照不变） | F |
| `open callback rs+` | 通过（已拦，快照不变） | F |
| `open callback w` | 通过（已拦，快照不变） | F |
| `open callback wx` | 通过（已拦，快照不变） | F |
| `open callback w+` | 通过（已拦，快照不变） | F |
| `open callback wx+` | 通过（已拦，快照不变） | F |
| `open callback a` | 通过（已拦，快照不变） | F |
| `open callback ax` | 通过（已拦，快照不变） | F |
| `open callback a+` | 通过（已拦，快照不变） | F |
| `open callback ax+` | 通过（已拦，快照不变） | F |
| `open callback as` | 通过（已拦，快照不变） | F |
| `open callback as+` | 通过（已拦，快照不变） | F |
| `open promise O_TRUNC` | 通过（已拦，快照不变） | F |
| `open promise O_APPEND` | 通过（已拦，快照不变） | F |
| `open promise O_CREAT` | 通过（已拦，快照不变） | F |
| `open promise O_EXCL` | 通过（已拦，快照不变） | F |
| `open promise r+` | 通过（已拦，快照不变） | F |
| `open promise rs+` | 通过（已拦，快照不变） | F |
| `open promise w` | 通过（已拦，快照不变） | F |
| `open promise wx` | 通过（已拦，快照不变） | F |
| `open promise w+` | 通过（已拦，快照不变） | F |
| `open promise wx+` | 通过（已拦，快照不变） | F |
| `open promise a` | 通过（已拦，快照不变） | F |
| `open promise ax` | 通过（已拦，快照不变） | F |
| `open promise a+` | 通过（已拦，快照不变） | F |
| `open promise ax+` | 通过（已拦，快照不变） | F |
| `open promise as` | 通过（已拦，快照不变） | F |
| `open promise as+` | 通过（已拦，快照不变） | F |
| `FileHandle.write` | 通过（已拦，快照不变） | F |
| `FileHandle.writev` | 通过（已拦，快照不变） | F |
| `FileHandle.writeFile` | 通过（已拦，快照不变） | F |
| `FileHandle.appendFile` | 通过（已拦，快照不变） | F |
| `FileHandle.truncate` | 通过（已拦，快照不变） | F |
| `FileHandle.chmod` | 通过（已拦，快照不变） | F |
| `FileHandle.chown` | 通过（已拦，快照不变） | F |
| `FileHandle.utimes` | 通过（已拦，快照不变） | F |
| `FileHandle.sync` | 通过（已拦，快照不变） | F |
| `FileHandle.datasync` | 通过（已拦，快照不变） | F |
| `FileHandle.createWriteStream` | 通过（已拦，快照不变） | F |
| `write sync` | 通过（已拦，快照不变） | F |
| `write callback` | 通过（已拦，快照不变） | F |
| `writev sync` | 通过（已拦，快照不变） | F |
| `writev callback` | 通过（已拦，快照不变） | F |
| `ftruncate sync` | 通过（已拦，快照不变） | F |
| `ftruncate callback` | 通过（已拦，快照不变） | F |
| `fchmod sync` | 通过（已拦，快照不变） | F |
| `fchmod callback` | 通过（已拦，快照不变） | F |
| `fchown sync` | 通过（已拦，快照不变） | F |
| `fchown callback` | 通过（已拦，快照不变） | F |
| `futimes sync` | 通过（已拦，快照不变） | F |
| `futimes callback` | 通过（已拦，快照不变） | F |
| `fsync sync` | 通过（已拦，快照不变） | F |
| `fsync callback` | 通过（已拦，快照不变） | F |
| `fdatasync sync` | 通过（已拦，快照不变） | F |
| `fdatasync callback` | 通过（已拦，快照不变） | F |
| `createWriteStream` | 通过（已拦，快照不变） | F |
| `WriteStream` | 通过（已拦，快照不变） | F |
| `createReadStream` | 通过（已拦，快照不变） | F |
| `ReadStream` | 通过（已拦，快照不变） | F |
| `symlink destination escape` | 通过（已拦，快照不变） | F |
| `Buffer path` | 通过（已拦，快照不变） | F |
| `URL path` | 通过（已拦，快照不变） | F |
| `unknown pre-install fd` | 通过（已拦，快照不变） | F |
| `existing hardlink write escape` | 通过（已拦，快照不变） | F |
| `existing hardlink O_TRUNC escape` | 通过（已拦，快照不变） | F |
| `existing hardlink FileHandle escape` | 通过（已拦，快照不变） | F |
| 继承 stdout 指向仿正式文件后 fs.writeSync(1, ...) | 通过（已拦，字节不变） | F |
| 同步 open(O_RDONLY)、open('r')；promises.open(O_RDONLY)、open('r') | 通过（正常读取原字节） | 正常读取，不计 F |

总计修改反例 141/141，去防护 F=141；独立只读 4/4。逐条 JSON：`coordination/items-cleanup-evidence/fix1/final-hardlink/fs-guarded.json`、`fs-disabled.json`、`stdio-guarded.json`、`stdio-disabled.json`；原始日志 `fs-suite-hardlink.log`，完整私有运行目录见日志。

### 原复核与正常 E2E

B 的 `independent-guard.cjs` 原样测试逻辑复用，只将证据根目录改为新的 /tmp 路径，防护开关仍为开启。结果如下。

| B 原独立反例 | 结果 |
|---|---|
| `alias check localhost:8766` | 通过 |
| `register alias localhost:8766` | 通过 |
| `alias check localhost:3000` | 通过 |
| `register alias localhost:3000` | 通过 |
| `alias check [::1]:8766` | 通过 |
| `register alias [::1]:8766` | 通过 |
| `alias check [::1]:3000` | 通过 |
| `register alias [::1]:3000` | 通过 |
| `alias check 0.0.0.0:8766` | 通过 |
| `register alias 0.0.0.0:8766` | 通过 |
| `alias check 0.0.0.0:3000` | 通过 |
| `register alias 0.0.0.0:3000` | 通过 |
| `alias check 127.0.0.1:8766` | 通过 |
| `register alias 127.0.0.1:8766` | 通过 |
| `alias check 127.0.0.1:3000` | 通过 |
| `register alias 127.0.0.1:3000` | 通过 |
| `symlink protected workspace is refused` | 通过 |
| `actual workspace symlink refused` | 通过 |
| `context.request direct post refused` | 通过 |
| `standalone request direct post refused` | 通过 |
| `Node fetch production write refused` | 通过 |
| `Node http production write refused` | 通过 |
| `page fetch production write refused` | 通过 |
| `307 cross-origin navigation refused before landing` | 通过 |
| `window.open redirect refused before landing` | 通过 |
| `page.route continue rewritten POST refused` | 通过 |
| `numeric O_RDONLY|O_TRUNC protected file refused` | 通过 |

补充 `fs-helper-probes.cjs` 的数字 O_TRUNC 同步/回调/promises 三路及两个 helper 单独接生产 base 共 5/5；共用网络反例 28/28，五个 E2E 入口去防护仍各 F=1，共 F=5。修改前数字来源为 B 原复核：独立 26/27（O_TRUNC 漏拦）、补充三路截断失败；本轮为 27/27 与 3/3。

| 默认脚本 / helper | 修改前（原交付、B 已复验） | 本轮最终源码 |
|---|---:|---:|
| library_page | 31 | 31 |
| today_page | 56 | 56 |
| today_t5 | 22 | 22 |
| interest_channel | 22 | 22 |
| write_and_publish | 28 | 28 |
| 五组合计 | 159 | 159 |
| helper：11、12、15、16、17、18、19 | 7 | 7 |

最终执行目录 `/tmp/items-fix1-runners-9e8w9lba/`，汇总 `fix1/suites-hardlink.log`，每组原始日志已复制至 `fix1/final-hardlink/`。没有正式脚本接线 diff：测试脚本只复制到 /tmp，将产品源码 ROOT 仍指向正式只读源码，将所有截图/输出路径改到全新 /tmp；fixture 的源码 import ROOT 同样修正到正式源码。断言正文不变。准备与运行命令全文在 `prepare-runners.py`、`run-suites.py`，每个中间失败与最终结果均分开留档，没有覆盖旧证据。

### 两库备份、动作和边界验证

执行前已核对两条均为 probe、相同指定链接；素材标签/备注 probe、作者账号 t；原始资料 provenance 作者 t、渠道“工作台收藏登记”、reader_note probe。没有级联子 card。开工状态和文件 SHA 保存于 `fix1/git-status-before.txt`、`sha-before.txt`。执行前运行的任务只有已核实不写 items 的 wechat_radar，未中断或重启它。

备份目录 `/tmp/items-probe-cleanup-20261030T085220Z` 权限 0700，`items.before.db`、`knowledge.before.db` 都由只读连接调用 SQLite backup API 生成；另有动作后的 `.after.db`。完整原记录、完整行 SHA 和全表 SHA 清单只留此 /tmp 目录，不写报告正文。备份原文 `/tmp/items-probe-cleanup-20261030T085220Z/probe-cleanup.py` 与本单 `fix1/probe-cleanup.py` 对应本次执行；准确调用：

```sh
../article-importer/.venv/bin/python coordination/items-cleanup-evidence/fix1/probe-cleanup.py
../article-importer/.venv/bin/python coordination/items-cleanup-evidence/fix1/verify-data.py
```

唯一实际生产写请求：

- `POST /api/items/url%3A3c953d1a497eb157/trash`，正文 `{}`。
- `POST /api/knowledge/trash`，`confirm:true`、`items:[{id:"6d7b145d038ef16e9acf4fa3dc21e7dd",expected_hash:<先核对的原文 hash>}]`；此前使用产品 preview 核实 ready 与 hash。

生产业务行变更 ID 集合恰为素材 `url:3c953d1a497eb157` 和原始资料标记 `6d7b145d038ef16e9acf4fa3dc21e7dd`。素材总数 111→111，回收站 40→41、活跃 71→70；其余 110 条素材 SHA 不变。原始资料 documents 共 302977 行完整 SHA 全部不变；document_trash 从 1→2，只有指定文档新增标记，原标记不变。素材除了 trashed_at 外全字段不变；未硬删，没有直接 UPDATE/DELETE 业务 SQL。

全表对照同时发现 `items_fts_data` 两个内部块和 `items_fts_idx` 一个内部行变化：`items_store.py` 的 `items_update` AFTER UPDATE 触发器对该素材执行 FTS delete/insert，即便只改 trashed_at 也会写这些派生索引。documents、其余业务表和 knowledge 全文索引均无变化，详见 `fix1/data-result.json` 与私有 `all-table-differences-private.json`。这不是额外素材记录改动。

执行脚本第一次全表清单读取遇到 WITHOUT ROWID 表、第二次遇到 BLOB，均停在备份/只读阶段；修正序列化后才执行生产动作。动作后的初版后置断言把 FTS 内部索引一并当成业务表而报错，未重复动作；随后独立 `verify-data.py` 对所有表及逐业务行核查，确认上述精确边界。相关失败日志与更正脚本均留在 fix1 中，不掩盖或改写历史执行记录。

导出文件从未重跑或改写，前后 SHA256 `30e67c19b7750d548c3b8c36a3607eb0276391c40c106d88605591124b569ca4`；备份里另存只读副本。

### 真实页面、恢复和回滚

真实 8766 使用新 /tmp 独立 profile、headless Playwright，仅允许同源 GET，5/5：素材搜索不含指定 ID，回收站 API 与卡片均有指定 ID；原始资料按外部文章检索不含指定 ID，已删除列表有该文档与恢复按钮。最终截图与日志见 `fix1/real-ui-result-r2.json`、`real-ui-r2.log`，截图目录由该 JSON 指向（私人列表均 mask）。

副本 `/tmp/items-probe-restore-5laewzco/workspace` 从动作后两库 SQLite 快照建立，只在随机端口运行，网络禁外部；真实浏览器先看已删除状态，再分别点击素材“恢复”和原始资料“确认恢复”。7/7 UI 断言通过；数据库核查两条完整原始行逐字段与备份一致 2/2。原始资料恢复后按 `kind=external&tag=probe&source=external-references` 精确检索成功。初次 UI 选择器误把详情动作当列表按钮，次次广泛检索首 30 条被文章/项目排序挤出目标；两次均只影响 /tmp 恢复预演，已改为详情按钮与限定来源检索，失败日志保留。最终 `fix1/restore-ui-r3.log`、`restore-result-r3.json`、`restore-proof-r3.log`。

仅在需要撤销本次回收时，由获授权操作者执行下面命令；本轮没有对真实库执行恢复：

```sh
../article-importer/.venv/bin/python /tmp/items-probe-cleanup-20261030T085220Z/rollback.py --base http://127.0.0.1:8766
```

回滚脚本只调用这两个指定 ID 的产品 restore API，解除素材 trashed_at 和原始资料隐藏标记，不覆盖后来新增或修改的数据，也不覆盖整库。动作脚本、恢复预演脚本、回滚脚本均保存在 `fix1/`；备份目录也保留备份原文与回滚脚本。

### 该工人已知弱点与自测

| 已知弱点 | 本轮逐条自测结果 |
|---|---|
| 只读保护与继承风险 | 通过：fs API 表与 141 条修改反例逐项给出，O_TRUNC 三路、追加/新建、r+、FileHandle.truncate、truncateSync、链接/既有硬链接逃逸、预先打开 fd、继承保护文件 stdout 均阻断；去防护 F=141；原网络与重定向 27/27、28/28 保持通过 |
| 整条链路遗漏 | 通过：素材库与原始资料分别有 SQLite 快照/逐行 SHA、真实列表搜索、回收站页面、随机端口副本 UI 恢复及完整原行恢复证据 |
| 输入/授权边界 | 通过：素材逻辑变更集合仅 1 个指定 ID，原始资料仅 1 个指定删除标记；无额外新增/业务行变化；其余行逐条 SHA 不变，FTS 触发器内部块变化单独披露 |
| 留痕与证据覆盖 | 通过：开工状态与 SHA、精确操作脚本留档；所有本轮截图均在全新 /tmp，日志分轮命名；未覆盖上轮 screenshots，未改五个正式 E2E、原网络测试或 H3a 文件 |

最终证据入口：`coordination/items-cleanup-evidence/fix1/`。报告本节之外不改协调台账；后续由 B 独立复核。
