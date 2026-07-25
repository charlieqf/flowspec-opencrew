"""Lightweight semantic checks for the proposed FlowSpec Process contract.

JSON Schema validates the shape of one object.  This linter deliberately keeps
the cross-step checks small: identity, references, DAG topology, artifacts,
resource pools, and the effective retry policy after defaults are applied.
It is documentation tooling; OpenCrew's runtime does not consume it yet.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Iterable


_TEMPLATE_PART = re.compile(r"\{[^{}]+\}")


def _dependency_ids(dependency: dict[str, Any]) -> Iterable[str]:
    if "any_of" in dependency:
        for child in dependency.get("any_of") or []:
            if isinstance(child, dict):
                yield from _dependency_ids(child)
        return
    step_id = dependency.get("step_id")
    if isinstance(step_id, str) and step_id:
        yield step_id


def _artifact_pattern(value: str) -> str:
    return _TEMPLATE_PART.sub("*", value)


def _artifact_matches(produced: str, consumed: str) -> bool:
    produced_pattern = _artifact_pattern(produced)
    consumed_pattern = _artifact_pattern(consumed)
    return (
        produced_pattern == consumed_pattern
        or fnmatch.fnmatchcase(produced, consumed_pattern)
        or fnmatch.fnmatchcase(consumed, produced_pattern)
    )


def lint_process(process: dict[str, Any]) -> list[str]:
    """Return deterministic, human-readable semantic errors."""
    errors: list[str] = []
    steps = [item for item in process.get("steps") or [] if isinstance(item, dict)]

    ids = [str(step.get("id") or "") for step in steps]
    seen: set[str] = set()
    for step_id in ids:
        if step_id in seen:
            errors.append(f"duplicate step id: {step_id}")
        seen.add(step_id)
    known_ids = set(ids)

    context_writers: dict[str, list[str]] = {}
    for step in steps:
        step_id = str(step.get("id") or "")
        for field in step.get("writes") or []:
            context_writers.setdefault(str(field), []).append(step_id)
    for field, writer_ids in sorted(context_writers.items()):
        unique_writers = sorted(set(writer_ids))
        if len(unique_writers) > 1:
            errors.append(
                f"context field {field} has multiple writers without a reducer/CAS contract: "
                f"{', '.join(unique_writers)}"
            )

    graph: dict[str, set[str]] = {step_id: set() for step_id in ids}
    for step in steps:
        step_id = str(step.get("id") or "")
        for dependency in step.get("depends_on") or []:
            if not isinstance(dependency, dict):
                continue
            for upstream_id in _dependency_ids(dependency):
                if upstream_id not in known_ids:
                    errors.append(f"{step_id}: unknown dependency step: {upstream_id}")
                else:
                    graph[step_id].add(upstream_id)

    for sla in process.get("sla") or []:
        if not isinstance(sla, dict):
            continue
        for step_id in sla.get("critical_path_override") or []:
            if step_id not in known_ids:
                errors.append(f"SLA critical_path_override references unknown step: {step_id}")

    produced = [
        (str(step.get("id") or ""), str(artifact))
        for step in steps
        for artifact in step.get("produces") or []
    ]
    for step in steps:
        step_id = str(step.get("id") or "")
        for consumed in step.get("consumes") or []:
            consumed_name = str(consumed)
            producer_ids = {
                producer_id
                for producer_id, artifact in produced
                if producer_id != step_id and _artifact_matches(artifact, consumed_name)
            }
            if not producer_ids:
                errors.append(f"{step_id}: consumed artifact has no producer: {consumed_name}")
            else:
                if len(producer_ids) > 1:
                    errors.append(
                        f"{step_id}: consumed artifact has multiple producers: "
                        f"{consumed_name} <- {', '.join(sorted(producer_ids))}"
                    )
                graph[step_id].update(producer_ids)

    if process.get("contract_level") == "executable":
        contracts = process.get("artifact_contracts") or {}
        for step in steps:
            step_id = str(step.get("id") or "")
            for artifact in [*(step.get("consumes") or []), *(step.get("produces") or [])]:
                matches = [
                    pattern
                    for pattern in contracts
                    if _artifact_matches(str(pattern), str(artifact))
                ]
                if len(matches) != 1:
                    errors.append(
                        f"{step_id}: executable artifact {artifact} resolves to "
                        f"{len(matches)} contracts"
                    )

            fanout = step.get("fanout")
            if isinstance(fanout, dict):
                source = str(fanout.get("over") or "").partition("#")[0]
                if not any(
                    _artifact_matches(str(consumed), source)
                    for consumed in step.get("consumes") or []
                ):
                    errors.append(
                        f"{step_id}: fanout source must be declared in consumes: {source}"
                    )
                item_key = str(fanout.get("item_key") or "")
                if not item_key:
                    errors.append(f"{step_id}: executable fanout requires item_key")
                elif not any(
                    "{" + item_key + "}" in str(name)
                    for name in step.get("produces") or []
                ):
                    errors.append(
                        f"{step_id}: fanout output must bind stable item_key {{{item_key}}}"
                    )
                if not isinstance(fanout.get("max_concurrency"), int):
                    errors.append(f"{step_id}: executable fanout requires max_concurrency")
                if "failure_policy" not in fanout:
                    errors.append(f"{step_id}: executable fanout requires failure_policy")
                reduce = fanout.get("reduce")
                if not isinstance(reduce, dict) or not reduce.get("order_by"):
                    errors.append(f"{step_id}: executable fanout requires deterministic reduce.order_by")
                elif reduce.get("order_by") != item_key:
                    errors.append(
                        f"{step_id}: reduce.order_by must equal item_key for stable ordering"
                    )
                if not isinstance(reduce, dict) or reduce.get("require_binding_key") is not True:
                    errors.append(
                        f"{step_id}: executable fanout must require artifact binding keys"
                    )
                elif item_key:
                    for output in step.get("produces") or []:
                        matching_contracts = [
                            contract
                            for pattern, contract in contracts.items()
                            if _artifact_matches(str(pattern), str(output))
                        ]
                        if (
                            len(matching_contracts) == 1
                            and item_key not in (matching_contracts[0].get("binding_keys") or [])
                        ):
                            errors.append(
                                f"{step_id}: fanout output {output} contract must bind item key {item_key}"
                            )

            if step.get("type") == "confirm":
                gate = step.get("human_gate")
                if not isinstance(gate, dict):
                    errors.append(f"{step_id}: confirm step requires human_gate")
                else:
                    if not gate.get("roles"):
                        errors.append(f"{step_id}: human task requires at least one authorized role")
                    if not gate.get("allowed_decisions"):
                        errors.append(f"{step_id}: human task requires allowed_decisions")
                    if gate.get("bind_to_input_hash") is not True:
                        errors.append(f"{step_id}: human task must bind to input hash")
                    if gate.get("require_reason") is not True:
                        errors.append(f"{step_id}: human task must require a decision reason")

    # Even illustrative definitions must not create an unassignable confirm
    # work item. Executable definitions add the stronger decision/CAS checks above.
    for step in steps:
        if step.get("type") != "confirm" or process.get("contract_level") == "executable":
            continue
        gate = step.get("human_gate")
        if isinstance(gate, dict) and not gate.get("roles"):
            errors.append(
                f"{step.get('id')}: human task requires at least one authorized role"
            )

        completion = process.get("completion") or {}
        outcome_ids: set[str] = set()
        guard_signatures: set[str] = set()
        for outcome in completion.get("outcomes") or []:
            if not isinstance(outcome, dict):
                continue
            outcome_id = str(outcome.get("id") or "")
            if outcome_id in outcome_ids:
                errors.append(f"duplicate completion outcome id: {outcome_id}")
            outcome_ids.add(outcome_id)
            signature = json.dumps(outcome.get("when"), sort_keys=True, separators=(",", ":"))
            if signature in guard_signatures:
                errors.append(f"duplicate completion outcome guard: {signature}")
            guard_signatures.add(signature)
            for step_id in outcome.get("terminal_steps") or []:
                if step_id not in known_ids:
                    errors.append(
                        f"completion outcome {outcome_id} references unknown step: {step_id}"
                    )
            for artifact in outcome.get("required_artifacts") or []:
                contract_matches = [
                    pattern
                    for pattern in contracts
                    if _artifact_matches(str(pattern), str(artifact))
                ]
                if len(contract_matches) != 1:
                    errors.append(
                        f"completion outcome {outcome_id}: artifact {artifact} resolves "
                        f"to {len(contract_matches)} contracts"
                    )
                if not any(
                    _artifact_matches(produced_name, str(artifact))
                    for _, produced_name in produced
                ):
                    errors.append(
                        f"completion outcome {outcome_id}: required artifact has no producer: "
                        f"{artifact}"
                    )

    outcomes = (process.get("completion") or {}).get("outcomes") or []
    if len(outcomes) > 1 and any(
        isinstance(outcome, dict) and not outcome.get("when") for outcome in outcomes
    ):
        errors.append("multiple completion outcomes cannot include an unguarded catch-all")
    finite_outcome_guards: dict[str, list[tuple[str, set[str]]]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        guard = outcome.get("when")
        if not isinstance(guard, dict):
            continue
        values: list[Any] | None = None
        if "equals" in guard:
            values = [guard["equals"]]
        elif "in" in guard:
            values = list(guard["in"])
        if values is None:
            continue
        encoded = {
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for value in values
        }
        variable = str(guard.get("variable") or "")
        outcome_id = str(outcome.get("id") or "")
        for previous_id, previous_values in finite_outcome_guards.setdefault(variable, []):
            overlap = sorted(previous_values & encoded)
            if overlap:
                errors.append(
                    f"completion outcomes {previous_id} and {outcome_id} overlap on "
                    f"{variable}: {', '.join(overlap)}"
                )
        finite_outcome_guards[variable].append((outcome_id, encoded))

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str, trail: list[str]) -> None:
        if step_id in visiting:
            start = trail.index(step_id)
            errors.append(f"dependency cycle: {' -> '.join([*trail[start:], step_id])}")
            return
        if step_id in visited:
            return
        visiting.add(step_id)
        for upstream_id in sorted(graph.get(step_id) or []):
            visit(upstream_id, [*trail, step_id])
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in sorted(graph):
        visit(step_id, [])

    pools = process.get("resource_pools") or {}
    for step in steps:
        step_id = str(step.get("id") or "")
        for resource in step.get("resources") or []:
            if not isinstance(resource, dict) or resource.get("kind") == "mutex":
                continue
            pool_name = str(resource.get("pool") or "")
            pool = pools.get(pool_name) if isinstance(pools, dict) else None
            if not isinstance(pool, dict):
                errors.append(f"{step_id}: unknown resource pool: {pool_name}")
            elif pool.get("type") != resource.get("kind"):
                errors.append(
                    f"{step_id}: resource kind {resource.get('kind')} does not match "
                    f"pool {pool_name} type {pool.get('type')}"
                )

    defaults = process.get("defaults") or {}
    default_retry = defaults.get("retry") if isinstance(defaults, dict) else None
    for step in steps:
        step_id = str(step.get("id") or "")
        retry = step.get("retry", default_retry)
        if not isinstance(retry, dict) or retry.get("policy") != "on_transient":
            continue
        if step.get("side_effect_class") not in {"pure", "idempotent"}:
            errors.append(
                f"{step_id}: on_transient is unsafe for "
                f"side_effect_class={step.get('side_effect_class')}"
            )
        if "max_attempts" not in retry:
            errors.append(f"{step_id}: on_transient requires max_attempts")

    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint one proposed FlowSpec Process JSON file")
    parser.add_argument("process", type=Path)
    args = parser.parse_args()
    process = json.loads(args.process.read_text(encoding="utf-8"))
    errors = lint_process(process)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
