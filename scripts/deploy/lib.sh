#!/usr/bin/env bash
# 共享函数 - 在 source 00_config.env 之后被各步骤 source
set -euo pipefail

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$_LIB_DIR/00_config.env" ]]; then
  printf '[deploy] missing %s/00_config.env; copy 00_config.env.example and fill local values\n' "$_LIB_DIR" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$_LIB_DIR/00_config.env"

if [[ -t 1 ]]; then c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_rst=$'\033[0m'
else c_red=""; c_grn=""; c_ylw=""; c_rst=""; fi
log()  { printf '%s[deploy]%s %s\n' "$c_grn" "$c_rst" "$*"; }
warn() { printf '%s[deploy]%s %s\n' "$c_ylw" "$c_rst" "$*" >&2; }
die()  { printf '%s[deploy]%s %s\n' "$c_red" "$c_rst" "$*" >&2; exit 1; }

TEST_SSH=(ssh -o ConnectTimeout=8 -o BatchMode=yes "$TEST_HOST")

# rt:把 stdin 的 bash 脚本送到 TEST 执行,登录环境 + Homebrew/bun 在 PATH。
# 本地(PROD)会先做变量展开,所以 $TEST_DATA 等会被注入;要用 TEST 本地变量请写 \$VAR。
rt() { "${TEST_SSH[@]}" 'export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$HOME/.bun/bin:$PATH"; bash -s'; }

# rt1:执行单行命令
rt1() { printf '%s\n' "$*" | rt; }

require_ssh() { "${TEST_SSH[@]}" true 2>/dev/null || die "无法 SSH 到 $TEST_HOST(检查机器是否在线/公钥)"; }

# TEST 上某 TCP 端口是否在监听
test_port_listening() { rt1 "lsof -nP -iTCP:$1 -sTCP:LISTEN >/dev/null 2>&1 && echo yes || echo no"; }
