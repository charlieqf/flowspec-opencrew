from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from ..adapters.wecom import build_text_reply, parse_wecom_message
from ..context import AppContext, now_ms
from ..schemas import WeComConfigPayload


def build_step3_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/setup/wecom/status")
    async def wecom_status() -> dict[str, Any]:
        config = ctx.runtime_repo.get_wecom_config(include_secret=False) or {}
        runtime = ctx.runtime_repo.get_runtime("wecom") or {}
        return {"config": config, "runtime": runtime}

    @router.post("/api/setup/wecom/save")
    async def wecom_save(payload: WeComConfigPayload) -> dict[str, Any]:
        ctx.runtime_repo.update_wecom_config(
            corp_id=payload.corp_id.strip(),
            agent_id=payload.agent_id.strip(),
            secret=payload.secret.strip(),
            token=payload.token.strip(),
            encoding_aes_key=payload.encoding_aes_key.strip(),
            enabled=1 if payload.enabled else 0,
            updated_at=now_ms(),
        )
        fields = [payload.corp_id, payload.agent_id, payload.secret, payload.token, payload.encoding_aes_key]
        complete = all(bool(v.strip()) for v in fields)
        ctx.runtime_repo.update_runtime(
            "wecom",
            status="configured" if complete else "unconfigured",
            message="Config saved" if complete else "Missing required fields",
            verified_at=None,
            last_error=None if complete else "Missing required fields",
        )
        ctx.event("info", "wecom", "WeCom config saved", {"complete": complete})
        return {"ok": True, "complete": complete}

    @router.post("/api/setup/wecom/verify")
    async def wecom_verify() -> dict[str, Any]:
        config = ctx.runtime_repo.get_wecom_config(include_secret=True) or {}
        required = [
            config.get("corp_id") or "",
            config.get("agent_id") or "",
            config.get("secret") or "",
            config.get("token") or "",
            config.get("encoding_aes_key") or "",
        ]
        if not all(bool(str(v).strip()) for v in required):
            ctx.runtime_repo.update_runtime(
                "wecom",
                status="failed",
                message="Missing required WeCom fields",
                verified_at=None,
                last_error="Missing required WeCom fields",
            )
            raise HTTPException(status_code=400, detail="Missing required WeCom fields")

        tunnel = ctx.runtime_repo.get_runtime("tunnel") or {}
        if tunnel.get("status") != "running" or not tunnel.get("webhook_url"):
            ctx.runtime_repo.update_runtime(
                "wecom",
                status="failed",
                message="Tunnel is not running",
                verified_at=None,
                last_error="Tunnel is not running",
            )
            raise HTTPException(status_code=400, detail="Tunnel is not running")

        check_code = secrets.token_hex(4).upper()
        ctx.set_setting("wecom.check_code", check_code)
        ctx.runtime_repo.update_runtime(
            "wecom",
            status="ready",
            message="WeCom config ready. Send test message with check code.",
            verified_at=now_ms(),
            last_error=None,
        )
        ctx.event("info", "wecom", "WeCom verify passed", {"check_code": check_code})
        return {
            "ok": True,
            "status": "ready",
            "check_code": check_code,
            "webhook_url": tunnel.get("webhook_url"),
        }

    @router.get("/webhooks/wecom")
    async def wecom_handshake(
        msg_signature: str = Query(default=""),
        timestamp: str = Query(default=""),
        nonce: str = Query(default=""),
        echostr: str = Query(default=""),
    ) -> Response:
        ctx.event(
            "info",
            "wecom",
            "WeCom handshake received",
            {"msg_signature": bool(msg_signature), "timestamp": timestamp, "nonce": nonce},
        )
        return PlainTextResponse(content=echostr or "ok")

    @router.post("/webhooks/wecom")
    async def wecom_messages(request: Request) -> Response:
        raw = (await request.body()).decode("utf-8", errors="ignore")
        try:
            message = parse_wecom_message(raw)
        except Exception as exc:
            ctx.event("error", "wecom", "Failed parsing WeCom message", {"error": str(exc)})
            raise HTTPException(status_code=400, detail="Invalid WeCom payload")

        config = ctx.runtime_repo.get_wecom_config(include_secret=False) or {}
        from .step4_verify import handle_incoming_verification

        ok, invoke_detail = handle_incoming_verification(ctx, message.sender, message.content, message.external_id)
        from_user = config.get("corp_id") or "OpenCrew"
        reply = build_text_reply(
            to_user=message.sender,
            from_user=from_user,
            content=(
                "OpenCrew received your message and verified OpenCode is reachable."
                if ok
                else f"OpenCrew received your message, but OpenCode check failed: {invoke_detail}"
            ),
        )
        return Response(content=reply, media_type="application/xml")

    return router
