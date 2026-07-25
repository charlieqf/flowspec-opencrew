from __future__ import annotations

from functools import lru_cache
import re
from typing import Any


@lru_cache(maxsize=1)
def _opencc_t2s() -> Any:
    try:
        from opencc import OpenCC  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime dependency packaging
        raise RuntimeError("Simplified Chinese normalization requires opencc-python-reimplemented.") from exc
    return OpenCC("t2s")


def to_simplified_chinese(value: Any) -> str:
    text = str(value if value is not None else "")
    return str(_opencc_t2s().convert(text)) if text else text


def simplify_storyboard_text(value: Any, fallback: str = "") -> str:
    return to_simplified_chinese(value if value is not None else fallback).strip()


def redact_secret_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"([?&]key=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", text, flags=re.I)
    return text


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value
