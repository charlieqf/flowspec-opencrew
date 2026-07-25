#!/usr/bin/env bash
# 85 - macmini-4 无需 GUI 登录的系统级自启。
# 用法: sudo ./85_system_launchd.sh install|status|uninstall
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF="$HERE/$(basename "$0")"
ACTION="${1:-install}"

if [[ "$(id -u)" -ne 0 ]]; then
  exec /usr/bin/sudo "$SELF" "$ACTION"
fi

TEST_USER="macmini-4"
TEST_UID="$(id -u "$TEST_USER")"
SYSTEM_DIR="/Library/LaunchDaemons"
BOOT_LABEL="com.opencrew.boot.system"
TUNNEL_LABEL="com.opencrew.cloudflared.system"
GATEWAY_LABEL="com.opencrew.opencode-gateway.system"
BOOT_SOURCE="$HERE/launchd/com.opencrew.boot.system.plist"
TUNNEL_SOURCE="$HERE/launchd/com.opencrew.cloudflared.system.plist"
GATEWAY_SOURCE="$HERE/launchd/com.opencrew.opencode-gateway.system.plist"
BOOT_PLIST="$SYSTEM_DIR/$BOOT_LABEL.plist"
TUNNEL_PLIST="$SYSTEM_DIR/$TUNNEL_LABEL.plist"
GATEWAY_PLIST="$SYSTEM_DIR/$GATEWAY_LABEL.plist"
USER_BOOT_LABEL="com.opencrew.boot"
USER_TUNNEL_LABEL="com.cloudflare.cloudflared"
USER_GATEWAY_LABEL="com.local.opencode-gateway"
USER_BOOT_PLIST="/Users/$TEST_USER/Library/LaunchAgents/$USER_BOOT_LABEL.plist"
USER_TUNNEL_PLIST="/Users/$TEST_USER/Library/LaunchAgents/$USER_TUNNEL_LABEL.plist"
USER_GATEWAY_PLIST="/Users/$TEST_USER/Library/LaunchAgents/$USER_GATEWAY_LABEL.plist"

log() { printf '[system-launchd] %s\n' "$*"; }

bootstrap_system_job() {
  local label="$1"
  local plist="$2"
  local attempt
  launchctl bootout "system/$label" >/dev/null 2>&1 || true
  launchctl enable "system/$label"
  # launchd can briefly return Bootstrap error 5 immediately after bootout.
  # Retry, and trust the loaded state if launchd accepted the job despite a
  # non-zero bootstrap response.
  for attempt in $(seq 1 5); do
    if launchctl bootstrap system "$plist"; then
      return 0
    fi
    if launchctl print "system/$label" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  log "failed to bootstrap system/$label after retries"
  return 1
}

status() {
  local label
  for label in "$BOOT_LABEL" "$TUNNEL_LABEL" "$GATEWAY_LABEL"; do
    if launchctl print "system/$label" >/dev/null 2>&1; then
      log "$label: loaded"
      launchctl print "system/$label" | awk '/state =|runs =|pid =|last exit code/{print "  " $0}'
    else
      log "$label: not loaded"
    fi
  done
  launchctl print-disabled "gui/$TEST_UID" 2>/dev/null \
    | awk '/com.opencrew.boot|com.cloudflare.cloudflared|com.local.opencode-gateway/{print "  user job: " $0}' || true
}

