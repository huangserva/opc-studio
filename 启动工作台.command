#!/bin/bash
set -euo pipefail
studio_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
studio_python="$studio_dir/../article-importer/.venv/bin/python"
if [[ ! -x "$studio_python" ]]; then
  studio_python="${ARTICLE_IMPORTER_PYTHON:-python3}"
fi
if "$studio_python" -c 'from urllib.request import urlopen; import json; d=json.load(urlopen("http://127.0.0.1:8766/api/library",timeout=1)); assert "workspace" in d and "articles" in d' >/dev/null 2>&1; then
  open 'http://127.0.0.1:8766'
  exit 0
fi
studio_node="${ARTICLE_STUDIO_NODE:-$(command -v node || true)}"
if [[ -z "$studio_node" ]]; then
  echo '助手需要 Node.js 22.19 或更新版本。安装后运行“安装助手运行环境.command”。'
  exit 1
fi
export ARTICLE_STUDIO_NODE="$studio_node"
if ! "$studio_node" "$studio_dir/pi-runtime/prepare.mjs"; then
  echo '请先运行“安装助手运行环境.command”，然后再打开工作台。'
  exit 1
fi
"$studio_python" "$studio_dir/server.py" --port 8766 --seed "$studio_dir/../x-article-resume/package" &
studio_pid=$!
trap 'kill "$studio_pid" 2>/dev/null || true' EXIT INT TERM
for studio_attempt in {1..40}; do
  if "$studio_python" -c 'from urllib.request import urlopen; urlopen("http://127.0.0.1:8766/api/library",timeout=1)' >/dev/null 2>&1; then
    open 'http://127.0.0.1:8766'
    wait "$studio_pid"
    exit 0
  fi
  sleep 0.15
done
echo '工作台未启动，请查看上面的错误。'
exit 1
