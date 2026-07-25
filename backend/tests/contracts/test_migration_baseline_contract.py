from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.db.migrations import MIGRATIONS, run_migrations  # noqa: E402
from opcrew_backend.db.schema import metadata  # noqa: E402


class MigrationBaselineContractTest(unittest.TestCase):
    def create_engine(self):
        return create_engine("sqlite:///:memory:", future=True)

    def columns(self, engine, table_name: str) -> set[str]:
        return {str(column["name"]) for column in inspect(engine).get_columns(table_name)}

    def migration_ids(self, engine) -> list[str]:
        with engine.connect() as conn:
            return [str(row[0]) for row in conn.execute(text("SELECT id FROM schema_migrations ORDER BY id")).fetchall()]

    def indexes(self, engine, table_name: str) -> dict[str, dict]:
        return {str(index["name"]): dict(index) for index in inspect(engine).get_indexes(table_name)}

    def unique_constraints(self, engine, table_name: str) -> dict[str, dict]:
        return {
            str(constraint["name"]): dict(constraint)
            for constraint in inspect(engine).get_unique_constraints(table_name)
        }

    def assert_video_interaction_schema(self, engine) -> None:
        self.assertTrue(
            {
                "thread_id", "task_id", "session_id", "actor_id", "head_turn_id",
                "lease_token", "lease_expires_at", "row_version",
            }.issubset(self.columns(engine, "video_interaction_threads"))
        )
        self.assertTrue(
            {
                "turn_id", "thread_id", "task_id", "actor_id", "parent_turn_id",
                "client_action_id", "request_config_json", "usage_request_id", "local_usage_id", "interaction_id",
                "provider_request_status", "provider_state_status", "provider_state_expires_at",
                "provider_expiry_source", "delete_status", "expected_row_version",
            }.issubset(self.columns(engine, "video_interaction_turns"))
        )
        thread_indexes = self.indexes(engine, "video_interaction_threads")
        turn_indexes = self.indexes(engine, "video_interaction_turns")
        self.assertIn("ix_video_interaction_threads_lease", thread_indexes)
        self.assertIn("ix_video_interaction_turns_pending", turn_indexes)
        constraints = self.unique_constraints(engine, "video_interaction_turns")
        self.assertEqual(
            constraints["uq_video_interaction_turn_action_operation"]["column_names"],
            ["task_id", "actor_id", "operation", "client_action_id"],
        )
        self.assertEqual(
            constraints["uq_video_interaction_turn_action"]["column_names"],
            ["task_id", "actor_id", "client_action_id"],
        )
        self.assertEqual(
            constraints["uq_video_interaction_turn_usage_request"]["column_names"],
            ["usage_request_id"],
        )
        foreign_keys = inspect(engine).get_foreign_keys("video_interaction_turns")
        self.assertIn("video_interaction_threads", {str(item["referred_table"]) for item in foreign_keys})

    def test_empty_current_schema_records_all_migrations(self) -> None:
        engine = self.create_engine()
        try:
            metadata.create_all(engine)

            run_migrations(engine)

            self.assertEqual(self.migration_ids(engine), [migration[0] for migration in MIGRATIONS])
            self.assertIn("visibility", self.columns(engine, "session_events"))
            self.assertIn("sensitivity", self.columns(engine, "session_files"))
            self.assertIn("tool_use_session_id", self.columns(engine, "session_files"))
            self.assertIn("tool_use_session_id", self.columns(engine, "openclip_attempts"))
            self.assertIn("tool_use_session_id", self.columns(engine, "oc_rebuild_attempts"))
            self.assertIn("workflow_mode", self.columns(engine, "oc_rebuild_tasks"))
            self.assertIn("workflow_mode", self.columns(engine, "openclip_tasks"))
            self.assertIn("storyboard_quick_config_json", self.columns(engine, "openclip_tasks"))
            self.assertIn("storyboard_quick_config_json", self.columns(engine, "openclip_prompt_versions"))
            self.assertTrue({"task_id", "attempt_id", "step_id", "idempotency_key"}.issubset(self.columns(engine, "local_usage_log")))
            self.assertTrue(self.indexes(engine, "local_usage_log")["ux_local_usage_log_idempotency_key"]["unique"])
            self.assertIn("ix_local_usage_log_task_attempt_step", self.indexes(engine, "local_usage_log"))
            self.assertTrue(
                {"content_sha256", "content_hashed_at"}.issubset(
                    self.columns(engine, "media_library_assets")
                )
            )
            self.assertTrue(
                {
                    "dialogue_current_run_id",
                    "visual_structure_status",
                    "visual_semantic_status",
                    "composite_current_run_id",
                }.issubset(self.columns(engine, "media_library_tasks"))
            )
            self.assertIn(
                "ux_media_library_analysis_runs_one_active",
                self.indexes(engine, "media_library_analysis_runs"),
            )
            self.assert_video_interaction_schema(engine)
        finally:
            engine.dispose()

    def test_existing_legacy_schema_gets_additive_p0_columns(self) -> None:
        engine = self.create_engine()
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE session_events (id INTEGER PRIMARY KEY, session_id INTEGER, kind TEXT, payload TEXT, created_at BIGINT)"))
                conn.execute(text("CREATE TABLE session_files (id INTEGER PRIMARY KEY, session_id INTEGER, path TEXT, kind TEXT, size INTEGER, origin TEXT, downloadable INTEGER, updated_at BIGINT)"))
                conn.execute(text("CREATE TABLE oc_rebuild_tasks (id INTEGER PRIMARY KEY, session_id INTEGER, status TEXT, created_at BIGINT, updated_at BIGINT)"))
                conn.execute(text("CREATE TABLE openclip_attempts (id INTEGER PRIMARY KEY)"))
                conn.execute(text("CREATE TABLE oc_rebuild_attempts (id INTEGER PRIMARY KEY)"))
                conn.execute(text("CREATE TABLE openclip_tasks (id INTEGER PRIMARY KEY)"))
                conn.execute(text("CREATE TABLE openclip_prompt_versions (id INTEGER PRIMARY KEY)"))
                conn.execute(text("CREATE TABLE local_usage_log (id INTEGER PRIMARY KEY, request_id TEXT, provider TEXT NOT NULL, model_id TEXT NOT NULL, modality TEXT NOT NULL, provider_mode TEXT NOT NULL DEFAULT 'local_box', billing_mode TEXT NOT NULL DEFAULT 'local_usage_only', status TEXT NOT NULL, created_at BIGINT NOT NULL)"))

            run_migrations(engine)

            self.assertTrue({"visibility", "event_scope", "severity", "family", "workflow_id", "task_id", "attempt_id", "tool_id", "step_id"}.issubset(self.columns(engine, "session_events")))
            self.assertTrue({"visibility", "sensitivity", "attempt_id", "tool_use_session_id", "stale"}.issubset(self.columns(engine, "session_files")))
            self.assertIn("tool_use_session_id", self.columns(engine, "openclip_attempts"))
            self.assertIn("tool_use_session_id", self.columns(engine, "oc_rebuild_attempts"))
            self.assertIn("result_manifest_json", self.columns(engine, "openclip_attempts"))
            self.assertIn("result_manifest_json", self.columns(engine, "oc_rebuild_attempts"))
            self.assertIn("workflow_mode", self.columns(engine, "oc_rebuild_tasks"))
            self.assertIn("workflow_mode", self.columns(engine, "openclip_tasks"))
            self.assertIn("storyboard_quick_config_json", self.columns(engine, "openclip_tasks"))
            self.assertIn("storyboard_quick_config_json", self.columns(engine, "openclip_prompt_versions"))
            self.assertTrue({"task_id", "attempt_id", "step_id", "idempotency_key"}.issubset(self.columns(engine, "local_usage_log")))
            self.assertTrue(self.indexes(engine, "local_usage_log")["ux_local_usage_log_idempotency_key"]["unique"])
            self.assertIn("ix_local_usage_log_task_attempt_step", self.indexes(engine, "local_usage_log"))
            self.assert_video_interaction_schema(engine)
        finally:
            engine.dispose()

    def test_migrations_are_idempotent_on_rerun(self) -> None:
        engine = self.create_engine()
        try:
            metadata.create_all(engine)

            run_migrations(engine)
            first_ids = self.migration_ids(engine)
            first_event_columns = self.columns(engine, "session_events")
            run_migrations(engine)

            self.assertEqual(self.migration_ids(engine), first_ids)
            self.assertEqual(self.columns(engine, "session_events"), first_event_columns)
            with engine.connect() as conn:
                count = int(conn.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one())
            self.assertEqual(count, len(MIGRATIONS))
        finally:
            engine.dispose()

    def test_existing_0018_database_upgrades_without_reclassifying_visual_semantics(self) -> None:
        engine = self.create_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
