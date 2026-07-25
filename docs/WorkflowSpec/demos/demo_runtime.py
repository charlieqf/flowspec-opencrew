"""Deterministic, dependency-free FlowSpec demo runner.

The runner is intentionally small.  It does not pretend to be OpenCrew's target
scheduler; it materializes the proposed executable contracts so the four demo
processes can be linted, replayed, and inspected without calling a model or an
external service.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import importlib.util
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable


DEMO_ROOT = Path(__file__).resolve().parent
TERMINAL_STEP_STATUSES = {"completed", "failed", "blocked", "skipped", "cancelled", "orphaned"}
SUCCESS_STEP_STATUSES = {"completed", "skipped"}
_TEMPLATE_PART = re.compile(r"\{([^{}]+)\}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_bytes(value)).hexdigest()}"


def short_digest(value: Any, length: int = 16) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()[:length]


def decimal_text(value: Decimal | str | int | float) -> str:
    normalized = Decimal(str(value)).quantize(Decimal("0.000001"))
    return format(normalized, "f").rstrip("0").rstrip(".") or "0"


def load_json(path: Path) -> dict[str, Any] | list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def artifact_pattern(value: str) -> str:
    return _TEMPLATE_PART.sub("*", value)


def artifact_matches(declared: str, actual: str) -> bool:
    return fnmatch.fnmatchcase(actual, artifact_pattern(declared)) or fnmatch.fnmatchcase(declared, artifact_pattern(actual))


def nested_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(path)
    return current


def substitute(template: str, values: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        try:
            return str(nested_value(values, key))
        except KeyError:
            return match.group(0)

    return _TEMPLATE_PART.sub(replace, template)


def safe_path_part(value: str) -> str:
    """Keep generated evidence paths portable without changing logical IDs."""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "unknown"


def json_pointer(value: Any, pointer: str) -> Any:
    if pointer in {"", "/"}:
        return value
    current = value
    for raw_part in pointer.lstrip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(pointer)
    return current


class DemoContractError(RuntimeError):
    pass


class BudgetExceeded(DemoContractError):
    pass


class DemoClock:
    def __init__(self, start: str):
        parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
        self.current = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def tick(self, seconds: int = 1) -> str:
        value = self.current.isoformat().replace("+00:00", "Z")
        self.current += timedelta(seconds=seconds)
        return value


@dataclass(frozen=True)
class ToolContext:
    """The deliberately narrow interface exposed to scenario mock tools."""

    run_id: str
    step_id: str
    variables: dict[str, Any]
    case: dict[str, Any]
    artifacts: dict[str, Any]
    item: dict[str, Any] | None = None
    item_key: str | None = None

    def artifact(self, pattern: str) -> Any:
        matches = [payload for name, payload in self.artifacts.items() if artifact_matches(pattern, name)]
        if not matches:
            raise KeyError(f"No artifact matches {pattern}")
        return matches[0] if len(matches) == 1 else matches

    def human_decision(self) -> dict[str, Any]:
        value = (self.case.get("human_decisions") or {}).get(self.step_id)
        if not isinstance(value, dict):
            raise DemoContractError(f"{self.step_id} has no deterministic human_decision fixture")
        return copy.deepcopy(value)

    def callback(self) -> dict[str, Any]:
        value = (self.case.get("callbacks") or {}).get(self.step_id)
        if not isinstance(value, dict):
            raise DemoContractError(f"{self.step_id} has no deterministic callback fixture")
        return copy.deepcopy(value)


class ScenarioRuntime:
    def __init__(self, scenario_dir: Path, case: dict[str, Any], output_parent: Path):
        self.scenario_dir = scenario_dir.resolve()
        self.case = copy.deepcopy(case)
        self.process = load_json(self.scenario_dir / "process.json")
        self.registry = load_json(self.scenario_dir / "tool_registry.json")
        if not isinstance(self.process, dict) or not isinstance(self.registry, dict):
            raise DemoContractError("process.json and tool_registry.json must contain objects")
        self.run_id = str(case["run_id"])
        self.run_dir = output_parent.resolve() / self.run_id
        self.clock = DemoClock(str(case["started_at"]))
        self.variables = copy.deepcopy(case.get("context") or {})
        self.input_revision_hash = digest({"context": self.variables, "source_revision": case.get("source_revision")})
        self.steps: dict[str, dict[str, Any]] = {
            str(step["id"]): {
                "step_id": str(step["id"]),
                "tool_id": str(step["tool"]),
                "tool_version": "",
                "status": "not_started",
                "skip_reason": None,
                "attempts": [],
                "fanout_items": [],
                "artifact_ids": [],
                "usage_record_ids": [],
            }
            for step in self.process.get("steps") or []
        }
        self.step_defs = {str(step["id"]): step for step in self.process.get("steps") or []}
        self.tool_defs = {str(tool["tool_id"]): tool for tool in self.registry.get("tools") or []}
        self.artifact_payloads: dict[str, Any] = {}
        self.artifact_records: list[dict[str, Any]] = []
        self.usage_records: list[dict[str, Any]] = []
        self.human_tasks: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.modules: dict[str, Any] = {}
        self.profiles: dict[str, dict[str, Any]] = {}
        self.profile_digests: dict[str, str] = {}
        self.cursor = 0
        self.ledger_transactions: list[dict[str, Any]] = []
        budget = self.process.get("run_budget") or {}
        self.budget_currency = str(budget.get("currency") or "USD")
        self.budget_limit = Decimal(str(budget.get("max_cost") or "0"))
        self.budget_reserved = Decimal("0")
        self.budget_settled = Decimal("0")
        self._validate_executable_contract()
        self._load_profiles()

    def _validate_executable_contract(self) -> None:
        if self.process.get("contract_level") != "executable":
            raise DemoContractError("demo process must use contract_level=executable")
        if self.process.get("branch_closure") != "skip_unreachable":
            raise DemoContractError("demo runtime requires branch_closure=skip_unreachable")
        if len(self.steps) != len(self.process.get("steps") or []):
            raise DemoContractError("duplicate process step id")
        if len(self.tool_defs) != len(self.registry.get("tools") or []):
            raise DemoContractError("duplicate tool registry id")

        contracts = self.process.get("artifact_contracts") or {}
        writer_owner: dict[str, str] = {}
        graph: dict[str, set[str]] = {step_id: set() for step_id in self.steps}
        for step in self.process.get("steps") or []:
            step_id = str(step["id"])
            tool_id = str(step["tool"])
            tool = self.tool_defs.get(tool_id)
            if tool is None:
                raise DemoContractError(f"{step_id}: unknown tool {tool_id}")
            self.steps[step_id]["tool_version"] = str(tool["version"])
            for key in ("type", "side_effect_class", "reads", "consumes", "produces", "writes"):
                process_value = step.get(key, [] if key in {"reads", "consumes", "produces", "writes"} else None)
                tool_value = tool.get(key, [] if key in {"reads", "consumes", "produces", "writes"} else None)
                if process_value != tool_value:
                    raise DemoContractError(f"{step_id}: Process/Tool Registry drift for {key}: {process_value!r} != {tool_value!r}")
            for name in [*(step.get("consumes") or []), *(step.get("produces") or [])]:
                if not any(artifact_matches(str(pattern), str(name)) for pattern in contracts):
                    raise DemoContractError(f"{step_id}: artifact has no typed contract: {name}")
            for field in step.get("writes") or []:
                if field in writer_owner and writer_owner[field] != step_id:
                    raise DemoContractError(f"context field {field} has multiple owners: {writer_owner[field]}, {step_id}")
                writer_owner[field] = step_id
            for dependency in step.get("depends_on") or []:
                for upstream in self._dependency_ids(dependency):
                    if upstream not in self.steps:
                        raise DemoContractError(f"{step_id}: unknown dependency {upstream}")
                    graph[step_id].add(upstream)
            profile_ref = step.get("ai_profile_ref")
            if step.get("type") in {"model", "agent"} and not profile_ref:
                raise DemoContractError(f"{step_id}: AI step must freeze ai_profile_ref")
            fanout = step.get("fanout")
            if fanout and not fanout.get("item_key"):
                raise DemoContractError(f"{step_id}: executable fanout requires a stable item_key")
            human = step.get("human_gate")
            if step.get("type") == "confirm":
                if not human or not human.get("allowed_decisions") or not human.get("bind_to_input_hash"):
                    raise DemoContractError(f"{step_id}: executable confirm requires decisions and input-hash binding")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise DemoContractError(f"dependency cycle reaches {step_id}")
            if step_id in visited:
                return
            visiting.add(step_id)
            for upstream in graph[step_id]:
                visit(upstream)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)

        completion = self.process.get("completion") or {}
        outcomes = completion.get("outcomes") or []
        if not outcomes:
            raise DemoContractError("executable Process requires at least one completion outcome")
        outcome_ids: set[str] = set()
        for outcome in outcomes:
            outcome_id = str(outcome["id"])
            if outcome_id in outcome_ids:
                raise DemoContractError(f"duplicate completion outcome {outcome_id}")
            outcome_ids.add(outcome_id)
            for step_id in outcome.get("terminal_steps") or []:
                if step_id not in self.steps:
                    raise DemoContractError(f"outcome {outcome_id}: unknown terminal step {step_id}")
            for name in outcome.get("required_artifacts") or []:
                if not any(artifact_matches(str(pattern), str(name)) for pattern in contracts):
                    raise DemoContractError(f"outcome {outcome_id}: artifact has no contract: {name}")

    @staticmethod
    def _dependency_ids(dependency: dict[str, Any]) -> list[str]:
        if "any_of" in dependency:
            values: list[str] = []
            for child in dependency.get("any_of") or []:
                values.extend(ScenarioRuntime._dependency_ids(child))
            return values
        return [str(dependency.get("step_id") or "")]

    def _load_profiles(self) -> None:
        for step in self.process.get("steps") or []:
            ref = step.get("ai_profile_ref")
            if not ref or ref in self.profiles:
                continue
            path = (self.scenario_dir / str(ref)).resolve()
            if self.scenario_dir not in path.parents:
                raise DemoContractError(f"AI profile escapes scenario directory: {ref}")
            value = load_json(path)
            if not isinstance(value, dict):
                raise DemoContractError(f"AI profile must be an object: {ref}")
            self.profiles[str(ref)] = value
            self.profile_digests[str(ref)] = digest(value)

    def emit(
        self,
        kind: str,
        step_id: str | None,
        payload: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
        *,
        attempt_id: str | None = None,
        attempt_no: int | None = None,
    ) -> None:
        if (attempt_id is None) != (attempt_no is None):
            raise DemoContractError(
                f"event {kind}: step_attempt_id and step_attempt_no must be supplied together"
            )
        self.cursor += 1
        event_payload = payload or {}
        self.events.append(
            {
                "schema_version": "1.1",
                "event_id": f"evt_{self.run_id}_{self.cursor:04d}",
                "cursor": self.cursor,
                "kind": kind,
                "session_id": str(self.case["session_id"]),
                "task_id": str(self.case["task_id"]),
                "run_id": self.run_id,
                "step_id": step_id,
                "tool_id": str(self.step_defs[step_id]["tool"]) if step_id else None,
                "step_attempt_id": attempt_id,
                "step_attempt_no": attempt_no,
                "correlation_id": self.run_id,
                "actor": actor or {"type": "system", "id": "flowspec-demo-runner"},
                "payload": event_payload,
                "at": self.clock.tick(),
            }
        )

    def _guard(self, guard: dict[str, Any] | None) -> tuple[str, Any]:
        if not guard:
            return "true", None
        variable = str(guard["variable"])
        exists = variable in self.variables
        if "exists" in guard:
            return ("true" if exists == bool(guard["exists"]) else "false"), None
        if not exists:
            return "missing", variable
        value = self.variables[variable]
        if "equals" in guard:
            matched = value == guard["equals"]
        elif "not_equals" in guard:
            matched = value != guard["not_equals"]
        else:
            matched = value in guard.get("in", [])
        return ("true" if matched else "false"), value

    def _dependency_state(self, dependency: dict[str, Any]) -> tuple[bool, bool]:
        """Return (met, impossible)."""
        if "any_of" in dependency:
            states = [self._dependency_state(child) for child in dependency.get("any_of") or []]
            return any(met for met, _ in states), bool(states) and all(impossible for _, impossible in states)
        upstream = self.steps[str(dependency["step_id"])]
        met = upstream["status"] in set(dependency.get("statuses") or [])
        impossible = not met and upstream["status"] in TERMINAL_STEP_STATUSES
        return met, impossible

    def _producer_steps(self, artifact_name: str) -> list[dict[str, Any]]:
        return [
            self.steps[str(step["id"])]
            for step in self.process.get("steps") or []
            if any(artifact_matches(str(produced), artifact_name) for produced in step.get("produces") or [])
        ]

    def _writer_steps(self, variable: str) -> list[dict[str, Any]]:
        return [self.steps[str(step["id"])] for step in self.process.get("steps") or [] if variable in (step.get("writes") or [])]

    def _artifacts_for(self, pattern: str) -> list[str]:
        return sorted(name for name in self.artifact_payloads if artifact_matches(pattern, name))

    def _mark_skipped(self, step_id: str, kind: str, detail: str) -> None:
        record = self.steps[step_id]
        record["status"] = "skipped"
        record["skip_reason"] = {"kind": kind, "detail": detail}
        self.emit("step.skipped", step_id, {"reason": record["skip_reason"]})

    def _input_snapshot_hash(self, step: dict[str, Any], item_key: str | None = None) -> str:
        consumed = {
            name: next(record["sha256"] for record in self.artifact_records if record["name"] == name)
            for pattern in step.get("consumes") or []
            for name in self._artifacts_for(str(pattern))
        }
        reads = {name: self.variables.get(name) for name in step.get("reads") or []}
        profile_ref = step.get("ai_profile_ref")
        execution_contract = {
            "step": {
                key: step.get(key)
                for key in (
                    "id", "tool", "type", "reads", "consumes", "produces", "writes",
                    "side_effect_class", "fanout", "human_gate",
                )
                if step.get(key) is not None
            },
            "tool_version": self.tool_defs[str(step["tool"])]["version"],
            "profile_digest": self.profile_digests.get(str(profile_ref)) if profile_ref else None,
        }
        # This is the single derivation fingerprint for the producer Attempt.
        # Artifact records reference the Attempt instead of duplicating this hash
        # on every output from the same execution.
        return digest(
            {
                "schema_version": "1.0",
                "declared_reads": reads,
                "consumed_artifact_sha256": consumed,
                "fanout_item_key": item_key,
                "execution_contract": execution_contract,
            }
        )

    def _load_entrypoint(self, entrypoint: str) -> Callable[[ToolContext], dict[str, Any]]:
        module_name, function_name = entrypoint.split(":", 1)
        if module_name not in self.modules:
            module_path = self.scenario_dir / f"{module_name.replace('.', '/')}.py"
            spec = importlib.util.spec_from_file_location(f"flowspec_demo_{self.scenario_dir.name}_{module_name}", module_path)
            if spec is None or spec.loader is None:
                raise DemoContractError(f"Cannot load mock tool module {module_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.modules[module_name] = module
        function = getattr(self.modules[module_name], function_name, None)
        if not callable(function):
            raise DemoContractError(f"Tool entrypoint is not callable: {entrypoint}")
        return function

    def _artifact_contract(self, name: str) -> dict[str, Any]:
        matches = [contract for pattern, contract in (self.process.get("artifact_contracts") or {}).items() if artifact_matches(str(pattern), name)]
        if len(matches) != 1:
            raise DemoContractError(f"Artifact {name} resolves to {len(matches)} contracts")
        return matches[0]

    def _write_artifact(
        self,
        *,
        step: dict[str, Any],
        attempt_id: str,
        attempt_no: int,
        name: str,
        payload: Any,
        item_values: dict[str, Any] | None = None,
    ) -> str:
        name = substitute(name, item_values or {})
        if name in self.artifact_payloads:
            raise DemoContractError(f"Artifact name already materialized in Run: {name}")
        contract = self._artifact_contract(name)
        logical_suffix = Path(name).suffix.lower()
        stored_name = name if logical_suffix in {".json", ".txt", ".md", ".csv"} else f"{name}.mock.json"
        path = self.run_dir / "artifacts" / stored_name
        if logical_suffix in {".txt", ".md", ".csv"} and isinstance(payload, str):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
            raw = payload.encode("utf-8")
        else:
            envelope = payload if logical_suffix == ".json" else {"mock": True, "logical_name": name, "payload": payload}
            write_json(path, envelope)
            raw = path.read_bytes()
        binding = {key: self.variables.get(key) for key in contract.get("binding_keys") or []}
        for key in contract.get("binding_keys") or []:
            if item_values and key in item_values:
                binding[key] = item_values[key]
        artifact_id = f"art_{short_digest([self.run_id, name, attempt_id])}"
        record = {
            "artifact_id": artifact_id,
            "name": name,
            "path_base": "run",
            "path": str(path.relative_to(self.run_dir)).replace("\\", "/"),
            "media_type": str(contract["media_type"]),
            "schema_ref": contract.get("schema_ref"),
            "classification": str(contract["classification"]),
            "sha256": f"sha256:{hashlib.sha256(raw).hexdigest()}",
            "size": len(raw),
            "producer": {
                "step_id": str(step["id"]),
                "attempt_id": attempt_id,
                "tool_id": str(step["tool"]),
                "tool_version": str(self.tool_defs[str(step["tool"])]["version"]),
            },
            "validity": "valid",
            "binding": binding,
            "created_at": self.clock.tick(),
            "mock": True,
        }
        self.artifact_payloads[name] = copy.deepcopy(payload)
        self.artifact_records.append(record)
        self.steps[str(step["id"])]["artifact_ids"].append(artifact_id)
        self.emit(
            "artifact.published",
            str(step["id"]),
            {"artifact_id": artifact_id, "name": name, "sha256": record["sha256"]},
            attempt_id=attempt_id,
            attempt_no=attempt_no,
        )
        return artifact_id

    def _ledger_transaction(self, kind: str, operation_id: str, amount: Decimal, invocation_id: str, reason: str) -> None:
        transaction_id = f"btx_{short_digest([self.run_id, kind, operation_id, len(self.ledger_transactions)])}"
        self.ledger_transactions.append(
            {
                "transaction_id": transaction_id,
                "kind": kind,
                "operation_id": operation_id,
                "model_invocation_id": invocation_id,
                "amount": decimal_text(amount),
                "at": self.clock.tick(),
                "reason": reason,
            }
        )

    def _record_usage(
        self,
        *,
        step: dict[str, Any],
        attempt_id: str,
        attempt_no: int,
        partial: dict[str, Any],
        item_key: str | None,
        ordinal: int,
    ) -> str:
        ref = str(step["ai_profile_ref"])
        profile = self.profiles[ref]
        model = profile["model"]
        usage_index = len(self.usage_records) + 1
        operation_id = f"op_{short_digest([self.run_id, step['id'], item_key, ordinal])}"
        invocation_id = f"mi_{short_digest([operation_id, 'invocation'])}"
        cost_amount = Decimal(str(partial.get("cost_amount") or "0"))
        reserve_amount = Decimal(str(partial.get("reserve_amount") or cost_amount))
        if self.budget_reserved + self.budget_settled + reserve_amount > self.budget_limit:
            self.emit(
                "budget.exhausted",
                str(step["id"]),
                {"requested": decimal_text(reserve_amount), "available": decimal_text(self.budget_limit - self.budget_settled - self.budget_reserved)},
                attempt_id=attempt_id,
                attempt_no=attempt_no,
            )
            raise BudgetExceeded(f"Run budget exhausted before {step['id']} invocation")
        self.budget_reserved += reserve_amount
        self._ledger_transaction("reserve", operation_id, reserve_amount, invocation_id, "pre-dispatch estimate")
        self.emit(
            "budget.reserved",
            str(step["id"]),
            {"operation_id": operation_id, "amount": decimal_text(reserve_amount), "currency": self.budget_currency},
            attempt_id=attempt_id,
            attempt_no=attempt_no,
        )
        started_at = self.clock.tick()
        agent_execution_id = partial.get("agent_execution_id")
        provider_units = (partial.get("usage") or {}).get("provider_units") or {}
        agent_turn = provider_units.get("agent_turn")
        invocation_payload = {
            "model_invocation_id": invocation_id,
            "profile_id": profile["profile_id"],
            "item_key": item_key,
        }
        if agent_execution_id is not None:
            invocation_payload["agent_execution_id"] = agent_execution_id
        if agent_turn is not None:
            invocation_payload["agent_turn"] = agent_turn
        self.emit(
            "model.invocation.started",
            str(step["id"]),
            invocation_payload,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
        )
        finished_at = self.clock.tick(2)
        record = {
            "schema_version": "1.2",
            "usage_record_id": f"usage_{self.run_id}_{usage_index:04d}",
            "operation_id": operation_id,
            "operation_idempotency_key": f"{self.run_id}:{step['id']}:{item_key or 'single'}:{ordinal}",
            "observation_idempotency_key": f"{self.run_id}:{step['id']}:{item_key or 'single'}:{ordinal}:observation:1",
            "model_invocation_id": invocation_id,
            "agent_execution_id": agent_execution_id,
            "run_id": self.run_id,
            "step_id": str(step["id"]),
            "step_attempt_id": attempt_id,
            "step_attempt_no": attempt_no,
            "profile_snapshot": {
                "profile_id": str(profile["profile_id"]),
                "version": str(profile["version"]),
                "digest": self.profile_digests[ref],
            },
            "provider": str(model["provider"]),
            "model_id": str(model["model_id"]),
            "model_revision": model.get("model_revision"),
            "deployment_id": model.get("deployment_id"),
            "region": model.get("region"),
            "modality": str(model.get("modality") or "text"),
            "invocation_status": str(partial.get("invocation_status") or "completed"),
            "provider_request_id": f"mock_req_{short_digest([invocation_id, 'provider'])}",
            "usage": copy.deepcopy(partial.get("usage") or {"measurement_status": "unavailable"}),
            "cost": {
                "status": "estimated",
                "amount": decimal_text(cost_amount),
                "currency": self.budget_currency,
                "source": "price_snapshot_calculated",
                "price_snapshot_ref": "mock-pricebook@2026-07-24",
            },
            "started_at": started_at,
            "finished_at": finished_at,
            "recorded_at": self.clock.tick(),
            "supersedes_usage_record_id": None,
        }
        self.usage_records.append(record)
        self.steps[str(step["id"])]["usage_record_ids"].append(record["usage_record_id"])
        completion_payload = {
            "model_invocation_id": invocation_id,
            "usage_record_id": record["usage_record_id"],
            "cost": record["cost"],
        }
        if agent_execution_id is not None:
            completion_payload["agent_execution_id"] = agent_execution_id
        if agent_turn is not None:
            completion_payload["agent_turn"] = agent_turn
        self.emit(
            "model.invocation.completed",
            str(step["id"]),
            completion_payload,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
        )
        self.budget_reserved -= reserve_amount
        self.budget_settled += cost_amount
        self._ledger_transaction("settle", operation_id, cost_amount, invocation_id, "mock invocation completed")
        if reserve_amount > cost_amount:
            self._ledger_transaction("release", operation_id, reserve_amount - cost_amount, invocation_id, "unused reservation")
        self.emit(
            "budget.settled",
            str(step["id"]),
            {"operation_id": operation_id, "settled": decimal_text(cost_amount), "released": decimal_text(max(Decimal('0'), reserve_amount - cost_amount))},
            attempt_id=attempt_id,
            attempt_no=attempt_no,
        )
        return str(record["usage_record_id"])

    def _apply_result(
        self,
        *,
        step: dict[str, Any],
        attempt_id: str,
        attempt_no: int,
        result: dict[str, Any],
        item_values: dict[str, Any] | None = None,
        item_key: str | None = None,
    ) -> tuple[list[str], list[str]]:
        if result.get("status", "completed") != "completed":
            raise DemoContractError(f"Mock tool {step['tool']} returned non-completed status: {result.get('status')}")
        patch = result.get("context_patch") or {}
        denied = sorted(set(patch) - set(step.get("writes") or []))
        if denied:
            raise DemoContractError(f"{step['id']}: mock tool writes undeclared fields: {', '.join(denied)}")
        for key, value in patch.items():
            self.variables[str(key)] = copy.deepcopy(value)
        artifact_ids: list[str] = []
        declared_outputs = [substitute(str(name), item_values or {}) for name in step.get("produces") or []]
        actual_outputs = [substitute(str(name), item_values or {}) for name in (result.get("artifacts") or {})]
        if sorted(declared_outputs) != sorted(actual_outputs):
            raise DemoContractError(f"{step['id']}: mock output drift: declared={declared_outputs}, actual={actual_outputs}")
        for name, payload in (result.get("artifacts") or {}).items():
            artifact_ids.append(
                self._write_artifact(
                    step=step,
                    attempt_id=attempt_id,
                    attempt_no=attempt_no,
                    name=str(name),
                    payload=payload,
                    item_values=item_values,
                )
            )
        usage_ids: list[str] = []
        usage_values = result.get("usage") or []
        if usage_values and not step.get("ai_profile_ref"):
            raise DemoContractError(f"{step['id']}: usage emitted without ai_profile_ref")
        for ordinal, partial in enumerate(usage_values, start=1):
            usage_ids.append(self._record_usage(step=step, attempt_id=attempt_id, attempt_no=attempt_no, partial=partial, item_key=item_key, ordinal=ordinal))
        return artifact_ids, usage_ids

    def _fanout_items(self, fanout: dict[str, Any]) -> list[dict[str, Any]]:
        over = str(fanout["over"])
        artifact_name, separator, pointer = over.partition("#")
        payloads = [payload for name, payload in self.artifact_payloads.items() if artifact_matches(artifact_name, name)]
        if len(payloads) != 1:
            raise DemoContractError(f"fanout source {artifact_name} resolves to {len(payloads)} artifacts")
        value = json_pointer(payloads[0], pointer if separator else "")
        if not isinstance(value, list):
            raise DemoContractError(f"fanout source {over} is not an array")
        if not all(isinstance(item, dict) for item in value):
            raise DemoContractError(f"demo fanout items must be objects: {over}")
        return copy.deepcopy(value)

    def _run_step(self, step: dict[str, Any]) -> None:
        step_id = str(step["id"])
        record = self.steps[step_id]
        tool = self.tool_defs[str(step["tool"])]
        attempt_no = 1
        attempt_id = f"sa_{short_digest([self.run_id, step_id, attempt_no])}"
        input_hash = self._input_snapshot_hash(step)
        attempt = {
            "attempt_id": attempt_id,
            "attempt_no": attempt_no,
            "status": "running",
            "operation_idempotency_key": f"{self.run_id}:{step_id}:{self.input_revision_hash}",
            "input_snapshot_hash": input_hash,
            "started_at": self.clock.tick(),
            "finished_at": None,
            "metrics": {},
            "error": None,
        }
        record["attempts"].append(attempt)
        record["status"] = "running"
        self.emit(
            "step.started",
            step_id,
            {"attempt_id": attempt_id, "tool_id": step["tool"], "tool_version": tool["version"]},
            attempt_id=attempt_id,
            attempt_no=attempt_no,
        )

        if step.get("type") == "confirm":
            gate = step["human_gate"]
            work_item = {
                "work_item_id": f"human_{short_digest([self.run_id, step_id])}",
                "step_id": step_id,
                "status": "open",
                "revision": 0,
                "form": str(gate.get("form") or "confirm"),
                "roles": list(gate.get("roles") or []),
                "input_snapshot_hash": input_hash,
                "created_at": self.clock.tick(),
                "completed_at": None,
                "decision": None,
                "decision_id": None,
                "actor": None,
                "reason": None,
                "expected_revision": 0,
            }
            self.human_tasks.append(work_item)
            record["status"] = "waiting"
            attempt["status"] = "waiting"
            self.emit(
                "human_task.created",
                step_id,
                {"work_item_id": work_item["work_item_id"], "roles": work_item["roles"], "input_snapshot_hash": input_hash},
                attempt_id=attempt_id,
                attempt_no=attempt_no,
            )
            self.emit(
                "step.waiting",
                step_id,
                {"wait_reason": {"kind": "user", "detail": work_item["work_item_id"]}},
                attempt_id=attempt_id,
                attempt_no=attempt_no,
            )
            fixture = (self.case.get("human_decisions") or {}).get(step_id)
            if not isinstance(fixture, dict):
                return
            decision = str(fixture["decision"])
            if decision not in gate.get("allowed_decisions", []):
                raise DemoContractError(f"{step_id}: fixture decision {decision} is not allowed")
            actor = copy.deepcopy(fixture.get("actor") or {})
            if not set(actor.get("roles") or []).intersection(gate.get("roles") or []):
                raise DemoContractError(f"{step_id}: human actor has none of the allowed roles")
            reason = str(fixture.get("reason") or "").strip()
            if gate.get("require_reason") and not reason:
                raise DemoContractError(f"{step_id}: human decision requires a reason")
            expected_revision = int(fixture.get("expected_revision", -1))
            if expected_revision != work_item["revision"]:
                raise DemoContractError(
                    f"{step_id}: human decision revision conflict: "
                    f"expected={expected_revision}, current={work_item['revision']}"
                )
            work_item.update(
                {
                    "status": "completed",
                    "revision": work_item["revision"] + 1,
                    "completed_at": self.clock.tick(),
                    "decision": decision,
                    "decision_id": f"decision_{short_digest([self.run_id, step_id, expected_revision])}",
                    "actor": actor,
                    "reason": reason,
                    "expected_revision": expected_revision,
                }
            )
            self.emit(
                "human_task.completed",
                step_id,
                {
                    "work_item_id": work_item["work_item_id"],
                    "decision_id": work_item["decision_id"],
                    "decision": decision,
                    "reason": work_item["reason"],
                    "expected_revision": work_item["expected_revision"],
                    "revision": work_item["revision"],
                },
                actor=work_item["actor"],
                attempt_id=attempt_id,
                attempt_no=attempt_no,
            )
            record["status"] = "running"
            attempt["status"] = "running"
        elif step.get("type") == "suspend":
            record["status"] = "waiting"
            attempt["status"] = "waiting"
            self.emit(
                "step.waiting",
                step_id,
                {"wait_reason": {"kind": "external_callback", "detail": step["tool"]}},
                attempt_id=attempt_id,
                attempt_no=attempt_no,
            )
            if not isinstance((self.case.get("callbacks") or {}).get(step_id), dict):
                return
            self.emit(
                "callback.received",
                step_id,
                {"callback_id": f"cb_{short_digest([self.run_id, step_id])}"},
                actor={"type": "service", "id": "mock-callback-adapter"},
                attempt_id=attempt_id,
                attempt_no=attempt_no,
            )
            record["status"] = "running"
            attempt["status"] = "running"

        function = self._load_entrypoint(str(tool["entrypoint"]))
        if step.get("fanout"):
            items = self._fanout_items(step["fanout"])
            item_key_field = str(step["fanout"]["item_key"])
            seen_keys: set[str] = set()
            failures = 0
            for item in items:
                item_key = str(nested_value(item, item_key_field))
                if not item_key or item_key in seen_keys:
                    raise DemoContractError(f"{step_id}: fanout item key must be unique and non-empty: {item_key}")
                seen_keys.add(item_key)
                item_attempt_id = f"{attempt_id}:{item_key}"
                item_input_hash = self._input_snapshot_hash(step, item_key)
                values = {**item, "item": item, "item_key": item_key}
                tool_context = ToolContext(self.run_id, step_id, copy.deepcopy(self.variables), self.case, copy.deepcopy(self.artifact_payloads), item=copy.deepcopy(item), item_key=item_key)
                try:
                    result = function(tool_context)
                    artifact_ids, usage_ids = self._apply_result(
                        step=step,
                        attempt_id=item_attempt_id,
                        attempt_no=attempt_no,
                        result=result,
                        item_values=values,
                        item_key=item_key,
                    )
                    item_status = "completed"
                    error = None
                except Exception as exc:  # a demo fixture may intentionally exercise fanout isolation
                    failures += 1
                    artifact_ids, usage_ids = [], []
                    item_status = "failed"
                    error = {"code": "MOCK_ITEM_FAILED", "message": str(exc)}
                record["fanout_items"].append(
                    {
                        "item_key": item_key,
                        "status": item_status,
                        "attempt_id": item_attempt_id,
                        "attempt_no": attempt_no,
                        "input_snapshot_hash": item_input_hash,
                        "artifact_ids": artifact_ids,
                        "usage_record_ids": usage_ids,
                        "error": error,
                    }
                )
                self.emit(
                    f"fanout.item.{item_status}",
                    step_id,
                    {"item_key": item_key, "attempt_id": item_attempt_id},
                    attempt_id=item_attempt_id,
                    attempt_no=attempt_no,
                )
            policy = step["fanout"].get("failure_policy", "fail_fast")
            tolerated = 0
            if isinstance(policy, dict):
                tolerated = int(policy.get("max_failed_items") or 0)
                if "max_failed_percent" in policy:
                    tolerated = max(tolerated, int(len(items) * float(policy["max_failed_percent"]) / 100.0))
            if failures > tolerated:
                raise DemoContractError(f"{step_id}: {failures} fanout items failed; tolerated={tolerated}")
        else:
            context = ToolContext(self.run_id, step_id, copy.deepcopy(self.variables), self.case, copy.deepcopy(self.artifact_payloads))
            result = function(context)
            self._apply_result(
                step=step,
                attempt_id=attempt_id,
                attempt_no=attempt_no,
                result=result,
            )

        attempt["status"] = "completed"
        attempt["finished_at"] = self.clock.tick()
        attempt["metrics"] = {
            "artifact_count": len(record["artifact_ids"]),
            "usage_record_count": len(record["usage_record_ids"]),
            "fanout_item_count": len(record["fanout_items"]),
        }
        record["status"] = "completed"
        self.emit(
            "step.completed",
            step_id,
            {"attempt_id": attempt_id, **attempt["metrics"]},
            attempt_id=attempt_id,
            attempt_no=attempt_no,
        )

    def _schedule(self) -> None:
        self.emit("run.started", None, {"process_id": self.process["process_id"], "version": self.process["version"], "input_revision_hash": self.input_revision_hash})
        remaining = set(self.steps)
        while remaining:
            progressed = False
            for step in self.process.get("steps") or []:
                step_id = str(step["id"])
                if step_id not in remaining:
                    continue
                state, detail = self._guard(step.get("when"))
                if state == "false":
                    self._mark_skipped(step_id, "when_false", f"guard evaluated false ({detail!r})")
                    remaining.remove(step_id)
                    progressed = True
                    continue
                if state == "missing":
                    continue

                dependencies = [self._dependency_state(dep) for dep in step.get("depends_on") or []]
                if any(impossible for met, impossible in dependencies if not met):
                    self._mark_skipped(step_id, "dependency_outcome", "all acceptable upstream outcomes are terminal and unreachable")
                    remaining.remove(step_id)
                    progressed = True
                    continue
                if any(not met for met, _ in dependencies):
                    continue

                consume_wait = False
                consume_unreachable = False
                for declared in step.get("consumes") or []:
                    if self._artifacts_for(str(declared)):
                        continue
                    producers = self._producer_steps(str(declared))
                    if not producers or any(producer["status"] not in TERMINAL_STEP_STATUSES for producer in producers):
                        consume_wait = True
                    elif all(producer["status"] == "skipped" for producer in producers):
                        consume_unreachable = True
                    else:
                        raise DemoContractError(f"{step_id}: producer completed without required artifact {declared}")
                if consume_unreachable:
                    self._mark_skipped(step_id, "artifact_unreachable", "required artifact belongs to a closed branch")
                    remaining.remove(step_id)
                    progressed = True
                    continue
                if consume_wait:
                    continue

                read_wait = False
                read_unreachable = False
                for variable in step.get("reads") or []:
                    if variable in self.variables:
                        continue
                    writers = self._writer_steps(str(variable))
                    if not writers or any(writer["status"] not in TERMINAL_STEP_STATUSES for writer in writers):
                        read_wait = True
                    elif all(writer["status"] == "skipped" for writer in writers):
                        read_unreachable = True
                    else:
                        raise DemoContractError(f"{step_id}: writer completed without required context field {variable}")
                if read_unreachable:
                    self._mark_skipped(step_id, "variable_unreachable", "required context belongs to a closed branch")
                    remaining.remove(step_id)
                    progressed = True
                    continue
                if read_wait:
                    continue

                self._run_step(step)
                if self.steps[step_id]["status"] in TERMINAL_STEP_STATUSES:
                    remaining.remove(step_id)
                progressed = True
            if not progressed:
                waiting = {
                    step_id: {
                        "status": self.steps[step_id]["status"],
                        "reads": self.step_defs[step_id].get("reads") or [],
                        "consumes": self.step_defs[step_id].get("consumes") or [],
                    }
                    for step_id in sorted(remaining)
                }
                raise DemoContractError(f"Run made no progress; unresolved steps: {waiting}")

    def _resolve_outcome(self) -> str:
        matched: list[dict[str, Any]] = []
        for outcome in self.process["completion"]["outcomes"]:
            state, _ = self._guard(outcome.get("when"))
            if state == "true":
                matched.append(outcome)
        if len(matched) != 1:
            raise DemoContractError(f"exactly_one_outcome expected, matched {[value.get('id') for value in matched]}")
        outcome = matched[0]
        missing_steps = [step_id for step_id in outcome["terminal_steps"] if self.steps[step_id]["status"] != "completed"]
        missing_artifacts = [name for name in outcome["required_artifacts"] if not self._artifacts_for(str(name))]
        if missing_steps or missing_artifacts:
            raise DemoContractError(f"outcome {outcome['id']} is incomplete: steps={missing_steps}, artifacts={missing_artifacts}")
        return str(outcome["id"])

    def _definition_snapshot(self) -> dict[str, Any]:
        return {
            "process_id": str(self.process["process_id"]),
            "version": str(self.process["version"]),
            "digest": digest(self.process),
            "tool_registry_id": str(self.registry["registry_id"]),
            "tool_registry_version": str(self.registry["version"]),
            "tool_registry_digest": digest(self.registry),
            "profiles": [
                {"profile_id": str(profile["profile_id"]), "version": str(profile["version"]), "digest": self.profile_digests[ref]}
                for ref, profile in sorted(self.profiles.items())
            ],
        }

    def _physical_step_dir(self, step_id: str) -> str:
        for index, step in enumerate(self.process.get("steps") or [], start=1):
            if str(step["id"]) == step_id:
                return f"S{index}_{safe_path_part(str(step['tool']))}"
        raise DemoContractError(f"Unknown step while building storage index: {step_id}")

    @staticmethod
    def _attempt_dir(attempt: dict[str, Any]) -> str:
        return f"A{int(attempt['attempt_no']):04d}_{safe_path_part(str(attempt['attempt_id']))}"

    def _diagnostic_record(
        self,
        *,
        timestamp: str,
        sequence: int,
        level: str,
        channel: str,
        message: str,
        step_id: str | None,
        attempt: dict[str, Any] | None,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "timestamp": timestamp,
            "sequence": sequence,
            "level": level,
            "channel": channel,
            "message": message,
            "session_id": str(self.case["session_id"]),
            "task_id": str(self.case["task_id"]),
            "run_id": self.run_id,
            "step_id": step_id,
            "step_attempt_id": str(attempt["attempt_id"]) if attempt else None,
            "step_attempt_no": int(attempt["attempt_no"]) if attempt else None,
            "tool_id": str(self.step_defs[step_id]["tool"]) if step_id else None,
            "correlation_id": self.run_id,
            "visibility": "internal",
            "sensitivity": "normal",
            "fields": fields,
        }

    def _write_diagnostic_files(self, run_record: dict[str, Any]) -> list[dict[str, Any]]:
        """Materialize mock diagnostics in the portable bundle, not OpenCrew's live workspace."""

        references: list[dict[str, Any]] = []
        runtime_records = [
            self._diagnostic_record(
                timestamp=str(run_record["started_at"]),
                sequence=1,
                level="info",
                channel="runtime",
                message="deterministic mock Run started",
                step_id=None,
                attempt=None,
                fields={"process_id": self.process["process_id"], "mock": True},
            ),
            self._diagnostic_record(
                timestamp=str(run_record["finished_at"]),
                sequence=2,
                level="info",
                channel="runtime",
                message="deterministic mock Run completed",
                step_id=None,
                attempt=None,
                fields={"status": run_record["status"], "outcome": run_record["outcome"], "mock": True},
            ),
        ]
        runtime_path = self.run_dir / "logs" / "runtime.ndjson"
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in runtime_records),
            encoding="utf-8",
        )
        references.append({"path": "logs/runtime.ndjson", "owner_type": "run", "owner_id": self.run_id})

        for step_record in run_record["steps"]:
            step_id = str(step_record["step_id"])
            for attempt in step_record["attempts"]:
                attempt_dir = self._attempt_dir(attempt)
                relative_dir = Path("logs") / safe_path_part(step_id) / attempt_dir
                report_dir = self.run_dir / relative_dir
                report_dir.mkdir(parents=True, exist_ok=True)
                diagnostics = [
                    self._diagnostic_record(
                        timestamp=str(attempt["started_at"]),
                        sequence=1,
                        level="info",
                        channel="tool",
                        message="mock Tool Attempt started",
                        step_id=step_id,
                        attempt=attempt,
                        fields={"input_snapshot_hash": attempt["input_snapshot_hash"], "mock": True},
                    ),
                    self._diagnostic_record(
                        timestamp=str(attempt["finished_at"] or attempt["started_at"]),
                        sequence=2,
                        level="info" if attempt["status"] == "completed" else "warning",
                        channel="tool",
                        message=f"mock Tool Attempt {attempt['status']}",
                        step_id=step_id,
                        attempt=attempt,
                        fields={"status": attempt["status"], "metrics": attempt.get("metrics") or {}, "mock": True},
                    ),
                ]
                diagnostic_path = report_dir / "diagnostic.ndjson"
                diagnostic_path.write_text(
                    "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in diagnostics),
                    encoding="utf-8",
                )
                stdout_payload = {
                    "mock": True,
                    "step_id": step_id,
                    "attempt_id": attempt["attempt_id"],
                    "status": attempt["status"],
                    "note": "No model or external service was called.",
                }
                (report_dir / "stdout.log").write_text(
                    json.dumps(stdout_payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                (report_dir / "stderr.log").write_text("", encoding="utf-8")
                references.extend(
                    {
                        "path": (relative_dir / filename).as_posix(),
                        "owner_type": "step_attempt",
                        "owner_id": str(attempt["attempt_id"]),
                    }
                    for filename in ("diagnostic.ndjson", "stdout.log", "stderr.log")
                )
        return references

    def _storage_index(self, run_record: dict[str, Any], diagnostic_refs: list[dict[str, Any]]) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []

        def add_entry(
            *,
            owner_type: str,
            owner_id: str,
            purpose: str,
            authority: str,
            base: str,
            locator: str,
            materialization: str,
            description: str,
            retention_class: str | None = None,
            actual_path: str | None = None,
        ) -> None:
            raw: bytes | None = None
            if actual_path is not None:
                raw = (self.run_dir / actual_path).read_bytes()
            identity = [owner_type, owner_id, purpose, base, locator, len(entries)]
            entries.append(
                {
                    "entry_id": f"loc_{short_digest(identity)}",
                    "owner_type": owner_type,
                    "owner_id": owner_id,
                    "purpose": purpose,
                    "authority": authority,
                    "base": base,
                    "locator": locator,
                    "materialization": materialization,
                    "description": description,
                    "sha256": f"sha256:{hashlib.sha256(raw).hexdigest()}" if raw is not None else None,
                    "size": len(raw) if raw is not None else None,
                    "retention_class": retention_class,
                }
            )

        add_entry(
            owner_type="task", owner_id=str(run_record["task_id"]), purpose="business_record",
            authority="database", base="database", locator=f"task:{run_record['task_id']}",
            materialization="external", description="Task is a database record that references the paired session Workspace; it has no duplicate file root.",
            retention_class="business_record",
        )
        add_entry(
            owner_type="session", owner_id=str(run_record["session_id"]), purpose="input",
            authority="file", base="workspace", locator="inbox/",
            materialization="layout_contract", description="Recommended immutable session input area; historical domain paths remain compatible.",
            retention_class="business_input",
        )
        add_entry(
            owner_type="run", owner_id=self.run_id, purpose="input_manifest",
            authority="file", base="run", locator="0_SessionContext/InputManifest.json",
            materialization="layout_contract", description="Live OpenCrew-compatible Run input snapshot; the portable demo keeps source fixtures beside the Process.",
            retention_class="run_evidence",
        )
        add_entry(
            owner_type="session", owner_id=str(run_record["session_id"]), purpose="audit_event",
            authority="database", base="database", locator="session_events",
            materialization="external", description="Durable audit events live in the database; events.ndjson is a portable evidence projection.",
            retention_class="audit",
        )
        add_entry(
            owner_type="run", owner_id=self.run_id, purpose="state_snapshot",
            authority="database", base="database", locator=f"workflow_run:{self.run_id}",
            materialization="external", description="Target query authority for Run/Step state; run.json is the portable demo snapshot, not evidence that this target repository exists in current OpenCrew.",
            retention_class="run_state",
        )
        add_entry(
            owner_type="run", owner_id=self.run_id, purpose="usage",
            authority="database", base="database", locator=f"usage_ledger:{self.run_id}",
            materialization="external", description="Target Usage/Cost ledger authority; current OpenCrew maps only part of this contract through local_usage_log.",
            retention_class="financial_audit",
        )
        add_entry(
            owner_type="service", owner_id="flowspec-runtime", purpose="service_log",
            authority="log_service", base="platform", locator="configured-service-log-sink",
            materialization="external", description="API/worker/scheduler process logs are deployment-owned and must stay outside the session Workspace.",
            retention_class="operations",
        )

        core_files = [
            ("run.json", "state_snapshot"),
            ("events.ndjson", "audit_event"),
            ("usage.json", "usage"),
            ("budget-ledger.json", "budget"),
            ("definition/process.snapshot.json", "definition"),
            ("definition/tool-registry.snapshot.json", "definition"),
        ]
        core_files.extend(
            (f"definition/profiles/{Path(ref).name}", "definition")
            for ref in sorted(self.profiles)
        )
        for relative_path, purpose in core_files:
            add_entry(
                owner_type="run", owner_id=self.run_id, purpose=purpose,
                authority="file", base="run_bundle", locator=relative_path,
                materialization="materialized", description="Portable demo evidence file; live deployments may use database or Workspace bindings.",
                retention_class="run_evidence", actual_path=relative_path,
            )

        for ref in diagnostic_refs:
            add_entry(
                owner_type=str(ref["owner_type"]), owner_id=str(ref["owner_id"]), purpose="diagnostic_log",
                authority="file", base="run_bundle", locator=str(ref["path"]),
                materialization="materialized", description="Mock diagnostic evidence in this portable bundle; it does not write to an OpenCrew Workspace.",
                retention_class="diagnostic", actual_path=str(ref["path"]),
            )

        for step_record in run_record["steps"]:
            step_id = str(step_record["step_id"])
            step_dir = self._physical_step_dir(step_id)
            for attempt in step_record["attempts"]:
                attempt_id = str(attempt["attempt_id"])
                attempt_dir = self._attempt_dir(attempt)
                layout_entries = [
                    ("working", f"{step_dir}/Working/Attempts/{attempt_dir}/", "Attempt-private temporary files; safe to clean by policy."),
                    ("diagnostic_log", f"{step_dir}/Report/Attempts/{attempt_dir}/", "OpenCrew-compatible target location for per-Attempt diagnostics."),
                    ("prompt", f"{step_dir}/Prompt/Attempts/{attempt_dir}/", "Access-controlled prompt or Agent transcript evidence."),
                    ("output_staging", f"{step_dir}/Output/.staging/{attempt_dir}/", "Unpublished output; only finalize may promote it."),
                    ("output_manifest", f"{step_dir}/Output/OutputManifest.json", "Canonical published Step manifest remains in the existing Output directory."),
                ]
                for purpose, locator, description in layout_entries:
                    add_entry(
                        owner_type="step_attempt" if purpose != "output_manifest" else "step",
                        owner_id=attempt_id if purpose != "output_manifest" else step_id,
                        purpose=purpose, authority="file", base="run", locator=locator,
                        materialization="layout_contract", description=description,
                        retention_class="temporary" if purpose in {"working", "output_staging"} else "run_evidence",
                    )

        artifacts_by_id = {artifact["artifact_id"]: artifact for artifact in run_record["artifacts"]}
        for artifact in artifacts_by_id.values():
            add_entry(
                owner_type="artifact", owner_id=str(artifact["artifact_id"]), purpose="published_artifact",
                authority="file", base="run_bundle", locator=str(artifact["path"]),
                materialization="materialized", description="Mock Artifact bytes in the portable evidence bundle.",
                retention_class="business_artifact", actual_path=str(artifact["path"]),
            )
            step_dir = self._physical_step_dir(str(artifact["producer"]["step_id"]))
            add_entry(
                owner_type="artifact", owner_id=str(artifact["artifact_id"]), purpose="published_artifact",
                authority="file", base="run", locator=f"{step_dir}/Output/{safe_path_part(str(artifact['name']))}",
                materialization="layout_contract", description="OpenCrew-compatible live publication location; canonical bytes remain under the producing Step Output directory.",
                retention_class="business_artifact",
            )

        add_entry(
            owner_type="run", owner_id=self.run_id, purpose="output_projection",
            authority="projection", base="run", locator="SessionOutput/",
            materialization="projection", description="User-facing catalog/projection; rebuildable from canonical Step Output manifests.",
            retention_class="projection",
        )
        return {
            "schema_version": "1.0",
            "index_id": f"storage_{self.run_id}",
            "layout_profile": "opencrew-compatible-v1",
            "bundle_kind": "portable_evidence",
            "session_id": str(run_record["session_id"]),
            "task_id": str(run_record["task_id"]),
            "run_id": self.run_id,
            "path_bases": [
                {"base": "workspace", "resolved_by": "storage_resolver", "example": "$OPENCREW_DATA_DIR/sessions/<session_ref>/workspace"},
                {"base": "run", "resolved_by": "run_record", "example": "tool_use_sessions/<run_id>"},
                {"base": "step", "resolved_by": "step_directory_map", "example": "S{index}_{tool}"},
                {"base": "step_attempt", "resolved_by": "attempt_record", "example": "Report/Attempts/A{no}_{attempt_id}"},
                {"base": "run_bundle", "resolved_by": "bundle_reader", "example": "."},
                {"base": "database", "resolved_by": "database_repository", "example": "session_events / task record / usage ledger"},
                {"base": "platform", "resolved_by": "deployment", "example": "configured process log sink outside Workspace"},
                {"base": "external", "resolved_by": "uri_scheme", "example": "s3://bucket/key?versionId=..."},
            ],
            "entries": entries,
        }

    def _write_outputs(self, run_record: dict[str, Any], ledger: dict[str, Any]) -> None:
        definition_dir = self.run_dir / "definition"
        write_json(definition_dir / "process.snapshot.json", self.process)
        write_json(definition_dir / "tool-registry.snapshot.json", self.registry)
        for ref, profile in self.profiles.items():
            write_json(definition_dir / "profiles" / Path(ref).name, profile)
        write_json(self.run_dir / "run.json", run_record)
        write_json(self.run_dir / "usage.json", self.usage_records)
        write_json(self.run_dir / "budget-ledger.json", ledger)
        events_text = "".join(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n" for event in self.events)
        (self.run_dir / "events.ndjson").write_text(events_text, encoding="utf-8")
        diagnostic_refs = self._write_diagnostic_files(run_record)
        write_json(self.run_dir / "storage-index.json", self._storage_index(run_record, diagnostic_refs))

    def run(self) -> dict[str, Any]:
        if self.run_dir.exists():
            if self.run_dir.parent.name != "runs" and not self.run_dir.parent.name.startswith("flowspec-demo-test"):
                raise DemoContractError(f"Refusing to replace unexpected run directory: {self.run_dir}")
            shutil.rmtree(self.run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._schedule()
            outcome = self._resolve_outcome()
            status = "completed"
            self.emit("run.completed", None, {"outcome": outcome})
        except Exception as exc:
            status = "failed"
            outcome = None
            self.emit("run.failed", None, {"error": {"type": type(exc).__name__, "message": str(exc)}})
            raise
        finally:
            finished_at = self.clock.tick()

        available = self.budget_limit - self.budget_settled - self.budget_reserved
        ledger = {
            "schema_version": "1.1",
            "ledger_id": f"budget_{self.run_id}",
            "run_id": self.run_id,
            "currency": self.budget_currency,
            "limit": decimal_text(self.budget_limit),
            "reserved": decimal_text(self.budget_reserved),
            "settled": decimal_text(self.budget_settled),
            "available": decimal_text(max(Decimal("0"), available)),
            "status": "within_budget" if available >= 0 else "exhausted",
            "transactions": self.ledger_transactions,
        }
        run_record = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "session_id": str(self.case["session_id"]),
            "task_id": str(self.case["task_id"]),
            "run_sequence": int(self.case["run_sequence"]),
            "supersedes_run_id": self.case.get("supersedes_run_id"),
            "process_snapshot": self._definition_snapshot(),
            "definition_snapshot_ref": "definition/process.snapshot.json",
            "storage_index_ref": "storage-index.json",
            "input_revision_hash": self.input_revision_hash,
            "status": status,
            "outcome": outcome,
            "started_at": str(self.case["started_at"]),
            "finished_at": finished_at,
            "context": self.variables,
            "steps": [self.steps[str(step["id"])] for step in self.process.get("steps") or []],
            "artifacts": self.artifact_records,
            "usage_records": self.usage_records,
            "human_tasks": self.human_tasks,
            "events": self.events,
            "budget_summary": {
                "currency": self.budget_currency,
                "limit": ledger["limit"],
                "reserved": ledger["reserved"],
                "settled": ledger["settled"],
                "available": ledger["available"],
                "status": ledger["status"],
                "ledger_ref": "budget-ledger.json",
            },
            "warnings": self.warnings,
        }
        self._write_outputs(run_record, ledger)
        return run_record


def build_scenario(scenario_dir: Path, output_parent: Path | None = None) -> list[dict[str, Any]]:
    scenario_dir = scenario_dir.resolve()
    cases = load_json(scenario_dir / "cases.json")
    if not isinstance(cases, list) or not cases:
        raise DemoContractError(f"{scenario_dir}/cases.json must be a non-empty array")
    destination = output_parent.resolve() if output_parent else scenario_dir / "runs"
    destination.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for case in cases:
        if not isinstance(case, dict):
            raise DemoContractError("each demo case must be an object")
        if previous and case.get("supersedes_run_id") == previous.get("run_id"):
            if case.get("session_id") != previous.get("session_id") or case.get("task_id") != previous.get("task_id"):
                raise DemoContractError("superseding Runs must remain in the same session/task business instance")
        record = ScenarioRuntime(scenario_dir, case, destination).run()
        records.append(record)
        previous = case
    return records


def build_all() -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for scenario_dir in sorted(path for path in DEMO_ROOT.iterdir() if path.is_dir() and (path / "process.json").exists()):
        result[scenario_dir.name] = build_scenario(scenario_dir)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic FlowSpec demo run records")
    parser.add_argument("scenario", nargs="?", help="Scenario directory name; omit to build all")
    args = parser.parse_args()
    if args.scenario:
        build_scenario(DEMO_ROOT / args.scenario)
    else:
        build_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
