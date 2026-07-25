from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (
    REPO_ROOT / "backend",
    REPO_ROOT / "ModelConfig" / "backend",
    REPO_ROOT / "backend",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from opcrew_backend.koubo.koubo_storyboard import media_tts_provider_services  # noqa: E402


class KouboStoryBoardCosyVoiceTtsRetryContractTest(unittest.TestCase):
    def test_storyboard_cosyvoice_compacts_template_and_retries_without_instruction(self) -> None:
        ns = SimpleNamespace()
        media_tts_provider_services.register_media_tts_provider_services(ns)
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_cosyvoice_audio_bytes(
            _api_key: str,
            _model: str,
            _voice_id: str,
            _sample_text: str,
            complex_prompt: str = "",
            **kwargs: object,
        ) -> bytes:
            calls.append((complex_prompt, kwargs))
            if len(calls) == 1:
                raise RuntimeError(
                    "CosyVoice speech synthesis returned empty audio. "
                    "Provider detail: DashScope event=task-failed, error_code=InvalidParameter, "
                    "error_message=[cosyvoice]Engine return error code: 428"
                )
            return b"fake-wav"

        prompt = (
            "请用普通话生成自然短视频口播。\n\n"
            "声音要求：自然、清晰、像手机自拍视频口播；避免硬广、夸张直播腔或机械朗读。\n"
            "节奏：中速平稳，重点词轻微强调。\n"
            "朗读规则：只朗读当前文本，不要读说明文字。\n\n"
            "严格朗读当前 Scene 文本，不要朗读 prompt 中的示例文本或历史文本。"
        )
        config = {"provider": "cosyvoice", "model": "cosyvoice-v3.5-flash", "api_key": "key"}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "out.wav"
            with patch.object(media_tts_provider_services, "dashscope_cosyvoice_tts_audio_bytes", fake_cosyvoice_audio_bytes), patch.object(media_tts_provider_services, "sanitize_audio_file_metadata", lambda _path: None):
                ns.generate_tts_audio(config, "说真的 如果你觉得心累到快炸了 就去生场小病吧", "voice-id", prompt, output_path, sc=ns)

            self.assertEqual(output_path.read_bytes(), b"fake-wav")

        self.assertEqual(calls[0][0], "普通话自然短视频口播；声音自然清晰，像自拍视频；中速平稳，重点词轻微强调；只朗读正文。")
        self.assertLessEqual(len(calls[0][0]), 96)
        self.assertEqual(calls[1][0], "")
        self.assertEqual(calls[1][1].get("max_attempts"), 1)

    def test_storyboard_heygen_tts_reports_tts_provider_error_not_image_provider(self) -> None:
        ns = SimpleNamespace()
        media_tts_provider_services.register_media_tts_provider_services(ns)
        calls: list[dict[str, object]] = []

        def fake_post_json_request(
            url: str,
            payload: dict[str, object],
            headers: dict[str, str],
            timeout: int = 120,
            error_prefix: str = "",
        ) -> dict[str, object]:
            calls.append({
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
                "error_prefix": error_prefix,
            })
            raise HTTPException(
                status_code=502,
                detail=(
                    f"{error_prefix} request failed: HTTP 500: "
                    '{"error":{"code":"internal_error","message":"Internal Server Error"}}'
                ),
            )

        config = {"provider": "heygen", "model": "heygen-voice-clone-v3", "api_key": "key"}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "out.mp3"
            with patch.object(ns, "post_json_request", fake_post_json_request, create=True):
                with self.assertRaises(HTTPException) as raised:
                    ns.generate_tts_audio(config, "你好，欢迎使用 OpenCrew。", "voice_123", "", output_path, sc=ns)

        self.assertEqual(calls[0]["error_prefix"], "TTS provider")
        detail = str(raised.exception.detail)
        self.assertIn("TTS provider request failed", detail)
        self.assertIn("HeyGen TTS 返回 5xx", detail)
        self.assertNotIn("Image provider", detail)

    def test_storyboard_audio_duration_uses_repo_ffprobe_for_non_wav_content(self) -> None:
        ns = SimpleNamespace()
        media_tts_provider_services.register_media_tts_provider_services(ns)
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout="4.127344\n", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "heygen_audio.wav"
            audio_path.write_bytes(b"not-a-riff-wav")
            with patch.object(media_tts_provider_services.shutil, "which", return_value=None):
                with patch.object(media_tts_provider_services.subprocess, "run", fake_run):
                    duration = ns.audio_duration_seconds(audio_path)

        self.assertEqual(duration, 4.127)
        self.assertTrue(calls)
        self.assertIn(REPO_ROOT / "ToolLibrary" / ".bin" / "ffprobe", ns.media_binary_candidates("ffprobe"))


if __name__ == "__main__":
    unittest.main()
