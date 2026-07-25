from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from typing import Any


NORMALIZATION_VERSION = "nfkc_casefold_ws_v1"
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: Any) -> str:
    """Apply the sole v1 search normalization contract.

    This intentionally does not tokenize Chinese text. Query and publisher code
    must use this same function so literal and substring matching stay stable.
    """

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalized_unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def normalized_search_text(*parts: Any) -> str:
    flattened: list[Any] = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            flattened.extend(part)
        else:
            flattened.append(part)
    return " ".join(normalized_unique(flattened))


def query_hash(value: Any) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()
