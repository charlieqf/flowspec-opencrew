#!/usr/bin/env bash
# 60 - 起/重启 OpenCrew 整栈(委托 opencrew_local_stack.sh,自动建 venv/npm/codesign)
# 用法: 60_stack.sh [restart|start|status|stop|doctor]  默认 restart
# 【日常重启】常用:60_stack.sh restart
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
ACTION="${1:-restart}"
REBUILD="${2:-}"   # 传 --rebuild 强制重建 venv/node_modules

log "TEST 整栈 $ACTION …"
rt <<EOF
set -e
cd "$TEST_REPO"
if [ "$REBUILD" = "--rebuild" ]; then rm -rf backend/.venv frontend/node_modules; fi
OPENCREW_DATA_DIR="$TEST_DATA" \
DATABASE_URL="postgresql+psycopg://$DB_USER:$DB_PASS@127.0.0.1:$PG_PORT/$DB_NAME" \
OPENCREW_BACKEND_PORT="$BACKEND_PORT" OPENCREW_FRONTEND_PORT="$FRONTEND_PORT" \
OPENCREW_FRONTEND_MODE="${FRONTEND_MODE:-preview}" \
scripts/opencrew_local_stack.sh "$ACTION"
EOF
log "整栈 $ACTION 完成。"
