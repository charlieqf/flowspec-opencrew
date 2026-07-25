from __future__ import annotations

from functools import lru_cache
from typing import Any


class SimplifiedChineseError(RuntimeError):
    pass


TRADITIONAL_MARKERS = frozenset(
    "義烏貨錢東換個單發國賣覺這嗎實註冊閉選這套無步法們經機遇囤資靠醫體說為臺後對產業營銷療顧戶門級來標準"
    "簡繁號錄機構優勢準備創業資源環境項目問題過程方案際畫面復盤視頻頻講試轉變總開關場與麼裏麗"
)


@lru_cache(maxsize=1)
def _opencc_t2s() -> Any:
    try:
        from opencc import OpenCC  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on runtime packaging
        raise SimplifiedChineseError(
            "Simplified Chinese normalization requires opencc-python-reimplemented. "
            "Install backend/Analysis_V1 runtime requirements before running ASR or SRT rewrite."
        ) from exc
    return OpenCC("t2s")


def text_value(value: Any) -> str:
    return str(value or "")


def has_traditional_marker(value: Any) -> bool:
    return bool(set(text_value(value)) & TRADITIONAL_MARKERS)


def to_simplified_chinese(value: Any) -> str:
    text = text_value(value)
    if not text:
        return text
    try:
        return str(_opencc_t2s().convert(text))
    except SimplifiedChineseError:
        if has_traditional_marker(text):
            raise
        return text


def contains_traditional_chinese(value: Any) -> bool:
    text = text_value(value)
    if not text:
        return False
    try:
        return _opencc_t2s().convert(text) != text
    except SimplifiedChineseError:
        return has_traditional_marker(text)
