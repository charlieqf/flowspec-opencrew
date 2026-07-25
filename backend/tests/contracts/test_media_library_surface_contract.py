from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_fragment_index,
    media_library_tasks,
    metadata,
    sessions,
)
from opcrew_backend.media_library_analysis.run_repository import AnalysisRunRepository  # noqa: E402
from opcrew_backend.repositories.media_library import MediaLibraryRepository  # noqa: E402
from opcrew_backend.repositories.media_library_tasks import MediaLibraryTaskRepository  # noqa: E402
from opcrew_backend.repositories.sessions import SessionRepository  # noqa: E402
from opcrew_backend.routes.media_library import (  # noqa: E402
    MediaLibraryPatchBodyLimitMiddleware,
    _serialize,
    build_media_library_router,
)


def request(app: FastAPI, method: str, url: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    import anyio

    path, _, query = url.partition("?")
    body_bytes = json.dumps(body or {}).encode("utf-8") if body is not None else b""

    async def run() -> tuple[int, dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        messages = [{"type": "http.request", "body": body_bytes, "more_body": False}]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": query.encode("utf-8"),
            "root_path": "",
            "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }

        async def receive() -> dict[str, Any]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)
        status = 500
        chunks: list[bytes] = []
        for message in sent:
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body") or b"")
        payload = b"".join(chunks).decode("utf-8")
        return status, json.loads(payload) if payload else {}

    return anyio.run(run)


def raw_request(
    app: FastAPI,
    method: str,
    url: str,
    body_bytes: bytes,
    *,
    chunk_size: int | None = None,
) -> tuple[int, dict[str, Any]]:
    import anyio

    path, _, query = url.partition("?")

    async def run() -> tuple[int, dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        chunks = (
            [body_bytes]
            if not chunk_size
            else [
                body_bytes[index:index + chunk_size]
                for index in range(0, len(body_bytes), chunk_size)
            ]
        )
        messages = [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(chunks) - 1,
            }
            for index, chunk in enumerate(chunks)
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": query.encode("utf-8"),
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
            ],
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
            (
                int(message["status"])
                for message in sent
                if message["type"] == "http.response.start"
            ),
            500,
        )
        payload = b"".join(
            message.get("body") or b""
            for message in sent
            if message["type"] == "http.response.body"
        ).decode("utf-8")
        return status, json.loads(payload) if payload else {}

    return anyio.run(run)


