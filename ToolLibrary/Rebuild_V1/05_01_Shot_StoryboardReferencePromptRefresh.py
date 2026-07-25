from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.opencrew_paths import opencrew_session_workspace


TOOL_ID = "05_01_Shot_StoryboardReferencePromptRefresh"
TOOL_NAME = "Shot Storyboard Reference Prompt Refresh"
TOOL_VERSION = "1.0.0"
DEFAULT_VARIANT_ID = "variant_001"
DEFAULT_REFERENCE_TASK_ID = 5
DEFAULT_REFERENCE_SESSION_ID = 58
REQUIRES = [
    "rebuild_shot_plan.json",
    "storyboard_scene_marks",
    "storyboard_dialogue_plan.json",
    "reference_task_rebuild_shot_plan.json",
    "shot_id",
]
PRODUCES = [
    "rebuild_shot_plan.json",
    "reports/rebuild_v1/05_01_Shot_StoryboardReferencePromptRefresh.json",
    "Assets/<variant_id>/<shot_id>/reports/storyboard_reference_prompt_refresh.json",
]
SUGGESTED_PREVIOUS_TOOLS = ["04_02_Shot_FirstLastFrameConfirm"]
SUGGESTED_NEXT_TOOLS = ["05_02_Shot_FinalPromptPackageBuild", "12_00_Shot_PlanD_ReplacementImagePromptBuild"]


class ToolError(RuntimeError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "")) or "item"


def strip_srt_timing(text: Any) -> str:
    lines: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def normalize_text(text: Any) -> str:
    value = strip_srt_timing(text)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value, flags=re.UNICODE)
    return value.lower()


def text_similarity(left: Any, right: Any) -> float:
    lhs = normalize_text(left)
    rhs = normalize_text(right)
    if not lhs or not rhs:
        return 0.0
    ratio = difflib.SequenceMatcher(None, lhs, rhs).ratio()
    left_tokens = set(lhs)
    right_tokens = set(rhs)
    jaccard = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    return round((ratio * 0.75) + (jaccard * 0.25), 4)


