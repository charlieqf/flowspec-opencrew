from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import sys
import threading
import time
import unittest
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import func, select, update


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from backend.tests.contracts import (  # noqa: E402
    test_media_library_visual_semantic_service_contract as service_fixture,
)
from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_fragment_index,
    media_library_tasks,
    session_files,
)
from opcrew_backend.media_library_analysis import visual_semantic  # noqa: E402
from opcrew_backend.media_library_analysis.contracts import (  # noqa: E402
    result_hash,
)
from opcrew_backend.media_library_analysis.visual_semantic import (  # noqa: E402
    VisualSemanticBlocked,
    VisualSemanticToolAdapter,
)
from opcrew_backend.media_library_analysis.visual_semantic_contracts import (  # noqa: E402
    QUALITY_PATH,
)
from opcrew_backend.routes.media_library import (  # noqa: E402
    VisualAnalysisRunRequest,
    _serialize_analysis_run,
)


VALID_DESCRIPTION = service_fixture._valid_description
ASSET_ID = service_fixture.ASSET_ID
MODEL_CONFIG_VERSION = service_fixture.MODEL_CONFIG_VERSION
PROMPT_VERSION = service_fixture.PROMPT_VERSION

# A valid 1x1 PNG. Keeping this fixture in source makes image-boundary tests
# independent from Pillow, FFmpeg, and the host image stack.
ONE_PIXEL_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk" "+A8AAQUBAScY42YAAAAASUVORK5CYII=")


class _FlexibleVisualClient(service_fixture._FakeCloudVisualClient):
    """Fake VLM with configurable catalog and verbatim malformed responses."""

    def __init__(
        self,
        responses: list[dict[str, object] | str] | None = None,
        *,
        model_ids: tuple[str, ...] = ("fake-vlm",),
        input_modalities: tuple[str, ...] = ("text", "image"),
        max_images_per_request: int = 4,
    ) -> None:
        super().__init__([])
        self.responses = deque(responses or [])
        self.model_ids = model_ids
        self.input_modalities = input_modalities
        self.max_images_per_request = max_images_per_request
        self.base_url = "https://fake-cloud.example"

    def providers(self) -> dict[str, object]:
        return {
            "connected": ["fake-cloud"],
            "default": {"fake-cloud": self.model_ids[0] if self.model_ids else ""},
            "all": [
                {
                    "id": "fake-cloud",
                    "name": "Fake Cloud",
                    "models": {
                        model_id: {
                            "id": model_id,
                            "name": model_id,
                            "modalities": {"input": list(self.input_modalities)},
                            "max_images_per_request": (
                                self.max_images_per_request
                            ),
                        }
                        for model_id in self.model_ids
                    },
                }
            ],
        }

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
        assistant_text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        self._messages[session_id].append(
            {
                "info": {
                    "role": "assistant",
                    "time": {"completed": 9_999_999_999_999},
                },
                "parts": [{"type": "text", "text": assistant_text}],
            }
        )


class _BarrierVisualClient(_FlexibleVisualClient):
    def __init__(self, responses: list[dict[str, object] | str]) -> None:
        super().__init__(responses)
        self.entered = threading.Event()
        self.release = threading.Event()

    def prompt_async(self, *args: object, **kwargs: object) -> None:
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test_visual_model_barrier_timeout")
        super().prompt_async(*args, **kwargs)


