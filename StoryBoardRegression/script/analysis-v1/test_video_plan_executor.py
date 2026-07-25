from __future__ import annotations

import importlib.util
import base64
import json
import math
import struct
import sys
import wave
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "OpenCrew" / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analysis_v1_video_plan_executor", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_wav(path: Path, seconds: float = 0.15, freq: float = 440.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 24000
    total = max(1, int(sample_rate * seconds))
    frames = bytearray()
    for index in range(total):
        sample = int(9000 * math.sin(2 * math.pi * freq * index / sample_rate))
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(bytes(frames))


def make_workspace(tmp_path: Path, *, need_lipsync: bool = True) -> Path:
    workspace = tmp_path / "workspace"
    source_image = workspace / "SessionOutput" / "visual" / "srt_frames" / "srt_0001.png"
    source_image.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (720, 1280), (234, 230, 222))
        draw = ImageDraw.Draw(image)
        draw.ellipse((250, 120, 470, 340), fill=(210, 168, 132), outline=(70, 60, 52), width=3)
        draw.rectangle((210, 360, 510, 920), fill=(46, 75, 110))
        draw.rectangle((110, 620, 260, 770), fill=(245, 245, 245), outline=(40, 40, 40), width=3)
        image.save(source_image)
    except Exception:
        source_image.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
    consistency_dir = workspace / "SessionContext" / "Consistency"
    consistency_dir.mkdir(parents=True, exist_ok=True)
    (consistency_dir / "HOST.png").write_bytes(source_image.read_bytes())
    (consistency_dir / "Product.png").write_bytes(source_image.read_bytes())
    write_json(consistency_dir / "host_manifest.json", {"output": "SessionContext/Consistency/HOST.png", "kind": "host"})
    write_json(consistency_dir / "product_manifest.json", {"output": "SessionContext/Consistency/Product.png", "kind": "product"})
    write_wav(workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_Audio_Final.wav")
    write_json(
        workspace / "SessionContext" / "Variables.json",
        {
            "schema_version": "analysis_v1_session_context_0.1",
            "workspace_dir": str(workspace),
            "default_image_config": {"kind": "image", "provider": "openai", "model": "gpt-image-1", "api_key_ref": "image_key", "has_api_key": True},
            "default_video_config": {"kind": "video", "provider": "xai", "model": "grok-2-vision-video", "api_key_ref": "video_key", "has_api_key": True},
            "default_lipsync_config": {"kind": "lipsync", "provider": "sync.so", "model": "lipsync-2", "api_key_ref": "sync_key", "has_api_key": True},
            "default_tts_config": {"kind": "tts", "provider": "google", "model": "gemini-3.1-flash-tts-preview", "api_key_ref": "tts_key", "has_api_key": True},
        },
    )
    write_json(
        workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json",
        {
            "schema_version": "analysis_v1_srt_storyboard_0.2",
            "shots": [
                {
                    "shot_id": "shot_001",
                    "summary": "quiet host closeup",
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "title": "intro",
                            "summary": "host speaks to camera",
                            "dialogue_items": [
                                {
                                    "srt_id": "srt_0001",
                                    "dialogue": "你好，欢迎来到今天的产品介绍。",
                                    "start": 0,
                                    "end": 1,
                                    "duration": 1,
                                    "image_path": "SessionOutput/visual/srt_frames/srt_0001.png",
                                    "working_assets": {"audio": {"path": "SessionOutput/storyboard/Working/srt_0001_Audio_Final.wav"}},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    write_json(
        workspace / "SessionOutput" / "storyboard" / "koubo_storyboard_edit.json",
        {
            "schema_version": "koubo_storyboard_edit_0.1",
            "shots": [
                {
                    "shot_id": "shot_001",
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "dialogues": [
                                {
                                    "dialogue_id": "scene_001_dialogue_001",
                                    "srt_id": "srt_0001",
                                    "srt_ids": ["srt_0001"],
                                    "dialogue_asset_key": "srt_0001",
                                    "text": "你好，欢迎来到今天的产品介绍。",
                                    "duration": 1,
                                    "image_path": "SessionOutput/visual/srt_frames/srt_0001.png",
                                    "bound_image_path": "",
                                    "working_assets": {
                                        "audio": {"slot": "Audio_Final", "source_type": "generated", "path": "SessionOutput/storyboard/Working/srt_0001_Audio_Final.wav"},
                                        "images": [{"slot": "Image_01", "source_type": "", "path": ""}, {"slot": "Image_02", "source_type": "", "path": ""}],
                                        "video": {"slot": "Video_Final", "source_type": "", "path": ""},
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    write_json(
        workspace / "SessionOutput" / "storyboard" / "video_generation_plan.json",
        {
            "schema_version": "analysis_v1_video_generation_plan_0.1",
            "shots": [
                {
                    "shot_id": "shot_001",
                    "status": "planned",
                    "scenes": [
                        {
                            "scene_id": "scene_001",
                            "status": "planned",
                            "segments": [
                                {
                                    "segment_id": "shot_001_scene_001_segment_001",
                                    "dialogue_ids": ["srt_0001"],
                                    "planned_video_duration": 4.0,
                                    "first_frame": {
                                        "source_type": "original_image",
                                        "source_path": "SessionOutput/visual/srt_frames/srt_0001.png",
                                        "requires_generated_image_before_video": True,
                                        "planned_generated_image_path": "SessionOutput/storyboard/Working/srt_0001_Image_01.png",
                                        "materialize_first_frame": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""},
                                    },
                                    "tail_frame": {"planned_path": "SessionOutput/storyboard/Working/srt_0001_TailFrame.jpg", "available": False},
                                    "tasks": {
                                        "need_audio": False,
                                        "need_image_prompt": True,
                                        "need_image": True,
                                        "need_video_prompt": True,
                                        "need_video": True,
                                        "need_lipsync": need_lipsync,
                                        "need_audio_video_sync": not need_lipsync,
                                        "need_sync": True,
                                        "sync_mode": "lipsync" if need_lipsync else "audio_replace_retime",
                                        "lipsync_disabled_by_ui": not need_lipsync,
                                        "lipsync_reason": "visible_face" if need_lipsync else "ui_disabled",
                                    },
                                    "dialogue_audio_tasks": [
                                        {
                                            "srt_id": "srt_0001",
                                            "need_audio": False,
                                            "audio_source": "existing_dialogue_audio",
                                            "existing_audio_path": "SessionOutput/storyboard/Working/srt_0001_Audio_Final.wav",
                                            "planned_audio_path": "SessionOutput/storyboard/Working/srt_0001_Audio_Final.wav",
                                        }
                                    ],
                                    "planned_outputs": {
                                        "image_prompt_path": "SessionOutput/storyboard/Working/srt_0001_ImagePrompt.json",
                                        "image_path": "SessionOutput/storyboard/Working/srt_0001_Image_01.png",
                                        "segment_audio_path": "SessionOutput/storyboard/Working/srt_0001_SegmentAudio_Final.wav",
                                        "video_prompt_path": "SessionOutput/storyboard/Working/srt_0001_VideoPrompt.json",
                                        "video_path": "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4",
                                        "video_duration_seconds": 4.0,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    return workspace


def args_for(module: ModuleType, workspace: Path, **overrides):
    values = {
        "workspace": str(workspace),
        "database_url": "",
        "max_segments": 0,
        "force": False,
        "execute_audio": True,
        "execute_image": True,
        "execute_video": True,
        "execute_lipsync": True,
        "image_provider": "",
        "image_model": "",
        "video_provider": "",
        "video_model": "",
        "lipsync_provider": "",
        "lipsync_model": "",
        "tts_provider": "",
        "tts_model": "",
        "provider_timeout_seconds": 30,
        "print_json": False,
        "execute_audio_video_sync": True,
    }
    values.update(overrides)
    return module.Args(**values)


def install_provider_test_doubles(module: ModuleType, monkeypatch, calls: list[str]) -> None:
    monkeypatch.setattr(module, "load_provider_config", lambda args, variables, kind, provider_override="", model_override="": {"kind": kind, "provider": provider_override or {"image": "openai", "video": "xai", "lipsync": "sync.so", "tts": "google"}[kind], "model": model_override or "test-model", "api_key": "test-secret"})

    def image(config, prompt_path, output_path, reference_paths, timeout_seconds):
        calls.append("image")
        assert len(reference_paths) == 3
        assert all(path.exists() for path in reference_paths)
        prompt_payload = json.loads(prompt_path.read_text(encoding="utf-8"))
        assert prompt_payload["provider_profile"] == "image_gpt"
        assert "TARGET_FRAME" in prompt_payload["prompt"]
        assert "HOST_REFERENCE" in prompt_payload["prompt"]
        assert "PRODUCT_REFERENCE" in prompt_payload["prompt"]
        assert "vertical 9:16" in prompt_payload["prompt"]
        from PIL import Image

        Image.new("RGB", (1024, 1536), (120, 160, 200)).save(output_path)
        return {"provider": config["provider"], "model": config["model"], "output_path": str(output_path), "reference_count": len(reference_paths)}

    def video(config, prompt_path, output_path, reference_images, duration, timeout_seconds):
        calls.append("video")
        prompt_payload = json.loads(prompt_path.read_text(encoding="utf-8"))
        assert prompt_payload["provider_profile"] == "video_grok"
        assert "Grok video task" in prompt_payload["prompt"]
        output_path.write_bytes(b"raw-video")
        return {"provider": config["provider"], "model": config["model"], "output_path": str(output_path)}

    def lipsync(config, video_path, audio_path, output_path, request_path, status_path, create_response_path, timeout_seconds, prompt_path=None, segment=None):
        calls.append("lipsync")
        assert prompt_path is not None
        prompt_payload = json.loads(prompt_path.read_text(encoding="utf-8"))
        assert prompt_payload["provider_profile"] == "lipsync_syncso"
        output_path.write_bytes(b"lipsync-video")
        module.write_json(request_path, {"provider": config["provider"], "model": config["model"]})
        module.write_json(status_path, {"status": "COMPLETED"})
        module.write_json(create_response_path, {"status_code": 200, "body": {"id": "test"}})
        return {"provider": config["provider"], "model": config["model"], "output_path": str(output_path)}

    def audio_video_sync(workspace, video_path, audio_path, output_path):
        calls.append("audio_video_sync")
        output_path.write_bytes(b"audio-video-sync")
        return {"source": "ffmpeg_audio_replace_retime", "video_path": str(video_path), "audio_path": str(audio_path), "output_path": str(output_path), "audio_duration_seconds": 1.0, "video_duration_seconds": 4.0}

    def tail(video_path, output_path):
        calls.append("tail")
        output_path.write_bytes(b"tail-frame")
        return {"source": "test_tail"}

    monkeypatch.setattr(module, "generate_image_with_provider", image)
    monkeypatch.setattr(module, "generate_video_with_provider", video)
    monkeypatch.setattr(module, "run_lipsync_with_provider", lipsync)
    monkeypatch.setattr(module, "replace_video_audio_to_match_duration", audio_video_sync)
    monkeypatch.setattr(module, "extract_tail_frame", tail)


def test_executes_segment_outputs_prompt_audio_image_video_lipsync(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path, need_lipsync=True)
    calls: list[str] = []
    install_provider_test_doubles(module, monkeypatch, calls)

    result = module.run(args_for(module, workspace))

    assert result["status"] == "completed"
    assert calls == ["image", "video", "lipsync", "tail"]
    assert (workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_ImagePrompt.json").exists()
    image_prompt = json.loads((workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_ImagePrompt.json").read_text(encoding="utf-8"))
    assert "vertical 9:16" in image_prompt["prompt"]
    assert image_prompt["target_frame_path"].endswith("_TargetFrame.png")
    assert image_prompt["host_reference_path"].endswith("_HOST_REFERENCE.png")
    assert image_prompt["product_reference_path"].endswith("_PRODUCT_REFERENCE.png")
    assert [item["role"] for item in image_prompt["reference_images"]] == ["TARGET_FRAME", "HOST_REFERENCE", "PRODUCT_REFERENCE"]
    assert (workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_VideoPrompt.json").exists()
    assert (workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_SegmentAudio_Final.wav").exists()
    assert (workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_Video_Final.mp4").read_bytes() == b"lipsync-video"
    from PIL import Image

    with Image.open(workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_Image_01.png") as image:
        assert image.size == (720, 1280)
    assert (workspace / "S9_05_02_VideoPlanExecutor" / "Working" / "srt_0001_Video_Raw.mp4").exists()
    assert not (workspace / "S9_05_02_VideoPlanExecutor" / "Output" / "srt_0001_Video_Raw.mp4").exists()
    storyboard = json.loads((workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json").read_text(encoding="utf-8"))
    dialogue = storyboard["shots"][0]["scenes"][0]["dialogue_items"][0]
    assert dialogue["working_assets"]["images"][0]["path"] == "SessionOutput/storyboard/Working/srt_0001_Image_01.png"
    assert dialogue["working_assets"]["images"][0]["source_type"] == "generated"
    assert dialogue["bound_image_path"] == "SessionOutput/storyboard/Working/srt_0001_Image_01.png"
    assert dialogue["working_assets"]["video"]["path"] == "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4"
    assert dialogue["working_assets"]["video"]["source_type"] == "generated"
    edit = json.loads((workspace / "SessionOutput" / "storyboard" / "koubo_storyboard_edit.json").read_text(encoding="utf-8"))
    edit_dialogue = edit["shots"][0]["scenes"][0]["dialogues"][0]
    assert edit_dialogue["working_assets"]["images"][0]["path"] == "SessionOutput/storyboard/Working/srt_0001_Image_01.png"
    assert edit_dialogue["bound_image_path"] == "SessionOutput/storyboard/Working/srt_0001_Image_01.png"
    assert edit_dialogue["working_assets"]["video"]["path"] == "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4"
    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert "test-secret" not in serialized
    assert "api_key" not in serialized


def test_cutaway_prompts_are_product_only_without_host_or_talking_face(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path, need_lipsync=False)
    plan = json.loads((workspace / "SessionOutput" / "storyboard" / "video_generation_plan.json").read_text(encoding="utf-8"))
    storyboard = json.loads((workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json").read_text(encoding="utf-8"))
    shot = plan["shots"][0]
    scene = shot["scenes"][0]
    segment = scene["segments"][0]
    segment["tasks"].update({
        "need_lipsync": False,
        "lipsync_disabled_by_ui": True,
        "lipsync_reason": "user_marked_cutaway",
        "lipsync_decision_source": "dialogue.video_plan.is_talking_head",
    })
    dialogue_index = module.flatten_dialogues(storyboard)

    references = module.prepare_image_references(workspace, segment, plan)
    image_module = module.image_module_for("openai", "gpt-image-1")
    video_module = module.video_module_for("xai", "grok-imagine-video")
    image_prompt = image_module.build_prompt_package({
        "workspace": str(workspace),
        "prompt_dir": str(workspace / "S9_05_02_VideoPlanExecutor" / "Prompt"),
        "segment": segment,
        "shot": shot,
        "scene": scene,
        "dialogue_index": dialogue_index,
        "references": references,
        "reference_manifests": {},
    })
    video_prompt = video_module.build_prompt_package({
        "workspace": str(workspace),
        "prompt_dir": str(workspace / "S9_05_02_VideoPlanExecutor" / "Prompt"),
        "segment": segment,
        "shot": shot,
        "scene": scene,
        "dialogue_index": dialogue_index,
    })

    assert [item["role"] for item in references] == ["TARGET_FRAME", "PRODUCT_REFERENCE"]
    assert image_prompt["host_reference_path"] == ""
    assert image_prompt["reference_priority"]["host_reference"] == "not_used_for_product_only_cutaway"
    assert "Product-only cutaway" in image_prompt["positive_prompt"]
    assert "do not use a host reference" in image_prompt["positive_prompt"]
    assert "complete visible product package" in image_prompt["positive_prompt"]
    assert "Remove subtitles" in image_prompt["positive_prompt"]
    assert "partial product replacement" in image_prompt["negative_prompt"]
    assert "mixed old and new packaging" in image_prompt["negative_prompt"]
    assert "subtitles" in image_prompt["negative_prompt"]
    assert "animated portrait" in image_prompt["negative_prompt"]
    assert "human face" in image_prompt["negative_prompt"]
    assert "VIDEO_GROK_SPEECH_CUTAWAY" in video_prompt["template_blocks"]
    assert "VIDEO_GROK_STORYBOARD_CUTAWAY" in video_prompt["template_blocks"]
    assert "VIDEO_GROK_PITFALLS_CUTAWAY" in video_prompt["template_blocks"]
    assert "product-only cutaway" in video_prompt["positive_prompt"]
    assert "No spoken presenter dialogue" in video_prompt["speech_prompt"]
    assert "Printed faces on packaging must remain flat static graphics" in video_prompt["positive_prompt"]
    assert "talking product package" in video_prompt["negative_prompt"]
    assert "mouth movement" in video_prompt["negative_prompt"]
    assert "Natural mouth movement base" not in video_prompt["positive_prompt"]
    assert "The presenter speaks exactly this Mandarin Chinese dialogue" not in video_prompt["prompt"]


def test_grok_talking_head_prompt_splits_speech_storyboard_and_scopes_pitfalls(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path, need_lipsync=True)
    plan = json.loads((workspace / "SessionOutput" / "storyboard" / "video_generation_plan.json").read_text(encoding="utf-8"))
    storyboard = json.loads((workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json").read_text(encoding="utf-8"))
    shot = plan["shots"][0]
    scene = shot["scenes"][0]
    segment = scene["segments"][0]
    dialogue_index = module.flatten_dialogues(storyboard)

    video_module = module.video_module_for("xai", "grok-imagine-video")
    video_prompt = video_module.build_prompt_package({
        "workspace": str(workspace),
        "prompt_dir": str(workspace / "S9_05_02_VideoPlanExecutor" / "Prompt"),
        "segment": segment,
        "shot": shot,
        "scene": scene,
        "dialogue_index": dialogue_index,
    })

    assert video_prompt["schema_version"] == "analysis_v1_05_02_video_prompt_grok_0.2"
    assert "VIDEO_GROK_SPEECH_TALKING_HEAD" in video_prompt["template_blocks"]
    assert "VIDEO_GROK_STORYBOARD_TALKING_HEAD" in video_prompt["template_blocks"]
    assert "VIDEO_GROK_NEGATIVE_TALKING_HEAD" in video_prompt["template_blocks"]
    assert "VIDEO_GROK_PITFALLS_TALKING_HEAD" in video_prompt["template_blocks"]
    assert "VIDEO_GROK_PITFALLS_CUTAWAY" not in video_prompt["template_blocks"]
    assert "The presenter speaks exactly this Mandarin Chinese dialogue" in video_prompt["speech_prompt"]
    assert "Do not translate, paraphrase, summarize, or add words" in video_prompt["speech_prompt"]
    assert "must never appear as subtitles or visible text" in video_prompt["speech_prompt"]
    assert "Camera locked, static, fixed medium shot" in video_prompt["storyboard_prompt"]
    assert "The product remains stable in shape, color, label layout, position, and scale" in video_prompt["storyboard_prompt"]
    assert video_prompt["prompt"].index("Speech / 口播:") < video_prompt["prompt"].index("Storyboard / 分镜:")
    assert "talking product package" not in video_prompt["negative_prompt"]
    assert "Do not create talking product packages" not in video_prompt["negative_prompt"]


def test_provider_modules_dry_run_write_their_own_prompt_packages(tmp_path) -> None:
    module = load_tool()
    prompt_dir = tmp_path / "Prompt"
    context = {
        "prompt_dir": str(prompt_dir),
        "segment": {"segment_id": "seg_001", "dialogue_ids": ["srt_0001"], "planned_video_duration": 4.2, "tasks": {}},
        "shot": {"shot_id": "shot_001", "summary": "demo shot"},
        "scene": {"scene_id": "scene_001", "summary": "demo scene"},
        "dialogue_index": {"srt_0001": {"dialogue": {"dialogue": "测试口播"}}},
        "references": [{"kind": "target_frame", "role": "TARGET_FRAME", "working_path": "target.png"}],
        "reference_manifests": {},
    }

    module_specs = [
        module.image_module_for("openai", "gpt-image-1"),
        module.image_module_for("gemini", "nano-banana"),
        module.image_module_for("xai", "grok-image"),
        module.video_module_for("openai", "gpt-video"),
        module.video_module_for("gemini", "veo"),
        module.video_module_for("xai", "grok-imagine-video"),
        module.video_module_for("wan", "wan2.7"),
        module.lipsync_module_for("sync.so", "lipsync-2"),
    ]

    profiles = []
    for index, provider_module in enumerate(module_specs, start=1):
        result = provider_module.dry_run_prompt(context, prompt_dir, f"asset_{index:02d}")
        prompt_path = Path(result["prompt_path"])
        payload = json.loads(prompt_path.read_text(encoding="utf-8"))
        profiles.append(payload["provider_profile"])
        assert payload["prompt"]
        assert payload["template_source"].startswith("Ref_05_02_")

    assert profiles == [
        "image_gpt",
        "image_gemini",
        "image_grok",
        "video_gpt",
        "video_gemini",
        "video_grok",
        "video_wan",
        "lipsync_syncso",
    ]


def test_wan_rtv_model_uses_dedicated_module_template_and_reference_video(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path, need_lipsync=True)
    variables = json.loads((workspace / "SessionContext" / "Variables.json").read_text(encoding="utf-8"))
    storyboard = json.loads((workspace / "SessionOutput" / "storyboard" / "srt_storyboard.json").read_text(encoding="utf-8"))
    plan = json.loads((workspace / "SessionOutput" / "storyboard" / "video_generation_plan.json").read_text(encoding="utf-8"))
    args = module.parse_args(["--workspace", str(workspace), "--video-provider", "wan", "--video-model", "wan2.7-r2v"])
    result = {"created_files": []}

    provider_module = module.video_module_for("wan", "wan2.7-r2v")
    prompt_package = provider_module.build_prompt_package({
        "workspace": str(workspace),
        "prompt_dir": str(workspace / "S9_05_02_VideoPlanExecutor" / "Prompt"),
        "segment": plan["shots"][0]["scenes"][0]["segments"][0],
        "shot": plan["shots"][0],
        "scene": plan["shots"][0]["scenes"][0],
        "dialogue_index": module.flatten_dialogues(storyboard),
    })

    assert provider_module.__name__.endswith("video_wan_rtv")
    assert prompt_package["schema_version"] == "analysis_v1_05_02_video_prompt_wan_rtv_0.1"
    assert prompt_package["provider_profile"] == "video_wan_rtv"
    assert prompt_package["template_source"] == "Ref_05_02_Video_Wan_R2V.md"
    assert prompt_package["reference_video"] == "Video_Wan_R2V.mp4"
    assert "VIDEO_WAN_R2V_PROMPT" in prompt_package["template_blocks"]

    module.ensure_tool_dirs(workspace)
    module.copy_inputs_to_working(workspace, variables, storyboard, plan, result, args)

    assert (workspace / "S9_05_02_VideoPlanExecutor" / "Prompt" / "Ref_05_02_Video_Wan_R2V.md").exists()
    reference_video = workspace / "S9_05_02_VideoPlanExecutor" / "Working" / "Video_Wan_R2V.mp4"
    assert reference_video.exists()
    assert reference_video.stat().st_size > 0
    assert "S9_05_02_VideoPlanExecutor/Working/Video_Wan_R2V.mp4" in result["created_files"]


def test_xai_video_duration_uses_planned_dialogue_duration() -> None:
    module = load_tool()

    assert module.provider_video_seconds({"provider": "xai"}, 3.65) == 4


def test_wan_rtv_duration_within_30_percent_tolerance_uses_max_10_seconds() -> None:
    module = load_tool()
    rtv_module = module.video_module_for("wan", "wan2.7-r2v")

    rtv_config = {"provider": "wan", "model": "wan2.7-r2v"}
    assert module.provider_video_seconds(rtv_config, 4.0) == 10
    assert module.provider_video_seconds(rtv_config, 12.9) == 10
    assert rtv_module.provider_video_seconds(rtv_config, 13.0) == 10
    assert module.provider_video_seconds({"provider": "wan", "model": "wan2.7-i2v-2026-04-25"}, 4.0) == 4


def test_wan_rtv_generation_payload_matches_reference_image_aspect(tmp_path, monkeypatch) -> None:
    try:
        from PIL import Image
    except Exception:
        pytest.skip("PIL is required for aspect-based Wan RTV payload test")
    module = load_tool()
    rtv_module = module.video_module_for("wan", "wan2.7-r2v")
    prompt_path = tmp_path / "Prompt" / "PromptRendered_asset_VideoPrompt.json"
    reference_video = tmp_path / "Working" / "Video_Wan_R2V.mp4"
    write_json(prompt_path, {"prompt": "生成竖屏口播视频"})
    reference_video.parent.mkdir(parents=True, exist_ok=True)
    reference_video.write_bytes(b"fake-mp4")
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(rtv_module, "dashscope_upload_file", lambda api_key, model, path: f"oss://{Path(path).name}")

    def fake_post(url, payload, headers, timeout=120):
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return {"output": {"task_id": "task_123"}}

    monkeypatch.setattr(rtv_module, "post_json_request", fake_post)
    monkeypatch.setattr(rtv_module, "get_json_request", lambda url, headers, timeout=120: {"output": {"task_status": "SUCCEEDED", "video_url": "https://example.test/video.mp4"}})
    monkeypatch.setattr(rtv_module, "download_binary", lambda url, path, headers=None, timeout=600: Path(path).write_bytes(b"fake-video"))

    def run_with_image(image_size: tuple[int, int], expected_video_size: str) -> None:
        calls.clear()
        reference_image = tmp_path / f"first_frame_{expected_video_size}.jpg"
        output_path = tmp_path / "Working" / f"asset_{expected_video_size}_Video_Raw.mp4"
        Image.new("RGB", image_size, (240, 240, 240)).save(reference_image)
        response = rtv_module.generate(
            {
                "config": {"provider": "wan", "model": "wan2.7-r2v", "api_key": "test-key"},
                "reference_images": [str(reference_image)],
                "reference_videos": [str(reference_video)],
                "duration_seconds": 12.0,
                "timeout_seconds": 60,
            },
            prompt_path,
            output_path,
        )
        payload = calls[0]["payload"]
        assert payload["parameters"]["duration"] == 10
        assert payload["parameters"]["size"] == expected_video_size
        assert [item["type"] for item in payload["input"]["media"]] == ["first_frame", "reference_video"]
        assert response["size"] == expected_video_size
        assert output_path.read_bytes() == b"fake-video"

    run_with_image((720, 1280), "720*1280")
    run_with_image((1280, 720), "1280*720")


def test_gemini_image_generation_uses_nano_banana_v1beta_query_key_payload(tmp_path, monkeypatch) -> None:
    module = load_tool()
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"fake-jpeg")
    output = tmp_path / "generated.png"
    prompt_path = tmp_path / "gemini_prompt.json"
    write_json(prompt_path, {"prompt": "生成一张 9:16 口播首帧"})
    calls: list[dict[str, object]] = []
    encoded = base64.b64encode(b"fake-image").decode("ascii")

    def fake_post(url, payload, headers, timeout=120):
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return {"candidates": [{"content": {"parts": [{"inlineData": {"data": encoded}}]}}]}

    image_module = module.image_module_for("gemini", "gemini-3.1-flash-image-preview")
    monkeypatch.setattr(image_module.common, "post_json_request", fake_post)

    response = module.generate_image_with_provider(
        {"provider": "gemini", "model": "gemini-3.1-flash-image-preview", "api_key": "test-key"},
        prompt_path,
        output,
        [reference],
        45,
    )

    assert output.read_bytes() == b"fake-image"
    assert response["model"] == "gemini-3.1-flash-image"
    assert response["requested_model"] == "gemini-3.1-flash-image-preview"
    assert calls[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image:generateContent?key=test-key"
    assert calls[0]["headers"] == {}
    payload = calls[0]["payload"]
    assert payload["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"text": "生成一张 9:16 口播首帧"}
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
    assert "data" in parts[1]["inline_data"]


def test_openai_gpt_image_generation_still_uses_openai_endpoints(tmp_path, monkeypatch) -> None:
    module = load_tool()
    reference_a = tmp_path / "reference-a.png"
    reference_b = tmp_path / "reference-b.png"
    reference_a.write_bytes(b"fake-png-a")
    reference_b.write_bytes(b"fake-png-b")
    output = tmp_path / "openai-generated.png"
    prompt_path = tmp_path / "openai_prompt.json"
    write_json(prompt_path, {"prompt": "生成一张 9:16 口播首帧"})
    calls: list[dict[str, object]] = []
    encoded = base64.b64encode(b"openai-image").decode("ascii")

    def fake_multipart(url, fields, files, headers, timeout=120):
        calls.append({"kind": "multipart", "url": url, "fields": fields, "files": files, "headers": headers, "timeout": timeout})
        return {"data": [{"b64_json": encoded}]}

    def fake_json(url, payload, headers, timeout=120):
        calls.append({"kind": "json", "url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return {"data": [{"b64_json": encoded}]}

    image_module = module.image_module_for("openai", "gpt-image-1.5")
    monkeypatch.setattr(image_module.common, "post_multipart_request", fake_multipart)
    monkeypatch.setattr(image_module.common, "post_json_request", fake_json)

    response = module.generate_image_with_provider(
        {"provider": "openai", "model": "gpt-image-1.5", "api_key": "openai-test-key"},
        prompt_path,
        output,
        [reference_a, reference_b],
        60,
    )

    assert output.read_bytes() == b"openai-image"
    assert response["provider"] == "openai"
    assert response["model"] == "gpt-image-1.5"
    assert calls == [
        {
            "kind": "multipart",
            "url": "https://api.openai.com/v1/images/edits",
            "fields": {"model": "gpt-image-1.5", "prompt": "生成一张 9:16 口播首帧", "size": "1024x1536"},
            "files": [("image[]", reference_a), ("image[]", reference_b)],
            "headers": {"Authorization": "Bearer openai-test-key"},
            "timeout": 60,
        }
    ]

    calls.clear()
    write_json(prompt_path, {"prompt": "纯文字生成"})
    module.generate_image_with_provider(
        {"provider": "openai", "model": "gpt-image-1.5", "api_key": "openai-test-key"},
        prompt_path,
        output,
        [],
        30,
    )
    assert calls == [
        {
            "kind": "json",
            "url": "https://api.openai.com/v1/images/generations",
            "payload": {"model": "gpt-image-1.5", "prompt": "纯文字生成", "size": "1024x1536"},
            "headers": {"Authorization": "Bearer openai-test-key"},
            "timeout": 30,
        }
    ]


def test_gemini_tts_matches_step03_query_key_access(tmp_path, monkeypatch) -> None:
    module = load_tool()
    output = tmp_path / "speech.wav"
    calls: list[dict[str, object]] = []
    pcm = struct.pack("<" + "h" * 240, *([0] * 240))
    encoded = base64.b64encode(pcm).decode("ascii")

    def fake_post(url, payload, headers, timeout=120):
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/pcm", "data": encoded}}]}}]}

    monkeypatch.setattr(module, "post_json_request", fake_post)
    monkeypatch.setattr(module, "apply_provider_proxy", lambda provider: (_ for _ in ()).throw(AssertionError("TTS should match 03_01 and not force provider proxy")))

    response = module.generate_tts_with_provider(
        {"provider": "google", "model": "gemini-3.1-flash-tts-preview", "api_key": "google-tts-key", "voice": "Aoede"},
        "请用自然中文口播读出这句话。",
        output,
        180,
    )

    assert output.read_bytes().startswith(b"RIFF")
    assert response["provider"] == "google"
    assert response["model"] == "gemini-3.1-flash-tts-preview"
    assert response["voice"] == "Aoede"
    assert calls[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-tts-preview:generateContent?key=google-tts-key"
    assert calls[0]["headers"] == {}
    assert calls[0]["timeout"] == 180
    payload = calls[0]["payload"]
    assert payload["contents"] == [{"parts": [{"text": "请用自然中文口播读出这句话。"}]}]
    assert payload["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert payload["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Aoede"


def test_xai_video_does_not_force_mihomo_proxy(tmp_path, monkeypatch) -> None:
    module = load_tool()
    output = tmp_path / "video.mp4"
    prompt_path = tmp_path / "video_prompt.json"
    write_json(prompt_path, {"prompt": "make a short vertical video"})
    calls: list[dict[str, object]] = []

    def fake_post(url, payload, headers, timeout=120):
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return {"url": "https://cdn.example/video.mp4"}

    def fake_download(url, output_path, headers=None, timeout=600):
        calls.append({"download_url": url, "headers": headers or {}, "timeout": timeout})
        output_path.write_bytes(b"video")

    video_module = module.video_module_for("xai", "grok-imagine-video")
    monkeypatch.setattr(video_module.common, "post_json_request", fake_post)
    monkeypatch.setattr(video_module.common, "download_binary", fake_download)

    response = module.generate_video_with_provider(
        {"provider": "xai", "model": "grok-imagine-video", "api_key": "xai-key"},
        prompt_path,
        output,
        [],
        3.2,
        120,
    )

    assert output.read_bytes() == b"video"
    assert response["provider"] == "xai"
    assert response["model"] == "grok-imagine-video"
    assert calls[0]["url"] == "https://api.x.ai/v1/videos/generations"
    assert calls[0]["headers"] == {"Authorization": "Bearer xai-key"}
    assert calls[0]["payload"]["duration"] == 3
    assert calls[1]["download_url"] == "https://cdn.example/video.mp4"


def test_sync_lipsync_does_not_force_mihomo_proxy(tmp_path, monkeypatch) -> None:
    module = load_tool()
    video = tmp_path / "input.mp4"
    audio = tmp_path / "input.wav"
    output = tmp_path / "sync.mp4"
    request_path = tmp_path / "request.json"
    status_path = tmp_path / "status.json"
    create_response_path = tmp_path / "create.json"
    video.write_bytes(b"video")
    write_wav(audio)
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, status_code=200, payload=None, content=b""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = json.dumps(self._payload)
            self._content = content

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def iter_content(self, chunk_size=1024):
            yield self._content

        def close(self):
            return None

    class FakeRequests:
        @staticmethod
        def post(url, headers=None, data=None, files=None, timeout=120):
            calls.append({"method": "post", "url": url, "headers": headers or {}, "data": data or {}, "file_keys": sorted((files or {}).keys()), "timeout": timeout})
            return FakeResponse(payload={"id": "sync-123"})

        @staticmethod
        def get(url, headers=None, stream=False, timeout=60):
            calls.append({"method": "get", "url": url, "headers": headers or {}, "stream": stream, "timeout": timeout})
            if url.endswith("/sync-123"):
                return FakeResponse(payload={"status": "COMPLETED", "outputUrl": "https://cdn.example/sync.mp4"})
            return FakeResponse(content=b"synced-video")

    monkeypatch.setitem(sys.modules, "requests", FakeRequests)
    monkeypatch.setattr(module, "apply_provider_proxy", lambda provider: (_ for _ in ()).throw(AssertionError("Sync.so should use its own requests path, not force mihomo")))

    response = module.run_lipsync_with_provider(
        {"provider": "sync.so", "model": "lipsync-2", "api_key": "sync-key"},
        video,
        audio,
        output,
        request_path,
        status_path,
        create_response_path,
        120,
    )

    assert output.read_bytes() == b"synced-video"
    assert response["provider"] == "sync.so"
    assert response["generation_id"] == "sync-123"
    assert calls[0]["url"] == "https://api.sync.so/v2/generate"
    assert calls[0]["headers"] == {"x-api-key": "sync-key"}
    assert calls[0]["file_keys"] == ["audio", "video"]
    assert calls[1]["url"] == "https://api.sync.so/v2/generate/sync-123"
    assert calls[2]["url"] == "https://cdn.example/sync.mp4"


def test_need_lipsync_false_retimes_video_and_replaces_audio(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path, need_lipsync=False)
    calls: list[str] = []
    install_provider_test_doubles(module, monkeypatch, calls)

    result = module.run(args_for(module, workspace))

    assert result["status"] == "completed"
    assert calls == ["image", "video", "audio_video_sync", "tail"]
    assert (workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_Video_Final.mp4").read_bytes() == b"audio-video-sync"
    assert result["summary"]["audio_video_sync_completed_count"] == 1


def test_bound_video_segment_materializes_without_video_model(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path, need_lipsync=False)
    source_video = workspace / "SessionOutput" / "storyboard" / "assets" / "videos" / "upload_001.mp4"
    source_video.parent.mkdir(parents=True, exist_ok=True)
    source_video.write_bytes(b"bound-video")
    plan_path = workspace / "SessionOutput" / "storyboard" / "video_generation_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    segment = plan["shots"][0]["scenes"][0]["segments"][0]
    segment["status"] = "ready"
    segment["first_frame"] = {
        "source_type": "bound_video",
        "source_path": "SessionOutput/storyboard/assets/videos/upload_001.mp4",
        "requires_generated_image_before_video": False,
        "planned_generated_image_path": "",
        "materialize_first_frame": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""},
    }
    segment["tail_frame"] = {"planned_path": "SessionOutput/storyboard/Working/srt_0001_TailFrame.jpg", "available": False, "continuation_allowed": True}
    segment["tasks"].update({
        "need_image_prompt": False,
        "need_image": False,
        "need_video_prompt": False,
        "need_video": False,
        "need_lipsync": False,
        "need_audio_video_sync": True,
        "need_sync": True,
        "sync_mode": "audio_replace_retime",
        "lipsync_reason": "existing_video_bound_complete",
    })
    segment["existing_video"] = {
        "path": "SessionOutput/storyboard/assets/videos/upload_001.mp4",
        "materialize_video": {
            "required": True,
            "copy_from_path": "SessionOutput/storyboard/assets/videos/upload_001.mp4",
            "copy_to_path": "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4",
            "source_type": "bound_dialogue_video",
        },
    }
    segment["planned_outputs"]["image_prompt_path"] = ""
    segment["planned_outputs"]["image_path"] = ""
    segment["planned_outputs"]["video_prompt_path"] = ""
    write_json(plan_path, plan)
    calls: list[str] = []
    install_provider_test_doubles(module, monkeypatch, calls)

    result = module.run(args_for(module, workspace, execute_image=False, execute_video=False, execute_lipsync=False))

    assert result["status"] == "completed"
    assert calls == ["audio_video_sync", "tail"]
    assert (workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_Video_Final.mp4").read_bytes() == b"audio-video-sync"
    segment_result = result["segments"][0]
    assert segment_result["completed_by_bound_video"] is True
    assert segment_result["source_video_path"] == "SessionOutput/storyboard/assets/videos/upload_001.mp4"
    assert segment_result["sync"]["source"] == "ffmpeg_audio_replace_retime"
    assert result["summary"]["bound_video_completed_count"] == 1


def test_force_cleans_tool_dir_but_backs_up_storyboard_outputs(tmp_path, monkeypatch) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path, need_lipsync=False)
    existing = workspace / "SessionOutput" / "storyboard" / "Working" / "srt_0001_Video_Final.mp4"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"old-final")
    stale_tool_file = workspace / "S9_05_02_VideoPlanExecutor" / "Working" / "stale.txt"
    stale_tool_file.parent.mkdir(parents=True, exist_ok=True)
    stale_tool_file.write_text("stale", encoding="utf-8")
    calls: list[str] = []
    install_provider_test_doubles(module, monkeypatch, calls)

    result = module.run(args_for(module, workspace, force=True))

    assert result["status"] == "completed"
    assert existing.read_bytes() == b"audio-video-sync"
    backups = list((workspace / "SessionOutput" / "storyboard" / "assets" / "history").glob("batch_*_05_02_overwrite_backup/srt_0001_Video_Final.mp4"))
    assert backups
    assert any(path.read_bytes() == b"old-final" for path in backups)
    manifests = list((workspace / "SessionOutput" / "storyboard" / "assets" / "history").glob("batch_*_05_02_overwrite_backup/manifest.json"))
    assert manifests
    manifest_payload = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest_payload["items"][0]["history_path"].startswith("SessionOutput/storyboard/assets/history/")
    assert not stale_tool_file.exists()


def test_missing_default_model_config_blocks_without_fake_provider(tmp_path) -> None:
    module = load_tool()
    workspace = make_workspace(tmp_path, need_lipsync=True)
    variables_path = workspace / "SessionContext" / "Variables.json"
    variables = json.loads(variables_path.read_text(encoding="utf-8"))
    variables["default_image_config"] = {}
    write_json(variables_path, variables)

    result = module.run(args_for(module, workspace))

    assert result["status"] == "failed"
    assert result["segments"][0]["status"] == "failed"
    assert "Default image model is not configured" in result["segments"][0]["error"]
