from __future__ import annotations

from typing import Any

from ..tool_sessions import ToolSessionResultSync, ToolSessionRunner
from ..tool_sessions.schemas import ResyncResult


def finalize_analysis_tool_session(
    ctx: Any,
    runner: ToolSessionRunner,
    *,
    terminal_status: str,
) -> ResyncResult:
    return runner.finalize_session(
        result_syncer=ToolSessionResultSync(
            session_repo=ctx.session_repo,
            engine=getattr(ctx, "engine", None),
            file_service=getattr(ctx, "session_file_service", None),
        ),
        terminal_status=terminal_status,
    )


def result_sync_error(result: ResyncResult) -> str:
    details = "; ".join(str(item).strip() for item in result.errors if str(item).strip())
    return details or "未知结果同步错误"
