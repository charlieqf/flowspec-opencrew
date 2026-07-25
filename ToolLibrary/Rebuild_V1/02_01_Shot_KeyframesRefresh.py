from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any


TOOL_ID = "02_01_Shot_KeyframesRefresh"
TOOL_NAME = TOOL_ID
TOOL_VERSION = "1.0.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
REQUIRES = ["rebuild_shot_plan.json", "source_package.json", "analysis_keyframes", "shot_id"]
PRODUCES = ["rebuild_shot_plan.json", f"reports/rebuild_v1/{TOOL_ID}.json"]
SUGGESTED_PREVIOUS_TOOLS = ["01_Rebuild_SourcePackageLoad", "02_Rebuild_ShotPlanBuilder"]
SUGGESTED_NEXT_TOOLS = ["03_04_ShotPlan_PreDeleteReadinessCheck", "04_01_ShotPlan_FirstLastFrameMark"]


TIME_RE = re.compile(r"_t([0-9]+(?:\.[0-9]+)?)\.(?:jpg|jpeg|png|webp)$", re.I)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def postgres_connect(database_url: str) -> Any:
    try:
        import psycopg  # type: ignore

        conn = psycopg.connect(normalize_database_url(database_url))
        conn.execute("SET client_encoding TO 'UTF8'")
        return conn
    except Exception:
        try:
            import psycopg2  # type: ignore
        except Exception as exc:
            raise RuntimeError("PostgreSQL driver is not available. Install psycopg[binary] or psycopg2-binary in the OpenCrew runtime.") from exc
        conn = psycopg2.connect(normalize_database_url(database_url))
        conn.set_client_encoding("UTF8")
        return conn


def fetch_task_context(database_url: str, task_id: int) -> dict[str, Any]:
    if not task_id:
        return {}
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
SELECT t.id, t.session_id, t.analysis_task_id, t.source_package_path,
       s.workspace_dir, analysis_s.workspace_dir AS analysis_workspace_dir
