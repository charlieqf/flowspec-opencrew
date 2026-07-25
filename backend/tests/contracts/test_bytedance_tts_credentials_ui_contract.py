from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TTS_CONFIG_MODAL = REPO_ROOT / "ModelConfig" / "frontend" / "src" / "tts" / "TTSConfigModal.tsx"
APP_CSS = REPO_ROOT / "frontend" / "src" / "styles" / "app.css"


class ByteDanceTTSCredentialsUIContractTest(unittest.TestCase):
    def test_bytedance_tts_uses_app_id_and_access_token_fields(self) -> None:
        source = TTS_CONFIG_MODAL.read_text(encoding="utf-8")

        self.assertIn("type ByteDanceCredentialDraft", source)
        self.assertIn("appId: string", source)
        self.assertIn("accessToken: string", source)
        self.assertIn('provider.provider === "bytedance"', source)
        self.assertIn("<span>App ID</span>", source)
        self.assertIn("<span>Access Token</span>", source)
        self.assertIn('JSON.stringify({ app_id: appId, access_token: accessToken })', source)
        self.assertIn("requires both App ID and Access Token", source)
        self.assertIn("Credentials saved", source)
        self.assertIn("Credentials missing", source)

    def test_bytedance_tts_does_not_offer_legacy_single_field_placeholder(self) -> None:
        source = TTS_CONFIG_MODAL.read_text(encoding="utf-8")

        self.assertNotIn("BytePlus API Key or appid:access_token", source)

    def test_bytedance_credentials_are_stacked_vertically(self) -> None:
        source = APP_CSS.read_text(encoding="utf-8")

        self.assertIn(".tts-bytedance-credentials", source)
        self.assertIn("grid-template-columns: 1fr", source)


if __name__ == "__main__":
    unittest.main()
