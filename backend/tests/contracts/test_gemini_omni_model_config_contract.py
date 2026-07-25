from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT / "backend", REPO_ROOT / "ModelConfig" / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from opcrew_model_config.media_model_config import (  # noqa: E402
    canonical_agent_model_alias_target,
    customer_media_public_config,
    media_options,
    normalize_agent_model_aliases,
    public_video_model_capability,
)


class GeminiOmniModelConfigContractTest(unittest.TestCase):
    def omni_model(self):
        providers = {item["provider"]: item for item in media_options("video")}
        models = {item["model"]: item for item in providers["gemini"]["models"]}
        return models.get("gemini-omni-flash-preview")

    def test_server_switch_controls_catalog_visibility(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(self.omni_model())
        with patch.dict(os.environ, {"OPENCREW_GEMINI_OMNI_ENABLED": "1"}, clear=True):
            self.assertIsNotNone(self.omni_model())

    def test_structured_tasks_are_not_derived_from_display_capabilities(self) -> None:
        with patch.dict(os.environ, {"OPENCREW_GEMINI_OMNI_ENABLED": "1"}, clear=True):
            model = self.omni_model()
        self.assertEqual(
            model["tasks"],
            ["text_to_video", "image_to_video", "reference_to_video", "edit"],
        )
        self.assertTrue(model["stateful_edit"])
        self.assertEqual(model["provider_state"], "interaction")
        self.assertTrue(model["supports_video_input"])
        self.assertFalse(model["supports_audio_input"])
        self.assertEqual(
            model["duration"],
            {
                "adjustable": False,
                "min": 3,
                "max": 3,
                "allowed": [3],
                "note": "当前 Preview 最短输出为 3 秒；OpenCrew 首期固定最短时长以控制付费和回归范围。",
            },
        )
        public = public_video_model_capability(model)
        self.assertEqual(public["tasks"], model["tasks"])
        self.assertNotIn("tasks", public.get("capabilities", []))

    def test_public_alias_resolves_canonically_and_needs_both_switches(self) -> None:
        self.assertEqual(
            canonical_agent_model_alias_target("video", "Omni Flash", "", ""),
            ("gemini", "gemini-omni-flash-preview"),
        )
        raw = {
            "kind": "video",
            "active_provider": "gemini",
            "providers": [
                {
                    "provider": "gemini",
                    "enabled": True,
                    "has_api_key": True,
                    "model": "gemini-omni-flash-preview",
                    "models": [],
                }
            ],
            "agent_model_aliases": [
                {
                    "alias": "Omni Flash",
                    "provider": "gemini",
                    "model": "gemini-omni-flash-preview",
                }
            ],
        }
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(customer_media_public_config(raw, "video")["agent_model_aliases"], [])
        with patch.dict(os.environ, {"OPENCREW_GEMINI_OMNI_ENABLED": "1"}, clear=True):
            self.assertEqual(
                customer_media_public_config(raw, "video")["agent_model_aliases"][0]["agentVideoAlias"],
                "Omni Flash",
            )
            disabled = {**raw, "providers": [{**raw["providers"][0], "enabled": False}]}
            self.assertEqual(customer_media_public_config(disabled, "video")["agent_model_aliases"], [])

    def test_switch_off_hides_but_preserves_stored_alias_for_rollback(self) -> None:
        stored = [{
            "alias": "Omni Flash",
            "provider": "gemini",
            "model": "gemini-omni-flash-preview",
            "created_at": 1,
            "updated_at": 1,
        }]
        with patch.dict(os.environ, {}, clear=True):
            normalized = normalize_agent_model_aliases("video", stored)
        self.assertEqual(normalized[0]["model"], "gemini-omni-flash-preview")


if __name__ == "__main__":
    unittest.main()
