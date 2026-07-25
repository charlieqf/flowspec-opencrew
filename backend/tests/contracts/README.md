# P0 Contract Tests

These tests cover P0 workflow infrastructure contracts without real model, VLM, OpenCode, or long-running tool calls.

Run from the repository root:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache \
  backend/.venv/bin/python -m unittest discover -s backend/tests/contracts
```

## P0 Stack Smoke

After starting the local stack, run the automated P0 HTTP smoke:

```bash
PYTHONPATH=backend \
DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
OPENCREW_DATA_DIR="/Users/macmini-1/.opencrew" \
PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache \
  backend/.venv/bin/python backend/scripts/opencrew_p0_stack_smoke.py --json
```

To include the local Caddy/nip.io reverse proxy, pass the local Caddy URL plus the public Host header and Basic Auth credentials through environment variables:

```bash
OPENCREW_SMOKE_CADDY_URL="http://127.0.0.1:18081" \
OPENCREW_SMOKE_CADDY_HOST_HEADER="1.42.112.164.nip.io:18081" \
OPENCREW_SMOKE_CADDY_USER="opencrew" \
OPENCREW_SMOKE_CADDY_PASSWORD="<password>" \
  backend/.venv/bin/python backend/scripts/opencrew_p0_stack_smoke.py --json
```
