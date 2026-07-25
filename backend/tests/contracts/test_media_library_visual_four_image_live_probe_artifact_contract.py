from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT = (
    REPO_ROOT
    / "frontend"
    / "e2e"
    / "artifacts"
    / "media-library-silent-visual-search"
    / "20260722-131216"
    / "four-image-capability-smoke.json"
)


class MediaLibraryVisualFourImageLiveProbeArtifactContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    def test_real_dscf0157_four_image_request_passed(self) -> None:
        payload = self.payload
        self.assertEqual(
            payload["schema_version"],
            "media_library_visual_four_image_live_probe_v1",
        )
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(
            payload["source"]["asset_id"],
            "mla_1784601908573_70c828790521",
        )
        self.assertEqual(
            payload["fragment"]["sampling_strategy"],
            "scene_uniform_4_v1",
        )
        frames = payload["fragment"]["frames"]
        self.assertEqual(len(frames), 4)
        self.assertEqual(
            [frame["keyframe_time_ms"] for frame in frames],
            [1625, 4875, 8125, 11375],
        )
        self.assertTrue(
            all(len(frame["image_sha256"]) == 64 for frame in frames)
        )

    def test_one_request_contains_all_four_ordered_images(self) -> None:
        request = self.payload["request"]
        self.assertEqual(request["model_call_count"], 1)
        self.assertEqual(request["text_part_count"], 1)
        self.assertEqual(request["image_part_count"], 4)
        self.assertEqual(
            request["ordered_keyframe_ids"],
            [
                "scene_0001-sample-01",
                "scene_0001-sample-02",
                "scene_0001-sample-03",
                "scene_0001-sample-04",
            ],
        )
        self.assertFalse(request["customer_video_uploaded"])
        self.assertFalse(request["data_urls_retained"])

    def test_response_obeys_sparse_evidence_contract(self) -> None:
        candidate = self.payload["response"]["validated_item"]
        self.assertIsNone(candidate["action"])
        self.assertEqual(candidate["claim_evidence"]["action"], [])
        self.assertIn("绿色包装", candidate["visual_summary"])
        self.assertTrue(
            any("深色液体" in item for item in candidate["objects"])
        )
        self.assertIsNone(self.payload["response"]["error"])
        self.assertFalse(self.payload["security"]["credentials_included"])
        self.assertFalse(self.payload["security"]["raw_image_bytes_included"])


if __name__ == "__main__":
    unittest.main()
