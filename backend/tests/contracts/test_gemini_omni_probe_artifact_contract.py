from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "artifacts"
    / "gemini_omni_probe_2026-07-22.json"
)
PROBE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "integration"
    / "gemini_omni_live_probe.py"
)
LIVE_CHAIN_ARTIFACT_PATH = (
    REPO_ROOT
    / "docs"
    / "SessionDesign-R2"
    / "acceptance"
    / "2026-07-22"
    / "gemini-omni-paid-chain-20260723-v4.json"
)
LIVE_CHAIN_MEDIA_DIR = LIVE_CHAIN_ARTIFACT_PATH.parent / "assets" / "omni-paid-chain-20260723-v4"
LIVE_BROWSER_ARTIFACT_PATH = (
    REPO_ROOT
    / "docs"
    / "SessionDesign-R2"
    / "acceptance"
    / "2026-07-22"
    / "gemini-omni-paid-browser-20260723-v2.json"
)
LIVE_UPLOAD_BROWSER_ARTIFACT_PATH = (
    LIVE_BROWSER_ARTIFACT_PATH.parent
    / "gemini-omni-paid-upload-browser-20260723-v9.json"
)


class GeminiOmniProbeArtifactContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
        cls.serialized = json.dumps(cls.artifact, ensure_ascii=False, sort_keys=True)
        cls.probe_source = PROBE_PATH.read_text(encoding="utf-8")
        cls.live_chain = json.loads(LIVE_CHAIN_ARTIFACT_PATH.read_text(encoding="utf-8"))
        cls.live_chain_serialized = json.dumps(cls.live_chain, ensure_ascii=False, sort_keys=True)
        cls.live_browser = json.loads(
            LIVE_BROWSER_ARTIFACT_PATH.read_text(encoding="utf-8")
        )
        cls.live_browser_serialized = json.dumps(
            cls.live_browser,
            ensure_ascii=False,
            sort_keys=True,
        )
        cls.live_upload_browser = json.loads(
            LIVE_UPLOAD_BROWSER_ARTIFACT_PATH.read_text(encoding="utf-8")
        )
        cls.live_upload_browser_serialized = json.dumps(
            cls.live_upload_browser,
            ensure_ascii=False,
            sort_keys=True,
        )

    def test_current_paid_two_turn_chain_is_sanitized_reproducible_evidence(self) -> None:
        artifact = self.live_chain
        self.assertTrue(artifact["ok"])
        self.assertTrue(artifact["interactions_endpoint_proven"])
        self.assertEqual(artifact["mode"], "chain")
        self.assertEqual(artifact["model"], "gemini-omni-flash-preview")
        self.assertEqual(artifact["request"]["response_format"]["duration"], "3s")
        self.assertEqual(
            artifact["request"]["generation_config"],
            {"video_config": {"task": "text_to_video"}},
        )
        self.assertEqual(
            artifact["request"]["budget"],
            {
                "max_usd": 1.2,
                "max_calls": 2,
                "max_total_seconds": 6,
                "estimated_usd": 1.2,
                "calls_used": 2,
                "requested_seconds": 6,
            },
        )
        self.assertEqual(artifact["response_projection"]["http_status"], 200)
        self.assertEqual(artifact["response_projection"]["status"], "completed")
        self.assertTrue(artifact["response_projection"]["interaction_id_present"])
        self.assertEqual(artifact["response_projection"]["video_part_count"], 1)
        continuation = artifact["continuation"]
        self.assertTrue(continuation["ok"])
        self.assertTrue(continuation["request_projection"]["previous_interaction_id_present"])
        self.assertFalse(continuation["request_projection"]["generation_config_present"])
        self.assertEqual(continuation["response_projection"]["http_status"], 200)
        self.assertEqual(continuation["response_projection"]["status"], "completed")
        self.assertEqual(len(artifact["cleanup"]), 2)
        self.assertTrue(all(item["deleted"] for item in artifact["cleanup"]))
        self.assertIsNone(re.search(r"AIza[0-9A-Za-z_-]{20,}", self.live_chain_serialized))
        self.assertNotIn("previous_interaction_id\"", self.live_chain_serialized)

        self.assertEqual(len(artifact["media_artifacts"]), 2)
        for item in artifact["media_artifacts"]:
            media_path = LIVE_CHAIN_MEDIA_DIR / item["file"]
            content = media_path.read_bytes()
            self.assertEqual(len(content), item["bytes"])
            self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])

    def test_current_paid_browser_chain_proves_product_path_and_cloud_cleanup(self) -> None:
        artifact = self.live_browser
        self.assertTrue(artifact["ok"])
        self.assertEqual(artifact["test"], "gemini_omni_paid_live_browser")
        self.assertEqual(artifact["target"], "macmini-4 local only")
        self.assertEqual(
            artifact["budget"],
            {"max_usd": 0.6, "max_calls": 2, "max_total_seconds": 6},
        )
        self.assertEqual(len(artifact["turns"]), 2)
        first, second = artifact["turns"]
        self.assertEqual(first["operation"], "generate")
        self.assertEqual(second["operation"], "continue")
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertIsNone(first["parent_turn_id"])
        self.assertEqual(second["parent_turn_id"], first["video_turn_id"])
        self.assertEqual(second["video_thread_id"], first["video_thread_id"])
        self.assertNotEqual(second["output_path"], first["output_path"])

        cleanup = artifact["cloud_cleanup"]
        self.assertTrue(cleanup["ok"])
        self.assertEqual(cleanup["status"], "deleted")
        self.assertEqual(cleanup["deleted_count"], 2)
        self.assertEqual(cleanup["failed_count"], 0)
        self.assertTrue(
            all(turn["provider_state_status"] == "deleted" for turn in cleanup["turns"])
        )
        self.assertTrue(all(turn["can_continue"] is False for turn in cleanup["turns"]))

        self.assertIsNone(re.search(r"AIza[0-9A-Za-z_-]{20,}", self.live_browser_serialized))
        self.assertNotIn("interaction_id", self.live_browser_serialized)
        for screenshot in artifact["screenshots"]:
            self.assertTrue((LIVE_BROWSER_ARTIFACT_PATH.parent / screenshot).is_file())

    def test_current_paid_upload_edit_browser_proves_files_product_path(self) -> None:
        artifact = self.live_upload_browser
        self.assertTrue(artifact["ok"])
        self.assertEqual(artifact["test"], "gemini_omni_paid_upload_edit_live_browser")
        self.assertEqual(artifact["target"], "macmini-4 local only")
        self.assertEqual(
            artifact["budget"],
            {"max_usd": 0.3, "max_calls": 1, "max_total_seconds": 3},
        )
        turn = artifact["turn"]
        self.assertEqual(turn["operation"], "edit")
        self.assertEqual(turn["status"], "completed")
        self.assertIsNone(turn["parent_turn_id"])
        self.assertEqual(turn["provider_state_status"], "available")
        self.assertTrue(turn["can_continue"])
        cleanup = artifact["cloud_cleanup"]
        self.assertTrue(cleanup["ok"])
        self.assertEqual(cleanup["status"], "deleted")
        self.assertEqual(cleanup["deleted_count"], 1)
        self.assertEqual(cleanup["failed_count"], 0)
        self.assertEqual(cleanup["turns"][0]["provider_state_status"], "deleted")
        self.assertFalse(cleanup["turns"][0]["can_continue"])
        self.assertIsNone(re.search(r"AIza[0-9A-Za-z_-]{20,}", self.live_upload_browser_serialized))
        self.assertNotIn("interaction_id", self.live_upload_browser_serialized)
        for screenshot in artifact["screenshots"]:
            self.assertTrue((LIVE_UPLOAD_BROWSER_ARTIFACT_PATH.parent / screenshot).is_file())

    def test_evidence_is_sanitized_and_scoped(self) -> None:
        self.assertEqual(self.artifact["schema_version"], 1)
        self.assertEqual(self.artifact["probe"], "gemini_omni_live_probe")
        self.assertEqual(
            self.artifact["capture"]["kind"],
            "mixed evidence bundle",
        )
        self.assertEqual(
            self.artifact["capture"]["model_discovery_capture"],
            "direct sanitized probe projection",
        )
        self.assertEqual(
            self.artifact["capture"]["paid_interactions_capture"],
            "same-day sanitized reconstruction from terminal output",
        )
        self.assertFalse(
            self.artifact["capture"]["original_success_response_body_retained"]
        )
        self.assertTrue(
            self.artifact["capture"]["future_captures_written_directly_by_probe"]
        )
        self.assertFalse(self.artifact["environment"]["production_host_executed"])
        self.assertFalse(self.artifact["security"]["api_key_included"])
        self.assertFalse(self.artifact["security"]["raw_interaction_id_included"])
        self.assertFalse(self.artifact["security"]["video_base64_included"])
        self.assertFalse(self.artifact["security"]["customer_media_used"])
        self.assertIsNone(re.search(r"AIza[0-9A-Za-z_-]{20,}", self.serialized))

        discovery = self.artifact["model_discovery"]
        self.assertEqual(discovery["response_projection"]["http_status"], 200)
        self.assertEqual(
            discovery["response_projection"]["name"],
            "models/gemini-omni-flash-preview",
        )
        self.assertEqual(discovery["response_projection"]["version"], "001")
        self.assertEqual(discovery["evidence_scope"], "model_metadata_only")
        self.assertFalse(discovery["interactions_endpoint_proven"])
        self.assertEqual(
            discovery["response_projection"]["supported_generation_methods"],
            ["generateContent", "countTokens"],
        )
        self.assertEqual(discovery["status"], "passed")

        self.assertEqual(
            self.artifact["paid_generation"]["evidence_scope"],
            "same_day_reconstructed_paid_interactions_observation",
        )
        self.assertFalse(
            self.artifact["paid_generation"][
                "currently_online_reproducible_without_paid_gate"
            ]
        )
        generated = self.artifact["paid_generation"]["response_projection"]
        self.assertEqual(generated["http_status"], 200)
        self.assertEqual(generated["status"], "completed")
        self.assertEqual(generated["video_part_count"], 1)
        self.assertEqual(generated["inline_video_bytes"], [767294])
        self.assertEqual(
            generated["inline_video_sha256"],
            ["cbcf2b498637aa20faa34ebb112cafa889642c68b34fc869be48fd80657cec87"],
        )
        self.assertFalse(self.artifact["stateful_continuation"]["executed"])

    def test_observed_uri_delivery_requires_storage(self) -> None:
        invariant = self.artifact["observed_uri_store_invariant"]
        self.assertEqual(
            invariant["evidence_scope"],
            "same_day_reconstructed_paid_interactions_observation",
        )
        self.assertFalse(invariant["currently_online_reproducible_without_paid_gate"])
        self.assertEqual(invariant["request_projection"]["response_format"]["delivery"], "uri")
        self.assertFalse(invariant["request_projection"]["store"])
        self.assertEqual(invariant["response_projection"]["http_status"], 400)
        self.assertIn(
            "store=true",
            invariant["response_projection"]["error_message"],
        )
        self.assertFalse(invariant["generation_completed"])

    def test_official_preview_constraints_are_explicit(self) -> None:
        contract = self.artifact["official_contract_snapshot"]
        self.assertEqual(contract["model_status"], "preview")
        self.assertEqual(contract["endpoint"], "/v1beta/interactions")
        self.assertEqual(
            contract["tasks"],
            [
                "text_to_video",
                "image_to_video",
                "reference_to_video",
                "edit",
            ],
        )
        self.assertEqual(contract["stateful_parameter"], "previous_interaction_id")
        self.assertTrue(contract["store_false_prevents_stateful_continuation"])
        self.assertTrue(contract["interaction_scoped_parameters_must_be_resent"])
        self.assertTrue(contract["uploaded_video_uses_files_api"])
        self.assertEqual(contract["uploaded_video_wait_state"], "ACTIVE")
        self.assertEqual(contract["allowed_aspect_ratios"], ["16:9", "9:16"])
        self.assertEqual(
            contract["unsupported"],
            {
                "uploaded_audio_reference": True,
                "multi_video_reference_or_reasoning": True,
                "video_extension": True,
                "video_interpolation": True,
                "voice_editing": True,
            },
        )
        self.assertEqual(
            contract["uploaded_video_edit_region_restriction"],
            ["EEA", "Switzerland", "United Kingdom"],
        )
        self.assertTrue(contract["synthid_present_in_all_generated_videos"])
        self.assertEqual(contract["retention_days"]["paid_default"], 55)
        self.assertEqual(contract["retention_days"]["free_default"], 1)
        self.assertEqual(
            contract["retention_days"]["paid_configurable"],
            [7, 14, 28, 55],
        )

    def test_paid_probe_requires_explicit_bounded_gates(self) -> None:
        for token in (
            "OPENCREW_RUN_PAID_GEMINI_OMNI_SMOKE",
            "OPENCREW_GEMINI_OMNI_TEST_KEY_ISOLATED",
            "OPENCREW_GEMINI_OMNI_SMOKE_MAX_USD",
            "OPENCREW_GEMINI_OMNI_SMOKE_MAX_CALLS",
            "OPENCREW_GEMINI_OMNI_SMOKE_MAX_TOTAL_SECONDS",
            "SMOKE_DURATION_SECONDS = 3",
            "SCRIPT_MAX_USD = 1.20",
            "SCRIPT_MAX_CALLS = 2",
            "SCRIPT_MAX_SECONDS = SMOKE_DURATION_SECONDS * SCRIPT_MAX_CALLS",
            '"model_metadata_only"',
            '"interactions_endpoint_proven"',
            '"paid_interactions_live_probe"',
        ):
            self.assertIn(token, self.probe_source)
        self.assertIn('choices=("model", "generate", "chain")', self.probe_source)
        self.assertIn('default="model"', self.probe_source)
        self.assertIn('"duration": f"{SMOKE_DURATION_SECONDS}s"', self.probe_source)
        self.assertNotIn('"duration_seconds": 1', self.probe_source)

        spec = importlib.util.spec_from_file_location("gemini_omni_live_probe", PROBE_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "paid generation is disabled"):
                module.require_paid_gates()
        with patch.dict(
            os.environ,
            {
                module.PAID_GATE: "1",
                module.PAID_BUDGET: "1.20",
                module.PAID_MAX_CALLS: "2",
                module.PAID_MAX_SECONDS: "6",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "isolated test key"):
                module.require_paid_gates()
        with patch.dict(
            os.environ,
            {
                module.PAID_GATE: "1",
                module.ISOLATED_KEY_GATE: "1",
                module.PAID_BUDGET: "1.20",
                module.PAID_MAX_CALLS: "2",
                module.PAID_MAX_SECONDS: "6",
            },
            clear=True,
        ):
            self.assertEqual(
                module.require_paid_gates(),
                {
                    "max_usd": 1.2,
                    "max_calls": 2,
                    "max_total_seconds": 6,
                    "estimated_usd": 0.6,
                    "calls_used": 1,
                    "requested_seconds": 3,
                },
            )
            self.assertEqual(
                module.require_paid_gates(calls_used=2, requested_seconds=6),
                {
                    "max_usd": 1.2,
                    "max_calls": 2,
                    "max_total_seconds": 6,
                    "estimated_usd": 1.2,
                    "calls_used": 2,
                    "requested_seconds": 6,
                },
            )
        with patch.dict(
            os.environ,
            {
                module.PAID_GATE: "1",
                module.ISOLATED_KEY_GATE: "1",
                module.PAID_BUDGET: "0.60",
                module.PAID_MAX_CALLS: "1",
                module.PAID_MAX_SECONDS: "3",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "must be between 1.20"):
                module.require_paid_gates(calls_used=2, requested_seconds=6)


if __name__ == "__main__":
    unittest.main()
