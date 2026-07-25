from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibrarySearchPlanner,
    OpenCodeMediaLibrarySearchPlannerAdapter,
)


class FakeUsageRecorder:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record_with_result(self, **fields):
        self.records.append(dict(fields))
        return SimpleNamespace(
            request_id="usage-request",
            local_usage_id="usage-row",
        )


class FakeOpenCodeClient:
    def __init__(
        self,
        output: dict | str | None,
        *,
        connected: bool = True,
    ) -> None:
        self.output = output
        self.connected = connected
        self.prompt_calls: list[dict] = []
        self.created_sessions: list[str] = []
        self.deleted_sessions: list[str] = []
        self.aborted_sessions: list[str] = []

    def providers(self, *, timeout: int = 30) -> dict:
        return {
            "connected": ["openai"] if self.connected else [],
            "default": {"openai": "gpt-5.5"},
            "all": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "models": {
                        "gpt-5.5": {
                            "id": "gpt-5.5",
                            "name": "GPT-5.5",
                            "modalities": {"input": ["text"]},
                        }
                    },
                }
            ],
        }

    def create_session(self, title: str, *, timeout: int = 30) -> dict:
        session_id = f"planner-session-{len(self.created_sessions) + 1}"
        self.created_sessions.append(session_id)
        return {"id": session_id}

    def prompt_async(
        self,
        session_id: str,
        prompt: str,
        **kwargs,
    ) -> None:
        self.prompt_calls.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                **kwargs,
            }
        )

    def messages(
        self,
        session_id: str,
        limit: int = 50,
        *,
        timeout: int = 30,
    ) -> list[dict]:
        if self.output is None:
            return []
        text = (
            json.dumps(self.output, ensure_ascii=False)
            if isinstance(self.output, dict)
            else str(self.output)
        )
        return [
            {
                "info": {
                    "role": "assistant",
                    "time": {"completed": 9_999_999_999_999},
                },
                "parts": [{"type": "text", "text": text}],
            }
        ]

    def abort(self, session_id: str) -> None:
        self.aborted_sessions.append(session_id)

    def delete_session(
        self, session_id: str, *, timeout: int = 30
    ) -> bool:
        self.deleted_sessions.append(session_id)
        return True


class FakeContext:
    def __init__(self) -> None:
        self.engine = None
        self.media_library_repo = SimpleNamespace(
            get=lambda asset_id: (
                {"asset_id": asset_id, "session_id": 41}
                if asset_id == "asset-source"
                else None
            )
        )
        self.session_repo = SimpleNamespace(
            get=lambda session_id: (
                {
                    "id": session_id,
                    "workspace_dir": "/tmp/media-library-planner",
                }
                if session_id == 41
                else None
            )
        )
        self.local_usage = FakeUsageRecorder()
        self.metrics: list[tuple[str, int]] = []

    def media_library_metric(self, name: str, value: int) -> None:
        self.metrics.append((name, value))


