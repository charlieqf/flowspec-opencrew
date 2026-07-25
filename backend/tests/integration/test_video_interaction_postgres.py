from __future__ import annotations

import os
import sys
import threading
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.db.migrations import run_migrations  # noqa: E402
from opcrew_backend.db.schema import metadata, sessions  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.video_interaction_repository import (  # noqa: E402
    VideoInteractionError,
    VideoInteractionRepository,
)


POSTGRES_URL = os.environ.get("OPENCREW_TEST_POSTGRES_URL", "").strip()
if POSTGRES_URL.startswith("postgresql://"):
    POSTGRES_URL = POSTGRES_URL.replace("postgresql://", "postgresql+psycopg://", 1)


@unittest.skipUnless(POSTGRES_URL, "OPENCREW_TEST_POSTGRES_URL is not configured")
class VideoInteractionPostgresIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = f"omni_test_{uuid.uuid4().hex[:12]}"
        self.control = create_engine(POSTGRES_URL, future=True)
        with self.control.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(
            POSTGRES_URL,
            future=True,
            # Keep checkfirst/create_all and every unqualified DML statement inside
            # the disposable schema. Including public here lets SQLAlchemy mistake
            # existing production-like tables for the fixture tables.
            connect_args={"options": f"-csearch_path={self.schema}"},
        )
        metadata.create_all(self.engine)
        run_migrations(self.engine)
        with self.engine.begin() as conn:
            conn.execute(
                sessions.insert().values(
                    id=7001,
                    source="test",
                    group_id="test",
                    title="Postgres lease race",
                    status="active",
                    workspace_dir="/tmp/opencrew-postgres-lease",
                    created_at=1,
                    updated_at=1,
                )
            )

    def tearDown(self) -> None:
        self.engine.dispose()
        with self.control.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.control.dispose()

    def test_migration_and_session_row_lock_admit_only_one_paid_action(self) -> None:
        self.assertIn("video_interaction_threads", inspect(self.engine).get_table_names())
        self.assertIn("video_interaction_turns", inspect(self.engine).get_table_names())
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def contender(label: str) -> None:
            repository = VideoInteractionRepository(self.engine, lease_seconds=60)
            barrier.wait()
            try:
                repository.create_or_replay_turn(
                    task_id=4201,
                    session_id=7001,
                    actor_id="session:7001",
                    operation="generate",
                    client_action_id=str(uuid.uuid4()),
                    model_alias="Omni Flash",
                    internal_provider="gemini",
                    internal_model="gemini-omni-flash-preview",
                    prompt=f"Postgres contender {label}",
                    input_scope={"aspect": "16:9"},
                )
            except VideoInteractionError as exc:
                result = exc.code
            else:
                result = "created"
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=contender, args=(str(index),)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertEqual(sorted(outcomes), ["created", "video_stateful_edit_in_progress"])


if __name__ == "__main__":
    unittest.main()
