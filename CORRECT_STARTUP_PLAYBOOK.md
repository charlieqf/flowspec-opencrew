# OpenCrew Correct Startup Playbook

> Canonical startup runbook. `ACTUAL_STARTUP_AND_VERIFICATION.md` is retained only as historical verification notes and should not be used as an operational source of truth.

## Purpose

This document records the **correct startup flow** for the current `OpenCrew` local environment based on the actual requirements from this round of debugging.

This is not a generic setup guide.

This document is specifically for the current machine and current project state, with emphasis on:

- using the **local standalone OpenCrew PostgreSQL database on `5433`**
- keeping local development isolated from the old `5432` database
- avoiding the startup mistakes that caused wrong data, wrong task visibility, and wrong OpenCode assumptions
- keeping OpenCode service auth recoverable after machine or service restarts

## Final Correct Rules

### Rule 1: Use the local standalone OpenCrew PostgreSQL database on `5433`

For the current working setup, `OpenCrew` must use the local standalone PostgreSQL instance:

- host: `127.0.0.1`
- port: `5433`
- database: `opencrew`
- user: `opencrew`
- password: `opencrew`
- data directory: `/Users/duheng/.opencrew/postgres`

SQLAlchemy / backend `DATABASE_URL`:

```bash
postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew
```

Direct `psycopg` URL:

```bash
postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew
```

### Rule 2: Do not use the old `5432` database for current local OpenCrew startup

The old database on `127.0.0.1:5432` may contain historical data from previous debugging rounds, but it is not the target for current local development.

For normal startup in the current environment:

- use `127.0.0.1:5433/opencrew`
- use username `opencrew`
- use password `opencrew`
- do not point backend to `5432` unless explicitly testing old historical data

If a clean local database is required, keep the `5433` PostgreSQL cluster and recreate only the `opencrew` database:

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

After recreating the database, restart OpenCrew. Backend startup runs `metadata.create_all()` and `run_migrations()`, so the schema is rebuilt to the latest version automatically.

### Rule 3: OpenCode must be the actual intended local service with usable auth

`OpenCrew` auto-discovery currently finds the currently running local OpenCode HTTP service.

For reliable OpenCrew automation, prefer a CLI server started with explicit Basic Auth environment variables:

```bash
nohup env OPENCODE_SERVER_USERNAME="opencode" \
  OPENCODE_SERVER_PASSWORD="<stable-local-password>" \
  "/Users/duheng/.opencode/bin/opencode" serve \
  --hostname 127.0.0.1 \
  --port 4096 \
  > "/tmp/opencrew-opencode-cli-4096.log" 2>&1 &
```

Then verify:

```bash
curl -i -m 10 -u "opencode:<stable-local-password>" http://127.0.0.1:4096/global/health
```

Expected OpenCrew discovery result:

- `base_url = http://127.0.0.1:4096`
- `auth_username = opencode`
- `auth_source = process_env`
- `status = ready`

Desktop OpenCode is also valid, but has different auth behavior:

- process: `/Applications/OpenCode.app/Contents/MacOS/OpenCode`
- the Desktop app starts a local OpenCode HTTP server on a dynamic localhost port
- username is `opencode`
- password is generated at Desktop startup and passed to the Desktop renderer through IPC
- the generated password is not a stable external config value and may not be readable by OpenCrew discovery

If OpenCrew discovers Desktop as `auth_required` / HTTP `401` without credentials, start the CLI server above and rediscover.

An older observed Desktop-backed service from this debugging round was:

- process: `/Applications/OpenCode.app/Contents/MacOS/OpenCode`
- child service: `/Applications/OpenCode.app/Contents/MacOS/opencode-cli --print-logs --log-level WARN serve --hostname 127.0.0.1 --port 49725`
- base URL: `http://127.0.0.1:49725`
- version: `1.14.29`

If the discovered service is **not** the OpenCode you want, or if auth is missing, then the real target OpenCode service must be started and rediscovered before using `OpenCrew` tasks.

### Rule 4: From Codex, restart OpenCrew through the managed stack script

When operating from Codex or another sandboxed agent, do not start backend or frontend with raw `nohup`, `npm run dev`, or foreground commands from inside the sandbox. The frontend can fail to bind with:

```text
listen EPERM: operation not permitted 0.0.0.0:18080
```

