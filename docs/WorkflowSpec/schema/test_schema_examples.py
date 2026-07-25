"""Validate implemented contracts and the proposed Process documentation.

These schemas are auto-generated from the real Pydantic models by _generate.py.
This test makes the FlowSpec claim "07 = current executable contract" checkable:
the *.valid.json fixtures MUST validate; the *.invalid-*.json fixtures MUST fail
(they exercise extra=forbid — e.g. business fields cannot be stuffed into Variables).

    backend/.venv/bin/python -m pytest docs/WorkflowSpec/schema/test_schema_examples.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys

import jsonschema
import pytest

HERE = pathlib.Path(__file__).resolve().parent
EXAMPLES = HERE / "examples"
REPO_ROOT = HERE.parents[2]
MODELS = REPO_ROOT / "backend/opcrew_backend/tool_sessions/schemas/models.py"
DOC_EXAMPLES = HERE.parent / "examples"
PROCESS_SCHEMA = HERE / "proposed" / "Process.schema.json"
PROCESS_LINTER = HERE / "proposed" / "lint_process.py"
ERROR_SCHEMA = HERE / "proposed" / "Error.schema.json"
CHECKPOINT_SCHEMA = HERE / "proposed" / "Checkpoint.schema.json"
AI_EXECUTION_PROFILE_SCHEMA = HERE / "proposed" / "AIExecutionProfile.schema.json"
AI_USAGE_RECORD_SCHEMA = HERE / "proposed" / "AIUsageRecord.schema.json"
BUDGET_LEDGER_SCHEMA = HERE / "proposed" / "BudgetLedger.schema.json"


def _strip_jsonc(text: str) -> str:
    """Remove // line-comments outside of double-quoted strings."""
    out, in_str, esc, i = [], False, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _first_jsonc_block(md_path: pathlib.Path) -> dict:
    m = re.search(r"```jsonc\s*\n(.*?)\n```", md_path.read_text(encoding="utf-8"), re.S)
    assert m, f"no ```jsonc block found in {md_path.name}"
    return json.loads(_strip_jsonc(m.group(1)))

# Same list _generate.py exports.
EXPORTED = [
    "Variables", "OutputManifest", "InputManifest", "State",
    "ToolResult", "SessionContextPatch", "DependencyCheckResult",
    "PromptManifest", "ModelCallAudit",
]


