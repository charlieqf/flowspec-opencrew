from __future__ import annotations

import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


PROVIDER_ID = "minimax"
DEFAULT_TARGET_MODEL = "minimax-voice-clone-v1"
DEFAULT_BASE_URL = "https://api.minimaxi.com"
# Model used for the activation/preview synthesis. MiniMax cloned voices are
# temporary until used by a T2A call once, so a synthesis is required to make
# the voice persistent and queryable. speech-02-hd is broadly available; newer
# accounts can switch to speech-2.6-hd / speech-2.8-hd via tts_model.
DEFAULT_TTS_MODEL = "speech-02-hd"
# MiniMax clones from an uploaded file (file_id), not a public URL, so the
# clone flow does not require publishing the reference audio to public R2.
REQUIRES_PUBLIC_AUDIO_URL = False

GROUP_ID_ENV_KEYS = (
    "OPENCREW_ANALYSIS_V1_MINIMAX_GROUP_ID",
    "MINIMAX_GROUP_ID",
    "MINIMAXI_GROUP_ID",
)
BASE_URL_ENV_KEYS = (
    "OPENCREW_ANALYSIS_V1_MINIMAX_BASE_URL",
    "MINIMAX_BASE_URL",
)
TTS_MODEL_ENV_KEYS = (
    "OPENCREW_ANALYSIS_V1_MINIMAX_TTS_MODEL",
    "MINIMAX_TTS_MODEL",
)


def resolve_tts_model(explicit: str = "") -> str:
    model = str(explicit or "").strip()
    if model:
        return model
    for key in TTS_MODEL_ENV_KEYS:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return DEFAULT_TTS_MODEL


def resolve_group_id(explicit: str = "") -> str:
    group_id = str(explicit or "").strip()
    if group_id:
        return group_id
    for key in GROUP_ID_ENV_KEYS:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def resolve_base_url(explicit: str = "") -> str:
    base_url = str(explicit or "").strip()
    if base_url:
        return base_url.rstrip("/")
    for key in BASE_URL_ENV_KEYS:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    return DEFAULT_BASE_URL


def endpoint(base_url: str, path: str, group_id: str) -> str:
    url = f"{base_url.rstrip('/')}{path}"
    if group_id:
        url = f"{url}?{urllib.parse.urlencode({'GroupId': group_id})}"
    return url


def check_base_resp(response: dict[str, Any], action: str) -> None:
    base_resp = response.get("base_resp") if isinstance(response.get("base_resp"), dict) else {}
    status_code = base_resp.get("status_code")
    if status_code in (None, 0, "0"):
        return
    status_msg = str(base_resp.get("status_msg") or "").strip() or "unknown error"
    raise RuntimeError(f"MiniMax {action} failed: status_code={status_code}: {status_msg}")


def request_raw(url: str, *, api_key: str, data: bytes, content_type: str, timeout: int = 180) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"MiniMax request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MiniMax network request failed: {exc.reason}") from exc
    return json.loads(body) if body else {}


