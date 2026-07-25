from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from ..services.session_files import SessionFileService
from .io import WorkspacePathError, resolve_workspace_file
from .paths import ToolSessionPaths
from .schemas import OutputManifest, ResyncResult, ResultIndex


class SessionFileRepo(Protocol):
    def upsert_file(
        self,
        session_id: int,
        path: str,
        kind: str,
        size: int,
        origin: str,
        downloadable: int,
        updated_at: int,
        *,
        visibility: str | None = None,
        sensitivity: str | None = None,
        attempt_id: int | None = None,
        tool_use_session_id: str | None = None,
        stale: int = 0,
    ) -> None:
        ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _output_manifests(paths: ToolSessionPaths) -> list[OutputManifest]:
    manifests: list[OutputManifest] = []
    for path in sorted(paths.root.glob("S*_*/Output/OutputManifest.json")):
        try:
            manifests.append(OutputManifest.model_validate(_read_json(path)))
        except Exception:
            continue
    return manifests


def _manifest_file_policy(paths: ToolSessionPaths) -> dict[str, dict[str, Any]]:
    policies: dict[str, dict[str, Any]] = {}
    for manifest in _output_manifests(paths):
        for item in manifest.files:
            rel = item.path.strip().lstrip("/")
            if rel.startswith("tool_use_sessions/"):
                rel = rel.split(f"{paths.tool_use_session_id}/", 1)[-1]
            policies[rel] = {
                "kind": item.kind or "artifact",
                "visibility": item.visibility,
                "sensitivity": item.sensitivity,
                "downloadable": int(item.downloadable),
            }
    return policies


def _session_output_default_policy(session_output_rel: str, file_service: SessionFileService) -> dict[str, Any]:
    if session_output_rel.startswith("SessionOutput/manifests/"):
        return {"kind": "manifest", "visibility": "internal", "sensitivity": "normal", "downloadable": 0}
    policy = file_service.classify(session_output_rel)
    return {
        "kind": "artifact",
        "visibility": str(policy["visibility"]),
        "sensitivity": str(policy["sensitivity"]),
        "downloadable": int(policy["downloadable"]),
    }


def build_result_index(paths: ToolSessionPaths, *, attempt_id: int | None = None) -> ResultIndex:
    variables_path = paths.context_dir / "Variables.json"
    variables = _read_json(variables_path) if variables_path.exists() else {}
    tool_outputs: dict[str, Any] = {}
    for manifest in _output_manifests(paths):
        tool_outputs[manifest.tool_id] = {
            "step_id": manifest.step_id,
            "status": manifest.status,
            "files": [item.model_dump() for item in manifest.files],
        }

    schemes: dict[str, Any] = {}
    reports: dict[str, Any] = {}
    for directory, target in (("schemes", schemes), ("reports", reports)):
        root = paths.session_output_dir / directory
        if not root.exists():
            continue
        for item in sorted(path for path in root.rglob("*") if path.is_file()):
            rel = item.relative_to(paths.root).as_posix()
            target[item.stem] = rel

    return ResultIndex(
        attempt_id=attempt_id,
        tool_use_session_id=paths.tool_use_session_id,
        source_video=str(variables.get("source_video_path") or ""),
        clip_mode=str(variables.get("clip_mode") or ""),
        schemes=schemes,
        reports=reports,
        tool_outputs=tool_outputs,
        created_at=utc_now_iso(),
    )


def write_result_index(paths: ToolSessionPaths, *, attempt_id: int | None = None) -> Path:
    result_index = build_result_index(paths, attempt_id=attempt_id)
    path = paths.session_output_dir / "manifests" / "result_index.json"
    _write_json(path, result_index.model_dump())
    return path