def _load_models():
    spec = importlib.util.spec_from_file_location("ocmodels", MODELS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ocmodels"] = module  # let pydantic resolve forward refs
    spec.loader.exec_module(module)
    return module


def _schema(name: str) -> dict:
    return json.loads((HERE / f"{name}.schema.json").read_text(encoding="utf-8"))


VALID = [
    ("Variables", "Variables.valid.json"),
    ("OutputManifest", "OutputManifest.valid.json"),
]
INVALID = [
    ("Variables", "Variables.invalid-extra-field.json"),
]


@pytest.mark.parametrize("schema_name, fixture", VALID)
def test_valid_fixtures_validate(schema_name: str, fixture: str) -> None:
    instance = json.loads((EXAMPLES / fixture).read_text(encoding="utf-8"))
    jsonschema.validate(instance, _schema(schema_name))  # raises on failure


@pytest.mark.parametrize("schema_name, fixture", INVALID)
def test_invalid_fixtures_rejected(schema_name: str, fixture: str) -> None:
    instance = json.loads((EXAMPLES / fixture).read_text(encoding="utf-8"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance, _schema(schema_name))


def _process_schema() -> dict:
    return json.loads(PROCESS_SCHEMA.read_text(encoding="utf-8"))


def _proposed_schema(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_process_linter():
    spec = importlib.util.spec_from_file_location("flowspec_process_linter", PROCESS_LINTER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["flowspec_process_linter"] = module
    spec.loader.exec_module(module)
    return module


# The four REAL business scenario documents must validate against the
# proposed Process schema — extracted straight from the .md, not a re-written copy.
REAL_EXAMPLES = [
    "loan-approval.md",
    "bank-data-report.md",
    "due-diligence.md",
    "opencrew-video-creation.md",
]


@pytest.mark.parametrize("md_name", REAL_EXAMPLES)
def test_real_example_doc_validates(md_name: str) -> None:
    inst = _first_jsonc_block(DOC_EXAMPLES / md_name)
    jsonschema.validate(inst, _process_schema())
    assert _load_process_linter().lint_process(inst) == []


def test_process_definition_registry_example_validates() -> None:
    inst = _first_jsonc_block(HERE.parent / "02_ProcessDefinition.md")
    jsonschema.validate(inst, _process_schema())
    assert _load_process_linter().lint_process(inst) == []


def test_proposed_process_schema_accepts_valid_fixture() -> None:
    inst = json.loads((HERE / "proposed" / "examples" / "loan-process.valid.json").read_text(encoding="utf-8"))
    jsonschema.validate(inst, _process_schema())  # depends_on plain + any_of OR-join


@pytest.mark.parametrize(
    "schema_path,fixture",
    [
        (ERROR_SCHEMA, "error.valid.json"),
        (CHECKPOINT_SCHEMA, "checkpoint.valid.json"),
        (AI_EXECUTION_PROFILE_SCHEMA, "ai-agent-profile.valid.json"),
        (AI_USAGE_RECORD_SCHEMA, "ai-usage-record.valid.json"),
    ],
)
def test_proposed_support_contracts_accept_valid_fixtures(
    schema_path: pathlib.Path, fixture: str
) -> None:
    inst = json.loads((HERE / "proposed" / "examples" / fixture).read_text(encoding="utf-8"))
    jsonschema.validate(inst, _proposed_schema(schema_path))


@pytest.mark.parametrize(
    "schema_path,inst,why",
    [
        (
            ERROR_SCHEMA,
            {
                "schema_version": "1.0",
                "error_code": "boom",
                "category": "internal",
                "phase": "run",
                "retryable": False,
                "resume_supported": False,
                "user_action_required": True,
                "safe_message": "执行失败",
                "suggested_action": "联系管理员",
                "debug_ref": None,
                "internal_path": "/secret/workspace",
            },
            "structured errors reject undeclared internal details",
        ),
        (
            CHECKPOINT_SCHEMA,
            {
                "schema_version": "1.0",
                "checkpoint_id": "cp1",
                "run_id": "run1",
                "step_id": "S1",
                "tool_id": "t",
                "tool_contract_version": "1.0",
                "input_snapshot_hash": "sha256:" + "a" * 64,
                "boundary": "batch 1",
                "resume_token": "1",
                "state_ref": {"path": "/absolute/checkpoint.json", "sha256": "sha256:" + "b" * 64, "size": 1},
                "created_at": "2026-07-24T10:00:00Z",
                "writer_id": "w1",
            },
            "checkpoint state paths must be workspace-relative",
        ),
        (
            AI_EXECUTION_PROFILE_SCHEMA,
            {
                "schema_version": "1.0",
                "kind": "model",
                "model": {
                    "provider": "example",
                    "model_id": "m1",
                    "fallback_policy": "automatic"
                },
                "budget": {
                    "max_wall_seconds": 30,
                    "max_input_tokens": 1000,
                    "max_output_tokens": 500
                },
                "data_policy": {
                    "sensitivity": "normal",
                    "external_transfer": "allowed",
                    "redaction_required": True
                },
                "output": {"validation": "strict", "max_repair_attempts": 0},
                "streaming": {"enabled": True, "partial_output_authoritative": False}
            },
            "silent or automatic model fallback is forbidden",
        ),
        (
            AI_EXECUTION_PROFILE_SCHEMA,
            {
                "schema_version": "1.0",
                "kind": "model",
                "model": {
                    "provider": "example",
                    "model_id": "m1",
                    "fallback_policy": "deny"
                },
                "budget": {
                    "max_wall_seconds": 30,
                    "max_input_tokens": 1000,
                    "max_output_tokens": 500
                },
                "data_policy": {
                    "sensitivity": "normal",
                    "external_transfer": "allowed",
                    "redaction_required": True
                },
                "output": {"validation": "strict", "max_repair_attempts": 0},
                "streaming": {"enabled": True, "partial_output_authoritative": True}
            },
            "streaming deltas are never authoritative outputs",
        ),
        (
            AI_USAGE_RECORD_SCHEMA,
            {
                "schema_version": "1.0",
                "usage_record_id": "u1",
                "operation_id": "op1",
                "idempotency_key": "key1",
                "model_invocation_id": "mi1",
                "run_id": "r1",
                "step_id": "s1",
                "step_attempt_no": 1,
                "provider": "p",
                "model_id": "m",
                "modality": "text",
                "usage": {"measurement_status": "provider_reported", "total_tokens": 1},
                "cost": {
                    "status": "final",
                    "amount": 0.1,
                    "currency": "USD",
                    "source": "invoice_reconciled"
                },
                "recorded_at": "2026-07-24T10:00:00Z"
            },
            "money amounts must be decimal strings rather than floats",
        ),
        (
            AI_USAGE_RECORD_SCHEMA,
            {
                **json.loads((HERE / "proposed" / "examples" / "ai-usage-record.valid.json").read_text(encoding="utf-8")),
                "usage": {"measurement_status": "provider_reported"},
            },
            "an available measurement status must carry at least one measured unit",
        ),
        (
            BUDGET_LEDGER_SCHEMA,
            {
                "schema_version": "1.1",
                "ledger_id": "b1",
                "run_id": "r1",
                "currency": "USD",
                "limit": "10",
                "status": "open",
                "transactions": [{
                    "transaction_id": "t1",
                    "kind": "adjust",
                    "operation_id": "op1",
                    "amount": "1",
                    "at": "2026-07-24T10:00:00Z",
                }],
            },
            "budget adjustments require an explicit debit or credit direction",
        ),
    ],
)
def test_proposed_support_contracts_reject_invalid_documents(
    schema_path: pathlib.Path, inst: dict, why: str
) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(inst, _proposed_schema(schema_path))


# P3/P8 invariants the schema must now REJECT.
STRICT_INVALID = [
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t"}]},
     "step missing side_effect_class"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "pure",
                 "resources": [{"kind": "mutex"}]}]},
     "mutex missing name/mode"),
    ({"process_id": "x", "version": "1",
      "resource_pools": {"gpu": {"type": "semaphore"}},
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "pure"}]},
     "semaphore pool missing limit"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "non_idempotent",
                 "retry": {"policy": "on_transient", "max_attempts": 2}}]},
     "non_idempotent + auto-retry"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "idempotent",
                 "retry": {"policy": "on_transient"}}]},
     "unbounded transient retry"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "pure",
                 "depends_on": [{"step_id": "S0", "statuses": ["completed"], "any_of": []}]}]},
     "dependency step_id + any_of together"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "pure",
                 "when": {"variable": "decision", "equals": "approved", "exists": True}}]},
     "when must use exactly one operator"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "pure",
                 "when": {"variable": "score", "expr": "score > 10"}}]},
     "arbitrary guard expression"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "pure",
                 "conditions_in": {"expr": "credit_ready && kyc_ready"}}]},
     "arbitrary named-condition expression"),
    ({"process_id": "x", "version": "1", "failure_propagation": "allow_partial",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "pure"}]},
     "failure propagation is intentionally minimal"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "pure",
                 "on_error": [{"when": "transient", "do": "rerun"}]}]},
     "on_error cannot create a second retry entry point"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "type": "agent",
                 "side_effect_class": "idempotent"}]},
     "agent steps require an AI execution profile"),
]


