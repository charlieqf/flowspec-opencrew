from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Any


PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
ORIGINAL_PROXY_ENV = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
WAN_VIDEO_SHARED_KEY_REFS = (
    "video_wan_key",
    "tts_qwen_key",
    "tts_cosyvoice_key",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "OPENCREW_DASHSCOPE_API_KEY",
    "OPENCREW_VIDEO_API_KEY",
    "WAN_API_KEY",
)
DASHSCOPE_TTS_SHARED_KEY_REFS = (
    "tts_qwen_key",
    "tts_cosyvoice_key",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "OPENCREW_TTS_API_KEY",
)
PUBLIC_ASSET_R2_ENV_KEYS = (
    "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT",
    "OPENCREW_PUBLIC_ASSET_R2_BUCKET",
    "OPENCREW_PUBLIC_ASSET_R2_REGION",
    "OPENCREW_PUBLIC_ASSET_R2_PREFIX",
    "OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS",
    "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID",
    "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY",
    "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_REF",
    "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY_REF",
)
PUBLIC_ASSET_R2_SECRET_REFS = {
    "public_assets_r2_access_key_id": "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID",
    "public_assets_r2_secret_access_key": "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    return Path(os.environ.get("OPENCREW_DATA_DIR") or (Path.home() / ".opencrew"))


def _backend_path() -> Path:
    return _repo_root() / "backend"


def _secret_store() -> Any | None:
    backend_path = _backend_path()
    if backend_path.exists() and str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))
    try:
        from opcrew_backend.services.local_secrets import LocalSecretStore  # type: ignore
    except Exception:
        return None
    try:
        return LocalSecretStore(_data_dir())
    except Exception:
        return None


def _public_asset_r2_env_candidates() -> list[Path]:
    values = [
        os.environ.get("OPENCREW_PUBLIC_ASSETS_R2_ENV"),
        os.environ.get("OPENCREW_PUBLIC_ASSET_R2_ENV"),
        str(_data_dir() / "public_assets_r2.env"),
        str(Path.home() / ".opencrew" / "public_assets_r2.env"),
    ]
    result: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        if not value:
            continue
        path = Path(value).expanduser()
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _parse_env_file_value(raw: str) -> str:
    value = str(raw or "").strip()
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


def public_asset_r2_runtime_config() -> dict[str, str]:
    """Resolve the configured public-asset R2 values for tool subprocesses.

    One-click execution intentionally strips secret-looking parent environment
    variables.  The canonical R2 configuration therefore has to be reloaded
    from OPENCREW_DATA_DIR/public_assets_r2.env inside the tool process, just as
    other Analysis_V1 runtime resources are resolved when the tool runs.
    """
    resolved = {
        key: str(os.environ.get(key) or "").strip()
        for key in PUBLIC_ASSET_R2_ENV_KEYS
        if str(os.environ.get(key) or "").strip()
    }
    missing = set(PUBLIC_ASSET_R2_ENV_KEYS) - set(resolved)
    if not missing:
        return resolved
    for path in _public_asset_r2_env_candidates():
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
            if key not in missing:
                continue
            value = _parse_env_file_value(raw_value)
            if value:
                resolved[key] = value
        break
    return resolved


def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
    ref = str(api_key_ref or "").strip()
    legacy = str(legacy_value or "").strip()
    if not ref:
        return legacy
    store = _secret_store()
    env_value = str(os.environ.get(ref) or "").strip()
    try:
        stored_value = str(store.get(ref, "")).strip() if store is not None else ""
    except Exception:
        stored_value = ""
    if stored_value or env_value or legacy:
        return stored_value or env_value or legacy
    runtime_key = PUBLIC_ASSET_R2_SECRET_REFS.get(ref, "")
    if runtime_key:
        runtime_value = public_asset_r2_runtime_config().get(runtime_key, "").strip()
        if runtime_value:
            return runtime_value
    fallback_refs: tuple[str, ...] = ()
    if ref in WAN_VIDEO_SHARED_KEY_REFS:
        fallback_refs = WAN_VIDEO_SHARED_KEY_REFS
    elif ref in DASHSCOPE_TTS_SHARED_KEY_REFS:
        fallback_refs = DASHSCOPE_TTS_SHARED_KEY_REFS
    for fallback_ref in fallback_refs:
        if fallback_ref == ref:
            continue
        try:
            fallback_stored = str(store.get(fallback_ref, "")).strip() if store is not None else ""
        except Exception:
            fallback_stored = ""
        fallback_env = str(os.environ.get(fallback_ref) or "").strip()
        if fallback_stored or fallback_env:
            return fallback_stored or fallback_env
    return ""


def store_secret_value(api_key_ref: str, value: str) -> None:
    ref = str(api_key_ref or "").strip()
    if not ref or not value:
        return
    store = _secret_store()
    if store is None:
        return
    store.set(ref, str(value))


def proxy_policy_for_provider(provider: str) -> str:
    backend_path = _backend_path()
    if backend_path.exists() and str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))
    try:
        from opcrew_backend.services.provider_resolver import proxy_policy_for_provider as backend_policy  # type: ignore

        return str(backend_policy(provider))
    except Exception:
        provider_id = str(provider or "").strip().lower()
        if provider_id in {"openai", "xai", "grok", "gemini", "google", "sync"}:
            return "mihomo"
        return "direct"


def apply_provider_proxy(provider: str) -> str:
    policy = proxy_policy_for_provider(provider)
    if policy != "mihomo":
        for key, value in ORIGINAL_PROXY_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return policy
    proxy_url = os.environ.get("OPENCREW_MIHOMO_PROXY_URL", "http://127.0.0.1:7890").strip()
    if proxy_url:
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url
        os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
        os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
    return policy
