from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import MediaClipError


CLIP_ID_RE = re.compile(r"^mlc_[0-9]{13}_[0-9a-f]{12}$")
CLIP_JOB_ID_RE = re.compile(
    r"^clipjob\.([0-9a-f]{32})\.([0-9a-f]{32})$"
)
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OPERATION_PRECISE_REENCODE = "precise_reencode_v1"
JOB_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def new_clip_id(timestamp_ms: int) -> str:
    return f"mlc_{int(timestamp_ms):013d}_{uuid.uuid4().hex[:12]}"


def new_boot_id() -> str:
    return uuid.uuid4().hex


def new_clip_job_id(boot_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", str(boot_id)):
        raise ValueError("clip_job_boot_id_invalid")
    return f"clipjob.{boot_id}.{uuid.uuid4().hex}"


def clean_display_name(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = "".join(
        " " if char in {"/", "\\"} or unicodedata.category(char).startswith("C")
        else char
        for char in normalized
    )
    normalized = " ".join(normalized.split()).strip(" .")
    if normalized.lower().endswith(".mp4"):
        normalized = normalized[:-4].strip(" .")
    normalized = normalized[:120].strip(" .")
    if not normalized:
        raise MediaClipError(
            "media_clip_display_name_required",
            "请输入有效的剪辑名称。",
            status_code=422,
        )
    return normalized


def clean_search_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise MediaClipError(
            "media_clip_tag_invalid",
            "片段标签格式无效。",
            status_code=422,
        )
    if len(value) > 10:
        raise MediaClipError(
            "media_clip_tags_too_many",
            "片段标签最多可填写 10 项。",
            status_code=422,
        )
    cleaned_tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in value:
        if not isinstance(raw_tag, str):
            raise MediaClipError(
                "media_clip_tag_invalid",
                "片段标签必须是文字。",
                status_code=422,
            )
        normalized = unicodedata.normalize("NFKC", raw_tag)
        normalized = "".join(
            " "
            if char in {"/", "\\"}
            or unicodedata.category(char).startswith("C")
            else char
            for char in normalized
        )
        cleaned = " ".join(normalized.split()).strip(" .")
        if not cleaned or len(cleaned) > 32:
            raise MediaClipError(
                "media_clip_tag_invalid",
                "每个片段标签清理后必须为 1 到 32 个字符。",
                status_code=422,
            )
        identity = cleaned.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        cleaned_tags.append(cleaned)
    return cleaned_tags


def validate_idempotency_key(value: Any) -> str:
    key = str(value or "").strip()
    if not IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise MediaClipError(
            "idempotency_key_invalid",
            "幂等键格式无效。",
            status_code=422,
        )
    return key


@dataclass(frozen=True)
class ClipRequest:
    idempotency_key: str
    source_asset_id: str
    source_session_id: int
    source_version: str
    source_start_ms: int
    source_end_ms: int
    source_duration_ms: int
    source_workspace: Path
    source_video_path: str
    display_name: str
    source_scheme: str | None = None
    source_fragment_id: str | None = None
    source_analysis_run_id: str | None = None
    source_search_id: str | None = None
    source_dialogue_asset_key: str | None = None
    manual_override: bool = False
    operation: str = OPERATION_PRECISE_REENCODE

    @property
    def requested_duration_ms(self) -> int:
        return self.source_end_ms - self.source_start_ms

    def identity_payload(self) -> dict[str, Any]:
        return {
            "source_asset_id": self.source_asset_id,
            "source_session_id": self.source_session_id,
            "source_version": self.source_version,
            "source_start_ms": self.source_start_ms,
            "source_end_ms": self.source_end_ms,
            "display_name": self.display_name,
            "source_scheme": self.source_scheme,
            "source_fragment_id": self.source_fragment_id,
            "source_analysis_run_id": self.source_analysis_run_id,
            "source_search_id": self.source_search_id,
            "source_dialogue_asset_key": self.source_dialogue_asset_key,
            "manual_override": self.manual_override,
            "operation": self.operation,
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.identity_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def derivative_identity(self) -> dict[str, Any]:
        payload = self.identity_payload()
        payload.pop("manual_override", None)
        return payload


@dataclass(frozen=True)
class ClipProbe:
    actual_duration_ms: int
    duration_tolerance_ms: int
    video_frame_budget_ms: int
    audio_frame_budget_ms: int
    has_audio: bool
    video_codec: str
    audio_codec: str | None
    avg_frame_rate: str
    sample_rate: int | None


@dataclass
class ClipJob:
    clip_job_id: str
    boot_id: str
    asset_id: str
    idempotency_key: str
    request_fingerprint: str
    request: ClipRequest
    status: str = "queued"
    progress: int = 0
    clip_id: str | None = None
    clip: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: int = 0
    updated_at: int = 0
    cancel_requested: bool = False
    process: Any | None = field(default=None, repr=False)
    part_path: Path | None = field(default=None, repr=False)

    def view(self) -> dict[str, Any]:
        return {
            "clip_job_id": self.clip_job_id,
            "status": self.status,
            "progress": self.progress,
            "clip_id": self.clip_id,
            "error": self.error,
            "clip": dict(self.clip) if self.clip is not None else None,
        }


def optional_text(value: Any, *, maximum: int = 256) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:maximum]


def clip_request_from_asset(
    *,
    asset: Mapping[str, Any],
    session: Mapping[str, Any],
    payload: Mapping[str, Any],
    minimum_duration_ms: int,
    maximum_duration_ms: int,
) -> ClipRequest:
    asset_id = str(asset.get("asset_id") or "").strip()
    session_id = int(asset.get("session_id") or 0)
    session_row_id = int(session.get("id") or 0)
    workspace = Path(str(session.get("workspace_dir") or "")).expanduser()
    source_path = str(asset.get("source_video_path") or "").strip()
    source_version = str(asset.get("content_sha256") or "").strip()
    source_duration_ms = int(asset.get("duration_ms") or 0)
    if (
        not asset_id
        or session_id <= 0
        or session_id != session_row_id
        or not workspace.is_absolute()
        or not source_path
        or not SHA256_RE.fullmatch(source_version)
        or source_duration_ms <= 0
        or str(asset.get("upload_status") or "") != "ready"
        or bool(asset.get("archived"))
    ):
        raise MediaClipError(
            "media_clip_source_not_ready",
            "原始素材尚未满足剪辑条件。",
            status_code=409,
        )
    requested_version = str(payload.get("source_version") or "").strip()
    if requested_version != source_version:
        raise MediaClipError(
            "media_source_version_mismatch",
            "素材源版本已变化，请刷新后重新选择范围。",
            status_code=409,
        )
    try:
        start_ms = int(payload.get("start_ms"))
        end_ms = int(payload.get("end_ms"))
    except (TypeError, ValueError) as exc:
        raise MediaClipError(
            "clip_range_invalid",
            "剪辑时间范围无效。",
            status_code=422,
        ) from exc
    duration_ms = end_ms - start_ms
    if (
        start_ms < 0
        or end_ms <= start_ms
        or end_ms > source_duration_ms
        or duration_ms < minimum_duration_ms
        or duration_ms > maximum_duration_ms
    ):
        raise MediaClipError(
            "clip_range_invalid",
            "剪辑范围超出素材时长或允许的时长限制。",
            status_code=422,
            details={
                "minimum_duration_ms": minimum_duration_ms,
                "maximum_duration_ms": maximum_duration_ms,
            },
        )
    manual_override = bool(payload.get("manual_override"))
    source_fragment_id = optional_text(payload.get("source_fragment_id"))
    source_analysis_run_id = optional_text(
        payload.get("source_analysis_run_id")
    )
    if manual_override and (
        source_fragment_id is not None or source_analysis_run_id is not None
    ):
        raise MediaClipError(
            "media_clip_manual_provenance_invalid",
            "手动选区不能继续引用旧分析片段。",
            status_code=422,
        )
    if not manual_override and bool(source_fragment_id) != bool(
        source_analysis_run_id
    ):
        raise MediaClipError(
            "media_clip_fragment_provenance_incomplete",
            "分析建议选区缺少完整的片段版本引用。",
            status_code=422,
        )
    return ClipRequest(
        idempotency_key=validate_idempotency_key(
            payload.get("idempotency_key")
        ),
        source_asset_id=asset_id,
        source_session_id=session_id,
        source_version=source_version,
        source_start_ms=start_ms,
        source_end_ms=end_ms,
        source_duration_ms=source_duration_ms,
        source_workspace=workspace.resolve(),
        source_video_path=source_path,
        display_name=clean_display_name(payload.get("display_name")),
        source_scheme=optional_text(payload.get("source_scheme")),
        source_fragment_id=source_fragment_id,
        source_analysis_run_id=source_analysis_run_id,
        source_search_id=optional_text(payload.get("source_search_id")),
        source_dialogue_asset_key=optional_text(
            payload.get("source_dialogue_asset_key")
        ),
        manual_override=manual_override,
    )


__all__ = [
    "CLIP_ID_RE",
    "CLIP_JOB_ID_RE",
    "IDEMPOTENCY_KEY_RE",
    "JOB_TERMINAL_STATUSES",
    "OPERATION_PRECISE_REENCODE",
    "ClipJob",
    "ClipProbe",
    "ClipRequest",
    "clean_display_name",
    "clip_request_from_asset",
    "new_boot_id",
    "new_clip_id",
    "new_clip_job_id",
    "validate_idempotency_key",
]
