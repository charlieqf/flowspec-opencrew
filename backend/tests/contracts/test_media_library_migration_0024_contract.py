from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event, inspect, text


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db import migrations  # noqa: E402
from opcrew_backend.db.migrations import MIGRATIONS, run_migrations  # noqa: E402
from opcrew_backend.db.schema import metadata  # noqa: E402


MIGRATION_ID = "0024_media_library_clip_search"


def sqlite_engine():
    engine = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def mark_through_0023(conn) -> None:
    migrations.schema_migrations.create(conn, checkfirst=True)
    for migration_id, description, _upgrade in MIGRATIONS:
        if migration_id == MIGRATION_ID:
            break
        conn.execute(
            migrations.schema_migrations.insert().values(
                id=migration_id,
                description=description,
                applied_at=1,
            )
        )


def make_0023_database(*, with_rows: bool = True):
    engine = sqlite_engine()
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE media_library_storyboard_imports"))
        conn.execute(text("DROP TABLE media_library_clip_derivatives"))
        migrations.migration_0021_media_library_clip_derivatives(conn)
        migrations.migration_0022_media_library_storyboard_imports(conn)
        mark_through_0023(conn)
        if not with_rows:
            return engine
        conn.execute(
            text(
                """
INSERT INTO sessions (
  id, source, group_id, title, status, workspace_dir, created_at, updated_at
) VALUES
  (41, 'test', 'test', '0024 source', 'draft', '/tmp/0024-source', 1, 1),
  (42, 'test', 'test', '0024 target', 'draft', '/tmp/0024-target', 1, 1)
"""
            )
        )
        conn.execute(
            text(
                """
INSERT INTO openclip_tasks (id, session_id, status, created_at, updated_at)
VALUES (43, 42, 'draft', 1, 1)
"""
            )
        )
        conn.execute(
            text(
                """
INSERT INTO media_library_assets (
  asset_id, session_id, display_name, original_filename, source_video_path,
  content_sha256, media_type, upload_status, analysis_status, subtitle_mode,
  archived, referenced_by_count, created_at, updated_at
) VALUES (
  'asset-0024', 41, '0024 source', 'source.mp4', 'inbox/source.mp4',
  :source_version, 'video', 'ready', 'not_analyzed', 'unknown', FALSE, 0, 1, 1
)
"""
            ),
            {"source_version": "a" * 64},
        )
        conn.execute(
            text(
                """
INSERT INTO media_library_clip_derivatives (
  clip_id, idempotency_key, source_asset_id, source_session_id,
  source_version, source_start_ms, source_end_ms, source_scheme,
  source_fragment_id, source_analysis_run_id, source_search_id,
  source_dialogue_asset_key, output_path, display_name, duration_ms,
  content_sha256, size_bytes, operation, search_eligible, created_at
) VALUES (
  'clip-before-0024', 'clip-before-0024-key', 'asset-0024', 41,
  :source_version, 1000, 5000, 'visual_semantic',
  'scene_0001', 'run-0024', 'search-0024',
  'dialogue_0024', 'cuts/clip.mp4', '  ＡＢＣ   玻璃碗  ', 4000,
  :clip_hash, 1234, 'precise_reencode_v1', FALSE, 2
)
"""
            ),
            {"source_version": "a" * 64, "clip_hash": "b" * 64},
        )
        conn.execute(
            text(
                """
INSERT INTO media_library_storyboard_imports (
  import_id, idempotency_key, source_kind, source_asset_id, source_clip_id,
  source_version, target_task_id, target_session_id, target_path,
  target_manifest_asset_id, content_sha256, size_bytes, status,
  created_at, updated_at
) VALUES (
  'import-before-0024', 'import-before-0024-key', 'media_library_clip',
  'asset-0024', 'clip-before-0024', :source_version, 43, 42,
  'storyboard/assets/videos/clip.mp4', 'asset_imported_0024',
  :clip_hash, 1234, 'completed', 3, 3
)
"""
            ),
            {"source_version": "a" * 64, "clip_hash": "b" * 64},
        )
        conn.execute(
            text(
                """
CREATE TABLE clip_0024_audit (clip_id TEXT NOT NULL, display_name TEXT)
"""
            )
        )
        conn.execute(
            text(
                """
CREATE TRIGGER clip_0024_update_audit
AFTER UPDATE OF display_name ON media_library_clip_derivatives
BEGIN
  INSERT INTO clip_0024_audit(clip_id, display_name)
  VALUES (NEW.clip_id, NEW.display_name);
END
"""
            )
        )
    return engine


