from __future__ import annotations

import sys
import unittest
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
OPENCLIP_BACKEND = REPO_ROOT / "backend"
if str(OPENCLIP_BACKEND) not in sys.path:
    sys.path.insert(0, str(OPENCLIP_BACKEND))

ROUTER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py"
SCHEMAS_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "schemas.py"
ANALYSIS_MODULE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "AnalysisV1Module.jsx"
TTS_BUILDER_COMPONENT_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "components" / "AnalysisV1TTSBuilder.jsx"


def load_schemas_module():
    spec = importlib.util.spec_from_file_location("openclip_backend_schemas_contract", SCHEMAS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1RunToStoryBoardTTSModeContractTest(unittest.TestCase):
    def test_run_payload_defaults_to_quick_tts_builder(self) -> None:
        schemas = load_schemas_module()
        payload = schemas.OpenClipAnalysisV1RunPayload(task_id=32)

        self.assertTrue(payload.include_tts_builder)
        self.assertEqual(payload.tts_builder_mode, "quick")
        self.assertEqual(payload.tts_voice_catalog_dir, "")
        self.assertEqual(payload.storyboard_mode, "quick")
        quick_adv = schemas.OpenClipTTSQuickAdvPayload(task_id=32)
        self.assertTrue(quick_adv.enable_speechbrain)
        self.assertFalse(quick_adv.clone_consent_confirmed)
        self.assertEqual(quick_adv.clone_target_model, "cosyvoice-v3.5-flash")
        selection = schemas.OpenClipTTSSelectionPayload(task_id=32, candidate_id="cand_1", voice_id="voice_1")
        self.assertEqual(selection.candidate_id, "cand_1")
        self.assertEqual(selection.voice_id, "voice_1")

    def test_backend_run_to_storyboard_has_03_02_and_03_03_quick_contract(self) -> None:
        source = ROUTER_PATH.read_text(encoding="utf-8")

        self.assertIn("ANALYSIS_V1_TTS_BUILDER_QUICK", source)
        self.assertIn("ANALYSIS_V1_TTS_BUILDER_QUICK_ADV", source)
        self.assertIn('"03_02_TTSBuilderQuick"', source)
        self.assertIn('"03_03_TTSBuilderQuickAdv"', source)
        self.assertIn('"04_03_StoryBoardQuick"', source)
        self.assertIn("normalize_analysis_v1_storyboard_mode", source)
        self.assertIn("storyboard_mode", source)
        self.assertIn('"--voice-catalog-dir"', source)
        self.assertIn("ANALYSIS_V1_DEFAULT_VOICE_CATALOG", source)
        self.assertIn('"builder_g"', source)
        self.assertIn('"quick_adv"', source)
        self.assertIn('"--reference-start"', source)
        self.assertIn('"--reference-duration"', source)

    def test_frontend_sends_role_aware_tts_builder_mode(self) -> None:
        source = ANALYSIS_MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn('createSignal("quick")', source)
        self.assertIn('createSignal(true)', source)
        self.assertNotIn("DEFAULT_RUN_PROVIDER_ID", source)
        self.assertNotIn("DEFAULT_RUN_MODEL_ID", source)
        self.assertIn('findModelPresetItem(models, "max")', source)
        self.assertIn("findPreferredRunModel", source)
        self.assertIn("if (isAdmin()) {", source)
        self.assertIn('mode: "run_all"', source)
        self.assertIn('include_tts_builder: runTtsBuilderMode() !== "skip"', source)
        self.assertIn("tts_builder_mode: runTtsBuilderMode()", source)
        self.assertIn("storyboard_mode: runStoryboardMode()", source)
        self.assertIn('createSignal("quick")', source)
        self.assertIn("04_03 快速分组", source)
        self.assertIn("03_02 快速声音匹配", source)
        self.assertIn("03_03 高级声音匹配", source)
        self.assertIn("03_01 全量声音匹配", source)
        self.assertIn('quick_adv: { run_only_step_id: "03_03", tts_builder_mode: "quick_adv"', source)
        self.assertIn("defaultRunOverrides", source)

    def test_tts_builder_dialog_starts_quick_builder_attempt(self) -> None:
        module_source = ANALYSIS_MODULE_PATH.read_text(encoding="utf-8")
        component_source = TTS_BUILDER_COMPONENT_PATH.read_text(encoding="utf-8")

        self.assertIn("startQuickTTSBuilderRun", module_source)
        self.assertIn('run_only_step_id: "03_02"', module_source)
        self.assertIn('run_only_step_id: "03_03"', module_source)
        self.assertIn('tts_builder_mode: "quick"', module_source)
        self.assertIn('tts_builder_mode: "quick_adv"', module_source)
        self.assertIn('source: "tts_builder_dialog"', module_source)
        self.assertIn("stage1_count: Number.isFinite", module_source)
        self.assertIn("enable_speechbrain: Boolean", module_source)
        self.assertIn("onRunQuickBuilder", module_source)
        self.assertIn("onRunQuickBuilder", component_source)
        self.assertIn("03_02_TTSBuilderQuick", component_source)
        self.assertIn("03_03_TTSBuilderQuickAdv", component_source)
        self.assertIn("高级匹配", component_source)
        self.assertIn("quickAdvState", component_source)
        self.assertIn("quickAdvRank", component_source)
        self.assertIn("quickAdvCloneVoice", component_source)
        self.assertIn("quickAdvCloneImport", component_source)
        self.assertIn("importQuickAdvCloneVoice", component_source)
        self.assertIn("选用到当前任务", component_source)
        self.assertIn("cloneCandidateItem", component_source)
        self.assertIn('voice_source: "cloud_clone"', component_source)
        self.assertIn("savedPromptMeta", component_source)
        self.assertIn("scenario_id: scenarioId()", component_source)
        self.assertIn("base_prompt: basePrompt", component_source)
        self.assertIn("打开试听声音", component_source)
        self.assertIn("匹配配置", component_source)
        self.assertIn("analysis-v1-tts-match-config-dialog", component_source)
        self.assertNotIn("window.confirm", component_source)
        self.assertIn("analysis-v1-tts-clone-consent-dialog", component_source)
        self.assertIn("clone_consent_confirmed: true", component_source)
        self.assertNotIn("LOCKED_CLONE_TARGET_MODEL", component_source)
        self.assertNotIn("clone_target_model: LOCKED_CLONE_TARGET_MODEL", component_source)
        self.assertIn("ttsSelectionPayload", component_source)
        self.assertIn("persistSelectedCandidate", component_source)
        self.assertIn("props.api.saveTTSSelection", component_source)
        self.assertIn("已保存选用到 SessionContext/Variables.json", component_source)
        self.assertNotIn("setCloneTargetModel", component_source)
        self.assertIn("授权克隆", component_source)
        self.assertIn("克隆配置", component_source)
        self.assertNotIn("云端声音克隆", component_source)
        self.assertNotIn("生成可复用的 voice_id", component_source)
        self.assertNotIn("默认开启高精度匹配", component_source)
        self.assertIn("setAdvSpeechbrain(true)", component_source)
        self.assertNotIn("已获授权", component_source)
        self.assertNotIn("cloneConsentConfirmed", component_source)
        self.assertNotIn("cloneConsentCheckbox", component_source)

    def test_quick_adv_page_api_routes_are_wired(self) -> None:
        router_source = ROUTER_PATH.read_text(encoding="utf-8")
        component_source = TTS_BUILDER_COMPONENT_PATH.read_text(encoding="utf-8")
        api_source = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "analysisV1Api.js").read_text(encoding="utf-8")

        self.assertIn("/analysis-v1/tts/quick-adv/state", router_source)
        self.assertIn("/analysis-v1/tts/quick-adv/catalog-list", router_source)
        self.assertIn("/analysis-v1/tts/quick-adv/sample-reference", router_source)
        self.assertIn("/analysis-v1/tts/quick-adv/rank", router_source)
        self.assertIn("/analysis-v1/tts/quick-adv/clone-voice", router_source)
        self.assertIn("/analysis-v1/tts/quick-adv/clone-import", router_source)
        self.assertIn("/analysis-v1/tts/selection", router_source)
        self.assertIn("save_analysis_v1_tts_selection_to_variables", router_source)
        self.assertIn('"SessionContext" / "Variables.json"', router_source)
        self.assertIn('variables["tts_builder_selection"]', router_source)
        self.assertIn('variables["selected_tts_candidate"]', router_source)
        self.assertIn('variables["selected_tts_candidate_id"]', router_source)
        self.assertIn('"SessionOutput" / "tts" / "tts_builder_candidates.json"', router_source)
        self.assertIn('candidate_result["selected_candidate_id"]', router_source)
        self.assertIn('"selected": True', router_source)
        self.assertIn('"is_selected": True', router_source)
        self.assertIn("candidateKey(item, index) === saved", component_source)
        self.assertIn("analysis_v1.tts.selection.saved", router_source)
        self.assertIn("saveTTSSelection", api_source)
        self.assertIn("quickAdvCloneImport", api_source)
        self.assertIn("/analysis-v1/voice-catalog/{model}/audio/{audio_path:path}", router_source)
        self.assertIn('target.relative_to(catalog_root)', router_source)
        self.assertIn("run_analysis_v1_quick_adv_command", router_source)
        self.assertIn("--clone-consent-confirmed", router_source)
        self.assertIn("--stage1-count", router_source)
        self.assertIn("--disable-speechbrain", router_source)
        self.assertIn('env["ANALYSIS_V1_ENABLE_SPEECHBRAIN"] = "1"', router_source)
        self.assertIn('options.get("enable_speechbrain", True)', router_source)
        self.assertIn("analysis_v1_voice_clone_config", router_source)
        self.assertIn('load_config(ctx, "voice-clone")', router_source)
        self.assertIn('load_stored_key(ctx, "voice-clone", provider)', router_source)
        self.assertNotIn("LOCKED_ANALYSIS_V1_CLONE_TARGET_MODEL", router_source)
        self.assertNotIn('str(payload.clone_target_model or "cosyvoice-v3.5-flash")', router_source)
        self.assertIn("OPENCREW_ANALYSIS_V1_HEYGEN_CLONE_API_KEY", router_source)
        self.assertIn("OPENCREW_ANALYSIS_V1_COSYVOICE_CLONE_API_KEY", router_source)
        self.assertIn("OPENCREW_ANALYSIS_V1_MINIMAX_CLONE_API_KEY", router_source)
        self.assertIn('analysis_v1_tts_config(provider, "")', router_source)
        self.assertIn('provider not in {"google", "qwen", "cosyvoice", "bytedance", "heygen", "minimax"}', router_source)
        self.assertIn("heygen_tts_preview_url", router_source)
        self.assertIn("minimax_tts_preview_url", router_source)
        self.assertIn('analysis_v1_voice_clone_tts_config(provider, model)', router_source)
        self.assertIn("bytedance_tts_preview_url", router_source)
        self.assertIn('dashscope_tts_preview_url, config["api_key"], provider', router_source)
        self.assertIn('"QWEN_API_KEY"', router_source)
        self.assertIn("quickAdvSampleReference", api_source)
        self.assertIn("quickAdvCatalogList", api_source)
        self.assertIn("voiceCatalogAudioUrl", api_source)

    def test_quick_adv_catalog_audio_uses_voice_catalog_route_not_workspace_raw(self) -> None:
        component_source = TTS_BUILDER_COMPONENT_PATH.read_text(encoding="utf-8")
        api_source = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "analysisV1Api.js").read_text(encoding="utf-8")

        self.assertIn("catalog_index_item?.sample_audio_path", component_source)
        self.assertIn("props.api.voiceCatalogAudioUrl(props.taskId, model, catalogRel)", component_source)
        self.assertIn("catalog_audio_path", component_source)
        self.assertIn("api/openclip/tasks/${taskId}/analysis-v1/voice-catalog/", api_source)
        self.assertIn("encodePathSegments(filePath)", api_source)

    def test_run_progress_running_duration_does_not_treat_null_as_zero(self) -> None:
        module_source = ANALYSIS_MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn("function finiteDurationSeconds", module_source)
        self.assertIn("value === null || value === undefined || value === \"\"", module_source)
        self.assertIn("const finished = finiteDurationSeconds(step?.duration_seconds)", module_source)
        self.assertNotIn("const finished = Number(step?.duration_seconds)", module_source)

    def test_tts_builder_single_step_progress_hides_generic_execute_button(self) -> None:
        module_source = ANALYSIS_MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn("isTtsBuilderDialogRunProgress", module_source)
        self.assertIn('runProgress()?.plan?.options?.source || "") === "tts_builder_dialog"', module_source)
        self.assertIn('<Show when={!isTtsBuilderDialogRunProgress()}>', module_source)

    def test_tts_builder_stale_progress_explains_existing_candidates(self) -> None:
        module_source = ANALYSIS_MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn("ttsBuilderDialogRunHint", module_source)
        self.assertIn('String(runProgress()?.status || "").toLowerCase() !== "stale_running"', module_source)
        self.assertIn("运行心跳已失联，但已读取到", module_source)
        self.assertIn("候选试听", module_source)
        self.assertIn("runProgressMessage() && !ttsBuilderDialogRunHint()", module_source)

    def test_reference_audio_upload_uses_analysis_v1_ffmpeg_resolver(self) -> None:
        source = ROUTER_PATH.read_text(encoding="utf-8")

        self.assertIn('OPENCREW_REPO_ROOT / "ToolLibrary" / ".bin" / "ffmpeg"', source)
        self.assertIn('BACKEND_ROOT / ".venv" / "bin" / "static_ffmpeg"', source)
        self.assertIn('os.environ.get("OPENCREW_FFMPEG_PATH", "")', source)
        self.assertIn("ffmpeg = analysis_v1_ffmpeg_binary()", source)
        self.assertNotIn('Path(__file__).resolve().parents[3] / ".bin" / "ffmpeg"', source)


if __name__ == "__main__":
    unittest.main()