Use the managed stack script with local/unrestricted permission. After a machine restart, always start with the project-local media binary environment as shown below; this prevents backend-launched media tools from failing with `No such file or directory: 'ffmpeg'` even when the user's interactive shell happens to have a different `PATH`:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin:/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin:$PATH" \
OPENCREW_FFMPEG_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg" \
OPENCREW_FFPROBE_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe" \
scripts/opencrew_local_stack.sh restart

PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin:/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin:$PATH" \
OPENCREW_FFMPEG_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg" \
OPENCREW_FFPROBE_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe" \
scripts/opencrew_local_stack.sh status
```

A healthy restart requires both the ports and the managed `screen` sessions:

- `backend: listening on 8011`
- `frontend: listening on 18080`
- `screen: opencrew-backend exists`
- `screen: opencrew-frontend exists`

Do not accept "port already listening" as proof that the current code is running. If `8011` or `18080` is listening but the matching `screen` session is missing, that is a stale unmanaged process. Stop the PID reported by `lsof`, then run the managed restart again. This prevents the frontend from calling an old backend that still serves outdated behavior.

### Rule 5: Treat Codex sandbox failures as environment failures, not product test failures

When tests are run from Codex, several commands need local/unrestricted permission. Do not report a product regression until these sandbox-sensitive checks have been rerun correctly.

- Writes to `~/.opencrew` are outside the normal Codex writable sandbox. Session workspace updates such as `~/.opencrew/sessions/<id>/workspace/...` require escalation.
- Service operations require escalation: starting/stopping backend or frontend, binding ports, killing stale PIDs, and opening GUI/browser apps.
- Localhost checks may be affected by sandbox/network restrictions. If `curl` to `127.0.0.1:8011` or `127.0.0.1:18080` fails inside Codex, rerun with local/unrestricted permission before diagnosing the service.
- Python compile/test commands can fail because `.pyc` files are written to `~/Library/Caches`. Use a writable pycache prefix:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache \
  OpenCrew/backend/.venv/bin/python -m py_compile <files>
```

- For raw workspace files, do not use `curl -I`. The route supports `GET`, not `HEAD`, so `curl -I` returns `405 Method Not Allowed`. To inspect cache headers or content type, use:

```bash
curl -sS -D - -o /dev/null -m 5 \
  http://127.0.0.1:18080/api/session-tasks/<session_id>/raw/<path>
```

- If a port is listening but `screen -ls` does not show the matching `opencrew-backend` or `opencrew-frontend` session, the process is stale. Kill the PID from `lsof -nP -iTCP:<port> -sTCP:LISTEN` with approval and restart through `scripts/opencrew_local_stack.sh`.
- For UI issues such as image cache, focus loss, stale state, drawer behavior, or visual rendering, use frontend/browser verification. Backend/API success is necessary but not enough.
- For final local verification after frontend changes, target the managed `18080`/`8011` stack, not an ad hoc Vite server on a fallback port. Run `npm run build`, `npm run check:koubo-cache`, `scripts/opencrew_local_stack.sh restart`, then verify health and the real browser UI.
- For Koubo frontend changes, cache-bust the whole served import path when needed: `index.html` -> `main.tsx` -> `App.jsx` -> `KouboStoryBoardModule.jsx` -> page/overlay -> changed component or shared API module. The checker is the minimum guard; browser-loaded module URLs are the final source of truth.

### Rule 6: Restart OpenCrew backend after changing mihomo/Tun Mode

When switching mihomo, Tun Mode, VPN, or any local proxy mode, restart the OpenCrew backend before running provider-backed tools such as `05_02_VideoPlanExecutor`.

Reason:

- `05_02` is launched as a backend child process.
- the child process inherits the backend process network environment and proxy variables.
- Tun Mode changes the system route, but an already-running backend may still carry stale proxy state from before the route change.
- if that stale state points Python requests at `127.0.0.1:7890` while the local HTTP proxy port is not listening, provider calls can fail with `Connection refused`.

Recommended recovery:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin:/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin:$PATH" \
OPENCREW_FFMPEG_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg" \
OPENCREW_FFPROBE_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe" \
scripts/opencrew_local_stack.sh restart

PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin:/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin:$PATH" \
OPENCREW_FFMPEG_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg" \
OPENCREW_FFPROBE_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe" \
scripts/opencrew_local_stack.sh status
```

### Rule 7: Backend restarts must carry project-local `ffmpeg` and `ffprobe`

The media path issue is considered solved only when the backend process itself inherits the project-local binary locations. Do not rely on `which ffmpeg` in the current terminal; UI-triggered tools run as backend child processes and inherit the backend environment, not a later shell.

Canonical media binaries for the current machine:

- `OPENCREW_FFMPEG_PATH=/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg`
- `OPENCREW_FFPROBE_PATH=/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe`
- `PATH` must include `/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin`
- `/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin` is the secondary fallback

Before declaring a post-reboot startup complete, verify the project-local binaries:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
test -x ToolLibrary/.bin/ffmpeg
test -x ToolLibrary/.bin/ffprobe
ToolLibrary/.bin/ffmpeg -version | head -1
ToolLibrary/.bin/ffprobe -version | head -1
```

Then verify the live backend listener was started with the media environment:

```bash
BACKEND_PID="$(lsof -nP -tiTCP:8011 -sTCP:LISTEN | head -1)"
ps eww -p "$BACKEND_PID" | tr ' ' '\n' | grep -E '^(PATH|OPENCREW_FFMPEG_PATH|OPENCREW_FFPROBE_PATH)='
```

Expected:

- `OPENCREW_FFMPEG_PATH` points to `OpenCrew/ToolLibrary/.bin/ffmpeg`
- `OPENCREW_FFPROBE_PATH` points to `OpenCrew/ToolLibrary/.bin/ffprobe`
- `PATH` contains `OpenCrew/ToolLibrary/.bin`

If a UI task shows `No such file or directory: 'ffmpeg'` after reboot, treat it as a bad backend process environment. Restart through the canonical command in this playbook and recheck the backend process environment before rerunning the task.

## What Must Be Started

The correct stack for the current required workflow is:

1. local standalone PostgreSQL instance on `5433`
2. intended OpenCode local service with usable auth
3. OpenCrew backend on `8011`, pointed to `postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew`
4. OpenCrew frontend on `18080`

## Actual Paths

### Local standalone OpenCrew PostgreSQL instance

- data directory:
  `/Users/duheng/.opencrew/postgres`

- database URL:
  `postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew`

- username / password:
  `opencrew` / `opencrew`

### OpenCrew

- project root:
  `/Users/duheng/Development/OpenCode/CrewAI/OpenCrew`

- backend:
  `/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/backend`

- frontend:
  `/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/frontend`

- canonical media binary directory:
  `/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin`

- canonical `ffmpeg`:
  `/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg`

- canonical `ffprobe`:
  `/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe`

- secondary media binary fallback:
  `/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin`

### OpenCode

- recommended CLI binary:
  `/Users/duheng/.opencode/bin/opencode`

- recommended CLI listen URL:
  `http://127.0.0.1:4096`

- recommended CLI log file:
  `/tmp/opencrew-opencode-cli-4096.log`

- Desktop app:
  `/Applications/OpenCode.app`

### Local business files

- `~/.opencrew/sessions`
- `~/.opencrew/npc`
- `~/.opencrew/bin`

## Correct Startup Sequence

### Codex managed restart path

Use this path first when restarting from Codex:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin:/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin:$PATH" \
OPENCREW_FFMPEG_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg" \
OPENCREW_FFPROBE_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe" \
scripts/opencrew_local_stack.sh restart

PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin:/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin:$PATH" \
OPENCREW_FFMPEG_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg" \
OPENCREW_FFPROBE_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe" \
scripts/opencrew_local_stack.sh status
```

Then verify the frontend proxy hits the current backend:

```bash
curl -s -m 10 http://127.0.0.1:18080/api/setup/summary
```

If `status` reports a listening port but a missing `screen` session, stop the stale PID from `lsof -nP -iTCP:<port> -sTCP:LISTEN` and rerun the managed restart.

### Manual local-shell sequence

### Step 1: Start the local standalone PostgreSQL instance on `5433`

Use the managed PostgreSQL helper from the OpenCrew project root:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
backend/.venv/bin/python scripts/opencrew_postgres.py start
```

Verify:

```bash
lsof -nP -iTCP:5433 -sTCP:LISTEN
```

Expected:

- PostgreSQL listening on `127.0.0.1:5433`

Optional data verification:

