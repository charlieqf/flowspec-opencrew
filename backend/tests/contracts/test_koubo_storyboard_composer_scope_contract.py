from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (REPO_ROOT / "backend",):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from fastapi import HTTPException  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.composer_services import register_composer_services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.io_utils import safe_workspace_rel, write_json  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.video_plan_signature_services import register_video_plan_signature_services  # noqa: E402


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def text(value, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


def video_plan_with_hash(payload: dict, **_sc_kwargs) -> dict:
    if not payload:
        return {}
    return {**payload, "plan_hash": payload.get("plan_hash") or "contract-plan-hash"}


def video_plan_target(payload: dict, plan: dict, **_sc_kwargs) -> dict[str, str]:
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    target_type = text(target.get("target_type") or target.get("scope") or "task")
    if target_type == "all":
        target_type = "task"
    shot_id = text(target.get("shot_id"))
    scene_id = text(target.get("scene_id"))
    if target_type == "task":
        return {"target_type": "task", "shot_id": "", "scene_id": ""}
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict) or text(shot.get("shot_id")) != shot_id:
            continue
        if target_type == "shot":
            return {"target_type": "shot", "shot_id": shot_id, "scene_id": ""}
        if any(text(scene.get("scene_id")) == scene_id for scene in shot.get("scenes") or [] if isinstance(scene, dict)):
            return {"target_type": "scene", "shot_id": shot_id, "scene_id": scene_id}
    raise HTTPException(status_code=404, detail="target not found")


def storyboard_three_scene_shot() -> dict:
    return {
        "schema_version": "koubo_storyboard_edit_0.1",
        "shots": [
            {
                "shot_id": "shot_001",
                "scenes": [
                    {"scene_id": "scene_001"},
                    {"scene_id": "scene_002"},
                    {"scene_id": "scene_003"},
                ],
            }
        ],
    }


def storyboard_with_dialogue_segments() -> dict:
    return {
        "schema_version": "koubo_storyboard_edit_0.1",
        "shots": [
            {
                "shot_id": "shot_001",
                "scenes": [
                    {
                        "scene_id": "scene_003",
                        "dialogues": [
                            {"dialogue_id": "d1", "working_assets": {"video": {"path": "SessionOutput/storyboard/Working/srt_0009_Video_Final.mp4"}}},
                            {"dialogue_id": "d2", "working_assets": {"video": {"path": "SessionOutput/storyboard/Working/srt_0011_Video_Final.mp4"}}},
                        ],
                    },
                ],
            }
        ],
    }


def scene_scoped_plan() -> dict:
    return {
        "schema_version": "analysis_v1_video_generation_plan_0.1",
        "target": {"target_type": "scene", "shot_id": "shot_001", "scene_id": "scene_003"},
        "shots": [
            {
                "shot_id": "shot_001",
                "scenes": [
                    {
                        "scene_id": "scene_003",
                        "segments": [
                            {
                                "segment_id": "shot_001_scene_003_segment_001",
                                "planned_outputs": {"video_path": "SessionOutput/storyboard/Working/srt_0009_Video_Final.mp4"},
                            }
                        ],
                    }
                ],
            }
        ],
    }


