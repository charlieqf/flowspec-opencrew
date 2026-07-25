# OpenCrew

OpenCrew is a local desktop bridge built with:

- Tauri + Vite + TypeScript + SolidJS (desktop UI shell)
- FastAPI + Python + PostgreSQL (deployable control backend)

This first version ships a single main module: `Connection`.

## Workflow implemented

1. Step 1: Detect local OpenCode
2. Step 2: Detect/start local tunnel (`cloudflared`) and expose URL + QR
3. Step 3: Configure WeCom callback settings
4. Step 4: Send WeCom message to verify end-to-end bridge

For the current local startup playbook, read this first:

- `CORRECT_STARTUP_PLAYBOOK.md`

## Project structure

```text
OpenCrew/
  backend/
    main.py
    requirements.txt
    opcrew_backend/
      app.py
      config.py
      db/
      repositories/
      storage/
      adapters/
  frontend/
    index.html
    package.json
    vite.config.ts
    src/
    src-tauri/
```

## Run backend

```bash
cd OpenCrew/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Backend default URL: `http://127.0.0.1:8011`

Backend reload is off by default for stable local runs. Set `OPENCREW_BACKEND_RELOAD=1` only when actively editing backend code.

For this macmini's full local service map, health checks, logs, and restart notes, see `docs/opencrew_local_services.md`. Do not assume the app is down just because common dev ports such as `5173`, `8000`, or `3000` are unused; the local stack normally uses backend `8011` and frontend `18080`.

## OpenCode service for OpenCrew

OpenCrew needs a reachable OpenCode HTTP service before running OpenClip/OpenFlow/OC-Rebuild tasks.

Recommended local mode is OpenCode CLI with stable Basic Auth environment variables:

```bash
nohup env OPENCODE_SERVER_USERNAME="opencode" \
  OPENCODE_SERVER_PASSWORD="<stable-local-password>" \
  "/Users/duheng/.opencode/bin/opencode" serve \
  --hostname 127.0.0.1 \
  --port 4096 \
  > "/tmp/opencrew-opencode-cli-4096.log" 2>&1 &
```

Verify:

```bash
curl -i -m 10 -u "opencode:<stable-local-password>" http://127.0.0.1:4096/global/health
curl -i -m 10 -X POST http://127.0.0.1:8011/api/setup/opencode/discover
curl -s -m 10 http://127.0.0.1:8011/api/setup/summary
```

Expected summary when CLI auth is discoverable:

- `summary.opencode.base_url = http://127.0.0.1:4096`
- `summary.opencode.auth_username = opencode`
- `summary.opencode.auth_source = process_env`
- `summary.opencode.status = ready`

OpenCode Desktop can also be discovered, but Desktop generates a fresh local server password at app startup and passes it through Electron IPC. That password is not a stable external config value and may not be readable by OpenCrew after a Desktop or machine restart. If discovery shows HTTP `401`, `auth_required`, or empty credentials for Desktop, use the CLI server with `OPENCODE_SERVER_PASSWORD` or manually provide the current Desktop credentials.

After any restart:

- restart the CLI server with the same `OPENCODE_SERVER_PASSWORD`, or restart Desktop and expect a new port/password
- rerun `POST /api/setup/opencode/discover`
- verify `summary.opencode.status = ready` before starting OpenCrew tasks

## PostgreSQL for OpenCrew

Current local development uses the existing PostgreSQL instance on `127.0.0.1:5433`.

Default local runtime:

- Host: `127.0.0.1`
- Port: `5433`
- User: `opencrew`
- Password: `opencrew` (local development default; override with your own `DATABASE_URL`)
- Database: `opencrew`

Use this backend URL:

```bash
export DATABASE_URL='postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew'
```

Backend startup:

```bash
cd OpenCrew/backend
nohup env DATABASE_URL='postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew' python3 main.py > /tmp/opencrew-backend.log 2>&1 &
```

Do not use the standalone PostgreSQL helper for normal local startup. It creates a separate database and will make the app show different data.

Required environment variables for backend:

```bash
export DATABASE_URL='postgresql+psycopg://<user>:<password>@<host>:5433/<database>'
export OPENCREW_DATA_DIR="$HOME/.opencrew"
```

For the normal local stack, prefer:

```bash
OPENCREW_DATA_DIR="$HOME/.opencrew" \
DATABASE_URL='postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew' \
scripts/opencrew_local_stack.sh restart
```

Do not start the normal local stack with Phase 0 test variables such as
`OPENCREW_DATA_DIR=/private/tmp/opencrew-phase0-data` or
`OPENCREW_SECRET_STORE_PATH=/private/tmp/opencrew-phase0-secrets.enc`; those
point provider keys at the isolated test secret store.

Optional frontend/backend deployment overrides:

```bash
export OPENCREW_FRONTEND_URL='http://127.0.0.1:18080/'
export OPENCREW_BACKEND_URL='http://127.0.0.1:8011'
```

## Deprecated Standalone PostgreSQL Helper

`scripts/opencrew_postgres.py` is retained for isolated database experiments only. It should not be used for the current local development data.

## Run frontend (web)

```bash
cd OpenCrew/frontend
npm install
npm run dev
```

Frontend default URL: `http://127.0.0.1:18080`

## Run Tauri desktop shell

```bash
cd OpenCrew/frontend
npm install
npm run tauri dev
```

## Notes

- The tunnel adapter currently uses `cloudflared tunnel --url ...` and parses `trycloudflare.com` URL.
- WeCom webhook GET handshake is implemented. POST message route is implemented with XML parsing and test reply.
- Step 1 validates OpenCode reachability through the discovered HTTP server and its Basic Auth credentials.
