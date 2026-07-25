from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from sqlalchemy import create_engine, insert, select, update


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.db.schema import metadata, openclip_attempts, openclip_tasks, session_events, session_files, sessions, tool_asr_provider_configs  # noqa: E402
from opcrew_backend.koubo import router as openclip_router_module  # noqa: E402
from opcrew_backend.services import opencode_runtime as opencode_runtime_module  # noqa: E402


FAKE_SCRIPT = r'''
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


OUTPUTS = {
    "00": ["SessionContext/Variables.json", "S1_00_PrepareSessionVariables/Report/Result.json"],
    "01": ["S2_01_VideoProbeMetadata/Output/Video_Metadata.json", "S2_01_VideoProbeMetadata/Report/Result.json"],
    "02_01": ["SessionOutput/Audio_Reference.wav", "S3_02_01_AudioASR/Output/ASR_Segments.json", "S3_02_01_AudioASR/Report/Result.json"],
    "02_02": ["SessionOutput/subtitle/final_srt_frame_items.json", "SessionOutput/visual/srt_frame_map.json", "S4_02_02_VideoSRTFrame/Report/Result.json"],
    "03_01": ["SessionOutput/tts/tts_builder_candidates.json", "S5_03_01_TTSBuilderG/Report/Result.json"],
    "03_02": ["SessionOutput/tts/tts_builder_candidates.json", "S5_03_02_TTSBuilderQuick/Report/Result.json"],
    "03_03": ["SessionOutput/tts/tts_builder_candidates.json", "S5_03_03_TTSBuilderQuickAdv/Report/Result.json"],
    "04_01": ["SessionOutput/subtitle/rewritten_srt_items.json", "S6_04_01_SRTRewrite/Report/Result.json"],
    "04_02": ["SessionOutput/storyboard/srt_storyboard.json", "S7_04_02_StoryBoard/Report/Result.json"],
    "04_03": ["SessionOutput/storyboard/srt_storyboard.json", "S7_04_03_StoryBoardQuick/Report/Result.json"],
}


def step_id_for_name(name: str) -> str:
    for step_id in sorted(OUTPUTS, key=len, reverse=True):
        if name.startswith(step_id):
            return step_id
    return "00"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


parser = argparse.ArgumentParser()
parser.add_argument("--workspace", default="")
parser.add_argument("--print-json", action="store_true")
parser.add_argument("--task-id", default="")
parser.add_argument("--session-id", default="")
parser.add_argument("--attempt-id", default="")
parser.add_argument("--attempt-mode", default="")
parser.add_argument("--clip-mode", default="")
parser.add_argument("--selected-scheme", default="")
parser.add_argument("--force", action="store_true")
parser.add_argument("--resume", action="store_true")
parser.add_argument("--allow-cloud-asr-data-transfer", action="store_true")
parser.add_argument("--asr-mode", default="")
parser.add_argument("--database-url-env", default="")
parser.add_argument("--voice-catalog-dir", default="")
parser.add_argument("--model", default="")
parser.add_argument("--model-provider", default="")
parser.add_argument("--model-id", default="")
parser.add_argument("--providers", default="")
parser.add_argument("--stage1-count", default="")
parser.add_argument("--stage2-count", default="")
parser.add_argument("--final-count", default="")
parser.add_argument("--reference-start", default="")
parser.add_argument("--reference-duration", default="")
args, _ = parser.parse_known_args()

name = Path(sys.argv[0]).name
step_id = step_id_for_name(name)
workspace = Path(args.workspace or os.environ["OPENCREW_FAKE_WORKSPACE"])
sleep_step = os.environ.get("OPENCREW_FAKE_SLEEP_STEP", "")
if sleep_step == step_id:
    time.sleep(float(os.environ.get("OPENCREW_FAKE_SLEEP_SECONDS", "1.5")))

for index in range(int(os.environ.get("OPENCREW_FAKE_LOG_LINES", "3"))):
    print(f"stdout-{step_id}-{index}-" + ("x" * 80), flush=True)
print("stderr api_key=sk-fake-secret", file=sys.stderr, flush=True)

for rel in OUTPUTS[step_id]:
    write_json(workspace / rel, {"status": "completed", "step_id": step_id, "path": rel})
print(json.dumps({"status": "completed", "message": f"{step_id} completed", "outputs": OUTPUTS[step_id]}), flush=True)
'''


