#!/usr/bin/env bash
set -euo pipefail

printf '[opencrew] static import cache-bump script retired; Vite hashed assets plus immutable cache headers now own frontend cache freshness.\n'
exit 0

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s <version-slug>\n' "$0" >&2
  printf 'Example: %s 20260621-koubo-ui-v1\n' "$0" >&2
  exit 2
fi

VERSION="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCREW_ROOT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
KOUBO_FRONTEND_DIR="$ROOT_DIR/frontend/src/modules/koubo"

replace_version() {
  local file="$1"
  local pattern="$2"
  VERSION="$VERSION" PATTERN="$pattern" perl -0pi -e 's#($ENV{PATTERN}\?v=)[^"]+#$1$ENV{VERSION}#g' "$file"
}

replace_static_import_versions() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  VERSION="$VERSION" perl -0pi -e 's#((?:from\s+|import\s+)["'\''][^"'\'']+\?v=)[^"'\'']+#$1$ENV{VERSION}#g' "$file"
}

replace_version "$ROOT_DIR/frontend/index.html" "src/main\\.tsx"
replace_version "$ROOT_DIR/frontend/src/main.tsx" "App\\.jsx"
replace_static_import_versions "$ROOT_DIR/frontend/src/App.jsx"
replace_static_import_versions "$KOUBO_FRONTEND_DIR/KouboStoryBoardModule.jsx"
for koubo_dir in \
  "$KOUBO_FRONTEND_DIR/AnalysisV1" \
  "$KOUBO_FRONTEND_DIR/KouboStoryBoard" \
  "$KOUBO_FRONTEND_DIR/KouboTaskList" \
  "$KOUBO_FRONTEND_DIR/DanceMimicV1" \
  "$KOUBO_FRONTEND_DIR/UploadAssetLibrary"; do
  [[ -d "$koubo_dir" ]] || continue
  while IFS= read -r file; do
    replace_static_import_versions "$file"
  done < <(find "$koubo_dir" -type f \( -name '*.js' -o -name '*.jsx' -o -name '*.ts' -o -name '*.tsx' \) | sort)
done
for dir in "$ROOT_DIR/frontend/src/lib" "$ROOT_DIR/frontend/src/pages" "$ROOT_DIR/frontend/src/components" "$ROOT_DIR/frontend/src/debug" "$ROOT_DIR/frontend/src/shell"; do
  [[ -d "$dir" ]] || continue
  while IFS= read -r file; do
    replace_static_import_versions "$file"
  done < <(find "$dir" -type f \( -name '*.js' -o -name '*.jsx' -o -name '*.ts' -o -name '*.tsx' \) | sort)
done

printf '[opencrew] bumped Koubo frontend cache version to %s\n' "$VERSION"
printf '[opencrew] run scripts/opencrew_frontend_preflight.sh to verify the served chain\n'
