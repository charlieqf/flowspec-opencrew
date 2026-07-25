from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_05_VideoOnlyPlanGenerator.py"
EXECUTOR_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_06_VideoOnlyPlanExecutor.py"
BACKEND_PATH = REPO_ROOT / "backend"
MAIN_BACKEND_PATH = REPO_ROOT / "backend"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    asset_key = "dak_0001"
    image_rel = "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
    audio_rel = f"SessionOutput/storyboard/Working/{asset_key}_Audio_Final.wav"
    (workspace / image_rel).parent.mkdir(parents=True, exist_ok=True)
    (workspace / image_rel).write_bytes(b"fake-jpeg")
    (workspace / audio_rel).parent.mkdir(parents=True, exist_ok=True)
    (workspace / audio_rel).write_bytes(b"fake-wav")
    write_json(
        workspace / "SessionContext/Variables.json",
        {
            "workflow_id": "openclip_analysis",
            "default_video_config": {
                "provider": "openai",
                "model": "sora-test",
                "api_key_ref": "test-video-key",
                "has_api_key": True,
            },
            "default_image_config": {
                "provider": "openai",
                "model": "gpt-image-1.5",
                "api_key_ref": "test-image-key",
                "has_api_key": True,
            },
            "default_tts_config": {
                "provider": "google",
                "model": "gemini-tts",
                "api_key_ref": "test-tts-key",
                "has_api_key": True,
            },
        },
    )
    write_json(
        workspace / "SessionOutput/storyboard/srt_storyboard.json",
        {
            "schema_version": "analysis_v1_storyboard_0.1",
            "shots": [
                {
                    "shot_id": "shot_001",
                    "summary": "口播测试",
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "summary": "第一句",
                            "start": 0.0,
                            "end": 1.2,
                            "duration": 1.2,
                            "dialogue_items": [
                                {
                                    "srt_id": "srt_0001_01",
                                    "dialogue_asset_key": asset_key,
                                    "dialogue": "给家里备这个化橘红啊",
                                    "start": 0.0,
                                    "end": 1.2,
                                    "duration": 1.2,
                                    "image_path": image_rel,
                                    "assets": {"audio": {"slot": "Audio_Final", "source_type": "generated", "path": audio_rel}},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    return workspace


def existing_working_slot_path(root: Path, asset_key: str, slot: str, exts: set[str], preferred_suffix: str = ".mp4", **_sc_kwargs) -> str:
    working = root / "SessionOutput/storyboard/Working"
    preferred = working / f"{asset_key}_{slot}{preferred_suffix}"
    if preferred.exists() and preferred.suffix.lower() in exts:
        return str(preferred.relative_to(root))
    matches = sorted(working.glob(f"{asset_key}_{slot}.*"))
    for path in matches:
        if path.is_file() and path.suffix.lower() in exts:
            return str(path.relative_to(root))
    return ""


class AnalysisV1VideoOnlyPlanToolsContractTest(unittest.TestCase):
    def test_video_only_plan_generator_creates_raw_plan(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_05_contract")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))

            result = generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))

            self.assertEqual(result["status"], "completed")
            plan = json.loads((workspace / "SessionOutput/storyboard/video_only_generation_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["schema_version"], "analysis_v1_video_only_generation_plan_0.1")
            self.assertEqual(plan["summary"]["planned_prompt_tasks"], 1)
            self.assertEqual(plan["summary"]["planned_video_tasks"], 1)
            task = plan["video_only_tasks"][0]
            self.assertEqual(task["asset_key"], "dak_0001")
            self.assertEqual(task["planned_outputs"]["raw_video_path"], "SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4")
            self.assertEqual(task["planned_outputs"]["final_video_path"], "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4")
            self.assertEqual(task["planned_outputs"]["tail_frame_path"], "SessionOutput/storyboard/Working/dak_0001_TailFrame.png")
            self.assertEqual(task["steps"]["confirm_final"]["status"], "disabled")

    def test_video_only_plan_generator_uses_storyboard_bound_final_video(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_05_contract_bound_final")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            bound_rel = "SessionOutput/storyboard/Working/dak_0001_manual_Video_Final.mp4"
            (workspace / bound_rel).write_bytes(b"manual-final-video")
            storyboard_path = workspace / "SessionOutput/storyboard/srt_storyboard.json"
            storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
            dialogue = storyboard["shots"][0]["scenes"][0]["dialogue_items"][0]
            dialogue["working_assets"] = {
                "audio": dialogue["assets"]["audio"],
                "video": {"slot": "Video_Final", "source_type": "manual", "path": bound_rel},
            }
            write_json(storyboard_path, storyboard)

            result = generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))

            self.assertEqual(result["status"], "completed")
            plan = json.loads((workspace / "SessionOutput/storyboard/video_only_generation_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["summary"]["planned_prompt_tasks"], 0)
            self.assertEqual(plan["summary"]["planned_video_tasks"], 0)
            self.assertEqual(plan["summary"]["final_completed_count"], 1)
            task = plan["video_only_tasks"][0]
            self.assertEqual(task["status"], "final_completed")
            self.assertEqual(task["planned_outputs"]["final_video_path"], bound_rel)
            self.assertTrue(task["existing_assets"]["final_exists"])
            self.assertTrue(task["existing_assets"]["final_bound"])
            self.assertEqual(task["steps"]["prompt"]["status"], "disabled_consumed_by_video")
            self.assertEqual(task["steps"]["video"]["status"], "completed_working")
            self.assertEqual(task["steps"]["confirm_final"]["status"], "completed_working")

    def test_video_only_prompt_only_writes_business_prompt_without_raw_or_final(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_05_contract_prompt")
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_06_contract_prompt")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))

            result = executor.run(executor.parse_args(["--workspace", str(workspace), "--mode", "prompt-only", "--overwrite-prompt", "--force"]))

            self.assertEqual(result["status"], "completed")
            prompt_path = workspace / "SessionOutput/storyboard/Working/dak_0001_VideoPrompt.json"
            raw_path = workspace / "SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4"
            final_path = workspace / "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4"
            self.assertTrue(prompt_path.exists())
            self.assertFalse(raw_path.exists())
            self.assertFalse(final_path.exists())
            prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
            self.assertEqual(prompt["asset_key"], "dak_0001")
            self.assertEqual(prompt["prompt_status"], "draft_generated")
            state = json.loads((workspace / "SessionOutput/storyboard/video_only_plan_execution_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["mode"], "prompt-only")
            self.assertEqual(state["segments"]["dak_0001"]["steps"]["prompt"]["status"], "completed_working")

    def test_video_only_prompt_uses_default_video_config_openrouter_template(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_05_contract_openrouter_prompt")
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_06_contract_openrouter_prompt")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            variables_path = workspace / "SessionContext/Variables.json"
            variables = json.loads(variables_path.read_text(encoding="utf-8"))
            variables["default_video_config"] = {
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
                "api_key_ref": "video_openrouter_key",
                "has_api_key": True,
            }
            write_json(variables_path, variables)
            generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))

            result = executor.run(executor.parse_args(["--workspace", str(workspace), "--mode", "prompt-only", "--overwrite-prompt", "--force"]))

            self.assertEqual(result["status"], "completed")
            prompt = json.loads(
                (workspace / "SessionOutput/storyboard/Working/dak_0001_VideoPrompt.json").read_text(encoding="utf-8")
            )
            self.assertEqual(prompt["provider_profile"], "video_openrouter")
            self.assertEqual(prompt["template_source"], "Ref_05_02_Video_SDR2V.md")
            self.assertTrue((workspace / "S13_05_06_VideoOnlyPlanExecutor/Working/Video_SDR2V.mp4").exists())
            self.assertTrue((workspace / "S13_05_06_VideoOnlyPlanExecutor/Prompt/Ref_05_02_Video_SDR2V.md").exists())
            self.assertNotIn("Video_Grok", json.dumps(prompt, ensure_ascii=False))

    def test_0502_provider_selection_uses_variables_and_rejects_conflicting_override(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_contract_variables_model_source",
        )
        variables = {
            "default_video_config": {
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
            }
        }

        selected = executor.video_selection_for_segment(
            variables,
            SimpleNamespace(video_provider="", video_model=""),
            {"tasks": {"lipsync_reason": "visible_talking_head"}},
        )
        module = executor.video_module_for(selected["provider"], selected["model"])

        self.assertEqual(selected["provider"], "openrouter")
        self.assertEqual(selected["model"], "bytedance/seedance-2.0")
        self.assertEqual(selected["prompt_template"], "Video_SDR2V.md")
        self.assertEqual(selected["reference_mode"], "input_references")
        self.assertEqual(module.TEMPLATE_NAME, "Ref_05_02_Video_OpenRouter.md")
        self.assertEqual(
            module.template_spec({"prompt_template": selected["prompt_template"]})[0],
            "Ref_05_02_Video_SDR2V.md",
        )
        with self.assertRaisesRegex(executor.ToolError, "Runtime video model overrides are not allowed"):
            executor.provider_selection(variables, "video", "xai", "grok-imagine-video")

    def test_0502_max_sd_2_cutaway_keeps_generic_openrouter_template(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_contract_max_sd_2_cutaway",
        )
        variables = {
            "default_video_config": {
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
            }
        }
        segment = {"tasks": {"lipsync_reason": "user_marked_cutaway"}}

        selected = executor.video_selection_for_segment(
            variables,
            SimpleNamespace(video_provider="", video_model=""),
            segment,
        )
        module = executor.video_module_for(selected["provider"], selected["model"])

        self.assertNotIn("prompt_template", selected)
        self.assertEqual(module.template_spec({"segment": segment})[0], "Ref_05_02_Video_OpenRouter.md")

    def test_0502_max_sd_2_reference_video_prefers_segment_then_falls_back(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_contract_max_sd_2_reference_video",
        )
        selection = {"provider": "openrouter", "model": "bytedance/seedance-2.0"}
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            working = workspace / "S9_05_02_VideoPlanExecutor/Working"
            working.mkdir(parents=True)

            fallback = executor.prepare_max_sd_2_reference_videos(
                workspace,
                {"tasks": {"lipsync_reason": "visible_talking_head"}},
                selection,
                working,
                "dak_fallback",
                {},
            )
            self.assertEqual([path.name for path in fallback], ["Video_SDR2V.mp4"])
            self.assertGreater(fallback[0].stat().st_size, 0)

            explicit_source = workspace / "SessionInput/reference/custom_motion.mp4"
            explicit_source.parent.mkdir(parents=True)
            explicit_source.write_bytes(b"explicit-reference")
            explicit = executor.prepare_max_sd_2_reference_videos(
                workspace,
                {
                    "provider_reference_video_path": "SessionInput/reference/custom_motion.mp4",
                    "tasks": {"lipsync_reason": "visible_talking_head"},
                },
                selection,
                working,
                "dak_explicit",
                {},
            )
            self.assertEqual([path.name for path in explicit], ["dak_explicit_MaxSD2Reference.mp4"])
            self.assertEqual(explicit[0].read_bytes(), b"explicit-reference")

    def test_max_sd_2_bundled_fallback_video_is_not_longer_than_15_seconds(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_contract_max_sd_2_fallback_duration",
        )
        ffprobe = REPO_ROOT / "ToolLibrary/.bin/ffprobe"
        fallback = REPO_ROOT / executor.MAX_SD_2_REFERENCE_VIDEO_REL.removeprefix("OpenCrew/")
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
                    str(fallback),
                ],
                text=True,
            ).strip()
        )

        self.assertLessEqual(duration, executor.MAX_SD_2_REFERENCE_VIDEO_MAX_SECONDS)

    def test_0506_talking_head_existing_tail_materialization_is_privacy_gridded_before_publish(self) -> None:
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_06_contract_talking_head_tail_privacy")

        class FakePrivacyGrid:
            @staticmethod
            def prepare_continuity_frame(workspace, variables, segment, first_frame_path, working_dir, asset_key):
                self.assertEqual(segment["talking_head_reference"]["privacy_grid_manifest_path"], "SessionOutput/reference/talking_head_privacy_grid_manifest.json")
                self.assertTrue(first_frame_path.is_file())
                output = working_dir / f"{asset_key}_ContinuityFirstFrame_PrivacyGrid.png"
                output.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (576, 1024), "red").save(output)
                return output, {"grid_applied": True, "line_presence_ratio_min": 1.0}

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            executor.ensure_tool_dirs(workspace)
            storyboard = json.loads((workspace / executor.STORYBOARD_REL).read_text(encoding="utf-8"))
            storyboard["workflow_id"] = "person_talking_head_v1"
            storyboard["talking_head_config"] = {
                "max_sd_2_reference": {
                    "privacy_grid_mode": True,
                    "target_identity_grid_applied": True,
                    "privacy_grid_manifest_path": "SessionOutput/reference/talking_head_privacy_grid_manifest.json",
                }
            }
            variables = json.loads((workspace / executor.VARIABLES_REL).read_text(encoding="utf-8"))
            variables["default_video_config"] = {"provider": "openrouter", "model": "bytedance/seedance-2.0"}
            variables["talking_head"] = {
                "reference_privacy": {
                    "enabled": True,
                    "reference_privacy_mode": "red_grid_guide",
                    "apply_privacy_grid_to_target_identity_image": True,
                }
            }
            planned = workspace / "SessionOutput/storyboard/Working/dak_0001_Image_New.png"
            Image.new("RGB", (576, 1024), "navy").save(planned)
            task = {
                "asset_key": "dak_0001",
                "dialogue_asset_keys": ["dak_0001"],
                "source_segment": {
                    "dialogue_asset_keys": ["dak_0001"],
                    "talking_head_reference": {},
                    "first_frame": {"source_type": "previous_segment_tail_frame"},
                    "tasks": {
                        "need_lipsync": True,
                        "sync_mode": "lipsync",
                        "lipsync_reason": "visible_face",
                    },
                },
                "planned_outputs": {"first_frame_path": "SessionOutput/storyboard/Working/dak_0001_Image_New.png"},
            }
            result = {"created_files": [], "backups": []}

            with patch.object(executor, "talking_head_privacy_grid_module", return_value=FakePrivacyGrid()):
                output = executor.prepare_first_frame(
                    workspace,
                    SimpleNamespace(),
                    variables,
                    storyboard,
                    {},
                    task,
                    result,
                )

            self.assertEqual(output.read_bytes(), planned.read_bytes())
            self.assertTrue(result["continuity_privacy_grid"]["dak_0001"]["grid_applied"])

    def test_max_sd_2_privacy_gate_requires_model_oral_markers_and_target_toggle(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_contract_max_sd_2_privacy_gate",
        )
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
        storyboard = {
            "talking_head_config": {
                "max_sd_2_reference": {"privacy_grid_mode": True, "target_identity_grid_applied": True}
            }
        }
        oral = {"tasks": {"need_lipsync": True, "sync_mode": "lipsync", "lipsync_reason": "visible_face"}}
        cutaway = {"tasks": {"need_lipsync": False, "sync_mode": "audio_replace_retime", "lipsync_reason": "user_marked_cutaway"}}

        self.assertTrue(executor.should_apply_max_sd_2_oral_privacy_grid(variables, storyboard, oral))
        self.assertFalse(executor.should_apply_max_sd_2_oral_privacy_grid(variables, storyboard, cutaway))
        self.assertFalse(executor.should_apply_max_sd_2_oral_privacy_grid(variables, storyboard, {"tasks": {}}))
        other_model = {**variables, "default_video_config": {"provider": "openrouter", "model": "x-ai/grok-imagine-video"}}
        self.assertFalse(executor.should_apply_max_sd_2_oral_privacy_grid(other_model, storyboard, oral))
        privacy_off = json.loads(json.dumps(variables))
        privacy_off["talking_head"]["reference_privacy"]["apply_privacy_grid_to_target_identity_image"] = False
        self.assertFalse(executor.should_apply_max_sd_2_oral_privacy_grid(privacy_off, storyboard, oral))

    def test_0502_max_sd_2_oral_privacy_frame_runs_only_after_strict_gate(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_contract_max_sd_2_privacy_frame",
        )

        class FakePrivacyGrid:
            calls = 0

            @classmethod
            def prepare_continuity_frame(cls, workspace, variables, segment, source, working, asset_key):
                cls.calls += 1
                output = working / f"{asset_key}_ContinuityFirstFrame_PrivacyGrid.png"
                output.write_bytes(b"gridded")
                return output, {"grid_applied": True}

        variables = {
            "default_video_config": {"provider": "openrouter", "model": "bytedance/seedance-2.0"},
            "talking_head": {"reference_privacy": {"enabled": True, "reference_privacy_mode": "red_grid_guide", "apply_privacy_grid_to_target_identity_image": True}},
        }
        storyboard = {"talking_head_config": {"max_sd_2_reference": {"privacy_grid_mode": True, "target_identity_grid_applied": True}}}
        oral = {"tasks": {"need_lipsync": True, "sync_mode": "lipsync", "lipsync_reason": "visible_face"}}
        cutaway = {"tasks": {"need_lipsync": False, "sync_mode": "audio_replace_retime", "lipsync_reason": "cutaway"}}

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "source.png"
            source.write_bytes(b"clean")
            gridded, metadata = executor.prepare_max_sd_2_oral_privacy_frame(
                workspace, variables, storyboard, oral, source, workspace, "dak_oral", privacy_tool=FakePrivacyGrid
            )
            clean, skipped = executor.prepare_max_sd_2_oral_privacy_frame(
                workspace, variables, storyboard, cutaway, source, workspace, "dak_cutaway", privacy_tool=FakePrivacyGrid
            )
            self.assertEqual(gridded.read_bytes(), b"gridded")
            self.assertTrue(metadata["grid_applied"])
            self.assertEqual(clean, source)
            self.assertEqual(skipped, {})
        self.assertEqual(FakePrivacyGrid.calls, 1)

    def test_0502_openrouter_video_config_includes_non_secret_runtime_r2_fields(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_contract_openrouter_runtime_r2",
        )
        variables = {
            "default_video_config": {
                "provider": "openrouter",
                "model": "bytedance/seedance-2.0",
            }
        }
        base_config = {
            "provider": "openrouter",
            "model": "bytedance/seedance-2.0",
            "api_key": "openrouter-test-key",
            "public_asset_provider": "",
            "extra": {"public_asset_provider": ""},
            "extra_json": {"public_asset_provider": ""},
        }
        runtime_env = {
            "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT": "https://account.r2.cloudflarestorage.com",
            "OPENCREW_PUBLIC_ASSET_R2_BUCKET": "opencrew-public-assets",
            "OPENCREW_PUBLIC_ASSET_R2_REGION": "auto",
            "OPENCREW_PUBLIC_ASSET_R2_PREFIX": "analysis-v1/openrouter-video",
            "OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS": "600",
            "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID": "runtime-access-key",
            "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY": "runtime-secret-key",
        }

        with patch.dict(executor.os.environ, runtime_env, clear=False), patch.object(
            executor,
            "load_provider_config",
            return_value=base_config,
        ):
            config = executor.load_video_provider_config_for_segment(
                SimpleNamespace(video_provider="", video_model=""),
                variables,
                {"tasks": {"lipsync_reason": "visible_talking_head"}},
            )

        self.assertEqual(config["public_asset_provider"], "r2")
        self.assertEqual(config["r2_bucket"], "opencrew-public-assets")
        self.assertEqual(config["public_asset_config_source"], "runtime_environment")
        self.assertNotIn("runtime-access-key", json.dumps(config))
        self.assertNotIn("runtime-secret-key", json.dumps(config))

    def test_video_only_executor_preserves_public_alias_in_execution_state(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_05_contract_alias_state")
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_06_contract_alias_state")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))
            write_json(
                workspace / "SessionOutput/storyboard/video_only_plan_execution_state.json",
                {
                    "schema_version": "analysis_v1_video_only_plan_execution_state_0.1",
                    "status": "queued",
                    "job_id": "route_job_1",
                    "agentVideoAlias": "Max SD 2.0",
                },
            )

            result = executor.run(executor.parse_args(["--workspace", str(workspace), "--mode", "prompt-only", "--overwrite-prompt", "--force"]))

            self.assertEqual(result["status"], "completed")
            state = json.loads((workspace / "SessionOutput/storyboard/video_only_plan_execution_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["agentVideoAlias"], "Max SD 2.0")
            self.assertNotIn("openrouter", json.dumps(state, ensure_ascii=False))

    def test_video_only_sensitive_scan_allows_key_reference_metadata(self) -> None:
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_06_contract_sensitive_scan")

        payload = {
            "model_calls": {
                "dak_0001": {
                    "video": {
                        "provider_config": {
                            "provider": "openrouter",
                            "model": "bytedance/seedance-2.0-fast",
                            "api_key_ref": "video_openrouter_key",
                            "has_api_key": True,
                            "secret_length": 73,
                            "extra": {
                                "r2_secret_access_key_ref": "public_assets_r2_secret_access_key",
                            },
                        }
                    }
                }
            }
        }

        self.assertEqual(executor.scan_for_sensitive_output(payload), [])
        self.assertTrue(executor.scan_for_sensitive_output({"detail": "postgresql://user:pass@host/db"}))
        self.assertTrue(executor.scan_for_sensitive_output({"detail": "password=abc"}))

    def test_video_only_wan_rtv_prompt_copies_fixed_reference_video(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_05_contract_wan_rtv")
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_06_contract_wan_rtv")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            variables_path = workspace / "SessionContext/Variables.json"
            variables = json.loads(variables_path.read_text(encoding="utf-8"))
            variables["default_video_config"] = {
                "provider": "wan",
                "model": "wan2.7-r2v",
                "api_key_ref": "test-video-key",
                "has_api_key": True,
            }
            write_json(variables_path, variables)
            generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))

            result = executor.run(executor.parse_args(["--workspace", str(workspace), "--mode", "prompt-only", "--overwrite-prompt", "--force"]))

            self.assertEqual(result["status"], "completed")
            reference_video = workspace / "S13_05_06_VideoOnlyPlanExecutor/Working/Video_Wan_R2V.mp4"
            prompt_template = workspace / "S13_05_06_VideoOnlyPlanExecutor/Prompt/Ref_05_02_Video_Wan_R2V.md"
            prompt_path = workspace / "SessionOutput/storyboard/Working/dak_0001_VideoPrompt.json"
            prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
            self.assertTrue(reference_video.exists())
            self.assertGreater(reference_video.stat().st_size, 0)
            self.assertTrue(prompt_template.exists())
            self.assertEqual(prompt["provider_profile"], "video_wan_rtv")
            self.assertEqual(prompt["reference_video"], "Video_Wan_R2V.mp4")

    def test_video_only_kling_omni_prompt_copies_fixed_reference_video(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_05_contract_kling_omni")
        executor = load_module(EXECUTOR_PATH, "analysis_v1_05_06_contract_kling_omni")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            variables_path = workspace / "SessionContext/Variables.json"
            variables = json.loads(variables_path.read_text(encoding="utf-8"))
            variables["default_video_config"] = {
                "provider": "kling",
                "model": "kling-v3-omni",
                "api_key_ref": "test-video-key",
                "has_api_key": True,
                "public_asset_provider": "tmpfiles",
            }
            write_json(variables_path, variables)
            generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "task", "--force"]))

            result = executor.run(executor.parse_args(["--workspace", str(workspace), "--mode", "prompt-only", "--overwrite-prompt", "--force"]))

            self.assertEqual(result["status"], "completed")
            reference_video = workspace / "S13_05_06_VideoOnlyPlanExecutor/Working/Video_Kling_Omni.mp4"
            prompt_template = workspace / "S13_05_06_VideoOnlyPlanExecutor/Prompt/Ref_05_02_Video_Kling_Omni.md"
            prompt_path = workspace / "SessionOutput/storyboard/Working/dak_0001_VideoPrompt.json"
            prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
            self.assertTrue(reference_video.exists())
            self.assertGreater(reference_video.stat().st_size, 0)
            self.assertTrue(prompt_template.exists())
            self.assertEqual(prompt["provider_profile"], "video_kling")
            self.assertEqual(prompt["reference_video"], "Video_Kling_Omni.mp4")

    def test_generator_keeps_shot_segments_after_first_final_is_confirmed(self) -> None:
        generator = load_module(GENERATOR_PATH, "analysis_v1_05_05_contract_confirmed_split")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            working = workspace / "SessionOutput/storyboard/Working"
            final_rel = "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4"
            (workspace / final_rel).write_bytes(b"fake-final-video")
            dialogues = [
                ("dak_0001", "srt_0001", "第一句", 0.0, 3.368, "SessionOutput/storyboard/Working/dak_0001_Image_01.png", final_rel),
                ("dak_0003_01", "srt_0003_01", "第二句", 3.368, 6.624, "", ""),
                ("dak_0005", "srt_0005", "第三句", 6.624, 10.343, "", ""),
                ("dak_0009_01", "srt_0009_01", "第四句", 10.343, 13.927, "", ""),
                ("dak_0012", "srt_0012", "第五句", 13.927, 17.268, "", ""),
                ("dak_0014_01", "srt_0014_01", "第六句", 17.268, 20.0, "", ""),
                ("dak_0016", "srt_0016", "第七句", 20.0, 23.2, "", ""),
                ("dak_0018_02", "srt_0018_02", "第八句", 23.2, 25.0, "", ""),
                ("dak_0020_01", "srt_0020_01", "第九句", 25.0, 27.248, "", ""),
            ]
            for asset_key, _srt_id, _text, _start, _end, _image, _video in dialogues:
                (working / f"{asset_key}_Audio_Final.wav").write_bytes(b"fake-wav")
            (working / "dak_0001_Image_01.png").write_bytes(b"fake-png")
            write_json(
                workspace / "SessionOutput/storyboard/srt_storyboard.json",
                {
                    "schema_version": "analysis_v1_srt_storyboard_0.2",
                    "shots": [
                        {
                            "shot_id": "shot_001",
                            "scenes": [
                                {
                                    "scene_id": "scene_001",
                                    "start": 0.0,
                                    "end": 27.248,
                                    "duration": 27.248,
                                    "dialogue_items": [
                                        {
                                            "srt_id": srt_id,
                                            "dialogue_asset_key": asset_key,
                                            "dialogue": dialogue_text,
                                            "start": start,
                                            "end": end,
                                            "duration": round(end - start, 3),
                                            "image_path": image_path,
                                            "working_assets": {
                                                "audio": {"slot": "Audio_Final", "source_type": "generated", "path": f"SessionOutput/storyboard/Working/{asset_key}_Audio_Final.wav"},
                                                "images": [
                                                    {"slot": "Image_01", "source_type": "generated" if image_path else "", "path": image_path},
                                                    {"slot": "Image_02", "source_type": "", "path": ""},
                                                ],
                                                "video": {"slot": "Video_Final", "source_type": "generated" if video_path else "", "path": video_path},
                                            },
                                        }
                                        for asset_key, srt_id, dialogue_text, start, end, image_path, video_path in dialogues
                                    ],
                                }
                            ],
                        }
                    ],
                },
            )

            result = generator.run(generator.parse_args(["--workspace", str(workspace), "--target-type", "shot", "--shot-id", "shot_001", "--max-video-seconds", "15", "--force"]))

            self.assertEqual(result["status"], "completed")
            plan = json.loads((workspace / "SessionOutput/storyboard/video_only_generation_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["summary"]["total_tasks"], 3)
            self.assertEqual([item["asset_key"] for item in plan["video_only_tasks"]], ["dak_0001", "dak_0003_01", "dak_0014_01"])
            self.assertEqual(plan["video_only_tasks"][0]["status"], "final_completed")
            self.assertEqual(plan["video_only_tasks"][1]["status"], "planned_prompt_and_video")
            self.assertEqual(plan["video_only_tasks"][1]["source_segment"]["first_frame"]["source_type"], "previous_segment_tail_frame")
            self.assertEqual(plan["video_only_tasks"][2]["status"], "planned_prompt_and_video")

    def test_confirm_final_binds_source_dialogue_items_working_video(self) -> None:
        for path in (BACKEND_PATH, MAIN_BACKEND_PATH):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from fastapi import APIRouter, HTTPException
        from opcrew_backend.koubo.koubo_storyboard import video_only_plan_routes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            raw_rel = "SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4"
            raw_path = workspace / raw_rel
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg = REPO_ROOT / "ToolLibrary" / ".bin" / "ffmpeg"
            subprocess.run(
                [
                    str(ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=16x16:d=1",
                    "-pix_fmt",
                    "yuv420p",
                    str(raw_path),
                ],
                check=True,
            )
            write_json(
                workspace / "SessionOutput/storyboard/video_only_generation_plan.json",
                {
                    "schema_version": "analysis_v1_video_only_generation_plan_0.1",
                    "plan_hash": "test-plan",
                    "video_only_tasks": [
                        {
                            "asset_key": "dak_0001",
                            "planned_outputs": {
                                "raw_video_path": raw_rel,
                                "final_video_path": "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4",
                                "tail_frame_path": "SessionOutput/storyboard/Working/dak_0001_TailFrame.png",
                            },
                        }
                    ],
                },
            )
            router = APIRouter()

            class Unlocked:
                def locked(self) -> bool:
                    return False

            def safe_workspace_rel(root: Path, rel_path: str):
                rel_path = str(rel_path).strip()
                target = (root / rel_path).resolve()
                if not str(target).startswith(str(root.resolve())):
                    raise HTTPException(status_code=400, detail="Unsafe path")
                return rel_path, target

            deps = SimpleNamespace(
                text=lambda value, default="": str(value if value is not None else default).strip(),
                read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {},
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                existing_working_slot_path=existing_working_slot_path,
                video_plan_with_hash=lambda payload, **_sc_kwargs: payload,
                redact_payload=lambda payload: payload,
                task_or_404=lambda task_id: {"id": task_id, "session_id": 1, "workspace_dir": str(workspace)},
                workspace_for=lambda task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                video_only_plan_execution_lock=Unlocked(),
                video_plan_execution_lock=Unlocked(),
                image_plan_execution_lock=Unlocked(),
                video_only_plan_lock=Unlocked(),
                video_plan_lock=Unlocked(),
                image_plan_lock=Unlocked(),
            )
            video_only_plan_routes.register_video_only_plan_routes(router, deps)
            endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "").endswith("/confirm-final"))

            result = asyncio.run(endpoint(task_id=1, asset_key="dak_0001"))

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "copied_raw_to_final")
            source = json.loads((workspace / "SessionOutput/storyboard/srt_storyboard.json").read_text(encoding="utf-8"))
            video = source["shots"][0]["scenes"][0]["dialogue_items"][0]["working_assets"]["video"]
            self.assertEqual(video["path"], "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4")
            self.assertEqual(video["source_type"], "generated")
            self.assertTrue((workspace / "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4").exists())
            self.assertEqual(result["tail_frame_path"], "SessionOutput/storyboard/Working/dak_0001_TailFrame.png")
            self.assertTrue((workspace / "SessionOutput/storyboard/Working/dak_0001_TailFrame.png").exists())
            self.assertGreater((workspace / "SessionOutput/storyboard/Working/dak_0001_TailFrame.png").stat().st_size, 0)

    def test_confirm_final_binds_existing_final_without_raw(self) -> None:
        for path in (BACKEND_PATH, MAIN_BACKEND_PATH):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from fastapi import APIRouter, HTTPException
        from opcrew_backend.koubo.koubo_storyboard import video_only_plan_routes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            raw_rel = "SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4"
            final_rel = "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4"
            final_path = workspace / final_rel
            final_path.parent.mkdir(parents=True, exist_ok=True)
            final_path.write_bytes(b"existing-final")
            write_json(
                workspace / "SessionOutput/storyboard/video_only_generation_plan.json",
                {
                    "schema_version": "analysis_v1_video_only_generation_plan_0.1",
                    "plan_hash": "test-plan",
                    "video_only_tasks": [
                        {
                            "asset_key": "dak_0001",
                            "planned_outputs": {
                                "raw_video_path": raw_rel,
                                "final_video_path": final_rel,
                            },
                        }
                    ],
                },
            )
            router = APIRouter()

            class Unlocked:
                def locked(self) -> bool:
                    return False

            def safe_workspace_rel(root: Path, rel_path: str):
                rel_path = str(rel_path).strip()
                target = (root / rel_path).resolve()
                if not str(target).startswith(str(root.resolve())):
                    raise HTTPException(status_code=400, detail="Unsafe path")
                return rel_path, target

            deps = SimpleNamespace(
                text=lambda value, default="": str(value if value is not None else default).strip(),
                read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {},
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                existing_working_slot_path=existing_working_slot_path,
                video_plan_with_hash=lambda payload, **_sc_kwargs: payload,
                redact_payload=lambda payload: payload,
                task_or_404=lambda task_id: {"id": task_id, "session_id": 1, "workspace_dir": str(workspace)},
                workspace_for=lambda task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                video_only_plan_execution_lock=Unlocked(),
                video_plan_execution_lock=Unlocked(),
                image_plan_execution_lock=Unlocked(),
                video_only_plan_lock=Unlocked(),
                video_plan_lock=Unlocked(),
                image_plan_lock=Unlocked(),
            )
            video_only_plan_routes.register_video_only_plan_routes(router, deps)
            endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "").endswith("/confirm-final"))

            result = asyncio.run(endpoint(task_id=1, asset_key="dak_0001"))

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "bound_existing_final")
            self.assertEqual(final_path.read_bytes(), b"existing-final")
            self.assertFalse((workspace / raw_rel).exists())
            source = json.loads((workspace / "SessionOutput/storyboard/srt_storyboard.json").read_text(encoding="utf-8"))
            video = source["shots"][0]["scenes"][0]["dialogue_items"][0]["working_assets"]["video"]
            self.assertEqual(video["path"], final_rel)
            self.assertEqual(video["source_type"], "generated")

    def test_confirm_final_uses_storyboard_bound_final_when_planned_outputs_missing(self) -> None:
        for path in (BACKEND_PATH, MAIN_BACKEND_PATH):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from fastapi import APIRouter, HTTPException
        from opcrew_backend.koubo.koubo_storyboard import video_only_plan_routes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            planned_raw_rel = "SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4"
            planned_final_rel = "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4"
            bound_final_rel = "SessionOutput/storyboard/Working/dak_0001_manual_Video_Final.mp4"
            bound_final_path = workspace / bound_final_rel
            bound_final_path.parent.mkdir(parents=True, exist_ok=True)
            bound_final_path.write_bytes(b"manual-bound-final")
            storyboard_path = workspace / "SessionOutput/storyboard/srt_storyboard.json"
            storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
            dialogue = storyboard["shots"][0]["scenes"][0]["dialogue_items"][0]
            dialogue["working_assets"] = {
                "audio": dialogue["assets"]["audio"],
                "video": {"slot": "Video_Final", "source_type": "manual", "path": bound_final_rel},
            }
            write_json(storyboard_path, storyboard)
            write_json(
                workspace / "SessionOutput/storyboard/video_only_generation_plan.json",
                {
                    "schema_version": "analysis_v1_video_only_generation_plan_0.1",
                    "plan_hash": "test-plan",
                    "video_only_tasks": [
                        {
                            "asset_key": "dak_0001",
                            "planned_outputs": {
                                "raw_video_path": planned_raw_rel,
                                "final_video_path": planned_final_rel,
                            },
                        }
                    ],
                },
            )
            router = APIRouter()

            class Unlocked:
                def locked(self) -> bool:
                    return False

            def safe_workspace_rel(root: Path, rel_path: str):
                rel_path = str(rel_path).strip()
                target = (root / rel_path).resolve()
                if not str(target).startswith(str(root.resolve())):
                    raise HTTPException(status_code=400, detail="Unsafe path")
                return rel_path, target

            deps = SimpleNamespace(
                text=lambda value, default="": str(value if value is not None else default).strip(),
                read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {},
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                existing_working_slot_path=existing_working_slot_path,
                video_plan_with_hash=lambda payload, **_sc_kwargs: payload,
                redact_payload=lambda payload: payload,
                task_or_404=lambda task_id: {"id": task_id, "session_id": 1, "workspace_dir": str(workspace)},
                workspace_for=lambda task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                video_only_plan_execution_lock=Unlocked(),
                video_plan_execution_lock=Unlocked(),
                image_plan_execution_lock=Unlocked(),
                video_only_plan_lock=Unlocked(),
                video_plan_lock=Unlocked(),
                image_plan_lock=Unlocked(),
            )
            video_only_plan_routes.register_video_only_plan_routes(router, deps)
            endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "").endswith("/confirm-final"))

            result = asyncio.run(endpoint(task_id=1, asset_key="dak_0001"))

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "bound_existing_final")
            self.assertEqual(result["final_path"], bound_final_rel)
            self.assertEqual(bound_final_path.read_bytes(), b"manual-bound-final")
            self.assertFalse((workspace / planned_raw_rel).exists())
            self.assertFalse((workspace / planned_final_rel).exists())
            source = json.loads(storyboard_path.read_text(encoding="utf-8"))
            video = source["shots"][0]["scenes"][0]["dialogue_items"][0]["working_assets"]["video"]
            self.assertEqual(video["path"], bound_final_rel)
            self.assertEqual(video["source_type"], "generated")

    def test_video_only_plan_execute_resolves_saved_video_alias_for_executor(self) -> None:
        for path in (BACKEND_PATH, MAIN_BACKEND_PATH):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from fastapi import APIRouter, HTTPException
        from opcrew_backend.koubo.koubo_storyboard import video_only_plan_routes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                workspace / "SessionOutput/storyboard/video_only_generation_plan.json",
                {
                    "schema_version": "analysis_v1_video_only_generation_plan_0.1",
                    "plan_hash": "test-plan",
                    "video_only_tasks": [
                        {
                            "video_only_task_id": "shot_001_scene_001_dak_0001_video_only",
                            "asset_key": "dak_0001",
                            "planned_outputs": {
                                "video_prompt_path": "SessionOutput/storyboard/Working/dak_0001_VideoPrompt.json",
                                "raw_video_path": "SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4",
                            },
                        }
                    ],
                },
            )
            write_json(
                workspace / "SessionContext/VideoAPISettings.json",
                {
                    "schema_version": "upload_asset_library_video_api_settings_0.1",
                    "task_id": 1,
                    "session_id": 1,
                    "settings": {
                        "agentVideoAlias": "Max SD 2.0",
                        "provider": "",
                        "model": "",
                    },
                },
            )
            router = APIRouter()
            recorded: dict[str, object] = {}
            events: list[tuple[int, str, dict]] = []

            class AsyncUnlocked:
                def locked(self) -> bool:
                    return False

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return False

            def safe_workspace_rel(root: Path, rel_path: str):
                rel_path = str(rel_path).strip()
                target = (root / rel_path).resolve()
                if not str(target).startswith(str(root.resolve())):
                    raise HTTPException(status_code=400, detail="Unsafe path")
                return rel_path, target

            async def fake_background(*args, **kwargs):
                recorded["args"] = args
                recorded["kwargs"] = kwargs

            deps = SimpleNamespace(
                ctx=SimpleNamespace(),
                text=lambda value, default="": str(value if value is not None else default).strip(),
                read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {},
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                existing_working_slot_path=existing_working_slot_path,
                video_plan_with_hash=lambda payload, **_sc_kwargs: payload,
                redact_payload=lambda payload: payload,
                task_or_404=lambda task_id: {"id": task_id, "session_id": 1, "workspace_dir": str(workspace)},
                workspace_for=lambda task: workspace,
                add_event=lambda session_id, kind, payload: events.append((session_id, kind, payload)),
                run_video_only_plan_execution_background=fake_background,
                video_only_plan_execution_jobs={},
                video_only_plan_execution_lock=AsyncUnlocked(),
                video_plan_execution_lock=AsyncUnlocked(),
                image_plan_execution_lock=AsyncUnlocked(),
                video_only_plan_lock=AsyncUnlocked(),
                video_plan_lock=AsyncUnlocked(),
                image_plan_lock=AsyncUnlocked(),
            )
            with patch.object(video_only_plan_routes, "load_agent_model_aliases", return_value=[
                {"alias": "Max SD 2.0", "provider": "openrouter", "model": "bytedance/seedance-2.0"}
            ]):
                video_only_plan_routes.register_video_only_plan_routes(router, deps)
                endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/video-only-plan/execute")

                async def run_endpoint():
                    result = await endpoint(task_id=1, request_payload={"mode": "video-only"})
                    await asyncio.sleep(0)
                    return result

                result = asyncio.run(run_endpoint())

            self.assertTrue(result["ok"])
            self.assertEqual(result["agentVideoAlias"], "Max SD 2.0")
            self.assertNotIn("openrouter", json.dumps(result, ensure_ascii=False))
            self.assertNotIn("seedance", json.dumps(result, ensure_ascii=False))
            model_selection = recorded["args"][8]
            self.assertEqual(model_selection["agentVideoAlias"], "Max SD 2.0")
            self.assertEqual(model_selection["video_provider"], "openrouter")
            self.assertEqual(model_selection["video_model"], "bytedance/seedance-2.0")
            state = json.loads((workspace / "SessionOutput/storyboard/video_only_plan_execution_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["agentVideoAlias"], "Max SD 2.0")
            self.assertNotIn("openrouter", json.dumps(state, ensure_ascii=False))
            self.assertEqual(events[-1][2]["agentVideoAlias"], "Max SD 2.0")
            self.assertNotIn("openrouter", json.dumps(events[-1][2], ensure_ascii=False))

    def test_video_only_plan_execute_ignores_stale_saved_video_alias(self) -> None:
        for path in (BACKEND_PATH, MAIN_BACKEND_PATH):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from fastapi import APIRouter, HTTPException
        from opcrew_backend.koubo.koubo_storyboard import video_only_plan_routes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                workspace / "SessionOutput/storyboard/video_only_generation_plan.json",
                {
                    "schema_version": "analysis_v1_video_only_generation_plan_0.1",
                    "plan_hash": "test-plan",
                    "video_only_tasks": [
                        {
                            "video_only_task_id": "shot_001_scene_001_dak_0001_video_only",
                            "asset_key": "dak_0001",
                            "planned_outputs": {
                                "video_prompt_path": "SessionOutput/storyboard/Working/dak_0001_VideoPrompt.json",
                                "raw_video_path": "SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4",
                            },
                        }
                    ],
                },
            )
            write_json(
                workspace / "SessionContext/VideoAPISettings.json",
                {
                    "schema_version": "upload_asset_library_video_api_settings_0.1",
                    "settings": {
                        "agentVideoAlias": "Deleted Alias",
                        "provider": "",
                        "model": "",
                    },
                },
            )
            router = APIRouter()
            recorded: dict[str, object] = {}

            class AsyncUnlocked:
                def locked(self) -> bool:
                    return False

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return False

            def safe_workspace_rel(root: Path, rel_path: str):
                rel_path = str(rel_path).strip()
                target = (root / rel_path).resolve()
                if not str(target).startswith(str(root.resolve())):
                    raise HTTPException(status_code=400, detail="Unsafe path")
                return rel_path, target

            async def fake_background(*args, **kwargs):
                recorded["args"] = args
                recorded["kwargs"] = kwargs

            deps = SimpleNamespace(
                ctx=SimpleNamespace(),
                text=lambda value, default="": str(value if value is not None else default).strip(),
                read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {},
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                existing_working_slot_path=existing_working_slot_path,
                video_plan_with_hash=lambda payload, **_sc_kwargs: payload,
                redact_payload=lambda payload: payload,
                task_or_404=lambda task_id: {"id": task_id, "session_id": 1, "workspace_dir": str(workspace)},
                workspace_for=lambda task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                run_video_only_plan_execution_background=fake_background,
                video_only_plan_execution_jobs={},
                video_only_plan_execution_lock=AsyncUnlocked(),
                video_plan_execution_lock=AsyncUnlocked(),
                image_plan_execution_lock=AsyncUnlocked(),
                video_only_plan_lock=AsyncUnlocked(),
                video_plan_lock=AsyncUnlocked(),
                image_plan_lock=AsyncUnlocked(),
            )
            with (
                patch.object(video_only_plan_routes, "load_agent_model_aliases", return_value=[]),
                patch.object(video_only_plan_routes, "load_config", return_value={"providers": []}),
            ):
                video_only_plan_routes.register_video_only_plan_routes(router, deps)
                endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/video-only-plan/execute")

                async def run_endpoint():
                    result = await endpoint(task_id=1, request_payload={"mode": "video-only"})
                    await asyncio.sleep(0)
                    return result

                result = asyncio.run(run_endpoint())

            self.assertTrue(result["ok"])
            self.assertNotIn("agentVideoAlias", result)
            self.assertEqual(recorded["args"][8], {})
            state = json.loads((workspace / "SessionOutput/storyboard/video_only_plan_execution_state.json").read_text(encoding="utf-8"))
            self.assertNotIn("agentVideoAlias", state)

    def test_video_only_execution_payload_normalizes_legacy_sensitive_output_false_failure(self) -> None:
        for path in (BACKEND_PATH, MAIN_BACKEND_PATH):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from fastapi import APIRouter, HTTPException
        from opcrew_backend.koubo.koubo_storyboard import video_only_plan_routes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            raw_rel = "SessionOutput/storyboard/Working/dak_0001_Video_Raw.mp4"
            (workspace / raw_rel).write_bytes(b"fake-video")
            write_json(
                workspace / "SessionOutput/storyboard/video_only_generation_plan.json",
                {
                    "schema_version": "analysis_v1_video_only_generation_plan_0.1",
                    "plan_hash": "test-plan",
                    "video_only_tasks": [
                        {
                            "video_only_task_id": "shot_001_scene_001_dak_0001_video_only",
                            "asset_key": "dak_0001",
                            "planned_outputs": {
                                "raw_video_path": raw_rel,
                            },
                        }
                    ],
                },
            )
            write_json(
                workspace / "SessionContext/VideoAPISettings.json",
                {
                    "schema_version": "upload_asset_library_video_api_settings_0.1",
                    "task_id": 1,
                    "session_id": 1,
                    "settings": {
                        "agentVideoAlias": "Max SD 2.0",
                        "provider": "",
                        "model": "",
                    },
                },
            )
            write_json(
                workspace / "SessionOutput/storyboard/video_only_plan_execution_state.json",
                {
                    "schema_version": "analysis_v1_video_only_plan_execution_state_0.1",
                    "job_id": "vop_exec_old",
                    "status": "failed",
                    "source_plan_hash": "test-plan",
                    "error": [{"code": "sensitive_output_detected", "message": "Sensitive-looking content detected in tool output."}],
                    "summary": {"completed_count": 1, "failed_count": 0, "blocked_count": 0},
                    "returncode": 1,
                    "tool_status": "failed",
                },
            )
            write_json(
                workspace / "SessionOutput/storyboard/video_only_plan_execution_result.json",
                {
                    "status": "completed",
                    "source_plan_hash": "test-plan",
                    "video_only_plan_hash": "test-plan",
                    "summary": {"completed_count": 1, "failed_count": 0, "blocked_count": 0},
                    "model_calls": {
                        "dak_0001": {
                            "video": {
                                "config": {
                                    "provider": "openrouter",
                                    "model": "bytedance/seedance-2.0",
                                    "api_key_ref": "video_openrouter_key",
                                }
                            }
                        }
                    },
                },
            )
            router = APIRouter()

            class Unlocked:
                def locked(self) -> bool:
                    return False

            def safe_workspace_rel(root: Path, rel_path: str):
                rel_path = str(rel_path).strip()
                target = (root / rel_path).resolve()
                if not str(target).startswith(str(root.resolve())):
                    raise HTTPException(status_code=400, detail="Unsafe path")
                return rel_path, target

            deps = SimpleNamespace(
                ctx=SimpleNamespace(),
                text=lambda value, default="": str(value if value is not None else default).strip(),
                read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {},
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                existing_working_slot_path=existing_working_slot_path,
                video_plan_with_hash=lambda payload, **_sc_kwargs: payload,
                redact_payload=lambda payload: json.loads(json.dumps(payload).replace("openrouter", "").replace("bytedance/seedance-2.0", "")),
                task_or_404=lambda task_id: {"id": task_id, "session_id": 1, "workspace_dir": str(workspace)},
                workspace_for=lambda task: workspace,
                add_event=lambda *_args, **_kwargs: None,
                video_only_plan_execution_lock=Unlocked(),
                video_plan_execution_lock=Unlocked(),
                image_plan_execution_lock=Unlocked(),
                video_only_plan_lock=Unlocked(),
                video_plan_lock=Unlocked(),
                image_plan_lock=Unlocked(),
            )
            with patch.object(video_only_plan_routes, "load_agent_model_aliases", return_value=[
                {"alias": "Max SD 2.0", "provider": "openrouter", "model": "bytedance/seedance-2.0"}
            ]):
                video_only_plan_routes.register_video_only_plan_routes(router, deps)
                endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/koubo-storyboard/tasks/{task_id}/video-only-plan/execution")

                result = asyncio.run(endpoint(task_id=1))

            state = result["execution_state"]
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["error"], "")
            self.assertEqual(state["returncode"], 0)
            self.assertEqual(state["tool_status"], "completed")
            self.assertEqual(state["agentVideoAlias"], "Max SD 2.0")
            self.assertNotIn("openrouter", json.dumps(result, ensure_ascii=False))
            self.assertNotIn("seedance", json.dumps(result, ensure_ascii=False))

    def test_video_only_plan_execution_tool_uses_variables_instead_of_cli_model_selection(self) -> None:
        for path in (BACKEND_PATH, MAIN_BACKEND_PATH):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from opcrew_backend.koubo.koubo_storyboard import tool_runner_services

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            calls: list[list[str]] = []

            def fake_run(cmd, **_kwargs):
                calls.append(list(cmd))
                return SimpleNamespace(stdout='{"status":"completed"}', stderr="", returncode=0)

            with patch.object(tool_runner_services.subprocess, "run", side_effect=fake_run):
                result, _stdout, _stderr, returncode = tool_runner_services.run_video_only_plan_execution_tool(
                    workspace,
                    "video-only",
                    "plan-hash",
                    "task-1",
                    "dak_0001",
                    {"video_provider": "openrouter", "video_model": "bytedance/seedance-2.0"},
                    sc=SimpleNamespace(ctx=None),
                )

            self.assertEqual(returncode, 0)
            self.assertEqual(result["status"], "completed")
            command = calls[0]
            self.assertNotIn("--video-provider", command)
            self.assertNotIn("openrouter", command)
            self.assertNotIn("--video-model", command)
            self.assertNotIn("bytedance/seedance-2.0", command)


if __name__ == "__main__":
    unittest.main()