class _AlwaysFailUsageRecorder:
    """Models an audit insert that becomes unavailable to the caller."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_with_result(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        raise RuntimeError("usage_recorder_unavailable")


class MediaLibraryVisualSemanticHardeningContractTest(unittest.TestCase):
    def test_model_catalog_normalizes_current_and_legacy_capability_shapes(
        self,
    ) -> None:
        class CatalogClient:
            @staticmethod
            def providers() -> dict[str, object]:
                return {
                    "connected": ["connected"],
                    "default": {"connected": "current-vision"},
                    "all": [
                        {
                            "id": "connected",
                            "name": "Connected",
                            "models": {
                                "current-vision": {
                                    "id": "current-vision",
                                    "max_images_per_request": 4,
                                    "capabilities": {
                                        "attachment": True,
                                        "input": {
                                            "text": True,
                                            "image": True,
                                            "video": False,
                                        },
                                    },
                                },
                                "legacy-vision": {
                                    "id": "legacy-vision",
                                    "modalities": {"input": ["text", "image"]},
                                },
                                "attachment-only": {
                                    "id": "attachment-only",
                                    "capabilities": {
                                        "attachment": True,
                                    },
                                },
                                "explicit-false": {
                                    "id": "explicit-false",
                                    "capabilities": {
                                        "input": {
                                            "text": True,
                                            "image": False,
                                        }
                                    },
                                },
                                "unknown": {
                                    "id": "unknown",
                                    "capabilities": {"input": {"image": "true"}},
                                },
                            },
                        },
                        {
                            "id": "disconnected",
                            "name": "Disconnected",
                            "models": {
                                "ignored": {
                                    "id": "ignored",
                                    "capabilities": {"input": {"image": True}},
                                }
                            },
                        },
                    ],
                }

        catalog = visual_semantic._model_catalog(CatalogClient())  # type: ignore[arg-type]
        modalities = {str(item["modelID"]): item["inputModalities"] for item in catalog["items"]}
        image_limits = {
            str(item["modelID"]): item["maxImagesPerRequest"]
            for item in catalog["items"]
        }
        self.assertEqual(
            modalities,
            {
                "current-vision": ["text", "image"],
                "legacy-vision": ["text", "image"],
                "attachment-only": [],
                "explicit-false": ["text"],
                "unknown": [],
            },
        )
        self.assertEqual(image_limits["current-vision"], 4)
        self.assertEqual(image_limits["legacy-vision"], 0)
        self.assertEqual(
            catalog["default_model"],
            {
                "providerID": "connected",
                "modelID": "current-vision",
            },
        )
        self.assertNotIn("ignored", modalities)

    def fixture(
        self,
    ) -> service_fixture.MediaLibraryVisualSemanticServiceContractTest:
        case = service_fixture.MediaLibraryVisualSemanticServiceContractTest(methodName="runTest")
        case.setUp()
        # The older service fixture intentionally used opaque bytes because
        # image decoding was outside its scope. Hardening tests need a real
        # image so failures remain attributable to the contract under test.
        visual_root = case.workspace / "tool_use_sessions" / service_fixture.STRUCTURE_TOOL_SESSION_ID / "SessionOutput" / "visual"
        for frame in case.structure_keyframe_paths:
            frame.write_bytes(ONE_PIXEL_PNG)
        case.keyframe_bytes = ONE_PIXEL_PNG
        case.keyframe_hash = hashlib.sha256(ONE_PIXEL_PNG).hexdigest()
        for keyframe in case.structure_payload["items"][0]["keyframes"]:
            keyframe["image_sha256"] = case.keyframe_hash
        case.structure_hash = result_hash(case.structure_payload)
        segments = visual_root / "visual_structure_segments.json"
        segments.write_text(
            json.dumps(case.structure_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        manifest = visual_root / "visual_structure_manifest.json"
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_payload["result_hash"] = case.structure_hash
        manifest.write_text(
            json.dumps(manifest_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        with case.engine.begin() as conn:
            conn.execute(
                update(media_library_analysis_runs)
                .where(media_library_analysis_runs.c.analysis_run_id == case.structure_run_id)
                .values(result_hash=case.structure_hash)
            )
        case.ctx.get_setting = lambda name: ("https://fake-cloud.example" if name == "opencode.base_url" else "")
        self.addCleanup(case.tearDown)
        return case

    @staticmethod
    def adapter(
        root: Path,
        *,
        model_config_id: str = MODEL_CONFIG_VERSION,
    ) -> VisualSemanticToolAdapter:
        return VisualSemanticToolAdapter(
            ctx=SimpleNamespace(data_dir=root),
            session={},
            asset={
                "asset_id": ASSET_ID,
                "content_sha256": "a" * 64,
            },
            analysis_run_id="mlar_visual_semantic_hardening",
            visual_structure_run_id="mlar_visual_structure_hardening",
            visual_structure_result_hash="b" * 64,
            visual_prompt_version=PROMPT_VERSION,
            model_config_id=model_config_id,
            allow_cloud_visual_data_transfer=True,
        )

    @staticmethod
    def call_model_once(
        adapter: VisualSemanticToolAdapter,
        client: _FlexibleVisualClient,
        image_path: Path,
        *,
        model_id: str = "fake-vlm",
    ) -> dict[str, Any]:
        session_id = str(client.create_session("hardening")["id"])
        return adapter._model_call(
            client=client,
            model_session_id=session_id,
            model={"providerID": "fake-cloud", "modelID": model_id},
            authoritative={
                "fragment_id": "scene_0001",
                "start_ms": 0,
                "end_ms": 3000,
                "duration_ms": 3000,
                "keyframes": [
                    {
                        "keyframe_id": f"scene_0001-sample-{index:02d}",
                        "image_sha256": "c" * 64,
                    }
                    for index in range(1, 5)
                ],
            },
            image_paths=[image_path] * 4,
        )

    @staticmethod
    def run_service(
        case: service_fixture.MediaLibraryVisualSemanticServiceContractTest,
        client: _FlexibleVisualClient,
        *,
        model_id: str = "fake-vlm",
        force: bool = False,
        immediate_thread: bool = True,
    ) -> dict[str, object]:
        patches: list[object] = [
            patch.dict(
                os.environ,
                {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "false"},
                clear=False,
            ),
            patch.object(
                visual_semantic,
                "surface_policy",
                return_value={
                    "mode": "alias",
                    "alias_only": True,
                    "read_only": True,
                    "version": MODEL_CONFIG_VERSION,
                    "required_input_modalities": ["image"],
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
                return_value=(
                    {
                        "providerID": "fake-cloud",
                        "modelID": model_id,
                    },
                    {},
                ),
            ),
            patch.object(
                visual_semantic,
                "_is_local_model_provider",
                return_value=False,
            ),
        ]
        if immediate_thread:
            patches.insert(
                0,
                patch.object(
                    visual_semantic.threading,
                    "Thread",
                    side_effect=service_fixture._thread_factory,
                ),
            )
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            return case.service.start(
                ASSET_ID,
                force=force,
                allow_cloud_visual_data_transfer=True,
                visual_prompt_version=PROMPT_VERSION,
            )

    @staticmethod
    def wait_for_terminal_run(
        case: service_fixture.MediaLibraryVisualSemanticServiceContractTest,
        run_id: str,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            run = case.run_repo.get(run_id)
            if run is not None and str(run.get("status") or "") not in {
                "queued",
                "running",
            }:
                return run
            time.sleep(0.02)
        raise AssertionError(f"analysis run did not finish: {run_id}")

    @staticmethod
    def activate_replacement_structure(
        case: service_fixture.MediaLibraryVisualSemanticServiceContractTest,
    ) -> dict[str, Any]:
        queued = case.run_repo.create_queued(
            asset_id=ASSET_ID,
            scheme="visual_structure",
            timestamp=20_000,
        )
        run_id = str(queued["analysis_run_id"])
        tool_session_id = "tus-visual-structure-replacement"
        root = case.workspace / "tool_use_sessions" / tool_session_id
        output = root / "SessionOutput" / "visual"
        output.mkdir(parents=True)
        payload = copy.deepcopy(case.structure_payload)
        payload["analysis_run_id"] = run_id
        digest = result_hash(payload)
        segments = output / "visual_structure_segments.json"
        manifest = output / "visual_structure_manifest.json"
        segments.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": ("media_library_visual_structure_manifest_v2"),
                    "asset_id": ASSET_ID,
                    "source_version": service_fixture.SOURCE_VERSION,
                    "analysis_run_id": run_id,
                    "result_hash": digest,
                    "result_path": ("SessionOutput/visual/" "visual_structure_segments.json"),
                    "sampling_strategy": "scene_uniform_4_v1",
                    "fragment_count": 1,
                    "keyframe_count": 4,
                }
            ),
            encoding="utf-8",
        )
        result_index_path = f"tool_use_sessions/{tool_session_id}/" "SessionOutput/visual/visual_structure_segments.json"
        case.run_repo.mark_running(
            run_id,
            timestamp=20_001,
            tool_use_session_id=tool_session_id,
        )
        with case.engine.begin() as conn:
            conn.execute(
                session_files.insert().values(
                    session_id=case.session_id,
                    path=result_index_path,
                    kind="artifact",
                    size=segments.stat().st_size,
                    origin="tool_session",
                    downloadable=0,
                    visibility="internal",
                    sensitivity="normal",
                    tool_use_session_id=tool_session_id,
                    stale=0,
                    updated_at=20_001,
                )
            )
        return case.run_repo.activate_ready(
            run_id,
            timestamp=20_002,
            schema_version="media_library_visual_structure_v2",
            result_hash=digest,
            result_index_path=result_index_path,
        )

    def test_model_call_explicitly_disables_every_tool(self) -> None:
        case = self.fixture()
        image = case.root / "frame.png"
        image.write_bytes(ONE_PIXEL_PNG)
        client = _FlexibleVisualClient([VALID_DESCRIPTION()])
        self.call_model_once(self.adapter(case.data_dir), client, image)
        self.assertEqual(len(client.prompt_calls), 1)
        tools = client.prompt_calls[0]["tools"]
        self.assertEqual(tools, visual_semantic.DISABLED_MODEL_TOOLS)
        self.assertTrue(tools)
        self.assertTrue(all(value is False for value in tools.values()))

    def test_semantic_run_becomes_unpublished_stale_if_structure_changes(
        self,
    ) -> None:
        case = self.fixture()
        client = _BarrierVisualClient([VALID_DESCRIPTION()])
        strict_policy = {
            "mode": "alias",
            "alias_only": True,
            "read_only": True,
            "version": MODEL_CONFIG_VERSION,
            "required_input_modalities": ["image"],
        }
        with (
            patch.dict(
                os.environ,
                {"OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "false"},
                clear=False,
            ),
            patch.object(
                visual_semantic,
                "surface_policy",
                return_value=strict_policy,
            ),
            patch.object(
                visual_semantic,
                "opencode_client_for_context",
                return_value=client,
            ),
            patch.object(
                visual_semantic,
                "resolve_prompt_model_for_role",
                return_value=(
                    {"providerID": "fake-cloud", "modelID": "fake-vlm"},
                    {},
                ),
            ),
            patch.object(
                visual_semantic,
                "_is_local_model_provider",
                return_value=False,
            ),
        ):
            response = case.service.start(
                ASSET_ID,
                force=False,
                allow_cloud_visual_data_transfer=True,
                visual_prompt_version=PROMPT_VERSION,
            )
            semantic_run_id = str(response["semantic_run_id"])
            self.assertTrue(
                client.entered.wait(timeout=5),
                "semantic model call did not reach the test barrier",
            )
            replacement = self.activate_replacement_structure(case)
            client.release.set()
            run = self.wait_for_terminal_run(case, semantic_run_id)
        self.assertEqual(run["status"], "stale")
        self.assertEqual(run["error_code"], "analysis_upstream_changed")
        self.assertFalse(run["is_current"])
        self.assertIsNone(run["result_hash"])
        self.assertIsNone(run["result_index_path"])
        self.assertEqual(
            case.run_repo.current(ASSET_ID, "visual_structure")["analysis_run_id"],
            replacement["analysis_run_id"],
        )
        with case.engine.connect() as conn:
            active_fragments = int(
                conn.execute(
                    select(func.count())
                    .select_from(media_library_fragment_index)
                    .where(
                        media_library_fragment_index.c.analysis_run_id == semantic_run_id,
                        media_library_fragment_index.c.is_active.is_(True),
                    )
                ).scalar_one()
            )
        self.assertEqual(active_fragments, 0)

    def test_visual_surface_must_be_alias_only_and_require_image(self) -> None:
        case = self.fixture()
        client = _FlexibleVisualClient([VALID_DESCRIPTION()])
        policies = {
            "missing_surface": {"surfaces": {}},
            "missing_image_requirement": {
                "surfaces": {
                    "media_library.visual_semantic": {
                        "mode": "alias",
                        "alias_only": True,
                        "version": "unsafe-policy-v1",
                        "required_input_modalities": [],
                        "options": [
                            {
                                "provider_alias": "Approved",
                                "provider_label": "Approved",
                                "model_alias": "Vision",
                                "model_label": "Vision",
                                "provider": "fake-cloud",
                                "model": "fake-vlm",
                            }
                        ],
                    }
                }
            },
        }
        for name, policy_payload in policies.items():
            with self.subTest(name=name):
                policy_path = case.root / f"{name}.json"
                policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")
                adapter = self.adapter(case.data_dir)
                with (
                    patch.dict(
                        os.environ,
                        {"OPENCREW_USER_MODEL_POLICY_PATH": str(policy_path)},
                        clear=False,
                    ),
                    patch.object(
                        visual_semantic,
                        "opencode_client_for_context",
                        return_value=client,
                    ),
                ):
                    with self.assertRaises(VisualSemanticBlocked):
                        adapter._resolve_model()
        self.assertEqual(client.created_sessions, [])
        self.assertEqual(client.prompt_calls, [])

    def test_model_must_prove_single_request_four_image_capability(self) -> None:
        case = self.fixture()
        client = _FlexibleVisualClient(
            [VALID_DESCRIPTION()], max_images_per_request=1
        )
        adapter = self.adapter(case.data_dir)
        with (
            patch.object(
                visual_semantic,
                "surface_policy",
                return_value={
                    "mode": "alias",
                    "alias_only": True,
                    "required_input_modalities": ["image"],
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
                return_value=(
                    {
                        "providerID": "fake-cloud",
                        "modelID": "fake-vlm",
                    },
                    {},
                ),
            ),
            self.assertRaises(VisualSemanticBlocked) as raised,
        ):
            adapter._resolve_model()
        self.assertEqual(
            raised.exception.code,
            "visual_model_multi_image_unsupported",
        )
        self.assertEqual(client.created_sessions, [])
        self.assertEqual(client.prompt_calls, [])

    def test_cache_identity_preserves_ordered_four_frame_hashes(self) -> None:
        case = self.fixture()
        adapter = self.adapter(case.data_dir)
        model = {"providerID": "fake-cloud", "modelID": "fake-vlm"}
        ordered = [str(index) * 64 for index in range(1, 5)]
        original = adapter._cache_key(ordered, model)
        self.assertEqual(original, adapter._cache_key(list(ordered), model))
        self.assertNotEqual(
            original,
            adapter._cache_key(list(reversed(ordered)), model),
        )
        changed = list(ordered)
        changed[2] = "f" * 64
        self.assertNotEqual(original, adapter._cache_key(changed, model))

    def test_semantic_success_projects_partial_when_dialogue_has_no_audio(
        self,
    ) -> None:
        case = self.fixture()
        with case.engine.begin() as conn:
            conn.execute(
                update(media_library_tasks)
                .where(media_library_tasks.c.asset_id == ASSET_ID)
                .values(
                    dialogue_status="blocked",
                    dialogue_error="Cloud ASR authorization required",
                )
            )
            conn.execute(update(media_library_assets).where(media_library_assets.c.asset_id == ASSET_ID).values(analysis_status="blocked"))
        response = self.run_service(case, _FlexibleVisualClient([VALID_DESCRIPTION()]))
        run = case.run_repo.get(str(response["semantic_run_id"]))
        self.assertEqual(run["status"], "ready")
        asset = case.asset_repo.get(ASSET_ID)
        self.assertEqual(asset["analysis_status"], "partial")
        self.assertEqual(asset["analysis_summary_json"]["visual_fragment_count"], 1)

    def test_public_run_serializer_redacts_internal_failure_details(
        self,
    ) -> None:
        public = _serialize_analysis_run(
            {
                "analysis_run_id": "mlar_public_error",
                "scheme": "visual_semantic",
                "source_version": "a" * 64,
                "status": "failed",
                "error_json": {
                    "code": "visual_semantic_execution_failed",
                    "user_message": ("provider=real-provider model=secret-vlm " "failed reading /srv/private/keyframes/person.jpg"),
                    "suggested_action": "use api_key_ref=prod-secret",
                    "result_sync_errors": ["postgresql://admin:password@db/internal"],
                },
            }
        )
        encoded = json.dumps(public, ensure_ascii=False)
        self.assertEqual(public["error"]["code"], "visual_semantic_execution_failed")
        self.assertNotIn("result_sync_errors", public["error"])
        for forbidden in (
            "real-provider",
            "secret-vlm",
            "/srv/private",
            "prod-secret",
            "postgresql://",
            "password@db",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_cache_identity_changes_when_resolved_model_target_changes(
        self,
    ) -> None:
        case = self.fixture()
        first_client = _FlexibleVisualClient(
            [VALID_DESCRIPTION()],
            model_ids=("fake-vlm-a", "fake-vlm-b"),
        )
        first_response = self.run_service(
            case,
            first_client,
            model_id="fake-vlm-a",
        )
        first = case.run_repo.get(str(first_response["semantic_run_id"]))
        self.assertEqual(first["status"], "ready")
        self.assertEqual(len(first_client.prompt_calls), 1)

        second_client = _FlexibleVisualClient(
            [VALID_DESCRIPTION()],
            model_ids=("fake-vlm-a", "fake-vlm-b"),
        )
        second_response = self.run_service(
            case,
            second_client,
            model_id="fake-vlm-b",
            force=True,
        )
        second = case.run_repo.get(str(second_response["semantic_run_id"]))
        self.assertEqual(second["status"], "ready")
        self.assertEqual(len(second_client.prompt_calls), 1)
        quality = json.loads((case._run_root(second) / QUALITY_PATH).read_text(encoding="utf-8"))
        self.assertEqual(quality["cache_hit_count"], 0)
        self.assertEqual(quality["model_call_count"], 1)

    def test_malformed_json_gets_exactly_one_structured_repair(self) -> None:
        self.assertEqual(
            visual_semantic.PROMPT_VERSION_DEFAULT,
            "visual_semantic_prompt_v3",
        )
        case = self.fixture()
        client = _FlexibleVisualClient(["this is not JSON", VALID_DESCRIPTION()])
        response = self.run_service(case, client)
        run = case.run_repo.get(str(response["semantic_run_id"]))
        self.assertEqual(run["status"], "ready")
        self.assertEqual(len(client.prompt_calls), 2)
        repair_payload = json.loads(str(client.prompt_calls[1]["text"]))
        self.assertIn("repair", repair_payload)
        self.assertIn(
            "exactly the JSON shape",
            repair_payload["repair"]["instruction"],
        )
        self.assertIn(
            "arrays of strings",
            client.prompt_calls[0]["system"],
        )
        self.assertIn(
            "Simplified Chinese",
            client.prompt_calls[0]["system"],
        )
        self.assertIn(
            "Simplified Chinese",
            repair_payload["repair"]["instruction"],
        )
        quality = json.loads((case._run_root(run) / QUALITY_PATH).read_text(encoding="utf-8"))
        self.assertEqual(quality["structured_repair_count"], 1)
        self.assertEqual(quality["model_call_count"], 2)

    def test_people_object_array_repair_explains_string_contract(
        self,
    ) -> None:
        case = self.fixture()
        invalid = VALID_DESCRIPTION()
        invalid["people"] = [
            {
                "description": "one anonymous person",
                "evidence_keyframe_id": "scene_0001-sample-01",
            }
        ]
        client = _FlexibleVisualClient([invalid, VALID_DESCRIPTION()])

        response = self.run_service(case, client)

        run = case.run_repo.get(str(response["semantic_run_id"]))
        self.assertEqual(run["status"], "ready")
        self.assertEqual(len(client.prompt_calls), 2)
        repair_payload = json.loads(client.prompt_calls[1]["text"])
        instruction = repair_payload["repair"]["instruction"]
        self.assertIn("people must be an array", instruction)
        self.assertIn("claim_evidence.people", instruction)

    def test_usage_failure_does_not_double_count_one_real_model_call(
        self,
    ) -> None:
        case = self.fixture()
        recorder = _AlwaysFailUsageRecorder()
        case.ctx.local_usage = recorder
        client = _FlexibleVisualClient([VALID_DESCRIPTION()])
        response = self.run_service(case, client)
        run = case.run_repo.get(str(response["semantic_run_id"]))
        self.assertEqual(run["status"], "failed")
        self.assertEqual(len(client.prompt_calls), 1)
        self.assertEqual(len(recorder.calls), 1)
        self.assertTrue(str(recorder.calls[0]["idempotency_key"]).endswith(":1"))

    def test_visual_run_request_forbids_real_model_and_keyframe_fields(
        self,
    ) -> None:
        forbidden_payloads = (
            {"provider": "real-provider"},
            {"providerID": "real-provider"},
            {"model": "real-model"},
            {"modelID": "real-model"},
            {"keyframe_ref": "/private/frame.jpg"},
        )
        for payload in forbidden_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    VisualAnalysisRunRequest.model_validate(payload)

    def test_model_boundary_rejects_oversized_and_non_image_payloads(
        self,
    ) -> None:
        case = self.fixture()
        adapter = self.adapter(case.data_dir)
        invalid = case.root / "not-an-image.jpg"
        invalid.write_bytes(b"not an image")
        invalid_client = _FlexibleVisualClient([VALID_DESCRIPTION()])
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_KEYFRAME_BYTES": "1048576"},
            clear=False,
        ):
            with self.assertRaises((VisualSemanticBlocked, ValueError)):
                self.call_model_once(adapter, invalid_client, invalid)
        self.assertEqual(invalid_client.prompt_calls, [])

        oversized = case.root / "oversized.png"
        oversized.write_bytes(ONE_PIXEL_PNG)
        oversized_client = _FlexibleVisualClient([VALID_DESCRIPTION()])
        with patch.dict(
            os.environ,
            {"OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_KEYFRAME_BYTES": "32"},
            clear=False,
        ):
            with self.assertRaises((VisualSemanticBlocked, ValueError)):
                self.call_model_once(adapter, oversized_client, oversized)
        self.assertEqual(oversized_client.prompt_calls, [])

    def test_four_image_encoded_payload_limit_is_structurally_blocked(self) -> None:
        case = self.fixture()
        image = case.root / "bounded.png"
        image.write_bytes(ONE_PIXEL_PNG)
        client = _FlexibleVisualClient([VALID_DESCRIPTION()])
        with patch.dict(
            os.environ,
            {
                "OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_KEYFRAME_BYTES": "1048576",
                "OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_FRAGMENT_IMAGE_BYTES": str(
                    len(ONE_PIXEL_PNG) * 4
                ),
            },
            clear=False,
        ):
            with self.assertRaises(VisualSemanticBlocked) as raised:
                self.call_model_once(
                    self.adapter(case.data_dir), client, image
                )
        self.assertEqual(
            raised.exception.code,
            "visual_semantic_keyframe_payload_too_large",
        )
        self.assertEqual(client.prompt_calls, [])


if __name__ == "__main__":
    unittest.main()
