from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "SemanticFirstQualityChecker"
TOOL_VERSION = "0.2.0"
SCHEME_ORDER = ["detail", "balanced", "summary"]
RETAKE_FIELD_KEYS = [
    "guide",
    "summary",
    "video_structure",
    "shooting_method",
    "camera",
    "shot_type",
    "visual_content",
    "scene",
    "main_scene",
    "people_coordination",
    "performer",
    "character_profile",
    "props",
    "emotion",
    "emotion_trigger",
    "spoken_script",
    "target_audience",
    "content_highlights",
    "product_or_business_focus",
    "main_action",
    "camera_movement",
    "composition",
    "transition_type",
    "editing_notes",
    "retake_notes",
    "visual_must_have",
]


@dataclass(frozen=True)
class Paths:
    workspace: Path | None
    meta_dir: Path
    schemes_dir: Path
    reports_dir: Path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_paths(workspace: Path | None, output_dir: Path | None, schemes_dir: Path | None, reports_dir: Path | None) -> Paths:
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    meta_dir = output_dir.expanduser().resolve() if output_dir else (resolved_workspace / "meta" if resolved_workspace else Path.cwd() / "meta")
    resolved_schemes = schemes_dir.expanduser().resolve() if schemes_dir else (resolved_workspace / "schemes" if resolved_workspace else Path.cwd() / "schemes")
    resolved_reports = reports_dir.expanduser().resolve() if reports_dir else (resolved_workspace / "reports" if resolved_workspace else Path.cwd() / "reports")
    return Paths(workspace=resolved_workspace, meta_dir=meta_dir, schemes_dir=resolved_schemes, reports_dir=resolved_reports)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def file_state(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists(), "size_bytes": path.stat().st_size if path.exists() else 0}


def add_issue(report: dict[str, Any], severity: str, category: str, message: str, path: str = "", item: dict[str, Any] | None = None) -> None:
    issue = {"severity": severity, "category": category, "message": message}
    if path:
        issue["path"] = path
    if item:
        issue["item"] = item
    report["issues"].append(issue)


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return read_json(path)


def check_dependency_results(paths: Paths, report: dict[str, Any]) -> dict[str, Any]:
    required = {
        "13_fine_timeline_builder_result": paths.meta_dir / "13_fine_timeline_builder_result.json",
        "14_segment_descriptor_subtitle_builder_result": paths.meta_dir / "14_segment_descriptor_subtitle_builder_result.json",
        "15_scheme_export_validator_result": paths.meta_dir / "15_scheme_export_validator_result.json",
        "timeline_coverage_check_report": paths.reports_dir / "timeline_coverage_check.json",
    }
    loaded = {}
    states = {}
    for name, path in required.items():
        states[name] = file_state(path)
        if not path.exists():
            add_issue(report, "error", "dependencies", f"Missing required output: {name}", str(path))
            continue
        try:
            loaded[name] = read_json(path)
        except Exception as exc:
            add_issue(report, "error", "dependencies", f"Invalid JSON for {name}: {exc}", str(path))
    for name in ["13_fine_timeline_builder_result", "14_segment_descriptor_subtitle_builder_result", "15_scheme_export_validator_result"]:
        payload = loaded.get(name) or {}
        if payload and payload.get("status") != "completed":
            add_issue(report, "error", "dependencies", f"Upstream tool did not complete: {name} status={payload.get('status')}", states[name]["path"])
    report["checks"]["dependencies"] = {"status": "passed" if not any(i["category"] == "dependencies" and i["severity"] == "error" for i in report["issues"]) else "failed", "files": states}
    return loaded


def exported_scheme_mapping(loaded: dict[str, Any]) -> dict[str, str]:
    result15 = loaded.get("15_scheme_export_validator_result") or {}
    mapping = result15.get("scheme_mapping") if isinstance(result15, dict) else None
    if isinstance(mapping, dict) and mapping:
        return {str(key): str(value) for key, value in mapping.items() if str(value) in SCHEME_ORDER}
    coverage = loaded.get("timeline_coverage_check_report") or {}
    schemes = coverage.get("schemes") if isinstance(coverage, dict) else None
    if isinstance(schemes, dict):
        inferred = {}
        for scheme_name, item in schemes.items():
            if not isinstance(item, dict):
                continue
            source = str(item.get("source_scheme") or "")
            if source in SCHEME_ORDER:
                inferred[str(scheme_name)] = source
        if inferred:
            return inferred
    return {"scheme_1": "detail", "scheme_2": "balanced", "scheme_3": "summary"}


