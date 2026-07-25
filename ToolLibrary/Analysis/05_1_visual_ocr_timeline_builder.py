from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol


TOOL_NAME = "VisualOCRTimelineBuilder"
TOOL_VERSION = "0.1.0"


class DependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    workspace: Path | None
    meta_dir: Path
    input_keyframes: Path


class OCREngine(Protocol):
    name: str

    def recognize(self, image_path: Path) -> list[dict[str, Any]]:
        ...


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


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_paths(workspace: Path | None, output_dir: Path | None, input_keyframes: Path | None) -> Paths:
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    if output_dir is not None:
        meta_dir = output_dir.expanduser().resolve()
    elif resolved_workspace is not None:
        meta_dir = resolved_workspace / "meta"
    else:
        meta_dir = Path.cwd() / "meta"
    keyframes_path = input_keyframes.expanduser().resolve() if input_keyframes else meta_dir / "visual_keyframes.json"
    return Paths(workspace=resolved_workspace, meta_dir=meta_dir, input_keyframes=keyframes_path)


def resolve_image_path(path_value: str, workspace: Path | None) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    if workspace is not None:
        return (workspace / path).resolve()
    return path.resolve()


def relative_path(path: Path, workspace: Path | None) -> str:
    if workspace is None:
        return str(path)
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path)


def load_keyframes(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise DependencyError(
            "05_1 requires 05 VisualEvidenceExtractor output: "
            f"{path}. Run 05_visual_evidence_extractor.py before 05_1."
        )
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise DependencyError(f"Invalid 05 dependency output: {path} must contain an items list.")
    keyframes = [item for item in items if isinstance(item, dict) and item.get("path")]
    if not keyframes:
        raise DependencyError(f"Invalid 05 dependency output: {path} has no keyframe items with paths.")
    return keyframes


def load_scenes(meta_dir: Path) -> list[dict[str, Any]]:
    path = meta_dir / "pyscenedetect_scenes.json"
    if not path.exists():
        return []
    payload = read_json(path)
    rows = payload.get("scenes") if isinstance(payload, dict) else None
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def scene_for_time(scenes: list[dict[str, Any]], time_value: float) -> dict[str, Any] | None:
    for scene in scenes:
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or start)
        if start <= time_value <= end:
            return scene
    return None


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


def normalize_text(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text or "")
    cleaned = re.sub(r"[|｜_＿=]+", "", cleaned)
    return cleaned.strip()


def display_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned


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


def text_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": round(float(item.get("time") or 0.0), 3),
        "text": str(item.get("ocr_text") or "").strip(),
        "confidence": round(float(item.get("confidence") or 0.0), 4),
        "path": item.get("path"),
    }


def choose_representative(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"text": "", "time": 0.0, "path": "", "confidence": 0.0}
    return max(
        candidates,
        key=lambda item: (
            len(normalize_text(str(item.get("text") or ""))),
            float(item.get("confidence") or 0.0),
            float(item.get("time") or 0.0),
        ),
    )


def looks_like_noise(text: str, min_chars: int) -> bool:
    compact = normalize_text(text)
    if len(compact) < min_chars:
        return True
    if re.fullmatch(r"[\W_\d]+", compact):
        return True
    return False


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
    if re.search(r"¥|￥|\b\d+(?:\.\d+)?\s*(?:元|ml|g|kg|斤|瓶|盒|片|粒|%|折)\b", text, flags=re.I):
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


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


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
            result, _ = raw
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
        blocks: list[dict[str, Any]] = []
        for item in raw or []:
            try:
                box, text, confidence = item[0], str(item[1] or "").strip(), float(item[2] or 0.0)
            except Exception:
                continue
            if text:
                blocks.append({"text": text, "confidence": round(confidence, 4), "bbox": box})
        return blocks


