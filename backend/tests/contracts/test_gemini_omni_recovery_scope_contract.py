from __future__ import annotations

import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (REPO_ROOT / "backend", REPO_ROOT / "ModelConfig" / "backend"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from opcrew_backend.db.schema import metadata, sessions, video_interaction_threads, video_interaction_turns  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard import asset_video_generation_services as services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.video_interaction_repository import VideoInteractionError  # noqa: E402


class FakeContext:
    def __init__(self, engine):
        self.engine = engine


class FakeRuntime:
    def __init__(self, engine, workspace: Path):
        self.ctx = FakeContext(engine)
        self.workspace = workspace
        self.task = {"id": 42, "session_id": 7}

    def task_or_404(self, task_id: int) -> dict[str, int]:
        if task_id != 42:
            raise AssertionError(f"unexpected recovery task: {task_id}")
        return self.task

    def workspace_for(self, _task: dict[str, int]) -> Path:
        return self.workspace


class GeminiOmniRecoveryScopeContractTest(unittest.TestCase):
    def setUp(self) -> None:
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
                    title="Recovery scope",
                    status="active",
                    workspace_dir=str(self.workspace),
                    created_at=1,
                    updated_at=1,
                )
            )
        self.runtime = FakeRuntime(self.engine, self.workspace)
        self.repository = services.video_interaction_repository(sc=self.runtime)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def create_recoverable_turn(self) -> tuple[str, str]:
        claim = self.repository.create_or_replay_turn(
            task_id=42,
            session_id=7,
            actor_id="session:7",
            operation="generate",
            client_action_id=str(uuid.uuid4()),
            model_alias="Omni Flash",
            internal_provider="gemini",
            internal_model="gemini-omni-flash-preview",
            prompt="Recover within the original task scope",
            input_scope={
                "aspect": "16:9",
                "duration": 1,
                "effective_prompt": "Recover within the original task scope",
                "reference_images": [],
                "reference_videos": [],
            },
        )
        self.repository.set_planned_output(
            claim.turn["turn_id"],
            "SessionOutput/storyboard/assets/videos/recovery-scope.mp4",
        )
        self.repository.release_lease(claim.turn["turn_id"], lease_token=claim.lease_token)
        return claim.turn["turn_id"], claim.turn["thread_id"]

    def assert_recovery_scope_error(self, turn_id: str, expected_code: str) -> None:
        with patch.object(
            services,
            "load_video_config",
            side_effect=AssertionError("provider setup must not run before recovery scope validation"),
        ) as load_video_config:
            with self.assertRaises(VideoInteractionError) as raised:
                services._recover_gemini_omni_turn(turn_id, sc=self.runtime)

        self.assertEqual(raised.exception.code, expected_code)
        load_video_config.assert_not_called()

    def test_recovery_rejects_non_uuid_thread_before_provider(self) -> None:
        turn_id, _thread_id = self.create_recoverable_turn()
        with self.engine.begin() as conn:
            conn.execute(
                video_interaction_turns.update()
                .where(video_interaction_turns.c.turn_id == turn_id)
                .values(thread_id="not-a-uuid")
            )

        self.assert_recovery_scope_error(turn_id, "video_stateful_invalid_request")

    def test_recovery_rejects_thread_from_different_task_before_provider(self) -> None:
        turn_id, thread_id = self.create_recoverable_turn()
        with self.engine.begin() as conn:
            conn.execute(
                video_interaction_threads.update()
                .where(video_interaction_threads.c.thread_id == thread_id)
                .values(task_id=999)
            )

        self.assert_recovery_scope_error(turn_id, "gemini_omni_previous_interaction_invalid")

    def test_recovery_rejects_thread_from_different_actor_before_provider(self) -> None:
        turn_id, thread_id = self.create_recoverable_turn()
        with self.engine.begin() as conn:
            conn.execute(
                video_interaction_threads.update()
                .where(video_interaction_threads.c.thread_id == thread_id)
                .values(actor_id="session:999")
            )

        self.assert_recovery_scope_error(turn_id, "gemini_omni_previous_interaction_invalid")


if __name__ == "__main__":
    unittest.main()
