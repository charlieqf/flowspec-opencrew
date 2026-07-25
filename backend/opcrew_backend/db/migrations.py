from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import BigInteger, Column, MetaData, Table, Text, inspect, select, text
from sqlalchemy.engine import Connection, Engine

from opcrew_backend.workflow_modes import infer_openclip_workflow_mode
MigrationFn = Callable[[Connection], None]
CLIP_SEARCH_NORMALIZATION_VERSION = "nfkc_casefold_ws_v1"


def _normalize_migration_search_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", normalized).strip()

schema_migrations = Table(
    "schema_migrations",
    MetaData(),
    Column("id", Text, primary_key=True),
    Column("description", Text, nullable=False),
    Column("applied_at", BigInteger, nullable=False),
)


def table_exists(conn: Connection, table_name: str) -> bool:
    return inspect(conn).has_table(table_name)


def column_exists(conn: Connection, table_name: str, column_name: str) -> bool:
    if not table_exists(conn, table_name):
        return False
    columns = inspect(conn).get_columns(table_name)
    return column_name in {str(column["name"]) for column in columns}


def add_column_if_missing(conn: Connection, table_name: str, column_name: str, column_sql: str) -> None:
    if not table_exists(conn, table_name) or column_exists(conn, table_name, column_name):
        return
    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"))


def migration_0001_baseline(_: Connection) -> None:
    return


def migration_0002_session_event_visibility(conn: Connection) -> None:
    add_column_if_missing(conn, "session_events", "visibility", "TEXT")
    add_column_if_missing(conn, "session_events", "event_scope", "TEXT")
    add_column_if_missing(conn, "session_events", "severity", "TEXT")
    add_column_if_missing(conn, "session_events", "family", "TEXT")
    add_column_if_missing(conn, "session_events", "workflow_id", "TEXT")
    add_column_if_missing(conn, "session_events", "task_id", "INTEGER")
    add_column_if_missing(conn, "session_events", "attempt_id", "INTEGER")
    add_column_if_missing(conn, "session_events", "tool_id", "TEXT")
    add_column_if_missing(conn, "session_events", "step_id", "TEXT")


def migration_0003_session_file_policy(conn: Connection) -> None:
    add_column_if_missing(conn, "session_files", "visibility", "TEXT")
    add_column_if_missing(conn, "session_files", "sensitivity", "TEXT")
    add_column_if_missing(conn, "session_files", "attempt_id", "INTEGER")
    add_column_if_missing(conn, "session_files", "stale", "INTEGER NOT NULL DEFAULT 0")


def migration_0004_oc_rebuild_workflow_mode(conn: Connection) -> None:
    add_column_if_missing(conn, "oc_rebuild_tasks", "workflow_mode", "TEXT")


