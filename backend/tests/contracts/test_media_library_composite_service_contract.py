from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_fragment_index,
    media_library_tasks,
    metadata,
    session_files,
    sessions,
)
from opcrew_backend.media_library_analysis import composite  # noqa: E402
from opcrew_backend.media_library_analysis.composite import (  # noqa: E402
    CompositeAnalysisService,
)
from opcrew_backend.media_library_analysis.composite_contracts import (  # noqa: E402
    INDEX_PATH,
    INPUT_DIALOGUE_REL,
    INPUT_SEMANTIC_REL,
    INPUT_STRUCTURE_REL,
    QUALITY_PATH,
    RESULT_PATH,
    SEARCH_MANIFEST_PATH,
    VIRTUAL_CLIPS_PATH,
)
from opcrew_backend.media_library_analysis.contracts import (  # noqa: E402
    result_hash,
)
from opcrew_backend.media_library_analysis.run_repository import (  # noqa: E402
    AnalysisRunRepository,
)
from opcrew_backend.media_library_search.repository import (  # noqa: E402
    MediaLibraryFragmentPublisher,
)
from opcrew_backend.repositories.media_library import (  # noqa: E402
    MediaLibraryRepository,
)
from opcrew_backend.repositories.media_library_tasks import (  # noqa: E402
    MediaLibraryTaskRepository,
)
from opcrew_backend.repositories.sessions import SessionRepository  # noqa: E402
from opcrew_backend.routes.media_library import (  # noqa: E402
    _serialize_analysis_run,
)
from opcrew_backend.services.session_events import (  # noqa: E402
    SessionEventService,
)


ASSET_ID = "asset-composite-service"
SOURCE_VERSION = "a" * 64
PROMPT_VERSION = "composite-service-contract-v1"
MODEL_CONFIG_VERSION = "composite-policy-contract-v1"
_REAL_THREAD = threading.Thread


class _ImmediateCompositeThread:
    def __init__(
        self,
        *,
        target: Any,
        kwargs: dict[str, object],
        **_: object,
    ) -> None:
        self.target = target
        self.kwargs = kwargs

    def start(self) -> None:
        self.target(**self.kwargs)


def _thread_factory(*args: object, **kwargs: object) -> object:
    name = str(kwargs.get("name") or "")
    if name.startswith("open-cut-composite-"):
        return _ImmediateCompositeThread(
            target=kwargs["target"],
            kwargs=dict(kwargs.get("kwargs") or {}),
        )
    return _REAL_THREAD(*args, **kwargs)


