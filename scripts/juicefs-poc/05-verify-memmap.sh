#!/usr/bin/env bash
# 跑 verify_memmap.py。需要 ops 项目的 venv(numpy 装在那里)。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

OPS_ROOT="${OPS_ROOT:-/home/wbai/gsim-ops}"

if [[ ! -d "$OPS_ROOT" ]]; then
  echo "error: OPS_ROOT=$OPS_ROOT not found" >&2; exit 1
fi

echo "running verify_memmap.py via uv (project=$OPS_ROOT)..."
uv run --project "$OPS_ROOT" python verify_memmap.py
