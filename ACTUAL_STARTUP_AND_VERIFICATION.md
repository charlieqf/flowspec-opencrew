# OpenCrew Actual Startup And Verification

> Deprecated: this file is kept as a historical verification log only.
> Use `CORRECT_STARTUP_PLAYBOOK.md` as the single current startup runbook.
> Do not update operational startup steps in this file; move durable corrections into the playbook instead.

## Scope

This document records the actual startup order and the practical verification steps for `OpenCrew` in the current local environment.

It covers:

- the existing PostgreSQL instance for `OpenCrew`
- local OpenCode CLI/Desktop service startup and auth discovery
- `OpenCrew` backend
- `OpenCrew` frontend
- API-level verification
- OpenFlow / Session verification points

It does not cover data migration.

## Actual Runtime Layout

### OpenCrew local data

- Business data root: `~/.opencrew`
- Session files: `~/.opencrew/sessions`
- NPC files: `~/.opencrew/npc`
- Local binaries: `~/.opencrew/bin`

### PostgreSQL

- Host: `127.0.0.1`
- Port: `5433`
- User: `opencrew`
- Password: `opencrew`
- Database: `opencrew`
- Data directory: `/Users/duheng/.opencrew/postgres`
- Backend URL: `postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew`

### Backend

- Project path: `OpenCrew/backend`
- Entry: `main.py`
- URL: `http://127.0.0.1:8011`
- Log file when started with `nohup`: `/tmp/opencrew-backend.log`

### Frontend

- Project path: `OpenCrew/frontend`
- Dev server: `vite`
- Actual URL: `http://127.0.0.1:18080`
- Log file when started with `nohup`: `/tmp/opencrew-frontend.log`

Note:

- `README.md` still contains an old default frontend URL example `5188`
- actual configured frontend port is `18080`

### OpenCode service

OpenCrew needs an OpenCode HTTP service for provider/model discovery and task execution.

Preferred local service for OpenCrew automation:

- CLI server on `127.0.0.1:4096`
- username: `opencode`
- password source: `OPENCODE_SERVER_PASSWORD` in the CLI server process environment
- OpenCrew auth source after discovery: `process_env`

Desktop service behavior:

- `OpenCode.app` starts its own local server on a dynamic localhost port
- username is `opencode`
- password is generated at Desktop startup and passed to the renderer through Electron IPC
- the generated password is not reliably readable by another process after startup
- after Desktop restarts, both port and password can change, so OpenCrew must discover again and may need manual credential input if discovery cannot read the password

## Startup Order

Always start services in this order:

1. local standalone PostgreSQL on `127.0.0.1:5433` using `opencrew` / `opencrew`
2. intended OpenCode service, preferably CLI with fixed env auth
3. backend
4. frontend

## Full Restart After Operating System Update

Use this path after a macOS update, machine reboot, or any restart where local service processes may have been killed. This is stronger than the daily OpenCrew restart because it explicitly restores the OpenCode CLI service before asking OpenCrew to rediscover it.

### 1. Restore OpenCrew backend/frontend/PostgreSQL stack

Run the managed stack script from the OpenCrew project root:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
scripts/opencrew_local_stack.sh restart
```

Expected successful output includes:

```text
PostgreSQL is healthy: 127.0.0.1:5433/opencrew
backend is healthy: http://127.0.0.1:8011/api/health
frontend is healthy: http://127.0.0.1:18080/
frontend proxy is healthy: http://127.0.0.1:18080/api/setup/summary
ready
```

The stack must run against `postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew`. If PostgreSQL is down after an OS restart, start it first with `backend/.venv/bin/python scripts/opencrew_postgres.py start`, then start backend and frontend in managed `screen` sessions.

### 2. Start OpenCode CLI with stable auth

OpenCode CLI is not part of `scripts/opencrew_local_stack.sh`. After an OS update, check `4096` first:

```bash
lsof -nP -iTCP:4096 -sTCP:LISTEN
```

If nothing is listening on `4096`, start the CLI server with the same stable `OPENCODE_SERVER_PASSWORD` used by OpenCrew automation:

```bash
nohup env OPENCODE_SERVER_USERNAME="opencode" \
  OPENCODE_SERVER_PASSWORD="$OPENCODE_SERVER_PASSWORD" \
  "/Users/duheng/.opencode/bin/opencode" serve \
  --hostname 127.0.0.1 \
  --port 4096 \
  > "/tmp/opencrew-opencode-cli-4096.log" 2>&1 &
