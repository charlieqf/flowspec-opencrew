from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT / "backend",):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)


class KouboAssetVideoProviderRetryContractTest(unittest.TestCase):
    def register_services(self) -> SimpleNamespace:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        ns = SimpleNamespace()
        asset_video_generation_services.register_asset_video_generation_services(ns)
        return ns

    def test_xai_quality_video_resolution_and_provider_cost_are_normalized(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        quality_model = "grok-imagine-video-1.5-preview"
        self.assertEqual(asset_video_generation_services.xai_video_resolution({}, quality_model), "1080p")
        self.assertEqual(asset_video_generation_services.xai_video_resolution({"resolution": "720p"}, quality_model), "720p")
        self.assertEqual(asset_video_generation_services.xai_video_resolution({}, "grok-imagine-video"), "720p")
        self.assertEqual(asset_video_generation_services.xai_video_resolution({"resolution": "1080p"}, "grok-imagine-video"), "720p")
        self.assertEqual(asset_video_generation_services.xai_usage_cost_micros({"cost_in_usd_ticks": 123_450_000}), 12_345)
        self.assertIsNone(asset_video_generation_services.xai_usage_cost_micros({"cost_in_usd_ticks": "invalid"}))

    def test_video_json_request_retries_gemini_unavailable_once(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        ns = self.register_services()

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self) -> bytes:
                return b'{"name":"operations/video-123"}'

        calls: list[dict[str, object]] = []

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
        with patch.object(asset_video_generation_services.urllib.request, "urlopen", fake_urlopen), patch.object(asset_video_generation_services.time, "sleep", lambda seconds: sleeps.append(seconds)):
            result = ns.post_video_json_request(
                "https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-lite-generate-preview:predictLongRunning?key=test",
                {"instances": [{"prompt": "测试"}], "parameters": {"sampleCount": 1}},
                {},
                provider="gemini",
            )

        self.assertEqual(result, {"name": "operations/video-123"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["url"], calls[1]["url"])
        self.assertEqual(calls[0]["timeout"], 120)
        self.assertEqual(sleeps, [10.0])
        self.assertEqual(calls[0]["headers"]["Content-type"], "application/json")
        self.assertEqual(calls[0]["payload"]["instances"][0]["prompt"], "测试")

    def test_video_json_request_does_not_retry_bad_request(self) -> None:
        from fastapi import HTTPException
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        ns = self.register_services()
        calls: list[str] = []

        def fake_urlopen(request, timeout: int = 120):
            calls.append(request.full_url)
            payload = json.dumps({"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}}).encode("utf-8")
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(payload))

        sleeps: list[float] = []
        with patch.object(asset_video_generation_services.urllib.request, "urlopen", fake_urlopen), patch.object(asset_video_generation_services.time, "sleep", lambda seconds: sleeps.append(seconds)):
            with self.assertRaises(HTTPException) as raised:
                ns.post_video_json_request("https://example.invalid/video", {"x": 1}, {}, provider="gemini")

        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])
        self.assertIn("HTTP 400", str(raised.exception.detail))

    def test_seedance_video_config_routes_through_openrouter(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        class FakeRow:
            def __init__(self, mapping: dict[str, object]) -> None:
                self._mapping = mapping

        class FakeResult:
            def __init__(self, row: FakeRow | None) -> None:
                self.row = row

            def first(self) -> FakeRow | None:
                return self.row

        class FakeConn:
            def __init__(self) -> None:
                self.providers: list[str] = []

            def execute(self, _statement, params: dict[str, object] | None = None) -> FakeResult:
                provider = str((params or {}).get("provider") or "")
                self.providers.append(provider)
                if provider == "openrouter":
                    return FakeResult(FakeRow({
                        "provider": "openrouter",
                        "model": "bytedance/seedance-2.0-fast",
                        "api_key_ref": "video_openrouter_key",
                        "api_key_ciphertext": "",
                        "extra_json": json.dumps({"base_url": "https://openrouter.ai/api/v1", "send_frame_images": True}),
                    }))
                return FakeResult(None)

        class FakeBegin:
            def __init__(self, conn: FakeConn) -> None:
                self.conn = conn

            def __enter__(self) -> FakeConn:
                return self.conn

            def __exit__(self, *_args) -> None:
                return None

        class FakeEngine:
            def __init__(self, conn: FakeConn) -> None:
                self.conn = conn

            def begin(self) -> FakeBegin:
                return FakeBegin(self.conn)

        conn = FakeConn()
        ns = SimpleNamespace(ctx=SimpleNamespace(engine=FakeEngine(conn), secret_store=SimpleNamespace(get=lambda _key: "")))
        with patch.object(asset_video_generation_services, "ensure_table", lambda _ctx: None), patch.object(asset_video_generation_services, "load_stored_key", lambda _ctx, _kind, provider: "openrouter-key" if provider == "openrouter" else ""):
            asset_video_generation_services.register_asset_video_generation_services(ns)
            config = ns.load_video_config("bytedance", "doubao-seedance-2-0-fast-260128", sc=ns)

        self.assertEqual(conn.providers, ["openrouter"])
        self.assertEqual(config["provider"], "openrouter")
        self.assertEqual(config["model"], "bytedance/seedance-2.0-fast")
        self.assertEqual(config["api_key"], "openrouter-key")
        self.assertEqual(config["base_url"], "https://openrouter.ai/api/v1")

    def test_chanjing_happyhorse_video_config_routes_through_wan(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        class FakeRow:
            def __init__(self, mapping: dict[str, object]) -> None:
                self._mapping = mapping

        class FakeResult:
            def __init__(self, row: FakeRow | None) -> None:
                self.row = row

            def first(self) -> FakeRow | None:
                return self.row

        class FakeConn:
            def __init__(self) -> None:
                self.providers: list[str] = []

            def execute(self, _statement, params: dict[str, object] | None = None) -> FakeResult:
                provider = str((params or {}).get("provider") or "")
                self.providers.append(provider)
                if provider == "wan":
                    return FakeResult(FakeRow({
                        "provider": "wan",
                        "model": "wan2.7-i2v-2026-04-25",
                        "api_key_ref": "video_wan_key",
                        "api_key_ciphertext": "",
                        "extra_json": "{}",
                    }))
                return FakeResult(None)

        class FakeBegin:
            def __init__(self, conn: FakeConn) -> None:
                self.conn = conn

            def __enter__(self) -> FakeConn:
                return self.conn

            def __exit__(self, *_args) -> None:
                return None

        class FakeEngine:
            def __init__(self, conn: FakeConn) -> None:
                self.conn = conn

            def begin(self) -> FakeBegin:
                return FakeBegin(self.conn)

        conn = FakeConn()
        ns = SimpleNamespace(ctx=SimpleNamespace(engine=FakeEngine(conn), secret_store=SimpleNamespace(get=lambda _key: "")))
        with patch.object(asset_video_generation_services, "ensure_table", lambda _ctx: None), patch.object(asset_video_generation_services, "load_stored_key", lambda _ctx, _kind, provider: "wan-key" if provider == "wan" else ""):
            asset_video_generation_services.register_asset_video_generation_services(ns)
            config = ns.load_video_config("chanjing", "happyhorse-1.0-r2v", sc=ns)

        self.assertEqual(conn.providers, ["wan"])
        self.assertEqual(config["provider"], "wan")
        self.assertEqual(config["model"], "happyhorse-1.0-r2v")
        self.assertEqual(config["api_key"], "wan-key")

    def test_video_generation_config_resolves_agent_video_alias_before_loading_config(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        ns = SimpleNamespace(ctx=SimpleNamespace())
        asset_video_generation_services.register_asset_video_generation_services(ns)
        load_calls: list[tuple[str, str]] = []

        def fake_aliases(_ctx, kind: str = "image") -> list[dict[str, str]]:
            self.assertEqual(kind, "video")
            return [{"alias": "Max SR2", "provider": "openrouter", "model": "bytedance/seedance-2.0"}]

        def fake_load_video_config(provider: str, model: str, *, sc) -> dict[str, str]:
            load_calls.append((provider, model))
            return {"provider": provider, "model": model, "api_key": "video-key"}

        with patch.object(asset_video_generation_services, "load_agent_model_aliases", fake_aliases), patch.object(asset_video_generation_services, "load_video_config", fake_load_video_config):
            config = ns.load_video_config_for_generation(
                {"session_id": 1},
                {"agentVideoAlias": "Max SR2", "provider": "", "model": ""},
                sc=ns,
            )

        self.assertEqual(load_calls, [("openrouter", "bytedance/seedance-2.0")])
        self.assertEqual(config["provider"], "openrouter")
        self.assertEqual(config["model"], "bytedance/seedance-2.0")
        self.assertEqual(config["agent_video_alias"], "Max SR2")

    def test_video_generation_stale_saved_agent_alias_falls_back_to_active_config(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        ns = SimpleNamespace(
            ctx=SimpleNamespace(),
            read_or_create_videos_agent_settings=lambda _task: {
                "settings": {
                    "agentVideoAlias": "Deleted Alias",
                    "provider": "deleted-provider",
                    "model": "deleted-model",
                }
            },
        )
        asset_video_generation_services.register_asset_video_generation_services(ns)
        load_calls: list[tuple[str, str]] = []

        def fake_load_video_config(provider: str, model: str, *, sc) -> dict[str, str]:
            load_calls.append((provider, model))
            return {"provider": "active-provider", "model": "active-model", "api_key": "video-key"}

        with (
            patch.object(asset_video_generation_services, "load_agent_model_aliases", return_value=[]),
            patch.object(asset_video_generation_services, "load_config", return_value={"providers": []}),
            patch.object(asset_video_generation_services, "load_video_config", fake_load_video_config),
        ):
            config = ns.load_video_config_for_generation(
                {"session_id": 1},
                {"agent_generation_id": "generation-1", "provider": "", "model": ""},
                sc=ns,
            )

        self.assertEqual(load_calls, [("", "")])
        self.assertEqual(config["provider"], "active-provider")
        self.assertEqual(config["model"], "active-model")
        self.assertNotIn("agent_video_alias", config)

    def test_video_generation_explicit_stale_alias_remains_strict(self) -> None:
        from fastapi import HTTPException
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        ns = SimpleNamespace(ctx=SimpleNamespace())
        asset_video_generation_services.register_asset_video_generation_services(ns)
        with (
            patch.object(asset_video_generation_services, "load_agent_model_aliases", return_value=[]),
            patch.object(asset_video_generation_services, "load_config", return_value={"providers": []}),
        ):
            with self.assertRaises(HTTPException) as raised:
                ns.load_video_config_for_generation(
                    {"session_id": 1},
                    {"agentVideoAlias": "Deleted Alias", "provider": "", "model": ""},
                    sc=ns,
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("valid Agent video model", str(raised.exception.detail))

    def test_openrouter_video_provider_submits_polls_and_downloads(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        class FakeResponse:
            def __init__(self, body: bytes) -> None:
                self.body = body

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self, _size: int = -1) -> bytes:
                body = self.body
                self.body = b""
                return body

        calls: list[dict[str, object]] = []

        def fake_urlopen(request, timeout: int = 120):
            payload = json.loads(request.data.decode("utf-8")) if getattr(request, "data", None) else None
            calls.append({"url": request.full_url, "payload": payload, "headers": dict(request.header_items()), "timeout": timeout})
            if request.full_url == "https://openrouter.ai/api/v1/videos":
                body = json.dumps({"id": "or_video_123", "status": "pending", "polling_url": "https://openrouter.ai/api/v1/videos/or_video_123?token=poll-token"}).encode("utf-8")
                return FakeResponse(body)
            if request.full_url == "https://openrouter.ai/api/v1/videos/or_video_123?token=poll-token":
                body = json.dumps({"id": "or_video_123", "status": "completed", "output_url": "https://cdn.openrouter.example/out.mp4?sig=secret"}).encode("utf-8")
                return FakeResponse(body)
            if request.full_url == "https://cdn.openrouter.example/out.mp4?sig=secret":
                return FakeResponse(b"openrouter-video")
            raise AssertionError(f"unexpected URL: {request.full_url}")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            reference_path = workspace / "SessionOutput/storyboard/assets/images/first.png"
            reference_path.parent.mkdir(parents=True)
            reference_path.write_bytes(b"png-bytes")
            def write_json(path: Path, payload: dict[str, object]) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            ns = SimpleNamespace(
                workspace_for=lambda _task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                write_json=write_json,
                ctx=SimpleNamespace(),
            )
            asset_video_generation_services.register_asset_video_generation_services(ns)
            module = ns.analysis_v1_openrouter_video_module()
            sleeps: list[float] = []

            def fake_post_json(url: str, payload: dict[str, object], headers: dict[str, str], timeout: int = 120) -> dict[str, object]:
                request = SimpleNamespace(full_url=url, data=json.dumps(payload).encode("utf-8"), header_items=lambda: headers.items())
                return json.loads(fake_urlopen(request, timeout).read().decode("utf-8"))

            def fake_get_json(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, object]:
                request = SimpleNamespace(full_url=url, data=None, header_items=lambda: headers.items())
                return json.loads(fake_urlopen(request, timeout).read().decode("utf-8"))

            def fake_download(url: str, output_path: Path, **_kwargs) -> None:
                request = SimpleNamespace(full_url=url, data=None, header_items=lambda: [])
                output_path.write_bytes(fake_urlopen(request, 600).read())

            with patch.object(module, "post_json_request", fake_post_json), patch.object(module, "get_json_request", fake_get_json), patch.object(module, "safe_download_to_path", fake_download), patch.object(module.time, "sleep", lambda seconds: sleeps.append(seconds)), patch.object(asset_video_generation_services, "sanitize_video_output", lambda _path: None):
                result = ns.run_asset_library_video_provider(
                    {"session_id": 1},
                    {"request_id": "video-request-1"},
                    "A product shot with gentle camera motion.",
                    {"provider": "openrouter", "model": "bytedance/seedance-2.0-fast", "api_key": "openrouter-test-key"},
                    "SessionOutput/storyboard/assets/videos/out.mp4",
                    [reference_path],
                    [],
                    [],
                    3,
                    "9:16",
                sc=ns)

            output_path = workspace / "SessionOutput/storyboard/assets/videos/out.mp4"
            output_bytes = output_path.read_bytes()
            state_path = Path(result["provider_state_path"])
            state_payload = state_path.read_text(encoding="utf-8")

        self.assertEqual(output_bytes, b"openrouter-video")
        self.assertEqual(result["provider"], "openrouter")
        self.assertEqual(result["provider_task_id"], "or_video_123")
        self.assertEqual(result["effective_duration_seconds"], 4)
        self.assertTrue(result["send_frame_images"])
        self.assertEqual(calls[0]["url"], "https://openrouter.ai/api/v1/videos")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer openrouter-test-key")
        self.assertEqual(calls[0]["payload"]["model"], "bytedance/seedance-2.0-fast")
        self.assertEqual(calls[0]["payload"]["duration"], 4)
        self.assertEqual(calls[0]["payload"]["aspect_ratio"], "9:16")
        self.assertEqual(calls[0]["payload"]["resolution"], "720p")
        self.assertEqual(calls[0]["payload"]["frame_images"][0]["frame_type"], "first_frame")
        self.assertTrue(calls[0]["payload"]["frame_images"][0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(calls[1]["url"], "https://openrouter.ai/api/v1/videos/or_video_123?token=poll-token")
        self.assertEqual(calls[2]["url"], "https://cdn.openrouter.example/out.mp4?sig=secret")
        self.assertNotIn("poll-token", state_payload)
        self.assertEqual(sleeps, [])

    def test_happyhorse_r2v_provider_call_routes_to_wan_and_sends_three_reference_images(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        calls: list[dict[str, object]] = []
        submit_payloads: list[dict[str, object]] = []

        class FakeResponse:
            def __init__(self, body: bytes, status: int = 200) -> None:
                self.body = body
                self.status = status

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self, _size: int = -1) -> bytes:
                body = self.body
                self.body = b""
                return body

        def fake_urlopen(request, timeout: int = 120):
            payload = json.loads(request.data.decode("utf-8")) if getattr(request, "data", None) and request.headers.get("Content-type") == "application/json" else None
            calls.append({"url": request.full_url, "payload": payload, "headers": dict(request.header_items()), "timeout": timeout})
            if request.full_url.startswith("https://dashscope.aliyuncs.com/api/v1/uploads?"):
                body = json.dumps({"data": {"upload_host": "https://oss.example/upload", "upload_dir": "tmp", "oss_access_key_id": "id", "signature": "sig", "policy": "policy"}}).encode("utf-8")
                return FakeResponse(body)
            if request.full_url == "https://oss.example/upload":
                return FakeResponse(b"", status=200)
            if request.full_url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis":
                submit_payloads.append(payload or {})
                return FakeResponse(json.dumps({"output": {"task_id": "wan_happyhorse_123"}}).encode("utf-8"))
            if request.full_url == "https://dashscope.aliyuncs.com/api/v1/tasks/wan_happyhorse_123":
                return FakeResponse(json.dumps({"output": {"status": "SUCCEEDED", "video_url": "https://dashscope.example/happyhorse.mp4"}}).encode("utf-8"))
            if request.full_url == "https://dashscope.example/happyhorse.mp4":
                return FakeResponse(b"happyhorse-video")
            raise AssertionError(f"unexpected URL: {request.full_url}")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            reference_paths: list[Path] = []
            for index in range(3):
                image_path = workspace / "SessionOutput/storyboard/assets/images" / f"ref_{index + 1}.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                image_path.write_bytes(b"png-bytes")
                reference_paths.append(image_path)

            def write_json(path: Path, payload: dict[str, object]) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            ns = SimpleNamespace(
                workspace_for=lambda _task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                write_json=write_json,
                ctx=SimpleNamespace(),
            )
            asset_video_generation_services.register_asset_video_generation_services(ns)
            with patch.object(asset_video_generation_services.urllib.request, "urlopen", fake_urlopen), patch.object(asset_video_generation_services.time, "sleep", lambda _seconds: None), patch.object(asset_video_generation_services, "sanitize_video_output", lambda _path: None):
                result = ns.run_asset_library_video_provider(
                    {"session_id": 1},
                    {"request_id": "video-request-happyhorse"},
                    "A product reference video.",
                    {"provider": "chanjing", "model": "happyhorse-1.0-r2v", "api_key": "wan-test-key"},
                    "SessionOutput/storyboard/assets/videos/happyhorse.mp4",
                    reference_paths,
                    [],
                    [],
                    5,
                    "9:16",
                sc=ns)

        submit_payload = submit_payloads[0]
        media = submit_payload["input"]["media"]
        self.assertEqual(result["provider"], "wan")
        self.assertEqual(result["model"], "happyhorse-1.0-r2v")
        self.assertEqual(len(media), 3)
        self.assertEqual([item["type"] for item in media], ["reference_image", "reference_image", "reference_image"])
        self.assertEqual(submit_payload["parameters"], {
            "duration": 5,
            "ratio": "9:16",
            "resolution": "720P",
            "prompt_extend": False,
            "watermark": False,
        })
        self.assertNotIn("last_frame_url", submit_payload["input"])
        self.assertIn("Bearer wan-test-key", calls[0]["headers"].get("Authorization", ""))
        self.assertEqual(calls[-1]["url"], "https://dashscope.example/happyhorse.mp4")

    def test_wan_wr27_provider_call_accepts_image_only_references(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        calls: list[dict[str, object]] = []
        submit_payloads: list[dict[str, object]] = []

        class FakeResponse:
            def __init__(self, body: bytes, status: int = 200) -> None:
                self.body = body
                self.status = status

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self, _size: int = -1) -> bytes:
                body = self.body
                self.body = b""
                return body

        def fake_urlopen(request, timeout: int = 120):
            payload = json.loads(request.data.decode("utf-8")) if getattr(request, "data", None) and request.headers.get("Content-type") == "application/json" else None
            calls.append({"url": request.full_url, "payload": payload, "headers": dict(request.header_items()), "timeout": timeout})
            if request.full_url.startswith("https://dashscope.aliyuncs.com/api/v1/uploads?"):
                body = json.dumps({"data": {"upload_host": "https://oss.example/upload", "upload_dir": "tmp", "oss_access_key_id": "id", "signature": "sig", "policy": "policy"}}).encode("utf-8")
                return FakeResponse(body)
            if request.full_url == "https://oss.example/upload":
                return FakeResponse(b"", status=200)
            if request.full_url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis":
                submit_payloads.append(payload or {})
                return FakeResponse(json.dumps({"output": {"task_id": "wan_wr27_123"}}).encode("utf-8"))
            if request.full_url == "https://dashscope.aliyuncs.com/api/v1/tasks/wan_wr27_123":
                return FakeResponse(json.dumps({"output": {"status": "SUCCEEDED", "video_url": "https://dashscope.example/wr27.mp4"}}).encode("utf-8"))
            if request.full_url == "https://dashscope.example/wr27.mp4":
                return FakeResponse(b"wr27-video")
            raise AssertionError(f"unexpected URL: {request.full_url}")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            reference_paths: list[Path] = []
            for index in range(3):
                image_path = workspace / "SessionOutput/storyboard/assets/images" / f"wr27_ref_{index + 1}.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                image_path.write_bytes(b"png-bytes")
                reference_paths.append(image_path)

            def write_json(path: Path, payload: dict[str, object]) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            ns = SimpleNamespace(
                workspace_for=lambda _task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                write_json=write_json,
                ctx=SimpleNamespace(),
            )
            asset_video_generation_services.register_asset_video_generation_services(ns)
            with patch.object(asset_video_generation_services.urllib.request, "urlopen", fake_urlopen), patch.object(asset_video_generation_services.time, "sleep", lambda _seconds: None), patch.object(asset_video_generation_services, "sanitize_video_output", lambda _path: None):
                result = ns.run_asset_library_video_provider(
                    {"session_id": 1},
                    {"request_id": "video-request-wr27"},
                    "Generate a consistent two-person scene from references.",
                    {"provider": "wan", "model": "wan2.7-r2v", "api_key": "wan-test-key"},
                    "SessionOutput/storyboard/assets/videos/wr27.mp4",
                    reference_paths,
                    [],
                    [],
                    5,
                    "9:16",
                sc=ns)

        submit_payload = submit_payloads[0]
        media = submit_payload["input"]["media"]
        self.assertEqual(result["provider"], "wan")
        self.assertEqual(result["model"], "wan2.7-r2v")
        self.assertEqual(len(media), 3)
        self.assertEqual([item["type"] for item in media], ["reference_image", "reference_image", "reference_image"])
        self.assertNotIn("last_frame_url", submit_payload["input"])
        self.assertNotIn("reference_video", [item["type"] for item in media])
        self.assertEqual(calls[-1]["url"], "https://dashscope.example/wr27.mp4")

    def test_openrouter_real_person_reference_rejection_is_actionable(self) -> None:
        from fastapi import HTTPException
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        class FakeToolError(RuntimeError):
            pass

        class FakeOpenRouterModule:
            ProviderTimeout = RuntimeError
            ToolError = FakeToolError

            @staticmethod
            def generate(_context, _prompt_path, _output_path):
                raise FakeToolError(
                    'HTTP 400 from https://openrouter.ai/api/v1/videos: {"error":{"message":"HTTP 400: '
                    '{\\"error\\":{\\"code\\":\\"InputImageSensitiveContentDetected.PrivacyInformation\\",'
                    '\\"message\\":\\"The request failed because the input image may contain real person.\\"}}"}}'
                )

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            reference_path = workspace / "SessionOutput/storyboard/assets/images/person.png"
            reference_path.parent.mkdir(parents=True)
            reference_path.write_bytes(b"png-bytes")

            def write_json(path: Path, payload: dict[str, object]) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            ns = SimpleNamespace(
                workspace_for=lambda _task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                write_json=write_json,
                ctx=SimpleNamespace(),
            )
            asset_video_generation_services.register_asset_video_generation_services(ns)
            ns.analysis_v1_video_module._module_video_openrouter = FakeOpenRouterModule()
            try:
                with self.assertRaises(HTTPException) as raised:
                    ns.run_asset_library_video_provider(
                        {"session_id": 1},
                        {"request_id": "video-request-sensitive"},
                        "Use this person reference.",
                        {"provider": "openrouter", "model": "bytedance/seedance-2.0-fast", "api_key": "openrouter-test-key"},
                        "SessionOutput/storyboard/assets/videos/out.mp4",
                        [reference_path],
                        [],
                        [],
                        4,
                        "9:16",
                    sc=ns)
            finally:
                if hasattr(ns.analysis_v1_video_module, "_module_video_openrouter"):
                    delattr(ns.analysis_v1_video_module, "_module_video_openrouter")

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIsInstance(raised.exception.detail, dict)
        self.assertEqual(raised.exception.detail["provider_error_code"], "InputImageSensitiveContentDetected.PrivacyInformation")
        self.assertEqual(raised.exception.detail["reference_image_count"], 1)
        self.assertIn("Remove the selected person reference image", raised.exception.detail["suggestion"])

    def test_xai_grok_imagine_15_text_to_video_is_blocked_before_provider_call(self) -> None:
        from fastapi import HTTPException
        from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            ns = SimpleNamespace(
                workspace_for=lambda _task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                write_json=lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
                ctx=SimpleNamespace(),
            )
            asset_video_generation_services.register_asset_video_generation_services(ns)

            with patch.object(asset_video_generation_services.urllib.request, "urlopen", side_effect=AssertionError("provider should not be called")):
                with self.assertRaises(HTTPException) as raised:
                    ns.run_asset_library_video_provider(
                        {"session_id": 1},
                        {"request_id": "video-request-xai-no-image"},
                        "Generate a product launch video from text only.",
                        {"provider": "xai", "model": "grok-imagine-video-1.5-preview", "api_key": "xai-test-key"},
                        "SessionOutput/storyboard/assets/videos/out.mp4",
                        [],
                        [],
                        [],
                        4,
                        "9:16",
                    sc=ns)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIsInstance(raised.exception.detail, dict)
        self.assertIn("does not support text-to-video", raised.exception.detail["message"])
        self.assertEqual(raised.exception.detail["reference_image_count"], 0)


if __name__ == "__main__":
    unittest.main()
