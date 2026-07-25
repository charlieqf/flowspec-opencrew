#!/usr/bin/env python3
"""Step 1 Phase 0 inventories for the koubo_storyboard globals->context migration.

Maintains two committed inventory files (see docs/opencrew_step1_globals_to_context_plan_2026-07-01.md
and the 2026-07-04 review additions):

1. scripts/storyboard_context_injected_names.json — the full universe of names
   injected into service-module globals (StoryboardContext fields + every
   function _export_services hangs onto the context). Gives the AST gate
   (scripts/check_storyboard_context_migration.py) complete coverage from day 1
   instead of an incrementally-built union that is only complete at Phase F.

2. scripts/storyboard_spawn_sites.json — every background-work spawn point
   (threading.Thread / asyncio.create_task / asyncio.to_thread / ...) in
   koubo_storyboard. These are where implicit global access hides and must be
   converted to explicit `sc` passing as each module migrates in Phase S.

Modes:
  --write   regenerate both files (requires backend venv python for the snapshot)
  (default) verify committed files match a fresh scan/build; exit 1 on drift

The contract test test_koubo_storyboard_step1_inventory_contract.py runs verify
mode in CI so drift red-lights in the backend job (the lint job stays dep-free).
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = REPO_ROOT / "backend"
STORYBOARD_ROOT = BACKEND_PATH / "opcrew_backend" / "koubo" / "koubo_storyboard"
SNAPSHOT_PATH = REPO_ROOT / "scripts" / "storyboard_context_injected_names.json"
SPAWN_SITES_PATH = REPO_ROOT / "scripts" / "storyboard_spawn_sites.json"

SNAPSHOT_PROBE = """
import dataclasses
import json

from opcrew_backend.koubo.koubo_storyboard.services import StoryboardContext, build_koubo_storyboard_services


class DummyContext:
    pass


class DummyRepository:
    pass


