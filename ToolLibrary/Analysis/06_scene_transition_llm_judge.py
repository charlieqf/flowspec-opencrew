from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOOL_NAME = "SceneTransitionLLMJudge"
TOOL_VERSION = "0.2.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
TRANSITION_DEFINITION = "shooting_location_transition_only"
PROMPT_SCENE_SOURCE = "final_prompt_extracted"
FALLBACK_SCENES = [
    {"scene_id": "unknown_or_other", "label": "未知或其它", "definition": "不能稳定归入已知主拍摄场景的实景画面", "visual_anchors": []},
    {"scene_id": "non_live_visual", "label": "非实景画面", "definition": "标题页、黑屏、截图、字幕卡、图文插页等非连续实景画面", "visual_anchors": []},
]


@dataclass(frozen=True)
class OpenCodeConfig:
    base_url: str
    username: str
    password: str
    directory: str
    session_id: str
    model: dict[str, str]
    task_id: int
    opencrew_session_id: int
    final_prompt: str


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_int_set(value: str | None) -> set[int]:
    result: set[int] = set()
    for part in re.split(r"[,，\s]+", str(value or "").strip()):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            if start.strip().isdigit() and end.strip().isdigit():
                result.update(range(int(start), int(end) + 1))
            continue
        if part.isdigit():
            result.add(int(part))
    return result


