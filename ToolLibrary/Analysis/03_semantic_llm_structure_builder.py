from __future__ import annotations

import argparse
import json
import os
import re
import time
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOOL_NAME = "SemanticLLMStructureBuilder"
TOOL_VERSION = "0.1.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"


@dataclass(frozen=True)
class OpenCodeConfig:
    base_url: str
    username: str
    password: str
    directory: str
    session_id: str
    model: dict[str, str]
    task_id: int | None
    opencrew_session_id: int | None
    final_prompt: str


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def log_progress(raw_dir: Path, payload: dict[str, Any]) -> None:
    event = {"time_ms": int(time.time() * 1000), **payload}
    append_jsonl(raw_dir / "llm_progress.jsonl", event)
    write_json(raw_dir / "llm_progress.json", event)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_semantic_text(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text or "")
    cleaned = re.sub(r"[\W_]+", "", cleaned)
    return cleaned.strip().lower()


def text_similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    left_norm = normalize_semantic_text(left)
    right_norm = normalize_semantic_text(right)
    if not left_norm or not right_norm:
        return 0.0
    return float(SequenceMatcher(None, left_norm, right_norm).ratio())


OCR_WATERMARK_PATTERNS = [
    r"CMAT[-—_\s]*\d+",
    r"\b(?:Lee|Leey|Leley|Liley|Pieey|Llly|Lilley|Lelly|Leeey|Laeey|Lieey|Lleey|Seey|Seee|Pley|Reey|oley|eeley|eeey|MID|MED)\b",
]

ASR_SUSPICIOUS_SHORT_TEXTS = {"红包", "上一局", "件为负", "在"}


def clean_ocr_text_for_arbitration(text: str) -> tuple[str, list[str]]:
    cleaned = str(text or "")
    removed: list[str] = []
    for pattern in OCR_WATERMARK_PATTERNS:
        for match in re.findall(pattern, cleaned, flags=re.I):
            if match and match not in removed:
                removed.append(str(match))
        cleaned = re.sub(pattern, " ", cleaned, flags=re.I)
    replacements = {
        "主任区师": "主任医师",
        "电任医师": "主任医师",
        "自天": "白天",
        "二匹院": "第二医院",
        "文通大学": "交通大学",
        "中心型肥胖": "中心性肥胖",
        "巧G包": "",
        "【1]": "[1]",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。；;、")
    return cleaned, removed


def clean_asr_text_for_arbitration(text: str) -> str:
    cleaned = re.sub(r"\s+", "", str(text or ""))
    return cleaned.strip()


def has_meaningful_chinese_sentence(text: str) -> bool:
    cleaned = clean_ocr_text_for_arbitration(text)[0]
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", cleaned)
    return len(chinese_chars) >= 4


def contains_evidence_text(text: str) -> bool:
    cleaned = clean_ocr_text_for_arbitration(text)[0]
    return bool(re.search(r"BMI|kg/m|腰围|肥胖|诊断|指南|参考文献|医院|主任医师|专家|打鼾|嗜睡|健康|强健", cleaned, flags=re.I))


def asr_overlap_count(asr_items: list[dict[str, Any]]) -> int:
    count = 0
    previous_end = -1.0
    for item in sorted(asr_items, key=lambda value: float(value.get("start") or 0.0)):
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or start)
        if previous_end >= 0 and start < previous_end - 0.05:
            count += 1
        previous_end = max(previous_end, end)
    return count


def base_asr_reliability(asr_quality: dict[str, Any] | None, asr_items: list[dict[str, Any]]) -> float:
    quality = str((asr_quality or {}).get("quality_level") or "unknown")
    score = {"good": 0.82, "usable": 0.58, "weak": 0.35, "failed": 0.1}.get(quality, 0.5)
    if bool((asr_quality or {}).get("timestamp_coverage_suspect")):
        score -= 0.18
    if int((asr_quality or {}).get("asr_gap_with_audio_activity_count") or 0) > 0:
        score -= 0.12
    if asr_overlap_count(asr_items) > 0:
        score -= 0.08
    return max(0.0, min(1.0, score))


def score_asr_reliability(asr_text: str, overlaps: list[dict[str, Any]], asr_quality: dict[str, Any] | None, similarity: float, ocr_stability: float) -> tuple[float, list[str]]:
    score = base_asr_reliability(asr_quality, overlaps)
    flags: list[str] = []
    text = clean_asr_text_for_arbitration(asr_text)
    if bool((asr_quality or {}).get("timestamp_coverage_suspect")):
        flags.append("timestamp_suspect")
    if any(str(item.get("text") or "").strip("。？！,. ") in ASR_SUSPICIOUS_SHORT_TEXTS for item in overlaps):
        score -= 0.2
        flags.append("suspicious_short_asr")
    if len(text) <= 2 and text:
        score -= 0.15
        flags.append("very_short_asr")
    if len(text) > 45 and ocr_stability >= 0.65 and similarity < 0.35:
        score -= 0.15
        flags.append("long_asr_low_match_to_stable_ocr")
    if asr_overlap_count(overlaps) > 0:
        flags.append("overlapping_asr_segments")
    return max(0.0, min(1.0, score)), flags


