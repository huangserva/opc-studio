#!/bin/sh
# Only local health reads; no credentials or service management commands.
set -eu
umask 077
TASK_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
TASK_PYTHON="$TASK_ROOT/../article-importer/.venv/bin/python"
exec "$TASK_PYTHON" "$TASK_ROOT/scripts/studio_watchdog.py" "$@"
