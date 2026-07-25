from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (REPO_ROOT / "backend", REPO_ROOT / "backend" / "scripts"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from opcrew_backend.services import media_sanitize  # noqa: E402
from sanitize_existing_assets import WorkspaceRef, run_sanitizer  # noqa: E402


def write_png_with_text(path: Path, text: str) -> None:
    from PIL import Image, PngImagePlugin

    path.parent.mkdir(parents=True, exist_ok=True)
    info = PngImagePlugin.PngInfo()
    info.add_text("generator", text)
    image = Image.new("RGB", (2, 2), (0, 0, 255))
    try:
        output = BytesIO()
        image.save(output, "PNG", pnginfo=info)
        path.write_bytes(output.getvalue())
    finally:
        image.close()


class SanitizeExistingAssetsContractTest(unittest.TestCase):
    def ffprobe_tags(self, path: Path) -> dict[str, str]:
        ffprobe = media_sanitize.media_binary("ffprobe")
        self.assertTrue(ffprobe, "ffprobe is required for sanitize-existing-assets contract tests")
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout or "{}")
        return payload.get("format", {}).get("tags", {}) if isinstance(payload.get("format"), dict) else {}

    def run_ffmpeg(self, args: list[str]) -> None:
        ffmpeg = media_sanitize.ffmpeg_binary()
        completed = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def write_video_with_metadata(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run_ffmpeg([
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:d=0.2",
            "-metadata",
            "title=OpenAI provider video",
            "-metadata",
            "comment=SynthID Google provider marker",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ])

    def write_audio_with_metadata(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run_ffmpeg([
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.2",
            "-metadata",
            "artist=HeyGen voice",
            "-metadata",
            "comment=OpenAI SynthID audio marker",
            str(path),
        ])

    def assert_no_sensitive_tags(self, path: Path) -> None:
        tags = json.dumps(self.ffprobe_tags(path), ensure_ascii=False)
        self.assertNotIn("OpenAI", tags)
        self.assertNotIn("Google", tags)
        self.assertNotIn("HeyGen", tags)
        self.assertNotIn("SynthID", tags)

    def test_dry_run_and_write_sanitize_only_whitelisted_media_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            image_path = workspace / "SessionOutput/storyboard/assets/images/generated.png"
            clean_image_path = workspace / "SessionScratch/CleanImageGenerations/g1/image.png"
            video_path = workspace / "SessionOutput/storyboard/assets/videos/generated.mp4"
            history_video_path = workspace / "SessionOutput/storyboard/assets/history/batch_1/generated.mp4"
            audio_path = workspace / "SessionOutput/storyboard/assets/audios/generated.wav"
            outside_path = workspace / "SessionOutput/not_scanned/outside.png"

            write_png_with_text(image_path, "OpenAI SynthID google metadata")
            write_png_with_text(clean_image_path, "OpenAI clean image metadata")
            write_png_with_text(outside_path, "OpenAI outside metadata")
            self.write_video_with_metadata(video_path)
            self.write_video_with_metadata(history_video_path)
            self.write_audio_with_metadata(audio_path)

            dry_run = run_sanitizer([WorkspaceRef(session_id=42, workspace=workspace)], write=False)

            self.assertEqual(dry_run["mode"], "dry-run")
            self.assertEqual(dry_run["candidate_count"], 5)
            self.assertEqual(dry_run["blocked_count"], 0)
            self.assertIn(b"OpenAI", image_path.read_bytes())
            self.assertIn(b"OpenAI", outside_path.read_bytes())
            self.assertNotIn("SessionOutput/not_scanned/outside.png", json.dumps(dry_run["candidates"], ensure_ascii=False))

            report = run_sanitizer(
                [WorkspaceRef(session_id=42, workspace=workspace)],
                write=True,
                snapshot_dir=workspace / "snapshots",
            )

            self.assertTrue(report["ok"])
            self.assertEqual(report["mode"], "write")
            self.assertEqual(report["candidate_count"], 5)
            self.assertEqual(report["sanitized_count"], 5)
            self.assertEqual(report["error_count"], 0)
            self.assertTrue(any("snapshot_path" in operation for operation in report["operations"]))  # type: ignore[operator]
            self.assertNotIn(b"OpenAI", image_path.read_bytes())
            self.assertNotIn(b"OpenAI", clean_image_path.read_bytes())
            self.assertIn(b"OpenAI", outside_path.read_bytes())
            self.assert_no_sensitive_tags(video_path)
            self.assert_no_sensitive_tags(history_video_path)
            self.assert_no_sensitive_tags(audio_path)


if __name__ == "__main__":
    unittest.main()
