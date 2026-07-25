#!/usr/bin/env bash
# 在 macmini-4 本地运行的开机编排:幂等地拉起 PG → OpenCode → Gateway → 整栈 → 命名隧道。
# 由用户级 LaunchAgent 或系统级 LaunchDaemon 在 RunAtLoad 时调用;
# 也可手动 `bash _boot_remote.sh`。
# 复用同目录 00_config.env 的变量(随仓库 rsync 到 macmini-4)。
set -uo pipefail
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$HOME/.bun/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# launchd 不继承登录 shell 的 locale。PG16 在缺少有效 locale 时会在启动阶段
# 报 "postmaster became multithreaded during startup" 并退出。
export LANG="C.UTF-8"
export LC_ALL="C.UTF-8"
# 系统级 LaunchDaemon 没有 GUI 登录会话注入的 TMPDIR。screen 必须使用与该
# 用户登录后相同的 Darwin 临时目录，日常 status/restart 才能看到同一批会话。
if [[ -z "${TMPDIR:-}" ]]; then
  TMPDIR="$(getconf DARWIN_USER_TEMP_DIR 2>/dev/null || true)"
  export TMPDIR="${TMPDIR:-/tmp/}"
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/00_config.env"
log(){ printf '[opencrew-boot] %s %s\n' "$(date '+%H:%M:%S')" "$*"; }

# OpenCode 必须脱离一次性 launchd 编排进程长期运行。screen 子会话重新调用本脚本
# 的这个入口，避免把 Basic Auth 口令放进 screen 的命令行参数。
if [[ "${1:-}" == "opencode" ]]; then
  cd "$TEST_OPENCODE_SRC"
  exec env OPENCODE_SERVER_USERNAME="$OPENCODE_USER" OPENCODE_SERVER_PASSWORD="$OPENCODE_PASS" \
    "$HOME/.bun/bin/bun" run --cwd packages/opencode --conditions=browser src/index.ts serve \
    --hostname 127.0.0.1 --port "$OPENCODE_PORT"
fi

# Gateway 由独立的系统级 KeepAlive LaunchDaemon 调用。它必须使用 OpenCrew
# 专用 OpenCode 实例的 Basic Auth，而不是已停用的旧用户级 server env。
if [[ "${1:-}" == "opencode-gateway" ]]; then
  GATEWAY_ENV="$HOME/.config/opencode-gateway/env"
  [[ -r "$GATEWAY_ENV" ]] || {
    log "missing OpenCode gateway env: $GATEWAY_ENV"
    exit 1
  }
  # shellcheck disable=SC1090
  source "$GATEWAY_ENV"
  cd "$HOME/work/code/macminis"
  exec env OPENCODE_SERVER_USERNAME="$OPENCODE_USER" OPENCODE_SERVER_PASSWORD="$OPENCODE_PASS" \
    "$HOME/.bun/bin/bun" run gateway/src/server.ts
fi

log "boot start"

# 1) PostgreSQL
if ! lsof -nP -iTCP:"$PG_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  log "starting postgres on $PG_PORT"
  pg_ctl -D "$TEST_DATA/postgres" -o "-p $PG_PORT" -l /tmp/opencrew-pg.log start || log "pg_ctl start failed"
fi
for _ in $(seq 1 30); do pg_isready -p "$PG_PORT" >/dev/null 2>&1 && break; sleep 1; done
log "postgres ready=$(pg_isready -p "$PG_PORT" >/dev/null 2>&1 && echo yes || echo no)"

# 2) OpenCode (4096)
if ! curl -fsS -m5 -u "$OPENCODE_USER:$OPENCODE_PASS" "http://127.0.0.1:$OPENCODE_PORT/global/health" >/dev/null 2>&1; then
  log "starting opencode on $OPENCODE_PORT"
  screen -S opencrew-opencode -X quit >/dev/null 2>&1 || true
  : > "/tmp/opencrew-opencode-$OPENCODE_PORT.log"
  screen -dmS opencrew-opencode /bin/bash -lc \
    "exec '$HERE/_boot_remote.sh' opencode >> '/tmp/opencrew-opencode-$OPENCODE_PORT.log' 2>&1"
  for _ in $(seq 1 30); do
    curl -fsS -m5 -u "$OPENCODE_USER:$OPENCODE_PASS" "http://127.0.0.1:$OPENCODE_PORT/global/health" >/dev/null 2>&1 && break
    sleep 1
  done
fi
log "opencode ready=$(curl -fsS -m5 -u "$OPENCODE_USER:$OPENCODE_PASS" "http://127.0.0.1:$OPENCODE_PORT/global/health" >/dev/null 2>&1 && echo yes || echo no)"

# 3) OpenCrew 整栈(backend+frontend,脚本自身幂等)
cd "$TEST_REPO"
OPENCREW_DATA_DIR="$TEST_DATA" \
DATABASE_URL="postgresql+psycopg://$DB_USER:$DB_PASS@127.0.0.1:$PG_PORT/$DB_NAME" \
OPENCREW_BACKEND_PORT="$BACKEND_PORT" OPENCREW_FRONTEND_PORT="$FRONTEND_PORT" \
  scripts/opencrew_local_stack.sh start || log "stack start returned nonzero"

# 4) 正式命名隧道由独立的 KeepAlive launchd agent 托管。
# 不在这里启动 quick tunnel，避免随机域名和重复 connector。
TUNNEL_LABEL="com.cloudflare.cloudflared"
TUNNEL_SYSTEM_LABEL="com.opencrew.cloudflared.system"
TUNNEL_CONFIG="$HOME/.cloudflared/config.yml"
if ! pgrep -f "cloudflared --config $TUNNEL_CONFIG tunnel run" >/dev/null 2>&1; then
  if launchctl print "system/$TUNNEL_SYSTEM_LABEL" >/dev/null 2>&1; then
    # 系统级隧道与本编排并行 RunAtLoad 且 KeepAlive，给它留出连接时间。
    log "waiting for system named tunnel $TUNNEL_SYSTEM_LABEL"
  else
    log "starting named tunnel via $TUNNEL_LABEL"
    launchctl kickstart -k "gui/$TEST_UID/$TUNNEL_LABEL" || log "named tunnel kickstart failed"
  fi
  for _ in $(seq 1 15); do
    pgrep -f "cloudflared --config $TUNNEL_CONFIG tunnel run" >/dev/null 2>&1 && break
    sleep 1
  done
fi
log "named tunnel ready=$(pgrep -f "cloudflared --config $TUNNEL_CONFIG tunnel run" >/dev/null 2>&1 && echo yes || echo no)"
log "boot done"
