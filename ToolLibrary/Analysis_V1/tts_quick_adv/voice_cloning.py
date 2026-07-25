from __future__ import annotations

import os
import re
import hashlib
import datetime as dt
import hmac
import mimetypes
import shlex
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import TOOL_NAME, TOOL_VERSION
from .io_utils import now_iso, read_json, relpath, write_json
from .paths import OUTPUT_CLOUD_CLONE_REL, SESSION_AUDIO_REFERENCE_REL, SESSION_CLOUD_CLONES_REL, SESSION_TTS_FINAL_REL, ensure_dirs
from .providers import aliyun_voice_clone, heygen_voice_clone, minimax_voice_clone


DEFAULT_CLONE_API_KEY_ENV = "DASHSCOPE_API_KEY"
DEFAULT_CLONE_PREFIX = "ocadv"
DEFAULT_PUBLIC_ASSET_R2_ENDPOINT = "https://2df00bb6f61532c69846abb0ec6cf49a.r2.cloudflarestorage.com"
DEFAULT_PUBLIC_ASSET_R2_BUCKET = "opencrew-public-assets"
DEFAULT_PUBLIC_ASSET_R2_REGION = "auto"
DEFAULT_PUBLIC_ASSET_R2_PREFIX = "analysis-v1/tts-clone"
DEFAULT_PUBLIC_ASSET_TTL_SECONDS = 604800
PUBLIC_ASSET_R2_ENV_KEYS = {
    "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT",
    "OPENCREW_PUBLIC_ASSET_R2_BUCKET",
    "OPENCREW_PUBLIC_ASSET_R2_REGION",
    "OPENCREW_PUBLIC_ASSET_R2_PREFIX",
    "OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS",
    "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID",
    "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY",
    "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_REF",
    "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY_REF",
}

try:
    from opencrew_runtime_secrets import resolve_secret_value
except Exception:  # pragma: no cover - used only outside the OpenCrew repo runtime
    try:
        from ToolLibrary.opencrew_runtime_secrets import resolve_secret_value
    except Exception:
        def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
            return legacy_value


class CloudCloneError(RuntimeError):
    def __init__(self, code: str, message: str, failed: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.failed = failed


@dataclass(frozen=True)
class CloneVoiceArgs:
    provider: str
    api_key: str
    api_key_env: str
    workspace_id: str
    enrollment_model: str
    target_model: str
    prefix: str
    audio_path: str
    audio_url: str
    language_hints: str
    max_prompt_audio_length: float
    page_index: int
    page_size: int
    voice_id: str
    consent_confirmed: bool
    consent_actor: str
    consent_note: str
    force: bool


def normalize_prefix(value: str) -> str:
    prefix = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())[:9]
    return prefix or DEFAULT_CLONE_PREFIX


def parse_language_hints(value: str) -> list[str] | None:
    hints = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return hints or None


def public_asset_r2_env_candidates() -> list[Path]:
    candidates = [
        os.environ.get("OPENCREW_PUBLIC_ASSETS_R2_ENV"),
        os.environ.get("OPENCREW_PUBLIC_ASSET_R2_ENV"),
    ]
    data_dir = os.environ.get("OPENCREW_DATA_DIR")
    if data_dir:
        candidates.append(str(Path(data_dir) / "public_assets_r2.env"))
    candidates.append(str(Path.home() / ".opencrew" / "public_assets_r2.env"))
    seen: set[Path] = set()
    paths: list[Path] = []
    for value in candidates:
        if not value:
            continue
        path = Path(value).expanduser()
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def parse_env_file_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    try:
        parsed = shlex.split(value, comments=False, posix=True)
    except ValueError:
        parsed = []
    if len(parsed) == 1:
        return parsed[0]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_public_asset_r2_env_file() -> None:
    for path in public_asset_r2_env_candidates():
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].strip()
            if "=" not in stripped:
                continue
            key, raw_value = stripped.split("=", 1)
            key = key.strip()
            if key not in PUBLIC_ASSET_R2_ENV_KEYS or os.environ.get(key):
                continue
            value = parse_env_file_value(raw_value)
            if value:
                os.environ[key] = value
        return


