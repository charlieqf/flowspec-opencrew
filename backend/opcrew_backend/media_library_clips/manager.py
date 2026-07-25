from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.engine import Engine

from ..db.schema import sessions
from .errors import ClipCancelled, MediaClipError
from .ffmpeg import (
    inspect_media_runtime,
    resolve_media_binary,
    terminate_process,
)
from .models import (
    CLIP_JOB_ID_RE,
    JOB_TERMINAL_STATUSES,
    ClipJob,
    ClipRequest,
    clean_display_name,
    clean_search_tags,
    clip_request_from_asset,
    new_boot_id,
    new_clip_id,
    new_clip_job_id,
)
from .processor import MediaClipProcessor
from .repository import ClipDerivativeRepository
from .storage import ClipStorage, OrphanCleanupReport, resolve_controlled_path


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def serialize_clip(record: Mapping[str, Any]) -> dict[str, Any]:
    asset_id = str(record.get("source_asset_id") or "")
    clip_id = str(record.get("clip_id") or "")
    path = str(record.get("output_path") or "")
    session_id = int(record.get("source_session_id") or 0)
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    content_url = (
        f"/api/session-tasks/{session_id}/raw/{encoded_path}"
        if session_id > 0 and encoded_path
        else None
    )
    return {
        "clip_id": clip_id,
        "source_asset_id": asset_id,
        "source_version": str(record.get("source_version") or ""),
        "start_ms": int(record.get("source_start_ms") or 0),
        "end_ms": int(record.get("source_end_ms") or 0),
        "duration_ms": int(record.get("duration_ms") or 0),
        "display_name": str(record.get("display_name") or ""),
        "tags": list(record.get("tags_json") or []),
        "search_eligible": bool(record.get("search_eligible")),
        "search_enabled_at": (
            int(record["search_enabled_at"])
            if record.get("search_enabled_at") is not None
            else None
        ),
        "search_updated_at": (
            int(record["search_updated_at"])
            if record.get("search_updated_at") is not None
            else None
        ),
        "source_scheme": record.get("source_scheme"),
        "source_fragment_id": record.get("source_fragment_id"),
        "source_analysis_run_id": record.get("source_analysis_run_id"),
        "source_search_id": record.get("source_search_id"),
        "source_dialogue_asset_key": record.get(
            "source_dialogue_asset_key"
        ),
        "operation": str(record.get("operation") or ""),
        "content_sha256": str(record.get("content_sha256") or ""),
        "size_bytes": int(record.get("size_bytes") or 0),
        "created_at": int(record.get("created_at") or 0),
        "preview_url": content_url,
        "download_url": (
            f"{content_url}?download=1" if content_url is not None else None
        ),
    }


