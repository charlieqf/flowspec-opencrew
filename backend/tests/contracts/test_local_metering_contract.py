from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.db.schema import metadata  # noqa: E402
from opcrew_backend.routes.local_metering import build_local_metering_router  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.usage_metering import video_usage_units  # noqa: E402
from opcrew_backend.services.local_metering import LocalMeteringService, capped_artifact_kb, now_ms  # noqa: E402
from opcrew_backend.services.local_usage import LocalUsageRecorder  # noqa: E402


class LocalMeteringContractTest(unittest.TestCase):
    def test_video_usage_units_preserve_resolution_for_pricebook_selection(self) -> None:
        self.assertEqual(
            video_usage_units(seconds=6, prompt="hello world", reference_count=1, resolution="1080p"),
            {"request": 1, "video_1080p_second": 6.0, "prompt_character": 10, "reference": 1},
        )

    def test_report_calculates_material_units_cost_sell_and_profit(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            created_at = now_ms()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
INSERT INTO local_usage_log (
  id, request_id, provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros, error_code,
  started_at, finished_at, created_at
) VALUES
  (1, 'req_image', 'openai', 'gpt-image-1', 'image', 'local_box', 'local_usage_only', '', 'ok', :image_units, NULL, '', :ts, :ts, :ts),
  (2, 'req_video', 'xai', 'grok-imagine-video', 'video', 'local_box', 'local_usage_only', '', 'ok', :video_units, NULL, '', :ts, :ts, :ts),
  (3, 'req_text', 'openai', 'gpt-5.5', 'chat', 'local_box', 'local_usage_only', '', 'ok', :text_units, NULL, '', :ts, :ts, :ts)
"""
                    ),
                    {
                        "image_units": json.dumps({"image": 2}),
                        "video_units": json.dumps({"video_second": 3}),
                        "text_units": json.dumps({"input_tokens": 1000, "output_tokens": 500}),
                        "ts": created_at,
                    },
                )

            report = LocalMeteringService(engine).report(days=1, limit=2)

            self.assertEqual(report["totals"]["request_count"], 3)
            self.assertGreater(report["totals"]["cost_micros"], 0)
            self.assertGreater(report["totals"]["sell_micros"], report["totals"]["cost_micros"])
            self.assertEqual(report["totals"]["charge_micros"], report["totals"]["sell_micros"])
            self.assertEqual(report["totals"]["profit_micros"], report["totals"]["sell_micros"] - report["totals"]["cost_micros"])
            self.assertEqual(report["totals"]["units"]["image"], 2.0)
            self.assertEqual(report["totals"]["units"]["video_second"], 3.0)
            modalities = {item["modality"] for item in report["by_modality"]}
            self.assertTrue({"image", "video", "chat"}.issubset(modalities))
            self.assertEqual(report["item_count"], 3)
            self.assertEqual(len(report["items"]), 2)
            self.assertIn("pricebook", report)
            self.assertIn("pricebook_note", report)
        finally:
            engine.dispose()

    def test_report_prefers_provider_actual_cost_and_keeps_pricebook_charge(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            created_at = now_ms()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
INSERT INTO local_usage_log (
  id, request_id, provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros,
  actual_cost_micros, actual_cost_currency, actual_cost_source, actual_cost_raw_json,
  pricebook_version, billing_reconciled_at, error_code,
  started_at, finished_at, created_at
) VALUES (
  1, 'req_xai_video', 'xai', 'grok-imagine-video', 'video', 'local_box', 'local_usage_only',
  '', 'ok', :units, NULL,
  12345, 'USD', 'response.usage.cost_in_usd_ticks', :raw_cost,
  'provider_response', NULL, '',
  :ts, :ts, :ts
)
"""
                    ),
                    {
                        "units": json.dumps({"video_second": 4, "cost_in_usd_ticks": 123450000}),
                        "raw_cost": json.dumps({"usage": {"cost_in_usd_ticks": 123450000}}),
                        "ts": created_at,
                    },
                )

            report = LocalMeteringService(engine).report(days=1, limit=10)
            item = report["items"][0]

            self.assertTrue(item["has_actual_cost"])
            self.assertEqual(item["actual_cost_micros"], 12345)
            self.assertEqual(item["cost_basis"], "actual")
            self.assertEqual(item["cost_micros"], 12345)
            self.assertEqual(item["estimated_cost_micros"], 200000)
            self.assertEqual(item["charge_micros"], 480000)
            self.assertEqual(item["profit_micros"], 480000 - 12345)
            self.assertEqual(item["units"], {"video_second": 4.0})
            self.assertEqual(report["totals"]["actual_cost_count"], 1)
            self.assertEqual(report["totals"]["cost_basis_counts"]["actual"], 1)
            self.assertGreater(len(report["pricebook"]), 0)
        finally:
            engine.dispose()

    def test_report_does_not_guess_billable_units_when_units_are_empty(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            created_at = now_ms()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
INSERT INTO local_usage_log (
  id, request_id, provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros, error_code,
  started_at, finished_at, created_at
) VALUES (
  1, 'req_missing_units', 'openai', 'gpt-image-1', 'image', 'local_box', 'local_usage_only',
  '', 'ok', :units, NULL, '', :ts, :ts, :ts
)
"""
                    ),
                    {"units": json.dumps({}), "ts": created_at},
                )

            report = LocalMeteringService(engine).report(days=1, limit=10)

            self.assertEqual(report["totals"]["request_count"], 1)
            self.assertEqual(report["totals"]["units"], {})
            self.assertEqual(report["totals"]["cost_micros"], 0)
            self.assertEqual(report["totals"]["sell_micros"], 0)
            self.assertEqual(report["items"][0]["raw_units"], {})
        finally:
            engine.dispose()

    def test_report_prices_kling_video_seconds_from_model_pricebook(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            created_at = now_ms()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
INSERT INTO local_usage_log (
  id, request_id, provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros, error_code,
  started_at, finished_at, created_at
) VALUES (
  1, 'req_kling_video', 'kling', 'kling-v3-omni', 'video', 'local_box', 'local_usage_only',
  'direct', 'ok', :units, NULL, '', :ts, :ts, :ts
)
"""
                    ),
                    {"units": json.dumps({"video_second": 5}), "ts": created_at},
                )

            report = LocalMeteringService(engine).report(days=1, limit=10)
            item = report["items"][0]

            self.assertEqual(item["units"], {"video_second": 5.0})
            self.assertEqual(item["estimated_cost_micros"], 560000)
            self.assertEqual(item["charge_micros"], 1200000)
            self.assertEqual(item["unit_lines"][0]["price_source"], "model")
        finally:
            engine.dispose()

    def test_report_prices_storyboard_tts_preview_video_and_lipsync_units(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            created_at = now_ms()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
INSERT INTO local_usage_log (
  id, request_id, provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros, error_code,
  started_at, finished_at, created_at
) VALUES
  (1, 'req_qwen_tts', 'qwen', 'qwen3-tts-instruct-flash', 'tts', 'local_box', 'local_usage_only', '', 'ok', :qwen_units, NULL, '', :ts, :ts, :ts),
  (2, 'req_grok_preview', 'xai', 'grok-imagine-video-1.5-preview', 'video', 'local_box', 'local_usage_only', '', 'ok', :video_units, NULL, '', :ts, :ts, :ts),
  (3, 'req_heygen_lipsync', 'heygen', 'heygen-lipsync-precision', 'lipsync', 'local_box', 'local_usage_only', '', 'ok', :lipsync_units, NULL, '', :ts, :ts, :ts)
"""
                    ),
                    {
                        "qwen_units": json.dumps({"character": 100, "audio_second_observed": 12.5, "request": 1}),
                        "video_units": json.dumps({"video_1080p_second": 6, "prompt_character": 300, "request": 1}),
                        "lipsync_units": json.dumps({"request": 1}),
                        "ts": created_at,
                    },
                )

            report = LocalMeteringService(engine).report(days=1, limit=10)
            by_key = {(item["provider"], item["model_id"], item["modality"]): item for item in report["items"]}

            self.assertEqual(by_key[("qwen", "qwen3-tts-instruct-flash", "tts")]["estimated_cost_micros"], 300)
            self.assertEqual(by_key[("xai", "grok-imagine-video-1.5-preview", "video")]["estimated_cost_micros"], 1500000)
            self.assertEqual(by_key[("heygen", "heygen-lipsync-precision", "lipsync")]["estimated_cost_micros"], 80000)
            self.assertEqual(report["totals"]["unpriced_count"], 0)
        finally:
            engine.dispose()

    def test_report_prices_local_artifact_units(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            created_at = now_ms()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
INSERT INTO local_usage_log (
  id, request_id, task_id, attempt_id, step_id, idempotency_key,
  provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros, error_code,
  started_at, finished_at, created_at
) VALUES (
  1, 'artifact:9:02_02', '5', '9', '02_02', 'artifact:9:02_02',
  'analysis_v1', '02_02_VideoSRTFrame', 'local_artifact', 'local_box', 'local_usage_only',
  '', 'ok', :units, NULL, '',
  :ts, :ts, :ts
)
"""
                    ),
                    {"units": json.dumps({"artifact_json_kb": 10, "artifact_image_kb": 100, "artifact_wav_kb": 1000}), "ts": created_at},
                )

            report = LocalMeteringService(engine).report(days=1, limit=10)
            item = report["items"][0]

            self.assertEqual(item["task_id"], "5")
            self.assertEqual(item["attempt_id"], "9")
            self.assertEqual(item["step_id"], "02_02")
            self.assertEqual(item["units"]["artifact_json_kb"], 10.0)
            self.assertEqual(item["charge_micros"], 4500)
            self.assertEqual(item["cost_micros"], 0)
            self.assertEqual(item["profit_micros"], 4500)
            self.assertIn("local_artifact", {row["modality"] for row in report["by_modality"]})
        finally:
            engine.dispose()

    def test_task_report_defaults_to_all_attempts_and_groups_actions(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            created_at = now_ms()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
INSERT INTO sessions (
  id, source, group_id, title, status, workspace_dir, created_at, updated_at
) VALUES (
  10, 'test', 'default', 'Task title', 'waiting_input', '/tmp/task-10', :ts, :ts
)
"""
                    ),
                    {"ts": created_at},
                )
                conn.execute(
                    text(
                        """
INSERT INTO openclip_tasks (
  id, session_id, status, latest_attempt_id, run_model_provider, run_model_id, created_at, updated_at
) VALUES (
  116, 10, 'completed', 158, 'openai', 'gpt-5.5', :ts, :ts
)
"""
                    ),
                    {"ts": created_at},
                )
                conn.execute(
                    text(
                        """
INSERT INTO openclip_attempts (
  id, task_id, session_id, attempt_no, status, created_at, started_at, finished_at
) VALUES
  (154, 116, 10, 2, 'completed', :old_ts, :old_ts, :old_done),
  (158, 116, 10, 3, 'completed', :new_ts, :new_ts, :new_done)
"""
                    ),
                    {"old_ts": created_at - 10_000, "old_done": created_at - 5_000, "new_ts": created_at, "new_done": created_at + 1_000},
                )
                conn.execute(
                    text(
                        """
INSERT INTO local_usage_log (
  id, request_id, task_id, attempt_id, step_id, idempotency_key,
  provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros,
  actual_cost_micros, actual_cost_currency, actual_cost_source, actual_cost_raw_json,
  error_code, started_at, finished_at, created_at
) VALUES
  (
    1, 'tts-1', '116', '154', '03_02', 'model:116:154:03_02:tts-1',
    'google', 'gemini-3.1-flash-tts-preview', 'tts', 'local_box', 'local_usage_only',
    '', 'ok', :tts_units, NULL,
    NULL, NULL, NULL, NULL,
    '', :old_ts, :old_ts, :old_ts
  ),
  (
    2, 'image-1', '116', '154', '05_02', 'model:116:154:05_02:image-1',
    'xai', 'grok-imagine-image', 'image', 'local_box', 'local_usage_only',
    '', 'ok', :image_units, NULL,
    20000, 'USD', 'response.usage.cost_in_usd_ticks', :raw_cost,
    '', :new_ts, :new_ts, :new_ts
  )
"""
                    ),
                    {
                        "tts_units": json.dumps({"promptTokenCount": 100, "candidatesTokenCount": 50}),
                        "image_units": json.dumps({"image": 1, "cost_in_usd_ticks": 200000000}),
                        "raw_cost": json.dumps({"usage": {"cost_in_usd_ticks": 200000000}}),
                        "old_ts": created_at - 9_000,
                        "new_ts": created_at + 500,
                    },
                )

            report = LocalMeteringService(engine).task_report(116)

            self.assertTrue(report["found"])
            self.assertEqual(report["attempt_scope"]["mode"], "all")
            self.assertEqual(report["task"]["latest_attempt_id"], 158)
            self.assertEqual(report["totals"]["request_count"], 2)
            self.assertEqual(report["totals"]["actual_cost_micros"], 20000)
            self.assertEqual(report["totals"]["units"]["input_token"], 100.0)
            self.assertEqual(report["totals"]["units"]["output_token"], 50.0)
            self.assertEqual(report["totals"]["units"]["image"], 1.0)
            self.assertEqual({row["step_id"] for row in report["by_action"]}, {"03_02", "05_02"})
            warning_codes = {item["code"] for item in report["warnings"]}
            self.assertIn("latest_attempt_has_no_usage", warning_codes)
            self.assertIn("usage_after_latest_start_attributed_to_older_attempt", warning_codes)
        finally:
            engine.dispose()

    def test_task_report_latest_scope_returns_empty_with_warning(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            created_at = now_ms()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
INSERT INTO sessions (
  id, source, group_id, title, status, workspace_dir, created_at, updated_at
) VALUES (
  10, 'test', 'default', 'Task title', 'waiting_input', '/tmp/task-10', :ts, :ts
)
"""
                    ),
                    {"ts": created_at},
                )
                conn.execute(
                    text(
                        """
INSERT INTO openclip_tasks (
  id, session_id, status, latest_attempt_id, created_at, updated_at
) VALUES (
  116, 10, 'completed', 158, :ts, :ts
)
"""
                    ),
                    {"ts": created_at},
                )
                conn.execute(
                    text(
                        """
INSERT INTO openclip_attempts (
  id, task_id, session_id, attempt_no, status, created_at
) VALUES (
  158, 116, 10, 3, 'completed', :ts
)
"""
                    ),
                    {"ts": created_at},
                )

            report = LocalMeteringService(engine).task_report(116, attempt="latest")

            self.assertEqual(report["attempt_scope"]["mode"], "latest")
            self.assertEqual(report["attempt_scope"]["attempt_ids"], [158])
            self.assertEqual(report["item_count"], 0)
            self.assertEqual(report["totals"]["request_count"], 0)
            self.assertIn("latest_attempt_has_no_usage", {item["code"] for item in report["warnings"]})
        finally:
            engine.dispose()

    def test_task_report_specific_attempt_does_not_emit_latest_scope_warning(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            created_at = now_ms()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
INSERT INTO sessions (
  id, source, group_id, title, status, workspace_dir, created_at, updated_at
) VALUES (
  10, 'test', 'default', 'Task title', 'waiting_input', '/tmp/task-10', :ts, :ts
)
"""
                    ),
                    {"ts": created_at},
                )
                conn.execute(
                    text(
                        """
INSERT INTO openclip_tasks (
  id, session_id, status, latest_attempt_id, created_at, updated_at
) VALUES (
  116, 10, 'completed', 158, :ts, :ts
)
"""
                    ),
                    {"ts": created_at},
                )
                conn.execute(
                    text(
                        """
INSERT INTO openclip_attempts (
  id, task_id, session_id, attempt_no, status, created_at
) VALUES
  (154, 116, 10, 2, 'completed', :old_ts),
  (158, 116, 10, 3, 'completed', :new_ts)
"""
                    ),
                    {"old_ts": created_at - 10_000, "new_ts": created_at},
                )
                conn.execute(
                    text(
                        """
INSERT INTO local_usage_log (
  id, request_id, task_id, attempt_id, step_id, idempotency_key,
  provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros, error_code,
  started_at, finished_at, created_at
) VALUES (
  1, 'tts-1', '116', '154', '03_02', 'model:116:154:03_02:tts-1',
  'google', 'gemini-3.1-flash-tts-preview', 'tts', 'local_box', 'local_usage_only',
  '', 'ok', :tts_units, NULL, '',
  :old_ts, :old_ts, :old_ts
)
"""
                    ),
                    {"tts_units": json.dumps({"promptTokenCount": 100}), "old_ts": created_at - 9_000},
                )

            report = LocalMeteringService(engine).task_report(116, attempt="154")

            self.assertEqual(report["attempt_scope"]["mode"], "attempt")
            self.assertEqual(report["attempt_scope"]["attempt_ids"], [154])
            self.assertEqual(report["item_count"], 1)
            self.assertNotIn("latest_attempt_has_no_usage", {item["code"] for item in report["warnings"]})
        finally:
            engine.dispose()

    def test_artifact_file_kb_cap_uses_pricebook_sell_cap(self) -> None:
        raw_kb, billed_kb = capped_artifact_kb("artifact_json_kb", 1024 * 1024)

        self.assertEqual(raw_kb, 1024.0)
        self.assertEqual(billed_kb, 250.0)

    def test_local_usage_recorder_idempotency_key_dedupes_but_allows_nulls(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            recorder = LocalUsageRecorder(engine)

            first = recorder.record_with_result(provider="analysis_v1", model_id="01_VideoProbeMetadata", modality="local_artifact", units={"artifact_json_kb": 1}, task_id=1, attempt_id=2, step_id="01", idempotency_key="artifact:2:01")
            second = recorder.record_with_result(provider="analysis_v1", model_id="01_VideoProbeMetadata", modality="local_artifact", units={"artifact_json_kb": 99}, task_id=1, attempt_id=2, step_id="01", idempotency_key="artifact:2:01")
            recorder.record(provider="openai", model_id="gpt-5.5", modality="chat", units={"input_tokens": 1})
            recorder.record(provider="openai", model_id="gpt-5.5", modality="chat", units={"input_tokens": 1})

            with engine.connect() as conn:
                rows = conn.execute(text("SELECT idempotency_key, units_json FROM local_usage_log ORDER BY id")).mappings().all()

            self.assertTrue(first.inserted)
            self.assertFalse(second.inserted)
            self.assertEqual(first.local_usage_id, second.local_usage_id)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["idempotency_key"], "artifact:2:01")
            self.assertEqual(json.loads(rows[0]["units_json"]), {"artifact_json_kb": 1})
            self.assertIsNone(rows[1]["idempotency_key"])
            self.assertIsNone(rows[2]["idempotency_key"])
        finally:
            engine.dispose()

    def test_report_route_is_registered_and_returns_schema(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            router = build_local_metering_router(SimpleNamespace(engine=engine))
            route = next(item for item in router.routes if getattr(item, "path", "") == "/api/local-metering/report")

            payload = route.endpoint(days=30, limit=5)

            self.assertEqual(payload["schema_version"], "1.0")
            self.assertIn("totals", payload)
            self.assertIn("by_provider_model", payload)
            self.assertIn("items", payload)
        finally:
            engine.dispose()

    def test_task_report_route_is_registered_and_returns_schema(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            router = build_local_metering_router(SimpleNamespace(engine=engine))
            route = next(item for item in router.routes if getattr(item, "path", "") == "/api/local-metering/tasks/{task_id}")

            payload = route.endpoint(task_id=999, attempt="all", limit=5, include_items=True)

            self.assertEqual(payload["schema_version"], "1.0")
            self.assertFalse(payload["found"])
            self.assertIn("totals", payload)
            self.assertIn("warnings", payload)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