def rel_path(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def split_scene_terms(value: str) -> list[str]:
    terms: list[str] = []
    for raw in re.split(r"[、,，/／|;；\n\r]+|以及|或者|和|与", value):
        item = re.sub(r"^[\s:：\-—]+|[\s。.!！?？:：\-—]+$", "", raw).strip()
        item = re.sub(r"^(包括|包含|有|为|分别是|主要是)", "", item).strip()
        item = re.sub(r"[这共]?[一二三四五六七八九十几多0-9]+个(?:主)?(?:场景|地点|空间).*$", "", item).strip()
        item = re.sub(r"这[一二三四五六七八九十几多0-9]+个主?.*$", "", item).strip()
        item = re.sub(r"(等|这几个|三个|多个|几个)$", "", item).strip()
        if 1 < len(item) <= 14:
            terms.append(item)
    return terms


def looks_like_scene_term(value: str) -> bool:
    if not value or len(value) < 2 or len(value) > 12:
        return False
    place_markers = {
        "厨", "厨房", "后厨", "前厅", "厅", "宴会", "走道", "过道", "通道", "走廊", "廊",
        "门", "门口", "大门", "入口", "出口", "房", "房间", "室", "间", "区", "区域", "场", "馆", "店", "院", "楼", "台",
    }
    if any(marker in value for marker in place_markers):
        return True
    return bool(re.search(r"(?:厅|房|室|间|区|道|廊|口|门|场|馆|店|院|楼|台)$", value))


def extract_prompt_scene_terms(final_prompt: str) -> list[dict[str, Any]]:
    """Extract explicit scene/place nouns from Final Prompt without reading task business fields."""
    text = compact_text(final_prompt)
    candidates: list[str] = []
    list_patterns = [
        r"(?:主场景|主要场景|拍摄场景地点|场景地点|拍摄地点|地点|拍摄空间|物理空间)(?:包括|包含|有|为|分别是|主要是)?[:：]?([^。.!！?？]{2,120})",
        r"(?:有|包含|包括)([^。.!！?？]{2,80})(?:这(?:三|四|五|几|多)个)?(?:主场景|场景|地点|空间)",
    ]
    for pattern in list_patterns:
        for match in re.finditer(pattern, text):
            candidates.extend(split_scene_terms(match.group(1)))

    suffix_pattern = r"(?<![一-龥A-Za-z0-9])([一-龥A-Za-z0-9]{1,10}(?:厅|房|室|间|区|道|廊|口|门|场|馆|店|院|楼|台|库|房间|走廊|过道|通道))(?![一-龥A-Za-z0-9])"
    candidates.extend(match.group(1) for match in re.finditer(suffix_pattern, text))

    stop_terms = {"场景", "地点", "空间", "主场景", "拍摄场景", "物理空间", "同一空间", "新空间", "画面", "镜头", "任务", "话题", "转换", "情绪铺垫", "推进脉络"}
    seen: set[str] = set()
    labels: list[dict[str, Any]] = []
    for term in candidates:
        cleaned = re.sub(r"^(有|在|到|从|进入|来到|切到|切回|回到|转入|转到)", "", term).strip()
        cleaned = re.sub(r"[这共]?[一二三四五六七八九十几多0-9]+个(?:主)?(?:场景|地点|空间).*$", "", cleaned).strip()
        cleaned = re.sub(r"这[一二三四五六七八九十几多0-9]+个主?.*$", "", cleaned).strip()
        cleaned = re.sub(r"(里|内|外|中|里面|内部|阶段|环节)$", "", cleaned).strip()
        if cleaned in stop_terms or len(cleaned) < 2 or cleaned in seen or not looks_like_scene_term(cleaned):
            continue
        if len(labels) >= 12:
            break
        seen.add(cleaned)
        labels.append({
            "scene_id": f"scene_{len(labels) + 1:02d}",
            "label": cleaned,
            "aliases": [],
            "definition": "从 Final Prompt 明确提取的主拍摄场景/地点名词，优先用于画面归类。",
            "visual_anchors": [],
        })
    return labels


def build_scene_taxonomy_seed(final_prompt: str) -> dict[str, Any]:
    labels = extract_prompt_scene_terms(final_prompt)
    return {
        "source": PROMPT_SCENE_SOURCE if labels else "inferred_required",
        "labels": labels,
        "fallback_labels": FALLBACK_SCENES,
    }


def load_human_overrides(meta_dir: Path) -> dict[str, Any]:
    path = meta_dir / "scene_transition_human_overrides.json"
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def postgres_connect(database_url: str) -> Any:
    try:
        import psycopg  # type: ignore
    except Exception as exc:
        raise RuntimeError("PostgreSQL driver is not available. Install psycopg[binary] in the OpenCrew runtime.") from exc
    return psycopg.connect(normalize_database_url(database_url))


def get_setting(conn: Any, key: str) -> Any:
    with conn.cursor() as cursor:
        cursor.execute("SELECT value FROM app_settings WHERE key = %s LIMIT 1", (key,))
        row = cursor.fetchone()
    if not row:
        return None
    try:
        return json.loads(decode_text(row[0]))
    except Exception:
        return None


def fetch_opencode_config(database_url: str, task_id: int) -> OpenCodeConfig:
    conn = postgres_connect(database_url)
    try:
        base_url = str(get_setting(conn, "opencode.base_url") or "").strip().rstrip("/")
        username = str(get_setting(conn, "opencode.username") or "").strip()
        password = str(get_setting(conn, "opencode.password") or "").strip()
        if not base_url or not username or not password:
            raise RuntimeError("OpenCode connection is incomplete in app_settings")
        with conn.cursor() as cursor:
            cursor.execute(
                """
SELECT t.id, t.final_prompt, t.run_model_provider, t.run_model_id,
       s.id AS opencrew_session_id, s.opencode_session_id, s.workspace_dir
FROM openclip_tasks t
JOIN sessions s ON s.id = t.session_id
WHERE t.id = %s
LIMIT 1
""",
                (task_id,),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
        if not row:
            raise RuntimeError(f"OpenClip Task #{task_id} not found")
        data = dict(zip(columns, row))
        final_prompt = decode_text(data.get("final_prompt")).strip()
        if not final_prompt:
            raise RuntimeError(f"Task #{task_id} has no final_prompt")
        session_id = decode_text(data.get("opencode_session_id")).strip()
        if not session_id:
            raise RuntimeError(f"Task #{task_id} has no bound OpenCode session")
        provider = decode_text(data.get("run_model_provider")).strip()
        model_id = decode_text(data.get("run_model_id")).strip()
        if not provider or not model_id:
            raise RuntimeError(f"Task #{task_id} has no run model configured")
        return OpenCodeConfig(
            base_url=base_url,
            username=username,
            password=password,
            directory=decode_text(data.get("workspace_dir")).strip(),
            session_id=session_id,
            model={"providerID": provider, "modelID": model_id},
            task_id=task_id,
            opencrew_session_id=int(data.get("opencrew_session_id") or 0),
            final_prompt=final_prompt,
        )
    finally:
        conn.close()


def request_json(config: OpenCodeConfig, method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, str] | None = None, timeout: int = 120) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    token = base64.b64encode(f"{config.username}:{config.password}".encode("utf-8")).decode("ascii")
    query_string = f"?{urlencode(query)}" if query else ""
    req = Request(
        f"{config.base_url}{path}{query_string}",
        data=data,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    return json.loads(raw) if raw else None


def ensure_model_supports_image(config: OpenCodeConfig) -> None:
    payload = request_json(config, "GET", "/provider", None, query={"directory": config.directory}, timeout=30) or {}
    connected = {str(item) for item in (payload.get("connected") or []) if item}
    provider_id = config.model["providerID"]
    model_id = config.model["modelID"]
    if provider_id not in connected:
        raise RuntimeError(f"OpenCode provider is not connected: {provider_id}")
    for provider in payload.get("all") or []:
        if str(provider.get("id") or "") != provider_id:
            continue
        for model in (provider.get("models") or {}).values():
            if str((model or {}).get("id") or "") != model_id:
                continue
            modalities = [str(item) for item in (((model or {}).get("modalities") or {}).get("input") or [])]
            if modalities and "image" not in modalities:
                raise RuntimeError(f"Selected run model does not support image input: {provider_id}/{model_id}")
            return
    raise RuntimeError(f"Selected run model not found in OpenCode providers: {provider_id}/{model_id}")


def create_opencode_session(config: OpenCodeConfig, title: str) -> OpenCodeConfig:
    payload = request_json(config, "POST", "/session", {"title": title}, query={"directory": config.directory}, timeout=30) or {}
    session_id = str(payload.get("id") or "").strip()
    if not session_id:
        raise RuntimeError("OpenCode failed to create a recovery session")
    return replace(config, session_id=session_id)


def session_has_stale_assistant(config: OpenCodeConfig, stale_after_seconds: int = 60) -> bool:
    try:
        messages = request_json(config, "GET", f"/session/{config.session_id}/message", None, query={"directory": config.directory, "limit": "8"}, timeout=30) or []
    except Exception:
        return True
    now_ms = int(time.time() * 1000)
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
        created = int(((info.get("time") or {}).get("created") or 0) or 0)
        if completed:
            return False
        if created and now_ms - created > stale_after_seconds * 1000:
            return True
        return False
    return False


def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str | None:
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
        if completed < started_after:
            continue
        texts = [str(part.get("text") or "") for part in (message.get("parts") or []) if part.get("type") == "text"]
        text = "\n".join([item.strip() for item in texts if item.strip()]).strip()
        if text:
            return text
    return None


def image_file_part(path: Path, workspace: Path) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    try:
        filename = path.relative_to(workspace).as_posix()
    except ValueError:
        filename = path.name
    return {"type": "file", "mime": mime, "filename": filename, "url": f"data:{mime};base64,{encoded}"}


def call_opencode(config: OpenCodeConfig, workspace: Path, prompt: str, image_paths: list[Path], system_prompt: str, timeout_seconds: int) -> str:
    started_at = int(time.time() * 1000)
    parts = [{"type": "text", "text": prompt}] + [image_file_part(path, workspace) for path in image_paths]
    payload = {"parts": parts, "model": config.model, "system": system_prompt}
    request_json(config, "POST", f"/session/{config.session_id}/prompt_async", payload, query={"directory": config.directory}, timeout=30)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        messages = request_json(config, "GET", f"/session/{config.session_id}/message", None, query={"directory": config.directory, "limit": "180"}, timeout=30) or []
        text = last_completed_assistant(messages, started_at)
        if text:
            return text
        time.sleep(2)
    raise RuntimeError("OpenCode timed out before returning scene transition judgement")


def extract_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, re.S)
    if fenced:
        value = fenced.group(1).strip()
    else:
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            value = value[start : end + 1]
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")
    return parsed


def repair_json(config: OpenCodeConfig, workspace: Path, bad_text: str, error: str, timeout_seconds: int) -> dict[str, Any]:
    prompt = f"""
请把下面内容修复为合法 JSON 对象。只返回 JSON，不要解释。

错误：{error}

原始内容：
{bad_text}
""".strip()
    text = call_opencode(config, workspace, prompt, [], build_system_prompt(), timeout_seconds)
    payload = extract_json_object(text)
    validate_batch_payload(payload)
    return payload


def build_system_prompt() -> str:
    return """
你是视频主拍摄场景分类与换场判断器。
你的首要任务是把每张画面归类到主拍摄场景，再判断 cut 是否打开新的连续主拍摄场景段。
如果 Final Prompt 或输入的 scene_taxonomy_seed 明确列出主场景/地点名词，必须优先使用这些标签给画面分类；不要因为角度、人物、景别、桌位、光线、动作、任务阶段、话题或字幕变化创建新场景。
如果 Final Prompt 没有明确主场景标签，才允许根据图片归纳一套稳定 scene_taxonomy，并在本批和后续上下文中保持一致。
同一主场景内的不同机位、不同角度、不同人物、不同景别、不同桌位或同一大空间内部走位，必须判断为非换场。
短暂插帧、B-roll、闪回、说明性补画面、同一段内快速 A-B-A 切回切出，必须判断为非真实换场。
只有 before_scene 与 after_scene 属于不同主场景，而且 after_scene 开启新的、可持续的连续拍摄场景段时，才判断为 true。
标题页、黑屏、截图、字幕卡、图文插页等归入 non_live_visual；它们可以作为特殊场景类型，但短暂插入仍需判断为 false。
只返回合法 JSON 对象，不要 Markdown、不要解释文字、不要代码块。
""".strip()


def build_user_prompt(final_prompt: str, batch: list[dict[str, Any]], scene_taxonomy_seed: dict[str, Any], timeline_context: dict[str, Any], human_overrides: dict[str, Any]) -> str:
    schema = {
        "scene_taxonomy": {
            "source": "final_prompt|inferred|mixed",
            "labels": [{"scene_id": "scene_01", "label": "主场景名称", "aliases": ["别名"], "definition": "主场景定义", "visual_anchors": ["稳定视觉锚点"]}],
            "fallback_labels": FALLBACK_SCENES,
        },
        "final_prompt_scene_terms": [{"scene_id": "scene_01", "label": "从Final Prompt提取的场景地点名词", "source_text": "Final Prompt原文依据", "confidence": 0.0}],
        "items": [
            {
                "cut_index": 1,
                "time": 0.0,
                "frame_classification": [
                    {"frame_id": "prev_context", "scene_id": "scene_01", "scene_label": "主场景名称", "confidence": 0.0, "visual_evidence": ["证据"]},
                    {"frame_id": "before_cut", "scene_id": "scene_01", "scene_label": "主场景名称", "confidence": 0.0, "visual_evidence": ["证据"]},
                    {"frame_id": "after_cut", "scene_id": "scene_02", "scene_label": "主场景名称", "confidence": 0.0, "visual_evidence": ["证据"]},
                    {"frame_id": "next_context", "scene_id": "scene_02", "scene_label": "主场景名称", "confidence": 0.0, "visual_evidence": ["证据"]},
                ],
                "before_scene_id": "scene_01",
                "after_scene_id": "scene_02",
                "before_scene_label": "主场景A",
                "after_scene_label": "主场景B",
                "same_scene_class": False,
                "same_physical_space": False,
                "same_space_different_angle": False,
                "brief_cutaway_or_broll": False,
                "opens_sustained_scene_segment": True,
                "relation_to_previous_context": "same_as_current_scene|new_sustained_scene|brief_insert|return_to_previous_scene|unknown",
                "is_shooting_location_transition": True,
                "transition_type": "location_change|indoor_outdoor_change|room_change|area_change|title_card_to_location|location_to_title_card|same_location_visual_cut|uncertain",
                "before_location_label": "根据图片归纳的空间标签",
                "after_location_label": "根据图片归纳的空间标签",
                "confidence": 0.0,
                "reason": "为什么是/不是拍摄空间变化",
                "not_transition_reason": "如果不是转换，说明原因；否则空字符串",
                "visual_evidence": ["前后帧中可见的空间布局、背景、环境或物理场地变化证据"],
                "final_prompt_hint_used": "使用的Final Prompt空间线索或空字符串",
            }
        ],
    }
    return """
请根据 Final Prompt、scene_taxonomy_seed、timeline_context、待判断 cut 的四帧时序信息，以及随附的 contact sheet 图片，先做主场景分类，再判断每个 cut 是否发生“新的连续主拍摄场景段”的转换。

判断约束：
1. 如果 scene_taxonomy_seed.source 是 final_prompt_extracted，必须优先使用其中 labels 作为画面分类标签；每张实景画面尽量归入这些主场景之一。
2. 如果 scene_taxonomy_seed.source 是 inferred_required，先根据图片归纳稳定主场景 taxonomy，再用这套 taxonomy 分类每张画面。
3. 每个 cut 有四帧：prev_context、before_cut、after_cut、next_context。请分别分类，不能只比较 before/after 两张图。
4. before_scene_id 与 after_scene_id 相同，或者属于同一大空间不同角度，必须标记为 false。
5. after_scene 必须在 after_cut 与 next_context 或后续上下文中持续成立，才允许 opens_sustained_scene_segment=true。
6. 如果是短暂插帧、B-roll、截图、标题卡、回切或 A-B-A 中间短段，应标记 brief_cutaway_or_broll=true 且 is_shooting_location_transition=false。
7. 图片上标注了 cut index、time、PREV/BEFORE/AFTER/NEXT。请逐个 cut 输出判断。
8. 只返回 JSON，结构必须符合 schema。

目标 JSON schema：
{schema_json}

Final Prompt：
{final_prompt}

scene_taxonomy_seed：
{scene_taxonomy_seed_json}

timeline_context：
{timeline_context_json}

human_overrides（如为空则忽略；如有则优先遵守）：
{human_overrides_json}

待判断 cut 元数据：
{batch_json}
""".strip().format(
        schema_json=json.dumps(schema, ensure_ascii=False, indent=2),
        final_prompt=final_prompt,
        scene_taxonomy_seed_json=json.dumps(scene_taxonomy_seed, ensure_ascii=False, indent=2),
        timeline_context_json=json.dumps(timeline_context, ensure_ascii=False, indent=2),
        human_overrides_json=json.dumps(human_overrides, ensure_ascii=False, indent=2),
        batch_json=json.dumps(batch, ensure_ascii=False, indent=2),
    )


def validate_batch_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("items"), list):
        raise ValueError("LLM response missing items list")
    if "final_prompt_scene_hints" in payload and not isinstance(payload["final_prompt_scene_hints"], list):
        raise ValueError("final_prompt_scene_hints must be a list")
    if "final_prompt_scene_terms" in payload and not isinstance(payload["final_prompt_scene_terms"], list):
        raise ValueError("final_prompt_scene_terms must be a list")
    if "scene_taxonomy" in payload and not isinstance(payload["scene_taxonomy"], dict):
        raise ValueError("scene_taxonomy must be an object")


