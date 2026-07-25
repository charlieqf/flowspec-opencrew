from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from opcrew_backend.context import now_ms
from opcrew_backend.model_policy import SURFACE_KOUBO_ASSET_AGENT_CHAT, SURFACE_KOUBO_TTS_TIMING, hidden_model_defaults_for_role, mask_model_fields_for_role, request_role
from opcrew_backend.routes.media_model_config import load_config
from opcrew_backend.services.tts_voice_aliases import PUBLIC_TTS_VOICE_PREFIX, resolve_tts_voice_alias

from .constants import *
from .tts_public_aliases import PUBLIC_TTS_PROVIDER_PREFIX, resolve_tts_public_alias


CLOUD_VOICE_CLONES_REL = "SessionOutput/tts/cloud_voice_clones.json"
COSYVOICE_CLONE_MODELS = ("cosyvoice-v3.5-flash", "cosyvoice-v3.5-plus", "cosyvoice-v1")
TTS_AGENT_DRAFT_TIMEOUT_SECONDS = 45


def safe_voice_target_defaults(voice_target: dict[str, Any], clone_defaults: dict[str, str]) -> dict[str, str]:
    if clone_defaults:
        return clone_defaults
    provider = str(voice_target.get("provider") or "").strip()
    model = str(voice_target.get("model") or "").strip()
    voice_id = str(voice_target.get("voice_id") or "").strip()
    if model.startswith("cosyvoice-"):
        provider = "cosyvoice"
    elif model.startswith("heygen-"):
        provider = "heygen"
    if not provider or not model or "[model]" in provider.lower() or "[provider]" in provider.lower():
        return {}
    return {"provider": provider, "model": model, "voice_id": voice_id}