```

If `OPENCODE_SERVER_PASSWORD` is not present in the shell, set it to the stable local password before starting the CLI server. Do not rely on the Desktop server for automation after an OS update; Desktop can restart with a new dynamic port/password and OpenCrew may only see `401 auth_required`.

Verify OpenCode CLI directly:

```bash
curl -i -m 10 -u "opencode:$OPENCODE_SERVER_PASSWORD" \
  http://127.0.0.1:4096/global/health
```

Expected response:

```text
HTTP/1.1 200 OK
```

The JSON body should include:

```json
{"healthy":true}
```

### 3. Rediscover OpenCode from OpenCrew

After the backend and OpenCode CLI are both running, force OpenCrew to rediscover the OpenCode service:

```bash
curl -i -m 10 -X POST http://127.0.0.1:8011/api/setup/opencode/discover
curl -s -m 10 http://127.0.0.1:8011/api/setup/summary
```

Expected OpenCrew summary fields:

```text
summary.opencode.status = ready
summary.opencode.base_url = http://127.0.0.1:4096
summary.opencode.auth_source = process_env
```

If `summary.opencode.status = auth_required` and the selected URL is a dynamic Desktop port such as `127.0.0.1:<random>`, the CLI server was not discovered or was not running with readable env auth. Start or restart the CLI server on `4096`, then rerun discovery.

### 4. Final verification

Confirm the managed OpenCrew stack is not only listening, but also owned by the expected `screen` sessions:

```bash
scripts/opencrew_local_stack.sh status
```

Required status lines:

```text
PostgreSQL: query healthy on 5433/opencrew
backend: listening on 8011
frontend: listening on 18080
screen: opencrew-backend exists
screen: opencrew-frontend exists
```

Run these API checks before starting OpenClip/OpenFlow/OC-Rebuild tasks:

```bash
curl -i -m 10 http://127.0.0.1:8011/api/health
curl -s -m 10 http://127.0.0.1:18080/api/setup/summary
```

The backend health endpoint must return HTTP `200` with `"ok": true`, and setup summary must show `summary.opencode.status = ready`.

## Codex / Sandbox Restart Rule

When restarting from Codex or another sandboxed agent, do not start backend or frontend with raw foreground commands from inside the sandbox. The frontend dev server can fail with:

```text
listen EPERM: operation not permitted 0.0.0.0:18080
```

Use the managed local stack script with local/unrestricted permission instead:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
scripts/opencrew_local_stack.sh restart
scripts/opencrew_local_stack.sh status
```

The stack is only considered correctly restarted when both the ports and the managed `screen` sessions are present:

```text
backend: listening on 8011
frontend: listening on 18080
screen: opencrew-backend exists
screen: opencrew-frontend exists
```

Do not trust port listening by itself. If `8011` or `18080` is listening but the matching `screen` session is missing, an unmanaged stale process is occupying the port. Stop that stale PID from `lsof`, then rerun the stack script with local/unrestricted permission. Otherwise the frontend can talk to old backend code even though the port checks appear healthy.

### Codex / Sandbox Testing Rules

When validating OpenCrew from Codex, distinguish product failures from sandbox failures before reporting a test result.