def workspace_path(workspace: Path, relative_or_abs: str) -> Path:
    path = Path(relative_or_abs)
    return path if path.is_absolute() else workspace / path


def load_cuts(meta_dir: Path, min_confidence: float, max_candidates: int) -> list[dict[str, Any]]:
    payload = read_json(meta_dir / "pyscenedetect_cuts.json")
    cuts = [item for item in (payload.get("cuts") or []) if float(item.get("confidence") or 0.0) >= min_confidence]
    cuts.sort(key=lambda item: float(item.get("time") or 0.0))
    if max_candidates <= 0 or max_candidates >= len(cuts):
        return cuts
    return cuts[:max_candidates]


def build_frame_lookup(workspace: Path, meta_dir: Path) -> dict[tuple[int, str], Path]:
    lookup: dict[tuple[int, str], Path] = {}
    path = meta_dir / "segment_keyframes.json"
    if not path.exists():
        return lookup
    payload = read_json(path)
    for item in payload.get("items") or []:
        if str(item.get("segment_source") or "") != "pyscenedetect_cut":
            continue
        cut_index = int(item.get("segment_index") or 0)
        for frame in item.get("keyframes") or []:
            role = str(frame.get("role") or "")
            if role in {"before_cut", "after_cut"} and frame.get("path"):
                lookup[(cut_index, role)] = workspace_path(workspace, str(frame["path"]))
    return lookup


