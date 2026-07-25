from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db import migrations  # noqa: E402
from opcrew_backend.db.migrations import MIGRATIONS, run_migrations  # noqa: E402
from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_clip_derivatives,
    media_library_fragment_index,
    metadata,
    sessions,
)


MIGRATION_ID = "0023_media_library_visual_search"


def sqlite_engine():
    engine = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def mark_through_0022(conn) -> None:
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


def make_0022_database(*, with_rows: bool = True):
    engine = sqlite_engine()
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE media_library_fragment_index"))
        migrations.migration_0020_media_library_fragment_search(conn)
        mark_through_0022(conn)
        if not with_rows:
            return engine
        conn.execute(
            sessions.insert().values(
                id=31,
                source="test",
                group_id="test",
                title="0023 upgrade",
                status="draft",
                workspace_dir="/tmp/0023-upgrade",
                created_at=1,
                updated_at=1,
            )
        )
        conn.execute(
            media_library_assets.insert().values(
                asset_id="asset-0023",
                session_id=31,
                display_name="0023 upgrade",
                original_filename="source.mp4",
                source_video_path="inbox/source.mp4",
                content_sha256="a" * 64,
                content_hashed_at=1,
                media_type="video",
                duration_ms=3000,
                upload_status="ready",
                analysis_status="not_analyzed",
                subtitle_mode="unknown",
                analysis_summary_json={},
                tags_json=[],
                archived=False,
                referenced_by_count=0,
                created_at=1,
                updated_at=1,
            )
        )
        conn.execute(
            media_library_analysis_runs.insert().values(
                analysis_run_id="run-dialogue-0023",
                asset_id="asset-0023",
                scheme="dialogue",
                source_version="a" * 64,
                status="ready",
                schema_version="media_library_dialogue_fragments_v1",
                result_hash="b" * 64,
                result_index_path="SessionOutput/dialogue.json",
                is_current=True,
                created_at=1,
                updated_at=1,
            )
        )
        conn.execute(
            media_library_analysis_runs.insert().values(
                analysis_run_id="run-visual-0023",
                asset_id="asset-0023",
                scheme="visual_semantic",
                source_version="a" * 64,
                status="ready",
                schema_version="media_library_visual_semantic_v2",
                result_hash="c" * 64,
                result_index_path="SessionOutput/visual.json",
                is_current=True,
                created_at=2,
                updated_at=2,
            )
        )
        conn.execute(
            media_library_fragment_index.insert().values(
                id=41,
                asset_id="asset-0023",
                source_session_id=31,
                source_version="a" * 64,
                analysis_scheme="dialogue",
                analysis_run_id="run-dialogue-0023",
                result_hash="b" * 64,
                fragment_id="dialogue_0001",
                start_ms=0,
                end_ms=3000,
                dialogue_text="保留的对白",
                title="保留标题",
                summary="保留摘要",
                keywords_json=["保留"],
                visual_labels_json=[],
                keyframe_ref_json=None,
                search_text="保留的对白",
                search_lexemes_text="保留 对白",
                tokenizer_name="jieba",
                tokenizer_version="v1",
                dictionary_hash="d" * 64,
                normalization_version="nfkc_casefold_ws_v1",
                quality_status="ready",
                confidence=0.8,
                is_active=True,
                created_at=1,
                updated_at=1,
            )
        )
        conn.execute(
            media_library_clip_derivatives.insert().values(
                clip_id="clip-before-0023",
                idempotency_key="clip-before-0023-key",
                source_asset_id="asset-0023",
                source_session_id=31,
                source_version="a" * 64,
                source_start_ms=0,
                source_end_ms=1000,
                output_path="cuts/clip.mp4",
                display_name="existing clip",
                duration_ms=1000,
                content_sha256="e" * 64,
                size_bytes=100,
                operation="precise_reencode_v1",
                search_eligible=False,
                created_at=1,
            )
        )
        conn.execute(
            text(
                """
CREATE TABLE fragment_0023_audit (
  fragment_id TEXT NOT NULL,
  summary TEXT
)
"""
            )
        )
        conn.execute(
            text(
                """
CREATE TRIGGER fragment_0023_update_audit
AFTER UPDATE OF summary ON media_library_fragment_index
BEGIN
  INSERT INTO fragment_0023_audit(fragment_id, summary)
  VALUES (NEW.fragment_id, NEW.summary);
END
"""
            )
        )
    return engine


