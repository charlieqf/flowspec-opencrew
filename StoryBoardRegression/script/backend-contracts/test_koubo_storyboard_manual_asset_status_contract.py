from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.koubo.koubo_storyboard import (  # noqa: E402
    asset_services,
    image_plan_routes,
    value_services,
    video_only_plan_routes,
    video_plan_artifact_services,
    video_plan_load_services,
    video_plan_signature_services,
)
from opcrew_backend.koubo.koubo_storyboard.io_utils import read_json, safe_workspace_rel, write_json  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.text_utils import redact_payload, redact_secret_text  # noqa: E402


WORKING_REL = "SessionOutput/storyboard/Working"


def touch(workspace: Path, rel_path: str, content: bytes = b"test") -> None:
    path = workspace / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def make_services(workspace: Path) -> SimpleNamespace:
    ns = SimpleNamespace(
        read_json=read_json,
        write_json=write_json,
        safe_workspace_rel=safe_workspace_rel,
        redact_payload=redact_payload,
        redact_secret_text=redact_secret_text,
        task_or_404=lambda task_id: {"id": task_id, "session_id": 1},
        workspace_for=lambda task: workspace,
    )
    value_services.register_value_services(ns)
    asset_services.register_asset_services(ns)
    video_plan_signature_services.register_video_plan_signature_services(ns)
    ns.video_plan_consistency_reference_snapshot = lambda workspace: {}
    video_plan_artifact_services.register_video_plan_artifact_services(ns)
    video_plan_load_services.register_video_plan_load_services(ns)
    return ns


