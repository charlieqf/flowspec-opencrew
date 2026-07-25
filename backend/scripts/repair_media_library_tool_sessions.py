from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.repositories.sessions import SessionRepository  # noqa: E402
from opcrew_backend.services.session_files import SessionFileService  # noqa: E402
from opcrew_backend.tool_sessions import ToolSessionResultSync, ToolSessionRunner  # noqa: E402


DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
MEDIA_SUFFIXES = {
    ".aac",
    ".avi",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair terminal summaries, result indexes, session_files rows, and duplicate "
            "legacy-context media for existing media-library Tool Sessions. Defaults to dry-run."
        )
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--session-id", type=int, default=0, help="Limit repair to one OpenCrew session.")
    parser.add_argument("--write", action="store_true", help="Apply repairs. Defaults to dry-run.")
    parser.add_argument("--json", action="store_true", help="Print the complete report as JSON.")
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def terminal_status_for(*, business_status: str, summary: dict[str, Any]) -> str:
    step_statuses = {
        str(item.get("status") or "")
        for item in summary.get("steps") or []
        if isinstance(item, dict)
    }
    if "blocked" in step_statuses:
        return "blocked"
    if business_status == "ready":
        return "completed"
    return "failed"


def business_status_for_terminal(terminal_status: str) -> str:
    if terminal_status == "completed":
        return "ready"
    if terminal_status == "blocked":
        return "blocked"
    return "failed"


def update_business_status(
    engine: Engine,
    *,
    task_id: int,
    scheme: str,
    business_status: str,
) -> None:
    if scheme not in {"dialogue", "visual"}:
        raise ValueError("unsupported_scheme")
    with engine.begin() as conn:
        task_columns = {
            str(column["name"])
            for column in inspect(conn).get_columns("media_library_tasks")
        }
        assignments = [f"{scheme}_status = :business_status"]
        if scheme == "visual" and "visual_structure_status" in task_columns:
            assignments.append("visual_structure_status = :business_status")
            if business_status == "ready" and "visual_semantic_status" in task_columns:
                assignments[0] = "visual_status = 'partial'"
        conn.execute(
            text(
                f"""
UPDATE media_library_tasks
SET {', '.join(assignments)}
WHERE id = :task_id
"""
            ),
            {"task_id": task_id, "business_status": business_status},
        )
        if business_status == "blocked":
            conn.execute(
                text(
                    """
UPDATE media_library_assets
SET analysis_status = 'blocked'
WHERE asset_id = (
  SELECT asset_id FROM media_library_tasks WHERE id = :task_id
)
"""
                ),
                {"task_id": task_id},
            )


