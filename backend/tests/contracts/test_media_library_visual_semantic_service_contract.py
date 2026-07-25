from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_assets,
    media_library_fragment_index,
    media_library_tasks,
    metadata,
    session_files,
    sessions,
)
from opcrew_backend.media_library_analysis import (  # noqa: E402
    visual_semantic,
)
from opcrew_backend.media_library_analysis.contracts import (  # noqa: E402
    result_hash,
)
from opcrew_backend.media_library_analysis.run_repository import (  # noqa: E402
    AnalysisRunRepository,
)
from opcrew_backend.media_library_analysis.visual_semantic import (  # noqa: E402
    VisualSemanticService,
    load_visual_semantic_result,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibraryFragmentPublisher,
)
from opcrew_backend.media_library_analysis.visual_semantic_contracts import (  # noqa: E402
    INPUT_KEYFRAMES_REL,
    INPUT_MANIFEST_REL,
    MANIFEST_PATH,
    QUALITY_PATH,
    RESULT_PATH,
)
from opcrew_backend.repositories.media_library import (  # noqa: E402
    MediaLibraryRepository,
)
from opcrew_backend.repositories.media_library_tasks import (  # noqa: E402
    MediaLibraryTaskRepository,
)
from opcrew_backend.repositories.sessions import SessionRepository  # noqa: E402
from opcrew_backend.services.session_events import (  # noqa: E402
    SessionEventService,
)


ASSET_ID = "asset-visual-semantic-service"
SOURCE_VERSION = "a" * 64
STRUCTURE_TOOL_SESSION_ID = "tus-visual-structure-source"
MODEL_CONFIG_VERSION = "test-visual-model-policy-v1"
PROMPT_VERSION = "visual-semantic-service-contract-v1"
_REAL_THREAD = threading.Thread


class _ImmediateServiceThread:
    def __init__(self, *, target: object, kwargs: dict[str, object], **_: object) -> None:
        self.target = target
        self.kwargs = kwargs

    def start(self) -> None:
        self.target(**self.kwargs)


def _thread_factory(*args: object, **kwargs: object) -> object:
    name = str(kwargs.get("name") or "")
    if name.startswith("open-cut-visual-semantic-"):
        return _ImmediateServiceThread(
            target=kwargs["target"],
            kwargs=dict(kwargs.get("kwargs") or {}),
        )
    return _REAL_THREAD(*args, **kwargs)


