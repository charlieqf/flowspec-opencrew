from __future__ import annotations

import json
import socket
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException

from ..adapters.opencode import invoke_smoke
from ..context import AppContext, now_ms
from ..schemas import NpcSkillPayload, UrlConfigPayload


def build_step3_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    LOCAL_FRONTEND_URL = ctx.config.frontend_url
    LOCAL_BACKEND_API_URL = f"{ctx.config.backend_url}/api/"
    PUBLISH_SKILL_KIND = "validate"
    DEFAULT_PUBLISH_SKILL = {
        "title": "Validate URL",
        "content": "".join(
            [
                "Goal: validate one external OpenCrew URL against the fixed local access standard.\n\n",
                "Hard requirements:\n",
                "1. Treat the local frontend as http://127.0.0.1:18080/.\n",
                "2. Treat the local backend API as http://127.0.0.1:8011/api/.\n",
                "3. Keep npc target_addr fixed at 127.0.0.1:18080.\n",
                "4. Produce the matching public nginx and NPS/NPC mapping recommendation for the exact target URL.\n",
                "5. Validate local frontend, local backend API, frontend proxy API, and NPC availability as the required pass criteria.\n",
                "6. Treat public frontend and public API probes as advisory checks only; they must not flip the final URL status to failed when the site is already up.\n\n",
                "Expected output:\n",
                "- Normalized target URL\n",
                "- Deployment mode (subdomain or subpath)\n",
                "- Recommended public nginx config\n",
                "- Recommended NPS/NPC config\n",
                "- Final status `verified` when the local chain is healthy\n",
                "- Detailed validation checks with recommended fixes\n",
                "- A markdown guide explaining the full setup and verification flow\n",
            ]
        ),
    }

    if not ctx.skill_repo.get("publish", PUBLISH_SKILL_KIND):
        ctx.skill_repo.upsert(
            "publish",
            PUBLISH_SKILL_KIND,
            DEFAULT_PUBLISH_SKILL["title"],
            DEFAULT_PUBLISH_SKILL["content"],
            now_ms(),
        )

    def get_publish_skill() -> dict[str, Any]:
        row = ctx.skill_repo.get("publish", PUBLISH_SKILL_KIND)
        if row:
            row["default_content"] = DEFAULT_PUBLISH_SKILL["content"]
            return row
        return {
            "kind": PUBLISH_SKILL_KIND,
            "title": DEFAULT_PUBLISH_SKILL["title"],
            "content": DEFAULT_PUBLISH_SKILL["content"],
            "updated_at": now_ms(),
            "default_content": DEFAULT_PUBLISH_SKILL["content"],
        }

    def create_task(skill_snapshot: str) -> int:
        return ctx.task_repo.create("publish_validate", "queued", skill_snapshot, now_ms(), now_ms())

    def log_task(task_id: int, phase: str, level: str, message: str) -> None:
        timestamp = now_ms()
        ctx.task_repo.add_log(task_id, phase, level, message, timestamp)
        ctx.event(level if level in {"info", "warn", "error"} else "info", "publish", message, {"task_id": task_id, "phase": phase})

    def current_publish() -> dict[str, Any]:
        row = ctx.runtime_repo.get_runtime("publish") or {}
        payload = {
            "status": row.get("status") or "idle",
            "input_url": row.get("input_url") or "",
            "normalized_url": row.get("normalized_url") or "",
            "scheme": row.get("scheme") or "https",
            "domain": row.get("domain") or "",
            "path_prefix": row.get("path_prefix") or "/",
            "nginx_config": row.get("nginx_config") or "",
            "nps_config": row.get("nps_config") or "",
            "message": row.get("message") or "Waiting for URL input",
            "last_error": row.get("last_error"),
            "test_detail": row.get("test_detail"),
            "updated_at": row.get("updated_at"),
            "tested_at": row.get("tested_at"),
            "deployment_mode": "subpath" if str(row.get("path_prefix") or "/") not in {"", "/"} else "subdomain",
            "local_frontend_url": LOCAL_FRONTEND_URL,
            "local_backend_api_url": LOCAL_BACKEND_API_URL,
            "public_api_url": "",
            "allowed_hosts_hint": "Keep allowedHosts in OpenCrew/frontend/vite.config.ts aligned with every external hostname that proxies to the Vite server, such as www.goldenstand.cn or OpenCrew.goldenstand.cn.",
            "guide_markdown": "",
        }
        if payload["normalized_url"]:
            payload.update(build_guide_payload(payload))
        return payload

    def update_publish(**fields: Any) -> None:
        if not fields:
            return
        ctx.runtime_repo.update_runtime("publish", **fields, updated_at=now_ms())

    def normalize_url(value: str) -> dict[str, str]:
        text = value.strip()
        if not text:
            raise HTTPException(status_code=400, detail="URL is required")
        parsed = urlparse(text if "://" in text else f"https://{text}")
        if parsed.scheme not in {"http", "https"}:
            raise HTTPException(status_code=400, detail="Only http and https URLs are supported")
        if not parsed.netloc:
            raise HTTPException(status_code=400, detail="URL must include a host")
        path_prefix = parsed.path or "/"
        if not path_prefix.startswith("/"):
            path_prefix = f"/{path_prefix}"
        if path_prefix != "/" and path_prefix.endswith("/"):
            path_prefix = path_prefix.rstrip("/")
        normalized = f"{parsed.scheme}://{parsed.netloc}{path_prefix}"
        return {
            "input_url": text,
            "normalized_url": normalized,
            "scheme": parsed.scheme,
            "domain": parsed.netloc,
            "path_prefix": path_prefix,
        }

    def get_npc_config() -> dict[str, Any]:
        return {
            "public_base_url": str(ctx.get_setting("npc.public_base_url") or "").strip(),
            "target_addr": str(ctx.get_setting("npc.target_addr") or "127.0.0.1:18080").strip(),
            "server_port": int(ctx.get_setting("npc.server_port") or 10000),
            "server_addr": str(ctx.get_setting("npc.server_addr") or "113.125.202.171:8024").strip(),
            "vkey": str(ctx.get_setting("npc.vkey") or "").strip(),
            "mode": str(ctx.get_setting("npc.mode") or "tcp").strip(),
        }

    def build_path_match(path_prefix: str) -> str:
        if path_prefix in {"", "/"}:
            return "/"
        return f"{path_prefix}/"

    def deployment_mode(path_prefix: str) -> str:
        return "subpath" if path_prefix not in {"", "/"} else "subdomain"

    def join_url(base_url: str, suffix: str) -> str:
        return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"

    def public_api_url(normalized_url: str, path_prefix: str) -> str:
        return join_url(normalized_url if path_prefix != "/" else normalized_url.rstrip("/") + "/", "api/setup/summary")

    def share_probe_url(normalized_url: str, path_prefix: str) -> str:
        return join_url(normalized_url if path_prefix != "/" else normalized_url.rstrip("/") + "/", f"session/share/{quote('health-check')}")

    def build_nginx_config(domain: str, path_prefix: str, server_port: int) -> str:
        if deployment_mode(path_prefix) == "subpath":
            return "\n".join(
                [
                    f"location = {path_prefix} {{",
                    f"    return 301 {path_prefix}/;",
                    "}",
                    "",
                    f"location {path_prefix}/ {{",
                    f"    proxy_pass http://127.0.0.1:{server_port}/;",
                    "    proxy_http_version 1.1;",
                    "    proxy_set_header Host $host;",
                    "    proxy_set_header X-Real-IP $remote_addr;",
                    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                    "    proxy_set_header X-Forwarded-Host $host;",
                    "    proxy_set_header X-Forwarded-Proto $scheme;",
                    f"    proxy_set_header X-Forwarded-Prefix {path_prefix};",
                    "}",
                ]
            )
        return "\n".join(
            [
                "server {",
                "    listen 80;",
                f"    server_name {domain};",
                "",
                "    location / {",
                f"        proxy_pass http://127.0.0.1:{server_port};",
                "        proxy_http_version 1.1;",
                "        proxy_set_header Host $host;",
                "        proxy_set_header X-Real-IP $remote_addr;",
                "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                "        proxy_set_header X-Forwarded-Host $host;",
                "        proxy_set_header X-Forwarded-Proto $scheme;",
                "    }",
                "}",
            ]
        )

    def build_guide_payload(config: dict[str, Any]) -> dict[str, Any]:
        path_prefix = str(config.get("path_prefix") or "/")
        mode = deployment_mode(path_prefix)
        normalized = str(config.get("normalized_url") or "")
        public_api = public_api_url(normalized, path_prefix) if normalized else ""
        lines = [
            f"# OpenCrew Target URL Guide: {normalized}",
            "",
            "## Access Standard",
            f"- Local frontend: `{LOCAL_FRONTEND_URL}`",
            f"- Local backend API: `{LOCAL_BACKEND_API_URL}`",
            "- Local frontend proxies `/api` and `/session/share` to `127.0.0.1:8011`.",
            "- NPC target must stay `127.0.0.1:18080`.",
            "",
            "## URL Mode",
            f"- Deployment mode: `{mode}`",
            f"- External hostname: `{config.get('domain') or '-'}`",
            f"- External path prefix: `{path_prefix}`",
            f"- Public API probe URL: `{public_api or '-'}`",
            "",
            "## allowedHosts",
            "- File: `OpenCrew/frontend/vite.config.ts`",
            "- Keep `allowedHosts` aligned with every external hostname that proxies to the Vite server.",
            f"- For this URL, include `{config.get('domain') or '-'}`.",
            "- Subdomain example: add `OpenCrew.goldenstand.cn`.",
            "- Subpath example: add `www.goldenstand.cn`.",
            "",
            "## NPS / NPC",
            f"- `server_addr={config.get('server_addr') or get_npc_config()['server_addr']}`",
            f"- `server_port={config.get('server_port') or get_npc_config()['server_port']}`",
            f"- `target_addr={config.get('target_addr') or get_npc_config()['target_addr']}`",
            "- `target_addr` should always stay on local frontend `127.0.0.1:18080`.",
            "",
            "## Public Nginx",
            "- Subdomain mode: proxy `/` directly to the exposed NPS port.",
            "- Subpath mode: strip the external prefix before proxying to NPS and set `X-Forwarded-Prefix`.",
            "",
            "## Validation Flow",
            "1. Confirm local frontend responds on `127.0.0.1:18080`.",
            "2. Confirm local backend responds on `127.0.0.1:8011/api/setup/summary`.",
            "3. Confirm local frontend proxy responds on `127.0.0.1:18080/api/setup/summary`.",
            "4. Confirm NPC is connected and exposing the mapped port.",
            "5. Mark the URL as verified once the required local chain is healthy.",
            f"6. Use the public URL probe `{normalized}` as an advisory check.",
            f"7. Use the public API probe `{public_api or '-'} ` as an advisory check.",
        ]
        return {
            "deployment_mode": mode,
            "local_frontend_url": LOCAL_FRONTEND_URL,
            "local_backend_api_url": LOCAL_BACKEND_API_URL,
            "public_api_url": public_api,
            "allowed_hosts_hint": f"Update allowedHosts in OpenCrew/frontend/vite.config.ts to include {config.get('domain') or '-'} if it is not already listed.",
            "guide_markdown": "\n".join(lines),
        }

    def build_recommendation(url_value: str) -> dict[str, Any]:
        parsed = normalize_url(url_value)
        npc = get_npc_config()
        nginx_config = build_nginx_config(parsed["domain"], parsed["path_prefix"], npc["server_port"])
        nps_config = "\n".join(
            [
                "# NPS / NPC mapping recommendation",
                f"server_addr={npc['server_addr']}",
                f"vkey={npc['vkey'] or '<fill-your-vkey>'}",
                f"mode={npc['mode']}",
                f"server_port={npc['server_port']}",
                f"target_addr={npc['target_addr']}",
                f"public_host={parsed['domain']}",
                f"public_path={parsed['path_prefix']}",
                f"public_base_url={npc['public_base_url'] or '<optional-public-base-url>'}",
            ]
        )
        return {
            **parsed,
            "nginx_config": nginx_config,
            "nps_config": nps_config,
            "server_addr": npc["server_addr"],
            "server_port": npc["server_port"],
            "target_addr": npc["target_addr"],
            "message": f"Generated recommendations for {parsed['normalized_url']}",
            **build_guide_payload({**parsed, **npc}),
        }

    def can_open_socket(target_addr: str) -> tuple[bool, str]:
        try:
            host, port_text = target_addr.rsplit(":", 1)
            port = int(port_text)
        except ValueError:
            return False, f"Invalid target_addr: {target_addr}"
        try:
            with socket.create_connection((host, port), timeout=3):
                return True, f"Target {target_addr} is reachable"
        except Exception as exc:
            return False, f"Target {target_addr} is unreachable: {exc}"

    def socket_target(url_value: str) -> str:
        parsed = urlparse(url_value)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return f"{host}:{port}"

    def probe_public_url(url_value: str) -> tuple[bool, str]:
        try:
            request = Request(url_value, headers={"User-Agent": "OpenCrew URL Test"})
            with urlopen(request, timeout=5) as response:
                return True, f"Public URL responded with HTTP {getattr(response, 'status', 200)}"
        except HTTPError as exc:
            return False, f"Public URL returned HTTP {exc.code}"
        except URLError as exc:
            return False, f"Public URL is unreachable: {exc.reason}"
        except Exception as exc:
            return False, f"Public URL test failed: {exc}"

    def probe_http(url_value: str, label: str) -> tuple[bool, str]:
        try:
            request = Request(url_value, headers={"User-Agent": "OpenCrew URL Test"})
            with urlopen(request, timeout=5) as response:
                return True, f"{label} responded with HTTP {getattr(response, 'status', 200)}"
        except HTTPError as exc:
            return False, f"{label} returned HTTP {exc.code}"
        except URLError as exc:
            return False, f"{label} is unreachable: {exc.reason}"
        except Exception as exc:
            return False, f"{label} probe failed: {exc}"

    def build_check(name: str, ok: bool, message: str, category: str, recommended_fix: str = "", severity: str | None = None) -> dict[str, Any]:
        return {
            "name": name,
            "ok": ok,
            "message": message,
            "category": category,
            "severity": severity or ("info" if ok else "error"),
            "recommended_fix": recommended_fix,
        }

    def run_validation(url_value: str, task_id: int | None = None) -> dict[str, Any]:
        recommendation = build_recommendation(url_value)
        npc = ctx.runtime_repo.get_runtime("npc") or {}
        npc_config = get_npc_config()
        target_addr = npc_config["target_addr"]

        if task_id is not None:
            log_task(task_id, "input", "info", f"Parsed target URL {recommendation['normalized_url']}")
            log_task(task_id, "recommend", "info", f"Detected deployment mode {recommendation['deployment_mode']} for {recommendation['domain']}{recommendation['path_prefix']}")

        socket_ok, socket_message = can_open_socket(target_addr)
        backend_socket_ok, backend_socket_message = can_open_socket(socket_target(ctx.config.backend_url))
        frontend_http_ok, frontend_http_message = probe_http(LOCAL_FRONTEND_URL, "Local frontend")
        backend_http_ok, backend_http_message = probe_http(join_url(LOCAL_BACKEND_API_URL, "setup/summary"), "Local backend API")
        proxy_http_ok, proxy_http_message = probe_http(join_url(LOCAL_FRONTEND_URL, "api/setup/summary"), "Local frontend proxy API")
        opencode_ok, opencode_message = invoke_smoke(
            str(ctx.get_setting("opencode.base_url") or ""),
            username=str(ctx.get_setting("opencode.username") or ""),
            password=str(ctx.get_setting("opencode.password") or ""),
        )
        public_ok, public_message = probe_public_url(recommendation["normalized_url"])
        public_api_ok, public_api_message = probe_http(str(recommendation["public_api_url"]), "Public API")
        subpath_hint_ok = recommendation["deployment_mode"] != "subpath" or recommendation["path_prefix"] not in {"", "/"}

        checks = [
            build_check("url", True, f"Parsed {recommendation['normalized_url']}", "input"),
            build_check("npc", str(npc.get("verify_status") or "") == "available", f"NPC status: {npc.get('verify_status') or 'idle'}", "tunnel", "Run or reconnect NPC until verify_status becomes available."),
            build_check("frontend_socket", socket_ok, socket_message, "local", "Start the frontend on 127.0.0.1:18080 and keep npc target_addr fixed there."),
            build_check("backend_socket", backend_socket_ok, backend_socket_message, "local", "Start the backend on 127.0.0.1:8011 before validating the public mapping."),
            build_check("frontend_http", frontend_http_ok, frontend_http_message, "local", "Open http://127.0.0.1:18080/ locally and fix any frontend startup errors."),
            build_check("backend_api_http", backend_http_ok, backend_http_message, "local", "Open http://127.0.0.1:8011/api/setup/summary locally and ensure backend routing is healthy."),
            build_check("frontend_proxy_api_http", proxy_http_ok, proxy_http_message, "local", "Confirm Vite proxy forwards /api to 127.0.0.1:8011."),
            build_check("opencode", opencode_ok, opencode_message, "local", "Refresh the OpenCode base URL and credentials in Step 1."),
            build_check("public_frontend", public_ok, public_message, "public", "Verify public nginx forwards the external URL to the NPS exposed port.", "info" if public_ok else "warn"),
            build_check("public_api", public_api_ok, public_api_message, "public", "Verify the same public mapping also forwards /api/setup/summary through to the local frontend proxy.", "info" if public_api_ok else "warn"),
            build_check("subpath_header_hint", subpath_hint_ok, "Subpath deployments must set X-Forwarded-Prefix to keep generated links under the external prefix." if recommendation["deployment_mode"] == "subpath" else "No forwarded prefix is needed for subdomain mode.", "public", "Set proxy_set_header X-Forwarded-Prefix to the external subpath such as /OpenCrew.", "info" if subpath_hint_ok else "warn"),
        ]

        if task_id is not None:
            for check in checks:
                level = "info" if check["ok"] else ("warn" if check["severity"] == "warn" else "error")
                log_task(task_id, check["category"], level, f"{check['name']}: {check['message']}")

        required_checks = {"url", "npc", "frontend_socket", "backend_socket", "frontend_http", "backend_api_http", "frontend_proxy_api_http", "opencode"}
        local_chain_ok = all(item["ok"] for item in checks if item["name"] in required_checks)
        success = bool(public_ok or local_chain_ok)
        detail = json.dumps(checks, ensure_ascii=True)
        update_publish(
            status="verified" if success else "failed",
            input_url=recommendation["input_url"],
            normalized_url=recommendation["normalized_url"],
            scheme=recommendation["scheme"],
            domain=recommendation["domain"],
            path_prefix=recommendation["path_prefix"],
            nginx_config=recommendation["nginx_config"],
            nps_config=recommendation["nps_config"],
            message="Publish URL verified" if success else "Publish URL test failed",
            last_error=None if success else next((item["message"] for item in checks if not item["ok"] and item["name"] in required_checks), "Unknown test failure"),
            test_detail=detail,
            tested_at=now_ms(),
        )
        ctx.event("info" if success else "error", "publish", "Publish URL test completed", {"success": success, "url": recommendation["normalized_url"]})
        return {"ok": success, "checks": checks, **current_publish()}

    @router.get("/api/setup/publish/config")
    async def publish_config() -> dict[str, Any]:
        return current_publish()

    @router.put("/api/setup/publish/config")
    async def publish_save(payload: UrlConfigPayload) -> dict[str, Any]:
        recommendation = build_recommendation(payload.url)
        update_publish(
            status="configured",
            input_url=recommendation["input_url"],
            normalized_url=recommendation["normalized_url"],
            scheme=recommendation["scheme"],
            domain=recommendation["domain"],
            path_prefix=recommendation["path_prefix"],
            nginx_config=recommendation["nginx_config"],
            nps_config=recommendation["nps_config"],
            message=recommendation["message"],
            last_error=None,
        )
        ctx.event("info", "publish", "Publish URL config saved", {"url": recommendation["normalized_url"]})
        return {"ok": True, **current_publish()}

    @router.post("/api/setup/publish/recommend")
    async def publish_recommend(payload: UrlConfigPayload) -> dict[str, Any]:
        recommendation = build_recommendation(payload.url)
        return {"ok": True, **recommendation}

    @router.post("/api/setup/publish/test")
    async def publish_test(payload: UrlConfigPayload) -> dict[str, Any]:
        return run_validation(payload.url)

    @router.get("/api/setup/publish/skills/{kind}")
    async def publish_skill(kind: str) -> dict[str, Any]:
        if kind != PUBLISH_SKILL_KIND:
            raise HTTPException(status_code=404, detail="Skill not found")
        return get_publish_skill()

    @router.put("/api/setup/publish/skills/{kind}")
    async def publish_skill_save(kind: str, payload: NpcSkillPayload) -> dict[str, Any]:
        if kind != PUBLISH_SKILL_KIND:
            raise HTTPException(status_code=404, detail="Skill not found")
        ctx.skill_repo.upsert("publish", PUBLISH_SKILL_KIND, DEFAULT_PUBLISH_SKILL["title"], payload.content.strip(), now_ms())
        ctx.event("info", "publish", "Publish skill updated", {"kind": kind})
        return {"ok": True}

    @router.post("/api/setup/publish/skills/{kind}/restore-default")
    async def publish_skill_restore(kind: str) -> dict[str, Any]:
        if kind != PUBLISH_SKILL_KIND:
            raise HTTPException(status_code=404, detail="Skill not found")
        ctx.skill_repo.upsert(
            "publish",
            PUBLISH_SKILL_KIND,
            DEFAULT_PUBLISH_SKILL["title"],
            DEFAULT_PUBLISH_SKILL["content"],
            now_ms(),
        )
        ctx.event("info", "publish", "Publish skill restored", {"kind": kind})
        return {"ok": True}

    @router.post("/api/setup/publish/validate")
    async def publish_validate(payload: UrlConfigPayload) -> dict[str, Any]:
        skill_snapshot = get_publish_skill()["content"]
        task_id = create_task(skill_snapshot)
        update_publish(status="validating", input_url=payload.url.strip(), message="Running URL validation", last_error=None)

        def runner() -> None:
            ctx.task_repo.update(task_id, status="running", started_at=now_ms())
            try:
                result = run_validation(payload.url, task_id)
                ctx.task_repo.update(
                    task_id,
                    status="succeeded" if result["ok"] else "failed",
                    summary=json.dumps(result, ensure_ascii=True),
                    error=None if result["ok"] else str(result.get("last_error") or "Validation failed"),
                    finished_at=now_ms(),
                )
                log_task(task_id, "result", "info" if result["ok"] else "error", result["message"])
            except Exception as exc:
                error_text = str(exc)
                ctx.task_repo.update(task_id, status="failed", summary=None, error=error_text, finished_at=now_ms())
                update_publish(status="failed", last_error=error_text, message="Publish URL test failed")
                log_task(task_id, "result", "error", error_text)

        threading.Thread(target=runner, name=f"publish-validate-{task_id}", daemon=True).start()
        return {"ok": True, "task_id": task_id}

    @router.get("/api/setup/publish/tasks/{task_id}")
    async def publish_task(task_id: int) -> dict[str, Any]:
        task = ctx.task_repo.get(task_id, "publish_validate")
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @router.get("/api/setup/publish/tasks/{task_id}/logs")
    async def publish_task_logs(task_id: int) -> dict[str, Any]:
        task = ctx.task_repo.get(task_id, "publish_validate")
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        logs = ctx.task_repo.list_logs(task_id)
        return {"items": logs}

    return router
