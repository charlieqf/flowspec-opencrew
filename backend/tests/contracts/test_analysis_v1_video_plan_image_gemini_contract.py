from __future__ import annotations

import base64
import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
IMAGE_GEMINI_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "image_gemini.py"
EXECUTOR_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py"


def load_image_gemini_module():
    spec = importlib.util.spec_from_file_location("analysis_v1_image_gemini_contract", IMAGE_GEMINI_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1VideoPlanImageGeminiContractTest(unittest.TestCase):
    def test_gemini_first_frame_payload_requests_image_only(self) -> None:
        module = load_image_gemini_module()

        payload = module.gemini_image_generate_payload("Create one first frame", [])

        self.assertEqual(payload["contents"][0]["role"], "user")
        self.assertEqual(payload["generationConfig"]["responseModalities"], ["IMAGE"])

    def test_gemini_response_parser_ignores_text_and_extracts_inline_image(self) -> None:
        module = load_image_gemini_module()
        encoded = base64.b64encode(b"fake-image").decode("ascii")

        result = module.image_b64_from_response(
            "gemini",
            {"candidates": [{"content": {"parts": [{"text": "ok"}, {"inlineData": {"data": encoded}}]}}]},
        )

        self.assertEqual(result, encoded)

    def test_gemini_no_image_error_summarizes_thought_signature_response(self) -> None:
        module = load_image_gemini_module()
        thought_signature = "abc123" * 300

        with self.assertRaises(module.ToolError) as raised:
            module.image_b64_from_response(
                "gemini",
                {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "", "thoughtSignature": thought_signature}]}}]},
            )

        message = str(raised.exception)
        self.assertIn("finish_reasons=STOP", message)
        self.assertIn("thought_parts=1", message)
        self.assertNotIn("thoughtSignature", message)
        self.assertNotIn(thought_signature, message)

    def test_monolith_fallback_keeps_same_image_only_payload_contract(self) -> None:
        source = EXECUTOR_PATH.read_text(encoding="utf-8")

        self.assertIn('"generationConfig": {"responseModalities": ["IMAGE"]}', source)
        self.assertNotIn('"generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}', source)


if __name__ == "__main__":
    unittest.main()
