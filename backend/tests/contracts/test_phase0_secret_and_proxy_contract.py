from __future__ import annotations

import importlib.util
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT / "backend", REPO_ROOT / "ToolLibrary"):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from opcrew_backend.services.local_secrets import LocalSecretStore  # noqa: E402
from opcrew_backend.services.provider_resolver import normalize_provider, proxy_policy_for_provider, resolve_endpoint  # noqa: E402
from opcrew_backend.routes import media_model_config  # noqa: E402
from opcrew_backend.routes.media_model_config import provider_has_submitted_or_stored_key  # noqa: E402


class FakeConfigRow:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping


class FakeConfigResult:
    def __init__(self, row: FakeConfigRow | None) -> None:
        self.row = row

    def first(self) -> FakeConfigRow | None:
        return self.row


class FakeConfigConnection:
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping

    def execute(self, *_args, **_kwargs) -> FakeConfigResult:
        return FakeConfigResult(FakeConfigRow(self.mapping) if self.mapping is not None else None)


class FakeConfigRowsResult:
    def __init__(self, rows: list[FakeConfigRow]) -> None:
        self.rows = rows

    def all(self) -> list[FakeConfigRow]:
        return self.rows


class FakeConfigRowsConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = [FakeConfigRow(row) for row in rows]

    def __enter__(self) -> "FakeConfigRowsConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, *_args, **_kwargs) -> FakeConfigRowsResult:
        return FakeConfigRowsResult(self.rows)


class FakeConfigRowsEngine:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def begin(self) -> FakeConfigRowsConnection:
        return FakeConfigRowsConnection(self.rows)


class FakeConfigSecretStore:
    def __init__(self, refs: set[str]) -> None:
        self.refs = refs

    def has(self, ref: str) -> bool:
        return ref in self.refs