def extract_frame(video_path: Path, workspace: Path, output_dir: Path, fps: float, time_value: float, label: str) -> Path:
    import cv2  # type: ignore

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open video: {video_path}")
    frame_index = max(0, int(round(time_value * fps)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"failed to extract frame at {time_value}s")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{label}_t{time_value:.3f}.jpg"
    cv2.imwrite(str(path), frame)
    return path


def make_contact_sheet(workspace: Path, batch: list[dict[str, Any]], output_path: Path, width: int) -> Path:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    cell_w = max(240, width // 4)
    cell_h = int(cell_w * 9 / 16)
    label_h = 42
    rows = []
    for item in batch:
        cells = []
        for role, key in [("PREV", "prev_context_frame"), ("BEFORE", "before_frame"), ("AFTER", "after_frame"), ("NEXT", "next_context_frame")]:
            img = cv2.imread(str(item[key]))
            if img is None:
                img = np.zeros((cell_h, cell_w, 3), dtype=np.uint8)
            img = cv2.resize(img, (cell_w, cell_h))
            label = np.full((label_h, cell_w, 3), 245, dtype=np.uint8)
            text = f"CUT {item['cut_index']} | {item['time']:.3f}s | {role}"
            cv2.putText(label, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (20, 20, 20), 2, cv2.LINE_AA)
            cells.append(np.vstack([label, img]))
        rows.append(np.hstack(cells))
    sheet = np.vstack(rows) if rows else np.zeros((100, width, 3), dtype=np.uint8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), sheet)
    return output_path


def prepare_candidates(workspace: Path, video_path: Path, meta_dir: Path, sheet_dir: Path, cuts: list[dict[str, Any]], batch_size: int, contact_sheet_width: int) -> list[tuple[list[dict[str, Any]], Path]]:
    video_payload = read_json(meta_dir / "pyscenedetect_cuts.json")
    fps = float(video_payload.get("fps") or 30.0)
    duration = float(video_payload.get("duration_seconds") or 0.0)
    frame_lookup = build_frame_lookup(workspace, meta_dir)
    frame_dir = workspace / "keyframes" / "scene_transition_frames"
    prepared: list[dict[str, Any]] = []
    cut_times = [float(item.get("time") or 0.0) for item in cuts]
    for position, cut in enumerate(cuts):
        index = int(cut.get("index") or 0)
        time_value = float(cut.get("time") or 0.0)
        previous_time = cut_times[position - 1] if position > 0 else None
        next_time = cut_times[position + 1] if position + 1 < len(cut_times) else None
        before = frame_lookup.get((index, "before_cut"))
        after = frame_lookup.get((index, "after_cut"))
        if before is None or not before.exists():
            before = extract_frame(video_path, workspace, frame_dir, fps, max(0.0, time_value - 0.25), f"cut_{index:04d}_before")
        if after is None or not after.exists():
            after = extract_frame(video_path, workspace, frame_dir, fps, min(max(0.0, duration - 0.001), time_value + 0.25), f"cut_{index:04d}_after")
        prev_context = extract_frame(video_path, workspace, frame_dir, fps, max(0.0, time_value - 1.5), f"cut_{index:04d}_prev_context")
        next_context = extract_frame(video_path, workspace, frame_dir, fps, min(max(0.0, duration - 0.001), time_value + 1.5), f"cut_{index:04d}_next_context")
        prepared.append({
            "cut_index": index,
            "time": round(time_value, 3),
            "frame": int(cut.get("frame") or round(time_value * fps)),
            "pyscenedetect_confidence": float(cut.get("confidence") or 0.0),
            "source_detectors": cut.get("source_detectors") or [],
            "reason": cut.get("reason") or "",
            "previous_cut_time": round(previous_time, 3) if previous_time is not None else None,
            "next_cut_time": round(next_time, 3) if next_time is not None else None,
            "seconds_since_previous_cut": round(time_value - previous_time, 3) if previous_time is not None else None,
            "seconds_until_next_cut": round(next_time - time_value, 3) if next_time is not None else None,
            "prev_context_frame": prev_context,
            "before_frame": before,
            "after_frame": after,
            "next_context_frame": next_context,
            "prev_context_frame_path": rel_path(prev_context, workspace),
            "before_frame_path": rel_path(before, workspace),
            "after_frame_path": rel_path(after, workspace),
            "next_context_frame_path": rel_path(next_context, workspace),
        })

    batches: list[tuple[list[dict[str, Any]], Path]] = []
    for start in range(0, len(prepared), batch_size):
        batch = prepared[start : start + batch_size]
        sheet_path = sheet_dir / f"batch_{len(batches) + 1:03d}.jpg"
        make_contact_sheet(workspace, batch, sheet_path, contact_sheet_width)
        batches.append((batch, sheet_path))
    return batches


def batch_cache_path(meta_dir: Path, batch_index: int) -> Path:
    return meta_dir / "scene_transition_batches" / f"batch_{batch_index:03d}.json"


def progress_paths(meta_dir: Path) -> tuple[Path, Path]:
    progress_dir = meta_dir / "scene_transition_batches"
    return progress_dir / "vlm_progress.json", progress_dir / "vlm_progress.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit_progress(meta_dir: Path, event: str, stats: dict[str, Any], batch_index: int, batch: list[dict[str, Any]] | None = None, config: OpenCodeConfig | None = None, message: str = "") -> None:
    total = int(stats.get("total") or 0)
    completed = int(stats.get("completed") or 0)
    cache_hits = int(stats.get("cache_hits") or 0)
    calls = int(stats.get("calls") or 0)
    failed = int(stats.get("failed") or 0)
    judged_cuts = int(stats.get("judged_cuts") or 0)
    transition_count = int(stats.get("transition_count") or 0)
    cut_indices = [int(item.get("cut_index") or 0) for item in (batch or []) if item.get("cut_index")]
    payload = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "mode": "vlm",
        "event": event,
        "timestamp": utc_now_iso(),
        "total": total,
        "current": batch_index,
        "completed": completed,
        "cache_hits": cache_hits,
        "calls": calls,
        "failed": failed,
        "judged_cuts": judged_cuts,
        "shooting_location_transitions": transition_count,
        "batch_index": batch_index,
        "batch_size": len(batch or []),
        "cut_indices": cut_indices,
        "message": message,
        "opencrew_task_id": config.task_id if config else None,
        "opencode_session_id": config.session_id if config else "",
        "model": config.model if config else {},
    }
    snapshot_path, jsonl_path = progress_paths(meta_dir)
    write_json(snapshot_path, payload)
    append_jsonl(jsonl_path, payload)
    print(
        f"[06 VLM] event={event} total={total} current={batch_index} completed={completed} "
        f"cache_hits={cache_hits} calls={calls} failed={failed} judged_cuts={judged_cuts} "
        f"transitions={transition_count} batch={batch_index:03d} {message}".rstrip(),
        flush=True,
    )


