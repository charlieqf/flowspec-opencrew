from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol


TOOL_NAME = "04_VideoSrtOCR"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
DEFAULT_SOURCE_VIDEO_REL = f"{CONTEXT_DIR_NAME}/Video_Source.mp4"
DEFAULT_METADATA_REL = f"{CONTEXT_DIR_NAME}/Video_Metadata.json"
TOOL_DIR_NAME = "S4_04_VideoSrtOCR"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_METADATA_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_1_Video_Metadata.json"
WORKING_KEYFRAMES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_3_visual_keyframes.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
OUTPUT_VISUAL_SUBTITLE_REL = f"{TOOL_DIR_NAME}/Output/visual_subtitle_timeline.json"
OUTPUT_VISUAL_TEXT_REL = f"{TOOL_DIR_NAME}/Output/visual_text_timeline.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_VISUAL_DIR_REL = "SessionOutput/visual"
SESSION_SUBTITLE_DIR_REL = "SessionOutput/subtitle"
SESSION_SCENE_SEGMENTS_REL = f"{SESSION_VISUAL_DIR_REL}/scene_segments.json"
SESSION_KEYFRAMES_ENHANCED_REL = f"{SESSION_VISUAL_DIR_REL}/visual_keyframes_enhanced.json"
SESSION_KEYFRAMES_BASE_REL = f"{SESSION_VISUAL_DIR_REL}/visual_keyframes.json"
SESSION_VISUAL_SUBTITLE_REL = f"{SESSION_SUBTITLE_DIR_REL}/visual_subtitle_timeline.json"
SESSION_VISUAL_TEXT_REL = f"{SESSION_VISUAL_DIR_REL}/visual_text_timeline.json"
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


@dataclass(frozen=True)
class Args:
    workspace: str
    input_keyframes: str
    ocr_engine: str
    languages: str
    min_confidence: float
    min_chars: int
    merge_similarity_threshold: float
    max_merge_gap_seconds: float
    no_progressive_merge: bool
    progressive_similarity_threshold: float
    max_progressive_gap_seconds: float
    max_items: int
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
    return payload


def resolve_source_video(workspace: Path, variables: dict[str, Any]) -> Path:
    raw = str(variables.get("source_video_path") or DEFAULT_SOURCE_VIDEO_REL).strip()
    path = Path(raw)
    if path.is_absolute():
        raise BlockedError("source_video_path_not_relative", "source_video_path must be workspace-relative.")
    path = workspace / path
    if not path.exists():
        raise BlockedError("source_video_missing", f"Source video is missing: {raw}. Run 00 first.")
    return path


def resolve_keyframes_path(workspace: Path, args: Args) -> tuple[str, dict[str, Any]]:
    candidates = [args.input_keyframes] if args.input_keyframes else [SESSION_KEYFRAMES_ENHANCED_REL, SESSION_KEYFRAMES_BASE_REL]
    for raw in candidates:
        rel = str(raw).strip()
        if not rel:
            continue
        path = Path(rel)
        if path.is_absolute():
            raise BlockedError("keyframes_path_not_relative", "input keyframes path must be workspace-relative.")
        full = workspace / path
        if not full.exists():
            continue
        payload = read_json(full)
        if isinstance(payload, dict) and isinstance(payload.get("items"), list) and payload["items"]:
            return rel, payload
    raise BlockedError("visual_keyframes_missing", "Required visual keyframes are missing. Run 03/03_02 first.")


def load_scenes(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / SESSION_SCENE_SEGMENTS_REL
    if not path.exists():
        return []
    payload = read_json(path)
    rows = payload.get("scenes") if isinstance(payload, dict) else None
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def ensure_tool_dirs(workspace: Path) -> None:
    for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Report"):
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
        "input_signature": input_signature,
        "ocr_engine": args.ocr_engine,
        "languages": args.languages,
        "min_confidence": float(args.min_confidence),
        "min_chars": int(args.min_chars),
        "merge_similarity_threshold": float(args.merge_similarity_threshold),
        "max_merge_gap_seconds": float(args.max_merge_gap_seconds),
        "no_progressive_merge": bool(args.no_progressive_merge),
        "progressive_similarity_threshold": float(args.progressive_similarity_threshold),
        "max_progressive_gap_seconds": float(args.max_progressive_gap_seconds),
        "max_items": int(args.max_items),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text or "")
    cleaned = re.sub(r"[|｜_＿=]+", "", cleaned)
    return cleaned.strip()


