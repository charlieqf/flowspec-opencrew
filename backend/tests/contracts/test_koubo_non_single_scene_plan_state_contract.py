from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


OPENCREW_ROOT = Path(__file__).resolve().parents[3]
VIDEO_PLAN_GENERATOR_PATH = OPENCREW_ROOT / "ToolLibrary" / "Analysis_V1" / "05_01_VideoPlanGenerator.py"
IMAGE_PLAN_GENERATOR_PATH = OPENCREW_ROOT / "ToolLibrary" / "Analysis_V1" / "05_03_ImagePlanGenerator.py"
VIDEO_ONLY_PLAN_GENERATOR_PATH = OPENCREW_ROOT / "ToolLibrary" / "Analysis_V1" / "05_05_VideoOnlyPlanGenerator.py"
SLOT_STATE_PATH = OPENCREW_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "slot_state_services.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def touch(workspace: Path, rel_path: str, content: bytes = b"test") -> None:
    path = workspace / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def asset_key(shot_index: int, scene_index: int, dialogue_index: int) -> str:
    return f"s{shot_index}c{scene_index}_d{dialogue_index}"


def working_rel(key: str, suffix: str) -> str:
    return f"SessionOutput/storyboard/Working/{key}_{suffix}"


def make_dialogue(
    workspace: Path,
    key: str,
    start: float,
    *,
    duration: float = 2.0,
    audio_exists: bool = True,
    source_image: bool = False,
    new_image: bool = False,
    raw_video: bool = False,
    final_video: bool = False,
    tail_frame: bool = False,
    talking_head: bool = True,
) -> dict[str, Any]:
    audio_rel = working_rel(key, "Audio_Final.wav")
    if audio_exists:
        touch(workspace, audio_rel, b"fake-wav")
    image_rel = f"SessionOutput/visual/srt_frames/{key}.jpg" if source_image else ""
    if image_rel:
        touch(workspace, image_rel, b"fake-jpg")
    new_image_rel = working_rel(key, "Image_New.png") if new_image else ""
    if new_image_rel:
        touch(workspace, new_image_rel, b"fake-png")
    if raw_video:
        touch(workspace, working_rel(key, "Video_Raw.mp4"), b"fake-raw")
    video_rel = working_rel(key, "Video_Final.mp4") if final_video else ""
    if video_rel:
        touch(workspace, video_rel, b"fake-mp4")
    if tail_frame:
        touch(workspace, working_rel(key, "TailFrame.png"), b"fake-tail")
    return {
        "srt_id": key,
        "dialogue_asset_key": key,
        "dialogue": f"{key} 测试对白",
        "start": start,
        "end": start + duration,
        "duration": duration,
        "image_path": image_rel,
        "video_plan": {"is_talking_head": bool(talking_head)},
        "working_assets": {
            "audio": {"slot": "Audio_Final", "source_type": "generated" if audio_exists else "", "path": audio_rel if audio_exists else ""},
            "images": [
                {"slot": "Image_New", "source_type": "generated" if new_image_rel else "", "path": new_image_rel},
            ],
            "video": {"slot": "Video_Final", "source_type": "generated" if video_rel else "", "path": video_rel},
        },
    }


def make_workspace(
    root: Path,
    *,
    source_images: set[str] | None = None,
    final_videos: set[str] | None = None,
    raw_videos: set[str] | None = None,
    tail_frames: set[str] | None = None,
    cutaway_dialogues: set[str] | None = None,
) -> Path:
    workspace = root / "workspace"
    source_images = source_images or set()
    final_videos = final_videos or set()
    raw_videos = raw_videos or set()
    tail_frames = tail_frames or set()
    cutaway_dialogues = cutaway_dialogues or set()
    write_json(
        workspace / "SessionContext/Variables.json",
        {
            "workflow_id": "openclip_analysis",
            "default_video_config": {"provider": "openai", "model": "sora-test", "api_key_ref": "test-video-key", "has_api_key": True},
            "default_image_config": {"provider": "openai", "model": "gpt-image-1.5", "api_key_ref": "test-image-key", "has_api_key": True},
            "default_tts_config": {"provider": "google", "model": "gemini-tts", "api_key_ref": "test-tts-key", "has_api_key": True},
        },
    )

    shots: list[dict[str, Any]] = []
    cursor = 0.0
    for shot_index in (1, 2):
        scenes: list[dict[str, Any]] = []
        for scene_index in (1, 2):
            dialogues: list[dict[str, Any]] = []
            scene_start = cursor
            for dialogue_index in (1, 2, 3):
                key = asset_key(shot_index, scene_index, dialogue_index)
                dialogues.append(
                    make_dialogue(
                        workspace,
                        key,
                        cursor,
                        source_image=key in source_images,
                        raw_video=key in raw_videos,
                        final_video=key in final_videos,
                        tail_frame=key in tail_frames,
                        talking_head=key not in cutaway_dialogues,
                    )
                )
                cursor += 2.0
            scenes.append(
                {
                    "scene_id": f"scene_{scene_index:03d}",
                    "summary": f"Shot {shot_index} Scene {scene_index}",
                    "start": scene_start,
                    "end": cursor,
                    "duration": cursor - scene_start,
                    "dialogue_items": dialogues,
                }
            )
        shots.append({"shot_id": f"shot_{shot_index:03d}", "summary": f"Shot {shot_index}", "scenes": scenes})

    write_json(
        workspace / "SessionOutput/storyboard/srt_storyboard.json",
        {"schema_version": "analysis_v1_storyboard_0.1", "shots": shots},
    )
    return workspace


