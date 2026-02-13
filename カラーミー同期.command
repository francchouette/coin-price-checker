#!/bin/bash
cd "$(dirname "$0")"

# .env から環境変数を読み込み
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

PYTHON="${PYTHON_PATH:-$(which python3)}"
"${PYTHON}" scripts/cm-sync-dashboard.py
