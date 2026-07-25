from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_assets,
    media_library_clip_derivatives,
    media_library_search_actions,
    media_library_search_runs,
    media_library_storyboard_imports,
    metadata,
    openclip_tasks,
    session_files,
    sessions,
)
from opcrew_backend.media_library_imports.repository import (  # noqa: E402
    MediaLibraryImportRepository,
)
from opcrew_backend.media_library_imports.router import (  # noqa: E402
    build_media_library_import_router,
)
from opcrew_backend.media_library_imports.schemas import (  # noqa: E402
    StoryBoardImportRequest,
    StoryBoardSearchImportRequest,
)
from opcrew_backend.media_library_imports.service import (  # noqa: E402
    MediaLibraryStoryBoardImportService,
)
from opcrew_backend.repositories.sessions import SessionRepository  # noqa: E402
from opcrew_backend.services.session_events import (  # noqa: E402
    SessionEventService,
    parse_payload,
)


SOURCE_BYTES = b"authoritative-media-library-video"
SOURCE_VERSION = hashlib.sha256(SOURCE_BYTES).hexdigest()
CLIP_BYTES = b"authoritative-derived-media-library-clip"
CLIP_HASH = hashlib.sha256(CLIP_BYTES).hexdigest()
CLIP_ID = "mlc_storyboard_contract"
CLIP_REL = f"SessionOutput/clips/{CLIP_ID}/产品核心卖点.mp4"
MANIFEST_REL = "SessionOutput/storyboard/koubo_storyboard_assets.json"
VIDEOS_REL = "SessionOutput/storyboard/assets/videos"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class MediaLibraryStoryBoardImportContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        self.session_repo = SessionRepository(self.engine)
        self.source_workspace = self.root / "source" / "workspace"
        self.target_workspace = self.root / "target" / "workspace"
        self.source_workspace.mkdir(parents=True)
        self.target_workspace.mkdir(parents=True)
        (self.source_workspace / "inbox").mkdir()
        (self.source_workspace / "inbox" / "source.mp4").write_bytes(SOURCE_BYTES)
        clip_path = self.source_workspace / CLIP_REL
        clip_path.parent.mkdir(parents=True)
        clip_path.write_bytes(CLIP_BYTES)
        self.original_manifest = {
            "schema_version": "koubo_storyboard_assets_v1",
            "assets": [
                {
                    "id": "existing",
                    "path": f"{VIDEOS_REL}/existing.mp4",
                    "kind": "video",
                }
            ],
            "custom": {"preserve": True},
            "updated_at": 1,
        }
        write_json(self.target_workspace / MANIFEST_REL, self.original_manifest)
        self.storyboard_source = {
            "shots": [
                {
                    "shot_id": "shot_001",
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "dialogue_items": [
                                {
                                    "dialogue_asset_key": "dialogue_0005",
                                    "text": "不要被导入修改",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        write_json(
            self.target_workspace / "SessionOutput/storyboard/srt_storyboard.json",
            self.storyboard_source,
        )
        self.storyboard_before = (self.target_workspace / "SessionOutput/storyboard/srt_storyboard.json").read_bytes()

        with self.engine.begin() as conn:
            self.source_session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="open-cut-v1",
                        group_id="open-cut-v1",
                        title="source",
                        status="draft",
                        workspace_dir=str(self.source_workspace),
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            self.target_session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="analysis-v1",
                        group_id="storyboard",
                        title="target",
                        status="draft",
                        workspace_dir=str(self.target_workspace),
                        created_at=2,
                        updated_at=2,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id="mla_source",
                    session_id=self.source_session_id,
                    display_name="权威源素材",
                    original_filename="source.mp4",
                    source_video_path="inbox/source.mp4",
                    content_sha256=SOURCE_VERSION,
                    content_hashed_at=1,
                    media_type="video",
                    size_bytes=len(SOURCE_BYTES),
                    upload_status="ready",
                    analysis_status="ready",
                    subtitle_mode="asr",
                    tags_json=[],
                    archived=False,
                    referenced_by_count=0,
                    created_at=1,
                    updated_at=1,
                )
            )
            conn.execute(
                media_library_clip_derivatives.insert().values(
                    clip_id=CLIP_ID,
                    idempotency_key="clip-create-key-0000001",
                    source_asset_id="mla_source",
                    source_session_id=self.source_session_id,
                    source_version=SOURCE_VERSION,
                    source_start_ms=12_300,
                    source_end_ms=18_800,
                    source_scheme="composite",
                    source_fragment_id="composite_0001",
                    source_analysis_run_id="mlar_composite_contract",
                    source_search_id="mls_import",
                    source_dialogue_asset_key="dialogue_0005",
                    output_path=CLIP_REL,
                    display_name="产品核心卖点",
                    duration_ms=6_500,
                    content_sha256=CLIP_HASH,
                    size_bytes=len(CLIP_BYTES),
                    operation="precise_reencode_v1",
                    search_eligible=False,
                    created_at=1,
                )
            )
            conn.execute(
                session_files.insert().values(
                    session_id=self.source_session_id,
                    path=CLIP_REL,
                    kind="video",
                    size=len(CLIP_BYTES),
                    origin="media_library_clip",
                    downloadable=1,
                    visibility="public",
                    sensitivity="normal",
                    stale=0,
                    updated_at=1,
                )
            )
            self.target_task_id = int(
                conn.execute(
                    openclip_tasks.insert()
                    .values(
                        session_id=self.target_session_id,
                        status="draft",
                        workflow_mode="script",
                        created_at=2,
                        updated_at=2,
                    )
                    .returning(openclip_tasks.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_search_runs.insert().values(
                    search_id="mls_import",
                    entry_point="storyboard",
                    target_task_id=self.target_task_id,
                    dialogue_asset_key="dialogue_0005",
                    source_asset_id=None,
                    query_source="dialogue",
                    query_hash="q" * 64,
                    query_plan_json={"term_hashes": ["x"]},
                    planner_version="deterministic-v1",
                    retrieval_version="substring-v1",
                    planner_degraded=False,
                    requested_sources_json=["media_library"],
                    source_runs_json={},
                    status="completed",
                    result_count=1,
                    zero_result=False,
                    planner_latency_ms=1,
                    retrieval_latency_ms=2,
                    total_latency_ms=3,
                    top_candidates_json=[
                        {
                            "source": "media_library",
                            "candidate_id": "media_library:mla_source",
                            "source_asset_id": "mla_source",
                            "rank": 4,
                            "score": 0.9,
                        }
                    ],
                    created_at=3,
                    updated_at=3,
                )
            )
        self.ctx = SimpleNamespace(
            engine=self.engine,
            session_event_service=SessionEventService(self.session_repo, lambda: 123456789),
        )
        self.service = MediaLibraryStoryBoardImportService(self.ctx)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def request(
        self,
        *,
        key: str = "storyboard-import-key-0001",
        name: str = "产品 防水 原片",
        search_id: str | None = "mls_import",
    ) -> StoryBoardImportRequest:
        return StoryBoardImportRequest(
            target_task_id=self.target_task_id,
            requested_name=name,
            search_id=search_id,
            dialogue_asset_key="dialogue_0005",
            idempotency_key=key,
        )

    def import_rows(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(row._mapping) for row in conn.execute(select(media_library_storyboard_imports)).fetchall()]

    def clip_request(
        self,
        *,
        key: str = "storyboard-clip-import-key-0001",
        name: str = "剪切核心卖点",
        search_id: str | None = "mls_import",
    ) -> StoryBoardImportRequest:
        return StoryBoardImportRequest(
            target_task_id=self.target_task_id,
            requested_name=name,
            search_id=search_id,
            dialogue_asset_key="dialogue_0005",
            idempotency_key=key,
        )

    def test_authoritative_cross_session_copy_manifest_provenance_and_db_atomicity(
        self,
    ) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("network must not be used"),
        ):
            result = self.service.import_original("mla_source", self.request())

        self.assertTrue(result["ok"])
        self.assertFalse(result["reused"])
        self.assertEqual(result["source_session_id"], self.source_session_id)
        self.assertEqual(result["target_session_id"], self.target_session_id)
        self.assertTrue(result["search_action_recorded"])
        target_rel = result["item"]["path"]
        self.assertTrue(target_rel.startswith(f"{VIDEOS_REL}/"))
        target_path = self.target_workspace / target_rel
        self.assertEqual(target_path.read_bytes(), SOURCE_BYTES)
        self.assertEqual(
            hashlib.sha256(target_path.read_bytes()).hexdigest(),
            SOURCE_VERSION,
        )

        manifest = json.loads((self.target_workspace / MANIFEST_REL).read_text(encoding="utf-8"))
        self.assertEqual(manifest["custom"], {"preserve": True})
        self.assertIn(self.original_manifest["assets"][0], manifest["assets"])
        imported = next(item for item in manifest["assets"] if item.get("id") == result["item"]["id"])
        self.assertEqual(imported["source"], "media_library_original")
        self.assertEqual(
            imported["provenance"],
            {
                "source": "media_library_original",
                "source_asset_id": "mla_source",
                "source_session_id": self.source_session_id,
                "source_version": SOURCE_VERSION,
                "source_search_id": "mls_import",
                "source_dialogue_asset_key": "dialogue_0005",
                "content_sha256": SOURCE_VERSION,
                "imported_at": imported["created_at"],
            },
        )
        self.assertEqual(
            (self.target_workspace / "SessionOutput/storyboard/srt_storyboard.json").read_bytes(),
            self.storyboard_before,
        )

        rows = self.import_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["source_version"], SOURCE_VERSION)
        self.assertEqual(rows[0]["content_sha256"], SOURCE_VERSION)
        self.assertEqual(rows[0]["target_task_id"], self.target_task_id)
        self.assertEqual(rows[0]["target_session_id"], self.target_session_id)
        self.assertNotIn(str(self.source_workspace), json.dumps(manifest))
        with self.engine.connect() as conn:
            session_file = (
                conn.execute(
                    select(session_files).where(
                        session_files.c.session_id == self.target_session_id,
                        session_files.c.path == target_rel,
                    )
                )
                .mappings()
                .one()
            )
            action = conn.execute(select(media_library_search_actions)).mappings().one()
            asset = conn.execute(select(media_library_assets).where(media_library_assets.c.asset_id == "mla_source")).mappings().one()
        self.assertEqual(session_file["origin"], "media_library_import")
        self.assertEqual(session_file["size"], len(SOURCE_BYTES))
        self.assertEqual(action["action_kind"], "import")
        self.assertEqual(action["source"], "media_library")
        self.assertEqual(action["candidate_rank"], 4)
        self.assertEqual(action["target_task_id"], self.target_task_id)
        self.assertEqual(asset["referenced_by_count"], 1)

        source_events = self.session_repo.list_events(self.source_session_id)
        target_events = self.session_repo.list_events(self.target_session_id)
        for events in (source_events, target_events):
            completed = next(row for row in events if row["kind"] == "media_library.storyboard_import.completed")
            payload = parse_payload(completed["payload"])
            self.assertEqual(payload["source_asset_id"], "mla_source")
            self.assertNotIn("workspace", payload)

    def test_same_idempotency_input_reuses_and_different_input_conflicts(
        self,
    ) -> None:
        first = self.service.import_original("mla_source", self.request())
        second = self.service.import_original("mla_source", self.request())

        self.assertEqual(second["import_id"], first["import_id"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(self.import_rows()), 1)
        manifest = json.loads((self.target_workspace / MANIFEST_REL).read_text(encoding="utf-8"))
        self.assertEqual(
            sum(1 for item in manifest["assets"] if item.get("id") == first["item"]["id"]),
            1,
        )
        with self.engine.connect() as conn:
            self.assertEqual(
                conn.execute(select(func.count()).select_from(media_library_search_actions)).scalar_one(),
                1,
            )
            referenced = conn.execute(select(media_library_assets.c.referenced_by_count).where(media_library_assets.c.asset_id == "mla_source")).scalar_one()
        self.assertEqual(referenced, 1)

        with self.assertRaises(HTTPException) as conflict:
            self.service.import_original("mla_source", self.request(name="不同名称"))
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(conflict.exception.detail["code"], "idempotency_key_conflict")

    def test_source_and_target_path_boundaries_are_enforced(self) -> None:
        outside = self.root / "outside.mp4"
        outside.write_bytes(SOURCE_BYTES)
        with self.engine.begin() as conn:
            conn.execute(update(media_library_assets).where(media_library_assets.c.asset_id == "mla_source").values(source_video_path="../outside.mp4"))
        with self.assertRaises(HTTPException) as escaped:
            self.service.import_original(
                "mla_source",
                self.request(key="storyboard-import-key-path1"),
            )
        self.assertEqual(escaped.exception.detail["code"], "media_source_missing")
        self.assertFalse(self.import_rows())

        with self.engine.begin() as conn:
            conn.execute(update(media_library_assets).where(media_library_assets.c.asset_id == "mla_source").values(source_video_path="inbox/source.mp4"))
        with self.assertRaises(HTTPException) as requested_name:
            self.service.import_original(
                "mla_source",
                self.request(key="storyboard-import-key-path2", name="../escape.mp4"),
            )
        self.assertEqual(
            requested_name.exception.detail["code"],
            "storyboard_import_name_invalid",
        )

        videos = self.target_workspace / VIDEOS_REL
        videos.parent.mkdir(parents=True, exist_ok=True)
        outside_dir = self.root / "outside-videos"
        outside_dir.mkdir()
        videos.symlink_to(outside_dir, target_is_directory=True)
        with self.assertRaises(HTTPException) as target_escape:
            self.service.import_original(
                "mla_source",
                self.request(key="storyboard-import-key-path3"),
            )
        self.assertEqual(
            target_escape.exception.detail["code"],
            "storyboard_target_invalid",
        )
        self.assertEqual(list(outside_dir.iterdir()), [])

    def test_hash_mismatch_and_db_failure_restore_exact_old_manifest(self) -> None:
        manifest_path = self.target_workspace / MANIFEST_REL
        original_bytes = manifest_path.read_bytes()

        def mismatched_copy(_source: Path, part: Path) -> tuple[str, int]:
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(SOURCE_BYTES)
            return "0" * 64, len(SOURCE_BYTES)

        with patch(
            "opcrew_backend.media_library_imports.service._copy_and_hash",
            side_effect=mismatched_copy,
        ):
            with self.assertRaises(HTTPException) as mismatch:
                self.service.import_original(
                    "mla_source",
                    self.request(key="storyboard-import-key-fail1"),
                )
        self.assertEqual(
            mismatch.exception.detail["code"],
            "media_source_version_mismatch",
        )
        self.assertEqual(manifest_path.read_bytes(), original_bytes)
        self.assertFalse(list(self.target_workspace.rglob("*.part")))
        self.assertEqual(self.import_rows()[0]["status"], "failed")

        with patch.object(
            self.service.repo,
            "finalize_completed",
            side_effect=RuntimeError("database unavailable"),
        ):
            with self.assertRaises(HTTPException) as db_failure:
                self.service.import_original(
                    "mla_source",
                    self.request(key="storyboard-import-key-fail2"),
                )
        self.assertEqual(db_failure.exception.detail["code"], "storyboard_import_failed")
        self.assertEqual(manifest_path.read_bytes(), original_bytes)
        self.assertFalse(list(self.target_workspace.rglob("*.part")))
        self.assertFalse(list(self.target_workspace.rglob("*.bak")))
        self.assertFalse(list(self.target_workspace.rglob("*.tmp")))
        completed_files = list((self.target_workspace / VIDEOS_REL).glob("*")) if (self.target_workspace / VIDEOS_REL).exists() else []
        self.assertEqual(completed_files, [])
        self.assertEqual(
            [row["status"] for row in self.import_rows()],
            ["failed", "failed"],
        )

    def test_search_telemetry_failure_does_not_block_primary_import(self) -> None:
        with patch.object(
            self.service.repo,
            "_insert_search_action",
            side_effect=RuntimeError("telemetry write failed"),
        ):
            result = self.service.import_original(
                "mla_source",
                self.request(key="storyboard-import-key-telemetry"),
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["search_action_recorded"])
        self.assertEqual(self.import_rows()[0]["status"], "completed")
        self.assertTrue((self.target_workspace / result["item"]["path"]).is_file())
        with self.engine.connect() as conn:
            self.assertEqual(
                conn.execute(select(func.count()).select_from(media_library_search_actions)).scalar_one(),
                0,
            )

    def test_preparing_reconciliation_completes_valid_publish_and_cleans_interrupted(
        self,
    ) -> None:
        repo = MediaLibraryImportRepository(self.engine)
        import_id = "mli_100_validreplay"
        target_rel = f"{VIDEOS_REL}/recovered.mp4"
        record = {
            "import_id": import_id,
            "idempotency_key": "storyboard-import-key-reconcile1",
            "source_kind": "media_library_original",
            "source_asset_id": "mla_source",
            "source_clip_id": None,
            "source_version": SOURCE_VERSION,
            "source_search_id": "mls_import",
            "source_dialogue_asset_key": "dialogue_0005",
            "target_task_id": self.target_task_id,
            "target_session_id": self.target_session_id,
            "target_path": target_rel,
            "target_manifest_asset_id": f"storyboard_asset_{import_id}",
            "content_sha256": SOURCE_VERSION,
            "size_bytes": 0,
            "requested_name": "恢复素材",
            "status": "preparing",
            "error_code": None,
            "created_at": 100,
            "updated_at": 100,
        }
        repo.claim_import(record)
        target_path = self.target_workspace / target_rel
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(SOURCE_BYTES)
        source = repo.get_source_asset("mla_source")
        manifest = json.loads((self.target_workspace / MANIFEST_REL).read_text(encoding="utf-8"))
        manifest["assets"].append(
            self.service._manifest_asset(
                row=record,
                source=source,
                target_path=target_rel,
                filename=target_path.name,
                imported_at=100,
                size_bytes=len(SOURCE_BYTES),
            )
        )
        write_json(self.target_workspace / MANIFEST_REL, manifest)

        recovered = self.service.reconcile_preparing()

        self.assertEqual(recovered, {"completed": 1, "failed": 0})
        self.assertEqual(repo.get_import(import_id)["status"], "completed")
        self.assertTrue(target_path.is_file())
        with self.engine.connect() as conn:
            self.assertIsNotNone(
                conn.execute(
                    select(session_files).where(
                        session_files.c.session_id == self.target_session_id,
                        session_files.c.path == target_rel,
                    )
                ).first()
            )

        interrupted_id = "mli_200_interrupted"
        interrupted_rel = f"{VIDEOS_REL}/interrupted.mp4"
        interrupted = {
            **record,
            "import_id": interrupted_id,
            "idempotency_key": "storyboard-import-key-reconcile2",
            "target_path": interrupted_rel,
            "target_manifest_asset_id": f"storyboard_asset_{interrupted_id}",
            "source_search_id": None,
            "created_at": 200,
            "updated_at": 200,
        }
        repo.claim_import(interrupted)
        interrupted_path = self.target_workspace / interrupted_rel
        interrupted_path.write_bytes(b"corrupt")
        backup_path = (self.target_workspace / MANIFEST_REL).with_name(f".{Path(MANIFEST_REL).name}.{interrupted_id}.bak")
        backup_bytes = (self.target_workspace / MANIFEST_REL).read_bytes()
        backup_path.write_bytes(backup_bytes)

        cleaned = self.service.reconcile_preparing()

        self.assertEqual(cleaned, {"completed": 0, "failed": 1})
        self.assertEqual(
            repo.get_import(interrupted_id)["error_code"],
            "import_interrupted",
        )
        self.assertFalse(interrupted_path.exists())
        self.assertFalse(backup_path.exists())
        self.assertEqual((self.target_workspace / MANIFEST_REL).read_bytes(), backup_bytes)

    def test_search_snapshot_candidate_and_source_version_are_revalidated(
        self,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_search_runs)
                .where(media_library_search_runs.c.search_id == "mls_import")
                .values(
                    top_candidates_json=[
                        {
                            "candidate_id": "other",
                            "source_asset_id": "mla_other",
                            "rank": 1,
                        }
                    ]
                )
            )
        with self.assertRaises(HTTPException) as missing:
            self.service.import_original(
                "mla_source",
                self.request(key="storyboard-import-key-search1"),
            )
        self.assertEqual(missing.exception.detail["code"], "search_candidate_not_found")
        self.assertFalse(self.import_rows())

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_search_runs)
                .where(media_library_search_runs.c.search_id == "mls_import")
                .values(
                    top_candidates_json=[
                        {
                            "candidate_id": "media_library:mla_source",
                            "source_asset_id": "mla_source",
                            "source_version": "0" * 64,
                            "rank": 1,
                        }
                    ]
                )
            )
        with self.assertRaises(HTTPException) as stale:
            self.service.import_original(
                "mla_source",
                self.request(key="storyboard-import-key-search2"),
            )
        self.assertEqual(
            stale.exception.detail["code"],
            "media_source_version_mismatch",
        )
        self.assertFalse(self.import_rows())

    def test_source_eligibility_is_rechecked_before_publish(self) -> None:
        from opcrew_backend.media_library_imports import service as import_service

        original_copy = import_service._copy_and_hash

        def archive_after_copy(source: Path, part: Path) -> tuple[str, int]:
            self.assertTrue(self.service.has_active_import("mla_source"))
            result = original_copy(source, part)
            with self.engine.begin() as conn:
                conn.execute(update(media_library_assets).where(media_library_assets.c.asset_id == "mla_source").values(archived=True))
            return result

        with patch(
            "opcrew_backend.media_library_imports.service._copy_and_hash",
            side_effect=archive_after_copy,
        ):
            with self.assertRaises(HTTPException) as archived:
                self.service.import_original(
                    "mla_source",
                    self.request(key="storyboard-import-key-concurrent-archive"),
                )
        self.assertEqual(archived.exception.detail["code"], "media_source_not_eligible")
        self.assertFalse(self.service.has_active_import("mla_source"))
        self.assertEqual(self.import_rows()[0]["status"], "failed")
        self.assertEqual(
            json.loads((self.target_workspace / MANIFEST_REL).read_text(encoding="utf-8")),
            self.original_manifest,
        )
        self.assertFalse(list((self.target_workspace / VIDEOS_REL).glob("*")) if (self.target_workspace / VIDEOS_REL).exists() else [])

    def test_clip_import_copies_authoritative_file_and_preserves_provenance(
        self,
    ) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("network must not be used"),
        ):
            result = self.service.import_clip("mla_source", CLIP_ID, self.clip_request())

        self.assertTrue(result["ok"])
        self.assertFalse(result["reused"])
        self.assertEqual(result["source_kind"], "media_library_clip")
        self.assertEqual(result["source_clip_id"], CLIP_ID)
        self.assertEqual(
            (self.target_workspace / result["item"]["path"]).read_bytes(),
            CLIP_BYTES,
        )
        manifest = json.loads((self.target_workspace / MANIFEST_REL).read_text(encoding="utf-8"))
        imported = next(item for item in manifest["assets"] if item.get("id") == result["item"]["id"])
        self.assertEqual(imported["source"], "media_library_clip")
        self.assertEqual(imported["duration_ms"], 6_500)
        provenance = imported["provenance"]
        self.assertEqual(provenance["source"], "media_library_clip")
        self.assertEqual(provenance["source_asset_id"], "mla_source")
        self.assertEqual(provenance["source_clip_id"], CLIP_ID)
        self.assertEqual(provenance["source_session_id"], self.source_session_id)
        self.assertEqual(provenance["source_version"], SOURCE_VERSION)
        self.assertEqual(provenance["source_start_ms"], 12_300)
        self.assertEqual(provenance["source_end_ms"], 18_800)
        self.assertEqual(provenance["source_scheme"], "composite")
        self.assertEqual(provenance["source_fragment_id"], "composite_0001")
        self.assertEqual(provenance["content_sha256"], CLIP_HASH)
        self.assertNotIn(str(self.source_workspace), json.dumps(imported))

        row = self.import_rows()[0]
        self.assertEqual(row["source_kind"], "media_library_clip")
        self.assertEqual(row["source_clip_id"], CLIP_ID)
        self.assertEqual(row["source_version"], SOURCE_VERSION)
        self.assertEqual(row["content_sha256"], CLIP_HASH)
        with self.engine.connect() as conn:
            action = conn.execute(select(media_library_search_actions)).mappings().one()
            target_file = (
                conn.execute(
                    select(session_files).where(
                        session_files.c.session_id == self.target_session_id,
                        session_files.c.path == result["item"]["path"],
                    )
                )
                .mappings()
                .one()
            )
        self.assertEqual(action["metadata_json"]["source_kind"], "media_library_clip")
        self.assertEqual(action["metadata_json"]["source_clip_id"], CLIP_ID)
        self.assertEqual(target_file["origin"], "media_library_import")
        self.assertEqual(self.service.repo.count_clip_references(CLIP_ID), 1)
        self.assertTrue(self.service.repo.has_clip_reference(CLIP_ID))
        events = self.session_repo.list_events(self.source_session_id)
        completed = next(row for row in events if row["kind"] == "media_library.storyboard_import.completed")
        event_payload = parse_payload(completed["payload"])
        self.assertEqual(event_payload["source_kind"], "media_library_clip")
        self.assertEqual(event_payload["source_clip_id"], CLIP_ID)

    def test_clip_idempotency_reuses_exact_request_and_rejects_conflict(
        self,
    ) -> None:
        first = self.service.import_clip("mla_source", CLIP_ID, self.clip_request())
        second = self.service.import_clip("mla_source", CLIP_ID, self.clip_request())
        self.assertEqual(second["import_id"], first["import_id"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(self.import_rows()), 1)

        with self.assertRaises(HTTPException) as conflict:
            self.service.import_clip(
                "mla_source",
                CLIP_ID,
                self.clip_request(name="另一个导入名称"),
            )
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(conflict.exception.detail["code"], "idempotency_key_conflict")

    def test_clip_authority_rejects_asset_version_path_registration_and_hash(
        self,
    ) -> None:
        with self.assertRaises(HTTPException) as wrong_asset:
            self.service.import_clip(
                "mla_other",
                CLIP_ID,
                self.clip_request(key="storyboard-clip-authority-0001"),
            )
        self.assertEqual(
            wrong_asset.exception.detail["code"],
            "media_clip_identity_mismatch",
        )

        with self.engine.begin() as conn:
            conn.execute(update(media_library_clip_derivatives).where(media_library_clip_derivatives.c.clip_id == CLIP_ID).values(source_version="0" * 64))
        with self.assertRaises(HTTPException) as stale:
            self.service.import_clip(
                "mla_source",
                CLIP_ID,
                self.clip_request(key="storyboard-clip-authority-0002"),
            )
        self.assertEqual(
            stale.exception.detail["code"],
            "media_clip_source_version_mismatch",
        )

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_clip_derivatives)
                .where(media_library_clip_derivatives.c.clip_id == CLIP_ID)
                .values(
                    source_version=SOURCE_VERSION,
                    output_path="SessionOutput/clips/another-clip/escaped.mp4",
                )
            )
        with self.assertRaises(HTTPException) as path:
            self.service.import_clip(
                "mla_source",
                CLIP_ID,
                self.clip_request(key="storyboard-clip-authority-0003"),
            )
        self.assertEqual(path.exception.detail["code"], "media_clip_path_invalid")

        with self.engine.begin() as conn:
            conn.execute(update(media_library_clip_derivatives).where(media_library_clip_derivatives.c.clip_id == CLIP_ID).values(output_path=CLIP_REL))
            conn.execute(
                update(session_files)
                .where(
                    session_files.c.session_id == self.source_session_id,
                    session_files.c.path == CLIP_REL,
                )
                .values(stale=1)
            )
        with self.assertRaises(HTTPException) as unregistered:
            self.service.import_clip(
                "mla_source",
                CLIP_ID,
                self.clip_request(key="storyboard-clip-authority-0004"),
            )
        self.assertEqual(
            unregistered.exception.detail["code"],
            "media_clip_file_unregistered",
        )

        with self.engine.begin() as conn:
            conn.execute(
                update(session_files)
                .where(
                    session_files.c.session_id == self.source_session_id,
                    session_files.c.path == CLIP_REL,
                )
                .values(stale=0)
            )
            conn.execute(update(media_library_clip_derivatives).where(media_library_clip_derivatives.c.clip_id == CLIP_ID).values(content_sha256="0" * 64))
        with self.assertRaises(HTTPException) as hash_mismatch:
            self.service.import_clip(
                "mla_source",
                CLIP_ID,
                self.clip_request(key="storyboard-clip-authority-0005"),
            )
        self.assertEqual(
            hash_mismatch.exception.detail["code"],
            "media_clip_hash_mismatch",
        )
        self.assertEqual(len(self.import_rows()), 1)
        self.assertEqual(self.import_rows()[0]["status"], "failed")
        self.assertFalse(list(self.target_workspace.rglob("*.part")))

    def test_clip_db_failure_restores_manifest_and_removes_visible_files(
        self,
    ) -> None:
        manifest_path = self.target_workspace / MANIFEST_REL
        original_bytes = manifest_path.read_bytes()
        with patch.object(
            self.service.repo,
            "finalize_completed",
            side_effect=RuntimeError("database unavailable"),
        ):
            with self.assertRaises(HTTPException) as failure:
                self.service.import_clip(
                    "mla_source",
                    CLIP_ID,
                    self.clip_request(key="storyboard-clip-rollback-0001"),
                )
        self.assertEqual(failure.exception.detail["code"], "storyboard_import_failed")
        self.assertEqual(manifest_path.read_bytes(), original_bytes)
        self.assertEqual(self.import_rows()[0]["status"], "failed")
        self.assertFalse(list(self.target_workspace.rglob("*.part")))
        self.assertFalse(list(self.target_workspace.rglob("*.bak")))
        self.assertFalse(list(self.target_workspace.rglob("*.tmp")))
        imported_files = list((self.target_workspace / VIDEOS_REL).glob("*")) if (self.target_workspace / VIDEOS_REL).exists() else []
        self.assertEqual(imported_files, [])

    def test_clip_preparing_reconciliation_completes_or_cleans_atomically(
        self,
    ) -> None:
        repo = MediaLibraryImportRepository(self.engine)
        source = self.service._clip_source("mla_source", CLIP_ID)[0]
        valid_id = "mli_300_clipvalid"
        valid_rel = f"{VIDEOS_REL}/clip-recovered.mp4"
        valid = {
            "import_id": valid_id,
            "idempotency_key": "storyboard-clip-reconcile-0001",
            "source_kind": "media_library_clip",
            "source_asset_id": "mla_source",
            "source_clip_id": CLIP_ID,
            "source_version": SOURCE_VERSION,
            "source_search_id": None,
            "source_dialogue_asset_key": "dialogue_0005",
            "target_task_id": self.target_task_id,
            "target_session_id": self.target_session_id,
            "target_path": valid_rel,
            "target_manifest_asset_id": f"storyboard_asset_{valid_id}",
            "content_sha256": CLIP_HASH,
            "size_bytes": 0,
            "requested_name": "恢复 clip",
            "status": "preparing",
            "error_code": None,
            "created_at": 300,
            "updated_at": 300,
        }
        repo.claim_import(valid)
        valid_path = self.target_workspace / valid_rel
        valid_path.parent.mkdir(parents=True, exist_ok=True)
        valid_path.write_bytes(CLIP_BYTES)
        manifest = json.loads((self.target_workspace / MANIFEST_REL).read_text(encoding="utf-8"))
        manifest["assets"].append(
            self.service._manifest_asset(
                row=valid,
                source=source,
                target_path=valid_rel,
                filename=valid_path.name,
                imported_at=300,
                size_bytes=len(CLIP_BYTES),
            )
        )
        write_json(self.target_workspace / MANIFEST_REL, manifest)

        recovered = self.service.reconcile_preparing()
        self.assertEqual(recovered, {"completed": 1, "failed": 0})
        self.assertEqual(repo.get_import(valid_id)["status"], "completed")
        self.assertTrue(valid_path.is_file())

        interrupted_id = "mli_400_clipinterrupted"
        interrupted_rel = f"{VIDEOS_REL}/clip-interrupted.mp4"
        interrupted = {
            **valid,
            "import_id": interrupted_id,
            "idempotency_key": "storyboard-clip-reconcile-0002",
            "target_path": interrupted_rel,
            "target_manifest_asset_id": (f"storyboard_asset_{interrupted_id}"),
            "created_at": 400,
            "updated_at": 400,
        }
        repo.claim_import(interrupted)
        interrupted_path = self.target_workspace / interrupted_rel
        interrupted_path.write_bytes(b"corrupt")
        manifest_path = self.target_workspace / MANIFEST_REL
        backup_path = manifest_path.with_name(f".{manifest_path.name}.{interrupted_id}.bak")
        backup_bytes = manifest_path.read_bytes()
        backup_path.write_bytes(backup_bytes)

        cleaned = self.service.reconcile_preparing()
        self.assertEqual(cleaned, {"completed": 0, "failed": 1})
        self.assertEqual(
            repo.get_import(interrupted_id)["error_code"],
            "import_interrupted",
        )
        self.assertFalse(interrupted_path.exists())
        self.assertFalse(backup_path.exists())
        self.assertEqual(manifest_path.read_bytes(), backup_bytes)

    def test_target_listing_and_router_surface_do_not_expose_workspace(self) -> None:
        targets = self.service.list_targets()
        self.assertEqual(
            targets,
            {
                "items": [
                    {
                        "task_id": self.target_task_id,
                        "session_id": self.target_session_id,
                        "title": "target",
                        "workflow_mode": "script",
                        "updated_at": 2,
                    }
                ]
            },
        )
        self.assertNotIn("workspace", json.dumps(targets))

        paths = {route.path for route in build_media_library_import_router(self.ctx).routes}
        self.assertIn("/api/media-library/import-targets/storyboards", paths)
        self.assertIn("/api/media-library/{asset_id}/import-to-storyboard", paths)
        self.assertIn(
            "/api/media-library/{asset_id}/search/runs/{search_id}/import-to-storyboard",
            paths,
        )
        self.assertIn(
            "/api/koubo-storyboard/tasks/{task_id}/media-library-search/import",
            paths,
        )

    def test_common_search_import_dispatches_derived_clip_and_removal_is_immediate(
        self,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_clip_derivatives)
                .where(media_library_clip_derivatives.c.clip_id == CLIP_ID)
                .values(
                    search_eligible=True,
                    tags_json=["玻璃碗", "产品演示"],
                    search_text="产品核心卖点 玻璃碗 产品演示",
                    search_enabled_at=4,
                    search_updated_at=4,
                )
            )
            conn.execute(
                update(media_library_search_runs)
                .where(media_library_search_runs.c.search_id == "mls_import")
                .values(
                    top_candidates_json=[
                        {
                            "source": "media_library",
                            "candidate_kind": "derived_clip",
                            "candidate_id": CLIP_ID,
                            "source_asset_id": "mla_source",
                            "source_clip_id": CLIP_ID,
                            "source_version": SOURCE_VERSION,
                            "content_sha256": CLIP_HASH,
                            "rank": 1,
                            "score": 1.0,
                            "matched_fragment_ids": [],
                        }
                    ]
                )
            )
        router = build_media_library_import_router(self.ctx)
        endpoint = next(
            route.endpoint
            for route in router.routes
            if getattr(route, "name", "")
            == "import_storyboard_search_result"
        )
        payload = StoryBoardSearchImportRequest(
            source_kind="media_library_clip",
            source_id=CLIP_ID,
            target_task_id=self.target_task_id,
            requested_name="全局复用片段",
            search_id="mls_import",
            dialogue_asset_key="dialogue_0005",
            idempotency_key="storyboard-global-clip-0001",
        )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "1"},
        ):
            result = asyncio.run(endpoint(self.target_task_id, payload))
        self.assertTrue(result["ok"])
        self.assertEqual(result["source_kind"], "media_library_clip")
        self.assertEqual(result["source_clip_id"], CLIP_ID)
        imported_path = self.target_workspace / result["item"]["path"]
        self.assertEqual(imported_path.read_bytes(), CLIP_BYTES)

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_clip_derivatives)
                .where(media_library_clip_derivatives.c.clip_id == CLIP_ID)
                .values(search_eligible=False, search_updated_at=5)
            )
        removed_payload = payload.model_copy(
            update={"idempotency_key": "storyboard-global-clip-0002"}
        )
        with (
            patch.dict(
                os.environ,
                {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "1"},
            ),
            self.assertRaises(HTTPException) as removed,
        ):
            asyncio.run(endpoint(self.target_task_id, removed_payload))
        self.assertEqual(
            removed.exception.detail["code"],
            "media_clip_search_not_eligible",
        )
        self.assertTrue(imported_path.is_file())

    def test_derived_clip_search_import_revalidates_snapshot_identity_and_context(
        self,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_clip_derivatives)
                .where(media_library_clip_derivatives.c.clip_id == CLIP_ID)
                .values(search_eligible=True)
            )
            conn.execute(
                update(media_library_search_runs)
                .where(media_library_search_runs.c.search_id == "mls_import")
                .values(
                    top_candidates_json=[
                        {
                            "source": "media_library",
                            "candidate_kind": "derived_clip",
                            "candidate_id": CLIP_ID,
                            "source_asset_id": "mla_source",
                            "source_clip_id": CLIP_ID,
                            "source_version": SOURCE_VERSION,
                            "content_sha256": "0" * 64,
                            "rank": 1,
                        }
                    ]
                )
            )
        payload = StoryBoardImportRequest(
            target_task_id=self.target_task_id,
            requested_name="身份复核",
            search_id="mls_import",
            dialogue_asset_key="dialogue_0005",
            idempotency_key="storyboard-global-identity-0001",
        )
        with self.assertRaises(HTTPException) as stale:
            self.service.import_clip(
                "mla_source",
                CLIP_ID,
                payload,
                search_candidate_kind="derived_clip",
            )
        self.assertEqual(stale.exception.detail["code"], "media_clip_hash_mismatch")
        self.assertFalse(self.import_rows())

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_search_runs)
                .where(media_library_search_runs.c.search_id == "mls_import")
                .values(
                    target_task_id=None,
                    top_candidates_json=[
                        {
                            "source": "media_library",
                            "candidate_kind": "derived_clip",
                            "candidate_id": CLIP_ID,
                            "source_asset_id": "mla_source",
                            "source_clip_id": CLIP_ID,
                            "source_version": SOURCE_VERSION,
                            "content_sha256": CLIP_HASH,
                            "rank": 1,
                        }
                    ],
                )
            )
        with self.assertRaises(HTTPException) as context:
            self.service.import_clip(
                "mla_source",
                CLIP_ID,
                payload,
                search_candidate_kind="derived_clip",
            )
        self.assertEqual(
            context.exception.detail["code"],
            "search_run_context_mismatch",
        )
        self.assertFalse(self.import_rows())

    def test_implementation_has_no_provider_or_network_import_path(self) -> None:
        source = (REPO_ROOT / "backend/opcrew_backend/media_library_imports/service.py").read_text(encoding="utf-8")
        self.assertNotIn("provider_for", source)
        self.assertNotIn("download_url", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("httpx", source)
        self.assertNotIn("dialogue_items.append", source)


if __name__ == "__main__":
    unittest.main()