def resolve_api_key(explicit: str, env_name: str) -> str:
    if str(explicit or "").strip():
        return str(explicit).strip()
    selected_env = str(env_name or DEFAULT_CLONE_API_KEY_ENV).strip()
    if selected_env and os.environ.get(selected_env):
        return str(os.environ[selected_env]).strip()
    raise CloudCloneError("clone_api_key_missing", f"Voice clone API key is missing. Set {selected_env or DEFAULT_CLONE_API_KEY_ENV} or pass --clone-api-key.")


def resolve_audio_path(workspace: Path, audio_path: str) -> Path:
    raw = str(audio_path or "").strip()
    if raw:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else workspace / path
    return workspace / SESSION_AUDIO_REFERENCE_REL


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aws_quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="-_.~")


def aws_sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def r2_signing_key(secret_key: str, date_stamp: str, region: str, service: str = "s3") -> bytes:
    k_date = aws_sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = aws_sign(k_date, region)
    k_service = aws_sign(k_region, service)
    return aws_sign(k_service, "aws4_request")


def r2_canonical_uri(bucket: str, object_key: str) -> str:
    parts = [bucket.strip("/"), *[part for part in object_key.strip("/").split("/") if part]]
    return "/" + "/".join(aws_quote(part) for part in parts)


def r2_put_object(endpoint: str, bucket: str, object_key: str, body: bytes, content_type: str, access_key: str, secret_key: str, region: str) -> None:
    parsed = urllib.parse.urlsplit(endpoint.rstrip("/"))
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("R2 endpoint must be an HTTPS URL.")
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    uri = r2_canonical_uri(bucket, object_key)
    canonical_headers = f"content-type:{content_type}\nhost:{parsed.netloc}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(["PUT", uri, "", canonical_headers, signed_headers, payload_hash])
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    signature = hmac.new(r2_signing_key(secret_key, date_stamp, region), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, uri, "", ""))
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Host": parsed.netloc,
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
            "Authorization": authorization,
        },
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        if int(response.status) >= 400:
            raise RuntimeError(f"R2 upload failed with HTTP {response.status}")


def r2_presigned_get_url(endpoint: str, bucket: str, object_key: str, access_key: str, secret_key: str, region: str, expires: int) -> str:
    parsed = urllib.parse.urlsplit(endpoint.rstrip("/"))
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("R2 endpoint must be an HTTPS URL.")
    ttl = min(604800, max(1, int(expires or DEFAULT_PUBLIC_ASSET_TTL_SECONDS)))
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    uri = r2_canonical_uri(bucket, object_key)
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(ttl),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(f"{aws_quote(key)}={aws_quote(value)}" for key, value in sorted(params.items()))
    canonical_headers = f"host:{parsed.netloc}\n"
    canonical_request = "\n".join(["GET", uri, canonical_query, canonical_headers, "host", "UNSIGNED-PAYLOAD"])
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    signature = hmac.new(r2_signing_key(secret_key, date_stamp, region), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    query = f"{canonical_query}&X-Amz-Signature={signature}"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, uri, query, ""))


def public_asset_secret(env_name: str, ref_env_name: str, default_ref: str) -> str:
    direct = str(os.environ.get(env_name) or "").strip()
    if direct:
        return direct
    ref = str(os.environ.get(ref_env_name) or default_ref).strip()
    return resolve_secret_value(ref)


def public_asset_object_key(prefix: str, path: Path, digest: str) -> str:
    suffix = re.sub(r"[^a-zA-Z0-9._-]+", "_", path.name).strip("._") or "reference.wav"
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    parts = [prefix.strip("/") or DEFAULT_PUBLIC_ASSET_R2_PREFIX, timestamp, f"{digest[:16]}_{suffix}"]
    return "/".join(part for part in parts if part)


