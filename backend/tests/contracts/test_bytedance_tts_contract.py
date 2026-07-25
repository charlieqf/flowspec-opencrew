from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "ModelConfig" / "backend"):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from opcrew_model_config.media_model_config import (  # noqa: E402
    bytedance_tts_request_payload,
    bytedance_tts_credentials,
    bytedance_tts_preview_url,
    byteplus_tts_request_payload,
    media_options,
    test_media_connection as media_connection_test,
)


def load_executor_module() -> Any:
    path = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py"
    spec = importlib.util.spec_from_file_location(f"analysis_v1_executor_bytedance_tts_contract_{id(path)}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ByteDanceTTSContractTest(unittest.TestCase):
    def test_media_options_expose_bytedance_tts_provider(self) -> None:
        providers = {item["provider"]: item for item in media_options("tts")}

        self.assertIn("bytedance", providers)
        provider = providers["bytedance"]
        self.assertEqual(provider["provider_label"], "ByteDance Volcano TTS")
        self.assertEqual(provider["default_extra_json"]["endpoint"], "https://openspeech.bytedance.com/api/v1/tts")
        self.assertEqual(provider["default_extra_json"]["byteplus_endpoint"], "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/unidirectional")
        self.assertEqual(provider["default_extra_json"]["byteplus_resource_id"], "seed-tts-2.0")
        self.assertEqual(provider["default_extra_json"]["byteplus_format"], "pcm")
        self.assertEqual(provider["default_extra_json"]["cluster"], "volcano_tts")
        self.assertEqual(provider["default_extra_json"]["encoding"], "wav")
        models = {item["model"]: item for item in provider["models"]}
        self.assertIn("seed-tts-1.1", models)
        voice_ids = {item["voice_id"] for item in models["seed-tts-1.1"]["voices"]}
        self.assertGreaterEqual(len(voice_ids), 100)
        self.assertIn("zh_male_M392_conversation_wvae_bigtts", voice_ids)
        self.assertIn("zh_female_tianmeitaozi_mars_bigtts", voice_ids)
        self.assertIn("zh_female_kefunvsheng_mars_bigtts", voice_ids)
        self.assertIn("zh_male_beijingxiaoye_emo_v2_mars_bigtts", voice_ids)
        self.assertIn("multi_female_sophie_conversation_wvae_bigtts", voice_ids)
        self.assertIn("custom_voice_id", voice_ids)

    def test_credentials_support_colon_json_and_extra_app_id(self) -> None:
        self.assertEqual(bytedance_tts_credentials("app-1:token-1"), {"app_id": "app-1", "access_token": "token-1"})
        self.assertEqual(
            bytedance_tts_credentials(json.dumps({"app_id": "app-2", "access_token": "token-2"})),
            {"app_id": "app-2", "access_token": "token-2"},
        )
        self.assertEqual(bytedance_tts_credentials("token-3", {"app_id": "app-3"}), {"app_id": "app-3", "access_token": "token-3"})
        self.assertEqual(
            bytedance_tts_credentials("123e4567-e89b-12d3-a456-426614174000"),
            {"auth_mode": "byteplus_api_key", "api_key": "123e4567-e89b-12d3-a456-426614174000"},
        )
        self.assertEqual(
            bytedance_tts_credentials(json.dumps({"api_key": "123e4567-e89b-12d3-a456-426614174000"})),
            {"auth_mode": "byteplus_api_key", "api_key": "123e4567-e89b-12d3-a456-426614174000"},
        )

        with self.assertRaisesRegex(RuntimeError, "app_id"):
            bytedance_tts_credentials("token-only")

    def test_connection_test_is_parse_only_and_non_paid(self) -> None:
        result = media_connection_test("tts", "bytedance", "seed-tts-1.1", "app-1:token-1")

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "Credentials saved")
        self.assertIn("paid synthesis", result["detail"])
        self.assertIn("App ID and Access Token", result["detail"])

        byteplus_result = media_connection_test("tts", "bytedance", "seed-tts-1.1", "123e4567-e89b-12d3-a456-426614174000")
        self.assertTrue(byteplus_result["ok"])
        self.assertEqual(byteplus_result["message"], "Credentials saved")
        self.assertIn("X-Api-Key", byteplus_result["detail"])

        legacy_result = media_connection_test("tts", "bytedance", "seed-tts-1.1", "token-from-secret", "direct", {"app_id": "app-from-extra"})
        self.assertTrue(legacy_result["ok"])
        self.assertEqual(legacy_result["message"], "Credentials saved")

    def test_payloads_pass_emotion_and_speed_controls(self) -> None:
        volcano_payload = bytedance_tts_request_payload(
            "seed-tts-1.1",
            "zh_female_cancan_mars_bigtts",
            "欢迎使用 OpenCrew。",
            {"app_id": "app-1", "access_token": "token-1"},
            {"speed_ratio": 1.35, "emotion": "excited", "emotion_scale": 5, "encoding": "wav"},
        )
        self.assertEqual(volcano_payload["audio"]["speed_ratio"], 1.35)
        self.assertTrue(volcano_payload["audio"]["enable_emotion"])
        self.assertEqual(volcano_payload["audio"]["emotion"], "excited")
        self.assertEqual(volcano_payload["audio"]["emotion_scale"], 5)

        byteplus_payload = byteplus_tts_request_payload(
            "seed-tts-1.1",
            "zh_female_cancan_mars_bigtts",
            "欢迎使用 OpenCrew。",
            {"speed_ratio": 1.2, "emotion": "happy", "emotion_scale": 4, "byteplus_format": "mp3"},
        )
        audio_params = byteplus_payload["req_params"]["audio_params"]
        self.assertEqual(audio_params["speed_ratio"], 1.2)
        self.assertTrue(audio_params["enable_emotion"])
        self.assertEqual(audio_params["emotion"], "happy")
        self.assertEqual(audio_params["emotion_scale"], 4)

    def test_preview_posts_volcano_payload_and_returns_data_url(self) -> None:
        import opcrew_model_config.media_model_config as module

        calls: dict[str, Any] = {}

        def fake_post_json(url: str, api_key: str, payload: dict[str, Any], extra_headers: dict[str, str] | None = None, timeout: int = 12, proxy_policy: str = "direct") -> dict[str, Any]:
            calls["url"] = url
            calls["api_key"] = api_key
            calls["payload"] = payload
            calls["headers"] = extra_headers or {}
            calls["timeout"] = timeout
            calls["proxy_policy"] = proxy_policy
            return {"status": 200, "body": {"code": 3000, "message": "Success", "data": base64.b64encode(b"wav-bytes").decode("ascii")}}

        old_post_json = module.post_json
        module.post_json = fake_post_json
        try:
            audio_url = bytedance_tts_preview_url(
                "app-1:token-1",
                "seed-tts-1.1",
                "zh_male_M392_conversation_wvae_bigtts",
                "欢迎使用 OpenCrew。",
                {"encoding": "wav", "sample_rate": 24000, "cluster": "volcano_tts", "endpoint": "https://openspeech.bytedance.com/api/v1/tts"},
                "direct",
            )
        finally:
            module.post_json = old_post_json

        self.assertEqual(audio_url, "data:audio/wav;base64,d2F2LWJ5dGVz")
        self.assertEqual(calls["url"], "https://openspeech.bytedance.com/api/v1/tts")
        self.assertEqual(calls["api_key"], "")
        self.assertEqual(calls["headers"]["Authorization"], "Bearer; token-1")
        payload = calls["payload"]
        self.assertEqual(payload["app"]["appid"], "app-1")
        self.assertEqual(payload["app"]["token"], "token-1")
        self.assertEqual(payload["app"]["cluster"], "volcano_tts")
        self.assertEqual(payload["audio"]["voice_type"], "zh_male_M392_conversation_wvae_bigtts")
        self.assertEqual(payload["audio"]["encoding"], "wav")
        self.assertEqual(payload["request"]["text"], "欢迎使用 OpenCrew。")
        self.assertEqual(payload["request"]["model"], "seed-tts-1.1")
        self.assertEqual(payload["request"]["operation"], "query")

    def test_preview_posts_byteplus_streaming_payload_and_returns_data_url(self) -> None:
        import opcrew_model_config.media_model_config as module

        calls: dict[str, Any] = {}

        def fake_stream_audio(endpoint: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 60, proxy_policy: str = "direct") -> bytes:
            calls["endpoint"] = endpoint
            calls["payload"] = payload
            calls["headers"] = headers
            calls["timeout"] = timeout
            calls["proxy_policy"] = proxy_policy
            return b"mp3-bytes"

        old_stream_audio = module.byteplus_tts_stream_audio
        module.byteplus_tts_stream_audio = fake_stream_audio
        try:
            audio_url = bytedance_tts_preview_url(
                "123e4567-e89b-12d3-a456-426614174000",
                "seed-tts-1.1",
                "zh_female_cancan_mars_bigtts",
                "欢迎使用 OpenCrew。",
                {"byteplus_format": "mp3", "sample_rate": 24000},
                "direct",
            )
        finally:
            module.byteplus_tts_stream_audio = old_stream_audio

        self.assertEqual(audio_url, "data:audio/mpeg;base64,bXAzLWJ5dGVz")
        self.assertEqual(calls["endpoint"], "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/unidirectional")
        self.assertEqual(calls["headers"]["X-Api-Key"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(calls["headers"]["X-Api-Resource-Id"], "seed-tts-2.0")
        self.assertEqual(calls["headers"]["X-Api-App-Key"], "aGjiRDfUWi")
        payload = calls["payload"]
        self.assertEqual(payload["user"]["uid"], "opencrew")
        self.assertEqual(payload["req_params"]["text"], "欢迎使用 OpenCrew。")
        self.assertEqual(payload["req_params"]["speaker"], "zh_female_cancan_mars_bigtts")
        self.assertEqual(payload["req_params"]["audio_params"]["format"], "mp3")
        self.assertEqual(payload["req_params"]["audio_params"]["sample_rate"], 24000)

    def test_analysis_v1_generates_bytedance_tts_audio_file(self) -> None:
        module = load_executor_module()
        calls: dict[str, Any] = {}

        def fake_post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
            calls["url"] = url
            calls["payload"] = payload
            calls["headers"] = headers
            calls["timeout"] = timeout
            return {"code": 3000, "message": "Success", "data": base64.b64encode(b"bytedance-audio").decode("ascii")}

        old_post = module.post_json_request
        module.post_json_request = fake_post_json_request
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "voice.wav"
                result = module.generate_tts_with_provider(
                    {
                        "provider": "bytedance",
                        "model": "seed-tts-1.1",
                        "api_key": "app-1:token-1",
                        "voice": "zh_male_M392_conversation_wvae_bigtts",
                        "endpoint": "https://openspeech.bytedance.com/api/v1/tts",
                        "cluster": "volcano_tts",
                        "encoding": "wav",
                    },
                    "欢迎使用 OpenCrew。",
                    output,
                    60,
                )
                self.assertEqual(output.read_bytes(), b"bytedance-audio")
        finally:
            module.post_json_request = old_post

        self.assertEqual(result["provider"], "bytedance")
        self.assertEqual(result["model"], "seed-tts-1.1")
        self.assertEqual(result["voice"], "zh_male_M392_conversation_wvae_bigtts")
        self.assertEqual(result["bytes"], len(b"bytedance-audio"))
        self.assertEqual(calls["headers"]["Authorization"], "Bearer; token-1")
        self.assertEqual(calls["payload"]["app"]["appid"], "app-1")
        self.assertEqual(calls["payload"]["request"]["operation"], "query")

    def test_analysis_v1_generates_byteplus_tts_audio_file(self) -> None:
        module = load_executor_module()
        calls: dict[str, Any] = {}

        def fake_stream(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> bytes:
            calls["url"] = url
            calls["payload"] = payload
            calls["headers"] = headers
            calls["timeout"] = timeout
            return b"byteplus-audio"

        old_stream = module.post_byteplus_tts_stream
        module.post_byteplus_tts_stream = fake_stream
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "voice.mp3"
                result = module.generate_tts_with_provider(
                    {
                        "provider": "bytedance",
                        "model": "seed-tts-1.1",
                        "api_key": "123e4567-e89b-12d3-a456-426614174000",
                        "voice": "zh_female_cancan_mars_bigtts",
                        "byteplus_format": "mp3",
                        "sample_rate": 24000,
                    },
                    "欢迎使用 OpenCrew。",
                    output,
                    60,
                )
                self.assertEqual(output.read_bytes(), b"byteplus-audio")
        finally:
            module.post_byteplus_tts_stream = old_stream

        self.assertEqual(result["provider"], "bytedance")
        self.assertEqual(result["model"], "seed-tts-1.1")
        self.assertEqual(result["voice"], "zh_female_cancan_mars_bigtts")
        self.assertEqual(result["bytes"], len(b"byteplus-audio"))
        self.assertEqual(calls["url"], "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/unidirectional")
        self.assertEqual(calls["headers"]["X-Api-Key"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(calls["headers"]["X-Api-Resource-Id"], "seed-tts-2.0")
        self.assertEqual(calls["payload"]["req_params"]["speaker"], "zh_female_cancan_mars_bigtts")
        self.assertEqual(calls["payload"]["req_params"]["audio_params"]["format"], "mp3")

    def test_analysis_v1_uses_selected_bytedance_voice(self) -> None:
        module = load_executor_module()

        voice = module.tts_voice_for_config(
            {"provider": "bytedance", "model": "seed-tts-1.1", "selected_voice_by_model": {"seed-tts-1.1": "zh_female_tianmeitaozi_mars_bigtts"}},
            {},
        )

        self.assertEqual(voice, "zh_female_tianmeitaozi_mars_bigtts")


if __name__ == "__main__":
    unittest.main()
