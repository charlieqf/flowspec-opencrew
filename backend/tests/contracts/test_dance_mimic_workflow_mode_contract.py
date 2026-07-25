from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.db.migrations import run_migrations  # noqa: E402
from opcrew_backend.workflow_modes import (  # noqa: E402
    WORKFLOW_ANALYSIS_V1,
    WORKFLOW_DANCE_MIMIC_V1,
    WORKFLOW_SCRIPT,
    infer_openclip_workflow_mode,
    is_analysis_v1_compatible_workflow,
    storyboard_meta_for_workflow,
)


class DanceMimicWorkflowModeContractTest(unittest.TestCase):
    def test_helper_prefers_db_mode_and_normalizes_legacy_aliases(self) -> None:
        self.assertEqual(infer_openclip_workflow_mode({"workflow_mode": "dance_mimic"}), WORKFLOW_DANCE_MIMIC_V1)
        self.assertEqual(infer_openclip_workflow_mode({"workflow_mode": "openclip_analysis"}), WORKFLOW_ANALYSIS_V1)
        self.assertEqual(infer_openclip_workflow_mode({"workflow_mode": "script_only"}), WORKFLOW_SCRIPT)
        self.assertTrue(is_analysis_v1_compatible_workflow(WORKFLOW_ANALYSIS_V1))
        self.assertTrue(is_analysis_v1_compatible_workflow(WORKFLOW_SCRIPT))
        self.assertFalse(is_analysis_v1_compatible_workflow(WORKFLOW_DANCE_MIMIC_V1))

    def test_helper_falls_back_to_task_meta_and_storyboard_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            meta_path = workspace / "SessionOutput/task_list/task_meta.json"
            meta_path.parent.mkdir(parents=True)
            meta_path.write_text(
                json.dumps({"workflow_id": "dance_mimic_v1", "create_mode": "dance_mimic"}),
                encoding="utf-8",
            )

            mode = infer_openclip_workflow_mode({"workflow_mode": ""}, workspace=workspace)
            self.assertEqual(mode, WORKFLOW_DANCE_MIMIC_V1)
            storyboard_meta = storyboard_meta_for_workflow(mode)
            self.assertEqual(storyboard_meta["title"], "故事板（舞蹈复刻）")
            self.assertEqual(storyboard_meta["source_type"], "dance_mimic_v1_storyboard")

    def test_migration_backfills_openclip_workflow_modes_from_task_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_workspace = root / "script"
            dance_workspace = root / "dance"
            analysis_workspace = root / "analysis"
            for workspace in (script_workspace, dance_workspace, analysis_workspace):
                (workspace / "SessionOutput/task_list").mkdir(parents=True)
            (script_workspace / "SessionOutput/task_list/task_meta.json").write_text(
                json.dumps({"create_mode": "script"}),
                encoding="utf-8",
            )
            (dance_workspace / "SessionOutput/task_list/task_meta.json").write_text(
                json.dumps({"workflow_id": "dance_mimic_v1", "create_mode": "dance_mimic"}),
                encoding="utf-8",
            )

            engine = create_engine("sqlite:///:memory:", future=True)
            try:
                with engine.begin() as conn:
                    conn.execute(text("CREATE TABLE sessions (id INTEGER PRIMARY KEY, workspace_dir TEXT)"))
                    conn.execute(text("CREATE TABLE openclip_tasks (id INTEGER PRIMARY KEY, session_id INTEGER, reference_video_path TEXT)"))
                    conn.execute(
                        text(
                            "INSERT INTO sessions (id, workspace_dir) VALUES "
                            "(1, :script_workspace), (2, :dance_workspace), (3, :analysis_workspace)"
                        ),
                        {
                            "script_workspace": str(script_workspace),
                            "dance_workspace": str(dance_workspace),
                            "analysis_workspace": str(analysis_workspace),
                        },
                    )
                    conn.execute(
                        text(
                            "INSERT INTO openclip_tasks (id, session_id, reference_video_path) VALUES "
                            "(101, 1, ''), (102, 2, 'SessionContext/Video_Reference_Source.mp4'), (103, 3, '/tmp/source.mp4')"
                        )
                    )

                run_migrations(engine)

                with engine.connect() as conn:
                    rows = {
                        int(row[0]): str(row[1])
                        for row in conn.execute(text("SELECT id, workflow_mode FROM openclip_tasks ORDER BY id")).fetchall()
                    }
                self.assertEqual(rows[101], WORKFLOW_SCRIPT)
                self.assertEqual(rows[102], WORKFLOW_DANCE_MIMIC_V1)
                self.assertEqual(rows[103], WORKFLOW_ANALYSIS_V1)
            finally:
                engine.dispose()

    def test_analysis_v1_run_routes_have_workflow_guard(self) -> None:
        source = (REPO_ROOT / "backend/opcrew_backend/koubo/router.py").read_text(encoding="utf-8")
        self.assertIn("workflow_mode_not_analysis_v1", source)
        self.assertGreaterEqual(source.count("ensure_analysis_v1_compatible_task(task_row)"), 2)


if __name__ == "__main__":
    unittest.main()
