#!/usr/bin/env python3
"""Phase R mechanical rewriter: bare injected names -> deps.<name>, AST-precise.

Usage: python phase_r_rewrite.py <routes_file.py>
- Finds every Name(Load) in INJECTED_NAMES (from the committed snapshot) that is
  not locally bound, using the same scope rules as the migration gate.
- Aborts if any hit is at module level (deps closure not available there).
- Inserts "deps." at exact (line, col) right-to-left; drops the
  globals().update(vars(deps)) line.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_storyboard_context_migration as gate  # noqa: E402

INJECTED = gate.INJECTED_NAMES
assert len(INJECTED) > 300, "snapshot not loaded?"


class RewriteVisitor(gate.StoryboardMigrationVisitor):
    def __init__(self, filename: Path) -> None:
        super().__init__(filename)
        self.hits: list[tuple[int, int, str, int]] = []  # line, col, name, scope_depth

    def visit_Name(self, node: ast.Name) -> None:
        if (
            isinstance(node.ctx, ast.Load)
            and node.id in INJECTED
            and not any(node.id in scope for scope in reversed(self.bound_stack))
        ):
            self.hits.append((node.lineno, node.col_offset, node.id, len(self.bound_stack)))


def main(path_str: str) -> int:
    path = Path(path_str).resolve()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    visitor = RewriteVisitor(path)
    visitor.visit(tree)

    module_level = [h for h in visitor.hits if h[3] <= 1]
    if module_level:
        print(f"ABORT: module-level injected-name usage (no deps closure): {module_level}")
        return 2

    # ast col_offset is a UTF-8 *byte* offset — slice bytes, not str.
    lines = [line.encode("utf-8") for line in source.splitlines(keepends=True)]
    for lineno, col, name, _depth in sorted(visitor.hits, key=lambda h: (h[0], h[1]), reverse=True):
        raw = lines[lineno - 1]
        name_b = name.encode("utf-8")
        assert raw[col:col + len(name_b)] == name_b, (lineno, col, name, raw)
        lines[lineno - 1] = raw[:col] + b"deps." + raw[col:]

    out = b"".join(lines).decode("utf-8")
    removed = 0
    new_lines = []
    for line in out.splitlines(keepends=True):
        if line.strip() == "globals().update(vars(deps))":
            removed += 1
            continue
        new_lines.append(line)
    if removed != 1:
        print(f"ABORT: expected exactly 1 globals().update line, found {removed}")
        return 2
    path.write_text("".join(new_lines), encoding="utf-8")

    errors = gate.check_file(path)
    if errors:
        print("GATE STILL RED AFTER REWRITE:")
        print("\n".join(errors))
        return 1
    print(f"{path.name}: rewrote {len(visitor.hits)} references, gate clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