- Commands that write to `~/.opencrew`, open local GUI/browser apps, bind local ports, restart services, or kill stale service PIDs must run with local/unrestricted permission. In Codex, request escalation for those commands instead of trying to work around the sandbox.
- The writable sandbox roots do not include `~/.opencrew`. Any tool that writes session outputs, for example `~/.opencrew/sessions/<id>/workspace/...`, needs escalation. A failed write there is a sandbox permission issue until proven otherwise.
- `curl` checks against `127.0.0.1:8011` or `127.0.0.1:18080` can fail or be blocked differently inside the sandbox. If a localhost health check is part of the test, rerun the same check with local/unrestricted permission before deciding the service is down.
- Use `GET` for raw workspace file checks. The raw file route `/api/session-tasks/<session_id>/raw/<path>` only supports `GET`; `curl -I` returns `405 Method Not Allowed` and is not a cache or routing failure. To inspect headers, use:

```bash
curl -sS -D - -o /dev/null -m 5 \
  http://127.0.0.1:18080/api/session-tasks/<session_id>/raw/<path>
```

- For Python syntax checks from Codex, set pycache into a writable location. macOS may otherwise try to write `.pyc` files under `~/Library/Caches`, which the sandbox blocks:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache \
  OpenCrew/backend/.venv/bin/python -m py_compile <files>
```

- If `scripts/opencrew_local_stack.sh restart` says a port is already listening but the matching `screen` session is absent, the active service is stale. Run `lsof -nP -iTCP:<port> -sTCP:LISTEN`, stop that PID with approval, then run the managed stack script again.
- After backend code edits, verify that the running backend is the restarted backend, not just "something on 8011". Check both `screen -ls` and the expected response behavior, such as changed response headers or API output.
- Frontend verification should use the actual browser UI when the issue is visual state, focus, image reload, or cache behavior. API checks alone do not prove the browser DOM refreshed.

### Frontend Change Verification Checklist

Use this checklist when the goal is "make the latest code effective locally" or when validating a frontend/user-visible fix.

1. Run code-level checks first:

```bash
cd frontend
npm run build
npm run check:koubo-cache
```

Run backend contract or syntax checks for touched backend files, for example:

```bash
backend/.venv/bin/python -m pytest backend/tests/contracts/<test_file>.py
backend/.venv/bin/python -m py_compile backend/opcrew_backend/<changed_file>.py
```

2. Restart the managed local stack before final runtime validation:

```bash
scripts/opencrew_local_stack.sh restart
```

Do not use a temporary `npm run dev` server as the final proof that local OpenCrew has loaded the change. Temporary Vite servers are useful for isolated frontend debugging, but the real local app uses the managed `18080` frontend and `8011` backend.

3. Verify the served runtime, not only the build output:

```bash
curl -fsS http://127.0.0.1:8011/api/health
curl -fsS http://127.0.0.1:18080/
curl -fsS http://127.0.0.1:18080/api/auth/status
```

For newly added authenticated routes, an unauthenticated `401` is often enough to prove the route is loaded; a `404` means the running backend does not have that route.

4. Verify the frontend cache chain for Koubo changes. Changes under `frontend/src/modules/koubo/UploadAssetLibrary/`, `frontend/src/modules/koubo/KouboStoryBoard/`, or shared frontend API modules can require query-string bumps along the served import chain:

```text
frontend/index.html
frontend/src/main.tsx
frontend/src/App.jsx
frontend/src/modules/koubo/KouboStoryBoardModule.jsx
frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage.jsx
frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx
changed component or shared API module
```

`npm run check:koubo-cache` is the automated minimum. For cache-sensitive fixes, also inspect the served entry/module text from `http://127.0.0.1:18080/` or the affected module URL to make sure the new version string is actually what the browser receives.

5. Use browser verification for visual, refresh, image, and persistence bugs. API checks can prove data exists, but they do not prove that the browser DOM rehydrated from that data. Prefer a Playwright smoke that:

