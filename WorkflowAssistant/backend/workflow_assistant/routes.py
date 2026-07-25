from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select, update

from opcrew_backend.adapters.opencode import OpenCodeSessionClient
from opcrew_backend.context import AppContext, now_ms
from opcrew_backend.db.schema import workflow_plans
from opcrew_backend.repositories.base import row_to_dict

from opcrew_backend.koubo.repository import OpenClipRepository
from opcrew_backend.koubo.rebuild_repository import OCRebuildRepository

from .workflow_config import WORKFLOW_CONFIGS, resolve_workflow_tool_library, workflow_tool_library_source


WORKFLOW_ASSISTANT_BASE_PROMPT = """
You are the Workflow Assistant running inside an OpenCode Session.

Your job:
1. Understand the current Task, Session, Workspace, and Tool Library.
2. Plan before execution.
3. Explain recommended tools in a concise table with tool, purpose, reason, cost, and whether to run.
4. Wait for the user to confirm or edit the plan before execution.
5. High-cost, long-running, LLM, and VLM tools require explicit confirmation.
6. Do not invent tools that are not in the Tool Registry.
7. Do not output arbitrary shell commands as the execution source of truth.
8. Execution is performed by the backend Plan Runner from confirmed JSON Plan only.
9. If the user asks about progress, answer based on injected workflow run status.
10. If the user edits the plan, treat the latest confirmed plan as authoritative.
""".strip()

OPENCLIP_ASSISTANT_ADDITION = """
The current workflow is OpenClip Analysis.
The goal is to use the Task Final Prompt, selected Run Model, and reference video to produce video semantic segmentation, retake descriptions, and three scheme outputs when requested.
Always respect Final Prompt business requirements over generic segmentation defaults.
""".strip()

OC_REBUILD_ASSISTANT_ADDITION = """
The current workflow is OC-Rebuild.
The goal is to use a selected OC-Analysis task and source scheme to build source_package.json, structure rebuild_intent.json, then plan the next rebuild steps.
Step 02 is intent-only: do not create shot plans, asset tasks, ComfyUI prompts, render plans, or execution steps unless the user explicitly moves beyond Intent Builder.
""".strip()

OC_REBUILD_PLAN_A_PHASE_BATCH_ADDITION = """
The current workflow is OC-Rebuild Plan A Phase-Batch Auto v0.
This is the previously verified Plan A workflow. Preserve it as the legacy stage-by-stage execution order: all shots complete a stage before the next stage starts.
When asked to execute the named workflow, choose Tool Registry entry PlanA_PhaseBatch_AutoV0.
""".strip()

OC_REBUILD_PLAN_A_SHOT_FIRST_ADDITION = """
The current workflow is OC-Rebuild Plan A Shot-First Auto v1.
This workflow completes each shot through the full Phase 3 chain before moving to the next shot, then runs final assembly.
When asked to execute the named workflow, choose Tool Registry entry PlanA_ShotFirst_AutoV1.
""".strip()

OPENCLIP_QUICK_PROMPTS = [
    {
        "id": "plan_segmentation_path",
        "label": "规划拆分路径",
        "mode": "fill",
        "prompt": "请浏览 Tool Library 中的所有工具，针对当前 Task 和 Session，使用这个 Task 的 Final Prompt / Run Model，以及 Video，规划整体视频拆分路径。请用简洁表格输出：工具、目的、理由、成本、是否建议执行。先不要执行。",
    },
    {
        "id": "check_outputs",
        "label": "检查已有产物",
        "mode": "send",
        "prompt": "请检查当前 workspace 已有产物，识别可以跳过的步骤、缺失的关键步骤，以及下一步建议。先不要执行任何工具。",
    },
    {
        "id": "optimize_balanced_scheme",
        "label": "优化均衡分镜",
        "mode": "fill",
        "prompt": "请基于当前已有输出，重点复盘 Scheme 2 均衡分镜是否适合复拍交付，并提出需要合并、拆分或重排的建议。先不要执行。",
    },
    {
        "id": "make_plan_json",
        "label": "生成执行计划",
        "mode": "send",
        "prompt": "请把当前讨论过的执行方案转换为 workflow_tool_plan_v1 JSON。只能使用 Tool Registry 中存在的工具，并标明成本、LLM/VLM 使用、依赖和 expected_outputs。先不要执行。",
    },
    {
        "id": "confirm_execution_plan",
        "label": "执行确认方案",
        "mode": "fill",
        "prompt": "请复核当前计划，指出高成本、LLM、VLM 或长耗时步骤，并列出需要我显式确认的项目。确认前不要执行。",
    },
    {
        "id": "review_quality",
        "label": "复盘质量",
        "mode": "fill",
        "prompt": "请复盘当前 OpenClip 交付物质量，检查三套方案、复拍描述、证据链和质量报告是否完整，并提出修复计划。先不要执行。",
    },
]