```bash
/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/backend/.venv/bin/python - <<'PY'
import psycopg
conn = psycopg.connect('postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew', connect_timeout=5)
cur = conn.cursor()
cur.execute('select id from schema_migrations order by id')
print('\n'.join(row[0] for row in cur.fetchall()))
cur.close(); conn.close()
PY
```

Expected latest migration includes:

```text
0009_local_usage_artifact_attribution
```

### Step 2: Start or verify OpenCode service

Recommended CLI server:

```bash
nohup env OPENCODE_SERVER_USERNAME="opencode" \
  OPENCODE_SERVER_PASSWORD="<stable-local-password>" \
  "/Users/duheng/.opencode/bin/opencode" serve \
  --hostname 127.0.0.1 \
  --port 4096 \
  > "/tmp/opencrew-opencode-cli-4096.log" 2>&1 &
```

Verify CLI auth directly:

```bash
lsof -nP -iTCP:4096 -sTCP:LISTEN
curl -i -m 10 -u "opencode:<stable-local-password>" http://127.0.0.1:4096/global/health
```

### Step 3: Start OpenCrew backend/frontend against `5433`

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin:/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin:$PATH" \
DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
OPENCREW_MANAGE_POSTGRES=0 \
OPENCREW_FFMPEG_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg" \
OPENCREW_FFPROBE_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe" \
scripts/opencrew_local_stack.sh restart
```
Verify:

```bash
PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin:/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/.bin:$PATH" \
DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
OPENCREW_MANAGE_POSTGRES=0 \
OPENCREW_FFMPEG_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffmpeg" \
OPENCREW_FFPROBE_PATH="/Users/duheng/Development/OpenCode/CrewAI/OpenCrew/ToolLibrary/.bin/ffprobe" \
scripts/opencrew_local_stack.sh status
```

Expected:

- listener on `127.0.0.1:4096`
- HTTP `200 OK`
- `{"healthy":true,...}`

If using Desktop instead, start `OpenCode.app` and remember that the discovered port and password can change after every Desktop restart. If OpenCrew cannot obtain Desktop auth, use the CLI server for stable operation.

### Step 4: Verify OpenCrew backend is running against the `5433` local database

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
OPENCREW_MANAGE_POSTGRES=0 \
scripts/opencrew_local_stack.sh status
```

Verify:

```bash
curl -i -m 10 http://127.0.0.1:8011/api/health
```

Expected:

```json
{"ok":true,"service":"opcrew-backend", ...}
```

Also verify the clean local database is reachable through backend APIs:

```bash
curl -s -m 10 "http://127.0.0.1:8011/api/session-tasks?group_id=openflow-analysis"
curl -s -m 10 "http://127.0.0.1:8011/api/openclip/tasks"
```

Expected:

- APIs return successfully
- on a freshly reset local database, task/session lists may be empty by design

### Step 5: Start OpenCrew frontend

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew/frontend
nohup npm run dev > /tmp/opencrew-frontend.log 2>&1 &
```

Verify:

```bash
curl -I -m 10 http://127.0.0.1:18080
```

Expected:

- HTTP `200 OK`

### Step 6: Verify OpenCode detection from backend

```bash
curl -s -m 10 http://127.0.0.1:8011/api/setup/summary
```

Look at:

- `summary.opencode.base_url`
- `summary.opencode.version`
- `summary.opencode.auth_username`
- `summary.opencode.auth_source`
- `summary.opencode.status`

Recommended CLI discovery result:

- `base_url = http://127.0.0.1:4096`
- `auth_username = opencode`
- `auth_source = process_env`
- `status = ready`

Older observed Desktop-backed service during this debugging round:

- `base_url = http://127.0.0.1:49725`
- `version = 1.14.29`

If this is not your intended local OpenCode service, or if auth is missing, then **the correct OpenCode service is not ready yet**.

To force rediscovery after any restart:

```bash
curl -i -m 10 -X POST http://127.0.0.1:8011/api/setup/opencode/discover
curl -s -m 10 http://127.0.0.1:8011/api/setup/summary
```

## Frontend Verification Checklist

Open:

```text
http://127.0.0.1:18080
```

### OpenCode card

Verify:

- discovered URL matches the intended local OpenCode service
- displayed version matches the intended service
- auth source is present, ideally `process_env` for the CLI server
- status is `ready`
- if it shows a different version, do not continue until the right OpenCode service is started

### OpenFlow

Verify:

