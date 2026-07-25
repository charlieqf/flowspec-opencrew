from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.koubo.router import build_openclip_router  # noqa: E402


SNAPSHOT_PATH = Path(__file__).with_name("fixtures") / "openclip_router_snapshot.json"


def openclip_route_surface() -> list[dict[str, object]]:
    ctx = SimpleNamespace(
        engine=None,
        data_dir=Path("/tmp/opencrew-route-snapshot"),
        config=SimpleNamespace(database_url="sqlite://", frontend_url="http://127.0.0.1:18081/"),
        session_repo=None,
        session_event_service=None,
        workspace_store=None,
        get_setting=lambda _key, default=None: default,
    )
    router = build_openclip_router(ctx)
    rows: list[dict[str, object]] = []
    for route in router.routes:
        methods = sorted(method for method in getattr(route, "methods", set()) if method not in {"HEAD", "OPTIONS"})
        rows.append(
            {
                "methods": methods,
                "path": getattr(route, "path", ""),
                "endpoint": getattr(getattr(route, "endpoint", None), "__name__", ""),
            }
        )
    return rows


class OpenClipRouterSurfaceSnapshotContractTest(unittest.TestCase):
    def test_openclip_runtime_route_surface_matches_snapshot(self) -> None:
        expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(openclip_route_surface(), expected)


def write_snapshot() -> None:
    SNAPSHOT_PATH.write_text(json.dumps(openclip_route_surface(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if "--write" in sys.argv:
        sys.argv.remove("--write")
        write_snapshot()
        raise SystemExit(0)
    unittest.main()
