from __future__ import annotations

from typing import Any


class MediaClipError(RuntimeError):
    """Structured, customer-safe clip service failure."""

    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        status_code: int = 400,
        suggested_action: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.user_message = str(user_message)
        self.status_code = int(status_code)
        self.suggested_action = str(suggested_action)
        self.details = dict(details or {})
        super().__init__(self.user_message)

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "user_message": self.user_message,
        }
        if self.suggested_action:
            payload["suggested_action"] = self.suggested_action
        payload.update(self.details)
        return payload


class ClipCancelled(RuntimeError):
    pass


__all__ = ["ClipCancelled", "MediaClipError"]