def migration_0005_phase0_local_usage_and_key_refs(conn: Connection) -> None:
    add_column_if_missing(conn, "tool_media_provider_configs", "api_key_ref", "TEXT")
    add_column_if_missing(conn, "tool_asr_provider_configs", "api_key_ref", "TEXT")
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS local_usage_log (
  id BIGSERIAL PRIMARY KEY,
  request_id TEXT,
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  modality TEXT NOT NULL,
  provider_mode TEXT NOT NULL DEFAULT 'local_box',
  billing_mode TEXT NOT NULL DEFAULT 'local_usage_only',
  proxy_policy TEXT,
  status TEXT NOT NULL,
  units_json JSONB,
  est_cost_micros BIGINT,
  error_code TEXT,
  started_at BIGINT,
  finished_at BIGINT,
  created_at BIGINT NOT NULL
)
"""
        )
    )


def migration_0006_tool_session_file_and_attempt_refs(conn: Connection) -> None:
    add_column_if_missing(conn, "session_files", "tool_use_session_id", "TEXT")
    add_column_if_missing(conn, "openclip_attempts", "tool_use_session_id", "TEXT")
    add_column_if_missing(conn, "oc_rebuild_attempts", "tool_use_session_id", "TEXT")
    add_column_if_missing(conn, "openclip_attempts", "result_manifest_json", "TEXT")
    add_column_if_missing(conn, "oc_rebuild_attempts", "result_manifest_json", "TEXT")
    if table_exists(conn, "openclip_attempts") and column_exists(conn, "openclip_attempts", "tool_use_session_id"):
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_openclip_attempts_tool_use_session_id ON openclip_attempts (tool_use_session_id)"))
    if table_exists(conn, "oc_rebuild_attempts") and column_exists(conn, "oc_rebuild_attempts", "tool_use_session_id"):
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_oc_rebuild_attempts_tool_use_session_id ON oc_rebuild_attempts (tool_use_session_id)"))


def migration_0007_openclip_storyboard_quick_config(conn: Connection) -> None:
    add_column_if_missing(conn, "openclip_tasks", "storyboard_quick_config_json", "TEXT")
    add_column_if_missing(conn, "openclip_prompt_versions", "storyboard_quick_config_json", "TEXT")


def migration_0008_local_usage_actual_cost_fields(conn: Connection) -> None:
    add_column_if_missing(conn, "local_usage_log", "actual_cost_micros", "BIGINT")
    add_column_if_missing(conn, "local_usage_log", "actual_cost_currency", "TEXT")
    add_column_if_missing(conn, "local_usage_log", "actual_cost_source", "TEXT")
    add_column_if_missing(conn, "local_usage_log", "actual_cost_raw_json", "JSONB")
    add_column_if_missing(conn, "local_usage_log", "pricebook_version", "TEXT")
    add_column_if_missing(conn, "local_usage_log", "billing_reconciled_at", "BIGINT")


def migration_0009_local_usage_artifact_attribution(conn: Connection) -> None:
    add_column_if_missing(conn, "local_usage_log", "task_id", "TEXT")
    add_column_if_missing(conn, "local_usage_log", "attempt_id", "TEXT")
    add_column_if_missing(conn, "local_usage_log", "step_id", "TEXT")
    add_column_if_missing(conn, "local_usage_log", "idempotency_key", "TEXT")
    if table_exists(conn, "local_usage_log") and column_exists(conn, "local_usage_log", "idempotency_key"):
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_local_usage_log_idempotency_key ON local_usage_log (idempotency_key)"))


def migration_0010_attempt_no_unique(conn: Connection) -> None:
    # M4: enforce unique (task_id, attempt_no) so concurrent reruns cannot allocate
    # duplicate attempt numbers. Before adding the index, resolve any pre-existing
    # duplicates from the old max()+1 race by reassigning ONLY the colliding rows
    # (the later-inserted ones) to fresh numbers above each task's current max.
    # Existing unique numbers are preserved as-is — even with gaps — so the history
    # directories named off attempt_no (see openclip_backend.router) stay consistent.
    # Plain SELECT + parameterized UPDATE keeps this dialect-agnostic (Postgres prod
    # and the SQLite contract/dev path both de-duplicate before the index is built).
    for table in ("openclip_attempts", "oc_rebuild_attempts"):
        if not table_exists(conn, table):
            continue
        if not (column_exists(conn, table, "task_id") and column_exists(conn, table, "attempt_no")):
            continue
        rows = conn.execute(
            text(f"SELECT id, task_id, attempt_no FROM {table} ORDER BY task_id, id")
        ).fetchall()
        max_no: dict[int, int] = {}
        for row in rows:
            task_id, attempt_no = int(row[1]), int(row[2])
            max_no[task_id] = max(max_no.get(task_id, attempt_no), attempt_no)
        seen: set[tuple[int, int]] = set()
        for row in rows:
            row_id, task_id, attempt_no = int(row[0]), int(row[1]), int(row[2])
            if (task_id, attempt_no) not in seen:
                seen.add((task_id, attempt_no))
                continue
            # Duplicate: bump this (later) row to the next free number for the task.
            new_no = max_no[task_id] + 1
            max_no[task_id] = new_no
            seen.add((task_id, new_no))
            conn.execute(
                text(f"UPDATE {table} SET attempt_no = :new_no WHERE id = :id"),
                {"new_no": new_no, "id": row_id},
            )
        conn.execute(
            text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{table}_task_id_attempt_no "
                f"ON {table} (task_id, attempt_no)"
            )
        )


def migration_0011_local_usage_task_scope_index(conn: Connection) -> None:
    if table_exists(conn, "local_usage_log") and all(
        column_exists(conn, "local_usage_log", column_name)
        for column_name in ("task_id", "attempt_id", "step_id")
    ):
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_local_usage_log_task_attempt_step ON local_usage_log (task_id, attempt_id, step_id)"))


def migration_0012_openclip_workflow_mode(conn: Connection) -> None:
    add_column_if_missing(conn, "openclip_tasks", "workflow_mode", "TEXT")
    if not table_exists(conn, "openclip_tasks") or not column_exists(conn, "openclip_tasks", "workflow_mode"):
        return

    openclip_columns = {str(column["name"]) for column in inspect(conn).get_columns("openclip_tasks")}
    if "id" not in openclip_columns:
        return

    has_sessions = table_exists(conn, "sessions")
    session_columns = {str(column["name"]) for column in inspect(conn).get_columns("sessions")} if has_sessions else set()
    can_join_sessions = "session_id" in openclip_columns and {"id", "workspace_dir"}.issubset(session_columns)

    select_columns = ["t.id", "t.workflow_mode"]
    if "reference_video_path" in openclip_columns:
        select_columns.append("t.reference_video_path")
    else:
        select_columns.append("'' AS reference_video_path")

    if can_join_sessions:
        query = (
            f"SELECT {', '.join(select_columns)}, s.workspace_dir "
            "FROM openclip_tasks t LEFT JOIN sessions s ON s.id = t.session_id "
            "WHERE t.workflow_mode IS NULL OR t.workflow_mode = ''"
        )
    else:
        query = (
            f"SELECT {', '.join(select_columns)}, '' AS workspace_dir "
            "FROM openclip_tasks t WHERE t.workflow_mode IS NULL OR t.workflow_mode = ''"
        )

    for row in conn.execute(text(query)).mappings().fetchall():
        workspace_dir = str(row.get("workspace_dir") or "").strip()
        mode = infer_openclip_workflow_mode(
            {
                "workflow_mode": row.get("workflow_mode"),
                "reference_video_path": row.get("reference_video_path"),
            },
            workspace=Path(workspace_dir) if workspace_dir else None,
        )
        conn.execute(
            text("UPDATE openclip_tasks SET workflow_mode = :workflow_mode WHERE id = :id"),
            {"workflow_mode": mode, "id": row["id"]},
        )


def migration_0013_talking_head_task_configs(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS talking_head_task_configs (
  task_id INTEGER PRIMARY KEY REFERENCES openclip_tasks(id) ON DELETE CASCADE,
  schema_version TEXT NOT NULL,
  script_creation_mode TEXT NOT NULL,
  config_json TEXT NOT NULL,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
)
"""
        )
    )
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_talking_head_task_configs_mode ON talking_head_task_configs (script_creation_mode)"))


def migration_0014_media_library_uploads(conn: Connection) -> None:
    add_column_if_missing(conn, "media_library_assets", "session_id", "INTEGER")
    add_column_if_missing(conn, "media_library_assets", "source_video_path", "TEXT")
    add_column_if_missing(conn, "media_library_assets", "upload_status", "TEXT NOT NULL DEFAULT 'ready'")
    if table_exists(conn, "media_library_assets") and column_exists(conn, "media_library_assets", "session_id"):
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_media_library_assets_session_id ON media_library_assets (session_id) WHERE session_id IS NOT NULL"))
    if table_exists(conn, "media_library_assets") and column_exists(conn, "media_library_assets", "upload_status"):
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_media_library_assets_upload_status ON media_library_assets (upload_status, updated_at)"))


