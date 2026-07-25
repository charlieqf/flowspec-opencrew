from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi import HTTPException
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
    sessions,
)
from opcrew_backend.media_library_analysis.contracts import (  # noqa: E402
    publish_dialogue_contract,
    publish_visual_structure_contract,
    result_hash,
)
from opcrew_backend.media_library_analysis.run_repository import (  # noqa: E402
    AnalysisRunRepository,
)


SOURCE_VERSION = "a" * 64


class MediaLibraryAnalysisRunsContractTest(unittest.TestCase):
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
                        title="analysis runs",
                        status="draft",
                        workspace_dir="/tmp/analysis-runs",
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id="asset-runs",
                    session_id=session_id,
                    display_name="analysis runs",
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
                    asset_id="asset-runs",
                    session_id=session_id,
                    title="analysis runs",
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
        self.repo = AnalysisRunRepository(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_startup_projection_reconciliation_repairs_retained_running_state(
        self,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_analysis_runs.insert().values(
                    analysis_run_id="mlar_composite_reconcile",
                    asset_id="asset-runs",
                    scheme="composite",
                    source_version=SOURCE_VERSION,
                    status="ready",
                    is_current=True,
                    progress_json={
                        "step": "completed",
                        "completed": 2,
                        "total": 2,
                    },
                    created_at=40,
                    updated_at=40,
                )
            )
            conn.execute(
                media_library_tasks.update()
                .where(media_library_tasks.c.asset_id == "asset-runs")
                .values(
                    status="running",
                    dialogue_status="ready",
                    visual_status="running",
                    visual_structure_status="ready",
                    visual_semantic_status="ready",
                    composite_status="ready",
                    composite_current_run_id="mlar_composite_reconcile",
                    composite_progress_json={
                        "step": "04_01",
                        "completed": 1,
                        "total": 2,
                    },
                )
            )
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "asset-runs")
                .values(analysis_status="running")
            )

        repaired = self.repo.reconcile_projections(timestamp=50)

        with self.engine.connect() as conn:
            task = conn.execute(select(media_library_tasks)).mappings().one()
            asset = conn.execute(select(media_library_assets)).mappings().one()
        self.assertEqual(repaired, 1)
        self.assertEqual(task["status"], "draft")
        self.assertEqual(task["visual_status"], "ready")
        self.assertEqual(asset["analysis_status"], "ready")
        self.assertEqual(
            task["composite_progress_json"],
            {"step": "completed", "completed": 2, "total": 2},
        )
        self.assertEqual(task["updated_at"], 50)
        self.assertEqual(asset["updated_at"], 50)

    def test_reconcile_preserves_stale_visual_content_for_silent_asset(
        self,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                media_library_tasks.update()
                .where(media_library_tasks.c.asset_id == "asset-runs")
                .values(
                    dialogue_status="blocked",
                    visual_status="not_analyzed",
                    visual_structure_status="stale",
                    visual_semantic_status="ready",
                    composite_status="not_analyzed",
                )
            )
            conn.execute(
                media_library_assets.update()
                .where(media_library_assets.c.asset_id == "asset-runs")
                .values(analysis_status="blocked")
            )

        self.repo.reconcile_projections(timestamp=60)

        with self.engine.connect() as conn:
            task = conn.execute(select(media_library_tasks)).mappings().one()
            asset = conn.execute(select(media_library_assets)).mappings().one()
        self.assertEqual(task["visual_status"], "stale")
        self.assertEqual(asset["analysis_status"], "partial")

    def test_current_switch_and_failed_retry_preserve_previous_ready(self) -> None:
        first = self.repo.create_queued(
            asset_id="asset-runs", scheme="dialogue", timestamp=100
        )
        with self.assertRaises(HTTPException) as active:
            self.repo.create_queued(
                asset_id="asset-runs", scheme="dialogue", timestamp=101
            )
        self.assertEqual(active.exception.detail["code"], "analysis_run_active")
        self.repo.mark_running(
            first["analysis_run_id"],
            timestamp=110,
            tool_use_session_id="tus-first",
        )
        self.repo.activate_ready(
            first["analysis_run_id"],
            timestamp=120,
            schema_version="media_library_dialogue_fragments_v1",
            result_hash="b" * 64,
            result_index_path="tool_use_sessions/tus-first/SessionOutput/json/dialogue_fragment_index.json",
        )

        retry = self.repo.create_queued(
            asset_id="asset-runs", scheme="dialogue", timestamp=130
        )
        self.repo.finish_unsuccessful(
            retry["analysis_run_id"],
            status="blocked",
            timestamp=140,
            error_code="cloud_asr_data_transfer_not_authorized",
            error={"user_message": "需要授权"},
        )

        current = self.repo.current("asset-runs", "dialogue")
        self.assertEqual(current["analysis_run_id"], first["analysis_run_id"])
        self.assertEqual(current["status"], "ready")
        with self.engine.connect() as conn:
            task = conn.execute(
                select(media_library_tasks).where(
                    media_library_tasks.c.asset_id == "asset-runs"
                )
            ).mappings().one()
        self.assertEqual(task["dialogue_status"], "ready")
        self.assertEqual(task["dialogue_current_run_id"], first["analysis_run_id"])
        self.assertIn("仍在使用上一次成功结果", task["dialogue_error"])
        self.assertIn("需要授权", task["dialogue_error"])

    def test_terminal_analysis_metrics_are_labeled_and_best_effort(self) -> None:
        metrics: list[tuple[str, int]] = []
        events: list[tuple[str, dict[str, Any]]] = []
        repository = AnalysisRunRepository(
            self.engine,
            metric_sink=lambda name, value: metrics.append((name, value)),
            event_sink=lambda kind, payload: events.append((kind, payload)),
        )
        blocked = repository.create_queued(
            asset_id="asset-runs",
            scheme="visual_semantic",
            timestamp=150,
        )
        repository.finish_unsuccessful(
            blocked["analysis_run_id"],
            status="blocked",
            timestamp=151,
            error_code="visual_model_not_configured",
            error={"user_message": "缺少视觉模型配置"},
        )
        self.assertIn(
            (
                'media_library_analysis_total'
                '{scheme="visual_semantic",status="blocked"}',
                1,
            ),
            metrics,
        )
        self.assertEqual(
            [kind for kind, _payload in events],
            [
                "media_library.analysis.run.created",
                "media_library.analysis.run.blocked",
            ],
        )
        self.assertEqual(
            events[-1][1],
            {
                "analysis_run_id": blocked["analysis_run_id"],
                "asset_id": "asset-runs",
                "scheme": "visual_semantic",
                "status": "blocked",
            },
        )

        def failed_metric_sink(_name: str, _value: int) -> None:
            raise RuntimeError("metric backend unavailable")

        repository = AnalysisRunRepository(
            self.engine,
            metric_sink=failed_metric_sink,
            event_sink=lambda _kind, _payload: (_ for _ in ()).throw(
                RuntimeError("event backend unavailable")
            ),
        )
        ready = repository.create_queued(
            asset_id="asset-runs",
            scheme="visual_structure",
            timestamp=160,
        )
        result = repository.activate_ready(
            ready["analysis_run_id"],
            timestamp=161,
            schema_version="media_library_visual_structure_v1",
            result_hash="9" * 64,
            result_index_path=(
                "tool_use_sessions/tus-visual/"
                "SessionOutput/visual/visual_structure_segments.json"
            ),
        )
        self.assertEqual(result["status"], "ready")

    def test_visual_structure_ready_derives_partial_until_semantic_ready(self) -> None:
        run = self.repo.create_queued(
            asset_id="asset-runs", scheme="visual_structure", timestamp=200
        )
        self.repo.activate_ready(
            run["analysis_run_id"],
            timestamp=220,
            schema_version="media_library_visual_structure_v1",
            result_hash="c" * 64,
            result_index_path="tool_use_sessions/tus-visual/SessionOutput/visual/visual_structure_segments.json",
        )

        with self.engine.connect() as conn:
            task = conn.execute(
                select(media_library_tasks).where(
                    media_library_tasks.c.asset_id == "asset-runs"
                )
            ).mappings().one()
        self.assertEqual(task["visual_structure_status"], "ready")
        self.assertEqual(task["visual_semantic_status"], "not_analyzed")
        self.assertEqual(task["visual_status"], "partial")

    def test_visual_structure_blocked_is_visible_on_task_and_asset(self) -> None:
        run = self.repo.create_queued(
            asset_id="asset-runs", scheme="visual_structure", timestamp=221
        )
        self.repo.finish_unsuccessful(
            run["analysis_run_id"],
            status="blocked",
            timestamp=222,
            error_code="analysis_blocked",
            error={"user_message": "画面结构工具等待授权"},
        )

        with self.engine.connect() as conn:
            task = conn.execute(select(media_library_tasks)).mappings().one()
            asset = conn.execute(select(media_library_assets)).mappings().one()
        self.assertEqual(task["visual_structure_status"], "blocked")
        self.assertEqual(task["visual_status"], "blocked")
        self.assertEqual(asset["analysis_status"], "blocked")

    def test_new_structure_atomically_stales_semantic_and_composite(self) -> None:
        structure = self.repo.create_queued(
            asset_id="asset-runs",
            scheme="visual_structure",
            timestamp=230,
        )
        self.repo.activate_ready(
            structure["analysis_run_id"],
            timestamp=231,
            schema_version="media_library_visual_structure_v1",
            result_hash="1" * 64,
            result_index_path="tool_use_sessions/tus-structure-1/SessionOutput/visual/visual_structure_segments.json",
        )
        semantic = self.repo.create_queued(
            asset_id="asset-runs",
            scheme="visual_semantic",
            timestamp=232,
        )
        self.repo.activate_ready(
            semantic["analysis_run_id"],
            timestamp=233,
            schema_version="media_library_visual_semantic_v1",
            result_hash="2" * 64,
            result_index_path="tool_use_sessions/tus-semantic-1/SessionOutput/visual/visual_semantic_segments.json",
        )
        composite = self.repo.create_queued(
            asset_id="asset-runs",
            scheme="composite",
            timestamp=234,
        )
        self.repo.activate_ready(
            composite["analysis_run_id"],
            timestamp=235,
            schema_version="media_library_composite_v1",
            result_hash="3" * 64,
            result_index_path="tool_use_sessions/tus-composite-1/SessionOutput/composite/composite_segments.json",
        )
        with self.engine.begin() as conn:
            session_id = int(
                conn.execute(
                    select(media_library_assets.c.session_id).where(
                        media_library_assets.c.asset_id == "asset-runs"
                    )
                ).scalar_one()
            )
            conn.execute(
                media_library_fragment_index.insert().values(
                    asset_id="asset-runs",
                    source_session_id=session_id,
                    source_version=SOURCE_VERSION,
                    analysis_scheme="composite",
                    analysis_run_id=composite["analysis_run_id"],
                    result_hash="3" * 64,
                    fragment_id="composite_0001",
                    start_ms=0,
                    end_ms=1000,
                    keywords_json=[],
                    visual_labels_json=[],
                    search_text="current composite",
                    tokenizer_name="none",
                    tokenizer_version="none",
                    normalization_version="nfkc_casefold_ws_v1",
                    quality_status="ready",
                    is_active=True,
                    created_at=235,
                    updated_at=235,
                )
            )

        replacement = self.repo.create_queued(
            asset_id="asset-runs",
            scheme="visual_structure",
            timestamp=236,
        )
        self.repo.activate_ready(
            replacement["analysis_run_id"],
            timestamp=237,
            schema_version="media_library_visual_structure_v1",
            result_hash="4" * 64,
            result_index_path="tool_use_sessions/tus-structure-2/SessionOutput/visual/visual_structure_segments.json",
        )

        stale_semantic = self.repo.current(
            "asset-runs", "visual_semantic"
        )
        stale_composite = self.repo.current("asset-runs", "composite")
        self.assertEqual(stale_semantic["status"], "stale")
        self.assertEqual(stale_composite["status"], "stale")
        self.assertEqual(
            stale_semantic["error_code"], "analysis_upstream_changed"
        )
        with self.engine.connect() as conn:
            task = conn.execute(
                select(media_library_tasks).where(
                    media_library_tasks.c.asset_id == "asset-runs"
                )
            ).mappings().one()
            active = conn.execute(
                select(media_library_fragment_index.c.is_active).where(
                    media_library_fragment_index.c.analysis_run_id
                    == composite["analysis_run_id"]
                )
            ).scalar_one()
        self.assertEqual(task["visual_status"], "stale")
        self.assertEqual(task["composite_status"], "stale")
        self.assertFalse(active)

        blocked_retry = self.repo.create_queued(
            asset_id="asset-runs",
            scheme="visual_semantic",
            timestamp=238,
        )
        self.repo.finish_unsuccessful(
            blocked_retry["analysis_run_id"],
            status="blocked",
            timestamp=239,
            error_code="cloud_visual_data_transfer_not_authorized",
            error={
                "code": "cloud_visual_data_transfer_not_authorized",
                "user_message": "需要显式授权",
            },
        )
        with self.engine.connect() as conn:
            task_after_block = conn.execute(
                select(media_library_tasks).where(
                    media_library_tasks.c.asset_id == "asset-runs"
                )
            ).mappings().one()
        self.assertEqual(
            self.repo.current("asset-runs", "visual_semantic")["status"],
            "stale",
        )
        self.assertEqual(
            task_after_block["visual_semantic_status"], "blocked"
        )
        self.assertEqual(task_after_block["visual_status"], "blocked")

    def test_model_session_is_bound_once_and_only_while_run_is_active(self) -> None:
        run = self.repo.create_queued(
            asset_id="asset-runs",
            scheme="visual_semantic",
            timestamp=180,
            model_config_id="model-config-alias-v1",
        )
        bound = self.repo.set_model_session(
            run["analysis_run_id"],
            model_session_id="opencode-model-session-1",
            timestamp=181,
        )
        self.assertEqual(
            bound["model_session_id"], "opencode-model-session-1"
        )
        with self.assertRaises(HTTPException) as conflict:
            self.repo.set_model_session(
                run["analysis_run_id"],
                model_session_id="opencode-model-session-2",
                timestamp=182,
            )
        self.assertEqual(
            conflict.exception.detail["code"],
            "analysis_model_session_conflict",
        )

    def test_business_run_status_domain_does_not_use_completed(self) -> None:
        statuses = {
            str(row[0])
            for row in self.engine.connect()
            .execute(select(media_library_analysis_runs.c.status))
            .fetchall()
        }
        self.assertNotIn("completed", statuses)
        self.assertNotIn("completed", {
            "queued", "running", "blocked", "ready", "stale", "failed"
        })

    def test_result_hash_is_stable_and_ignores_delivery_fields(self) -> None:
        left = {
            "schema_version": "v1",
            "items": [{"fragment_id": "x", "start_ms": 0, "end_ms": 1}],
            "preview_url": "/a",
            "created_at": 1,
        }
        right = {
            "created_at": 2,
            "preview_url": "/b",
            "items": [{"end_ms": 1, "start_ms": 0, "fragment_id": "x"}],
            "schema_version": "v1",
        }
        self.assertEqual(result_hash(left), result_hash(right))

    def test_dialogue_and_visual_structure_publish_integer_ms_and_image_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subtitle = root / "SessionOutput" / "subtitle"
            visual = root / "SessionOutput" / "visual"
            frames = visual / "scene_frames"
            subtitle.mkdir(parents=True)
            frames.mkdir(parents=True)
            (subtitle / "final_srt_frame_items.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "srt_id": "srt_0001",
                                "dialogue": "整数毫秒",
                                "start": 0.125,
                                "end": 1.375,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            for index in range(1, 5):
                (frames / f"scene_0001-sample-{index:02d}.jpg").write_bytes(
                    f"jpeg-{index}".encode()
                )
            (visual / "final_scene_frame_items.json").write_text(
                json.dumps(
                    {
                        "sampling_strategy": "scene_uniform_4_v1",
                        "items": [
                            {
                                "scene_id": "scene_0001",
                                "start": 0,
                                "end": 2,
                                "sampling_strategy": "scene_uniform_4_v1",
                                "keyframes": [
                                    {
                                        "keyframe_id": (
                                            f"scene_0001-sample-{index:02d}"
                                        ),
                                        "keyframe_time": time_value,
                                        "image_path": (
                                            "SessionOutput/visual/scene_frames/"
                                            f"scene_0001-sample-{index:02d}.jpg"
                                        ),
                                    }
                                    for index, time_value in enumerate(
                                        (0.25, 0.75, 1.25, 1.75),
                                        start=1,
                                    )
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            dialogue, dialogue_hash, _ = publish_dialogue_contract(
                tool_root=root,
                asset_id="asset-runs",
                source_version=SOURCE_VERSION,
                analysis_run_id="mlar_dialogue_contract",
                source_duration_ms=10_000,
            )
            visual_result, visual_hash, _ = publish_visual_structure_contract(
                tool_root=root,
                asset_id="asset-runs",
                source_version=SOURCE_VERSION,
                analysis_run_id="mlar_visual_structure_contract",
                source_duration_ms=10_000,
            )

        self.assertEqual(
            (dialogue["items"][0]["start_ms"], dialogue["items"][0]["end_ms"]),
            (125, 1375),
        )
        self.assertEqual(dialogue["items"][0]["duration_ms"], 1250)
        self.assertEqual(len(dialogue_hash), 64)
        scene = visual_result["items"][0]
        self.assertEqual(scene["sampling_strategy"], "scene_uniform_4_v1")
        self.assertEqual(scene["keyframes"][0]["keyframe_time_ms"], 250)
        self.assertEqual(len(scene["keyframes"]), 4)
        self.assertEqual(len(scene["keyframes"][0]["image_sha256"]), 64)
        self.assertEqual(len(visual_hash), 64)

    def test_contract_normalizes_tail_boundaries_and_skips_degenerate_items(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subtitle = root / "SessionOutput" / "subtitle"
            visual = root / "SessionOutput" / "visual"
            frame_root = visual / "scene_frames"
            subtitle.mkdir(parents=True)
            frame_root.mkdir(parents=True)
            for index in range(1, 5):
                (frame_root / f"scene_0001-sample-{index:02d}.jpg").write_bytes(
                    f"real-frame-{index}".encode()
                )
            (subtitle / "final_srt_frame_items.json").write_text(
                json.dumps(
                    {
                        "sampling_strategy": "scene_uniform_4_v1",
                        "items": [
                            {
                                "srt_id": "srt_tail",
                                "dialogue": "尾部对白",
                                "start": 9.5,
                                "end": 10.001,
                            },
                            {
                                "srt_id": "srt_degenerate",
                                "dialogue": "退化边界",
                                "start": 10,
                                "end": 10.001,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (visual / "final_scene_frame_items.json").write_text(
                json.dumps(
                    {
                        "sampling_strategy": "scene_uniform_4_v1",
                        "items": [
                            {
                                "scene_id": "scene_0001",
                                "start": 9,
                                "end": 10.001,
                                "sampling_strategy": "scene_uniform_4_v1",
                                "keyframes": [
                                    {
                                        "keyframe_id": (
                                            f"scene_0001-sample-{index:02d}"
                                        ),
                                        "keyframe_time": time_value,
                                        "image_path": (
                                            "SessionOutput/visual/scene_frames/"
                                            f"scene_0001-sample-{index:02d}.jpg"
                                        ),
                                    }
                                    for index, time_value in enumerate(
                                        (9.125, 9.375, 9.625, 9.875),
                                        start=1,
                                    )
                                ],
                            },
                            {
                                "scene_id": "scene_degenerate",
                                "start": 10,
                                "end": 10.001,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            dialogue, _dialogue_hash, _ = publish_dialogue_contract(
                tool_root=root,
                asset_id="asset-runs",
                source_version=SOURCE_VERSION,
                analysis_run_id="mlar_dialogue_tail",
                source_duration_ms=10_000,
            )
            structure, _structure_hash, _ = (
                publish_visual_structure_contract(
                    tool_root=root,
                    asset_id="asset-runs",
                    source_version=SOURCE_VERSION,
                    analysis_run_id="mlar_visual_tail",
                    source_duration_ms=10_000,
                )
            )

        self.assertEqual(len(dialogue["items"]), 1)
        self.assertEqual(dialogue["items"][0]["end_ms"], 10_000)
        self.assertEqual(dialogue["items"][0]["duration_ms"], 500)
        self.assertEqual(len(structure["items"]), 1)
        self.assertEqual(structure["items"][0]["end_ms"], 10_000)

    def test_dialogue_contract_accepts_valid_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subtitle = root / "SessionOutput" / "subtitle"
            subtitle.mkdir(parents=True)
            (subtitle / "final_srt_frame_items.json").write_text(
                json.dumps({"items": []}), encoding="utf-8"
            )

            payload, digest, relative_path = publish_dialogue_contract(
                tool_root=root,
                asset_id="asset-runs",
                source_version=SOURCE_VERSION,
                analysis_run_id="mlar_dialogue_empty",
                source_duration_ms=10_000,
            )
            manifest = json.loads(
                (
                    root
                    / "SessionOutput"
                    / "manifests"
                    / "dialogue_analysis_manifest.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(payload["items"], [])
        self.assertEqual(len(digest), 64)
        self.assertEqual(
            relative_path,
            "SessionOutput/json/dialogue_fragment_index.json",
        )
        self.assertEqual(manifest["fragment_count"], 0)

    def test_visual_structure_rejects_absolute_keyframe_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visual = root / "SessionOutput" / "visual"
            visual.mkdir(parents=True)
            (visual / "final_scene_frame_items.json").write_text(
                json.dumps(
                    {
                        "sampling_strategy": "scene_uniform_4_v1",
                        "items": [
                            {
                                "scene_id": "scene_0001",
                                "start": 0,
                                "end": 1,
                                "sampling_strategy": "scene_uniform_4_v1",
                                "keyframes": [
                                    {
                                        "keyframe_id": (
                                            f"scene_0001-sample-{index:02d}"
                                        ),
                                        "keyframe_time": time_value,
                                        "image_path": (
                                            "/tmp/outside.jpg"
                                            if index == 1
                                            else "SessionOutput/visual/"
                                            f"scene_frames/sample-{index:02d}.jpg"
                                        ),
                                    }
                                    for index, time_value in enumerate(
                                        (0.125, 0.375, 0.625, 0.875),
                                        start=1,
                                    )
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "keyframe_path_invalid"):
                publish_visual_structure_contract(
                    tool_root=root,
                    asset_id="asset-runs",
                    source_version=SOURCE_VERSION,
                    analysis_run_id="mlar_visual_structure_absolute",
                    source_duration_ms=10_000,
                )

    def test_contract_validation_dry_run_does_not_publish_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subtitle = root / "SessionOutput" / "subtitle"
            subtitle.mkdir(parents=True)
            (subtitle / "final_srt_frame_items.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "srt_id": "srt_0001",
                                "dialogue": "只校验，不发布",
                                "start": 0,
                                "end": 1,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload, digest, relative_path = publish_dialogue_contract(
                tool_root=root,
                asset_id="asset-runs",
                source_version=SOURCE_VERSION,
                analysis_run_id="mlar_dialogue_dry_run",
                source_duration_ms=10_000,
                write=False,
            )

            self.assertEqual(len(payload["items"]), 1)
            self.assertEqual(len(digest), 64)
            self.assertEqual(
                relative_path,
                "SessionOutput/json/dialogue_fragment_index.json",
            )
            self.assertFalse((root / relative_path).exists())
            self.assertFalse(
                (
                    root
                    / "SessionOutput"
                    / "manifests"
                    / "dialogue_analysis_manifest.json"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
