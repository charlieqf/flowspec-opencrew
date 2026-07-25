from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.media_library_analysis.dialogue import (  # noqa: E402
    dialogue_failure_code,
    enrich_dialogue_progress_timing,
    load_dialogue_result,
    summarize_dialogue_output,
    tool_result_failure_message,
)
from ToolLibrary.OpenCut_V1.framework_bridge import _prepare_legacy_context  # noqa: E402


class OpenCutV1DialogueContractTest(unittest.TestCase):
    def test_legacy_context_hard_links_large_media_but_copies_mutable_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = root / "0_SessionContext"
            context.mkdir()
            source_video = context / "Video_Source.mp4"
            variables = context / "Variables.json"
            source_video.write_bytes(b"video")
            variables.write_text('{"schema_version":"1.0"}', encoding="utf-8")

            _prepare_legacy_context(root)

            legacy_video = root / "SessionContext" / "Video_Source.mp4"
            legacy_variables = root / "SessionContext" / "Variables.json"
            self.assertTrue(legacy_video.samefile(source_video))
            self.assertNotEqual(legacy_variables.stat().st_ino, variables.stat().st_ino)
            self.assertEqual(legacy_variables.read_text(encoding="utf-8"), variables.read_text(encoding="utf-8"))

    def test_legacy_context_safely_copies_media_when_hard_link_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = root / "0_SessionContext"
            context.mkdir()
            source_video = context / "Video_Source.mp4"
            source_video.write_bytes(b"video")

            with patch("ToolLibrary.OpenCut_V1.framework_bridge.os.link", side_effect=OSError("cross-device")):
                _prepare_legacy_context(root)

            legacy_video = root / "SessionContext" / "Video_Source.mp4"
            self.assertFalse(legacy_video.samefile(source_video))
            self.assertEqual(legacy_video.read_bytes(), source_video.read_bytes())

    def test_blocked_dependency_message_exposes_cloud_asr_consent_action(self) -> None:
        result = SimpleNamespace(
            status="blocked",
            errors=[],
            outputs={
                "dependency_check": {
                    "status": "blocked",
                    "missing_dependencies": [
                        {
                            "kind": "cloud_asr_data_transfer_not_authorized",
                            "suggested_action": "developer-only fallback message",
                        }
                    ],
                }
            },
        )

        message = tool_result_failure_message(result, "02_01")

        self.assertIn("cloud_asr_data_transfer_not_authorized", message)
        self.assertIn("允许本次运行使用云端 ASR", message)
        self.assertNotIn("工具 02_01 返回 blocked", message)

    def test_failed_tool_message_still_uses_direct_errors(self) -> None:
        result = SimpleNamespace(status="failed", errors=["ASR provider unavailable"], outputs={})

        self.assertEqual(tool_result_failure_message(result, "02_01"), "ASR provider unavailable")

    def test_no_audio_block_is_a_specific_chinese_business_state(self) -> None:
        result = SimpleNamespace(
            status="blocked",
            errors=[],
            outputs={
                "dependency_check": {
                    "status": "blocked",
                    "missing_dependencies": [
                        {
                            "kind": "video_has_no_audio",
                            "suggested_action": "Video metadata says the source video has no audio track.",
                        }
                    ],
                }
            },
        )

        message = tool_result_failure_message(result, "02_01")

        self.assertIn("源视频没有音轨", message)
        self.assertIn("画面结构和视觉语义分析", message)
        self.assertNotIn("Video metadata says", message)
        self.assertEqual(
            dialogue_failure_code(message, blocked=True),
            "video_has_no_audio",
        )

    def test_registry_is_independent_and_contains_no_rewrite_tools(self) -> None:
        root = REPO_ROOT / "ToolLibrary" / "OpenCut_V1"
        registry = json.loads((root / "tool_registry.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [tool["id"] for tool in registry["tools"]],
            [
                "00",
                "01",
                "02_01",
                "02_02",
                "03_01",
                "03_02",
                "03_03",
                "04_01",
            ],
        )
        self.assertFalse(any("rewrite" in json.dumps(tool).lower() for tool in registry["tools"]))
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("ToolLibrary.Analysis_V1", source, path)
            self.assertNotIn("ToolLibrary.Analysis.", source, path)

    def test_analysis_compatible_final_items_are_one_sentence_one_fragment(self) -> None:
        module_path = REPO_ROOT / "ToolLibrary" / "OpenCut_V1" / "02_02_VideoSRTFrame.py"
        spec = importlib.util.spec_from_file_location("open_cut_v1_video_srt_frame", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        payload = module.build_final_srt_frame_items({
            "items": [
                {"sentence_id": "srt_0001_01", "text": "第一句话", "frame_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg", "start": 0, "end": 1.2},
                {"sentence_id": "srt_0001_02", "text": "第二句话", "frame_path": "SessionOutput/visual/srt_frames/srt_0001_02.jpg", "start": 1.2, "end": 2.5},
            ]
        })
        self.assertEqual([item["srt_id"] for item in payload["items"]], ["srt_0001_01", "srt_0001_02"])
        self.assertEqual(payload["items"][1]["duration"], 1.3)
        self.assertNotIn("rewritten", json.dumps(payload).lower())

    def test_backend_maps_real_tool_output_to_detail_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            tool_id = "tus_dialogue_test"
            root = workspace / "tool_use_sessions" / tool_id
            subtitle = root / "SessionOutput" / "subtitle"
            frames = root / "SessionOutput" / "visual" / "srt_frames"
            subtitle.mkdir(parents=True)
            frames.mkdir(parents=True)
            (frames / "srt_0001.jpg").write_bytes(b"jpeg")
            (subtitle / "final_srt_frame_items.json").write_text(json.dumps({
                "items": [{
                    "srt_id": "srt_0001",
                    "dialogue": "保留原始对白",
                    "image_path": "SessionOutput/visual/srt_frames/srt_0001.jpg",
                    "start": 0.1,
                    "end": 2.2,
                    "duration": 2.1,
                }]
            }, ensure_ascii=False), encoding="utf-8")
            (subtitle / "calibrated_srt_items.json").write_text(json.dumps({
                "items": [{
                    "sentence_id": "srt_0001",
                    "ocr_text": "保留原始对白",
                    "ocr_confidence": 0.93,
                    "frame_time": 1.1,
                    "calibration": {"needs_review": False},
                }]
            }, ensure_ascii=False), encoding="utf-8")

            result = load_dialogue_result(workspace=workspace, session_id=53, tool_use_session_id=tool_id, preview_url="/video")
            self.assertIsNone(result["error"])
            self.assertEqual(result["items"][0]["fragment_id"], "srt_0001")
            self.assertEqual(result["items"][0]["title"], "对白片段 1")
            self.assertEqual(result["items"][0]["preview_url"], "/video")
            self.assertIn(f"tool_use_sessions/{tool_id}/SessionOutput/visual/srt_frames/srt_0001.jpg", result["items"][0]["keyframes"][0]["image_url"])
            summary = summarize_dialogue_output(root)
            self.assertEqual(summary["fragment_count"], 1)
            self.assertEqual(summary["subtitle_mode"], "embedded")

    def test_historical_dialogue_run_timing_is_backfilled_from_tool_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            tool_id = "tus_1783989151814_contract"
            state_dir = workspace / "tool_use_sessions" / tool_id / "S3_02_02_VideoSRTFrame"
            state_dir.mkdir(parents=True)
            (state_dir / "State.json").write_text(json.dumps({
                "status": "completed",
                "finished_at": "2026-07-14T00:32:55.384000+00:00",
            }), encoding="utf-8")

            timing = enrich_dialogue_progress_timing(
                workspace=workspace,
                tool_use_session_id=tool_id,
                progress={"step": "completed", "label": "对白分析已完成"},
            )

            self.assertEqual(timing["started_at"], 1783989151814)
            self.assertEqual(timing["finished_at"], 1783989175384)
            self.assertEqual(timing["elapsed_ms"], 23570)


if __name__ == "__main__":
    unittest.main()