def selected_schemes_from_result(loaded: dict[str, Any]) -> list[str]:
    result15 = loaded.get("15_scheme_export_validator_result") or {}
    selected = result15.get("selected_schemes") if isinstance(result15, dict) else None
    if isinstance(selected, list):
        return [str(item) for item in selected if str(item) in SCHEME_ORDER]
    return list(exported_scheme_mapping(loaded).values())


def check_coverage(paths: Paths, loaded: dict[str, Any], report: dict[str, Any]) -> None:
    coverage = loaded.get("timeline_coverage_check_report") or {}
    schemes = coverage.get("schemes") if isinstance(coverage, dict) else None
    if coverage.get("valid") is not True:
        add_issue(report, "error", "timeline_coverage", "Coverage report is not valid", str(paths.reports_dir / "timeline_coverage_check.json"))
    expected = exported_scheme_mapping(loaded)
    if not isinstance(schemes, dict) or len(schemes) != len(expected):
        add_issue(report, "error", "timeline_coverage", "Coverage report must contain the exported schemes", str(paths.reports_dir / "timeline_coverage_check.json"), {"expected": expected, "actual_count": len(schemes) if isinstance(schemes, dict) else 0})
        schemes = schemes if isinstance(schemes, dict) else {}
    summary = {}
    for scheme_name, source in expected.items():
        item = schemes.get(scheme_name) or {}
        summary[scheme_name] = item
        if item.get("source_scheme") != source:
            add_issue(report, "error", "timeline_coverage", f"{scheme_name} should map to {source}", item={"actual": item.get("source_scheme")})
        if item.get("valid") is not True:
            add_issue(report, "error", "timeline_coverage", f"{scheme_name} coverage is invalid", item=item)
        if safe_float(item.get("start"), 999.0) != 0.0:
            add_issue(report, "error", "timeline_coverage", f"{scheme_name} does not start at 0", item=item)
        if item.get("issues"):
            add_issue(report, "error", "timeline_coverage", f"{scheme_name} contains coverage issues", item={"issues": item.get("issues")})
    report["checks"]["timeline_coverage"] = {"status": "passed" if not any(i["category"] == "timeline_coverage" and i["severity"] == "error" for i in report["issues"]) else "failed", "schemes": summary, "expected_mapping": expected}


def expected_segment_counts(paths: Paths) -> dict[str, int]:
    counts = {}
    for scheme in SCHEME_ORDER:
        path = paths.meta_dir / f"scheme_{scheme}_segments.json"
        if not path.exists():
            counts[scheme] = 0
            continue
        payload = read_json(path)
        items = payload.get("items") if isinstance(payload, dict) else []
        counts[scheme] = len(items) if isinstance(items, list) else 0
    return counts


def check_retake_json(path: Path) -> tuple[bool, list[str]]:
    try:
        payload = read_json(path)
    except Exception as exc:
        return False, [f"invalid_json:{exc}"]
    fields = payload.get("retake_fields") if isinstance(payload, dict) else None
    if not isinstance(fields, dict):
        return False, ["missing_retake_fields"]
    missing = [key for key in RETAKE_FIELD_KEYS if not str(fields.get(key) or "").strip()]
    return not missing, missing