@pytest.mark.parametrize("inst, why", STRICT_INVALID, ids=[c[1] for c in STRICT_INVALID])
def test_process_schema_rejects_invariant_violations(inst: dict, why: str) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(inst, _process_schema())


SEMANTIC_INVALID = [
    ({"process_id": "x", "version": "1", "steps": [
        {"id": "S1", "tool": "t", "side_effect_class": "pure",
         "depends_on": [{"step_id": "S2", "statuses": ["completed"]}]},
        {"id": "S2", "tool": "t", "side_effect_class": "pure",
         "depends_on": [{"step_id": "S1", "statuses": ["completed"]}]},
    ]}, "dependency cycle"),
    ({"process_id": "x", "version": "1", "steps": [
        {"id": "S1", "tool": "t", "side_effect_class": "pure",
         "consumes": ["b.json"], "produces": ["a.json"]},
        {"id": "S2", "tool": "t", "side_effect_class": "pure",
         "consumes": ["a.json"], "produces": ["b.json"]},
    ]}, "artifact dependency cycle"),
    ({"process_id": "x", "version": "1", "steps": [
        {"id": "S1", "tool": "t", "side_effect_class": "pure",
         "depends_on": [{"step_id": "MISSING", "statuses": ["completed"]}]},
    ]}, "unknown dependency"),
    ({"process_id": "x", "version": "1", "steps": [
        {"id": "S1", "tool": "t", "side_effect_class": "pure"},
        {"id": "S1", "tool": "t", "side_effect_class": "pure"},
    ]}, "duplicate step id"),
    ({"process_id": "x", "version": "1", "steps": [
        {"id": "S1", "tool": "t", "side_effect_class": "pure",
         "consumes": ["missing.json"]},
    ]}, "artifact producer missing"),
    ({"process_id": "x", "version": "1", "steps": [
        {"id": "S1", "tool": "t", "side_effect_class": "pure",
         "resources": [{"kind": "semaphore", "pool": "gpu", "amount": 1}]},
    ]}, "resource pool missing"),
    ({"process_id": "x", "version": "1",
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "pure"}],
      "sla": [{"service": "x", "critical_path_override": ["MISSING"]}]},
     "SLA step missing"),
    ({"process_id": "x", "version": "1",
      "defaults": {"retry": {"policy": "on_transient", "max_attempts": 2}},
      "steps": [{"id": "S1", "tool": "t", "side_effect_class": "non_idempotent"}]},
     "inherited retry unsafe"),
    ({"process_id": "x", "version": "1", "steps": [
        {"id": "S1", "tool": "t", "side_effect_class": "pure", "writes": ["decision"]},
        {"id": "S2", "tool": "t", "side_effect_class": "pure", "writes": ["decision"]},
    ]}, "duplicate context writer"),
    ({"process_id": "x", "version": "1", "steps": [
        {"id": "S1", "tool": "human_confirm", "type": "confirm",
         "side_effect_class": "idempotent", "human_gate": {"type": "confirm"}},
    ]}, "confirm role missing"),
    ({"process_id": "x", "version": "1", "steps": [
        {"id": "S1", "tool": "t", "side_effect_class": "pure",
         "produces": ["A.json", "B.json"]},
    ], "completion": {"mode": "exactly_one_outcome", "outcomes": [
        {"id": "a", "when": {"variable": "decision", "in": ["approved", "rejected"]},
         "terminal_steps": ["S1"], "required_artifacts": ["A.json"]},
        {"id": "b", "when": {"variable": "decision", "equals": "rejected"},
         "terminal_steps": ["S1"], "required_artifacts": ["B.json"]},
    ]}}, "overlapping completion outcomes"),
]


@pytest.mark.parametrize("inst, why", SEMANTIC_INVALID, ids=[c[1] for c in SEMANTIC_INVALID])
def test_process_linter_rejects_cross_step_errors(inst: dict, why: str) -> None:
    jsonschema.validate(inst, _process_schema())
    assert _load_process_linter().lint_process(inst), why


@pytest.mark.parametrize("name", EXPORTED)
def test_checked_in_schema_matches_model(name: str) -> None:
    """Drift guard: if models.py changes, the checked-in *.schema.json must be
    regenerated (backend/.venv/bin/python docs/WorkflowSpec/schema/_generate.py).
    """
    module = _load_models()
    cls = getattr(module, name)
    cls.model_rebuild(_types_namespace=vars(module))
    live = cls.model_json_schema()
    checked_in = _schema(name)
    assert live == checked_in, (
        f"{name}.schema.json is stale vs models.py — re-run schema/_generate.py"
    )
