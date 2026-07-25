from __future__ import annotations

import importlib.util
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_03_ImagePlanGenerator.py"
EXECUTOR_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_04_ImagePlanExecutor.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    asset_key = "dak_0001"
    image_rel = "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
    (workspace / image_rel).parent.mkdir(parents=True, exist_ok=True)
    (workspace / image_rel).write_bytes(b"fake-jpeg")
    write_json(
        workspace / "SessionContext/Variables.json",
        {
            "workflow_id": "openclip_analysis",
            "default_image_config": {
                "provider": "openai",
                "model": "gpt-image-1.5",
                "api_key_ref": "test-image-key",
                "has_api_key": True,
            },
        },
    )
    write_json(
        workspace / "SessionOutput/storyboard/srt_storyboard.json",
        {
            "schema_version": "analysis_v1_storyboard_0.1",
            "shots": [
                {
                    "shot_id": "shot_001",
                    "summary": "口播测试",
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "summary": "第一句",
                            "start": 0.0,
                            "end": 1.2,
                            "duration": 1.2,
                            "dialogue_items": [
                                {
                                    "srt_id": "srt_0001_01",
                                    "dialogue_asset_key": asset_key,
                                    "dialogue": "给家里备这个化橘红啊",
                                    "start": 0.0,
                                    "end": 1.2,
                                    "duration": 1.2,
                                    "image_path": image_rel,
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    return workspace


class AnalysisV1ImagePlanToolsContractTest(unittest.TestCase):
    def test_image_plan_generator_creates_plan_without_prompt_dir(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_03_contract")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))

            result = generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))

            self.assertEqual(result["status"], "completed")
            self.assertFalse((workspace / "S10_05_03_ImagePlanGenerator/Prompt").exists())
            plan = json.loads((workspace / "SessionOutput/storyboard/image_generation_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["summary"]["planned_prompt_tasks"], 1)
            self.assertEqual(plan["summary"]["planned_image_tasks"], 1)
            task = plan["image_tasks"][0]
            self.assertEqual(task["status"], "planned_prompt_and_image")
            self.assertEqual(task["asset_key"], "dak_0001")
            self.assertIn("source_segment", task)

    def test_image_plan_executor_prompt_only_writes_business_prompt_without_image(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_03_contract_prompt")
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_04_contract_prompt")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))

            result = executor.run(executor.parse_args(["--workspace", str(workspace), "--mode", "prompt-only", "--overwrite-prompt", "--force"]))

            self.assertEqual(result["status"], "completed")
            prompt_path = workspace / "SessionOutput/storyboard/Working/dak_0001_ImagePrompt.json"
            image_path = workspace / "SessionOutput/storyboard/Working/dak_0001_Image_01.png"
            rendered_path = workspace / "S11_05_04_ImagePlanExecutor/Prompt/PromptRendered_dak_0001_ImagePrompt.json"
            self.assertTrue(prompt_path.exists())
            self.assertTrue(rendered_path.exists())
            self.assertFalse(image_path.exists())
            prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
            self.assertEqual(prompt["asset_key"], "dak_0001")
            self.assertEqual(prompt["prompt_status"], "draft_generated")
            self.assertIn("IMAGE_GPT_PROMPT", prompt["template_blocks"])
            state = json.loads((workspace / "SessionOutput/storyboard/image_plan_execution_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["mode"], "prompt-only")
            self.assertEqual(state["current_step"], "")
            self.assertEqual(state["tasks"]["dak_0001"]["steps"]["prompt"]["status"], "completed_working")

    def test_image_only_blocks_when_prompt_is_missing(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_03_contract_image_only")
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_04_contract_image_only")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))

            result = executor.run(executor.parse_args(["--workspace", str(workspace), "--mode", "image-only", "--force"]))

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["summary"]["failed_count"], 1)
            self.assertIn("Image prompt is missing", result["tasks"][0]["error"])
            state = json.loads((workspace / "SessionOutput/storyboard/image_plan_execution_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["mode"], "image-only")
            self.assertEqual(state["tasks"]["dak_0001"]["steps"]["image"]["status"], "failed")

    def test_image_plan_sensitive_scan_allows_key_reference_metadata(self) -> None:
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_04_contract_sensitive_scan")

        payload = {
            "model_calls": {
                "dak_0001": {
                    "image": {
                        "provider_config": {
                            "provider": "openrouter",
                            "model": "gpt-image-2",
                            "api_key_ref": "image_openrouter_key",
                            "has_api_key": True,
                            "secret_length": 73,
                            "extra": {
                                "r2_secret_access_key_ref": "public_assets_r2_secret_access_key",
                            },
                        }
                    }
                }
            }
        }

        self.assertEqual(executor.scan_for_sensitive_output(payload), [])
        self.assertTrue(executor.scan_for_sensitive_output({"detail": "postgresql://user:pass@host/db"}))
        self.assertTrue(executor.scan_for_sensitive_output({"detail": "password=abc"}))

    def test_image_plan_execute_resolves_saved_image_alias_for_executor(self) -> None:
        for path in (REPO_ROOT / "backend", REPO_ROOT / "backend"):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from fastapi import APIRouter, HTTPException
        from opcrew_backend.koubo.koubo_storyboard import image_plan_routes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                workspace / "SessionOutput/storyboard/image_generation_plan.json",
                {
                    "schema_version": "analysis_v1_image_generation_plan_0.1",
                    "plan_hash": "image-plan-hash",
                    "image_tasks": [
                        {
                            "image_task_id": "shot_001_scene_001_dak_0001_image",
                            "asset_key": "dak_0001",
                            "status": "planned_prompt_and_image",
                            "planned_outputs": {
                                "image_prompt_path": "SessionOutput/storyboard/Working/dak_0001_ImagePrompt.json",
                                "image_path": "SessionOutput/storyboard/Working/dak_0001_Image_New.png",
                            },
                        }
                    ],
                },
            )
            write_json(
                workspace / "SessionContext/ImageAPISettings.json",
                {
                    "schema_version": "upload_asset_library_image_api_settings_0.1",
                    "settings": {
                        "agentImageAlias": "Image Alias 01",
                        "provider": "",
                        "model": "",
                    },
                },
            )
            router = APIRouter()
            recorded: dict[str, object] = {}
            events: list[tuple[int, str, dict]] = []

            class AsyncUnlocked:
                def locked(self) -> bool:
                    return False

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
                add_event=lambda session_id, kind, payload: events.append((session_id, kind, payload)),
                run_image_plan_execution_background=fake_background,
                image_plan_execution_jobs={},
                video_plan_consistency_reference_snapshot=lambda workspace, **_sc_kwargs: {},
                image_plan_execution_lock=AsyncUnlocked(),
                image_plan_lock=AsyncUnlocked(),
                video_plan_execution_lock=AsyncUnlocked(),
            )
            with patch.object(image_plan_routes, "load_agent_model_aliases", return_value=[
                {"alias": "Image Alias 01", "provider": "gemini", "model": "imagen-session"}
            ]):
                image_plan_routes.register_image_plan_routes(router, deps)
                endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/image-plan/execute")

                async def run_endpoint():
                    result = await endpoint(task_id=1, payload={"mode": "image-only"})
                    await asyncio.sleep(0)
                    return result

                result = asyncio.run(run_endpoint())

            self.assertTrue(result["ok"])
            self.assertEqual(result["agentImageAlias"], "Image Alias 01")
            self.assertNotIn("gemini", json.dumps(result, ensure_ascii=False))
            model_selection = recorded["args"][8]
            self.assertEqual(model_selection["agentImageAlias"], "Image Alias 01")
            self.assertEqual(model_selection["image_provider"], "gemini")
            self.assertEqual(model_selection["image_model"], "imagen-session")
            state = json.loads((workspace / "SessionOutput/storyboard/image_plan_execution_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["agentImageAlias"], "Image Alias 01")
            self.assertEqual(events[-1][2]["agentImageAlias"], "Image Alias 01")


if __name__ == "__main__":
    unittest.main()
