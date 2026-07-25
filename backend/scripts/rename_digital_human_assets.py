from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
ASSET_VIDEOS_REL = "SessionOutput/storyboard/assets/videos"
DIGITAL_HUMAN_VIDEO_ROOT_RELS = (
    ASSET_VIDEOS_REL,
    "SessionOutput/storyboard/assets/history",
)
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}

FILENAME_REPLACEMENTS = (
    ("_heygen_video_agent_", "_digital_human_agent_"),
    ("_heygen_digital_human_", "_digital_human_"),
)

JSON_STRING_REPLACEMENTS = (
    ("heygen_video_agent", "digital_human_agent"),
    ("heygen_digital_human", "digital_human"),
    ("HeyGen Video Agent video", "Digital human agent video"),
    ("HeyGen digital human video", "Digital human video"),
)


@dataclass(frozen=True)
class WorkspaceRef:
    session_id: int
    workspace: Path


@dataclass(frozen=True)
class RenameCandidate:
    session_id: int
    workspace: Path
    old_rel: str
    new_rel: str
    old_sidecar_rel: str
    new_sidecar_rel: str
    issue: str = ""

    @property
    def old_path(self) -> Path:
        return self.workspace / self.old_rel

    @property
    def new_path(self) -> Path:
        return self.workspace / self.new_rel

    @property
    def old_sidecar_path(self) -> Path:
        return self.workspace / self.old_sidecar_rel

    @property
    def new_sidecar_path(self) -> Path:
        return self.workspace / self.new_sidecar_rel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rename legacy HeyGen digital-human asset files to neutral names and update workspace/DB references."
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--workspace", action="append", default=[], help="Limit to a workspace path. Can be passed more than once.")
    parser.add_argument("--session-id", type=int, action="append", default=[], help="Limit to a session id. Can be passed more than once.")
    parser.add_argument("--write", action="store_true", help="Apply changes. Defaults to dry-run.")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    parser.add_argument("--fail-on-candidates", action="store_true", help="Exit non-zero if dry-run finds candidates.")
    return parser.parse_args()


def neutral_video_name(name: str) -> str:
    result = name
    for old, new in FILENAME_REPLACEMENTS:
        result = result.replace(old, new)
    return result