def shot_id_of(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_id") or shot.get("id") or "").strip()


def scene_id_of(mark: dict[str, Any]) -> str:
    return str(mark.get("scene_mark_id") or mark.get("scene_id") or mark.get("id") or "").strip()


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    marks = reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else []
    return [item for item in marks if isinstance(item, dict)]


def target_shot(plan: dict[str, Any], shot_id: str) -> dict[str, Any] | None:
    return next((shot for shot in shot_list(plan) if shot_id_of(shot) == shot_id), None)


def scene_text_from_mark(mark: dict[str, Any]) -> str:
    for key in ("srt_text", "source_srt_text", "original_srt_text", "scene_srt", "text", "subtitle"):
        value = strip_srt_timing(mark.get(key))
        if value:
            return value
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    for key in ("srt_text", "summary", "video_prompt", "prompt"):
        value = strip_srt_timing(desc.get(key))
        if value:
            return value
    return ""


def shot_text(shot: dict[str, Any]) -> str:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    for key in ("srt_text", "source_srt_text", "original_srt_text", "spoken_script", "script"):
        value = strip_srt_timing(reference.get(key) or shot.get(key))
        if value:
            return value
    parts = [scene_text_from_mark(mark) for mark in scene_marks_for_shot(shot)]
    return " ".join(item for item in parts if item).strip()


def current_match_text(mark: dict[str, Any]) -> str:
    parts = [
        strip_srt_timing(mark.get("source_srt_text")),
        strip_srt_timing(mark.get("original_srt_text")),
        strip_srt_timing(mark.get("srt_text")),
    ]
    return " ".join(item for item in parts if item).strip()


def source_entry_from_mark(mark: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_shot_id": str(mark.get("source_shot_id") or "").strip(),
        "source_scene_mark_id": str(mark.get("source_scene_mark_id") or "").strip(),
        "current_scene_mark_id": scene_id_of(mark),
        "dialogue_index": mark.get("dialogue_index"),
        "dialogue_id": str(mark.get("dialogue_id") or "").strip(),
        "current_srt_text": strip_srt_timing(mark.get("srt_text")),
        "source_srt_text": strip_srt_timing(mark.get("source_srt_text") or mark.get("original_srt_text")),
    }


def source_entries_for_shot(shot: dict[str, Any], storyboard_dialogue_shot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    source_shot = storyboard_dialogue_shot if isinstance(storyboard_dialogue_shot, dict) else shot
    rows = []
    for mark in scene_marks_for_shot(source_shot):
        entry = source_entry_from_mark(mark)
        if entry["source_shot_id"]:
            rows.append(entry)
    return rows


def dedupe_source_coverage(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coverage: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (str(entry.get("source_shot_id") or ""), str(entry.get("source_scene_mark_id") or ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        coverage.append(
            {
                "reference_shot_id": key[0],
                "reference_scene_mark_id": key[1],
                "first_current_scene_mark_id": entry.get("current_scene_mark_id") or "",
                "dialogue_ids": [entry.get("dialogue_id")] if entry.get("dialogue_id") else [],
                "current_srt_texts": [entry.get("current_srt_text")] if entry.get("current_srt_text") else [],
                "source_srt_texts": [entry.get("source_srt_text")] if entry.get("source_srt_text") else [],
            }
        )
    for item in coverage:
        for entry in entries:
            if item["reference_shot_id"] == entry.get("source_shot_id") and item["reference_scene_mark_id"] == entry.get("source_scene_mark_id"):
                if entry.get("dialogue_id") and entry["dialogue_id"] not in item["dialogue_ids"]:
                    item["dialogue_ids"].append(entry["dialogue_id"])
                if entry.get("current_srt_text") and entry["current_srt_text"] not in item["current_srt_texts"]:
                    item["current_srt_texts"].append(entry["current_srt_text"])
                if entry.get("source_srt_text") and entry["source_srt_text"] not in item["source_srt_texts"]:
                    item["source_srt_texts"].append(entry["source_srt_text"])
    return coverage


def numeric_shot_id(value: str) -> int | None:
    match = re.search(r"shot_(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def expand_contiguous_coverage(coverage: list[dict[str, Any]], reference_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numbers = [numeric_shot_id(str(item.get("reference_shot_id") or "")) for item in coverage]
    numbers = [item for item in numbers if item is not None]
    if len(numbers) < 2:
        return coverage
    low, high = min(numbers), max(numbers)
    existing = {str(item.get("reference_shot_id") or ""): item for item in coverage}
    expanded: list[dict[str, Any]] = []
    for number in range(low, high + 1):
        shot_id = f"shot_{number:03d}"
        if shot_id in existing:
            item = dict(existing[shot_id])
            item.setdefault("coverage_fill", "explicit")
            expanded.append(item)
            continue
        if any(row["shot_id"] == shot_id for row in reference_rows):
            expanded.append(
                {
                    "reference_shot_id": shot_id,
                    "reference_scene_mark_id": "",
                    "first_current_scene_mark_id": "",
                    "dialogue_ids": [],
                    "current_srt_texts": [],
                    "source_srt_texts": [],
                    "coverage_fill": "contiguous_range_fill",
                }
            )
    return expanded


def source_entries_for_scene(mark: dict[str, Any], shot_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scene_text_norm = normalize_text(mark.get("srt_text"))
    scene_source_norm = normalize_text(current_match_text(mark))
    own_source_shot = str(mark.get("source_shot_id") or "").strip()
    selected: list[dict[str, Any]] = []
    for entry in shot_entries:
        current_norm = normalize_text(entry.get("current_srt_text"))
        source_norm = normalize_text(entry.get("source_srt_text"))
        source_shot = str(entry.get("source_shot_id") or "").strip()
        if current_norm and scene_text_norm and current_norm in scene_text_norm:
            selected.append(entry)
        elif source_norm and scene_source_norm and source_norm in scene_source_norm:
            selected.append(entry)
        elif own_source_shot and source_shot == own_source_shot:
            selected.append(entry)
    return selected or ([source_entry_from_mark(mark)] if str(mark.get("source_shot_id") or "").strip() else [])


def coverage_reference_payload(coverage: list[dict[str, Any]], reference_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coverage = expand_contiguous_coverage(coverage, reference_rows)
    enriched = []
    for item in coverage:
        row = next(
            (
                candidate
                for candidate in reference_rows
                if candidate["shot_id"] == item["reference_shot_id"] and candidate["scene_mark_id"] == item["reference_scene_mark_id"]
            ),
            None,
        )
        if row is None:
            row = next((candidate for candidate in reference_rows if candidate["shot_id"] == item["reference_shot_id"]), None)
        next_item = dict(item)
        if row:
            next_item.update(
                {
                    "reference_srt_text": row.get("text") or row.get("shot_text") or "",
                    "reference_summary": row.get("summary") or "",
                    "reference_rebuild_direction": row.get("rebuild_direction") or "",
                    "reference_generation_hint": row.get("generation_hint") or "",
                    "has_final_prompt_package": bool(row.get("final_prompt_package")),
                }
            )
        enriched.append(next_item)
    return enriched


def aggregate_visual_payload(coverage_payload: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [str(item.get("reference_summary") or "").strip() for item in coverage_payload if str(item.get("reference_summary") or "").strip()]
    directions = [str(item.get("reference_rebuild_direction") or "").strip() for item in coverage_payload if str(item.get("reference_rebuild_direction") or "").strip()]
    hints = [str(item.get("reference_generation_hint") or "").strip() for item in coverage_payload if str(item.get("reference_generation_hint") or "").strip()]
    refs = [str(item.get("reference_shot_id") or "").strip() for item in coverage_payload if str(item.get("reference_shot_id") or "").strip()]
    return {
        "summary": " / ".join(dict.fromkeys(summaries)),
        "visual_change": " / ".join(dict.fromkeys(hints)),
        "motion_prompt": " / ".join(dict.fromkeys(directions)),
        "reference_range": f"{refs[0]} to {refs[-1]}" if refs else "",
        "reference_shot_ids": list(dict.fromkeys(refs)),
    }


def field_text(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        return " / ".join(str(item).strip() for item in value.values() if isinstance(item, str) and item.strip())
    return ""


def load_final_prompt_package(workspace: Path, variant_id: str, shot_id: str) -> dict[str, Any]:
    path = workspace / "Assets" / variant_id / safe_name(shot_id) / "final_prompt_package.json"
    if not path.exists():
        return {}
    parsed = read_json(path)
    return parsed if isinstance(parsed, dict) else {}


def final_package_scene(package: dict[str, Any], scene_mark_id: str) -> dict[str, Any]:
    scenes = package.get("scenes") if isinstance(package.get("scenes"), list) else []
    return next((item for item in scenes if isinstance(item, dict) and str(item.get("scene_mark_id") or "") == scene_mark_id), {})


def reference_scene_entry(
    reference_workspace: Path,
    reference_plan: dict[str, Any],
    variant_id: str,
    shot: dict[str, Any],
    mark: dict[str, Any] | None,
) -> dict[str, Any]:
    shot_id = shot_id_of(shot)
    scene_id = scene_id_of(mark or {}) if mark else ""
    package = load_final_prompt_package(reference_workspace, variant_id, shot_id)
    package_scene = final_package_scene(package, scene_id) if scene_id else {}
    return {
        "shot": shot,
        "mark": mark or {},
        "final_prompt_package": package,
        "final_prompt_scene": package_scene,
        "shot_id": shot_id,
        "scene_mark_id": scene_id,
        "text": scene_text_from_mark(mark or {}) or shot_text(shot),
        "shot_text": shot_text(shot),
        "summary": field_text(shot.get("ui_summary"), ("summary", "title", "what_happens")),
        "rebuild_direction": field_text(shot.get("rebuild_direction"), ("direction", "new_scene", "new_spoken_script")),
        "generation_hint": field_text(shot.get("generation_hint"), ("hint", "visual", "prompt", "motion")),
    }


def build_reference_index(reference_workspace: Path, reference_plan: dict[str, Any], variant_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for shot in shot_list(reference_plan):
        marks = scene_marks_for_shot(shot)
        if marks:
            rows.extend(reference_scene_entry(reference_workspace, reference_plan, variant_id, shot, mark) for mark in marks)
        else:
            rows.append(reference_scene_entry(reference_workspace, reference_plan, variant_id, shot, None))
    return rows


def find_reference_match(mark: dict[str, Any], reference_rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_shot_id = str(mark.get("source_shot_id") or "").strip()
    source_scene_id = str(mark.get("source_scene_mark_id") or "").strip()
    if source_shot_id and source_scene_id:
        direct = next((row for row in reference_rows if row["shot_id"] == source_shot_id and row["scene_mark_id"] == source_scene_id), None)
        if direct:
            return {**direct, "match_method": "source_shot_scene_id", "match_score": 1.0}
    if source_shot_id:
        shot_rows = [row for row in reference_rows if row["shot_id"] == source_shot_id]
        if len(shot_rows) == 1:
            return {**shot_rows[0], "match_method": "source_shot_id", "match_score": 0.95}
        if shot_rows:
            best = max(shot_rows, key=lambda row: text_similarity(current_match_text(mark), row.get("text") or row.get("shot_text")))
            return {**best, "match_method": "source_shot_id_text", "match_score": text_similarity(current_match_text(mark), best.get("text") or best.get("shot_text"))}
    text = current_match_text(mark)
    best = max(reference_rows, key=lambda row: text_similarity(text, row.get("text") or row.get("shot_text")))
    return {**best, "match_method": "text_similarity", "match_score": text_similarity(text, best.get("text") or best.get("shot_text"))}


def prompt_piece(*values: Any) -> str:
    return " ".join(str(value or "").strip() for value in values if str(value or "").strip()).strip()


def reference_visual_payload(match: dict[str, Any]) -> dict[str, Any]:
    mark = match.get("mark") if isinstance(match.get("mark"), dict) else {}
    final_scene = match.get("final_prompt_scene") if isinstance(match.get("final_prompt_scene"), dict) else {}
    final_prompts = mark.get("final_prompts") if isinstance(mark.get("final_prompts"), dict) else {}
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    image_prompt = str(final_scene.get("image_prompt") or final_prompts.get("image_prompt") or "").strip()
    video_prompt = str(final_scene.get("video_prompt") or final_prompts.get("video_prompt") or desc.get("video_prompt") or "").strip()
    return {
        "summary": prompt_piece(desc.get("summary"), match.get("summary")),
        "visual_change": prompt_piece(desc.get("visual_change"), match.get("generation_hint")),
        "motion_prompt": prompt_piece(desc.get("motion_prompt"), match.get("rebuild_direction")),
        "video_prompt": video_prompt,
        "image_prompt": image_prompt,
        "negative_prompt": str(desc.get("negative_prompt") or "watermark, logo, subtitles, captions, unreadable text, distorted face, bad hands, low quality").strip(),
        "model_notes": desc.get("model_notes") if isinstance(desc.get("model_notes"), dict) else {},
        "image_prompt_authoring": final_scene.get("image_prompt_authoring") if isinstance(final_scene.get("image_prompt_authoring"), dict) else final_prompts.get("image_prompt_authoring", {}),
    }


def merge_scene(
    mark: dict[str, Any],
    match: dict[str, Any],
    args: argparse.Namespace,
    scene_coverage_payload: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    visual = reference_visual_payload(match)
    scene_coverage_payload = scene_coverage_payload or []
    aggregate_visual = aggregate_visual_payload(scene_coverage_payload)
    if scene_coverage_payload:
        visual["summary"] = prompt_piece(aggregate_visual["summary"], visual["summary"])
        visual["visual_change"] = prompt_piece(aggregate_visual["visual_change"], visual["visual_change"])
        visual["motion_prompt"] = prompt_piece(aggregate_visual["motion_prompt"], visual["motion_prompt"])
    current_text = strip_srt_timing(mark.get("srt_text"))
    reference_text = strip_srt_timing(match.get("text") or match.get("shot_text"))
    source_text = current_match_text(mark)
    scene_id = scene_id_of(mark)
    reference_match = {
        "source": TOOL_ID,
        "reference_task_id": int(args.reference_task_id),
        "reference_session_id": int(args.reference_session_id),
        "reference_workspace": str(args.reference_workspace),
        "reference_shot_id": match.get("shot_id"),
        "reference_scene_mark_id": match.get("scene_mark_id"),
        "match_method": match.get("match_method"),
        "match_score": match.get("match_score"),
        "current_scene_mark_id": scene_id,
        "current_srt_text": current_text,
        "current_source_srt_text": source_text,
        "reference_srt_text": reference_text,
        "reference_coverage": scene_coverage_payload,
        "reference_coverage_count": len(scene_coverage_payload),
    }
    scene_description = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    scene_description.update(
        {
            "summary": prompt_piece("StoryBoard aggregate scene based on new dialogue:", current_text, "Reference coverage:", aggregate_visual.get("reference_range"), "Reference structure:", visual["summary"]),
            "visual_change": prompt_piece("Preserve the StoryBoard frame as current composition. Borrow one-to-many reference Task structure:", visual["visual_change"]),
            "motion_prompt": prompt_piece("Use current dialogue for acting rhythm. Aggregate reference motion logic:", visual["motion_prompt"]),
            "video_prompt": prompt_piece(
                "Create a realistic vertical 9:16 phone-video scene from the current StoryBoard frame.",
                "Use the current scene narration as the spoken content:",
                current_text,
                "Use the matched reference coverage only for product-demo action structure, camera continuity, and short-video realism:",
                ", ".join(aggregate_visual.get("reference_shot_ids") or []),
                visual["video_prompt"][:2200],
            ),
            "negative_prompt": visual["negative_prompt"],
            "model_notes": {
                **(visual["model_notes"] if isinstance(visual["model_notes"], dict) else {}),
                "storyboard_reference": "Current StoryBoard frame wins for composition; current srt_text wins for TTS/dialogue; matched Task #5 coverage supplies aggregated action, visual logic, and prompt structure.",
            },
            "reference_match": reference_match,
            "reference_coverage": scene_coverage_payload,
        }
    )
    mark["scene_description"] = scene_description
    mark["srt_source_span"] = current_text
    mark["srt_match_source"] = "storyboard_dialogue"
    mark["srt_match_reason"] = "StoryBoard dialogue is the target script; reference Task content is used only for structural alignment."
    mark["prompt_source"] = TOOL_ID
    mark["prompt_priority"] = "storyboard_dialogue_first_reference_task_structure_second"
    mark["prompt_refreshed_at"] = now_ms()
    mark["storyboard_reference_match"] = reference_match
    final_prompts = mark.get("final_prompts") if isinstance(mark.get("final_prompts"), dict) else {}
    final_prompts["storyboard_reference_image_prompt_seed"] = visual["image_prompt"]
    final_prompts["storyboard_reference_video_prompt_seed"] = visual["video_prompt"]
    final_prompts["storyboard_reference_match"] = reference_match
    final_prompts["storyboard_reference_coverage"] = scene_coverage_payload
    mark["final_prompts"] = final_prompts
    return {"scene_mark_id": scene_id, "status": "completed", "match": reference_match, "reference_coverage_count": len(scene_coverage_payload)}


def report_paths(workspace: Path, variant_id: str, shot_id: str) -> tuple[Path, Path]:
    global_report = workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json"
    shot_report = workspace / "Assets" / variant_id / safe_name(shot_id) / "reports" / "storyboard_reference_prompt_refresh.json"
    return global_report, shot_report


def resolve_reference_workspace(args: argparse.Namespace) -> Path:
    if args.reference_workspace:
        return Path(args.reference_workspace).expanduser().resolve()
    return opencrew_session_workspace(args.reference_session_id)


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    reference_workspace = resolve_reference_workspace(args)
    args.reference_workspace = str(reference_workspace)
    plan_path = workspace / args.input
    storyboard_dialogue_path = workspace / args.storyboard_dialogue_plan
    reference_plan_path = reference_workspace / args.reference_input
    satisfied: list[str] = []
    missing: list[dict[str, Any]] = []
    warnings: list[str] = []
    if len(args.shot_id) != 1:
        missing.append({"dependency": "shot_id", "reason": "tool requires exactly one --shot-id"})
    if plan_path.exists():
        satisfied.append("rebuild_shot_plan.json")
    else:
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"missing {plan_path}"})
    if reference_plan_path.exists():
        satisfied.append("reference_task_rebuild_shot_plan.json")
    else:
        missing.append({"dependency": "reference_task_rebuild_shot_plan.json", "reason": f"missing {reference_plan_path}"})
    if storyboard_dialogue_path.exists():
        satisfied.append("storyboard_dialogue_plan.json")
    else:
        warnings.append(f"optional storyboard dialogue plan not found; falling back to current scene marks: {storyboard_dialogue_path}")
    if not missing and len(args.shot_id) == 1:
        plan = read_json(plan_path)
        shot = target_shot(plan, args.shot_id[0])
        if not shot:
            missing.append({"dependency": "target_shot", "reason": f"shot not found: {args.shot_id[0]}"})
        elif scene_marks_for_shot(shot):
            satisfied.append("storyboard_scene_marks")
        else:
            missing.append({"dependency": "storyboard_scene_marks", "reason": f"target shot has no scene marks: {args.shot_id[0]}"})
        task = plan.get("task") if isinstance(plan.get("task"), dict) else {}
        if args.task_id and int(task.get("task_id") or 0) and int(task.get("task_id") or 0) != int(args.task_id):
            warnings.append(f"requested task_id={args.task_id} differs from plan task_id={task.get('task_id')}")
        if args.session_id and int(task.get("session_id") or 0) and int(task.get("session_id") or 0) != int(args.session_id):
            warnings.append(f"requested session_id={args.session_id} differs from plan session_id={task.get('session_id')}")
    return {"status": "blocked" if missing else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}


def run(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    reference_workspace = resolve_reference_workspace(args)
    plan = read_json(workspace / args.input)
    reference_plan = read_json(reference_workspace / args.reference_input)
    storyboard_dialogue_path = workspace / args.storyboard_dialogue_plan
    storyboard_dialogue_plan = read_json(storyboard_dialogue_path) if storyboard_dialogue_path.exists() else {}
    shot = target_shot(plan, args.shot_id[0])
    if not shot:
        raise ToolError(f"Shot not found: {args.shot_id[0]}")
    storyboard_dialogue_shot = target_shot(storyboard_dialogue_plan, args.shot_id[0]) if isinstance(storyboard_dialogue_plan, dict) else None
    reference_rows = build_reference_index(reference_workspace, reference_plan, args.variant_id)
    shot_source_entries = source_entries_for_shot(shot, storyboard_dialogue_shot)
    shot_reference_coverage = coverage_reference_payload(dedupe_source_coverage(shot_source_entries), reference_rows)
    results = []
    for mark in scene_marks_for_shot(shot):
        match = find_reference_match(mark, reference_rows)
        scene_source_entries = source_entries_for_scene(mark, shot_source_entries)
        scene_reference_coverage = coverage_reference_payload(dedupe_source_coverage(scene_source_entries), reference_rows)
        results.append(merge_scene(mark, match, args, scene_reference_coverage))
    shot.setdefault("reference", {})["storyboard_reference_prompt_refresh"] = {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "updated_at": now_ms(),
        "reference_task_id": int(args.reference_task_id),
        "reference_session_id": int(args.reference_session_id),
        "reference_workspace": str(reference_workspace),
        "scene_count": len(results),
        "reference_coverage_count": len(shot_reference_coverage),
        "reference_coverage": shot_reference_coverage,
        "coverage_source": args.storyboard_dialogue_plan if storyboard_dialogue_path.exists() else args.input,
    }
    write_json(workspace / args.output, plan)
    report = {
        "status": "completed",
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "workspace": str(workspace),
        "shot_id": args.shot_id[0],
        "variant_id": args.variant_id,
        "reference": {
            "task_id": int(args.reference_task_id),
            "session_id": int(args.reference_session_id),
            "workspace": str(reference_workspace),
            "input": args.reference_input,
        },
        "reference_coverage_count": len(shot_reference_coverage),
        "reference_coverage": shot_reference_coverage,
        "outputs": [
            args.output,
            f"reports/rebuild_v1/{TOOL_ID}.json",
            f"Assets/{args.variant_id}/{safe_name(args.shot_id[0])}/reports/storyboard_reference_prompt_refresh.json",
        ],
        "algorithm": [
            "Load current StoryBoard shot plan and reference Task shot plan.",
            "Load storyboard_dialogue_plan.json when available to recover the fine-grained one-to-many source coverage that was compressed into the current StoryBoard shot.",
            "Build a reference index from Task shots, scene marks when available, and any existing final_prompt_package scene prompts.",
            "For each current scene, collect all source_shot_id/source_scene_mark_id entries whose dialogue was merged into that scene, then enrich that coverage from the reference task.",
            "Select a primary reference by source ids or dialogue similarity for backward compatibility, but write the full reference_coverage list as the main audit object.",
            "Keep current scene timing, keyframes, scene_mark_id, and srt_text unchanged.",
            "Merge aggregate reference visual/action/prompt structure into scene_description and final_prompts seed fields.",
            "Write auditable reference_match and reference_coverage objects per scene plus shot-level coverage reports.",
        ],
        "results": results,
    }
    global_report, shot_report = report_paths(workspace, args.variant_id, args.shot_id[0])
    write_json(global_report, report)
    write_json(shot_report, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--variant-id", default=DEFAULT_VARIANT_ID)
    parser.add_argument("--reference-task-id", type=int, default=DEFAULT_REFERENCE_TASK_ID)
    parser.add_argument("--reference-session-id", type=int, default=DEFAULT_REFERENCE_SESSION_ID)
    parser.add_argument("--reference-workspace", default="")
    parser.add_argument("--reference-input", default="rebuild_shot_plan.json")
    parser.add_argument("--storyboard-dialogue-plan", default="storyboard_dialogue_plan.json")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    dependencies = check_dependencies(workspace, args)
    try:
        if args.check_dependencies_only or (dependencies["missing"] and not args.force):
            status, result = ("blocked" if dependencies["missing"] else "completed"), None
        else:
            result = run(workspace, args)
            status = result.get("status", "completed")
        payload = {
            "tool": TOOL_ID,
            "tool_version": TOOL_VERSION,
            "status": status,
            "workspace": str(workspace),
            "dependencies": dependencies,
            "produces": PRODUCES,
            "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS,
            "suggested_next_tools": SUGGESTED_NEXT_TOOLS,
            "result": result,
        }
    except Exception as exc:
        payload = {
            "tool": TOOL_ID,
            "tool_version": TOOL_VERSION,
            "status": "failed",
            "workspace": str(workspace),
            "message": str(exc),
            "dependencies": dependencies,
            "produces": PRODUCES,
            "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS,
            "suggested_next_tools": SUGGESTED_NEXT_TOOLS,
        }
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] == "blocked":
        raise SystemExit(2)
    if payload["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