- OpenFlow session list endpoint loads
- on a freshly reset local database, the list may be empty by design
- file list opens
- `Re-run` remains on the same session id

### OpenClip

Verify:

- `OpenClip Tasks` dialog loads from the local `5433` database
- on a freshly reset local database, the list may be empty by design

### Media tools

Verify before running storyboard generation, audio extraction, video composition, or any tool step that uses media files:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
ToolLibrary/.bin/ffmpeg -version | head -1
ToolLibrary/.bin/ffprobe -version | head -1
BACKEND_PID="$(lsof -nP -tiTCP:8011 -sTCP:LISTEN | head -1)"
ps eww -p "$BACKEND_PID" | tr ' ' '\n' | grep -E '^(PATH|OPENCREW_FFMPEG_PATH|OPENCREW_FFPROBE_PATH)='
```

Expected:

- project-local `ffmpeg` and `ffprobe` commands print versions
- backend process env contains `OPENCREW_FFMPEG_PATH`
- backend process env contains `OPENCREW_FFPROBE_PATH`
- backend process `PATH` contains `OpenCrew/ToolLibrary/.bin`

Important:

- OpenClip tasks come from PostgreSQL table `openclip_tasks`
- they are **not** a direct mirror of the current OpenCode `/session` list

## Pitfalls We Hit In This Round

### Pitfall 1: Mixing `5432` and `5433` changes visible data

What happened:

- backend was sometimes pointed at the old `5432` database and sometimes at the local `5433` database
- frontend showed different tasks depending on which database the backend used

Impact:

- task/session visibility changed unexpectedly

How to avoid:

- for the current project state, always use local standalone DB on `5433`
- do not point backend to `5432` unless explicitly testing old historical data

### Pitfall 2: Empty OpenClip task list was mistaken for OpenCode service failure

What happened:

- OpenClip task list was empty on the new DB
- it looked like OpenCode had no session data

Reality:

- OpenClip task list comes from `openclip_tasks` table in PostgreSQL
- it does not come directly from OpenCode `/session`

How to avoid:

- if OpenClip task list is empty, first verify which database backend is using
- check `openclip_tasks` table or `GET /api/openclip/tasks`

### Pitfall 3: OpenCode detection found a valid service, but not the intended one

What happened:

- OpenCrew found the only active local OpenCode HTTP service
- that service was `OpenCode.app` -> `opencode-cli serve` on `49725`
- user expected a different local OpenCode version/service

How to avoid:

- before using OpenCrew, confirm the intended OpenCode service is actually running
- prefer the CLI server with `OPENCODE_SERVER_PASSWORD` for stable auth across restarts
- rerun `/api/setup/opencode/discover` after restarting OpenCode or the machine
- compare:

- process
- port
- version
- auth source
- `/api/setup/summary` output

### Pitfall 3b: Desktop OpenCode was reachable but auth was not discoverable

What happened:

- OpenCrew found a Desktop OpenCode server on localhost
- `/global/health` returned HTTP `401 Unauthorized`
- the Desktop-generated Basic Auth password was not available from the external process environment

How to avoid:

- for OpenCrew automation, start the CLI server with `OPENCODE_SERVER_PASSWORD`
- if Desktop is required, rediscover after every Desktop restart and verify `summary.opencode.status = ready`
- do not run OpenClip/OpenFlow/OC-Rebuild tasks while OpenCode summary shows `auth_required`, HTTP `401`, or missing credentials

### Pitfall 4: Imported or historical OpenClip tasks may point to unavailable OpenCode session ids

What happened:

- tasks imported from older data or another database referenced an `opencode_session_id`
- some task detail requests failed because that session id was not available in the currently running OpenCode service

How to avoid:

- database task visibility is not enough
- the currently running OpenCode service must also contain or be compatible with the referenced sessions

### Pitfall 5: Session workspace path bugs caused wrong data to display

What happened earlier:

- some sessions pointed at another session's workspace directory

Impact:

- frontend preview and raw file APIs showed the wrong data

Current fix status:

- workspace path logic was corrected to use `session_id`

How to avoid:

- verify `workspace_dir` matches `~/.opencrew/sessions/<session_id>/workspace`

### Pitfall 5b: Backend was healthy, but UI-triggered media tools could not find `ffmpeg`

What happened:

- frontend and backend health checks were green
- a storyboard generation step failed inside the UI with `No such file or directory: 'ffmpeg'`
- the problem was not the uploaded media file; the backend process environment did not guarantee project-local media binaries for child tools that invoke `ffmpeg` by command name

Impact:

- audio extraction, video probing, video composition, or sync steps can fail after reboot even though `8011` and `18080` are listening

How to avoid:

- restart through the canonical command in this playbook after every machine restart
- include `OPENCREW_FFMPEG_PATH`, `OPENCREW_FFPROBE_PATH`, and a `PATH` prefix for `OpenCrew/ToolLibrary/.bin`
- verify the live backend process environment, not only the current terminal environment
- if this error appears, restart the backend through the canonical media-env command before rerunning the UI task

### Pitfall 6: UI looked like the system crashed because backend exited when PostgreSQL was down

What happened:

- frontend on `18080` was still running
- backend on `8011` was not listening
- backend startup log showed PostgreSQL connection failure:

```text
connection to server at "127.0.0.1", port 5433 failed: Connection refused
```

Impact:

- OC-Rebuild pages could still load from the frontend dev server
- any API-backed action failed or appeared frozen
- it felt like the whole system had crashed, but the direct failure was backend startup dependency failure

Root cause:

- OpenCrew backend creates/checks database tables during import/startup
- if local standalone PostgreSQL on `127.0.0.1:5433` is not running first, backend exits before binding `8011`
- because backend is started manually with `nohup`/Python, it is not automatically restarted after PostgreSQL comes back

How to diagnose:

```bash
# 1. Is local standalone PostgreSQL alive?
lsof -nP -iTCP:5433 -sTCP:LISTEN

