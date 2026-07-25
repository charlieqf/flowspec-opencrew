from __future__ import annotations

import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from opcrew_backend.adapters.opencode import OpenCodeAuthError, OpenCodeSessionClient
from opcrew_backend.context import AppContext, now_ms
from opcrew_backend.routes.media_model_config import load_config, load_stored_key
from opcrew_backend.routes.sessions import MAX_UPLOAD_BYTES, UPLOAD_CHUNK_BYTES
from opcrew_backend.services.opencode_runtime import discover_and_save_opencode_runtime, opencode_client_for_context
from opcrew_backend.services.tts_voice_aliases import PUBLIC_TTS_VOICE_PREFIX, resolve_tts_voice_alias
from opcrew_backend.workflow_modes import WORKFLOW_DANCE_MIMIC_V1, infer_openclip_workflow_mode

from .koubo_storyboard.constants import (
    ASSET_AUDIOS_REL,
    ASSET_HISTORY_REL,
    ASSET_IMAGES_REL,
    ASSET_VIDEOS_REL,
    AUDIO_EXTS,
    IMAGE_EXTS,
    LEGACY_UPLOAD_ROOT_REL,
    VIDEO_EXTS,
    ASSETS_REL,
)
from .prompt_options import prompt_options_payload
from .repository import OpenClipRepository
from .schemas import KouboScriptTaskCreatePayload
from .talking_head_models import (
    DEFAULT_TALKING_HEAD_PRIVACY_GRID_PRESET,
    DEFAULT_TALKING_HEAD_VIDEO_MODEL_KEY,
    resolve_talking_head_privacy_grid_preset,
    resolve_talking_head_video_model,
    talking_head_video_model_key as infer_talking_head_video_model_key,
)

OPENCLIP_SOURCE = "openclip-analysis"
OPENCLIP_GROUP_ID = "openclip-analysis"
WORKFLOW_PERSON_TALKING_HEAD_V1 = "person_talking_head_v1"
CREATE_MODE_PERSON_TALKING_HEAD = "person_talking_head"
VARIABLES_REL = "SessionContext/Variables.json"
TASK_META_REL = "SessionOutput/task_list/task_meta.json"
SOURCE_SCRIPT_REL = "SessionOutput/subtitle/source_script.txt"
TALKING_HEAD_USER_SCRIPT_REL = "SessionContext/Script/user_script.txt"
TALKING_HEAD_REFERENCE_SCRIPT_REL = "SessionContext/Script/reference_script.txt"
FINAL_SRT_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
REWRITTEN_SRT_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
STORYBOARD_EDIT_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"
TALKING_HEAD_PORTRAIT_DIR_REL = "SessionInput/talking_head/portrait_images"
TALKING_HEAD_REFERENCE_VIDEO_DIR_REL = "SessionInput/talking_head/reference_videos"
SUPPORTED_PORTRAIT_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

ASSET_KIND_DIRS = {
    "image": (ASSET_IMAGES_REL, IMAGE_EXTS),
    "audio": (ASSET_AUDIOS_REL, AUDIO_EXTS),
    "video": (ASSET_VIDEOS_REL, VIDEO_EXTS),
}


