from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
OPENCLIP_BACKEND = REPO_ROOT / "backend"
if str(OPENCLIP_BACKEND) not in sys.path:
    sys.path.insert(0, str(OPENCLIP_BACKEND))

ROUTER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py"
ARTIFACT_BILLING_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "analysis_v1_artifact_billing.py"
SCHEMAS_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "schemas.py"
API_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "analysisV1Api.js"
MODULE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "AnalysisV1Module.jsx"


def load_schemas_module():
    spec = importlib.util.spec_from_file_location("openclip_backend_schemas_task_indicator_contract", SCHEMAS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1TaskProcessIndicatorMvpContractTest(unittest.TestCase):
    def test_payload_supports_mvp_modes_and_optional_body_task_id(self) -> None:
        schemas = load_schemas_module()
        payload = schemas.OpenClipAnalysisV1RunPayload(mode="run_from_step", start_step_id="02_02")

        self.assertIsNone(payload.task_id)
        self.assertEqual(payload.mode, "run_from_step")
        self.assertEqual(payload.start_step_id, "02_02")
        self.assertTrue(hasattr(payload, "pause_before_step_id"))
        self.assertTrue(hasattr(payload, "previous_attempt_id"))

    def test_backend_contract_exposes_plan_indicator_controls_and_popen_runner(self) -> None:
        source = ROUTER_PATH.read_text(encoding="utf-8")
        artifact_source = ARTIFACT_BILLING_PATH.read_text(encoding="utf-8")

        for route in (
            '"/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/plan"',
            '"/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}"',
            '"/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/stop"',
            '"/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/pause-before"',
            '"/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/resume"',
            '"/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/steps/{step_id}/quick-watch"',
            '"/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/steps/{step_id}/logs"',
        ):
            self.assertIn(route, source)

        for token in (
            '"analysis_v1_tool_run"',
            '"analysis_v1.run_to_storyboard"',
            '"run_state.json"',
            "threading.RLock()",
            "SessionReport",
            "tool_runs",
            "subprocess.Popen",
            'encoding="utf-8"',
            'errors="replace"',
            "stdout_tail",
            "stderr_tail",
            "redact_analysis_v1_tail",
            "heartbeat_at",
            "ANALYSIS_V1_HEARTBEAT_STALE_MS",
            "analysis_v1_mark_stale_attempt",
            "analysis_v1_plan_dependency_block",
            "analysis_v1_metering_summary",
            "analysis_v1_empty_step_metering",
            '"by_step"',
            'step_payload["metering"]',
            '"api"',
            '"local_artifacts"',
            "analysis_v1_record_local_artifacts",
            "billing_scope",
            '"diagnostic"',
            "cancel_requested",
            "pause_before_step_id",
            '"run_from_step"',
            '"run_only_step"',
            '"rerun_failed"',
            '"rerun_from_step"',
            '"stopping"',
            '"paused"',
            '"reused"',
        ):
            self.assertIn(token, source)

        self.assertNotIn("analysis_v1.run_to_04_02", source)
        self.assertNotIn("analysis_v1_run_to_04_02", source)
        for token in (
            "artifact_billable",
            "billable_outputs",
            "idempotency_key",
            "modality <> 'local_artifact'",
            "provider_not_billable",
            "api_usage_already_metered",
            "deduped",
        ):
            self.assertIn(token, artifact_source)

    def test_frontend_contract_exposes_chinese_mvp_controls(self) -> None:
        api_source = API_PATH.read_text(encoding="utf-8")
        module_source = MODULE_PATH.read_text(encoding="utf-8")

        for token in (
            "runToStoryBoardPlan",
            "stopRunToStoryBoard",
            "pauseBeforeRunToStoryBoard",
            "cancelPauseBeforeRunToStoryBoard",
            "resumeRunToStoryBoard",
            "runToStoryBoardQuickWatch",
            "runToStoryBoardLogs",
        ):
            self.assertIn(token, api_source)

        # Run-menu was redesigned after this contract was written: the old grouped
        # dropdown ("运行..." with 范围运行/从某步开始/单独运行某步/从失败步骤重跑/全量重跑/继续运行)
        # was replaced by icon controls ("运行设置", "单步运行") while the per-step run
        # capabilities below (开始运行/全量运行/运行至此步/从此步开始运行/重跑此步及后续/
        # 运行到此步前暂停/当前步骤结束后停止) remain. "打开结果" -> "查看详情", and the
        # metering empty-state "本步暂无计费" was folded into the longer message below.
        for token in (
            "运行设置",
            "进入任务",
            "开始运行",
            "全量运行",
            "运行至此步",
            "从此步开始运行",
            "重跑此步及后续",
            "运行到此步前暂停",
            "查看详情",
            "当前步骤结束后停止",
            "取消暂停点",
            "概览",
            "计费",
            "参数",
            "命令",
            "文件",
            "日志",
            "本步骤暂无 API 或本地产物计费记录",
            "StepMeteringBreakdown",
        ):
            self.assertIn(token, module_source)
        self.assertNotIn("重新运行", module_source)


if __name__ == "__main__":
    unittest.main()
