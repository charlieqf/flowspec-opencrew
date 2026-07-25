from __future__ import annotations

import json
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
    media_library_analysis_runs,
    media_library_assets,
    media_library_fragment_index,
    media_library_tasks,
    metadata,
    session_files,
    sessions,
)
from scripts.rebuild_media_library_fragment_index import run_rebuild  # noqa: E402


SOURCE_VERSION = "a" * 64
TOOL_SESSION_ID = "tus_legacy_dialogue"


class MediaLibraryLegacyAdoptionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        self.tool_root = (
            self.workspace / "tool_use_sessions" / TOOL_SESSION_ID
        )
        subtitle = self.tool_root / "SessionOutput" / "subtitle"
        manifests = self.tool_root / "SessionOutput" / "manifests"
        report = self.tool_root / "SessionReport"
        subtitle.mkdir(parents=True)
        manifests.mkdir(parents=True)
        report.mkdir(parents=True)
        (subtitle / "final_srt_frame_items.json").write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "srt_id": "srt_0001",
                            "dialogue": "可以被可信采纳的对白",
                            "start": 0.25,
                            "end": 1.75,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (manifests / "result_index.json").write_text(
            json.dumps({"tool_use_session_id": TOOL_SESSION_ID}),
            encoding="utf-8",
        )
        (report / "SessionRunSummary.json").write_text(
            json.dumps({"status": "completed", "steps": []}),
            encoding="utf-8",
        )

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
                        title="legacy adoption",
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
                    asset_id="asset-legacy",
                    session_id=session_id,
                    display_name="legacy",
                    original_filename="legacy.mp4",
                    source_video_path="inbox/legacy.mp4",
                    content_sha256=SOURCE_VERSION,
                    content_hashed_at=1,
                    media_type="video",
                    duration_ms=10_000,
                    upload_status="ready",
                    analysis_status="ready",
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
                    asset_id="asset-legacy",
                    session_id=session_id,
                    title="legacy",
                    status="draft",
                    dialogue_status="ready",
                    dialogue_tool_use_session_id=TOOL_SESSION_ID,
                    visual_status="not_analyzed",
                    visual_structure_status="not_analyzed",
                    visual_semantic_status="not_analyzed",
                    composite_status="not_analyzed",
                    created_at=1,
                    updated_at=1,
                )
            )
            relative_result_index = (
                f"tool_use_sessions/{TOOL_SESSION_ID}/"
                "SessionOutput/manifests/result_index.json"
            )
            conn.execute(
                session_files.insert().values(
                    session_id=session_id,
                    path=relative_result_index,
                    kind="manifest",
                    size=(manifests / "result_index.json").stat().st_size,
                    origin="tool_session",
                    downloadable=1,
                    tool_use_session_id=TOOL_SESSION_ID,
                    stale=0,
                    updated_at=1,
                )
            )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def test_dry_run_then_write_and_repeat_are_safe_and_idempotent(self) -> None:
        published = (
            self.tool_root
            / "SessionOutput"
            / "json"
            / "dialogue_fragment_index.json"
        )

        dry_run = run_rebuild(self.engine, scheme="dialogue")

        self.assertEqual(dry_run["failed_count"], 0)
        self.assertEqual(dry_run["items"][0]["status"], "would_adopt")
        self.assertFalse(published.exists())
        with self.engine.connect() as conn:
            self.assertEqual(
                conn.execute(
                    select(media_library_analysis_runs.c.analysis_run_id)
                ).fetchall(),
                [],
            )

        written = run_rebuild(self.engine, write=True, scheme="dialogue")
        repeated = run_rebuild(self.engine, write=True, scheme="dialogue")

        self.assertEqual(written["items"][0]["status"], "adopted")
        self.assertEqual(repeated["items"][0]["status"], "already_adopted")
        self.assertTrue(published.is_file())
        with self.engine.connect() as conn:
            run = conn.execute(
                select(media_library_analysis_runs)
            ).mappings().one()
            fragment = conn.execute(
                select(media_library_fragment_index)
            ).mappings().one()
        self.assertEqual(run["status"], "ready")
        self.assertTrue(run["is_current"])
        self.assertTrue(run["upstream_refs_json"]["adopted_legacy"])
        self.assertEqual(fragment["start_ms"], 250)
        self.assertEqual(fragment["end_ms"], 1750)
        self.assertTrue(fragment["is_active"])

    def test_unregistered_result_index_is_not_adopted(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(session_files.delete())

        report = run_rebuild(self.engine, write=True, scheme="dialogue")

        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(
            report["items"][0]["error"],
            "tool_session_result_index_not_registered",
        )
        with self.engine.connect() as conn:
            self.assertEqual(
                conn.execute(
                    select(media_library_analysis_runs.c.analysis_run_id)
                ).fetchall(),
                [],
            )


if __name__ == "__main__":
    unittest.main()
