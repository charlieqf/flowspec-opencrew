from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
MODEL_CONFIG_ROOT = REPO_ROOT / "ModelConfig" / "backend"
for path in (BACKEND_ROOT, MODEL_CONFIG_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from opcrew_backend.db.schema import metadata, sessions, video_interaction_turns  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services as services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.gemini_omni_video_services import GeminiOmniError  # noqa: E402


class FakeContext:
    def __init__(self, engine):
        self.engine = engine


class FakeRuntime:
    def __init__(self, engine, workspace: Path):
        self.ctx = FakeContext(engine)
        self.workspace = workspace
        self.task = {"id": 42, "session_id": 7, "latest_attempt_id": 1}
        self.events = []
        self.assets = []
        self.download_video_binary = services.download_video_binary
        self.uploaded_video_asset_payload = services.uploaded_video_asset_payload

    def task_or_404(self, task_id):
        assert task_id == 42
        return self.task

    def workspace_for(self, _task):
        return self.workspace

    def add_event(self, session_id, kind, payload):
        self.events.append((session_id, kind, payload))

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def upsert_asset_manifest_item(self, _workspace, asset, **_kwargs):
        self.assets.append(asset)

    def load_plan(self, _task, **_kwargs):
        raise RuntimeError("no plan")


class GeminiOmniGenerationIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.engine = create_engine(
            "sqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(
                sessions.insert().values(
                    id=7,
                    source="test",
                    group_id="test",
                    title="Omni",
                    status="active",
                    workspace_dir=str(self.workspace),
                    created_at=1,
                    updated_at=1,
                )
            )
        self.runtime = FakeRuntime(self.engine, self.workspace)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    @staticmethod
    def config():
        return {
            "provider": "gemini",
            "model": "gemini-omni-flash-preview",
            "api_key": "private-test-key",
            "agent_video_alias": "Omni Flash",
        }

    def test_turn_is_persisted_before_provider_and_head_advances_after_manifest(self):
        provider_calls = []

        def fake_provider(task, request_payload, prompt, config, output_rel, *_args, **_kwargs):
            provider_calls.append((request_payload, config))
            repo = services.video_interaction_repository(sc=self.runtime)
            repo.mark_provider_request_sent(config["_omni_turn_id"], interaction_id="private-provider-state")
            output = self.workspace / output_rel
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"sanitized-mp4")
            return {
                "ok": True,
                "output": output_rel,
                "output_path": output_rel,
                "effective_duration_seconds": 3,
                "output_width": 1280,
                "output_height": 720,
                "stateful": True,
                "operation": config["_omni_operation"],
                "local_usage_id": "usage-1",
                "local_usage": {"local_usage_id": "usage-1", "request_id": config["_omni_usage_request_id"]},
            }

        action_id = str(uuid.uuid4())
        payload = {
            "prompt": "Create a calm blue circle video",
            "aspect": "16:9",
            "duration": 3,
            "operation": "generate",
            "stateful": True,
            "client_action_id": action_id,
            "agentVideoAlias": "Omni Flash",
            "previous_interaction_id": "client-injected-provider-state",
        }
        with patch.dict(os.environ, {"OPENCREW_GEMINI_OMNI_ENABLED": "1"}), patch.object(
            services, "load_video_config_for_generation", return_value=self.config()
        ), patch.object(services, "run_asset_library_video_provider", side_effect=fake_provider):
            result = services.generate_asset_library_video(42, payload, sc=self.runtime)
            replay = services.generate_asset_library_video(42, payload, sc=self.runtime)

        self.assertEqual(len(provider_calls), 1)
        self.assertEqual(provider_calls[0][1]["_omni_previous_interaction_id"], "")
        self.assertTrue(result["can_continue"])
        self.assertEqual(replay["video_turn_id"], result["video_turn_id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.runtime.assets), 1)
        self.assertEqual(result["asset"]["video_turn_id"], result["video_turn_id"])
        self.assertNotIn("provider", result["asset"]["origin"])
        self.assertNotIn("model", result["asset"]["origin"])

        sidecar = next((self.workspace / "SessionOutput/storyboard/assets/videos").glob("*.json"))
        serialized = sidecar.read_text(encoding="utf-8")
        self.assertNotIn("private-provider-state", serialized)
        self.assertNotIn("gemini-omni-flash-preview", serialized)
        with self.engine.connect() as conn:
            turn = dict(conn.execute(select(video_interaction_turns)).one()._mapping)
        self.assertEqual(turn["interaction_id"], "private-provider-state")
        self.assertEqual(turn["local_usage_id"], "usage-1")
        self.assertEqual(turn["status"], "completed")

    def test_unknown_paid_post_result_is_not_retried(self):
        calls = 0

        def uncertain_provider(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise GeminiOmniError(
                "gemini_omni_request_failed",
                "network response was lost",
                status_code=503,
                retryable=True,
            )

        action_id = str(uuid.uuid4())
        payload = {
            "prompt": "Create a simple one second test video",
            "aspect": "16:9",
            "operation": "generate",
            "stateful": True,
            "client_action_id": action_id,
            "agentVideoAlias": "Omni Flash",
        }
        with patch.dict(os.environ, {"OPENCREW_GEMINI_OMNI_ENABLED": "1"}), patch.object(
            services, "load_video_config_for_generation", return_value=self.config()
        ), patch.object(services, "run_asset_library_video_provider", side_effect=uncertain_provider):
            with self.assertRaises(HTTPException) as first:
                services.generate_asset_library_video(42, payload, sc=self.runtime)
            replay = services.generate_asset_library_video(42, payload, sc=self.runtime)

        self.assertEqual(first.exception.detail["code"], "gemini_omni_request_failed")
        self.assertEqual(calls, 1)
        self.assertTrue(replay["pending"])
        with self.engine.connect() as conn:
            turn = dict(conn.execute(select(video_interaction_turns)).one()._mapping)
        self.assertEqual(turn["provider_request_status"], "provider_result_unknown")
        self.assertIsNone(turn["interaction_id"])

    def test_provider_metering_uses_observed_ffprobe_duration_and_stays_public(self):
        recorded = {}

        class FakeRepository:
            def mark_provider_request_sent(self, *_args, **_kwargs):
                return None

            def renew_lease(self, *_args, **_kwargs):
                return True

        class FakeClient:
            def __init__(self, _api_key):
                pass

            def run_interaction(self, _payload, *, interaction_callback, lease_callback):
                interaction_callback("private-provider-state", None, "unknown")
                lease_callback()
                return {"status": "completed"}

        def materialize(_payload, output_path, **_kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"sanitized-observed-mp4")
            return {
                "width": 1280,
                "height": 720,
                "duration_seconds": 1.75,
                "delivery": "uri",
            }

        def record_usage(*_args, **kwargs):
            recorded.update(kwargs)
            return {"local_usage_id": "usage-observed", "request_id": kwargs["request_id"]}

        config = {
            **self.config(),
            "_omni_turn_id": "turn-observed",
            "_omni_parent_turn_id": "",
            "_omni_previous_interaction_id": "",
            "_omni_lease_token": "lease-observed",
            "_omni_usage_request_id": "usage-request-observed",
            "_omni_operation": "generate",
        }
        with patch.dict(os.environ, {"OPENCREW_GEMINI_OMNI_ENABLED": "1"}), patch.object(
            services, "video_interaction_repository", return_value=FakeRepository()
        ), patch.object(services, "GeminiOmniClient", FakeClient), patch.object(
            services, "materialize_gemini_omni_video_output", side_effect=materialize
        ), patch.object(
            services,
            "validate_video_output_aspect",
            return_value={"output_width": 1280, "output_height": 720},
        ), patch.object(services, "record_storyboard_usage", side_effect=record_usage):
            result = services.run_asset_library_video_provider(
                self.runtime.task,
                {"request_id": "public-request"},
                "One-second test prompt",
                config,
                "SessionOutput/storyboard/assets/videos/observed.mp4",
                [],
                [],
                [],
                1,
                "16:9",
                sc=self.runtime,
            )

        self.assertEqual(recorded["units"]["video_720p_second"], 1.75)
        self.assertEqual(recorded["estimated_cost_micros"], 175_000)
        self.assertNotIn("provider", result)
        self.assertNotIn("model", result)
        self.assertNotIn("private-provider-state", json.dumps(result))

    def test_restart_recovery_polls_existing_interaction_without_new_paid_post(self):
        repository = services.video_interaction_repository(sc=self.runtime)
        claim = repository.create_or_replay_turn(
            task_id=42,
            session_id=7,
            actor_id="session:7",
            operation="generate",
            client_action_id=str(uuid.uuid4()),
            model_alias="Omni Flash",
            internal_provider="gemini",
            internal_model="gemini-omni-flash-preview",
            prompt="Recover this video",
            input_scope={
                "aspect": "16:9",
                "duration": 1,
                "effective_prompt": "Recover this video",
                "reference_images": [],
                "reference_videos": [],
            },
        )
        output_rel = "SessionOutput/storyboard/assets/videos/recovered.mp4"
        repository.set_planned_output(claim.turn["turn_id"], output_rel)
        repository.mark_provider_request_sent(claim.turn["turn_id"], interaction_id="existing-provider-state")
        repository.release_lease(claim.turn["turn_id"], lease_token=claim.lease_token)

        observed_gets = []

        class ExistingInteractionClient:
            created = 0

            def __init__(self, _api_key):
                pass

            def get_interaction(self, interaction_id):
                observed_gets.append(interaction_id)
                return {"id": interaction_id, "status": "completed", "output": {"type": "video", "data": "ignored-by-test"}}

            def create_interaction(self, *_args, **_kwargs):
                type(self).created += 1
                raise AssertionError("recovery must not create a second Interaction")

        def materialize(_payload, output_path, **_kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"sanitized-recovered-mp4")
            return {"width": 1280, "height": 720, "duration_seconds": 3.0, "delivery": "uri"}

        with patch.object(services, "load_video_config", return_value=self.config()), patch.object(
            services, "GeminiOmniClient", ExistingInteractionClient
        ), patch.object(services, "materialize_gemini_omni_video_output", side_effect=materialize):
            result = services.recover_gemini_omni_pending_turns(sc=self.runtime)

        self.assertEqual(result, {"scanned": 1, "recovered": 1, "remaining": 0})
        self.assertEqual(ExistingInteractionClient.created, 0)
        self.assertEqual(observed_gets, ["existing-provider-state"])
        recovered = repository.get_turn(task_id=42, actor_id="session:7", turn_id=claim.turn["turn_id"])
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["interaction_id"], "existing-provider-state")
        self.assertTrue((self.workspace / output_rel).is_file())


if __name__ == "__main__":
    unittest.main()
