from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..context import now_ms
from ..koubo.koubo_storyboard.constants import (
    ASSETS_REL,
    ASSET_VIDEOS_REL,
    SOURCE_REL,
    VIDEO_EXTS,
)
from ..workflow_modes import infer_openclip_workflow_mode
from .repository import MediaLibraryImportRepository
from .schemas import StoryBoardImportRequest


STREAM_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INVALID_TASK_STATUSES = {"archived", "deleting", "deleted", "cancelled"}
_LOCK_GUARD = threading.Lock()
_WORKSPACE_LOCKS: dict[str, threading.RLock] = {}


def _http_error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **extra},
    )


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        return str(exc.detail.get("code") or "storyboard_import_failed")
    return "storyboard_import_failed"


def _workspace_lock(workspace: Path) -> threading.RLock:
    key = str(workspace.resolve())
    with _LOCK_GUARD:
        return _WORKSPACE_LOCKS.setdefault(key, threading.RLock())


def _resolve_workspace(value: Any, *, code: str, message: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise _http_error(409, code, message)
    path = Path(raw)
    if not path.is_absolute() or not path.exists() or not path.is_dir():
        raise _http_error(409, code, message)
    return path.resolve()


def _resolve_relative(
    workspace: Path,
    relative_path: Any,
    *,
    require_file: bool,
    code: str,
    message: str,
) -> tuple[str, Path]:
    value = str(relative_path or "").strip().replace("\\", "/")
    relative = Path(value)
    if not value or value.startswith("/") or relative.is_absolute() or ".." in relative.parts:
        raise _http_error(409, code, message)
    resolved = (workspace / relative).resolve(strict=False)
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise _http_error(409, code, message) from exc
    if require_file and (not resolved.is_file()):
        raise _http_error(409, code, message)
    return relative.as_posix(), resolved


def _resolve_clip_relative(workspace: Path, clip_id: str, relative_path: Any) -> tuple[str, Path]:
    if not clip_id or len(clip_id) > 128 or "/" in clip_id or "\\" in clip_id or clip_id in {".", ".."}:
        raise _http_error(
            409,
            "media_clip_identity_mismatch",
            "派生片段身份无效。",
        )
    relative, resolved = _resolve_relative(
        workspace,
        relative_path,
        require_file=False,
        code="media_clip_path_invalid",
        message="派生片段登记路径无效。",
    )
    expected_prefix = f"SessionOutput/clips/{clip_id}/"
    if not relative.startswith(expected_prefix) or not relative.lower().endswith(".mp4"):
        raise _http_error(
            409,
            "media_clip_path_invalid",
            "派生片段不在其受控输出目录内。",
        )
    current = workspace
    for component in Path(relative).parts:
        current = current / component
        if current.is_symlink():
            raise _http_error(
                409,
                "media_clip_path_invalid",
                "派生片段受控路径不能包含符号链接。",
            )
    controlled = (workspace / "SessionOutput" / "clips" / clip_id).resolve(strict=False)
    try:
        resolved.relative_to(controlled)
    except ValueError as exc:
        raise _http_error(
            409,
            "media_clip_path_invalid",
            "派生片段不在其受控输出目录内。",
        ) from exc
    if not resolved.is_file():
        raise _http_error(
            409,
            "media_clip_file_missing",
            "派生片段登记文件不存在。",
        )
    return relative, resolved


def _strict_json_object(path: Path, *, allow_missing: bool) -> dict[str, Any]:
    if not path.exists():
        if allow_missing:
            return {}
        raise ValueError(f"{path.name} is missing")
    if not path.is_file():
        raise ValueError(f"{path.name} is not a regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _validate_storyboard_workspace(workspace: Path) -> None:
    for relative in (SOURCE_REL, ASSETS_REL):
        _rel, path = _resolve_relative(
            workspace,
            relative,
            require_file=False,
            code="storyboard_target_invalid",
            message="目标 StoryBoard 路径无效。",
        )
        if path.exists():
            try:
                payload = _strict_json_object(path, allow_missing=False)
            except Exception as exc:
                raise _http_error(
                    409,
                    "storyboard_target_invalid",
                    "目标 StoryBoard 文件无法安全解析。",
                ) from exc
            if relative == ASSETS_REL:
                assets = payload.get("assets", [])
                if not isinstance(assets, list) or any(not isinstance(item, dict) for item in assets):
                    raise _http_error(
                        409,
                        "storyboard_target_invalid",
                        "目标 StoryBoard 素材 manifest 无效。",
                    )


def _safe_requested_filename(requested_name: str | None, source: dict[str, Any], source_path: Path) -> str:
    requested = str(requested_name or "").strip()
    if any(char in requested for char in ("\x00", "/", "\\")):
        raise _http_error(
            422,
            "storyboard_import_name_invalid",
            "导入名称不能包含路径分隔符。",
        )
    source_suffix = source_path.suffix.lower()
    if source_suffix not in VIDEO_EXTS:
        raise _http_error(
            409,
            "media_source_invalid",
            "权威源文件不是支持的视频格式。",
        )
    raw_stem = Path(requested).stem if requested else str(source.get("display_name") or source_path.stem).strip()
    raw_stem = raw_stem or "video"
    safe_stem = re.sub(r"[\x00-\x1f/:*?\"<>|]", "_", raw_stem)
    safe_stem = safe_stem.strip(" .")[:180] or "video"
    return f"{safe_stem}{source_suffix}"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _manifest_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def _copy_and_hash(source: Path, target_part: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    target_part.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle, target_part.open("xb") as output_handle:
        while True:
            chunk = input_handle.read(STREAM_BYTES)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
            output_handle.write(chunk)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    return digest.hexdigest(), size


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(STREAM_BYTES)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


class MediaLibraryStoryBoardImportService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.repo = MediaLibraryImportRepository(ctx.engine)

    def has_active_import(self, asset_id: str) -> bool:
        return self.repo.has_preparing_for_asset(asset_id)

    def _source(self, asset_id: str) -> tuple[dict[str, Any], Path, str, str]:
        source = self.repo.get_source_asset(asset_id)
        if source is None:
            raise _http_error(404, "media_asset_not_found", "素材不存在或已删除。")
        if str(source.get("upload_status") or "") != "ready" or bool(source.get("archived")) or str(source.get("media_type") or "") != "video":
            raise _http_error(
                409,
                "media_source_not_eligible",
                "素材当前不可导入 StoryBoard。",
            )
        source_version = str(source.get("content_sha256") or "").lower()
        if not SHA256_RE.fullmatch(source_version):
            raise _http_error(
                409,
                "media_source_version_missing",
                "素材尚未建立可信内容版本。",
            )
        workspace = _resolve_workspace(
            source.get("source_workspace_dir"),
            code="media_source_missing",
            message="素材来源 workspace 不存在。",
        )
        relative, path = _resolve_relative(
            workspace,
            source.get("source_video_path"),
            require_file=True,
            code="media_source_missing",
            message="素材权威源文件不存在或路径无效。",
        )
        return source, path, relative, source_version

    def _clip_source(self, asset_id: str, clip_id: str) -> tuple[dict[str, Any], Path, str, str, str]:
        clip = self.repo.get_source_clip(clip_id)
        if clip is None:
            raise _http_error(404, "media_clip_not_found", "派生片段不存在或已删除。")
        if str(clip.get("source_asset_id") or "") != asset_id:
            raise _http_error(
                409,
                "media_clip_identity_mismatch",
                "派生片段不属于路径指定的素材。",
            )

        asset, _asset_path, _asset_rel, source_version = self._source(asset_id)
        if int(clip.get("source_session_id") or 0) != int(asset.get("session_id") or 0) or str(clip.get("source_version") or "").lower() != source_version:
            raise _http_error(
                409,
                "media_clip_source_version_mismatch",
                "派生片段对应的素材 Session 或内容版本已经失效。",
            )

        clip_hash = str(clip.get("content_sha256") or "").lower()
        if not SHA256_RE.fullmatch(clip_hash):
            raise _http_error(
                409,
                "media_clip_hash_invalid",
                "派生片段没有可信内容哈希。",
            )
        workspace = _resolve_workspace(
            asset.get("source_workspace_dir"),
            code="media_clip_file_missing",
            message="派生片段来源 workspace 不存在。",
        )
        relative, path = _resolve_clip_relative(workspace, clip_id, clip.get("output_path"))
        expected_size = int(clip.get("size_bytes") or 0)
        if expected_size <= 0 or path.stat().st_size != expected_size:
            raise _http_error(
                409,
                "media_clip_size_mismatch",
                "派生片段文件大小与权威登记不一致。",
            )
        registered = self.repo.get_registered_file(int(asset["session_id"]), relative)
        if (
            registered is None
            or str(registered.get("kind") or "") != "video"
            or bool(registered.get("stale"))
            or int(registered.get("size") or 0) != expected_size
        ):
            raise _http_error(
                409,
                "media_clip_file_unregistered",
                "派生片段未在来源 Session 中完成可信文件登记。",
            )
        source = {
            **asset,
            **clip,
            "asset_id": asset_id,
            "session_id": int(asset["session_id"]),
            "display_name": str(clip.get("display_name") or asset.get("display_name") or ""),
            "format": "mp4",
        }
        return source, path, relative, source_version, clip_hash

    def _target(self, task_id: int) -> tuple[dict[str, Any], Path]:
        target = self.repo.get_target_task(task_id)
        if target is None:
            raise _http_error(
                409,
                "storyboard_target_invalid",
                "目标 StoryBoard Task 不存在。",
            )
        if str(target.get("status") or "").lower() in INVALID_TASK_STATUSES or str(target.get("session_status") or "").lower() in INVALID_TASK_STATUSES:
            raise _http_error(
                409,
                "storyboard_target_invalid",
                "目标 StoryBoard Task 当前不可写。",
            )
        workspace = _resolve_workspace(
            target.get("workspace_dir"),
            code="storyboard_target_invalid",
            message="目标 StoryBoard workspace 不存在。",
        )
        _validate_storyboard_workspace(workspace)
        return target, workspace

    def list_targets(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for row in self.repo.list_target_tasks():
            try:
                _target, workspace = self._target(int(row["id"]))
                _validate_storyboard_workspace(workspace)
            except HTTPException:
                continue
            items.append(
                {
                    "task_id": int(row["id"]),
                    "session_id": int(row["session_id"]),
                    "title": str(row.get("title") or "未命名 StoryBoard"),
                    "workflow_mode": infer_openclip_workflow_mode(row, workspace=workspace),
                    "updated_at": int(row.get("updated_at") or 0),
                }
            )
        return {"items": items}

    @staticmethod
    def _same_request(
        row: dict[str, Any],
        *,
        asset_id: str,
        source_version: str,
        payload: StoryBoardImportRequest,
    ) -> bool:
        return (
            str(row.get("source_kind") or "") == "media_library_original"
            and str(row.get("source_asset_id") or "") == asset_id
            and not row.get("source_clip_id")
            and str(row.get("source_version") or "") == source_version
            and int(row.get("target_task_id") or 0) == payload.target_task_id
            and str(row.get("source_search_id") or "") == str(payload.search_id or "")
            and str(row.get("source_dialogue_asset_key") or "") == str(payload.dialogue_asset_key or "")
            and str(row.get("requested_name") or "") == str(payload.requested_name or "")
        )

    @staticmethod
    def _same_clip_request(
        row: dict[str, Any],
        *,
        asset_id: str,
        clip_id: str,
        source_version: str,
        payload: StoryBoardImportRequest,
    ) -> bool:
        return (
            str(row.get("source_kind") or "") == "media_library_clip"
            and str(row.get("source_asset_id") or "") == asset_id
            and str(row.get("source_clip_id") or "") == clip_id
            and str(row.get("source_version") or "") == source_version
            and int(row.get("target_task_id") or 0) == payload.target_task_id
            and str(row.get("source_search_id") or "") == str(payload.search_id or "")
            and str(row.get("source_dialogue_asset_key") or "") == str(payload.dialogue_asset_key or "")
            and str(row.get("requested_name") or "") == str(payload.requested_name or "")
        )

    def _search_candidate(
        self,
        search_id: str | None,
        asset_id: str,
        source_version: str,
        *,
        candidate_kind: str = "original_video",
        candidate_id: str | None = None,
        content_sha256: str | None = None,
        target_task_id: int | None = None,
        dialogue_asset_key: str | None = None,
    ) -> dict[str, Any] | None:
        if not search_id:
            return None
        candidate = self.repo.get_search_candidate(
            search_id,
            asset_id,
            candidate_kind=candidate_kind,
            candidate_id=candidate_id,
        )
        if candidate is None:
            raise _http_error(
                404,
                "search_run_not_found",
                "检索运行不存在，无法关联导入行为。",
            )
        run = candidate["run"]
        if str(run.get("status") or "") != "completed":
            raise _http_error(
                409,
                "search_run_not_completed",
                "检索运行尚未完成，不能关联导入。",
            )
        if (
            target_task_id is not None
            and int(run.get("target_task_id") or 0) != int(target_task_id)
        ):
            raise _http_error(
                409,
                "search_run_context_mismatch",
                "检索运行不属于当前目标 Task。",
            )
        if (
            dialogue_asset_key is not None
            and str(run.get("dialogue_asset_key") or "")
            != str(dialogue_asset_key)
        ):
            raise _http_error(
                409,
                "search_run_context_mismatch",
                "检索运行不属于当前 Dialogue。",
            )
        if not candidate.get("candidate_found"):
            raise _http_error(
                409,
                "search_candidate_not_found",
                "检索快照不包含该素材，不能关联导入。",
            )
        candidate_source_version = str(candidate.get("candidate_source_version") or "")
        if candidate_source_version and candidate_source_version != source_version:
            raise _http_error(
                409,
                "media_source_version_mismatch",
                "检索候选对应的素材版本已经失效。",
            )
        candidate_hash = str(
            candidate.get("candidate_content_sha256") or ""
        )
        if candidate_kind == "derived_clip":
            if not candidate_source_version or not candidate_hash:
                raise _http_error(
                    409,
                    "search_candidate_identity_incomplete",
                    "派生片段检索快照缺少完整内容身份。",
                )
            if candidate_hash != str(content_sha256 or ""):
                raise _http_error(
                    409,
                    "media_clip_hash_mismatch",
                    "检索候选对应的派生片段内容已经失效。",
                )
        return candidate

    @staticmethod
    def _manifest_asset(
        *,
        row: dict[str, Any],
        source: dict[str, Any],
        target_path: str,
        filename: str,
        imported_at: int,
        size_bytes: int,
    ) -> dict[str, Any]:
        source_kind = str(row.get("source_kind") or "media_library_original")
        provenance: dict[str, Any] = {
            "source": source_kind,
            "source_asset_id": str(source["asset_id"]),
            "source_session_id": int(source["session_id"]),
            "source_version": str(row["source_version"]),
            "source_search_id": row.get("source_search_id"),
            "source_dialogue_asset_key": row.get("source_dialogue_asset_key"),
            "content_sha256": str(row["content_sha256"]),
            "imported_at": imported_at,
        }
        if source_kind == "media_library_clip":
            provenance.update(
                {
                    "source_clip_id": str(row["source_clip_id"]),
                    "source_start_ms": int(source["source_start_ms"]),
                    "source_end_ms": int(source["source_end_ms"]),
                    "source_scheme": source.get("source_scheme"),
                    "source_fragment_id": source.get("source_fragment_id"),
                }
            )
        return {
            "id": str(row["target_manifest_asset_id"]),
            "path": target_path,
            "label": str(row.get("requested_name") or source.get("display_name") or filename),
            "filename": filename,
            "asset_type": "Video",
            "kind": "video",
            "source": source_kind,
            "created_at": imported_at,
            "content_sha256": str(row["content_sha256"]),
            "size_bytes": size_bytes,
            "duration_ms": source.get("duration_ms"),
            "width": source.get("width"),
            "height": source.get("height"),
            "format": source.get("format") or Path(filename).suffix.lstrip("."),
            "provenance": provenance,
            "origin": {"tool": "media_library_import", **provenance},
        }

    @staticmethod
    def _result(
        row: dict[str, Any],
        *,
        source: dict[str, Any],
        asset: dict[str, Any] | None,
        reused: bool,
        telemetry_written: bool | None = None,
    ) -> dict[str, Any]:
        source_kind = str(row.get("source_kind") or "media_library_original")
        item = asset or {
            "id": str(row.get("target_manifest_asset_id") or ""),
            "path": str(row.get("target_path") or ""),
            "label": str(row.get("requested_name") or source.get("display_name") or ""),
            "filename": Path(str(row.get("target_path") or "")).name,
            "asset_type": "Video",
            "kind": "video",
            "source": source_kind,
            "content_sha256": str(row.get("content_sha256") or ""),
        }
        result = {
            "ok": str(row.get("status") or "") == "completed",
            "import_id": str(row.get("import_id") or ""),
            "status": str(row.get("status") or ""),
            "reused": reused,
            "source_kind": source_kind,
            "source_asset_id": str(row.get("source_asset_id") or ""),
            "source_session_id": int(source.get("session_id") or 0),
            "source_version": str(row.get("source_version") or ""),
            "target_task_id": int(row.get("target_task_id") or 0),
            "target_session_id": int(row.get("target_session_id") or 0),
            "item": item,
        }
        if source_kind == "media_library_clip":
            result["source_clip_id"] = str(row.get("source_clip_id") or "")
        if telemetry_written is not None:
            result["search_action_recorded"] = telemetry_written
        return result

    @staticmethod
    def _manifest_asset_by_record(
        workspace: Path,
        row: dict[str, Any],
        *,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            payload = _strict_json_object(workspace / ASSETS_REL, allow_missing=True)
        except Exception:
            return None
        for item in payload.get("assets") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "") == str(row.get("target_manifest_asset_id") or "") and str(item.get("path") or "") == str(row.get("target_path") or ""):
                provenance = item.get("provenance")
                source_kind = str(row.get("source_kind") or "")
                if (
                    str(item.get("source") or "") != source_kind
                    or not isinstance(provenance, dict)
                    or str(provenance.get("source") or "") != source_kind
                    or str(provenance.get("source_asset_id") or "") != str(row.get("source_asset_id") or "")
                    or str(provenance.get("source_version") or "") != str(row.get("source_version") or "")
                    or str(provenance.get("content_sha256") or "") != str(row.get("content_sha256") or "")
                ):
                    return None
                if source_kind == "media_library_clip" and str(provenance.get("source_clip_id") or "") != str(row.get("source_clip_id") or ""):
                    return None
                if source is not None and (int(provenance.get("source_session_id") or 0) != int(source.get("session_id") or 0)):
                    return None
                if source_kind == "media_library_clip" and source is not None:
                    expected = {
                        "source_start_ms": source.get("source_start_ms"),
                        "source_end_ms": source.get("source_end_ms"),
                        "source_scheme": source.get("source_scheme"),
                        "source_fragment_id": source.get("source_fragment_id"),
                    }
                    if any(provenance.get(key) != value for key, value in expected.items()):
                        return None
                return item
        return None

    @staticmethod
    def _manifest_has_record_identity(workspace: Path, row: dict[str, Any]) -> bool:
        try:
            payload = _strict_json_object(workspace / ASSETS_REL, allow_missing=True)
        except Exception:
            return False
        return any(
            isinstance(item, dict)
            and str(item.get("id") or "") == str(row.get("target_manifest_asset_id") or "")
            and str(item.get("path") or "") == str(row.get("target_path") or "")
            for item in payload.get("assets") or []
        )

    def import_original(self, asset_id: str, payload: StoryBoardImportRequest) -> dict[str, Any]:
        source, source_path, _source_rel, source_version = self._source(asset_id)
        target, workspace = self._target(payload.target_task_id)
        search_candidate = self._search_candidate(
            payload.search_id,
            asset_id,
            source_version,
            target_task_id=payload.target_task_id,
            dialogue_asset_key=payload.dialogue_asset_key,
        )
        lock = _workspace_lock(workspace)
        with lock:
            existing = self.repo.get_import_by_key(payload.idempotency_key)
            if existing is not None:
                if not self._same_request(
                    existing,
                    asset_id=asset_id,
                    source_version=source_version,
                    payload=payload,
                ):
                    raise _http_error(
                        409,
                        "idempotency_key_conflict",
                        "该幂等键已用于不同的导入请求。",
                    )
                if str(existing.get("status") or "") == "completed":
                    asset = self._manifest_asset_by_record(workspace, existing, source=source)
                    return self._result(existing, source=source, asset=asset, reused=True)
                if str(existing.get("status") or "") == "failed":
                    raise _http_error(
                        409,
                        str(existing.get("error_code") or "storyboard_import_failed"),
                        "该幂等导入请求此前已失败，请使用新的幂等键重试。",
                        import_id=str(existing["import_id"]),
                    )
                recovered = self._reconcile_record(existing, source=source, target=target, workspace=workspace)
                if str(recovered.get("status") or "") == "completed":
                    return self._result(
                        recovered,
                        source=source,
                        asset=self._manifest_asset_by_record(workspace, recovered, source=source),
                        reused=True,
                    )
                raise _http_error(
                    409,
                    str(recovered.get("error_code") or "import_interrupted"),
                    "上一次同幂等键导入已中断，请使用新的幂等键重试。",
                    import_id=str(recovered["import_id"]),
                )

            created_at = now_ms()
            import_id = f"mli_{created_at}_{uuid.uuid4().hex[:12]}"
            safe_name = _safe_requested_filename(payload.requested_name, source, source_path)
            filename = f"{Path(safe_name).stem}_{import_id.rsplit('_', 1)[-1]}" f"{Path(safe_name).suffix.lower()}"
            target_rel = f"{ASSET_VIDEOS_REL}/{filename}"
            _rel, final_path = _resolve_relative(
                workspace,
                target_rel,
                require_file=False,
                code="storyboard_target_invalid",
                message="目标 StoryBoard 素材路径无效。",
            )
            if final_path.exists():
                raise _http_error(
                    409,
                    "storyboard_import_path_conflict",
                    "无法生成唯一的 StoryBoard 素材路径。",
                )
            record_values = {
                "import_id": import_id,
                "idempotency_key": payload.idempotency_key,
                "source_kind": "media_library_original",
                "source_asset_id": asset_id,
                "source_clip_id": None,
                "source_version": source_version,
                "source_search_id": payload.search_id,
                "source_dialogue_asset_key": payload.dialogue_asset_key,
                "target_task_id": payload.target_task_id,
                "target_session_id": int(target["session_id"]),
                "target_path": target_rel,
                "target_manifest_asset_id": f"storyboard_asset_{import_id}",
                "content_sha256": source_version,
                "size_bytes": 0,
                "requested_name": payload.requested_name,
                "status": "preparing",
                "error_code": None,
                "created_at": created_at,
                "updated_at": created_at,
            }
            row, claimed = self.repo.claim_import(record_values)
            if not claimed:
                if not self._same_request(
                    row,
                    asset_id=asset_id,
                    source_version=source_version,
                    payload=payload,
                ):
                    raise _http_error(
                        409,
                        "idempotency_key_conflict",
                        "该幂等键已用于不同的导入请求。",
                    )
                return self.import_original(asset_id, payload)

            part_path = final_path.with_name(f".{final_path.name}.{import_id}.part")
            manifest_path = workspace / ASSETS_REL
            manifest_tmp = manifest_path.with_name(f".{manifest_path.name}.{import_id}.tmp")
            manifest_backup = manifest_path.with_name(f".{manifest_path.name}.{import_id}.bak")
            final_created = False
            manifest_replaced = False
            old_manifest_exists = manifest_path.exists()
            old_manifest_bytes: bytes | None = None
            try:
                manifest_payload = _strict_json_object(manifest_path, allow_missing=True)
                assets = manifest_payload.get("assets", [])
                if not isinstance(assets, list) or any(not isinstance(item, dict) for item in assets):
                    raise _http_error(
                        409,
                        "storyboard_target_invalid",
                        "目标 StoryBoard 素材 manifest 无效。",
                    )
                if old_manifest_exists:
                    old_manifest_bytes = manifest_path.read_bytes()
                    _atomic_write(manifest_backup, old_manifest_bytes)

                copied_hash, copied_size = _copy_and_hash(source_path, part_path)
                if copied_hash != source_version:
                    raise _http_error(
                        409,
                        "media_source_version_mismatch",
                        "复制期间素材内容版本发生变化。",
                    )
                current_source, current_source_path, _current_rel, current_version = self._source(asset_id)
                if (
                    current_version != source_version
                    or current_source_path != source_path
                    or int(current_source.get("session_id") or 0) != int(source.get("session_id") or 0)
                ):
                    raise _http_error(
                        409,
                        "media_source_version_mismatch",
                        "发布前素材权威身份或内容版本发生变化。",
                    )
                current_target, current_workspace = self._target(payload.target_task_id)
                if int(current_target.get("session_id") or 0) != int(target.get("session_id") or 0) or current_workspace != workspace:
                    raise _http_error(
                        409,
                        "storyboard_target_invalid",
                        "发布前目标 StoryBoard 身份发生变化。",
                    )
                asset = self._manifest_asset(
                    row=row,
                    source=source,
                    target_path=target_rel,
                    filename=filename,
                    imported_at=created_at,
                    size_bytes=copied_size,
                )
                next_manifest = {
                    **manifest_payload,
                    "assets": [
                        item
                        for item in assets
                        if str(item.get("id") or "") != str(row["target_manifest_asset_id"]) and str(item.get("path") or "") != target_rel
                    ]
                    + [asset],
                    "updated_at": created_at,
                }
                _atomic_write(manifest_tmp, _manifest_bytes(next_manifest))
                final_path.parent.mkdir(parents=True, exist_ok=True)
                part_path.replace(final_path)
                final_created = True
                manifest_tmp.replace(manifest_path)
                manifest_replaced = True

                completed, telemetry_written = self.repo.finalize_completed(
                    import_id,
                    session_id=int(target["session_id"]),
                    target_path=target_rel,
                    size_bytes=copied_size,
                    source_asset_id=asset_id,
                    target_task_id=payload.target_task_id,
                    content_sha256=copied_hash,
                    updated_at=now_ms(),
                    search_candidate=search_candidate,
                )
                if not completed:
                    raise _http_error(
                        409,
                        "storyboard_import_state_conflict",
                        "导入状态已被其他请求更新。",
                    )
                manifest_backup.unlink(missing_ok=True)
                completed_row = self.repo.get_import(import_id) or {
                    **row,
                    "status": "completed",
                    "size_bytes": copied_size,
                }
                self._emit_completed(
                    source=source,
                    target=target,
                    row=completed_row,
                    telemetry_written=telemetry_written,
                )
                return self._result(
                    completed_row,
                    source=source,
                    asset=asset,
                    reused=False,
                    telemetry_written=telemetry_written,
                )
            except Exception as exc:
                try:
                    if manifest_replaced:
                        if old_manifest_exists and old_manifest_bytes is not None:
                            restore = manifest_path.with_name(f".{manifest_path.name}.{import_id}.restore")
                            _atomic_write(restore, old_manifest_bytes)
                            restore.replace(manifest_path)
                        elif not old_manifest_exists:
                            manifest_path.unlink(missing_ok=True)
                    if final_created:
                        final_path.unlink(missing_ok=True)
                finally:
                    part_path.unlink(missing_ok=True)
                    manifest_tmp.unlink(missing_ok=True)
                    manifest_backup.unlink(missing_ok=True)
                code = _error_code(exc)
                self.repo.mark_failed(import_id, error_code=code, updated_at=now_ms())
                self._emit_failed(source=source, target=target, row=row, error_code=code)
                if isinstance(exc, HTTPException):
                    raise
                raise _http_error(
                    500,
                    code,
                    "导入 StoryBoard 失败，已清理本次文件并恢复 manifest。",
                    import_id=import_id,
                ) from exc

    def import_clip(
        self,
        asset_id: str,
        clip_id: str,
        payload: StoryBoardImportRequest,
        *,
        search_candidate_kind: str | None = None,
    ) -> dict[str, Any]:
        source, source_path, source_rel, source_version, clip_hash = self._clip_source(asset_id, clip_id)
        if search_candidate_kind == "derived_clip" and not bool(
            source.get("search_eligible")
        ):
            raise _http_error(
                409,
                "media_clip_search_not_eligible",
                "派生片段已移出全局素材检索。",
            )
        target, workspace = self._target(payload.target_task_id)
        search_candidate = self._search_candidate(
            payload.search_id,
            asset_id,
            source_version,
            candidate_kind=search_candidate_kind or "original_video",
            candidate_id=(clip_id if search_candidate_kind else None),
            content_sha256=(clip_hash if search_candidate_kind else None),
            target_task_id=payload.target_task_id,
            dialogue_asset_key=payload.dialogue_asset_key,
        )
        lock = _workspace_lock(workspace)
        with lock:
            existing = self.repo.get_import_by_key(payload.idempotency_key)
            if existing is not None:
                if not self._same_clip_request(
                    existing,
                    asset_id=asset_id,
                    clip_id=clip_id,
                    source_version=source_version,
                    payload=payload,
                ):
                    raise _http_error(
                        409,
                        "idempotency_key_conflict",
                        "该幂等键已用于不同的导入请求。",
                    )
                if str(existing.get("status") or "") == "completed":
                    return self._result(
                        existing,
                        source=source,
                        asset=self._manifest_asset_by_record(workspace, existing, source=source),
                        reused=True,
                    )
                if str(existing.get("status") or "") == "failed":
                    raise _http_error(
                        409,
                        str(existing.get("error_code") or "storyboard_import_failed"),
                        "该幂等导入请求此前已失败，请使用新的幂等键重试。",
                        import_id=str(existing["import_id"]),
                    )
                recovered = self._reconcile_record(
                    existing,
                    source=source,
                    target=target,
                    workspace=workspace,
                )
                if str(recovered.get("status") or "") == "completed":
                    return self._result(
                        recovered,
                        source=source,
                        asset=self._manifest_asset_by_record(workspace, recovered, source=source),
                        reused=True,
                    )
                raise _http_error(
                    409,
                    str(recovered.get("error_code") or "import_interrupted"),
                    "上一次同幂等键导入已中断，请使用新的幂等键重试。",
                    import_id=str(recovered["import_id"]),
                )

            created_at = now_ms()
            import_id = f"mli_{created_at}_{uuid.uuid4().hex[:12]}"
            safe_name = _safe_requested_filename(payload.requested_name, source, source_path)
            filename = f"{Path(safe_name).stem}_{import_id.rsplit('_', 1)[-1]}" f"{Path(safe_name).suffix.lower()}"
            target_rel = f"{ASSET_VIDEOS_REL}/{filename}"
            _rel, final_path = _resolve_relative(
                workspace,
                target_rel,
                require_file=False,
                code="storyboard_target_invalid",
                message="目标 StoryBoard 素材路径无效。",
            )
            if final_path.exists():
                raise _http_error(
                    409,
                    "storyboard_import_path_conflict",
                    "无法生成唯一的 StoryBoard 素材路径。",
                )
            record_values = {
                "import_id": import_id,
                "idempotency_key": payload.idempotency_key,
                "source_kind": "media_library_clip",
                "source_asset_id": asset_id,
                "source_clip_id": clip_id,
                "source_version": source_version,
                "source_search_id": payload.search_id,
                "source_dialogue_asset_key": payload.dialogue_asset_key,
                "target_task_id": payload.target_task_id,
                "target_session_id": int(target["session_id"]),
                "target_path": target_rel,
                "target_manifest_asset_id": f"storyboard_asset_{import_id}",
                "content_sha256": clip_hash,
                "size_bytes": 0,
                "requested_name": payload.requested_name,
                "status": "preparing",
                "error_code": None,
                "created_at": created_at,
                "updated_at": created_at,
            }
            row, claimed = self.repo.claim_import(record_values)
            if not claimed:
                if not self._same_clip_request(
                    row,
                    asset_id=asset_id,
                    clip_id=clip_id,
                    source_version=source_version,
                    payload=payload,
                ):
                    raise _http_error(
                        409,
                        "idempotency_key_conflict",
                        "该幂等键已用于不同的导入请求。",
                    )
                return self.import_clip(
                    asset_id,
                    clip_id,
                    payload,
                    search_candidate_kind=search_candidate_kind,
                )

            part_path = final_path.with_name(f".{final_path.name}.{import_id}.part")
            manifest_path = workspace / ASSETS_REL
            manifest_tmp = manifest_path.with_name(f".{manifest_path.name}.{import_id}.tmp")
            manifest_backup = manifest_path.with_name(f".{manifest_path.name}.{import_id}.bak")
            final_created = False
            manifest_replaced = False
            old_manifest_exists = manifest_path.exists()
            old_manifest_bytes: bytes | None = None
            try:
                manifest_payload = _strict_json_object(manifest_path, allow_missing=True)
                assets = manifest_payload.get("assets", [])
                if not isinstance(assets, list) or any(not isinstance(item, dict) for item in assets):
                    raise _http_error(
                        409,
                        "storyboard_target_invalid",
                        "目标 StoryBoard 素材 manifest 无效。",
                    )
                if old_manifest_exists:
                    old_manifest_bytes = manifest_path.read_bytes()
                    _atomic_write(manifest_backup, old_manifest_bytes)

                copied_hash, copied_size = _copy_and_hash(source_path, part_path)
                if copied_hash != clip_hash:
                    raise _http_error(
                        409,
                        "media_clip_hash_mismatch",
                        "复制期间派生片段内容与权威哈希不一致。",
                    )
                (
                    current_source,
                    current_source_path,
                    current_source_rel,
                    current_source_version,
                    current_clip_hash,
                ) = self._clip_source(asset_id, clip_id)
                if search_candidate_kind == "derived_clip" and not bool(
                    current_source.get("search_eligible")
                ):
                    raise _http_error(
                        409,
                        "media_clip_search_not_eligible",
                        "派生片段已移出全局素材检索。",
                    )
                identity_fields = (
                    "source_start_ms",
                    "source_end_ms",
                    "source_scheme",
                    "source_fragment_id",
                )
                if (
                    current_source_version != source_version
                    or current_clip_hash != clip_hash
                    or current_source_path != source_path
                    or current_source_rel != source_rel
                    or int(current_source.get("source_session_id") or 0) != int(source.get("source_session_id") or 0)
                    or any(current_source.get(field) != source.get(field) for field in identity_fields)
                ):
                    raise _http_error(
                        409,
                        "media_clip_source_version_mismatch",
                        "发布前派生片段权威身份、范围或内容版本发生变化。",
                    )
                current_target, current_workspace = self._target(payload.target_task_id)
                if int(current_target.get("session_id") or 0) != int(target.get("session_id") or 0) or current_workspace != workspace:
                    raise _http_error(
                        409,
                        "storyboard_target_invalid",
                        "发布前目标 StoryBoard 身份发生变化。",
                    )
                asset = self._manifest_asset(
                    row=row,
                    source=source,
                    target_path=target_rel,
                    filename=filename,
                    imported_at=created_at,
                    size_bytes=copied_size,
                )
                next_manifest = {
                    **manifest_payload,
                    "assets": [
                        item
                        for item in assets
                        if str(item.get("id") or "") != str(row["target_manifest_asset_id"]) and str(item.get("path") or "") != target_rel
                    ]
                    + [asset],
                    "updated_at": created_at,
                }
                _atomic_write(manifest_tmp, _manifest_bytes(next_manifest))
                final_path.parent.mkdir(parents=True, exist_ok=True)
                part_path.replace(final_path)
                final_created = True
                manifest_tmp.replace(manifest_path)
                manifest_replaced = True

                completed, telemetry_written = self.repo.finalize_completed(
                    import_id,
                    session_id=int(target["session_id"]),
                    target_path=target_rel,
                    size_bytes=copied_size,
                    source_asset_id=asset_id,
                    target_task_id=payload.target_task_id,
                    content_sha256=copied_hash,
                    updated_at=now_ms(),
                    search_candidate=search_candidate,
                    source_kind="media_library_clip",
                    source_clip_id=clip_id,
                )
                if not completed:
                    raise _http_error(
                        409,
                        "storyboard_import_state_conflict",
                        "导入状态已被其他请求更新。",
                    )
                manifest_backup.unlink(missing_ok=True)
                completed_row = self.repo.get_import(import_id) or {
                    **row,
                    "status": "completed",
                    "size_bytes": copied_size,
                }
                self._emit_completed(
                    source=source,
                    target=target,
                    row=completed_row,
                    telemetry_written=telemetry_written,
                )
                return self._result(
                    completed_row,
                    source=source,
                    asset=asset,
                    reused=False,
                    telemetry_written=telemetry_written,
                )
            except Exception as exc:
                try:
                    if manifest_replaced:
                        if old_manifest_exists and old_manifest_bytes is not None:
                            restore = manifest_path.with_name(f".{manifest_path.name}.{import_id}.restore")
                            _atomic_write(restore, old_manifest_bytes)
                            restore.replace(manifest_path)
                        elif not old_manifest_exists:
                            manifest_path.unlink(missing_ok=True)
                    if final_created:
                        final_path.unlink(missing_ok=True)
                finally:
                    part_path.unlink(missing_ok=True)
                    manifest_tmp.unlink(missing_ok=True)
                    manifest_backup.unlink(missing_ok=True)
                code = _error_code(exc)
                self.repo.mark_failed(import_id, error_code=code, updated_at=now_ms())
                self._emit_failed(source=source, target=target, row=row, error_code=code)
                if isinstance(exc, HTTPException):
                    raise
                raise _http_error(
                    500,
                    code,
                    "导入 StoryBoard 失败，已清理本次文件并恢复 manifest。",
                    import_id=import_id,
                ) from exc

    def _emit_completed(
        self,
        *,
        source: dict[str, Any],
        target: dict[str, Any],
        row: dict[str, Any],
        telemetry_written: bool,
    ) -> None:
        payload = {
            "import_id": str(row["import_id"]),
            "source_kind": str(row["source_kind"]),
            "source_asset_id": str(source["asset_id"]),
            "source_session_id": int(source["session_id"]),
            "source_version": str(row["source_version"]),
            "source_search_id": row.get("source_search_id"),
            "target_task_id": int(target["id"]),
            "target_session_id": int(target["session_id"]),
            "target_path": str(row["target_path"]),
            "content_sha256": str(row["content_sha256"]),
            "search_action_recorded": telemetry_written,
        }
        if row.get("source_clip_id"):
            payload["source_clip_id"] = str(row["source_clip_id"])
        self._safe_event(
            int(source["session_id"]),
            "media_library.storyboard_import.completed",
            payload,
        )
        self._safe_event(
            int(target["session_id"]),
            "media_library.storyboard_import.completed",
            payload,
        )

    def _emit_failed(
        self,
        *,
        source: dict[str, Any],
        target: dict[str, Any],
        row: dict[str, Any],
        error_code: str,
    ) -> None:
        payload = {
            "import_id": str(row["import_id"]),
            "source_kind": str(row["source_kind"]),
            "source_asset_id": str(source["asset_id"]),
            "source_session_id": int(source["session_id"]),
            "target_task_id": int(target["id"]),
            "target_session_id": int(target["session_id"]),
            "error_code": error_code,
        }
        if row.get("source_clip_id"):
            payload["source_clip_id"] = str(row["source_clip_id"])
        self._safe_event(
            int(source["session_id"]),
            "media_library.storyboard_import.failed",
            payload,
        )
        self._safe_event(
            int(target["session_id"]),
            "media_library.storyboard_import.failed",
            payload,
        )

    def _safe_event(self, session_id: int, kind: str, payload: dict[str, Any]) -> None:
        try:
            self.ctx.session_event_service.add_event(session_id, kind, payload, workflow_id="media_library")
        except Exception as exc:
            reporter = getattr(self.ctx, "event", None)
            if callable(reporter):
                try:
                    reporter(
                        "error",
                        "media_library",
                        "StoryBoard import event write failed",
                        {
                            "kind": kind,
                            "session_id": session_id,
                            "error": str(exc)[:500],
                        },
                    )
                except Exception:
                    pass

    def _reconcile_record(
        self,
        row: dict[str, Any],
        *,
        source: dict[str, Any],
        target: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        import_id = str(row["import_id"])
        _rel, final_path = _resolve_relative(
            workspace,
            row.get("target_path"),
            require_file=False,
            code="storyboard_target_invalid",
            message="导入记录的目标路径无效。",
        )
        part_path = final_path.with_name(f".{final_path.name}.{import_id}.part")
        manifest_path = workspace / ASSETS_REL
        manifest_tmp = manifest_path.with_name(f".{manifest_path.name}.{import_id}.tmp")
        manifest_backup = manifest_path.with_name(f".{manifest_path.name}.{import_id}.bak")
        asset = self._manifest_asset_by_record(workspace, row, source=source)
        hash_matches = False
        size = 0
        if final_path.is_file():
            final_hash, size = _hash_file(final_path)
            hash_matches = final_hash == str(row["content_sha256"])
        if hash_matches and asset is not None:
            candidate = self._search_candidate(
                row.get("source_search_id"),
                str(row["source_asset_id"]),
                str(row["source_version"]),
                candidate_kind=(
                    "derived_clip"
                    if str(row.get("source_kind") or "")
                    == "media_library_clip"
                    and row.get("source_search_id")
                    else "original_video"
                ),
                candidate_id=(
                    str(row["source_clip_id"])
                    if str(row.get("source_kind") or "")
                    == "media_library_clip"
                    and row.get("source_search_id")
                    else None
                ),
                content_sha256=(
                    str(row["content_sha256"])
                    if str(row.get("source_kind") or "")
                    == "media_library_clip"
                    else None
                ),
                target_task_id=int(row["target_task_id"]),
                dialogue_asset_key=(
                    str(row["source_dialogue_asset_key"])
                    if row.get("source_dialogue_asset_key")
                    else None
                ),
            )
            completed, telemetry_written = self.repo.finalize_completed(
                import_id,
                session_id=int(row["target_session_id"]),
                target_path=str(row["target_path"]),
                size_bytes=size,
                source_asset_id=str(row["source_asset_id"]),
                target_task_id=int(row["target_task_id"]),
                content_sha256=str(row["content_sha256"]),
                updated_at=now_ms(),
                search_candidate=candidate,
                source_kind=str(row["source_kind"]),
                source_clip_id=(str(row["source_clip_id"]) if row.get("source_clip_id") else None),
            )
            manifest_backup.unlink(missing_ok=True)
            if completed:
                current = self.repo.get_import(import_id) or {
                    **row,
                    "status": "completed",
                    "size_bytes": size,
                }
                self._emit_completed(
                    source=source,
                    target=target,
                    row=current,
                    telemetry_written=telemetry_written,
                )
                return current

        if manifest_backup.is_file():
            restore = manifest_path.with_name(f".{manifest_path.name}.{import_id}.restore")
            _atomic_write(restore, manifest_backup.read_bytes())
            restore.replace(manifest_path)
        elif self._manifest_has_record_identity(workspace, row):
            payload = _strict_json_object(manifest_path, allow_missing=True)
            assets = [
                item
                for item in payload.get("assets") or []
                if not (
                    isinstance(item, dict)
                    and str(item.get("id") or "") == str(row["target_manifest_asset_id"])
                    and str(item.get("path") or "") == str(row["target_path"])
                )
            ]
            cleanup_tmp = manifest_path.with_name(f".{manifest_path.name}.{import_id}.cleanup")
            _atomic_write(
                cleanup_tmp,
                _manifest_bytes({**payload, "assets": assets, "updated_at": now_ms()}),
            )
            cleanup_tmp.replace(manifest_path)
        final_path.unlink(missing_ok=True)
        part_path.unlink(missing_ok=True)
        manifest_tmp.unlink(missing_ok=True)
        manifest_backup.unlink(missing_ok=True)
        self.repo.mark_failed(import_id, error_code="import_interrupted", updated_at=now_ms())
        current = self.repo.get_import(import_id) or {
            **row,
            "status": "failed",
            "error_code": "import_interrupted",
        }
        self._emit_failed(
            source=source,
            target=target,
            row=current,
            error_code="import_interrupted",
        )
        return current

    def _cleanup_interrupted_without_target(self, row: dict[str, Any]) -> None:
        session = self.repo.get_session(int(row.get("target_session_id") or 0))
        if session is None:
            return
        try:
            workspace = _resolve_workspace(
                session.get("workspace_dir"),
                code="storyboard_target_invalid",
                message="目标 StoryBoard workspace 不存在。",
            )
            with _workspace_lock(workspace):
                _rel, final_path = _resolve_relative(
                    workspace,
                    row.get("target_path"),
                    require_file=False,
                    code="storyboard_target_invalid",
                    message="导入记录的目标路径无效。",
                )
                if not str(row.get("target_path") or "").startswith(f"{ASSET_VIDEOS_REL}/"):
                    return
                import_id = str(row["import_id"])
                manifest_path = workspace / ASSETS_REL
                backup = manifest_path.with_name(f".{manifest_path.name}.{import_id}.bak")
                if backup.is_file():
                    restore = manifest_path.with_name(f".{manifest_path.name}.{import_id}.restore")
                    _atomic_write(restore, backup.read_bytes())
                    restore.replace(manifest_path)
                else:
                    if self._manifest_has_record_identity(workspace, row):
                        payload = _strict_json_object(manifest_path, allow_missing=True)
                        assets = [
                            item
                            for item in payload.get("assets") or []
                            if not (
                                isinstance(item, dict)
                                and str(item.get("id") or "") == str(row["target_manifest_asset_id"])
                                and str(item.get("path") or "") == str(row["target_path"])
                            )
                        ]
                        cleanup = manifest_path.with_name(f".{manifest_path.name}.{import_id}.cleanup")
                        _atomic_write(
                            cleanup,
                            _manifest_bytes(
                                {
                                    **payload,
                                    "assets": assets,
                                    "updated_at": now_ms(),
                                }
                            ),
                        )
                        cleanup.replace(manifest_path)
                final_path.unlink(missing_ok=True)
                final_path.with_name(f".{final_path.name}.{import_id}.part").unlink(missing_ok=True)
                manifest_path.with_name(f".{manifest_path.name}.{import_id}.tmp").unlink(missing_ok=True)
                backup.unlink(missing_ok=True)
        except Exception:
            # The record still becomes failed. Cleanup is deliberately bounded
            # to a resolvable target Session workspace and controlled videos
            # prefix; unsafe or missing paths are never guessed.
            return

    def reconcile_preparing(self) -> dict[str, int]:
        completed = 0
        failed = 0
        for row in self.repo.list_preparing():
            try:
                if str(row.get("source_kind") or "") == "media_library_clip":
                    source, _path, _rel, _version, _clip_hash = self._clip_source(
                        str(row["source_asset_id"]),
                        str(row["source_clip_id"]),
                    )
                else:
                    source, _path, _rel, _version = self._source(str(row["source_asset_id"]))
                target, workspace = self._target(int(row["target_task_id"]))
                with _workspace_lock(workspace):
                    current = self._reconcile_record(
                        row,
                        source=source,
                        target=target,
                        workspace=workspace,
                    )
                if str(current.get("status") or "") == "completed":
                    completed += 1
                else:
                    failed += 1
            except Exception:
                try:
                    self._cleanup_interrupted_without_target(row)
                    self.repo.mark_failed(
                        str(row["import_id"]),
                        error_code="import_interrupted",
                        updated_at=now_ms(),
                    )
                finally:
                    failed += 1
        return {"completed": completed, "failed": failed}
