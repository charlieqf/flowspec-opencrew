from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from fastapi import HTTPException  # noqa: E402

from opcrew_backend.model_policy import (  # noqa: E402
    DEFAULT_USER_MODEL_POLICY,
    SURFACE_ANALYSIS_V1_PROMPT,
    SURFACE_ANALYSIS_V1_RUN,
    SURFACE_KOUBO_ASSET_AGENT_CHAT,
    SURFACE_KOUBO_HOST_PRODUCT_PROMPT,
    SURFACE_KOUBO_TTS_TIMING,
    SURFACE_MEDIA_LIBRARY_COMPOSITE,
    SURFACE_MEDIA_LIBRARY_SEARCH_PLANNER,
    SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC,
    fixed_fields_update_for_role,
    hidden_model_defaults_for_role,
    mask_model_fields_for_role,
    mask_model_fields_under_keys_for_role,
    mask_prompt_models_for_role,
    model_surface_policy_for_role,
    model_supports_input_modality,
    request_role,
    require_image_input_capability,
    resolve_prompt_model_for_role,
)
from opcrew_backend.routes.auth import AUTH_ROLE_ADMIN, AUTH_ROLE_USER  # noqa: E402


class FakeContext:
    def get_setting(self, key: str, default: object = None) -> object:
        return default

    def set_setting(self, key: str, value: object) -> None:
        return None


PROMPT_MODELS = {
    "items": [
        {
            "providerID": "openai",
            "providerName": "OpenAI",
            "modelID": "gpt-5.5",
            "modelName": "GPT-5.5",
            "inputModalities": ["text", "image"],
        },
        {
            "providerID": "opencode",
            "providerName": "OpenCode Zen",
            "modelID": "deepseek-v4-flash-free",
            "modelName": "DeepSeek v4 flash free",
            "inputModalities": ["text"],
        },
        {"providerID": "anthropic", "providerName": "Anthropic", "modelID": "claude-opus-5", "modelName": "Claude Opus 5"},
    ],
    "default_model": {"providerID": "anthropic", "modelID": "claude-opus-5"},
}


