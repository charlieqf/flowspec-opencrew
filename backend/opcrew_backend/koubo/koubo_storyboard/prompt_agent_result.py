from __future__ import annotations

import json
import re
from typing import Any


PROMPT_AGENT_RESULT_TAG = "PROMPT_AGENT_RESULT"


def _text(value: Any, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


def prompt_agent_normalize_issue(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    severity = _text(item.get("severity"), "medium").lower()
    if severity not in {"high", "medium", "low"}:
        severity = "medium"
    problem = _text(item.get("problem") or item.get("message") or item.get("title"))[:1000]
    suggestion = _text(item.get("suggestion") or item.get("recommendation"))[:2000]
    if not problem and not suggestion:
        return None
    return {
        "severity": severity,
        "span": _text(item.get("span") or item.get("source_text"))[:1000],
        "problem": problem,
        "why_it_matters": _text(item.get("why_it_matters") or item.get("why"))[:2000],
        "suggestion": suggestion,
    }


def prompt_agent_normalize_used_source(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    doc_id = _text(item.get("doc_id"))
    if not doc_id:
        return None
    return {
        "doc_id": doc_id[:240],
        "title": _text(item.get("title"))[:500],
        "trust_level": _text(item.get("trust_level"))[:80],
        "reason": _text(item.get("reason"))[:1000],
    }


def prompt_agent_normalize_result(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    mode = _text(payload.get("mode"), "optimize").lower()
    if mode not in {"critique", "optimize", "rewrite", "adapt"}:
        mode = "optimize"
    issues = [item for item in (prompt_agent_normalize_issue(issue) for issue in payload.get("issues") or []) if item]
    used_sources = [item for item in (prompt_agent_normalize_used_source(source) for source in payload.get("used_sources") or []) if item]
    result = {
        "mode": mode,
        "summary": _text(payload.get("summary"))[:2000],
        "issues": issues[:40],
        "revised_prompt": _text(payload.get("revised_prompt") or payload.get("prompt"))[:20000],
        "negative_prompt": _text(payload.get("negative_prompt"))[:8000],
        "changes": [str(item).strip()[:1000] for item in payload.get("changes") or [] if str(item).strip()][:40],
        "model_notes": [str(item).strip()[:1000] for item in payload.get("model_notes") or payload.get("notes") or [] if str(item).strip()][:40],
        "used_sources": used_sources[:20],
    }
    if not result["summary"] and not result["issues"] and not result["revised_prompt"] and not result["negative_prompt"] and not result["model_notes"]:
        return None
    return result


def extract_prompt_agent_results(message_text: str) -> list[dict[str, Any]]:
    pattern = re.compile(rf"<{PROMPT_AGENT_RESULT_TAG}>([\s\S]*?)</{PROMPT_AGENT_RESULT_TAG}>")
    results: list[dict[str, Any]] = []
    for match in pattern.finditer(message_text or ""):
        try:
            parsed = json.loads(match.group(1).strip())
        except Exception:
            continue
        normalized = prompt_agent_normalize_result(parsed)
        if normalized:
            results.append(normalized)
    return results


def prompt_agent_result_doc_ids(result: dict[str, Any] | None) -> list[str]:
    if not isinstance(result, dict):
        return []
    return [
        _text(source.get("doc_id"))
        for source in result.get("used_sources") or []
        if isinstance(source, dict) and _text(source.get("doc_id"))
    ]


def prompt_agent_validate_result_sources(result: dict[str, Any], allowed_doc_ids: set[str] | list[str] | tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    allowed = {str(doc_id).strip() for doc_id in allowed_doc_ids if str(doc_id).strip()}
    kept = []
    dropped = []
    for source in result.get("used_sources") or []:
        if source.get("doc_id") not in allowed:
            dropped.append(source)
            continue
        kept.append(source)
    normalized = {**result, "used_sources": kept}
    return normalized, {"kept": kept, "dropped": dropped}
