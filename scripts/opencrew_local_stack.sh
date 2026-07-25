#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-start}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCREW_ROOT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BACKEND_DIR="${OPENCREW_BACKEND_DIR:-$ROOT_DIR/backend}"
FRONTEND_DIR="${OPENCREW_FRONTEND_DIR:-$ROOT_DIR/frontend}"

BACKEND_SCREEN="${OPENCREW_BACKEND_SCREEN:-opencrew-backend}"
FRONTEND_SCREEN="${OPENCREW_FRONTEND_SCREEN:-opencrew-frontend}"
BACKEND_LOG="${OPENCREW_BACKEND_LOG:-/tmp/opencrew-backend.log}"
FRONTEND_LOG="${OPENCREW_FRONTEND_LOG:-/tmp/opencrew-frontend.log}"

BACKEND_HOST="${OPENCREW_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${OPENCREW_BACKEND_PORT:-8011}"
FRONTEND_HOST="${OPENCREW_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${OPENCREW_FRONTEND_PORT:-18080}"
FRONTEND_MODE="${OPENCREW_FRONTEND_MODE:-preview}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"

DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew}"
OPENCREW_DATA_DIR="${OPENCREW_DATA_DIR:-$HOME/.opencrew}"
OPENCREW_ALLOW_PHASE0_ENV="${OPENCREW_ALLOW_PHASE0_ENV:-0}"
OPENCREW_MANAGE_POSTGRES="${OPENCREW_MANAGE_POSTGRES:-0}"
OPENCREW_PYTHON="${OPENCREW_PYTHON:-}"
BACKEND_PYTHON="${OPENCREW_BACKEND_PYTHON:-$BACKEND_DIR/.venv/bin/python}"
ANALYSIS_V1_PYTHON="${OPENCREW_ANALYSIS_V1_PYTHON:-$OPENCREW_DATA_DIR/runtimes/analysis_v1_py312/bin/python}"
OPENCREW_FFMPEG_PATH="${OPENCREW_FFMPEG_PATH:-$ROOT_DIR/ToolLibrary/.bin/ffmpeg}"
OPENCREW_FFPROBE_PATH="${OPENCREW_FFPROBE_PATH:-$ROOT_DIR/ToolLibrary/.bin/ffprobe}"
OPENCREW_PUBLIC_ASSETS_R2_ENV="${OPENCREW_PUBLIC_ASSETS_R2_ENV:-$OPENCREW_DATA_DIR/public_assets_r2.env}"

info() {
  printf '[opencrew] %s\n' "$*"
}

load_public_assets_r2_env() {
  if [[ ! -f "$OPENCREW_PUBLIC_ASSETS_R2_ENV" ]]; then
    return 0
  fi
  if [[ ! -r "$OPENCREW_PUBLIC_ASSETS_R2_ENV" ]]; then
    info "public assets R2 env exists but is not readable: $OPENCREW_PUBLIC_ASSETS_R2_ENV"
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$OPENCREW_PUBLIC_ASSETS_R2_ENV"
  set +a
  info "loaded public assets R2 env: $OPENCREW_PUBLIC_ASSETS_R2_ENV"
}

assert_not_phase0_env() {
  if [[ "$OPENCREW_ALLOW_PHASE0_ENV" == "1" ]]; then
    return 0
  fi
  local name value
  for name in OPENCREW_DATA_DIR OPENCREW_SECRET_STORE_PATH OPENCREW_SECRET_STORE_KEY OPENCREW_APP_PASSWORD OPENCREW_PG_DATA_DIR OPENCREW_PG_LOG_PATH; do
    value="${!name:-}"
    if [[ "$value" == *phase0* || "$value" == *opencrew-phase0* ]]; then
      info "refusing to start local stack with Phase 0 test environment: $name=$value"
      info "clear test env vars first, or set OPENCREW_ALLOW_PHASE0_ENV=1 only for an intentional test stack"
      return 1
    fi
  done
}

port_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

http_healthy() {
  local url="$1"
  curl -fsS -m 5 "$url" >/dev/null 2>&1
}

assert_runtime_paths() {
  local missing=0
  for path in "$BACKEND_DIR/main.py" "$FRONTEND_DIR/package.json"; do
    if [[ ! -e "$path" ]]; then
      info "missing required runtime path: $path"
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    return 1
  fi
}

