from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.media_library_analysis.visual_semantic_contracts import (  # noqa: E402
    CANDIDATE_SCHEMA_VERSION,
    INPUT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    SAMPLING_STRATEGY,
    VisualSemanticValidationError,
    publish_visual_semantic_contract,
    validate_visual_semantic_candidate,
    validate_visual_semantic_item,
    validate_with_single_repair,
)


TOOL_NAME = "03_03_KeyframeVisualSemantic"
TOOL_VERSION = "0.2.0"

# This module intentionally has no executable model client. The OpenCrew backend
# adapter owns authorization, model sessions, retries, and Tool Session finalize;
# this registry module exposes only the frozen schemas and pure validation/publish
# contract used at that boundary.


def _main() -> int:
    print(
        "03_03 is contract-only and must run through the OpenCrew visual "
        "semantic ToolAdapter.",
        file=sys.stderr,
    )
    return 2


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "INPUT_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "SAMPLING_STRATEGY",
    "TOOL_NAME",
    "TOOL_VERSION",
    "VisualSemanticValidationError",
    "publish_visual_semantic_contract",
    "validate_visual_semantic_candidate",
    "validate_visual_semantic_item",
    "validate_with_single_repair",
]


if __name__ == "__main__":
    raise SystemExit(_main())
