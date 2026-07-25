#!/usr/bin/env python3
"""Phase S mechanical hoister: nested service defs -> module level + _sc bridge.

Per plan §5 Phase S: hoist every nested def out of register_X_services, export
via explicit SERVICE_EXPORTS, replace globals().update(vars(ns)) with the
transitional module-global _sc, rewrite injected bare names to _sc.<name>.

Aborts (for manual treatment) when:
- register body has non-def statements besides the globals().update line and
  the trailing _export_services(...) call;
- any hoisted def has an injected name in a default-arg position (would
  evaluate against _sc=None at import time);
- a hoisted def name collides with an existing module-level binding.
"""
from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_storyboard_context_migration as gate  # noqa: E402

INJECTED = gate.INJECTED_NAMES
assert len(INJECTED) > 300


def string_interior_lines(source: str) -> set[int]:
    interior: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)) and tok.end[0] > tok.start[0]:
            interior.update(range(tok.start[0] + 1, tok.end[0] + 1))
    return interior


def module_level_bindings(tree: ast.Module) -> set[str]:
    bound: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Import):
            bound.update(a.asname or a.name.split(".", 1)[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(a.asname or a.name for a in node.names if a.name != "*")
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    bound.add(t.id)
    return bound


def main(path_str: str) -> int:
    path = Path(path_str).resolve()
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source, filename=str(path))

    reg = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("register_")), None)
    if reg is None:
        print("ABORT: no register_* function")
        return 2

    body = list(reg.body)
    if not (isinstance(body[0], ast.Expr) and "globals" in ast.dump(body[0])):
        print("ABORT: register body[0] is not globals().update")
        return 2
    tail = body[-1]
    if not (isinstance(tail, ast.Expr) and isinstance(tail.value, ast.Call)
            and getattr(tail.value.func, "id", "") == "_export_services"):
        print("ABORT: register body[-1] is not _export_services(...)")
        return 2
    defs = body[1:-1]
    if not all(isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) for d in defs):
        bad = [type(d).__name__ for d in defs if not isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))]
        print(f"ABORT: non-def statements in register body: {bad}")
        return 2

    existing = module_level_bindings(tree)
    collisions = [d.name for d in defs if d.name in existing]
    if collisions:
        print(f"ABORT: hoisted names collide with module-level bindings: {collisions}")
        return 2
    for d in defs:
        for default in [*d.args.defaults, *[x for x in d.args.kw_defaults if x is not None]]:
            for node in ast.walk(default):
                if isinstance(node, ast.Name) and node.id in INJECTED and node.id not in {x.name for x in defs}:
                    print(f"ABORT: {d.name} has injected name {node.id!r} in a default arg")
                    return 2

    interior = string_interior_lines(source)

    def dedent_block(start: int, end: int) -> str:
        out: list[str] = []
        for ln in range(start, end + 1):
            raw = lines[ln - 1]
            if ln in interior or raw.strip() == "":
                out.append(raw)
                continue
            if not raw.startswith("    "):
                raise AssertionError(f"line {ln} not indented by 4: {raw!r}")
            out.append(raw[4:])
        return "".join(out)

    hoisted_blocks = []
    for d in defs:
        start = min([d.lineno, *[dec.lineno for dec in d.decorator_list]])
        hoisted_blocks.append(dedent_block(start, d.end_lineno))

    exports = [d.name for d in defs if not d.name.startswith("_")]
    export_lines = "SERVICE_EXPORTS = (\n" + "".join(f'    "{n}",\n' for n in exports) + ")\n"
    sc_decl = "_sc: Any = None" if "Any" in existing else "_sc = None"
    sc_lines = (
        "# Phase S transitional context bridge (step-1 plan §5 Phase S): holds the\n"
        "# last-registered StoryboardContext until Phase F passes `sc` explicitly.\n"
        f"{sc_decl}\n"
    )
    new_register = (
        f"def {reg.name}(ns: Any) -> None:\n"
        "    global _sc\n"
        "    _sc = ns\n"
        "    for name in SERVICE_EXPORTS:\n"
        "        setattr(ns, name, globals()[name])\n"
    )

    new_block = sc_lines + "\n" + export_lines + "\n\n" + "\n\n".join(b.rstrip("\n") + "\n" for b in hoisted_blocks) + "\n\n" + new_register

    # Replace the register function block; drop the local _export_services def.
    exp_def = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_export_services"), None)
    replacements = [(reg.lineno, reg.end_lineno, new_block)]
    if exp_def is not None:
        replacements.append((exp_def.lineno, exp_def.end_lineno, ""))
    replacements.sort(reverse=True)
    out_lines = lines[:]
    for start, end, text_block in replacements:
        out_lines[start - 1:end] = [text_block]
    new_source = "".join(out_lines)

    # _sc-prefix pass: bare injected names not bound anywhere in scope -> _sc.<name>
    new_tree = ast.parse(new_source, filename=str(path))
    visitor = gate.StoryboardMigrationVisitor(path)
    hits: list[tuple[int, int, str]] = []
    original_visit_name = gate.StoryboardMigrationVisitor.visit_Name

    def record_name(self, node: ast.Name) -> None:
        if (isinstance(node.ctx, ast.Load) and node.id in INJECTED
                and not any(node.id in s for s in reversed(self.bound_stack))):
            hits.append((node.lineno, node.col_offset, node.id))

    gate.StoryboardMigrationVisitor.visit_Name = record_name
    try:
        visitor.visit(new_tree)
    finally:
        gate.StoryboardMigrationVisitor.visit_Name = original_visit_name

    raw_lines = [ln.encode("utf-8") for ln in new_source.splitlines(keepends=True)]
    for lineno, col, name in sorted(hits, reverse=True):
        raw = raw_lines[lineno - 1]
        nb = name.encode("utf-8")
        assert raw[col:col + len(nb)] == nb, (lineno, col, name)
        raw_lines[lineno - 1] = raw[:col] + b"_sc." + raw[col:]
    final = b"".join(raw_lines).decode("utf-8")
    while "\n\n\n\n" in final:
        final = final.replace("\n\n\n\n", "\n\n\n")
    path.write_text(final, encoding="utf-8")

    errors = gate.check_file(path)
    if errors:
        print("GATE STILL RED AFTER HOIST:")
        print("\n".join(errors))
        return 1
    print(f"{path.name}: hoisted {len(defs)} defs ({len(exports)} exported), {len(hits)} _sc rewrites, gate clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