def display_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def text_similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    return float(SequenceMatcher(None, left_norm, right_norm).ratio())


def is_progressive_reveal(previous_text: str, current_text: str, threshold: float) -> bool:
    previous = normalize_text(previous_text)
    current = normalize_text(current_text)
    if not previous or not current or current == previous:
        return False
    if previous in current and len(current) > len(previous):
        return True
    if current in previous:
        return False
    return len(current) > len(previous) and text_similarity(previous, current) >= threshold


def looks_like_noise(text: str, min_chars: int) -> bool:
    compact = normalize_text(text)
    if len(compact) < min_chars:
        return True
    return bool(re.fullmatch(r"[\W_\d]+", compact))


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def bbox_points(bbox: Any) -> list[tuple[float, float]]:
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


def image_size(image_path: Path) -> tuple[int, int]:
    try:
        import cv2  # type: ignore

        image = cv2.imread(str(image_path))
        if image is not None:
            height, width = image.shape[:2]
            return int(width), int(height)
    except Exception:
        pass
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


def looks_like_natural_subtitle(text: str) -> bool:
    compact = normalize_text(text)
    chinese = re.findall(r"[\u4e00-\u9fff]", compact)
    if len(chinese) >= 6:
        return True
    return bool(re.search(r"[，。！？、,.!?]", text or "")) and len(compact) >= 4


def classify_ocr_block(block: dict[str, Any]) -> tuple[str, float, list[str]]:
    text = str(block.get("text") or "").strip()
    compact = normalize_text(text)
    layout = block.get("layout") or {}
    center_x = float(layout.get("center_x") or 0.0)
    center_y = float(layout.get("center_y") or 0.0)
    width_ratio = float(layout.get("width_ratio") or 0.0)
    height_ratio = float(layout.get("height_ratio") or 0.0)
    confidence = float(block.get("confidence") or 0.0)
    reasons: list[str] = []
    if not compact:
        return "noise", 0.0, ["empty_text"]
    if re.fullmatch(r"[\W_\d]+", compact):
        return "noise", 0.1, ["symbols_or_digits_only"]
    if re.search(r"@|抖音|快手|小红书|微信|视频号|关注|点赞|评论|转发|ID[:：]?", text, flags=re.I):
        return "watermark_ui", min(0.95, confidence + 0.1), ["platform_or_account_pattern"]
    if re.search(r"¥|￥|\d+(?:\.\d+)?\s*(?:元|ml|g|kg|斤|瓶|盒|片|粒|%|折)", text, flags=re.I):
        return "price_spec", min(0.9, confidence + 0.05), ["price_or_spec_pattern"]
    lower_band = center_y >= 0.58
    centered = 0.12 <= center_x <= 0.88
    subtitle_width = width_ratio >= 0.18
    natural = looks_like_natural_subtitle(text)
    if lower_band:
        reasons.append("lower_band")
    if centered:
        reasons.append("centered")
    if subtitle_width:
        reasons.append("subtitle_width")
    if natural:
        reasons.append("natural_sentence")
    subtitle_score = confidence * 0.45 + (0.18 if lower_band else 0.0) + (0.12 if centered else 0.0) + (0.13 if subtitle_width else 0.0) + (0.12 if natural else 0.0)
    if subtitle_score >= 0.62 and lower_band and centered and natural:
        return "subtitle_candidate", round(min(0.98, subtitle_score), 4), reasons
    if center_y <= 0.28 and width_ratio >= 0.2 and natural:
        return "title_card", round(min(0.9, confidence + 0.1), 4), ["upper_title_like", "natural_sentence"]
    if len(compact) <= 12 and height_ratio >= 0.025:
        return "brand_text", round(min(0.82, confidence), 4), ["short_prominent_text"]
    if re.search(r"成分|功效|适用|规格|净含量|保质期|产品|使用|添加|不含", text):
        return "product_text", round(min(0.88, confidence + 0.05), 4), ["product_keyword"]
    return "other_visual_text", round(min(0.8, confidence), 4), ["default_visual_text"]


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
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except Exception:
            from rapidocr import RapidOCR  # type: ignore

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


