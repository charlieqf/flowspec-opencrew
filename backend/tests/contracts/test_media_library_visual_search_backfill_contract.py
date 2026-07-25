from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from backend.scripts.backfill_media_library_visual_search import (  # noqa: E402
    run_backfill,
)
from backend.tests.contracts import (  # noqa: E402
    test_media_library_visual_semantic_service_contract as service_fixture,
)
from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_fragment_index,
)


class MediaLibraryVisualSearchBackfillContractTest(unittest.TestCase):
    def fixture(
        self,
    ) -> service_fixture.MediaLibraryVisualSemanticServiceContractTest:
        case = service_fixture.MediaLibraryVisualSemanticServiceContractTest(
            methodName="runTest"
        )
        case.setUp()
        self.addCleanup(case.tearDown)
        return case

    def test_dry_run_write_and_repeat_are_model_free_and_idempotent(self) -> None:
        case = self.fixture()
        client = service_fixture._FakeCloudVisualClient(
            [service_fixture._valid_description()]
        )
        response = case._start(client, allow_cloud=True)
        run_id = str(response["semantic_run_id"])
        self.assertEqual(len(client.prompt_calls), 1)
        with case.engine.connect() as conn:
            self.assertEqual(
                int(
                    conn.execute(
                        select(func.count())
                        .select_from(media_library_fragment_index)
                        .where(
                            media_library_fragment_index.c.analysis_run_id
                            == run_id
                        )
                    ).scalar_one()
                ),
                0,
            )

        dry_run = run_backfill(case.engine, timestamp=1000)
        self.assertTrue(dry_run["dry_run"])
        self.assertEqual(dry_run["publishable_count"], 1)
        self.assertEqual(dry_run["published_count"], 0)
        self.assertEqual(dry_run["reanalysis_required_count"], 0)

        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "true"},
            clear=False,
        ):
            written = run_backfill(
                case.engine, write=True, timestamp=1001
            )
            repeated = run_backfill(
                case.engine, write=True, timestamp=1002
            )
        self.assertEqual(written["published_count"], 1)
        self.assertEqual(written["failed_count"], 0)
        self.assertEqual(repeated["already_published_count"], 1)
        self.assertEqual(repeated["published_count"], 0)
        self.assertEqual(len(client.prompt_calls), 1)
        self.assertEqual(len(case.usage.calls), 1)
        with case.engine.connect() as conn:
            fragment = conn.execute(
                select(media_library_fragment_index).where(
                    media_library_fragment_index.c.analysis_run_id == run_id
                )
            ).mappings().one()
        self.assertTrue(fragment["is_active"])
        self.assertEqual(fragment["analysis_scheme"], "visual_semantic")
        self.assertEqual(
            fragment["keyframe_ref_json"],
            [
                f"scene_0001-sample-{index:02d}"
                for index in range(1, 5)
            ],
        )

    def test_historical_single_frame_is_reanalysis_required_not_published(
        self,
    ) -> None:
        case = self.fixture()
        with case.engine.begin() as conn:
            conn.execute(
                media_library_analysis_runs.insert().values(
                    analysis_run_id="mlar_historical_midpoint",
                    asset_id=service_fixture.ASSET_ID,
                    scheme="visual_semantic",
                    source_version=service_fixture.SOURCE_VERSION,
                    status="ready",
                    schema_version="media_library_visual_semantic_v1",
                    result_hash="e" * 64,
                    result_index_path="missing/historical-v1.json",
                    upstream_refs_json={
                        "sampling_strategy": "scene_midpoint_v1"
                    },
                    progress_json={},
                    is_current=True,
                    created_at=200,
                    updated_at=200,
                )
            )
        report = run_backfill(case.engine)
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["reanalysis_required_count"], 1)
        self.assertEqual(report["publishable_count"], 0)
        self.assertEqual(report["failed_count"], 0)
        self.assertEqual(
            report["items"][0],
            {
                "asset_id": service_fixture.ASSET_ID,
                "analysis_run_id": "mlar_historical_midpoint",
                "status": "reanalysis_required",
                "reason": "sampling_strategy_ineligible",
            },
        )
        with case.engine.connect() as conn:
            count = int(
                conn.execute(
                    select(func.count())
                    .select_from(media_library_fragment_index)
                    .where(
                        media_library_fragment_index.c.analysis_run_id
                        == "mlar_historical_midpoint"
                    )
                ).scalar_one()
            )
        self.assertEqual(count, 0)

    def test_changed_registered_v2_result_fails_without_partial_index(self) -> None:
        case = self.fixture()
        client = service_fixture._FakeCloudVisualClient(
            [service_fixture._valid_description()]
        )
        response = case._start(client, allow_cloud=True)
        run = case.run_repo.get(str(response["semantic_run_id"]))
        assert run is not None
        result_path = case.workspace / str(run["result_index_path"])
        result_path.write_text("{}", encoding="utf-8")
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "true"},
            clear=False,
        ):
            report = run_backfill(case.engine, write=True)
        self.assertEqual(report["failed_count"], 1)
        self.assertIn("sampling_strategy_ineligible", report["items"][0]["error"])
        with case.engine.connect() as conn:
            count = int(
                conn.execute(
                    select(func.count())
                    .select_from(media_library_fragment_index)
                    .where(
                        media_library_fragment_index.c.analysis_run_id
                        == run["analysis_run_id"]
                    )
                ).scalar_one()
            )
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
