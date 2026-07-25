#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${OPENCREW_DATA_DIR:-$HOME/.opencrew}"
RUNTIME_DIR="${OPENCREW_ANALYSIS_V1_RUNTIME_DIR:-$DATA_DIR/runtimes/analysis_v1_py312}"
PYTHON312="${PYTHON312:-$(command -v python3.12 || true)}"

if [[ -z "$PYTHON312" ]]; then
  echo "python3.12 is required. Set PYTHON312=/path/to/python3.12 if it is not on PATH." >&2
  exit 1
fi

VERSION="$("$PYTHON312" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$VERSION" != "3.12" ]]; then
  echo "Analysis_V1 runtime must use Python 3.12, got $VERSION from $PYTHON312" >&2
  exit 1
fi

mkdir -p "$(dirname "$RUNTIME_DIR")"
if [[ ! -x "$RUNTIME_DIR/bin/python" ]]; then
  "$PYTHON312" -m venv "$RUNTIME_DIR"
fi

"$RUNTIME_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$RUNTIME_DIR/bin/python" -m pip install -r "$ROOT_DIR/ToolLibrary/Analysis_V1/requirements-runtime.txt"
PYTHONPATH="$ROOT_DIR/ToolLibrary/Analysis" "$RUNTIME_DIR/bin/python" "$ROOT_DIR/ToolLibrary/Analysis/install_media_binaries.py" --print-json >/dev/null

export OPENCREW_ANALYSIS_V1_RUNTIME_DIR="$RUNTIME_DIR"
export OPENCREW_ANALYSIS_V1_PYTHON="$RUNTIME_DIR/bin/python"
"$ROOT_DIR/scripts/check_analysis_v1_runtime.py" --python "$RUNTIME_DIR/bin/python"

cat <<EOF
Analysis_V1 runtime ready.
OPENCREW_ANALYSIS_V1_RUNTIME_DIR=$RUNTIME_DIR
OPENCREW_ANALYSIS_V1_PYTHON=$RUNTIME_DIR/bin/python
EOF
