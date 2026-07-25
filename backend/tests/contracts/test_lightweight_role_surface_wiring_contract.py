from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

APP_PATH = REPO_ROOT / "frontend" / "src" / "App.jsx"
APP_CONTROLLER_PATH = REPO_ROOT / "frontend" / "src" / "shell" / "useOpenCrewAppController.jsx"
APP_ROUTING_CONTROLLER_PATH = REPO_ROOT / "frontend" / "src" / "shell" / "controllers" / "useShellRoutingController.jsx"
APP_VIEW_PATH = REPO_ROOT / "frontend" / "src" / "shell" / "OpenCrewShellView.jsx"
APP_UTILS_PATH = REPO_ROOT / "frontend" / "src" / "shell" / "appShellUtils.jsx"
ANALYSIS_V1_MODULE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "AnalysisV1Module.jsx"
KOUBO_MODULE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoardModule.jsx"
KOUBO_TIMING_MENU_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboTimingMenu.jsx"
KOUBO_TTS_CONTROLLER_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardTts.js"
KOUBO_HOST_PRODUCT_BUILDER_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "hostProduct" / "KouboHostProductBuilder.jsx"
MODEL_PRESET_CARDS_PATH = REPO_ROOT / "frontend" / "src" / "components" / "ModelPresetCards.jsx"

AUTH_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "routes" / "auth.py"
OPENFLOW_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "routes" / "openflow_analysis.py"
OPENCLIP_ROUTER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py"
KOUBO_PROVIDER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "provider_services.py"
KOUBO_HOST_PRODUCT_SERVICES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "host_product_services.py"
KOUBO_TTS_ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "tts_routes.py"
KOUBO_MEDIA_TTS_PROVIDER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "media_tts_provider_services.py"
KOUBO_TASK_ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "task_routes.py"


def app_surface_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in (APP_PATH, APP_CONTROLLER_PATH, APP_ROUTING_CONTROLLER_PATH, APP_VIEW_PATH, APP_UTILS_PATH))