class _FakeCloudVisualClient:
    def __init__(self, responses: list[dict[str, object]] | None = None) -> None:
        self.responses = deque(responses or [])
        self.created_sessions: list[str] = []
        self.prompt_calls: list[dict[str, object]] = []
        self._messages: dict[str, list[dict[str, object]]] = {}

    def providers(self) -> dict[str, object]:
        return {
            "connected": ["fake-cloud"],
            "default": {"fake-cloud": "fake-vlm"},
            "all": [
                {
                    "id": "fake-cloud",
                    "name": "Fake Cloud",
                    "models": {
                        "fake-vlm": {
                            "id": "fake-vlm",
                            "name": "Fake VLM",
                            "modalities": {"input": ["text", "image"]},
                            "max_images_per_request": 4,
                        }
                    },
                }
            ],
        }

    def create_session(self, _title: str) -> dict[str, str]:
        session_id = f"model-session-{len(self.created_sessions) + 1}"
        self.created_sessions.append(session_id)
        self._messages[session_id] = []
        return {"id": session_id}

    def prompt_async(
        self,
        session_id: str,
        text: str,
        *,
        model: dict[str, str],
        system: str,
        tools: dict[str, object],
        parts: list[dict[str, object]],
    ) -> None:
        if not self.responses:
            raise AssertionError("unexpected_fake_vlm_call")
        response = self.responses.popleft()
        self.prompt_calls.append(
            {
                "session_id": session_id,
                "text": text,
                "model": model,
                "system": system,
                "tools": tools,
                "parts": parts,
            }
        )
        self._messages[session_id].append(
            {
                "info": {
                    "role": "assistant",
                    "time": {"completed": 9_999_999_999_999},
                },
                "parts": [
                    {
                        "type": "text",
                        "text": json.dumps(response, ensure_ascii=False),
                    }
                ],
            }
        )

    def messages(
        self,
        session_id: str,
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        return self._messages.get(session_id, [])[-limit:]

    def abort(self, _session_id: str) -> None:
        return None


class _FakeUsageRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_with_result(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        index = len(self.calls)
        return SimpleNamespace(
            request_id=f"usage-request-{index}",
            local_usage_id=f"usage-local-{index}",
            inserted=True,
        )


def _valid_description() -> dict[str, object]:
    return {
        "visual_summary": "一名讲解者站在室内白板旁。",
        "people": ["一名讲解者"],
        "objects": ["白板"],
        "scene": "室内演示空间",
        "action": None,
        "keywords": ["讲解者", "白板"],
        "claim_evidence": {
            "people": ["scene_0001-sample-01"],
            "objects": ["scene_0001-sample-02"],
            "scene": ["scene_0001-sample-04"],
            "action": [],
        },
        "confidence": 0.88,
        "needs_review": False,
    }


def _invalid_description_requiring_repair() -> dict[str, object]:
    value = _valid_description()
    value["claim_evidence"] = {
        "people": [],
        "objects": ["scene_0001-sample-02"],
        "scene": ["scene_0001-sample-04"],
        "action": [],
    }
    return value


class MediaLibraryVisualSemanticServiceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.data_dir = self.root / "data"
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            self.session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="open-cut-v1",
                        group_id="open-cut-v1",
                        title="visual semantic service contract",
                        status="draft",
                        workspace_dir=str(self.workspace),
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id=ASSET_ID,
                    session_id=self.session_id,
                    display_name="visual semantic service contract",
                    original_filename="source.mp4",
                    source_video_path="inbox/source.mp4",
                    content_sha256=SOURCE_VERSION,
                    content_hashed_at=1,
                    media_type="video",
                    duration_ms=3000,
                    upload_status="ready",
                    analysis_status="not_analyzed",
                    subtitle_mode="unknown",
                    analysis_summary_json={},
                    tags_json=[],
                    archived=False,
                    referenced_by_count=0,
                    created_at=1,
                    updated_at=1,
                )
            )
            self.task_id = int(
                conn.execute(
                    media_library_tasks.insert()
                    .values(
                        asset_id=ASSET_ID,
                        session_id=self.session_id,
                        title="visual semantic service contract",
                        status="draft",
                        dialogue_status="not_analyzed",
                        visual_status="not_analyzed",
                        visual_structure_status="not_analyzed",
                        visual_semantic_status="not_analyzed",
                        composite_status="not_analyzed",
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(media_library_tasks.c.id)
                ).scalar_one()
            )

        self.session_repo = SessionRepository(self.engine)
        self.asset_repo = MediaLibraryRepository(self.engine)
        self.task_repo = MediaLibraryTaskRepository(self.engine)
        self.run_repo = AnalysisRunRepository(self.engine)
        self.usage = _FakeUsageRecorder()
        self.ctx = SimpleNamespace(
            engine=self.engine,
            data_dir=self.data_dir,
            session_repo=self.session_repo,
            media_library_repo=self.asset_repo,
            media_library_task_repo=self.task_repo,
            media_analysis_run_repo=self.run_repo,
            local_usage=self.usage,
        )
        self.ctx.session_event_service = SessionEventService(
            self.session_repo,
            visual_semantic.now_ms,
        )
        self.service = VisualSemanticService(self.ctx)
        self._prepare_structure_run()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def _prepare_structure_run(self) -> None:
        source_video = self.workspace / "inbox" / "source.mp4"
        source_video.parent.mkdir()
        source_video.write_bytes(b"source-video-must-not-enter-semantic-run")

        queued = self.run_repo.create_queued(
            asset_id=ASSET_ID,
            scheme="visual_structure",
            timestamp=100,
        )
        self.structure_run_id = str(queued["analysis_run_id"])
        structure_root = (
            self.workspace
            / "tool_use_sessions"
            / STRUCTURE_TOOL_SESSION_ID
        )
        visual_root = structure_root / "SessionOutput" / "visual"
        keyframe_root = visual_root / "scene_frames"
        keyframe_root.mkdir(parents=True)
        self.keyframe_bytes = (
            b"\xff\xd8\xff\xe0"
            b"controlled-current-keyframe"
            b"\xff\xd9"
        )
        self.keyframe_hash = hashlib.sha256(
            self.keyframe_bytes
        ).hexdigest()
        self.structure_keyframe_paths = []
        for index in range(1, 5):
            keyframe_path = (
                keyframe_root / f"scene_0001-sample-{index:02d}.jpg"
            )
            keyframe_path.write_bytes(self.keyframe_bytes)
            self.structure_keyframe_paths.append(keyframe_path)
        self.structure_payload = {
            "schema_version": "media_library_visual_structure_v2",
            "asset_id": ASSET_ID,
            "source_version": SOURCE_VERSION,
            "analysis_run_id": self.structure_run_id,
            "sampling_strategy": "scene_uniform_4_v1",
            "items": [
                {
                    "fragment_id": "scene_0001",
                    "start_ms": 0,
                    "end_ms": 3000,
                    "duration_ms": 3000,
                    "keyframes": [
                        {
                            "keyframe_id": f"scene_0001-sample-{index:02d}",
                            "keyframe_time_ms": time_ms,
                            "image_path": (
                                "SessionOutput/visual/scene_frames/"
                                f"scene_0001-sample-{index:02d}.jpg"
                            ),
                            "image_sha256": self.keyframe_hash,
                        }
                        for index, time_ms in enumerate(
                            (375, 1125, 1875, 2625), start=1
                        )
                    ],
                    "sampling_strategy": "scene_uniform_4_v1",
                }
            ],
        }
        self.structure_hash = result_hash(self.structure_payload)
        segments_path = visual_root / "visual_structure_segments.json"
        segments_path.write_text(
            json.dumps(self.structure_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        (visual_root / "visual_structure_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": (
                        "media_library_visual_structure_manifest_v2"
                    ),
                    "asset_id": ASSET_ID,
                    "source_version": SOURCE_VERSION,
                    "analysis_run_id": self.structure_run_id,
                    "result_hash": self.structure_hash,
                    "result_path": (
                        "SessionOutput/visual/"
                        "visual_structure_segments.json"
                    ),
                    "sampling_strategy": "scene_uniform_4_v1",
                    "fragment_count": 1,
                    "keyframe_count": 4,
                }
            ),
            encoding="utf-8",
        )
        result_index_path = (
            f"tool_use_sessions/{STRUCTURE_TOOL_SESSION_ID}/"
            "SessionOutput/visual/visual_structure_segments.json"
        )
        self.run_repo.mark_running(
            self.structure_run_id,
            timestamp=101,
            tool_use_session_id=STRUCTURE_TOOL_SESSION_ID,
        )
        self.run_repo.activate_ready(
            self.structure_run_id,
            timestamp=102,
            schema_version="media_library_visual_structure_v2",
            result_hash=self.structure_hash,
            result_index_path=result_index_path,
        )
        with self.engine.begin() as conn:
            conn.execute(
                session_files.insert().values(
                    session_id=self.session_id,
                    path=result_index_path,
                    kind="artifact",
                    size=segments_path.stat().st_size,
                    origin="tool_session",
                    downloadable=0,
                    visibility="internal",
                    sensitivity="normal",
                    tool_use_session_id=STRUCTURE_TOOL_SESSION_ID,
                    stale=0,
                    updated_at=102,
                )
            )
            for keyframe_path in self.structure_keyframe_paths:
                relative = (
                    f"tool_use_sessions/{STRUCTURE_TOOL_SESSION_ID}/"
                    "SessionOutput/visual/scene_frames/"
                    f"{keyframe_path.name}"
                )
                conn.execute(
                    session_files.insert().values(
                        session_id=self.session_id,
                        path=relative,
                        kind="artifact",
                        size=keyframe_path.stat().st_size,
                        origin="tool_session",
                        downloadable=0,
                        visibility="internal",
                        sensitivity="normal",
                        tool_use_session_id=STRUCTURE_TOOL_SESSION_ID,
                        stale=0,
                        updated_at=102,
                    )
                )

    def _model_patches(
        self,
        client: _FakeCloudVisualClient,
        *,
        link_failure: bool = False,
    ) -> list[object]:
        model = {
            "providerID": "fake-cloud",
            "modelID": "fake-vlm",
        }
        patches: list[object] = [
            patch.object(
                visual_semantic.threading,
                "Thread",
                side_effect=_thread_factory,
            ),
            patch.object(
                visual_semantic,
                "surface_policy",
                return_value={
                    "mode": "alias",
                    "alias_only": True,
                    "read_only": True,
                    "required_input_modalities": ["image"],
                    "version": MODEL_CONFIG_VERSION,
                },
            ),
            patch.object(
                visual_semantic,
                "opencode_client_for_context",
                return_value=client,
            ),
            patch.object(
                visual_semantic,
                "resolve_prompt_model_for_role",
                return_value=(model, {}),
            ),
            patch.object(
                visual_semantic,
                "_is_local_model_provider",
                return_value=False,
            ),
        ]
        if link_failure:
            patches.append(
                patch.object(
                    visual_semantic.os,
                    "link",
                    side_effect=OSError("cross-device link"),
                )
            )
        return patches

    def _start(
        self,
        client: _FakeCloudVisualClient,
        *,
        allow_cloud: bool,
        force: bool = False,
        link_failure: bool = False,
        visual_search_enabled: bool = False,
    ) -> dict[str, object]:
        started: list[object] = []
        try:
            for item in self._model_patches(
                client,
                link_failure=link_failure,
            ):
                item.start()
                started.append(item)
            with patch.dict(
                os.environ,
                {
                    "OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": (
                        "true" if visual_search_enabled else "false"
                    )
                },
                clear=False,
            ):
                return self.service.start(
                    ASSET_ID,
                    force=force,
                    allow_cloud_visual_data_transfer=allow_cloud,
                    visual_prompt_version=PROMPT_VERSION,
                )
        finally:
            for item in reversed(started):
                item.stop()

    def _run_root(self, run: dict[str, object]) -> Path:
        tool_use_session_id = str(run.get("tool_use_session_id") or "")
        self.assertTrue(tool_use_session_id)
        return (
            self.workspace
            / "tool_use_sessions"
            / tool_use_session_id
        )

    def _assert_semantic_snapshot_has_no_source_video(
        self,
        run_root: Path,
    ) -> None:
        input_manifest = json.loads(
            (
                run_root
                / "0_SessionContext"
                / "InputManifest.json"
            ).read_text(encoding="utf-8")
        )
        variables = json.loads(
            (
                run_root / "0_SessionContext" / "Variables.json"
            ).read_text(encoding="utf-8")
        )
        frozen = json.loads(
            (run_root / INPUT_MANIFEST_REL).read_text(encoding="utf-8")
        )
        paths = [str(item["path"]) for item in input_manifest["files"]]
        source_refs = [
            str(item.get("source_ref") or "")
            for item in input_manifest["files"]
        ]
        self.assertEqual(variables["source_video_path"], "")
        self.assertFalse(any("Video_Source" in path for path in paths))
        self.assertFalse(
            any(path.endswith("inbox/source.mp4") for path in source_refs)
        )
        self.assertNotIn("source_video_path", frozen)
        self.assertEqual(
            frozen["visual_structure_run_id"],
            self.structure_run_id,
        )
        self.assertEqual(
            frozen["visual_structure_result_hash"],
            self.structure_hash,
        )
        self.assertEqual(
            frozen["keyframes"],
            [
                {
                    "keyframe_id": f"scene_0001-sample-{index:02d}",
                    "image_sha256": self.keyframe_hash,
                }
                for index in range(1, 5)
            ],
        )

    def test_unapproved_cloud_model_blocks_business_and_tool_session(
        self,
    ) -> None:
        client = _FakeCloudVisualClient()
        response = self._start(client, allow_cloud=False)
        run = self.run_repo.get(str(response["semantic_run_id"]))
        assert run is not None
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(
            run["error_code"],
            "cloud_visual_data_transfer_not_authorized",
        )
        self.assertFalse(run["is_current"])
        self.assertIsNone(run["model_session_id"])
        run_root = self._run_root(run)
        summary = json.loads(
            (
                run_root
                / "SessionReport"
                / "SessionRunSummary.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(summary["status"], "blocked")
        step = next(
            item for item in summary["steps"] if item["tool_id"] == "03_03"
        )
        self.assertEqual(step["status"], "blocked")
        self._assert_semantic_snapshot_has_no_source_video(run_root)
        target = (
            run_root
            / INPUT_KEYFRAMES_REL
            / "scene_0001-sample-01.jpg"
        )
        self.assertEqual(target.read_bytes(), self.keyframe_bytes)
        self.assertEqual(
            target.stat().st_ino,
            self.structure_keyframe_paths[0].stat().st_ino,
        )
        self.assertEqual(client.created_sessions, [])

    def test_keyframe_snapshot_copies_when_hard_link_is_unavailable(
        self,
    ) -> None:
        client = _FakeCloudVisualClient()
        response = self._start(
            client,
            allow_cloud=False,
            link_failure=True,
        )
        run = self.run_repo.get(str(response["semantic_run_id"]))
        assert run is not None
        self.assertEqual(run["status"], "blocked")
        run_root = self._run_root(run)
        self._assert_semantic_snapshot_has_no_source_video(run_root)
        target = (
            run_root
            / INPUT_KEYFRAMES_REL
            / "scene_0001-sample-01.jpg"
        )
        self.assertEqual(target.read_bytes(), self.keyframe_bytes)
        self.assertEqual(
            hashlib.sha256(target.read_bytes()).hexdigest(),
            self.keyframe_hash,
        )
        self.assertNotEqual(
            target.stat().st_ino,
            self.structure_keyframe_paths[0].stat().st_ino,
        )

    def test_fake_vlm_repairs_once_publishes_and_second_run_hits_cache(
        self,
    ) -> None:
        client = _FakeCloudVisualClient(
            [
                _invalid_description_requiring_repair(),
                _valid_description(),
            ]
        )
        response = self._start(client, allow_cloud=True)
        first_run_id = str(response["semantic_run_id"])
        first = self.run_repo.get(first_run_id)
        assert first is not None
        self.assertEqual(first["status"], "ready")
        self.assertTrue(first["is_current"])
        self.assertEqual(first["model_session_id"], "model-session-1")
        self.assertEqual(
            first["upstream_refs_json"],
            {
                "visual_structure_run_id": self.structure_run_id,
                "visual_structure_result_hash": self.structure_hash,
            },
        )
        first_root = self._run_root(first)
        self._assert_semantic_snapshot_has_no_source_video(first_root)
        payload = json.loads(
            (first_root / RESULT_PATH).read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (first_root / MANIFEST_PATH).read_text(encoding="utf-8")
        )
        quality = json.loads(
            (first_root / QUALITY_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(first["result_hash"], result_hash(payload))
        self.assertEqual(manifest["result_hash"], first["result_hash"])
        self.assertEqual(
            payload["visual_structure_result_hash"],
            self.structure_hash,
        )
        self.assertEqual(quality["structured_repair_count"], 1)
        self.assertEqual(quality["model_call_count"], 2)
        self.assertEqual(quality["image_count"], 8)
        self.assertEqual(quality["cache_hit_count"], 0)
        self.assertEqual(len(client.prompt_calls), 2)
        self.assertEqual(len(self.usage.calls), 2)
        self.assertTrue(
            all(call["units"]["image_count"] == 4 for call in self.usage.calls)
        )
        first_summary = json.loads(
            (
                first_root
                / "SessionReport"
                / "SessionRunSummary.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(first_summary["status"], "completed")
        first_step = next(
            item
            for item in first_summary["steps"]
            if item["tool_id"] == "03_03"
        )
        self.assertEqual(first_step["status"], "completed")
        self.assertTrue(
            any(
                part.get("type") == "file"
                and str(part.get("url") or "").startswith(
                    "data:image/jpeg;base64,"
                )
                for call in client.prompt_calls
                for part in call["parts"]
            )
        )
        self.assertTrue(
            all(
                len(call["parts"]) == 5
                and sum(
                    1 for part in call["parts"] if part.get("type") == "file"
                )
                == 4
                for call in client.prompt_calls
            )
        )
        prompt_manifest = json.loads(
            next(
                first_root.glob(
                    "S1_*/Prompt/PromptManifest.json"
                )
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(prompt_manifest["model_calls"]), 2)
        self.assertFalse(prompt_manifest["model_calls"][0]["repair"])
        self.assertTrue(prompt_manifest["model_calls"][1]["repair"])
        current = self.run_repo.current(ASSET_ID, "visual_semantic")
        assert current is not None
        self.assertEqual(current["analysis_run_id"], first_run_id)
        loaded = load_visual_semantic_result(
            workspace=self.workspace,
            run=current,
        )
        self.assertIsNone(loaded["error"])
        self.assertEqual(
            loaded["items"][0]["fragment_id"],
            "scene_0001",
        )
        registered = {
            str(item["path"])
            for item in self.session_repo.list_files(self.session_id)
            if item.get("tool_use_session_id")
            == first["tool_use_session_id"]
        }
        first_prefix = (
            f"tool_use_sessions/{first['tool_use_session_id']}/"
        )
        self.assertIn(first_prefix + RESULT_PATH, registered)
        self.assertIn(first_prefix + MANIFEST_PATH, registered)

        cached_response = self._start(
            client,
            allow_cloud=True,
            force=True,
        )
        second_run_id = str(cached_response["semantic_run_id"])
        second = self.run_repo.get(second_run_id)
        assert second is not None
        self.assertEqual(second["status"], "ready")
        self.assertTrue(second["is_current"])
        self.assertEqual(second["model_session_id"], "model-session-2")
        self.assertEqual(
            second["upstream_refs_json"],
            first["upstream_refs_json"],
        )
        first_after = self.run_repo.get(first_run_id)
        assert first_after is not None
        self.assertFalse(first_after["is_current"])
        second_root = self._run_root(second)
        second_quality = json.loads(
            (second_root / QUALITY_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(second_quality["cache_hit_count"], 1)
        self.assertEqual(second_quality["model_call_count"], 0)
        self.assertEqual(
            second_quality["structured_repair_count"],
            0,
        )
        self.assertEqual(len(client.prompt_calls), 2)
        self.assertEqual(len(self.usage.calls), 2)
        current_after = self.run_repo.current(
            ASSET_ID,
            "visual_semantic",
        )
        assert current_after is not None
        self.assertEqual(
            current_after["analysis_run_id"],
            second_run_id,
        )

    def test_enabled_r1_atomically_publishes_four_frame_visual_index(
        self,
    ) -> None:
        self.ctx.media_library_fragment_publisher = (
            MediaLibraryFragmentPublisher(self.engine)
        )
        client = _FakeCloudVisualClient([_valid_description()])
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "true"},
            clear=False,
        ):
            response = self._start(
                client,
                allow_cloud=True,
                visual_search_enabled=True,
            )
        run = self.run_repo.get(str(response["semantic_run_id"]))
        assert run is not None
        self.assertEqual(run["status"], "ready")
        self.assertTrue(run["is_current"])
        with self.engine.connect() as conn:
            fragment = conn.execute(
                select(media_library_fragment_index).where(
                    media_library_fragment_index.c.analysis_run_id
                    == run["analysis_run_id"]
                )
            ).mappings().one()
            task = conn.execute(
                select(media_library_tasks).where(
                    media_library_tasks.c.asset_id == ASSET_ID
                )
            ).mappings().one()
        self.assertEqual(fragment["analysis_scheme"], "visual_semantic")
        self.assertEqual(fragment["summary"], _valid_description()["visual_summary"])
        self.assertEqual(
            fragment["keyframe_ref_json"],
            [
                f"scene_0001-sample-{index:02d}"
                for index in range(1, 5)
            ],
        )
        self.assertTrue(fragment["is_active"])
        self.assertEqual(task["visual_semantic_status"], "ready")
        self.assertEqual(task["visual_status"], "ready")


if __name__ == "__main__":
    unittest.main()
