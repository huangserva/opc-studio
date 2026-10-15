#!/bin/zsh
# Template: set ARTICLE_STUDIO_ROOT / ARTICLE_STUDIO_PYTHON before installation.
set -eu
umask 077
export TZ=Asia/Shanghai
TASK_ROOT="${ARTICLE_STUDIO_ROOT:-/Users/huangzongning/development/article-studio}"
TASK_PYTHON="${ARTICLE_STUDIO_PYTHON:-${TASK_ROOT}/../article-importer/.venv/bin/python}"
TASK_SERVICE_PLIST="${ARTICLE_STUDIO_SERVICE_PLIST:-${HOME}/Library/LaunchAgents/com.servasyy.article-studio.plist}"
# No tracing, no plaintext credentials, no child output containing API errors.
AISA_API_KEY="$(/usr/bin/plutil -extract EnvironmentVariables.AISA_API_KEY raw -o - "$TASK_SERVICE_PLIST")"
export AISA_API_KEY

load_optional_feishu_config() {
  local key="$1" kind value
  if [[ -n "${(P)key:-}" ]]; then
    export "$key"
    return 0
  fi
  # An inherited empty string is unconfigured, not an exported empty credential.
  unset "$key"
  if kind="$(/usr/bin/plutil -type "EnvironmentVariables.$key" "$TASK_SERVICE_PLIST" 2>/dev/null)"; then
    if [[ "$kind" != string ]]; then
      printf '%s\n' "radar-monitor: $key 配置类型不是字符串，已忽略" >&2
      return 0
    fi
    # Sentinel preserves trailing newlines; raw adds exactly one output newline.
    if value="$(/usr/bin/plutil -extract "EnvironmentVariables.$key" raw -o - "$TASK_SERVICE_PLIST" 2>/dev/null && printf '.')"; then
      value="${value%.}"
      value="${value%$'\n'}"
      if [[ -n "$value" ]]; then
        export "$key=$value"
      fi
    fi
  fi
  return 0
}
load_optional_feishu_config FEISHU_RADAR_WEBHOOK
load_optional_feishu_config FEISHU_RADAR_SECRET
cd "$TASK_ROOT"
exec "$TASK_PYTHON" scripts/radar_monitor.py --root "$TASK_ROOT"
