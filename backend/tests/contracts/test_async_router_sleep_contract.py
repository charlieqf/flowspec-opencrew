from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
REBUILD_ROUTER_PATH = ROOT / "backend" / "opcrew_backend" / "koubo" / "rebuild_router.py"
ROUTER_PATHS = (
    ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py",
    REBUILD_ROUTER_PATH,
)
PROMPT_POLLING_HELPERS = (
    "refine_asset_prompt",
    "generate_host_product_final_prompt",
    "refine_asset_video_prompt",
)
PROMPT_ROUTE_HELPERS = {
    "host_product_builder_prompt": "generate_host_product_final_prompt",
    "refine_asset_image_prompt": "refine_asset_prompt",
    "refine_asset_video_prompt_route": "refine_asset_video_prompt",
}
SYNC_OPENCODE_METHODS = {"prompt_async", "messages"}
SYNC_POLLING_HELPERS = {"resolve_model"}


class BlockingSleepVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.lines: list[int] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "sleep"
            and isinstance(function.value, ast.Name)
            and function.value.id == "time"
        ):
            self.lines.append(node.lineno)
        self.generic_visit(node)


class AsyncRouterSleepContractTest(unittest.TestCase):
    def test_async_routes_do_not_block_event_loop_with_time_sleep(self) -> None:
        failures: list[str] = []
        for path in ROUTER_PATHS:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for function in (node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)):
                visitor = BlockingSleepVisitor()
                for statement in function.body:
                    visitor.visit(statement)
                failures.extend(
                    f"{path.relative_to(ROOT)}:{line} ({function.name})"
                    for line in visitor.lines
                )
        self.assertEqual(failures, [], f"Blocking time.sleep found in async route: {failures}")

    def test_prompt_polling_helpers_are_async_and_offload_opencode_io(self) -> None:
        tree = ast.parse(REBUILD_ROUTER_PATH.read_text(encoding="utf-8"), filename=str(REBUILD_ROUTER_PATH))
        async_functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
        }
        failures: list[str] = []
        for helper_name in PROMPT_POLLING_HELPERS:
            function = async_functions.get(helper_name)
            if function is None:
                failures.append(f"{helper_name} must be async")
                continue
            direct_calls = [
                node.lineno
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Attribute) and node.func.attr in SYNC_OPENCODE_METHODS)
                    or (isinstance(node.func, ast.Name) and node.func.id in SYNC_POLLING_HELPERS)
                )
            ]
            if direct_calls:
                failures.append(f"{helper_name} directly calls synchronous polling I/O at {direct_calls}")
            offloaded_calls = {
                first_arg.attr if isinstance(first_arg, ast.Attribute) else first_arg.id
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
                and node.func.attr == "to_thread"
                and node.args
                and isinstance((first_arg := node.args[0]), (ast.Attribute, ast.Name))
                and (
                    (isinstance(first_arg, ast.Attribute) and first_arg.attr in SYNC_OPENCODE_METHODS)
                    or (isinstance(first_arg, ast.Name) and first_arg.id in SYNC_POLLING_HELPERS)
                )
            }
            missing = (SYNC_OPENCODE_METHODS | SYNC_POLLING_HELPERS) - offloaded_calls
            if missing:
                failures.append(f"{helper_name} does not offload {sorted(missing)} with asyncio.to_thread")
        self.assertEqual(failures, [])

    def test_async_routes_await_prompt_polling_helpers(self) -> None:
        tree = ast.parse(REBUILD_ROUTER_PATH.read_text(encoding="utf-8"), filename=str(REBUILD_ROUTER_PATH))
        async_functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
        }
        failures: list[str] = []
        for route_name, helper_name in PROMPT_ROUTE_HELPERS.items():
            function = async_functions.get(route_name)
            if function is None:
                failures.append(f"missing async route {route_name}")
                continue
            returns = [statement for statement in function.body if isinstance(statement, ast.Return)]
            value = returns[0].value if len(returns) == 1 else None
            if not (
                isinstance(value, ast.Await)
                and isinstance(value.value, ast.Call)
                and isinstance(value.value.func, ast.Name)
                and value.value.func.id == helper_name
            ):
                failures.append(f"{route_name} must return await {helper_name}(...)")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
