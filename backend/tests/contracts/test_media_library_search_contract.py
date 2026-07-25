from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_clip_derivatives,
    media_library_fragment_index,
    media_library_search_runs,
    media_library_tasks,
    metadata,
    session_files,
    sessions,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibraryFragmentPublisher,
    MediaLibrarySearchPlanner,
    MediaLibrarySearchRepository,
    MediaLibrarySearchService,
)
from opcrew_backend.media_library_search.router import (  # noqa: E402
    _replay_response,
)


class MediaLibrarySearchContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def seed_asset(
        self,
        asset_id: str,
        *,
        title: str,
        fragments: list[str],
        timestamp: int,
        width: int = 1920,
        height: int = 1080,
        tags: list[str] | None = None,
    ) -> str:
        source_version = f"{timestamp % 16:x}" * 64
        run_id = f"mlar_dialogue_{asset_id}"
        result_hash = f"{(timestamp + 1) % 16:x}" * 64
        with self.engine.begin() as conn:
            session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="open-cut-v1",
                        group_id="open-cut-v1",
                        title=asset_id,
                        status="draft",
                        workspace_dir=f"/tmp/{asset_id}",
                        created_at=timestamp,
                        updated_at=timestamp,
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
                    content_hashed_at=timestamp,
                    media_type="video",
                    duration_ms=max(60_000, (len(fragments) + 1) * 1000),
                    width=width,
                    height=height,
                    upload_status="ready",
                    analysis_status="not_analyzed",
                    subtitle_mode="unknown",
                    tags_json=tags or [],
                    archived=False,
                    referenced_by_count=0,
                    created_at=timestamp,
                    updated_at=timestamp,
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
                    created_at=timestamp,
                    updated_at=timestamp,
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
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        MediaLibraryFragmentPublisher(self.engine).publish_dialogue(
            asset_id=asset_id,
            analysis_run_id=run_id,
            result_hash=result_hash,
            fragments=[
                {
                    "fragment_id": f"srt_{index:04d}",
                    "start_ms": (index - 1) * 1000,
                    "end_ms": index * 1000,
                    "dialogue_text": text,
                    "confidence": max(0.1, 1 - index * 0.05),
                }
                for index, text in enumerate(fragments, start=1)
            ],
            timestamp=timestamp + 1,
        )
        return run_id

    def service(self) -> MediaLibrarySearchService:
        def successful_planner(_payload):
            return {
                "schema_version": "media_library_query_plan_v1",
                "original_query": "ignored by authoritative request",
                "exact_phrases": ["防水能力"],
                "optional_terms": ["防护", "进水"],
                "negative_terms": ["虚假"],
                "orientation": "any",
                "min_duration_ms": None,
                "max_duration_ms": None,
                "sources": ["media_library"],
                "planner_version": "ml_query_planner_v1",
            }

        return MediaLibrarySearchService(
            self.engine,
            planner=MediaLibrarySearchPlanner(successful_planner),
        )

    def seed_visual_asset(
        self,
        asset_id: str,
        *,
        timestamp: int,
        summary: str = "玻璃碗中装有深色液体，放在绿色包装盒上。",
    ) -> tuple[str, str]:
        self.seed_asset(
            asset_id,
            title="无声产品素材",
            fragments=["不相关对白"],
            timestamp=timestamp,
        )
        structure_run_id = f"mlar_visual_structure_{asset_id}"
        semantic_run_id = f"mlar_visual_semantic_{asset_id}"
        structure_hash = f"{(timestamp + 2) % 16:x}" * 64
        source_version = f"{timestamp % 16:x}" * 64
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_tasks)
                .where(media_library_tasks.c.asset_id == asset_id)
                .values(
                    dialogue_status="blocked",
                    visual_structure_status="ready",
                    visual_semantic_status="running",
                    visual_status="running",
                )
            )
            conn.execute(
                media_library_analysis_runs.insert(),
                [
                    {
                        "analysis_run_id": structure_run_id,
                        "asset_id": asset_id,
                        "scheme": "visual_structure",
                        "source_version": source_version,
                        "status": "ready",
                        "schema_version": "media_library_visual_structure_v2",
                        "result_hash": structure_hash,
                        "progress_json": {},
                        "upstream_refs_json": {},
                        "is_current": True,
                        "created_at": timestamp + 2,
                        "updated_at": timestamp + 2,
                    },
                    {
                        "analysis_run_id": semantic_run_id,
                        "asset_id": asset_id,
                        "scheme": "visual_semantic",
                        "source_version": source_version,
                        "status": "running",
                        "schema_version": None,
                        "result_hash": None,
                        "progress_json": {},
                        "upstream_refs_json": {},
                        "is_current": False,
                        "created_at": timestamp + 3,
                        "updated_at": timestamp + 3,
                    },
                ],
            )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "1"},
        ):
            MediaLibraryFragmentPublisher(
                self.engine
            ).publish_visual_semantic(
                asset_id=asset_id,
                analysis_run_id=semantic_run_id,
                result_hash=f"{(timestamp + 4) % 16:x}" * 64,
                fragments=[
                    {
                        "fragment_id": "scene_0001",
                        "start_ms": 1500,
                        "end_ms": 9000,
                        "summary": summary,
                        "keywords": ["玻璃碗", "深色液体", "绿色包装"],
                        "visual_labels": [
                            "玻璃碗",
                            "深色液体",
                            "绿色包装盒",
                            "产品静物",
                        ],
                        "keyframe_ref": [
                            f"scene_0001-sample-{index:02d}"
                            for index in range(1, 5)
                        ],
                        "confidence": 0.91,
                    }
                ],
                timestamp=timestamp + 4,
                result_index_path=(
                    "tool_use_sessions/test/SessionOutput/visual/"
                    "visual_semantic_segments.json"
                ),
                visual_structure_run_id=structure_run_id,
                visual_structure_result_hash=structure_hash,
            )
        return structure_run_id, semantic_run_id

    def seed_clip(
        self,
        *,
        asset_id: str,
        clip_id: str,
        display_name: str,
        tags: list[str],
        timestamp: int,
        search_eligible: bool = True,
    ) -> None:
        with self.engine.begin() as conn:
            asset = conn.execute(
                select(media_library_assets).where(
                    media_library_assets.c.asset_id == asset_id
                )
            ).mappings().one()
            output_path = f"SessionOutput/clips/{clip_id}/clip.mp4"
            conn.execute(
                media_library_clip_derivatives.insert().values(
                    clip_id=clip_id,
                    idempotency_key=f"{clip_id}-idempotency",
                    source_asset_id=asset_id,
                    source_session_id=int(asset["session_id"]),
                    source_version=str(asset["content_sha256"]),
                    source_start_ms=13_000,
                    source_end_ms=17_240,
                    output_path=output_path,
                    display_name=display_name,
                    duration_ms=4_240,
                    content_sha256="e" * 64,
                    size_bytes=1_234,
                    operation="precise_reencode_v1",
                    search_eligible=search_eligible,
                    tags_json=tags,
                    search_text=" ".join(
                        [display_name.casefold(), *tags]
                    ),
                    search_enabled_at=(timestamp if search_eligible else None),
                    search_updated_at=timestamp,
                    created_at=timestamp,
                )
            )
            conn.execute(
                session_files.insert().values(
                    session_id=int(asset["session_id"]),
                    path=output_path,
                    kind="video",
                    size=1_234,
                    origin="media_library_clip",
                    downloadable=1,
                    visibility="internal",
                    sensitivity="normal",
                    stale=0,
                    updated_at=timestamp,
                )
            )

    def test_exact_phrase_optional_scoring_aggregation_and_stable_order(self) -> None:
        self.seed_asset(
            "asset-exact",
            title="户外测试",
            fragments=[
                "这一段完整介绍防水能力和进水保护",
                "防水能力通过长期测试",
                "防护表现可靠",
                "额外提到防水能力",
            ],
            timestamp=10,
        )
        self.seed_asset(
            "asset-optional",
            title="普通介绍",
            fragments=["产品具有防护设计", "能够避免进水"],
            timestamp=20,
        )
        response = self.service().search_sync(
            {
                "query": "防水能力",
                "entry_point": "storyboard",
                "query_source": "dialogue",
                "dialogue_asset_key": "dialogue-1",
                "target_task_id": 27,
                "orientation": "any",
                "sources": ["media_library"],
                "limit": 12,
            }
        )
        self.assertFalse(response.planner_degraded)
        self.assertEqual(
            [candidate.asset_id for candidate in response.items],
            ["asset-exact", "asset-optional"],
        )
        exact = response.items[0]
        self.assertEqual(len(exact.matched_fragments), 3)
        self.assertIn("完整原始查询命中对白", exact.score_reasons)
        self.assertTrue(
            any("对白短语命中" in reason for reason in exact.score_reasons)
        )
        self.assertTrue(
            any("规划关键词命中" in reason for reason in exact.score_reasons)
        )
        self.assertGreater(exact.raw_score, response.items[1].raw_score)
        self.assertTrue(
            all(
                isinstance(fragment.start_ms, int)
                and isinstance(fragment.end_ms, int)
                for candidate in response.items
                for fragment in candidate.matched_fragments
            )
        )
        self.assertEqual(
            exact.allowed_actions,
            ["preview", "open_editor", "import_original"],
        )
        self.assertEqual(
            exact.matched_fragments[0].run_id,
            exact.matched_fragments[0].analysis_run_id,
        )

    def test_qualification_excludes_archived_uploading_failed_and_version_mismatch(self) -> None:
        self.seed_asset(
            "asset-ready",
            title="ready",
            fragments=["防水能力"],
            timestamp=10,
        )
        for index, asset_id in enumerate(
            (
                "asset-archived",
                "asset-uploading",
                "asset-failed",
                "asset-version-mismatch",
            ),
            start=20,
        ):
            self.seed_asset(
                asset_id,
                title=asset_id,
                fragments=["防水能力"],
                timestamp=index,
            )
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == "asset-archived")
                .values(archived=True)
            )
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == "asset-uploading")
                .values(upload_status="uploading")
            )
            conn.execute(
                update(media_library_tasks)
                .where(media_library_tasks.c.asset_id == "asset-failed")
                .values(dialogue_status="failed")
            )
            conn.execute(
                update(media_library_assets)
                .where(
                    media_library_assets.c.asset_id == "asset-version-mismatch"
                )
                .values(content_sha256="f" * 64)
            )

        response = self.service().search_sync(
            {
                "query": "防水能力",
                "entry_point": "agent",
                "query_source": "manual",
            }
        )
        self.assertEqual(
            [candidate.asset_id for candidate in response.items],
            ["asset-ready"],
        )

    def test_search_is_global_and_does_not_filter_source_session(self) -> None:
        self.seed_asset(
            "asset-session-a",
            title="A",
            fragments=["防水能力"],
            timestamp=10,
        )
        self.seed_asset(
            "asset-session-b",
            title="B",
            fragments=["防水能力"],
            timestamp=20,
        )
        response = self.service().search_sync(
            {
                "query": "防水能力",
                "entry_point": "storyboard",
                "query_source": "dialogue",
                "target_task_id": 999,
                "dialogue_asset_key": "dialogue-other-session",
            }
        )
        self.assertEqual(
            {candidate.asset_id for candidate in response.items},
            {"asset-session-a", "asset-session-b"},
        )

    def test_repository_caps_300_fragments_and_three_per_asset(self) -> None:
        self.seed_asset(
            "asset-many",
            title="many",
            fragments=[f"防水能力 fragment {index}" for index in range(305)],
            timestamp=10,
        )
        plan = asyncio.run(
            MediaLibrarySearchPlanner(enabled=False).plan("防水能力")
        ).plan
        rows = MediaLibrarySearchRepository(self.engine).retrieve(plan)
        self.assertLessEqual(len(rows), 300)
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["asset_id"] for row in rows}, {"asset-many"})

    def test_planner_disabled_timeout_invalid_and_provider_error_fallback(self) -> None:
        async def timeout_planner(_payload):
            await asyncio.sleep(0.05)
            return {}

        planners = [
            MediaLibrarySearchPlanner(enabled=False),
            MediaLibrarySearchPlanner(
                timeout_planner, timeout_seconds=0.001
            ),
            MediaLibrarySearchPlanner(lambda _payload: {"invalid": True}),
            MediaLibrarySearchPlanner(
                lambda _payload: (_ for _ in ()).throw(
                    RuntimeError("quota_exceeded")
                )
            ),
        ]
        for planner in planners:
            with self.subTest(planner=planner):
                outcome = asyncio.run(planner.plan("防水能力"))
                self.assertTrue(outcome.degraded)
                self.assertEqual(outcome.plan.exact_phrases, ["防水能力"])
                self.assertEqual(outcome.plan.optional_terms, [])
                self.assertIsNotNone(outcome.error_code)

        with self.assertRaises(HTTPException) as too_short:
            asyncio.run(MediaLibrarySearchPlanner(enabled=False).plan("a"))
        self.assertEqual(
            too_short.exception.detail["code"], "search_query_too_short"
        )

    def test_negative_terms_and_explicit_orientation_remain_authoritative(self) -> None:
        self.seed_asset(
            "asset-landscape",
            title="landscape",
            fragments=["防水能力真实演示"],
            timestamp=10,
            width=1920,
            height=1080,
        )
        self.seed_asset(
            "asset-portrait-negative",
            title="portrait",
            fragments=["防水能力虚假演示"],
            timestamp=20,
            width=1080,
            height=1920,
        )
        response = self.service().search_sync(
            {
                "query": "防水能力",
                "entry_point": "editor",
                "query_source": "manual",
                "orientation": "landscape",
            }
        )
        self.assertEqual(
            [candidate.asset_id for candidate in response.items],
            ["asset-landscape"],
        )
        with self.engine.connect() as conn:
            run = conn.execute(
                select(media_library_analysis_runs).where(
                    media_library_analysis_runs.c.asset_id
                    == "asset-landscape"
                )
            ).mappings().one()
        self.assertEqual(run["status"], "ready")
        self.assertTrue(run["is_current"])

    def test_dialogue_confidence_breaks_otherwise_equal_scores(self) -> None:
        base = {
            "analysis_scheme": "dialogue",
            "dialogue_text": "化橘红产品介绍",
            "display_name": "产品素材",
            "tags_json": [],
            "width": 1920,
            "height": 1080,
        }
        low_score, low_reasons = MediaLibrarySearchRepository._score_fragment(
            {**base, "confidence": 0.1},
            original="化橘红",
            exact_phrases=[],
            optional_terms=[],
            requested_orientation="any",
        )
        high_score, high_reasons = MediaLibrarySearchRepository._score_fragment(
            {**base, "confidence": 0.9},
            original="化橘红",
            exact_phrases=[],
            optional_terms=[],
            requested_orientation="any",
        )
        self.assertAlmostEqual(high_score - low_score, 4.0)
        self.assertIn("对白置信度加分", low_reasons)
        self.assertIn("对白置信度加分", high_reasons)

    def test_editor_excludes_current_source_asset(self) -> None:
        self.seed_asset(
            "asset-editor-source",
            title="current",
            fragments=["防水能力"],
            timestamp=10,
        )
        self.seed_asset(
            "asset-editor-other",
            title="other",
            fragments=["防水能力"],
            timestamp=20,
        )
        response = self.service().search_sync(
            {
                "query": "防水能力",
                "entry_point": "editor",
                "query_source": "manual",
                "source_asset_id": "asset-editor-source",
            }
        )
        self.assertEqual(
            [candidate.asset_id for candidate in response.items],
            ["asset-editor-other"],
        )

    def test_visual_only_asset_is_r1_eligible_with_explicit_original_identity(self) -> None:
        self.seed_visual_asset("asset-visual-only", timestamp=40)
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "1"},
        ):
            response = MediaLibrarySearchService(self.engine).search_sync(
                {
                    "query": "无关长对白 玻璃碗",
                    "dialogue_query": "无关长对白",
                    "user_query": "玻璃碗",
                    "entry_point": "storyboard",
                    "query_source": "dialogue",
                }
            )
        self.assertEqual(len(response.items), 1)
        candidate = response.items[0]
        self.assertEqual(candidate.candidate_kind, "original_video")
        self.assertEqual(candidate.candidate_id, "asset-visual-only")
        self.assertEqual(candidate.asset_id, "asset-visual-only")
        self.assertEqual(candidate.source_asset_id, "asset-visual-only")
        self.assertIsNone(candidate.source_clip_id)
        self.assertEqual(candidate.content_sha256, candidate.source_version)
        self.assertEqual(
            candidate.allowed_actions,
            ["preview", "open_editor", "import_original"],
        )
        self.assertEqual(len(candidate.matched_fragments), 1)
        matched = candidate.matched_fragments[0]
        self.assertEqual(matched.scheme, "visual_semantic")
        self.assertEqual(matched.analysis_scheme, "visual_semantic")
        self.assertEqual((matched.start_ms, matched.end_ms), (1500, 9000))
        self.assertIn("玻璃碗", matched.summary or "")
        self.assertIn("视觉摘要完整短语命中", matched.score_reasons)
        self.assertTrue(
            any("视觉对象" in reason for reason in matched.score_reasons)
        )
        with self.engine.connect() as conn:
            snapshot = conn.execute(
                select(media_library_search_runs)
                .where(
                    media_library_search_runs.c.search_id
                    == response.search_id
                )
            ).mappings().one()["top_candidates_json"][0]
        self.assertEqual(snapshot["candidate_kind"], "original_video")
        self.assertEqual(snapshot["source_asset_id"], "asset-visual-only")
        self.assertIsNone(snapshot["source_clip_id"])
        self.assertEqual(snapshot["content_sha256"], candidate.source_version)

    def test_visual_search_flag_off_and_ineligible_v1_or_missing_frames_are_excluded(self) -> None:
        _structure_run_id, semantic_run_id = self.seed_visual_asset(
            "asset-visual-gated", timestamp=50
        )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "0"},
        ):
            response = MediaLibrarySearchService(self.engine).search_sync(
                {
                    "query": "玻璃碗",
                    "user_query": "玻璃碗",
                    "entry_point": "agent",
                }
            )
        self.assertEqual(response.items, [])
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.analysis_run_id
                    == semantic_run_id
                )
                .values(schema_version="media_library_visual_semantic_v1")
            )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "1"},
        ):
            response = MediaLibrarySearchService(self.engine).search_sync(
                {
                    "query": "玻璃碗",
                    "user_query": "玻璃碗",
                    "entry_point": "agent",
                }
            )
        self.assertEqual(response.items, [])
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.analysis_run_id
                    == semantic_run_id
                )
                .values(schema_version="media_library_visual_semantic_v2")
            )
            conn.execute(
                update(media_library_fragment_index)
                .where(
                    media_library_fragment_index.c.analysis_run_id
                    == semantic_run_id
                )
                .values(keyframe_ref_json=["scene_0001-sample-01"])
            )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "1"},
        ):
            response = MediaLibrarySearchService(self.engine).search_sync(
                {
                    "query": "玻璃碗",
                    "user_query": "玻璃碗",
                    "entry_point": "agent",
                }
            )
        self.assertEqual(response.items, [])

    def test_dialogue_and_visual_hits_aggregate_to_one_original_video_card(self) -> None:
        self.seed_visual_asset("asset-multi-scheme", timestamp=60)
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_tasks)
                .where(
                    media_library_tasks.c.asset_id == "asset-multi-scheme"
                )
                .values(dialogue_status="ready")
            )
            conn.execute(
                update(media_library_fragment_index)
                .where(
                    media_library_fragment_index.c.asset_id
                    == "asset-multi-scheme",
                    media_library_fragment_index.c.analysis_scheme
                    == "dialogue",
                )
                .values(
                    dialogue_text="玻璃碗产品对白",
                    search_text="玻璃碗产品对白",
                )
            )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "1"},
        ):
            response = MediaLibrarySearchService(self.engine).search_sync(
                {
                    "query": "玻璃碗",
                    "user_query": "玻璃碗",
                    "entry_point": "editor",
                }
            )
        self.assertEqual(len(response.items), 1)
        self.assertEqual(
            {item.scheme for item in response.items[0].matched_fragments},
            {"dialogue", "visual_semantic"},
        )

    def test_derived_clip_name_and_tag_recall_identity_ranking_and_snapshot(
        self,
    ) -> None:
        asset_id = "asset-derived-parent"
        clip_id = "mlc_0000000002000_aaaaaaaaaaaa"
        clip_name = "化橘红倒入玻璃碗中"
        self.seed_asset(
            asset_id,
            title="父原视频",
            fragments=[clip_name],
            timestamp=70,
            width=1080,
            height=1920,
        )
        self.seed_clip(
            asset_id=asset_id,
            clip_id=clip_id,
            display_name=clip_name,
            tags=["化橘红", "玻璃碗", "产品演示"],
            timestamp=75,
        )
        request = {
            "query": clip_name,
            "user_query": clip_name,
            "entry_point": "storyboard",
            "query_source": "manual",
        }
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "0"},
        ):
            response = MediaLibrarySearchService(self.engine).search_sync(
                request
            )
        self.assertEqual(
            [candidate.candidate_kind for candidate in response.items],
            ["original_video"],
        )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "1"},
        ):
            response = MediaLibrarySearchService(self.engine).search_sync(
                request
            )
        self.assertEqual(
            [candidate.candidate_kind for candidate in response.items],
            ["derived_clip", "original_video"],
        )
        candidate = response.items[0]
        self.assertEqual(candidate.candidate_id, clip_id)
        self.assertIsNone(candidate.asset_id)
        self.assertEqual(candidate.source_asset_id, asset_id)
        self.assertEqual(candidate.source_clip_id, clip_id)
        self.assertEqual(candidate.content_sha256, "e" * 64)
        self.assertEqual(candidate.duration_ms, 4_240)
        self.assertEqual(
            (candidate.candidate_start_ms, candidate.candidate_end_ms),
            (0, 4_240),
        )
        self.assertEqual(
            (candidate.source_start_ms, candidate.source_end_ms),
            (13_000, 17_240),
        )
        self.assertEqual(candidate.time_basis, "candidate")
        self.assertEqual(candidate.orientation, "portrait")
        self.assertEqual(candidate.matched_fragments, [])
        self.assertEqual(candidate.allowed_actions, ["preview", "import_clip"])
        self.assertIn("片段完整名称命中", candidate.score_reasons)
        self.assertTrue(
            candidate.preview_url.endswith(
                f"SessionOutput/clips/{clip_id}/clip.mp4"
            )
        )
        with self.engine.connect() as conn:
            snapshot = conn.execute(
                select(media_library_search_runs).where(
                    media_library_search_runs.c.search_id
                    == response.search_id
                )
            ).mappings().one()["top_candidates_json"][0]
        self.assertEqual(snapshot["candidate_kind"], "derived_clip")
        self.assertEqual(snapshot["candidate_id"], clip_id)
        self.assertEqual(snapshot["source_asset_id"], asset_id)
        self.assertEqual(snapshot["source_clip_id"], clip_id)
        self.assertEqual(snapshot["content_sha256"], "e" * 64)
        self.assertNotIn("preview_url", snapshot)
        service = MediaLibrarySearchService(self.engine)
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "1"},
        ):
            replay = _replay_response(
                SimpleNamespace(
                    engine=self.engine,
                    media_library_search_service=service,
                ),
                response.search_id,
                {"entry_point": "storyboard"},
            )
        replay_clip = next(
            item
            for item in replay["items"]
            if item["candidate_kind"] == "derived_clip"
        )
        self.assertEqual(replay_clip["candidate_id"], clip_id)
        self.assertEqual(replay_clip["candidate_start_ms"], 0)
        self.assertEqual(replay_clip["candidate_end_ms"], 4_240)

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_clip_derivatives)
                .where(media_library_clip_derivatives.c.clip_id == clip_id)
                .values(search_eligible=False)
            )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "1"},
        ):
            removed_replay = _replay_response(
                SimpleNamespace(
                    engine=self.engine,
                    media_library_search_service=service,
                ),
                response.search_id,
                {"entry_point": "storyboard"},
            )
        self.assertFalse(
            any(
                item["candidate_kind"] == "derived_clip"
                for item in removed_replay["items"]
            )
        )

    def test_planner_negative_terms_always_exclude_explicit_user_match(
        self,
    ) -> None:
        asset_id = "asset-derived-manual-override"
        clip_id = "mlc_0000000002002_cccccccccccc"
        exact_tag = "r2复用20260722-142253"
        self.seed_asset(
            asset_id,
            title="父素材",
            fragments=["不相关对白"],
            timestamp=78,
            tags=[exact_tag],
        )
        self.seed_clip(
            asset_id=asset_id,
            clip_id=clip_id,
            display_name="可复用片段",
            tags=[exact_tag],
            timestamp=79,
        )

        def planner(_payload):
            return {
                "schema_version": "media_library_query_plan_v1",
                "original_query": "ignored",
                "exact_phrases": ["不相关对白"],
                "optional_terms": [],
                "negative_terms": ["r2复用", "20260722-142253"],
                "orientation": "any",
                "sources": ["media_library"],
                "planner_version": "test_conflicting_planner_v1",
            }

        service = MediaLibrarySearchService(
            self.engine,
            planner=MediaLibrarySearchPlanner(planner),
        )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "1"},
        ):
            response = service.search_sync(
                {
                    "query": f"不相关对白 {exact_tag}",
                    "dialogue_query": "不相关对白",
                    "user_query": exact_tag,
                    "entry_point": "storyboard",
                    "query_source": "dialogue",
                }
            )
        self.assertEqual(response.items, [])

    def test_capacity_groups_fragment_counts_without_materializing_rows(
        self,
    ) -> None:
        _structure_run_id, semantic_run_id = self.seed_visual_asset(
            "asset-capacity-visual",
            timestamp=95,
        )
        statements: list[str] = []

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            capacity = MediaLibrarySearchRepository(self.engine).capacity()
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual(capacity["active_dialogue_fragments"], 0)
        self.assertEqual(capacity["active_visual_fragments"], 1)
        fragment_count_queries = [
            statement
            for statement in statements
            if "media_library_fragment_index" in statement
            and "count(" in statement.lower()
        ]
        self.assertEqual(len(fragment_count_queries), 1)
        self.assertIn("GROUP BY", fragment_count_queries[0].upper())
        self.assertRegex(
            fragment_count_queries[0],
            r"(?is)^SELECT\s+media_library_fragment_index\.analysis_scheme,\s+count\(",
        )

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_fragment_index)
                .where(
                    media_library_fragment_index.c.analysis_run_id
                    == semantic_run_id
                )
                .values(keyframe_ref_json=["scene_0001-sample-01"])
            )
        self.assertEqual(
            MediaLibrarySearchRepository(self.engine).capacity()[
                "active_visual_fragments"
            ],
            0,
        )

    def test_derived_clip_tag_recall_removal_archive_stale_and_editor_exclusion(
        self,
    ) -> None:
        asset_id = "asset-derived-lifecycle"
        clip_id = "mlc_0000000002001_bbbbbbbbbbbb"
        self.seed_asset(
            asset_id,
            title="父素材",
            fragments=["不相关对白"],
            timestamp=80,
        )
        self.seed_clip(
            asset_id=asset_id,
            clip_id=clip_id,
            display_name="产品展示核心片段",
            tags=["绿色包装"],
            timestamp=85,
        )

        def search(**extra):
            with patch.dict(
                os.environ,
                {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "1"},
            ):
                return MediaLibrarySearchService(self.engine).search_sync(
                    {
                        "query": "绿色包装",
                        "user_query": "绿色包装",
                        "entry_point": "editor",
                        **extra,
                    }
                )

        response = search()
        self.assertEqual(
            [(item.candidate_kind, item.candidate_id) for item in response.items],
            [("derived_clip", clip_id)],
        )
        self.assertIn("片段人工标签命中", response.items[0].score_reasons)
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "0"},
        ):
            self.assertEqual(
                MediaLibrarySearchRepository(self.engine).capacity()[
                    "search_eligible_clips"
                ],
                0,
            )
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "1"},
        ):
            self.assertEqual(
                MediaLibrarySearchRepository(self.engine).capacity()[
                    "search_eligible_clips"
                ],
                1,
            )
        self.assertEqual(search(source_asset_id=asset_id).items, [])

        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_clip_derivatives)
                .where(media_library_clip_derivatives.c.clip_id == clip_id)
                .values(search_eligible=False)
            )
        self.assertEqual(search().items, [])
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_clip_derivatives)
                .where(media_library_clip_derivatives.c.clip_id == clip_id)
                .values(search_eligible=True)
            )
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == asset_id)
                .values(archived=True)
            )
        self.assertEqual(search().items, [])
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == asset_id)
                .values(archived=False, content_sha256="f" * 64)
            )
        self.assertEqual(search().items, [])


if __name__ == "__main__":
    unittest.main()
