#!/usr/bin/env bash
# 50 - 确保 OpenCrew 专用 OpenCode 在 4096 健康(幂等:健康则跳过,否则起一个)
# 用法: 50_opencode.sh           健康检查,缺则启动
#       50_opencode.sh --restart 强制重起
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
[[ "$OPENCODE_PASS" == CHANGE_ME* ]] && warn "OPENCODE_PASS 仍是占位符,请在 00_config.env 改成 TEST 专用口令"
FORCE="${1:-}"

if [[ "$FORCE" != "--restart" ]]; then
  if rt1 "curl -fsS -m 6 -u '$OPENCODE_USER:$OPENCODE_PASS' http://127.0.0.1:$OPENCODE_PORT/global/health >/dev/null 2>&1 && echo ok || echo no" | grep -q ok; then
    log "OpenCode 已健康(4096),跳过。"; exit 0
  fi
fi

log "启动 OpenCrew 专用 OpenCode(源码 $TEST_OPENCODE_SRC,端口 $OPENCODE_PORT)…"
rt <<EOF
set -e
# 先停掉占 4096 的旧进程(若有)
pid=\$(lsof -nP -tiTCP:$OPENCODE_PORT -sTCP:LISTEN 2>/dev/null | head -1); [ -n "\$pid" ] && kill "\$pid" 2>/dev/null || true
sleep 1
cd "$TEST_OPENCODE_SRC"
nohup env OPENCODE_SERVER_USERNAME="$OPENCODE_USER" OPENCODE_SERVER_PASSWORD="$OPENCODE_PASS" \
  "\$HOME/.bun/bin/bun" run --cwd packages/opencode --conditions=browser src/index.ts serve \
  --hostname 127.0.0.1 --port $OPENCODE_PORT > /tmp/opencrew-opencode-$OPENCODE_PORT.log 2>&1 &
sleep 3
curl -fsS -m 8 -u "$OPENCODE_USER:$OPENCODE_PASS" http://127.0.0.1:$OPENCODE_PORT/global/health >/dev/null \
  && echo "OpenCode 健康 ✓" || { echo "启动失败,日志:"; tail -20 /tmp/opencrew-opencode-$OPENCODE_PORT.log; exit 1; }
EOF
log "OpenCode 就绪。部署后记得在 OpenCrew 跑 POST /api/setup/opencode/discover。"