def check_scheme_packages(paths: Paths, loaded: dict[str, Any], report: dict[str, Any]) -> None:
    expected_counts = expected_segment_counts(paths)
    mapping = exported_scheme_mapping(loaded)
    summary = {}
    for scheme_dir_name, source_scheme in mapping.items():
        scheme_dir = paths.schemes_dir / scheme_dir_name
        manifest_path = scheme_dir / "manifest.json"
        mp4s = sorted(scheme_dir.glob("segment_*.mp4"))
        srts = sorted(scheme_dir.glob("segment_*.srt"))
        jsons = sorted(scheme_dir.glob("segment_*.json"))
        expected = expected_counts.get(source_scheme, 0)
        manifest: dict[str, Any] = {}
        manifest_items: list[Any] = []
        clip_mode = "unknown"
        summary[scheme_dir_name] = {"source_scheme": source_scheme, "expected_segments": expected, "clip_mode": clip_mode, "mp4": len(mp4s), "srt": len(srts), "json": len(jsons), "manifest": file_state(manifest_path)}
        if not manifest_path.exists():
            add_issue(report, "error", "scheme_packages", f"Missing manifest for {scheme_dir_name}", str(manifest_path))
        else:
            manifest = read_json(manifest_path)
            manifest_items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
            clip_mode = str(manifest.get("clip_mode") or "unknown")
            summary[scheme_dir_name]["clip_mode"] = clip_mode
            if manifest.get("scheme") != source_scheme:
                add_issue(report, "error", "scheme_packages", f"Manifest scheme mismatch for {scheme_dir_name}", str(manifest_path), {"actual": manifest.get("scheme"), "expected": source_scheme})
        required_file_sets = [("srt", srts), ("json", jsons)]
        if clip_mode != "virtual":
            required_file_sets.insert(0, ("mp4", mp4s))
        for ext, files in required_file_sets:
            if len(files) != expected:
                add_issue(report, "error", "scheme_packages", f"{scheme_dir_name} {ext} count mismatch", str(scheme_dir), {"actual": len(files), "expected": expected})
        if len(manifest_items) != expected:
            add_issue(report, "error", "scheme_packages", f"{scheme_dir_name} manifest item count mismatch", str(manifest_path), {"actual": len(manifest_items), "expected": expected})
        if clip_mode == "virtual":
            source_video = str(manifest.get("source_video_path") or "")
            source_video_path = (paths.workspace / source_video) if paths.workspace and source_video and not Path(source_video).is_absolute() else Path(source_video)
            if not source_video or not source_video_path.exists() or source_video_path.stat().st_size <= 0:
                add_issue(report, "error", "scheme_packages", f"Missing virtual source video for {scheme_dir_name}", str(source_video_path) if source_video else str(manifest_path))
            for item in manifest_items:
                if not isinstance(item, dict):
                    add_issue(report, "error", "scheme_packages", f"Invalid manifest item in {scheme_dir_name}", str(manifest_path))
                    continue
                idx = int(item.get("segment_index") or 0)
                if str(item.get("clip_status") or "") != "virtual":
                    add_issue(report, "error", "scheme_packages", f"Virtual {scheme_dir_name} segment {idx:03d} has non-virtual clip_status", str(manifest_path), {"clip_status": item.get("clip_status")})
                if str(item.get("clip_path") or "") != source_video:
                    add_issue(report, "error", "scheme_packages", f"Virtual {scheme_dir_name} segment {idx:03d} should reference source video", str(manifest_path), {"clip_path": item.get("clip_path"), "source_video_path": source_video})
                if safe_float(item.get("end"), 0.0) <= safe_float(item.get("start"), 0.0):
                    add_issue(report, "error", "scheme_packages", f"Virtual {scheme_dir_name} segment {idx:03d} has invalid time range", str(manifest_path), {"start": item.get("start"), "end": item.get("end")})
        for idx in range(1, expected + 1):
            required_exts = ["srt", "json"] if clip_mode == "virtual" else ["mp4", "srt", "json"]
            for ext in required_exts:
                path = scheme_dir / f"segment_{idx:03d}.{ext}"
                if not path.exists() or path.stat().st_size <= 0:
                    add_issue(report, "error", "scheme_packages", f"Missing or empty {scheme_dir_name} segment {idx:03d}.{ext}", str(path))
            ok, missing = check_retake_json(scheme_dir / f"segment_{idx:03d}.json")
            if not ok:
                add_issue(report, "error", "retake_descriptions", f"Invalid retake description in {scheme_dir_name} segment {idx:03d}", str(scheme_dir / f"segment_{idx:03d}.json"), {"missing_fields": missing})
    report["checks"]["scheme_packages"] = {"status": "passed" if not any(i["category"] == "scheme_packages" and i["severity"] == "error" for i in report["issues"]) else "failed", "schemes": summary}
    report["checks"]["retake_descriptions"] = {"status": "passed" if not any(i["category"] == "retake_descriptions" and i["severity"] == "error" for i in report["issues"]) else "failed"}


