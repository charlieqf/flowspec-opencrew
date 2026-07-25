from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.koubo.koubo_storyboard import asset_history_services, runtime  # noqa: E402


class KouboStoryboardMediaBinaryContractTest(unittest.TestCase):
    def test_storyboard_video_dimensions_uses_configured_ffprobe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ffprobe = root / "configured-ffprobe"
            ffprobe.write_text("#!/bin/sh\nprintf '%s\\n' '{\"streams\":[{\"width\":1080,\"height\":1920}]}'\n", encoding="utf-8")
            ffprobe.chmod(0o755)
            video = root / "video.mp4"
            video.write_bytes(b"contract-test")

            with patch.dict(os.environ, {"OPENCREW_FFPROBE_PATH": str(ffprobe)}), patch.object(runtime.shutil, "which", return_value=None):
                dimensions = asset_history_services.media_video_dimensions(video)

            self.assertEqual(dimensions, (1080, 1920))

    def test_storyboard_resolves_repo_bundled_ffprobe_without_system_install(self) -> None:
        with patch.dict(os.environ, {"OPENCREW_FFPROBE_PATH": ""}), patch.object(runtime.shutil, "which", return_value=None):
            resolved = runtime.resolve_media_binary("ffprobe")

        self.assertEqual(Path(resolved), REPO_ROOT / "ToolLibrary" / ".bin" / "ffprobe")


if __name__ == "__main__":
    unittest.main()
