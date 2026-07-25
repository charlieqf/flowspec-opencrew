from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
