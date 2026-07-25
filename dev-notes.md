# OpenCrew Dev Notes

## API checklist

- `GET /api/health`
- `GET /api/setup/summary`
- `POST /api/setup/opencode/save`
- `POST /api/setup/opencode/check`
- `POST /api/setup/tunnel/check`
- `POST /api/setup/tunnel/start`
- `POST /api/setup/tunnel/stop`
- `GET /api/setup/tunnel/qrcode`
- `POST /api/setup/wecom/save`
- `POST /api/setup/wecom/verify`
- `GET /api/setup/verification/status`
- `POST /api/setup/verification/reset`
- `GET /webhooks/wecom`
- `POST /webhooks/wecom`

## UI checklist

- 3-column shell layout
- Left nav with icon + text (`Connection`)
- Center workflow with four step cards
- Right sidebar with health, endpoints, QR, events
