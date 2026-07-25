from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.services.session_events import event_visible, parse_payload, present_event, serialize_payload  # noqa: E402


class SessionEventPolicyContractTest(unittest.TestCase):
    def test_public_event_is_visible_to_customer_and_share(self) -> None:
        row = {"kind": "user.message", "payload": serialize_payload({"text": "hello"})}

        self.assertTrue(event_visible(row, "customer"))
        self.assertTrue(event_visible(row, "share"))
        self.assertEqual(present_event(row)["visibility"], "public")

    def test_debug_event_is_hidden_from_share_but_visible_to_debug(self) -> None:
        row = {"kind": "opencode.provider.raw", "payload": serialize_payload({"properties": {"x": 1}})}

        self.assertFalse(event_visible(row, "share"))
        self.assertFalse(event_visible(row, "customer"))
        self.assertTrue(event_visible(row, "debug"))

    def test_namespaced_opencode_public_collision_stays_internal(self) -> None:
        row = {"kind": "opencode.session.error", "payload": serialize_payload({"message": "provider error"})}

        self.assertFalse(event_visible(row, "share"))
        self.assertFalse(event_visible(row, "customer"))
        self.assertEqual(present_event(row)["visibility"], "internal")

    def test_legacy_event_without_visibility_uses_kind_inference(self) -> None:
        public_row = {"kind": "assistant.final", "payload": "{\"text\":\"done\"}", "visibility": None}
        debug_row = {"kind": "workflow.plan.created", "payload": "{\"plan\":{}}", "visibility": None}

        self.assertTrue(event_visible(public_row, "customer"))
        self.assertFalse(event_visible(debug_row, "share"))

    def test_secret_and_pii_redaction(self) -> None:
        payload = parse_payload({
            "Authorization": "Bearer sk-secret-token",
            "email": "person@example.com",
            "phone": "+1 415 555 1212",
            "nested": {"api_key": "secret"},
        })

        text = str(payload)
        self.assertNotIn("sk-secret-token", text)
        self.assertNotIn("person@example.com", text)
        self.assertNotIn("+1 415 555 1212", text)
        self.assertIn("[REDACTED]", text)

    def test_numeric_ids_and_epoch_ms_are_not_redacted_as_phones(self) -> None:
        payload = parse_payload({"created_at": "1779849584351", "bytes": "1234567890", "phone": "415-555-1212", "e164": "+14155551212"})

        self.assertEqual(payload["created_at"], "1779849584351")
        self.assertEqual(payload["bytes"], "1234567890")
        self.assertEqual(payload["phone"], "[REDACTED_PHONE]")
        self.assertEqual(payload["e164"], "[REDACTED_PHONE]")


if __name__ == "__main__":
    unittest.main()