class ClipJobManager:
    def __init__(
        self,
        engine: Engine,
        *,
        repository: ClipDerivativeRepository | None = None,
        processor: MediaClipProcessor | None = None,
        max_workers: int | None = None,
        boot_id: str | None = None,
        now_ms: Callable[[], int] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        metric_sink: Callable[[str, int], None] | None = None,
    ) -> None:
        self.engine = engine
        self.repository = repository or ClipDerivativeRepository(engine)
        self.media_runtime: dict[str, Any] | None = None
        if processor is None:
            ffmpeg_path = resolve_media_binary("ffmpeg")
            ffprobe_path = resolve_media_binary("ffprobe")
            self.media_runtime = inspect_media_runtime(
                ffmpeg_path=ffmpeg_path,
                ffprobe_path=ffprobe_path,
            )
            self.processor = MediaClipProcessor(
                self.repository,
                ffmpeg_path=ffmpeg_path,
                ffprobe_path=ffprobe_path,
            )
        else:
            self.processor = processor
        self.boot_id = boot_id or new_boot_id()
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.event_sink = event_sink
        self.metric_sink = metric_sink
        self._lock = threading.RLock()
        self._accepting = True
        self._jobs_by_id: dict[str, ClipJob] = {}
        self._job_id_by_idempotency_key: dict[str, str] = {}
        self._active_job_ids_by_asset: dict[str, set[str]] = {}
        self._futures: dict[str, Future[None]] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers
            or _env_int("OPENCREW_MEDIA_CLIP_MAX_CONCURRENCY", 2),
            thread_name_prefix="media-clip",
        )
        if self.media_runtime is not None:
            self._emit(
                "media_library.clip.runtime",
                dict(self.media_runtime),
            )

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(kind, payload)
        except Exception:
            pass

    def _metric(self, name: str, value: int) -> None:
        if self.metric_sink is None:
            return
        try:
            self.metric_sink(name, int(value))
        except Exception:
            pass

    def _emit_active_metric(self) -> None:
        self._metric(
            "media_library_clip_active",
            sum(
                len(job_ids)
                for job_ids in self._active_job_ids_by_asset.values()
            ),
        )

    def _require_job(self, asset_id: str, clip_job_id: str) -> ClipJob:
        match = CLIP_JOB_ID_RE.fullmatch(str(clip_job_id or ""))
        if match is None:
            raise MediaClipError(
                "clip_job_not_found",
                "剪辑任务不存在。",
                status_code=404,
            )
        if match.group(1) != self.boot_id:
            self._emit(
                "media_library.clip.lost",
                {
                    "asset_id": asset_id,
                    "clip_job_id": clip_job_id,
                    "boot_id": self.boot_id,
                },
            )
            raise MediaClipError(
                "clip_job_lost",
                "后端服务已重启，未完成的剪辑任务无法恢复。",
                status_code=410,
                suggested_action="请重新创建剪辑任务。",
            )
        job = self._jobs_by_id.get(clip_job_id)
        if job is None or job.asset_id != asset_id:
            raise MediaClipError(
                "clip_job_not_found",
                "剪辑任务不存在。",
                status_code=404,
            )
        return job

    def _completed_job(
        self, request: ClipRequest, record: Mapping[str, Any]
    ) -> dict[str, Any]:
        timestamp = self.now_ms()
        job_id = new_clip_job_id(self.boot_id)
        clip = serialize_clip(record)
        job = ClipJob(
            clip_job_id=job_id,
            boot_id=self.boot_id,
            asset_id=request.source_asset_id,
            idempotency_key=request.idempotency_key,
            request_fingerprint=request.fingerprint(),
            request=request,
            status="completed",
            progress=100,
            clip_id=str(record["clip_id"]),
            clip=clip,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._jobs_by_id[job_id] = job
        self._job_id_by_idempotency_key[request.idempotency_key] = job_id
        return job.view()

    def submit(
        self,
        *,
        asset: Mapping[str, Any],
        session: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request = clip_request_from_asset(
            asset=asset,
            session=session,
            payload=payload,
            minimum_duration_ms=_env_int(
                "OPENCREW_MEDIA_CLIP_MIN_MS", 250
            ),
            maximum_duration_ms=_env_int(
                "OPENCREW_MEDIA_CLIP_MAX_MS", 1_800_000
            ),
        )
        fingerprint = request.fingerprint()
        with self._lock:
            if not self._accepting:
                raise MediaClipError(
                    "clip_job_manager_stopping",
                    "剪辑服务正在停止，暂时不能创建任务。",
                    status_code=503,
                )
            existing_job_id = self._job_id_by_idempotency_key.get(
                request.idempotency_key
            )
            if existing_job_id:
                existing_job = self._jobs_by_id[existing_job_id]
                if existing_job.request_fingerprint != fingerprint:
                    raise MediaClipError(
                        "idempotency_key_conflict",
                        "该幂等键已用于不同的剪辑参数。",
                        status_code=409,
                    )
                return existing_job.view()
            persisted = self.repository.get_by_idempotency_key(
                request.idempotency_key
            )
            if persisted is not None:
                self.repository.assert_compatible_idempotent_result(
                    persisted, request
                )
                persisted_path = resolve_controlled_path(
                    request.source_workspace,
                    str(persisted["output_path"]),
                )
                if (
                    not persisted_path.is_file()
                    or not self.repository.session_file_registered(
                        request.source_session_id,
                        str(persisted["output_path"]),
                    )
                ):
                    raise MediaClipError(
                        "media_clip_persisted_file_missing",
                        "已登记的派生片段文件缺失。",
                        status_code=500,
                    )
                return self._completed_job(request, persisted)
            self.repository.validate_source_fragment(request)
            timestamp = self.now_ms()
            job_id = new_clip_job_id(self.boot_id)
            job = ClipJob(
                clip_job_id=job_id,
                boot_id=self.boot_id,
                asset_id=request.source_asset_id,
                idempotency_key=request.idempotency_key,
                request_fingerprint=fingerprint,
                request=request,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._jobs_by_id[job_id] = job
            self._job_id_by_idempotency_key[
                request.idempotency_key
            ] = job_id
            self._active_job_ids_by_asset.setdefault(
                request.source_asset_id, set()
            ).add(job_id)
            self._emit_active_metric()
            self._emit(
                "media_library.clip.requested",
                {
                    "asset_id": request.source_asset_id,
                    "source_session_id": request.source_session_id,
                    "clip_job_id": job_id,
                    "start_ms": request.source_start_ms,
                    "end_ms": request.source_end_ms,
                },
            )
            try:
                self._futures[job_id] = self._executor.submit(
                    self._run_job, job_id
                )
            except Exception:
                self._active_job_ids_by_asset[
                    request.source_asset_id
                ].discard(job_id)
                self._emit_active_metric()
                job.status = "failed"
                job.error = {
                    "code": "clip_job_start_failed",
                    "user_message": "剪辑任务无法启动。",
                }
                self._emit(
                    "media_library.clip.failed",
                    {
                        "asset_id": request.source_asset_id,
                        "source_session_id": request.source_session_id,
                        "clip_job_id": job_id,
                        "error": dict(job.error),
                    },
                )
                raise
            return job.view()

    def _update_process(self, job_id: str, process: Any | None) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is not None:
                job.process = process

    def _update_part_path(
        self, job_id: str, part_path: Path | None
    ) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is not None:
                job.part_path = part_path

    def _update_progress(self, job_id: str, progress: int) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None or job.status != "running":
                return
            job.progress = max(job.progress, min(99, max(0, int(progress))))
            job.updated_at = self.now_ms()

    def _cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            return job is None or job.cancel_requested

    def _finish_active(self, job: ClipJob) -> None:
        active = self._active_job_ids_by_asset.get(job.asset_id)
        if active is not None:
            active.discard(job.clip_job_id)
            if not active:
                self._active_job_ids_by_asset.pop(job.asset_id, None)
        self._emit_active_metric()

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs_by_id[job_id]
            if job.cancel_requested:
                job.status = "cancelled"
                job.updated_at = self.now_ms()
                self._finish_active(job)
                return
            job.status = "running"
            job.updated_at = self.now_ms()
        clip_id = new_clip_id(self.now_ms())
        try:
            record, _inserted = self.processor.run(
                request=job.request,
                clip_id=clip_id,
                timestamp_ms=self.now_ms(),
                cancel_requested=lambda: self._cancel_requested(job_id),
                on_progress=lambda value: self._update_progress(job_id, value),
                on_process=lambda process: self._update_process(
                    job_id, process
                ),
                on_part_path=lambda path: self._update_part_path(job_id, path),
            )
            with self._lock:
                job.status = "completed"
                job.progress = 100
                job.clip_id = str(record["clip_id"])
                job.clip = serialize_clip(record)
                job.error = None
                job.updated_at = self.now_ms()
            self._emit(
                "media_library.clip.completed",
                {
                    "asset_id": job.asset_id,
                    "source_session_id": job.request.source_session_id,
                    "clip_job_id": job.clip_job_id,
                    "clip_id": job.clip_id,
                },
            )
            self._metric(
                "media_library_clip_duration_ms",
                int(record.get("duration_ms") or 0),
            )
        except ClipCancelled:
            with self._lock:
                job.status = "cancelled"
                job.progress = min(job.progress, 99)
                job.error = None
                job.updated_at = self.now_ms()
            self._emit(
                "media_library.clip.cancelled",
                {
                    "asset_id": job.asset_id,
                    "source_session_id": job.request.source_session_id,
                    "clip_job_id": job.clip_job_id,
                },
            )
        except MediaClipError as exc:
            with self._lock:
                job.status = "failed"
                job.error = exc.payload()
                job.updated_at = self.now_ms()
            self._emit(
                "media_library.clip.failed",
                {
                    "asset_id": job.asset_id,
                    "source_session_id": job.request.source_session_id,
                    "clip_job_id": job.clip_job_id,
                    "error": exc.payload(),
                },
            )
            self._metric(
                f'media_library_clip_failure_total{{code="{exc.code}"}}',
                1,
            )
        except Exception:
            with self._lock:
                job.status = "failed"
                job.error = {
                    "code": "media_clip_execution_failed",
                    "user_message": "视频剪切失败，请稍后重试。",
                }
                job.updated_at = self.now_ms()
            self._emit(
                "media_library.clip.failed",
                {
                    "asset_id": job.asset_id,
                    "source_session_id": job.request.source_session_id,
                    "clip_job_id": job.clip_job_id,
                    "error": dict(job.error),
                },
            )
            self._metric(
                'media_library_clip_failure_total{code="media_clip_execution_failed"}',
                1,
            )
        finally:
            with self._lock:
                job.process = None
                job.part_path = None
                self._finish_active(job)

    def get_job(self, asset_id: str, clip_job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._require_job(asset_id, clip_job_id).view()

    def cancel_job(
        self, asset_id: str, clip_job_id: str
    ) -> dict[str, Any]:
        process: Any | None = None
        part_path: Path | None = None
        future: Future[None] | None = None
        with self._lock:
            job = self._require_job(asset_id, clip_job_id)
            if job.status == "cancelled":
                return job.view()
            if job.status in {"completed", "failed"}:
                raise MediaClipError(
                    "clip_job_not_cancellable",
                    "剪辑任务已经结束。",
                    status_code=409,
                )
            job.cancel_requested = True
            job.updated_at = self.now_ms()
            process = job.process
            part_path = job.part_path
            future = self._futures.get(job.clip_job_id)
            if future is not None and future.cancel():
                job.status = "cancelled"
                self._finish_active(job)
        if process is not None:
            terminate_process(process)
        if part_path is not None:
            part_path.unlink(missing_ok=True)
        with self._lock:
            return job.view()

    def has_active_jobs(self, asset_id: str) -> bool:
        with self._lock:
            return bool(self._active_job_ids_by_asset.get(asset_id))

    def has_active_job(self, asset_id: str) -> bool:
        return self.has_active_jobs(asset_id)

    def list_clips(self, asset_id: str) -> list[dict[str, Any]]:
        return [
            serialize_clip(row)
            for row in self.repository.list_for_asset(asset_id)
        ]

    def get_clip(self, asset_id: str, clip_id: str) -> dict[str, Any]:
        row = self.repository.get_for_asset(asset_id, clip_id)
        if row is None:
            raise MediaClipError(
                "media_clip_not_found",
                "派生片段不存在。",
                status_code=404,
            )
        return serialize_clip(row)

    def update_clip_search_metadata(
        self,
        *,
        asset_id: str,
        clip_id: str,
        display_name: Any = None,
        tags: Any = None,
        search_eligible: bool | None = None,
        update_display_name: bool = False,
        update_tags: bool = False,
    ) -> dict[str, Any]:
        current = self.repository.get_for_asset(asset_id, clip_id)
        if current is None:
            raise MediaClipError(
                "media_clip_not_found",
                "派生片段不存在。",
                status_code=404,
            )
        next_display_name = (
            clean_display_name(display_name)
            if update_display_name
            else str(current.get("display_name") or "")
        )
        next_tags = (
            clean_search_tags(tags)
            if update_tags
            else list(current.get("tags_json") or [])
        )
        from ..media_library_search.normalization import normalized_search_text

        search_text = normalized_search_text(next_display_name, next_tags)
        next_eligible = (
            bool(search_eligible)
            if search_eligible is not None
            else bool(current.get("search_eligible"))
        )
        if next_eligible and not search_text:
            raise MediaClipError(
                "media_clip_search_terms_required",
                "加入素材检索前，请填写有效的片段名称或标签。",
                status_code=422,
            )
        updated = self.repository.update_search_metadata(
            asset_id=asset_id,
            clip_id=clip_id,
            display_name=next_display_name if update_display_name else None,
            tags=next_tags if update_tags else None,
            search_eligible=search_eligible,
            search_text=search_text,
            updated_at=int(self.now_ms()),
        )
        previous_eligible = bool(current.get("search_eligible"))
        current_eligible = bool(updated.get("search_eligible"))
        if previous_eligible != current_eligible:
            self._metric(
                "media_library_clip_search_enabled_total"
                if current_eligible
                else "media_library_clip_search_disabled_total",
                1,
            )
        self._emit(
            "media_library.clip.search_metadata_updated",
            {
                "asset_id": asset_id,
                "clip_id": clip_id,
                "source_session_id": int(
                    updated.get("source_session_id") or 0
                ),
                "search_eligible": current_eligible,
                "tags_count": len(next_tags),
            },
        )
        return serialize_clip(updated)

    def delete_clip(
        self, *, asset_id: str, clip_id: str, workspace: Path
    ) -> dict[str, Any]:
        row = self.repository.get_for_asset(asset_id, clip_id)
        if row is None:
            raise MediaClipError(
                "media_clip_not_found",
                "派生片段不存在。",
                status_code=404,
            )
        if self.repository.is_in_use(clip_id):
            raise MediaClipError(
                "media_clip_in_use",
                "派生片段已被 StoryBoard 引用，不能删除。",
                status_code=409,
            )
        storage = ClipStorage(workspace)
        storage.delete_file_transactionally(
            relative_path=str(row["output_path"]),
            delete_database=lambda: self.repository.delete_after_file_removal(
                asset_id=asset_id, clip_id=clip_id
            ),
        )
        self._emit(
            "media_library.clip.deleted",
            {"asset_id": asset_id, "clip_id": clip_id},
        )
        return serialize_clip(row)

    def cleanup_workspace(
        self,
        *,
        session_id: int,
        workspace: Path,
        now_ms: int | None = None,
        ttl_ms: int | None = None,
    ) -> OrphanCleanupReport:
        report = ClipStorage(workspace).cleanup_orphans(
            registered_output_paths=self.repository.registered_output_paths(
                session_id
            ),
            now_ms=int(now_ms if now_ms is not None else self.now_ms()),
            ttl_ms=int(
                ttl_ms
                if ttl_ms is not None
                else _env_int(
                    "OPENCREW_MEDIA_CLIP_PART_TTL_MS", 86_400_000
                )
            ),
        )
        for relative in report.missing_registered:
            self._emit(
                "media_library.clip.registered_file_missing",
                {
                    "session_id": session_id,
                    "output_path": relative,
                    "severity": "critical",
                },
            )
        return report

    def startup_cleanup(self) -> dict[int, OrphanCleanupReport]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(sessions.c.id, sessions.c.workspace_dir)
            ).fetchall()
        reports: dict[int, OrphanCleanupReport] = {}
        for session_id, workspace_dir in rows:
            workspace = Path(str(workspace_dir or "")).expanduser()
            if not workspace.is_absolute():
                continue
            try:
                reports[int(session_id)] = self.cleanup_workspace(
                    session_id=int(session_id), workspace=workspace
                )
            except Exception:
                self._emit(
                    "media_library.clip.cleanup_failed",
                    {"session_id": int(session_id), "severity": "warning"},
                )
        return reports

    def shutdown(self) -> None:
        with self._lock:
            if not self._accepting:
                return
            self._accepting = False
            active_jobs = [
                job
                for job in self._jobs_by_id.values()
                if job.status not in JOB_TERMINAL_STATUSES
            ]
            for job in active_jobs:
                job.cancel_requested = True
            processes = [job.process for job in active_jobs if job.process]
            parts = [job.part_path for job in active_jobs if job.part_path]
        for process in processes:
            terminate_process(process)
        deadline = time.monotonic() + 5
        futures: Iterable[Future[None]] = tuple(self._futures.values())
        while time.monotonic() < deadline and any(
            not future.done() for future in futures
        ):
            time.sleep(0.02)
        for process in processes:
            terminate_process(process, timeout_seconds=0.1)
        for path in parts:
            path.unlink(missing_ok=True)
        # The application disposes the SQLAlchemy engine immediately after
        # this method returns. Wait for every worker to leave its processor
        # and DB publication path so shutdown cannot race a late commit.
        self._executor.shutdown(wait=True, cancel_futures=True)


__all__ = ["ClipJobManager", "serialize_clip"]
