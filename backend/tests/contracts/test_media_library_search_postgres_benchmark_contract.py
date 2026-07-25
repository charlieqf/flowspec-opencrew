from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import metadata  # noqa: E402
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibrarySearchPlanner,
)
from scripts import (  # noqa: E402
    benchmark_media_library_search_postgres as benchmark,
)
from scripts.benchmark_media_library_search_postgres import (  # noqa: E402
    BENCHMARK_TABLES,
    MIN_MEASURED_ITERATIONS,
    MIN_WARMUP_ITERATIONS,
    P95_GATE_MS,
    PLANNER_VERSION,
    R2_CLIP_COUNT,
    VIDEO_COUNT,
    PrecachedBenchmarkPlanner,
    assert_representative_capacity,
    distribution_summary,
    isolated_schema_name,
    load_workspace_sample_distribution,
    nearest_rank_percentile,
    run_measured_searches,
    seed_representative_distribution,
)


class MediaLibrarySearchPostgresBenchmarkContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.sample_root = Path(self.temporary.name) / "sessions"
        counts = (1, 3, 5, 8, 13, 21)
        for session_id, count in enumerate(counts, start=1):
            path = (
                self.sample_root
                / str(session_id)
                / "workspace"
                / "SessionOutput"
                / "subtitle"
                / "final_srt_frame_items.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "srt_id": f"srt_{index + 1:04d}",
                                "dialogue": (
                                    "真实样本"
                                    + "长" * ((session_id * 7 + index) % 80)
                                ),
                                "start": index,
                                "end": index + 0.8,
                            }
                            for index in range(count)
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        self.sample = load_workspace_sample_distribution(
            self.sample_root
        )
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine, tables=list(BENCHMARK_TABLES))

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def test_seed_uses_audited_variable_real_sample_distribution(
        self,
    ) -> None:
        capacity = seed_representative_distribution(
            self.engine,
            sample_distribution=self.sample,
        )
        assert_representative_capacity(capacity)
        self.assertEqual(capacity["ready_original_video_count"], VIDEO_COUNT)
        self.assertNotEqual(
            capacity["fragment_count_per_video"]["minimum"],
            capacity["fragment_count_per_video"]["maximum"],
        )
        self.assertEqual(
            capacity["fragment_count_per_video"]["p50"],
            self.sample.audit["fragment_count_per_video"]["p50"],
        )
        self.assertEqual(
            capacity["fragment_count_per_video"]["p95"],
            self.sample.audit["fragment_count_per_video"]["p95"],
        )
        self.assertGreater(
            capacity["active_dialogue_fragment_count"], VIDEO_COUNT
        )
        self.assertEqual(
            capacity["active_visual_semantic_fragment_count"], 1_250
        )
        self.assertEqual(
            capacity["current_ready_visual_structure_run_count"],
            VIDEO_COUNT,
        )
        self.assertEqual(
            capacity["current_ready_visual_semantic_run_count"],
            VIDEO_COUNT,
        )
        self.assertEqual(
            capacity["visual_fragment_count_per_video"]["minimum"], 1
        )
        self.assertEqual(
            capacity["visual_fragment_count_per_video"]["maximum"], 4
        )
        self.assertEqual(capacity["duration_bucket_count"], 5)
        self.assertGreater(
            capacity["repeated_dialogue_fragment_count"], 0
        )
        seed = capacity["dataset_seed"]
        self.assertEqual(
            seed["source_sample"]["source_kind"],
            "workspace_analysis_results",
        )
        self.assertEqual(seed["source_sample"]["source_file_count"], 6)
        self.assertEqual(seed["source_sample"]["source_fragment_count"], 51)
        self.assertRegex(
            seed["source_sample"]["source_snapshot_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertNotIn(
            str(self.sample_root), json.dumps(seed, ensure_ascii=False)
        )
        self.assertFalse(hasattr(benchmark, "FRAGMENTS_PER_VIDEO"))

    def test_real_sample_snapshot_is_auditable_and_content_sensitive(
        self,
    ) -> None:
        first_digest = self.sample.audit["source_snapshot_sha256"]
        path = (
            self.sample_root
            / "1"
            / "workspace"
            / "SessionOutput"
            / "subtitle"
            / "final_srt_frame_items.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["items"][0]["dialogue"] += "内容变化"
        path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        changed = load_workspace_sample_distribution(self.sample_root)
        self.assertNotEqual(
            first_digest, changed.audit["source_snapshot_sha256"]
        )
        self.assertIn(
            "p50", changed.audit["fragment_count_per_video"]
        )
        self.assertIn("p95", changed.audit["text_length_chars"])
        self.assertIn("p99", changed.audit["text_length_chars"])

    def test_r2_seed_adds_exactly_2000_explicitly_eligible_clips(
        self,
    ) -> None:
        capacity = seed_representative_distribution(
            self.engine,
            sample_distribution=self.sample,
            eligible_clip_count=R2_CLIP_COUNT,
        )
        assert_representative_capacity(
            capacity, eligible_clip_count=R2_CLIP_COUNT
        )
        self.assertEqual(
            capacity["search_eligible_clip_count"], R2_CLIP_COUNT
        )
        self.assertEqual(
            capacity["ready_original_video_count"], VIDEO_COUNT
        )

    def test_precached_planner_is_enabled_normal_and_not_degraded(
        self,
    ) -> None:
        cache = PrecachedBenchmarkPlanner()
        planner = MediaLibrarySearchPlanner(
            planner=cache,
            enabled=True,
        )
        first = asyncio.run(planner.plan("防水能力"))
        second = asyncio.run(planner.plan("防水能力"))
        self.assertFalse(first.degraded)
        self.assertFalse(second.degraded)
        self.assertEqual(first.plan.planner_version, PLANNER_VERSION)
        self.assertEqual(cache.misses, 1)
        self.assertEqual(cache.hits, 1)
        self.assertEqual(first.plan.exact_phrases, ["防水能力"])

    def test_latency_summary_has_p50_p95_and_p99(self) -> None:
        samples = [float(value) for value in range(1, 201)]
        self.assertEqual(nearest_rank_percentile(samples, 0.50), 100.0)
        self.assertEqual(nearest_rank_percentile(samples, 0.95), 190.0)
        self.assertEqual(nearest_rank_percentile(samples, 0.99), 198.0)
        summary = distribution_summary(samples)
        self.assertEqual(summary["p50"], 100.0)
        self.assertEqual(summary["p95"], 190.0)
        self.assertEqual(summary["p99"], 198.0)
        self.assertEqual(P95_GATE_MS, 3000.0)

    def test_benchmark_requires_20_warmups_and_200_queries(self) -> None:
        self.assertGreaterEqual(MIN_WARMUP_ITERATIONS, 20)
        self.assertGreaterEqual(MIN_MEASURED_ITERATIONS, 200)
        with self.assertRaisesRegex(
            ValueError, "benchmark_warmups_must_be_at_least"
        ):
            run_measured_searches(
                self.engine,
                warmups=MIN_WARMUP_ITERATIONS - 1,
                iterations=MIN_MEASURED_ITERATIONS,
            )
        with self.assertRaisesRegex(
            ValueError, "benchmark_iterations_must_be_at_least"
        ):
            run_measured_searches(
                self.engine,
                warmups=MIN_WARMUP_ITERATIONS,
                iterations=MIN_MEASURED_ITERATIONS - 1,
            )

    def test_isolation_schema_name_is_generated_and_identifier_safe(
        self,
    ) -> None:
        first = isolated_schema_name()
        second = isolated_schema_name()
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^oc_mlsearch_bench_[0-9a-f]{32}$")

    def test_committed_postgres_artifact_satisfies_release_gate(
        self,
    ) -> None:
        artifact_path = (
            BACKEND_PATH
            / "tests"
            / "artifacts"
            / "media_library_search_postgres_benchmark.json"
        )
        artifact = json.loads(
            artifact_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            artifact["benchmark"],
            "media_library_dialogue_visual_literal_search_postgresql_v3",
        )
        self.assertEqual(
            artifact["retrieval_version"],
            "dialogue_visual_literal_v1",
        )
        self.assertIn("PostgreSQL 16", artifact["database_version"])
        self.assertRegex(
            artifact["worktree"]["head"], r"^[0-9a-f]{40}$"
        )
        self.assertRegex(
            artifact["worktree"]["status_porcelain_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertTrue(artifact["cleanup_confirmed"])
        self.assertFalse(
            artifact["isolation"]["main_schema_data_modified"]
        )
        self.assertEqual(
            artifact["dataset"]["ready_original_video_count"], 500
        )
        self.assertEqual(
            artifact["dataset"][
                "active_visual_semantic_fragment_count"
            ],
            1_250,
        )
        self.assertEqual(
            artifact["dataset"][
                "current_ready_visual_semantic_run_count"
            ],
            500,
        )
        self.assertNotEqual(
            artifact["dataset"]["fragment_count_per_video"]["minimum"],
            artifact["dataset"]["fragment_count_per_video"]["maximum"],
        )
        source = artifact["dataset_seed"]["source_sample"]
        self.assertGreaterEqual(
            source["source_file_count"], benchmark.MIN_REAL_SAMPLE_FILES
        )
        self.assertGreaterEqual(
            source["source_fragment_count"],
            benchmark.MIN_REAL_SAMPLE_FRAGMENTS,
        )
        measurements = artifact["measurements"]
        self.assertGreaterEqual(
            measurements["warmup_count"], MIN_WARMUP_ITERATIONS
        )
        self.assertGreaterEqual(
            measurements["query_count"], MIN_MEASURED_ITERATIONS
        )
        self.assertEqual(
            measurements["planner_mode"],
            benchmark.PLANNER_MODE,
        )
        self.assertEqual(
            measurements["retrieval_version"],
            "dialogue_visual_literal_v1",
        )
        self.assertEqual(
            measurements["visual_query_distribution"],
            list(benchmark.VISUAL_QUERY_DISTRIBUTION),
        )
        for field in (
            "planner_cold_wall_latency_ms",
            "planner_cached_latency_ms",
            "retrieval_latency_ms",
            "total_without_external_provider_latency_ms",
        ):
            self.assertTrue(
                {"p50", "p95", "p99"}.issubset(
                    measurements[field]
                )
            )
        self.assertGreater(measurements["zero_result_rate"], 0)
        self.assertGreaterEqual(
            len(measurements["top_query_plans"]),
            len(benchmark.BENCHMARK_QUERIES),
        )
        self.assertLessEqual(
            measurements[
                "total_without_external_provider_latency_ms"
            ]["p95"],
            P95_GATE_MS,
        )
        self.assertTrue(measurements["gate"]["passed"])

    def test_committed_r2_postgres_artifact_satisfies_clip_release_gate(
        self,
    ) -> None:
        artifact_path = (
            BACKEND_PATH
            / "tests"
            / "artifacts"
            / "media_library_search_postgres_benchmark_r2.json"
        )
        artifact = json.loads(
            artifact_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            artifact["benchmark"],
            "media_library_dialogue_visual_clip_literal_search_postgresql_v4",
        )
        self.assertIn("PostgreSQL 16", artifact["database_version"])
        self.assertEqual(
            artifact["dataset"]["ready_original_video_count"],
            VIDEO_COUNT,
        )
        self.assertEqual(
            artifact["dataset"]["search_eligible_clip_count"],
            R2_CLIP_COUNT,
        )
        self.assertTrue(artifact["cleanup_confirmed"])
        self.assertFalse(
            artifact["isolation"]["main_schema_data_modified"]
        )
        measurements = artifact["measurements"]
        self.assertGreaterEqual(
            measurements["warmup_count"], MIN_WARMUP_ITERATIONS
        )
        self.assertGreaterEqual(
            measurements["query_count"], MIN_MEASURED_ITERATIONS
        )
        for field in (
            "retrieval_latency_ms",
            "total_without_external_provider_latency_ms",
        ):
            self.assertTrue(
                {"p50", "p95", "p99"}.issubset(measurements[field])
            )
            self.assertLessEqual(
                measurements[field]["p95"], P95_GATE_MS
            )
        self.assertTrue(measurements["gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
