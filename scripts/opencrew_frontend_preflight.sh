#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCREW_ROOT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FRONTEND_PORT="${OPENCREW_FRONTEND_PORT:-18080}"
FRONTEND_URL="${OPENCREW_FRONTEND_URL:-http://127.0.0.1:${FRONTEND_PORT}}"
KOUBO_FRONTEND_DIR="$ROOT_DIR/frontend/src/modules/koubo"

section() {
  printf '\n[opencrew] %s\n' "$*"
}

trim_url_path() {
  local value="$1"
  value="${value#./}"
  value="${value#/}"
  printf '%s' "$value"
}

fetch_url() {
  local url="$1"
  curl --connect-timeout "${OPENCREW_PREFLIGHT_CONNECT_TIMEOUT:-2}" --max-time "${OPENCREW_PREFLIGHT_MAX_TIME:-8}" -fsS "$url" || true
}

extract_import_path() {
  local js="$1"
  local path_pattern="$2"
  printf '%s\n' "$js" | grep -Eo "\"[^\"]*${path_pattern}\\?v=[^\"]+\"" | head -1 | tr -d '"' || true
}

print_import_path() {
  local label="$1"
  local js="$2"
  local path_pattern="$3"
  local path
  path="$(extract_import_path "$js" "$path_pattern")"
  if [[ -n "$path" ]]; then
    printf '%s -> /%s\n' "$label" "$(trim_url_path "$path")"
    return 0
  fi
  printf '%s -> import not found (%s)\n' "$label" "$path_pattern"
  return 1
}

extract_asset_path() {
  local html="$1"
  local ext="$2"
  printf '%s\n' "$html" | grep -Eo "assets/index-[^\"']+\\.${ext}" | head -1 || true
}

section "authoritative docs"
printf 'ARCHITECTURE.md explains the runtime split and module imports.\n'
printf 'Runtime frontend: %s\n' "$ROOT_DIR/frontend"
printf 'Koubo frontend module: %s\n' "$KOUBO_FRONTEND_DIR"
printf 'WorkflowAssistant frontend remains external: %s\n' "$ROOT_DIR/WorkflowAssistant/frontend"

section "listener on ${FRONTEND_PORT}"
pids="$(lsof -nP -tiTCP:"$FRONTEND_PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -z "$pids" ]]; then
  printf 'No frontend listener on %s. Start it with scripts/opencrew_local_stack.sh restart\n' "$FRONTEND_PORT"
  exit 1
fi
for pid in $pids; do
  ps -p "$pid" -o pid= -o command=
  lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | awk '/^n/ { sub(/^n/, "  cwd: "); print }' || true
done

section "source static import cache guard"
grep -n '<script type="module".*src/main.tsx' "$ROOT_DIR/frontend/index.html" || true
if grep -rE "from [\"'][^\"']+[?]v=|import [\"'][^\"']+[?]v=" "$ROOT_DIR/frontend/src"; then
  printf 'Static import query strings are retired; remove the ?v= import source above.\n'
  exit 1
fi
printf 'source static imports contain no ?v= query strings\n'

section "served entry chain"
index_html="$(fetch_url "$FRONTEND_URL/")"
if [[ -z "$index_html" ]]; then
  printf 'Could not fetch %s/\n' "$FRONTEND_URL"
  exit 1
fi
main_src="$(printf '%s\n' "$index_html" | sed -nE 's/.*src="\.?\/?([^"]*src\/main\.tsx[^"]*)".*/\1/p' | head -1)"
if [[ -z "$main_src" ]]; then
  preview_js_path="$(extract_asset_path "$index_html" "js")"
  preview_css_path="$(extract_asset_path "$index_html" "css")"
  if [[ -z "$preview_js_path" || -z "$preview_css_path" ]]; then
    printf 'Could not find src/main.tsx or built index assets in served index.\n'
    exit 1
  fi

  printf 'served preview build detected\n'
  printf 'index -> /%s\n' "$(trim_url_path "$preview_js_path")"
  printf 'index -> /%s\n' "$(trim_url_path "$preview_css_path")"
  preview_js="$(fetch_url "$FRONTEND_URL/$(trim_url_path "$preview_js_path")")"
  preview_css="$(fetch_url "$FRONTEND_URL/$(trim_url_path "$preview_css_path")")"
  if [[ -z "$preview_js" || -z "$preview_css" ]]; then
    printf 'Could not fetch built preview assets.\n'
    exit 1
  fi
  if grep -Eq '@import["'\'']?\.?/styles/' <<<"$preview_css"; then
    printf 'Preview CSS still contains local ./styles/ @import; Vite did not inline a module stylesheet.\n'
    exit 1
  fi
  if ! grep -q '视频分析（口播）' <<<"$preview_js"; then
    printf 'Preview JS did not contain the Koubo shell route label.\n'
    exit 1
  fi
  printf 'preview entry assets loaded; module CSS is expected to live in lazy chunks\n'
