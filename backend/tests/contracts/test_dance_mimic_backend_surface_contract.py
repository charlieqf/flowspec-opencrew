from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.db.schema import metadata, openclip_attempts, openclip_tasks, session_events, sessions  # noqa: E402
from opcrew_backend.koubo import dance_mimic_router as dm_router_module  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard import video_plan_routes as video_plan_routes_module  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.router import build_koubo_storyboard_router  # noqa: E402
from opcrew_backend.workflow_modes import WORKFLOW_DANCE_MIMIC_V1  # noqa: E402


FAKE_DM_SCRIPT = r'''
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


OUTPUTS = {
    "00": ["SessionContext/Variables.json", "SessionContext/Video_Reference_Source.mp4", "SessionContext/Target_Identity_Image.png", "S1_00_PrepareSessionVariables/Report/Result.json"],
    "01": ["SessionOutput/reference/reference_media_manifest.json", "SessionOutput/reference/Video_Reference_Silent.mp4", "SessionOutput/reference/Audio_Reference_Mixed.wav", "S2_01_ReferenceMediaDemux/Report/Result.json"],
    "02": ["SessionOutput/reference/segments/reference_segments_manifest.json", "SessionOutput/reference/segments/Segment_0001/Segment_0001_Reference_FaceMasked.mp4", "S3_02_ReferenceFaceMaskedVideoBuild/Report/Result.json"],
    "03": ["SessionOutput/storyboard/srt_storyboard.json", "SessionOutput/storyboard/storyboard_seed.json", "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4", "SessionOutput/storyboard/Working/dak_0001_Image_Source.png", "SessionOutput/storyboard/Working/dak_0001_Image_New.png", "S4_03_StoryBoardStandardTaskBuild/Report/Result.json"],
}
REFERENCE_REL = "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4"
TARGET_IDENTITY_REL = "SessionContext/Target_Identity_Image.png"
STORYBOARD_TARGET_REL = "SessionOutput/storyboard/Working/dak_0001_Image_Source.png"
STORYBOARD_FIRST_FRAME_REL = "SessionOutput/storyboard/Working/dak_0001_Image_New.png"
VIDEO_PLAN_REL = "SessionOutput/storyboard/video_generation_plan.json"
STALE_REL = "SessionReport/stale_manifest.json"


def step_id() -> str:
    name = Path(sys.argv[0]).name
    if name.startswith("00_"):
        return "00"
    if name.startswith("01_"):
        return "01"
    if name.startswith("02_"):
        return "02"
    return "03"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def storyboard_payload() -> dict:
    return {
        "schema_version": "analysis_v1_srt_storyboard_0.2",
        "workflow_id": "dance_mimic_v1",
        "source_type": "dance_mimic_v1_storyboard",
        "task_summary": "DanceMimic smoke storyboard",
        "video_formula": "dance_mimic_motion_reference",
        "shots": [
            {
                "shot_id": "shot_001",
                "shot_name": "DanceMimic reference motion",
                "start": 0,
                "end": 3,
                "duration": 3,
                "scenes": [
                    {
                        "scene_id": "scene_001",
                        "scene_name": "Reference dance motion",
                        "start": 0,
                        "end": 3,
                        "duration": 3,
                        "dialogue_items": [
                            {
                                "srt_id": "srt_0001",
                                "dialogue_asset_key": "dak_0001",
                                "dialogue": "Dance motion segment 0001",
                                "start": 0,
                                "end": 3,
                                "duration": 3,
                                "image_path": STORYBOARD_TARGET_REL,
                                "source_image_paths": [STORYBOARD_TARGET_REL],
                                "dance_mimic": {
                                    "source_segment_id": "segment_0001",
                                    "reference_video_path": REFERENCE_REL,
                                    "reference_video_role": "dance_mimic_segment_motion_reference",
                                    "target_identity_image_path": STORYBOARD_TARGET_REL,
                                    "source_target_identity_image_path": TARGET_IDENTITY_REL,
                                },
                                "working_assets": {
                                    "audio": {"slot": "Audio_Final", "source_type": "", "path": ""},
                                    "images": [
                                        {"slot": "Image_New", "source_type": "dance_mimic_target_identity", "path": STORYBOARD_FIRST_FRAME_REL},
                                        {"slot": "Image_02", "source_type": "", "path": ""},
                                    ],
                                    "video": {"slot": "Video_Final", "source_type": "", "path": ""},
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def seed_payload() -> dict:
    return {
        "schema_version": "dance_mimic_v1_storyboard_seed_0.1",
        "workflow_id": "dance_mimic_v1",
        "source_video_path": "SessionContext/Video_Reference_Source.mp4",
        "target_identity_image_path": TARGET_IDENTITY_REL,
        "segments": [
            {
                "segment_id": "segment_0001",
                "dialogue_asset_key": "dak_0001",
                "reference_video_path": REFERENCE_REL,
                "source_face_masked_reference_video_path": "SessionOutput/reference/segments/Segment_0001/Segment_0001_Reference_FaceMasked.mp4",
                "target_identity_image_path": STORYBOARD_TARGET_REL,
                "first_frame_image_path": STORYBOARD_FIRST_FRAME_REL,
                "source_target_identity_image_path": TARGET_IDENTITY_REL,
                "video_generation_mode": "dance_mimic_reference_video",
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
                "model_alias": "MaxSR2",
                "reference_mode": "input_references",
                "prompt_template": "Video_SDR2V_DanceMimic.md",
                "reference_video_role": "dance_mimic_segment_motion_reference",
            }
        ],
    }


def stale_manifest_payload() -> dict:
    return {
        "schema_version": "dance_mimic_v1_stale_manifest_0.1",
        "workflow_id": "dance_mimic_v1",
        "items": {
            "video_generation_plan": {
                "status": "stale",
                "source_step": "02_ReferenceFaceMaskedVideoBuild",
                "reason": "reference_face_mask_force_rerun",
                "paths": [VIDEO_PLAN_REL],
            }
        },
        "events": [{"event": "marked_stale", "items": ["video_generation_plan"]}],
        "updated_at": "2026-06-29T00:00:00Z",
    }


parser = argparse.ArgumentParser()
parser.add_argument("--workspace", required=True)
parser.add_argument("--source-video-path", default="")
parser.add_argument("--reference-video-path", default="")
parser.add_argument("--target-identity-image-path", default="")
parser.add_argument("--reference-privacy-mode", default="")
parser.add_argument("--print-json", action="store_true")
args, unknown = parser.parse_known_args()
sid = step_id()
workspace = Path(args.workspace)
(workspace / "commands").mkdir(parents=True, exist_ok=True)
(workspace / "commands" / f"{sid}.json").write_text(json.dumps({"argv": sys.argv[1:], "unknown": unknown, "source_video_path": args.source_video_path, "reference_video_path": args.reference_video_path, "target_identity_image_path": args.target_identity_image_path, "reference_privacy_mode": args.reference_privacy_mode}), encoding="utf-8")
for rel in OUTPUTS[sid]:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if sid == "03" and rel.endswith("srt_storyboard.json"):
        write_json(path, storyboard_payload())
    elif sid == "03" and rel.endswith("storyboard_seed.json"):
        write_json(path, seed_payload())
    else:
        path.write_bytes(b"{}" if path.suffix == ".json" else b"fake")
if sid == "02" and "--force" in sys.argv:
    write_json(workspace / STALE_REL, stale_manifest_payload())
print(json.dumps({"status": "completed", "outputs": {"result_path": OUTPUTS[sid][-1]}}))
'''

