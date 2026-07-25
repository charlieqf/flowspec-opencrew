from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


TOOL_NAME = "SubtitleBidirectionalCalibrator"
TOOL_VERSION = "0.1.0"


@dataclass(frozen=True)
class Paths:
    workspace: Path | None
    meta_dir: Path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text or "")
    cleaned = re.sub(r"[\W_]+", "", cleaned)
    return cleaned.lower().strip()


def is_sentence_boundary(punctuation: Any) -> bool:
    return bool(re.search(r"[，,。！？!?]", str(punctuation or "")))


def clean_subtitle_noise(text: str) -> tuple[str, list[str]]:
    raw = display_text(text)
    removed: list[str] = []
    visual_noise_phrases = ["草本舒缓·蜂胶润护", "草本舒缓 蜂胶润护", "草本舒缓蜂胶润护"]
    cleaned = raw
    for phrase in visual_noise_phrases:
        if phrase in cleaned:
            removed.append(phrase)
            cleaned = cleaned.replace(phrase, " ")
    patterns = [
        r"\b[A-Za-z][A-Za-z0-9.!:;_+\-/]*\b",
        r"[.·\-_:;!！]+(?=\s*[\u4e00-\u9fff])",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, cleaned):
            token = str(match).strip()
            if token and token not in removed and re.search(r"[A-Za-z]", token):
                removed.append(token)
        cleaned = re.sub(pattern, " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。；;、-_.!")
    return cleaned or raw, removed


def asr_sentence_row(index: int, chunk: dict[str, Any], sentence: dict[str, Any], words: list[dict[str, Any]]) -> dict[str, Any]:
    first = words[0]
    last = words[-1]
    return {
        "index": index,
        "id": f"asr_sentence_{index:03d}",
        "start": round(float(first["start"]), 3),
        "end": round(float(last["end"]), 3),
        "text": "".join(str(word.get("text") or "") + (str(word.get("punctuation") or "") if word is last else "") for word in words).strip(),
        "source_chunk_index": chunk.get("index"),
        "source_chunk_start": round(float(chunk.get("start") or 0.0), 3),
        "source_sentence_id": sentence.get("sentence_id"),
        "source_sentence_text": str(sentence.get("text") or ""),
        "words": words,
        "timing_source": "provider_word_timestamps",
    }


def dedupe_asr_sentence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")))):
        key = normalize_text(str(row.get("text") or ""))
        if not key:
            continue
        duplicate_index = next((idx for idx, existing in enumerate(output) if normalize_text(str(existing.get("text") or "")) == key and abs(safe_float(existing.get("start")) - safe_float(row.get("start"))) <= 0.75), -1)
        if duplicate_index >= 0:
            existing = output[duplicate_index]
            existing_duration = safe_float(existing.get("end")) - safe_float(existing.get("start"))
            row_duration = safe_float(row.get("end")) - safe_float(row.get("start"))
            if row_duration > 0 and (existing_duration <= 0 or row_duration < existing_duration):
                output[duplicate_index] = row
            continue
        output.append(row)
    for index, row in enumerate(output, start=1):
        row["index"] = index
        row["id"] = f"asr_sentence_{index:03d}"
    filtered: list[dict[str, Any]] = []
    for row in output:
        if filtered and abs(safe_float(row.get("start")) - safe_float(filtered[-1].get("start"))) <= 0.05:
            previous = filtered[-1]
            previous_norm = normalize_text(str(previous.get("text") or ""))
            row_norm = normalize_text(str(row.get("text") or ""))
            previous_duration = safe_float(previous.get("end")) - safe_float(previous.get("start"))
            row_duration = safe_float(row.get("end")) - safe_float(row.get("start"))
            if previous_norm in row_norm or previous_duration <= row_duration:
                filtered[-1] = row
                continue
            if row_norm in previous_norm or row_duration <= previous_duration:
                continue
        filtered.append(row)
    for index, row in enumerate(filtered, start=1):
        row["index"] = index
        row["id"] = f"asr_sentence_{index:03d}"
    return filtered


