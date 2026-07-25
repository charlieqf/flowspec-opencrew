from __future__ import annotations

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

from opcrew_backend.routes.media_model_config import media_options, test_media_connection as media_connection_test  # noqa: E402


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1SeedanceVideoContractTest(unittest.TestCase):
    def load_seedance_module(self):
        return load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_seedance.py",
            f"analysis_v1_video_seedance_contract_{id(self)}",
        )

    def test_media_options_expose_bytedance_seedance_video(self) -> None:
        providers = {item["provider"]: item for item in media_options("video")}

        self.assertIn("bytedance", providers)
        bytedance = providers["bytedance"]
        self.assertEqual(bytedance["provider_label"], "ByteDance Volcano Ark")
        models = {item["model"]: item for item in bytedance["models"]}
        model = models["doubao-seedance-2-0-fast-260128"]
        self.assertIn("text", model["input_modes"])
        self.assertIn("first_frame", model["input_modes"])
        self.assertTrue(model.get("audio_input", {}).get("recommended", False))
        self.assertEqual(bytedance["default_extra_json"]["base_url"], "https://ark.cn-beijing.volces.com/api/v3")
        self.assertEqual(bytedance["default_extra_json"]["default_resolution"], "720p")
        self.assertTrue(bytedance["default_extra_json"]["generate_audio"])

    def test_media_options_keep_non_omni_video_audio_enabled(self) -> None:
        providers = {item["provider"]: item for item in media_options("video")}

        kling = providers["kling"]
        self.assertEqual(kling["default_extra_json"]["sound"], "on")
        kling_models = {item["model"]: item for item in kling["models"]}
        self.assertTrue(kling_models["kling-3.0-turbo"]["audio_input"]["recommended"])
        self.assertFalse(kling_models["kling-v3-omni"]["audio_input"]["recommended"])

        chanjing_models = {item["model"]: item for item in providers["chanjing"]["models"]}
        self.assertTrue(chanjing_models["kling2.5"]["audio_input"]["recommended"])
        self.assertTrue(chanjing_models["kling-v2-1-master"]["audio_input"]["recommended"])
        self.assertTrue(chanjing_models["kling1.6"]["audio_input"]["recommended"])
        self.assertIn("viduq1", chanjing_models)
        self.assertIn("MiniMax-Hailuo-02", chanjing_models)
        self.assertIn("Doubao-Seedance-1.0-pro", chanjing_models)
        self.assertIn("happyhorse-1.0-i2v", chanjing_models)

    def test_connection_test_does_not_submit_paid_video_generation(self) -> None:
        result = media_connection_test("video", "bytedance", "doubao-seedance-2-0-fast-260128", "ark-test-key")

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "API Key saved")
        self.assertIn("paid async task", result["detail"])

    def test_video_plan_dispatches_bytedance_to_seedance_module(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            f"analysis_v1_05_02_seedance_dispatch_contract_{id(self)}",
        )

        module = executor.video_module_for("bytedance", "doubao-seedance-2-0-fast-260128")

        self.assertEqual(module.TEMPLATE_NAME, "Ref_05_02_Video_Seedance.md")

    def test_seedance_generate_submits_polls_and_uses_safe_download(self) -> None:
        module = self.load_seedance_module()
        calls: dict[str, Any] = {"post": None, "gets": [], "downloads": []}

        def fake_post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
            calls["post"] = {"url": url, "payload": payload, "headers": headers, "timeout": timeout}
            return {"id": "seedance_task_123", "status": "queued"}

        poll_payloads = [
            {"id": "seedance_task_123", "status": "running"},
            {"id": "seedance_task_123", "status": "succeeded", "content": [{"type": "video_url", "video_url": "https://cdn.volc.example/out.mp4?sig=secret"}]},
        ]

        def fake_get(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
            calls["gets"].append({"url": url, "headers": headers, "timeout": timeout})
            return poll_payloads.pop(0)

        def fake_download(url: str, output_path: Path, **kwargs: Any) -> Any:
            calls["downloads"].append({"url": url, "output_path": output_path, "kwargs": kwargs})
            output_path.write_bytes(b"seedance-video")
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
                state_path = root / "seedance_task_state.json"
                prompt_path.write_text(json.dumps({"prompt": "A product shot with gentle camera motion."}), encoding="utf-8")
                image_path.write_bytes(b"png-bytes")

                result = module.generate(
                    {
                        "config": {"provider": "bytedance", "model": "doubao-seedance-2-0-fast-260128", "api_key": "ark-test-key"},
                        "reference_images": [str(image_path)],
                        "duration_seconds": 5,
                        "timeout_seconds": 60,
                        "provider_task_state_path": str(state_path),
                    },
                    prompt_path,
                    output_path,
                )

                self.assertEqual(output_path.read_bytes(), b"seedance-video")
                self.assertEqual(result["provider"], "bytedance")
                self.assertEqual(result["provider_task_id"], "seedance_task_123")
                self.assertEqual(result["duration"], 5)
                self.assertTrue(result["generate_audio"])
                self.assertEqual(calls["post"]["url"], "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks")
                self.assertEqual(calls["post"]["headers"]["Authorization"], "Bearer ark-test-key")
                payload = calls["post"]["payload"]
                self.assertEqual(payload["model"], "doubao-seedance-2-0-fast-260128")
                self.assertEqual(payload["ratio"], "9:16")
                self.assertEqual(payload["resolution"], "720p")
                self.assertTrue(payload["generate_audio"])
                self.assertEqual(payload["content"][0]["type"], "text")
                self.assertEqual(payload["content"][1]["type"], "image_url")
                self.assertTrue(payload["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
                self.assertEqual(calls["downloads"][0]["url"], "https://cdn.volc.example/out.mp4?sig=secret")
                self.assertIn("video/*", calls["downloads"][0]["kwargs"]["allowed_content_types"])
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["provider_task_id"], "seedance_task_123")
                self.assertEqual(state["status"], "succeeded")
                self.assertEqual(state["video_url_summary"], "https://cdn.volc.example/out.mp4")
                self.assertNotIn("secret", json.dumps(state))
        finally:
            module.post_json_request = old_post
            module.get_json_request = old_get
            module.time.sleep = old_sleep
            module.safe_download_to_path = old_download

    def test_seedance_resume_reuses_existing_task_id_without_resubmitting(self) -> None:
        module = self.load_seedance_module()
        calls: dict[str, Any] = {"post_count": 0, "get_count": 0}

        def fake_post(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["post_count"] += 1
            raise AssertionError("resume should not submit a new Seedance task")

        def fake_get(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["get_count"] += 1
            return {"id": "seedance_task_resume", "status": "succeeded", "content": [{"video_url": "https://cdn.volc.example/resumed.mp4?sig=secret"}]}

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
                image_path = root / "first_frame.png"
                output_path = root / "out.mp4"
                state_path = root / "seedance_task_state.json"
                prompt_path.write_text(json.dumps({"prompt": "A product shot with gentle camera motion."}), encoding="utf-8")
                image_path.write_bytes(b"png-bytes")
                payload = module.seedance_request_payload(
                    "A product shot with gentle camera motion.",
                    "doubao-seedance-2-0-fast-260128",
                    [image_path],
                    5,
                    {"provider": "bytedance", "model": "doubao-seedance-2-0-fast-260128"},
                )
                fingerprint = module.request_fingerprint("A product shot with gentle camera motion.", "doubao-seedance-2-0-fast-260128", [image_path], 5, payload)
                state_path.write_text(json.dumps({
                    "provider": "bytedance",
                    "model": "doubao-seedance-2-0-fast-260128",
                    "provider_task_id": "seedance_task_resume",
                    "fingerprint": fingerprint,
                    "status": "running",
                }), encoding="utf-8")

                result = module.generate(
                    {
                        "config": {"provider": "bytedance", "model": "doubao-seedance-2-0-fast-260128", "api_key": "ark-test-key"},
                        "reference_images": [str(image_path)],
                        "duration_seconds": 5,
                        "timeout_seconds": 60,
                        "provider_task_state_path": str(state_path),
                    },
                    prompt_path,
                    output_path,
                )

                self.assertEqual(calls["post_count"], 0)
                self.assertEqual(calls["get_count"], 1)
                self.assertEqual(result["provider_task_id"], "seedance_task_resume")
                self.assertEqual(output_path.read_bytes(), b"resumed-video")
        finally:
            module.post_json_request = old_post
            module.get_json_request = old_get
            module.safe_download_to_path = old_download

    def test_seedance_failed_task_raises_tool_error_without_download(self) -> None:
        module = self.load_seedance_module()
        downloads: list[str] = []

        old_post = module.post_json_request
        old_get = module.get_json_request
        old_download = module.safe_download_to_path
        module.post_json_request = lambda *_args, **_kwargs: {"id": "seedance_task_failed"}
        module.get_json_request = lambda *_args, **_kwargs: {"id": "seedance_task_failed", "status": "failed", "error": {"message": "bad prompt"}}
        module.safe_download_to_path = lambda url, *_args, **_kwargs: downloads.append(str(url))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prompt_path = root / "prompt.json"
                output_path = root / "out.mp4"
                prompt_path.write_text(json.dumps({"prompt": "bad"}), encoding="utf-8")

                with self.assertRaisesRegex(module.ToolError, "Seedance video generation failed"):
                    module.generate(
                        {
                            "config": {"provider": "bytedance", "model": "doubao-seedance-2-0-fast-260128", "api_key": "ark-test-key"},
                            "reference_images": [],
                            "duration_seconds": 5,
                            "timeout_seconds": 60,
                        },
                        prompt_path,
                        output_path,
                    )

                self.assertEqual(downloads, [])
                self.assertFalse(output_path.exists())
        finally:
            module.post_json_request = old_post
            module.get_json_request = old_get
            module.safe_download_to_path = old_download

    def test_video_provider_config_loads_database_extra_json(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            f"analysis_v1_05_02_seedance_config_contract_{id(self)}",
        )

        class FakeCursor:
            def __init__(self, conn):
                self.conn = conn

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params):
                self.conn.queries.append((query, params))
                self.conn.next_row = (
                    "bytedance",
                    "doubao-seedance-2-0-fast-260128",
                    "video_bytedance_key",
                    "",
                    json.dumps({"base_url": "https://ark.cn-beijing.volces.com/api/v3", "default_resolution": "720p", "generate_audio": False}),
                )

            def fetchone(self):
                return self.conn.next_row

        class FakeConn:
            def __init__(self):
                self.queries = []
                self.next_row = None

            def cursor(self):
                return FakeCursor(self)

            def close(self):
                pass

        fake_conn = FakeConn()
        old_connect = executor.postgres_connect
        old_secret = executor.resolve_secret_value
        executor.postgres_connect = lambda _database_url: fake_conn
        executor.resolve_secret_value = lambda api_key_ref, legacy_value="": "stored-ark-key"
        try:
            args = executor.Args(
                workspace="",
                database_url="postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew",
                max_segments=0,
                force=False,
                execute_audio=True,
                execute_image=True,
                execute_video=True,
                execute_lipsync=True,
                image_provider="",
                image_model="",
                video_provider="",
                video_model="",
                lipsync_provider="",
                lipsync_model="",
                tts_provider="",
                tts_model="",
                provider_timeout_seconds=1800,
            )
            variables = {
                "default_video_config": {
                    "provider": "bytedance",
                    "model": "doubao-seedance-2-0-fast-260128",
                    "api_key_ref": "video_bytedance_key",
                    "extra": {"default_ratio": "9:16"},
                }
            }

            config = executor.load_provider_config(args, variables, "video")
        finally:
            executor.postgres_connect = old_connect
            executor.resolve_secret_value = old_secret

        self.assertEqual(config["provider"], "bytedance")
        self.assertEqual(config["api_key"], "stored-ark-key")
        self.assertEqual(config["base_url"], "https://ark.cn-beijing.volces.com/api/v3")
        self.assertEqual(config["default_ratio"], "9:16")
        self.assertEqual(config["default_resolution"], "720p")
        self.assertTrue(config["generate_audio"])


if __name__ == "__main__":
    unittest.main()