CREATE TABLE media_library_assets (
  asset_id TEXT PRIMARY KEY,
  session_id INTEGER,
  display_name TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  upload_status TEXT NOT NULL DEFAULT 'ready',
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
)
"""
                    )
                )
                conn.execute(
                    text(
                        """
CREATE TABLE media_library_tasks (
  id INTEGER PRIMARY KEY,
  asset_id TEXT NOT NULL,
  session_id INTEGER NOT NULL,
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
                conn.execute(
                    text(
                        """
INSERT INTO media_library_assets (
  asset_id, session_id, display_name, original_filename,
  upload_status, created_at, updated_at
) VALUES ('legacy-ready', 1, '旧画面结果', 'legacy.mp4', 'ready', 1, 1)
"""
                    )
                )
                conn.execute(
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
                conn.execute(
                    text(
                        """
CREATE TABLE schema_migrations (
  id TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  applied_at BIGINT NOT NULL
)
"""
                    )
                )
                for migration_id, description, _ in MIGRATIONS:
                    if migration_id == "0019_media_library_source_identity_and_analysis_runs":
                        break
                    conn.execute(
                        text(
                            """
INSERT INTO schema_migrations (id, description, applied_at)
VALUES (:id, :description, 1)
"""
                        ),
                        {"id": migration_id, "description": description},
                    )

            run_migrations(engine)

            with engine.connect() as conn:
                task = conn.execute(
                    text(
                        """
SELECT visual_status, visual_structure_status, visual_semantic_status
FROM media_library_tasks
WHERE asset_id = 'legacy-ready'
"""
                    )
                ).mappings().one()
                run_count = int(
                    conn.execute(
                        text("SELECT count(*) FROM media_library_analysis_runs")
                    ).scalar_one()
                )
            self.assertEqual(task["visual_structure_status"], "ready")
            self.assertEqual(task["visual_semantic_status"], "not_analyzed")
            self.assertEqual(task["visual_status"], "partial")
            self.assertEqual(run_count, 0)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