def load_completed_batch(meta_dir: Path, batch_index: int, force_rerun_batches: set[int]) -> dict[str, Any] | None:
    if batch_index in force_rerun_batches:
        return None
    path = batch_cache_path(meta_dir, batch_index)
    if not path.exists():
        return None
    payload = read_json(path)
    if isinstance(payload, dict) and payload.get("status") == "completed" and isinstance(payload.get("items"), list):
        return payload
    return None


def build_timeline_context(all_items: list[dict[str, Any]], space_segments: list[dict[str, Any]]) -> dict[str, Any]:
    last_item = all_items[-1] if all_items else {}
    current_scene_id = str(last_item.get("after_scene_id") or last_item.get("before_scene_id") or "").strip()
    current_scene_label = str(last_item.get("after_scene_label") or last_item.get("before_scene_label") or "").strip()
    return {
        "current_scene_id": current_scene_id,
        "current_scene_label": current_scene_label,
        "recent_scene_history": space_segments[-6:],
        "recent_items": [
            {
                "cut_index": item.get("cut_index") or item.get("source_cut_index"),
                "time": item.get("time"),
                "before_scene_id": item.get("before_scene_id"),
                "after_scene_id": item.get("after_scene_id"),
                "is_shooting_location_transition": item.get("is_shooting_location_transition"),
            }
            for item in all_items[-8:]
        ],
    }


