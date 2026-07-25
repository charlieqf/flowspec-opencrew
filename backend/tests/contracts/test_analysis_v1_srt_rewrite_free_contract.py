from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class AnalysisV1SrtRewriteFreeContractTest(unittest.TestCase):
    def test_free_tool_original_dialogue_passthrough_copies_original_srt_without_model_config(self) -> None:
        path = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "04_01_SRTRewriteFree.py"
        spec = importlib.util.spec_from_file_location("analysis_v1_srt_rewrite_free_passthrough_contract", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionOutput" / "subtitle").mkdir(parents=True)
            variables = {
                "rewrite_prompt": {
                    "final_prompt": "产品信息处要求：还原我的原视频口播文案，不需要重新改写。",
                    "source": "contract_test",
                },
                "product_info": "改写 SRT 和原 SRT 一样",
            }
            final_items = {
                "items": [
                    {
                        "srt_id": "srt_0001",
                        "dialogue": "自由改写入口也应保留这一句。",
                        "start": 0,
                        "end": 1,
                        "duration": 1,
                    },
                    {
                        "srt_id": "srt_0002",
                        "dialogue": "不要让模型重新生成第二句。",
                        "start": 1,
                        "end": 2,
                        "duration": 1,
                    },
                ]
            }
            (workspace / "SessionContext" / "Variables.json").write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")
            (workspace / "SessionOutput" / "subtitle" / "final_srt_frame_items.json").write_text(json.dumps(final_items, ensure_ascii=False), encoding="utf-8")

            result = module.run(module.Args(
                workspace=str(workspace),
                model_provider="",
                model_id="",
                force=False,
                resume=False,
                force_regenerate_prompts=False,
                print_json=True,
            ))

            output = json.loads((workspace / "SessionOutput" / "subtitle" / "rewritten_srt_items.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "completed")
            self.assertTrue(output["passthrough_original_dialogue"])
            self.assertEqual(output["identity_policy"], module.strict.ORIGINAL_DIALOGUE_PASSTHROUGH_POLICY)
            self.assertEqual([item["dialogue"] for item in output["items"]], [item["dialogue"] for item in final_items["items"]])
            self.assertFalse((workspace / "S6_04_01_SRTRewriteFree" / "Prompt" / "00_srt_rewrite_free_prompt.md").exists())
            self.assertTrue((workspace / "S6_04_01_SRTRewriteFree" / "Output" / "OutputManifest.json").exists())

    def test_router_supports_free_rewrite_selected_step_chain(self) -> None:
        router_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py").read_text(encoding="utf-8")
        schema_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "schemas.py").read_text(encoding="utf-8")

        self.assertIn('rewrite_mode: str = "strict"', schema_source)
        self.assertIn("selected_step_ids: list[str]", schema_source)
        self.assertIn('"run_selected_steps"', router_source)
        self.assertIn("def normalize_analysis_v1_rewrite_mode", router_source)
        self.assertIn("ANALYSIS_V1_SRT_REWRITE_FREE", router_source)
        self.assertIn('"requires_database": False', router_source)
        self.assertIn('spec.get("requires_database", True)', router_source)
        self.assertIn('str(plan.get("rewrite_mode") or payload.rewrite_mode)', router_source)

    def test_frontend_button_uses_free_rewrite_selected_steps(self) -> None:
        module_source = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "AnalysisV1Module.jsx").read_text(encoding="utf-8")
        dialogue_source = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "components" / "AnalysisV1DialogueView.jsx").read_text(encoding="utf-8")
        prompt_builder_source = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "components" / "AnalysisV1PromptBuilder.jsx").read_text(encoding="utf-8")

        self.assertNotIn("async function startFreeRewriteStoryboardRun()", module_source)
        self.assertIn("async function saveFreeRewriteConfigAndOpenRunPanel()", module_source)
        self.assertIn('const FREE_REWRITE_STORYBOARD_STEP_IDS = ["00", "04_01", "04_02"]', module_source)
        self.assertIn("FREE_REWRITE_STORYBOARD_PENDING_STEPS", module_source)
        self.assertIn("FREE_REWRITE_PREREQUISITE_HINT", module_source)
        self.assertIn("ensureFreeRewritePrerequisites", module_source)
        self.assertIn("02_02 字幕帧对齐", module_source)
        self.assertIn("FINAL_ITEMS_PATH", module_source)
        self.assertIn('mode: "run_selected_steps"', module_source)
        self.assertIn("selected_step_ids: FREE_REWRITE_STORYBOARD_STEP_IDS", module_source)
        self.assertIn('const [runRewriteMode, setRunRewriteMode] = createSignal("strict")', module_source)
        self.assertIn("setRunRewriteMode(\"free\")", module_source)
        self.assertIn("payload.rewrite_mode = runRewriteMode()", module_source)
        self.assertIn("rewrite_mode: runRewriteMode()", module_source)
        self.assertIn("<span>SRT 改写</span>", module_source)
        self.assertIn('<option value="strict">04_01 SRT 改写</option>', module_source)
        self.assertIn('<option value="free">04_01 SRT 自由改写</option>', module_source)
        self.assertIn('storyboard_mode: "model"', module_source)
        self.assertIn("pendingRunOverrides", module_source)
        self.assertIn("visibleRunPlanSteps", module_source)
        self.assertIn("visibleRunProgressSteps", module_source)
        self.assertIn("进入任务", module_source)
        self.assertNotIn("打开运行面板", module_source)
        self.assertIn('runDialogPurpose() === "prompt_builder_full_task"', module_source)
        self.assertIn("function openPromptBuilderFullTaskRunDialog()", module_source)
        self.assertIn("function saveFullTaskConfigAndOpenRunPanel()", module_source)
        self.assertIn("function fullTaskRunOverrides()", module_source)
        self.assertIn('mode: "run_all"', module_source)
        self.assertIn("运行全部任务", module_source)
        self.assertIn("运行自由改写链路", module_source)
        self.assertIn("运行 SRT 改写链路", module_source)
        self.assertIn("if (opened) setPromptDrawerOpen(false)", module_source)
        self.assertIn("<span>StoryBoard</span>", module_source)
        self.assertIn('const [promptDrawerMode, setPromptDrawerMode] = createSignal("prompt_builder")', module_source)
        self.assertIn("function openPromptBuilderDrawer()", module_source)
        self.assertIn("function openSrtRewriterDrawer()", module_source)
        self.assertIn('promptDrawerMode() === "srt_rewriter" ? "脚本重写" : "提示词构建器"', module_source)
        self.assertIn('title="提示词构建器"', module_source)
        self.assertIn("<span>视频分析</span>", module_source)
        self.assertIn("<span>脚本重写</span>", module_source)
        self.assertIn("<WaveformIcon /><span>音色选择</span>", module_source)
        self.assertIn("<StoryboardIcon /><span>故事板</span>", module_source)
        self.assertLess(module_source.index("<span>视频分析</span>"), module_source.index("<span>脚本重写</span>"))
        self.assertLess(module_source.index("<span>脚本重写</span>"), module_source.index("<WaveformIcon /><span>音色选择</span>"))
        self.assertLess(module_source.index("<WaveformIcon /><span>音色选择</span>"), module_source.index("<StoryboardIcon /><span>故事板</span>"))
        self.assertNotIn("analysis-v1-run-main-button", module_source)
        self.assertIn("analysis-v1-srt-rewriter-entry", module_source)
        self.assertIn("analysis-v1-tts-builder-entry", module_source)
        self.assertIn("analysis-v1-builder-run-selected", module_source)
        self.assertIn("analysis-v1-builder-run-config", module_source)
        self.assertIn("openPromptBuilderFullTaskRunDialog", module_source)
        self.assertLess(module_source.index("analysis-v1-builder-run-config"), module_source.index('title={PROMPT_TABS[activePromptTab()]?.saveLabel'))
        self.assertIn("><PlayClipIcon /></button>", module_source)
        self.assertNotIn('<PlayClipIcon /><span>运行选中步骤</span>', module_source)
        self.assertIn("openFreeRewriteFromRewriter", module_source)
        self.assertIn("运行选中步骤", module_source)
        self.assertIn('hideTargetVideo={promptDrawerMode() === "srt_rewriter"}', module_source)
        self.assertIn("<Show when={!props.hideTargetVideo}>", prompt_builder_source)
        self.assertIn("目标视频 <small>/ TARGET VIDEO</small>", prompt_builder_source)
        self.assertNotIn(">自由改写 + StoryBoard</button>", module_source)
        self.assertNotIn("onRunFreeRewrite", dialogue_source)
        self.assertNotIn("analysis-v1-free-rewrite-button", dialogue_source)

    def test_frontend_free_rewrite_keeps_original_srt_reference_column(self) -> None:
        model_source = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "analysisV1Model.js").read_text(encoding="utf-8")

        self.assertIn('rewrittenPayload?.rewrite_mode === "free"', model_source)
        self.assertIn("const originalItems = payload?.items || []", model_source)
        self.assertIn("const rewrittenItems = rewrittenPayload?.items || []", model_source)
        self.assertIn("const rowCount = Math.max(originalItems.length, rewrittenItems.length)", model_source)
        self.assertIn("const originalDialogue = String(original?.dialogue || original?.original_dialogue || \"\")", model_source)
        self.assertIn("dialogue: originalDialogue", model_source)
        self.assertIn("originalDialogue,", model_source)
        self.assertNotIn("originalDialogue: \"\"", model_source)

    def test_free_tool_declares_no_database_and_writes_standard_outputs(self) -> None:
        path = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "04_01_SRTRewriteFree.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn('"requires_database": False', source)
        self.assertIn('OUTPUT_REWRITTEN_ITEMS_REL = f"{TOOL_DIR_NAME}/Output/rewritten_srt_items.json"', source)
        self.assertIn('SESSION_REWRITTEN_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"', source)
        self.assertIn("resolve_opencode_runtime", source)
        self.assertNotIn("postgres_connect", source)

        spec = importlib.util.spec_from_file_location("analysis_v1_srt_rewrite_free_contract", path)
        self.assertIsNotNone(spec)


if __name__ == "__main__":
    unittest.main()
