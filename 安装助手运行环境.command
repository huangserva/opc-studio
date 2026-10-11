#!/bin/bash
set -euo pipefail
studio_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
studio_node="${ARTICLE_STUDIO_NODE:-$(command -v node || true)}"
if [[ -z "$studio_node" ]]; then
  echo '请先安装 Node.js 22.19 或更新版本。'
  exit 1
fi
"$studio_node" -e 'const [a,b]=process.versions.node.split(".").map(Number);if(a<22||(a===22&&b<19))process.exit(1)' || {
  echo '需要 Node.js 22.19 或更新版本。'; exit 1;
}
cd "$studio_dir/pi-runtime"
npm ci --ignore-scripts
"$studio_node" prepare.mjs
echo '助手运行环境已就绪，打开“启动内容工作台.command”即可。'
