#!/usr/bin/env bash
# One-shot: configure MiniMax (Hailuo) as the active Voice Clone provider.
#
# This is "Option A": it persists the MiniMax API key (into the secret store)
# and the GroupId (into the provider extra_json) on a *running* backend, via the
# media-model config save API that now accepts a per-provider `extra` object.
#
# Secrets are read from the environment and are NOT written to this file.
#
# Usage:
#   MINIMAX_KEY='sk-api-...' \
#   MINIMAX_GROUP_ID='<your-minimax-group-id>' \
#   BACKEND_URL='http://localhost:8000' \
#   bash scripts/configure_minimax_voice_clone.sh
#
# Notes:
#   - BACKEND_URL must point at the running backend.
#   - The config kind is "voice-clone"; model is minimax-voice-clone-v1.
#   - Rotate the API key afterwards if it has been exposed.
set -euo pipefail

: "${MINIMAX_KEY:?Set MINIMAX_KEY to your MiniMax API key}"
: "${MINIMAX_GROUP_ID:?Set MINIMAX_GROUP_ID to your MiniMax account GroupId}"
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"

# Try both known mount points (top-level setup router and ModelConfig router).
PATHS=(
  "/api/setup/media-models/voice-clone/config"
  "/api/model-config/voice-clone/config"
)

read -r -d '' BODY <<JSON || true
{
  "active_provider": "minimax",
  "providers": [
    {
      "provider": "minimax",
      "model": "minimax-voice-clone-v1",
      "api_key": "${MINIMAX_KEY}",
      "enabled": true,
      "extra": { "group_id": "${MINIMAX_GROUP_ID}", "base_url": "https://api.minimaxi.com", "tts_model": "speech-02-hd" }
    }
  ]
}
JSON

for p in "${PATHS[@]}"; do
  echo "PUT ${BACKEND_URL}${p}"
  code=$(curl -s -o /tmp/minimax_cfg_resp.json -w "%{http_code}" -X PUT \
    -H "Content-Type: application/json" \
    --data "${BODY}" \
    "${BACKEND_URL}${p}" || echo "000")
  echo "  -> HTTP ${code}"
  if [ "${code}" = "200" ]; then
    echo "  OK. Response:"
    cat /tmp/minimax_cfg_resp.json | sed 's/.\{200\}/&\n/g' | head -n 5
    rm -f /tmp/minimax_cfg_resp.json
    exit 0
  fi
done

echo "Failed to configure via known paths. Last response:" >&2
cat /tmp/minimax_cfg_resp.json 2>/dev/null >&2 || true
rm -f /tmp/minimax_cfg_resp.json
exit 1