def write_manual_asset_workspace(workspace: Path) -> None:
    base_key = "scene_001_dialogue_003"
    manual_key = f"{base_key}_manual"
    source = f"{WORKING_REL}/{manual_key}_Image_Source.jpg"
    audio = f"{WORKING_REL}/{manual_key}_Audio_Final.wav"
    image = f"{WORKING_REL}/{manual_key}_Image_New.png"
    raw = f"{WORKING_REL}/{manual_key}_Video_Raw.mov"
    final = f"{WORKING_REL}/{manual_key}_Video_Final.mov"
    for rel_path in (source, audio, image, raw, final):
        touch(workspace, rel_path)

    source_storyboard = {
        "schema_version": "analysis_v1_storyboard_0.1",
        "shots": [{"shot_id": "shot_001", "scenes": [{"scene_id": "scene_001", "dialogue_items": []}]}],
    }
    edit_storyboard = {
        "schema_version": "koubo_storyboard_edit_0.1",
        "shots": [
            {
                "shot_id": "shot_001",
                "scenes": [
                    {
                        "scene_id": "scene_001",
                        "dialogues": [
                            {
                                "dialogue_id": base_key,
                                "dialogue_asset_key": manual_key,
                                "text": "123",
                                "image_path": source,
                                "source_image_paths": [source],
                                "bound_image_path": image,
                                "working_assets": {
                                    "audio": {"slot": "Audio_Final", "source_type": "upload", "path": audio},
                                    "images": [{"slot": "Image_New", "source_type": "generated", "path": image}],
                                    "video": {"slot": "Video_Final", "source_type": "generated", "path": final},
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }
    write_json(workspace / "SessionOutput/storyboard/srt_storyboard.json", source_storyboard)
    write_json(workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json", edit_storyboard)

    segment = {
        "segment_id": "shot_001_scene_001_segment_001",
        "asset_key": base_key,
        "dialogue_ids": [base_key],
        "planned_outputs": {
            "image_path": "",
            "segment_audio_path": f"{WORKING_REL}/{base_key}_SegmentAudio_Final.wav",
            "video_prompt_path": f"{WORKING_REL}/{base_key}_VideoPrompt.json",
            "raw_video_path": f"{WORKING_REL}/{base_key}_Video_Raw.mp4",
            "final_video_path": f"{WORKING_REL}/{base_key}_Video_Final.mp4",
            "video_path": f"{WORKING_REL}/{base_key}_Video_Final.mp4",
        },
        "first_frame": {"source_path": source, "planned_generated_image_path": ""},
        "tasks": {"need_sync": True},
    }
    write_json(
        workspace / "SessionOutput/storyboard/video_generation_plan.json",
        {"schema_version": "test", "shots": [{"shot_id": "shot_001", "scenes": [{"scene_id": "scene_001", "segments": [segment]}]}]},
    )
    write_json(
        workspace / "SessionOutput/storyboard/image_generation_plan.json",
        {
            "schema_version": "test",
            "image_tasks": [
                {
                    "image_task_id": "image_1",
                    "asset_key": base_key,
                    "dialogue_ids": [base_key],
                    "planned_outputs": {"image_prompt_path": f"{WORKING_REL}/{base_key}_ImagePrompt.json", "image_path": ""},
                    "status": "skipped",
                }
            ],
        },
    )
    write_json(
        workspace / "SessionOutput/storyboard/video_only_generation_plan.json",
        {
            "schema_version": "test",
            "video_only_tasks": [
                {
                    "video_only_task_id": "video_only_1",
                    "asset_key": base_key,
                    "dialogue_ids": [base_key],
                    "planned_outputs": {
                        "segment_audio_path": f"{WORKING_REL}/{base_key}_SegmentAudio_Final.wav",
                        "first_frame_path": image,
                        "video_prompt_path": f"{WORKING_REL}/{base_key}_VideoPrompt.json",
                        "raw_video_path": f"{WORKING_REL}/{base_key}_Video_Raw.mp4",
                        "final_video_path": f"{WORKING_REL}/{base_key}_Video_Final.mp4",
                    },
                    "first_frame": {"source_path": image, "planned_image_path": image, "source_type": "generated_image"},
                    "status": "planned_prompt_and_video",
                }
            ],
        },
    )


class KouboStoryboardManualAssetStatusContractTest(unittest.TestCase):
    def test_manual_dialogue_asset_key_overrides_plan_asset_key_for_all_plan_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            write_manual_asset_workspace(workspace)
            ns = make_services(workspace)

            video_plan = ns.video_plan_with_hash(read_json(workspace / "SessionOutput/storyboard/video_generation_plan.json"))
            video_status = ns.video_plan_artifact_status(workspace, video_plan)["segments"]["shot_001_scene_001_segment_001"]
            self.assertEqual(video_status["image"]["path"], f"{WORKING_REL}/scene_001_dialogue_003_manual_Image_New.png")
            self.assertEqual(video_status["video"]["path"], f"{WORKING_REL}/scene_001_dialogue_003_manual_Video_Raw.mov")
            self.assertEqual(video_status["final_video"]["path"], f"{WORKING_REL}/scene_001_dialogue_003_manual_Video_Final.mov")
            self.assertEqual(video_status["slot_states"]["image"]["color_zh"], "绿")
            self.assertEqual(video_status["slot_states"]["raw_video"]["color_zh"], "绿")
            self.assertEqual(video_status["slot_states"]["final_video"]["color_zh"], "绿")

            image_router = APIRouter()
            image_plan_routes.register_image_plan_routes(image_router, ns)
            image_endpoint = next(
                route.endpoint
                for route in image_router.routes
                if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/image-plan/execution"
            )
            image_payload = asyncio.run(image_endpoint(1))
            image_task = image_payload["artifact_status"]["tasks"][0]
            self.assertEqual(image_task["slot_states"]["image"]["color_zh"], "绿")
            self.assertEqual(image_task["raw_video"]["path"], f"{WORKING_REL}/scene_001_dialogue_003_manual_Video_Raw.mov")
            self.assertEqual(image_task["final_video"]["path"], f"{WORKING_REL}/scene_001_dialogue_003_manual_Video_Final.mov")

            video_only_router = APIRouter()
            video_only_plan_routes.register_video_only_plan_routes(video_only_router, ns)
            video_only_endpoint = next(
                route.endpoint
                for route in video_only_router.routes
                if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/video-only-plan/execution"
            )
            video_only_payload = asyncio.run(video_only_endpoint(1))
            video_only_task = video_only_payload["artifact_status"]["tasks"][0]
            self.assertEqual(video_only_task["slot_states"]["image"]["color_zh"], "绿")
            self.assertEqual(video_only_task["slot_states"]["raw_video"]["color_zh"], "绿")
            self.assertEqual(video_only_task["slot_states"]["copy_final"]["color_zh"], "绿")

    def test_storyboard_video_slot_state_falls_back_to_dialogue_id_for_generated_raw_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            write_manual_asset_workspace(workspace)
            edit_path = workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json"
            edit = read_json(edit_path)
            dialogue = edit["shots"][0]["scenes"][0]["dialogues"][0]
            dialogue["working_assets"]["video"] = {"slot": "Video_Final", "source_type": "", "path": ""}
            write_json(edit_path, edit)
            manual_raw = workspace / f"{WORKING_REL}/scene_001_dialogue_003_manual_Video_Raw.mov"
            if manual_raw.exists():
                manual_raw.unlink()
            touch(workspace, f"{WORKING_REL}/scene_001_dialogue_003_Video_Raw.mp4", b"generated-raw")
            ns = make_services(workspace)

            states = ns.storyboard_video_slot_states(workspace, read_json(edit_path))
            state = states["by_dialogue_id"]["scene_001_dialogue_003"]

            self.assertEqual(state["asset_key"], "scene_001_dialogue_003_manual")
            self.assertTrue(state["raw_video_exists"])
            self.assertEqual(state["raw_video_path"], f"{WORKING_REL}/scene_001_dialogue_003_Video_Raw.mp4")
            self.assertEqual(state["next_action"], "final_from_raw")


if __name__ == "__main__":
    unittest.main()
