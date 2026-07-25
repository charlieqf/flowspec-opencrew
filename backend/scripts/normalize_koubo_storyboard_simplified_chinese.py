from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text as sql_text


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.koubo.koubo_storyboard.text_utils import to_simplified_chinese  # noqa: E402


DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
SOURCE_REL = "SessionOutput/storyboard/srt_storyboard.json"
EDIT_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and normalize existing Koubo StoryBoard text fields to Simplified Chinese."
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--task-id", type=int, default=0, help="Limit to one OpenClip task id.")
    parser.add_argument("--session-id", type=int, default=0, help="Limit to one session id.")
    parser.add_argument("--workspace", action="append", default=[], help="Inspect a workspace directly; can be repeated.")
    parser.add_argument("--write", action="store_true", help="Apply changes. Defaults to dry-run.")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    parser.add_argument("--fail-on-changes", action="store_true", help="Exit non-zero if any traditional text is detected.")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def preview(value: str, limit: int = 80) -> str:
    text = value.replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def normalize_field(container: dict[str, Any], key: str, pointer: str, changes: list[dict[str, str]]) -> None:
    if key not in container or not isinstance(container.get(key), str):
        return
    before = str(container.get(key) or "")
    after = to_simplified_chinese(before).strip()
    if after == before:
        return
    container[key] = after
    changes.append({"path": f"{pointer}.{key}", "before": preview(before), "after": preview(after)})