class FakeOpenCodeSessionClient:
    def __init__(self, **_: Any) -> None:
        pass

    def providers(self) -> dict[str, Any]:
        return {
            "connected": ["openai"],
            "default": {"openai": "gpt-5.5"},
            "all": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "models": {
                        "gpt-5.5": {
                            "id": "gpt-5.5",
                            "name": "GPT 5.5",
                            "reasoning": True,
                            "limit": {"context": 1000},
                            "modalities": {"input": ["text"]},
                        }
                    },
                }
            ],
        }


class FakeSessionRepo:
    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def get(self, session_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(sessions).where(sessions.c.id == session_id)).mappings().first()
        return dict(row) if row else None

    def update(self, session_id: int, **fields: Any) -> None:
        if not fields:
            return
        with self.engine.begin() as conn:
            conn.execute(update(sessions).where(sessions.c.id == session_id).values(**fields))

    def upsert_file(self, session_id: int, path: str, kind: str, size: int, origin: str, downloadable: int, updated_at: int) -> None:
        with self.engine.begin() as conn:
            existing = conn.execute(select(session_files.c.id).where(session_files.c.session_id == session_id, session_files.c.path == path)).scalar()
            values = {"session_id": session_id, "path": path, "kind": kind, "size": size, "origin": origin, "downloadable": downloadable, "updated_at": updated_at}
            if existing:
                conn.execute(update(session_files).where(session_files.c.id == existing).values(**values))
            else:
                conn.execute(insert(session_files).values(**values))


class FakeSessionEventService:
    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def add_event(self, session_id: int, kind: str, payload: dict[str, Any], workflow_id: str = "", **fields: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(session_events).values(
                    session_id=session_id,
                    kind=kind,
                    payload=json.dumps(payload or {}, ensure_ascii=False),
                    workflow_id=workflow_id,
                    family=fields.get("family"),
                    task_id=fields.get("task_id"),
                    attempt_id=fields.get("attempt_id"),
                    tool_id=fields.get("tool_id"),
                    step_id=fields.get("step_id"),
                    created_at=int(time.time() * 1000),
                )
            )


class FakeWorkspaceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def sessions_root(self) -> Path:
        return self.root / "sessions"


async def asgi_request(app: FastAPI, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    payload = json.dumps(body or {}).encode("utf-8") if body is not None else b""
    sent: list[dict[str, Any]] = []
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("test", 1),
        "server": ("test", 80),
        "root_path": "",
        # This focused app mounts the runner router without the production auth
        # middleware. Seed the authenticated admin role explicitly so model
        # policy normalization cannot rewrite privileged runner options as an
        # unauthenticated/default user before the ASR authorization contract is
        # evaluated.
        "state": {"opencrew_auth_role": "admin"},
    }
    await app(scope, receive, send)
    status = int(next(message.get("status") for message in sent if message["type"] == "http.response.start"))
    response_body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, json.loads(response_body.decode("utf-8") or "{}")


