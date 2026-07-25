from __future__ import annotations

import json
import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


FIXTURE_PATH = REPO_ROOT / "ToolLibrary" / "PromptKnowledge" / "fixtures" / "prompt_agent_result" / "cases.json"
PROMPT_AGENT_RESULT_PATH = BACKEND_ROOT / "opcrew_backend" / "koubo" / "koubo_storyboard" / "prompt_agent_result.py"


def load_prompt_agent_result_module():
    spec = importlib.util.spec_from_file_location("prompt_agent_result_contract", PROMPT_AGENT_RESULT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PromptAgentResultFixturesContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.module = load_prompt_agent_result_module()

    def assert_expected_parse(self, actual: dict | None, expected: dict | None) -> None:
        if expected is None:
            self.assertIsNone(actual)
            return
        self.assertIsNotNone(actual)
        assert actual is not None
        for key, value in expected.items():
            if key == "used_source_doc_ids":
                self.assertEqual(self.module.prompt_agent_result_doc_ids(actual), value)
            else:
                self.assertEqual(actual.get(key), value)

    def test_backend_parser_matches_shared_fixtures(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["name"]):
                results = self.module.extract_prompt_agent_results(case["input"])
                actual = results[-1] if results else None
                self.assert_expected_parse(actual, case.get("expected_parse"))

    def test_backend_source_validation_matches_shared_fixtures(self) -> None:
        for case in self.cases:
            if "expected_backend_after_validation" not in case:
                continue
            with self.subTest(case=case["name"]):
                results = self.module.extract_prompt_agent_results(case["input"])
                actual = results[-1] if results else None
                self.assertIsNotNone(actual)
                assert actual is not None
                validated, _source_validation = self.module.prompt_agent_validate_result_sources(actual, [])
                self.assert_expected_parse(validated, case["expected_backend_after_validation"])


if __name__ == "__main__":
    unittest.main()