# 2. Is backend alive?
lsof -nP -iTCP:8011 -sTCP:LISTEN
curl -i -m 10 http://127.0.0.1:8011/api/health

# 3. Is frontend only alive while backend is down?
lsof -nP -iTCP:18080 -sTCP:LISTEN
curl -I -m 10 http://127.0.0.1:18080

# 4. Check backend crash reason
tail -120 /tmp/opencrew-backend.log
tail -120 /Users/duheng/Development/OpenCode/CrewAI/OpenCrew/backend/backend.log
```

Expected interpretation:

- `18080` up + `8011` down = frontend is alive, backend is down
- `8011` down + backend log mentions `5433` = start PostgreSQL first, then restart backend
- `5433` up + backend still down = restart backend and inspect `/tmp/opencrew-backend.log`

How to recover:

```bash
# 1. Start local standalone PostgreSQL first
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
backend/.venv/bin/python scripts/opencrew_postgres.py start

# 2. Confirm PostgreSQL is listening
lsof -nP -iTCP:5433 -sTCP:LISTEN

# 3. Restart backend/frontend after PostgreSQL is ready
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
OPENCREW_MANAGE_POSTGRES=0 \
scripts/opencrew_local_stack.sh restart

# 4. Confirm backend is healthy
curl -i -m 10 http://127.0.0.1:8011/api/health
```

Long-term fix:

- create a single local startup script that starts PostgreSQL, waits for `5433`, starts backend, waits for `/api/health`, then starts frontend
- or register PostgreSQL/backend/frontend as `launchctl` services so backend is restarted after DB and machine restarts
- keep `DATABASE_URL` pinned to `127.0.0.1:5433/opencrew` unless intentionally testing a separate database

### Pitfall 7: Sandbox startup and stale unmanaged processes made the UI use old code

What happened:

- frontend startup from Codex failed with `listen EPERM: operation not permitted 0.0.0.0:18080`
- a stale backend was already listening on `8011`
- the managed script saw the port and skipped backend startup
- the UI loaded, but API behavior came from old backend code

How to avoid:

- from Codex, always restart with `scripts/opencrew_local_stack.sh restart` using local/unrestricted permission
- after restart, run `scripts/opencrew_local_stack.sh status`
- require both `opencrew-backend` and `opencrew-frontend` `screen` sessions to exist
- if a port is listening while its `screen` session is missing, stop the stale PID from `lsof` and rerun the managed restart

### Pitfall 8: Tun Mode was enabled but provider tools still failed with Connection refused

What happened:

- mihomo or another proxy client was switched to Tun Mode.
- the browser or other apps could reach overseas provider endpoints.
- OpenCrew `05_02_VideoPlanExecutor` still failed during a provider call, for example:

```text
POST https://generativelanguage.googleapis.com/v1beta/models/...:generateContent?key=*** failed: [Errno 61] Connection refused
```

Impact:

- the Video Plan modal may show `First Frame` or other provider-backed steps as failed.
- the error can look like a Gemini/OpenAI/model/key problem even when the real problem is the local network path.

Root cause:

- `05_02` is not a browser request. The backend starts it as a Python child process.
- the child process inherits the already-running backend process environment.
- Tun Mode changes system routing, but an old backend process may still have stale `HTTP_PROXY`, `HTTPS_PROXY`, or `OPENCREW_MIHOMO_PROXY_URL` assumptions from before Tun Mode was enabled.
- if the inherited path points to `127.0.0.1:7890` and that local HTTP proxy port is not listening in the current mode, Python raises `Connection refused`.

How to diagnose:

- first identify whether the failure mentions a provider endpoint such as `generativelanguage.googleapis.com`, `api.openai.com`, `api.x.ai`, or `api.sync.so`.
- if the message is `Connection refused`, treat it as a local network path/proxy-state problem before investigating prompt, model name, image input, or API key.
- check whether the proxy/Tun Mode was changed after OpenCrew backend was started.
- check OpenCrew status and make sure the managed backend session exists:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
scripts/opencrew_local_stack.sh status
```