def request(app: FastAPI, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    return asyncio.run(asgi_request(app, method, path, body))


class AnalysisV1ExecutableRunnerContractTest(unittest.TestCase):
    def test_local_openclip_runner_path_exists_under_backend_scripts(self) -> None:
        self.assertEqual(openclip_router_module.OPENCLIP_RUNNER, BACKEND_ROOT / "scripts" / "openclip_analysis_runner.py")
        self.assertTrue(openclip_router_module.OPENCLIP_RUNNER.exists())

    def setUp(self) -> None:
        # Snapshot pre-existing threads so tearDown can drain any run worker this
        # test spawns (see tearDown): the run-to-storyboard endpoint starts a
        # daemon thread, and if it outlives the test it races later tests on
        # shared DB/module state (observed as an intermittent IndexError in an
        # unrelated dance_mimic test under CI).
        self._threads_at_setup = {t.ident for t in threading.enumerate()}
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "sessions" / "1" / "workspace"
        self.workspace.mkdir(parents=True)
        self.fake_tools = self.root / "fake_analysis_v1"
        self.fake_tools.mkdir()
        # The runner writes from a background thread while the test polls from the
        # request thread.  A StaticPool-backed in-memory database makes both paths
        # concurrently reuse one sqlite3 connection, which can corrupt SQLAlchemy's
        # transient Row view and fail intermittently while converting row._mapping.
        # A per-test file database gives each thread its own pooled connection while
        # preserving the same SQLite schema/transaction behaviour under test.
        self.engine = create_engine(
            f"sqlite:///{self.root / 'analysis-v1-contract.sqlite3'}",
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        metadata.create_all(self.engine)
        self._insert_task()
        self._write_fake_scripts()
        self._patch_router_module()
        self._set_env()
        self.app = self._build_app()

    def tearDown(self) -> None:
        # Drain run-worker threads spawned by this test BEFORE restoring globals/
        # env or disposing the engine, so a worker never touches a torn-down
        # engine/workspace or leaks shared-state mutations into a later test.
        for worker in threading.enumerate():
            if worker.ident not in self._threads_at_setup and worker is not threading.current_thread():
                worker.join(timeout=20)
        openclip_router_module.OpenCodeSessionClient = self.old_client
        opencode_runtime_module.OpenCodeSessionClient = self.old_runtime_client
        openclip_router_module.ANALYSIS_V1_ROOT = self.old_root
        openclip_router_module.ANALYSIS_V1_TTS_BUILDER_G = self.old_tts_g
        openclip_router_module.ANALYSIS_V1_TTS_BUILDER_QUICK = self.old_tts_quick
        openclip_router_module.ANALYSIS_V1_TTS_BUILDER_QUICK_ADV = self.old_tts_quick_adv
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.engine.dispose()
        # A run started in a test may still have a background worker writing into
        # the workspace when tearDown fires; that races TemporaryDirectory.cleanup()
        # and raises "Directory not empty" under slower CI timing. Retry briefly,
        # then remove tolerantly so cleanup never flakes the gate.
        for _ in range(10):
            try:
                self.tmp.cleanup()
                break
            except OSError:
                time.sleep(0.2)
        else:
            shutil.rmtree(self.root, ignore_errors=True)

    def _insert_task(self) -> None:
        now = int(time.time() * 1000)
        with self.engine.begin() as conn:
            conn.execute(
                insert(sessions).values(
                    id=1,
                    source="openclip-analysis",
                    group_id="openclip-analysis",
                    title="Analysis V1 fake",
                    status="draft",
                    opencode_session_id="fake-opencode",
                    workspace_dir=str(self.workspace),
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                insert(openclip_tasks).values(
                    id=1,
                    session_id=1,
                    status="draft",
                    reference_video_path="/tmp/fake.mp4",
                    industry="餐饮",
                    persona="创始人",
                    target_audience="老板",
                    product_info="产品",
                    constraints="",
                    analysis_goal="提取整体公式",
                    video_formula="Hook/Trust/CTA",
                    simple_prompt="",
                    final_prompt="",
                    rewrite_simple_prompt="",
                    rewrite_final_prompt="rewrite prompt",
                    storyboard_simple_prompt="",
                    storyboard_final_prompt="storyboard prompt",
                    storyboard_quick_config_json="{}",
                    run_model_provider="openai",
                    run_model_id="gpt-5.5",
                    created_at=now,
                    updated_at=now,
                )
            )

    def _write_fake_scripts(self) -> None:
        for name in (
            "00_PrepareSessionVariables.py",
            "01_VideoProbeMetadata.py",
            "02_01_AudioASR.py",
            "02_02_VideoSRTFrame.py",
            "03_01_TTSBuilderG.py",
            "03_02_TTSBuilderQuick.py",
            "03_03_TTSBuilderQuickAdv.py",
            "04_01_SRTRewrite.py",
            "04_02_StoryBoard.py",
            "04_03_StoryBoardQuick.py",
        ):
            path = self.fake_tools / name
            path.write_text(FAKE_SCRIPT, encoding="utf-8")

    def _patch_router_module(self) -> None:
        self.old_client = openclip_router_module.OpenCodeSessionClient
        # Prompt-model resolution builds the OpenCode client via
        # services.opencode_runtime.opencode_client_for_context, so the fake must be
        # patched there too (patching only the router-module name lets a real network
        # call leak through — the source of the runner contract failures).
        self.old_runtime_client = opencode_runtime_module.OpenCodeSessionClient
        self.old_root = openclip_router_module.ANALYSIS_V1_ROOT
        self.old_tts_g = openclip_router_module.ANALYSIS_V1_TTS_BUILDER_G
        self.old_tts_quick = openclip_router_module.ANALYSIS_V1_TTS_BUILDER_QUICK
        self.old_tts_quick_adv = openclip_router_module.ANALYSIS_V1_TTS_BUILDER_QUICK_ADV
        openclip_router_module.OpenCodeSessionClient = FakeOpenCodeSessionClient
        opencode_runtime_module.OpenCodeSessionClient = FakeOpenCodeSessionClient
        openclip_router_module.ANALYSIS_V1_ROOT = self.fake_tools
        openclip_router_module.ANALYSIS_V1_TTS_BUILDER_G = self.fake_tools / "03_01_TTSBuilderG.py"
        openclip_router_module.ANALYSIS_V1_TTS_BUILDER_QUICK = self.fake_tools / "03_02_TTSBuilderQuick.py"
        openclip_router_module.ANALYSIS_V1_TTS_BUILDER_QUICK_ADV = self.fake_tools / "03_03_TTSBuilderQuickAdv.py"

    def _set_env(self) -> None:
        keys = [
            "OPENCREW_ANALYSIS_V1_PYTHON",
            "OPENCREW_FAKE_WORKSPACE",
            "OPENCREW_FAKE_SLEEP_STEP",
            "OPENCREW_FAKE_SLEEP_SECONDS",
            "OPENCREW_FAKE_LOG_LINES",
        ]
        self.old_env = {key: os.environ.get(key) for key in keys}
        os.environ["OPENCREW_ANALYSIS_V1_PYTHON"] = sys.executable
        os.environ["OPENCREW_FAKE_WORKSPACE"] = str(self.workspace)
        os.environ.pop("OPENCREW_FAKE_SLEEP_STEP", None)
        os.environ["OPENCREW_FAKE_SLEEP_SECONDS"] = "1.2"
        os.environ["OPENCREW_FAKE_LOG_LINES"] = "5"

    def _build_app(self) -> FastAPI:
        ctx = SimpleNamespace(
            engine=self.engine,
            data_dir=self.root,
            config=SimpleNamespace(database_url="sqlite://", frontend_url="http://127.0.0.1:18081/"),
            session_repo=FakeSessionRepo(self.engine),
            session_event_service=FakeSessionEventService(self.engine),
            workspace_store=FakeWorkspaceStore(self.root),
            get_setting=lambda key, default=None: {"opencode.base_url": "http://fake", "opencode.username": "u", "opencode.password": "p"}.get(key, default),
        )
        app = FastAPI()
        app.include_router(openclip_router_module.build_openclip_router(ctx))
        return app

    def precreate_dependencies(self, step_id: str = "02_02") -> None:
        paths = [
            "SessionContext/Variables.json",
            "S2_01_VideoProbeMetadata/Output/Video_Metadata.json",
            "S3_02_01_AudioASR/Output/ASR_Segments.json",
        ]
        if step_id in {"03_01", "03_02", "03_03", "04_01", "04_02", "04_03"}:
            paths.append("SessionOutput/subtitle/final_srt_frame_items.json")
        if step_id in {"02_02", "03_01", "03_02", "03_03", "04_01", "04_02", "04_03"}:
            paths.append("SessionOutput/Audio_Reference.wav")
        if step_id in {"04_02", "04_03"}:
            paths.append("SessionOutput/subtitle/rewritten_srt_items.json")
        for rel in paths:
            path = self.workspace / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"ok": True, "path": rel}), encoding="utf-8")

    def wait_for_status(self, attempt_id: int, statuses: set[str], timeout: float = 8.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            status, payload = request(self.app, "GET", f"/api/openclip/tasks/1/analysis-v1/run-to-storyboard/{attempt_id}")
            self.assertEqual(status, 200, payload)
            last = payload
            if str(payload.get("status")) in statuses:
                return payload
            time.sleep(0.1)
        self.fail(f"Timed out waiting for {statuses}; last={last}")

    def wait_for_current_step(self, attempt_id: int, step_id: str, timeout: float = 8.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            status, payload = request(self.app, "GET", f"/api/openclip/tasks/1/analysis-v1/run-to-storyboard/{attempt_id}")
            self.assertEqual(status, 200, payload)
            last = payload
            if payload.get("status") == "running" and payload.get("current_step_id") == step_id:
                return payload
            time.sleep(0.1)
        self.fail(f"Timed out waiting for current_step_id={step_id}; last={last}")

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        status, response = request(self.app, "POST", "/api/openclip/tasks/1/analysis-v1/run-to-storyboard", {
            "run_model_provider": "openai",
            "run_model_id": "gpt-5.5",
            "allow_cloud_asr_data_transfer": True,
            "tts_builder_mode": "quick",
            "storyboard_mode": "quick",
            **payload,
        })
        self.assertEqual(status, 200, response)
        return response

    def clear_reference_video(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(openclip_tasks).where(openclip_tasks.c.id == 1).values(reference_video_path=""))

    def test_run_to_storyboard_rejects_active_video_plan_execution(self) -> None:
        state_path = self.workspace / "SessionOutput/storyboard/video_plan_execution_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "schema_version": "koubo_video_plan_execution_state_0.1",
            "job_id": "active-video-job",
            "source_plan_hash": "active-video-plan",
            "status": "running",
        }), encoding="utf-8")

        status, response = request(self.app, "POST", "/api/openclip/tasks/1/analysis-v1/run-to-storyboard", {
            "run_model_provider": "openai",
            "run_model_id": "gpt-5.5",
            "allow_cloud_asr_data_transfer": True,
            "tts_builder_mode": "quick",
            "storyboard_mode": "quick",
        })

        self.assertEqual(status, 409, response)
        self.assertEqual(response["detail"]["code"], "active_video_plan_execution_exists")
        self.assertEqual(response["detail"]["job_id"], "active-video-job")
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["status"], "running")
        with self.engine.connect() as conn:
            self.assertIsNone(conn.execute(select(openclip_attempts.c.id)).first())

    def test_run_all_on_fresh_workspace_waits_for_02_01_reference_audio(self) -> None:
        started = self.start_run({"mode": "run_all"})

        completed = self.wait_for_status(int(started["attempt_id"]), {"completed"})
        quick_step = next(item for item in completed["steps"] if item["id"] == "03_02")

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(quick_step["status"], "completed")
        self.assertTrue((self.workspace / "SessionOutput" / "Audio_Reference.wav").is_file())

    def test_run_from_step_writes_run_state_and_recovers_from_file(self) -> None:
        self.precreate_dependencies("02_02")
        started = self.start_run({"mode": "run_from_step", "start_step_id": "02_02"})

        completed = self.wait_for_status(int(started["attempt_id"]), {"completed"})

        self.assertEqual(completed["plan"]["mode"], "run_from_step")
        self.assertEqual(completed["steps"][0]["status"], "reused")
        self.assertEqual(completed["steps"][1]["status"], "reused")
        self.assertEqual(completed["steps"][2]["status"], "reused")
        run_state_path = self.workspace / "SessionReport" / "tool_runs" / f"attempt_{started['attempt_id']}" / "run_state.json"
        self.assertTrue(run_state_path.is_file())

        recovered_app = self._build_app()
        status, recovered = request(recovered_app, "GET", f"/api/openclip/tasks/1/analysis-v1/run-to-storyboard/{started['attempt_id']}")
        self.assertEqual(status, 200, recovered)
        self.assertEqual(recovered["status"], "completed")
        self.assertGreater(len(recovered["steps"]), 0)

    def test_cloud_asr_rejects_local_default_provider_before_attempt_created(self) -> None:
        now_dt = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            conn.execute(
                insert(tool_asr_provider_configs).values(
                    name="default_asr_provider",
                    provider="local_whisper",
                    enabled=True,
                    priority=5,
                    model="small",
                    language="zh",
                    api_url="",
                    api_key_ref="local_whisper_key",
                    extra_json="{}",
                    created_at=now_dt,
                    updated_at=now_dt,
                )
            )

        status, response = request(self.app, "POST", "/api/openclip/tasks/1/analysis-v1/run-to-storyboard", {
            "run_model_provider": "openai",
            "run_model_id": "gpt-5.5",
            "asr_mode": "cloud",
            "allow_cloud_asr_data_transfer": True,
            "tts_builder_mode": "quick",
            "storyboard_mode": "quick",
        })

        self.assertEqual(status, 400, response)
        self.assertEqual(response["detail"]["code"], "cloud_asr_config_not_cloud")
        with self.engine.connect() as conn:
            self.assertIsNone(conn.execute(select(openclip_attempts.c.id)).first())

    def test_run_only_step_records_diagnostic_scope_and_bounded_log_tail(self) -> None:
        self.precreate_dependencies("04_01")
        os.environ["OPENCREW_FAKE_LOG_LINES"] = "500"
        started = self.start_run({"mode": "run_only_step", "run_only_step_id": "04_01"})

        completed = self.wait_for_status(int(started["attempt_id"]), {"completed"})
        step = next(item for item in completed["steps"] if item["id"] == "04_01")

        self.assertEqual(completed["plan"]["mode"], "run_only_step")
        self.assertEqual(completed["plan"]["options"]["billing_scope"], "diagnostic")
        self.assertLessEqual(len(step.get("stdout_tail") or ""), 16000)
        self.assertNotIn("stdout-04_01-0-", step.get("stdout_tail") or "")
        self.assertIn("stdout-04_01-499-", step.get("stdout_tail") or "")
        self.assertNotIn("sk-fake-secret", step.get("stderr_tail") or "")
        with self.engine.connect() as conn:
            row = conn.execute(select(session_events.c.payload).where(session_events.c.kind == "analysis_v1.run_to_storyboard.attempt.created", session_events.c.attempt_id == int(started["attempt_id"]))).first()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0])["billing_scope"], "diagnostic")

    def test_run_only_quick_tts_builder_requires_reference_audio(self) -> None:
        self.precreate_dependencies("03_02")
        (self.workspace / "SessionOutput" / "Audio_Reference.wav").unlink()

        started = self.start_run({"mode": "run_only_step", "run_only_step_id": "03_02"})
        step = next(item for item in started["steps"] if item["id"] == "03_02")

        self.assertEqual(started["status"], "blocked")
        self.assertEqual(step["status"], "blocked")
        self.assertEqual(step["blocked_reasons"][0]["code"], "analysis_v1_dependency_missing")
        self.assertIn("SessionOutput/Audio_Reference.wav", step["blocked_reasons"][0]["missing"])

    def test_run_only_quick_tts_builder_accepts_uploaded_audio_without_reference_video(self) -> None:
        self.clear_reference_video()
        self.precreate_dependencies("03_02")

        started = self.start_run({"mode": "run_only_step", "run_only_step_id": "03_02"})
        completed = self.wait_for_status(int(started["attempt_id"]), {"completed"})
        step = next(item for item in completed["steps"] if item["id"] == "03_02")

        self.assertFalse((self.workspace / "SessionOutput/subtitle/source_script.txt").exists())
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(step["status"], "completed")
        self.assertTrue((self.workspace / "SessionOutput/tts/tts_builder_candidates.json").is_file())

    def test_run_only_builder_g_tts_builder_accepts_uploaded_audio_without_reference_video(self) -> None:
        self.clear_reference_video()
        self.precreate_dependencies("03_01")

        started = self.start_run({
            "mode": "run_only_step",
            "run_only_step_id": "03_01",
            "tts_builder_mode": "builder_g",
        })
        completed = self.wait_for_status(int(started["attempt_id"]), {"completed"})
        step = next(item for item in completed["steps"] if item["id"] == "03_01")

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(step["status"], "completed")
        self.assertTrue((self.workspace / "S5_03_01_TTSBuilderG/Report/Result.json").is_file())

    def test_free_rewrite_selected_steps_explains_missing_02_02_output(self) -> None:
        started = self.start_run({
            "mode": "run_selected_steps",
            "selected_step_ids": ["00", "04_01", "04_02"],
            "rewrite_mode": "free",
            "storyboard_mode": "model",
            "include_tts_builder": False,
            "tts_builder_mode": "skip",
        })
        step = next(item for item in started["steps"] if item["id"] == "04_01")
        block = step["blocked_reasons"][0]

        self.assertEqual(started["status"], "blocked")
        self.assertEqual(step["status"], "blocked")
        self.assertEqual(block["code"], "analysis_v1_dependency_missing")
        self.assertIn("SessionOutput/subtitle/final_srt_frame_items.json", block["missing"])
        self.assertIn("02_02 字幕帧对齐", block["message"])
        self.assertIn("02_02 字幕帧对齐", block["suggested_action"])

    def test_run_only_quick_adv_tts_builder_uses_03_03_outputs(self) -> None:
        self.precreate_dependencies("03_03")
        started = self.start_run({
            "mode": "run_only_step",
            "run_only_step_id": "03_03",
            "tts_builder_mode": "quick_adv",
        })

        completed = self.wait_for_status(int(started["attempt_id"]), {"completed"})
        step = next(item for item in completed["steps"] if item["id"] == "03_03")

        self.assertEqual(step["status"], "completed")
        self.assertTrue((self.workspace / "SessionOutput/tts/tts_builder_candidates.json").is_file())
        self.assertTrue((self.workspace / "S5_03_03_TTSBuilderQuickAdv/Report/Result.json").is_file())

    def test_pause_before_step_and_resume_same_attempt(self) -> None:
        self.precreate_dependencies("02_02")
        started = self.start_run({"mode": "run_from_step", "start_step_id": "02_02", "pause_before_step_id": "04_01"})
        attempt_id = int(started["attempt_id"])

        paused = self.wait_for_status(attempt_id, {"paused"})
        self.assertEqual(paused["pause_before_step_id"], "04_01")
        self.assertEqual(next(item for item in paused["steps"] if item["id"] == "04_01")["status"], "pending")

        status, resumed = request(self.app, "POST", f"/api/openclip/tasks/1/analysis-v1/run-to-storyboard/{attempt_id}/resume", {"reason": "test"})
        self.assertEqual(status, 200, resumed)
        completed = self.wait_for_status(attempt_id, {"completed"})
        self.assertEqual(completed["attempt_id"], attempt_id)
        self.assertEqual(next(item for item in completed["steps"] if item["id"] == "04_01")["status"], "completed")

    def test_stop_after_current_marks_remaining_cancelled(self) -> None:
        self.precreate_dependencies("04_01")
        os.environ["OPENCREW_FAKE_SLEEP_STEP"] = "04_01"
        started = self.start_run({"mode": "run_from_step", "start_step_id": "04_01"})
        attempt_id = int(started["attempt_id"])
        running = self.wait_for_current_step(attempt_id, "04_01")
        self.assertEqual(running["current_step_id"], "04_01")

        status, stopping = request(self.app, "POST", f"/api/openclip/tasks/1/analysis-v1/run-to-storyboard/{attempt_id}/stop", {"mode": "graceful", "reason": "test"})
        self.assertEqual(status, 200, stopping)
        cancelled = self.wait_for_status(attempt_id, {"cancelled"})

        self.assertEqual(next(item for item in cancelled["steps"] if item["id"] == "04_01")["status"], "completed")
        self.assertEqual(next(item for item in cancelled["steps"] if item["id"] == "04_03")["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