class KouboStoryboardComposerScopeContractTest(unittest.TestCase):
    def make_services(self, workspace: Path, storyboard: dict) -> SimpleNamespace:
        ns = SimpleNamespace(
            text=text,
            read_json=read_json,
            write_json=write_json,
            safe_workspace_rel=safe_workspace_rel,
            workspace_for=lambda task: workspace,
            load_plan=lambda task, **_sc_kwargs: (storyboard, {}),
            video_plan_with_hash=video_plan_with_hash,
            video_plan_target=video_plan_target,
            composer_execution_jobs={},
            redact_payload=lambda payload: payload,
        )
        register_composer_services(ns)
        return ns

    def test_composer_settings_default_does_not_remove_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            services = self.make_services(Path(tmp), storyboard_three_scene_shot())

            settings = services.composer_settings({}, sc=services)

        self.assertEqual(settings["watermark_mode"], "never")

    def test_scene_scoped_plan_does_not_become_ready_shot_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            plan = scene_scoped_plan()
            write_json(workspace / "SessionOutput/storyboard/video_generation_plan.json", plan)
            ready_video = workspace / "SessionOutput/storyboard/Working/srt_0009_Video_Final.mp4"
            ready_video.parent.mkdir(parents=True, exist_ok=True)
            ready_video.write_bytes(b"ready")

            services = self.make_services(workspace, storyboard_three_scene_shot())
            payload = services.composer_candidates_payload({"id": 1, "session_id": 1}, sc=services)
            candidates = {item["id"]: item for item in payload["candidates"]}

            self.assertIn("composer_video_plan_scope_scene", {item["code"] for item in payload["warnings"]})
            self.assertEqual(candidates["shot_001"]["ready"], False)
            self.assertEqual(candidates["shot_001"]["scene_count"], 3)
            self.assertEqual(candidates["shot_001"]["ready_scene_count"], 1)
            self.assertEqual(candidates["shot_001"]["status"]["status"], "incomplete_plan")
            self.assertEqual(candidates["shot_001"]["status"]["exists"], False)
            self.assertIn("shot_001:scene_001", candidates["shot_001"]["missing"])
            self.assertIn("shot_001:scene_002", candidates["shot_001"]["missing"])
            self.assertEqual(candidates["shot_001:scene_003"]["ready"], True)
            self.assertNotIn("shot_plan", candidates)

    def test_requested_scene_outside_current_plan_is_returned_as_unready_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            plan = scene_scoped_plan()
            write_json(workspace / "SessionOutput/storyboard/video_generation_plan.json", plan)
            ready_video = workspace / "SessionOutput/storyboard/Working/srt_0009_Video_Final.mp4"
            ready_video.parent.mkdir(parents=True, exist_ok=True)
            ready_video.write_bytes(b"ready")

            services = self.make_services(workspace, storyboard_three_scene_shot())
            payload = services.composer_candidates_payload(
                {"id": 1, "session_id": 1},
                {"target": {"target_type": "scene", "shot_id": "shot_001", "scene_id": "scene_002"}},
            sc=services)
            candidates = {item["id"]: item for item in payload["candidates"]}

            self.assertEqual(payload["requested_target"], {"target_type": "scene", "shot_id": "shot_001", "scene_id": "scene_002"})
            self.assertIn("composer_requested_target_not_ready", {item["code"] for item in payload["warnings"]})
            self.assertEqual(candidates["shot_001:scene_002"]["ready"], False)
            self.assertEqual(candidates["shot_001:scene_002"]["status"]["status"], "incomplete_plan")
            self.assertEqual(candidates["shot_001:scene_002"]["status"]["exists"], False)
            self.assertEqual(candidates["shot_001:scene_003"]["ready"], True)

    def test_requested_task_outside_current_plan_is_returned_as_unready_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            plan = scene_scoped_plan()
            write_json(workspace / "SessionOutput/storyboard/video_generation_plan.json", plan)
            ready_video = workspace / "SessionOutput/storyboard/Working/srt_0009_Video_Final.mp4"
            ready_video.parent.mkdir(parents=True, exist_ok=True)
            ready_video.write_bytes(b"ready")

            services = self.make_services(workspace, storyboard_three_scene_shot())
            payload = services.composer_candidates_payload(
                {"id": 1, "session_id": 1},
                {"target": {"target_type": "task"}},
            sc=services)
            candidates = {item["id"]: item for item in payload["candidates"]}

            self.assertEqual(payload["requested_target"], {"target_type": "task", "shot_id": "", "scene_id": ""})
            self.assertEqual(payload["plan_target"], {"target_type": "scene", "shot_id": "shot_001", "scene_id": "scene_003"})
            self.assertIn("composer_requested_target_not_ready", {item["code"] for item in payload["warnings"]})
            self.assertIn("shot_plan", candidates)
            self.assertEqual(candidates["shot_plan"]["ready"], False)
            self.assertEqual(candidates["shot_plan"]["status"]["status"], "incomplete_plan")
            self.assertIn("shot_001:scene_001", candidates["shot_plan"]["missing"])
            self.assertIn("shot_001:scene_002", candidates["shot_plan"]["missing"])

    def test_scene_candidates_report_storyboard_segment_counts_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            plan = scene_scoped_plan()
            write_json(workspace / "SessionOutput/storyboard/video_generation_plan.json", plan)
            for rel in (
                "SessionOutput/storyboard/Working/srt_0009_Video_Final.mp4",
                "SessionOutput/storyboard/Working/srt_0011_Video_Final.mp4",
            ):
                path = workspace / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"ready")

            services = self.make_services(workspace, storyboard_with_dialogue_segments())
            payload = services.composer_candidates_payload({"id": 1, "session_id": 1}, sc=services)
            scene = next(item for item in payload["candidates"] if item["id"] == "shot_001:scene_003")

            self.assertEqual(scene["segment_count"], 1)
            self.assertEqual(scene["ready_segment_count"], 1)
            self.assertEqual(scene["storyboard_segment_count"], 2)
            self.assertEqual(scene["ready_storyboard_segment_count"], 2)

    def test_scene_scoped_plan_rejects_shot_and_task_composer_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            plan = video_plan_with_hash(scene_scoped_plan())
            services = self.make_services(workspace, storyboard_three_scene_shot())

            with self.assertRaises(HTTPException) as shot_error:
                services.composer_target({"target": {"target_type": "shot", "shot_id": "shot_001"}}, plan, storyboard_three_scene_shot(), sc=services)
            self.assertEqual(shot_error.exception.status_code, 409)

            with self.assertRaises(HTTPException) as task_error:
                services.composer_target({"target": {"target_type": "task"}}, plan, storyboard_three_scene_shot(), sc=services)
            self.assertEqual(task_error.exception.status_code, 409)

            scene_target = services.composer_target(
                {"target": {"target_type": "scene", "shot_id": "shot_001", "scene_id": "scene_003"}},
                plan,
                storyboard_three_scene_shot(),
            sc=services)
            self.assertEqual(scene_target, {"target_type": "scene", "shot_id": "shot_001", "scene_id": "scene_003"})

            with self.assertRaises(HTTPException) as scene_error:
                services.composer_target(
                    {"target": {"target_type": "scene", "shot_id": "shot_001", "scene_id": "scene_002"}},
                    plan,
                    storyboard_three_scene_shot(),
                sc=services)
            self.assertEqual(scene_error.exception.status_code, 409)

    def test_composer_execute_route_starts_background_job_instead_of_waiting_for_tool(self) -> None:
        source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "composer_routes.py").read_text(encoding="utf-8")

        self.assertIn("asyncio.create_task(deps.run_composer_background", source)
        self.assertIn('/api/koubo-storyboard/tasks/{task_id}/composer/execution', source)
        self.assertNotIn("await asyncio.to_thread(run_composer_tool", source)

    def test_stale_running_composer_state_is_marked_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            write_json(workspace / "SessionOutput/storyboard/video_generation_plan.json", scene_scoped_plan())
            write_json(workspace / "SessionOutput/storyboard/video_plan_compose_state.json", {"job_id": "stale_job", "status": "running"})
            services = self.make_services(workspace, storyboard_three_scene_shot())
            services.composer_execution_jobs = {}

            state = services.read_composer_execution_state(workspace, sc=services)

            self.assertEqual(state["status"], "failed")
            self.assertIn("no longer active", state["error"])

    def test_running_composer_state_without_job_id_is_marked_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            write_json(workspace / "SessionOutput/storyboard/video_generation_plan.json", scene_scoped_plan())
            write_json(workspace / "SessionOutput/storyboard/video_plan_compose_state.json", {"status": "queued"})
            services = self.make_services(workspace, storyboard_three_scene_shot())

            state = services.read_composer_execution_state(workspace, sc=services)

            self.assertEqual(state["status"], "failed")
            self.assertIn("no longer active", state["error"])

    def test_timeline_video_plan_keeps_current_scope_and_composer_requests_task_scope(self) -> None:
        timeline = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboTimeline.jsx").read_text(encoding="utf-8")
        api = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardApi.js").read_text(encoding="utf-8")
        video_plan = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardVideoPlan.js").read_text(encoding="utf-8")
        composer = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardComposer.js").read_text(encoding="utf-8")
        module = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoardModule.jsx").read_text(encoding="utf-8")
        modal = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboComposerModal.jsx").read_text(encoding="utf-8")
        video_plan_modal = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboVideoPlanModal.jsx").read_text(encoding="utf-8")
        styles = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "styles" / "video-plan-modal.css").read_text(encoding="utf-8")

        self.assertIn('const TASK_TARGET = { target_type: "task", shot_id: "", scene_id: "" };', timeline)
        self.assertIn("...(props.currentVideoPlanTarget?.() || TASK_TARGET)", timeline)
        self.assertIn('action_source: "timeline_scope_button"', timeline)
        self.assertIn("timelineVideoPlanTarget", timeline)
        self.assertIn('params.set("action_source", payload.action_source)', api)
        self.assertIn("timelineVideoPlanTarget", timeline)
        self.assertIn("props.videoPlanDisabledReason?.(timelineVideoPlanTarget())", timeline)
        self.assertIn('props.openComposer?.(TIMELINE_GLOBAL_TARGET);', timeline)
        self.assertIn('props.openVideoPlan?.(timelineVideoPlanTarget());', timeline)
        self.assertNotIn('props.setScope("all");\n            props.setSelectedDialogueId("");\n            props.openVideoPlan?.(timelineVideoPlanTarget());', timeline)
        self.assertIn("currentVideoPlanTarget={currentVideoPlanTarget}", module)
        self.assertIn('if (targetType === "task" || targetType === "all") return { ...TASK_TARGET };', video_plan)
        self.assertIn("action_source: actionSource", video_plan)
        self.assertIn('if (targetType === "task" || targetType === "all") return { ...TASK_TARGET };', composer)
        self.assertIn("action_source: actionSource", composer)
        self.assertIn("openFullVideoPlanFromComposer", module)
        self.assertIn('const actionSource = String(targetOverride.action_source || "composer_scope_mismatch_cta").trim() || "composer_scope_mismatch_cta";', module)
        self.assertIn("action_source: actionSource", module)
        self.assertIn("regenerateVideoPlan={openFullVideoPlanFromComposer}", module)
        self.assertIn("const requestedCandidate = createMemo(", modal)
        self.assertIn("if (requested) return requested;", modal)
        self.assertIn("const needsTaskVideoPlan = createMemo(", modal)
        self.assertIn("props.regenerateVideoPlan?.(", modal)
        self.assertIn("生成整片计划", modal)
        self.assertIn('"composer_scope_mismatch_cta"', modal)
        self.assertIn('props.result?.()?.plan_target', modal)
        self.assertIn('class="kbsp-trace-strip"', modal)
        self.assertIn('class="kbsp-trace-strip"', video_plan_modal)
        self.assertIn(".kbsp-trace-strip", styles)
        self.assertIn(".kbsp-cpm-plan-action", styles)

    def test_video_plan_and_composer_scope_events_include_diagnostic_markers(self) -> None:
        video_plan_routes = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "video_plan_routes.py").read_text(encoding="utf-8")
        composer_routes = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "composer_routes.py").read_text(encoding="utf-8")

        for token in (
            '"previous_target"',
            '"previous_plan_hash"',
            '"new_target"',
            '"new_plan_hash"',
            '"shot_count"',
            '"scene_count"',
            '"segment_count"',
            '"action_source"',
        ):
            self.assertIn(token, video_plan_routes)
        self.assertIn("koubo_storyboard.composer.candidates_checked", composer_routes)
        self.assertIn("koubo_storyboard.composer.scope_mismatch_warning", composer_routes)
        for token in (
            '"requested_target"',
            '"current_plan_target"',
            '"candidate_count"',
            '"ready_count"',
            '"warnings"',
            '"action_source"',
        ):
            self.assertIn(token, composer_routes)

    def test_video_plan_settings_are_persisted_from_timeline_apply(self) -> None:
        constants = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "constants.py").read_text(encoding="utf-8")
        task_routes = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "task_routes.py").read_text(encoding="utf-8")
        load_services = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "video_plan_load_services.py").read_text(encoding="utf-8")
        api = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardApi.js").read_text(encoding="utf-8")
        video_plan = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardVideoPlan.js").read_text(encoding="utf-8")
        timeline = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboTimeline.jsx").read_text(encoding="utf-8")
        module = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoardModule.jsx").read_text(encoding="utf-8")

        self.assertIn('VIDEO_PLAN_SETTINGS_REL = "SessionOutput/storyboard/video_plan_settings.json"', constants)
        self.assertIn('/api/koubo-storyboard/tasks/{task_id}/video-plan/settings', task_routes)
        self.assertIn("write_json(workspace / VIDEO_PLAN_SETTINGS_REL, saved)", task_routes)
        self.assertIn('"video_plan_settings": saved_video_plan_settings', load_services)
        self.assertIn("saveVideoPlanSettings", api)
        self.assertIn('runAction("video-plan-settings"', video_plan)
        self.assertIn("await props.applyVideoPlanSettings?.(videoPlanDraft());", timeline)
        self.assertIn("setVideoPlanSettings(result.meta?.video_plan_settings || DEFAULT_VIDEO_PLAN_SETTINGS);", module)

    def test_timeline_parameter_popovers_render_in_portal_above_storyboard_layers(self) -> None:
        timeline = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboTimeline.jsx").read_text(encoding="utf-8")
        styles = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "styles" / "timeline.css").read_text(encoding="utf-8")

        self.assertIn('import { Portal } from "solid-js/web";', timeline)
        self.assertGreaterEqual(timeline.count("<Portal>"), 3)
        self.assertIn("z-index: 10020;", styles)

    def test_video_plan_seconds_controls_match_grok_limits_and_tolerance_zero(self) -> None:
        timeline = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboTimeline.jsx").read_text(encoding="utf-8")
        video_plan = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardVideoPlan.js").read_text(encoding="utf-8")
        signature = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "video_plan_signature_services.py").read_text(encoding="utf-8")
        planner = (REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_01_VideoPlanGenerator.py").read_text(encoding="utf-8")
        grok_video = (REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_grok.py").read_text(encoding="utf-8")

        self.assertIn("VIDEO_PLAN_MAX_OPTIONS = [4, 8, 10, 15]", video_plan)
        self.assertIn("VIDEO_PLAN_MAX_OPTIONS", timeline)
        self.assertIn("seconds === 10 ? \"0.00\"", timeline)
        self.assertIn("maxVideo === 10 ? 0", video_plan)
        self.assertIn('min="0"', timeline)
        self.assertIn("nonNegativeNumber", video_plan)
        self.assertIn("(4.0, 8.0, 10.0, 15.0)", signature)
        self.assertIn("0.0 if max_video == 10.0 else tolerance", signature)
        self.assertIn("video_plan_nonnegative_number", signature)
        self.assertIn("VIDEO_MODEL_MAX_SECONDS = 15.0", planner)
        self.assertIn("split_tolerance_seconds", planner)
        self.assertIn("return min(15, seconds)", grok_video)

    def test_video_plan_settings_force_zero_tolerance_for_r2v_10s(self) -> None:
        services = SimpleNamespace(text=text)
        register_video_plan_signature_services(services)

        settings = services.video_plan_settings({
            "settings": {
                "max_video_seconds": 10,
                "min_video_seconds": 2,
                "split_tolerance_seconds": 2,
            }
        })

        self.assertEqual(settings["max_video_seconds"], 10.0)
        self.assertEqual(settings["min_video_seconds"], 2.0)
        self.assertEqual(settings["split_tolerance_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
