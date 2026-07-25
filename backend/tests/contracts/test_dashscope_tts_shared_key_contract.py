from __future__ import annotations

import os
import sys
from types import ModuleType
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
MODEL_CONFIG_BACKEND = REPO_ROOT / "ModelConfig" / "backend"
for path in (BACKEND_ROOT, MODEL_CONFIG_BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from opcrew_model_config import media_model_config  # noqa: E402


class FakeSecretStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str) -> str:
        return self.values.get(key, "")

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeRow:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping


class FakeResult:
    def __init__(self, row: FakeRow | None) -> None:
        self.row = row

    def first(self) -> FakeRow | None:
        return self.row


class FakeConnection:
    def __init__(self, rows: dict[tuple[str, str], dict[str, str]]) -> None:
        self.rows = rows

    def execute(self, _statement: object, params: dict[str, str] | None = None) -> FakeResult:
        params = params or {}
        kind = str(params.get("kind") or "tts")
        provider = str(params.get("provider") or "")
        row = self.rows.get((kind, provider))
        return FakeResult(FakeRow(row) if row is not None else None)


class FakeBegin:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    def __enter__(self) -> FakeConnection:
        return self.conn

    def __exit__(self, *_exc: object) -> bool:
        return False


class FakeEngine:
    def __init__(self, rows: dict[tuple[str, str], dict[str, str]]) -> None:
        self.conn = FakeConnection(rows)

    def begin(self) -> FakeBegin:
        return FakeBegin(self.conn)


class DashScopeTTSSharedKeyContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ensure_table_patch = patch.object(media_model_config, "ensure_table", lambda _ctx: None)
        self.ensure_table_patch.start()

    def tearDown(self) -> None:
        self.ensure_table_patch.stop()

    def fake_ctx(self, rows: dict[tuple[str, str], dict[str, str]], secrets: dict[str, str] | None = None) -> SimpleNamespace:
        return SimpleNamespace(engine=FakeEngine(rows), secret_store=FakeSecretStore(secrets))

    def test_cosyvoice_reuses_qwen_dashscope_tts_key(self) -> None:
        ctx = self.fake_ctx(
            {
                ("tts", "cosyvoice"): {"api_key_ref": "tts_cosyvoice_key", "api_key_ciphertext": ""},
                ("tts", "qwen"): {"api_key_ref": "tts_qwen_key", "api_key_ciphertext": ""},
            },
            {"tts_qwen_key": "dashscope-key"},
        )

        self.assertEqual(media_model_config.load_stored_key(ctx, "tts", "cosyvoice"), "dashscope-key")

    def test_cosyvoice_voice_clone_reuses_existing_tts_key(self) -> None:
        rows = {
            ("voice-clone", "cosyvoice"): {"api_key_ref": "voice_clone_cosyvoice_key", "api_key_ciphertext": ""},
            ("tts", "cosyvoice"): {"api_key_ref": "tts_cosyvoice_key", "api_key_ciphertext": ""},
        }
        ctx = self.fake_ctx(rows, {"tts_cosyvoice_key": "dashscope-key"})

        self.assertEqual(media_model_config.load_stored_key(ctx, "voice-clone", "cosyvoice"), "dashscope-key")
        self.assertTrue(
            media_model_config.provider_has_submitted_or_stored_key(
                ctx,
                ctx.engine.conn,
                "voice-clone",
                "cosyvoice",
                "",
            )
        )

    def test_cosyvoice_voice_clone_reuses_tts_key_without_own_config_row(self) -> None:
        rows = {
            ("tts", "cosyvoice"): {"api_key_ref": "tts_cosyvoice_key", "api_key_ciphertext": ""},
        }
        ctx = self.fake_ctx(rows, {"tts_cosyvoice_key": "dashscope-key"})

        self.assertEqual(media_model_config.load_stored_key(ctx, "voice-clone", "cosyvoice"), "dashscope-key")
        self.assertTrue(
            media_model_config.provider_has_submitted_or_stored_key(
                ctx,
                ctx.engine.conn,
                "voice-clone",
                "cosyvoice",
                "",
            )
        )

    def test_wan_video_reuses_qwen_dashscope_tts_key(self) -> None:
        ctx = self.fake_ctx(
            {
                ("video", "wan"): {"api_key_ref": "video_wan_key", "api_key_ciphertext": ""},
            },
            {"tts_qwen_key": "dashscope-key"},
        )

        self.assertEqual(media_model_config.load_stored_key(ctx, "video", "wan"), "dashscope-key")

    def test_cosyvoice_reuses_dashscope_environment_key(self) -> None:
        ctx = self.fake_ctx({
            ("tts", "cosyvoice"): {"api_key_ref": "tts_cosyvoice_key", "api_key_ciphertext": ""},
        })

        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-dashscope-key"}, clear=False):
            self.assertEqual(media_model_config.load_stored_key(ctx, "tts", "cosyvoice"), "env-dashscope-key")

    def test_direct_environment_api_key_ref_is_supported(self) -> None:
        ctx = self.fake_ctx({
            ("tts", "cosyvoice"): {"api_key_ref": "DASHSCOPE_API_KEY", "api_key_ciphertext": ""},
        })

        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "direct-env-key"}, clear=False):
            self.assertEqual(media_model_config.load_stored_key(ctx, "tts", "cosyvoice"), "direct-env-key")

    def test_cosyvoice_sdk_wav_header_is_normalized(self) -> None:
        wav_bytes = (
            b"RIFF"
            + (0x7FFFFFBF).to_bytes(4, "little")
            + b"WAVEfmt "
            + (16).to_bytes(4, "little")
            + b"\x01\x00\x01\x00\xc0]\x00\x00\x80\xbb\x00\x00\x02\x00\x10\x00"
            + b"data"
            + (0x7FFFFF9B).to_bytes(4, "little")
            + (b"\x00\x00" * 10)
        )

        normalized = media_model_config.normalize_dashscope_cosyvoice_wav_bytes(wav_bytes)

        self.assertEqual(int.from_bytes(normalized[4:8], "little"), len(normalized) - 8)
        data_index = normalized.find(b"data")
        self.assertEqual(int.from_bytes(normalized[data_index + 4 : data_index + 8], "little"), 20)

    def test_cosyvoice_language_hints_are_not_hardcoded_to_chinese(self) -> None:
        self.assertEqual(media_model_config.dashscope_cosyvoice_language_hints("Chinese"), ["zh"])
        self.assertEqual(media_model_config.dashscope_cosyvoice_language_hints("en-US"), ["en"])
        self.assertEqual(media_model_config.dashscope_cosyvoice_language_hints("zh,en"), ["zh", "en"])

    def test_cosyvoice_empty_audio_includes_provider_response_detail(self) -> None:
        class FakeSpeechSynthesizer:
            def __init__(self, **_kwargs: object) -> None:
                self.response = {
                    "header": {
                        "task_id": "task-123",
                        "event": "task-failed",
                        "error_code": "InvalidParameter",
                        "error_message": "[cosyvoice]Engine return error code: 428",
                    },
                    "payload": {},
                }

            def call(self, _text: str, timeout_millis: int | None = None) -> bytes:
                return b""

            def get_response(self) -> dict[str, object]:
                return self.response

        fake_dashscope = ModuleType("dashscope")
        fake_dashscope.api_key = None
        fake_audio = ModuleType("dashscope.audio")
        fake_tts_v2 = ModuleType("dashscope.audio.tts_v2")
        fake_tts_v2.SpeechSynthesizer = FakeSpeechSynthesizer
        fake_speech_synthesizer = ModuleType("dashscope.audio.tts_v2.speech_synthesizer")
        fake_speech_synthesizer.AudioFormat = SimpleNamespace(WAV_24000HZ_MONO_16BIT="wav24")

        with patch.dict(
            sys.modules,
            {
                "dashscope": fake_dashscope,
                "dashscope.audio": fake_audio,
                "dashscope.audio.tts_v2": fake_tts_v2,
                "dashscope.audio.tts_v2.speech_synthesizer": fake_speech_synthesizer,
            },
        ):
            with self.assertRaises(RuntimeError) as raised:
                media_model_config.dashscope_cosyvoice_tts_audio_bytes("key", "model", "voice", "你好", "自然朗读", max_attempts=1)

        detail = str(raised.exception)
        self.assertIn("CosyVoice speech synthesis returned empty audio", detail)
        self.assertIn("event=task-failed", detail)
        self.assertIn("error_code=InvalidParameter", detail)
        self.assertIn("428", detail)
        self.assertIn("task_id=task-123", detail)

    def test_tts_generation_paths_use_shared_key_resolver(self) -> None:
        media_model_source = (REPO_ROOT / "ModelConfig" / "backend" / "opcrew_model_config" / "media_model_config.py").read_text(encoding="utf-8")
        router_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py").read_text(encoding="utf-8")
        media_tts_source = (
            REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "media_tts_provider_services.py"
        ).read_text(encoding="utf-8")
        rebuild_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "rebuild_router.py").read_text(encoding="utf-8")

        self.assertIn('load_stored_key(ctx, "tts", provider)', router_source)
        # media_tts resolves keys by config_kind (voice-clone vs tts) so cloned
        # voices read their voice-clone key; it still uses the shared resolver.
        self.assertIn("load_stored_key(sc.ctx, config_kind, requested_provider)", media_tts_source)
        self.assertIn("def dashscope_cosyvoice_tts_audio_bytes", media_model_source)
        self.assertIn("SpeechSynthesizer(", media_model_source)
        self.assertIn("max_attempts: int = 2", media_model_source)
        self.assertIn("safe_attempts = max(1, min(int(max_attempts or 1), 3))", media_model_source)
        self.assertIn("time.sleep(min(1.0, 0.25 * attempt))", media_model_source)
        self.assertNotIn("services/audio/tts/SpeechSynthesizer", media_model_source)
        self.assertIn("DASHSCOPE_TTS_SHARED_KEY_REFS", media_model_source)
        self.assertIn("stored_secret_or_env", media_model_source)
        self.assertIn("dashscope_cosyvoice_language_hints(language)", media_model_source)
        self.assertIn('if provider == "cosyvoice":', media_tts_source)
        self.assertIn("dashscope_cosyvoice_tts_audio_bytes", media_tts_source)
        self.assertIn('if provider == "cosyvoice":', rebuild_source)
        self.assertIn("dashscope_cosyvoice_tts_audio_bytes", rebuild_source)
        self.assertIn("cosyvoice_instruction_from_prompt(prompt)", rebuild_source)
        self.assertIn("should_retry_cosyvoice_without_instruction", rebuild_source)


if __name__ == "__main__":
    unittest.main()
