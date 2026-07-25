from __future__ import annotations

import json
import sys
import tempfile
import unittest
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (REPO_ROOT / "backend",):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from opcrew_backend.koubo.koubo_storyboard import tool_runner_services  # noqa: E402


class FakeResult:
    def __init__(self, row: Any = None) -> None:
        self.row = row

    def first(self) -> Any:
        return self.row


class FakeConnection:
    def __init__(self, engine: "FakeEngine") -> None:
        self.engine = engine

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeResult:
        query = str(statement)
        payload = params or {}
        self.engine.calls.append((query, payload))
        if "SELECT provider, model" not in query:
            return FakeResult()
        selection = self.engine.selections.get(str(payload.get("kind") or ""))
        if not selection:
            return FakeResult()
        return FakeResult(SimpleNamespace(_mapping=selection))


class FakeEngine:
    def __init__(self, selections: dict[str, dict[str, str]]) -> None:
        self.selections = selections
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def begin(self) -> FakeConnection:
        return FakeConnection(self)


class KouboStoryboardVideoExecutionProviderContractTest(unittest.TestCase):
    def cli_arg_value(self, cmd: list[str], name: str) -> str:
        index = len(cmd) - 1 - cmd[::-1].index(name)
        return cmd[index + 1]

    def test_active_media_provider_cli_args_use_current_connection_selection(self) -> None:
        ctx = SimpleNamespace(
            engine=FakeEngine(
                {
                    "image": {"provider": "xai", "model": "grok-imagine-image"},
                    "video": {"provider": "xai", "model": "grok-imagine-video"},
                    "lipsync": {"provider": "syncso", "model": "lipsync-2"},
                    "tts": {"provider": "xai", "model": "xai-tts"},
                }
            )
        )

        args = tool_runner_services.active_media_provider_cli_args(ctx)

        self.assertIn("--video-provider", args)
        self.assertEqual(args[args.index("--video-provider") + 1], "xai")
        self.assertIn("--video-model", args)
        self.assertEqual(args[args.index("--video-model") + 1], "grok-imagine-video")
        self.assertIn("--image-provider", args)
        self.assertIn("--lipsync-provider", args)
        self.assertIn("--tts-provider", args)
        self.assertNotIn("openrouter", args)

    def test_video_prompt_reload_routes_talking_head_to_local_0502_template(self) -> None:
        reload_source = inspect.getsource(tool_runner_services.reload_video_only_plan_prompt)
        talking_head_reload_source = inspect.getsource(tool_runner_services.reload_talking_head_video_only_plan_prompt)

        self.assertIn("storyboard_workflow_id", reload_source)
        self.assertIn("reload_talking_head_video_only_plan_prompt", reload_source)
        self.assertNotIn('variables.get("talking_head")', talking_head_reload_source)
        self.assertIn('variables.get("default_video_config")', talking_head_reload_source)
        self.assertIn("TALKING_HEAD_REFERENCE_PROMPT_TEMPLATE", talking_head_reload_source)

        module = tool_runner_services.load_analysis_tool_module(
            tool_runner_services.TALKING_HEAD_VIDEO_PLAN_EXECUTION_SCRIPT_PATH,
            "talking_head_video_prompt_template_contract",
        )
        video_module = module.video_module_for("openrouter", "bytedance/seedance-2.0")
        template_name, template_path = video_module.template_spec({
            "prompt_template": module.TALKING_HEAD_REFERENCE_PROMPT_TEMPLATE,
        })
        self.assertEqual(template_name, "Ref_05_02_Video_SDR2V_TalkingHead.md")
        self.assertEqual(template_path.name, "Video_SDR2V_TalkingHead.md")

    def test_storyboard_video_plan_executor_is_always_analysis_v1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            variables_path = workspace / "SessionContext" / "Variables.json"
            variables_path.parent.mkdir(parents=True)
            variables_path.write_text(
                json.dumps({"workflow_id": "person_talking_head_v1"}),
                encoding="utf-8",
            )

            self.assertEqual(
                tool_runner_services.video_plan_execution_script_path(workspace),
                tool_runner_services.VIDEO_PLAN_EXECUTION_SCRIPT_PATH,
            )
            self.assertNotEqual(
                tool_runner_services.video_plan_execution_script_path(workspace),
                tool_runner_services.TALKING_HEAD_VIDEO_PLAN_EXECUTION_SCRIPT_PATH,
            )

    def test_talking_head_video_execution_auto_refreshes_missing_session_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            variables_path = workspace / "SessionContext" / "Variables.json"
            variables_path.parent.mkdir(parents=True)
            variables_path.write_text(
                json.dumps({
                    "workflow_id": "person_talking_head_v1",
                    "talking_head": {
                        "video_model": {"provider": "provider-a", "model": "video-a"},
                    },
                }),
                encoding="utf-8",
            )
            fake_script = Path(tmp) / "00_PrepareSessionVariables.py"
            fake_script.write_text("# test\n", encoding="utf-8")
            events: list[tuple[int, str, dict[str, Any]]] = []

            def fake_run(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
                variables_path.write_text(
                    json.dumps({
                        "workflow_id": "person_talking_head_v1",
                        "talking_head": {
                            "video_model": {"provider": "provider-a", "model": "video-a"},
                        },
                        "default_video_config": {"provider": "provider-a", "model": "video-a"},
                    }),
                    encoding="utf-8",
                )
                return SimpleNamespace(stdout=json.dumps({"status": "completed"}), stderr="", returncode=0)

            with (
                patch.object(tool_runner_services, "TALKING_HEAD_SESSION_VARIABLES_PREPARE_SCRIPT_PATH", fake_script),
                patch.object(tool_runner_services.subprocess, "run", side_effect=fake_run) as run_process,
            ):
                result = tool_runner_services.ensure_talking_head_session_variables(
                    {"id": 276, "session_id": 335, "workflow_mode": "person_talking_head_v1"},
                    workspace,
                    sc=SimpleNamespace(add_event=lambda session_id, kind, payload: events.append((session_id, kind, payload))),
                )

            self.assertEqual(result, {"status": "refreshed", "refreshed": True})
            run_process.assert_called_once()
            self.assertEqual(events[0][1], "talking_head_v1.session_variables.auto_refresh_started")
            self.assertEqual(events[-1][1], "talking_head_v1.session_variables.auto_refresh_completed")

    def test_structured_video_execution_error_becomes_readable_text(self) -> None:
        state = {
            "current_segment_id": "segment_001",
            "current_step": "video",
            "segments": {},
        }

        updated = tool_runner_services.mark_current_video_plan_step_failed(
            state,
            [{"code": "variables_missing", "message": "请刷新人物口播运行配置。"}],
        )

        self.assertEqual(updated["segments"]["segment_001"]["error"], "请刷新人物口播运行配置。")
        self.assertNotIn("[object Object]", json.dumps(updated, ensure_ascii=False))

    def test_run_video_plan_execution_tool_does_not_pass_database_active_media_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            executor = Path(tmp) / "05_02_VideoPlanExecutor.py"
            executor.write_text("# test executor\n", encoding="utf-8")
            captured: dict[str, Any] = {}

            original_path = tool_runner_services.VIDEO_PLAN_EXECUTION_SCRIPT_PATH
            original_run = tool_runner_services.subprocess.run

            def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs
                return SimpleNamespace(stdout=json.dumps({"status": "completed"}), stderr="", returncode=0)

            try:
                tool_runner_services.VIDEO_PLAN_EXECUTION_SCRIPT_PATH = executor
                tool_runner_services.subprocess.run = fake_run
                ns = SimpleNamespace(
                    ctx=SimpleNamespace(
                        engine=FakeEngine(
                            {
                                "image": {"provider": "xai", "model": "grok-imagine-image"},
                                "video": {"provider": "xai", "model": "grok-imagine-video"},
                            }
                        )
                    ),
                    analysis_tool_env=lambda: {},
                )
                tool_runner_services.register_tool_runner_services(ns)

                result, _stdout, _stderr, returncode = ns.run_video_plan_execution_tool(workspace, "job-1", "hash-1", sc=ns)

                cmd = captured["cmd"]
                self.assertEqual(returncode, 0)
                self.assertEqual(result["status"], "completed")
                self.assertNotIn("--video-provider", cmd)
                self.assertNotIn("--video-model", cmd)
                self.assertNotIn("--image-provider", cmd)
                self.assertNotIn("--image-model", cmd)
                self.assertEqual(ns.ctx.engine.calls, [])
            finally:
                tool_runner_services.VIDEO_PLAN_EXECUTION_SCRIPT_PATH = original_path
                tool_runner_services.subprocess.run = original_run

    def test_run_video_plan_execution_tool_does_not_pass_ui_media_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            executor = Path(tmp) / "05_02_VideoPlanExecutor.py"
            executor.write_text("# test executor\n", encoding="utf-8")
            captured: dict[str, Any] = {}

            original_path = tool_runner_services.VIDEO_PLAN_EXECUTION_SCRIPT_PATH
            original_run = tool_runner_services.subprocess.run

            def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs
                return SimpleNamespace(stdout=json.dumps({"status": "completed"}), stderr="", returncode=0)

            try:
                tool_runner_services.VIDEO_PLAN_EXECUTION_SCRIPT_PATH = executor
                tool_runner_services.subprocess.run = fake_run
                ns = SimpleNamespace(
                    ctx=SimpleNamespace(
                        engine=FakeEngine(
                            {
                                "image": {"provider": "xai", "model": "grok-imagine-image"},
                                "video": {"provider": "xai", "model": "grok-imagine-video"},
                            }
                        )
                    ),
                    analysis_tool_env=lambda: {},
                )
                tool_runner_services.register_tool_runner_services(ns)

                result, _stdout, _stderr, returncode = ns.run_video_plan_execution_tool(
                    workspace,
                    "job-1",
                    "hash-1",
                    {
                        "image_provider": "gemini",
                        "image_model": "imagen-session",
                        "video_provider": "openrouter",
                        "video_model": "bytedance/seedance-2.0",
                    },
                    sc=ns,
                )

                cmd = captured["cmd"]
                self.assertEqual(returncode, 0)
                self.assertEqual(result["status"], "completed")
                self.assertNotIn("--image-provider", cmd)
                self.assertNotIn("--image-model", cmd)
                self.assertNotIn("--video-provider", cmd)
                self.assertNotIn("--video-model", cmd)
            finally:
                tool_runner_services.VIDEO_PLAN_EXECUTION_SCRIPT_PATH = original_path
                tool_runner_services.subprocess.run = original_run

    def test_run_image_plan_execution_tool_uses_session_image_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            executor = Path(tmp) / "05_04_ImagePlanExecutor.py"
            executor.write_text("# test executor\n", encoding="utf-8")
            captured: dict[str, Any] = {}

            original_path = tool_runner_services.IMAGE_PLAN_EXECUTION_SCRIPT_PATH
            original_run = tool_runner_services.subprocess.run

            def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs
                return SimpleNamespace(stdout=json.dumps({"status": "completed"}), stderr="", returncode=0)

            try:
                tool_runner_services.IMAGE_PLAN_EXECUTION_SCRIPT_PATH = executor
                tool_runner_services.subprocess.run = fake_run
                ns = SimpleNamespace(
                    ctx=SimpleNamespace(
                        engine=FakeEngine(
                            {
                                "image": {"provider": "xai", "model": "grok-imagine-image"},
                            }
                        )
                    ),
                    analysis_tool_env=lambda: {},
                )
                tool_runner_services.register_tool_runner_services(ns)

                result, _stdout, _stderr, returncode = ns.run_image_plan_execution_tool(
                    workspace,
                    "image-only",
                    "hash-1",
                    "task-1",
                    "dak_0001",
                    {
                        "image_provider": "gemini",
                        "image_model": "imagen-session",
                    },
                    sc=ns,
                )

                cmd = captured["cmd"]
                self.assertEqual(returncode, 0)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(self.cli_arg_value(cmd, "--image-provider"), "gemini")
                self.assertEqual(self.cli_arg_value(cmd, "--image-model"), "imagen-session")
                self.assertIn("--overwrite-image", cmd)
                self.assertNotIn("--video-provider", cmd)
            finally:
                tool_runner_services.IMAGE_PLAN_EXECUTION_SCRIPT_PATH = original_path
                tool_runner_services.subprocess.run = original_run

    def test_video_plan_background_preserves_public_aliases_after_executor_state_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir(parents=True)
            state_path = workspace / "SessionOutput/storyboard/video_plan_execution_state.json"
            result_path = workspace / "SessionOutput/storyboard/video_plan_execution_result.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"status": "completed", "source_plan_hash": "hash-1"}, ensure_ascii=False), encoding="utf-8")
            result_path.write_text(json.dumps({"status": "completed", "source_plan_hash": "hash-1"}, ensure_ascii=False), encoding="utf-8")

            def write_state(root: Path, payload: dict[str, Any], **_kwargs: Any) -> None:
                (root / "SessionOutput/storyboard/video_plan_execution_state.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            ns = SimpleNamespace(write_video_plan_execution_state=write_state)

            tool_runner_services.preserve_video_plan_public_aliases(
                workspace,
                {
                    "agentImageAlias": "Max 3.1",
                    "image_provider": "gemini",
                    "image_model": "gemini-3.1-flash-image",
                    "agentVideoAlias": "Max SD 2.0",
                    "video_provider": "openrouter",
                    "video_model": "bytedance/seedance-2.0",
                },
                sc=ns,
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(state["agentImageAlias"], "Max 3.1")
            self.assertEqual(state["agentVideoAlias"], "Max SD 2.0")
            self.assertEqual(result["agentImageAlias"], "Max 3.1")
            self.assertEqual(result["agentVideoAlias"], "Max SD 2.0")
            serialized = json.dumps({"state": state, "result": result}, ensure_ascii=False)
            self.assertNotIn("gemini", serialized)
            self.assertNotIn("openrouter", serialized)
            self.assertNotIn("seedance", serialized)

    def test_image_plan_background_preserves_public_alias_after_executor_state_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir(parents=True)
            state_path = workspace / "SessionOutput/storyboard/image_plan_execution_state.json"
            result_path = workspace / "SessionOutput/storyboard/image_plan_execution_result.json"

            def fake_run(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], str, str, int]:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
                result_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
                return {"status": "completed", "summary": {}}, "", "", 0

            ns = SimpleNamespace(
                text=lambda value, default="": str(value if value is not None else default).strip(),
                add_event=lambda *_args, **_kwargs: None,
                image_plan_execution_jobs={"job-1": object()},
            )
            selection = {
                "agentImageAlias": "Max 3.1",
                "image_provider": "gemini",
                "image_model": "gemini-3.1-flash-image",
            }

            with patch.object(tool_runner_services, "run_image_plan_execution_tool", fake_run):
                asyncio.run(tool_runner_services.run_image_plan_execution_background(
                    1,
                    1,
                    workspace,
                    "job-1",
                    "hash-1",
                    "image-only",
                    "",
                    "",
                    selection,
                    sc=ns,
                ))

            state = json.loads(state_path.read_text(encoding="utf-8"))
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(state["agentImageAlias"], "Max 3.1")
            self.assertEqual(result["agentImageAlias"], "Max 3.1")
            serialized = json.dumps({"state": state, "result": result}, ensure_ascii=False)
            self.assertNotIn("gemini", serialized)
            self.assertNotIn("image_provider", serialized)
            self.assertNotIn("image_model", serialized)
            self.assertNotIn("job-1", ns.image_plan_execution_jobs)

    def test_video_only_background_preserves_public_alias_after_executor_state_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir(parents=True)
            state_path = workspace / "SessionOutput/storyboard/video_only_plan_execution_state.json"
            result_path = workspace / "SessionOutput/storyboard/video_only_plan_execution_result.json"

            def fake_run(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], str, str, int]:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
                result_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
                return {"status": "completed", "summary": {}}, "", "", 0

            ns = SimpleNamespace(
                text=lambda value, default="": str(value if value is not None else default).strip(),
                add_event=lambda *_args, **_kwargs: None,
                video_only_plan_execution_jobs={"job-1": object()},
            )
            selection = {
                "agentVideoAlias": "Max SD 2.0",
                "video_provider": "openrouter",
                "video_model": "bytedance/seedance-2.0",
            }

            with patch.object(tool_runner_services, "run_video_only_plan_execution_tool", fake_run):
                asyncio.run(tool_runner_services.run_video_only_plan_execution_background(
                    1,
                    1,
                    workspace,
                    "job-1",
                    "hash-1",
                    "video-only",
                    "",
                    "",
                    selection,
                    sc=ns,
                ))

            state = json.loads(state_path.read_text(encoding="utf-8"))
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(state["agentVideoAlias"], "Max SD 2.0")
            self.assertEqual(result["agentVideoAlias"], "Max SD 2.0")
            serialized = json.dumps({"state": state, "result": result}, ensure_ascii=False)
            self.assertNotIn("openrouter", serialized)
            self.assertNotIn("seedance", serialized)
            self.assertNotIn("video_provider", serialized)
            self.assertNotIn("video_model", serialized)
            self.assertNotIn("job-1", ns.video_only_plan_execution_jobs)

    def test_video_plan_execute_resolves_saved_image_and_video_aliases_for_executor(self) -> None:
        from fastapi import APIRouter, HTTPException
        from opcrew_backend.koubo.koubo_storyboard import video_plan_routes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()

            def write_json(path: Path, payload: dict[str, Any]) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            def write_state(root: Path, payload: dict[str, Any], **_kwargs: Any) -> None:
                write_json(root / "SessionOutput/storyboard/video_plan_execution_state.json", payload)

            write_json(
                workspace / "SessionOutput/storyboard/video_generation_plan.json",
                {
                    "schema_version": "analysis_v1_video_generation_plan_0.1",
                    "plan_hash": "video-plan-hash",
                    "target": {"target_type": "task", "shot_id": "", "scene_id": ""},
                    "settings": {"max_video_seconds": 4.0, "min_video_seconds": 2.0, "split_tolerance_seconds": 2.0},
                    "shots": [],
                },
            )
            write_json(
                workspace / "SessionContext/ImageAPISettings.json",
                {"settings": {"agentImageAlias": "Image Alias 01", "provider": "", "model": ""}},
            )
            write_json(
                workspace / "SessionContext/VideoAPISettings.json",
                {"settings": {"agentVideoAlias": "Video Alias 01", "provider": "", "model": ""}},
            )

            router = APIRouter()
            recorded: dict[str, object] = {}
            events: list[tuple[int, str, dict]] = []

            class AsyncUnlocked:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return False

            def safe_workspace_rel(root: Path, rel_path: str):
                rel_path = str(rel_path).strip()
                target = (root / rel_path).resolve()
                if not str(target).startswith(str(root.resolve())):
                    raise HTTPException(status_code=400, detail="Unsafe path")
                return rel_path, target

            async def fake_background(*args, **kwargs):
                recorded["args"] = args
                recorded["kwargs"] = kwargs

            deps = SimpleNamespace(
                ctx=SimpleNamespace(),
                text=lambda value, default="": str(value if value is not None else default).strip(),
                read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {},
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                video_plan_with_hash=lambda payload, **_sc_kwargs: payload,
                redact_payload=lambda payload: payload,
                task_or_404=lambda task_id: {"id": task_id, "session_id": 1, "workspace_dir": str(workspace)},
                workspace_for=lambda task: workspace,
                load_plan=lambda task, **_sc_kwargs: ({"shots": []}, {}),
                video_plan_settings=lambda payload: payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
                video_plan_signature=lambda *_args, **_kwargs: {"signature": "ok"},
                video_plan_cache_matches=lambda *_args, **_kwargs: (True, "cache_match"),
                video_plan_tts_audio_preflight=lambda *_args, **_kwargs: {"requires_generation": False, "generation_count": 0},
                video_plan_execution_is_running=lambda state, **_sc_kwargs: False,
                video_plan_artifact_status=lambda *_args, **_kwargs: {"segments": {}},
                write_video_plan_execution_state=write_state,
                video_plan_execution_payload=lambda *_args, **_kwargs: {},
                run_video_plan_execution_background=fake_background,
                add_event=lambda session_id, kind, payload: events.append((session_id, kind, payload)),
                video_plan_execution_jobs={},
                video_plan_lock=AsyncUnlocked(),
                video_plan_execution_lock=AsyncUnlocked(),
            )

            with (
                patch.object(video_plan_routes, "is_dance_mimic_storyboard", return_value=False),
                patch.object(video_plan_routes, "load_agent_model_aliases", return_value=[
                    {"alias": "Image Alias 01", "provider": "gemini", "model": "imagen-session"},
                    {"alias": "Video Alias 01", "provider": "openrouter", "model": "bytedance/seedance-2.0"},
                ]),
            ):
                video_plan_routes.register_video_plan_routes(router, deps)
                endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/video-plan/execute")

                async def run_endpoint():
                    result = await endpoint(task_id=1, payload={"plan_hash": "video-plan-hash"})
                    await asyncio.sleep(0)
                    return result

                result = asyncio.run(run_endpoint())

            self.assertTrue(result["ok"])
            self.assertEqual(result["agentImageAlias"], "Image Alias 01")
            self.assertEqual(result["agentVideoAlias"], "Video Alias 01")
            self.assertNotIn("openrouter", json.dumps(result, ensure_ascii=False))
            self.assertNotIn("gemini", json.dumps(result, ensure_ascii=False))
            model_selection = recorded["args"][5]
            self.assertEqual(model_selection["agentImageAlias"], "Image Alias 01")
            self.assertEqual(model_selection["image_provider"], "gemini")
            self.assertEqual(model_selection["image_model"], "imagen-session")
            self.assertEqual(model_selection["agentVideoAlias"], "Video Alias 01")
            self.assertEqual(model_selection["video_provider"], "openrouter")
            self.assertEqual(model_selection["video_model"], "bytedance/seedance-2.0")
            state = json.loads((workspace / "SessionOutput/storyboard/video_plan_execution_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["agentImageAlias"], "Image Alias 01")
            self.assertEqual(state["agentVideoAlias"], "Video Alias 01")
            self.assertEqual(events[-1][2]["agentImageAlias"], "Image Alias 01")
            self.assertEqual(events[-1][2]["agentVideoAlias"], "Video Alias 01")


if __name__ == "__main__":
    unittest.main()