def post_json(url: str, *, api_key: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    return request_raw(url, api_key=api_key, data=json.dumps(payload).encode("utf-8"), content_type="application/json", timeout=timeout)


def post_multipart(url: str, *, api_key: str, fields: dict[str, str], file_field: str, filename: str, file_bytes: bytes, mime: str, timeout: int = 180) -> dict[str, Any]:
    boundary = f"----OpenCrewMiniMax{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        file_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return request_raw(url, api_key=api_key, data=b"".join(chunks), content_type=f"multipart/form-data; boundary={boundary}", timeout=timeout)


def generate_voice_id(prefix: str) -> str:
    # MiniMax custom voice_id: starts with a letter, 8-256 chars, [A-Za-z0-9_-],
    # must not end with - or _, and must contain at least one letter and digit.
    token = re.sub(r"[^a-zA-Z0-9]", "", f"{prefix or 'ocadv'}{uuid.uuid4().hex}")
    if not token[:1].isalpha():
        token = f"oc{token}"
    if not any(ch.isdigit() for ch in token):
        token = f"{token}0"
    return token[:64]


def reference_audio_bytes(audio_path: str = "", audio_url: str = "") -> tuple[bytes, str]:
    local = str(audio_path or "").strip()
    if local:
        path = Path(local).expanduser()
        if not path.is_file():
            raise RuntimeError(f"MiniMax clone reference audio is missing: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
        return path.read_bytes(), mime
    url = str(audio_url or "").strip()
    if url:
        try:
            with urllib.request.urlopen(url, timeout=180) as res:
                data = res.read()
                mime = res.headers.get_content_type() if hasattr(res.headers, "get_content_type") else "audio/mpeg"
        except urllib.error.URLError as exc:
            raise RuntimeError(f"MiniMax could not download reference audio: {exc.reason}") from exc
        return data, mime or "audio/mpeg"
    raise RuntimeError("MiniMax voice clone requires audio_path or audio_url.")


def upload_file(*, api_key: str, base_url: str, group_id: str, file_bytes: bytes, filename: str, mime: str, purpose: str = "voice_clone") -> int:
    response = post_multipart(
        endpoint(base_url, "/v1/files/upload", group_id),
        api_key=api_key,
        fields={"purpose": purpose},
        file_field="file",
        filename=filename or "reference.mp3",
        file_bytes=file_bytes,
        mime=mime,
    )
    check_base_resp(response, "file upload")
    file_obj = response.get("file") if isinstance(response.get("file"), dict) else {}
    file_id = file_obj.get("file_id", response.get("file_id"))
    if file_id in (None, "", 0):
        raise RuntimeError("MiniMax file upload did not return a file_id.")
    return int(file_id)


def synthesize_url(*, api_key: str, voice_id: str, text: str, base_url: str = "", group_id: str = "", tts_model: str = "", speed: float = 1.0, audio_format: str = "mp3") -> str:
    base = resolve_base_url(base_url)
    gid = resolve_group_id(group_id)
    response = post_json(
        endpoint(base, "/v1/t2a_v2", gid),
        api_key=api_key,
        payload={
            "model": resolve_tts_model(tts_model),
            "text": text,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": max(0.5, min(2.0, float(speed or 1.0))),
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {"sample_rate": 32000, "format": audio_format},
            "output_format": "url",
        },
    )
    check_base_resp(response, "text-to-audio")
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    audio_url = str(data.get("audio") or "").strip()
    if not audio_url:
        raise RuntimeError("MiniMax t2a_v2 response did not include data.audio url.")
    return audio_url


def create_voice(
    *,
    api_key: str,
    target_model: str,
    prefix: str,
    audio_url: str = "",
    audio_path: str = "",
    language_hints: list[str] | None = None,
    group_id: str = "",
    base_url: str = "",
    tts_model: str = "",
    activate: bool = True,
    **_: Any,
) -> dict[str, Any]:
    base = resolve_base_url(base_url)
    gid = resolve_group_id(group_id)
    if not gid:
        raise RuntimeError(
            "MiniMax voice clone requires a GroupId. Set OPENCREW_ANALYSIS_V1_MINIMAX_GROUP_ID "
            "or provide group_id in the provider extra config."
        )
    file_bytes, mime = reference_audio_bytes(audio_path=audio_path, audio_url=audio_url)
    filename = Path(str(audio_path or "reference.mp3")).name or "reference.mp3"
    file_id = upload_file(api_key=api_key, base_url=base, group_id=gid, file_bytes=file_bytes, filename=filename, mime=mime)
    voice_id = generate_voice_id(prefix)
    clone_response = post_json(
        endpoint(base, "/v1/voice_clone", gid),
        api_key=api_key,
        payload={"file_id": file_id, "voice_id": voice_id},
    )
    check_base_resp(clone_response, "voice clone")
    returned_voice = str(clone_response.get("voice_id") or voice_id).strip() or voice_id

    activated = False
    activation_error = ""
    if activate:
        # MiniMax clones are temporary (auto-deleted within ~7 days) until used by a
        # T2A call once; the first successful synthesis both confirms readiness and
        # makes the voice persistent/queryable. This call is billed.
        try:
            synthesize_url(
                api_key=api_key,
                voice_id=returned_voice,
                text="你好，这是一段克隆音色激活试听。",
                base_url=base,
                group_id=gid,
                tts_model=tts_model,
            )
            activated = True
        except Exception as exc:  # noqa: BLE001 - surface but do not fail the clone
            activation_error = str(exc)

    return {
        "provider": PROVIDER_ID,
        "target_model": target_model or DEFAULT_TARGET_MODEL,
        "voice_id": returned_voice,
        "voice": returned_voice,
        "file_id": file_id,
        "activated": activated,
        "activation_error": activation_error,
        "request_id": (clone_response.get("base_resp") or {}).get("request_id") if isinstance(clone_response.get("base_resp"), dict) else None,
    }


def list_voices(*, api_key: str, base_url: str = "", group_id: str = "", **_: Any) -> dict[str, Any]:
    base = resolve_base_url(base_url)
    gid = resolve_group_id(group_id)
    response = post_json(
        endpoint(base, "/v1/get_voice", gid),
        api_key=api_key,
        payload={"voice_type": "voice_cloning"},
    )
    check_base_resp(response, "list voices")
    voices = response.get("voice_cloning") if isinstance(response.get("voice_cloning"), list) else []
    return {"provider": PROVIDER_ID, "voices": voices, "request_id": None}


def query_voice(*, api_key: str, voice_id: str, base_url: str = "", group_id: str = "", **_: Any) -> dict[str, Any]:
    # MiniMax has no per-voice status endpoint; derive readiness from the clone
    # list (a cloned voice only appears there after it has been used by T2A once).
    listing = list_voices(api_key=api_key, base_url=base_url, group_id=group_id)
    target = str(voice_id or "").strip()
    match = next((item for item in listing.get("voices", []) if isinstance(item, dict) and str(item.get("voice_id") or "").strip() == target), None)
    output = {
        "voice_id": target,
        "exists": bool(match),
        "status": "ready" if match else "pending_or_unused",
        "detail": match or {},
    }
    return {"provider": PROVIDER_ID, "voice_id": target, "output": output, "request_id": None}


def delete_voice(*, api_key: str, voice_id: str, base_url: str = "", group_id: str = "", **_: Any) -> dict[str, Any]:
    base = resolve_base_url(base_url)
    gid = resolve_group_id(group_id)
    response = post_json(
        endpoint(base, "/v1/delete_voice", gid),
        api_key=api_key,
        payload={"voice_type": "voice_cloning", "voice_id": str(voice_id or "").strip()},
    )
    check_base_resp(response, "delete voice")
    return {"provider": PROVIDER_ID, "voice_id": str(voice_id or "").strip(), "deleted": True, "request_id": None}