def row_hash(row: dict[str, object], fields: tuple[str, ...]) -> str:
    payload = {field: row[field] for field in fields}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def table_contract(engine) -> dict[str, object]:
    inspector = inspect(engine)
    table_name = "media_library_clip_derivatives"
    columns = {
        str(column["name"]): {
            "type": str(column["type"]),
            "nullable": bool(column["nullable"]),
            "default": str(column.get("default")),
            "primary_key": int(column.get("primary_key") or 0),
        }
        for column in inspector.get_columns(table_name)
    }
    indexes = {
        str(index["name"]): {
            "columns": list(index.get("column_names") or []),
            "unique": bool(index.get("unique")),
        }
        for index in inspector.get_indexes(table_name)
    }
    unique_constraints = {
        (
            str(constraint.get("name") or ""),
            tuple(constraint.get("column_names") or []),
        )
        for constraint in inspector.get_unique_constraints(table_name)
    }
    foreign_keys = {
        (
            tuple(foreign_key.get("constrained_columns") or []),
            str(foreign_key.get("referred_table") or ""),
            tuple(foreign_key.get("referred_columns") or []),
            str((foreign_key.get("options") or {}).get("ondelete") or ""),
        )
        for foreign_key in inspector.get_foreign_keys(table_name)
    }
    checks = {
        str(check.get("name") or ""): " ".join(
            str(check.get("sqltext") or "").split()
        )
        for check in inspector.get_check_constraints(table_name)
    }
    with engine.connect() as conn:
        triggers = {
            str(row.name): " ".join(str(row.sql).split())
            for row in conn.execute(
                text(
                    """
SELECT name, sql FROM sqlite_master
WHERE type = 'trigger' AND tbl_name = 'media_library_clip_derivatives'
ORDER BY name
"""
                )
            ).mappings()
        }
    return {
        "columns": columns,
        "indexes": indexes,
        "unique_constraints": unique_constraints,
        "foreign_keys": foreign_keys,
        "checks": checks,
        "triggers": triggers,
    }