def ocr_stability_score(ocr_item: dict[str, Any]) -> float:
    candidates = [item for item in (ocr_item.get("text_candidates") or []) if isinstance(item, dict)]
    times = ocr_item.get("source_keyframe_times") or []
    if not candidates and not times:
        return 0.25
    cleaned_values = [clean_ocr_text_for_arbitration(str(item.get("text") or ""))[0] for item in candidates]
    meaningful_values = [value for value in cleaned_values if normalize_semantic_text(value)]
    repeated = len(set(meaningful_values)) < len(meaningful_values) if meaningful_values else False
    score = min(1.0, 0.25 + 0.08 * min(len(times), 6) + (0.2 if repeated else 0.0))
    return score


def score_ocr_reliability(ocr_item: dict[str, Any], cleaned_text: str) -> tuple[float, list[str]]:
    flags: list[str] = []
    raw_confidence = float(ocr_item.get("confidence") or 0.0)
    stability = ocr_stability_score(ocr_item)
    score = 0.35 * raw_confidence + 0.45 * stability
    if has_meaningful_chinese_sentence(cleaned_text):
        score += 0.15
        flags.append("meaningful_chinese_text")
    if contains_evidence_text(cleaned_text):
        score += 0.12
        flags.append("evidence_text")
    if not normalize_semantic_text(cleaned_text):
        score -= 0.5
        flags.append("ocr_noise_only")
    if len(normalize_semantic_text(cleaned_text)) < 4 and not contains_evidence_text(cleaned_text):
        score -= 0.25
        flags.append("too_short_after_cleaning")
    return max(0.0, min(1.0, score)), flags


def choose_evidence_policy(asr_text: str, ocr_text_clean: str, overlaps: list[dict[str, Any]], similarity: float, asr_score: float, ocr_score: float, ocr_flags: list[str]) -> tuple[str, str, str, str]:
    if not normalize_semantic_text(ocr_text_clean):
        return "ocr_noise", "ignored_ocr_noise", "asr", asr_text
    if not overlaps or not asr_text.strip():
        return "ocr_only", "ocr_fill_gap", "ocr", ocr_text_clean
    if similarity >= 0.72:
        return "subtitle_duplicate", "suppress_as_duplicate", "asr", asr_text
    if ocr_score >= 0.68 and (asr_score <= 0.55 or similarity < 0.25):
        return "ocr_corrects_asr", "ocr_primary", "ocr", ocr_text_clean
    if ocr_score >= 0.65 and asr_score >= 0.5 and similarity < 0.45:
        preferred = f"{asr_text} / 画面文字：{ocr_text_clean}"
        return "mixed", "mixed_reconcile", "mixed", preferred
    if asr_score >= 0.65 and ocr_score < 0.55:
        return "asr_more_reliable", "asr_primary", "asr", asr_text
    if "evidence_text" in ocr_flags and ocr_score >= 0.6:
        preferred = f"{asr_text} / 画面文字：{ocr_text_clean}"
        return "mixed", "mixed_reconcile", "mixed", preferred
    return "visual_context", "ocr_context", "mixed", f"{asr_text} / 画面文字：{ocr_text_clean}"


