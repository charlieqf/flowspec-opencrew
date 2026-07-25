from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import patch

import anyio
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
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
    sessions,
)
from opcrew_backend.media_library_clips.router import (  # noqa: E402
    ClipJobCreateRequest,
    ClipSearchMetadataPatchRequest,
    build_media_library_clip_router,
)
from opcrew_backend.media_library_features import (  # noqa: E402
    MEDIA_LIBRARY_FEATURE_FLAGS,
    media_library_capabilities,
    media_library_feature_state,
    require_media_library_feature,
    strict_bool,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibraryFragmentPublisher,
    MediaLibrarySearchRequest,
    MediaLibrarySearchService,
)
from opcrew_backend.routes.media_library import (  # noqa: E402
    AnalysisRunRequest,
    CompositeAnalysisRunRequest,
    VisualAnalysisRunRequest,
    build_media_library_router,
)


SOURCE_VERSION = "a" * 64


@contextmanager
def feature_environment(
    values: dict[str, str] | None = None,
) -> Iterator[None]:
    names = tuple(MEDIA_LIBRARY_FEATURE_FLAGS.values())
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        for name, value in (values or {}).items():
            os.environ[name] = value
        yield
    finally:
        for name in names:
            os.environ.pop(name, None)
        for name, value in previous.items():
            if value is not None:
                os.environ[name] = value


def endpoint(router: Any, name: str) -> Any:
    return next(route.endpoint for route in router.routes if getattr(route, "name", "") == name)


def run_async(callback: Any, *args: Any) -> Any:
    async def invoke() -> Any:
        return await callback(*args)

    return anyio.run(invoke)


class _UnexpectedRepository:
    def get(self, _asset_id: str) -> dict[str, Any] | None:
        raise AssertionError("feature gate must run before asset lookup")


