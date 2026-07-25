from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (REPO_ROOT / "backend", REPO_ROOT / "backend" / "scripts"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from rename_digital_human_assets import WorkspaceRef, run_migration  # noqa: E402


ASSETS_REL = "SessionOutput/storyboard/koubo_storyboard_assets.json"
VIDEOS_REL = "SessionOutput/storyboard/assets/videos"
HISTORY_REL = "SessionOutput/storyboard/assets/history/batch_1"
EDIT_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class RenameDigitalHumanAssetsContractTest(unittest.TestCase):
    def make_engine(self, old_video: str, old_sidecar: str):
        engine = create_engine("sqlite:///:memory:", future=True)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE session_files (session_id INTEGER, path TEXT)"))
            conn.execute(
                text("INSERT INTO session_files (session_id, path) VALUES (42, :video), (42, :sidecar)"),
                {"video": old_video, "sidecar": old_sidecar},
            )
        return engine

    def test_dry_run_and_write_rename_legacy_heygen_assets_without_dead_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            old_video = f"{VIDEOS_REL}/123_heygen_digital_human_x.mp4"
            old_sidecar = f"{VIDEOS_REL}/123_heygen_digital_human_x.json"
            new_video = f"{VIDEOS_REL}/123_digital_human_x.mp4"
            new_sidecar = f"{VIDEOS_REL}/123_digital_human_x.json"
            video_path = workspace / old_video
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video")
            write_json(workspace / old_sidecar, {"output": old_video, "source": "heygen_digital_human"})
            write_json(workspace / ASSETS_REL, {
                "assets": [
                    {
                        "id": old_video,
                        "path": old_video,
                        "filename": "123_heygen_digital_human_x.mp4",
                        "source": "heygen_digital_human",
                        "label": "HeyGen digital human video",
                        "origin": {"request_path": old_sidecar, "provider": "heygen"},
                    }
                ]
            })
            write_json(workspace / EDIT_REL, {"shots": [{"video_path": old_video}], "sidecar": old_sidecar})
            engine = self.make_engine(old_video, old_sidecar)

            dry_run = run_migration([WorkspaceRef(session_id=42, workspace=workspace)], engine=engine, write=False)
            self.assertEqual(dry_run["mode"], "dry-run")
            self.assertEqual(dry_run["candidate_count"], 1)
            self.assertTrue((workspace / old_video).exists())
            self.assertFalse((workspace / new_video).exists())

            report = run_migration([WorkspaceRef(session_id=42, workspace=workspace)], engine=engine, write=True)

            self.assertEqual(report["mode"], "write")
            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["blocked_count"], 0)
            self.assertTrue((workspace / new_video).is_file())
            self.assertTrue((workspace / new_sidecar).is_file())
            self.assertFalse((workspace / old_video).exists())
            self.assertFalse((workspace / old_sidecar).exists())

            manifest = read_json(workspace / ASSETS_REL)
            asset = manifest["assets"][0]  # type: ignore[index]
            public_fields = {key: asset[key] for key in ("id", "path", "filename", "source", "label")}  # type: ignore[index]
            self.assertEqual(public_fields, {
                "id": new_video,
                "path": new_video,
                "filename": "123_digital_human_x.mp4",
                "source": "digital_human",
                "label": "Digital human video",
            })
            self.assertNotIn("heygen", json.dumps(public_fields, ensure_ascii=False).lower())
            self.assertEqual(asset["origin"]["request_path"], new_sidecar)  # type: ignore[index]

            sidecar = read_json(workspace / new_sidecar)
            self.assertEqual(sidecar["output"], new_video)
            self.assertEqual(sidecar["source"], "digital_human")
            edit = read_json(workspace / EDIT_REL)
            self.assertEqual(edit["shots"][0]["video_path"], new_video)  # type: ignore[index]
            self.assertEqual(edit["sidecar"], new_sidecar)

            with engine.begin() as conn:
                rows = [row[0] for row in conn.execute(text("SELECT path FROM session_files ORDER BY path")).fetchall()]
            self.assertEqual(rows, [new_sidecar, new_video])

            engine.dispose()

    def test_existing_target_blocks_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            old_video = Path(VIDEOS_REL) / "123_heygen_video_agent_x.mp4"
            new_video = Path(VIDEOS_REL) / "123_digital_human_agent_x.mp4"
            (workspace / old_video).parent.mkdir(parents=True, exist_ok=True)
            (workspace / old_video).write_bytes(b"old")
            (workspace / new_video).write_bytes(b"new")

            report = run_migration([WorkspaceRef(session_id=0, workspace=workspace)], write=True)

            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["blocked_count"], 1)
            self.assertIn("target file already exists", report["candidates"][0]["issue"])
            self.assertEqual((workspace / old_video).read_bytes(), b"old")
            self.assertEqual((workspace / new_video).read_bytes(), b"new")

    def test_history_assets_are_renamed_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            old_video = f"{HISTORY_REL}/123_heygen_digital_human_x.mp4"
            old_sidecar = f"{HISTORY_REL}/123_heygen_digital_human_x.json"
            new_video = f"{HISTORY_REL}/123_digital_human_x.mp4"
            new_sidecar = f"{HISTORY_REL}/123_digital_human_x.json"
            (workspace / old_video).parent.mkdir(parents=True, exist_ok=True)
            (workspace / old_video).write_bytes(b"video")
            write_json(workspace / old_sidecar, {"output": old_video, "source": "heygen_digital_human"})
            write_json(workspace / ASSETS_REL, {"history": [{"path": old_video, "request_path": old_sidecar}]})

            report = run_migration([WorkspaceRef(session_id=42, workspace=workspace)], write=True)

            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["blocked_count"], 0)
            self.assertTrue((workspace / new_video).is_file())
            self.assertTrue((workspace / new_sidecar).is_file())
            self.assertFalse((workspace / old_video).exists())
            self.assertFalse((workspace / old_sidecar).exists())
            manifest = read_json(workspace / ASSETS_REL)
            self.assertEqual(manifest["history"][0]["path"], new_video)  # type: ignore[index]
            self.assertEqual(manifest["history"][0]["request_path"], new_sidecar)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