def publish_clone_audio_url(audio_path: Path, reference_audio_sha256: str) -> str:
    load_public_asset_r2_env_file()
    endpoint = str(os.environ.get("OPENCREW_PUBLIC_ASSET_R2_ENDPOINT") or os.environ.get("OPENCREW_R2_ENDPOINT") or DEFAULT_PUBLIC_ASSET_R2_ENDPOINT).strip()
    bucket = str(os.environ.get("OPENCREW_PUBLIC_ASSET_R2_BUCKET") or os.environ.get("OPENCREW_R2_BUCKET") or DEFAULT_PUBLIC_ASSET_R2_BUCKET).strip()
    region = str(os.environ.get("OPENCREW_PUBLIC_ASSET_R2_REGION") or os.environ.get("OPENCREW_R2_REGION") or DEFAULT_PUBLIC_ASSET_R2_REGION).strip()
    prefix = str(os.environ.get("OPENCREW_PUBLIC_ASSET_R2_PREFIX") or DEFAULT_PUBLIC_ASSET_R2_PREFIX).strip()
    ttl = int(os.environ.get("OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS") or DEFAULT_PUBLIC_ASSET_TTL_SECONDS)
    access_key = public_asset_secret("OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID", "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_REF", "public_assets_r2_access_key_id")
    secret_key = public_asset_secret("OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY", "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY_REF", "public_assets_r2_secret_access_key")
    if not (endpoint and bucket and access_key and secret_key):
        return ""
    object_key = public_asset_object_key(prefix, audio_path, reference_audio_sha256)
    content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    r2_put_object(endpoint, bucket, object_key, audio_path.read_bytes(), content_type, access_key, secret_key, region)
    return r2_presigned_get_url(endpoint, bucket, object_key, access_key, secret_key, region, ttl)


def is_http_url(value: str) -> bool:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def load_clone_records(workspace: Path) -> list[dict[str, Any]]:
    clones_path = workspace / SESSION_CLOUD_CLONES_REL
    current = read_json(clones_path) if clones_path.exists() else {}
    return current.get("clones") if isinstance(current, dict) and isinstance(current.get("clones"), list) else []


def append_clone_record(workspace: Path, payload: dict[str, Any]) -> None:
    records = load_clone_records(workspace)
    records.append(payload)
    write_json(workspace / SESSION_CLOUD_CLONES_REL, {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "clones": records,
        "updated_at": now_iso(),
    })


def upsert_clone_record_payload(workspace: Path, payload: dict[str, Any]) -> bool:
    voice = clone_voice_value(payload)
    provider = normalized_clone_provider(str(payload.get("provider") or payload.get("source_clone_provider") or ""))
    target_model = str(payload.get("target_model") or "").strip()
    records = load_clone_records(workspace)
    replaced = False
    next_records: list[dict[str, Any]] = []
    for item in records:
        if (
            isinstance(item, dict)
            and clone_voice_value(item) == voice
            and normalized_clone_provider(str(item.get("provider") or item.get("source_clone_provider") or "")) == provider
            and (not target_model or not str(item.get("target_model") or "").strip() or str(item.get("target_model") or "").strip() == target_model)
        ):
            next_records.append({**item, **payload})
            replaced = True
        else:
            next_records.append(item)
    if not replaced:
        next_records.append(payload)
    write_json(workspace / SESSION_CLOUD_CLONES_REL, {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "clones": next_records,
        "updated_at": now_iso(),
    })
    return replaced


def safe_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip()).strip("_")
    return token[:64] or "clone"


def clone_voice_value(record: dict[str, Any]) -> str:
    return str(record.get("voice") or record.get("voice_id") or "").strip()


def clone_provider_for_target_model(target_model: str) -> str:
    model = str(target_model or "").strip().lower()
    if model.startswith("heygen"):
        return "heygen"
    if model.startswith("minimax"):
        return "minimax"
    if model.startswith("qwen"):
        return "qwen"
    return "cosyvoice"


def normalized_clone_provider(value: str) -> str:
    provider = str(value or "").strip().lower()
    if provider in {"aliyun", "aliyun_dashscope", "dashscope", "qwen", "cosyvoice"}:
        return "cosyvoice"
    if provider == "heygen":
        return "heygen"
    if provider in {"minimax", "minimaxi", "hailuo"}:
        return "minimax"
    return provider


