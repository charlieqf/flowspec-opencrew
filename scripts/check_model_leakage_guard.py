#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from fastapi import FastAPI  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from opcrew_backend.app import include_app_routers  # noqa: E402
from opcrew_backend.model_leakage_guard import (  # noqa: E402
    CUSTOMER_EGRESS_FREE_TEXT_KEYS,
    CUSTOMER_EGRESS_KEY_DENYLIST,
    CUSTOMER_EGRESS_MODEL_KEYS,
    CUSTOMER_EGRESS_PRESERVED_PATH_KEYS,
    CUSTOMER_EGRESS_PROVIDER_KEYS,
    CUSTOMER_EGRESS_PROVIDER_VALUES,
    MODEL_LEAKAGE_BRAND_RE,
    MODEL_LEAKAGE_DENY_RE,
    sanitize_customer_payload,
    should_filter_customer_egress_path,
)
from opcrew_backend.routes.media_model_config import customer_media_public_config  # noqa: E402
from opcrew_backend.routes.auth import AUTH_ROLE_USER  # noqa: E402
from opcrew_backend.services.session_files import SessionFileService  # noqa: E402


EXPECTED_ROUTE_COUNTS = {
    "api_routes": 431,
    "excluded_api_routes": 76,
    "guarded_api_routes": 355,
    "koubo_storyboard_routes": 121,
    "guarded_koubo_storyboard_routes": 121,
}

HIGH_RISK_ROUTE_FRAGMENTS = (
    "/api/koubo-storyboard/tasks/{task_id}",
    "/clean-image/",
    "/asset-library-search/",
    "/asset-library/digital-human/",
    "/agents/{agent_key}/chat/messages",
    "/asset-library/tts-model-config",
    "/asset-library/video-model-config",
    "/asset-library/image-model-config",
    "/analysis-v1/one-click-movie",
)

LOCAL_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:/Users/[^/\\\s\"']+|/home/[^/\\\s\"']+|[A-Z]:[/\\]+Users[/\\]+[^/\\\s\"']+)[/\\]")

SAMPLE_CUSTOMER_RESPONSES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "task_detail",
        "/api/koubo-storyboard/tasks/214",
        {
            "provider": "openai",
            "model": "gpt-5.5",
            "detail": "OpenAI gpt-image-2 via generativelanguage.googleapis.com",
            "prompt": "Customer free text may mention Google style, Sora, flux, or a volcano.",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1",
            "provider_result": {"id": "resp_1", "video_url": "https://api.openai.com/v1/files/file-1"},
        },
    ),
    (
        "asset_search_local_raw",
        "/api/koubo-storyboard/tasks/214/asset-library-search/runs/search_1",
        {
            "results": [
                {
                    "id": "asset-1",
                    "raw": {"asset": {"id": "asset-1", "path": "SessionOutput/storyboard/assets/videos/local.mp4"}},
                    "snapshot": {"user_edit_marker": "preserve"},
                    "video_url": "/api/session-tasks/214/raw/SessionOutput/storyboard/assets/videos/local.mp4",
                    "endpoint": "/api/koubo-storyboard/tasks/214/asset-library-search/import",
                    "title": "Google Sora flux remains customer-visible text",
                }
            ]
        },
    ),
    (
        "digital_human_agent",
        "/api/koubo-storyboard/tasks/214/asset-library/digital-human/agents/session_1",
        {
            "provider": "heygen",
            "model": "avatar iv",
            "heygen_audio_asset_id": "aud_123",
            "agent_snapshot": {"provider_result": {"url": "https://api.heygen.com/v1/session"}},
            "asset": {
                "source": "heygen_digital_human",
                "label": "HeyGen digital human video",
                "filename": "123_heygen_digital_human_x.mp4",
                "path": "SessionOutput/storyboard/assets/videos/123_agent_digital_human_x.mp4",
            },
        },
    ),
    (
        "video_generation_result",
        "/api/koubo-storyboard/tasks/214/video-plan/execution",
        {
            "provider": "google",
            "model": "veo-3.1",
            "video_url": "https://generativelanguage.googleapis.com/v1beta/videos/video-1",
            "output": "SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4",
            "local_preview": "/api/session-tasks/214/raw/SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4",
        },
    ),
    (
        "channel_a_alias_payload",
        "/api/koubo-storyboard/tasks/214/video-plan/settings",
        {
            "provider": "Max",
            "model": "MaxWR2.7",
            "providerID": "Flash",
            "modelID": "Flash",
            "provider_label": "Max",
            "model_label": "MaxWR2.7",
        },
    ),
    (
        "analysis_v1_one_click_status",
        "/api/openclip/tasks/214/analysis-v1/one-click-movie/run_1",
        {
            "workspace_dir": "/Users/test/.opencrew/sessions/214/workspace",
            "plan": {"video_provider": "wan", "video_model": "wan2.7-r2v"},
            "summary": json.dumps({
                "workspace_dir": "/Users/test/.opencrew/sessions/214/workspace",
                "created_files": ["S9_05_02/Working/private.json"],
                "segments": [{"error": "HeyGen lipsync failed"}],
            }),
            "steps": [{"stdout_tail": "HeyGen", "argv": ["python", "/Users/test/tool.py"]}],
        },
    ),
)

