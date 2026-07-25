from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.koubo.talking_head_models import (  # noqa: E402
    resolve_talking_head_video_model,
    talking_head_video_model_key,
)
from opcrew_backend.model_leakage_guard import _sanitize_customer_value  # noqa: E402


TASK_ROUTER_PATH = BACKEND_ROOT / "opcrew_backend" / "koubo" / "task_list_router.py"
CREATE_MODAL_PATH = ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboTaskList" / "KouboTaskCreateFromScriptModal.jsx"
TALKING_HEAD_MODULE_PATH = ROOT / "frontend" / "src" / "modules" / "koubo" / "TalkingHeadV1" / "TalkingHeadV1Module.jsx"
PREPARE_PATH = ROOT / "ToolLibrary" / "TalkingHead_V1" / "00_PrepareSessionVariables.py"
STORYBOARD_CONFIG_PATH = ROOT / "ToolLibrary" / "TalkingHead_V1" / "03_StoryBoardConfig.py"
VIDEO_PLAN_PATH = ROOT / "ToolLibrary" / "Analysis_V1" / "05_01_VideoPlanGenerator.py"
VIDEO_PLAN_MODAL_PATH = ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboVideoPlanModal.jsx"
ROUTER_PATH = BACKEND_ROOT / "opcrew_backend" / "koubo" / "router.py"
COMPOSER_PATH = ROOT / "ToolLibrary" / "TalkingHead_V1" / "06_01_VideoPlanComposer.py"
EXECUTOR_PATH = ROOT / "ToolLibrary" / "TalkingHead_V1" / "05_02_VideoPlanExecutor.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TalkingHeadRegressionFixesContractTest(unittest.TestCase):
    def test_partial_video_executor_result_returns_failure_exit_code(self) -> None:
        module = load_module("talking_head_executor_partial_exit_contract", EXECUTOR_PATH)
        with patch.object(module, "run", return_value={"status": "completed_with_failed_items"}):
            self.assertEqual(module.main([]), 1)

    def test_composer_blocks_failed_video_execution_before_removing_previous_outputs(self) -> None:
        module = load_module("talking_head_composer_failed_segment_guard", COMPOSER_PATH)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            result_path = workspace / module.EXECUTION_RESULT_REL
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                '{"status":"completed_with_failed_items","summary":{"failed_count":1}}',
                encoding="utf-8",
            )

            with self.assertRaises(module.BlockedError) as raised:
                module.validate_video_execution_complete(workspace)

        self.assertEqual(raised.exception.code, "video_plan_has_failed_segments")
        self.assertIn("已停止合成", raised.exception.message)

    def test_composer_accepts_completed_video_execution(self) -> None:
        module = load_module("talking_head_composer_completed_segment_guard", COMPOSER_PATH)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            result_path = workspace / module.EXECUTION_RESULT_REL
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                '{"status":"completed","summary":{"failed_count":0}}',
                encoding="utf-8",
            )

            self.assertIsNone(module.validate_video_execution_complete(workspace))

    def test_talking_head_video_plan_does_not_require_generic_consistency_boards(self) -> None:
        module = load_module("talking_head_regression_video_plan", VIDEO_PLAN_PATH)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            image_rel = "SessionOutput/storyboard/Working/dialogue_001_Image_New.png"
            image_path = workspace / image_rel
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"image")
            storyboard = {
                "workflow_id": "person_talking_head_v1",
                "shots": [{
                    "shot_id": "shot_001",
                    "scenes": [{
                        "scene_id": "scene_001",
                        "start": 0,
                        "end": 3,
                        "dialogue_items": [{
                            "dialogue_asset_key": "dialogue_001",
                            "srt_id": "srt_001",
                            "start": 0,
                            "end": 3,
                            "duration": 3,
                            "dialogue": "测试口播",
                            "image_path": image_rel,
                            "segment_audio_path": "SessionOutput/storyboard/Working/dialogue_001_SegmentAudio_Final.wav",
                            "video_plan": {"is_talking_head": True},
                        }],
                    }],
                }],
            }
            args = module.Args(
                workspace=str(workspace),
                target_type="task",
                shot_id="",
                scene_id="",
                max_video_seconds=15.0,
                min_video_seconds=2.0,
                split_tolerance_seconds=0.0,
                force=False,
                resume=False,
                print_json=False,
            )

            plan = module.build_plan(workspace, args, storyboard)

        self.assertEqual(plan["workflow_id"], "person_talking_head_v1")
        self.assertEqual(plan["consistency_references"]["status"], "not_required")
        self.assertEqual(plan["consistency_references"]["missing"], [])

    def test_video_plan_errors_render_structured_reasons_and_one_click_refreshes_defaults(self) -> None:
        modal_source = VIDEO_PLAN_MODAL_PATH.read_text(encoding="utf-8")
        router_source = ROUTER_PATH.read_text(encoding="utf-8")

        self.assertIn("return reasonText(value)", modal_source)
        self.assertIn("talking_head_session_video_config_ready", router_source)
        self.assertIn("run_session_variables_prepare_00(task, force=True)", router_source)

    def test_video_model_public_key_survives_customer_sanitization(self) -> None:
        selected = resolve_talking_head_video_model(model_key="max_2_7_w")
        self.assertIsNotNone(selected)
        self.assertEqual(selected["provider"], "wan")
        self.assertEqual(selected["model"], "wan2.7-r2v")
        self.assertEqual(talking_head_video_model_key({"provider": "wan", "model": "wan2.7-r2v"}), "max_2_7_w")
        sanitized = _sanitize_customer_value({"talking_head": {"video_model": selected}})
        public_model = sanitized["talking_head"]["video_model"]
        self.assertEqual(public_model["model_key"], "max_2_7_w")
        self.assertEqual(public_model["model_alias"], "Max 2.7 W")
        self.assertEqual(public_model["provider"], "")
        self.assertEqual(public_model["model"], "")

    def test_customer_frontend_contains_only_public_talking_head_model_keys(self) -> None:
        modal_source = CREATE_MODAL_PATH.read_text(encoding="utf-8")
        module_source = TALKING_HEAD_MODULE_PATH.read_text(encoding="utf-8")
        for internal_id in ("grok-imagine-video", "wan2.7-r2v"):
            self.assertNotIn(internal_id, modal_source)
            self.assertNotIn(internal_id, module_source)
        self.assertIn('talking_head_video_model_key: "max_1_5_x"', modal_source)
        self.assertNotIn('video_provider: selectedVideoModel', module_source)

    def test_one_click_audio_label_follows_0501_lipsync_contract(self) -> None:
        module_source = TALKING_HEAD_MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn("function oneClickMovieSyncLabel(segment)", module_source)
        self.assertIn('segment?.lipsync?.need_lipsync === false || syncMode === "audio_replace_retime"', module_source)
        self.assertIn('? "音频合成"', module_source)
        self.assertIn(': "音频匹配"', module_source)
        self.assertIn("syncLabel={oneClickMovieSyncLabel}", module_source)

    def test_one_click_video_resume_actions_are_wired(self) -> None:
        module_source = TALKING_HEAD_MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn('resume: Boolean(options.resume)', module_source)
        self.assertIn('run_only_step_id: String(options.run_only_step_id || "")', module_source)
        self.assertIn('run_from_step_id: String(options.run_from_step_id || (options.run_only_step_id ? "" : (hasStoryboard ? "05_01" : "00")))', module_source)
        self.assertIn('onResumeVideoStep={() => void startOneClickMovie({ force: false, resume: true, run_only_step_id: "05_02" })}', module_source)
        self.assertIn('onResumeVideoStepAndFollowing={() => void startOneClickMovie({ force: false, resume: true, run_from_step_id: "05_02" })}', module_source)

    def test_one_click_movie_preview_url_is_stable_during_polling(self) -> None:
        module_source = TALKING_HEAD_MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn('const cacheKey = latestMovieRun()?.run_id || output;', module_source)
        self.assertNotIn('latestMovieRun()?.updated_at || latestMovieRun()?.finished_at', module_source)

    def test_cosyvoice_audio_dispatch_uses_shared_synthesizer(self) -> None:
        module = load_module("talking_head_regression_storyboard_config", STORYBOARD_CONFIG_PATH)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"

            def fake_convert(source: Path, target: Path, tempo: float = 1.0) -> None:
                self.assertEqual(tempo, 1.25)
                target.write_bytes(source.read_bytes())

            with (
                patch.object(module, "load_voice_clone_config", return_value={"provider": "cosyvoice", "model": "cosyvoice-v3.5-flash", "api_key": "secret", "api_key_ref": "ref", "source": "test", "extra": {}}),
                patch("opcrew_backend.routes.media_model_config.dashscope_cosyvoice_tts_audio_bytes", return_value=b"RIFF-test") as synthesize,
                patch.object(module, "convert_audio_to_wav", side_effect=fake_convert),
                patch.object(module, "audio_duration_seconds", return_value=2.5),
            ):
                result = module.generate_clone_audio("cosyvoice", "测试文本", "voice-1", 1.25, output)

            synthesize.assert_called_once()
            self.assertEqual(result["provider"], "cosyvoice")
            self.assertEqual(result["duration_seconds"], 2.5)
            self.assertTrue(output.is_file())

    def test_minimax_audio_dispatch_uses_selected_clone_voice(self) -> None:
        module = load_module("talking_head_regression_storyboard_config_minimax", STORYBOARD_CONFIG_PATH)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"

            def fake_download(url: str, target: Path) -> str:
                self.assertEqual(url, "https://audio.example/test.mp3")
                target.write_bytes(b"audio")
                return "audio/mpeg"

            def fake_convert(source: Path, target: Path, tempo: float = 1.0) -> None:
                self.assertEqual(tempo, 1.0)
                target.write_bytes(source.read_bytes())

            with (
                patch.object(module, "load_voice_clone_config", return_value={"provider": "minimax", "model": "minimax-voice-clone", "api_key": "secret", "api_key_ref": "ref", "source": "test", "extra": {"group_id": "group", "tts_model": "speech-02-hd"}}),
                patch.object(module, "http_json", return_value={"base_resp": {"status_code": 0}, "data": {"audio": "https://audio.example/test.mp3"}}) as request_audio,
                patch.object(module, "download_binary", side_effect=fake_download),
                patch.object(module, "convert_audio_to_wav", side_effect=fake_convert),
                patch.object(module, "audio_duration_seconds", return_value=3.0),
            ):
                result = module.generate_clone_audio("minimax", "测试文本", "voice-mini", 1.2, output)

            request_payload = request_audio.call_args.args[1]
            self.assertEqual(request_payload["voice_setting"]["voice_id"], "voice-mini")
            self.assertEqual(request_payload["voice_setting"]["speed"], 1.2)
            self.assertEqual(result["provider"], "minimax")

    def test_upload_create_and_update_have_rollback_contracts(self) -> None:
        source = TASK_ROUTER_PATH.read_text(encoding="utf-8")
        create = source.split('@router.post("/api/koubo-tasks/create-talking-head")', 1)[1].split('@router.put("/api/koubo-tasks/{task_id}/talking-head")', 1)[0]
        update = source.split('@router.put("/api/koubo-tasks/{task_id}/talking-head")', 1)[1].split('@router.put("/api/koubo-tasks/{task_id}/script")', 1)[0]
        self.assertLess(create.index("save_talking_head_portrait(staging_root"), create.index("ctx.session_repo.create("))
        self.assertIn("rollback_talking_head_create(session_id, workspace, opencode_session_id)", create)
        self.assertIn("safe_unlink_upload(workspace, installed_portrait_rel", update)
        self.assertIn("safe_unlink_upload(workspace, old_portrait_rel", update)
        self.assertIn("safe_unlink_upload(workspace, old_reference_rel", update)

    def test_prepare_loads_selected_voice_provider_config(self) -> None:
        source = PREPARE_PATH.read_text(encoding="utf-8")
        self.assertIn('fetch_media_public_config(database_url, "voice-clone", voice_provider)', source)
        self.assertNotIn('fetch_media_public_config(database_url, "voice-clone", VOICE_CLONE_PROVIDER)', source)


if __name__ == "__main__":
    unittest.main()
