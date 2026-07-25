from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROUTER = ROOT / "WorkflowAssistant" / "backend" / "workflow_assistant" / "routes.py"
OPENCODE = ROOT / "backend" / "opcrew_backend" / "adapters" / "opencode.py"
APP = ROOT / "backend" / "opcrew_backend" / "app.py"
SCHEMA = ROOT / "backend" / "opcrew_backend" / "db" / "schema.py"
OPENCLIP_MODULE = ROOT / "frontend" / "src" / "modules" / "koubo" / "OpenClipModule.jsx"
ASSISTANT_MODULE = ROOT / "WorkflowAssistant" / "frontend" / "src" / "WorkflowAssistantDrawer.jsx"
ASSISTANT_API = ROOT / "WorkflowAssistant" / "frontend" / "src" / "workflowAssistantApi.js"
TOOL_REGISTRY = ROOT / "ToolLibrary" / "Analysis" / "tool_registry.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_contains(content: str, needle: str, label: str) -> None:
    if needle not in content:
        raise AssertionError(f"Missing {label}: {needle}")


def main() -> None:
    router = read(ROUTER)
    opencode = read(OPENCODE)
    app = read(APP)
    schema = read(SCHEMA)
    module = read(OPENCLIP_MODULE)
    assistant_module = read(ASSISTANT_MODULE)
    api = read(ASSISTANT_API)
    registry = json.loads(TOOL_REGISTRY.read_text(encoding="utf-8"))

    assert_contains(router, "WORKFLOW_CONFIGS", "workflow registry")
    assert_contains(router, '"openclip_analysis"', "OpenClip workflow id")
    assert_contains(router, "OPENCLIP_QUICK_PROMPTS", "OpenClip quick prompt config")
    assert_contains(router, "/api/workflows/{workflow_id}/tasks/{task_id}/assistant/bootstrap", "canonical bootstrap route")
    assert_contains(router, "/api/workflows/{workflow_id}/tasks/{task_id}/assistant/messages", "canonical messages route")
    assert_contains(router, "/api/workflows/{workflow_id}/tasks/{task_id}/assistant/events", "canonical events route")
    assert_contains(router, "/api/workflows/{workflow_id}/tasks/{task_id}/assistant/message", "canonical message route")
    assert_contains(router, "/api/workflows/{workflow_id}/tasks/{task_id}/assistant/abort", "canonical abort route")
    assert_contains(router, "/api/workflows/{workflow_id}/tasks/{task_id}/assistant/plan", "canonical plan route")
    assert_contains(router, "/api/workflows/{workflow_id}/tasks/{task_id}/assistant/plan/confirm", "canonical plan confirm route")
    assert_contains(router, "/api/workflows/{workflow_id}/tasks/{task_id}/assistant/execute", "P5 execute guard route")
    assert_contains(router, "validate_plan", "P4 plan validation")
    assert_contains(router, "workflow.plan.confirmed", "P4 plan confirmed event")
    assert_contains(router, "current_workflow_plan.json", "P4 workspace plan audit")
    assert_contains(router, "Workflow Plan Runner is not implemented until P5", "P4 no-runner guard")
    assert_contains(router, '"type": str(payload.get("type")', "SSE type envelope")
    assert_contains(router, '"source": "opencode"', "OpenCode SSE source")
    assert_contains(router, '"source": "workflow"', "workflow SSE source")

    assert_contains(opencode, "def abort", "OpenCode abort client")
    assert_contains(opencode, "/global/event", "OpenCode global event stream")
    assert_contains(app, "build_workflow_assistant_router", "app workflow assistant router include")
    assert_contains(schema, "workflow_plans", "workflow plans table")

    assert_contains(api, "bootstrap", "frontend bootstrap API")
    assert_contains(api, "sendMessage", "frontend send message API")
    assert_contains(api, "eventsUrl", "frontend events API")
    assert_contains(api, "savePlan", "frontend save plan API")
    assert_contains(api, "confirmPlan", "frontend confirm plan API")

    assert_contains(assistant_module, "export default function WorkflowAssistantDrawer", "assistant drawer component")
    assert_contains(assistant_module, "new EventSource", "frontend SSE subscription")
    assert_contains(assistant_module, "message.part.delta", "delta reducer handling")
    assert_contains(assistant_module, "workflow-plan-panel", "frontend plan panel")
    assert_contains(assistant_module, "Import Latest JSON", "frontend plan extraction import")
    assert_contains(assistant_module, "Save Plan", "frontend save plan control")
    assert_contains(assistant_module, "Confirm", "frontend confirm plan control")
    assert_contains(module, "SharedWorkflowAssistantDrawer", "OpenClip imports shared drawer")
    assert_contains(module, "Task Assistant", "OpenClip assistant entry")

    tools = registry.get("tools") or []
    by_id = {str(tool.get("id")): tool for tool in tools if isinstance(tool, dict)}
    for required in ["01", "02", "06"]:
        if required not in by_id:
            raise AssertionError(f"Missing required Tool Registry entry: {required}")
    if by_id["06"].get("uses_vlm") is not True or by_id["06"].get("cost_level") != "very_high":
        raise AssertionError("Tool 06 must be VLM and very_high cost for P1 context tests")

    print("Workflow Assistant P1-P4 contract smoke checks passed.")


if __name__ == "__main__":
    main()
