from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from ..context import now_ms
from ..media_library_features import require_media_library_feature
from ..media_library_search import MediaLibraryFragmentPublisher
from ..tool_sessions import (
    PrepareInputFile,
    PrepareSessionVariablesInput,
    SubprocessToolAdapter,
    ToolSessionRunner,
    ToolSessionService,
)
from ..tool_sessions.registry_normalizer import normalize_registry_file
from .contracts import publish_dialogue_contract
from .lifecycle import finalize_analysis_tool_session, result_sync_error
from .run_repository import AnalysisRunRepository


OPENCREW_ROOT = Path(__file__).resolve().parents[3]
OPEN_CUT_ROOT = OPENCREW_ROOT / "ToolLibrary" / "OpenCut_V1"
OPEN_CUT_REGISTRY = OPEN_CUT_ROOT / "tool_registry.json"
WORKFLOW_ID = "open-cut-v1-dialogue"
ACTIVE_STATUSES = {"queued", "running", "processing"}
STEPS = (
    ("S1", "01", 1, "正在解析视频"),
    ("S2", "02_01", 2, "正在识别对白"),
    ("S3", "02_02", 3, "正在校准对白和关键帧"),
)
TOTAL_STEPS = len(STEPS) + 1
BLOCKED_USER_MESSAGES = {
    "cloud_asr_data_transfer_not_authorized": (
        "当前对白分析使用云端 ASR，但本次运行尚未获得音频数据传输授权。"
        "请勾选“允许本次运行使用云端 ASR”后重新运行，或改用本地 ASR。"
    ),
    "video_has_no_audio": (
        "源视频没有音轨，无法进行对白识别。"
        "可以继续运行画面结构和视觉语义分析；若需要对白检索，请上传含音轨的版本。"
    ),
}

DIALOGUE_BLOCKED_DETAILS = {
    "cloud_asr_data_transfer_not_authorized": {
        "label": "对白分析等待授权",
        "suggested_action": "勾选“允许本次运行使用云端 ASR”后重新运行，或改用本地 ASR。",
    },
    "video_has_no_audio": {
        "label": "对白分析不可用（无音轨）",
        "suggested_action": "无需调整 ASR 授权；请运行画面分析。若需对白检索，请上传含音轨版本。",
    },
}


def dialogue_failure_code(message: str, *, blocked: bool) -> str:
    for code in DIALOGUE_BLOCKED_DETAILS:
        if code in message:
            return code
    if "源视频没有音轨" in message or "source video has no audio track" in message.lower():
        return "video_has_no_audio"
    return "analysis_blocked" if blocked else "analysis_execution_failed"


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


def tool_result_failure_message(result: Any, tool_id: str) -> str:
    if str(getattr(result, "status", "") or "") == "blocked":
        outputs = getattr(result, "outputs", {}) or {}
        dependency_check = outputs.get("dependency_check") if isinstance(outputs, dict) else None
        missing = dependency_check.get("missing_dependencies") if isinstance(dependency_check, dict) else None
        messages: list[str] = []
        for item in missing if isinstance(missing, list) else []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("kind") or item.get("code") or "").strip()
            message = BLOCKED_USER_MESSAGES.get(code) or str(
                item.get("suggested_action") or item.get("message") or code
            ).strip()
            if not message:
                continue
            messages.append(f"{code}: {message}" if code and code not in message else message)
        if messages:
            return "; ".join(dict.fromkeys(messages))
    errors = [
        str(item).strip()
        for item in (getattr(result, "errors", None) or [])
        if str(item).strip()
    ]
    return "; ".join(dict.fromkeys(errors)) or f"工具 {tool_id} 返回 {getattr(result, 'status', 'failed')}"


