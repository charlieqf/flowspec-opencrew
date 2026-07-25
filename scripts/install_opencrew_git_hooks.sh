#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCREW_ROOT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

cd "$ROOT_DIR"
git config core.hooksPath .githooks
printf '[opencrew] git hooks enabled: core.hooksPath=.githooks\n'
