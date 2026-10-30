# 雷达监控离线回归夹具

原 `/tmp/viral-b1c-deliver/evidence/live/round-1-*.json` 已于重启时丢失，不冒充找回的原件。本夹具于本次任务从正式库只读导出真实采集记录，未访问网络、未改正式数据。

- 原始快照：`workspace/twitter-radar/snapshots/2026-10-16/20261016-030005-0bcf942d.json`，SHA256 `e8f367740a31b90c922bbec36c8a557e1a5378c655906b6f3fe0a37cc7d7c337`。
- 原始公开包：`workspace/twitter-radar/bundles/20261016-030005-0bcf942d.json`，SHA256 `dc4dd4e15319f21b1d84369c7a6189df80e7d764c331e107ce9378adf8fffb40`。
- 测试消费的轮次元数据原值保留（51/51，54次）；账号只保留测试实际消费的7个原字段；仅对前4个账号保留首条作者身份被观察且匹配查询账号的真实帖子（4条），用于empty/partial/timeout/not_started矩阵，保留测试消费的17个字段。所有值逐项来自上述快照，没有补写账号、正文、指标或时间。
- bundle只保留真实run_id与manifest。测试save()会按裁剪后快照字节重新计算SHA，原始SHA仍可在本文件核对；重算摘要属于夹具封装，不把摘要当历史证据。
- `radar_page_api_419dbc2.py` 为真实Git提交419dbc2中的完整源码，SHA256 `4c2b1e52baf5c6f1040f66913bff1da9dc39f3c4a46a05d6cbf7b7817b0ff9ca`。旧B2-b/B2-b2未入库交付已丢失，不能伪造同名文件；双端回归改为该可追溯已合入版本与当前正式模块，原数值、只读及投影一致性断言全部保留。

不存在凭据或服务配置；真实帖子为公开内容。运行时只读tests内夹具，不读取workspace或历史/tmp目录。