STORYBOARD_REFERENCE_REL = "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4"
VIDEO_PLAN_REL = "SessionOutput/storyboard/video_generation_plan.json"
VIDEO_PLAN_UI_CACHE_REL = "SessionOutput/storyboard/video_generation_plan.ui_cache.json"
STALE_REL = "SessionReport/stale_manifest.json"


def dance_mimic_video_plan(plan_hash: str = "hash-stale") -> dict[str, Any]:
    return {
        "schema_version": "analysis_v1_video_generation_plan_0.1",
        "workflow_id": WORKFLOW_DANCE_MIMIC_V1,
        "target": {"target_type": "task", "shot_id": "", "scene_id": ""},
        "settings": {"max_video_seconds": 4.0, "min_video_seconds": 2.0, "split_tolerance_seconds": 2.0},
        "summary": {"shot_count": 0, "scene_count": 0, "segment_count": 0},
        "shots": [],
        "plan_hash": plan_hash,
    }


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class FakeSessionRepo:
    def __init__(self, engine: Any, root: Path) -> None:
        self.engine = engine
        self.root = root
        self.next_id = 1

    def create(self, **fields: Any) -> int:
        session_id = self.next_id
        self.next_id += 1
        with self.engine.begin() as conn:
            conn.execute(insert(sessions).values(id=session_id, **fields))
        return session_id

    def get(self, session_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(sessions).where(sessions.c.id == session_id)).mappings().first()
        return dict(row) if row else None

    def update(self, session_id: int, **fields: Any) -> None:
        if not fields:
            return
        with self.engine.begin() as conn:
            conn.execute(update(sessions).where(sessions.c.id == session_id).values(**fields))

    def add_event(self, session_id: int, kind: str, payload: str | dict[str, Any], *_args: Any, **fields: Any) -> None:
        body = payload if isinstance(payload, str) else json.dumps(payload or {}, ensure_ascii=False)
        with self.engine.begin() as conn:
            conn.execute(
                insert(session_events).values(
                    session_id=session_id,
                    kind=kind,
                    payload=body,
                    workflow_id=fields.get("workflow_id") or WORKFLOW_DANCE_MIMIC_V1,
                    task_id=fields.get("task_id"),
                    attempt_id=fields.get("attempt_id"),
                    step_id=fields.get("step_id"),
                    created_at=int(time.time() * 1000),
                )
            )


class FakeWorkspaceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def sessions_root(self) -> Path:
        return self.root / "sessions"

    def create_session_workspace(self, session_id: int) -> Path:
        workspace = self.sessions_root() / str(session_id) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace


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
                    task_id=fields.get("task_id"),
                    attempt_id=fields.get("attempt_id"),
                    step_id=fields.get("step_id"),
                    tool_id=fields.get("tool_id"),
                    created_at=int(time.time() * 1000),
                )
            )


def request(app: FastAPI, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    import anyio

    async def run() -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else b""
        messages = [
            {"type": "http.request", "body": body, "more_body": False},
        ]
        sent: list[dict[str, Any]] = []
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }

        async def receive() -> dict[str, Any]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)
        status = 500
        chunks: list[bytes] = []
        for message in sent:
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body") or b"")
        text = b"".join(chunks).decode("utf-8")
        try:
            parsed = json.loads(text) if text else {}
        except Exception:
            parsed = {"text": text}
        return status, parsed if isinstance(parsed, dict) else {"value": parsed}

    return anyio.run(run)


