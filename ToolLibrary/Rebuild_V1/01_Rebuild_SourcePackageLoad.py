from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "RebuildIntentPackageBuilder"
TOOL_VERSION = "0.1.0"
SCHEME_CHOICES = ["detail", "balanced", "summary"]
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"


class DependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    workspace: Path
    meta_dir: Path
    schemes_dir: Path
    rebuild_dir: Path
    keyframes_dir: Path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def fetch_task_context(database_url: str, task_id: int | None, session_id: int | None) -> dict[str, Any]:
    if not task_id and not session_id:
        raise ValueError("Either --task-id or --session-id is required to fetch Final Prompt from database")
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            if task_id:
                cursor.execute(
                    """
SELECT t.id, t.session_id, t.analysis_task_id, a.session_id AS analysis_session_id,
       t.source_package_path, t.final_prompt, t.run_model_provider, t.run_model_id,
       s.workspace_dir, s.opencode_session_id, analysis_s.workspace_dir AS analysis_workspace_dir
FROM oc_rebuild_tasks t
JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_tasks a ON a.id = t.analysis_task_id
LEFT JOIN sessions analysis_s ON analysis_s.id = a.session_id
WHERE t.id = %s
LIMIT 1
""",
                    (task_id,),
                )
            else:
                cursor.execute(
                    """
SELECT t.id, t.session_id, t.analysis_task_id, a.session_id AS analysis_session_id,
       t.source_package_path, t.final_prompt, t.run_model_provider, t.run_model_id,
       s.workspace_dir, s.opencode_session_id, analysis_s.workspace_dir AS analysis_workspace_dir
FROM oc_rebuild_tasks t
JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_tasks a ON a.id = t.analysis_task_id
LEFT JOIN sessions analysis_s ON analysis_s.id = a.session_id
WHERE t.session_id = %s
LIMIT 1
""",
                    (session_id,),
                )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
        if not row:
            target = f"Task #{task_id}" if task_id else f"Session #{session_id}"
            raise RuntimeError(f"OC-Rebuild {target} not found")
        data = dict(zip(columns, row))
        final_prompt = decode_text(data.get("final_prompt")).strip()
        if not final_prompt:
            raise RuntimeError(f"OC-Rebuild Task #{data.get('id')} has no Final Prompt")
        return {
            "task_id": int(data.get("id") or 0),
            "session_id": int(data.get("session_id") or 0),
            "analysis_task_id": int(data.get("analysis_task_id") or 0) or None,
            "analysis_session_id": int(data.get("analysis_session_id") or 0) or None,
            "workspace_dir": decode_text(data.get("workspace_dir")).strip(),
            "analysis_workspace_dir": decode_text(data.get("analysis_workspace_dir")).strip(),
            "source_package_path": decode_text(data.get("source_package_path")).strip() or "source_package.json",
            "final_prompt": final_prompt,
            "run_model_provider": decode_text(data.get("run_model_provider")).strip(),
            "run_model_id": decode_text(data.get("run_model_id")).strip(),
            "opencode_session_id": decode_text(data.get("opencode_session_id")).strip(),
        }
    finally:
        conn.close()


def first_match(patterns: list[str], text: str, default: str = "") -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.S)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ：:，,。；;\n\t")
    return default


def extract_count(text: str) -> int:
    value = first_match([r"生成\s*(\d+)\s*条", r"(\d+)\s*条(?:内容|视频|短视频)", r"target_count\D+(\d+)"], text)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def extract_aspect_ratio(text: str, source_package: dict[str, Any]) -> str:
    value = first_match([r"(\d+\s*:\s*\d+)\s*(?:竖版|横版|比例|短视频|视频)?", r"目标比例[:：\s]+(\d+\s*:\s*\d+)"] , text)
    if value:
        return value.replace(" ", "")
    video = source_package.get("video") if isinstance(source_package.get("video"), dict) else {}
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width and height:
        return "9:16" if height > width else "16:9" if width > height else "1:1"
    return "9:16"


def extract_platform(text: str) -> str:
    return first_match([r"适用于(.+?)的\s*\d+\s*:\s*\d+", r"适用于(.+?)(?:竖版|横版)?短视频", r"目标平台[:：\s]+(.+?)(?:\n|。|；|$)"], text)