class Phase0SecretStoreContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_env = {key: os.environ.get(key) for key in ("OPENCREW_SECRET_STORE_PATH", "OPENCREW_SECRET_STORE_KEY", "OPENCREW_SECRET_STORE_DEV_KEY_PATH", "OPENCREW_SECRET_STORE_KEYCHAIN", "OPENCREW_DATA_DIR")}
        for key in self.old_env:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_default_store_uses_data_dir_and_local_key_file(self) -> None:
        store = LocalSecretStore(self.root)
        store.set("openai", "sk-test")

        self.assertEqual(store.path, self.root / "secrets.enc")
        self.assertEqual(store.key_source, "local_key_file")
        self.assertEqual(store.get("openai"), "sk-test")
        self.assertEqual(self.mode(store.path), 0o600)
        self.assertEqual(self.mode(self.root / "secret_store.key"), 0o600)

    def test_set_many_and_cross_instance_writes_do_not_drop_keys(self) -> None:
        first = LocalSecretStore(self.root)
        second = LocalSecretStore(self.root)
        first.set_many({"openai": "sk-openai", "gemini": "gemini-key"})

        def write_value(ref: str, value: str) -> None:
            LocalSecretStore(self.root).set(ref, value)

        threads = [threading.Thread(target=write_value, args=(f"provider_{index}", f"value_{index}")) for index in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(second.get("openai"), "sk-openai")
        self.assertEqual(second.get("gemini"), "gemini-key")
        for index in range(12):
            self.assertEqual(second.get(f"provider_{index}"), f"value_{index}")

    def test_corrupt_store_returns_default_without_masking_legacy_value(self) -> None:
        store = LocalSecretStore(self.root)
        store.set("openai", "sk-test")
        store.path.write_text("not encrypted", encoding="utf-8")

        self.assertEqual(store.get("openai", "legacy-key"), "legacy-key")


class Phase0ProviderProxyContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_env = {key: os.environ.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "OPENCREW_MIHOMO_PROXY_URL", "OPENCREW_DATA_DIR", "OPENCREW_SECRET_STORE_PATH", "OPENCREW_SECRET_STORE_KEY")}

    def tearDown(self) -> None:
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def load_runtime_module(self):
        module_path = REPO_ROOT / "ToolLibrary" / "opencrew_runtime_secrets.py"
        module_name = f"opencrew_runtime_secrets_test_{id(self)}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_provider_policy_covers_abroad_and_cn_models(self) -> None:
        self.assertEqual(normalize_provider("google"), "gemini")
        self.assertEqual(normalize_provider("grok"), "xai")
        for provider in ("openai", "gemini", "google", "xai", "grok", "sync"):
            self.assertEqual(proxy_policy_for_provider(provider), "mihomo", provider)
        for provider in ("qwen", "wan", "minimax", "dashscope", "aliyun_bailian_fun_asr", "kling"):
            self.assertEqual(proxy_policy_for_provider(provider), "direct", provider)
        self.assertEqual(resolve_endpoint("sync", "lipsync-2", "video", "sync_key").base_url, "https://api.sync.so")
        self.assertEqual(resolve_endpoint("kling", "kling-3.0-turbo", "video", "kling_key").base_url, "https://api-beijing.klingai.com")
        self.assertEqual(resolve_endpoint("kling", "kling-lipsync-advanced", "lipsync", "kling_key").base_url, "https://api-beijing.klingai.com")

    def test_toollibrary_proxy_restores_original_proxy_for_direct_provider(self) -> None:
        os.environ["HTTP_PROXY"] = "http://corp.proxy:8080"
        os.environ["HTTPS_PROXY"] = "http://corp.proxy:8080"
        os.environ.pop("http_proxy", None)
        os.environ.pop("https_proxy", None)
        runtime = self.load_runtime_module()

        self.assertEqual(runtime.apply_provider_proxy("gemini"), "mihomo")
        self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(runtime.apply_provider_proxy("qwen"), "direct")
        self.assertEqual(os.environ["HTTPS_PROXY"], "http://corp.proxy:8080")
        self.assertNotIn("https_proxy", os.environ)

    def test_toollibrary_resolves_secret_store_ref_with_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.environ["OPENCREW_DATA_DIR"] = str(root)
            os.environ.pop("OPENCREW_SECRET_STORE_PATH", None)
            os.environ.pop("OPENCREW_SECRET_STORE_KEY", None)
            LocalSecretStore(root).set("image_openai_key", "sk-from-store")
            runtime = self.load_runtime_module()

            self.assertEqual(runtime.resolve_secret_value("image_openai_key", "legacy"), "sk-from-store")
            self.assertEqual(runtime.resolve_secret_value("missing_ref", "legacy"), "legacy")

    def test_toollibrary_runtime_reuses_dashscope_key_for_wan_video_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.environ["OPENCREW_DATA_DIR"] = str(root)
            os.environ.pop("OPENCREW_SECRET_STORE_PATH", None)
            os.environ.pop("OPENCREW_SECRET_STORE_KEY", None)
            LocalSecretStore(root).set("tts_qwen_key", "dashscope-shared-key")
            runtime = self.load_runtime_module()

            self.assertEqual(runtime.resolve_secret_value("video_wan_key", ""), "dashscope-shared-key")

    def test_toollibrary_runtime_reads_secret_env_ref_without_store_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.environ["OPENCREW_DATA_DIR"] = str(root)
            os.environ["DASHSCOPE_API_KEY"] = "dashscope-env-key"
            runtime = self.load_runtime_module()

            self.assertEqual(runtime.resolve_secret_value("video_wan_key", ""), "dashscope-env-key")
            self.assertEqual(runtime.resolve_secret_value("DASHSCOPE_API_KEY", ""), "dashscope-env-key")

    def test_media_active_provider_key_guard_accepts_submitted_store_or_legacy_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalSecretStore(Path(tmp))
            ctx = SimpleNamespace(secret_store=store)

            self.assertTrue(provider_has_submitted_or_stored_key(ctx, FakeConfigConnection(), "image", "xai", "submitted-key"))
            self.assertFalse(provider_has_submitted_or_stored_key(ctx, FakeConfigConnection(), "image", "xai", ""))

            store.set("image_xai_key", "stored-key")
            self.assertTrue(provider_has_submitted_or_stored_key(ctx, FakeConfigConnection(), "image", "xai", ""))

            store.set("custom_image_key", "custom-stored-key")
            self.assertTrue(provider_has_submitted_or_stored_key(ctx, FakeConfigConnection({"api_key_ref": "custom_image_key", "api_key_ciphertext": ""}), "image", "xai", ""))
            self.assertTrue(provider_has_submitted_or_stored_key(ctx, FakeConfigConnection({"api_key_ref": "", "api_key_ciphertext": "legacy-key"}), "image", "openai", ""))

    def test_media_load_config_keeps_stored_active_provider_when_default_provider_has_no_row(self) -> None:
        ctx = SimpleNamespace(
            engine=FakeConfigRowsEngine([
                {
                    "kind": "voice-clone",
                    "provider": "heygen",
                    "enabled": True,
                    "active": True,
                    "model": "heygen-voice-clone-v3",
                    "api_key_ciphertext": "",
                    "api_key_ref": "voice_clone_heygen_key",
                    "extra_json": "{}",
                    "updated_at": None,
                }
            ]),
            secret_store=FakeConfigSecretStore({"voice_clone_heygen_key"}),
        )
        original_ensure_table = media_model_config.ensure_table
        media_model_config.ensure_table = lambda _ctx: None
        try:
            config = media_model_config.load_config(ctx, "voice-clone")
        finally:
            media_model_config.ensure_table = original_ensure_table

        providers = {item["provider"]: item for item in config["providers"]}
        self.assertEqual(config["active_provider"], "heygen")
        self.assertFalse(providers["cosyvoice"]["active"])
        self.assertTrue(providers["heygen"]["active"])
        self.assertTrue(providers["heygen"]["has_api_key"])

    def test_lipsync_options_include_kling_advanced_model(self) -> None:
        providers = {item["provider"]: item for item in media_model_config.media_options("lipsync")}

        self.assertIn("kling", providers)
        kling = providers["kling"]
        self.assertEqual(kling["provider_label"], "Kling AI")
        self.assertEqual(kling["docs_url"], "https://klingai.com/document-api/api/video/lip-sync")
        self.assertEqual(kling["models"][0]["model"], "kling-lipsync-advanced")
        self.assertIn("¥0.5 per 5 seconds", kling["models"][0]["price_summary"])

    def test_voice_clone_options_include_minimax_with_group_id_extra(self) -> None:
        providers = {item["provider"]: item for item in media_model_config.media_options("voice-clone")}

        self.assertIn("minimax", providers)
        minimax = providers["minimax"]
        self.assertEqual(minimax["provider_label"], "MiniMax")
        self.assertEqual(minimax["models"][0]["model"], "minimax-voice-clone-v1")
        # The UI renders a Group ID field for any provider whose default_extra_json
        # declares group_id; MiniMax requires it to call the API.
        self.assertIn("group_id", minimax["default_extra_json"])

    def test_media_provider_save_payload_accepts_extra(self) -> None:
        # The save endpoint must carry provider-specific extra (e.g. MiniMax
        # group_id) so it can be configured from the UI.
        payload = media_model_config.MediaProviderSavePayload(
            provider="minimax",
            model="minimax-voice-clone-v1",
            extra={"group_id": "1234567890123456789"},
        )
        self.assertEqual(payload.extra.get("group_id"), "1234567890123456789")


if __name__ == "__main__":
    unittest.main()