def _rel(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix()


def scan_workspace(ref: WorkspaceRef) -> list[RenameCandidate]:
    candidates: list[RenameCandidate] = []
    for root_rel in DIGITAL_HUMAN_VIDEO_ROOT_RELS:
        video_root = ref.workspace / root_rel
        if not video_root.exists() or not video_root.is_dir():
            continue
        for path in sorted(video_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTS:
                continue
            new_name = neutral_video_name(path.name)
            if new_name == path.name or "heygen" not in path.name.lower():
                continue
            new_path = path.with_name(new_name)
            old_sidecar = path.with_suffix(".json")
            new_sidecar = new_path.with_suffix(".json")
            issue = ""
            if "heygen" in new_name.lower():
                issue = "neutral name still contains heygen"
            elif new_path.exists():
                issue = f"target file already exists: {_rel(new_path, ref.workspace)}"
            elif old_sidecar.exists() and new_sidecar.exists():
                issue = f"target sidecar already exists: {_rel(new_sidecar, ref.workspace)}"
            candidates.append(
                RenameCandidate(
                    session_id=ref.session_id,
                    workspace=ref.workspace,
                    old_rel=_rel(path, ref.workspace),
                    new_rel=_rel(new_path, ref.workspace),
                    old_sidecar_rel=_rel(old_sidecar, ref.workspace),
                    new_sidecar_rel=_rel(new_sidecar, ref.workspace),
                    issue=issue,
                )
            )
    return candidates


def replacement_pairs(candidate: RenameCandidate) -> list[tuple[str, str]]:
    old_path = str(candidate.old_path)
    new_path = str(candidate.new_path)
    old_sidecar_path = str(candidate.old_sidecar_path)
    new_sidecar_path = str(candidate.new_sidecar_path)
    pairs = [
        (candidate.old_rel, candidate.new_rel),
        (Path(candidate.old_rel).name, Path(candidate.new_rel).name),
        (old_path, new_path),
        (candidate.old_sidecar_rel, candidate.new_sidecar_rel),
        (Path(candidate.old_sidecar_rel).name, Path(candidate.new_sidecar_rel).name),
        (old_sidecar_path, new_sidecar_path),
        *JSON_STRING_REPLACEMENTS,
    ]
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for old, new in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        if old and old != new and (old, new) not in seen:
            seen.add((old, new))
            result.append((old, new))
    return result


def dedupe_pairs(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for old, new in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        if old and old != new and (old, new) not in seen:
            seen.add((old, new))
            result.append((old, new))
    return result


def replace_json_value(value: Any, pairs: list[tuple[str, str]]) -> tuple[Any, int]:
    if isinstance(value, dict):
        changed = 0
        result: dict[str, Any] = {}
        for key, item in value.items():
            next_item, item_changed = replace_json_value(item, pairs)
            result[str(key)] = next_item
            changed += item_changed
        return result, changed
    if isinstance(value, list):
        changed = 0
        result = []
        for item in value:
            next_item, item_changed = replace_json_value(item, pairs)
            result.append(next_item)
            changed += item_changed
        return result, changed
    if isinstance(value, str):
        result = value
        changed = 0
        for old, new in pairs:
            count = result.count(old)
            if count:
                result = result.replace(old, new)
                changed += count
        return result, changed
    return value, 0


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def rewrite_workspace_json(workspace: Path, pairs: list[tuple[str, str]], *, write: bool) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for path in sorted(workspace.rglob("*.json")):
        if any(part.startswith(".") for part in path.relative_to(workspace).parts):
            continue
        try:
            payload = read_json(path)
        except Exception:
            continue
        next_payload, replacements = replace_json_value(payload, pairs)
        if replacements <= 0:
            continue
        rel = _rel(path, workspace)
        updates.append({"path": rel, "replacement_count": replacements})
        if write:
            write_json_atomic(path, next_payload)
    return updates


def rename_candidate_files(candidate: RenameCandidate, *, write: bool) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if candidate.issue:
        return operations
    if candidate.old_sidecar_path.exists():
        operations.append({"from": candidate.old_sidecar_rel, "to": candidate.new_sidecar_rel, "kind": "sidecar"})
        if write:
            candidate.old_sidecar_path.rename(candidate.new_sidecar_path)
    operations.append({"from": candidate.old_rel, "to": candidate.new_rel, "kind": "video"})
    if write:
        candidate.old_path.rename(candidate.new_path)
    return operations


def update_session_file_rows(conn: Any, candidates: Iterable[RenameCandidate]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.issue:
            continue
        path_pairs = [
            (candidate.old_rel, candidate.new_rel),
            (candidate.old_sidecar_rel, candidate.new_sidecar_rel),
        ]
        for old_rel, new_rel in path_pairs:
            if candidate.session_id > 0:
                result = conn.execute(
                    text("UPDATE session_files SET path = :new_path WHERE session_id = :session_id AND path = :old_path"),
                    {"session_id": candidate.session_id, "old_path": old_rel, "new_path": new_rel},
                )
            else:
                result = conn.execute(
                    text("UPDATE session_files SET path = :new_path WHERE path = :old_path"),
                    {"old_path": old_rel, "new_path": new_rel},
                )
            if int(result.rowcount or 0):
                updates.append({"session_id": candidate.session_id, "from": old_rel, "to": new_rel, "rowcount": int(result.rowcount or 0)})
    return updates


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


def run_migration(
    refs: list[WorkspaceRef],
    *,
    write: bool = False,
    engine: Engine | None = None,
) -> dict[str, Any]:
    all_candidates: list[RenameCandidate] = []
    for ref in refs:
        all_candidates.extend(scan_workspace(ref))

    applied_candidates = [candidate for candidate in all_candidates if not candidate.issue]
    json_updates: list[dict[str, Any]] = []
    file_operations: list[dict[str, Any]] = []

    candidates_by_workspace: dict[tuple[int, str], list[RenameCandidate]] = {}
    for candidate in applied_candidates:
        key = (candidate.session_id, str(candidate.workspace))
        candidates_by_workspace.setdefault(key, []).append(candidate)

    for (_session_id, _workspace), candidates in candidates_by_workspace.items():
        pairs = dedupe_pairs(pair for candidate in candidates for pair in replacement_pairs(candidate))
        workspace = candidates[0].workspace
        json_updates.extend(
            {
                "session_id": candidates[0].session_id,
                "workspace": str(workspace),
                "candidate_count": len(candidates),
                **item,
            }
            for item in rewrite_workspace_json(workspace, pairs, write=write)
        )

    for candidate in applied_candidates:
        file_operations.extend({"session_id": candidate.session_id, **item} for item in rename_candidate_files(candidate, write=write))

    db_updates: list[dict[str, Any]] = []
    if write and engine is not None:
        with engine.begin() as conn:
            db_updates = update_session_file_rows(conn, applied_candidates)

    return {
        "ok": True,
        "mode": "write" if write else "dry-run",
        "workspace_count": len(refs),
        "candidate_count": len(all_candidates),
        "blocked_count": sum(1 for candidate in all_candidates if candidate.issue),
        "applied_count": len(applied_candidates) if write else 0,
        "candidates": [
            {
                "session_id": candidate.session_id,
                "workspace": str(candidate.workspace),
                "old_path": candidate.old_rel,
                "new_path": candidate.new_rel,
                "old_sidecar_path": candidate.old_sidecar_rel,
                "new_sidecar_path": candidate.new_sidecar_rel,
                "issue": candidate.issue,
            }
            for candidate in all_candidates
        ],
        "json_updates": json_updates,
        "file_operations": file_operations,
        "db_updates": db_updates,
        "generated_at": int(time.time() * 1000),
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"mode={report['mode']}")
    print(f"workspace_count={report['workspace_count']}")
    print(f"candidate_count={report['candidate_count']}")
    print(f"blocked_count={report['blocked_count']}")
    print(f"applied_count={report['applied_count']}")
    for item in report["candidates"]:
        suffix = f" issue={item['issue']}" if item.get("issue") else ""
        print(f"session_id={item['session_id']} {item['old_path']} -> {item['new_path']}{suffix}")
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
        report = run_migration(refs, write=bool(args.write), engine=engine)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_report(report)
        if args.fail_on_candidates and not args.write and int(report["candidate_count"] or 0) > 0:
            return 1
        if int(report["blocked_count"] or 0) > 0:
            return 2
        return 0
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
