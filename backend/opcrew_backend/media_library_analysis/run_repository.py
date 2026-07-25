from __future__ import annotations

import re
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from ..db.schema import (
    media_library_analysis_runs,
    media_library_assets,
    media_library_fragment_index,
    media_library_tasks,
    sessions,
)
from ..media_library_status import (
    ACTIVE_ANALYSIS_STATUSES,
    derive_asset_status,
    derive_task_status,
    derive_visual_status,
)
from ..repositories.base import row_to_dict
from .contracts import new_analysis_run_id


SCHEMES = {"dialogue", "visual_structure", "visual_semantic", "composite"}
ACTIVE_STATUSES = ACTIVE_ANALYSIS_STATUSES
TERMINAL_STATUSES = {"blocked", "ready", "stale", "failed"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DOWNSTREAM_SCHEMES = {
    "dialogue": ("composite",),
    "visual_structure": ("visual_semantic", "composite"),
    "visual_semantic": ("composite",),
    "composite": (),
}

class AnalysisRunRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        metric_sink: Callable[[str, int], None] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.engine = engine
        self.metric_sink = metric_sink
        self.event_sink = event_sink

    def _metric(self, *, scheme: str, status: str) -> None:
        if self.metric_sink is None:
            return
        try:
            self.metric_sink(
                "media_library_analysis_total"
                f'{{scheme="{scheme}",status="{status}"}}',
                1,
            )
        except Exception:
            # Analysis publication and terminal state are authoritative. A
            # best-effort observability sink must never roll them back.
            return

    def _event(
        self,
        kind: str,
        *,
        run: dict[str, Any],
        status: str,
    ) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(
                kind,
                {
                    "analysis_run_id": str(run["analysis_run_id"]),
                    "asset_id": str(run["asset_id"]),
                    "scheme": str(run["scheme"]),
                    "status": status,
                },
            )
        except Exception:
            # Event persistence is observability, never part of the business
            # state transaction.
            return

    def get(self, analysis_run_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(
                conn.execute(
                    select(media_library_analysis_runs).where(
                        media_library_analysis_runs.c.analysis_run_id
                        == analysis_run_id
                    )
                ).first()
            )

    def current(self, asset_id: str, scheme: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(
                conn.execute(
                    select(media_library_analysis_runs).where(
                        media_library_analysis_runs.c.asset_id == asset_id,
                        media_library_analysis_runs.c.scheme == scheme,
                        media_library_analysis_runs.c.is_current.is_(True),
                    )
                ).first()
            )

    def active_runs(self) -> list[dict[str, Any]]:
        statement = (
            select(
                media_library_analysis_runs,
                media_library_assets.c.session_id,
                sessions.c.workspace_dir,
            )
            .select_from(
                media_library_analysis_runs.join(
                    media_library_assets,
                    media_library_assets.c.asset_id
                    == media_library_analysis_runs.c.asset_id,
                ).join(sessions, sessions.c.id == media_library_assets.c.session_id)
            )
            .where(media_library_analysis_runs.c.status.in_(("queued", "running")))
            .order_by(media_library_analysis_runs.c.created_at)
        )
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().fetchall()]

    def reconcile_projections(self, *, timestamp: int) -> int:
        """Repair legacy task/asset projections from their persisted scheme states."""
        repaired = 0
        with self.engine.begin() as conn:
            tasks = [
                dict(row)
                for row in conn.execute(select(media_library_tasks)).mappings().fetchall()
            ]
            for task in tasks:
                projected = dict(task)
                projected["visual_status"] = derive_visual_status(
                    str(projected.get("visual_structure_status") or "not_analyzed"),
                    str(projected.get("visual_semantic_status") or "not_analyzed"),
                )
                task_status = derive_task_status(projected)
                asset_status = derive_asset_status(projected)
                task_updates: dict[str, Any] = {}
                if str(task.get("status") or "draft") != task_status:
                    task_updates["status"] = task_status
                if (
                    str(task.get("visual_status") or "not_analyzed")
                    != projected["visual_status"]
                ):
                    task_updates["visual_status"] = projected["visual_status"]
                for scheme, progress_field in (
                    ("dialogue", "dialogue_progress_json"),
                    ("visual_structure", "visual_progress_json"),
                    ("visual_semantic", "visual_semantic_progress_json"),
                    ("composite", "composite_progress_json"),
                ):
                    if str(task.get(f"{scheme}_status") or "") != "ready":
                        continue
                    current_run_id = str(
                        task.get(f"{scheme}_current_run_id") or ""
                    )
                    if not current_run_id:
                        continue
                    run_progress = conn.execute(
                        select(
                            media_library_analysis_runs.c.progress_json
                        ).where(
                            media_library_analysis_runs.c.analysis_run_id
                            == current_run_id,
                            media_library_analysis_runs.c.status == "ready",
                        )
                    ).scalar_one_or_none()
                    if (
                        isinstance(run_progress, dict)
                        and run_progress != (task.get(progress_field) or {})
                    ):
                        task_updates[progress_field] = run_progress
                task_changed = bool(task_updates)
                asset_current = conn.execute(
                    select(media_library_assets.c.analysis_status).where(
                        media_library_assets.c.asset_id == task["asset_id"]
                    )
                ).scalar_one_or_none()
                asset_changed = (
                    asset_current is not None and str(asset_current) != asset_status
                )
                if task_changed:
                    conn.execute(
                        update(media_library_tasks)
                        .where(media_library_tasks.c.id == task["id"])
                        .values(
                            **task_updates,
                            updated_at=timestamp,
                        )
                    )
                if asset_changed:
                    conn.execute(
                        update(media_library_assets)
                        .where(
                            media_library_assets.c.asset_id == task["asset_id"]
                        )
                        .values(analysis_status=asset_status, updated_at=timestamp)
                    )
                if task_changed or asset_changed:
                    repaired += 1
        return repaired

    def create_queued(
        self,
        *,
        asset_id: str,
        scheme: str,
        timestamp: int,
        progress: dict[str, Any] | None = None,
        prompt_version: str | None = None,
        model_config_id: str | None = None,
        upstream_refs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if scheme not in SCHEMES:
            raise ValueError("analysis_scheme_invalid")
        analysis_run_id = new_analysis_run_id(scheme, timestamp)
        try:
            with self.engine.begin() as conn:
                asset = row_to_dict(
                    conn.execute(
                        select(media_library_assets)
                        .where(media_library_assets.c.asset_id == asset_id)
                        .with_for_update()
                    ).first()
                )
                if asset is None:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "code": "media_asset_not_found",
                            "user_message": "素材不存在或已删除。",
                        },
                    )
                source_version = str(asset.get("content_sha256") or "")
                if not SHA256_RE.fullmatch(source_version):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "media_source_identity_missing",
                            "user_message": "素材尚未完成内容哈希，暂时不能创建分析运行。",
                            "suggested_action": "先完成 source hash 回填后重试。",
                        },
                    )
                conn.execute(
                    media_library_analysis_runs.insert().values(
                        analysis_run_id=analysis_run_id,
                        asset_id=asset_id,
                        scheme=scheme,
                        source_version=source_version,
                        status="queued",
                        prompt_version=prompt_version,
                        model_config_id=model_config_id,
                        upstream_refs_json=upstream_refs or {},
                        progress_json=progress or {},
                        is_current=False,
                        started_at=None,
                        finished_at=None,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                self._set_projection(
                    conn,
                    asset_id=asset_id,
                    scheme=scheme,
                    status="queued",
                    progress=progress or {},
                    error=None,
                    updated_at=timestamp,
                )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_run_active",
                    "user_message": "同一分析类型已有运行中的任务。",
                },
            ) from exc
        row = self.get(analysis_run_id)
        assert row is not None
        self._event(
            "media_library.analysis.run.created",
            run=row,
            status="queued",
        )
        return row

    def mark_running(
        self,
        analysis_run_id: str,
        *,
        timestamp: int,
        tool_use_session_id: str | None = None,
        attempt_id: int | None = None,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.engine.begin() as conn:
            run = self._require_run(conn, analysis_run_id)
            values: dict[str, Any] = {
                "status": "running",
                "started_at": run.get("started_at") or timestamp,
                "updated_at": timestamp,
            }
            if tool_use_session_id is not None:
                values["tool_use_session_id"] = tool_use_session_id
            if attempt_id is not None:
                values["attempt_id"] = attempt_id
            if progress is not None:
                values["progress_json"] = progress
            result = conn.execute(
                update(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.analysis_run_id
                    == analysis_run_id,
                    media_library_analysis_runs.c.status.in_(("queued", "running")),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "analysis_run_not_active",
                        "user_message": "分析运行已结束，不能继续更新进度。",
                    },
                )
            self._set_projection(
                conn,
                asset_id=str(run["asset_id"]),
                scheme=str(run["scheme"]),
                status="running",
                tool_use_session_id=tool_use_session_id,
                progress=progress,
                error=None,
                updated_at=timestamp,
            )
        row = self.get(analysis_run_id)
        assert row is not None
        return row

    def set_model_session(
        self,
        analysis_run_id: str,
        *,
        model_session_id: str,
        timestamp: int,
    ) -> dict[str, Any]:
        value = str(model_session_id or "").strip()
        if not value:
            raise ValueError("model_session_id_required")
        with self.engine.begin() as conn:
            self._require_run(conn, analysis_run_id)
            result = conn.execute(
                update(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.analysis_run_id
                    == analysis_run_id,
                    media_library_analysis_runs.c.status.in_(
                        ("queued", "running")
                    ),
                    media_library_analysis_runs.c.model_session_id.is_(None),
                )
                .values(
                    model_session_id=value,
                    updated_at=timestamp,
                )
            )
            if result.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "analysis_model_session_conflict",
                        "user_message": "分析运行已经绑定模型 Session 或已结束。",
                    },
                )
        row = self.get(analysis_run_id)
        assert row is not None
        return row

    def finish_unsuccessful(
        self,
        analysis_run_id: str,
        *,
        status: str,
        timestamp: int,
        error_code: str,
        error: dict[str, Any],
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"blocked", "failed", "stale"}:
            raise ValueError("analysis_terminal_status_invalid")
        with self.engine.begin() as conn:
            run = self._require_run(conn, analysis_run_id)
            result = conn.execute(
                update(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.analysis_run_id
                    == analysis_run_id,
                    media_library_analysis_runs.c.status.in_(("queued", "running")),
                )
                .values(
                    status=status,
                    progress_json=progress or run.get("progress_json") or {},
                    error_code=error_code,
                    error_json=error,
                    finished_at=timestamp,
                    updated_at=timestamp,
                )
            )
            if result.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "analysis_run_not_active",
                        "user_message": "分析运行已结束，不能重复写入终态。",
                    },
                )
            current = row_to_dict(
                conn.execute(
                    select(media_library_analysis_runs).where(
                        media_library_analysis_runs.c.asset_id == run["asset_id"],
                        media_library_analysis_runs.c.scheme == run["scheme"],
                        media_library_analysis_runs.c.is_current.is_(True),
                    )
                ).first()
            )
            if current is None or (
                str(current.get("status") or "") == "stale"
                and status in {"blocked", "failed"}
            ):
                self._set_projection(
                    conn,
                    asset_id=str(run["asset_id"]),
                    scheme=str(run["scheme"]),
                    status=status,
                    current_run_id=(
                        str(current["analysis_run_id"])
                        if current is not None
                        else None
                    ),
                    tool_use_session_id=run.get("tool_use_session_id"),
                    progress=progress,
                    error=error,
                    updated_at=timestamp,
                )
            else:
                attempt_message = str(
                    error.get("user_message")
                    or error.get("message")
                    or ""
                ).strip()
                projection_message = (
                    "本次重新运行未成功，仍在使用上一次成功结果。"
                    + (f" {attempt_message}" if attempt_message else "")
                )
                self._set_projection(
                    conn,
                    asset_id=str(run["asset_id"]),
                    scheme=str(run["scheme"]),
                    status=str(current["status"]),
                    current_run_id=str(current["analysis_run_id"]),
                    tool_use_session_id=current.get("tool_use_session_id"),
                    progress=current.get("progress_json") or {},
                    error={"user_message": projection_message},
                    updated_at=timestamp,
                )
        row = self.get(analysis_run_id)
        assert row is not None
        self._metric(scheme=str(row["scheme"]), status=status)
        self._event(
            f"media_library.analysis.run.{status}",
            run=row,
            status=status,
        )
        return row

    def activate_ready(
        self,
        analysis_run_id: str,
        *,
        timestamp: int,
        schema_version: str,
        result_hash: str,
        result_index_path: str,
        progress: dict[str, Any] | None = None,
        upstream_refs: dict[str, Any] | None = None,
        expected_current_upstreams: dict[
            str, dict[str, str]
        ] | None = None,
    ) -> dict[str, Any]:
        if not SHA256_RE.fullmatch(result_hash):
            raise ValueError("analysis_result_hash_invalid")
        with self.engine.begin() as conn:
            run = self._require_run(conn, analysis_run_id)
            asset = row_to_dict(
                conn.execute(
                    select(media_library_assets)
                    .where(media_library_assets.c.asset_id == run["asset_id"])
                    .with_for_update()
                ).first()
            )
            if asset is None or str(asset.get("content_sha256") or "") != str(
                run["source_version"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "media_source_version_mismatch",
                        "user_message": "素材源版本已变化，本次分析结果不能发布。",
                    },
                )
            upstream_changed = False
            for upstream_scheme, expected in (
                expected_current_upstreams or {}
            ).items():
                current_upstream = row_to_dict(
                    conn.execute(
                        select(media_library_analysis_runs)
                        .where(
                            media_library_analysis_runs.c.asset_id
                            == run["asset_id"],
                            media_library_analysis_runs.c.scheme
                            == upstream_scheme,
                            media_library_analysis_runs.c.is_current.is_(
                                True
                            ),
                            media_library_analysis_runs.c.status == "ready",
                        )
                        .with_for_update()
                    ).first()
                )
                if (
                    current_upstream is None
                    or str(current_upstream["analysis_run_id"])
                    != str(expected.get("analysis_run_id") or "")
                    or str(current_upstream.get("result_hash") or "")
                    != str(expected.get("result_hash") or "")
                ):
                    upstream_changed = True
                    break
            if upstream_changed:
                error = {
                    "code": "analysis_upstream_changed",
                    "user_message": "分析运行期间上游结果已更新，本次结果不会激活。",
                    "suggested_action": "基于当前上游结果重新运行分析。",
                    "run_id": analysis_run_id,
                    "failed_step": "publish",
                }
                result = conn.execute(
                    update(media_library_analysis_runs)
                    .where(
                        media_library_analysis_runs.c.analysis_run_id
                        == analysis_run_id,
                        media_library_analysis_runs.c.status.in_(
                            ("queued", "running")
                        ),
                    )
                    .values(
                        status="stale",
                        progress_json=progress
                        or run.get("progress_json")
                        or {},
                        error_code="analysis_upstream_changed",
                        error_json=error,
                        is_current=False,
                        finished_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                if result.rowcount != 1:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "analysis_run_not_active",
                            "user_message": "分析运行已结束，不能发布结果。",
                        },
                    )
                existing_current = row_to_dict(
                    conn.execute(
                        select(media_library_analysis_runs).where(
                            media_library_analysis_runs.c.asset_id
                            == run["asset_id"],
                            media_library_analysis_runs.c.scheme
                            == run["scheme"],
                            media_library_analysis_runs.c.is_current.is_(
                                True
                            ),
                        )
                    ).first()
                )
                if existing_current is None:
                    self._set_projection(
                        conn,
                        asset_id=str(run["asset_id"]),
                        scheme=str(run["scheme"]),
                        status="stale",
                        progress=progress,
                        error=error,
                        updated_at=timestamp,
                    )
                else:
                    self._set_projection(
                        conn,
                        asset_id=str(run["asset_id"]),
                        scheme=str(run["scheme"]),
                        status=str(existing_current["status"]),
                        current_run_id=str(
                            existing_current["analysis_run_id"]
                        ),
                        tool_use_session_id=existing_current.get(
                            "tool_use_session_id"
                        ),
                        progress=existing_current.get("progress_json")
                        or {},
                        error=existing_current.get("error_json"),
                        updated_at=timestamp,
                    )
                self._metric(
                    scheme=str(run["scheme"]),
                    status="stale",
                )
                self._event(
                    "media_library.analysis.run.stale",
                    run=run,
                    status="stale",
                )
                return {
                    **run,
                    "status": "stale",
                    "progress_json": progress
                    or run.get("progress_json")
                    or {},
                    "error_code": "analysis_upstream_changed",
                    "error_json": error,
                    "is_current": False,
                    "finished_at": timestamp,
                    "updated_at": timestamp,
                }
            downstream = DOWNSTREAM_SCHEMES.get(str(run["scheme"]), ())
            for downstream_scheme in downstream:
                downstream_run = row_to_dict(
                    conn.execute(
                        select(media_library_analysis_runs)
                        .where(
                            media_library_analysis_runs.c.asset_id
                            == run["asset_id"],
                            media_library_analysis_runs.c.scheme
                            == downstream_scheme,
                            media_library_analysis_runs.c.is_current.is_(True),
                        )
                        .with_for_update()
                    ).first()
                )
                if downstream_run is None:
                    continue
                conn.execute(
                    update(media_library_analysis_runs)
                    .where(
                        media_library_analysis_runs.c.analysis_run_id
                        == downstream_run["analysis_run_id"],
                        media_library_analysis_runs.c.status == "ready",
                    )
                    .values(
                        status="stale",
                        error_code="analysis_upstream_changed",
                        error_json={
                            "code": "analysis_upstream_changed",
                            "user_message": "上游分析已更新，本结果仅保留为只读审计版本。",
                            "suggested_action": "重新运行此分析以生成当前结果。",
                            "upstream_scheme": str(run["scheme"]),
                            "upstream_run_id": analysis_run_id,
                        },
                        updated_at=timestamp,
                    )
                )
                conn.execute(
                    update(media_library_fragment_index)
                    .where(
                        media_library_fragment_index.c.analysis_run_id
                        == downstream_run["analysis_run_id"],
                        media_library_fragment_index.c.is_active.is_(True),
                    )
                    .values(is_active=False, updated_at=timestamp)
                )
                self._set_projection(
                    conn,
                    asset_id=str(run["asset_id"]),
                    scheme=downstream_scheme,
                    status="stale",
                    current_run_id=str(
                        downstream_run["analysis_run_id"]
                    ),
                    tool_use_session_id=downstream_run.get(
                        "tool_use_session_id"
                    ),
                    progress=downstream_run.get("progress_json") or {},
                    error={
                        "code": "analysis_upstream_changed",
                        "user_message": "上游分析已更新，本结果需要重新运行。",
                    },
                    updated_at=timestamp,
                )
                self._metric(
                    scheme=downstream_scheme,
                    status="stale",
                )
                self._event(
                    "media_library.analysis.run.stale",
                    run=downstream_run,
                    status="stale",
                )
            result = conn.execute(
                update(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.asset_id == run["asset_id"],
                    media_library_analysis_runs.c.scheme == run["scheme"],
                    media_library_analysis_runs.c.is_current.is_(True),
                )
                .values(is_current=False, updated_at=timestamp)
            )
            result = conn.execute(
                update(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.analysis_run_id
                    == analysis_run_id,
                    media_library_analysis_runs.c.status.in_(("queued", "running")),
                )
                .values(
                    status="ready",
                    schema_version=schema_version,
                    result_hash=result_hash,
                    result_index_path=result_index_path,
                    upstream_refs_json=upstream_refs
                    if upstream_refs is not None
                    else run.get("upstream_refs_json") or {},
                    progress_json=progress or run.get("progress_json") or {},
                    error_code=None,
                    error_json=None,
                    is_current=True,
                    finished_at=timestamp,
                    updated_at=timestamp,
                )
            )
            if result.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "analysis_run_not_active",
                        "user_message": "分析运行已结束，不能发布结果。",
                    },
                )
            self._set_projection(
                conn,
                asset_id=str(run["asset_id"]),
                scheme=str(run["scheme"]),
                status="ready",
                current_run_id=analysis_run_id,
                tool_use_session_id=run.get("tool_use_session_id"),
                progress=progress,
                error=None,
                updated_at=timestamp,
            )
        row = self.get(analysis_run_id)
        assert row is not None
        self._metric(scheme=str(row["scheme"]), status="ready")
        self._event(
            "media_library.analysis.run.ready",
            run=row,
            status="ready",
        )
        return row

    def _require_run(
        self, conn: Connection, analysis_run_id: str
    ) -> dict[str, Any]:
        run = row_to_dict(
            conn.execute(
                select(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.analysis_run_id
                    == analysis_run_id
                )
                .with_for_update()
            ).first()
        )
        if run is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "analysis_run_not_found",
                    "user_message": "分析运行不存在。",
                },
            )
        return run

    def _set_projection(
        self,
        conn: Connection,
        *,
        asset_id: str,
        scheme: str,
        status: str,
        updated_at: int,
        current_run_id: str | None = None,
        tool_use_session_id: str | None = None,
        progress: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        # Every projection writer must acquire the shared asset row before the
        # task row. create_queued() already owns the asset lock, while progress
        # and terminal writers arrive here after locking their individual run.
        # Locking task -> asset here lets a concurrent cross-scheme
        # create_queued() hold asset -> task and forms a PostgreSQL deadlock.
        asset = row_to_dict(
            conn.execute(
                select(media_library_assets)
                .where(media_library_assets.c.asset_id == asset_id)
                .with_for_update()
            ).first()
        )
        if asset is None:
            return
        task = row_to_dict(
            conn.execute(
                select(media_library_tasks)
                .where(media_library_tasks.c.asset_id == asset_id)
                .with_for_update()
            ).first()
        )
        if task is None:
            return
        prefix = scheme
        values: dict[str, Any] = {
            f"{prefix}_status": status,
            "updated_at": updated_at,
        }
        if current_run_id is not None:
            values[f"{prefix}_current_run_id"] = current_run_id
        if scheme == "visual_structure":
            if tool_use_session_id is not None:
                values["visual_tool_use_session_id"] = tool_use_session_id
            if progress is not None:
                values["visual_progress_json"] = progress
            if error is not None or status in {"ready", "running", "queued"}:
                values["visual_error"] = (
                    None
                    if error is None
                    else str(error.get("user_message") or error.get("message") or "")
                )
        else:
            if tool_use_session_id is not None:
                values[f"{prefix}_tool_use_session_id"] = tool_use_session_id
            if progress is not None:
                values[f"{prefix}_progress_json"] = progress
            if error is not None or status in {"ready", "running", "queued"}:
                values[f"{prefix}_error"] = (
                    None
                    if error is None
                    else str(error.get("user_message") or error.get("message") or "")
                )
        projected = {**task, **values}
        if scheme in {"visual_structure", "visual_semantic"}:
            values["visual_status"] = derive_visual_status(
                str(projected.get("visual_structure_status") or "not_analyzed"),
                str(projected.get("visual_semantic_status") or "not_analyzed"),
            )
            projected["visual_status"] = values["visual_status"]
        values["status"] = derive_task_status(projected)
        conn.execute(
            update(media_library_tasks)
            .where(media_library_tasks.c.id == task["id"])
            .values(**values)
        )
        conn.execute(
            update(media_library_assets)
            .where(media_library_assets.c.asset_id == asset_id)
            .values(
                analysis_status=derive_asset_status(projected),
                updated_at=updated_at,
            )
        )
