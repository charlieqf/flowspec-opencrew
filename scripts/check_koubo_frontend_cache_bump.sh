#!/usr/bin/env bash
set -euo pipefail

printf '[opencrew] static import cache-bump guard retired; Vite hashed assets plus immutable cache headers now own frontend cache freshness.\n'
exit 0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCREW_ROOT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
MODE="worktree"
BASE_REF=""
HEAD_REF="HEAD"

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/check_koubo_frontend_cache_bump.sh
  scripts/check_koubo_frontend_cache_bump.sh --staged
  scripts/check_koubo_frontend_cache_bump.sh --base <ref> [--head <ref>]

Default mode checks unstaged, staged, and untracked local changes.
--staged is intended for git pre-commit hooks.
--base is intended for CI and checks <base>...<head>.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --staged)
      MODE="staged"
      shift
      ;;
    --base)
      if [[ $# -lt 2 ]]; then
        usage
        exit 2
      fi
      MODE="base"
      BASE_REF="$2"
      shift 2
      ;;
    --head)
      if [[ $# -lt 2 ]]; then
        usage
        exit 2
      fi
      HEAD_REF="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

cd "$ROOT_DIR"

changed_files() {
  case "$MODE" in
    staged)
      git diff --name-only --cached | sort -u
      ;;
    base)
      git diff --name-only "${BASE_REF}...${HEAD_REF}" | sort -u
      ;;
    worktree)
      {
        git diff --name-only
        git diff --name-only --cached
        git ls-files --others --exclude-standard
      } | sort -u
      ;;
  esac
}

has_changed_path() {
  local pattern="$1"
  printf '%s\n' "$CHANGED_FILES" | grep -Eq "$pattern"
}

file_diff() {
  local file="$1"
  case "$MODE" in
    staged)
      git diff --cached -- "$file"
      ;;
    base)
      git diff "${BASE_REF}...${HEAD_REF}" -- "$file"
      ;;
    worktree)
      {
        git diff -- "$file"
        git diff --cached -- "$file"
        if git ls-files --others --exclude-standard -- "$file" | grep -q .; then
          sed 's/^/+/' "$file"
        fi
      }
      ;;
  esac
}

has_version_diff() {
  local file="$1"
  file_diff "$file" | grep -E '^[+-].*\?v=' >/dev/null
}

escape_ere() {
  printf '%s' "$1" | sed -e 's/[][(){}.^$*+?|\\]/\\&/g'
}

has_version_diff_for_literal() {
  local file="$1"
  local literal="$2"
  local escaped
  escaped="$(escape_ere "$literal")"
  file_diff "$file" | grep -E "^[+-].*[\"']${escaped}\\?v=" >/dev/null
}

if [[ "$MODE" == "base" && -z "$BASE_REF" ]]; then
  usage
  exit 2
fi

CHANGED_FILES="$(changed_files)"
KOUBO_FRONTEND_PATTERN='^frontend/src/modules/koubo/'
APP_SHELL_PATTERN='^(frontend/index\.html|frontend/src/main\.tsx|frontend/src/App\.jsx|frontend/src/(lib|pages|components|debug|shell)/)'

if ! printf '%s\n' "$CHANGED_FILES" | grep -Eq "($KOUBO_FRONTEND_PATTERN|$APP_SHELL_PATTERN)"; then
  printf '[opencrew] no Koubo/App shell frontend changes detected (%s mode)\n' "$MODE"
  exit 0
fi

missing=()

require_version_diff() {
  local file="$1"
  local reason="$2"
  if ! has_version_diff "$file"; then
    missing+=("$file ($reason)")
  fi
}

require_versioned_import_diff() {
  local file="$1"
  local import_literal="$2"
  local reason="$3"
  if ! has_version_diff_for_literal "$file" "$import_literal"; then
    missing+=("$file ($reason: $import_literal)")
  fi
}

