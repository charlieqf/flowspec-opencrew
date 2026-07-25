#!/usr/bin/env bash
# 70 - 确保 quick tunnel 在运行,并打印当前对外网址(幂等:在跑则只回显网址)
# quick tunnel 无需域名/登录;网址随机,每次重启会变。
# 用法: 70_tunnel.sh           确保在跑并打印网址
#       70_tunnel.sh --restart 重起(换一个新网址)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
FORCE="${1:-}"
RUN_PAT="cloudflared tunnel --url http://127.0.0.1:$FRONTEND_PORT"

print_url() {
  rt1 "grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' '$TUNNEL_LOG' 2>/dev/null | tail -1"
}

if [[ "$FORCE" != "--restart" ]] && rt1 "pgrep -f '$RUN_PAT' >/dev/null 2>&1 && echo up || echo down" | grep -q up; then
  url="$(print_url)"
  log "隧道已在运行。当前网址:${url:-（日志里暂未解析到,稍后再跑本脚本）}"
  exit 0
fi

log "启动 quick tunnel(目标 127.0.0.1:$FRONTEND_PORT)…"
rt <<EOF
pkill -f "$RUN_PAT" 2>/dev/null || true
sleep 1
# --config /dev/null:忽略已存在的 ~/.cloudflared/config.yml(那是别的命名隧道的配置,
# 会把 ha-connections:1/credentials-file 串进来,导致 quick tunnel 边缘 404)
nohup cloudflared tunnel --config /dev/null --url http://127.0.0.1:$FRONTEND_PORT > "$TUNNEL_LOG" 2>&1 &
EOF
# 轮询日志拿网址(cloudflared 起隧道要几秒)
url=""
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 2
  url="$(print_url)"
  [[ -n "$url" ]] && break
done
if [[ -n "$url" ]]; then
  log "隧道就绪 ✓  对外网址:$url"
  log "（.trycloudflare.com 已在 vite allowedHosts 放行,前端可直接接受）"
else
  warn "未能在日志解析到网址,请查看 TEST:$TUNNEL_LOG"
fi