class MediaLibraryMigration0024ContractTest(unittest.TestCase):
    def test_empty_database_records_0024_with_closed_default(self) -> None:
        engine = sqlite_engine()
        try:
            metadata.create_all(engine)
            run_migrations(engine)
            contract = table_contract(engine)
            self.assertIn(
                "ix_media_library_clip_search_eligible_source",
                contract["indexes"],
            )
            self.assertNotIn(
                "ck_media_library_clip_not_search_eligible",
                contract["checks"],
            )
            with engine.connect() as conn:
                migration_ids = {
                    str(row[0])
                    for row in conn.execute(
                        text("SELECT id FROM schema_migrations")
                    )
                }
                self.assertEqual(
                    conn.execute(text("PRAGMA foreign_key_check")).all(), []
                )
                self.assertEqual(
                    conn.execute(text("PRAGMA integrity_check")).scalar_one(),
                    "ok",
                )
            self.assertIn(MIGRATION_ID, migration_ids)
        finally:
            engine.dispose()

    def test_0023_upgrade_preserves_rows_references_constraints_and_triggers(
        self,
    ) -> None:
        engine = make_0023_database()
        preserved_fields = (
            "clip_id",
            "idempotency_key",
            "source_asset_id",
            "source_session_id",
            "source_version",
            "source_start_ms",
            "source_end_ms",
            "output_path",
            "display_name",
            "duration_ms",
            "content_sha256",
            "size_bytes",
            "created_at",
        )
        try:
            with engine.connect() as conn:
                before = dict(
                    conn.execute(
                        text(
                            "SELECT * FROM media_library_clip_derivatives "
                            "WHERE clip_id='clip-before-0024'"
                        )
                    ).mappings().one()
                )
                before_hash = row_hash(before, preserved_fields)
            run_migrations(engine)
            contract = table_contract(engine)
            self.assertEqual(
                contract["indexes"][
                    "ix_media_library_clip_search_eligible_source"
                ]["columns"],
                ["search_eligible", "source_asset_id", "created_at"],
            )
            self.assertNotIn(
                "ck_media_library_clip_not_search_eligible",
                contract["checks"],
            )
            self.assertEqual(len(contract["foreign_keys"]), 2)
            self.assertIn("clip_0024_update_audit", contract["triggers"])
            with engine.begin() as conn:
                after = dict(
                    conn.execute(
                        text(
                            "SELECT * FROM media_library_clip_derivatives "
                            "WHERE clip_id='clip-before-0024'"
                        )
                    ).mappings().one()
                )
                self.assertEqual(row_hash(after, preserved_fields), before_hash)
                self.assertEqual(after["tags_json"], "[]")
                self.assertEqual(after["search_text"], "abc 玻璃碗")
                self.assertEqual(
                    after["search_normalization_version"],
                    "nfkc_casefold_ws_v1",
                )
                self.assertFalse(after["search_eligible"])
                self.assertIsNone(after["search_enabled_at"])
                self.assertIsNone(after["search_updated_at"])
                imported_clip = conn.execute(
                    text(
                        "SELECT source_clip_id FROM media_library_storyboard_imports "
                        "WHERE import_id='import-before-0024'"
                    )
                ).scalar_one()
                self.assertEqual(imported_clip, "clip-before-0024")
                conn.execute(
                    text(
                        "UPDATE media_library_clip_derivatives "
                        "SET search_eligible=TRUE, display_name='trigger survives' "
                        "WHERE clip_id='clip-before-0024'"
                    )
                )
                audit = conn.execute(
                    text("SELECT * FROM clip_0024_audit")
                ).mappings().one()
                self.assertEqual(dict(audit), {
                    "clip_id": "clip-before-0024",
                    "display_name": "trigger survives",
                })
                self.assertEqual(
                    conn.execute(text("PRAGMA foreign_key_check")).all(), []
                )
                self.assertEqual(
                    conn.execute(text("PRAGMA integrity_check")).scalar_one(),
                    "ok",
                )
        finally:
            engine.dispose()

    def test_current_and_0023_upgraded_sqlite_schemas_are_equivalent(self) -> None:
        current = sqlite_engine()
        upgraded = make_0023_database(with_rows=False)
        try:
            metadata.create_all(current)
            run_migrations(current)
            run_migrations(upgraded)
            current_contract = table_contract(current)
            upgraded_contract = table_contract(upgraded)
            current_contract["triggers"] = {}
            upgraded_contract["triggers"] = {}
            self.assertEqual(current_contract, upgraded_contract)
        finally:
            current.dispose()
            upgraded.dispose()

    def test_failed_sqlite_rebuild_rolls_back_every_change(self) -> None:
        engine = make_0023_database()
        try:
            with engine.connect() as conn:
                before_sql = str(
                    conn.execute(
                        text(
                            "SELECT sql FROM sqlite_master WHERE type='table' "
                            "AND name='media_library_clip_derivatives'"
                        )
                    ).scalar_one()
                )
                before_rows = conn.execute(
                    text(
                        "SELECT clip_id, display_name FROM "
                        "media_library_clip_derivatives ORDER BY clip_id"
                    )
                ).all()
            with (
                patch.object(
                    migrations,
                    "_create_clip_search_indexes",
                    side_effect=RuntimeError("injected_0024_failure"),
                ),
                self.assertRaisesRegex(RuntimeError, "injected_0024_failure"),
            ):
                with engine.begin() as conn:
                    migrations.migration_0024_media_library_clip_search(conn)
            with engine.connect() as conn:
                after_sql = str(
                    conn.execute(
                        text(
                            "SELECT sql FROM sqlite_master WHERE type='table' "
                            "AND name='media_library_clip_derivatives'"
                        )
                    ).scalar_one()
                )
                after_rows = conn.execute(
                    text(
                        "SELECT clip_id, display_name FROM "
                        "media_library_clip_derivatives ORDER BY clip_id"
                    )
                ).all()
                temporary_count = int(
                    conn.execute(
                        text(
                            "SELECT count(*) FROM sqlite_master WHERE "
                            "type='table' AND "
                            "name='media_library_clip_derivatives__0024'"
                        )
                    ).scalar_one()
                )
                self.assertEqual(
                    conn.execute(text("PRAGMA foreign_key_check")).all(), []
                )
            self.assertEqual(after_sql, before_sql)
            self.assertEqual(after_rows, before_rows)
            self.assertEqual(temporary_count, 0)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
