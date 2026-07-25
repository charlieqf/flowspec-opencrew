from __future__ import annotations

import base64
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
from opcrew_backend.koubo.koubo_storyboard import clean_image_services, value_services  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.constants import ASSET_IMAGES_REL, CLEAN_IMAGE_REFERENCES_REL, CLEAN_IMAGE_REL, SOURCE_REL  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.io_utils import read_json, safe_workspace_rel, write_json  # noqa: E402


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "clean_image_routes.py"
PANEL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "CleanImagePanel.jsx"
MODULE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoardModule.jsx"


def workspace_files(workspace: Path) -> set[str]:
    if not workspace.exists():
        return set()
    return {path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file()}


class KouboCleanImageContractTest(unittest.TestCase):
    def make_services(self, workspace: Path, generate_image_bytes: Any | None = None) -> SimpleNamespace:
        ns = SimpleNamespace(
            ctx=SimpleNamespace(),
            read_json=read_json,
            write_json=write_json,
            safe_workspace_rel=safe_workspace_rel,
            workspace_for=lambda task: workspace,
            load_image_config=lambda provider="", model="", **_sc_kwargs: {"provider": provider or "fake", "model": model or "fake-image"},
            load_reference_image_config=lambda provider="", model="", **_sc_kwargs: ({"provider": provider or "fake", "model": model or "fake-image-ref"}, ""),
            generate_image_bytes=generate_image_bytes or (lambda _config, _prompt, _refs=None, _size="1536x1024", **_sc_kwargs: PNG_1X1),
            load_plan=lambda _task, **_sc_kwargs: ({}, {"uploaded_images": [], "uploaded_audios": [], "uploaded_videos": [], "history_versions": []}),
        )
        value_services.register_value_services(ns)
        clean_image_services.register_clean_image_services(ns)
        return ns

    def test_generate_route_offloads_provider_call_to_thread(self) -> None:
        source = ROUTES_PATH.read_text(encoding="utf-8")
        self.assertIn("import asyncio", source)
        self.assertIn("await asyncio.to_thread(deps.generate_clean_image, task, payload, sc=deps)", source)
        self.assertIn("/clean-image/references", source)

    def test_frontend_promote_preserves_current_storyboard_plan(self) -> None:
        panel = PANEL_PATH.read_text(encoding="utf-8")
        module = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("plan={plan}", module)
        self.assertIn("applyAssetPayload={applyAssetLibraryMetaResult}", module)
        self.assertIn("promoteCleanImageToAssetLibrary(currentTaskId, item.generation_id, { plan: planPayload() })", panel)
        self.assertIn("promoteCleanImageToDialogue(currentTaskId, item.generation_id, { dialogue_id: targetDialogueId, plan: planPayload() })", panel)

    def test_generate_sends_only_user_prompt_and_explicit_negative_prompt_to_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            write_json(workspace / SOURCE_REL, {"storyboard_context": "POLLUTING STORYBOARD CONTEXT"})
            calls: list[dict[str, Any]] = []

            def fake_generate(config: dict[str, str], prompt: str, refs: Any = None, size: str = "1536x1024", **_sc_kwargs) -> bytes:
                calls.append({"config": config, "prompt": prompt, "refs": refs, "size": size})
                return PNG_1X1

            services = self.make_services(workspace, fake_generate)
            result = services.generate_clean_image(
                {"id": 91, "session_id": 171, "workspace_dir": str(workspace)},
                {"prompt": "猫", "negative_prompt": "不要狗", "size": "1024x1024"},
            sc=services)

            self.assertEqual(calls[0]["prompt"], "猫\n\nNegative: 不要狗")
            self.assertNotIn("POLLUTING STORYBOARD CONTEXT", calls[0]["prompt"])
            self.assertEqual(calls[0]["size"], "1024x1024")
            self.assertTrue(result["output_path"].startswith(f"{CLEAN_IMAGE_REL}/{result['generation_id']}/"))

    def test_generate_writes_only_clean_scratch_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sentinel = workspace / ASSET_IMAGES_REL / "existing.png"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_bytes(PNG_1X1)
            before = workspace_files(workspace)

            services = self.make_services(workspace)
            result = services.generate_clean_image(
                {"id": 92, "session_id": 172, "workspace_dir": str(workspace)},
                {"prompt": "clean object"},
            sc=services)
            after = workspace_files(workspace)
            new_files = after - before

            expected_prefix = f"{CLEAN_IMAGE_REL}/{result['generation_id']}/"
            self.assertEqual(new_files, {f"{expected_prefix}image.png", f"{expected_prefix}manifest.json"})
            self.assertEqual(sentinel.read_bytes(), PNG_1X1)

    def test_preview_path_validation_rejects_traversal_and_non_clean_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            services = self.make_services(workspace)
            with self.assertRaises(HTTPException) as invalid_id:
                services.clean_image_generation_dir(workspace, "../escape", sc=services)
            self.assertEqual(invalid_id.exception.status_code, 400)

            generation_id = "cln_1780999609968_badbad01"
            generation_dir = workspace / CLEAN_IMAGE_REL / generation_id
            generation_dir.mkdir(parents=True, exist_ok=True)
            leak = workspace / ASSET_IMAGES_REL / "leak.png"
            leak.parent.mkdir(parents=True, exist_ok=True)
            leak.write_bytes(PNG_1X1)
            write_json(generation_dir / "manifest.json", {
                "schema_version": "clean_single_image_generation_0.1",
                "generation_id": generation_id,
                "output_path": f"{ASSET_IMAGES_REL}/leak.png",
            })

            with self.assertRaises(HTTPException) as outside_output:
                services.clean_image_output_path(workspace, generation_id, sc=services)
            self.assertEqual(outside_output.exception.status_code, 400)

    def test_reference_images_over_limit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            refs = []
            for index in range(9):
                rel = f"{CLEAN_IMAGE_REL}/refs/ref_{index}.png"
                path = workspace / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(PNG_1X1)
                refs.append(rel)
            services = self.make_services(workspace)

            with self.assertRaises(HTTPException) as exc:
                services.generate_clean_image(
                    {"id": 93, "session_id": 173, "workspace_dir": str(workspace)},
                    {"prompt": "clean object", "reference_paths": refs},
                sc=services)
            self.assertEqual(exc.exception.status_code, 400)

    def test_reference_uploads_are_saved_only_to_clean_scratch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            services = self.make_services(workspace)

            item = services.save_clean_image_reference_bytes(workspace, "ref..name #1.png", PNG_1X1, 1, "image/png", sc=services)

            self.assertTrue(item["path"].startswith(f"{CLEAN_IMAGE_REFERENCES_REL}/"))
            self.assertTrue((workspace / item["path"]).exists())
            self.assertNotIn(ASSET_IMAGES_REL, item["path"])
            self.assertNotIn("..", Path(item["path"]).name)
            self.assertTrue(services.clean_image_reference_path(workspace, Path(item["path"]).name, sc=services).exists())

    def test_reference_preview_rejects_non_reference_scratch_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            services = self.make_services(workspace)
            leak = workspace / CLEAN_IMAGE_REL / "cln_1780999609968_badbad01" / "image.png"
            leak.parent.mkdir(parents=True, exist_ok=True)
            leak.write_bytes(PNG_1X1)

            with self.assertRaises(HTTPException) as invalid:
                services.clean_image_reference_path(workspace, "../cln_1780999609968_badbad01/image.png", sc=services)
            self.assertEqual(invalid.exception.status_code, 400)

            with self.assertRaises(HTTPException) as missing:
                services.clean_image_reference_path(workspace, "image.png", sc=services)
            self.assertEqual(missing.exception.status_code, 404)

    def test_provider_failure_does_not_leave_empty_generation_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)

            def fail_provider(_config: dict[str, str], _prompt: str, _refs: Any = None, _size: str = "1536x1024", **_sc_kwargs) -> bytes:
                raise RuntimeError("provider unavailable")

            services = self.make_services(workspace, fail_provider)

            with self.assertRaises(RuntimeError):
                services.generate_clean_image(
                    {"id": 94, "session_id": 174, "workspace_dir": str(workspace)},
                    {"prompt": "clean object"},
                sc=services)
            scratch = workspace / CLEAN_IMAGE_REL
            self.assertFalse(scratch.exists() and any(scratch.iterdir()))


if __name__ == "__main__":
    unittest.main()
