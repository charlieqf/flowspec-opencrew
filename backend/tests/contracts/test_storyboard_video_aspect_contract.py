from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StoryboardVideoAspectContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.aspect = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_aspect.py",
            "storyboard_video_aspect_contract",
        )

    def test_landscape_first_frame_is_center_cropped_to_16_9_without_resize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wide.png"
            Image.new("RGB", (960, 416), "navy").save(path)

            result = self.aspect.normalize_video_first_frame(path)
            with Image.open(path) as image:
                width, height = image.size

        self.assertEqual(result["aspect"], "16:9")
        self.assertEqual((result["original_width"], result["original_height"]), (960, 416))
        self.assertTrue(result["cropped"])
        self.assertFalse(result["resized"])
        self.assertEqual(height, 416)
        self.assertAlmostEqual(width / height, 16 / 9, delta=0.003)

    def test_portrait_first_frame_is_center_cropped_to_9_16_without_resize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portrait.png"
            Image.new("RGB", (800, 1200), "maroon").save(path)

            result = self.aspect.normalize_video_first_frame(path)
            with Image.open(path) as image:
                width, height = image.size

        self.assertEqual(result["aspect"], "9:16")
        self.assertTrue(result["cropped"])
        self.assertFalse(result["resized"])
        self.assertEqual(height, 1200)
        self.assertAlmostEqual(width / height, 9 / 16, delta=0.003)

    def test_standard_landscape_frame_is_not_resized_or_cropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "landscape.png"
            Image.new("RGB", (1280, 720), "black").save(path)

            result = self.aspect.normalize_video_first_frame(path)

        self.assertEqual(result["aspect"], "16:9")
        self.assertEqual((result["final_width"], result["final_height"]), (1280, 720))
        self.assertFalse(result["cropped"])
        self.assertFalse(result["resized"])

    def test_prompt_aspect_is_rewritten_without_changing_spoken_text(self) -> None:
        package = {
            "speech_prompt": "请逐字读出：比例 9:16",
            "storyboard_prompt": "Generate a vertical 9:16 continuation.",
            "positive_prompt": "真实竖屏 9:16 视频",
            "prompt": "Generate a vertical 9:16 continuation.",
            "extracted_fields": {"duration": 4},
        }

        result = self.aspect.prompt_package_for_video_aspect(package, "16:9")

        self.assertEqual(result["speech_prompt"], package["speech_prompt"])
        self.assertIn("horizontal 16:9", result["storyboard_prompt"])
        self.assertIn("横屏 16:9", result["positive_prompt"])
        self.assertEqual(result["extracted_fields"]["aspect_ratio"], "16:9")
        self.assertEqual(result["requested_aspect"], "16:9")

    def test_common_provider_context_overrides_vertical_defaults(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "storyboard_video_aspect_provider_context_contract",
        )
        captured: dict[str, Any] = {}

        class FakeVideoModule:
            @staticmethod
            def generate(context, _prompt_path, _output_path):
                captured.update(context)
                return {"status": "completed"}

        original_video_module_for = executor.video_module_for
        original_route_guard = executor.ensure_dance_mimic_openrouter_route
        executor.video_module_for = lambda *_args: FakeVideoModule
        executor.ensure_dance_mimic_openrouter_route = lambda *_args: None
        try:
            result = executor.generate_video_with_provider(
                {"provider": "xai", "model": "grok-imagine-video", "aspect_ratio": "9:16"},
                Path("prompt.json"),
                Path("output.mp4"),
                [],
                4,
                60,
                requested_aspect="16:9",
            )
        finally:
            executor.video_module_for = original_video_module_for
            executor.ensure_dance_mimic_openrouter_route = original_route_guard

        self.assertEqual(result["status"], "completed")
        self.assertEqual(captured["aspect"], "16:9")
        self.assertEqual(captured["requested_aspect"], "16:9")
        self.assertEqual(captured["config"]["aspect_ratio"], "16:9")
        self.assertEqual(captured["config"]["ratio"], "16:9")

    def test_video_only_existing_new_image_and_provider_copy_are_cropped(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_06_VideoOnlyPlanExecutor.py",
            "storyboard_video_only_aspect_contract",
        )
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            executor.ensure_tool_dirs(workspace)
            rel_path = "SessionOutput/storyboard/Working/dak_test_Image_New.png"
            source = workspace / rel_path
            source.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (960, 416), "green").save(source)
            task = {
                "asset_key": "dak_test",
                "planned_outputs": {"first_frame_path": rel_path},
                "source_segment": {"dialogue_asset_keys": []},
            }
            result: dict[str, Any] = {}

            prepared = executor.prepare_first_frame(
                workspace,
                SimpleNamespace(),
                {},
                {"shots": []},
                {},
                task,
                result,
            )
            with Image.open(source) as published, Image.open(prepared) as cropped:
                published_size = published.size
                cropped_size = cropped.size

        self.assertEqual(published_size, cropped_size)
        self.assertEqual(cropped_size[1], 416)
        self.assertAlmostEqual(cropped_size[0] / cropped_size[1], 16 / 9, delta=0.003)
        self.assertEqual(result["first_frame_normalization"]["dak_test"]["aspect"], "16:9")


if __name__ == "__main__":
    unittest.main()
