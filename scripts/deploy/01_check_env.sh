#!/usr/bin/env bash
# 01 - 探查/校验 TEST 运行时与端口(只读,随时可重复跑)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
log "校验 macmini-4 运行时与端口…"
rt <<EOF
echo "== 身份 =="; scutil --get ComputerName 2>/dev/null; echo "user=\$(whoami) HOME=\$HOME"; sw_vers -productVersion; uname -m
echo "== 运行时 =="
for b in brew git rsync screen cloudflared node npm python3.12 psql pg_ctl initdb pg_dump; do
  printf "%-12s %s\n" "\$b" "\$(command -v "\$b" 2>/dev/null || echo 缺失)"
done
[ -x "\$HOME/.bun/bin/bun" ] && echo "bun          \$HOME/.bun/bin/bun" || echo "bun          缺失"
echo "node=\$(node --version 2>/dev/null) py312=\$(python3.12 --version 2>/dev/null)"
echo "pg16: \$(brew list --versions postgresql@16 2>/dev/null || echo 未装)"
echo "== 目标端口(应全部空闲)=="
for p in $BACKEND_PORT $FRONTEND_PORT $PG_PORT $OPENCODE_PORT; do
  printf "%-6s " "\$p"; lsof -nP -iTCP:\$p -sTCP:LISTEN 2>/dev/null | awk 'NR==2{print \$1; f=1} END{if(!f)print "空闲"}'
done
echo "== 磁盘 =="; df -h / | tail -1
EOF
log "校验完成。"
