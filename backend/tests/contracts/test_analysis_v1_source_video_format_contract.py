from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1SourceVideoFormatContractTest(unittest.TestCase):
    def test_force_prepare_preserves_application_and_consistency_assets(self) -> None:
        module = load_module("analysis_v1_prepare_preserves_assets_contract", REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "00_PrepareSessionVariables.py")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            protected = {
                "SessionContext/Consistency/HOST.png": b"host-image",
                "SessionContext/Consistency/Product.png": b"product-image",
                "SessionContext/PromptBuilder/draft.json": b"{}",
                "meta/thumbnails/preview.jpg": b"thumbnail",
                "outbox/result.txt": b"result",
            }
            for relative_path, content in protected.items():
                path = workspace / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            variables = workspace / module.VARIABLES_REL
            variables.parent.mkdir(parents=True, exist_ok=True)
            variables.write_text('{"stale": true}', encoding="utf-8")
            old_output = workspace / module.TOOL_DIR_NAME / "Output" / "old.json"
            old_output.parent.mkdir(parents=True, exist_ok=True)
            old_output.write_text("{}", encoding="utf-8")
            result: dict[str, object] = {"cleanup_actions": [], "prepared_directories": []}

            module.prepare_workspace_layout(workspace, True, result)

            for relative_path, content in protected.items():
                self.assertEqual((workspace / relative_path).read_bytes(), content)
            self.assertFalse(variables.exists())
            self.assertFalse(old_output.exists())
            self.assertEqual(
                {item["path"] for item in result["cleanup_actions"]},
                {module.VARIABLES_REL, module.TOOL_DIR_NAME},
            )

    def test_prepare_accepts_mov_and_normalizes_internal_source_path(self) -> None:
        module = load_module("analysis_v1_prepare_source_format_contract", REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "00_PrepareSessionVariables.py")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "inbox" / "reference_video.mov"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fake-mov")
            warnings: list[dict[str, str]] = []

            resolved = module.resolve_source_video(workspace, str(source), "", warnings)
            module.copy_source_video(resolved, workspace / module.SOURCE_VIDEO_REL, False, warnings)

            self.assertEqual(resolved, source.resolve(strict=True))
            self.assertEqual((workspace / module.SOURCE_VIDEO_REL).read_bytes(), b"fake-mov")
            self.assertIn("source_video_extension_normalized", {item["code"] for item in warnings})

    def test_probe_accepts_mov_when_session_context_points_to_mov(self) -> None:
        module = load_module("analysis_v1_probe_source_format_contract", REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "01_VideoProbeMetadata.py")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "SessionContext" / "Video_Source.mov"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fake-mov")
            variables = {"source_video_path": "SessionContext/Video_Source.mov"}
            (workspace / "SessionContext" / "Variables.json").write_text(json.dumps(variables), encoding="utf-8")

            resolved = module.resolve_source_video(workspace, variables)

            self.assertEqual(resolved, source)


if __name__ == "__main__":
    unittest.main()