install_jobs() {
  [[ -f "$BOOT_SOURCE" && -f "$TUNNEL_SOURCE" && -f "$GATEWAY_SOURCE" ]] || {
    log "missing plist templates under $HERE/launchd"
    exit 1
  }
  [[ -r "/Users/$TEST_USER/.config/opencode-gateway/env" ]] || {
    log "missing OpenCode gateway env for $TEST_USER"
    exit 1
  }
  plutil -lint "$BOOT_SOURCE" "$TUNNEL_SOURCE" "$GATEWAY_SOURCE"
  install -d -o root -g wheel -m 755 "$SYSTEM_DIR"
  install -o root -g wheel -m 644 "$BOOT_SOURCE" "$BOOT_PLIST"
  install -o root -g wheel -m 644 "$TUNNEL_SOURCE" "$TUNNEL_PLIST"
  install -o root -g wheel -m 644 "$GATEWAY_SOURCE" "$GATEWAY_PLIST"

  if ! bootstrap_system_job "$TUNNEL_LABEL" "$TUNNEL_PLIST"; then
    log "restoring user tunnel after system tunnel bootstrap failure"
    launchctl enable "gui/$TEST_UID/$USER_TUNNEL_LABEL" || true
    [[ ! -f "$USER_TUNNEL_PLIST" ]] || launchctl bootstrap "gui/$TEST_UID" "$USER_TUNNEL_PLIST" 2>/dev/null || true
    launchctl kickstart -k "gui/$TEST_UID/$USER_TUNNEL_LABEL" 2>/dev/null || true
    exit 1
  fi
  for _ in $(seq 1 15); do
    launchctl print "system/$TUNNEL_LABEL" 2>/dev/null | grep -q 'state = running' && break
    sleep 1
  done
  launchctl print "system/$TUNNEL_LABEL" 2>/dev/null | grep -q 'state = running' || {
    log "$TUNNEL_LABEL did not reach running state; keeping user LaunchAgents enabled"
    exit 1
  }

  bootstrap_system_job "$BOOT_LABEL" "$BOOT_PLIST"
  launchctl kickstart -k "system/$BOOT_LABEL"
  for _ in $(seq 1 60); do
    if lsof -nP -iTCP:5433 -sTCP:LISTEN >/dev/null 2>&1 \
      && lsof -nP -iTCP:4096 -sTCP:LISTEN >/dev/null 2>&1 \
      && lsof -nP -iTCP:8011 -sTCP:LISTEN >/dev/null 2>&1 \
      && lsof -nP -iTCP:18080 -sTCP:LISTEN >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  for port in 5433 4096 8011 18080; do
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || {
      log "port $port did not become ready; keeping user LaunchAgents enabled"
      exit 1
    }
  done

  # Gateway 与上面的专用 OpenCode 共用同一套部署凭据。系统级 KeepAlive
  # 接管前先停掉用户级旧副本，避免 5096 端口竞争。
  launchctl bootout "gui/$TEST_UID/$USER_GATEWAY_LABEL" >/dev/null 2>&1 || true
  bootstrap_system_job "$GATEWAY_LABEL" "$GATEWAY_PLIST"
  for _ in $(seq 1 30); do
    curl -fsS -m 3 "http://127.0.0.1:5096/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS -m 3 "http://127.0.0.1:5096/health" >/dev/null 2>&1 || {
    log "$GATEWAY_LABEL did not become healthy; keeping the user LaunchAgent enabled"
    launchctl bootout "system/$GATEWAY_LABEL" >/dev/null 2>&1 || true
    launchctl enable "gui/$TEST_UID/$USER_GATEWAY_LABEL" || true
    [[ ! -f "$USER_GATEWAY_PLIST" ]] || launchctl bootstrap "gui/$TEST_UID" "$USER_GATEWAY_PLIST" 2>/dev/null || true
    exit 1
  }

  # 系统级任务已接管后停用用户级副本，避免登录时产生重复 connector/编排。
  launchctl bootout "gui/$TEST_UID/$USER_TUNNEL_LABEL" >/dev/null 2>&1 || true
  launchctl disable "gui/$TEST_UID/$USER_TUNNEL_LABEL"
  launchctl bootout "gui/$TEST_UID/$USER_BOOT_LABEL" >/dev/null 2>&1 || true
  launchctl disable "gui/$TEST_UID/$USER_BOOT_LABEL"
  launchctl disable "gui/$TEST_UID/$USER_GATEWAY_LABEL"

  log "installed system jobs; user LaunchAgents disabled but files retained for rollback"
  status
}

uninstall_jobs() {
  launchctl bootout "system/$BOOT_LABEL" >/dev/null 2>&1 || true
  launchctl bootout "system/$TUNNEL_LABEL" >/dev/null 2>&1 || true
  launchctl bootout "system/$GATEWAY_LABEL" >/dev/null 2>&1 || true
  rm -f "$BOOT_PLIST" "$TUNNEL_PLIST" "$GATEWAY_PLIST"

  # 恢复原用户级启动项；若当前没有 GUI 会话，enable 会在下次登录时生效。
  launchctl enable "gui/$TEST_UID/$USER_TUNNEL_LABEL" || true
  launchctl enable "gui/$TEST_UID/$USER_BOOT_LABEL" || true
  launchctl enable "gui/$TEST_UID/$USER_GATEWAY_LABEL" || true
  if launchctl print "gui/$TEST_UID" >/dev/null 2>&1; then
    [[ ! -f "$USER_TUNNEL_PLIST" ]] || launchctl bootstrap "gui/$TEST_UID" "$USER_TUNNEL_PLIST" 2>/dev/null || true
    [[ ! -f "$USER_BOOT_PLIST" ]] || launchctl bootstrap "gui/$TEST_UID" "$USER_BOOT_PLIST" 2>/dev/null || true
    [[ ! -f "$USER_GATEWAY_PLIST" ]] || launchctl bootstrap "gui/$TEST_UID" "$USER_GATEWAY_PLIST" 2>/dev/null || true
    launchctl kickstart -k "gui/$TEST_UID/$USER_BOOT_LABEL" 2>/dev/null || true
    launchctl kickstart -k "gui/$TEST_UID/$USER_GATEWAY_LABEL" 2>/dev/null || true
  fi
  log "uninstalled system jobs and restored user LaunchAgents"
}

case "$ACTION" in
  install) install_jobs ;;
  status) status ;;
  uninstall) uninstall_jobs ;;
  *) printf 'Usage: %s {install|status|uninstall}\n' "$0" >&2; exit 2 ;;
esac