def extract_topic(text: str) -> str:
    return first_match([r"新主题为[“\"](.+?)[”\"]", r"主题为[“\"](.+?)[”\"]", r"目标主题为(.+?)(?:，|,|。|；|$)", r"新主题为(.+?)(?:，|,|。|；|$)", r"目标主题[:：\s]+(.+?)(?:\n|。|；|，|,|$)", r"围绕(.+?)场景展开"], text)


def extract_audience(text: str) -> str:
    return first_match([r"目标受众为(.+?)(?:，|,|。|；|$)", r"目标受众[:：\s]+(.+?)(?:\n|。|；|$)"], text)


def extract_product(text: str) -> str:
    return first_match([r"产品/服务为[“\"](.+?)[”\"]", r"产品/服务为(.+?)(?:，|,|。|；|$)", r"产品/服务[:：\s]+(.+?)(?:\n|。|；|$)", r"关联[“\"](.+?)[”\"]"], text)


def sentence_contains(text: str, labels: list[str]) -> bool:
    return any(label in text for label in labels)


def extract_preserve(text: str) -> dict[str, bool]:
    return {
        "duration_pattern": sentence_contains(text, ["分镜时长", "时长模式"]),
        "subtitle_timing": sentence_contains(text, ["字幕节奏", "字幕 timing", "字幕时间"]),
        "semantic_roles": sentence_contains(text, ["语义公式槽位", "语义槽位", "表达功能"]),
        "title_layout": sentence_contains(text, ["标题布局", "标题位置"]),
        "transition_rhythm": sentence_contains(text, ["转场节奏", "转场"]),
        "camera_style": sentence_contains(text, ["镜头/景别", "镜头风格", "景别风格"]),
        "emotion_arc": sentence_contains(text, ["情绪推进", "情绪曲线"]),
    }


def extract_replace(text: str) -> dict[str, bool]:
    return {
        "topic": sentence_contains(text, ["原主题", "替换主题", "新主题"]),
        "visuals": sentence_contains(text, ["画面内容", "替换画面"]),
        "voiceover": sentence_contains(text, ["口播文案", "旁白"]),
        "subtitles": sentence_contains(text, ["字幕文案", "替换字幕"]),
        "title_copy": sentence_contains(text, ["标题文案", "标题表达"]),
        "product": sentence_contains(text, ["产品/服务", "产品引入", "产品"]),
        "persona": sentence_contains(text, ["人物身份", "人群身份"]),
        "bgm": sentence_contains(text, ["替换背景音乐", "替换 bgm", "替换BGM"]),
    }


def extract_style(text: str) -> dict[str, str]:
    return {
        "visual": first_match([r"整体视觉保持(.+?)(?:，|,|。|；|$)", r"视觉风格保持(.+?)(?:，|,|。|；|$)", r"视觉风格[:：\s]+(.+?)(?:\n|。|；|$)"], text),
        "subtitle": first_match([r"字幕采用(.+?)(?:，|,|。|；|$)", r"字幕风格[:：\s]+(.+?)(?:\n|。|；|$)"], text),
        "title": first_match([r"标题采用(.+?)(?:，|,|。|；|$)", r"标题风格[:：\s]+(.+?)(?:\n|。|；|$)"], text),
        "voice": first_match([r"旁白使用(.+?)(?:，|,|。|；|$)", r"声音采用(.+?)(?:，|,|。|；|$)", r"声音风格[:：\s]+(.+?)(?:\n|。|；|$)"], text),
    }


