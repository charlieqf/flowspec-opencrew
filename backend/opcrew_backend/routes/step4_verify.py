from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..adapters.opencode import invoke_smoke
from ..context import AppContext, now_ms


def handle_incoming_verification(
    ctx: AppContext, sender: str, content: str, external_id: str
) -> tuple[bool, str]:
    ok, invoke_detail = invoke_smoke(
        ctx.get_setting("opencode.base_url"),
        username=ctx.get_setting("opencode.username"),
        password=ctx.get_setting("opencode.password"),
    )
    status = "success" if ok else "failed"
    ctx.verification_repo.add_message_log(
        source="wecom",
        external_id=external_id,
        sender=sender,
        content=content,
        status=status,
        result=invoke_detail,
        created_at=now_ms(),
    )
    ctx.verification_repo.add_run(
        status="success" if ok else "failed",
        message="WeCom test message processed" if ok else "WeCom message received but OpenCode check failed",
        detail=invoke_detail,
        created_at=now_ms(),
    )
    ctx.event(
        "info" if ok else "error",
        "verify",
        "WeCom test message handled",
        {"sender": sender, "content": content[:120], "result": invoke_detail},
    )
    return ok, invoke_detail


def build_step4_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/setup/verification/status")
    async def verification_status() -> dict[str, Any]:
        row = ctx.verification_repo.latest()
        if not row:
            row = {
                "status": "waiting_message",
                "message": "Waiting for first WeCom test message",
                "detail": None,
                "created_at": None,
            }
        return row

    @router.post("/api/setup/verification/reset")
    async def verification_reset() -> dict[str, Any]:
        ctx.verification_repo.add_run(
            status="waiting_message",
            message="Waiting for first WeCom test message",
            detail=None,
            created_at=now_ms(),
        )
        ctx.event("info", "verify", "Verification state reset", None)
        return {"ok": True}

    return router