def raw_request(app: FastAPI, method: str, url: str, body: bytes = b"", headers: list[tuple[str, str]] | None = None) -> tuple[int, dict[str, str], bytes]:
    import anyio

    async def run() -> tuple[int, dict[str, str], bytes]:
        parsed = urlsplit(url)
        messages = [
            {"type": "http.request", "body": body, "more_body": False},
        ]
        sent: list[dict[str, Any]] = []
        header_items = [(name.lower().encode("utf-8"), value.encode("utf-8")) for name, value in (headers or [])]
        if not any(name == b"host" for name, _value in header_items):
            header_items.append((b"host", b"testserver"))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": parsed.path,
            "raw_path": parsed.path.encode("utf-8"),
            "query_string": parsed.query.encode("utf-8"),
            "root_path": "",
            "headers": header_items,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }

        async def receive() -> dict[str, Any]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)
        status = 500
        response_headers: dict[str, str] = {}
        chunks: list[bytes] = []
        for message in sent:
            if message["type"] == "http.response.start":
                status = int(message["status"])
                response_headers = {name.decode("latin1").lower(): value.decode("latin1") for name, value in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body") or b"")
        return status, response_headers, b"".join(chunks)

    return anyio.run(run)


class DanceMimicBackendSurfaceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = create_engine(
            "sqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        self.fake_tools = self.root / "fake_tools"
        self.fake_tools.mkdir()
        for name in ("00_PrepareSessionVariables.py", "01_ReferenceMediaDemux.py", "02_ReferenceFaceMaskedVideoBuild.py", "03_StoryBoardStandardTaskBuild.py"):
            (self.fake_tools / name).write_text(FAKE_DM_SCRIPT, encoding="utf-8")
        self.old_specs = dm_router_module.DANCE_MIMIC_TOOL_SPECS
        dm_router_module.DANCE_MIMIC_TOOL_SPECS = [
            {"id": "00", "name": "00_PrepareSessionVariables", "script": self.fake_tools / "00_PrepareSessionVariables.py", "timeout": 30},
            {"id": "01", "name": "01_ReferenceMediaDemux", "script": self.fake_tools / "01_ReferenceMediaDemux.py", "timeout": 30},
            {"id": "02", "name": "02_ReferenceFaceMaskedVideoBuild", "script": self.fake_tools / "02_ReferenceFaceMaskedVideoBuild.py", "timeout": 30},
            {"id": "03", "name": "03_StoryBoardStandardTaskBuild", "script": self.fake_tools / "03_StoryBoardStandardTaskBuild.py", "timeout": 30},
        ]
        dm_router_module._ACTIVE_ATTEMPTS.clear()
        ctx = SimpleNamespace(
            engine=self.engine,
            data_dir=self.root,
            session_repo=FakeSessionRepo(self.engine, self.root),
            session_event_service=FakeSessionEventService(self.engine),
            workspace_store=FakeWorkspaceStore(self.root),
            new_share_token=lambda: "share-test",
        )
        self.app = FastAPI()
        self.app.include_router(dm_router_module.build_dance_mimic_router(ctx))
        storyboard_router = build_koubo_storyboard_router(ctx)
        self.app.include_router(storyboard_router)
        self.storyboard_deps = storyboard_router.storyboard_context
        self.source_video = self.root / "reference.mp4"
        self.source_video.write_bytes(b"fake-video")
        self.target_image = self.root / "target.png"
        self.target_image.write_bytes(b"fake-target-image")
        # Same containment as the runner contract tests (8e195d1): snapshot
        # pre-existing threads so tearDown can drain any run worker a test
        # spawns. Disposing the engine while a worker is mid-query on the
        # shared in-memory SQLite connection crashes natively (segfaults
        # observed on Linux CI), so workers must be joined first.
        self._threads_at_setup = {t.ident for t in threading.enumerate()}

    def tearDown(self) -> None:
        for worker in threading.enumerate():
            if worker.ident not in self._threads_at_setup and worker is not threading.current_thread():
                worker.join(timeout=20)
        dm_router_module.DANCE_MIMIC_TOOL_SPECS = self.old_specs
        dm_router_module._ACTIVE_ATTEMPTS.clear()
        self.engine.dispose()
        self.tmp.cleanup()

    def wait_for_status(self, task_id: int, attempt_id: int, statuses: set[str], timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            status, payload = request(self.app, "GET", f"/api/dance-mimic-v1/tasks/{task_id}/run/{attempt_id}")
            if status != 200:
                # Known Linux-CI isolation race (issue #1): a corrupted read
                # under run-worker concurrency can surface as a one-poll 404
                # ("OpenClip task not found", CI run 28729583185). Treat any
                # non-200 as transient and keep polling — a real regression
                # still fails via the deadline below with the payload shown.
                last = {"transient_status": status, **(payload if isinstance(payload, dict) else {"payload": payload})}
                time.sleep(0.05)
                continue
            last = payload
            if str(payload.get("status")) in statuses:
                return payload
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {statuses}; last={last}")

    def test_create_writes_dance_mimic_workflow_and_task_meta(self) -> None:
        status, response = request(self.app, "POST", "/api/dance-mimic-v1/tasks", {
            "title": "Fixture Dance",
            "reference_video_path": str(self.source_video),
            "target_identity_image_path": str(self.target_image),
            "auto_run": False,
        })

        self.assertEqual(status, 200, response)
        task_id = int(response["task_id"])
        self.assertEqual(response["workflow_mode"], WORKFLOW_DANCE_MIMIC_V1)
        with self.engine.connect() as conn:
            row = conn.execute(select(openclip_tasks).where(openclip_tasks.c.id == task_id)).mappings().one()
        self.assertEqual(row["workflow_mode"], WORKFLOW_DANCE_MIMIC_V1)
        self.assertEqual(row["reference_video_path"], str(self.source_video))
        meta_path = Path(response["workspace_dir"]) / "SessionOutput/task_list/task_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["workflow_id"], WORKFLOW_DANCE_MIMIC_V1)
        self.assertEqual(meta["dance_mimic"]["reference_video_path"], str(self.source_video))
        self.assertEqual(meta["dance_mimic"]["target_identity_image_path"], str(self.target_image))
        self.assertEqual(meta["dance_mimic"]["reference_privacy_mode"], "face_mask_only")

        detail_status, detail = request(self.app, "GET", f"/api/dance-mimic-v1/tasks/{task_id}")
        self.assertEqual(detail_status, 200, detail)
        self.assertEqual(detail["workflow_mode"], WORKFLOW_DANCE_MIMIC_V1)
        self.assertEqual(detail["target"], "dance_mimic_v1")
        self.assertEqual(detail["reference_video_path"], str(self.source_video))
        self.assertEqual(detail["target_identity_image_path"], str(self.target_image))
        self.assertEqual(detail["reference_privacy_mode"], "face_mask_only")
        self.assertEqual(detail["task"]["target_identity_image"], str(self.target_image))
        self.assertEqual(detail["task"]["reference_privacy_mode"], "face_mask_only")
        self.assertIsNone(detail["latest_run"])
        self.assertFalse(detail["artifacts"]["storyboard_ready"])
        self.assertFalse(detail["artifacts"]["files"]["storyboard_seed"]["exists"])
        self.assertFalse(detail["artifacts"]["files"]["target_identity_image"]["exists"])
        self.assertFalse(detail["artifacts"]["files"]["stale_manifest"]["exists"])
        self.assertEqual(detail["artifacts"]["files"]["stale_manifest"]["path"], "SessionReport/stale_manifest.json")
        self.assertEqual(detail["stale"]["path"], "SessionReport/stale_manifest.json")
        self.assertEqual(detail["stale"]["active_count"], 0)
        self.assertEqual(detail["run_plan"]["provider"], "openrouter")
        self.assertEqual(detail["run_plan"]["options"]["reference_privacy_mode"], "face_mask_only")
        self.assertFalse(detail["run_plan"]["options"]["force"])

    def test_privacy_grid_preview_is_task_scoped_hash_checked_and_stale_aware(self) -> None:
        status, response = request(self.app, "POST", "/api/dance-mimic-v1/tasks", {
            "title": "Privacy Grid Preview",
            "reference_video_path": str(self.source_video),
            "target_identity_image_path": str(self.target_image),
            "reference_privacy_mode": "red_grid_guide",
            "apply_privacy_grid_to_reference_video": True,
            "apply_privacy_grid_to_target_identity_image": True,
            "auto_run": False,
        })
        self.assertEqual(status, 200, response)
        task_id = int(response["task_id"])
        workspace = Path(response["workspace_dir"])
        reference_rel = "SessionOutput/reference/PrivacyGrid_Reference_Preview.png"
        target_rel = "SessionContext/Target_Identity_Image_PrivacyGrid.png"
        reference = workspace / reference_rel
        target = workspace / target_rel
        reference.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        reference.write_bytes(b"reference-grid-preview")
        target.write_bytes(b"target-grid-preview")
        reference_sha = hashlib.sha256(reference.read_bytes()).hexdigest()
        target_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        write_json_file(workspace / "SessionOutput/reference/privacy_grid_manifest.json", {
            "schema_version": "dance_mimic_v1_privacy_grid_0.2",
            "mode": "red_grid_guide",
            "apply_to_reference_video": True,
            "apply_to_target_identity_image": True,
            "effective_grid_scope": "both",
            "reference_video": {
                "grid_applied": True,
                "preview": {
                    "path": reference_rel,
                    "sha256": reference_sha,
                    "timestamp_seconds": 5.25,
                },
            },
            "target_identity": {
                "grid_applied": True,
                "provider_path": target_rel,
                "provider_sha256": target_sha,
            },
            "created_at": "2026-07-13T00:00:00Z",
        })

        detail_status, detail = request(self.app, "GET", f"/api/dance-mimic-v1/tasks/{task_id}")
        self.assertEqual(detail_status, 200, detail)
        preview = detail["privacy_grid_preview"]
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["effective_grid_scope"], "both")
        self.assertEqual(preview["reference_video"]["preview_timestamp_seconds"], 5.25)
        self.assertIn(f"/tasks/{task_id}/privacy-grid-preview/reference?v=", preview["reference_video"]["preview_url"])
        self.assertNotIn("?path=", preview["reference_video"]["preview_url"])

        reference_status, reference_headers, reference_body = raw_request(self.app, "GET", preview["reference_video"]["preview_url"])
        self.assertEqual(reference_status, 200)
        self.assertEqual(reference_body, b"reference-grid-preview")
        self.assertEqual(reference_headers.get("etag"), f'"{reference_sha}"')
        self.assertEqual(reference_headers.get("cache-control"), "private, max-age=3600")

        target.write_bytes(b"tampered-target-preview")
        target_status, _headers, _body = raw_request(self.app, "GET", preview["target_identity"]["preview_url"])
        self.assertEqual(target_status, 409)

        write_json_file(workspace / STALE_REL, {
            "items": {
                "02_reference_face_masked_video_build": {
                    "status": "stale",
                    "reason": "privacy_grid_config_changed",
                },
            },
        })
        stale_status, stale_detail = request(self.app, "GET", f"/api/dance-mimic-v1/tasks/{task_id}")
        self.assertEqual(stale_status, 200, stale_detail)
        self.assertEqual(stale_detail["privacy_grid_preview"]["status"], "stale")
        stale_media_status, _headers, _body = raw_request(self.app, "GET", preview["reference_video"]["preview_url"])
        self.assertEqual(stale_media_status, 409)

    def test_target_image_library_lists_ai_generated_people_and_uploads(self) -> None:
        asset_dir = self.root / "sessions/999/workspace/SessionOutput/storyboard/assets/images"
        asset_dir.mkdir(parents=True, exist_ok=True)
        ai_person = asset_dir / "1782379120890_agent_generated_ai_host.png"
        ai_person.write_bytes(b"fake-ai-person-image")
        ai_person.with_suffix(".json").write_text(json.dumps({
            "prompt": "AI generated 数字人 男主持 半身 正脸 人物",
            "origin": {"tool": "upload_asset_library_agent"},
        }, ensure_ascii=False), encoding="utf-8")
        product = asset_dir / "1782379120999_agent_generated_product.png"
        product.write_bytes(b"fake-product-image")
        product.with_suffix(".json").write_text(json.dumps({"prompt": "AI generated product package only"}, ensure_ascii=False), encoding="utf-8")

        library_status, library_payload = request(self.app, "GET", "/api/dance-mimic-v1/target-images")
        self.assertEqual(library_status, 200, library_payload)
        payload = library_payload
        paths = [item["path"] for item in payload["items"]]
        self.assertIn(str(ai_person), paths)
        self.assertNotIn(str(product), paths)
        item = next(item for item in payload["items"] if item["path"] == str(ai_person))
        self.assertTrue(item["is_ai_generated"])
        self.assertTrue(item["person_hint"])
        self.assertIn("/api/dance-mimic-v1/target-images/preview", item["preview_url"])

        preview_status, _preview_headers, preview_body = raw_request(self.app, "GET", item["preview_url"])
        self.assertEqual(preview_status, 200, preview_body)
        self.assertEqual(preview_body, b"fake-ai-person-image")

        boundary = "----dance-mimic-upload-contract"
        upload_body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="selected-ai-host.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + b"uploaded-ai-host" + f"\r\n--{boundary}--\r\n".encode("utf-8")
        upload_status, _upload_headers, upload_bytes = raw_request(
            self.app,
            "POST",
            "/api/dance-mimic-v1/target-images/upload",
            upload_body,
            [("content-type", f"multipart/form-data; boundary={boundary}")],
        )
        self.assertEqual(upload_status, 200, upload_bytes)
        uploaded = json.loads(upload_bytes.decode("utf-8"))
        uploaded_path = Path(uploaded["target_identity_image_path"])
        self.assertTrue(uploaded_path.exists())
        self.assertEqual(uploaded_path.read_bytes(), b"uploaded-ai-host")
        self.assertEqual(uploaded["item"]["source"], "uploaded")
        self.assertTrue(uploaded["item"]["preview_url"].startswith("/api/dance-mimic-v1/target-images/preview"))

    def test_reference_video_library_lists_fixtures_assets_and_uploads(self) -> None:
        asset_dir = self.root / "sessions/998/workspace/SessionOutput/storyboard/assets/videos"
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_video = asset_dir / "dance_reference_clip.mp4"
        asset_video.write_bytes(b"fake-dance-reference-video")

        library_status, library_payload = request(self.app, "GET", "/api/dance-mimic-v1/reference-videos")
        self.assertEqual(library_status, 200, library_payload)
        paths = [item["path"] for item in library_payload["items"]]
        self.assertIn(str(asset_video), paths)
        self.assertIn(str(REPO_ROOT / "ToolLibrary/DanceMimic_V1/test_fixtures/dance_solo_frontal_studio.mp4"), paths)
        item = next(item for item in library_payload["items"] if item["path"] == str(asset_video))
        self.assertEqual(item["source"], "asset_library")
        self.assertIn("/api/dance-mimic-v1/reference-videos/preview", item["preview_url"])

        preview_status, preview_headers, preview_body = raw_request(self.app, "GET", item["preview_url"])
        self.assertEqual(preview_status, 200, preview_body)
        self.assertIn("video/mp4", preview_headers.get("content-type", ""))
        self.assertEqual(preview_body, b"fake-dance-reference-video")

        boundary = "----dance-mimic-reference-upload-contract"
        upload_body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="selected-dance.mp4"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
        ).encode("utf-8") + b"uploaded-reference-video" + f"\r\n--{boundary}--\r\n".encode("utf-8")
        upload_status, _upload_headers, upload_bytes = raw_request(
            self.app,
            "POST",
            "/api/dance-mimic-v1/reference-videos/upload",
            upload_body,
            [("content-type", f"multipart/form-data; boundary={boundary}")],
        )
        self.assertEqual(upload_status, 200, upload_bytes)
        uploaded = json.loads(upload_bytes.decode("utf-8"))
        uploaded_path = Path(uploaded["reference_video_path"])
        self.assertTrue(uploaded_path.exists())
        self.assertEqual(uploaded_path.read_bytes(), b"uploaded-reference-video")
        self.assertEqual(uploaded["item"]["source"], "uploaded")
        self.assertTrue(uploaded["item"]["preview_url"].startswith("/api/dance-mimic-v1/reference-videos/preview"))

    # CI skip removed 2026-07-05: Step 1 Phase F deleted _sync_service_globals
    # and the isolation contracts are green (issue #1 burn-in: watch 10
    # consecutive CI runs; if the Linux-only flake persists, the residual
    # suspect is SQLAlchemy compiled-cache sharing — escalate per the ticket).

    def test_face_masked_reference_video_is_finalized_as_h264_mp4_before_storyboard(self) -> None:
        source = (REPO_ROOT / "ToolLibrary/DanceMimic_V1/_tool_impl.py").read_text(encoding="utf-8")

        self.assertIn("def reencode_reference_video_for_provider(", source)
        self.assertIn('"libx264"', source)
        self.assertIn('"-pix_fmt"', source)
        self.assertIn('"yuv420p"', source)
        self.assertIn('"-movflags"', source)
        self.assertIn('"+faststart"', source)
        self.assertNotIn('force_h264 = privacy_mode != "face_mask_only"', source)
        self.assertIn("provider_video = reencode_reference_video_for_provider(", source)
        self.assertIn('"face_masked_reference_video_path": masked_rel', source)
        self.assertIn('source_rel = text_value(segment.get("provider_reference_video_path") or segment.get("face_masked_reference_video_path"))', source)
        self.assertIn('"provider_reference_video_path": source_rel', source)
        self.assertIn('"source_face_masked_reference_video_path": source_rel', source)

    def test_task_uploads_stage_directly_in_session_context_and_run_with_relative_paths(self) -> None:
        boundary = "----dance-mimic-task-session-context-upload-contract"

        def field(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")

        def upload(name: str, filename: str, content_type: str, payload: bytes) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8") + payload + b"\r\n"

        body = b"".join([
            field("title", "Session Context Upload"),
            field("target_video_seconds", "2"),
            field("minimum_video_seconds", "1"),
            field("auto_run", "false"),
            upload("reference_video_file", "selected-dance.mov", "video/quicktime", b"uploaded-reference-video"),
            upload("target_identity_image_file", "selected-ai-host.jpg", "image/jpeg", b"uploaded-target-image"),
            f"--{boundary}--\r\n".encode("utf-8"),
        ])
        status, _headers, response_bytes = raw_request(
            self.app,
            "POST",
            "/api/dance-mimic-v1/tasks/with-uploads",
            body,
            [("content-type", f"multipart/form-data; boundary={boundary}")],
        )

        self.assertEqual(status, 200, response_bytes)
        response = json.loads(response_bytes.decode("utf-8"))
        workspace = Path(response["workspace_dir"])
        task_id = int(response["task_id"])
        self.assertNotIn("run", response)

        self.assertEqual((workspace / "SessionContext/Video_Reference_Source.mp4").read_bytes(), b"uploaded-reference-video")
        self.assertEqual((workspace / "SessionContext/Target_Identity_Image.jpg").read_bytes(), b"uploaded-target-image")
        self.assertFalse((workspace / "SessionInput/dance_mimic").exists())

        with self.engine.connect() as conn:
            row = conn.execute(select(openclip_tasks).where(openclip_tasks.c.id == task_id)).mappings().one()
        self.assertEqual(row["reference_video_path"], "SessionContext/Video_Reference_Source.mp4")

        meta = json.loads((workspace / "SessionOutput/task_list/task_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["dance_mimic"]["reference_video_path"], "SessionContext/Video_Reference_Source.mp4")
        self.assertEqual(meta["dance_mimic"]["target_identity_image_path"], "SessionContext/Target_Identity_Image.jpg")

        run_status, run = request(self.app, "POST", f"/api/dance-mimic-v1/tasks/{task_id}/run", {"force": False})
        self.assertEqual(run_status, 200, run)
        self.wait_for_status(task_id, int(run["attempt_id"]), {"completed"})

        command_00 = json.loads((workspace / "commands/00.json").read_text(encoding="utf-8"))
        self.assertEqual(command_00["source_video_path"], "SessionContext/Video_Reference_Source.mp4")
        self.assertEqual(command_00["target_identity_image_path"], "SessionContext/Target_Identity_Image.jpg")

        detail_status, detail = request(self.app, "GET", f"/api/dance-mimic-v1/tasks/{task_id}")
        self.assertEqual(detail_status, 200, detail)
        self.assertEqual(detail["reference_video_path"], "SessionContext/Video_Reference_Source.mp4")
        self.assertEqual(detail["target_identity_image_path"], "SessionContext/Target_Identity_Image.jpg")
        self.assertEqual(detail["artifacts"]["files"]["source_reference_video"]["path"], "SessionContext/Video_Reference_Source.mp4")

    def test_legacy_session_input_paths_are_normalized_on_detail(self) -> None:
        status, response = request(self.app, "POST", "/api/dance-mimic-v1/tasks", {
            "title": "Legacy SessionInput Upload",
            "reference_video_path": str(self.source_video),
            "target_identity_image_path": str(self.target_image),
            "auto_run": False,
        })
        self.assertEqual(status, 200, response)
        workspace = Path(response["workspace_dir"])
        task_id = int(response["task_id"])

        legacy_reference = workspace / "SessionInput/dance_mimic/reference_videos/1783234067269_c7fa22c58fb367609afc386c7669371f.mov"
        legacy_target = workspace / "SessionInput/dance_mimic/target_images/1783234067270_target_identity.jpg"
        legacy_reference.parent.mkdir(parents=True, exist_ok=True)
        legacy_target.parent.mkdir(parents=True, exist_ok=True)
        legacy_reference.write_bytes(b"legacy-reference-video")
        legacy_target.write_bytes(b"legacy-target-image")

        legacy_reference_value = str(legacy_reference)
        legacy_target_value = str(legacy_target)
        meta_path = workspace / "SessionOutput/task_list/task_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["reference_video_path"] = legacy_reference_value
        meta["target_identity_image_path"] = legacy_target_value
        meta["dance_mimic"]["reference_video_path"] = legacy_reference_value
        meta["dance_mimic"]["target_identity_image_path"] = legacy_target_value
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        quick_config = json.loads(meta_path.read_text(encoding="utf-8"))["dance_mimic"]
        with self.engine.begin() as conn:
            conn.execute(
                update(openclip_tasks).where(openclip_tasks.c.id == task_id).values(
                    reference_video_path=legacy_reference_value,
                    storyboard_quick_config_json=json.dumps(quick_config, ensure_ascii=False, sort_keys=True),
                )
            )

        detail_status, detail = request(self.app, "GET", f"/api/dance-mimic-v1/tasks/{task_id}")
        self.assertEqual(detail_status, 200, detail)
        self.assertEqual(detail["reference_video_path"], "SessionContext/Video_Reference_Source.mp4")
        self.assertEqual(detail["target_identity_image_path"], "SessionContext/Target_Identity_Image.jpg")
        self.assertEqual((workspace / "SessionContext/Video_Reference_Source.mp4").read_bytes(), b"legacy-reference-video")
        self.assertEqual((workspace / "SessionContext/Target_Identity_Image.jpg").read_bytes(), b"legacy-target-image")

        with self.engine.connect() as conn:
            row = conn.execute(select(openclip_tasks).where(openclip_tasks.c.id == task_id)).mappings().one()
        self.assertEqual(row["reference_video_path"], "SessionContext/Video_Reference_Source.mp4")

        migrated_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated_meta["dance_mimic"]["reference_video_path"], "SessionContext/Video_Reference_Source.mp4")
        self.assertEqual(migrated_meta["dance_mimic"]["target_identity_image_path"], "SessionContext/Target_Identity_Image.jpg")

    def test_auto_run_invokes_dance_mimic_tools_with_source_video_path(self) -> None:
        status, response = request(self.app, "POST", "/api/dance-mimic-v1/tasks", {
            "title": "Fixture Dance",
            "reference_video_path": str(self.source_video),
            "target_identity_image_path": str(self.target_image),
            "target_video_seconds": 2,
            "minimum_video_seconds": 1,
            "face_detections_manifest": str(self.root / "fixed_bbox.json"),
            "auto_run": True,
        })
        self.assertEqual(status, 200, response)
        run = response["run"]
        completed = self.wait_for_status(int(response["task_id"]), int(run["attempt_id"]), {"completed"})

        self.assertEqual(completed["target"], "dance_mimic_v1")
        self.assertEqual(completed["provider"], "openrouter")
        self.assertEqual([step["id"] for step in completed["steps"]], ["00", "01", "02", "03"])
        self.assertIsNotNone(completed.get("duration_seconds"))
        for step in completed["steps"]:
            self.assertIsNotNone(step.get("duration_seconds"), step)
            self.assertGreaterEqual(float(step["duration_seconds"]), 0.0)
        self.assertTrue((Path(response["workspace_dir"]) / "SessionOutput/storyboard/storyboard_seed.json").exists())
        command_00 = json.loads((Path(response["workspace_dir"]) / "commands/00.json").read_text(encoding="utf-8"))
        self.assertEqual(command_00["source_video_path"], str(self.source_video))
        self.assertEqual(command_00["reference_video_path"], "")
        self.assertEqual(command_00["target_identity_image_path"], str(self.target_image))
        self.assertEqual(command_00["reference_privacy_mode"], "face_mask_only")
        self.assertIn("--source-video-path", command_00["argv"])
        self.assertIn("--target-identity-image-path", command_00["argv"])
        self.assertIn("--reference-privacy-mode", command_00["argv"])
        self.assertNotIn("--reference-video-path", command_00["argv"])
        command_03 = json.loads((Path(response["workspace_dir"]) / "commands/03.json").read_text(encoding="utf-8"))
        self.assertNotIn("--force", command_03["argv"])
        with self.engine.connect() as conn:
            attempt = conn.execute(select(openclip_attempts).where(openclip_attempts.c.id == int(run["attempt_id"]))).mappings().one()
        self.assertEqual(attempt["status"], "completed")
        self.assertEqual(attempt["run_model_provider"], "openrouter")
        self.assertEqual(attempt["run_model_id"], "bytedance/seedance-2.0")

        detail_status, detail = request(self.app, "GET", f"/api/dance-mimic-v1/tasks/{int(response['task_id'])}")
        self.assertEqual(detail_status, 200, detail)
        self.assertEqual(detail["latest_run"]["status"], "completed")
        self.assertEqual(detail["latest_run"]["attempt_id"], int(run["attempt_id"]))
        self.assertIsNotNone(detail["latest_run"].get("duration_seconds"))
        for step in detail["latest_run"]["steps"]:
            self.assertIsNotNone(step.get("duration_seconds"), step)
        self.assertTrue(detail["artifacts"]["reference_ready"])
        self.assertTrue(detail["artifacts"]["storyboard_ready"])
        self.assertTrue(detail["storyboard"]["ready"])
        self.assertEqual(detail["storyboard"]["item_count"], len(detail["storyboard"]["items"]))
        self.assertGreater(detail["storyboard"]["item_count"], 0)
        self.assertEqual(detail["storyboard"]["items"][0]["srt_id"], "srt_0001")
        self.assertEqual(detail["storyboard"]["items"][0]["text"], "Dance motion segment 0001")
        self.assertTrue(detail["storyboard"]["items"][0]["reference_video"]["exists"])
        self.assertIn("/api/dance-mimic-v1/reference-videos/preview?path=", detail["storyboard"]["items"][0]["reference_video"]["preview_url"])
        self.assertTrue(detail["artifacts"]["files"]["target_identity_image"]["exists"])
        self.assertTrue(detail["artifacts"]["files"]["reference_segments_manifest"]["exists"])
        self.assertTrue(detail["artifacts"]["files"]["storyboard_seed"]["exists"])
        self.assertFalse(detail["latest_run"]["plan"]["options"]["force"])
        self.assertEqual(detail["latest_run"]["plan"]["options"]["reference_privacy_mode"], "face_mask_only")

    def test_auto_run_creation_response_contract_without_polling(self) -> None:
        # Deterministic slice of the auto-run contract kept enabled on CI while the
        # full polling test above is CI-skipped (issue #1): asserts only the
        # synchronous POST response, never queries while the worker thread runs.
        status, response = request(self.app, "POST", "/api/dance-mimic-v1/tasks", {
            "title": "Fixture Dance",
            "reference_video_path": str(self.source_video),
            "target_identity_image_path": str(self.target_image),
            "target_video_seconds": 2,
            "minimum_video_seconds": 1,
            "face_detections_manifest": str(self.root / "fixed_bbox.json"),
            "auto_run": True,
        })
        self.assertEqual(status, 200, response)
        self.assertIsInstance(response.get("task_id"), int)
        run = response.get("run") or {}
        self.assertIsInstance(run.get("attempt_id"), int)
        workspace_dir = Path(response["workspace_dir"])
        self.assertTrue(workspace_dir.exists())

    def test_smoke_create_run_open_storyboard_and_block_stale_video_plan_execution(self) -> None:
        status, response = request(self.app, "POST", "/api/dance-mimic-v1/tasks", {
            "title": "Fixture Dance",
            "reference_video_path": str(self.source_video),
            "target_identity_image_path": str(self.target_image),
            "target_video_seconds": 2,
            "minimum_video_seconds": 1,
            "face_detections_manifest": str(self.root / "fixed_bbox.json"),
            "auto_run": True,
        })
        self.assertEqual(status, 200, response)
        task_id = int(response["task_id"])
        workspace = Path(response["workspace_dir"])
        initial_run = response["run"]
        completed = self.wait_for_status(task_id, int(initial_run["attempt_id"]), {"completed"})
        self.assertEqual(completed["status"], "completed")

        storyboard_status, storyboard = request(self.app, "GET", f"/api/koubo-storyboard/tasks/{task_id}")
        self.assertEqual(storyboard_status, 200, storyboard)
        self.assertEqual(storyboard["meta"]["workflow_mode"], WORKFLOW_DANCE_MIMIC_V1)
        self.assertEqual(storyboard["stale"]["active_count"], 0)
        dialogue = storyboard["plan"]["shots"][0]["scenes"][0]["dialogues"][0]
        self.assertEqual(dialogue["dance_mimic"]["reference_video_path"], STORYBOARD_REFERENCE_REL)
        self.assertEqual(dialogue["dance_mimic"]["target_identity_image_path"], "SessionOutput/storyboard/Working/dak_0001_Image_Source.png")
        self.assertEqual(dialogue["image_path"], "SessionOutput/storyboard/Working/dak_0001_Image_Source.png")
        self.assertEqual(dialogue["source_image_paths"], ["SessionOutput/storyboard/Working/dak_0001_Image_Source.png"])
        self.assertEqual(dialogue["working_assets"]["images"][0]["source_type"], "dance_mimic_target_identity")
        self.assertEqual(dialogue["working_assets"]["images"][0]["path"], "SessionOutput/storyboard/Working/dak_0001_Image_New.png")
        self.assertEqual(dialogue["working_assets"]["video"]["slot"], "Video_Final")
        self.assertEqual(dialogue["working_assets"]["video"]["path"], "")

        write_json_file(workspace / VIDEO_PLAN_REL, dance_mimic_video_plan())
        write_json_file(workspace / VIDEO_PLAN_UI_CACHE_REL, {"cache_status": "cache_hit"})
        force_status, force_run = request(self.app, "POST", f"/api/dance-mimic-v1/tasks/{task_id}/run", {
            "target_video_seconds": 2,
            "minimum_video_seconds": 1,
            "face_detections_manifest": str(self.root / "fixed_bbox.json"),
            "force": True,
        })
        self.assertEqual(force_status, 200, force_run)
        forced = self.wait_for_status(task_id, int(force_run["attempt_id"]), {"completed"})
        self.assertEqual(forced["stale"]["items"]["video_generation_plan"]["status"], "stale")

        stale_storyboard_status, stale_storyboard = request(self.app, "GET", f"/api/koubo-storyboard/tasks/{task_id}")
        self.assertEqual(stale_storyboard_status, 200, stale_storyboard)
        self.assertEqual(stale_storyboard["stale"]["items"]["video_generation_plan"]["reason"], "reference_face_mask_force_rerun")
        self.assertIn("dance_mimic_storyboard_stale", {item["code"] for item in stale_storyboard["warnings"]})

        execute_status, execute_payload = request(self.app, "POST", f"/api/koubo-storyboard/tasks/{task_id}/video-plan/execute", {"plan_hash": "hash-stale"})
        self.assertEqual(execute_status, 409, execute_payload)
        self.assertEqual(execute_payload["detail"]["code"], "dance_mimic_video_plan_stale")

        old_cache_matches = self.storyboard_deps.video_plan_cache_matches
        old_run_tool = self.storyboard_deps.run_video_plan_tool

        def fake_cache_matches(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
            return True, "cache_signature_matched"

        def fake_run_tool(workspace_path: Path, target: dict[str, str], settings: dict[str, float]) -> tuple[dict[str, Any], str, str, int]:
            write_json_file(workspace_path / VIDEO_PLAN_REL, {
                **dance_mimic_video_plan(plan_hash="hash-regenerated"),
                "target": target,
                "settings": settings,
            })
            return {"status": "completed"}, "", "", 0

        try:
            self.storyboard_deps.video_plan_cache_matches = fake_cache_matches
            self.storyboard_deps.run_video_plan_tool = fake_run_tool
            regenerate_status, regenerated = request(self.app, "POST", f"/api/koubo-storyboard/tasks/{task_id}/video-plan", {})
        finally:
            self.storyboard_deps.video_plan_cache_matches = old_cache_matches
            self.storyboard_deps.run_video_plan_tool = old_run_tool

        self.assertEqual(regenerate_status, 200, regenerated)
        self.assertEqual(regenerated["cache_status"], "regenerated")
        self.assertEqual(regenerated["reason"], "dance_mimic_video_plan_stale")
        self.assertIn("video_generation_plan", regenerated["cleared_stale_items"])
        cleared_status, cleared_storyboard = request(self.app, "GET", f"/api/koubo-storyboard/tasks/{task_id}")
        self.assertEqual(cleared_status, 200, cleared_storyboard)
        self.assertEqual(cleared_storyboard["stale"]["active_count"], 0)

    def test_detail_and_run_status_include_stale_manifest(self) -> None:
        status, response = request(self.app, "POST", "/api/dance-mimic-v1/tasks", {
            "title": "Fixture Dance",
            "reference_video_path": str(self.source_video),
            "target_identity_image_path": str(self.target_image),
            "target_video_seconds": 2,
            "minimum_video_seconds": 1,
            "face_detections_manifest": str(self.root / "fixed_bbox.json"),
            "auto_run": True,
        })
        self.assertEqual(status, 200, response)
        run = response["run"]
        completed = self.wait_for_status(int(response["task_id"]), int(run["attempt_id"]), {"completed"})
        self.assertEqual(completed["status"], "completed")
        workspace = Path(response["workspace_dir"])
        self.assertEqual(dm_router_module.DANCE_MIMIC_STALE_MANIFEST_REL, "SessionReport/stale_manifest.json")
        self.assertNotIn("SessionOutput/dance_mimic_v1", dm_router_module.DANCE_MIMIC_STALE_MANIFEST_REL)
        stale_manifest_path = workspace / dm_router_module.DANCE_MIMIC_STALE_MANIFEST_REL
        stale_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        stale_manifest_path.write_text(json.dumps({
            "schema_version": "dance_mimic_v1_stale_manifest_0.1",
            "workflow_id": WORKFLOW_DANCE_MIMIC_V1,
            "items": {
                "video_generation_plan": {
                    "status": "stale",
                    "source_step": "02_ReferenceFaceMaskedVideoBuild",
                    "reason": "reference_face_mask_force_rerun",
                    "paths": ["SessionOutput/storyboard/video_generation_plan.json"],
                }
            },
            "events": [{"event": "marked_stale"}],
            "updated_at": 1780999609000,
        }), encoding="utf-8")

        detail_status, detail = request(self.app, "GET", f"/api/dance-mimic-v1/tasks/{int(response['task_id'])}")
        run_status, run_payload = request(self.app, "GET", f"/api/dance-mimic-v1/tasks/{int(response['task_id'])}/run/{int(run['attempt_id'])}")

        self.assertEqual(detail_status, 200, detail)
        self.assertEqual(run_status, 200, run_payload)
        self.assertEqual(detail["stale"]["active_count"], 1)
        self.assertTrue(detail["artifacts"]["files"]["stale_manifest"]["exists"])
        self.assertEqual(detail["stale"]["items"]["video_generation_plan"]["status"], "stale")
        self.assertEqual(run_payload["stale"]["active_count"], 1)
        self.assertEqual(run_payload["stale"]["items"]["video_generation_plan"]["reason"], "reference_face_mask_force_rerun")

    def test_explicit_force_run_passes_force_to_dance_mimic_tools(self) -> None:
        status, response = request(self.app, "POST", "/api/dance-mimic-v1/tasks", {
            "title": "Fixture Dance",
            "reference_video_path": str(self.source_video),
            "target_identity_image_path": str(self.target_image),
            "target_video_seconds": 2,
            "minimum_video_seconds": 1,
            "face_detections_manifest": str(self.root / "fixed_bbox.json"),
            "auto_run": False,
        })
        self.assertEqual(status, 200, response)

        run_status, run = request(self.app, "POST", f"/api/dance-mimic-v1/tasks/{int(response['task_id'])}/run", {
            "target_video_seconds": 2,
            "minimum_video_seconds": 1,
            "face_detections_manifest": str(self.root / "fixed_bbox.json"),
            "force": True,
        })
        self.assertEqual(run_status, 200, run)
        completed = self.wait_for_status(int(response["task_id"]), int(run["attempt_id"]), {"completed"})

        self.assertTrue(completed["plan"]["options"]["force"])
        command_03 = json.loads((Path(response["workspace_dir"]) / "commands/03.json").read_text(encoding="utf-8"))
        self.assertIn("--force", command_03["argv"])

    def test_app_registers_dance_mimic_router(self) -> None:
        app_source = (REPO_ROOT / "backend/opcrew_backend/app.py").read_text(encoding="utf-8")
        package_source = (REPO_ROOT / "backend/opcrew_backend/koubo/__init__.py").read_text(encoding="utf-8")

        self.assertIn("build_dance_mimic_router", app_source)
        self.assertIn("app.include_router(build_dance_mimic_router(ctx))", app_source)
        self.assertIn("build_dance_mimic_router", package_source)


if __name__ == "__main__":
    unittest.main()