listener_pids() {
  local port="$1"
  lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

listener_summary() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR == 1 || NR > 1 {print}'
}

stop_stale_listener() {
  local port="$1"
  local label="$2"
  local pids
  pids="$(listener_pids "$port")"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  info "stopping stale $label listener on $port: ${pids//$'\n'/ }"
  kill $pids 2>/dev/null || {
    info "failed to stop stale $label listener; stop it manually and rerun"
    return 1
  }
  local i
  for ((i = 1; i <= 10; i += 1)); do
    if ! port_listening "$port"; then
      return 0
    fi
    sleep 1
  done
  info "stale $label listener is still on $port"
  return 1
}

screen_exists() {
  local name="$1"
  local output
  output="$(screen -list 2>/dev/null || true)"
  printf '%s\n' "$output" | grep -q "[.]${name}"
}

stop_screen() {
  local name="$1"
  if screen_exists "$name"; then
    info "stopping screen session: $name"
    screen -S "$name" -X quit || true
  fi
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local attempts="${3:-30}"
  local delay="${4:-1}"
  local i
  for ((i = 1; i <= attempts; i += 1)); do
    if curl -fsS -m 5 "$url" >/dev/null 2>&1; then
      info "$label is healthy: $url"
      return 0
    fi
    sleep "$delay"
  done
  info "$label did not become healthy: $url"
  return 1
}

choose_python() {
  if [[ -n "$OPENCREW_PYTHON" ]]; then
    printf '%s\n' "$OPENCREW_PYTHON"
    return 0
  fi
  local candidate
  for candidate in python3.12 python3.13 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  info "missing Python; install Python 3.12 or set OPENCREW_PYTHON"
  return 1
}

ensure_backend_venv() {
  if [[ -x "$BACKEND_PYTHON" ]]; then
    return 0
  fi
  local python_bin
  python_bin="$(choose_python)"
  info "creating backend venv with $python_bin"
  "$python_bin" -m venv "$BACKEND_DIR/.venv"
  "$BACKEND_PYTHON" -m pip install --upgrade pip
  "$BACKEND_PYTHON" -m pip install -r "$BACKEND_DIR/requirements.txt"
}

analysis_v1_python_bin() {
  if [[ -x "$ANALYSIS_V1_PYTHON" ]]; then
    printf '%s\n' "$ANALYSIS_V1_PYTHON"
    return 0
  fi
  printf '%s\n' "$BACKEND_PYTHON"
}

analysis_v1_ocr_ready() {
  local python_bin
  python_bin="$(analysis_v1_python_bin)"
  "$python_bin" - "$ROOT_DIR" <<'PY' >/dev/null 2>&1
import importlib.util
import shutil
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
sys.path[:0] = [str(repo_root / "backend"), str(repo_root), str(repo_root.parent)]
has_python_ocr = any(
    importlib.util.find_spec(name) is not None
    for name in ("rapidocr_onnxruntime", "rapidocr", "paddleocr", "easyocr")
)
if not has_python_ocr and shutil.which("tesseract") is None:
    raise SystemExit(1)

try:
    import cv2  # noqa: F401
except Exception:
    raise SystemExit(1)

if importlib.util.find_spec("rapidocr_onnxruntime") is not None:
    from rapidocr_onnxruntime import RapidOCR  # noqa: F401
elif importlib.util.find_spec("rapidocr") is not None:
    from rapidocr import RapidOCR  # noqa: F401
PY
}

codesign_backend_native_extensions() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi
  if ! command -v codesign >/dev/null 2>&1; then
    return 0
  fi
  local site_packages
  site_packages="$("$BACKEND_PYTHON" - <<'PY'
import site
paths = site.getsitepackages()
print(paths[0] if paths else "")
PY
)"
  if [[ -z "$site_packages" || ! -d "$site_packages" ]]; then
    return 0
  fi
  find "$site_packages" -type f \( -name '*.so' -o -name '*.dylib' \) -print0 \
    | xargs -0 -n 1 codesign --force --sign - >/dev/null 2>&1 || true
}

