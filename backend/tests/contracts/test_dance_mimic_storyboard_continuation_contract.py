from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.db.schema import metadata, openclip_tasks, session_events, sessions  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard import video_plan_routes as video_plan_routes_module  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.router import build_koubo_storyboard_router  # noqa: E402
from opcrew_backend.workflow_modes import WORKFLOW_DANCE_MIMIC_V1  # noqa: E402


SOURCE_REL = "SessionOutput/storyboard/srt_storyboard.json"
SEED_REL = "SessionOutput/storyboard/storyboard_seed.json"
REFERENCE_REL = "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4"
TARGET_IDENTITY_REL = "SessionOutput/storyboard/Working/dak_0001_Image_Source.png"
FIRST_FRAME_REL = "SessionOutput/storyboard/Working/dak_0001_Image_New.png"
AUDIO_REL = "SessionOutput/storyboard/Working/dak_0001_Audio_Final.wav"
SHOT_PLAN_REL = "SessionOutput/storyboard/Working/ShotPlan_Final.mp4"
VIDEO_PLAN_REL = "SessionOutput/storyboard/video_generation_plan.json"
STALE_REL = "SessionReport/stale_manifest.json"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def request(app: FastAPI, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    import anyio

    async def run() -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else b""
        messages = [{"type": "http.request", "body": body, "more_body": False}]
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


class FakeSessionRepo:
    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def get(self, session_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(sessions).where(sessions.c.id == session_id)).mappings().first()
        return dict(row) if row else None

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


def dance_mimic_source_storyboard() -> dict[str, Any]:
    return {
        "schema_version": "analysis_v1_srt_storyboard_0.2",
        "workflow_id": WORKFLOW_DANCE_MIMIC_V1,
        "source_type": "dance_mimic_v1_storyboard",
        "task_summary": "DanceMimic reference motion storyboard",
        "video_formula": "dance_mimic_motion_reference",
        "compose_assets": {
            "shot_plan": {
                "status": "completed",
                "scope": "shot_plan",
                "video_path": SHOT_PLAN_REL,
                "updated_at": "2026-07-01T00:00:00Z",
                "source": "06_01_VideoPlanComposer",
            }
        },
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
                                "image_path": TARGET_IDENTITY_REL,
                                "source_image_paths": [TARGET_IDENTITY_REL],
                                "dance_mimic": {
                                    "source_segment_id": "segment_0001",
                                    "reference_video_path": REFERENCE_REL,
                                    "reference_video_role": "dance_mimic_segment_motion_reference",
                                    "target_identity_image_path": TARGET_IDENTITY_REL,
                                    "source_target_identity_image_path": "SessionContext/Target_Identity_Image.png",
                                },
                                "working_assets": {
                                    "audio": {"slot": "Audio_Final", "source_type": "dance_mimic_reference_audio", "path": AUDIO_REL},
                                    "images": [
                                        {"slot": "Image_New", "source_type": "dance_mimic_target_identity", "path": FIRST_FRAME_REL},
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


def dance_mimic_seed() -> dict[str, Any]:
    return {
        "schema_version": "dance_mimic_v1_storyboard_seed_0.1",
        "workflow_id": WORKFLOW_DANCE_MIMIC_V1,
        "source_video_path": "SessionContext/Video_Reference_Source.mp4",
        "target_identity_image_path": "SessionContext/Target_Identity_Image.png",
        "segments": [
            {
                "segment_id": "segment_0001",
                "dialogue_asset_key": "dak_0001",
                "reference_video_path": REFERENCE_REL,
                "source_face_masked_reference_video_path": "SessionOutput/reference/segments/Segment_0001/Segment_0001_Reference_FaceMasked.mp4",
                "target_identity_image_path": TARGET_IDENTITY_REL,
                "first_frame_image_path": FIRST_FRAME_REL,
                "source_target_identity_image_path": "SessionContext/Target_Identity_Image.png",
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


def dance_mimic_stale_manifest() -> dict[str, Any]:
    return {
        "schema_version": "dance_mimic_v1_stale_manifest_0.1",
        "workflow_id": WORKFLOW_DANCE_MIMIC_V1,
        "items": {
            "video_generation_plan": {
                "status": "stale",
                "source_step": "02_ReferenceFaceMaskedVideoBuild",
                "reason": "reference_face_mask_force_rerun",
                "paths": [VIDEO_PLAN_REL],
                "updated_at": "2026-06-29T00:00:00Z",
            }
        },
        "events": [
            {
                "event": "marked_stale",
                "source_step": "02_ReferenceFaceMaskedVideoBuild",
                "reason": "reference_face_mask_force_rerun",
                "items": ["video_generation_plan"],
                "created_at": "2026-06-29T00:00:00Z",
            }
        ],
        "updated_at": "2026-06-29T00:00:00Z",
    }


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


class DanceMimicStoryboardContinuationContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        metadata.create_all(self.engine)
        self.workspace = self.root / "sessions" / "1" / "workspace"
        self.workspace.mkdir(parents=True)
        write_json(self.workspace / SOURCE_REL, dance_mimic_source_storyboard())
        write_json(self.workspace / SEED_REL, dance_mimic_seed())
        (self.workspace / REFERENCE_REL).parent.mkdir(parents=True, exist_ok=True)
        (self.workspace / REFERENCE_REL).write_bytes(b"masked-reference-video")
        (self.workspace / TARGET_IDENTITY_REL).parent.mkdir(parents=True, exist_ok=True)
        (self.workspace / TARGET_IDENTITY_REL).write_bytes(b"target-image")
        (self.workspace / AUDIO_REL).write_bytes(b"RIFF....WAVEfmt ")
        (self.workspace / SHOT_PLAN_REL).write_bytes(b"shot-plan-video")
        with self.engine.begin() as conn:
            conn.execute(
                insert(sessions).values(
                    id=1,
                    source="openclip-analysis",
                    group_id="openclip-analysis",
                    sender_name="DanceMimic",
                    title="DanceMimic fixture",
                    command_text="",
                    status="waiting_input",
                    workspace_dir=str(self.workspace),
                    share_token="share-test",
                    created_at=1780999609000,
                    updated_at=1780999609000,
                )
            )
            conn.execute(
                insert(openclip_tasks).values(
                    id=1,
                    session_id=1,
                    status="completed",
                    workflow_mode=WORKFLOW_DANCE_MIMIC_V1,
                    reference_video_path=str(self.root / "reference.mp4"),
                    analysis_goal="DanceMimic reference motion mimic",
                    video_formula="dance_mimic_motion_reference",
                    run_model_provider="openrouter",
                    run_model_id="bytedance/seedance-2.0",
                    created_at=1780999609000,
                    updated_at=1780999609000,
                )
            )
        ctx = SimpleNamespace(engine=self.engine, session_repo=FakeSessionRepo(self.engine))
        self.app = FastAPI()
        storyboard_router = build_koubo_storyboard_router(ctx)
        self.app.include_router(storyboard_router)
        self.storyboard_deps = storyboard_router.storyboard_context

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def test_storyboard_detail_preserves_dance_mimic_reference_metadata(self) -> None:
        status, detail = request(self.app, "GET", "/api/koubo-storyboard/tasks/1")

        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["meta"]["workflow_mode"], WORKFLOW_DANCE_MIMIC_V1)
        self.assertEqual(detail["meta"]["source_type"], "dance_mimic_v1_storyboard")
        self.assertEqual(detail["plan"]["workflow_mode"], WORKFLOW_DANCE_MIMIC_V1)
        self.assertEqual(detail["plan"]["source_type"], "dance_mimic_v1_storyboard")
        dialogue = detail["plan"]["shots"][0]["scenes"][0]["dialogues"][0]
        self.assertEqual(dialogue["dialogue_asset_key"], "dak_0001")
        self.assertEqual(dialogue["dance_mimic"]["reference_video_path"], REFERENCE_REL)
        self.assertEqual(dialogue["dance_mimic"]["reference_video_role"], "dance_mimic_segment_motion_reference")
        self.assertEqual(dialogue["dance_mimic"]["target_identity_image_path"], TARGET_IDENTITY_REL)
        self.assertEqual(dialogue["image_path"], TARGET_IDENTITY_REL)
        self.assertEqual(dialogue["source_image_paths"], [TARGET_IDENTITY_REL])
        self.assertEqual(dialogue["working_assets"]["audio"]["source_type"], "dance_mimic_reference_audio")
        self.assertEqual(dialogue["working_assets"]["audio"]["path"], AUDIO_REL)
        self.assertEqual(dialogue["working_assets"]["images"][0]["source_type"], "dance_mimic_target_identity")
        self.assertEqual(dialogue["working_assets"]["images"][0]["path"], FIRST_FRAME_REL)
        self.assertEqual(dialogue["working_assets"]["video"]["slot"], "Video_Final")
        self.assertEqual(dialogue["working_assets"]["video"]["path"], "")
        self.assertEqual(detail["plan"]["compose_assets"]["shot_plan"]["status"], "completed")
        self.assertEqual(detail["plan"]["compose_assets"]["shot_plan"]["video_path"], SHOT_PLAN_REL)
        seed = json.loads((self.workspace / SEED_REL).read_text(encoding="utf-8"))
        self.assertEqual(seed["segments"][0]["provider"], "openrouter")
        self.assertEqual(seed["segments"][0]["reference_mode"], "input_references")
        self.assertEqual(seed["segments"][0]["reference_video_path"], REFERENCE_REL)
        self.assertEqual(detail["stale"]["active_count"], 0)

    def test_storyboard_save_does_not_drop_dance_mimic_reference_or_bind_reference_as_final(self) -> None:
        status, detail = request(self.app, "GET", "/api/koubo-storyboard/tasks/1")
        self.assertEqual(status, 200, detail)
        plan = detail["plan"]
        plan["shots"][0]["scenes"][0]["dialogues"][0]["text"] = "edited motion note"

        save_status, saved = request(self.app, "PUT", "/api/koubo-storyboard/tasks/1", {"plan": plan})

        self.assertEqual(save_status, 200, saved)
        saved_dialogue = saved["plan"]["shots"][0]["scenes"][0]["dialogues"][0]
        self.assertEqual(saved_dialogue["dance_mimic"]["reference_video_path"], REFERENCE_REL)
        self.assertEqual(saved_dialogue["dance_mimic"]["target_identity_image_path"], TARGET_IDENTITY_REL)
        self.assertEqual(saved_dialogue["image_path"], TARGET_IDENTITY_REL)
        self.assertEqual(saved_dialogue["working_assets"]["images"][0]["path"], FIRST_FRAME_REL)
        self.assertEqual(saved_dialogue["working_assets"]["video"]["path"], "")
        source = json.loads((self.workspace / SOURCE_REL).read_text(encoding="utf-8"))
        source_dialogue = source["shots"][0]["scenes"][0]["dialogue_items"][0]
        self.assertEqual(source_dialogue["dance_mimic"]["reference_video_path"], REFERENCE_REL)
        self.assertEqual(source_dialogue["dance_mimic"]["target_identity_image_path"], TARGET_IDENTITY_REL)
        self.assertEqual(source_dialogue["image_path"], TARGET_IDENTITY_REL)
        self.assertEqual(source_dialogue["working_assets"]["images"][0]["path"], FIRST_FRAME_REL)
        self.assertEqual(source_dialogue["working_assets"]["video"]["path"], "")
        self.assertNotEqual(source_dialogue["working_assets"]["video"]["path"], REFERENCE_REL)

    def test_storyboard_detail_reports_dance_mimic_stale_manifest(self) -> None:
        write_json(self.workspace / STALE_REL, dance_mimic_stale_manifest())

        status, detail = request(self.app, "GET", "/api/koubo-storyboard/tasks/1")

        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["stale"]["path"], STALE_REL)
        self.assertEqual(detail["stale"]["active_count"], 1)
        self.assertEqual(detail["meta"]["stale"]["items"]["video_generation_plan"]["status"], "stale")
        self.assertIn("dance_mimic_storyboard_stale", {item["code"] for item in detail["warnings"]})

    def test_video_plan_execute_blocks_stale_dance_mimic_video_plan(self) -> None:
        write_json(self.workspace / VIDEO_PLAN_REL, dance_mimic_video_plan())
        write_json(self.workspace / STALE_REL, dance_mimic_stale_manifest())

        status, payload = request(self.app, "POST", "/api/koubo-storyboard/tasks/1/video-plan/execute", {"plan_hash": "hash-stale"})

        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["detail"]["code"], "dance_mimic_video_plan_stale")
        self.assertEqual(payload["detail"]["stale"]["items"]["video_generation_plan"]["reason"], "reference_face_mask_force_rerun")

    def test_video_plan_regenerate_clears_dance_mimic_stale_manifest_item(self) -> None:
        write_json(self.workspace / VIDEO_PLAN_REL, dance_mimic_video_plan())
        write_json(self.workspace / STALE_REL, dance_mimic_stale_manifest())
        old_cache_matches = self.storyboard_deps.video_plan_cache_matches
        old_run_tool = self.storyboard_deps.run_video_plan_tool

        def fake_cache_matches(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
            return True, "cache_signature_matched"

        def fake_run_tool(workspace: Path, target: dict[str, str], settings: dict[str, float]) -> tuple[dict[str, Any], str, str, int]:
            write_json(workspace / VIDEO_PLAN_REL, {
                **dance_mimic_video_plan(plan_hash="hash-regenerated"),
                "target": target,
                "settings": settings,
            })
            return {"status": "completed"}, "", "", 0

        try:
            self.storyboard_deps.video_plan_cache_matches = fake_cache_matches
            self.storyboard_deps.run_video_plan_tool = fake_run_tool
            status, payload = request(self.app, "POST", "/api/koubo-storyboard/tasks/1/video-plan", {})
        finally:
            self.storyboard_deps.video_plan_cache_matches = old_cache_matches
            self.storyboard_deps.run_video_plan_tool = old_run_tool

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["cache_status"], "regenerated")
        self.assertEqual(payload["reason"], "dance_mimic_video_plan_stale")
        self.assertIn("video_generation_plan", payload["cleared_stale_items"])
        refreshed = json.loads((self.workspace / STALE_REL).read_text(encoding="utf-8"))
        self.assertNotIn("video_generation_plan", refreshed["items"])
        self.assertEqual(refreshed["events"][-1]["event"], "cleared_stale")


if __name__ == "__main__":
    unittest.main()