class MediaLibrarySurfaceContractTest(unittest.TestCase):
    def setUp(self) -> None:
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
                        sender_name="OpenCut V1",
                        title="老板采访横屏",
                        command_text="",
                        status="draft",
                        workspace_dir="/tmp/opencrew-test/1/workspace",
                        created_at=1000,
                        updated_at=3000,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            self.session_id = session_id
            conn.execute(
                media_library_assets.insert(),
                [
                    {
                        "asset_id": "landscape-1",
                        "session_id": session_id,
                        "display_name": "老板采访横屏",
                        "original_filename": "boss-interview.mp4",
                        "media_type": "video",
                        "duration_ms": 125_000,
                        "width": 1920,
                        "height": 1080,
                        "format": "mp4",
                        "language": "zh-CN",
                        "dialogue_summary": "介绍产品核心能力",
                        "analysis_status": "ready",
                        "subtitle_mode": "embedded",
                        "analysis_summary_json": {"dialogue_fragment_count": 12, "exclude_count": 2},
                        "tags_json": ["采访", "老板"],
                        "archived": False,
                        "referenced_by_count": 0,
                        "created_at": 1000,
                        "updated_at": 3000,
                    },
                    {
                        "asset_id": "portrait-1",
                        "session_id": None,
                        "display_name": "产品演示竖屏",
                        "original_filename": "demo.mov",
                        "media_type": "video",
                        "duration_ms": 45_000,
                        "width": 1080,
                        "height": 1920,
                        "format": "mov",
                        "language": None,
                        "dialogue_summary": None,
                        "analysis_status": "not_analyzed",
                        "subtitle_mode": "none",
                        "analysis_summary_json": None,
                        "tags_json": ["演示"],
                        "archived": False,
                        "referenced_by_count": 0,
                        "created_at": 1000,
                        "updated_at": 2000,
                    },
                ],
            )
        repo = MediaLibraryRepository(self.engine)
        task_repo = MediaLibraryTaskRepository(self.engine)
        task_repo.create_for_asset(asset_id="landscape-1", session_id=session_id, title="老板采访横屏", created_at=1000)
        self.app = FastAPI()
        self.app.include_router(
            build_media_library_router(
                SimpleNamespace(
                    engine=self.engine,
                    media_library_repo=repo,
                    media_library_task_repo=task_repo,
                    media_analysis_run_repo=AnalysisRunRepository(self.engine),
                    session_repo=SessionRepository(self.engine),
                )
            )
        )
        self.app.add_middleware(MediaLibraryPatchBodyLimitMiddleware)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_list_supports_search_orientation_and_summary_payload(self) -> None:
        status, payload = request(self.app, "GET", "/api/media-library?q=boss&orientation=landscape")

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["total"], 1)
        item = payload["items"][0]
        self.assertEqual(item["asset_id"], "landscape-1")
        self.assertEqual((item["width"], item["height"]), (1920, 1080))
        self.assertEqual(item["analysis_summary"]["dialogue_fragment_count"], 12)
        self.assertEqual(item["analysis_summary"]["exclude_count"], 2)
        self.assertEqual(payload["facets"]["tags"], ["演示", "老板", "采访"])

    def test_no_audio_projection_is_sanitized_and_not_mislabeled_as_consent(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "landscape-1")
                .values(analysis_status="blocked")
            )
            conn.execute(
                media_library_tasks.update()
                .where(media_library_tasks.c.asset_id == "landscape-1")
                .values(
                    dialogue_status="blocked",
                    dialogue_error=(
                        "video_has_no_audio: Video metadata says the source video has no audio track."
                    ),
                )
            )

        list_status, listed = request(self.app, "GET", "/api/media-library?q=boss")
        detail_status, detail = request(self.app, "GET", "/api/media-library/landscape-1")

        self.assertEqual(list_status, 200, listed)
        self.assertEqual(
            listed["items"][0]["analysis_status_reason"],
            "video_has_no_audio",
        )
        self.assertEqual(detail_status, 200, detail)
        open_cut = detail["item"]["open_cut"]
        self.assertEqual(open_cut["dialogue_error_code"], "video_has_no_audio")
        self.assertIn("源视频没有音轨", open_cut["dialogue_error"])
        self.assertNotIn("video_has_no_audio", open_cut["dialogue_error"])
        self.assertNotIn("Video metadata says", open_cut["dialogue_error"])

    def test_visual_search_projection_distinguishes_v2_ready_from_legacy_v1(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "landscape-1")
                .values(content_sha256="a" * 64)
            )
            conn.execute(
                media_library_analysis_runs.insert().values(
                    analysis_run_id="mlar_surface_visual_v2",
                    asset_id="landscape-1",
                    scheme="visual_semantic",
                    source_version="a" * 64,
                    status="ready",
                    schema_version="media_library_visual_semantic_v2",
                    result_hash="b" * 64,
                    is_current=True,
                    created_at=3200,
                    updated_at=3200,
                )
            )
            conn.execute(
                media_library_tasks.update()
                .where(media_library_tasks.c.asset_id == "landscape-1")
                .values(
                    visual_structure_status="ready",
                    visual_semantic_status="ready",
                    visual_semantic_current_run_id="mlar_surface_visual_v2",
                    visual_status="ready",
                )
            )
            conn.execute(
                media_library_fragment_index.insert().values(
                    asset_id="landscape-1",
                    source_session_id=self.session_id,
                    source_version="a" * 64,
                    analysis_scheme="visual_semantic",
                    analysis_run_id="mlar_surface_visual_v2",
                    result_hash="b" * 64,
                    fragment_id="scene_0001",
                    start_ms=0,
                    end_ms=4000,
                    summary="玻璃碗和绿色包装",
                    keywords_json=["玻璃碗"],
                    visual_labels_json=["绿色包装"],
                    keyframe_ref_json=[
                        f"scene_0001-sample-{index:02d}"
                        for index in range(1, 5)
                    ],
                    search_text="玻璃碗 绿色包装",
                    tokenizer_name="none",
                    tokenizer_version="none",
                    normalization_version="nfkc_casefold_ws_v1",
                    quality_status="ready",
                    is_active=True,
                    created_at=3200,
                    updated_at=3200,
                )
            )
        with patch.dict(
            "os.environ",
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "1"},
        ):
            status, payload = request(
                self.app, "GET", "/api/media-library?q=boss"
            )
            detail_status, detail = request(
                self.app, "GET", "/api/media-library/landscape-1"
            )
        self.assertEqual(status, 200, payload)
        item = payload["items"][0]
        self.assertTrue(item["visual_search_ready"])
        self.assertFalse(item["visual_search_reanalysis_required"])
        self.assertEqual(item["visual_search_state"], "ready")
        self.assertEqual(item["visual_search_fragment_count"], 1)
        self.assertEqual(detail_status, 200, detail)
        self.assertTrue(detail["item"]["visual_search_ready"])

        with self.engine.begin() as conn:
            conn.execute(
                media_library_analysis_runs.update()
                .where(
                    media_library_analysis_runs.c.analysis_run_id
                    == "mlar_surface_visual_v2"
                )
                .values(schema_version="media_library_visual_semantic_v1")
            )
        with patch.dict(
            "os.environ",
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "1"},
        ):
            status, payload = request(
                self.app, "GET", "/api/media-library?q=boss"
            )
        self.assertEqual(status, 200, payload)
        item = payload["items"][0]
        self.assertFalse(item["visual_search_ready"])
        self.assertTrue(item["visual_search_reanalysis_required"])
        self.assertEqual(item["visual_search_state"], "reanalysis_required")

    def test_list_aggregates_quality_only_from_active_fragments(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_analysis_runs.insert().values(
                    analysis_run_id="mlar_surface_quality",
                    asset_id="landscape-1",
                    scheme="dialogue",
                    source_version="a" * 64,
                    status="ready",
                    is_current=True,
                    created_at=3100,
                    updated_at=3100,
                )
            )
            base = {
                "asset_id": "landscape-1",
                "source_session_id": self.session_id,
                "source_version": "a" * 64,
                "analysis_scheme": "dialogue",
                "analysis_run_id": "mlar_surface_quality",
                "result_hash": "b" * 64,
                "start_ms": 0,
                "end_ms": 1000,
                "keywords_json": [],
                "visual_labels_json": [],
                "search_text": "quality",
                "tokenizer_name": "none",
                "tokenizer_version": "none",
                "normalization_version": "nfkc_casefold_ws_v1",
                "created_at": 3100,
                "updated_at": 3100,
            }
            conn.execute(
                media_library_fragment_index.insert(),
                [
                    {
                        **base,
                        "fragment_id": "ready-active",
                        "quality_status": "ready",
                        "is_active": True,
                    },
                    {
                        **base,
                        "fragment_id": "review-active",
                        "start_ms": 1000,
                        "end_ms": 2000,
                        "quality_status": "review",
                        "is_active": True,
                    },
                    {
                        **base,
                        "fragment_id": "old-inactive",
                        "start_ms": 2000,
                        "end_ms": 3000,
                        "quality_status": "ready",
                        "is_active": False,
                    },
                ],
            )

        status, payload = request(self.app, "GET", "/api/media-library?q=boss")

        self.assertEqual(status, 200, payload)
        summary = payload["items"][0]["analysis_summary"]
        self.assertEqual(summary["dialogue_fragment_count"], 12)
        self.assertEqual(
            {
                key: summary[key]
                for key in ("keep_count", "review_count", "exclude_count")
            },
            {"keep_count": 1, "review_count": 1, "exclude_count": 2},
        )

    def test_summary_updates_do_not_overwrite_derived_business_status(self) -> None:
        repo = MediaLibraryRepository(self.engine)
        with self.engine.begin() as conn:
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "landscape-1")
                .values(analysis_status="blocked")
            )

        repo.update_dialogue_analysis(
            "landscape-1",
            status=None,
            updated_at=4000,
            fragment_count=12,
            subtitle_mode="embedded",
            dialogue_summary="对白摘要",
        )
        repo.update_visual_analysis(
            "landscape-1",
            status=None,
            updated_at=4001,
            fragment_count=4,
        )

        asset = repo.get("landscape-1")
        self.assertEqual(asset["analysis_status"], "blocked")
        self.assertEqual(asset["analysis_summary_json"]["dialogue_fragment_count"], 12)
        self.assertEqual(asset["analysis_summary_json"]["visual_fragment_count"], 4)

    def test_processing_filter_covers_queued_running_and_legacy_processing(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "portrait-1")
                .values(analysis_status="queued")
            )

        status, payload = request(
            self.app,
            "GET",
            "/api/media-library?analysis_status=processing",
        )

        self.assertEqual(status, 200, payload)
        self.assertEqual(
            [item["asset_id"] for item in payload["items"]],
            ["portrait-1"],
        )

    def test_null_thumbnail_columns_fall_back_to_lazy_cache_routes(self) -> None:
        item = _serialize(
            {
                "asset_id": "lazy-thumbnail",
                "session_id": 364,
                "source_video_path": "inbox/老板 采访.mp4",
                "thumbnail_url": None,
                "preview_url": None,
            }
        )

        self.assertEqual(
            item["thumbnail_url"],
            "/api/session-tasks/364/thumbnail/inbox/%E8%80%81%E6%9D%BF%20%E9%87%87%E8%AE%BF.mp4",
        )
        self.assertEqual(
            item["preview_url"],
            "/api/session-tasks/364/raw/inbox/%E8%80%81%E6%9D%BF%20%E9%87%87%E8%AE%BF.mp4",
        )

    def test_detail_metadata_update_archive_restore_and_delete(self) -> None:
        detail_status, detail = request(self.app, "GET", "/api/media-library/portrait-1")
        self.assertEqual(detail_status, 200, detail)
        self.assertEqual((detail["item"]["width"], detail["item"]["height"]), (1080, 1920))

        patch_status, patched = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"display_name": "竖屏产品演示", "tags": ["竖屏", "竖屏", "演示"]},
        )
        self.assertEqual(patch_status, 200, patched)
        self.assertEqual(patched["item"]["display_name"], "竖屏产品演示")
        self.assertEqual(patched["item"]["tags"], ["竖屏", "演示"])

        archive_status, archived = request(self.app, "POST", "/api/media-library/portrait-1/archive")
        self.assertEqual(archive_status, 200, archived)
        self.assertTrue(archived["item"]["archived"])
        list_status, listed = request(self.app, "GET", "/api/media-library")
        self.assertEqual(list_status, 200, listed)
        self.assertEqual(listed["total"], 1)

        restore_status, restored = request(self.app, "POST", "/api/media-library/portrait-1/restore")
        self.assertEqual(restore_status, 200, restored)
        self.assertFalse(restored["item"]["archived"])

        delete_status, deleted = request(self.app, "DELETE", "/api/media-library/portrait-1")
        self.assertEqual(delete_status, 200, deleted)
        missing_status, _ = request(self.app, "GET", "/api/media-library/portrait-1")
        self.assertEqual(missing_status, 404)

    def test_tag_patch_normalizes_and_refreshes_search_and_facets(self) -> None:
        status, patched = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": ["  横屏  ", "访谈", "横屏"]},
        )
        self.assertEqual(status, 200, patched)
        self.assertEqual(patched["item"]["tags"], ["横屏", "访谈"])

        search_status, searched = request(
            self.app,
            "GET",
            "/api/media-library?tag=%E8%AE%BF%E8%B0%88",
        )
        self.assertEqual(search_status, 200, searched)
        self.assertEqual(
            [item["asset_id"] for item in searched["items"]],
            ["portrait-1"],
        )
        self.assertIn("访谈", searched["facets"]["tags"])

        clear_status, cleared = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": []},
        )
        self.assertEqual(clear_status, 200, cleared)
        self.assertEqual(cleared["item"]["tags"], [])

    def test_tag_patch_returns_stable_business_errors(self) -> None:
        cases = [
            (
                [f"tag-{index}" for index in range(21)],
                "media_library_tags_too_many",
            ),
            (["x" * 33], "media_library_tag_too_long"),
            (["   "], "media_library_tag_empty"),
        ]
        for tags, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                status, payload = request(
                    self.app,
                    "PATCH",
                    "/api/media-library/portrait-1",
                    {"tags": tags},
                )
                self.assertEqual(status, 422, payload)
                self.assertEqual(payload["detail"]["code"], expected_code)

    def test_legacy_tag_count_can_only_improve_until_within_limit(self) -> None:
        legacy = [f"legacy-{index}" for index in range(25)]
        with self.engine.begin() as conn:
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "portrait-1")
                .values(tags_json=legacy)
            )

        same_status, same = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": list(reversed(legacy))},
        )
        self.assertEqual(same_status, 200, same)
        self.assertEqual(len(same["item"]["tags"]), 25)

        increase_status, increase = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": legacy + ["legacy-25"]},
        )
        self.assertEqual(increase_status, 422, increase)
        self.assertEqual(
            increase["detail"]["code"],
            "media_library_tags_too_many",
        )

        for expected_count in (21, 20):
            status, payload = request(
                self.app,
                "PATCH",
                "/api/media-library/portrait-1",
                {"tags": legacy[:expected_count]},
            )
            self.assertEqual(status, 200, payload)
            self.assertEqual(len(payload["item"]["tags"]), expected_count)

        strict_status, strict = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": legacy[:21]},
        )
        self.assertEqual(strict_status, 422, strict)
        self.assertEqual(
            strict["detail"]["code"],
            "media_library_tags_too_many",
        )

    def test_unchanged_legacy_invalid_tags_are_multiset_exemptions(self) -> None:
        long_tag = "历史超长标签" * 7
        self.assertGreater(len(long_tag), 32)
        with self.engine.begin() as conn:
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "portrait-1")
                .values(tags_json=[long_tag, "", "保留"])
            )

        status, payload = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": ["保留", "", long_tag, "新增"]},
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["item"]["tags"], ["保留", "", long_tag, "新增"])

        copied_long_status, copied_long = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": [long_tag, long_tag]},
        )
        self.assertEqual(copied_long_status, 422, copied_long)
        self.assertEqual(
            copied_long["detail"]["code"],
            "media_library_tag_too_long",
        )

        copied_empty_status, copied_empty = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": ["", ""]},
        )
        self.assertEqual(copied_empty_status, 422, copied_empty)
        self.assertEqual(
            copied_empty["detail"]["code"],
            "media_library_tag_empty",
        )

        changed_long_status, changed_long = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": [long_tag + "改"]},
        )
        self.assertEqual(changed_long_status, 422, changed_long)
        self.assertEqual(
            changed_long["detail"]["code"],
            "media_library_tag_too_long",
        )

    def test_tag_structure_guard_and_body_byte_limit_are_distinct(self) -> None:
        business_status, business = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": [f"tag-{index}" for index in range(1000)]},
        )
        self.assertEqual(business_status, 422, business)
        self.assertEqual(
            business["detail"]["code"],
            "media_library_tags_too_many",
        )

        guard_status, guard = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": ["tag"] * 1001},
        )
        self.assertEqual(guard_status, 422, guard)
        self.assertIsInstance(guard["detail"], list)

        oversized = json.dumps(
            {"display_name": "x" * (64 * 1024)},
            ensure_ascii=False,
        ).encode("utf-8")
        body_status, body = raw_request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            oversized,
            chunk_size=1024,
        )
        self.assertEqual(body_status, 413, body)
        self.assertEqual(
            body["detail"]["code"],
            "media_library_patch_body_too_large",
        )

    def test_patch_targets_only_the_requested_asset(self) -> None:
        status, payload = request(
            self.app,
            "PATCH",
            "/api/media-library/portrait-1",
            {"tags": ["仅竖屏"]},
        )
        self.assertEqual(status, 200, payload)
        landscape_status, landscape = request(
            self.app,
            "GET",
            "/api/media-library/landscape-1",
        )
        self.assertEqual(landscape_status, 200, landscape)
        self.assertEqual(landscape["item"]["tags"], ["采访", "老板"])

    def test_detail_exposes_real_open_cut_task_and_independent_result_tabs(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_tasks.update()
                .where(media_library_tasks.c.asset_id == "landscape-1")
                .values(status="running")
            )
        detail_status, detail = request(self.app, "GET", "/api/media-library/landscape-1")

        self.assertEqual(detail_status, 200, detail)
        self.assertIsInstance(detail["item"]["open_cut"]["task_id"], int)
        self.assertEqual(detail["item"]["open_cut"]["session_id"], detail["item"]["session_id"])
        self.assertEqual(detail["item"]["open_cut"]["status"], "ready")
        self.assertEqual(detail["item"]["open_cut"]["dialogue_status"], "not_analyzed")
        self.assertEqual(set(detail["item"]["analysis_results"]), {"dialogue", "visual", "composite"})

    def test_dialogue_run_route_and_progress_contract(self) -> None:
        task = MediaLibraryTaskRepository(self.engine).get_by_asset("landscape-1")
        assert task is not None
        MediaLibraryTaskRepository(self.engine).update_dialogue_run(
            int(task["id"]),
            status="running",
            updated_at=4000,
            tool_use_session_id="tus_contract",
            progress={"step": "02_01", "label": "正在识别对白", "completed": 2, "total": 4},
            task_status="running",
        )
        detail_status, detail = request(self.app, "GET", "/api/media-library/landscape-1")
        self.assertEqual(detail_status, 200, detail)
        self.assertEqual(detail["item"]["open_cut"]["dialogue_status"], "running")
        self.assertEqual(detail["item"]["open_cut"]["dialogue_progress"]["label"], "正在识别对白")

        missing_status, missing = request(self.app, "POST", "/api/media-library/missing/analyses/dialogue/run", {"force": False})
        self.assertEqual(missing_status, 404, missing)

    def test_dialogue_run_route_forwards_explicit_cloud_asr_consent(self) -> None:
        queued = {"status": "queued", "task_id": 12, "session_id": 34}
        with patch(
            "opcrew_backend.routes.media_library.OpenCutDialogueService.start",
            return_value=queued,
        ) as start:
            status, payload = request(
                self.app,
                "POST",
                "/api/media-library/landscape-1/analyses/dialogue/run",
                {"force": True, "allow_cloud_asr_data_transfer": True},
            )

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload, queued)
        start.assert_called_once_with(
            "landscape-1",
            force=True,
            allow_cloud_asr_data_transfer=True,
        )

    def test_analysis_current_and_known_run_do_not_expose_history_or_activate(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "landscape-1")
                .values(content_sha256="a" * 64, content_hashed_at=3500)
            )
        run_repo = AnalysisRunRepository(self.engine)
        run = run_repo.create_queued(
            asset_id="landscape-1", scheme="dialogue", timestamp=3600
        )
        run_repo.activate_ready(
            run["analysis_run_id"],
            timestamp=3700,
            schema_version="media_library_dialogue_fragments_v1",
            result_hash="b" * 64,
            result_index_path="tool_use_sessions/missing/SessionOutput/json/dialogue_fragment_index.json",
        )

        current_status, current = request(
            self.app,
            "GET",
            "/api/media-library/landscape-1/analyses/dialogue/current",
        )
        known_status, known = request(
            self.app,
            "GET",
            f"/api/media-library/landscape-1/analyses/dialogue/runs/{run['analysis_run_id']}",
        )
        history_status, _ = request(
            self.app,
            "GET",
            "/api/media-library/landscape-1/analyses/dialogue/runs",
        )
        activate_status, _ = request(
            self.app,
            "POST",
            f"/api/media-library/landscape-1/analyses/dialogue/runs/{run['analysis_run_id']}/activate",
            {},
        )

        self.assertEqual(current_status, 200, current)
        self.assertEqual(
            current["run"]["analysis_run_id"], run["analysis_run_id"]
        )
        self.assertEqual(known_status, 200, known)
        self.assertNotIn("tool_use_session_id", known["run"])
        self.assertNotIn("model_config_id", known["run"])
        self.assertIn(history_status, {404, 405})
        self.assertIn(activate_status, {404, 405})

    def test_frontend_requires_and_sends_cloud_asr_consent(self) -> None:
        detail_page = (
            REPO_ROOT / "frontend" / "src" / "modules" / "mediaLibrary" / "pages" / "MediaLibraryDetailPage.jsx"
        ).read_text(encoding="utf-8")
        drawer = (
            REPO_ROOT / "frontend" / "src" / "modules" / "mediaLibrary" / "detail" / "MediaLibraryToolDrawer.jsx"
        ).read_text(encoding="utf-8")
        api_source = (REPO_ROOT / "frontend" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")

        self.assertIn("allow_cloud_asr_data_transfer: allowCloudAsrDataTransfer()", detail_page)
        self.assertIn("允许本次运行使用云端 ASR", drawer)
        self.assertIn("cloudAsrConsentMissing", drawer)
        self.assertIn("allow_cloud_asr_data_transfer?: boolean", api_source)

    def test_visual_run_state_and_route_contract(self) -> None:
        task = MediaLibraryTaskRepository(self.engine).get_by_asset("landscape-1")
        assert task is not None
        MediaLibraryTaskRepository(self.engine).update_visual_run(
            int(task["id"]),
            status="running",
            updated_at=5000,
            tool_use_session_id="tus_visual_contract",
            progress={"step": "03_01", "label": "正在检测镜头切换边界", "completed": 2, "total": 4},
            task_status="running",
        )
        detail_status, detail = request(self.app, "GET", "/api/media-library/landscape-1")
        self.assertEqual(detail_status, 200, detail)
        self.assertEqual(detail["item"]["open_cut"]["visual_status"], "running")
        self.assertEqual(detail["item"]["open_cut"]["visual_progress"]["label"], "正在检测镜头切换边界")

        missing_status, missing = request(self.app, "POST", "/api/media-library/missing/analyses/visual/run", {"force": False})
        self.assertEqual(missing_status, 404, missing)


if __name__ == "__main__":
    unittest.main()