def migration_0015_media_library_open_cut_tasks(conn: Connection) -> None:
    if not table_exists(conn, "media_library_tasks") or not table_exists(conn, "media_library_assets"):
        return
    conn.execute(
        text(
            """
INSERT INTO media_library_tasks (
  asset_id, session_id, title, status,
  dialogue_status, visual_status, composite_status,
  created_at, updated_at
)
SELECT
  asset.asset_id,
  asset.session_id,
  asset.display_name,
  'draft',
  'not_analyzed',
  'not_analyzed',
  'not_analyzed',
  asset.created_at,
  asset.updated_at
FROM media_library_assets AS asset
WHERE asset.session_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM media_library_tasks AS task WHERE task.asset_id = asset.asset_id
  )
"""
        )
    )


def migration_0016_media_library_dialogue_runs(conn: Connection) -> None:
    add_column_if_missing(conn, "media_library_tasks", "dialogue_tool_use_session_id", "TEXT")
    add_column_if_missing(conn, "media_library_tasks", "dialogue_error", "TEXT")
    add_column_if_missing(conn, "media_library_tasks", "dialogue_progress_json", "JSON")


def migration_0017_media_library_visual_runs(conn: Connection) -> None:
    add_column_if_missing(conn, "media_library_tasks", "visual_tool_use_session_id", "TEXT")
    add_column_if_missing(conn, "media_library_tasks", "visual_error", "TEXT")
    add_column_if_missing(conn, "media_library_tasks", "visual_progress_json", "JSON")


def migration_0018_media_library_upload_finalization(conn: Connection) -> None:
    add_column_if_missing(conn, "media_library_uploads", "finalization_token", "TEXT")
    add_column_if_missing(conn, "media_library_uploads", "finalization_started_at", "BIGINT")


def migration_0019_media_library_source_identity_and_analysis_runs(conn: Connection) -> None:
    add_column_if_missing(conn, "media_library_assets", "content_sha256", "TEXT")
    add_column_if_missing(conn, "media_library_assets", "content_hashed_at", "BIGINT")

    task_columns = {
        "dialogue_current_run_id": "TEXT",
        "visual_structure_status": "TEXT NOT NULL DEFAULT 'not_analyzed'",
        "visual_structure_current_run_id": "TEXT",
        "visual_semantic_status": "TEXT NOT NULL DEFAULT 'not_analyzed'",
        "visual_semantic_current_run_id": "TEXT",
        "visual_semantic_tool_use_session_id": "TEXT",
        "visual_semantic_error": "TEXT",
        "visual_semantic_progress_json": "JSON",
        "composite_current_run_id": "TEXT",
        "composite_tool_use_session_id": "TEXT",
        "composite_error": "TEXT",
        "composite_progress_json": "JSON",
    }
    for column_name, column_sql in task_columns.items():
        add_column_if_missing(conn, "media_library_tasks", column_name, column_sql)

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS media_library_analysis_runs (
  analysis_run_id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES media_library_assets(asset_id) ON DELETE CASCADE,
  scheme TEXT NOT NULL,
  source_version TEXT NOT NULL,
  status TEXT NOT NULL,
  tool_use_session_id TEXT,
  attempt_id BIGINT,
  prompt_version TEXT,
  model_config_id TEXT,
  model_session_id TEXT,
  schema_version TEXT,
  result_hash TEXT,
  result_index_path TEXT,
  upstream_refs_json JSON,
  progress_json JSON,
  error_code TEXT,
  error_json JSON,
  is_current BOOLEAN NOT NULL DEFAULT FALSE,
  started_at BIGINT,
  finished_at BIGINT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  CONSTRAINT ck_media_library_analysis_runs_scheme
    CHECK (scheme IN ('dialogue', 'visual_structure', 'visual_semantic', 'composite')),
  CONSTRAINT ck_media_library_analysis_runs_status
    CHECK (status IN ('queued', 'running', 'blocked', 'ready', 'stale', 'failed'))
)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE UNIQUE INDEX IF NOT EXISTS ux_media_library_analysis_runs_current
ON media_library_analysis_runs(asset_id, scheme)
WHERE is_current = TRUE
"""
        )
    )
    conn.execute(
        text(
            """
CREATE UNIQUE INDEX IF NOT EXISTS ux_media_library_analysis_runs_tool_session
ON media_library_analysis_runs(tool_use_session_id)
WHERE tool_use_session_id IS NOT NULL
"""
        )
    )
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_analysis_runs_asset_scheme_created
ON media_library_analysis_runs(asset_id, scheme, created_at DESC)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE UNIQUE INDEX IF NOT EXISTS ux_media_library_analysis_runs_one_active
ON media_library_analysis_runs(asset_id, scheme)
WHERE status IN ('queued', 'running')
"""
        )
    )

    if table_exists(conn, "media_library_tasks") and column_exists(
        conn, "media_library_tasks", "visual_structure_status"
    ):
        conn.execute(
            text(
                """
UPDATE media_library_tasks
SET visual_structure_status = CASE
      WHEN visual_status = 'ready' THEN 'ready'
      WHEN visual_status IN ('queued', 'running', 'processing') THEN
        CASE WHEN visual_status = 'queued' THEN 'queued' ELSE 'running' END
      WHEN visual_status = 'failed' THEN 'failed'
      ELSE 'not_analyzed'
    END,
    visual_semantic_status = 'not_analyzed',
    visual_status = CASE
      WHEN visual_status = 'ready' THEN 'partial'
      WHEN visual_status = 'processing' THEN 'running'
      ELSE visual_status
    END
"""
            )
        )


