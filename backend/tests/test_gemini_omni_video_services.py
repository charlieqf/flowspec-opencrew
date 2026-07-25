from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.koubo.koubo_storyboard.gemini_omni_video_services import (  # noqa: E402
    GEMINI_OMNI_MODEL,
    GeminiOmniClient,
    GeminiOmniError,
    build_interaction_request,
    extract_video_parts,
    file_input,
    gemini_omni_enabled,
    map_provider_error,
    materialize_video_output,
    omni_task_for,
)


MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"0" * 128


def valid_probe(_path: Path):
    return {"has_video": True, "width": 1280, "height": 720, "duration_seconds": 3.0}


class ScriptedClient(GeminiOmniClient):
    def __init__(self, responses, **kwargs):
        super().__init__("test-key", sleep=lambda _seconds: None, **kwargs)
        self.responses = list(responses)
        self.requests = []

    def _request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class GeminiOmniVideoServicesTest(unittest.TestCase):
    def test_feature_flag_defaults_off(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(gemini_omni_enabled())
        with patch.dict(os.environ, {"OPENCREW_GEMINI_OMNI_ENABLED": "1"}, clear=True):
            self.assertTrue(gemini_omni_enabled())

    def test_task_selection_and_continuation_omits_rejected_task_config(self) -> None:
        scenarios = [
            ("generate", 0, 0, "text_to_video"),
            ("generate", 1, 0, "image_to_video"),
            ("generate", 2, 0, "reference_to_video"),
            ("edit", 0, 1, "edit"),
            ("continue", 0, 0, "edit"),
        ]
        for operation, image_count, video_count, expected in scenarios:
            task = omni_task_for(operation, image_count=image_count, video_count=video_count)
            self.assertEqual(task, expected)
            payload = build_interaction_request(
                prompt="Test prompt",
                task=task,
                aspect_ratio="16:9",
                file_inputs=[{"type": "document", "uri": "https://example.invalid/file", "mime_type": "video/mp4"}] if video_count else [],
                previous_interaction_id="provider-parent" if operation == "continue" else "",
                duration_seconds=3,
            )
            self.assertEqual(payload["model"], GEMINI_OMNI_MODEL)
            if operation in {"edit", "continue"}:
                self.assertNotIn("generation_config", payload)
            else:
                self.assertEqual(payload["generation_config"]["video_config"]["task"], expected)
                self.assertEqual(payload["generation_config"]["video_config"], {"task": expected})
            if operation == "edit":
                self.assertEqual(payload["response_format"], {"type": "video", "delivery": "uri"})
                self.assertNotIn("duration", payload["response_format"])
            else:
                self.assertEqual(payload["response_format"]["aspect_ratio"], "16:9")
                self.assertEqual(payload["response_format"]["duration"], "3s")
            self.assertTrue(payload["store"])
            self.assertTrue(payload["background"])

    def test_uri_or_continuation_with_store_false_is_rejected_locally(self) -> None:
        with self.assertRaises(GeminiOmniError) as uri_error:
            build_interaction_request(
                prompt="Test",
                task="text_to_video",
                aspect_ratio="16:9",
                delivery="uri",
                store=False,
            )
        self.assertEqual(uri_error.exception.code, "gemini_omni_store_required")

        with self.assertRaises(GeminiOmniError) as continuation_error:
            build_interaction_request(
                prompt="Test",
                task="edit",
                aspect_ratio="9:16",
                delivery="inline",
                store=False,
                previous_interaction_id="private-parent",
            )
        self.assertEqual(continuation_error.exception.code, "gemini_omni_store_required")

    def test_duration_outside_current_provider_range_is_rejected_locally(self) -> None:
        for duration in (1, 2, 11):
            with self.subTest(duration=duration), self.assertRaises(GeminiOmniError) as error:
                build_interaction_request(
                    prompt="Test",
                    task="text_to_video",
                    aspect_ratio="16:9",
                    duration_seconds=duration,
                )
            self.assertEqual(error.exception.code, "video_stateful_invalid_request")

    def test_files_api_waits_from_processing_to_active(self) -> None:
        client = ScriptedClient(
            [
                (200, {}, {"x-goog-upload-url": "https://upload.example.test/session"}),
                (200, {"file": {"name": "files/input-1", "state": "PROCESSING"}}, {}),
                (200, {"file": {"name": "files/input-1", "state": "PROCESSING"}}, {}),
                (200, {"file": {"name": "files/input-1", "state": "ACTIVE", "uri": "https://generativelanguage.googleapis.com/v1beta/files/input-1"}}, {}),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.mp4"
            source.write_bytes(MP4_BYTES)
            result = client.upload_file(source, interval_seconds=0)
        self.assertEqual(result["name"], "files/input-1")
        self.assertIn("/files/input-1", result["uri"])
        self.assertEqual([item[0] for item in client.requests], ["POST", "POST", "GET", "GET"])
        self.assertEqual(client.requests[1][2]["headers"]["X-Goog-Upload-Command"], "upload, finalize")
        self.assertEqual(
            file_input(result, media_type="video"),
            {
                "type": "document",
                "uri": "https://generativelanguage.googleapis.com/v1beta/files/input-1",
                "mime_type": "video/mp4",
            },
        )

    def test_files_api_failed_and_timeout_are_stable_errors(self) -> None:
        failed = ScriptedClient(
            [
                (200, {}, {"x-goog-upload-url": "https://upload.example.test/session"}),
                (200, {"file": {"name": "files/input-1"}}, {}),
                (200, {"file": {"name": "files/input-1", "state": "FAILED"}}, {}),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.mp4"
            source.write_bytes(MP4_BYTES)
            with self.assertRaises(GeminiOmniError) as error:
                failed.upload_file(source, interval_seconds=0)
        self.assertEqual(error.exception.code, "gemini_omni_file_processing_failed")

    def test_interaction_id_is_persisted_before_polling_and_not_returned_by_output_helper(self) -> None:
        callbacks = []
        client = ScriptedClient(
            [
                (200, {"id": "private-interaction", "status": "pending"}, {}),
                (200, {"id": "private-interaction", "status": "completed", "output": {"type": "video", "data": base64.b64encode(MP4_BYTES).decode("ascii")}}, {}),
            ]
        )
        result = client.run_interaction(
            {"model": GEMINI_OMNI_MODEL},
            interaction_callback=lambda interaction_id, expires_at, source: callbacks.append((interaction_id, expires_at, source)),
        )
        self.assertEqual(callbacks, [("private-interaction", None, "unknown")])
        self.assertEqual(result["status"], "completed")

    def test_poll_failure_cancel_and_timeout(self) -> None:
        failed = ScriptedClient([(200, {"status": "failed", "error": {"message": "generation failed"}}, {})])
        with self.assertRaises(GeminiOmniError):
            failed.poll_interaction("private", interval_seconds=0)

        times = iter([0.0, 2.0])
        timeout = ScriptedClient([], clock=lambda: next(times))
        with self.assertRaises(GeminiOmniError) as error:
            timeout.poll_interaction("private", timeout_seconds=1, interval_seconds=0)
        self.assertEqual(error.exception.status_code, 504)

    def test_inline_and_uri_outputs_use_shared_sanitize_sink_before_atomic_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inline_output = root / "inline.mp4"
            sanitized = []
            inline_payload = {
                "output": {
                    "type": "video",
                    "mime_type": "video/mp4",
                    "data": base64.b64encode(MP4_BYTES).decode("ascii"),
                }
            }
            inline_meta = materialize_video_output(
                inline_payload,
                inline_output,
                api_key="secret",
                download_video_binary=lambda *_args, **_kwargs: self.fail("inline must not download"),
                sanitize_video_output=lambda path: sanitized.append(path),
                probe=valid_probe,
            )
            self.assertEqual(inline_meta["delivery"], "inline")
            self.assertEqual(inline_output.read_bytes(), MP4_BYTES)
            self.assertEqual(len(sanitized), 1)
            self.assertEqual(sanitized[0].suffix, ".mp4")

            uri_output = root / "uri.mp4"
            downloads = []

            def download(uri, path, headers, **kwargs):
                downloads.append((uri, headers, kwargs))
                self.assertEqual(path.suffix, ".mp4")
                path.write_bytes(MP4_BYTES)

            uri_meta = materialize_video_output(
                {"output": {"type": "video", "uri": "https://generativelanguage.googleapis.com/v1beta/download/output"}},
                uri_output,
                api_key="secret",
                download_video_binary=download,
                sanitize_video_output=lambda _path: self.fail("shared downloader owns URI sanitization"),
                probe=valid_probe,
            )
            self.assertEqual(uri_meta["delivery"], "uri")
            self.assertEqual(downloads[0][1], {"x-goog-api-key": "secret"})
            self.assertTrue(uri_output.exists())

    def test_invalid_download_host_and_non_video_output_are_rejected_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "existing.mp4"
            output.write_bytes(MP4_BYTES)
            with self.assertRaises(GeminiOmniError):
                materialize_video_output(
                    {"type": "video", "uri": "https://attacker.example/video.mp4"},
                    output,
                    api_key="secret",
                    download_video_binary=lambda *_args, **_kwargs: None,
                    sanitize_video_output=lambda _path: None,
                    probe=valid_probe,
                )
            self.assertEqual(output.read_bytes(), MP4_BYTES)

        self.assertEqual(extract_video_parts({"output": {"type": "text", "data": "not-video"}}), [])

    def test_sanitize_failure_does_not_expose_local_paths_or_ffmpeg_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output.mp4"
            payload = {
                "output": {
                    "type": "video",
                    "mime_type": "video/mp4",
                    "data": base64.b64encode(MP4_BYTES).decode("ascii"),
                }
            }
            with self.assertRaises(GeminiOmniError) as error:
                materialize_video_output(
                    payload,
                    output,
                    api_key="secret",
                    download_video_binary=lambda *_args, **_kwargs: None,
                    sanitize_video_output=lambda _path: (_ for _ in ()).throw(
                        RuntimeError("ffmpeg failed at /Users/private/workspace/output.mp4")
                    ),
                    probe=valid_probe,
                )
            self.assertEqual(error.exception.code, "gemini_omni_video_output_missing")
            self.assertNotIn("/Users/", error.exception.message)
            self.assertNotIn("ffmpeg", error.exception.message.lower())
            self.assertFalse(output.exists())

    def test_error_mapping_uses_stable_codes_and_redacts_provider_identifiers(self) -> None:
        expired = map_provider_error(404, {"error": {"message": "interaction interactions/private-123 not found"}})
        self.assertEqual(expired.code, "gemini_omni_interaction_expired")
        self.assertNotIn("private-123", expired.message)
        filtered = map_provider_error(400, {"error": {"status": "CONTENT_FILTERED", "message": "blocked prompt"}})
        self.assertEqual(filtered.code, "gemini_omni_content_filtered")
        retryable = map_provider_error(429, {"error": {"message": "rate limited"}})
        self.assertTrue(retryable.retryable)
        uploaded_edit_duration = map_provider_error(
            500,
            {"error": {"message": "Duration cannot be set in response format for edit task."}},
        )
        self.assertEqual(uploaded_edit_duration.code, "video_stateful_invalid_request")
        self.assertEqual(uploaded_edit_duration.status_code, 400)
        self.assertFalse(uploaded_edit_duration.retryable)
        uploaded_edit_aspect = map_provider_error(
            500,
            {"error": {"message": "Aspect ratio cannot be set in response format for edit task."}},
        )
        self.assertEqual(uploaded_edit_aspect.code, "video_stateful_invalid_request")
        self.assertEqual(uploaded_edit_aspect.status_code, 400)
        missing_edit_video = map_provider_error(
            500,
            {"error": {"message": "Exactly one input video is required for edit task."}},
        )
        self.assertEqual(missing_edit_video.code, "video_stateful_invalid_request")
        self.assertEqual(missing_edit_video.status_code, 400)


if __name__ == "__main__":
    unittest.main()