def normalize_item_decision(item: dict[str, Any]) -> dict[str, Any]:
    before_scene_id = str(item.get("before_scene_id") or "").strip()
    after_scene_id = str(item.get("after_scene_id") or "").strip()
    same_scene = bool(item.get("same_scene_class")) or (before_scene_id and after_scene_id and before_scene_id == after_scene_id)
    blockers = [
        same_scene,
        bool(item.get("same_physical_space")),
        bool(item.get("same_space_different_angle")),
        bool(item.get("brief_cutaway_or_broll")),
        not bool(item.get("opens_sustained_scene_segment")),
    ]
    item["same_scene_class"] = same_scene
    item["is_shooting_location_transition"] = bool(item.get("is_shooting_location_transition")) and not any(blockers)
    if not item["is_shooting_location_transition"] and not item.get("not_transition_reason"):
        if same_scene:
            item["not_transition_reason"] = "same_scene_class"
        elif item.get("same_space_different_angle"):
            item["not_transition_reason"] = "same_space_different_angle"
        elif item.get("brief_cutaway_or_broll"):
            item["not_transition_reason"] = "brief_cutaway_or_broll"
        elif not item.get("opens_sustained_scene_segment"):
            item["not_transition_reason"] = "not_sustained_scene_segment"
    return item


def segment_scene_id(item: dict[str, Any], fallback: str = "unknown_or_other") -> str:
    return str(item.get("after_scene_id") or item.get("before_scene_id") or fallback).strip() or fallback


def segment_scene_label(item: dict[str, Any], fallback: str = "未知或其它") -> str:
    return str(item.get("after_scene_label") or item.get("before_scene_label") or item.get("after_location_label") or item.get("before_location_label") or fallback).strip() or fallback


