#!/usr/bin/env bash
# 90 - 验收清单(只读,每次部署/更新后跑)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
pass=0; fail=0
check() { # desc, remote-test-cmd
  if rt1 "$2 >/dev/null 2>&1 && echo ok || echo no" | grep -q ok; then
    printf '  %s✓%s %s\n' "$c_grn" "$c_rst" "$1"; pass=$((pass+1))
  else
    printf '  %s✗%s %s\n' "$c_red" "$c_rst" "$1"; fail=$((fail+1))
  fi
}
log "验收 macmini-4:"
check "后端 $BACKEND_PORT /api/health"        "curl -fsS -m5 http://127.0.0.1:$BACKEND_PORT/api/health"
check "前端 $FRONTEND_PORT /api/auth/status"  "curl -fsS -m5 http://127.0.0.1:$FRONTEND_PORT/api/auth/status"
check "PostgreSQL $PG_PORT 可连"              "psql -p $PG_PORT -U $DB_USER -d $DB_NAME -c 'SELECT 1'"
check "OpenCode $OPENCODE_PORT /global/health" "curl -fsS -m6 -u $OPENCODE_USER:$OPENCODE_PASS http://127.0.0.1:$OPENCODE_PORT/global/health"
check "DB 无旧路径残留"                        "test \$(psql -p $PG_PORT -U $DB_USER -d $DB_NAME -tAc \"SELECT count(*) FROM sessions WHERE workspace_dir LIKE '${PATH_OLD}%'\") = 0"
check "会话文件无旧路径残留"                   "test \$(grep -rl '$PATH_OLD' $TEST_DATA/sessions 2>/dev/null | wc -l | tr -d ' ') = 0"
check "隧道进程在跑"                           "pgrep -f 'cloudflared tunnel --url http://127.0.0.1:$FRONTEND_PORT'"
echo
log "通过 $pass / 失败 $fail"
[ "$fail" -eq 0 ] || exit 1
