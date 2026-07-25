from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
OPENCLIP_BACKEND_ROOT = REPO_ROOT / "backend"
for path in (BACKEND_ROOT, OPENCLIP_BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

CONSTANTS_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "constants.py"
MEDIA_GRID_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "components" / "MediaGrid.jsx"
OVERLAY_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "UploadAssetLibraryOverlay.jsx"
UPLOAD_MODEL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "uploadAssetLibraryModel.js"
STORYBOARD_ASSETS_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardAssets.js"
ASSET_ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_routes.py"


class KouboAssetAudioUploadContractTest(unittest.TestCase):
    def test_backend_classifies_common_audio_extensions_as_audio(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard.asset_core_services import register_asset_core_services
        from opcrew_backend.koubo.koubo_storyboard.constants import AUDIO_EXTS

        expected = {".wav", ".m4a", ".mp3", ".aac", ".ogg", ".oga", ".flac", ".opus", ".aiff", ".aif", ".caf", ".weba", ".wma"}
        self.assertTrue(expected.issubset(AUDIO_EXTS))

        ns = SimpleNamespace()
        register_asset_core_services(ns)
        for suffix in expected:
            self.assertEqual(ns.asset_type_for_path(f"SessionOutput/storyboard/assets/audios/example{suffix}"), "Audio")

        routes = ASSET_ROUTES_PATH.read_text(encoding="utf-8")
        self.assertIn('content_type.startswith("audio/")', routes)
        self.assertIn('content_type.startswith("video/")', routes)

    def test_frontend_audio_upload_allowlist_and_feedback_match_backend(self) -> None:
        constants = CONSTANTS_PATH.read_text(encoding="utf-8")
        media_grid = MEDIA_GRID_PATH.read_text(encoding="utf-8")
        overlay = OVERLAY_PATH.read_text(encoding="utf-8")
        upload_model = UPLOAD_MODEL_PATH.read_text(encoding="utf-8")
        storyboard_assets = STORYBOARD_ASSETS_PATH.read_text(encoding="utf-8")

        for token in (".flac", ".opus", ".aiff", ".aif", ".caf", ".weba", ".wma"):
            self.assertIn(token, constants)
            self.assertIn(token, media_grid)
            self.assertIn(token.lstrip("."), overlay)
            self.assertIn(token.lstrip("."), upload_model)
            self.assertIn(token.lstrip("."), storyboard_assets)

        for token in (
            "audio/*",
            "uploadStatus",
            "Unsupported ${kind === \"audio\" ? \"audio\" : \"video\"} file type",
            "No ${kind === \"videos\" ? \"video\" : \"audio\"} assets were added",
            "ual-media-upload-status",
        ):
            self.assertIn(token, media_grid + overlay)


if __name__ == "__main__":
    unittest.main()