def ranges_overlap(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    left_end = max(left_start, left_end)
    right_end = max(right_start, right_end)
    if left_start == left_end:
        return right_start <= left_start <= right_end
    if right_start == right_end:
        return left_start <= right_start <= left_end
    return max(left_start, right_start) <= min(left_end, right_end)


def overlapping_asr_segments(ocr_item: dict[str, Any], asr_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = float(ocr_item.get("start") or 0.0)
    end = float(ocr_item.get("end") or start)
    overlaps: list[dict[str, Any]] = []
    for segment in asr_segments:
        try:
            seg_start = float(segment.get("start") or 0.0)
            seg_end = float(segment.get("end") or seg_start)
        except Exception:
            continue
        if ranges_overlap(start, end, seg_start, seg_end):
            overlaps.append(segment)
    return overlaps


def build_calibrated_semantic_evidence_timeline(subtitle_alignment: dict[str, Any] | None, visual_text_timeline: dict[str, Any] | None) -> list[dict[str, Any]]:
    alignment_items = (subtitle_alignment or {}).get("items") or []
    visual_items = [item for item in ((visual_text_timeline or {}).get("items") or []) if isinstance(item, dict)]
    evidence: list[dict[str, Any]] = []
    for item in alignment_items if isinstance(alignment_items, list) else []:
        if not isinstance(item, dict):
            continue
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or start)
        visual_context = list(item.get("visual_text_context") or [])
        for visual in visual_items:
            visual_start = float(visual.get("start") or visual.get("time") or 0.0)
            visual_end = float(visual.get("end") or visual_start)
            if ranges_overlap(start, end, visual_start, visual_end):
                text = str(visual.get("text") or visual.get("ocr_text") or "").strip()
                if text and text not in visual_context:
                    visual_context.append(text)
        policy = str(item.get("alignment_policy") or item.get("evidence_policy") or "asr_primary")
        preferred_source = str(item.get("preferred_source") or "asr")
        preferred_text = str(item.get("preferred_text") or item.get("text") or "").strip()
        evidence.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "asr_text": str(item.get("asr_text") or ""),
            "subtitle_ocr_text": str(item.get("ocr_text") or ""),
            "visual_context_text": " / ".join([str(value) for value in visual_context if str(value).strip()]),
            "preferred_text": preferred_text,
            "preferred_source": preferred_source,
            "evidence_policy": policy,
            "use_policy": policy,
            "ocr_relation_to_asr": policy,
            "asr_reliability": round(float(item.get("asr_reliability") or 0.0), 4),
            "subtitle_ocr_reliability": round(float(item.get("subtitle_ocr_reliability") or 0.0), 4),
            "ocr_reliability": round(float(item.get("subtitle_ocr_reliability") or 0.0), 4),
            "ocr_asr_similarity": round(float(item.get("ocr_asr_similarity") or 0.0), 4),
            "scene_index": item.get("scene_index"),
            "source_asr_segment_ids": item.get("source_asr_segment_ids") or [],
            "source_ocr_item_ids": item.get("source_ocr_item_ids") or [],
            "source_keyframe_times": item.get("source_keyframe_times") or [],
            "time_alignment_hint": {"start": round(start, 3), "end": round(end, 3), "policy": policy, "preferred_source": preferred_source},
            "needs_review": bool(item.get("needs_review")),
            "decision_reason": f"calibrated_by_05_2; policy={policy}; preferred={preferred_source}",
        })
    return evidence