class _FakeTextClient:
    def __init__(
        self,
        responses: list[dict[str, object] | str] | None = None,
    ) -> None:
        self.responses = deque(responses or [])
        self.created_sessions: list[str] = []
        self.prompt_calls: list[dict[str, object]] = []
        self._messages: dict[str, list[dict[str, object]]] = {}

    def providers(self) -> dict[str, object]:
        return {
            "connected": ["fake-text-cloud"],
            "default": {"fake-text-cloud": "fake-composite-model"},
            "all": [
                {
                    "id": "fake-text-cloud",
                    "name": "Fake Text Cloud",
                    "models": {
                        "fake-composite-model": {
                            "id": "fake-composite-model",
                            "name": "Fake Composite Model",
                            "modalities": {"input": ["text"]},
                        }
                    },
                }
            ],
        }

    def create_session(self, _title: str) -> dict[str, str]:
        session_id = (
            f"composite-model-session-{len(self.created_sessions) + 1}"
        )
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
            raise AssertionError("unexpected_fake_composite_model_call")
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
        assistant_text = (
            response
            if isinstance(response, str)
            else json.dumps(response, ensure_ascii=False)
        )
        self._messages[session_id].append(
            {
                "info": {
                    "role": "assistant",
                    "time": {"completed": 9_999_999_999_999},
                },
                "parts": [{"type": "text", "text": assistant_text}],
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


class _BarrierTextClient(_FakeTextClient):
    def __init__(
        self,
        responses: list[dict[str, object] | str],
    ) -> None:
        super().__init__(responses)
        self.entered = threading.Event()
        self.release = threading.Event()

    def prompt_async(self, *args: object, **kwargs: object) -> None:
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test_composite_model_barrier_timeout")
        super().prompt_async(*args, **kwargs)


class _FakeUsageRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_with_result(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        index = len(self.calls)
        return SimpleNamespace(
            request_id=f"composite-usage-request-{index}",
            local_usage_id=f"composite-usage-local-{index}",
            inserted=True,
        )


def _valid_model_candidate() -> dict[str, object]:
    return {
        "items": [
            {
                "start_ms": 0,
                "end_ms": 2000,
                "title": "讲解桌面产品用途",
                "summary": "讲解者在室内展示并介绍桌面产品。",
                "keywords": ["产品讲解", "室内演示"],
                "people": ["一名讲解者"],
                "objects": ["桌面产品"],
                "scene": "室内演示区",
                "action": None,
                "dialogue_refs": ["dialogue_0001"],
                "visual_refs": ["scene_0001"],
                "visual_claim_refs": {
                    "people": ["scene_0001"],
                    "objects": ["scene_0001"],
                    "scene": ["scene_0001"],
                    "action": [],
                },
                "boundary_reasons": ["对白和 Scene 边界一致"],
                "confidence": 0.91,
                "needs_review": False,
            }
        ]
    }


def _unknown_reference_candidate() -> dict[str, object]:
    value = copy.deepcopy(_valid_model_candidate())
    value["items"][0]["visual_refs"] = ["scene_unknown"]
    value["items"][0]["visual_claim_refs"] = {
        "people": ["scene_unknown"],
        "objects": ["scene_unknown"],
        "scene": ["scene_unknown"],
        "action": [],
    }
    return value


def _hallucinated_visual_candidate() -> dict[str, object]:
    value = copy.deepcopy(_valid_model_candidate())
    value["items"][0]["objects"] = ["上游不存在的品牌产品"]
    return value


def _keyframe_claim_reference_candidate() -> dict[str, object]:
    value = copy.deepcopy(_valid_model_candidate())
    value["items"][0]["visual_claim_refs"] = {
        "people": ["scene_0001-sample-01"],
        "objects": ["scene_0001-sample-02"],
        "scene": ["scene_0001-sample-04"],
        "action": [],
    }
    return value


class MediaLibraryCompositeServiceContractTest(unittest.TestCase):
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
                        title="composite service contract",
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
                    display_name="composite service contract",
                    original_filename="source.mp4",
                    source_video_path="inbox/source.mp4",
                    content_sha256=SOURCE_VERSION,
                    content_hashed_at=1,
                    media_type="video",
                    duration_ms=2000,
                    width=1920,
                    height=1080,
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
                        title="composite service contract",
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
            media_library_fragment_publisher=MediaLibraryFragmentPublisher(
                self.engine
            ),
            local_usage=self.usage,
        )
        self.ctx.session_event_service = SessionEventService(
            self.session_repo,
            composite.now_ms,
        )
        self.service = CompositeAnalysisService(self.ctx)
        self.upstream = self._prepare_upstream_runs()
        self._write_unrelated_media()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def _write_unrelated_media(self) -> None:
        inbox = self.workspace / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "source.mp4").write_bytes(b"must-not-be-snapshotted")
        (inbox / "source.wav").write_bytes(b"must-not-be-snapshotted")
        frames = self.workspace / "unrelated-keyframes"
        frames.mkdir()
        (frames / "scene.jpg").write_bytes(b"must-not-be-snapshotted")

    def _activate_payload(
        self,
        *,
        scheme: str,
        payload: dict[str, object],
        timestamp: int,
        output_relative: str,
    ) -> dict[str, object]:
        queued = self.run_repo.create_queued(
            asset_id=ASSET_ID,
            scheme=scheme,
            timestamp=timestamp,
        )
        run_id = str(queued["analysis_run_id"])
        payload["analysis_run_id"] = run_id
        digest = result_hash(payload)
        tool_use_session_id = f"tus-{scheme}-{timestamp}"
        workspace_relative = (
            f"tool_use_sessions/{tool_use_session_id}/"
            f"{output_relative}"
        )
        path = self.workspace / workspace_relative
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        self.run_repo.mark_running(
            run_id,
            timestamp=timestamp + 1,
            tool_use_session_id=tool_use_session_id,
        )
        self.run_repo.activate_ready(
            run_id,
            timestamp=timestamp + 2,
            schema_version=str(payload["schema_version"]),
            result_hash=digest,
            result_index_path=workspace_relative,
        )
        with self.engine.begin() as conn:
            conn.execute(
                session_files.insert().values(
                    session_id=self.session_id,
                    path=workspace_relative,
                    kind="artifact",
                    size=path.stat().st_size,
                    origin="tool_session",
                    downloadable=0,
                    visibility="internal",
                    sensitivity="normal",
                    tool_use_session_id=tool_use_session_id,
                    stale=0,
                    updated_at=timestamp + 2,
                )
            )
        run = self.run_repo.get(run_id)
        assert run is not None
        return run

    def _prepare_upstream_runs(self) -> dict[str, dict[str, object]]:
        structure_payload: dict[str, object] = {
            "schema_version": "media_library_visual_structure_v2",
            "asset_id": ASSET_ID,
            "source_version": SOURCE_VERSION,
            "analysis_run_id": "",
            "sampling_strategy": "scene_uniform_4_v1",
            "items": [
                {
                    "fragment_id": "scene_0001",
                    "start_ms": 0,
                    "end_ms": 2000,
                    "duration_ms": 2000,
                    "keyframes": [
                        {
                            "keyframe_id": f"scene_0001-sample-{index:02d}",
                            "keyframe_time_ms": time_ms,
                            "image_path": (
                                "SessionOutput/visual/scene_frames/"
                                f"scene_0001-sample-{index:02d}.jpg"
                            ),
                            "image_sha256": "b" * 64,
                        }
                        for index, time_ms in enumerate(
                            (250, 750, 1250, 1750), start=1
                        )
                    ],
                    "sampling_strategy": "scene_uniform_4_v1",
                }
            ],
        }
        structure = self._activate_payload(
            scheme="visual_structure",
            payload=structure_payload,
            timestamp=100,
            output_relative=(
                "SessionOutput/visual/visual_structure_segments.json"
            ),
        )
        dialogue_payload: dict[str, object] = {
            "schema_version": "media_library_dialogue_fragments_v1",
            "asset_id": ASSET_ID,
            "source_version": SOURCE_VERSION,
            "analysis_run_id": "",
            "items": [
                {
                    "fragment_id": "dialogue_0001",
                    "start_ms": 0,
                    "end_ms": 2000,
                    "duration_ms": 2000,
                    "dialogue_text": "介绍桌面产品的核心用途。",
                    "keyframe_refs": [],
                }
            ],
        }
        dialogue = self._activate_payload(
            scheme="dialogue",
            payload=dialogue_payload,
            timestamp=200,
            output_relative=(
                "SessionOutput/json/dialogue_fragment_index.json"
            ),
        )
        semantic_payload: dict[str, object] = {
            "schema_version": "media_library_visual_semantic_v2",
            "asset_id": ASSET_ID,
            "source_version": SOURCE_VERSION,
            "analysis_run_id": "",
            "visual_structure_run_id": structure["analysis_run_id"],
            "visual_structure_result_hash": structure["result_hash"],
            "sampling_strategy": "scene_uniform_4_v1",
            "visual_prompt_version": "visual-semantic-prompt-v3",
            "model_config_id": "visual-semantic-model-v1",
            "items": [
                {
                    "fragment_id": "scene_0001",
                    "start_ms": 0,
                    "end_ms": 2000,
                    "duration_ms": 2000,
                    "keyframe_refs": [
                        f"scene_0001-sample-{index:02d}"
                        for index in range(1, 5)
                    ],
                    "visual_summary": (
                        "一名讲解者在室内展示桌面产品。"
                    ),
                    "people": ["一名讲解者"],
                    "objects": ["桌面产品"],
                    "scene": "室内演示区",
                    "action": None,
                    "keywords": ["室内", "桌面产品"],
                    "claim_evidence": {
                        "people": ["scene_0001-sample-01"],
                        "objects": ["scene_0001-sample-02"],
                        "scene": ["scene_0001-sample-04"],
                        "action": [],
                    },
                    "confidence": 0.9,
                    "needs_review": False,
                }
            ],
        }
        semantic = self._activate_payload(
            scheme="visual_semantic",
            payload=semantic_payload,
            timestamp=300,
            output_relative=(
                "SessionOutput/visual/visual_semantic_segments.json"
            ),
        )
        return {
            "dialogue": dialogue,
            "visual_structure": structure,
            "visual_semantic": semantic,
        }

    def _patch_model(
        self,
        stack: ExitStack,
        client: _FakeTextClient | None,
        *,
        immediate: bool,
        blocked: bool = False,
    ) -> None:
        policy = {
            "mode": "alias",
            "alias_only": True,
            "read_only": True,
            "version": MODEL_CONFIG_VERSION,
        }
        stack.enter_context(
            patch.object(
                composite,
                "surface_policy",
                return_value=policy,
            )
        )
        if immediate:
            stack.enter_context(
                patch.object(
                    composite.threading,
                    "Thread",
                    side_effect=_thread_factory,
                )
            )
        if blocked:
            stack.enter_context(
                patch.object(
                    composite,
                    "opencode_client_for_context",
                    side_effect=RuntimeError("model unavailable"),
                )
            )
            return
        assert client is not None
        stack.enter_context(
            patch.object(
                composite,
                "opencode_client_for_context",
                return_value=client,
            )
        )
        stack.enter_context(
            patch.object(
                composite,
                "resolve_prompt_model_for_role",
                return_value=(
                    {
                        "providerID": "fake-text-cloud",
                        "modelID": "fake-composite-model",
                    },
                    {},
                ),
            )
        )

    def _start(
        self,
        client: _FakeTextClient | None,
        *,
        force: bool = False,
        prompt_version: str = PROMPT_VERSION,
        blocked: bool = False,
    ) -> dict[str, object]:
        with ExitStack() as stack:
            self._patch_model(
                stack,
                client,
                immediate=True,
                blocked=blocked,
            )
            return self.service.start(
                ASSET_ID,
                force=force,
                prompt_version=prompt_version,
            )

    def _run_root(self, run: dict[str, object]) -> Path:
        tool_use_session_id = str(run.get("tool_use_session_id") or "")
        self.assertTrue(tool_use_session_id)
        return (
            self.workspace
            / "tool_use_sessions"
            / tool_use_session_id
        )

    def _assert_snapshot_is_three_json_only(self, run_root: Path) -> None:
        context_root = run_root / "0_SessionContext"
        composite_inputs = context_root / "composite_inputs"
        files = {
            path.relative_to(composite_inputs).as_posix()
            for path in composite_inputs.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            files,
            {
                "InputManifest.json",
                "dialogue_fragment_index.json",
                "visual_structure_segments.json",
                "visual_semantic_segments.json",
            },
        )
        generic_manifest = json.loads(
            (context_root / "InputManifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(generic_manifest["files"]), 3)
        generic_paths = {
            str(item["path"]) for item in generic_manifest["files"]
        }
        self.assertEqual(
            generic_paths,
            {
                INPUT_DIALOGUE_REL,
                INPUT_STRUCTURE_REL,
                INPUT_SEMANTIC_REL,
            },
        )
        variables = json.loads(
            (context_root / "Variables.json").read_text(encoding="utf-8")
        )
        self.assertEqual(variables["source_video_path"], "")
        frozen = json.loads(
            (composite_inputs / "InputManifest.json").read_text(
                encoding="utf-8"
            )
        )
        for scheme, upstream in self.upstream.items():
            self.assertEqual(
                frozen[f"{scheme}_run_id"],
                upstream["analysis_run_id"],
            )
            self.assertEqual(
                frozen[f"{scheme}_result_hash"],
                upstream["result_hash"],
            )
        encoded = json.dumps(generic_manifest).lower()
        for forbidden in (
            ".mp4",
            ".wav",
            ".mp3",
            ".jpg",
            ".jpeg",
            ".png",
            "video_source",
            "audio_reference",
            "keyframe",
        ):
            self.assertNotIn(forbidden, encoded)

    def _wait_terminal(
        self,
        analysis_run_id: str,
        *,
        timeout: float = 10,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.run_repo.get(analysis_run_id)
            if run is not None and str(run["status"]) in {
                "blocked",
                "ready",
                "stale",
                "failed",
            }:
                return run
            time.sleep(0.02)
        self.fail(f"composite run did not finish: {analysis_run_id}")

    def _active_composite_rows(self) -> list[dict[str, object]]:
        with self.engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(media_library_fragment_index).where(
                        media_library_fragment_index.c.asset_id == ASSET_ID,
                        media_library_fragment_index.c.analysis_scheme
                        == "composite",
                        media_library_fragment_index.c.is_active.is_(True),
                    )
                )
                .mappings()
                .all()
            ]

    def test_requires_all_three_current_ready_upstreams(self) -> None:
        for scheme, run in self.upstream.items():
            with self.subTest(scheme=scheme):
                with self.engine.begin() as conn:
                    conn.execute(
                        update(media_library_analysis_runs)
                        .where(
                            media_library_analysis_runs.c.analysis_run_id
                            == run["analysis_run_id"]
                        )
                        .values(status="failed")
                    )
                with self.assertRaises(HTTPException) as raised:
                    self.service.start(ASSET_ID)
                self.assertEqual(
                    raised.exception.detail["code"],
                    "analysis_upstream_missing",
                )
                self.assertIn(
                    scheme,
                    raised.exception.detail["metadata"][
                        "missing_schemes"
                    ],
                )
                with self.engine.begin() as conn:
                    conn.execute(
                        update(media_library_analysis_runs)
                        .where(
                            media_library_analysis_runs.c.analysis_run_id
                            == run["analysis_run_id"]
                        )
                        .values(status="ready")
                    )
        with self.engine.connect() as conn:
            count = int(
                conn.execute(
                    select(func.count())
                    .select_from(media_library_analysis_runs)
                    .where(
                        media_library_analysis_runs.c.scheme
                        == "composite"
                    )
                ).scalar_one()
            )
        self.assertEqual(count, 0)

    def test_model_unavailable_blocks_business_and_tool_session(self) -> None:
        response = self._start(None, blocked=True)
        run = self.run_repo.get(str(response["analysis_run_id"]))
        assert run is not None
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(
            run["error_code"],
            "composite_model_configuration_unavailable",
        )
        self.assertFalse(run["is_current"])
        self.assertIsNone(run["model_session_id"])
        run_root = self._run_root(run)
        self._assert_snapshot_is_three_json_only(run_root)
        summary = json.loads(
            (
                run_root
                / "SessionReport"
                / "SessionRunSummary.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(summary["status"], "blocked")
        step = next(
            item for item in summary["steps"] if item["tool_id"] == "04_01"
        )
        self.assertEqual(step["status"], "blocked")

    def test_malformed_response_repairs_once_and_publishes_atomically(
        self,
    ) -> None:
        client = _FakeTextClient(
            ["not valid json", _valid_model_candidate()]
        )
        response = self._start(client)
        run_id = str(response["analysis_run_id"])
        run = self.run_repo.get(run_id)
        assert run is not None
        self.assertEqual(run["status"], "ready")
        self.assertTrue(run["is_current"])
        self.assertEqual(
            run["model_session_id"],
            "composite-model-session-1",
        )
        self.assertTrue(
            all(
                upstream["model_session_id"] is None
                for upstream in self.upstream.values()
            )
        )
        expected_upstream = {
            f"{scheme}_run_id": str(value["analysis_run_id"])
            for scheme, value in self.upstream.items()
        }
        expected_upstream.update(
            {
                f"{scheme}_result_hash": str(value["result_hash"])
                for scheme, value in self.upstream.items()
            }
        )
        self.assertEqual(run["upstream_refs_json"], expected_upstream)
        run_root = self._run_root(run)
        self._assert_snapshot_is_three_json_only(run_root)
        payload = json.loads(
            (run_root / RESULT_PATH).read_text(encoding="utf-8")
        )
        quality = json.loads(
            (run_root / QUALITY_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(run["result_hash"], result_hash(payload))
        self.assertEqual(quality["structured_repair_count"], 1)
        self.assertEqual(quality["model_call_count"], 2)
        self.assertEqual(quality["cache_hit_count"], 0)
        self.assertEqual(len(client.prompt_calls), 2)
        self.assertEqual(len(self.usage.calls), 2)
        self.assertTrue(
            all(
                call["modality"] == "text_to_text"
                and call["attempt_id"] == run_id
                and call["step_id"] == "04_01"
                for call in self.usage.calls
            )
        )
        prompt_manifest = json.loads(
            next(
                run_root.glob(
                    "S1_*/Prompt/PromptManifest.json"
                )
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(prompt_manifest["model_calls"]), 2)
        self.assertFalse(prompt_manifest["model_calls"][0]["repair"])
        self.assertTrue(prompt_manifest["model_calls"][1]["repair"])
        references = prompt_manifest["references"][0]
        self.assertEqual(set(references), set(self.upstream))
        registered = {
            str(item["path"])
            for item in self.session_repo.list_files(self.session_id)
            if item.get("tool_use_session_id")
            == run["tool_use_session_id"]
        }
        prefix = f"tool_use_sessions/{run['tool_use_session_id']}/"
        for output in (
            RESULT_PATH,
            INDEX_PATH,
            VIRTUAL_CLIPS_PATH,
            SEARCH_MANIFEST_PATH,
        ):
            self.assertIn(prefix + output, registered)
        active = self._active_composite_rows()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["analysis_run_id"], run_id)
        self.assertEqual(active[0]["result_hash"], run["result_hash"])

    def test_second_identical_run_hits_cache_and_atomically_switches_index(
        self,
    ) -> None:
        client = _FakeTextClient([_valid_model_candidate()])
        first_response = self._start(client)
        first_id = str(first_response["analysis_run_id"])
        first = self.run_repo.get(first_id)
        assert first is not None
        self.assertEqual(first["status"], "ready")
        self.assertEqual(len(client.prompt_calls), 1)

        second_response = self._start(client, force=True)
        second_id = str(second_response["analysis_run_id"])
        second = self.run_repo.get(second_id)
        assert second is not None
        self.assertEqual(second["status"], "ready")
        self.assertTrue(second["is_current"])
        self.assertEqual(
            second["model_session_id"],
            "composite-model-session-2",
        )
        quality = json.loads(
            (self._run_root(second) / QUALITY_PATH).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(quality["cache_hit_count"], 1)
        self.assertEqual(quality["model_call_count"], 0)
        self.assertEqual(len(client.prompt_calls), 1)
        self.assertEqual(len(self.usage.calls), 1)
        first_after = self.run_repo.get(first_id)
        assert first_after is not None
        self.assertFalse(first_after["is_current"])
        with self.engine.connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    select(media_library_fragment_index).where(
                        media_library_fragment_index.c.analysis_scheme
                        == "composite"
                    )
                )
                .mappings()
                .all()
            ]
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {
                str(row["analysis_run_id"])
                for row in rows
                if bool(row["is_active"])
            },
            {second_id},
        )

    def test_structured_repair_gets_detailed_reference_closure_rules(
        self,
    ) -> None:
        self.assertEqual(
            composite.PROMPT_VERSION_DEFAULT,
            "composite_prompt_v6",
        )
        client = _FakeTextClient(
            [_keyframe_claim_reference_candidate(), _valid_model_candidate()]
        )
        response = self._start(client)
        run = self.run_repo.get(str(response["analysis_run_id"]))
        assert run is not None
        self.assertEqual(run["status"], "ready")
        self.assertEqual(len(client.prompt_calls), 2)
        repair_prompt = json.loads(
            str(client.prompt_calls[1]["text"])
        )
        repair = repair_prompt["repair"]
        self.assertIn("reference_ranges", repair_prompt)
        self.assertIn("visual_evidence_catalog", repair_prompt)
        self.assertIn("full_candidate_audit", repair)
        self.assertTrue(repair["full_candidate_audit"]["item_issues"])
        self.assertEqual(
            repair["validation_error"],
            "composite_visual_claim_ref_unknown:people",
        )
        instruction = str(repair["instruction"])
        self.assertIn("start_ms=min(ref.start_ms)", instruction)
        self.assertIn("visual_claim_refs", instruction)
        self.assertIn("never keyframe_refs", instruction)
        self.assertIn("avoid overlap", instruction)
        self.assertIn("visual_evidence_catalog", instruction)
        self.assertIn("full_candidate_audit", instruction)
        self.assertIn(
            "visual_claim_refs.<field>",
            str(client.prompt_calls[1]["system"]),
        )
        self.assertIn(
            "Simplified Chinese",
            str(client.prompt_calls[0]["system"]),
        )
        self.assertIn(
            "Simplified Chinese",
            str(client.prompt_calls[1]["system"]),
        )

    def test_long_multi_window_video_cannot_publish_one_overmerged_item(
        self,
    ) -> None:
        semantic = {
            "items": [
                {"fragment_id": f"scene_{index:04d}"}
                for index in range(1, 6)
            ]
        }
        with self.assertRaises(
            composite.CompositeValidationError
        ) as raised:
            composite.CompositeAnalysisToolAdapter._validate_segmentation_granularity(
                candidate={"items": [{"fragment_id": "composite_0001"}]},
                semantic=semantic,
                source_duration_ms=74_000,
            )
        self.assertEqual(raised.exception.code, "composite_segments_overmerged")
        composite.CompositeAnalysisToolAdapter._validate_segmentation_granularity(
            candidate={
                "items": [
                    {"fragment_id": "composite_0001"},
                    {"fragment_id": "composite_0002"},
                ]
            },
            semantic=semantic,
            source_duration_ms=74_000,
        )

    def test_upstream_switch_while_model_runs_marks_new_stale_without_publish(
        self,
    ) -> None:
        seed_client = _FakeTextClient([_valid_model_candidate()])
        seed_response = self._start(seed_client)
        old_id = str(seed_response["analysis_run_id"])
        old = self.run_repo.get(old_id)
        assert old is not None
        self.assertEqual(old["status"], "ready")
        barrier = _BarrierTextClient([_valid_model_candidate()])
        with ExitStack() as stack:
            self._patch_model(
                stack,
                barrier,
                immediate=False,
            )
            response = self.service.start(
                ASSET_ID,
                force=True,
                prompt_version=f"{PROMPT_VERSION}-changed",
            )
            new_id = str(response["analysis_run_id"])
            self.assertTrue(barrier.entered.wait(timeout=5))
            replacement_payload = {
                "schema_version": (
                    "media_library_dialogue_fragments_v1"
                ),
                "asset_id": ASSET_ID,
                "source_version": SOURCE_VERSION,
                "analysis_run_id": "",
                "items": [
                    {
                        "fragment_id": "dialogue_new",
                        "start_ms": 0,
                        "end_ms": 2000,
                        "duration_ms": 2000,
                        "dialogue_text": "上游在模型运行中更新。",
                        "keyframe_refs": [],
                    }
                ],
            }
            replacement = self._activate_payload(
                scheme="dialogue",
                payload=replacement_payload,
                timestamp=1000,
                output_relative=(
                    "SessionOutput/json/dialogue_fragment_index.json"
                ),
            )
            self.assertNotEqual(
                replacement["analysis_run_id"],
                self.upstream["dialogue"]["analysis_run_id"],
            )
            barrier.release.set()
            new_run = self._wait_terminal(new_id)
        self.assertEqual(new_run["status"], "stale")
        self.assertEqual(
            new_run["error_code"],
            "analysis_upstream_changed",
        )
        current = self.run_repo.current(ASSET_ID, "composite")
        assert current is not None
        self.assertEqual(current["analysis_run_id"], old_id)
        self.assertEqual(current["status"], "stale")
        with self.engine.connect() as conn:
            new_fragment_count = int(
                conn.execute(
                    select(func.count())
                    .select_from(media_library_fragment_index)
                    .where(
                        media_library_fragment_index.c.analysis_run_id
                        == new_id
                    )
                ).scalar_one()
            )
        self.assertEqual(new_fragment_count, 0)
        self.assertEqual(self._active_composite_rows(), [])

    def _assert_invalid_candidate_never_publishes(
        self,
        candidate: dict[str, object],
        expected_error: str,
    ) -> None:
        client = _FakeTextClient(
            [copy.deepcopy(candidate), copy.deepcopy(candidate)]
        )
        response = self._start(client)
        run_id = str(response["analysis_run_id"])
        run = self.run_repo.get(run_id)
        assert run is not None
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "composite_execution_failed")
        self.assertIn(expected_error, run["error_json"]["user_message"])
        self.assertFalse(run["is_current"])
        self.assertEqual(len(client.prompt_calls), 2)
        self.assertEqual(len(self.usage.calls), 2)
        with self.engine.connect() as conn:
            fragment_count = int(
                conn.execute(
                    select(func.count())
                    .select_from(media_library_fragment_index)
                    .where(
                        media_library_fragment_index.c.analysis_run_id
                        == run_id
                    )
                ).scalar_one()
            )
        self.assertEqual(fragment_count, 0)
        self.assertEqual(self._active_composite_rows(), [])
        cache_files = list(
            (
                self.data_dir
                / "cache"
                / "media_library_composite"
            ).glob("*.json")
        )
        self.assertEqual(cache_files, [])

    def test_unknown_references_never_publish_or_enter_cache(self) -> None:
        self._assert_invalid_candidate_never_publishes(
            _unknown_reference_candidate(),
            "composite_visual_ref_unknown",
        )

    def test_visual_hallucination_never_publishes_or_enters_cache(
        self,
    ) -> None:
        self._assert_invalid_candidate_never_publishes(
            _hallucinated_visual_candidate(),
            "composite_visual_fact_unsupported",
        )

    def test_known_current_dto_redacts_provider_and_internal_model(self) -> None:
        public = _serialize_analysis_run(
            {
                "analysis_run_id": "mlar_composite_public",
                "scheme": "composite",
                "source_version": SOURCE_VERSION,
                "status": "ready",
                "schema_version": "media_library_composite_v1",
                "prompt_version": PROMPT_VERSION,
                "model_config_id": MODEL_CONFIG_VERSION,
                "provider": "must-not-leak-provider",
                "model_id": "must-not-leak-model",
                "model_session_id": "must-not-leak-session",
                "result_hash": "b" * 64,
            },
            {"sampling_strategy": "scene_uniform_4_v1"},
        )
        encoded = json.dumps(public, ensure_ascii=False)
        self.assertEqual(public["scheme"], "composite")
        self.assertEqual(public["model_alias"], "server-default")
        self.assertEqual(
            public["sampling_strategy"],
            "scene_uniform_4_v1",
        )
        for forbidden in (
            "must-not-leak-provider",
            "must-not-leak-model",
            "must-not-leak-session",
        ):
            self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    unittest.main()
