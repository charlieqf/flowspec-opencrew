from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "lipsync_heygen.py"
EXECUTOR_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py"


def load_module(path: Path = MODULE_PATH, name: str = "lipsync_heygen_resume_contract") -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise AssertionError("could not load lipsync_heygen module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None, chunks: list[bytes] | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}
        self.chunks = chunks or []
        self.headers: dict[str, str] = {}
        self.text = json.dumps(self.payload)

    def json(self) -> dict[str, Any]:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int) -> Any:
        del chunk_size
        yield from self.chunks

    def close(self) -> None:
        return None


class KouboStoryboardHeyGenResumeContractTest(unittest.TestCase):
    def test_terminal_failed_lipsync_id_is_not_resumed(self) -> None:
        module = load_module(name="lipsync_heygen_terminal_resume_contract")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video_path = root / "raw.mp4"
            audio_path = root / "audio.wav"
            request_path = root / "request.json"
            status_path = root / "status.json"
            response_path = root / "response.json"
            video_path.write_bytes(b"raw")
            audio_path.write_bytes(b"audio")
            request_path.write_text(json.dumps({
                "mode": "precision",
                "video_path": str(video_path),
                "audio_path": str(audio_path),
                "video_size_bytes": video_path.stat().st_size,
                "audio_size_bytes": audio_path.stat().st_size,
            }), encoding="utf-8")
            status_path.write_text(json.dumps({
                "lipsync_id": "failed-1",
                "latest": {"body": {"data": {"status": "failed", "failure_message": "Insufficient credit"}}},
            }), encoding="utf-8")
            response_path.write_text(json.dumps({"body": {"data": {"lipsync_id": "failed-1"}}}), encoding="utf-8")

            candidate = module.resumable_lipsync_id(request_path, status_path, response_path, video_path, audio_path, "precision")

            self.assertEqual(candidate, "")

    def test_changed_lipsync_input_is_not_resumed(self) -> None:
        module = load_module(name="lipsync_heygen_changed_input_resume_contract")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video_path = root / "raw.mp4"
            audio_path = root / "audio.wav"
            request_path = root / "request.json"
            status_path = root / "status.json"
            response_path = root / "response.json"
            video_path.write_bytes(b"new-raw")
            audio_path.write_bytes(b"audio")
            request_path.write_text(json.dumps({
                "mode": "precision",
                "video_path": str(video_path),
                "audio_path": str(audio_path),
                "video_size_bytes": video_path.stat().st_size,
                "audio_size_bytes": audio_path.stat().st_size,
                "video_sha256": "0" * 64,
            }), encoding="utf-8")
            status_path.write_text(json.dumps({"lipsync_id": "stale-1"}), encoding="utf-8")

            candidate = module.resumable_lipsync_id(request_path, status_path, response_path, video_path, audio_path, "precision")

            self.assertEqual(candidate, "")

    def test_existing_lipsync_id_resumes_without_create_post(self) -> None:
        module = load_module()
        calls: list[tuple[str, str]] = []
        output_url = "https://cdn.example.test/lipsync.mp4"

        def fake_request(method: str, url: str, **_kwargs: Any) -> FakeResponse:
            calls.append((method, url))
            if method == "GET" and url.endswith("/v3/lipsyncs/resume-1"):
                return FakeResponse(200, {"data": {"id": "resume-1", "status": "completed", "video_url": output_url}})
            if method == "GET" and url == output_url:
                return FakeResponse(200, {}, [b"final-video"])
            raise AssertionError(f"unexpected request: {method} {url}")

        fake_requests = SimpleNamespace(request=fake_request)
        original_requests = sys.modules.get("requests")
        sys.modules["requests"] = fake_requests
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                video_path = root / "raw.mp4"
                audio_path = root / "audio.wav"
                output_path = root / "out.mp4"
                request_path = root / "ModelCall_asset_LipSync_request.json"
                status_path = root / "ModelCall_asset_LipSync_status.json"
                response_path = root / "ModelCall_asset_LipSync_response.json"
                video_path.write_bytes(b"raw")
                audio_path.write_bytes(b"audio")
                request_path.write_text(
                    json.dumps({"mode": "precision", "video_path": str(video_path), "audio_path": str(audio_path)}),
                    encoding="utf-8",
                )
                status_path.write_text(json.dumps({"lipsync_id": "resume-1"}), encoding="utf-8")

                result = module.generate(
                    {
                        "config": {"provider": "heygen", "model": "precision", "api_key": "test-key"},
                        "video_path": str(video_path),
                        "audio_path": str(audio_path),
                        "output_path": str(output_path),
                        "request_path": str(request_path),
                        "status_path": str(status_path),
                        "create_response_path": str(response_path),
                        "timeout_seconds": 60,
                    },
                    request_path,
                    output_path,
                )

                self.assertTrue(result["resumed"])
                self.assertEqual(output_path.read_bytes(), b"final-video")
                self.assertNotIn("POST", [method for method, _url in calls])
        finally:
            if original_requests is not None:
                sys.modules["requests"] = original_requests
            else:
                sys.modules.pop("requests", None)

    def test_force_reset_preserves_lipsync_provider_state_files(self) -> None:
        module = load_module(EXECUTOR_PATH, "video_plan_executor_lipsync_resume_contract")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            prompt_dir = workspace / "S9_05_02_VideoPlanExecutor" / "Prompt"
            prompt_dir.mkdir(parents=True)
            state_file = prompt_dir / "ModelCall_asset_LipSync_status.json"
            request_file = prompt_dir / "ModelCall_asset_LipSync_request.json"
            unrelated_file = prompt_dir / "ModelCall_asset_Image_status.json"
            state_file.write_text('{"lipsync_id":"resume-1"}', encoding="utf-8")
            request_file.write_text('{"mode":"precision"}', encoding="utf-8")
            unrelated_file.write_text("{}", encoding="utf-8")

            result: dict[str, Any] = {}
            module.force_reset(workspace, result)

            self.assertEqual(state_file.read_text(encoding="utf-8"), '{"lipsync_id":"resume-1"}')
            self.assertEqual(request_file.read_text(encoding="utf-8"), '{"mode":"precision"}')
            self.assertFalse(unrelated_file.exists())
            self.assertEqual(result["cleanup_actions"][0]["preserved_lipsync_state_files"], 2)


if __name__ == "__main__":
    unittest.main()
