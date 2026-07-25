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
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from opcrew_backend.db.schema import metadata, openclip_tasks, sessions  # noqa: E402
from opcrew_backend.koubo.task_list_router import (  # noqa: E402
    FINAL_SRT_ITEMS_REL,
    SOURCE_SCRIPT_REL,
    STORYBOARD_REL,
    TASK_META_REL,
    VARIABLES_REL,
    build_koubo_task_list_router,
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def request(app: FastAPI, method: str, url: str) -> tuple[int, dict[str, Any]]:
    import anyio

    path, _, query = url.partition("?")

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
            "query_string": query.encode("utf-8"),
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


class KouboTaskListPayloadContractTest(unittest.TestCase):
    def test_list_uses_summary_payload_but_detail_keeps_full_task_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            long_script = "这是一段很长的口播脚本。" * 200
            (workspace / SOURCE_SCRIPT_REL).parent.mkdir(parents=True, exist_ok=True)
            (workspace / SOURCE_SCRIPT_REL).write_text(long_script, encoding="utf-8")
            write_json(workspace / FINAL_SRT_ITEMS_REL, {"items": [{"dialogue": "第一句"}, {"dialogue": "第二句"}]})
            write_json(workspace / STORYBOARD_REL, {"task_summary": "列表摘要", "shots": [{"scenes": [{"dialogues": []}]}]})
            write_json(workspace / VARIABLES_REL, {"task_summary": "变量摘要"})
            write_json(workspace / TASK_META_REL, {
                "title": "Payload slim task",
                "create_mode": "script",
                "input_mode": "script_only",
                "script_preview": long_script[:160],
                "talking_head": {"voice_timing": {"voice_id": "clone-voice"}},
            })

            engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
            metadata.create_all(engine)
            with engine.begin() as conn:
                conn.execute(sessions.insert().values(
                    id=456,
                    source="openclip-analysis",
                    group_id="openclip-analysis",
                    sender_name="OpenClip",
                    title="Payload slim task",
                    command_text="",
                    status="draft",
                    workspace_dir=str(workspace),
                    share_token="share-token",
                    created_at=1000,
                    updated_at=2000,
                ))
                conn.execute(openclip_tasks.insert().values(
                    id=123,
                    session_id=456,
                    status="draft",
                    workflow_mode="script",
                    reference_video_path="",
                    industry="行业",
                    persona="人设",
                    target_audience="受众",
                    product_info="产品" * 200,
                    constraints="约束" * 200,
                    analysis_goal="目标",
                    video_formula="公式",
                    simple_prompt="simple prompt",
                    final_prompt="final prompt " * 500,
                    rewrite_simple_prompt="rewrite simple",
                    rewrite_final_prompt="rewrite final " * 500,
                    storyboard_simple_prompt="storyboard simple",
                    storyboard_final_prompt="storyboard final " * 500,
                    storyboard_quick_config_json=json.dumps({"target_scene_seconds": 8, "talking_head": {"voice_timing": {"voice_id": "clone-voice"}}}),
                    created_at=1000,
                    updated_at=2000,
                ))

            app = FastAPI()
            app.include_router(build_koubo_task_list_router(SimpleNamespace(engine=engine)))

            list_status, list_payload = request(app, "GET", "/api/koubo-tasks?include_archived=false")
            self.assertEqual(list_status, 200, list_payload)
            item = list_payload["items"][0]
            self.assertEqual(item["task_id"], 123)
            self.assertEqual(item["session_id"], 456)
            self.assertEqual(item["task_summary"], "变量摘要")
            self.assertEqual(item["dialogue_count"], 2)
            self.assertEqual(item["shot_count"], 1)
            self.assertEqual(item["scene_count"], 1)
            self.assertIn("storyboard_url", item)
            for key in (
                "source_script",
                "final_prompt",
                "rewrite_final_prompt",
                "storyboard_final_prompt",
                "storyboard_quick_config",
                "talking_head",
                "workspace_dir",
                "product_info",
                "constraints",
            ):
                self.assertNotIn(key, item)

            detail_status, detail_payload = request(app, "GET", "/api/koubo-tasks/123")
            self.assertEqual(detail_status, 200, detail_payload)
            detail = detail_payload["item"]
            self.assertEqual(detail["source_script"], long_script)
            self.assertEqual(detail["final_prompt"], "final prompt " * 500)
            self.assertEqual(detail["storyboard_final_prompt"], "storyboard final " * 500)
            self.assertEqual(detail["storyboard_quick_config"]["target_scene_seconds"], 8.0)
            self.assertEqual(detail["talking_head"]["voice_timing"]["voice_id"], "clone-voice")
            self.assertEqual(detail["workspace_dir"], str(workspace))


if __name__ == "__main__":
    unittest.main()
