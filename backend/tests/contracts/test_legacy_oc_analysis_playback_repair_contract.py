from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
scripts_path = str(REPO_ROOT / "backend" / "scripts")
if scripts_path not in sys.path:
    sys.path.insert(0, scripts_path)

from repair_legacy_oc_analysis_playback import inspect_workspace  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class LegacyOCAnalysisPlaybackRepairContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "sessions" / "123" / "workspace"
        self.workspace.mkdir(parents=True)
        self.external_video = self.root / "external" / "legacy.mp4"
        self.external_video.parent.mkdir(parents=True)
        self.external_video.write_bytes(b"video-bytes" * 32)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_virtual_manifest(self, source_value: str) -> Path:
        manifest = self.workspace / "schemes" / "scheme_1" / "manifest.json"
        write_json(
            manifest,
            {
                "clip_mode": "virtual",
                "source_video_path": source_value,
                "items": [
                    {
                        "segment_index": 1,
                        "clip_status": "virtual",
                        "clip_path": source_value,
                        "source_video_path": source_value,
                        "start": 0,
                        "end": 1,
                    }
                ],
            },
        )
        return manifest

    def test_dry_run_detects_external_virtual_manifest_without_mutation(self) -> None:
        manifest = self.write_virtual_manifest(str(self.external_video))

        report = inspect_workspace(123, 456, self.workspace, str(self.external_video), write=False)

        self.assertEqual(report["status"], "repairable")
        self.assertFalse((self.workspace / "source_video.mp4").exists())
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["source_video_path"], str(self.external_video))
        self.assertIn("would_copy_source_video", {action["code"] for action in report["actions"]})
        self.assertIn("would_rewrite_virtual_manifest", {action["code"] for action in report["actions"]})

    def test_write_repairs_source_video_and_virtual_manifest_paths(self) -> None:
        manifest = self.write_virtual_manifest(str(self.external_video))

        report = inspect_workspace(123, 456, self.workspace, str(self.external_video), write=True)

        self.assertEqual(report["status"], "repaired")
        self.assertEqual((self.workspace / "source_video.mp4").read_bytes(), self.external_video.read_bytes())
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_video_path"], "source_video.mp4")
        self.assertEqual(payload["items"][0]["clip_path"], "source_video.mp4")
        self.assertEqual(payload["items"][0]["source_video_path"], "source_video.mp4")

    def test_write_replaces_workspace_outside_source_video_symlink(self) -> None:
        (self.workspace / "source_video.mp4").symlink_to(self.external_video)
        self.write_virtual_manifest("source_video.mp4")

        report = inspect_workspace(123, 456, self.workspace, "", write=True)

        self.assertEqual(report["status"], "repaired")
        self.assertFalse((self.workspace / "source_video.mp4").is_symlink())
        self.assertEqual((self.workspace / "source_video.mp4").read_bytes(), self.external_video.read_bytes())

    def test_valid_in_workspace_source_video_and_manifest_are_ok(self) -> None:
        source = self.workspace / "source_video.mp4"
        source.write_bytes(b"safe-video")
        self.write_virtual_manifest("source_video.mp4")

        report = inspect_workspace(123, 456, self.workspace, "", write=False)

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["actions"], [])
        self.assertEqual(report["issues"], [])


if __name__ == "__main__":
    unittest.main()