def _bigint_identity_primary_key(conn: Connection) -> str:
    if conn.dialect.name == "sqlite":
        return "INTEGER PRIMARY KEY AUTOINCREMENT"
    return "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"


def migration_0020_media_library_fragment_search(conn: Connection) -> None:
    identity = _bigint_identity_primary_key(conn)
    conn.execute(
        text(
            f"""
CREATE TABLE IF NOT EXISTS media_library_fragment_index (
  id {identity},
  asset_id TEXT NOT NULL REFERENCES media_library_assets(asset_id) ON DELETE CASCADE,
  source_session_id BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_version TEXT NOT NULL,
  analysis_scheme TEXT NOT NULL,
  analysis_run_id TEXT NOT NULL
    REFERENCES media_library_analysis_runs(analysis_run_id) ON DELETE CASCADE,
  result_hash TEXT NOT NULL,
  fragment_id TEXT NOT NULL,
  start_ms BIGINT NOT NULL,
  end_ms BIGINT NOT NULL,
  dialogue_text TEXT,
  title TEXT,
  summary TEXT,
  keywords_json JSON NOT NULL,
  visual_labels_json JSON NOT NULL,
  keyframe_ref_json JSON,
  search_text TEXT NOT NULL,
  search_lexemes_text TEXT,
  tokenizer_name TEXT NOT NULL DEFAULT 'none',
  tokenizer_version TEXT NOT NULL DEFAULT 'none',
  dictionary_hash TEXT,
  normalization_version TEXT NOT NULL DEFAULT 'nfkc_casefold_ws_v1',
  quality_status TEXT NOT NULL DEFAULT 'ready',
  confidence REAL,
  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  CONSTRAINT uq_media_library_fragment_run_fragment
    UNIQUE (analysis_run_id, fragment_id),
  CONSTRAINT ck_media_library_fragment_scheme
    CHECK (analysis_scheme IN ('dialogue', 'composite')),
  CONSTRAINT ck_media_library_fragment_time_range
    CHECK (start_ms >= 0 AND end_ms > start_ms),
  CONSTRAINT ck_media_library_fragment_quality
    CHECK (quality_status IN ('ready', 'review')),
  CONSTRAINT ck_media_library_fragment_confidence
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_fragment_active_scheme_asset
ON media_library_fragment_index(is_active, analysis_scheme, asset_id)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_fragment_asset_scheme_active
ON media_library_fragment_index(asset_id, analysis_scheme, is_active)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_fragment_analysis_run
ON media_library_fragment_index(analysis_run_id)
"""
        )
    )

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS media_library_search_runs (
  search_id TEXT PRIMARY KEY,
  entry_point TEXT NOT NULL,
  target_task_id BIGINT,
  dialogue_asset_key TEXT,
  source_asset_id TEXT,
  query_source TEXT NOT NULL,
  query_hash TEXT NOT NULL,
  query_plan_json JSON NOT NULL,
  planner_version TEXT NOT NULL,
  retrieval_version TEXT NOT NULL,
  planner_degraded BOOLEAN NOT NULL,
  requested_sources_json JSON NOT NULL,
  source_runs_json JSON NOT NULL,
  status TEXT NOT NULL,
  result_count INTEGER NOT NULL DEFAULT 0,
  zero_result BOOLEAN NOT NULL DEFAULT TRUE,
  planner_latency_ms BIGINT,
  retrieval_latency_ms BIGINT,
  total_latency_ms BIGINT,
  top_candidates_json JSON NOT NULL,
  error_code TEXT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  CONSTRAINT ck_media_library_search_entry_point
    CHECK (entry_point IN ('storyboard', 'agent', 'editor')),
  CONSTRAINT ck_media_library_search_query_source
    CHECK (query_source IN ('dialogue', 'manual', 'planner')),
  CONSTRAINT ck_media_library_search_status
    CHECK (status IN ('queued', 'running', 'completed', 'failed'))
)
"""
        )
    )

    conn.execute(
        text(
            f"""
CREATE TABLE IF NOT EXISTS media_library_search_actions (
  id {identity},
  search_id TEXT NOT NULL
    REFERENCES media_library_search_runs(search_id) ON DELETE CASCADE,
  action_kind TEXT NOT NULL,
  source TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  source_asset_id TEXT,
  candidate_rank INTEGER,
  target_task_id BIGINT,
  metadata_json JSON NOT NULL,
  created_at BIGINT NOT NULL,
  CONSTRAINT ck_media_library_search_action_kind
    CHECK (action_kind IN ('preview', 'open_editor', 'import'))
)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_search_action_search_created
ON media_library_search_actions(search_id, created_at)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_search_action_kind_created
ON media_library_search_actions(action_kind, created_at)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_search_action_target_created
ON media_library_search_actions(target_task_id, created_at)
"""
        )
    )


def migration_0021_media_library_clip_derivatives(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS media_library_clip_derivatives (
  clip_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  source_asset_id TEXT NOT NULL
    REFERENCES media_library_assets(asset_id) ON DELETE CASCADE,
  source_session_id BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_version TEXT NOT NULL,
  source_start_ms BIGINT NOT NULL,
  source_end_ms BIGINT NOT NULL,
  source_scheme TEXT,
  source_fragment_id TEXT,
  source_analysis_run_id TEXT,
  source_search_id TEXT,
  source_dialogue_asset_key TEXT,
  output_path TEXT NOT NULL,
  display_name TEXT NOT NULL,
  duration_ms BIGINT NOT NULL,
  content_sha256 TEXT NOT NULL,
  size_bytes BIGINT NOT NULL,
  operation TEXT NOT NULL DEFAULT 'precise_reencode_v1',
  search_eligible BOOLEAN NOT NULL DEFAULT FALSE,
  created_at BIGINT NOT NULL,
  CONSTRAINT uq_media_library_clip_session_output
    UNIQUE (source_session_id, output_path),
  CONSTRAINT ck_media_library_clip_source_range
    CHECK (source_start_ms >= 0 AND source_end_ms > source_start_ms),
  CONSTRAINT ck_media_library_clip_duration CHECK (duration_ms > 0),
  CONSTRAINT ck_media_library_clip_not_search_eligible
    CHECK (search_eligible = FALSE),
  CONSTRAINT ck_media_library_clip_output_path
    CHECK (
      output_path <> ''
      AND output_path NOT LIKE '/%'
      AND output_path NOT LIKE '../%'
      AND output_path NOT LIKE '%/../%'
    )
)
"""
        )
    )