FROM oc_rebuild_tasks t
JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_tasks a ON a.id = t.analysis_task_id
LEFT JOIN sessions analysis_s ON analysis_s.id = a.session_id
WHERE t.id = %s
LIMIT 1
""",
                (task_id,),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
        if not row:
            raise RuntimeError(f"OC-Rebuild Task #{task_id} not found")
        data = dict(zip(columns, row))
        return {
            "task_id": int(data.get("id") or 0),
            "session_id": int(data.get("session_id") or 0),
            "analysis_task_id": int(data.get("analysis_task_id") or 0) or None,
            "workspace_dir": decode_text(data.get("workspace_dir")).strip(),
            "analysis_workspace_dir": decode_text(data.get("analysis_workspace_dir")).strip(),
            "source_package_path": decode_text(data.get("source_package_path")).strip() or "source_package.json",
        }
    finally:
        conn.close()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def source_package_path(context: dict[str, Any], args: argparse.Namespace) -> str:
    return str(args.source_package or context.get("source_package_path") or "source_package.json")


def resolve_workspace(context: dict[str, Any], args: argparse.Namespace) -> Path:
    if args.workspace:
        return args.workspace.expanduser().resolve()
    workspace_value = str(context.get("workspace_dir") or "").strip()
    if not workspace_value:
        raise RuntimeError("Either --workspace or a DB-resolvable --task-id is required")
    return Path(workspace_value).expanduser().resolve()


def analysis_workspace(source_package: dict[str, Any], context: dict[str, Any]) -> Path | None:
    source = source_package.get("source") if isinstance(source_package.get("source"), dict) else {}
    for value in (context.get("analysis_workspace_dir"), source.get("analysis_workspace")):
        text = str(value or "").strip()
        if text:
            return Path(text).expanduser().resolve()
    return None


def frame_time(frame: dict[str, Any]) -> float:
    for key in ("time", "timestamp", "start"):
        if key in frame:
            return safe_float(frame.get(key), -1.0)
    match = TIME_RE.search(str(frame.get("path") or ""))
    return safe_float(match.group(1), -1.0) if match else -1.0


def rel_to(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def candidate_priority(source: str, path: str) -> int:
    if source == "existing":
        return 100
    if source == "pyscenedetect" or path.startswith("keyframes/pyscenedetect_scenes/"):
        return 90
    if source == "pyscenedetect_cut" or "pyscenedetect_cut_" in path:
        return 75
    if source == "source_package":
        return 70
    return 50


def normalize_candidate(frame: dict[str, Any], source: str, analysis_root: Path | None = None) -> dict[str, Any] | None:
    raw_path = str(frame.get("path") or frame.get("image_path") or frame.get("frame_path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    path_value = raw_path
    if path.is_absolute() and analysis_root:
        path_value = rel_to(path, analysis_root)
    time_value = frame_time({**frame, "path": path_value})
    if time_value < 0:
        return None
    normalized_source = source
    if source != "existing" and path_value.startswith("keyframes/pyscenedetect_scenes/"):
        normalized_source = "pyscenedetect"
    elif source != "existing" and "pyscenedetect_cut_" in path_value:
        normalized_source = "pyscenedetect_cut"
    elif source == "scan":
        normalized_source = "visual_candidate"
    return {
        "time": time_value,
        "path": path_value,
        "source": normalized_source,
        "resource_session": "analysis",
        "selection_priority": candidate_priority(normalized_source, path_value),
    }


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (plan.get("shots") or []) if isinstance(item, dict)]


def find_shot(plan: dict[str, Any], shot_id: str) -> dict[str, Any]:
    for shot in shot_list(plan):
        if str(shot.get("shot_id") or "") == shot_id:
            return shot
    raise RuntimeError(f"Shot not found in rebuild_shot_plan.json: {shot_id}")


def matching_source_segment(source_package: dict[str, Any], shot: dict[str, Any]) -> dict[str, Any]:
    segments = [item for item in (source_package.get("segments") or []) if isinstance(item, dict)]
    source_segment_id = str(shot.get("source_segment_id") or "")
    source_index = int(safe_float(shot.get("source_index"), 0))
    for segment in segments:
        if source_segment_id and str(segment.get("segment_id") or "") == source_segment_id:
            return segment
    for segment in segments:
        if source_index and int(safe_float(segment.get("index"), 0)) == source_index:
            return segment
    shot_id = str(shot.get("shot_id") or "")
    match = re.search(r"(\d+)$", shot_id)
    if match:
        ordinal = int(match.group(1))
        if 1 <= ordinal <= len(segments):
            return segments[ordinal - 1]
    return {}


def scan_keyframe_dir(root: Path | None, relative_dir: str) -> list[dict[str, Any]]:
    if not root:
        return []
    directory = root / relative_dir
    rows: list[dict[str, Any]] = []
    if not directory.exists():
        return rows
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        match = TIME_RE.search(path.name)
        if not match:
            continue
        item = normalize_candidate({"path": rel_to(path, root), "time": float(match.group(1))}, "scan", root)
        if item:
            rows.append(item)
    return rows


def deleted_paths(shot: dict[str, Any]) -> set[str]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    rows = reference.get("deleted_keyframes") if isinstance(reference.get("deleted_keyframes"), list) else []
    paths: set[str] = set()
    for item in rows:
        if isinstance(item, dict):
            value = str(item.get("path") or "").strip()
        else:
            value = str(item or "").strip()
        if value:
            paths.add(value)
    return paths


def in_range(candidate: dict[str, Any], start: float, end: float, tolerance: float) -> bool:
    time_value = safe_float(candidate.get("time"), -1.0)
    return start - tolerance <= time_value <= end + tolerance


def all_candidates(shot: dict[str, Any], source_package: dict[str, Any], root: Path | None, args: argparse.Namespace) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    start = safe_float(shot.get("start"), safe_float(reference.get("start")))
    end = safe_float(shot.get("end"), safe_float(reference.get("end"), start))
    if end < start:
        start, end = end, start
    candidates: list[dict[str, Any]] = []
    for frame in reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []:
        if isinstance(frame, dict):
            item = normalize_candidate(frame, "existing", root)
            if item:
                candidates.append(item)
    segment = matching_source_segment(source_package, shot)
    for frame in segment.get("keyframes") if isinstance(segment.get("keyframes"), list) else []:
        if isinstance(frame, dict):
            item = normalize_candidate(frame, "source_package", root)
            if item:
                candidates.append(item)
    candidates.extend(scan_keyframe_dir(root, "keyframes/pyscenedetect_scenes"))
    candidates.extend(scan_keyframe_dir(root, "keyframes/visual_candidates"))
    excluded = deleted_paths(shot) if not args.include_deleted else set()
    filtered = [
        item for item in candidates
        if in_range(item, start, end, safe_float(args.tolerance_seconds, 0.0)) and str(item.get("path") or "") not in excluded
    ]
    return dedupe_candidates(filtered, safe_float(args.time_dedupe_seconds, 0.04))


def better_candidate(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return max(
        (left, right),
        key=lambda item: (
            int(item.get("selection_priority") or 0),
            -len(str(item.get("path") or "")),
            -safe_float(item.get("time"), 0.0),
        ),
    )


def dedupe_candidates(candidates: list[dict[str, Any]], time_epsilon: float) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for item in candidates:
        path = str(item.get("path") or "")
        if not path:
            continue
        by_path[path] = better_candidate(by_path[path], item) if path in by_path else item
    rows = sorted(by_path.values(), key=lambda item: (safe_float(item.get("time"), 0.0), -int(item.get("selection_priority") or 0), str(item.get("path") or "")))
    deduped: list[dict[str, Any]] = []
    for item in rows:
        replaced = False
        for index, existing in enumerate(deduped):
            if abs(safe_float(item.get("time"), 0.0) - safe_float(existing.get("time"), 0.0)) <= time_epsilon:
                deduped[index] = better_candidate(existing, item)
                replaced = True
                break
        if not replaced:
            deduped.append(item)
    return sorted(deduped, key=lambda item: (safe_float(item.get("time"), 0.0), str(item.get("path") or "")))


def compact_frame(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": round(safe_float(candidate.get("time"), 0.0), 3),
        "path": str(candidate.get("path") or ""),
        "source": str(candidate.get("source") or "visual_candidate"),
        "resource_session": "analysis",
    }


def select_keyframes(shot: dict[str, Any], candidates: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    start = safe_float(shot.get("start"), safe_float(reference.get("start")))
    end = safe_float(shot.get("end"), safe_float(reference.get("end"), start))
    target_count = max(1, int(args.target_count or 1))
    existing_paths = set()
    if not args.replace:
        for frame in reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []:
            if isinstance(frame, dict) and str(frame.get("path") or "").strip():
                existing_paths.add(str(frame.get("path") or "").strip())
    selected: list[dict[str, Any]] = [item for item in candidates if str(item.get("path") or "") in existing_paths]
    selected_paths = {str(item.get("path") or "") for item in selected}
    if len(selected) >= target_count:
        return [compact_frame(item) for item in selected]
    slots = max(0, target_count - len(selected))
    if slots == 1:
        target_times = [(start + end) / 2]
    else:
        target_times = [start + ((end - start) * index / max(1, slots - 1)) for index in range(slots)]
    for target in target_times:
        available = [item for item in candidates if str(item.get("path") or "") not in selected_paths]
        if not available:
            break
        best = min(
            available,
            key=lambda item: (
                abs(safe_float(item.get("time"), 0.0) - target),
                -int(item.get("selection_priority") or 0),
                str(item.get("path") or ""),
            ),
        )
        selected.append(best)
        selected_paths.add(str(best.get("path") or ""))
    while len(selected) < target_count:
        available = [item for item in candidates if str(item.get("path") or "") not in selected_paths]
        if not available:
            break
        best = max(available, key=lambda item: (int(item.get("selection_priority") or 0), -abs(safe_float(item.get("time"), 0.0) - ((start + end) / 2))))
        selected.append(best)
        selected_paths.add(str(best.get("path") or ""))
    return [compact_frame(item) for item in sorted(selected, key=lambda item: (safe_float(item.get("time"), 0.0), str(item.get("path") or "")))]


def refresh_shot_keyframes(workspace: Path, plan: dict[str, Any], source_package: dict[str, Any], root: Path | None, args: argparse.Namespace) -> dict[str, Any]:
    shot = find_shot(plan, args.shot_id)
    reference = shot.setdefault("reference", {})
    before = [item for item in (reference.get("keyframes") or []) if isinstance(item, dict)]
    candidates = all_candidates(shot, source_package, root, args)
    selected = select_keyframes(shot, candidates, args)
    added_paths = sorted({str(item.get("path") or "") for item in selected} - {str(item.get("path") or "") for item in before})
    result = {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "generated_at": now_ms(),
        "task": {"task_id": args.task_id, "session_id": args.session_id, "workspace": str(workspace)},
        "shot_id": args.shot_id,
        "analysis_workspace": str(root) if root else "",
        "mode": "replace" if args.replace else "backfill",
        "target_count": int(args.target_count),
        "candidate_count": len(candidates),
        "before_count": len(before),
        "after_count": len(selected),
        "added_count": len(added_paths),
        "added_paths": added_paths,
        "selected_keyframes": selected,
        "dry_run": bool(args.dry_run),
    }
    if not args.dry_run:
        reference["keyframes"] = selected
        reference["original_keyframes"] = [dict(item) for item in selected]
        reference.setdefault("deleted_keyframes", [])
        reference["keyframe_refresh_summary"] = {
            "tool": TOOL_ID,
            "tool_version": TOOL_VERSION,
            "generated_at": result["generated_at"],
            "mode": result["mode"],
            "candidate_count": len(candidates),
            "before_count": len(before),
            "after_count": len(selected),
        }
        write_json(workspace / str(args.input), plan)
    write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}_{args.shot_id}.json", result)
    write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json", result)
    return result


def build_dependencies(workspace: Path, source_package: dict[str, Any] | None, root: Path | None, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[str] = []
    missing: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    plan_path = workspace / str(args.input)
    if plan_path.exists():
        satisfied.append("rebuild_shot_plan.json")
    else:
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"]})
    if source_package is not None:
        satisfied.append("source_package.json")
    else:
        missing.append({"dependency": "source_package.json", "reason": f"required workspace file does not exist: {args.source_package}", "suggested_tools": ["01_Rebuild_SourcePackageLoad"]})
    if args.shot_id:
        satisfied.append("shot_id")
    else:
        missing.append({"dependency": "shot_id", "reason": "--shot-id is required"})
    if root and ((root / "keyframes" / "pyscenedetect_scenes").exists() or (root / "keyframes" / "visual_candidates").exists()):
        satisfied.append("analysis_keyframes")
    else:
        warnings.append({"dependency": "analysis_keyframes", "reason": "No Analysis keyframe directories found; only existing/source_package keyframes can be used"})
    return {"status": "blocked" if missing else "warning" if warnings else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--shot-id", required=True)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--target-count", type=int, default=6)
    parser.add_argument("--tolerance-seconds", type=float, default=0.0)
    parser.add_argument("--time-dedupe-seconds", type=float, default=0.04)
    parser.add_argument("--include-deleted", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    context: dict[str, Any] = {}
    try:
        if args.task_id:
            database_url = args.database_url or os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
            context = fetch_task_context(database_url, args.task_id)
            args.session_id = args.session_id or int(context.get("session_id") or 0)
        workspace = resolve_workspace(context, args)
        source_payload = None
        source_path = workspace / source_package_path(context, args)
        if source_path.exists():
            payload = read_json(source_path)
            if isinstance(payload, dict):
                source_payload = payload
        root = analysis_workspace(source_payload or {}, context)
        dependencies = build_dependencies(workspace, source_payload, root, args)
        if args.check_dependencies_only or (dependencies["missing"] and not args.force):
            status = "blocked" if dependencies["missing"] else "completed_with_warnings" if dependencies["warnings"] else "completed"
            result_payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": status, "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": None}
        else:
            plan = read_json(workspace / str(args.input))
            if not isinstance(plan, dict):
                raise RuntimeError("rebuild_shot_plan.json must contain a JSON object")
            result = refresh_shot_keyframes(workspace, plan, source_payload or {}, root, args)
            result_payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": result.get("status", "completed"), "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": result}
    except Exception as exc:
        result_payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": "failed", "message": str(exc), "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS}
    if args.print_json:
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
    if result_payload.get("status") == "blocked":
        raise SystemExit(2)
    if result_payload.get("status") == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
