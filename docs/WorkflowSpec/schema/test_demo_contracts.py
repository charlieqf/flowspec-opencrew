"""End-to-end contract checks for the four executable FlowSpec demos.

These tests deliberately cross file boundaries.  JSON Schema proves each
document's shape; the assertions below prove that frozen definitions, Run
records, artifacts, usage observations, budget transactions, and the
standalone HTML projections still describe the same execution.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any
from urllib.parse import unquote

import jsonschema
import pytest


HERE = Path(__file__).resolve().parent
WORKFLOW_ROOT = HERE.parent
DEMO_ROOT = WORKFLOW_ROOT / "demos"
PROPOSED = HERE / "proposed"
SCENARIOS = tuple(
    sorted(path for path in DEMO_ROOT.iterdir() if path.is_dir() and (path / "process.json").exists())
)
TERMINAL = {"completed", "failed", "blocked", "skipped", "cancelled", "stale_running", "orphaned"}
WAIT_KINDS = {
    "step", "variable", "artifact", "resource", "condition", "user", "host",
    "external_callback",
}
ARTIFACT_PATH_BASES = {"workspace", "run", "step", "step_attempt", "external"}
STORAGE_INDEX_BASES = ARTIFACT_PATH_BASES | {"run_bundle", "database", "platform"}
EXPECTED_LATEST = {
    "loan-approval": {"outcome": "approved_and_disbursed", "usage": 1, "fanout": 0},
    "bank-risk-report": {"outcome": "report_distributed", "usage": 1, "fanout": 0},
    "due-diligence": {"outcome": "report_issued", "usage": 6, "fanout": 4},
    "opencrew-video": {"outcome": "delivered", "usage": 17, "fanout": 15},
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict[str, Any]:
    return _load(PROPOSED / name)


def _validate(instance: Any, schema: dict[str, Any]) -> None:
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    validator.validate(instance)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNTIME = _load_module("flowspec_demo_runtime_tests", DEMO_ROOT / "demo_runtime.py")
LINTER = _load_module("flowspec_demo_linter_tests", PROPOSED / "lint_process.py")


def _runs(scenario: Path) -> list[dict[str, Any]]:
    cases = _load(scenario / "cases.json")
    return [_load(scenario / "runs" / case["run_id"] / "run.json") for case in cases]


def _contract_for(process: dict[str, Any], artifact_name: str) -> tuple[str, dict[str, Any]]:
    matches = [
        (pattern, contract)
        for pattern, contract in process["artifact_contracts"].items()
        if RUNTIME.artifact_matches(pattern, artifact_name)
    ]
    assert len(matches) == 1, (artifact_name, matches)
    return matches[0]


def _schema_fragment(scenario: Path, schema_ref: str) -> dict[str, Any]:
    file_ref, _, pointer = schema_ref.partition("#")
    value = _load(scenario / file_ref)
    if pointer:
        value = RUNTIME.json_pointer(value, pointer)
    assert isinstance(value, dict)
    return value


def _guard_matches(guard: dict[str, Any] | None, context: dict[str, Any]) -> bool:
    if not guard:
        return True
    variable = guard["variable"]
    exists = variable in context
    if "exists" in guard:
        return exists == guard["exists"]
    if not exists:
        return False
    value = context[variable]
    if "equals" in guard:
        return value == guard["equals"]
    if "not_equals" in guard:
        return value != guard["not_equals"]
    return value in guard["in"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda path: path.name)
def test_executable_sources_validate_and_have_no_registry_drift(scenario: Path) -> None:
    process = _load(scenario / "process.json")
    registry = _load(scenario / "tool_registry.json")
    _validate(process, _schema("Process.schema.json"))
    _validate(registry, _schema("ToolRegistry.schema.json"))
    assert LINTER.lint_process(process) == []
    assert process["contract_level"] == "executable"
    assert (scenario / process["tool_registry_ref"]).resolve() == (scenario / "tool_registry.json").resolve()
    assert (scenario / process["context_schema_ref"]).is_file()
    context_schema = _load(scenario / process["context_schema_ref"])
    context_fields = set(context_schema.get("properties") or {})

    for pattern, contract in process["artifact_contracts"].items():
        assert contract.get("schema_ref"), f"{scenario.name}: {pattern} has no schema_ref"
        assert isinstance(_schema_fragment(scenario, contract["schema_ref"]), dict)

    tools = {tool["tool_id"]: tool for tool in registry["tools"]}
    assert len(tools) == len(registry["tools"])
    for step in process["steps"]:
        tool = tools[step["tool"]]
        referenced_fields = {
            *step.get("reads", []),
            *step.get("writes", []),
        }
        if step.get("when"):
            referenced_fields.add(step["when"]["variable"])
        assert referenced_fields <= context_fields, (
            step["id"], sorted(referenced_fields - context_fields)
        )
        for field in ("type", "side_effect_class", "reads", "consumes", "produces", "writes"):
            assert step.get(field, []) == tool.get(field, []), (step["id"], field)
        module_name, function_name = tool["entrypoint"].split(":", 1)
        source = scenario / f"{module_name.replace('.', '/')}.py"
        assert source.is_file()
        assert re.search(rf"^def\s+{re.escape(function_name)}\s*\(", source.read_text(encoding="utf-8"), re.M)

    profile_refs = {step["ai_profile_ref"] for step in process["steps"] if step.get("ai_profile_ref")}
    for ref in profile_refs:
        profile = _load(scenario / ref)
        _validate(profile, _schema("AIExecutionProfile.schema.json"))
        assert profile["model"]["provider"] == "mock-provider"


def test_artifact_binding_is_opt_in_but_fanout_item_identity_is_enforced() -> None:
    ordinary = _load(DEMO_ROOT / "loan-approval" / "process.json")
    ordinary["artifact_contracts"]["RejectionNotice.json"].pop("binding_keys")
    _validate(ordinary, _schema("Process.schema.json"))
    assert LINTER.lint_process(ordinary) == []

    fanout = _load(DEMO_ROOT / "due-diligence" / "process.json")
    fanout["artifact_contracts"]["Extract_{document_id}.json"]["binding_keys"].remove(
        "document_id"
    )
    _validate(fanout, _schema("Process.schema.json"))
    assert any(
        "fanout output Extract_{document_id}.json contract must bind item key document_id"
        in error
        for error in LINTER.lint_process(fanout)
    )


def test_wait_and_storage_base_enums_preserve_the_two_contract_boundaries() -> None:
    run_schema = _schema("RunRecord.schema.json")
    storage_schema = _schema("StorageIndex.schema.json")

    artifact_bases = set(
        run_schema["$defs"]["artifactRecord"]["properties"]["path_base"]["enum"]
    )
    wait_kinds = set(
        run_schema["$defs"]["event"]["allOf"][1]["then"]["properties"]
        ["payload"]["properties"]["wait_reason"]["properties"]["kind"]["enum"]
    )
    path_base_bases = set(
        storage_schema["$defs"]["pathBase"]["properties"]["base"]["enum"]
    )
    entry_bases = set(
        storage_schema["$defs"]["entry"]["properties"]["base"]["enum"]
    )

    assert artifact_bases == ARTIFACT_PATH_BASES
    assert wait_kinds == WAIT_KINDS
    assert path_base_bases == entry_bases == STORAGE_INDEX_BASES
    assert STORAGE_INDEX_BASES - ARTIFACT_PATH_BASES == {
        "run_bundle", "database", "platform",
    }


def _finite_outcome_values(process: dict[str, Any], variable: str) -> set[Any]:
    values: set[Any] = set()
    for outcome in process["completion"]["outcomes"]:
        guard = outcome.get("when") or {}
        if guard.get("variable") != variable:
            continue
        if "equals" in guard:
            values.add(guard["equals"])
        if "in" in guard:
            values.update(guard["in"])
    return values


def test_demo_human_decision_domains_have_explicit_business_outcomes() -> None:
    loan = _load(DEMO_ROOT / "loan-approval" / "process.json")
    loan_gate = next(step for step in loan["steps"] if step["id"] == "S5_human_review")
    assert set(loan_gate["human_gate"]["allowed_decisions"]) == _finite_outcome_values(
        loan, "decision"
    )
    assert "ApplicationRevisionRequest.json" in loan["artifact_contracts"]

    bank = _load(DEMO_ROOT / "bank-risk-report" / "process.json")
    signoff = next(step for step in bank["steps"] if step["id"] == "S10_report_signoff")
    assert set(signoff["human_gate"]["allowed_decisions"]) == _finite_outcome_values(
        bank, "publish_decision"
    )
    assert "ReportRevisionRequest.json" in bank["artifact_contracts"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda path: path.name)
def test_run_lineage_is_one_business_instance_with_immutable_revisions(scenario: Path) -> None:
    records = _runs(scenario)
    assert len(records) == 2
    assert [run["run_sequence"] for run in records] == [1, 2]
    assert len({run["session_id"] for run in records}) == 1
    assert len({run["task_id"] for run in records}) == 1
    assert records[0]["supersedes_run_id"] is None
    assert records[1]["supersedes_run_id"] == records[0]["run_id"]
    assert records[0]["input_revision_hash"] != records[1]["input_revision_hash"]
    assert records[0]["process_snapshot"] == records[1]["process_snapshot"]
    assert records[-1]["outcome"] == EXPECTED_LATEST[scenario.name]["outcome"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda path: path.name)
def test_materialized_runs_validate_end_to_end(scenario: Path) -> None:
    process = _load(scenario / "process.json")
    registry = _load(scenario / "tool_registry.json")
    context_schema = _load(scenario / process["context_schema_ref"])
    run_schema = _schema("RunRecord.schema.json")
    usage_schema = _schema("AIUsageRecord.schema.json")
    ledger_schema = _schema("BudgetLedger.schema.json")
    storage_schema = _schema("StorageIndex.schema.json")
    diagnostic_schema = _schema("DiagnosticLogRecord.schema.json")

    for run in _runs(scenario):
        run_dir = scenario / "runs" / run["run_id"]
        _validate(run, run_schema)
        _validate(run["context"], context_schema)
        storage_ref = PurePosixPath(run["storage_index_ref"])
        assert not storage_ref.is_absolute() and ".." not in storage_ref.parts
        storage = _load(run_dir / storage_ref)
        _validate(storage, storage_schema)
        assert storage["bundle_kind"] == "portable_evidence"
        assert storage["layout_profile"] == "opencrew-compatible-v1"
        assert (storage["session_id"], storage["task_id"], storage["run_id"]) == (
            run["session_id"], run["task_id"], run["run_id"]
        )
        assert {item["base"] for item in storage["path_bases"]} == STORAGE_INDEX_BASES
        materialized = [
            entry for entry in storage["entries"]
            if entry["materialization"] == "materialized"
        ]
        assert materialized
        for entry in materialized:
            assert entry["base"] == "run_bundle"
            locator = PurePosixPath(entry["locator"])
            assert not locator.is_absolute() and ".." not in locator.parts
            raw = (run_dir / locator).read_bytes()
            assert entry["size"] == len(raw)
            assert entry["sha256"] == f"sha256:{hashlib.sha256(raw).hexdigest()}"

        diagnostic_entries = [
            entry for entry in materialized
            if entry["purpose"] == "diagnostic_log" and entry["locator"].endswith(".ndjson")
        ]
        assert diagnostic_entries
        attempt_ids_for_logs = {
            attempt["attempt_id"]
            for step in run["steps"]
            for attempt in step["attempts"]
        }
        for entry in diagnostic_entries:
            records = [
                json.loads(line)
                for line in (run_dir / entry["locator"]).read_text(encoding="utf-8").splitlines()
            ]
            assert records
            assert [record["sequence"] for record in records] == list(range(1, len(records) + 1))
            for record in records:
                _validate(record, diagnostic_schema)
                assert record["session_id"] == run["session_id"]
                assert record["task_id"] == run["task_id"]
                assert record["run_id"] == run["run_id"]
                assert record["correlation_id"] == run["run_id"]
                if record["step_attempt_id"] is not None:
                    assert record["step_attempt_id"] in attempt_ids_for_logs

        entries = storage["entries"]
        assert any(
            entry["owner_type"] == "task"
            and entry["purpose"] == "business_record"
            and entry["authority"] == "database"
            and "no duplicate file root" in entry["description"]
            for entry in entries
        )
        assert any(
            entry["purpose"] == "audit_event"
            and entry["authority"] == "database"
            and entry["locator"] == "session_events"
            for entry in entries
        )
        assert any(
            entry["purpose"] == "service_log"
            and entry["base"] == "platform"
            and entry["authority"] == "log_service"
            for entry in entries
        )
        layout_purposes = {
            entry["purpose"]
            for entry in entries
            if entry["materialization"] == "layout_contract"
        }
        assert {
            "input", "input_manifest", "working", "diagnostic_log", "prompt",
            "output_staging", "output_manifest", "published_artifact",
        } <= layout_purposes
        assert run["status"] == "completed"
        assert all(step["status"] in TERMINAL for step in run["steps"])
        assert all(step["status"] in {"completed", "skipped"} for step in run["steps"])
        assert not any(step["status"] in {"not_started", "waiting", "running"} for step in run["steps"])

        assert _load(run_dir / run["definition_snapshot_ref"]) == process
        assert _load(run_dir / "definition/tool-registry.snapshot.json") == registry
        assert run["process_snapshot"]["digest"] == RUNTIME.digest(process)
        assert run["process_snapshot"]["tool_registry_digest"] == RUNTIME.digest(registry)

        events = [json.loads(line) for line in (run_dir / "events.ndjson").read_text(encoding="utf-8").splitlines()]
        event_attempt_numbers = {
            attempt["attempt_id"]: attempt["attempt_no"]
            for step in run["steps"]
            for attempt in step["attempts"]
        } | {
            item["attempt_id"]: item["attempt_no"]
            for step in run["steps"]
            for item in step["fanout_items"]
        }
        assert events == run["events"]
        assert [event["cursor"] for event in events] == list(range(1, len(events) + 1))
        assert len({event["event_id"] for event in events}) == len(events)
        assert all(event["session_id"] == run["session_id"] for event in events)
        assert all(event["task_id"] == run["task_id"] for event in events)
        assert all(event["correlation_id"] == run["run_id"] for event in events)
        assert events[0]["kind"] == "run.started"
        assert events[-1]["kind"] == "run.completed"
        for event in events:
            attempt_id = event["step_attempt_id"]
            attempt_no = event["step_attempt_no"]
            assert (attempt_id is None) == (attempt_no is None)
            if attempt_id is not None:
                assert event_attempt_numbers[attempt_id] == attempt_no
            if event["step_id"] is None or event["kind"] == "step.skipped":
                assert attempt_id is None
            else:
                assert attempt_id is not None, event["kind"]
            if event["kind"] in {"step.waiting", "step.blocked"}:
                assert event["payload"]["wait_reason"]["kind"] in WAIT_KINDS
                assert event["payload"]["wait_reason"]["detail"]

        usage = _load(run_dir / "usage.json")
        assert usage == run["usage_records"]
        assert len({record["usage_record_id"] for record in usage}) == len(usage)
        agent_executions_by_attempt: dict[str, set[str]] = {}
        for record in usage:
            execution_id = record.get("agent_execution_id")
            if execution_id is not None:
                agent_executions_by_attempt.setdefault(
                    record["step_attempt_id"], set()
                ).add(execution_id)
        assert all(
            len(execution_ids) <= 1
            for execution_ids in agent_executions_by_attempt.values()
        ), "a Step/Fanout Item Attempt may contain at most one Agent Execution"
        profile_snapshots = {
            (profile["profile_id"], profile["version"], profile["digest"])
            for profile in run["process_snapshot"]["profiles"]
        }
        usage_attempt_ids = {
            attempt["attempt_id"]
            for step in run["steps"]
            for attempt in step["attempts"]
        } | {
            item["attempt_id"]
            for step in run["steps"]
            for item in step["fanout_items"]
        }
        for record in usage:
            _validate(record, usage_schema)
            assert record["run_id"] == run["run_id"]
            assert record["step_attempt_id"] in usage_attempt_ids
            frozen = record["profile_snapshot"]
            assert (frozen["profile_id"], frozen["version"], frozen["digest"]) in profile_snapshots
            assert record["cost"]["status"] == "estimated"
            assert record["cost"]["source"] == "price_snapshot_calculated"

        ledger = _load(run_dir / "budget-ledger.json")
        _validate(ledger, ledger_schema)
        assert ledger["run_id"] == run["run_id"]
        assert ledger["reserved"] == "0"
        settled = sum(
            (Decimal(item["amount"]) for item in ledger["transactions"] if item["kind"] == "settle"),
            Decimal("0"),
        )
        assert settled == Decimal(ledger["settled"]) == Decimal(run["budget_summary"]["settled"])
        assert Decimal(ledger["available"]) == Decimal(ledger["limit"]) - settled
        operations = {record["operation_id"] for record in usage}
        for operation_id in operations:
            kinds = {item["kind"] for item in ledger["transactions"] if item["operation_id"] == operation_id}
            assert {"reserve", "settle"} <= kinds

        artifact_ids = {artifact["artifact_id"] for artifact in run["artifacts"]}
        assert len(artifact_ids) == len(run["artifacts"])
        attempt_ids = {
            attempt["attempt_id"]
            for step in run["steps"]
            for attempt in step["attempts"]
        } | {
            item["attempt_id"]
            for step in run["steps"]
            for item in step["fanout_items"]
        }
        producer_input_hashes = {
            attempt["attempt_id"]: attempt["input_snapshot_hash"]
            for step in run["steps"]
            for attempt in step["attempts"]
        } | {
            item["attempt_id"]: item["input_snapshot_hash"]
            for step in run["steps"]
            for item in step["fanout_items"]
        }
        assert set(producer_input_hashes) == attempt_ids
        for artifact in run["artifacts"]:
            assert artifact["path_base"] == "run"
            assert "input_revision_hash" not in artifact
            relpath = PurePosixPath(artifact["path"])
            assert not relpath.is_absolute() and ".." not in relpath.parts
            stored_path = run_dir / artifact["path"]
            raw = stored_path.read_bytes()
            assert artifact["size"] == len(raw)
            assert artifact["sha256"] == f"sha256:{hashlib.sha256(raw).hexdigest()}"
            assert artifact["producer"]["attempt_id"] in attempt_ids
            assert producer_input_hashes[artifact["producer"]["attempt_id"]].startswith("sha256:")
            _, contract = _contract_for(process, artifact["name"])
            assert artifact["media_type"] == contract["media_type"]
            assert artifact["classification"] == contract["classification"]
            assert set(contract.get("binding_keys") or []) <= set(artifact["binding"])
            assert all(value is not None for value in artifact["binding"].values())
            stored = json.loads(raw) if stored_path.suffix == ".json" else raw.decode("utf-8")
            payload = stored["payload"] if artifact["mock"] and isinstance(stored, dict) and stored.get("mock") is True and "payload" in stored else stored
            _validate(payload, _schema_fragment(scenario, artifact["schema_ref"]))

        for step_def in process["steps"]:
            step = next(item for item in run["steps"] if item["step_id"] == step_def["id"])
            assert set(step["artifact_ids"]) <= artifact_ids
            assert set(step["usage_record_ids"]) <= {item["usage_record_id"] for item in usage}
            keys = [item["item_key"] for item in step["fanout_items"]]
            assert len(keys) == len(set(keys))
            if step_def.get("fanout"):
                binding_key = step_def["fanout"]["item_key"]
                for item in step["fanout_items"]:
                    for artifact_id in item["artifact_ids"]:
                        artifact = next(value for value in run["artifacts"] if value["artifact_id"] == artifact_id)
                        assert artifact["binding"][binding_key] == item["item_key"]

        for work_item in run["human_tasks"]:
            step = next(item for item in run["steps"] if item["step_id"] == work_item["step_id"])
            step_def = next(item for item in process["steps"] if item["id"] == work_item["step_id"])
            assert work_item["status"] == "completed"
            assert work_item["revision"] == work_item["expected_revision"] + 1
            assert work_item["decision_id"]
            assert work_item["reason"] and work_item["actor"]
            assert set(work_item["actor"]["roles"]) & set(step_def["human_gate"]["roles"])
            assert work_item["input_snapshot_hash"] == step["attempts"][0]["input_snapshot_hash"]
            kinds = [event["kind"] for event in events if event["step_id"] == work_item["step_id"]]
            assert "human_task.created" in kinds and "human_task.completed" in kinds

        outcomes = [
            outcome
            for outcome in process["completion"]["outcomes"]
            if _guard_matches(outcome.get("when"), run["context"])
        ]
        assert [outcome["id"] for outcome in outcomes] == [run["outcome"]]
        selected = outcomes[0]
        for step_id in selected["terminal_steps"]:
            assert next(step for step in run["steps"] if step["step_id"] == step_id)["status"] == "completed"
        for name in selected["required_artifacts"]:
            assert any(RUNTIME.artifact_matches(name, artifact["name"]) for artifact in run["artifacts"])

    latest = _runs(scenario)[-1]
    assert len(latest["usage_records"]) == EXPECTED_LATEST[scenario.name]["usage"]
    assert sum(len(step["fanout_items"]) for step in latest["steps"]) == EXPECTED_LATEST[scenario.name]["fanout"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda path: path.name)
def test_mock_execution_is_deterministic(scenario: Path, tmp_path: Path) -> None:
    output_parent = tmp_path / "flowspec-demo-test-runs"
    generated = RUNTIME.build_scenario(scenario, output_parent)
    assert generated == _runs(scenario)
    assert RUNTIME.build_scenario(scenario, output_parent) == generated


def test_video_agent_inner_loop_materializes_multiple_invocations() -> None:
    scenario = DEMO_ROOT / "opencrew-video"
    profile = _load(scenario / "profiles/creative-agent.json")
    assert profile["kind"] == "agent"
    assert profile["budget"]["max_turns"] == 4
    assert profile["budget"]["max_tool_calls"] == 4

    for run in _runs(scenario):
        usage = [record for record in run["usage_records"] if record["step_id"] == "S1_creative_brief"]
        assert len(usage) == 2
        assert len({record["agent_execution_id"] for record in usage}) == 1
        assert None not in {record["agent_execution_id"] for record in usage}
        assert [record["usage"]["provider_units"]["agent_turn"] for record in usage] == [1, 2]
        assert sum(Decimal(record["cost"]["amount"]) for record in usage) == Decimal("0.020")

        started = [
            event
            for event in run["events"]
            if event["kind"] == "model.invocation.started" and event["step_id"] == "S1_creative_brief"
        ]
        assert [event["payload"]["agent_turn"] for event in started] == [1, 2]
        assert {event["payload"]["agent_execution_id"] for event in started} == {
            usage[0]["agent_execution_id"]
        }


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda path: path.name)
def test_demo_ui_has_complete_chinese_business_labels(scenario: Path) -> None:
    metadata = _load(scenario / "demo.json")
    process = _load(scenario / "process.json")
    stage_labels = metadata["stage_labels"]
    step_labels = metadata["step_labels"]
    outcome_labels = metadata["outcome_labels"]

    assert set(stage_labels) == set(process["stages"])
    assert set(step_labels) == {step["id"] for step in process["steps"]}
    assert set(outcome_labels) == {
        outcome["id"] for outcome in process["completion"]["outcomes"]
    }
    for value in [*stage_labels.values(), *outcome_labels.values()]:
        assert re.search(r"[\u4e00-\u9fff]", value), value
    for step_id, localized in step_labels.items():
        assert set(localized) == {"title", "description"}, step_id
        assert re.search(r"[\u4e00-\u9fff]", localized["title"]), step_id
        assert re.search(r"[\u4e00-\u9fff]", localized["description"]), step_id


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda path: path.name)
def test_standalone_html_embeds_the_authoritative_documents(scenario: Path) -> None:
    html = (scenario / "index.html").read_text(encoding="utf-8")
    lowered = html.lower()
    assert "<link" not in lowered
    assert not re.search(r"<script[^>]+\bsrc\s*=", lowered)
    assert "fetch(" not in lowered
    assert "Standalone HTML · embedded Process + Run records · no network" not in html
    assert 'class="top-note"' not in html
    assert "Executive TL;DR" in html
    assert "演示边界" in html
    assert "步骤次序、分叉与汇合" in html
    assert "契约投影 · 非操作按钮" in html
    assert "存储与日志" in html
    assert "为什么 task 没有目录" in html
    assert "Diagnostic levels" in html
    assert 'data-tab="storage"' in html
    assert "只给正式产物足够的治理" in html
    assert "为什么不在 Artifact 重复输入 hash" in html
    assert "为什么四个 Demo 的 binding 都非空" in html
    assert "临时文件明确排除" in html
    assert "手机阅读提示" in html
    assert "color-scheme:dark" not in lowered
    match = re.search(
        r'<script type="application/json" id="demo-data">(.*?)</script>',
        html,
        re.S,
    )
    assert match
    bundle = json.loads(match.group(1))
    assert bundle["process"] == _load(scenario / "process.json")
    assert bundle["registry"] == _load(scenario / "tool_registry.json")
    assert bundle["runs"] == _runs(scenario)
    assert bundle["storage_indexes"] == [
        _load(scenario / "runs" / run["run_id"] / run["storage_index_ref"])
        for run in bundle["runs"]
    ]
    assert all(
        f"<strong>{label['title']}</strong>" in html
        for label in bundle["metadata"]["step_labels"].values()
    )
    assert set(bundle["metadata"]["run_control_examples"]) == {"full", "through", "from", "single"}
    assert html.count('class="dag-map-node"') == len(bundle["process"]["steps"])
    assert html.count('class="dependency-mobile-row"') == len(bundle["process"]["steps"])
    assert html.count('class="run-control-card"') == 4
    assert html.count('class="log-level ') == 5
    assert html.count('class="artifact-scope-card') == 3
    assert all(
        "input_revision_hash" not in artifact
        for run in bundle["runs"]
        for artifact in run["artifacts"]
    )
    assert all(
        item.get("input_snapshot_hash", "").startswith("sha256:")
        for run in bundle["runs"]
        for step in run["steps"]
        for item in step["fanout_items"]
    )
    incoming: dict[str, int] = {step["id"]: 0 for step in bundle["process"]["steps"]}
    outgoing: dict[str, int] = {step["id"]: 0 for step in bundle["process"]["steps"]}

    def sources(dependency: dict[str, Any]) -> list[str]:
        if dependency.get("step_id"):
            return [dependency["step_id"]]
        return [source for child in dependency.get("any_of") or [] for source in sources(child)]

    for step in bundle["process"]["steps"]:
        for dependency in step.get("depends_on") or []:
            for source in sources(dependency):
                incoming[step["id"]] += 1
                outgoing[source] += 1
    assert max(incoming.values()) > 1, "each demo must visibly exercise a multi-upstream join"
    assert max(outgoing.values()) > 1, "each demo must visibly exercise a one-to-many fork"
    for step in bundle["process"]["steps"]:
        assert step["id"] in html


def test_landing_page_links_exactly_four_scenarios() -> None:
    html = (DEMO_ROOT / "index.html").read_text(encoding="utf-8")
    assert "color-scheme:dark" not in html.lower()
    links = re.findall(r'<a class="card" href="([^"/]+)/index\.html"', html)
    assert links == ["loan-approval", "bank-risk-report", "due-diligence", "opencrew-video"]
    assert "分布式 claim / lease / fencing" in html
    assert "Standalone HTML" not in html
    assert 'href="../index.html"' in html


def test_overview_is_self_contained_and_links_all_authoritative_material() -> None:
    html = (WORKFLOW_ROOT / "index.html").read_text(encoding="utf-8")
    lowered = html.lower()
    assert "<link" not in lowered
    assert not re.search(r"<script[^>]+\bsrc\s*=", lowered)
    assert "fetch(" not in lowered
    assert "公司决策层" in html and "开发团队" in html
    assert all(label in html for label in ("可直接复用", "团队统一目标", "上线前补齐"))
    assert "一次 AI 调用：先检查权限，再执行，最后逐笔记账" in html
    assert "三列表示同一能力的三个建设阶段" in html
    assert all(
        phrase not in html
        for phrase in (
            "每个 Demo 都是单页、自包含、无网络依赖",
            "本页负责 framing",
            "全文强制区分",
            "implemented/proposed/roadmap 边界",
            "现状与目标严格分层",
            "现状与目标不要混读",
        )
    )
    assert 'id="run-controls"' in html
    assert "运行整个流程" in html and "运行到某步骤" in html
    assert "从某步骤开始重跑" in html and "重新单独运行某步骤" in html
    assert "一对多 fork · 多对一 join" in html
    assert 'id="storage-logging"' in html
    assert all(level in html for level in ("debug", "info", "warning", "error", "critical"))
    assert "task 是数据库记录" in html
    assert "先分清“谁属于谁”，再讨论怎么运行" in html
    assert "session + task 共同表达一个业务实例" in html
    assert html.count('class="relation-legend-item"') == 4
    assert html.count('class="relation-node"') == 4
    assert html.count('class="relation-evidence"') == 6
    assert all(
        fact in html
        for fact in (
            "12 个步骤",
            "三路数据汇合",
            "4 个并行文档项",
            "15 个并行素材项",
        )
    )
    assert "color-scheme:dark" not in lowered

    hrefs = re.findall(r'href="([^"]+)"', html)
    required = {
        *(f"{index:02d}_{name}.md" for index, name in [
            (0, "Overview"),
            (1, "ConceptModel"),
            (2, "ProcessDefinition"),
            (3, "ToolContract"),
            (4, "VariablesAndState"),
            (5, "Workspace"),
            (6, "Runtime_Observability"),
            (7, "ImplementationBinding"),
            (8, "PriorArt_CrossReference"),
            (9, "ProductionLessons"),
            (10, "AI_ModelAndAgent_Profile"),
            (11, "FourScenarioValidation"),
            (12, "ExecutableDemos"),
        ]),
        "demos/loan-approval/index.html",
        "demos/bank-risk-report/index.html",
        "demos/due-diligence/index.html",
        "demos/opencrew-video/index.html",
        "README.md",
        "demos/README.md",
        "schema/README.md",
        "examples/loan-approval.md",
        "examples/bank-data-report.md",
        "examples/due-diligence.md",
        "examples/opencrew-video-creation.md",
        "schema/proposed/Process.schema.json",
        "schema/proposed/RunRecord.schema.json",
        "schema/proposed/AIExecutionProfile.schema.json",
        "schema/proposed/AIUsageRecord.schema.json",
        "schema/proposed/DiagnosticLogRecord.schema.json",
        "schema/proposed/StorageIndex.schema.json",
    }
    assert required <= set(hrefs)
    for href in hrefs:
        if href.startswith("#"):
            continue
        assert "://" not in href
        target = (WORKFLOW_ROOT / href.split("#", 1)[0]).resolve()
        assert target == WORKFLOW_ROOT or WORKFLOW_ROOT in target.parents
        assert target.exists(), href


def test_concept_model_defines_hierarchy_cardinality_and_lifecycle_sequence() -> None:
    markdown = (WORKFLOW_ROOT / "01_ConceptModel.md").read_text(encoding="utf-8")
    assert "先分清四种关系" in markdown
    assert "权威层级、基数与包含关系" in markdown
    assert "一次正常执行与一次业务返工的先后关系" in markdown
    assert "session [1] ⇄ task [1]" in markdown
    assert "Run [0..N]" in markdown
    assert "Step [1..N]" in markdown
    assert "Step Attempt [0..N]" in markdown
    assert "Agent Execution [0..1]" in markdown
    assert "Model Invocation [1..N]" in markdown
    assert "Tool Call [0..N]" in markdown
    assert "operation / Invocation" in markdown
    assert "不是两级重复文件树" in markdown


def test_only_purpose_built_html_pages_exist() -> None:
    expected = {
        "index.html",
        "demos/index.html",
        "demos/loan-approval/index.html",
        "demos/bank-risk-report/index.html",
        "demos/due-diligence/index.html",
        "demos/opencrew-video/index.html",
    }
    actual = {
        path.relative_to(WORKFLOW_ROOT).as_posix()
        for path in WORKFLOW_ROOT.rglob("*.html")
    }
    assert actual == expected
    assert not (WORKFLOW_ROOT / "build_docs_html.py").exists()


def test_all_markdown_local_links_resolve_without_html_mirrors() -> None:
    link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for markdown_path in WORKFLOW_ROOT.rglob("*.md"):
        markdown = markdown_path.read_text(encoding="utf-8")
        for raw_target in link_pattern.findall(markdown):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if not relative:
                continue
            resolved = (markdown_path.parent / relative).resolve()
            assert resolved.exists(), f"{markdown_path}: unresolved link {target}"


def test_purpose_built_html_contains_no_internal_placeholder_bytes() -> None:
    for path in WORKFLOW_ROOT.rglob("*.html"):
        assert "\x00" not in path.read_text(encoding="utf-8"), path