- opens the real `http://127.0.0.1:18080` app after stack restart;
- uses the real auth flow or existing auth cookie;
- mocks only external model/provider calls when needed;
- keeps the real persistence endpoints active;
- reloads the page and asserts the restored DOM and screenshot state.

If the smoke writes fixture records into an existing task workspace, remove those records after validation unless the task is dedicated test data.

## Step 1: Confirm PostgreSQL

Confirm the existing PostgreSQL instance is listening:

```bash
lsof -nP -iTCP:5433 -sTCP:LISTEN
```

Expected output:

```text
postgres ... TCP 127.0.0.1:5433 (LISTEN)
```

Use this database URL for backend startup:

```bash
export DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
```

This is the normal local development database. Do not use the old `5432` database unless explicitly testing historical data.

To reset to a clean local OpenCrew database while keeping the same `5433` PostgreSQL cluster:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
backend/.venv/bin/python - <<'PY'
import psycopg
conn = psycopg.connect('postgresql://opencrew:opencrew@127.0.0.1:5433/postgres')
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'opencrew' AND pid <> pg_backend_pid()")
    cur.execute('DROP DATABASE IF EXISTS opencrew')
    cur.execute('CREATE DATABASE opencrew')
conn.close()
PY
```

After reset, restart OpenCrew. Backend startup creates all tables and applies migrations through `metadata.create_all()` and `run_migrations()`.

## Step 2: Start OpenCode service

### Option A: OpenCode CLI server, recommended

Use a stable password in the environment so OpenCrew can recover auth after process discovery.

```bash
nohup env OPENCODE_SERVER_USERNAME="opencode" \
  OPENCODE_SERVER_PASSWORD="<stable-local-password>" \
  "/Users/duheng/.opencode/bin/opencode" serve \
  --hostname 127.0.0.1 \
  --port 4096 \
  > "/tmp/opencrew-opencode-cli-4096.log" 2>&1 &
```

Verify the CLI server directly:

```bash
lsof -nP -iTCP:4096 -sTCP:LISTEN
curl -i -m 10 -u "opencode:<stable-local-password>" http://127.0.0.1:4096/global/health
```

Expected response:

- HTTP `200 OK`
- JSON contains `"healthy":true`

Then ask OpenCrew to rediscover the service:

```bash
curl -i -m 10 -X POST http://127.0.0.1:8011/api/setup/opencode/discover
curl -s -m 10 http://127.0.0.1:8011/api/setup/summary
```

Expected OpenCrew summary fields:

- `summary.opencode.base_url = http://127.0.0.1:4096`
- `summary.opencode.auth_username = opencode`
- `summary.opencode.auth_source = process_env`
- `summary.opencode.status = ready`

If the backend is not started yet, run the discovery commands after Step 3.

### Option B: OpenCode Desktop service

Start `/Applications/OpenCode.app` normally. It will launch an internal local HTTP server.

Important auth behavior:

- Desktop uses Basic Auth just like CLI
- username is `opencode`
- password is generated with `randomUUID()` at Desktop startup
- password is available inside the Desktop renderer, not as a stable external config value
- external tools may see the server port but not the password

Verify discovery from OpenCrew after backend startup:

```bash
curl -i -m 10 -X POST http://127.0.0.1:8011/api/setup/opencode/discover
curl -s -m 10 http://127.0.0.1:8011/api/setup/summary
```

If summary shows `probe_status = auth_required`, `http_status = 401`, or empty `auth_username/auth_password`, OpenCrew found the Desktop server but did not get the generated password. In that case either:

- use the CLI server option above for stable automation
- or manually provide the current Desktop credentials if they are exposed by the Desktop UI or another trusted channel

### Restart rule for OpenCode

After any system restart or OpenCode restart:

- CLI: restart the CLI server with the same `OPENCODE_SERVER_PASSWORD`, then rerun OpenCrew discover
- Desktop: restart Desktop, then rerun OpenCrew discover; expect a new port/password and verify whether auth was obtained
- if discovery switches to a `401` Desktop candidate without credentials, start the CLI server and rediscover before running OpenClip/OpenFlow/OC-Rebuild tasks