@dataclass
class ToolSessionResultSync:
    session_repo: SessionFileRepo | None = None
    engine: Engine | None = None
    file_service: SessionFileService | None = None
    clock_ms: Any = now_ms

    def resync_tool_session(
        self,
        *,
        paths: ToolSessionPaths,
        attempt_id: int | None = None,
        session_id: int | None = None,
    ) -> ResyncResult:
        synced_files: list[str] = []
        synced_events: list[str] = ["tool_session.resync"]
        errors: list[str] = []

        try:
            result_index_path = write_result_index(paths, attempt_id=attempt_id)
            synced_files.append(result_index_path.relative_to(paths.root).as_posix())
            synced_events.append("result_index.write")
        except Exception as exc:
            return ResyncResult(
                tool_use_session_id=paths.tool_use_session_id,
                attempt_id=attempt_id,
                status="failed",
                synced_files=synced_files,
                synced_events=synced_events,
                errors=[str(exc)],
            )

        if self.engine is not None and attempt_id is not None:
            synced_events.extend(self._sync_attempt_result_index(paths, attempt_id=attempt_id, errors=errors))

        if self.session_repo is not None and session_id is not None:
            try:
                synced_files.extend(self.sync_session_output_files(paths=paths, session_id=session_id, attempt_id=attempt_id))
                synced_events.append("session_files.sync")
            except Exception as exc:
                errors.append(str(exc))

        return ResyncResult(
            tool_use_session_id=paths.tool_use_session_id,
            attempt_id=attempt_id,
            status="failed" if errors else "completed",
            synced_files=sorted(set(synced_files)),
            synced_events=sorted(set(synced_events)),
            errors=errors,
        )

    def sync_session_output_files(self, *, paths: ToolSessionPaths, session_id: int, attempt_id: int | None = None) -> list[str]:
        if self.session_repo is None:
            return []
        service = self.file_service or SessionFileService()
        policies = _manifest_file_policy(paths)
        synced: list[str] = []
        workspace_root = paths.workspace_dir
        session_output_root = paths.session_output_dir
        if not session_output_root.exists():
            return []

        for item in sorted(path for path in session_output_root.rglob("*") if path.is_file()):
            session_rel = item.relative_to(paths.root).as_posix()
            workspace_rel = item.relative_to(workspace_root).as_posix()
            try:
                _, resolved = resolve_workspace_file(workspace_root, workspace_rel, require_file=True)
            except WorkspacePathError:
                continue
            policy = {**_session_output_default_policy(session_rel, service), **policies.get(session_rel, {})}
            self.session_repo.upsert_file(
                session_id,
                workspace_rel,
                str(policy["kind"]),
                int(resolved.stat().st_size),
                "tool_session",
                int(policy["downloadable"]),
                int(self.clock_ms()),
                visibility=str(policy["visibility"]),
                sensitivity=str(policy["sensitivity"]),
                attempt_id=attempt_id,
                tool_use_session_id=paths.tool_use_session_id,
                stale=0,
            )
            synced.append(session_rel)
        return synced

    def _sync_attempt_result_index(self, paths: ToolSessionPaths, *, attempt_id: int, errors: list[str]) -> list[str]:
        if self.engine is None:
            return []
        result_index_path = paths.session_output_dir / "manifests" / "result_index.json"
        manifest_json = result_index_path.read_text(encoding="utf-8")
        synced: list[str] = []
        with self.engine.begin() as conn:
            inspector = inspect(conn)
            for table_name in ("openclip_attempts", "oc_rebuild_attempts"):
                if not inspector.has_table(table_name):
                    continue
                columns = {str(column["name"]) for column in inspector.get_columns(table_name)}
                if "result_manifest_json" not in columns:
                    continue
                updates = {"result_manifest_json": manifest_json}
                if "tool_use_session_id" in columns:
                    updates["tool_use_session_id"] = paths.tool_use_session_id
                assignments = ", ".join(f"{column} = :{column}" for column in updates)
                result = conn.execute(
                    text(f"UPDATE {table_name} SET {assignments} WHERE id = :attempt_id"),
                    {**updates, "attempt_id": attempt_id},
                )
                if result.rowcount:
                    synced.append(f"{table_name}.result_manifest_json")
        return synced