How to recover:

```bash
# 1. Make sure Tun Mode or the intended proxy mode is already enabled and stable.

# 2. Restart OpenCrew so backend child processes inherit the current network state.
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
scripts/opencrew_local_stack.sh restart
scripts/opencrew_local_stack.sh status

# 3. Retry the failed provider step from the UI.
```

How to avoid:

- after toggling Tun Mode, VPN, or mihomo proxy mode, restart OpenCrew backend before running `05_02`.
- do not interpret `Connection refused` on a provider URL as proof that the provider endpoint, prompt, key, or model is wrong.
- keep the distinction clear:
  - HTTP proxy mode: provider traffic may explicitly go through `127.0.0.1:7890`.
  - Tun Mode: provider traffic should normally be routed by the system network layer, so stale process-level proxy variables can be harmful.

## Correct Daily Startup Commands

Preferred from Codex:

```bash
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
scripts/opencrew_local_stack.sh restart
scripts/opencrew_local_stack.sh status
curl -s -m 10 http://127.0.0.1:18080/api/setup/summary
```

Manual local-shell fallback:

Use this exact order for the current local standalone `5433` database.

```bash
# 1. Start local standalone PostgreSQL on 5433
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
backend/.venv/bin/python scripts/opencrew_postgres.py start

# 2. Start recommended OpenCode CLI server with stable auth
nohup env OPENCODE_SERVER_USERNAME="opencode" \
  OPENCODE_SERVER_PASSWORD="<stable-local-password>" \
  "/Users/duheng/.opencode/bin/opencode" serve \
  --hostname 127.0.0.1 \
  --port 4096 \
  > "/tmp/opencrew-opencode-cli-4096.log" 2>&1 &

# 3. Start backend/frontend on local 5433 DB
cd /Users/duheng/Development/OpenCode/CrewAI/OpenCrew
DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
OPENCREW_MANAGE_POSTGRES=0 \
scripts/opencrew_local_stack.sh restart

# 4. Rediscover OpenCode after backend is up
curl -i -m 10 -X POST http://127.0.0.1:8011/api/setup/opencode/discover
```

## Quick Verification Commands

```bash
curl -i -m 10 http://127.0.0.1:8011/api/health
curl -I -m 10 http://127.0.0.1:18080
curl -i -m 10 -u "opencode:<stable-local-password>" http://127.0.0.1:4096/global/health
curl -s -m 10 http://127.0.0.1:8011/api/setup/summary
curl -s -m 10 "http://127.0.0.1:8011/api/session-tasks?group_id=openflow-analysis"
curl -s -m 10 http://127.0.0.1:8011/api/openclip/tasks
```

## Final Practical Conclusion

For your current requirement, the only correct startup baseline is:

- local standalone PostgreSQL on `5433` with `opencrew` / `opencrew`
- intended OpenCode service with usable auth, preferably CLI on `4096` with `OPENCODE_SERVER_PASSWORD`
- OpenCrew backend connected to `5433/opencrew`
- frontend on `18080`

If any of these four are wrong, the UI will look "incorrect" even though some services may still appear healthy.