SAMPLE_SENSITIVE_FILE_PATHS = (
    "SessionOutput/storyboard/assets/images/generated.json",
    "SessionOutput/storyboard/assets/videos/123_agent_digital_human_x.json",
    "SessionOutput/storyboard/koubo_storyboard_assets.json",
)

SAMPLE_PUBLIC_FILE_PATHS = (
    "SessionOutput/storyboard/assets/images/generated.png",
    "SessionOutput/storyboard/assets/videos/123_agent_digital_human_x.mp4",
)

SAMPLE_REAL_MEDIA_MODEL_CONFIGS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "image_model_config_real_shape",
        "image",
        {
            "kind": "image",
            "active_provider": "gemini",
            "providers": [
                {
                    "provider": "gemini",
                    "provider_label": "Gemini",
                    "label": "Gemini",
                    "model": "gpt-image-2",
                    "has_api_key": True,
                    "api_key_ref": "image_gemini_key",
                    "base_url": "https://generativelanguage.googleapis.com",
                    "models": [
                        {
                            "model": "grok-imagine-image-quality",
                            "label": "gpt-image-2",
                            "description": "Gemini image model via OpenRouter",
                        }
                    ],
                }
            ],
            "agent_model_aliases": [
                {"alias": "Image Alias 01", "provider": "gemini", "model": "grok-imagine-image-quality"}
            ],
        },
    ),
    (
        "video_model_config_real_shape",
        "video",
        {
            "kind": "video",
            "active_provider": "openrouter",
            "providers": [
                {
                    "provider": "openrouter",
                    "provider_label": "Kling",
                    "label": "Kling",
                    "model": "kling2.5",
                    "has_api_key": True,
                    "api_key_ref": "video_kling_key",
                    "base_url": "https://openrouter.ai/api/v1",
                    "models": [
                        {
                            "model": "grok-imagine-video-1.5-preview",
                            "label": "grok-imagine-video-1.5-preview",
                            "description": "Veo models via Gemini API",
                            "price_summary": "Volcano Ark Seedance 2.0",
                            "input_modes": ["text", "image"],
                            "duration": {"values": [4, 8], "label": "Kling duration"},
                            "reference_images": {"min": 0, "max": 2, "provider_label": "Gemini"},
                        }
                    ],
                }
            ],
            "agent_model_aliases": [
                {"alias": "Video Alias 01", "provider": "openrouter", "model": "grok-imagine-video-1.5-preview"}
            ],
        },
    ),
    (
        "video_model_config_no_alias_real_shape",
        "video",
        {
            "kind": "video",
            "active_provider": "openrouter",
            "providers": [
                {
                    "provider": "openrouter",
                    "provider_label": "Kling",
                    "label": "Kling",
                    "model": "bytedance/seedance-2.0",
                    "enabled": True,
                    "has_api_key": True,
                    "api_key_ref": "video_openrouter_key",
                    "base_url": "https://openrouter.ai/api/v1",
                    "models": [
                        {
                            "model": "bytedance/seedance-2.0",
                            "label": "Volcano Ark Seedance 2.0",
                            "description": "Veo models via Gemini API",
                            "input_modes": ["text", "image"],
                        }
                    ],
                }
            ],
            "agent_model_aliases": [],
        },
    ),
)