def source_summary(source_package: dict[str, Any]) -> dict[str, Any]:
    video = source_package.get("video") if isinstance(source_package.get("video"), dict) else {}
    segments = source_package.get("segments") if isinstance(source_package.get("segments"), list) else []
    return {
        "source_scheme": str(source_package.get("source_scheme") or "detail"),
        "segment_count": len(segments),
        "duration": float(video.get("duration") or 0.0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": float(video.get("fps") or 0.0),
    }


def split_constraints(value: str) -> dict[str, Any]:
    text = value.strip()
    if not text:
        return {"must_keep": [], "must_avoid": [], "notes": ""}
    avoid: list[str] = []
    keep: list[str] = []
    notes: list[str] = []
    for part in re.split(r"[\n；;。]+", text):
        item = part.strip()
        if not item:
            continue
        if any(token in item for token in ["不要", "禁止", "避免", "不得"]):
            avoid.append(item)
        elif any(token in item for token in ["必须", "保留", "需要"]):
            keep.append(item)
        else:
            notes.append(item)
    return {"must_keep": keep, "must_avoid": avoid, "notes": "。".join(notes)}


def parse_variants(value: str, target_topic: str, target_count: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in value.splitlines():
        line = line.strip(" -\t")
        if not line:
            continue
        match = re.match(r"(variant[_-]?\d+)\s*[：:]\s*(.+)", line, re.I)
        rows.append({"variant_id": match.group(1).replace("-", "_"), "topic": match.group(2).strip()} if match else {"variant_id": f"variant_{len(rows) + 1:03d}", "topic": line})
    while len(rows) < max(1, target_count):
        rows.append({"variant_id": f"variant_{len(rows) + 1:03d}", "topic": target_topic or f"Variant {len(rows) + 1}"})
    return rows[:max(1, target_count)]


def parse_batch_variables(final_prompt: str, target_topic: str, target_count: int) -> list[dict[str, str]]:
    batch_text = first_match([r"批量(?:策略|变量)[:：](.+?)(?:\n\n|限制条件[:：]|$)"], final_prompt)
    if batch_text and ("variant" in batch_text.lower() or "\n" in batch_text):
        return parse_variants(batch_text, target_topic, target_count)
    return parse_variants("", target_topic, target_count)


def extract_batch_strategy(final_prompt: str) -> str:
    return first_match([r"批量(?:策略|变量)[:：](.+?)(?:\n\n|限制条件[:：]|$)"], final_prompt)


def extract_rebuild_goal(final_prompt: str) -> str:
    if "复刻" in final_prompt and "结构" in final_prompt:
        return "复刻参考视频结构"
    return first_match([r"重建目标[:：\s]+(.+?)(?:\n|。|；|$)", r"重建意图[:：](.+?)(?:，|,|。|；|$)"], final_prompt, "")


def build_intent_from_final_prompt(final_prompt: str, source_package: dict[str, Any], source_package_path: str = "") -> dict[str, Any]:
    final_prompt = final_prompt.strip()
    summary = source_summary(source_package)
    topic = extract_topic(final_prompt)
    target_count = extract_count(final_prompt)
    return {
        "version": 1,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "source_scheme": summary["source_scheme"],
        "source": {
            "package_path": source_package_path,
            "segment_count": summary["segment_count"],
            "duration": summary["duration"],
            "format": {"width": summary["width"], "height": summary["height"], "fps": summary["fps"]},
        },
        "target_count": target_count,
        "target": {
            "topic": topic,
            "platform": extract_platform(final_prompt),
            "aspect_ratio": extract_aspect_ratio(final_prompt, source_package),
            "audience": extract_audience(final_prompt),
            "product_info": extract_product(final_prompt),
            "rebuild_goal": extract_rebuild_goal(final_prompt),
        },
        "preserve": extract_preserve(final_prompt),
        "replace": extract_replace(final_prompt),
        "style": extract_style(final_prompt),
        "batch": {"variants": parse_batch_variables(final_prompt, topic, target_count), "strategy": extract_batch_strategy(final_prompt)},
        "constraints": split_constraints(first_match([r"限制条件[:：](.+?)$"], final_prompt)),
        "final_intent_prompt": final_prompt,
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def first_present(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def resolve_paths(workspace: Path, meta_dir: Path | None, schemes_dir: Path | None, rebuild_dir: Path | None) -> Paths:
    resolved_workspace = workspace.expanduser().resolve()
    return Paths(
        workspace=resolved_workspace,
        meta_dir=meta_dir.expanduser().resolve() if meta_dir else resolved_workspace / "meta",
        schemes_dir=schemes_dir.expanduser().resolve() if schemes_dir else resolved_workspace / "schemes",
        rebuild_dir=rebuild_dir.expanduser().resolve() if rebuild_dir else resolved_workspace,
        keyframes_dir=resolved_workspace / "keyframes",
    )


def safe_relative_output(value: str, default_name: str) -> Path:
    raw = value.strip() or default_name
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Output path must be workspace-relative: {value}")
    return path


def sibling_output(source_rel: Path, name: str) -> Path:
    return source_rel.parent / name if str(source_rel.parent) != "." else Path(name)


def rel_path(paths: Paths, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(str(path)).expanduser()
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(paths.workspace).as_posix()
    except ValueError:
        return candidate.as_posix()


def analysis_rel_path(paths: Paths, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(str(path)).expanduser()
    if not candidate.is_absolute():
        return candidate.as_posix()
    analysis_workspace = paths.schemes_dir.parent
    try:
        return candidate.resolve().relative_to(analysis_workspace.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def load_scheme_segments(paths: Paths, scheme: str) -> list[dict[str, Any]]:
    path = paths.meta_dir / f"scheme_{scheme}_segments.json"
    payload = optional_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise DependencyError(f"Rebuild requires Analysis scheme output with non-empty items: {path}")
    rows = [item for item in items if isinstance(item, dict)]
    return sorted(rows, key=lambda item: safe_float(item.get("start")))


def load_video(paths: Paths) -> dict[str, Any]:
    metadata = optional_json(paths.meta_dir / "video_metadata.json")
    if not isinstance(metadata, dict):
        return {"available": False}
    video_path = metadata.get("path") or metadata.get("video_path") or metadata.get("source_video") or ""
    return {
        "available": True,
        "path": rel_path(paths, video_path),
        "duration": safe_float(metadata.get("duration_seconds") or metadata.get("duration")),
        "width": int(safe_float(metadata.get("width"))),
        "height": int(safe_float(metadata.get("height"))),
        "fps": safe_float(metadata.get("fps")),
        "metadata": metadata,
    }


def load_description(paths: Paths, scheme: str, index: int) -> dict[str, Any] | None:
    candidates = [
        paths.meta_dir / "segment_descriptions" / f"scheme_{scheme}" / f"segment_{index:03d}.json",
        paths.schemes_dir / f"scheme_{scheme}" / f"segment_{index:03d}.json",
    ]
    for path in candidates:
        payload = optional_json(path)
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["_path"] = rel_path(paths, path)
            return payload
    return None


def load_scheme_manifest(paths: Paths, scheme: str) -> tuple[dict[str, Any] | None, Path | None]:
    candidates = [paths.schemes_dir / f"scheme_{scheme}" / "manifest.json"]
    if scheme == "detail":
        candidates.append(paths.schemes_dir / "scheme_1" / "manifest.json")
    for path in candidates:
        payload = optional_json(path)
        if isinstance(payload, dict):
            return payload, path.parent
    return None, candidates[0].parent


def existing_rel(paths: Paths, path: Path) -> str:
    return analysis_rel_path(paths, path) if path.exists() else ""


def manifest_item_for_segment(manifest: dict[str, Any] | None, index: int) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {}
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    for item in items:
        if isinstance(item, dict) and int(item.get("segment_index") or 0) == index:
            return item
    return {}


def keyframes_for_segment(segment: dict[str, Any], visual_keyframes: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(visual_keyframes, dict):
        return []
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"), start)
    source_items = visual_keyframes.get("items") or visual_keyframes.get("keyframes") or []
    rows = []
    for item in source_items:
        if not isinstance(item, dict):
            continue
        time_value = safe_float(first_present(item, ("time", "timestamp", "start")), -1.0)
        if time_value < start or time_value > end:
            continue
        rows.append({
            "time": time_value,
            "path": str(item.get("path") or item.get("image_path") or item.get("frame_path") or ""),
            "source": item.get("source") or item.get("type") or "visual_keyframes",
        })
    return rows


def ocr_for_segment(segment: dict[str, Any], ocr_timeline: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(ocr_timeline, dict):
        return []
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"), start)
    source_items = ocr_timeline.get("items") or ocr_timeline.get("segments") or []
    rows = []
    for item in source_items:
        if not isinstance(item, dict):
            continue
        item_start = safe_float(item.get("start") or item.get("time"), -1.0)
        item_end = safe_float(item.get("end"), item_start)
        if min(end, item_end) - max(start, item_start) <= 0:
            continue
        rows.append(item)
    return rows


def build_segment(paths: Paths, scheme: str, segment: dict[str, Any], export_dir: Path | None, visual_keyframes: dict[str, Any] | None, ocr_timeline: dict[str, Any] | None) -> dict[str, Any]:
    index = int(segment.get("index") or 0)
    description = load_description(paths, scheme, index) or {}
    time_info = description.get("time") if isinstance(description.get("time"), dict) else {}
    source_info = description.get("source") if isinstance(description.get("source"), dict) else {}
    subtitle_info = description.get("subtitle") if isinstance(description.get("subtitle"), dict) else {}
    retake_fields = description.get("retake_fields") if isinstance(description.get("retake_fields"), dict) else {}
    base_export_dir = export_dir or paths.schemes_dir / f"scheme_{scheme}"
    manifest, _manifest_dir = load_scheme_manifest(paths, scheme)
    manifest_item = manifest_item_for_segment(manifest, index)
    manifest_clip_path = analysis_rel_path(paths, manifest_item.get("clip_path"))
    manifest_source_video_path = analysis_rel_path(paths, manifest_item.get("source_video_path"))
    clip_status = str(manifest_item.get("clip_status") or "").strip()
    clip_path = manifest_clip_path or existing_rel(paths, base_export_dir / f"segment_{index:03d}.mp4")
    if clip_status == "virtual" and manifest_source_video_path:
        clip_path = manifest_source_video_path
    subtitle_path = existing_rel(paths, base_export_dir / f"segment_{index:03d}.srt") or rel_path(paths, subtitle_info.get("subtitle_path"))
    description_path = existing_rel(paths, base_export_dir / f"segment_{index:03d}.json") or str(description.get("_path") or "")
    start = safe_float(manifest_item.get("start"), safe_float(segment.get("start"), safe_float(time_info.get("start"))))
    end = safe_float(manifest_item.get("end"), safe_float(segment.get("end"), safe_float(time_info.get("end"))))
    return {
        "segment_id": str(description.get("segment_id") or f"{scheme}_segment_{index:03d}"),
        "index": index,
        "start": start,
        "end": end,
        "duration": safe_float(manifest_item.get("duration"), safe_float(segment.get("duration"), max(0.0, end - start))),
        "title": segment.get("title") or source_info.get("title") or "",
        "semantic_role": segment.get("semantic_role") or source_info.get("semantic_role") or "",
        "formula_slot": segment.get("formula_slot") or source_info.get("formula_slot") or "",
        "clip_path": clip_path,
        "source_video_path": manifest_source_video_path,
        "clip_status": clip_status or ("exported" if clip_path else ""),
        "subtitle_path": subtitle_path,
        "description_path": description_path,
        "spoken_script": retake_fields.get("spoken_script") or subtitle_info.get("dialogue_text") or segment.get("dialogue_text") or "",
        "retake_fields": retake_fields,
        "source_segment_indices": segment.get("source_segment_indices") or source_info.get("source_segment_indices") or [],
        "keyframes": keyframes_for_segment(segment, visual_keyframes),
        "ocr_text": ocr_for_segment(segment, ocr_timeline),
        "analysis_segment": segment,
    }


def build_source_package(paths: Paths, scheme: str) -> tuple[dict[str, Any], dict[str, Any]]:
    segments = load_scheme_segments(paths, scheme)
    manifest, export_dir = load_scheme_manifest(paths, scheme)
    visual_keyframes = optional_json(paths.meta_dir / "visual_keyframes.json")
    ocr_timeline = optional_json(paths.meta_dir / "visual_ocr_timeline.json")
    package_segments = [build_segment(paths, scheme, segment, export_dir, visual_keyframes if isinstance(visual_keyframes, dict) else None, ocr_timeline if isinstance(ocr_timeline, dict) else None) for segment in segments]
    dependencies = {
        "scheme_segments": (paths.meta_dir / f"scheme_{scheme}_segments.json").exists(),
        "video_metadata": (paths.meta_dir / "video_metadata.json").exists(),
        "segment_descriptions_dir": (paths.meta_dir / "segment_descriptions" / f"scheme_{scheme}").exists(),
        "scheme_manifest": manifest is not None,
        "visual_keyframes": isinstance(visual_keyframes, dict),
        "visual_ocr_timeline": isinstance(ocr_timeline, dict),
    }
    missing_clips = [item["segment_id"] for item in package_segments if not item["clip_path"]]
    missing_descriptions = [item["segment_id"] for item in package_segments if not item["description_path"]]
    source_package = {
        "version": 1,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "source_scheme": scheme,
        "workspace": str(paths.workspace),
        "source": {
            "analysis_workspace": str(paths.schemes_dir.parent),
            "resource_session": "analysis",
        },
        "video": load_video(paths),
        "segments": package_segments,
        "manifest": manifest or {},
    }
    input_check = {
        "version": 1,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "passed" if not missing_descriptions else "warning",
        "source_scheme": scheme,
        "segment_count": len(package_segments),
        "dependency_status": dependencies,
        "warnings": {
            "missing_clips": missing_clips,
            "missing_descriptions": missing_descriptions,
        },
        "outputs": {
            "source_package": rel_path(paths, paths.rebuild_dir / "source_package.json"),
            "input_check": rel_path(paths, paths.rebuild_dir / "rebuild_input_check.json"),
        },
    }
    return source_package, input_check


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build OpenClip Rebuild source_package.json, rebuild_input_check.json, and rebuild_intent.json from Analysis outputs and Task Final Prompt.")
    parser.add_argument("--workspace", type=Path, default=None, help="OC-Rebuild task workspace. Defaults to the database-bound workspace when --task-id/--session-id is provided.")
    parser.add_argument("--task-id", type=int, default=None, help="OC-Rebuild task id used to fetch Final Prompt from database.")
    parser.add_argument("--session-id", type=int, default=None, help="OC-Rebuild session id used to fetch Final Prompt from database when task id is not provided.")
    parser.add_argument("--database-url", default="", help="Override OpenCrew database URL.")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV, help="Environment variable containing OpenCrew database URL.")
    parser.add_argument("--source-scheme", choices=SCHEME_CHOICES, default="detail", help="Analysis scheme granularity to load.")
    parser.add_argument("--meta-dir", type=Path, default=None, help="Override Analysis meta directory.")
    parser.add_argument("--schemes-dir", type=Path, default=None, help="Override Analysis schemes directory.")
    parser.add_argument("--rebuild-dir", type=Path, default=None, help="Override Rebuild output directory. Defaults to the workspace root for OC-Rebuild sessions.")
    parser.add_argument("--print-json", action="store_true", help="Print run result JSON to stdout.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    database_url = args.database_url or os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
    task_context: dict[str, Any] = {}
    if args.task_id or args.session_id:
        task_context = fetch_task_context(database_url, args.task_id, args.session_id)
    workspace = args.workspace or (Path(str(task_context.get("workspace_dir"))) if task_context.get("workspace_dir") else None)
    if workspace is None:
        raise SystemExit("--workspace is required when --task-id/--session-id is not provided")
    analysis_workspace = Path(str(task_context.get("analysis_workspace_dir") or "")) if task_context.get("analysis_workspace_dir") else None
    meta_dir = args.meta_dir or (analysis_workspace / "meta" if analysis_workspace else None)
    schemes_dir = args.schemes_dir or (analysis_workspace / "schemes" if analysis_workspace else None)
    source_rel = safe_relative_output(str(task_context.get("source_package_path") or "source_package.json"), "source_package.json")
    rebuild_dir = args.rebuild_dir or (workspace / source_rel.parent if str(source_rel.parent) != "." else workspace)
    source_name = source_rel.name
    input_check_name = sibling_output(Path(source_name), "rebuild_input_check.json")
    intent_name = sibling_output(Path(source_name), "rebuild_intent.json")
    paths = resolve_paths(workspace, meta_dir, schemes_dir, rebuild_dir)
    try:
        source_package, input_check = build_source_package(paths, str(args.source_scheme))
        write_json(paths.rebuild_dir / source_name, source_package)
        write_json(paths.rebuild_dir / input_check_name, input_check)
        intent_output = ""
        if task_context:
            intent = build_intent_from_final_prompt(str(task_context["final_prompt"]), source_package, source_rel.as_posix())
            write_json(paths.rebuild_dir / intent_name, intent)
            intent_output = rel_path(paths, paths.rebuild_dir / intent_name)
        result = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "status": "completed",
            "workspace": str(paths.workspace),
            "source_scheme": str(args.source_scheme),
            "segment_count": len(source_package["segments"]),
            "outputs": input_check["outputs"],
            "input_check_status": input_check["status"],
        }
        if intent_output:
            result["outputs"]["rebuild_intent"] = intent_output
    except Exception as exc:
        result = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "status": "failed",
            "workspace": str(paths.workspace),
            "source_scheme": str(args.source_scheme),
            "error_code": "missing_dependency" if isinstance(exc, DependencyError) else "runtime_error",
            "message": str(exc),
        }
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