class MediaLibrarySearchModelPlannerContractTest(unittest.TestCase):
    def planner(
        self,
        client: FakeOpenCodeClient,
        *,
        ctx: FakeContext | None = None,
        timeout_seconds: float = 2,
    ) -> tuple[MediaLibrarySearchPlanner, FakeContext]:
        context = ctx or FakeContext()
        adapter = OpenCodeMediaLibrarySearchPlannerAdapter(
            context,
            client_factory=lambda _ctx, _session, _message: client,
        )
        return (
            MediaLibrarySearchPlanner(
                adapter,
                enabled=True,
                timeout_seconds=timeout_seconds,
            ),
            context,
        )

    def run_plan(self, planner: MediaLibrarySearchPlanner):
        return asyncio.run(
            planner.plan(
                "产品防水能力",
                orientation="portrait",
                min_duration_ms=250,
                max_duration_ms=5000,
                sources=["media_library"],
                request_context={"source_asset_id": "asset-source"},
            )
        )

    def test_approved_alias_strict_json_tools_and_authoritative_filters(
        self,
    ) -> None:
        client = FakeOpenCodeClient(
            {
                "exact_phrases": ["防水能力"],
                "optional_terms": ["防护", "进水"],
                "negative_terms": ["虚假演示"],
            }
        )
        planner, ctx = self.planner(client)

        outcome = self.run_plan(planner)

        self.assertFalse(outcome.degraded)
        self.assertEqual(outcome.error_code, None)
        self.assertEqual(outcome.plan.original_query, "产品防水能力")
        self.assertEqual(outcome.plan.orientation, "portrait")
        self.assertEqual(outcome.plan.min_duration_ms, 250)
        self.assertEqual(outcome.plan.max_duration_ms, 5000)
        self.assertEqual(
            outcome.plan.planner_version,
            "ml_query_planner_opencode_v1",
        )
        self.assertEqual(len(client.prompt_calls), 1)
        call = client.prompt_calls[0]
        self.assertTrue(call["tools"])
        self.assertTrue(all(value is False for value in call["tools"].values()))
        sent = json.loads(call["prompt"])
        self.assertEqual(
            set(sent), {"schema_version", "query"}
        )
        self.assertNotIn("asset-source", call["prompt"])
        self.assertNotIn("openai", json.dumps(outcome.plan.model_dump()))
        self.assertNotIn("gpt-5.5", json.dumps(outcome.plan.model_dump()))
        self.assertEqual(client.deleted_sessions, client.created_sessions)
        self.assertEqual(len(ctx.local_usage.records), 1)
        usage = ctx.local_usage.records[0]
        self.assertEqual(
            usage["proxy_policy"],
            "opencode_media_library_search_planner",
        )
        self.assertNotIn("query", usage)
        self.assertNotIn("prompt", usage)

    def test_successful_plan_is_cached_without_second_model_call(self) -> None:
        client = FakeOpenCodeClient(
            {
                "exact_phrases": ["防水能力"],
                "optional_terms": [],
                "negative_terms": [],
            }
        )
        planner, ctx = self.planner(client)

        first = self.run_plan(planner)
        second = self.run_plan(planner)

        self.assertFalse(first.degraded)
        self.assertFalse(second.degraded)
        self.assertEqual(len(client.prompt_calls), 1)
        self.assertEqual(len(ctx.local_usage.records), 1)
        self.assertIn(
            ("media_library_search_planner_cache_hit_total", 1),
            ctx.metrics,
        )

    def test_unknown_field_and_non_json_degrade_without_leaking_output(
        self,
    ) -> None:
        for output in (
            {
                "exact_phrases": ["防水能力"],
                "optional_terms": [],
                "negative_terms": [],
                "model": "real-model",
            },
            "```json\n{\"exact_phrases\":[\"防水能力\"]}\n```",
        ):
            with self.subTest(output=output):
                planner, _ctx = self.planner(
                    FakeOpenCodeClient(output)
                )
                outcome = self.run_plan(planner)
                self.assertTrue(outcome.degraded)
                self.assertEqual(outcome.error_code, "planner_invalid")
                self.assertEqual(
                    outcome.plan.exact_phrases, ["产品防水能力"]
                )

    def test_disabled_quota_timeout_and_unavailable_are_structured(
        self,
    ) -> None:
        disabled = asyncio.run(
            MediaLibrarySearchPlanner(enabled=False).plan("产品防水能力")
        )
        self.assertEqual(disabled.error_code, "planner_disabled")

        with patch.dict(
            os.environ,
            {
                "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_EST_COST_PER_CALL_MICROS": "500",
                "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_MAX_EST_COST_MICROS": "100",
            },
        ):
            quota, _ctx = self.planner(FakeOpenCodeClient({}))
            quota_outcome = self.run_plan(quota)
        self.assertTrue(quota_outcome.degraded)
        self.assertEqual(
            quota_outcome.error_code, "planner_quota_exceeded"
        )

        unavailable, _ctx = self.planner(
            FakeOpenCodeClient({}, connected=False)
        )
        unavailable_outcome = self.run_plan(unavailable)
        self.assertTrue(unavailable_outcome.degraded)
        self.assertEqual(
            unavailable_outcome.error_code, "planner_unavailable"
        )

        with patch.dict(
            os.environ,
            {
                "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_TIMEOUT_SECONDS": "0.1"
            },
        ):
            timeout, _ctx = self.planner(
                FakeOpenCodeClient(None),
                timeout_seconds=1,
            )
            timeout_outcome = self.run_plan(timeout)
        self.assertTrue(timeout_outcome.degraded)
        self.assertEqual(timeout_outcome.error_code, "planner_timeout")


if __name__ == "__main__":
    unittest.main()
