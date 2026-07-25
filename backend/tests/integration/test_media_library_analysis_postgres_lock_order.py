from __future__ import annotations

import os
import sys
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, delete, select, text


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_tasks,
    sessions,
)
from opcrew_backend.media_library_analysis.run_repository import (  # noqa: E402
    AnalysisRunRepository,
)


SOURCE_VERSION = "f" * 64


class MediaLibraryAnalysisPostgresLockOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.environ.get(
            "OPENCREW_TEST_POSTGRES_URL", ""
        ).strip()
        if not database_url:
            raise unittest.SkipTest(
                "OPENCREW_TEST_POSTGRES_URL is required for PostgreSQL lock-order acceptance"
            )
        if not database_url.startswith("postgresql"):
            raise unittest.SkipTest(
                "PostgreSQL is required for lock-order acceptance"
            )
        cls.application_name = (
            "opencrew-analysis-lock-order-" + uuid.uuid4().hex[:10]
        )
        cls.engine = create_engine(
            database_url,
            pool_size=8,
            max_overflow=0,
            connect_args={"application_name": cls.application_name},
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:12]
        self.asset_id = f"accept-lock-{suffix}"
        self.session_id = 0
        with self.engine.begin() as conn:
            self.session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="media-library-lock-acceptance",
                        group_id="media-library-lock-acceptance",
                        title=self.asset_id,
                        status="draft",
                        workspace_dir=f"/tmp/{self.asset_id}",
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id=self.asset_id,
                    session_id=self.session_id,
                    display_name=self.asset_id,
                    original_filename="source.mp4",
                    source_video_path="inbox/source.mp4",
                    content_sha256=SOURCE_VERSION,
                    content_hashed_at=1,
                    media_type="video",
                    duration_ms=1000,
                    upload_status="ready",
                    analysis_status="not_analyzed",
                    subtitle_mode="unknown",
                    tags_json=[],
                    archived=False,
                    referenced_by_count=0,
                    created_at=1,
                    updated_at=1,
                )
            )
            conn.execute(
                media_library_tasks.insert().values(
                    asset_id=self.asset_id,
                    session_id=self.session_id,
                    title=self.asset_id,
                    status="draft",
                    dialogue_status="not_analyzed",
                    visual_status="not_analyzed",
                    visual_structure_status="not_analyzed",
                    visual_semantic_status="not_analyzed",
                    composite_status="not_analyzed",
                    created_at=1,
                    updated_at=1,
                )
            )
        self.repo = AnalysisRunRepository(self.engine)

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                delete(media_library_analysis_runs).where(
                    media_library_analysis_runs.c.asset_id == self.asset_id
                )
            )
            conn.execute(
                delete(media_library_tasks).where(
                    media_library_tasks.c.asset_id == self.asset_id
                )
            )
            conn.execute(
                delete(media_library_assets).where(
                    media_library_assets.c.asset_id == self.asset_id
                )
            )
            conn.execute(
                delete(sessions).where(sessions.c.id == self.session_id)
            )

    def _wait_until_projection_writer_blocks_on_asset(
        self, locker_pid: int
    ) -> None:
        deadline = time.monotonic() + 10
        query = text(
            """
            SELECT count(*)
              FROM pg_stat_activity
             WHERE datname = current_database()
               AND application_name = :application_name
               AND pid <> :locker_pid
               AND wait_event_type = 'Lock'
               AND query ILIKE '%media_library_assets%'
            """
        )
        while time.monotonic() < deadline:
            with self.engine.connect() as conn:
                count = int(
                    conn.execute(
                        query,
                        {
                            "application_name": self.application_name,
                            "locker_pid": locker_pid,
                        },
                    ).scalar_one()
                )
            if count:
                return
            time.sleep(0.05)
        self.fail(
            "analysis projection writer did not block on the asset lock"
        )

    def test_projection_locks_asset_before_task(self) -> None:
        run = self.repo.create_queued(
            asset_id=self.asset_id,
            scheme="dialogue",
            timestamp=100,
        )
        locker = self.engine.connect()
        locker_transaction = locker.begin()
        locker_pid = int(
            locker.execute(text("SELECT pg_backend_pid()")).scalar_one()
        )
        locker.execute(
            select(media_library_assets.c.asset_id)
            .where(media_library_assets.c.asset_id == self.asset_id)
            .with_for_update()
        ).one()

        started = threading.Event()

        def mark_running() -> dict[str, object]:
            started.set()
            return self.repo.mark_running(
                str(run["analysis_run_id"]),
                timestamp=101,
                tool_use_session_id="tus-lock-order",
            )

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(mark_running)
        try:
            self.assertTrue(started.wait(timeout=2))
            self._wait_until_projection_writer_blocks_on_asset(locker_pid)
            # A task-first writer would already own this row while waiting
            # for the asset and NOWAIT would fail. Success proves the
            # shared row order is asset -> task.
            with self.engine.begin() as probe:
                probe.execute(
                    select(media_library_tasks.c.id)
                    .where(
                        media_library_tasks.c.asset_id == self.asset_id
                    )
                    .with_for_update(nowait=True)
                ).one()
            locker_transaction.commit()
            result = future.result(timeout=10)
        finally:
            if locker_transaction.is_active:
                locker_transaction.rollback()
            locker.close()
            executor.shutdown(wait=True, cancel_futures=True)

        self.assertEqual(result["status"], "running")

    def test_cross_scheme_create_and_progress_overlap_has_no_deadlock(
        self,
    ) -> None:
        for iteration in range(12):
            timestamp = 1000 + iteration * 10
            dialogue = self.repo.create_queued(
                asset_id=self.asset_id,
                scheme="dialogue",
                timestamp=timestamp,
            )
            barrier = threading.Barrier(2)

            def mark_dialogue_running() -> dict[str, object]:
                barrier.wait(timeout=5)
                return self.repo.mark_running(
                    str(dialogue["analysis_run_id"]),
                    timestamp=timestamp + 1,
                    tool_use_session_id=f"tus-dialogue-{iteration}",
                )

            def create_visual_structure() -> dict[str, object]:
                barrier.wait(timeout=5)
                return self.repo.create_queued(
                    asset_id=self.asset_id,
                    scheme="visual_structure",
                    timestamp=timestamp + 2,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                dialogue_future = executor.submit(mark_dialogue_running)
                visual_future = executor.submit(create_visual_structure)
                dialogue_result = dialogue_future.result(timeout=10)
                visual_result = visual_future.result(timeout=10)

            self.assertEqual(dialogue_result["status"], "running")
            self.assertEqual(visual_result["status"], "queued")
            self.repo.finish_unsuccessful(
                str(dialogue_result["analysis_run_id"]),
                status="failed",
                timestamp=timestamp + 3,
                error_code="acceptance_cleanup",
                error={"code": "acceptance_cleanup"},
            )
            self.repo.finish_unsuccessful(
                str(visual_result["analysis_run_id"]),
                status="failed",
                timestamp=timestamp + 4,
                error_code="acceptance_cleanup",
                error={"code": "acceptance_cleanup"},
            )


if __name__ == "__main__":
    unittest.main()