ensure_analysis_v1_runtime_deps() {
  ensure_backend_venv
  local python_bin
  python_bin="$(analysis_v1_python_bin)"
  if analysis_v1_ocr_ready; then
    return 0
  fi
  local requirements="$ROOT_DIR/ToolLibrary/Analysis_V1/requirements-runtime.txt"
  if [[ ! -f "$requirements" ]]; then
    info "missing Analysis_V1 runtime requirements: $requirements"
    return 1
  fi
  info "installing Analysis_V1 runtime dependencies into $python_bin"
  "$python_bin" -m pip install -r "$requirements"
  codesign_backend_native_extensions
  if ! analysis_v1_ocr_ready; then
    info "Analysis_V1 OCR runtime is required but unavailable after installing $requirements"
    return 1
  fi
}

ensure_frontend_deps() {
  if [[ -d "$FRONTEND_DIR/node_modules" ]]; then
    return 0
  fi
  info "installing frontend dependencies"
  cd "$FRONTEND_DIR"
  npm install
}

start_postgres() {
  if [[ "$OPENCREW_MANAGE_POSTGRES" != "1" ]]; then
    info "PostgreSQL is externally managed; DATABASE_URL=$DATABASE_URL"
    return 0
  fi
  ensure_backend_venv
  "$BACKEND_PYTHON" "$ROOT_DIR/scripts/opencrew_postgres.py" start
}

start_backend() {
  ensure_backend_venv
  ensure_analysis_v1_runtime_deps
  if port_listening "$BACKEND_PORT"; then
    if http_healthy "$BACKEND_URL/api/health" && screen_exists "$BACKEND_SCREEN"; then
      info "backend already healthy on $BACKEND_PORT"
      return 0
    fi
    info "backend port $BACKEND_PORT is occupied but not managed/healthy"
    stop_stale_listener "$BACKEND_PORT" "backend"
  fi
  stop_screen "$BACKEND_SCREEN"
  : > "$BACKEND_LOG"
  local analysis_python
  analysis_python="$(analysis_v1_python_bin)"
  info "starting backend in screen: $BACKEND_SCREEN"
  screen -dmS "$BACKEND_SCREEN" /bin/zsh -lc \
    "cd '$BACKEND_DIR' && unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy && export DATABASE_URL='$DATABASE_URL' OPENCREW_DATA_DIR='$OPENCREW_DATA_DIR' OPENCREW_BACKEND_HOST='$BACKEND_HOST' OPENCREW_BACKEND_PORT='$BACKEND_PORT' OPENCREW_ANALYSIS_V1_PYTHON='$analysis_python' OPENCREW_FFMPEG_PATH='$OPENCREW_FFMPEG_PATH' OPENCREW_FFPROBE_PATH='$OPENCREW_FFPROBE_PATH' && unset OPENCREW_BACKEND_RELOAD && exec '$BACKEND_PYTHON' main.py >> '$BACKEND_LOG' 2>&1"
  wait_for_http "$BACKEND_URL/api/health" "backend"
}

start_frontend() {
  ensure_frontend_deps
  if [[ "$FRONTEND_MODE" != "dev" && "$FRONTEND_MODE" != "preview" ]]; then
    info "unsupported OPENCREW_FRONTEND_MODE=$FRONTEND_MODE; expected dev or preview"
    return 1
  fi
  if [[ "$FRONTEND_MODE" == "preview" ]]; then
    info "building frontend for preview mode"
    (cd "$FRONTEND_DIR" && OPENCREW_BACKEND_URL="$BACKEND_URL" OPENCREW_FRONTEND_HOST="$FRONTEND_HOST" OPENCREW_FRONTEND_PORT="$FRONTEND_PORT" npm run build)
  fi
  if port_listening "$FRONTEND_PORT"; then
    if http_healthy "$FRONTEND_URL/" && screen_exists "$FRONTEND_SCREEN"; then
      info "frontend already healthy on $FRONTEND_PORT"
      return 0
    fi
    info "frontend port $FRONTEND_PORT is occupied but not managed/healthy"
    stop_stale_listener "$FRONTEND_PORT" "frontend"
  fi
  stop_screen "$FRONTEND_SCREEN"
  : > "$FRONTEND_LOG"
  info "starting frontend in screen: $FRONTEND_SCREEN ($FRONTEND_MODE)"
  local frontend_command
  if [[ "$FRONTEND_MODE" == "preview" ]]; then
    frontend_command="npm run preview -- --host '$FRONTEND_HOST' --port '$FRONTEND_PORT'"
  else
    frontend_command="npm run dev"
  fi
  screen -dmS "$FRONTEND_SCREEN" /bin/zsh -lc \
    "cd '$FRONTEND_DIR' && export OPENCREW_BACKEND_URL='$BACKEND_URL' OPENCREW_FRONTEND_HOST='$FRONTEND_HOST' OPENCREW_FRONTEND_PORT='$FRONTEND_PORT' && exec $frontend_command >> '$FRONTEND_LOG' 2>&1"
  wait_for_http "$FRONTEND_URL/" "frontend"
}