def _iso_timestamp_ms(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def enrich_dialogue_progress_timing(*, workspace: Path, tool_use_session_id: str, progress: dict[str, Any] | None) -> dict[str, Any]:
    """Backfill timing for runs created before dialogue progress stored timestamps."""
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


def _calibrated_index(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "SessionOutput" / "subtitle" / "calibrated_srt_items.json"
    if not path.is_file():
        return {}
    payload = _read_json(path)
    return {
        str(item.get("sentence_id") or ""): item
        for item in payload.get("items") or []
        if isinstance(item, dict) and str(item.get("sentence_id") or "")
    }


def load_dialogue_result(*, workspace: Path, session_id: int, tool_use_session_id: str, preview_url: str = "") -> dict[str, Any]:
    root = _tool_root(workspace, tool_use_session_id)
    final_path = root / "SessionOutput" / "subtitle" / "final_srt_frame_items.json"
    if not final_path.is_file():
        return {"items": [], "error": "对白分析结果文件不存在。"}
    try:
        final_payload = _read_json(final_path)
        calibrated = _calibrated_index(root)
    except Exception as exc:
        return {"items": [], "error": f"对白分析结果无法读取：{exc}"}

    items: list[dict[str, Any]] = []
    for index, raw in enumerate(final_payload.get("items") or [], start=1):
        if not isinstance(raw, dict):
            continue
        unit_id = str(raw.get("srt_id") or f"srt_{index:04d}")
        evidence = calibrated.get(unit_id) or {}
        calibration = evidence.get("calibration") if isinstance(evidence.get("calibration"), dict) else {}
        image_path = str(raw.get("image_path") or evidence.get("frame_path") or "").lstrip("/")
        workspace_image = f"tool_use_sessions/{tool_use_session_id}/{image_path}" if image_path else ""
        frame_time = evidence.get("frame_time")
        keyframes = []
        if workspace_image:
            keyframes.append({
                "id": f"{unit_id}-keyframe",
                "time": frame_time,
                "image_url": _raw_url(session_id, workspace_image),
                "path": image_path,
            })
        needs_review = bool(calibration.get("needs_review"))
        items.append({
            "fragment_id": unit_id,
            "title": f"对白片段 {index}",
            "dialogue_text": str(raw.get("dialogue") or evidence.get("text") or ""),
            "start": float(raw.get("start") or 0.0),
            "end": float(raw.get("end") or raw.get("start") or 0.0),
            "duration": float(raw.get("duration") or 0.0),
            "preview_url": preview_url,
            "keyframes": keyframes,
            "usability": "review" if needs_review else "keep",
            "needs_review": needs_review,
            "parent_sentence_id": evidence.get("parent_sentence_id"),
        })
    return {"items": items, "error": None}


def summarize_dialogue_output(root: Path) -> dict[str, Any]:
    final_payload = _read_json(root / "SessionOutput" / "subtitle" / "final_srt_frame_items.json")
    calibrated_path = root / "SessionOutput" / "subtitle" / "calibrated_srt_items.json"
    calibrated_payload = _read_json(calibrated_path) if calibrated_path.is_file() else {"items": []}
    calibrated_items = [item for item in calibrated_payload.get("items") or [] if isinstance(item, dict)]
    embedded = any(
        str(item.get("ocr_text") or "").strip() and float(item.get("ocr_confidence") or 0.0) >= 0.5
        for item in calibrated_items
    )
    items = [item for item in final_payload.get("items") or [] if isinstance(item, dict)]
    summary = " ".join(str(item.get("dialogue") or "").strip() for item in items[:3]).strip()
    return {
        "fragment_count": len(items),
        "subtitle_mode": "embedded" if embedded else "none",
        "dialogue_summary": summary or None,
    }


class OpenCutDialogueService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.asset_repo = ctx.media_library_repo
        self.task_repo = ctx.media_library_task_repo
        self.run_repo = getattr(ctx, "media_analysis_run_repo", None)
        if self.run_repo is None and getattr(ctx, "engine", None) is not None:
            self.run_repo = AnalysisRunRepository(ctx.engine)
        self.fragment_publisher = getattr(
            ctx, "media_library_fragment_publisher", None
        )
        if (
            self.fragment_publisher is None
            and getattr(ctx, "engine", None) is not None
        ):
            self.fragment_publisher = MediaLibraryFragmentPublisher(ctx.engine)

    def start(
        self,
        asset_id: str,
        *,
        force: bool = False,
        allow_cloud_asr_data_transfer: bool = False,
    ) -> dict[str, Any]:
        require_media_library_feature("analysis_runs")
        asset = self.asset_repo.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail={"code": "media_asset_not_found", "message": "素材不存在或已删除。"})
        task = self.task_repo.get_by_asset(asset_id)
        if task is None:
            raise HTTPException(status_code=409, detail={"code": "open_cut_task_missing", "message": "素材对应的 OpenCut Task 不存在。"})
        if str(task.get("dialogue_status") or "") in ACTIVE_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "open_cut_dialogue_active", "message": "对白分析已经在运行。"})
        if str(task.get("dialogue_status") or "") == "ready" and not force:
            raise HTTPException(status_code=409, detail={"code": "open_cut_dialogue_exists", "message": "对白分析已经完成；如需重跑请使用重新运行。"})
        session_id = int(task.get("session_id") or 0)
        session = self.ctx.session_repo.get(session_id)
        if session is None:
            raise HTTPException(status_code=409, detail={"code": "media_session_missing", "message": "素材 Session 不存在。"})
        workspace = Path(str(session.get("workspace_dir") or ""))
        source_rel = str(asset.get("source_video_path") or "")
        if not source_rel or not (workspace / source_rel).is_file():
            raise HTTPException(status_code=409, detail={"code": "media_source_missing", "message": "素材源视频不存在，无法开始对白分析。"})

        timestamp = now_ms()
        initial_progress = {
            "step": "prepare",
            "label": "正在准备 Session",
            "completed": 0,
            "total": TOTAL_STEPS,
            "started_at": timestamp,
            "updated_at": timestamp,
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
            scheme="dialogue",
            timestamp=timestamp,
            progress=initial_progress,
        )
        analysis_run_id = str(business_run["analysis_run_id"])
        thread = threading.Thread(
            target=self._run,
            kwargs={
                "asset": asset,
                "task": task,
                "session": session,
                "force": force,
                "allow_cloud_asr_data_transfer": allow_cloud_asr_data_transfer,
                "started_at": timestamp,
                "analysis_run_id": analysis_run_id,
            },
            name=f"open-cut-dialogue-{task['id']}",
            daemon=True,
        )
        thread.start()
        return {
            "status": "queued",
            "task_id": int(task["id"]),
            "session_id": session_id,
            "analysis_run_id": analysis_run_id,
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
        allow_cloud_asr_data_transfer: bool,
        started_at: int,
        analysis_run_id: str,
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
                    selected_scheme="dialogue",
                    allow_cloud_asr_data_transfer=allow_cloud_asr_data_transfer,
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
            runner = ToolSessionRunner(
                workspace_dir=workspace,
                tool_use_session_id=tool_use_session_id,
                session_id=session_id,
                event_sink=self.ctx.session_event_service,
            )
            adapter = SubprocessToolAdapter(
                repo_root=OPENCREW_ROOT,
                extra_env={"OPENCREW_DATA_DIR": str(self.ctx.data_dir)},
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
                result = runner.run_registry_step(
                    step_id=step_id,
                    tool_id=tool_id,
                    normalized_registry=registry,
                    step_index=step_index,
                    adapters={"*": adapter},
                    force_rerun=force,
                )
                if result.status != "completed":
                    terminal_status = "blocked" if result.status == "blocked" else "failed"
                    raise RuntimeError(tool_result_failure_message(result, tool_id))

            tool_root = _tool_root(workspace, tool_use_session_id)
            published, published_hash, published_rel = publish_dialogue_contract(
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
            summary = summarize_dialogue_output(tool_root)
            sync_result = finalize_analysis_tool_session(self.ctx, runner, terminal_status="completed")
            run_finalized = True
            if sync_result.status != "completed":
                raise RuntimeError(f"对白分析结果登记失败：{result_sync_error(sync_result)}")
            finished_at = now_ms()
            completed_progress = {
                "step": "completed",
                "label": "对白分析已完成",
                "completed": TOTAL_STEPS,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": finished_at,
                "finished_at": finished_at,
                "elapsed_ms": max(0, finished_at - started_at),
            }
            if self.fragment_publisher is None:
                raise RuntimeError("media_library_fragment_publisher_unavailable")
            self.fragment_publisher.publish_dialogue(
                asset_id=asset_id,
                analysis_run_id=analysis_run_id,
                result_hash=published_hash,
                fragments=published["items"],
                timestamp=finished_at,
                result_index_path=(
                    f"tool_use_sessions/{tool_use_session_id}/{published_rel}"
                ),
                progress=completed_progress,
            )
            self.asset_repo.update_dialogue_analysis(
                asset_id,
                status=None,
                updated_at=now_ms(),
                fragment_count=int(summary["fragment_count"]),
                subtitle_mode=str(summary["subtitle_mode"]),
                dialogue_summary=summary.get("dialogue_summary"),
            )
            self.ctx.session_event_service.add_event(
                session_id,
                "open_cut.dialogue.completed",
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
                "label": "对白分析等待授权"
                if business_status == "blocked"
                else "对白分析失败",
                "completed": 0,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": finished_at,
                "finished_at": finished_at,
                "elapsed_ms": max(0, finished_at - started_at),
            }
            error_code = dialogue_failure_code(
                message,
                blocked=business_status == "blocked",
            )
            blocked_detail = DIALOGUE_BLOCKED_DETAILS.get(error_code, {})
            public_message = BLOCKED_USER_MESSAGES.get(error_code, message)
            if business_status == "blocked" and blocked_detail:
                failed_progress["label"] = str(blocked_detail["label"])
            self.run_repo.finish_unsuccessful(
                analysis_run_id,
                status=business_status,
                timestamp=finished_at,
                error_code=error_code,
                error={
                    "code": error_code,
                    "user_message": public_message,
                    "suggested_action": str(
                        blocked_detail.get("suggested_action")
                        or (
                            "检查授权或依赖后重新运行。"
                            if business_status == "blocked"
                            else "检查失败步骤后重试。"
                        )
                    ),
                    "run_id": analysis_run_id,
                    "failed_step": failed_progress["step"],
                },
                progress=failed_progress,
            )
            self.ctx.session_event_service.add_event(
                session_id,
                f"open_cut.dialogue.{business_status}",
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