def make_single_scene_workspace(
    root: Path,
    *,
    durations: list[float],
    source_images: set[int] | None = None,
    new_images: set[int] | None = None,
    raw_videos: set[int] | None = None,
    final_videos: set[int] | None = None,
    tail_frames: set[int] | None = None,
    audio_missing: set[int] | None = None,
    cutaway_dialogues: set[int] | None = None,
) -> Path:
    workspace = root / "workspace"
    source_images = source_images or set()
    new_images = new_images or set()
    raw_videos = raw_videos or set()
    final_videos = final_videos or set()
    tail_frames = tail_frames or set()
    audio_missing = audio_missing or set()
    cutaway_dialogues = cutaway_dialogues or set()
    write_json(
        workspace / "SessionContext/Variables.json",
        {
            "workflow_id": "openclip_analysis",
            "default_video_config": {"provider": "openai", "model": "sora-test", "api_key_ref": "test-video-key", "has_api_key": True},
            "default_image_config": {"provider": "openai", "model": "gpt-image-1.5", "api_key_ref": "test-image-key", "has_api_key": True},
            "default_tts_config": {"provider": "google", "model": "gemini-tts", "api_key_ref": "test-tts-key", "has_api_key": True},
        },
    )
    cursor = 0.0
    dialogues: list[dict[str, Any]] = []
    for index, duration in enumerate(durations, start=1):
        key = f"d{index}"
        dialogues.append(
            make_dialogue(
                workspace,
                key,
                cursor,
                duration=duration,
                audio_exists=index not in audio_missing,
                source_image=index in source_images,
                new_image=index in new_images,
                raw_video=index in raw_videos,
                final_video=index in final_videos,
                tail_frame=index in tail_frames,
                talking_head=index not in cutaway_dialogues,
            )
        )
        cursor += duration
    write_json(
        workspace / "SessionOutput/storyboard/srt_storyboard.json",
        {
            "schema_version": "analysis_v1_storyboard_0.1",
            "shots": [
                {
                    "shot_id": "shot_001",
                    "summary": "Single Shot",
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "summary": "Single Scene",
                            "start": 0.0,
                            "end": cursor,
                            "duration": cursor,
                            "dialogue_items": dialogues,
                        }
                    ],
                }
            ],
        },
    )
    return workspace


def run_video_plan(workspace: Path, *, target_type: str = "task", max_seconds: float = 4.0):
    generator = load_module(VIDEO_PLAN_GENERATOR_PATH, f"analysis_v1_05_01_non_single_{target_type}_{id(workspace)}")
    args = [
        "--workspace",
        str(workspace),
        "--target-type",
        target_type,
        "--max-video-seconds",
        str(max_seconds),
        "--split-tolerance-seconds",
        "0",
        "--force",
    ]
    if target_type == "shot":
        args.extend(["--shot-id", "shot_002"])
    result = generator.run(generator.parse_args(args))
    plan = json.loads((workspace / "SessionOutput/storyboard/video_generation_plan.json").read_text(encoding="utf-8"))
    return result, plan


