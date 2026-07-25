from __future__ import annotations

import copy
import asyncio
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from opcrew_backend.adapters.opencode import OpenCodeSessionClient
from opcrew_backend.context import AppContext, now_ms

from .rebuild_repository import OCRebuildRepository
from .repository import OpenClipRepository


OC_STORYBOARD_SOURCE = "oc-storyboard"
OC_STORYBOARD_GROUP_ID = "oc-storyboard"
OC_REBUILD_SOURCE = "oc-rebuild"
OC_REBUILD_GROUP_ID = "oc-rebuild"

STORYBOARD_WORKSPACE_DIRS = {
    "Assets",
    "asset_image_workflows",
    "asset_tts_workflows",
    "asset_video_workflows",
    "consistency_references",
    "final_prompt_packages",
    "keyframes",
    "plan_c",
    "renders",
    "reports",
    "schemes",
    "tts",
    "uploads",
}

STORYBOARD_WORKSPACE_FILE_DENYLIST = {
    ".DS_Store",
    "storyboard_dialogue_plan.json",
    "storyboard_meta.json",
    "storyboard_snapshot.json",
}

STORYBOARD_DIALOGUE_PLAN_FILE = "storyboard_dialogue_plan.json"


def build_oc_storyboard_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    repo = OCRebuildRepository(ctx.engine)
    analysis_repo = OpenClipRepository(ctx.engine)

    def safe_session(session_id: int) -> dict[str, Any]:
        row = ctx.session_repo.get(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        return row

    def get_task(task_id: int) -> dict[str, Any]:
        row = repo.get_task(task_id)
        if not row:
            raise HTTPException(status_code=404, detail="OC-Rebuild task not found")
        return row

    def get_analysis_session_id(analysis_task_id: int | None) -> int | None:
        if not analysis_task_id:
            return None
        row = analysis_repo.get_task(int(analysis_task_id))
        if not row:
            return None
        try:
            return int(row.get("session_id") or 0) or None
        except (TypeError, ValueError):
            return None

    def opencode_client_for(session_row: dict[str, Any]) -> OpenCodeSessionClient:
        base_url = str(ctx.get_setting("opencode.base_url") or "").strip()
        username = str(ctx.get_setting("opencode.username") or "").strip()
        password = str(ctx.get_setting("opencode.password") or "").strip()
        if not base_url or not username or not password:
            raise HTTPException(status_code=400, detail="OpenCode connection is incomplete. Finish Step 1 before using StoryBoard.")
        return OpenCodeSessionClient(base_url=base_url, username=username, password=password, directory=str(session_row["workspace_dir"]))

    def read_json_file(path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write_json_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return payload

    def read_storyboard_edit_plan(workspace: Path) -> dict[str, Any]:
        return read_json_file(workspace / STORYBOARD_DIALOGUE_PLAN_FILE) or read_json_file(workspace / "rebuild_shot_plan.json")

    def clean_dialogue_text(value: Any) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"(^|\n)\s*\d+\s+(?=\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->)", r"\1", text)
        text = re.sub(r"\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}", " ", text)
        lines = [line.strip() for line in text.split("\n")]
        lines = [line for line in lines if line and not re.fullmatch(r"\d+", line)]
        return re.sub(r"\s+", " ", " ".join(lines)).strip()

    def add_event(session_id: int, kind: str, payload: dict[str, Any]) -> None:
        ctx.session_event_service.add_event(session_id, kind, payload, workflow_id="oc_storyboard")

    def workspace_path(task_row: dict[str, Any]) -> Path:
        value = str(task_row.get("workspace_dir") or "").strip()
        if value:
            return Path(value)
        return Path(str(safe_session(int(task_row["session_id"]))["workspace_dir"]))

    def rel_path(workspace: Path, path: Path) -> str:
        return str(path.resolve().relative_to(workspace.resolve()))

    def safe_upload_name(filename: str, fallback: str) -> str:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".png"
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(filename or fallback).stem).strip("_") or fallback
        return f"{stem[:60]}_{now_ms()}_{uuid.uuid4().hex[:6]}{suffix}"

    def is_supported_image_upload(upload: UploadFile) -> bool:
        suffix = Path(upload.filename or "").suffix.lower()
        content_type = str(upload.content_type or "").lower()
        return suffix in {".png", ".jpg", ".jpeg", ".webp"} or content_type in {"image/png", "image/jpeg", "image/webp"}

    def create_rebuild_session(title_prefix: str) -> tuple[int, Path]:
        created = now_ms()
        session_id = ctx.session_repo.create(
            source=OC_REBUILD_SOURCE,
            group_id=OC_REBUILD_GROUP_ID,
            sender_name="OC-StoryBoard",
            title=f"{title_prefix} {ctx.next_session_title()}",
            command_text="",
            status="queued",
            workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"),
            share_token=ctx.new_share_token(),
            created_at=created,
            updated_at=created,
        )
        workspace = ctx.workspace_store.create_session_workspace(session_id)
        ctx.session_repo.update(session_id, workspace_dir=str(workspace), updated_at=created)
        session_row = safe_session(session_id)
        op_session = opencode_client_for(session_row).create_session(str(session_row["title"]))
        ctx.session_repo.update(session_id, opencode_session_id=str(op_session["id"]), status="draft", updated_at=now_ms())
        return session_id, workspace

    def copy_rebuild_task(source_task: dict[str, Any], session_id: int, status: str) -> int:
        created = now_ms()
        fields: dict[str, Any] = {}
        for key in (
            "analysis_task_id",
            "source_package_path",
            "source_scheme",
            "target_topic",
            "target_platform",
            "aspect_ratio",
            "target_count",
            "target_audience",
            "product_info",
            "rebuild_goal",
            "preserve_strategy_json",
            "replace_strategy_json",
            "visual_style",
            "subtitle_style",
            "title_style",
            "voice_style",
            "batch_variables",
            "constraints",
            "simple_prompt",
            "final_prompt",
            "prompt_model_provider",
            "prompt_model_id",
            "run_model_provider",
            "run_model_id",
        ):
            fields[key] = source_task.get(key)
        return repo.create_task(session_id=session_id, status=status, current_version_id=None, latest_attempt_id=None, workflow_mode="storyboard", created_at=created, updated_at=created, **fields)

    def create_blank_rebuild_task(session_id: int) -> int:
        created = now_ms()
        return repo.create_task(
            session_id=session_id,
            analysis_task_id=None,
            status="storyboard_editing",
            source_package_path="source_package.json",
            source_scheme="storyboard",
            target_topic="",
            target_platform="抖音",
            aspect_ratio="9:16",
            target_count=1,
            target_audience="",
            product_info="",
            rebuild_goal="从 StoryBoard 上传素材创建 Rebuild Session",
            preserve_strategy_json=json.dumps({"duration_pattern": True, "subtitle_timing": True}, ensure_ascii=False),
            replace_strategy_json=json.dumps({"visuals": True, "voiceover": True, "subtitles": True}, ensure_ascii=False),
            visual_style="真实口播",
            subtitle_style="底部大字",
            title_style="顶部强钩子",
            voice_style="年轻中文旁白",
            batch_variables="",
            constraints="",
            simple_prompt="",
            final_prompt="",
            current_version_id=None,
            latest_attempt_id=None,
            prompt_model_provider="",
            prompt_model_id="",
            run_model_provider="",
            run_model_id="",
            workflow_mode="storyboard",
            created_at=created,
            updated_at=created,
        )

    def copy_tree_missing(source: Path, target: Path, overwrite: bool = False) -> None:
        if not source.exists() or not source.is_dir():
            return
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            if child.name == ".DS_Store":
                continue
            dst = target / child.name
            if child.is_dir():
                copy_tree_missing(child, dst, overwrite=overwrite)
            elif overwrite or not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, dst)

    def copy_reference_workspace(source: Path, target: Path, overwrite: bool = False) -> None:
        if not source.exists() or not source.is_dir():
            return
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            if child.name in STORYBOARD_WORKSPACE_FILE_DENYLIST:
                continue
            dst = target / child.name
            if child.is_dir():
                if child.name in STORYBOARD_WORKSPACE_DIRS:
                    copy_tree_missing(child, dst, overwrite=overwrite)
            elif child.is_file() and (overwrite or not dst.exists()):
                shutil.copy2(child, dst)

    def backfill_storyboard_copy_workspace(workspace: Path, meta: dict[str, Any]) -> None:
        phase2 = meta.get("phase2_refresh") if isinstance(meta.get("phase2_refresh"), dict) else {}
        if phase2.get("suppress_plan_d_prompt_autobuild") or phase2.get("boundary") == "storyboard_to_reference_ready_phase2":
            return
        source_session_id = int(meta.get("copied_from_rebuild_session_id") or 0)
        if not source_session_id:
            return
        source_workspace = Path(str(safe_session(source_session_id).get("workspace_dir") or ""))
        if not source_workspace.exists():
            return
        copy_reference_workspace(source_workspace, workspace, overwrite=False)

    def shot_keyframe_paths(reference: dict[str, Any]) -> list[str]:
        frames = reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []
        paths: list[str] = []
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            path = str(frame.get("path") or "").strip()
            if path and path not in paths:
                paths.append(path)
        return paths

    def indexed_shot_keyframe(reference: dict[str, Any], scene_index: int, scene_count: int) -> str:
        paths = shot_keyframe_paths(reference)
        if not paths:
            return ""
        if scene_count <= 1 or len(paths) == 1:
            return paths[0]
        ratio = max(0.0, min(1.0, (scene_index - 1) / max(1, scene_count - 1)))
        return paths[min(len(paths) - 1, round(ratio * (len(paths) - 1)))]

    def primary_scene_image(mark: dict[str, Any]) -> str:
        plan_d = mark.get("plan_d") if isinstance(mark.get("plan_d"), dict) else {}
        replacement = plan_d.get("replacement_first_frame") if isinstance(plan_d.get("replacement_first_frame"), dict) else {}
        plan_a = mark.get("plan_a") if isinstance(mark.get("plan_a"), dict) else {}
        scene_asset = plan_a.get("scene_asset") if isinstance(plan_a.get("scene_asset"), dict) else {}
        keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
        for value in (
            replacement.get("selected_image"),
            scene_asset.get("selected_image"),
            keyframes.get("single"),
            keyframes.get("first"),
            paths[0] if paths else "",
            keyframes.get("last"),
            paths[-1] if paths else "",
        ):
            path = str(value or "").strip()
            if path:
                return path
        return ""

    def scene_reference_image(mark: dict[str, Any]) -> str:
        keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
        for value in (
            keyframes.get("single"),
            keyframes.get("first"),
            paths[0] if paths else "",
            keyframes.get("last"),
            paths[-1] if paths else "",
        ):
            path = str(value or "").strip()
            if path:
                return path
        plan_a = mark.get("plan_a") if isinstance(mark.get("plan_a"), dict) else {}
        scene_asset = plan_a.get("scene_asset") if isinstance(plan_a.get("scene_asset"), dict) else {}
        source = str(scene_asset.get("source") or "").lower()
        selected = str(scene_asset.get("selected_image") or "").strip()
        if selected and "storyboard" in source:
            return selected
        return ""

    def ensure_scene_primary_image(mark: dict[str, Any], reference: dict[str, Any], scene_index: int, scene_count: int) -> None:
        if primary_scene_image(mark):
            return
        fallback = indexed_shot_keyframe(reference, scene_index, scene_count)
        if not fallback:
            return
        keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
        next_paths = [path for path in [fallback, *paths] if path]
        mark["keyframes"] = {
            **keyframes,
            "single": keyframes.get("single") or fallback,
            "first": keyframes.get("first") or fallback,
            "last": keyframes.get("last") or "",
            "paths": list(dict.fromkeys(next_paths)),
        }
        mark["image_fallback_source"] = "shot_keyframe"

    def normalize_scene_marks(shot: dict[str, Any], allow_image_fallback: bool = True) -> list[dict[str, Any]]:
        reference = shot.setdefault("reference", {})
        marks = reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else []
        if marks:
            normalized = []
            raw_marks = [item for item in marks if isinstance(item, dict)]
            scene_count = len(raw_marks) or 1
            for index, mark in enumerate(raw_marks, start=1):
                next_mark = copy.deepcopy(mark)
                next_mark["shot_id"] = str(shot.get("shot_id") or next_mark.get("shot_id") or f"shot_{index:03d}")
                next_mark["scene_index"] = index
                next_mark.setdefault("scene_mark_id", f"{next_mark['shot_id']}_scene_{index:03d}")
                next_mark.setdefault("duration", max(0.0, float(next_mark.get("end") or 0) - float(next_mark.get("start") or 0)))
                raw_text = next_mark.get("srt_text") or reference.get("srt_text") or reference.get("source_srt_text") or reference.get("original_srt_text") or ""
                next_mark["srt_text"] = clean_dialogue_text(raw_text)
                next_mark["source_srt_text"] = clean_dialogue_text(next_mark.get("source_srt_text") or raw_text)
                next_mark["original_srt_text"] = clean_dialogue_text(next_mark.get("original_srt_text") or raw_text)
                if allow_image_fallback:
                    ensure_scene_primary_image(next_mark, reference, index, scene_count)
                normalized.append(next_mark)
            reference["scene_marks"] = normalized
            return normalized
        shot_id = str(shot.get("shot_id") or "shot_001")
        duration = float(shot.get("duration") or reference.get("duration") or 0)
        keyframes = reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []
        first = str((keyframes[0] or {}).get("path") or "") if keyframes else ""
        last = str((keyframes[-1] or {}).get("path") or first) if keyframes else first
        scene = {
            "scene_mark_id": f"{shot_id}_scene_001",
            "shot_id": shot_id,
            "scene_index": 1,
            "mode": "first_last" if first and last and first != last else "single",
            "generation_mode": "first_last" if first and last and first != last else "first_frame",
            "start": float(shot.get("start") or reference.get("start") or 0),
            "end": float(shot.get("end") or reference.get("end") or duration),
            "duration": duration,
            "boundary_source": "storyboard_default_scene",
            "keyframes": {"single": first, "first": first, "last": last, "paths": [path for path in [first, last] if path]},
            "srt_text": clean_dialogue_text(reference.get("srt_text") or reference.get("source_srt_text") or reference.get("original_srt_text") or ""),
            "source_srt_text": clean_dialogue_text(reference.get("source_srt_text") or reference.get("original_srt_text") or ""),
            "original_srt_text": clean_dialogue_text(reference.get("original_srt_text") or reference.get("source_srt_text") or ""),
            "plan_a": {"scene_confirmed": bool(first), "first_last_confirmed": bool(first)},
        }
        reference["scene_marks"] = [scene]
        return [scene]

    def best_scene_image(mark: dict[str, Any]) -> tuple[str, str]:
        plan_d = mark.get("plan_d") if isinstance(mark.get("plan_d"), dict) else {}
        replacement = plan_d.get("replacement_first_frame") if isinstance(plan_d.get("replacement_first_frame"), dict) else {}
        plan_a = mark.get("plan_a") if isinstance(mark.get("plan_a"), dict) else {}
        scene_asset = plan_a.get("scene_asset") if isinstance(plan_a.get("scene_asset"), dict) else {}
        scene_asset_source = str(scene_asset.get("source") or "").lower()
        scene_asset_selected = str(scene_asset.get("selected_image") or "").strip()
        generated = str(replacement.get("selected_image") or (""
            if "storyboard" in scene_asset_source or "reference" in scene_asset_source
            else scene_asset_selected
        )).strip()
        if generated:
            return generated, "generated" if generated.startswith(("Assets/", "uploads/")) else "original"
        original = scene_reference_image(mark)
        return original, "original"

    def is_canonical_final_scene_image(path: str, shot_id: str, scene_id: str) -> bool:
        value = str(path or "").strip()
        return value in {
            f"Assets/variant_001/{shot_id}/{scene_id}/first.png",
            f"Assets/variant_001/{shot_id}/{scene_id}/last.png",
            f"assets/variant_001/{shot_id}/{scene_id}/first.png",
            f"assets/variant_001/{shot_id}/{scene_id}/last.png",
        }

    def is_variant_scene_image(path: str) -> bool:
        return bool(re.match(r"^(Assets|assets)/variant_[^/]+/[^/]+/[^/]+/(first|last)\.(png|jpg|jpeg|webp)$", str(path or "").strip(), re.IGNORECASE))

    def storyboard_source_workspace(meta: dict[str, Any]) -> Path | None:
        source_session_id = int(meta.get("copied_from_rebuild_session_id") or 0)
        if not source_session_id:
            return None
        try:
            source_workspace = Path(str(safe_session(source_session_id).get("workspace_dir") or ""))
        except Exception:
            return None
        return source_workspace if source_workspace.exists() else None

    def reference_only_storyboard_image(workspace: Path, source_workspace: Path | None, shot: dict[str, Any], mark: dict[str, Any], image_path: str) -> str:
        shot_id = str(shot.get("shot_id") or mark.get("shot_id") or "").strip()
        scene_id = str(mark.get("scene_mark_id") or "").strip()
        value = str(image_path or "").strip()
        if value.startswith("uploads/storyboard_references/"):
            cached = workspace / value
            return value if cached.exists() and cached.is_file() else ""
        if not shot_id or not scene_id or not (is_canonical_final_scene_image(value, shot_id, scene_id) or is_variant_scene_image(value)):
            return image_path
        source_path = workspace / value
        if not source_path.exists() or not source_path.is_file():
            source_path = (source_workspace / value) if source_workspace else source_path
        if not source_path.exists() or not source_path.is_file():
            return ""
        suffix = source_path.suffix or ".png"
        role = "last" if source_path.stem == "last" else "reference"
        target_rel = f"uploads/storyboard_references/{shot_id}/{scene_id}/{role}{suffix}"
        target_path = workspace / target_rel
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.resolve() != target_path.resolve():
            shutil.copy2(source_path, target_path)
        return target_rel

    def normalize_scene_reference_image(workspace: Path, source_workspace: Path | None, shot: dict[str, Any], mark: dict[str, Any]) -> None:
        shot_id = str(shot.get("shot_id") or mark.get("shot_id") or "").strip()
        scene_id = str(mark.get("scene_mark_id") or "").strip()
        keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        dialogue_asset = mark.get("storyboard_dialogue_asset") if isinstance(mark.get("storyboard_dialogue_asset"), dict) else {}
        raw_dialogue_asset_path = str(dialogue_asset.get("path") or "").strip()
        dialogue_asset_path = ""
        if raw_dialogue_asset_path:
            dialogue_asset_path = reference_only_storyboard_image(workspace, source_workspace, shot, mark, raw_dialogue_asset_path)
            if not dialogue_asset_path and dialogue_asset.get("original_path"):
                dialogue_asset_path = reference_only_storyboard_image(workspace, source_workspace, shot, mark, str(dialogue_asset.get("original_path") or ""))
            if dialogue_asset_path:
                mark["storyboard_dialogue_asset"] = {**dialogue_asset, "path": dialogue_asset_path, "original_path": dialogue_asset.get("original_path") or raw_dialogue_asset_path, "source": "storyboard_reference_image"}
        existing_paths = [str(item or "").strip() for item in (keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []) if str(item or "").strip()]
        raw_first = str(keyframes.get("first") or keyframes.get("single") or (existing_paths[0] if existing_paths else "") or "").strip()
        raw_last = str(keyframes.get("last") or (existing_paths[-1] if existing_paths else "") or "").strip()
        first = reference_only_storyboard_image(workspace, source_workspace, shot, mark, raw_first) if raw_first else ""
        if not first and dialogue_asset_path:
            first = dialogue_asset_path
        last = reference_only_storyboard_image(workspace, source_workspace, shot, mark, raw_last) if raw_last else ""
        extra_paths = [
            value
            for value in existing_paths
            if value and not is_canonical_final_scene_image(value, shot_id, scene_id) and value not in {raw_first, raw_last}
        ]
        if first or last or raw_first or raw_last:
            paths = list(dict.fromkeys([value for value in [first, last, *extra_paths] if value]))
            mark["keyframes"] = {
                **keyframes,
                "single": first if not last or first == last else "",
                "first": first,
                "last": "" if first == last else last,
                "paths": paths,
            }
        plan_a = mark.get("plan_a") if isinstance(mark.get("plan_a"), dict) else {}
        scene_asset = plan_a.get("scene_asset") if isinstance(plan_a.get("scene_asset"), dict) else {}
        for key in ("selected_image", "manifest", "generated_at", "provider", "model"):
            scene_asset.pop(key, None)
        if scene_asset:
            scene_asset["source"] = "storyboard_reference_image"
            plan_a["scene_asset"] = scene_asset
        else:
            plan_a.pop("scene_asset", None)
        if plan_a:
            mark["plan_a"] = plan_a
        else:
            mark.pop("plan_a", None)

    def collect_storyboard_image_paths(plan: dict[str, Any], meta: dict[str, Any]) -> list[str]:
        paths: list[str] = []

        def add(path: str) -> None:
            value = str(path or "").strip()
            if value and value not in paths:
                paths.append(value)

        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            for frame_path in shot_keyframe_paths(reference):
                add(frame_path)
            for mark in normalize_scene_marks(shot):
                add(primary_scene_image(mark))
                keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
                mark_paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
                for value in (keyframes.get("single"), keyframes.get("first"), keyframes.get("last"), *mark_paths):
                    add(str(value or ""))
        for item in meta.get("uploaded_assets") or []:
            if isinstance(item, dict):
                add(str(item.get("path") or ""))
        return paths

    def ensure_plan_has_scene_primary_images(plan: dict[str, Any], meta: dict[str, Any]) -> None:
        fallbacks = collect_storyboard_image_paths(plan, meta)
        if not fallbacks:
            return
        scene_counter = 0
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            for mark in normalize_scene_marks(shot):
                if primary_scene_image(mark):
                    continue
                fallback = fallbacks[min(scene_counter, len(fallbacks) - 1)]
                mark["keyframes"] = {"single": fallback, "first": fallback, "last": "", "paths": [fallback]}
                mark["image_fallback_source"] = "global_storyboard_image"
                scene_counter += 1

    def sync_storyboard_files(
        workspace: Path,
        task_row: dict[str, Any],
        plan: dict[str, Any],
        meta: dict[str, Any],
        *,
        write_rebuild_plan: bool = True,
        write_dialogue_plan: bool = False,
        allow_image_fallback: bool = True,
    ) -> dict[str, Any]:
        backfill_storyboard_copy_workspace(workspace, meta)
        source_workspace = storyboard_source_workspace(meta)
        shots = [item for item in (plan.get("shots") or []) if isinstance(item, dict)]
        for index, shot in enumerate(shots, start=1):
            shot["shot_id"] = str(shot.get("shot_id") or f"shot_{index:03d}")
            shot["shot_name"] = str(shot.get("shot_name") or shot["shot_id"])
            for mark in normalize_scene_marks(shot, allow_image_fallback=allow_image_fallback):
                normalize_scene_reference_image(workspace, source_workspace, shot, mark)
        plan["shots"] = shots
        if allow_image_fallback:
            ensure_plan_has_scene_primary_images(plan, meta)
        plan.setdefault("task", {})
        plan["task"]["task_id"] = int(task_row["id"])
        plan["task"]["session_id"] = int(task_row["session_id"])
        meta = {**meta, "working_rebuild_task_id": int(task_row["id"]), "working_rebuild_session_id": int(task_row["session_id"]), "updated_at": now_ms()}
        if write_rebuild_plan:
            write_json_file(workspace / "rebuild_shot_plan.json", plan)
        if write_dialogue_plan:
            write_json_file(workspace / STORYBOARD_DIALOGUE_PLAN_FILE, plan)
        write_json_file(workspace / "storyboard_meta.json", meta)
        snapshot = {
            "task_id": int(task_row["id"]),
            "session_id": int(task_row["session_id"]),
            "source_type": meta.get("source_type"),
            "shots": plan.get("shots") or [],
            "updated_at": meta["updated_at"],
        }
        write_json_file(workspace / "storyboard_snapshot.json", snapshot)
        return meta

    def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
        reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
        return [item for item in (reference.get("scene_marks") or []) if isinstance(item, dict)]

    def explicit_storyboard_dialogue_image(mark: dict[str, Any]) -> str:
        asset = mark.get("storyboard_dialogue_asset") if isinstance(mark.get("storyboard_dialogue_asset"), dict) else {}
        return str(asset.get("path") or "").strip()

    def phase2_scene_image(mark: dict[str, Any]) -> str:
        explicit = explicit_storyboard_dialogue_image(mark)
        if explicit:
            return explicit
        keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
        return str(
            keyframes.get("single")
            or keyframes.get("first")
            or (paths[0] if paths else "")
            or keyframes.get("last")
            or (paths[-1] if paths else "")
            or ""
        ).strip()

    def scene_group_id(mark: dict[str, Any], shot_id: str, fallback_index: int) -> str:
        value = str(mark.get("scene_id") or mark.get("scene_mark_id") or "").strip()
        value = re.sub(r"_dialogue_\d+$", "", value)
        return value or f"{shot_id}_scene_{fallback_index:03d}"

    def joined_scene_text(marks: list[dict[str, Any]], key: str) -> str:
        pieces = []
        for mark in marks:
            text = clean_dialogue_text(mark.get(key) or mark.get("srt_text") or mark.get("source_srt_text") or mark.get("original_srt_text") or "")
            if text:
                pieces.append(text)
        return clean_dialogue_text(" ".join(pieces))

    def aggregate_storyboard_plan_for_rebuild(plan: dict[str, Any]) -> dict[str, Any]:
        next_plan = copy.deepcopy(plan)
        shots = []
        for shot_index, shot in enumerate(next_plan.get("shots") or [], start=1):
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("shot_id") or f"shot_{shot_index:03d}")
            shot["shot_id"] = shot_id
            reference = shot.setdefault("reference", {})
            raw_marks = [item for item in (reference.get("scene_marks") or []) if isinstance(item, dict)]
            if not raw_marks:
                raw_marks = normalize_scene_marks(shot, allow_image_fallback=False)
            grouped: list[tuple[str, list[dict[str, Any]]]] = []
            group_lookup: dict[str, list[dict[str, Any]]] = {}
            for mark_index, mark in enumerate(raw_marks, start=1):
                group_id = scene_group_id(mark, shot_id, mark_index)
                if group_id not in group_lookup:
                    group_lookup[group_id] = []
                    grouped.append((group_id, group_lookup[group_id]))
                group_lookup[group_id].append(mark)
            scene_marks = []
            for scene_index, (scene_id, marks) in enumerate(grouped, start=1):
                first_mark = marks[0]
                last_mark = marks[-1]
                scene = copy.deepcopy(first_mark)
                for key in (
                    "dialogue_id",
                    "dialogue_index",
                    "dialogue_count",
                    "dialogue_mark_id",
                    "dialogue_source",
                    "dialogue",
                ):
                    scene.pop(key, None)
                first = phase2_scene_image(first_mark)
                last = phase2_scene_image(last_mark) if len(marks) > 1 else ""
                if first and last and first == last:
                    last = ""
                start = float(first_mark.get("start") or 0)
                end = float(last_mark.get("end") or first_mark.get("end") or start)
                duration_sum = sum(float(mark.get("duration") or 0) for mark in marks)
                duration = duration_sum if duration_sum > 0 else max(0.0, end - start)
                uses_first_last = len(marks) > 1
                scene.update({
                    "scene_mark_id": scene_id,
                    "scene_id": scene_id,
                    "shot_id": shot_id,
                    "scene_index": scene_index,
                    "mode": "first_last" if uses_first_last else "single",
                    "generation_mode": "first_last" if uses_first_last else "first_frame",
                    "start": start,
                    "end": end,
                    "duration": duration,
                    "boundary_source": "storyboard_scene_aggregate",
                    "keyframes": {
                        "single": first if not uses_first_last else "",
                        "first": first,
                        "last": last,
                        "paths": list(dict.fromkeys([path for path in (first, last) if path])),
                    },
                    "srt_text": joined_scene_text(marks, "srt_text"),
                    "source_srt_text": joined_scene_text(marks, "source_srt_text"),
                    "original_srt_text": joined_scene_text(marks, "original_srt_text"),
                })
                plan_a = scene.get("plan_a") if isinstance(scene.get("plan_a"), dict) else {}
                scene_asset = plan_a.get("scene_asset") if isinstance(plan_a.get("scene_asset"), dict) else {}
                for key in ("selected_image", "manifest", "generated_at", "provider", "model"):
                    scene_asset.pop(key, None)
                if scene_asset:
                    plan_a["scene_asset"] = scene_asset
                else:
                    plan_a.pop("scene_asset", None)
                plan_a["scene_confirmed"] = bool(first)
                plan_a["first_last_confirmed"] = bool(first) and (not uses_first_last or bool(last))
                scene["plan_a"] = plan_a
                scene_marks.append(scene)
            reference["scene_marks"] = scene_marks
            reference["scene_mark_summary"] = {
                "shot_id": shot_id,
                "scene_count": len(scene_marks),
                "source": "storyboard_scene_aggregate",
            }
            reference["srt_text"] = clean_dialogue_text(" ".join(mark.get("srt_text") or "" for mark in scene_marks))
            reference["source_srt_text"] = clean_dialogue_text(" ".join(mark.get("source_srt_text") or "" for mark in scene_marks))
            reference["original_srt_text"] = clean_dialogue_text(" ".join(mark.get("original_srt_text") or "" for mark in scene_marks))
            shot["reference"] = reference
            shots.append(shot)
        next_plan["shots"] = shots
        return next_plan

    def reset_phase2_downstream_state(plan: dict[str, Any]) -> None:
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            shot.pop("final_prompt_package", None)
            shot.pop("plan_d", None)
            for mark in scene_marks_for_shot(shot):
                mark.pop("plan_d", None)
                mark.pop("scene_description", None)
                mark.pop("prompt", None)
                mark.pop("video_prompt", None)
                mark.pop("final_prompts", None)
                plan_a = mark.get("plan_a") if isinstance(mark.get("plan_a"), dict) else {}
                scene_asset = plan_a.get("scene_asset") if isinstance(plan_a.get("scene_asset"), dict) else {}
                for key in ("selected_image", "manifest", "generated_at", "provider", "model"):
                    scene_asset.pop(key, None)
                if scene_asset:
                    scene_asset["source"] = "storyboard_reference_image"
                    plan_a["scene_asset"] = scene_asset
                else:
                    plan_a.pop("scene_asset", None)
                if plan_a:
                    mark["plan_a"] = plan_a
                else:
                    mark.pop("plan_a", None)

    def current_scene_ids(plan: dict[str, Any]) -> dict[str, set[str]]:
        by_shot: dict[str, set[str]] = {}
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("shot_id") or "").strip()
            if not shot_id:
                continue
            by_shot[shot_id] = {
                str(mark.get("scene_mark_id") or "").strip()
                for mark in scene_marks_for_shot(shot)
                if str(mark.get("scene_mark_id") or "").strip()
            }
        return by_shot

    def clean_phase2_downstream_outputs(workspace: Path, plan: dict[str, Any], variant_id: str = "variant_001") -> list[str]:
        cleaned: list[str] = []
        scenes_by_shot = current_scene_ids(plan)

        def remove_path(path: Path) -> None:
            if not path.exists():
                return
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            cleaned.append(rel_path(workspace, path))

        def clean_scene_dir_preserving_tts(scene_dir: Path) -> None:
            if not scene_dir.exists() or not scene_dir.is_dir():
                return
            for child in scene_dir.iterdir():
                if child.name == "tts" and child.is_dir():
                    continue
                remove_path(child)

        remove_path(workspace / "asset_tasks.json")
        for prompt_sidecar in workspace.glob("asset_prompts_shot_*.json"):
            remove_path(prompt_sidecar)
        remove_path(workspace / "final_prompt_packages")
        for flow_root in ("asset_image_workflows", "asset_video_workflows"):
            base = workspace / flow_root
            if base.exists():
                for child in base.iterdir():
                    if child.is_dir():
                        remove_path(child)

        for shot_id, scene_ids in scenes_by_shot.items():
            remove_names = {
                "final_prompt_package.json",
                "codex_imagegen_jobs.json",
                "plan_d_video_prompt_timed.txt",
                "plan_d_video_prompt_timed.json",
                "plan_d_raw.mp4",
                "plan_d.mp4",
            }
            for assets_root in ("Assets", "assets"):
                shot_dir = workspace / assets_root / variant_id / shot_id
                for name in remove_names:
                    remove_path(shot_dir / name)
                reports_dir = shot_dir / "reports"
                if reports_dir.exists():
                    for report in reports_dir.glob("plan_d*"):
                        remove_path(report)
                    for report in reports_dir.glob("*12_00*"):
                        remove_path(report)
                    for report in reports_dir.glob("*12_02*"):
                        remove_path(report)
                if not shot_dir.exists():
                    continue
                for child in shot_dir.iterdir():
                    if not child.is_dir() or not child.name.startswith(f"{shot_id}_"):
                        continue
                    clean_scene_dir_preserving_tts(child)
        global_plan_d = workspace / "reports" / "plan_d"
        if global_plan_d.exists():
            for report in global_plan_d.glob("*"):
                remove_path(report)
        return cleaned

    async def run_rebuild_v1_tool(workspace: Path, task_id: int, session_id: int, script_name: str, extra_args: list[str] | None = None, timeout: int = 1800) -> dict[str, Any]:
        script_path = Path(__file__).resolve().parents[3] / "ToolLibrary" / "Rebuild_V1" / script_name
        if not script_path.exists():
            raise HTTPException(status_code=500, detail=f"Rebuild tool not found: {script_name}")
        cmd = [sys.executable or "python3", str(script_path), "--workspace", str(workspace), "--print-json"]
        if script_name != "04_04_ShotPlan_SceneAssetCanonicalize.py":
            cmd.extend(["--task-id", str(task_id), "--session-id", str(session_id)])
        if extra_args:
            cmd.extend(extra_args)

        def run_tool() -> subprocess.CompletedProcess[str]:
            return subprocess.run(cmd, cwd=str(script_path.parent), check=False, capture_output=True, text=True, timeout=timeout)

        completed = await asyncio.to_thread(run_tool)
        stdout_text = (completed.stdout or "").strip()
        parsed: dict[str, Any] = {}
        if stdout_text:
            try:
                value = json.loads(stdout_text)
                parsed = value if isinstance(value, dict) else {}
            except Exception:
                parsed = {}
        if completed.returncode == 0:
            return parsed or {"tool": script_path.stem, "status": "completed"}
        detail = (completed.stderr or completed.stdout or f"{script_path.stem} failed").strip()[-4000:]
        status = int(400 if completed.returncode == 2 else 500)
        raise HTTPException(status_code=status, detail=detail)

    async def refresh_storyboard_phase2_outputs(task_row: dict[str, Any], plan: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        workspace = workspace_path(task_row)
        task_id = int(task_row["id"])
        session_id = int(task_row["session_id"])
        shutil.rmtree(workspace / "uploads" / "storyboard_references", ignore_errors=True)
        dialogue_plan = read_storyboard_edit_plan(workspace) or plan
        meta = sync_storyboard_files(
            workspace,
            task_row,
            dialogue_plan,
            meta,
            write_rebuild_plan=False,
            write_dialogue_plan=True,
            allow_image_fallback=False,
        )
        plan = aggregate_storyboard_plan_for_rebuild(dialogue_plan)
        reset_phase2_downstream_state(plan)
        meta.pop("reference_asset_pool", None)
        meta = sync_storyboard_files(
            workspace,
            task_row,
            plan,
            meta,
            write_rebuild_plan=True,
            write_dialogue_plan=False,
            allow_image_fallback=False,
        )
        cleaned = clean_phase2_downstream_outputs(workspace, plan)
        steps: list[dict[str, Any]] = []
        for script_name, extra_args in (
            ("04_02_ShotPlan_FirstLastFrameConfirm.py", ["--force"]),
            ("04_03_ShotPlan_FirstLastReadinessCheck.py", ["--force"]),
        ):
            result = await run_rebuild_v1_tool(workspace, task_id, session_id, script_name, extra_args)
            steps.append({"script": script_name, "status": result.get("status"), "payload": result})
        plan = read_json_file(workspace / "rebuild_shot_plan.json")
        meta = read_json_file(workspace / "storyboard_meta.json")
        meta["phase2_refresh"] = {
            "status": "completed",
            "refreshed_at": now_ms(),
            "cleaned_outputs": cleaned,
            "steps": steps,
            "boundary": "storyboard_to_reference_ready_phase2",
            "storyboard_images_role": "reference_image_only",
            "final_images": "empty_until_manual_import",
            "suppress_plan_d_prompt_autobuild": True,
            "next_step": "inspect_task_structure_then_run_plan_d_image_prepare_manually",
        }
        meta["reference_asset_pool"] = reference_asset_pool(task_row, plan, meta)
        meta["finalized_at"] = meta["phase2_refresh"]["refreshed_at"]
        write_json_file(workspace / "storyboard_meta.json", meta)
        return {"meta": meta, "shot_plan": plan, "cleaned_outputs": cleaned, "steps": steps}

    def asset_from_path(asset_id: str, path: str, label: str, session_id: int | None, source: str, shot_id: str = "", scene_mark_id: str = "", role: str = "single", meta_fields: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not path:
            return None
        item = {
            "id": asset_id,
            "path": path,
            "label": label,
            "resource_session_id": session_id,
            "source": source,
            "shot_id": shot_id,
            "scene_mark_id": scene_mark_id,
            "role": role,
        }
        if meta_fields:
            item.update(meta_fields)
        return item

    def build_asset_pool(task_row: dict[str, Any], plan: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, Any]]:
        task_session_id = int(task_row["session_id"])
        analysis_session_id = get_analysis_session_id(int(task_row.get("analysis_task_id") or 0) or None)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        def image_session_id(path: str) -> int | None:
            return task_session_id if path.startswith(("Assets/", "uploads/")) else analysis_session_id

        def image_source(path: str) -> str:
            return "generated_frame" if path.startswith("Assets/") else "uploaded_frame" if path.startswith("uploads/") else "confirmed_frame"

        def add(item: dict[str, Any] | None) -> None:
            if not item:
                return
            key = str(item.get("id") or f"{item.get('resource_session_id')}:{item.get('path')}:{item.get('role')}:{item.get('label')}")
            if key in seen:
                return
            seen.add(key)
            items.append(item)

        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("shot_id") or "")
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            for mark in normalize_scene_marks(shot):
                scene_id = str(mark.get("scene_mark_id") or "")
                scene_meta = {
                    "scene_id": str(mark.get("scene_id") or scene_id),
                    "scene_index": mark.get("scene_index"),
                    "duration": float(mark.get("duration") or 0),
                    "srt_text": clean_dialogue_text(mark.get("srt_text") or mark.get("source_srt_text") or mark.get("original_srt_text") or ""),
                    "char_count": spoken_char_count(str(mark.get("srt_text") or mark.get("source_srt_text") or mark.get("original_srt_text") or "")),
                }
                generated, generated_source = best_scene_image(mark)
                if generated:
                    add(asset_from_path(f"{scene_id}-display", generated, f"{shot_id} {scene_id}", task_session_id if generated_source == "generated" else analysis_session_id, generated_source, shot_id, scene_id, "display", scene_meta))
                keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
                for role in ("single", "first", "last"):
                    path = str(keyframes.get(role) or "").strip()
                    add(asset_from_path(f"{scene_id}-{role}", path, f"{scene_id} {role}", image_session_id(path), image_source(path), shot_id, scene_id, role, scene_meta))
            for frame in reference.get("keyframes") or []:
                if not isinstance(frame, dict):
                    continue
                path = str(frame.get("path") or "").strip()
                if not path:
                    continue
                resource = str(frame.get("resource_session") or "")
                add(asset_from_path(f"{shot_id}-keyframe-{len(items)}", path, f"{shot_id} candidate", analysis_session_id if resource == "analysis" else task_session_id, "candidate_frame", shot_id, "", "candidate"))
        for item in meta.get("uploaded_assets") or []:
            if isinstance(item, dict):
                add({**item, "resource_session_id": item.get("resource_session_id") or task_session_id, "source": item.get("source") or "upload"})
        return items

    def reference_asset_pool(task_row: dict[str, Any], plan: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, Any]]:
        def append_current_uploads(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
            task_session_id = int(task_row["session_id"])
            seen = {str(item.get("id") or f"{item.get('resource_session_id')}:{item.get('path')}:{item.get('role')}:{item.get('label')}") for item in pool if isinstance(item, dict)}
            for item in meta.get("uploaded_assets") or []:
                if not isinstance(item, dict):
                    continue
                upload = {**item, "resource_session_id": item.get("resource_session_id") or task_session_id, "source": item.get("source") or "upload"}
                key = str(upload.get("id") or f"{upload.get('resource_session_id')}:{upload.get('path')}:{upload.get('role')}:{upload.get('label')}")
                if key in seen:
                    continue
                seen.add(key)
                pool.append(upload)
            return pool

        source_task_id = int(meta.get("copied_from_rebuild_task_id") or 0)
        if source_task_id:
            try:
                source_task = get_task(source_task_id)
                source_plan = read_json_file(workspace_path(source_task) / "rebuild_shot_plan.json")
                pool = build_asset_pool(source_task, source_plan or plan, {})
                for item in pool:
                    item["source_rebuild_task_id"] = int(source_task["id"])
                    item["source_rebuild_session_id"] = int(source_task["session_id"])
            except HTTPException:
                pool = build_asset_pool(task_row, plan, meta)
        else:
            frozen = meta.get("reference_asset_pool")
            pool = [item for item in frozen if isinstance(item, dict)] if isinstance(frozen, list) else build_asset_pool(task_row, plan, meta)
        pool = append_current_uploads(pool)
        meta["reference_asset_pool"] = pool
        return pool

    def spoken_char_count(text: str) -> int:
        value = clean_dialogue_text(text)
        value = re.sub(r"\s+", "", value)
        value = re.sub(r"[，。！？、；：,.!?;:\"“”‘’（）()\[\]【】《》<>-]", "", value)
        return len(value)

    def timing_workspace_candidates(workspace: Path, meta: dict[str, Any]) -> list[Path]:
        candidates = [workspace]
        source_session_id = int(meta.get("copied_from_rebuild_session_id") or 0)
        if source_session_id:
            try:
                source_workspace = Path(str(safe_session(source_session_id).get("workspace_dir") or ""))
                if source_workspace and source_workspace not in candidates:
                    candidates.append(source_workspace)
            except Exception:
                pass
        return candidates

    def read_build_g_report(workspaces: list[Path]) -> tuple[dict[str, Any], str]:
        for base in workspaces:
            report_path = base / "reports" / "rebuild_v1" / "03_02_ShotPlan_GTTSVoiceBuilder.json"
            data = read_json_file(report_path)
            if data:
                return data, str(report_path)
        return {}, ""

    def build_timing_model(task_row: dict[str, Any], plan: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        workspace = workspace_path(task_row)
        workspaces = timing_workspace_candidates(workspace, meta)
        report, report_path = read_build_g_report(workspaces)
        plan_selection = plan.get("plan_a_tts_selection") if isinstance(plan.get("plan_a_tts_selection"), dict) else {}
        report_selection = report.get("selection") if isinstance(report.get("selection"), dict) else {}
        default_selection = report_selection.get("default_selection") if isinstance(report_selection.get("default_selection"), dict) else {}
        top_candidates = report.get("top_candidates") if isinstance(report.get("top_candidates"), list) else []
        top_candidate = next((item for item in top_candidates if isinstance(item, dict)), {})
        candidate_id = str(plan_selection.get("candidate_id") or report_selection.get("selected_candidate_id") or "")
        report_candidate = next((item for item in top_candidates if isinstance(item, dict) and str(item.get("candidate_id") or "") == candidate_id), {})
        selection = plan_selection or report_candidate or default_selection or top_candidate
        sample_text = str(report.get("sample_text") or "")
        if not sample_text:
            prompt = str(selection.get("prompt") or "")
            sample_text = prompt.split("正文：", 1)[-1].strip() if "正文：" in prompt else prompt
        build_g_duration = float(
            selection.get("fit_duration")
            or (selection.get("fit_meta") or {}).get("fit_duration")
            or report.get("target_duration")
            or 0
        )
        build_g_chars = spoken_char_count(sample_text)
        sec_per_char = build_g_duration / build_g_chars if build_g_duration > 0 and build_g_chars > 0 else 0
        shots_payload: dict[str, Any] = {}
        sample_sources = report.get("sample_text_sources") if isinstance(report.get("sample_text_sources"), list) else []
        sample_by_shot = {
            str(item.get("shot_id") or ""): item
            for item in sample_sources
            if isinstance(item, dict) and item.get("shot_id")
        }

        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("shot_id") or "").strip()
            if not shot_id:
                continue
            sample = sample_by_shot.get(shot_id) or {}
            text = str(sample.get("text") or shot.get("reference", {}).get("source_srt_text") or shot.get("reference", {}).get("srt_text") or field_text(shot.get("ui_summary"), ["summary"]) or "")
            chars = spoken_char_count(text)
            duration = sec_per_char * chars if sec_per_char and chars else 0
            shots_payload[shot_id] = {
                "shot_id": shot_id,
                "source": "build_g_sample_text" if sample else "build_g_global_formula",
                "builder": "Builder-G",
                "provider": selection.get("provider") or report.get("provider") or "",
                "model": selection.get("model") or report.get("model") or "",
                "voice": selection.get("voice") or selection.get("voice_id") or "",
                "duration": round(duration, 3),
                "raw_duration": selection.get("raw_duration") or None,
                "tempo": (selection.get("fit_meta") or {}).get("tempo") or None,
                "char_count": chars,
                "sec_per_char": round(sec_per_char, 6),
                "text": text,
                "candidate_id": selection.get("candidate_id") or "",
            }
        return {
            "source": "Builder-G fitted voice duration",
            "provider": selection.get("provider") or report.get("provider") or "",
            "model": selection.get("model") or report.get("model") or "",
            "voice": selection.get("voice") or selection.get("voice_id") or "",
            "candidate_id": selection.get("candidate_id") or "",
            "selection": selection,
            "top_candidates": top_candidates,
            "target_duration": report.get("target_duration") or None,
            "report": report_path,
            "build_g_text": sample_text,
            "build_g_duration": round(build_g_duration, 3),
            "build_g_chars": build_g_chars,
            "sec_per_char": round(sec_per_char, 6),
            "total_duration": round(build_g_duration, 3),
            "total_chars": build_g_chars,
            "avg_sec_per_char": round(sec_per_char, 6),
            "shots": shots_payload,
        }

    def field_text(value: Any, keys: list[str]) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, dict):
            return ""
        for key in keys:
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""

    def serialize_storyboard(task_row: dict[str, Any]) -> dict[str, Any]:
        workspace = workspace_path(task_row)
        plan = read_storyboard_edit_plan(workspace)
        meta = read_json_file(workspace / "storyboard_meta.json")
        if not meta:
            raise HTTPException(status_code=404, detail="StoryBoard metadata not found for this task")
        meta = sync_storyboard_files(
            workspace,
            task_row,
            plan,
            meta,
            write_rebuild_plan=False,
            write_dialogue_plan=True,
            allow_image_fallback=False,
        )
        asset_pool = reference_asset_pool(task_row, plan, meta)
        if meta.get("reference_asset_pool") != asset_pool:
            meta["reference_asset_pool"] = asset_pool
        write_json_file(workspace / "storyboard_meta.json", meta)
        return {"ok": True, "task": task_row, "meta": meta, "shot_plan": plan, "asset_pool": asset_pool, "timing_model": build_timing_model(task_row, plan, meta)}

    def parse_srt_timestamp(value: str) -> float:
        match = re.match(r"(?P<h>\d+):(?P<m>\d+):(?P<s>\d+),(?P<ms>\d+)", value.strip())
        if not match:
            return 0.0
        return int(match.group("h")) * 3600 + int(match.group("m")) * 60 + int(match.group("s")) + int(match.group("ms")) / 1000

    def parse_srt(content: str) -> list[dict[str, Any]]:
        blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n").replace("\r", "\n").strip())
        rows: list[dict[str, Any]] = []
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            time_line_index = next((index for index, line in enumerate(lines) if "-->" in line), -1)
            if time_line_index < 0:
                continue
            start_raw, end_raw = [part.strip() for part in lines[time_line_index].split("-->", 1)]
            text = " ".join(lines[time_line_index + 1 :]).strip()
            start = parse_srt_timestamp(start_raw)
            end = parse_srt_timestamp(end_raw)
            rows.append({"start": start, "end": end, "duration": max(0.1, end - start), "text": text})
        return rows

    def blank_plan_from_srt(task_id: int, session_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
        shots = []
        for index, row in enumerate(rows or [{"start": 0, "end": 4, "duration": 4, "text": ""}], start=1):
            shot_id = f"shot_{index:03d}"
            scene_id = f"{shot_id}_scene_001"
            scene = {
                "scene_mark_id": scene_id,
                "shot_id": shot_id,
                "scene_index": 1,
                "mode": "single",
                "generation_mode": "first_frame",
                "start": row["start"],
                "end": row["end"],
                "duration": row["duration"],
                "boundary_source": "storyboard_upload_srt",
                "keyframes": {"single": "", "first": "", "last": "", "paths": []},
                "srt_text": row["text"],
                "source_srt_text": row["text"],
                "original_srt_text": row["text"],
                "plan_a": {"scene_confirmed": False, "first_last_confirmed": False},
            }
            shots.append({
                "source_segment_id": shot_id,
                "source_index": index,
                "start": row["start"],
                "end": row["end"],
                "duration": row["duration"],
                "role": "storyboard_upload",
                "formula_slot": "scene",
                "shot_id": shot_id,
                "shot_name": shot_id,
                "reference": {
                    "start": row["start"],
                    "end": row["end"],
                    "duration": row["duration"],
                    "srt_text": row["text"],
                    "source_srt_text": row["text"],
                    "original_srt_text": row["text"],
                    "keyframes": [],
                    "scene_marks": [scene],
                },
                "ui_summary": {"summary": row["text"]},
                "rebuild_direction": {"new_spoken_script": row["text"]},
            })
        return {"task": {"task_id": task_id, "session_id": session_id, "source": "storyboard_upload"}, "shots": shots}

    def number_value(value: Any, fallback: float = 0.0) -> float:
        try:
            parsed = float(value)
            return parsed if parsed == parsed else fallback
        except (TypeError, ValueError):
            return fallback

    def structured_scene_marks(shot: dict[str, Any], shot_id: str) -> list[dict[str, Any]]:
        reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
        raw_marks = reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else None
        if raw_marks is None:
            raw_marks = shot.get("scenes") if isinstance(shot.get("scenes"), list) else []
        marks: list[dict[str, Any]] = []
        cursor = 0.0
        for index, raw in enumerate([item for item in raw_marks if isinstance(item, dict)], start=1):
            scene_id_raw = str(raw.get("scene_id") or raw.get("scene_mark_id") or f"{shot_id}_scene_{index:03d}").strip()
            scene_id = re.sub(r"_dialogue_\d+$", "", scene_id_raw) or f"{shot_id}_scene_{index:03d}"
            text = clean_dialogue_text(raw.get("srt_text") or raw.get("narration") or raw.get("text") or raw.get("subtitle") or "")
            start = number_value(raw.get("start"), cursor)
            duration = number_value(raw.get("duration"), -1.0)
            end = number_value(raw.get("end"), start + max(duration, 0.0))
            if duration < 0:
                duration = max(0.2, end - start)
            end = start + duration
            keyframes = raw.get("keyframes") if isinstance(raw.get("keyframes"), dict) else {}
            paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
            plan_a = raw.get("plan_a") if isinstance(raw.get("plan_a"), dict) else {}
            mark = copy.deepcopy(raw)
            mark.update({
                "scene_mark_id": str(raw.get("scene_mark_id") or scene_id),
                "scene_id": scene_id,
                "scene_name": str(raw.get("scene_name") or raw.get("name") or raw.get("title") or scene_id).strip(),
                "shot_id": shot_id,
                "scene_index": index,
                "dialogue_index": int(number_value(raw.get("dialogue_index"), 1)),
                "dialogue_id": str(raw.get("dialogue_id") or f"{scene_id}_dialogue_001"),
                "mode": str(raw.get("mode") or "single"),
                "generation_mode": str(raw.get("generation_mode") or "first_frame"),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(max(0.2, duration), 3),
                "scene_target_duration": round(number_value(raw.get("scene_target_duration"), max(0.2, duration)), 3),
                "boundary_source": str(raw.get("boundary_source") or "storyboard_json_upload"),
                "srt_text": text,
                "source_srt_text": clean_dialogue_text(raw.get("source_srt_text") or text),
                "original_srt_text": clean_dialogue_text(raw.get("original_srt_text") or text),
                "keyframes": {
                    "single": str(keyframes.get("single") or ""),
                    "first": str(keyframes.get("first") or ""),
                    "last": str(keyframes.get("last") or ""),
                    "paths": [str(path) for path in paths if str(path or "").strip()],
                },
                "plan_a": {
                    **plan_a,
                    "scene_confirmed": bool(plan_a.get("scene_confirmed", False)),
                    "first_last_confirmed": bool(plan_a.get("first_last_confirmed", False)),
                },
            })
            marks.append(mark)
            cursor = end
        return marks

    def structured_plan_from_json(task_id: int, session_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        source_plan = payload.get("shot_plan") if isinstance(payload.get("shot_plan"), dict) else payload
        if not isinstance(source_plan.get("shots"), list) or not source_plan.get("shots"):
            raise HTTPException(status_code=400, detail="JSON must include a non-empty shots array")
        next_plan = copy.deepcopy(source_plan)
        shots = []
        timeline_cursor = 0.0
        for index, raw_shot in enumerate([item for item in source_plan.get("shots") if isinstance(item, dict)], start=1):
            shot = copy.deepcopy(raw_shot)
            shot_id = str(shot.get("shot_id") or f"shot_{index:03d}").strip() or f"shot_{index:03d}"
            ui_summary = shot.get("ui_summary") if isinstance(shot.get("ui_summary"), dict) else {}
            shot_name = str(shot.get("shot_name") or shot.get("name") or shot.get("title") or ui_summary.get("title") or shot_id).strip()
            scene_marks = structured_scene_marks(shot, shot_id)
            if not scene_marks:
                text = clean_dialogue_text((shot.get("reference") or {}).get("srt_text") if isinstance(shot.get("reference"), dict) else shot.get("srt_text") or "")
                scene_marks = [{
                    "scene_mark_id": f"{shot_id}_scene_001",
                    "scene_id": f"{shot_id}_scene_001",
                    "scene_name": shot_name,
                    "shot_id": shot_id,
                    "scene_index": 1,
                    "dialogue_index": 1,
                    "dialogue_id": f"{shot_id}_scene_001_dialogue_001",
                    "mode": "single",
                    "generation_mode": "first_frame",
                    "start": 0.0,
                    "end": max(0.2, number_value(shot.get("duration"), 4.0)),
                    "duration": max(0.2, number_value(shot.get("duration"), 4.0)),
                    "scene_target_duration": max(0.2, number_value(shot.get("duration"), 4.0)),
                    "boundary_source": "storyboard_json_upload",
                    "srt_text": text,
                    "source_srt_text": text,
                    "original_srt_text": text,
                    "keyframes": {"single": "", "first": "", "last": "", "paths": []},
                    "plan_a": {"scene_confirmed": False, "first_last_confirmed": False},
                }]
            duration = round(sum(number_value(mark.get("duration"), 0.0) for mark in scene_marks), 3)
            start = number_value(shot.get("start"), timeline_cursor)
            end = number_value(shot.get("end"), start + duration)
            text = clean_dialogue_text(" ".join(mark.get("srt_text") or "" for mark in scene_marks))
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            shot.update({
                "source_segment_id": str(shot.get("source_segment_id") or shot_id),
                "source_index": int(number_value(shot.get("source_index"), index)),
                "start": round(start, 3),
                "end": round(end if end > start else start + duration, 3),
                "duration": duration,
                "role": str(shot.get("role") or "storyboard_json_upload"),
                "formula_slot": str(shot.get("formula_slot") or "scene"),
                "shot_id": shot_id,
                "shot_name": shot_name,
                "reference": {
                    **reference,
                    "start": round(start, 3),
                    "end": round(end if end > start else start + duration, 3),
                    "duration": duration,
                    "srt_text": text,
                    "source_srt_text": clean_dialogue_text(reference.get("source_srt_text") or text),
                    "original_srt_text": clean_dialogue_text(reference.get("original_srt_text") or text),
                    "keyframes": reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else [],
                    "scene_marks": scene_marks,
                },
                "ui_summary": {
                    **ui_summary,
                    "title": str(ui_summary.get("title") or shot_name),
                    "summary": str(ui_summary.get("summary") or text),
                },
                "rebuild_direction": {
                    **(shot.get("rebuild_direction") if isinstance(shot.get("rebuild_direction"), dict) else {}),
                    "new_spoken_script": text,
                },
            })
            shots.append(shot)
            timeline_cursor = shot["end"]
        next_plan["task"] = {
            **(next_plan.get("task") if isinstance(next_plan.get("task"), dict) else {}),
            "task_id": task_id,
            "session_id": session_id,
            "source": "storyboard_json_upload",
        }
        next_plan["shots"] = shots
        return next_plan

    @router.get("/api/ocstoryboard/tasks")
    async def list_storyboard_tasks() -> dict[str, Any]:
        items = []
        for task in repo.list_tasks():
            meta = read_json_file(workspace_path(task) / "storyboard_meta.json")
            if meta:
                items.append({"task": task, "meta": meta})
        return {"items": items}

    @router.post("/api/ocstoryboard/copy-from-rebuild/{source_task_id}")
    async def copy_from_rebuild(source_task_id: int) -> dict[str, Any]:
        source_task = get_task(source_task_id)
        source_workspace = workspace_path(source_task)
        source_plan = read_json_file(source_workspace / "rebuild_shot_plan.json")
        if not source_plan:
            raise HTTPException(status_code=400, detail="Source Rebuild task has no rebuild_shot_plan.json")
        session_id, workspace = create_rebuild_session("StoryBoard")
        copy_reference_workspace(source_workspace, workspace)
        task_id = copy_rebuild_task(source_task, session_id, "storyboard_editing")
        task_row = get_task(task_id)
        plan = read_json_file(workspace / "rebuild_shot_plan.json") or copy.deepcopy(source_plan)
        meta = {
            "source_type": "rebuild_copy",
            "copied_from_rebuild_task_id": int(source_task["id"]),
            "copied_from_rebuild_session_id": int(source_task["session_id"]),
            "analysis_task_id": int(source_task.get("analysis_task_id") or 0) or None,
            "analysis_session_id": get_analysis_session_id(int(source_task.get("analysis_task_id") or 0) or None),
            "created_at": now_ms(),
            "source_readonly": True,
            "notes": "Created as an editable StoryBoard working copy. The source Rebuild session is not modified.",
        }
        sync_storyboard_files(workspace, task_row, plan, meta, write_dialogue_plan=True)
        add_event(session_id, "ocstoryboard.copy.created", {"task_id": task_id, "source_task_id": source_task_id})
        return serialize_storyboard(task_row)

    @router.post("/api/ocstoryboard/blank")
    async def create_blank_storyboard(srt: UploadFile = File(...), images: list[UploadFile] = File(default=[])) -> dict[str, Any]:
        content = await srt.read()
        if not content:
            raise HTTPException(status_code=400, detail="SRT file is empty")
        rows = parse_srt(content.decode("utf-8-sig", errors="ignore"))
        if not rows:
            raise HTTPException(status_code=400, detail="Unable to parse SRT rows")
        session_id, workspace = create_rebuild_session("Blank StoryBoard")
        task_id = create_blank_rebuild_task(session_id)
        task_row = get_task(task_id)
        upload_root = workspace / "uploads" / "storyboard"
        image_dir = upload_root / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        (upload_root / "source.srt").write_bytes(content)
        uploaded_assets: list[dict[str, Any]] = []
        for index, upload in enumerate(images or [], start=1):
            data = await upload.read()
            if not data:
                continue
            target = image_dir / safe_upload_name(upload.filename or "", f"image_{index:03d}")
            target.write_bytes(data)
            uploaded_assets.append({
                "id": f"upload-{index:03d}",
                "path": rel_path(workspace, target),
                "filename": upload.filename or target.name,
                "label": upload.filename or target.name,
                "content_type": upload.content_type or "",
                "source": "upload",
                "resource_session_id": session_id,
                "role": "pool",
            })
        plan = blank_plan_from_srt(task_id, session_id, rows)
        source_package = {"source": "storyboard_upload", "srt_path": "uploads/storyboard/source.srt", "image_count": len(uploaded_assets), "created_at": now_ms()}
        write_json_file(workspace / "source_package.json", source_package)
        write_json_file(workspace / "rebuild_intent.json", {"source": "storyboard_upload", "goal": "Create Rebuild session from uploaded SRT and images.", "created_at": now_ms()})
        meta = {"source_type": "blank_upload", "created_at": now_ms(), "uploaded_srt": "uploads/storyboard/source.srt", "uploaded_assets": uploaded_assets, "source_readonly": False}
        sync_storyboard_files(workspace, task_row, plan, meta, write_dialogue_plan=True)
        add_event(session_id, "ocstoryboard.blank.created", {"task_id": task_id, "srt_rows": len(rows), "image_count": len(uploaded_assets)})
        return serialize_storyboard(task_row)

    @router.post("/api/ocstoryboard/json")
    async def create_json_storyboard(storyboard: UploadFile = File(...), images: list[UploadFile] = File(default=[])) -> dict[str, Any]:
        content = await storyboard.read()
        if not content:
            raise HTTPException(status_code=400, detail="JSON file is empty")
        try:
            payload = json.loads(content.decode("utf-8-sig", errors="ignore"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Unable to parse JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON root must be an object")
        session_id, workspace = create_rebuild_session("Structured StoryBoard")
        task_id = create_blank_rebuild_task(session_id)
        task_row = get_task(task_id)
        upload_root = workspace / "uploads" / "storyboard"
        image_dir = upload_root / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        json_path = upload_root / "source.json"
        json_path.write_bytes(content)
        uploaded_assets: list[dict[str, Any]] = []
        for index, upload in enumerate(images or [], start=1):
            data = await upload.read()
            if not data:
                continue
            target = image_dir / safe_upload_name(upload.filename or "", f"image_{index:03d}")
            target.write_bytes(data)
            uploaded_assets.append({
                "id": f"upload-{index:03d}",
                "path": rel_path(workspace, target),
                "filename": upload.filename or target.name,
                "label": upload.filename or target.name,
                "content_type": upload.content_type or "",
                "source": "upload",
                "resource_session_id": session_id,
                "role": "pool",
            })
        plan = structured_plan_from_json(task_id, session_id, payload)
        title = str(payload.get("title") or (payload.get("task") if isinstance(payload.get("task"), dict) else {}).get("title") or "Structured StoryBoard").strip()
        source_package = {"source": "storyboard_json_upload", "json_path": "uploads/storyboard/source.json", "image_count": len(uploaded_assets), "created_at": now_ms()}
        write_json_file(workspace / "source_package.json", source_package)
        write_json_file(workspace / "rebuild_intent.json", {"source": "storyboard_json_upload", "goal": f"Create Rebuild session from structured StoryBoard JSON: {title}", "created_at": now_ms()})
        meta = {
            "source_type": "structured_json_upload",
            "created_at": now_ms(),
            "uploaded_json": "uploads/storyboard/source.json",
            "uploaded_assets": uploaded_assets,
            "source_readonly": False,
            "title": title,
            "schema_version": payload.get("schema_version") or "",
        }
        sync_storyboard_files(workspace, task_row, plan, meta, write_dialogue_plan=True)
        add_event(session_id, "ocstoryboard.json.created", {"task_id": task_id, "shot_count": len(plan.get("shots") or []), "image_count": len(uploaded_assets)})
        return serialize_storyboard(task_row)

    @router.get("/api/ocstoryboard/tasks/{task_id}")
    async def get_storyboard(task_id: int) -> dict[str, Any]:
        return serialize_storyboard(get_task(task_id))

    @router.post("/api/ocstoryboard/tasks/{task_id}/assets")
    async def upload_storyboard_assets(task_id: int, files: list[UploadFile] = File(default=[])) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        meta = read_json_file(workspace / "storyboard_meta.json")
        if not meta:
            raise HTTPException(status_code=404, detail="StoryBoard metadata not found for this task")
        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")

        session_id = int(task_row["session_id"])
        batch_id = f"{now_ms()}_{uuid.uuid4().hex[:6]}"
        target_dir = workspace / "uploads" / "storyboard" / "asset_pool" / "manual" / batch_id
        target_dir.mkdir(parents=True, exist_ok=True)
        uploaded_assets = [item for item in meta.get("uploaded_assets") or [] if isinstance(item, dict)]
        added: list[dict[str, Any]] = []
        for index, upload in enumerate(files, start=1):
            if not is_supported_image_upload(upload):
                continue
            data = await upload.read()
            if not data:
                continue
            target = target_dir / safe_upload_name(upload.filename or "", f"manual_{index:03d}")
            target.write_bytes(data)
            added.append({
                "id": f"manual-upload-{batch_id}-{index:03d}",
                "path": rel_path(workspace, target),
                "filename": Path(upload.filename or target.name).name,
                "label": Path(upload.filename or target.name).name,
                "original_relative_path": upload.filename or "",
                "content_type": upload.content_type or "",
                "source": "manual_upload",
                "pool_section": "manual",
                "resource_session_id": session_id,
                "role": "pool",
                "upload_batch_id": batch_id,
                "uploaded_at": now_ms(),
            })
        if not added:
            raise HTTPException(status_code=400, detail="No supported image files uploaded")

        meta["uploaded_assets"] = [*uploaded_assets, *added]
        meta["manual_asset_uploads"] = [*(meta.get("manual_asset_uploads") if isinstance(meta.get("manual_asset_uploads"), list) else []), {
            "batch_id": batch_id,
            "count": len(added),
            "root": rel_path(workspace, target_dir),
            "created_at": now_ms(),
        }]
        write_json_file(workspace / "storyboard_meta.json", meta)
        add_event(session_id, "ocstoryboard.assets.uploaded", {"task_id": task_id, "batch_id": batch_id, "count": len(added)})
        return serialize_storyboard(get_task(task_id))

    @router.delete("/api/ocstoryboard/tasks/{task_id}/assets/{asset_id}")
    async def delete_storyboard_asset(task_id: int, asset_id: str) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        meta = read_json_file(workspace / "storyboard_meta.json")
        if not meta:
            raise HTTPException(status_code=404, detail="StoryBoard metadata not found for this task")

        uploaded_assets = [item for item in meta.get("uploaded_assets") or [] if isinstance(item, dict)]
        reference_assets = [item for item in meta.get("reference_asset_pool") or [] if isinstance(item, dict)]
        asset = next((item for item in uploaded_assets if str(item.get("id") or "") == asset_id), None)
        if not asset:
            asset = next((item for item in reference_assets if str(item.get("id") or "") == asset_id), None)
        if not asset:
            raise HTTPException(status_code=404, detail="Uploaded asset not found")
        if str(asset.get("pool_section") or "") != "manual" and str(asset.get("source") or "") not in {"manual_upload", "upload"}:
            raise HTTPException(status_code=400, detail="Only uploaded assets can be deleted")

        asset_path = str(asset.get("path") or "").strip()
        if asset_path:
            target = (workspace / asset_path).resolve()
            root = workspace.resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Asset path is outside workspace") from exc
            if target.is_file() and asset_path.startswith("uploads/storyboard/"):
                target.unlink()

        plan = read_storyboard_edit_plan(workspace)
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            for mark in normalize_scene_marks(shot, allow_image_fallback=False):
                dialogue_asset = mark.get("storyboard_dialogue_asset") if isinstance(mark.get("storyboard_dialogue_asset"), dict) else {}
                if dialogue_asset and (str(dialogue_asset.get("asset_id") or "") == asset_id or str(dialogue_asset.get("path") or "") == asset_path):
                    mark.pop("storyboard_dialogue_asset", None)
                keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
                changed_keyframes = False
                for role in ("single", "first", "last"):
                    if str(keyframes.get(role) or "") == asset_path:
                        keyframes[role] = ""
                        changed_keyframes = True
                paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
                next_paths = [value for value in paths if str(value or "") != asset_path]
                if len(next_paths) != len(paths):
                    keyframes["paths"] = next_paths
                    changed_keyframes = True
                asset_ids = mark.get("keyframe_asset_ids") if isinstance(mark.get("keyframe_asset_ids"), dict) else {}
                for role, value in list(asset_ids.items()):
                    if str(value or "") == asset_id:
                        asset_ids.pop(role, None)
                        changed_keyframes = True
                if asset_ids:
                    mark["keyframe_asset_ids"] = asset_ids
                else:
                    mark.pop("keyframe_asset_ids", None)
                if changed_keyframes:
                    mark["keyframes"] = keyframes

        meta["uploaded_assets"] = [item for item in uploaded_assets if str(item.get("id") or "") != asset_id]
        meta["reference_asset_pool"] = [item for item in reference_assets if str(item.get("id") or "") != asset_id]
        sync_storyboard_files(workspace, task_row, plan, meta, write_rebuild_plan=False, write_dialogue_plan=True, allow_image_fallback=False)
        add_event(int(task_row["session_id"]), "ocstoryboard.assets.deleted", {"task_id": task_id, "asset_id": asset_id, "path": asset_path})
        return serialize_storyboard(get_task(task_id))

    @router.delete("/api/ocstoryboard/tasks/{task_id}")
    async def delete_storyboard(task_id: int) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        meta = read_json_file(workspace / "storyboard_meta.json")
        if not meta:
            raise HTTPException(status_code=404, detail="StoryBoard metadata not found for this task")
        session_row = ctx.session_repo.get(session_id) or {"id": session_id, "workspace_dir": str(workspace)}
        ctx.workflow_deletion_service.delete_session_db_first(session_row)
        try:
            ctx.workflow_deletion_service.cleanup_workspace(session_row)
        except Exception as exc:
            ctx.event("warning", "cleanup", "Workspace cleanup failed after StoryBoard DB deletion", {"session_id": session_id, "task_id": task_id, "error": str(exc)})
        return {"ok": True, "deleted_id": task_id, "deleted_session_id": session_id}

    @router.put("/api/ocstoryboard/tasks/{task_id}")
    async def save_storyboard(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        meta = read_json_file(workspace / "storyboard_meta.json")
        if not meta:
            raise HTTPException(status_code=404, detail="StoryBoard metadata not found for this task")
        plan = payload.get("shot_plan") if isinstance(payload.get("shot_plan"), dict) else payload
        sync_storyboard_files(
            workspace,
            task_row,
            plan,
            meta,
            write_rebuild_plan=False,
            write_dialogue_plan=True,
            allow_image_fallback=False,
        )
        repo.update_task(task_id, status="storyboard_editing", updated_at=now_ms())
        add_event(int(task_row["session_id"]), "ocstoryboard.saved", {"task_id": task_id, "shot_count": len(plan.get("shots") or [])})
        return serialize_storyboard(get_task(task_id))

    @router.post("/api/ocstoryboard/tasks/{task_id}/finalize")
    async def finalize_storyboard(task_id: int) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        meta = read_json_file(workspace / "storyboard_meta.json")
        if not meta:
            raise HTTPException(status_code=404, detail="StoryBoard metadata not found for this task")
        plan = read_storyboard_edit_plan(workspace)
        add_event(int(task_row["session_id"]), "ocstoryboard.phase2_refresh.started", {"task_id": task_id})
        refreshed = await refresh_storyboard_phase2_outputs(task_row, plan, meta)
        repo.update_task(task_id, status="draft", updated_at=now_ms())
        add_event(
            int(task_row["session_id"]),
            "ocstoryboard.phase2_refresh.completed",
            {"task_id": task_id, "cleaned_count": len(refreshed.get("cleaned_outputs") or []), "step_count": len(refreshed.get("steps") or [])},
        )
        return serialize_storyboard(get_task(task_id))

    @router.post("/api/ocstoryboard/tasks/{task_id}/refresh-phase2")
    async def refresh_phase2_storyboard(task_id: int) -> dict[str, Any]:
        return await finalize_storyboard(task_id)

    return router
