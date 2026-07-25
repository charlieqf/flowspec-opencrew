from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.services import media_sanitize  # noqa: E402


DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
TARGET_ROOT_RELS = (
    "SessionOutput/storyboard/assets/images",
    "SessionOutput/storyboard/assets/videos",
    "SessionOutput/storyboard/assets/audios",
    "SessionOutput/storyboard/assets/history",
    "SessionScratch/CleanImageGenerations",
)


@dataclass(frozen=True)
class WorkspaceRef:
    session_id: int
    workspace: Path


@dataclass(frozen=True)
class MediaCandidate:
    session_id: int
    workspace: Path
    rel_path: str
    kind: str
    issue: str = ""

    @property
    def path(self) -> Path:
        return self.workspace / self.rel_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sanitize metadata from existing storyboard media assets. Defaults to dry-run."
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--workspace", action="append", default=[], help="Limit to a workspace path. Can be passed more than once.")
    parser.add_argument("--session-id", type=int, action="append", default=[], help="Limit to a session id. Can be passed more than once.")
    parser.add_argument("--write", action="store_true", help="Apply metadata sanitization. Defaults to dry-run.")
    parser.add_argument("--apply", dest="write", action="store_true", help="Alias for --write.")
    parser.add_argument("--snapshot-dir", default="", help="Copy original files here before --write modifies them.")
    parser.add_argument("--min-age-seconds", type=int, default=0, help="Skip files modified more recently than this many seconds.")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    parser.add_argument("--preview-limit", type=int, default=80, help="Number of candidates to print in non-JSON mode.")
    parser.add_argument("--fail-on-candidates", action="store_true", help="Exit non-zero if dry-run finds candidates.")
    return parser.parse_args()


def _rel(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _is_hidden_path(path: Path, workspace: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(workspace).parts)


def scan_workspace(ref: WorkspaceRef, *, min_age_seconds: int = 0) -> list[MediaCandidate]:
    now = time.time()
    candidates: list[MediaCandidate] = []
    for root_rel in TARGET_ROOT_RELS:
        root = ref.workspace / root_rel
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or _is_hidden_path(path, ref.workspace):
                continue
            kind = media_sanitize.media_kind_for_path(path)
            if not kind:
                continue
            issue = ""
            try:
                stat = path.stat()
            except OSError as exc:
                issue = f"stat failed: {exc}"
            else:
                if stat.st_size <= 0:
                    issue = "empty file"
                elif min_age_seconds > 0 and now - stat.st_mtime < min_age_seconds:
                    continue
            candidates.append(MediaCandidate(session_id=ref.session_id, workspace=ref.workspace, rel_path=_rel(path, ref.workspace), kind=kind, issue=issue))
    return candidates


def workspace_refs_from_db(engine: Engine, session_ids: list[int]) -> list[WorkspaceRef]:
    clauses: list[str] = ["workspace_dir IS NOT NULL", "workspace_dir != ''"]
    params: dict[str, Any] = {}
    if session_ids:
        keys = []
        for index, session_id in enumerate(session_ids):
            key = f"session_id_{index}"
            params[key] = int(session_id)
            keys.append(f":{key}")
        clauses.append(f"id IN ({', '.join(keys)})")
    with engine.begin() as conn:
        rows = conn.execute(
            text(f"SELECT id, workspace_dir FROM sessions WHERE {' AND '.join(clauses)} ORDER BY id"),
            params,
        ).mappings().fetchall()
    return [WorkspaceRef(session_id=int(row["id"]), workspace=Path(str(row["workspace_dir"]))) for row in rows]


def snapshot_candidate(candidate: MediaCandidate, snapshot_dir: Path) -> str:
    session_name = f"session_{candidate.session_id}" if candidate.session_id > 0 else f"workspace_{abs(hash(str(candidate.workspace)))}"
    target = snapshot_dir / session_name / candidate.rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate.path, target)
    return str(target)


