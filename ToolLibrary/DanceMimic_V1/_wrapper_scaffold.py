from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

TOOLSET_ID = "DanceMimic_V1"
WORKFLOW_ID = "dance_mimic_v1"
SCHEMA_VERSION = "dance_mimic_v1_tool_result_0.1"

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "00": {
        "tool_id": "00_PrepareSessionVariables",
        "tool_version": "0.1.0",
        "tool_dir": "S1_00_PrepareSessionVariables",
        "display_name_zh": "准备会话变量",
        "blocked_code": "dance_mimic_00_not_implemented",
        "blocked_message": (
            "DanceMimic 00 is a thin wrapper placeholder. "
            "Extend ToolLibrary/Analysis_V1/00_PrepareSessionVariables.py with "
            "workflow_id=dance_mimic_v1 before enabling this step."
        ),
    },
    "01": {
        "tool_id": "01_ReferenceMediaDemux",
        "tool_version": "0.1.0",
        "tool_dir": "S2_01_ReferenceMediaDemux",
        "display_name_zh": "参考视频音画拆分",
        "blocked_code": "dance_mimic_01_not_implemented",
        "blocked_message": (
            "DanceMimic 01 scaffold is present, but reference media demux is "
            "not implemented in this phase."
        ),
    },
    "02": {
        "tool_id": "02_ReferenceFaceMaskedVideoBuild",
        "tool_version": "0.1.0",
        "tool_dir": "S3_02_ReferenceFaceMaskedVideoBuild",
        "display_name_zh": "参考视频人脸遮挡构建",
        "blocked_code": "dance_mimic_02_not_implemented",
        "blocked_message": (
            "DanceMimic 02 scaffold is present, but face masking and QA are "
            "not implemented in this phase."
        ),
    },
    "03": {
        "tool_id": "03_StoryBoardStandardTaskBuild",
        "tool_version": "0.1.0",
        "tool_dir": "S4_03_StoryBoardStandardTaskBuild",
        "display_name_zh": "标准 StoryBoard 构建",
        "blocked_code": "dance_mimic_03_not_implemented",
        "blocked_message": (
            "DanceMimic 03 scaffold is present, but StoryBoard standard task "
            "build is not implemented in this phase."
        ),
    },
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relpath(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def parse_args(argv: list[str], tool_id: str) -> tuple[argparse.Namespace, list[str]]:
    spec = TOOL_SPECS[tool_id]
    parser = argparse.ArgumentParser(description=f"DanceMimic_V1 scaffold wrapper for {spec['tool_id']}.")
    parser.add_argument("--workspace", required=True, help="Session workspace root.")
    parser.add_argument("--workflow-id", default=WORKFLOW_ID)
    parser.add_argument("--task-id", type=int)
    parser.add_argument("--session-id", type=int)
    parser.add_argument("--attempt-id", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_known_args(argv)


def base_result(spec: dict[str, Any], workspace: Path, args: argparse.Namespace, unknown_args: list[str]) -> dict[str, Any]:
    tool_dir = spec["tool_dir"]
    result_path = workspace / tool_dir / "Report" / "Result.json"
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": spec["tool_id"],
        "tool_id": spec["tool_id"],
        "tool_version": spec["tool_version"],
        "toolset_id": TOOLSET_ID,
        "workflow_id": WORKFLOW_ID,
        "status": "blocked",
        "task_id": args.task_id,
        "session_id": args.session_id,
        "attempt_id": args.attempt_id,
        "workspace_dir": str(workspace),
        "outputs": {
            "result_path": relpath(result_path, workspace),
        },
        "created_files": [
            relpath(result_path, workspace),
        ],
        "warnings": [],
        "blocked_reasons": [],
        "error": {},
        "scaffold": {
            "phase": "phase_0_toollibrary_cleanup",
            "implemented": False,
            "ignored_unknown_arg_count": len(unknown_args),
        },
        "updated_at": now_iso(),
    }


def run_scaffold(tool_id: str, argv: list[str] | None = None) -> int:
    if tool_id not in TOOL_SPECS:
        raise ValueError(f"Unknown DanceMimic scaffold tool id: {tool_id}")

    cli_args = list(sys.argv[1:] if argv is None else argv)
    args, unknown_args = parse_args(cli_args, tool_id)
    spec = TOOL_SPECS[tool_id]
    workspace = Path(args.workspace).expanduser()
    result = base_result(spec, workspace, args, unknown_args)

    if args.workflow_id != WORKFLOW_ID:
        code = "unsupported_workflow"
        message = f"{spec['tool_id']} only supports workflow_id={WORKFLOW_ID}."
    else:
        code = spec["blocked_code"]
        message = spec["blocked_message"]

    result["blocked_reasons"].append({"code": code, "message": message})
    result["error"] = {"code": code, "message": message}

    result_path = workspace / spec["tool_dir"] / "Report" / "Result.json"
    try:
        write_json(result_path, result)
    except Exception as exc:
        fallback = {
            "schema_version": SCHEMA_VERSION,
            "tool": spec["tool_id"],
            "toolset_id": TOOLSET_ID,
            "workflow_id": WORKFLOW_ID,
            "status": "failed",
            "error": {
                "code": "result_write_failed",
                "message": str(exc),
            },
            "updated_at": now_iso(),
        }
        if args.print_json:
            print(json.dumps(fallback, ensure_ascii=False, indent=2))
        return 1

    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2
