from __future__ import annotations

import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.db import migrations
from opcrew_backend.db.migrations import MIGRATIONS, run_migrations
from opcrew_backend.db.schema import metadata


POSTGRES_URL = os.getenv("OPENCREW_TEST_POSTGRES_URL", "").strip()


@contextmanager
def isolated_postgres_schema():
    if not POSTGRES_URL:
        pytest.skip("OPENCREW_TEST_POSTGRES_URL is required for PostgreSQL migration acceptance")
    schema_name = f"oc_ml_migration_{uuid.uuid4().hex}"
    admin_engine = create_engine(POSTGRES_URL, future=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    scoped_engine = create_engine(
        POSTGRES_URL,
        future=True,
        connect_args={"options": f"-csearch_path={schema_name}"},
    )
    try:
        yield scoped_engine
    finally:
        scoped_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def migration_ids(engine) -> list[str]:
    with engine.connect() as connection:
        return [
            str(row[0])
            for row in connection.execute(
                text("SELECT id FROM schema_migrations ORDER BY id")
            ).all()
        ]


def test_postgres_empty_schema_runs_all_migrations() -> None:
    with isolated_postgres_schema() as engine:
        metadata.create_all(engine)
        run_migrations(engine)

        inspector = inspect(engine)
        assert migration_ids(engine) == [migration[0] for migration in MIGRATIONS]
        assert {
            "media_library_analysis_runs",
            "media_library_fragment_index",
            "media_library_search_runs",
            "media_library_search_actions",
            "media_library_clip_derivatives",
            "media_library_storyboard_imports",
        }.issubset(inspector.get_table_names())
        assert {
            "content_sha256",
            "content_hashed_at",
        }.issubset({column["name"] for column in inspector.get_columns("media_library_assets")})


def test_postgres_existing_0018_schema_preserves_data_and_runs_through_0023() -> None:
    with isolated_postgres_schema() as engine:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sessions (id BIGINT PRIMARY KEY)"))
            connection.execute(
                text(
                    "CREATE TABLE openclip_tasks ("
                    "id BIGINT PRIMARY KEY, session_id BIGINT REFERENCES sessions(id))"
                )
            )
            connection.execute(
                text(
                    """
CREATE TABLE media_library_assets (
  asset_id TEXT PRIMARY KEY,
  session_id BIGINT REFERENCES sessions(id),
  display_name TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  upload_status TEXT NOT NULL DEFAULT 'ready',
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
)
"""
                )
            )
            connection.execute(
                text(
                    """
CREATE TABLE media_library_tasks (
  id BIGINT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES media_library_assets(asset_id),
  session_id BIGINT NOT NULL REFERENCES sessions(id),
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  dialogue_status TEXT NOT NULL DEFAULT 'not_analyzed',
  dialogue_tool_use_session_id TEXT,
  dialogue_error TEXT,
  dialogue_progress_json JSON,
  visual_status TEXT NOT NULL DEFAULT 'not_analyzed',
  visual_tool_use_session_id TEXT,
  visual_error TEXT,
  visual_progress_json JSON,
  composite_status TEXT NOT NULL DEFAULT 'not_analyzed',
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
)
"""
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE schema_migrations ("
                    "id TEXT PRIMARY KEY, description TEXT NOT NULL, applied_at BIGINT NOT NULL)"
                )
            )
            connection.execute(text("INSERT INTO sessions (id) VALUES (1)"))
            connection.execute(
                text("INSERT INTO openclip_tasks (id, session_id) VALUES (1, 1)")
            )
            connection.execute(
                text(
                    """
INSERT INTO media_library_assets (
  asset_id, session_id, display_name, original_filename,
  upload_status, created_at, updated_at
) VALUES ('legacy-ready', 1, '旧画面结果', 'legacy.mp4', 'ready', 1, 1)
"""
                )
            )
            connection.execute(
                text(
                    """
INSERT INTO media_library_tasks (
  id, asset_id, session_id, title, status,
  dialogue_status, visual_status, composite_status,
  created_at, updated_at
) VALUES (
  1, 'legacy-ready', 1, '旧画面结果', 'draft',
  'ready', 'ready', 'not_analyzed', 1, 1
)
"""
                )
            )
            for migration_id, description, _ in MIGRATIONS:
                if migration_id == "0019_media_library_source_identity_and_analysis_runs":
                    break
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations (id, description, applied_at) "
                        "VALUES (:id, :description, 1)"
                    ),
                    {"id": migration_id, "description": description},
                )

        run_migrations(engine)

        inspector = inspect(engine)
        assert migration_ids(engine) == [migration[0] for migration in MIGRATIONS]
        assert {
            "media_library_analysis_runs",
            "media_library_fragment_index",
            "media_library_clip_derivatives",
            "media_library_storyboard_imports",
        }.issubset(inspector.get_table_names())
        with engine.connect() as connection:
            task = connection.execute(
                text(
                    "SELECT visual_status, visual_structure_status, visual_semantic_status "
                    "FROM media_library_tasks WHERE asset_id = 'legacy-ready'"
                )
            ).mappings().one()
            asset_count = int(
                connection.execute(
                    text("SELECT count(*) FROM media_library_assets WHERE asset_id = 'legacy-ready'")
                ).scalar_one()
            )
        assert task == {
            "visual_status": "partial",
            "visual_structure_status": "ready",
            "visual_semantic_status": "not_analyzed",
        }
        assert asset_count == 1