def sanitize_candidate(candidate: MediaCandidate, *, write: bool, snapshot_dir: Path | None = None) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "session_id": candidate.session_id,
        "workspace": str(candidate.workspace),
        "path": candidate.rel_path,
        "kind": candidate.kind,
    }
    if candidate.issue:
        operation.update({"status": "blocked", "error": candidate.issue})
        return operation
    if not write:
        operation["status"] = "planned"
        return operation
    try:
        if snapshot_dir is not None:
            operation["snapshot_path"] = snapshot_candidate(candidate, snapshot_dir)
        media_sanitize.sanitize_file_in_place(candidate.path)
    except Exception as exc:
        operation.update({"status": "error", "error": str(exc)[:2000]})
    else:
        operation["status"] = "sanitized"
    return operation


def run_sanitizer(
    refs: list[WorkspaceRef],
    *,
    write: bool = False,
    snapshot_dir: Path | None = None,
    min_age_seconds: int = 0,
) -> dict[str, Any]:
    candidates: list[MediaCandidate] = []
    for ref in refs:
        candidates.extend(scan_workspace(ref, min_age_seconds=min_age_seconds))

    operations = [sanitize_candidate(candidate, write=write, snapshot_dir=snapshot_dir) for candidate in candidates]
    blocked_count = sum(1 for candidate in candidates if candidate.issue)
    error_count = sum(1 for operation in operations if operation.get("status") == "error")
    sanitized_count = sum(1 for operation in operations if operation.get("status") == "sanitized")

    return {
        "ok": blocked_count == 0 and error_count == 0,
        "mode": "write" if write else "dry-run",
        "workspace_count": len(refs),
        "target_roots": list(TARGET_ROOT_RELS),
        "candidate_count": len(candidates),
        "blocked_count": blocked_count,
        "sanitized_count": sanitized_count,
        "error_count": error_count,
        "candidates": [
            {
                "session_id": candidate.session_id,
                "workspace": str(candidate.workspace),
                "path": candidate.rel_path,
                "kind": candidate.kind,
                "issue": candidate.issue,
            }
            for candidate in candidates
        ],
        "operations": operations,
        "generated_at": int(time.time() * 1000),
    }


def print_report(report: dict[str, Any], *, preview_limit: int) -> None:
    print(f"mode={report['mode']}")
    print(f"workspace_count={report['workspace_count']}")
    print(f"candidate_count={report['candidate_count']}")
    print(f"blocked_count={report['blocked_count']}")
    print(f"sanitized_count={report['sanitized_count']}")
    print(f"error_count={report['error_count']}")
    for item in report["candidates"][: max(0, preview_limit)]:
        suffix = f" issue={item['issue']}" if item.get("issue") else ""
        print(f"session_id={item['session_id']} {item['kind']} {item['path']}{suffix}")
    remaining = len(report["candidates"]) - max(0, preview_limit)
    if remaining > 0:
        print(f"... {remaining} more candidates omitted; use --json for the full report")
    if report["mode"] == "dry-run":
        print("dry_run=yes")


def main() -> int:
    args = parse_args()
    engine: Engine | None = None
    refs = [WorkspaceRef(session_id=0, workspace=Path(value).expanduser()) for value in args.workspace]
    try:
        if not refs:
            engine = create_engine(args.database_url, future=True)
            refs = workspace_refs_from_db(engine, [int(value) for value in args.session_id])
        snapshot_dir = Path(args.snapshot_dir).expanduser() if args.snapshot_dir else None
        report = run_sanitizer(
            refs,
            write=bool(args.write),
            snapshot_dir=snapshot_dir,
            min_age_seconds=max(0, int(args.min_age_seconds or 0)),
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_report(report, preview_limit=int(args.preview_limit or 0))
        if args.fail_on_candidates and not args.write and int(report["candidate_count"] or 0) > 0:
            return 1
        if int(report["blocked_count"] or 0) > 0 or int(report["error_count"] or 0) > 0:
            return 2
        return 0
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
