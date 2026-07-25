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
        finally:
            module.time.sleep = old_sleep
            module.prepare_media_for_heygen = old_prepare_media
            if old_requests is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = old_requests

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


if __name__ == "__main__":
    unittest.main()