def test_postgres_existing_0022_fragment_rows_upgrade_to_visual_scheme() -> None:
    with isolated_postgres_schema() as engine:
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text("DROP TABLE media_library_fragment_index")
            )
            migrations.migration_0020_media_library_fragment_search(
                connection
            )
            migrations.schema_migrations.create(connection, checkfirst=True)
            for migration_id, description, _upgrade in MIGRATIONS:
                if migration_id == "0023_media_library_visual_search":
                    break
                connection.execute(
                    migrations.schema_migrations.insert().values(
                        id=migration_id,
                        description=description,
                        applied_at=1,
                    )
                )
            connection.execute(
                text(
                    """
INSERT INTO sessions (
  id, source, group_id, title, status, workspace_dir,
  created_at, updated_at
) VALUES (
  9101, 'test', 'test', 'pg 0023', 'draft', '/tmp/pg-0023', 1, 1
)
"""
                )
            )
            connection.execute(
                text(
                    """
INSERT INTO media_library_assets (
  asset_id, session_id, display_name, original_filename,
  source_video_path, content_sha256, media_type, upload_status,
  analysis_status, subtitle_mode, archived, referenced_by_count,
  created_at, updated_at
) VALUES (
  'pg-asset-0023', 9101, 'pg 0023', 'source.mp4', 'inbox/source.mp4',
  :source_version, 'video', 'ready', 'not_analyzed', 'unknown',
  FALSE, 0, 1, 1
)
"""
                ),
                {"source_version": "a" * 64},
            )
            connection.execute(
                text(
                    """
INSERT INTO media_library_analysis_runs (
  analysis_run_id, asset_id, scheme, source_version, status,
  result_hash, is_current, created_at, updated_at
) VALUES (
  'pg-run-dialogue-0023', 'pg-asset-0023', 'dialogue',
  :source_version, 'ready', :result_hash, TRUE, 1, 1
), (
  'pg-run-visual-0023', 'pg-asset-0023', 'visual_semantic',
  :source_version, 'ready', :visual_hash, TRUE, 2, 2
)
"""
                ),
                {
                    "source_version": "a" * 64,
                    "result_hash": "b" * 64,
                    "visual_hash": "c" * 64,
                },
            )
            connection.execute(
                text(
                    """
INSERT INTO media_library_fragment_index (
  id, asset_id, source_session_id, source_version, analysis_scheme,
  analysis_run_id, result_hash, fragment_id, start_ms, end_ms,
  keywords_json, visual_labels_json, search_text, is_active,
  created_at, updated_at
) VALUES (
  9201, 'pg-asset-0023', 9101, :source_version, 'dialogue',
  'pg-run-dialogue-0023', :result_hash, 'dialogue_0001', 0, 1000,
  '[]', '[]', 'preserved dialogue', TRUE, 1, 1
)
"""
                ),
                {
                    "source_version": "a" * 64,
                    "result_hash": "b" * 64,
                },
            )

        run_migrations(engine)

        inspector = inspect(engine)
        checks = {
            str(item.get("name") or ""): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(
                "media_library_fragment_index"
            )
        }
        assert "visual_semantic" in checks[
            "ck_media_library_fragment_scheme"
        ]
        with engine.begin() as connection:
            preserved = connection.execute(
                text(
                    "SELECT id, fragment_id, search_text FROM "
                    "media_library_fragment_index WHERE id = 9201"
                )
            ).mappings().one()
            assert dict(preserved) == {
                "id": 9201,
                "fragment_id": "dialogue_0001",
                "search_text": "preserved dialogue",
            }
            connection.execute(
                text(
                    """
INSERT INTO media_library_fragment_index (
  asset_id, source_session_id, source_version, analysis_scheme,
  analysis_run_id, result_hash, fragment_id, start_ms, end_ms,
  keywords_json, visual_labels_json, search_text, is_active,
  created_at, updated_at
) VALUES (
  'pg-asset-0023', 9101, :source_version, 'visual_semantic',
  'pg-run-visual-0023', :result_hash, 'scene_0001', 0, 1000,
  '[]', '[]', '玻璃碗 深色液体', TRUE, 2, 2
)
"""
                ),
                {
                    "source_version": "a" * 64,
                    "result_hash": "c" * 64,
                },
            )
        assert migration_ids(engine) == [
            migration[0] for migration in MIGRATIONS
        ]


