from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - handled at runtime
    cv2 = None  # type: ignore

try:
    from Analysis_V1.simplified_chinese import SimplifiedChineseError, to_simplified_chinese
except Exception:  # pragma: no cover - direct script execution fallback
    from simplified_chinese import SimplifiedChineseError, to_simplified_chinese  # type: ignore


TOOL_NAME = "02_02_VideoSRTFrame"
TOOL_VERSION = "0.1.1"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
DEFAULT_SOURCE_VIDEO_REL = f"{CONTEXT_DIR_NAME}/Video_Source.mp4"
DEFAULT_METADATA_REL = f"{CONTEXT_DIR_NAME}/Video_Metadata.json"
DEFAULT_ASR_SEGMENTS_REL = f"{CONTEXT_DIR_NAME}/ASR_Segments.json"
TOOL_DIR_NAME = "S4_02_02_VideoSRTFrame"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_METADATA_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_1_Video_Metadata.json"
WORKING_ASR_SEGMENTS_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_2_ASR_Segments.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
WORKING_CANDIDATES_DIR_REL = f"{TOOL_DIR_NAME}/Working/Candidates"
OUTPUT_SRT_FRAME_MAP_REL = f"{TOOL_DIR_NAME}/Output/srt_frame_map.json"
OUTPUT_SENTENCE_INDEX_REL = f"{TOOL_DIR_NAME}/Output/srt_sentence_index.json"
OUTPUT_CALIBRATED_ITEMS_REL = f"{TOOL_DIR_NAME}/Output/calibrated_srt_items.json"
OUTPUT_FINAL_SRT_FRAME_ITEMS_REL = f"{TOOL_DIR_NAME}/Output/final_srt_frame_items.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_VISUAL_DIR_REL = "SessionOutput/visual"
SESSION_SUBTITLE_DIR_REL = "SessionOutput/subtitle"
SESSION_SRT_FRAMES_DIR_REL = f"{SESSION_VISUAL_DIR_REL}/srt_frames"
SESSION_SRT_FRAME_MAP_REL = f"{SESSION_VISUAL_DIR_REL}/srt_frame_map.json"
SESSION_SENTENCE_INDEX_REL = f"{SESSION_SUBTITLE_DIR_REL}/srt_sentence_index.json"
SESSION_CALIBRATED_ITEMS_REL = f"{SESSION_SUBTITLE_DIR_REL}/calibrated_srt_items.json"
SESSION_FINAL_SRT_FRAME_ITEMS_REL = f"{SESSION_SUBTITLE_DIR_REL}/final_srt_frame_items.json"
SECRET_PATTERNS = (
    "postgresql://",
    "postgresql+psycopg://",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth header",
    "cookie",
)


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class OCREngine(Protocol):
    name: str

    def recognize(self, image_path: Path) -> list[dict[str, Any]]:
        ...


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def module_spec_exists(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def current_runtime_has_ocr(requested: str) -> bool:
    if requested == "paddleocr":
        return module_spec_exists("paddleocr")
    if requested == "rapidocr":
        return module_spec_exists("rapidocr") or module_spec_exists("rapidocr_onnxruntime")
    if requested == "easyocr":
        return module_spec_exists("easyocr")
    if requested == "tesseract":
        return shutil.which("tesseract") is not None
    return (
        module_spec_exists("paddleocr")
        or module_spec_exists("rapidocr")
        or module_spec_exists("rapidocr_onnxruntime")
        or module_spec_exists("easyocr")
        or shutil.which("tesseract") is not None
    )


def python_can_import_rapidocr(python: Path) -> bool:
    if not python.exists():
        return False
    code = "import importlib.util, sys; sys.exit(0 if (importlib.util.find_spec('rapidocr') or importlib.util.find_spec('rapidocr_onnxruntime')) else 1)"
    try:
        proc = subprocess.run([str(python), "-c", code], check=False, capture_output=True, text=True, timeout=20)
    except Exception:
        return False
    return proc.returncode == 0


def analysis_v1_runtime_python_candidates() -> list[Path]:
    candidates: list[Path | None] = [
        Path(os.environ["OPENCREW_ANALYSIS_V1_PYTHON"]).expanduser() if os.environ.get("OPENCREW_ANALYSIS_V1_PYTHON") else None,
        Path(os.environ["OPENCREW_ANALYSIS_V1_RUNTIME_DIR"]).expanduser() / "bin" / "python" if os.environ.get("OPENCREW_ANALYSIS_V1_RUNTIME_DIR") else None,
        Path(os.environ.get("OPENCREW_DATA_DIR") or (Path.home() / ".opencrew")).expanduser() / "runtimes" / "analysis_v1_py312" / "bin" / "python",
        Path.home().expanduser() / ".opencrew" / "runtimes" / "analysis_v1_py312" / "bin" / "python",
        repo_root() / ".venv" / "bin" / "python",
        repo_root() / "backend" / ".venv" / "bin" / "python",
    ]
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        if candidate is None:
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def same_python_launcher(left: Path, right: Path) -> bool:
    return left.expanduser().absolute() == right.expanduser().absolute()


def maybe_reexec_with_rapidocr_runtime(requested: str) -> None:
    env_flag = "ANALYSIS_V1_RAPIDOCR_RUNTIME_REEXEC"
    if os.environ.get(env_flag) == "1" or current_runtime_has_ocr(requested):
        return
    if requested not in {"auto", "rapidocr"}:
        return
    python = next((candidate for candidate in analysis_v1_runtime_python_candidates() if python_can_import_rapidocr(candidate)), None)
    if python is None:
        return
    if same_python_launcher(python, Path(sys.executable)):
        return
    os.environ[env_flag] = "1"
    os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])


