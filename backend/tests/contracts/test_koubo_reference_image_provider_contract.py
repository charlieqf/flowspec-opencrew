from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT / "backend",):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)


class FakeConfigRow:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping


class FakeConfigResult:
    def __init__(self, row: FakeConfigRow | None) -> None:
        self.row = row

    def first(self) -> FakeConfigRow | None:
        return self.row


class FakeConfigConnection:
    def __init__(self, active: dict[str, str], providers: dict[str, dict[str, str]]) -> None:
        self.active = active
        self.providers = providers

    def execute(self, _statement, params: dict[str, str] | None = None) -> FakeConfigResult:
        provider = str((params or {}).get("provider") or "").strip()
        mapping = self.providers.get(provider) if provider else self.active
        return FakeConfigResult(FakeConfigRow(mapping) if mapping else None)


class FakeEngineBegin:
    def __init__(self, connection: FakeConfigConnection) -> None:
        self.connection = connection

    def __enter__(self) -> FakeConfigConnection:
        return self.connection

    def __exit__(self, *_args) -> None:
        return None


class FakeEngine:
    def __init__(self, active: dict[str, str], providers: dict[str, dict[str, str]]) -> None:
        self.connection = FakeConfigConnection(active, providers)

    def begin(self) -> FakeEngineBegin:
        return FakeEngineBegin(self.connection)


