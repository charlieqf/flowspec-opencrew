from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
KOUBO_CSS = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "styles" / "host-product-builder.css"
OCREBUILD_CSS = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "styles" / "oc-rebuild-host-product.css"


def css_block(source: str, selector: str) -> str:
    matches = re.findall(rf"{re.escape(selector)}\s*\{{(?P<body>[^}}]*)\}}", source)
    if not matches:
        raise AssertionError(f"Missing CSS selector: {selector}")
    return matches[-1]


class HostProductPreviewImageCssContractTest(unittest.TestCase):
    def assert_preserves_image_aspect_ratio(self, css: str, selector: str) -> None:
        block = css_block(css, selector)
        self.assertIn("max-width: 100%;", block)
        self.assertIn("max-height:", block)
        self.assertIn("width: auto;", block)
        self.assertIn("height: auto;", block)
        self.assertIn("object-fit: contain;", block)
        self.assertNotRegex(block, r"(?m)^\s*width:\s*100%;")

    def test_koubo_host_product_output_preview_preserves_image_aspect_ratio(self) -> None:
        css = KOUBO_CSS.read_text(encoding="utf-8")

        self.assert_preserves_image_aspect_ratio(css, ".kbsp-hpb-output img")
        button = css_block(css, ".kbsp-hpb-output-item > button:first-child")
        self.assertIn("display: flex;", button)
        self.assertIn("align-items: center;", button)
        self.assertIn("justify-content: center;", button)

    def test_ocrebuild_host_product_output_preview_preserves_image_aspect_ratio(self) -> None:
        css = OCREBUILD_CSS.read_text(encoding="utf-8")

        self.assert_preserves_image_aspect_ratio(css, ".host-product-output img")
        button = css_block(css, ".host-product-output-item > button:first-child")
        self.assertIn("display: flex;", button)
        self.assertIn("align-items: center;", button)
        self.assertIn("justify-content: center;", button)


if __name__ == "__main__":
    unittest.main()
