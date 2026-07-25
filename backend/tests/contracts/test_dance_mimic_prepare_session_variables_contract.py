from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_IMPL_PATH = REPO_ROOT / "ToolLibrary" / "DanceMimic_V1" / "_tool_impl.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("dance_mimic_prepare_session_variables_tool", TOOL_IMPL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DanceMimicPrepareSessionVariablesContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = load_tool()

    def setUp(self) -> None:
        self.original_fetch_default_video_config = self.tool.fetch_dance_mimic_default_video_config
        self.tool.fetch_dance_mimic_default_video_config = lambda _args: (
            {
                "kind": "video",
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
                "model_label": "ByteDance Seedance 2.0",
                "model_alias": "MaxSR2",
                "api_key_ref": "video_openrouter_key",
                "has_api_key": True,
                "source": "postgres:tool_media_provider_configs:provider=openrouter",
                "extra": {"reference_mode": "input_references"},
                "extra_json": {"reference_mode": "input_references"},
            },
            [],
        )

    def tearDown(self) -> None:
        self.tool.fetch_dance_mimic_default_video_config = self.original_fetch_default_video_config

    def run_00(self, root: Path, *extra_args: str) -> dict:
        code = self.tool.run_tool("00", ["--workspace", str(root), *extra_args])
        self.assertEqual(code, 0)
        variables_path = root / self.tool.VARIABLES_REL
        self.assertTrue(variables_path.exists())
        return json.loads(variables_path.read_text(encoding="utf-8"))

    def test_00_writes_dance_mimic_openrouter_video_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "reference.mp4"
            source.write_bytes(b"reference-video")

            variables = self.run_00(root, "--source-video-path", str(source))

            self.assertEqual(variables["default_video_config"]["kind"], "video")
            self.assertEqual(variables["default_video_config"]["provider"], "openrouter")
            self.assertEqual(variables["default_video_config"]["model"], "bytedance/seedance-2.0")
            self.assertEqual(variables["default_video_config"]["model_label"], "ByteDance Seedance 2.0")
            self.assertEqual(variables["default_video_config"]["model_alias"], "MaxSR2")
            self.assertEqual(variables["default_video_config"]["api_key_ref"], "video_openrouter_key")
            self.assertEqual(variables["default_video_config"]["source"], "postgres:tool_media_provider_configs:provider=openrouter")
            self.assertEqual(variables["default_video_config"]["extra"]["reference_mode"], "input_references")
            self.assertNotIn("api_key_ciphertext", json.dumps(variables))

    def test_00_overwrites_non_dance_mimic_video_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "reference.mp4"
            source.write_bytes(b"reference-video")
            variables_path = root / self.tool.VARIABLES_REL
            variables_path.parent.mkdir(parents=True, exist_ok=True)
            variables_path.write_text(
                json.dumps(
                    {
                        "source_video_path": str(source),
                        "default_video_config": {
                            "kind": "video",
                            "provider": "xai",
                            "model": "grok-imagine-video",
                            "api_key_ref": "video_xai_key",
                        },
                    }
                ),
                encoding="utf-8",
            )

            variables = self.run_00(root)

            self.assertEqual(variables["default_video_config"]["provider"], "openrouter")
            self.assertEqual(variables["default_video_config"]["model"], "bytedance/seedance-2.0")
            self.assertEqual(variables["default_video_config"]["api_key_ref"], "video_openrouter_key")
            self.assertEqual(variables["default_video_config"]["source"], "postgres:tool_media_provider_configs:provider=openrouter")

    def test_database_openrouter_fast_model_is_normalized_to_dance_mimic_seedance_2(self) -> None:
        class FakeAnalysisModule:
            @staticmethod
            def parse_extra_json(value: object) -> dict:
                return json.loads(str(value)) if value else {}

            @staticmethod
            def resolve_secret_value(api_key_ref: str, legacy_key: str) -> str:
                return "saved-key" if api_key_ref == "video_openrouter_key" or legacy_key else ""

        config, warnings = self.tool.dance_mimic_video_config_from_row(
            FakeAnalysisModule,
            {
                "kind": "video",
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0-fast",
                "enabled": True,
                "active": False,
                "api_key_ref": "video_openrouter_key",
                "api_key_ciphertext": "",
                "extra_json": '{"base_url":"https://openrouter.ai/api/v1"}',
                "updated_at": "2026-07-05 18:47:23+08:00",
            },
        )

        self.assertEqual(config["provider"], "openrouter")
        self.assertEqual(config["model"], "bytedance/seedance-2.0")
        self.assertEqual(config["model_label"], "ByteDance Seedance 2.0")
        self.assertEqual(config["model_alias"], "MaxSR2")
        self.assertEqual(config["extra"]["reference_mode"], "input_references")
        self.assertTrue(config["has_api_key"])
        self.assertEqual(warnings[0]["code"], "dance_mimic_video_model_overridden")


if __name__ == "__main__":
    unittest.main()
