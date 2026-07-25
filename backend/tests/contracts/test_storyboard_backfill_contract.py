from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
scripts_path = str(REPO_ROOT / "backend" / "scripts")
if scripts_path not in sys.path:
    sys.path.insert(0, scripts_path)

from backfill_storyboard_workflow_mode import run_backfill  # noqa: E402


class StoryboardBackfillContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storyboard_workspace = self.root / "sessions" / "1" / "workspace"
        self.storyboard_workspace.mkdir(parents=True)
        (self.storyboard_workspace / "storyboard_meta.json").write_text("{}", encoding="utf-8")
        self.rebuild_workspace = self.root / "sessions" / "2" / "workspace"
        self.rebuild_workspace.mkdir(parents=True)
        self.engine = create_engine("sqlite:///:memory:", future=True)
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE sessions (id INTEGER PRIMARY KEY, workspace_dir TEXT)"))
            conn.execute(text("CREATE TABLE oc_rebuild_tasks (id INTEGER PRIMARY KEY, session_id INTEGER, workflow_mode TEXT)"))
            conn.execute(
                text(
                    "INSERT INTO sessions (id, workspace_dir) VALUES "
                    "(:storyboard_session, :storyboard_workspace), "
                    "(:rebuild_session, :rebuild_workspace), "
                    "(:already_session, :already_workspace)"
                ),
                {
                    "storyboard_session": 1,
                    "storyboard_workspace": str(self.storyboard_workspace),
                    "rebuild_session": 2,
                    "rebuild_workspace": str(self.rebuild_workspace),
                    "already_session": 3,
                    "already_workspace": str(self.storyboard_workspace),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO oc_rebuild_tasks (id, session_id, workflow_mode) VALUES "
                    "(101, 1, NULL), "
                    "(102, 2, NULL), "
                    "(103, 3, 'storyboard')"
                )
            )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def mode_for_task(self, task_id: int) -> str | None:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT workflow_mode FROM oc_rebuild_tasks WHERE id = :task_id"), {"task_id": task_id}).scalar_one()

    def test_dry_run_reports_only_legacy_storyboard_candidates(self) -> None:
        result = run_backfill(self.engine, write=False)

        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(result["candidates"][0]["task_id"], 101)
        self.assertIsNone(self.mode_for_task(101))

    def test_write_is_idempotent_and_does_not_mutate_plain_rebuild_rows(self) -> None:
        first = run_backfill(self.engine, write=True)
        second = run_backfill(self.engine, write=True)

        self.assertEqual(first["updated_count"], 1)
        self.assertEqual(second["updated_count"], 0)
        self.assertEqual(self.mode_for_task(101), "storyboard")
        self.assertIsNone(self.mode_for_task(102))
        self.assertEqual(self.mode_for_task(103), "storyboard")


if __name__ == "__main__":
    unittest.main()
