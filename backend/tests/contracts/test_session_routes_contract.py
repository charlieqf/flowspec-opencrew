from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.routes.sessions import build_session_router  # noqa: E402


class FakeSessionRepository:
    def __init__(self, session_row: dict[str, Any], share_row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> None:
        self.session_row = session_row
        self.share_row = share_row
        self.events = events or []

    def get(self, session_id: int) -> dict[str, Any] | None:
        if int(self.session_row["id"]) != session_id:
            return None
        return dict(self.session_row)

    def get_share(self, token: str) -> dict[str, Any] | None:
        if token != "share-token":
            return None
        return dict(self.share_row)

    def update(self, session_id: int, **fields: Any) -> None:
        if int(self.session_row["id"]) == session_id:
            self.session_row.update(fields)

    def exists(self, session_id: int) -> bool:
        return int(self.session_row["id"]) == session_id

    def list_events(self, session_id: int, since: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        if int(self.session_row["id"]) != session_id:
            return []
        return [row for row in self.events if int(row.get("id") or 0) > since][:limit]


class FakeWorkspaceStore:
    def __init__(self, sessions_root: Path) -> None:
        self._sessions_root = sessions_root

    def sessions_root(self) -> Path:
        return self._sessions_root


class FakeContext:
    def __init__(self, session_row: dict[str, Any], share_row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> None:
        self.session_repo = FakeSessionRepository(session_row, share_row, events)
        self.workspace_store = FakeWorkspaceStore(Path(session_row["workspace_dir"]).parents[1])


class SessionRoutesContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sessions_root = Path(self.tmp.name) / "sessions"
        self.workspace = self.sessions_root / "1" / "workspace"
        self.workspace.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def endpoint_for(self, path: str, session_row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> Any:
        share_row = {"session_id": 1, "scope": "viewer", "expires_at": 9_999_999_999_999}
        router = build_session_router(FakeContext(session_row, share_row, events))  # type: ignore[arg-type]
        for route in router.routes:
            if getattr(route, "path", "") == path:
                return route.endpoint
        raise AssertionError(f"Route not found: {path}")

    def test_share_page_escapes_session_fields(self) -> None:
        session_row = {
            "id": 1,
            "title": "<img src=x onerror=alert(1)>",
            "status": "waiting_input",
            "group_id": "</script><script>alert(1)</script>",
            "sender_name": "<b>sender</b>",
            "workspace_dir": str(self.workspace),
        }

        endpoint = self.endpoint_for("/session/share/{token}", session_row)
        response = asyncio.run(endpoint("share-token"))

        self.assertEqual(response.status_code, 200)
        body = response.body.decode("utf-8")
        self.assertNotIn("<img src=x onerror=alert(1)>", body)
        self.assertNotIn("</script><script>alert(1)</script>", body)
        self.assertNotIn("<b>sender</b>", body)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", body)
        self.assertIn("&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertIn("&lt;b&gt;sender&lt;/b&gt;", body)

    def test_session_task_logs_returns_lines_without_workspace_logs(self) -> None:
        session_row = {
            "id": 1,
            "title": "Session 1",
            "status": "waiting_input",
            "group_id": "default",
            "sender_name": "Simulator",
            "workspace_dir": str(self.workspace),
        }

        endpoint = self.endpoint_for("/api/session-tasks/{session_id}/logs", session_row)
        response = asyncio.run(endpoint(1))

        self.assertEqual(response, {"lines": []})

    def test_session_task_logs_formats_public_events(self) -> None:
        session_row = {
            "id": 1,
            "title": "Session 1",
            "status": "waiting_input",
            "group_id": "default",
            "sender_name": "Simulator",
            "workspace_dir": str(self.workspace),
        }
        events = [
            {"id": 1, "kind": "opencode.provider.raw", "payload": "{\"message\":\"hidden\"}", "created_at": 1000},
            {"id": 2, "kind": "session.error", "payload": "{\"message\":\"visible\"}", "created_at": 1001},
        ]

        endpoint = self.endpoint_for("/api/session-tasks/{session_id}/logs", session_row, events)
        response = asyncio.run(endpoint(1))

        self.assertEqual(response, {"lines": ["1001 session.error visible"]})


if __name__ == "__main__":
    unittest.main()
