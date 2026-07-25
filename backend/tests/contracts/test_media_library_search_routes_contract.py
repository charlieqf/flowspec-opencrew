from __future__ import annotations

import json
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select, update
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_fragment_index,
    media_library_search_actions,
    media_library_search_runs,
    media_library_tasks,
    metadata,
    openclip_tasks,
    sessions,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibraryFragmentPublisher,
    MediaLibrarySearchService,
)
from opcrew_backend.media_library_search.router import (  # noqa: E402
    EditorMediaSearchInput,
    EditorSearchActionInput,
    StoryBoardMediaSearchInput,
    _external_search_payload,
    build_media_library_search_router,
)


class MediaLibrarySearchRoutesContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        self.target_task_id = self._seed_storyboard()
        self._seed_asset("asset-current", "当前素材不应返回")
        self._seed_asset("asset-match", "候选素材")
        self.ctx = SimpleNamespace(
            engine=self.engine,
            media_library_search_service=MediaLibrarySearchService(self.engine),
        )
        self.canonical_load_calls = 0
        self.ctx.koubo_storyboard_services = SimpleNamespace(
            task_or_404=lambda task_id: {
                "id": task_id,
                "session_id": 1,
            },
            workspace_for=lambda _task: self.target_workspace,
            load_plan=self._load_canonical_storyboard,
        )
        self.router = build_media_library_search_router(self.ctx)

    def _endpoint(self, name: str):
        return next(route.endpoint for route in self.router.routes if getattr(route, "name", "") == name)

    def test_external_plan_translates_known_cjk_and_marks_unknown_fallback(
        self,
    ) -> None:
        translated = _external_search_payload(
            query="办公室产品视频",
            orientation="landscape",
            limit=12,
        )["plan"]
        self.assertEqual(
            translated["queries"][0]["query"],
            "office product video",
        )
        self.assertEqual(translated["queries"][0]["language"], "en")
        self.assertFalse(translated["degraded"])

        unknown = _external_search_payload(
            query="量子纠缠意境",
            orientation="any",
            limit=12,
        )["plan"]
        self.assertEqual(unknown["queries"][0]["query"], "量子纠缠意境")
        self.assertEqual(unknown["queries"][0]["language"], "zh")
        self.assertTrue(unknown["degraded"])
        self.assertIn(
            "external_query_translation_unavailable",
            unknown["risk_notes"],
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def _seed_storyboard(self) -> int:
        workspace = Path(self.temporary.name) / "storyboard"
        self.target_workspace = workspace
        output = workspace / "SessionOutput" / "storyboard"
        output.mkdir(parents=True)
        (output / "srt_storyboard.json").write_text(
            json.dumps(
                {
                    "shots": [
                        {
                            "scenes": [
                                {
                                    "dialogue_items": [
                                        {
                                            "dialogue_asset_key": (
                                                "raw-source-only"
                                            ),
                                            "text": "过期原始内容",
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (output / "koubo_storyboard_edit.json").write_text(
            json.dumps(
                {
                    "schema_version": "koubo_storyboard_edit_0.1",
                    "shots": [
                        {
                            "scenes": [
                                {
                                    "dialogues": [
                                        {
                                            "dialogue_asset_key": "dlg-stable",
                                            "text": "产品防水能力",
                                        }
                                    ]
                                }
                            ]
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with self.engine.begin() as conn:
            session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="koubo-storyboard",
                        group_id="koubo-storyboard",
                        title="target",
                        status="draft",
                        workspace_dir=str(workspace),
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            return int(
                conn.execute(
                    openclip_tasks.insert()
                    .values(
                        session_id=session_id,
                        status="draft",
                        workflow_mode="koubo_storyboard",
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(openclip_tasks.c.id)
                ).scalar_one()
            )

    def _load_canonical_storyboard(
        self, _task: dict, *, sc
    ) -> tuple[dict, dict]:
        self.assertIs(sc, self.ctx.koubo_storyboard_services)
        self.canonical_load_calls += 1
        path = (
            self.target_workspace
            / "SessionOutput"
            / "storyboard"
            / "koubo_storyboard_edit.json"
        )
        return json.loads(path.read_text(encoding="utf-8")), {}

    def _seed_asset(self, asset_id: str, title: str) -> None:
        workspace = Path(self.temporary.name) / asset_id
        workspace.mkdir()
        source_version = ("a" if asset_id.endswith("current") else "b") * 64
        run_id = f"mlar_dialogue_{asset_id}"
        with self.engine.begin() as conn:
            session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="open-cut-v1",
                        group_id="open-cut-v1",
                        title=title,
                        status="draft",
                        workspace_dir=str(workspace),
                        created_at=10,
                        updated_at=10,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id=asset_id,
                    session_id=session_id,
                    display_name=title,
                    original_filename=f"{asset_id}.mp4",
                    source_video_path=f"inbox/{asset_id}.mp4",
                    content_sha256=source_version,
                    content_hashed_at=10,
                    media_type="video",
                    duration_ms=5000,
                    width=1920,
                    height=1080,
                    upload_status="ready",
                    analysis_status="not_analyzed",
                    subtitle_mode="unknown",
                    tags_json=[],
                    archived=False,
                    referenced_by_count=0,
                    created_at=10,
                    updated_at=10,
                )
            )
            conn.execute(
                media_library_tasks.insert().values(
                    asset_id=asset_id,
                    session_id=session_id,
                    title=title,
                    status="draft",
                    dialogue_status="not_analyzed",
                    visual_status="not_analyzed",
                    visual_structure_status="not_analyzed",
                    visual_semantic_status="not_analyzed",
                    composite_status="not_analyzed",
                    created_at=10,
                    updated_at=10,
                )
            )
            conn.execute(
                media_library_analysis_runs.insert().values(
                    analysis_run_id=run_id,
                    asset_id=asset_id,
                    scheme="dialogue",
                    source_version=source_version,
                    status="running",
                    progress_json={},
                    upstream_refs_json={},
                    is_current=False,
                    created_at=10,
                    updated_at=10,
                )
            )
        MediaLibraryFragmentPublisher(self.engine).publish_dialogue(
            asset_id=asset_id,
            analysis_run_id=run_id,
            result_hash="c" * 64,
            fragments=[
                {
                    "fragment_id": "srt_0001",
                    "start_ms": 100,
                    "end_ms": 1200,
                    "dialogue_text": "产品防水能力经过测试",
                }
            ],
            timestamp=11,
        )

    def test_storyboard_run_rereads_authoritative_dialogue_and_replays(self) -> None:
        payload = asyncio.run(
            self._endpoint("storyboard_search_run")(
                self.target_task_id,
                "dlg-stable",
                StoryBoardMediaSearchInput(user_text="", orientation="any", limit=12),
            )
        )

        self.assertTrue(payload["planner_degraded"])
        self.assertGreater(self.canonical_load_calls, 0)
        self.assertEqual(
            {item["asset_id"] for item in payload["items"]},
            {"asset-current", "asset-match"},
        )
        self.assertEqual(
            payload["items"][0]["allowed_actions"],
            ["preview", "open_editor", "import_original"],
        )
        self.assertTrue(payload["items"][0]["matched_fragments"][0]["run_id"].startswith("mlar_dialogue_"))

        replay = asyncio.run(self._endpoint("storyboard_search_replay")(self.target_task_id, payload["search_id"]))
        self.assertEqual(len(replay["items"]), 2)

    def test_storyboard_rejects_unknown_or_empty_authoritative_dialogue(self) -> None:
        with self.assertRaises(HTTPException) as missing:
            asyncio.run(
                self._endpoint("storyboard_search_run")(
                    self.target_task_id,
                    "dlg-client-invented",
                    StoryBoardMediaSearchInput(),
                )
            )
        self.assertEqual(missing.exception.status_code, 409)
        self.assertEqual(missing.exception.detail["code"], "storyboard_dialogue_stale")
        with self.assertRaises(HTTPException) as raw_only:
            asyncio.run(
                self._endpoint("storyboard_search_run")(
                    self.target_task_id,
                    "raw-source-only",
                    StoryBoardMediaSearchInput(),
                )
            )
        self.assertEqual(
            raw_only.exception.detail["code"],
            "storyboard_dialogue_stale",
        )

    def test_editor_excludes_current_asset_and_stale_replay_items(self) -> None:
        payload = asyncio.run(
            self._endpoint("editor_search_run")(
                "asset-current",
                EditorMediaSearchInput(
                    fragment_refs=[
                        {
                            "scheme": "dialogue",
                            "run_id": "mlar_dialogue_asset-current",
                            "fragment_id": "srt_0001",
                        }
                    ],
                    sources=["media_library"],
                ),
            )
        )
        self.assertEqual(
            [item["asset_id"] for item in payload["items"]],
            ["asset-match"],
        )

        with self.engine.begin() as conn:
            conn.execute(update(media_library_fragment_index).where(media_library_fragment_index.c.asset_id == "asset-match").values(is_active=False))
            conn.execute(update(media_library_analysis_runs).where(media_library_analysis_runs.c.asset_id == "asset-match").values(status="stale"))
        replay = asyncio.run(self._endpoint("editor_search_replay")("asset-current", payload["search_id"]))
        self.assertEqual(replay["items"], [])

    def test_editor_combines_real_provider_and_global_sources_without_crossing_actions(
        self,
    ) -> None:
        async def external_events(task, payload, *, sc):
            self.assertEqual(task["id"], self.target_task_id)
            self.assertEqual(payload["media_types"], ["video"])
            self.assertNotIn("media_library", payload["sources"])
            self.assertNotIn(
                "plan",
                payload,
                "editor run must use the existing provider planner/translation flow",
            )
            yield {
                "type": "completed",
                "search_id": "search_provider_contract",
                "items": [
                    {
                        "candidate_id": "pexels_video_7",
                        "provider": "pexels",
                        "provider_asset_id": "7",
                        "media_type": "video",
                        "title": "External candidate",
                        "preview_url": "https://videos.pexels.com/preview.mp4",
                        "thumbnail_url": "https://images.pexels.com/thumb.jpg",
                        "source_url": "https://www.pexels.com/video/7",
                        "creator": {
                            "name": "Creator",
                            "url": "https://www.pexels.com/@creator",
                        },
                        "license": {
                            "name": "Pexels License",
                            "url": "https://www.pexels.com/license/",
                            "license_status": "confirmed",
                        },
                        "width": 1920,
                        "height": 1080,
                        "duration_seconds": 4.25,
                        "orientation": "landscape",
                        "score": 0.9,
                        "score_reasons": ["provider relevance rank"],
                        "allowed_actions": ["preview", "import_whole"],
                    }
                ],
                "provider_stats": {"pexels": {"status": "ok"}},
                "ranking": {},
            }

        self.ctx.koubo_storyboard_services = SimpleNamespace(
            task_or_404=lambda task_id: {"id": task_id, "session_id": 1},
            stream_asset_search_events=external_events,
        )
        payload = asyncio.run(
            self._endpoint("editor_search_run")(
                "asset-current",
                EditorMediaSearchInput(
                    target_task_id=self.target_task_id,
                    fragment_refs=[
                        {
                            "scheme": "dialogue",
                            "run_id": "mlar_dialogue_asset-current",
                            "fragment_id": "srt_0001",
                        }
                    ],
                    sources=["external", "media_library"],
                ),
            )
        )

        self.assertEqual(
            set(payload["search_runs"]),
            {"external", "media_library"},
        )
        self.assertTrue(str(payload["search_runs"]["media_library"]).startswith("mls_"))
        self.assertEqual(
            payload["search_runs"]["external"],
            "search_provider_contract",
        )
        self.assertEqual(
            payload["search_id"],
            payload["search_runs"]["media_library"],
        )
        self.assertNotEqual(payload["search_id"], payload["search_runs"]["external"])
        by_source = {item["source"]: item for item in payload["items"]}
        self.assertEqual(
            by_source["external"]["allowed_actions"],
            ["preview", "import_whole"],
        )
        self.assertIsNone(by_source["external"]["asset_id"])
        self.assertIsNone(by_source["external"]["source_version"])
        self.assertNotIn("open_editor", by_source["external"]["allowed_actions"])
        self.assertEqual(
            by_source["media_library"]["allowed_actions"],
            ["preview", "open_editor", "import_original"],
        )
        with self.engine.connect() as conn:
            center = conn.execute(select(media_library_search_runs).where(media_library_search_runs.c.search_id == payload["search_id"])).mappings().one()
        self.assertEqual(
            center["source_runs_json"],
            {
                "media_library": payload["search_id"],
                "external": "search_provider_contract",
            },
        )
        snapshot_text = json.dumps(center["top_candidates_json"])
        self.assertNotIn("https://", snapshot_text)
        self.assertNotIn("External candidate", snapshot_text)
        self.assertNotIn("preview", snapshot_text)
        snapshots = {item["source"]: item for item in center["top_candidates_json"]}
        self.assertEqual(snapshots["media_library"]["source_version"], "b" * 64)
        self.assertIsNone(snapshots["external"]["source_version"])

    def test_editor_external_source_requires_target_task(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                self._endpoint("editor_search_run")(
                    "asset-current",
                    EditorMediaSearchInput(
                        sources=["external"],
                        user_text="office product video",
                    ),
                )
            )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(
            raised.exception.detail["code"],
            "search_target_task_required",
        )

    def test_editor_external_only_uses_center_run_and_secure_provider_replay(
        self,
    ) -> None:
        candidate = {
            "candidate_id": "pexels_video_external_only",
            "provider": "pexels",
            "provider_asset_id": "99",
            "media_type": "video",
            "title": "External only private title",
            "description": "private provider body",
            "preview_url": "https://videos.example/preview.mp4",
            "thumbnail_url": "https://images.example/thumb.jpg",
            "source_url": "https://provider.example/video/99",
            "width": 1080,
            "height": 1920,
            "duration_seconds": 3.5,
            "orientation": "portrait",
            "score": 0.8,
        }

        async def external_events(task, payload, *, sc):
            del payload, sc
            self.assertEqual(task["id"], self.target_task_id)
            yield {
                "type": "completed",
                "search_id": "search_100_externalonly",
                "items": [candidate],
                "provider_stats": {"pexels": {"status": "ok"}},
            }

        def task_or_404(task_id):
            self.assertEqual(task_id, self.target_task_id)
            return {"id": task_id, "session_id": 901}

        def load_run(task, search_id, *, sc):
            del sc
            self.assertEqual(task["id"], self.target_task_id)
            self.assertEqual(search_id, "search_100_externalonly")
            return {
                "search_id": search_id,
                "task_id": self.target_task_id,
                "session_id": 901,
                "candidates": [candidate],
            }

        self.ctx.koubo_storyboard_services = SimpleNamespace(
            task_or_404=task_or_404,
            stream_asset_search_events=external_events,
            load_asset_search_run=load_run,
        )
        payload = asyncio.run(
            self._endpoint("editor_search_run")(
                "asset-current",
                EditorMediaSearchInput(
                    target_task_id=self.target_task_id,
                    sources=["external"],
                    user_text="office product video",
                ),
            )
        )
        self.assertTrue(payload["search_id"].startswith("mls_"))
        self.assertNotEqual(payload["search_id"], "search_100_externalonly")
        self.assertEqual(
            payload["search_runs"],
            {
                "media_library": None,
                "external": "search_100_externalonly",
            },
        )
        self.assertIsNone(payload["items"][0]["asset_id"])
        self.assertIsNone(payload["items"][0]["source_version"])
        with self.engine.connect() as conn:
            center = conn.execute(select(media_library_search_runs).where(media_library_search_runs.c.search_id == payload["search_id"])).mappings().one()
        self.assertEqual(center["entry_point"], "editor")
        self.assertEqual(center["source_asset_id"], "asset-current")
        self.assertEqual(center["requested_sources_json"], ["external"])
        self.assertEqual(
            center["source_runs_json"],
            {
                "media_library": None,
                "external": "search_100_externalonly",
            },
        )
        snapshot = json.dumps(center["top_candidates_json"])
        for private in (
            "https://",
            "External only private title",
            "private provider body",
            "preview.mp4",
        ):
            self.assertNotIn(private, snapshot)

        replay = asyncio.run(self._endpoint("editor_search_replay")("asset-current", payload["search_id"]))
        self.assertEqual(
            replay["search_runs"]["external"],
            "search_100_externalonly",
        )
        self.assertEqual(len(replay["items"]), 1)
        self.assertEqual(replay["items"][0]["source"], "external")
        self.assertIsNone(replay["items"][0]["asset_id"])
        self.assertIsNone(replay["items"][0]["source_version"])
        self.assertEqual(
            replay["items"][0]["allowed_actions"],
            ["preview", "import_whole"],
        )
        self.assertNotIn("open_editor", replay["items"][0]["allowed_actions"])

        with self.assertRaises(HTTPException) as mismatch:
            asyncio.run(self._endpoint("editor_search_replay")("asset-match", payload["search_id"]))
        self.assertEqual(mismatch.exception.status_code, 404)
        self.assertEqual(mismatch.exception.detail["code"], "search_run_not_found")

        preview_action = asyncio.run(
            self._endpoint("editor_search_action")(
                "asset-current",
                payload["search_id"],
                EditorSearchActionInput(
                    action_kind="preview",
                    source="external",
                    candidate_id="pexels_video_external_only",
                ),
            )
        )
        self.assertEqual(preview_action, {"ok": True, "recorded": True})
        with self.engine.connect() as conn:
            external_action = conn.execute(select(media_library_search_actions)).mappings().one()
        self.assertEqual(external_action["source"], "external")
        self.assertIsNone(external_action["source_asset_id"])
        self.assertEqual(external_action["candidate_rank"], 1)

        with self.assertRaises(HTTPException) as forbidden_action:
            asyncio.run(
                self._endpoint("editor_search_action")(
                    "asset-current",
                    payload["search_id"],
                    EditorSearchActionInput(
                        action_kind="open_editor",
                        source="external",
                        candidate_id="pexels_video_external_only",
                    ),
                )
            )
        self.assertEqual(
            forbidden_action.exception.detail["code"],
            "search_action_not_allowed",
        )

    def test_editor_partial_source_failure_is_returned_and_persisted(
        self,
    ) -> None:
        async def external_events(task, payload, *, sc):
            del task, payload, sc
            yield {
                "type": "failed",
                "detail": "provider unavailable at https://private.invalid",
            }

        self.ctx.koubo_storyboard_services = SimpleNamespace(
            task_or_404=lambda task_id: {
                "id": task_id,
                "session_id": 902,
            },
            stream_asset_search_events=external_events,
        )
        payload = asyncio.run(
            self._endpoint("editor_search_run")(
                "asset-current",
                EditorMediaSearchInput(
                    target_task_id=self.target_task_id,
                    sources=["external", "media_library"],
                    fragment_refs=[
                        {
                            "scheme": "dialogue",
                            "run_id": "mlar_dialogue_asset-current",
                            "fragment_id": "srt_0001",
                        }
                    ],
                ),
            )
        )
        self.assertEqual(
            [item["source"] for item in payload["items"]],
            ["media_library"],
        )
        self.assertEqual(
            payload["source_errors"]["external"]["code"],
            "external_search_failed",
        )
        with self.engine.connect() as conn:
            center = conn.execute(select(media_library_search_runs).where(media_library_search_runs.c.search_id == payload["search_id"])).mappings().one()
        self.assertEqual(center["status"], "completed")
        self.assertEqual(center["error_code"], "partial_source_failure")
        self.assertEqual(
            center["source_runs_json"]["source_errors"],
            {"external": {"code": "external_search_failed"}},
        )
        self.assertNotIn("private.invalid", json.dumps(center["source_runs_json"]))

    def test_editor_all_sources_failed_returns_502_and_persists_center_failure(
        self,
    ) -> None:
        async def external_events(task, payload, *, sc):
            del task, payload, sc
            yield {"type": "failed", "detail": "external offline"}

        self.ctx.koubo_storyboard_services = SimpleNamespace(
            task_or_404=lambda task_id: {
                "id": task_id,
                "session_id": 903,
            },
            stream_asset_search_events=external_events,
        )
        with patch.object(
            self.ctx.media_library_search_service.repository,
            "retrieve",
            side_effect=RuntimeError("global retrieval failed"),
        ):
            with self.assertRaises(HTTPException) as failed:
                asyncio.run(
                    self._endpoint("editor_search_run")(
                        "asset-current",
                        EditorMediaSearchInput(
                            target_task_id=self.target_task_id,
                            sources=["external", "media_library"],
                            user_text="product",
                        ),
                    )
                )
        self.assertEqual(failed.exception.status_code, 502)
        self.assertEqual(failed.exception.detail["code"], "editor_search_failed")
        search_id = failed.exception.detail["search_id"]
        self.assertTrue(search_id.startswith("mls_"))
        with self.engine.connect() as conn:
            center = conn.execute(select(media_library_search_runs).where(media_library_search_runs.c.search_id == search_id)).mappings().one()
        self.assertEqual(center["status"], "failed")
        self.assertEqual(center["error_code"], "editor_search_failed")
        self.assertEqual(
            set(center["source_runs_json"]["source_errors"]),
            {"external", "media_library"},
        )

    def test_editor_action_validates_snapshot_and_telemetry_is_best_effort(
        self,
    ) -> None:
        run = asyncio.run(
            self._endpoint("editor_search_run")(
                "asset-current",
                EditorMediaSearchInput(
                    sources=["media_library"],
                    fragment_refs=[
                        {
                            "scheme": "dialogue",
                            "run_id": "mlar_dialogue_asset-current",
                            "fragment_id": "srt_0001",
                        }
                    ],
                ),
            )
        )
        with self.engine.connect() as conn:
            snapshot = conn.execute(
                select(media_library_search_runs.c.top_candidates_json).where(media_library_search_runs.c.search_id == run["search_id"])
            ).scalar_one()
        self.assertEqual(snapshot[0]["source_asset_id"], "asset-match")
        self.assertEqual(snapshot[0]["source_version"], "b" * 64)
        replay = asyncio.run(self._endpoint("editor_search_replay")("asset-current", run["search_id"]))
        self.assertEqual(replay["items"][0]["asset_id"], "asset-match")
        self.assertEqual(replay["items"][0]["source_version"], "b" * 64)
        action = EditorSearchActionInput(
            action_kind="preview",
            source="media_library",
            candidate_id="asset-match",
            metadata={
                "fragment_id": "srt_0001",
                "preview_url": "https://private.invalid/preview",
            },
        )
        recorded = asyncio.run(self._endpoint("editor_search_action")("asset-current", run["search_id"], action))
        self.assertEqual(recorded, {"ok": True, "recorded": True})
        with self.engine.connect() as conn:
            stored = conn.execute(select(media_library_search_actions)).mappings().one()
        self.assertEqual(stored["candidate_rank"], 1)
        self.assertEqual(stored["source_asset_id"], "asset-match")
        self.assertEqual(stored["metadata_json"], {"fragment_id": "srt_0001"})

        with self.assertRaises(HTTPException) as unknown:
            asyncio.run(
                self._endpoint("editor_search_action")(
                    "asset-current",
                    run["search_id"],
                    EditorSearchActionInput(
                        action_kind="preview",
                        source="media_library",
                        candidate_id="client-forged",
                    ),
                )
            )
        self.assertEqual(
            unknown.exception.detail["code"],
            "search_candidate_not_found",
        )

        with patch.object(
            self.ctx.media_library_search_service.repository,
            "create_action",
            side_effect=RuntimeError("telemetry database unavailable"),
        ):
            best_effort = asyncio.run(self._endpoint("editor_search_action")("asset-current", run["search_id"], action))
        self.assertEqual(best_effort, {"ok": True, "recorded": False})


if __name__ == "__main__":
    unittest.main()
