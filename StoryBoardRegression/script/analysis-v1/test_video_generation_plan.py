from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "OpenCrew" / "ToolLibrary" / "Analysis_V1" / "05_01_VideoPlanGenerator.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analysis_v1_video_generation_plan", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dialogue(
    srt_id: str,
    start: float,
    end: float,
    image_path: str = "",
    working_assets: dict | None = None,
    video_plan: dict | None = None,
) -> dict:
    item = {
        "srt_id": srt_id,
        "dialogue_asset_key": srt_id,
        "dialogue": f"dialogue {srt_id}",
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "image_path": image_path,
    }
    if working_assets is not None:
        item["working_assets"] = working_assets
    if video_plan is not None:
        item["video_plan"] = video_plan
    return item


def scene(scene_id: str, items: list[dict]) -> dict:
    return {
        "scene_id": scene_id,
        "start": items[0]["start"] if items else 0,
        "end": items[-1]["end"] if items else 0,
        "duration": round((items[-1]["end"] - items[0]["start"]) if items else 0, 3),
        "dialogue_items": items,
    }


def make_workspace(tmp_path: Path, scenes_by_shot: list[tuple[str, list[dict]]]) -> Path:
    workspace = tmp_path / "workspace"
    write_json(
        workspace / "SessionContext" / "Variables.json",
        {
            "schema_version": "analysis_v1_session_context_0.1",
            "workflow_id": "openclip_analysis",
            "workspace_dir": str(workspace),
        },
    )
    shots = []
    for shot_id, scenes in scenes_by_shot:
        shots.append({"shot_id": shot_id, "scenes": scenes})
    write_json(
        workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json",
        {
            "schema_version": "analysis_v1_srt_storyboard_0.2",
            "shots": shots,
        },
    )
    return workspace


def run_tool(module: ModuleType, workspace: Path, **overrides) -> dict:
    values = {
        "workspace": str(workspace),
        "target_type": "task",
        "shot_id": "",
        "scene_id": "",
        "max_video_seconds": 4.0,
        "min_video_seconds": 4.0,
        "split_tolerance_seconds": 1.0,
        "force": False,
        "resume": False,
        "print_json": False,
    }
    values.update(overrides)
    return module.run(module.Args(**values))


def read_plan(workspace: Path) -> dict:
    return json.loads((workspace / "SessionOutput" / "storyboard" / "video_generation_plan.json").read_text(encoding="utf-8"))


def all_segments(plan: dict) -> list[dict]:
    segments: list[dict] = []
    for shot in plan.get("shots", []):
        for scene_payload in shot.get("scenes", []):
            segments.extend(scene_payload.get("segments", []))
    return segments


def test_scene_target_requires_shot_and_scene_id(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )

    result = run_tool(module, workspace, target_type="scene", shot_id="", scene_id="")

    assert result["status"] == "blocked"
    assert result["blocked_reasons"][0]["code"] == "scene_target_requires_ids"


def test_shot_target_requires_shot_id(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )

    result = run_tool(module, workspace, target_type="shot", shot_id="")

    assert result["status"] == "blocked"
    assert result["blocked_reasons"][0]["code"] == "shot_target_requires_shot_id"


def test_task_target_accepts_entire_storyboard(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            ("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])]),
            ("shot_002", [scene("scene_002", [dialogue("srt_0002", 1, 2, "SessionOutput/visual/srt_frames/srt_0002.jpg")])]),
        ],
    )

    result = run_tool(module, workspace, target_type="task")

    assert result["status"] == "completed"
    plan = read_plan(workspace)
    assert plan["summary"]["shot_count"] == 2
    assert [shot["shot_id"] for shot in plan["shots"]] == ["shot_001", "shot_002"]


