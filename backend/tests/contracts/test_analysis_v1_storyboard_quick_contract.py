from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "04_03_StoryBoardQuick.py"
PREPARE_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "00_PrepareSessionVariables.py"
MODEL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "analysisV1Model.js"
PROMPT_BUILDER_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "components" / "AnalysisV1PromptBuilder.jsx"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("analysis_v1_storyboard_quick_contract", TOOL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_prepare_module():
    spec = importlib.util.spec_from_file_location("analysis_v1_prepare_contract", PREPARE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class AnalysisV1StoryBoardQuickContractTest(unittest.TestCase):
    def test_storyboard_quick_tool_writes_storyboard_without_prompt_or_model(self) -> None:
        module = load_tool_module()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            write_json(workspace / "SessionContext" / "Variables.json", {
                "schema_version": "analysis_v1_session_context_0.1",
                "workflow_id": "openclip_analysis",
                "storyboard_quick_config": {
                    "target_scene_seconds": 8,
                    "target_shot_seconds": 16,
                    "split_tolerance_seconds": 2,
                    "language_boundary_mode": "balanced",
                },
            })
            items = []
            for index in range(1, 7):
                start = float((index - 1) * 4)
                items.append({
                    "srt_id": f"srt_{index:03d}",
                    "dialogue": f"这是第 {index} 句完整口播。",
                    "start": start,
                    "end": start + 4,
                    "duration": 4,
                    "image_path": f"SessionOutput/visual/srt_frames/frame_{index:03d}.jpg",
                })
            write_json(workspace / "SessionOutput" / "subtitle" / "rewritten_srt_items.json", {"items": items})

            args = module.Args(
                workspace=str(workspace),
                target_scene_seconds=None,
                target_shot_seconds=None,
                split_tolerance_seconds=None,
                language_boundary_mode="",
                force=True,
                resume=False,
                print_json=False,
            )
            result = module.run(args)

            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["requires_model_calls"])
            self.assertFalse((workspace / "S7_04_03_StoryBoardQuick" / "Prompt").exists())
            storyboard = json.loads((workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json").read_text(encoding="utf-8"))
            self.assertEqual(storyboard["tool"], "04_03_StoryBoardQuick")
            self.assertEqual(storyboard["storyboard_mode"], "quick")
            covered = [srt_id for shot in storyboard["shots"] for scene in shot["scenes"] for srt_id in scene["srt_ids"]]
            self.assertEqual(covered, [item["srt_id"] for item in items])
            self.assertTrue((workspace / "S7_04_03_StoryBoardQuick" / "Output" / "grouping_audit.json").exists())

    def test_prepare_and_frontend_contract_include_storyboard_quick_config(self) -> None:
        prepare_source = PREPARE_PATH.read_text(encoding="utf-8")
        model_source = MODEL_PATH.read_text(encoding="utf-8")
        prompt_builder_source = PROMPT_BUILDER_PATH.read_text(encoding="utf-8")

        self.assertIn("storyboard_quick_config_json", prepare_source)
        self.assertIn('"storyboard_quick_config"', prepare_source)
        self.assertIn("storyboard_quick_config_defaulted", prepare_source)
        self.assertNotIn("ALTER TABLE", prepare_source)
        self.assertIn("DEFAULT_STORYBOARD_QUICK_CONFIG", model_source)
        self.assertIn("normalizeStoryboardQuickConfig", model_source)
        self.assertIn("StoryBoard 结构参数", model_source)
        self.assertIn("故事版快速参数", prompt_builder_source)
        self.assertIn("updateStoryboardQuickConfig", prompt_builder_source)
        self.assertIn("buildStoryboardSimplePrompt(next)", prompt_builder_source)

    def test_prepare_normalizes_heygen_lipsync_ui_model_to_api_mode(self) -> None:
        prepare = load_prepare_module()

        model, extra = prepare.normalize_media_public_config(
            "lipsync",
            "heygen",
            "heygen-lipsync-speed",
            {"enable_watermark": False},
        )

        self.assertEqual(model, "speed")
        self.assertEqual(extra["mode"], "speed")
        self.assertEqual(extra["selected_model"], "heygen-lipsync-speed")
        self.assertFalse(extra["enable_watermark"])

    def test_prompt_builder_preserves_explicit_empty_constraints(self) -> None:
        model_source = MODEL_PATH.read_text(encoding="utf-8")

        self.assertIn("function fieldTextOrDefault(source, field, fallback)", model_source)
        self.assertIn('fieldTextOrDefault(draft, "constraints", DEFAULT_REWRITE_CONSTRAINTS)', model_source)
        self.assertIn('constraints: fieldTextOrDefault(task, "constraints", DEFAULT_REWRITE_CONSTRAINTS)', model_source)
        self.assertIn('constraints: fieldTextOrDefault(draft, "constraints", DEFAULT_REWRITE_CONSTRAINTS)', model_source)
        self.assertNotIn("draft?.constraints || DEFAULT_REWRITE_CONSTRAINTS", model_source)
        self.assertNotIn("task?.constraints || DEFAULT_REWRITE_CONSTRAINTS", model_source)


if __name__ == "__main__":
    unittest.main()