OC_REBUILD_QUICK_PROMPTS = [
    {"id": "check_rebuild_inputs", "label": "检查输入", "mode": "send", "prompt": "请检查当前 OC-Rebuild Task 的 Analysis 来源、source_package.json 和 rebuild_intent.json 是否完整，并列出缺失项。先不要执行。"},
    {"id": "plan_intent_builder", "label": "规划意图", "mode": "fill", "prompt": "请基于当前 Rebuild 页面参数和来源 Analysis Task，复核 Step 02 Intent Builder 的意图是否清晰、可结构化，并指出需要补充的字段。先不要执行。"},
    {"id": "make_rebuild_plan", "label": "生成计划", "mode": "send", "prompt": "请把 OC-Rebuild 的下一步转换为 workflow_tool_plan_v1 JSON。只能使用 Rebuild Tool Registry 中存在的工具，并标明依赖、成本和 expected_outputs。先不要执行。"},
]

OC_REBUILD_PLAN_A_QUICK_PROMPTS = [
    {"id": "choose_plan_a_workflow", "label": "选择 Plan A", "mode": "fill", "prompt": "请比较 PlanA_PhaseBatch_AutoV0 和 PlanA_ShotFirst_AutoV1，基于当前目标推荐一个 workflow。先不要执行。"},
    {"id": "make_shot_first_plan", "label": "Shot 优先计划", "mode": "send", "prompt": "请生成 workflow_tool_plan_v1 JSON，使用 Tool Registry 中的 PlanA_ShotFirst_AutoV1 作为执行入口。说明它会按 shot 完整跑完 Phase 3 后再跑下一个 shot。先不要执行。"},
    {"id": "make_phase_batch_plan", "label": "旧版计划", "mode": "send", "prompt": "请生成 workflow_tool_plan_v1 JSON，使用 Tool Registry 中的 PlanA_PhaseBatch_AutoV0 作为执行入口。说明这是已验证过的阶段批处理工作流。先不要执行。"},
]


