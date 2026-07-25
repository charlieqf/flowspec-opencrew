from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.media_library_analysis.contracts import (  # noqa: E402
    result_hash,
)
from opcrew_backend.media_library_analysis.visual_semantic_contracts import (  # noqa: E402
    CANDIDATE_SCHEMA_VERSION,
    INPUT_KEYFRAMES_REL,
    INPUT_MANIFEST_REL,
    INPUT_SCHEMA_VERSION,
    INPUT_STRUCTURE_MANIFEST_REL,
    INPUT_STRUCTURE_SEGMENTS_REL,
    MANIFEST_PATH,
    QUALITY_PATH,
    RESULT_PATH,
    SAMPLING_STRATEGY,
    VisualSemanticValidationError,
    publish_visual_semantic_contract,
    validate_visual_semantic_candidate,
    validate_visual_semantic_item,
    validate_with_single_repair,
)
from opcrew_backend.tool_sessions.registry_normalizer import (  # noqa: E402
    normalize_registry_file,
)


ASSET_ID = "asset-visual-semantic"
SOURCE_VERSION = "a" * 64
STRUCTURE_RUN_ID = "mlar_visual_structure_current"
SEMANTIC_RUN_ID = "mlar_visual_semantic_current"
PROMPT_VERSION = "visual-semantic-prompt-v1"
MODEL_CONFIG_ID = "openrouter-vlm-alias"


def _load_numeric_tool_module() -> object:
    path = REPO_ROOT / "ToolLibrary" / "OpenCut_V1" / "03_03_KeyframeVisualSemantic.py"
    spec = importlib.util.spec_from_file_location(
        "opencut_v1_03_03_visual_semantic",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could_not_load_03_03")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _structure_item(
    *,
    fragment_id: str = "scene_0001",
    start_ms: int = 0,
    end_ms: int = 3000,
    image_sha256: str = "b" * 64,
) -> dict[str, object]:
    duration_ms = end_ms - start_ms
    return {
        "fragment_id": fragment_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_ms": duration_ms,
        "keyframes": [
            {
                "keyframe_id": f"{fragment_id}-sample-{index:02d}",
                "keyframe_time_ms": start_ms
                + round(duration_ms * ratio),
                "image_path": (
                    "SessionOutput/visual/scene_frames/"
                    f"{fragment_id}-sample-{index:02d}.jpg"
                ),
                "image_sha256": image_sha256,
            }
            for index, ratio in enumerate(
                (0.125, 0.375, 0.625, 0.875),
                start=1,
            )
        ],
        "sampling_strategy": SAMPLING_STRATEGY,
    }


def _candidate_item(
    *,
    fragment_id: str = "scene_0001",
    start_ms: int = 0,
    end_ms: int = 3000,
) -> dict[str, object]:
    keyframe_ids = [
        f"{fragment_id}-sample-{index:02d}"
        for index in range(1, 5)
    ]
    return {
        "fragment_id": fragment_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "keyframe_refs": keyframe_ids,
        "visual_summary": "一名讲解者站在白板旁，画面中可见流程图。",
        "people": ["一名讲解者"],
        "objects": ["白板", "流程图"],
        "scene": "室内演示空间",
        "action": None,
        "keywords": ["讲解者", "白板", "流程图"],
        "claim_evidence": {
            "people": [keyframe_ids[0]],
            "objects": [keyframe_ids[1], keyframe_ids[2]],
            "scene": [keyframe_ids[3]],
            "action": [],
        },
        "confidence": 0.82,
        "needs_review": False,
    }


def _structure_payload(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "media_library_visual_structure_v2",
        "asset_id": ASSET_ID,
        "source_version": SOURCE_VERSION,
        "analysis_run_id": STRUCTURE_RUN_ID,
        "sampling_strategy": SAMPLING_STRATEGY,
        "items": items,
    }


