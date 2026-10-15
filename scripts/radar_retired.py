#!/usr/bin/env python3
"""已停用脚本的统一出口：只打印指引并以非 0 退出，不读写任何数据。"""
import sys

MESSAGE = "已停用，请用 scripts/refresh_twitter_radar.py（推特雷达唯一入口）"


def retire(name: str) -> int:
    print(f"{name}: {MESSAGE}")
    return 2


if __name__ == "__main__":
    raise SystemExit(retire("radar_retired"))
