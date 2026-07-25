from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)
        self._chunks = chunks or []
        self.closed = False

    def json(self) -> dict[str, Any]:
        return self._payload

    def close(self) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield from self._chunks


class FakeRequests:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.post_count = 0

    def request(self, method: str, url: str, **_kwargs: Any) -> FakeResponse:
        self.calls.append((method, url))
        if method == "POST" and url == "https://api.sync.so/v2/generate":
            self.post_count += 1
            if self.post_count == 1:
                return FakeResponse(429, {"message": "Concurrency limit reached"}, {"Retry-After": "1"})
            return FakeResponse(200, {"id": "generation_123"})
        if method == "GET" and url == "https://api.sync.so/v2/generate/generation_123":
            return FakeResponse(200, {"status": "COMPLETED", "outputUrl": "https://download.example/video.mp4"})
        if method == "GET" and url == "https://download.example/video.mp4":
            return FakeResponse(200, {}, chunks=[b"video-bytes"])
        return FakeResponse(404, {"message": f"unexpected call: {method} {url}"})


class FakeSyncAssetRequests:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.upload_count = 0
        self.register_count = 0

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if method == "POST" and url == "https://api.sync.so/v2/assets/upload":
            self.upload_count += 1
            return FakeResponse(200, {
                "uploadUrl": f"https://upload.example/{self.upload_count}?signature=secret",
                "url": f"https://assets.example/{self.upload_count}",
            })
        if method == "PUT" and url.startswith("https://upload.example/"):
            source = kwargs.get("data")
            if source:
                source.read()
            return FakeResponse(200, {})
        if method == "POST" and url == "https://api.sync.so/v2/assets":
            if kwargs.get("json", {}).get("type") not in {"VIDEO", "AUDIO", "IMAGE"}:
                return FakeResponse(422, {"message": "invalid asset type"})
            self.register_count += 1
            return FakeResponse(200, {"id": f"asset_{self.register_count}"})
        if method == "POST" and url == "https://api.sync.so/v2/generate":
            return FakeResponse(200, {"id": "generation_asset_123"})
        if method == "GET" and url == "https://api.sync.so/v2/generate/generation_asset_123":
            return FakeResponse(200, {"status": "COMPLETED", "outputUrl": "https://download.example/asset-video.mp4"})
        if method == "GET" and url == "https://download.example/asset-video.mp4":
            return FakeResponse(200, {}, chunks=[b"asset-video-bytes"])
        if method == "DELETE" and url.startswith("https://api.sync.so/v2/assets/"):
            return FakeResponse(200, {"id": url.rsplit("/", 1)[-1]})
        return FakeResponse(404, {"message": f"unexpected call: {method} {url}"})


class FakeSyncAssetUploadFailureRequests(FakeSyncAssetRequests):
    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if method == "PUT" and url.startswith("https://upload.example/"):
            self.calls.append((method, url, kwargs))
            return FakeResponse(503, {"message": "storage unavailable"})
        return super().request(method, url, **kwargs)


class FakeSyncSecondAssetUploadFailureRequests(FakeSyncAssetRequests):
    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if method == "PUT" and url.startswith("https://upload.example/2"):
            self.calls.append((method, url, kwargs))
            return FakeResponse(503, {"message": "audio storage unavailable"})
        return super().request(method, url, **kwargs)


class FakeSyncTimeoutRequests(FakeSyncAssetRequests):
    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if method == "GET" and url == "https://api.sync.so/v2/generate/generation_asset_123":
            self.calls.append((method, url, kwargs))
            return FakeResponse(200, {"status": "PROCESSING"})
        return super().request(method, url, **kwargs)


class FakeSyncGenerationFailureRequests(FakeSyncAssetRequests):
    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if method == "POST" and url == "https://api.sync.so/v2/generate":
            self.calls.append((method, url, kwargs))
            return FakeResponse(422, {"message": "invalid generation input"})
        return super().request(method, url, **kwargs)


class FakeSyncCleanupFailureRequests(FakeSyncAssetRequests):
    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if method == "DELETE" and url.startswith("https://api.sync.so/v2/assets/"):
            self.calls.append((method, url, kwargs))
            return FakeResponse(500, {"message": "cleanup unavailable"})
        return super().request(method, url, **kwargs)


class FakeHeyGenRequests:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.upload_count = 0

    def request(self, method: str, url: str, **_kwargs: Any) -> FakeResponse:
        self.calls.append((method, url))
        if method == "POST" and url == "https://api.heygen.com/v3/assets":
            self.upload_count += 1
            return FakeResponse(
                200,
                {
                    "data": {
                        "asset_id": f"asset_{self.upload_count}",
                        "url": f"https://asset.example/{self.upload_count}",
                        "mime_type": "video/mp4" if self.upload_count == 1 else "audio/wav",
                        "size_bytes": 12,
                    }
                },
            )
        if method == "POST" and url == "https://api.heygen.com/v3/lipsyncs":
            return FakeResponse(200, {"data": {"lipsync_id": "lip_123"}})
        if method == "GET" and url == "https://api.heygen.com/v3/lipsyncs/lip_123":
            return FakeResponse(200, {"data": {"id": "lip_123", "status": "completed", "video_url": "https://download.example/heygen.mp4"}})
        if method == "GET" and url == "https://download.example/heygen.mp4":
            return FakeResponse(200, {}, chunks=[b"heygen-video"])
        return FakeResponse(404, {"message": f"unexpected call: {method} {url}"})


class FakeHeyGenRulesetRequests(FakeHeyGenRequests):
    def __init__(self) -> None:
        super().__init__()
        self.block_first_asset_upload = True

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if self.block_first_asset_upload and method == "POST" and url == "https://api.heygen.com/v3/assets":
            self.block_first_asset_upload = False
            raise RuntimeError(
                "SOCKSHTTPSConnectionPool(host='api.heygen.com', port=443): "
                "Max retries exceeded with url: /v3/assets "
                "(Caused by NewConnectionError('Connection not allowed by ruleset'))"
            )
        return super().request(method, url, **kwargs)


class FakeKlingRequests:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.payloads: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url))
        payload = kwargs.get("json")
        if isinstance(payload, dict):
            self.payloads.append(payload)
        if method == "POST" and url == "https://api-beijing.klingai.com/v1/videos/identify-face":
            return FakeResponse(200, {"code": 0, "data": {"session_id": "session_123", "face_data": [{"face_id": "face_1", "start_time": 0, "end_time": 5200}]}})
        if method == "POST" and url == "https://api-beijing.klingai.com/v1/videos/advanced-lip-sync":
            return FakeResponse(200, {"code": 0, "data": {"task_id": "kling_lip_123"}})
        if method == "GET" and url == "https://api-beijing.klingai.com/v1/videos/advanced-lip-sync/kling_lip_123":
            return FakeResponse(200, {"code": 0, "data": {"task_status": "succeed", "task_result": {"videos": [{"url": "https://download.example/kling.mp4"}]}, "final_unit_deduction": 0.5}})
        if method == "GET" and url == "https://download.example/kling.mp4":
            return FakeResponse(200, {}, chunks=[b"kling-video"])
        return FakeResponse(404, {"message": f"unexpected call: {method} {url}"})


