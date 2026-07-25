from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
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
    media_library_search_actions,
    media_library_search_runs,
    media_library_storyboard_imports,
    media_library_tasks,
    metadata,
    sessions,
)
from opcrew_backend.db.migrations import (  # noqa: E402
    MIGRATIONS,
    run_migrations,
    schema_migrations,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibraryFragmentPublisher,
)


SOURCE_VERSION = "a" * 64
FIRST_HASH = "b" * 64
SECOND_HASH = "c" * 64


class MediaLibraryFragmentPublisherContractTest(unittest.TestCase):
    def setUp(self) -> None:
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
                        title="fragment publisher",
                        status="draft",
                        workspace_dir="/tmp/fragment-publisher",
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id="asset-publisher",
                    session_id=self.session_id,
                    display_name="发布合同",
                    original_filename="source.mp4",
                    source_video_path="inbox/source.mp4",
                    content_sha256=SOURCE_VERSION,
                    content_hashed_at=1,
                    media_type="video",
                    duration_ms=10_000,
                    width=1920,
                    height=1080,
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
                    asset_id="asset-publisher",
                    session_id=self.session_id,
                    title="发布合同",
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

    def tearDown(self) -> None:
        self.engine.dispose()

    def create_run(
        self,
        run_id: str,
        timestamp: int,
        *,
        scheme: str = "dialogue",
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_analysis_runs.insert().values(
                    analysis_run_id=run_id,
                    asset_id="asset-publisher",
                    scheme=scheme,
                    source_version=SOURCE_VERSION,
                    status="running",
                    progress_json={},
                    upstream_refs_json={},
                    is_current=False,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            conn.execute(
                media_library_tasks.update()
                .where(media_library_tasks.c.asset_id == "asset-publisher")
                .values(
                    status="running",
                    **{f"{scheme}_status": "running"},
                    updated_at=timestamp,
                )
            )

    def fragments(self, label: str = "第一版") -> list[dict]:
        return [
            {
                "fragment_id": "srt_0001",
                "start_ms": 125,
                "end_ms": 1375,
                "duration_ms": 1250,
                "dialogue_text": f"{label} 对白",
                "keyframe_refs": ["srt_0001-keyframe"],
                "confidence": 0.8,
            },
            {
                "fragment_id": "srt_0002",
                "start_ms": 1500,
                "end_ms": 2600,
                "duration_ms": 1100,
                "dialogue_text": f"{label} 关键词",
                "keyframe_refs": [],
                "confidence": 0.6,
            },
        ]

    def prepare_visual_structure(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_analysis_runs.insert().values(
                    analysis_run_id="mlar_structure_current",
                    asset_id="asset-publisher",
                    scheme="visual_structure",
                    source_version=SOURCE_VERSION,
                    status="ready",
                    schema_version="media_library_visual_structure_v2",
                    result_hash="d" * 64,
                    result_index_path=(
                        "SessionOutput/visual/visual_structure_segments.json"
                    ),
                    progress_json={},
                    upstream_refs_json={},
                    is_current=True,
                    created_at=5,
                    updated_at=5,
                )
            )
            conn.execute(
                media_library_tasks.update()
                .where(
                    media_library_tasks.c.asset_id == "asset-publisher"
                )
                .values(
                    visual_status="partial",
                    visual_structure_status="ready",
                    visual_structure_current_run_id=(
                        "mlar_structure_current"
                    ),
                    updated_at=5,
                )
            )

    def visual_fragments(self, label: str = "玻璃碗") -> list[dict]:
        return [
            {
                "fragment_id": "scene_0001",
                "start_ms": 0,
                "end_ms": 3000,
                "dialogue_text": None,
                "title": None,
                "summary": f"{label}中有深色液体，旁边是绿色包装。",
                "keywords": [label, "深色液体", "绿色包装"],
                "visual_labels": [label, "液体", "包装"],
                "keyframe_ref": [
                    f"scene_0001-sample-{index:02d}"
                    for index in range(1, 5)
                ],
                "confidence": 0.91,
                "needs_review": False,
            }
        ]

    def test_publish_is_atomic_idempotent_and_switches_current_active_set(self) -> None:
        events: list[tuple[str, dict]] = []
        metrics: list[tuple[str, int]] = []
        publisher = MediaLibraryFragmentPublisher(
            self.engine,
            event_sink=lambda kind, payload: events.append((kind, payload)),
            metric_sink=lambda name, value: metrics.append((name, value)),
        )
        self.create_run("mlar_dialogue_first", 10)
        first = publisher.publish_dialogue(
            asset_id="asset-publisher",
            analysis_run_id="mlar_dialogue_first",
            result_hash=FIRST_HASH,
            fragments=self.fragments(),
            timestamp=20,
            result_index_path="SessionOutput/json/dialogue_fragment_index.json",
            progress={"phase": "completed", "percent": 100},
        )
        repeated = publisher.publish_dialogue(
            asset_id="asset-publisher",
            analysis_run_id="mlar_dialogue_first",
            result_hash=FIRST_HASH,
            fragments=self.fragments(),
            timestamp=21,
            result_index_path="SessionOutput/json/dialogue_fragment_index.json",
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(repeated["idempotent"])

        self.create_run("mlar_dialogue_second", 30)
        publisher.publish_dialogue(
            asset_id="asset-publisher",
            analysis_run_id="mlar_dialogue_second",
            result_hash=SECOND_HASH,
            fragments=self.fragments("第二版"),
            timestamp=40,
        )
        with self.engine.connect() as conn:
            fragments = conn.execute(
                select(
                    media_library_fragment_index.c.analysis_run_id,
                    media_library_fragment_index.c.is_active,
                ).order_by(
                    media_library_fragment_index.c.analysis_run_id,
                    media_library_fragment_index.c.fragment_id,
                )
            ).all()
            runs = conn.execute(
                select(
                    media_library_analysis_runs.c.analysis_run_id,
                    media_library_analysis_runs.c.status,
                    media_library_analysis_runs.c.is_current,
                ).order_by(media_library_analysis_runs.c.analysis_run_id)
            ).all()
            task = conn.execute(select(media_library_tasks)).mappings().one()
        self.assertEqual(len(fragments), 4)
        self.assertTrue(
            all(not row.is_active for row in fragments if row.analysis_run_id.endswith("first"))
        )
        self.assertTrue(
            all(row.is_active for row in fragments if row.analysis_run_id.endswith("second"))
        )
        self.assertEqual(
            [(row.analysis_run_id, row.status, row.is_current) for row in runs],
            [
                ("mlar_dialogue_first", "ready", False),
                ("mlar_dialogue_second", "ready", True),
            ],
        )
        self.assertEqual(task["dialogue_status"], "ready")
        self.assertEqual(task["status"], "draft")
        self.assertEqual(
            task["dialogue_current_run_id"], "mlar_dialogue_second"
        )
        first_run = next(
            row for row in runs if row.analysis_run_id == "mlar_dialogue_first"
        )
        with self.engine.connect() as conn:
            first_progress = conn.execute(
                select(media_library_analysis_runs.c.progress_json).where(
                    media_library_analysis_runs.c.analysis_run_id
                    == first_run.analysis_run_id
                )
            ).scalar_one()
        self.assertEqual(
            first_progress, {"phase": "completed", "percent": 100}
        )
        self.assertEqual(
            [kind for kind, _payload in events],
            [
                "media_library.fragment_index.published",
                "media_library.analysis.run.ready",
                "media_library.fragment_index.published",
                "media_library.analysis.run.ready",
            ],
        )
        fragment_events = [
            payload
            for kind, payload in events
            if kind == "media_library.fragment_index.published"
        ]
        self.assertEqual(fragment_events[-1]["fragment_count"], 2)
        self.assertEqual(
            fragment_events[-1]["analysis_run_id"],
            "mlar_dialogue_second",
        )
        self.assertEqual(
            metrics,
            [
                (
                    'media_library_analysis_total'
                    '{scheme="dialogue",status="ready"}',
                    1,
                ),
                (
                    'media_library_analysis_total'
                    '{scheme="dialogue",status="ready"}',
                    1,
                ),
            ],
        )

    def test_composite_publication_recomputes_business_and_quality_projections(
        self,
    ) -> None:
        publisher = MediaLibraryFragmentPublisher(self.engine)
        self.create_run("mlar_dialogue_quality", 10)
        publisher.publish_dialogue(
            asset_id="asset-publisher",
            analysis_run_id="mlar_dialogue_quality",
            result_hash=FIRST_HASH,
            fragments=self.fragments(),
            timestamp=20,
        )
        with self.engine.begin() as conn:
            conn.execute(
                media_library_tasks.update()
                .where(media_library_tasks.c.asset_id == "asset-publisher")
                .values(
                    visual_status="ready",
                    visual_structure_status="ready",
                    visual_semantic_status="ready",
                    updated_at=25,
                )
            )
        self.create_run(
            "mlar_composite_quality",
            30,
            scheme="composite",
        )

        publisher.publish(
            asset_id="asset-publisher",
            analysis_run_id="mlar_composite_quality",
            analysis_scheme="composite",
            result_hash=SECOND_HASH,
            fragments=[
                {
                    "fragment_id": "composite_0001",
                    "start_ms": 100,
                    "end_ms": 2100,
                    "title": "待复核综合片段",
                    "quality_status": "review",
                }
            ],
            timestamp=40,
            schema_version="media_library_composite_fragments_v1",
            progress={"step": "completed", "completed": 2, "total": 2},
        )

        with self.engine.connect() as conn:
            task = conn.execute(select(media_library_tasks)).mappings().one()
            asset = conn.execute(select(media_library_assets)).mappings().one()
        self.assertEqual(task["dialogue_status"], "ready")
        self.assertEqual(task["visual_status"], "ready")
        self.assertEqual(task["composite_status"], "ready")
        self.assertEqual(task["status"], "draft")
        self.assertEqual(
            task["composite_progress_json"],
            {"step": "completed", "completed": 2, "total": 2},
        )
        self.assertEqual(asset["analysis_status"], "ready")
        self.assertEqual(
            {
                key: asset["analysis_summary_json"][key]
                for key in ("keep_count", "review_count")
            },
            {"keep_count": 2, "review_count": 1},
        )
        self.assertNotIn("exclude_count", asset["analysis_summary_json"])

    def test_empty_dialogue_publication_is_ready_idempotent_and_not_indexed(
        self,
    ) -> None:
        publisher = MediaLibraryFragmentPublisher(self.engine)
        self.create_run("mlar_dialogue_empty", 10)

        first = publisher.publish_dialogue(
            asset_id="asset-publisher",
            analysis_run_id="mlar_dialogue_empty",
            result_hash=FIRST_HASH,
            fragments=[],
            timestamp=20,
        )
        repeated = publisher.publish_dialogue(
            asset_id="asset-publisher",
            analysis_run_id="mlar_dialogue_empty",
            result_hash=FIRST_HASH,
            fragments=[],
            timestamp=21,
        )

        with self.engine.connect() as conn:
            run = conn.execute(
                select(media_library_analysis_runs).where(
                    media_library_analysis_runs.c.analysis_run_id
                    == "mlar_dialogue_empty"
                )
            ).mappings().one()
            task = conn.execute(select(media_library_tasks)).mappings().one()
            fragments = conn.execute(
                select(media_library_fragment_index).where(
                    media_library_fragment_index.c.analysis_run_id
                    == "mlar_dialogue_empty"
                )
            ).all()

        self.assertFalse(first["idempotent"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(first["fragment_count"], 0)
        self.assertEqual(run["status"], "ready")
        self.assertTrue(run["is_current"])
        self.assertEqual(task["dialogue_status"], "ready")
        self.assertEqual(fragments, [])

        with self.assertRaisesRegex(ValueError, "fragment_set_empty"):
            publisher.publish(
                asset_id="asset-publisher",
                analysis_run_id="mlar_dialogue_empty",
                analysis_scheme="composite",
                result_hash=SECOND_HASH,
                fragments=[],
                timestamp=30,
                schema_version="media_library_composite_fragments_v1",
            )

    def test_failure_after_old_deactivation_rolls_back_and_preserves_old_set(self) -> None:
        publisher = MediaLibraryFragmentPublisher(self.engine)
        self.create_run("mlar_dialogue_old", 10)
        publisher.publish_dialogue(
            asset_id="asset-publisher",
            analysis_run_id="mlar_dialogue_old",
            result_hash=FIRST_HASH,
            fragments=self.fragments(),
            timestamp=20,
        )
        self.create_run("mlar_dialogue_failed", 30)

        def fail_after_deactivate(_conn, phase: str) -> None:
            if phase == "old_deactivated":
                raise RuntimeError("injected_publish_failure")

        failing = MediaLibraryFragmentPublisher(
            self.engine, transaction_hook=fail_after_deactivate
        )
        with self.assertRaisesRegex(RuntimeError, "injected_publish_failure"):
            failing.publish_dialogue(
                asset_id="asset-publisher",
                analysis_run_id="mlar_dialogue_failed",
                result_hash=SECOND_HASH,
                fragments=self.fragments("失败新版"),
                timestamp=40,
            )

        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    media_library_fragment_index.c.analysis_run_id,
                    media_library_fragment_index.c.is_active,
                )
            ).all()
            failed_count = conn.execute(
                select(media_library_fragment_index).where(
                    media_library_fragment_index.c.analysis_run_id
                    == "mlar_dialogue_failed"
                )
            ).all()
        self.assertEqual(len(failed_count), 0)
        self.assertTrue(rows)
        self.assertTrue(all(row.is_active for row in rows))
        self.assertTrue(
            all(row.analysis_run_id == "mlar_dialogue_old" for row in rows)
        )

    def test_visual_semantic_publish_is_flagged_mapped_and_atomic(self) -> None:
        self.prepare_visual_structure()
        self.create_run(
            "mlar_visual_semantic_first",
            10,
            scheme="visual_semantic",
        )
        publisher = MediaLibraryFragmentPublisher(self.engine)
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "false"},
            clear=True,
        ):
            with self.assertRaises(HTTPException) as raised:
                publisher.publish_visual_semantic(
                    asset_id="asset-publisher",
                    analysis_run_id="mlar_visual_semantic_first",
                    result_hash=FIRST_HASH,
                    fragments=self.visual_fragments(),
                    timestamp=20,
                    result_index_path="SessionOutput/visual/semantic.json",
                    visual_structure_run_id="mlar_structure_current",
                    visual_structure_result_hash="d" * 64,
                )
        self.assertEqual(raised.exception.detail["code"], "feature_disabled")
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "true"},
            clear=False,
        ):
            first = publisher.publish_visual_semantic(
                asset_id="asset-publisher",
                analysis_run_id="mlar_visual_semantic_first",
                result_hash=FIRST_HASH,
                fragments=self.visual_fragments(),
                timestamp=20,
                result_index_path="SessionOutput/visual/semantic.json",
                visual_structure_run_id="mlar_structure_current",
                visual_structure_result_hash="d" * 64,
                progress={"step": "completed"},
            )
            repeated = publisher.publish_visual_semantic(
                asset_id="asset-publisher",
                analysis_run_id="mlar_visual_semantic_first",
                result_hash=FIRST_HASH,
                fragments=self.visual_fragments(),
                timestamp=21,
                result_index_path="SessionOutput/visual/semantic.json",
                visual_structure_run_id="mlar_structure_current",
                visual_structure_result_hash="d" * 64,
            )
        self.assertFalse(first["idempotent"])
        self.assertTrue(repeated["idempotent"])
        with self.engine.connect() as conn:
            fragment = conn.execute(
                select(media_library_fragment_index).where(
                    media_library_fragment_index.c.analysis_run_id
                    == "mlar_visual_semantic_first"
                )
            ).mappings().one()
            run = conn.execute(
                select(media_library_analysis_runs).where(
                    media_library_analysis_runs.c.analysis_run_id
                    == "mlar_visual_semantic_first"
                )
            ).mappings().one()
            task = conn.execute(select(media_library_tasks)).mappings().one()
        self.assertEqual(fragment["analysis_scheme"], "visual_semantic")
        self.assertIsNone(fragment["dialogue_text"])
        self.assertIsNone(fragment["title"])
        self.assertEqual(
            fragment["summary"],
            "玻璃碗中有深色液体，旁边是绿色包装。",
        )
        self.assertEqual(
            fragment["keyframe_ref_json"],
            [
                f"scene_0001-sample-{index:02d}"
                for index in range(1, 5)
            ],
        )
        for term in ("玻璃碗", "深色液体", "绿色包装"):
            self.assertIn(term, fragment["search_text"])
        self.assertTrue(fragment["is_active"])
        self.assertEqual(run["schema_version"], "media_library_visual_semantic_v2")
        self.assertEqual(
            run["upstream_refs_json"],
            {
                "visual_structure_run_id": "mlar_structure_current",
                "visual_structure_result_hash": "d" * 64,
            },
        )
        self.assertEqual(task["visual_semantic_status"], "ready")
        self.assertEqual(task["visual_status"], "ready")

    def test_visual_semantic_rejects_single_frame_and_preserves_old_index(
        self,
    ) -> None:
        self.prepare_visual_structure()
        publisher = MediaLibraryFragmentPublisher(self.engine)
        self.create_run(
            "mlar_visual_semantic_old",
            10,
            scheme="visual_semantic",
        )
        enabled = {
            "OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "true"
        }
        with patch.dict(os.environ, enabled, clear=False):
            publisher.publish_visual_semantic(
                asset_id="asset-publisher",
                analysis_run_id="mlar_visual_semantic_old",
                result_hash=FIRST_HASH,
                fragments=self.visual_fragments("旧玻璃碗"),
                timestamp=20,
                result_index_path="SessionOutput/visual/old.json",
                visual_structure_run_id="mlar_structure_current",
                visual_structure_result_hash="d" * 64,
            )
        self.create_run(
            "mlar_visual_semantic_bad",
            30,
            scheme="visual_semantic",
        )
        invalid = self.visual_fragments("不应发布")
        invalid[0]["keyframe_ref"] = ["scene_0001-sample-02"]
        with patch.dict(os.environ, enabled, clear=False):
            with self.assertRaisesRegex(
                ValueError, "visual_semantic_fragment_ineligible"
            ):
                publisher.publish_visual_semantic(
                    asset_id="asset-publisher",
                    analysis_run_id="mlar_visual_semantic_bad",
                    result_hash=SECOND_HASH,
                    fragments=invalid,
                    timestamp=40,
                    result_index_path="SessionOutput/visual/bad.json",
                    visual_structure_run_id="mlar_structure_current",
                    visual_structure_result_hash="d" * 64,
                )
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    media_library_fragment_index.c.analysis_run_id,
                    media_library_fragment_index.c.is_active,
                ).where(
                    media_library_fragment_index.c.analysis_scheme
                    == "visual_semantic"
                )
            ).all()
        self.assertEqual(
            [(row.analysis_run_id, row.is_active) for row in rows],
            [("mlar_visual_semantic_old", True)],
        )

    def test_new_visual_index_stales_dependent_composite_atomically(self) -> None:
        self.prepare_visual_structure()
        publisher = MediaLibraryFragmentPublisher(self.engine)
        enabled = {
            "OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "true"
        }
        self.create_run(
            "mlar_visual_semantic_v1",
            10,
            scheme="visual_semantic",
        )
        with patch.dict(os.environ, enabled, clear=False):
            publisher.publish_visual_semantic(
                asset_id="asset-publisher",
                analysis_run_id="mlar_visual_semantic_v1",
                result_hash=FIRST_HASH,
                fragments=self.visual_fragments("第一版玻璃碗"),
                timestamp=20,
                result_index_path="SessionOutput/visual/v1.json",
                visual_structure_run_id="mlar_structure_current",
                visual_structure_result_hash="d" * 64,
            )
        self.create_run(
            "mlar_composite_from_visual_v1", 30, scheme="composite"
        )
        publisher.publish(
            asset_id="asset-publisher",
            analysis_run_id="mlar_composite_from_visual_v1",
            analysis_scheme="composite",
            result_hash="e" * 64,
            fragments=[
                {
                    "fragment_id": "composite_0001",
                    "start_ms": 0,
                    "end_ms": 3000,
                    "summary": "依赖第一版画面语义",
                    "keyframe_ref": ["scene_0001-sample-02"],
                }
            ],
            timestamp=40,
            schema_version="media_library_composite_v1",
        )
        self.create_run(
            "mlar_visual_semantic_v2",
            50,
            scheme="visual_semantic",
        )
        with patch.dict(os.environ, enabled, clear=False):
            publisher.publish_visual_semantic(
                asset_id="asset-publisher",
                analysis_run_id="mlar_visual_semantic_v2",
                result_hash=SECOND_HASH,
                fragments=self.visual_fragments("第二版玻璃碗"),
                timestamp=60,
                result_index_path="SessionOutput/visual/v2.json",
                visual_structure_run_id="mlar_structure_current",
                visual_structure_result_hash="d" * 64,
            )
        with self.engine.connect() as conn:
            fragments = conn.execute(
                select(
                    media_library_fragment_index.c.analysis_run_id,
                    media_library_fragment_index.c.analysis_scheme,
                    media_library_fragment_index.c.is_active,
                ).where(
                    media_library_fragment_index.c.analysis_run_id.in_(
                        (
                            "mlar_visual_semantic_v1",
                            "mlar_visual_semantic_v2",
                            "mlar_composite_from_visual_v1",
                        )
                    )
                )
            ).all()
            composite = conn.execute(
                select(media_library_analysis_runs).where(
                    media_library_analysis_runs.c.analysis_run_id
                    == "mlar_composite_from_visual_v1"
                )
            ).mappings().one()
            task = conn.execute(select(media_library_tasks)).mappings().one()
        active_by_run = {
            row.analysis_run_id: row.is_active for row in fragments
        }
        self.assertFalse(active_by_run["mlar_visual_semantic_v1"])
        self.assertTrue(active_by_run["mlar_visual_semantic_v2"])
        self.assertFalse(active_by_run["mlar_composite_from_visual_v1"])
        self.assertEqual(composite["status"], "stale")
        self.assertEqual(
            composite["error_json"]["upstream_scheme"],
            "visual_semantic",
        )
        self.assertEqual(task["composite_status"], "stale")

    def test_fragment_contract_requires_integer_ms_dialogue_and_safe_refs(self) -> None:
        self.create_run("mlar_dialogue_validation", 10)
        publisher = MediaLibraryFragmentPublisher(self.engine)
        invalid_cases = [
            {
                "fragment_id": "float-time",
                "start_ms": 1.5,
                "end_ms": 2,
                "dialogue_text": "时间",
            },
            {
                "fragment_id": "empty-dialogue",
                "start_ms": 1,
                "end_ms": 2,
                "dialogue_text": " ",
            },
            {
                "fragment_id": "unsafe-ref",
                "start_ms": 1,
                "end_ms": 2,
                "dialogue_text": "引用",
                "keyframe_ref": "/private/tmp/secret.jpg",
            },
        ]
        for fragment in invalid_cases:
            with self.subTest(fragment=fragment["fragment_id"]):
                with self.assertRaises((ValueError, TypeError)):
                    publisher.publish_dialogue(
                        asset_id="asset-publisher",
                        analysis_run_id="mlar_dialogue_validation",
                        result_hash=FIRST_HASH,
                        fragments=[fragment],
                        timestamp=20,
                    )

    def test_0020_through_0024_schema_and_clip_constraints(self) -> None:
        inspector = inspect(self.engine)
        self.assertTrue(
            {
                "media_library_fragment_index",
                "media_library_search_runs",
                "media_library_search_actions",
                "media_library_clip_derivatives",
                "media_library_storyboard_imports",
            }.issubset(set(inspector.get_table_names()))
        )
        checks = {
            str(item.get("name")): str(item.get("sqltext"))
            for item in inspector.get_check_constraints(
                "media_library_clip_derivatives"
            )
        }
        self.assertNotIn("ck_media_library_clip_not_search_eligible", checks)
        self.assertIn("ck_media_library_clip_source_range", checks)
        self.assertIn("ck_media_library_clip_output_path", checks)
        clip_columns = {
            str(item.get("name"))
            for item in inspector.get_columns("media_library_clip_derivatives")
        }
        self.assertTrue(
            {
                "tags_json",
                "search_text",
                "search_normalization_version",
                "search_enabled_at",
                "search_updated_at",
            }.issubset(clip_columns)
        )

        base = {
            "clip_id": "clip-invalid",
            "idempotency_key": "clip-invalid-key",
            "source_asset_id": "asset-publisher",
            "source_session_id": self.session_id,
            "source_version": SOURCE_VERSION,
            "source_start_ms": 0,
            "source_end_ms": 250,
            "output_path": "SessionOutput/clips/clip.mp4",
            "display_name": "clip",
            "duration_ms": 250,
            "content_sha256": "d" * 64,
            "size_bytes": 1,
            "operation": "precise_reencode_v1",
            "search_eligible": False,
            "created_at": 1,
        }
        invalid_values = [
            {"source_start_ms": 250, "source_end_ms": 250},
            {"output_path": "../escape.mp4"},
        ]
        for index, overrides in enumerate(invalid_values):
            values = {
                **base,
                "clip_id": f"clip-invalid-{index}",
                "idempotency_key": f"clip-invalid-key-{index}",
                **overrides,
            }
            with self.subTest(overrides=overrides):
                with self.assertRaises(IntegrityError):
                    with self.engine.begin() as conn:
                        conn.execute(
                            media_library_clip_derivatives.insert().values(
                                **values
                            )
                        )

    def test_existing_0018_database_runs_0019_through_0022_in_order(self) -> None:
        with self.engine.begin() as conn:
            for table in (
                media_library_storyboard_imports,
                media_library_clip_derivatives,
                media_library_search_actions,
                media_library_search_runs,
                media_library_fragment_index,
                media_library_analysis_runs,
            ):
                table.drop(conn)
            schema_migrations.create(conn)
            for migration_id, description, _upgrade in MIGRATIONS:
                if migration_id == "0019_media_library_source_identity_and_analysis_runs":
                    break
                conn.execute(
                    schema_migrations.insert().values(
                        id=migration_id,
                        description=description,
                        applied_at=1,
                    )
                )

        run_migrations(self.engine)

        inspector = inspect(self.engine)
        self.assertTrue(
            {
                "media_library_analysis_runs",
                "media_library_fragment_index",
                "media_library_search_runs",
                "media_library_search_actions",
                "media_library_clip_derivatives",
                "media_library_storyboard_imports",
            }.issubset(set(inspector.get_table_names()))
        )
        with self.engine.connect() as conn:
            applied = [
                str(row[0])
                for row in conn.execute(
                    select(schema_migrations.c.id).order_by(
                        schema_migrations.c.id
                    )
                ).all()
            ]
        self.assertEqual(applied, [migration[0] for migration in MIGRATIONS])


if __name__ == "__main__":
    unittest.main()
