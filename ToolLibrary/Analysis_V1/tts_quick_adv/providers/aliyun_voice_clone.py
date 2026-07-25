from __future__ import annotations

import json
import mimetypes
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


PROVIDER_ID = "aliyun_dashscope"
DEFAULT_ENROLLMENT_MODEL = "voice-enrollment"
DEFAULT_TARGET_MODEL = "cosyvoice-v3.5-flash"


def request_json(url: str, headers: dict[str, str], timeout: int = 60) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def dashscope_upload_file(api_key: str, model: str, path: Path) -> str:
    query = urllib.parse.urlencode({"action": "getPolicy", "model": model})
    policy = request_json(
        f"https://dashscope.aliyuncs.com/api/v1/uploads?{query}",
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    policy_data = policy.get("data") if isinstance(policy.get("data"), dict) else {}
    upload_host = str(policy_data.get("upload_host") or "")
    upload_dir = str(policy_data.get("upload_dir") or "")
    if not upload_host or not upload_dir:
        raise RuntimeError("DashScope upload policy is missing upload_host/upload_dir.")
    key = f"{upload_dir.rstrip('/')}/{uuid.uuid4().hex}_{path.name}"
    boundary = f"----OpenCrewDashScope{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    fields = {
        "OSSAccessKeyId": str(policy_data.get("oss_access_key_id") or ""),
        "Signature": str(policy_data.get("signature") or ""),
        "policy": str(policy_data.get("policy") or ""),
        "x-oss-object-acl": str(policy_data.get("x_oss_object_acl") or "private"),
        "x-oss-forbid-overwrite": str(policy_data.get("x_oss_forbid_overwrite") or "true"),
        "key": key,
        "success_action_status": "200",
    }
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8"),
            b"\r\n",
        ])
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        upload_host,
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as res:
        if res.status != 200:
            raise RuntimeError(f"DashScope upload failed: HTTP {res.status}")
    return f"oss://{key}"


def enrollment_service(api_key: str, workspace: str = "", model: str = DEFAULT_ENROLLMENT_MODEL) -> Any:
    try:
        from dashscope.audio.tts_v2 import VoiceEnrollmentService  # type: ignore
    except Exception as exc:
        raise RuntimeError("Python package dashscope with audio.tts_v2 is required for Aliyun voice cloning.") from exc
    return VoiceEnrollmentService(api_key=api_key, workspace=workspace or None, model=model or DEFAULT_ENROLLMENT_MODEL)


def create_voice(
    *,
    api_key: str,
    target_model: str,
    prefix: str,
    audio_url: str,
    language_hints: list[str] | None = None,
    max_prompt_audio_length: float | None = None,
    workspace: str = "",
    enrollment_model: str = DEFAULT_ENROLLMENT_MODEL,
    **_: Any,
) -> dict[str, Any]:
    service = enrollment_service(api_key, workspace=workspace, model=enrollment_model)
    voice_id = service.create_voice(
        target_model=target_model or DEFAULT_TARGET_MODEL,
        prefix=prefix,
        url=audio_url,
        language_hints=language_hints,
        max_prompt_audio_length=max_prompt_audio_length,
    )
    return {
        "provider": PROVIDER_ID,
        "target_model": target_model or DEFAULT_TARGET_MODEL,
        "voice_id": voice_id,
        "request_id": service.get_last_request_id(),
    }


def list_voices(*, api_key: str, prefix: str = "", page_index: int = 0, page_size: int = 10, workspace: str = "", enrollment_model: str = DEFAULT_ENROLLMENT_MODEL) -> dict[str, Any]:
    service = enrollment_service(api_key, workspace=workspace, model=enrollment_model)
    voices = service.list_voices(prefix=prefix or None, page_index=max(0, page_index), page_size=max(1, page_size))
    return {"provider": PROVIDER_ID, "voices": voices, "request_id": service.get_last_request_id()}


def query_voice(*, api_key: str, voice_id: str, workspace: str = "", enrollment_model: str = DEFAULT_ENROLLMENT_MODEL) -> dict[str, Any]:
    service = enrollment_service(api_key, workspace=workspace, model=enrollment_model)
    output = service.query_voice(voice_id)
    return {"provider": PROVIDER_ID, "voice_id": voice_id, "output": output, "request_id": service.get_last_request_id()}


def delete_voice(*, api_key: str, voice_id: str, workspace: str = "", enrollment_model: str = DEFAULT_ENROLLMENT_MODEL) -> dict[str, Any]:
    service = enrollment_service(api_key, workspace=workspace, model=enrollment_model)
    service.delete_voice(voice_id)
    return {"provider": PROVIDER_ID, "voice_id": voice_id, "deleted": True, "request_id": service.get_last_request_id()}