def normalize_edit_storyboard(payload: dict[str, Any]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    normalize_field(payload, "video_formula", "$", changes)
    for shot_index, shot in enumerate(payload.get("shots") or []):
        if not isinstance(shot, dict):
            continue
        shot_pointer = f"$.shots[{shot_index}]"
        for key in ("shot_name", "formula_stage", "summary"):
            normalize_field(shot, key, shot_pointer, changes)
        for scene_index, scene in enumerate(shot.get("scenes") or []):
            if not isinstance(scene, dict):
                continue
            scene_pointer = f"{shot_pointer}.scenes[{scene_index}]"
            for key in ("scene_name", "summary"):
                normalize_field(scene, key, scene_pointer, changes)
            for dialogue_index, dialogue in enumerate(scene.get("dialogues") or []):
                if not isinstance(dialogue, dict):
                    continue
                dialogue_pointer = f"{scene_pointer}.dialogues[{dialogue_index}]"
                for key in ("text", "dialogue"):
                    normalize_field(dialogue, key, dialogue_pointer, changes)
    return changes


def normalize_source_storyboard(payload: dict[str, Any]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    normalize_field(payload, "video_formula", "$", changes)
    for shot_index, shot in enumerate(payload.get("shots") or []):
        if not isinstance(shot, dict):
            continue
        shot_pointer = f"$.shots[{shot_index}]"
        for key in ("title", "formula_stage", "summary", "dialogue"):
            normalize_field(shot, key, shot_pointer, changes)
        for scene_index, scene in enumerate(shot.get("scenes") or []):
            if not isinstance(scene, dict):
                continue
            scene_pointer = f"{shot_pointer}.scenes[{scene_index}]"
            for key in ("title", "summary", "dialogue"):
                normalize_field(scene, key, scene_pointer, changes)
            for item_index, item in enumerate(scene.get("dialogue_items") or []):
                if not isinstance(item, dict):
                    continue
                item_pointer = f"{scene_pointer}.dialogue_items[{item_index}]"
                for key in ("dialogue", "text", "original_dialogue", "rewritten_dialogue"):
                    normalize_field(item, key, item_pointer, changes)
    return changes


def normalize_storyboard_payload(payload: dict[str, Any], kind: str) -> list[dict[str, str]]:
    if kind == "edit":
        return normalize_edit_storyboard(payload)
    if kind == "source":
        return normalize_source_storyboard(payload)
    raise ValueError(f"Unknown storyboard kind: {kind}")


def normalize_storyboard_file(path: Path, kind: str, *, write: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "path": str(path),
        "kind": kind,
        "exists": path.exists(),
        "changed": False,
        "changed_fields": 0,
        "changes": [],
        "written": False,
        "error": "",
    }
    if not path.exists():
        return report
    try:
        payload = read_json(path)
        if not isinstance(payload, dict):
            report["error"] = "json_root_not_object"
            return report
        changes = normalize_storyboard_payload(payload, kind)
        report["changes"] = changes
        report["changed_fields"] = len(changes)
        report["changed"] = bool(changes)
        if changes and write:
            write_json_atomic(path, payload)
            report["written"] = True
    except Exception as exc:
        report["error"] = str(exc)
    return report


def inspect_workspace(session_id: int, task_id: int, workspace: Path, *, write: bool = False) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    report: dict[str, Any] = {
        "session_id": session_id,
        "task_id": task_id,
        "workspace": str(workspace),
        "exists": workspace.exists(),
        "files": [],
    }
    if not workspace.exists() or not workspace.is_dir():
        report["error"] = "workspace_missing"
        return report
    report["files"].append(normalize_storyboard_file(workspace / SOURCE_REL, "source", write=write))
    report["files"].append(normalize_storyboard_file(workspace / EDIT_REL, "edit", write=write))
    return report


def query_task_workspaces(database_url: str, *, task_id: int = 0, session_id: int = 0) -> list[dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {}
    if task_id:
        clauses.append("t.id = :task_id")
        params["task_id"] = task_id
    if session_id:
        clauses.append("t.session_id = :session_id")
        params["session_id"] = session_id
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sql_text(
                    f"""
                    SELECT t.id AS task_id, t.session_id, s.workspace_dir
                    FROM openclip_tasks t
                    JOIN sessions s ON s.id = t.session_id
                    {where_sql}
                    ORDER BY t.id
                    """
                ),
                params,
            ).mappings().fetchall()
        return [dict(row) for row in rows]
    finally:
        engine.dispose()


def run(args: argparse.Namespace) -> dict[str, Any]:
    workspace_rows: list[dict[str, Any]] = []
    if args.workspace:
        workspace_rows = [
            {"task_id": 0, "session_id": 0, "workspace_dir": str(Path(workspace))}
            for workspace in args.workspace
        ]
    else:
        workspace_rows = query_task_workspaces(args.database_url, task_id=args.task_id, session_id=args.session_id)

    workspaces = [
        inspect_workspace(
            int(row.get("session_id") or 0),
            int(row.get("task_id") or 0),
            Path(str(row.get("workspace_dir") or "")),
            write=bool(args.write),
        )
        for row in workspace_rows
    ]
    changed_files = sum(1 for item in workspaces for file_report in item.get("files", []) if file_report.get("changed"))
    changed_fields = sum(int(file_report.get("changed_fields") or 0) for item in workspaces for file_report in item.get("files", []))
    return {
        "dry_run": not bool(args.write),
        "workspace_count": len(workspaces),
        "changed_files": changed_files,
        "changed_fields": changed_fields,
        "workspaces": workspaces,
    }


def print_summary(report: dict[str, Any]) -> None:
    print(f"dry_run={'yes' if report['dry_run'] else 'no'}")
    print(f"workspace_count={report['workspace_count']}")
    print(f"changed_files={report['changed_files']}")
    print(f"changed_fields={report['changed_fields']}")
    for workspace in report["workspaces"]:
        changed_fields = sum(int(file_report.get("changed_fields") or 0) for file_report in workspace.get("files", []))
        if not changed_fields:
            continue
        print(f"task_id={workspace['task_id']} session_id={workspace['session_id']} workspace={workspace['workspace']} changed_fields={changed_fields}")
        for file_report in workspace.get("files", []):
            if file_report.get("changed_fields"):
                written = " written=yes" if file_report.get("written") else ""
                print(f"  {file_report['kind']} {file_report['path']} changed_fields={file_report['changed_fields']}{written}")


def main() -> None:
    args = parse_args()
    report = run(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
    if args.fail_on_changes and report["changed_fields"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
