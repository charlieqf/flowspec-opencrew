from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.koubo.koubo_storyboard import video_plan_execution_state_services as services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.constants import VIDEO_PLAN_EXECUTION_STATE_REL  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.io_utils import read_json, write_json  # noqa: E402


def text(value: Any, fallback: str = "") -> str:
    raw = str(value if value is not None else "").strip()
    return raw or fallback


class PendingJob:
    def done(self) -> bool:
        return False

    def cancelled(self) -> bool:
        return False


class KouboStoryboardVideoExecutionStateContractTest(unittest.TestCase):
    def test_running_state_without_active_job_becomes_failed_after_grace_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            old_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
            write_json(
                workspace / VIDEO_PLAN_EXECUTION_STATE_REL,
                {
                    "schema_version": "koubo_video_plan_execution_state_0.1",
                    "job_id": "vp_exec_lost",
                    "source_plan_hash": "hash-1",
                    "status": "running",
                    "started_at": old_time,
                    "updated_at": old_time,
                    "current_segment_id": "segment-1",
                    "current_step": "sync",
                    "segments": {
                        "segment-1": {
                            "segment_id": "segment-1",
                            "status": "running",
                            "steps": {"sync": {"status": "running_generate", "updated_at": old_time}},
                        }
                    },
                    "error": "",
                },
            )
            sc = SimpleNamespace(
                text=text,
                read_json=read_json,
                write_json=write_json,
                video_plan_execution_jobs={},
                video_plan_with_hash=lambda payload, sc: {"plan_hash": payload.get("plan_hash", "hash-1")},
                video_plan_artifact_status=lambda workspace, plan, sc: {"segments": {}},
                redact_payload=lambda payload: payload,
            )

            self.assertFalse(services.video_plan_execution_is_running(read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL), sc=sc))
            payload = services.video_plan_execution_payload(workspace, {"plan_hash": "hash-1"}, sc=sc)

            state = payload["execution_state"]
            self.assertEqual(state["status"], "failed")
            self.assertTrue(state["orphaned"])
            self.assertIn("worker job is no longer active", state["error"])
            self.assertEqual(state["segments"]["segment-1"]["steps"]["sync"]["status"], "failed")
            self.assertEqual(read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL)["status"], "failed")

    def test_running_state_with_active_job_stays_running(self) -> None:
        now_value = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
        sc = SimpleNamespace(text=text, video_plan_execution_jobs={"vp_exec_active": PendingJob()})
        self.assertTrue(
            services.video_plan_execution_is_running(
                {"job_id": "vp_exec_active", "status": "running", "updated_at": now_value},
                sc=sc,
            )
        )

    def test_analysis_entrypoints_normalize_orphan_state_before_blocking(self) -> None:
        source = (REPO_ROOT / "backend/opcrew_backend/koubo/router.py").read_text(encoding="utf-8")

        self.assertGreaterEqual(source.count("normalize_existing_video_plan_execution_state(workspace)"), 2)
        self.assertIn('setattr(ctx, "koubo_video_plan_execution_jobs", shared_video_plan_jobs)', source)


if __name__ == "__main__":
    unittest.main()
