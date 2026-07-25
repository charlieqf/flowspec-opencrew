#!/usr/bin/env bash
# 40 - 克隆生产库到 TEST,并把 /Users/macmini-1 → /Users/macmini-4 路径重写
# 幂等:dump 带 --clean --if-exists,重复跑会覆盖刷新。
# 用法: 40_migrate_db.sh          增量刷新(保留已有库)
#       40_migrate_db.sh --fresh  先 dropdb 再重建
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
FRESH="${1:-}"
DUMP_PROD="/tmp/opencrew_prod.sql"
DUMP_TEST="/tmp/opencrew_test.sql"

log "从生产导出纯 SQL(不影响线上)…"
PGPASSWORD="$DB_PASS" pg_dump -h 127.0.0.1 -p "$PG_PORT" -U "$DB_USER" -d "$DB_NAME" \
  --clean --if-exists -Fp -f "$DUMP_PROD"

log "路径重写 $PATH_OLD → $PATH_NEW …"
sed "s#${PATH_OLD}#${PATH_NEW}#g" "$DUMP_PROD" > "$DUMP_TEST"

log "传输到 TEST …"
scp -q "$DUMP_TEST" "$TEST_RSYNC_HOST:/tmp/"

log "TEST:确保 PG16 集群 + 角色/库,然后恢复…"
rt <<EOF
set -e
export PGPASSWORD="$DB_PASS"
if [ ! -f "$TEST_DATA/postgres/PG_VERSION" ]; then initdb -D "$TEST_DATA/postgres"; fi
pg_ctl -D "$TEST_DATA/postgres" -o "-p $PG_PORT" -l /tmp/opencrew-pg.log start 2>/dev/null \
  || pg_ctl -D "$TEST_DATA/postgres" status >/dev/null
sleep 2
psql -p $PG_PORT -d postgres -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 \
  || createuser -p $PG_PORT -s "$DB_USER"
psql -p $PG_PORT -d postgres -c "ALTER USER $DB_USER PASSWORD '$DB_PASS';" >/dev/null
if [ "$FRESH" = "--fresh" ]; then
  psql -p $PG_PORT -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;" >/dev/null
fi
psql -p $PG_PORT -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
  || createdb -p $PG_PORT -O "$DB_USER" "$DB_NAME"
psql -p $PG_PORT -U "$DB_USER" -d "$DB_NAME" -f /tmp/opencrew_test.sql >/dev/null
echo "恢复完成。残留旧路径行数(应为0): \$(psql -p $PG_PORT -U $DB_USER -d $DB_NAME -tAc "SELECT count(*) FROM sessions WHERE workspace_dir LIKE '${PATH_OLD}%'")"
EOF
log "数据库迁移完成。"
