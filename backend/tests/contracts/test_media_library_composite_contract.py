from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.media_library_analysis.composite_contracts import (  # noqa: E402
    CANDIDATE_SCHEMA_VERSION,
    INDEX_PATH,
    INPUT_DIALOGUE_REL,
    INPUT_MANIFEST_REL,
    INPUT_SCHEMA_VERSION,
    INPUT_SEMANTIC_REL,
    INPUT_STRUCTURE_REL,
    QUALITY_PATH,
    RESULT_PATH,
    SEARCH_MANIFEST_PATH,
    VIRTUAL_CLIPS_PATH,
    CompositeValidationError,
    publish_composite_contract,
)
from opcrew_backend.media_library_analysis.composite import SYSTEM_PROMPT  # noqa: E402
from opcrew_backend.media_library_analysis.contracts import result_hash  # noqa: E402
from opcrew_backend.tool_sessions.registry_normalizer import (  # noqa: E402
    normalize_registry_file,
)


SOURCE_VERSION = "a" * 64
ASSET_ID = "asset-composite"
DIALOGUE_RUN_ID = "mlar_dialogue_composite"
STRUCTURE_RUN_ID = "mlar_visual_structure_composite"
SEMANTIC_RUN_ID = "mlar_visual_semantic_composite"
COMPOSITE_RUN_ID = "mlar_composite_contract"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class MediaLibraryCompositeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dialogue = {
            "schema_version": "media_library_dialogue_fragments_v1",
            "asset_id": ASSET_ID,
            "source_version": SOURCE_VERSION,
            "analysis_run_id": DIALOGUE_RUN_ID,
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
        self.structure = {
            "schema_version": "media_library_visual_structure_v2",
            "asset_id": ASSET_ID,
            "source_version": SOURCE_VERSION,
            "analysis_run_id": STRUCTURE_RUN_ID,
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

        self.semantic = {
            "schema_version": "media_library_visual_semantic_v2",
            "asset_id": ASSET_ID,
            "source_version": SOURCE_VERSION,
            "analysis_run_id": SEMANTIC_RUN_ID,
            "visual_structure_run_id": STRUCTURE_RUN_ID,
            "visual_structure_result_hash": result_hash(self.structure),
            "sampling_strategy": "scene_uniform_4_v1",
            "visual_prompt_version": "visual_semantic_prompt_v3",
            "model_config_id": "visual_semantic_default_v1",
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
                    "visual_summary": "一名讲解者在室内展示桌面产品。",
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
        self.hashes = {
            "dialogue": result_hash(self.dialogue),
            "structure": result_hash(self.structure),
            "semantic": result_hash(self.semantic),
        }
        write_json(self.root / INPUT_DIALOGUE_REL, self.dialogue)
        write_json(self.root / INPUT_STRUCTURE_REL, self.structure)
        write_json(self.root / INPUT_SEMANTIC_REL, self.semantic)
        write_json(
            self.root / INPUT_MANIFEST_REL,
            {
                "schema_version": INPUT_SCHEMA_VERSION,
                "asset_id": ASSET_ID,
                "source_version": SOURCE_VERSION,
                "dialogue_run_id": DIALOGUE_RUN_ID,
                "dialogue_result_hash": self.hashes["dialogue"],
                "visual_structure_run_id": STRUCTURE_RUN_ID,
                "visual_structure_result_hash": self.hashes["structure"],
                "visual_semantic_run_id": SEMANTIC_RUN_ID,
                "visual_semantic_result_hash": self.hashes["semantic"],
                "sampling_strategy": "scene_uniform_4_v1",
                "composite_prompt_version": "composite_prompt_v1",
                "model_config_id": "composite_default_v1",
                "source_duration_ms": 2000,
                "source_width": 1920,
                "source_height": 1080,
                "source_orientation": "landscape",
            },
        )

    def test_system_prompt_describes_sparse_four_frame_evidence(self) -> None:
        self.assertIn("four sparse ordered keyframes", SYSTEM_PROMPT)
        self.assertNotIn("sampling is one midpoint", SYSTEM_PROMPT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def candidate(self) -> dict:
        return {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "items": [
                {
                    "fragment_id": "composite_0001",
                    "asset_id": ASSET_ID,
                    "scheme": "composite",
                    "start_ms": 0,
                    "end_ms": 2000,
                    "duration_ms": 2000,
                    "title": "讲解桌面产品用途",
                    "summary": "讲解者展示产品，同时介绍核心用途。",
                    "dialogue_text": "介绍桌面产品的核心用途。",
                    "visual_summary": "一名讲解者在室内展示桌面产品。",
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
                    "keyframe_refs": [
                        f"scene_0001-sample-{index:02d}"
                        for index in range(1, 5)
                    ],
                    "boundary_reasons": ["对白和 Scene 边界一致"],
                    "confidence": 0.91,
                    "needs_review": False,
                }
            ],
        }

    def publish(self, candidate: dict, *, write: bool = True):
        return publish_composite_contract(
            tool_root=self.root,
            asset_id=ASSET_ID,
            source_version=SOURCE_VERSION,
            analysis_run_id=COMPOSITE_RUN_ID,
            dialogue_run_id=DIALOGUE_RUN_ID,
            dialogue_result_hash=self.hashes["dialogue"],
            visual_structure_run_id=STRUCTURE_RUN_ID,
            visual_structure_result_hash=self.hashes["structure"],
            visual_semantic_run_id=SEMANTIC_RUN_ID,
            visual_semantic_result_hash=self.hashes["semantic"],
            composite_prompt_version="composite_prompt_v1",
            model_config_id="composite_default_v1",
            candidate=candidate,
            write=write,
        )

    def test_registry_declares_contract_only_text_model_tool(self) -> None:
        registry = normalize_registry_file(
            REPO_ROOT / "ToolLibrary" / "OpenCut_V1" / "tool_registry.json",
            strict=True,
        )
        tool = next(item for item in registry["tools"] if item["id"] == "04_01")
        self.assertTrue(tool["uses_llm"])
        self.assertFalse(tool["uses_vlm"])
        self.assertEqual(tool.get("hard_dependencies") or [], [])
        self.assertNotIn("source_video", json.dumps(tool))
        script_path = REPO_ROOT / tool["script"]
        spec = importlib.util.spec_from_file_location(
            "open_cut_v1_composite_contract", script_path
        )
        self.assertIsNotNone(spec)

    def test_publish_writes_all_outputs_and_uses_integer_ms(self) -> None:
        payload, digest, relative = self.publish(self.candidate())
        self.assertEqual(relative, RESULT_PATH)
        self.assertEqual(len(digest), 64)
        self.assertEqual(payload["items"][0]["duration_ms"], 2000)
        for path in (
            RESULT_PATH,
            INDEX_PATH,
            VIRTUAL_CLIPS_PATH,
            SEARCH_MANIFEST_PATH,
            QUALITY_PATH,
        ):
            self.assertTrue((self.root / path).is_file(), path)
        line = json.loads(
            (self.root / INDEX_PATH).read_text(encoding="utf-8").strip()
        )
        self.assertIn("介绍桌面产品", line["search_text"])

    def test_write_false_validates_without_outputs(self) -> None:
        payload, digest, _ = self.publish(self.candidate(), write=False)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(len(digest), 64)
        self.assertFalse((self.root / RESULT_PATH).exists())

    def test_unknown_reference_and_out_of_range_boundary_are_rejected(self) -> None:
        candidate = self.candidate()
        candidate["items"][0]["dialogue_refs"] = ["dialogue_unknown"]
        with self.assertRaisesRegex(
            CompositeValidationError, "composite_dialogue_ref_unknown"
        ):
            self.publish(candidate, write=False)
        candidate = self.candidate()
        candidate["items"][0]["end_ms"] = 1999
        candidate["items"][0]["duration_ms"] = 1999
        with self.assertRaisesRegex(
            CompositeValidationError, "composite_time_invalid"
        ):
            self.publish(candidate, write=False)

    def test_visual_hallucination_and_midpoint_action_are_rejected(self) -> None:
        candidate = self.candidate()
        candidate["items"][0]["objects"] = ["上游没有的品牌产品"]
        with self.assertRaisesRegex(
            CompositeValidationError, "composite_visual_fact_unsupported"
        ):
            self.publish(candidate, write=False)
        candidate = self.candidate()
        candidate["items"][0]["action"] = "持续走动"
        candidate["items"][0]["visual_claim_refs"]["action"] = [
            "scene_0001"
        ]
        with self.assertRaisesRegex(
            CompositeValidationError, "composite_midpoint_action_must_be_null"
        ):
            self.publish(candidate, write=False)

    def test_upstream_hash_change_prevents_publish(self) -> None:
        self.dialogue["items"][0]["dialogue_text"] = "已变化"
        write_json(self.root / INPUT_DIALOGUE_REL, self.dialogue)
        with self.assertRaisesRegex(
            CompositeValidationError, "composite_upstream_snapshot_invalid"
        ):
            self.publish(self.candidate(), write=False)


if __name__ == "__main__":
    unittest.main()