def build_koubo_task_list_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    repo = OpenClipRepository(ctx.engine)

    def read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def text_value(value: Any) -> str:
        return str(value or "").strip()

    def normalized_script_for_change(value: Any) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in text.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    def safe_upload_filename(filename: str, fallback: str = "portrait.png") -> str:
        raw = Path(text_value(filename) or fallback).name.replace("\x00", "")
        raw = raw.strip(" .") or fallback
        suffix = Path(raw).suffix.lower()
        if suffix not in SUPPORTED_PORTRAIT_IMAGE_EXTS:
            suffix = Path(fallback).suffix.lower()
        stem = Path(raw).stem.strip(" .") or Path(fallback).stem
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:80].strip("._-") or "portrait"
        return f"{stem}{suffix}"

    async def save_bounded_upload(
        upload: UploadFile,
        target: Path,
        *,
        empty_code: str,
        empty_message: str,
        too_large_code: str,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.with_name(f".{target.name}.upload-{uuid.uuid4().hex}")
        total = 0
        try:
            with temp_target.open("wb") as handle:
                while True:
                    chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail={
                                "code": too_large_code,
                                "message": f"Upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit.",
                            },
                        )
                    handle.write(chunk)
            if total <= 0:
                raise HTTPException(status_code=400, detail={"code": empty_code, "message": empty_message})
            temp_target.replace(target)
        except Exception:
            try:
                temp_target.unlink()
            except FileNotFoundError:
                pass
            raise

    async def save_talking_head_portrait(workspace: Path, file: UploadFile | None) -> str:
        if file is None:
            return ""
        filename = safe_upload_filename(file.filename or "portrait.png")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_PORTRAIT_IMAGE_EXTS:
            raise HTTPException(status_code=400, detail={"code": "talking_head_portrait_unsupported", "message": f"Portrait image must be one of: {', '.join(sorted(SUPPORTED_PORTRAIT_IMAGE_EXTS))}"})
        target = workspace / TALKING_HEAD_PORTRAIT_DIR_REL / f"{now_ms()}_{filename}"
        await save_bounded_upload(
            file,
            target,
            empty_code="talking_head_portrait_empty",
            empty_message="Uploaded portrait image is empty.",
            too_large_code="talking_head_portrait_too_large",
        )
        return target.relative_to(workspace).as_posix()

    async def save_talking_head_reference_video(workspace: Path, file: UploadFile | None) -> str:
        if file is None:
            return ""
        raw_name = Path(text_value(file.filename) or "reference.mp4").name
        suffix = Path(raw_name).suffix.lower()
        if suffix not in VIDEO_EXTS:
            raise HTTPException(status_code=400, detail={"code": "talking_head_reference_video_unsupported", "message": f"Reference video must be one of: {', '.join(sorted(VIDEO_EXTS))}"})
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(raw_name).stem)[:80].strip("._-") or "reference"
        target = workspace / TALKING_HEAD_REFERENCE_VIDEO_DIR_REL / f"{now_ms()}_{stem}{suffix}"
        await save_bounded_upload(
            file,
            target,
            empty_code="talking_head_reference_video_empty",
            empty_message="Uploaded reference video is empty.",
            too_large_code="talking_head_reference_video_too_large",
        )
        return target.relative_to(workspace).as_posix()

    def install_staged_upload(staging_root: Path, workspace: Path, rel_path: str) -> str:
        rel_path = text_value(rel_path)
        if not rel_path:
            return ""
        source = (staging_root / rel_path).resolve()
        source.relative_to(staging_root.resolve())
        target = workspace / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        return rel_path

    def safe_unlink(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def safe_unlink_upload(workspace: Path, rel_path: Any, upload_dir_rel: str) -> None:
        rel = text_value(rel_path)
        if not rel:
            return
        try:
            workspace_root = workspace.resolve()
            upload_root = (workspace / upload_dir_rel).resolve()
            target = (workspace / rel).resolve()
            target.relative_to(workspace_root)
            target.relative_to(upload_root)
        except Exception:
            return
        safe_unlink(target)

    def rollback_talking_head_create(session_id: int, workspace: Path | None, opencode_session_id: str = "") -> None:
        def rollback_session_row() -> dict[str, Any]:
            row = ctx.session_repo.get(session_id) or {"id": session_id, "workspace_dir": ""}
            return {**row, **({"workspace_dir": str(workspace)} if workspace is not None else {})}

        if opencode_session_id and session_id:
            session_row = rollback_session_row()
            try:
                opencode_client_for(session_row).delete_session(opencode_session_id)
            except Exception as exc:
                ctx.event("warning", "cleanup", "OpenCode session cleanup failed after talking-head create rollback", {"session_id": session_id, "opencode_session_id": opencode_session_id, "error": str(exc)})
        if not session_id:
            return
        session_row = rollback_session_row()
        try:
            ctx.workflow_deletion_service.delete_session_db_first(session_row)
        except Exception as exc:
            ctx.event("warning", "cleanup", "Database cleanup failed after talking-head create rollback", {"session_id": session_id, "error": str(exc)})
        try:
            ctx.workflow_deletion_service.cleanup_workspace(session_row)
        except Exception as exc:
            ctx.event("warning", "cleanup", "Workspace cleanup failed after talking-head create rollback", {"session_id": session_id, "workspace_dir": session_row.get("workspace_dir"), "error": str(exc)})

    def workspace_for(task: dict[str, Any]) -> Path:
        workspace = str(task.get("workspace_dir") or "").strip()
        if workspace:
            return Path(workspace)
        session = ctx.session_repo.get(int(task["session_id"]))
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return Path(str(session["workspace_dir"]))

    def add_event(session_id: int, kind: str, payload: dict[str, Any]) -> None:
        ctx.session_event_service.add_event(session_id, kind, payload, workflow_id="openclip_analysis")

    def opencode_client_for(session_row: dict[str, Any]) -> OpenCodeSessionClient:
        try:
            return opencode_client_for_context(ctx, session_row, "OpenCode connection is incomplete. Finish Step 1 before creating tasks.")
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def refresh_opencode_client_for(session_row: dict[str, Any], reason: str) -> OpenCodeSessionClient:
        result = discover_and_save_opencode_runtime(ctx, reason=reason)
        selected = result.get("selected") or {}
        if not selected.get("healthy") or not selected.get("username") or not selected.get("password"):
            raise HTTPException(
                status_code=400,
                detail="OpenCode authentication failed and automatic rediscovery did not find valid credentials. Reconnect OpenCode in Connection / Step 1, then retry.",
            )
        return opencode_client_for(session_row)

    def create_opencode_session(session_row: dict[str, Any], title: str) -> dict[str, Any]:
        client = opencode_client_for(session_row)
        try:
            return client.create_session(title)
        except OpenCodeAuthError as exc:
            add_event(int(session_row["id"]), "koubo_task.opencode_auth_refreshed", {"stage": "create_session", "detail": str(exc)})
            client = refresh_opencode_client_for(session_row, "koubo_task.create_session_401")
            try:
                return client.create_session(title)
            except OpenCodeAuthError as retry_exc:
                raise HTTPException(status_code=400, detail=str(retry_exc)) from retry_exc

    def normalize_quick_config(value: Any) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}

        def number(key: str, fallback: float) -> float:
            try:
                parsed = float(raw.get(key))
                return parsed if parsed > 0 else fallback
            except Exception:
                return fallback

        scene = number("target_scene_seconds", 8.0)
        shot = number("target_shot_seconds", 16.0)
        mode = str(raw.get("language_boundary_mode") or "balanced").strip().lower()
        if mode not in {"strict", "balanced", "loose"}:
            mode = "balanced"
        try:
            tolerance = float(raw.get("split_tolerance_seconds"))
            tolerance = tolerance if tolerance >= 0 else 2.0
        except Exception:
            tolerance = 2.0
        return {
            "enabled": raw.get("enabled") is not False,
            "target_scene_seconds": max(1.0, scene),
            "target_shot_seconds": max(1.0, shot),
            "split_tolerance_seconds": tolerance,
            "language_boundary_mode": mode,
        }

    def load_quick_config(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return normalize_quick_config(value)
        try:
            return normalize_quick_config(json.loads(str(value or "{}")))
        except Exception:
            return normalize_quick_config({})

    def load_raw_config(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value or "{}"))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def default_rewrite_prompt(script: str, payload: KouboScriptTaskCreatePayload) -> str:
        base = (payload.rewrite_simple_prompt or payload.simple_prompt or "").strip()
        return "\n".join([
            "请基于用户给定脚本执行 SRT Rewrite。",
            "保持句子数量、顺序、srt_id、时间轴不变；每个输入句子只输出一个对应句子。",
            "如需优化表达，只围绕行业、人设、目标受众、产品信息和约束条件做轻量改写，不新增事实。",
            "输出必须使用简体中文。",
            f"简单提示词：{base}" if base else "",
            "完整脚本：",
            script.strip(),
        ]).strip()

    def default_storyboard_prompt(script: str, payload: KouboScriptTaskCreatePayload) -> str:
        base = (payload.storyboard_simple_prompt or payload.simple_prompt or "").strip()
        return "\n".join([
            "请把改写后的 SRT 组织为口播 StoryBoard。",
            "只做 Shot / Scene / formula_stage 结构化分组，不改写对白。",
            "每个 srt_id 必须出现且只出现一次，顺序不变，不能切断一句话。",
            f"视频公式：{payload.video_formula.strip() or '按脚本语义组织'}",
            f"简单提示词：{base}" if base else "",
            "完整脚本：",
            script.strip(),
        ]).strip()

    def split_plain_script(script: str) -> list[str]:
        paragraphs = [item.strip() for item in re.split(r"\n+", script.strip()) if item.strip()]
        chunks: list[str] = []
        for paragraph in paragraphs:
            parts = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", paragraph) if item.strip()]
            chunks.extend(parts or [paragraph])
        return chunks or [script.strip()]

    def parse_srt_script(script: str) -> list[dict[str, Any]]:
        pattern = re.compile(
            r"(?:^|\n)\s*(?:\d+\s*\n)?(\d{2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{1,3})\s*\n(.+?)(?=\n\s*\d+\s*\n\d{2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->|\Z)",
            re.S,
        )

        def seconds(value: str) -> float:
            hours, minutes, rest = value.replace(",", ".").split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(rest)

        items = []
        for index, match in enumerate(pattern.finditer(script), 1):
            start = seconds(match.group(1))
            end = seconds(match.group(2))
            dialogue = re.sub(r"\s+", " ", match.group(3)).strip()
            if not dialogue:
                continue
            items.append({
                "srt_id": f"srt_{index:04d}",
                "index": index,
                "start": round(start, 3),
                "end": round(max(end, start + 0.5), 3),
                "duration": round(max(end - start, 0.5), 3),
                "dialogue": dialogue,
                "image_path": "",
            })
        return items

    def script_to_srt_items(script: str, script_format: str, *, srt_target_seconds: float | None = None, portrait_image_path: str = "") -> dict[str, Any]:
        if script_format.strip().lower() == "srt":
            parsed = parse_srt_script(script)
            target_seconds = float(srt_target_seconds or 0)
            for item in parsed:
                item["image_path"] = portrait_image_path if portrait_image_path and int(item.get("index") or 0) == 1 else ""
                duration = float(item.get("duration") or 0)
                item["warnings"] = [{"code": "srt_duration_over_target", "message": "SRT duration exceeds target seconds."}] if target_seconds > 0 and duration > target_seconds else []
            if parsed:
                return {"schema_version": "analysis_v1_final_srt_frame_items_0.1", "source_type": "script", "items": parsed}
        current = 0.0
        items = []
        fixed_duration = float(srt_target_seconds or 0)
        for index, dialogue in enumerate(split_plain_script(script), 1):
            zh_chars = len(re.findall(r"[\u4e00-\u9fff]", dialogue))
            non_space = len(re.sub(r"\s+", "", dialogue))
            duration = fixed_duration if fixed_duration > 0 else max(2.0, (zh_chars or non_space) / 4.5)
            start = current
            end = current + duration
            items.append({
                "srt_id": f"srt_{index:04d}",
                "index": index,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(duration, 3),
                "dialogue": dialogue,
                "image_path": portrait_image_path if index == 1 else "",
                "warnings": [{"code": "srt_duration_over_target", "message": "SRT duration exceeds target seconds."}] if fixed_duration > 0 and duration > fixed_duration else [],
            })
            current = end
        return {"schema_version": "analysis_v1_final_srt_frame_items_0.1", "source_type": "script", "items": items}

    def talking_head_quick_config(source_config: dict[str, Any], srt_target_seconds: float) -> dict[str, Any]:
        quick = normalize_quick_config(source_config or {
            "target_scene_seconds": 8.0,
            "target_shot_seconds": 8.0,
            "split_tolerance_seconds": 2.0,
            "language_boundary_mode": "strict",
        })
        seconds = max(1.0, float(srt_target_seconds or 8.0))
        return {
            **quick,
            "enabled": True,
            "shot_policy": "single_shot",
            "scene_policy": "single_scene",
            "segment_policy": "merge_srt_to_single_video_length",
            "srt_target_seconds": seconds,
        }

    def talking_head_video_model_config(payload: KouboScriptTaskCreatePayload) -> dict[str, Any]:
        selected = resolve_talking_head_video_model(
            model_key=payload.talking_head_video_model_key,
            provider=payload.talking_head_video_provider,
            model=payload.talking_head_video_model,
            model_alias=payload.talking_head_video_model_alias,
        )
        if not selected:
            raise HTTPException(status_code=400, detail={"code": "talking_head_video_model_invalid", "message": "人物口播视频模型必须从四个标准别名中选择。"})
        payload.talking_head_video_model_key = text_value(selected.get("model_key"))
        payload.talking_head_video_provider = text_value(selected.get("provider"))
        payload.talking_head_video_model = text_value(selected.get("model"))
        payload.talking_head_video_model_alias = text_value(selected.get("model_alias"))
        if payload.talking_head_video_model_key == "max_sd_2":
            requested_privacy_mode = text_value(payload.reference_privacy_mode) or "red_grid_guide"
            if requested_privacy_mode != "red_grid_guide":
                raise HTTPException(status_code=400, detail={"code": "talking_head_reference_privacy_mode_invalid", "message": "Max SD 2 只支持隐私网格（身份可见）。"})
            payload.reference_privacy_mode = "red_grid_guide"
            preset = resolve_talking_head_privacy_grid_preset(payload.privacy_grid_preset)
            if not preset:
                raise HTTPException(status_code=400, detail={"code": "talking_head_privacy_grid_preset_invalid", "message": "隐私网格预设必须从 8 个标准选项中选择。"})
            payload.privacy_grid_preset = text_value(preset.get("preset"))
            payload.cell_size_reference = int(preset["cell_size_reference"])
            payload.line_width_reference = float(preset["line_width_reference"])
            if payload.use_default_reference_video:
                payload.apply_privacy_grid_to_reference_video = False
        return selected

    def resolve_talking_head_voice(payload: KouboScriptTaskCreatePayload) -> None:
        voice_id = text_value(payload.voice_id)
        if not voice_id:
            return
        target = resolve_tts_voice_alias(ctx, voice_id)
        if voice_id.startswith(PUBLIC_TTS_VOICE_PREFIX) and not target:
            raise HTTPException(status_code=400, detail={"code": "talking_head_voice_alias_invalid", "message": "请选择有效的云端克隆音色。"})
        if target:
            payload.voice_id = text_value(target.get("voice_id"))
            payload.voice_provider = text_value(target.get("provider")) or payload.voice_provider

    def validate_talking_head_voice(payload: KouboScriptTaskCreatePayload) -> None:
        if not text_value(payload.voice_id):
            return
        provider = text_value(payload.voice_provider).lower() or "heygen"
        if provider == "minimaxi":
            provider = "minimax"
        payload.voice_provider = provider
        if provider not in {"heygen", "cosyvoice", "minimax"}:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "talking_head_voice_provider_unsupported",
                    "message": f"人物口播当前支持 HeyGen、CosyVoice 和 MiniMax 克隆声音，不能使用 {provider}。",
                },
            )
        config = load_config(ctx, "voice-clone")
        providers = config.get("providers") if isinstance(config.get("providers"), list) else []
        configured = next(
            (
                item for item in providers
                if isinstance(item, dict)
                and text_value(item.get("provider")).lower() == provider
                and item.get("enabled") is not False
            ),
            None,
        )
        api_key = text_value(load_stored_key(ctx, "voice-clone", provider))
        if not configured or not api_key:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "talking_head_voice_provider_not_configured",
                    "message": f"请先在连接设置中启用 {provider} 克隆声音并配置 API Key。",
                },
            )

    def talking_head_meta_config(payload: KouboScriptTaskCreatePayload, portrait_image_path: str, selected_video_model: dict[str, Any] | None = None) -> dict[str, Any]:
        srt_seconds = max(1.0, float(payload.srt_target_seconds or 8.0))
        portrait_segments = max(1, int(payload.portrait_segments_per_image or 2))
        privacy_grid_preset = text_value(payload.privacy_grid_preset) or DEFAULT_TALKING_HEAD_PRIVACY_GRID_PRESET
        apply_privacy_grid_to_reference_video = bool(
            payload.apply_privacy_grid_to_reference_video
            and not payload.use_default_reference_video
        )
        tempo_by_voice_id = {
            text_value(voice_id): max(0.1, float(tempo or 1.0))
            for voice_id, tempo in dict(payload.voice_tempo_by_id or {}).items()
            if text_value(voice_id)
        }
        if text_value(payload.voice_id):
            tempo_by_voice_id[text_value(payload.voice_id)] = max(0.1, float(payload.tempo or 1.0))
        return {
            "portrait": {
                "source": "upload" if portrait_image_path else "",
                "portrait_image_path": portrait_image_path,
                "role": "host_portrait_first_frame",
            },
            "voice_timing": {
                "provider": text_value(payload.voice_provider) or "heygen",
                "voice_id": text_value(payload.voice_id),
                "voice_label": text_value(payload.voice_label),
                "tempo": max(0.1, float(payload.tempo or 1.0)),
                "tempo_by_voice_id": tempo_by_voice_id,
                "duration_estimation": "voice_tempo" if text_value(payload.voice_id) else "srt_target_seconds",
            },
            "segment_planning": {
                "shot_policy": "single_shot",
                "scene_policy": "single_scene",
                "segment_policy": "merge_srt_to_single_video_length",
                "srt_target_seconds": srt_seconds,
                "portrait_segments_per_image": portrait_segments,
                "allow_sentence_split": False,
            },
            "video_model": selected_video_model or talking_head_video_model_config(payload),
            "reference_video": {
                "use_system_default": bool(payload.use_default_reference_video),
                "reference_video_path": text_value(payload.reference_video_path),
                "source": "system_default" if payload.use_default_reference_video else ("upload" if text_value(payload.reference_video_path) else ""),
                "enabled": text_value(payload.talking_head_video_model_key) in {"max_2_7_w", "max_sd_2"},
            },
            "reference_privacy": {
                "enabled": text_value(payload.talking_head_video_model_key) == "max_sd_2",
                "reference_privacy_mode": "red_grid_guide",
                "apply_privacy_grid_to_reference_video": apply_privacy_grid_to_reference_video,
                "apply_privacy_grid_to_target_identity_image": bool(payload.apply_privacy_grid_to_target_identity_image),
                "privacy_grid_preset": privacy_grid_preset,
                "cell_size_reference": int(payload.cell_size_reference),
                "line_width_reference": float(payload.line_width_reference),
                "render_config": {
                    "privacy_grid_preset": privacy_grid_preset,
                    "privacy_grid": {
                        "cell_size_reference": int(payload.cell_size_reference),
                        "line_width_reference": float(payload.line_width_reference),
                    },
                },
            },
            "resource_strategy": {
                "kind": "talking_head_only",
                "allow_cutaway": False,
            },
            "tail_frame_policy": {
                "mode": "previous_segment_tail_frame",
                "reset_every_segments": portrait_segments,
                "reset_source": "uploaded_portrait_image",
                "fallback": "uploaded_portrait_image",
            },
        }

    def normalize_script_creation_mode(value: Any) -> str:
        normalized = text_value(value).lower()
        aliases = {
            "user": "user_provided",
            "user_provided": "user_provided",
            "generate": "ai_create",
            "ai_create": "ai_create",
            "rewrite": "ai_rewrite",
            "ai_rewrite": "ai_rewrite",
        }
        return aliases.get(normalized, "user_provided")

    def talking_head_config_payload(
        payload: KouboScriptTaskCreatePayload,
        *,
        script_creation_mode: str,
        script_text: str,
        rewrite_simple: str,
        rewrite_final: str,
        storyboard_simple: str,
        storyboard_final: str,
        quick_config: dict[str, Any],
        talking_head: dict[str, Any],
    ) -> dict[str, Any]:
        user_script = script_text if script_creation_mode == "user_provided" else ""
        reference_script = script_text if script_creation_mode == "ai_rewrite" else ""
        return {
            "schema_version": "talking_head_task_config_1.0",
            "script_input": {
                "user_script_text": user_script,
                "reference_script_text": reference_script,
                "script_format": text_value(payload.script_format) or "plain",
            },
            "business_context": {
                "industry": payload.industry.strip(),
                "persona": payload.persona.strip(),
                "target_audience": payload.target_audience.strip(),
                "video_formula": payload.video_formula.strip() or "人物口播",
                "product_info": payload.product_info.strip(),
                "constraints": payload.constraints.strip(),
            },
            "script_prompt": {
                "simple_prompt": rewrite_simple,
                "final_prompt": rewrite_final,
                "model_provider": payload.prompt_model_provider.strip(),
                "model_id": payload.prompt_model_id.strip(),
            },
            "storyboard": {
                "simple_prompt": storyboard_simple,
                "final_prompt": storyboard_final,
                "quick_config": quick_config,
            },
            "talking_head": talking_head,
            "run_model": {
                "provider": payload.run_model_provider.strip(),
                "model_id": payload.run_model_id.strip(),
            },
        }

    def load_talking_head_config(task_id: int) -> tuple[dict[str, Any], str]:
        row = repo.get_talking_head_config(task_id) or {}
        return load_raw_config(row.get("config_json")), normalize_script_creation_mode(row.get("script_creation_mode"))

    def validate_talking_head_script_input(mode: str, script_text: str) -> None:
        if mode == "user_provided" and not script_text:
            raise HTTPException(status_code=400, detail={"code": "talking_head_user_script_required", "message": "用户给定脚本模式必须填写完整口播脚本。"})
        if mode == "ai_rewrite" and not script_text:
            raise HTTPException(status_code=400, detail={"code": "talking_head_reference_script_required", "message": "智能改写脚本模式必须填写参考脚本。"})

    def task_counts(workspace: Path) -> dict[str, int]:
        srt = read_json(workspace / FINAL_SRT_ITEMS_REL) or {}
        rewritten = read_json(workspace / REWRITTEN_SRT_ITEMS_REL) or {}
        storyboard = read_json(workspace / STORYBOARD_REL) or {}
        shots = storyboard.get("shots") if isinstance(storyboard, dict) else []
        return {
            "dialogue_count": len(srt.get("items") or rewritten.get("items") or []),
            "shot_count": len(shots or []),
            "scene_count": sum(len(shot.get("scenes") or []) for shot in (shots or []) if isinstance(shot, dict)),
        }

    def task_asset_counts(workspace: Path) -> dict[str, int]:
        paths_by_kind: dict[str, set[str]] = {"audio": set(), "image": set(), "video": set()}

        def add_path(kind: str, rel_path: str) -> None:
            normalized_kind = kind if kind in paths_by_kind else ""
            normalized_path = str(rel_path or "").strip()
            if not normalized_kind or not normalized_path or normalized_path.startswith(f"{ASSET_HISTORY_REL}/"):
                return
            paths_by_kind[normalized_kind].add(normalized_path)

        def kind_for_path(rel_path: str, explicit: Any = "") -> str:
            explicit_kind = str(explicit or "").strip().lower()
            if "audio" in explicit_kind:
                return "audio"
            if "image" in explicit_kind:
                return "image"
            if "video" in explicit_kind:
                return "video"
            if rel_path.startswith(f"{ASSET_AUDIOS_REL}/"):
                return "audio"
            if rel_path.startswith(f"{ASSET_IMAGES_REL}/"):
                return "image"
            if rel_path.startswith(f"{ASSET_VIDEOS_REL}/"):
                return "video"
            suffix = Path(rel_path).suffix.lower()
            if suffix in AUDIO_EXTS:
                return "audio"
            if suffix in IMAGE_EXTS:
                return "image"
            if suffix in VIDEO_EXTS:
                return "video"
            return ""

        for kind, (rel_dir, allowed_exts) in ASSET_KIND_DIRS.items():
            root = workspace / rel_dir
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.name != ".DS_Store" and path.suffix.lower() in allowed_exts:
                    add_path(kind, path.relative_to(workspace).as_posix())

        audio_root = workspace / ASSET_AUDIOS_REL
        if audio_root.exists():
            for sidecar in sorted(audio_root.glob("*.json"), key=lambda item: item.name):
                payload = read_json(sidecar)
                audio = payload.get("audio") if isinstance(payload, dict) and isinstance(payload.get("audio"), dict) else {}
                audio_path = str(audio.get("path") or "").strip()
                if not audio_path or not audio_path.startswith(f"{ASSET_AUDIOS_REL}/") or Path(audio_path).suffix.lower() not in AUDIO_EXTS:
                    audio_path = f"{ASSET_AUDIOS_REL}/{sidecar.stem}.wav"
                add_path("audio", audio_path)

        manifest = read_json(workspace / ASSETS_REL)
        manifest_items = manifest.get("assets") if isinstance(manifest, dict) else []
        if isinstance(manifest_items, list):
            for item in manifest_items:
                if not isinstance(item, dict):
                    continue
                rel_path = str(item.get("path") or item.get("id") or "").strip()
                if not rel_path:
                    continue
                explicit = item.get("kind") or item.get("asset_type")
                kind = kind_for_path(rel_path, explicit)
                if not kind:
                    continue
                if rel_path.startswith((f"{ASSET_IMAGES_REL}/", f"{ASSET_AUDIOS_REL}/", f"{ASSET_VIDEOS_REL}/", f"{LEGACY_UPLOAD_ROOT_REL}/")):
                    add_path(kind, rel_path)

        return {
            "audio_asset_count": len(paths_by_kind["audio"]),
            "image_asset_count": len(paths_by_kind["image"]),
            "video_asset_count": len(paths_by_kind["video"]),
        }

    def task_status(task: dict[str, Any], workspace: Path, meta: dict[str, Any]) -> dict[str, str]:
        has_srt = (workspace / FINAL_SRT_ITEMS_REL).exists()
        has_rewritten = (workspace / REWRITTEN_SRT_ITEMS_REL).exists()
        has_storyboard = (workspace / STORYBOARD_REL).exists()
        has_edit = (workspace / STORYBOARD_EDIT_REL).exists()
        if meta.get("archived_at"):
            status = "archived"
        elif has_storyboard:
            status = "editable"
        elif has_srt:
            status = str(meta.get("initialize_status") or "waiting_input")
        else:
            status = str(task.get("status") or "draft")
        return {
            "status": status,
            "srt_status": "rewritten" if has_rewritten else ("generated" if has_srt else "missing"),
            "storyboard_status": "edited" if has_edit else ("generated" if has_storyboard else "missing"),
        }

    def read_text_preview(path: Path, limit: int = 160) -> str:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                return handle.read(limit)
        except Exception:
            return ""

    def serialize_task(task: dict[str, Any], *, detail: bool = True) -> dict[str, Any]:
        workspace = workspace_for(task)
        meta = read_json(workspace / TASK_META_REL) or {}
        variables = read_json(workspace / VARIABLES_REL) or {}
        storyboard = read_json(workspace / STORYBOARD_REL) or {}
        talking_head_config, script_creation_mode = load_talking_head_config(int(task["id"]))
        variables = variables if isinstance(variables, dict) else {}
        storyboard = storyboard if isinstance(storyboard, dict) else {}
        workflow_mode = str(task.get("workflow_mode") or "").strip() or infer_openclip_workflow_mode(task, workspace=workspace, meta=meta)
        create_mode = str(meta.get("create_mode") or ("script" if (workspace / SOURCE_SCRIPT_REL).exists() else "video"))
        is_talking_head_source = (
            workflow_mode == WORKFLOW_PERSON_TALKING_HEAD_V1
            or bool(talking_head_config)
            or str(meta.get("profile_id") or "") == WORKFLOW_PERSON_TALKING_HEAD_V1
            or str(meta.get("workflow_id") or "") == WORKFLOW_PERSON_TALKING_HEAD_V1
            or str(meta.get("display_source_label") or "") == "人物口播"
            or str(task.get("sender_name") or "") == "人物口播"
        )
        input_mode = str(meta.get("input_mode") or ("script_only" if create_mode == "script" else "video"))
        if is_talking_head_source:
            create_mode = CREATE_MODE_PERSON_TALKING_HEAD
            input_mode = {
                "user_provided": "user_script",
                "ai_create": "prompt_only",
                "ai_rewrite": "reference_script",
            }.get(script_creation_mode, "prompt_only")
        if workflow_mode == WORKFLOW_DANCE_MIMIC_V1:
            create_mode = "dance_mimic"
            input_mode = str(meta.get("input_mode") or "reference_video")
        raw_quick_config = load_raw_config(task.get("storyboard_quick_config_json"))
        quick_config = raw_quick_config if workflow_mode == WORKFLOW_DANCE_MIMIC_V1 else load_quick_config(raw_quick_config)
        dance_meta = meta.get("dance_mimic") if isinstance(meta.get("dance_mimic"), dict) else {}
        talking_head_meta = talking_head_config.get("talking_head") if isinstance(talking_head_config.get("talking_head"), dict) else (meta.get("talking_head") if isinstance(meta.get("talking_head"), dict) else {})
        source_script = ""
        script_input = talking_head_config.get("script_input") if isinstance(talking_head_config.get("script_input"), dict) else {}
        if is_talking_head_source:
            source_script = text_value(script_input.get("user_script_text") or script_input.get("reference_script_text"))
            if not detail:
                source_script = source_script[:160]
        else:
            source_script_path = workspace / SOURCE_SCRIPT_REL
            if source_script_path.exists():
                source_script = source_script_path.read_text(encoding="utf-8", errors="ignore") if detail else read_text_preview(source_script_path)
        counts = task_counts(workspace)
        asset_counts = task_asset_counts(workspace)
        statuses = task_status(task, workspace, meta)
        title = str(task.get("title") or meta.get("title") or "").strip() or f"Task #{task['id']}"
        script_preview = str(meta.get("script_preview") or source_script[:160]).strip()
        payload = {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "title": title,
            "workflow_mode": workflow_mode,
            "workflow_id": WORKFLOW_PERSON_TALKING_HEAD_V1 if is_talking_head_source else str(meta.get("workflow_id") or workflow_mode),
            "profile_id": WORKFLOW_PERSON_TALKING_HEAD_V1 if is_talking_head_source else str(meta.get("profile_id") or ""),
            "create_mode": create_mode,
            "input_mode": input_mode,
            "script_creation_mode": script_creation_mode if is_talking_head_source else "",
            "status": statuses["status"],
            "srt_status": statuses["srt_status"],
            "storyboard_status": statuses["storyboard_status"],
            "reference_video": str(task.get("reference_video_path") or ""),
            "script_preview": script_preview,
            "task_summary": str(variables.get("task_summary") or storyboard.get("task_summary") or "").strip(),
            "dialogue_count": counts["dialogue_count"],
            "shot_count": counts["shot_count"],
            "scene_count": counts["scene_count"],
            "audio_asset_count": asset_counts["audio_asset_count"],
            "image_asset_count": asset_counts["image_asset_count"],
            "video_asset_count": asset_counts["video_asset_count"],
            "voice_status": str(meta.get("voice_status") or "not_selected"),
            "last_error": str(meta.get("last_error") or ""),
            "archived": bool(meta.get("archived_at")),
            "analysis_url": f"#/dance-mimic/tasks/{int(task['id'])}" if workflow_mode == WORKFLOW_DANCE_MIMIC_V1 else (f"#/talking-head/tasks/{int(task['id'])}" if create_mode == CREATE_MODE_PERSON_TALKING_HEAD else f"#/analysis-v1/tasks/{int(task['id'])}"),
            "dance_mimic_url": f"#/dance-mimic/tasks/{int(task['id'])}",
            "talking_head_url": f"#/talking-head/tasks/{int(task['id'])}",
            "storyboard_url": f"#/koubo-storyboard/tasks/{int(task['id'])}",
            "created_at": int(task.get("created_at") or 0),
            "updated_at": int(task.get("updated_at") or 0),
        }
        if detail:
            business = talking_head_config.get("business_context") if isinstance(talking_head_config.get("business_context"), dict) else {}
            script_prompt = talking_head_config.get("script_prompt") if isinstance(talking_head_config.get("script_prompt"), dict) else {}
            storyboard_config = talking_head_config.get("storyboard") if isinstance(talking_head_config.get("storyboard"), dict) else {}
            payload.update({
                "source_script": source_script,
                "industry": str(business.get("industry") if is_talking_head_source else task.get("industry") or ""),
                "persona": str(business.get("persona") if is_talking_head_source else task.get("persona") or ""),
                "target_audience": str(business.get("target_audience") if is_talking_head_source else task.get("target_audience") or ""),
                "product_info": str(business.get("product_info") if is_talking_head_source else task.get("product_info") or ""),
                "constraints": str(business.get("constraints") if is_talking_head_source else task.get("constraints") or ""),
                "analysis_goal": str(task.get("analysis_goal") or ""),
                "video_formula": str(business.get("video_formula") if is_talking_head_source else task.get("video_formula") or ""),
                "simple_prompt": str(script_prompt.get("simple_prompt") if is_talking_head_source else task.get("simple_prompt") or ""),
                "final_prompt": str(script_prompt.get("final_prompt") if is_talking_head_source else task.get("final_prompt") or ""),
                "rewrite_simple_prompt": str(script_prompt.get("simple_prompt") if is_talking_head_source else task.get("rewrite_simple_prompt") or task.get("simple_prompt") or ""),
                "rewrite_final_prompt": str(script_prompt.get("final_prompt") if is_talking_head_source else task.get("rewrite_final_prompt") or task.get("final_prompt") or ""),
                "storyboard_simple_prompt": str(storyboard_config.get("simple_prompt") if is_talking_head_source else task.get("storyboard_simple_prompt") or ""),
                "storyboard_final_prompt": str(storyboard_config.get("final_prompt") if is_talking_head_source else task.get("storyboard_final_prompt") or ""),
                "storyboard_quick_config": storyboard_config.get("quick_config") if is_talking_head_source else quick_config,
                "target_identity_image": str(dance_meta.get("target_identity_image_path") or quick_config.get("target_identity_image_path") or meta.get("target_identity_image_path") or ""),
                "portrait_image": str((talking_head_meta.get("portrait") or {}).get("portrait_image_path") if isinstance(talking_head_meta.get("portrait"), dict) else ""),
                "talking_head": talking_head_meta,
                "reference_privacy_mode": str(dance_meta.get("reference_privacy_mode") or quick_config.get("reference_privacy_mode") or meta.get("reference_privacy_mode") or ""),
                "workspace_dir": str(workspace),
            })
        return payload

    @router.get("/api/koubo-tasks/options")
    async def koubo_task_options() -> dict[str, list[str]]:
        return prompt_options_payload()

    @router.get("/api/koubo-tasks")
    async def list_koubo_tasks(include_archived: bool = Query(False)) -> dict[str, Any]:
        items = [serialize_task(task, detail=False) for task in repo.list_task_summaries()]
        if not include_archived:
            items = [item for item in items if not item["archived"]]
        return {"items": items}

    @router.get("/api/koubo-tasks/{task_id}")
    async def get_koubo_task_detail(task_id: int) -> dict[str, Any]:
        task = repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="OpenClip task not found")
        return {"ok": True, "item": serialize_task(task, detail=True)}

    @router.post("/api/koubo-tasks/create-from-script")
    async def create_koubo_script_task(payload: KouboScriptTaskCreatePayload) -> dict[str, Any]:
        script = payload.script.strip()
        created = now_ms()
        title_seed = script or payload.product_info.strip() or payload.rewrite_simple_prompt.strip() or payload.storyboard_simple_prompt.strip() or payload.industry.strip()
        title = payload.title.strip() or (split_plain_script(title_seed)[0][:32] if title_seed else "") or "脚本口播任务"
        session_id = ctx.session_repo.create(
            source=OPENCLIP_SOURCE,
            group_id=OPENCLIP_GROUP_ID,
            sender_name="OpenClip",
            title=title,
            command_text="",
            status="draft",
            workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"),
            share_token=ctx.new_share_token(),
            created_at=created,
            updated_at=created,
        )
        workspace = ctx.workspace_store.create_session_workspace(session_id)
        ctx.session_repo.update(session_id, workspace_dir=str(workspace), updated_at=created)
        session_row = ctx.session_repo.get(session_id) or {"id": session_id, "workspace_dir": str(workspace), "title": title}
        op_session = create_opencode_session(session_row, title)
        ctx.session_repo.update(session_id, opencode_session_id=str(op_session["id"]), updated_at=now_ms())

        quick_config = normalize_quick_config(payload.storyboard_quick_config)
        rewrite_simple = payload.rewrite_simple_prompt.strip() or payload.simple_prompt.strip()
        storyboard_simple = payload.storyboard_simple_prompt.strip() or payload.simple_prompt.strip()
        rewrite_final = payload.rewrite_final_prompt.strip() or default_rewrite_prompt(script or title, payload)
        storyboard_final = payload.storyboard_final_prompt.strip() or default_storyboard_prompt(script or title, payload)
        task_id = repo.create_task(
            session_id=session_id,
            status="draft",
            workflow_mode="script",
            reference_video_path="",
            industry=payload.industry.strip(),
            persona=payload.persona.strip(),
            target_audience=payload.target_audience.strip(),
            product_info=payload.product_info.strip(),
            constraints=payload.constraints.strip(),
            analysis_goal=payload.analysis_goal.strip() or "脚本生成口播任务",
            video_formula=payload.video_formula.strip() or "脚本口播",
            simple_prompt=rewrite_simple,
            final_prompt=rewrite_final,
            rewrite_simple_prompt=rewrite_simple,
            rewrite_final_prompt=rewrite_final,
            storyboard_simple_prompt=storyboard_simple,
            storyboard_final_prompt=storyboard_final,
            storyboard_quick_config_json=json.dumps(quick_config, ensure_ascii=False, sort_keys=True),
            prompt_model_provider=payload.prompt_model_provider.strip(),
            prompt_model_id=payload.prompt_model_id.strip(),
            run_model_provider=payload.run_model_provider.strip(),
            run_model_id=payload.run_model_id.strip(),
            created_at=created,
            updated_at=now_ms(),
        )
        srt_payload = script_to_srt_items(script, payload.script_format) if script else {"schema_version": "analysis_v1_final_srt_frame_items_0.1", "source_type": "script", "items": []}
        if script:
            write_text(workspace / SOURCE_SCRIPT_REL, script)
            write_json(workspace / FINAL_SRT_ITEMS_REL, srt_payload)
        write_json(workspace / TASK_META_REL, {
            "schema_version": "koubo_task_list_meta_0.1",
            "workflow_id": "script",
            "workflow_mode": "script",
            "task_id": task_id,
            "session_id": session_id,
            "title": title,
            "create_mode": "script",
            "input_mode": "script_only" if script else "prompt_only",
            "source_script_path": SOURCE_SCRIPT_REL if script else "",
            "source_srt_items_path": FINAL_SRT_ITEMS_REL if script else "",
            "script_preview": script[:160],
            "voice_status": "not_selected",
            "created_at": created,
            "initialize_status": "waiting_input" if script else "draft",
            "updated_at": now_ms(),
        })
        add_event(session_id, "koubo_task.script_created", {"task_id": task_id, "workspace_dir": str(workspace), "dialogue_count": len(srt_payload["items"]), "input_mode": "script_only" if script else "prompt_only"})
        return {"ok": True, "task_id": task_id, "session_id": session_id, "workspace_dir": str(workspace), "status": "waiting_input" if script else "draft", "item": serialize_task(repo.get_task(task_id) or {"id": task_id, "session_id": session_id, "workspace_dir": str(workspace)})}

    @router.post("/api/koubo-tasks/create-talking-head")
    async def create_koubo_talking_head_task(
        title: str = Form(""),
        script: str = Form(""),
        script_creation_mode: str = Form("user_provided"),
        script_format: str = Form("plain"),
        industry: str = Form("医美"),
        persona: str = Form("强判断老板型"),
        target_audience: str = Form("老板"),
        video_formula: str = Form("人物口播"),
        product_info: str = Form(""),
        constraints: str = Form(""),
        rewrite_simple_prompt: str = Form(""),
        rewrite_final_prompt: str = Form(""),
        storyboard_simple_prompt: str = Form(""),
        storyboard_final_prompt: str = Form(""),
        storyboard_quick_config: str = Form(""),
        prompt_model_provider: str = Form(""),
        prompt_model_id: str = Form(""),
        run_model_provider: str = Form(""),
        run_model_id: str = Form(""),
        srt_target_seconds: float = Form(15.0),
        portrait_segments_per_image: int = Form(2),
        voice_provider: str = Form("heygen"),
        voice_id: str = Form(""),
        voice_label: str = Form(""),
        tempo: float = Form(1.0),
        voice_tempo_by_id: str = Form("{}"),
        talking_head_video_model_key: str = Form(""),
        talking_head_video_provider: str = Form(""),
        talking_head_video_model: str = Form(""),
        talking_head_video_model_alias: str = Form(""),
        use_default_reference_video: bool = Form(True),
        reference_privacy_mode: str = Form("red_grid_guide"),
        apply_privacy_grid_to_reference_video: bool = Form(True),
        apply_privacy_grid_to_target_identity_image: bool = Form(True),
        privacy_grid_preset: str = Form(DEFAULT_TALKING_HEAD_PRIVACY_GRID_PRESET),
        cell_size_reference: int = Form(12),
        line_width_reference: float = Form(1.0),
        portrait_image_file: UploadFile | None = File(default=None),
        reference_video_file: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        quick_config_from_form = load_quick_config(storyboard_quick_config) if str(storyboard_quick_config or "").strip() else normalize_quick_config({
            "target_scene_seconds": 15.0,
            "target_shot_seconds": 15.0,
            "split_tolerance_seconds": 0.0,
            "language_boundary_mode": "strict",
        })
        srt_target_seconds = max(1.0, float(quick_config_from_form.get("target_shot_seconds") or srt_target_seconds or 8.0))
        payload = KouboScriptTaskCreatePayload(
            title=title,
            script=script,
            script_creation_mode=script_creation_mode,
            script_format=script_format,
            industry=industry,
            persona=persona,
            target_audience=target_audience,
            video_formula=video_formula,
            product_info=product_info,
            constraints=constraints,
            analysis_goal="人物口播",
            rewrite_simple_prompt=rewrite_simple_prompt,
            rewrite_final_prompt=rewrite_final_prompt,
            storyboard_simple_prompt=storyboard_simple_prompt,
            storyboard_final_prompt=storyboard_final_prompt,
            prompt_model_provider=prompt_model_provider,
            prompt_model_id=prompt_model_id,
            run_model_provider=run_model_provider,
            run_model_id=run_model_id,
            storyboard_quick_config=quick_config_from_form,
            profile_id=WORKFLOW_PERSON_TALKING_HEAD_V1,
            create_mode=CREATE_MODE_PERSON_TALKING_HEAD,
            srt_target_seconds=srt_target_seconds,
            portrait_segments_per_image=portrait_segments_per_image,
            voice_provider=voice_provider,
            voice_id=voice_id,
            voice_label=voice_label,
            tempo=tempo,
            voice_tempo_by_id=load_raw_config(voice_tempo_by_id),
            talking_head_video_model_key=talking_head_video_model_key or DEFAULT_TALKING_HEAD_VIDEO_MODEL_KEY,
            talking_head_video_provider=talking_head_video_provider,
            talking_head_video_model=talking_head_video_model,
            talking_head_video_model_alias=talking_head_video_model_alias,
            use_default_reference_video=use_default_reference_video,
            reference_privacy_mode=reference_privacy_mode,
            apply_privacy_grid_to_reference_video=apply_privacy_grid_to_reference_video,
            apply_privacy_grid_to_target_identity_image=apply_privacy_grid_to_target_identity_image,
            privacy_grid_preset=privacy_grid_preset,
            cell_size_reference=cell_size_reference,
            line_width_reference=line_width_reference,
        )
        resolve_talking_head_voice(payload)
        validate_talking_head_voice(payload)
        selected_video_model = talking_head_video_model_config(payload)
        payload.srt_target_seconds = min(payload.srt_target_seconds, float(selected_video_model["max_duration_seconds"]))
        payload.storyboard_quick_config = {
            **payload.storyboard_quick_config,
            "target_scene_seconds": payload.srt_target_seconds,
            "target_shot_seconds": payload.srt_target_seconds,
            "split_tolerance_seconds": 0 if payload.srt_target_seconds >= float(selected_video_model["max_duration_seconds"]) else float(payload.storyboard_quick_config.get("split_tolerance_seconds", 1)),
        }
        script_text = payload.script.strip()
        script_mode = normalize_script_creation_mode(payload.script_creation_mode)
        validate_talking_head_script_input(script_mode, script_text)
        created = now_ms()
        title_seed = script_text or payload.product_info.strip() or payload.rewrite_simple_prompt.strip() or payload.storyboard_simple_prompt.strip() or payload.industry.strip()
        task_title = payload.title.strip() or (split_plain_script(title_seed)[0][:32] if title_seed else "") or "人物口播任务"
        with tempfile.TemporaryDirectory(prefix="opencrew-talking-head-upload-") as staging_dir:
            staging_root = Path(staging_dir)
            staged_portrait_rel = await save_talking_head_portrait(staging_root, portrait_image_file)
            needs_reference_video = not payload.use_default_reference_video and payload.talking_head_video_model_key in {"max_2_7_w", "max_sd_2"}
            staged_reference_rel = await save_talking_head_reference_video(staging_root, reference_video_file) if needs_reference_video else ""
            payload.reference_video_path = staged_reference_rel
            session_id = 0
            workspace: Path | None = None
            opencode_session_id = ""
            try:
                session_id = ctx.session_repo.create(
                    source=OPENCLIP_SOURCE,
                    group_id=OPENCLIP_GROUP_ID,
                    sender_name="人物口播",
                    title=task_title,
                    command_text="",
                    status="draft",
                    workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"),
                    share_token=ctx.new_share_token(),
                    created_at=created,
                    updated_at=created,
                )
                workspace = ctx.workspace_store.create_session_workspace(session_id)
                ctx.session_repo.update(session_id, workspace_dir=str(workspace), updated_at=created)
                portrait_rel = install_staged_upload(staging_root, workspace, staged_portrait_rel)
                payload.reference_video_path = install_staged_upload(staging_root, workspace, staged_reference_rel)
                talking_head = talking_head_meta_config(payload, portrait_rel, selected_video_model)
                quick_config = talking_head_quick_config(payload.storyboard_quick_config, payload.srt_target_seconds)
                quick_config = {
                    **quick_config,
                    "workflow_profile": {
                        "profile_id": WORKFLOW_PERSON_TALKING_HEAD_V1,
                        "workflow_id": WORKFLOW_PERSON_TALKING_HEAD_V1,
                        "create_mode": CREATE_MODE_PERSON_TALKING_HEAD,
                    },
                    "talking_head": talking_head,
                }
                rewrite_simple = payload.rewrite_simple_prompt.strip() or payload.simple_prompt.strip()
                storyboard_simple = payload.storyboard_simple_prompt.strip() or payload.simple_prompt.strip()
                rewrite_final = payload.rewrite_final_prompt.strip() or default_rewrite_prompt(script_text or task_title, payload)
                storyboard_final = payload.storyboard_final_prompt.strip() or default_storyboard_prompt(script_text or task_title, payload)
                config = talking_head_config_payload(
                    payload,
                    script_creation_mode=script_mode,
                    script_text=script_text,
                    rewrite_simple=rewrite_simple,
                    rewrite_final=rewrite_final,
                    storyboard_simple=storyboard_simple,
                    storyboard_final=storyboard_final,
                    quick_config=quick_config,
                    talking_head=talking_head,
                )
                task_id = repo.create_talking_head_task(
                    config_schema_version="talking_head_task_config_1.0",
                    script_creation_mode=script_mode,
                    config_json=json.dumps(config, ensure_ascii=False, sort_keys=True),
                    session_id=session_id,
                    status="draft",
                    workflow_mode=WORKFLOW_PERSON_TALKING_HEAD_V1,
                    reference_video_path="",
                    industry=payload.industry.strip(),
                    persona=payload.persona.strip(),
                    target_audience=payload.target_audience.strip(),
                    product_info=payload.product_info.strip(),
                    constraints=payload.constraints.strip(),
                    analysis_goal="人物口播",
                    video_formula=payload.video_formula.strip() or "人物口播",
                    simple_prompt=rewrite_simple,
                    final_prompt=rewrite_final,
                    rewrite_simple_prompt=rewrite_simple,
                    rewrite_final_prompt=rewrite_final,
                    storyboard_simple_prompt=storyboard_simple,
                    storyboard_final_prompt=storyboard_final,
                    storyboard_quick_config_json=json.dumps(quick_config, ensure_ascii=False, sort_keys=True),
                    prompt_model_provider=payload.prompt_model_provider.strip(),
                    prompt_model_id=payload.prompt_model_id.strip(),
                    run_model_provider=payload.run_model_provider.strip(),
                    run_model_id=payload.run_model_id.strip(),
                    created_at=created,
                    updated_at=now_ms(),
                )
                session_row = ctx.session_repo.get(session_id) or {"id": session_id, "workspace_dir": str(workspace), "title": task_title}
                op_session = create_opencode_session(session_row, task_title)
                opencode_session_id = str(op_session["id"])
                ctx.session_repo.update(session_id, opencode_session_id=opencode_session_id, updated_at=now_ms())
                add_event(session_id, "talking_head_v1.task.created", {"task_id": task_id, "workspace_dir": str(workspace), "script_creation_mode": script_mode, "portrait_image_path": portrait_rel})
            except Exception:
                rollback_talking_head_create(session_id, workspace, opencode_session_id)
                raise
            return {"ok": True, "task_id": task_id, "session_id": session_id, "workspace_dir": str(workspace), "status": "draft", "item": serialize_task(repo.get_task(task_id) or {"id": task_id, "session_id": session_id, "workspace_dir": str(workspace)})}

    @router.put("/api/koubo-tasks/{task_id}/talking-head")
    async def update_koubo_talking_head_task(
        task_id: int,
        title: str = Form(""),
        script: str = Form(""),
        script_creation_mode: str = Form("user_provided"),
        script_format: str = Form("plain"),
        industry: str = Form("医美"),
        persona: str = Form("强判断老板型"),
        target_audience: str = Form("老板"),
        video_formula: str = Form("人物口播"),
        product_info: str = Form(""),
        constraints: str = Form(""),
        rewrite_simple_prompt: str = Form(""),
        rewrite_final_prompt: str = Form(""),
        storyboard_simple_prompt: str = Form(""),
        storyboard_final_prompt: str = Form(""),
        storyboard_quick_config: str = Form(""),
        prompt_model_provider: str = Form(""),
        prompt_model_id: str = Form(""),
        run_model_provider: str = Form(""),
        run_model_id: str = Form(""),
        srt_target_seconds: float = Form(15.0),
        portrait_segments_per_image: int = Form(2),
        voice_provider: str = Form("heygen"),
        voice_id: str = Form(""),
        voice_label: str = Form(""),
        tempo: float = Form(1.0),
        voice_tempo_by_id: str = Form("{}"),
        talking_head_video_model_key: str = Form(""),
        talking_head_video_provider: str = Form(""),
        talking_head_video_model: str = Form(""),
        talking_head_video_model_alias: str = Form(""),
        use_default_reference_video: bool = Form(True),
        reference_privacy_mode: str = Form("red_grid_guide"),
        apply_privacy_grid_to_reference_video: bool = Form(True),
        apply_privacy_grid_to_target_identity_image: bool = Form(True),
        privacy_grid_preset: str = Form(DEFAULT_TALKING_HEAD_PRIVACY_GRID_PRESET),
        cell_size_reference: int = Form(12),
        line_width_reference: float = Form(1.0),
        portrait_image_file: UploadFile | None = File(default=None),
        reference_video_file: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        task = repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="OpenClip task not found")
        workspace = workspace_for(task)
        old_meta = read_json(workspace / TASK_META_REL) or {}
        old_config, _ = load_talking_head_config(task_id)
        if str(task.get("workflow_mode") or "") not in {"", "script", WORKFLOW_PERSON_TALKING_HEAD_V1} and not old_config:
            raise HTTPException(status_code=400, detail={"code": "workflow_mode_not_talking_head_v1", "message": f"Task #{task_id} is not a 人物口播 task."})
        quick_config_from_form = load_quick_config(storyboard_quick_config) if str(storyboard_quick_config or "").strip() else normalize_quick_config({
            "target_scene_seconds": 15.0,
            "target_shot_seconds": 15.0,
            "split_tolerance_seconds": 0.0,
            "language_boundary_mode": "strict",
        })
        srt_target_seconds = max(1.0, float(quick_config_from_form.get("target_shot_seconds") or srt_target_seconds or 8.0))
        old_talking_head = old_config.get("talking_head") if isinstance(old_config.get("talking_head"), dict) else (old_meta.get("talking_head") if isinstance(old_meta.get("talking_head"), dict) else {})
        old_video_model = old_talking_head.get("video_model") if isinstance(old_talking_head.get("video_model"), dict) else {}
        preserved_video_model_key = infer_talking_head_video_model_key(old_video_model)
        payload = KouboScriptTaskCreatePayload(
            title=title,
            script=script,
            script_creation_mode=script_creation_mode,
            script_format=script_format,
            industry=industry,
            persona=persona,
            target_audience=target_audience,
            video_formula=video_formula,
            product_info=product_info,
            constraints=constraints,
            analysis_goal="人物口播",
            rewrite_simple_prompt=rewrite_simple_prompt,
            rewrite_final_prompt=rewrite_final_prompt,
            storyboard_simple_prompt=storyboard_simple_prompt,
            storyboard_final_prompt=storyboard_final_prompt,
            prompt_model_provider=prompt_model_provider,
            prompt_model_id=prompt_model_id,
            run_model_provider=run_model_provider,
            run_model_id=run_model_id,
            storyboard_quick_config=quick_config_from_form,
            profile_id=WORKFLOW_PERSON_TALKING_HEAD_V1,
            create_mode=CREATE_MODE_PERSON_TALKING_HEAD,
            srt_target_seconds=srt_target_seconds,
            portrait_segments_per_image=portrait_segments_per_image,
            voice_provider=voice_provider,
            voice_id=voice_id,
            voice_label=voice_label,
            tempo=tempo,
            voice_tempo_by_id=load_raw_config(voice_tempo_by_id),
            talking_head_video_model_key=talking_head_video_model_key or preserved_video_model_key,
            talking_head_video_provider=talking_head_video_provider,
            talking_head_video_model=talking_head_video_model,
            talking_head_video_model_alias=talking_head_video_model_alias,
            use_default_reference_video=use_default_reference_video,
            reference_privacy_mode=reference_privacy_mode,
            apply_privacy_grid_to_reference_video=apply_privacy_grid_to_reference_video,
            apply_privacy_grid_to_target_identity_image=apply_privacy_grid_to_target_identity_image,
            privacy_grid_preset=privacy_grid_preset,
            cell_size_reference=cell_size_reference,
            line_width_reference=line_width_reference,
        )
        resolve_talking_head_voice(payload)
        validate_talking_head_voice(payload)
        selected_video_model = talking_head_video_model_config(payload)
        payload.srt_target_seconds = min(payload.srt_target_seconds, float(selected_video_model["max_duration_seconds"]))
        payload.storyboard_quick_config = {
            **payload.storyboard_quick_config,
            "target_scene_seconds": payload.srt_target_seconds,
            "target_shot_seconds": payload.srt_target_seconds,
            "split_tolerance_seconds": 0 if payload.srt_target_seconds >= float(selected_video_model["max_duration_seconds"]) else float(payload.storyboard_quick_config.get("split_tolerance_seconds", 1)),
        }
        script_text = payload.script.strip()
        script_mode = normalize_script_creation_mode(payload.script_creation_mode)
        validate_talking_head_script_input(script_mode, script_text)
        old_portrait = old_talking_head.get("portrait") if isinstance(old_talking_head.get("portrait"), dict) else {}
        old_portrait_rel = text_value(old_portrait.get("portrait_image_path"))
        old_reference_video = old_talking_head.get("reference_video") if isinstance(old_talking_head.get("reference_video"), dict) else {}
        old_reference_rel = text_value(old_reference_video.get("reference_video_path"))
        needs_reference_video = not payload.use_default_reference_video and payload.talking_head_video_model_key in {"max_2_7_w", "max_sd_2"}
        with tempfile.TemporaryDirectory(prefix="opencrew-talking-head-update-") as staging_dir:
            staging_root = Path(staging_dir)
            new_portrait_rel = await save_talking_head_portrait(staging_root, portrait_image_file)
            new_reference_rel = await save_talking_head_reference_video(staging_root, reference_video_file) if needs_reference_video and reference_video_file is not None else ""
            portrait_rel = new_portrait_rel or old_portrait_rel
            payload.reference_video_path = "" if not needs_reference_video else (new_reference_rel or old_reference_rel)
            talking_head = talking_head_meta_config(payload, portrait_rel, selected_video_model)
            quick_config = talking_head_quick_config(payload.storyboard_quick_config, payload.srt_target_seconds)
            quick_config = {
                **quick_config,
                "workflow_profile": {
                    "profile_id": WORKFLOW_PERSON_TALKING_HEAD_V1,
                    "workflow_id": WORKFLOW_PERSON_TALKING_HEAD_V1,
                    "create_mode": CREATE_MODE_PERSON_TALKING_HEAD,
                },
                "talking_head": talking_head,
            }
            rewrite_simple = payload.rewrite_simple_prompt.strip() or payload.simple_prompt.strip()
            storyboard_simple = payload.storyboard_simple_prompt.strip() or payload.simple_prompt.strip()
            title_seed = script_text or payload.product_info.strip() or rewrite_simple or storyboard_simple or payload.industry.strip()
            task_title = payload.title.strip() or str(old_meta.get("title") or task.get("title") or "").strip() or (split_plain_script(title_seed)[0][:32] if title_seed else "") or f"Task #{task_id}"
            rewrite_final = payload.rewrite_final_prompt.strip() or default_rewrite_prompt(script_text or task_title, payload)
            storyboard_final = payload.storyboard_final_prompt.strip() or default_storyboard_prompt(script_text or task_title, payload)
            updated = now_ms()
            try:
                ctx.session_repo.update(int(task["session_id"]), title=task_title, updated_at=updated)
            except Exception:
                pass
            old_script_input = old_config.get("script_input") if isinstance(old_config.get("script_input"), dict) else {}
            existing_script = text_value(old_script_input.get("user_script_text") or old_script_input.get("reference_script_text"))
            script_changed = normalized_script_for_change(script_text) != normalized_script_for_change(existing_script)
            config = talking_head_config_payload(
                payload,
                script_creation_mode=script_mode,
                script_text=script_text,
                rewrite_simple=rewrite_simple,
                rewrite_final=rewrite_final,
                storyboard_simple=storyboard_simple,
                storyboard_final=storyboard_final,
                quick_config=quick_config,
                talking_head=talking_head,
            )
            current_config_row = repo.get_talking_head_config(task_id) or {}
            installed_portrait_rel = ""
            installed_reference_rel = ""
            try:
                installed_portrait_rel = install_staged_upload(staging_root, workspace, new_portrait_rel)
                installed_reference_rel = install_staged_upload(staging_root, workspace, new_reference_rel)
                repo.update_talking_head_task(
                    task_id,
                    config_schema_version="talking_head_task_config_1.0",
                    script_creation_mode=script_mode,
                    config_json=json.dumps(config, ensure_ascii=False, sort_keys=True),
                    config_created_at=int(current_config_row.get("created_at") or updated),
                    config_updated_at=updated,
                    status="draft",
                    workflow_mode=WORKFLOW_PERSON_TALKING_HEAD_V1,
                    reference_video_path="",
                    industry=payload.industry.strip(),
                    persona=payload.persona.strip(),
                    target_audience=payload.target_audience.strip(),
                    product_info=payload.product_info.strip(),
                    constraints=payload.constraints.strip(),
                    analysis_goal="人物口播",
                    video_formula=payload.video_formula.strip() or "人物口播",
                    simple_prompt=rewrite_simple,
                    final_prompt=rewrite_final,
                    rewrite_simple_prompt=rewrite_simple,
                    rewrite_final_prompt=rewrite_final,
                    storyboard_simple_prompt=storyboard_simple,
                    storyboard_final_prompt=storyboard_final,
                    storyboard_quick_config_json=json.dumps(quick_config, ensure_ascii=False, sort_keys=True),
                    prompt_model_provider=payload.prompt_model_provider.strip(),
                    prompt_model_id=payload.prompt_model_id.strip(),
                    run_model_provider=payload.run_model_provider.strip(),
                    run_model_id=payload.run_model_id.strip(),
                    updated_at=updated,
                )
            except Exception:
                safe_unlink_upload(workspace, installed_portrait_rel, TALKING_HEAD_PORTRAIT_DIR_REL)
                safe_unlink_upload(workspace, installed_reference_rel, TALKING_HEAD_REFERENCE_VIDEO_DIR_REL)
                raise
        if new_portrait_rel and old_portrait_rel != new_portrait_rel:
            safe_unlink_upload(workspace, old_portrait_rel, TALKING_HEAD_PORTRAIT_DIR_REL)
        if old_reference_rel and old_reference_rel != payload.reference_video_path:
            safe_unlink_upload(workspace, old_reference_rel, TALKING_HEAD_REFERENCE_VIDEO_DIR_REL)
        safe_unlink(workspace / VARIABLES_REL)
        if script_changed:
            safe_unlink(workspace / SOURCE_SCRIPT_REL)
            safe_unlink(workspace / FINAL_SRT_ITEMS_REL)
            safe_unlink(workspace / REWRITTEN_SRT_ITEMS_REL)
            safe_unlink(workspace / STORYBOARD_REL)
            safe_unlink(workspace / STORYBOARD_EDIT_REL)
        add_event(int(task["session_id"]), "talking_head_v1.task.updated", {"task_id": task_id, "workspace_dir": str(workspace), "script_creation_mode": script_mode, "portrait_image_path": portrait_rel, "variables_invalidated": True})
        return {"ok": True, "task_id": task_id, "session_id": int(task["session_id"]), "workspace_dir": str(workspace), "status": "draft", "item": serialize_task(repo.get_task(task_id) or task)}

    @router.put("/api/koubo-tasks/{task_id}/script")
    async def update_koubo_script_task(task_id: int, payload: KouboScriptTaskCreatePayload) -> dict[str, Any]:
        task = repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="OpenClip task not found")
        workspace = workspace_for(task)
        existing_meta = read_json(workspace / TASK_META_REL) or {}
        if (
            str(task.get("workflow_mode") or "") == WORKFLOW_PERSON_TALKING_HEAD_V1
            or bool(repo.get_talking_head_config(task_id))
            or str(existing_meta.get("profile_id") or "") == WORKFLOW_PERSON_TALKING_HEAD_V1
            or str(existing_meta.get("workflow_id") or "") == WORKFLOW_PERSON_TALKING_HEAD_V1
            or str(existing_meta.get("display_source_label") or "") == "人物口播"
        ):
            raise HTTPException(status_code=400, detail={"code": "use_talking_head_update", "message": "人物口播任务必须使用人物口播保存接口。"})
        script = payload.script.strip()
        quick_config = normalize_quick_config(payload.storyboard_quick_config)
        rewrite_simple = payload.rewrite_simple_prompt.strip() or payload.simple_prompt.strip()
        storyboard_simple = payload.storyboard_simple_prompt.strip() or payload.simple_prompt.strip()
        title_seed = script or payload.product_info.strip() or rewrite_simple or storyboard_simple or payload.industry.strip()
        title = payload.title.strip() or str(task.get("title") or "").strip() or (split_plain_script(title_seed)[0][:32] if title_seed else "") or f"Task #{task_id}"
        rewrite_final = payload.rewrite_final_prompt.strip() or default_rewrite_prompt(script or title, payload)
        storyboard_final = payload.storyboard_final_prompt.strip() or default_storyboard_prompt(script or title, payload)
        updated = now_ms()
        repo.update_task(
            task_id,
            status="draft",
            industry=payload.industry.strip(),
            persona=payload.persona.strip(),
            target_audience=payload.target_audience.strip(),
            product_info=payload.product_info.strip(),
            constraints=payload.constraints.strip(),
            analysis_goal=payload.analysis_goal.strip() or "脚本生成口播任务",
            video_formula=payload.video_formula.strip() or "脚本口播",
            simple_prompt=rewrite_simple,
            final_prompt=rewrite_final,
            rewrite_simple_prompt=rewrite_simple,
            rewrite_final_prompt=rewrite_final,
            storyboard_simple_prompt=storyboard_simple,
            storyboard_final_prompt=storyboard_final,
            storyboard_quick_config_json=json.dumps(quick_config, ensure_ascii=False, sort_keys=True),
            prompt_model_provider=payload.prompt_model_provider.strip(),
            prompt_model_id=payload.prompt_model_id.strip(),
            run_model_provider=payload.run_model_provider.strip(),
            run_model_id=payload.run_model_id.strip(),
            updated_at=updated,
        )
        meta = read_json(workspace / TASK_META_REL) or {}
        existing_script = ""
        source_script_path = workspace / SOURCE_SCRIPT_REL
        if source_script_path.exists():
            existing_script = source_script_path.read_text(encoding="utf-8", errors="ignore")
        script_changed = normalized_script_for_change(script) != normalized_script_for_change(existing_script)
        if script:
            srt_payload = script_to_srt_items(script, payload.script_format)
            write_text(workspace / SOURCE_SCRIPT_REL, script)
            write_json(workspace / FINAL_SRT_ITEMS_REL, srt_payload)
            if script_changed:
                safe_unlink(workspace / REWRITTEN_SRT_ITEMS_REL)
                safe_unlink(workspace / STORYBOARD_REL)
                safe_unlink(workspace / STORYBOARD_EDIT_REL)
            meta.update({
                "input_mode": "script_only",
                "source_script_path": SOURCE_SCRIPT_REL,
                "source_srt_items_path": FINAL_SRT_ITEMS_REL,
                "script_preview": script[:160],
                "initialize_status": "waiting_input",
            })
        meta.update({
            "schema_version": "koubo_task_list_meta_0.1",
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "title": title,
            "create_mode": "script",
            "updated_at": updated,
        })
        write_json(workspace / TASK_META_REL, meta)
        add_event(int(task["session_id"]), "koubo_task.script_updated", {"task_id": task_id, "workspace_dir": str(workspace), "script_updated": bool(script)})
        return {"ok": True, "task_id": task_id, "session_id": int(task["session_id"]), "workspace_dir": str(workspace), "status": "waiting_input" if script else "draft", "item": serialize_task(repo.get_task(task_id) or task)}

    @router.get("/api/talking-head-v1/tasks/{task_id}")
    async def get_talking_head_task_detail(task_id: int) -> dict[str, Any]:
        task = repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="OpenClip task not found")
        workspace = workspace_for(task)
        meta = read_json(workspace / TASK_META_REL) or {}
        talking_head_config, script_creation_mode = load_talking_head_config(task_id)
        if str(task.get("workflow_mode") or "") != WORKFLOW_PERSON_TALKING_HEAD_V1 and not talking_head_config and str(meta.get("create_mode") or "") != CREATE_MODE_PERSON_TALKING_HEAD:
            raise HTTPException(status_code=400, detail={"code": "workflow_mode_not_talking_head_v1", "message": f"Task #{task_id} is not a 人物口播 task."})
        srt_payload = read_json(workspace / FINAL_SRT_ITEMS_REL) or {}
        rewritten = read_json(workspace / REWRITTEN_SRT_ITEMS_REL) or {}
        # The task config is the canonical reference-script source for AI
        # rewrite tasks. Workspace SRT files can still belong to an older mode
        # or run, so do not bind those stale lines to the current reference.
        if script_creation_mode == "ai_rewrite":
            persisted_srt_payload = srt_payload
            script_input = talking_head_config.get("script_input") if isinstance(talking_head_config.get("script_input"), dict) else {}
            reference_script = text_value(script_input.get("reference_script_text"))
            if reference_script:
                script_format = text_value(script_input.get("script_format")) or "plain"
                segment_planning = ((talking_head_config.get("talking_head") or {}).get("segment_planning") or {})
                target_seconds = float(segment_planning.get("srt_target_seconds") or 15)
                srt_payload = script_to_srt_items(reference_script, script_format, srt_target_seconds=target_seconds)
                persisted_items = [item for item in (persisted_srt_payload.get("items") or []) if isinstance(item, dict)]
                persisted_by_id = {
                    text_value(item.get("srt_id") or item.get("id")): item
                    for item in persisted_items
                    if text_value(item.get("srt_id") or item.get("id"))
                }
                for index, item in enumerate(srt_payload.get("items") or []):
                    if not isinstance(item, dict):
                        continue
                    srt_id = text_value(item.get("srt_id") or item.get("id"))
                    timing = persisted_by_id.get(srt_id) or (persisted_items[index] if index < len(persisted_items) else {})
                    if not timing:
                        continue
                    for key in ("start", "end", "duration", "estimated_duration", "timing_source", "voice_timing_estimate"):
                        if key in timing:
                            item[key] = timing[key]
        storyboard = read_json(workspace / STORYBOARD_REL) or {}
        storyboard_items: list[dict[str, Any]] = []
        if isinstance(storyboard, dict):
            for shot in storyboard.get("shots") or []:
                if not isinstance(shot, dict):
                    continue
                for scene in shot.get("scenes") or []:
                    if not isinstance(scene, dict):
                        continue
                    for dialogue in scene.get("dialogue_items") or scene.get("dialogues") or []:
                        if not isinstance(dialogue, dict):
                            continue
                        storyboard_items.append({
                            "index": len(storyboard_items) + 1,
                            "srt_id": text_value(dialogue.get("srt_id") or dialogue.get("dialogue_id")),
                            "dialogue_id": text_value(dialogue.get("dialogue_id")),
                            "dialogue_asset_key": text_value(dialogue.get("dialogue_asset_key")),
                            "text": text_value(dialogue.get("dialogue") or dialogue.get("text")),
                            "start": dialogue.get("start") or 0,
                            "end": dialogue.get("end") or 0,
                            "duration": dialogue.get("duration") or 0,
                            "first_frame_source": text_value(dialogue.get("image_path") or ""),
                        })
        source_items = [item for item in (srt_payload.get("items") or []) if isinstance(item, dict)]
        rewritten_items = [item for item in (rewritten.get("items") or []) if isinstance(item, dict)]
        rewritten_by_id = {
            text_value(item.get("srt_id")): item
            for item in rewritten_items
            if text_value(item.get("srt_id"))
        }
        display_items = source_items or rewritten_items
        srt_items = []
        for item in display_items:
            if not isinstance(item, dict):
                continue
            srt_id = text_value(item.get("srt_id") or f"srt_{len(srt_items) + 1:04d}")
            rewritten_item = rewritten_by_id.get(srt_id) or (rewritten_items[len(srt_items)] if len(srt_items) < len(rewritten_items) else {})
            original_text = text_value(item.get("dialogue") or item.get("text")) if source_items else ""
            rewritten_text = text_value(rewritten_item.get("dialogue") or rewritten_item.get("text")) if rewritten_item else ""
            visible_item = rewritten_item or item
            srt_items.append({
                "index": int(visible_item.get("index") or item.get("index") or len(srt_items) + 1),
                "srt_id": srt_id,
                "text": rewritten_text or original_text,
                "original_text": original_text,
                "rewritten_text": rewritten_text,
                "start": visible_item.get("start") or item.get("start") or 0,
                "end": visible_item.get("end") or item.get("end") or 0,
                "duration": visible_item.get("duration") or item.get("duration") or 0,
                "image_path": text_value(visible_item.get("image_path") or item.get("image_path")),
                "warnings": visible_item.get("warnings") or item.get("warnings") or [],
            })
        return {
            "ok": True,
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "workflow_id": WORKFLOW_PERSON_TALKING_HEAD_V1,
            "create_mode": CREATE_MODE_PERSON_TALKING_HEAD,
            "task": serialize_task(task),
            "meta": meta,
            "script_creation_mode": script_creation_mode,
            "talking_head": talking_head_config.get("talking_head") if isinstance(talking_head_config.get("talking_head"), dict) else (meta.get("talking_head") if isinstance(meta.get("talking_head"), dict) else {}),
            "workspace_dir": str(workspace),
            "srt": {"exists": bool((workspace / FINAL_SRT_ITEMS_REL).is_file()), "path": FINAL_SRT_ITEMS_REL, "items": srt_items, "item_count": len(srt_items)},
            "storyboard": {"exists": bool((workspace / STORYBOARD_REL).is_file()), "configured": bool(storyboard.get("talking_head_configured")) if isinstance(storyboard, dict) else False, "path": STORYBOARD_REL, "items": storyboard_items, "item_count": len(storyboard_items)},
            "storyboard_url": f"#/koubo-storyboard/tasks/{task_id}",
        }

    @router.delete("/api/koubo-tasks/{task_id}/delete")
    async def delete_koubo_task(task_id: int) -> dict[str, Any]:
        task = repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="OpenClip task not found")
        session_id = int(task["session_id"])
        workspace = workspace_for(task)
        session_row = ctx.session_repo.get(session_id) or {"id": session_id, "workspace_dir": str(workspace)}
        ctx.workflow_deletion_service.delete_session_db_first(session_row)
        try:
            ctx.workflow_deletion_service.cleanup_workspace(session_row)
        except Exception as exc:
            ctx.event("warning", "cleanup", "Workspace cleanup failed after Koubo task DB deletion", {"session_id": session_id, "task_id": task_id, "error": str(exc)})
        return {"ok": True, "deleted_id": task_id, "deleted_session_id": session_id}

    @router.delete("/api/koubo-tasks/{task_id}")
    async def archive_koubo_task(task_id: int) -> dict[str, Any]:
        task = repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="OpenClip task not found")
        workspace = workspace_for(task)
        meta = read_json(workspace / TASK_META_REL) or {}
        archived_at = now_ms()
        meta.update({"archived_at": archived_at, "updated_at": archived_at})
        write_json(workspace / TASK_META_REL, meta)
        add_event(int(task["session_id"]), "koubo_task.archived", {"task_id": task_id, "workspace_dir": str(workspace)})
        return {"ok": True, "task_id": task_id, "archived_at": archived_at}

    return router