def item_keyframes(item: dict[str, Any], roles: list[str]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    role_to_key = {
        "prev_context": "prev_context_frame",
        "before_cut": "before_frame",
        "after_cut": "after_frame",
        "next_context": "next_context_frame",
    }
    for role in roles:
        path = item.get(role_to_key.get(role, ""))
        if not path:
            continue
        frames.append({
            "role": role,
            "path": path,
            "cut_index": item.get("cut_index") or item.get("source_cut_index"),
            "time": item.get("time"),
            "before_scene_label": item.get("before_scene_label"),
            "after_scene_label": item.get("after_scene_label"),
            "contact_sheet": item.get("contact_sheet"),
        })
    return frames


def unique_keyframes(frames: list[dict[str, Any]], limit: int = 24) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for frame in frames:
        key = (str(frame.get("role") or ""), str(frame.get("path") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(frame)
        if len(result) >= limit:
            break
    return result


def build_space_segments(items: list[dict[str, Any]], duration_seconds: float) -> list[dict[str, Any]]:
    if not items:
        return []
    ordered = sorted(items, key=lambda item: float(item.get("time") or 0.0))
    segments: list[dict[str, Any]] = []
    current_id = str(ordered[0].get("before_scene_id") or segment_scene_id(ordered[0]))
    current_label = str(ordered[0].get("before_scene_label") or segment_scene_label(ordered[0]))
    start_time = 0.0
    entry_cut_index: int | None = None
    anchors: list[str] = []
    keyframes: list[dict[str, Any]] = []
    for item in ordered:
        item = normalize_item_decision(item)
        next_id = segment_scene_id(item, current_id)
        next_label = segment_scene_label(item, current_label)
        evidence = item.get("visual_evidence") or []
        if isinstance(evidence, list):
            anchors.extend(str(value) for value in evidence[:2] if value)
        if bool(item.get("is_shooting_location_transition")) and next_id != current_id:
            keyframes.extend(item_keyframes(item, ["prev_context", "before_cut"]))
            time_value = float(item.get("time") or start_time)
            segments.append({
                "segment_index": len(segments) + 1,
                "scene_id": current_id,
                "scene_label": current_label,
                "start_time": round(start_time, 3),
                "end_time": round(time_value, 3),
                "entry_cut_index": entry_cut_index,
                "exit_cut_index": item.get("cut_index") or item.get("source_cut_index"),
                "visual_anchors": list(dict.fromkeys(anchors))[:8],
                "keyframes": unique_keyframes(keyframes),
                "source": "llm_classification_state_machine",
            })
            current_id = next_id
            current_label = next_label
            start_time = time_value
            entry_cut_index = int(item.get("cut_index") or item.get("source_cut_index") or 0) or None
            anchors = []
            keyframes = item_keyframes(item, ["after_cut", "next_context"])
        else:
            keyframes.extend(item_keyframes(item, ["prev_context", "before_cut", "after_cut", "next_context"]))
    segments.append({
        "segment_index": len(segments) + 1,
        "scene_id": current_id,
        "scene_label": current_label,
        "start_time": round(start_time, 3),
        "end_time": round(duration_seconds, 3),
        "entry_cut_index": entry_cut_index,
        "exit_cut_index": None,
        "visual_anchors": list(dict.fromkeys(anchors))[:8],
        "keyframes": unique_keyframes(keyframes),
        "source": "llm_classification_state_machine",
    })
    return segments


def merge_taxonomies(seed: dict[str, Any], batch_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    labels_by_id: dict[str, dict[str, Any]] = {}
    for label in seed.get("labels") or []:
        if isinstance(label, dict) and label.get("scene_id"):
            labels_by_id[str(label["scene_id"])] = label
    source = str(seed.get("source") or "inferred_required")
    for payload in batch_payloads:
        taxonomy = payload.get("scene_taxonomy") or {}
        if not isinstance(taxonomy, dict):
            continue
        if taxonomy.get("source") and source == "inferred_required":
            source = str(taxonomy.get("source"))
        for label in taxonomy.get("labels") or []:
            if isinstance(label, dict) and label.get("scene_id") and str(label["scene_id"]) not in labels_by_id:
                labels_by_id[str(label["scene_id"])] = label
    return {"source": source, "labels": list(labels_by_id.values()), "fallback_labels": FALLBACK_SCENES}


def enrich_items_from_payload(batch: list[dict[str, Any]], sheet_path: Path, workspace: Path, payload: dict[str, Any], start_index: int) -> list[dict[str, Any]]:
    by_cut = {int(item.get("cut_index") or 0): item for item in batch}
    enriched: list[dict[str, Any]] = []
    for raw_item in payload.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        cut_index = int(item.get("cut_index") or 0)
        source = by_cut.get(cut_index, {})
        item["index"] = start_index + len(enriched)
        item["source_cut_index"] = cut_index
        item["frame"] = source.get("frame")
        item["prev_context_frame"] = source.get("prev_context_frame_path")
        item["before_frame"] = source.get("before_frame_path")
        item["after_frame"] = source.get("after_frame_path")
        item["next_context_frame"] = source.get("next_context_frame_path")
        item["contact_sheet"] = rel_path(sheet_path, workspace)
        item["pyscenedetect_confidence"] = source.get("pyscenedetect_confidence")
        item["source_detectors"] = source.get("source_detectors")
        enriched.append(normalize_item_decision(item))
    return enriched


def write_partial_outputs(meta_dir: Path, video_path: Path, all_items: list[dict[str, Any]], taxonomy: dict[str, Any], raw_batches: list[dict[str, Any]], config: OpenCodeConfig, duration_seconds: float) -> None:
    transition_items = [item for item in all_items if bool(item.get("is_shooting_location_transition"))]
    write_json(meta_dir / "shooting_location_transition_candidates.json", {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "transition_definition": TRANSITION_DEFINITION,
        "video_path": str(video_path),
        "scene_taxonomy": taxonomy,
        "items": all_items,
    })
    write_json(meta_dir / "scene_transition_space_segments.json", {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "video_path": str(video_path),
        "items": build_space_segments(all_items, duration_seconds),
    })
    write_json(meta_dir / "scene_transition_scene_taxonomy.json", taxonomy)
    write_json(meta_dir / "scene_transition_llm_analysis.json", {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "llm_called": True,
        "final_prompt_used": True,
        "transition_definition": TRANSITION_DEFINITION,
        "scene_taxonomy": taxonomy,
        "opencode_session_id": config.session_id,
        "opencrew_task_id": config.task_id,
        "opencrew_session_id": config.opencrew_session_id,
        "model": config.model,
        "summary": f"Judged {len(all_items)} visual cuts; {len(transition_items)} were marked as shooting location transitions.",
        "raw_batches": raw_batches,
    })


def run_judge(workspace: Path, task_id: int, args: argparse.Namespace) -> dict[str, Any]:
    database_url = os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
    config = fetch_opencode_config(database_url, task_id)
    ensure_model_supports_image(config)
    if bool(args.fresh_session) or session_has_stale_assistant(config):
        config = create_opencode_session(config, f"{TOOL_NAME} Task #{task_id}")
    meta_dir = workspace / "meta"
    cuts = load_cuts(meta_dir, float(args.min_cut_confidence), int(args.max_candidates))
    if not cuts:
        raise RuntimeError("No PySceneDetect cuts available for scene transition judgement")
    video_payload = read_json(meta_dir / "pyscenedetect_cuts.json")
    video_path = Path(video_payload.get("video_path") or args.video or "").expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"video file does not exist: {video_path}")
    duration_seconds = float(video_payload.get("duration_seconds") or 0.0)
    sheet_dir = workspace / "keyframes" / "scene_transition_contact_sheets"
    batches = prepare_candidates(workspace, video_path, meta_dir, sheet_dir, cuts, int(args.batch_size), int(args.contact_sheet_width))
    scene_taxonomy_seed = build_scene_taxonomy_seed(config.final_prompt)
    human_overrides = load_human_overrides(meta_dir)
    force_rerun_batches = parse_int_set(args.force_rerun_batches)

    all_items: list[dict[str, Any]] = []
    raw_batches: list[dict[str, Any]] = []
    completed_payloads: list[dict[str, Any]] = []
    space_segments: list[dict[str, Any]] = []
    progress_stats: dict[str, Any] = {"total": len(batches), "completed": 0, "cache_hits": 0, "calls": 0, "failed": 0, "judged_cuts": 0, "transition_count": 0}
    for batch_index, (batch, sheet_path) in enumerate(batches, start=1):
        progress_stats["judged_cuts"] = len(all_items)
        progress_stats["transition_count"] = sum(1 for item in all_items if bool(item.get("is_shooting_location_transition")))
        emit_progress(meta_dir, "batch_start", progress_stats, batch_index, batch, config)
        cached = load_completed_batch(meta_dir, batch_index, force_rerun_batches) if bool(args.resume) else None
        if cached is not None:
            items = cached.get("items") or []
            for item in items:
                if isinstance(item, dict):
                    item["index"] = len(all_items) + 1
                    all_items.append(normalize_item_decision(item))
            raw_batches.append({"batch_index": batch_index, "contact_sheet": str(sheet_path), "cache": "hit"})
            completed_payloads.append(cached.get("payload") or cached)
            space_segments = build_space_segments(all_items, duration_seconds)
            progress_stats["cache_hits"] += 1
            progress_stats["completed"] += 1
            progress_stats["judged_cuts"] = len(all_items)
            progress_stats["transition_count"] = sum(1 for item in all_items if bool(item.get("is_shooting_location_transition")))
            emit_progress(meta_dir, "cache_hit", progress_stats, batch_index, batch, config)
            continue

        public_batch = [
            {key: value for key, value in item.items() if key not in {"prev_context_frame", "before_frame", "after_frame", "next_context_frame"}}
            for item in batch
        ]
        timeline_context = build_timeline_context(all_items, space_segments)
        prompt = build_user_prompt(config.final_prompt, public_batch, scene_taxonomy_seed, timeline_context, human_overrides)
        cache_path = batch_cache_path(meta_dir, batch_index)
        try:
            emit_progress(meta_dir, "calling", progress_stats, batch_index, batch, config)
            raw_text = call_opencode(config, workspace, prompt, [sheet_path], build_system_prompt(), int(args.timeout_seconds))
        except Exception as exc:
            progress_stats["failed"] += 1
            emit_progress(meta_dir, "failed", progress_stats, batch_index, batch, config, str(exc))
            write_json(cache_path, {
                "batch_index": batch_index,
                "status": "failed",
                "error": str(exc),
                "contact_sheet": rel_path(sheet_path, workspace),
                "timeline_context_before": timeline_context,
            })
            if bool(args.stop_on_batch_error):
                raise
            break
        try:
            payload = extract_json_object(raw_text)
            validate_batch_payload(payload)
        except Exception as exc:
            payload = repair_json(config, workspace, raw_text, str(exc), int(args.timeout_seconds))
        items = enrich_items_from_payload(batch, sheet_path, workspace, payload, len(all_items) + 1)
        all_items.extend(items)
        completed_payloads.append(payload)
        space_segments = build_space_segments(all_items, duration_seconds)
        timeline_context_after = build_timeline_context(all_items, space_segments)
        write_json(cache_path, {
            "batch_index": batch_index,
            "status": "completed",
            "tool_version": TOOL_VERSION,
            "contact_sheet": rel_path(sheet_path, workspace),
            "timeline_context_before": timeline_context,
            "timeline_context_after": timeline_context_after,
            "payload": payload,
            "items": items,
            "raw_response": raw_text[:8000],
        })
        raw_batches.append({"batch_index": batch_index, "contact_sheet": str(sheet_path), "raw_response": raw_text[:4000]})
        taxonomy = merge_taxonomies(scene_taxonomy_seed, completed_payloads)
        write_partial_outputs(meta_dir, video_path, all_items, taxonomy, raw_batches, config, duration_seconds)
        progress_stats["calls"] += 1
        progress_stats["completed"] += 1
        progress_stats["judged_cuts"] = len(all_items)
        progress_stats["transition_count"] = sum(1 for item in all_items if bool(item.get("is_shooting_location_transition")))
        emit_progress(meta_dir, "completed", progress_stats, batch_index, batch, config)

    taxonomy = merge_taxonomies(scene_taxonomy_seed, completed_payloads)
    write_partial_outputs(meta_dir, video_path, all_items, taxonomy, raw_batches, config, duration_seconds)
    transition_items = [item for item in all_items if bool(item.get("is_shooting_location_transition"))]
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed" if len(all_items) == len(cuts) else "partial",
        "transition_definition": TRANSITION_DEFINITION,
        "workspace": str(workspace),
        "opencrew_task_id": config.task_id,
        "opencrew_session_id": config.opencrew_session_id,
        "opencode_session_id": config.session_id,
        "model": config.model,
        "outputs": {
            "shooting_location_transition_candidates": str(meta_dir / "shooting_location_transition_candidates.json"),
            "scene_transition_llm_analysis": str(meta_dir / "scene_transition_llm_analysis.json"),
            "scene_transition_scene_taxonomy": str(meta_dir / "scene_transition_scene_taxonomy.json"),
            "scene_transition_space_segments": str(meta_dir / "scene_transition_space_segments.json"),
            "scene_transition_batches": str(meta_dir / "scene_transition_batches"),
            "progress_snapshot": str(progress_paths(meta_dir)[0]),
            "progress_log": str(progress_paths(meta_dir)[1]),
        },
        "counts": {
            "judged_cuts": len(all_items),
            "shooting_location_transitions": len(transition_items),
            "contact_sheets": len(batches),
            "prompt_scene_terms": len(scene_taxonomy_seed.get("labels") or []),
            "batch_calls": int(progress_stats.get("calls") or 0),
            "batch_cache_hits": int(progress_stats.get("cache_hits") or 0),
            "batches_completed": int(progress_stats.get("completed") or 0),
            "batches_failed": int(progress_stats.get("failed") or 0),
        },
        "scene_taxonomy_source": taxonomy.get("source"),
        "prompt_scene_terms": scene_taxonomy_seed.get("labels") or [],
    }
    write_json(meta_dir / "06_scene_transition_llm_judge_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Use an OpenCode VLM to judge whether PySceneDetect cuts are shooting-location transitions.")
    parser.add_argument("--workspace", required=True, help="Target workspace containing 04/05 outputs. Outputs are written here.")
    parser.add_argument("--task-id", type=int, required=True, help="OpenClip task id used for final_prompt, OpenCode session, and run model.")
    parser.add_argument("--video", help="Optional video path fallback if pyscenedetect_cuts.json has no video_path.")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--max-candidates", type=int, default=0, help="Maximum cuts to judge. 0 means all cuts; non-zero is only for quick/debug runs.")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--min-cut-confidence", type=float, default=0.7)
    parser.add_argument("--contact-sheet-width", type=int, default=1280)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--resume", action="store_true", help="Reuse completed meta/scene_transition_batches/batch_XXX.json files.")
    parser.add_argument("--force-rerun-batches", default="", help="Comma/range list of batch indexes to rerun, e.g. 7,8,9 or 7-9.")
    parser.add_argument("--stop-on-batch-error", action="store_true", help="Raise on first failed batch instead of writing partial outputs.")
    parser.add_argument("--fresh-session", action="store_true", help="Create a fresh OpenCode session for this run while keeping the Task final_prompt and run model.")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace}")
    result = run_judge(workspace, int(args.task_id), args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result['outputs']['shooting_location_transition_candidates']}")


if __name__ == "__main__":
    main()