def migration_0022_media_library_storyboard_imports(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS media_library_storyboard_imports (
  import_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  source_kind TEXT NOT NULL,
  source_asset_id TEXT NOT NULL
    REFERENCES media_library_assets(asset_id) ON DELETE CASCADE,
  source_clip_id TEXT REFERENCES media_library_clip_derivatives(clip_id),
  source_version TEXT NOT NULL,
  source_search_id TEXT
    REFERENCES media_library_search_runs(search_id) ON DELETE SET NULL,
  source_dialogue_asset_key TEXT,
  target_task_id BIGINT NOT NULL
    REFERENCES openclip_tasks(id) ON DELETE CASCADE,
  target_session_id BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  target_path TEXT NOT NULL,
  target_manifest_asset_id TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  size_bytes BIGINT NOT NULL,
  requested_name TEXT,
  status TEXT NOT NULL,
  error_code TEXT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  CONSTRAINT uq_media_library_storyboard_import_target_path
    UNIQUE (target_session_id, target_path),
  CONSTRAINT ck_media_library_storyboard_import_source_kind
    CHECK (source_kind IN ('media_library_original', 'media_library_clip')),
  CONSTRAINT ck_media_library_storyboard_import_status
    CHECK (status IN ('preparing', 'completed', 'failed')),
  CONSTRAINT ck_media_library_storyboard_import_target_path
    CHECK (
      target_path <> ''
      AND target_path NOT LIKE '/%'
      AND target_path NOT LIKE '../%'
      AND target_path NOT LIKE '%/../%'
    )
)
"""
        )
    )


def _create_fragment_indexes(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_fragment_active_scheme_asset
ON media_library_fragment_index(is_active, analysis_scheme, asset_id)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_fragment_asset_scheme_active
ON media_library_fragment_index(asset_id, analysis_scheme, is_active)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_fragment_analysis_run
ON media_library_fragment_index(analysis_run_id)
"""
        )
    )


def migration_0023_media_library_visual_search(conn: Connection) -> None:
    """Allow only validated visual-semantic rows in the shared fragment table."""

    if not table_exists(conn, "media_library_fragment_index"):
        raise RuntimeError("media_library_fragment_index_missing_for_0023")
    if conn.dialect.name != "sqlite":
        conn.execute(
            text(
                """
ALTER TABLE media_library_fragment_index
DROP CONSTRAINT IF EXISTS ck_media_library_fragment_scheme
"""
            )
        )
        conn.execute(
            text(
                """
ALTER TABLE media_library_fragment_index
ADD CONSTRAINT ck_media_library_fragment_scheme
CHECK (analysis_scheme IN ('dialogue', 'visual_semantic', 'composite'))
"""
            )
        )
        _create_fragment_indexes(conn)
        return

    driver_connection = conn.connection.driver_connection
    if not bool(getattr(driver_connection, "in_transaction", False)):
        # Python's sqlite3 legacy transaction mode does not begin on DDL.
        # Start the DB transaction explicitly so table rebuild failures roll
        # back the temporary table, copied rows, rename, indexes, and triggers.
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    create_sql = str(
        conn.execute(
            text(
                """
SELECT sql FROM sqlite_master
WHERE type = 'table' AND name = 'media_library_fragment_index'
"""
            )
        ).scalar_one()
    )
    if "'visual_semantic'" in create_sql:
        _create_fragment_indexes(conn)
        return

    temporary_table = "media_library_fragment_index__0023"
    if table_exists(conn, temporary_table):
        raise RuntimeError("media_library_fragment_index_0023_temp_exists")
    triggers = [
        (str(row.name), str(row.sql))
        for row in conn.execute(
            text(
                """
SELECT name, sql FROM sqlite_master
WHERE type = 'trigger'
  AND tbl_name = 'media_library_fragment_index'
  AND sql IS NOT NULL
ORDER BY name
"""
            )
        ).mappings()
    ]
    before_count = int(
        conn.execute(
            text("SELECT count(*) FROM media_library_fragment_index")
        ).scalar_one()
    )
    conn.execute(
        text(
            f"""
CREATE TABLE {temporary_table} (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  asset_id TEXT NOT NULL,
  source_session_id BIGINT NOT NULL,
  source_version TEXT NOT NULL,
  analysis_scheme TEXT NOT NULL,
  analysis_run_id TEXT NOT NULL,
  result_hash TEXT NOT NULL,
  fragment_id TEXT NOT NULL,
  start_ms BIGINT NOT NULL,
  end_ms BIGINT NOT NULL,
  dialogue_text TEXT,
  title TEXT,
  summary TEXT,
  keywords_json JSON NOT NULL,
  visual_labels_json JSON NOT NULL,
  keyframe_ref_json JSON,
  search_text TEXT NOT NULL,
  search_lexemes_text TEXT,
  tokenizer_name TEXT NOT NULL DEFAULT 'none',
  tokenizer_version TEXT NOT NULL DEFAULT 'none',
  dictionary_hash TEXT,
  normalization_version TEXT NOT NULL DEFAULT 'nfkc_casefold_ws_v1',
  quality_status TEXT NOT NULL DEFAULT 'ready',
  confidence FLOAT,
  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  CONSTRAINT uq_media_library_fragment_run_fragment UNIQUE (analysis_run_id, fragment_id),
  CONSTRAINT ck_media_library_fragment_scheme CHECK (analysis_scheme IN ('dialogue', 'visual_semantic', 'composite')),
  CONSTRAINT ck_media_library_fragment_time_range CHECK (start_ms >= 0 AND end_ms > start_ms),
  CONSTRAINT ck_media_library_fragment_quality CHECK (quality_status IN ('ready', 'review')),
  CONSTRAINT ck_media_library_fragment_confidence CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  FOREIGN KEY(asset_id) REFERENCES media_library_assets(asset_id) ON DELETE CASCADE,
  FOREIGN KEY(source_session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY(analysis_run_id) REFERENCES media_library_analysis_runs(analysis_run_id) ON DELETE CASCADE
)
"""
        )
    )
    columns = (
        "id, asset_id, source_session_id, source_version, analysis_scheme, "
        "analysis_run_id, result_hash, fragment_id, start_ms, end_ms, "
        "dialogue_text, title, summary, keywords_json, visual_labels_json, "
        "keyframe_ref_json, search_text, search_lexemes_text, tokenizer_name, "
        "tokenizer_version, dictionary_hash, normalization_version, "
        "quality_status, confidence, is_active, created_at, updated_at"
    )
    conn.execute(
        text(
            f"""
INSERT INTO {temporary_table} ({columns})
SELECT {columns} FROM media_library_fragment_index
"""
        )
    )
    copied_count = int(
        conn.execute(
            text(f"SELECT count(*) FROM {temporary_table}")
        ).scalar_one()
    )
    if copied_count != before_count:
        raise RuntimeError("media_library_fragment_index_0023_copy_mismatch")
    conn.execute(text("DROP TABLE media_library_fragment_index"))
    conn.execute(
        text(
            f"ALTER TABLE {temporary_table} "
            "RENAME TO media_library_fragment_index"
        )
    )
    _create_fragment_indexes(conn)
    for _trigger_name, trigger_sql in triggers:
        conn.execute(text(trigger_sql))


