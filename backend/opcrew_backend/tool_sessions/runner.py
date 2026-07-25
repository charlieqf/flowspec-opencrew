from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import importlib.util
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .paths import ToolSessionPaths
from .schemas import (
    DependencyCheckResult,
    MissingDependency,
    OutputFileEntry,
    OutputManifest,
    ResyncResult,
    SessionContextPatch,
    SessionRunSummary,
    State,
    ToolResult,
    Variables,
)


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class RunnerEventSink(Protocol):
    def add_event(
        self,
        session_id: int,
        kind: str,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> int:
        ...


class ToolAdapter(Protocol):
    def run(self, *, tool: dict[str, Any], step: "RunnerStep", paths: ToolSessionPaths, tool_dir: Path) -> ToolResult:
        ...


class RunnerResultSyncer(Protocol):
    def resync_tool_session(
        self,
        *,
        paths: ToolSessionPaths,
        attempt_id: int | None = None,
        session_id: int | None = None,
    ) -> ResyncResult:
        ...


ENVIRONMENT_HINT_TOKENS = {
    "gpu_or_mps_for_speed",
}

ANALYSIS_V1_PYTHON_ENV = "OPENCREW_ANALYSIS_V1_PYTHON"
ANALYSIS_V1_RUNTIME_DIR_ENV = "OPENCREW_ANALYSIS_V1_RUNTIME_DIR"
OPENCREW_DATA_DIR_ENV = "OPENCREW_DATA_DIR"
OPENCREW_FFMPEG_PATH_ENV = "OPENCREW_FFMPEG_PATH"
OPENCREW_FFPROBE_PATH_ENV = "OPENCREW_FFPROBE_PATH"

SAFE_SUBPROCESS_ENV_KEYS = {
    "HOME",
    "LANG",
    "LOGNAME",
    ANALYSIS_V1_PYTHON_ENV,
    ANALYSIS_V1_RUNTIME_DIR_ENV,
    OPENCREW_DATA_DIR_ENV,
    OPENCREW_FFMPEG_PATH_ENV,
    OPENCREW_FFPROBE_PATH_ENV,
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
}
SAFE_SUBPROCESS_ENV_PREFIXES = ("LC_",)
SENSITIVE_SUBPROCESS_ENV_PARTS = (
    "APIKEY",
    "API_KEY",
    "AUTH",
    "BEARER",
    "COOKIE",
    "CREDENTIAL",
    "DATABASE_URL",
    "DB_URL",
    "DSN",
    "KEY",
    "PASSWORD",
    "PASSWD",
    "PROXY",
    "SECRET",
    "TOKEN",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _opencrew_data_dir(parent_env: dict[str, str] | None = None) -> Path:
    env = parent_env or dict(os.environ)
    return Path(env.get(OPENCREW_DATA_DIR_ENV) or Path.home() / ".opencrew").expanduser()


def _analysis_v1_runtime_python_path(parent_env: dict[str, str] | None = None) -> Path:
    env = parent_env or dict(os.environ)
    runtime_dir = env.get(ANALYSIS_V1_RUNTIME_DIR_ENV)
    if runtime_dir:
        return Path(runtime_dir).expanduser() / "bin" / "python"
    return _opencrew_data_dir(env) / "runtimes" / "analysis_v1_py312" / "bin" / "python"


def _select_subprocess_python(
    *,
    configured_python: str | None,
    parent_env: dict[str, str] | None = None,
) -> str:
    if configured_python:
        return str(configured_python)
    env = parent_env or dict(os.environ)
    env_python = env.get(ANALYSIS_V1_PYTHON_ENV)
    if env_python:
        return str(Path(env_python).expanduser())
    managed_python = _analysis_v1_runtime_python_path(env)
    if managed_python.exists():
        return str(managed_python)
    return sys.executable


def _is_sensitive_env_key(key: str) -> bool:
    normalized = key.upper()
    return any(part in normalized for part in SENSITIVE_SUBPROCESS_ENV_PARTS)


def _is_safe_parent_env_key(key: str) -> bool:
    normalized = key.upper()
    if _is_sensitive_env_key(normalized):
        return False
    return normalized in SAFE_SUBPROCESS_ENV_KEYS or normalized.startswith(SAFE_SUBPROCESS_ENV_PREFIXES)


def _scrub_subprocess_env(
    *,
    parent_env: dict[str, str],
    extra_env: dict[str, str],
    repo_root: Path,
) -> dict[str, str]:
    env = {
        str(key): str(value)
        for key, value in parent_env.items()
        if _is_safe_parent_env_key(str(key))
    }
    env.setdefault("PATH", os.defpath)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault(OPENCREW_DATA_DIR_ENV, str(_opencrew_data_dir(parent_env)))
    env.setdefault(OPENCREW_FFMPEG_PATH_ENV, str(repo_root / "ToolLibrary" / ".bin" / "ffmpeg"))
    env.setdefault(OPENCREW_FFPROBE_PATH_ENV, str(repo_root / "ToolLibrary" / ".bin" / "ffprobe"))
    env["PYTHONPATH"] = os.pathsep.join([str(repo_root / "backend"), str(repo_root), str(repo_root.parent)])
    for key, value in extra_env.items():
        key_text = str(key)
        if _is_sensitive_env_key(key_text):
            raise ValueError(f"Refusing to pass sensitive environment variable to tool subprocess: {key_text}")
        env[key_text] = str(value)
    return env


@dataclass(frozen=True)
class NoopTool:
    tool_id: str = "noop"
    tool_name: str = "NoopTool"

    def run(self, *, step_id: str, tool_use_session_id: str, idempotency_key: str) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.tool_name,
            step_id=step_id,
            status="completed",
            outputs={"noop": True, "idempotency_key": idempotency_key},
            result_paths=[],
        )


@dataclass(frozen=True)
class SubprocessToolAdapter:
    python_executable: str | None = None
    repo_root: Path = field(default_factory=_repo_root)
    timeout_seconds: int | None = None
    extra_env: dict[str, str] = field(default_factory=dict)

    def run(self, *, tool: dict[str, Any], step: "RunnerStep", paths: ToolSessionPaths, tool_dir: Path) -> ToolResult:
        script = self._resolve_script(str(tool.get("script") or ""))
        command = [
            self.resolved_python_executable(),
            str(script),
            "--tool-session-root",
            str(paths.root),
            "--step-id",
            step.step_id,
            "--tool-id",
            step.tool_id,
            "--print-json",
        ]
        if step.retry_count > 0:
            command.append("--force-rerun")
        env = _scrub_subprocess_env(parent_env=dict(os.environ), extra_env=self.extra_env, repo_root=self.repo_root)
        completed = subprocess.run(command, cwd=str(paths.workspace_dir), env=env, capture_output=True, text=True, timeout=self.timeout_seconds, check=False)
        if completed.returncode != 0:
            return ToolResult(
                tool_id=step.tool_id,
                tool_name=step.tool_name,
                step_id=step.step_id,
                status="failed",
                errors=[(completed.stderr or completed.stdout or f"subprocess exited {completed.returncode}").strip()],
            )
        payload = self._parse_stdout_json(completed.stdout)
        if payload:
            return ToolResult.model_validate(payload)
        if (tool_dir / "Output" / "OutputManifest.json").exists():
            return ToolResult(tool_id=step.tool_id, tool_name=step.tool_name, step_id=step.step_id, status="completed")
        return ToolResult(tool_id=step.tool_id, tool_name=step.tool_name, step_id=step.step_id, status="completed", outputs={"stdout": completed.stdout.strip()})

    def resolved_python_executable(self) -> str:
        return _select_subprocess_python(configured_python=self.python_executable)

    def _resolve_script(self, script: str) -> Path:
        if not script:
            raise ValueError("tool script is required")
        path = Path(script)
        if path.is_absolute():
            return path
        parts = path.parts
        if parts and parts[0] == self.repo_root.name:
            path = Path(*parts[1:])
        return self.repo_root / path

    def _parse_stdout_json(self, stdout: str) -> dict[str, Any] | None:
        for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None


@dataclass
class RunnerStep:
    step_id: str
    tool_id: str
    tool_name: str
    step_index: int = 0
    status: str = "not_started"
    retry_count: int = 0
    idempotency_key: str = ""
    heartbeat_at: str = ""
    heartbeat_timeout_seconds: int = 0
    result: dict[str, Any] = field(default_factory=dict)


class ToolSessionRunner:
    def __init__(
        self,
        *,
        workspace_dir: Path,
        tool_use_session_id: str,
        attempt_id: int | None = None,
        heartbeat_timeout_seconds: int = 900,
        session_id: int | None = None,
        event_sink: RunnerEventSink | None = None,
        heartbeat_interval_seconds: float = 30.0,
    ) -> None:
        self.paths = ToolSessionPaths(workspace_dir=workspace_dir, tool_use_session_id=tool_use_session_id)
        self.tool_use_session_id = tool_use_session_id
        self.attempt_id = attempt_id
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.session_id = session_id
        self.event_sink = event_sink
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.steps: dict[str, RunnerStep] = {}
        self.status = "running"

    @classmethod
    def from_summary(
        cls,
        *,
        workspace_dir: Path,
        tool_use_session_id: str,
        heartbeat_timeout_seconds: int = 900,
        session_id: int | None = None,
        event_sink: RunnerEventSink | None = None,
        heartbeat_interval_seconds: float = 30.0,
    ) -> "ToolSessionRunner":
        paths = ToolSessionPaths(workspace_dir=workspace_dir, tool_use_session_id=tool_use_session_id)
        summary = _read_json(paths.session_report_dir / "SessionRunSummary.json")
        runner = cls(
            workspace_dir=workspace_dir,
            tool_use_session_id=tool_use_session_id,
            attempt_id=summary.get("attempt_id"),
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            session_id=session_id,
            event_sink=event_sink,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        runner.status = str(summary.get("status") or "running")
        for item in summary.get("steps") or []:
            step = RunnerStep(
                step_index=int(item.get("step_index") or 0),
                step_id=str(item.get("step_id") or ""),
                tool_id=str(item.get("tool_id") or ""),
                tool_name=str(item.get("tool_name") or ""),
                status=str(item.get("status") or "not_started"),
                retry_count=int(item.get("retry_count") or 0),
                idempotency_key=str(item.get("idempotency_key") or ""),
                heartbeat_at=str(item.get("heartbeat_at") or ""),
                heartbeat_timeout_seconds=int(item.get("heartbeat_timeout_seconds") or heartbeat_timeout_seconds),
                result=dict(item.get("result") or {}),
            )
            if step.step_id:
                runner.steps[step.step_id] = step
        return runner

    def step_idempotency_key(self, step_id: str, retry_count: int = 0) -> str:
        return f"{self.tool_use_session_id}:{step_id}:{retry_count}"

    def run_noop(self, step_id: str = "S1", *, force_rerun: bool = False) -> ToolResult:
        existing = self.steps.get(step_id)
        retry_count = (existing.retry_count + 1) if (force_rerun and existing) else (existing.retry_count if existing else 0)
        idempotency_key = self.step_idempotency_key(step_id, retry_count)
        tool = NoopTool()
        now = utc_now_iso()
        step = RunnerStep(
            step_index=1,
            step_id=step_id,
            tool_id=tool.tool_id,
            tool_name=tool.tool_name,
            status="running",
            retry_count=retry_count,
            idempotency_key=idempotency_key,
            heartbeat_at=now,
            heartbeat_timeout_seconds=self.heartbeat_timeout_seconds,
        )
        self.steps[step_id] = step
        self._emit_event("tool_session.step.started", step)

        tool_dir = self.paths.tool_dir(1, tool.tool_name)
        for part in ("Working", "Output", "Report", "Prompt"):
            (tool_dir / part).mkdir(parents=True, exist_ok=True)
        state = State(
            tool_use_session_id=self.tool_use_session_id,
            attempt_id=self.attempt_id,
            step_index=1,
            step_id=step_id,
            tool_id=tool.tool_id,
            tool_name=tool.tool_name,
            status="running",
            started_at=now,
            updated_at=now,
            heartbeat_at=now,
            retry_count=retry_count,
            idempotency_key=idempotency_key,
        )
        _write_json(tool_dir / "State.json", state.model_dump())

        result = tool.run(step_id=step_id, tool_use_session_id=self.tool_use_session_id, idempotency_key=idempotency_key)
        finished = utc_now_iso()
        state.status = "completed"
        state.updated_at = finished
        state.finished_at = finished
        state.heartbeat_at = finished
        output_manifest = OutputManifest(
            tool_use_session_id=self.tool_use_session_id,
            step_id=step_id,
            tool_id=tool.tool_id,
            status="completed",
            files=[],
        )
        _write_json(tool_dir / "State.json", state.model_dump())
        _write_json(tool_dir / "Output" / "OutputManifest.json", output_manifest.model_dump())
        step.status = "completed"
        step.heartbeat_at = finished
        step.result = result.model_dump()
        self._write_summary()
        self._emit_event("tool_session.step.completed", step)
        return result

    def run_registry_step(
        self,
        *,
        step_id: str,
        tool_id: str,
        normalized_registry: dict[str, Any],
        step_index: int = 1,
        adapters: dict[str, ToolAdapter] | None = None,
        force_rerun: bool = False,
    ) -> ToolResult:
        tool = self._lookup_registry_tool(normalized_registry, tool_id)
        tool_name = str(tool.get("tool_name") or tool.get("name") or tool_id)
        heartbeat_timeout = int(tool.get("heartbeat_timeout_seconds") or self.heartbeat_timeout_seconds)
        existing = self.steps.get(step_id)
        retry_count = (existing.retry_count + 1) if (force_rerun and existing) else (existing.retry_count if existing else 0)
        idempotency_key = self.step_idempotency_key(step_id, retry_count)
        step = RunnerStep(
            step_index=step_index,
            step_id=step_id,
            tool_id=tool_id,
            tool_name=tool_name,
            status="not_started",
            retry_count=retry_count,
            idempotency_key=idempotency_key,
            heartbeat_timeout_seconds=heartbeat_timeout,
        )
        self.steps[step_id] = step
        tool_dir = self.paths.tool_dir(step_index, tool_name)
        if force_rerun:
            self._reset_tool_dir(tool_dir)
        self._ensure_tool_dirs(tool_dir)

        dependency_result = self.check_dependencies(tool)
        if dependency_result.status == "blocked":
            result = ToolResult(
                tool_id=tool_id,
                tool_name=tool_name,
                step_id=step_id,
                status="blocked",
                errors=[item.suggested_action for item in dependency_result.missing_dependencies],
                outputs={"dependency_check": dependency_result.model_dump()},
            )
            return self._finish_step(tool=tool, step=step, tool_dir=tool_dir, result=result, status="blocked")

        adapter = (adapters or {}).get(tool_id) or (adapters or {}).get("*")
        if adapter is None:
            dependency_result = DependencyCheckResult(
                status="blocked",
                missing_dependencies=[
                    MissingDependency(
                        kind="tool_adapter",
                        required_from=tool_id,
                        suggested_action=f"No adapter registered for tool {tool_id}; business script migration is owned outside M4/M5 platform work.",
                    )
                ],
            )
            result = ToolResult(
                tool_id=tool_id,
                tool_name=tool_name,
                step_id=step_id,
                status="blocked",
                errors=[dependency_result.missing_dependencies[0].suggested_action],
                outputs={"dependency_check": dependency_result.model_dump()},
            )
            return self._finish_step(tool=tool, step=step, tool_dir=tool_dir, result=result, status="blocked")

        now = utc_now_iso()
        step.status = "running"
        step.heartbeat_at = now
        self._write_state(tool=tool, step=step, tool_dir=tool_dir, status="running", started_at=now, updated_at=now, heartbeat_at=now)
        self._emit_event("tool_session.step.started", step)
        stop_heartbeat = threading.Event()
        heartbeat_thread = self._start_heartbeat_thread(tool=tool, step=step, tool_dir=tool_dir, stop_event=stop_heartbeat)
        try:
            result = adapter.run(tool=tool, step=step, paths=self.paths, tool_dir=tool_dir)
        except Exception as exc:
            result = ToolResult(tool_id=tool_id, tool_name=tool_name, step_id=step_id, status="failed", errors=[str(exc)])
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=max(1.0, self.heartbeat_interval_seconds * 2))
        if result.context_patch:
            allowed_fields = set(tool.get("writes_session_context") or tool.get("variables_ownership") or [])
            try:
                self.merge_context_patch(SessionContextPatch(tool_id=tool_id, step_id=step_id, patch=result.context_patch), allowed_fields=allowed_fields)
            except Exception as exc:
                result = ToolResult(
                    tool_id=tool_id,
                    tool_name=tool_name,
                    step_id=step_id,
                    status="failed",
                    outputs=result.outputs,
                    warnings=result.warnings,
                    errors=[*result.errors, str(exc)],
                    result_paths=result.result_paths,
                    metrics=result.metrics,
                    context_patch=result.context_patch,
                )
        return self._finish_step(tool=tool, step=step, tool_dir=tool_dir, result=result, status=result.status)

    def check_dependencies(self, tool: dict[str, Any]) -> DependencyCheckResult:
        missing: list[MissingDependency] = []
        variables_path = self.paths.context_dir / "Variables.json"
        variables = _read_json(variables_path) if variables_path.exists() else {}

        for ref in tool.get("reads_session_context") or []:
            token = str(ref)
            if token == "source_video":
                required_path = str(variables.get("source_video_path") or "")
                if not required_path or not (self.paths.root / required_path).exists():
                    missing.append(MissingDependency(kind="session_context", required_from=token, required_path=required_path, suggested_action="Prepare source video in 0_SessionContext before running this step."))
            elif token == "source_video_or_audio":
                required_path = str(variables.get("source_video_path") or "")
                if not required_path or not (self.paths.root / required_path).exists():
                    missing.append(MissingDependency(kind="session_context", required_from=token, required_path=required_path, suggested_action="Prepare source media in 0_SessionContext before running this step."))
            elif token == "task_id" and variables.get("task_id") is None:
                missing.append(MissingDependency(kind="session_context", required_from=token, suggested_action="Populate task_id in Variables.json before running this step."))
            elif token == "opencode_image_model" and not (variables.get("prompt_model_id") or variables.get("run_model_id")):
                missing.append(MissingDependency(kind="session_context", required_from=token, suggested_action="Populate image model fields in Variables.json before running this step."))

        for item in tool.get("consumes_outputs") or []:
            upstream_tool_id = str(item.get("tool_id") or item.get("required_from") or "")
            required_path = str(item.get("path") or "")
            if required_path:
                exists = (self.paths.root / required_path).exists()
            else:
                exists = self._has_completed_output_manifest(upstream_tool_id)
            if not exists:
                producers = item.get("producer_tool_ids") or []
                producer_hint = " or ".join(str(value) for value in producers)
                if upstream_tool_id:
                    suggested_action = f"Run upstream tool {upstream_tool_id} before this step."
                    required_from = upstream_tool_id
                else:
                    required_from = producer_hint or str(item.get("source_token") or "upstream")
                    suggested_action = f"Run upstream producer {required_from} before this step."
                missing.append(MissingDependency(kind="tool_output", required_from=required_from, required_path=required_path, suggested_action=suggested_action))

        for token in tool.get("runtime_dependencies") or []:
            if str(token) in ENVIRONMENT_HINT_TOKENS:
                continue
            if shutil.which(str(token)) is None:
                missing.append(MissingDependency(kind="runtime_dependency", required_from=str(token), suggested_action=f"Install runtime dependency {token} before running this step."))

        for token in tool.get("python_package_dependencies") or []:
            if importlib.util.find_spec(str(token)) is None:
                missing.append(MissingDependency(kind="python_package", required_from=str(token), suggested_action=f"Install Python package {token} in the Analysis_V1 runtime before running this step."))

        for item in tool.get("data_asset_dependencies") or []:
            required_path = str(item.get("path") or "")
            if required_path and not (_repo_root() / required_path).exists():
                missing.append(MissingDependency(kind="data_asset", required_from=str(item.get("source_token") or required_path), required_path=required_path, suggested_action=f"Prepare required data asset {required_path} before running this step."))

        if missing:
            return DependencyCheckResult(status="blocked", missing_dependencies=missing)
        return DependencyCheckResult(status="ready")

    def merge_context_patch(self, patch: SessionContextPatch, *, allowed_fields: set[str]) -> dict[str, Any]:
        denied = sorted(set(patch.patch) - allowed_fields)
        if denied:
            raise ValueError(f"Context patch writes undeclared fields: {', '.join(denied)}")
        variables_path = self.paths.context_dir / "Variables.json"
        variables = _read_json(variables_path) if variables_path.exists() else {"schema_version": "1.0"}
        merged = {**variables, **patch.patch, "updated_at": utc_now_iso()}
        validated = Variables.model_validate(merged)
        _write_json(variables_path, validated.model_dump())
        return validated.model_dump()

    def mark_stale_running(self, step_id: str) -> bool:
        step = self.steps.get(step_id)
        if step is None or step.status != "running":
            return False
        step.status = "stale_running"
        self._write_summary()
        self._emit_event("tool_session.step.stale_running", step)
        return True

    def recover_stale_running_steps(self, *, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(timezone.utc)
        recovered: list[str] = []
        for step in list(self.steps.values()):
            if step.status != "running":
                continue
            heartbeat = _parse_iso_datetime(step.heartbeat_at)
            if heartbeat is None:
                continue
            age_seconds = (current - heartbeat).total_seconds()
            timeout = step.heartbeat_timeout_seconds or self.heartbeat_timeout_seconds
            if age_seconds > timeout and self.mark_stale_running(step.step_id):
                recovered.append(step.step_id)
        return recovered

    def mark_completed_with_sync_error(self) -> None:
        self.status = "completed_with_sync_error"
        self._write_summary()
        self._emit_event(
            "tool_session.sync_error",
            None,
            payload={"tool_use_session_id": self.tool_use_session_id, "attempt_id": self.attempt_id},
        )

    def resync(self, *, result_syncer: RunnerResultSyncer | None = None) -> ResyncResult:
        """MVP resync entry point.

        M5 will extend this path to rebuild attempt result indexes,
        session_files rows, and missing sync events from manifests.
        """
        if result_syncer is not None:
            result = result_syncer.resync_tool_session(paths=self.paths, attempt_id=self.attempt_id, session_id=self.session_id)
            self.status = result.status
            self._write_summary()
            self._emit_event("tool_session.resync.completed" if result.status == "completed" else "tool_session.resync.failed", None, payload=result.model_dump())
            return result

        result_index = self.paths.session_output_dir / "manifests" / "result_index.json"
        synced_files: list[str] = []
        errors: list[str] = []
        if result_index.exists():
            synced_files.append(result_index.relative_to(self.paths.root).as_posix())
            self.status = "completed"
            event_kind = "tool_session.resync.completed"
        else:
            errors.append("SessionOutput/manifests/result_index.json is missing")
            self.status = "failed"
            event_kind = "tool_session.resync.failed"
        self._write_summary()
        result = ResyncResult(
            tool_use_session_id=self.tool_use_session_id,
            attempt_id=self.attempt_id,
            status=self.status,
            synced_files=synced_files,
            synced_events=["tool_session.resync"],
            errors=errors,
        )
        self._emit_event(event_kind, None, payload=result.model_dump())
        return result

    def finalize_session(
        self,
        *,
        result_syncer: RunnerResultSyncer,
        terminal_status: str | None = None,
    ) -> ResyncResult:
        if terminal_status is not None and terminal_status not in {"completed", "failed", "blocked", "cancelled"}:
            raise ValueError(f"Unsupported tool session terminal status: {terminal_status}")
        result = result_syncer.resync_tool_session(paths=self.paths, attempt_id=self.attempt_id, session_id=self.session_id)
        if terminal_status is None:
            self.status = result.status
        elif result.status == "completed":
            self.status = terminal_status
        elif terminal_status == "completed":
            self.status = "completed_with_sync_error"
        else:
            self.status = f"{terminal_status}_with_sync_error"
        self._write_summary()
        self._emit_event(
            "tool_session.finalize.completed" if result.status == "completed" else "tool_session.finalize.failed",
            None,
            payload={
                **result.model_dump(),
                "requested_terminal_status": terminal_status,
                "summary_status": self.status,
            },
        )
        return result

    def _start_heartbeat_thread(self, *, tool: dict[str, Any], step: RunnerStep, tool_dir: Path, stop_event: threading.Event) -> threading.Thread:
        interval = max(0.01, float(self.heartbeat_interval_seconds))

        def run() -> None:
            while not stop_event.wait(interval):
                if step.status != "running":
                    return
                now = utc_now_iso()
                step.heartbeat_at = now
                self._write_state(tool=tool, step=step, tool_dir=tool_dir, status="running", updated_at=now, heartbeat_at=now)
                self._write_summary()

        thread = threading.Thread(target=run, name=f"tool-session-heartbeat-{step.step_id}", daemon=True)
        thread.start()
        return thread

    def _write_summary(self) -> None:
        summary = SessionRunSummary(
            tool_use_session_id=self.tool_use_session_id,
            attempt_id=self.attempt_id,
            status=self.status,
            steps=[
                {
                    "step_id": step.step_id,
                    "step_index": step.step_index,
                    "tool_id": step.tool_id,
                    "tool_name": step.tool_name,
                    "status": step.status,
                    "retry_count": step.retry_count,
                    "idempotency_key": step.idempotency_key,
                    "heartbeat_at": step.heartbeat_at,
                    "heartbeat_timeout_seconds": step.heartbeat_timeout_seconds,
                    "result": step.result,
                }
                for step in self.steps.values()
            ],
        )
        _write_json(self.paths.session_report_dir / "SessionRunSummary.json", summary.model_dump())

    def _emit_event(
        self,
        kind: str,
        step: RunnerStep | None,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.event_sink is None:
            return
        if self.session_id is None:
            logger.warning("Dropping tool session event without session_id", extra={"event_kind": kind, "tool_use_session_id": self.tool_use_session_id})
            return
        event_payload = payload or {
            "tool_use_session_id": self.tool_use_session_id,
            "attempt_id": self.attempt_id,
            "step_id": step.step_id if step else "",
            "tool_id": step.tool_id if step else "",
            "status": step.status if step else self.status,
            "retry_count": step.retry_count if step else 0,
            "idempotency_key": step.idempotency_key if step else "",
        }
        self.event_sink.add_event(
            self.session_id,
            kind,
            event_payload,
            attempt_id=self.attempt_id,
            tool_id=step.tool_id if step else None,
            step_id=step.step_id if step else None,
        )

    def _lookup_registry_tool(self, normalized_registry: dict[str, Any], tool_id: str) -> dict[str, Any]:
        for tool in normalized_registry.get("tools") or []:
            if str(tool.get("id") or tool.get("tool_id") or "") == tool_id:
                return dict(tool)
        raise ValueError(f"Tool {tool_id} is not present in normalized registry")

    def _ensure_tool_dirs(self, tool_dir: Path) -> None:
        for part in ("Working", "Output", "Report", "Prompt"):
            (tool_dir / part).mkdir(parents=True, exist_ok=True)

    def _reset_tool_dir(self, tool_dir: Path) -> None:
        root_resolved = self.paths.root.resolve()
        target = tool_dir.resolve() if tool_dir.exists() else tool_dir.parent.resolve() / tool_dir.name
        try:
            target.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("Refusing to clear tool directory outside Tool Use Session root") from exc
        if tool_dir.exists():
            shutil.rmtree(tool_dir)

    def _has_completed_output_manifest(self, upstream_tool_id: str) -> bool:
        if not upstream_tool_id:
            return False
        for path in self.paths.root.glob("S*_*/Output/OutputManifest.json"):
            try:
                manifest = OutputManifest.model_validate(_read_json(path))
            except Exception:
                continue
            if manifest.tool_id == upstream_tool_id and manifest.status == "completed":
                return True
        return False

    def _write_state(
        self,
        *,
        tool: dict[str, Any],
        step: RunnerStep,
        tool_dir: Path,
        status: str,
        started_at: str = "",
        updated_at: str = "",
        finished_at: str = "",
        heartbeat_at: str = "",
        error_summary: dict[str, Any] | None = None,
    ) -> None:
        state = State(
            tool_use_session_id=self.tool_use_session_id,
            attempt_id=self.attempt_id,
            step_index=step.step_index,
            step_id=step.step_id,
            tool_id=step.tool_id,
            tool_name=step.tool_name,
            status=status,
            started_at=started_at,
            updated_at=updated_at,
            finished_at=finished_at,
            heartbeat_at=heartbeat_at,
            retry_count=step.retry_count,
            idempotency_key=step.idempotency_key,
            error_summary=error_summary,
        )
        _write_json(tool_dir / "State.json", state.model_dump())

    def _finish_step(
        self,
        *,
        tool: dict[str, Any],
        step: RunnerStep,
        tool_dir: Path,
        result: ToolResult,
        status: str,
    ) -> ToolResult:
        finished = utc_now_iso()
        step.status = status
        step.heartbeat_at = finished
        step.result = result.model_dump()
        output_manifest = self._read_adapter_output_manifest(tool_dir, step=step, status=status) or self._output_manifest_for_result(tool=tool, step=step, result=result)
        _write_json(tool_dir / "Output" / "OutputManifest.json", output_manifest.model_dump())
        self._write_state(
            tool=tool,
            step=step,
            tool_dir=tool_dir,
            status=status,
            updated_at=finished,
            finished_at=finished,
            heartbeat_at=finished,
            error_summary={"errors": result.errors} if result.errors else None,
        )
        self._write_summary()
        self._emit_event(f"tool_session.step.{status}", step)
        return result

    def _read_adapter_output_manifest(self, tool_dir: Path, *, step: RunnerStep, status: str) -> OutputManifest | None:
        manifest_path = tool_dir / "Output" / "OutputManifest.json"
        if not manifest_path.exists():
            return None
        try:
            manifest = OutputManifest.model_validate(_read_json(manifest_path))
        except Exception as exc:
            raise ValueError(f"Adapter wrote invalid OutputManifest.json: {exc}") from exc
        if manifest.tool_use_session_id != self.tool_use_session_id:
            raise ValueError("Adapter OutputManifest tool_use_session_id does not match current Tool Use Session")
        if manifest.step_id != step.step_id or manifest.tool_id != step.tool_id:
            raise ValueError("Adapter OutputManifest step_id/tool_id does not match current step")
        if manifest.status != status:
            if status == "failed":
                return None
            raise ValueError("Adapter OutputManifest status does not match ToolResult status")
        return manifest

    def _output_manifest_for_result(self, *, tool: dict[str, Any], step: RunnerStep, result: ToolResult) -> OutputManifest:
        files: list[OutputFileEntry] = []
        for raw_path in result.result_paths:
            rel = str(raw_path).replace("\\", "/").strip().lstrip("/")
            path = self.paths.root / rel
            size = path.stat().st_size if path.exists() and path.is_file() else 0
            files.append(
                OutputFileEntry(
                    path=rel,
                    kind="artifact",
                    size=size,
                    visibility="public" if rel.startswith("SessionOutput/") else "internal",
                    sensitivity="normal",
                    downloadable=1 if rel.startswith("SessionOutput/") else 0,
                )
            )
        return OutputManifest(
            tool_use_session_id=self.tool_use_session_id,
            step_id=step.step_id,
            tool_id=step.tool_id,
            status=result.status,
            files=files,
        )
