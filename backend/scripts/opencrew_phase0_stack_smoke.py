from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import psycopg


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
    parser = argparse.ArgumentParser(description="Run Phase 0 Local Box Trial smoke checks.")
    parser.add_argument("--backend-url", default=os.environ.get("OPENCREW_SMOKE_BACKEND_URL", DEFAULT_BACKEND_URL))
    parser.add_argument("--frontend-url", default=os.environ.get("OPENCREW_SMOKE_FRONTEND_URL", DEFAULT_FRONTEND_URL))
    parser.add_argument("--app-password", default=os.environ.get("OPENCREW_SMOKE_APP_PASSWORD", os.environ.get("OPENCREW_APP_PASSWORD", "")))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def url_join(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, json_body: dict[str, Any] | None = None) -> HttpResult:
    req_headers = dict(headers or {})
    data = None
    if json_body is not None:
        req_headers["Content-Type"] = "application/json"
        data = json.dumps(json_body, ensure_ascii=True).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return HttpResult(int(response.status), response.read(), {key.lower(): value for key, value in response.headers.items()})
    except urllib.error.HTTPError as exc:
        return HttpResult(int(exc.code), exc.read(), {key.lower(): value for key, value in exc.headers.items()})


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def login_cookie(base_url: str, password: str) -> str:
    status = request(url_join(base_url, "/api/auth/status")).json()
    require(bool(status.get("enabled")), "app auth is not enabled")
    require(bool(status.get("configured")), "app auth is not configured")
    require(not bool(status.get("debug_console_enabled")), "Debug Console is enabled for Phase 0")
    require(bool(password), "OPENCREW_SMOKE_APP_PASSWORD or OPENCREW_APP_PASSWORD is required")
    response = request(url_join(base_url, "/api/auth/login"), method="POST", json_body={"password": password})
    require(response.status == 200, f"login expected 200, got {response.status}: {response.body[:200]!r}")
    cookie = response.headers.get("set-cookie", "")
    require("opencrew_session=" in cookie, "login did not set opencrew_session cookie")
    return cookie.split(";", 1)[0]


def db_connect(database_url: str):
    return psycopg.connect(database_url.replace("postgresql+psycopg://", "postgresql://"))


def run_smoke(args: argparse.Namespace) -> list[dict[str, Any]]:
    backend_url = str(args.backend_url).rstrip("/")
    frontend_url = str(args.frontend_url).rstrip("/")
    database_url = str(args.database_url)
    checks: list[dict[str, Any]] = []

    def record(name: str, fn: Any) -> None:
        detail = fn()
        checks.append({"name": name, "ok": True, "detail": detail})

    def check_auth_gate() -> dict[str, Any]:
        unauth = request(url_join(frontend_url, "/api/setup/summary"))
        require(unauth.status == 401, f"protected setup endpoint expected 401 before login, got {unauth.status}")
        cors_unauth = request(url_join(backend_url, "/api/setup/summary"), headers={"Origin": "http://1.42.112.164.nip.io"})
        require(cors_unauth.status == 401, f"CORS unauth check expected 401, got {cors_unauth.status}")
        require(cors_unauth.headers.get("access-control-allow-origin") == "http://1.42.112.164.nip.io", "auth 401 did not pass through CORS middleware")
        cookie = login_cookie(backend_url, str(args.app_password or ""))
        authed = request(url_join(frontend_url, "/api/setup/summary"), headers={"Cookie": cookie})
        require(authed.status == 200, f"protected setup endpoint expected 200 after login, got {authed.status}")
        return {"unauth": unauth.status, "cors_unauth": cors_unauth.status, "authed": authed.status}

    record("app_login_gate", check_auth_gate)
    cookie = login_cookie(backend_url, str(args.app_password or ""))
    headers = {"Cookie": cookie}

    def check_secret_store() -> dict[str, Any]:
        config = request(url_join(frontend_url, "/api/setup/media-models/image/config"), headers=headers).json()
        providers = []
        for provider in config["providers"]:
            providers.append(
                {
                    "provider": provider["provider"],
                    "model": provider["model"],
                    "enabled": provider.get("enabled", True),
                    "api_key": "sk-phase0-smoke" if provider["provider"] == "openai" else "",
                }
            )
        saved = request(
            url_join(frontend_url, "/api/setup/media-models/image/config"),
            method="PUT",
            headers=headers,
            json_body={"active_provider": config["active_provider"], "providers": providers},
        )
        require(saved.status == 200, f"media config save expected 200, got {saved.status}")
        with db_connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT api_key_ciphertext, api_key_ref FROM tool_media_provider_configs WHERE kind = 'image' AND provider = 'openai'")
                row = cur.fetchone()
                require(row is not None, "openai image config row missing")
                require(row[0] is None or row[0] == "", "provider key was stored in api_key_ciphertext")
                require(row[1] == "image_openai_key", "api_key_ref was not set")
        reloaded = request(url_join(frontend_url, "/api/setup/media-models/image/config"), headers=headers).json()
        openai = next(item for item in reloaded["providers"] if item["provider"] == "openai")
        require(bool(openai["has_api_key"]), "saved key was not visible via has_api_key")
        return {"provider": "openai", "api_key_ref": openai["api_key_ref"], "has_api_key": openai["has_api_key"]}

    def check_mihomo() -> dict[str, Any]:
        saved = request(
            url_join(frontend_url, "/api/setup/mihomo/config"),
            method="PUT",
            headers=headers,
            json_body={"enabled": True, "subscription_url": "https://example.invalid/sub"},
        )
        require(saved.status == 200, f"mihomo config save expected 200, got {saved.status}")
        body = saved.json()
        require(body["enabled"] is True, "mihomo enabled flag was not persisted")
        require(body["has_subscription_url"] is True, "mihomo subscription URL was not saved")
        require(body["listen_host"] == "127.0.0.1", "mihomo listen host must stay localhost")
        return {"enabled": body["enabled"], "has_subscription_url": body["has_subscription_url"], "listen_host": body["listen_host"]}

    def check_local_usage() -> dict[str, Any]:
        before = 0
        with db_connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM local_usage_log")
                before = int(cur.fetchone()[0])
        result = request(
            url_join(frontend_url, "/api/setup/media-models/image/test"),
            method="POST",
            headers=headers,
            json_body={"provider": "openai", "model": "gpt-image-2"},
        )
        require(result.status == 200, f"media connection test expected 200, got {result.status}")
        with db_connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT provider, model_id, modality, proxy_policy, status FROM local_usage_log ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                cur.execute("SELECT count(*) FROM local_usage_log")
                after = int(cur.fetchone()[0])
        require(after > before, "local_usage_log did not receive a new row")
        require(row[:4] == ("openai", "gpt-image-2", "image", "mihomo"), f"unexpected local_usage_log row: {row!r}")
        return {"before": before, "after": after, "latest": row}

    record("local_secret_store_key_config", check_secret_store)
    record("mihomo_localhost_config", check_mihomo)
    record("local_usage_log", check_local_usage)
    return checks


def main() -> None:
    args = parse_args()
    try:
        checks = run_smoke(args)
        payload = {"ok": True, "checks": checks, "error": ""}
    except Exception as exc:
        payload = {"ok": False, "checks": [], "error": str(exc)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        for check in payload["checks"]:
            print(f"ok {check['name']}")
        if payload["error"]:
            print(f"error {payload['error']}")
        print("phase0_smoke_ok" if payload["ok"] else "phase0_smoke_failed")
    if not payload["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
