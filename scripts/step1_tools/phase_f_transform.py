#!/usr/bin/env python3
"""Phase F step 1: transitional keyword-only sc across all migrated service modules.

Per plan §5 Phase F item 1:
- every context-dependent module-level service function gains a TAIL
  keyword-only `*, sc: Any = None` and a first-line fallback
  `sc = sc if sc is not None else _sc`;
- `_sc` references inside those functions become `sc`;
- intra-module bare calls and cross-module `sc.<func>(...)` calls whose target
  needs sc get an explicit `sc=sc` argument;
- pure helpers are untouched; register functions and the module-level `_sc`
  bridge stay until step 3 deletes them.

Need-set: direct `_sc` reference, closed over intra-module bare calls to needy
siblings (fixpoint), computed globally first so cross-module `sc=sc` appending
is correct.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STORY = REPO_ROOT / "backend/opcrew_backend/koubo/koubo_storyboard"
SNAPSHOT = json.loads((REPO_ROOT / "scripts/storyboard_context_injected_names.json").read_text())
FIELDS = set(SNAPSHOT["fields"])

SERVICE_MODULES = sorted(p for p in STORY.glob("*services*.py"))


def module_functions(tree: ast.Module):
    return [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("register_")]


def refs_sc(fn: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id == "_sc" for n in ast.walk(fn))


def bare_calls(fn: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


# ---- pass 1: global NEED map -------------------------------------------------
trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in SERVICE_MODULES}
need: dict[str, set[str]] = {}
owner_module: dict[str, str] = {}
for p, tree in trees.items():
    fns = module_functions(tree)
    for f in fns:
        owner_module[f.name] = p.stem
    local_need = {f.name for f in fns if refs_sc(f)}
    names = {f.name for f in fns}
    changed = True
    while changed:
        changed = False
        for f in fns:
            if f.name in local_need:
                continue
            if bare_calls(f) & local_need & names:
                local_need.add(f.name)
                changed = True
    need[p.stem] = local_need

NEEDY = {name for mod, s in need.items() for name in s}


# ---- pass 2: transform each module -------------------------------------------
def transform(path: Path) -> tuple[int, int]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fns = [f for f in module_functions(tree) if f.name in need[path.stem]]
    own_names = {f.name for f in module_functions(tree)}
    raw = [l.encode("utf-8") for l in source.splitlines(keepends=True)]

    # edits: (line, col, kind, payload) applied bottom-up; kinds:
    #   ins  -> insert payload bytes at position
    #   name -> replace len('_sc') bytes with payload
    edits: list[tuple[int, int, str, bytes]] = []

    for f in fns:
        a = f.args
        assert not any(x.arg == "sc" for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)), f"{path.stem}.{f.name} already has sc param"
        if a.kwarg is not None:
            # insert before the ** of kwarg
            k = a.kwarg
            line = raw[k.lineno - 1]
            star2 = line.rfind(b"**", 0, k.col_offset)
            assert star2 != -1, (path.stem, f.name)
            lead = b"sc: Any = None, " if (a.vararg or a.kwonlyargs) else b"*, sc: Any = None, "
            edits.append((k.lineno, star2, "ins", lead))
        else:
            parts = [*a.posonlyargs, *a.args, *([a.vararg] if a.vararg else []), *a.kwonlyargs]
            defaults = [d for d in (*a.defaults, *[x for x in a.kw_defaults if x is not None]) if d is not None]
            ends = [(x.end_lineno, x.end_col_offset) for x in parts if x is not None]
            ends += [(d.end_lineno, d.end_col_offset) for d in defaults]
            ann_ends = [(x.annotation.end_lineno, x.annotation.end_col_offset) for x in parts if x is not None and x.annotation]
            ends += ann_ends
            if ends:
                ln, col = max(ends)
                sep = b", sc: Any = None" if (a.vararg or a.kwonlyargs) else b", *, sc: Any = None"
                edits.append((ln, col, "ins", sep))
            else:
                # zero-arg function: insert right after '('
                line = raw[f.lineno - 1]
                paren = line.index(b"(", line.index(b"def" if isinstance(f, ast.FunctionDef) else b"async"))
                edits.append((f.lineno, paren + 1, "ins", b"*, sc: Any = None"))

        # fallback first line (after docstring if any)
        body0 = f.body[0]
        insert_stmt = body0
        if (isinstance(body0, ast.Expr) and isinstance(body0.value, ast.Constant) and isinstance(body0.value.value, str) and len(f.body) > 1):
            insert_stmt = f.body[1]
        indent = b" " * insert_stmt.col_offset
        edits.append((insert_stmt.lineno, 0, "ins", indent + b"sc = sc if sc is not None else _sc\n"))

        # _sc -> sc inside function
        for n in ast.walk(f):
            if isinstance(n, ast.Name) and n.id == "_sc":
                edits.append((n.lineno, n.col_offset, "name", b"sc"))

        # sc=sc appending: intra-module bare calls to needy; cross-module _sc.<needy>(...) calls
        for n in ast.walk(f):
            if not isinstance(n, ast.Call):
                continue
            target = None
            if isinstance(n.func, ast.Name) and n.func.id in own_names and n.func.id in need[path.stem]:
                target = n.func.id
            elif (isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                  and n.func.value.id == "_sc" and n.func.attr in NEEDY and n.func.attr not in FIELDS):
                target = n.func.attr
            if target is None:
                continue
            if any(kw.arg == "sc" for kw in n.keywords):
                continue
            edits.append((n.end_lineno, n.end_col_offset - 1, "call", b""))

    # apply bottom-up, rightmost-first; 'name' replacements must apply before
    # insertions at the same position shift things — sort key handles order.
    def key(e):
        return (e[0], e[1], 0 if e[2] == "name" else 1)

    def prev_nonspace(lineno: int, col: int) -> bytes:
        # last non-whitespace byte before (lineno, col), scanning back lines
        ln, c = lineno, col
        while ln >= 1:
            seg = raw[ln - 1][:c].rstrip()
            if seg:
                return seg[-1:]
            ln -= 1
            c = len(raw[ln - 1]) if ln >= 1 else 0
        return b""

    for lineno, col, kind, payload in sorted(edits, key=key, reverse=True):
        line = raw[lineno - 1]
        if kind == "name":
            assert line[col:col + 3] == b"_sc", (path.stem, lineno, col, line)
            raw[lineno - 1] = line[:col] + payload + line[col + 3:]
        elif kind == "call":
            assert line[col:col + 1] == b")", (path.stem, lineno, col, line)
            prev = prev_nonspace(lineno, col)
            payload = b"sc=sc" if prev in (b",", b"(") else b", sc=sc"
            raw[lineno - 1] = line[:col] + payload + line[col:]
        else:
            raw[lineno - 1] = line[:col] + payload + line[col:]

    path.write_text(b"".join(raw).decode("utf-8"), encoding="utf-8")
    ast.parse(path.read_text(encoding="utf-8"))  # syntax sanity
    return len(fns), len(edits)


if __name__ == "__main__":
    targets = sys.argv[1:] or [p.stem for p in SERVICE_MODULES]
    total_f = total_e = 0
    for p in SERVICE_MODULES:
        if p.stem not in targets:
            continue
        nf, ne = transform(p)
        total_f += nf; total_e += ne
        print(f"{p.name}: {nf} functions gained sc, {ne} edits")
    print(f"TOTAL: {total_f} functions, {total_e} edits")