class LightweightRoleModelPolicyContractTest(unittest.TestCase):
    def test_missing_or_unknown_request_role_fails_closed_to_user(self) -> None:
        self.assertEqual(request_role(None), AUTH_ROLE_USER)
        self.assertEqual(
            request_role(SimpleNamespace(state=SimpleNamespace())),
            AUTH_ROLE_USER,
        )
        self.assertEqual(
            request_role(
                SimpleNamespace(
                    state=SimpleNamespace(opencrew_auth_role="unknown")
                )
            ),
            AUTH_ROLE_USER,
        )
        for role in (AUTH_ROLE_ADMIN, AUTH_ROLE_USER):
            with self.subTest(role=role):
                self.assertEqual(
                    request_role(
                        SimpleNamespace(
                            state=SimpleNamespace(
                                opencrew_auth_role=role
                            )
                        )
                    ),
                    role,
                )

    def test_user_catalog_uses_aliases_without_real_provider_or_model_names(self) -> None:
        masked = mask_prompt_models_for_role(FakeContext(), AUTH_ROLE_USER, SURFACE_ANALYSIS_V1_PROMPT, PROMPT_MODELS)  # type: ignore[arg-type]

        self.assertEqual(masked["default_model"], {"providerID": "Max", "modelID": "Max"})
        self.assertEqual([(item["providerID"], item["modelID"]) for item in masked["items"]], [("Max", "Max"), ("Flash", "Flash")])
        text = json.dumps(masked, ensure_ascii=False)
        for forbidden in ("openai", "OpenAI", "gpt-5.5", "GPT-5.5", "OpenCode Zen", "deepseek-v4-flash-free", "Anthropic"):
            self.assertNotIn(forbidden, text)

    def test_admin_catalog_is_not_masked(self) -> None:
        masked = mask_prompt_models_for_role(FakeContext(), AUTH_ROLE_ADMIN, SURFACE_ANALYSIS_V1_PROMPT, PROMPT_MODELS)  # type: ignore[arg-type]

        self.assertIs(masked, PROMPT_MODELS)

    def test_user_alias_resolution_maps_alias_or_equivalent_real_values_to_real_model(self) -> None:
        ctx = FakeContext()
        resolved, masked_catalog = resolve_prompt_model_for_role(  # type: ignore[arg-type]
            ctx,
            AUTH_ROLE_USER,
            SURFACE_ANALYSIS_V1_RUN,
            PROMPT_MODELS,
            "Max",
            "Max",
            "Run",
        )

        self.assertEqual(resolved, {"providerID": "openai", "modelID": "gpt-5.5"})
        self.assertEqual(masked_catalog["default_model"], {"providerID": "Max", "modelID": "Max"})

        resolved_raw, _catalog = resolve_prompt_model_for_role(  # type: ignore[arg-type]
            ctx,
            AUTH_ROLE_USER,
            SURFACE_ANALYSIS_V1_RUN,
            PROMPT_MODELS,
            "openai",
            "gpt-5.5",
            "Run",
        )
        self.assertEqual(resolved_raw, {"providerID": "openai", "modelID": "gpt-5.5"})

        with self.assertRaises(HTTPException) as raised:
            resolve_prompt_model_for_role(  # type: ignore[arg-type]
                ctx,
                AUTH_ROLE_USER,
                SURFACE_ANALYSIS_V1_RUN,
                PROMPT_MODELS,
                "anthropic",
                "claude-4",
                "Run",
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_user_host_product_policy_exposes_max_and_flash_aliases(self) -> None:
        masked = mask_prompt_models_for_role(FakeContext(), AUTH_ROLE_USER, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, PROMPT_MODELS)  # type: ignore[arg-type]

        self.assertEqual([(item["providerID"], item["modelID"]) for item in masked["items"]], [("Max", "Max"), ("Flash", "Flash")])

        resolved_max, _catalog = resolve_prompt_model_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_KOUBO_HOST_PRODUCT_PROMPT,
            PROMPT_MODELS,
            "Max",
            "Max",
            "Prompt",
        )
        self.assertEqual(resolved_max, {"providerID": "openai", "modelID": "gpt-5.5"})

        resolved, _catalog = resolve_prompt_model_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_KOUBO_HOST_PRODUCT_PROMPT,
            PROMPT_MODELS,
            "Flash",
            "Flash",
            "Prompt",
        )
        self.assertEqual(resolved, {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"})

        resolved_raw_flash, _catalog = resolve_prompt_model_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_KOUBO_HOST_PRODUCT_PROMPT,
            PROMPT_MODELS,
            "opencode",
            "deepseek-v4-flash-free",
            "Prompt",
        )
        self.assertEqual(resolved_raw_flash, {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"})

    def test_user_asset_agent_chat_policy_exposes_aliases_and_resolves_flash(self) -> None:
        masked = mask_prompt_models_for_role(FakeContext(), AUTH_ROLE_USER, SURFACE_KOUBO_ASSET_AGENT_CHAT, PROMPT_MODELS)  # type: ignore[arg-type]

        self.assertEqual([(item["providerID"], item["modelID"]) for item in masked["items"]], [("Max", "Max"), ("Flash", "Flash")])

        resolved, _catalog = resolve_prompt_model_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_KOUBO_ASSET_AGENT_CHAT,
            PROMPT_MODELS,
            "Flash",
            "Flash",
            "Prompt",
        )
        self.assertEqual(resolved, {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"})

    def test_media_library_model_surfaces_are_alias_only_and_read_only(self) -> None:
        expected = {
            SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC: (
                "media_library.visual_semantic",
                "visual_semantic_default_v1",
            ),
            SURFACE_MEDIA_LIBRARY_COMPOSITE: (
                "media_library.composite",
                "composite_default_v1",
            ),
            SURFACE_MEDIA_LIBRARY_SEARCH_PLANNER: (
                "media_library.search_planner",
                "search_planner_default_v1",
            ),
        }
        for surface, (literal, version) in expected.items():
            with self.subTest(surface=surface):
                self.assertEqual(surface, literal)
                policy = DEFAULT_USER_MODEL_POLICY["surfaces"][surface]
                self.assertEqual(policy["mode"], "alias")
                self.assertTrue(policy["alias_only"])
                self.assertTrue(policy["read_only"])
                self.assertEqual(policy["version"], version)
                descriptor = model_surface_policy_for_role(  # type: ignore[arg-type]
                    FakeContext(), AUTH_ROLE_USER, surface
                )
                self.assertEqual(descriptor["surface"], literal)
                self.assertEqual(descriptor["version"], version)
                self.assertTrue(descriptor["read_only"])
                serialized = json.dumps(descriptor, ensure_ascii=False)
                for forbidden in (
                    "openai",
                    "gpt-5.5",
                    "opencode",
                    "deepseek-v4-flash-free",
                    "provider_label_real",
                    "model_label_real",
                ):
                    self.assertNotIn(forbidden, serialized)

        visual_descriptor = model_surface_policy_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC,
        )
        self.assertEqual(
            visual_descriptor["required_input_modalities"], ["image"]
        )
        masked_error = mask_prompt_models_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_MEDIA_LIBRARY_COMPOSITE,
            {
                **PROMPT_MODELS,
                "error": "OpenAI gpt-5.5 connection failed",
            },
        )
        self.assertEqual(
            masked_error["error"],
            "Approved model catalog is unavailable",
        )
        self.assertNotIn(
            "openai", json.dumps(masked_error, ensure_ascii=False).lower()
        )

    def test_media_library_user_resolves_alias_but_cannot_submit_real_model(self) -> None:
        for surface in (
            SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC,
            SURFACE_MEDIA_LIBRARY_COMPOSITE,
            SURFACE_MEDIA_LIBRARY_SEARCH_PLANNER,
        ):
            with self.subTest(surface=surface):
                resolved, masked = resolve_prompt_model_for_role(  # type: ignore[arg-type]
                    FakeContext(),
                    AUTH_ROLE_USER,
                    surface,
                    PROMPT_MODELS,
                    "Max",
                    "Max",
                    "Media analysis",
                )
                self.assertEqual(
                    resolved,
                    {"providerID": "openai", "modelID": "gpt-5.5"},
                )
                if surface == SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC:
                    self.assertEqual(
                        [
                            (item["providerID"], item["modelID"])
                            for item in masked["items"]
                        ],
                        [("Max", "Max")],
                    )
                response_text = json.dumps(masked, ensure_ascii=False)
                self.assertNotIn("openai", response_text)
                self.assertNotIn("gpt-5.5", response_text)

                with self.assertRaises(HTTPException) as raised:
                    resolve_prompt_model_for_role(  # type: ignore[arg-type]
                        FakeContext(),
                        AUTH_ROLE_USER,
                        surface,
                        PROMPT_MODELS,
                        "openai",
                        "gpt-5.5",
                        "Media analysis",
                    )
                self.assertEqual(raised.exception.status_code, 403)
                self.assertNotIn(
                    "openai",
                    json.dumps(
                        raised.exception.detail, ensure_ascii=False
                    ).lower(),
                )

        with self.assertRaises(HTTPException) as unsupported:
            resolve_prompt_model_for_role(  # type: ignore[arg-type]
                FakeContext(),
                AUTH_ROLE_USER,
                SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC,
                PROMPT_MODELS,
                "Flash",
                "Flash",
                "Visual semantic",
            )
        self.assertEqual(unsupported.exception.status_code, 409)
        self.assertEqual(
            unsupported.exception.detail["code"],
            "model_input_capability_missing",
        )
        unsupported_detail = json.dumps(
            unsupported.exception.detail, ensure_ascii=False
        )
        self.assertNotIn("opencode", unsupported_detail)
        self.assertNotIn("deepseek", unsupported_detail)

    def test_image_input_capability_validation_is_strict_and_non_leaking(self) -> None:
        catalog = {
            "items": [
                {
                    "providerID": "openai",
                    "providerName": "OpenAI",
                    "modelID": "gpt-vision",
                    "modelName": "Vision",
                    "inputModalities": ["text", "image"],
                },
                {
                    "providerID": "opencode",
                    "providerName": "OpenCode",
                    "modelID": "text-only",
                    "modelName": "Text",
                    "inputModalities": ["text"],
                },
            ]
        }
        vision = {"providerID": "openai", "modelID": "gpt-vision"}
        text_only = {
            "providerID": "opencode",
            "modelID": "text-only",
        }
        self.assertTrue(
            model_supports_input_modality(catalog, vision, "image")
        )
        self.assertFalse(
            model_supports_input_modality(catalog, text_only, "image")
        )
        self.assertFalse(
            model_supports_input_modality(
                catalog,
                {"providerID": "missing", "modelID": "unknown"},
                "image",
            )
        )
        self.assertIs(require_image_input_capability(catalog, vision), vision)

        for unsupported in (
            text_only,
            {"providerID": "secret-provider", "modelID": "secret-model"},
        ):
            with self.subTest(unsupported=unsupported):
                with self.assertRaises(HTTPException) as raised:
                    require_image_input_capability(catalog, unsupported)
                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(
                    raised.exception.detail["code"],
                    "model_input_capability_missing",
                )
                detail = json.dumps(
                    raised.exception.detail, ensure_ascii=False
                )
                self.assertNotIn(
                    str(unsupported["providerID"]), detail
                )
                self.assertNotIn(str(unsupported["modelID"]), detail)

    def test_input_capability_accepts_current_and_legacy_catalog_shapes(self) -> None:
        catalog = {
            "items": [
                {
                    "providerID": "openai",
                    "modelID": "gpt-5.5",
                    "capabilities": {
                        "attachment": True,
                        "input": {
                            "text": True,
                            "image": True,
                            "video": False,
                        },
                    },
                },
                {
                    "providerID": "legacy",
                    "modelID": "legacy-vision",
                    "modalities": {"input": ["text", "image"]},
                },
                {
                    "providerID": "attachment-only",
                    "modelID": "unknown-inputs",
                    "capabilities": {"attachment": True},
                },
                {
                    "providerID": "explicit-false",
                    "modelID": "text-only",
                    "capabilities": {
                        "attachment": True,
                        "input": {"text": True, "image": False},
                    },
                },
                {
                    "providerID": "untrusted-shape",
                    "modelID": "string-boolean",
                    "capabilities": {"input": {"image": "true"}},
                },
            ]
        }
        self.assertTrue(
            model_supports_input_modality(
                catalog,
                {"providerID": "openai", "modelID": "gpt-5.5"},
                "image",
            )
        )
        self.assertTrue(
            model_supports_input_modality(
                catalog,
                {
                    "providerID": "legacy",
                    "modelID": "legacy-vision",
                },
                "image",
            )
        )
        for provider, model in (
            ("attachment-only", "unknown-inputs"),
            ("explicit-false", "text-only"),
            ("untrusted-shape", "string-boolean"),
            ("missing", "unknown"),
        ):
            with self.subTest(provider=provider, model=model):
                self.assertFalse(
                    model_supports_input_modality(
                        catalog,
                        {"providerID": provider, "modelID": model},
                        "image",
                    )
                )

        masked = mask_prompt_models_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC,
            catalog,
        )
        self.assertEqual(
            [
                (item["providerID"], item["modelID"])
                for item in masked["items"]
            ],
            [("Max", "Max")],
        )
        serialized = json.dumps(masked, ensure_ascii=False).lower()
        self.assertNotIn("openai", serialized)
        self.assertNotIn("gpt-5.5", serialized)

    def test_user_run_surface_uses_default_asr_fields(self) -> None:
        update = fixed_fields_update_for_role(FakeContext(), AUTH_ROLE_USER, SURFACE_ANALYSIS_V1_RUN, {}, set())  # type: ignore[arg-type]

        self.assertEqual(update, {"asr_mode": "default", "allow_cloud_asr_data_transfer": True})
        self.assertNotIn("mode", update)

        conflicting_update = fixed_fields_update_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_ANALYSIS_V1_RUN,
            {"asr_mode": "cloud", "allow_cloud_asr_data_transfer": False},
            {"asr_mode", "allow_cloud_asr_data_transfer"},
        )
        self.assertEqual(conflicting_update, {"asr_mode": "default", "allow_cloud_asr_data_transfer": True})
        self.assertNotIn("mode", conflicting_update)

    def test_hidden_tts_surface_defaults_model_and_removes_model_fields_from_payload(self) -> None:
        defaults = hidden_model_defaults_for_role(FakeContext(), AUTH_ROLE_USER, SURFACE_KOUBO_TTS_TIMING)  # type: ignore[arg-type]
        self.assertEqual(defaults, {"provider": "google", "model": "gemini-3.1-flash-tts-preview"})

        with self.assertRaises(HTTPException) as raised:
            hidden_model_defaults_for_role(FakeContext(), AUTH_ROLE_USER, SURFACE_KOUBO_TTS_TIMING, "google", "gemini-3.1-flash-tts-preview")  # type: ignore[arg-type]
        self.assertEqual(raised.exception.status_code, 403)

        masked = mask_model_fields_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_KOUBO_TTS_TIMING,
            {"provider": "google", "model": "gemini-3.1-flash-tts-preview", "voice": "ja-JP-Chirp3-HD-Aoede", "tts_tempo": 1.05},
        )
        self.assertEqual(masked, {"voice": "ja-JP-Chirp3-HD-Aoede", "tts_tempo": 1.05})

    def test_payload_masking_rewrites_real_models_to_aliases_recursively(self) -> None:
        payload = {
            "detail": {
                "prompt_model_provider": "openai",
                "prompt_model_id": "gpt-5.5",
                "attempts": [{"run_model_provider": "opencode", "run_model_id": "deepseek-v4-flash-free"}],
                "legacy": {"model_provider": "anthropic", "model_id": "claude-opus-5"},
                "prompt_models": {"items": [{"providerID": "Max", "providerName": "Max", "modelID": "Max", "modelName": "Max"}]},
            }
        }

        masked = mask_model_fields_for_role(FakeContext(), AUTH_ROLE_USER, SURFACE_ANALYSIS_V1_RUN, payload)  # type: ignore[arg-type]

        self.assertEqual(masked["detail"]["prompt_model_provider"], "Max")
        self.assertEqual(masked["detail"]["prompt_model_id"], "Max")
        self.assertEqual(masked["detail"]["attempts"][0]["run_model_provider"], "Flash")
        self.assertEqual(masked["detail"]["attempts"][0]["run_model_id"], "Flash")
        self.assertEqual(masked["detail"]["legacy"], {"model_provider": "", "model_id": ""})
        self.assertEqual(masked["detail"]["prompt_models"]["items"][0], {"providerID": "Max", "providerName": "Max", "modelID": "Max", "modelName": "Max"})

    def test_scoped_tts_hide_preserves_host_product_flash_aliases(self) -> None:
        payload = {
            "task": {"run_model_provider": "opencode", "run_model_id": "deepseek-v4-flash-free"},
            "plan": {
                "storyboard_tts_selection": {
                    "provider": "google",
                    "model": "gemini-3.1-flash-tts-preview",
                    "voice": "ja-JP-Chirp3-HD-Aoede",
                    "top_candidates": [{"provider": "google", "model": "gemini-3.1-flash-tts-preview", "voice": "ja-JP-Chirp3-HD-Aoede"}],
                }
            },
        }

        masked = mask_model_fields_for_role(FakeContext(), AUTH_ROLE_USER, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, payload)  # type: ignore[arg-type]
        masked = mask_model_fields_under_keys_for_role(  # type: ignore[arg-type]
            FakeContext(),
            AUTH_ROLE_USER,
            SURFACE_KOUBO_TTS_TIMING,
            masked,
            {"storyboard_tts_selection", "top_candidates"},
        )

        self.assertEqual(masked["task"], {"run_model_provider": "Flash", "run_model_id": "Flash"})
        self.assertEqual(masked["plan"]["storyboard_tts_selection"], {"voice": "ja-JP-Chirp3-HD-Aoede", "top_candidates": [{"voice": "ja-JP-Chirp3-HD-Aoede"}]})


if __name__ == "__main__":
    unittest.main()
