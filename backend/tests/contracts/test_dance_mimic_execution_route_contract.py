from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)


class DanceMimicExecutionRouteContractTest(unittest.TestCase):
    def test_dance_mimic_must_not_add_seedance_copy_provider(self) -> None:
        forbidden_paths = [
            REPO_ROOT / "ToolLibrary/Analysis_V1/video_plan_executor_modules/video_seedance_dancemimic.py",
            REPO_ROOT / "ToolLibrary/Analysis_V1/Reference/05_02/Video_Seedance_DanceMimic.md",
        ]
        for path in forbidden_paths:
            self.assertFalse(path.exists(), f"{path.relative_to(REPO_ROOT)} must not exist; DanceMimic must use OpenRouter input_references.")

    def test_video_executor_does_not_dispatch_to_seedance_dancemimic(self) -> None:
        executor_source = (REPO_ROOT / "ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py").read_text(encoding="utf-8")
        self.assertNotIn("video_seedance_dancemimic", executor_source)
        self.assertNotIn("Video_Seedance_DanceMimic.md", executor_source)

    def test_dance_mimic_design_points_to_openrouter_input_references(self) -> None:
        design_source = (REPO_ROOT / "docs/DanceMimic_V1/DanceMimic_V1_实施收敛设计.md").read_text(encoding="utf-8")
        self.assertIn("不新增 `video_sdr2v_dancemimic.py` provider 模块", design_source)
        self.assertIn("复用 `video_openrouter.py`", design_source)
        self.assertIn("reference_mode=input_references", design_source)


if __name__ == "__main__":
    unittest.main()
