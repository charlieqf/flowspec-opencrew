from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_model_leakage_guard.py"
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))


def load_guard_script():
    spec = importlib.util.spec_from_file_location("opencrew_model_leakage_guard_check", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["opencrew_model_leakage_guard_check"] = module
    spec.loader.exec_module(module)
    return module


class ModelLeakageGuardCiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = load_guard_script()

    def test_customer_api_route_inventory_is_guarded_by_default(self) -> None:
        failures = self.guard.check_route_inventory()

        self.assertEqual(failures, [])

    def test_sample_customer_responses_scan_clean_after_c0_sanitization(self) -> None:
        failures = self.guard.check_sample_responses()

        self.assertEqual(failures, [])

    def test_session_file_policy_samples_block_provider_sidecars(self) -> None:
        failures = self.guard.check_session_file_policy_samples()

        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
