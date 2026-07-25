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


def existing_working_slot_path(root: Path, asset_key: str, slot: str, exts: set[str], preferred_suffix: str = ".mp4") -> str:
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
        from opcrew_backend.koubo.koubo_storyboard.video_only_plan_routes import register_video_only_plan_routes

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
                video_plan_with_hash=lambda payload: payload,
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
            register_video_only_plan_routes(router, deps)
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
        from opcrew_backend.koubo.koubo_storyboard.video_only_plan_routes import register_video_only_plan_routes

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
                video_plan_with_hash=lambda payload: payload,
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
            register_video_only_plan_routes(router, deps)
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
        from opcrew_backend.koubo.koubo_storyboard.video_only_plan_routes import register_video_only_plan_routes

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
                video_plan_with_hash=lambda payload: payload,
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
            register_video_only_plan_routes(router, deps)
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


if __name__ == "__main__":
    unittest.main()
