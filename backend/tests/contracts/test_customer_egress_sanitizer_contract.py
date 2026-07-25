from __future__ import annotations

import asyncio
import gzip
import json
import sys
import unittest
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from starlette.types import Message, Scope


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.app import JsonGZipMiddleware  # noqa: E402
from opcrew_backend.model_leakage_guard import (  # noqa: E402
    CUSTOMER_EGRESS_KEY_DENYLIST,
    CustomerEgressSanitizerMiddleware,
    sanitize_customer_payload,
    should_filter_customer_egress_path,
)
from opcrew_backend.routes.auth import AUTH_ROLE_ADMIN, AUTH_ROLE_USER, build_auth_middleware, hash_password, make_token  # noqa: E402
from opcrew_backend.services.tts_voice_aliases import PUBLIC_TTS_VOICE_PREFIX, normalize_storyboard_tts_selection, resolve_tts_voice_alias  # noqa: E402


class FakeContext:
    def __init__(self) -> None:
        self.settings: dict[str, object] = {}

    def get_setting(self, key: str, default: object = None) -> object:
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: object) -> None:
        self.settings[key] = value


class AsgiResponse:
    def __init__(self, status_code: int, body: bytes, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.body = body
        self.headers = headers


def asgi_request(app: Any, *, path: str = "/api/test", accept_encoding: str = "", cookies: dict[str, str] | None = None) -> AsgiResponse:
    headers = [(b"accept-encoding", accept_encoding.encode("latin1"))] if accept_encoding else []
    if cookies:
        cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
        headers.append((b"cookie", cookie_header.encode("latin1")))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }
    sent: list[Message] = []
    received = False

    async def receive() -> Message:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    response_headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in start.get("headers", [])}
    return AsgiResponse(int(start["status"]), body, response_headers)


def response_app(content_type: str, body: bytes, *, role: str | None = AUTH_ROLE_USER, status: int = 200, chunks: list[bytes] | None = None) -> Any:
    async def app(scope: Scope, _receive: Any, send: Any) -> None:
        if role is not None:
            scope.setdefault("state", {})["opencrew_auth_role"] = role
        headers = [(b"content-type", content_type.encode("latin1"))]
        if chunks is None:
            headers.append((b"content-length", str(len(body)).encode("ascii")))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        if chunks is None:
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return
        for index, chunk in enumerate(chunks):
            await send({"type": "http.response.body", "body": chunk, "more_body": index < len(chunks) - 1})

    return app


class CustomerEgressSanitizerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = FakeContext()

    def test_guard_scope_is_default_deny_for_customer_api_routes(self) -> None:
        self.assertTrue(should_filter_customer_egress_path("/api/koubo-storyboard/tasks/214"))
        self.assertTrue(should_filter_customer_egress_path("/api/koubo-storyboard/tasks/214/agents/asset_video/chat/messages"))
        self.assertTrue(should_filter_customer_egress_path("/api/koubo-storyboard/tasks/214/clean-image"))
        self.assertTrue(should_filter_customer_egress_path("/api/future/customer-route"))
        self.assertFalse(should_filter_customer_egress_path("/api/auth/status"))
        self.assertFalse(should_filter_customer_egress_path("/api/model-config/prompt-models"))
        self.assertFalse(should_filter_customer_egress_path("/api/session-tasks/214/raw/output.json"))

    def test_user_json_response_is_sanitized(self) -> None:
        body = json.dumps({
            "provider": "heygen",
            "model": "heygen-voice-clone-v3",
            "provider_result": {"video_url": "https://api.heygen.com/video"},
            "detail": "OpenAI gpt-image-2 via generativelanguage.googleapis.com",
            "nested": {"endpoint": "https://generativelanguage.googleapis.com", "items": [{"providerID": "openai", "modelID": "gpt-5.5"}]},
        }).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx))
        payload = json.loads(response.body)
        serialized = response.body.decode("utf-8").lower()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["provider"], "")
        self.assertEqual(payload["model"], "")
        self.assertEqual(payload["nested"]["items"][0]["providerID"], "")
        self.assertNotIn("provider_result", payload)
        self.assertEqual(payload["nested"]["endpoint"], "https://[model]")
        self.assertNotIn("heygen", serialized)
        self.assertNotIn("openai", serialized)
        self.assertNotIn("googleapis", serialized)
        self.assertNotIn("[model]apis", serialized)
        self.assertNotIn("gpt-5.5", serialized)

    def test_provider_lookup_error_does_not_leak_grok_model_name(self) -> None:
        body = json.dumps({
            "detail": "No enabled video provider config found for xai/grok-imagine-video-1.5-preview."
        }).encode("utf-8")

        response = asgi_request(
            CustomerEgressSanitizerMiddleware(
                response_app("application/json", body, status=400),
                self.ctx,
            )
        )
        payload = json.loads(response.body)
        serialized = response.body.decode("utf-8").lower()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["detail"], "No enabled video provider config found for [model]/[model].")
        self.assertNotIn("xai", serialized)
        self.assertNotIn("grok", serialized)
        self.assertNotIn("imagine-video", serialized)

    def test_user_json_response_drops_internal_execution_and_template_metadata(self) -> None:
        body = json.dumps({
            "execution_result": {
                "status": "completed",
                "created_files": [
                    "S11_05_04_ImagePlanExecutor/Prompt/Ref_05_02_Image_Gemini.md",
                    "S11_05_04_ImagePlanExecutor/Prompt/Ref_05_02_Image_Grok.md",
                ],
                "model_calls": {"image": {"provider": "gemini", "model": "gemini-3.1-flash-image"}},
            },
            "prompt": {
                "schema_version": "analysis_v1_05_02_image_prompt_gemini_0.1",
                "positive_prompt": "用户可编辑提示词应保留",
                "prompt": "Gemini image generation task. Keep the customer scene.",
                "provider_profile": "image_gemini",
                "template_source": "Ref_05_02_Image_Gemini.md",
                "template_blocks": ["IMAGE_GEMINI_PROMPT"],
                "template_snapshot_chars": 1000,
            },
        }).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx))
        payload = json.loads(response.body)
        serialized = response.body.decode("utf-8").lower()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("created_files", payload["execution_result"])
        self.assertNotIn("model_calls", payload["execution_result"])
        self.assertEqual(payload["prompt"]["positive_prompt"], "用户可编辑提示词应保留")
        self.assertIn("[model] image generation task", payload["prompt"]["prompt"])
        self.assertNotIn("provider_profile", payload["prompt"])
        self.assertNotIn("template_source", payload["prompt"])
        self.assertNotIn("template_blocks", payload["prompt"])
        self.assertNotIn("template_snapshot_chars", payload["prompt"])
        self.assertNotIn("gemini", serialized)
        self.assertNotIn("grok", serialized)

    def test_user_json_response_scrubs_workspace_and_embedded_execution_metadata(self) -> None:
        embedded = json.dumps({
            "tool": "05_02_VideoPlanExecutor",
            "workspace_dir": "/Users/test/.opencrew/sessions/297/workspace",
            "created_files": ["S9_05_02/Working/private.json"],
            "segments": [{"status": "failed", "error": "HeyGen lipsync failed"}],
        })
        body = json.dumps({
            "workspace_dir": "/Users/test/.opencrew/sessions/297/workspace",
            "asset": {
                "catalog_audio_path": "/Users/test/catalog/private.wav",
                "sample_audio_path": "SessionOutput/tts/public.wav",
                "gemini_meta": {"prompt_path": "/Users/test/prompts/private.txt"},
            },
            "plan": {"video_provider": "wan", "video_model": "wan2.7-r2v"},
            "steps": [{
                "message": embedded,
                "stdout_tail": embedded,
                "stderr_tail": "provider stderr",
                "argv": ["python", "/Users/test/tool.py"],
                "script_path": "/Users/test/tool.py",
            }],
        }).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx))
        payload = json.loads(response.body)
        embedded_payload = json.loads(payload["steps"][0]["message"])
        serialized = response.body.decode("utf-8").lower()

        self.assertNotIn("workspace_dir", CUSTOMER_EGRESS_KEY_DENYLIST)
        self.assertEqual(payload["workspace_dir"], "")
        self.assertEqual(payload["asset"]["catalog_audio_path"], "")
        self.assertEqual(payload["asset"]["sample_audio_path"], "SessionOutput/tts/public.wav")
        self.assertNotIn("gemini_meta", payload["asset"])
        self.assertEqual(payload["plan"], {"video_provider": "", "video_model": ""})
        self.assertNotIn("stdout_tail", payload["steps"][0])
        self.assertNotIn("stderr_tail", payload["steps"][0])
        self.assertNotIn("argv", payload["steps"][0])
        self.assertNotIn("script_path", payload["steps"][0])
        self.assertEqual(embedded_payload["workspace_dir"], "")
        self.assertNotIn("created_files", embedded_payload)
        self.assertEqual(embedded_payload["segments"][0]["error"], "[model] lipsync failed")
        self.assertNotIn("/users/", serialized)
        self.assertNotIn("heygen", serialized)

    def test_user_json_response_scrubs_native_windows_paths(self) -> None:
        payload = sanitize_customer_payload(
            self.ctx,
            AUTH_ROLE_USER,
            {
                "workspace_dir": r"C:\Users\alice\.opencrew\sessions\297\workspace",
                "artifact_path": r"C:\Users\alice\.opencrew\private.json",
            },
        )

        self.assertEqual(payload["workspace_dir"], "")
        self.assertEqual(payload["artifact_path"], "")

    def test_tts_public_alias_fields_survive_user_sanitizer(self) -> None:
        body = json.dumps({
            "kind": "tts",
            "active_provider": "google",
            "active_public_provider": "tts_provider_01",
            "providers": [
                {
                    "provider": "google",
                    "public_provider": "tts_provider_01",
                    "provider_alias": "tts_provider_01",
                    "model": "gemini-3.1-flash-tts-preview",
                    "models": [
                        {
                            "model": "gemini-3.1-flash-tts-preview",
                            "public_model": "tts_provider_01_model_01",
                            "model_alias": "tts_provider_01_model_01",
                            "voices": [{"voice_id": "Kore", "label": "Kore - 坚定"}],
                        }
                    ],
                }
            ],
        }).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx))
        payload = json.loads(response.body)
        provider = payload["providers"][0]
        model = provider["models"][0]

        self.assertEqual(payload["active_public_provider"], "tts_provider_01")
        self.assertEqual(provider["provider"], "")
        self.assertEqual(provider["public_provider"], "tts_provider_01")
        self.assertEqual(provider["provider_alias"], "tts_provider_01")
        self.assertEqual(model["model"], "")
        self.assertEqual(model["public_model"], "tts_provider_01_model_01")
        self.assertEqual(model["model_alias"], "tts_provider_01_model_01")
        self.assertEqual(model["voices"][0]["voice_id"], "Kore")

    def test_agent_image_model_alias_survives_user_sanitizer_without_real_model_fields(self) -> None:
        body = json.dumps({
            "kind": "image",
            "agent_model_aliases": [
                {"alias": "Quality X", "provider": "xai", "model": "grok-imagine-image-quality"}
            ],
        }).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx))
        payload = json.loads(response.body)
        alias = payload["agent_model_aliases"][0]

        self.assertEqual(alias["alias"], "Quality X")
        self.assertEqual(alias["provider"], "")
        self.assertEqual(alias["model"], "")

    def test_admin_json_response_is_not_sanitized(self) -> None:
        body = json.dumps({"provider": "heygen", "model": "avatar iv", "provider_result": {"video_url": "https://api.heygen.com/video"}}).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body, role=AUTH_ROLE_ADMIN), self.ctx))
        payload = json.loads(response.body)

        self.assertEqual(payload["provider"], "heygen")
        self.assertEqual(payload["model"], "avatar iv")
        self.assertIn("provider_result", payload)

    def test_missing_role_fails_closed_to_user(self) -> None:
        body = json.dumps({"provider": "openai", "model": "gpt-5.5"}).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body, role=None), self.ctx))
        payload = json.loads(response.body)

        self.assertEqual(payload, {"provider": "", "model": ""})

    def test_generic_keys_and_customer_free_text_are_preserved(self) -> None:
        body = json.dumps({
            "raw": {"asset": {"id": "local-asset", "path": "SessionOutput/storyboard/assets/videos/local.mp4"}},
            "snapshot": {"user_edit_marker": "keep"},
            "video_url": "/api/session-tasks/214/raw/local.mp4",
            "endpoint": "/api/koubo-storyboard/tasks/214/assets",
            "prompt": "a shot of a volcano in Google style with flux and Sora as visible words",
            "title": "Google Sora flux volcano",
        }).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx))
        payload = json.loads(response.body)

        self.assertEqual(payload["raw"]["asset"]["id"], "local-asset")
        self.assertEqual(payload["snapshot"]["user_edit_marker"], "keep")
        self.assertEqual(payload["video_url"], "/api/session-tasks/214/raw/local.mp4")
        self.assertEqual(payload["endpoint"], "/api/koubo-storyboard/tasks/214/assets")
        self.assertEqual(payload["prompt"], "a shot of a volcano in Google style with flux and Sora as visible words")
        self.assertEqual(payload["title"], "Google Sora flux volcano")

    def test_digital_human_asset_brand_fields_are_sanitized(self) -> None:
        body = json.dumps({
            "asset": {
                "source": "heygen_digital_human",
                "label": "HeyGen digital human video",
                "filename": "123_heygen_digital_human_x.mp4",
                "origin": {"provider": "heygen", "model": "avatar iv"},
            }
        }).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx))
        payload = json.loads(response.body)
        serialized = response.body.decode("utf-8").lower()

        self.assertEqual(payload["asset"]["source"], "[model]")
        self.assertEqual(payload["asset"]["label"], "[model] digital human video")
        self.assertEqual(payload["asset"]["filename"], "123_[model].mp4")
        self.assertEqual(payload["asset"]["origin"]["provider"], "")
        self.assertNotIn("heygen", serialized)

    def test_alias_model_pairs_are_preserved(self) -> None:
        body = json.dumps({"provider": "Max", "model": "MaxWR2.7", "providerID": "Flash", "modelID": "Flash"}).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx))

        self.assertEqual(json.loads(response.body), {"provider": "Max", "model": "MaxWR2.7", "providerID": "Flash", "modelID": "Flash"})

    def test_sse_frames_are_sanitized_across_chunks(self) -> None:
        chunks = [
            b'data: {"provider":"hey',
            b'gen","model":"avatar iv","detail":"OpenAI googleapis"}\n\n: keepalive\n\n',
        ]

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("text/event-stream", b"", chunks=chunks), self.ctx))
        text = response.body.decode("utf-8").lower()

        self.assertIn(": keepalive", text)
        self.assertNotIn("heygen", text)
        self.assertNotIn("openai", text)
        self.assertNotIn("googleapis", text)

    def test_multiline_non_json_sse_data_preserves_data_fields(self) -> None:
        body = b"event: note\ndata: first OpenAI line\ndata: second googleapis line\n\n"

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("text/event-stream", b"", chunks=[body]), self.ctx))
        text = response.body.decode("utf-8").lower()
        data_lines = [line for line in text.splitlines() if line.startswith("data:")]

        self.assertEqual(len(data_lines), 2)
        self.assertIn("event: note", text)
        self.assertNotIn("openai", text)
        self.assertNotIn("googleapis", text)

    def test_empty_json_response_body_is_not_rewritten_to_null(self) -> None:
        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", b"", status=204), self.ctx))

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers.get("content-length"), "0")

    def test_json_response_is_sanitized_before_gzip(self) -> None:
        body = json.dumps({"items": [{"provider": "openai", "model": "gpt-5.5", "detail": "OpenAI googleapis"} for _ in range(4)]}).encode("utf-8")
        app = JsonGZipMiddleware(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx), minimum_size=1)

        response = asgi_request(app, accept_encoding="gzip")
        decompressed = gzip.decompress(response.body)
        payload = json.loads(decompressed)
        serialized = decompressed.decode("utf-8").lower()

        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertEqual(payload["items"][0]["provider"], "")
        self.assertNotIn("openai", serialized)
        self.assertNotIn("googleapis", serialized)

    def test_json_like_plain_text_response_is_sanitized(self) -> None:
        body = json.dumps({"provider": "openai", "model": "gpt-5.5"}).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("text/plain", body), self.ctx))

        self.assertEqual(json.loads(response.body), {"provider": "", "model": ""})

    def test_non_json_plain_text_response_is_not_scrubbed(self) -> None:
        body = b"OpenAI gpt-5.5 text remains untouched when the response is plain text."

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("text/plain", body), self.ctx))

        self.assertEqual(response.body, body)

    def test_file_download_path_is_not_sanitized(self) -> None:
        body = json.dumps({"provider": "openai", "model": "gpt-5.5"}).encode("utf-8")

        response = asgi_request(
            CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx),
            path="/api/session-tasks/214/raw/output.json",
        )

        self.assertEqual(json.loads(response.body), {"provider": "openai", "model": "gpt-5.5"})

    def test_excluded_auth_path_is_not_sanitized(self) -> None:
        body = json.dumps({"provider": "openai", "model": "gpt-5.5"}).encode("utf-8")

        response = asgi_request(CustomerEgressSanitizerMiddleware(response_app("application/json", body), self.ctx), path="/api/auth/status")

        self.assertEqual(json.loads(response.body), {"provider": "openai", "model": "gpt-5.5"})

    def test_http_exception_response_is_sanitized_by_middleware(self) -> None:
        app = FastAPI()
        app.add_middleware(CustomerEgressSanitizerMiddleware, ctx=self.ctx)

        @app.get("/api/leak")
        async def leak() -> None:
            raise HTTPException(status_code=502, detail="HeyGen request failed via api.openai.com and googleapis")

        response = asgi_request(app, path="/api/leak")
        serialized = response.body.decode("utf-8").lower()

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("heygen", serialized)
        self.assertNotIn("openai", serialized)
        self.assertNotIn("googleapis", serialized)

    def test_real_auth_middleware_role_reaches_customer_egress_sanitizer(self) -> None:
        self.ctx.set_setting("auth.password_hash", hash_password("admin-password"))
        app = FastAPI()
        app.middleware("http")(build_auth_middleware(self.ctx))  # type: ignore[arg-type]

        @app.get("/api/leak")
        async def leak() -> dict[str, str]:
            return {"provider": "openai", "model": "gpt-5.5"}

        app.add_middleware(CustomerEgressSanitizerMiddleware, ctx=self.ctx)

        user_response = asgi_request(app, path="/api/leak", cookies={"opencrew_session": make_token(self.ctx, AUTH_ROLE_USER)})
        admin_response = asgi_request(app, path="/api/leak", cookies={"opencrew_session": make_token(self.ctx, AUTH_ROLE_ADMIN)})

        self.assertEqual(json.loads(user_response.body), {"provider": "", "model": ""})
        self.assertEqual(json.loads(admin_response.body), {"provider": "openai", "model": "gpt-5.5"})

    def test_payload_sanitizer_drops_high_risk_subtrees(self) -> None:
        payload = sanitize_customer_payload(
            self.ctx,
            AUTH_ROLE_USER,
            {
                "ok": True,
                "snapshot": {"provider": "openai", "model": "gpt-5.5"},
                "agent_snapshot": {"provider": "openai", "model": "gpt-5.5"},
                "heygen": {"provider_result": {"video_url": "https://api.heygen.com/video"}},
                "safe": "OpenAI detail should be scrubbed",
            },
        )

        self.assertEqual(payload, {"ok": True, "snapshot": {"provider": "", "model": ""}, "safe": "[model] detail should be scrubbed"})

    def test_cloud_clone_voice_ids_use_distinct_reversible_customer_aliases(self) -> None:
        real_voice_ids = [
            "cosyvoice-v3.5-plus-ocadv-11111111111111111111",
            "cosyvoice-v3.5-plus-ocadv-22222222222222222222",
        ]
        source = {
            "provider": "cosyvoice",
            "voices": [
                {"target_model": "cosyvoice-v3.5-plus", "voice_id": voice_id, "in_current_task": index == 0}
                for index, voice_id in enumerate(real_voice_ids)
            ],
        }

        first = sanitize_customer_payload(self.ctx, AUTH_ROLE_USER, source)
        second = sanitize_customer_payload(self.ctx, AUTH_ROLE_USER, source)
        aliases = [item["voice_id"] for item in first["voices"]]

        self.assertEqual(len(set(aliases)), 2)
        self.assertTrue(all(alias.startswith(PUBLIC_TTS_VOICE_PREFIX) for alias in aliases))
        self.assertTrue(all(alias != "[model]" for alias in aliases))
        self.assertEqual(aliases, [item["voice_id"] for item in second["voices"]])
        self.assertEqual([item["in_current_task"] for item in first["voices"]], [True, False])
        for alias, real_voice_id in zip(aliases, real_voice_ids):
            target = resolve_tts_voice_alias(self.ctx, alias)
            self.assertIsNotNone(target)
            self.assertEqual((target or {}).get("voice_id"), real_voice_id)
            self.assertEqual((target or {}).get("provider"), "cosyvoice")
            self.assertEqual((target or {}).get("model"), "cosyvoice-v3.5-plus")

        preview = sanitize_customer_payload(
            self.ctx,
            AUTH_ROLE_USER,
            {"ok": True, "provider": "cosyvoice", "model": "cosyvoice-v3.5-plus", "voice_id": real_voice_ids[0]},
        )
        self.assertEqual(preview["voice_id"], aliases[0])

        opaque_clone = sanitize_customer_payload(
            self.ctx,
            AUTH_ROLE_USER,
            {"ok": True, "provider": "heygen", "voice_id": "opaque-provider-voice-id"},
        )
        self.assertTrue(opaque_clone["voice_id"].startswith(PUBLIC_TTS_VOICE_PREFIX))
        self.assertEqual((resolve_tts_voice_alias(self.ctx, opaque_clone["voice_id"]) or {}).get("voice_id"), "opaque-provider-voice-id")

    def test_storyboard_tts_selection_resolves_aliases_deduplicates_and_drops_inactive_clone_provider(self) -> None:
        cosy_voice = "cosyvoice-v3.5-plus-ocadv-active-voice"
        public = sanitize_customer_payload(
            self.ctx,
            AUTH_ROLE_USER,
            {
                "voices": [
                    {"provider": "cosyvoice", "target_model": "cosyvoice-v3.5-plus", "voice_id": cosy_voice, "candidate_id": "clone-cosy", "voice_source": "cloud_clone"},
                    {"provider": "heygen", "target_model": "heygen-voice-clone-v3", "voice_id": "opaque-heygen-voice", "candidate_id": "clone-heygen", "voice_source": "cloud_clone"},
                ]
            },
        )
        cosy_alias = public["voices"][0]["voice_id"]
        heygen_alias = public["voices"][1]["voice_id"]
        cosy_candidate = {
            "provider": "",
            "model": "",
            "voice_id": cosy_alias,
            "voice": cosy_alias,
            "candidate_id": f"clone_{cosy_alias}",
            "voice_source": "cloud_clone",
            "score": 100,
        }
        heygen_candidate = {
            "provider": "",
            "model": "",
            "voice_id": heygen_alias,
            "voice": heygen_alias,
            "candidate_id": f"clone_{heygen_alias}",
            "voice_source": "cloud_clone",
            "score": 100,
        }
        plan = {
            "shots": [],
            "storyboard_tts_selection": {
                "voice_id": cosy_alias,
                "candidate_id": f"clone_{cosy_alias}",
                "top_candidates": [cosy_candidate, heygen_candidate, dict(cosy_candidate)],
                "recommendations": [dict(cosy_candidate), dict(heygen_candidate)],
            },
        }

        normalized = normalize_storyboard_tts_selection(
            self.ctx,
            plan,
            active_clone_provider="cosyvoice",
            strict=True,
        )
        selection = normalized["storyboard_tts_selection"]

        self.assertEqual(selection["provider"], "cosyvoice")
        self.assertEqual(selection["model"], "cosyvoice-v3.5-plus")
        self.assertEqual(selection["voice_id"], cosy_voice)
        self.assertEqual(len(selection["top_candidates"]), 1)
        self.assertEqual(selection["top_candidates"], selection["recommendations"])

    def test_storyboard_tts_selection_clears_inactive_clone_when_no_candidates_remain(self) -> None:
        old_clone = {
            "provider": "heygen",
            "model": "heygen-voice-clone-v3",
            "voice_id": "old-cloud-voice",
            "voice": "old-cloud-voice",
            "candidate_id": "clone_old-cloud-voice",
            "voice_source": "cloud_clone",
            "source_clone_provider": "heygen",
        }
        plan = {
            "shots": [],
            "storyboard_tts_selection": {
                **old_clone,
                "top_candidates": [old_clone],
                "recommendations": [old_clone],
            },
        }

        normalized = normalize_storyboard_tts_selection(
            self.ctx,
            plan,
            active_clone_provider="cosyvoice",
        )
        selection = normalized["storyboard_tts_selection"]

        self.assertEqual(selection["top_candidates"], [])
        self.assertEqual(selection["recommendations"], [])
        for key in ("provider", "model", "voice_id", "voice", "candidate_id", "voice_source", "source_clone_provider"):
            self.assertNotIn(key, selection)


if __name__ == "__main__":
    unittest.main()
