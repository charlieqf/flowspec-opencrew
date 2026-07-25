from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.koubo.talking_head_models import (  # noqa: E402
    TALKING_HEAD_PRIVACY_GRID_PRESETS,
    resolve_talking_head_privacy_grid_preset,
    resolve_talking_head_video_model,
)
from opcrew_backend.koubo.koubo_storyboard import video_only_plan_routes  # noqa: E402
from ToolLibrary.TalkingHead_V1 import reference_privacy_grid  # noqa: E402
from ToolLibrary.TalkingHead_V1.video_plan_executor_modules import video_openrouter  # noqa: E402


FRONTEND = ROOT / "frontend/src/modules/koubo/KouboTaskList/KouboTaskCreateFromScriptModal.jsx"
ROUTER = ROOT / "backend/opcrew_backend/koubo/task_list_router.py"
STORYBOARD_CONFIG = ROOT / "ToolLibrary/TalkingHead_V1/03_StoryBoardConfig.py"
PLAN_GENERATOR = ROOT / "ToolLibrary/Analysis_V1/05_01_VideoPlanGenerator.py"
EXECUTOR = ROOT / "ToolLibrary/TalkingHead_V1/05_02_VideoPlanExecutor.py"
PROMPT = ROOT / "ToolLibrary/TalkingHead_V1/Reference/05_02/Video_SDR2V_TalkingHead.md"
ANALYSIS_PROMPT = ROOT / "ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V.md"
SDR2V_VIDEO = ROOT / "ToolLibrary/TalkingHead_V1/Reference/05_02/Video_SDR2V_TalkingHead.mp4"
ANALYSIS_SDR2V_VIDEO = ROOT / "ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V.mp4"
LOCAL_OPENROUTER = ROOT / "ToolLibrary/TalkingHead_V1/video_plan_executor_modules/video_openrouter.py"
LOCAL_GRID_TOOL = ROOT / "ToolLibrary/TalkingHead_V1/privacy_grid_tool.py"
REQUIREMENTS = ROOT / "docs/SessionDesign-R2/TalkingHead_V1_Max_SD_2_参考视频与隐私网格完整需求.md"


