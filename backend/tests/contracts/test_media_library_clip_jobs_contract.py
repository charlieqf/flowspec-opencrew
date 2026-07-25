from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, update
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_clip_derivatives,
    media_library_search_runs,
    metadata,
    sessions,
)
from opcrew_backend.media_library_analysis.contracts import (  # noqa: E402
    result_hash,
)
from opcrew_backend.media_library_clips import (  # noqa: E402
    ClipDerivativeRepository,
    ClipJobCreateRequest,
    ClipJobManager,
    ClipSearchMetadataPatchRequest,
    ClipStorage,
    MediaClipError,
    build_media_library_clip_router,
    clip_request_from_asset,
)
from opcrew_backend.media_library_clips.errors import (  # noqa: E402
    ClipCancelled,
)


ASSET_ID = "asset-clip-jobs-contract"
SOURCE_VERSION = "a" * 64


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminate_count = 0
        self.kill_count = 0
        self.terminated = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_count += 1
        self.returncode = -15
        self.terminated.set()

    def kill(self) -> None:
        self.kill_count += 1
        self.returncode = -9
        self.terminated.set()

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None and not self.terminated.wait(timeout):
            from subprocess import TimeoutExpired

            raise TimeoutExpired("fake-ffmpeg", timeout)
        return int(self.returncode or 0)


class _BlockingProcessor:
    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.process = _FakeProcess()
        self.part_path: Path | None = None

    def run(self, **kwargs: object) -> tuple[dict, bool]:
        self.calls += 1
        request = kwargs["request"]
        self.part_path = (
            request.source_workspace / "SessionOutput" / "blocking.part.mp4"
        )
        self.part_path.parent.mkdir(parents=True, exist_ok=True)
        self.part_path.write_bytes(b"partial")
        kwargs["on_part_path"](self.part_path)
        kwargs["on_process"](self.process)
        self.started.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if kwargs["cancel_requested"]():
                self.part_path.unlink(missing_ok=True)
                raise ClipCancelled()
            time.sleep(0.01)
        raise AssertionError("blocking clip processor was not cancelled")


class _NeverCalledProcessor:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, **_: object) -> tuple[dict, bool]:
        self.calls += 1
        raise AssertionError("persisted clip must not run FFmpeg")


class _FailingProcessor:
    def run(self, **_: object) -> tuple[dict, bool]:
        raise MediaClipError(
            "media_clip_contract_failure",
            "contract failure",
            status_code=500,
        )


class MediaLibraryClipJobsContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        source = self.workspace / "inbox" / "source.mp4"
        source.parent.mkdir()
        source.write_bytes(b"immutable-source")
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            self.session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="open-cut-v1",
                        group_id="open-cut-v1",
                        title="clip jobs contract",
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
                    asset_id=ASSET_ID,
                    session_id=self.session_id,
                    display_name="source",
                    original_filename="source.mp4",
                    source_video_path="inbox/source.mp4",
                    content_sha256=SOURCE_VERSION,
                    content_hashed_at=1,
                    media_type="video",
                    duration_ms=10_000,
                    upload_status="ready",
                    analysis_status="not_analyzed",
                    subtitle_mode="unknown",
                    analysis_summary_json={},
                    tags_json=[],
                    archived=False,
                    referenced_by_count=0,
                    created_at=1,
                    updated_at=1,
                )
            )
        self.asset = {
            "asset_id": ASSET_ID,
            "session_id": self.session_id,
            "source_video_path": "inbox/source.mp4",
            "content_sha256": SOURCE_VERSION,
            "duration_ms": 10_000,
            "upload_status": "ready",
            "archived": False,
        }
        self.session = {
            "id": self.session_id,
            "workspace_dir": str(self.workspace),
        }
        self.managers: list[ClipJobManager] = []

    def tearDown(self) -> None:
        for manager in self.managers:
            manager.shutdown()
        self.engine.dispose()
        self.temporary.cleanup()

    def payload(
        self,
        *,
        idempotency_key: str = "clip-job-key-000001",
        start_ms: int = 0,
        end_ms: int = 250,
        display_name: str = "片段",
    ) -> dict[str, object]:
        return {
            "source_version": SOURCE_VERSION,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "display_name": display_name,
            "manual_override": True,
            "idempotency_key": idempotency_key,
        }

    def manager(
        self,
        *,
        processor: object,
        boot_id: str = "1" * 32,
        event_sink: object | None = None,
        metric_sink: object | None = None,
    ) -> ClipJobManager:
        manager = ClipJobManager(
            self.engine,
            processor=processor,
            max_workers=1,
            boot_id=boot_id,
            event_sink=event_sink,
            metric_sink=metric_sink,
        )
        self.managers.append(manager)
        return manager

    @staticmethod
    def wait_terminal(
        manager: ClipJobManager, job_id: str, *, asset_id: str = ASSET_ID
    ) -> dict[str, object]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            view = manager.get_job(asset_id, job_id)
            if view["status"] in {"completed", "failed", "cancelled"}:
                return view
            time.sleep(0.01)
        raise AssertionError("clip job did not reach a terminal state")

    def test_request_validates_source_version_range_and_manual_provenance(
        self,
    ) -> None:
        repository = ClipDerivativeRepository(self.engine)
        invalid_payloads = (
            {
                **self.payload(),
                "source_version": "b" * 64,
            },
            self.payload(start_ms=-1),
            self.payload(start_ms=0, end_ms=249),
            self.payload(start_ms=9_900, end_ms=10_001),
            {
                **self.payload(),
                "manual_override": False,
            },
            {
                **self.payload(),
                "source_fragment_id": "stale-fragment",
                "source_analysis_run_id": "mlar_stale",
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(MediaClipError):
                    request = clip_request_from_asset(
                        asset=self.asset,
                        session=self.session,
                        payload=payload,
                        minimum_duration_ms=250,
                        maximum_duration_ms=1_800_000,
                    )
                    repository.validate_source_fragment(request)

        request = clip_request_from_asset(
            asset=self.asset,
            session=self.session,
            payload=self.payload(),
            minimum_duration_ms=250,
            maximum_duration_ms=1_800_000,
        )
        repository.validate_source_fragment(request)
        self.assertEqual(request.requested_duration_ms, 250)
        self.assertEqual(request.source_start_ms, 0)
        self.assertEqual(request.source_end_ms, 250)
        with self.assertRaises(MediaClipError) as invalid_range:
            clip_request_from_asset(
                asset=self.asset,
                session=self.session,
                payload=self.payload(end_ms=249),
                minimum_duration_ms=250,
                maximum_duration_ms=1_800_000,
            )
        self.assertEqual(invalid_range.exception.code, "clip_range_invalid")

    def test_visual_structure_and_semantic_use_authoritative_result_items(
        self,
    ) -> None:
        repository = ClipDerivativeRepository(self.engine)
        fixtures = (
            ("visual_structure", "mlar_visual_structure_clip_contract"),
            ("visual_semantic", "mlar_visual_semantic_clip_contract"),
        )
        for offset, (scheme, run_id) in enumerate(fixtures, start=1):
            relative = f"analysis/{scheme}.json"
            result_path = self.workspace / relative
            result_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": f"media_library_{scheme}_v1",
                "asset_id": ASSET_ID,
                "source_version": SOURCE_VERSION,
                "analysis_run_id": run_id,
                "items": [
                    {
                        "fragment_id": "scene_0001",
                        "start_ms": 0,
                        "end_ms": 250,
                    }
                ],
            }
            result_path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.engine.begin() as conn:
                conn.execute(
                    media_library_analysis_runs.insert().values(
                        analysis_run_id=run_id,
                        asset_id=ASSET_ID,
                        scheme=scheme,
                        source_version=SOURCE_VERSION,
                        status="ready",
                        result_index_path=relative,
                        result_hash=result_hash(payload),
                        upstream_refs_json={},
                        progress_json={},
                        is_current=True,
                        created_at=offset,
                        updated_at=offset,
                    )
                )
            request = clip_request_from_asset(
                asset=self.asset,
                session=self.session,
                payload={
                    **self.payload(
                        idempotency_key=(
                            f"clip-visual-{scheme}-contract"
                        )
                    ),
                    "manual_override": False,
                    "source_scheme": "visual",
                    "source_fragment_id": "scene_0001",
                    "source_analysis_run_id": run_id,
                },
                minimum_duration_ms=250,
                maximum_duration_ms=1_800_000,
            )
            repository.validate_source_fragment(request)

            payload["items"][0]["end_ms"] = 500
            result_path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.engine.begin() as conn:
                conn.execute(
                    update(media_library_analysis_runs)
                    .where(
                        media_library_analysis_runs.c.analysis_run_id
                        == run_id
                    )
                    .values(result_hash=result_hash(payload))
                )
            with self.assertRaises(MediaClipError) as stale_range:
                repository.validate_source_fragment(request)
            self.assertEqual(
                stale_range.exception.code, "media_clip_fragment_stale"
            )

            payload["items"][0] = {
                "fragment_id": "scene_other",
                "start_ms": 0,
                "end_ms": 250,
            }
            result_path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.engine.begin() as conn:
                conn.execute(
                    update(media_library_analysis_runs)
                    .where(
                        media_library_analysis_runs.c.analysis_run_id
                        == run_id
                    )
                    .values(result_hash=result_hash(payload))
                )
            with self.assertRaises(MediaClipError) as unknown_fragment:
                repository.validate_source_fragment(request)
            self.assertEqual(
                unknown_fragment.exception.code,
                "media_clip_fragment_stale",
            )

    def test_search_provenance_requires_completed_current_asset_snapshot(
        self,
    ) -> None:
        repository = ClipDerivativeRepository(self.engine)
        search_id = "mls_clip_provenance_contract"
        with self.engine.begin() as conn:
            conn.execute(
                media_library_search_runs.insert().values(
                    search_id=search_id,
                    entry_point="storyboard",
                    target_task_id=27,
                    dialogue_asset_key="dialogue_0005",
                    source_asset_id=None,
                    query_source="dialogue",
                    query_hash="b" * 64,
                    query_plan_json={},
                    planner_version="contract",
                    retrieval_version="contract",
                    planner_degraded=False,
                    requested_sources_json=["media_library"],
                    source_runs_json={},
                    status="completed",
                    result_count=1,
                    zero_result=False,
                    top_candidates_json=[
                        {
                            "source": "media_library",
                            "source_asset_id": ASSET_ID,
                            "source_version": SOURCE_VERSION,
                            "rank": 1,
                        }
                    ],
                    created_at=1,
                    updated_at=1,
                )
            )
        valid_payload = {
            **self.payload(
                idempotency_key="clip-search-provenance-0001"
            ),
            "source_search_id": search_id,
            "source_dialogue_asset_key": "dialogue_0005",
        }
        valid = clip_request_from_asset(
            asset=self.asset,
            session=self.session,
            payload=valid_payload,
            minimum_duration_ms=250,
            maximum_duration_ms=1_800_000,
        )
        repository.validate_source_fragment(valid)

        forged_dialogue = clip_request_from_asset(
            asset=self.asset,
            session=self.session,
            payload={
                **valid_payload,
                "idempotency_key": "clip-search-provenance-0002",
                "source_dialogue_asset_key": "dialogue_forged",
            },
            minimum_duration_ms=250,
            maximum_duration_ms=1_800_000,
        )
        with self.assertRaises(MediaClipError) as dialogue_error:
            repository.validate_source_fragment(forged_dialogue)
        self.assertEqual(
            dialogue_error.exception.code,
            "media_clip_search_provenance_invalid",
        )

        editor_search_id = "mls_clip_editor_fragments_contract"
        with self.engine.begin() as conn:
            conn.execute(
                media_library_search_runs.insert().values(
                    search_id=editor_search_id,
                    entry_point="editor",
                    target_task_id=None,
                    dialogue_asset_key=None,
                    source_asset_id="asset-open-in-editor",
                    query_source="dialogue",
                    query_hash="d" * 64,
                    query_plan_json={},
                    planner_version="contract",
                    retrieval_version="contract",
                    planner_degraded=False,
                    requested_sources_json=["media_library"],
                    source_runs_json={},
                    status="completed",
                    result_count=1,
                    zero_result=False,
                    top_candidates_json=[
                        {
                            "source": "media_library",
                            "source_asset_id": ASSET_ID,
                            "source_version": SOURCE_VERSION,
                            "rank": 1,
                        }
                    ],
                    created_at=2,
                    updated_at=2,
                )
            )
        editor_fragment_search = clip_request_from_asset(
            asset=self.asset,
            session=self.session,
            payload={
                **self.payload(
                    idempotency_key="clip-editor-search-frags-0001"
                ),
                "source_search_id": editor_search_id,
            },
            minimum_duration_ms=250,
            maximum_duration_ms=1_800_000,
        )
        repository.validate_source_fragment(editor_fragment_search)

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_search_runs)
                .where(media_library_search_runs.c.search_id == search_id)
                .values(
                    top_candidates_json=[
                        {
                            "source": "media_library",
                            "source_asset_id": ASSET_ID,
                            "source_version": "c" * 64,
                            "rank": 1,
                        }
                    ]
                )
            )
        with self.assertRaises(MediaClipError) as version_error:
            repository.validate_source_fragment(valid)
        self.assertEqual(
            version_error.exception.code,
            "media_clip_search_provenance_invalid",
        )

    def test_process_local_idempotency_returns_same_job_and_conflicts(
        self,
    ) -> None:
        processor = _BlockingProcessor()
        manager = self.manager(processor=processor)
        first = manager.submit(
            asset=self.asset,
            session=self.session,
            payload=self.payload(),
        )
        self.assertTrue(processor.started.wait(timeout=2))
        replay = manager.submit(
            asset=self.asset,
            session=self.session,
            payload=self.payload(),
        )
        self.assertEqual(replay["clip_job_id"], first["clip_job_id"])
        self.assertEqual(processor.calls, 1)

        with self.assertRaises(MediaClipError) as raised:
            manager.submit(
                asset=self.asset,
                session=self.session,
                payload=self.payload(end_ms=500),
            )
        self.assertEqual(raised.exception.code, "idempotency_key_conflict")
        manager.cancel_job(ASSET_ID, str(first["clip_job_id"]))
        terminal = self.wait_terminal(manager, str(first["clip_job_id"]))
        self.assertEqual(terminal["status"], "cancelled")

    def test_cancel_terminates_ffmpeg_cleans_part_and_is_idempotent(
        self,
    ) -> None:
        metrics: list[tuple[str, int]] = []
        processor = _BlockingProcessor()
        manager = self.manager(
            processor=processor,
            metric_sink=lambda name, value: metrics.append((name, value)),
        )
        created = manager.submit(
            asset=self.asset,
            session=self.session,
            payload=self.payload(idempotency_key="clip-job-cancel-0001"),
        )
        self.assertTrue(processor.started.wait(timeout=2))
        self.assertTrue(manager.has_active_job(ASSET_ID))
        self.assertTrue(manager.has_active_jobs(ASSET_ID))
        cancelled = manager.cancel_job(
            ASSET_ID, str(created["clip_job_id"])
        )
        self.assertIn(cancelled["status"], {"running", "cancelled"})
        terminal = self.wait_terminal(manager, str(created["clip_job_id"]))
        self.assertEqual(terminal["status"], "cancelled")
        self.assertGreaterEqual(processor.process.terminate_count, 1)
        self.assertFalse(processor.part_path.exists())
        replay = manager.cancel_job(ASSET_ID, str(created["clip_job_id"]))
        self.assertEqual(replay["status"], "cancelled")
        active_values = [
            value
            for name, value in metrics
            if name == "media_library_clip_active"
        ]
        self.assertIn(1, active_values)
        self.assertEqual(active_values[-1], 0)

    def test_shutdown_waits_for_active_worker_before_database_disposal(
        self,
    ) -> None:
        processor = _BlockingProcessor()
        manager = self.manager(processor=processor)
        created = manager.submit(
            asset=self.asset,
            session=self.session,
            payload=self.payload(
                idempotency_key="clip-job-shutdown-0001"
            ),
        )
        self.assertTrue(processor.started.wait(2))
        manager.shutdown()
        self.assertTrue(
            all(future.done() for future in manager._futures.values())
        )
        self.assertEqual(
            manager.get_job(ASSET_ID, str(created["clip_job_id"]))["status"],
            "cancelled",
        )
        self.assertFalse(processor.part_path and processor.part_path.exists())
        with self.engine.connect() as conn:
            self.assertEqual(
                conn.execute(
                    media_library_assets.select().where(
                        media_library_assets.c.asset_id == ASSET_ID
                    )
                ).first().asset_id,
                ASSET_ID,
            )
        self.assertFalse(manager.has_active_job(ASSET_ID))

    def test_clip_failure_metric_contains_structured_error_code(
        self,
    ) -> None:
        metrics: list[tuple[str, int]] = []
        manager = self.manager(
            processor=_FailingProcessor(),
            metric_sink=lambda name, value: metrics.append((name, value)),
        )
        created = manager.submit(
            asset=self.asset,
            session=self.session,
            payload=self.payload(
                idempotency_key="clip-job-failure-metric-0001"
            ),
        )
        terminal = self.wait_terminal(
            manager,
            str(created["clip_job_id"]),
        )
        self.assertEqual(terminal["status"], "failed")
        self.assertIn(
            (
                'media_library_clip_failure_total'
                '{code="media_clip_contract_failure"}',
                1,
            ),
            metrics,
        )

    def test_boot_generation_distinguishes_lost_from_not_found(self) -> None:
        first_events: list[tuple[str, dict]] = []
        first_processor = _BlockingProcessor()
        first = self.manager(
            processor=first_processor,
            boot_id="1" * 32,
            event_sink=lambda kind, payload: first_events.append(
                (kind, payload)
            ),
        )
        created = first.submit(
            asset=self.asset,
            session=self.session,
            payload=self.payload(idempotency_key="clip-job-lost-000001"),
        )
        self.assertTrue(first_processor.started.wait(timeout=2))
        self.assertIn(
            "media_library.clip.requested",
            [kind for kind, _payload in first_events],
        )

        second_events: list[tuple[str, dict]] = []
        second = self.manager(
            processor=_NeverCalledProcessor(),
            boot_id="2" * 32,
            event_sink=lambda kind, payload: second_events.append(
                (kind, payload)
            ),
        )
        with self.assertRaises(MediaClipError) as lost:
            second.get_job(ASSET_ID, str(created["clip_job_id"]))
        self.assertEqual(lost.exception.status_code, 410)
        self.assertEqual(lost.exception.code, "clip_job_lost")
        self.assertIn(
            "media_library.clip.lost",
            [kind for kind, _payload in second_events],
        )
        with self.assertRaises(MediaClipError) as missing:
            second.get_job(ASSET_ID, "not-a-job")
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(missing.exception.code, "clip_job_not_found")
        first.cancel_job(ASSET_ID, str(created["clip_job_id"]))

    def test_database_success_short_circuits_ffmpeg_after_restart(
        self,
    ) -> None:
        repository = ClipDerivativeRepository(self.engine)
        request = clip_request_from_asset(
            asset=self.asset,
            session=self.session,
            payload=self.payload(idempotency_key="clip-job-durable-0001"),
            minimum_duration_ms=250,
            maximum_duration_ms=1_800_000,
        )
        paths = ClipStorage(self.workspace).allocate(
            "mlc_0000000001000_aaaaaaaaaaaa", request.display_name
        )
        paths.final_path.write_bytes(b"durable-clip")
        values = {
            **request.derivative_identity(),
            "clip_id": "mlc_0000000001000_aaaaaaaaaaaa",
            "idempotency_key": request.idempotency_key,
            "output_path": paths.relative_path,
            "duration_ms": 250,
            "content_sha256": "b" * 64,
            "size_bytes": paths.final_path.stat().st_size,
            "search_eligible": False,
            "created_at": 1_000,
        }
        repository.create_with_session_file(
            values=values,
            request=request,
            updated_at=1_000,
        )
        processor = _NeverCalledProcessor()
        manager = self.manager(processor=processor)
        view = manager.submit(
            asset=self.asset,
            session=self.session,
            payload=self.payload(idempotency_key="clip-job-durable-0001"),
        )
        self.assertEqual(view["status"], "completed")
        self.assertEqual(view["progress"], 100)
        self.assertEqual(
            view["clip_id"], "mlc_0000000001000_aaaaaaaaaaaa"
        )
        self.assertEqual(processor.calls, 0)

    def test_router_contract_has_extra_forbid_and_all_clip_routes(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            ClipJobCreateRequest.model_validate(
                {**self.payload(), "provider": "forbidden"}
            )
        with self.assertRaises(ValidationError):
            ClipSearchMetadataPatchRequest.model_validate({})
        with self.assertRaises(ValidationError):
            ClipSearchMetadataPatchRequest.model_validate(
                {"search_eligible": True, "provider": "forbidden"}
            )
        manager = self.manager(processor=_NeverCalledProcessor())
        fake_asset_repo = SimpleNamespace(get=lambda _asset_id: self.asset)
        fake_session_repo = SimpleNamespace(get=lambda _session_id: self.session)
        ctx = SimpleNamespace(
            engine=self.engine,
            media_library_repo=fake_asset_repo,
            session_repo=fake_session_repo,
        )
        router = build_media_library_clip_router(ctx, manager=manager)
        routes = {
            (next(iter(route.methods)), route.path)
            for route in router.routes
            if getattr(route, "methods", None)
        }
        expected = {
            ("POST", "/api/media-library/{asset_id}/clip-jobs"),
            (
                "GET",
                "/api/media-library/{asset_id}/clip-jobs/{clip_job_id}",
            ),
            (
                "POST",
                "/api/media-library/{asset_id}/clip-jobs/"
                "{clip_job_id}/cancel",
            ),
            ("GET", "/api/media-library/{asset_id}/clips"),
            ("GET", "/api/media-library/{asset_id}/clips/{clip_id}"),
            ("PATCH", "/api/media-library/{asset_id}/clips/{clip_id}"),
            ("DELETE", "/api/media-library/{asset_id}/clips/{clip_id}"),
        }
        self.assertTrue(expected.issubset(routes))

    def test_router_inventory_can_be_built_without_database_engine(
        self,
    ) -> None:
        ctx = SimpleNamespace(
            engine=None,
            media_library_repo=SimpleNamespace(get=lambda _asset_id: None),
            session_repo=SimpleNamespace(get=lambda _session_id: None),
        )

        router = build_media_library_clip_router(ctx)

        self.assertEqual(len(router.routes), 7)
        self.assertIsNone(getattr(ctx, "media_clip_job_manager", None))

    def test_router_with_engine_lazily_initializes_unavailable_clip_runtime(
        self,
    ) -> None:
        ctx = SimpleNamespace(
            engine=self.engine,
            media_library_repo=SimpleNamespace(
                get=lambda _asset_id: self.asset
            ),
            session_repo=SimpleNamespace(
                get=lambda _session_id: self.session
            ),
        )
        unavailable = MediaClipError(
            "media_binary_not_found",
            "FFmpeg 不可用。",
            status_code=503,
        )

        with patch(
            "opcrew_backend.media_library_clips.router."
            "ensure_clip_job_manager",
            side_effect=unavailable,
        ) as ensure:
            router = build_media_library_clip_router(ctx)
            ensure.assert_not_called()
            list_clips = next(
                route.endpoint
                for route in router.routes
                if getattr(route, "name", "") == "list_clips"
            )
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(list_clips(ASSET_ID))

        self.assertEqual(ensure.call_count, 1)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail["code"],
            "media_clip_service_unavailable",
        )

    def test_clip_search_metadata_is_atomic_normalized_and_audited(self) -> None:
        clip_id = "mlc_0000000001000_aaaaaaaaaaaa"
        with self.engine.begin() as conn:
            conn.execute(
                media_library_clip_derivatives.insert().values(
                    clip_id=clip_id,
                    idempotency_key="clip-search-metadata-0001",
                    source_asset_id=ASSET_ID,
                    source_session_id=self.session_id,
                    source_version=SOURCE_VERSION,
                    source_start_ms=1000,
                    source_end_ms=5000,
                    output_path=(
                        f"SessionOutput/clips/{clip_id}/clip.mp4"
                    ),
                    display_name="旧片段名称",
                    duration_ms=4000,
                    content_sha256="b" * 64,
                    size_bytes=123,
                    operation="precise_reencode_v1",
                    search_eligible=False,
                    created_at=1,
                )
            )
        events: list[tuple[str, dict[str, object]]] = []
        metrics: list[tuple[str, int]] = []
        manager = ClipJobManager(
            self.engine,
            processor=_NeverCalledProcessor(),
            max_workers=1,
            boot_id="2" * 32,
            now_ms=lambda: 2_000,
            event_sink=lambda kind, payload: events.append((kind, payload)),
            metric_sink=lambda name, value: metrics.append((name, value)),
        )
        self.managers.append(manager)

        updated = manager.update_clip_search_metadata(
            asset_id=ASSET_ID,
            clip_id=clip_id,
            display_name=" 化橘红/倒入  玻璃碗 ",
            tags=["化橘红", "玻璃碗", "化橘红", "ＧＲＥＥＮ"],
            search_eligible=True,
            update_display_name=True,
            update_tags=True,
        )
        self.assertEqual(updated["display_name"], "化橘红 倒入 玻璃碗")
        self.assertEqual(updated["tags"], ["化橘红", "玻璃碗", "GREEN"])
        self.assertTrue(updated["search_eligible"])
        self.assertEqual(updated["search_enabled_at"], 2_000)
        self.assertEqual(updated["search_updated_at"], 2_000)
        with self.engine.connect() as conn:
            row = conn.execute(
                media_library_clip_derivatives.select().where(
                    media_library_clip_derivatives.c.clip_id == clip_id
                )
            ).mappings().one()
        self.assertEqual(
            row["search_text"],
            "化橘红 倒入 玻璃碗 化橘红 玻璃碗 green",
        )
        self.assertEqual(
            row["search_normalization_version"],
            "nfkc_casefold_ws_v1",
        )
        self.assertIn(
            ("media_library_clip_search_enabled_total", 1), metrics
        )
        self.assertEqual(
            events[-1][0], "media_library.clip.search_metadata_updated"
        )
        self.assertNotIn("output_path", events[-1][1])

        disabled = manager.update_clip_search_metadata(
            asset_id=ASSET_ID,
            clip_id=clip_id,
            search_eligible=False,
        )
        self.assertFalse(disabled["search_eligible"])
        self.assertEqual(disabled["search_enabled_at"], 2_000)
        self.assertIn(
            ("media_library_clip_search_disabled_total", 1), metrics
        )

    def test_clip_search_metadata_validation_and_source_authority(self) -> None:
        clip_id = "mlc_0000000001001_bbbbbbbbbbbb"
        with self.engine.begin() as conn:
            conn.execute(
                media_library_clip_derivatives.insert().values(
                    clip_id=clip_id,
                    idempotency_key="clip-search-metadata-0002",
                    source_asset_id=ASSET_ID,
                    source_session_id=self.session_id,
                    source_version=SOURCE_VERSION,
                    source_start_ms=0,
                    source_end_ms=1000,
                    output_path=(
                        f"SessionOutput/clips/{clip_id}/clip.mp4"
                    ),
                    display_name="   ",
                    duration_ms=1000,
                    content_sha256="c" * 64,
                    size_bytes=123,
                    search_eligible=False,
                    created_at=1,
                )
            )
        manager = self.manager(processor=_NeverCalledProcessor())
        invalid_cases = (
            ({"tags": ["ok"] * 11, "update_tags": True}, "media_clip_tags_too_many"),
            ({"tags": [""], "update_tags": True}, "media_clip_tag_invalid"),
            ({"tags": ["x" * 33], "update_tags": True}, "media_clip_tag_invalid"),
            ({"tags": [1], "update_tags": True}, "media_clip_tag_invalid"),
            ({"search_eligible": True}, "media_clip_search_terms_required"),
        )
        for kwargs, code in invalid_cases:
            with self.subTest(code=code), self.assertRaises(MediaClipError) as raised:
                manager.update_clip_search_metadata(
                    asset_id=ASSET_ID,
                    clip_id=clip_id,
                    **kwargs,
                )
            self.assertEqual(raised.exception.code, code)

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == ASSET_ID)
                .values(archived=True)
            )
        with self.assertRaises(MediaClipError) as archived:
            manager.update_clip_search_metadata(
                asset_id=ASSET_ID,
                clip_id=clip_id,
                tags=["可检索"],
                update_tags=True,
            )
        self.assertEqual(
            archived.exception.code, "media_clip_source_not_eligible"
        )

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == ASSET_ID)
                .values(archived=False, content_sha256="d" * 64)
            )
        with self.assertRaises(MediaClipError) as stale:
            manager.update_clip_search_metadata(
                asset_id=ASSET_ID,
                clip_id=clip_id,
                tags=["可检索"],
                update_tags=True,
            )
        self.assertEqual(
            stale.exception.code, "media_clip_source_version_mismatch"
        )


if __name__ == "__main__":
    unittest.main()
