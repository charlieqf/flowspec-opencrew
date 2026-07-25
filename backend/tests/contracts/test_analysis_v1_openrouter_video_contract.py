from __future__ import annotations

import io
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "ModelConfig" / "backend"):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from opcrew_backend.routes.media_model_config import media_options, test_media_connection as media_connection_test  # noqa: E402


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1OpenRouterVideoContractTest(unittest.TestCase):
    def load_openrouter_module(self):
        return load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_openrouter.py",
            f"analysis_v1_video_openrouter_contract_{id(self)}",
        )

    def test_media_options_expose_openrouter_seedance_video(self) -> None:
        providers = {item["provider"]: item for item in media_options("video")}

        self.assertIn("openrouter", providers)
        openrouter = providers["openrouter"]
        self.assertEqual(openrouter["provider_label"], "OpenRouter")
        self.assertEqual(openrouter["default_extra_json"]["base_url"], "https://openrouter.ai/api/v1")
        self.assertTrue(openrouter["default_extra_json"]["send_frame_images"])
        self.assertEqual(openrouter["default_extra_json"]["r2_access_key_ref"], "public_assets_r2_access_key_id")
        self.assertEqual(openrouter["default_extra_json"]["r2_secret_access_key_ref"], "public_assets_r2_secret_access_key")
        models = {item["model"]: item for item in openrouter["models"]}
        self.assertIn("bytedance/seedance-2.0-fast", models)
        self.assertIn("text", models["bytedance/seedance-2.0-fast"]["input_modes"])
        self.assertIn("first_frame", models["bytedance/seedance-2.0-fast"]["input_modes"])
        self.assertTrue(models["bytedance/seedance-2.0-fast"]["reference_images"]["supported"])
        self.assertEqual(models["bytedance/seedance-2.0-fast"]["reference_images"]["min"], 0)
        self.assertEqual(models["bytedance/seedance-2.0-fast"]["reference_images"]["max"], 1)

    def test_text_capable_video_models_do_not_require_reference_images(self) -> None:
        failures: list[str] = []
        for provider in media_options("video"):
            for model in provider.get("models") or []:
                if "text" not in (model.get("input_modes") or []):
                    continue
                reference_images = model.get("reference_images") if isinstance(model.get("reference_images"), dict) else {}
                if reference_images.get("supported") and int(reference_images.get("min") or 0) > 0:
                    failures.append(f"{provider['provider']}/{model['model']}")

        self.assertEqual(failures, [])

    def test_xai_grok_imagine_15_requires_first_frame_image(self) -> None:
        providers = {item["provider"]: item for item in media_options("video")}
        xai = providers["xai"]
        models = {item["model"]: item for item in xai["models"]}
        model = models["grok-imagine-video-1.5-preview"]

        self.assertNotIn("text", model["input_modes"])
        self.assertEqual(model["input_modes"], ["first_frame"])
        self.assertEqual(model["reference_images"]["min"], 1)
        self.assertEqual(model["reference_images"]["max"], 1)

    def test_connection_test_does_not_submit_paid_video_generation(self) -> None:
        result = media_connection_test("video", "openrouter", "bytedance/seedance-2.0-fast", "openrouter-test-key")

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "API Key saved")
        self.assertIn("paid async task", result["detail"])

    def test_openrouter_reference_video_uses_runtime_r2_environment_without_persisting_secrets(self) -> None:
        module = self.load_openrouter_module()
        env_values = {
            "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT": "https://account.r2.cloudflarestorage.com",
            "OPENCREW_PUBLIC_ASSET_R2_BUCKET": "opencrew-public-assets",
            "OPENCREW_PUBLIC_ASSET_R2_REGION": "auto",
            "OPENCREW_PUBLIC_ASSET_R2_PREFIX": "analysis-v1/openrouter-video",
            "OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS": "600",
            "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID": "runtime-access-key",
            "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY": "runtime-secret-key",
        }
        previous_env = {key: os.environ.get(key) for key in env_values}
        os.environ.update(env_values)
        captured: dict[str, Any] = {}

        def fake_put(endpoint, bucket, object_key, body, content_type, access_key, secret_key, region):
            captured["put"] = {
                "endpoint": endpoint,
                "bucket": bucket,
                "object_key": object_key,
                "body": body,
                "content_type": content_type,
                "access_key": access_key,
                "secret_key": secret_key,
                "region": region,
            }

        def fake_presign(endpoint, bucket, object_key, access_key, secret_key, region="auto", expires=3600):
            captured["presign"] = {
                "endpoint": endpoint,
                "bucket": bucket,
                "object_key": object_key,
                "access_key": access_key,
                "secret_key": secret_key,
                "region": region,
                "expires": expires,
            }
            return "https://account.r2.cloudflarestorage.com/opencrew-public-assets/reference.mp4?X-Amz-Signature=redacted"

        old_put = module.r2_put_object
        old_presign = module.r2_presigned_get_url
        module.r2_put_object = fake_put
        module.r2_presigned_get_url = fake_presign
        try:
            resolved = module.apply_public_asset_runtime_config({
                "public_asset_provider": "",
                "extra": {"public_asset_provider": ""},
                "extra_json": {"public_asset_provider": ""},
            })
            self.assertEqual(resolved["public_asset_provider"], "r2")
            self.assertEqual(resolved["r2_bucket"], "opencrew-public-assets")
            self.assertEqual(resolved["public_asset_prefix"], "analysis-v1/openrouter-video")
            self.assertEqual(resolved["public_asset_ttl_seconds"], 600)
            self.assertEqual(resolved["public_asset_config_source"], "runtime_environment")
            self.assertEqual(resolved["extra"]["public_asset_provider"], "r2")
            self.assertNotIn("runtime-access-key", json.dumps(resolved))
            self.assertNotIn("runtime-secret-key", json.dumps(resolved))

            with tempfile.TemporaryDirectory() as tmp:
                video = Path(tmp) / "reference.mp4"
                video.write_bytes(b"video-reference")
                url = module.reference_asset_url(video, resolved, "video")

            self.assertIn("X-Amz-Signature=redacted", url)
            self.assertEqual(captured["put"]["access_key"], "runtime-access-key")
            self.assertEqual(captured["put"]["secret_key"], "runtime-secret-key")
            self.assertEqual(captured["put"]["bucket"], "opencrew-public-assets")
            self.assertEqual(captured["presign"]["expires"], 600)
        finally:
            module.r2_put_object = old_put
            module.r2_presigned_get_url = old_presign
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_explicit_non_r2_public_asset_provider_is_not_overridden_by_runtime_r2(self) -> None:
        module = self.load_openrouter_module()
        runtime_env = {
            "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT": "https://account.r2.cloudflarestorage.com",
            "OPENCREW_PUBLIC_ASSET_R2_BUCKET": "opencrew-public-assets",
        }

        with patch.dict(os.environ, runtime_env, clear=False):
            resolved = module.apply_public_asset_runtime_config({
                "public_asset_provider": "tmpfiles",
                "public_asset_prefix": "explicit-prefix",
            })

        self.assertEqual(resolved["public_asset_provider"], "tmpfiles")
        self.assertEqual(resolved["public_asset_prefix"], "explicit-prefix")
        self.assertNotIn("r2_bucket", resolved)

    def test_analysis_openrouter_loads_saved_r2_config_during_0502_and_0506_run(self) -> None:
        module = self.load_openrouter_module()
        r2_env_keys = {
            "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT",
            "OPENCREW_PUBLIC_ASSET_R2_BUCKET",
            "OPENCREW_PUBLIC_ASSET_R2_REGION",
            "OPENCREW_PUBLIC_ASSET_R2_PREFIX",
            "OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS",
            "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID",
            "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY",
        }
        control_keys = {
            "OPENCREW_DATA_DIR",
            "OPENCREW_PUBLIC_ASSETS_R2_ENV",
            "OPENCREW_PUBLIC_ASSET_R2_ENV",
            *r2_env_keys,
        }
        previous = {key: os.environ.get(key) for key in control_keys}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for key in control_keys:
                    os.environ.pop(key, None)
                os.environ["OPENCREW_DATA_DIR"] = tmp
                (Path(tmp) / "public_assets_r2.env").write_text(
                    "\n".join([
                        "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT=https://account.r2.cloudflarestorage.com",
                        "OPENCREW_PUBLIC_ASSET_R2_BUCKET=opencrew-public-assets",
                        "OPENCREW_PUBLIC_ASSET_R2_REGION=auto",
                        "OPENCREW_PUBLIC_ASSET_R2_PREFIX=analysis-v1/openrouter-video",
                        "OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS=600",
                        "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID=saved-access-key",
                        "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY=saved-secret-key",
                    ]),
                    encoding="utf-8",
                )

                resolved = module.apply_public_asset_runtime_config({
                    "public_asset_provider": "",
                    "r2_access_key_ref": "public_assets_r2_access_key_id",
                    "r2_secret_access_key_ref": "public_assets_r2_secret_access_key",
                })
                access_key = module.r2_secret(
                    resolved,
                    "r2_access_key_id",
                    "r2_access_key_ref",
                    "public_assets_r2_access_key_id",
                )
                secret_key = module.r2_secret(
                    resolved,
                    "r2_secret_access_key",
                    "r2_secret_access_key_ref",
                    "public_assets_r2_secret_access_key",
                )

            self.assertEqual(resolved["public_asset_provider"], "r2")
            self.assertEqual(resolved["r2_bucket"], "opencrew-public-assets")
            self.assertEqual(access_key, "saved-access-key")
            self.assertEqual(secret_key, "saved-secret-key")
            self.assertNotIn("saved-access-key", json.dumps(resolved))
            self.assertNotIn("saved-secret-key", json.dumps(resolved))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_openrouter_json_request_retries_transient_unavailable_once(self) -> None:
        module = self.load_openrouter_module()

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self) -> bytes:
                return b'{"id":"or_video_123","status":"pending"}'

        calls: list[dict[str, Any]] = []

        def fake_urlopen(request, timeout: int = 120):
            calls.append({
                "url": request.full_url,
                "payload": json.loads(request.data.decode("utf-8")),
                "headers": dict(request.header_items()),
                "timeout": timeout,
            })
            if len(calls) == 1:
                payload = json.dumps({"error": {"code": 503, "message": "The service is currently unavailable.", "status": "UNAVAILABLE"}}).encode("utf-8")
                raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, io.BytesIO(payload))
            return FakeResponse()

        sleeps: list[float] = []
        old_urlopen = module.urllib.request.urlopen
        old_sleep = module.time.sleep
        module.urllib.request.urlopen = fake_urlopen
        module.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            result = module.post_json_request("https://openrouter.ai/api/v1/videos", {"model": "bytedance/seedance-2.0-fast"}, {"Authorization": "Bearer openrouter-test-key"})
        finally:
            module.urllib.request.urlopen = old_urlopen
            module.time.sleep = old_sleep

        self.assertEqual(result, {"id": "or_video_123", "status": "pending"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["url"], calls[1]["url"])
        self.assertEqual(calls[0]["payload"]["model"], "bytedance/seedance-2.0-fast")
        self.assertEqual(sleeps, [10.0])

    def test_openrouter_post_retries_http_408_timeout(self) -> None:
        module = self.load_openrouter_module()

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self) -> bytes:
                return b'{"id":"or_video_after_retry","status":"pending"}'

        calls: list[str] = []

        def fake_urlopen(request, timeout: int = 120):
            calls.append(request.full_url)
            if len(calls) < 3:
                payload = json.dumps({"error": {"code": 408, "message": "Timed out. Please try again."}}).encode("utf-8")
                raise urllib.error.HTTPError(request.full_url, 408, "Request Timeout", {}, io.BytesIO(payload))
            return FakeResponse()

        sleeps: list[float] = []
        old_urlopen = module.urllib.request.urlopen
        old_sleep = module.time.sleep
        module.urllib.request.urlopen = fake_urlopen
        module.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            result = module.post_json_request("https://openrouter.ai/api/v1/videos", {"model": "bytedance/seedance-2.0"}, {"Authorization": "Bearer openrouter-test-key"})
        finally:
            module.urllib.request.urlopen = old_urlopen
            module.time.sleep = old_sleep

        self.assertEqual(result, {"id": "or_video_after_retry", "status": "pending"})
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [10.0, 20.0])

    def test_video_plan_dispatches_openrouter_provider(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            f"analysis_v1_05_02_openrouter_dispatch_contract_{id(self)}",
        )

        module = executor.video_module_for("openrouter", "bytedance/seedance-2.0-fast")

        self.assertEqual(module.TEMPLATE_NAME, "Ref_05_02_Video_OpenRouter.md")

    def test_sdr2v_chinese_prompt_reserves_dialogue_budget(self) -> None:
        module = self.load_openrouter_module()
        dialogue = "大家好，今天我们聊聊自然表情。"

        package = module.build_prompt_package({
            "segment": {
                "segment_id": "segment_0001",
                "planned_video_duration": 5,
                "prompt_template": "Video_SDR2V.md",
                "dialogue_asset_keys": ["dialogue_0001"],
            },
            "dialogue_index": {
                "dialogue_0001": {"dialogue": {"dialogue": dialogue}},
            },
        })

        budget = package["extracted_fields"]["prompt_budget"]
        self.assertEqual(module.SDR2V_PROMPT_MAX_CHARS, 1000)
        self.assertEqual(module.SDR2V_FIXED_PROMPT_MAX_CHARS, 700)
        self.assertEqual(module.SDR2V_DIALOGUE_MIN_RESERVED_CHARS, 300)
        self.assertLessEqual(len(package["prompt"]), 1000)
        self.assertLessEqual(budget["fixed_prompt_chars"], 700)
        self.assertGreaterEqual(budget["dialogue_budget_chars"], 300)
        self.assertEqual(budget["dialogue_chars"], len(dialogue))
        self.assertIn("只用自然普通话说", package["prompt"])
        self.assertIn("负向提示", package["prompt"])
        self.assertNotIn("Negative prompt", package["prompt"])

    def test_sdr2v_prompt_budget_rejects_overlong_dialogue_without_truncation(self) -> None:
        module = self.load_openrouter_module()
        dialogue = "长" * (module.SDR2V_PROMPT_MAX_CHARS + 1)

        with self.assertRaisesRegex(module.ToolError, "不会自动截断"):
            module.build_prompt_package({
                "segment": {
                    "segment_id": "segment_0001",
                    "planned_video_duration": 5,
                    "prompt_template": "Video_SDR2V.md",
                    "dialogue_asset_keys": ["dialogue_0001"],
                },
                "dialogue_index": {
                    "dialogue_0001": {"dialogue": {"dialogue": dialogue}},
                },
            })

    def test_openrouter_generate_submits_polls_and_uses_safe_download(self) -> None:
        module = self.load_openrouter_module()
        calls: dict[str, Any] = {"post": None, "gets": [], "downloads": []}

        def fake_post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
            calls["post"] = {"url": url, "payload": payload, "headers": headers, "timeout": timeout}
            return {"id": "or_video_123", "status": "pending", "polling_url": "https://openrouter.ai/api/v1/videos/or_video_123?token=poll-token"}

        poll_payloads = [
            {"id": "or_video_123", "status": "running"},
            {"id": "or_video_123", "status": "completed"},
            {"id": "or_video_123", "status": "completed", "output_url": "https://cdn.openrouter.example/out.mp4?sig=secret"},
        ]

        def fake_get(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
            calls["gets"].append({"url": url, "headers": headers, "timeout": timeout})
            return poll_payloads.pop(0)

        def fake_download(url: str, output_path: Path, **kwargs: Any) -> Any:
            calls["downloads"].append({"url": url, "output_path": output_path, "kwargs": kwargs})
            if url.endswith("/content"):
                raise module.ToolError("content endpoint not ready")
            output_path.write_bytes(b"openrouter-video")
            return None

        old_post = module.post_json_request
        old_get = module.get_json_request
        old_sleep = module.time.sleep
        old_download = module.safe_download_to_path
        module.post_json_request = fake_post
        module.get_json_request = fake_get
        module.time.sleep = lambda _seconds: None
        module.safe_download_to_path = fake_download
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prompt_path = root / "prompt.json"
                image_path = root / "first_frame.png"
                output_path = root / "out.mp4"
                state_path = root / "openrouter_task_state.json"
                prompt_path.write_text(json.dumps({"prompt": "A product shot with gentle camera motion."}), encoding="utf-8")
                image_path.write_bytes(b"png-bytes")

                result = module.generate(
                    {
                        "config": {"provider": "openrouter", "model": "bytedance/seedance-2.0-fast", "api_key": "openrouter-test-key"},
                        "reference_images": [str(image_path)],
                        "duration_seconds": 5,
                        "timeout_seconds": 60,
                        "provider_task_state_path": str(state_path),
                    },
                    prompt_path,
                    output_path,
                )

                self.assertEqual(output_path.read_bytes(), b"openrouter-video")
                self.assertEqual(result["provider"], "openrouter")
                self.assertEqual(result["provider_task_id"], "or_video_123")
                self.assertEqual(result["duration"], 5)
                self.assertTrue(result["send_frame_images"])
                self.assertEqual(calls["post"]["url"], "https://openrouter.ai/api/v1/videos")
                self.assertEqual(calls["post"]["headers"]["Authorization"], "Bearer openrouter-test-key")
                self.assertEqual(calls["post"]["headers"]["X-Title"], "OpenCrew")
                payload = calls["post"]["payload"]
                self.assertEqual(payload["model"], "bytedance/seedance-2.0-fast")
                self.assertEqual(payload["aspect_ratio"], "9:16")
                self.assertEqual(payload["resolution"], "720p")
                self.assertEqual(len(payload["frame_images"]), 1)
                self.assertEqual(payload["frame_images"][0]["frame_type"], "first_frame")
                self.assertTrue(payload["frame_images"][0]["image_url"]["url"].startswith("data:image/png;base64,"))
                self.assertEqual(calls["gets"][0]["url"], "https://openrouter.ai/api/v1/videos/or_video_123?token=poll-token")
                self.assertEqual(len(calls["gets"]), 3)
                self.assertEqual(calls["downloads"][0]["url"], "https://openrouter.ai/api/v1/videos/or_video_123/content")
                self.assertIn("Authorization", calls["downloads"][0]["kwargs"]["headers"])
                self.assertEqual(calls["downloads"][1]["url"], "https://cdn.openrouter.example/out.mp4?sig=secret")
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["provider_task_id"], "or_video_123")
                self.assertEqual(state["status"], "completed")
                self.assertEqual(state["error"], "")
                self.assertEqual(state["content_download_error"], "")
                self.assertEqual(state["video_url_summary"], "https://cdn.openrouter.example/out.mp4")
                self.assertEqual(state["polling_url"], "https://openrouter.ai/api/v1/videos/or_video_123")
                self.assertNotIn("polling_url_full", state)
                private_state = json.loads(module.private_task_state_path(state_path).read_text(encoding="utf-8"))
                self.assertEqual(private_state["polling_url_full"], "https://openrouter.ai/api/v1/videos/or_video_123?token=poll-token")
                self.assertNotIn("poll-token", json.dumps(state))
                self.assertNotIn("secret", json.dumps(state))
        finally:
            module.post_json_request = old_post
            module.get_json_request = old_get
            module.time.sleep = old_sleep
            module.safe_download_to_path = old_download

    def test_openrouter_payload_sends_only_first_reference_as_first_frame(self) -> None:
        module = self.load_openrouter_module()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.png"
            second = root / "second.png"
            first.write_bytes(b"first-image")
            second.write_bytes(b"second-image")

            payload = module.openrouter_request_payload(
                "Use the selected product frame.",
                "bytedance/seedance-2.0-fast",
                [first, second],
                5,
                {},
            )

            self.assertEqual(len(payload["frame_images"]), 1)
            self.assertEqual(payload["frame_images"][0]["frame_type"], "first_frame")
            self.assertEqual(payload["frame_images"][0]["image_url"]["url"], "data:image/png;base64,Zmlyc3QtaW1hZ2U=")

    def test_openrouter_payload_sends_sr2_multimodal_input_references(self) -> None:
        module = self.load_openrouter_module()
        tmpfiles_calls: list[dict[str, Any]] = []

        def fake_publish_tmpfiles(path: Path, config: dict[str, Any], purpose: str) -> dict[str, Any]:
            tmpfiles_calls.append({"path": path, "config": config, "purpose": purpose})
            return {
                "provider": "tmpfiles",
                "public_url": f"https://tmpfiles.org/dl/opencrew/{path.name}",
            }

        old_publish_tmpfiles = module.publish_tmpfiles_asset
        module.publish_tmpfiles_asset = fake_publish_tmpfiles

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                image = root / "reference.png"
                audio = root / "voice.mp3"
                video = root / "motion.mp4"
                image.write_bytes(b"image-reference")
                audio.write_bytes(b"audio-reference")
                video.write_bytes(b"video-reference")

                payload = module.openrouter_request_payload(
                    "Use the selected multimodal references.",
                    "bytedance/seedance-2.0",
                    [image],
                    5,
                    {"reference_mode": "input_references"},
                    [audio],
                    [video],
                )

                self.assertNotIn("frame_images", payload)
                self.assertTrue(payload["generate_audio"])
                self.assertEqual([item["type"] for item in payload["input_references"]], ["image_url", "audio_url", "video_url"])
                self.assertTrue(payload["input_references"][0]["image_url"]["url"].startswith("data:image/png;base64,"))
                self.assertTrue(payload["input_references"][1]["audio_url"]["url"].startswith("data:audio/mpeg;base64,"))
                self.assertEqual(payload["input_references"][2]["video_url"]["url"], "https://tmpfiles.org/dl/opencrew/motion.mp4")
                self.assertEqual(tmpfiles_calls, [{"path": video, "config": {"reference_mode": "input_references"}, "purpose": "openrouter_video_reference"}])
        finally:
            module.publish_tmpfiles_asset = old_publish_tmpfiles

    def test_openrouter_payload_publishes_first_frame_to_r2_when_configured(self) -> None:
        module = self.load_openrouter_module()
        calls: dict[str, Any] = {"put": None}

        def fake_put(url: str, body: bytes, headers: dict[str, str], timeout: int = 120) -> int:
            calls["put"] = {"url": url, "body": body, "headers": headers, "timeout": timeout}
            return 200

        old_put = module.put_binary_request
        module.put_binary_request = fake_put
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                first = root / "first.png"
                first.write_bytes(b"first-image")

                payload = module.openrouter_request_payload(
                    "Use the selected product frame.",
                    "bytedance/seedance-2.0-fast",
                    [first],
                    5,
                    {
                        "public_asset_provider": "r2",
                        "r2_endpoint": "https://account.r2.cloudflarestorage.com",
                        "r2_bucket": "opencrew-public-assets",
                        "r2_region": "auto",
                        "r2_access_key_id": "test-access-key",
                        "r2_secret_access_key": "test-secret-key",
                        "public_asset_ttl_seconds": 600,
                    },
                )

                self.assertEqual(calls["put"]["body"], b"first-image")
                self.assertTrue(calls["put"]["url"].startswith("https://account.r2.cloudflarestorage.com/opencrew-public-assets/tmp/openrouter-frames/"))
                self.assertIn("Authorization", calls["put"]["headers"])
                url = payload["frame_images"][0]["image_url"]["url"]
                parsed = urllib.parse.urlsplit(url)
                query = urllib.parse.parse_qs(parsed.query)
                self.assertEqual(parsed.scheme, "https")
                self.assertEqual(parsed.netloc, "account.r2.cloudflarestorage.com")
                self.assertTrue(parsed.path.startswith("/opencrew-public-assets/tmp/openrouter-frames/"))
                self.assertEqual(query["X-Amz-Algorithm"], ["AWS4-HMAC-SHA256"])
                self.assertEqual(query["X-Amz-Expires"], ["600"])
                self.assertIn("X-Amz-Signature", query)
                self.assertNotIn("test-secret-key", url)
        finally:
            module.put_binary_request = old_put

    def test_openrouter_redacts_presigned_r2_url_secrets(self) -> None:
        module = self.load_openrouter_module()

        redacted = module.redact_secret_text(
            "https://account.r2.cloudflarestorage.com/bucket/key"
            "?X-Amz-Credential=access/20260615/auto/s3/aws4_request"
            "&X-Amz-Signature=abcdef"
            "&X-Amz-Security-Token=session-token"
        )

        self.assertIn("X-Amz-Credential=***", redacted)
        self.assertIn("X-Amz-Signature=***", redacted)
        self.assertIn("X-Amz-Security-Token=***", redacted)

    def test_openrouter_rejects_cross_host_polling_url(self) -> None:
        module = self.load_openrouter_module()

        with self.assertRaisesRegex(module.ToolError, "polling URL host"):
            module.polling_url_from_response({"polling_url": "https://evil.example/videos/1"}, "https://openrouter.ai/api/v1", "or_video_123")

    def test_openrouter_polling_url_fallback_keeps_api_prefix(self) -> None:
        module = self.load_openrouter_module()

        url = module.polling_url_from_response({}, "https://openrouter.ai/api/v1", "or_video_123")

        self.assertEqual(url, "https://openrouter.ai/api/v1/videos/or_video_123")

    def test_openrouter_completed_without_url_can_download_content_endpoint(self) -> None:
        module = self.load_openrouter_module()
        calls: dict[str, Any] = {"downloads": []}

        old_post = module.post_json_request
        old_get = module.get_json_request
        old_sleep = module.time.sleep
        old_download = module.safe_download_to_path
        module.post_json_request = lambda *_args, **_kwargs: {"id": "or_video_content", "status": "pending"}
        module.get_json_request = lambda *_args, **_kwargs: {"id": "or_video_content", "status": "completed"}
        module.time.sleep = lambda _seconds: None

        def fake_download(url: str, output_path: Path, **kwargs: Any) -> Any:
            calls["downloads"].append({"url": url, "kwargs": kwargs})
            output_path.write_bytes(b"content-endpoint-video")
            return None

        module.safe_download_to_path = fake_download
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prompt_path = root / "prompt.json"
                output_path = root / "out.mp4"
                prompt_path.write_text(json.dumps({"prompt": "A product shot with gentle camera motion."}), encoding="utf-8")

                result = module.generate(
                    {
                        "config": {"provider": "openrouter", "model": "bytedance/seedance-2.0-fast", "api_key": "openrouter-test-key"},
                        "reference_images": [],
                        "duration_seconds": 5,
                        "timeout_seconds": 60,
                    },
                    prompt_path,
                    output_path,
                )

                self.assertEqual(result["provider_task_id"], "or_video_content")
                self.assertEqual(output_path.read_bytes(), b"content-endpoint-video")
                self.assertEqual(calls["downloads"][0]["url"], "https://openrouter.ai/api/v1/videos/or_video_content/content")
                self.assertEqual(calls["downloads"][0]["kwargs"]["headers"]["Authorization"], "Bearer openrouter-test-key")
        finally:
            module.post_json_request = old_post
            module.get_json_request = old_get
            module.time.sleep = old_sleep
            module.safe_download_to_path = old_download

    def test_openrouter_resume_reuses_existing_task_id_without_resubmitting(self) -> None:
        module = self.load_openrouter_module()
        calls: dict[str, Any] = {"post_count": 0, "get_count": 0}

        def fake_post(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["post_count"] += 1
            raise AssertionError("resume should not submit a new OpenRouter video task")

        def fake_get(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["get_count"] += 1
            return {"id": "or_video_resume", "status": "completed", "output_url": "https://cdn.openrouter.example/resumed.mp4?sig=secret"}

        old_post = module.post_json_request
        old_get = module.get_json_request
        old_download = module.safe_download_to_path
        module.post_json_request = fake_post
        module.get_json_request = fake_get
        module.safe_download_to_path = lambda _url, output_path, **_kwargs: Path(output_path).write_bytes(b"resumed-video")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prompt_path = root / "prompt.json"
                output_path = root / "out.mp4"
                state_path = root / "openrouter_task_state.json"
                prompt_text = "A product shot with gentle camera motion."
                prompt_path.write_text(json.dumps({"prompt": prompt_text}), encoding="utf-8")
                payload = module.openrouter_request_payload(prompt_text, "bytedance/seedance-2.0-fast", [], 5, {})
                fingerprint = module.request_fingerprint(prompt_text, "bytedance/seedance-2.0-fast", [], 5, payload)
                state_path.write_text(json.dumps({
                    "provider": "openrouter",
                    "model": "bytedance/seedance-2.0-fast",
                    "provider_task_id": "or_video_resume",
                    "polling_url": "https://openrouter.ai/api/v1/videos/or_video_resume",
                    "fingerprint": fingerprint,
                    "status": "running",
                }), encoding="utf-8")

                result = module.generate(
                    {
                        "config": {"provider": "openrouter", "model": "bytedance/seedance-2.0-fast", "api_key": "openrouter-test-key"},
                        "reference_images": [],
                        "duration_seconds": 5,
                        "timeout_seconds": 60,
                        "provider_task_state_path": str(state_path),
                    },
                    prompt_path,
                    output_path,
                )

                self.assertEqual(calls["post_count"], 0)
                self.assertEqual(calls["get_count"], 1)
                self.assertEqual(result["provider_task_id"], "or_video_resume")
                self.assertEqual(output_path.read_bytes(), b"resumed-video")
        finally:
            module.post_json_request = old_post
            module.get_json_request = old_get
            module.safe_download_to_path = old_download

    def test_openrouter_resume_prefers_full_polling_url_with_query(self) -> None:
        module = self.load_openrouter_module()
        calls: dict[str, Any] = {"poll_urls": []}

        def fake_get(url: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["poll_urls"].append(url)
            return {"id": "or_video_resume", "status": "completed", "output_url": "https://cdn.openrouter.example/resumed.mp4"}

        def fake_post(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("resume should not submit a new OpenRouter video task")

        old_post = module.post_json_request
        old_get = module.get_json_request
        old_download = module.safe_download_to_path
        module.post_json_request = fake_post
        module.get_json_request = fake_get
        module.safe_download_to_path = lambda _url, output_path, **_kwargs: Path(output_path).write_bytes(b"resumed-video")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prompt_path = root / "prompt.json"
                output_path = root / "out.mp4"
                state_path = root / "openrouter_task_state.json"
                prompt_text = "A product shot with gentle camera motion."
                prompt_path.write_text(json.dumps({"prompt": prompt_text}), encoding="utf-8")
                payload = module.openrouter_request_payload(prompt_text, "bytedance/seedance-2.0-fast", [], 5, {})
                fingerprint = module.request_fingerprint(prompt_text, "bytedance/seedance-2.0-fast", [], 5, payload)
                state_path.write_text(json.dumps({
                    "provider": "openrouter",
                    "model": "bytedance/seedance-2.0-fast",
                    "provider_task_id": "or_video_resume",
                    "polling_url": "https://openrouter.ai/api/v1/videos/or_video_resume?old_public_token=legacy",
                    "fingerprint": fingerprint,
                    "status": "running",
                }), encoding="utf-8")
                private_path = module.private_task_state_path(state_path)
                private_path.parent.mkdir(parents=True, exist_ok=True)
                private_path.write_text(json.dumps({
                    "polling_url_full": "https://openrouter.ai/api/v1/videos/or_video_resume?token=poll-token",
                }), encoding="utf-8")

                result = module.generate(
                    {
                        "config": {"provider": "openrouter", "model": "bytedance/seedance-2.0-fast", "api_key": "openrouter-test-key"},
                        "reference_images": [],
                        "duration_seconds": 5,
                        "timeout_seconds": 60,
                        "provider_task_state_path": str(state_path),
                    },
                    prompt_path,
                    output_path,
                )

                self.assertEqual(result["provider_task_id"], "or_video_resume")
                self.assertEqual(calls["poll_urls"], ["https://openrouter.ai/api/v1/videos/or_video_resume?token=poll-token"])
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["polling_url"], "https://openrouter.ai/api/v1/videos/or_video_resume")
                self.assertNotIn("polling_url_full", state)
                self.assertNotIn("poll-token", json.dumps(state))
                self.assertNotIn("old_public_token", json.dumps(state))
        finally:
            module.post_json_request = old_post
            module.get_json_request = old_get
            module.safe_download_to_path = old_download


if __name__ == "__main__":
    unittest.main()