else
  main_path="$(trim_url_path "$main_src")"
  printf 'index -> /%s\n' "$main_path"

  main_js="$(fetch_url "$FRONTEND_URL/$main_path")"
  app_path="$(extract_import_path "$main_js" 'src/App\.jsx')"
  if [[ -n "$app_path" ]]; then
    printf 'main -> /%s\n' "$(trim_url_path "$app_path")"
    app_js="$(fetch_url "$FRONTEND_URL/$(trim_url_path "$app_path")")"
    if grep -Eq "from [\"'][^\"']+[?]v=|import [\"'][^\"']+[?]v=" <<<"$main_js$app_js"; then
      printf 'Served main/App modules still contain static import query strings.\n'
      exit 1
    fi
    print_import_path "app -> controller" "$app_js" 'src/shell/useOpenCrewAppController\.jsx' || true
    shell_view_path="$(extract_import_path "$app_js" 'src/shell/OpenCrewShellView\.jsx')"
    if [[ -n "$shell_view_path" ]]; then
      printf 'app -> /%s\n' "$(trim_url_path "$shell_view_path")"
      shell_view_js="$(fetch_url "$FRONTEND_URL/$(trim_url_path "$shell_view_path")")"
      if grep -Eq "from [\"'][^\"']+[?]v=|import [\"'][^\"']+[?]v=" <<<"$shell_view_js"; then
        printf 'Served ShellView module still contains static import query strings.\n'
        exit 1
      fi
      analysis_path="$(extract_import_path "$shell_view_js" 'src/modules/koubo/AnalysisV1/AnalysisV1Module\.jsx')"
      koubo_task_list_path="$(extract_import_path "$shell_view_js" 'src/modules/koubo/KouboTaskList/index\.jsx')"
      dance_mimic_path="$(extract_import_path "$shell_view_js" 'src/modules/koubo/DanceMimicV1/DanceMimicV1Module\.jsx')"
      koubo_path="$(extract_import_path "$shell_view_js" 'src/modules/koubo/KouboStoryBoardModule\.jsx')"
      upload_asset_library_path="$(extract_import_path "$shell_view_js" 'src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage\.jsx')"
      auth_gate_path="$(extract_import_path "$shell_view_js" 'src/shell/AuthGate\.jsx')"
      print_import_path "shell -> AnalysisV1Module" "$shell_view_js" 'src/modules/koubo/AnalysisV1/AnalysisV1Module\.jsx' || true
      print_import_path "shell -> KouboTaskList" "$shell_view_js" 'src/modules/koubo/KouboTaskList/index\.jsx' || true
      print_import_path "shell -> DanceMimicV1Module" "$shell_view_js" 'src/modules/koubo/DanceMimicV1/DanceMimicV1Module\.jsx' || true
      print_import_path "shell -> KouboStoryBoardModule" "$shell_view_js" 'src/modules/koubo/KouboStoryBoardModule\.jsx' || true
      print_import_path "shell -> UploadAssetLibraryPage" "$shell_view_js" 'src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage\.jsx' || true
      print_import_path "shell -> AuthGate" "$shell_view_js" 'src/shell/AuthGate\.jsx' || true
      print_import_path "shell -> DebugConsole" "$shell_view_js" 'src/debug/DebugConsole\.jsx' || true
      if [[ -n "$analysis_path" ]]; then
        analysis_js="$(fetch_url "$FRONTEND_URL/$(trim_url_path "$analysis_path")")"
        if grep -Eq "from [\"'][^\"']+[?]v=|import [\"'][^\"']+[?]v=" <<<"$analysis_js"; then
          printf 'Served AnalysisV1 module still contains static import query strings.\n'
          exit 1
        fi
      fi
      if [[ -n "$koubo_path" ]]; then
        koubo_js="$(fetch_url "$FRONTEND_URL/$(trim_url_path "$koubo_path")")"
        if grep -Eq "from [\"'][^\"']+[?]v=|import [\"'][^\"']+[?]v=" <<<"$koubo_js"; then
          printf 'Served KouboStoryBoard module still contains static import query strings.\n'
          exit 1
        fi
      fi
      if [[ -n "$upload_asset_library_path" ]]; then
        upload_asset_library_js="$(fetch_url "$FRONTEND_URL/$(trim_url_path "$upload_asset_library_path")")"
        if grep -Eq "from [\"'][^\"']+[?]v=|import [\"'][^\"']+[?]v=" <<<"$upload_asset_library_js"; then
          printf 'Served UploadAssetLibrary module still contains static import query strings.\n'
          exit 1
        fi
      fi
      [[ -n "$analysis_path" && -n "$koubo_task_list_path" && -n "$dance_mimic_path" && -n "$koubo_path" && -n "$upload_asset_library_path" && -n "$auth_gate_path" ]]
    else
      printf 'app -> OpenCrewShellView import not found\n'
      exit 1
    fi
  else
    printf 'main -> App.jsx import not found\n'
    exit 1
  fi
fi

section "Koubo edit checklist"
printf 'Static import ?v= cache strings are retired; do not bump or re-add them.\n'
printf 'For App shell and Koubo edits, confirm the served entry chain resolves the expected module paths or built hashed assets.\n'
printf 'Runtime media URLs may still use ?v= as the raw-file cache switch; do not remove those.\n'
printf 'Validate against %s, not only by inspecting source files.\n' "$FRONTEND_URL"
