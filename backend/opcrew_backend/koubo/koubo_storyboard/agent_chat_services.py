from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import *


AGENT_CHAT_CONTEXT_CHAR_LIMIT = 18000
AGENT_CHAT_PLAN_SHOT_LIMIT = 24


def _text(value: Any, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


def _redacted_compact_json(value: Any, limit: int = AGENT_CHAT_CONTEXT_CHAR_LIMIT, *, sc: Any) -> str:
    try:
        raw = json.dumps(sc.redact_payload(value), ensure_ascii=False, default=str, indent=2)
    except Exception:
        raw = str(value)
    if len(raw) <= limit:
        return raw
    return f"{raw[:limit]}\n... truncated ..."


def _safe_read(path: Path, *, sc: Any) -> dict[str, Any]:
    try:
        payload = sc.read_json(path)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        return {"error": str(exc)}


SERVICE_EXPORTS = (
    "agent_chat_asset_summary",
    "agent_chat_asset_kind",
    "agent_chat_compact_asset",
    "agent_chat_client_media_assets",
    "agent_chat_media_asset_summary",
    "agent_chat_plan_summary",
    "agent_chat_saved_storyboard",
    "agent_chat_base_context",
    "agent_chat_video_plan_settings",
    "agent_chat_video_plan_context",
    "agent_chat_image_plan_context",
    "agent_chat_composer_context",
    "agent_chat_storyboard_edit_context",
    "agent_chat_asset_media_context",
    "agent_chat_prompt_agent_context",
    "agent_chat_context",
    "agent_chat_system_prompt",
)


def agent_chat_asset_summary(workspace: Path, *, sc: Any) -> dict[str, Any]:
    store = _safe_read(workspace / ASSETS_REL, sc=sc)
    assets = store.get("assets") if isinstance(store.get("assets"), list) else []
    items: list[dict[str, Any]] = []
    for asset in assets[:80]:
        if not isinstance(asset, dict):
            continue
        items.append({
            "path": _text(asset.get("path") or asset.get("id")),
            "label": _text(asset.get("label") or asset.get("filename")),
            "kind": _text(asset.get("kind") or asset.get("asset_type")),
            "source": _text(asset.get("source")),
        })
    return {"count": len(assets), "items": items}


def agent_chat_asset_kind(asset: dict[str, Any]) -> str:
    explicit = _text(asset.get("kind") or asset.get("asset_type")).lower()
    if "audio" in explicit:
        return "audio"
    if "video" in explicit:
        return "video"
    if "image" in explicit:
        return "image"
    path = _text(asset.get("path") or asset.get("history_path") or asset.get("filename"))
    suffix = Path(path).suffix.lower()
    if suffix in AUDIO_EXTS:
        return "audio"
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in IMAGE_EXTS:
        return "image"
    return explicit or "other"


def agent_chat_compact_asset(asset: dict[str, Any]) -> dict[str, Any]:
    path = _text(asset.get("path") or asset.get("history_path") or asset.get("id"))
    return {
        "id": _text(asset.get("id") or path),
        "path": path,
        "label": _text(asset.get("label") or asset.get("filename") or Path(path).name),
        "kind": agent_chat_asset_kind(asset),
        "source": _text(asset.get("source")),
        "duration": asset.get("duration") if asset.get("duration") is not None else asset.get("duration_seconds"),
        "shot_id": _text(asset.get("shot_id")),
        "scene_id": _text(asset.get("scene_id")),
        "dialogue_id": _text(asset.get("dialogue_id")),
    }


def agent_chat_client_media_assets(client_context: dict[str, Any], media_kind: str) -> dict[str, Any]:
    sources = []
    for key in ("visible_assets", "assets", "all_assets", "current_assets"):
        value = client_context.get(key)
        if isinstance(value, list):
            sources.extend(value)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    count = 0
    for item in sources:
        if not isinstance(item, dict):
            continue
        compact = agent_chat_compact_asset(item)
        if compact["kind"] != media_kind:
            continue
        key = compact["path"] or compact["id"]
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        count += 1
        if len(items) < 80:
            items.append(compact)
    return {"kind": media_kind, "count": count, "items": items, "truncated": count > len(items), "limit": 80}


def agent_chat_media_asset_summary(workspace: Path, media_kind: str, *, sc: Any) -> dict[str, Any]:
    media_kind = "audio" if media_kind == "audio" else "video"
    store = _safe_read(workspace / ASSETS_REL, sc=sc)
    manifest_assets = store.get("assets") if isinstance(store.get("assets"), list) else []
    pool_assets: list[dict[str, Any]] = []
    try:
        meta = sc.asset_pool_meta(workspace, sc=sc)
        key = "uploaded_audios" if media_kind == "audio" else "uploaded_videos"
        pool_assets = meta.get(key) if isinstance(meta.get(key), list) else []
    except Exception:
        pool_assets = []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for asset in [*pool_assets, *manifest_assets]:
        if not isinstance(asset, dict):
            continue
        compact = agent_chat_compact_asset(asset)
        if compact["kind"] != media_kind:
            continue
        key = compact["path"] or compact["id"]
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        items.append(compact)
    return {
        "kind": media_kind,
        "count": len(items),
        "items": items[:80],
        "truncated": len(items) > 80,
        "limit": 80,
        "manifest_count": len([item for item in manifest_assets if isinstance(item, dict) and agent_chat_asset_kind(item) == media_kind]),
        "pool_count": len([item for item in pool_assets if isinstance(item, dict) and agent_chat_asset_kind(item) == media_kind]),
    }


def agent_chat_plan_summary(plan: dict[str, Any], meta: dict[str, Any] | None = None, *, sc: Any) -> dict[str, Any]:
    shots = plan.get("shots") if isinstance(plan.get("shots"), list) else []
    compact_shots = []
    for index, shot in enumerate(shots[:AGENT_CHAT_PLAN_SHOT_LIMIT], start=1):
        if not isinstance(shot, dict):
            continue
        compact_shots.append({
            "index": index,
            "shot_id": _text(shot.get("shot_id")),
            "shot_name": _text(shot.get("shot_name")),
            "duration": shot.get("duration"),
            "scene_count": len(shot.get("scenes") if isinstance(shot.get("scenes"), list) else []),
            "summary": _redacted_compact_json(shot, 1400, sc=sc),
        })
    return {
        "meta": meta or {},
        "title": plan.get("title"),
        "shot_count": len(shots),
        "shots": compact_shots,
    }


def agent_chat_saved_storyboard(task: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    try:
        plan, meta = sc.load_plan(task, sc=sc)
        return agent_chat_plan_summary(plan, meta, sc=sc)
    except Exception as exc:
        return {"error": str(exc)}


def agent_chat_base_context(task: dict[str, Any], client_context: dict[str, Any] | None = None, *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    return {
        "task": {
            "id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "title": _text(task.get("title") or task.get("name")),
            "status": _text(task.get("status")),
        },
        "workspace": {"root": str(workspace)},
        "client_context": client_context if isinstance(client_context, dict) else {},
    }


def agent_chat_video_plan_settings(workspace: Path, *, sc: Any) -> dict[str, Any]:
    settings_path = workspace / VIDEO_PLAN_SETTINGS_REL
    settings = _safe_read(settings_path, sc=sc)
    try:
        normalized = sc.video_plan_settings({"settings": settings})
    except Exception as exc:
        return {"error": str(exc), "path": VIDEO_PLAN_SETTINGS_REL}
    return {"path": VIDEO_PLAN_SETTINGS_REL, "settings": normalized, "raw": settings if settings else {}}


def agent_chat_video_plan_context(task: dict[str, Any], client_context: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    plan = _safe_read(workspace / VIDEO_PLAN_REL, sc=sc)
    execution_payload: dict[str, Any] = {}
    if plan:
        try:
            execution_payload = sc.video_plan_execution_payload(workspace, plan, sc=sc)
        except Exception as exc:
            execution_payload = {"error": str(exc)}
    return {
        **agent_chat_base_context(task, client_context, sc=sc),
        "storyboard": agent_chat_saved_storyboard(task, sc=sc),
        "video_plan": {
            "path": VIDEO_PLAN_REL,
            "plan": sc.redact_payload(plan),
            "settings": agent_chat_video_plan_settings(workspace, sc=sc),
            "execution": execution_payload,
            "execution_state": sc.redact_payload(_safe_read(workspace / VIDEO_PLAN_EXECUTION_STATE_REL, sc=sc)),
            "execution_result": sc.redact_payload(_safe_read(workspace / VIDEO_PLAN_EXECUTION_RESULT_REL, sc=sc)),
        },
    }


def agent_chat_image_plan_context(task: dict[str, Any], client_context: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    plan = _safe_read(workspace / IMAGE_PLAN_REL, sc=sc)
    artifact_status: dict[str, Any] = {}
    if callable(getattr(sc, "image_plan_artifact_status", None)) and plan:
        try:
            artifact_status = sc.image_plan_artifact_status(workspace, plan)
        except Exception as exc:
            artifact_status = {"error": str(exc)}
    return {
        **agent_chat_base_context(task, client_context, sc=sc),
        "storyboard": agent_chat_saved_storyboard(task, sc=sc),
        "image_plan": {
            "path": IMAGE_PLAN_REL,
            "plan": sc.redact_payload(plan),
            "artifact_status": sc.redact_payload(artifact_status),
            "execution_state": sc.redact_payload(_safe_read(workspace / IMAGE_PLAN_EXECUTION_STATE_REL, sc=sc)),
            "execution_result": sc.redact_payload(_safe_read(workspace / IMAGE_PLAN_EXECUTION_RESULT_REL, sc=sc)),
            "consistency_references": sc.redact_payload(sc.video_plan_consistency_reference_snapshot(workspace, sc=sc)),
        },
    }


def agent_chat_composer_context(task: dict[str, Any], client_context: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    requested_target = client_context.get("requestedTarget") or client_context.get("requested_target") or client_context.get("target") or {}
    candidates_payload: dict[str, Any] = {}
    try:
        candidates_payload = sc.composer_candidates_payload(task, {"target": requested_target} if isinstance(requested_target, dict) else {}, sc=sc)
    except Exception as exc:
        candidates_payload = {"error": str(exc)}
    try:
        execution_payload = sc.composer_execution_payload(task, sc=sc)
    except Exception as exc:
        execution_payload = {"error": str(exc)}
    return {
        **agent_chat_base_context(task, client_context, sc=sc),
        "storyboard": agent_chat_saved_storyboard(task, sc=sc),
        "composer": {
            "candidates": sc.redact_payload(candidates_payload),
            "execution": sc.redact_payload(execution_payload),
            "video_plan": sc.redact_payload(_safe_read(workspace / VIDEO_PLAN_REL, sc=sc)),
            "compose_result": sc.redact_payload(_safe_read(workspace / COMPOSER_RESULT_REL, sc=sc)),
            "compose_state": sc.redact_payload(_safe_read(workspace / COMPOSER_EXECUTION_STATE_REL, sc=sc)),
        },
    }


def agent_chat_storyboard_edit_context(task: dict[str, Any], client_context: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    return {
        **agent_chat_base_context(task, client_context, sc=sc),
        "storyboard": agent_chat_saved_storyboard(task, sc=sc),
        "assets": agent_chat_asset_summary(workspace, sc=sc),
        "allowed_operations": [
            "replace_dialogue_text",
            "update_dialogue_duration",
            "update_shot_name",
            "add_dialogue_after",
            "split_scene_after_dialogue",
            "split_shot_after_dialogue",
            "merge_dialogue_up",
            "merge_scene_up",
        ],
    }


def agent_chat_asset_media_context(task: dict[str, Any], client_context: dict[str, Any], media_kind: str, *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    media_kind = "audio" if media_kind == "audio" else "video"
    workspace_name = "Audio" if media_kind == "audio" else "Videos"
    return {
        **agent_chat_base_context(task, client_context, sc=sc),
        "storyboard": agent_chat_saved_storyboard(task, sc=sc),
        "asset_library": {
            "workspace": workspace_name,
            "media_kind": media_kind,
            "storage_dir": ASSET_AUDIOS_REL if media_kind == "audio" else ASSET_VIDEOS_REL,
            "assets": agent_chat_media_asset_summary(workspace, media_kind, sc=sc),
            "client_assets": agent_chat_client_media_assets(client_context, media_kind),
            "related_bind_targets": {
                "audio": ["dialogue.audio_path", "Working/*/Audio_Final.*"],
                "video": ["dialogue.video_path", "Working/*/Video_Final.*"],
            }[media_kind],
        },
    }


def agent_chat_prompt_agent_context(task: dict[str, Any], client_context: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    prompt_context = client_context.get("prompt_agent") if isinstance(client_context.get("prompt_agent"), dict) else {}
    selected_assets = client_context.get("selected_reference_assets") if isinstance(client_context.get("selected_reference_assets"), list) else []
    knowledge = client_context.get("knowledge") if isinstance(client_context.get("knowledge"), dict) else {}
    knowledge_items = knowledge.get("items") if isinstance(knowledge.get("items"), list) else []
    return {
        **agent_chat_base_context(task, client_context, sc=sc),
        "prompt_agent": {
            "mode": _text(prompt_context.get("mode") or client_context.get("mode"), "optimize"),
            "model_family": _text(prompt_context.get("model_family") or client_context.get("model_family"), "image"),
            "provider": _text(prompt_context.get("provider") or client_context.get("provider")),
            "model": _text(prompt_context.get("model") or client_context.get("model")),
            "current_prompt": _text(prompt_context.get("prompt") or client_context.get("prompt")),
            "reference_assets": sc.redact_payload(selected_assets[:20]),
            "knowledge": {
                "retrieval_id": _text(knowledge.get("retrieval_id")),
                "items": sc.redact_payload(knowledge_items[:8]),
            },
            "enabled_modes": ["critique", "optimize", "rewrite", "adapt"],
            "disabled_modes": ["compare"],
        },
    }


def agent_chat_context(agent_key: str, task: dict[str, Any], client_context: dict[str, Any] | None = None, *, sc: Any) -> dict[str, Any]:
    payload = client_context if isinstance(client_context, dict) else {}
    if agent_key == "storyboard_edit":
        return agent_chat_storyboard_edit_context(task, payload, sc=sc)
    if agent_key == "image_plan":
        return agent_chat_image_plan_context(task, payload, sc=sc)
    if agent_key == "video_plan":
        return agent_chat_video_plan_context(task, payload, sc=sc)
    if agent_key == "composer":
        return agent_chat_composer_context(task, payload, sc=sc)
    if agent_key == "asset_audio":
        return agent_chat_asset_media_context(task, payload, "audio", sc=sc)
    if agent_key == "asset_video":
        return agent_chat_asset_media_context(task, payload, "video", sc=sc)
    if agent_key == "prompt_agent":
        return agent_chat_prompt_agent_context(task, payload, sc=sc)
    return agent_chat_base_context(task, payload, sc=sc)


def agent_chat_system_prompt(agent_key: str, task: dict[str, Any], client_context: dict[str, Any] | None = None, *, sc: Any) -> str:
    context_text = _redacted_compact_json(agent_chat_context(agent_key, task, client_context, sc=sc), sc=sc)
    if agent_key == "storyboard_edit":
        return f"""你是 Koubo StoryBoard 口播编辑助手。

边界：
- 只能基于上下文提出编辑建议，不要声称已经保存。
- 不要调用工具、读取文件、写文件、访问网络或执行命令。
- 如果给出可应用修改，必须输出一个 <STORYBOARD_EDIT_CANDIDATE> JSON 块。
- operations 只能使用上下文中 allowed_operations；所有操作必须引用现有 shot_id / scene_id / dialogue_id。
- 不允许删除 dialogue，不允许标记口播/空镜，不允许自动保存或运行 TTS/ImagePlan/VideoPlan/Composer。

候选格式：
<STORYBOARD_EDIT_CANDIDATE>{{"title":"简短标题","summary":"说明","operations":[{{"type":"replace_dialogue_text","dialogue_id":"...","text":"..."}}],"warnings":[]}}</STORYBOARD_EDIT_CANDIDATE>

当前上下文：
{context_text}
"""
    if agent_key == "image_plan":
        return f"""你是 Koubo ImagePlan 任务助手。

边界：
- 只解释状态、优化 image prompt、建议执行步骤。
- 不要声称已经保存 Prompt 或生成图片；这些动作必须由用户点击前端按钮完成。
- 不要调用工具、读取文件、写文件、访问网络或执行命令。
- 优化单个图片提示词时输出 <IMAGE_PLAN_CANDIDATE> JSON。
- 建议执行步骤时输出 <IMAGE_PLAN_ACTION> JSON。

候选格式：
<IMAGE_PLAN_CANDIDATE>{{"title":"标题","kind":"image_prompt","asset_key":"...","positive_prompt":"...","negative_prompt":"...","notes":"..."}}</IMAGE_PLAN_CANDIDATE>
<IMAGE_PLAN_ACTION>{{"title":"标题","action":"execute_image_plan","mode":"prompt-only","target_asset_key":"","reason":"..."}}</IMAGE_PLAN_ACTION>

当前上下文：
{context_text}
"""
    if agent_key == "video_plan":
        return f"""你是 Koubo VideoPlan 任务助手。

边界：
- 只解释计划、blocked/failed 原因，并建议参数或重跑步骤。
- 不要声称已经生成或执行 VideoPlan；这些动作必须由用户确认按钮完成。
- 不要调用工具、读取文件、写文件、访问网络或执行命令。
- 可操作建议必须输出 <VIDEO_PLAN_ACTION> JSON。

候选格式：
<VIDEO_PLAN_ACTION>{{"title":"标题","action":"open_video_plan","target":{{"target_type":"task","shot_id":"","scene_id":""}},"settings":{{"max_video_seconds":8,"min_video_seconds":2,"split_tolerance_seconds":2}},"reason":"..."}}</VIDEO_PLAN_ACTION>

当前上下文：
{context_text}
"""
    if agent_key == "asset_audio":
        return f"""你是 Koubo Asset Library 的 Audio 工作区助手。

职责：
- 帮用户理解当前 Audio 素材池、命名、质量、口播/TTS 关系和 StoryBoard 绑定位置。
- 可以建议上传、替换、重命名、分组、试听检查、TTS 重跑或绑定到具体 dialogue，但只能提出建议。
- 优先基于上下文中的 asset_library.assets、asset_library.client_assets 和 storyboard 判断。

边界：
- 不要声称已经上传、生成、删除、重命名、绑定或保存任何音频。
- 不要调用工具、读取文件、写文件、访问网络或执行命令。
- 不要编造不存在的音频文件；如果上下文没有音频素材，要明确说明当前 Audio workspace 为空。
- 给结构化建议时必须输出 <ASSET_AUDIO_ADVICE> JSON。

候选格式：
<ASSET_AUDIO_ADVICE>{{"title":"标题","summary":"一句话结论","findings":[{{"severity":"info","message":"...","evidence":["..."]}}],"next_actions":[{{"label":"上传音频素材","action":"upload_audio","target":{{"path":"","dialogue_id":"","shot_id":"","scene_id":""}},"confirm":true}}]}}</ASSET_AUDIO_ADVICE>

当前上下文：
{context_text}
"""
    if agent_key == "asset_video":
        return f"""你是 Koubo Asset Library 的 Videos 工作区助手。

职责：
- 帮用户理解当前 Videos 素材池、可用片段、命名、比例、时长、口播画面替换关系和 StoryBoard/VideoPlan/Composer 使用方式。
- 可以建议上传、替换、重命名、分组、预览检查、生成 VideoPlan 或绑定到具体 dialogue。
- 当用户明确要求生成视频素材时，你可以发起一次受控视频生成请求。你自己不会直接生成或保存视频；后端会读取请求并调用 Connection 中配置的视频生成 API。
- 优先基于上下文中的 asset_library.assets、asset_library.client_assets 和 storyboard 判断。

边界：
- 不要声称已经上传、生成、删除、重命名、绑定、合成或保存任何视频；视频生成完成状态由后端事件通知。
- 不要调用工具、读取文件、写文件、访问网络或执行命令。
- 不要编造不存在的视频文件；如果上下文没有视频素材，要明确说明当前 Videos workspace 为空。
- 给结构化建议时必须输出 <ASSET_VIDEO_ADVICE> JSON。
- 发起视频生成时必须只输出一个 <VIDEO_GENERATION_REQUEST> JSON 块；不要同时声称已完成。

候选格式：
<ASSET_VIDEO_ADVICE>{{"title":"标题","summary":"一句话结论","findings":[{{"severity":"info","message":"...","evidence":["..."]}}],"next_actions":[{{"label":"上传视频素材","action":"upload_video","target":{{"path":"","dialogue_id":"","shot_id":"","scene_id":""}},"confirm":true}}]}}</ASSET_VIDEO_ADVICE>
<VIDEO_GENERATION_REQUEST>{{"title":"简短标题","prompt":"完整视频生成提示词","duration":4,"aspect":"9:16","reference_images":[],"reference_audios":[],"reference_videos":[],"reference_mode":"","operation":"","video_thread_id":"","parent_turn_id":"","provider":"","model":"","notes":"简短说明"}}</VIDEO_GENERATION_REQUEST>

生成请求要求：
- prompt 必须独立完整，包含主体、动作、镜头、光线、风格、时长和画幅意图。
- duration 建议 4 到 8 秒；aspect 只能是 9:16 或 16:9。
- reference_images 只能使用上下文或用户消息中真实出现的图片路径；没有参考图时传空数组。
- reference_audios/reference_videos 只能使用上下文或用户消息中真实出现的音频/视频路径；没有对应参考时传空数组。
- 当用户选择或明确要求 Max SR2 / Seedance SR2 图片+声音+视频参考时，reference_images 最多 8 张、reference_audios/reference_videos 各最多 4 个，reference_mode 填 input_references；Max SI2 必须用 1 个 reference_images，留空或 first_frame；Max WR2.7 是 wan2.7-r2v 参考生视频，reference_images + reference_videos 合计 1-5 个，可只用多张图片，也可图片+视频，reference_mode 留空；Max HR1.0 用 1-3 个 reference_images。
- provider/model 通常留空，让后端使用 Connection 中启用的视频模型；只有用户明确指定时才填写。
- 使用有状态编辑时只能引用上下文给出的 OpenCrew video_thread_id / video_turn_id，并把后者写入 parent_turn_id；绝不能请求、猜测或输出供应商 Interaction ID。

当前上下文：
{context_text}
"""
    if agent_key == "prompt_agent":
        return f"""你是 Koubo Asset Library 的提示词优化 Agent。

任务：根据用户目标、目标模型和参考素材，按当前 mode 批注、优化、改写或做模型适配。支持 critique、optimize、rewrite、adapt 四种 mode；compare 暂不执行。当前 mode 见上下文 prompt_agent.mode。

边界：
1. 保留用户原始意图，不擅自替换产品、人物、品牌、画幅和核心动作。
2. 不要声称已保存、已应用或已生成；保存/应用/生成由用户点击前端按钮完成。
3. 不要调用工具、读写文件、访问网络或执行命令。
4. 没有知识库来源时 used_sources 必须返回 []，不得编造 doc_id、标题或来源。
5. 使用知识库时，used_sources 的 doc_id 只能来自当前上下文 prompt_agent.knowledge.items。

批注/优化清单：
- 先批注、再给可用改写；不改用户核心意图。
- 通用：主体是否明确、结构是否完整、负向约束是否缺失、是否存在互相冲突的描述。
- 图像模型：主体、构图、光线、参考图角色与顺序、画幅比例、风格边界、negative prompt。
- 视频模型：首帧/尾帧、动作幅度、镜头运动与稳定性、时长、口型、文字风险、声音责任边界（不在画面 prompt 里承诺读字幕，声音交 TTS）。
- 数字人模型：脚本、语气、Avatar、Voice、口型驱动方式、音频来源。
- 严重度只能用 high、medium、low；每条 issue 给 span、problem、why_it_matters、suggestion。

各 mode 行为：
- critique：只批注，给 issues 与 model_notes，revised_prompt 可为空。
- optimize：保留原意，补齐结构、模型关键字、约束与负向项，给出 revised_prompt。
- rewrite：不改变核心意图，重写表达使其更清晰可用，给出 revised_prompt 并在 changes 说明改写点。
- adapt：把提示词适配到上下文给定的 model_family/provider/model，给出 revised_prompt，并在 model_notes 说明该模型的关键差异与注意事项。

输出：
- 正文先用自然语言简述关键结论。
- 必须输出且只输出一个结构化结果标签块：
<PROMPT_AGENT_RESULT>{{"mode":"optimize","summary":"一句话总结","issues":[{{"severity":"high","span":"原文片段","problem":"问题","why_it_matters":"影响","suggestion":"建议"}}],"revised_prompt":"优化后的提示词","negative_prompt":"负向提示词，没有则空字符串","changes":["改动说明"],"model_notes":["模型注意事项"],"used_sources":[]}}</PROMPT_AGENT_RESULT>
- mode 必须等于当前请求的 mode（critique、optimize、rewrite 或 adapt）。critique 时 revised_prompt 可为空，但仍要给 issues 与 model_notes；optimize、rewrite、adapt 必须给出 revised_prompt。
- 如果上下文含 prompt_agent.knowledge.items，请优先参考其中 summary/rules，并在 used_sources 中列出实际使用的 doc_id/title/trust_level/reason。

当前上下文：
{context_text}
"""
    return f"""你是 Koubo Composer 合成结果诊断助手。

边界：
- 只解释 Composer 候选、scope mismatch、缺失片段、执行状态和下一步。
- 不要声称已经合成视频；合成和重跑 VideoPlan 必须由用户确认按钮完成。
- 不要调用工具、读取文件、写文件、访问网络或执行命令。
- 诊断建议必须输出 <COMPOSER_DIAGNOSIS> JSON。

候选格式：
<COMPOSER_DIAGNOSIS>{{"title":"标题","findings":[{{"severity":"warning","message":"...","evidence":["..."]}}],"next_actions":[{{"label":"生成整片 VideoPlan","action":"generate_task_video_plan","target":{{"target_type":"task","shot_id":"","scene_id":""}},"confirm":true}}]}}</COMPOSER_DIAGNOSIS>

当前上下文：
{context_text}
"""


def register_agent_chat_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
