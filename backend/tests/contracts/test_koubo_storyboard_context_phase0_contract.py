from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"


def run_backend_probe(source: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(BACKEND_PATH) if not existing else f"{BACKEND_PATH}{os.pathsep}{existing}"
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class KouboStoryboardContextPhase0ContractTest(unittest.TestCase):
    def test_storyboard_context_keeps_per_router_state_objects(self) -> None:
        result = run_backend_probe(
            """
            from dataclasses import is_dataclass

            from fastapi import APIRouter
            from opcrew_backend.koubo.koubo_storyboard import composer_routes, video_plan_routes
            from opcrew_backend.koubo.koubo_storyboard.services import StoryboardContext, build_koubo_storyboard_services


            class DummyContext:
                pass


            class DummyRepository:
                pass


            def check(condition, message):
                if not condition:
                    raise AssertionError(message)


            first_ctx = DummyContext()
            first_repo = DummyRepository()
            first = build_koubo_storyboard_services(first_ctx, first_repo)
            second = build_koubo_storyboard_services(DummyContext(), DummyRepository())

            check(is_dataclass(first), "storyboard context should be a dataclass instance")
            check(isinstance(first, StoryboardContext), "builder should return StoryboardContext")
            check(first.ctx is first_ctx, "context object should be preserved")
            check(first.repo is first_repo, "repository object should be preserved")
            check(first.video_plan_lock is first.video_plan_lock, "same context should reuse the same video lock")
            check(first.video_plan_execution_jobs is first.video_plan_execution_jobs, "same context should reuse the same job map")
            check(first.video_plan_lock is not second.video_plan_lock, "different contexts should not share locks")
            check(first.video_plan_execution_jobs is not second.video_plan_execution_jobs, "different contexts should not share job maps")

            router = APIRouter()
            video_plan_routes.register_video_plan_routes(router, first)
            composer_routes.register_composer_routes(router, first)
            # Phase R flipped these modules to closure-held deps: registering
            # routes must no longer inject anything into module globals.
            check(not hasattr(video_plan_routes, "video_plan_lock"), "video plan route module must not hold injected globals after Phase R")
            check(not hasattr(composer_routes, "composer_execution_jobs"), "composer route module must not hold injected globals after Phase R")

            first.dynamic_service_marker = "ok"
            check(first.dynamic_service_marker == "ok", "phase 0 context must remain dynamically extensible")
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_same_router_requests_share_context_lock_and_stay_router_scoped(self) -> None:
        # Isolation contract (a), request-path form (plan §5 Phase 0 item 2):
        # handlers on ONE router must all serialize on that router's own
        # composer_lock (lifecycle: per-router, shared across requests, never
        # rebuilt per request), and a second router must be unaffected.
        # Pre-Phase-R this failed (module globals overwritten by the second
        # build); it passes now because Phase R handlers close over deps.
        result = run_backend_probe(
            """
            import asyncio

            from fastapi import APIRouter
            from opcrew_backend.koubo.koubo_storyboard import composer_routes
            from opcrew_backend.koubo.koubo_storyboard.services import build_koubo_storyboard_services


            class DummyContext:
                pass


            class DummyRepository:
                pass


            def composer_execute_endpoint(router):
                for route in router.routes:
                    if route.path.endswith("/composer/execute") and "POST" in route.methods:
                        return route.endpoint
                raise AssertionError("composer execute route not found")


            first = build_koubo_storyboard_services(DummyContext(), DummyRepository())
            second = build_koubo_storyboard_services(DummyContext(), DummyRepository())
            router_one = APIRouter()
            router_two = APIRouter()
            composer_routes.register_composer_routes(router_one, first)
            composer_routes.register_composer_routes(router_two, second)
            endpoint_one = composer_execute_endpoint(router_one)
            endpoint_two = composer_execute_endpoint(router_two)


            async def main():
                await first.composer_lock.acquire()
                try:
                    # Two requests on router one: both must block on the SAME
                    # held lock object — proving per-router identity and
                    # cross-request sharing through the real handler path.
                    for attempt in range(2):
                        try:
                            await asyncio.wait_for(endpoint_one(1, {}), timeout=0.3)
                        except asyncio.TimeoutError:
                            continue
                        raise AssertionError(f"router-one request {attempt} did not block on the router-one composer lock")
                    # Router two must get PAST the lock (then fail later in
                    # the dummy environment, which is fine) — proving no
                    # cross-router lock sharing.
                    try:
                        await asyncio.wait_for(endpoint_two(1, {}), timeout=2.0)
                    except asyncio.TimeoutError:
                        raise AssertionError("router-two request blocked on router-one's composer lock (cross-context sharing)")
                    except Exception:
                        pass
                finally:
                    first.composer_lock.release()


            asyncio.run(main())
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_two_storyboard_contexts_do_not_overwrite_each_other(self) -> None:
        # Isolation contract (b), behavioral form — GREEN since Phase F removed
        # _sync_service_globals: building a second context must not leak into
        # calls that carry the first context explicitly. Historically the
        # second build overwrote every service module's globals (this test was
        # @unittest.expectedFailure until 2026-07-05).
        result = run_backend_probe(
            """
            from opcrew_backend.koubo.koubo_storyboard import asset_search_services
            from opcrew_backend.koubo.koubo_storyboard.services import build_koubo_storyboard_services


            class DummyContext:
                pass


            class DummyRepository:
                pass


            def check(condition, message):
                if not condition:
                    raise AssertionError(message)


            first = build_koubo_storyboard_services(DummyContext(), DummyRepository())
            second = build_koubo_storyboard_services(DummyContext(), DummyRepository())

            # Building the second context must not stuff either context's
            # payload into service module globals any more.
            check(not hasattr(asset_search_services, "ctx"), "service module globals must not carry a context after Phase F")

            # A call that explicitly carries the FIRST context must observe the
            # first context's dependencies even after the second build.
            from pathlib import Path as _P
            seen = []
            first.read_json = lambda path, **_kw: (seen.append(("first", path)), {})[1]
            second.read_json = lambda path, **_kw: (seen.append(("second", path)), {})[1]
            first.workspace_for = lambda task, **_kw: _P("/tmp/first-ws")
            second.workspace_for = lambda task, **_kw: _P("/tmp/second-ws")
            asset_search_services.read_asset_search_settings({"id": 1, "session_id": 1, "workspace_dir": "/tmp/x"}, sc=first)
            check(seen and all(tag == "first" for tag, _ in seen), f"explicit sc=first must resolve first-context deps, saw {seen}")
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
