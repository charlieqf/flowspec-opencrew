from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "model_leakage_policy.json"
MEDIA_CONFIG_PATH = REPO_ROOT / "ModelConfig" / "backend" / "opcrew_model_config" / "media_model_config.py"
FRONTEND_SOURCE_ROOTS = (
    REPO_ROOT / "frontend" / "src",
    REPO_ROOT / "ModelConfig" / "frontend" / "src",
)
REVIEWED_PROVIDER_BRANDS = {"heygen", "chanjing", "minimax", "cosyvoice"}


def provider_brand_pattern(name: str) -> str:
    return rf"(?<![a-z0-9]){re.escape(name)}(?:[_-][a-z0-9]+)*(?=$|[^a-z0-9])"


class ModelLeakagePolicyContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    def test_policy_is_versioned_and_covers_reviewed_provider_brands(self) -> None:
        self.assertGreaterEqual(int(self.policy.get("version") or 0), 1)
        self.assertTrue(REVIEWED_PROVIDER_BRANDS.issubset(set(self.policy.get("provider_brands") or [])))
        self.assertTrue(REVIEWED_PROVIDER_BRANDS.issubset(set(self.policy.get("egress_provider_brands") or [])))
        self.assertTrue(set(self.policy.get("egress_provider_brands") or []).issubset(set(self.policy.get("provider_brands") or [])))
        self.assertTrue({"provider_label_real", "model_label_real"}.issubset(set(self.policy.get("forbidden_fields") or [])))

    def test_every_media_catalog_provider_is_covered_by_bundle_policy(self) -> None:
        patterns = [provider_brand_pattern(name) for name in self.policy.get("provider_brands") or []]
        patterns.extend(self.policy.get("provider_literal_patterns") or [])
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        source = MEDIA_CONFIG_PATH.read_text(encoding="utf-8")
        providers = set(re.findall(r'"provider"\s*:\s*"([^"]+)"', source))
        self.assertTrue(providers)
        uncovered = sorted(
            provider
            for provider in providers
            if not any(regex.search(provider) or regex.search(json.dumps(provider)) for regex in compiled)
        )
        self.assertEqual(uncovered, [])

    def test_temporary_bundle_debt_is_bounded_and_owned(self) -> None:
        allowances = self.policy.get("temporary_bundle_allowances") or {}
        for pattern_id, allowance in allowances.items():
            with self.subTest(pattern_id=pattern_id):
                self.assertIsInstance(allowance, dict)
                self.assertGreater(int(allowance.get("max_matches") or 0), 0)
                self.assertTrue(str(allowance.get("owner_phase") or "").strip())
                self.assertTrue(str(allowance.get("expires") or "").strip())

    def test_reviewed_provider_brands_are_absent_from_anonymous_frontend_sources(self) -> None:
        findings: list[str] = []
        for root in FRONTEND_SOURCE_ROOTS:
            for path in root.rglob("*"):
                if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
                    continue
                source = path.read_text(encoding="utf-8").lower()
                for brand in REVIEWED_PROVIDER_BRANDS:
                    if re.search(provider_brand_pattern(brand), source, re.IGNORECASE):
                        findings.append(f"{path.relative_to(REPO_ROOT)}: {brand}")
        self.assertEqual(findings, [])

    def test_media_credentials_are_schema_driven_in_both_frontends(self) -> None:
        backend_source = MEDIA_CONFIG_PATH.read_text(encoding="utf-8")
        model_config_source = (REPO_ROOT / "ModelConfig" / "frontend" / "src" / "shared" / "MediaConfigModalBase.tsx").read_text(encoding="utf-8")
        shell_source = (REPO_ROOT / "frontend" / "src" / "shell" / "controllers" / "useMediaSettingsController.jsx").read_text(encoding="utf-8")
        self.assertIn('"credential_fields": provider_credential_fields(kind, provider)', backend_source)
        self.assertIn("credential_fields", model_config_source)
        self.assertIn("credential_fields", shell_source)
        self.assertNotIn("chanjing", model_config_source.lower())
        self.assertNotIn("chanjing", shell_source.lower())


if __name__ == "__main__":
    unittest.main()