ns = build_koubo_storyboard_services(DummyContext(), DummyRepository())
field_names = sorted(field.name for field in dataclasses.fields(StoryboardContext))
all_names = sorted(vars(ns).keys())
dynamic_names = sorted(set(all_names) - set(field_names))
print(json.dumps({"fields": field_names, "dynamic": dynamic_names}))
"""

SPAWN_ATTR_KINDS = {
    "Thread": "threading.Thread",
    "create_task": "asyncio.create_task",
    "to_thread": "asyncio.to_thread",
    "ensure_future": "asyncio.ensure_future",
    "run_in_executor": "run_in_executor",
    "submit": "executor.submit",
    "ThreadPoolExecutor": "ThreadPoolExecutor",
    "start_new_thread": "start_new_thread",
}


def build_injected_names_snapshot() -> dict[str, list[str]]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(BACKEND_PATH) if not existing else f"{BACKEND_PATH}{os.pathsep}{existing}"
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(SNAPSHOT_PROBE)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "building StoryboardContext snapshot failed (run with backend/.venv/bin/python):\n"
            + result.stderr
        )
    return json.loads(result.stdout)


class SpawnSiteVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str, source_lines: list[str]) -> None:
        self.rel_path = rel_path
        self.source_lines = source_lines
        self.sites: list[dict[str, object]] = []

    def _record(self, node: ast.Call, kind: str) -> None:
        code = self.source_lines[node.lineno - 1].strip() if node.lineno - 1 < len(self.source_lines) else ""
        self.sites.append({"file": self.rel_path, "line": node.lineno, "kind": kind, "code": code})

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name in SPAWN_ATTR_KINDS:
            self._record(node, SPAWN_ATTR_KINDS[name])
        self.generic_visit(node)


def scan_spawn_sites() -> list[dict[str, object]]:
    sites: list[dict[str, object]] = []
    for path in sorted(STORYBOARD_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        visitor = SpawnSiteVisitor(str(path.relative_to(REPO_ROOT)), source.splitlines())
        visitor.visit(tree)
        sites.extend(visitor.sites)
    return sites


def _dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def collect_static_service_exports() -> dict[str, list[str]]:
    # Static SERVICE_EXPORTS declarations (Phase S migrated modules). These are
    # the intent-declared export whitelists; the runtime snapshot must agree
    # with them, and at Phase F the dynamic set must equal exactly their union.
    exports: dict[str, list[str]] = {}
    for path in sorted(STORYBOARD_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "SERVICE_EXPORTS" for t in node.targets)
                and isinstance(node.value, (ast.Tuple, ast.List))
            ):
                names = [c.value for c in node.value.elts if isinstance(c, ast.Constant) and isinstance(c.value, str)]
                exports[str(path.relative_to(REPO_ROOT))] = names
    return exports


def write_inventories(allow_new: set[str]) -> int:
    # Pollution tripwire (plan §0 decision 3): the snapshot is generated from
    # the runtime assembly, so a bad Phase S export filter could leak imported
    # symbols into ns and --write would silently re-solidify them as baseline.
    # Every NEW dynamic name therefore requires explicit --allow-new listing —
    # additions become a reviewed act instead of an automatic refresh.
    fresh = build_injected_names_snapshot()
    if SNAPSHOT_PATH.exists():
        committed = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        added = sorted(
            (set(fresh.get("fields", [])) | set(fresh.get("dynamic", [])))
            - (set(committed.get("fields", [])) | set(committed.get("dynamic", [])))
        )
        undeclared = [name for name in added if name not in allow_new]
        if undeclared:
            print(
                "refusing to solidify new injected names not listed via --allow-new "
                f"(export pollution guard): {undeclared}",
                file=sys.stderr,
            )
            return 1
    SNAPSHOT_PATH.write_text(_dump(fresh), encoding="utf-8")
    SPAWN_SITES_PATH.write_text(_dump(scan_spawn_sites()), encoding="utf-8")
    print(f"wrote {SNAPSHOT_PATH.relative_to(REPO_ROOT)} and {SPAWN_SITES_PATH.relative_to(REPO_ROOT)}")
    return 0


def verify_inventories() -> int:
    errors: list[str] = []
    if not SNAPSHOT_PATH.exists():
        errors.append(f"{SNAPSHOT_PATH.relative_to(REPO_ROOT)} is missing; run --write")
    else:
        committed = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        fresh = build_injected_names_snapshot()
        if committed != fresh:
            for key in ("fields", "dynamic"):
                missing = sorted(set(committed.get(key, [])) - set(fresh.get(key, [])))
                added = sorted(set(fresh.get(key, [])) - set(committed.get(key, [])))
                if missing:
                    errors.append(f"injected-names snapshot stale — {key} removed at runtime: {missing}")
                if added:
                    errors.append(f"injected-names snapshot stale — new {key} not in snapshot: {added}")
    if not SPAWN_SITES_PATH.exists():
        errors.append(f"{SPAWN_SITES_PATH.relative_to(REPO_ROOT)} is missing; run --write")
    else:
        committed_sites = json.loads(SPAWN_SITES_PATH.read_text(encoding="utf-8"))
        fresh_sites = scan_spawn_sites()
        if committed_sites != fresh_sites:
            errors.append(
                "spawn-site inventory stale — koubo_storyboard spawn points changed; "
                "review sc-passing for the new/moved sites, then run --write"
            )
    if SNAPSHOT_PATH.exists():
        # Cross-check static intent vs runtime reality: every statically
        # declared SERVICE_EXPORTS name must exist in the snapshot. (Phase F
        # tightens this to exact equality: dynamic == union of all
        # SERVICE_EXPORTS; until then unmigrated modules export via locals().)
        snapshot_names = set(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")).get("dynamic", []))
        for module, names in collect_static_service_exports().items():
            lost = sorted(set(names) - snapshot_names)
            if lost:
                errors.append(f"{module}: SERVICE_EXPORTS declares names missing from runtime snapshot: {lost}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("storyboard step1 inventories are up to date")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--write" in args:
        allow_new: set[str] = set()
        for i, arg in enumerate(args):
            if arg == "--allow-new" and i + 1 < len(args):
                allow_new.update(n.strip() for n in args[i + 1].split(",") if n.strip())
            elif arg.startswith("--allow-new="):
                allow_new.update(n.strip() for n in arg.split("=", 1)[1].split(",") if n.strip())
        return write_inventories(allow_new)
    return verify_inventories()


if __name__ == "__main__":
    raise SystemExit(main())
