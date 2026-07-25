from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any, Callable, Optional


THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parents[1]
for extra_path in (REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "ModelConfig" / "backend"):
    value = str(extra_path)
    if value not in sys.path:
        sys.path.insert(0, value)
GENERATE_PATH = THIS_DIR / "01_StoryBoardGenerate.py"
spec = importlib.util.spec_from_file_location("talking_head_storyboard_generate", GENERATE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load {GENERATE_PATH}")
generate = importlib.util.module_from_spec(spec)
sys.modules.setdefault("talking_head_storyboard_generate", generate)
spec.loader.exec_module(generate)

PRIVACY_PATH = THIS_DIR / "reference_privacy_grid.py"
privacy_spec = importlib.util.spec_from_file_location("talking_head_reference_privacy_grid", PRIVACY_PATH)
if privacy_spec is None or privacy_spec.loader is None:
    raise RuntimeError(f"Cannot load {PRIVACY_PATH}")
reference_privacy_grid = importlib.util.module_from_spec(privacy_spec)
sys.modules.setdefault("talking_head_reference_privacy_grid", reference_privacy_grid)
privacy_spec.loader.exec_module(reference_privacy_grid)


WORKFLOW_ID = "person_talking_head_v1"
TOOL_NAME = "03_StoryBoardConfig"
TOOL_VERSION = "0.3.0"
WORKING_DIR_REL = "SessionOutput/storyboard/Working"
REPORT_REL = "S4_03_StoryBoardConfig/Report/Result.json"
OUTPUT_REL = "S4_03_StoryBoardConfig/Output/srt_storyboard.json"
DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
MEDIA_CONFIG_TABLE = "tool_media_provider_configs"
FINAL_SRT_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
REWRITTEN_SRT_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def normalize_rel(value: Any) -> str:
    text = generate.text(value).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def rel_exists(workspace: Path, rel_path: str) -> bool:
    rel_path = normalize_rel(rel_path)
    return bool(rel_path) and (workspace / rel_path).is_file()


def media_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    repo_root = Path(__file__).resolve().parents[2]
    for candidate in (
        repo_root / ".bin" / name,
        repo_root / "ToolLibrary" / ".bin" / name,
        repo_root / "ToolLibrary" / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
    ):
        if candidate.exists():
            return str(candidate)
    return ""


def audio_duration_seconds(path: Path) -> float:
    ffprobe = media_binary("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            duration = float((result.stdout or "").strip())
            if duration > 0:
                return round(duration, 3)
        except Exception:
            pass
    try:
        with wave.open(str(path), "rb") as handle:
            return round(handle.getnframes() / handle.getframerate(), 3)
    except Exception:
        return 0.0


def atempo_filter_chain(rate: float) -> str:
    value = max(0.1, float(rate or 1.0))
    filters: list[str] = []
    while value < 0.5:
        filters.append("atempo=0.5")
        value /= 0.5
    while value > 2.0:
        filters.append("atempo=2.0")
        value /= 2.0
    filters.append(f"atempo={value:.6f}")
    return ",".join(filters)


def convert_audio_to_wav(source: Path, target: Path, tempo: float = 1.0) -> None:
    ffmpeg = media_binary("ffmpeg")
    if not ffmpeg:
        shutil.copyfile(source, target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y", "-i", str(source)]
    if abs(float(tempo or 1.0) - 1.0) > 0.001:
        command.extend(["-filter:a", atempo_filter_chain(tempo)])
    command.extend(["-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(target)])
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "ffmpeg audio conversion failed")[:2000])


def normalize_database_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def postgres_connect(database_url: str) -> Any:
    normalized = normalize_database_url(database_url)
    try:
        import psycopg  # type: ignore
        return psycopg.connect(normalized, connect_timeout=5)
    except ImportError:
        import psycopg2  # type: ignore
        return psycopg2.connect(normalized, connect_timeout=5)


def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
    try:
        from opencrew_runtime_secrets import resolve_secret_value as runtime_resolve_secret_value  # type: ignore
        return generate.text(runtime_resolve_secret_value(api_key_ref, legacy_value))
    except Exception:
        ref = generate.text(api_key_ref)
        if ref:
            env_value = generate.text(os.environ.get(ref))
            if env_value:
                return env_value
            try:
                from opcrew_backend.services.local_secrets import LocalSecretStore

                data_dir = Path(os.environ.get("OPENCREW_DATA_DIR") or Path.home() / ".opencrew")
                stored = generate.text(LocalSecretStore(data_dir).get(ref))
                if stored:
                    return stored
            except Exception:
                pass
        return generate.text(legacy_value)


def default_api_key_ref(kind: str, provider: str) -> str:
    return f"{kind.replace('-', '_')}_{provider}_key"


def load_voice_clone_config(provider: str, session_config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    provider = generate.text(provider, "heygen").lower()
    session_config = generate.dict_value(session_config)
    session_provider = generate.text(session_config.get("provider")).lower()
    if session_provider and session_provider != provider:
        raise RuntimeError(f"Session Variables voice clone provider mismatch: expected {provider}, got {session_provider}")
    database_url = generate.text(os.environ.get(DATABASE_URL_ENV), DEFAULT_DATABASE_URL)
    data: dict[str, Any] = {}
    if session_config:
        data = {
            "provider": generate.text(session_config.get("provider"), provider),
            "model": generate.text(session_config.get("model")),
            "api_key_ref": generate.text(session_config.get("api_key_ref")),
            "api_key_ciphertext": "",
            "extra_json": session_config.get("extra_json") if session_config.get("extra_json") is not None else session_config.get("extra"),
        }
    if not data.get("api_key_ref"):
        conn = postgres_connect(database_url)
        try:
            query = f"""
SELECT provider, model, api_key_ref, api_key_ciphertext, extra_json
FROM {MEDIA_CONFIG_TABLE}
WHERE kind = %s AND provider = %s AND enabled = true
ORDER BY active DESC, id ASC
LIMIT 1
"""
            with conn.cursor() as cursor:
                cursor.execute(query, ("voice-clone", provider))
                row = cursor.fetchone()
                columns = [item.name for item in cursor.description] if cursor.description else []
        finally:
            conn.close()
        if not row:
            raise RuntimeError(f"Voice Clone provider is not configured or enabled: {provider}")
        data = {**dict(zip(columns, row)), **{key: value for key, value in data.items() if value not in ("", None, {})}}
    api_key_ref = generate.text(data.get("api_key_ref")) or default_api_key_ref("voice-clone", provider)
    api_key = resolve_secret_value(api_key_ref, generate.text(data.get("api_key_ciphertext")))
    if not api_key:
        raise RuntimeError(f"Voice Clone API key is missing: {provider}")
    try:
        extra = json.loads(generate.text(data.get("extra_json"), "{}"))
    except Exception:
        extra = {}
    return {
        "provider": generate.text(data.get("provider"), provider),
        "model": generate.text(data.get("model")),
        "api_key": api_key,
        "api_key_ref": api_key_ref,
        "source": generate.text(session_config.get("source"), "SessionContext/Variables.json") if session_config else f"postgres:{MEDIA_CONFIG_TABLE}",
        "extra": extra if isinstance(extra, dict) else {},
    }


def first_audio_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("audio_url", "url", "download_url", "uri"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in payload.values():
            found = first_audio_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = first_audio_url(item)
            if found:
                return found
    return ""


def http_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120, error_prefix: str = "Voice Clone") -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"{error_prefix} request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{error_prefix} network request failed: {exc.reason}") from exc


def download_binary(url: str, target: Path) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "OpenCrew-TalkingHead/1.0"})
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(50 * 1024 * 1024 + 1)
        content_type = response.headers.get("Content-Type", "")
    if len(data) > 50 * 1024 * 1024:
        raise RuntimeError("HeyGen TTS audio exceeded 50MB limit")
    target.write_bytes(data)
    return content_type


def generate_clone_audio(provider: str, text_value: str, voice_id: str, tempo: float, output_path: Path, force: bool = False, voice_runtime_config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    provider = generate.text(provider, "heygen").lower()
    if provider not in {"heygen", "cosyvoice", "minimax"}:
        raise RuntimeError(f"人物口播不支持克隆声音 provider：{provider}")
    manifest_path = output_path.with_suffix(".json")
    signature = {"provider": provider, "voice_id": voice_id, "tempo": round(float(tempo or 1.0), 4), "text_sha256": sha256_text(text_value)}
    runtime_snapshot = generate.dict_value(voice_runtime_config)
    runtime_meta = {
        "api_key_ref": generate.text(runtime_snapshot.get("api_key_ref")),
        "runtime_config_source": generate.text(runtime_snapshot.get("source"), "SessionContext/Variables.json") if runtime_snapshot else "",
        "runtime_provider": generate.text(runtime_snapshot.get("provider")),
        "runtime_model": generate.text(runtime_snapshot.get("model")),
    }
    existing = generate.read_json(manifest_path, {}) or {}
    if output_path.is_file() and not force and existing.get("signature") == signature:
        duration = audio_duration_seconds(output_path)
        if duration > 0:
            patched = {**existing, **{key: value for key, value in runtime_meta.items() if value}, "output": normalize_rel(output_path), "duration_seconds": duration, "cache_hit": True}
            if any(runtime_meta.get(key) and not existing.get(key) for key in runtime_meta):
                generate.write_json(manifest_path, {**patched, "output": ""})
            return patched
    config = load_voice_clone_config(provider, voice_runtime_config)
    raw_path = output_path.with_name(f"{output_path.stem}_raw")
    audio_url = ""
    if provider == "heygen":
        result = http_json(
            "https://api.heygen.com/v3/voices/speech",
            {
                "text": text_value,
                "voice_id": voice_id,
                "input_type": "text",
                "speed": max(0.5, min(2.0, float(tempo or 1.0))),
                "language": "zh",
            },
            {"x-api-key": config["api_key"]},
            timeout=120,
            error_prefix="HeyGen TTS",
        )
        audio_url = first_audio_url(result)
        if not audio_url:
            raise RuntimeError("HeyGen TTS response did not include audio_url")
        content_type = download_binary(audio_url, raw_path)
    elif provider == "cosyvoice":
        from opcrew_backend.routes.media_model_config import dashscope_cosyvoice_tts_audio_bytes

        extra = generate.dict_value(config.get("extra"))
        try:
            raw = dashscope_cosyvoice_tts_audio_bytes(
                config["api_key"],
                generate.text(config.get("model"), "cosyvoice-v3.5-flash"),
                voice_id,
                text_value,
                "",
                workspace=generate.text(extra.get("workspace") or extra.get("workspace_id")),
            )
        except Exception as exc:
            raise RuntimeError(f"CosyVoice TTS request failed: {exc}") from exc
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        content_type = "audio/wav"
    else:
        extra = generate.dict_value(config.get("extra"))
        group_id = generate.text(extra.get("group_id"))
        if not group_id:
            raise RuntimeError("MiniMax TTS requires GroupId in the provider extra config.")
        base_url = generate.text(extra.get("base_url"), "https://api.minimaxi.com").rstrip("/")
        configured_model = generate.text(config.get("model"))
        speech_model = configured_model.split("/")[-1]
        if not speech_model or speech_model.startswith("minimax-voice-clone"):
            speech_model = generate.text(extra.get("tts_model"), "speech-02-hd")
        result = http_json(
            f"{base_url}/v1/t2a_v2?{urllib.parse.urlencode({'GroupId': group_id})}",
            {
                "model": speech_model,
                "text": text_value,
                "voice_setting": {"voice_id": voice_id, "speed": max(0.5, min(2.0, float(tempo or 1.0))), "vol": 1.0, "pitch": 0},
                "audio_setting": {"sample_rate": 32000, "format": "mp3"},
                "output_format": "url",
            },
            {"Authorization": f"Bearer {config['api_key']}"},
            timeout=120,
            error_prefix="MiniMax TTS",
        )
        base_response = result.get("base_resp") if isinstance(result.get("base_resp"), dict) else {}
        if base_response.get("status_code") not in (None, 0, "0"):
            raise RuntimeError(f"MiniMax TTS failed: {base_response.get('status_msg') or base_response.get('status_code')}")
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        audio_url = first_audio_url(data) or generate.text(data.get("audio"))
        if not audio_url:
            raise RuntimeError("MiniMax TTS response did not include data.audio url")
        content_type = download_binary(audio_url, raw_path)
    if provider == "heygen" and ("wav" in content_type.lower() or "wave" in content_type.lower()):
        shutil.copyfile(raw_path, output_path)
    else:
        convert_audio_to_wav(raw_path, output_path, tempo if provider == "cosyvoice" else 1.0)
    duration = audio_duration_seconds(output_path)
    if duration <= 0:
        raise RuntimeError(f"Generated audio duration is invalid: {output_path.name}")
    payload = {
        "signature": signature,
        "provider": provider,
        "model": config.get("model") or ({"heygen": "heygen-voice-clone-v3", "cosyvoice": "cosyvoice-v3.5-flash", "minimax": "speech-02-hd"}[provider]),
        "api_key_ref": config.get("api_key_ref") or runtime_meta["api_key_ref"],
        "runtime_config_source": config.get("source") or runtime_meta["runtime_config_source"],
        "runtime_provider": config.get("provider") or runtime_meta["runtime_provider"],
        "runtime_model": config.get("model") or runtime_meta["runtime_model"],
        "voice_id": voice_id,
        "tempo": signature["tempo"],
        "text_sha256": signature["text_sha256"],
        "output": "",
        "duration_seconds": duration,
        "audio_url": audio_url,
        "content_type": content_type,
        "generated_at": generate.now_iso(),
        "cache_hit": False,
    }
    generate.write_json(manifest_path, payload)
    return payload


def generate_heygen_audio(text_value: str, voice_id: str, tempo: float, output_path: Path, force: bool = False, voice_runtime_config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return generate_clone_audio("heygen", text_value, voice_id, tempo, output_path, force=force, voice_runtime_config=voice_runtime_config)


def talking_head_config(meta: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    return generate.dict_value(variables.get("talking_head") or meta.get("talking_head"))


def portrait_path(meta: dict[str, Any], variables: dict[str, Any]) -> str:
    talking_head = talking_head_config(meta, variables)
    portrait = generate.dict_value(talking_head.get("portrait"))
    return normalize_rel(portrait.get("context_portrait_image_path") or portrait.get("portrait_image_path") or variables.get("talking_head_portrait_image_path") or variables.get("portrait_image_path"))


def reuse_count(meta: dict[str, Any], variables: dict[str, Any]) -> int:
    talking_head = talking_head_config(meta, variables)
    planning = generate.dict_value(talking_head.get("segment_planning"))
    try:
        return max(1, int(planning.get("portrait_segments_per_image") or variables.get("portrait_segments_per_image") or 2))
    except Exception:
        return 2


def target_segment_seconds(meta: dict[str, Any], variables: dict[str, Any]) -> float:
    talking_head = talking_head_config(meta, variables)
    planning = generate.dict_value(talking_head.get("segment_planning"))
    return max(0.1, generate.number(planning.get("srt_target_seconds") or variables.get("srt_target_seconds"), 8.0))


def voice_config(meta: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    talking_head = talking_head_config(meta, variables)
    timing = generate.dict_value(talking_head.get("voice_timing"))
    clone_config = generate.dict_value(talking_head.get("voice_clone_config") or variables.get("default_voice_clone_config"))
    provider = generate.text(timing.get("provider") or timing.get("voice_provider") or variables.get("voice_provider") or clone_config.get("provider"), "heygen").lower()
    voice_id = generate.text(timing.get("voice_id") or variables.get("voice_id"))
    tempo = generate.number(timing.get("tempo") or variables.get("tempo"), 1.0)
    return {
        "provider": provider,
        "voice_id": voice_id,
        "voice_label": generate.text(timing.get("voice_label") or variables.get("voice_label")),
        "tempo": max(0.1, tempo),
        "clone_config": clone_config,
        "source": "SessionContext/Variables.json",
        "status": "selected" if voice_id else "not_selected",
    }


def max_sd_2_reference_assets(workspace: Path, variables: dict[str, Any]) -> dict[str, Any]:
    default_video = generate.dict_value(variables.get("default_video_config"))
    if generate.text(default_video.get("provider")).lower() != "openrouter" or generate.text(default_video.get("model")).lower() != "bytedance/seedance-2.0":
        return {}
    talking_head = talking_head_config({}, variables)
    reference_video = generate.dict_value(talking_head.get("reference_video"))
    portrait = portrait_path({}, variables)
    manifest = reference_privacy_grid.materialize_privacy_assets(
        workspace,
        variables,
        portrait,
        generate.text(reference_video.get("reference_video_path")),
        use_system_default=reference_video.get("use_system_default") is not False,
    )
    target = generate.dict_value(manifest.get("target_identity"))
    video = generate.dict_value(manifest.get("reference_video"))
    render = generate.dict_value(manifest.get("render"))
    return {
        "enabled": True,
        "video_generation_mode": "talking_head_reference_video",
        "provider": "openrouter",
        "model": "bytedance/seedance-2.0",
        "model_alias": "Max SD 2",
        "reference_mode": "input_references",
        "prompt_template": "Video_SDR2V_TalkingHead.md",
        "reference_video_role": "talking_head_motion_expression_reference",
        "reference_video_path": generate.text(video.get("provider_path")),
        "provider_reference_video_path": generate.text(video.get("provider_path")),
        "source_reference_video_path": generate.text(video.get("source_path")),
        "target_identity_image_path": generate.text(target.get("provider_path")),
        "provider_target_identity_image_path": generate.text(target.get("provider_path")),
        "source_target_identity_image_path": generate.text(target.get("source_path")),
        "privacy_grid_mode": True,
        "reference_video_grid_applied": bool(video.get("grid_applied")),
        "target_identity_grid_applied": bool(target.get("grid_applied")),
        "effective_grid_scope": generate.text(manifest.get("effective_grid_scope")),
        "privacy_grid_preset": generate.text(render.get("density_preset")) or "dense_12_1",
        "cell_size_reference": int(render.get("cell_size_reference") or 12),
        "line_width_reference": float(render.get("line_width_reference") or 1.0),
        "privacy_grid_manifest_path": generate.text(manifest.get("manifest_path")),
        "prompt_contract": "talking_head_privacy_grid_clean_output_0.1" if generate.text(manifest.get("effective_grid_scope")) != "none" else "",
    }


def working_path(asset_key: str, suffix: str) -> str:
    return f"{WORKING_DIR_REL}/{asset_key}_{suffix}"


def spoken_unit_count(value: str) -> int:
    text = generate.text(value)
    return len(re.findall(r"[\u3400-\u9fff]", text)) + len(re.findall(r"[A-Za-z0-9]", text))


def source_dialogues(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    shots = generate.list_value(storyboard.get("shots"))
    if not shots:
        return []
    scenes = generate.list_value(generate.dict_value(shots[0]).get("scenes"))
    if not scenes:
        return []
    return [dict(item) for item in generate.list_value(generate.dict_value(scenes[0]).get("dialogue_items")) if isinstance(item, dict)]


def configure_dialogues(
    workspace: Path,
    dialogues: list[dict[str, Any]],
    meta: dict[str, Any],
    variables: dict[str, Any],
    force: bool,
    reference_assets: Optional[dict[str, Any]] = None,
    on_dialogue_configured: Optional[Callable[[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]], None]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    portrait = portrait_path(meta, variables)
    reference_assets = generate.dict_value(reference_assets)
    provider_portrait = generate.text(reference_assets.get("provider_target_identity_image_path") or portrait)
    reuse = reuse_count(meta, variables)
    voice = voice_config(meta, variables)
    segment_seconds = target_segment_seconds(meta, variables)
    warnings: list[dict[str, Any]] = []
    outputs = {"portrait_reset_count": 0, "audio_generated_count": 0, "audio_cache_hit_count": 0, "dialogue_count": len(dialogues)}
    if portrait and not rel_exists(workspace, portrait):
        warnings.append({"code": "portrait_missing", "message": f"人物形象图片不存在：{portrait}"})
    if voice["status"] != "selected":
        warnings.append({"code": "voice_not_selected", "message": "未选择克隆声音，本次只配置首帧，不生成声音。"})

    configured: list[dict[str, Any]] = []
    cursor = 0.0
    previous_asset_key = ""
    for index, dialogue in enumerate(dialogues, start=1):
        asset_key = generate.safe_key(generate.text(dialogue.get("dialogue_asset_key")), f"talking_head_{index:04d}")
        text_value = generate.text(dialogue.get("dialogue") or dialogue.get("text"))
        audio_rel = ""
        audio_duration = generate.number(dialogue.get("duration"), 0.0)
        audio_meta: dict[str, Any] = {}
        if voice["status"] == "selected":
            output_rel = working_path(asset_key, "Audio_Final.wav")
            output_path = workspace / output_rel
            audio_meta = generate_clone_audio(voice["provider"], text_value, voice["voice_id"], voice["tempo"], output_path, force=force, voice_runtime_config=voice.get("clone_config"))
            audio_rel = output_rel
            audio_duration = generate.number(audio_meta.get("duration_seconds"), audio_duration)
            outputs["audio_generated_count"] += 1
            if audio_meta.get("cache_hit"):
                outputs["audio_cache_hit_count"] += 1
        if audio_duration <= 0:
            audio_duration = max(0.2, generate.number(dialogue.get("duration"), segment_seconds))
        if audio_duration > segment_seconds:
            warnings.append({
                "code": "dialogue_audio_exceeds_single_video_length",
                "srt_id": generate.text(dialogue.get("srt_id")),
                "srt_ids": [generate.text(value) for value in generate.list_value(dialogue.get("srt_ids")) if generate.text(value)],
                "duration_seconds": round(audio_duration, 3),
                "single_video_length_seconds": segment_seconds,
                "message": "声音时长超过单个视频长度，人物口播不拆句，仅提示。",
            })

        reset = bool(portrait) and ((index - 1) % reuse == 0)
        if reset:
            image_rel = working_path(asset_key, "Image_New.png")
            if rel_exists(workspace, provider_portrait):
                target = workspace / image_rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(workspace / provider_portrait, target)
            outputs["portrait_reset_count"] += 1
            image_source_type = "uploaded_portrait_first_frame"
            image_source = provider_portrait
        else:
            image_rel = ""
            image_source_type = ""
            image_source = ""

        start = cursor
        end = cursor + audio_duration
        cursor = end
        working_assets = {
            "audio": {
                "slot": "Audio_Final",
                "source_type": f"{voice['provider']}_clone_voice" if audio_rel else "",
                "path": audio_rel,
                "voice_provider": voice["provider"],
                "voice_id": voice["voice_id"],
                "voice_label": voice["voice_label"],
                "tempo": voice["tempo"],
                "runtime_config_source": generate.text(generate.dict_value(voice.get("clone_config")).get("source")),
                "duration_seconds": round(audio_duration, 3),
            },
            "images": [
                {
                    "slot": "Image_New",
                    "source_type": image_source_type,
                    "path": image_rel,
                    "source_path": image_source,
                    "portrait_reset": reset,
                },
                {"slot": "Image_02", "source_type": "", "path": ""},
            ],
            "video": {"slot": "Video_Final", "source_type": "", "path": ""},
        }
        configured.append({
            **dialogue,
            "dialogue_asset_key": asset_key,
            "dialogue_id": generate.text(dialogue.get("dialogue_id"), f"scene_001_dialogue_{index:03d}"),
            "dialogue": text_value,
            "text": text_value,
            "segment_index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(audio_duration, 3),
            "image_path": image_rel if reset else "",
            "source_image_paths": [provider_portrait] if reset and provider_portrait else [],
            "target_identity_image_path": provider_portrait,
            "source_target_identity_image_path": portrait,
            "talking_head_reference": dict(reference_assets),
            "talking_head": {
                **generate.dict_value(dialogue.get("talking_head")),
                "enabled": True,
                "portrait_reset": reset,
                "portrait_segments_per_image": reuse,
                "segment_policy": "merge_srt_to_single_video_length",
                "first_frame_strategy": "uploaded_portrait_first_frame" if reset else "previous_segment_tail_frame",
                "previous_segment_asset_key": "" if reset else previous_asset_key,
                "voice": voice,
            },
            "video_plan": {
                **generate.dict_value(dialogue.get("video_plan")),
                "is_talking_head": True,
                "resource_strategy": "talking_head_only",
                "allow_cutaway": False,
                "force_single_segment": True,
                "segment_policy": "merge_srt_to_single_video_length",
                "first_frame_policy": "portrait_reset_then_previous_tail",
                "first_frame_strategy": "uploaded_portrait_first_frame" if reset else "previous_segment_tail_frame",
                "previous_segment_asset_key": "" if reset else previous_asset_key,
                "first_frame_path": image_rel,
            },
            "working_assets": working_assets,
            "audio_generation": audio_meta,
        })
        previous_asset_key = asset_key
        if on_dialogue_configured is not None:
            on_dialogue_configured(configured, warnings, outputs)
    return configured, warnings, outputs


def sync_subtitle_timings(workspace: Path, dialogues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    timing_by_srt: dict[str, dict[str, Any]] = {}
    for dialogue in dialogues:
        source_items = [item for item in generate.list_value(dialogue.get("source_srt_items")) if isinstance(item, dict)]
        srt_ids = [generate.text(value) for value in generate.list_value(dialogue.get("srt_ids")) if generate.text(value)]
        if not source_items:
            source_items = [{"srt_id": srt_id, "dialogue": ""} for srt_id in srt_ids]
        if not source_items:
            srt_id = generate.text(dialogue.get("srt_id"))
            source_items = [{"srt_id": srt_id, "dialogue": generate.text(dialogue.get("dialogue") or dialogue.get("text"))}] if srt_id else []
        weights: list[float] = []
        for item in source_items:
            weight = generate.number(item.get("estimated_duration"), 0.0)
            if weight <= 0:
                estimate = generate.dict_value(item.get("voice_timing_estimate"))
                weight = generate.number(estimate.get("duration"), 0.0)
            if weight <= 0:
                weight = max(1.0, float(spoken_unit_count(generate.text(item.get("dialogue") or item.get("text")))))
            weights.append(weight)
        total_weight = sum(weights) or float(len(source_items) or 1)
        cursor = generate.number(dialogue.get("start"), 0.0)
        dialogue_end = generate.number(dialogue.get("end"), cursor + generate.number(dialogue.get("duration"), 0.0))
        for item, weight in zip(source_items, weights):
            srt_id = generate.text(item.get("srt_id") or item.get("id"))
            if not srt_id:
                continue
            duration = round(generate.number(dialogue.get("duration"), 0.0) * (weight / total_weight), 3)
            start = round(cursor, 3)
            end = round(dialogue_end if item is source_items[-1] else cursor + duration, 3)
            duration = round(max(0.001, end - start), 3)
            timing_by_srt[srt_id] = {
                "start": start,
                "end": end,
                "duration": duration,
                "dialogue_id": generate.text(dialogue.get("dialogue_id")),
                "dialogue_asset_key": generate.text(dialogue.get("dialogue_asset_key")),
                "timing_source": "talking_head_dialogue_audio_distribution",
            }
            cursor = end
    if not timing_by_srt:
        return actions
    for rel in (REWRITTEN_SRT_ITEMS_REL, FINAL_SRT_ITEMS_REL):
        path = workspace / rel
        payload = generate.read_json(path, {}) or {}
        items = generate.list_value(payload.get("items")) if isinstance(payload, dict) else []
        if not items:
            continue
        updated = 0
        next_items = []
        for item in items:
            if not isinstance(item, dict):
                next_items.append(item)
                continue
            srt_id = generate.text(item.get("srt_id") or item.get("id"))
            timing = timing_by_srt.get(srt_id)
            if timing:
                item = {**item, **timing}
                updated += 1
            next_items.append(item)
        if updated:
            payload = {**payload, "items": next_items, "timing_updated_by": TOOL_NAME, "timing_updated_at": generate.now_iso()}
            generate.write_json(path, payload)
            actions.append({"path": rel, "updated_items": updated})
    return actions


def rebuild_source(source: dict[str, Any], dialogues: list[dict[str, Any]], config: dict[str, Any], complete: bool = True, configured_dialogue_count: Optional[int] = None) -> dict[str, Any]:
    start = generate.number(dialogues[0].get("start")) if dialogues else 0.0
    end = generate.number(dialogues[-1].get("end")) if dialogues else 0.0
    scene = {
        "scene_id": "scene_001",
        "title": "人物口播",
        "summary": "单场景人物口播，逐句独立 Segment。",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(max(0.0, end - start), 3),
        "srt_ids": [generate.text(item.get("srt_id")) for item in dialogues if generate.text(item.get("srt_id"))],
        "key_frame_paths": [generate.text(item.get("image_path")) for item in dialogues if generate.text(item.get("image_path"))],
        "working_assets": generate.empty_assets(),
        "dialogue_items": dialogues,
    }
    shot = {
        "shot_id": "shot_001",
        "title": "人物口播",
        "formula_stage": "talking_head",
        "summary": "固定一个 Shot，一个 Scene。",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": scene["duration"],
        "srt_ids": scene["srt_ids"],
        "key_frame_paths": scene["key_frame_paths"],
        "scenes": [scene],
    }
    return {
        **source,
        "workflow_id": WORKFLOW_ID,
        "talking_head_configured": bool(complete),
        "talking_head_configuration_status": "completed" if complete else "partial",
        "talking_head_configured_dialogue_count": len(dialogues) if configured_dialogue_count is None else int(configured_dialogue_count),
        "talking_head_config": config,
        "shots": [shot],
        "updated_at": generate.now_iso(),
    }


def run(workspace: Path, force: bool = False) -> dict[str, Any]:
    workspace = workspace.resolve()
    variables = generate.read_json(workspace / generate.VARIABLES_REL, {}) or {}
    if not isinstance(variables, dict) or generate.text(variables.get("workflow_id")) != WORKFLOW_ID:
        raise RuntimeError("请先运行 TalkingHead_V1/00 生成当前人物口播 Variables。")
    source = generate.read_json(workspace / generate.STORYBOARD_REL, {}) or {}
    dialogues = source_dialogues(source)
    if not dialogues:
        raise RuntimeError("StoryBoard 配置需要先运行 01 和 02，当前没有 Dialogue。")
    voice = voice_config({}, variables)
    config = {
        "portrait_image_path": portrait_path({}, variables),
        "portrait_segments_per_image": reuse_count({}, variables),
        "single_video_length_seconds": target_segment_seconds({}, variables),
        "voice": voice,
        "resource_strategy": "talking_head_only",
        "segment_policy": "merge_srt_to_single_video_length",
        "first_frame_policy": "portrait_reset_then_previous_tail",
    }
    reference_assets = max_sd_2_reference_assets(workspace, variables)
    if reference_assets:
        config["max_sd_2_reference"] = reference_assets
    task_snapshot = generate.dict_value(variables.get("task"))
    task_id = int(task_snapshot.get("task_id") or variables.get("task_id") or 0)
    session_id = int(task_snapshot.get("session_id") or variables.get("session_id") or 0)
    checkpoint_state: dict[str, Any] = {"configured_count": 0, "timing_sync_actions": []}

    def persist_checkpoint(configured_prefix: list[dict[str, Any]], checkpoint_warnings: list[dict[str, Any]], checkpoint_outputs: dict[str, Any]) -> None:
        configured_count = len(configured_prefix)
        combined = [*configured_prefix, *[dict(item) for item in dialogues[configured_count:]]]
        checkpoint_config = {
            **config,
            "configuration_status": "partial" if configured_count < len(dialogues) else "completed",
            "configured_dialogue_count": configured_count,
            "dialogue_count": len(dialogues),
        }
        checkpoint_source = rebuild_source(
            source,
            combined,
            checkpoint_config,
            complete=configured_count == len(dialogues),
            configured_dialogue_count=configured_count,
        )
        checkpoint_edit = generate.edit_storyboard(checkpoint_source, task_id=task_id, session_id=session_id)
        generate.write_json(workspace / generate.STORYBOARD_REL, checkpoint_source)
        generate.write_json(workspace / generate.EDIT_REL, checkpoint_edit)
        generate.write_json(workspace / OUTPUT_REL, checkpoint_source)
        timing_actions = sync_subtitle_timings(workspace, configured_prefix)
        checkpoint_state.update({
            "configured_count": configured_count,
            "timing_sync_actions": timing_actions,
            "warnings": list(checkpoint_warnings),
            "outputs": dict(checkpoint_outputs),
        })
        variables.update({
            "talking_head_storyboard_configured": configured_count == len(dialogues),
            "talking_head_storyboard_configuration_status": "completed" if configured_count == len(dialogues) else "partial",
            "talking_head_configured_dialogue_count": configured_count,
            "talking_head_audio_generated_count": checkpoint_outputs.get("audio_generated_count", configured_count),
            "talking_head_timing_sync_actions": timing_actions,
            "updated_at": generate.now_iso(),
        })
        generate.write_json(workspace / generate.VARIABLES_REL, variables)

    try:
        configured, warnings, outputs = configure_dialogues(
            workspace,
            dialogues,
            {},
            variables,
            force,
            reference_assets=reference_assets,
            on_dialogue_configured=persist_checkpoint,
        )
    except Exception as exc:
        failed_result = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "workflow_id": WORKFLOW_ID,
            "status": "failed",
            "force": bool(force),
            "outputs": {
                **generate.dict_value(checkpoint_state.get("outputs")),
                "configured_dialogue_count": int(checkpoint_state.get("configured_count") or 0),
                "dialogue_count": len(dialogues),
                "storyboard_path": generate.STORYBOARD_REL,
                "edit_storyboard_path": generate.EDIT_REL,
                "result_path": REPORT_REL,
                "voice_status": voice["status"],
                "timing_sync_actions": checkpoint_state.get("timing_sync_actions") or [],
            },
            "warnings": checkpoint_state.get("warnings") or [],
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "updated_at": generate.now_iso(),
        }
        generate.write_json(workspace / REPORT_REL, failed_result)
        raise
    source = rebuild_source(source, configured, config)
    edit = generate.edit_storyboard(source, task_id=task_id, session_id=session_id)
    generate.write_json(workspace / generate.STORYBOARD_REL, source)
    generate.write_json(workspace / generate.EDIT_REL, edit)
    generate.write_json(workspace / OUTPUT_REL, source)
    timing_sync_actions = sync_subtitle_timings(workspace, configured)
    if isinstance(variables, dict):
        talking_head_vars = generate.dict_value(variables.get("talking_head"))
        segment_planning = generate.dict_value(talking_head_vars.get("segment_planning"))
        segment_planning["segment_policy"] = "merge_srt_to_single_video_length"
        talking_head_vars["segment_planning"] = segment_planning
        variables["talking_head"] = talking_head_vars
        quick_config = generate.dict_value(variables.get("storyboard_quick_config"))
        quick_config["segment_policy"] = "merge_srt_to_single_video_length"
        quick_talking_head = generate.dict_value(quick_config.get("talking_head"))
        quick_segment_planning = generate.dict_value(quick_talking_head.get("segment_planning"))
        quick_segment_planning["segment_policy"] = "merge_srt_to_single_video_length"
        quick_talking_head["segment_planning"] = quick_segment_planning
        quick_config["talking_head"] = quick_talking_head
        variables["storyboard_quick_config"] = quick_config
        variables.update({
            "workflow_id": WORKFLOW_ID,
            "talking_head_storyboard_configured": True,
            "talking_head_voice": voice,
            "talking_head_audio_generated_count": outputs["audio_generated_count"],
            "talking_head_timing_sync_actions": timing_sync_actions,
            "updated_at": generate.now_iso(),
        })
        generate.write_json(workspace / generate.VARIABLES_REL, variables)
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workflow_id": WORKFLOW_ID,
        "status": "completed",
        "force": bool(force),
        "outputs": {
            **outputs,
            "storyboard_path": generate.STORYBOARD_REL,
            "edit_storyboard_path": generate.EDIT_REL,
            "result_path": REPORT_REL,
            "voice_status": voice["status"],
            "timing_sync_actions": timing_sync_actions,
        },
        "warnings": warnings,
        "updated_at": generate.now_iso(),
    }
    generate.write_json(workspace / REPORT_REL, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure TalkingHead_V1 StoryBoard images and HeyGen clone voice.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()
    result = run(Path(args.workspace), force=args.force)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
