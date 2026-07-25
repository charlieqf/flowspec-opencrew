from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ToolLibrary.Analysis_V1.opencode_autoheal as autoheal  # noqa: E402
from ToolLibrary.Analysis_V1.opencode_autoheal import (  # noqa: E402
    is_opencode_session_not_found,
    recover_opencode_session_id,
)


class AnalysisV1OpenCodeAutohealContractTest(unittest.TestCase):
    def test_detects_session_not_found_errors(self) -> None:
        self.assertTrue(is_opencode_session_not_found(RuntimeError('OpenCode HTTP 404: {"message":"Session not found"}')))
        self.assertFalse(is_opencode_session_not_found(RuntimeError("OpenCode HTTP 500: provider unavailable")))

    def test_recover_creates_session_and_updates_variables_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            variables_dir = workspace / "SessionContext"
            variables_dir.mkdir(parents=True)
            variables_path = variables_dir / "Variables.json"
            variables = {
                "schema_version": "analysis_v1_session_context_0.1",
                "task_id": 31,
                "opencrew_session_id": None,
                "opencode_session_id": "ses_stale",
            }
            variables_path.write_text(json.dumps(variables), encoding="utf-8")
            calls: list[dict] = []

            def fake_request(runtime, method, path, payload, directory, timeout):
                calls.append({"runtime": runtime, "method": method, "path": path, "payload": payload, "directory": directory, "timeout": timeout})
                return {"id": "ses_new"}

            session_id = recover_opencode_session_id(
                runtime={"base_url": "http://127.0.0.1:4096", "username": "u", "password": "p"},
                variables=variables,
                workspace=workspace,
                request_func=fake_request,
                database_url="",
                title="Analysis_V1 task 31",
            )

            updated = json.loads(variables_path.read_text(encoding="utf-8"))

        self.assertEqual(session_id, "ses_new")
        self.assertEqual(updated["opencode_session_id"], "ses_new")
        self.assertEqual(calls[0]["method"], "POST")
        self.assertEqual(calls[0]["path"], "/session")
        self.assertEqual(calls[0]["payload"], {"title": "Analysis_V1 task 31"})

    def test_recover_keeps_new_session_when_db_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            variables_dir = workspace / "SessionContext"
            variables_dir.mkdir(parents=True)
            variables_path = variables_dir / "Variables.json"
            variables = {
                "schema_version": "analysis_v1_session_context_0.1",
                "task_id": 31,
                "opencrew_session_id": 87,
                "opencode_session_id": "ses_stale",
            }
            variables_path.write_text(json.dumps(variables), encoding="utf-8")

            def fake_request(runtime, method, path, payload, directory, timeout):
                return {"id": "ses_new_after_db_error"}

            def failing_update(*_args, **_kwargs):
                raise RuntimeError("db unavailable")

            original_update = autoheal._update_opencrew_session
            try:
                autoheal._update_opencrew_session = failing_update
                with self.assertLogs(autoheal.logger, level="WARNING") as logs:
                    session_id = recover_opencode_session_id(
                        runtime={"base_url": "http://127.0.0.1:4096", "username": "u", "password": "p"},
                        variables=variables,
                        workspace=workspace,
                        request_func=fake_request,
                        database_url="postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew",
                        title="Analysis_V1 task 31",
                    )
            finally:
                autoheal._update_opencrew_session = original_update

            updated = json.loads(variables_path.read_text(encoding="utf-8"))

        self.assertEqual(session_id, "ses_new_after_db_error")
        self.assertEqual(updated["opencode_session_id"], "ses_new_after_db_error")
        self.assertIn("DB session row update failed", logs.output[0])


if __name__ == "__main__":
    unittest.main()