def plan_segments(plan: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for shot in plan.get("shots", []):
        for scene in shot.get("scenes", []):
            segments.extend(scene.get("segments", []))
    return segments


class KouboNonSingleScenePlanStateContractTest(unittest.TestCase):
    def test_video_plan_single_scene_cases_1_to_7_cover_time_and_visual_anchor_splitting(self) -> None:
        cases = [
            {
                "name": "case_1_max_2s_tail_chain",
                "durations": [1, 1, 1, 1, 1],
                "max": 2,
                "source_images": {1},
                "new_images": set(),
                "expected": [["d1", "d2"], ["d3", "d4"], ["d5"]],
                "first_frames": ["original_image", "previous_segment_tail_frame", "previous_segment_tail_frame"],
            },
            {
                "name": "case_2_one_visual_anchor_cut",
                "durations": [1, 1, 1, 1, 1],
                "max": 5,
                "source_images": {1},
                "new_images": {3},
                "expected": [["d1", "d2"], ["d3", "d4", "d5"]],
                "first_frames": ["original_image", "generated_image"],
            },
            {
                "name": "case_4_max_4s_three_segments",
                "durations": [2, 2, 2, 2, 2],
                "max": 4,
                "source_images": {1},
                "new_images": set(),
                "expected": [["d1", "d2"], ["d3", "d4"], ["d5"]],
                "first_frames": ["original_image", "previous_segment_tail_frame", "previous_segment_tail_frame"],
            },
            {
                "name": "case_5_max_8s_two_segments",
                "durations": [2, 2, 2, 2, 2],
                "max": 8,
                "source_images": {1},
                "new_images": set(),
                "expected": [["d1", "d2", "d3", "d4"], ["d5"]],
                "first_frames": ["original_image", "previous_segment_tail_frame"],
            },
            {
                "name": "case_6_max_15s_one_segment",
                "durations": [2, 2, 2, 2, 2],
                "max": 15,
                "source_images": {1},
                "new_images": set(),
                "expected": [["d1", "d2", "d3", "d4", "d5"]],
                "first_frames": ["original_image"],
            },
            {
                "name": "case_7_two_visual_anchor_cuts",
                "durations": [1, 1, 1, 1, 1],
                "max": 10,
                "source_images": {1, 3},
                "new_images": {5},
                "expected": [["d1", "d2"], ["d3", "d4"], ["d5"]],
                "first_frames": ["original_image", "original_image", "generated_image"],
            },
        ]
        for case in cases:
            with self.subTest(case["name"]):
                with tempfile.TemporaryDirectory() as tmp:
                    workspace = make_single_scene_workspace(
                        Path(tmp),
                        durations=case["durations"],
                        source_images=case["source_images"],
                        new_images=case["new_images"],
                    )

                    result, plan = run_video_plan(workspace, max_seconds=case["max"])

                    self.assertEqual(result["status"], "completed")
                    segments = plan["shots"][0]["scenes"][0]["segments"]
                    self.assertEqual([segment["dialogue_ids"] for segment in segments], case["expected"])
                    self.assertEqual([segment["first_frame"]["source_type"] for segment in segments], case["first_frames"])

    def test_existing_new_image_is_reused_by_video_plan_and_video_only_plan(self) -> None:
        video_only_generator = load_module(VIDEO_ONLY_PLAN_GENERATOR_PATH, "analysis_v1_05_05_existing_new_image_contract")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_single_scene_workspace(
                Path(tmp),
                durations=[1, 1, 1],
                source_images={1},
                new_images={2},
            )

            video_result, video_plan = run_video_plan(workspace, max_seconds=10)

            self.assertEqual(video_result["status"], "completed")
            segments = video_plan["shots"][0]["scenes"][0]["segments"]
            self.assertEqual([segment["dialogue_ids"] for segment in segments], [["d1"], ["d2", "d3"]])
            new_image_segment = segments[1]
            self.assertEqual(new_image_segment["first_frame"]["source_type"], "generated_image")
            self.assertEqual(new_image_segment["first_frame"]["source_path"], working_rel("d2", "Image_New.png"))
            self.assertEqual(new_image_segment["planned_outputs"]["image_path"], working_rel("d2", "Image_New.png"))
            self.assertFalse(new_image_segment["tasks"]["need_image_prompt"])
            self.assertFalse(new_image_segment["tasks"]["need_image"])

            video_only_result = video_only_generator.run(
                video_only_generator.parse_args(
                    ["--workspace", str(workspace), "--target-type", "scene", "--shot-id", "shot_001", "--scene-id", "scene_001", "--max-video-seconds", "10", "--split-tolerance-seconds", "0", "--force"]
                )
            )

            self.assertEqual(video_only_result["status"], "completed")
            video_only_plan = json.loads((workspace / "SessionOutput/storyboard/video_only_generation_plan.json").read_text(encoding="utf-8"))
            task_by_asset = {task["asset_key"]: task for task in video_only_plan["video_only_tasks"]}
            self.assertEqual(task_by_asset["d2"]["first_frame"]["source_type"], "generated_image")
            self.assertEqual(task_by_asset["d2"]["first_frame"]["planned_image_path"], working_rel("d2", "Image_New.png"))
            self.assertEqual(task_by_asset["d2"]["planned_outputs"]["first_frame_path"], working_rel("d2", "Image_New.png"))
            self.assertEqual(task_by_asset["d2"]["steps"]["first_frame"]["status"], "completed_working")

    def test_video_plan_case_3_first_scene_missing_visual_is_skipped_not_started_from_mid_scene(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_single_scene_workspace(Path(tmp), durations=[1, 1, 1, 1, 1], source_images={3})

            result, plan = run_video_plan(workspace, max_seconds=5)

            self.assertEqual(result["status"], "completed_with_skipped_items")
            scene = plan["shots"][0]["scenes"][0]
            self.assertEqual(scene["status"], "skipped")
            self.assertEqual(scene["skipped_reason"]["code"], "first_scene_missing_visual_source")

    def test_video_plan_audio_cases_8_to_10_merge_audio_by_all_dialogues_in_segment(self) -> None:
        cases = [
            ("case_8_all_audio_ready", set(), [False, False]),
            ("case_9_first_audio_ready_second_missing", {2}, [False, True]),
            ("case_10_first_audio_missing_second_ready", {1}, [True, False]),
        ]
        for name, audio_missing, expected_need_audio in cases:
            with self.subTest(name):
                with tempfile.TemporaryDirectory() as tmp:
                    workspace = make_single_scene_workspace(
                        Path(tmp),
                        durations=[1, 1, 1, 1, 1],
                        source_images={1},
                        audio_missing=audio_missing,
                    )

                    _result, plan = run_video_plan(workspace, max_seconds=2)

                    first_segment = plan["shots"][0]["scenes"][0]["segments"][0]
                    self.assertEqual(first_segment["dialogue_ids"], ["d1", "d2"])
                    self.assertEqual([item["need_audio"] for item in first_segment["dialogue_audio_tasks"]], expected_need_audio)
                    self.assertEqual(first_segment["tasks"]["need_audio"], any(expected_need_audio))

    def test_video_plan_cases_14_to_16_cover_bound_video_anchor_and_final_task_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_single_scene_workspace(Path(tmp), durations=[1, 1, 1, 1, 1], source_images={1}, final_videos={3})

            _result, plan = run_video_plan(workspace, max_seconds=5)

            segments = plan["shots"][0]["scenes"][0]["segments"]
            self.assertEqual([segment["dialogue_ids"] for segment in segments], [["d1", "d2"], ["d3"], ["d4", "d5"]])
            self.assertEqual(segments[1]["first_frame"]["source_type"], "bound_video")
            self.assertFalse(segments[1]["tasks"]["need_video"])
            self.assertEqual(segments[2]["first_frame"]["source_type"], "previous_segment_tail_frame")

        for talking_head, expected_sync_mode, expected_lipsync, expected_audio_sync, continuation_allowed in (
            (True, "lipsync", True, False, True),
            (False, "audio_replace_retime", False, True, False),
        ):
            with self.subTest(f"talking_head_{talking_head}"):
                with tempfile.TemporaryDirectory() as tmp:
                    workspace = make_single_scene_workspace(
                        Path(tmp),
                        durations=[1, 1, 1, 1, 1],
                        source_images={1},
                        cutaway_dialogues=set() if talking_head else {1},
                    )

                    _result, plan = run_video_plan(workspace, max_seconds=15)

                    segment = plan["shots"][0]["scenes"][0]["segments"][0]
                    self.assertEqual(segment["dialogue_ids"], ["d1", "d2", "d3", "d4", "d5"])
                    self.assertEqual(segment["tasks"]["sync_mode"], expected_sync_mode)
                    self.assertEqual(segment["tasks"]["need_lipsync"], expected_lipsync)
                    self.assertEqual(segment["tasks"]["need_audio_video_sync"], expected_audio_sync)
                    self.assertEqual(segment["tail_frame"]["continuation_allowed"], continuation_allowed)

    def test_video_plan_reuses_existing_raw_video_for_cutaway_audio_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_single_scene_workspace(
                Path(tmp),
                durations=[2, 2],
                raw_videos={1},
                cutaway_dialogues={1},
            )

            _result, plan = run_video_plan(workspace, max_seconds=10)

            scene = plan["shots"][0]["scenes"][0]
            self.assertEqual(scene["status"], "planned")
            segment = scene["segments"][0]
            self.assertEqual(segment["first_frame"]["source_type"], "existing_raw_video")
            self.assertFalse(segment["tasks"]["need_video"])
            self.assertTrue(segment["tasks"]["need_sync"])
            self.assertFalse(segment["tasks"]["need_lipsync"])
            self.assertTrue(segment["tasks"]["need_audio_video_sync"])
            self.assertEqual(segment["tasks"]["sync_mode"], "audio_replace_retime")
            self.assertEqual(segment["planned_outputs"]["raw_video_path"], working_rel("d1", "Video_Raw.mp4"))
            self.assertEqual(segment["planned_outputs"]["final_video_path"], working_rel("d1", "Video_Final.mp4"))

    def test_video_plan_case_18_shot_scope_uses_existing_previous_scene_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(
                Path(tmp),
                final_videos={asset_key(1, 2, 3)},
                tail_frames={asset_key(1, 2, 3)},
            )

            result, plan = run_video_plan(workspace, target_type="shot")

            self.assertEqual(result["status"], "completed")
            first_segment = plan["shots"][0]["scenes"][0]["segments"][0]
            self.assertEqual(first_segment["dialogue_ids"], ["s2c1_d1", "s2c1_d2"])
            self.assertEqual(first_segment["first_frame"]["source_type"], "previous_scene_tail_frame")
            self.assertEqual(first_segment["dependencies"]["depends_on_video_path"], working_rel(asset_key(1, 2, 3), "Video_Final.mp4"))

    def test_video_plan_case_21_bound_video_first_scene_starts_cross_shot_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp), final_videos={asset_key(1, 1, 1)})

            result, plan = run_video_plan(workspace)

            self.assertEqual(result["status"], "completed")
            segments = plan_segments(plan)
            self.assertEqual(segments[0]["dialogue_ids"], ["s1c1_d1"])
            self.assertEqual(segments[0]["first_frame"]["source_type"], "bound_video")
            self.assertFalse(segments[0]["tasks"]["need_video"])
            self.assertEqual(segments[1]["first_frame"]["source_type"], "previous_segment_tail_frame")
            self.assertEqual(segments[1]["dependencies"]["depends_on_segment_id"], "shot_001_scene_001_segment_001")
            self.assertEqual(segments[1]["dialogue_ids"], ["s1c1_d2", "s1c1_d3"])

    def test_video_plan_existing_raw_video_wins_over_original_image_and_final_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_key = asset_key(1, 1, 1)
            workspace = make_workspace(Path(tmp), source_images={first_key})
            raw_rel = working_rel(first_key, "Video_Raw.mov")
            touch(workspace, raw_rel, b"existing-raw")
            touch(workspace, working_rel(first_key, "Video_Final.mp4"), b"existing-final")

            result, plan = run_video_plan(workspace)

            self.assertEqual(result["status"], "completed")
            first_segment = plan_segments(plan)[0]
            self.assertEqual(first_segment["first_frame"]["source_type"], "existing_raw_video")
            self.assertEqual(first_segment["first_frame"]["source_path"], raw_rel)
            self.assertEqual(first_segment["planned_outputs"]["raw_video_path"], raw_rel)
            self.assertFalse(first_segment["tasks"]["need_image"])
            self.assertFalse(first_segment["tasks"]["need_image_prompt"])
            self.assertFalse(first_segment["tasks"]["need_video"])

    def test_video_plan_original_image_with_existing_prompt_does_not_rebuild_image_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_key = asset_key(1, 1, 1)
            workspace = make_workspace(Path(tmp), source_images={first_key})
            prompt_rel = working_rel(first_key, "ImagePrompt.json")
            touch(workspace, prompt_rel, b"{}")

            result, plan = run_video_plan(workspace)

            self.assertEqual(result["status"], "completed")
            first_segment = plan_segments(plan)[0]
            self.assertEqual(first_segment["first_frame"]["source_type"], "original_image")
            self.assertEqual(first_segment["planned_outputs"]["image_prompt_path"], prompt_rel)
            self.assertFalse(first_segment["tasks"]["need_image_prompt"])
            self.assertTrue(first_segment["tasks"]["need_image"])

    def test_video_plan_existing_image_prompt_can_anchor_cutaway_without_original_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_key = asset_key(2, 1, 1)
            workspace = make_workspace(
                Path(tmp),
                source_images={asset_key(2, 2, 1)},
                final_videos={asset_key(1, 2, 3)},
                tail_frames={asset_key(1, 2, 3)},
                cutaway_dialogues={asset_key(1, 2, 3), first_key},
            )
            prompt_rel = working_rel(first_key, "ImagePrompt.json")
            touch(workspace, prompt_rel, b"{}")

            result, plan = run_video_plan(workspace, target_type="shot", max_seconds=10.0)

            self.assertEqual(result["status"], "completed")
            first_scene = plan["shots"][0]["scenes"][0]
            self.assertEqual(first_scene["status"], "planned")
            first_segment = first_scene["segments"][0]
            self.assertEqual(first_segment["first_frame"]["source_type"], "existing_image_prompt")
            self.assertEqual(first_segment["planned_outputs"]["image_prompt_path"], prompt_rel)
            self.assertFalse(first_segment["tasks"]["need_image_prompt"])
            self.assertTrue(first_segment["tasks"]["need_image"])
            self.assertTrue(first_segment["tasks"]["need_video"])

    def test_video_only_plan_single_scene_cases_1_to_9_match_slot_colors(self) -> None:
        slot_state = load_module(SLOT_STATE_PATH, "koubo_slot_state_vop_single_cases")
        rows = [
            ("case_1_original_only", [0, 1, 0, 0, 0], False, {}, {"audio": "白", "image": "白", "video_prompt": "灰", "raw_video": "灰", "copy_final": "灰"}),
            ("case_2_image_without_prompt", [0, 0, 1, 0, 0], False, {}, {"audio": "白", "image": "绿", "video_prompt": "白", "raw_video": "灰", "copy_final": "灰"}),
            ("case_3_image_and_prompt", [0, 0, 1, 0, 0], True, {}, {"audio": "白", "image": "绿", "video_prompt": "绿", "raw_video": "白", "copy_final": "灰"}),
            ("case_4_raw_pending_confirm", [0, 0, 0, 1, 0], False, {}, {"audio": "白", "image": "灰", "video_prompt": "灰", "raw_video": "绿", "copy_final": "白"}),
            ("case_5_final_without_raw", [0, 0, 0, 0, 1], False, {}, {"audio": "白", "image": "灰", "video_prompt": "灰", "raw_video": "灰", "copy_final": "绿"}),
            ("case_6_raw_and_final", [1, 0, 0, 1, 1], False, {}, {"audio": "绿", "image": "灰", "video_prompt": "灰", "raw_video": "绿", "copy_final": "绿"}),
            ("case_7_audio_not_required_for_confirm", [0, 0, 0, 1, 0], False, {}, {"audio": "白", "image": "灰", "video_prompt": "灰", "raw_video": "绿", "copy_final": "白"}),
            ("case_8_raw_running", [0, 0, 1, 0, 0], True, {"raw_video": "running"}, {"audio": "白", "image": "绿", "video_prompt": "绿", "raw_video": "黄", "copy_final": "灰"}),
            ("case_9_raw_failed", [0, 0, 1, 0, 0], True, {"raw_video": "failed"}, {"audio": "白", "image": "绿", "video_prompt": "绿", "raw_video": "红", "copy_final": "灰"}),
        ]
        for name, vector, prompt_exists, execution, expected in rows:
            with self.subTest(name):
                states = slot_state.derive_video_only_plan_slot_states(
                    slot_state.slot_inputs_from_vector(vector, video_prompt_exists=prompt_exists),
                    execution,
                )
                self.assertEqual({key: states[key]["color_zh"] for key in expected}, expected)

    def test_image_plan_single_scene_cases_1_to_9_match_slot_colors(self) -> None:
        slot_state = load_module(SLOT_STATE_PATH, "koubo_slot_state_ip_single_cases")
        rows = [
            ("case_1_original_only", [0, 1, 0, 0, 0], False, {}, {"image_prompt": "白", "image": "灰"}),
            ("case_2_original_and_prompt", [0, 1, 0, 0, 0], True, {}, {"image_prompt": "绿", "image": "白"}),
            ("case_3_image_exists", [0, 0, 1, 0, 0], False, {}, {"image_prompt": "灰", "image": "绿"}),
            ("case_4_prompt_without_original", [0, 0, 0, 0, 0], True, {}, {"image_prompt": "绿", "image": "灰"}),
            ("case_5_raw_consumes_image", [0, 1, 0, 1, 0], False, {}, {"image_prompt": "灰", "image": "灰"}),
            ("case_6_final_consumes_image_prompt_stays_green", [0, 0, 0, 0, 1], True, {}, {"image_prompt": "绿", "image": "灰"}),
            ("case_7_audio_does_not_affect_image_plan", [1, 1, 0, 0, 0], False, {}, {"image_prompt": "白", "image": "灰"}),
            ("case_8_image_running", [0, 1, 0, 0, 0], True, {"image": "running"}, {"image_prompt": "绿", "image": "黄"}),
            ("case_9_image_failed", [0, 1, 0, 0, 0], True, {"image": "failed"}, {"image_prompt": "绿", "image": "红"}),
        ]
        for name, vector, prompt_exists, execution, expected in rows:
            with self.subTest(name):
                states = slot_state.derive_image_plan_slot_states(
                    slot_state.slot_inputs_from_vector(vector, image_prompt_exists=prompt_exists),
                    execution,
                )
                self.assertEqual({key: states[key]["color_zh"] for key in expected}, expected)

    def test_video_only_plan_cross_case_10_prompt_controls_raw_for_all_source_segments(self) -> None:
        generator = load_module(VIDEO_ONLY_PLAN_GENERATOR_PATH, "analysis_v1_05_05_cross_case_10")
        with tempfile.TemporaryDirectory() as tmp:
            first_key = asset_key(1, 1, 1)
            workspace = make_workspace(Path(tmp), source_images={first_key}, raw_videos=set())
            touch(workspace, working_rel(first_key, "Image_New.png"), b"fake-image")
            touch(workspace, working_rel(first_key, "VideoPrompt.json"), b"{}")

            result = generator.run(
                generator.parse_args(
                    ["--workspace", str(workspace), "--target-type", "task", "--max-video-seconds", "4", "--split-tolerance-seconds", "0", "--force"]
                )
            )

            self.assertEqual(result["status"], "completed")
            plan = json.loads((workspace / "SessionOutput/storyboard/video_only_generation_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["summary"]["total_tasks"], 8)
            first_task = plan["video_only_tasks"][0]
            self.assertEqual(first_task["dialogue_ids"], ["s1c1_d1", "s1c1_d2"])
            self.assertEqual(first_task["steps"]["first_frame"]["status"], "completed_working")
            self.assertEqual(first_task["steps"]["prompt"]["status"], "completed_working")
            self.assertEqual(first_task["steps"]["video"]["status"], "pending")
            self.assertTrue(all(task["steps"]["video"]["status"] == "pending" for task in plan["video_only_tasks"]))

    def test_video_only_plan_cross_case_11_raw_existing_requires_confirm_for_each_unfinalized_segment(self) -> None:
        generator = load_module(VIDEO_ONLY_PLAN_GENERATOR_PATH, "analysis_v1_05_05_cross_case_11")
        segment_keys = [
            asset_key(1, 1, 1),
            asset_key(1, 1, 3),
            asset_key(1, 2, 1),
            asset_key(1, 2, 3),
            asset_key(2, 1, 1),
            asset_key(2, 1, 3),
            asset_key(2, 2, 1),
            asset_key(2, 2, 3),
        ]
        finalized = set(segment_keys[:4])
        pending = set(segment_keys[4:])
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp), source_images={asset_key(1, 1, 1)}, final_videos=finalized, raw_videos=set(segment_keys))

            result = generator.run(
                generator.parse_args(
                    ["--workspace", str(workspace), "--target-type", "task", "--max-video-seconds", "4", "--split-tolerance-seconds", "0", "--force"]
                )
            )

            self.assertEqual(result["status"], "completed")
            plan = json.loads((workspace / "SessionOutput/storyboard/video_only_generation_plan.json").read_text(encoding="utf-8"))
            by_key = {task["asset_key"]: task for task in plan["video_only_tasks"]}
            self.assertEqual(len(by_key), 10)
            for key in finalized:
                self.assertEqual(by_key[key]["status"], "final_completed")
                self.assertEqual(by_key[key]["steps"]["confirm_final"]["status"], "completed_working")
            for key in pending:
                self.assertEqual(by_key[key]["status"], "raw_completed_pending_final")
                self.assertEqual(by_key[key]["steps"]["confirm_final"]["status"], "pending")
            for key in (asset_key(1, 1, 2), asset_key(1, 2, 2)):
                self.assertEqual(by_key[key]["status"], "planned_prompt_and_video")
                self.assertEqual(by_key[key]["source_segment"]["first_frame"]["source_type"], "previous_segment_tail_frame")

    def test_image_plan_cross_case_10_prompt_controls_image_for_all_source_segments(self) -> None:
        generator = load_module(IMAGE_PLAN_GENERATOR_PATH, "analysis_v1_05_03_cross_case_10")
        with tempfile.TemporaryDirectory() as tmp:
            first_key = asset_key(1, 1, 1)
            workspace = make_workspace(Path(tmp), source_images={first_key})
            touch(workspace, working_rel(first_key, "ImagePrompt.json"), b"{}")

            result = generator.run(
                generator.parse_args(
                    ["--workspace", str(workspace), "--target-type", "task", "--max-video-seconds", "4", "--split-tolerance-seconds", "0", "--force"]
                )
            )

            self.assertEqual(result["status"], "completed")
            plan = json.loads((workspace / "SessionOutput/storyboard/image_generation_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["summary"]["total_tasks"], 8)
            self.assertEqual(plan["summary"]["planned_image_tasks"], 1)
            self.assertEqual(plan["image_tasks"][0]["status"], "planned_image_from_existing_prompt")
            self.assertTrue(all(task["status"] == "skipped" for task in plan["image_tasks"][1:]))

    def test_image_plan_cross_case_11_downstream_raw_final_and_existing_image_colors(self) -> None:
        slot_state = load_module(SLOT_STATE_PATH, "koubo_slot_state_ip_cross_case_11")
        rows = [
            ("S1C1-IP1", [0, 0, 1, 1, 1], False, {"image_prompt": "灰", "image": "绿"}),
            ("S1C1-IP2", [0, 0, 0, 1, 1], False, {"image_prompt": "灰", "image": "灰"}),
            ("S1C2-IP1", [0, 0, 0, 1, 0], False, {"image_prompt": "灰", "image": "灰"}),
            ("S1C2-IP2", [0, 0, 0, 0, 1], False, {"image_prompt": "灰", "image": "灰"}),
            ("S2C1-IP1", [0, 1, 0, 1, 0], False, {"image_prompt": "灰", "image": "灰"}),
            ("S2C1-IP2", [0, 1, 0, 0, 1], False, {"image_prompt": "灰", "image": "灰"}),
            ("S2C2-IP1", [0, 0, 1, 0, 0], False, {"image_prompt": "灰", "image": "绿"}),
            ("S2C2-IP2", [0, 1, 0, 0, 0], False, {"image_prompt": "白", "image": "灰"}),
        ]
        for name, vector, prompt_exists, expected in rows:
            with self.subTest(name):
                states = slot_state.derive_image_plan_slot_states(
                    slot_state.slot_inputs_from_vector(vector, image_prompt_exists=prompt_exists)
                )
                self.assertEqual({key: states[key]["color_zh"] for key in expected}, expected)

    def test_video_plan_task_scope_splits_every_scene_and_chains_planned_tail_across_shots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp), source_images={asset_key(1, 1, 1)})

            result, plan = run_video_plan(workspace)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(plan["summary"]["shot_count"], 2)
            self.assertEqual(plan["summary"]["scene_count"], 4)
            self.assertEqual(plan["summary"]["segment_count"], 8)
            self.assertEqual(plan["summary"]["dialogue_count"], 12)
            expected_dialogue_groups = [
                ["s1c1_d1", "s1c1_d2"],
                ["s1c1_d3"],
                ["s1c2_d1", "s1c2_d2"],
                ["s1c2_d3"],
                ["s2c1_d1", "s2c1_d2"],
                ["s2c1_d3"],
                ["s2c2_d1", "s2c2_d2"],
                ["s2c2_d3"],
            ]
            segments = plan_segments(plan)
            self.assertEqual([segment["dialogue_ids"] for segment in segments], expected_dialogue_groups)
            self.assertEqual(segments[0]["first_frame"]["source_type"], "original_image")
            self.assertTrue(all(segment["duration"] <= 4.0 for segment in segments))
            self.assertTrue(all(segment["first_frame"]["source_type"] == "previous_segment_tail_frame" for segment in segments[1:]))
            self.assertEqual(segments[4]["dependencies"]["depends_on_segment_id"], "shot_001_scene_002_segment_002")

    def test_video_plan_shot_scope_blocks_when_previous_scene_final_has_no_tail_then_recovers_on_own_visual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(
                Path(tmp),
                source_images={asset_key(2, 2, 1)},
                final_videos={asset_key(1, 2, 3)},
            )

            result, plan = run_video_plan(workspace, target_type="shot")

            self.assertEqual(result["status"], "completed_with_blocked_items")
            shot = plan["shots"][0]
            self.assertEqual(shot["shot_id"], "shot_002")
            blocked_scene, recovered_scene = shot["scenes"]
            self.assertEqual(blocked_scene["status"], "blocked")
            self.assertEqual(blocked_scene["blocked_reason"]["code"], "scene_first_dialogue_missing_first_frame_and_previous_tail_missing")
            self.assertEqual(recovered_scene["status"], "planned")
            self.assertEqual(recovered_scene["segments"][0]["first_frame"]["source_type"], "original_image")

    def test_video_plan_shot_scope_blocks_cutaway_tail_then_recovers_on_own_visual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(
                Path(tmp),
                source_images={asset_key(2, 2, 1)},
                final_videos={asset_key(1, 2, 3)},
                tail_frames={asset_key(1, 2, 3)},
                cutaway_dialogues={asset_key(1, 2, 3)},
            )

            result, plan = run_video_plan(workspace, target_type="shot")

            self.assertEqual(result["status"], "completed_with_blocked_items")
            blocked_scene, recovered_scene = plan["shots"][0]["scenes"]
            self.assertEqual(blocked_scene["blocked_reason"]["code"], "previous_segment_cutaway_tail_not_usable")
            self.assertEqual(recovered_scene["status"], "planned")

    def test_video_only_plan_reuses_non_single_scene_segments_and_marks_audio_merge_by_segment_dialogues(self) -> None:
        generator = load_module(VIDEO_ONLY_PLAN_GENERATOR_PATH, "analysis_v1_05_05_non_single_contract")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp), source_images={asset_key(1, 1, 1)})

            result = generator.run(
                generator.parse_args(
                    ["--workspace", str(workspace), "--target-type", "task", "--max-video-seconds", "4", "--split-tolerance-seconds", "0", "--force"]
                )
            )

            self.assertEqual(result["status"], "completed")
            plan = json.loads((workspace / "SessionOutput/storyboard/video_only_generation_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["summary"]["total_tasks"], 8)
            task = plan["video_only_tasks"][0]
            self.assertEqual(task["dialogue_ids"], ["s1c1_d1", "s1c1_d2"])
            self.assertEqual(task["steps"]["audio"]["status"], "completed_working")
            audio_tasks = task["source_segment"]["dialogue_audio_tasks"]
            self.assertEqual([item["srt_id"] for item in audio_tasks], ["s1c1_d1", "s1c1_d2"])
            self.assertTrue(all(not item["need_audio"] for item in audio_tasks))

    def test_video_only_plan_uses_same_bound_video_segment_truth_as_video_plan(self) -> None:
        generator = load_module(VIDEO_ONLY_PLAN_GENERATOR_PATH, "analysis_v1_05_05_bound_segment_truth_contract")
        with tempfile.TemporaryDirectory() as tmp:
            first_key = asset_key(1, 1, 1)
            workspace = make_workspace(Path(tmp), final_videos={first_key})

            result = generator.run(
                generator.parse_args(
                    ["--workspace", str(workspace), "--target-type", "task", "--max-video-seconds", "4", "--split-tolerance-seconds", "0", "--force"]
                )
            )

            self.assertEqual(result["status"], "completed")
            plan = json.loads((workspace / "SessionOutput/storyboard/video_only_generation_plan.json").read_text(encoding="utf-8"))
            first_task, second_task = plan["video_only_tasks"][:2]
            self.assertEqual(first_task["dialogue_ids"], ["s1c1_d1"])
            self.assertEqual(first_task["status"], "final_completed")
            self.assertEqual(first_task["source_segment"]["first_frame"]["source_type"], "bound_video")
            self.assertEqual(second_task["dialogue_ids"], ["s1c1_d2", "s1c1_d3"])
            self.assertEqual(second_task["source_segment"]["first_frame"]["source_type"], "previous_segment_tail_frame")
            self.assertEqual(second_task["source_segment"]["dependencies"]["depends_on_segment_id"], "shot_001_scene_001_segment_001")

    def test_video_only_plan_raw_without_final_requires_confirm_final(self) -> None:
        generator = load_module(VIDEO_ONLY_PLAN_GENERATOR_PATH, "analysis_v1_05_05_non_single_confirm_contract")
        with tempfile.TemporaryDirectory() as tmp:
            first_key = asset_key(1, 1, 1)
            workspace = make_workspace(Path(tmp), source_images={first_key})
            touch(workspace, working_rel(first_key, "Image_New.png"), b"fake-image")
            touch(workspace, working_rel(first_key, "VideoPrompt.json"), b"{}")
            touch(workspace, working_rel(first_key, "Video_Raw.mp4"), b"fake-raw")

            generator.run(
                generator.parse_args(
                    ["--workspace", str(workspace), "--target-type", "task", "--max-video-seconds", "4", "--split-tolerance-seconds", "0", "--force"]
                )
            )
            plan = json.loads((workspace / "SessionOutput/storyboard/video_only_generation_plan.json").read_text(encoding="utf-8"))

            task = plan["video_only_tasks"][0]
            self.assertEqual(task["asset_key"], first_key)
            self.assertEqual(task["status"], "raw_completed_pending_final")
            self.assertEqual(task["steps"]["prompt"]["status"], "completed_working")
            self.assertEqual(task["steps"]["video"]["status"], "completed_working")
            self.assertEqual(task["steps"]["confirm_final"]["status"], "pending")

    def test_image_plan_reuses_video_plan_split_and_only_emits_image_required_segments(self) -> None:
        generator = load_module(IMAGE_PLAN_GENERATOR_PATH, "analysis_v1_05_03_non_single_contract")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp), source_images={asset_key(1, 1, 1)})

            result = generator.run(
                generator.parse_args(
                    ["--workspace", str(workspace), "--target-type", "task", "--max-video-seconds", "4", "--split-tolerance-seconds", "0", "--force"]
                )
            )

            self.assertEqual(result["status"], "completed")
            plan = json.loads((workspace / "SessionOutput/storyboard/image_generation_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["summary"]["total_tasks"], 8)
            self.assertEqual(plan["summary"]["planned_prompt_tasks"], 1)
            self.assertEqual(plan["summary"]["planned_image_tasks"], 1)
            task = plan["image_tasks"][0]
            self.assertEqual(task["dialogue_ids"], ["s1c1_d1", "s1c1_d2"])
            self.assertEqual(task["source_segment"]["segment_id"], "shot_001_scene_001_segment_001")
            self.assertEqual(task["status"], "planned_prompt_and_image")
            self.assertTrue(all(item["status"] == "skipped" for item in plan["image_tasks"][1:]))

    def test_slot_state_rules_cover_video_only_and_image_plan_non_single_scene_key_colors(self) -> None:
        slot_state = load_module(SLOT_STATE_PATH, "koubo_slot_state_non_single_contract")
        slot_inputs_from_vector = slot_state.slot_inputs_from_vector

        video_only_prompt_ready = slot_state.derive_video_only_plan_slot_states(
            slot_inputs_from_vector([0, 0, 1, 0, 0], video_prompt_exists=True)
        )
        self.assertEqual(video_only_prompt_ready["video_prompt"]["color_zh"], "绿")
        self.assertEqual(video_only_prompt_ready["raw_video"]["color_zh"], "白")
        self.assertEqual(video_only_prompt_ready["copy_final"]["color_zh"], "灰")

        video_only_raw_ready = slot_state.derive_video_only_plan_slot_states(slot_inputs_from_vector([0, 0, 0, 1, 0]))
        self.assertEqual(video_only_raw_ready["raw_video"]["color_zh"], "绿")
        self.assertEqual(video_only_raw_ready["copy_final"]["color_zh"], "白")

        video_only_unbound_final = slot_state.derive_video_only_plan_slot_states(
            slot_state.SlotInputs(final_exists=True, binding_missing=True)
        )
        self.assertEqual(video_only_unbound_final["copy_final"]["color_zh"], "绿")
        self.assertEqual(video_only_unbound_final["copy_final"]["ui_tone"], "done")
        self.assertEqual(video_only_unbound_final["copy_final"]["binding_consistency"], "file_exists_unbound")

        image_prompt_ready = slot_state.derive_image_plan_slot_states(slot_inputs_from_vector([0, 1, 0, 0, 0]))
        self.assertEqual(image_prompt_ready["image_prompt"]["color_zh"], "白")
        self.assertEqual(image_prompt_ready["image"]["color_zh"], "灰")

        image_ready = slot_state.derive_image_plan_slot_states(slot_inputs_from_vector([0, 1, 0, 0, 0], image_prompt_exists=True))
        self.assertEqual(image_ready["image_prompt"]["color_zh"], "绿")
        self.assertEqual(image_ready["image"]["color_zh"], "白")


if __name__ == "__main__":
    unittest.main()