start_stack() {
  assert_runtime_paths
  load_public_assets_r2_env
  start_postgres
  start_backend
  start_frontend
  wait_for_http "$FRONTEND_URL/api/auth/status" "frontend proxy"
  info "ready"
  info "frontend: $FRONTEND_URL/"
  info "backend:  $BACKEND_URL"
  info "database: $DATABASE_URL"
  info "data dir: $OPENCREW_DATA_DIR"
  info "logs:     $BACKEND_LOG / $FRONTEND_LOG"
}

stop_stack() {
  stop_screen "$FRONTEND_SCREEN"
  stop_screen "$BACKEND_SCREEN"
  info "stopped OpenCrew frontend/backend screen sessions"
  if [[ "$OPENCREW_MANAGE_POSTGRES" == "1" ]]; then
    info "PostgreSQL is intentionally left running; stop with scripts/opencrew_postgres.py stop"
  fi
}

status_stack() {
  info "data dir: $OPENCREW_DATA_DIR"
  if [[ "$OPENCREW_MANAGE_POSTGRES" == "1" ]]; then
    ensure_backend_venv
    "$BACKEND_PYTHON" "$ROOT_DIR/scripts/opencrew_postgres.py" status || true
  else
    info "PostgreSQL: externally managed"
  fi
  if port_listening "$BACKEND_PORT"; then
    info "backend: listening on $BACKEND_PORT"
  else
    info "backend: down on $BACKEND_PORT"
  fi
  if port_listening "$FRONTEND_PORT"; then
    info "frontend: listening on $FRONTEND_PORT"
  else
    info "frontend: down on $FRONTEND_PORT"
  fi
  if screen_exists "$BACKEND_SCREEN"; then
    info "screen: $BACKEND_SCREEN exists"
  else
    info "screen: $BACKEND_SCREEN missing"
  fi
  if screen_exists "$FRONTEND_SCREEN"; then
    info "screen: $FRONTEND_SCREEN exists"
  else
    info "screen: $FRONTEND_SCREEN missing"
  fi
}

doctor_stack() {
  assert_runtime_paths || true
  status_stack
  info "listener detail: 5433"
  listener_summary 5433 || true
  info "listener detail: 8011"
  listener_summary 8011 || true
  info "listener detail: 18080"
  listener_summary 18080 || true
  info "backend health: $BACKEND_URL/api/health"
  if http_healthy "$BACKEND_URL/api/health"; then
    info "backend health check passed"
  else
    info "backend health check failed"
  fi
  info "frontend proxy health: $FRONTEND_URL/api/setup/summary"
  if http_healthy "$FRONTEND_URL/api/setup/summary"; then
    info "frontend proxy health check passed"
  else
    info "frontend proxy health check failed"
  fi
  info "backend log: $BACKEND_LOG"
  tail -20 "$BACKEND_LOG" 2>/dev/null || true
  info "frontend log: $FRONTEND_LOG"
  tail -20 "$FRONTEND_LOG" 2>/dev/null || true
}

case "$ACTION" in
  start)
    assert_not_phase0_env
    start_stack
    ;;
  stop)
    stop_stack
    ;;
  restart)
    assert_not_phase0_env
    stop_stack
    start_stack
    ;;
  status)
    status_stack
    ;;
  doctor)
    doctor_stack
    ;;
  *)
    printf 'Usage: %s {start|stop|restart|status|doctor}\n' "$0" >&2
    exit 2
    ;;
esac