def build_workflow_assistant_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    openclip_repo = OpenClipRepository(ctx.engine)
    rebuild_repo = OCRebuildRepository(ctx.engine)

    def workflow_config(workflow_id: str) -> dict[str, Any]:
        config = WORKFLOW_CONFIGS.get(workflow_id)
        if not config:
            raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")
        return config

    def get_openclip_task(task_id: int) -> dict[str, Any]:
        row = openclip_repo.get_task(task_id)
        if not row:
            raise HTTPException(status_code=404, detail="OpenClip task not found")
        return row

    def get_oc_rebuild_task(task_id: int) -> dict[str, Any]:
        row = rebuild_repo.get_task(task_id)
        if not row:
            raise HTTPException(status_code=404, detail="OC-Rebuild task not found")
        return row

    def resolve_workflow_task(workflow_id: str, task_id: int) -> dict[str, Any]:
        config = workflow_config(workflow_id)
        if config["task_adapter"] == "oc_rebuild":
            return get_oc_rebuild_task(task_id)
        if config["task_adapter"] != "openclip":
            raise HTTPException(status_code=404, detail=f"Unsupported workflow adapter: {config['task_adapter']}")
        return get_openclip_task(task_id)

    def opencode_client_for(session_row: dict[str, Any]) -> OpenCodeSessionClient:
        base_url = str(ctx.get_setting("opencode.base_url") or "").strip()
        username = str(ctx.get_setting("opencode.username") or "").strip()
        password = str(ctx.get_setting("opencode.password") or "").strip()
        if not base_url or not username or not password:
            raise HTTPException(status_code=400, detail="OpenCode connection is incomplete. Finish Step 1 before using Workflow Assistant.")
        return OpenCodeSessionClient(base_url=base_url, username=username, password=password, directory=str(session_row["workspace_dir"]))

    def tool_library_root(workflow_id: str = "openclip_analysis") -> Path:
        try:
            return resolve_workflow_tool_library(workflow_id)["root"]
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    def load_tool_registry_summary(warnings: list[str], workflow_id: str = "openclip_analysis") -> list[dict[str, Any]]:
        try:
            path = resolve_workflow_tool_library(workflow_id)["registry"]
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to load Tool Registry: {exc}") from exc
        tools = payload.get("tools")
        if not isinstance(tools, list):
            raise HTTPException(status_code=500, detail="Tool Registry is invalid: tools must be a list")
        summary: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            summary.append({
                "id": tool.get("id"),
                "name": tool.get("name"),
                "stage": tool.get("stage"),
                "purpose": tool.get("agent_notes") or tool.get("description") or "",
                "cost_level": tool.get("cost_level"),
                "uses_llm": bool(tool.get("uses_llm")),
                "uses_vlm": bool(tool.get("uses_vlm")),
                "required_by_default": bool(tool.get("required_by_default")),
                "supports_resume": bool(tool.get("supports_resume")),
                "hard_dependencies": tool.get("hard_dependencies") or [],
                "soft_dependencies": tool.get("soft_dependencies") or [],
                "main_outputs": tool.get("main_outputs") or [],
                "args_schema": tool.get("args_schema") or None,
            })
        if not summary:
            warnings.append("Tool Registry loaded but contains no tools.")
        return summary

    def tool_registry_by_id(workflow_id: str = "openclip_analysis") -> dict[str, dict[str, Any]]:
        warnings: list[str] = []
        return {str(tool.get("id") or ""): tool for tool in load_tool_registry_summary(warnings, workflow_id) if tool.get("id")}

    def add_workflow_event(session_id: int, kind: str, payload: dict[str, Any]) -> None:
        ctx.session_event_service.add_event(
            session_id,
            kind,
            payload,
            visibility="internal",
            event_scope="debug",
            family="workflow",
            workflow_id=str(payload.get("workflow_id") or ""),
            task_id=int(payload["task_id"]) if str(payload.get("task_id") or "").isdigit() else None,
        )

    def current_plan_row(workflow_id: str, task_id: int) -> dict[str, Any] | None:
        statement = (
            select(workflow_plans)
            .where(workflow_plans.c.workflow_id == workflow_id, workflow_plans.c.task_id == task_id)
            .order_by(desc(workflow_plans.c.updated_at), desc(workflow_plans.c.id))
            .limit(1)
        )
        with ctx.engine.connect() as conn:
            return row_to_dict(conn.execute(statement).first())

    def plan_payload_from_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        try:
            plan = json.loads(str(row.get("plan_json") or "{}"))
        except Exception:
            plan = {}
        return {
            "id": int(row["id"]),
            "workflow_id": row.get("workflow_id"),
            "task_id": int(row["task_id"]),
            "session_id": int(row["session_id"]),
            "status": row.get("status"),
            "plan_json": plan,
            "created_by_message_id": row.get("created_by_message_id"),
            "confirmed_by_message_id": row.get("confirmed_by_message_id"),
            "created_at": int(row.get("created_at") or 0),
            "updated_at": int(row.get("updated_at") or 0),
            "confirmed_at": int(row["confirmed_at"]) if row.get("confirmed_at") is not None else None,
        }

    def workspace_plan_path(task_row: dict[str, Any]) -> Path:
        return Path(str(task_row["workspace_dir"])) / "input" / "current_workflow_plan.json"

    def write_plan_audit_file(task_row: dict[str, Any], plan: dict[str, Any]) -> None:
        path = workspace_plan_path(task_row)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def validate_plan(workflow_id: str, task_id: int, task_row: dict[str, Any], plan: Any, *, require_high_cost_ack: bool = False, acknowledged_step_ids: set[str] | None = None) -> tuple[dict[str, Any], list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if not isinstance(plan, dict):
            raise HTTPException(status_code=400, detail={"message": "Plan must be a JSON object", "validation_errors": ["Plan must be a JSON object"]})
        normalized = dict(plan)
        if normalized.get("schema") != "workflow_tool_plan_v1":
            errors.append("schema must be workflow_tool_plan_v1")
        if normalized.get("workflow_id") != workflow_id:
            errors.append("workflow_id does not match request workflow")
        if int(normalized.get("task_id") or 0) != task_id:
            errors.append("task_id does not match request task")
        if int(normalized.get("session_id") or 0) != int(task_row["session_id"]):
            errors.append("session_id does not match task session")
        steps = normalized.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append("steps must be a non-empty list")
            steps = []
        registry = tool_registry_by_id(workflow_id)
        step_ids: set[str] = set()
        enabled_by_id: dict[str, bool] = {}
        high_cost_steps: list[str] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"steps[{index}] must be an object")
                continue
            step_id = str(step.get("id") or "").strip()
            tool_id = str(step.get("tool_id") or "").strip()
            if not step_id:
                errors.append(f"steps[{index}].id is required")
            elif step_id in step_ids:
                errors.append(f"duplicate step id: {step_id}")
            else:
                step_ids.add(step_id)
            if not tool_id:
                errors.append(f"steps[{index}].tool_id is required")
            elif tool_id not in registry:
                errors.append(f"steps[{index}].tool_id does not exist in Tool Registry: {tool_id}")
            enabled = step.get("enabled")
            if not isinstance(enabled, bool):
                errors.append(f"steps[{index}].enabled must be boolean")
            enabled_by_id[step_id] = bool(enabled)
            if not isinstance(step.get("args"), dict):
                errors.append(f"steps[{index}].args must be an object")
            if not isinstance(step.get("depends_on") or [], list):
                errors.append(f"steps[{index}].depends_on must be a list")
            if not isinstance(step.get("expected_outputs") or [], list):
                errors.append(f"steps[{index}].expected_outputs must be a list")
            registry_tool = registry.get(tool_id) or {}
            if registry_tool:
                step["tool_name"] = registry_tool.get("name") or step.get("tool_name") or tool_id
                step["cost_level"] = registry_tool.get("cost_level") or step.get("cost_level") or "medium"
                step["uses_llm"] = bool(registry_tool.get("uses_llm"))
                step["uses_vlm"] = bool(registry_tool.get("uses_vlm"))
                if not step.get("expected_outputs"):
                    step["expected_outputs"] = registry_tool.get("main_outputs") or []
            is_high_cost = bool(step.get("uses_llm")) or bool(step.get("uses_vlm")) or str(step.get("cost_level") or "") == "very_high"
            if is_high_cost:
                high_cost_steps.append(step_id or f"steps[{index}]")
                step["requires_confirmation"] = True
            if is_high_cost and require_high_cost_ack and step_id not in (acknowledged_step_ids or set()):
                errors.append(f"high-cost step requires explicit acknowledgement: {step_id}")
            for key, value in (step.get("args") or {}).items():
                if "path" not in str(key).lower() and key not in {"workspace", "output_dir", "input", "output"}:
                    continue
                text = str(value)
                if ".." in Path(text).parts:
                    errors.append(f"steps[{index}].args.{key} contains unsafe path traversal")
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not step.get("enabled"):
                continue
            for dep in step.get("depends_on") or []:
                dep_id = str(dep)
                if dep_id in enabled_by_id and not enabled_by_id[dep_id]:
                    warnings.append(f"enabled step {step.get('id')} depends on disabled step {dep_id}")
        normalized["steps"] = steps
        normalized.setdefault("status", "draft")
        normalized.setdefault("created_by", "assistant")
        normalized.setdefault("created_at", now_ms())
        normalized.setdefault("confirmed_at", None)
        if errors:
            raise HTTPException(status_code=400, detail={"message": "Plan validation failed", "validation_errors": errors, "warnings": warnings})
        return normalized, [], warnings

    def save_plan(workflow_id: str, task_id: int, body: dict[str, Any]) -> dict[str, Any]:
        task_row = resolve_workflow_task(workflow_id, task_id)
        raw_plan = body.get("plan") if isinstance(body, dict) and "plan" in body else body
        plan, validation_errors, warnings = validate_plan(workflow_id, task_id, task_row, raw_plan)
        plan["status"] = "draft"
        created = now_ms()
        existing = current_plan_row(workflow_id, task_id)
        source_message_id = str(body.get("source_message_id") or "").strip() if isinstance(body, dict) else ""
        with ctx.engine.begin() as conn:
            if existing and str(existing.get("status") or "") != "confirmed":
                conn.execute(
                    update(workflow_plans)
                    .where(workflow_plans.c.id == int(existing["id"]))
                    .values(status="draft", plan_json=json.dumps(plan, ensure_ascii=True), created_by_message_id=source_message_id or existing.get("created_by_message_id"), updated_at=created)
                )
                plan_id = int(existing["id"])
                event_kind = "workflow.plan.updated"
            else:
                result = conn.execute(
                    workflow_plans.insert()
                    .values(
                        workflow_id=workflow_id,
                        task_id=task_id,
                        session_id=int(task_row["session_id"]),
                        status="draft",
                        plan_json=json.dumps(plan, ensure_ascii=True),
                        created_by_message_id=source_message_id or None,
                        created_at=created,
                        updated_at=created,
                    )
                    .returning(workflow_plans.c.id)
                )
                plan_id = int(result.scalar_one())
                event_kind = "workflow.plan.created"
        write_plan_audit_file(task_row, plan)
        row = current_plan_row(workflow_id, task_id)
        payload = plan_payload_from_row(row)
        add_workflow_event(int(task_row["session_id"]), event_kind, {"workflow_id": workflow_id, "task_id": task_id, "session_id": int(task_row["session_id"]), "plan_id": plan_id, "plan": plan})
        return {"plan": payload, "validation_errors": validation_errors, "warnings": warnings}

    def confirm_plan(workflow_id: str, task_id: int, body: dict[str, Any]) -> dict[str, Any]:
        task_row = resolve_workflow_task(workflow_id, task_id)
        row = current_plan_row(workflow_id, task_id)
        if not row:
            raise HTTPException(status_code=404, detail="No plan to confirm")
        plan = json.loads(str(row.get("plan_json") or "{}"))
        acknowledged = {str(item) for item in (body.get("acknowledged_high_cost_step_ids") or [])} if isinstance(body, dict) else set()
        plan, validation_errors, warnings = validate_plan(workflow_id, task_id, task_row, plan, require_high_cost_ack=True, acknowledged_step_ids=acknowledged)
        confirmed = now_ms()
        plan["status"] = "confirmed"
        plan["confirmed_at"] = confirmed
        confirmed_by_message_id = str(body.get("confirmed_by_message_id") or "").strip() if isinstance(body, dict) else ""
        with ctx.engine.begin() as conn:
            conn.execute(
                update(workflow_plans)
                .where(workflow_plans.c.id == int(row["id"]))
                .values(status="confirmed", plan_json=json.dumps(plan, ensure_ascii=True), confirmed_by_message_id=confirmed_by_message_id or None, updated_at=confirmed, confirmed_at=confirmed)
            )
        write_plan_audit_file(task_row, plan)
        writeback_warning = ""
        opencode_session_id = str(task_row.get("opencode_session_id") or "")
        if opencode_session_id:
            writeback = "[Workflow State Update]\nThe user has confirmed the following execution plan. Treat this as the authoritative plan for all future conversation in this session.\n\n" + json.dumps(plan, ensure_ascii=False, indent=2)
            try:
                opencode_client_for(task_row).prompt_async(opencode_session_id, writeback, system="Record this workflow state update. Do not execute tools.")
            except Exception as exc:
                writeback_warning = f"OpenCode writeback failed: {exc}"
                warnings.append(writeback_warning)
        payload = plan_payload_from_row(current_plan_row(workflow_id, task_id))
        add_workflow_event(int(task_row["session_id"]), "workflow.plan.confirmed", {"workflow_id": workflow_id, "task_id": task_id, "session_id": int(task_row["session_id"]), "plan_id": int(row["id"]), "plan": plan, "writeback_warning": writeback_warning})
        return {"plan": payload, "validation_errors": validation_errors, "warnings": warnings}

    def load_agent_guide_summary(warnings: list[str], workflow_id: str = "openclip_analysis") -> str:
        try:
            path = resolve_workflow_tool_library(workflow_id)["agent_guide"]
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to load Agent Guide: {exc}") from exc
        keep: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#") or "confirmation" in stripped.lower() or "VLM" in stripped or "LLM" in stripped or "Run `" in stripped or "Skip" in stripped or "Cost" in stripped or "Recommended" in stripped:
                keep.append(stripped)
            if len("\n".join(keep)) > 5000:
                break
        return "\n".join(keep[:120])

    def workspace_state_summary(task_row: dict[str, Any], registry_summary: list[dict[str, Any]]) -> dict[str, Any]:
        root = Path(str(task_row.get("workspace_dir") or ""))
        existing: list[str] = []
        if root.exists() and root.is_dir():
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                if rel.startswith("history/") or "/." in rel or Path(rel).name.startswith("."):
                    continue
                existing.append(rel)
                if len(existing) >= 300:
                    break
        expected: list[str] = []
        for tool in registry_summary:
            for rel in tool.get("main_outputs") or []:
                if isinstance(rel, str) and rel and not rel.endswith("/"):
                    expected.append(rel)
        existing_set = set(existing)
        missing = [rel for rel in dict.fromkeys(expected) if rel not in existing_set][:120]
        shallow: list[str] = []
        if root.exists() and root.is_dir():
            for child in sorted(root.iterdir(), key=lambda item: item.name)[:80]:
                shallow.append(f"{child.name}{'/' if child.is_dir() else ''}")
        return {
            "existing_outputs": existing,
            "missing_expected_outputs": missing,
            "tree_summary": "\n".join(shallow),
            "workspace_exists": root.exists() and root.is_dir(),
        }

    def build_openclip_assistant_context(workflow_id: str, task_id: int) -> tuple[dict[str, Any], list[str]]:
        task_row = resolve_workflow_task(workflow_id, task_id)
        warnings: list[str] = []
        registry_summary = load_tool_registry_summary(warnings, workflow_id)
        guide_summary = load_agent_guide_summary(warnings, workflow_id)
        run_provider = str(task_row.get("run_model_provider") or "").strip()
        run_model_id = str(task_row.get("run_model_id") or "").strip()
        context = {
            "workflow": {"id": workflow_id, "name": workflow_config(workflow_id)["name"]},
            "task": {
                "id": int(task_row["id"]),
                "title": str(task_row.get("title") or f"OpenClip Task #{task_row['id']}"),
                "status": str(task_row.get("status") or "draft"),
            },
            "session": {
                "id": int(task_row["session_id"]),
                "opencode_session_id": str(task_row.get("opencode_session_id") or ""),
                "workspace_dir": str(task_row.get("workspace_dir") or ""),
            },
            "business_context": {
                "final_prompt": str(task_row.get("final_prompt") or ""),
                "run_model": f"{run_provider}/{run_model_id}" if run_provider or run_model_id else "",
                "reference_video_path": str(task_row.get("reference_video_path") or ""),
                "industry": str(task_row.get("industry") or ""),
                "persona": str(task_row.get("persona") or ""),
                "target_audience": str(task_row.get("target_audience") or ""),
                "product_info": str(task_row.get("product_info") or ""),
                "constraints": str(task_row.get("constraints") or ""),
                "analysis_goal": str(task_row.get("analysis_goal") or ""),
                "video_formula": str(task_row.get("video_formula") or ""),
            },
            "workspace_state": workspace_state_summary(task_row, registry_summary),
            "tool_library": {
                "source": workflow_tool_library_source(workflow_id),
                "registry_summary": registry_summary,
                "agent_guide_summary": guide_summary,
            },
            "current_plan": plan_payload_from_row(current_plan_row(workflow_id, task_id)),
            "current_run": None,
        }
        if workflow_config(workflow_id)["task_adapter"] == "oc_rebuild":
            analysis_task = openclip_repo.get_task(int(task_row.get("analysis_task_id") or 0)) if task_row.get("analysis_task_id") else None
            context["business_context"] = {
                "analysis_task_id": int(task_row.get("analysis_task_id") or 0) or None,
                "analysis_session_id": int(analysis_task.get("session_id")) if analysis_task else None,
                "analysis_workspace_dir": str(analysis_task.get("workspace_dir") or "") if analysis_task else "",
                "source_scheme": str(task_row.get("source_scheme") or "detail"),
                "source_package_path": str(task_row.get("source_package_path") or "source_package.json"),
                "target_topic": str(task_row.get("target_topic") or ""),
                "target_platform": str(task_row.get("target_platform") or ""),
                "aspect_ratio": str(task_row.get("aspect_ratio") or ""),
                "target_count": int(task_row.get("target_count") or 0),
                "target_audience": str(task_row.get("target_audience") or ""),
                "product_info": str(task_row.get("product_info") or ""),
                "rebuild_goal": str(task_row.get("rebuild_goal") or ""),
                "simple_prompt": str(task_row.get("simple_prompt") or ""),
                "final_intent_prompt": str(task_row.get("final_prompt") or ""),
            }
        return context, warnings

    def quick_prompts_for_workflow(workflow_id: str) -> list[dict[str, Any]]:
        if workflow_id in {"oc_rebuild_plan_a_phase_batch_v0", "oc_rebuild_plan_a_shot_first_v1"}:
            return OC_REBUILD_PLAN_A_QUICK_PROMPTS
        if workflow_id == "oc_rebuild":
            return OC_REBUILD_QUICK_PROMPTS
        return OPENCLIP_QUICK_PROMPTS

    def assistant_addition_for_workflow(workflow_id: str) -> str:
        if workflow_id == "oc_rebuild_plan_a_phase_batch_v0":
            return OC_REBUILD_PLAN_A_PHASE_BATCH_ADDITION
        if workflow_id == "oc_rebuild_plan_a_shot_first_v1":
            return OC_REBUILD_PLAN_A_SHOT_FIRST_ADDITION
        if workflow_id == "oc_rebuild":
            return OC_REBUILD_ASSISTANT_ADDITION
        return OPENCLIP_ASSISTANT_ADDITION

    def build_assistant_system_prompt(context: dict[str, Any]) -> str:
        compact_context = json.dumps(context, ensure_ascii=False, indent=2)
        addition = assistant_addition_for_workflow(str(context.get("workflow", {}).get("id") or "openclip_analysis"))
        return f"{WORKFLOW_ASSISTANT_BASE_PROMPT}\n\n{addition}\n\n[Workflow Context]\n{compact_context}"

    def assistant_bootstrap_payload(workflow_id: str, task_id: int) -> dict[str, Any]:
        config = workflow_config(workflow_id)
        task_row = resolve_workflow_task(workflow_id, task_id)
        context, warnings = build_openclip_assistant_context(workflow_id, task_id)
        messages: list[dict[str, Any]] = []
        opencode_session_id = str(task_row.get("opencode_session_id") or "")
        can_chat = bool(opencode_session_id)
        if opencode_session_id:
            try:
                messages = opencode_client_for(task_row).messages(opencode_session_id, limit=120)
            except Exception as exc:
                can_chat = False
                warnings.append(f"OpenCode messages unavailable: {exc}")
        return {
            "workflow": {"id": config["id"], "name": config["name"], "source": config["source"]},
            "task": context["task"],
            "session": context["session"],
            "context": context,
            "quick_prompts": quick_prompts_for_workflow(workflow_id),
            "messages": messages,
            "plan": context["current_plan"],
            "run": None,
            "capabilities": {"can_chat": can_chat, "can_abort": bool(opencode_session_id), "can_execute": False},
            "warnings": warnings,
        }

    @router.get("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/bootstrap")
    @router.get("/api/openclip/tasks/{task_id}/assistant/bootstrap")
    async def workflow_assistant_bootstrap(task_id: int, workflow_id: str = "openclip_analysis") -> dict[str, Any]:
        return assistant_bootstrap_payload(workflow_id, task_id)

    @router.get("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/quick-prompts")
    @router.get("/api/openclip/tasks/{task_id}/assistant/quick-prompts")
    async def workflow_assistant_quick_prompts(task_id: int, workflow_id: str = "openclip_analysis") -> dict[str, Any]:
        resolve_workflow_task(workflow_id, task_id)
        return {"items": quick_prompts_for_workflow(workflow_id)}

    @router.get("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/plan")
    @router.get("/api/openclip/tasks/{task_id}/assistant/plan")
    async def workflow_assistant_get_plan(task_id: int, workflow_id: str = "openclip_analysis") -> dict[str, Any]:
        resolve_workflow_task(workflow_id, task_id)
        return {"plan": plan_payload_from_row(current_plan_row(workflow_id, task_id)), "validation_errors": [], "warnings": []}

    @router.put("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/plan")
    @router.put("/api/openclip/tasks/{task_id}/assistant/plan")
    async def workflow_assistant_put_plan(task_id: int, body: dict[str, Any], workflow_id: str = "openclip_analysis") -> dict[str, Any]:
        return save_plan(workflow_id, task_id, body)

    @router.post("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/plan/confirm")
    @router.post("/api/openclip/tasks/{task_id}/assistant/plan/confirm")
    async def workflow_assistant_confirm_plan(task_id: int, body: Optional[dict[str, Any]] = None, workflow_id: str = "openclip_analysis") -> dict[str, Any]:
        return confirm_plan(workflow_id, task_id, body or {})

    @router.post("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/execute")
    @router.post("/api/openclip/tasks/{task_id}/assistant/execute")
    async def workflow_assistant_execute_guard(task_id: int, workflow_id: str = "openclip_analysis") -> dict[str, Any]:
        resolve_workflow_task(workflow_id, task_id)
        raise HTTPException(status_code=501, detail="Workflow Plan Runner is not implemented until P5")

    @router.get("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/messages")
    @router.get("/api/openclip/tasks/{task_id}/assistant/messages")
    async def workflow_assistant_messages(task_id: int, workflow_id: str = "openclip_analysis") -> dict[str, Any]:
        task_row = resolve_workflow_task(workflow_id, task_id)
        opencode_session_id = str(task_row.get("opencode_session_id") or "")
        if not opencode_session_id:
            raise HTTPException(status_code=400, detail="Session is not bound to OpenCode")
        try:
            return {"items": opencode_client_for(task_row).messages(opencode_session_id, limit=160)}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"OpenCode messages unavailable: {exc}") from exc

    @router.post("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/message")
    @router.post("/api/openclip/tasks/{task_id}/assistant/message")
    async def workflow_assistant_message(task_id: int, body: dict[str, Any], workflow_id: str = "openclip_analysis") -> dict[str, Any]:
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message is required")
        task_row = resolve_workflow_task(workflow_id, task_id)
        opencode_session_id = str(task_row.get("opencode_session_id") or "")
        if not opencode_session_id:
            raise HTTPException(status_code=400, detail="Session is not bound to OpenCode")
        context, _warnings = build_openclip_assistant_context(workflow_id, task_id)
        provider = str(task_row.get("run_model_provider") or "").strip()
        model_id = str(task_row.get("run_model_id") or "").strip()
        model = {"providerID": provider, "modelID": model_id} if provider and model_id else None
        try:
            opencode_client_for(task_row).prompt_async(opencode_session_id, text, model=model, system=build_assistant_system_prompt(context))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"OpenCode prompt failed: {exc}") from exc
        return {"ok": True}

    @router.post("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/abort")
    @router.post("/api/openclip/tasks/{task_id}/assistant/abort")
    async def workflow_assistant_abort(task_id: int, workflow_id: str = "openclip_analysis") -> dict[str, Any]:
        task_row = resolve_workflow_task(workflow_id, task_id)
        opencode_session_id = str(task_row.get("opencode_session_id") or "")
        if not opencode_session_id:
            raise HTTPException(status_code=400, detail="Session is not bound to OpenCode")
        try:
            opencode_client_for(task_row).abort(opencode_session_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"OpenCode abort failed: {exc}") from exc
        return {"ok": True}

    @router.get("/api/workflows/{workflow_id}/tasks/{task_id}/assistant/events")
    @router.get("/api/openclip/tasks/{task_id}/assistant/events")
    async def workflow_assistant_events(task_id: int, workflow_id: str = "openclip_analysis", since: int = Query(default=0)) -> StreamingResponse:
        task_row = resolve_workflow_task(workflow_id, task_id)
        opencode_session_id = str(task_row.get("opencode_session_id") or "")
        if not opencode_session_id:
            raise HTTPException(status_code=400, detail="Session is not bound to OpenCode")
        event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        stop_event = threading.Event()

        def on_opencode_event(payload: dict[str, Any]) -> None:
            properties = payload.get("properties")
            event_queue.put({
                "type": str(payload.get("type") or "opencode.event"),
                "source": "opencode",
                "properties": properties if isinstance(properties, dict) else {},
                "created_at": now_ms(),
            })

        try:
            client = opencode_client_for(task_row)
            collector = threading.Thread(target=client.collect_events, args=(opencode_session_id, stop_event, on_opencode_event), daemon=True)
            collector.start()
        except Exception as exc:
            event_queue.put({"type": "session.stream.error", "source": "opencode", "properties": {"message": str(exc)}, "created_at": now_ms()})

        async def event_generator() -> Any:
            cursor = since
            try:
                while True:
                    sent = False
                    while True:
                        try:
                            event = event_queue.get_nowait()
                        except queue.Empty:
                            break
                        sent = True
                        yield f"data: {json.dumps(event, ensure_ascii=True)}\n\n"
                    for row in ctx.session_repo.list_events(int(task_row["session_id"]), cursor, 100):
                        cursor = max(cursor, int(row["id"]))
                        kind = str(row.get("kind") or "")
                        if not kind.startswith("workflow."):
                            continue
                        try:
                            properties = json.loads(row.get("payload") or "{}")
                        except Exception:
                            properties = {}
                        sent = True
                        event = {"type": kind, "source": "workflow", "properties": properties, "created_at": int(row.get("created_at") or 0)}
                        yield f"data: {json.dumps(event, ensure_ascii=True)}\n\n"
                    if not sent:
                        yield ": keepalive\n\n"
                    await asyncio.sleep(1)
            finally:
                stop_event.set()

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return router