def test_missing_consistency_reference_images_are_recorded_but_non_blocking(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    refs = read_plan(workspace)["consistency_references"]
    assert refs["status"] == "missing_reference_images"
    assert refs["blocking"] is False
    assert {item["kind"] for item in refs["missing"]} == {"host", "product"}


def test_consistency_reference_images_are_detected(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )
    consistency_dir = workspace / "SessionContext" / "Consistency"
    consistency_dir.mkdir(parents=True, exist_ok=True)
    (consistency_dir / "HOST.png").write_bytes(b"host")
    (consistency_dir / "Product.png").write_bytes(b"product")

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    refs = read_plan(workspace)["consistency_references"]
    assert refs["status"] == "ready"
    assert refs["missing"] == []
    assert {item["output_path"] for item in refs["references"]} == {
        "SessionContext/Consistency/HOST.png",
        "SessionContext/Consistency/Product.png",
    }


def test_blocked_when_storyboard_missing(tmp_path) -> None:
    module = load_tool()
    workspace = tmp_path / "workspace"
    write_json(workspace / "SessionContext" / "Variables.json", {"workspace_dir": str(workspace)})

    result = run_tool(module, workspace)

    assert result["status"] == "blocked"
    assert result["blocked_reasons"][0]["code"] == "storyboard_missing"


def test_task_target_splits_multi_image_and_overlong_anchor_range(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg"),
                            dialogue("srt_0002", 1, 3),
                            dialogue("srt_0003", 3, 6),
                            dialogue("srt_0004", 6, 7, "SessionOutput/visual/srt_frames/srt_0004.jpg"),
                        ],
                    )
                ],
            )
        ],
    )
    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    plan = read_plan(workspace)
    segments = plan["shots"][0]["scenes"][0]["segments"]
    assert [segment["dialogue_ids"] for segment in segments] == [["srt_0001", "srt_0002"], ["srt_0003"], ["srt_0004"]]
    assert segments[0]["tasks"]["need_image_prompt"] is True
    assert segments[1]["first_frame"]["source_type"] == "previous_segment_tail_frame"
    assert segments[1]["dependencies"]["depends_on_segment_id"] == segments[0]["segment_id"]
    assert segments[2]["first_frame"]["source_type"] == "original_image"


def test_first_middle_middle_tail_images_split_expected_segments(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg"),
                            dialogue("srt_0002", 1, 2),
                            dialogue("srt_0003", 2, 3, "SessionOutput/visual/srt_frames/srt_0003.jpg"),
                            dialogue("srt_0004", 3, 4),
                            dialogue("srt_0005", 4, 5, "SessionOutput/visual/srt_frames/srt_0005.jpg"),
                            dialogue("srt_0006", 5, 6, "SessionOutput/visual/srt_frames/srt_0006.jpg"),
                        ],
                    )
                ],
            )
        ],
    )
    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    segments = read_plan(workspace)["shots"][0]["scenes"][0]["segments"]
    assert [segment["dialogue_ids"] for segment in segments] == [
        ["srt_0001", "srt_0002"],
        ["srt_0003", "srt_0004"],
        ["srt_0005"],
        ["srt_0006"],
    ]


def test_every_dialogue_has_image_creates_one_segment_per_dialogue(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0, 0.7, "SessionOutput/visual/srt_frames/srt_0001.jpg"),
                            dialogue("srt_0002", 0.7, 1.4, "SessionOutput/visual/srt_frames/srt_0002.jpg"),
                            dialogue("srt_0003", 1.4, 2.1, "SessionOutput/visual/srt_frames/srt_0003.jpg"),
                            dialogue("srt_0004", 2.1, 2.8, "SessionOutput/visual/srt_frames/srt_0004.jpg"),
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    segments = read_plan(workspace)["shots"][0]["scenes"][0]["segments"]
    assert [segment["dialogue_ids"] for segment in segments] == [["srt_0001"], ["srt_0002"], ["srt_0003"], ["srt_0004"]]
    assert all(segment["planned_video_duration"] == 4.0 for segment in segments)
    assert all(segment["tasks"]["need_lipsync"] is True for segment in segments)
    assert all(segment["tasks"]["lipsync_disabled_by_ui"] is False for segment in segments)
    assert all(segment["tasks"]["lipsync_reason"] == "visible_face" for segment in segments)
    assert all(segment["planned_outputs"]["segment_audio_path"] for segment in segments)


