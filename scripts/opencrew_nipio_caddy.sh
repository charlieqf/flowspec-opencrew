#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-render}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCREW_ROOT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RUNTIME_DIR="${OPENCREW_CADDY_RUNTIME_DIR:-$HOME/.opencrew/caddy}"
CADDYFILE="${OPENCREW_CADDYFILE:-$RUNTIME_DIR/Caddyfile}"
CADDY_LOG="${OPENCREW_CADDY_LOG:-/tmp/opencrew-caddy.log}"
CADDY_SCREEN="${OPENCREW_CADDY_SCREEN:-opencrew-caddy}"

CADDY_BIN="${OPENCREW_CADDY_BIN:-$(command -v caddy || true)}"
SITE="${OPENCREW_CADDY_SITE:-http://127.0.0.1.nip.io:18081}"
UPSTREAM="${OPENCREW_FRONTEND_UPSTREAM:-127.0.0.1:18080}"
BASIC_AUTH_USER="${OPENCREW_BASIC_AUTH_USER:-}"
BASIC_AUTH_PASSWORD="${OPENCREW_BASIC_AUTH_PASSWORD:-}"

info() {
  printf '[opencrew-caddy] %s\n' "$*"
}

require_caddy() {
  if [[ -z "$CADDY_BIN" || ! -x "$CADDY_BIN" ]]; then
    info "missing caddy; install with: brew install caddy"
    return 1
  fi
}

screen_exists() {
  local name="$1"
  local output
  output="$(screen -list 2>/dev/null || true)"
  printf '%s\n' "$output" | grep -q "[.]${name}"
}

stop_screen() {
  if screen_exists "$CADDY_SCREEN"; then
    info "stopping screen session: $CADDY_SCREEN"
    screen -S "$CADDY_SCREEN" -X quit || true
  fi
}

stop_caddy() {
  stop_screen
  local pids
  pids="$(pgrep -f "$CADDY_BIN run --config $CADDYFILE" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    info "stopping Caddy process: ${pids//$'\n'/ }"
    kill $pids 2>/dev/null || true
  fi
}

render_caddyfile() {
  require_caddy
  if [[ -z "$BASIC_AUTH_USER" || -z "$BASIC_AUTH_PASSWORD" ]]; then
    info "OPENCREW_BASIC_AUTH_USER and OPENCREW_BASIC_AUTH_PASSWORD are required"
    return 2
  fi
  mkdir -p "$RUNTIME_DIR"
  local password_hash
  password_hash="$("$CADDY_BIN" hash-password --plaintext "$BASIC_AUTH_PASSWORD")"
  cat > "$CADDYFILE" <<EOF
$SITE {
  basic_auth {
    $BASIC_AUTH_USER $password_hash
  }

  request_body {
    max_size 4KB
  }

  reverse_proxy $UPSTREAM {
    header_up Host $UPSTREAM
  }
}
EOF
  "$CADDY_BIN" fmt --overwrite "$CADDYFILE" >/dev/null
  info "rendered $CADDYFILE"
  "$CADDY_BIN" validate --config "$CADDYFILE"
}

start_caddy() {
  render_caddyfile
  stop_caddy
  : > "$CADDY_LOG"
  info "starting Caddy in screen: $CADDY_SCREEN"
  screen -dmS "$CADDY_SCREEN" /bin/zsh -lc \
    "cd '$ROOT_DIR' && exec '$CADDY_BIN' run --config '$CADDYFILE' >> '$CADDY_LOG' 2>&1"
  sleep 2
  status_caddy
}

status_caddy() {
  if screen_exists "$CADDY_SCREEN"; then
    info "screen: $CADDY_SCREEN exists"
  else
    info "screen: $CADDY_SCREEN missing"
  fi
  info "site: $SITE"
  info "upstream: $UPSTREAM"
  info "config: $CADDYFILE"
  info "log: $CADDY_LOG"
}

case "$ACTION" in
  render)
    render_caddyfile
    ;;
  start)
    start_caddy
    ;;
  stop)
    stop_caddy
    ;;
  restart)
    stop_caddy
    start_caddy
    ;;
  status)
    status_caddy
    ;;
  *)
    printf 'Usage: %s {render|start|stop|restart|status}\n' "$0" >&2
    exit 2
    ;;
esac
