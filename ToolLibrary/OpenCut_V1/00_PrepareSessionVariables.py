from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "00_PrepareSessionVariables"
TOOL_VERSION = "1.0.0"
WORKFLOW_ID = "open-cut-v1-dialogue"
CONTEXT_DIR_NAME = "SessionContext"
SOURCE_VIDEO_REL = f"{CONTEXT_DIR_NAME}/Video_Source.mp4"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
INPUT_MANIFEST_REL = f"{CONTEXT_DIR_NAME}/InputManifest.json"
TOOL_DIR_NAME = "S1_00_PrepareSessionVariables"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SUPPORTED_SOURCE_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}


@dataclass(frozen=True)
class Args:
    workspace: str
    source_video: str
    force: bool
    resume: bool
    print_json: bool


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def resolve_workspace(value: str) -> Path:
    return Path(value or ".").expanduser().resolve()


def resolve_source(workspace: Path, explicit: str) -> Path:
    if explicit:
        candidate = Path(explicit)
        candidate = candidate if candidate.is_absolute() else workspace / candidate
        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SOURCE_VIDEO_EXTS:
            return candidate.resolve()
        raise BlockedError("source_video_missing", f"Source video does not exist or is unsupported: {explicit}")
    current = workspace / SOURCE_VIDEO_REL
    if current.is_file():
        return current
    candidates = sorted(
        path for path in (workspace / "inbox").glob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_VIDEO_EXTS
    )
    if len(candidates) != 1:
        raise BlockedError("source_video_ambiguous", "OpenCut requires exactly one uploaded video in workspace/inbox.")
    return candidates[0]


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result: dict[str, Any] = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "outputs": {},
        "warnings": [],
        "blocked_reasons": [],
        "created_files": [],
        "updated_at": now_iso(),
    }
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        source = resolve_source(workspace, args.source_video)
        target = workspace / SOURCE_VIDEO_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        if source != target.resolve():
            if args.force or not target.exists():
                shutil.copy2(source, target)
        context0 = workspace / "0_SessionContext" / "Variables.json"
        variables_path = workspace / VARIABLES_REL
        variables = read_json(variables_path) or read_json(context0)
        variables.update({
            "schema_version": "opencut_v1_session_context_0.1",
            "workflow_id": str(variables.get("workflow_id") or WORKFLOW_ID),
            "source_video_path": SOURCE_VIDEO_REL,
            "selected_scheme": "dialogue",
            "updated_at": now_iso(),
        })
        write_json(variables_path, variables)
        manifest_path = workspace / INPUT_MANIFEST_REL
        if not manifest_path.exists():
            write_json(manifest_path, {
                "schema_version": "opencut_v1_input_manifest_0.1",
                "files": [{"path": SOURCE_VIDEO_REL, "source_kind": "uploaded_video"}],
            })
        result["outputs"] = {
            "variables": VARIABLES_REL,
            "input_manifest": INPUT_MANIFEST_REL,
            "source_video": SOURCE_VIDEO_REL,
        }
        result["created_files"] = [VARIABLES_REL, INPUT_MANIFEST_REL, SOURCE_VIDEO_REL]
    except BlockedError as exc:
        result["status"] = "blocked"
        result["blocked_reasons"].append({"code": exc.code, "message": exc.message})
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
    write_json(workspace / REPORT_RESULT_REL, result)
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Prepare an independent OpenCut V1 dialogue-analysis session.")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--source-video", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    namespace = parser.parse_args(argv)
    return Args(
        workspace=str(namespace.workspace or ""),
        source_video=str(namespace.source_video or ""),
        force=bool(namespace.force),
        resume=bool(namespace.resume),
        print_json=bool(namespace.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli_args:
        try:
            from ToolLibrary.OpenCut_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.OpenCut_V1.framework_bridge import maybe_run_framework_bridge
        framework_exit = maybe_run_framework_bridge(cli_args, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit
    args = parse_args(cli_args)
    result = run(args)
    if args.print_json or result["status"] != "completed":
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 2 if result["status"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
