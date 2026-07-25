#!/usr/bin/env python3
"""Generate formal JSON Schema files from the REAL Pydantic contract models.

Source of truth: backend/opcrew_backend/tool_sessions/schemas/models.py
Run with the backend venv so pydantic is importable, from the repo root:

    backend/.venv/bin/python docs/WorkflowSpec/schema/_generate.py

This keeps docs/WorkflowSpec/schema/*.schema.json in lockstep with the code.
These schemas are `[implemented]` — they are the current executable contract.
Proposed extensions (wait_reason, artifact name/role, business fields, …) are
NOT in these files by design; see docs/WorkflowSpec/07_ImplementationBinding.md 3.B.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MODELS = REPO_ROOT / "backend/opcrew_backend/tool_sessions/schemas/models.py"
OUT = pathlib.Path(__file__).resolve().parent

EXPORT = [
    "Variables",
    "OutputManifest",
    "InputManifest",
    "State",
    "ToolResult",
    "SessionContextPatch",
    "DependencyCheckResult",
    "PromptManifest",
    "ModelCallAudit",
]


def main() -> int:
    spec = importlib.util.spec_from_file_location("ocmodels", MODELS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ocmodels"] = module  # let pydantic resolve forward refs
    spec.loader.exec_module(module)

    for name in EXPORT:
        cls = getattr(module, name)
        cls.model_rebuild(_types_namespace=vars(module))
        schema = cls.model_json_schema()
        (OUT / f"{name}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {name}.schema.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