def provider_module(provider: str) -> Any:
    normalized = normalized_clone_provider(provider)
    if normalized == "cosyvoice":
        return aliyun_voice_clone
    if normalized == "heygen":
        return heygen_voice_clone
    if normalized == "minimax":
        return minimax_voice_clone
    raise CloudCloneError("clone_provider_unsupported", f"Unsupported cloud voice clone provider: {provider}")


def clone_candidate_from_record(record: dict[str, Any]) -> dict[str, Any]:
    voice = clone_voice_value(record)
    target_model = str(record.get("target_model") or aliyun_voice_clone.DEFAULT_TARGET_MODEL).strip()
    provider = normalized_clone_provider(str(record.get("provider") or "")) or clone_provider_for_target_model(target_model)
    candidate_id = f"clone_{safe_token(target_model)}_{safe_token(voice)}"
    label = str(record.get("label") or record.get("voice_label") or f"克隆音色 {voice}").strip()
    prompt = "欢迎使用 OpenCrew。这是一段克隆音色试听，请用自然、清晰、稳定的短视频口播方式朗读。"
    return {
        "candidate_id": candidate_id,
        "provider": provider,
        "model": target_model,
        "voice": voice,
        "voice_id": voice,
        "voice_label": label,
        "voice_source": "cloud_clone",
        "source_clone_provider": str(record.get("provider") or aliyun_voice_clone.PROVIDER_ID),
        "reference_audio_sha256": str(record.get("reference_audio_sha256") or ""),
        "prompt": prompt,
        "generation_prompt": prompt,
        "tts_builder_prompt": prompt,
        "sample_audio_path": "",
        "score": 100,
        "match_score": 100,
        "scores": {"scoring_mode": "cloud_clone", "final_score": 100.0},
        "scoring_mode": "cloud_clone",
        "reason": "Cloud cloned voice created from the selected reference audio. Generate a preview before final production use.",
        "created_at": str(record.get("created_at") or now_iso()),
    }


def upsert_clone_candidate(workspace: Path, record: dict[str, Any]) -> dict[str, Any]:
    voice = clone_voice_value(record)
    if not voice:
        return {}
    candidate = clone_candidate_from_record(record)
    final_path = workspace / SESSION_TTS_FINAL_REL
    current = read_json(final_path) if final_path.exists() else {}
    payload = current if isinstance(current, dict) else {}
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    next_candidates = [
        item for item in candidates
        if not (
            isinstance(item, dict)
            and (
                str(item.get("candidate_id") or "") == candidate["candidate_id"]
                or (
                    str(item.get("voice_id") or item.get("voice") or "") == candidate["voice_id"]
                    and str(item.get("model") or "") == candidate["model"]
                    and str(item.get("voice_source") or "") == "cloud_clone"
                )
            )
        )
    ]
    payload = {
        "schema_version": payload.get("schema_version") or "analysis_v1_tts_builder_adv_candidates_0.1",
        "tool": payload.get("tool") or TOOL_NAME,
        "tool_version": payload.get("tool_version") or TOOL_VERSION,
        "source_tool_dir": payload.get("source_tool_dir") or "S5_03_03_TTSBuilderQuickAdv",
        **payload,
        "selected_candidate_id": payload.get("selected_candidate_id") or candidate["candidate_id"],
        "selected_candidate": payload.get("selected_candidate") or candidate,
        "candidates": [candidate, *next_candidates],
        "updated_at": now_iso(),
    }
    final_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(final_path, payload)
    return candidate