## Step 3: Start backend

Preferred restart path from Codex:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
scripts/opencrew_local_stack.sh restart
```

Manual backend startup is only for non-sandboxed local shells or debugging.

From backend directory:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew/backend
nohup env DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
  /Users/duheng/Development/OpenCode/CrewAI/OpenCrew/backend/.venv/bin/python main.py \
  > /tmp/opencrew-backend.log 2>&1 &
```

Basic checks:

```bash
lsof -nP -iTCP:8011 -sTCP:LISTEN
curl -i -m 10 http://127.0.0.1:8011/api/health
```

Expected health response:

```json
{"ok":true,"service":"opcrew-backend", ...}
```

If needed, inspect log:

```bash
sed -n '1,80p' /tmp/opencrew-backend.log
```

Expected early log lines:

```text
INFO: Uvicorn running on http://0.0.0.0:8011
INFO: Started reloader process ...
INFO: Started server process ...
INFO: Application startup complete.
```

## Step 4: Start frontend

Preferred restart path from Codex:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
scripts/opencrew_local_stack.sh restart
```

Manual frontend startup is only for non-sandboxed local shells or debugging.

From frontend directory:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew/frontend
nohup npm run dev > /tmp/opencrew-frontend.log 2>&1 &
```

Basic checks:

```bash
lsof -nP -iTCP:18080 -sTCP:LISTEN
curl -I -m 10 http://127.0.0.1:18080
```

Expected response:

- HTTP `200 OK`

Expected log lines:

```text
VITE ... ready
Local: http://localhost:18080/
```

## API Verification

These are the minimum backend endpoints the frontend depends on during initial load.

### Summary

```bash
curl -s -m 10 http://127.0.0.1:8011/api/setup/summary
```

Verify:

- JSON returns normally
- `summary.sessions` exists
- `summary.opencode.status` is `ready` before running tasks that call OpenCode
- `summary.opencode.auth_source` is `process_env` when using the recommended CLI server

### OpenFlow config

```bash
curl -s -m 10 http://127.0.0.1:8011/api/openflow/analysis/config
```

Verify:

- JSON returns normally
- `draft.session_id` exists
- `package_spec` exists

### OpenFlow task list

```bash
curl -s -m 10 "http://127.0.0.1:8011/api/session-tasks?group_id=openflow-analysis"
```

Verify:

- JSON returns normally
- `items` is present

### Normal session task list

```bash
curl -s -m 10 "http://127.0.0.1:8011/api/session-tasks?group_id=demo-group"
```

Verify:

- JSON returns normally
- `summary` is present

## Frontend Verification

Open:

```text
http://127.0.0.1:18080
```

### Connection page

Verify:

- page loads successfully
- no blank screen
- no immediate API error banner

### OpenFlow page

Verify:

- OpenFlow workspace loads
- `Reference Video`, `Session`, `Industry`, `Video Formula` fields render
- `Session List` button works
- `New Session` button works

### Sessions page

Verify:

- sessions table loads
- task detail page opens from `#/sessions/task/<id>`
- file list loads
- work log loads
- `Re-run` button is visible for completed sessions

## OpenFlow / Session Verification

### Rerun expectation

For a completed OpenFlow session:

- `Re-run` must reuse the same session id
- old outputs are cleared before the new run
- new clips, transcripts, and reports are regenerated in the same session workspace

### Key output folders

For session `<id>`:

- `~/.opencrew/sessions/<id>/workspace/clips`
- `~/.opencrew/sessions/<id>/workspace/transcripts`
- `~/.opencrew/sessions/<id>/workspace/storyboards`
- `~/.opencrew/sessions/<id>/workspace/reports`
- `~/.opencrew/sessions/<id>/workspace/meta`