SAMPLE_REAL_MEDIA_MODEL_FORBIDDEN_TOKENS = (
    "grok-imagine",
    "gpt-image",
    "kling",
    "kling2.5",
    "veo",
    "gemini",
    "seedance",
    "volcano",
    "openrouter",
    "api_key_ref",
    "base_url",
    "video_kling_key",
    "image_gemini_key",
    "video_openrouter_key",
)


class _FakeContext:
    def __init__(self) -> None:
        repo = _NullRepository()
        self.config = SimpleNamespace(
            database_url="postgresql+psycopg://route-inventory:route-inventory@127.0.0.1:5433/route_inventory",
            frontend_url="http://127.0.0.1:18080/",
            backend_url="http://127.0.0.1:8011",
        )
        self.data_dir = Path("/tmp/opencrew-model-leakage-route-inventory")
        self.engine = None
        self.settings_repo = repo
        self.event_repo = repo
        self.runtime_repo = repo
        self.skill_repo = repo
        self.task_repo = repo
        self.media_library_repo = repo
        self.session_repo = repo
        self.openflow_repo = repo
        self.workspace_store = repo
        self.session_event_service = repo
        self.session_file_service = repo
        self.workflow_deletion_service = repo
        self.verification_repo = repo
        self.secret_store = repo
        self.local_usage = repo

    def get_setting(self, _key: str, default: Any = None) -> Any:
        return default

    def set_setting(self, _key: str, _value: Any) -> None:
        return None

    def event(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def sessions_root(self) -> Path:
        return self.data_dir / "sessions"


class _NullRepository:
    def get(self, *_args: Any, **_kwargs: Any) -> Any:
        return None

    def upsert(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def get_runtime(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def create(self, *_args: Any, **_kwargs: Any) -> int:
        return 1

    def __getattr__(self, _name: str) -> Any:
        def _noop(*_args: Any, **_kwargs: Any) -> Any:
            return None

        return _noop


def _is_provider_value(value: Any) -> bool:
    text = str(value or "").strip().lower().replace("_", "-")
    return bool(text) and text in CUSTOMER_EGRESS_PROVIDER_VALUES


def scan_structured_payload(value: Any, *, parent_key: str = "", path: str = "$") -> list[str]:
    findings: list[str] = []
    normalized_parent = parent_key.lower()
    if isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(scan_structured_payload(item, parent_key=parent_key, path=f"{path}[{index}]"))
        return findings
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            key_norm = key_text.lower()
            child_path = f"{path}.{key_text}"
            if key_norm in CUSTOMER_EGRESS_KEY_DENYLIST:
                findings.append(f"{child_path}: denylisted key survived")
                continue
            if key_norm in CUSTOMER_EGRESS_PROVIDER_KEYS and _is_provider_value(item):
                findings.append(f"{child_path}: provider value survived ({item!r})")
            if key_norm in CUSTOMER_EGRESS_MODEL_KEYS and isinstance(item, str) and MODEL_LEAKAGE_DENY_RE.search(item):
                findings.append(f"{child_path}: model value survived ({item!r})")
            findings.extend(scan_structured_payload(item, parent_key=key_text, path=child_path))
        return findings
    if isinstance(value, str):
        candidate = value.strip()
        if candidate[:1] in {"{", "["}:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                return scan_structured_payload(parsed, parent_key=parent_key, path=path)
        if normalized_parent in CUSTOMER_EGRESS_PRESERVED_PATH_KEYS:
            return findings
        if LOCAL_ABSOLUTE_PATH_RE.search(value):
            findings.append(f"{path}: local absolute path survived ({value!r})")
        pattern = MODEL_LEAKAGE_BRAND_RE if normalized_parent in CUSTOMER_EGRESS_FREE_TEXT_KEYS else MODEL_LEAKAGE_DENY_RE
        if pattern.search(value):
            findings.append(f"{path}: sensitive token survived ({value!r})")
    return findings


def route_inventory() -> list[dict[str, Any]]:
    ctx = _FakeContext()
    app = FastAPI()
    include_app_routers(app, ctx)  # Same router registration path used by create_app().
    entries: list[dict[str, Any]] = []
    for route in app.routes:
        path = str(getattr(route, "path", "") or "")
        methods = sorted(method for method in getattr(route, "methods", set()) if method not in {"HEAD", "OPTIONS"})
        if not path.startswith("/api/") or not methods:
            continue
        entries.append(
            {
                "path": path,
                "methods": methods,
                "endpoint": str(getattr(getattr(route, "endpoint", None), "__name__", "") or ""),
                "guarded": should_filter_customer_egress_path(path),
            }
        )
    return sorted(entries, key=lambda item: (str(item["path"]), tuple(item["methods"])))


def check_route_inventory() -> list[str]:
    entries = route_inventory()
    guarded = [item for item in entries if item["guarded"]]
    koubo_storyboard = [item for item in entries if str(item["path"]).startswith("/api/koubo-storyboard/")]
    guarded_koubo_storyboard = [item for item in koubo_storyboard if item["guarded"]]
    counts = {
        "api_routes": len(entries),
        "guarded_api_routes": len(guarded),
        "excluded_api_routes": len(entries) - len(guarded),
        "koubo_storyboard_routes": len(koubo_storyboard),
        "guarded_koubo_storyboard_routes": len(guarded_koubo_storyboard),
    }
    failures: list[str] = []
    for key, expected in EXPECTED_ROUTE_COUNTS.items():
        actual = counts[key]
        if actual != expected:
            failures.append(f"route inventory changed for {key}: {actual} != {expected}; review C0 classification")
    for fragment in HIGH_RISK_ROUTE_FRAGMENTS:
        matches = [item for item in entries if fragment in str(item["path"])]
        if not matches:
            failures.append(f"high-risk route fragment missing from inventory: {fragment}")
            continue
        unguarded = [item for item in matches if not item["guarded"]]
        if unguarded:
            failures.append(f"high-risk route fragment not guarded: {fragment}: {unguarded}")
    print(
        json.dumps(
            counts,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return failures


def check_sample_responses() -> list[str]:
    ctx = _FakeContext()
    failures: list[str] = []
    for name, path, payload in SAMPLE_CUSTOMER_RESPONSES:
        if not should_filter_customer_egress_path(path):
            failures.append(f"{name}: sample path is not guarded: {path}")
            continue
        sanitized = sanitize_customer_payload(ctx, AUTH_ROLE_USER, payload)
        findings = scan_structured_payload(sanitized)
        if findings:
            failures.extend(f"{name}: {finding}" for finding in findings)
    return failures


def check_public_media_model_config_samples() -> list[str]:
    failures: list[str] = []
    for name, kind, payload in SAMPLE_REAL_MEDIA_MODEL_CONFIGS:
        public_config = customer_media_public_config(payload, kind)
        findings = scan_structured_payload(public_config)
        if findings:
            failures.extend(f"{name}: {finding}" for finding in findings)
        serialized = json.dumps(public_config, ensure_ascii=False).lower()
        for token in SAMPLE_REAL_MEDIA_MODEL_FORBIDDEN_TOKENS:
            if token in serialized:
                failures.append(f"{name}: real config token survived ({token!r})")
    return failures


def check_session_file_policy_samples() -> list[str]:
    service = SessionFileService()
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        for rel in (*SAMPLE_SENSITIVE_FILE_PATHS, *SAMPLE_PUBLIC_FILE_PATHS):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        for rel in SAMPLE_SENSITIVE_FILE_PATHS:
            try:
                service.resolve_download(root, rel)
            except HTTPException as exc:
                if exc.status_code != 403:
                    failures.append(f"{rel}: expected HTTP 403, got {exc.status_code}")
            else:
                failures.append(f"{rel}: sensitive sidecar is downloadable")
        for rel in SAMPLE_PUBLIC_FILE_PATHS:
            try:
                service.resolve_download(root, rel)
            except HTTPException as exc:
                failures.append(f"{rel}: public media unexpectedly blocked with HTTP {exc.status_code}")
        zip_names = {arcname for _path, arcname in service.zip_entries(root, root)}
        for rel in SAMPLE_SENSITIVE_FILE_PATHS:
            if rel in zip_names:
                failures.append(f"{rel}: sensitive sidecar included in zip")
        for rel in SAMPLE_PUBLIC_FILE_PATHS:
            if rel not in zip_names:
                failures.append(f"{rel}: public media missing from zip")
    return failures


def main() -> int:
    failures = [
        *check_route_inventory(),
        *check_sample_responses(),
        *check_public_media_model_config_samples(),
        *check_session_file_policy_samples(),
    ]
    if failures:
        print("Model leakage guard check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Model leakage guard check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
