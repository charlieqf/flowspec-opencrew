from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from fastapi import FastAPI  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.constants import EDIT_REL, SOURCE_REL  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.task_routes import register_task_routes  # noqa: E402


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def request(app: FastAPI, method: str, path: str) -> tuple[int, dict[str, Any]]:
    import anyio

    async def run() -> tuple[int, dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        messages = [{"type": "http.request", "body": b"", "more_body": False}]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }

        async def receive() -> dict[str, Any]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)
        status = 500
        chunks: list[bytes] = []
        for message in sent:
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body") or b"")
        body = b"".join(chunks).decode("utf-8")
        return status, json.loads(body) if body else {}

    return anyio.run(run)


class FakeRepo:
    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task

    def list_tasks(self) -> list[dict[str, Any]]:
        return [self.task]


class FakeDeps:
    def __init__(self, workspace: Path, task: dict[str, Any]) -> None:
        self.ctx = SimpleNamespace()
        self.repo = FakeRepo(task)
        self.workspace = workspace
        self.task = task

    def workspace_for(self, _task: dict[str, Any]) -> Path:
        return self.workspace

    def read_json(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def current_storyboard_source(self, _workspace: Path, source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bool]:
        return source, {"sha256": "source-sha"}, False

    def storyboard_edit_matches_source(self, edit: dict[str, Any], signature: dict[str, Any]) -> bool:
        return edit.get("source_sha256") == signature.get("sha256")

    def task_or_404(self, task_id: int) -> dict[str, Any]:
        if task_id == int(self.task["id"]):
            return self.task
        raise AssertionError(f"unexpected task id: {task_id}")

    def load_plan(self, _task: dict[str, Any], *, sc: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return {"shots": []}, {"loaded": True}

    def empty_asset_library_payload(self, task: dict[str, Any], _workspace: Path, *, sc: Any) -> dict[str, Any]:
        return {"ok": True, "task": task, "meta": {}, "plan": {"shots": []}}


class KouboStoryboardTaskListPayloadContractTest(unittest.TestCase):
    def test_task_list_uses_summary_task_but_detail_keeps_full_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            write_json(workspace / SOURCE_REL, {"schema_version": "analysis_v1_srt_storyboard_0.2", "shots": []})
            write_json(workspace / EDIT_REL, {"schema_version": "koubo_storyboard_edit_0.1", "source_sha256": "source-sha"})
            full_task = {
                "id": 123,
                "session_id": 456,
                "title": "Large prompt task",
                "final_prompt": "x" * 1000,
                "rewrite_final_prompt": "y" * 1000,
                "storyboard_final_prompt": "z" * 1000,
                "storyboard_quick_config_json": "{\"target_scene_seconds\":8}",
                "workspace_dir": str(workspace),
            }
            app = FastAPI()
            register_task_routes(app.router, FakeDeps(workspace, full_task))

            list_status, list_payload = request(app, "GET", "/api/koubo-storyboard/tasks")
            self.assertEqual(list_status, 200, list_payload)
            item = list_payload["items"][0]
            self.assertEqual(item["task"], {"id": 123, "session_id": 456})
            self.assertEqual(item["meta"]["analysis_task_id"], 123)
            self.assertEqual(item["meta"]["analysis_session_id"], 456)
            self.assertTrue(item["meta"]["has_saved_edit"])
            self.assertNotIn("final_prompt", item["task"])
            self.assertNotIn("storyboard_final_prompt", item["task"])
            self.assertNotIn("workspace_dir", item["task"])

            detail_status, detail_payload = request(app, "GET", "/api/koubo-storyboard/tasks/123")
            self.assertEqual(detail_status, 200, detail_payload)
            self.assertEqual(detail_payload["task"]["final_prompt"], "x" * 1000)
            self.assertEqual(detail_payload["task"]["storyboard_final_prompt"], "z" * 1000)


if __name__ == "__main__":
    unittest.main()
