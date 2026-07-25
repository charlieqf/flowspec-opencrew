from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
MODEL_CONFIG_BACKEND = REPO_ROOT / "ModelConfig" / "backend"
ROUTER_PATH = BACKEND_ROOT / "opcrew_backend" / "koubo" / "router.py"
ASSET_ROUTES_PATH = BACKEND_ROOT / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_routes.py"
TTS_ROUTES_PATH = BACKEND_ROOT / "opcrew_backend" / "koubo" / "koubo_storyboard" / "tts_routes.py"
TTS_AGENT_MODEL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "ttsAgent" / "ttsAgentModel.js"
ANALYSIS_V1_MODULE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "AnalysisV1Module.jsx"
for item in (BACKEND_ROOT, MODEL_CONFIG_BACKEND, REPO_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from opcrew_backend.koubo.koubo_storyboard.tts_public_aliases import (
    attach_tts_public_aliases,
    customer_tts_public_config,
    resolve_tts_public_alias_from_config,
    tts_public_alias_state,
)
from opcrew_backend.koubo.koubo_storyboard.tts_routes import safe_voice_target_defaults
from opcrew_backend.koubo.router import resolve_analysis_v1_clone_delete_payload
from opcrew_backend.koubo.schemas import OpenClipTTSQuickAdvPayload
from opcrew_backend.services.tts_voice_aliases import (
    TTS_VOICE_ALIAS_STATE_KEY,
    normalize_storyboard_tts_selection,
    resolve_tts_voice_alias,
)


class TTSPublicAliasContractTest(unittest.TestCase):
    def test_historical_minimaxi_clone_is_kept_for_active_minimax_provider(self) -> None:
        class FakeContext:
            def get_setting(self, key: str, default: object = None) -> object:
                return default

        candidate = {
            "candidate_id": "clone_minimaxi_legacy",
            "provider": "minimaxi",
            "source_clone_provider": "minimaxi",
            "model": "minimax-voice-clone-v1",
            "voice_id": "legacy-minimax-voice",
            "voice_source": "cloud_clone",
        }

        normalized = normalize_storyboard_tts_selection(
            FakeContext(),
            {
                "storyboard_tts_selection": {
                    **candidate,
                    "top_candidates": [candidate],
                    "recommendations": [candidate],
                }
            },
            active_clone_provider="minimax",
        )

        selection = normalized["storyboard_tts_selection"]
        self.assertEqual([item["candidate_id"] for item in selection["top_candidates"]], ["clone_minimaxi_legacy"])
        self.assertEqual(selection["provider"], "minimax")
        self.assertEqual(selection["source_clone_provider"], "minimax")

    def test_clone_delete_uses_resolved_cosyvoice_provider_and_model(self) -> None:
        payload, provider, model = resolve_analysis_v1_clone_delete_payload(
            OpenClipTTSQuickAdvPayload(
                clone_provider="heygen",
                clone_target_model="heygen-voice-clone-v3",
                clone_voice_id="tts_voice_public",
            ),
            {
                "provider": "cosyvoice",
                "model": "cosyvoice-v3.5-plus",
                "voice_id": "cosyvoice-v3.5-plus-private-voice",
            },
        )

        self.assertEqual(provider, "cosyvoice")
        self.assertEqual(model, "cosyvoice-v3.5-plus")
        self.assertEqual(payload.clone_provider, "cosyvoice")
        self.assertEqual(payload.clone_target_model, "cosyvoice-v3.5-plus")
        self.assertEqual(payload.clone_voice_id, "cosyvoice-v3.5-plus-private-voice")

    def test_existing_redacted_voice_alias_target_resolves_to_runtime_provider(self) -> None:
        class FakeContext:
            def get_setting(self, key: str, default: object = None) -> object:
                if key != TTS_VOICE_ALIAS_STATE_KEY:
                    return default
                return {
                    "secret": "test-secret",
                    "targets": {
                        "tts_voice_existing": {
                            "voice_id": "cosyvoice-v3.5-plus-voice-123",
                            "provider": "aliyun_[model]",
                            "model": "cosyvoice-v3.5-plus",
                            "candidate_id": "clone-123",
                        },
                    },
                }

        self.assertEqual(resolve_tts_voice_alias(FakeContext(), "tts_voice_existing"), {
            "voice_id": "cosyvoice-v3.5-plus-voice-123",
            "provider": "cosyvoice",
            "model": "cosyvoice-v3.5-plus",
            "candidate_id": "clone-123",
        })

    def test_redacted_clone_provider_is_recovered_from_runtime_model(self) -> None:
        defaults = safe_voice_target_defaults(
            {
                "provider": "aliyun_[model]",
                "model": "cosyvoice-v3.5-plus",
                "voice_id": "cosyvoice-v3.5-plus-voice-123",
            },
            {},
        )

        self.assertEqual(defaults, {
            "provider": "cosyvoice",
            "model": "cosyvoice-v3.5-plus",
            "voice_id": "cosyvoice-v3.5-plus-voice-123",
        })

    def test_unresolvable_redacted_clone_provider_is_not_used_at_runtime(self) -> None:
        defaults = safe_voice_target_defaults(
            {"provider": "aliyun_[model]", "model": "unknown-tts", "voice_id": "voice-123"},
            {},
        )

        self.assertEqual(defaults, {})

    def test_public_aliases_attach_and_resolve_to_real_tts_model(self) -> None:
        config = {
            "kind": "tts",
            "active_provider": "google",
            "providers": [
                {
                    "provider": "google",
                    "model": "gemini-3.1-flash-tts-preview",
                    "selected_voice_by_model": {"gemini-3.1-flash-tts-preview": "Kore"},
                    "models": [
                        {"model": "gemini-3.1-flash-tts-preview", "voices": [{"voice_id": "Kore"}]},
                        {"model": "gemini-2.5-flash-preview-tts", "voices": [{"voice_id": "Puck"}]},
                    ],
                },
                {
                    "provider": "qwen",
                    "model": "qwen3-tts-flash",
                    "models": [{"model": "qwen3-tts-flash", "voices": [{"voice_id": "Cherry"}]}],
                },
            ],
        }

        public_config = attach_tts_public_aliases(config)
        public_provider_1 = public_config["providers"][0]["public_provider"]
        public_model_1 = public_config["providers"][0]["models"][0]["public_model"]
        public_model_2 = public_config["providers"][0]["models"][1]["public_model"]
        public_provider_2 = public_config["providers"][1]["public_provider"]
        public_model_3 = public_config["providers"][1]["models"][0]["public_model"]

        self.assertEqual(public_config["active_public_provider"], public_provider_1)
        self.assertTrue(public_provider_1.startswith("tts_provider_"))
        self.assertNotEqual(public_provider_1, "tts_provider_01")
        self.assertTrue(public_model_1.startswith(f"{public_provider_1}_model_"))
        self.assertEqual(public_config["providers"][0]["selected_voice_by_public_model"], {public_model_1: "Kore"})
        self.assertEqual(
            resolve_tts_public_alias_from_config(public_config, public_provider_1, public_model_2),
            ("google", "gemini-2.5-flash-preview-tts"),
        )
        self.assertEqual(
            resolve_tts_public_alias_from_config(public_config, public_provider_2, public_model_3),
            ("qwen", "qwen3-tts-flash"),
        )
        self.assertEqual(
            resolve_tts_public_alias_from_config(public_config, "tts_provider_09", "tts_provider_09_model_01"),
            ("", ""),
        )
        self.assertEqual(
            resolve_tts_public_alias_from_config(public_config, public_provider_1, "removed-model"),
            ("", ""),
        )

    def test_stable_aliases_and_legacy_snapshot_survive_provider_reordering(self) -> None:
        config = {
            "kind": "tts",
            "active_provider": "google",
            "providers": [
                {"provider": "google", "model": "gemini-tts", "models": [{"model": "gemini-tts"}]},
                {"provider": "qwen", "model": "qwen-tts", "models": [{"model": "qwen-tts"}]},
            ],
        }

        class FakeContext:
            def __init__(self) -> None:
                self.settings: dict[str, object] = {}

            def get_setting(self, key: str, default: object = None) -> object:
                return self.settings.get(key, default)

            def set_setting(self, key: str, value: object) -> None:
                self.settings[key] = value

        ctx = FakeContext()
        state = tts_public_alias_state(ctx, config)
        before = attach_tts_public_aliases(config, alias_secret=str(state["secret"]))
        reordered = {**config, "providers": list(reversed(config["providers"]))}
        after = attach_tts_public_aliases(reordered, alias_secret=str(state["secret"]))
        before_aliases = {item["provider"]: item["public_provider"] for item in before["providers"]}
        after_aliases = {item["provider"]: item["public_provider"] for item in after["providers"]}

        self.assertEqual(before_aliases, after_aliases)
        self.assertEqual(
            resolve_tts_public_alias_from_config(
                after,
                "tts_provider_01",
                "tts_provider_01_model_01",
                legacy_targets=state["legacy_targets"],
            ),
            ("google", "gemini-tts"),
        )
        removed = {**config, "providers": [config["providers"][1]]}
        removed_public_config = attach_tts_public_aliases(removed, alias_secret=str(state["secret"]))
        self.assertEqual(
            resolve_tts_public_alias_from_config(
                removed_public_config,
                "tts_provider_01",
                "tts_provider_01_model_01",
                legacy_targets=state["legacy_targets"],
            ),
            ("", ""),
        )

    def test_customer_tts_public_config_does_not_emit_real_provider_or_model_ids(self) -> None:
        config = {
            "kind": "tts",
            "active_provider": "google",
            "providers": [
                {
                    "provider": "google",
                    "provider_label": "Google / Gemini",
                    "model": "gemini-3.1-flash-tts-preview",
                    "selected_voice_by_model": {"gemini-3.1-flash-tts-preview": "Kore"},
                    "models": [
                        {
                            "model": "gemini-3.1-flash-tts-preview",
                            "label": "Gemini 3.1 Flash TTS",
                            "voices": [{"voice_id": "Kore", "label": "Kore"}],
                        },
                    ],
                },
            ],
        }

        public_config = customer_tts_public_config(config)
        serialized = str(public_config).lower()
        public_provider = public_config["providers"][0]["public_provider"]
        public_model = public_config["providers"][0]["models"][0]["public_model"]

        self.assertEqual(public_config["active_provider"], public_provider)
        self.assertEqual(public_config["active_public_provider"], public_provider)
        self.assertEqual(public_config["providers"][0]["provider"], public_provider)
        self.assertEqual(public_config["providers"][0]["models"][0]["model"], public_model)
        self.assertEqual(public_config["providers"][0]["selected_voice_by_model"], {public_model: "Kore"})
        for forbidden in ("google", "gemini", "qwen", "openai", "heygen"):
            self.assertNotIn(forbidden, serialized)

    def test_analysis_v1_task_preview_requires_public_tts_selection(self) -> None:
        source = ROUTER_PATH.read_text(encoding="utf-8")
        preview_section = source.split('async def preview_analysis_v1_tts', 1)[1].split('def list_analysis_v1_previews', 1)[0]

        self.assertIn('resolve_tts_public_alias(ctx, payload.provider or "", payload.model or "")', preview_section)
        self.assertNotIn('payload.provider or "google"', preview_section)
        self.assertNotIn('payload.model or "gemini-3.1-flash-tts-preview"', preview_section)

    def test_tts_agent_generation_resolves_public_aliases(self) -> None:
        asset_routes_source = ASSET_ROUTES_PATH.read_text(encoding="utf-8")
        tts_routes_source = TTS_ROUTES_PATH.read_text(encoding="utf-8")
        model_source = TTS_AGENT_MODEL_PATH.read_text(encoding="utf-8")

        self.assertIn('alias_state = tts_public_alias_state(deps.ctx, config)', asset_routes_source)
        self.assertIn('customer_tts_public_config(config, alias_secret=deps.text(alias_state.get("secret")))', asset_routes_source)
        self.assertIn("PUBLIC_TTS_PROVIDER_PREFIX", tts_routes_source)
        self.assertIn("def tts_public_alias_requested(provider: str, model: str) -> bool:", tts_routes_source)
        self.assertIn("resolved_provider, resolved_model = resolve_tts_public_alias(deps.ctx, requested_provider, requested_model)", tts_routes_source)
        self.assertIn('"provider": resolved_provider', tts_routes_source)
        self.assertIn('"model": resolved_model', tts_routes_source)
        self.assertIn("const settings = selectedTtsSettings(await ensureTtsConfig());", model_source)
        self.assertIn("const activeProvider = text(config?.active_public_provider || config?.active_provider).toLowerCase();", model_source)
        self.assertIn("text(item.public_provider || item.provider_alias || item.provider).toLowerCase() === activeProvider", model_source)
        self.assertIn("provider: settings.provider", model_source)
        self.assertIn("model: settings.model", model_source)

    def test_analysis_v1_quick_adv_resolves_public_tts_aliases(self) -> None:
        router_source = ROUTER_PATH.read_text(encoding="utf-8")
        module_source = ANALYSIS_V1_MODULE_PATH.read_text(encoding="utf-8")
        quick_run_section = module_source.split("async function startQuickTTSBuilderRun", 1)[1].split("beginBusy();", 1)[0]

        self.assertIn("def resolve_analysis_v1_tts_model_option(provider: str, model: str) -> tuple[str, str]:", router_source)
        self.assertIn("provider_value = model_value.split(PUBLIC_TTS_MODEL_SEGMENT, 1)[0]", router_source)
        self.assertIn("return resolve_tts_public_alias(ctx, provider_value, model_value)", router_source)
        self.assertIn("selected_providers, selected_model = resolve_analysis_v1_tts_model_option", router_source)
        self.assertIn("_providers, tts_model = resolve_analysis_v1_tts_model_option", router_source)
        self.assertIn("providers, tts_model = resolve_analysis_v1_tts_model_option", router_source)
        self.assertIn('providers: String(builderPayload.providers || "")', quick_run_section)
        self.assertIn('model: String(builderPayload.model || "")', quick_run_section)
        self.assertNotIn('providers: String(builderPayload.providers || "google")', quick_run_section)
        self.assertNotIn('model: String(builderPayload.model || "gemini-3.1-flash-tts-preview")', quick_run_section)


if __name__ == "__main__":
    unittest.main()