def remove_clone_record(workspace: Path, provider: str, voice_id: str) -> dict[str, int]:
    normalized_provider = normalized_clone_provider(provider)
    target_voice = str(voice_id or "").strip()
    if not target_voice:
        return {"removed_records": 0, "removed_candidates": 0}
    records = load_clone_records(workspace)
    next_records = [
        item for item in records
        if not (
            isinstance(item, dict)
            and clone_voice_value(item) == target_voice
            and (not normalized_provider or normalized_clone_provider(str(item.get("provider") or item.get("source_clone_provider") or "")) == normalized_provider)
        )
    ]
    removed_records = len(records) - len(next_records)
    if removed_records or (workspace / SESSION_CLOUD_CLONES_REL).exists():
        write_json(workspace / SESSION_CLOUD_CLONES_REL, {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "clones": next_records,
            "updated_at": now_iso(),
        })
    final_path = workspace / SESSION_TTS_FINAL_REL
    removed_candidates = 0
    if final_path.exists():
        current = read_json(final_path)
        payload = current if isinstance(current, dict) else {}
        candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        next_candidates = [
            item for item in candidates
            if not (
                isinstance(item, dict)
                and str(item.get("voice_id") or item.get("voice") or "").strip() == target_voice
                and str(item.get("voice_source") or "").strip() == "cloud_clone"
            )
        ]
        removed_candidates = len(candidates) - len(next_candidates)
        selected_candidate = payload.get("selected_candidate") if isinstance(payload.get("selected_candidate"), dict) else {}
        selected_voice = str(selected_candidate.get("voice_id") or selected_candidate.get("voice") or "").strip()
        selected_id = str(payload.get("selected_candidate_id") or "").strip()
        if removed_candidates or selected_voice == target_voice or any(isinstance(item, dict) and str(item.get("candidate_id") or "") == selected_id and str(item.get("voice_id") or item.get("voice") or "") == target_voice for item in candidates):
            replacement = next_candidates[0] if next_candidates else {}
            payload = {
                **payload,
                "candidates": next_candidates,
                "selected_candidate_id": str(replacement.get("candidate_id") or "") if isinstance(replacement, dict) else "",
                "selected_candidate": replacement if isinstance(replacement, dict) else {},
                "updated_at": now_iso(),
            }
            write_json(final_path, payload)
    return {"removed_records": removed_records, "removed_candidates": removed_candidates}


