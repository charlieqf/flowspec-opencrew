from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from scripts.repair_media_library_tool_sessions import (  # noqa: E402
    business_status_for_terminal,
    relink_legacy_context_media,
    terminal_status_for,
)


class MediaLibraryToolSessionRepairContractTest(unittest.TestCase):
    def test_terminal_status_preserves_blocked_step(self) -> None:
        summary = {"status": "running", "steps": [{"step_id": "S2", "status": "blocked"}]}

        self.assertEqual(
            terminal_status_for(business_status="failed", summary=summary),
            "blocked",
        )
        self.assertEqual(
            terminal_status_for(business_status="ready", summary={"steps": [{"status": "completed"}]}),
            "completed",
        )
        self.assertEqual(
            terminal_status_for(business_status="failed", summary={"steps": [{"status": "failed"}]}),
            "failed",
        )
        self.assertEqual(business_status_for_terminal("blocked"), "blocked")
        self.assertEqual(business_status_for_terminal("completed"), "ready")
        self.assertEqual(business_status_for_terminal("failed"), "failed")

    def test_relink_is_dry_run_by_default_and_atomic_when_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "0_SessionContext"
            legacy_root = root / "SessionContext"
            source_root.mkdir()
            legacy_root.mkdir()
            source = source_root / "Video_Source.mp4"
            target = legacy_root / "Video_Source.mp4"
            source.write_bytes(b"video")
            target.write_bytes(b"video")
            original_target_inode = target.stat().st_ino

            dry_run = relink_legacy_context_media(root, write=False)
            self.assertEqual(dry_run[0]["status"], "would_relink")
            self.assertEqual(target.stat().st_ino, original_target_inode)

            applied = relink_legacy_context_media(root, write=True)
            self.assertEqual(applied[0]["status"], "relinked")
            self.assertTrue(source.samefile(target))


if __name__ == "__main__":
    unittest.main()