def build_asr_sentence_timeline_from_provider(provider_raw: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for chunk_result in provider_raw.get("items") or []:
        chunk = chunk_result.get("chunk") or {"index": 1, "start": 0.0}
        chunk_start = safe_float(chunk.get("start"))
        for sentence in chunk_result.get("raw_sentences") or []:
            words: list[dict[str, Any]] = []
            for word in sentence.get("words") or []:
                begin_ms = safe_float(word.get("begin_time"))
                end_ms = safe_float(word.get("end_time"), begin_ms)
                words.append({"text": str(word.get("text") or ""), "punctuation": str(word.get("punctuation") or ""), "start": round(chunk_start + begin_ms / 1000.0, 3), "end": round(chunk_start + end_ms / 1000.0, 3)})
                if is_sentence_boundary(word.get("punctuation")) and words:
                    rows.append(asr_sentence_row(len(rows) + 1, chunk, sentence, words))
                    words = []
            if words:
                rows.append(asr_sentence_row(len(rows) + 1, chunk, sentence, words))
    return {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "timing_policy": "provider_word_timestamps", "items": dedupe_asr_sentence_rows(rows)}


def display_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def text_similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    return float(SequenceMatcher(None, left_norm, right_norm).ratio())


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def ranges_overlap(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    return max(left_start, right_start) <= min(max(left_start, left_end), max(right_start, right_end))


def overlap_items(start: float, end: float, items: list[dict[str, Any]], window: float = 0.0) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        item_start = safe_float(item.get("start"), safe_float(item.get("time")))
        item_end = safe_float(item.get("end"), item_start)
        if ranges_overlap(start - window, end + window, item_start, item_end):
            rows.append(item)
    return rows


def asr_base_reliability(asr_quality: dict[str, Any]) -> float:
    level = str(asr_quality.get("quality_level") or "unknown")
    score = {"good": 0.86, "usable": 0.62, "weak": 0.35, "failed": 0.1}.get(level, 0.5)
    if bool(asr_quality.get("timestamp_coverage_suspect")):
        score -= 0.18
    return max(0.0, min(1.0, score))


def ocr_reliability(item: dict[str, Any]) -> float:
    confidence = safe_float(item.get("confidence"))
    class_confidence = safe_float(item.get("class_confidence"), confidence)
    times = item.get("source_keyframe_times") or []
    stability = min(0.2, 0.04 * len(times))
    return round(max(0.0, min(1.0, confidence * 0.45 + class_confidence * 0.4 + stability)), 4)


def choose_best_asr_match(ocr: dict[str, Any], asr_items: list[dict[str, Any]], window: float) -> tuple[list[dict[str, Any]], float, str]:
    start = safe_float(ocr.get("start"), safe_float(ocr.get("time")))
    end = safe_float(ocr.get("end"), start)
    candidates = overlap_items(start, end, asr_items, window=window)
    if not candidates:
        return [], 0.0, ""
    ocr_text = str(ocr.get("ocr_clean_text") or ocr.get("text") or ocr.get("ocr_text") or "")
    best_rows: list[dict[str, Any]] = []
    best_similarity = -1.0
    best_text = ""
    for index in range(len(candidates)):
        for length in range(1, min(4, len(candidates) - index) + 1):
            rows = candidates[index : index + length]
            text = "".join(str(item.get("text") or "") for item in rows)
            similarity = text_similarity(ocr_text, text)
            if similarity > best_similarity:
                best_similarity = similarity
                best_rows = rows
                best_text = text
    return best_rows, max(0.0, best_similarity), best_text


def scene_index_for_time(scenes: list[dict[str, Any]], time_value: float) -> int | None:
    for scene in scenes:
        if safe_float(scene.get("start")) <= time_value <= safe_float(scene.get("end"), safe_float(scene.get("start"))):
            return int(scene.get("index") or 0) or None
    return None


def visual_text_for_range(start: float, end: float, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return overlap_items(start, end, items, window=0.25)


def calibrate_subtitles(asr: dict[str, Any], asr_quality: dict[str, Any], subtitle_items: list[dict[str, Any]], visual_text_items: list[dict[str, Any]], scenes: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    asr_items = [item for item in (asr.get("items") or asr.get("segments") or []) if isinstance(item, dict)]
    asr_score = asr_base_reliability(asr_quality)
    calibrated: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    used_asr_ids: set[int] = set()

    for idx, ocr in enumerate(subtitle_items, start=1):
        start = safe_float(ocr.get("start"), safe_float(ocr.get("time")))
        end = safe_float(ocr.get("end"), start)
        ocr_raw_text = display_text(str(ocr.get("text") or ocr.get("ocr_text") or ""))
        ocr_text, removed_noise_tokens = clean_subtitle_noise(ocr_raw_text)
        ocr_for_match = {**ocr, "ocr_clean_text": ocr_text}
        matched_asr, similarity, asr_text = choose_best_asr_match(ocr_for_match, asr_items, float(args.match_window_seconds))
        ocr_score = ocr_reliability(ocr)
        policy = "needs_review"
        preferred_source = "mixed"
        preferred_text = ocr_text or asr_text
        calibrated_start = start
        calibrated_end = end
        warnings: list[str] = []

        ocr_norm = normalize_text(ocr_text)
        asr_norm = normalize_text(asr_text)
        local_confirmed = bool(matched_asr and ocr_norm and (ocr_norm in asr_norm or similarity >= float(args.duplicate_similarity)))

        if removed_noise_tokens and local_confirmed:
            policy = "ocr_noise_trimmed"
            preferred_source = "subtitle_ocr"
            preferred_text = ocr_text
            warnings.append("removed_visual_noise_tokens")
        elif matched_asr and local_confirmed:
            policy = "ocr_asr_local_confirmed"
            preferred_source = "subtitle_ocr"
            preferred_text = ocr_text
        elif matched_asr and similarity >= float(args.duplicate_similarity):
            policy = "subtitle_duplicate"
            preferred_source = "subtitle_ocr"
            preferred_text = ocr_text
        elif not matched_asr and ocr_score >= float(args.ocr_fill_gap_reliability):
            policy = "ocr_fills_asr_gap"
            preferred_source = "subtitle_ocr"
            preferred_text = ocr_text
        elif matched_asr and ocr_score >= asr_score + 0.12 and similarity < float(args.low_similarity):
            policy = "ocr_corrects_asr"
            preferred_source = "subtitle_ocr"
            preferred_text = ocr_text
            warnings.append("low_similarity_ocr_preferred")
        elif matched_asr:
            policy = "mixed_reconcile"
            preferred_source = "mixed"
            preferred_text = ocr_text or asr_text
            warnings.append("asr_ocr_text_mismatch")

        source_asr_ids = [int(item.get("index") or 0) for item in matched_asr if item.get("index") is not None]
        used_asr_ids.update(source_asr_ids)
        scene_index = ocr.get("scene_index") or scene_index_for_time(scenes, calibrated_start)
        row = {
            "id": f"subtitle_align_{idx:03d}",
            "start": round(calibrated_start, 3),
            "end": round(max(calibrated_start, calibrated_end), 3),
            "text": preferred_text,
            "preferred_text": preferred_text,
            "preferred_source": preferred_source,
            "alignment_policy": policy,
            "ocr_text": ocr_text,
            "ocr_raw_text": ocr_raw_text,
            "ocr_clean_text": ocr_text,
            "asr_text": asr_text,
            "asr_sentence_text": asr_text,
            "ocr_asr_similarity": round(similarity, 4),
            "asr_reliability": round(asr_score, 4),
            "subtitle_ocr_reliability": round(ocr_score, 4),
            "scene_index": scene_index,
            "source_asr_segment_ids": source_asr_ids,
            "source_asr_sentence_ids": [str(item.get("id") or item.get("index") or "") for item in matched_asr if item.get("id") or item.get("index")],
            "source_ocr_item_ids": [ocr.get("id") or ocr.get("index") or idx],
            "source_keyframe_times": ocr.get("source_keyframe_times") or [],
            "visual_text_context": [str(item.get("text") or item.get("ocr_text") or "") for item in visual_text_for_range(calibrated_start, calibrated_end, visual_text_items)],
            "needs_review": policy in {"needs_review", "mixed_reconcile"},
            "warnings": warnings,
            "removed_visual_noise_tokens": removed_noise_tokens,
        }
        calibrated.append(row)
        decisions.append({**row, "decision_reason": f"asr={asr_score:.2f}, ocr={ocr_score:.2f}, similarity={similarity:.2f}"})

    # Add high-confidence ASR rows not matched by any subtitle OCR so downstream steps still have complete speech coverage.
    for asr_item in asr_items:
        asr_id = int(asr_item.get("index") or 0)
        if asr_id in used_asr_ids:
            continue
        start = safe_float(asr_item.get("start"))
        end = safe_float(asr_item.get("end"), start)
        text = display_text(str(asr_item.get("text") or ""))
        if not text:
            continue
        calibrated.append({
            "id": f"asr_unmatched_{asr_id:03d}",
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "preferred_text": text,
            "preferred_source": "asr",
            "alignment_policy": "asr_primary",
            "ocr_text": "",
            "asr_text": text,
            "ocr_asr_similarity": 0.0,
            "asr_reliability": round(asr_score, 4),
            "subtitle_ocr_reliability": 0.0,
            "scene_index": scene_index_for_time(scenes, start),
            "source_asr_segment_ids": [asr_id] if asr_id else [],
            "source_ocr_item_ids": [],
            "source_keyframe_times": [],
            "visual_text_context": [str(item.get("text") or item.get("ocr_text") or "") for item in visual_text_for_range(start, end, visual_text_items)],
            "needs_review": False,
            "warnings": [],
        })

    return sorted(calibrated, key=lambda item: safe_float(item.get("start"))), decisions, calibrated


def resolve_paths(workspace: Path | None, output_dir: Path | None) -> Paths:
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    meta_dir = output_dir.expanduser().resolve() if output_dir else (resolved_workspace / "meta" if resolved_workspace else Path.cwd() / "meta")
    return Paths(workspace=resolved_workspace, meta_dir=meta_dir)


def optional_items(meta_dir: Path, filename: str, key: str = "items") -> list[dict[str, Any]]:
    path = meta_dir / filename
    if not path.exists():
        return []
    payload = read_json(path)
    value = payload.get(key) if isinstance(payload, dict) else None
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def run_calibrator(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    asr_sentence_path = paths.meta_dir / "asr_sentence_timeline.json"
    if (paths.meta_dir / "asr_provider_sentences_raw.json").exists():
        asr = build_asr_sentence_timeline_from_provider(read_json(paths.meta_dir / "asr_provider_sentences_raw.json"))
        write_json(asr_sentence_path, asr)
    elif asr_sentence_path.exists():
        asr = read_json(asr_sentence_path)
    else:
        asr = read_json(paths.meta_dir / "asr_segments.json")
    asr_quality = read_json(paths.meta_dir / "asr_quality.json")
    subtitles = optional_items(paths.meta_dir, "visual_subtitle_timeline.json")
    visual_text = optional_items(paths.meta_dir, "visual_text_timeline.json")
    scenes = optional_items(paths.meta_dir, "pyscenedetect_scenes.json", "scenes")
    timeline, decisions, calibrated = calibrate_subtitles(asr, asr_quality, subtitles, visual_text, scenes, args)
    common = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "workspace": str(paths.workspace) if paths.workspace else ""}
    write_json(paths.meta_dir / "subtitle_alignment_timeline.json", {**common, "items": timeline})
    write_json(paths.meta_dir / "subtitle_calibration_decisions.json", {**common, "items": decisions})
    write_json(paths.meta_dir / "visual_subtitle_timeline_calibrated.json", {**common, "items": calibrated})
    write_json(paths.meta_dir / "asr_subtitle_alignment.json", {**common, "items": timeline, "asr_quality": asr_quality})
    result = {
        **common,
        "status": "completed",
        "outputs": {
            "subtitle_alignment_timeline": str(paths.meta_dir / "subtitle_alignment_timeline.json"),
            "subtitle_calibration_decisions": str(paths.meta_dir / "subtitle_calibration_decisions.json"),
            "visual_subtitle_timeline_calibrated": str(paths.meta_dir / "visual_subtitle_timeline_calibrated.json"),
            "asr_subtitle_alignment": str(paths.meta_dir / "asr_subtitle_alignment.json"),
        },
        "counts": {"subtitle_candidates": len(subtitles), "alignment_items": len(timeline), "needs_review": sum(1 for item in timeline if item.get("needs_review"))},
    }
    write_json(paths.meta_dir / "05_2_subtitle_bidirectional_calibrator_result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Bidirectionally calibrate ASR and visual OCR subtitle timelines.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--match-window-seconds", type=float, default=2.0)
    parser.add_argument("--duplicate-similarity", type=float, default=0.72)
    parser.add_argument("--low-similarity", type=float, default=0.35)
    parser.add_argument("--high-asr-reliability", type=float, default=0.72)
    parser.add_argument("--ocr-fill-gap-reliability", type=float, default=0.62)
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None)
    result = run_calibrator(paths, args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result['outputs']['subtitle_alignment_timeline']}")


if __name__ == "__main__":
    main()