def load_executor(name: str):
    spec = importlib.util.spec_from_file_location(name, EXECUTOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TalkingHeadMaxSD2PrivacyGridContractTest(unittest.TestCase):
    def test_talking_head_0502_privacy_requires_selected_max_sd_2_and_oral_segment(self) -> None:
        executor = load_executor("talking_head_05_02_max_sd_2_privacy_gate_contract")
        reference = {
            "enabled": True,
            "provider": "openrouter",
            "model": "bytedance/seedance-2.0",
            "reference_video_path": "SessionContext/TalkingHead_PrivacyGrid/ReferenceVideo_PrivacyGrid.mp4",
            "privacy_grid_mode": True,
            "target_identity_grid_applied": True,
        }
        oral = {
            "talking_head_reference": reference,
            "tasks": {"need_lipsync": True, "sync_mode": "lipsync", "lipsync_reason": "visible_face"},
        }
        cutaway = {
            "talking_head_reference": reference,
            "tasks": {"need_lipsync": False, "sync_mode": "audio_replace_retime", "lipsync_reason": "cutaway"},
        }
        variables = {
            "default_video_config": {"provider": "openrouter", "model": "bytedance/seedance-2.0"},
            "talking_head": {"reference_privacy": {"enabled": True, "reference_privacy_mode": "red_grid_guide", "apply_privacy_grid_to_target_identity_image": True}},
        }
        other_model = {**variables, "default_video_config": {"provider": "xai", "model": "grok-imagine-video"}}

        self.assertTrue(executor.should_apply_talking_head_max_sd_2_privacy_grid(variables, oral))
        self.assertFalse(executor.should_apply_talking_head_max_sd_2_privacy_grid(variables, cutaway))
        self.assertFalse(executor.should_apply_talking_head_max_sd_2_privacy_grid(other_model, oral))
        privacy_off = json.loads(json.dumps(variables))
        privacy_off["talking_head"]["reference_privacy"]["apply_privacy_grid_to_target_identity_image"] = False
        self.assertFalse(executor.should_apply_talking_head_max_sd_2_privacy_grid(privacy_off, oral))
        selected = executor.video_selection_for_segment(variables, SimpleNamespace(video_provider="", video_model=""), oral)
        self.assertEqual(selected["prompt_template"], "Video_SDR2V_TalkingHead.md")
        unselected = executor.video_selection_for_segment(other_model, SimpleNamespace(video_provider="", video_model=""), oral)
        self.assertEqual(unselected, {"kind": "video", "provider": "xai", "model": "grok-imagine-video"})

        class FakePrivacyGrid:
            calls = 0

            @classmethod
            def prepare_continuity_frame(cls, workspace, runtime_variables, segment, source, working, asset_key):
                cls.calls += 1
                output = working / f"{asset_key}_ContinuityFirstFrame_PrivacyGrid.png"
                output.write_bytes(b"gridded")
                return output, {"grid_applied": True}

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "source.png"
            source.write_bytes(b"clean")
            gridded, metadata = executor.prepare_talking_head_continuity_frame(
                workspace, variables, oral, source, workspace, "oral", privacy_tool=FakePrivacyGrid
            )
            clean, skipped = executor.prepare_talking_head_continuity_frame(
                workspace, variables, cutaway, source, workspace, "cutaway", privacy_tool=FakePrivacyGrid
            )
            self.assertEqual(gridded.read_bytes(), b"gridded")
            self.assertTrue(metadata["grid_applied"])
            self.assertEqual(clean, source)
            self.assertEqual(skipped, {})
        self.assertEqual(FakePrivacyGrid.calls, 1)

    def test_model_and_frontend_expose_max_sd_2_with_two_independent_grid_switches(self) -> None:
        model = resolve_talking_head_video_model(model_key="max_sd_2")
        self.assertIsNotNone(model)
        self.assertEqual(model["provider"], "openrouter")
        self.assertEqual(model["model"], "bytedance/seedance-2.0")
        self.assertEqual(model["model_alias"], "Max SD 2")
        self.assertEqual(model["max_duration_seconds"], 15)

        frontend = FRONTEND.read_text(encoding="utf-8")
        router = ROUTER.read_text(encoding="utf-8")
        self.assertIn('key: "max_sd_2"', frontend)
        self.assertIn('alias: "Max SD 2"', frontend)
        self.assertRegex(frontend, r'key: "max_sd_2",\s+alias: "Max SD 2",\s+maxSeconds: 15')
        self.assertNotIn('<span>参考隐私设置</span>', frontend)
        self.assertNotIn('<option value="red_grid_guide">隐私网格（身份可见）</option>', frontend)
        self.assertIn('<span>应用视频</span>', frontend)
        self.assertIn('<span>应用目标图</span>', frontend)
        self.assertIn('has-reference-privacy', frontend)
        self.assertIn("apply_privacy_grid_to_reference_video: false", frontend)
        self.assertIn("apply_privacy_grid_to_target_identity_image: true", frontend)
        self.assertIn("function updateUseDefaultReferenceVideo(value)", frontend)
        self.assertIn("apply_privacy_grid_to_reference_video: !useSystemDefault", frontend)
        self.assertIn("onChange={(event) => updateUseDefaultReferenceVideo(event.currentTarget.checked)}", frontend)
        self.assertIn('onChange={(event) => update("apply_privacy_grid_to_reference_video", event.currentTarget.checked)}', frontend)
        self.assertIn('disabled={form().use_default_reference_video}', frontend)
        self.assertIn('<span>网格密度与线宽</span>', frontend)
        self.assertIn('privacy_grid_preset: "dense_12_1"', frontend)
        self.assertEqual(len(TALKING_HEAD_PRIVACY_GRID_PRESETS), 8)
        for preset, label in (
            ("dense_12_1", "密集 12×1（默认）"),
            ("dense_12_0_5", "密集细线 12×0.5"),
            ("medium_dense_24_1", "较密 24×1"),
            ("medium_dense_24_0_5", "较密细线 24×0.5"),
            ("sparse_36_1", "稀疏 36×1"),
            ("sparse_36_0_5", "稀疏细线 36×0.5"),
            ("very_sparse_48_1", "极疏 48×1"),
            ("very_sparse_48_0_5", "极疏细线 48×0.5"),
        ):
            resolved = resolve_talking_head_privacy_grid_preset(preset)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["label"], label)
            self.assertIn(label, frontend)
        self.assertIsNone(resolve_talking_head_privacy_grid_preset("unknown"))
        self.assertIn('payload.talking_head_video_model_key in {"max_2_7_w", "max_sd_2"}', router)
        self.assertIn('"reference_privacy_mode": "red_grid_guide"', router)
        self.assertIn('"privacy_grid_preset": privacy_grid_preset', router)
        self.assertIn('and not payload.use_default_reference_video', router)

    def test_talking_head_owns_copied_provider_prompt_and_grid_tools(self) -> None:
        self.assertTrue(LOCAL_OPENROUTER.is_file())
        self.assertTrue(LOCAL_GRID_TOOL.is_file())
        self.assertTrue(PROMPT.is_file())
        self.assertTrue(SDR2V_VIDEO.is_file())
        self.assertTrue(REQUIREMENTS.is_file())
        self.assertEqual(sha256(PROMPT), sha256(ANALYSIS_PROMPT))
        self.assertEqual(sha256(SDR2V_VIDEO), sha256(ANALYSIS_SDR2V_VIDEO))
        self.assertEqual(reference_privacy_grid.DEFAULT_REFERENCE_VIDEO, SDR2V_VIDEO)
        privacy_source = (ROOT / "ToolLibrary/TalkingHead_V1/reference_privacy_grid.py").read_text(encoding="utf-8")
        self.assertNotIn("ToolLibrary.DanceMimic_V1", privacy_source)
        self.assertNotIn("ToolLibrary.Analysis_V1", privacy_source)

    def test_talking_head_bundled_max_sd_2_fallback_video_is_not_longer_than_15_seconds(self) -> None:
        ffprobe = ROOT / "ToolLibrary/.bin/ffprobe"
        duration = float(
            subprocess.check_output(
                [
                    str(ffprobe),
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(SDR2V_VIDEO),
                ],
                text=True,
            ).strip()
        )

        self.assertLessEqual(duration, 15.0)
        self.assertEqual(SDR2V_VIDEO.name, "Video_SDR2V_TalkingHead.mp4")

    def test_prompt_requires_reference_person_expression_without_identity_transfer(self) -> None:
        prompt_source = PROMPT.read_text(encoding="utf-8")
        self.assertIn("参考视频只提供表情、微表情", prompt_source)
        self.assertIn("其他均不参考", prompt_source)
        package = video_openrouter.build_prompt_package({
            "segment": {
                "segment_id": "segment_001",
                "planned_video_duration": 8,
                "talking_head_reference": {
                    "prompt_template": "Video_SDR2V_TalkingHead.md",
                    "reference_video_grid_applied": True,
                    "target_identity_grid_applied": True,
                },
            },
            "reference_image_roles": [
                {"role": "continuity_first_frame", "path": "first.png"},
                {"role": "target_identity", "path": "identity.png"},
            ],
        })
        self.assertEqual(package["template_source"], "Ref_05_02_Video_SDR2V_TalkingHead.md")
        self.assertIn("参考视频只提供表情、微表情", package["prompt"])
        self.assertIn("清除目标身份图和表情参考视频的红网格", package["prompt"])
        self.assertEqual(video_openrouter.TALKING_HEAD_PROMPT_MAX_CHARS, 1000)
        self.assertEqual(video_openrouter.TALKING_HEAD_FIXED_PROMPT_MAX_CHARS, 700)
        self.assertEqual(video_openrouter.TALKING_HEAD_DIALOGUE_MIN_RESERVED_CHARS, 300)
        self.assertLessEqual(len(package["prompt"]), 1000)
        self.assertGreaterEqual(package["extracted_fields"]["prompt_budget"]["dialogue_budget_chars"], 300)

    def test_storyboard_plan_and_executor_carry_reference_and_privacy_contract(self) -> None:
        storyboard = STORYBOARD_CONFIG.read_text(encoding="utf-8")
        plan = PLAN_GENERATOR.read_text(encoding="utf-8")
        executor = EXECUTOR.read_text(encoding="utf-8")
        self.assertIn('"reference_video_role": "talking_head_motion_expression_reference"', storyboard)
        self.assertIn('"prompt_template": "Video_SDR2V_TalkingHead.md"', storyboard)
        self.assertIn('"talking_head_reference": dict_value(first_dialogue.get("talking_head_reference"))', plan)
        self.assertIn("prepare_talking_head_reference_videos", executor)
        self.assertIn("prepare_talking_head_continuity_frame", executor)
        self.assertIn("validate_talking_head_privacy_grid_inputs", executor)
        self.assertIn('"require_r2_public_assets": True', executor)
        self.assertNotIn('"require_r2_public_assets": bool(reference.get("reference_video_grid_applied"))', executor)

    def test_system_default_reference_without_new_grid_still_requires_r2(self) -> None:
        executor = load_executor("talking_head_05_02_default_reference_r2_contract")
        variables = {
            "default_video_config": {
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
            },
        }
        segment = {
            "talking_head_reference": {
                "enabled": True,
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
                "reference_video_path": "SessionContext/TalkingHead_PrivacyGrid/ReferenceVideo_Source.mp4",
                "reference_video_grid_applied": False,
                "use_default_reference_video": True,
            },
            "tasks": {
                "need_lipsync": True,
                "sync_mode": "lipsync",
                "lipsync_reason": "visible_face",
            },
        }
        args = SimpleNamespace(video_provider="", video_model="")

        with patch.object(
            executor,
            "load_provider_config",
            return_value={"provider": "openrouter", "model": "bytedance/seedance-2.0"},
        ):
            config = executor.load_video_provider_config_for_segment(args, variables, segment)

        self.assertTrue(config["talking_head_reference_video"])
        self.assertTrue(config["strict_input_references"])
        self.assertTrue(config["require_r2_public_assets"])

    def test_talking_head_openrouter_reference_video_uses_runtime_r2_transport(self) -> None:
        env_values = {
            "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT": "https://account.r2.cloudflarestorage.com",
            "OPENCREW_PUBLIC_ASSET_R2_BUCKET": "opencrew-public-assets",
            "OPENCREW_PUBLIC_ASSET_R2_REGION": "auto",
            "OPENCREW_PUBLIC_ASSET_R2_PREFIX": "talking-head-v1/openrouter-video",
            "OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS": "600",
            "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID": "runtime-access-key",
            "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY": "runtime-secret-key",
        }
        captured: dict[str, object] = {}

        def fake_put(endpoint, bucket, object_key, body, content_type, access_key, secret_key, region):
            captured["put"] = {
                "endpoint": endpoint,
                "bucket": bucket,
                "object_key": object_key,
                "body": body,
                "content_type": content_type,
                "access_key": access_key,
                "secret_key": secret_key,
                "region": region,
            }

        def fake_presign(endpoint, bucket, object_key, access_key, secret_key, region="auto", expires=3600):
            captured["presign"] = {
                "endpoint": endpoint,
                "bucket": bucket,
                "object_key": object_key,
                "access_key": access_key,
                "secret_key": secret_key,
                "region": region,
                "expires": expires,
            }
            return "https://account.r2.cloudflarestorage.com/opencrew-public-assets/reference.mp4?X-Amz-Signature=redacted"

        with patch.dict(os.environ, env_values, clear=False), tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "reference.mp4"
            video.write_bytes(b"video-reference")
            with patch.object(video_openrouter, "r2_put_object", side_effect=fake_put), patch.object(
                video_openrouter,
                "r2_presigned_get_url",
                side_effect=fake_presign,
            ):
                resolved = video_openrouter.apply_public_asset_runtime_config({
                    "public_asset_provider": "",
                    "require_r2_public_assets": True,
                    "extra": {"public_asset_provider": ""},
                })
                url = video_openrouter.reference_asset_url(video, resolved, "video")

        self.assertEqual(resolved["public_asset_provider"], "r2")
        self.assertEqual(resolved["r2_bucket"], "opencrew-public-assets")
        self.assertEqual(resolved["public_asset_prefix"], "talking-head-v1/openrouter-video")
        self.assertEqual(resolved["public_asset_config_source"], "runtime_environment")
        self.assertEqual(resolved["extra"]["public_asset_provider"], "r2")
        self.assertNotIn("runtime-access-key", json.dumps(resolved))
        self.assertNotIn("runtime-secret-key", json.dumps(resolved))
        self.assertIn("X-Amz-Signature=redacted", url)
        self.assertEqual(captured["put"]["access_key"], "runtime-access-key")
        self.assertEqual(captured["put"]["secret_key"], "runtime-secret-key")
        self.assertEqual(captured["presign"]["expires"], 600)

    def test_talking_head_openrouter_loads_saved_r2_config_during_run(self) -> None:
        r2_env_keys = {
            "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT",
            "OPENCREW_PUBLIC_ASSET_R2_BUCKET",
            "OPENCREW_PUBLIC_ASSET_R2_REGION",
            "OPENCREW_PUBLIC_ASSET_R2_PREFIX",
            "OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS",
            "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID",
            "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY",
        }
        control_keys = {
            "OPENCREW_DATA_DIR",
            "OPENCREW_PUBLIC_ASSETS_R2_ENV",
            "OPENCREW_PUBLIC_ASSET_R2_ENV",
            *r2_env_keys,
        }
        previous = {key: os.environ.get(key) for key in control_keys}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for key in control_keys:
                    os.environ.pop(key, None)
                os.environ["OPENCREW_DATA_DIR"] = tmp
                (Path(tmp) / "public_assets_r2.env").write_text(
                    "\n".join([
                        "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT=https://account.r2.cloudflarestorage.com",
                        "OPENCREW_PUBLIC_ASSET_R2_BUCKET=opencrew-public-assets",
                        "OPENCREW_PUBLIC_ASSET_R2_REGION=auto",
                        "OPENCREW_PUBLIC_ASSET_R2_PREFIX=talking-head-v1/openrouter-video",
                        "OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS=600",
                        "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID=saved-access-key",
                        "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY=saved-secret-key",
                    ]),
                    encoding="utf-8",
                )

                resolved = video_openrouter.apply_public_asset_runtime_config({
                    "public_asset_provider": "",
                    "r2_access_key_ref": "public_assets_r2_access_key_id",
                    "r2_secret_access_key_ref": "public_assets_r2_secret_access_key",
                })
                access_key = video_openrouter.r2_secret(
                    resolved,
                    "r2_access_key_id",
                    "r2_access_key_ref",
                    "public_assets_r2_access_key_id",
                )
                secret_key = video_openrouter.r2_secret(
                    resolved,
                    "r2_secret_access_key",
                    "r2_secret_access_key_ref",
                    "public_assets_r2_secret_access_key",
                )

            self.assertEqual(resolved["public_asset_provider"], "r2")
            self.assertEqual(resolved["r2_bucket"], "opencrew-public-assets")
            self.assertEqual(access_key, "saved-access-key")
            self.assertEqual(secret_key, "saved-secret-key")
            self.assertNotIn("saved-access-key", json.dumps(resolved))
            self.assertNotIn("saved-secret-key", json.dumps(resolved))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_talking_head_openrouter_does_not_resume_failed_provider_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "dialogue_001_Video_ProviderTask.json"
            state_path.write_text(json.dumps({
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
                "fingerprint": "same-request",
                "provider_task_id": "gen-video-failed",
                "status": "failed",
            }), encoding="utf-8")

            state = video_openrouter.matching_task_state(
                state_path,
                "same-request",
                "bytedance/seedance-2.0",
            )

        self.assertEqual(state, {})

    def test_uploaded_portrait_grid_does_not_mutate_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "SessionInput/talking_head/portraits/portrait.png"
            source.parent.mkdir(parents=True, exist_ok=True)
            cv2, np = reference_privacy_grid.grid_tool.import_cv2_np()
            image = np.full((240, 160, 3), 232, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(source), image))
            source_hash = sha256(source)
            config = reference_privacy_grid.render_config({"talking_head": {"reference_privacy": {}}})
            faces = [{"bbox": [48, 42, 58, 70], "confidence": 0.99}]
            with patch.object(reference_privacy_grid.grid_tool, "detect_faces_in_image", return_value=(faces, "contract_detector")):
                result = reference_privacy_grid.build_target_identity_grid(workspace, source, config, True)
            provider = workspace / result["provider_path"]
            self.assertEqual(sha256(source), source_hash)
            self.assertNotEqual(provider.resolve(), source.resolve())
            self.assertTrue(provider.is_file())
            self.assertGreaterEqual(result["line_presence_ratio"], 0.95)

    def test_target_identity_grid_deduplicates_one_face_and_ignores_small_background_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "portrait.png"
            cv2, np = reference_privacy_grid.grid_tool.import_cv2_np()
            image = np.full((1000, 600, 3), 232, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(source), image))
            config = reference_privacy_grid.render_config({"talking_head": {"reference_privacy": {}}})
            faces = [
                {"bbox": [120, 140, 260, 260], "confidence": 0.48},
                {"bbox": [122, 142, 258, 258], "confidence": 0.46},
                {"bbox": [165, 155, 220, 220], "confidence": 0.42},
                {"bbox": [410, 300, 90, 90], "confidence": 0.42},
                {"bbox": [250, 700, 30, 30], "confidence": 0.42},
            ]
            captured_config = {}

            def fake_detect(_image, detection_config):
                captured_config.update(detection_config)
                return faces, "opencv_haar"

            with patch.object(reference_privacy_grid.grid_tool, "detect_faces_in_image", side_effect=fake_detect):
                result = reference_privacy_grid.build_target_identity_grid(workspace, source, config, True)

            self.assertEqual(result["face_count"], 1)
            self.assertEqual(result["face_bbox"], [120, 140, 260, 260])
            self.assertEqual(len(result["faces"]), 1)
            self.assertEqual(result["detected_candidate_count"], 5)
            self.assertEqual(result["deduplicated_candidate_count"], 2)
            self.assertEqual(result["ignored_background_candidate_count"], 2)
            self.assertGreaterEqual(captured_config["opencv_haar_min_neighbors"], 4)

    def test_target_identity_grid_renders_every_distinct_valid_face(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "portrait.png"
            cv2, np = reference_privacy_grid.grid_tool.import_cv2_np()
            image = np.full((480, 640, 3), 232, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(source), image))
            config = reference_privacy_grid.render_config({"talking_head": {"reference_privacy": {}}})
            faces = [
                {"bbox": [80, 120, 220, 220], "confidence": 0.9},
                {"bbox": [84, 124, 214, 214], "confidence": 0.88},
                {"bbox": [360, 125, 205, 205], "confidence": 0.91},
            ]
            with patch.object(reference_privacy_grid.grid_tool, "detect_faces_in_image", return_value=(faces, "contract_detector")):
                result = reference_privacy_grid.build_target_identity_grid(workspace, source, config, True)

            self.assertEqual(result["face_count"], 2)
            self.assertEqual(len(result["faces"]), 2)
            self.assertEqual(result["deduplicated_candidate_count"], 1)
            self.assertEqual(result["ignored_background_candidate_count"], 0)
            self.assertEqual(result["render_mode"], "unique_region_union_once")
            self.assertEqual(result["maximum_render_count_per_pixel"], 1)
            self.assertGreaterEqual(result["line_presence_ratio_min"], 0.95)

    def test_target_identity_grid_rejects_background_object_below_one_fifth_of_primary_face(self) -> None:
        faces = [
            {"bbox": [727, 864, 660, 660], "confidence": 0.42, "cascade": "profile"},
            {"bbox": [644, 910, 621, 621], "confidence": 0.46, "cascade": "frontal_alt2"},
            {"bbox": [636, 907, 635, 635], "confidence": 0.48, "cascade": "frontal_default"},
            {"bbox": [782, 332, 295, 295], "confidence": 0.48, "cascade": "frontal_default"},
        ]

        selected, summary = reference_privacy_grid.target_identity_face_regions(faces, 1932, 3432)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["bbox"], [727, 864, 660, 660])
        self.assertEqual(summary["deduplicated_candidate_count"], 2)
        self.assertEqual(summary["ignored_background_candidate_count"], 1)

    def test_reference_video_sample_keeps_only_large_upper_foreground_face(self) -> None:
        faces = [
            {"bbox": [360, 480, 340, 340], "confidence": 0.42},
            {"bbox": [390, 500, 315, 315], "confidence": 0.48},
            {"bbox": [800, 1500, 120, 120], "confidence": 0.48},
            {"bbox": [100, 980, 70, 70], "confidence": 0.48},
        ]
        selected = reference_privacy_grid.reference_video_primary_face(faces, 1080, 2340)

        self.assertIsNotNone(selected)
        self.assertEqual(selected["bbox"], [390, 500, 315, 315])
        self.assertEqual(selected["cluster_candidate_count"], 2)

    def test_reference_video_frame_collapses_duplicate_detectors_without_choosing_oversized_box(self) -> None:
        faces = [
            {"bbox": [462, 615, 468, 468], "confidence": 0.48, "cascade": "frontal_default"},
            {"bbox": [468, 621, 457, 457], "confidence": 0.46, "cascade": "frontal_alt2"},
            {"bbox": [584, 654, 388, 388], "confidence": 0.42, "cascade": "profile"},
            {"bbox": [415, 536, 630, 630], "confidence": 0.42, "cascade": "profile"},
        ]

        selected, summary = reference_privacy_grid.reference_video_face_regions(faces, 1440, 2560)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["bbox"], [462, 615, 468, 468])
        self.assertEqual(selected[0]["cluster_candidate_count"], 4)
        self.assertEqual(summary["deduplicated_candidate_count"], 3)

    def test_privacy_grid_union_renders_overlapping_face_regions_once(self) -> None:
        cv2, np = reference_privacy_grid.grid_tool.import_cv2_np()
        image = np.full((240, 320, 3), 232, dtype=np.uint8)
        config = reference_privacy_grid.render_config({"talking_head": {"reference_privacy": {}}})
        overlapping_faces = [
            [40, 40, 120, 140],
            [80, 50, 120, 140],
            [55, 55, 60, 60],
        ]

        rendered, metadata = reference_privacy_grid.render_privacy_grid_union(
            image, overlapping_faces, config, cv2, np
        )

        self.assertEqual(metadata["render_mode"], "unique_region_union_once")
        self.assertEqual(metadata["discarded_contained_region_count"], 1)
        self.assertEqual(metadata["maximum_input_overlap_depth"], 2)
        self.assertEqual(metadata["maximum_render_count_per_pixel"], 1)
        self.assertEqual(metadata["render_pass_count"], 1)
        self.assertGreaterEqual(
            reference_privacy_grid.privacy_grid_union_line_presence(
                rendered, overlapping_faces, config, cv2, np
            ),
            0.95,
        )

    def test_privacy_grid_presets_scale_each_1080_reference_spacing_by_short_edge(self) -> None:
        for preset, expected_line_width in (
            ("dense_12_1", 1.0),
            ("dense_12_0_5", 0.5),
            ("medium_dense_24_1", 1.0),
            ("medium_dense_24_0_5", 0.5),
            ("sparse_36_1", 1.0),
            ("sparse_36_0_5", 0.5),
            ("very_sparse_48_1", 1.0),
            ("very_sparse_48_0_5", 0.5),
        ):
            resolved = resolve_talking_head_privacy_grid_preset(preset)
            self.assertIsNotNone(resolved)
            base = resolved["cell_size_reference"]
            config = reference_privacy_grid.render_config({
                "talking_head": {
                    "reference_privacy": {
                        "privacy_grid_preset": preset,
                        "render_config": {
                            "privacy_grid_preset": preset,
                            "privacy_grid": {
                                "cell_size_reference": base,
                                "line_width_reference": expected_line_width,
                            },
                        },
                    }
                }
            })
            self.assertEqual(config["privacy_grid"]["cell_size_reference"], base)
            for width, height, expected_cell in (
                (720, 1280, base),
                (1080, 1920, base),
                (1440, 2560, round(base * 1440 / 1080)),
                (2160, 3840, base * 2),
            ):
                line_width, cell, _color = reference_privacy_grid.grid_tool.privacy_grid_visual(
                    config, width, height
                )
                self.assertEqual(line_width, expected_line_width)
                self.assertEqual(cell, expected_cell)

    def test_continuity_safety_floor_does_not_mutate_user_identity_grid_config(self) -> None:
        original = reference_privacy_grid.render_config({
            "talking_head": {
                "reference_privacy": {
                    "privacy_grid_preset": "sparse_36_1",
                    "render_config": {
                        "privacy_grid_preset": "sparse_36_1",
                        "privacy_grid": {"cell_size_reference": 36, "line_width_reference": 1},
                    },
                }
            }
        })

        continuity, metadata = reference_privacy_grid.continuity_provider_safety_config(original)

        self.assertEqual(original["privacy_grid"]["cell_size_reference"], 36)
        self.assertEqual(original["privacy_grid_preset"], "sparse_36_1")
        self.assertEqual(continuity["privacy_grid"]["cell_size_reference"], 12)
        self.assertEqual(continuity["privacy_grid_preset"], "dense_12_1")
        self.assertTrue(metadata["provider_safety_escalated"])

    def test_uploaded_reference_video_safety_floor_preserves_requested_thin_line_config(self) -> None:
        original = reference_privacy_grid.render_config({
            "talking_head": {
                "reference_privacy": {
                    "privacy_grid_preset": "dense_12_0_5",
                    "render_config": {
                        "privacy_grid_preset": "dense_12_0_5",
                        "privacy_grid": {"cell_size_reference": 12, "line_width_reference": 0.5},
                    },
                }
            }
        })

        provider, metadata = reference_privacy_grid.reference_video_provider_safety_config(original)

        self.assertEqual(original["privacy_grid"]["line_width_reference"], 0.5)
        self.assertEqual(original["privacy_grid_preset"], "dense_12_0_5")
        self.assertEqual(provider["privacy_grid"]["cell_size_reference"], 12)
        self.assertEqual(provider["privacy_grid"]["line_width_reference"], 1)
        self.assertEqual(provider["privacy_grid_preset"], "dense_12_1")
        self.assertTrue(metadata["provider_safety_escalated"])
        self.assertEqual(metadata["requested_render"]["line_width_reference"], 0.5)
        self.assertEqual(metadata["effective_render"]["line_width_reference"], 1)

    def test_half_pixel_preset_renders_visibly_thinner_without_rounding_to_one(self) -> None:
        cv2, np = reference_privacy_grid.grid_tool.import_cv2_np()
        image = np.full((240, 320, 3), 232, dtype=np.uint8)
        faces = [[40, 40, 180, 160]]

        def config_for(line_width: float) -> dict[str, Any]:
            return reference_privacy_grid.render_config({
                "talking_head": {
                    "reference_privacy": {
                        "render_config": {
                            "privacy_grid": {
                                "cell_size_reference": 12,
                                "line_width_reference": line_width,
                            }
                        }
                    }
                }
            })

        half_config = config_for(0.5)
        full_config = config_for(1.0)
        half_rendered, _metadata = reference_privacy_grid.render_privacy_grid_union(image, faces, half_config, cv2, np)
        full_rendered, _metadata = reference_privacy_grid.render_privacy_grid_union(image, faces, full_config, cv2, np)
        _coverage, expected_lines, _metadata = reference_privacy_grid.privacy_grid_union_masks(
            image.shape[0], image.shape[1], faces, half_config, cv2, np
        )
        half_delta = float(np.mean(np.abs(half_rendered[expected_lines].astype(np.int16) - image[expected_lines].astype(np.int16))))
        full_delta = float(np.mean(np.abs(full_rendered[expected_lines].astype(np.int16) - image[expected_lines].astype(np.int16))))

        self.assertEqual(half_config["privacy_grid"]["line_width_reference"], 0.5)
        self.assertGreater(half_delta, 0)
        self.assertLess(half_delta, full_delta)
        self.assertGreaterEqual(
            reference_privacy_grid.privacy_grid_union_line_presence(half_rendered, faces, half_config, cv2, np),
            0.95,
        )

    def test_system_default_reference_never_runs_grid_overlay_even_if_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            portrait = workspace / "portrait.png"
            portrait.write_bytes(b"portrait-contract")
            variables = {
                "talking_head": {
                    "reference_privacy": {
                        "reference_privacy_mode": "red_grid_guide",
                        "apply_privacy_grid_to_reference_video": True,
                        "apply_privacy_grid_to_target_identity_image": False,
                        "privacy_grid_preset": "very_sparse_48_0_5",
                        "render_config": {
                            "privacy_grid_preset": "very_sparse_48_0_5",
                            "privacy_grid": {"cell_size_reference": 48, "line_width_reference": 0.5},
                        },
                    }
                }
            }
            with (
                patch.object(reference_privacy_grid, "build_target_identity_grid", return_value={"grid_applied": False}),
                patch.object(reference_privacy_grid, "build_reference_video_grid", return_value={"grid_applied": False}) as build_video,
            ):
                manifest = reference_privacy_grid.materialize_privacy_assets(
                    workspace,
                    variables,
                    portrait,
                    use_system_default=True,
                )

            self.assertFalse(build_video.call_args.args[3])
            self.assertTrue(manifest["requested_apply_to_reference_video"])
            self.assertFalse(manifest["apply_to_reference_video"])
            self.assertEqual(manifest["reference_video"]["skip_reason"], "system_default_preprocessed")
            self.assertEqual(manifest["effective_grid_scope"], "none")
            self.assertEqual(manifest["render"]["density_preset"], "very_sparse_48_0_5")
            self.assertEqual(manifest["render"]["cell_size_reference"], 48)
            self.assertEqual(manifest["render"]["line_width_reference"], 0.5)

    def test_privacy_grid_union_adds_only_new_area_for_distinct_faces(self) -> None:
        cv2, np = reference_privacy_grid.grid_tool.import_cv2_np()
        config = reference_privacy_grid.render_config({"talking_head": {"reference_privacy": {}}})
        coverage, _lines, metadata = reference_privacy_grid.privacy_grid_union_masks(
            300,
            500,
            [[40, 60, 120, 140], [90, 60, 120, 140], [320, 70, 100, 120]],
            config,
            cv2,
            np,
        )

        expected_union_area = (120 * 140) + (50 * 140) + (100 * 120)
        self.assertEqual(int(np.count_nonzero(coverage)), expected_union_area)
        self.assertEqual(metadata["maximum_input_overlap_depth"], 2)
        self.assertEqual(metadata["maximum_render_count_per_pixel"], 1)
        self.assertLess(metadata["region_area_ratio"], metadata["bounding_box_area_ratio"])

    def test_reference_video_builds_one_stable_region_per_persistent_face_track(self) -> None:
        samples = []
        for frame_index in range(5):
            samples.append({
                "frame_index": frame_index * 30,
                "faces": [
                    {"bbox": [80 + frame_index * 3, 70, 90, 100]},
                    {"bbox": [330 - frame_index * 2, 75, 85, 95]},
                ],
            })
        detections = {"segments": [{"segment_id": "reference", "samples": samples}]}
        config = reference_privacy_grid.render_config({"talking_head": {"reference_privacy": {}}})

        regions, metadata = reference_privacy_grid.reference_video_stable_regions(
            detections, 500, 300, config
        )

        self.assertEqual(len(regions), 2)
        self.assertEqual(metadata["track_count"], 2)
        self.assertEqual(metadata["raw_detection_count"], 10)
        self.assertEqual(metadata["discarded_transient_track_count"], 0)
        self.assertGreaterEqual(metadata["face_sample_coverage_ratio"], 0.95)
        self.assertGreaterEqual(metadata["face_area_coverage_ratio"], 0.90)

    def test_reference_video_expands_to_full_motion_without_lowering_privacy_thresholds(self) -> None:
        samples = []
        for index, x in enumerate((80, 100, 120, 140, 160, 180, 200, 220, 240, 260)):
            samples.append({
                "frame_index": index * 30,
                "faces": [{"bbox": [x, 100, 100, 100], "confidence": 0.99}],
            })
        detections = {"segments": [{"segment_id": "reference", "samples": samples}]}
        config = reference_privacy_grid.render_config({"talking_head": {"reference_privacy": {}}})

        regions, metadata = reference_privacy_grid.reference_video_stable_regions(
            detections,
            480,
            720,
            config,
        )

        self.assertTrue(metadata["motion_bounds_expanded"])
        self.assertEqual(metadata["tracking_mode"], "spatial_face_track_full_motion_region")
        self.assertLess(metadata["initial_face_sample_coverage_ratio"], 0.95)
        self.assertGreaterEqual(metadata["face_sample_coverage_ratio"], 0.95)
        self.assertGreaterEqual(metadata["face_area_coverage_ratio"], 0.90)
        self.assertLessEqual(regions[0][0], 80)
        self.assertGreaterEqual(regions[0][0] + regions[0][2], 360)

    def test_reference_video_sample_drops_only_tiny_or_lower_false_positives(self) -> None:
        faces = [
            {"bbox": [700, 1234, 100, 100], "confidence": 0.48},
            {"bbox": [480, 1501, 94, 94], "confidence": 0.48},
        ]
        self.assertIsNone(reference_privacy_grid.reference_video_primary_face(faces, 1080, 2340))

    def test_ungridded_reference_video_copy_preserves_container_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "SessionInput/talking_head/reference_videos/reference.webm"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"contract-video-container")
            copied = reference_privacy_grid.copy_source_video(workspace, source)
            self.assertEqual(copied.suffix, ".webm")
            self.assertEqual(copied.read_bytes(), source.read_bytes())

    def test_reference_video_grid_writes_independent_provider_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "SessionInput/talking_head/reference_videos/reference.mp4"
            source.parent.mkdir(parents=True, exist_ok=True)
            cv2, np = reference_privacy_grid.grid_tool.import_cv2_np()
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 6.0, (160, 240))
            self.assertTrue(writer.isOpened())
            for frame_index in range(12):
                frame = np.full((240, 160, 3), 220 - frame_index, dtype=np.uint8)
                writer.write(frame)
            writer.release()
            source_hash = sha256(source)
            config = reference_privacy_grid.render_config({"talking_head": {"reference_privacy": {}}})
            detections = {
                "schema_version": "contract",
                "face_detection_engine": "contract_detector",
                "segments": [{"segment_id": "talking_head_reference", "samples": [{"frame_index": 0, "faces": [{"bbox": [48, 42, 58, 70]}]}]}],
            }
            probe = {"fps": 6.0, "width": 160, "height": 240, "frame_count": 12}
            with (
                patch.object(reference_privacy_grid, "reference_video_detections", return_value=(detections, probe)),
                patch.object(reference_privacy_grid.grid_tool, "reencode_reference_video_for_provider", return_value={"codec": "contract"}),
            ):
                result = reference_privacy_grid.build_reference_video_grid(workspace, source, config, True)
            provider = workspace / result["provider_path"]
            self.assertEqual(sha256(source), source_hash)
            self.assertNotEqual(provider.resolve(), source.resolve())
            self.assertTrue(provider.is_file())
            self.assertGreaterEqual(result["line_presence_ratio_min"], 0.95)
            self.assertEqual(result["fixed_region"]["render_mode"], "unique_region_union_once")
            self.assertEqual(result["fixed_region"]["maximum_render_count_per_pixel"], 1)

    def test_tail_frame_grid_enforces_provider_safety_floor_and_keeps_clean_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "SessionOutput/storyboard/Working/dak_0001_TailFrame.png"
            source.parent.mkdir(parents=True, exist_ok=True)
            cv2, np = reference_privacy_grid.grid_tool.import_cv2_np()
            image = np.full((240, 160, 3), 232, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(source), image))
            source_hash = sha256(source)
            manifest_path = workspace / reference_privacy_grid.PRIVACY_GRID_MANIFEST_REL
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps({
                "mode": "red_grid_guide",
                "apply_to_target_identity_image": True,
                "render": {
                    "density_preset": "sparse_36_0_5",
                    "line_width_reference": 0.5,
                    "cell_size_reference": 36,
                },
                "target_identity": {"provider_sha256": "different"},
            }), encoding="utf-8")
            variables = {"talking_head": {"reference_privacy": {"reference_privacy_mode": "red_grid_guide"}}}
            segment = {
                "first_frame": {"source_type": "previous_segment_tail_frame"},
                "talking_head_reference": {
                    "privacy_grid_mode": True,
                    "target_identity_grid_applied": True,
                    "privacy_grid_manifest_path": reference_privacy_grid.PRIVACY_GRID_MANIFEST_REL,
                },
            }
            faces = [{"bbox": [48, 42, 58, 70], "confidence": 0.99}]
            captured_render_configs = []
            original_render = reference_privacy_grid.render_privacy_grid_union

            def capture_render(image, coverage_bboxes, config, cv2_module, np_module):
                captured_render_configs.append(config)
                return original_render(image, coverage_bboxes, config, cv2_module, np_module)

            with (
                patch.object(reference_privacy_grid.grid_tool, "detect_faces_in_image", return_value=(faces, "contract_detector")),
                patch.object(reference_privacy_grid, "render_privacy_grid_union", side_effect=capture_render),
            ):
                provider, result = reference_privacy_grid.prepare_continuity_frame(
                    workspace,
                    variables,
                    segment,
                    source,
                    source.parent,
                    "dak_0002",
                )
                repeated_provider, repeated_result = reference_privacy_grid.prepare_continuity_frame(
                    workspace,
                    variables,
                    segment,
                    provider,
                    source.parent,
                    "dak_0002",
                )
            self.assertEqual(sha256(source), source_hash)
            self.assertEqual(provider.name, "dak_0002_ContinuityFirstFrame_PrivacyGrid.png")
            self.assertTrue(provider.is_file())
            self.assertEqual(result["face_count"], 1)
            self.assertGreaterEqual(result["line_presence_ratio_min"], 0.95)
            self.assertTrue(result["provider_safety_escalated"])
            self.assertEqual(result["provider_safety_policy"], "continuity_dense_12_1_minimum")
            self.assertEqual(result["requested_render"]["cell_size_reference"], 36)
            self.assertEqual(result["requested_render"]["line_width_reference"], 0.5)
            self.assertEqual(result["effective_render"]["cell_size_reference"], 12)
            self.assertEqual(result["effective_render"]["line_width_reference"], 1)
            self.assertEqual(captured_render_configs[0]["privacy_grid"]["cell_size_reference"], 12)
            self.assertEqual(captured_render_configs[0]["privacy_grid"]["line_width_reference"], 1)
            self.assertEqual(repeated_provider, provider)
            self.assertTrue(repeated_result["already_gridded"])

    def test_video_only_tail_materialize_recovers_talking_head_privacy_from_storyboard_root(self) -> None:
        class FakePrivacyGrid:
            @staticmethod
            def prepare_continuity_frame(workspace, variables, segment, source_path, working_dir, asset_key):
                self.assertEqual(segment["talking_head_reference"]["privacy_grid_manifest_path"], "SessionOutput/reference/talking_head_privacy_grid_manifest.json")
                output = working_dir / f"{asset_key}_ContinuityFirstFrame_PrivacyGrid.png"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"privacy-grid-tail")
                return output, {"grid_applied": True, "line_presence_ratio_min": 1.0}

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "SessionOutput/storyboard/Working/dak_0001_TailFrame.png"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"clean-tail")
            storyboard = {
                "workflow_id": "person_talking_head_v1",
                "talking_head_config": {
                    "max_sd_2_reference": {
                        "privacy_grid_mode": True,
                        "target_identity_grid_applied": True,
                        "privacy_grid_manifest_path": "SessionOutput/reference/talking_head_privacy_grid_manifest.json",
                    }
                },
            }
            variables = {
                "default_video_config": {"provider": "openrouter", "model": "bytedance/seedance-2.0"},
                "talking_head": {
                    "reference_privacy": {
                        "enabled": True,
                        "reference_privacy_mode": "red_grid_guide",
                        "apply_privacy_grid_to_target_identity_image": True,
                    }
                },
            }
            item = {
                "source_segment": {
                    "tasks": {
                        "need_lipsync": True,
                        "sync_mode": "lipsync",
                        "lipsync_reason": "visible_face",
                    }
                }
            }
            with patch.object(video_only_plan_routes, "talking_head_privacy_grid_module", return_value=FakePrivacyGrid()):
                output, metadata = video_only_plan_routes.apply_talking_head_tail_frame_privacy(
                    workspace,
                    variables,
                    storyboard,
                    item,
                    source,
                    source.parent,
                    "dak_0002",
                )

            self.assertEqual(output.read_bytes(), b"privacy-grid-tail")
            self.assertTrue(metadata["grid_applied"])

            clean_output, clean_metadata = video_only_plan_routes.apply_talking_head_tail_frame_privacy(
                workspace,
                variables,
                storyboard,
                {"source_segment": {"tasks": {"need_lipsync": False, "sync_mode": "audio_replace_retime"}}},
                source,
                source.parent,
                "dak_0003",
            )
            self.assertEqual(clean_output, source)
            self.assertEqual(clean_metadata, {})


if __name__ == "__main__":
    unittest.main()
