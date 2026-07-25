#!/usr/bin/env bash
# 45 - 重写磁盘会话文件里写死的绝对路径(/Users/macmini-1 → /Users/macmini-4)
# 幂等:无命中即 no-op,可反复跑做补漏。
# 实际重写逻辑在 _rewrite_paths_remote.sh,scp 到 TEST 本地执行(避开嵌套引号问题)。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
log "上传重写 helper 并在 TEST 本地执行 $PATH_OLD → $PATH_NEW …"
scp -q ./_rewrite_paths_remote.sh "$TEST_RSYNC_HOST:/tmp/opencrew_rewrite_paths.sh"
rt1 "bash /tmp/opencrew_rewrite_paths.sh '$PATH_OLD' '$PATH_NEW' '$TEST_DATA/sessions'"
log "路径重写完成。"
