from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opcrew_backend.config import load_config
from opcrew_backend.context import AppContext, now_ms


DEFAULT_BACKEND_URL = "http://127.0.0.1:8011"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:18080"


class SmokeFailure(AssertionError):
    pass


@dataclass
class HttpResult:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run automated P0 stack smoke checks against a running OpenCrew local stack.")
    parser.add_argument("--backend-url", default=os.environ.get("OPENCREW_SMOKE_BACKEND_URL", DEFAULT_BACKEND_URL))
    parser.add_argument("--frontend-url", default=os.environ.get("OPENCREW_SMOKE_FRONTEND_URL", DEFAULT_FRONTEND_URL))
    parser.add_argument("--caddy-url", default=os.environ.get("OPENCREW_SMOKE_CADDY_URL", ""))
    parser.add_argument("--caddy-host-header", default=os.environ.get("OPENCREW_SMOKE_CADDY_HOST_HEADER", ""))
    parser.add_argument("--caddy-user", default=os.environ.get("OPENCREW_SMOKE_CADDY_USER", ""))
    parser.add_argument("--caddy-password", default=os.environ.get("OPENCREW_SMOKE_CADDY_PASSWORD", ""))
    parser.add_argument("--app-password", default=os.environ.get("OPENCREW_SMOKE_APP_PASSWORD", os.environ.get("OPENCREW_APP_PASSWORD", "")))
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON results.")
    return parser.parse_args()


def url_join(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, basic_auth: tuple[str, str] | None = None, json_body: dict[str, Any] | None = None) -> HttpResult:
    req_headers = dict(headers or {})
    data = None
    if json_body is not None:
        req_headers["Content-Type"] = "application/json"
        data = json.dumps(json_body, ensure_ascii=True).encode("utf-8")
    if basic_auth:
        raw = f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")
        req_headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return HttpResult(int(response.status), response.read(), {key.lower(): value for key, value in response.headers.items()})
    except urllib.error.HTTPError as exc:
        return HttpResult(int(exc.code), exc.read(), {key.lower(): value for key, value in exc.headers.items()})