def test_postgres_existing_0023_clip_rows_upgrade_closed_to_clip_search() -> None:
    with isolated_postgres_schema() as engine:
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE media_library_storyboard_imports"))
            connection.execute(text("DROP TABLE media_library_clip_derivatives"))
            migrations.migration_0021_media_library_clip_derivatives(connection)
            migrations.migration_0022_media_library_storyboard_imports(connection)
            migrations.schema_migrations.create(connection, checkfirst=True)
            for migration_id, description, _upgrade in MIGRATIONS:
                if migration_id == "0024_media_library_clip_search":
                    break
                connection.execute(
                    migrations.schema_migrations.insert().values(
                        id=migration_id,
                        description=description,
                        applied_at=1,
                    )
                )
            connection.execute(
                text(
                    """
INSERT INTO sessions (
  id, source, group_id, title, status, workspace_dir,
  created_at, updated_at
) VALUES (
  9301, 'test', 'test', 'pg 0024', 'draft', '/tmp/pg-0024', 1, 1
)
"""
                )
            )
            connection.execute(
                text(
                    """
INSERT INTO media_library_assets (
  asset_id, session_id, display_name, original_filename,
  source_video_path, content_sha256, media_type, upload_status,
  analysis_status, subtitle_mode, archived, referenced_by_count,
  created_at, updated_at
) VALUES (
  'pg-asset-0024', 9301, 'pg 0024', 'source.mp4', 'inbox/source.mp4',
  :source_version, 'video', 'ready', 'not_analyzed', 'unknown',
  FALSE, 0, 1, 1
)
"""
                ),
                {"source_version": "d" * 64},
            )
            connection.execute(
                text(
                    """
INSERT INTO media_library_clip_derivatives (
  clip_id, idempotency_key, source_asset_id, source_session_id,
  source_version, source_start_ms, source_end_ms, output_path,
  display_name, duration_ms, content_sha256, size_bytes,
  operation, search_eligible, created_at
) VALUES (
  'pg-clip-before-0024', 'pg-clip-before-0024-key', 'pg-asset-0024', 9301,
  :source_version, 1000, 5000, 'cuts/clip.mp4', ' ＰＧ  玻璃碗 ',
  4000, :clip_hash, 100, 'precise_reencode_v1', FALSE, 2
)
"""
                ),
                {"source_version": "d" * 64, "clip_hash": "e" * 64},
            )

        run_migrations(engine)

        inspector = inspect(engine)
        columns = {
            str(column["name"]): column
            for column in inspector.get_columns(
                "media_library_clip_derivatives"
            )
        }
        assert {
            "tags_json",
            "search_text",
            "search_normalization_version",
            "search_enabled_at",
            "search_updated_at",
        }.issubset(columns)
        checks = {
            str(item.get("name") or "")
            for item in inspector.get_check_constraints(
                "media_library_clip_derivatives"
            )
        }
        assert "ck_media_library_clip_not_search_eligible" not in checks
        indexes = {
            str(item.get("name") or ""): list(
                item.get("column_names") or []
            )
            for item in inspector.get_indexes(
                "media_library_clip_derivatives"
            )
        }
        assert indexes[
            "ix_media_library_clip_search_eligible_source"
        ] == ["search_eligible", "source_asset_id", "created_at"]
        with engine.begin() as connection:
            clip = connection.execute(
                text(
                    "SELECT display_name, tags_json, search_text, "
                    "search_normalization_version, search_eligible, "
                    "search_enabled_at, search_updated_at "
                    "FROM media_library_clip_derivatives "
                    "WHERE clip_id='pg-clip-before-0024'"
                )
            ).mappings().one()
            assert dict(clip) == {
                "display_name": " ＰＧ  玻璃碗 ",
                "tags_json": [],
                "search_text": "pg 玻璃碗",
                "search_normalization_version": "nfkc_casefold_ws_v1",
                "search_eligible": False,
                "search_enabled_at": None,
                "search_updated_at": None,
            }
            connection.execute(
                text(
                    "UPDATE media_library_clip_derivatives "
                    "SET search_eligible=TRUE "
                    "WHERE clip_id='pg-clip-before-0024'"
                )
            )
        assert migration_ids(engine) == [
            migration[0] for migration in MIGRATIONS
        ]