def relink_legacy_context_media(root: Path, *, write: bool) -> list[dict[str, Any]]:
    source_root = root / "0_SessionContext"
    legacy_root = root / "SessionContext"
    if not source_root.is_dir() or not legacy_root.is_dir():
        return []
    candidates: list[dict[str, Any]] = []
    for source in sorted(source_root.iterdir()):
        target = legacy_root / source.name
        if source.suffix.lower() not in MEDIA_SUFFIXES or not source.is_file() or not target.is_file():
            continue
        try:
            if source.samefile(target):
                continue
        except OSError:
            continue
        if source.stat().st_size != target.stat().st_size or _sha256(source) != _sha256(target):
            candidates.append({"path": target.name, "status": "content_mismatch"})
            continue
        row = {"path": target.name, "status": "would_relink" if not write else "relinked", "bytes": source.stat().st_size}
        if write:
            temporary = target.with_name(f".{target.name}.relink-{os.getpid()}")
            temporary.unlink(missing_ok=True)
            try:
                os.link(source, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        candidates.append(row)
    return candidates


def find_runs(engine: Engine, *, session_id: int = 0) -> list[dict[str, Any]]:
    where = "WHERE task.session_id = :session_id" if session_id else ""
    query = text(
        f"""
        SELECT
          task.id AS task_id,
          task.session_id,
          session.workspace_dir,
          task.dialogue_status,
          task.dialogue_tool_use_session_id,
          task.visual_status,
          task.visual_tool_use_session_id
        FROM media_library_tasks AS task
        JOIN sessions AS session ON session.id = task.session_id
        {where}
        ORDER BY task.session_id, task.id
        """
    )
    params = {"session_id": session_id} if session_id else {}
    with engine.connect() as conn:
        tasks = [dict(row) for row in conn.execute(query, params).mappings().fetchall()]
    runs: list[dict[str, Any]] = []
    for task in tasks:
        for scheme in ("dialogue", "visual"):
            business_status = str(task.get(f"{scheme}_status") or "")
            tool_use_session_id = str(task.get(f"{scheme}_tool_use_session_id") or "")
            if business_status not in {"ready", "failed"} or not tool_use_session_id:
                continue
            root = Path(str(task["workspace_dir"])) / "tool_use_sessions" / tool_use_session_id
            summary_path = root / "SessionReport" / "SessionRunSummary.json"
            if not summary_path.is_file():
                runs.append(
                    {
                        "task_id": int(task["task_id"]),
                        "session_id": int(task["session_id"]),
                        "scheme": scheme,
                        "business_status": business_status,
                        "tool_use_session_id": tool_use_session_id,
                        "root": str(root),
                        "repairable": False,
                        "issue": "summary_missing",
                    }
                )
                continue
            summary = _read_json(summary_path)
            terminal_status = terminal_status_for(business_status=business_status, summary=summary)
            runs.append(
                {
                    "task_id": int(task["task_id"]),
                    "session_id": int(task["session_id"]),
                    "scheme": scheme,
                    "business_status": business_status,
                    "tool_use_session_id": tool_use_session_id,
                    "root": str(root),
                    "repairable": True,
                    "current_summary_status": str(summary.get("status") or ""),
                    "terminal_status": terminal_status,
                    "result_index_exists": (root / "SessionOutput" / "manifests" / "result_index.json").is_file(),
                }
            )
    return runs


def run_repair(engine: Engine, *, session_id: int = 0, write: bool = False) -> dict[str, Any]:
    runs = find_runs(engine, session_id=session_id)
    session_repo = SessionRepository(engine)
    syncer = ToolSessionResultSync(
        session_repo=session_repo,
        engine=engine,
        file_service=SessionFileService(),
    )
    repaired = 0
    failed = 0
    for row in runs:
        root = Path(str(row["root"]))
        row["legacy_media"] = relink_legacy_context_media(root, write=write)
        if not row["repairable"]:
            failed += 1
            continue
        needs_finalize = (
            row["current_summary_status"] != row["terminal_status"]
            or not row["result_index_exists"]
        )
        row["needs_finalize"] = needs_finalize
        target_business_status = business_status_for_terminal(
            str(row["terminal_status"])
        )
        row["target_business_status"] = target_business_status
        row["needs_business_status_update"] = (
            str(row["business_status"]) != target_business_status
        )
        if not write:
            continue
        runner = ToolSessionRunner.from_summary(
            workspace_dir=root.parents[1],
            tool_use_session_id=str(row["tool_use_session_id"]),
            session_id=int(row["session_id"]),
        )
        result = runner.finalize_session(
            result_syncer=syncer,
            terminal_status=str(row["terminal_status"]),
        )
        row["sync_status"] = result.status
        row["sync_errors"] = list(result.errors)
        if result.status == "completed":
            if row["needs_business_status_update"]:
                update_business_status(
                    engine,
                    task_id=int(row["task_id"]),
                    scheme=str(row["scheme"]),
                    business_status=target_business_status,
                )
                row["business_status_updated"] = True
            repaired += 1
        else:
            failed += 1
    return {
        "dry_run": not write,
        "session_id": session_id or None,
        "run_count": len(runs),
        "repaired_count": repaired,
        "failed_count": failed,
        "runs": runs,
    }


def main() -> None:
    args = parse_args()
    engine = create_engine(args.database_url, future=True)
    try:
        report = run_repair(engine, session_id=int(args.session_id or 0), write=bool(args.write))
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(
        f"dry_run={str(report['dry_run']).lower()} run_count={report['run_count']} "
        f"repaired_count={report['repaired_count']} failed_count={report['failed_count']}"
    )
    for row in report["runs"]:
        print(
            f"session_id={row['session_id']} scheme={row['scheme']} "
            f"tool_use_session_id={row['tool_use_session_id']} "
            f"current={row.get('current_summary_status', row.get('issue', ''))} "
            f"target={row.get('terminal_status', '')} "
            f"legacy_media={len(row.get('legacy_media') or [])}"
        )


if __name__ == "__main__":
    main()
