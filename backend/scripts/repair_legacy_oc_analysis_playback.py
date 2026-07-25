from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text


DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
MANIFEST_PATTERNS = (
    "schemes/*/manifest.json",
    "reports/17_*_export_manifest.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and repair legacy OC-Analysis virtual playback that points outside the session workspace."
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--session-id", type=int, default=0, help="Limit to one session id.")
    parser.add_argument("--task-id", type=int, default=0, help="Limit to one OpenClip task id.")
    parser.add_argument("--write", action="store_true", help="Apply repairs. Defaults to dry-run.")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    parser.add_argument("--fail-on-issues", action="store_true", help="Exit non-zero if unrepaired issues remain.")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def clean_value(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip()


def path_from_value(value: Any, workspace: Path) -> Path | None:
    raw = clean_value(value)
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else workspace / path


def served_reference_issue(value: Any, workspace: Path) -> str:
    raw = clean_value(value)
    if not raw:
        return "empty"
    path = Path(raw).expanduser()
    if path.is_absolute():
        return "" if is_inside(path, workspace) else "absolute_outside_workspace"
    if ".." in path.parts:
        return "relative_escape"
    return ""


def source_video_state(workspace: Path) -> dict[str, Any]:
    target = workspace / "source_video.mp4"
    if target.is_symlink():
        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError:
            return {"valid": False, "issue": "source_video_broken_symlink", "path": str(target), "resolved": ""}
        if not is_inside(resolved, workspace):
            return {"valid": False, "issue": "source_video_symlink_escape", "path": str(target), "resolved": str(resolved)}
    if not target.exists():
        return {"valid": False, "issue": "source_video_missing", "path": str(target), "resolved": ""}
    if not target.is_file():
        return {"valid": False, "issue": "source_video_not_file", "path": str(target), "resolved": str(target.resolve())}
    if target.stat().st_size <= 0:
        return {"valid": False, "issue": "source_video_empty", "path": str(target), "resolved": str(target.resolve())}
    return {"valid": True, "issue": "", "path": str(target), "resolved": str(target.resolve()), "size": int(target.stat().st_size)}


def manifest_is_virtual(payload: dict[str, Any]) -> bool:
    if str(payload.get("clip_mode") or "").lower() == "virtual":
        return True
    items = payload.get("items")
    if not isinstance(items, list):
        return False
    return any(isinstance(item, dict) and str(item.get("clip_status") or "").lower() == "virtual" for item in items)


def item_is_virtual(item: dict[str, Any], manifest_virtual: bool) -> bool:
    return manifest_virtual or str(item.get("clip_status") or "").lower() == "virtual"


def find_manifest_records(workspace: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for pattern in MANIFEST_PATTERNS:
        for path in sorted(workspace.glob(pattern)):
            try:
                payload = read_json(path)
            except Exception as exc:
                records.append({"path": path, "payload": None, "virtual": False, "error": str(exc)})
                continue
            records.append({"path": path, "payload": payload, "virtual": isinstance(payload, dict) and manifest_is_virtual(payload), "error": ""})
    return records


def collect_values_from_json(path: Path, keys: tuple[str, ...]) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = read_json(path)
    except Exception:
        return []
    values: list[str] = []
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        staged = payload.get("staged_video")
        if isinstance(staged, dict):
            for key in ("source", "target"):
                value = staged.get(key)
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
    return values


def collect_candidate_sources(workspace: Path, reference_video_path: str, manifest_records: list[dict[str, Any]]) -> list[Path]:
    values: list[str] = []
    if reference_video_path.strip():
        values.append(reference_video_path.strip())
    values.extend(collect_values_from_json(workspace / "input" / "project_input.json", ("reference_video_path",)))
    values.extend(collect_values_from_json(workspace / "meta" / "run_manifest.json", ("reference_video_path",)))
    values.extend(collect_values_from_json(workspace / "meta" / "video_metadata.json", ("workspace_source_video_path", "path", "source_video_path")))

    source_video = workspace / "source_video.mp4"
    if source_video.is_symlink():
        try:
            values.append(str(source_video.resolve(strict=True)))
        except FileNotFoundError:
            pass

    for record in manifest_records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        for key in ("source_video_path", "video_path"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        items = payload.get("items")
        if not isinstance(items, list):
            continue
        manifest_virtual = bool(record.get("virtual"))
        for item in items:
            if not isinstance(item, dict) or not item_is_virtual(item, manifest_virtual):
                continue
            for key in ("source_video_path", "clip_path"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())

    candidates: list[Path] = []
    seen: set[str] = set()
    for value in values:
        path = path_from_value(value, workspace)
        if not path:
            continue
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            continue
        key = str(resolved)
        if key in seen or not resolved.is_file() or resolved.stat().st_size <= 0:
            continue
        seen.add(key)
        candidates.append(resolved)
    return candidates


def rewrite_virtual_manifest(payload: dict[str, Any]) -> bool:
    changed = False
    manifest_virtual = manifest_is_virtual(payload)
    if not manifest_virtual:
        return False
    if payload.get("source_video_path") != "source_video.mp4":
        payload["source_video_path"] = "source_video.mp4"
        changed = True
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict) or not item_is_virtual(item, manifest_virtual):
                continue
            if item.get("clip_path") != "source_video.mp4":
                item["clip_path"] = "source_video.mp4"
                changed = True
            if item.get("source_video_path") != "source_video.mp4":
                item["source_video_path"] = "source_video.mp4"
                changed = True
    return changed


def inspect_workspace(
    session_id: int,
    task_id: int,
    workspace: Path,
    reference_video_path: str = "",
    *,
    write: bool = False,
) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    report: dict[str, Any] = {
        "session_id": session_id,
        "task_id": task_id,
        "workspace": str(workspace),
        "status": "ok",
        "issues": [],
        "actions": [],
        "manifests": [],
    }
    if not workspace.exists() or not workspace.is_dir():
        report["status"] = "broken"
        report["issues"].append({"code": "workspace_missing", "path": str(workspace)})
        return report

    manifest_records = find_manifest_records(workspace)
    virtual_manifests = [record for record in manifest_records if record.get("virtual")]
    needs_source_video = bool(virtual_manifests)

    for record in manifest_records:
        manifest_report = {"path": str(record["path"]), "virtual": bool(record.get("virtual")), "issues": []}
        payload = record.get("payload")
        if record.get("error"):
            manifest_report["issues"].append({"code": "manifest_unreadable", "error": str(record["error"])})
        elif isinstance(payload, dict) and record.get("virtual"):
            for key in ("source_video_path",):
                issue = served_reference_issue(payload.get(key), workspace)
                if issue:
                    manifest_report["issues"].append({"code": issue, "field": key, "value": clean_value(payload.get(key))})
            items = payload.get("items")
            if isinstance(items, list):
                for index, item in enumerate(items):
                    if not isinstance(item, dict) or not item_is_virtual(item, True):
                        continue
                    for key in ("clip_path", "source_video_path"):
                        issue = served_reference_issue(item.get(key), workspace)
                        if issue:
                            manifest_report["issues"].append({"code": issue, "field": f"items[{index}].{key}", "value": clean_value(item.get(key))})
        if manifest_report["issues"]:
            report["issues"].append({"code": "manifest_playback_reference", **manifest_report})
        report["manifests"].append(manifest_report)

    source_state = source_video_state(workspace)
    if needs_source_video and not source_state["valid"]:
        report["issues"].append({"code": source_state["issue"], "path": source_state["path"], "resolved": source_state.get("resolved") or ""})

    candidates = collect_candidate_sources(workspace, reference_video_path, manifest_records)
    target = workspace / "source_video.mp4"
    if needs_source_video and not source_state["valid"]:
        source = candidates[0] if candidates else None
        if source is None:
            report["issues"].append({"code": "source_video_repair_source_missing"})
        elif write:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = target.with_suffix(target.suffix + ".repair-tmp")
            if target.exists() or target.is_symlink():
                target.unlink()
            shutil.copy2(source, tmp_path)
            tmp_path.replace(target)
            report["actions"].append({"code": "copied_source_video", "source": str(source), "target": str(target)})
            source_state = source_video_state(workspace)
        else:
            report["actions"].append({"code": "would_copy_source_video", "source": str(source), "target": str(target)})

    source_ready = bool(source_state.get("valid")) or any(action["code"] in {"copied_source_video", "would_copy_source_video"} for action in report["actions"])
    for record in manifest_records:
        payload = record.get("payload")
        if not isinstance(payload, dict) or not record.get("virtual"):
            continue
        candidate_payload = json.loads(json.dumps(payload))
        if not rewrite_virtual_manifest(candidate_payload):
            continue
        if not source_ready:
            report["issues"].append({"code": "manifest_needs_rewrite_but_source_video_unavailable", "path": str(record["path"])})
            continue
        if write:
            write_json_atomic(record["path"], candidate_payload)
            report["actions"].append({"code": "rewrote_virtual_manifest", "path": str(record["path"])})
        else:
            report["actions"].append({"code": "would_rewrite_virtual_manifest", "path": str(record["path"])})

    unrepaired = [issue for issue in report["issues"] if issue.get("code") in {"workspace_missing", "source_video_repair_source_missing", "manifest_needs_rewrite_but_source_video_unavailable"}]
    if unrepaired:
        report["status"] = "broken"
    elif report["actions"]:
        report["status"] = "repaired" if write else "repairable"
    elif report["issues"]:
        report["status"] = "needs_review"
    return report


def load_openclip_rows(database_url: str, session_id: int, task_id: int) -> list[dict[str, Any]]:
    engine = create_engine(database_url, future=True)
    try:
        where = []
        params: dict[str, Any] = {}
        if session_id:
            where.append("t.session_id = :session_id")
            params["session_id"] = session_id
        if task_id:
            where.append("t.id = :task_id")
            params["task_id"] = task_id
        suffix = "WHERE " + " AND ".join(where) if where else ""
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT t.id AS task_id, t.session_id, t.reference_video_path, s.workspace_dir
                    FROM openclip_tasks t
                    JOIN sessions s ON s.id = t.session_id
                    {suffix}
                    ORDER BY t.id
                    """
                ),
                params,
            ).mappings().fetchall()
        return [dict(row) for row in rows]
    finally:
        engine.dispose()


def upsert_source_video_file(database_url: str, reports: list[dict[str, Any]]) -> None:
    engine = create_engine(database_url, future=True)
    try:
        with engine.begin() as conn:
            for report in reports:
                if report.get("status") not in {"repaired", "ok", "needs_review"}:
                    continue
                source_video = Path(str(report["workspace"])) / "source_video.mp4"
                if not source_video.exists() or not source_video.is_file():
                    continue
                updated_at = int(source_video.stat().st_mtime * 1000)
                conn.execute(
                    text(
                        """
                        INSERT INTO session_files (
                            session_id, path, kind, size, origin, downloadable,
                            visibility, sensitivity, stale, updated_at
                        )
                        VALUES (
                            :session_id, 'source_video.mp4', 'video', :size, 'legacy-repair', 1,
                            'public', 'normal', 0, :updated_at
                        )
                        ON CONFLICT (session_id, path) DO UPDATE SET
                            kind = EXCLUDED.kind,
                            size = EXCLUDED.size,
                            origin = EXCLUDED.origin,
                            downloadable = EXCLUDED.downloadable,
                            visibility = EXCLUDED.visibility,
                            sensitivity = EXCLUDED.sensitivity,
                            stale = 0,
                            updated_at = EXCLUDED.updated_at
                        """
                    ),
                    {"session_id": int(report["session_id"]), "size": int(source_video.stat().st_size), "updated_at": updated_at},
                )
    finally:
        engine.dispose()


def summarize(reports: list[dict[str, Any]], *, write: bool) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for report in reports:
        status = str(report.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "mode": "write" if write else "dry-run",
        "checked": len(reports),
        "counts": counts,
        "generated_at": int(time.time() * 1000),
    }


def main() -> None:
    args = parse_args()
    rows = load_openclip_rows(args.database_url, args.session_id, args.task_id)
    reports = [
        inspect_workspace(
            int(row["session_id"]),
            int(row["task_id"]),
            Path(str(row.get("workspace_dir") or "")),
            str(row.get("reference_video_path") or ""),
            write=bool(args.write),
        )
        for row in rows
    ]
    if args.write:
        upsert_source_video_file(args.database_url, reports)
    summary = summarize(reports, write=bool(args.write))
    payload = {"summary": summary, "reports": reports}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"mode={summary['mode']} checked={summary['checked']} counts={summary['counts']}")
        for report in reports:
            if report.get("status") == "ok":
                continue
            print(
                f"task_id={report['task_id']} session_id={report['session_id']} "
                f"status={report['status']} issues={len(report['issues'])} actions={len(report['actions'])}"
            )
            for issue in report["issues"]:
                print(f"  issue={issue.get('code')} path={issue.get('path') or issue.get('value') or ''}")
            for action in report["actions"]:
                print(f"  action={action.get('code')} path={action.get('path') or action.get('target') or ''}")
    if args.fail_on_issues and any(report.get("status") in {"broken", "needs_review", "repairable"} for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
