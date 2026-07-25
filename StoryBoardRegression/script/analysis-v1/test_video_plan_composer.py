from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "OpenCrew" / "ToolLibrary" / "Analysis_V1" / "06_01_VideoPlanComposer.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analysis_v1_video_plan_composer", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def args_for(module: ModuleType, workspace: Path, **overrides):
    values = {
        "workspace": str(workspace),
        "target_type": "task",
        "shot_id": "",
        "scene_id": "",
        "subtitle_mode": "none",
        "watermark_mode": "never",
        "force": False,
        "resume": False,
        "print_json": False,
    }
    values.update(overrides)
    return module.Args(**values)


def segment_payload(shot_id: str, scene_id: str, index: int, srt_id: str) -> dict:
    return {
        "segment_id": f"{shot_id}_{scene_id}_segment_{index:03d}",
        "segment_index": index,
        "dialogue_ids": [srt_id],
        "planned_outputs": {"video_path": f"SessionOutput/storyboard/Working/{srt_id}_Video_Final.mp4"},
    }


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    write_json(workspace / "SessionContext" / "Variables.json", {"workspace_dir": str(workspace)})
    shots = []
    plan_shots = []
    for shot_no in range(1, 3):
        shot_id = f"shot_{shot_no:03d}"
        scenes = []
        plan_scenes = []
        for scene_no in range(1, 3):
            scene_id = f"scene_{shot_no:03d}_{scene_no:03d}"
            srt_id = f"srt_{shot_no:03d}_{scene_no:03d}"
            video = workspace / "SessionOutput" / "storyboard" / "Working" / f"{srt_id}_Video_Final.mp4"
            video.parent.mkdir(parents=True, exist_ok=True)
            video.write_bytes(f"video-{srt_id}".encode("utf-8"))
            scenes.append({
                "scene_id": scene_id,
                "dialogue_items": [
                    {"srt_id": srt_id, "dialogue": f"生成 TTS 的文本 {srt_id}", "start": 0, "end": 1, "duration": 1}
                ],
            })
            plan_scenes.append({"scene_id": scene_id, "segments": [segment_payload(shot_id, scene_id, 1, srt_id)]})
        shots.append({"shot_id": shot_id, "scenes": scenes})
        plan_shots.append({"shot_id": shot_id, "scenes": plan_scenes})
    write_json(workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json", {"schema_version": "analysis_v1_srt_storyboard_0.2", "shots": shots})
    write_json(workspace / "SessionOutput" / "storyboard" / "koubo_storyboard_edit.json", {"schema_version": "koubo_storyboard_edit_0.1", "shots": shots})
    write_json(workspace / "SessionOutput" / "storyboard" / "video_generation_plan.json", {"schema_version": "analysis_v1_video_generation_plan_0.1", "shots": plan_shots})
    return workspace


def install_media_doubles(module: ModuleType, monkeypatch, calls: list[dict]) -> None:
    def probe(path: Path):
        return {"path": str(path), "duration_seconds": 1.0, "width": 720, "height": 1280, "has_audio": True, "size_bytes": path.stat().st_size, "sha256": path.name}

    def compose(workspace: Path, input_paths: list[Path], output_path: Path, scope_key: str):
        calls.append({"kind": "compose", "scope": scope_key, "inputs": [path.name for path in input_paths]})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(f"composed-{scope_key}".encode("utf-8"))
        return {"source": "test_compose", "input_count": len(input_paths), "output_path": module.rel(workspace, output_path)}

    def watermark(workspace: Path, input_path: Path, output_path: Path, scope_key: str, args, result):
        calls.append({"kind": "watermark", "scope": scope_key, "input": input_path.name})
        return input_path, {"status": "not_detected", "input_path": module.rel(workspace, input_path)}

    def hyperframe(workspace: Path, scene_video: Path, srt_path: Path, subtitles: list[dict], output_path: Path, scope_key: str, duration: float):
        calls.append({"kind": "hyperframe", "scope": scope_key, "srt": srt_path.name})
        assert subtitles
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(f"subtitled-{scope_key}".encode("utf-8"))
        return {"source": "test_hyperframe", "output_path": module.rel(workspace, output_path)}

    monkeypatch.setattr(module, "probe_media", probe)
    monkeypatch.setattr(module, "compose_videos", compose)
    monkeypatch.setattr(module, "process_watermark", watermark)
    monkeypatch.setattr(module, "render_hyperframe_subtitles", hyperframe)


def test_scene_compose_outputs_to_working_and_writes_storyboard(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path)
    calls: list[dict] = []
    install_media_doubles(module, monkeypatch, calls)

    result = module.run(args_for(module, workspace, target_type="scene", shot_id="shot_001", scene_id="scene_001_001", subtitle_mode="hyperframe"))

    assert result["status"] == "completed"
    assert (workspace / "SessionOutput" / "storyboard" / "Working" / "shot_001_scene_001_001_Scene_Final.mp4").exists()
    assert (workspace / "SessionOutput" / "storyboard" / "Working" / "shot_001_scene_001_001_Scene_Subtitled_Final.mp4").exists()
    storyboard = json.loads((workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json").read_text(encoding="utf-8"))
    scene_assets = storyboard["shots"][0]["scenes"][0]["compose_assets"]["scene"]
    assert scene_assets["video_path"] == "SessionOutput/storyboard/Working/shot_001_scene_001_001_Scene_Final.mp4"
    assert scene_assets["subtitled_video_path"] == "SessionOutput/storyboard/Working/shot_001_scene_001_001_Scene_Subtitled_Final.mp4"
    edit = json.loads((workspace / "SessionOutput" / "storyboard" / "koubo_storyboard_edit.json").read_text(encoding="utf-8"))
    assert edit["shots"][0]["scenes"][0]["compose_assets"]["scene"]["status"] == "completed"


def test_shot_target_composes_scenes_before_shot(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path)
    calls: list[dict] = []
    install_media_doubles(module, monkeypatch, calls)

    result = module.run(args_for(module, workspace, target_type="shot", shot_id="shot_001"))

    assert result["status"] == "completed"
    compose_calls = [call for call in calls if call["kind"] == "compose"]
    assert [call["scope"] for call in compose_calls] == [
        "shot_001_scene_001_001_Scene",
        "shot_001_scene_001_002_Scene",
        "shot_001_Shot",
    ]
    assert compose_calls[-1]["inputs"] == ["shot_001_scene_001_001_Scene_Final.mp4", "shot_001_scene_001_002_Scene_Final.mp4"]


def test_task_target_composes_shots_before_shot_plan(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path)
    calls: list[dict] = []
    install_media_doubles(module, monkeypatch, calls)

    result = module.run(args_for(module, workspace, target_type="task"))

    assert result["status"] == "completed"
    compose_scopes = [call["scope"] for call in calls if call["kind"] == "compose"]
    assert compose_scopes[-1] == "ShotPlan"
    shot_plan_call = [call for call in calls if call.get("scope") == "ShotPlan"][0]
    assert shot_plan_call["inputs"] == ["shot_001_Shot_Subtitled_Final.mp4", "shot_002_Shot_Subtitled_Final.mp4"]
    storyboard = json.loads((workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json").read_text(encoding="utf-8"))
    assert storyboard["compose_assets"]["shot_plan"]["video_path"] == "SessionOutput/storyboard/Working/ShotPlan_Final.mp4"


def test_missing_segment_video_blocks_scene(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path)
    missing = workspace / "SessionOutput" / "storyboard" / "Working" / "srt_001_001_Video_Final.mp4"
    missing.unlink()
    calls: list[dict] = []
    install_media_doubles(module, monkeypatch, calls)

    result = module.run(args_for(module, workspace, target_type="scene", shot_id="shot_001", scene_id="scene_001_001"))

    assert result["status"] == "blocked"
    assert result["blocked_reasons"][0]["code"] == "segment_video_missing"
    assert not [call for call in calls if call["kind"] == "compose"]


def test_hyperframe_failure_marks_scene_failed(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path)
    calls: list[dict] = []
    install_media_doubles(module, monkeypatch, calls)

    def fail_hyperframe(*args, **kwargs):
        raise module.ToolError("hyperframe failed")

    monkeypatch.setattr(module, "render_hyperframe_subtitles", fail_hyperframe)

    result = module.run(args_for(module, workspace, target_type="scene", shot_id="shot_001", scene_id="scene_001_001", subtitle_mode="hyperframe"))

    assert result["status"] == "failed"
    assert "hyperframe failed" in result["blocked_reasons"][0]["message"]


def test_watermark_always_no_detection_continues_without_processing(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path)
    source = workspace / "SessionOutput" / "storyboard" / "Working" / "srt_001_001_Video_Final.mp4"
    output = workspace / "S10_06_01_VideoPlanComposer" / "Working" / "unused.mp4"
    monkeypatch.setattr(module, "detect_watermark_region", lambda *args, **kwargs: None)

    result: dict = {"warnings": []}
    processed, info = module.process_watermark(workspace, source, output, "scope", args_for(module, workspace), result)

    assert processed == source
    assert info["status"] == "not_detected"
    assert not output.exists()


def test_watermark_region_is_clamped_to_frame(tmp_path) -> None:
    module = load_tool()

    bounded = module.clamp_watermark_region({"x": 720, "y": 0, "w": 280, "h": 160, "label": "top_right"}, 1000, 600)

    assert bounded["x"] == 720
    assert bounded["w"] == 279
    assert bounded["h"] == 160


def test_compose_videos_maps_streams_by_type_for_concat(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = tmp_path / "workspace"
    first = workspace / "first_audio_then_video.mp4"
    second = workspace / "second_video_then_audio.mp4"
    output = workspace / "S10_06_01_VideoPlanComposer" / "Working" / "scene.mp4"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    commands: list[list[str]] = []

    def probe(path: Path):
        if path == output:
            return {"duration_seconds": 2.0, "width": 720, "height": 1280, "r_frame_rate": "24/1", "has_audio": True}
        return {"duration_seconds": 1.0, "width": 720, "height": 1280, "r_frame_rate": "24/1", "has_audio": True}

    class Completed:
        returncode = 0
        stderr = ""

    def run(command, capture_output, text, check):
        commands.append(command)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"composed")
        return Completed()

    monkeypatch.setattr(module, "probe_media", probe)
    monkeypatch.setattr(module, "ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(module.subprocess, "run", run)

    result = module.compose_videos(workspace, [first, second], output, "scene")

    assert result["source"] == "ffmpeg_concat_filter_reencode"
    command = commands[0]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "[0:v:0]" in filter_graph
    assert "[0:a:0]" in filter_graph
    assert "[1:v:0]" in filter_graph
    assert "[1:a:0]" in filter_graph
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]" in filter_graph
    assert command[command.index("-map") + 1] == "[outv]"
    assert command[command.index("-g") + 1] == "24"
    assert command[command.index("-keyint_min") + 1] == "24"