def clone_voice(workspace: Path, args: CloneVoiceArgs) -> dict[str, Any]:
    ensure_dirs(workspace)
    provider = normalized_clone_provider(args.provider or "cosyvoice")
    impl = provider_module(provider)
    if not args.consent_confirmed:
        raise CloudCloneError("clone_consent_required", "Cloud voice cloning requires explicit consent. Pass --clone-consent-confirmed only after the user has confirmed rights to upload and clone this reference audio.")
    target_model = str(args.target_model or getattr(impl, "DEFAULT_TARGET_MODEL", aliyun_voice_clone.DEFAULT_TARGET_MODEL)).strip()
    prefix = normalize_prefix(args.prefix)
    audio_url = str(args.audio_url or "").strip()
    provided_audio_url = bool(audio_url)
    audio_path = resolve_audio_path(workspace, args.audio_path)
    if not audio_path.exists():
        raise CloudCloneError("clone_audio_missing", f"Clone audio is missing: {relpath(audio_path, workspace)}")
    reference_audio_sha256 = sha256_file(audio_path)
    existing = next((
        item for item in reversed(load_clone_records(workspace))
        if isinstance(item, dict)
        and item.get("reference_audio_sha256") == reference_audio_sha256
        and item.get("target_model") == target_model
        and normalized_clone_provider(str(item.get("provider") or "")) == provider
        and item.get("voice_id")
    ), None)
    if existing and not args.force:
        reused = {**existing, "ok": True, "reused_existing": True, "updated_at": now_iso()}
        write_json(workspace / OUTPUT_CLOUD_CLONE_REL, reused)
        upsert_clone_candidate(workspace, reused)
        return reused
    api_key = resolve_api_key(args.api_key, args.api_key_env)
    uploaded_to_dashscope = False
    published_to_public_assets = False
    audio_publish_provider = ""
    # Some providers (e.g. MiniMax) clone from a directly uploaded local file and
    # do not need the reference audio published to a public URL.
    needs_public_audio_url = bool(getattr(impl, "REQUIRES_PUBLIC_AUDIO_URL", True))
    if needs_public_audio_url and not audio_url:
        try:
            audio_url = publish_clone_audio_url(audio_path, reference_audio_sha256)
            if audio_url:
                published_to_public_assets = True
                audio_publish_provider = "r2"
            else:
                raise CloudCloneError(
                    "clone_audio_publish_unavailable",
                    "Missing public R2 asset configuration. Put public_assets_r2.env at OPENCREW_DATA_DIR/public_assets_r2.env or set OPENCREW_PUBLIC_ASSETS_R2_ENV.",
                    failed=True,
                )
        except Exception as exc:
            if isinstance(exc, CloudCloneError):
                raise
            raise CloudCloneError("clone_audio_publish_failed", str(exc), failed=True) from exc
    if needs_public_audio_url and not is_http_url(audio_url):
        raise CloudCloneError(
            "clone_audio_url_invalid",
            f"Voice clone audio URL must start with http or https, got {urllib.parse.urlsplit(audio_url).scheme or 'empty'} URL.",
            failed=True,
        )
    consent = {
        "confirmed": True,
        "confirmed_at": now_iso(),
        "scope": "cloud_voice_clone",
        "provider": provider,
        "target_model": target_model,
        "actor": str(args.consent_actor or "cli"),
        "note": str(args.consent_note or ""),
    }
    try:
        result = impl.create_voice(
            api_key=api_key,
            target_model=target_model,
            prefix=prefix,
            audio_url=audio_url,
            audio_path=str(audio_path),
            language_hints=parse_language_hints(args.language_hints),
            max_prompt_audio_length=float(args.max_prompt_audio_length or 0.0) or None,
            workspace=args.workspace_id,
            enrollment_model=args.enrollment_model,
        )
    except Exception as exc:
        raise CloudCloneError("clone_provider_failed", str(exc), failed=True) from exc
    payload = {
        "ok": True,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "provider": provider,
        "source_clone_provider": result.get("provider") or provider,
        "target_model": target_model,
        "voice_id": result.get("voice_id") or result.get("voice"),
        "voice": result.get("voice") or result.get("voice_id"),
        "voice_source": "cloud_clone",
        "prefix": prefix,
        "reference_audio_sha256": reference_audio_sha256,
        "language_hints": parse_language_hints(args.language_hints) or [],
        "max_prompt_audio_length": float(args.max_prompt_audio_length or 0.0) or None,
        "audio_path": relpath(audio_path, workspace) if audio_path else "",
        "audio_url": audio_url if provided_audio_url else "",
        "uploaded_to_dashscope": uploaded_to_dashscope,
        "published_to_public_assets": published_to_public_assets,
        "audio_publish_provider": audio_publish_provider,
        "external_file_id": result.get("file_id"),
        "activated": result.get("activated"),
        "activation_error": result.get("activation_error") or "",
        "consent": consent,
        "request_id": result.get("request_id"),
        "created_at": now_iso(),
    }
    write_json(workspace / OUTPUT_CLOUD_CLONE_REL, payload)
    append_clone_record(workspace, payload)
    upsert_clone_candidate(workspace, payload)
    return payload


def list_cloned_voices(args: CloneVoiceArgs) -> dict[str, Any]:
    impl = provider_module(args.provider or "cosyvoice")
    api_key = resolve_api_key(args.api_key, args.api_key_env)
    try:
        result = impl.list_voices(
            api_key=api_key,
            prefix=normalize_prefix(args.prefix) if args.prefix else "",
            page_index=int(args.page_index or 0),
            page_size=int(args.page_size or 10),
            workspace=args.workspace_id,
            enrollment_model=args.enrollment_model,
        )
    except Exception as exc:
        raise CloudCloneError("clone_provider_failed", str(exc), failed=True) from exc
    return {"ok": True, **result, "updated_at": now_iso()}


def query_cloned_voice(args: CloneVoiceArgs) -> dict[str, Any]:
    impl = provider_module(args.provider or "cosyvoice")
    api_key = resolve_api_key(args.api_key, args.api_key_env)
    voice_id = str(args.voice_id or "").strip()
    if not voice_id:
        raise CloudCloneError("clone_voice_id_missing", "--clone-voice-id is required.")
    try:
        result = impl.query_voice(api_key=api_key, voice_id=voice_id, workspace=args.workspace_id, enrollment_model=args.enrollment_model)
    except Exception as exc:
        raise CloudCloneError("clone_provider_failed", str(exc), failed=True) from exc
    return {"ok": True, **result, "updated_at": now_iso()}


