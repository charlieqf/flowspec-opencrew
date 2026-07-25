from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
openclip_backend_path = str(REPO_ROOT / "backend")
for path in (backend_path, openclip_backend_path):
    if path not in sys.path:
        sys.path.insert(0, path)

from opcrew_backend.koubo.analysis_v1_artifact_billing import (  # noqa: E402
    collect_billable_artifacts,
    has_non_artifact_usage,
    record_local_artifacts,
    step_result_provider,
)
from opcrew_backend.db.schema import metadata  # noqa: E402
from opcrew_backend.services.local_usage import LocalUsageRecorder  # noqa: E402


class AnalysisV1ArtifactBillingContractTest(unittest.TestCase):
    def test_collect_billable_artifacts_dedupes_patterns_and_caps_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            json_path = workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json"
            image_path = workspace / "SessionOutput" / "visual" / "srt_frames" / "001.jpg"
            text_path = workspace / "SessionOutput" / "storyboard" / "ignore.txt"
            json_path.parent.mkdir(parents=True)
            image_path.parent.mkdir(parents=True)
            json_path.write_bytes(b"{" + b"a" * (1024 * 1024) + b"}")
            image_path.write_bytes(b"i" * 2048)
            text_path.write_text("ignore", encoding="utf-8")

            units, artifacts = collect_billable_artifacts(
                workspace,
                [
                    "SessionOutput/storyboard/srt_storyboard.json",
                    "SessionOutput/storyboard/*.json",
                    "SessionOutput/visual/srt_frames/*.jpg",
                    "SessionOutput/storyboard/*.txt",
                ],
            )

            self.assertEqual(len(artifacts), 2)
            self.assertEqual(units["artifact_json_kb"], 250.0)
            self.assertEqual(units["artifact_image_kb"], 2.0)
            self.assertEqual([item["path"] for item in artifacts], ["SessionOutput/storyboard/srt_storyboard.json", "SessionOutput/visual/srt_frames/001.jpg"])

    def test_step_provider_reads_parsed_config_or_02_01_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            self.assertEqual(step_result_provider(workspace, "02_01", {"provider_config": {"provider": "local_whisper"}}), "local_whisper")

            output_path = workspace / "SessionContext" / "ASR_Segments.json"
            output_path.parent.mkdir(parents=True)
            output_path.write_text('{"provider":"aliyun_bailian_fun_asr"}', encoding="utf-8")

            self.assertEqual(step_result_provider(workspace, "02_01", {}), "aliyun_bailian_fun_asr")

    def test_non_artifact_usage_safety_net_skips_artifact_billing(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
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
  1, 'api_req', '3', '7', '04_02', 'api:7:04_02',
  'openai', 'gpt-test', 'chat', 'local_box', 'local_usage_only',
  '', 'ok', '{}', NULL, '',
  1, 1, 1
)
"""
                    )
                )
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                path = workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json"
                path.parent.mkdir(parents=True)
                path.write_text("{}", encoding="utf-8")

                result = record_local_artifacts(
                    engine=engine,
                    recorder=LocalUsageRecorder(engine),
                    spec={"id": "04_02", "name": "04_02_StoryBoard", "artifact_billable": True, "billable_outputs": ["SessionOutput/storyboard/srt_storyboard.json"]},
                    task_id=3,
                    attempt_id=7,
                    workspace=workspace,
                    parsed={},
                    started_at=1,
                    finished_at=2,
                )

            self.assertTrue(has_non_artifact_usage(engine, 7, "04_02"))
            self.assertEqual(result, {"status": "skipped", "reason": "api_usage_already_metered"})
            with engine.connect() as conn:
                self.assertEqual(int(conn.execute(text("SELECT COUNT(*) FROM local_usage_log")).scalar_one()), 1)
        finally:
            engine.dispose()

    def test_02_01_cloud_provider_is_not_artifact_billable(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                wav = workspace / "SessionOutput" / "Audio_Reference.wav"
                wav.parent.mkdir(parents=True)
                wav.write_bytes(b"w" * 2048)

                result = record_local_artifacts(
                    engine=engine,
                    recorder=LocalUsageRecorder(engine),
                    spec={"id": "02_01", "name": "02_01_AudioASR", "artifact_billable": True, "billable_outputs": ["SessionOutput/Audio_Reference.wav"], "artifact_provider_allowlist": ["local_whisper"]},
                    task_id=3,
                    attempt_id=7,
                    workspace=workspace,
                    parsed={"provider_config": {"provider": "aliyun_bailian_fun_asr"}},
                    started_at=1,
                    finished_at=2,
                )

            self.assertEqual(result, {"status": "skipped", "reason": "provider_not_billable", "provider": "aliyun_bailian_fun_asr"})
            with engine.connect() as conn:
                self.assertEqual(int(conn.execute(text("SELECT COUNT(*) FROM local_usage_log")).scalar_one()), 0)
        finally:
            engine.dispose()

    def test_record_local_artifacts_reports_recorded_then_deduped(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        try:
            metadata.create_all(engine)
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                path = workspace / "SessionContext" / "Video_Metadata.json"
                path.parent.mkdir(parents=True)
                path.write_text("{}", encoding="utf-8")
                recorder = LocalUsageRecorder(engine)
                spec = {"id": "01", "name": "01_VideoProbeMetadata", "artifact_billable": True, "billable_outputs": ["SessionContext/Video_Metadata.json"]}

                first = record_local_artifacts(engine=engine, recorder=recorder, spec=spec, task_id=3, attempt_id=7, workspace=workspace, parsed={}, started_at=1, finished_at=2)
                second = record_local_artifacts(engine=engine, recorder=recorder, spec=spec, task_id=3, attempt_id=7, workspace=workspace, parsed={}, started_at=1, finished_at=2)

            self.assertEqual(first["status"], "recorded")
            self.assertEqual(second["status"], "deduped")
            self.assertEqual(first["idempotency_key"], "artifact:7:01")
            self.assertEqual(second["idempotency_key"], "artifact:7:01")
            with engine.connect() as conn:
                self.assertEqual(int(conn.execute(text("SELECT COUNT(*) FROM local_usage_log")).scalar_one()), 1)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
