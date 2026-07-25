from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (REPO_ROOT / "backend",):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from fastapi import HTTPException  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard import storyboard_plan_services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.asset_services import register_asset_services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.storyboard_plan_services import register_storyboard_plan_services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.video_plan_load_services import register_video_plan_load_services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.working_asset_services import register_working_asset_services  # noqa: E402


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def text(value: Any, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def default_working_assets() -> dict[str, Any]:
    return {
        "audio": {"slot": "Audio_Final", "source_type": "", "path": ""},
        "images": [
            {"slot": "Image_01", "source_type": "", "path": ""},
            {"slot": "Image_02", "source_type": "", "path": ""},
        ],
        "video": {"slot": "Video_Final", "source_type": "", "path": ""},
    }


def ensure_working_assets(item: dict[str, Any]) -> dict[str, Any]:
    assets = item.get("working_assets") if isinstance(item.get("working_assets"), dict) else {}
    if not assets:
        assets = default_working_assets()
        item["working_assets"] = assets
    return assets


def ensure_dialogue_working_assets(item: dict[str, Any]) -> dict[str, Any]:
    return ensure_working_assets(item)


def dialogue_asset_key(item: dict[str, Any]) -> str:
    return text(item.get("srt_id") or item.get("dialogue_id") or "dialogue")


def safe_name(value: str, fallback: str) -> str:
    name = Path(str(value or "")).name.strip().replace("/", "_").replace("\\", "_")
    return name or fallback


def safe_workspace_rel(root: Path, rel_path: str):
    rel_path = str(rel_path or "").strip()
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise HTTPException(status_code=400, detail="Unsafe path")
    return rel_path, target


def source_storyboard(tool: str, dialogue: str) -> dict[str, Any]:
    return {
        "schema_version": "analysis_v1_srt_storyboard_0.2",
        "tool": tool,
        "shots": [
            {
                "shot_id": "shot_001",
                "title": "shot_001",
                "start": 0.0,
                "end": 1.0,
                "duration": 1.0,
                "scenes": [
                    {
                        "scene_id": "scene_001",
                        "title": "scene_001",
                        "start": 0.0,
                        "end": 1.0,
                        "duration": 1.0,
                        "dialogue_items": [
                            {
                                "srt_id": "srt_0001",
                                "dialogue": dialogue,
                                "start": 0.0,
                                "end": 1.0,
                                "duration": 1.0,
                                "image_path": "SessionOutput/visual/srt_frames/srt_0001.jpg",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def edit_storyboard(tool: str, dialogue: str, source_hash: str = "") -> dict[str, Any]:
    payload = {
        "schema_version": "koubo_storyboard_edit_0.1",
        "title": "故事版（口播）",
        "source_type": "analysis_v1_storyboard",
        "source_path": "SessionOutput/storyboard/srt_storyboard.json",
        "created_from_tool": tool,
        "analysis_task_id": 112,
        "analysis_session_id": 171,
        "shots": [
            {
                "shot_id": "shot_001",
                "shot_name": "shot_001",
                "start": 0.0,
                "end": 1.0,
                "duration": 1.0,
                "scenes": [
                    {
                        "scene_id": "scene_001",
                        "scene_name": "scene_001",
                        "start": 0.0,
                        "end": 1.0,
                        "duration": 1.0,
                        "dialogues": [
                            {
                                "dialogue_id": "scene_001_dialogue_001",
                                "scene_id": "scene_001",
                                "dialogue_index": 1,
                                "srt_id": "srt_0001",
                                "srt_ids": ["srt_0001"],
                                "dialogue_asset_key": "srt_0001",
                                "text": dialogue,
                                "start": 0.0,
                                "end": 1.0,
                                "duration": 1.0,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    if source_hash:
        payload["source_storyboard_sha256"] = source_hash
        payload["source_storyboard_tool"] = tool
    return payload


class KouboStoryboardStaleEditContractTest(unittest.TestCase):
    def make_services(self, workspace: Path) -> SimpleNamespace:
        ns = SimpleNamespace(
            workspace_for=lambda task: workspace,
            read_json=read_json,
            write_json=write_json,
            text=text,
            number=number,
            now_ms=lambda: 1780999609968,
            ensure_working_assets=ensure_working_assets,
            ensure_dialogue_working_assets=ensure_dialogue_working_assets,
            dialogue_asset_key=dialogue_asset_key,
            asset_pool_meta=lambda _workspace: {
                "uploaded_images": [],
                "uploaded_audios": [],
                "uploaded_videos": [],
                "history_versions": [],
            },
            video_plan_settings=lambda payload: payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
            source_asset_groups_from_source=lambda _workspace, _source: [],
        )
        register_working_asset_services(ns)
        register_storyboard_plan_services(ns)
        register_video_plan_load_services(ns)
        return ns

    def test_load_plan_ignores_legacy_edit_from_older_storyboard_tool_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            old_source = {
                **source_storyboard("04_03_StoryBoardQuick", "旧化橘红文案"),
                "updated_from": "SessionOutput/storyboard/koubo_storyboard_edit.json",
            }
            new_source = source_storyboard("04_02_StoryBoard", "新润喉片文案")
            write_json(workspace / "SessionOutput/storyboard/srt_storyboard.json", old_source)
            write_json(workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json", edit_storyboard("04_03_StoryBoardQuick", "旧化橘红文案"))
            write_json(workspace / "S7_04_02_StoryBoard/Output/srt_storyboard.json", new_source)
            os.utime(workspace / "S7_04_02_StoryBoard/Output/srt_storyboard.json", (2000, 2000))
            services = self.make_services(workspace)

            plan, meta = services.load_plan({"id": 112, "session_id": 171})

            self.assertEqual(plan["shots"][0]["scenes"][0]["dialogues"][0]["text"], "新润喉片文案")
            self.assertEqual(meta["has_saved_edit"], False)
            self.assertEqual(meta["stale_edit_ignored"], True)
            self.assertEqual(meta["active_source_path"], "S7_04_02_StoryBoard/Output/srt_storyboard.json")

    def test_load_plan_keeps_edit_when_source_hash_matches_latest_storyboard_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = source_storyboard("04_02_StoryBoard", "新润喉片文案")
            source_hash = storyboard_plan_services.stable_storyboard_sha256(source)
            write_json(workspace / "SessionOutput/storyboard/srt_storyboard.json", source)
            write_json(workspace / "S7_04_02_StoryBoard/Output/srt_storyboard.json", source)
            write_json(workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json", edit_storyboard("04_02_StoryBoard", "手动编辑后的新文案", source_hash))
            services = self.make_services(workspace)

            plan, meta = services.load_plan({"id": 112, "session_id": 171})

            self.assertEqual(plan["shots"][0]["scenes"][0]["dialogues"][0]["text"], "手动编辑后的新文案")
            self.assertEqual(meta["has_saved_edit"], True)
            self.assertEqual(meta["stale_edit_ignored"], False)

    def test_stale_edit_save_is_rejected_instead_of_overwriting_new_storyboard_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            new_source = source_storyboard("04_02_StoryBoard", "新润喉片文案")
            write_json(workspace / "SessionOutput/storyboard/srt_storyboard.json", new_source)
            write_json(workspace / "S7_04_02_StoryBoard/Output/srt_storyboard.json", new_source)
            services = self.make_services(workspace)

            with self.assertRaises(HTTPException) as error:
                services.save_edit_and_source_storyboard(
                    {"id": 112, "session_id": 171},
                    workspace,
                    edit_storyboard("04_03_StoryBoardQuick", "旧化橘红文案"),
                )

            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(error.exception.detail["code"], "storyboard_source_changed")

    def test_analysis_v1_storyboard_steps_archive_saved_edit_after_source_refresh(self) -> None:
        source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py").read_text(encoding="utf-8")

        self.assertIn("analysis_v1_archive_storyboard_edit_after_source_refresh", source)
        self.assertIn('"04_02", "04_03"', source)
        self.assertIn("analysis_v1_storyboard_source_refreshed", source)
        self.assertIn("storyboard_edit.archived", source)
        self.assertIn("analysis_v1_archive_storyboard_working_for_full_grouping", source)
        self.assertIn("04_02_full_grouping_reset_working", source)
        self.assertIn("analysis_v1_reset_storyboard_generated_plan_state", source)
        self.assertIn("video_only_generation_plan.json", source)

    def test_storyboard_video_delete_archives_video_only_raw_tail_and_clears_execution_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            working = workspace / "SessionOutput/storyboard/Working"
            working.mkdir(parents=True, exist_ok=True)
            for name in (
                "srt_0001_Video_Final.mp4",
                "srt_0001_Video_Raw.mp4",
                "srt_0001_TailFrame.jpg",
                "srt_0001_VideoPrompt.json",
            ):
                (working / name).write_bytes(b"payload")
            write_json(
                workspace / "SessionOutput/storyboard/video_only_plan_execution_state.json",
                {
                    "schema_version": "analysis_v1_video_only_plan_execution_state_0.1",
                    "status": "completed",
                    "current_asset_key": "srt_0001",
                    "current_step": "video",
                    "current_step_status": "completed_working",
                    "segments": {
                        "srt_0001": {
                            "steps": {
                                "prompt": {"status": "completed_working"},
                                "video": {"status": "completed_working"},
                                "confirm_final": {"status": "completed_working"},
                            }
                        }
                    },
                },
            )
            dialogue = {
                "dialogue_id": "scene_001_dialogue_001",
                "srt_id": "srt_0001",
                "dialogue_asset_key": "srt_0001",
                "working_assets": {
                    "video": {
                        "slot": "Video_Final",
                        "source_type": "generated",
                        "path": "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4",
                    }
                },
            }
            previous_plan = {"shots": [{"scenes": [{"scene_id": "scene_001", "dialogues": [dialogue]}]}]}
            next_dialogue = json.loads(json.dumps(dialogue))
            next_dialogue["working_assets"]["video"] = {"slot": "Video_Final", "source_type": "", "path": ""}
            next_plan = {"shots": [{"scenes": [{"scene_id": "scene_001", "dialogues": [next_dialogue]}]}]}
            services = SimpleNamespace(
                read_json=read_json,
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                text=text,
                number=number,
                now_ms=lambda: 1780999609968,
                safe_name=safe_name,
            )
            register_asset_services(services)

            archived = services.archive_removed_generated_assets(workspace, previous_plan, next_plan, "storyboard_save_removed_generated_asset")

            self.assertEqual({Path(item["original_path"]).name for item in archived}, {"srt_0001_Video_Final.mp4", "srt_0001_Video_Raw.mp4", "srt_0001_TailFrame.jpg"})
            self.assertFalse((working / "srt_0001_Video_Final.mp4").exists())
            self.assertFalse((working / "srt_0001_Video_Raw.mp4").exists())
            self.assertFalse((working / "srt_0001_TailFrame.jpg").exists())
            self.assertTrue((working / "srt_0001_VideoPrompt.json").exists())
            state = read_json(workspace / "SessionOutput/storyboard/video_only_plan_execution_state.json")
            steps = state["segments"]["srt_0001"]["steps"]
            self.assertEqual(steps, {"prompt": {"status": "completed_working"}})
            self.assertEqual(state["current_step"], "")
            self.assertEqual(state["current_step_status"], "")

    def test_storyboard_uploaded_video_delete_archives_matching_video_only_raw_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            working = workspace / "SessionOutput/storyboard/Working"
            working.mkdir(parents=True, exist_ok=True)
            (working / "srt_0001_Video_Raw.mp4").write_bytes(b"raw")
            (working / "srt_0001_TailFrame.jpg").write_bytes(b"tail")
            (working / "srt_0001_VideoPrompt.json").write_bytes(b"prompt")
            upload_rel = "SessionOutput/storyboard/assets/videos/uploaded_srt_0001.mp4"
            (workspace / upload_rel).parent.mkdir(parents=True, exist_ok=True)
            (workspace / upload_rel).write_bytes(b"upload")
            dialogue = {
                "dialogue_id": "scene_001_dialogue_001",
                "srt_id": "srt_0001",
                "dialogue_asset_key": "srt_0001",
                "working_assets": {
                    "video": {
                        "slot": "Video_Final",
                        "source_type": "upload",
                        "path": upload_rel,
                    }
                },
            }
            previous_plan = {"shots": [{"scenes": [{"scene_id": "scene_001", "dialogues": [dialogue]}]}]}
            next_dialogue = json.loads(json.dumps(dialogue))
            next_dialogue["working_assets"]["video"] = {"slot": "Video_Final", "source_type": "", "path": ""}
            next_plan = {"shots": [{"scenes": [{"scene_id": "scene_001", "dialogues": [next_dialogue]}]}]}
            services = SimpleNamespace(
                read_json=read_json,
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                text=text,
                number=number,
                now_ms=lambda: 1780999609968,
                safe_name=safe_name,
            )
            register_asset_services(services)

            archived = services.archive_removed_generated_assets(workspace, previous_plan, next_plan, "storyboard_save_removed_generated_asset")

            self.assertEqual({Path(item["original_path"]).name for item in archived}, {"srt_0001_Video_Raw.mp4", "srt_0001_TailFrame.jpg"})
            self.assertFalse((working / "srt_0001_Video_Raw.mp4").exists())
            self.assertFalse((working / "srt_0001_TailFrame.jpg").exists())
            self.assertTrue((working / "srt_0001_VideoPrompt.json").exists())
            self.assertTrue((workspace / upload_rel).exists())

    def test_clear_new_image_archives_standard_image_slot_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            working = workspace / "SessionOutput/storyboard/Working"
            working.mkdir(parents=True, exist_ok=True)
            (working / "srt_0005_01_Image_New.png").write_bytes(b"tail-materialized")
            (working / "srt_0005_01_Image_New.jpg").write_bytes(b"old-bound-image")
            dialogue = {
                "dialogue_id": "scene_001_dialogue_003",
                "srt_id": "srt_0005_01",
                "dialogue_asset_key": "srt_0005_01",
                "working_assets": {
                    "images": [
                        {"slot": "Image_New", "source_type": "", "path": ""},
                        {"slot": "Image_02", "source_type": "", "path": ""},
                    ],
                    "video": {"slot": "Video_Final", "source_type": "", "path": ""},
                },
            }
            plan = {"shots": [{"scenes": [{"scene_id": "scene_001", "dialogues": [dialogue]}]}]}
            services = SimpleNamespace(
                read_json=read_json,
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                text=text,
                number=number,
                now_ms=lambda: 1780999609968,
                safe_name=safe_name,
            )
            register_asset_services(services)

            plan, old_path, deleted = services.clear_asset_from_plan(workspace, plan, "scene_001_dialogue_003", "image")

            self.assertEqual(old_path, "")
            self.assertTrue(deleted)
            self.assertFalse((working / "srt_0005_01_Image_New.png").exists())
            self.assertFalse((working / "srt_0005_01_Image_New.jpg").exists())
            manifests = list((workspace / "SessionOutput/storyboard/assets/history").glob("*/manifest.json"))
            archived_names = set()
            for manifest_path in manifests:
                manifest = read_json(manifest_path)
                archived_names.update(Path(item["original_path"]).name for item in manifest.get("items", []))
            self.assertEqual(archived_names, {"srt_0005_01_Image_New.png", "srt_0005_01_Image_New.jpg"})
            next_dialogue = plan["shots"][0]["scenes"][0]["dialogues"][0]
            self.assertEqual(next_dialogue["working_assets"]["images"][0]["path"], "")
            self.assertEqual(next_dialogue.get("bound_image_path", ""), "")

    def test_storyboard_save_removed_new_image_archives_standard_image_slot_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            working = workspace / "SessionOutput/storyboard/Working"
            working.mkdir(parents=True, exist_ok=True)
            (working / "srt_0005_01_Image_New.jpg").write_bytes(b"bound")
            (working / "srt_0005_01_Image_New.png").write_bytes(b"tail-materialized")
            previous_dialogue = {
                "dialogue_id": "scene_001_dialogue_003",
                "srt_id": "srt_0005_01",
                "dialogue_asset_key": "srt_0005_01",
                "working_assets": {
                    "audio": {"slot": "Audio_Final", "source_type": "", "path": ""},
                    "images": [
                        {"slot": "Image_New", "source_type": "generated", "path": "SessionOutput/storyboard/Working/srt_0005_01_Image_New.jpg"},
                        {"slot": "Image_02", "source_type": "", "path": ""},
                    ],
                    "video": {"slot": "Video_Final", "source_type": "", "path": ""},
                },
            }
            next_dialogue = json.loads(json.dumps(previous_dialogue))
            next_dialogue["working_assets"]["images"][0] = {"slot": "Image_New", "source_type": "", "path": ""}
            next_dialogue["bound_image_path"] = ""
            previous_plan = {"shots": [{"scenes": [{"scene_id": "scene_001", "dialogues": [previous_dialogue]}]}]}
            next_plan = {"shots": [{"scenes": [{"scene_id": "scene_001", "dialogues": [next_dialogue]}]}]}
            services = SimpleNamespace(
                read_json=read_json,
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                text=text,
                number=number,
                now_ms=lambda: 1780999609968,
                safe_name=safe_name,
            )
            register_asset_services(services)

            archived = services.archive_removed_generated_assets(workspace, previous_plan, next_plan, "storyboard_save_removed_generated_asset")

            self.assertEqual({Path(item["original_path"]).name for item in archived}, {"srt_0005_01_Image_New.jpg", "srt_0005_01_Image_New.png"})
            self.assertFalse((working / "srt_0005_01_Image_New.jpg").exists())
            self.assertFalse((working / "srt_0005_01_Image_New.png").exists())


if __name__ == "__main__":
    unittest.main()
