from __future__ import annotations

import unittest
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
ROUTER_SOURCE = REPO_ROOT / "backend/opcrew_backend/koubo/router.py"
SCHEMAS_SOURCE = REPO_ROOT / "backend/opcrew_backend/koubo/schemas.py"
ANALYSIS_MODULE_SOURCE = REPO_ROOT / "frontend/src/modules/koubo/AnalysisV1/AnalysisV1Module.jsx"
ANALYSIS_API_SOURCE = REPO_ROOT / "frontend/src/modules/koubo/AnalysisV1/analysisV1Api.js"
DIALOG_SOURCE = REPO_ROOT / "frontend/src/modules/koubo/DanceMimicV1/OneClickMovieDialog.jsx"
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.koubo.router import (  # noqa: E402
    analysis_v1_one_click_step_succeeded,
    talking_head_one_click_prerequisite_step_ids,
    talking_head_one_click_public_error_message,
)


class AnalysisV1OneClickMovieContractTest(unittest.TestCase):
    def test_backend_exposes_independent_oral_one_click_movie_surface(self) -> None:
        router_source = ROUTER_SOURCE.read_text(encoding="utf-8")
        schemas_source = SCHEMAS_SOURCE.read_text(encoding="utf-8")

        self.assertIn("class OpenClipAnalysisV1OneClickMoviePayload", schemas_source)
        self.assertIn("analysis_v1_koubo_one_click_movie", router_source)
        self.assertIn("/api/openclip/tasks/{task_id}/analysis-v1/one-click-movie", router_source)
        self.assertIn("analysis_v1_one_click_start", router_source)
        self.assertIn("analysis_v1_one_click_status", router_source)
        self.assertIn("SessionReport/analysis_v1/one_click_movie_state.json", router_source)

    def test_backend_one_click_status_exposes_public_progress_only(self) -> None:
        router_source = ROUTER_SOURCE.read_text(encoding="utf-8")

        for token in (
            "def analysis_v1_public_message",
            "def analysis_v1_one_click_public_steps",
            '"语音模型余额不足，请充值"',
            '"音频匹配服务额度不足，请联系管理员充值后重试。"',
            '"steps": analysis_v1_one_click_public_steps(state.get("steps"), fallback_error)',
            '"summary": summary',
        ):
            self.assertIn(token, router_source)
        status_section = router_source.split("def analysis_v1_one_click_status", 1)[1].split("def analysis_v1_one_click_video_plan_settings", 1)[0]
        self.assertNotIn('"workspace_dir"', status_section)
        self.assertNotIn('"plan": state.get("plan")', status_section)

    def test_talking_head_provider_errors_are_customer_safe_and_actionable(self) -> None:
        privacy_error = (
            'HTTP 400 from https://openrouter.ai/api/v1/videos: '
            'InputImageSensitiveContentDetected.PrivacyInformation: input image may contain real person'
        )
        missing_segment = (
            "segment_video_missing: Segment final video is missing: "
            "SessionOutput/storyboard/Working/dialogue_002_Video_Final.mp4"
        )

        privacy_message = talking_head_one_click_public_error_message(privacy_error)
        missing_message = talking_head_one_click_public_error_message(missing_segment)
        model_message = talking_head_one_click_public_error_message(
            "No enabled video provider config found for bytedance/seedance-2.0"
        )
        reference_message = talking_head_one_click_public_error_message(
            "PrivacyGridError: 参考视频稳定人脸区域覆盖不足：sample=0.880, area=0.628"
        )
        video_privacy_message = talking_head_one_click_public_error_message(
            "InputVideoSensitiveContentDetected.PrivacyInformation: input video may contain real person"
        )

        self.assertIn("隐私安全检查", privacy_message)
        self.assertIn("逐句生成视频", privacy_message)
        self.assertNotIn("openrouter", privacy_message.lower())
        self.assertNotIn("seedance", privacy_message.lower())
        self.assertIn("已停止合成", missing_message)
        self.assertNotIn("dialogue_002", missing_message)
        self.assertIn("视频生成服务请求失败", model_message)
        self.assertNotIn("bytedance", model_message.lower())
        self.assertNotIn("seedance", model_message.lower())
        self.assertIn("自动扩展隐私网格", reference_message)
        self.assertNotIn("sample=", reference_message)
        self.assertIn("增强视频网格线", video_privacy_message)
        self.assertIn("逐句生成视频", video_privacy_message)

    def test_partial_video_execution_stops_before_composer(self) -> None:
        router_source = ROUTER_SOURCE.read_text(encoding="utf-8")
        executor_source = (REPO_ROOT / "ToolLibrary/TalkingHead_V1/05_02_VideoPlanExecutor.py").read_text(encoding="utf-8")

        self.assertFalse(analysis_v1_one_click_step_succeeded("05_02", 0, "completed_with_failed_items"))
        self.assertFalse(analysis_v1_one_click_step_succeeded("05_02", 1, "completed"))
        self.assertTrue(analysis_v1_one_click_step_succeeded("05_02", 0, "completed"))
        self.assertTrue(analysis_v1_one_click_step_succeeded("05_01", 0, "completed_with_warnings"))
        self.assertIn("analysis_v1_one_click_step_succeeded(step_id, completed.returncode, parsed_status)", router_source)
        self.assertIn('return 0 if result.get("status") == "completed" else 1', executor_source)

    def test_backend_orchestrates_oral_steps_without_action_mimic_forced_audio_contract(self) -> None:
        router_source = ROUTER_SOURCE.read_text(encoding="utf-8")

        for step_id in ('"00"', '"01"', '"02_01"', '"02_02"', '"03_02"', '"04_01"', '"04_03"'):
            self.assertIn(step_id, router_source)
        self.assertIn("05_01_VideoPlanGenerator.py", router_source)
        self.assertIn("05_02_VideoPlanExecutor.py", router_source)
        self.assertIn("06_01_VideoPlanComposer.py", router_source)
        self.assertIn("--execution-job-id", router_source)
        self.assertIn("--source-plan-hash", router_source)
        self.assertNotIn("--no-execute-lipsync", router_source)
        self.assertNotIn("--execute-audio-video-sync", router_source)
        self.assertIn('tasks.get("need_lipsync", True)', router_source)
        self.assertIn('"sync_mode": str(tasks.get("sync_mode")', router_source)

    def test_talking_head_video_resume_restores_server_trusted_audio_prerequisites(self) -> None:
        router_source = ROUTER_SOURCE.read_text(encoding="utf-8")

        self.assertEqual(talking_head_one_click_prerequisite_step_ids({"05_01", "05_02", "06_01"}), ["03"])
        self.assertEqual(talking_head_one_click_prerequisite_step_ids({"05_02"}), ["03", "05_01"])
        self.assertEqual(talking_head_one_click_prerequisite_step_ids({"05_02", "06_01"}), ["03", "05_01"])
        self.assertEqual(talking_head_one_click_prerequisite_step_ids({"03", "05_01", "05_02"}), [])
        self.assertEqual(talking_head_one_click_prerequisite_step_ids({"06_01"}), [])
        self.assertIn('{**spec, "talking_head_server_preflight": True}', router_source)
        self.assertIn("talking_head_one_click_prerequisite_step_ids(selected_ids)", router_source)
        self.assertIn('analysis_payload.model_copy(update={"force": False, "resume": True})', router_source)
        self.assertIn('payload.force or spec.get("talking_head_server_preflight")', router_source)
        self.assertIn('"code": "talking_head_voice_required"', router_source)
        self.assertIn('"message": "当前人物口播任务尚未选择可用音色，请选择音色并保存后再执行一键成片。"', router_source)

    def test_frontend_button_only_opens_panel_and_panel_starts_run(self) -> None:
        module_source = ANALYSIS_MODULE_SOURCE.read_text(encoding="utf-8")
        api_source = ANALYSIS_API_SOURCE.read_text(encoding="utf-8")
        dialog_source = DIALOG_SOURCE.read_text(encoding="utf-8")

        self.assertIn("analysis-v1-one-click-movie-entry", module_source)
        self.assertIn("打开口播一键成片面板", module_source)
        self.assertIn("onClick={() => void openOneClickMoviePanel()}", module_source)
        self.assertIn("async function openOneClickMoviePanel", module_source)
        self.assertIn("setOneClickMovieOpen(true)", module_source)
        self.assertIn("async function startOneClickMovie", module_source)
        self.assertIn("onStart={() => void startOneClickMovie({ force: true })}", module_source)
        self.assertIn('run_only_step_id: "05_02"', module_source)
        self.assertIn('run_from_step_id: "05_02"', module_source)
        self.assertIn("oneClickMovieSyncLabel", module_source)
        self.assertIn('import "../DanceMimicV1/danceMimicV1.css"', module_source)
        self.assertIn('"音频匹配"', module_source)
        self.assertIn('"音频合成"', module_source)
        self.assertIn("oneClickMovie: async", api_source)
        self.assertIn("oneClickMovieStatus: async", api_source)
        self.assertIn("/analysis-v1/one-click-movie", api_source)
        self.assertIn("全流程进度", dialog_source)
        self.assertIn("逐句成片状态", dialog_source)
        self.assertIn("function syncLabel", dialog_source)
        self.assertIn('title: `等待${syncLabel(segment, props)}`', dialog_source)


if __name__ == "__main__":
    unittest.main()
