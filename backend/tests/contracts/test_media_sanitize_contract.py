from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (REPO_ROOT / "backend",):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from opcrew_backend.services import media_sanitize  # noqa: E402


class MediaSanitizeContractTest(unittest.TestCase):
    def ffprobe_tags(self, path: Path) -> dict[str, str]:
        ffprobe = media_sanitize.media_binary("ffprobe")
        self.assertTrue(ffprobe, "ffprobe is required for media sanitize contract tests")
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

    def test_image_sanitizer_strips_png_text_metadata(self) -> None:
        from PIL import Image, PngImagePlugin

        info = PngImagePlugin.PngInfo()
        info.add_text("generator", "OpenAI SynthID google metadata")
        image = Image.new("RGB", (2, 2), (255, 0, 0))
        raw = BytesIO()
        image.save(raw, "PNG", pnginfo=info)
        image.close()
        raw_bytes = raw.getvalue()

        self.assertIn(b"SynthID", raw_bytes)
        sanitized = media_sanitize.sanitize_image_bytes(raw_bytes, ".png")

        self.assertNotIn(b"SynthID", sanitized)
        self.assertNotIn(b"OpenAI", sanitized)
        with Image.open(BytesIO(sanitized)) as sanitized_image:
            self.assertEqual(sanitized_image.size, (2, 2))

    def test_video_sanitizer_strips_format_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "provider.mp4"
            self.run_ffmpeg([
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=16x16:d=0.2",
                "-metadata",
                "title=OpenAI provider video",
                "-metadata",
                "comment=SynthID google provider marker",
                "-pix_fmt",
                "yuv420p",
                str(video_path),
            ])

            before = json.dumps(self.ffprobe_tags(video_path), ensure_ascii=False)
            self.assertIn("OpenAI", before)
            media_sanitize.sanitize_video_file_metadata(video_path)
            after = json.dumps(self.ffprobe_tags(video_path), ensure_ascii=False)

            self.assertNotIn("OpenAI", after)
            self.assertNotIn("SynthID", after)

    def test_audio_sanitizer_strips_format_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "tts.wav"
            self.run_ffmpeg([
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=0.2",
                "-metadata",
                "artist=HeyGen voice",
                "-metadata",
                "comment=OpenAI SynthID audio marker",
                str(audio_path),
            ])

            before = json.dumps(self.ffprobe_tags(audio_path), ensure_ascii=False)
            self.assertIn("OpenAI", before)
            media_sanitize.sanitize_audio_file_metadata(audio_path)
            after = json.dumps(self.ffprobe_tags(audio_path), ensure_ascii=False)

            self.assertNotIn("OpenAI", after)
            self.assertNotIn("HeyGen", after)


if __name__ == "__main__":
    unittest.main()