def login_cookie(base_url: str, app_password: str) -> str:
    status = request(url_join(base_url, "/api/auth/status")).json()
    if not bool(status.get("enabled")):
        return ""
    require(bool(app_password), "OPENCREW_SMOKE_APP_PASSWORD or OPENCREW_APP_PASSWORD is required when app auth is enabled")
    response = request(url_join(base_url, "/api/auth/login"), method="POST", json_body={"password": app_password})
    require(response.status == 200, f"app login expected 200, got {response.status}: {response.body[:200]!r}")
    cookie = response.headers.get("set-cookie", "")
    require("opencrew_session=" in cookie, "app login response did not set opencrew_session cookie")
    return cookie.split(";", 1)[0]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def file_id(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


def raw_url(backend_url: str, session_id: int, path: str) -> str:
    encoded = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    return url_join(backend_url, f"/api/session-tasks/{session_id}/raw/{encoded}")


def create_fixture() -> tuple[int, str, Path, Path]:
    ctx = AppContext(load_config())
    outside_tmp = tempfile.NamedTemporaryFile(prefix="opencrew-smoke-outside-", suffix=".txt", delete=False)
    outside_path = Path(outside_tmp.name)
    try:
        outside_tmp.write(b"outside")
        outside_tmp.close()
        created = now_ms()
        session_id = ctx.session_repo.create(
            source="p0-smoke",
            group_id="p0-smoke",
            sender_name="P0 Smoke",
            title=f"P0 Smoke {created}",
            command_text="automated p0 stack smoke",
            status="waiting_input",
            workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"),
            share_token="",
            created_at=created,
            updated_at=created,
        )
        workspace = ctx.workspace_store.create_session_workspace(session_id)
        (workspace / "source_video.mp4").write_bytes((b"0123456789abcdef" * 4096))
        (workspace / "outbox").mkdir(parents=True, exist_ok=True)
        (workspace / "outbox" / "result.txt").write_text("result", encoding="utf-8")
        (workspace / ".env").write_text("SECRET=1", encoding="utf-8")
        (workspace / "meta").mkdir(parents=True, exist_ok=True)
        (workspace / "meta" / "debug.json").write_text('{"debug": true}', encoding="utf-8")
        (workspace / "history").mkdir(exist_ok=True)
        (workspace / "history" / "trace.log").write_text("trace", encoding="utf-8")
        (workspace / "external_link.txt").symlink_to(outside_path)
        token = ctx.new_share_token()
        ctx.session_repo.update(session_id, workspace_dir=str(workspace), share_token=token, updated_at=created)
        ctx.session_repo.upsert_share(session_id, token, "viewer", created + 3600000, created)
        ctx.session_repo.upsert_file(session_id, "source_video.mp4", "video", (workspace / "source_video.mp4").stat().st_size, "smoke", 1, created, visibility="public", sensitivity="normal")
        ctx.session_repo.upsert_file(session_id, "outbox/result.txt", "file", (workspace / "outbox" / "result.txt").stat().st_size, "smoke", 1, created, visibility="public", sensitivity="normal")
        ctx.session_event_service.add_event(session_id, "user.message", {"text": "hello from smoke"})
        ctx.session_event_service.add_event(
            session_id,
            "opencode.session.error",
            {"message": "provider failed", "email": "person@example.com", "phone": "+14155551212", "created_at": "1779849584351", "api_key": "secret"},
            visibility="internal",
            event_scope="debug",
            family="opencode",
        )
        return session_id, token, workspace, outside_path
    finally:
        ctx.shutdown()


def cleanup_fixture(backend_url: str, session_id: int, outside_path: Path, headers: dict[str, str] | None = None) -> None:
    try:
        request(url_join(backend_url, f"/api/session-tasks/{session_id}"), method="DELETE", headers=headers or {})
    finally:
        outside_path.unlink(missing_ok=True)


def run_smoke(args: argparse.Namespace) -> list[dict[str, Any]]:
    backend_url = str(args.backend_url).rstrip("/")
    frontend_url = str(args.frontend_url).rstrip("/")
    checks: list[dict[str, Any]] = []
    app_cookie = login_cookie(backend_url, str(args.app_password or ""))
    auth_headers = {"Cookie": app_cookie} if app_cookie else {}

    def record(name: str, fn: Any) -> None:
        try:
            detail = fn()
            checks.append({"name": name, "ok": True, "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "ok": False, "error": str(exc)})
            raise

    record("backend_health", lambda: request(url_join(backend_url, "/api/health")).json())
    record("frontend_proxy", lambda: request(url_join(frontend_url, "/api/auth/status")).json())

    session_id, token, workspace, outside_path = create_fixture()
    try:
        def check_events() -> dict[str, Any]:
            customer = request(url_join(backend_url, f"/api/sessions/{session_id}/events"), headers=auth_headers).json()["items"]
            debug = request(url_join(backend_url, f"/api/sessions/{session_id}/events?audience=debug"), headers=auth_headers).json()["items"]
            share = request(url_join(backend_url, f"/api/session-share/{token}/events")).json()["items"]
            customer_kinds = {item["kind"] for item in customer}
            debug_kinds = {item["kind"] for item in debug}
            share_kinds = {item["kind"] for item in share}
            require("user.message" in customer_kinds, "customer events lost public user.message")
            require("opencode.session.error" not in customer_kinds, "customer events expose opencode debug event")
            require("opencode.session.error" not in share_kinds, "share events expose opencode debug event")
            require("opencode.session.error" in debug_kinds, "debug events hide opencode debug event")
            opencode_event = next(item for item in debug if item["kind"] == "opencode.session.error")
            payload = opencode_event["payload"]
            require(payload["created_at"] == "1779849584351", "epoch-ms id was redacted")
            require(payload["email"] == "[REDACTED_EMAIL]", "email was not redacted")
            require(payload["phone"] == "[REDACTED_PHONE]", "phone was not redacted")
            require(payload["api_key"] == "[REDACTED]", "api key was not redacted")
            return {"customer": sorted(customer_kinds), "debug": sorted(debug_kinds), "share": sorted(share_kinds)}

        def check_files() -> dict[str, Any]:
            listing = request(url_join(backend_url, f"/api/session-tasks/{session_id}/files"), headers=auth_headers).json()["files"]
            listed = {item["path"] for item in listing}
            require("source_video.mp4" in listed, "source video missing from task file listing")
            require("meta" not in listed and "history" not in listed and ".env" not in listed, "internal files leaked in task file listing")
            require("external_link.txt" not in listed, "workspace-escaping symlink leaked in task file listing")
            share_files = request(url_join(backend_url, f"/api/session-share/{token}/files")).json()["items"]
            share_paths = {item["path"] for item in share_files}
            require("source_video.mp4" in share_paths, "source video missing from share file list")
            require(".env" not in share_paths and "meta/debug.json" not in share_paths and "external_link.txt" not in share_paths, "share file list leaked internal files")
            return {"task_paths": sorted(listed), "share_paths": sorted(share_paths)}

        def check_raw_downloads() -> dict[str, Any]:
            ranged = request(raw_url(backend_url, session_id, "source_video.mp4"), headers={**auth_headers, "Range": "bytes=0-15"})
            require(ranged.status == 206 and len(ranged.body) == 16, f"source video range expected 206/16, got {ranged.status}/{len(ranged.body)}")
            share_range = request(url_join(backend_url, f"/api/session-share/{token}/files/{file_id('source_video.mp4')}"), headers={"Range": "bytes=0-15"})
            require(share_range.status == 206 and len(share_range.body) == 16, f"share video range expected 206/16, got {share_range.status}/{len(share_range.body)}")
            require(request(raw_url(backend_url, session_id, ".env"), headers=auth_headers).status == 403, ".env raw download was not forbidden")
            require(request(raw_url(backend_url, session_id, "meta/debug.json"), headers=auth_headers).status == 403, "meta raw download was not forbidden")
            require(request(raw_url(backend_url, session_id, "external_link.txt"), headers=auth_headers).status == 400, "workspace-escaping symlink was not rejected")
            zip_response = request(url_join(backend_url, f"/api/session-tasks/{session_id}/files.zip"), headers=auth_headers)
            require(zip_response.status == 200 and b"SECRET=1" not in zip_response.body and b"outside" not in zip_response.body, "zip leaked sensitive or external content")
            return {"raw_range": ranged.status, "share_range": share_range.status, "zip_bytes": len(zip_response.body)}

        def check_delete_cleanup() -> dict[str, Any]:
            delete_response = request(url_join(backend_url, f"/api/session-tasks/{session_id}"), method="DELETE", headers=auth_headers)
            require(delete_response.status == 200, f"delete expected 200, got {delete_response.status}")
            require(not workspace.parent.exists(), "session workspace directory still exists after delete")
            return delete_response.json()

        record("event_visibility_and_redaction", check_events)
        record("file_listing_policy", check_files)
        record("raw_share_zip_file_policy", check_raw_downloads)
        record("delete_db_first_workspace_cleanup", check_delete_cleanup)
        session_id = 0
    finally:
        if session_id:
            cleanup_fixture(backend_url, session_id, outside_path, auth_headers)

    caddy_url = str(args.caddy_url or "").strip()
    if caddy_url:
        def check_caddy() -> dict[str, Any]:
            headers = {"Host": str(args.caddy_host_header).strip()} if str(args.caddy_host_header).strip() else {}
            unauth = request(caddy_url, headers=headers)
            require(unauth.status == 401, f"caddy unauth expected 401, got {unauth.status}")
            if not args.caddy_user or not args.caddy_password:
                return {"unauth": unauth.status, "auth": "skipped"}
            auth = request(url_join(caddy_url, "/api/health"), headers=headers, basic_auth=(args.caddy_user, args.caddy_password))
            require(auth.status == 200, f"caddy auth health expected 200, got {auth.status}")
            return {"unauth": unauth.status, "auth": auth.status}

        record("caddy_basic_auth", check_caddy)

    return checks


def main() -> None:
    args = parse_args()
    error = ""
    try:
        checks = run_smoke(args)
        ok = True
    except Exception as exc:
        checks = []
        ok = False
        error = str(exc)
    payload = {"ok": ok, "checks": checks, "error": error}
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        for check in checks:
            status = "ok" if check.get("ok") else "failed"
            print(f"{status} {check['name']}")
        if error:
            print(f"error {error}")
        print("smoke_ok" if ok else "smoke_failed")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