class KouboReferenceImageProviderContractTest(unittest.TestCase):
    def register_services(self, active: dict[str, str], providers: dict[str, dict[str, str]]) -> SimpleNamespace:
        from opcrew_backend.koubo.koubo_storyboard import provider_services

        ns = SimpleNamespace(ctx=SimpleNamespace(engine=FakeEngine(active, providers)))
        provider_services.register_provider_services(ns)
        return ns

    def test_reference_generation_keeps_active_xai_provider(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import provider_services

        keys = {"xai": "sk-xai", "openai": "sk-openai"}
        with patch.object(provider_services, "ensure_table", lambda _ctx: None), patch.object(provider_services, "load_stored_key", lambda _ctx, _kind, provider: keys.get(provider, "")):
            ns = self.register_services(
                active={"provider": "xai", "model": "grok-imagine-image-quality"},
                providers={
                    "xai": {"provider": "xai", "model": "grok-imagine-image-quality"},
                    "openai": {"provider": "openai", "model": "gpt-image-1.5"},
                    "gemini": {"provider": "gemini", "model": "gemini-3.1-flash-image"},
                },
            )

            config, fallback_from = ns.load_reference_image_config("", "", sc=ns)

        self.assertEqual(config["provider"], "xai")
        self.assertEqual(config["model"], "grok-imagine-image-quality")
        self.assertEqual(fallback_from, "")

    def test_openai_reference_generation_retries_cloudflare_520_once(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import provider_services

        ns = self.register_services(
            active={"provider": "openai", "model": "gpt-image-2"},
            providers={"openai": {"provider": "openai", "model": "gpt-image-2"}},
        )

        class FakeResponse:
            headers: dict[str, str] = {}

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self) -> bytes:
                return b'{"data":[{"b64_json":"VEVTVA=="}]}'

        calls: list[dict[str, object]] = []

        class FakeOpener:
            def open(self, request, timeout: int = 120):
                calls.append({
                    "url": request.full_url,
                    "data": request.data,
                    "headers": dict(request.header_items()),
                    "timeout": timeout,
                })
                if len(calls) == 1:
                    payload = json.dumps({"status": 520, "zone": "api.openai.com", "cloudflare_error": True, "retryable": True, "retry_after": 60}).encode("utf-8")
                    raise urllib.error.HTTPError(request.full_url, 520, "unknown origin error", {}, io.BytesIO(payload))
                return FakeResponse()

        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            ref = Path(tmpdir) / "ref.png"
            ref.write_bytes(b"\x89PNG\r\n\x1a\n")
            with patch.object(provider_services.urllib.request, "build_opener", lambda *_args, **_kwargs: FakeOpener()), patch.object(provider_services.time, "sleep", lambda seconds: sleeps.append(seconds)):
                result = ns.generate_image_bytes({"provider": "openai", "model": "gpt-image-2", "api_key": "sk-openai"}, "测试提示词", [ref], "1024x1536", sc=ns)

        self.assertEqual(result, b"TEST")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["url"], "https://api.openai.com/v1/images/edits")
        self.assertEqual(calls[1]["url"], "https://api.openai.com/v1/images/edits")
        self.assertEqual(calls[0]["timeout"], 120)
        self.assertEqual(sleeps, [60.0])
        self.assertIn(b'name="model"', calls[0]["data"])
        self.assertIn(b"gpt-image-2", calls[0]["data"])

    def test_xai_reference_generation_uses_json_edit_endpoint_for_both_grok_models(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import provider_services

        ns = self.register_services(
            active={"provider": "xai", "model": "grok-imagine-image-quality"},
            providers={"xai": {"provider": "xai", "model": "grok-imagine-image-quality"}},
        )
        image_bytes = b"\x89PNG\r\n\x1a\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            refs = []
            for index in range(3):
                path = Path(tmpdir) / f"ref_{index}.png"
                path.write_bytes(image_bytes)
                refs.append(path)
            for model in ("grok-imagine-image-quality", "grok-imagine-image"):
                calls: list[dict[str, object]] = []

                class FakeResponse:
                    headers: dict[str, str] = {}

                    def __enter__(self) -> "FakeResponse":
                        return self

                    def __exit__(self, *_args) -> None:
                        return None

                    def read(self) -> bytes:
                        return b'{"data":[{"b64_json":"VEVTVA=="}]}'

                class FakeOpener:
                    def open(self, request, timeout: int = 120) -> FakeResponse:
                        calls.append({
                            "url": request.full_url,
                            "payload": json_loads(request.data.decode("utf-8")),
                            "headers": dict(request.header_items()),
                            "timeout": timeout,
                        })
                        return FakeResponse()

                def json_loads(value: str) -> dict[str, object]:
                    import json

                    return json.loads(value)

                with patch.object(provider_services.urllib.request, "build_opener", lambda *_args, **_kwargs: FakeOpener()):
                    result = ns.generate_image_bytes({"provider": "xai", "model": model, "api_key": "sk-xai"}, "测试提示词", refs, "1024x1536", sc=ns)

                self.assertEqual(result, b"TEST")
                self.assertEqual(len(calls), 1)
                call = calls[0]
                payload = call["payload"]
                headers = call["headers"]
                self.assertEqual(call["url"], "https://api.x.ai/v1/images/edits")
                self.assertEqual(headers["Authorization"], "Bearer sk-xai")
                self.assertEqual(payload["model"], model)
                self.assertEqual(payload["prompt"], "测试提示词")
                self.assertEqual(payload["aspect_ratio"], "9:16")
                self.assertEqual(len(payload["images"]), 3)
                self.assertTrue(payload["images"][0]["url"].startswith("data:image/png;base64,"))

    def test_reference_generation_keeps_reference_capable_active_provider(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import provider_services

        keys = {"openai": "sk-openai"}
        with patch.object(provider_services, "ensure_table", lambda _ctx: None), patch.object(provider_services, "load_stored_key", lambda _ctx, _kind, provider: keys.get(provider, "")):
            ns = self.register_services(
                active={"provider": "openai", "model": "gpt-image-1.5"},
                providers={"openai": {"provider": "openai", "model": "gpt-image-1.5"}},
            )

            config, fallback_from = ns.load_reference_image_config("", "", sc=ns)

        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["model"], "gpt-image-1.5")
        self.assertEqual(fallback_from, "")


if __name__ == "__main__":
    unittest.main()
