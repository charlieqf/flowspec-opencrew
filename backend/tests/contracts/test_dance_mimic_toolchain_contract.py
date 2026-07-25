from __future__ import annotations

import importlib.util
import json
import subprocess
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


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TOOL_IMPL_PATH = REPO_ROOT / "ToolLibrary" / "DanceMimic_V1" / "_tool_impl.py"


class DanceMimicToolchainContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = load_module(TOOL_IMPL_PATH, "dance_mimic_toolchain_contract_tool_impl")
        try:
            cls.ffmpeg = cls.tool.find_ffmpeg()
            cls.tool.find_ffprobe()
        except Exception as exc:
            raise unittest.SkipTest(f"ffmpeg/ffprobe unavailable: {exc}") from exc
        if importlib.util.find_spec("cv2") is None or importlib.util.find_spec("numpy") is None:
            raise unittest.SkipTest("opencv/numpy unavailable")

    def make_source_video(self, root: Path, *, duration: float = 2.0) -> Path:
        output = root / "source.mp4"
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=160x120:rate=10:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(output),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        self.assertTrue(output.exists())
        return output

    def make_blank_source_video(self, root: Path, *, duration: float = 2.0) -> Path:
        output = root / "blank_source.mp4"
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:size=160x120:rate=10:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(output),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        self.assertTrue(output.exists())
        return output

    def make_source_video_without_audio(self, root: Path, *, duration: float = 2.0) -> Path:
        output = root / "source_no_audio.mp4"
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=160x120:rate=10:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        self.assertTrue(output.exists())
        return output

    def make_target_image(self, root: Path) -> Path:
        output = root / "target.png"
        output.write_bytes(b"fake-png-target-identity")
        return output

    def run_tool(self, tool_key: str, root: Path, *extra_args: str, expected_code: int = 0) -> dict[str, Any]:
        code = self.tool.run_tool(tool_key, ["--workspace", str(root), *extra_args])
        self.assertEqual(code, expected_code)
        result_path = root / self.tool.TOOL_META[tool_key]["tool_dir"] / "Report" / "Result.json"
        self.assertTrue(result_path.exists())
        return json.loads(result_path.read_text(encoding="utf-8"))

    def write_fixed_bbox_manifest(self, root: Path) -> Path:
        path = root / "fixed_bbox.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "dance_mimic_v1_fake_face_detections_0.1",
                    "fixed_bbox": [48, 32, 48, 40],
                    "post_mask_faces": [],
                }
            ),
            encoding="utf-8",
        )
        return path

    def write_detection_manifest(self, root: Path, payload: dict[str, Any], name: str = "detections.json") -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_stale_manifest_uses_session_report_not_session_output_toolset_dir(self) -> None:
        self.assertEqual(self.tool.DANCE_MIMIC_STALE_MANIFEST_REL, "SessionReport/stale_manifest.json")
        self.assertNotIn("SessionOutput/dance_mimic_v1", self.tool.DANCE_MIMIC_STALE_MANIFEST_REL)

    def prepare_03_ready_workspace(self, root: Path) -> None:
        source = self.make_source_video(root)
        target = self.make_target_image(root)
        self.run_tool("00", root, "--source-video-path", str(source), "--target-identity-image-path", str(target))
        self.run_tool("01", root)
        fixed_bbox = self.write_fixed_bbox_manifest(root)
        self.run_tool(
            "02",
            root,
            "--face-detections-manifest",
            str(fixed_bbox),
            "--target-video-seconds",
            "2",
            "--minimum-video-seconds",
            "1",
        )

    def test_00_to_03_builds_storyboard_seed_and_masked_reference_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video(root)
            target = self.make_target_image(root)
            variables_path = root / self.tool.VARIABLES_REL
            variables_path.parent.mkdir(parents=True, exist_ok=True)
            variables_path.write_text(json.dumps({"reference_video_path": "must_not_be_read.mp4"}), encoding="utf-8")

            result_00 = self.run_tool("00", root, "--source-video-path", str(source), "--target-identity-image-path", str(target))
            variables = json.loads(variables_path.read_text(encoding="utf-8"))
            self.assertEqual(result_00["status"], "completed")
            self.assertEqual(variables["workflow_id"], "dance_mimic_v1")
            self.assertEqual(variables["source_video_path"], self.tool.SOURCE_VIDEO_REL)
            self.assertEqual(variables["target_identity_image_path"], "SessionContext/Target_Identity_Image.png")
            self.assertEqual(variables["reference_face_masked_video_build"]["reference_privacy_mode"], "provider_safe_outline")
            self.assertTrue((root / variables["target_identity_image_path"]).exists())
            self.assertNotIn("reference_video_path", variables)

            result_01 = self.run_tool("01", root)
            media_manifest = json.loads((root / self.tool.REFERENCE_MANIFEST_REL).read_text(encoding="utf-8"))
            self.assertEqual(result_01["status"], "completed")
            self.assertFalse(media_manifest["probes"]["silent_video"]["has_audio"])
            self.assertTrue((root / self.tool.SILENT_VIDEO_REL).exists())
            self.assertTrue((root / self.tool.MIXED_AUDIO_REL).exists())

            fixed_bbox = self.write_fixed_bbox_manifest(root)
            result_02 = self.run_tool(
                "02",
                root,
                "--face-detections-manifest",
                str(fixed_bbox),
                "--target-video-seconds",
                "2",
                "--minimum-video-seconds",
                "1",
            )
            segments_manifest = json.loads((root / self.tool.SEGMENTS_MANIFEST_REL).read_text(encoding="utf-8"))
            segment = segments_manifest["segments"][0]
            self.assertEqual(result_02["status"], "completed")
            self.assertEqual(result_02["face_mask_summary"]["segments_with_faces"], 1)
            self.assertTrue(segment["qa"]["face_detected"])
            self.assertEqual(segment["qa"]["bbox"], [48, 32, 48, 40])
            self.assertIn("mask_coverage", segment["qa"])
            self.assertIn("output_probe", segment["qa"])
            self.assertEqual(segment["qa"]["reference_privacy_mode"], "provider_safe_outline")
            self.assertGreater(segment["qa"]["output_probe"]["frame_count"], 0)
            self.assertGreater(segment["qa"]["output_probe"]["size_bytes"], 0)
            self.assertLessEqual(segment["qa"]["output_probe"]["size_bytes"], 49000000)
            self.assertIn("provider_reference_video", segment["qa"])
            self.assertGreaterEqual(segment["qa"]["grid_black_black_pixel_ratio"], 0.60)
            self.assertGreaterEqual(segment["qa"]["masked_region_diff_mean"], 15.0)
            self.assertTrue((root / segment["face_masked_reference_video_path"]).exists())
            self.assertTrue((root / segment["face_track_path"]).exists())

            result_03 = self.run_tool("03", root)
            storyboard = json.loads((root / self.tool.STORYBOARD_REL).read_text(encoding="utf-8"))
            seed = json.loads((root / self.tool.STORYBOARD_SEED_REL).read_text(encoding="utf-8"))
            dialogue = storyboard["shots"][0]["scenes"][0]["dialogue_items"][0]
            seed_segment = seed["segments"][0]
            self.assertEqual(result_03["status"], "completed")
            self.assertEqual(storyboard["workflow_id"], "dance_mimic_v1")
            self.assertEqual(dialogue["working_assets"]["video"]["path"], "")
            self.assertEqual(seed_segment["provider"], "openrouter")
            self.assertEqual(seed_segment["reference_mode"], "input_references")
            self.assertEqual(seed_segment["prompt_template"], "Video_SDR2V_DanceMimic.md")
            self.assertEqual(seed_segment["reference_video_path"], "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4")
            self.assertEqual(seed["target_identity_image_path"], "SessionContext/Target_Identity_Image.png")
            self.assertEqual(seed_segment["target_identity_image_path"], "SessionOutput/storyboard/Working/dak_0001_Image_Source.png")
            self.assertEqual(seed_segment["first_frame_image_path"], "SessionOutput/storyboard/Working/dak_0001_Image_New.png")
            self.assertEqual(seed_segment["source_target_identity_image_path"], "SessionContext/Target_Identity_Image.png")
            self.assertEqual(seed_segment["segment_audio_path"], "SessionOutput/storyboard/Working/dak_0001_Audio_Final.wav")
            self.assertEqual(seed_segment["segment_audio_source_path"], "SessionOutput/reference/Audio_Reference_Mixed.wav")
            self.assertTrue((root / seed_segment["reference_video_path"]).exists())
            self.assertTrue((root / seed_segment["target_identity_image_path"]).exists())
            self.assertEqual(dialogue["image_path"], "SessionOutput/storyboard/Working/dak_0001_Image_Source.png")
            self.assertEqual(dialogue["source_image_paths"], ["SessionOutput/storyboard/Working/dak_0001_Image_Source.png"])
            self.assertEqual(dialogue["dance_mimic"]["target_identity_image_path"], "SessionOutput/storyboard/Working/dak_0001_Image_Source.png")
            self.assertEqual(dialogue["dance_mimic"]["source_target_identity_image_path"], "SessionContext/Target_Identity_Image.png")
            self.assertEqual(dialogue["dance_mimic"]["segment_audio_source_path"], "SessionOutput/storyboard/Working/dak_0001_Audio_Final.wav")
            self.assertEqual(dialogue["working_assets"]["audio"]["slot"], "Audio_Final")
            self.assertEqual(dialogue["working_assets"]["audio"]["source_type"], "dance_mimic_reference_audio")
            self.assertEqual(dialogue["working_assets"]["audio"]["path"], "SessionOutput/storyboard/Working/dak_0001_Audio_Final.wav")
            self.assertEqual(dialogue["working_assets"]["images"][0]["slot"], "Image_New")
            self.assertEqual(dialogue["working_assets"]["images"][0]["source_type"], "dance_mimic_target_identity")
            self.assertEqual(dialogue["working_assets"]["images"][0]["path"], "SessionOutput/storyboard/Working/dak_0001_Image_New.png")
            self.assertNotEqual(dialogue["working_assets"]["video"]["path"], seed_segment["reference_video_path"])

    def test_02_blocks_when_reference_media_manifest_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video(root)
            self.run_tool("00", root, "--source-video-path", str(source))

            result = self.run_tool("02", root, expected_code=2)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blocked_reasons"][0]["code"], "reference_media_manifest_missing")

    def test_01_generates_silent_mixed_audio_when_reference_has_no_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video_without_audio(root)
            self.run_tool("00", root, "--source-video-path", str(source))

            result = self.run_tool("01", root)

            self.assertEqual(result["status"], "completed")
            media_manifest = json.loads((root / self.tool.REFERENCE_MANIFEST_REL).read_text(encoding="utf-8"))
            self.assertFalse(media_manifest["probes"]["source_video"]["has_audio"])
            self.assertTrue(media_manifest["probes"]["mixed_audio"]["has_audio"])
            self.assertEqual(media_manifest["audio_config"]["mixed_audio_source"], "generated_silence")
            self.assertIn("source_audio_missing_silent_mixed_audio", {item["code"] for item in media_manifest["warnings"]})
            self.assertTrue((root / self.tool.MIXED_AUDIO_REL).exists())

    def test_02_real_detector_builds_wikimedia_reference_without_fixed_bbox_fixture(self) -> None:
        fixture = REPO_ROOT / "ToolLibrary" / "DanceMimic_V1" / "test_fixtures" / "dance_solo_frontal_studio.mp4"
        if not fixture.exists():
            self.skipTest(f"missing Wikimedia fixture: {fixture}")
        # The committed fixture is a tiny synthetic placeholder (see test_fixtures/manifest.json
        # committed_file) with no real face; real face-detection needs the full clip fetched via
        # the manifest download_url. Skip unless a real (multi-MB) clip is present.
        if fixture.stat().st_size < 100_000:
            self.skipTest(f"synthetic placeholder fixture ({fixture.stat().st_size} bytes); fetch real clip via manifest download_url")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_tool("00", root, "--source-video-path", str(fixture))
            self.run_tool("01", root)

            result = self.run_tool("02", root, "--target-video-seconds", "8", "--minimum-video-seconds", "4", "--block-on-face-not-detected")

            self.assertEqual(result["status"], "completed")
            self.assertGreaterEqual(result["face_mask_summary"]["segments_with_faces"], 1)
            self.assertEqual(result["face_mask_summary"]["requested_face_detection_engine"], "insightface_scrfd")
            self.assertIn(result["face_mask_summary"]["face_detection_engine"], {"insightface_scrfd", "mediapipe_blazeface", "opencv_haar"})
            segments_manifest = json.loads((root / self.tool.SEGMENTS_MANIFEST_REL).read_text(encoding="utf-8"))
            self.assertEqual(segments_manifest["face_detection"]["requested_face_detection_engine"], "insightface_scrfd")
            segment = segments_manifest["segments"][0]
            self.assertTrue(segment["qa"]["face_detected"])
            self.assertEqual(len(segment["qa"]["bbox"]), 4)
            self.assertGreater(segment["qa"]["detection_sample_count"], 0)
            self.assertTrue((root / segment["face_masked_reference_video_path"]).exists())

    def test_02_blocks_no_face_when_real_detector_finds_no_face_and_block_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_blank_source_video(root)
            self.run_tool("00", root, "--source-video-path", str(source))
            variables_path = root / self.tool.VARIABLES_REL
            variables = json.loads(variables_path.read_text(encoding="utf-8"))
            variables["reference_face_masked_video_build"]["face_detection_engine"] = "opencv_haar"
            variables_path.write_text(json.dumps(variables), encoding="utf-8")
            self.run_tool("01", root)

            result = self.run_tool("02", root, "--target-video-seconds", "2", "--minimum-video-seconds", "1", "--block-on-face-not-detected", expected_code=2)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blocked_reasons"][0]["code"], "face_not_detected")

    def test_02_blocks_infeasible_segment_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video(root)
            target = self.make_target_image(root)
            self.run_tool("00", root, "--source-video-path", str(source), "--target-identity-image-path", str(target))
            self.run_tool("01", root)
            fixed_bbox = self.write_fixed_bbox_manifest(root)

            result = self.run_tool(
                "02",
                root,
                "--face-detections-manifest",
                str(fixed_bbox),
                "--target-video-seconds",
                "5",
                "--minimum-video-seconds",
                "3",
                expected_code=2,
            )

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blocked_reasons"][0]["code"], "segment_constraints_infeasible")

    def test_02_blocks_no_face_segment_when_block_on_face_not_detected_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video(root)
            target = self.make_target_image(root)
            self.run_tool("00", root, "--source-video-path", str(source), "--target-identity-image-path", str(target))
            self.run_tool("01", root)
            detections = self.write_detection_manifest(
                root,
                {
                    "schema_version": "dance_mimic_v1_fake_face_detections_0.1",
                    "segments": [{"segment_id": "segment_0001", "post_mask_faces": []}],
                },
                "no_face.json",
            )

            result = self.run_tool(
                "02",
                root,
                "--face-detections-manifest",
                str(detections),
                "--target-video-seconds",
                "2",
                "--minimum-video-seconds",
                "1",
                "--block-on-face-not-detected",
                expected_code=2,
            )

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blocked_reasons"][0]["code"], "face_not_detected")

    def test_02_blocks_empty_bbox_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video(root)
            target = self.make_target_image(root)
            self.run_tool("00", root, "--source-video-path", str(source), "--target-identity-image-path", str(target))
            self.run_tool("01", root)
            detections = self.write_detection_manifest(
                root,
                {
                    "schema_version": "dance_mimic_v1_fake_face_detections_0.1",
                    "fixed_bbox": [],
                    "post_mask_faces": [],
                },
                "empty_bbox.json",
            )

            result = self.run_tool(
                "02",
                root,
                "--face-detections-manifest",
                str(detections),
                "--target-video-seconds",
                "2",
                "--minimum-video-seconds",
                "1",
                expected_code=2,
            )

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blocked_reasons"][0]["code"], "face_bbox_empty")

    def test_02_blocks_out_of_bounds_bbox_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video(root)
            target = self.make_target_image(root)
            self.run_tool("00", root, "--source-video-path", str(source), "--target-identity-image-path", str(target))
            self.run_tool("01", root)
            detections = self.write_detection_manifest(
                root,
                {
                    "schema_version": "dance_mimic_v1_fake_face_detections_0.1",
                    "fixed_bbox": [150, 100, 48, 40],
                    "post_mask_faces": [],
                },
                "out_of_bounds_bbox.json",
            )

            result = self.run_tool(
                "02",
                root,
                "--face-detections-manifest",
                str(detections),
                "--target-video-seconds",
                "2",
                "--minimum-video-seconds",
                "1",
                expected_code=2,
            )

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blocked_reasons"][0]["code"], "face_bbox_out_of_bounds")

    def test_03_blocks_when_face_masked_reference_video_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video(root)
            target = self.make_target_image(root)
            self.run_tool("00", root, "--source-video-path", str(source), "--target-identity-image-path", str(target))
            self.run_tool("01", root)
            fixed_bbox = self.write_fixed_bbox_manifest(root)
            self.run_tool(
                "02",
                root,
                "--face-detections-manifest",
                str(fixed_bbox),
                "--target-video-seconds",
                "2",
                "--minimum-video-seconds",
                "1",
            )
            segments_manifest_path = root / self.tool.SEGMENTS_MANIFEST_REL
            segments_manifest = json.loads(segments_manifest_path.read_text(encoding="utf-8"))
            masked_rel = segments_manifest["segments"][0]["face_masked_reference_video_path"]
            (root / masked_rel).unlink()

            result = self.run_tool("03", root, expected_code=2)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["error"]["code"], "missing_face_masked_reference_video")
            self.assertFalse((root / self.tool.STORYBOARD_REL).exists())

    def test_03_blocks_when_face_masked_reference_video_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video(root)
            target = self.make_target_image(root)
            self.run_tool("00", root, "--source-video-path", str(source), "--target-identity-image-path", str(target))
            self.run_tool("01", root)
            fixed_bbox = self.write_fixed_bbox_manifest(root)
            self.run_tool(
                "02",
                root,
                "--face-detections-manifest",
                str(fixed_bbox),
                "--target-video-seconds",
                "2",
                "--minimum-video-seconds",
                "1",
            )
            segments_manifest = json.loads((root / self.tool.SEGMENTS_MANIFEST_REL).read_text(encoding="utf-8"))
            masked_rel = segments_manifest["segments"][0]["face_masked_reference_video_path"]
            (root / masked_rel).write_bytes(b"")

            result = self.run_tool("03", root, expected_code=2)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["error"]["code"], "empty_face_masked_reference_video")
            self.assertFalse((root / self.tool.STORYBOARD_REL).exists())

    def test_03_blocks_bad_qa_manifest_instead_of_entering_storyboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_source_video(root)
            target = self.make_target_image(root)
            self.run_tool("00", root, "--source-video-path", str(source), "--target-identity-image-path", str(target))
            self.run_tool("01", root)
            fixed_bbox = self.write_fixed_bbox_manifest(root)
            self.run_tool(
                "02",
                root,
                "--face-detections-manifest",
                str(fixed_bbox),
                "--target-video-seconds",
                "2",
                "--minimum-video-seconds",
                "1",
            )
            segments_manifest_path = root / self.tool.SEGMENTS_MANIFEST_REL
            segments_manifest = json.loads(segments_manifest_path.read_text(encoding="utf-8"))
            segments_manifest["segments"][0]["qa"]["status"] = "failed"
            segments_manifest["segments"][0]["qa"]["mask_coverage"]["grid_black_black_pixel_ratio_min"] = 0.1
            segments_manifest_path.write_text(json.dumps(segments_manifest), encoding="utf-8")

            result = self.run_tool("03", root, expected_code=2)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["error"]["code"], "reference_segment_qa_failed")
            self.assertFalse((root / self.tool.STORYBOARD_REL).exists())

    def test_03_blocks_existing_storyboard_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_03_ready_workspace(root)
            self.run_tool("03", root)
            storyboard_path = root / self.tool.STORYBOARD_REL
            storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
            storyboard["user_edit_marker"] = "must_not_overwrite"
            storyboard_path.write_text(json.dumps(storyboard), encoding="utf-8")

            result = self.run_tool("03", root, expected_code=2)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["error"]["code"], "storyboard_existing_requires_force")
            self.assertIn(self.tool.STORYBOARD_REL, result["error"]["details"]["existing_outputs"])
            preserved = json.loads(storyboard_path.read_text(encoding="utf-8"))
            self.assertEqual(preserved["user_edit_marker"], "must_not_overwrite")
            self.assertFalse((root / self.tool.STORYBOARD_ARCHIVE_DIR_REL).exists())

    def test_03_force_archives_existing_storyboard_outputs_before_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_03_ready_workspace(root)
            self.run_tool("03", root)
            storyboard_path = root / self.tool.STORYBOARD_REL
            storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
            storyboard["user_edit_marker"] = "archive_me"
            storyboard_path.write_text(json.dumps(storyboard), encoding="utf-8")
            edit_path = root / self.tool.STORYBOARD_EDIT_REL
            edit_path.write_text(json.dumps({"schema_version": "koubo_storyboard_edit_0.1", "user_edit_marker": "archive_edit"}), encoding="utf-8")
            working_file = root / self.tool.STORYBOARD_WORKING_REL / "dak_0001_Video_Final.mp4"
            working_file.parent.mkdir(parents=True, exist_ok=True)
            working_file.write_bytes(b"user-final-video")
            reference_asset = root / "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4"
            reference_asset.write_bytes(b"old-reference-asset")
            user_upload = root / "SessionOutput/storyboard/assets/videos/user_uploaded_clip.mp4"
            user_upload.write_bytes(b"user-upload")

            result = self.run_tool("03", root, "--force")

            self.assertEqual(result["status"], "completed")
            archive_root = root / self.tool.STORYBOARD_ARCHIVE_DIR_REL
            archives = [path for path in archive_root.iterdir() if path.is_dir()]
            self.assertEqual(len(archives), 1)
            archive = archives[0]
            archived_storyboard = json.loads((archive / self.tool.STORYBOARD_REL).read_text(encoding="utf-8"))
            self.assertEqual(archived_storyboard["user_edit_marker"], "archive_me")
            archived_edit = json.loads((archive / self.tool.STORYBOARD_EDIT_REL).read_text(encoding="utf-8"))
            self.assertEqual(archived_edit["user_edit_marker"], "archive_edit")
            self.assertEqual((archive / self.tool.STORYBOARD_WORKING_REL / "dak_0001_Video_Final.mp4").read_bytes(), b"user-final-video")
            self.assertEqual((archive / "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4").read_bytes(), b"old-reference-asset")
            rebuilt_storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
            self.assertNotIn("user_edit_marker", rebuilt_storyboard)
            self.assertTrue(reference_asset.exists())
            self.assertNotEqual(reference_asset.read_bytes(), b"old-reference-asset")
            self.assertTrue(user_upload.exists())
            self.assertEqual(user_upload.read_bytes(), b"user-upload")
            self.assertFalse((root / self.tool.STORYBOARD_WORKING_REL / "dak_0001_Video_Final.mp4").exists())
            self.assertTrue((root / self.tool.STORYBOARD_WORKING_REL / "dak_0001_Image_New.png").exists())
            self.assertFalse(edit_path.exists())

    def test_01_force_marks_02_and_03_stale_without_deleting_storyboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_03_ready_workspace(root)
            self.run_tool("03", root)
            storyboard_path = root / self.tool.STORYBOARD_REL
            reference_asset = root / "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4"

            result = self.run_tool("01", root, "--force")

            self.assertEqual(result["status"], "completed")
            self.assertTrue(storyboard_path.exists())
            self.assertTrue(reference_asset.exists())
            stale_manifest = json.loads((root / self.tool.DANCE_MIMIC_STALE_MANIFEST_REL).read_text(encoding="utf-8"))
            self.assertEqual(stale_manifest["items"]["02_reference_face_masked_video_build"]["status"], "stale")
            self.assertEqual(stale_manifest["items"]["03_storyboard_standard_task_build"]["status"], "stale")
            self.assertEqual(stale_manifest["items"]["02_reference_face_masked_video_build"]["source_step"], "01_ReferenceMediaDemux")

    def test_02_force_marks_storyboard_and_video_plan_stale_until_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_03_ready_workspace(root)
            self.run_tool("03", root)
            video_plan = root / self.tool.VIDEO_PLAN_REL
            video_plan.write_text(json.dumps({"schema_version": "koubo_video_plan_0.1", "old": True}), encoding="utf-8")
            video_only_plan = root / self.tool.VIDEO_ONLY_PLAN_REL
            video_only_plan.write_text(json.dumps({"schema_version": "koubo_video_only_plan_0.1", "old": True}), encoding="utf-8")
            self.run_tool("01", root, "--force")
            fixed_bbox = self.write_fixed_bbox_manifest(root)

            result_02 = self.run_tool(
                "02",
                root,
                "--force",
                "--face-detections-manifest",
                str(fixed_bbox),
                "--target-video-seconds",
                "2",
                "--minimum-video-seconds",
                "1",
            )

            self.assertEqual(result_02["status"], "completed")
            self.assertTrue(video_plan.exists())
            self.assertTrue(video_only_plan.exists())
            stale_manifest = json.loads((root / self.tool.DANCE_MIMIC_STALE_MANIFEST_REL).read_text(encoding="utf-8"))
            self.assertNotIn("02_reference_face_masked_video_build", stale_manifest["items"])
            self.assertEqual(stale_manifest["items"]["03_storyboard_standard_task_build"]["source_step"], "02_ReferenceFaceMaskedVideoBuild")
            self.assertEqual(stale_manifest["items"]["storyboard_reference_video_assets"]["status"], "stale")
            self.assertEqual(stale_manifest["items"]["video_generation_plan"]["status"], "stale")
            self.assertEqual(stale_manifest["items"]["video_only_generation_plan"]["status"], "stale")

            result_03 = self.run_tool("03", root, "--force")

            self.assertEqual(result_03["status"], "completed")
            refreshed_manifest = json.loads((root / self.tool.DANCE_MIMIC_STALE_MANIFEST_REL).read_text(encoding="utf-8"))
            self.assertNotIn("03_storyboard_standard_task_build", refreshed_manifest["items"])
            self.assertNotIn("storyboard_reference_video_assets", refreshed_manifest["items"])
            self.assertEqual(refreshed_manifest["items"]["video_generation_plan"]["status"], "stale")
            self.assertEqual(refreshed_manifest["items"]["video_only_generation_plan"]["status"], "stale")
            self.assertTrue(video_plan.exists())
            self.assertTrue(video_only_plan.exists())


if __name__ == "__main__":
    unittest.main()