def import_cloned_voice(workspace: Path, args: CloneVoiceArgs) -> dict[str, Any]:
    ensure_dirs(workspace)
    provider = normalized_clone_provider(args.provider or "cosyvoice")
    impl = provider_module(provider)
    api_key = resolve_api_key(args.api_key, args.api_key_env)
    voice_id = str(args.voice_id or "").strip()
    if not voice_id:
        raise CloudCloneError("clone_voice_id_missing", "--clone-voice-id is required.")
    target_model = str(args.target_model or getattr(impl, "DEFAULT_TARGET_MODEL", aliyun_voice_clone.DEFAULT_TARGET_MODEL)).strip()
    existing = next((
        item for item in reversed(load_clone_records(workspace))
        if isinstance(item, dict)
        and clone_voice_value(item) == voice_id
        and normalized_clone_provider(str(item.get("provider") or item.get("source_clone_provider") or "")) == provider
        and (not target_model or not str(item.get("target_model") or "").strip() or str(item.get("target_model") or "").strip() == target_model)
    ), None)
    if existing:
        candidate = upsert_clone_candidate(workspace, existing)
        return {"ok": True, **existing, "candidate": candidate, "reused_existing": True, "updated_at": now_iso()}
    try:
        result = impl.query_voice(api_key=api_key, voice_id=voice_id, workspace=args.workspace_id, enrollment_model=args.enrollment_model)
    except Exception as exc:
        raise CloudCloneError("clone_provider_failed", str(exc), failed=True) from exc
    voice_data = result.get("voice") if isinstance(result.get("voice"), dict) else {}
    if not voice_data and isinstance(result.get("output"), dict):
        voice_data = result.get("output")
    label = str(
        voice_data.get("voice_name")
        or voice_data.get("name")
        or voice_data.get("label")
        or voice_id
    ).strip()
    payload = {
        "ok": True,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "provider": provider,
        "source_clone_provider": result.get("provider") or provider,
        "target_model": target_model or str(voice_data.get("target_model") or getattr(impl, "DEFAULT_TARGET_MODEL", aliyun_voice_clone.DEFAULT_TARGET_MODEL)),
        "voice_id": voice_id,
        "voice": voice_id,
        "voice_source": "cloud_clone",
        "voice_name": label,
        "voice_label": label,
        "label": label,
        "language": str(voice_data.get("language") or "").strip(),
        "gender": str(voice_data.get("gender") or "").strip(),
        "prefix": normalize_prefix(args.prefix),
        "reference_audio_sha256": "",
        "imported_from_cloud": True,
        "imported_at": now_iso(),
        "created_at": now_iso(),
        "request_id": result.get("request_id"),
    }
    replaced = upsert_clone_record_payload(workspace, payload)
    candidate = upsert_clone_candidate(workspace, payload)
    write_json(workspace / OUTPUT_CLOUD_CLONE_REL, payload)
    return {**payload, "candidate": candidate, "reused_existing": replaced, "updated_at": now_iso()}


def delete_cloned_voice(workspace: Path, args: CloneVoiceArgs) -> dict[str, Any]:
    ensure_dirs(workspace)
    impl = provider_module(args.provider or "cosyvoice")
    provider = normalized_clone_provider(args.provider or "cosyvoice")
    api_key = resolve_api_key(args.api_key, args.api_key_env)
    voice_id = str(args.voice_id or "").strip()
    if not voice_id:
        raise CloudCloneError("clone_voice_id_missing", "--clone-voice-id is required.")
    try:
        result = impl.delete_voice(api_key=api_key, voice_id=voice_id, workspace=args.workspace_id, enrollment_model=args.enrollment_model)
    except Exception as exc:
        raise CloudCloneError("clone_provider_failed", str(exc), failed=True) from exc
    if result.get("deleted") is False or result.get("delete_confirmed") is False:
        message = str(result.get("message") or f"Cloud voice clone provider did not confirm deletion for voice_id {voice_id}.").strip()
        raise CloudCloneError("clone_delete_not_confirmed", message, failed=True)
    cleanup = remove_clone_record(workspace, provider, voice_id)
    return {"ok": True, **result, **cleanup, "updated_at": now_iso()}
