from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from media_binaries import media_dependency_status, media_env


TOOL_NAME = "CheckMediaDependencies"
TOOL_VERSION = "0.1.0"


def binary_version(path: str) -> str:
    if not path:
        return ""
    result = subprocess.run([path, "-version"], capture_output=True, text=True, check=False, env=media_env())
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else ""


def build_result() -> dict[str, Any]:
    status = media_dependency_status()
    items = status.get("items", {})
    for item in items.values():
        if isinstance(item, dict) and item.get("available"):
            item["version"] = binary_version(str(item.get("path") or ""))
    all_available = all(bool(item.get("available")) for item in items.values() if isinstance(item, dict))
    return {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "passed" if all_available else "failed", **status}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check project/system ffmpeg and ffprobe availability.")
    parser.add_argument("--result-path", help="Optional JSON result path.")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = build_result()
    if args.result_path:
        path = Path(args.result_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["status"])


if __name__ == "__main__":
    main()
