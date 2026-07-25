from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_executor_module(tool_package: str = "Analysis_V1"):
    path = REPO_ROOT / "ToolLibrary" / tool_package / "05_02_VideoPlanExecutor.py"
    module_name = f"{tool_package.lower()}_05_02_audit_contract"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1ProviderAuditContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_disable = os.environ.get("OPENCREW_DISABLE_LOCAL_USAGE_LOG")
        os.environ["OPENCREW_DISABLE_LOCAL_USAGE_LOG"] = "1"

    def tearDown(self) -> None:
        if self.previous_disable is None:
            os.environ.pop("OPENCREW_DISABLE_LOCAL_USAGE_LOG", None)
        else:
            os.environ["OPENCREW_DISABLE_LOCAL_USAGE_LOG"] = self.previous_disable

    def test_05_02_record_model_call_writes_redacted_standard_audit(self) -> None:
        module = load_executor_module()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionContext" / "Variables.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "tool_use_session_id": "tus_provider_audit",
                        "task_id": 36,
                        "opencrew_session_id": 92,
                        "current_attempt_id": 7,
                    }
                ),
                encoding="utf-8",
            )
            prompt_dir = workspace / "S9_05_02_VideoPlanExecutor" / "Prompt"
            request = {
                "provider_config": {"provider": "openai", "model": "gpt-image-1", "api_key": "sk-secret"},
                "prompt_path": "S9_05_02_VideoPlanExecutor/Prompt/prompt.json",
                "reference_paths": ["SessionOutput/storyboard/ref.png"],
                "Authorization": "Bearer sk-secret-token",
                "url": "https://example.test/v1?key=AIzaSecret",
            }
            response = {"provider": "openai", "model": "gpt-image-1", "bytes": 12, "usage": {"image": 1}}

            module.record_model_call(prompt_dir, "asset_001", "Image", request, response)

            audit_files = sorted((workspace / "S9_05_02_VideoPlanExecutor" / "Report" / "ModelCalls").glob("ModelCallAudit_*.json"))
            self.assertEqual(len(audit_files), 1)
            audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
            self.assertEqual(audit["tool_use_session_id"], "tus_provider_audit")
            self.assertEqual(audit["task_id"], 36)
            self.assertEqual(audit["attempt_id"], 7)
            self.assertEqual(audit["step_id"], "05_02")
            self.assertEqual(audit["model_provider"], "openai")
            self.assertEqual(audit["model_id"], "gpt-image-1")
            self.assertEqual(audit["usage_summary"], {"image": 1})
            self.assertEqual(audit["local_usage_status"], "disabled")
            self.assertFalse(audit["local_usage_recorded"])
            self.assertEqual(audit["local_usage_error"], "")
            text = audit_files[0].read_text(encoding="utf-8")
            self.assertNotIn("sk-secret", text)
            self.assertNotIn("Authorization", text)
            self.assertNotIn("AIzaSecret", text)

    def test_provider_audit_env_context_overrides_stale_variables(self) -> None:
        module = load_executor_module()
        previous = {name: os.environ.get(name) for name in ("OPENCREW_TASK_ID", "OPENCREW_SESSION_ID", "OPENCREW_ATTEMPT_ID", "OPENCREW_STEP_ID")}
        os.environ.update({
            "OPENCREW_TASK_ID": "116",
            "OPENCREW_SESSION_ID": "175",
            "OPENCREW_ATTEMPT_ID": "158",
            "OPENCREW_STEP_ID": "05_02",
        })
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                (workspace / "SessionContext").mkdir(parents=True)
                (workspace / "SessionContext" / "Variables.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "tool_use_session_id": "tus_provider_audit",
                            "task_id": 116,
                            "opencrew_session_id": 175,
                            "current_attempt_id": 154,
                        }
                    ),
                    encoding="utf-8",
                )
                prompt_dir = workspace / "S9_05_02_VideoPlanExecutor" / "Prompt"
                request = {
                    "provider_config": {"provider": "xai", "model": "grok-imagine-image"},
                    "prompt_path": "S9_05_02_VideoPlanExecutor/Prompt/image.json",
                }
                response = {"provider": "xai", "model": "grok-imagine-image", "usage": {"image": 1}}

                module.record_model_call(prompt_dir, "asset_001", "Image", request, response)

                audit_files = sorted((workspace / "S9_05_02_VideoPlanExecutor" / "Report" / "ModelCalls").glob("ModelCallAudit_*.json"))
                audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
                self.assertEqual(audit["task_id"], "116")
                self.assertEqual(audit["opencrew_session_id"], "175")
                self.assertEqual(audit["attempt_id"], "158")
                self.assertEqual(audit["step_id"], "05_02")
                self.assertTrue(str(audit["idempotency_key"]).startswith("model:116:158:05_02:"))
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_05_02_record_model_call_marks_local_usage_failure_in_audit(self) -> None:
        module = load_executor_module()
        os.environ["OPENCREW_DISABLE_LOCAL_USAGE_LOG"] = "0"
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionContext" / "Variables.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "tool_use_session_id": "tus_provider_audit",
                        "database_url": "postgresql+psycopg://opencrew:super-secret-db-password@127.0.0.1:9/opencrew",
                    }
                ),
                encoding="utf-8",
            )
            prompt_dir = workspace / "S9_05_02_VideoPlanExecutor" / "Prompt"
            request = {
                "provider_config": {"provider": "openai", "model": "gpt-image-1"},
                "prompt_path": "S9_05_02_VideoPlanExecutor/Prompt/prompt.json",
            }
            response = {"provider": "openai", "model": "gpt-image-1", "usage": {"image": 1}}
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                module.record_model_call(prompt_dir, "asset_001", "Image", request, response)

            audit_files = sorted((workspace / "S9_05_02_VideoPlanExecutor" / "Report" / "ModelCalls").glob("ModelCallAudit_*.json"))
            self.assertEqual(len(audit_files), 1)
            audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
            self.assertEqual(audit["local_usage_status"], "failed")
            self.assertFalse(audit["local_usage_recorded"])
            self.assertEqual(audit["local_usage_id"], "")
            self.assertTrue(audit["local_usage_error"])
            self.assertTrue(audit["local_usage_error_type"])
            self.assertIn("local usage log failed", stderr.getvalue())
            audit_text = audit_files[0].read_text(encoding="utf-8")
            self.assertNotIn("super-secret-db-password", audit_text)
            self.assertNotIn("super-secret-db-password", stderr.getvalue())

    def test_05_02_record_model_call_warns_when_audit_helper_fails(self) -> None:
        module = load_executor_module()
        with tempfile.TemporaryDirectory() as tmp:
            prompt_dir = Path(tmp) / "S9_05_02_VideoPlanExecutor" / "Prompt"
            original = module.record_model_call_from_prompt_dir
            module.record_model_call_from_prompt_dir = lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("audit helper failed postgresql://opencrew:super-secret-db-password@127.0.0.1/opencrew")
            )
            stderr = io.StringIO()
            try:
                with redirect_stderr(stderr):
                    module.record_model_call(prompt_dir, "asset_001", "Image", {"prompt": "hello"}, {"usage": {"image": 1}})
            finally:
                module.record_model_call_from_prompt_dir = original

            self.assertTrue((prompt_dir / "ModelCall_asset_001_Image_request.json").exists())
            self.assertTrue((prompt_dir / "ModelCall_asset_001_Image_response.json").exists())
            self.assertIn("provider audit failed", stderr.getvalue())
            self.assertIn("audit helper failed", stderr.getvalue())
            self.assertNotIn("super-secret-db-password", stderr.getvalue())

    def test_xai_cost_ticks_are_preserved_as_actual_cost(self) -> None:
        module = load_executor_module()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionContext" / "Variables.json").write_text(
                json.dumps({"schema_version": "1.0", "tool_use_session_id": "tus_provider_audit"}),
                encoding="utf-8",
            )
            prompt_dir = workspace / "S9_05_02_VideoPlanExecutor" / "Prompt"
            request = {
                "provider_config": {"provider": "xai", "model": "grok-imagine-video"},
                "prompt_path": "S9_05_02_VideoPlanExecutor/Prompt/video.json",
            }
            response = {
                "provider": "xai",
                "model": "grok-imagine-video",
                "duration": 4,
                "usage": {"request": 1, "video_second": 4, "cost_in_usd_ticks": 123450000},
            }

            module.record_model_call(prompt_dir, "asset_001", "Video", request, response)

            audit_files = sorted((workspace / "S9_05_02_VideoPlanExecutor" / "Report" / "ModelCalls").glob("ModelCallAudit_*.json"))
            audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
            self.assertEqual(audit["actual_cost_micros"], 12345)
            self.assertEqual(audit["actual_cost_currency"], "USD")
            self.assertEqual(audit["actual_cost_source"], "response.usage.cost_in_usd_ticks")

    def test_stale_session_default_model_falls_back_to_current_active_provider_config(self) -> None:
        module = load_executor_module()

        class FakeCursor:
            def __init__(self, conn):
                self.conn = conn

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params):
                self.conn.queries.append((query, params))
                if "AND model = %s" in query:
                    self.conn.next_row = None
                else:
                    self.conn.next_row = ("xai", "grok-imagine-video", "video_xai_key", "")

            def fetchone(self):
                return self.conn.next_row

        class FakeConn:
            def __init__(self):
                self.queries = []
                self.next_row = None
                self.closed = False

            def cursor(self):
                return FakeCursor(self)

            def close(self):
                self.closed = True

        fake_conn = FakeConn()
        original_connect = module.postgres_connect
        original_secret = module.resolve_secret_value
        module.postgres_connect = lambda database_url: fake_conn
        module.resolve_secret_value = lambda api_key_ref, legacy_value="": "stored-video-key"
        try:
            args = module.Args(
                workspace="",
                database_url="postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew",
                max_segments=0,
                force=False,
                execute_audio=True,
                execute_image=True,
                execute_video=True,
                execute_lipsync=True,
                image_provider="",
                image_model="",
                video_provider="",
                video_model="",
                lipsync_provider="",
                lipsync_model="",
                tts_provider="",
                tts_model="",
                provider_timeout_seconds=1800,
            )
            variables = {
                "default_video_config": {
                    "provider": "xai",
                    "model": "grok-imagine-video-1.5-preview",
                    "api_key_ref": "video_xai_key",
                }
            }

            config = module.load_provider_config(args, variables, "video")
        finally:
            module.postgres_connect = original_connect
            module.resolve_secret_value = original_secret

        self.assertEqual(config["provider"], "xai")
        self.assertEqual(config["model"], "grok-imagine-video")
        self.assertEqual(config["api_key"], "stored-video-key")
        self.assertEqual(config["source"], "database_active_model_fallback")
        self.assertEqual(config["requested_model"], "grok-imagine-video-1.5-preview")
        self.assertEqual(len(fake_conn.queries), 2)
        self.assertTrue(fake_conn.closed)

    def test_explicit_media_model_override_reuses_enabled_provider_credentials(self) -> None:
        for tool_package in ("Analysis_V1", "TalkingHead_V1"):
            with self.subTest(tool_package=tool_package):
                module = load_executor_module(tool_package)

                class FakeCursor:
                    def __init__(self, conn):
                        self.conn = conn

                    def __enter__(self):
                        return self

                    def __exit__(self, exc_type, exc, tb):
                        return False

                    def execute(self, query, params):
                        self.conn.queries.append((query, params))
                        if "AND model = %s" in query:
                            self.conn.next_row = None
                        else:
                            self.conn.next_row = (
                                "xai",
                                "grok-imagine-video",
                                "video_xai_key",
                                "",
                                json.dumps({"provider_option": "kept"}),
                            )

                    def fetchone(self):
                        return self.conn.next_row

                class FakeConn:
                    def __init__(self):
                        self.queries = []
                        self.next_row = None
                        self.closed = False

                    def cursor(self):
                        return FakeCursor(self)

                    def close(self):
                        self.closed = True

                fake_conn = FakeConn()
                original_connect = module.postgres_connect
                original_secret = module.resolve_secret_value
                module.postgres_connect = lambda database_url: fake_conn
                module.resolve_secret_value = lambda api_key_ref, legacy_value="": "stored-video-key"
                try:
                    args = module.Args(
                        workspace="",
                        database_url="postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew",
                        max_segments=0,
                        force=False,
                        execute_audio=True,
                        execute_image=True,
                        execute_video=True,
                        execute_lipsync=True,
                        image_provider="",
                        image_model="",
                        video_provider="xai",
                        video_model="grok-imagine-video-1.5-preview",
                        lipsync_provider="",
                        lipsync_model="",
                        tts_provider="",
                        tts_model="",
                        provider_timeout_seconds=1800,
                    )
                    variables = {
                        "default_video_config": {
                            "provider": "xai",
                            "model": "grok-imagine-video",
                            "api_key_ref": "video_xai_key",
                        }
                    }

                    config = module.load_provider_config(
                        args,
                        variables,
                        "video",
                        provider_override=args.video_provider,
                        model_override=args.video_model,
                    )
                finally:
                    module.postgres_connect = original_connect
                    module.resolve_secret_value = original_secret

                self.assertEqual(config["provider"], "xai")
                self.assertEqual(config["model"], "grok-imagine-video-1.5-preview")
                self.assertEqual(config["api_key"], "stored-video-key")
                self.assertEqual(config["source"], "database_api_key_for_requested_model")
                self.assertEqual(config["provider_option"], "kept")
                self.assertNotIn("requested_model", config)
                self.assertEqual(len(fake_conn.queries), 2)
                self.assertTrue(fake_conn.closed)

    def test_lipsync_session_default_uses_variables_for_model_and_extra_but_database_for_key(self) -> None:
        module = load_executor_module()

        class FakeCursor:
            def __init__(self, conn):
                self.conn = conn

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params):
                self.conn.queries.append((query, params))
                self.conn.next_row = (
                    "heygen",
                    "precision",
                    "lipsync_heygen_key",
                    "",
                    json.dumps({"enable_watermark": True, "folder_id": "from-db"}),
                )

            def fetchone(self):
                return self.conn.next_row

        class FakeConn:
            def __init__(self):
                self.queries = []
                self.next_row = None
                self.closed = False

            def cursor(self):
                return FakeCursor(self)

            def close(self):
                self.closed = True

        fake_conn = FakeConn()
        original_connect = module.postgres_connect
        original_secret = module.resolve_secret_value
        module.postgres_connect = lambda database_url: fake_conn
        module.resolve_secret_value = lambda api_key_ref, legacy_value="": "stored-heygen-key"
        try:
            args = module.Args(
                workspace="",
                database_url="postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew",
                max_segments=0,
                force=False,
                execute_audio=True,
                execute_image=True,
                execute_video=True,
                execute_lipsync=True,
                image_provider="",
                image_model="",
                video_provider="",
                video_model="",
                lipsync_provider="",
                lipsync_model="",
                tts_provider="",
                tts_model="",
                provider_timeout_seconds=1800,
            )
            variables = {
                "default_lipsync_config": {
                    "provider": "heygen",
                    "model": "speed",
                    "api_key_ref": "lipsync_heygen_key",
                    "extra": {"enable_watermark": False, "folder_id": "from-session"},
                }
            }

            config = module.load_provider_config(args, variables, "lipsync")
        finally:
            module.postgres_connect = original_connect
            module.resolve_secret_value = original_secret

        self.assertEqual(config["provider"], "heygen")
        self.assertEqual(config["model"], "speed")
        self.assertEqual(config["api_key"], "stored-heygen-key")
        self.assertEqual(config["source"], "database_api_key_for_session_default")
        self.assertFalse(config["enable_watermark"])
        self.assertEqual(config["folder_id"], "from-session")
        self.assertEqual(len(fake_conn.queries), 1)
        self.assertTrue(fake_conn.closed)


if __name__ == "__main__":
    unittest.main()