### Minimum output checks

For a successful OpenFlow run, verify these exist:

- `meta/video_metadata.json`
- `meta/asr_segments.json`
- `transcripts/original_dialogue_segments_scheme_1.json`
- `transcripts/original_dialogue_segments_scheme_2.json`
- `transcripts/original_dialogue_segments_scheme_3.json`
- `storyboards/scheme_1_fine_storyboard.md`
- `storyboards/scheme_2_balanced_storyboard.md`
- `storyboards/scheme_3_coarse_storyboard.md`
- `reports/analysis_summary.json`
- `reports/componentized_analysis.json`

### Session 21 validation example

Representative checks used during actual validation:

```bash
curl -s http://127.0.0.1:8011/api/session-tasks/21
curl -s "http://127.0.0.1:8011/api/session-tasks/21/files?path=clips/scheme_3"
```

Things to verify:

- session status returns to `waiting_input`
- `workspace_dir` points to `~/.opencrew/sessions/21/workspace`
- clips cover the full source duration
- ASR language is `zh` when the source language is Chinese

## Known Practical Notes

### 1. Backend runs with `uvicorn --reload`

Current `main.py` uses reload mode.

This means:

- there is a reloader parent process
- there is a worker child process
- after code edits, the backend may need a full restart if the worker becomes unhealthy

### 2. Frontend preview must tolerate multiple output naming styles

OpenFlow historical outputs may appear as either:

- `clips/scheme_1`, `clips/scheme_2`, `clips/scheme_3`
- or named Chinese directories such as `clips/方案二_老板判断核心`

Frontend preview logic must tolerate both.

### 3. Session workspace must be keyed by session id

The correct workspace layout is:

```text
~/.opencrew/sessions/<session_id>/workspace
```

If a session points at another session's directory, task detail, raw file access, and preview data can all become inconsistent.

## Stop Commands

### PostgreSQL

Keep the existing PostgreSQL instance on `127.0.0.1:5433` running while using OpenCrew.

### Stop backend and frontend

Preferred managed stop:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
scripts/opencrew_local_stack.sh stop
scripts/opencrew_local_stack.sh status
```

Typical port-based stop checks:

```bash
lsof -nP -iTCP:8011 -sTCP:LISTEN
lsof -nP -iTCP:18080 -sTCP:LISTEN
```

Then kill the returned PIDs if needed.

## Recommended Daily Startup Sequence

From Codex, use the managed script with local/unrestricted permission:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
scripts/opencrew_local_stack.sh restart
scripts/opencrew_local_stack.sh status
```

If the status shows a port listener but a missing `screen` session, stop the stale PID reported by `lsof` and run the managed restart again.

Manual sequence for a normal local shell:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
lsof -nP -iTCP:5433 -sTCP:LISTEN

nohup env OPENCODE_SERVER_USERNAME="opencode" \
  OPENCODE_SERVER_PASSWORD="<stable-local-password>" \
  "/Users/duheng/.opencode/bin/opencode" serve \
  --hostname 127.0.0.1 \
  --port 4096 \
  > "/tmp/opencrew-opencode-cli-4096.log" 2>&1 &

cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew/backend
nohup env DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
  /Users/duheng/Development/OpenCode/CrewAI/OpenCrew/backend/.venv/bin/python main.py \
  > /tmp/opencrew-backend.log 2>&1 &

cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew/frontend
nohup npm run dev > /tmp/opencrew-frontend.log 2>&1 &
```

Then verify:

```bash
curl -i -m 10 http://127.0.0.1:8011/api/health
curl -I -m 10 http://127.0.0.1:18080
curl -i -m 10 -u "opencode:<stable-local-password>" http://127.0.0.1:4096/global/health
curl -i -m 10 -X POST http://127.0.0.1:8011/api/setup/opencode/discover
curl -s -m 10 http://127.0.0.1:8011/api/setup/summary
```
