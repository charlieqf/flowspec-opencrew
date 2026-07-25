from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tool_impl import run_tool


if __name__ == "__main__":
    raise SystemExit(run_tool("03"))
