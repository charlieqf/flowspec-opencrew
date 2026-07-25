from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

import anyio  # noqa: E402
from fastapi import UploadFile  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from opcrew_backend.db.schema import media_library_assets, media_library_tasks, media_library_uploads, metadata, session_events, session_files, sessions  # noqa: E402
from opcrew_backend.media_library_upload.repository import MediaLibraryUploadRepository  # noqa: E402
from opcrew_backend.media_library_upload.schemas import MediaLibraryUploadCreate  # noqa: E402
from opcrew_backend.media_library_upload.service import MediaLibraryUploadService  # noqa: E402
from opcrew_backend.media_library_upload.storage import (  # noqa: E402
    create_proxy_preview,
    merge_chunks,
    proxy_timebase_guard_result,
    should_create_proxy_preview,
)
from opcrew_backend.repositories.media_library import MediaLibraryRepository  # noqa: E402
from opcrew_backend.repositories.media_library_tasks import MediaLibraryTaskRepository  # noqa: E402
from opcrew_backend.repositories.sessions import SessionRepository  # noqa: E402
from opcrew_backend.services.session_events import SessionEventService  # noqa: E402
from opcrew_backend.services.workflow_deletion import WorkflowDeletionService  # noqa: E402
from opcrew_backend.storage.workspace import LocalWorkspaceStore  # noqa: E402


class FakeOpenCodeClient:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    def create_session(self, title: str) -> dict[str, str]:
        session_id = f"oc_{len(self.created) + 1}"
        self.created.append(title)
        return {"id": session_id}

    def delete_session(self, session_id: str) -> bool:
        self.deleted.append(session_id)
        return True


class MediaLibraryUploadContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        metadata.create_all(self.engine)
        self.session_repo = SessionRepository(self.engine)
        self.workspace_store = LocalWorkspaceStore(self.data_dir)
        self.client = FakeOpenCodeClient()
        self.ctx = SimpleNamespace(
            engine=self.engine,
            session_repo=self.session_repo,
            workspace_store=self.workspace_store,
            workflow_deletion_service=WorkflowDeletionService(self.engine, self.workspace_store.sessions_root()),
            session_event_service=SessionEventService(self.session_repo, lambda: 123456789),
            media_library_opencode_client_factory=lambda _row: self.client,
            media_library_task_repo=MediaLibraryTaskRepository(self.engine),
            new_share_token=lambda: "share-token",
        )
        self.service = MediaLibraryUploadService(self.ctx)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def save_chunk(self, upload_id: str, index: int, total: int, data: bytes) -> dict:
        async def run() -> dict:
            upload = UploadFile(file=io.BytesIO(data), filename=f"chunk-{index}.part")
            return await self.service.save_chunk(upload_id, index, total, upload)

        return anyio.run(run)

    def test_large_file_flow_creates_one_session_and_one_ready_asset(self) -> None:
        with patch("opcrew_backend.media_library_upload.service.DEFAULT_CHUNK_SIZE", 4):
            transaction = self.service.create_upload(MediaLibraryUploadCreate(filename="老板采访.mp4", size_bytes=10, content_type="video/mp4"))

        self.assertEqual(transaction["total_chunks"], 3)
        session_id = int(transaction["session_id"])
        session = self.session_repo.get(session_id)
        self.assertEqual(session["source"], "open-cut-v1")
        self.assertEqual(session["group_id"], "open-cut-v1")
        self.assertEqual(session["opencode_session_id"], "oc_1")
        self.assertEqual(self.client.created, ["老板采访"])
        with self.engine.connect() as conn:
            task = conn.execute(select(media_library_tasks).where(media_library_tasks.c.asset_id == transaction["asset_id"])).mappings().first()
        self.assertIsNotNone(task)
        self.assertEqual(int(task["session_id"]), session_id)
        self.assertEqual(task["dialogue_status"], "not_analyzed")

        library_repo = MediaLibraryRepository(self.engine)
        _, total_before, _ = library_repo.list()
        self.assertEqual(total_before, 0)

        self.save_chunk(transaction["upload_id"], 0, 3, b"abcd")
        duplicate = self.save_chunk(transaction["upload_id"], 0, 3, b"abcd")
        self.assertEqual(duplicate["received_bytes"], 4)
        self.save_chunk(transaction["upload_id"], 1, 3, b"efgh")
        last = self.save_chunk(transaction["upload_id"], 2, 3, b"ij")
        self.assertEqual(last["received_bytes"], 10)

        completed = self.service.complete(transaction["upload_id"], 10)
        self.assertEqual(completed["upload"]["status"], "ready")
        self.assertEqual(completed["item"]["upload_status"], "ready")
        self.assertEqual(
            completed["item"]["content_sha256"],
            hashlib.sha256(b"abcdefghij").hexdigest(),
        )
        self.assertEqual(
            completed["item"]["content_sha256"],
            completed["item"]["content_sha256"].lower(),
        )
        self.assertIsNotNone(completed["item"]["content_hashed_at"])
        source_path = Path(str(session["workspace_dir"])) / "inbox" / "老板采访.mp4"
        self.assertEqual(source_path.read_bytes(), b"abcdefghij")

        rows, total_after, _ = library_repo.list()
        self.assertEqual(total_after, 1)
        self.assertEqual(rows[0]["session_id"], session_id)
        self.assertEqual(rows[0]["source_video_path"], "inbox/老板采访.mp4")
        with self.engine.connect() as conn:
            events = conn.execute(
                select(session_events)
                .where(session_events.c.session_id == session_id)
                .order_by(session_events.c.id)
            ).mappings().all()
        self.assertIn(
            "media_library.source_hash.completed",
            [event["kind"] for event in events],
        )
        hash_event = next(
            event
            for event in events
            if event["kind"]
            == "media_library.source_hash.completed"
        )
        hash_payload = json.loads(hash_event["payload"])
        self.assertEqual(
            hash_payload["source_version"],
            hashlib.sha256(b"abcdefghij").hexdigest(),
        )
        self.assertNotIn(str(self.data_dir), str(hash_payload))

    def test_high_bandwidth_source_publishes_registered_proxy_preview(self) -> None:
        transaction = self.service.create_upload(MediaLibraryUploadCreate(filename="camera.MOV", size_bytes=4, content_type="video/quicktime"))
        self.save_chunk(transaction["upload_id"], 0, 1, b"data")
        session = self.session_repo.get(int(transaction["session_id"]))
        workspace = Path(str(session["workspace_dir"]))

        def fake_preview(_workspace: Path, asset_id: str, _source: Path, _metadata: dict) -> tuple[str, Path]:
            rel = f"SessionOutput/media_library/previews/{asset_id}.mp4"
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"preview")
            return rel, target

        with (
            patch(
                "opcrew_backend.media_library_upload.service.probe_video",
                return_value={"duration_ms": 26_000, "width": 1920, "height": 1080, "bit_rate": 103_000_000, "fps": 50.0},
            ),
            patch("opcrew_backend.media_library_upload.service.create_proxy_preview", side_effect=fake_preview),
        ):
            completed = self.service.complete(transaction["upload_id"], 4)

        item = completed["item"]
        expected_rel = f"SessionOutput/media_library/previews/{transaction['asset_id']}.mp4"
        self.assertIn(f"/api/session-tasks/{transaction['session_id']}/raw/{expected_rel}", item["preview_url"])
        self.assertIn("?v=", item["preview_url"])
        self.assertIn(f"/api/session-tasks/{transaction['session_id']}/thumbnail/{expected_rel}", item["thumbnail_url"])
        with self.engine.connect() as conn:
            preview_row = conn.execute(
                select(session_files).where(
                    session_files.c.session_id == transaction["session_id"],
                    session_files.c.path == expected_rel,
                )
            ).mappings().one()
            events = conn.execute(select(session_events).where(session_events.c.session_id == transaction["session_id"])).mappings().all()
        self.assertEqual(preview_row["origin"], "generated_preview")
        self.assertEqual(int(preview_row["downloadable"]), 1)
        self.assertIn("media_library.preview.generated", [event["kind"] for event in events])

    def test_proxy_preview_policy_catches_dscf0157_profile(self) -> None:
        source = self.data_dir / "DSCF0157.mov"
        source.write_bytes(b"x" * 1024)
        self.assertTrue(
            should_create_proxy_preview(
                source,
                {
                    "duration_ms": 26_000,
                    "width": 1920,
                    "height": 1080,
                    "codec_name": "h264",
                    "pixel_format": "yuvj420p",
                    "bit_rate": 103_255_118,
                    "fps": 50.0,
                },
            )
        )
        self.assertFalse(
            should_create_proxy_preview(
                source,
                {
                    "duration_ms": 26_000,
                    "width": 1280,
                    "height": 720,
                    "codec_name": "h264",
                    "pixel_format": "yuv420p",
                    "bit_rate": 4_000_000,
                    "fps": 30.0,
                },
            )
        )

    def test_proxy_timebase_guard_contract_and_tolerance_boundary(self) -> None:
        self.assertTrue(proxy_timebase_guard_result(10_000, 10_100)["valid"])
        rejected = proxy_timebase_guard_result(10_000, 10_101)
        self.assertFalse(rejected["valid"])
        self.assertEqual(rejected["reason"], "duration_delta_exceeded")
        self.assertEqual(rejected["delta_ms"], 101)
        self.assertEqual(
            proxy_timebase_guard_result(None, 10_000)["reason"],
            "source_duration_unavailable",
        )
        self.assertEqual(
            proxy_timebase_guard_result(10_000, None)["reason"],
            "preview_duration_unavailable",
        )

    def test_proxy_timebase_guard_falls_back_without_publishing(self) -> None:
        source = self.data_dir / "guard-source.mov"
        source.write_bytes(b"source")
        workspace = self.data_dir / "guard-workspace"
        existing_preview = (
            workspace
            / "SessionOutput/media_library/previews/asset-guarded.mp4"
        )
        existing_preview.parent.mkdir(parents=True, exist_ok=True)
        existing_preview.write_bytes(b"previous-valid-proxy")

        def fake_ffmpeg(command: list[str], **_kwargs: Any) -> SimpleNamespace:
            Path(command[-1]).write_bytes(b"proxy")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with (
            patch.dict(
                os.environ,
                {"OPENCREW_MEDIA_LIBRARY_PROXY_TIMEBASE_GUARD": "1"},
                clear=False,
            ),
            patch(
                "opcrew_backend.media_library_upload.storage._ffmpeg_binary",
                return_value="/fake/ffmpeg",
            ),
            patch(
                "opcrew_backend.media_library_upload.storage.subprocess.run",
                side_effect=fake_ffmpeg,
            ),
            patch(
                "opcrew_backend.media_library_upload.storage.probe_video",
                return_value={"duration_ms": 10_101},
            ),
            self.assertLogs(
                "opcrew_backend.media_library_upload.storage",
                level="WARNING",
            ) as captured,
        ):
            preview = create_proxy_preview(
                workspace,
                "asset-guarded",
                source,
                {
                    "duration_ms": 10_000,
                    "width": 3840,
                    "height": 2160,
                },
            )

        self.assertIsNone(preview)
        self.assertEqual(
            existing_preview.read_bytes(),
            b"previous-valid-proxy",
            "rejecting a new temporary proxy must not delete an older valid proxy",
        )
        self.assertIn("delta_ms=101", "\n".join(captured.output))

    def test_enabled_proxy_timebase_guard_validates_before_publishing(self) -> None:
        source = self.data_dir / "guard-valid.mov"
        source.write_bytes(b"source")
        workspace = self.data_dir / "guard-valid-workspace"

        def fake_ffmpeg(command: list[str], **_kwargs: Any) -> SimpleNamespace:
            Path(command[-1]).write_bytes(b"proxy")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with (
            patch.dict(
                os.environ,
                {"OPENCREW_MEDIA_LIBRARY_PROXY_TIMEBASE_GUARD": "1"},
                clear=False,
            ),
            patch(
                "opcrew_backend.media_library_upload.storage._ffmpeg_binary",
                return_value="/fake/ffmpeg",
            ),
            patch(
                "opcrew_backend.media_library_upload.storage.subprocess.run",
                side_effect=fake_ffmpeg,
            ),
            patch(
                "opcrew_backend.media_library_upload.storage.probe_video",
                return_value={"duration_ms": 10_100},
            ) as probe,
        ):
            preview = create_proxy_preview(
                workspace,
                "asset-guard-valid",
                source,
                {
                    "duration_ms": 10_000,
                    "width": 3840,
                    "height": 2160,
                },
            )

        self.assertIsNotNone(preview)
        self.assertTrue(preview[1].is_file())
        probe.assert_called_once()

    def test_disabled_proxy_timebase_guard_preserves_original_publish_path(self) -> None:
        source = self.data_dir / "guard-disabled.mov"
        source.write_bytes(b"source")
        workspace = self.data_dir / "guard-disabled-workspace"

        def fake_ffmpeg(command: list[str], **_kwargs: Any) -> SimpleNamespace:
            Path(command[-1]).write_bytes(b"proxy")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with (
            patch.dict(
                os.environ,
                {"OPENCREW_MEDIA_LIBRARY_PROXY_TIMEBASE_GUARD": "0"},
                clear=False,
            ),
            patch(
                "opcrew_backend.media_library_upload.storage._ffmpeg_binary",
                return_value="/fake/ffmpeg",
            ),
            patch(
                "opcrew_backend.media_library_upload.storage.subprocess.run",
                side_effect=fake_ffmpeg,
            ),
            patch(
                "opcrew_backend.media_library_upload.storage.probe_video"
            ) as probe,
        ):
            preview = create_proxy_preview(
                workspace,
                "asset-unguarded",
                source,
                {
                    "duration_ms": 10_000,
                    "width": 3840,
                    "height": 2160,
                },
            )

        self.assertIsNotNone(preview)
        self.assertTrue(preview[1].is_file())
        probe.assert_not_called()

    def test_concurrent_complete_has_one_finalizer_and_cannot_downgrade_ready_asset(self) -> None:
        transaction = self.service.create_upload(MediaLibraryUploadCreate(filename="concurrent.mp4", size_bytes=4, content_type="video/mp4"))
        self.save_chunk(transaction["upload_id"], 0, 1, b"data")
        entered = threading.Event()
        release = threading.Event()

        from opcrew_backend.media_library_upload import storage

        original_merge = storage.merge_chunks

        def slow_merge(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return original_merge(*args, **kwargs)

        with patch("opcrew_backend.media_library_upload.service.merge_chunks", side_effect=slow_merge):
            with ThreadPoolExecutor(max_workers=1) as pool:
                primary = pool.submit(self.service.complete, transaction["upload_id"], 4)
                self.assertTrue(entered.wait(timeout=5))
                duplicate = self.service.complete(transaction["upload_id"], 4)
                claimed = self.service.repo.get_upload(transaction["upload_id"])
                self.assertEqual(duplicate["upload"]["status"], "finalizing")
                self.assertTrue(duplicate["finalizing"])
                self.assertTrue(claimed["finalization_token"])
                release.set()
                completed = primary.result(timeout=5)

        self.assertEqual(completed["upload"]["status"], "ready")
        self.assertFalse(
            self.service.repo.mark_failed(
                transaction["upload_id"],
                message="late worker failure",
                updated_at=999,
                finalization_token=str(claimed["finalization_token"]),
            )
        )
        self.assertEqual(self.service.status(transaction["upload_id"])["status"], "ready")
        self.assertEqual(self.service.repo.get_asset(transaction["asset_id"])["upload_status"], "ready")

    def test_stale_finalization_claim_can_be_recovered_after_restart(self) -> None:
        transaction = self.service.create_upload(MediaLibraryUploadCreate(filename="recover.mp4", size_bytes=4, content_type="video/mp4"))
        self.save_chunk(transaction["upload_id"], 0, 1, b"data")
        repo = MediaLibraryUploadRepository(self.engine)
        first = repo.claim_finalization(transaction["upload_id"], token="worker-a", updated_at=100, stale_before=50)
        current = repo.claim_finalization(transaction["upload_id"], token="worker-b", updated_at=200, stale_before=50)
        recovered = repo.claim_finalization(transaction["upload_id"], token="worker-c", updated_at=300, stale_before=150)

        self.assertEqual(first["finalization_token"], "worker-a")
        self.assertIsNone(current)
        self.assertEqual(recovered["finalization_token"], "worker-c")
        self.assertFalse(repo.mark_failed(transaction["upload_id"], message="old failure", updated_at=400, finalization_token="worker-a"))
        self.assertEqual(repo.get_upload(transaction["upload_id"])["status"], "finalizing")

        session = self.session_repo.get(int(transaction["session_id"]))
        workspace = Path(str(session["workspace_dir"]))
        merge_chunks(
            workspace,
            transaction["upload_id"],
            "recover.mp4",
            total_chunks=1,
            expected_size=4,
            finalization_token="worker-c",
        )
        self.assertFalse((workspace / ".media_uploads" / transaction["upload_id"]).exists())

        completed = self.service.complete(transaction["upload_id"], 4)

        self.assertEqual(completed["upload"]["status"], "ready")
        self.assertEqual((workspace / "inbox/recover.mp4").read_bytes(), b"data")

    def test_cancel_cleans_pending_asset_session_and_workspace(self) -> None:
        with patch("opcrew_backend.media_library_upload.service.DEFAULT_CHUNK_SIZE", 4):
            transaction = self.service.create_upload(MediaLibraryUploadCreate(filename="cancel.mp4", size_bytes=8, content_type="video/mp4"))
        self.save_chunk(transaction["upload_id"], 0, 2, b"abcd")
        session = self.session_repo.get(int(transaction["session_id"]))
        session_dir = Path(str(session["workspace_dir"])).parent

        result = self.service.cancel(transaction["upload_id"])

        self.assertTrue(result["ok"])
        self.assertIsNone(self.session_repo.get(int(transaction["session_id"])))
        self.assertFalse(session_dir.exists())
        with self.engine.connect() as conn:
            self.assertIsNone(conn.execute(select(media_library_assets).where(media_library_assets.c.asset_id == transaction["asset_id"])).first())
            self.assertIsNone(conn.execute(select(media_library_uploads).where(media_library_uploads.c.upload_id == transaction["upload_id"])).first())
            self.assertIsNone(conn.execute(select(sessions).where(sessions.c.id == transaction["session_id"])).first())
        self.assertEqual(self.client.deleted, ["oc_1"])

    def test_delete_ready_asset_cleans_session_workspace_and_opencode_session(self) -> None:
        transaction = self.service.create_upload(MediaLibraryUploadCreate(filename="delete-ready.mp4", size_bytes=4, content_type="video/mp4"))
        self.save_chunk(transaction["upload_id"], 0, 1, b"data")
        completed = self.service.complete(transaction["upload_id"], 4)
        session = self.session_repo.get(int(transaction["session_id"]))
        session_dir = Path(str(session["workspace_dir"])).parent

        result = self.service.delete_ready_asset(completed["item"]["asset_id"])

        self.assertTrue(result["ok"])
        self.assertIsNone(self.session_repo.get(int(transaction["session_id"])))
        self.assertFalse(session_dir.exists())
        with self.engine.connect() as conn:
            self.assertIsNone(conn.execute(select(media_library_assets).where(media_library_assets.c.asset_id == transaction["asset_id"])).first())
            self.assertIsNone(conn.execute(select(media_library_uploads).where(media_library_uploads.c.upload_id == transaction["upload_id"])).first())
            self.assertIsNone(conn.execute(select(media_library_tasks).where(media_library_tasks.c.asset_id == transaction["asset_id"])).first())
            self.assertIsNone(conn.execute(select(sessions).where(sessions.c.id == transaction["session_id"])).first())
        self.assertEqual(self.client.deleted, ["oc_1"])

    def test_upload_module_has_no_analysis_v1_or_openclip_business_dependency(self) -> None:
        module_root = REPO_ROOT / "backend" / "opcrew_backend" / "media_library_upload"
        source = "\n".join(path.read_text(encoding="utf-8") for path in module_root.glob("*.py"))
        self.assertNotIn("Analysis_V1", source)
        self.assertNotIn("/api/openclip", source)
        self.assertNotIn("koubo.router", source)

    def test_complete_route_offloads_blocking_merge_from_event_loop(self) -> None:
        source = (REPO_ROOT / "backend/opcrew_backend/media_library_upload/router.py").read_text(encoding="utf-8")

        self.assertIn("await asyncio.to_thread(service.complete", source)

    def test_chunk_progress_update_is_locked_for_parallel_browser_workers(self) -> None:
        source = (REPO_ROOT / "backend/opcrew_backend/media_library_upload/repository.py").read_text(encoding="utf-8")

        self.assertIn(".with_for_update()", source)


if __name__ == "__main__":
    unittest.main()
