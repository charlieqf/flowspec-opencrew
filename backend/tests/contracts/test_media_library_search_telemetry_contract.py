from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_search_actions,
    media_library_search_runs,
    metadata,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MatchedMediaLibraryFragment,
    MediaLibraryQueryPlanV1,
    MediaLibrarySearchAction,
    MediaLibrarySearchCandidate,
    MediaLibrarySearchPlanner,
    MediaLibrarySearchRepository,
    MediaLibrarySearchRequest,
    MediaLibrarySearchService,
    SearchTelemetry,
)


class MediaLibrarySearchTelemetryContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        self.repository = MediaLibrarySearchRepository(self.engine)
        self.metrics: list[tuple[str, int]] = []
        self.events: list[tuple[str, dict]] = []
        self.telemetry = SearchTelemetry(
            self.repository,
            metric_sink=lambda name, value: self.metrics.append((name, value)),
            event_sink=lambda kind, payload: self.events.append(
                (kind, payload)
            ),
            raw_query_retention_days=0,
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def request(self) -> MediaLibrarySearchRequest:
        return MediaLibrarySearchRequest(
            query="内部敏感对白 防水能力",
            entry_point="storyboard",
            query_source="dialogue",
            target_task_id=27,
            dialogue_asset_key="dialogue-stable-key",
        )

    def plan(self) -> MediaLibraryQueryPlanV1:
        return MediaLibraryQueryPlanV1(
            original_query="内部敏感对白 防水能力",
            exact_phrases=["防水能力"],
            optional_terms=["防护"],
            negative_terms=[],
        )

    def candidate(self) -> MediaLibrarySearchCandidate:
        return MediaLibrarySearchCandidate(
            candidate_id="asset-telemetry",
            asset_id="asset-telemetry",
            source_version="a" * 64,
            display_name="不应进入候选快照的标题",
            thumbnail_url="/api/private/thumbnail",
            preview_url="/api/private/preview",
            duration_ms=5000,
            orientation="landscape",
            score=0.75,
            raw_score=150,
            score_reasons=["完整原始查询命中对白"],
            matched_fragments=[
                MatchedMediaLibraryFragment(
                    scheme="dialogue",
                    run_id="mlar_dialogue_telemetry",
                    fragment_id="srt_0001",
                    start_ms=100,
                    end_ms=900,
                    dialogue_text="不应进入候选快照的完整对白",
                    summary="不应进入候选快照的摘要",
                    raw_score=150,
                    score_reasons=["完整原始查询命中对白"],
                )
            ],
        )

    def test_run_persists_hash_counts_versions_latency_and_private_candidate_snapshot(self) -> None:
        self.assertTrue(
            self.telemetry.create_run(
                search_id="mls_telemetry",
                request=self.request(),
                plan=self.plan(),
                planner_degraded=False,
                planner_latency_ms=12,
                timestamp=100,
            )
        )
        self.assertTrue(
            self.telemetry.complete_run(
                search_id="mls_telemetry",
                candidates=[self.candidate()],
                result_count=1,
                retrieval_latency_ms=23,
                total_latency_ms=40,
                timestamp=140,
            )
        )
        with self.engine.connect() as conn:
            row = conn.execute(
                select(media_library_search_runs).where(
                    media_library_search_runs.c.search_id == "mls_telemetry"
                )
            ).mappings().one()
        plan = row["query_plan_json"]
        snapshot = row["top_candidates_json"]
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["result_count"], 1)
        self.assertFalse(row["zero_result"])
        self.assertEqual(row["planner_latency_ms"], 12)
        self.assertEqual(row["retrieval_latency_ms"], 23)
        self.assertEqual(row["total_latency_ms"], 40)
        self.assertEqual(plan["exact_phrase_count"], 1)
        self.assertEqual(plan["optional_term_count"], 1)
        self.assertEqual(plan["tokenizer_version"], "none")
        self.assertEqual(plan["reranker_version"], "none")
        self.assertNotIn("original_query", plan)
        self.assertNotIn("retained_original_query", plan)
        self.assertNotIn("内部敏感对白", str(plan))
        self.assertEqual(
            snapshot,
            [
                {
                    "source": "media_library",
                    "candidate_kind": "original_video",
                    "candidate_id": "asset-telemetry",
                    "source_asset_id": "asset-telemetry",
                    "source_clip_id": None,
                    "source_version": "a" * 64,
                    "content_sha256": "a" * 64,
                    "rank": 1,
                    "score": 0.75,
                    "matched_fragment_ids": ["srt_0001"],
                }
            ],
        )
        self.assertNotIn("完整对白", str(snapshot))
        self.assertNotIn("preview", str(snapshot))
        self.assertEqual(
            self.events[-1],
            (
                "media_library.search.completed",
                {
                    "search_id": "mls_telemetry",
                    "result_count": 1,
                    "retrieval_latency_ms": 23,
                    "total_latency_ms": 40,
                },
            ),
        )

    def test_editor_run_emits_retrieval_and_total_latency_metrics(
        self,
    ) -> None:
        self.telemetry.create_run(
            search_id="mls_editor_metrics",
            request=self.request(),
            plan=self.plan(),
            planner_degraded=False,
            planner_latency_ms=3,
            timestamp=100,
        )
        self.telemetry.complete_editor_run(
            search_id="mls_editor_metrics",
            candidates=[],
            source_runs={"media_library": "mls_source_run"},
            source_errors={},
            retrieval_latency_ms=17,
            total_latency_ms=29,
            timestamp=129,
        )
        self.assertIn(
            ("media_library_search_retrieval_latency_ms", 17),
            self.metrics,
        )
        self.assertIn(
            ("media_library_search_latency_ms", 29),
            self.metrics,
        )

    def test_action_uses_original_rank_and_scrubs_paths_urls_keys_and_query_text(self) -> None:
        service = MediaLibrarySearchService(
            repository=self.repository,
            planner=MediaLibrarySearchPlanner(enabled=False),
            telemetry=self.telemetry,
        )
        self.telemetry.create_run(
            search_id="mls_action",
            request=self.request(),
            plan=self.plan(),
            planner_degraded=True,
            planner_latency_ms=0,
            timestamp=100,
        )
        self.telemetry.complete_run(
            search_id="mls_action",
            candidates=[self.candidate()],
            result_count=1,
            retrieval_latency_ms=1,
            total_latency_ms=2,
            timestamp=102,
        )
        self.assertTrue(
            service.record_action(
                MediaLibrarySearchAction(
                    search_id="mls_action",
                    action_kind="preview",
                    source="media_library",
                    candidate_id="asset-telemetry",
                    source_asset_id="client-forged",
                    candidate_rank=99,
                    target_task_id=27,
                    metadata={
                        "fragment_id": "srt_0001",
                        "path": "/private/tmp/source.mp4",
                        "preview_url": "https://secret.invalid/preview",
                        "api_key": "secret",
                        "raw_query": "内部敏感对白",
                        "nested": {
                            "dialogue_text": "完整对白",
                            "safe_version": "v1",
                        },
                    },
                ),
                timestamp=200,
            )
        )
        with self.engine.connect() as conn:
            action = conn.execute(
                select(media_library_search_actions)
            ).mappings().one()
        self.assertEqual(action["candidate_rank"], 1)
        self.assertEqual(action["source_asset_id"], "asset-telemetry")
        self.assertEqual(
            action["metadata_json"],
            {
                "fragment_id": "srt_0001",
                "nested": {"safe_version": "v1"},
            },
        )
        self.assertEqual(
            self.events[-1][0],
            "media_library.search.action",
        )
        self.assertEqual(
            self.events[-1][1]["candidate_rank"],
            1,
        )

    def test_telemetry_database_and_metric_failures_do_not_block_search(self) -> None:
        class FailingRepository:
            def create_search_run(self, _values):
                raise RuntimeError("database unavailable")

            def update_search_run(self, _search_id, **_values):
                raise RuntimeError("database unavailable")

            def create_action(self, _values):
                raise RuntimeError("database unavailable")

            def retrieve(
                self,
                _plan,
                *,
                exclude_asset_id=None,
                dialogue_query="",
                user_query="",
            ):
                return []

            def recheck_eligible(self, _asset_ids):
                return set()

            def get_search_run(self, _search_id):
                return None

        repository = FailingRepository()

        def failing_metric(_name, _value):
            raise RuntimeError("metric sink unavailable")

        telemetry = SearchTelemetry(
            repository,  # type: ignore[arg-type]
            metric_sink=failing_metric,
        )
        service = MediaLibrarySearchService(
            repository=repository,  # type: ignore[arg-type]
            planner=MediaLibrarySearchPlanner(enabled=False),
            telemetry=telemetry,
        )
        response = service.search_sync(
            {
                "query": "防水能力",
                "entry_point": "agent",
                "query_source": "manual",
            }
        )
        self.assertTrue(response.planner_degraded)
        self.assertEqual(response.result_count, 0)
        self.assertEqual(response.items, [])
        self.assertFalse(
            telemetry.record_action(
                MediaLibrarySearchAction(
                    search_id=response.search_id,
                    action_kind="import",
                    source="media_library",
                    candidate_id="asset-missing",
                ),
                timestamp=10,
            )
        )

    def test_internal_type_error_is_not_retried_without_query_context(
        self,
    ) -> None:
        class TypeErrorRepository:
            def __init__(self) -> None:
                self.retrieve_calls = 0

            def capacity(self):
                return {
                    "ready_assets": 0,
                    "active_dialogue_fragments": 0,
                }

            def create_search_run(self, _values):
                return None

            def update_search_run(self, _search_id, **_values):
                return None

            def retrieve(
                self,
                _plan,
                *,
                exclude_asset_id=None,
                dialogue_query="",
                user_query="",
            ):
                self.retrieve_calls += 1
                raise TypeError("dialogue_query normalization failed")

        repository = TypeErrorRepository()
        service = MediaLibrarySearchService(
            repository=repository,  # type: ignore[arg-type]
            planner=MediaLibrarySearchPlanner(enabled=False),
        )
        with self.assertRaisesRegex(
            TypeError, "dialogue_query normalization failed"
        ):
            service.search_sync(
                {
                    "query": "防水能力",
                    "entry_point": "agent",
                    "query_source": "manual",
                }
            )
        self.assertEqual(repository.retrieve_calls, 1)

    def test_capacity_observation_is_ttl_sampled_without_concurrent_stampede(
        self,
    ) -> None:
        class CapacityRepository:
            def __init__(self) -> None:
                self.capacity_calls = 0

            def capacity(self):
                self.capacity_calls += 1
                return {
                    "ready_assets": 500,
                    "active_dialogue_fragments": 1500,
                }

        class CapacityTelemetry:
            def __init__(self) -> None:
                self.capacity: list[tuple[int, int]] = []

            def observe_capacity(
                self,
                *,
                ready_assets: int,
                active_dialogue_fragments: int,
            ) -> None:
                self.capacity.append(
                    (ready_assets, active_dialogue_fragments)
                )

            def planner_degraded(self, _error_code) -> None:
                return None

            def create_run(self, **_values) -> bool:
                return True

        repository = CapacityRepository()
        telemetry = CapacityTelemetry()
        clock = [100.0]
        service = MediaLibrarySearchService(
            repository=repository,  # type: ignore[arg-type]
            planner=MediaLibrarySearchPlanner(enabled=False),
            telemetry=telemetry,  # type: ignore[arg-type]
            capacity_sample_interval_seconds=60,
            monotonic=lambda: clock[0],
        )

        async def exercise() -> None:
            await asyncio.gather(
                *(service.begin_search(self.request()) for _ in range(8))
            )
            clock[0] += 59
            await service.begin_search(self.request())
            clock[0] += 2
            await asyncio.gather(
                *(service.begin_search(self.request()) for _ in range(8))
            )

        asyncio.run(exercise())

        self.assertEqual(repository.capacity_calls, 2)
        self.assertEqual(
            telemetry.capacity,
            [(500, 1500), (500, 1500)],
        )

    def test_capacity_and_rolling_p95_metrics_emit_release_warnings(
        self,
    ) -> None:
        with self.assertLogs(
            "opcrew_backend.media_library_search.telemetry",
            level="WARNING",
        ) as captured:
            self.telemetry.observe_capacity(
                ready_assets=450,
                active_dialogue_fragments=1350,
            )
            for _ in range(20):
                self.telemetry._observe_search_latency(3001)
        self.assertIn(
            ("media_library_ready_assets", 450),
            self.metrics,
        )
        self.assertIn(
            ("media_library_active_dialogue_fragments", 1350),
            self.metrics,
        )
        self.assertIn(
            ("media_library_search_rolling_p95_ms", 3001),
            self.metrics,
        )
        logs = "\n".join(captured.output)
        self.assertIn("media_library_capacity_warning", logs)
        self.assertIn("media_library_search_latency_p95_warning", logs)


if __name__ == "__main__":
    unittest.main()
