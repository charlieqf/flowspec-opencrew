#!/usr/bin/env bash
# 80 - 在 macmini-4 安装/卸载开机自启(launchd com.opencrew.boot)
# 登录时 RunAtLoad 调用 _boot_remote.sh,幂等拉起 PG/OpenCode/整栈/隧道。
# 用法: 80_launchd.sh            安装并加载(幂等)
#       80_launchd.sh --now      安装并立即触发一次 boot
#       80_launchd.sh --uninstall 卸载
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; source ./lib.sh
require_ssh
LABEL="com.opencrew.boot"
PLIST="$TEST_HOME/Library/LaunchAgents/$LABEL.plist"
BOOT="$TEST_REPO/scripts/deploy/_boot_remote.sh"
MODE="${1:-install}"

if [[ "$MODE" == "--uninstall" ]]; then
  log "卸载 $LABEL …"
  rt1 "launchctl bootout gui/$TEST_UID/$LABEL 2>/dev/null; rm -f '$PLIST'; echo done"
  exit 0
fi

log "确保 _boot_remote.sh 可执行并安装 $LABEL …"
rt1 "chmod +x '$BOOT'; mkdir -p '$TEST_HOME/Library/LaunchAgents'"

# 生成 plist 并写到 TEST
rt <<EOF
cat > "$PLIST" <<'PLISTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$BOOT</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>/tmp/opencrew-boot.log</string>
  <key>StandardErrorPath</key><string>/tmp/opencrew-boot.log</string>
</dict>
</plist>
PLISTEOF
# 重新加载
launchctl bootout gui/$TEST_UID/$LABEL 2>/dev/null || true
launchctl bootstrap gui/$TEST_UID "$PLIST"
launchctl enable gui/$TEST_UID/$LABEL
echo "installed: \$(launchctl print gui/$TEST_UID/$LABEL >/dev/null 2>&1 && echo ok || echo missing)"
EOF

if [[ "$MODE" == "--now" ]]; then
  log "立即触发一次 boot(kickstart)…"
  rt1 "launchctl kickstart -k gui/$TEST_UID/$LABEL"
  sleep 8
  log "boot 日志尾部:"
  rt1 "tail -12 /tmp/opencrew-boot.log 2>/dev/null"
fi
log "完成。重启 macmini-4 后将自动恢复整栈。"
