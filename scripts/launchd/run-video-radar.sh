#!/bin/zsh
# 只交模板；安装由协调者后续处理。
set -eu
umask 077
export TZ=Asia/Shanghai
TASK_ROOT="${ARTICLE_STUDIO_ROOT:-/Users/huangzongning/development/article-studio}"
TASK_PYTHON="${ARTICLE_STUDIO_PYTHON:-${TASK_ROOT}/../article-importer/.venv/bin/python}"
TASK_SERVICE_PLIST="${ARTICLE_STUDIO_SERVICE_PLIST:-${HOME}/Library/LaunchAgents/com.servasyy.article-studio.plist}"
AISA_API_KEY="$(/usr/bin/plutil -extract EnvironmentVariables.AISA_API_KEY raw -o - "$TASK_SERVICE_PLIST")"
export AISA_API_KEY
cd "$TASK_ROOT"
exec "$TASK_PYTHON" scripts/video_radar.py --root "$TASK_ROOT" --mode auto
