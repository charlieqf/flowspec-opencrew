# OpenCrew P0 Migration Runbook

日期：2026-05-27

OpenCrew P0 uses a lightweight migration runner in `backend/opcrew_backend/db/migrations.py`.

## Apply Migrations

Migrations run automatically during backend startup through `initialize_database()`.

Manual smoke check:

```bash
cd /Users/macmini-1/work/code/OpenCrew
PYTHONPATH=backend \
DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
  backend/.venv/bin/python - <<'PY'
from opcrew_backend.config import load_config
from opcrew_backend.db.bootstrap import build_engine, initialize_database

cfg = load_config(None)
engine = build_engine(cfg.database_url)
initialize_database(engine, cfg.data_dir)
engine.dispose()
print("migrations_ok")
PY
```

## Existing DB Upgrade

The runner creates `schema_migrations`, records `0001_baseline`, then applies additive P0 migrations:

- `session_events` visibility and metadata columns.
- `session_files` visibility, sensitivity, attempt, and stale columns.
- `oc_rebuild_tasks.workflow_mode`.

Each migration checks for existing columns before altering the table.

## Legacy OC-Analysis Playback Repair

Before relying on workspace-outside symlink rejection for served files, scan legacy OC-Analysis virtual playback manifests:

```bash
cd /Users/macmini-1/work/code/OpenCrew
PYTHONPATH=backend \
DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
  backend/.venv/bin/python backend/scripts/repair_legacy_oc_analysis_playback.py --json
```

Apply repairs after reviewing the dry-run:

```bash
PYTHONPATH=backend \
DATABASE_URL="postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew" \
  backend/.venv/bin/python backend/scripts/repair_legacy_oc_analysis_playback.py --write
```

The repair copies the original source video into `workspace/source_video.mp4`, rewrites virtual playback manifest fields to `source_video.mp4`, and upserts that file as public/downloadable in `session_files`.

## Empty DB Initialization

For an empty PostgreSQL database, `metadata.create_all()` creates the current base schema and the migration runner records/applies the same migration sequence idempotently.

## Rollback

The P0 baseline is additive and does not define automatic down migrations. To rollback:

1. Stop backend/frontend.
2. Restore the PostgreSQL database from a backup taken before the migration.
3. Restore the matching git commit.
4. Restart through `scripts/opencrew_local_stack.sh restart`.
