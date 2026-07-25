from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_lib
import hmac
import io
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from sqlalchemy import text

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None

from ..adapters.opencode import OpenCodeSessionClient
from ..context import AppContext, now_ms
from ..routes.auth import session_secret
from ..services.media_thumbnails import ensure_media_thumbnail
from ..services.session_events import event_visible, present_event
from ..services.session_files import clean_relative_path, is_hidden_path, is_sensitive_path


def configured_upload_limit_bytes() -> int:
    raw = str(os.environ.get("OPENCREW_MAX_UPLOAD_BYTES") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 512 * 1024 * 1024


MAX_UPLOAD_BYTES = configured_upload_limit_bytes()
UPLOAD_CHUNK_BYTES = 1024 * 1024
DEFAULT_SHARE_TTL_MS = 7 * 24 * 60 * 60 * 1000
CACHEABLE_RAW_SUFFIXES = {
    ".aac",
    ".aif",
    ".aiff",
    ".caf",
    ".flac",
    ".gif",
    ".jpg",
    ".jpeg",
    ".m4a",
    ".m4v",
    ".mov",
    ".mp3",
    ".mp4",
    ".oga",
    ".ogg",
    ".opus",
    ".png",
    ".wav",
    ".weba",
    ".webm",
    ".webp",
}
NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
TRUTHY_QUERY_VALUES = {"1", "true", "yes", "y", "on", "download"}
DOWNLOAD_TOKEN_TTL_SECONDS = 60 * 60


def query_flag(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUTHY_QUERY_VALUES


def download_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    return {**(headers or {}), "X-Content-Type-Options": "nosniff"}


def build_session_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    session_ref_pattern = re.compile(r"^#?(\d+)(?:\s+(.*))?$")

    def forwarded_prefix(request: Request) -> str:
        raw = str(request.headers.get("x-forwarded-prefix") or "").strip()
        if not raw:
            return ""
        value = raw if raw.startswith("/") else f"/{raw}"
        return value.rstrip("/")

    def external_origin(request: Request) -> str:
        prefix = forwarded_prefix(request)
        proto = str(request.headers.get("x-forwarded-proto") or request.url.scheme).strip() or request.url.scheme
        host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).strip() or request.url.netloc
        return f"{proto}://{host}{prefix}"

    def session_share_url(request: Request, token: str) -> str:
        return f"{external_origin(request)}/session/share/{token}"

    def encode_file_id(path: str) -> str:
        return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")

    def encode_raw_file_path(path: str) -> str:
        return "/".join(urllib.parse.quote(part, safe="") for part in str(path or "").split("/"))

    def make_download_token(session_id: int, relative_path: str) -> tuple[str, int]:
        expires_at = int(time.time()) + DOWNLOAD_TOKEN_TTL_SECONDS
        payload = {"sid": int(session_id), "path": relative_path, "exp": expires_at}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")
        signature = hmac.new(session_secret(ctx).encode("utf-8"), f"download.{body}".encode("utf-8"), hashlib.sha256).digest()
        token = f"{body}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"
        return token, expires_at

    def verify_download_token(token: str, session_id: int, relative_path: str) -> None:
        value = str(token or "").strip()
        if "." not in value:
            raise HTTPException(status_code=403, detail="Invalid download token")
        body, signature = value.split(".", 1)
        expected = hmac.new(session_secret(ctx).encode("utf-8"), f"download.{body}".encode("utf-8"), hashlib.sha256).digest()
        try:
            actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=403, detail="Invalid download token") from exc
        if not hmac.compare_digest(actual, expected):
            raise HTTPException(status_code=403, detail="Invalid download token")
        if int(payload.get("exp") or 0) < int(time.time()):
            raise HTTPException(status_code=403, detail="Download token expired")
        if int(payload.get("sid") or 0) != int(session_id) or str(payload.get("path") or "") != relative_path:
            raise HTTPException(status_code=403, detail="Download token scope mismatch")

    def frontend_task_url(request: Request, session_id: int) -> str:
        return f"{external_origin(request)}/#/sessions/task/{session_id}"

    def decode_file_id(value: str) -> str:
        padding = "=" * (-len(value) % 4)
        try:
            return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid file id") from exc

    def workspace_dirs(session_row: dict[str, Any]) -> dict[str, Path]:
        root = Path(str(session_row["workspace_dir"]))
        return {
            "workspace": root,
            "inbox": root / "inbox",
            "outbox": root / "outbox",
            "meta": root / "meta",
        }

    def ensure_workspace(session_row: dict[str, Any]) -> None:
        workspace_dirs(session_row)["workspace"].mkdir(parents=True, exist_ok=True)

    def safe_join(root: Path, relative_path: str) -> Path:
        cleaned = relative_path.replace("\\", "/").strip().lstrip("/")
        if not cleaned or ".." in cleaned.split("/"):
            raise HTTPException(status_code=400, detail="Invalid file path")
        resolved = (root / cleaned).resolve()
        if root.resolve() not in {resolved, *resolved.parents}:
            raise HTTPException(status_code=400, detail="File path escapes workspace")
        return resolved

    def safe_read_join(root: Path, relative_path: str) -> Path:
        cleaned = relative_path.replace("\\", "/").strip().lstrip("/")
        if not cleaned or ".." in cleaned.split("/"):
            raise HTTPException(status_code=400, detail="Invalid file path")
        candidate = root / cleaned
        lexical = candidate.absolute()
        root_abs = root.absolute()
        if root_abs not in {lexical, *lexical.parents}:
            raise HTTPException(status_code=400, detail="File path escapes workspace")
        return candidate

    def clear_workspace(session_row: dict[str, Any]) -> None:
        root = Path(str(session_row["workspace_dir"]))
        if not root.exists():
            return
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    def session_runtime_seconds(session_row: dict[str, Any]) -> float:
        started = int(session_row.get("started_at") or 0)
        if not started:
            return 0.0
        finished = int(session_row.get("finished_at") or now_ms())
        return max(0.0, (finished - started) / 1000)

    def session_system_metrics() -> dict[str, Any]:
        if psutil is None:
            return {"cpu_percent": 0.0, "memory_percent": 0.0, "memory_used_mb": 0.0}
        try:
            memory = psutil.virtual_memory()
            return {
                "cpu_percent": float(psutil.cpu_percent(interval=None)),
                "memory_percent": float(memory.percent),
                "memory_used_mb": round(float(memory.used) / (1024 * 1024), 1),
            }
        except Exception:
            return {"cpu_percent": 0.0, "memory_percent": 0.0, "memory_used_mb": 0.0}

    def get_session(session_id: int) -> dict[str, Any]:
        row = ctx.session_repo.get(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        expected_workspace = ctx.workspace_store.sessions_root() / str(session_id) / "workspace"
        current_workspace = Path(str(row.get("workspace_dir") or ""))
        if expected_workspace.exists() and current_workspace != expected_workspace:
            ctx.session_repo.update(session_id, workspace_dir=str(expected_workspace), updated_at=now_ms())
            row["workspace_dir"] = str(expected_workspace)
        return row

    def parse_session_reference(raw: str) -> tuple[Optional[int], str]:
        match = session_ref_pattern.fullmatch((raw or "").strip())
        if not match:
            return None, ""
        return int(match.group(1)), str(match.group(2) or "").strip()

    def get_share(token: str) -> dict[str, Any]:
        row = ctx.session_repo.get_share(token)
        if not row:
            raise HTTPException(status_code=404, detail="Share not found")
        if int(row.get("expires_at") or 0) < now_ms():
            raise HTTPException(status_code=410, detail="Share expired")
        return row

    def add_session_event(session_id: int, kind: str, payload: dict[str, Any] | None, **event_fields: Any) -> int:
        if not ctx.session_repo.exists(session_id):
            return 0
        return ctx.session_event_service.add_event(session_id, kind, payload or {}, **event_fields)

    def emit_legacy_fallback_event(session_id: int, *, reason: str, path: str = "") -> None:
        add_session_event(
            session_id,
            "tool_session.legacy_fallback",
            {
                "reason": reason,
                "path": path,
                "stale_warning": "Legacy fallback is a migration-only path and may show stale artifacts.",
            },
            visibility="internal",
            event_scope="debug",
            severity="warning",
            family="tool_session",
        )

    def update_session_status(session_id: int, status: str, **fields: Any) -> None:
        if not ctx.session_repo.exists(session_id):
            return
        ctx.session_repo.update(session_id, status=status, updated_at=now_ms(), **fields)

    def session_current_status(session_id: int) -> str:
        row = ctx.session_repo.get(session_id) or {}
        return str(row.get("status") or "")

    def latest_attempt_result_index(session_id: int) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        with ctx.engine.connect() as conn:
            for table_name in ("openclip_attempts", "oc_rebuild_attempts"):
                rows = conn.execute(
                    text(
                        f"""
SELECT id, created_at, result_manifest_json, tool_use_session_id
FROM {table_name}
WHERE session_id = :session_id
ORDER BY id DESC
LIMIT 1
"""
                    ),
                    {"session_id": session_id},
                ).mappings().all()
                for row in rows:
                    attempts.append({"workflow_table": table_name, **dict(row)})
        attempts.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
        attempt = attempts[0] if attempts else {}
        manifest_raw = str(attempt.get("result_manifest_json") or "").strip()
        if manifest_raw:
            try:
                result_index = json.loads(manifest_raw)
            except Exception:
                result_index = {}
            if isinstance(result_index, dict) and result_index:
                return {
                    "source": "attempt_result_index",
                    "attempt_id": attempt.get("id"),
                    "tool_use_session_id": result_index.get("tool_use_session_id") or attempt.get("tool_use_session_id") or "",
                    "result_index": result_index,
                    "files": result_index_files(result_index),
                }

        rows = ctx.session_repo.list_files(session_id)
        session_output_files = [
            row
            for row in rows
            if "/SessionOutput/" in str(row.get("path") or "") and int(row.get("downloadable") or 0) == 1
        ]
        if session_output_files:
            return {
                "source": "session_files",
                "attempt_id": attempt.get("id"),
                "tool_use_session_id": str(session_output_files[0].get("tool_use_session_id") or attempt.get("tool_use_session_id") or ""),
                "result_index": {},
                "files": [{**row, "file_id": encode_file_id(str(row.get("path") or ""))} for row in session_output_files],
            }

        emit_legacy_fallback_event(session_id, reason="missing_attempt_result_index_and_session_output")
        return {
            "source": "legacy_fallback",
            "attempt_id": attempt.get("id"),
            "tool_use_session_id": str(attempt.get("tool_use_session_id") or ""),
            "result_index": {},
            "files": [],
        }

    def result_index_files(result_index: dict[str, Any]) -> list[dict[str, Any]]:
        tool_use_session_id = str(result_index.get("tool_use_session_id") or "")
        files: list[dict[str, Any]] = []
        for output in (result_index.get("tool_outputs") or {}).values():
            for item in (output or {}).get("files") or []:
                path = str((item or {}).get("path") or "").strip().lstrip("/")
                if not path:
                    continue
                workspace_path = path if path.startswith("tool_use_sessions/") else f"tool_use_sessions/{tool_use_session_id}/{path}"
                files.append(
                    {
                        **dict(item or {}),
                        "path": workspace_path,
                        "file_id": encode_file_id(workspace_path),
                    }
                )
        return files

    def sync_session_files(session_row: dict[str, Any]) -> list[dict[str, Any]]:
        ensure_workspace(session_row)
        dirs = workspace_dirs(session_row)
        workspace = dirs["workspace"]
        workspace_resolved = workspace.resolve()
        rows: list[dict[str, Any]] = []
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            try:
                path.resolve().relative_to(workspace_resolved)
            except ValueError:
                continue
            rel = path.relative_to(workspace).as_posix()
            if rel == "tool_use_sessions" or rel.startswith("tool_use_sessions/"):
                continue
            if rel.startswith("meta/"):
                continue
            stat = path.stat()
            origin = "uploaded" if rel.startswith("inbox/") else "generated"
            policy = ctx.session_file_service.classify(rel)
            ctx.session_repo.upsert_file(
                int(session_row["id"]),
                rel,
                "file",
                int(stat.st_size),
                origin,
                int(policy["downloadable"]),
                int(stat.st_mtime * 1000),
                visibility=str(policy["visibility"]),
                sensitivity=str(policy["sensitivity"]),
            )
        rows = ctx.session_repo.list_files(int(session_row["id"]))
        for row in rows:
            row["file_id"] = encode_file_id(str(row["path"]))
        return rows

    def list_session_events(session_id: int, since: int = 0, audience: str = "customer") -> list[dict[str, Any]]:
        rows = ctx.session_repo.list_events(session_id, since, 500)
        return [present_event(row) for row in rows if event_visible(row, audience)]

    def log_payload_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload or "").strip()
        for key in ("message", "text", "status", "reason", "path"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value).replace("\n", " ").strip()[:1000]
        if not payload:
            return ""
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)[:1000]

    def log_lines(session_id: int) -> list[str]:
        rows = ctx.session_repo.list_events(session_id, 0, 500)
        lines: list[str] = []
        for row in rows:
            if not event_visible(row, "customer"):
                continue
            item = present_event(row)
            message = log_payload_summary(item.get("payload"))
            line = f"{item.get('created_at') or ''} {item.get('kind') or ''} {message}".strip()
            if line:
                lines.append(line)
        return lines

    def detail_from_row(
        session_row: dict[str, Any],
        request: Request | None = None,
        *,
        include_files: bool = False,
        events_count: int | None = None,
    ) -> dict[str, Any]:
        result = dict(session_row)
        result["files"] = sync_session_files(session_row) if include_files else []
        result["execution_seconds"] = session_runtime_seconds(session_row)
        if events_count is None:
            events_count = ctx.session_repo.count_events(int(session_row["id"]))
        result["events_count"] = int(events_count)
        if request and session_row.get("share_token"):
            result["share_url"] = session_share_url(request, str(session_row["share_token"]))
            result["task_url"] = frontend_task_url(request, int(session_row["id"]))
        return result

    def list_task_items(request: Request, group_id: Optional[str] = None) -> list[dict[str, Any]]:
        rows = ctx.session_repo.list(group_id, 200)
        session_ids = [int(row["id"]) for row in rows]
        event_counts = ctx.session_repo.count_events_for_sessions(session_ids)
        items = []
        for row in rows:
            detail = detail_from_row(row, request, include_files=False, events_count=event_counts.get(int(row["id"]), 0))
            detail["task_id"] = int(row["id"])
            detail["task_number"] = str(row["title"])
            detail["task_label"] = f"#{row['id']}"
            detail["prompt_summary"] = str(row.get("command_text") or "")[:180]
            items.append(detail)
        return items

    def list_directory_entries(session_row: dict[str, Any], relative_path: str = "") -> dict[str, Any]:
        root = Path(str(session_row["workspace_dir"]))
        current = root if not relative_path else safe_join(root, relative_path)
        if not current.exists() or not current.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        items = []
        for child in sorted(current.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            if child.name.startswith("."):
                continue
            try:
                child.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            rel = child.relative_to(root).as_posix()
            if is_hidden_path(rel) or is_sensitive_path(rel):
                continue
            if rel == "meta" or rel.startswith("meta/") or rel == "history" or rel.startswith("history/") or rel == "tool_use_sessions" or rel.startswith("tool_use_sessions/"):
                continue
            if child.is_file() and int(ctx.session_file_service.classify(rel)["downloadable"]) != 1:
                continue
            items.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else 0,
                "path": rel,
            })
        return {"path": current.relative_to(root).as_posix() if current != root else "", "files": items}

    def delete_session_record(session_id: int) -> dict[str, Any]:
        row = get_session(session_id)
        ctx.workflow_deletion_service.delete_session_db_first(row)
        active_openflow_session_id = int(ctx.get_setting("openflow.analysis.active_session_id") or 0)
        if active_openflow_session_id == session_id:
            ctx.set_setting("openflow.analysis.active_session_id", 0)
        try:
            ctx.workflow_deletion_service.cleanup_workspace(row)
        except Exception as exc:
            ctx.event("warning", "cleanup", "Workspace cleanup failed after DB deletion", {"session_id": session_id, "workspace_dir": row.get("workspace_dir"), "error": str(exc)})
        return {"ok": True, "deleted_id": session_id, "deleted_title": row["title"]}

    def zip_directory_bytes(session_row: dict[str, Any], relative_path: str = "") -> bytes:
        root = Path(str(session_row["workspace_dir"]))
        current = root if not relative_path else ctx.session_file_service.resolve_workspace_path(root, relative_path, require_file=False)[1]
        if not current.exists():
            raise HTTPException(status_code=404, detail="Path not found")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for item, arcname in ctx.session_file_service.zip_entries(root, current):
                archive.write(item, arcname=arcname)
        return buffer.getvalue()

    def opencode_client_for(session_row: dict[str, Any]) -> OpenCodeSessionClient:
        base_url = str(ctx.get_setting("opencode.base_url") or "").strip()
        username = str(ctx.get_setting("opencode.username") or "").strip()
        password = str(ctx.get_setting("opencode.password") or "").strip()
        if not base_url or not username or not password:
            raise RuntimeError("OpenCode connection is incomplete. Finish Step 1 before creating sessions.")
        return OpenCodeSessionClient(base_url=base_url, username=username, password=password, directory=str(session_row["workspace_dir"]))

    def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str | None:
        for message in reversed(messages):
            info = message.get("info") or {}
            if info.get("role") != "assistant":
                continue
            completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
            if completed < started_after:
                continue
            texts = [str(part.get("text") or "") for part in (message.get("parts") or []) if part.get("type") == "text"]
            text = "\n".join([item.strip() for item in texts if item.strip()]).strip()
            if text:
                return text
        return None

    def sync_session_todos(session_row: dict[str, Any]) -> None:
        try:
            client = opencode_client_for(session_row)
            todos = client.todo(str(session_row["opencode_session_id"]))
            add_session_event(session_row["id"], "todo.snapshot", {"items": todos})
        except Exception:
            return

    def create_or_refresh_share(session_id: int, scope: str) -> dict[str, Any]:
        row = get_session(session_id)
        token = str(row.get("share_token") or ctx.new_share_token())
        expires_at = now_ms() + DEFAULT_SHARE_TTL_MS
        ctx.session_repo.upsert_share(session_id, token, scope, expires_at, now_ms())
        ctx.session_repo.update(session_id, share_token=token, updated_at=now_ms())
        return {"ok": True, "token": token, "scope": scope, "expires_at": expires_at}

    async def store_session_upload(session_id: int, upload: UploadFile, path: str = "") -> dict[str, Any]:
        row = get_session(session_id)
        ensure_workspace(row)
        target_rel = path.strip() or f"inbox/{upload.filename or 'upload.bin'}"
        if not target_rel.startswith("inbox/"):
            target_rel = f"inbox/{Path(target_rel).name}"
        target = safe_join(Path(str(row["workspace_dir"])), target_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        temp_target = target.with_name(f".{target.name}.upload-{time.time_ns()}")
        try:
            with temp_target.open("wb") as handle:
                while True:
                    chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail=f"File exceeds upload limit ({MAX_UPLOAD_BYTES // (1024 * 1024)} MiB)")
                    handle.write(chunk)
            temp_target.replace(target)
        except Exception:
            try:
                temp_target.unlink()
            except FileNotFoundError:
                pass
            raise
        add_session_event(session_id, "file.uploaded", {"path": target_rel, "bytes": total, "name": upload.filename})
        sync_session_files(row)
        return {"ok": True, "path": target_rel, "bytes": total}

    def stream_prompt(session_id: int, prompt: str) -> None:
        session_row = get_session(session_id)
        prompt_started_at = now_ms()
        update_session_status(session_id, "running", started_at=prompt_started_at)
        add_session_event(session_id, "user.message", {"text": prompt})
        stop_event = threading.Event()

        def on_event(payload: dict[str, Any]) -> None:
            if session_current_status(session_id) == "cancelled":
                return
            raw_kind = str(payload.get("type") or "event").strip() or "event"
            kind = raw_kind if raw_kind.startswith("opencode.") else f"opencode.{raw_kind}"
            raw_properties = payload.get("properties") or {}
            properties = raw_properties if isinstance(raw_properties, dict) else {}
            add_session_event(
                session_id,
                kind,
                properties if isinstance(raw_properties, dict) else {"value": raw_properties},
                visibility="internal",
                event_scope="debug",
                family="opencode",
            )
            if raw_kind == "session.idle":
                update_session_status(session_id, "waiting_input")
            elif raw_kind == "session.status":
                status = str(((properties.get("status") or {}).get("type") or "running")).strip() or "running"
                update_session_status(session_id, status)

        try:
            client = opencode_client_for(session_row)
            collector = threading.Thread(
                target=client.collect_events,
                args=(str(session_row["opencode_session_id"]), stop_event, on_event),
                daemon=True,
            )
            collector.start()
            client.prompt_async(str(session_row["opencode_session_id"]), prompt)
            deadline = time.time() + 600
            assistant_text: str | None = None
            while time.time() < deadline:
                if session_current_status(session_id) == "cancelled":
                    return
                messages = client.messages(str(session_row["opencode_session_id"]), limit=80)
                assistant_text = last_completed_assistant(messages, prompt_started_at)
                if assistant_text:
                    break
                time.sleep(1)
            if not assistant_text:
                raise RuntimeError("OpenCode timed out before returning an assistant response")
            if session_current_status(session_id) == "cancelled":
                return
            add_session_event(session_id, "assistant.final", {"text": assistant_text})
            update_session_status(session_id, "waiting_input", last_summary=assistant_text[:4000], finished_at=now_ms())
            sync_session_files(get_session(session_id))
            sync_session_todos(get_session(session_id))
        except Exception as exc:
            if session_current_status(session_id) == "cancelled":
                return
            add_session_event(session_id, "session.error", {"message": str(exc)})
            update_session_status(session_id, "failed", finished_at=now_ms())
        finally:
            stop_event.set()

    def start_prompt_thread(session_id: int, prompt: str) -> None:
        thread = threading.Thread(target=stream_prompt, args=(session_id, prompt), daemon=True)
        thread.start()

    def create_session_record(group_id: str, sender_name: str, command_text: str) -> dict[str, Any]:
        title = ctx.next_session_title()
        share_token = ctx.new_share_token()
        created = now_ms()
        session_id = ctx.session_repo.create(
            source="im-simulator",
            group_id=group_id,
            sender_name=sender_name,
            title=title,
            command_text=command_text,
            status="queued",
            workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"),
            share_token=share_token,
            created_at=created,
            updated_at=created,
        )
        workspace_dir = ctx.workspace_store.create_session_workspace(session_id)
        ctx.session_repo.update(session_id, workspace_dir=str(workspace_dir), updated_at=created)
        add_session_event(session_id, "session.created", {"title": title, "group_id": group_id, "sender_name": sender_name})
        return get_session(session_id)

    def create_real_session(request: Request, group_id: str, sender_name: str, prompt: str) -> dict[str, Any]:
        row = create_session_record(group_id, sender_name, prompt)
        try:
            client = opencode_client_for(row)
            op_session = client.create_session(str(row["title"]))
            next_status = "running" if prompt.strip() else "waiting_input"
            ctx.session_repo.update(
                int(row["id"]),
                opencode_session_id=str(op_session["id"]),
                status=next_status,
                started_at=now_ms(),
                updated_at=now_ms(),
            )
            add_session_event(row["id"], "session.bound", {"opencode_session_id": str(op_session["id"])})
            if prompt.strip():
                start_prompt_thread(int(row["id"]), prompt)
            return detail_from_row(get_session(int(row["id"])), request)
        except Exception as exc:
            add_session_event(int(row["id"]), "session.error", {"message": str(exc)})
            update_session_status(int(row["id"]), "failed", finished_at=now_ms())
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def rerun_real_session(request: Request, session_row: dict[str, Any]) -> dict[str, Any]:
        if str(session_row.get("source") or "") == "openflow-analysis":
            raise HTTPException(status_code=400, detail="OpenFlow sessions must use the OpenFlow rerun flow")
        if not ctx.session_repo.claim_for_rerun(
            int(session_row["id"]),
            busy_statuses=("running", "queued", "rerun_preparing"),
            claim_status="rerun_preparing",
            updated_at=now_ms(),
        ):
            raise HTTPException(status_code=409, detail="Stop the active run before rerunning this session")
        prompt = str(session_row.get("command_text") or "").strip()
        # Workspace reset runs under the claimed "rerun_preparing" sentinel; any
        # failure here must clear it (to "failed") so the session is not stuck busy.
        try:
            clear_workspace(session_row)
            ensure_workspace(session_row)
            ctx.session_repo.delete_files_for_session(int(session_row["id"]))
            ctx.session_repo.delete_events_for_session(int(session_row["id"]))
            ctx.session_repo.update(
                int(session_row["id"]),
                status="queued",
                opencode_session_id=None,
                last_summary=None,
                started_at=None,
                finished_at=None,
                updated_at=now_ms(),
            )
        except Exception as exc:
            update_session_status(int(session_row["id"]), "failed", finished_at=now_ms())
            add_session_event(int(session_row["id"]), "session.error", {"message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        add_session_event(int(session_row["id"]), "session.rerun", {"source": str(session_row.get("source") or "im-simulator")})
        refreshed = get_session(int(session_row["id"]))
        try:
            client = opencode_client_for(refreshed)
            op_session = client.create_session(str(refreshed["title"]))
            next_status = "running" if prompt else "waiting_input"
            ctx.session_repo.update(
                int(refreshed["id"]),
                opencode_session_id=str(op_session["id"]),
                status=next_status,
                started_at=now_ms() if prompt else None,
                finished_at=None,
                updated_at=now_ms(),
            )
            add_session_event(int(refreshed["id"]), "session.bound", {"opencode_session_id": str(op_session["id"]), "rerun": True})
            if prompt:
                start_prompt_thread(int(refreshed["id"]), prompt)
            return detail_from_row(get_session(int(refreshed["id"])), request)
        except Exception as exc:
            add_session_event(int(refreshed["id"]), "session.error", {"message": str(exc)})
            update_session_status(int(refreshed["id"]), "failed", finished_at=now_ms())
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def continue_session(request: Request, session_row: dict[str, Any], prompt: str) -> dict[str, Any]:
        if not session_row.get("opencode_session_id"):
            raise HTTPException(status_code=400, detail="Session is not bound to OpenCode")
        start_prompt_thread(int(session_row["id"]), prompt)
        return detail_from_row(get_session(int(session_row["id"])), request)

    @router.get("/api/sessions")
    async def sessions(request: Request, group_id: Optional[str] = None) -> dict[str, Any]:
        return {"items": list_task_items(request, group_id), "metrics": session_system_metrics()}

    @router.get("/api/session-tasks")
    async def session_tasks(request: Request, group_id: Optional[str] = None) -> dict[str, Any]:
        items = list_task_items(request, group_id)
        metrics = session_system_metrics()
        summary = {
            "total": len(items),
            "running": len([item for item in items if item.get("status") == "running"]),
            "waiting": len([item for item in items if item.get("status") == "waiting_input"]),
            "failed": len([item for item in items if item.get("status") == "failed"]),
            **metrics,
        }
        return {"items": items, "summary": summary}

    @router.get("/api/sessions/{session_id}")
    async def session_detail(session_id: int, request: Request) -> dict[str, Any]:
        return detail_from_row(get_session(session_id), request, include_files=False)

    @router.get("/api/session-tasks/{session_id}")
    async def session_task_detail(session_id: int, request: Request) -> dict[str, Any]:
        row = get_session(session_id)
        detail = detail_from_row(row, request, include_files=False)
        detail["messages"] = []
        detail["logs"] = []
        detail["task_id"] = int(row["id"])
        detail["task_number"] = str(row["title"])
        detail["task_label"] = f"#{row['id']}"
        return detail

    @router.delete("/api/session-tasks/{session_id}")
    async def session_task_delete(session_id: int) -> dict[str, Any]:
        return delete_session_record(session_id)

    @router.post("/api/session-tasks/{session_id}/cancel")
    async def session_task_cancel(session_id: int) -> dict[str, Any]:
        row = get_session(session_id)
        update_session_status(session_id, "cancelled", finished_at=now_ms())
        add_session_event(session_id, "session.cancelled", {"task_id": session_id, "title": row.get("title")})
        return {"ok": True, "task_id": session_id, "status": "cancelled"}

    @router.post("/api/session-tasks/{session_id}/rerun")
    async def session_task_rerun(session_id: int, request: Request) -> dict[str, Any]:
        row = get_session(session_id)
        session = rerun_real_session(request, row)
        return {"ok": True, "session_id": session_id, "session": session, "task_url": frontend_task_url(request, session_id)}

    @router.get("/api/sessions/{session_id}/events")
    async def session_events(session_id: int, since: int = 0, audience: str = Query(default="customer")) -> dict[str, Any]:
        get_session(session_id)
        return {"items": list_session_events(session_id, since, "debug" if audience == "debug" else "customer")}

    @router.get("/api/sessions/{session_id}/events/stream")
    async def session_events_stream(session_id: int, request: Request, since: int = Query(default=0), audience: str = Query(default="customer")) -> StreamingResponse:
        get_session(session_id)
        event_audience = "debug" if audience == "debug" else "customer"

        async def event_generator() -> Any:
            cursor = since
            last_keepalive = time.monotonic()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    rows = ctx.session_repo.stream_session_events(session_id, cursor, 100)
                    if rows:
                        for row in rows:
                            cursor = int(row["id"])
                            if not event_visible(row, event_audience):
                                continue
                            yield f"data: {json.dumps(present_event(row), ensure_ascii=True)}\n\n"
                    elif time.monotonic() - last_keepalive >= 20:
                        last_keepalive = time.monotonic()
                        yield ": keepalive\n\n"
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                return

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.get("/api/sessions/{session_id}/files")
    async def session_files(session_id: int) -> dict[str, Any]:
        row = get_session(session_id)
        return {"items": sync_session_files(row)}

    @router.get("/api/session-tasks/{session_id}/result-index")
    async def session_task_result_index(session_id: int) -> dict[str, Any]:
        get_session(session_id)
        return latest_attempt_result_index(session_id)

    @router.get("/api/session-tasks/{session_id}/files")
    async def session_task_files(session_id: int, path: str = "") -> dict[str, Any]:
        row = get_session(session_id)
        return list_directory_entries(row, path)

    @router.get("/api/session-tasks/{session_id}/files.zip")
    async def session_task_files_zip(session_id: int, path: str = "") -> StreamingResponse:
        row = get_session(session_id)
        payload = zip_directory_bytes(row, path)
        filename = f"task-{session_id}.zip" if not path else f"task-{session_id}-{Path(path).name or 'files'}.zip"
        return StreamingResponse(
            io.BytesIO(payload),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/session-tasks/{session_id}/raw/{file_path:path}")
    async def session_task_raw_file(request: Request, session_id: int, file_path: str) -> FileResponse:
        row = get_session(session_id)
        root = Path(str(row["workspace_dir"]))
        relative_path = clean_relative_path(file_path)
        file_row = ctx.session_repo.get_file(session_id, relative_path)
        resolved = ctx.session_file_service.resolve_download(root, relative_path, row=file_row)
        has_version = bool(str(request.query_params.get("v") or "").strip())
        cache_headers = {"Cache-Control": "private, max-age=86400"} if has_version and resolved.suffix.lower() in CACHEABLE_RAW_SUFFIXES else NO_STORE_HEADERS
        if query_flag(request.query_params.get("download")):
            return FileResponse(resolved, filename=resolved.name, headers=download_headers(cache_headers))
        return FileResponse(resolved, headers=cache_headers)

    @router.post("/api/session-tasks/{session_id}/raw-download-link/{file_path:path}")
    async def session_task_raw_download_link(request: Request, session_id: int, file_path: str) -> dict[str, Any]:
        row = get_session(session_id)
        root = Path(str(row["workspace_dir"]))
        relative_path = clean_relative_path(file_path)
        file_row = ctx.session_repo.get_file(session_id, relative_path)
        resolved = ctx.session_file_service.resolve_download(root, relative_path, row=file_row)
        token, expires_at = make_download_token(session_id, relative_path)
        encoded_path = encode_raw_file_path(relative_path)
        url = f"/api/session-download/{session_id}/{encoded_path}?token={urllib.parse.quote(token, safe='')}"
        return {"ok": True, "url": url, "filename": resolved.name, "expires_at": expires_at}

    @router.get("/api/session-download/{session_id}/{file_path:path}")
    async def session_task_signed_download(session_id: int, file_path: str, token: str = Query(default="")) -> FileResponse:
        row = get_session(session_id)
        root = Path(str(row["workspace_dir"]))
        relative_path = clean_relative_path(file_path)
        verify_download_token(token, session_id, relative_path)
        file_row = ctx.session_repo.get_file(session_id, relative_path)
        resolved = ctx.session_file_service.resolve_download(root, relative_path, row=file_row)
        return FileResponse(resolved, filename=resolved.name, headers=download_headers({"Cache-Control": "private, max-age=3600"}))

    @router.get("/api/session-tasks/{session_id}/thumbnail/{file_path:path}")
    def session_task_thumbnail_file(session_id: int, file_path: str) -> FileResponse:
        row = get_session(session_id)
        root = Path(str(row["workspace_dir"]))
        relative_path = clean_relative_path(file_path)
        file_row = ctx.session_repo.get_file(session_id, relative_path)
        resolved = ctx.session_file_service.resolve_download(root, relative_path, row=file_row)
        thumbnail = ensure_media_thumbnail(root, relative_path, resolved)
        return FileResponse(
            thumbnail,
            filename=f"{resolved.stem}.thumbnail.jpg",
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    @router.post("/api/session-tasks/{session_id}/upload")
    async def session_task_upload(session_id: int, file: UploadFile = File(...), path: str = Query(default="")) -> dict[str, Any]:
        target_path = f"{path.rstrip('/')}/{file.filename}" if path else file.filename or "upload.bin"
        return await store_session_upload(session_id, file, target_path)

    @router.get("/api/session-tasks/{session_id}/logs")
    async def session_task_logs(session_id: int) -> dict[str, Any]:
        get_session(session_id)
        return {"lines": log_lines(session_id)}

    @router.post("/api/sessions/{session_id}/files")
    async def session_file_upload(session_id: int, file: UploadFile = File(...), path: str = Form(default="")) -> dict[str, Any]:
        return await store_session_upload(session_id, file, path)

    @router.get("/api/sessions/{session_id}/files/{file_id}")
    async def session_file_download(session_id: int, file_id: str) -> FileResponse:
        row = get_session(session_id)
        relative_path = clean_relative_path(decode_file_id(file_id))
        file_row = ctx.session_repo.get_file(session_id, relative_path)
        file_path = ctx.session_file_service.resolve_download(Path(str(row["workspace_dir"])), relative_path, row=file_row)
        add_session_event(session_id, "file.downloaded", {"path": relative_path})
        return FileResponse(file_path, filename=file_path.name)

    @router.post("/api/sessions/{session_id}/share")
    async def session_share(session_id: int, request: Request, scope: str = Form(default="collaborator")) -> dict[str, Any]:
        share = create_or_refresh_share(session_id, scope)
        return {**share, "url": session_share_url(request, share["token"])}

    @router.post("/api/sessions/im/send")
    async def session_im_send(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        sender_name = str(body.get("sender_name") or "Simulator")
        group_id = str(body.get("group_id") or "default")
        message = str(body.get("message") or "").strip()
        active_session_id = body.get("active_session_id")
        if not message:
            raise HTTPException(status_code=400, detail="Message is required")
        lowered = message.lower()
        if lowered.startswith("/task"):
            prompt = message[5:].strip()
            if not prompt:
                session = create_real_session(request, group_id, sender_name, "")
                share = create_or_refresh_share(int(session["id"]), "collaborator")
                return {
                    "ok": True,
                    "kind": "task_created",
                    "reply": f"Task {session['title']} created. Task ID: {session['id']}\nDetail: {frontend_task_url(request, int(session['id']))}",
                    "session": {**session, "share_url": session_share_url(request, share["token"]), "task_url": frontend_task_url(request, int(session["id"]))},
                }
            referenced_session_id, trailing_message = parse_session_reference(prompt)
            if referenced_session_id is not None:
                session = (
                    continue_session(request, get_session(referenced_session_id), trailing_message)
                    if trailing_message
                    else detail_from_row(get_session(referenced_session_id), request)
                )
                return {
                    "ok": True,
                    "kind": "message_sent" if trailing_message else "session_selected",
                    "reply": (
                        f"Sent to task {session['title']}.\nDetail: {frontend_task_url(request, int(session['id']))}"
                        if trailing_message
                        else f"Task {session['title']} selected.\nDetail: {frontend_task_url(request, int(session['id']))}"
                    ),
                    "session": session,
                }
            session = create_real_session(request, group_id, sender_name, prompt)
            share = create_or_refresh_share(int(session["id"]), "collaborator")
            return {
                "ok": True,
                "kind": "task_created",
                "reply": f"Task {session['title']} created. Task ID: {session['id']}\nDetail: {frontend_task_url(request, int(session['id']))}",
                "session": {**session, "share_url": session_share_url(request, share["token"]), "task_url": frontend_task_url(request, int(session["id"]))},
            }
        if lowered == "/sessions":
            items = [detail_from_row(row, request) for row in ctx.session_repo.list_recent_group(group_id, 50)]
            return {"ok": True, "kind": "session_list", "reply": f"{len(items)} sessions", "items": items}
        if lowered.startswith("/session"):
            raw = message[8:].strip()
            referenced_session_id, trailing_message = parse_session_reference(raw)
            if referenced_session_id is None:
                raise HTTPException(status_code=400, detail="/Session requires a numeric session id")
            session = (
                continue_session(request, get_session(referenced_session_id), trailing_message)
                if trailing_message
                else detail_from_row(get_session(referenced_session_id), request)
            )
            return {
                "ok": True,
                "kind": "message_sent" if trailing_message else "session_selected",
                "reply": (
                    f"Sent to task {session['title']}.\nDetail: {frontend_task_url(request, int(session['id']))}"
                    if trailing_message
                    else f"Task {session['title']} selected.\nDetail: {frontend_task_url(request, int(session['id']))}"
                ),
                "session": session,
            }
        if not active_session_id:
            raise HTTPException(status_code=400, detail="No active session. Use empty /Task to create one, or /Task #7 to switch to an existing task.")
        session = continue_session(request, get_session(int(active_session_id)), message)
        return {"ok": True, "kind": "message_sent", "reply": f"Sent to task {session['title']}.\nDetail: {frontend_task_url(request, int(session['id']))}", "session": session}

    @router.get("/api/events/sessions/stream")
    async def session_stream(request: Request, since: int = Query(default=0)) -> StreamingResponse:
        async def event_generator() -> Any:
            cursor = since
            last_keepalive = time.monotonic()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    rows = ctx.session_repo.stream_events(cursor, 100)
                    if rows:
                        for row in rows:
                            cursor = int(row["id"])
                            if not event_visible(row, "customer"):
                                continue
                            yield f"data: {json.dumps(present_event(row), ensure_ascii=True)}\n\n"
                    elif time.monotonic() - last_keepalive >= 20:
                        last_keepalive = time.monotonic()
                        yield ": keepalive\n\n"
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                return

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.get("/api/session-share/{token}")
    async def session_share_data(token: str) -> dict[str, Any]:
        share = get_share(token)
        return {"session": detail_from_row(get_session(int(share["session_id"]))), "scope": share["scope"], "expires_at": share["expires_at"]}

    @router.get("/api/session-share/{token}/events")
    async def session_share_events(token: str, since: int = 0) -> dict[str, Any]:
        share = get_share(token)
        return {"items": list_session_events(int(share["session_id"]), since, "share")}

    @router.get("/api/session-share/{token}/files")
    async def session_share_files(token: str) -> dict[str, Any]:
        share = get_share(token)
        rows = sync_session_files(get_session(int(share["session_id"])))
        return {"items": ctx.session_file_service.visible_file_rows(rows, audience="share")}

    @router.post("/api/session-share/{token}/files")
    async def session_share_upload(token: str, file: UploadFile = File(...)) -> dict[str, Any]:
        share = get_share(token)
        if str(share.get("scope") or "viewer") != "collaborator":
            raise HTTPException(status_code=403, detail="This share is read-only")
        return await store_session_upload(int(share["session_id"]), file)

    @router.get("/api/session-share/{token}/files/{file_id}")
    async def session_share_download(token: str, file_id: str) -> FileResponse:
        share = get_share(token)
        session_id = int(share["session_id"])
        row = get_session(session_id)
        relative_path = clean_relative_path(decode_file_id(file_id))
        file_row = ctx.session_repo.get_file(session_id, relative_path)
        if not file_row:
            raise HTTPException(status_code=404, detail="File not found")
        file_path = ctx.session_file_service.resolve_download(Path(str(row["workspace_dir"])), relative_path, row=file_row, audience="share")
        return FileResponse(file_path, filename=file_path.name)

    @router.get("/session/share/{token}")
    async def session_share_page(token: str) -> HTMLResponse:
        share = get_share(token)
        session_row = get_session(int(share["session_id"]))
        page_title = html_lib.escape(str(session_row.get("title") or ""), quote=True)
        page_status = html_lib.escape(str(session_row.get("status") or ""), quote=True)
        page_group_id = html_lib.escape(str(session_row.get("group_id") or ""), quote=True)
        page_sender_name = html_lib.escape(str(session_row.get("sender_name") or "-"), quote=True)
        html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Session {page_title}</title>
  <style>
    :root {{ color-scheme: light; --bg:#f3f0e8; --card:#fffaf0; --line:#d8cfbf; --text:#2d261d; --muted:#73685a; --accent:#a3532d; }}
    body {{ margin:0; font-family: Georgia, 'Iowan Old Style', serif; background: radial-gradient(circle at top, #f8f4ec, var(--bg)); color:var(--text); }}
    .wrap {{ max-width:1100px; margin:0 auto; padding:24px; }}
    .hero, .grid > section {{ background:rgba(255,250,240,.94); border:1px solid var(--line); border-radius:20px; box-shadow:0 12px 32px rgba(70,45,20,.08); }}
    .hero {{ padding:20px 22px; margin-bottom:18px; }}
    .eyebrow {{ font-size:12px; text-transform:uppercase; letter-spacing:.14em; color:var(--accent); }}
    h1 {{ margin:8px 0 0; font-size:40px; }}
    .meta {{ display:flex; gap:16px; flex-wrap:wrap; color:var(--muted); margin-top:10px; }}
    .grid {{ display:grid; grid-template-columns:1.3fr .9fr; gap:18px; }}
    section {{ padding:18px; min-height:240px; }}
    .feed {{ display:flex; flex-direction:column; gap:10px; max-height:560px; overflow:auto; }}
    .item {{ border:1px solid var(--line); border-radius:14px; padding:12px; background:#fff; }}
    .kind {{ font-size:11px; text-transform:uppercase; letter-spacing:.1em; color:var(--accent); margin-bottom:6px; }}
    .time {{ font-size:12px; color:var(--muted); margin-top:6px; }}
    .files {{ display:flex; flex-direction:column; gap:10px; }}
    .file {{ display:flex; justify-content:space-between; gap:12px; align-items:center; border:1px solid var(--line); border-radius:14px; padding:10px 12px; background:#fff; }}
    button, input[type=file]::file-selector-button {{ font:inherit; }}
    button {{ border:1px solid #b77f57; background:#b85f35; color:#fff; border-radius:999px; padding:10px 14px; cursor:pointer; }}
    button.secondary {{ background:#fff; color:var(--text); }}
    .upload {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    @media (max-width: 860px) {{ .grid {{ grid-template-columns:1fr; }} h1 {{ font-size:30px; }} .wrap {{ padding:14px; }} }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"hero\">
      <div class=\"eyebrow\">OpenCrew Session</div>
      <h1>{page_title}</h1>
      <div class=\"meta\"><span id=\"status\">status: {page_status}</span><span>group: {page_group_id}</span><span>sender: {page_sender_name}</span></div>
    </div>
    <div class=\"grid\">
      <section>
        <h2>Timeline</h2>
        <div class=\"feed\" id=\"events\"></div>
      </section>
      <section>
        <h2>Files</h2>
        <div class=\"files\" id=\"files\"></div>
        <div class=\"upload\">
          <input id=\"upload\" type=\"file\" />
          <button id=\"upload-btn\">Upload</button>
        </div>
      </section>
    </div>
  </div>
  <script>
    const token = {json.dumps(token)};
    let lastEventId = 0;
    const apiBase = window.location.pathname.replace(/\\/session\\/share\\/[^/]+\\/?$/, '') + '/api/session-share/' + encodeURIComponent(token);
    async function j(path, init) {{ const res = await fetch(path, init); if (!res.ok) throw new Error(await res.text()); return res.json(); }}
    function fmt(ts) {{ return new Date(ts).toLocaleString(); }}
    function renderEvent(item) {{
      const card = document.createElement('div'); card.className='item';
      const kind = document.createElement('div'); kind.className='kind'; kind.textContent=item.kind; card.appendChild(kind);
      const body = document.createElement('div'); body.textContent=JSON.stringify(item.payload || {{}}, null, 2); card.appendChild(body);
      const time = document.createElement('div'); time.className='time'; time.textContent=fmt(item.created_at); card.appendChild(time);
      return card;
    }}
    function renderFile(item) {{
      const row = document.createElement('div'); row.className='file';
      const left = document.createElement('div'); left.textContent=item.path + ' (' + item.size + ' bytes)'; row.appendChild(left);
      const btn = document.createElement('button'); btn.className='secondary'; btn.textContent='Download'; btn.onclick=() => window.open(apiBase + '/files/' + encodeURIComponent(item.file_id), '_blank'); row.appendChild(btn);
      return row;
    }}
    async function refresh() {{
      const [sessionData, eventsData, filesData] = await Promise.all([
        j(apiBase),
        j(apiBase + '/events?since=' + lastEventId),
        j(apiBase + '/files'),
      ]);
      document.getElementById('status').textContent='status: ' + sessionData.session.status;
      const eventsEl = document.getElementById('events');
      if (lastEventId === 0) eventsEl.innerHTML='';
      for (const item of eventsData.items) {{ lastEventId = Math.max(lastEventId, item.id); eventsEl.prepend(renderEvent(item)); }}
      const filesEl = document.getElementById('files'); filesEl.innerHTML='';
      for (const item of filesData.items.filter((x) => x.downloadable)) filesEl.appendChild(renderFile(item));
    }}
    document.getElementById('upload-btn').onclick = async () => {{
      const input = document.getElementById('upload');
      if (!input.files.length) return;
      const form = new FormData(); form.append('file', input.files[0]);
      await fetch(apiBase + '/files', {{ method:'POST', body: form }});
      input.value=''; await refresh();
    }};
    refresh(); setInterval(refresh, 2000);
  </script>
</body>
</html>"""
        return HTMLResponse(html)

    return router