def check_vlm(loaded: dict[str, Any], report: dict[str, Any], require_vlm: bool) -> None:
    result14 = loaded.get("14_segment_descriptor_subtitle_builder_result") or {}
    vlm = result14.get("vlm") or {}
    quality = result14.get("quality") or {}
    if require_vlm and vlm.get("enabled") is not True:
        add_issue(report, "error", "vlm", "14 did not run in VLM mode")
    detail_count = int((result14.get("counts") or {}).get("detail") or 0)
    completed = int(vlm.get("detail_segments_completed") or 0)
    failed = int(vlm.get("detail_segments_failed") or 0)
    if require_vlm and completed != detail_count:
        add_issue(report, "error", "vlm", "VLM completed count does not match detail count", item={"completed": completed, "detail_count": detail_count})
    if failed:
        add_issue(report, "error", "vlm", "VLM detail segments failed", item={"failed": failed})
    quality_items = quality.items() if isinstance(quality, dict) else []
    for scheme, item in quality_items:
        if safe_float(item.get("min_field_completeness"), 0.0) < 1.0:
            add_issue(report, "error", "retake_descriptions", f"{scheme} retake fields are incomplete", item=item)
        if int(item.get("items_needing_review") or 0) > 0:
            add_issue(report, "warning", "retake_descriptions", f"{scheme} has items needing review", item=item)
    report["checks"]["vlm"] = {"status": "passed" if not any(i["category"] == "vlm" and i["severity"] == "error" for i in report["issues"]) else "failed", "summary": vlm}


def check_final_counts(loaded: dict[str, Any], report: dict[str, Any]) -> None:
    result15 = loaded.get("15_scheme_export_validator_result") or {}
    counts = result15.get("counts") or {}
    expected_segments = int(counts.get("segments") or 0)
    for key in ["clips", "srts", "retake_descriptions"]:
        if int(counts.get(key) or 0) != expected_segments:
            add_issue(report, "error", "final_status", f"15 count mismatch for {key}", item={"actual": counts.get(key), "expected": expected_segments})
    if result15.get("coverage_valid") is not True:
        add_issue(report, "error", "final_status", "15 coverage_valid is not true")
    report["checks"]["final_status"] = {"status": "passed" if not any(i["category"] == "final_status" and i["severity"] == "error" for i in report["issues"]) else "failed", "counts": counts}


def run_checker(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workspace": str(paths.workspace) if paths.workspace else "",
        "status": "running",
        "checks": {},
        "issues": [],
    }
    loaded = check_dependency_results(paths, report)
    result15 = loaded.get("15_scheme_export_validator_result") or {}
    report["selected_schemes"] = selected_schemes_from_result(loaded)
    report["partial_export"] = bool((result15.get("partial_export") if isinstance(result15, dict) else False) or exported_scheme_mapping(loaded) != {"scheme_1": "detail", "scheme_2": "balanced", "scheme_3": "summary"})
    check_coverage(paths, loaded, report)
    check_scheme_packages(paths, loaded, report)
    check_vlm(loaded, report, bool(args.require_vlm))
    check_final_counts(loaded, report)
    errors = [item for item in report["issues"] if item.get("severity") == "error"]
    warnings = [item for item in report["issues"] if item.get("severity") == "warning"]
    report["status"] = "passed" if not errors else "failed"
    report["summary"] = {"errors": len(errors), "warnings": len(warnings), "checks": len(report["checks"])}
    report["outputs"] = {"quality_check": str(paths.reports_dir / "quality_check.json")}
    write_json(paths.reports_dir / "quality_check.json", report)
    write_json(paths.meta_dir / "16_semantic_first_quality_checker_result.json", {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": report["status"], "workspace": report["workspace"], "outputs": report["outputs"], "summary": report["summary"]})
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run full semantic-first pipeline quality checks.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/reports.")
    parser.add_argument("--output-dir", help="Explicit meta directory. Overrides --workspace/meta.")
    parser.add_argument("--schemes-dir", help="Explicit schemes directory. Overrides --workspace/schemes.")
    parser.add_argument("--reports-dir", help="Explicit reports directory. Overrides --workspace/reports.")
    parser.add_argument("--require-vlm", action="store_true", default=True, help="Require 14 to have completed VLM detail descriptions. Enabled by default.")
    parser.add_argument("--allow-rule-descriptions", action="store_true", help="Do not fail if 14 used rule mode instead of VLM mode.")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if bool(args.allow_rule_descriptions):
        args.require_vlm = False
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None, Path(args.schemes_dir) if args.schemes_dir else None, Path(args.reports_dir) if args.reports_dir else None)
    report = run_checker(paths, args)
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {report['status']}: {report['outputs']['quality_check']}")
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
