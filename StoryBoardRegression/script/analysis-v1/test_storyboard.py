from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "OpenCrew" / "ToolLibrary" / "Analysis_V1" / "04_02_StoryBoard.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analysis_v1_storyboard", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    write_json(
        workspace / "SessionContext" / "Variables.json",
        {
            "workflow_id": "openclip_analysis",
            "task_id": 31,
            "business_context": {
                "industry": "大健康",
                "persona": "专家型主理人",
                "target_audience": "潜在客户",
                "product_info": "化橘红",
                "constraints": "Scene 归集 SRT，但每句 Dialogue 保持独立记录",
                "video_formula": "Hook/Trust/CTA",
            },
            "storyboard_prompt": {
                "simple_prompt": "按语义组织分镜",
                "final_prompt": "把 SRT 组织成 Shot / Scene。Scene 可以包含多条 SRT，但每条 SRT 仍然是独立 Dialogue 记录。",
                "source": "test",
            },
            "run_model_provider": "openai",
            "run_model_id": "gpt-test-run",
            "opencode_session_id": "ses_test",
            "workspace_dir": str(workspace),
        },
    )
    write_json(
        workspace / "SessionOutput" / "subtitle" / "rewritten_srt_items.json",
        {
            "schema_version": "analysis_v1_rewritten_srt_items_0.1",
            "items": [
                {
                    "srt_id": "srt_0001_01",
                    "dialogue": "第一句改写",
                    "original_dialogue": "第一句原文",
                    "image_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg",
                    "start": 0.28,
                    "end": 1.68,
                    "duration": 1.4,
                },
                {
                    "srt_id": "srt_0001_02",
                    "dialogue": "第二句改写",
                    "original_dialogue": "第二句原文",
                    "image_path": "SessionOutput/visual/srt_frames/srt_0001_02.jpg",
                    "start": 1.68,
                    "end": 3.08,
                    "duration": 1.4,
                },
            ],
        },
    )
    return workspace


def fake_args(module: ModuleType, workspace: Path, **overrides):
    values = {
        "workspace": str(workspace),
        "model_provider": "",
        "model_id": "",
        "database_url": "",
        "database_url_env": "OPENCREW_DATABASE_URL",
        "force": False,
        "resume": False,
        "force_regenerate_prompts": False,
        "max_repair_attempts": 1,
        "print_json": False,
    }
    values.update(overrides)
    return module.Args(**values)


def test_storyboard_groups_srt_under_scene_without_merging_dialogue_records(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path)

    monkeypatch.setattr(
        module,
        "call_opencode_run_model",
        lambda args, variables, config, prompt_path: {
            "video_formula": "Hook/Trust/CTA",
            "shots": [
                {
                    "shot_id": "shot_001",
                    "formula_stage": "Hook",
                    "summary": "开场",
                    "srt_ids": ["srt_0001_01", "srt_0001_02"],
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "summary": "一个 Scene 归集两条 SRT",
                            "srt_ids": ["srt_0001_01", "srt_0001_02"],
                        }
                    ],
                }
            ],
        },
    )

    result = module.run(fake_args(module, workspace))

    assert result["status"] == "completed"
    payload = json.loads((workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json").read_text())
    assert payload["schema_version"] == "analysis_v1_srt_storyboard_0.2"
    shot = payload["shots"][0]
    scene = shot["scenes"][0]
    assert "dialogue" not in shot
    assert "dialogue" not in scene
    assert shot["srt_ids"] == ["srt_0001_01", "srt_0001_02"]
    assert scene["srt_ids"] == ["srt_0001_01", "srt_0001_02"]
    assert [item["srt_id"] for item in scene["dialogue_items"]] == ["srt_0001_01", "srt_0001_02"]
    assert [item["dialogue"] for item in scene["dialogue_items"]] == ["第一句改写", "第二句改写"]
    assert scene["dialogue_items"][0]["start"] == 0.28
    assert scene["dialogue_items"][0]["image_path"] == "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
    assert scene["dialogue_items"][1]["image_path"] == ""
    assert scene["key_frame_paths"] == ["SessionOutput/visual/srt_frames/srt_0001_01.jpg"]
    assert shot["key_frame_paths"] == ["SessionOutput/visual/srt_frames/srt_0001_01.jpg"]
    assert "asset_dir" not in shot
    assert "asset_dir" not in scene
    assert "final_assets" not in scene
    assert scene["asset_key"] == "shot_001_scene_001"
    assert scene["working_assets"] == {
        "audio": {"slot": "Audio_Final", "path": ""},
        "images": [
            {"slot": "Image_01", "path": ""},
            {"slot": "Image_02", "path": ""},
        ],
        "video": {"slot": "Video_Final", "path": ""},
    }
    assert (workspace / "SessionOutput" / "storyboard" / "Working").exists()
    assert not (workspace / "SessionOutput" / "storyboard" / "shots").exists()
    assert not (workspace / "SessionOutput" / "storyboard" / "scenes").exists()
    assert (workspace / "SessionOutput" / "storyboard" / "assets" / "images").exists()
    assert (workspace / "SessionOutput" / "storyboard" / "assets" / "videos").exists()
    prompt = (workspace / "S7_04_02_StoryBoard" / "Prompt" / "00_storyboard_prompt.md").read_text(encoding="utf-8")
    assert "逐条 dialogue_items" in prompt
