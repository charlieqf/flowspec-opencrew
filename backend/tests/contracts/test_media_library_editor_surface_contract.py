from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from fastapi import FastAPI  # noqa: E402

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_clip_derivatives,
    media_library_search_runs,
    metadata,
    openclip_tasks,
    sessions,
)
from opcrew_backend.media_library_analysis.run_repository import (  # noqa: E402
    AnalysisRunRepository,
)
from opcrew_backend.media_library_imports import (  # noqa: E402
    MediaLibraryStoryBoardImportService,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibrarySearchService,
)
from opcrew_backend.repositories.media_library import (  # noqa: E402
    MediaLibraryRepository,
)
from opcrew_backend.repositories.sessions import SessionRepository  # noqa: E402
from opcrew_backend.routes.media_library import (  # noqa: E402
    build_media_library_router,
)


def request(
    app: FastAPI, url: str
) -> tuple[int, dict[str, Any]]:
    import anyio

    path, _, query = url.partition("?")

    async def run() -> tuple[int, dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        messages = [
            {"type": "http.request", "body": b"", "more_body": False}
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }

        async def receive() -> dict[str, Any]:
            return (
                messages.pop(0)
                if messages
                else {"type": "http.disconnect"}
            )

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)
        status = next(
            int(item["status"])
            for item in sent
            if item["type"] == "http.response.start"
        )
        body = b"".join(
            item.get("body") or b""
            for item in sent
            if item["type"] == "http.response.body"
        )
        return status, json.loads(body) if body else {}

    return anyio.run(run)


class MediaLibraryEditorSurfaceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        self.asset_id = "mla_editor_contract"
        self.source_version = "a" * 64
        self.source_workspace = self.root / "source"
        self.source_workspace.mkdir()
        self.target_workspace = self.root / "target"
        self.authoritative_plan = {
            "shots": [
                {
                    "scenes": [
                        {
                            "dialogues": [
                                {
                                    "dialogue_asset_key": (
                                        "dialogue_0005"
                                    ),
                                    "text": "防水能力",
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        self.storyboard_load_calls = 0
        storyboard_root = (
            self.target_workspace / "SessionOutput" / "storyboard"
        )
        storyboard_root.mkdir(parents=True)
        (storyboard_root / "srt_storyboard.json").write_text(
            json.dumps(
                {
                    "shots": [
                        {
                            "scenes": [
                                {
                                    "dialogues": [
                                        {
                                            "dialogue_asset_key": (
                                                "raw_source_only"
                                            ),
                                            "text": "过期的原始内容",
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (storyboard_root / "koubo_storyboard_assets.json").write_text(
            '{"assets":[]}', encoding="utf-8"
        )
        with self.engine.begin() as conn:
            source_session_id = int(
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
            target_session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="koubo-storyboard",
                        group_id="koubo-storyboard",
                        title="target",
                        status="draft",
                        workspace_dir=str(self.target_workspace),
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            self.target_task_id = int(
                conn.execute(
                    openclip_tasks.insert()
                    .values(
                        session_id=target_session_id,
                        status="draft",
                        workflow_mode="script",
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(openclip_tasks.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id=self.asset_id,
                    session_id=source_session_id,
                    display_name="十分钟原片",
                    original_filename="原片.mp4",
                    source_video_path="inbox/原片.mp4",
                    content_sha256=self.source_version,
                    content_hashed_at=1,
                    media_type="video",
                    duration_ms=600_000,
                    width=1920,
                    height=1080,
                    format="mp4",
                    size_bytes=123,
                    upload_status="ready",
                    analysis_status="stale",
                    subtitle_mode="unknown",
                    tags_json=[],
                    archived=False,
                    referenced_by_count=0,
                    created_at=1,
                    updated_at=1,
                )
            )
            for index, scheme in enumerate(
                (
                    "dialogue",
                    "visual_structure",
                    "visual_semantic",
                    "composite",
                ),
                start=1,
            ):
                count = 257 if scheme == "dialogue" else 3
                items = [
                    {
                        "fragment_id": (
                            f"srt_{item_index:04d}"
                            if scheme == "dialogue"
                            else f"{scheme}_{item_index:04d}"
                        ),
                        "start_ms": item_index * 1000,
                        "end_ms": item_index * 1000 + 500,
                        "dialogue_text": (
                            f"对白 {item_index}"
                            if scheme == "dialogue"
                            else None
                        ),
                    }
                    for item_index in range(count)
                ]
                relative = f"results/{scheme}.json"
                path = self.source_workspace / relative
                path.parent.mkdir(exist_ok=True)
                path.write_text(
                    json.dumps({"items": items}), encoding="utf-8"
                )
                conn.execute(
                    media_library_analysis_runs.insert().values(
                        analysis_run_id=f"run_{scheme}",
                        asset_id=self.asset_id,
                        scheme=scheme,
                        source_version=self.source_version,
                        status=(
                            "stale" if scheme == "composite" else "ready"
                        ),
                        schema_version=f"{scheme}_v1",
                        result_hash=f"{index}" * 64,
                        result_index_path=relative,
                        upstream_refs_json={},
                        progress_json={},
                        is_current=True,
                        started_at=1,
                        finished_at=2,
                        created_at=1,
                        updated_at=2,
                    )
                )
            conn.execute(
                media_library_clip_derivatives.insert().values(
                    clip_id="mlc_1000_abcdef123456",
                    idempotency_key="editor-clip-key-1234",
                    source_asset_id=self.asset_id,
                    source_session_id=source_session_id,
                    source_version=self.source_version,
                    source_start_ms=543_217,
                    source_end_ms=544_217,
                    source_scheme="dialogue",
                    source_fragment_id="srt_0001",
                    source_analysis_run_id="run_dialogue",
                    output_path=(
                        "SessionOutput/clips/mlc_1000_abcdef123456/"
                        "核心片段.mp4"
                    ),
                    display_name="核心片段",
                    duration_ms=1000,
                    content_sha256="f" * 64,
                    size_bytes=88,
                    operation="precise_reencode_v1",
                    search_eligible=False,
                    created_at=5,
                )
            )
            self.search_id = "mls_1000_abcdef123456"
            conn.execute(
                media_library_search_runs.insert().values(
                    search_id=self.search_id,
                    entry_point="storyboard",
                    target_task_id=self.target_task_id,
                    dialogue_asset_key="dialogue_0005",
                    query_source="dialogue",
                    query_hash="b" * 64,
                    query_plan_json={},
                    planner_version="planner_v1",
                    retrieval_version="retrieval_v1",
                    planner_degraded=False,
                    requested_sources_json=["media_library"],
                    source_runs_json=[],
                    status="completed",
                    result_count=1,
                    zero_result=False,
                    top_candidates_json=[
                        {
                            "source_asset_id": self.asset_id,
                            "matched_fragments": [
                                {"fragment_id": "srt_0001"}
                            ],
                        }
                    ],
                    created_at=1,
                    updated_at=1,
                )
            )
        self.metrics: list[tuple[str, int]] = []
        self.ctx = SimpleNamespace(
            engine=self.engine,
            media_library_repo=MediaLibraryRepository(self.engine),
            media_analysis_run_repo=AnalysisRunRepository(self.engine),
            session_repo=SessionRepository(self.engine),
            media_library_search_service=MediaLibrarySearchService(
                self.engine
            ),
            media_library_metric=lambda name, value: self.metrics.append(
                (name, value)
            ),
        )
        self.ctx.media_library_import_service = (
            MediaLibraryStoryBoardImportService(self.ctx)
        )
        self.ctx.koubo_storyboard_services = SimpleNamespace(
            task_or_404=lambda task_id: {
                "id": task_id,
                "session_id": target_session_id,
            },
            workspace_for=lambda _task: self.target_workspace,
            load_plan=self.load_authoritative_plan,
        )
        self.app = FastAPI()
        self.router = build_media_library_router(self.ctx)
        self.app.include_router(self.router)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def load_authoritative_plan(
        self, _task: dict[str, Any], *, sc: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.assertIs(sc, self.ctx.koubo_storyboard_services)
        self.storyboard_load_calls += 1
        return self.authoritative_plan, {}

    def test_editor_returns_every_fragment_stale_run_clip_and_context(
        self,
    ) -> None:
        url = (
            f"/api/media-library/{self.asset_id}/editor"
            "?start_ms=543217&end_ms=999999"
            f"&target_task_id={self.target_task_id}"
            "&dialogue_asset_key=dialogue_0005"
            f"&search_id={self.search_id}"
            "&matched_fragment_id=srt_0001"
            "&return_to=storyboard_dialogue"
        )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_EDITOR_PAYLOAD_WARN_BYTES": "1"},
        ), self.assertLogs(
            "opcrew_backend.routes.media_library", level="WARNING"
        ) as captured:
            status, payload = request(self.app, url)

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["source_version"], self.source_version)
        self.assertEqual(len(payload["fragments"]["dialogue"]), 257)
        self.assertEqual(len(payload["fragments"]["visual"]), 3)
        self.assertEqual(len(payload["fragments"]["composite"]), 3)
        self.assertEqual(payload["runs"]["composite"]["status"], "stale")
        self.assertEqual(payload["clips"][0]["clip_id"], "mlc_1000_abcdef123456")
        self.assertNotIn("output_path", payload["clips"][0])
        self.assertEqual(
            payload["navigation_context"]["end_ms"], 600_000
        )
        self.assertTrue(payload["navigation_context"]["target_valid"])
        self.assertTrue(payload["navigation_context"]["dialogue_valid"])
        self.assertGreater(self.storyboard_load_calls, 0)
        self.assertTrue(payload["navigation_context"]["search_valid"])
        self.assertEqual(
            payload["navigation_context"]["matched_fragment_id"],
            "srt_0001",
        )
        self.assertEqual(len(payload["import_targets"]), 1)
        self.assertIn(
            ("media_library_editor_fragment_count", 263),
            self.metrics,
        )
        payload_metric = next(
            value
            for name, value in self.metrics
            if name == "media_library_editor_payload_bytes"
        )
        self.assertGreater(payload_metric, 0)
        self.assertIn(
            "media_library_editor_payload_capacity_warning",
            "\n".join(captured.output),
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(str(self.source_workspace), serialized)
        self.assertNotIn(str(self.target_workspace), serialized)

    def test_navigation_is_allowlisted_and_revalidated(self) -> None:
        status, payload = request(
            self.app,
            f"/api/media-library/{self.asset_id}/editor"
            "?target_task_id=999999&dialogue_asset_key=../../secret"
            "&search_id=not-a-real-run&matched_fragment_id=unknown"
            "&return_to=https%3A%2F%2Fevil.example",
        )

        self.assertEqual(status, 200, payload)
        context = payload["navigation_context"]
        self.assertFalse(context["target_valid"])
        self.assertFalse(context["dialogue_valid"])
        self.assertFalse(context["search_valid"])
        self.assertIsNone(context["dialogue_asset_key"])
        self.assertIsNone(context["search_id"])
        self.assertIsNone(context["matched_fragment_id"])
        self.assertIsNone(context["return_to"])

    def test_dialogue_validation_rejects_raw_source_and_duplicates(
        self,
    ) -> None:
        status, payload = request(
            self.app,
            f"/api/media-library/{self.asset_id}/editor"
            f"?target_task_id={self.target_task_id}"
            "&dialogue_asset_key=raw_source_only",
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(
            payload["navigation_context"]["target_valid"]
        )
        self.assertFalse(
            payload["navigation_context"]["dialogue_valid"]
        )

        duplicate = self.authoritative_plan["shots"][0]["scenes"][
            0
        ]["dialogues"][0].copy()
        self.authoritative_plan["shots"][0]["scenes"][0][
            "dialogues"
        ].append(duplicate)
        status, payload = request(
            self.app,
            f"/api/media-library/{self.asset_id}/editor"
            f"?target_task_id={self.target_task_id}"
            "&dialogue_asset_key=dialogue_0005",
        )
        self.assertEqual(status, 200, payload)
        self.assertFalse(
            payload["navigation_context"]["dialogue_valid"]
        )

    def test_editor_route_precedes_detail_and_has_no_pagination(self) -> None:
        paths = [
            str(getattr(route, "path", "")) for route in self.router.routes
        ]
        self.assertLess(
            paths.index("/api/media-library/{asset_id}/editor"),
            paths.index("/api/media-library/{asset_id}"),
        )
        editor_route = next(
            route
            for route in self.router.routes
            if getattr(route, "path", "")
            == "/api/media-library/{asset_id}/editor"
        )
        parameter_names = {
            parameter.name
            for parameter in editor_route.dependant.query_params
        }
        self.assertNotIn("page", parameter_names)
        self.assertNotIn("page_size", parameter_names)
        self.assertNotIn("limit", parameter_names)


if __name__ == "__main__":
    unittest.main()
