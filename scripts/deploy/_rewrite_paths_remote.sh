#!/usr/bin/env bash
# 在 TEST 本地执行的路径重写 helper(由 45_rewrite_paths.sh scp 过去再运行)。
# 用 grep 找候选文件,Python 做实际改写(二进制安全、无 sed/perl -i 的引号/原地编辑坑)。
# 用法: _rewrite_paths_remote.sh <OLD> <NEW> <DIR>
set -euo pipefail
OLD="$1"; NEW="$2"; DIR="$3"

count() { grep -rl "$OLD" "$DIR" 2>/dev/null | wc -l | tr -d ' '; }
echo "命中文件: $(count)"

PYF="$(mktemp)"
cat > "$PYF" <<'PY'
import sys
old, new = sys.argv[1].encode(), sys.argv[2].encode()
n = 0
for p in sys.stdin.buffer.read().split(b'\n'):   # grep -rl 输出按换行分隔(路径无空格/换行)
    if not p:
        continue
    with open(p, 'rb') as f:
        b = f.read()
    if old not in b:
        continue
    with open(p, 'wb') as f:
        f.write(b.replace(old, new))
    n += 1
print("已重写文件数:", n)
PY
grep -rl "$OLD" "$DIR" 2>/dev/null | python3 "$PYF" "$OLD" "$NEW"
rm -f "$PYF"

echo "重写后剩余命中: $(count)"
