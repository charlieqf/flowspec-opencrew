#!/usr/bin/env bash
# 30 - 同步 git 之外的大件:ffmpeg 二进制 + ~/.opencrew(密钥/会话/模型)
# rsync 天然幂等,可重复跑做增量同步。不同步 postgres 物理目录(DB 走 40)。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
log "rsync ToolLibrary/.bin(ffmpeg/ffprobe)… 走 LAN $TEST_RSYNC_HOST"
rt1 "mkdir -p '$TEST_REPO/ToolLibrary/.bin'"
rsync -a --stats \
  "$PROD_REPO/ToolLibrary/.bin/" \
  "$TEST_RSYNC_HOST:$TEST_REPO/ToolLibrary/.bin/"

log "rsync ~/.opencrew(含 secret_store.key/secrets.enc/sessions/runtimes)… 走 LAN"
rt1 "mkdir -p '$TEST_DATA'"
rsync -a --stats \
  --exclude 'postgres/' --exclude '*.lock' --exclude 'caddy/' --exclude 'npc/' \
  "$PROD_DATA/" "$TEST_RSYNC_HOST:$TEST_DATA/"

log "收紧密钥权限…"
rt1 "chmod 700 '$TEST_DATA' 2>/dev/null; chmod 600 '$TEST_DATA/secret_store.key' '$TEST_DATA/secrets.enc' 2>/dev/null; echo ok"
log "资产同步完成。"