@dataclass(frozen=True)
class Args:
    workspace: str
    ocr_engine: str
    languages: str
    sample_interval_seconds: float
    min_candidates: int
    max_candidates: int
    subtitle_top_ratio: float
    min_confidence: float
    min_chars: int
    text_match_weight: float
    ocr_confidence_weight: float
    sharpness_weight: float
    center_weight: float
    min_match_score: float
    force: bool
    resume: bool
    print_progress: bool
    print_json: bool


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def relpath(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    try:
        return workspace.resolve()
    except Exception:
        return workspace.absolute()


def validate_workspace(workspace: Path) -> None:
    if not workspace.exists():
        raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise BlockedError("workspace_not_directory", f"Workspace is not a directory: {workspace}")


def load_variables(workspace: Path) -> dict[str, Any]:
    path = workspace / VARIABLES_REL
    if not path.exists():
        raise BlockedError("variables_missing", f"Required SessionContext file is missing: {VARIABLES_REL}.")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return payload


def load_video_metadata(workspace: Path, variables: dict[str, Any]) -> dict[str, Any]:
    raw = str(variables.get("video_metadata_path") or DEFAULT_METADATA_REL).strip()
    path = Path(raw)
    if path.is_absolute():
        raise BlockedError("video_metadata_path_not_relative", "video_metadata_path must be workspace-relative.")
    path = workspace / path
    if not path.exists():
        raise BlockedError("video_metadata_missing", f"Required video metadata is missing: {raw}. Run 01 first.")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("video_metadata_invalid", f"{raw} must contain a JSON object.")
    if float(payload.get("duration_seconds") or 0.0) <= 0:
        raise BlockedError("video_duration_missing", "Video metadata must contain duration_seconds.")
    if float(payload.get("fps") or 0.0) <= 0:
        raise BlockedError("video_fps_missing", "Video metadata must contain fps.")
    return payload


def resolve_source_video(workspace: Path, variables: dict[str, Any]) -> Path:
    raw = str(variables.get("source_video_path") or DEFAULT_SOURCE_VIDEO_REL).strip()
    path = Path(raw)
    if path.is_absolute():
        raise BlockedError("source_video_path_not_relative", "source_video_path must be workspace-relative.")
    path = workspace / path
    if not path.exists():
        raise BlockedError("source_video_missing", f"Source video is missing: {raw}. Run 00 first.")
    if not path.is_file():
        raise BlockedError("source_video_not_file", f"Source video is not a file: {raw}")
    return path


def load_asr_segments(workspace: Path, variables: dict[str, Any]) -> dict[str, Any]:
    raw = str(variables.get("asr_segments_path") or DEFAULT_ASR_SEGMENTS_REL).strip()
    path = Path(raw)
    if path.is_absolute():
        raise BlockedError("asr_segments_path_not_relative", "asr_segments_path must be workspace-relative.")
    path = workspace / path
    if not path.exists():
        raise BlockedError("asr_segments_missing", f"Required ASR segments are missing: {raw}. Run 02_01_AudioASR.py first.")
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise BlockedError("asr_segments_invalid", f"{raw} must contain a JSON object with a segments list.")
    if not payload["segments"]:
        raise BlockedError("asr_segments_empty", f"{raw} contains no SRT sentence segments.")
    return payload


def ensure_tool_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        WORKING_CANDIDATES_DIR_REL,
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "fingerprint": hashlib.sha256(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")).hexdigest(),
    }


def file_signature(workspace: Path, rels: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in rels:
        path = workspace / rel
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def config_signature(args: Args, input_signature: str) -> str:
    payload = {
        "tool_version": TOOL_VERSION,
        "input_signature": input_signature,
        "ocr_engine": args.ocr_engine,
        "languages": args.languages,
        "sample_interval_seconds": float(args.sample_interval_seconds),
        "min_candidates": int(args.min_candidates),
        "max_candidates": int(args.max_candidates),
        "subtitle_top_ratio": float(args.subtitle_top_ratio),
        "min_confidence": float(args.min_confidence),
        "min_chars": int(args.min_chars),
        "text_match_weight": float(args.text_match_weight),
        "ocr_confidence_weight": float(args.ocr_confidence_weight),
        "sharpness_weight": float(args.sharpness_weight),
        "center_weight": float(args.center_weight),
        "min_match_score": float(args.min_match_score),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    cleaned = re.sub(r"\s+", "", str(text or ""))
    cleaned = re.sub(r"[|｜_＿=]+", "", cleaned)
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "", cleaned)
    return cleaned.strip().lower()


def dedupe_text_key(text: str) -> str:
    cleaned = normalize_text(text)
    cleaned = re.sub(r"^\d+", "", cleaned)
    cleaned = re.sub(r"^[a-z]{1,3}(?=[\u4e00-\u9fff])", "", cleaned)
    return cleaned


def display_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def text_similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    return float(SequenceMatcher(None, left_norm, right_norm).ratio())


def normalized_text_with_source_map(text: str) -> tuple[str, list[int]]:
    normalized_chars: list[str] = []
    source_indexes: list[int] = []
    for index, char in enumerate(str(text or "")):
        normalized = normalize_text(char)
        if not normalized:
            continue
        normalized_chars.append(normalized)
        source_indexes.extend([index] * len(normalized))
    return "".join(normalized_chars), source_indexes


def strip_dialogue_edges(text: str) -> str:
    return re.sub(r"^[\s,，。.!！?？;；:：、\"'“”‘’（）()【】\[\]]+|[\s,，。.!！?？;；:：、\"'“”‘’（）()【】\[\]]+$", "", str(text or "")).strip()


def retained_subtitle_tail(current_norm: str, previous_norms: list[str]) -> tuple[str, dict[str, Any]]:
    best_tail = current_norm
    best_meta: dict[str, Any] = {"retained_prefix_removed": False}
    best_removed_len = 0
    for previous_norm in previous_norms:
        if len(previous_norm) < 4 or len(current_norm) <= len(previous_norm):
            continue
        start = current_norm.find(previous_norm)
        if start < 0:
            continue
        tail = current_norm[start + len(previous_norm):]
        if len(tail) < 2:
            continue
        removed_len = len(previous_norm)
        if removed_len > best_removed_len:
            best_tail = tail
            best_removed_len = removed_len
            best_meta = {
                "retained_prefix_removed": True,
                "retained_prefix_length": removed_len,
                "retained_prefix_offset": start,
            }
    return best_tail, best_meta


def best_text_span(haystack_norm: str, needle_norm: str, min_start: int = 0) -> dict[str, Any] | None:
    haystack = str(haystack_norm or "")
    needle = str(needle_norm or "")
    if not haystack or not needle:
        return None
    start_floor = max(0, min(int(min_start), len(haystack)))
    exact_at = haystack.find(needle, start_floor)
    if exact_at >= 0:
        return {"start": exact_at, "end": exact_at + len(needle), "score": 1.0, "match": haystack[exact_at:exact_at + len(needle)]}
    best: dict[str, Any] | None = None
    min_common = max(2, min(3, len(needle)))
    max_extra = max(4, min(10, len(needle)))
    min_length = max(1, len(needle) - max(2, len(needle) // 3))
    max_length = max(min_length, len(needle) + max_extra)
    for start in range(start_floor, len(haystack)):
        for end in range(start + min_length, min(len(haystack), start + max_length) + 1):
            candidate = haystack[start:end]
            matcher = SequenceMatcher(None, candidate, needle)
            common = sum(block.size for block in matcher.get_matching_blocks())
            if common < min_common:
                continue
            ratio = float(matcher.ratio())
            coverage = common / max(1, len(needle))
            if ratio < 0.55 or coverage < 0.5:
                continue
            score_tuple = (round(ratio, 4), round(coverage, 4), common, -abs(len(candidate) - len(needle)), -start)
            if best is None or score_tuple > best["score_tuple"]:
                best = {
                    "start": start,
                    "end": end,
                    "score": round(ratio, 4),
                    "coverage": round(coverage, 4),
                    "match": candidate,
                    "score_tuple": score_tuple,
                }
    if best is None:
        return None
    best.pop("score_tuple", None)
    return best


def slice_by_normalized_span(raw_text: str, source_indexes: list[int], start: int, end: int) -> str:
    if not source_indexes or end <= start:
        return ""
    safe_start = max(0, min(start, len(source_indexes) - 1))
    safe_end = max(0, min(end - 1, len(source_indexes) - 1))
    raw_start = source_indexes[safe_start]
    raw_end = source_indexes[safe_end] + 1
    return strip_dialogue_edges(str(raw_text or "")[raw_start:raw_end])


def repair_retained_subtitle_event_texts(asr_text: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(events) <= 1:
        return events
    parent_norm, parent_source_indexes = normalized_text_with_source_map(asr_text)
    if len(parent_norm) < 4:
        return events

    previous_norms: list[str] = []
    evidences: list[dict[str, Any]] = []
    retained_prefix_seen = False
    for event in events:
        current_text = str(event.get("ocr_text") or event.get("text") or "")
        current_norm, _ = normalized_text_with_source_map(current_text)
        evidence_norm, retained_meta = retained_subtitle_tail(current_norm, previous_norms)
        retained_prefix_seen = retained_prefix_seen or bool(retained_meta.get("retained_prefix_removed"))
        evidences.append({
            "current_norm": current_norm,
            "evidence_norm": evidence_norm,
            "retained_meta": retained_meta,
        })
        if current_norm:
            previous_norms.append(current_norm)

    if not retained_prefix_seen:
        return events

    anchors: list[dict[str, Any] | None] = []
    search_start = 0
    for evidence in evidences:
        anchor = best_text_span(parent_norm, evidence["evidence_norm"], search_start)
        if anchor is None and evidence["current_norm"] != evidence["evidence_norm"]:
            anchor = best_text_span(parent_norm, evidence["current_norm"], search_start)
        anchors.append(anchor)
        if anchor is not None:
            search_start = max(search_start, int(anchor["end"]))

    if sum(1 for anchor in anchors if anchor is not None) < 2:
        return events

    repaired_events: list[dict[str, Any]] = []
    previous_end = 0
    for index, event in enumerate(events):
        anchor = anchors[index]
        if index == 0:
            if anchor is not None and int(anchor["start"]) <= 1 and int(anchor["end"]) > previous_end:
                end = int(anchor["end"])
            else:
                next_anchor = anchors[index + 1] if index + 1 < len(anchors) else None
                end = int(next_anchor["start"]) if next_anchor is not None and int(next_anchor["start"]) > previous_end else previous_end
        elif index == len(events) - 1:
            end = len(parent_norm)
        else:
            next_anchor = anchors[index + 1]
            if next_anchor is not None and int(next_anchor["start"]) > previous_end:
                end = int(next_anchor["start"])
            elif anchor is not None and int(anchor["end"]) > previous_end:
                end = int(anchor["end"])
            else:
                end = previous_end

        end = max(previous_end, min(len(parent_norm), end))
        span_start = previous_end
        repaired_text = slice_by_normalized_span(asr_text, parent_source_indexes, span_start, end)
        if not normalize_text(repaired_text):
            repaired_text = str(event.get("text") or event.get("ocr_text") or "")
        previous_end = end

        next_event = {**event}
        original_text = str(event.get("text") or "")
        if repaired_text and repaired_text != original_text:
            next_event["text"] = repaired_text
            calibration = dict(next_event.get("calibration") or {})
            calibration["text_source"] = "asr_repaired_ocr_subtitle_event"
            calibration["asr_repair"] = {
                "policy": "strip_retained_visual_prefix_and_slice_parent_asr",
                "original_event_text": original_text,
                "retained_prefix_removed": bool(evidences[index]["retained_meta"].get("retained_prefix_removed")),
                "evidence_text": evidences[index]["evidence_norm"],
                "asr_span": [span_start, end],
                "anchor": anchors[index],
            }
            next_event["calibration"] = calibration
        repaired_events.append(next_event)
    return repaired_events


def looks_like_noise(text: str, min_chars: int) -> bool:
    compact = normalize_text(text)
    if len(compact) < min_chars:
        return True
    return bool(re.fullmatch(r"[\W_\d]+", compact))


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def bbox_points(bbox: Any) -> list[tuple[float, float]]:
    if hasattr(bbox, "tolist"):
        bbox = bbox.tolist()
    if not isinstance(bbox, list):
        return []
    if len(bbox) == 4 and all(isinstance(value, (int, float)) for value in bbox):
        left, top, right, bottom = [float(value) for value in bbox]
        return [(left, top), (right, top), (right, bottom), (left, bottom)]
    points: list[tuple[float, float]] = []
    for point in bbox:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                points.append((float(point[0]), float(point[1])))
            except Exception:
                continue
    return points


def bbox_rect(block: dict[str, Any]) -> tuple[float, float, float, float] | None:
    points = bbox_points(block.get("bbox"))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def image_size(image_path: Path) -> tuple[int, int]:
    if cv2 is not None:
        image = cv2.imread(str(image_path))
        if image is not None:
            height, width = image.shape[:2]
            return int(width), int(height)
    return 0, 0


def layout_features(block: dict[str, Any], width: int, height: int) -> dict[str, float]:
    points = bbox_points(block.get("bbox"))
    if not points or width <= 0 or height <= 0:
        return {"center_x": 0.0, "center_y": 0.0, "width_ratio": 0.0, "height_ratio": 0.0, "area_ratio": 0.0}
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    box_width = max(0.0, right - left)
    box_height = max(0.0, bottom - top)
    return {
        "center_x": round(((left + right) / 2.0) / width, 4),
        "center_y": round(((top + bottom) / 2.0) / height, 4),
        "width_ratio": round(box_width / width, 4),
        "height_ratio": round(box_height / height, 4),
        "area_ratio": round((box_width * box_height) / max(1.0, width * height), 6),
    }


def is_subtitle_block(block: dict[str, Any], args: Args) -> bool:
    text = str(block.get("text") or "").strip()
    if float(block.get("confidence") or 0.0) < args.min_confidence:
        return False
    if looks_like_noise(text, args.min_chars):
        return False
    layout = block.get("layout") or {}
    center_y = float(layout.get("center_y") or 0.0)
    center_x = float(layout.get("center_x") or 0.0)
    width_ratio = float(layout.get("width_ratio") or 0.0)
    return center_y >= args.subtitle_top_ratio and 0.05 <= center_x <= 0.95 and width_ratio >= 0.05


def block_left(block: dict[str, Any]) -> float:
    rect = bbox_rect(block)
    return rect[0] if rect else 0.0


def block_center_y(block: dict[str, Any], height: int) -> float:
    layout = block.get("layout") or {}
    center_y = float(layout.get("center_y") or 0.0)
    if center_y <= 1.0:
        return center_y * max(1, height)
    return center_y


def block_height(block: dict[str, Any]) -> float:
    rect = bbox_rect(block)
    if not rect:
        return 0.0
    return max(0.0, rect[3] - rect[1])


def group_blocks_by_subtitle_line(blocks: list[dict[str, Any]], height: int) -> list[list[dict[str, Any]]]:
    if not blocks:
        return []
    sorted_blocks = sorted(blocks, key=lambda item: (block_center_y(item, height), block_left(item)))
    heights = [block_height(block) for block in sorted_blocks if block_height(block) > 0]
    threshold = max(8.0, min(36.0, median(heights) * 0.65 if heights else height * 0.018))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_center = 0.0
    for block in sorted_blocks:
        center = block_center_y(block, height)
        if not current:
            current = [block]
            current_center = center
            continue
        if abs(center - current_center) <= threshold:
            current.append(block)
            current_center = average([block_center_y(item, height) for item in current])
        else:
            groups.append(sorted(current, key=block_left))
            current = [block]
            current_center = center
    if current:
        groups.append(sorted(current, key=block_left))
    return groups


def merge_line_blocks(blocks: list[dict[str, Any]], width: int, height: int) -> dict[str, Any]:
    ordered = sorted(blocks, key=block_left)
    text = display_text(" ".join(str(block.get("text") or "") for block in ordered))
    rects = [bbox_rect(block) for block in ordered]
    rects = [rect for rect in rects if rect is not None]
    if rects:
        left = min(rect[0] for rect in rects)
        top = min(rect[1] for rect in rects)
        right = max(rect[2] for rect in rects)
        bottom = max(rect[3] for rect in rects)
        bbox = [(left, top), (right, top), (right, bottom), (left, bottom)]
    else:
        bbox = []
    merged = {
        "text": text,
        "confidence": round(average([float(block.get("confidence") or 0.0) for block in ordered]), 4),
        "bbox": bbox,
        "blocks": ordered,
    }
    merged["layout"] = layout_features(merged, width, height)
    return merged


def candidate_line_windows(line_groups: list[list[dict[str, Any]]], width: int, height: int) -> list[dict[str, Any]]:
    lines = [merge_line_blocks(group, width, height) for group in line_groups]
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[tuple[int, ...], str]] = set()
    for index, group in enumerate(line_groups):
        ordered = sorted(group, key=block_left)
        for start in range(len(ordered)):
            for end in range(start + 1, len(ordered) + 1):
                window = ordered[start:end]
                line = merge_line_blocks(window, width, height)
                key = ((index,), normalize_text(str(line.get("text") or "")))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({"line_count": 1, "line_indexes": [index], **line})
    for index in range(len(lines) - 1):
        top = lines[index]
        bottom = lines[index + 1]
        top_layout = top.get("layout") or {}
        bottom_layout = bottom.get("layout") or {}
        gap = float(bottom_layout.get("center_y") or 0.0) - float(top_layout.get("center_y") or 0.0)
        if gap > 0.16:
            continue
        merged = merge_line_blocks((top.get("blocks") or []) + (bottom.get("blocks") or []), width, height)
        key = ((index, index + 1), normalize_text(str(merged.get("text") or "")))
        if key not in seen:
            seen.add(key)
            candidates.append({"line_count": 2, "line_indexes": [index, index + 1], **merged})
    return candidates


def select_dialogue_line_candidate(candidates: list[dict[str, Any]], expected_text: str) -> dict[str, Any] | None:
    if not candidates:
        return None
    expected_norm = normalize_text(expected_text)
    scored: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates:
        layout = candidate.get("layout") or {}
        text = str(candidate.get("text") or "")
        confidence = float(candidate.get("confidence") or 0.0)
        center_x_score = max(0.0, 1.0 - abs(float(layout.get("center_x") or 0.5) - 0.5) * 2.0)
        width_score = min(1.0, float(layout.get("width_ratio") or 0.0) / 0.45)
        bottom_score = min(1.0, max(0.0, (float(layout.get("center_y") or 0.0) - 0.52) / 0.35))
        match_score = text_similarity(expected_text, text) if expected_norm else 0.0
        score = (0.72 * match_score + 0.16 * confidence + 0.07 * center_x_score + 0.03 * width_score + 0.02 * bottom_score) if expected_norm else (0.55 * confidence + 0.2 * center_x_score + 0.15 * width_score + 0.1 * bottom_score)
        enriched = {
            **candidate,
            "dialogue_line_score": round(score, 4),
            "dialogue_line_match_score": round(match_score, 4),
        }
        scored.append((score, enriched))
    return max(scored, key=lambda item: (item[0], float((item[1].get("layout") or {}).get("center_y") or 0.0)))[1]


def find_local_cjk_font() -> str | None:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/Supplemental/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/STHeiti Medium.ttc",
        "/System/Library/Fonts/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for raw_path in candidates:
        if Path(raw_path).exists():
            return raw_path
    return None


class PaddleOCREngine:
    name = "paddleocr"

    def __init__(self, languages: list[str]) -> None:
        from paddleocr import PaddleOCR  # type: ignore

        lang = "ch" if any(item.lower() in {"ch", "zh", "cn"} for item in languages) else (languages[0] if languages else "ch")
        self._ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

    def recognize(self, image_path: Path) -> list[dict[str, Any]]:
        raw = self._ocr.ocr(str(image_path), cls=True)
        blocks: list[dict[str, Any]] = []
        groups = raw or []
        if groups and isinstance(groups[0], list) and groups[0] and isinstance(groups[0][0], list) and len(groups) == 1:
            groups = groups[0]
        for item in groups:
            try:
                box = item[0]
                text = str(item[1][0] or "").strip()
                confidence = float(item[1][1] or 0.0)
            except Exception:
                continue
            if text:
                if hasattr(box, "tolist"):
                    box = box.tolist()
                blocks.append({"text": text, "confidence": round(confidence, 4), "bbox": box})
        return blocks


class RapidOCREngine:
    name = "rapidocr"

    def __init__(self, languages: list[str]) -> None:
        self.font_path = find_local_cjk_font()
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except Exception:
            from rapidocr import RapidOCR  # type: ignore

        if self.font_path:
            try:
                self._ocr = RapidOCR(params={"Global.font_path": self.font_path})
                return
            except TypeError:
                pass
        self._ocr = RapidOCR()

    def recognize(self, image_path: Path) -> list[dict[str, Any]]:
        raw = self._ocr(str(image_path))
        if hasattr(raw, "boxes") and hasattr(raw, "txts") and hasattr(raw, "scores"):
            boxes = getattr(raw, "boxes", None)
            txts = getattr(raw, "txts", None)
            scores = getattr(raw, "scores", None)
            result = zip([] if boxes is None else boxes, [] if txts is None else txts, [] if scores is None else scores)
        else:
            result = raw[0] if isinstance(raw, tuple) else raw
        blocks: list[dict[str, Any]] = []
        for item in result or []:
            try:
                box, text, confidence = item[0], str(item[1] or "").strip(), float(item[2] or 0.0)
            except Exception:
                continue
            if text:
                blocks.append({"text": text, "confidence": round(confidence, 4), "bbox": box})
        return blocks


class EasyOCREngine:
    name = "easyocr"

    def __init__(self, languages: list[str]) -> None:
        import easyocr  # type: ignore

        normalized = []
        for item in languages or ["ch", "en"]:
            value = item.lower()
            if value in {"ch", "zh", "cn"}:
                normalized.append("ch_sim")
            elif value == "en":
                normalized.append("en")
        self._reader = easyocr.Reader(normalized or ["ch_sim", "en"], gpu=False)

    def recognize(self, image_path: Path) -> list[dict[str, Any]]:
        raw = self._reader.readtext(str(image_path), detail=1, paragraph=False)
        return [{"text": str(item[1] or "").strip(), "confidence": round(float(item[2] or 0.0), 4), "bbox": item[0]} for item in raw or [] if str(item[1] or "").strip()]


class TesseractEngine:
    name = "tesseract"

    def __init__(self, languages: list[str]) -> None:
        if shutil.which("tesseract") is None:
            raise RuntimeError("tesseract command not found")
        self._language = "+".join(["chi_sim" if item.lower() in {"ch", "zh", "cn"} else item for item in (languages or ["ch", "en"])])

    def recognize(self, image_path: Path) -> list[dict[str, Any]]:
        proc = subprocess.run(["tesseract", str(image_path), "stdout", "-l", self._language, "--psm", "6", "tsv"], check=False, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "tesseract failed")
        blocks: list[dict[str, Any]] = []
        for line in [line for line in proc.stdout.splitlines() if line.strip()][1:]:
            parts = line.split("\t")
            if len(parts) < 12 or not parts[11].strip():
                continue
            try:
                confidence = max(0.0, float(parts[10]) / 100.0)
                left, top, width, height = [int(float(parts[index])) for index in (6, 7, 8, 9)]
            except Exception:
                confidence = 0.0
                left = top = width = height = 0
            blocks.append({"text": parts[11].strip(), "confidence": round(confidence, 4), "bbox": [left, top, left + width, top + height]})
        return blocks


def build_engine(requested: str, languages: list[str]) -> tuple[OCREngine, list[dict[str, str]]]:
    candidates = [requested] if requested != "auto" else ["paddleocr", "rapidocr", "easyocr", "tesseract"]
    failures: list[dict[str, str]] = []
    for name in candidates:
        try:
            if name == "paddleocr":
                return PaddleOCREngine(languages), failures
            if name == "rapidocr":
                return RapidOCREngine(languages), failures
            if name == "easyocr":
                return EasyOCREngine(languages), failures
            if name == "tesseract":
                return TesseractEngine(languages), failures
        except Exception as exc:
            failures.append({"engine": name, "error": f"{type(exc).__name__}: {exc}"})
    raise BlockedError("ocr_engine_missing", "No OCR engine is available. Tried: " + json.dumps(failures, ensure_ascii=False))


def stable_sentence_id(segment: dict[str, Any], order: int) -> str:
    raw = str(segment.get("sentence_id") or segment.get("srt_sentence_id") or "").strip()
    if raw:
        return raw
    index = int(segment.get("index") or order)
    return f"srt_{index:04d}"


def normalized_segments(asr_payload: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order, segment in enumerate(asr_payload.get("segments") or [], start=1):
        if not isinstance(segment, dict):
            continue
        text = display_text(str(segment.get("text") or ""))
        if not text:
            continue
        start = max(0.0, float(segment.get("start") or 0.0))
        end = min(duration, float(segment.get("end") or start))
        if end < start:
            end = start
        rows.append({
            "sentence_id": stable_sentence_id(segment, order),
            "sentence_order": order,
            "source_asr_index": segment.get("index") or order,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "confidence": segment.get("confidence"),
            "time_source": segment.get("time_source"),
        })
    return rows


def candidate_times(start: float, end: float, duration: float, args: Args) -> list[float]:
    if end < start:
        end = start
    window = max(0.001, end - start)
    max_candidates = max(1, int(args.max_candidates))
    min_candidates = max(1, min(int(args.min_candidates), max_candidates))
    interval = max(0.05, float(args.sample_interval_seconds))
    count = int(window / interval) + 1
    count = max(min_candidates, min(max_candidates, count))
    if count <= 1:
        points = [(start + end) / 2.0]
    else:
        points = [start + (window * index / (count - 1)) for index in range(count)]
    return sorted({round(min(max(0.0, value), max(0.0, duration - 0.001)), 3) for value in points})


def extract_candidate_frame(video_path: Path, fps: float, time_seconds: float, output_path: Path) -> tuple[int, str]:
    if cv2 is None:
        raise BlockedError("opencv_missing", "opencv-python is required to extract SRT frames.")
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise BlockedError("opencv_video_open_failed", f"Failed to open video with OpenCV: {video_path}")
        frame_index = max(0, int(round(time_seconds * fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise BlockedError("frame_extract_failed", f"Failed to read frame at {time_seconds:.3f}s.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output_path), frame):
            raise RuntimeError(f"Failed to write candidate frame: {output_path}")
        return int(frame_index), str(output_path)
    finally:
        cap.release()


def subtitle_region_sharpness(image_path: Path, subtitle_top_ratio: float) -> float:
    if cv2 is None:
        return 0.0
    image = cv2.imread(str(image_path))
    if image is None:
        return 0.0
    height = image.shape[0]
    top = int(max(0.0, min(0.95, subtitle_top_ratio)) * height)
    region = image[top:, :]
    if region.size == 0:
        region = image
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return round(min(1.0, variance / 900.0), 4)


def recognize_subtitle_blocks(image_path: Path, engine: OCREngine, args: Args, expected_text: str = "") -> tuple[str, float, list[dict[str, Any]], float]:
    raw_blocks = engine.recognize(image_path)
    width, height = image_size(image_path)
    blocks: list[dict[str, Any]] = []
    for block in raw_blocks:
        text = str(block.get("text") or "").strip()
        layout = layout_features(block, width, height)
        item = {**block, "text": text, "layout": layout}
        if not is_subtitle_block(item, args):
            continue
        blocks.append(item)
    line_groups = group_blocks_by_subtitle_line(blocks, height)
    line_candidates = candidate_line_windows(line_groups, width, height)
    selected = select_dialogue_line_candidate(line_candidates, expected_text)
    if selected is None:
        return "", 0.0, [], 0.0
    selected_blocks = selected.get("blocks") or []
    selected_layout = selected.get("layout") or {}
    center_score = round(max(0.0, 1.0 - abs(float(selected_layout.get("center_x") or 0.5) - 0.5) * 2.0), 4)
    text = display_text(str(selected.get("text") or ""))
    confidence = round(float(selected.get("confidence") or 0.0), 4)
    return text, confidence, selected_blocks, center_score


def weighted_score(match_score: float, ocr_confidence: float, sharpness: float, center_score: float, args: Args) -> float:
    weights = {
        "text": max(0.0, float(args.text_match_weight)),
        "ocr": max(0.0, float(args.ocr_confidence_weight)),
        "sharpness": max(0.0, float(args.sharpness_weight)),
        "center": max(0.0, float(args.center_weight)),
    }
    total = sum(weights.values()) or 1.0
    score = (
        weights["text"] * match_score
        + weights["ocr"] * ocr_confidence
        + weights["sharpness"] * sharpness
        + weights["center"] * center_score
    ) / total
    return round(max(0.0, min(1.0, score)), 4)


def is_probably_partial_ocr(asr_text: str, ocr_text: str, match_score: float) -> bool:
    asr_norm = normalize_text(asr_text)
    ocr_norm = normalize_text(ocr_text)
    if not asr_norm or not ocr_norm:
        return False
    return len(ocr_norm) < len(asr_norm) * 0.68 and match_score < 0.88


def choose_final_text(asr_text: str, ocr_text: str, match_score: float, ocr_confidence: float, args: Args) -> tuple[str, dict[str, Any]]:
    if ocr_text and ocr_confidence >= 0.88 and match_score >= float(args.min_match_score) and not is_probably_partial_ocr(asr_text, ocr_text, match_score):
        return ocr_text, {"text_source": "ocr_preferred_high_confidence", "needs_review": False}
    if match_score >= 0.92:
        return asr_text, {"text_source": "asr_ocr_agree", "needs_review": False}
    if match_score >= float(args.min_match_score):
        return asr_text, {"text_source": "asr_preferred_ocr_supported_partial", "needs_review": True}
    if ocr_text and ocr_confidence >= 0.9 and match_score >= 0.38 and len(normalize_text(ocr_text)) >= len(normalize_text(asr_text)) * 0.85:
        return ocr_text, {"text_source": "ocr_corrected_high_confidence", "needs_review": True}
    return asr_text, {"text_source": "asr_needs_review", "needs_review": True}


def same_subtitle_event(left_text: str, right_text: str) -> bool:
    left = normalize_text(left_text)
    right = normalize_text(right_text)
    if not left or not right:
        return False
    if left in right or right in left:
        return min(len(left), len(right)) >= max(2, max(len(left), len(right)) * 0.55)
    return text_similarity(left, right) >= 0.74


def is_duplicate_subtitle_text(left_text: str, right_text: str) -> bool:
    left = dedupe_text_key(left_text)
    right = dedupe_text_key(right_text)
    if not left or not right:
        return False
    if left == right:
        return True
    if left in right or right in left:
        return min(len(left), len(right)) >= max(2, max(len(left), len(right)) * 0.78)
    return float(SequenceMatcher(None, left, right).ratio()) >= 0.86


def better_subtitle_event(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_score = (float(left.get("text_match_score") or 0.0), float(left.get("ocr_confidence") or 0.0), float(left.get("score") or 0.0))
    right_score = (float(right.get("text_match_score") or 0.0), float(right.get("ocr_confidence") or 0.0), float(right.get("score") or 0.0))
    return right if right_score > left_score else left


def merge_subtitle_event_duplicate(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    chosen = better_subtitle_event(left, right)
    merged = {**left, **{key: chosen.get(key) for key in ("text", "ocr_text", "frame_path", "frame_time", "frame", "score", "text_match_score", "ocr_confidence")}}
    merged["start"] = min(float(left.get("start") or 0.0), float(right.get("start") or 0.0))
    merged["end"] = max(float(left.get("end") or merged["start"]), float(right.get("end") or merged["start"]))
    merged["candidate_count"] = int(left.get("candidate_count") or 0) + int(right.get("candidate_count") or 0)
    merged["calibration"] = {
        **(chosen.get("calibration") or {}),
        "dedupe_policy": "merged_adjacent_duplicate_subtitle_event",
        "merged_dialogue_unit_ids": [left.get("dialogue_unit_id"), right.get("dialogue_unit_id")],
    }
    return merged


def dedupe_subtitle_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for event in events:
        if deduped and is_duplicate_subtitle_text(str(deduped[-1].get("text") or ""), str(event.get("text") or "")):
            deduped[-1] = merge_subtitle_event_duplicate(deduped[-1], event)
        else:
            deduped.append(event)
    return deduped


def better_calibrated_item(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_cal = left.get("calibration") or {}
    right_cal = right.get("calibration") or {}
    left_score = (
        0 if left_cal.get("needs_review") else 1,
        float(left.get("ocr_confidence") or 0.0),
        float(left.get("text_match_score") or 0.0),
        len(dedupe_text_key(str(left.get("text") or ""))),
    )
    right_score = (
        0 if right_cal.get("needs_review") else 1,
        float(right.get("ocr_confidence") or 0.0),
        float(right.get("text_match_score") or 0.0),
        len(dedupe_text_key(str(right.get("text") or ""))),
    )
    return right if right_score > left_score else left


def merge_calibrated_duplicate(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    chosen = better_calibrated_item(left, right)
    merged = {**left, **{key: chosen.get(key) for key in ("text", "ocr_text", "frame_path", "frame_time")}}
    merged["start"] = min(float(left.get("start") or 0.0), float(right.get("start") or 0.0))
    merged["end"] = max(float(left.get("end") or merged["start"]), float(right.get("end") or merged["start"]))
    merged["calibration"] = {
        **(chosen.get("calibration") or {}),
        "dedupe_policy": "merged_adjacent_duplicate_calibrated_item",
        "merged_sentence_ids": [left.get("sentence_id"), right.get("sentence_id")],
    }
    return merged


def dedupe_calibrated_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deduped: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for item in items:
        if deduped and is_duplicate_subtitle_text(str(deduped[-1].get("text") or ""), str(item.get("text") or "")):
            previous = deduped[-1]
            deduped[-1] = merge_calibrated_duplicate(previous, item)
            duplicates.append({
                "removed_sentence_id": item.get("sentence_id"),
                "merged_into_sentence_id": previous.get("sentence_id"),
                "text": item.get("text"),
                "reason": "adjacent_duplicate_text",
            })
        else:
            deduped.append(item)
    return deduped, duplicates


def build_final_srt_frame_items(calibrated: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in calibrated.get("items") or []:
        start = round(float(item.get("start") or 0.0), 3)
        end = round(float(item.get("end") or start), 3)
        duration = round(max(0.0, end - start), 3)
        items.append({
            "srt_id": item.get("sentence_id"),
            "dialogue": to_simplified_chinese(item.get("text") or ""),
            "image_path": str(item.get("frame_path") or ""),
            "start": start,
            "end": end,
            "duration": duration,
        })
    return {
        "schema_version": "analysis_v1_final_srt_frame_items_0.2",
        "items": items,
    }


def normalize_calibrated_items_to_simplified(calibrated: dict[str, Any]) -> int:
    changed = 0
    for item in calibrated.get("items") or []:
        if not isinstance(item, dict):
            continue
        before = str(item.get("text") or "")
        after = to_simplified_chinese(before)
        if after != before:
            item["text"] = after
            changed += 1
    return changed


def build_ocr_subtitle_events(workspace: Path, sentence_id: str, start: float, end: float, asr_text: str, candidates: list[dict[str, Any]], args: Args) -> list[dict[str, Any]]:
    usable = [item for item in sorted(candidates, key=lambda row: float(row.get("time") or 0.0)) if item.get("path") and normalize_text(str(item.get("ocr_text") or ""))]
    groups: list[list[dict[str, Any]]] = []
    for candidate in usable:
        text = str(candidate.get("ocr_text") or "")
        if not groups:
            groups.append([candidate])
            continue
        previous_text = str(groups[-1][-1].get("ocr_text") or "")
        if same_subtitle_event(previous_text, text):
            groups[-1].append(candidate)
        else:
            groups.append([candidate])

    filtered_groups: list[list[dict[str, Any]]] = []
    for group in groups:
        selected = max(group, key=lambda item: (float(item.get("score") or 0.0), float(item.get("ocr_confidence") or 0.0), float(item.get("time") or 0.0)))
        match_score = text_similarity(asr_text, str(selected.get("ocr_text") or ""))
        if match_score >= 0.38:
            filtered_groups.append(group)

    groups = []
    for group in filtered_groups:
        if groups and same_subtitle_event(str(groups[-1][-1].get("ocr_text") or ""), str(group[0].get("ocr_text") or "")):
            groups[-1].extend(group)
        else:
            groups.append(group)

    events: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        selected = max(group, key=lambda item: (float(item.get("score") or 0.0), float(item.get("ocr_confidence") or 0.0), float(item.get("time") or 0.0)))
        event_id = f"{sentence_id}_{index:02d}"
        event_path = workspace / SESSION_SRT_FRAMES_DIR_REL / f"{event_id}.jpg"
        event_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(workspace / str(selected["path"]), event_path)
        next_group = groups[index] if index < len(groups) else []
        event_start = float(group[0].get("time") or start)
        event_end = float(next_group[0].get("time") if next_group else (group[-1].get("time") or end))
        if event_end <= event_start:
            event_end = min(end, event_start + 0.001)
        match_score = text_similarity(asr_text, str(selected.get("ocr_text") or ""))
        ocr_confidence = float(selected.get("ocr_confidence") or 0.0)
        if len(groups) > 1 and ocr_confidence >= 0.82:
            final_text = str(selected.get("ocr_text") or "")
            calibration = {
                "text_source": "ocr_subtitle_event",
                "needs_review": match_score < 0.45,
                "split_from_parent_asr": True,
            }
        else:
            final_text, calibration = choose_final_text(asr_text, str(selected.get("ocr_text") or ""), match_score, ocr_confidence, args)
        events.append({
            "dialogue_unit_id": event_id,
            "parent_sentence_id": sentence_id,
            "event_order": index,
            "start": round(max(start, event_start), 3),
            "end": round(min(end, event_end), 3),
            "text": final_text,
            "asr_parent_text": asr_text,
            "ocr_text": selected.get("ocr_text") or "",
            "frame_path": relpath(event_path, workspace),
            "frame_time": selected.get("time"),
            "frame": selected.get("frame"),
            "score": selected.get("score"),
            "text_match_score": round(match_score, 4),
            "ocr_confidence": selected.get("ocr_confidence"),
            "candidate_count": len(group),
            "calibration": {
                **calibration,
                "time_source": "ocr_event_within_asr_window",
                "frame_binding_key": "dialogue_unit_id",
            },
        })
    return dedupe_subtitle_events(repair_retained_subtitle_event_texts(asr_text, events))


def select_sentence_frame(workspace: Path, video_path: Path, fps: float, duration: float, segment: dict[str, Any], engine: OCREngine, args: Args) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sentence_id = str(segment["sentence_id"])
    start = float(segment["start"])
    end = float(segment["end"])
    asr_text = str(segment["text"])
    candidates: list[dict[str, Any]] = []
    for candidate_index, time_value in enumerate(candidate_times(start, end, duration, args), start=1):
        candidate_path = workspace / WORKING_CANDIDATES_DIR_REL / sentence_id / f"candidate_{candidate_index:03d}_t{time_value:.3f}.jpg"
        try:
            frame_index, saved_path = extract_candidate_frame(video_path, fps, time_value, candidate_path)
            ocr_text, ocr_confidence, ocr_blocks, center_score = recognize_subtitle_blocks(Path(saved_path), engine, args, asr_text)
            match_score = text_similarity(asr_text, ocr_text)
            sharpness = subtitle_region_sharpness(Path(saved_path), args.subtitle_top_ratio)
            score = weighted_score(match_score, ocr_confidence, sharpness, center_score, args)
            candidates.append({
                "candidate_index": candidate_index,
                "time": round(time_value, 3),
                "frame": frame_index,
                "path": relpath(Path(saved_path), workspace),
                "ocr_text": ocr_text,
                "ocr_confidence": ocr_confidence,
                "text_match_score": round(match_score, 4),
                "subtitle_region_sharpness": sharpness,
                "subtitle_center_score": center_score,
                "score": score,
                "ocr_blocks": ocr_blocks,
            })
        except BlockedError:
            raise
        except Exception as exc:
            candidates.append({
                "candidate_index": candidate_index,
                "time": round(time_value, 3),
                "frame": None,
                "path": "",
                "score": 0.0,
                "error": f"{type(exc).__name__}: {exc}",
            })
    usable = [item for item in candidates if item.get("path")]
    if not usable:
        raise BlockedError("sentence_frame_extract_failed", f"No usable frame candidates for sentence_id={sentence_id}.")
    selected = max(usable, key=lambda item: (float(item.get("score") or 0.0), float(item.get("text_match_score") or 0.0), float(item.get("ocr_confidence") or 0.0)))
    selected_path = workspace / SESSION_SRT_FRAMES_DIR_REL / f"{sentence_id}.jpg"
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(workspace / str(selected["path"]), selected_path)
    subtitle_events = build_ocr_subtitle_events(workspace, sentence_id, start, end, asr_text, candidates, args)
    final_text, calibration = choose_final_text(asr_text, str(selected.get("ocr_text") or ""), float(selected.get("text_match_score") or 0.0), float(selected.get("ocr_confidence") or 0.0), args)
    if len(subtitle_events) > 1:
        final_text = display_text(" ".join(str(event.get("text") or "") for event in subtitle_events))
        calibration = {
            "text_source": "ocr_subtitle_events",
            "needs_review": any(bool((event.get("calibration") or {}).get("needs_review")) for event in subtitle_events),
            "split_into_dialogue_units": True,
            "dialogue_unit_count": len(subtitle_events),
        }
    needs_review = bool(calibration.get("needs_review")) or (float(selected.get("text_match_score") or 0.0) < float(args.min_match_score) and len(subtitle_events) <= 1)
    item = {
        "sentence_id": sentence_id,
        "sentence_order": segment.get("sentence_order"),
        "source_asr_index": segment.get("source_asr_index"),
        "asr_start": start,
        "asr_end": end,
        "asr_text": asr_text,
        "ocr_text": selected.get("ocr_text") or "",
        "final_text": final_text,
        "selected_frame": {
            "time": selected.get("time"),
            "frame": selected.get("frame"),
            "path": relpath(selected_path, workspace),
            "source": "sentence_id",
            "match_policy": "sentence_id_not_text_or_time",
        },
        "score": selected.get("score"),
        "text_match_score": selected.get("text_match_score"),
        "ocr_confidence": selected.get("ocr_confidence"),
        "subtitle_region_sharpness": selected.get("subtitle_region_sharpness"),
        "calibration": {
            **calibration,
            "time_source": "asr_window_ocr_best_frame",
            "frame_binding_key": "sentence_id",
            "needs_review": needs_review,
        },
        "subtitle_events": subtitle_events,
        "candidates": candidates,
    }
    if len(subtitle_events) > 1:
        calibrated = [
            {
                "sentence_id": event["dialogue_unit_id"],
                "parent_sentence_id": sentence_id,
                "index": f"{segment.get('sentence_order')}.{event['event_order']}",
                "source_asr_index": segment.get("source_asr_index"),
                "start": event["start"],
                "end": event["end"],
                "text": event["text"],
                "asr_text": asr_text,
                "ocr_text": event["ocr_text"],
                "frame_path": event["frame_path"],
                "frame_time": event["frame_time"],
                "calibration": event["calibration"],
            }
            for event in subtitle_events
        ]
    else:
        calibrated = [{
            "sentence_id": sentence_id,
            "parent_sentence_id": sentence_id,
            "index": segment.get("sentence_order"),
            "source_asr_index": segment.get("source_asr_index"),
            "start": start,
            "end": end,
            "text": final_text,
            "asr_text": asr_text,
            "ocr_text": selected.get("ocr_text") or "",
            "frame_path": relpath(selected_path, workspace),
            "frame_time": selected.get("time"),
            "calibration": item["calibration"],
        }]
    return item, calibrated


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "runtime": {
            "python_executable": sys.executable,
            "runtime_reexec": os.environ.get("ANALYSIS_V1_RAPIDOCR_RUNTIME_REEXEC") == "1",
        },
        "reads_session_context": [VARIABLES_REL, DEFAULT_METADATA_REL, DEFAULT_SOURCE_VIDEO_REL, DEFAULT_ASR_SEGMENTS_REL],
        "writes_session_context": [],
        "writes_session_output": [
            SESSION_FINAL_SRT_FRAME_ITEMS_REL,
            SESSION_SRT_FRAMES_DIR_REL,
            SESSION_SRT_FRAME_MAP_REL,
            SESSION_SENTENCE_INDEX_REL,
            SESSION_CALIBRATED_ITEMS_REL,
        ],
        "identity_policy": "Final SRT frame items are bound by stable srt_id/dialogue_unit_id, not by current rewritten text or frame time.",
        "created_files": [],
        "prepared_directories": [],
        "cleanup_actions": [],
        "inputs": {},
        "outputs": {},
        "counts": {},
        "ocr_engine": {},
        "warnings": [],
        "blocked_reasons": [],
        "resume": bool(args.resume),
        "force": bool(args.force),
        "updated_at": now_iso(),
    }


def add_block(result: dict[str, Any], code: str, message: str) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": code, "message": message})


def scan_for_sensitive_output(payload: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return [{"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"} for pattern in SECRET_PATTERNS if pattern in text]


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    for rel in (TOOL_DIR_NAME, SESSION_FINAL_SRT_FRAME_ITEMS_REL, SESSION_SRT_FRAME_MAP_REL, SESSION_SENTENCE_INDEX_REL, SESSION_CALIBRATED_ITEMS_REL, SESSION_SRT_FRAMES_DIR_REL):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            result.setdefault("cleanup_actions", []).append({"path": rel, "action": "removed_for_force_rerun"})


def prepare_inputs(workspace: Path, variables: dict[str, Any], metadata: dict[str, Any], asr_segments: dict[str, Any], source_video: Path, source_info: dict[str, Any], input_signature: str, signature: str, result: dict[str, Any]) -> dict[str, Any]:
    ensure_tool_dirs(workspace)
    for rel in (f"{TOOL_DIR_NAME}/Working", WORKING_CANDIDATES_DIR_REL, f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Report"):
        result.setdefault("prepared_directories", []).append(rel)
    write_json(workspace / WORKING_VARIABLES_REL, variables)
    write_json(workspace / WORKING_METADATA_REL, metadata)
    write_json(workspace / WORKING_ASR_SEGMENTS_REL, asr_segments)
    state = {
        "tool": TOOL_NAME,
        "status": "ready",
        "phase": "prepare",
        "source": source_info,
        "input_signature": input_signature,
        "config_signature": signature,
        "identity_policy": "sentence_id",
        "inputs": {
            "variables": WORKING_VARIABLES_REL,
            "video_metadata": WORKING_METADATA_REL,
            "source_video": relpath(source_video, workspace),
            "asr_segments": WORKING_ASR_SEGMENTS_REL,
        },
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["inputs"] = state["inputs"]
    return state


def selected_frame_paths(payload: dict[str, Any]) -> list[str]:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    paths = []
    for item in items:
        if isinstance(item, dict):
            frame = item.get("selected_frame") if isinstance(item.get("selected_frame"), dict) else {}
            if frame.get("path"):
                paths.append(str(frame["path"]))
    return paths


def load_reusable_outputs(workspace: Path, source_info: dict[str, Any], input_signature: str, signature: str, force: bool) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    if force:
        return None
    paths = [workspace / OUTPUT_SRT_FRAME_MAP_REL, workspace / OUTPUT_SENTENCE_INDEX_REL, workspace / OUTPUT_CALIBRATED_ITEMS_REL, workspace / WORKING_STATE_REL]
    if not all(path.exists() for path in paths):
        return None
    try:
        state = read_json(workspace / WORKING_STATE_REL)
        frame_map = read_json(workspace / OUTPUT_SRT_FRAME_MAP_REL)
        sentence_index = read_json(workspace / OUTPUT_SENTENCE_INDEX_REL)
        calibrated = read_json(workspace / OUTPUT_CALIBRATED_ITEMS_REL)
    except Exception:
        return None
    if state.get("status") != "completed":
        return None
    if (state.get("source") or {}).get("fingerprint") != source_info.get("fingerprint"):
        return None
    if state.get("input_signature") != input_signature or state.get("config_signature") != signature:
        return None
    if any(not (workspace / rel).exists() for rel in selected_frame_paths(frame_map)):
        return None
    return frame_map, sentence_index, calibrated


def finalize_outputs(workspace: Path, frame_map: dict[str, Any], sentence_index: dict[str, Any], calibrated: dict[str, Any], state: dict[str, Any], result: dict[str, Any], reused: bool) -> None:
    try:
        simplified_changes = normalize_calibrated_items_to_simplified(calibrated)
    except SimplifiedChineseError as exc:
        raise BlockedError("simplified_chinese_normalizer_missing", str(exc)) from exc
    if simplified_changes:
        result.setdefault("warnings", []).append({
            "code": "srt_text_normalized_to_simplified_chinese",
            "message": f"Normalized {simplified_changes} calibrated SRT item(s) to Simplified Chinese.",
        })
    final_srt_frame_items = build_final_srt_frame_items(calibrated)
    write_json(workspace / OUTPUT_SRT_FRAME_MAP_REL, frame_map)
    write_json(workspace / OUTPUT_SENTENCE_INDEX_REL, sentence_index)
    write_json(workspace / OUTPUT_CALIBRATED_ITEMS_REL, calibrated)
    write_json(workspace / OUTPUT_FINAL_SRT_FRAME_ITEMS_REL, final_srt_frame_items)
    write_json(workspace / SESSION_SRT_FRAME_MAP_REL, frame_map)
    write_json(workspace / SESSION_SENTENCE_INDEX_REL, sentence_index)
    write_json(workspace / SESSION_CALIBRATED_ITEMS_REL, calibrated)
    write_json(workspace / SESSION_FINAL_SRT_FRAME_ITEMS_REL, final_srt_frame_items)
    state = {
        **state,
        "status": "completed",
        "phase": "finalize",
        "outputs": {
            "final_srt_frame_items": SESSION_FINAL_SRT_FRAME_ITEMS_REL,
            "session_srt_frames_dir": SESSION_SRT_FRAMES_DIR_REL,
        },
        "intermediate_outputs": {
            "tool_final_srt_frame_items_copy": OUTPUT_FINAL_SRT_FRAME_ITEMS_REL,
            "srt_frame_map": OUTPUT_SRT_FRAME_MAP_REL,
            "srt_sentence_index": OUTPUT_SENTENCE_INDEX_REL,
            "calibrated_srt_items": OUTPUT_CALIBRATED_ITEMS_REL,
            "session_srt_frame_map": SESSION_SRT_FRAME_MAP_REL,
            "session_srt_sentence_index": SESSION_SENTENCE_INDEX_REL,
            "session_calibrated_srt_items": SESSION_CALIBRATED_ITEMS_REL,
        },
        "reused_completed_output": reused,
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["status"] = "completed"
    result["outputs"] = state["outputs"]
    result["counts"] = {
        "sentences": len(frame_map.get("items") or []),
        "needs_review": sum(1 for item in frame_map.get("items") or [] if ((item.get("calibration") or {}).get("needs_review"))),
        "selected_frames": len(selected_frame_paths(frame_map)),
        "dialogue_units": len(calibrated.get("items") or []),
        "final_srt_frame_items": len(final_srt_frame_items.get("items") or []),
        "split_sentences": sum(1 for item in frame_map.get("items") or [] if len(item.get("subtitle_events") or []) > 1),
        "deduped_duplicates": len(calibrated.get("duplicate_items") or []),
    }
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_METADATA_REL,
        WORKING_ASR_SEGMENTS_REL,
        WORKING_STATE_REL,
        OUTPUT_FINAL_SRT_FRAME_ITEMS_REL,
        OUTPUT_SRT_FRAME_MAP_REL,
        OUTPUT_SENTENCE_INDEX_REL,
        OUTPUT_CALIBRATED_ITEMS_REL,
        SESSION_FINAL_SRT_FRAME_ITEMS_REL,
        SESSION_SRT_FRAME_MAP_REL,
        SESSION_SENTENCE_INDEX_REL,
        SESSION_CALIBRATED_ITEMS_REL,
        SESSION_SRT_FRAMES_DIR_REL,
        REPORT_RESULT_REL,
    ]
    if reused:
        result["warnings"].append({"code": "reused_completed_output", "message": "Existing SRT frame map was reused because the input and parameter signature matched."})


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        validate_workspace(workspace)
        if args.force:
            force_reset(workspace, result)
        variables = load_variables(workspace)
        metadata = load_video_metadata(workspace, variables)
        source_video = resolve_source_video(workspace, variables)
        asr_payload = load_asr_segments(workspace, variables)
        asr_rel = str(variables.get("asr_segments_path") or DEFAULT_ASR_SEGMENTS_REL)
        input_signature = file_signature(workspace, [str(variables.get("video_metadata_path") or DEFAULT_METADATA_REL), asr_rel])
        source_info = source_fingerprint(source_video)
        signature = config_signature(args, input_signature)
        reusable = load_reusable_outputs(workspace, source_info, input_signature, signature, args.force or not args.resume)
        state = prepare_inputs(workspace, variables, metadata, asr_payload, source_video, source_info, input_signature, signature, result)
        if reusable is not None:
            frame_map, sentence_index, calibrated = reusable
            result["ocr_engine"] = {"source": "reused_completed_output"}
            finalize_outputs(workspace, frame_map, sentence_index, calibrated, state, result, reused=True)
        else:
            languages = [item.strip() for item in args.languages.split(",") if item.strip()]
            engine, failures = build_engine(args.ocr_engine, languages)
            result["ocr_engine"] = {"requested": args.ocr_engine, "used": engine.name, "failures": failures}
            state = {**state, "phase": "srt_frame_ocr", "ocr_engine": engine.name, "updated_at": now_iso()}
            write_json(workspace / WORKING_STATE_REL, state)
            duration = float(metadata.get("duration_seconds") or 0.0)
            fps = float(metadata.get("fps") or 0.0)
            segments = normalized_segments(asr_payload, duration)
            items: list[dict[str, Any]] = []
            calibrated_items: list[dict[str, Any]] = []
            for segment in segments:
                item, calibrated_item = select_sentence_frame(workspace, source_video, fps, duration, segment, engine, args)
                items.append(item)
                calibrated_items.extend(calibrated_item)
                if args.print_progress:
                    print(f"[02_01] sentence_id={segment['sentence_id']} score={item['score']} frame={item['selected_frame']['path']}")
            calibrated_items, duplicate_items = dedupe_calibrated_items(calibrated_items)
            common = {
                "tool": TOOL_NAME,
                "tool_version": TOOL_VERSION,
                "source_video_path": relpath(source_video, workspace),
                "identity_policy": "sentence_id_not_text_or_time",
                "sentence_id_rule": "Use existing segment.sentence_id when present; otherwise srt_XXXX from source ASR index/order.",
                "ocr_engine_requested": args.ocr_engine,
                "ocr_engine_used": engine.name,
                "ocr_engine_failures": failures,
                "languages": languages,
                "created_at": now_iso(),
            }
            frame_map = {
                "schema_version": "analysis_v1_srt_frame_map_0.1",
                **common,
                "items": items,
            }
            sentence_index = {
                "schema_version": "analysis_v1_srt_sentence_index_0.1",
                **common,
                "items": [
                    {
                        "sentence_id": item["sentence_id"],
                        "sentence_order": item["sentence_order"],
                        "source_asr_index": item["source_asr_index"],
                        "asr_start": item["asr_start"],
                        "asr_end": item["asr_end"],
                        "asr_text": item["asr_text"],
                        "frame_path": (item.get("selected_frame") or {}).get("path"),
                    }
                    for item in items
                ],
            }
            calibrated = {
                "schema_version": "analysis_v1_calibrated_srt_items_0.1",
                **common,
                "dedupe_policy": "adjacent duplicate calibrated subtitle texts are merged before output.",
                "duplicate_items": duplicate_items,
                "items": calibrated_items,
            }
            finalize_outputs(workspace, frame_map, sentence_index, calibrated, state, result, reused=False)
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
    except PermissionError as exc:
        add_block(result, "workspace_permission_denied", f"Cannot read/write Analysis_V1 workspace. Original error: {exc}")
    except Exception as exc:
        result["status"] = "failed"
        result["warnings"].append({"code": "unexpected_error", "message": str(exc)})

    result["updated_at"] = now_iso()
    result["warnings"].extend(scan_for_sensitive_output(result))
    try:
        if workspace.exists() and workspace.is_dir():
            (workspace / f"{TOOL_DIR_NAME}/Report").mkdir(parents=True, exist_ok=True)
            write_json(workspace / REPORT_RESULT_REL, result)
    except Exception as exc:
        result["warnings"].append({"code": "result_write_failed", "message": str(exc)})
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Bind each ASR SRT sentence to the clearest subtitle frame by stable sentence_id.")
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--ocr-engine", choices=["auto", "paddleocr", "rapidocr", "easyocr", "tesseract"], default="auto")
    parser.add_argument("--languages", default="ch,en", help="Comma-separated OCR languages.")
    parser.add_argument("--sample-interval-seconds", type=float, default=0.2)
    parser.add_argument("--min-candidates", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--subtitle-top-ratio", type=float, default=0.55, help="Only OCR text with bbox center_y below this ratio is considered dialogue subtitle text.")
    parser.add_argument("--min-confidence", type=float, default=0.45)
    parser.add_argument("--min-chars", type=int, default=2)
    parser.add_argument("--text-match-weight", type=float, default=0.55)
    parser.add_argument("--ocr-confidence-weight", type=float, default=0.25)
    parser.add_argument("--sharpness-weight", type=float, default=0.15)
    parser.add_argument("--center-weight", type=float, default=0.05)
    parser.add_argument("--min-match-score", type=float, default=0.62)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-progress", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        ocr_engine=str(ns.ocr_engine),
        languages=str(ns.languages),
        sample_interval_seconds=float(ns.sample_interval_seconds),
        min_candidates=int(ns.min_candidates),
        max_candidates=int(ns.max_candidates),
        subtitle_top_ratio=float(ns.subtitle_top_ratio),
        min_confidence=float(ns.min_confidence),
        min_chars=int(ns.min_chars),
        text_match_weight=float(ns.text_match_weight),
        ocr_confidence_weight=float(ns.ocr_confidence_weight),
        sharpness_weight=float(ns.sharpness_weight),
        center_weight=float(ns.center_weight),
        min_match_score=float(ns.min_match_score),
        force=bool(ns.force),
        resume=bool(ns.resume),
        print_progress=bool(ns.print_progress),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli_args:
        try:
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge

        framework_exit = maybe_run_framework_bridge(cli_args, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit

    args = parse_args(cli_args)
    maybe_reexec_with_rapidocr_runtime(args.ocr_engine)
    result = run(args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('session_srt_frame_map', '')}")
    return 0 if result["status"] in {"completed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
