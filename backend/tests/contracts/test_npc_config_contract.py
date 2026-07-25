from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.routes.step2_tunnel import (  # noqa: E402
    DEFAULT_NPC_CONFIG,
    build_npc_conf_text,
    normalize_npc_config,
    sanitize_npc_conf_text,
)


class NpcConfigContractTest(unittest.TestCase):
    def test_default_config_does_not_emit_basic_auth(self) -> None:
        config = dict(DEFAULT_NPC_CONFIG)

        conf_text = build_npc_conf_text(config)

        self.assertNotIn("basic_username=", conf_text)
        self.assertNotIn("basic_password=", conf_text)

    def test_legacy_default_basic_auth_is_normalized_and_removed(self) -> None:
        config = {**DEFAULT_NPC_CONFIG, "basic_username": "11", "basic_password": "3"}
        legacy_text = "\n".join(
            [
                "[common]",
                "server_addr=113.125.202.171:8024",
                "basic_username=11",
                "basic_password=3",
                "crypt=true",
            ]
        )

        normalized = normalize_npc_config(config)
        sanitized = sanitize_npc_conf_text(legacy_text, normalized)

        self.assertEqual(normalized["basic_username"], "")
        self.assertEqual(normalized["basic_password"], "")
        self.assertNotIn("basic_username=", sanitized)
        self.assertNotIn("basic_password=", sanitized)
        self.assertIn("crypt=true", sanitized)

    def test_custom_basic_auth_is_kept_when_both_fields_are_explicit(self) -> None:
        config = {**DEFAULT_NPC_CONFIG, "basic_username": "operator", "basic_password": "strong-password"}

        conf_text = build_npc_conf_text(config)

        self.assertIn("basic_username=operator", conf_text)
        self.assertIn("basic_password=strong-password", conf_text)


if __name__ == "__main__":
    unittest.main()