def table_contract(engine) -> dict[str, object]:
    inspector = inspect(engine)
    columns = {
        str(column["name"]): {
            "type": str(column["type"]),
            "nullable": bool(column["nullable"]),
            "default": str(column.get("default")),
            "primary_key": int(column.get("primary_key") or 0),
        }
        for column in inspector.get_columns("media_library_fragment_index")
    }
    indexes = {
        str(index["name"]): {
            "columns": list(index.get("column_names") or []),
            "unique": bool(index.get("unique")),
        }
        for index in inspector.get_indexes("media_library_fragment_index")
    }
    unique_constraints = {
        (
            str(constraint.get("name") or ""),
            tuple(constraint.get("column_names") or []),
        )
        for constraint in inspector.get_unique_constraints(
            "media_library_fragment_index"
        )
    }
    foreign_keys = {
        (
            tuple(foreign_key.get("constrained_columns") or []),
            str(foreign_key.get("referred_table") or ""),
            tuple(foreign_key.get("referred_columns") or []),
            str((foreign_key.get("options") or {}).get("ondelete") or ""),
        )
        for foreign_key in inspector.get_foreign_keys(
            "media_library_fragment_index"
        )
    }
    checks = {
        str(check.get("name") or ""): " ".join(
            str(check.get("sqltext") or "").split()
        )
        for check in inspector.get_check_constraints(
            "media_library_fragment_index"
        )
    }
    with engine.connect() as conn:
        triggers = {
            str(row.name): " ".join(str(row.sql).split())
            for row in conn.execute(
                text(
                    """
SELECT name, sql FROM sqlite_master
WHERE type = 'trigger'
  AND tbl_name = 'media_library_fragment_index'
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


class MediaLibraryMigration0023ContractTest(unittest.TestCase):
    def test_empty_database_records_0023_and_uses_current_scheme_check(self) -> None:
        engine = sqlite_engine()
        try:
            metadata.create_all(engine)
            run_migrations(engine)
            with engine.connect() as conn:
                migration_ids = {
                    str(row[0])
                    for row in conn.execute(
                        text("SELECT id FROM schema_migrations")
                    )
                }
                create_sql = str(
                    conn.execute(
                        text(
                            "SELECT sql FROM sqlite_master WHERE "
                            "type='table' AND name='media_library_fragment_index'"
                        )
                    ).scalar_one()
                )
                self.assertEqual(
                    conn.execute(text("PRAGMA foreign_key_check")).all(), []
                )
                self.assertEqual(
                    conn.execute(text("PRAGMA integrity_check")).scalar_one(),
                    "ok",
                )
            self.assertIn(MIGRATION_ID, migration_ids)
            self.assertIn("'visual_semantic'", create_sql)
        finally:
            engine.dispose()

    def test_0022_upgrade_preserves_rows_constraints_indexes_fks_and_triggers(
        self,
    ) -> None:
        engine = make_0022_database()
        try:
            before_clip = None
            with engine.connect() as conn:
                before_fragment = dict(
                    conn.execute(
                        text(
                            "SELECT * FROM media_library_fragment_index "
                            "WHERE id = 41"
                        )
                    ).mappings().one()
                )
                before_clip = dict(
                    conn.execute(
                        select(media_library_clip_derivatives).where(
                            media_library_clip_derivatives.c.clip_id
                            == "clip-before-0023"
                        )
                    ).mappings().one()
                )
            run_migrations(engine)
            contract = table_contract(engine)
            self.assertEqual(
                set(contract["indexes"]),
                {
                    "ix_media_library_fragment_active_scheme_asset",
                    "ix_media_library_fragment_asset_scheme_active",
                    "ix_media_library_fragment_analysis_run",
                },
            )
            self.assertEqual(len(contract["foreign_keys"]), 3)
            self.assertIn(
                (
                    "uq_media_library_fragment_run_fragment",
                    ("analysis_run_id", "fragment_id"),
                ),
                contract["unique_constraints"],
            )
            self.assertIn(
                "'visual_semantic'",
                contract["checks"]["ck_media_library_fragment_scheme"],
            )
            self.assertIn(
                "fragment_0023_update_audit", contract["triggers"]
            )
            with engine.begin() as conn:
                after_fragment = dict(
                    conn.execute(
                        text(
                            "SELECT * FROM media_library_fragment_index "
                            "WHERE id = 41"
                        )
                    ).mappings().one()
                )
                after_clip = dict(
                    conn.execute(
                        select(media_library_clip_derivatives).where(
                            media_library_clip_derivatives.c.clip_id
                            == "clip-before-0023"
                        )
                    ).mappings().one()
                )
                conn.execute(
                    media_library_fragment_index.insert().values(
                        asset_id="asset-0023",
                        source_session_id=31,
                        source_version="a" * 64,
                        analysis_scheme="visual_semantic",
                        analysis_run_id="run-visual-0023",
                        result_hash="c" * 64,
                        fragment_id="scene_0001",
                        start_ms=0,
                        end_ms=3000,
                        dialogue_text=None,
                        title=None,
                        summary="玻璃碗和深色液体",
                        keywords_json=["玻璃碗", "深色液体"],
                        visual_labels_json=["玻璃碗", "深色液体"],
                        keyframe_ref_json={
                            "keyframe_ids": [
                                f"scene_0001-sample-{index:02d}"
                                for index in range(1, 5)
                            ]
                        },
                        search_text="玻璃碗 深色液体",
                        search_lexemes_text="玻璃 碗 深色 液体",
                        tokenizer_name="jieba",
                        tokenizer_version="v1",
                        normalization_version="nfkc_casefold_ws_v1",
                        quality_status="ready",
                        confidence=0.9,
                        is_active=True,
                        created_at=2,
                        updated_at=2,
                    )
                )
                conn.execute(
                    text(
                        "UPDATE media_library_fragment_index "
                        "SET summary='触发器仍在' WHERE id=41"
                    )
                )
                audit = conn.execute(
                    text("SELECT * FROM fragment_0023_audit")
                ).mappings().one()
                self.assertEqual(dict(audit), {
                    "fragment_id": "dialogue_0001",
                    "summary": "触发器仍在",
                })
                with self.assertRaises(IntegrityError):
                    conn.execute(
                        text(
                            """
INSERT INTO media_library_fragment_index (
  asset_id, source_session_id, source_version, analysis_scheme,
  analysis_run_id, result_hash, fragment_id, start_ms, end_ms,
  keywords_json, visual_labels_json, search_text, created_at, updated_at
) VALUES (
  'asset-0023', 31, :source_version, 'unknown',
  'run-visual-0023', :result_hash, 'bad', 0, 1,
  '[]', '[]', 'bad', 3, 3
)
"""
                        ),
                        {
                            "source_version": "a" * 64,
                            "result_hash": "c" * 64,
                        },
                    )
            self.assertEqual(after_fragment, before_fragment)
            for field, value in before_clip.items():
                if field in {
                    "search_text",
                    "search_normalization_version",
                    "search_enabled_at",
                    "search_updated_at",
                }:
                    continue
                self.assertEqual(after_clip[field], value)
            self.assertEqual(after_clip["search_text"], "existing clip")
            self.assertFalse(after_clip["search_eligible"])
            with engine.connect() as conn:
                self.assertEqual(
                    conn.execute(text("PRAGMA foreign_key_check")).all(), []
                )
                self.assertEqual(
                    conn.execute(text("PRAGMA integrity_check")).scalar_one(),
                    "ok",
                )
        finally:
            engine.dispose()

    def test_current_and_0022_upgraded_sqlite_schemas_are_equivalent(self) -> None:
        current = sqlite_engine()
        upgraded = make_0022_database(with_rows=False)
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
        engine = make_0022_database()
        try:
            with engine.connect() as conn:
                before_sql = str(
                    conn.execute(
                        text(
                            "SELECT sql FROM sqlite_master WHERE "
                            "type='table' AND name='media_library_fragment_index'"
                        )
                    ).scalar_one()
                )
                before_rows = conn.execute(
                    text(
                        "SELECT id, fragment_id, summary FROM "
                        "media_library_fragment_index ORDER BY id"
                    )
                ).all()
            with (
                patch.object(
                    migrations,
                    "_create_fragment_indexes",
                    side_effect=RuntimeError("injected_0023_failure"),
                ),
                self.assertRaisesRegex(
                    RuntimeError, "injected_0023_failure"
                ),
            ):
                with engine.begin() as conn:
                    migrations.migration_0023_media_library_visual_search(
                        conn
                    )
            with engine.connect() as conn:
                after_sql = str(
                    conn.execute(
                        text(
                            "SELECT sql FROM sqlite_master WHERE "
                            "type='table' AND name='media_library_fragment_index'"
                        )
                    ).scalar_one()
                )
                after_rows = conn.execute(
                    text(
                        "SELECT id, fragment_id, summary FROM "
                        "media_library_fragment_index ORDER BY id"
                    )
                ).all()
                temporary_count = int(
                    conn.execute(
                        text(
                            "SELECT count(*) FROM sqlite_master WHERE "
                            "type='table' AND "
                            "name='media_library_fragment_index__0023'"
                        )
                    ).scalar_one()
                )
            self.assertEqual(after_sql, before_sql)
            self.assertEqual(after_rows, before_rows)
            self.assertEqual(temporary_count, 0)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
