from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1RuntimeSelectionContractTest(unittest.TestCase):
    def test_audio_asr_candidates_prioritize_managed_runtime_over_backend_venv(self) -> None:
        module = load_module("analysis_v1_audio_asr_runtime_contract", REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "02_01_AudioASR.py")
        old_env = {key: os.environ.get(key) for key in ("HOME", "OPENCREW_DATA_DIR", "OPENCREW_ANALYSIS_V1_PYTHON", "OPENCREW_ANALYSIS_V1_RUNTIME_DIR")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "home"
                data_dir = Path(tmp) / "data"
                runtime_python = data_dir / "runtimes" / "analysis_v1_py312" / "bin" / "python"
                runtime_python.parent.mkdir(parents=True)
                runtime_python.write_text("# fake python\n", encoding="utf-8")
                os.environ["HOME"] = str(home)
                os.environ["OPENCREW_DATA_DIR"] = str(data_dir)
                os.environ.pop("OPENCREW_ANALYSIS_V1_PYTHON", None)
                os.environ.pop("OPENCREW_ANALYSIS_V1_RUNTIME_DIR", None)

                candidates = module.analysis_v1_runtime_python_candidates()

            self.assertEqual(candidates[0], runtime_python)
            self.assertIn(REPO_ROOT / "backend" / ".venv" / "bin" / "python", candidates)
            self.assertEqual(module.repo_root(), REPO_ROOT)
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_audio_asr_reexecs_to_configured_runtime_even_if_current_has_whisper(self) -> None:
        module = load_module("analysis_v1_audio_asr_reexec_contract", REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "02_01_AudioASR.py")
        old_env = {key: os.environ.get(key) for key in ("OPENCREW_ANALYSIS_V1_PYTHON", "ANALYSIS_V1_LOCAL_WHISPER_RUNTIME_REEXEC")}
        old_python_can_import = module.python_can_import
        old_execv = module.os.execv
        try:
            with tempfile.TemporaryDirectory() as tmp:
                runtime_python = Path(tmp) / "analysis_runtime" / "bin" / "python"
                runtime_python.parent.mkdir(parents=True)
                runtime_python.write_text("# fake python\n", encoding="utf-8")
                os.environ["OPENCREW_ANALYSIS_V1_PYTHON"] = str(runtime_python)
                os.environ.pop("ANALYSIS_V1_LOCAL_WHISPER_RUNTIME_REEXEC", None)
                module.python_can_import = lambda _python, _module: True
                captured: dict[str, object] = {}

                def fake_execv(path: str, argv: list[str]) -> None:
                    captured["path"] = path
                    captured["argv"] = argv
                    raise RuntimeError("execv captured")

                module.os.execv = fake_execv
                with self.assertRaisesRegex(RuntimeError, "execv captured"):
                    module.maybe_reexec_with_local_whisper_runtime()

            self.assertEqual(captured["path"], str(runtime_python))
            self.assertEqual(os.environ["ANALYSIS_V1_LOCAL_WHISPER_RUNTIME_REEXEC"], "1")
        finally:
            module.python_can_import = old_python_can_import
            module.os.execv = old_execv
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_video_srt_frame_candidates_include_home_managed_runtime(self) -> None:
        module = load_module("analysis_v1_video_srt_frame_runtime_contract", REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "02_02_VideoSRTFrame.py")
        old_env = {key: os.environ.get(key) for key in ("HOME", "OPENCREW_DATA_DIR", "OPENCREW_ANALYSIS_V1_PYTHON", "OPENCREW_ANALYSIS_V1_RUNTIME_DIR")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "home"
                data_dir = Path(tmp) / "data"
                runtime_python = home / ".opencrew" / "runtimes" / "analysis_v1_py312" / "bin" / "python"
                runtime_python.parent.mkdir(parents=True)
                runtime_python.write_text("# fake python\n", encoding="utf-8")
                os.environ["HOME"] = str(home)
                os.environ["OPENCREW_DATA_DIR"] = str(data_dir)
                os.environ.pop("OPENCREW_ANALYSIS_V1_PYTHON", None)
                os.environ.pop("OPENCREW_ANALYSIS_V1_RUNTIME_DIR", None)

                candidates = [str(path) for path in module.analysis_v1_runtime_python_candidates()]

            self.assertIn(str(runtime_python), candidates)
            self.assertEqual(module.repo_root(), REPO_ROOT)
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_video_srt_frame_distinguishes_venv_launchers_with_same_resolved_binary(self) -> None:
        module = load_module("analysis_v1_video_srt_frame_launcher_contract", REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "02_02_VideoSRTFrame.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "python3.12"
            target.write_text("# fake python\n", encoding="utf-8")
            backend_launcher = root / "backend" / ".venv" / "bin" / "python"
            managed_launcher = root / ".opencrew" / "runtimes" / "analysis_v1_py312" / "bin" / "python"
            backend_launcher.parent.mkdir(parents=True)
            managed_launcher.parent.mkdir(parents=True)
            backend_launcher.symlink_to(target)
            managed_launcher.symlink_to(target)

            self.assertEqual(backend_launcher.resolve(), managed_launcher.resolve())
            self.assertFalse(module.same_python_launcher(managed_launcher, backend_launcher))

    def test_framework_bridge_selects_home_managed_runtime(self) -> None:
        module = load_module("analysis_v1_framework_bridge_runtime_contract", REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "framework_bridge.py")
        old_home = os.environ.get("HOME")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "home"
                runtime_dir = home / ".opencrew" / "runtimes" / "analysis_v1_py312"
                runtime_python = runtime_dir / "bin" / "python"
                runtime_python.parent.mkdir(parents=True)
                runtime_python.write_text("# fake python\n", encoding="utf-8")
                os.environ["HOME"] = str(home)
                env = {"OPENCREW_DATA_DIR": str(Path(tmp) / "data")}

                selected = module._select_legacy_python(env)

            self.assertEqual(selected, str(runtime_python))
            self.assertEqual(env["OPENCREW_ANALYSIS_V1_PYTHON"], str(runtime_python))
            self.assertEqual(env["OPENCREW_ANALYSIS_V1_RUNTIME_DIR"], str(runtime_dir))
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
