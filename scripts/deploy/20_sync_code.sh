#!/usr/bin/env bash
# 20 - 把代码从 PROD(macmini-1,跟 main 的权威源)rsync 镜像到 TEST
# 用 rsync 而非 git clone:macmini-4 无 GitHub 私有库凭据;PROD 已有凭据并跟 main。
# 含 .git(macmini-4 仍是真实 git 检出),排除可重建/体积大的目录;--delete 做真正镜像。
# 这是【日常增量更新】主入口:PROD 先 git pull,再跑本脚本即可。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
source ./lib_sync_guard.sh

REVIEWED_BACKUP_DIR=""
while (( $# > 0 )); do
  case "$1" in
    --force-after-reviewed-backup)
      (( $# >= 2 )) || die "--force-after-reviewed-backup 需要已人工审核的备份绝对路径"
      REVIEWED_BACKUP_DIR="$2"
      shift 2
      ;;
    -h|--help)
      printf 'usage: %s [--force-after-reviewed-backup /absolute/reviewed/backup]\n' "$(basename "$0")"
      exit 0
      ;;
    *) die "未知参数:$1" ;;
  esac
done

require_ssh
# Dirty-tree guard: refuse to mirror over uncommitted work. PROD dirty aborts
# unconditionally; TEST dirty is backed up + verified then aborts unless the
# reviewed-backup override is passed. See lib_sync_guard.sh.
sync_guard_preflight "$REVIEWED_BACKUP_DIR"

log "PROD 仓库当前提交:$(git -C "$PROD_REPO" rev-parse --short HEAD) $(git -C "$PROD_REPO" log -1 --format=%s)"
rt1 "mkdir -p '$TEST_REPO'"

log "rsync 代码 $PROD_REPO → $TEST_REPO(镜像)…"
rsync -a --delete --stats \
  --exclude '.venv/' --exclude 'backend/.venv/' \
  --exclude 'frontend/node_modules/' --exclude 'frontend/dist/' \
  --exclude 'frontend/src-tauri/target/' --exclude 'frontend/tsconfig.tsbuildinfo' \
  --exclude 'ToolLibrary/.bin/' --exclude 'ToolLibrary/vendor/' --exclude 'ToolLibrary/Rebuild/' \
  --exclude 'ToolLibrary/Analysis_V1/node_modules/' \
  --exclude 'playground/' --exclude 'issues/' --exclude 'test-results/' \
  --exclude 'docs/Analysis_V1/' --exclude 'docs/artifacts/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '*.log' --exclude '.DS_Store' \
  "$PROD_REPO/" "$TEST_RSYNC_HOST:$TEST_REPO/"

log "TEST 镜像后提交:$(rt1 "cd '$TEST_REPO' && git rev-parse --short HEAD 2>/dev/null && git log -1 --format=%s 2>/dev/null")"
log "代码同步完成。"
