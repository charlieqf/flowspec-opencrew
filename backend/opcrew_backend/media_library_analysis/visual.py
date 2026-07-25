from __future__ import annotations

import json
import re
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from ..context import now_ms
from ..media_library_features import require_media_library_feature
from ..tool_sessions import PrepareInputFile, PrepareSessionVariablesInput, SubprocessToolAdapter, ToolSessionRunner, ToolSessionService
from ..tool_sessions.registry_normalizer import normalize_registry_file
from .contracts import publish_visual_structure_contract
from .lifecycle import finalize_analysis_tool_session, result_sync_error
from .run_repository import AnalysisRunRepository


OPENCREW_ROOT = Path(__file__).resolve().parents[3]
OPEN_CUT_REGISTRY = OPENCREW_ROOT / "ToolLibrary" / "OpenCut_V1" / "tool_registry.json"
WORKFLOW_ID = "open-cut-v1-visual"
ACTIVE_STATUSES = {"queued", "running", "processing"}
STEPS = (
    ("S1", "01", 1, "正在解析视频"),
    ("S2", "03_01", 2, "正在检测镜头切换边界"),
    ("S3", "03_02", 3, "正在提取 Scene 关键帧"),
)
TOTAL_STEPS = len(STEPS) + 1


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _raw_url(session_id: int, relative_path: str) -> str:
    encoded = "/".join(quote(part, safe="") for part in relative_path.replace("\\", "/").split("/") if part)
    return f"/api/session-tasks/{session_id}/raw/{encoded}"


def _tool_root(workspace: Path, tool_use_session_id: str) -> Path:
    return workspace / "tool_use_sessions" / tool_use_session_id