def build_semantic_evidence_timeline(asr_segments: dict[str, Any], visual_ocr_timeline: dict[str, Any] | None, asr_quality: dict[str, Any] | None = None, subtitle_alignment: dict[str, Any] | None = None, visual_text_timeline: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    calibrated = build_calibrated_semantic_evidence_timeline(subtitle_alignment, visual_text_timeline)
    if calibrated:
        return calibrated
    ocr_items = (visual_ocr_timeline or {}).get("items") or []
    asr_items = [item for item in (asr_segments.get("segments") or []) if isinstance(item, dict)]
    evidence: list[dict[str, Any]] = []
    for ocr_item in ocr_items if isinstance(ocr_items, list) else []:
        if not isinstance(ocr_item, dict):
            continue
        ocr_text = str(ocr_item.get("text") or "").strip()
        if not ocr_text:
            continue
        ocr_text_clean, removed_tokens = clean_ocr_text_for_arbitration(ocr_text)
        overlaps = overlapping_asr_segments(ocr_item, asr_items)
        asr_text = "".join(str(item.get("text") or "") for item in overlaps).strip()
        asr_text_clean = clean_asr_text_for_arbitration(asr_text)
        similarity = text_similarity(ocr_text_clean, asr_text_clean) if asr_text_clean else 0.0
        stability = ocr_stability_score(ocr_item)
        asr_reliability, asr_flags = score_asr_reliability(asr_text, overlaps, asr_quality, similarity, stability)
        ocr_reliability, ocr_flags = score_ocr_reliability(ocr_item, ocr_text_clean)
        relation, use_policy, preferred_source, preferred_text = choose_evidence_policy(asr_text, ocr_text_clean, overlaps, similarity, asr_reliability, ocr_reliability, ocr_flags)
        decision_reason = f"asr={asr_reliability:.2f}, ocr={ocr_reliability:.2f}, similarity={similarity:.2f}; policy={use_policy}"
        evidence.append({
            "start": round(float(ocr_item.get("start") or 0.0), 3),
            "end": round(float(ocr_item.get("end") or float(ocr_item.get("start") or 0.0)), 3),
            "ocr_text": ocr_text,
            "ocr_text_raw": ocr_text,
            "ocr_text_clean": ocr_text_clean,
            "overlapping_asr_text": asr_text,
            "asr_text_clean": asr_text_clean,
            "overlapping_asr_segment_ids": [item.get("index") for item in overlaps if item.get("index") is not None],
            "ocr_relation_to_asr": relation,
            "use_policy": use_policy,
            "preferred_source": preferred_source,
            "preferred_text": preferred_text,
            "asr_reliability": round(asr_reliability, 4),
            "ocr_reliability": round(ocr_reliability, 4),
            "ocr_asr_similarity": round(similarity, 4),
            "ocr_noise_tokens_removed": removed_tokens,
            "asr_warning_flags": asr_flags,
            "ocr_warning_flags": ocr_flags,
            "decision_reason": decision_reason,
            "source_keyframe_times": ocr_item.get("source_keyframe_times") or [],
            "representative_time": ocr_item.get("representative_time"),
        })
    return evidence


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


def fetch_task_opencode_config(database_url: str, task_id: int | None, fallback_workspace: Path) -> OpenCodeConfig:
    conn = postgres_connect(database_url)
    try:
        base_url = str(get_setting(conn, "opencode.base_url") or "").strip().rstrip("/")
        username = str(get_setting(conn, "opencode.username") or "").strip()
        password = str(get_setting(conn, "opencode.password") or "").strip()
        if not base_url or not username or not password:
            raise RuntimeError("OpenCode connection is incomplete in app_settings")

        if task_id is None:
            return create_fallback_opencode_session(base_url, username, password, fallback_workspace)

        with conn.cursor() as cursor:
            cursor.execute(
                """
SELECT t.id, t.final_prompt, t.run_model_provider, t.run_model_id, t.prompt_model_provider, t.prompt_model_id,
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
            return create_fallback_opencode_session(base_url, username, password, fallback_workspace)
        data = dict(zip(columns, row))
        final_prompt = decode_text(data.get("final_prompt")).strip()
        if not final_prompt:
            raise RuntimeError(f"Task #{task_id} has no final_prompt")
        session_id = decode_text(data.get("opencode_session_id")).strip()
        if not session_id:
            return create_fallback_opencode_session(base_url, username, password, fallback_workspace, final_prompt=final_prompt, task_id=task_id)
        run_provider = decode_text(data.get("run_model_provider")).strip()
        run_model = decode_text(data.get("run_model_id")).strip()
        prompt_provider = decode_text(data.get("prompt_model_provider")).strip()
        prompt_model = decode_text(data.get("prompt_model_id")).strip()
        provider = run_provider or prompt_provider
        model_id = run_model or prompt_model
        model = {"providerID": provider, "modelID": model_id} if provider and model_id else {}
        return OpenCodeConfig(
            base_url=base_url,
            username=username,
            password=password,
            directory=decode_text(data.get("workspace_dir")).strip() or str(fallback_workspace),
            session_id=session_id,
            model=model,
            task_id=task_id,
            opencrew_session_id=int(data.get("opencrew_session_id") or 0) or None,
            final_prompt=final_prompt,
        )
    finally:
        conn.close()


def request_json(config: OpenCodeConfig, method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, str] | None = None, timeout: int = 120) -> Any:
    query_string = f"?{urlencode(query)}" if query else ""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    token = b64encode(f"{config.username}:{config.password}".encode("utf-8")).decode("ascii")
    req = Request(
        f"{config.base_url}{path}{query_string}",
        data=data,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    return json.loads(raw) if raw else None


def create_fallback_opencode_session(base_url: str, username: str, password: str, workspace: Path, final_prompt: str = "", task_id: int | None = None) -> OpenCodeConfig:
    temp_config = OpenCodeConfig(base_url, username, password, str(workspace), "", {}, task_id, None, final_prompt)
    session = request_json(temp_config, "POST", "/session", {"title": f"{TOOL_NAME} {int(time.time())}"}, query={"directory": str(workspace)}, timeout=30)
    session_id = str((session or {}).get("id") or "").strip()
    if not session_id:
        raise RuntimeError("Failed to create OpenCode session")
    return OpenCodeConfig(base_url, username, password, str(workspace), session_id, {}, task_id, None, final_prompt)


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


def call_opencode_llm(config: OpenCodeConfig, prompt: str, system_prompt: str, timeout_seconds: int, raw_dir: Path | None = None, call_name: str = "semantic_structure") -> str:
    started_at = int(time.time() * 1000)
    payload: dict[str, Any] = {"parts": [{"type": "text", "text": prompt}]}
    if config.model:
        payload["model"] = config.model
    if system_prompt:
        payload["system"] = system_prompt
    if raw_dir:
        write_json(raw_dir / f"{call_name}_request.json", {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "call_name": call_name,
            "started_at_ms": started_at,
            "opencode_session_id": config.session_id,
            "opencrew_task_id": config.task_id,
            "opencrew_session_id": config.opencrew_session_id,
            "model": config.model,
            "directory": config.directory,
            "system_prompt": system_prompt,
            "user_prompt": prompt,
            "opencode_payload": payload,
        })
        log_progress(raw_dir, {"event": "request_started", "call_name": call_name, "time_ms": started_at})
    request_json(config, "POST", f"/session/{config.session_id}/prompt_async", payload, query={"directory": config.directory}, timeout=30)
    if raw_dir:
        log_progress(raw_dir, {"event": "request_submitted", "call_name": call_name})
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        messages = request_json(config, "GET", f"/session/{config.session_id}/message", None, query={"directory": config.directory, "limit": "160"}, timeout=30) or []
        assistant_text = last_completed_assistant(messages, started_at)
        if assistant_text:
            if raw_dir:
                completed_at = int(time.time() * 1000)
                write_text(raw_dir / f"{call_name}_response.txt", assistant_text)
                write_json(raw_dir / f"{call_name}_response_meta.json", {
                    "tool": TOOL_NAME,
                    "tool_version": TOOL_VERSION,
                    "call_name": call_name,
                    "started_at_ms": started_at,
                    "completed_at_ms": completed_at,
                    "duration_ms": completed_at - started_at,
                    "response_chars": len(assistant_text),
                })
                log_progress(raw_dir, {"event": "response_received", "call_name": call_name, "time_ms": completed_at, "response_chars": len(assistant_text)})
            return assistant_text
        time.sleep(2)
    if raw_dir:
        log_progress(raw_dir, {"event": "timeout", "call_name": call_name, "timeout_seconds": timeout_seconds})
    raise RuntimeError("OpenCode timed out before returning semantic structure JSON")


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1).strip()
    else:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON root must be an object")
    return payload


def build_system_prompt() -> str:
    return """
你是 OpenClip 的语义结构拆解引擎。你必须调用语义理解能力完成拆解，不要按固定时长或句号机械切分。
你只能返回一个合法 JSON 对象，不要输出 Markdown、解释文字或代码块。
所有时间戳必须来自输入 ASR 时间轴范围；可以在 ASR segment 内按语义位置合理插值，但不能超出视频时长。
输出必须包含 semantic_units、semantic_boundary_candidates、semantic_llm_analysis、semantic_segment_candidates 四个顶层字段。
""".strip()


def build_user_prompt(final_prompt: str, video_metadata: dict[str, Any], asr_segments: dict[str, Any], normalized_segments: dict[str, Any], asr_quality: dict[str, Any], semantic_evidence_timeline: list[dict[str, Any]] | None = None, asr_sentence_timeline: dict[str, Any] | None = None) -> str:
    compact_input = {
        "final_prompt": final_prompt,
        "video_metadata": video_metadata,
        "asr_quality": asr_quality,
        "asr_segments": asr_segments.get("segments") or [],
        "asr_sentence_timeline": (asr_sentence_timeline or {}).get("items") or [],
        "normalized_asr_segments": normalized_segments.get("items") or [],
        "full_transcript": asr_segments.get("text") or "",
        "semantic_evidence_timeline": semantic_evidence_timeline or [],
    }
    schema = {
        "semantic_units": [
            {
                "id": "unit_001",
                "start": 0.0,
                "end": 1.0,
                "text": "语义单元原文",
                "source_type": "asr|ocr|mixed",
                "source_segment_ids": [1],
                "source_sentence_ids": ["asr_sentence_001"],
                "source_ocr_times": [1.0],
                "ocr_relation_to_asr": "subtitle_duplicate|visual_context|ocr_only|mixed|none",
                "visual_text": "OCR画面文字；没有则空字符串",
                "dialogue_text": "ASR口播文字；没有则空字符串",
                "evidence_policy": "asr_primary|ocr_context|ocr_fill_gap|ocr_primary|mixed_reconcile|mixed",
                "semantic_function": "表达功能",
                "formula_slot": "来自Final Prompt的结构槽位或空字符串",
                "reason": "为什么这样构建这个语义单元",
                "confidence": 0.0,
            }
        ],
        "semantic_boundary_candidates": [
            {
                "id": "sem_boundary_001",
                "time": 1.0,
                "source": "llm_semantic",
                "type": "topic_shift|question_to_answer|problem_to_solution|setup_to_turning_point|explanation_to_summary|emotion_shift|speaker_change|pause_boundary|density_change|prompt_anchor|ocr_title_card|visual_text_change|asr_to_ocr_transition|ocr_to_asr_transition",
                "confidence": 0.0,
                "reason": "边界理由",
                "before_unit_id": "unit_001",
                "after_unit_id": "unit_002",
            }
        ],
        "semantic_llm_analysis": {
            "llm_called": True,
            "final_prompt_used": True,
            "overall_formula_summary": "结合Final Prompt总结当前视频结构",
            "unit_annotations": [],
            "boundary_strategy": "边界判断原则",
            "risks": [],
        },
        "semantic_segment_candidates": [
            {
                "index": 1,
                "start": 0.0,
                "end": 1.0,
                "title": "片段标题",
                "semantic_role": "语义角色",
                "summary": "片段摘要",
                "dialogue_text": "覆盖的对白原文",
                "boundary_reason": "起止边界理由",
                "confidence": 0.0,
                "source_unit_ids": ["unit_001"],
                "source_sentence_ids": ["asr_sentence_001", "asr_sentence_002"],
                "start_sentence_id": "asr_sentence_001",
                "end_sentence_id": "asr_sentence_002",
                "formula_slot": "来自Final Prompt的结构槽位或空字符串",
                "merge_reason": "为什么这些短句合并成一个完整 Detail 片段",
                "covered_sentence_texts": ["短句1", "短句2"],
            }
        ],
    }
    return """
    请基于 Final Prompt、ASR 句级时间轴和可选 OCR/ASR 融合证据时间轴，完成语义单元构建、语义边界识别、LLM语义分析和 Detail Scheme 候选分段。asr_sentence_timeline 的 start/end 来自 ASR provider word 时间戳，是口播时间的主依据；asr_segments 只是全文上下文，不能作为边界主依据。semantic_evidence_timeline 若来自 05_2，已经完成 ASR/OCR 字幕双向校准，应优先使用其中 preferred_text、preferred_source、evidence_policy 和 visual_context_text。

硬性要求：
1. 必须结合 Final Prompt 判断业务结构和公式锚点。
2. 不要按固定时长切分，不要按句号机械切分，也不要机械地“一句一个 Detail”。
3. asr_sentence_timeline 是最小可切分时间粒度：禁止拆开其中任何一条短句；所有 semantic_units 和 semantic_segment_candidates 的 start/end 必须落在被覆盖短句的 start/end 上。
4. semantic_units 是最小但完整的自然语义单元；必须由 1 条或多条连续 asr_sentence_timeline 短句和/或 semantic_evidence_timeline 条目组成，长 ASR 段必须拆成多个语义单元。
5. semantic_units 必须填写 source_sentence_ids；如果某个 unit 使用 OCR 补充但无对应 ASR 句子，也要解释原因。

Detail Scheme 拆分硬规则：
6. semantic_segment_candidates 就是 Detail Scheme 的候选片段，必须以 asr_sentence_timeline 短句为最细粒度进行合并，不能拆散短句。
7. 每个 Detail 片段必须表达一句相对完整的话、一个完整动作、一个完整卖点、一个完整论证步骤或一个自然过渡；不能只是一个残缺从句，不能把多个不相关动作塞进一个片段。
8. 通常每个 Detail 合并 1-2 条连续短句；只有当 3 条短句共同构成一个不可拆开的完整表达时，才允许合并 3 条；超过 3 条必须给出非常强的 merge_reason。
9. Final Prompt 的 formula_slot 是槽位标签，不是粗分段边界。每个槽位可以有一个或多个 Detail 片段；禁止把同一槽位下所有内容粗暴合成一个长片段。
10. 禁止跨 formula_slot 合并，除非这条短句本身是两个槽位之间不可分割的过渡，并且 merge_reason 说明原因。
11. semantic_segment_candidates 必须填写 source_sentence_ids、start_sentence_id、end_sentence_id、covered_sentence_texts 和 merge_reason。
12. semantic_segment_candidates.dialogue_text 必须等于覆盖短句文本按顺序拼接后的口播文本，可用 05_2 的 preferred_text 做错字/噪声修正，但不能改变句子覆盖范围。
13. semantic_segment_candidates.formula_slot 必须来自 Final Prompt 槽位；如果无法匹配，填空字符串并在 boundary_reason 或 merge_reason 中说明。
14. 语义边界优先来自：Final Prompt 槽位切换、卖点/论证步骤切换、动作变化、转折词、结论/召唤变化、明显停顿、OCR/画面语义变化。
15. Scene/keyframe/visual boundary 只用于后续 08 BoundaryAligner 微调边界；你在 03 中应给出语义边界，不要因为画面切换而切断一条完整短句。
OCR/ASR 使用规则：
16. ASR 与 OCR 都是候选证据。通常 ASR 是口播主证据、OCR 是画面文字证据；但当 semantic_evidence_timeline 判定 use_policy/evidence_policy 为 ocr_primary、ocr_corrects_asr、ocr_fills_asr_gap 或 mixed_reconcile 时，必须优先采用 preferred_text，不得机械沿用 ASR。
17. 当 evidence_policy 为 asr_primary 或 asr_corrects_ocr 时，说明 ASR 质量较高或已反向校准 OCR 字幕，口播文本以 ASR/preferred_text 为准，OCR 字幕只作为画面对应证据。
18. 如果 use_policy=suppress_as_duplicate，表示 OCR 与同时间 ASR 高度重复，通常是字幕型 OCR：不要为它单独生成 OCR 语义单元，semantic_units.text 和 dialogue_text 以 ASR 为准；可在 reason 中说明 OCR 仅作视觉佐证。
19. 如果 use_policy=ocr_fill_gap，表示 OCR 位于 ASR 空白或弱覆盖时间段：必须用 OCR 补齐画面承载但口播未覆盖的信息，可生成 source_type=ocr 的语义单元，visual_text 填 OCR，dialogue_text 为空。
20. 如果 use_policy=ocr_primary，表示同时间 OCR 比 ASR 更可靠：semantic_units.text 必须使用 preferred_text，dialogue_text 可填 preferred_text，source_type 使用 ocr 或 mixed，evidence_policy 使用 ocr_primary，并在 reason 说明 OCR 为何纠正 ASR。
21. 如果 use_policy=mixed_reconcile，表示 ASR 与 OCR 需要融合校正：semantic_units.text 使用 preferred_text 或融合后的语义文本，dialogue_text 保留 ASR 原文，visual_text 保留 OCR，source_type=mixed，evidence_policy=mixed_reconcile。
22. 如果 use_policy=ocr_context，表示同时间 ASR 与 OCR 不重复而是互补：必须把 OCR 标题、章节名、图表、研究名称、页面文字与 ASR 口播一起理解，生成 source_type=mixed 或在相邻 ASR 单元中填 visual_text，evidence_policy 使用 ocr_context 或 mixed。
23. OCR 原文中的品牌残片、水印、CMAT 编号不应进入主要语义文本；优先参考 ocr_text_clean 和 preferred_text。
24. semantic_units 允许来自 asr、ocr 或 mixed，并用 source_type 标注来源；OCR 来源需填写 source_ocr_times、ocr_relation_to_asr、visual_text、evidence_policy。
25. semantic_boundary_candidates 必须来自语义推进、角色关系、冲突转折、Final Prompt锚点、明显停顿或视觉文字变化。
26. semantic_segment_candidates 必须由连续 semantic_units 和连续 source_sentence_ids 组成，不能重叠，尽量覆盖所有有效对白与重要 OCR 画面文字；边界优先落在 asr_sentence_timeline 的句子起止点或 calibrated subtitle item 起止点。
27. 只返回合法 JSON，字段名必须与目标 schema 一致。

目标 JSON schema 示例：
{schema_json}

输入数据：
{input_json}
""".strip().format(
        schema_json=json.dumps(schema, ensure_ascii=False, indent=2),
        input_json=json.dumps(compact_input, ensure_ascii=False, indent=2),
    )


def validate_payload(payload: dict[str, Any]) -> None:
    required = ["semantic_units", "semantic_boundary_candidates", "semantic_llm_analysis", "semantic_segment_candidates"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"LLM JSON missing required fields: {', '.join(missing)}")
    if not isinstance(payload["semantic_units"], list):
        raise ValueError("semantic_units must be a list")
    if not isinstance(payload["semantic_boundary_candidates"], list):
        raise ValueError("semantic_boundary_candidates must be a list")
    if not isinstance(payload["semantic_llm_analysis"], dict):
        raise ValueError("semantic_llm_analysis must be an object")
    if not isinstance(payload["semantic_segment_candidates"], list):
        raise ValueError("semantic_segment_candidates must be a list")


def repair_json_with_llm(config: OpenCodeConfig, bad_text: str, error: str, timeout_seconds: int, raw_dir: Path | None = None) -> dict[str, Any]:
    prompt = f"""
上一次返回不是可解析的目标 JSON。请修复为一个合法 JSON 对象，只返回 JSON，不要解释。

解析错误：{error}

原始输出：
{bad_text}
""".strip()
    repaired = call_opencode_llm(config, prompt, build_system_prompt(), timeout_seconds, raw_dir=raw_dir, call_name="semantic_structure_repair")
    payload = extract_json_object(repaired)
    validate_payload(payload)
    return payload


def run_builder(workspace: Path, config: OpenCodeConfig, timeout_seconds: int) -> dict[str, Any]:
    meta_dir = workspace / "meta"
    raw_dir = meta_dir / "semantic_llm" / "raw"
    input_paths = {
        "video_metadata": meta_dir / "video_metadata.json",
        "asr_segments": meta_dir / "asr_segments.json",
        "asr_normalized_segments": meta_dir / "asr_normalized_segments.json",
        "asr_sentence_timeline": meta_dir / "asr_sentence_timeline.json",
        "asr_quality": meta_dir / "asr_quality.json",
        "visual_ocr_timeline": meta_dir / "visual_ocr_timeline.json",
        "subtitle_alignment_timeline": meta_dir / "subtitle_alignment_timeline.json",
        "visual_subtitle_timeline_calibrated": meta_dir / "visual_subtitle_timeline_calibrated.json",
        "visual_text_timeline": meta_dir / "visual_text_timeline.json",
    }
    video_metadata = read_json(input_paths["video_metadata"])
    asr_segments = read_json(input_paths["asr_segments"])
    normalized_segments = read_json(input_paths["asr_normalized_segments"])
    asr_sentence_timeline = read_json(input_paths["asr_sentence_timeline"]) if input_paths["asr_sentence_timeline"].exists() else {"items": []}
    asr_quality = read_json(input_paths["asr_quality"])
    visual_ocr_timeline = read_json(input_paths["visual_ocr_timeline"]) if input_paths["visual_ocr_timeline"].exists() else None
    subtitle_alignment = read_json(input_paths["subtitle_alignment_timeline"]) if input_paths["subtitle_alignment_timeline"].exists() else None
    visual_text_timeline = read_json(input_paths["visual_text_timeline"]) if input_paths["visual_text_timeline"].exists() else None
    semantic_evidence_timeline = build_semantic_evidence_timeline(asr_segments, visual_ocr_timeline, asr_quality, subtitle_alignment=subtitle_alignment, visual_text_timeline=visual_text_timeline)
    write_json(meta_dir / "semantic_evidence_timeline.json", {"items": semantic_evidence_timeline})
    prompt = build_user_prompt(config.final_prompt, video_metadata, asr_segments, normalized_segments, asr_quality, semantic_evidence_timeline, asr_sentence_timeline)
    system_prompt = build_system_prompt()
    write_json(raw_dir / "input_manifest.json", {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workspace": str(workspace),
        "input_files": {key: str(path) for key, path in input_paths.items()},
        "final_prompt_source": "openclip_tasks.final_prompt" if config.task_id is not None else "fallback_config.final_prompt",
        "final_prompt_chars": len(config.final_prompt),
        "video_duration_seconds": video_metadata.get("duration_seconds"),
        "asr_segment_count": len(asr_segments.get("segments") or []),
        "normalized_asr_segment_count": len(normalized_segments.get("items") or []),
        "asr_sentence_timeline_count": len(asr_sentence_timeline.get("items") or []),
        "visual_ocr_timeline_present": visual_ocr_timeline is not None,
        "subtitle_alignment_timeline_present": subtitle_alignment is not None,
        "visual_ocr_timeline_count": len((visual_ocr_timeline or {}).get("items") or []),
        "subtitle_alignment_timeline_count": len((subtitle_alignment or {}).get("items") or []),
        "semantic_evidence_timeline_count": len(semantic_evidence_timeline),
        "semantic_evidence_policy_counts": {policy: len([item for item in semantic_evidence_timeline if item.get("use_policy") == policy]) for policy in sorted({str(item.get("use_policy") or "") for item in semantic_evidence_timeline}) if policy},
        "opencode_session_id": config.session_id,
        "opencrew_task_id": config.task_id,
        "opencrew_session_id": config.opencrew_session_id,
        "model": config.model,
    })
    raw_text = call_opencode_llm(config, prompt, system_prompt, timeout_seconds, raw_dir=raw_dir, call_name="semantic_structure")
    try:
        payload = extract_json_object(raw_text)
        validate_payload(payload)
    except Exception as exc:
        log_progress(raw_dir, {"event": "response_parse_failed", "error": str(exc)})
        payload = repair_json_with_llm(config, raw_text, str(exc), timeout_seconds, raw_dir=raw_dir)

    analysis = dict(payload["semantic_llm_analysis"])
    analysis.update({
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "llm_called": True,
        "opencode_session_id": config.session_id,
        "opencrew_task_id": config.task_id,
        "opencrew_session_id": config.opencrew_session_id,
        "model": config.model,
    })
    payload["semantic_llm_analysis"] = analysis

    write_json(meta_dir / "semantic_units.json", {"items": payload["semantic_units"]})
    write_json(meta_dir / "semantic_boundary_candidates.json", {"items": payload["semantic_boundary_candidates"]})
    write_json(meta_dir / "semantic_llm_analysis.json", payload["semantic_llm_analysis"])
    write_json(meta_dir / "semantic_segment_candidates.json", {"items": payload["semantic_segment_candidates"]})

    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace": str(workspace),
        "opencode_session_id": config.session_id,
        "opencrew_task_id": config.task_id,
        "opencrew_session_id": config.opencrew_session_id,
        "model": config.model,
        "outputs": {
            "semantic_units": str(meta_dir / "semantic_units.json"),
            "semantic_boundary_candidates": str(meta_dir / "semantic_boundary_candidates.json"),
            "semantic_llm_analysis": str(meta_dir / "semantic_llm_analysis.json"),
            "semantic_segment_candidates": str(meta_dir / "semantic_segment_candidates.json"),
            "semantic_evidence_timeline": str(meta_dir / "semantic_evidence_timeline.json"),
            "raw_logs": str(raw_dir),
            "llm_progress_current": str(raw_dir / "llm_progress.json"),
            "llm_progress": str(raw_dir / "llm_progress.jsonl"),
        },
        "counts": {
            "semantic_units": len(payload["semantic_units"]),
            "semantic_boundary_candidates": len(payload["semantic_boundary_candidates"]),
            "semantic_segment_candidates": len(payload["semantic_segment_candidates"]),
            "semantic_evidence_timeline": len(semantic_evidence_timeline),
        },
    }
    write_json(meta_dir / "03_semantic_llm_structure_builder_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Use OpenCode LLM to build semantic units, boundaries, analysis, and initial semantic segments.")
    parser.add_argument("--workspace", required=True, help="Target Task workspace where outputs are written, e.g. OpenCrew/tmp_sessions/Task#Demo1.")
    parser.add_argument("--task-id", type=int, help="OpenClip task id used only for final_prompt, OpenCode session, and selected model.")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV, help="Environment variable containing OpenCrew PostgreSQL URL.")
    parser.add_argument("--timeout-seconds", type=int, default=600, help="Timeout for each OpenCode LLM call.")
    parser.add_argument("--print-json", action="store_true", help="Print result JSON to stdout.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace}")
    database_url = os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
    config = fetch_task_opencode_config(database_url, args.task_id, workspace)
    result = run_builder(workspace, config, args.timeout_seconds)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result['outputs']['semantic_segment_candidates']}")


if __name__ == "__main__":
    main()
