from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
SCRIPTS_PATH = BACKEND_PATH / "scripts"
for path in (BACKEND_PATH, SCRIPTS_PATH):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from validate_media_library_e2e_sample_matrix import (  # noqa: E402
    DEFAULT_DATABASE_URL,
    EXPECTED_MEDIA_HASHES,
    QUALITY_QUERY,
    run_postgres_quality_gate,
    sqlalchemy_database_url,
)


class MediaLibraryRealSampleSearchQualityPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = os.environ.get(
            "OPENCREW_MEDIA_LIBRARY_QUALITY_DATABASE_URL",
            os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        )
        cls.sessions_root = Path(
            os.environ.get(
                "OPENCREW_MEDIA_LIBRARY_SAMPLE_ROOT",
                str(Path.home() / ".opencrew" / "sessions"),
            )
        )
        required = (
            cls.sessions_root
            / "185"
            / "workspace"
            / "SessionContext"
            / "Video_Source.mp4"
        )
        if not required.is_file():
            raise unittest.SkipTest(
                f"real media quality samples unavailable: {required}"
            )
        probe = create_engine(
            sqlalchemy_database_url(cls.database_url),
            pool_pre_ping=True,
        )
        try:
            with probe.connect() as conn:
                if conn.dialect.name != "postgresql":
                    raise unittest.SkipTest(
                        "real search quality gate requires PostgreSQL"
                    )
                conn.execute(text("SELECT 1"))
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(
                f"quality PostgreSQL unavailable: {type(exc).__name__}"
            ) from exc
        finally:
            probe.dispose()

    def test_candidate_level_synonym_recall_and_stable_order(self) -> None:
        result = run_postgres_quality_gate(
            database_url=self.database_url,
            sessions_root=self.sessions_root,
        )
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["candidate_level_synonym_only"])
        self.assertTrue(result["stable_order"])
        self.assertEqual(result["first_order"], result["second_order"])
        self.assertEqual(
            set(result["first_order"]),
            {"real-session-185", "real-session-190"},
        )
        self.assertTrue(result["cleanup_confirmed"])
        self.assertFalse(result["isolation"]["public_data_modified"])
        self.assertEqual(result["query"]["literal"], QUALITY_QUERY)
        for candidate in result["candidates"]:
            self.assertFalse(
                candidate["source_dialogue_literal_query_present"]
            )
            self.assertTrue(
                candidate["optional_terms_observed_in_full_dialogue"]
            )
            self.assertTrue(
                any(
                    reason.startswith("规划关键词命中")
                    for reason in candidate["score_reasons"]
                )
            )
            self.assertIn(
                candidate["source_content_sha256"],
                {
                    EXPECTED_MEDIA_HASHES["similar_dialogue_a"],
                    EXPECTED_MEDIA_HASHES["similar_dialogue_b"],
                },
            )

    def test_real_sample_zero_result_is_empty(self) -> None:
        result = run_postgres_quality_gate(
            database_url=self.database_url,
            sessions_root=self.sessions_root,
        )
        self.assertEqual(
            result["zero_result_backend"]["result_count"],
            0,
        )
        self.assertTrue(result["zero_result_backend"]["passed"])
        self.assertTrue(result["cleanup_confirmed"])


if __name__ == "__main__":
    unittest.main()
