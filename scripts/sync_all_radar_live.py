#!/usr/bin/env python3
"""已停用：不再读写任何数据。推特雷达唯一入口是 scripts/refresh_twitter_radar.py。

历史职责（脚本 sync_all_radar_live）已由 scripts/radar_collect.py + scripts/radar_bundle.py + scripts/radar_store.py 承担：
状态分类、原始快照留档、原子发布与 manifest 切换点都在那里；旧脚本单独跑只会污染已清理的数据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from radar_retired import retire  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(retire("sync_all_radar_live.py"))
