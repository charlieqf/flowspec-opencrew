from __future__ import annotations

import os
import socket
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from opcrew_backend.context import AppContext
from opcrew_backend.services.provider_resolver import mihomo_proxy_url


SUBSCRIPTION_REF = "mihomo_subscription_url"


class MihomoConfigPayload(BaseModel):
    enabled: bool = False
    subscription_url: str = ""


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def build_mihomo_router(ctx: AppContext) -> APIRouter:
    router = APIRouter(prefix="/api/setup/mihomo", tags=["mihomo"])

    @router.get("/config")
    def get_config() -> dict[str, Any]:
        proxy_url = mihomo_proxy_url()
        enabled = bool(ctx.get_setting("mihomo.enabled", False))
        subscription_url = ctx.secret_store.get(SUBSCRIPTION_REF)
        return {
            "enabled": enabled,
            "has_subscription_url": bool(subscription_url),
            "proxy_url": proxy_url,
            "listen_host": "127.0.0.1",
            "http_port": int(os.environ.get("OPENCREW_MIHOMO_HTTP_PORT", "7890")),
            "socks_port": int(os.environ.get("OPENCREW_MIHOMO_SOCKS_PORT", "7891")),
            "status": "enabled" if enabled else "disabled",
            "running": _port_open("127.0.0.1", int(os.environ.get("OPENCREW_MIHOMO_HTTP_PORT", "7890"))),
        }

    @router.put("/config")
    def save_config(payload: MihomoConfigPayload) -> dict[str, Any]:
        ctx.set_setting("mihomo.enabled", bool(payload.enabled))
        if payload.subscription_url.strip():
            ctx.secret_store.set(SUBSCRIPTION_REF, payload.subscription_url.strip())
        ctx.event("info", "mihomo", "mihomo config saved", {"enabled": bool(payload.enabled), "has_subscription_url": bool(ctx.secret_store.get(SUBSCRIPTION_REF))})
        return {"ok": True, **get_config()}

    @router.post("/test")
    def test_config() -> dict[str, Any]:
        config = get_config()
        ok = bool(config["running"])
        return {
            "ok": ok,
            "status": "success" if ok else "failed",
            "message": "mihomo localhost proxy is reachable" if ok else "mihomo localhost proxy is not listening",
            "detail": config["proxy_url"],
            **config,
        }

    return router
