#!/usr/bin/env bash
# ops PG 逻辑备份 — pg_dump, 跨版本/跨机安全
# 用法: ./backup.sh [输出目录]  (缺省 ./dumps)
# 恢复: gunzip -c ops-YYYYmmdd-HHMM.sql.gz | docker exec -i ops-pg psql -U ops -d ops
set -euo pipefail

OUT_DIR="${1:-$(dirname "$0")/dumps}"
mkdir -p "$OUT_DIR"
STAMP=$(date +%Y%m%d-%H%M)
OUT="$OUT_DIR/ops-$STAMP.sql.gz"

docker exec ops-pg pg_dump -U ops -d ops --clean --if-exists | gzip > "$OUT"
echo "dumped -> $OUT ($(du -h "$OUT" | cut -f1))"

# 保留最近 14 份
ls -1t "$OUT_DIR"/ops-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
