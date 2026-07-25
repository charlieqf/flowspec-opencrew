from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.db.schema import metadata, openclip_tasks, sessions, talking_head_task_configs  # noqa: E402
from opcrew_backend.workflow_modes import (  # noqa: E402
    WORKFLOW_ANALYSIS_V1,
    WORKFLOW_DANCE_MIMIC_V1,
    WORKFLOW_PERSON_TALKING_HEAD_V1,
    WORKFLOW_SCRIPT,
    normalize_openclip_workflow_mode,
)
from opcrew_backend.koubo.koubo_storyboard.tool_runner_services import storyboard_workflow_id, video_plan_execution_script_path  # noqa: E402


PREPARE_PATH = REPO_ROOT / "ToolLibrary" / "TalkingHead_V1" / "00_PrepareSessionVariables.py"
REWRITE_PATH = REPO_ROOT / "ToolLibrary" / "TalkingHead_V1" / "04_01_SRTRewrite.py"
TASK_ROUTER_PATH = BACKEND_ROOT / "opcrew_backend" / "koubo" / "task_list_router.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TalkingHeadParameterStorageContractTest(unittest.TestCase):
    def test_workflow_mode_extension_does_not_change_script_dance_or_analysis(self) -> None:
        self.assertEqual(normalize_openclip_workflow_mode("script"), WORKFLOW_SCRIPT)
        self.assertEqual(normalize_openclip_workflow_mode("dance_mimic_v1"), WORKFLOW_DANCE_MIMIC_V1)
        self.assertEqual(normalize_openclip_workflow_mode("analysis_v1"), WORKFLOW_ANALYSIS_V1)
        self.assertEqual(normalize_openclip_workflow_mode("person_talking_head_v1"), WORKFLOW_PERSON_TALKING_HEAD_V1)

    def test_talking_head_config_isolated_one_to_one_storage(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(sessions.insert().values(
                id=1,
                source="openclip-analysis",
                group_id="openclip-analysis",
                sender_name="人物口播",
                title="人物口播任务",
                command_text="",
                status="draft",
                workspace_dir="/tmp/talking-head-contract",
                share_token="token",
                created_at=1,
                updated_at=1,
            ))
            conn.execute(openclip_tasks.insert().values(
                id=2,
                session_id=1,
                status="draft",
                workflow_mode=WORKFLOW_PERSON_TALKING_HEAD_V1,
                created_at=1,
                updated_at=1,
            ))
        config = {
            "schema_version": "talking_head_task_config_1.0",
            "script_input": {"reference_script_text": "参考脚本"},
            "business_context": {"industry": "医美"},
            "script_prompt": {"final_prompt": "改写提示词"},
            "talking_head": {
                "voice_timing": {
                    "voice_id": "voice_a",
                    "tempo": 1.2,
                    "tempo_by_voice_id": {"voice_a": 1.2, "voice_b": 0.9},
                }
            },
        }
        with engine.begin() as conn:
            conn.execute(talking_head_task_configs.insert().values(
                task_id=2,
                schema_version="talking_head_task_config_1.0",
                script_creation_mode="ai_rewrite",
                config_json=json.dumps(config, ensure_ascii=False),
                created_at=1,
                updated_at=2,
            ))
            stored = dict(conn.execute(talking_head_task_configs.select()).mappings().one())
        self.assertEqual(stored["script_creation_mode"], "ai_rewrite")
        self.assertEqual(json.loads(stored["config_json"])["script_input"]["reference_script_text"], "参考脚本")
        self.assertEqual(json.loads(stored["config_json"])["talking_head"]["voice_timing"]["tempo_by_voice_id"]["voice_b"], 0.9)

    def test_save_and_prepare_sources_enforce_database_then_variables_boundary(self) -> None:
        router_source = TASK_ROUTER_PATH.read_text(encoding="utf-8")
        prepare_source = PREPARE_PATH.read_text(encoding="utf-8")
        rewrite_source = REWRITE_PATH.read_text(encoding="utf-8")
        talking_head_create = router_source.split('@router.post("/api/koubo-tasks/create-talking-head")', 1)[1].split('@router.put("/api/koubo-tasks/{task_id}/talking-head")', 1)[0]
        talking_head_update = router_source.split('@router.put("/api/koubo-tasks/{task_id}/talking-head")', 1)[1].split('@router.put("/api/koubo-tasks/{task_id}/script")', 1)[0]

        self.assertIn("create_talking_head_task", talking_head_create)
        self.assertIn("update_talking_head_task", talking_head_update)
        self.assertIn('"tempo_by_voice_id": tempo_by_voice_id', router_source)
        self.assertNotIn("write_json(workspace / VARIABLES_REL", talking_head_create)
        self.assertNotIn("write_json(workspace / TASK_META_REL", talking_head_create)
        self.assertNotIn("write_json(workspace / VARIABLES_REL", talking_head_update)
        self.assertNotIn("write_json(workspace / TASK_META_REL", talking_head_update)
        self.assertIn("fetch_talking_head_task_config", prepare_source)
        self.assertIn("JOIN talking_head_task_configs", prepare_source)
        self.assertIn("s.opencode_session_id", prepare_source)
        self.assertIn('"opencode_session_id": text_value(task.get("opencode_session_id"))', prepare_source)
        self.assertNotIn("task_meta.json", prepare_source)
        self.assertNotIn("task_meta.json", rewrite_source)
        self.assertIn('if key != "video_model"', prepare_source)
        self.assertIn('"default_video_config": talking_head_video_config', prepare_source)
        self.assertNotIn('"default_video_provider":', prepare_source)
        self.assertNotIn('"default_video_model":', prepare_source)

    def test_talking_head_uploads_are_streamed_and_size_limited(self) -> None:
        router_source = TASK_ROUTER_PATH.read_text(encoding="utf-8")
        upload_helpers = router_source.split("async def save_bounded_upload", 1)[1].split("def safe_unlink", 1)[0]

        self.assertIn("await upload.read(UPLOAD_CHUNK_BYTES)", upload_helpers)
        self.assertIn("total > MAX_UPLOAD_BYTES", upload_helpers)
        self.assertIn("status_code=413", upload_helpers)
        self.assertIn("temp_target.replace(target)", upload_helpers)
        self.assertNotIn("await file.read()", upload_helpers)

    def test_storyboard_keeps_analysis_0502_while_one_click_and_prompt_reload_may_use_talking_head(self) -> None:
        constants_source = (BACKEND_ROOT / "opcrew_backend" / "koubo" / "koubo_storyboard" / "constants.py").read_text(encoding="utf-8")
        runner_source = (BACKEND_ROOT / "opcrew_backend" / "koubo" / "koubo_storyboard" / "tool_runner_services.py").read_text(encoding="utf-8")
        router_source = (BACKEND_ROOT / "opcrew_backend" / "koubo" / "router.py").read_text(encoding="utf-8")
        self.assertIn('ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py"', constants_source)
        self.assertIn('ToolLibrary" / "TalkingHead_V1" / "05_02_VideoPlanExecutor.py"', constants_source)
        storyboard_selector = runner_source.split("def video_plan_execution_script_path", 1)[1].split("def storyboard_workflow_id", 1)[0]
        self.assertIn("return VIDEO_PLAN_EXECUTION_SCRIPT_PATH", storyboard_selector)
        self.assertNotIn("TALKING_HEAD_VIDEO_PLAN_EXECUTION_SCRIPT_PATH", storyboard_selector)
        prompt_reload_selector = runner_source.split("def reload_video_only_plan_prompt", 1)[1].split("def run_video_only_plan_execution_tool", 1)[0]
        self.assertIn('storyboard_workflow_id(workspace) == "person_talking_head_v1"', prompt_reload_selector)
        self.assertIn("reload_talking_head_video_only_plan_prompt", prompt_reload_selector)
        talking_head_0502_command = router_source.split('if step_id == "05_02":', 1)[1].split('if step_id == "06_01":', 1)[0]
        self.assertIn('analysis_v1_payload_workflow_profile(payload) == WORKFLOW_PERSON_TALKING_HEAD_V1', talking_head_0502_command)
        self.assertIn('command.append("--execute-lipsync")', talking_head_0502_command)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            write_json(workspace / "SessionContext" / "Variables.json", {"workflow_id": WORKFLOW_PERSON_TALKING_HEAD_V1})
            self.assertEqual(video_plan_execution_script_path(workspace), REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py")
            self.assertEqual(storyboard_workflow_id(workspace), WORKFLOW_PERSON_TALKING_HEAD_V1)
            write_json(workspace / "SessionContext" / "Variables.json", {"workflow_id": WORKFLOW_ANALYSIS_V1})
            self.assertEqual(video_plan_execution_script_path(workspace), REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py")
            self.assertEqual(storyboard_workflow_id(workspace), WORKFLOW_ANALYSIS_V1)

    def test_user_provided_runtime_uses_only_declared_variables_input(self) -> None:
        rewrite = load_module("talking_head_parameter_storage_rewrite", REWRITE_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script_path = workspace / "SessionContext" / "Script" / "user_script.txt"
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text("第一句。\n第二句。", encoding="utf-8")
            write_json(workspace / "SessionContext" / "Variables.json", {
                "workflow_id": WORKFLOW_PERSON_TALKING_HEAD_V1,
                "script_creation_mode": "user_provided",
                "script_creation": {
                    "mode": "user_provided",
                    "input": {"user_script": {"path": "SessionContext/Script/user_script.txt"}},
                    "simple_prompt": "",
                    "final_prompt": "",
                },
                "talking_head": {"segment_planning": {"srt_target_seconds": 8}},
                "storyboard_quick_config": {"target_shot_seconds": 8},
            })
            args = rewrite.Args(
                workspace=str(workspace),
                model_provider="",
                model_id="",
                database_url="",
                database_url_env="OPENCREW_DATABASE_URL",
                force=False,
                resume=False,
                print_json=False,
            )
            result = rewrite.run(args)
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["requires_model_calls"])
            self.assertEqual(result["counts"]["items"], 2)
            self.assertNotIn("task_meta", result["inputs"])
            variables = json.loads((workspace / "SessionContext" / "Variables.json").read_text(encoding="utf-8"))
            rewritten = json.loads((workspace / "SessionOutput" / "subtitle" / "rewritten_srt_items.json").read_text(encoding="utf-8"))
            self.assertEqual(variables["task_summary"], rewritten["task_summary"])
            self.assertIn("第一句", variables["task_summary"])
            self.assertEqual(result["outputs"]["task_summary"], variables["task_summary"])

    def test_selected_wan_model_becomes_session_default_video_config(self) -> None:
        prepare = load_module("talking_head_parameter_storage_prepare", PREPARE_PATH)
        selected = prepare.talking_head_video_config(
            {
                "kind": "video",
                "provider": "wan",
                "model": "wan2.7-r2v",
                "enabled": True,
                "active": False,
                "api_key_ref": "video_wan_key",
                "has_api_key": True,
                "source": "postgres:tool_media_provider_configs",
            },
            {
                "video_model": {
                    "provider": "wan",
                    "model": "wan2.7-r2v",
                    "model_alias": "Max 2.7 W",
                }
            },
        )
        self.assertEqual(selected["provider"], "wan")
        self.assertEqual(selected["model"], "wan2.7-r2v-2026-06-12")
        self.assertEqual(selected["model_alias"], "Max 2.7 W")
        self.assertEqual(selected["api_key_ref"], "video_wan_key")
        self.assertTrue(selected["enabled"])
        self.assertTrue(selected["active"])


if __name__ == "__main__":
    unittest.main()
