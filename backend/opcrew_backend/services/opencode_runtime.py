from __future__ import annotations

from typing import Any

from opcrew_backend.adapters.opencode import OpenCodeSessionClient, discover_opencode_servers
from opcrew_backend.context import now_ms


def opencode_runtime_status(probe_status: str | None, healthy: bool | None) -> str:
    if healthy:
        return "ready"
    if probe_status == "auth_required":
        return "auth_required"
    if probe_status in {"unexpected_response", "http_error", "unreachable", "error"}:
        return "failed"
    return "discovered"


def save_opencode_discovery(ctx: Any, result: dict[str, Any]) -> dict[str, Any]:
    selected = result.get("selected") or {}
    if selected:
        base_url = str(selected.get("base_url") or "").strip()
        username = str(selected.get("username") or "").strip()
        password = str(selected.get("password") or "").strip()
        ctx.set_setting("opencode.base_url", base_url)
        ctx.set_setting("opencode.username", username)
        ctx.set_setting("opencode.password", password)
        ctx.runtime_repo.update_runtime(
            "opencode",
            status=opencode_runtime_status(selected.get("probe_status"), selected.get("healthy")),
            base_url=base_url,
            health_url=f"{base_url}/global/health" if base_url else None,
            auth_username=username or None,
            auth_password=password or None,
            auth_source=selected.get("auth_source"),
            version=selected.get("version"),
            error=selected.get("error"),
            checked_at=now_ms(),
        )
        return selected

    ctx.runtime_repo.update_runtime(
        "opencode",
        status="failed",
        base_url=None,
        health_url=None,
        auth_username=None,
        auth_password=None,
        auth_source=None,
        version=None,
        error="No OpenCode server process discovered",
        checked_at=now_ms(),
    )
    ctx.set_setting("opencode.base_url", "")
    ctx.set_setting("opencode.username", "")
    ctx.set_setting("opencode.password", "")
    return {}


def discover_and_save_opencode_runtime(ctx: Any, reason: str = "") -> dict[str, Any]:
    result = discover_opencode_servers()
    selected = save_opencode_discovery(ctx, result)
    ctx.event(
        "info" if result.get("ok") else "warn",
        "opencode",
        "OpenCode server discovery completed",
        {
            "ok": result.get("ok"),
            "reason": reason,
            "selected": selected.get("base_url"),
            "candidate_count": len(result.get("candidates", [])),
            "probe_status": selected.get("probe_status"),
            "http_status": selected.get("http_status"),
            "auth_source": selected.get("auth_source"),
        },
    )
    return result


def opencode_client_for_context(ctx: Any, session_row: dict[str, Any], incomplete_message: str) -> OpenCodeSessionClient:
    base_url = str(ctx.get_setting("opencode.base_url") or "").strip()
    username = str(ctx.get_setting("opencode.username") or "").strip()
    password = str(ctx.get_setting("opencode.password") or "").strip()
    if not base_url or not username or not password:
        raise RuntimeError(incomplete_message)
    return OpenCodeSessionClient(base_url=base_url, username=username, password=password, directory=str(session_row["workspace_dir"]))
