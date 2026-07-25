from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.services.workflow_deletion import WorkflowDeletionService  # noqa: E402


class WorkflowDeletionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"
        self.root.mkdir()
        self.service = WorkflowDeletionService(cast(Any, object()), self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_rejects_empty_workspace_dir_without_touching_root(self) -> None:
        marker = self.root / "marker.txt"
        marker.write_text("keep", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.service.cleanup_workspace({"workspace_dir": ""})

        self.assertTrue(marker.exists())

    def test_rejects_relative_workspace_dir(self) -> None:
        with self.assertRaises(ValueError):
            self.service.cleanup_workspace({"workspace_dir": "sessions/1/workspace"})

    def test_rejects_workspace_dir_outside_sessions_root(self) -> None:
        outside = Path(self.tmp.name) / "outside" / "workspace"
        outside.mkdir(parents=True)
        (outside / "keep.txt").write_text("keep", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.service.cleanup_workspace({"workspace_dir": str(outside)})

        self.assertTrue(outside.exists())

    def test_rejects_workspace_dir_for_a_different_session(self) -> None:
        session_dir = self.root / "456"
        workspace = session_dir / "workspace"
        workspace.mkdir(parents=True)

        with self.assertRaises(ValueError):
            self.service.cleanup_workspace({"id": 123, "workspace_dir": str(workspace)})

        self.assertTrue(session_dir.exists())

    def test_deletes_only_session_directory_under_sessions_root(self) -> None:
        session_dir = self.root / "123"
        workspace = session_dir / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "result.txt").write_text("delete", encoding="utf-8")
        sibling = self.root / "456"
        sibling.mkdir()

        self.service.cleanup_workspace({"id": 123, "workspace_dir": str(workspace)})

        self.assertFalse(session_dir.exists())
        self.assertTrue(sibling.exists())

    def create_deletion_engine(self):
        engine = create_engine("sqlite:///:memory:", future=True)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE sessions (id INTEGER PRIMARY KEY, title TEXT, workspace_dir TEXT)"))
            conn.execute(text("CREATE TABLE session_shares (id INTEGER PRIMARY KEY, session_id INTEGER)"))
            conn.execute(text("CREATE TABLE session_files (id INTEGER PRIMARY KEY, session_id INTEGER)"))
            conn.execute(text("CREATE TABLE session_events (id INTEGER PRIMARY KEY, session_id INTEGER)"))
            conn.execute(text("CREATE TABLE workflow_plans (id INTEGER PRIMARY KEY, session_id INTEGER, task_id INTEGER, workflow_id TEXT)"))
            conn.execute(text("CREATE TABLE openflow_analysis_runs (id INTEGER PRIMARY KEY, session_id INTEGER)"))
            conn.execute(text("CREATE TABLE openclip_tasks (id INTEGER PRIMARY KEY, session_id INTEGER)"))
            conn.execute(text("CREATE TABLE talking_head_task_configs (task_id INTEGER PRIMARY KEY, config_json TEXT)"))
            conn.execute(text("CREATE TABLE openclip_param_versions (id INTEGER PRIMARY KEY, task_id INTEGER)"))
            conn.execute(text("CREATE TABLE openclip_attempts (id INTEGER PRIMARY KEY, task_id INTEGER)"))
            conn.execute(text("CREATE TABLE openclip_skill_versions (id INTEGER PRIMARY KEY, task_id INTEGER)"))
            conn.execute(text("CREATE TABLE openclip_prompt_versions (id INTEGER PRIMARY KEY, task_id INTEGER)"))
            conn.execute(text("CREATE TABLE oc_rebuild_tasks (id INTEGER PRIMARY KEY, session_id INTEGER, workflow_mode TEXT)"))
            conn.execute(text("CREATE TABLE oc_rebuild_attempts (id INTEGER PRIMARY KEY, task_id INTEGER)"))
            conn.execute(text("CREATE TABLE oc_rebuild_prompt_versions (id INTEGER PRIMARY KEY, task_id INTEGER)"))
        return engine

    def count(self, engine: Any, table_name: str, where_sql: str = "1=1", params: dict[str, Any] | None = None) -> int:
        with engine.connect() as conn:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM {table_name} WHERE {where_sql}"), params or {}).scalar_one())

    def test_delete_openclip_session_cascades_owned_rows(self) -> None:
        engine = self.create_deletion_engine()
        try:
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO sessions (id, title, workspace_dir) VALUES (1, 'delete', '/tmp/1'), (2, 'keep', '/tmp/2')"))
                conn.execute(text("INSERT INTO openclip_tasks (id, session_id) VALUES (10, 1), (20, 2)"))
                conn.execute(text("INSERT INTO talking_head_task_configs (task_id, config_json) VALUES (10, '{}'), (20, '{}')"))
                for table_name in ("openclip_param_versions", "openclip_attempts", "openclip_skill_versions", "openclip_prompt_versions"):
                    conn.execute(text(f"INSERT INTO {table_name} (id, task_id) VALUES (1, 10), (2, 20)"))
                conn.execute(text("INSERT INTO workflow_plans (id, session_id, task_id, workflow_id) VALUES (1, 1, 10, 'openclip_analysis'), (2, 2, 20, 'openclip_analysis')"))
                conn.execute(text("INSERT INTO session_shares (id, session_id) VALUES (1, 1), (2, 2)"))
                conn.execute(text("INSERT INTO session_files (id, session_id) VALUES (1, 1), (2, 2)"))
                conn.execute(text("INSERT INTO session_events (id, session_id) VALUES (1, 1), (2, 2)"))
                conn.execute(text("INSERT INTO openflow_analysis_runs (id, session_id) VALUES (1, 1), (2, 2)"))

            WorkflowDeletionService(engine, self.root).delete_session_db_first({"id": 1})

            for table_name in ("sessions", "openclip_tasks", "session_shares", "session_files", "session_events", "workflow_plans", "openflow_analysis_runs"):
                self.assertEqual(self.count(engine, table_name, "id = 1" if table_name != "openclip_tasks" else "session_id = 1"), 0)
            for table_name in ("openclip_param_versions", "openclip_attempts", "openclip_skill_versions", "openclip_prompt_versions"):
                self.assertEqual(self.count(engine, table_name, "task_id = 10"), 0)
                self.assertEqual(self.count(engine, table_name, "task_id = 20"), 1)
            self.assertEqual(self.count(engine, "talking_head_task_configs", "task_id = 10"), 0)
            self.assertEqual(self.count(engine, "talking_head_task_configs", "task_id = 20"), 1)
            self.assertEqual(self.count(engine, "sessions", "id = 2"), 1)
            self.assertEqual(self.count(engine, "openclip_tasks", "session_id = 2"), 1)
        finally:
            engine.dispose()

    def test_delete_rebuild_and_storyboard_sessions_cascade_owned_rows(self) -> None:
        engine = self.create_deletion_engine()
        try:
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO sessions (id, title, workspace_dir) VALUES (3, 'storyboard', '/tmp/3'), (4, 'legacy_storyboard', '/tmp/4'), (5, 'keep', '/tmp/5')"))
                conn.execute(text("INSERT INTO oc_rebuild_tasks (id, session_id, workflow_mode) VALUES (30, 3, 'storyboard'), (40, 4, NULL), (50, 5, 'rebuild')"))
                conn.execute(text("INSERT INTO oc_rebuild_attempts (id, task_id) VALUES (1, 30), (2, 40), (3, 50)"))
                conn.execute(text("INSERT INTO oc_rebuild_prompt_versions (id, task_id) VALUES (1, 30), (2, 40), (3, 50)"))
                conn.execute(text("INSERT INTO workflow_plans (id, session_id, task_id, workflow_id) VALUES (1, 3, 30, 'oc_rebuild.storyboard'), (2, 4, 40, 'oc_rebuild.legacy_storyboard'), (3, 5, 50, 'oc_rebuild.rebuild')"))
                conn.execute(text("INSERT INTO session_shares (id, session_id) VALUES (3, 3), (4, 4), (5, 5)"))
                conn.execute(text("INSERT INTO session_files (id, session_id) VALUES (3, 3), (4, 4), (5, 5)"))
                conn.execute(text("INSERT INTO session_events (id, session_id) VALUES (3, 3), (4, 4), (5, 5)"))

            service = WorkflowDeletionService(engine, self.root)
            service.delete_session_db_first({"id": 3})
            service.delete_session_db_first({"id": 4})

            self.assertEqual(self.count(engine, "oc_rebuild_tasks", "session_id IN (3, 4)"), 0)
            self.assertEqual(self.count(engine, "oc_rebuild_attempts", "task_id IN (30, 40)"), 0)
            self.assertEqual(self.count(engine, "oc_rebuild_prompt_versions", "task_id IN (30, 40)"), 0)
            self.assertEqual(self.count(engine, "workflow_plans", "session_id IN (3, 4)"), 0)
            self.assertEqual(self.count(engine, "session_files", "session_id IN (3, 4)"), 0)
            self.assertEqual(self.count(engine, "session_events", "session_id IN (3, 4)"), 0)
            self.assertEqual(self.count(engine, "session_shares", "session_id IN (3, 4)"), 0)
            self.assertEqual(self.count(engine, "oc_rebuild_tasks", "session_id = 5"), 1)
            self.assertEqual(self.count(engine, "oc_rebuild_attempts", "task_id = 50"), 1)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
