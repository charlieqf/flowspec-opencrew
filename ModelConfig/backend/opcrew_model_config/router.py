from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter

from opcrew_backend.context import AppContext

from .asr_config import (
    ASRConfigSavePayload,
    ASRConnectionTestPayload,
    build_asr_config_router,
    connection_result as asr_connection_result,
    load_stored_key as load_asr_stored_key,
    model_options as asr_model_options,
    test_asr_connection,
)
from .media_model_config import (
    MediaConfigSavePayload,
    MediaConnectionTestPayload,
    TTSVoiceMatchPayload,
    TTSVoicePreviewPayload,
    build_media_model_config_router,
    load_config as load_media_config,
    load_stored_key as load_media_stored_key,
    option_by_provider,
    test_media_connection,
    connection_result as media_connection_result,
)
from opcrew_backend.services.provider_resolver import resolve_endpoint

MediaKind = Literal["image", "video", "tts", "lipsync", "digital-human", "voice-clone"]


def build_model_config_router(ctx: AppContext) -> APIRouter:
    router = APIRouter(tags=["model-config"])

    # Legacy-compatible routes remain available while the frontend moves to
    # explicit ASR/Image/Video/TTS module endpoints.
    router.include_router(build_asr_config_router(ctx))
    router.include_router(build_media_model_config_router(ctx))

    @router.get("/api/model-config/asr/config")
    def get_asr_config() -> dict[str, Any]:
        return build_asr_config_router(ctx).routes[0].endpoint()

    @router.put("/api/model-config/asr/config")
    def save_asr_config(payload: ASRConfigSavePayload) -> dict[str, Any]:
        return build_asr_config_router(ctx).routes[1].endpoint(payload)

    @router.post("/api/model-config/asr/test")
    def test_asr_config(payload: ASRConnectionTestPayload) -> dict[str, Any]:
        provider = payload.provider.strip()
        model = payload.model.strip()
        selected = next((item for item in asr_model_options() if item["provider"] == provider and item["model"] == model), None)
        if selected is None:
            return asr_connection_result(False, "Unknown ASR model", f"{provider}/{model}")
        result = test_asr_connection(provider, model, load_asr_stored_key(ctx, provider))
        ctx.event("info" if result["ok"] else "warn", "asr", "ASR connection test", {"provider": provider, "model": model, "status": result["status"]})
        return result

    def media_config(kind: MediaKind) -> dict[str, Any]:
        return load_media_config(ctx, kind)

    def save_media_config(kind: MediaKind, payload: MediaConfigSavePayload) -> dict[str, Any]:
        # Reuse the battle-tested legacy endpoint implementation through the
        # included router to avoid drifting storage behavior between paths.
        endpoint = build_media_model_config_router(ctx).routes[1].endpoint
        return endpoint(kind, payload)

    def test_media_config(kind: MediaKind, payload: MediaConnectionTestPayload) -> dict[str, Any]:
        options = option_by_provider(kind)
        provider = payload.provider.strip()
        model = payload.model.strip()
        if provider not in options:
            return media_connection_result(False, "Unknown provider", provider)
        valid_models = {str(item["model"]) for item in options[provider]["models"]}
        if model not in valid_models:
            return media_connection_result(False, "Unknown model", f"{provider}/{model}")
        resolution = resolve_endpoint(provider, model, kind, f"{kind}_{provider}_key")
        result = test_media_connection(kind, provider, model, load_media_stored_key(ctx, kind, provider), resolution.proxy_policy)
        ctx.event("info" if result["ok"] else "warn", "media-model", "Media model connection test", {"kind": kind, "provider": provider, "model": model, "status": result["status"]})
        return result

    @router.get("/api/model-config/image/config")
    def get_image_config() -> dict[str, Any]:
        return media_config("image")

    @router.put("/api/model-config/image/config")
    def save_image_config(payload: MediaConfigSavePayload) -> dict[str, Any]:
        return save_media_config("image", payload)

    @router.post("/api/model-config/image/test")
    def test_image_config(payload: MediaConnectionTestPayload) -> dict[str, Any]:
        return test_media_config("image", payload)

    @router.get("/api/model-config/video/config")
    def get_video_config() -> dict[str, Any]:
        return media_config("video")

    @router.put("/api/model-config/video/config")
    def save_video_config(payload: MediaConfigSavePayload) -> dict[str, Any]:
        return save_media_config("video", payload)

    @router.post("/api/model-config/video/test")
    def test_video_config(payload: MediaConnectionTestPayload) -> dict[str, Any]:
        return test_media_config("video", payload)

    @router.get("/api/model-config/lipsync/config")
    def get_lipsync_config() -> dict[str, Any]:
        return media_config("lipsync")

    @router.put("/api/model-config/lipsync/config")
    def save_lipsync_config(payload: MediaConfigSavePayload) -> dict[str, Any]:
        return save_media_config("lipsync", payload)

    @router.post("/api/model-config/lipsync/test")
    def test_lipsync_config(payload: MediaConnectionTestPayload) -> dict[str, Any]:
        return test_media_config("lipsync", payload)

    @router.get("/api/model-config/digital-human/config")
    def get_digital_human_config() -> dict[str, Any]:
        return media_config("digital-human")

    @router.put("/api/model-config/digital-human/config")
    def save_digital_human_config(payload: MediaConfigSavePayload) -> dict[str, Any]:
        return save_media_config("digital-human", payload)

    @router.post("/api/model-config/digital-human/test")
    def test_digital_human_config(payload: MediaConnectionTestPayload) -> dict[str, Any]:
        return test_media_config("digital-human", payload)

    @router.get("/api/model-config/voice-clone/config")
    def get_voice_clone_config() -> dict[str, Any]:
        return media_config("voice-clone")

    @router.put("/api/model-config/voice-clone/config")
    def save_voice_clone_config(payload: MediaConfigSavePayload) -> dict[str, Any]:
        return save_media_config("voice-clone", payload)

    @router.post("/api/model-config/voice-clone/test")
    def test_voice_clone_config(payload: MediaConnectionTestPayload) -> dict[str, Any]:
        return test_media_config("voice-clone", payload)

    @router.get("/api/model-config/tts/config")
    def get_tts_config() -> dict[str, Any]:
        return media_config("tts")

    @router.put("/api/model-config/tts/config")
    def save_tts_config(payload: MediaConfigSavePayload) -> dict[str, Any]:
        return save_media_config("tts", payload)

    @router.post("/api/model-config/tts/test")
    def test_tts_config(payload: MediaConnectionTestPayload) -> dict[str, Any]:
        return test_media_config("tts", payload)

    @router.post("/api/model-config/tts/voices/preview")
    def preview_tts_voice(payload: TTSVoicePreviewPayload) -> dict[str, Any]:
        endpoint = build_media_model_config_router(ctx).routes[3].endpoint
        return endpoint(payload)

    @router.post("/api/model-config/tts/voices/match")
    def match_tts_voices(payload: TTSVoiceMatchPayload) -> dict[str, Any]:
        endpoint = build_media_model_config_router(ctx).routes[4].endpoint
        return endpoint(payload)

    return router
