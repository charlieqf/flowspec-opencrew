from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import types
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


class DanceMimicVideoReferenceRouteContractTest(unittest.TestCase):
    def load_05_01(self):
        return load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_01_VideoPlanGenerator.py",
            f"dance_mimic_05_01_contract_{id(self)}",
        )

    def load_05_05(self):
        return load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_05_VideoOnlyPlanGenerator.py",
            f"dance_mimic_05_05_contract_{id(self)}",
        )

    def load_05_02(self):
        return load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            f"dance_mimic_05_02_contract_{id(self)}",
        )

    def build_workspace(self, root: Path) -> dict[str, Any]:
        first_frame = root / "SessionOutput/storyboard/Working/dak_0001_Image_New.png"
        source_image = root / "SessionOutput/storyboard/Working/dak_0001_Image_Source.png"
        reference_video = root / "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4"
        seed_path = root / "SessionOutput/storyboard/storyboard_seed.json"
        first_frame.parent.mkdir(parents=True, exist_ok=True)
        reference_video.parent.mkdir(parents=True, exist_ok=True)
        first_frame.write_bytes(b"png")
        source_image.write_bytes(b"target-identity")
        reference_video.write_bytes(b"mp4")
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_text(json.dumps({
            "schema_version": "dance_mimic_v1_storyboard_seed_0.1",
            "workflow_id": "dance_mimic_v1",
            "source_video_path": "SessionContext/Video_Reference_Source.mp4",
            "target_identity_image_path": "SessionContext/Target_Identity_Image.png",
            "segments": [
                {
                    "segment_id": "segment_0001",
                    "dialogue_asset_key": "dak_0001",
                    "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
                    "source_face_masked_reference_video_path": "SessionOutput/reference/segments/segment_0001/face_masked_reference.mp4",
                    "target_identity_image_path": "SessionOutput/storyboard/Working/dak_0001_Image_Source.png",
                    "first_frame_image_path": "SessionOutput/storyboard/Working/dak_0001_Image_New.png",
                    "source_target_identity_image_path": "SessionContext/Target_Identity_Image.png",
                    "video_generation_mode": "dance_mimic_reference_video",
                    "provider": "openrouter",
                    "model": "bytedance/seedance-2.0",
                    "model_alias": "MaxSR2",
                    "reference_mode": "input_references",
                    "prompt_template": "Video_SDR2V_DanceMimic.md",
                    "reference_video_role": "dance_mimic_segment_motion_reference",
                }
            ],
        }), encoding="utf-8")
        storyboard = {
            "shots": [
                {
                    "shot_id": "shot_001",
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "start": 0,
                            "end": 3,
                            "dialogue_items": [
                                {
                                    "dialogue_asset_key": "dak_0001",
                                    "srt_id": "srt_0001",
                                    "start": 0,
                                    "end": 3,
                                    "dialogue": "copy only the dance motion",
                                    "image_path": "SessionOutput/storyboard/Working/dak_0001_Image_Source.png",
                                    "source_image_paths": ["SessionOutput/storyboard/Working/dak_0001_Image_Source.png"],
                                    "dance_mimic": {
                                        "source_segment_id": "segment_0001",
                                        "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
                                        "reference_video_role": "dance_mimic_segment_motion_reference",
                                        "target_identity_image_path": "SessionOutput/storyboard/Working/dak_0001_Image_Source.png",
                                    },
                                    "working_assets": {
                                        "images": [
                                            {
                                                "slot": "Image_New",
                                                "source_type": "dance_mimic_target_identity",
                                                "path": "SessionOutput/storyboard/Working/dak_0001_Image_New.png",
                                            }
                                        ]
                                    },
                                    "video_plan": {"is_talking_head": False},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        return {"storyboard": storyboard, "reference_video": reference_video}

    def test_05_01_reads_storyboard_seed_into_video_plan_segment(self) -> None:
        module = self.load_05_01()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.build_workspace(root)
            args = module.Args(
                workspace=str(root),
                target_type="task",
                shot_id="",
                scene_id="",
                max_video_seconds=4.0,
                min_video_seconds=4.0,
                split_tolerance_seconds=1.0,
                force=False,
                resume=False,
                print_json=False,
            )

            plan = module.build_plan(root, args, fixture["storyboard"])
            segment = plan["shots"][0]["scenes"][0]["segments"][0]

            self.assertEqual(plan["workflow_id"], "dance_mimic_v1")
            self.assertEqual(segment["provider"], "openrouter")
            self.assertEqual(segment["model"], "bytedance/seedance-2.0")
            self.assertEqual(segment["reference_mode"], "input_references")
            self.assertEqual(segment["prompt_template"], "Video_SDR2V_DanceMimic.md")
            self.assertEqual(segment["reference_video_path"], "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4")
            self.assertEqual(segment["target_identity_image_path"], "SessionOutput/storyboard/Working/dak_0001_Image_Source.png")
            self.assertEqual(segment["first_frame_image_path"], "SessionOutput/storyboard/Working/dak_0001_Image_New.png")
            self.assertEqual(segment["source_target_identity_image_path"], "SessionContext/Target_Identity_Image.png")
            self.assertEqual(segment["first_frame"]["source_type"], "generated_image")
            self.assertEqual(segment["first_frame"]["source_path"], "SessionOutput/storyboard/Working/dak_0001_Image_New.png")
            self.assertFalse(segment["tasks"]["need_audio"])
            self.assertFalse(segment["tasks"]["need_lipsync"])
            self.assertTrue(segment["tasks"]["need_audio_video_sync"])
            self.assertTrue(segment["tasks"]["need_video"])

    def test_05_01_uses_previous_tail_before_source_image_fallback_for_dance_mimic_continuation(self) -> None:
        module = self.load_05_01()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.build_workspace(root)
            storyboard = fixture["storyboard"]
            second_reference = root / "SessionOutput/storyboard/assets/videos/dak_0002_Reference_FaceMasked.mp4"
            second_source = root / "SessionOutput/storyboard/Working/dak_0002_Image_Source.png"
            second_reference.parent.mkdir(parents=True, exist_ok=True)
            second_source.parent.mkdir(parents=True, exist_ok=True)
            second_reference.write_bytes(b"mp4-2")
            second_source.write_bytes(b"target-identity-2")
            seed_path = root / "SessionOutput/storyboard/storyboard_seed.json"
            seed = json.loads(seed_path.read_text(encoding="utf-8"))
            seed["segments"].append({
                "segment_id": "segment_0002",
                "dialogue_asset_key": "dak_0002",
                "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0002_Reference_FaceMasked.mp4",
                "source_face_masked_reference_video_path": "SessionOutput/reference/segments/segment_0002/face_masked_reference.mp4",
                "target_identity_image_path": "SessionOutput/storyboard/Working/dak_0002_Image_Source.png",
                "first_frame_image_path": "",
                "source_target_identity_image_path": "SessionContext/Target_Identity_Image.png",
                "video_generation_mode": "dance_mimic_reference_video",
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
                "model_alias": "MaxSR2",
                "reference_mode": "input_references",
                "prompt_template": "Video_SDR2V_DanceMimic.md",
                "reference_video_role": "dance_mimic_segment_motion_reference",
            })
            seed_path.write_text(json.dumps(seed), encoding="utf-8")
            storyboard["shots"][0]["scenes"][0]["end"] = 6
            storyboard["shots"][0]["scenes"][0]["dialogue_items"].append({
                "dialogue_asset_key": "dak_0002",
                "srt_id": "srt_0002",
                "start": 3,
                "end": 6,
                "dialogue": "copy the second dance motion segment",
                "image_path": "SessionOutput/storyboard/Working/dak_0002_Image_Source.png",
                "source_image_paths": ["SessionOutput/storyboard/Working/dak_0002_Image_Source.png"],
                "dance_mimic": {
                    "source_segment_id": "segment_0002",
                    "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0002_Reference_FaceMasked.mp4",
                    "reference_video_role": "dance_mimic_segment_motion_reference",
                    "target_identity_image_path": "SessionOutput/storyboard/Working/dak_0002_Image_Source.png",
                },
                "working_assets": {
                    "images": [
                        {"slot": "Image_New", "source_type": "", "path": ""},
                        {"slot": "Image_02", "source_type": "", "path": ""},
                    ]
                },
                "video_plan": {"is_talking_head": False},
            })
            args = module.Args(
                workspace=str(root),
                target_type="task",
                shot_id="",
                scene_id="",
                max_video_seconds=4.0,
                min_video_seconds=4.0,
                split_tolerance_seconds=1.0,
                force=False,
                resume=False,
                print_json=False,
            )

            plan = module.build_plan(root, args, storyboard)
            segments = plan["shots"][0]["scenes"][0]["segments"]

            self.assertEqual(segments[0]["first_frame"]["source_type"], "generated_image")
            self.assertEqual(segments[1]["first_frame"]["source_type"], "previous_segment_tail_frame")
            self.assertEqual(segments[1]["first_frame"]["source_path"], "SessionOutput/storyboard/Working/dak_0001_TailFrame.png")
            self.assertEqual(segments[1]["first_frame"]["materialize_first_frame"]["copy_to_path"], "SessionOutput/storyboard/Working/dak_0002_Image_New.png")
            self.assertEqual(segments[1]["dependencies"]["depends_on_segment_id"], segments[0]["segment_id"])
            self.assertEqual(segments[1]["dependencies"]["depends_on_tail_frame_path"], "SessionOutput/storyboard/Working/dak_0001_TailFrame.png")

    def test_05_01_blocks_dance_mimic_reference_video_without_target_image_anchor(self) -> None:
        module = self.load_05_01()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.build_workspace(root)
            storyboard = fixture["storyboard"]
            dialogue = storyboard["shots"][0]["scenes"][0]["dialogue_items"][0]
            dialogue["working_assets"]["images"] = []
            dialogue["image_path"] = ""
            dialogue["source_image_paths"] = []
            dialogue["dance_mimic"] = {
                "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
                "reference_video_role": "dance_mimic_segment_motion_reference",
            }
            args = module.Args(
                workspace=str(root),
                target_type="task",
                shot_id="",
                scene_id="",
                max_video_seconds=4.0,
                min_video_seconds=4.0,
                split_tolerance_seconds=1.0,
                force=False,
                resume=False,
                print_json=False,
            )

            plan = module.build_plan(root, args, storyboard)
            scene = plan["shots"][0]["scenes"][0]
            segment = scene["segments"][0]

            self.assertEqual(scene["status"], "blocked")
            self.assertEqual(scene["blocked_reason"]["code"], "dancemimic_first_frame_missing")
            self.assertEqual(segment["status"], "blocked")
            self.assertEqual(segment["blocked_reason"]["code"], "dancemimic_first_frame_missing")
            self.assertNotEqual(segment["first_frame"]["source_type"], "dance_mimic_reference_video")
            self.assertEqual(segment["first_frame"]["source_path"], "")
            self.assertFalse(segment["tasks"]["need_audio"])
            self.assertFalse(segment["tasks"]["need_lipsync"])
            self.assertFalse(segment["tasks"]["need_video"])
            self.assertEqual(segment["provider"], "openrouter")

    def test_05_05_carries_dance_mimic_reference_fields_to_video_only_task(self) -> None:
        module = self.load_05_05()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.build_workspace(root)
            args = module.Args(
                workspace=str(root),
                target_type="task",
                shot_id="",
                scene_id="",
                max_video_seconds=4.0,
                min_video_seconds=4.0,
                split_tolerance_seconds=1.0,
                force=False,
                resume=False,
                print_json=False,
            )

            plan = module.build_plan(root, args, fixture["storyboard"])
            task = plan["video_only_tasks"][0]

            self.assertEqual(task["provider"], "openrouter")
            self.assertEqual(task["model"], "bytedance/seedance-2.0")
            self.assertEqual(task["reference_mode"], "input_references")
            self.assertEqual(task["prompt_template"], "Video_SDR2V_DanceMimic.md")
            self.assertEqual(task["reference_video_path"], "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4")

    def test_normal_seedance_dispatch_stays_on_seedance_without_dance_mimic_reference(self) -> None:
        module = self.load_05_02()

        selected = module.dance_mimic_video_selection(
            {"kind": "video", "provider": "bytedance", "model": "doubao-seedance-2-0-fast-260128"},
            {},
        )
        dispatched = module.video_module_for("bytedance", "doubao-seedance-2-0-fast-260128")

        self.assertEqual(selected["provider"], "bytedance")
        self.assertEqual(dispatched.TEMPLATE_NAME, "Ref_05_02_Video_Seedance.md")

    def test_dance_mimic_reference_routes_to_openrouter_and_copies_reference_video(self) -> None:
        module = self.load_05_02()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.build_workspace(root)
            working = root / "S9_05_02_VideoPlanExecutor/Working"
            segment = {
                "asset_key": "dak_0001",
                "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
                "reference_mode": "input_references",
                "prompt_template": "Video_SDR2V_DanceMimic.md",
            }

            selected = module.dance_mimic_video_selection(
                {"kind": "video", "provider": "seedance", "model": "MaxSR2"},
                segment,
            )
            copied = module.prepare_dance_mimic_reference_videos(root, segment, working, "dak_0001", {})

            self.assertEqual(selected["provider"], "openrouter")
            self.assertEqual(selected["model"], "bytedance/seedance-2.0")
            self.assertEqual(selected["reference_mode"], "input_references")
            self.assertEqual(copied[0].read_bytes(), fixture["reference_video"].read_bytes())
            self.assertIn("DanceMimicReference", copied[0].name)

    def test_dance_mimic_segment_without_reference_video_path_blocks_instead_of_fallback(self) -> None:
        module = self.load_05_02()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            working = root / "S9_05_02_VideoPlanExecutor/Working"
            segment = {
                "asset_key": "dak_0001",
                "video_generation_mode": "dance_mimic_reference_video",
                "reference_mode": "input_references",
                "prompt_template": "Video_SDR2V_DanceMimic.md",
            }

            selected = module.dance_mimic_video_selection(
                {"kind": "video", "provider": "seedance", "model": "MaxSR2"},
                segment,
            )

            self.assertEqual(selected["provider"], "openrouter")
            self.assertTrue(module.is_dance_mimic_reference_video_segment(segment))
            with self.assertRaisesRegex(module.ToolError, "dance_mimic_reference_video_missing"):
                module.prepare_dance_mimic_reference_videos(root, segment, working, "dak_0001", {})

    def test_05_01_keeps_dance_mimic_mode_when_seed_segment_lacks_reference_video_path(self) -> None:
        module = self.load_05_01()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.build_workspace(root)
            seed_path = root / "SessionOutput/storyboard/storyboard_seed.json"
            seed = json.loads(seed_path.read_text(encoding="utf-8"))
            del seed["segments"][0]["reference_video_path"]
            seed_path.write_text(json.dumps(seed), encoding="utf-8")
            args = module.Args(
                workspace=str(root),
                target_type="task",
                shot_id="",
                scene_id="",
                max_video_seconds=4.0,
                min_video_seconds=4.0,
                split_tolerance_seconds=1.0,
                force=False,
                resume=False,
                print_json=False,
            )

            plan = module.build_plan(root, args, fixture["storyboard"])
            segment = plan["shots"][0]["scenes"][0]["segments"][0]

            self.assertEqual(segment["video_generation_mode"], "dance_mimic_reference_video")
            self.assertEqual(segment["provider"], "openrouter")
            self.assertEqual(segment["reference_mode"], "input_references")
            self.assertEqual(segment.get("reference_video_path"), "")

    def test_dance_mimic_missing_first_frame_has_specific_block_code(self) -> None:
        module = self.load_05_02()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segment = {
                "asset_key": "dak_0001",
                "video_generation_mode": "dance_mimic_reference_video",
                "reference_mode": "input_references",
                "prompt_template": "Video_SDR2V_DanceMimic.md",
                "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
                "first_frame": {"source_path": ""},
            }

            with self.assertRaisesRegex(module.ToolError, "dancemimic_first_frame_missing"):
                module.first_frame_for_segment(root, segment, None)

    def test_dance_mimic_reference_video_cannot_be_used_as_first_frame(self) -> None:
        module = self.load_05_02()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4"
            reference.parent.mkdir(parents=True, exist_ok=True)
            reference.write_bytes(b"reference-video")
            segment = {
                "asset_key": "dak_0001",
                "video_generation_mode": "dance_mimic_reference_video",
                "reference_mode": "input_references",
                "prompt_template": "Video_SDR2V_DanceMimic.md",
                "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
                "first_frame": {"source_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4"},
            }

            with self.assertRaisesRegex(module.ToolError, "dancemimic_first_frame_from_reference_video_forbidden"):
                module.first_frame_for_segment(root, segment, None)

    def test_dance_mimic_reference_images_adds_identity_when_distinct_from_first_frame(self) -> None:
        module = self.load_05_02()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_frame = root / "SessionOutput/storyboard/Working/dak_0002_Image_New.png"
            identity = root / "SessionOutput/storyboard/Working/dak_0002_Image_Source.png"
            first_frame.parent.mkdir(parents=True, exist_ok=True)
            first_frame.write_bytes(b"tail-frame")
            identity.write_bytes(b"target-identity")
            segment = {
                "asset_key": "dak_0002",
                "video_generation_mode": "dance_mimic_reference_video",
                "reference_mode": "input_references",
                "prompt_template": "Video_SDR2V_DanceMimic.md",
                "target_identity_image_path": "SessionOutput/storyboard/Working/dak_0002_Image_Source.png",
            }

            refs, roles = module.dance_mimic_video_reference_images(root, segment, first_frame, {})

            self.assertEqual([module.rel(root, path) for path in refs], [
                "SessionOutput/storyboard/Working/dak_0002_Image_New.png",
                "SessionOutput/storyboard/Working/dak_0002_Image_Source.png",
            ])
            self.assertEqual([item["role"] for item in roles], ["continuity_first_frame", "target_identity"])

    def test_dance_mimic_dependency_gate_requires_completed_previous_tail(self) -> None:
        module = self.load_05_02()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segment = {
                "asset_key": "dak_0002",
                "video_generation_mode": "dance_mimic_reference_video",
                "reference_mode": "input_references",
                "prompt_template": "Video_SDR2V_DanceMimic.md",
                "dependencies": {
                    "depends_on_segment_id": "seg_1",
                    "depends_on_video_path": "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4",
                    "depends_on_tail_frame_path": "SessionOutput/storyboard/Working/dak_0001_TailFrame.png",
                },
            }
            result = {"segments": [{"segment_id": "seg_1", "status": "completed", "outputs": {}}]}

            with self.assertRaisesRegex(module.ToolError, "dancemimic_dependency_not_ready"):
                module.validate_dance_mimic_segment_dependencies(root, segment, result)

            for rel_path in (
                "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4",
                "SessionOutput/storyboard/Working/dak_0001_TailFrame.png",
            ):
                path = root / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"ready")

            module.validate_dance_mimic_segment_dependencies(root, segment, result)

    def test_dance_mimic_audio_video_sync_uses_planned_duration(self) -> None:
        module = self.load_05_02()
        try:
            ffmpeg = module.ffmpeg_executable()
            module.ffprobe_executable()
        except Exception as exc:
            self.skipTest(f"ffmpeg/ffprobe unavailable: {exc}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "video.mp4"
            audio = root / "audio.wav"
            fitted = root / "fitted.wav"
            synced = root / "synced.mp4"
            video_cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=160x240:rate=12:duration=1.2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ]
            audio_cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.35",
                "-c:a",
                "pcm_s16le",
                str(audio),
            ]
            for command in (video_cmd, audio_cmd):
                completed = subprocess.run(command, capture_output=True, text=True, check=False)
                self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

            target_duration = 1.6
            fitted_path, meta = module.pad_trim_audio_to_target_duration(
                root,
                audio,
                target_duration,
                fitted,
                source="dance_mimic_audio_pad_trim_to_planned_duration",
            )
            sync = module.replace_video_audio_to_target_duration(root, video, fitted_path, target_duration, synced)

            self.assertEqual(meta["source"], "dance_mimic_audio_pad_trim_to_planned_duration")
            self.assertEqual(sync["source"], "ffmpeg_audio_replace_retime_to_target_duration")
            self.assertAlmostEqual(module.media_duration_seconds(fitted_path), target_duration, delta=0.08)
            self.assertAlmostEqual(module.media_duration_seconds(synced), target_duration, delta=0.08)
            self.assertTrue(synced.exists())

    def test_generate_video_with_provider_passes_dance_mimic_reference_videos_to_context(self) -> None:
        module = self.load_05_02()
        captured: dict[str, Any] = {}

        def fake_generate(context: dict[str, Any], _prompt_path: Path, output_path: Path) -> dict[str, Any]:
            captured.update(context)
            output_path.write_bytes(b"video")
            return {"provider_profile": "video_openrouter"}

        fake_module = types.SimpleNamespace(__name__="video_openrouter", generate=fake_generate)
        old_video_module_for = module.video_module_for
        module.video_module_for = lambda _provider, _model="": fake_module
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prompt = root / "prompt.json"
                output = root / "out.mp4"
                image = root / "first.png"
                identity = root / "identity.png"
                video = root / "motion.mp4"
                prompt.write_text(json.dumps({"prompt": "DanceMimic prompt"}), encoding="utf-8")
                image.write_bytes(b"image")
                identity.write_bytes(b"identity")
                video.write_bytes(b"video-reference")

                module.generate_video_with_provider(
                    {
                        "provider": "openrouter",
                        "model": "bytedance/seedance-2.0",
                        "api_key": "test",
                        "reference_mode": "input_references",
                        "dance_mimic_reference_video": True,
                    },
                    prompt,
                    output,
                    [image, identity],
                    4.0,
                    30,
                    reference_videos=[video],
                )
        finally:
            module.video_module_for = old_video_module_for

        self.assertEqual(captured["reference_images"], [str(image), str(identity)])
        self.assertEqual(captured["reference_videos"], [str(video)])
        self.assertEqual(captured["config"]["provider"], "openrouter")
        self.assertEqual(captured["config"]["reference_mode"], "input_references")
        self.assertFalse(captured["config"]["generate_audio"])

    def test_dance_mimic_provider_mismatch_is_blocked(self) -> None:
        module = self.load_05_02()
        fake_module = types.SimpleNamespace(__name__="video_seedance", generate=lambda *_args, **_kwargs: {})
        old_video_module_for = module.video_module_for
        module.video_module_for = lambda _provider, _model="": fake_module
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prompt = root / "prompt.json"
                output = root / "out.mp4"
                image = root / "first.png"
                video = root / "motion.mp4"
                prompt.write_text(json.dumps({"prompt": "DanceMimic prompt"}), encoding="utf-8")
                image.write_bytes(b"image")
                video.write_bytes(b"video-reference")

                with self.assertRaisesRegex(module.ToolError, "dance_mimic_video_provider_mismatch"):
                    module.generate_video_with_provider(
                        {
                            "provider": "openrouter",
                            "model": "bytedance/seedance-2.0",
                            "api_key": "test",
                            "reference_mode": "input_references",
                            "dance_mimic_reference_video": True,
                        },
                        prompt,
                        output,
                        [image],
                        4.0,
                        30,
                        reference_videos=[video],
                    )
        finally:
            module.video_module_for = old_video_module_for

    def test_05_06_passes_reference_videos_keyword(self) -> None:
        source = (REPO_ROOT / "ToolLibrary/Analysis_V1/05_06_VideoOnlyPlanExecutor.py").read_text(encoding="utf-8")

        self.assertIn("dance_mimic_video_reference_images", source)
        self.assertIn("\"reference_images\"", source)
        self.assertIn("\"reference_image_roles\"", source)
        self.assertIn("prepare_dance_mimic_reference_videos", source)
        self.assertIn("reference_videos=reference_videos", source)


if __name__ == "__main__":
    unittest.main()
