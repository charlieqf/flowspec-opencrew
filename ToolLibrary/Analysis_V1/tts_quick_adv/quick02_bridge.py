from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def load_quick02() -> Any:
    module_name = "analysis_v1_tts_builder_quick_bridge"
    if module_name in sys.modules:
        return sys.modules[module_name]
    module_path = Path(__file__).resolve().parents[1] / "03_02_TTSBuilderQuick.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load 03_02 helper module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
