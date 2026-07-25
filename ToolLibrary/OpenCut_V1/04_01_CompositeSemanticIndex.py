from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.media_library_analysis.composite_contracts import (  # noqa: E402
    CANDIDATE_SCHEMA_VERSION,
    INPUT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    CompositeValidationError,
    publish_composite_contract,
    validate_composite_candidate,
)


TOOL_NAME = "04_01_CompositeSemanticIndex"
TOOL_VERSION = "0.1.0"


def _main() -> int:
    print(
        "04_01 is contract-only and must run through the OpenCrew "
        "CompositeAnalysisToolAdapter.",
        file=sys.stderr,
    )
    return 2


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "INPUT_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "CompositeValidationError",
    "TOOL_NAME",
    "TOOL_VERSION",
    "publish_composite_contract",
    "validate_composite_candidate",
]


if __name__ == "__main__":
    raise SystemExit(_main())
