from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.media_library_status import (  # noqa: E402
    derive_asset_status,
    derive_visual_status,
)


class MediaLibraryStatusContractTest(unittest.TestCase):
    def test_stale_visual_structure_projects_stale_visual_status(self) -> None:
        self.assertEqual(
            derive_visual_status("stale", "not_analyzed"),
            "stale",
        )
        self.assertEqual(derive_visual_status("stale", "ready"), "stale")

    def test_blocked_dialogue_does_not_hide_stale_visual_content(self) -> None:
        self.assertEqual(
            derive_asset_status(
                {
                    "dialogue_status": "blocked",
                    "visual_status": "stale",
                    "composite_status": "not_analyzed",
                }
            ),
            "partial",
        )


if __name__ == "__main__":
    unittest.main()