class AnalysisV1VideoPlanExecutorResilienceContractTest(unittest.TestCase):
    def test_segment_audio_ffmpeg_fallback_uses_configured_binary_when_path_is_missing(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "video_plan_executor_audio_ffmpeg_env_contract",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            audio_a = workspace / "a.wav"
            audio_b = workspace / "b.wav"
            output = workspace / "out.wav"
            audio_a.parent.mkdir(parents=True, exist_ok=True)
            audio_a.write_bytes(b"not-a-valid-wav-a")
            audio_b.write_bytes(b"not-a-valid-wav-b")
            configured_ffmpeg = str(REPO_ROOT / "ToolLibrary" / ".bin" / "ffmpeg")
            captured: dict[str, Any] = {}

            def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
                captured["command"] = command
                return SimpleNamespace(returncode=0, stderr="", stdout="")

            old_env = os.environ.get("OPENCREW_FFMPEG_PATH")
            old_which = module.shutil.which
            old_run = module.subprocess.run
            try:
                os.environ["OPENCREW_FFMPEG_PATH"] = configured_ffmpeg
                module.shutil.which = lambda _name: None
                module.subprocess.run = fake_run
                result = module.compose_segment_audio(workspace, [audio_a, audio_b], output)
            finally:
                if old_env is None:
                    os.environ.pop("OPENCREW_FFMPEG_PATH", None)
                else:
                    os.environ["OPENCREW_FFMPEG_PATH"] = old_env
                module.shutil.which = old_which
                module.subprocess.run = old_run

            self.assertEqual(result["source"], "ffmpeg_concat")
            self.assertEqual(captured["command"][0], configured_ffmpeg)

    def test_existing_dialogue_audio_copy_does_not_pollute_storyboard_slots(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "video_plan_executor_audio_slot_contract",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            audio_rel = "SessionOutput/storyboard/Working/srt_0001_Audio_Final.wav"
            stale_tool_audio_rel = "S9_05_02_VideoPlanExecutor/Working/srt_0001_DialogueAudio.wav"
            (workspace / audio_rel).parent.mkdir(parents=True, exist_ok=True)
            (workspace / audio_rel).write_bytes(b"fake-wav")
            source_dialogue = {
                "srt_id": "srt_0001",
                "dialogue_id": "scene_001_dialogue_001",
                "dialogue": "测试对白",
                "start": 0.0,
                "end": 1.0,
                "duration": 1.0,
                "working_assets": {
                    "audio": {"slot": "Audio_Final", "source_type": "generated", "path": audio_rel},
                    "images": [
                        {"slot": "Image_New", "source_type": "", "path": ""},
                        {"slot": "Image_02", "source_type": "", "path": ""},
                    ],
                    "video": {"slot": "Video_Final", "source_type": "", "path": ""},
                },
            }
            storyboard = {
                "schema_version": "analysis_v1_srt_storyboard_0.2",
                "shots": [{"shot_id": "shot_001", "scenes": [{"scene_id": "scene_001", "dialogue_items": [source_dialogue]}]}],
            }
            edit_dialogue = {
                "srt_id": "srt_0001",
                "dialogue_id": "scene_001_dialogue_001",
                "dialogue_asset_key": "srt_0001",
                "working_assets": {
                    "audio": {"slot": "Audio_Final", "source_type": "generated", "path": stale_tool_audio_rel},
                    "images": [
                        {"slot": "Image_New", "source_type": "", "path": ""},
                        {"slot": "Image_02", "source_type": "", "path": ""},
                    ],
                    "video": {"slot": "Video_Final", "source_type": "", "path": ""},
                },
            }
            write_json(workspace / "SessionOutput/storyboard/srt_storyboard.json", storyboard)
            write_json(
                workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json",
                {
                    "schema_version": "koubo_storyboard_edit_0.1",
                    "shots": [{"shot_id": "shot_001", "scenes": [{"scene_id": "scene_001", "dialogues": [edit_dialogue]}]}],
                },
            )
            segment = {
                "segment_id": "shot_001_scene_001_segment_001",
                "asset_key": "srt_0001",
                "dialogue_ids": ["srt_0001"],
                "dialogue_audio_tasks": [
                    {
                        "srt_id": "srt_0001",
                        "need_audio": False,
                        "audio_source": "existing_dialogue_audio",
                        "existing_audio_path": audio_rel,
                        "planned_audio_path": audio_rel,
                    }
                ],
                "planned_outputs": {
                    "segment_audio_path": "SessionOutput/storyboard/Working/srt_0001_SegmentAudio_Final.wav",
                    "video_path": "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4",
                },
                "tasks": {
                    "need_audio": False,
                    "need_image": False,
                    "need_image_prompt": False,
                    "need_video": False,
                    "need_lipsync": False,
                    "need_audio_video_sync": False,
                },
                "first_frame": {"source_type": "", "source_path": ""},
            }
            args = module.Args(
                workspace=str(workspace),
                database_url="",
                max_segments=1,
                force=False,
                execute_audio=False,
                execute_image=False,
                execute_video=False,
                execute_lipsync=False,
                image_provider="",
                image_model="",
                video_provider="",
                video_model="",
                lipsync_provider="",
                lipsync_model="",
                tts_provider="",
                tts_model="",
                provider_timeout_seconds=1,
                execute_audio_video_sync=False,
            )

            with self.assertRaises(Exception):
                module.execute_segment(
                    workspace,
                    args,
                    {},
                    {},
                    storyboard,
                    {},
                    storyboard["shots"][0],
                    storyboard["shots"][0]["scenes"][0],
                    segment,
                    module.flatten_dialogues(storyboard),
                    {},
                )

            self.assertTrue((workspace / stale_tool_audio_rel).exists())
            source_after = json.loads((workspace / "SessionOutput/storyboard/srt_storyboard.json").read_text(encoding="utf-8"))
            edit_after = json.loads((workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json").read_text(encoding="utf-8"))
            self.assertEqual(
                source_after["shots"][0]["scenes"][0]["dialogue_items"][0]["working_assets"]["audio"]["path"],
                audio_rel,
            )
            self.assertEqual(
                edit_after["shots"][0]["scenes"][0]["dialogues"][0]["working_assets"]["audio"]["path"],
                audio_rel,
            )
            self.assertNotIn("S9_05_02_VideoPlanExecutor/Working", json.dumps(source_after))
            self.assertNotIn("S9_05_02_VideoPlanExecutor/Working", json.dumps(edit_after))

    def test_existing_raw_video_is_reused_even_when_final_path_exists(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "video_plan_executor_raw_reuse_contract",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            audio_rel = "SessionOutput/storyboard/Working/srt_0001_Audio_Final.wav"
            raw_rel = "SessionOutput/storyboard/Working/srt_0001_Video_Raw.mov"
            final_rel = "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4"
            for rel_path, content in ((audio_rel, b"fake-wav"), (raw_rel, b"existing-raw"), (final_rel, b"existing-final")):
                path = workspace / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            dialogue = {
                "srt_id": "srt_0001",
                "dialogue_id": "scene_001_dialogue_001",
                "dialogue": "测试对白",
                "start": 0.0,
                "end": 1.0,
                "duration": 1.0,
                "working_assets": {
                    "audio": {"slot": "Audio_Final", "source_type": "generated", "path": audio_rel},
                    "images": [
                        {"slot": "Image_New", "source_type": "", "path": ""},
                        {"slot": "Image_02", "source_type": "", "path": ""},
                    ],
                    "video": {"slot": "Video_Final", "source_type": "", "path": ""},
                },
            }
            storyboard = {"schema_version": "analysis_v1_srt_storyboard_0.2", "shots": [{"shot_id": "shot_001", "scenes": [{"scene_id": "scene_001", "dialogue_items": [dialogue]}]}]}
            write_json(workspace / "SessionOutput/storyboard/srt_storyboard.json", storyboard)
            write_json(workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json", {"schema_version": "koubo_storyboard_edit_0.1", "shots": [{"shot_id": "shot_001", "scenes": [{"scene_id": "scene_001", "dialogues": [dialogue]}]}]})
            segment = {
                "segment_id": "shot_001_scene_001_segment_001",
                "asset_key": "srt_0001",
                "dialogue_ids": ["srt_0001"],
                "dialogue_audio_tasks": [{"srt_id": "srt_0001", "need_audio": False, "existing_audio_path": audio_rel, "planned_audio_path": audio_rel}],
                "planned_outputs": {
                    "segment_audio_path": "SessionOutput/storyboard/Working/srt_0001_SegmentAudio_Final.wav",
                    "raw_video_path": raw_rel,
                    "video_path": final_rel,
                },
                "tasks": {
                    "need_audio": False,
                    "need_image": False,
                    "need_image_prompt": False,
                    "need_video": False,
                    "need_video_prompt": False,
                    "need_lipsync": False,
                    "need_audio_video_sync": True,
                    "need_sync": True,
                },
                "first_frame": {"source_type": "existing_raw_video", "source_path": raw_rel},
            }
            args = module.Args(
                workspace=str(workspace),
                database_url="",
                max_segments=1,
                force=False,
                execute_audio=False,
                execute_image=False,
                execute_video=False,
                execute_lipsync=False,
                image_provider="",
                image_model="",
                video_provider="",
                video_model="",
                lipsync_provider="",
                lipsync_model="",
                tts_provider="",
                tts_model="",
                provider_timeout_seconds=1,
                execute_audio_video_sync=False,
            )

            with self.assertRaisesRegex(Exception, "Audio/video sync is required"):
                module.execute_segment(
                    workspace,
                    args,
                    {},
                    {},
                    storyboard,
                    {},
                    storyboard["shots"][0],
                    storyboard["shots"][0]["scenes"][0],
                    segment,
                    module.flatten_dialogues(storyboard),
                    {},
                )

            self.assertEqual((workspace / raw_rel).read_bytes(), b"existing-raw")
            self.assertEqual((workspace / "S9_05_02_VideoPlanExecutor/Working/srt_0001_Video_Raw.mov").read_bytes(), b"existing-raw")

    def test_syncso_create_retries_http_429_before_failing_the_step(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_syncso.py",
            "lipsync_syncso_retry_contract",
        )
        fake_requests = FakeRequests()
        fake_requests_module = SimpleNamespace(
            request=fake_requests.request,
            Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request),
        )
        old_requests = sys.modules.get("requests")
        old_sleep = module.time.sleep
        sys.modules["requests"] = fake_requests_module
        module.time.sleep = lambda _seconds: None
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_path = root / "audio.wav"
                output_path = root / "output.mp4"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")

                result = module.generate(
                    {
                        "config": {"provider": "sync", "model": "lipsync-2", "api_key": "sync-key"},
                        "video_path": str(video_path),
                        "audio_path": str(audio_path),
                        "request_path": str(root / "request.json"),
                        "status_path": str(root / "status.json"),
                        "create_response_path": str(root / "create_response.json"),
                        "timeout_seconds": 60,
                    },
                    root / "prompt.json",
                    output_path,
                )

                create_response = json.loads((root / "create_response.json").read_text(encoding="utf-8"))
                self.assertEqual(result["generation_id"], "generation_123")
                self.assertEqual(output_path.read_bytes(), b"video-bytes")
                self.assertEqual([attempt["status_code"] for attempt in create_response["attempts"]], [429, 200])
                self.assertEqual(fake_requests.calls.count(("POST", "https://api.sync.so/v2/generate")), 2)
        finally:
            module.time.sleep = old_sleep
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_syncso_large_inputs_use_asset_api_without_leaking_upload_credentials(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_syncso.py",
            "lipsync_syncso_asset_contract",
        )
        fake_requests = FakeSyncAssetRequests()
        fake_requests_module = SimpleNamespace(
            request=fake_requests.request,
            Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request),
        )
        old_requests = sys.modules.get("requests")
        old_limit = module.SYNC_DIRECT_UPLOAD_MAX_BYTES
        module.SYNC_DIRECT_UPLOAD_MAX_BYTES = 1
        sys.modules["requests"] = fake_requests_module
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_path = root / "audio.wav"
                output_path = root / "output.mp4"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")

                result = module.generate(
                    {
                        "config": {"provider": "sync", "model": "lipsync-2", "api_key": "sync-key"},
                        "video_path": str(video_path),
                        "audio_path": str(audio_path),
                        "request_path": str(root / "request.json"),
                        "status_path": str(root / "status.json"),
                        "create_response_path": str(root / "create_response.json"),
                        "timeout_seconds": 60,
                    },
                    root / "prompt.json",
                    output_path,
                )

                request_record = json.loads((root / "request.json").read_text(encoding="utf-8"))
                generate_call = next(call for call in fake_requests.calls if call[0:2] == ("POST", "https://api.sync.so/v2/generate"))
                put_calls = [call for call in fake_requests.calls if call[0] == "PUT"]
                register_calls = [call for call in fake_requests.calls if call[0:2] == ("POST", "https://api.sync.so/v2/assets")]
                delete_calls = [call for call in fake_requests.calls if call[0] == "DELETE"]
                self.assertEqual(result["generation_id"], "generation_asset_123")
                self.assertEqual(output_path.read_bytes(), b"asset-video-bytes")
                self.assertEqual(request_record["upload_mode"], "asset_api")
                self.assertEqual([item["id"] for item in request_record["assets"]], ["asset_1", "asset_2"])
                self.assertNotIn("upload.example", json.dumps(request_record))
                self.assertEqual(generate_call[2]["json"]["input"], [
                    {"type": "video", "assetId": "asset_1"},
                    {"type": "audio", "assetId": "asset_2"},
                ])
                self.assertNotIn("files", generate_call[2])
                self.assertEqual(len(put_calls), 2)
                self.assertTrue(all("x-api-key" not in call[2]["headers"] for call in put_calls))
                self.assertEqual([call[2]["headers"]["Content-Type"] for call in put_calls], ["video/mp4", "audio/wav"])
                self.assertEqual([call[2]["json"]["type"] for call in register_calls], ["VIDEO", "AUDIO"])
                self.assertEqual([call[1].rsplit("/", 1)[-1] for call in delete_calls], ["asset_2", "asset_1"])
                self.assertEqual([item["status"] for item in request_record["asset_cleanup"]["items"]], ["deleted", "deleted"])
        finally:
            module.SYNC_DIRECT_UPLOAD_MAX_BYTES = old_limit
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_syncso_asset_upload_failure_stops_before_registration(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_syncso.py",
            "lipsync_syncso_asset_failure_contract",
        )
        fake_requests = FakeSyncAssetUploadFailureRequests()
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "large-video.mp4"
            video_path.write_bytes(b"video")

            with self.assertRaisesRegex(module.ToolError, "asset byte upload failed for large-video.mp4"):
                module._upload_sync_asset(
                    fake_requests,
                    api_key="sync-key",
                    path=video_path,
                    asset_type="video",
                    content_type="video/mp4",
                )

        self.assertFalse(any(call[0:2] == ("POST", "https://api.sync.so/v2/assets") for call in fake_requests.calls))

    def test_syncso_partial_asset_upload_failure_cleans_registered_asset(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_syncso.py",
            "lipsync_syncso_partial_asset_failure_contract",
        )
        fake_requests = FakeSyncSecondAssetUploadFailureRequests()
        fake_requests_module = SimpleNamespace(request=fake_requests.request, Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request))
        old_requests = sys.modules.get("requests")
        old_limit = module.SYNC_DIRECT_UPLOAD_MAX_BYTES
        module.SYNC_DIRECT_UPLOAD_MAX_BYTES = 1
        sys.modules["requests"] = fake_requests_module
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_path = root / "audio.wav"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")
                context = {
                    "config": {"provider": "sync", "model": "lipsync-2", "api_key": "sync-key"},
                    "video_path": str(video_path),
                    "audio_path": str(audio_path),
                    "request_path": str(root / "request.json"),
                    "status_path": str(root / "status.json"),
                    "create_response_path": str(root / "create_response.json"),
                }

                with self.assertRaisesRegex(module.ToolError, "asset byte upload failed for audio.wav"):
                    module.generate(context, root / "prompt.json", root / "output.mp4")

                request_record = json.loads((root / "request.json").read_text(encoding="utf-8"))
                delete_calls = [call for call in fake_requests.calls if call[0] == "DELETE"]
                self.assertEqual([item["id"] for item in request_record["assets"]], ["asset_1"])
                self.assertEqual([call[1].rsplit("/", 1)[-1] for call in delete_calls], ["asset_1"])
                self.assertFalse(any(call[0:2] == ("POST", "https://api.sync.so/v2/generate") for call in fake_requests.calls))
        finally:
            module.SYNC_DIRECT_UPLOAD_MAX_BYTES = old_limit
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_syncso_timeout_cleans_assets_and_preserves_timeout_error(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_syncso.py",
            "lipsync_syncso_timeout_cleanup_contract",
        )
        fake_requests = FakeSyncTimeoutRequests()
        fake_requests_module = SimpleNamespace(request=fake_requests.request, Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request))
        old_requests = sys.modules.get("requests")
        old_limit = module.SYNC_DIRECT_UPLOAD_MAX_BYTES
        old_time = module.time.time
        old_sleep = module.time.sleep
        clock = [0.0]
        def advancing_time() -> float:
            clock[0] += 30.0
            return clock[0]
        module.SYNC_DIRECT_UPLOAD_MAX_BYTES = 1
        module.time.time = advancing_time
        module.time.sleep = lambda _seconds: None
        sys.modules["requests"] = fake_requests_module
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_path = root / "audio.wav"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")
                context = {
                    "config": {"provider": "sync", "model": "lipsync-2", "api_key": "sync-key"},
                    "video_path": str(video_path),
                    "audio_path": str(audio_path),
                    "request_path": str(root / "request.json"),
                    "status_path": str(root / "status.json"),
                    "create_response_path": str(root / "create_response.json"),
                    "timeout_seconds": 60,
                }

                with self.assertRaisesRegex(module.ProviderTimeout, "timed out"):
                    module.generate(context, root / "prompt.json", root / "output.mp4")

                request_record = json.loads((root / "request.json").read_text(encoding="utf-8"))
                self.assertEqual(len([call for call in fake_requests.calls if call[0] == "DELETE"]), 2)
                self.assertEqual([item["status"] for item in request_record["asset_cleanup"]["items"]], ["deleted", "deleted"])
        finally:
            module.SYNC_DIRECT_UPLOAD_MAX_BYTES = old_limit
            module.time.time = old_time
            module.time.sleep = old_sleep
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_syncso_generation_create_failure_cleans_both_assets(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_syncso.py",
            "lipsync_syncso_generation_failure_cleanup_contract",
        )
        fake_requests = FakeSyncGenerationFailureRequests()
        fake_requests_module = SimpleNamespace(request=fake_requests.request, Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request))
        old_requests = sys.modules.get("requests")
        old_limit = module.SYNC_DIRECT_UPLOAD_MAX_BYTES
        module.SYNC_DIRECT_UPLOAD_MAX_BYTES = 1
        sys.modules["requests"] = fake_requests_module
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_path = root / "audio.wav"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")
                context = {
                    "config": {"provider": "sync", "model": "lipsync-2", "api_key": "sync-key"},
                    "video_path": str(video_path),
                    "audio_path": str(audio_path),
                    "request_path": str(root / "request.json"),
                    "status_path": str(root / "status.json"),
                    "create_response_path": str(root / "create_response.json"),
                }

                with self.assertRaisesRegex(module.ToolError, "Sync.so create failed: HTTP 422"):
                    module.generate(context, root / "prompt.json", root / "output.mp4")

                request_record = json.loads((root / "request.json").read_text(encoding="utf-8"))
                self.assertEqual(len([call for call in fake_requests.calls if call[0] == "DELETE"]), 2)
                self.assertEqual([item["status"] for item in request_record["asset_cleanup"]["items"]], ["deleted", "deleted"])
        finally:
            module.SYNC_DIRECT_UPLOAD_MAX_BYTES = old_limit
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_syncso_cleanup_failure_does_not_replace_successful_generation(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_syncso.py",
            "lipsync_syncso_cleanup_failure_contract",
        )
        fake_requests = FakeSyncCleanupFailureRequests()
        fake_requests_module = SimpleNamespace(request=fake_requests.request, Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request))
        old_requests = sys.modules.get("requests")
        old_limit = module.SYNC_DIRECT_UPLOAD_MAX_BYTES
        module.SYNC_DIRECT_UPLOAD_MAX_BYTES = 1
        sys.modules["requests"] = fake_requests_module
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_path = root / "audio.wav"
                output_path = root / "output.mp4"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")
                result = module.generate({
                    "config": {"provider": "sync", "model": "lipsync-2", "api_key": "sync-key"},
                    "video_path": str(video_path),
                    "audio_path": str(audio_path),
                    "request_path": str(root / "request.json"),
                    "status_path": str(root / "status.json"),
                    "create_response_path": str(root / "create_response.json"),
                }, root / "prompt.json", output_path)

                self.assertEqual(output_path.read_bytes(), b"asset-video-bytes")
                self.assertEqual([item["status"] for item in result["asset_cleanup"]], ["failed", "failed"])
        finally:
            module.SYNC_DIRECT_UPLOAD_MAX_BYTES = old_limit
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_heygen_lipsync_uploads_assets_creates_polls_and_downloads(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_heygen.py",
            "lipsync_heygen_contract",
        )
        fake_requests = FakeHeyGenRequests()
        fake_requests_module = SimpleNamespace(
            request=fake_requests.request,
            Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request),
        )
        old_requests = sys.modules.get("requests")
        old_sleep = module.time.sleep
        old_prepare_media = module.prepare_media_for_heygen
        sys.modules["requests"] = fake_requests_module
        module.time.sleep = lambda _seconds: None
        module.prepare_media_for_heygen = lambda video, audio, _working_dir: (video, audio, {"video": {"changed": False}, "audio": {"changed": False}})
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_path = root / "audio.wav"
                output_path = root / "output.mp4"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")

                result = module.generate(
                    {
                        "config": {
                            "provider": "heygen",
                            "model": "speed",
                            "api_key": "heygen-key",
                            "enable_watermark": False,
                        },
                        "video_path": str(video_path),
                        "audio_path": str(audio_path),
                        "request_path": str(root / "request.json"),
                        "status_path": str(root / "status.json"),
                        "create_response_path": str(root / "create_response.json"),
                        "timeout_seconds": 60,
                    },
                    root / "prompt.json",
                    output_path,
                )

                request_record = json.loads((root / "request.json").read_text(encoding="utf-8"))
                create_response = json.loads((root / "create_response.json").read_text(encoding="utf-8"))
                self.assertEqual(result["lipsync_id"], "lip_123")
                self.assertEqual(result["model"], "speed")
                self.assertEqual(output_path.read_bytes(), b"heygen-video")
                self.assertEqual(fake_requests.calls[0], ("POST", "https://api.heygen.com/v3/assets"))
                self.assertEqual(fake_requests.calls[1], ("POST", "https://api.heygen.com/v3/assets"))
                self.assertIn(("POST", "https://api.heygen.com/v3/lipsyncs"), fake_requests.calls)
                self.assertIn(("GET", "https://api.heygen.com/v3/lipsyncs/lip_123"), fake_requests.calls)
                self.assertEqual(request_record["payload"]["mode"], "speed")
                self.assertEqual(request_record["upload_video_path"], str(video_path))
                self.assertEqual(request_record["upload_audio_path"], str(audio_path))
                self.assertEqual(create_response["body"]["data"]["lipsync_id"], "lip_123")
                self.assertEqual(result["quality_preservation"]["status"], "failed")
        finally:
            module.time.sleep = old_sleep
            module.prepare_media_for_heygen = old_prepare_media
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_heygen_color_metadata_restoration_is_best_effort(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_heygen.py",
            "lipsync_heygen_color_metadata_contract",
        )
        old_video_stream = module.video_stream
        old_run_ffmpeg = module.run_ffmpeg
        streams = [
            {"codec_type": "video", "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"},
            {"codec_type": "video"},
            {"codec_type": "video", "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"},
        ]
        commands: list[list[str]] = []

        def fake_video_stream(_path: Path) -> dict[str, Any]:
            return streams.pop(0)

        def fake_run_ffmpeg(command: list[str], _description: str) -> None:
            commands.append(command)
            Path(command[-1]).write_bytes(b"remuxed")

        module.video_stream = fake_video_stream
        module.run_ffmpeg = fake_run_ffmpeg
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source_path = root / "source.mp4"
                output_path = root / "output.mp4"
                source_path.write_bytes(b"source")
                output_path.write_bytes(b"provider-output")

                result = module.restore_source_color_metadata(source_path, output_path)

                self.assertEqual(result["status"], "restored")
                self.assertEqual(output_path.read_bytes(), b"remuxed")
                self.assertIn("copy", commands[0])
                self.assertIn("bt709", commands[0])
        finally:
            module.video_stream = old_video_stream
            module.run_ffmpeg = old_run_ffmpeg

    def test_heygen_lipsync_normalizes_silent_video_and_mislabeled_wav(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_heygen.py",
            "lipsync_heygen_prepare_media_contract",
        )
        old_media_streams = module.media_streams
        old_media_audio_codec = module.media_audio_codec
        old_media_duration_seconds = module.media_duration_seconds
        old_random_uniform = module.random.uniform
        old_run_ffmpeg = module.run_ffmpeg
        calls: list[dict[str, Any]] = []

        def fake_media_streams(path: Path) -> list[dict[str, Any]]:
            if path.name == "video.mp4":
                return [{"codec_type": "video", "codec_name": "h264"}]
            if path.name.endswith("_heygen_video_with_detection_audio.mp4"):
                return [{"codec_type": "video", "codec_name": "h264"}, {"codec_type": "audio", "codec_name": "aac"}]
            return [{"codec_type": "audio", "codec_name": "mp3"}]

        def fake_media_audio_codec(path: Path) -> str:
            return "mp3" if path.name == "audio.wav" else "pcm_s16le"

        def fake_run_ffmpeg(command: list[str], description: str) -> None:
            calls.append({"command": command, "description": description})
            Path(command[-1]).write_bytes(b"prepared")

        module.media_streams = fake_media_streams
        module.media_audio_codec = fake_media_audio_codec
        module.media_duration_seconds = lambda path: 12.956757 if Path(path).name == "video.mp4" else 12.303628
        module.random.uniform = lambda start, end: (start + end) / 2.0
        module.run_ffmpeg = fake_run_ffmpeg
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_path = root / "audio.wav"
                prepared_dir = root / "prepared"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"mp3-audio")

                prepared_video, prepared_audio, info = module.prepare_media_for_heygen(video_path, audio_path, prepared_dir)

                self.assertTrue(info["video"]["changed"])
                self.assertEqual(info["video"]["reason"], "source_video_missing_audio_stream_attached_sparse_detection_audio")
                self.assertEqual(Path(info["video"]["attached_audio_path"]).name, "audio_heygen_audio_pcm.wav")
                self.assertEqual(info["video"]["source_video_duration_seconds"], 12.956757)
                self.assertEqual(info["video"]["driving_audio_duration_seconds"], 12.303628)
                self.assertAlmostEqual(info["video"]["detection_snippet_duration_seconds"], 2.5913514)
                self.assertAlmostEqual(info["video"]["detection_insert_at_seconds"], 5.1827028)
                self.assertAlmostEqual(info["video"]["detection_audio_source_start_seconds"], 4.8561383)
                self.assertAlmostEqual(info["video"]["detection_audio_source_end_seconds"], 7.4474897)
                self.assertAlmostEqual(info["video"]["detection_audio_tempo"], 1.02)
                self.assertEqual(info["video"]["detection_audio_policy"], "single_random_middle_speech_snippet_20_percent_with_tempo_shift")
                self.assertTrue(info["audio"]["changed"])
                self.assertEqual(info["audio"]["source_codec"], "mp3")
                self.assertEqual(prepared_video.name, "video_heygen_video_with_detection_audio.mp4")
                self.assertEqual(prepared_audio.name, "audio_heygen_audio_pcm.wav")
                self.assertEqual(len(calls), 2)
                filter_text = " ".join(calls[-1]["command"])
                self.assertIn("anullsrc=r=48000:cl=mono:d=12.956757", filter_text)
                self.assertIn("atrim=4.856138:7.447490", filter_text)
                self.assertIn("atempo=1.020", filter_text)
                self.assertIn("adelay=5182:all=1", filter_text)
                self.assertIn(str(prepared_audio), calls[-1]["command"])
        finally:
            module.media_streams = old_media_streams
            module.media_audio_codec = old_media_audio_codec
            module.media_duration_seconds = old_media_duration_seconds
            module.random.uniform = old_random_uniform
            module.run_ffmpeg = old_run_ffmpeg

    def test_heygen_ruleset_proxy_error_retries_with_direct_session(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_heygen.py",
            "lipsync_heygen_ruleset_contract",
        )
        fake_requests = FakeHeyGenRulesetRequests()
        sessions: list[Any] = []

        def make_session():
            session = SimpleNamespace(trust_env=True, request=fake_requests.request, close=lambda: None)
            sessions.append(session)
            return session

        fake_requests_module = SimpleNamespace(
            request=fake_requests.request,
            Session=make_session,
        )
        old_requests = sys.modules.get("requests")
        old_sleep = module.time.sleep
        old_prepare_media = module.prepare_media_for_heygen
        sys.modules["requests"] = fake_requests_module
        module.time.sleep = lambda _seconds: None
        module.prepare_media_for_heygen = lambda video, audio, _working_dir: (video, audio, {"video": {"changed": False}, "audio": {"changed": False}})
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_path = root / "audio.wav"
                output_path = root / "output.mp4"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")

                result = module.generate(
                    {
                        "config": {"provider": "heygen", "model": "speed", "api_key": "heygen-key"},
                        "video_path": str(video_path),
                        "audio_path": str(audio_path),
                        "request_path": str(root / "request.json"),
                        "status_path": str(root / "status.json"),
                        "create_response_path": str(root / "create_response.json"),
                        "timeout_seconds": 60,
                    },
                    root / "prompt.json",
                    output_path,
                )

                self.assertEqual(result["lipsync_id"], "lip_123")
                self.assertTrue(sessions)
                self.assertFalse(sessions[0].trust_env)
                self.assertEqual(output_path.read_bytes(), b"heygen-video")
        finally:
            module.time.sleep = old_sleep
            module.prepare_media_for_heygen = old_prepare_media
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_kling_lipsync_module_uses_05_02_template_and_official_flow(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "video_plan_executor_kling_lipsync_contract",
        )
        module = executor.lipsync_module_for("kling", "kling-lipsync-advanced")
        self.assertTrue(module.__name__.endswith("lipsync_kling"))
        self.assertFalse(executor.should_fit_lipsync_audio_to_video({"provider": "kling"}, {"provider": "grok", "model": "video"}))
        self.assertTrue(executor.should_fit_lipsync_audio_to_video({"provider": "kling"}, {"provider": "wan", "model": "r2v"}))
        self.assertTrue(executor.should_fit_lipsync_audio_to_video({"provider": "chanjing"}, {"provider": "wan", "model": "r2v"}))
        self.assertFalse(executor.should_fit_lipsync_audio_to_video({"provider": "chanjing"}, {"provider": "grok", "model": "video"}))
        self.assertTrue(executor.should_fit_lipsync_audio_to_video({"provider": "heygen"}, {"provider": "grok", "model": "video"}))
        self.assertEqual(executor.lipsync_audio_fit_mode({"provider": "heygen"}, {"provider": "grok", "model": "video"}), "heygen_provider_limit")

        fake_requests = FakeKlingRequests()
        fake_requests_module = SimpleNamespace(
            request=fake_requests.request,
            Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request, close=lambda: None),
        )
        old_requests = sys.modules.get("requests")
        old_safe_download = module.safe_download_to_path
        old_video_duration_seconds = module.video_duration_seconds
        sys.modules["requests"] = fake_requests_module
        safe_download_calls: list[dict[str, Any]] = []

        def fake_safe_download(url: str, path: Path, **kwargs: Any) -> None:
            safe_download_calls.append({"url": url, "kwargs": kwargs})
            Path(path).write_bytes(b"kling-video")

        module.safe_download_to_path = fake_safe_download
        module.video_duration_seconds = lambda _path: 3.25
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                audio_path = root / "audio.wav"
                output_path = root / "output.mp4"
                audio_path.write_bytes(b"audio")

                prompt = module.build_prompt_package({
                    "prompt_dir": str(root),
                    "segment": {"segment_id": "srt_0001", "dialogue_ids": ["d1"]},
                    "video_path": str(root / "raw.mp4"),
                    "source_video_url": "https://cdn.example/source.mp4",
                    "audio_path": str(audio_path),
                    "output_path": str(output_path),
                })
                self.assertEqual(prompt["provider_profile"], "lipsync_kling")
                self.assertGreater(prompt["template_snapshot_chars"], 0)
                self.assertIn("LIPSYNC_KLING_PROMPT", prompt["template_blocks"])

                result = module.generate(
                    {
                        "config": {
                            "provider": "kling",
                            "model": "kling-lipsync-advanced",
                            "api_key": "Bearer test-token",
                            "source_video_url": "https://cdn.example/source.mp4",
                        },
                        "video_path": str(root / "raw.mp4"),
                        "audio_path": str(audio_path),
                        "request_path": str(root / "request.json"),
                        "status_path": str(root / "status.json"),
                        "create_response_path": str(root / "create_response.json"),
                        "timeout_seconds": 60,
                    },
                    root / "prompt.json",
                    output_path,
                )

                request_record = json.loads((root / "request.json").read_text(encoding="utf-8"))
                self.assertEqual(result["task_id"], "kling_lip_123")
                self.assertEqual(result["session_id"], "session_123")
                self.assertEqual(output_path.read_bytes(), b"kling-video")
                self.assertEqual(request_record["video_url"], "https://cdn.example/source.mp4")
                self.assertEqual(request_record["requested_sound_end_time_ms"], 3250)
                self.assertEqual(request_record["sound_end_time_ms"], 3200)
                self.assertEqual(request_record["time_boundary_guard_ms"], 50)
                create_payload = fake_requests.payloads[1]
                self.assertIn("session_id", create_payload)
                self.assertIn("face_choose", create_payload)
                self.assertNotIn("faceChoose", create_payload)
                face_choose = create_payload["face_choose"][0]
                self.assertEqual(face_choose["face_id"], "face_1")
                self.assertEqual(face_choose["sound_insert_time"], 0)
                self.assertEqual(face_choose["sound_start_time"], 0)
                self.assertEqual(face_choose["sound_end_time"], 3200)
                self.assertTrue(face_choose["sound_file"])
                self.assertEqual(safe_download_calls[0]["url"], "https://download.example/kling.mp4")
                self.assertEqual(safe_download_calls[0]["kwargs"]["allowed_content_types"], module.VIDEO_CONTENT_TYPES)
                self.assertEqual(safe_download_calls[0]["kwargs"]["max_bytes"], module.VIDEO_MAX_BYTES)
                self.assertEqual(fake_requests.calls[:3], [
                    ("POST", "https://api-beijing.klingai.com/v1/videos/identify-face"),
                    ("POST", "https://api-beijing.klingai.com/v1/videos/advanced-lip-sync"),
                    ("GET", "https://api-beijing.klingai.com/v1/videos/advanced-lip-sync/kling_lip_123"),
                ])
                self.assertEqual(module.kling_poll_timeout_seconds({"timeout_seconds": 1800}, {}), 7200)
        finally:
            module.safe_download_to_path = old_safe_download
            module.video_duration_seconds = old_video_duration_seconds
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_heygen_lipsync_audio_fit_only_runs_outside_provider_duration_limit(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "video_plan_executor_heygen_audio_fit_contract",
        )
        old_duration = executor.media_duration_seconds
        old_run = executor.subprocess.run
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "video.mp4"
                audio_ok_path = root / "audio_ok.wav"
                audio_short_path = root / "audio_short.wav"
                output_ok_path = root / "fit_ok.wav"
                output_short_path = root / "fit_short.wav"
                for path in (video_path, audio_ok_path, audio_short_path):
                    path.write_bytes(b"media")

                durations = {
                    str(video_path): 5.041667,
                    str(audio_ok_path): 4.911,
                    str(audio_short_path): 4.127344,
                    str(output_short_path): 5.041667,
                }
                executor.media_duration_seconds = lambda path: durations[str(path)]

                def unexpected_run(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
                    raise AssertionError("ffmpeg should not run when HeyGen duration ratio is within the provider limit")

                executor.subprocess.run = unexpected_run
                fitted_ok, meta_ok = executor.fit_audio_to_video_duration(
                    root,
                    audio_ok_path,
                    video_path,
                    output_ok_path,
                    mode="heygen_provider_limit",
                )
                self.assertEqual(fitted_ok, audio_ok_path)
                self.assertFalse(meta_ok["applied"])
                self.assertFalse(output_ok_path.exists())

                def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
                    Path(command[-1]).write_bytes(b"fitted audio")
                    return SimpleNamespace(returncode=0, stderr="", stdout="")

                executor.subprocess.run = fake_run
                fitted_short, meta_short = executor.fit_audio_to_video_duration(
                    root,
                    audio_short_path,
                    video_path,
                    output_short_path,
                    mode="heygen_provider_limit",
                )
                self.assertEqual(fitted_short, output_short_path)
                self.assertTrue(meta_short["applied"])
                self.assertEqual(meta_short["source"], "heygen_lipsync_audio_duration_fit")
                self.assertGreater(meta_short["difference_ratio"], executor.HEYGEN_LIPSYNC_MAX_DURATION_DIFFERENCE_RATIO)
                self.assertTrue(output_short_path.exists())
        finally:
            executor.media_duration_seconds = old_duration
            executor.subprocess.run = old_run

    def test_kling_lipsync_clamps_sound_end_to_local_video_duration(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_kling.py",
            "lipsync_kling_video_duration_guard_contract",
        )

        class FakeKlingRequestsWithLongFace(FakeKlingRequests):
            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                if method == "POST" and url == "https://api-beijing.klingai.com/v1/videos/identify-face":
                    self.calls.append((method, url))
                    payload = kwargs.get("json")
                    if isinstance(payload, dict):
                        self.payloads.append(payload)
                    return FakeResponse(200, {"code": 0, "data": {"session_id": "session_123", "face_data": [{"face_id": "face_1", "start_time": 0, "end_time": 12300}]}})
                return super().request(method, url, **kwargs)

        fake_requests = FakeKlingRequestsWithLongFace()
        fake_requests_module = SimpleNamespace(
            request=fake_requests.request,
            Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request, close=lambda: None),
        )
        old_requests = sys.modules.get("requests")
        old_safe_download = module.safe_download_to_path
        old_video_duration_seconds = module.video_duration_seconds
        sys.modules["requests"] = fake_requests_module
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "raw.mp4"
                audio_path = root / "audio.wav"
                output_path = root / "output.mp4"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")
                module.video_duration_seconds = lambda path: 12.041667 if Path(path).name == "raw.mp4" else 12.303628
                module.safe_download_to_path = lambda _url, path, **_kwargs: Path(path).write_bytes(b"kling-video")

                result = module.generate(
                    {
                        "config": {
                            "provider": "kling",
                            "model": "kling-lipsync-advanced",
                            "api_key": "Bearer test-token",
                            "source_video_url": "https://cdn.example/source.mp4",
                        },
                        "video_path": str(video_path),
                        "audio_path": str(audio_path),
                        "request_path": str(root / "request.json"),
                        "status_path": str(root / "status.json"),
                        "create_response_path": str(root / "create_response.json"),
                        "timeout_seconds": 60,
                    },
                    root / "prompt.json",
                    output_path,
                )

                request_record = json.loads((root / "request.json").read_text(encoding="utf-8"))
                create_payload = fake_requests.payloads[1]
                self.assertEqual(request_record["audio_duration_ms_floor"], 12303)
                self.assertEqual(request_record["source_video_duration_ms_floor"], 12041)
                self.assertEqual(request_record["face_end_time_ms"], 12300)
                self.assertEqual(request_record["sound_end_time_ms"], 11991)
                self.assertEqual(create_payload["face_choose"][0]["sound_end_time"], 11991)
                self.assertEqual(result["sound_end_time_ms"], 11991)
        finally:
            module.safe_download_to_path = old_safe_download
            module.video_duration_seconds = old_video_duration_seconds
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_kling_lipsync_publishes_local_video_when_source_url_is_missing(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_kling.py",
            "lipsync_kling_local_publish_contract",
        )
        fake_requests = FakeKlingRequests()
        fake_requests_module = SimpleNamespace(
            request=fake_requests.request,
            Session=lambda: SimpleNamespace(trust_env=True, request=fake_requests.request, close=lambda: None),
        )
        old_requests = sys.modules.get("requests")
        old_reference_video_public_url = module.reference_video_public_url
        old_safe_download = module.safe_download_to_path
        old_video_duration_seconds = module.video_duration_seconds
        sys.modules["requests"] = fake_requests_module
        safe_download_calls: list[dict[str, Any]] = []
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                video_path = root / "raw.mp4"
                audio_path = root / "audio.wav"
                output_path = root / "output.mp4"
                video_path.write_bytes(b"video")
                audio_path.write_bytes(b"audio")
                module.reference_video_public_url = lambda _path, _config: "https://tmpfiles.org/dl/example/raw.mp4"
                module.video_duration_seconds = lambda _path: 4.0

                def fake_safe_download(url: str, path: Path, **kwargs: Any) -> None:
                    safe_download_calls.append({"url": url, "kwargs": kwargs})
                    Path(path).write_bytes(b"kling-video")

                module.safe_download_to_path = fake_safe_download

                result = module.generate(
                    {
                        "config": {
                            "provider": "kling",
                            "model": "kling-lipsync-advanced",
                            "api_key": "Bearer test-token",
                        },
                        "video_path": str(video_path),
                        "audio_path": str(audio_path),
                        "request_path": str(root / "request.json"),
                        "status_path": str(root / "status.json"),
                        "create_response_path": str(root / "create_response.json"),
                        "timeout_seconds": 60,
                    },
                    root / "prompt.json",
                    output_path,
                )

                request_record = json.loads((root / "request.json").read_text(encoding="utf-8"))
                self.assertEqual(result["published_asset"]["provider"], "tmpfiles")
                self.assertEqual(request_record["video_url"], "https://tmpfiles.org/dl/example/raw.mp4")
                self.assertEqual(result["sound_end_time_ms"], 3950)
                self.assertEqual(safe_download_calls[0]["kwargs"]["allowed_content_types"], module.VIDEO_CONTENT_TYPES)
                self.assertEqual(safe_download_calls[0]["kwargs"]["max_bytes"], module.VIDEO_MAX_BYTES)
                self.assertEqual(output_path.read_bytes(), b"kling-video")
        finally:
            module.reference_video_public_url = old_reference_video_public_url
            module.safe_download_to_path = old_safe_download
            module.video_duration_seconds = old_video_duration_seconds
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

    def test_kling_omni_publishes_local_reference_video_before_request(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_kling.py",
            "video_kling_reference_publish_contract",
        )
        old_upload = module.tmpfiles_upload_video
        old_post = module.post_json_request
        old_poll = module.poll_task
        old_download = module.download_video
        captured: dict[str, Any] = {}
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                prompt_path = root / "prompt.json"
                image_path = root / "first.png"
                reference_video = root / "reference_video_10s.mp4"
                output_path = root / "output.mp4"
                prompt_path.write_text(json.dumps({"prompt": "测试 Kling Omni"}, ensure_ascii=False), encoding="utf-8")
                image_path.write_bytes(b"png")
                reference_video.write_bytes(b"video")
                module.tmpfiles_upload_video = lambda _path, _config: "https://tmpfiles.org/dl/example/reference_video_10s.mp4"

                def fake_post(_url: str, payload: dict[str, Any], _headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
                    del timeout
                    captured["payload"] = payload
                    return {"data": {"task_id": "kling_video_123"}, "task_status": "submitted"}

                module.post_json_request = fake_post
                module.poll_task = lambda *_args, **_kwargs: ("https://cdn.example/kling-output.mp4", {"data": {"task_status": "succeed"}})
                module.download_video = lambda _url, path: Path(path).write_bytes(b"kling-omni")

                result = module.generate(
                    {
                        "config": {
                            "provider": "kling",
                            "model": "kling-v3-omni",
                            "api_key": "Bearer test-token",
                            "public_asset_provider": "tmpfiles",
                            "sound": "on",
                        },
                        "reference_images": [str(image_path)],
                        "reference_videos": [str(reference_video)],
                        "duration_seconds": 10,
                        "timeout_seconds": 60,
                    },
                    prompt_path,
                    output_path,
                )

                self.assertEqual(captured["payload"]["video_list"][0]["video_url"], "https://tmpfiles.org/dl/example/reference_video_10s.mp4")
                self.assertEqual(captured["payload"]["video_list"][0]["refer_type"], "feature")
                self.assertEqual(captured["payload"]["video_list"][0]["keep_original_sound"], "no")
                self.assertEqual(captured["payload"]["sound"], "off")
                self.assertEqual(result["published_assets"][0]["provider"], "tmpfiles")
                self.assertEqual(output_path.read_bytes(), b"kling-omni")
        finally:
            module.tmpfiles_upload_video = old_upload
            module.post_json_request = old_post
            module.poll_task = old_poll
            module.download_video = old_download

    def test_executor_extracts_raw_video_tail_frame_when_lipsync_fails_after_video_generation(self) -> None:
        source = (REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py").read_text(
            encoding="utf-8",
        )

        self.assertIn("raw_video_tail_after_lipsync_failure", source)
        self.assertIn("tail_frame_extracted_from_raw_video_after_lipsync_failure", source)
        self.assertIn('tracker.step(segment_id, "sync", "failed", error=str(exc))', source)
        self.assertIn("publish_segment_tail_frame(", source)

    def test_no_executable_plan_reports_original_skipped_reason(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "video_plan_executor_no_executable_reason_contract",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            write_json(workspace / "SessionContext" / "Variables.json", {})
            write_json(
                workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json",
                {
                    "shots": [{
                        "shot_id": "shot_001",
                        "scenes": [{
                            "scene_id": "scene_001",
                            "dialogue_items": [{
                                "srt_id": "srt_0001",
                                "dialogue_asset_key": "dialogue_001",
                                "dialogue": "测试口播",
                                "start": 0,
                                "end": 8,
                            }],
                        }],
                    }],
                },
            )
            write_json(
                workspace / "SessionOutput" / "storyboard" / "video_generation_plan.json",
                {
                    "summary": {"segment_count": 1, "need_video_count": 0, "skipped_scene_count": 1},
                    "shots": [{
                        "shot_id": "shot_001",
                        "status": "completed_with_skipped_items",
                        "scenes": [{
                            "scene_id": "scene_001",
                            "status": "skipped",
                            "skipped_reason": {
                                "code": "first_scene_missing_visual_source",
                                "message": "The first scene has no visual source at its first dialogue.",
                            },
                            "segments": [{
                                "segment_id": "shot_001_scene_001_segment_001",
                                "asset_key": "dialogue_001",
                                "status": "skipped",
                                "skipped_reason": {
                                    "code": "first_scene_missing_visual_source",
                                    "message": "The first scene has no visual source at its first dialogue.",
                                },
                                "tasks": {"need_video": False},
                            }],
                        }],
                    }],
                },
            )

            result = module.run(module.Args(
                workspace=str(workspace),
                database_url="",
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
                provider_timeout_seconds=60,
                execution_job_id="test_job",
                source_plan_hash="",
                print_json=False,
                execute_audio_video_sync=True,
            ))

            self.assertEqual(result["status"], "blocked")
            message = result["blocked_reasons"][0]["message"]
            self.assertIn("plan_has_no_executable_segments", message)
            self.assertIn("first_scene_missing_visual_source", message)
            self.assertIn("人物形象", message)


if __name__ == "__main__":
    unittest.main()
