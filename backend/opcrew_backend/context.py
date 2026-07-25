from __future__ import annotations

import json
import os
import secrets
import signal
import time
from typing import Any

from .config import AppConfig
from .db.bootstrap import build_engine, initialize_database
from .repositories import (
    EventLogRepository,
    MediaLibraryRepository,
    MediaLibraryTaskRepository,
    OpenFlowRepository,
    RuntimeRepository,
    SessionRepository,
    SettingsRepository,
    SkillRepository,
    TaskRepository,
    VerificationRepository,
)
from .services.session_events import SessionEventService
from .services.session_files import SessionFileService
from .services.local_secrets import LocalSecretStore
from .services.local_usage import LocalUsageRecorder
from .services.workflow_deletion import WorkflowDeletionService
from .storage.workspace import LocalWorkspaceStore
from sqlalchemy import text


def now_ms() -> int:
    return int(time.time() * 1000)


class AppContext:
    def __init__(self, config: AppConfig) -> None:
        from .media_library_analysis.run_repository import AnalysisRunRepository
        from .media_library_imports import MediaLibraryStoryBoardImportService
        from .media_library_search import (
            MediaLibraryFragmentPublisher,
            MediaLibrarySearchPlanner,
            MediaLibrarySearchRepository,
            MediaLibrarySearchService,
            OpenCodeMediaLibrarySearchPlannerAdapter,
            SearchTelemetry,
        )

        self.config = config
        self.data_dir = config.data_dir
        self.engine = build_engine(config.database_url)
        initialize_database(self.engine, self.data_dir)
        self.settings_repo = SettingsRepository(self.engine)
        self.event_repo = EventLogRepository(self.engine)
        self.media_library_repo = MediaLibraryRepository(self.engine)
        self.media_library_task_repo = MediaLibraryTaskRepository(self.engine)
        self.media_analysis_run_repo = AnalysisRunRepository(
            self.engine,
            metric_sink=self.media_library_metric,
            event_sink=self.media_library_analysis_event,
        )
        self.media_library_fragment_publisher = MediaLibraryFragmentPublisher(
            self.engine,
            event_sink=self.media_library_analysis_event,
            metric_sink=self.media_library_metric,
        )
        media_search_repo = MediaLibrarySearchRepository(self.engine)
        self.media_library_search_service = MediaLibrarySearchService(
            repository=media_search_repo,
            planner=MediaLibrarySearchPlanner(
                OpenCodeMediaLibrarySearchPlannerAdapter(self),
                enabled=str(
                    os.environ.get(
                        "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_ENABLED",
                        "1",
                    )
                ).strip().lower()
                not in {"0", "false", "no", "off"},
                timeout_seconds=8.0,
            ),
            telemetry=SearchTelemetry(
                media_search_repo,
                metric_sink=self.media_library_metric,
                event_sink=self.media_library_search_event,
            ),
        )
        self.runtime_repo = RuntimeRepository(self.engine)
        self.skill_repo = SkillRepository(self.engine)
        self.openflow_repo = OpenFlowRepository(self.engine)
        self.task_repo = TaskRepository(self.engine)
        self.session_repo = SessionRepository(self.engine)
        self.workspace_store = LocalWorkspaceStore(self.data_dir)
        self.secret_store = LocalSecretStore(self.data_dir)
        self.local_usage = LocalUsageRecorder(self.engine)
        self.session_event_service = SessionEventService(self.session_repo, now_ms)
        self.session_file_service = SessionFileService()
        self.workflow_deletion_service = WorkflowDeletionService(self.engine, self.workspace_store.sessions_root())
        self.verification_repo = VerificationRepository(self.engine)
        self.media_library_import_service = MediaLibraryStoryBoardImportService(
            self
        )
        self.tunnel_process = None
        self.npc_process = None
        self.reconcile_media_analysis_runs()
        self.media_library_import_service.reconcile_preparing()
        try:
            self.migrate_legacy_provider_keys()
        except Exception as exc:
            self.event("error", "phase0", "Provider key migration into local secret store failed", {"error": str(exc), "secret_store_path": str(self.secret_store.path)})

    def sessions_root(self):
        return self.workspace_store.sessions_root()

    def next_session_title(self) -> str:
        return self.settings_repo.next_counter_title("session.title_counter", now_ms())

    def new_share_token(self) -> str:
        return secrets.token_urlsafe(24)

    def event(self, level: str, category: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self.event_repo.add(level, category, message, json.dumps(payload, ensure_ascii=True) if payload else None, now_ms())

    def media_library_metric(self, name: str, value: int) -> None:
        self.event_repo.add(
            "info",
            "media_library_metric",
            str(name),
            json.dumps(
                {"metric": str(name), "value": int(value)},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            now_ms(),
        )

    def media_library_search_event(
        self,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        self.event("info", "media_library_search", kind, payload)

    def media_library_analysis_event(
        self,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        self.event("info", "media_library_analysis", kind, payload)

    def set_setting(self, key: str, value: Any) -> None:
        self.settings_repo.set_json(key, value, now_ms())

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.settings_repo.get_json(key, default)

    def summary(self) -> dict[str, Any]:
        opencode = self.runtime_repo.get_runtime("opencode") or {}
        tunnel = self.runtime_repo.get_runtime("tunnel") or {}
        npc = self.runtime_repo.get_runtime("npc") or {}
        publish = self.runtime_repo.get_runtime("publish") or {}
        wecom_runtime = self.runtime_repo.get_runtime("wecom") or {}
        verify = self.verification_repo.latest()
        sessions = self.session_repo.sessions_summary()
        return {
            "opencode": opencode,
            "npc": npc,
            "tunnel": tunnel,
            "publish": publish or {"status": "idle", "message": "Waiting for URL input"},
            "wecom": wecom_runtime,
            "sessions": sessions,
            "verification": verify
            or {"status": "idle", "message": "No verification yet", "detail": None, "created_at": None},
        }

    def shutdown(self) -> None:
        if self.tunnel_process and self.tunnel_process.poll() is None:
            self.tunnel_process.send_signal(signal.SIGTERM)
        if self.npc_process and self.npc_process.poll() is None:
            self.npc_process.send_signal(signal.SIGTERM)
        clip_manager = getattr(self, "media_clip_job_manager", None)
        if clip_manager is not None:
            clip_manager.shutdown()
        self.engine.dispose()

    def reconcile_media_analysis_runs(self) -> None:
        from pathlib import Path

        from .media_library_analysis.lifecycle import finalize_analysis_tool_session
        from .tool_sessions import ToolSessionRunner

        for run in self.media_analysis_run_repo.active_runs():
            analysis_run_id = str(run["analysis_run_id"])
            tool_use_session_id = str(run.get("tool_use_session_id") or "")
            sync_errors: list[str] = []
            if tool_use_session_id:
                try:
                    runner = ToolSessionRunner.from_summary(
                        workspace_dir=Path(str(run["workspace_dir"])),
                        tool_use_session_id=tool_use_session_id,
                        session_id=int(run["session_id"]),
                    )
                    result = finalize_analysis_tool_session(
                        self, runner, terminal_status="failed"
                    )
                    sync_errors = list(result.errors)
                except Exception as exc:
                    sync_errors = [str(exc).strip() or exc.__class__.__name__]
            timestamp = now_ms()
            self.media_analysis_run_repo.finish_unsuccessful(
                analysis_run_id,
                status="failed",
                timestamp=timestamp,
                error_code="analysis_worker_lost",
                error={
                    "code": "analysis_worker_lost",
                    "user_message": "后端服务重启，本次未完成的分析已中断。",
                    "suggested_action": "请重新创建分析运行。",
                    "run_id": analysis_run_id,
                    "failed_step": "reconciliation",
                    "result_sync_errors": sync_errors,
                },
            )
        self.media_analysis_run_repo.reconcile_projections(timestamp=now_ms())

    def migrate_legacy_provider_keys(self) -> None:
        migrated = 0
        secret_updates: dict[str, str] = {}
        media_updates: list[dict[str, Any]] = []
        asr_updates: list[dict[str, Any]] = []
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
SELECT kind, provider, COALESCE(api_key_ref, '') AS api_key_ref, api_key_ciphertext
FROM tool_media_provider_configs
WHERE COALESCE(api_key_ciphertext, '') <> ''
"""
                )
            ).mappings().all()
            for row in rows:
                ref = str(row["api_key_ref"] or f"{row['kind']}_{row['provider']}_key")
                secret_updates[ref] = str(row["api_key_ciphertext"] or "")
                media_updates.append({"ref": ref, "kind": row["kind"], "provider": row["provider"]})
            asr_rows = conn.execute(
                text(
                    """
SELECT name, provider, COALESCE(api_key_ref, '') AS api_key_ref, api_key_ciphertext
FROM tool_asr_provider_configs
WHERE COALESCE(api_key_ciphertext, '') <> ''
"""
                )
            ).mappings().all()
            for row in asr_rows:
                ref = str(row["api_key_ref"] or f"{row['provider']}_key")
                secret_updates[ref] = str(row["api_key_ciphertext"] or "")
                asr_updates.append({"ref": ref, "name": row["name"]})
        if not secret_updates:
            return
        self.secret_store.set_many(secret_updates)
        with self.engine.begin() as conn:
            for item in media_updates:
                conn.execute(
                    text(
                        """
UPDATE tool_media_provider_configs
SET api_key_ref = :ref, api_key_ciphertext = NULL, updated_at = now()
WHERE kind = :kind AND provider = :provider
"""
                    ),
                    item,
                )
                migrated += 1
            for item in asr_updates:
                conn.execute(
                    text(
                        """
UPDATE tool_asr_provider_configs
SET api_key_ref = :ref, api_key_ciphertext = NULL, updated_at = now()
WHERE name = :name
"""
                    ),
                    item,
                )
                migrated += 1
        if migrated:
            self.event("info", "phase0", "Migrated provider keys into local secret store", {"count": migrated, "secret_store_path": str(self.secret_store.path)})
