from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    event_logs,
    media_library_assets,
    metadata,
    sessions,
)
from scripts.backfill_media_library_source_hashes import run_backfill  # noqa: E402


class MediaLibrarySourceIdentityContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        (self.workspace / "inbox").mkdir(parents=True)
        self.source = self.workspace / "inbox" / "原片.mp4"
        self.source.write_bytes(b"immutable-source")
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="open-cut-v1",
                        group_id="open-cut-v1",
                        title="source identity",
                        status="draft",
                        workspace_dir=str(self.workspace),
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id="asset-hash",
                    session_id=session_id,
                    display_name="原片",
                    original_filename="原片.mp4",
                    source_video_path="inbox/原片.mp4",
                    media_type="video",
                    size_bytes=self.source.stat().st_size,
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

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def test_backfill_is_dry_run_by_default_and_write_is_idempotent(self) -> None:
        expected = hashlib.sha256(b"immutable-source").hexdigest()

        dry_run = run_backfill(self.engine)
        self.assertTrue(dry_run["dry_run"])
        self.assertEqual(dry_run["candidate_count"], 1)
        self.assertEqual(dry_run["items"][0]["content_sha256"], expected)
        with self.engine.connect() as conn:
            self.assertIsNone(
                conn.execute(
                    select(media_library_assets.c.content_sha256).where(
                        media_library_assets.c.asset_id == "asset-hash"
                    )
                ).scalar_one()
            )
        with self.engine.begin() as conn:
            conn.execute(
                media_library_assets.update()
                .where(
                    media_library_assets.c.asset_id == "asset-hash"
                )
                .values(content_sha256="")
            )

        written = run_backfill(self.engine, write=True)
        repeated = run_backfill(self.engine, write=True)

        self.assertEqual(written["updated_count"], 1)
        self.assertEqual(repeated["candidate_count"], 0)
        with self.engine.connect() as conn:
            row = conn.execute(
                select(
                    media_library_assets.c.content_sha256,
                    media_library_assets.c.content_hashed_at,
                ).where(media_library_assets.c.asset_id == "asset-hash")
            ).one()
            events = conn.execute(
                select(event_logs).where(
                    event_logs.c.message
                    == "media_library.source_hash.completed"
                )
            ).mappings().all()
        self.assertEqual(row.content_sha256, expected)
        self.assertIsNotNone(row.content_hashed_at)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["category"], "media_library_source_identity")
        self.assertNotIn(str(self.workspace), str(events[0]["payload"]))

    def test_backfill_rejects_source_outside_workspace(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "asset-hash")
                .values(source_video_path="../outside.mp4")
            )

        report = run_backfill(self.engine, write=True)

        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["items"][0]["error"], "source_path_outside_workspace")


if __name__ == "__main__":
    unittest.main()
