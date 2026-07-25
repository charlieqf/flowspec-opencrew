from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from fastapi import HTTPException  # noqa: E402
from opcrew_backend.services.session_files import SessionFileService  # noqa: E402


class SessionFilePolicyContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = SessionFileService()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_rejects_path_traversal_and_absolute_escape(self) -> None:
        with self.assertRaises(HTTPException):
            self.service.resolve_workspace_path(self.root, "../secret.txt")
        with self.assertRaises(HTTPException):
            self.service.resolve_workspace_path(self.root, "/tmp/secret.txt")

    def test_rejects_hidden_and_sensitive_files(self) -> None:
        (self.root / ".env").write_text("SECRET=1", encoding="utf-8")
        (self.root / "token.json").write_text("{}", encoding="utf-8")

        with self.assertRaises(HTTPException):
            self.service.resolve_download(self.root, ".env")
        with self.assertRaises(HTTPException):
            self.service.resolve_download(self.root, "token.json")

    def test_rejects_symlink_escape(self) -> None:
        outside = self.root.parent / "outside-opencrew-secret.txt"
        outside.write_text("secret", encoding="utf-8")
        try:
            (self.root / "safe_link.txt").symlink_to(outside)
            with self.assertRaises(HTTPException):
                self.service.resolve_download(self.root, "safe_link.txt")
        finally:
            outside.unlink(missing_ok=True)

    def test_share_filters_internal_and_non_downloadable_files(self) -> None:
        rows = [
            {"path": "outbox/result.txt", "downloadable": 1, "visibility": "public"},
            {"path": "meta/debug.json", "downloadable": 0, "visibility": "internal"},
            {"path": ".env", "downloadable": 1, "visibility": "public"},
        ]

        visible = self.service.visible_file_rows(rows, audience="share")

        self.assertEqual([row["path"] for row in visible], ["outbox/result.txt"])

    def test_provider_sidecar_json_files_are_not_downloadable_or_zipped(self) -> None:
        sensitive_paths = [
            "SessionOutput/storyboard/assets/images/generated.json",
            "SessionOutput/storyboard/assets/videos/123_agent_digital_human_x.json",
            "SessionOutput/storyboard/koubo_storyboard_assets.json",
        ]
        public_paths = [
            "SessionOutput/storyboard/assets/images/generated.png",
            "SessionOutput/storyboard/assets/videos/123_agent_digital_human_x.mp4",
            "SessionScratch/CleanImageGenerations/g1/image.png",
        ]
        for rel in [*sensitive_paths, *public_paths]:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

        for rel in sensitive_paths:
            with self.assertRaises(HTTPException):
                self.service.resolve_download(self.root, rel)

        for rel in public_paths:
            self.assertEqual(self.service.resolve_download(self.root, rel), (self.root / rel).resolve())

        zip_names = {arcname for _path, arcname in self.service.zip_entries(self.root, self.root)}
        for rel in sensitive_paths:
            self.assertNotIn(rel, zip_names)
        for rel in public_paths:
            self.assertIn(rel, zip_names)

    def test_internal_context_tool_working_and_execution_files_are_blocked(self) -> None:
        sensitive_paths = [
            "SessionContext/Variables.json",
            "SessionScratch/cleanImageGenerations/request.json",
            "S13_05_06_VideoOnlyPlanExecutor/Working/provider_response.json",
            "SessionOutput/storyboard/video_only_plan_execution_state.json",
            "SessionOutput/storyboard/video_only_plan_execution_result.json",
        ]
        public_path = "SessionOutput/storyboard/assets/videos/generated.mp4"
        for rel in [*sensitive_paths, public_path]:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

        for rel in sensitive_paths:
            policy = self.service.classify(rel)
            self.assertEqual(policy, {"visibility": "internal", "sensitivity": "sensitive", "downloadable": 0})
            with self.assertRaises(HTTPException) as raised:
                self.service.resolve_download(self.root, rel)
            self.assertEqual(raised.exception.status_code, 403)

        self.assertEqual(self.service.resolve_download(self.root, public_path), (self.root / public_path).resolve())

        zip_names = {arcname for _path, arcname in self.service.zip_entries(self.root, self.root)}
        for rel in sensitive_paths:
            self.assertNotIn(rel, zip_names)
        self.assertIn(public_path, zip_names)


if __name__ == "__main__":
    unittest.main()