def _iso_timestamp_ms(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def enrich_visual_progress_timing(*, workspace: Path, tool_use_session_id: str, progress: dict[str, Any] | None) -> dict[str, Any]:
    enriched = dict(progress or {})
    if enriched.get("started_at") and enriched.get("elapsed_ms") is not None:
        return enriched
    root = _tool_root(workspace, tool_use_session_id)
    if not root.is_dir():
        return enriched
    started_at = int(enriched.get("started_at") or 0)
    if not started_at:
        match = re.match(r"^tus_(\d{13})(?:_|$)", tool_use_session_id)
        if match:
            started_at = int(match.group(1))
    finished_candidates: list[int] = []
    for state_path in root.glob("S*_*/State.json"):
        try:
            state = _read_json(state_path)
        except Exception:
            continue
        timestamp = _iso_timestamp_ms(state.get("finished_at") or state.get("updated_at"))
        if timestamp:
            finished_candidates.append(timestamp)
    finished_at = max(finished_candidates, default=0)
    if started_at:
        enriched.setdefault("started_at", started_at)
    if finished_at and finished_at >= started_at:
        enriched.setdefault("updated_at", finished_at)
        enriched.setdefault("finished_at", finished_at)
        enriched.setdefault("elapsed_ms", finished_at - started_at)
    return enriched


def load_visual_result(*, workspace: Path, session_id: int, tool_use_session_id: str, preview_url: str = "") -> dict[str, Any]:
    root = _tool_root(workspace, tool_use_session_id)
    final_path = root / "SessionOutput" / "visual" / "final_scene_frame_items.json"
    if not final_path.is_file():
        return {"items": [], "error": "画面分析结果文件不存在。"}
    try:
        payload = _read_json(final_path)
    except Exception as exc:
        return {"items": [], "error": f"画面分析结果无法读取：{exc}"}
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(payload.get("items") or [], start=1):
        if not isinstance(raw, dict):
            continue
        scene_id = str(raw.get("scene_id") or f"scene_{index:04d}")
        raw_keyframes = raw.get("keyframes")
        if not isinstance(raw_keyframes, list):
            raw_keyframes = [
                {
                    "keyframe_id": f"{scene_id}-keyframe",
                    "keyframe_time": raw.get("keyframe_time"),
                    "image_path": raw.get("image_path"),
                }
            ]
        keyframes = []
        for raw_keyframe in raw_keyframes:
            if not isinstance(raw_keyframe, dict):
                continue
            image_path = str(raw_keyframe.get("image_path") or "").lstrip("/")
            if not image_path:
                continue
            workspace_image = (
                f"tool_use_sessions/{tool_use_session_id}/{image_path}"
            )
            keyframes.append(
                {
                    "id": str(
                        raw_keyframe.get("keyframe_id")
                        or f"{scene_id}-keyframe"
                    ),
                    "time": raw_keyframe.get("keyframe_time"),
                    "image_url": _raw_url(session_id, workspace_image),
                    "path": image_path,
                    "image_sha256": raw_keyframe.get("image_sha256"),
                }
            )
        raw_title = str(raw.get("title") or "").strip()
        public_title = (
            raw_title
            if raw_title and not re.fullmatch(r"scene[ _-]*\d+", raw_title, re.IGNORECASE)
            else f"画面片段 {index}"
        )
        items.append({
            "fragment_id": scene_id,
            "title": public_title,
            "summary": (
                "长镜头分析窗口"
                if str(raw.get("segment_kind") or "") == "long_scene_window"
                else "场景切分片段"
            ),
            "visual_summary": "",
            "start": float(raw.get("start") or 0.0),
            "end": float(raw.get("end") or raw.get("start") or 0.0),
            "duration": float(raw.get("duration") or 0.0),
            "preview_url": preview_url,
            "keyframes": keyframes,
            "representative_keyframe_id": (
                str(keyframes[1]["id"])
                if len(keyframes) >= 2
                else str(keyframes[0]["id"])
                if keyframes
                else None
            ),
            "sampling_strategy": str(
                raw.get("sampling_strategy") or "scene_midpoint_v1"
            ),
            "usability": str(raw.get("usability") or "detected"),
            "confidence": raw.get("confidence"),
            "source_detectors": raw.get("source_detectors") or [],
            "segment_kind": str(raw.get("segment_kind") or "detected_scene"),
        })
    return {"items": items, "error": None}


def summarize_visual_output(root: Path) -> dict[str, Any]:
    payload = _read_json(root / "SessionOutput" / "visual" / "final_scene_frame_items.json")
    items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    return {"fragment_count": len(items)}


class OpenCutVisualService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.asset_repo = ctx.media_library_repo
        self.task_repo = ctx.media_library_task_repo
        self.run_repo = getattr(ctx, "media_analysis_run_repo", None)
        if self.run_repo is None and getattr(ctx, "engine", None) is not None:
            self.run_repo = AnalysisRunRepository(ctx.engine)

    def start(
        self,
        asset_id: str,
        *,
        force: bool = False,
        continue_semantic: bool = False,
        allow_cloud_visual_data_transfer: bool = False,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        require_media_library_feature("analysis_runs")
        if continue_semantic:
            require_media_library_feature("visual_semantic")
        asset = self.asset_repo.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail={"code": "media_asset_not_found", "message": "素材不存在或已删除。"})
        task = self.task_repo.get_by_asset(asset_id)
        if task is None:
            raise HTTPException(status_code=409, detail={"code": "open_cut_task_missing", "message": "素材对应的 OpenCut Task 不存在。"})
        status = str(task.get("visual_status") or "")
        if status in ACTIVE_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "open_cut_visual_active", "message": "画面分析已经在运行。"})
        if status == "ready" and not force:
            raise HTTPException(status_code=409, detail={"code": "open_cut_visual_exists", "message": "画面分析已经完成；如需重跑请使用重新运行。"})
        session_id = int(task.get("session_id") or 0)
        session = self.ctx.session_repo.get(session_id)
        if session is None:
            raise HTTPException(status_code=409, detail={"code": "media_session_missing", "message": "素材 Session 不存在。"})
        workspace = Path(str(session.get("workspace_dir") or ""))
        source_rel = str(asset.get("source_video_path") or "")
        if not source_rel or not (workspace / source_rel).is_file():
            raise HTTPException(status_code=409, detail={"code": "media_source_missing", "message": "素材源视频不存在，无法开始画面分析。"})

        started_at = now_ms()
        initial_progress = {
            "step": "prepare",
            "label": "正在准备 Session",
            "completed": 0,
            "total": TOTAL_STEPS,
            "started_at": started_at,
            "updated_at": started_at,
            "elapsed_ms": 0,
        }
        if self.run_repo is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "analysis_run_repository_unavailable",
                    "user_message": "分析运行服务尚未就绪。",
                },
            )
        business_run = self.run_repo.create_queued(
            asset_id=asset_id,
            scheme="visual_structure",
            timestamp=started_at,
            progress=initial_progress,
        )
        analysis_run_id = str(business_run["analysis_run_id"])
        threading.Thread(
            target=self._run,
            kwargs={
                "asset": asset,
                "task": task,
                "session": session,
                "force": force,
                "started_at": started_at,
                "analysis_run_id": analysis_run_id,
                "continue_semantic": continue_semantic,
                "allow_cloud_visual_data_transfer": (
                    allow_cloud_visual_data_transfer
                ),
                "operation_id": operation_id,
            },
            name=f"open-cut-visual-{task['id']}", daemon=True,
        ).start()
        return {
            "status": "queued",
            "operation_id": operation_id
            or f"mlvo_{started_at}_{uuid.uuid4().hex[:10]}",
            "structure_run_id": analysis_run_id,
            "semantic_run_id": None,
        }

    def _progress(
        self,
        task_id: int,
        *,
        analysis_run_id: str,
        status: str,
        step: str,
        label: str,
        completed: int,
        started_at: int,
        tool_use_session_id: str | None = None,
    ) -> None:
        timestamp = now_ms()
        progress = {
            "step": step,
            "label": label,
            "completed": completed,
            "total": TOTAL_STEPS,
            "started_at": started_at,
            "updated_at": timestamp,
            "elapsed_ms": max(0, timestamp - started_at),
        }
        self.run_repo.mark_running(
            analysis_run_id,
            timestamp=timestamp,
            tool_use_session_id=tool_use_session_id,
            progress=progress,
        )

    def _run(
        self,
        *,
        asset: dict[str, Any],
        task: dict[str, Any],
        session: dict[str, Any],
        force: bool,
        started_at: int,
        analysis_run_id: str,
        continue_semantic: bool = False,
        allow_cloud_visual_data_transfer: bool = False,
        operation_id: str | None = None,
    ) -> None:
        asset_id = str(asset["asset_id"])
        task_id = int(task["id"])
        session_id = int(task["session_id"])
        workspace = Path(str(session["workspace_dir"]))
        tool_use_session_id = ""
        runner: ToolSessionRunner | None = None
        run_finalized = False
        terminal_status = "failed"
        try:
            prepared = ToolSessionService(event_sink=self.ctx.session_event_service).start(
                PrepareSessionVariablesInput(
                    workspace_dir=workspace,
                    workflow_id=WORKFLOW_ID,
                    task_id=task_id,
                    opencrew_session_id=session_id,
                    opencode_session_id=str(session.get("opencode_session_id") or ""),
                    selected_scheme="visual",
                    input_files=[PrepareInputFile(source_path=str(asset["source_video_path"]), target_name="Video_Source.mp4")],
                ),
                session_id=session_id,
            )
            tool_use_session_id = prepared.tool_use_session_id
            self._progress(
                task_id,
                analysis_run_id=analysis_run_id,
                status="running",
                step="prepare",
                label="Session 已准备",
                completed=1,
                started_at=started_at,
                tool_use_session_id=tool_use_session_id,
            )
            registry = normalize_registry_file(OPEN_CUT_REGISTRY, strict=True)
            runner = ToolSessionRunner(workspace_dir=workspace, tool_use_session_id=tool_use_session_id, session_id=session_id, event_sink=self.ctx.session_event_service)
            adapter = SubprocessToolAdapter(
                repo_root=OPENCREW_ROOT,
                extra_env={
                    "OPENCREW_DATA_DIR": str(self.ctx.data_dir),
                    "OPENCREW_OPENCUT_V1_PYTHON": sys.executable,
                },
            )
            for step_id, tool_id, step_index, label in STEPS:
                self._progress(
                    task_id,
                    analysis_run_id=analysis_run_id,
                    status="running",
                    step=tool_id,
                    label=label,
                    completed=step_index,
                    started_at=started_at,
                    tool_use_session_id=tool_use_session_id,
                )
                run_result = runner.run_registry_step(
                    step_id=step_id, tool_id=tool_id, normalized_registry=registry, step_index=step_index,
                    adapters={"*": adapter}, force_rerun=force,
                )
                if run_result.status != "completed":
                    terminal_status = "blocked" if run_result.status == "blocked" else "failed"
                    message = "; ".join(run_result.errors or []) or f"工具 {tool_id} 返回 {run_result.status}"
                    raise RuntimeError(message)
            tool_root = _tool_root(workspace, tool_use_session_id)
            published, published_hash, published_rel = (
                publish_visual_structure_contract(
                    tool_root=tool_root,
                    asset_id=asset_id,
                    source_version=str(asset["content_sha256"]),
                    analysis_run_id=analysis_run_id,
                    source_duration_ms=(
                        int(asset["duration_ms"])
                        if asset.get("duration_ms") is not None
                        else None
                    ),
                )
            )
            summary = summarize_visual_output(tool_root)
            sync_result = finalize_analysis_tool_session(self.ctx, runner, terminal_status="completed")
            run_finalized = True
            if sync_result.status != "completed":
                raise RuntimeError(f"画面分析结果登记失败：{result_sync_error(sync_result)}")
            finished_at = now_ms()
            completed_progress = {
                "step": "completed",
                "label": "画面结构分析已完成",
                "completed": TOTAL_STEPS,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": finished_at,
                "finished_at": finished_at,
                "elapsed_ms": max(0, finished_at - started_at),
            }
            self.run_repo.activate_ready(
                analysis_run_id,
                timestamp=finished_at,
                schema_version=str(published["schema_version"]),
                result_hash=published_hash,
                result_index_path=(
                    f"tool_use_sessions/{tool_use_session_id}/{published_rel}"
                ),
                progress=completed_progress,
            )
            self.asset_repo.update_visual_analysis(asset_id, status=None, updated_at=finished_at, fragment_count=int(summary["fragment_count"]))
            self.ctx.session_event_service.add_event(
                session_id, "open_cut.visual.completed",
                {
                    "asset_id": asset_id,
                    "task_id": task_id,
                    "analysis_run_id": analysis_run_id,
                    "tool_use_session_id": tool_use_session_id,
                    "result_hash": published_hash,
                    **summary,
                },
                workflow_id=WORKFLOW_ID,
            )
            if continue_semantic:
                try:
                    from .visual_semantic import VisualSemanticService

                    VisualSemanticService(self.ctx).start(
                        asset_id,
                        force=True,
                        allow_cloud_visual_data_transfer=(
                            allow_cloud_visual_data_transfer
                        ),
                        operation_id=operation_id,
                    )
                except Exception as semantic_exc:
                    self.ctx.session_event_service.add_event(
                        session_id,
                        "open_cut.visual_semantic.enqueue_failed",
                        {
                            "asset_id": asset_id,
                            "structure_run_id": analysis_run_id,
                            "error": str(semantic_exc).strip()
                            or semantic_exc.__class__.__name__,
                        },
                        workflow_id=WORKFLOW_ID,
                    )
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            sync_errors: list[str] = []
            if runner is not None and not run_finalized:
                try:
                    sync_result = finalize_analysis_tool_session(self.ctx, runner, terminal_status=terminal_status)
                    run_finalized = True
                    sync_errors = list(sync_result.errors)
                except Exception as sync_exc:
                    sync_errors = [str(sync_exc).strip() or sync_exc.__class__.__name__]
            finished_at = now_ms()
            business_status = "blocked" if terminal_status == "blocked" else "failed"
            failed_progress = {
                "step": business_status,
                "label": "画面结构分析被阻止"
                if business_status == "blocked"
                else "画面结构分析失败",
                "completed": 0,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": finished_at,
                "finished_at": finished_at,
                "elapsed_ms": max(0, finished_at - started_at),
            }
            error_code = (
                "analysis_blocked"
                if business_status == "blocked"
                else "analysis_execution_failed"
            )
            self.run_repo.finish_unsuccessful(
                analysis_run_id,
                status=business_status,
                timestamp=finished_at,
                error_code=error_code,
                error={
                    "code": error_code,
                    "user_message": message,
                    "suggested_action": "检查依赖后重新运行。",
                    "run_id": analysis_run_id,
                    "failed_step": failed_progress["step"],
                },
                progress=failed_progress,
            )
            self.ctx.session_event_service.add_event(
                session_id,
                f"open_cut.visual.{business_status}",
                {
                    "asset_id": asset_id,
                    "task_id": task_id,
                    "analysis_run_id": analysis_run_id,
                    "tool_use_session_id": tool_use_session_id,
                    "error": message,
                    "result_sync_errors": sync_errors,
                },
                workflow_id=WORKFLOW_ID,
            )
