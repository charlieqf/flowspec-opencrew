#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_API_MODULE = Path("frontend/src/lib/api.ts")
LEGACY_API_HELPER_FILES = {
    Path("frontend/src/modules/koubo/api.js"),
    Path("frontend/src/modules/koubo/AnalysisV1/analysisV1Api.js"),
    Path("frontend/src/modules/koubo/DanceMimicV1/danceMimicV1Api.js"),
    Path("frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js"),
    Path("frontend/src/modules/koubo/KouboTaskList/kouboTaskListApi.js"),
    Path("frontend/src/modules/koubo/OCRebuildModule.jsx"),
    Path("frontend/src/modules/koubo/OCStoryBoard/storyboardApi.js"),
    Path("frontend/src/modules/koubo/TalkingHeadV1/talkingHeadV1Api.js"),
}
ALLOWED_FILES = {CANONICAL_API_MODULE, *LEGACY_API_HELPER_FILES}
HELPER_PATTERNS = (
    re.compile(r"^\s*const\s+API_BASE\s*=", re.MULTILINE),
)
FUNCTION_DECLARATION_PATTERN = re.compile(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)
CONST_DECLARATION_PATTERN = re.compile(r"^\s*const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", re.MULTILINE)
HELPER_NAME_MARKERS = ("fetch", "request", "api")
DOM_REQUEST_NAMES = {"requestfullscreen", "requestpictureinpicture"}


def iter_frontend_source_files() -> list[Path]:
    source_root = REPO_ROOT / "frontend" / "src"
    files: list[Path] = []
    for suffix in ("*.js", "*.jsx", "*.ts", "*.tsx"):
        files.extend(source_root.rglob(suffix))
    return sorted(files)


def looks_like_helper_name(name: str) -> bool:
    lower = name.lower()
    if lower in DOM_REQUEST_NAMES or lower.startswith("requestfull"):
        return False
    return any(marker in lower for marker in HELPER_NAME_MARKERS)


def has_fetch_wrapper_declaration(text: str) -> bool:
    for pattern in (FUNCTION_DECLARATION_PATTERN, CONST_DECLARATION_PATTERN):
        for match in pattern.finditer(text):
            name = match.group(1)
            if not looks_like_helper_name(name):
                continue
            snippet = text[match.start() : match.start() + 2000]
            if "fetch(" in snippet:
                return True
    return False


def main() -> int:
    violations: list[str] = []
    for path in iter_frontend_source_files():
        rel = path.relative_to(REPO_ROOT)
        if rel in ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in HELPER_PATTERNS) or has_fetch_wrapper_declaration(text):
            violations.append(str(rel))
    if violations:
        print("New frontend API request helper definitions are frozen.")
        print(f"Use {CANONICAL_API_MODULE} instead, or explicitly migrate an existing legacy helper before changing the allowlist.")
        for rel in violations:
            print(f"- {rel}")
        return 1
    print("frontend API helper freeze check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