class OpenCutV1VisualSemanticContractTest(unittest.TestCase):
    def _write_snapshot(
        self,
        root: Path,
        *,
        keyframe_bytes: bytes = b"frozen-keyframe",
    ) -> tuple[dict[str, object], str]:
        import hashlib

        keyframe_hash = hashlib.sha256(keyframe_bytes).hexdigest()
        structure = _structure_payload(
            [_structure_item(image_sha256=keyframe_hash)]
        )
        structure_hash = result_hash(structure)
        keyframe_dir = root / INPUT_KEYFRAMES_REL
        keyframe_dir.mkdir(parents=True)
        for index in range(1, 5):
            (keyframe_dir / f"scene_0001-sample-{index:02d}.jpg").write_bytes(
                keyframe_bytes
            )
        (root / INPUT_STRUCTURE_SEGMENTS_REL).write_text(
            json.dumps(structure, ensure_ascii=False),
            encoding="utf-8",
        )
        (root / INPUT_STRUCTURE_MANIFEST_REL).write_text(
            json.dumps(
                {
                    "schema_version": (
                        "media_library_visual_structure_manifest_v2"
                    ),
                    "asset_id": ASSET_ID,
                    "source_version": SOURCE_VERSION,
                    "analysis_run_id": STRUCTURE_RUN_ID,
                    "result_hash": structure_hash,
                    "result_path": (
                        "SessionOutput/visual/visual_structure_segments.json"
                    ),
                    "sampling_strategy": SAMPLING_STRATEGY,
                    "fragment_count": 1,
                }
            ),
            encoding="utf-8",
        )
        (root / INPUT_MANIFEST_REL).write_text(
            json.dumps(
                {
                    "schema_version": INPUT_SCHEMA_VERSION,
                    "asset_id": ASSET_ID,
                    "source_version": SOURCE_VERSION,
                    "visual_structure_run_id": STRUCTURE_RUN_ID,
                    "visual_structure_result_hash": structure_hash,
                    "sampling_strategy": SAMPLING_STRATEGY,
                    "keyframes": [
                        {
                            "keyframe_id": f"scene_0001-sample-{index:02d}",
                            "image_sha256": keyframe_hash,
                        }
                        for index in range(1, 5)
                    ],
                    "visual_prompt_version": PROMPT_VERSION,
                    "model_config_id": MODEL_CONFIG_ID,
                    "allow_cloud_visual_data_transfer": True,
                }
            ),
            encoding="utf-8",
        )
        return structure, structure_hash

    def _candidate(
        self,
        item: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "items": [item or _candidate_item()],
        }

    def _publish(
        self,
        root: Path,
        structure_hash: str,
        *,
        candidate: dict[str, object] | None = None,
        write: bool = True,
    ) -> tuple[dict[str, object], str, str]:
        return publish_visual_semantic_contract(
            tool_root=root,
            asset_id=ASSET_ID,
            source_version=SOURCE_VERSION,
            analysis_run_id=SEMANTIC_RUN_ID,
            current_visual_structure_run_id=STRUCTURE_RUN_ID,
            current_visual_structure_result_hash=structure_hash,
            candidate=candidate or self._candidate(),
            visual_prompt_version=PROMPT_VERSION,
            model_config_id=MODEL_CONFIG_ID,
            write=write,
        )

    def test_registry_declares_contract_only_03_03_without_source_video(self) -> None:
        registry = json.loads(
            (
                REPO_ROOT
                / "ToolLibrary"
                / "OpenCut_V1"
                / "tool_registry.json"
            ).read_text(encoding="utf-8")
        )
        tool = next(item for item in registry["tools"] if item["id"] == "03_03")
        # 03_03 runs in its own Tool Session. Its publisher validates the
        # frozen current 03_02 result instead of requiring 03_02 to have run
        # inside this session.
        self.assertEqual(tool["hard_dependencies"], [])
        self.assertEqual(tool["soft_dependencies"], [])
        self.assertNotIn("source_video", tool["hard_dependencies"])
        self.assertTrue(tool["contract_only"])
        self.assertEqual(tool["execution_owner"], "opencrew_backend_adapter")
        self.assertEqual(
            tool["main_outputs"],
            [RESULT_PATH, MANIFEST_PATH, QUALITY_PATH],
        )
        for path in tool["input_schemas"] + [tool["output_schema"]]:
            schema = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
            self.assertEqual(
                schema["$schema"],
                "https://json-schema.org/draft/2020-12/schema",
            )
        module = _load_numeric_tool_module()
        self.assertEqual(module.TOOL_NAME, "03_03_KeyframeVisualSemantic")
        self.assertIs(
            module.validate_visual_semantic_item,
            validate_visual_semantic_item,
        )
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / tool["script"])],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("must run through", completed.stderr)
        normalized_registry = normalize_registry_file(
            REPO_ROOT
            / "ToolLibrary"
            / "OpenCut_V1"
            / "tool_registry.json",
            strict=True,
        )
        normalized = next(
            item
            for item in normalized_registry["tools"]
            if item["id"] == "03_03"
        )
        self.assertEqual(normalized["unresolved_dependencies"], [])
        self.assertEqual(normalized["consumes_outputs"], [])

    def test_validator_restores_authoritative_order_and_fields(self) -> None:
        first = _structure_item()
        second = _structure_item(
            fragment_id="scene_0002",
            start_ms=3000,
            end_ms=5500,
        )
        structure = _structure_payload([first, second])
        candidate = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "items": [
                _candidate_item(
                    fragment_id="scene_0002",
                    start_ms=3000,
                    end_ms=5500,
                ),
                _candidate_item(),
            ],
        }
        normalized = validate_visual_semantic_candidate(
            structure_segments=structure,
            candidate=candidate,
        )
        self.assertEqual(
            [item["fragment_id"] for item in normalized["items"]],
            ["scene_0001", "scene_0002"],
        )
        self.assertEqual(normalized["items"][0]["duration_ms"], 3000)
        self.assertIsNone(normalized["items"][0]["action"])

    def test_sparse_four_frame_action_is_always_null(self) -> None:
        candidate = _candidate_item()
        candidate["action"] = "正在持续讲解"
        candidate["claim_evidence"]["action"] = [
            "scene_0001-sample-01"
        ]
        with self.assertRaisesRegex(
            VisualSemanticValidationError,
            "visual_semantic_action_must_be_null",
        ):
            validate_visual_semantic_item(
                authoritative_item=_structure_item(),
                candidate_item=candidate,
            )

    def test_non_empty_claim_requires_known_keyframe_evidence(self) -> None:
        missing = _candidate_item()
        missing["claim_evidence"]["objects"] = []
        with self.assertRaisesRegex(
            VisualSemanticValidationError,
            "visual_semantic_claim_evidence_required",
        ):
            validate_visual_semantic_item(
                authoritative_item=_structure_item(),
                candidate_item=missing,
            )

        unknown = _candidate_item()
        unknown["claim_evidence"]["scene"] = ["invented-keyframe"]
        with self.assertRaisesRegex(
            VisualSemanticValidationError,
            "visual_semantic_unknown_keyframe_evidence",
        ):
            validate_visual_semantic_item(
                authoritative_item=_structure_item(),
                candidate_item=unknown,
            )

    def test_model_cannot_modify_id_time_or_keyframe_references(self) -> None:
        mutations = (
            ("fragment_id", "scene_9999", "fragment_id_modified"),
            ("start_ms", 1, "time_modified"),
            ("end_ms", 2999, "time_modified"),
            (
                "keyframe_refs",
                [f"scene_9999-sample-{index:02d}" for index in range(1, 5)],
                "keyframe_refs_modified",
            ),
        )
        for field, value, code in mutations:
            with self.subTest(field=field):
                candidate = _candidate_item()
                candidate[field] = value
                with self.assertRaisesRegex(
                    VisualSemanticValidationError,
                    code,
                ):
                    validate_visual_semantic_item(
                        authoritative_item=_structure_item(),
                        candidate_item=candidate,
                    )

    def test_identity_and_sensitive_attribute_inference_is_rejected(self) -> None:
        forbidden_values = (
            ("visual_summary", "画面中的人真实姓名是张三。"),
            ("people", ["一名亚裔男性"]),
            ("people", ["Taylor Swift"]),
            ("people", ["张三"]),
            ("scene", "一名人物正在表达政治立场"),
            ("keywords", ["宗教信仰"]),
        )
        for field, value in forbidden_values:
            with self.subTest(field=field):
                candidate = _candidate_item()
                candidate[field] = value
                with self.assertRaisesRegex(
                    VisualSemanticValidationError,
                    "identity_or_sensitive_inference_forbidden",
                ):
                    validate_visual_semantic_item(
                        authoritative_item=_structure_item(),
                        candidate_item=candidate,
                    )

    def test_missing_or_modified_candidate_contract_fields_are_rejected(
        self,
    ) -> None:
        missing = _candidate_item()
        del missing["action"]
        with self.assertRaisesRegex(
            VisualSemanticValidationError,
            "visual_semantic_item_missing_field",
        ):
            validate_visual_semantic_item(
                authoritative_item=_structure_item(),
                candidate_item=missing,
            )

        candidate = self._candidate()
        candidate["visual_structure_run_id"] = "mlar_stale"
        with self.assertRaisesRegex(
            VisualSemanticValidationError,
            "visual_structure_run_id_modified",
        ):
            validate_visual_semantic_candidate(
                structure_segments=_structure_payload([_structure_item()]),
                candidate=candidate,
            )

    def test_validator_allows_exactly_one_structured_repair(self) -> None:
        structure = _structure_payload([_structure_item()])
        invalid = self._candidate()
        invalid["items"][0]["people"] = ["一名讲解者"]
        invalid["items"][0]["claim_evidence"]["people"] = []
        calls: list[str] = []

        def validator(value: object) -> dict[str, object]:
            return validate_visual_semantic_candidate(
                structure_segments=structure,
                candidate=value,
            )

        def repair(
            value: dict[str, object],
            error: VisualSemanticValidationError,
        ) -> dict[str, object]:
            calls.append(error.code)
            value["items"][0]["claim_evidence"]["people"] = [
                "scene_0001-sample-01"
            ]
            return value

        normalized, repair_used = validate_with_single_repair(
            invalid,
            validator,
            repair,
        )
        self.assertTrue(repair_used)
        self.assertEqual(calls, ["visual_semantic_claim_evidence_required"])
        self.assertEqual(
            normalized["items"][0]["claim_evidence"]["people"],
            ["scene_0001-sample-01"],
        )

        repair_calls = 0

        def ineffective(
            value: dict[str, object],
            _error: VisualSemanticValidationError,
        ) -> dict[str, object]:
            nonlocal repair_calls
            repair_calls += 1
            return value

        with self.assertRaisesRegex(
            VisualSemanticValidationError,
            "visual_semantic_structured_repair_exhausted",
        ):
            validate_with_single_repair(invalid, validator, ineffective)
        self.assertEqual(repair_calls, 1)

    def test_publish_reads_frozen_current_snapshot_and_writes_three_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            structure, structure_hash = self._write_snapshot(root)
            payload, digest, relative_path = self._publish(
                root,
                structure_hash,
            )
            self.assertFalse((root / "source.mp4").exists())
            self.assertEqual(relative_path, RESULT_PATH)
            self.assertEqual(digest, result_hash(payload))
            self.assertEqual(
                payload["visual_structure_result_hash"],
                result_hash(structure),
            )
            self.assertEqual(
                payload["items"][0]["keyframe_refs"],
                [
                    f"scene_0001-sample-{index:02d}"
                    for index in range(1, 5)
                ],
            )
            for path in (RESULT_PATH, MANIFEST_PATH, QUALITY_PATH):
                self.assertTrue((root / path).is_file(), path)
            manifest = json.loads(
                (root / MANIFEST_PATH).read_text(encoding="utf-8")
            )
            quality = json.loads(
                (root / QUALITY_PATH).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["result_hash"], digest)
            self.assertEqual(manifest["keyframe_count"], 4)
            self.assertTrue(quality["valid"])
            self.assertEqual(quality["action_claim_count"], 0)

    def test_publish_write_false_validates_without_creating_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, structure_hash = self._write_snapshot(root)
            payload, digest, relative_path = self._publish(
                root,
                structure_hash,
                write=False,
            )
            self.assertEqual(relative_path, RESULT_PATH)
            self.assertEqual(digest, result_hash(payload))
            for path in (RESULT_PATH, MANIFEST_PATH, QUALITY_PATH):
                self.assertFalse((root / path).exists(), path)

    def test_publish_rejects_stale_structure_and_changed_keyframe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, structure_hash = self._write_snapshot(root)
            with self.assertRaisesRegex(
                VisualSemanticValidationError,
                "input_manifest_mismatch",
            ):
                publish_visual_semantic_contract(
                    tool_root=root,
                    asset_id=ASSET_ID,
                    source_version=SOURCE_VERSION,
                    analysis_run_id=SEMANTIC_RUN_ID,
                    current_visual_structure_run_id="mlar_old",
                    current_visual_structure_result_hash=structure_hash,
                    candidate=self._candidate(),
                    visual_prompt_version=PROMPT_VERSION,
                    model_config_id=MODEL_CONFIG_ID,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, structure_hash = self._write_snapshot(root)
            keyframe = (
                root
                / INPUT_KEYFRAMES_REL
                / "scene_0001-sample-01.jpg"
            )
            keyframe.write_bytes(b"changed-after-snapshot")
            with self.assertRaisesRegex(
                VisualSemanticValidationError,
                "input_keyframe_hash_mismatch",
            ):
                self._publish(root, structure_hash)

    def test_publish_rejects_snapshot_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary)
            root = outer / "tool"
            root.mkdir()
            _, structure_hash = self._write_snapshot(root)
            keyframe = (
                root / INPUT_KEYFRAMES_REL / "scene_0001-sample-01.jpg"
            )
            external = outer / "external.jpg"
            external.write_bytes(keyframe.read_bytes())
            keyframe.unlink()
            keyframe.symlink_to(external)
            with self.assertRaisesRegex(
                VisualSemanticValidationError,
                "input_keyframe_path_invalid",
            ):
                self._publish(root, structure_hash)

    def test_candidate_input_is_not_mutated(self) -> None:
        structure = _structure_payload([_structure_item()])
        candidate = self._candidate()
        before = copy.deepcopy(candidate)
        validate_visual_semantic_candidate(
            structure_segments=structure,
            candidate=candidate,
        )
        self.assertEqual(candidate, before)


if __name__ == "__main__":
    unittest.main()
