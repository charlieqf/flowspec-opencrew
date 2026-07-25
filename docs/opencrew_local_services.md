# OpenCrew Local Services

This macmini checkout uses `scripts/opencrew_local_stack.sh` as the source of truth for local frontend/backend service discovery.

## Default Runtime

- Repo root: `/Users/macmini-2/work/code/OpenCrew`
- Backend: `http://127.0.0.1:8011`
- Frontend: `http://127.0.0.1:18080`
- PostgreSQL: `127.0.0.1:5433`
- Backend log: `/tmp/opencrew-backend.log`
- Frontend log: `/tmp/opencrew-frontend.log`
- Backend screen: `opencrew-backend`
- Frontend screen: `opencrew-frontend`

Do not infer that the app is down just because common framework ports such as `5173`, `8000`, or `3000` are not listening. This project normally uses `8011` and `18080`.

## First Checks

Prefer the stack script over ad hoc port guesses:

```bash
scripts/opencrew_local_stack.sh status
scripts/opencrew_local_stack.sh doctor
```

Direct health probes:

```bash
curl -fsS http://127.0.0.1:8011/api/health
curl -fsS http://127.0.0.1:18080/
curl -fsS http://127.0.0.1:18080/api/auth/status
```

Listener and process checks:

```bash
lsof -nP -iTCP:8011 -sTCP:LISTEN
lsof -nP -iTCP:18080 -sTCP:LISTEN
lsof -nP -iTCP:5433 -sTCP:LISTEN
ps aux | grep -Ei 'opencrew|vite|python|node|screen' | grep -v grep
```

Expected process pattern when the local stack is running:

- Backend: `backend/.venv/bin/python main.py`, usually launched in `SCREEN -dmS opencrew-backend`
- Frontend: `node .../frontend/node_modules/.bin/vite`, usually launched in `SCREEN -dmS opencrew-frontend`
- PostgreSQL: `postgres ... -p 5433`

## Applying Local Code Changes

Frontend Vite normally hot-reloads source changes. Backend reload is intentionally disabled for stable local runs, so Python route/service changes require restarting the stack or at least the backend:

```bash
scripts/opencrew_local_stack.sh restart
scripts/opencrew_local_stack.sh status
```

After restart, verify both sides:

```bash
curl -fsS http://127.0.0.1:8011/api/health
curl -fsS http://127.0.0.1:18080/api/auth/status
```

For user-visible frontend fixes, use this local validation flow:

```bash
cd frontend
npm run build
npm run check:koubo-cache
cd ..
scripts/opencrew_local_stack.sh restart
curl -fsS http://127.0.0.1:8011/api/health
curl -fsS http://127.0.0.1:18080/
curl -fsS http://127.0.0.1:18080/api/auth/status
```

Do not treat a temporary Vite server on an alternate port as proof that the local OpenCrew app has loaded a change. Final validation should target the managed stack on frontend `18080` and backend `8011`.

For Koubo frontend changes, also confirm the browser receives the intended cache-busted import chain. `npm run check:koubo-cache` is the minimum guard, but shared modules can require checking the full served chain from `index.html` through `main.tsx`, `App.jsx`, `KouboStoryBoardModule.jsx`, the Asset Library page/overlay, and the changed component or API module.

Use browser or Playwright verification when the issue is visual state, image rendering, refresh/reload behavior, drawer state, or local workspace persistence. API checks alone do not prove that the browser DOM restored correctly.

## Logs

Use logs after failed health checks or unexpected UI behavior:

```bash
tail -80 /tmp/opencrew-backend.log
tail -80 /tmp/opencrew-frontend.log
```

The frontend proxies `/api` to the backend configured by `OPENCREW_BACKEND_URL`, defaulting to `http://127.0.0.1:8011`.