def test_single_image_long_scene_splits_near_max_dialogue_boundary(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg"),
                            dialogue("srt_0002", 1, 2),
                            dialogue("srt_0003", 2, 3),
                            dialogue("srt_0004", 3, 4),
                            dialogue("srt_0005", 4, 5),
                            dialogue("srt_0006", 5, 6),
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    segments = read_plan(workspace)["shots"][0]["scenes"][0]["segments"]
    assert [segment["dialogue_ids"] for segment in segments] == [["srt_0001", "srt_0002", "srt_0003", "srt_0004"], ["srt_0005", "srt_0006"]]
    assert segments[1]["first_frame"]["source_type"] == "previous_segment_tail_frame"


def test_unplaced_upload_asset_cannot_be_first_frame(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1)])])],
    )
    upload = workspace / "SessionOutput" / "storyboard" / "assets" / "images" / "upload_001.png"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"upload")

    result = run_tool(module, workspace)

    assert result["status"] == "completed_with_skipped_items"
    assert read_plan(workspace)["shots"][0]["scenes"][0]["status"] == "skipped"


def test_first_scene_without_visual_source_is_skipped_but_later_visual_scene_runs(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene("scene_001", [dialogue("srt_0001", 0, 1), dialogue("srt_0002", 1, 2)]),
                    scene("scene_002", [dialogue("srt_0003", 2, 3, "SessionOutput/visual/srt_frames/srt_0003.jpg")]),
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed_with_skipped_items"
    scenes = read_plan(workspace)["shots"][0]["scenes"]
    assert scenes[0]["status"] == "skipped"
    assert scenes[0]["skipped_reason"]["code"] == "first_scene_missing_visual_source"
    assert scenes[1]["status"] == "planned"
    assert len(scenes[1]["segments"]) == 1


def test_zero_image_non_first_scene_uses_previous_planned_tail(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")]),
                    scene("scene_002", [dialogue("srt_0002", 1, 2), dialogue("srt_0003", 2, 3)]),
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    scenes = read_plan(workspace)["shots"][0]["scenes"]
    assert scenes[1]["segments"][0]["first_frame"]["source_type"] == "previous_segment_tail_frame"
    assert scenes[1]["segments"][0]["dependencies"]["depends_on_segment_id"] == scenes[0]["segments"][0]["segment_id"]


def test_zero_image_non_first_scene_blocks_without_previous_tail(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            ("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1)])]),
            ("shot_002", [scene("scene_002", [dialogue("srt_0002", 1, 2)])]),
        ],
    )

    result = run_tool(module, workspace, target_type="shot", shot_id="shot_002")

    assert result["status"] == "completed_with_blocked_items"
    scene_payload = read_plan(workspace)["shots"][0]["scenes"][0]
    assert scene_payload["status"] == "blocked"
    assert scene_payload["blocked_reason"]["code"] == "scene_first_dialogue_missing_first_frame_and_previous_tail_missing"


def test_task_scope_carries_tail_frame_across_shots(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            ("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])]),
            ("shot_002", [scene("scene_002", [dialogue("srt_0002", 1, 2), dialogue("srt_0003", 2, 3)])]),
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    shots = read_plan(workspace)["shots"]
    assert shots[1]["scenes"][0]["segments"][0]["first_frame"]["source_type"] == "previous_segment_tail_frame"
    assert shots[1]["scenes"][0]["segments"][0]["dependencies"]["depends_on_segment_id"] == shots[0]["scenes"][0]["segments"][0]["segment_id"]


def test_blocked_scene_does_not_drop_following_visual_scene(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene("scene_001", [dialogue("srt_0001", 0, 1)]),
                    scene("scene_002", [dialogue("srt_0002", 1, 2)]),
                    scene("scene_003", [dialogue("srt_0003", 2, 3, "SessionOutput/visual/srt_frames/srt_0003.jpg")]),
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed_with_blocked_items"
    scenes = read_plan(workspace)["shots"][0]["scenes"]
    assert [item["status"] for item in scenes] == ["skipped", "blocked", "planned"]


def test_scene_scope_requires_existing_previous_tail_file(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")]),
                    scene("scene_002", [dialogue("srt_0002", 1, 2)]),
                ],
            )
        ],
    )

    result = run_tool(module, workspace, target_type="scene", shot_id="shot_001", scene_id="scene_002")

    assert result["status"] == "completed_with_blocked_items"
    assert read_plan(workspace)["shots"][0]["scenes"][0]["status"] == "blocked"


def test_scene_scope_can_use_existing_previous_tail_file(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue(
                                "srt_0001",
                                0,
                                1,
                                "SessionOutput/visual/srt_frames/srt_0001.jpg",
                                {"video": {"path": "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4"}},
                            )
                        ],
                    ),
                    scene("scene_002", [dialogue("srt_0002", 1, 2)]),
                ],
            )
        ],
    )
    (workspace / "SessionOutput" / "storyboard" / "Working").mkdir(parents=True)
    (workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_Video_Final.mp4").write_bytes(b"video")
    (workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_TailFrame.png").write_bytes(b"tail")

    result = run_tool(module, workspace, target_type="scene", shot_id="shot_001", scene_id="scene_002")

    assert result["status"] == "completed"
    segment = read_plan(workspace)["shots"][0]["scenes"][0]["segments"][0]
    assert segment["first_frame"]["source_type"] == "previous_scene_tail_frame"
    assert segment["dependencies"]["depends_on_tail_frame_path"] == "SessionOutput/storyboard/Working/srt_0001_TailFrame.png"


def test_single_overlong_dialogue_is_not_split(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 6, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )

    result = run_tool(module, workspace, max_video_seconds=4.0)

    assert result["status"] == "completed"
    segments = read_plan(workspace)["shots"][0]["scenes"][0]["segments"]
    assert len(segments) == 1
    assert segments[0]["dialogue_ids"] == ["srt_0001"]
    assert segments[0]["duration_exceeds_limit_unavoidable"] is True


def test_generated_image_slot_can_be_first_frame_without_image_prompt(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue(
                                "srt_0001",
                                0,
                                1,
                                "",
                                {"images": [{"slot": "Image_New", "path": "SessionOutput/storyboard/Working/srt_0001_Image_New.png"}]},
                            )
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    segment = read_plan(workspace)["shots"][0]["scenes"][0]["segments"][0]
    assert segment["first_frame"]["source_type"] == "generated_image"
    assert segment["tasks"]["need_image_prompt"] is False
    assert segment["tasks"]["need_image"] is False
    assert segment["first_frame"]["materialize_first_frame"]["required"] is False


def test_placed_uploaded_image_records_materialize_copy_action(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue(
                                "srt_0001",
                                0,
                                1,
                                "",
                                {
                                    "images": [
                                        {
                                            "slot": "Image_New",
                                            "source_type": "placed_uploaded_image",
                                            "path": "SessionOutput/storyboard/assets/images/upload_001.png",
                                        }
                                    ]
                                },
                            )
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    first_frame = read_plan(workspace)["shots"][0]["scenes"][0]["segments"][0]["first_frame"]
    assert first_frame["source_type"] == "placed_uploaded_image"
    assert first_frame["materialize_first_frame"] == {
        "required": True,
        "copy_from_path": "SessionOutput/storyboard/assets/images/upload_001.png",
        "copy_to_path": "SessionOutput/storyboard/Working/srt_0001_Image_New.png",
        "source_type": "placed_uploaded_image",
    }


def test_video_outputs_use_first_dialogue_key_and_audio_is_dialogue_level(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue(
                                "srt_0001",
                                0,
                                1,
                                "SessionOutput/visual/srt_frames/srt_0001.jpg",
                                {"audio": {"path": "SessionOutput/storyboard/Working/srt_0001_Audio_Final.wav"}},
                            ),
                            dialogue("srt_0002", 1, 2),
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    segment = read_plan(workspace)["shots"][0]["scenes"][0]["segments"][0]
    assert segment["planned_outputs"]["video_path"] == "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4"
    assert segment["tail_frame"]["planned_path"] == "SessionOutput/storyboard/Working/srt_0001_TailFrame.png"
    assert segment["planned_outputs"]["segment_audio_path"] == "SessionOutput/storyboard/Working/srt_0001_SegmentAudio_Final.wav"
    assert segment["tasks"]["need_audio"] is True
    assert segment["tasks"]["need_lipsync"] is True
    assert segment["tasks"]["need_sync"] is True
    assert segment["tasks"]["sync_mode"] == "lipsync"
    assert segment["tasks"]["lipsync_disabled_by_ui"] is False
    assert segment["tasks"]["lipsync_reason"] == "visible_face"
    assert [item["need_audio"] for item in segment["dialogue_audio_tasks"]] == [False, True]
    assert read_plan(workspace)["summary"]["segment_audio_count"] == 1
    assert read_plan(workspace)["summary"]["need_lipsync_count"] == 1
    assert read_plan(workspace)["summary"]["need_sync_count"] == 1


def test_dialogue_cutaway_flag_disables_lipsync_for_segment(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue(
                                "srt_0001",
                                0,
                                1,
                                "SessionOutput/visual/srt_frames/srt_0001.jpg",
                                video_plan={
                                    "is_talking_head": False,
                                    "lipsync_override": "skip_cutaway",
                                    "lipsync_override_source": "storyboard_original_image_context_menu",
                                    "lipsync_override_reason": "user_marked_cutaway",
                                },
                            ),
                            dialogue("srt_0002", 1, 2),
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    segment = read_plan(workspace)["shots"][0]["scenes"][0]["segments"][0]
    assert segment["tasks"]["need_lipsync"] is False
    assert segment["tasks"]["need_audio_video_sync"] is True
    assert segment["tasks"]["need_sync"] is True
    assert segment["tasks"]["sync_mode"] == "audio_replace_retime"
    assert segment["tasks"]["lipsync_disabled_by_ui"] is True
    assert segment["tasks"]["lipsync_reason"] == "user_marked_cutaway"
    assert segment["tasks"]["lipsync_decision_source"] == "dialogue.video_plan.is_talking_head"
    assert read_plan(workspace)["summary"]["need_lipsync_count"] == 0
    assert read_plan(workspace)["summary"]["need_audio_video_sync_count"] == 1


def test_bound_video_dialogue_starts_new_audio_synced_segment(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg"),
                            dialogue("srt_0002", 1, 2),
                            dialogue("srt_0003", 2, 3, "", {"video": {"path": "SessionOutput/storyboard/assets/videos/upload_001.mp4"}}),
                            dialogue("srt_0004", 3, 4),
                            dialogue("srt_0005", 4, 5, "SessionOutput/visual/srt_frames/srt_0005.jpg"),
                        ],
                    )
                ],
            )
        ],
    )
    bound_video_path = workspace / "SessionOutput/storyboard/assets/videos/upload_001.mp4"
    bound_video_path.parent.mkdir(parents=True, exist_ok=True)
    bound_video_path.write_bytes(b"fake-bound-video")

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    segments = read_plan(workspace)["shots"][0]["scenes"][0]["segments"]
    assert [segment["dialogue_ids"] for segment in segments] == [["srt_0001", "srt_0002"], ["srt_0003"], ["srt_0004"], ["srt_0005"]]
    bound = segments[1]
    assert bound["status"] == "planned"
    assert bound["first_frame"]["source_type"] == "bound_video"
    assert bound["tasks"]["need_video_prompt"] is False
    assert bound["tasks"]["need_video"] is False
    assert bound["tasks"]["need_lipsync"] is False
    assert bound["tasks"]["need_audio_video_sync"] is True
    assert bound["tasks"]["need_sync"] is True
    assert bound["tasks"]["sync_mode"] == "audio_replace_retime"
    assert bound["tasks"]["lipsync_reason"] == "existing_video_bound_complete"
    assert bound["existing_video"]["materialize_video"]["copy_from_path"] == "SessionOutput/storyboard/assets/videos/upload_001.mp4"
    assert bound["existing_video"]["materialize_video"]["copy_to_path"] == "SessionOutput/storyboard/Working/srt_0003_Video_Final.mp4"
    assert bound["planned_outputs"]["video_path"] == "SessionOutput/storyboard/Working/srt_0003_Video_Final.mp4"
    assert bound["planned_outputs"]["video_prompt_path"] == ""
    tail_segment = segments[2]
    assert tail_segment["first_frame"]["source_type"] == "previous_segment_tail_frame"
    assert tail_segment["dependencies"]["depends_on_segment_id"] == bound["segment_id"]
    assert read_plan(workspace)["summary"]["need_video_count"] == 3


def test_cutaway_tail_blocks_following_empty_split_segment(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg", video_plan={"is_talking_head": False}),
                            dialogue("srt_0002", 1, 2),
                            dialogue("srt_0003", 2, 3),
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace, max_video_seconds=2.0)

    assert result["status"] == "completed_with_blocked_items"
    segments = read_plan(workspace)["shots"][0]["scenes"][0]["segments"]
    assert [segment["dialogue_ids"] for segment in segments] == [["srt_0001", "srt_0002"], ["srt_0003"]]
    assert segments[0]["tail_frame"]["continuation_allowed"] is False
    assert segments[1]["status"] == "blocked"
    assert segments[1]["blocked_reason"]["code"] == "previous_segment_cutaway_tail_not_usable"
    assert read_plan(workspace)["summary"]["blocked_segment_count"] == 1


def test_talking_head_tail_allows_following_empty_split_segment(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg", video_plan={"is_talking_head": True}),
                            dialogue("srt_0002", 1, 2),
                            dialogue("srt_0003", 2, 3),
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace, max_video_seconds=2.0)

    assert result["status"] == "completed"
    segments = read_plan(workspace)["shots"][0]["scenes"][0]["segments"]
    assert [segment["dialogue_ids"] for segment in segments] == [["srt_0001", "srt_0002"], ["srt_0003"]]
    assert segments[0]["tail_frame"]["continuation_allowed"] is True
    assert segments[1]["first_frame"]["source_type"] == "previous_segment_tail_frame"


def test_frontend_edit_file_is_not_used_as_plan_input(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg"),
                            dialogue("srt_0002", 1, 2),
                        ],
                    )
                ],
            )
        ],
    )
    write_json(
        workspace / "SessionOutput" / "storyboard" / "koubo_storyboard_edit.json",
        {
            "schema_version": "koubo_storyboard_edit_0.1",
            "shots": [
                {
                    "shot_id": "shot_001",
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "dialogues": [
                                {
                                    "srt_id": "srt_0001",
                                    "dialogue_id": "scene_001_dialogue_001",
                                    "start": 0,
                                    "end": 1,
                                    "duration": 1,
                                    "text": "dialogue srt_0001",
                                    "image_path": "SessionOutput/visual/srt_frames/srt_0001.jpg",
                                    "video_plan": {"is_talking_head": False},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    segment = read_plan(workspace)["shots"][0]["scenes"][0]["segments"][0]
    assert segment["tasks"]["need_lipsync"] is True
    assert segment["tasks"]["lipsync_decision_source"] == "default"


def test_each_short_dialogue_with_image_uses_model_minimum_video_duration(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0.0, 0.8, "SessionOutput/visual/srt_frames/srt_0001.jpg"),
                            dialogue("srt_0002", 0.8, 1.6, "SessionOutput/visual/srt_frames/srt_0002.jpg"),
                            dialogue("srt_0003", 1.6, 2.4, "SessionOutput/visual/srt_frames/srt_0003.jpg"),
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace, min_video_seconds=4.0)

    assert result["status"] == "completed"
    segments = read_plan(workspace)["shots"][0]["scenes"][0]["segments"]
    assert [segment["dialogue_ids"] for segment in segments] == [["srt_0001"], ["srt_0002"], ["srt_0003"]]
    assert [segment["duration"] for segment in segments] == [0.8, 0.8, 0.8]
    assert all(segment["planned_video_duration"] == 4.0 for segment in segments)
    assert all(segment["duration_padding_seconds"] == 3.2 for segment in segments)
    assert all(segment["duration_policy"] == "model_minimum_extended" for segment in segments)
    assert all(segment["planned_outputs"]["video_duration_seconds"] == 4.0 for segment in segments)


def test_no_prompt_directory_created_and_resume_reuses_matching_output(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )

    first = run_tool(module, workspace)
    second = run_tool(module, workspace, resume=True)

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert any(item["code"] == "reused_completed_output" for item in second["warnings"])
    assert not (workspace / "S8_05_01_VideoPlanGenerator" / "Prompt").exists()


def test_force_rerun_scope_does_not_delete_storyboard_or_assets(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )
    asset = workspace / "SessionOutput" / "storyboard" / "Working" / "keep.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"asset")

    result = run_tool(module, workspace, force=True)

    assert result["status"] == "completed"
    assert asset.exists()
    assert (workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json").exists()


def test_each_dialogue_appears_once_per_plan_and_times_are_backfilled(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [
            (
                "shot_001",
                [
                    scene(
                        "scene_001",
                        [
                            dialogue("srt_0001", 0.2, 1.2, "SessionOutput/visual/srt_frames/srt_0001.jpg"),
                            dialogue("srt_0002", 1.2, 2.5),
                            dialogue("srt_0003", 2.5, 3.7),
                        ],
                    )
                ],
            )
        ],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    plan = read_plan(workspace)
    ids = [dialogue_id for segment in all_segments(plan) for dialogue_id in segment["dialogue_ids"]]
    assert ids == ["srt_0001", "srt_0002", "srt_0003"]
    assert len(ids) == len(set(ids))
    segment = all_segments(plan)[0]
    assert segment["start"] == 0.2
    assert segment["end"] == 3.7
    assert segment["duration"] == 3.5


def test_result_json_has_no_secret_strings(tmp_path, monkeypatch) -> None:
    module = load_tool()
    monkeypatch.setenv("OPENCREW_DATABASE_URL", "postgresql://user:password@127.0.0.1/db")
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )

    result = run_tool(module, workspace)

    assert result["status"] == "completed"
    result_text = (workspace / "S8_05_01_VideoPlanGenerator" / "Report" / "Result.json").read_text(encoding="utf-8").lower()
    assert "postgresql://" not in result_text
    assert "password@127.0.0.1" not in result_text


def test_print_json_matches_result_json(tmp_path, capsys) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )

    exit_code = module.main(["--workspace", str(workspace), "--target-type", "task", "--print-json"])

    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    result_json = json.loads((workspace / "S8_05_01_VideoPlanGenerator" / "Report" / "Result.json").read_text(encoding="utf-8"))
    assert printed == result_json


def test_blocked_when_variables_missing(tmp_path) -> None:
    module = load_tool()
    workspace = tmp_path / "workspace"
    write_json(workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json", {"shots": []})

    result = run_tool(module, workspace)

    assert result["status"] == "blocked"
    assert result["blocked_reasons"][0]["code"] == "variables_missing"


def test_blocked_when_target_scene_not_found(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(
        tmp_path,
        [("shot_001", [scene("scene_001", [dialogue("srt_0001", 0, 1, "SessionOutput/visual/srt_frames/srt_0001.jpg")])])],
    )

    result = run_tool(module, workspace, target_type="scene", shot_id="shot_001", scene_id="scene_missing")

    assert result["status"] == "blocked"
    assert result["blocked_reasons"][0]["code"] == "scene_not_found"
