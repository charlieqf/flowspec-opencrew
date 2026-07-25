from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.db.schema import metadata, sessions, video_interaction_threads, video_interaction_turns  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.video_interaction_repository import (  # noqa: E402
    VideoInteractionError,
    VideoInteractionRepository,
)


class VideoInteractionRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_000_000
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
                    sender_id="actor-7",
                    sender_name="Tester",
                    title="Omni",
                    status="active",
                    workspace_dir="/tmp/opencrew-omni-test",
                    created_at=self.now,
                    updated_at=self.now,
                )
            )
        self.repo = VideoInteractionRepository(
            self.engine,
            now_ms=lambda: self.now,
            lease_seconds=60,
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def create(self, **overrides):
        values = {
            "task_id": 42,
            "session_id": 7,
            "actor_id": "session:7",
            "operation": "generate",
            "client_action_id": str(uuid.uuid4()),
            "model_alias": "Omni Flash",
            "internal_provider": "gemini",
            "internal_model": "gemini-omni-flash-preview",
            "prompt": "A blue circle",
            "input_scope": {"aspect": "16:9"},
        }
        values.update(overrides)
        return self.repo.create_or_replay_turn(**values)

    def test_replay_returns_same_turn_and_usage_mapping_without_internal_state(self) -> None:
        action_id = str(uuid.uuid4())
        first = self.create(client_action_id=action_id)
        replay = self.create(client_action_id=action_id)

        self.assertTrue(first.created)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.turn["turn_id"], first.turn["turn_id"])
        self.assertEqual(replay.turn["usage_request_id"], first.turn["usage_request_id"])
        self.assertNotIn("interaction_id", replay.public)
        self.assertNotIn("usage_request_id", replay.public)
        self.assertNotIn("internal_model", replay.public)

        with self.engine.connect() as conn:
            count = len(conn.execute(select(video_interaction_turns)).all())
        self.assertEqual(count, 1)

    def test_same_action_with_changed_operation_or_input_is_conflict(self) -> None:
        action_id = str(uuid.uuid4())
        first = self.create(client_action_id=action_id)
        self.repo.fail_turn(first.turn["turn_id"], lease_token=first.lease_token)

        with self.assertRaises(VideoInteractionError) as changed_input:
            self.create(client_action_id=action_id, prompt="A red circle")
        self.assertEqual(changed_input.exception.code, "video_stateful_idempotency_conflict")

        with self.assertRaises(VideoInteractionError) as changed_operation:
            self.create(client_action_id=action_id, operation="edit")
        self.assertEqual(changed_operation.exception.code, "video_stateful_idempotency_conflict")

    def test_complete_advances_head_only_after_output_and_usage_are_saved(self) -> None:
        claim = self.create()
        self.repo.mark_provider_request_sent(claim.turn["turn_id"], interaction_id="internal-provider-id")
        before = self.repo.list_thread(
            task_id=42,
            actor_id="session:7",
            thread_id=claim.turn["thread_id"],
        )
        self.assertIsNone(before["head_turn_id"])

        completed = self.repo.complete_turn(
            claim.turn["turn_id"],
            lease_token=claim.lease_token,
            output_asset_id="asset-1",
            output_path="SessionOutput/storyboard/assets/videos/asset-1.mp4",
            local_usage_id="usage-1",
        )
        after = self.repo.list_thread(
            task_id=42,
            actor_id="session:7",
            thread_id=claim.turn["thread_id"],
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(after["head_turn_id"], claim.turn["turn_id"])
        self.assertTrue(after["turns"][0]["can_continue"])

    def test_failed_turn_releases_lease_and_does_not_advance_head(self) -> None:
        claim = self.create()
        self.repo.fail_turn(claim.turn["turn_id"], lease_token=claim.lease_token)
        state = self.repo.list_thread(
            task_id=42,
            actor_id="session:7",
            thread_id=claim.turn["thread_id"],
        )
        self.assertIsNone(state["head_turn_id"])
        self.assertEqual(state["turns"][0]["status"], "failed")
        with self.engine.connect() as conn:
            thread = dict(conn.execute(select(video_interaction_threads)).first()._mapping)
        self.assertIsNone(thread["lease_token"])

    def test_continue_can_branch_from_completed_history_and_active_lease_blocks_race(self) -> None:
        root = self.create()
        self.repo.mark_provider_request_sent(root.turn["turn_id"], interaction_id="provider-root")
        self.repo.complete_turn(
            root.turn["turn_id"],
            lease_token=root.lease_token,
            output_asset_id="root.mp4",
            output_path="root.mp4",
        )
        child = self.create(
            operation="continue",
            thread_id=root.turn["thread_id"],
            parent_turn_id=root.turn["turn_id"],
            prompt="Make it night",
        )
        with self.assertRaises(VideoInteractionError) as race:
            self.create(
                operation="continue",
                thread_id=root.turn["thread_id"],
                parent_turn_id=root.turn["turn_id"],
                prompt="Make it morning",
            )
        self.assertEqual(race.exception.code, "video_stateful_edit_in_progress")

        self.repo.mark_provider_request_sent(child.turn["turn_id"], interaction_id="provider-child")
        self.repo.complete_turn(
            child.turn["turn_id"],
            lease_token=child.lease_token,
            output_asset_id="child.mp4",
            output_path="child.mp4",
        )
        branch = self.create(
            operation="continue",
            thread_id=root.turn["thread_id"],
            parent_turn_id=root.turn["turn_id"],
            prompt="Make it sunset",
        )
        self.assertEqual(branch.turn["parent_turn_id"], root.turn["turn_id"])

    def test_recovery_never_recreates_unknown_provider_request(self) -> None:
        claim = self.create()
        with self.engine.begin() as conn:
            conn.execute(
                video_interaction_turns.update()
                .where(video_interaction_turns.c.turn_id == claim.turn["turn_id"])
                .values(provider_request_status="sent_without_id")
            )
            conn.execute(
                video_interaction_threads.update()
                .where(video_interaction_threads.c.thread_id == claim.turn["thread_id"])
                .values(lease_expires_at=self.now - 1)
            )
        self.assertIsNone(self.repo.claim_recovery(claim.turn["turn_id"]))
        turn = self.repo.get_turn(task_id=42, actor_id="session:7", turn_id=claim.turn["turn_id"])
        self.assertEqual(turn["provider_request_status"], "provider_result_unknown")
        self.assertNotIn(claim.turn["turn_id"], self.repo.recoverable_turn_ids())

    def test_cross_task_parent_is_rejected_and_cloud_delete_is_retryable(self) -> None:
        claim = self.create()
        self.repo.mark_provider_request_sent(claim.turn["turn_id"], interaction_id="provider-private-id")
        self.repo.complete_turn(
            claim.turn["turn_id"],
            lease_token=claim.lease_token,
            output_asset_id="asset.mp4",
            output_path="asset.mp4",
        )
        with self.assertRaises(VideoInteractionError):
            self.repo.get_turn(task_id=999, actor_id="session:7", turn_id=claim.turn["turn_id"])

        pending = self.repo.begin_cloud_delete(
            task_id=42,
            actor_id="session:7",
            thread_id=claim.turn["thread_id"],
        )
        self.assertEqual(pending, [(claim.turn["turn_id"], "provider-private-id")])
        self.repo.finish_cloud_delete(claim.turn["turn_id"], error="temporary 503")
        retry = self.repo.begin_cloud_delete(
            task_id=42,
            actor_id="session:7",
            thread_id=claim.turn["thread_id"],
        )
        self.assertEqual(len(retry), 1)
        self.repo.finish_cloud_delete(claim.turn["turn_id"])
        state = self.repo.list_thread(task_id=42, actor_id="session:7", thread_id=claim.turn["thread_id"])
        self.assertEqual(state["status"], "deleted")
        self.assertEqual(state["turns"][0]["provider_state_status"], "deleted")

    def test_cloud_delete_explicitly_abandons_unknown_turn_without_retrying_post(self) -> None:
        claim = self.create()
        self.repo.mark_provider_result_unknown(claim.turn["turn_id"])

        pending = self.repo.begin_cloud_delete(
            task_id=42,
            actor_id="session:7",
            thread_id=claim.turn["thread_id"],
        )

        self.assertEqual(pending, [])
        state = self.repo.list_thread(
            task_id=42,
            actor_id="session:7",
            thread_id=claim.turn["thread_id"],
        )
        self.assertEqual(state["status"], "deleted")
        self.assertEqual(state["turns"][0]["status"], "failed")
        self.assertEqual(state["turns"][0]["provider_state_status"], "deleted")
        self.assertEqual(state["turns"][0]["delete_status"], "deleted")
        self.assertTrue(self.create(prompt="A new action after explicit abandonment").created)

    def test_server_side_concurrency_and_hourly_limits_are_enforced(self) -> None:
        pending = self.create()
        with self.assertRaises(VideoInteractionError) as concurrent:
            self.create(prompt="A separate new thread")
        self.assertEqual(concurrent.exception.code, "video_stateful_edit_in_progress")
        self.repo.fail_turn(pending.turn["turn_id"], lease_token=pending.lease_token)

        with patch.dict(os.environ, {"OPENCREW_GEMINI_OMNI_USER_HOURLY_LIMIT": "1"}):
            with self.assertRaises(VideoInteractionError) as quota:
                self.create(prompt="A new paid action after the first one")
        self.assertEqual(quota.exception.code, "gemini_omni_quota_exceeded")


if __name__ == "__main__":
    unittest.main()