def _create_clip_search_indexes(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE INDEX IF NOT EXISTS ix_media_library_clip_search_eligible_source
ON media_library_clip_derivatives(search_eligible, source_asset_id, created_at)
"""
        )
    )


def _backfill_clip_search_text(conn: Connection) -> None:
    rows = conn.execute(
        text(
            """
SELECT clip_id, display_name
FROM media_library_clip_derivatives
ORDER BY clip_id
"""
        )
    ).mappings()
    for row in rows:
        conn.execute(
            text(
                """
UPDATE media_library_clip_derivatives
SET search_text = :search_text,
    search_normalization_version = :normalization_version,
    search_eligible = FALSE,
    search_enabled_at = NULL,
    search_updated_at = NULL
WHERE clip_id = :clip_id
"""
            ),
            {
                "clip_id": str(row["clip_id"]),
                "search_text": _normalize_migration_search_text(
                    row["display_name"]
                ),
                "normalization_version": CLIP_SEARCH_NORMALIZATION_VERSION,
            },
        )


def migration_0024_media_library_clip_search(conn: Connection) -> None:
    """Add explicit, metadata-only global-search eligibility to clips."""

    if not table_exists(conn, "media_library_clip_derivatives"):
        raise RuntimeError("media_library_clip_derivatives_missing_for_0024")

    if conn.dialect.name != "sqlite":
        conn.execute(
            text(
                """
ALTER TABLE media_library_clip_derivatives
DROP CONSTRAINT IF EXISTS ck_media_library_clip_not_search_eligible
"""
            )
        )
        add_column_if_missing(
            conn,
            "media_library_clip_derivatives",
            "tags_json",
            "JSON NOT NULL DEFAULT '[]'",
        )
        add_column_if_missing(
            conn,
            "media_library_clip_derivatives",
            "search_text",
            "TEXT NOT NULL DEFAULT ''",
        )
        add_column_if_missing(
            conn,
            "media_library_clip_derivatives",
            "search_normalization_version",
            "TEXT NOT NULL DEFAULT 'nfkc_casefold_ws_v1'",
        )
        add_column_if_missing(
            conn,
            "media_library_clip_derivatives",
            "search_enabled_at",
            "BIGINT NULL",
        )
        add_column_if_missing(
            conn,
            "media_library_clip_derivatives",
            "search_updated_at",
            "BIGINT NULL",
        )
        _backfill_clip_search_text(conn)
        _create_clip_search_indexes(conn)
        return

    driver_connection = conn.connection.driver_connection
    if not bool(getattr(driver_connection, "in_transaction", False)):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    create_sql = str(
        conn.execute(
            text(
                """
SELECT sql FROM sqlite_master
WHERE type = 'table' AND name = 'media_library_clip_derivatives'
"""
            )
        ).scalar_one()
    )
    current_columns = {
        str(column[1])
        for column in conn.exec_driver_sql(
            "PRAGMA table_info(media_library_clip_derivatives)"
        ).all()
    }
    required_columns = {
        "tags_json",
        "search_text",
        "search_normalization_version",
        "search_enabled_at",
        "search_updated_at",
    }
    if (
        required_columns.issubset(current_columns)
        and "ck_media_library_clip_not_search_eligible" not in create_sql
    ):
        _backfill_clip_search_text(conn)
        _create_clip_search_indexes(conn)
        return

    temporary_table = "media_library_clip_derivatives__0024"
    if table_exists(conn, temporary_table):
        raise RuntimeError("media_library_clip_derivatives_0024_temp_exists")
    triggers = [
        (str(row.name), str(row.sql))
        for row in conn.execute(
            text(
                """
SELECT name, sql FROM sqlite_master
WHERE type = 'trigger'
  AND tbl_name = 'media_library_clip_derivatives'
  AND sql IS NOT NULL
ORDER BY name
"""
            )
        ).mappings()
    ]
    before_count = int(
        conn.execute(
            text("SELECT count(*) FROM media_library_clip_derivatives")
        ).scalar_one()
    )
    # The table is referenced by StoryBoard imports. Defer those checks until
    # the rebuilt table has reclaimed the authoritative table name.
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    conn.execute(
        text(
            f"""
CREATE TABLE {temporary_table} (
  clip_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  source_asset_id TEXT NOT NULL,
  source_session_id BIGINT NOT NULL,
  source_version TEXT NOT NULL,
  source_start_ms BIGINT NOT NULL,
  source_end_ms BIGINT NOT NULL,
  source_scheme TEXT,
  source_fragment_id TEXT,
  source_analysis_run_id TEXT,
  source_search_id TEXT,
  source_dialogue_asset_key TEXT,
  output_path TEXT NOT NULL,
  display_name TEXT NOT NULL,
  duration_ms BIGINT NOT NULL,
  content_sha256 TEXT NOT NULL,
  size_bytes BIGINT NOT NULL,
  operation TEXT NOT NULL DEFAULT 'precise_reencode_v1',
  search_eligible BOOLEAN NOT NULL DEFAULT FALSE,
  tags_json JSON NOT NULL DEFAULT '[]',
  search_text TEXT NOT NULL DEFAULT '',
  search_normalization_version TEXT NOT NULL DEFAULT 'nfkc_casefold_ws_v1',
  search_enabled_at BIGINT,
  search_updated_at BIGINT,
  created_at BIGINT NOT NULL,
  PRIMARY KEY (clip_id),
  CONSTRAINT uq_media_library_clip_session_output UNIQUE (source_session_id, output_path),
  CONSTRAINT ck_media_library_clip_source_range CHECK (source_start_ms >= 0 AND source_end_ms > source_start_ms),
  CONSTRAINT ck_media_library_clip_duration CHECK (duration_ms > 0),
  CONSTRAINT ck_media_library_clip_output_path CHECK (output_path <> '' AND output_path NOT LIKE '/%' AND output_path NOT LIKE '../%' AND output_path NOT LIKE '%/../%'),
  UNIQUE (idempotency_key),
  FOREIGN KEY(source_asset_id) REFERENCES media_library_assets(asset_id) ON DELETE CASCADE,
  FOREIGN KEY(source_session_id) REFERENCES sessions(id) ON DELETE CASCADE
)
"""
        )
    )
    existing_columns = (
        "clip_id, idempotency_key, source_asset_id, source_session_id, "
        "source_version, source_start_ms, source_end_ms, source_scheme, "
        "source_fragment_id, source_analysis_run_id, source_search_id, "
        "source_dialogue_asset_key, output_path, display_name, duration_ms, "
        "content_sha256, size_bytes, operation, created_at"
    )
    conn.execute(
        text(
            f"""
INSERT INTO {temporary_table} ({existing_columns})
SELECT {existing_columns} FROM media_library_clip_derivatives
"""
        )
    )
    copied_count = int(
        conn.execute(text(f"SELECT count(*) FROM {temporary_table}")).scalar_one()
    )
    if copied_count != before_count:
        raise RuntimeError("media_library_clip_derivatives_0024_copy_mismatch")
    conn.execute(text("DROP TABLE media_library_clip_derivatives"))
    conn.execute(
        text(
            f"ALTER TABLE {temporary_table} "
            "RENAME TO media_library_clip_derivatives"
        )
    )
    _backfill_clip_search_text(conn)
    _create_clip_search_indexes(conn)
    for _trigger_name, trigger_sql in triggers:
        conn.execute(text(trigger_sql))
    foreign_key_violations = conn.exec_driver_sql(
        "PRAGMA foreign_key_check"
    ).all()
    if foreign_key_violations:
        raise RuntimeError("media_library_clip_derivatives_0024_fk_mismatch")
    # DROP TABLE creates a deferred parent-delete event even after the new
    # table has reclaimed the same name. The explicit check above proves the
    # final graph is valid; clearing deferred mode prevents that obsolete
    # event from rejecting the otherwise valid transaction at COMMIT.
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=OFF")


def migration_0025_video_interaction_threads(conn: Connection) -> None:
    """Add durable Gemini Omni thread/turn state without touching existing media."""

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS video_interaction_threads (
  thread_id TEXT PRIMARY KEY,
  task_id BIGINT NOT NULL,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  actor_id TEXT NOT NULL,
  chat_session_id TEXT,
  model_alias TEXT NOT NULL,
  internal_provider TEXT NOT NULL,
  internal_model TEXT NOT NULL,
  head_turn_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  lease_token TEXT,
  lease_expires_at BIGINT,
  row_version BIGINT NOT NULL DEFAULT 0,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  CONSTRAINT uq_video_interaction_thread_scope UNIQUE (thread_id, task_id, actor_id)
)
"""
        )
    )
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS video_interaction_turns (
  turn_id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES video_interaction_threads(thread_id) ON DELETE CASCADE,
  task_id BIGINT NOT NULL,
  actor_id TEXT NOT NULL,
  parent_turn_id TEXT REFERENCES video_interaction_turns(turn_id) ON DELETE SET NULL,
  client_action_id TEXT NOT NULL,
  client_action_scope TEXT NOT NULL,
  request_config_json JSON,
  usage_request_id TEXT,
  local_usage_id TEXT,
  interaction_id TEXT,
  operation TEXT NOT NULL,
  prompt TEXT NOT NULL,
  input_asset_id TEXT,
  output_asset_id TEXT,
  output_path TEXT,
  status TEXT NOT NULL,
  provider_request_status TEXT NOT NULL DEFAULT 'not_sent',
  provider_state_status TEXT NOT NULL DEFAULT 'pending',
  provider_state_expires_at BIGINT,
  provider_expiry_source TEXT NOT NULL DEFAULT 'unknown',
  delete_status TEXT NOT NULL DEFAULT 'not_requested',
  delete_attempts INTEGER NOT NULL DEFAULT 0,
  delete_error TEXT,
  expected_head_turn_id TEXT,
  expected_row_version BIGINT NOT NULL DEFAULT 0,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  CONSTRAINT uq_video_interaction_turn_action_operation UNIQUE (task_id, actor_id, operation, client_action_id),
  CONSTRAINT uq_video_interaction_turn_action UNIQUE (task_id, actor_id, client_action_id),
  CONSTRAINT uq_video_interaction_turn_usage_request UNIQUE (usage_request_id)
)
"""
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_video_interaction_threads_task_actor ON video_interaction_threads (task_id, actor_id)",
        "CREATE INDEX IF NOT EXISTS ix_video_interaction_threads_lease ON video_interaction_threads (status, lease_expires_at)",
        "CREATE INDEX IF NOT EXISTS ix_video_interaction_turns_thread_created ON video_interaction_turns (thread_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_video_interaction_turns_parent ON video_interaction_turns (parent_turn_id)",
        "CREATE INDEX IF NOT EXISTS ix_video_interaction_turns_pending ON video_interaction_turns (status, updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_video_interaction_turns_delete ON video_interaction_turns (delete_status, updated_at)",
    ):
        conn.execute(text(statement))


MIGRATIONS: list[tuple[str, str, MigrationFn]] = [
    ("0001_baseline", "Record the current pre-P0 schema as the migration baseline", migration_0001_baseline),
    ("0002_session_event_visibility", "Add session event visibility and routing metadata", migration_0002_session_event_visibility),
    ("0003_session_file_policy", "Add session file visibility and retention metadata", migration_0003_session_file_policy),
    ("0004_oc_rebuild_workflow_mode", "Add OC-Rebuild workflow mode discriminator", migration_0004_oc_rebuild_workflow_mode),
    ("0005_phase0_local_usage_and_key_refs", "Add Phase 0 local usage log and provider key refs", migration_0005_phase0_local_usage_and_key_refs),
    ("0006_tool_session_file_and_attempt_refs", "Add Tool Use Session file and attempt references", migration_0006_tool_session_file_and_attempt_refs),
    ("0007_openclip_storyboard_quick_config", "Add Analysis_V1 quick storyboard config columns", migration_0007_openclip_storyboard_quick_config),
    ("0008_local_usage_actual_cost_fields", "Add provider actual cost fields to local usage log", migration_0008_local_usage_actual_cost_fields),
    ("0009_local_usage_artifact_attribution", "Add local usage task, attempt, step, and idempotency attribution", migration_0009_local_usage_artifact_attribution),
    ("0010_attempt_no_unique", "Add unique (task_id, attempt_no) index on attempt tables", migration_0010_attempt_no_unique),
    ("0011_local_usage_task_scope_index", "Add task scope index for local usage metering reports", migration_0011_local_usage_task_scope_index),
    ("0012_openclip_workflow_mode", "Add OpenClip task workflow mode discriminator", migration_0012_openclip_workflow_mode),
    ("0013_talking_head_task_configs", "Add isolated TalkingHead_V1 editable configuration", migration_0013_talking_head_task_configs),
    ("0014_media_library_uploads", "Add isolated media-library upload session fields", migration_0014_media_library_uploads),
    ("0015_media_library_open_cut_tasks", "Add one isolated OpenCut task for each media asset session", migration_0015_media_library_open_cut_tasks),
    ("0016_media_library_dialogue_runs", "Add durable OpenCut dialogue analysis run state", migration_0016_media_library_dialogue_runs),
    ("0017_media_library_visual_runs", "Add durable OpenCut Scene Detect visual run state", migration_0017_media_library_visual_runs),
    ("0018_media_library_upload_finalization", "Add durable and idempotent media upload finalization state", migration_0018_media_library_upload_finalization),
    (
        "0019_media_library_source_identity_and_analysis_runs",
        "Add immutable media source identity, business analysis runs, and split visual status",
        migration_0019_media_library_source_identity_and_analysis_runs,
    ),
    (
        "0020_media_library_fragment_search",
        "Add central media fragment index and privacy-safe search telemetry",
        migration_0020_media_library_fragment_search,
    ),
    (
        "0021_media_library_clip_derivatives",
        "Add non-search-eligible precise clip derivative provenance",
        migration_0021_media_library_clip_derivatives,
    ),
    (
        "0022_media_library_storyboard_imports",
        "Add idempotent cross-session StoryBoard import provenance",
        migration_0022_media_library_storyboard_imports,
    ),
    (
        "0023_media_library_visual_search",
        "Allow visual-semantic fragments in the shared media search index",
        migration_0023_media_library_visual_search,
    ),
    (
        "0024_media_library_clip_search",
        "Add explicit metadata-only global search eligibility for derived clips",
        migration_0024_media_library_clip_search,
    ),
    (
        "0025_video_interaction_threads",
        "Add durable stateful video interaction threads, turns, leases, and idempotency",
        migration_0025_video_interaction_threads,
    ),
]


def run_migrations(engine: Engine) -> None:
    with engine.begin() as conn:
        schema_migrations.create(conn, checkfirst=True)
        applied = {
            str(row[0])
            for row in conn.execute(select(schema_migrations.c.id)).fetchall()
        }
        for migration_id, description, upgrade in MIGRATIONS:
            if migration_id in applied:
                continue
            upgrade(conn)
            conn.execute(
                schema_migrations.insert().values(id=migration_id, description=description, applied_at=int(time.time() * 1000))
            )