def register_tts_routes(router: APIRouter, deps: Any) -> None:

    def extract_json_object(value: str) -> dict[str, Any] | None:
        source = deps.text(value)
        if not source:
            return None
        fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", source, flags=re.IGNORECASE)
        if fenced:
            source = fenced.group(1)
        start = source.find("{")
        end = source.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(source[start:end + 1])
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def bounded_role_count(value: Any) -> int:
        try:
            count = int(value)
        except Exception:
            return 0
        return max(1, min(8, count))

    def repeated_single_role_mention_count(user_text: str) -> int:
        source = deps.text(user_text)
        matches = list(re.finditer(r"(?:一个|一位|一名|1\s*(?:个|位|名)?)\s*[A-Za-z0-9\u4e00-\u9fa5]{0,8}(?:角色|人物|人|speaker|声音|配音)", source, flags=re.IGNORECASE))
        if len(matches) < 2:
            return 0
        span = source[matches[0].start():matches[-1].end()]
        return bounded_role_count(len(matches)) if re.search(r"[和与跟、,，]", span) else 0

    def requested_role_count(user_text: str) -> int:
        source = deps.text(user_text)
        if not source:
            return 0
        digit = re.search(r"([2-8])\s*(?:个|位|名)?\s*(?:[A-Za-z0-9\u4e00-\u9fa5]{0,8})?(?:角色|人物|人|speaker|声音|配音)", source, flags=re.IGNORECASE)
        if digit:
            return bounded_role_count(digit.group(1))
        chinese = re.search(r"([二两三四五六七八])\s*(?:个|位|名)?\s*(?:[A-Za-z0-9\u4e00-\u9fa5]{0,8})?(?:角色|人物|人|speaker|声音|配音)", source, flags=re.IGNORECASE)
        if chinese:
            return bounded_role_count({"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}.get(chinese.group(1), 0))
        repeated_singles = repeated_single_role_mention_count(source)
        if repeated_singles:
            return repeated_singles
        if re.search(r"1\s*(?:个|位|名)?\s*(?:[A-Za-z0-9\u4e00-\u9fa5]{0,8})?(?:角色|人物|人|speaker|声音|配音)", source, flags=re.IGNORECASE):
            return 1
        if re.search(r"一\s*(?:个|位|名)?\s*(?:[A-Za-z0-9\u4e00-\u9fa5]{0,8})?(?:角色|人物|人|speaker|声音|配音)", source, flags=re.IGNORECASE):
            return 1
        if re.search(r"单人|单个角色|单角色|一个人|一个角色|一位角色|一名角色|1\s*个角色", source, flags=re.IGNORECASE):
            return 1
        if re.search(r"双人|两人|二人|两个角色|两个.*配音|2\s*个角色", source, flags=re.IGNORECASE):
            return 2
        if "多人" in source or "多个角色" in source:
            return 3
        return 0

    def requested_role_gender(user_text: str) -> str:
        source = deps.text(user_text).lower()
        if re.search(r"女|女性|女生|女声|female", source):
            return "female"
        if re.search(r"男|男性|男生|男声|male", source):
            return "male"
        return ""

    def default_role_name(index: int, user_text: str) -> str:
        gender = requested_role_gender(user_text)
        if index == 0 and gender == "female":
            return "女性角色"
        if index == 0 and gender == "male":
            return "男性角色"
        return f"角色{chr(ord('A') + min(index, 25))}"

    def implied_role_count(user_text: str) -> int:
        requested = requested_role_count(user_text)
        if requested:
            return requested
        return 2 if re.search(r"对话|互相|交流", deps.text(user_text)) else 1

    def fallback_role_names(user_text: str) -> list[str]:
        source = deps.text(user_text)
        target_count = implied_role_count(source)
        names: list[str] = []
        blocked = ("角色", "生成", "帮我", "调整", "对话", "两个", "一个", "多人", "人的")
        for match in re.finditer(r"一个\s*([A-Za-z0-9\u4e00-\u9fa5]{1,12})", source):
            name = deps.text(match.group(1))
            if name and not any(token in name for token in blocked) and name not in names:
                names.append(name)
        if len(names) < 2:
            scoped = source[source.find("角色"):] if "角色" in source else source
            for match in re.finditer(r"([A-Za-z0-9\u4e00-\u9fa5]{1,12})[和与跟、,，]\s*([A-Za-z0-9\u4e00-\u9fa5]{1,12})", scoped):
                for raw in (match.group(1), match.group(2)):
                    name = deps.text(raw).lstrip("一个")
                    if name and not any(token in name for token in blocked) and name not in names:
                        names.append(name)
        if not names:
            names = [default_role_name(index, source) for index in range(target_count)]
        while len(names) < target_count:
            names.append(default_role_name(len(names), source))
        return names[:target_count or 8]

    def normalize_tts_agent_draft(raw: dict[str, Any], user_text: str, voices: list[str], source: str, error: str = "") -> dict[str, Any]:
        voice_pool = [deps.text(item) for item in voices if deps.text(item)] or ["Kore", "Puck", "Zephyr", "Charon"]
        raw_roles = raw.get("roles") if isinstance(raw.get("roles"), list) else []
        requested_count = requested_role_count(user_text)
        roles: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_roles):
            if not isinstance(item, dict):
                continue
            speaker = deps.text(item.get("speaker") or item.get("name"))
            if not speaker or speaker in seen:
                continue
            seen.add(speaker)
            roles.append({
                "speaker": speaker[:24],
                "speaker_id": deps.safe_name(f"speaker_{speaker}", f"speaker_{index + 1}"),
                "voice": deps.text(item.get("voice")) or voice_pool[index % len(voice_pool)],
                "style": [deps.text(word) for word in (item.get("style") if isinstance(item.get("style"), list) else []) if deps.text(word)][:6] or ["自然口播", "清晰", "可信"],
                "pace": [deps.text(word) for word in (item.get("pace") if isinstance(item.get("pace"), list) else []) if deps.text(word)][:6] or ["中速", "句尾干净"],
            })
        if requested_count and len(roles) > requested_count:
            roles = roles[:requested_count]
            seen = {item["speaker"] for item in roles}
        if requested_count and len(roles) < requested_count:
            for speaker in fallback_role_names(user_text):
                if len(roles) >= requested_count:
                    break
                if speaker in seen:
                    continue
                index = len(roles)
                seen.add(speaker)
                roles.append({
                    "speaker": speaker,
                    "speaker_id": deps.safe_name(f"speaker_{speaker}", f"speaker_{index + 1}"),
                    "voice": voice_pool[index % len(voice_pool)],
                    "style": ["自然口播", "清晰", "可信"],
                    "pace": ["中速", "句尾干净"],
                })
        if not roles:
            for index, speaker in enumerate(fallback_role_names(user_text)):
                roles.append({
                    "speaker": speaker,
                    "speaker_id": deps.safe_name(f"speaker_{speaker}", f"speaker_{index + 1}"),
                    "voice": voice_pool[index % len(voice_pool)],
                    "style": ["自然口播", "清晰", "可信"],
                    "pace": ["中速", "句尾干净"],
                })
        role_by_name = {item["speaker"]: item for item in roles}
        raw_lines = raw.get("dialogues") or raw.get("lines")
        raw_lines = raw_lines if isinstance(raw_lines, list) else []
        dialogues: list[dict[str, Any]] = []
        for index, item in enumerate(raw_lines):
            if not isinstance(item, dict):
                continue
            line_text = deps.text(item.get("text") or item.get("line") or item.get("dialogue"))
            if not line_text:
                continue
            speaker = deps.text(item.get("speaker") or item.get("name"))
            role = role_by_name.get(speaker) or roles[index % len(roles)]
            dialogues.append({
                "speaker_id": role["speaker_id"],
                "speaker": role["speaker"],
                "voice": deps.text(item.get("voice")) or role["voice"],
                "text": line_text,
            })
        if not dialogues:
            templates = [
                "我们先确认这段语音的目标和语气。",
                "好的，我会把节奏放自然一点，也保留可以调整的空间。",
                "那我们再补充一点细节，让对话听起来更真实。",
                "没问题，最后生成一个可以拖拽使用的音频素材。",
            ]
            for index, line_text in enumerate(templates):
                role = roles[index % len(roles)]
                dialogues.append({"speaker_id": role["speaker_id"], "speaker": role["speaker"], "voice": role["voice"], "text": line_text})
        return {
            "ok": True,
            "source": source,
            "planner_error": error,
            "roles": roles[:8],
            "dialogues": dialogues[:40],
        }

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library/tts-agent/draft")
    async def build_tts_agent_draft(request: Request, task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        user_text = deps.text(payload.get("user_text") or payload.get("prompt"))
        voices = [deps.text(item) for item in (payload.get("voices") if isinstance(payload.get("voices"), list) else []) if deps.text(item)]
        if not user_text:
            raise HTTPException(status_code=400, detail="user_text is required")
        system_prompt = (
            "You are the OpenCrew TTS Agent planner. Convert the user's Chinese natural-language request into a normal editable TTS role table and dialogue table. "
            "Return strict JSON only. Do not include Markdown. Do not treat ordinary request words as character names. "
            "Use only explicitly mentioned character/person names as roles; if missing, create sensible role names. "
            "Strictly preserve the requested role count: if the user asks for one/single role, return exactly 1 role and do not add narrator, partner, or second speaker. "
            "Only create multiple roles when the user explicitly asks for multiple roles or names multiple characters. "
            "Schema: {\"roles\":[{\"speaker\":\"角色名\",\"voice\":\"voice id\",\"style\":[\"词\"],\"pace\":[\"词\"]}],"
            "\"dialogues\":[{\"speaker\":\"角色名\",\"voice\":\"voice id optional\",\"text\":\"可直接朗读的中文台词\"}]}. "
            "Generate concise, natural dialogue lines that the user can edit."
        )
        user_prompt = json.dumps({
            "user_request": user_text,
            "available_voices": voices,
            "requirements": [
                "角色表只包含真实角色名，不要包含整句需求片段",
                "必须严格匹配用户指定的角色数量；用户说一个/一位/单人角色时，只返回 1 个 role",
                "不要为了对话感自动添加旁白、搭档或第二角色",
                "对白表属于一个 Dialogue 音频素材",
                "每行对白可有不同 speaker 和 voice",
                "返回 JSON，不要解释",
            ],
        }, ensure_ascii=False)
        try:
            session_row = deps.safe_session(int(task["session_id"]))
            opencode_session_id = deps.text(session_row.get("opencode_session_id"))
            if not opencode_session_id:
                raise RuntimeError("OpenCode session is missing")
            model, _prompt_models = deps.resolve_model(session_row, deps.text(payload.get("provider")), deps.text(payload.get("model")), role, SURFACE_KOUBO_ASSET_AGENT_CHAT, sc=deps)
            client = deps.opencode_client_for(session_row, sc=deps)
            started_at = now_ms()
            client.prompt_async(
                opencode_session_id,
                user_prompt,
                model=model,
                system=system_prompt,
                tools={"bash": False, "read": False, "write": False, "websearch": False},
            )
            deadline = time.time() + TTS_AGENT_DRAFT_TIMEOUT_SECONDS
            assistant_text = ""
            while time.time() < deadline:
                messages = await asyncio.to_thread(client.messages, opencode_session_id, limit=120)
                assistant_text = deps.last_completed_assistant(messages, started_at) or ""
                if assistant_text:
                    break
                await asyncio.sleep(1)
            parsed = extract_json_object(assistant_text)
            if not parsed:
                raise RuntimeError("OpenCode did not return valid TTS Agent JSON")
            result = normalize_tts_agent_draft(parsed, user_text, voices, "opencode")
            deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.tts_agent.draft.created", json.dumps({
                "task_id": task_id,
                "source": "opencode",
                "role_count": len(result["roles"]),
                "dialogue_count": len(result["dialogues"]),
            }, ensure_ascii=True), now_ms())
            return result
        except Exception as exc:
            detail = str(exc)[:500]
            result = normalize_tts_agent_draft({}, user_text, voices, "fallback", detail)
            deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.tts_agent.draft.created", json.dumps({
                "task_id": task_id,
                "source": "fallback",
                "detail": detail,
                "role_count": len(result["roles"]),
                "dialogue_count": len(result["dialogues"]),
            }, ensure_ascii=True), now_ms())
            return result

    def infer_cosyvoice_model_from_voice_id(voice_id: str) -> str:
        normalized = deps.text(voice_id).lower()
        for model in COSYVOICE_CLONE_MODELS:
            if normalized == model or normalized.startswith(f"{model}-"):
                return model
        return "cosyvoice-v3.5-flash" if normalized.startswith("cosyvoice-") else ""

    def cloud_clone_tts_defaults(workspace: Any, prompt_item: dict[str, Any]) -> dict[str, str]:
        voice_id = deps.text(prompt_item.get("voice_id"))
        if not voice_id:
            return {}
        voice_target = resolve_tts_voice_alias(deps.ctx, voice_id)
        if voice_id.startswith(PUBLIC_TTS_VOICE_PREFIX) and not voice_target:
            raise HTTPException(status_code=400, detail="Select a valid cloud voice before generating.")
        if voice_target:
            voice_id = voice_target["voice_id"]
        voice_source = deps.text(prompt_item.get("voice_source"))
        inferred_model = infer_cosyvoice_model_from_voice_id(voice_id)
        if voice_source != "cloud_clone" and not inferred_model:
            return {}
        clones_payload = deps.read_json(workspace / CLOUD_VOICE_CLONES_REL)
        clone_records = clones_payload.get("clones") if isinstance(clones_payload, dict) and isinstance(clones_payload.get("clones"), list) else []
        for record in clone_records:
            if not isinstance(record, dict):
                continue
            record_voice = deps.text(record.get("voice_id") or record.get("voice"))
            if record_voice != voice_id:
                continue
            record_provider = deps.text(record.get("provider") or record.get("source_clone_provider"))
            target_model = deps.text(record.get("target_model")) or inferred_model
            if record_provider == "heygen" or target_model.startswith("heygen-"):
                return {"provider": "heygen", "model": target_model or "heygen-voice-clone-v3", "voice_id": voice_id}
            if target_model.startswith("cosyvoice-"):
                return {"provider": "cosyvoice", "model": target_model, "voice_id": voice_id}
        if voice_target and voice_target.get("provider"):
            return {
                "provider": voice_target["provider"],
                "model": voice_target.get("model") or inferred_model,
                "voice_id": voice_id,
            }
        if inferred_model:
            return {"provider": "cosyvoice", "model": inferred_model, "voice_id": voice_id}
        return {}

    def safe_normal_voice_token(value: Any) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", deps.text(value)).strip("_")[:64] or "clone"

    def normal_voice_tts_defaults(prompt_item: dict[str, Any]) -> dict[str, str]:
        candidate_id = deps.text(prompt_item.get("candidate_id"))
        requested_voice = deps.text(prompt_item.get("voice_id") or prompt_item.get("voice"))
        if not candidate_id and not requested_voice:
            return {}
        config = load_config(deps.ctx, "tts")
        providers = [item for item in (config.get("providers") or []) if isinstance(item, dict)]
        any_key_configured = any(bool(item.get("has_api_key")) for item in providers)
        matches: list[dict[str, str]] = []
        for provider in providers:
            if provider.get("enabled") is False:
                continue
            if any_key_configured and not provider.get("has_api_key"):
                continue
            provider_id = deps.text(provider.get("provider"))
            if not provider_id:
                continue
            selected_by_model = provider.get("selected_voice_by_model") if isinstance(provider.get("selected_voice_by_model"), dict) else {}
            models = [item for item in (provider.get("models") or []) if isinstance(item, dict)]
            for model_item in models:
                if model_item.get("enabled") is False:
                    continue
                model = deps.text(model_item.get("model"))
                if not model:
                    continue
                voices = [deps.text(item.get("voice_id") or item.get("id") or item.get("value")) for item in (model_item.get("voices") or []) if isinstance(item, dict)]
                voices = [voice for voice in voices if voice]
                default_voice = deps.text(selected_by_model.get(model)) or (voices[0] if voices else requested_voice)
                prefix = f"normal_{safe_normal_voice_token(provider_id)}_{safe_normal_voice_token(model)}_"
                if candidate_id and candidate_id.startswith(prefix):
                    candidate_voice_token = candidate_id[len(prefix):]
                    voice_id = requested_voice if requested_voice and (not voices or requested_voice in voices) else ""
                    if not voice_id:
                        voice_id = next((voice for voice in voices if safe_normal_voice_token(voice) == candidate_voice_token), "")
                    return {"provider": provider_id, "model": model, "voice_id": voice_id or default_voice}
                if requested_voice and requested_voice in voices:
                    matches.append({"provider": provider_id, "model": model, "voice_id": requested_voice})
        if not matches:
            return {}
        active_provider = deps.text(config.get("active_provider"))
        return next((item for item in matches if item["provider"] == active_provider), matches[0])

    def active_tts_generation_defaults(prompt_item: dict[str, Any]) -> dict[str, str]:
        config = load_config(deps.ctx, "tts")
        providers = [item for item in (config.get("providers") or []) if isinstance(item, dict)]
        provider = next((item for item in providers if item.get("active")), None) or next((item for item in providers if item.get("enabled") and item.get("has_api_key")), None)
        if not provider:
            raise HTTPException(status_code=400, detail="TTS provider is not configured or enabled")
        model = deps.text(provider.get("model"))
        models = [item for item in (provider.get("models") or []) if isinstance(item, dict)]
        model_item = next((item for item in models if deps.text(item.get("model")) == model), None) or (models[0] if models else {})
        if not model:
            model = deps.text(model_item.get("model"))
        voices = [deps.text(item.get("voice_id")) for item in (model_item.get("voices") or []) if isinstance(item, dict) and deps.text(item.get("voice_id"))]
        requested_voice = deps.text(prompt_item.get("voice_id"))
        selected_by_model = provider.get("selected_voice_by_model") if isinstance(provider.get("selected_voice_by_model"), dict) else {}
        default_voice = deps.text(selected_by_model.get(model)) or (voices[0] if voices else requested_voice)
        voice_id = requested_voice if requested_voice and (not voices or requested_voice in voices) else default_voice
        return {"provider": deps.text(provider.get("provider")), "model": model, "voice_id": voice_id}

    def tts_public_alias_requested(provider: str, model: str) -> bool:
        return deps.text(provider).startswith(PUBLIC_TTS_PROVIDER_PREFIX) or deps.text(model).startswith(PUBLIC_TTS_PROVIDER_PREFIX)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/scene-tts/events")
    async def generate_scene_tts(request: Request, task_id: int, payload: dict[str, Any]) -> StreamingResponse:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        workflow_id = deps.safe_name(deps.text(payload.get("workflow_id"), f"koubo_storyboard_scene_tts_{payload.get('scene_mark_id') or 'scene'}"), "koubo_storyboard_scene_tts")
        prompts = [item for item in (payload.get("prompts") if isinstance(payload.get("prompts"), list) else []) if isinstance(item, dict) and (deps.text(item.get("prompt")) or deps.text(item.get("provider")))]
        if not prompts:
            raise HTTPException(status_code=400, detail="At least one TTS prompt is required")
        if role == "admin":
            normalized_prompts = []
            for prompt_item in prompts:
                requested_provider = deps.text(prompt_item.get("provider"))
                requested_model = deps.text(prompt_item.get("model"))
                defaults = cloud_clone_tts_defaults(workspace, prompt_item) if not requested_provider and not requested_model else {}
                normalized_prompts.append({
                    **prompt_item,
                    **({
                        "provider": defaults["provider"],
                        "model": defaults["model"],
                        "voice_id": defaults.get("voice_id") or deps.text(prompt_item.get("voice_id")),
                    } if defaults else {}),
                })
            prompts = normalized_prompts
        else:
            normalized_prompts = []
            for prompt_item in prompts:
                alias_voice_id = deps.text(prompt_item.get("voice_id"))
                voice_target = resolve_tts_voice_alias(deps.ctx, alias_voice_id)
                if alias_voice_id.startswith(PUBLIC_TTS_VOICE_PREFIX) and not voice_target:
                    raise HTTPException(status_code=400, detail="Select a valid cloud voice before generating.")
                if voice_target:
                    prompt_item = {
                        **prompt_item,
                        "voice_id": voice_target["voice_id"],
                        "voice": voice_target["voice_id"],
                        "candidate_id": voice_target.get("candidate_id") or deps.text(prompt_item.get("candidate_id")),
                    }
                requested_provider = deps.text(prompt_item.get("provider"))
                requested_model = deps.text(prompt_item.get("model"))
                if tts_public_alias_requested(requested_provider, requested_model):
                    resolved_provider, resolved_model = resolve_tts_public_alias(deps.ctx, requested_provider, requested_model)
                    defaults = {
                        "provider": resolved_provider,
                        "model": resolved_model,
                        "voice_id": deps.text(prompt_item.get("voice_id")),
                    }
                elif voice_target:
                    defaults = safe_voice_target_defaults(
                        voice_target,
                        cloud_clone_tts_defaults(workspace, prompt_item),
                    )
                else:
                    defaults = cloud_clone_tts_defaults(workspace, prompt_item) if not requested_provider and not requested_model else {}
                if not defaults:
                    defaults = normal_voice_tts_defaults(prompt_item)
                if not defaults:
                    try:
                        defaults = hidden_model_defaults_for_role(deps.ctx, role, SURFACE_KOUBO_TTS_TIMING, requested_provider, requested_model)
                    except HTTPException as exc:
                        if exc.status_code != 403:
                            raise
                        defaults = active_tts_generation_defaults(prompt_item)
                normalized_prompts.append({**prompt_item, "provider": defaults["provider"], "model": defaults["model"], "voice_id": defaults.get("voice_id") or deps.text(prompt_item.get("voice_id"))})
            prompts = normalized_prompts
        if not deps.text(payload.get("scene_mark_id")):
            raise HTTPException(status_code=400, detail="scene_mark_id is required")
        if not deps.text(payload.get("srt_text")):
            raise HTTPException(status_code=400, detail="srt_text is required")
        started_payload = {
            "workflow_id": workflow_id,
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "shot_id": deps.text(payload.get("shot_id")),
            "scene_mark_id": deps.text(payload.get("scene_mark_id")),
            "dialogue_id": deps.text(payload.get("dialogue_id")),
            "dialogue_asset_key": deps.text(payload.get("dialogue_asset_key")),
            "input_mode": "tts",
            "srt_text": deps.text(payload.get("srt_text")),
            "temporary": False,
            "writes_asset_json": True,
        }
        if len(prompts) == 1 and deps.text(payload.get("locked_output")):
            locked_output = deps.safe_workspace_rel(workspace, deps.text(payload.get("locked_output")))[0]
            locked_config_key = deps.text(payload.get("locked_config_key"))
            payload = {
                **payload,
                "locked_output": locked_output,
                "output_generation_token": deps.reserve_tts_output_generation(workspace, locked_output, locked_config_key, sc=deps),
            }
        cached_result = deps.read_locked_tts_cache(workspace, payload, prompts[0], workflow_id, sc=deps) if len(prompts) == 1 else None
        if cached_result:
            cached_payload = {**started_payload, **cached_result, "task_id": task_id, "session_id": int(task["session_id"])}
            if not payload.get("asset_library_only"):
                if deps.text(payload.get("dialogue_id")) or deps.text(payload.get("dialogue_asset_key")):
                    deps.update_dialogue_audio_path(workspace, task, deps.text(payload.get("dialogue_id")), deps.text(cached_payload.get("output")), deps.text(payload.get("dialogue_asset_key")), cached_payload.get("duration_seconds") or cached_payload.get("duration"), sc=deps)
                else:
                    deps.update_scene_audio_path(workspace, task, deps.text(payload.get("scene_mark_id")), deps.text(cached_payload.get("output")), cached_payload.get("duration_seconds") or cached_payload.get("duration"), sc=deps)
            deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.locked_cache.hit", json.dumps(cached_payload, ensure_ascii=True), now_ms())

            def public_cached_payload(value: dict[str, Any]) -> dict[str, Any]:
                if role == "admin":
                    return value
                return mask_model_fields_for_role(deps.ctx, role, SURFACE_KOUBO_TTS_TIMING, value)

            async def cached_event_generator() -> Any:
                yield f"data: {json.dumps({'type': 'workflow_started', **started_payload, 'cache_hit': True}, ensure_ascii=True)}\n\n"
                yield f"data: {json.dumps({'type': 'completed', **public_cached_payload(cached_payload)}, ensure_ascii=True)}\n\n"
                yield f"data: {json.dumps({'type': 'round_completed', **started_payload, 'elapsed_seconds': 0, 'cache_hit': True}, ensure_ascii=True)}\n\n"

            return StreamingResponse(cached_event_generator(), media_type="text/event-stream")

        deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.workflow.started", json.dumps({**started_payload, "candidate_count": len(prompts)}, ensure_ascii=True), now_ms())

        def public_tts_payload(value: dict[str, Any]) -> dict[str, Any]:
            value = {key: item for key, item in value.items() if key != "output_generation_token"}
            if role == "admin":
                return value
            return mask_model_fields_for_role(deps.ctx, role, SURFACE_KOUBO_TTS_TIMING, value)

        def concat_asset_audio_segments(segment_results: list[dict[str, Any]]) -> dict[str, Any]:
            output_rel = deps.text(payload.get("locked_output"))
            if not output_rel:
                raise HTTPException(status_code=400, detail="locked_output is required for combined Asset Audio generation")
            output_rel, output_path = deps.safe_workspace_rel(workspace, output_rel)
            ordered_segments = sorted(segment_results, key=lambda item: int(item.get("segment_index") or 0))
            segment_paths = [workspace / deps.text(item.get("output")) for item in ordered_segments if deps.text(item.get("output"))]
            if not segment_paths:
                raise HTTPException(status_code=502, detail="No TTS segments were generated")
            for path in segment_paths:
                if not path.exists():
                    raise HTTPException(status_code=502, detail=f"TTS segment is missing: {path.name}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            normalized_dir = workspace / "SessionOutput/storyboard/Working" / f"{deps.safe_name(output_path.stem, 'asset_audio')}_concat_segments"
            normalized_dir.mkdir(parents=True, exist_ok=True)
            normalized_paths: list[Path] = []
            for index, path in enumerate(segment_paths, start=1):
                normalized_path = normalized_dir / f"segment_{index:03d}.wav"
                normalize_cmd = [
                    deps.ffmpeg_binary(),
                    "-y",
                    "-i",
                    str(path),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-af",
                    "aresample=48000,aformat=channel_layouts=stereo,asetpts=N/SR/TB",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    str(normalized_path),
                ]
                normalized = subprocess.run(normalize_cmd, capture_output=True, text=True, timeout=180, check=False)
                if normalized.returncode != 0:
                    raise HTTPException(status_code=500, detail=(normalized.stderr or "ffmpeg audio segment normalize failed")[:2000])
                normalized_paths.append(normalized_path)
            concat_list = output_path.with_suffix(f"{output_path.suffix}.concat.txt")
            codec_args = ["-c:a", "libmp3lame", "-b:a", "192k"] if output_path.suffix.lower() == ".mp3" else ["-c:a", "pcm_s16le"]
            def concat_file_line(path: Any) -> str:
                escaped = str(path).replace("'", "'\\''")
                return f"file '{escaped}'\n"
            concat_list.write_text("".join(concat_file_line(path) for path in normalized_paths), encoding="utf-8")
            cmd = [deps.ffmpeg_binary(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-ar", "48000", "-ac", "2", *codec_args, str(output_path)]
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=240, check=False)
            if completed.returncode != 0:
                raise HTTPException(status_code=500, detail=(completed.stderr or "ffmpeg audio concat failed")[:2000])
            duration = deps.audio_duration_seconds(output_path)
            result = {
                **started_payload,
                "ok": True,
                "status": "completed",
                "provider": deps.text(ordered_segments[0].get("provider")),
                "model": deps.text(ordered_segments[0].get("model")),
                "voice_id": deps.text(ordered_segments[0].get("voice_id")),
                "output": output_rel,
                "output_path": str(output_path),
                "duration_seconds": duration,
                "segment_count": len(ordered_segments),
                "segment_count_expected": len(prompts),
                "normalized_segment_count": len(normalized_paths),
                "elapsed_seconds": 0,
            }
            deps.write_locked_tts_manifest(workspace, task, payload, prompts[0], result, sc=deps)
            return result

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'workflow_started', **started_payload}, ensure_ascii=True)}\n\n"
            running: dict[asyncio.Task, dict[str, Any]] = {}
            heartbeat_counts: dict[str, int] = {}
            combine_asset_audio = bool(payload.get("asset_library_only") and len(prompts) > 1 and deps.text(payload.get("locked_output")))
            combined_suffix = Path(deps.text(payload.get("locked_output"))).suffix or ".wav"
            segment_results: list[dict[str, Any]] = []

            async def client_disconnected() -> bool:
                try:
                    return bool(await request.is_disconnected())
                except RuntimeError:
                    return False

            def record_aborted(pending_count: int) -> None:
                aborted = {**started_payload, "elapsed_seconds": round(time.time() - started, 1), "status": "aborted", "pending_count": pending_count}
                deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.workflow.aborted", json.dumps(aborted, ensure_ascii=True), now_ms())

            for index, prompt_item in enumerate(prompts, start=1):
                if await client_disconnected():
                    record_aborted(len(running))
                    for running_task in running:
                        if not running_task.done():
                            running_task.cancel()
                    return
                provider = deps.text(prompt_item.get("provider"), "provider")
                candidate_id = f"{provider}_tts_{index}" if role == "admin" else f"tts_{index}"
                if combine_asset_audio:
                    output_rel = f"SessionOutput/storyboard/Working/{workflow_id}_{candidate_id}{combined_suffix}"
                else:
                    output_rel = deps.text(payload.get("locked_output")) if len(prompts) == 1 and deps.text(payload.get("locked_output")) else f"SessionOutput/storyboard/Working/{workflow_id}_{candidate_id}.wav"
                output_rel = deps.safe_workspace_rel(workspace, output_rel)[0]
                locked_request = {
                    "use_locked_cache": bool(payload.get("use_locked_cache")),
                    "locked_output": deps.text(payload.get("locked_output")),
                    "locked_manifest": deps.text(payload.get("locked_manifest")),
                    "locked_config_key": deps.text(payload.get("locked_config_key")),
                } if len(prompts) == 1 else {}
                generation_config_key = deps.text(locked_request.get("locked_config_key")) or json.dumps({
                    "provider": provider,
                    "model": prompt_item.get("model"),
                    "voice_id": prompt_item.get("voice_id"),
                    "prompt": prompt_item.get("prompt"),
                    "text": prompt_item.get("text"),
                    "tempo": prompt_item.get("tempo"),
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                generation_token = deps.text(payload.get("output_generation_token")) or deps.reserve_tts_output_generation(workspace, output_rel, generation_config_key, sc=deps)
                request_payload = {**started_payload, **locked_request, "api_call_id": f"{workflow_id}-{candidate_id}-{now_ms()}", "candidate_id": candidate_id, "provider": provider, "model": prompt_item.get("model"), "voice_id": prompt_item.get("voice_id"), "output": output_rel, "output_path": str(workspace / output_rel), "prompt_preview": deps.text(prompt_item.get("prompt"))[:1000], "prompt_length": len(deps.text(prompt_item.get("prompt"))), "segment_index": index if combine_asset_audio else 0, "output_generation_token": generation_token}
                deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.requested", json.dumps(request_payload, ensure_ascii=True), now_ms())
                yield f"data: {json.dumps({'type': 'requested', **public_tts_payload(request_payload)}, ensure_ascii=True)}\n\n"
                async_task = asyncio.create_task(asyncio.to_thread(deps.run_scene_tts_candidate, task, workspace, request_payload, prompt_item, output_rel, sc=deps))
                running[async_task] = request_payload
                heartbeat_counts[request_payload["api_call_id"]] = 0
            pending = set(running.keys())
            try:
                while pending:
                    if await client_disconnected():
                        record_aborted(len(pending))
                        return
                    done, pending = await asyncio.wait(pending, timeout=2)
                    for done_task in done:
                        request_payload = running[done_task]
                        try:
                            render_result = await done_task
                        except HTTPException as exc:
                            failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1), "status": "failed"}
                            deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.failed", json.dumps(failed, ensure_ascii=True), now_ms())
                            yield f"data: {json.dumps({'type': 'failed', **public_tts_payload(failed)}, ensure_ascii=True)}\n\n"
                        except Exception as exc:
                            failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1), "status": "failed"}
                            deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.failed", json.dumps(failed, ensure_ascii=True), now_ms())
                            yield f"data: {json.dumps({'type': 'failed', **public_tts_payload(failed)}, ensure_ascii=True)}\n\n"
                        else:
                            result = {**request_payload, **render_result, "ok": True, "status": "completed", "elapsed_seconds": render_result.get("elapsed_seconds") or round(time.time() - started, 1)}
                            if combine_asset_audio:
                                segment_results.append(result)
                                yield f"data: {json.dumps({'type': 'segment_completed', **public_tts_payload(result)}, ensure_ascii=True)}\n\n"
                            if not payload.get("asset_library_only"):
                                if deps.text(payload.get("dialogue_id")) or deps.text(payload.get("dialogue_asset_key")):
                                    deps.update_dialogue_audio_path(workspace, task, deps.text(payload.get("dialogue_id")), deps.text(result.get("output")), deps.text(payload.get("dialogue_asset_key")), result.get("duration_seconds") or result.get("duration"), sc=deps)
                                else:
                                    deps.update_scene_audio_path(workspace, task, deps.text(payload.get("scene_mark_id")), deps.text(result.get("output")), result.get("duration_seconds") or result.get("duration"), sc=deps)
                            deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.completed", json.dumps(result, ensure_ascii=True), now_ms())
                            if not combine_asset_audio:
                                yield f"data: {json.dumps({'type': 'completed', **public_tts_payload(result)}, ensure_ascii=True)}\n\n"
                    if pending:
                        for pending_task in list(pending):
                            request_payload = running[pending_task]
                            api_call_id = deps.text(request_payload.get("api_call_id"))
                            heartbeat_counts[api_call_id] = heartbeat_counts.get(api_call_id, 0) + 1
                            heartbeat_payload = {**request_payload, "heartbeat": heartbeat_counts[api_call_id], "elapsed_seconds": round(time.time() - started, 1)}
                            yield f"data: {json.dumps({'type': 'heartbeat', **public_tts_payload(heartbeat_payload)}, ensure_ascii=True)}\n\n"
            finally:
                for pending_task in list(pending):
                    if not pending_task.done():
                        pending_task.cancel()
            if combine_asset_audio:
                if len(segment_results) != len(prompts):
                    failed = {**started_payload, "detail": f"Only generated {len(segment_results)} of {len(prompts)} TTS segments; combined Asset Audio was not created.", "elapsed_seconds": round(time.time() - started, 1), "status": "failed", "segment_count": len(segment_results), "segment_count_expected": len(prompts)}
                    deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.asset_audio_combine_failed", json.dumps(failed, ensure_ascii=True), now_ms())
                    yield f"data: {json.dumps({'type': 'failed', **public_tts_payload(failed)}, ensure_ascii=True)}\n\n"
                else:
                    combined = concat_asset_audio_segments(segment_results)
                    combined["elapsed_seconds"] = round(time.time() - started, 1)
                    deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.asset_audio_combined", json.dumps(combined, ensure_ascii=True), now_ms())
                    yield f"data: {json.dumps({'type': 'completed', **public_tts_payload(combined)}, ensure_ascii=True)}\n\n"
            completed_payload = {**started_payload, "elapsed_seconds": round(time.time() - started, 1)}
            deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.workflow.completed", json.dumps(completed_payload, ensure_ascii=True), now_ms())
            yield f"data: {json.dumps({'type': 'round_completed', **completed_payload}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")
