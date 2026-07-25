#!/usr/bin/env bash
# 10 - 停用/恢复占用 OpenCrew 端口的别的项目(marker + 本地 opencode)
# 用法: 10_free_ports.sh           停+禁用(幂等)
#       10_free_ports.sh --restore 恢复并重新启用
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
MODE="${1:-stop}"
AGENTS=("${MARKER_AGENTS[@]}" "${OPENCODE_AGENTS[@]}")

if [[ "$MODE" == "--restore" ]]; then
  log "恢复并启用 marker + 本地 opencode …"
  for L in "${OPENCODE_AGENTS[@]}" "${MARKER_AGENTS[@]}"; do
    rt1 "launchctl enable gui/$TEST_UID/$L 2>/dev/null; launchctl bootstrap gui/$TEST_UID $TEST_HOME/Library/LaunchAgents/$L.plist 2>/dev/null; echo '  bootstrap $L'"
  done
else
  log "停用占端口的别的项目(可逆,plist 保留)…"
  for L in "${AGENTS[@]}"; do
    rt1 "launchctl bootout gui/$TEST_UID/$L 2>/dev/null; launchctl disable gui/$TEST_UID/$L 2>/dev/null; echo '  bootout+disable $L'"
  done
fi
log "复查目标端口:"
for p in "$FRONTEND_PORT" "$OPENCODE_PORT"; do
  printf '  %-6s %s\n' "$p" "$(test_port_listening "$p")"
done
