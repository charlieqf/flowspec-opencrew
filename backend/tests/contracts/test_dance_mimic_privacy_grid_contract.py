from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "ModelConfig" / "backend"):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from opcrew_backend.koubo.koubo_storyboard.dance_mimic_stale import (  # noqa: E402
    dance_mimic_stale_summary,
    mark_dance_mimic_stale_items,
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DanceMimicPrivacyGridContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = load_module(
            REPO_ROOT / "ToolLibrary" / "DanceMimic_V1" / "_tool_impl.py",
            "dance_mimic_privacy_grid_tool_contract",
        )
        cls.executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "dance_mimic_privacy_grid_executor_contract",
        )
        cls.openrouter = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_openrouter.py",
            "dance_mimic_privacy_grid_openrouter_contract",
        )

    def test_fixed_grid_region_covers_all_detected_faces(self) -> None:
        samples = [
            {"faces": [{"bbox": [120 + offset, 80 + offset // 2, 60, 72]}]}
            for offset in range(0, 41, 4)
        ]
        detections = {"segments": [{"samples": samples}]}

        region = self.tool.privacy_grid_region(detections, 640, 480, {"privacy_grid": {}})

        self.assertEqual(region["valid_face_sample_count"], len(samples))
        self.assertGreaterEqual(region["face_sample_coverage_ratio"], 0.98)
        self.assertGreaterEqual(region["face_area_coverage_ratio"], 0.95)
        self.assertLessEqual(region["region_area_ratio"], 0.45)

    def test_reference_preview_selects_detected_sample_nearest_video_midpoint(self) -> None:
        detections = {
            "segments": [
                {"samples": [
                    {"frame_index": 10, "timestamp_seconds": 1.0, "faces": [{"bbox": [1, 1, 2, 2]}]},
                    {"frame_index": 50, "timestamp_seconds": 5.0, "faces": [{"bbox": [1, 1, 2, 2]}]},
                ]},
                {"samples": [
                    {"frame_index": 61, "timestamp_seconds": 6.1, "faces": [{"bbox": [1, 1, 2, 2]}]},
                    {"frame_index": 90, "timestamp_seconds": 9.0, "faces": []},
                ]},
            ],
        }

        sample = self.tool.representative_privacy_grid_sample(detections, 12.0)

        self.assertEqual(sample["frame_index"], 61)
        self.assertEqual(sample["timestamp_seconds"], 6.1)

    def test_legacy_privacy_grid_cell_size_is_capped_for_dense_rendering(self) -> None:
        config = {"privacy_grid": {"line_width_reference": 1, "cell_size_reference": 44}}

        line_width, cell, color = self.tool.privacy_grid_visual(config, 1080, 2340)

        self.assertEqual(line_width, 1)
        self.assertEqual(cell, 12)
        self.assertEqual(color, (31, 31, 255))

    def test_default_privacy_grid_uses_dense_cell_size(self) -> None:
        grid = self.tool.default_variables()["reference_face_masked_video_build"]["privacy_grid"]

        self.assertEqual(grid["cell_size_reference"], 12)

    def test_prompt_records_final_reference_roles_and_grid_cleanup_contract(self) -> None:
        package = self.openrouter.build_prompt_package({
            "segment": {
                "segment_id": "segment_0001",
                "planned_video_duration": 4,
                "prompt_template": "Video_SDR2V_DanceMimic.md",
                "dance_mimic": {
                    "privacy_grid_mode": True,
                    "reference_video_grid_applied": True,
                    "target_identity_grid_applied": True,
                },
            },
            "reference_image_roles": [
                {"role": "continuity_first_frame", "path": "first.png"},
                {"role": "target_identity", "path": "identity.png"},
            ],
        })

        self.assertIn("Input image reference 2 has role target_identity", package["prompt"])
        self.assertIn("generated video must contain no red grid", package["prompt"])
        self.assertEqual(package["extracted_fields"]["reference_image_roles"][1]["role"], "target_identity")

    def privacy_workspace(self, root: Path) -> tuple[dict, Path, Path]:
        identity = root / "SessionOutput/storyboard/Working/identity.png"
        video = root / "S9_05_02_VideoPlanExecutor/Working/motion.mp4"
        identity.parent.mkdir(parents=True, exist_ok=True)
        video.parent.mkdir(parents=True, exist_ok=True)
        identity.write_bytes(b"privacy-grid-identity")
        video.write_bytes(b"privacy-grid-video")
        manifest_path = root / "SessionOutput/reference/privacy_grid_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps({
            "mode": "red_grid_guide",
            "apply_to_reference_video": True,
            "apply_to_target_identity_image": False,
            "effective_grid_scope": "reference_video",
            "target_identity": {
                "grid_applied": False,
                "provider_sha256": sha256(identity),
            },
            "reference_video": {
                "grid_applied": True,
                "provider_segments": [{
                    "segment_id": "segment_0001",
                    "provider_sha256": sha256(video),
                }],
            },
        }), encoding="utf-8")
        segment = {
            "dance_mimic": {
                "privacy_grid_mode": True,
                "reference_video_grid_applied": True,
                "target_identity_grid_applied": False,
                "effective_grid_scope": "reference_video",
                "privacy_grid_manifest_path": "SessionOutput/reference/privacy_grid_manifest.json",
                "prompt_contract": "dance_mimic_privacy_grid_clean_output_0.1",
                "storyboard_seed_segment_id": "segment_0001",
            }
        }
        return segment, identity, video

    def test_provider_preflight_accepts_hash_equivalent_materialized_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            segment, identity, video = self.privacy_workspace(Path(tmp))
            self.executor.validate_privacy_grid_provider_inputs(Path(tmp), segment, identity, video)

    def test_provider_preflight_rejects_reference_not_declared_by_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segment, identity, video = self.privacy_workspace(root)
            video.write_bytes(b"unprocessed-video")

            with self.assertRaisesRegex(self.executor.ToolError, "privacy_grid_provider_preflight_failed"):
                self.executor.validate_privacy_grid_provider_inputs(root, segment, identity, video)

    def test_previous_tail_frame_gets_provider_privacy_grid_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "S9_05_02_VideoPlanExecutor/Working/dak_0002_MaterializedFirstFrame.png"
            source.parent.mkdir(parents=True, exist_ok=True)
            cv2, np = self.tool.import_cv2_np()
            image = np.full((240, 160, 3), 232, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(source), image))
            source_sha = sha256(source)
            manifest = root / "SessionOutput/reference/privacy_grid_manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({
                "mode": "red_grid_guide",
                "apply_to_target_identity_image": True,
                "render": {"line_width_reference": 1, "cell_size_reference": 44},
            }), encoding="utf-8")
            segment = {
                "asset_key": "dak_0002",
                "first_frame": {"source_type": "previous_segment_tail_frame"},
                "dance_mimic": {
                    "privacy_grid_mode": True,
                    "target_identity_grid_applied": True,
                    "privacy_grid_manifest_path": "SessionOutput/reference/privacy_grid_manifest.json",
                },
            }
            result = {"created_files": []}
            faces = [{"bbox": [50, 48, 54, 64], "confidence": 0.99}]

            with patch.object(self.tool, "detect_faces_in_image", return_value=(faces, "contract_detector")):
                provider, qa = self.executor.prepare_privacy_grid_continuity_frame(
                    root,
                    segment,
                    source,
                    source.parent,
                    "dak_0002",
                    result,
                    privacy_tool=self.tool,
                )

            self.assertEqual(sha256(source), source_sha)
            self.assertNotEqual(provider, source)
            self.assertEqual(provider.name, "dak_0002_ContinuityFirstFrame_PrivacyGrid.png")
            self.assertTrue(provider.is_file())
            self.assertEqual(qa["face_count"], 1)
            self.assertEqual(qa["detection_engine"], "contract_detector")
            self.assertGreaterEqual(qa["line_presence_ratio_min"], 0.95)
            self.assertIn(self.executor.rel(root, provider), result["created_files"])

    def test_tail_frame_grid_respects_target_grid_switch(self) -> None:
        segment = {
            "first_frame": {"source_type": "previous_segment_tail_frame"},
            "dance_mimic": {"privacy_grid_mode": True, "target_identity_grid_applied": False},
        }

        self.assertFalse(self.executor.requires_privacy_grid_continuity_frame(segment))

    def test_materialized_storyboard_image_uses_continuity_grid_before_video_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio_rel = "SessionOutput/storyboard/Working/dak_0002_Audio_Final.wav"
            tail_rel = "SessionOutput/storyboard/Working/dak_0001_TailFrame.png"
            image_rel = "SessionOutput/storyboard/Working/dak_0002_Image_New.png"
            for rel_path, content in ((audio_rel, b"audio"), (tail_rel, b"clean-tail")):
                path = root / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            segment = {
                "segment_id": "segment_0002",
                "asset_key": "dak_0002",
                "dialogue_audio_tasks": [{
                    "srt_id": "srt_0002",
                    "dialogue_asset_key": "dak_0002",
                    "need_audio": False,
                    "existing_audio_path": audio_rel,
                }],
                "planned_outputs": {
                    "image_path": image_rel,
                    "segment_audio_path": "SessionOutput/storyboard/Working/dak_0002_SegmentAudio_Final.wav",
                    "video_path": "SessionOutput/storyboard/Working/dak_0002_Video_Final.mp4",
                },
                "tasks": {"need_audio": False, "need_image": False, "need_image_prompt": False, "need_video": True},
                "first_frame": {
                    "source_type": "previous_segment_tail_frame",
                    "materialize_first_frame": {
                        "required": True,
                        "copy_from_path": tail_rel,
                        "copy_to_path": image_rel,
                        "source_type": "previous_segment_tail_frame",
                    },
                },
                "dance_mimic": {"privacy_grid_mode": True, "target_identity_grid_applied": True},
            }
            args = self.executor.Args(
                workspace=str(root),
                database_url="",
                max_segments=1,
                force=False,
                execute_audio=False,
                execute_image=False,
                execute_video=True,
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
            result = {"created_files": []}
            grid_sha: dict[str, str] = {}

            def fake_compose(_workspace, _files, output):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"segment-audio")
                return {"duration_seconds": 1.0}

            def fake_grid(_workspace, _segment, _source, working_dir, asset_key, call_result):
                provider = working_dir / f"{asset_key}_ContinuityFirstFrame_PrivacyGrid.png"
                Image.new("RGB", (576, 1024), "red").save(provider)
                grid_sha["value"] = sha256(provider)
                call_result["created_files"].append(self.executor.rel(root, provider))
                return provider, {"grid_applied": True, "provider_path": self.executor.rel(root, provider)}

            with (
                patch.object(self.executor, "validate_dance_mimic_segment_dependencies"),
                patch.object(self.executor, "compose_segment_audio", side_effect=fake_compose),
                patch.object(self.executor, "prepare_privacy_grid_continuity_frame", side_effect=fake_grid) as prepare_grid,
                patch.object(self.executor, "video_selection_for_segment", side_effect=RuntimeError("stop after materialization")),
            ):
                with self.assertRaisesRegex(RuntimeError, "stop after materialization"):
                    self.executor.execute_segment(root, args, {}, {}, {}, {}, {}, {}, segment, {}, result)

            self.assertEqual((root / tail_rel).read_bytes(), b"clean-tail")
            self.assertEqual(sha256(root / image_rel), grid_sha["value"])
            self.assertEqual(prepare_grid.call_count, 1)

    def test_privacy_grid_video_transport_rejects_tmpfiles_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "motion.mp4"
            video.write_bytes(b"video")

            with self.assertRaisesRegex(self.openrouter.ToolError, "privacy_grid_public_asset_transport_invalid"):
                self.openrouter.reference_asset_url(video, {"require_r2_public_assets": True}, "video")

    def test_privacy_config_update_can_mark_downstream_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marked = mark_dance_mimic_stale_items(
                root,
                {"02_reference_face_masked_video_build": ["SessionOutput/reference/privacy_grid_manifest.json"]},
                source_step="dance_mimic_task_update",
                reason="privacy_grid_config_changed",
            )

            summary = dance_mimic_stale_summary(root)
            self.assertEqual(marked, ["02_reference_face_masked_video_build"])
            self.assertEqual(summary["items"]["02_reference_face_masked_video_build"]["status"], "stale")


if __name__ == "__main__":
    unittest.main()