def scene_for_time(scenes: list[dict[str, Any]], time_value: float) -> dict[str, Any] | None:
    for scene in scenes:
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or start)
        if start <= time_value <= end:
            return scene
    return None


def resolve_image_path(workspace: Path, path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (workspace / path).resolve()


def ocr_keyframes(workspace: Path, keyframes: list[dict[str, Any]], scenes: list[dict[str, Any]], engine: OCREngine, args: Args) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, keyframe in enumerate(keyframes, start=1):
        image_path = resolve_image_path(workspace, str(keyframe.get("path") or ""))
        base = {
            "index": index,
            "time": round(float(keyframe.get("time") or 0.0), 3),
            "frame": keyframe.get("frame"),
            "source": keyframe.get("source"),
            "segment_index": keyframe.get("segment_index"),
            "role": keyframe.get("role"),
            "path": relpath(image_path, workspace),
        }
        scene = scene_for_time(scenes, float(base["time"]))
        if scene:
            base.update({"scene_index": scene.get("index"), "scene_start": scene.get("start"), "scene_end": scene.get("end")})
        if not image_path.exists():
            errors.append({**base, "error": "image_not_found"})
            continue
        try:
            raw_blocks = engine.recognize(image_path)
        except Exception as exc:
            errors.append({**base, "error": f"{type(exc).__name__}: {exc}"})
            continue
        width, height = image_size(image_path)
        blocks: list[dict[str, Any]] = []
        for block in raw_blocks:
            if float(block.get("confidence") or 0.0) < args.min_confidence or looks_like_noise(str(block.get("text") or ""), args.min_chars):
                continue
            layout = layout_features(block, width, height)
            text_class, class_confidence, class_reasons = classify_ocr_block({**block, "layout": layout})
            blocks.append({**block, "layout": layout, "text_class": text_class, "class_confidence": class_confidence, "classification_reason": class_reasons})
        text = display_text(" ".join(str(block.get("text") or "") for block in blocks))
        confidence = round(average([float(block.get("confidence") or 0.0) for block in blocks]), 4)
        class_counts: dict[str, int] = {}
        for block in blocks:
            key = str(block.get("text_class") or "other_visual_text")
            class_counts[key] = class_counts.get(key, 0) + 1
        items.append({**base, "image_width": width, "image_height": height, "ocr_text": text, "ocr_blocks": blocks, "confidence": confidence, "block_count": len(blocks), "class_counts": class_counts})
        if args.print_progress:
            print(f"[04 OCR] keyframe={index}/{len(keyframes)} time={base['time']:.3f} blocks={len(blocks)}")
    return items, errors


def split_items_by_block_class(ocr_items: list[dict[str, Any]], target_classes: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in ocr_items:
        blocks = [block for block in (item.get("ocr_blocks") or []) if str(block.get("text_class") or "") in target_classes]
        if not blocks:
            continue
        text = display_text(" ".join(str(block.get("text") or "") for block in blocks))
        confidence = round(average([float(block.get("confidence") or 0.0) for block in blocks]), 4)
        class_confidence = round(average([float(block.get("class_confidence") or 0.0) for block in blocks]), 4)
        rows.append({**item, "ocr_text": text, "ocr_blocks": blocks, "confidence": confidence, "class_confidence": class_confidence, "block_count": len(blocks)})
    return rows


def text_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {"time": round(float(item.get("time") or 0.0), 3), "text": str(item.get("ocr_text") or "").strip(), "confidence": round(float(item.get("confidence") or 0.0), 4), "path": item.get("path")}


def choose_representative(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"text": "", "time": 0.0, "path": "", "confidence": 0.0}
    return max(candidates, key=lambda item: (len(normalize_text(str(item.get("text") or ""))), float(item.get("confidence") or 0.0), float(item.get("time") or 0.0)))


def append_to_timeline_item(current: dict[str, Any], item: dict[str, Any], reason: str) -> None:
    time_value = round(float(item.get("time") or 0.0), 3)
    confidence = float(item.get("confidence") or 0.0)
    current["end"] = time_value
    current["source_keyframe_times"].append(time_value)
    current["source_keyframe_paths"].append(item.get("path"))
    current["confidence"] = round(average([float(current.get("confidence") or 0.0), confidence]), 4)
    current.setdefault("text_candidates", []).append(text_candidate(item))
    current.setdefault("merge_reasons", [])
    if reason not in current["merge_reasons"]:
        current["merge_reasons"].append(reason)
    representative = choose_representative(current["text_candidates"])
    current["text"] = representative.get("text") or ""
    current["representative_time"] = representative.get("time")
    current["representative_path"] = representative.get("path")


def merge_timeline(ocr_items: list[dict[str, Any]], args: Args) -> list[dict[str, Any]]:
    source = [item for item in sorted(ocr_items, key=lambda entry: float(entry.get("time") or 0.0)) if str(item.get("ocr_text") or "").strip()]
    timeline: list[dict[str, Any]] = []
    for item in source:
        time_value = float(item.get("time") or 0.0)
        text = str(item.get("ocr_text") or "").strip()
        confidence = float(item.get("confidence") or 0.0)
        if timeline:
            current = timeline[-1]
            gap = time_value - float(current.get("end") or 0.0)
            similarity = text_similarity(str(current.get("text") or ""), text)
            duplicate = gap <= args.max_merge_gap_seconds and similarity >= args.merge_similarity_threshold
            progressive = (not args.no_progressive_merge and gap <= args.max_progressive_gap_seconds and is_progressive_reveal(str(current.get("text") or ""), text, args.progressive_similarity_threshold))
            if duplicate or progressive:
                append_to_timeline_item(current, item, "progressive_text_reveal" if progressive else "near_duplicate")
                continue
        candidate = text_candidate(item)
        timeline.append({
            "start": round(time_value, 3),
            "end": round(time_value, 3),
            "text": text,
            "source_keyframe_times": [round(time_value, 3)],
            "source_keyframe_paths": [item.get("path")],
            "confidence": round(confidence, 4),
            "scene_index": item.get("scene_index"),
            "text_class": item.get("text_class"),
            "class_confidence": item.get("class_confidence"),
            "representative_time": candidate["time"],
            "representative_path": candidate["path"],
            "text_candidates": [candidate],
            "merge_reasons": ["single_keyframe"],
        })
    return timeline[: args.max_items] if args.max_items > 0 and len(timeline) > args.max_items else timeline


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "reads_session_context": [VARIABLES_REL, DEFAULT_METADATA_REL, DEFAULT_SOURCE_VIDEO_REL],
        "writes_session_context": [],
        "writes_session_output": [SESSION_VISUAL_SUBTITLE_REL, SESSION_VISUAL_TEXT_REL],
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
    for rel in (TOOL_DIR_NAME, SESSION_VISUAL_SUBTITLE_REL, SESSION_VISUAL_TEXT_REL):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            result.setdefault("cleanup_actions", []).append({"path": rel, "action": "removed_for_force_rerun"})


def prepare_inputs(workspace: Path, variables: dict[str, Any], metadata: dict[str, Any], keyframes: dict[str, Any], source_video: Path, source_info: dict[str, Any], input_rel: str, input_signature: str, signature: str, result: dict[str, Any]) -> dict[str, Any]:
    ensure_tool_dirs(workspace)
    for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Report"):
        result.setdefault("prepared_directories", []).append(rel)
    write_json(workspace / WORKING_VARIABLES_REL, variables)
    write_json(workspace / WORKING_METADATA_REL, metadata)
    write_json(workspace / WORKING_KEYFRAMES_REL, keyframes)
    state = {
        "tool": TOOL_NAME,
        "status": "ready",
        "phase": "prepare",
        "source": source_info,
        "input_keyframes": input_rel,
        "input_signature": input_signature,
        "config_signature": signature,
        "inputs": {
            "variables": WORKING_VARIABLES_REL,
            "video_metadata": WORKING_METADATA_REL,
            "source_video": relpath(source_video, workspace),
            "visual_keyframes": WORKING_KEYFRAMES_REL,
        },
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["inputs"] = state["inputs"]
    return state


def load_reusable_outputs(workspace: Path, source_info: dict[str, Any], input_signature: str, signature: str, force: bool) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if force:
        return None
    paths = [workspace / OUTPUT_VISUAL_SUBTITLE_REL, workspace / OUTPUT_VISUAL_TEXT_REL, workspace / WORKING_STATE_REL]
    if not all(path.exists() for path in paths):
        return None
    try:
        state = read_json(workspace / WORKING_STATE_REL)
        subtitles = read_json(workspace / OUTPUT_VISUAL_SUBTITLE_REL)
        visual_text = read_json(workspace / OUTPUT_VISUAL_TEXT_REL)
    except Exception:
        return None
    if state.get("status") != "completed":
        return None
    if (state.get("source") or {}).get("fingerprint") != source_info.get("fingerprint"):
        return None
    if state.get("input_signature") != input_signature or state.get("config_signature") != signature:
        return None
    return subtitles, visual_text


def finalize_outputs(workspace: Path, subtitles: dict[str, Any], visual_text: dict[str, Any], state: dict[str, Any], result: dict[str, Any], reused: bool) -> None:
    write_json(workspace / OUTPUT_VISUAL_SUBTITLE_REL, subtitles)
    write_json(workspace / OUTPUT_VISUAL_TEXT_REL, visual_text)
    write_json(workspace / SESSION_VISUAL_SUBTITLE_REL, subtitles)
    write_json(workspace / SESSION_VISUAL_TEXT_REL, visual_text)
    state = {
        **state,
        "status": "completed",
        "phase": "finalize",
        "outputs": {
            "visual_subtitle_timeline": OUTPUT_VISUAL_SUBTITLE_REL,
            "visual_text_timeline": OUTPUT_VISUAL_TEXT_REL,
            "session_visual_subtitle_timeline": SESSION_VISUAL_SUBTITLE_REL,
            "session_visual_text_timeline": SESSION_VISUAL_TEXT_REL,
        },
        "reused_completed_output": reused,
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["status"] = "completed"
    result["outputs"] = state["outputs"]
    result["counts"] = {
        "subtitle_items": len(subtitles.get("items") or []),
        "visual_text_items": len(visual_text.get("items") or []),
    }
    result["created_files"] = [WORKING_VARIABLES_REL, WORKING_METADATA_REL, WORKING_KEYFRAMES_REL, WORKING_STATE_REL, OUTPUT_VISUAL_SUBTITLE_REL, OUTPUT_VISUAL_TEXT_REL, SESSION_VISUAL_SUBTITLE_REL, SESSION_VISUAL_TEXT_REL, REPORT_RESULT_REL]
    if reused:
        result["warnings"].append({"code": "reused_completed_output", "message": "Existing OCR timeline output was reused because the input and parameter signature matched."})


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
        input_rel, keyframes_payload = resolve_keyframes_path(workspace, args)
        input_signature = file_signature(workspace, [input_rel])
        source_info = source_fingerprint(source_video)
        signature = config_signature(args, input_signature)
        reusable = load_reusable_outputs(workspace, source_info, input_signature, signature, args.force or not args.resume)
        state = prepare_inputs(workspace, variables, metadata, keyframes_payload, source_video, source_info, input_rel, input_signature, signature, result)
        if reusable is not None:
            subtitles, visual_text = reusable
            result["ocr_engine"] = {"source": "reused_completed_output"}
            finalize_outputs(workspace, subtitles, visual_text, state, result, reused=True)
        else:
            languages = [item.strip() for item in args.languages.split(",") if item.strip()]
            engine, failures = build_engine(args.ocr_engine, languages)
            result["ocr_engine"] = {"requested": args.ocr_engine, "used": engine.name, "failures": failures}
            state = {**state, "phase": "ocr", "ocr_engine": engine.name, "updated_at": now_iso()}
            write_json(workspace / WORKING_STATE_REL, state)
            ocr_items, errors = ocr_keyframes(workspace, keyframes_payload.get("items") or [], load_scenes(workspace), engine, args)
            if errors:
                result["warnings"].append({"code": "ocr_item_errors", "message": f"{len(errors)} keyframes failed OCR."})
            subtitle_source = split_items_by_block_class(ocr_items, {"subtitle_candidate"})
            visual_text_source = split_items_by_block_class(ocr_items, {"product_text", "brand_text", "price_spec", "title_card", "other_visual_text"})
            subtitle_items = merge_timeline(subtitle_source, args)
            for item in subtitle_items:
                item["text_class"] = "subtitle_candidate"
            visual_text_items = merge_timeline(visual_text_source, args)
            for item in visual_text_items:
                item["text_class"] = item.get("text_class") or "visual_text"
            common = {
                "tool": TOOL_NAME,
                "tool_version": TOOL_VERSION,
                "source_video_path": relpath(source_video, workspace),
                "input_keyframes": input_rel,
                "ocr_engine_requested": args.ocr_engine,
                "ocr_engine_used": engine.name,
                "ocr_engine_failures": failures,
                "languages": languages,
                "min_confidence": float(args.min_confidence),
                "created_at": now_iso(),
                "ocr_keyframe_count": len(ocr_items),
                "ocr_error_count": len(errors),
            }
            subtitles = {"schema_version": "analysis_v1_visual_subtitle_timeline_0.1", **common, "items": subtitle_items}
            visual_text = {"schema_version": "analysis_v1_visual_text_timeline_0.1", **common, "items": visual_text_items}
            result["counts"] = {
                "keyframes": len(keyframes_payload.get("items") or []),
                "ocr_items": len(ocr_items),
                "ocr_items_with_text": len([item for item in ocr_items if str(item.get("ocr_text") or "").strip()]),
                "subtitle_items": len(subtitle_items),
                "visual_text_items": len(visual_text_items),
                "errors": len(errors),
            }
            finalize_outputs(workspace, subtitles, visual_text, state, result, reused=False)
            result["counts"].update({"keyframes": len(keyframes_payload.get("items") or []), "ocr_items": len(ocr_items), "ocr_items_with_text": len([item for item in ocr_items if str(item.get("ocr_text") or "").strip()]), "errors": len(errors)})
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
    parser = argparse.ArgumentParser(description="Run Analysis_V1 OCR over visual keyframes and build subtitle/visual text timelines.")
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--input-keyframes", default="", help="Workspace-relative keyframes JSON. Defaults to enhanced, then base visual_keyframes.")
    parser.add_argument("--ocr-engine", choices=["auto", "paddleocr", "rapidocr", "easyocr", "tesseract"], default="auto")
    parser.add_argument("--languages", default="ch,en", help="Comma-separated OCR languages.")
    parser.add_argument("--min-confidence", type=float, default=0.45)
    parser.add_argument("--min-chars", type=int, default=2)
    parser.add_argument("--merge-similarity-threshold", type=float, default=0.82)
    parser.add_argument("--max-merge-gap-seconds", type=float, default=4.0)
    parser.add_argument("--no-progressive-merge", action="store_true")
    parser.add_argument("--progressive-similarity-threshold", type=float, default=0.55)
    parser.add_argument("--max-progressive-gap-seconds", type=float, default=4.0)
    parser.add_argument("--max-items", type=int, default=120)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-progress", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        input_keyframes=str(ns.input_keyframes or ""),
        ocr_engine=str(ns.ocr_engine),
        languages=str(ns.languages),
        min_confidence=float(ns.min_confidence),
        min_chars=int(ns.min_chars),
        merge_similarity_threshold=float(ns.merge_similarity_threshold),
        max_merge_gap_seconds=float(ns.max_merge_gap_seconds),
        no_progressive_merge=bool(ns.no_progressive_merge),
        progressive_similarity_threshold=float(ns.progressive_similarity_threshold),
        max_progressive_gap_seconds=float(ns.max_progressive_gap_seconds),
        max_items=int(ns.max_items),
        force=bool(ns.force),
        resume=bool(ns.resume),
        print_progress=bool(ns.print_progress),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    result = run(args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('visual_subtitle_timeline', '')}")
    return 0 if result["status"] in {"completed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
