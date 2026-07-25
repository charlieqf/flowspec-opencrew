from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.koubo.koubo_storyboard import (  # noqa: E402
    asset_services,
    image_plan_routes,
    value_services,
    video_only_plan_routes,
    video_plan_routes,
    video_plan_artifact_services,
    video_plan_execution_state_services,
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
        builder_root=lambda root: root / "SessionOutput/storyboard/builder",
        builder_rel=lambda root, path: str(Path(path).resolve().relative_to(root.resolve())),
        builder_state_path=lambda root, kind: root / f"SessionOutput/storyboard/builder/{kind}_state.json",
        legacy_builder_state_path=lambda root, kind: root / f"SessionOutput/storyboard/{kind}_state.json",
        video_plan_lock=asyncio.Lock(),
        video_plan_execution_lock=asyncio.Lock(),
        video_plan_execution_jobs={},
    )
    value_services.register_value_services(ns)
    asset_services.register_asset_services(ns)
    video_plan_signature_services.register_video_plan_signature_services(ns)
    ns.video_plan_consistency_reference_snapshot = lambda workspace, **_sc_kwargs: {}
    video_plan_artifact_services.register_video_plan_artifact_services(ns)
    video_plan_execution_state_services.register_video_plan_execution_state_services(ns)
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
    def test_video_plan_signature_invalidates_cache_when_dialogue_video_plan_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            write_manual_asset_workspace(workspace)
            ns = make_services(workspace)
            target = {"target_type": "task", "shot_id": "", "scene_id": ""}
            settings = ns.video_plan_settings({})
            edit_path = workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json"
            plan = read_json(edit_path)

            original_signature = ns.video_plan_signature(workspace, plan, target, settings, sc=ns)
            cache = {
                **original_signature,
                "target": target,
                "settings": settings,
            }
            plan_payload = {
                "target": target,
                "settings": settings,
                "consistency_references": original_signature["consistency_references"],
            }
            self.assertEqual(ns.video_plan_cache_matches(plan_payload, cache, target, settings, original_signature, sc=ns), (True, "cache_signature_matched"))

            dialogue = plan["shots"][0]["scenes"][0]["dialogues"][0]
            dialogue["video_plan"] = {
                "is_talking_head": False,
                "lipsync_override": "skip_cutaway",
                "lipsync_override_reason": "user_marked_cutaway",
            }
            changed_signature = ns.video_plan_signature(workspace, plan, target, settings, sc=ns)
            cache_match, cache_reason = ns.video_plan_cache_matches(plan_payload, cache, target, settings, changed_signature, sc=ns)

            self.assertNotEqual(original_signature["input_signature"], changed_signature["input_signature"])
            self.assertFalse(cache_match)
            self.assertIn(cache_reason, {"storyboard_structure_signature_mismatch", "media_binding_signature_mismatch", "input_signature_mismatch"})

    def test_execute_video_plan_rejects_stale_dialogue_video_plan_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            write_manual_asset_workspace(workspace)
            ns = make_services(workspace)
            events: list[tuple[int, str, dict[str, object]]] = []
            ns.add_event = lambda session_id, name, payload: events.append((session_id, name, payload))
            ns.load_plan = lambda _task, **_sc_kwargs: (read_json(workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json"), {})
            target = {"target_type": "task", "shot_id": "", "scene_id": ""}
            settings = ns.video_plan_settings({})
            original_plan = read_json(workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json")
            original_signature = ns.video_plan_signature(workspace, original_plan, target, settings, sc=ns)
            write_json(workspace / "SessionOutput/storyboard/video_generation_plan.ui_cache.json", {
                **original_signature,
                "target": target,
                "settings": settings,
            })
            write_json(workspace / "SessionOutput/storyboard/video_generation_plan.json", {
                "schema_version": "analysis_v1_video_generation_plan_0.1",
                "target": target,
                "settings": settings,
                "consistency_references": original_signature["consistency_references"],
                "shots": [],
                "plan_hash": "plan-before-cutaway",
            })

            edit_path = workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json"
            changed_plan = read_json(edit_path)
            changed_plan["shots"][0]["scenes"][0]["dialogues"][0]["video_plan"] = {
                "is_talking_head": False,
                "lipsync_override": "skip_cutaway",
                "lipsync_override_reason": "user_marked_cutaway",
            }
            write_json(edit_path, changed_plan)

            router = APIRouter()
            video_plan_routes.register_video_plan_routes(router, ns)
            execute_endpoint = next(
                route.endpoint
                for route in router.routes
                if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/video-plan/execute"
            )
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(execute_endpoint(1, {"plan_hash": "plan-before-cutaway"}))

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.detail["code"], "video_plan_stale")
            self.assertIn(raised.exception.detail["reason"], {"storyboard_structure_signature_mismatch", "media_binding_signature_mismatch", "input_signature_mismatch"})
            self.assertEqual(events[-1][1], "koubo_storyboard.video_plan.execution_blocked")
            self.assertEqual(events[-1][2]["reason"], "video_plan_stale")

    def test_execute_video_plan_returns_running_job_before_stale_cache_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            write_manual_asset_workspace(workspace)
            ns = make_services(workspace)
            target = {"target_type": "task", "shot_id": "", "scene_id": ""}
            settings = ns.video_plan_settings({})
            write_json(workspace / "SessionOutput/storyboard/video_generation_plan.json", {
                "schema_version": "analysis_v1_video_generation_plan_0.1",
                "target": target,
                "settings": settings,
                "shots": [],
                "plan_hash": "running-plan",
            })
            write_json(workspace / "SessionOutput/storyboard/video_plan_execution_state.json", {
                "schema_version": "koubo_video_plan_execution_state_0.1",
                "job_id": "job-running",
                "source_plan_hash": "running-plan",
                "status": "running",
            })
            ns.video_plan_execution_jobs["job-running"] = SimpleNamespace(done=lambda: False, cancelled=lambda: False)
            ns.load_plan = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("running execution must bypass stale cache validation"))

            router = APIRouter()
            video_plan_routes.register_video_plan_routes(router, ns)
            execute_endpoint = next(
                route.endpoint
                for route in router.routes
                if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/video-plan/execute"
            )
            result = asyncio.run(execute_endpoint(1, {"plan_hash": "running-plan"}))

            self.assertTrue(result["ok"])
            self.assertTrue(result["already_running"])
            self.assertEqual(result["job_id"], "job-running")

    def test_generate_video_plan_preserves_running_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            write_manual_asset_workspace(workspace)
            ns = make_services(workspace)
            target = {"target_type": "task", "shot_id": "", "scene_id": ""}
            settings = ns.video_plan_settings({})
            plan_path = workspace / "SessionOutput/storyboard/video_generation_plan.json"
            write_json(plan_path, {
                "schema_version": "analysis_v1_video_generation_plan_0.1",
                "target": target,
                "settings": settings,
                "summary": {"segment_count": 1},
                "shots": [],
                "plan_hash": "running-plan",
            })
            write_json(workspace / "SessionOutput/storyboard/video_plan_execution_state.json", {
                "schema_version": "koubo_video_plan_execution_state_0.1",
                "job_id": "job-running",
                "source_plan_hash": "running-plan",
                "status": "running",
            })
            ns.video_plan_execution_jobs["job-running"] = SimpleNamespace(done=lambda: False, cancelled=lambda: False)
            ns.load_plan = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("running execution must prevent plan regeneration"))
            original_plan = plan_path.read_bytes()

            router = APIRouter()
            video_plan_routes.register_video_plan_routes(router, ns)
            generate_endpoint = next(
                route.endpoint
                for route in router.routes
                if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/video-plan"
            )
            result = asyncio.run(generate_endpoint(1, {"target": target, "settings": settings}))

            self.assertTrue(result["ok"])
            self.assertTrue(result["already_running"])
            self.assertEqual(result["reason"], "video_plan_execution_running")
            self.assertEqual(plan_path.read_bytes(), original_plan)

    def test_manual_dialogue_asset_key_overrides_plan_asset_key_for_all_plan_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            write_manual_asset_workspace(workspace)
            ns = make_services(workspace)

            video_plan = ns.video_plan_with_hash(read_json(workspace / "SessionOutput/storyboard/video_generation_plan.json"), sc=ns)
            video_status = ns.video_plan_artifact_status(workspace, video_plan, sc=ns)["segments"]["shot_001_scene_001_segment_001"]
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

            states = ns.storyboard_video_slot_states(workspace, read_json(edit_path), sc=ns)
            state = states["by_dialogue_id"]["scene_001_dialogue_003"]

            self.assertEqual(state["asset_key"], "scene_001_dialogue_003_manual")
            self.assertTrue(state["raw_video_exists"])
            self.assertEqual(state["raw_video_path"], f"{WORKING_REL}/scene_001_dialogue_003_Video_Raw.mp4")
            self.assertEqual(state["next_action"], "final_from_raw")


if __name__ == "__main__":
    unittest.main()
