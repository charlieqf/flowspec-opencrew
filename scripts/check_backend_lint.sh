#!/usr/bin/env bash
set -euo pipefail

ruff_bin="${RUFF_BIN:-ruff}"
if ! command -v "$ruff_bin" >/dev/null 2>&1 && [ -x "backend/.venv/bin/ruff" ]; then
  ruff_bin="backend/.venv/bin/ruff"
fi

"$ruff_bin" check backend/opcrew_backend backend/scripts ModelConfig/backend WorkflowAssistant/backend scripts