class MediaLibraryFeatureFlagsContractTest(unittest.TestCase):
    def test_strict_bool_tokens_defaults_and_public_capability_dto(self) -> None:
        for value in ("1", "true", "TRUE", " on ", "yes"):
            with self.subTest(value=value):
                self.assertTrue(strict_bool(value))
        for value in ("0", "false", "FALSE", " off ", "no"):
            with self.subTest(value=value):
                self.assertFalse(strict_bool(value))
        for value in ("", "enabled", "disabled", "2", "null"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "strict_boolean_invalid"):
                    strict_bool(value)

        with feature_environment():
            capabilities = media_library_capabilities().model_dump(mode="json")
        self.assertEqual(
            capabilities["schema_version"],
            "media_library_capabilities_v1",
        )
        self.assertEqual(
            set(capabilities["features"]),
            set(MEDIA_LIBRARY_FEATURE_FLAGS),
        )
        for feature, item in capabilities["features"].items():
            self.assertEqual(
                item,
                {
                    "enabled": True,
                    "configuration_valid": True,
                },
            )

    def test_each_flag_can_be_disabled_and_invalid_values_fail_closed(
        self,
    ) -> None:
        for feature, env_name in MEDIA_LIBRARY_FEATURE_FLAGS.items():
            with self.subTest(feature=feature, state="enabled"):
                with feature_environment({env_name: "on"}):
                    state = media_library_feature_state(feature)
                    self.assertTrue(state.enabled)
                    self.assertTrue(state.configuration_valid)
                    require_media_library_feature(feature)
            with self.subTest(feature=feature, state="disabled"):
                for value in ("0", "false", "off", "no"):
                    with feature_environment({env_name: value}):
                        state = media_library_feature_state(feature)
                        self.assertFalse(state.enabled)
                        self.assertTrue(state.configuration_valid)
                        with self.assertRaises(HTTPException) as raised:
                            require_media_library_feature(feature)
                        self.assertEqual(
                            raised.exception.detail["code"],
                            "feature_disabled",
                        )
                        self.assertEqual(raised.exception.detail["feature"], feature)
            with self.subTest(feature=feature, state="invalid"):
                with feature_environment({env_name: "maybe"}):
                    state = media_library_feature_state(feature)
                    self.assertFalse(state.enabled)
                    self.assertFalse(state.configuration_valid)
                    with self.assertRaises(HTTPException) as raised:
                        require_media_library_feature(feature)
                    self.assertEqual(
                        raised.exception.detail["code"],
                        "feature_flag_invalid",
                    )
                    self.assertEqual(raised.exception.detail["feature"], feature)
                    capability = media_library_capabilities().model_dump(mode="json")["features"][feature]
                    self.assertEqual(
                        capability,
                        {
                            "enabled": False,
                            "configuration_valid": False,
                        },
                    )
                    self.assertNotIn("maybe", str(capability))

    def test_capability_endpoint_and_analysis_editor_routes_are_gated(
        self,
    ) -> None:
        router = build_media_library_router(SimpleNamespace(media_library_repo=_UnexpectedRepository()))
        capability_endpoint = endpoint(router, "media_library_capability_status")
        capability_route = next(route for route in router.routes if getattr(route, "name", "") == "media_library_capability_status")
        self.assertEqual(capability_route.path, "/api/media-library/capabilities")
        self.assertEqual(capability_route.methods, {"GET"})
        editor_endpoint = endpoint(router, "asset_editor")
        dialogue_endpoint = endpoint(router, "run_dialogue_analysis")
        composite_endpoint = endpoint(router, "run_composite_analysis")

        with feature_environment({"OPENCREW_MEDIA_EDITOR_V1": "false"}):
            capability = run_async(capability_endpoint)
            self.assertFalse(capability.features["editor"].enabled)
            with self.assertRaises(HTTPException) as raised:
                run_async(
                    editor_endpoint,
                    "missing",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            self.assertEqual(
                raised.exception.detail,
                {
                    "code": "feature_disabled",
                    "feature": "editor",
                    "user_message": "该素材库功能当前未启用。",
                    "suggested_action": "请联系管理员启用对应功能后重试。",
                },
            )

        with feature_environment({"OPENCREW_MEDIA_ANALYSIS_RUNS_V1": "off"}):
            with self.assertRaises(HTTPException) as raised:
                run_async(
                    dialogue_endpoint,
                    "missing",
                    AnalysisRunRequest(),
                )
            self.assertEqual(raised.exception.detail["feature"], "analysis_runs")

        with feature_environment({"OPENCREW_MEDIA_COMPOSITE_V1": "0"}):
            with self.assertRaises(HTTPException) as raised:
                run_async(
                    composite_endpoint,
                    "missing",
                    CompositeAnalysisRunRequest(),
                )
            self.assertEqual(raised.exception.detail["feature"], "composite")

    def test_visual_structure_can_run_before_semantic_flag_is_enabled(
        self,
    ) -> None:
        structure: dict[str, Any] | None = None
        ctx = SimpleNamespace(
            media_library_repo=SimpleNamespace(get=lambda asset_id: {"asset_id": asset_id}),
            media_analysis_run_repo=SimpleNamespace(current=lambda _asset_id, _scheme: structure),
        )
        router = build_media_library_router(ctx)
        visual_endpoint = endpoint(router, "run_visual_analysis")
        structure_result = {"analysis_run_id": "mlar_structure"}

        for value in ("off", "invalid"):
            with self.subTest(semantic_flag=value):
                with (
                    feature_environment({"OPENCREW_MEDIA_VISUAL_SEMANTIC_V1": value}),
                    patch("opcrew_backend.routes.media_library.OpenCutVisualService") as structure_service,
                ):
                    structure_service.return_value.start.return_value = structure_result
                    result = run_async(
                        visual_endpoint,
                        "asset-visual",
                        VisualAnalysisRunRequest(),
                    )
                self.assertEqual(result, structure_result)
                structure_service.return_value.start.assert_called_once()
                self.assertFalse(structure_service.return_value.start.call_args.kwargs["continue_semantic"])

        structure = {
            "analysis_run_id": "mlar_structure",
            "status": "ready",
        }
        with (
            feature_environment({"OPENCREW_MEDIA_VISUAL_SEMANTIC_V1": "off"}),
            patch("opcrew_backend.routes.media_library.OpenCutVisualService") as structure_service,
        ):
            structure_service.return_value.start.return_value = structure_result
            result = run_async(
                visual_endpoint,
                "asset-visual",
                VisualAnalysisRunRequest(force_structure=True),
            )
        self.assertEqual(result, structure_result)
        self.assertFalse(structure_service.return_value.start.call_args.kwargs["continue_semantic"])

        with feature_environment({"OPENCREW_MEDIA_VISUAL_SEMANTIC_V1": "off"}):
            with self.assertRaises(HTTPException) as raised:
                run_async(
                    visual_endpoint,
                    "asset-visual",
                    VisualAnalysisRunRequest(),
                )
        self.assertEqual(
            raised.exception.detail,
            {
                "code": "feature_disabled",
                "feature": "visual_semantic",
                "user_message": "该素材库功能当前未启用。",
                "suggested_action": "请联系管理员启用对应功能后重试。",
            },
        )

    def test_search_and_clip_creation_are_gated_at_service_boundaries(
        self,
    ) -> None:
        search_service = MediaLibrarySearchService(repository=SimpleNamespace())
        request = MediaLibrarySearchRequest(
            query="防水",
            entry_point="storyboard",
            query_source="manual",
        )
        with feature_environment({"OPENCREW_MEDIA_LIBRARY_SEARCH_V1": "false"}):
            with self.assertRaises(HTTPException) as raised:
                run_async(search_service.plan, request)
            self.assertEqual(raised.exception.detail["code"], "feature_disabled")
            self.assertEqual(raised.exception.detail["feature"], "library_search")

        clip_router = build_media_library_clip_router(
            SimpleNamespace(
                media_library_repo=_UnexpectedRepository(),
                session_repo=SimpleNamespace(),
            ),
            manager=SimpleNamespace(),
        )
        create_clip = endpoint(clip_router, "create_clip_job")
        with feature_environment({"OPENCREW_MEDIA_EDITOR_V1": "off"}):
            with self.assertRaises(HTTPException) as raised:
                run_async(
                    create_clip,
                    "missing",
                    ClipJobCreateRequest(
                        source_version=SOURCE_VERSION,
                        start_ms=0,
                        end_ms=250,
                        display_name="clip",
                        idempotency_key="feature-gate-clip",
                    ),
                )
            self.assertEqual(raised.exception.detail["feature"], "editor")

        update_clip = endpoint(
            clip_router, "update_clip_search_metadata"
        )
        with feature_environment(
            {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "off"}
        ):
            with self.assertRaises(HTTPException) as raised:
                run_async(
                    update_clip,
                    "missing",
                    "missing",
                    ClipSearchMetadataPatchRequest(
                        search_eligible=True
                    ),
                )
        self.assertEqual(raised.exception.detail["code"], "feature_disabled")
        self.assertEqual(
            raised.exception.detail["feature"], "clip_search_v1"
        )

    def test_successful_clip_reads_remain_available_when_editor_disabled(
        self,
    ) -> None:
        clip = {
            "clip_id": "mlc_existing",
            "source_asset_id": "asset-existing",
        }
        manager = SimpleNamespace(
            list_clips=lambda asset_id: ([clip] if asset_id == "asset-existing" else []),
            get_clip=lambda asset_id, clip_id: (clip if (asset_id, clip_id) == ("asset-existing", "mlc_existing") else None),
        )
        router = build_media_library_clip_router(
            SimpleNamespace(
                media_library_repo=SimpleNamespace(get=lambda asset_id: ({"asset_id": asset_id, "session_id": 7})),
                session_repo=SimpleNamespace(
                    get=lambda session_id: (
                        {
                            "id": session_id,
                            "workspace_dir": "/tmp/existing",
                        }
                    )
                ),
            ),
            manager=manager,
        )
        with feature_environment({"OPENCREW_MEDIA_EDITOR_V1": "off"}):
            listed = run_async(endpoint(router, "list_clips"), "asset-existing")
            loaded = run_async(
                endpoint(router, "get_clip"),
                "asset-existing",
                "mlc_existing",
            )
        self.assertEqual(listed, {"items": [clip]})
        self.assertEqual(loaded, {"clip": clip})

    def test_disabling_analysis_publication_preserves_old_active_rows(
        self,
    ) -> None:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(engine)
        try:
            with engine.begin() as conn:
                session_id = int(
                    conn.execute(
                        sessions.insert()
                        .values(
                            source="open-cut-v1",
                            group_id="open-cut-v1",
                            title="feature rollback",
                            status="draft",
                            workspace_dir="/tmp/feature-rollback",
                            created_at=1,
                            updated_at=1,
                        )
                        .returning(sessions.c.id)
                    ).scalar_one()
                )
                conn.execute(
                    media_library_assets.insert().values(
                        asset_id="asset-feature-rollback",
                        session_id=session_id,
                        display_name="feature rollback",
                        original_filename="source.mp4",
                        source_video_path="inbox/source.mp4",
                        content_sha256=SOURCE_VERSION,
                        content_hashed_at=1,
                        media_type="video",
                        duration_ms=10_000,
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
                        asset_id="asset-feature-rollback",
                        session_id=session_id,
                        title="feature rollback",
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
                conn.execute(
                    media_library_analysis_runs.insert().values(
                        analysis_run_id="mlar_feature_old",
                        asset_id="asset-feature-rollback",
                        scheme="dialogue",
                        source_version=SOURCE_VERSION,
                        status="running",
                        progress_json={},
                        upstream_refs_json={},
                        is_current=False,
                        created_at=10,
                        updated_at=10,
                    )
                )

            publisher = MediaLibraryFragmentPublisher(engine)
            with feature_environment():
                publisher.publish_dialogue(
                    asset_id="asset-feature-rollback",
                    analysis_run_id="mlar_feature_old",
                    result_hash="b" * 64,
                    fragments=[
                        {
                            "fragment_id": "old-fragment",
                            "start_ms": 0,
                            "end_ms": 1000,
                            "duration_ms": 1000,
                            "dialogue_text": "旧索引继续可用",
                        }
                    ],
                    timestamp=30,
                )

            with engine.begin() as conn:
                conn.execute(
                    media_library_analysis_runs.insert().values(
                        analysis_run_id="mlar_feature_new",
                        asset_id="asset-feature-rollback",
                        scheme="dialogue",
                        source_version=SOURCE_VERSION,
                        status="running",
                        progress_json={},
                        upstream_refs_json={},
                        is_current=False,
                        created_at=20,
                        updated_at=20,
                    )
                )

            with feature_environment({"OPENCREW_MEDIA_ANALYSIS_RUNS_V1": "off"}):
                with self.assertRaises(HTTPException) as raised:
                    publisher.publish_dialogue(
                        asset_id="asset-feature-rollback",
                        analysis_run_id="mlar_feature_new",
                        result_hash="c" * 64,
                        fragments=[
                            {
                                "fragment_id": "new-fragment",
                                "start_ms": 1000,
                                "end_ms": 2000,
                                "duration_ms": 1000,
                                "dialogue_text": "不应发布",
                            }
                        ],
                        timestamp=40,
                    )
                self.assertEqual(raised.exception.detail["code"], "feature_disabled")

            with engine.connect() as conn:
                active = (
                    conn.execute(select(media_library_fragment_index.c.fragment_id).where(media_library_fragment_index.c.is_active.is_(True))).scalars().all()
                )
                new_count = int(
                    conn.execute(
                        select(func.count())
                        .select_from(media_library_fragment_index)
                        .where(media_library_fragment_index.c.analysis_run_id == "mlar_feature_new")
                    ).scalar_one()
                )
                runs = {
                    row.analysis_run_id: (row.status, row.is_current)
                    for row in conn.execute(
                        select(
                            media_library_analysis_runs.c.analysis_run_id,
                            media_library_analysis_runs.c.status,
                            media_library_analysis_runs.c.is_current,
                        )
                    )
                }
            self.assertEqual(active, ["old-fragment"])
            self.assertEqual(new_count, 0)
            self.assertEqual(runs["mlar_feature_old"], ("ready", True))
            self.assertEqual(runs["mlar_feature_new"], ("running", False))
        finally:
            engine.dispose()

    def test_historical_search_read_remains_available_when_disabled(
        self,
    ) -> None:
        repository = SimpleNamespace(
            get_search_run=lambda search_id: {
                "search_id": search_id,
                "query_plan_json": {
                    "retained_original_query": "private",
                    "planner_version": "deterministic_v1",
                },
                "status": "completed",
            }
        )
        service = MediaLibrarySearchService(repository=repository)
        with feature_environment({"OPENCREW_MEDIA_LIBRARY_SEARCH_V1": "off"}):
            run = service.get_run("mls_existing")
        self.assertEqual(run["search_id"], "mls_existing")
        self.assertNotIn("retained_original_query", run["query_plan_json"])


if __name__ == "__main__":
    unittest.main()