class TesseractEngine:
    name = "tesseract"

    def __init__(self, languages: list[str]) -> None:
        if shutil.which("tesseract") is None:
            raise RuntimeError("tesseract command not found")
        self._language = "+".join(["chi_sim" if item.lower() in {"ch", "zh", "cn"} else item for item in (languages or ["ch", "en"])])

    def recognize(self, image_path: Path) -> list[dict[str, Any]]:
        command = ["tesseract", str(image_path), "stdout", "-l", self._language, "--psm", "6", "tsv"]
        proc = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "tesseract failed")
        blocks: list[dict[str, Any]] = []
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < 12:
                continue
            text = parts[11].strip()
            if not text:
                continue
            try:
                confidence = max(0.0, float(parts[10]) / 100.0)
                left, top, width, height = [int(float(parts[index])) for index in (6, 7, 8, 9)]
            except Exception:
                confidence = 0.0
                left = top = width = height = 0
            blocks.append({"text": text, "confidence": round(confidence, 4), "bbox": [left, top, left + width, top + height]})
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
    raise DependencyError("No OCR engine is available. Tried: " + json.dumps(failures, ensure_ascii=False))


def ocr_keyframes(paths: Paths, keyframes: list[dict[str, Any]], engine: OCREngine, scenes: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, keyframe in enumerate(keyframes, start=1):
        image_path = resolve_image_path(str(keyframe.get("path") or ""), paths.workspace)
        base = {
            "index": index,
            "time": round(float(keyframe.get("time") or 0.0), 3),
            "frame": keyframe.get("frame"),
            "source": keyframe.get("source"),
            "segment_index": keyframe.get("segment_index"),
            "role": keyframe.get("role"),
            "path": relative_path(image_path, paths.workspace),
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
        enriched_blocks: list[dict[str, Any]] = []
        for block in raw_blocks:
            if float(block.get("confidence") or 0.0) < float(args.min_confidence) or looks_like_noise(str(block.get("text") or ""), int(args.min_chars)):
                continue
            layout = layout_features(block, width, height)
            text_class, class_confidence, class_reasons = classify_ocr_block({**block, "layout": layout})
            enriched_blocks.append({**block, "layout": layout, "text_class": text_class, "class_confidence": class_confidence, "classification_reason": class_reasons})
        blocks = enriched_blocks
        text = display_text(" ".join(str(block.get("text") or "") for block in blocks))
        confidence = round(average([float(block.get("confidence") or 0.0) for block in blocks]), 4)
        class_counts: dict[str, int] = {}
        for block in blocks:
            key = str(block.get("text_class") or "other_visual_text")
            class_counts[key] = class_counts.get(key, 0) + 1
        items.append({**base, "image_width": width, "image_height": height, "ocr_text": text, "ocr_blocks": blocks, "confidence": confidence, "block_count": len(blocks), "class_counts": class_counts})
        if args.print_progress:
            print(f"[05_1 OCR] keyframe={index}/{len(keyframes)} time={base['time']:.3f} blocks={len(blocks)}")
    return items, errors


def merge_timeline(ocr_items: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
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
            duplicate = gap <= float(args.max_merge_gap_seconds) and similarity >= float(args.merge_similarity_threshold)
            progressive = (
                not bool(args.no_progressive_merge)
                and gap <= float(args.max_progressive_gap_seconds)
                and is_progressive_reveal(str(current.get("text") or ""), text, float(args.progressive_similarity_threshold))
            )
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
    max_items = int(args.max_items_for_llm)
    if max_items > 0 and len(timeline) > max_items:
        return timeline[:max_items]
    return timeline


def scene_visual_ocr_alignment(scenes: list[dict[str, Any]], subtitle_items: list[dict[str, Any]], visual_text_items: list[dict[str, Any]], keyframes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not scenes:
        scene_ids = sorted({int(item.get("scene_index") or 0) for item in subtitle_items + visual_text_items if item.get("scene_index")})
        scenes = [{"index": scene_id, "start": min([float(item.get("start") or item.get("time") or 0.0) for item in subtitle_items + visual_text_items if int(item.get("scene_index") or 0) == scene_id] or [0.0]), "end": max([float(item.get("end") or item.get("time") or 0.0) for item in subtitle_items + visual_text_items if int(item.get("scene_index") or 0) == scene_id] or [0.0])} for scene_id in scene_ids]
    rows: list[dict[str, Any]] = []
    for scene in scenes:
        scene_index = int(scene.get("index") or len(rows) + 1)
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or start)
        subtitles = [item for item in subtitle_items if int(item.get("scene_index") or 0) == scene_index or (start <= float(item.get("start") or 0.0) <= end)]
        visuals = [item for item in visual_text_items if int(item.get("scene_index") or 0) == scene_index or (start <= float(item.get("start") or 0.0) <= end)]
        reps = [item for item in keyframes if start <= float(item.get("time") or 0.0) <= end]
        rows.append({
            "scene_index": scene_index,
            "scene_start": round(start, 3),
            "scene_end": round(end, 3),
            "subtitle_items": subtitles,
            "visual_text_items": visuals,
            "representative_keyframes": [{"time": item.get("time"), "path": item.get("path"), "role": item.get("role")} for item in reps],
            "ocr_coverage": {"subtitle_count": len(subtitles), "visual_text_count": len(visuals), "has_subtitle": bool(subtitles)},
        })
    return rows


def run_builder(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    keyframes = load_keyframes(paths.input_keyframes)
    scenes = load_scenes(paths.meta_dir)
    languages = [item.strip() for item in str(args.languages).split(",") if item.strip()]
    engine, engine_failures = build_engine(str(args.ocr_engine), languages)
    ocr_items, errors = ocr_keyframes(paths, keyframes, engine, scenes, args)
    timeline = merge_timeline(ocr_items, args)
    subtitle_items = merge_timeline(split_items_by_block_class(ocr_items, {"subtitle_candidate"}), args)
    for item in subtitle_items:
        item["text_class"] = "subtitle_candidate"
    visual_text_items = merge_timeline(split_items_by_block_class(ocr_items, {"product_text", "brand_text", "price_spec", "title_card", "other_visual_text"}), args)
    for item in visual_text_items:
        item["text_class"] = item.get("text_class") or "visual_text"
    scene_alignment = scene_visual_ocr_alignment(scenes, subtitle_items, visual_text_items, keyframes)
    common = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workspace": str(paths.workspace) if paths.workspace else "",
        "input_keyframes": str(paths.input_keyframes),
        "ocr_engine_requested": str(args.ocr_engine),
        "ocr_engine_used": engine.name,
        "ocr_engine_failures": engine_failures,
        "languages": languages,
        "min_confidence": float(args.min_confidence),
        "merge_similarity_threshold": float(args.merge_similarity_threshold),
        "progressive_merge_enabled": not bool(args.no_progressive_merge),
        "progressive_similarity_threshold": float(args.progressive_similarity_threshold),
        "max_progressive_gap_seconds": float(args.max_progressive_gap_seconds),
    }
    write_json(paths.meta_dir / "visual_ocr_text.json", {**common, "items": ocr_items, "errors": errors})
    write_json(paths.meta_dir / "visual_ocr_timeline.json", {**common, "items": timeline})
    write_json(paths.meta_dir / "visual_subtitle_timeline.json", {**common, "items": subtitle_items})
    write_json(paths.meta_dir / "visual_text_timeline.json", {**common, "items": visual_text_items})
    write_json(paths.meta_dir / "scene_visual_ocr_alignment.json", {**common, "items": scene_alignment})
    summary_lines = [
        f"# {TOOL_NAME}",
        "",
        f"- status: completed",
        f"- ocr_engine_used: {engine.name}",
        f"- keyframes: {len(keyframes)}",
        f"- ocr_items: {len(ocr_items)}",
        f"- timeline_items: {len(timeline)}",
        f"- subtitle_items: {len(subtitle_items)}",
        f"- visual_text_items: {len(visual_text_items)}",
        f"- progressive_groups: {len([item for item in timeline if 'progressive_text_reveal' in item.get('merge_reasons', [])])}",
        f"- errors: {len(errors)}",
    ]
    write_text(paths.meta_dir / "visual_ocr_timeline_summary.md", "\n".join(summary_lines) + "\n")
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "ocr_engine_requested": str(args.ocr_engine),
        "ocr_engine_used": engine.name,
        "outputs": {
            "visual_ocr_text": str(paths.meta_dir / "visual_ocr_text.json"),
            "visual_ocr_timeline": str(paths.meta_dir / "visual_ocr_timeline.json"),
            "visual_subtitle_timeline": str(paths.meta_dir / "visual_subtitle_timeline.json"),
            "visual_text_timeline": str(paths.meta_dir / "visual_text_timeline.json"),
            "scene_visual_ocr_alignment": str(paths.meta_dir / "scene_visual_ocr_alignment.json"),
            "summary": str(paths.meta_dir / "visual_ocr_timeline_summary.md"),
        },
        "counts": {
            "keyframes": len(keyframes),
            "ocr_items": len(ocr_items),
            "ocr_items_with_text": len([item for item in ocr_items if str(item.get("ocr_text") or "").strip()]),
            "timeline_items": len(timeline),
            "subtitle_items": len(subtitle_items),
            "visual_text_items": len(visual_text_items),
            "scene_alignment_items": len(scene_alignment),
            "progressive_groups": len([item for item in timeline if "progressive_text_reveal" in item.get("merge_reasons", [])]),
            "merged_groups": len([item for item in timeline if len(item.get("source_keyframe_times") or []) > 1]),
            "errors": len(errors),
        },
    }
    write_json(paths.meta_dir / "05_1_visual_ocr_timeline_builder_result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OCR over 05 keyframes and build a visual OCR timeline for semantic segmentation.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta.")
    parser.add_argument("--input-keyframes", help="Explicit visual_keyframes.json path. Defaults to <workspace>/meta/visual_keyframes.json.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--ocr-engine", choices=["auto", "paddleocr", "rapidocr", "easyocr", "tesseract"], default="auto")
    parser.add_argument("--languages", default="ch,en", help="Comma-separated OCR languages. Default: ch,en.")
    parser.add_argument("--min-confidence", type=float, default=0.45)
    parser.add_argument("--min-chars", type=int, default=2)
    parser.add_argument("--merge-similarity-threshold", type=float, default=0.82)
    parser.add_argument("--max-merge-gap-seconds", type=float, default=4.0)
    parser.add_argument("--no-progressive-merge", action="store_true", help="Disable merging progressively revealed text across adjacent keyframes.")
    parser.add_argument("--progressive-similarity-threshold", type=float, default=0.55)
    parser.add_argument("--max-progressive-gap-seconds", type=float, default=4.0)
    parser.add_argument("--max-items-for-llm", type=int, default=80)
    parser.add_argument("--print-progress", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()

    workspace = Path(args.workspace) if args.workspace else None
    paths = resolve_paths(workspace, Path(args.output_dir) if args.output_dir else None, Path(args.input_keyframes) if args.input_keyframes else None)
    try:
        result = run_builder(paths, args)
    except Exception as exc:
        error_code = type(exc).__name__
        message = str(exc)
        result = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "status": "failed",
            "workspace": str(paths.workspace) if paths.workspace else "",
            "error_code": error_code,
            "message": message,
            "required_dependencies": ["05 visual_keyframes.json", "one OCR engine: paddleocr, rapidocr, easyocr, or tesseract"],
        }
        write_json(paths.meta_dir / "05_1_visual_ocr_timeline_builder_result.json", result)
        if args.print_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result['outputs']['visual_ocr_timeline']}")


if __name__ == "__main__":
    main()