require_app_import_diff_for_changed_app_modules() {
  local path
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    case "$path" in
      frontend/src/pages/MeteringPage.jsx)
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../pages/MeteringPage.jsx" "MeteringPage cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/pages/ConnectionPage.jsx)
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../pages/ConnectionPage.jsx" "ConnectionPage cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/shell/controllers/*.js|frontend/src/shell/controllers/*.jsx|frontend/src/shell/controllers/*.ts|frontend/src/shell/controllers/*.tsx)
        controller_import="./${path#frontend/src/shell/}"
        require_versioned_import_diff "frontend/src/shell/useOpenCrewAppController.jsx" "$controller_import" "domain controller cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/useOpenCrewAppController.jsx" "App controller cache string"
        ;;
      frontend/src/shell/useOpenCrewAppController.jsx)
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/useOpenCrewAppController.jsx" "App controller cache string"
        ;;
      frontend/src/shell/OpenCrewShellView.jsx)
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/shell/AuthGate.jsx)
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "./AuthGate.jsx" "AuthGate cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/shell/SettingsDrawers.jsx)
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "./SettingsDrawers.jsx" "SettingsDrawers cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/shell/AppRightSidebar.jsx)
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "./AppRightSidebar.jsx" "AppRightSidebar cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/shell/ShellDialogs.jsx)
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "./ShellDialogs.jsx" "ShellDialogs cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/shell/appShellUtils.jsx)
        require_versioned_import_diff "frontend/src/shell/useOpenCrewAppController.jsx" "./appShellUtils.jsx" "appShellUtils controller cache string"
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "./appShellUtils.jsx" "appShellUtils view cache string"
        require_versioned_import_diff "frontend/src/pages/MeteringPage.jsx" "../shell/appShellUtils.jsx" "appShellUtils metering page cache string"
        require_versioned_import_diff "frontend/src/pages/ConnectionPage.jsx" "../shell/appShellUtils.jsx" "appShellUtils connection page cache string"
        require_versioned_import_diff "frontend/src/shell/SettingsDrawers.jsx" "./appShellUtils.jsx" "appShellUtils settings drawer cache string"
        require_versioned_import_diff "frontend/src/shell/AppRightSidebar.jsx" "./appShellUtils.jsx" "appShellUtils right sidebar cache string"
        require_versioned_import_diff "frontend/src/shell/ShellDialogs.jsx" "./appShellUtils.jsx" "appShellUtils shell dialogs cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/useOpenCrewAppController.jsx" "App controller cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/lib/api.ts)
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/useOpenCrewAppController.jsx" "App controller cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../pages/ConnectionPage.jsx" "ConnectionPage cache string"
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../debug/DebugConsole.jsx" "DebugConsole cache string"
        ;;
      frontend/src/lib/meteringFormat.js)
        require_versioned_import_diff "frontend/src/shell/useOpenCrewAppController.jsx" "../lib/meteringFormat.js" "meteringFormat controller cache string"
        require_versioned_import_diff "frontend/src/pages/MeteringPage.jsx" "../lib/meteringFormat.js" "meteringFormat page cache string"
        require_versioned_import_diff "frontend/src/shell/SettingsDrawers.jsx" "../lib/meteringFormat.js" "meteringFormat settings drawer cache string"
        require_versioned_import_diff "frontend/src/shell/AppRightSidebar.jsx" "../lib/meteringFormat.js" "meteringFormat right sidebar cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/useOpenCrewAppController.jsx" "App controller cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/debug/DebugConsole.jsx)
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../debug/DebugConsole.jsx" "DebugConsole cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
      frontend/src/debug/debugAdapter.js)
        require_versioned_import_diff "frontend/src/shell/useOpenCrewAppController.jsx" "../debug/debugAdapter.js" "debugAdapter controller cache string"
        require_versioned_import_diff "frontend/src/debug/DebugConsole.jsx" "./debugAdapter.js" "debugAdapter debug console cache string"
        require_versioned_import_diff "frontend/src/modules/koubo/AnalysisV1/components/AnalysisV1TTSBuilder.jsx" "../../../../debug/debugAdapter.js" "debugAdapter AnalysisV1 TTS builder cache string"
        require_versioned_import_diff "frontend/src/modules/koubo/KouboStoryBoard/components/KouboComposerModal.jsx" "../../../../debug/debugAdapter.js" "debugAdapter storyboard composer cache string"
        require_versioned_import_diff "frontend/src/modules/koubo/UploadAssetLibrary/digitalHuman/DigitalHumanAgentPanel.jsx" "../../../../debug/debugAdapter.js" "debugAdapter digital human agent cache string"
        require_versioned_import_diff "frontend/src/modules/koubo/UploadAssetLibrary/searchAgent/SearchAgentWorkspace.jsx" "../../../../debug/debugAdapter.js" "debugAdapter search agent cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/useOpenCrewAppController.jsx" "App controller cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../debug/DebugConsole.jsx" "DebugConsole cache string"
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../modules/koubo/AnalysisV1/AnalysisV1Module.jsx" "AnalysisV1 cache string"
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../modules/koubo/KouboStoryBoardModule.jsx" "KouboStoryBoardModule cache string"
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage.jsx" "UploadAssetLibrary cache string"
        ;;
      frontend/src/debug/debugStore.js)
        require_versioned_import_diff "frontend/src/debug/debugAdapter.js" "./debugStore.js" "debugStore adapter cache string"
        require_versioned_import_diff "frontend/src/debug/DebugConsole.jsx" "./debugStore.js" "debugStore debug console cache string"
        require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../debug/DebugConsole.jsx" "DebugConsole cache string"
        require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
        ;;
    esac
  done < <(printf '%s\n' "$CHANGED_FILES" | grep -E '^frontend/src/(lib|pages|debug|shell)/.*\.(js|jsx|ts|tsx)$' || true)
}

if has_changed_path '^frontend/src/modules/koubo/(KouboStoryBoard/|KouboStoryBoardModule\.jsx$)'; then
  require_version_diff "frontend/index.html" "Koubo served entry cache string"
  require_version_diff "frontend/src/main.tsx" "Koubo App import cache string"
  require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
  require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../modules/koubo/KouboStoryBoardModule.jsx" "KouboStoryBoardModule cache string"
  require_version_diff "frontend/src/modules/koubo/KouboStoryBoardModule.jsx" "Koubo nested import cache strings"
fi

if has_changed_path '^frontend/src/modules/koubo/KouboStoryBoard/components/KouboComposerModal\.jsx$'; then
  require_versioned_import_diff "frontend/src/modules/koubo/KouboStoryBoardModule.jsx" "./KouboStoryBoard/components/KouboComposerModal.jsx" "KouboComposerModal cache string"
fi

if has_changed_path "$APP_SHELL_PATTERN"; then
  require_versioned_import_diff "frontend/index.html" "./src/main.tsx" "App shell served entry cache string"
  require_versioned_import_diff "frontend/src/main.tsx" "./App.jsx" "App shell import cache string"
fi

if has_changed_path '^frontend/src/(lib|pages|debug|shell)/'; then
  require_app_import_diff_for_changed_app_modules
fi

if has_changed_path '^frontend/src/modules/koubo/AnalysisV1/'; then
  require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
  require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../modules/koubo/AnalysisV1/AnalysisV1Module.jsx" "AnalysisV1 cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/AnalysisV1/analysisV1Api'; then
  require_versioned_import_diff "frontend/src/modules/koubo/AnalysisV1/AnalysisV1Module.jsx" "./analysisV1Api" "analysisV1Api cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/AnalysisV1/components/AnalysisV1TTSBuilder\.jsx$'; then
  require_versioned_import_diff "frontend/src/modules/koubo/AnalysisV1/AnalysisV1Module.jsx" "./components/AnalysisV1TTSBuilder.jsx" "AnalysisV1TTSBuilder cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/styles\.css$'; then
  require_versioned_import_diff "frontend/src/modules/koubo/AnalysisV1/AnalysisV1Module.jsx" "../styles.css" "AnalysisV1 styles cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/UploadAssetLibrary/'; then
  require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
  require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage.jsx" "UploadAssetLibrary cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay\.jsx$'; then
  require_versioned_import_diff "frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage.jsx" "./UploadAssetLibraryOverlay.jsx" "UploadAssetLibraryOverlay cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/UploadAssetLibrary/digitalHuman/DigitalHumanAgentPanel\.jsx$'; then
  require_versioned_import_diff "frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx" "./digitalHuman/DigitalHumanAgentPanel.jsx" "DigitalHumanAgentPanel cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/UploadAssetLibrary/searchAgent/SearchAgentWorkspace\.jsx$'; then
  require_versioned_import_diff "frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx" "./searchAgent/SearchAgentWorkspace.jsx" "SearchAgentWorkspace overlay cache string"
  require_versioned_import_diff "frontend/src/modules/koubo/UploadAssetLibrary/searchAgent/SearchAgentPanel.jsx" "./SearchAgentWorkspace.jsx" "SearchAgentWorkspace panel cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/UploadAssetLibrary/searchAgent/SearchAgentPanel\.jsx$'; then
  require_versioned_import_diff "frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx" "./searchAgent/SearchAgentPanel.jsx" "SearchAgentPanel cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/KouboTaskList/'; then
  require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
  require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../modules/koubo/KouboTaskList/index.jsx" "KouboTaskList cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/DanceMimicV1/'; then
  require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
  require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../modules/koubo/DanceMimicV1/DanceMimicV1Module.jsx" "DanceMimicV1 cache string"
fi

if has_changed_path '^frontend/src/modules/koubo/shared/'; then
  require_versioned_import_diff "frontend/src/App.jsx" "./shell/OpenCrewShellView.jsx" "App shell view cache string"
  require_versioned_import_diff "frontend/src/shell/OpenCrewShellView.jsx" "../modules/koubo/shared/StoryboardIcon.jsx" "shared StoryboardIcon cache string"
fi

if ((${#missing[@]})); then
  printf '[opencrew] Koubo frontend changed, but cache bumps are missing:\n' >&2
  printf '  - %s\n' "${missing[@]}" >&2
  printf '\nRun the relevant bump command, for Koubo:\n' >&2
  printf '  scripts/bump_koubo_frontend_cache_version.sh <version-slug>\n' >&2
  printf 'Then verify the real served chain with:\n' >&2
  printf '  scripts/opencrew_frontend_preflight.sh\n' >&2
  exit 1
fi

printf '[opencrew] Koubo frontend cache bump check passed\n'