class LightweightRoleSurfaceWiringContractTest(unittest.TestCase):
    def test_auth_surface_exposes_role_capabilities_and_admin_only_prefixes(self) -> None:
        source = AUTH_PATH.read_text(encoding="utf-8")

        for token in (
            'AUTH_ROLE_ADMIN = "admin"',
            'AUTH_ROLE_USER = "user"',
            'ADMIN_ONLY_PATH_PREFIXES = ("/api/setup/", "/api/model-config/", "/api/local-metering/")',
            '"role": role',
            '"capabilities": auth_capabilities(role)',
            "Admin role required.",
            "parsed[\"role\"] != AUTH_ROLE_ADMIN",
        ):
            self.assertIn(token, source)

    def test_backend_openflow_uses_role_aware_catalog_resolve_and_payload_masking(self) -> None:
        source = OPENFLOW_PATH.read_text(encoding="utf-8")

        for token in (
            "mask_prompt_models_for_role",
            "resolve_prompt_model_for_role",
            "mask_model_fields_for_role",
            "SURFACE_OPENFLOW_PROMPT",
            "def resolve_prompt_model(",
            "def resolve_skill_model(",
            "role: str = \"admin\"",
            "request_role(request)",
            "mask_for_role(role",
            "mask_prompt_models(role",
        ):
            self.assertIn(token, source)

    def test_backend_openclip_analysis_v1_run_forces_user_asr_and_masks_outputs(self) -> None:
        source = OPENCLIP_ROUTER_PATH.read_text(encoding="utf-8")

        for token in (
            "SURFACE_ANALYSIS_V1_PROMPT",
            "SURFACE_ANALYSIS_V1_RUN",
            "resolve_prompt_model_for_role",
            "mask_prompt_models_for_role",
            "mask_model_fields_for_role",
            "fixed_fields_update_for_role",
            "payload.model_fields_set",
            "payload = payload.model_copy(update=fixed_update)",
            "if role == \"admin\":",
            "role, SURFACE_ANALYSIS_V1_RUN",
            "mask_for_role(role, SURFACE_ANALYSIS_V1_RUN",
            "mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT",
        ):
            self.assertIn(token, source)

    def test_backend_koubo_surfaces_use_flash_alias_and_tts_hide_policy(self) -> None:
        provider_source = KOUBO_PROVIDER_PATH.read_text(encoding="utf-8")
        host_source = KOUBO_HOST_PRODUCT_SERVICES_PATH.read_text(encoding="utf-8")
        tts_source = KOUBO_TTS_ROUTES_PATH.read_text(encoding="utf-8")
        task_source = KOUBO_TASK_ROUTES_PATH.read_text(encoding="utf-8")

        for token in ("SURFACE_KOUBO_HOST_PRODUCT_PROMPT", "resolve_prompt_model_for_role", "role: str = \"admin\""):
            self.assertIn(token, provider_source)

        for token in (
            "info = message.get(\"info\")",
            "info.get(\"role\")",
            "time_info.get(\"completed\")",
        ):
            self.assertIn(token, provider_source)

        for token in (
            "SURFACE_KOUBO_HOST_PRODUCT_PROMPT",
            "mask_prompt_models_for_role",
            "mask_model_fields_for_role",
            "prompt_models_with_task_run_model(sc.safe_prompt_models(session_row",
            "def generate_host_product_final_prompt(task_id: int, payload: dict[str, Any], role: str = \"admin\", *, sc: Any)",
        ):
            self.assertIn(token, host_source)

        for token in (
            "SURFACE_KOUBO_TTS_TIMING",
            "hidden_model_defaults_for_role",
            "mask_model_fields_for_role",
            "defaults[\"provider\"]",
            "defaults[\"model\"]",
            'defaults = cloud_clone_tts_defaults(workspace, prompt_item) if not requested_provider and not requested_model else {}',
            "candidate_id = f\"{provider}_tts_{index}\" if role == \"admin\" else f\"tts_{index}\"",
        ):
            self.assertIn(token, tts_source)

        for token in (
            "SURFACE_KOUBO_HOST_PRODUCT_PROMPT",
            "SURFACE_KOUBO_TTS_TIMING",
            "mask_task_payload",
            "TTS_TIMING_PAYLOAD_KEYS",
            "mask_model_fields_under_keys_for_role(deps.ctx, role, SURFACE_KOUBO_TTS_TIMING",
        ):
            self.assertIn(token, task_source)

    def test_frontend_navigation_uses_backend_role_capabilities_and_logout(self) -> None:
        source = app_surface_source()

        for token in (
            "capabilities",
            "canManageConnection",
            "canViewMetering",
            "roleAccess",
            "statusCanManageConnection",
            "statusCanViewMetering",
            "RETIRED_NAV_HASH_PREFIXES",
            '"#/openflow"',
            "isRetiredNavHash",
            "goToBusinessHome",
            "navAllowed",
            "api.authLogout",
            'createSignal("analysis-v1")',
            "<Show when={canManageConnection()}>",
            "<Show when={canViewMetering()}>",
            "AnalysisV1Module routeHash={routeHash()} roleAccess={roleAccess()}",
            "KouboStoryBoardModule routeHash={routeHash()} roleAccess={roleAccess()}",
        ):
            self.assertIn(token, source)

        for retired_token in (
            "OC - Analysis",
            "OC - Rebuild",
            "OC - StoryBoard",
            "renderSessionWorkspace",
            'setActiveNav("session")',
        ):
            self.assertNotIn(retired_token, source)

    def test_frontend_analysis_v1_user_uses_masked_catalog_and_default_asr_payload(self) -> None:
        source = ANALYSIS_V1_MODULE_PATH.read_text(encoding="utf-8")

        for token in (
            "const isAdmin = () => Boolean(props.roleAccess?.isAdmin);",
            "promptModelSelectItems",
            "runModelSelectItems",
            "updatePromptModelId",
            "updateRunModelId",
            "ModelPresetCards",
            "findModelPresetItem",
            "selectPromptModelPreset",
            "selectRunModelPreset",
            "userPromptModelOptions()[0]",
            "userRunModelOptions()[0]",
            'mode: "run_all"',
            "asr_mode: runAsrMode()",
            "allow_cloud_asr_data_transfer: runAllowCloudAsr()",
            "include_tts_builder: runTtsBuilderMode() !== \"skip\"",
            "tts_builder_mode: runTtsBuilderMode()",
            "storyboard_mode: runStoryboardMode()",
            "return { ...payload, ...overrides, mode };",
            "isAdmin() && executesAsr",
            "<Show when={isAdmin()}>",
            "asrModeNeedsCloudConsent(runAsrMode()) && !runAllowCloudAsr()",
        ):
            self.assertIn(token, source)

        self.assertNotIn("OpenAI + GPT-5.5", source)
        self.assertNotIn("OpenCode Zen + DeepSeek", source)

    def test_frontend_koubo_user_hides_tts_provider_model_without_alias_mapping(self) -> None:
        module_source = KOUBO_MODULE_PATH.read_text(encoding="utf-8")
        menu_source = KOUBO_TIMING_MENU_PATH.read_text(encoding="utf-8")
        controller_source = KOUBO_TTS_CONTROLLER_PATH.read_text(encoding="utf-8")
        builder_source = KOUBO_HOST_PRODUCT_BUILDER_PATH.read_text(encoding="utf-8")

        self.assertIn("roleAccess: props.roleAccess", module_source)
        self.assertIn("roleAccess={props.roleAccess}", module_source)

        for token in (
            "const isAdmin = () => Boolean(props.roleAccess?.isAdmin);",
            "<Show when={isAdmin()}>",
            "if (isAdmin())",
            "next.provider",
            "next.model",
        ):
            self.assertIn(token, menu_source)

        for token in (
            "const isAdmin = () => Boolean(roleAccess?.isAdmin);",
            "if (!isAdmin())",
            "kbApi.ttsModelConfig",
            "mergeTtsCandidates(talkingHeadCandidates, builderCandidates, savedCandidates",
            "candidateMergeKey",
            "return voice ? `voice|${voice}` : candidateId ? `candidate|${candidateId}` : \"\";",
            "topCandidates: mergeTtsCandidates(selection?.top_candidates, selection?.recommendations)",
            'const fallback = voiceSource === "cloud_clone" ? {} : (defaultTTSModel() || {});',
            "const preservedCandidates = mergeTtsCandidates(selection.top_candidates, selection.recommendations);",
            "score && score <= 3 ? score : null",
            "voiceSource,",
            "voice_source: settings.voiceSource || \"\"",
            "provider: isAdmin() ? selectedProvider : \"\"",
            "model: isAdmin() ? selectedModel : \"\"",
            "...(isAdmin() ? { provider: settings.provider, model: settings.model } : {})",
        ):
            self.assertIn(token, controller_source)

        self.assertNotIn("provider_alias", menu_source)
        self.assertNotIn("model_alias", menu_source)
        self.assertNotIn("provider_alias", controller_source)
        self.assertNotIn("model_alias", controller_source)
        self.assertNotIn('metaObj.voice_provider, "heygen"', controller_source)
        self.assertNotIn('talkingHead.voice_model, "heygen-voice-clone-v3"', controller_source)

        for token in ("ModelPresetCards", "findModelPresetItem", "selectModelPreset", "model-preset-dialog-body"):
            self.assertIn(token, builder_source)

        for token in ("kbsp-hpb-model-summary", "modelDetail", "modelFilter"):
            self.assertNotIn(token, builder_source)

    def test_koubo_timing_menu_exposes_cloned_builder_candidates(self) -> None:
        menu_source = KOUBO_TIMING_MENU_PATH.read_text(encoding="utf-8")

        for token in (
            "candidateLabel",
            "voice_source === \"cloud_clone\"",
            "providerOptions()",
            "providerModels()",
            "modelVoices()",
            "if (matchedInModel) return matchedInModel;",
            "applyRecommendation(matched)",
            "score && score <= 3 ? score : null",
            "return voiceMatches(settingsDraft(), item);",
        ):
            self.assertIn(token, menu_source)
        self.assertNotIn("Math.abs(tempo - draftTempo)", menu_source)

    def test_backend_koubo_storyboard_tts_supports_cloud_clone_generation(self) -> None:
        provider_source = KOUBO_MEDIA_TTS_PROVIDER_PATH.read_text(encoding="utf-8")
        routes_source = KOUBO_TTS_ROUTES_PATH.read_text(encoding="utf-8")

        for token in (
            "dashscope_cosyvoice_tts_audio_bytes",
            "if provider == \"cosyvoice\":",
            "config_kind = \"voice-clone\"",
            "load_stored_key(sc.ctx, config_kind, requested_provider)",
            "if provider == \"heygen\":",
            "https://api.heygen.com/v3/voices/speech",
            "workspace=str(config.get(\"workspace\") or config.get(\"workspace_id\") or \"\")",
            "cosyvoice_instruction_from_prompt(prompt)",
            "should_retry_cosyvoice_without_instruction",
            "CosyVoice TTS failed after retry without instruction",
        ):
            self.assertIn(token, provider_source)
        for token in (
            "CLOUD_VOICE_CLONES_REL",
            "infer_cosyvoice_model_from_voice_id",
            "cloud_clone_tts_defaults",
            "record_provider == \"heygen\"",
            "cloud_clone_tts_defaults(workspace, prompt_item)",
        ):
            self.assertIn(token, routes_source)

    def test_frontend_model_preset_cards_batch_provider_model_selection(self) -> None:
        source = MODEL_PRESET_CARDS_PATH.read_text(encoding="utf-8")

        for token in (
            "batch",
            "function emitSelection(selection)",
            "batch(() => props.onSelect?.(selection));",
            "emitSelection({",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
