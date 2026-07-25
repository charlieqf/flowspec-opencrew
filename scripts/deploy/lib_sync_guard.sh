#!/usr/bin/env bash
# Dirty-tree guard for the prod -> test mirror.
# Sourced by 20_sync_code.sh AFTER lib.sh (needs _LIB_DIR, PROD_REPO, TEST_REPO,
# TEST_HOME, TEST_HOST, TEST_SSH[], and log/warn/die).
#
# Two-sided policy:
#   * PROD source dirty  -> abort unconditionally; --force cannot bypass.
#   * TEST target dirty  -> back up (outside the mirror target) + verify, then
#     still abort by default. Only --force-after-reviewed-backup continues,
#     and only after a verified backup exists.
#   * Any backup / hash / disk-space / SSH failure -> do NOT run rsync.

_GUARD_PY="$_LIB_DIR/sync_guard.py"
# macOS ships a system python3 here; guaranteed present over a bare SSH login.
_REMOTE_PY="/usr/bin/python3"

# Run the guard on TEST by piping the (authoritative PROD copy of the) script to
# the remote python's stdin. Args after the dash become the remote argv.
_guard_remote() {
  "${TEST_SSH[@]}" "$_REMOTE_PY - $*" < "$_GUARD_PY"
}

sync_guard_preflight() {
  local reviewed_backup="${1:-}"
  [[ -f "$_GUARD_PY" ]] || die "sync guard 缺失:$_GUARD_PY"

  # ---- 1) PROD 源端:dirty 无条件中止(force 不可绕过)----
  log "预检 PROD 源端工作树:$PROD_REPO"
  local rc=0
  python3 "$_GUARD_PY" is-dirty "$PROD_REPO" >/dev/null || rc=$?
  case "$rc" in
    0) log "PROD 源端干净。" ;;
    3) die "PROD 源端存在未提交改动;镜像会把半成品推向 TEST。请先在 PROD 提交或暂存后再同步。此中止不可用 --force-after-reviewed-backup 绕过。" ;;
    *) die "无法判定 PROD 源端工作树状态(rc=$rc);禁止 rsync。" ;;
  esac

  # ---- 2) TEST 目标端:dirty 则先备份+校验,默认仍中止 ----
  log "预检 TEST 目标端工作树:$TEST_HOST:$TEST_REPO"
  rc=0
  _guard_remote "is-dirty '$TEST_REPO'" >/dev/null || rc=$?
  case "$rc" in
    0)
      [[ -z "$reviewed_backup" ]] || die "TEST 目标端当前干净,不接受无关的 --force-after-reviewed-backup。"
      log "TEST 目标端干净,允许镜像。"
      return 0
      ;;
    3) warn "TEST 目标端存在未提交改动;先在镜像目录外备份并校验……" ;;
    *) die "无法判定 TEST 目标端工作树状态(rc=$rc,可能 SSH 中断);禁止 rsync。" ;;
  esac

  if [[ -n "$reviewed_backup" ]]; then
    case "$reviewed_backup" in
      "$TEST_HOME"/opencrew-sync-backups/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
      *) die "审核备份路径不合法:$reviewed_backup" ;;
    esac
    rc=0
    _guard_remote "verify-worktree '$TEST_REPO' '$reviewed_backup'" || rc=$?
    [[ "$rc" == "0" ]] || die "审核后的 TEST 备份与当前工作树不一致或校验失败(rc=$rc);禁止 rsync。"
    warn "--force-after-reviewed-backup:已重新验证人工审核的备份及当前工作树:$reviewed_backup"
    return 0
  fi

  local ts backup_dir
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_dir="$TEST_HOME/opencrew-sync-backups/$ts"

  rc=0
  _guard_remote "backup '$TEST_REPO' '$backup_dir'" || rc=$?
  [[ "$rc" == "0" ]] || die "TEST 目标端备份失败(rc=$rc;备份/磁盘空间/SSH 任一失败);禁止 rsync。"

  rc=0
  _guard_remote "verify '$backup_dir'" || rc=$?
  [[ "$rc" == "0" ]] || die "TEST 目标端备份校验失败(rc=$rc);禁止 rsync。"

  log "TEST 目标端已备份并通过校验:$TEST_HOST:$backup_dir"

  die "TEST 目标端曾有未提交改动;已完成备份+校验但默认中止。请人工审核 $TEST_HOST:$backup_dir,确认无误后使用 --force-after-reviewed-backup '$backup_dir' 重新运行。"
}
