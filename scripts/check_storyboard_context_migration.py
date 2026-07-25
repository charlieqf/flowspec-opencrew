#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STORYBOARD_ROOT = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard"
SNAPSHOT_PATH = REPO_ROOT / "scripts" / "storyboard_context_injected_names.json"

# Step 1 fills this as modules migrate (Phase R/S/F).
MIGRATED_FILES: tuple[str, ...] = (
    "services.py",
    "asset_search_services.py",
    "clean_image_services.py",
    "asset_video_generation_services.py",
    "composer_services.py",
    "asset_history_services.py",
    "agent_chat_services.py",
    "provider_services.py",
    "asset_reference_services.py",
    "media_tts_provider_services.py",
    "video_plan_artifact_services.py",
    "builder_state_services.py",
    "video_plan_signature_services.py",
    "host_product_services.py",
    "video_plan_load_services.py",
    "tts_workflow_services.py",
    "asset_core_services.py",
    "asset_pool_services.py",
    "working_asset_services.py",
    "working_reset_services.py",
    "video_plan_execution_state_services.py",
    "value_services.py",
    "asset_routes.py",
    "composer_routes.py",
    "video_plan_routes.py",
    "agent_chat_routes.py",
    "video_only_plan_routes.py",
    "tts_routes.py",
    "image_plan_routes.py",
    "host_product_routes.py",
    "task_routes.py",
    "hyperframe_template_routes.py",
    "asset_search_routes.py",
    "clean_image_routes.py",
)


def _load_snapshot() -> dict:
    # Full injected-name universe from the committed snapshot (regenerate with
    # scripts/storyboard_step1_inventory.py --write; freshness is enforced by
    # test_koubo_storyboard_step1_inventory_contract.py in the backend CI job).
    # Static + committed => reproducible, and complete from day 1 instead of an
    # incrementally-built union that is only whole at Phase F.
    if not SNAPSHOT_PATH.exists():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


_SNAPSHOT = _load_snapshot()
# Context FIELDS are the injection surface (overridable per StoryboardContext
# instance). Several of them (read_json, write_json, safe_workspace_rel,
# analysis_tool_env, redact_payload, redact_secret_text) shadow module-level
# imports in service modules; the old globals().update(vars(ns)) OVERWROTE
# those import bindings, so a bare call honored the context override. Migrated
# code must therefore access fields through _sc./deps. EVEN when an import of
# the same name exists — imports do not count as bindings for field names.
CONTEXT_FIELDS: frozenset[str] = frozenset(_SNAPSHOT.get("fields", []))
INJECTED_NAMES: frozenset[str] = CONTEXT_FIELDS | frozenset(_SNAPSHOT.get("dynamic", []))


def _is_globals_call(node: ast.AST, attr: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == "globals"
    )


def _is_module_dict_update(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "update"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "__dict__"
    )


class StoryboardMigrationVisitor(ast.NodeVisitor):
    def __init__(self, filename: Path) -> None:
        self.filename = filename
        self.errors: list[tuple[int, str]] = []
        self.bound_stack: list[set[str]] = [set()]

    @property
    def bound(self) -> set[str]:
        return self.bound_stack[-1]

    def _bind_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self.bound.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._bind_target(item)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".", 1)[0]
            if name not in CONTEXT_FIELDS:
                self.bound.add(name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            name = alias.asname or alias.name
            # Imports never satisfy a context FIELD reference (see note at
            # CONTEXT_FIELDS): the field must be read off the context object.
            if name not in CONTEXT_FIELDS:
                self.bound.add(name)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._bind_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind_target(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._bind_target(node.target)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bound.add(node.name)
        function_bound = {arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
        if node.args.vararg:
            function_bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            function_bound.add(node.args.kwarg.arg)
        self.bound_stack.append(function_bound)
        for stmt in node.body:
            self.visit(stmt)
        self.bound_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_globals_call(node, "update"):
            self.errors.append((node.lineno, "globals().update is forbidden in migrated storyboard modules"))
        if _is_globals_call(node, "get"):
            self.errors.append((node.lineno, "globals().get is forbidden in migrated storyboard modules"))
        if _is_module_dict_update(node):
            self.errors.append((node.lineno, "module.__dict__.update is forbidden in migrated storyboard modules"))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "_sc":
            # Phase F red-line: the transitional bridge is deleted; contexts
            # travel only as explicit sc parameters.
            self.errors.append((node.lineno, "_sc bridge is deleted; pass sc explicitly"))
        if isinstance(node.ctx, ast.Load) and node.id in INJECTED_NAMES and not any(node.id in scope for scope in reversed(self.bound_stack)):
            self.errors.append((node.lineno, f"injected dependency {node.id!r} must be accessed through explicit context"))


def check_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = StoryboardMigrationVisitor(path)
    visitor.visit(tree)
    rel = path.relative_to(REPO_ROOT)
    return [f"{rel}:{line}: {message}" for line, message in visitor.errors]


def main() -> int:
    errors: list[str] = []
    for item in MIGRATED_FILES:
        path = (STORYBOARD_ROOT / item).resolve()
        if not path.exists():
            errors.append(f"{path.relative_to(REPO_ROOT)}: migrated file is missing")
            continue
        errors.extend(check_file(path))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("storyboard context migration check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
