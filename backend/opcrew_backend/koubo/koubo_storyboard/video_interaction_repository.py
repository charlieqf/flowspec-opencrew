from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from opcrew_backend.db.schema import sessions, video_interaction_threads, video_interaction_turns

from .usage_metering import stable_usage_request_id


TERMINAL_TURN_STATUSES = {"completed", "failed", "cancelled"}
PUBLIC_TURN_FIELDS = {
    "parent_turn_id",
    "client_action_id",
    "operation",
    "output_asset_id",
    "output_path",
    "status",
    "provider_state_status",
    "provider_state_expires_at",
    "provider_expiry_source",
    "delete_status",
    "created_at",
    "updated_at",
}


class VideoInteractionError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class TurnClaim:
    created: bool
    replayed: bool
    lease_token: str
    turn: dict[str, Any]

    @property
    def public(self) -> dict[str, Any]:
        return public_turn(self.turn)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _lease_seconds() -> int:
    try:
        value = int(os.environ.get("OPENCREW_VIDEO_INTERACTION_LEASE_SECONDS", "900"))
    except ValueError:
        value = 900
    return max(60, min(value, 3600))


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(1, value)


def _positive_env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(0.0, value)


def _uuid(value: Any, *, field: str) -> str:
    normalized = _text(value)
    try:
        return str(uuid.UUID(normalized))
    except (ValueError, AttributeError) as exc:
        raise VideoInteractionError(
            "video_stateful_invalid_request",
            f"{field} must be a UUID",
        ) from exc


def action_scope_digest(
    *,
    operation: str,
    prompt: str,
    parent_turn_id: str,
    input_asset_id: str,
    input_scope: dict[str, Any] | None = None,
) -> str:
    serialized = json.dumps(
        {
            "operation": operation,
            "prompt": prompt,
            "parent_turn_id": parent_turn_id,
            "input_asset_id": input_asset_id,
            "input_scope": input_scope or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def public_turn(row: dict[str, Any]) -> dict[str, Any]:
    payload = {key: row.get(key) for key in PUBLIC_TURN_FIELDS}
    payload.update(
        {
            "video_thread_id": _text(row.get("thread_id")),
            "video_turn_id": _text(row.get("turn_id")),
            "can_continue": (
                row.get("status") == "completed"
                and row.get("provider_state_status") == "available"
                and row.get("delete_status") not in {"pending", "deleted"}
            ),
        }
    )
    return payload


class VideoInteractionRepository:
    """Database-authoritative state for paid stateful video interactions."""

    def __init__(
        self,
        engine: Engine,
        *,
        now_ms: Callable[[], int] = _now_ms,
        lease_seconds: int | None = None,
    ) -> None:
        self.engine = engine
        self.now_ms = now_ms
        self.lease_seconds = lease_seconds if lease_seconds is not None else _lease_seconds()

    def _lease_expiry(self, now: int) -> int:
        return now + self.lease_seconds * 1000

    @staticmethod
    def _mapping(row: Any) -> dict[str, Any]:
        return dict(row._mapping) if row is not None else {}

    def _turn_for_action(self, conn: Any, task_id: int, actor_id: str, client_action_id: str) -> dict[str, Any]:
        row = conn.execute(
            select(video_interaction_turns).where(
                video_interaction_turns.c.task_id == task_id,
                video_interaction_turns.c.actor_id == actor_id,
                video_interaction_turns.c.client_action_id == client_action_id,
            ).with_for_update()
        ).first()
        return self._mapping(row)

    def _enforce_paid_action_limits(self, conn: Any, *, task_id: int, session_id: int, actor_id: str, now: int) -> None:
        # Lock the durable owner row so concurrent requests cannot both pass
        # task/user quota checks on PostgreSQL.
        conn.execute(select(sessions.c.id).where(sessions.c.id == session_id).with_for_update()).first()
        concurrent_limit = _positive_env_int("OPENCREW_GEMINI_OMNI_TASK_CONCURRENCY", 1)
        hourly_limit = _positive_env_int("OPENCREW_GEMINI_OMNI_USER_HOURLY_LIMIT", 10)
        daily_limit = _positive_env_int("OPENCREW_GEMINI_OMNI_USER_DAILY_LIMIT", 50)
        daily_usd_limit = _positive_env_float("OPENCREW_GEMINI_OMNI_USER_DAILY_MAX_USD", 5.0)
        estimated_usd = _positive_env_float("OPENCREW_GEMINI_OMNI_ESTIMATED_ACTION_USD", 0.10)

        pending_count = int(conn.execute(
            select(func.count()).select_from(video_interaction_turns).where(
                video_interaction_turns.c.task_id == task_id,
                video_interaction_turns.c.actor_id == actor_id,
                video_interaction_turns.c.status == "pending",
            )
        ).scalar_one())
        if pending_count >= concurrent_limit:
            raise VideoInteractionError(
                "video_stateful_edit_in_progress",
                "This task has reached its concurrent stateful video limit",
                status_code=409,
            )
        hour_count = int(conn.execute(
            select(func.count()).select_from(video_interaction_turns).where(
                video_interaction_turns.c.actor_id == actor_id,
                video_interaction_turns.c.created_at >= now - 60 * 60 * 1000,
            )
        ).scalar_one())
        day_count = int(conn.execute(
            select(func.count()).select_from(video_interaction_turns).where(
                video_interaction_turns.c.actor_id == actor_id,
                video_interaction_turns.c.created_at >= now - 24 * 60 * 60 * 1000,
            )
        ).scalar_one())
        if hour_count >= hourly_limit or day_count >= daily_limit or (daily_usd_limit > 0 and (day_count + 1) * estimated_usd > daily_usd_limit):
            raise VideoInteractionError(
                "gemini_omni_quota_exceeded",
                "The server-side Gemini Omni usage limit has been reached",
                status_code=429,
            )

    def _scoped_thread(self, conn: Any, thread_id: str, task_id: int, actor_id: str) -> dict[str, Any]:
        row = conn.execute(
            select(video_interaction_threads).where(
                video_interaction_threads.c.thread_id == thread_id
            ).with_for_update()
        ).first()
        thread = self._mapping(row)
        if not thread or int(thread.get("task_id") or 0) != int(task_id) or _text(thread.get("actor_id")) != actor_id:
            raise VideoInteractionError(
                "gemini_omni_previous_interaction_invalid",
                "The requested video thread does not belong to this task",
                status_code=404,
            )
        return thread

    def _scoped_turn(self, conn: Any, turn_id: str, task_id: int, actor_id: str) -> dict[str, Any]:
        row = conn.execute(
            select(video_interaction_turns).where(
                video_interaction_turns.c.turn_id == turn_id
            ).with_for_update()
        ).first()
        turn = self._mapping(row)
        if not turn or int(turn.get("task_id") or 0) != int(task_id) or _text(turn.get("actor_id")) != actor_id:
            raise VideoInteractionError(
                "gemini_omni_previous_interaction_invalid",
                "The requested parent version does not belong to this task",
                status_code=404,
            )
        return turn

    def create_or_replay_turn(
        self,
        *,
        task_id: int,
        session_id: int,
        actor_id: str,
        operation: str,
        client_action_id: str,
        model_alias: str,
        internal_provider: str,
        internal_model: str,
        prompt: str,
        thread_id: str = "",
        parent_turn_id: str = "",
        input_asset_id: str = "",
        chat_session_id: str = "",
        input_scope: dict[str, Any] | None = None,
    ) -> TurnClaim:
        actor_id = _text(actor_id)
        if not actor_id:
            raise VideoInteractionError("video_stateful_invalid_request", "actor_id is required")
        action_id = _uuid(client_action_id, field="client_action_id")
        operation = _text(operation).lower()
        if operation not in {"generate", "edit", "continue"}:
            raise VideoInteractionError("video_stateful_invalid_request", "operation must be generate, edit, or continue")
        thread_id = _text(thread_id)
        parent_turn_id = _text(parent_turn_id)
        if thread_id:
            thread_id = _uuid(thread_id, field="thread_id")
        if parent_turn_id:
            parent_turn_id = _uuid(parent_turn_id, field="parent_turn_id")
        if operation == "continue" and not thread_id:
            raise VideoInteractionError(
                "gemini_omni_previous_interaction_invalid",
                "continue requires video_thread_id",
            )

        now = self.now_ms()
        lease_token = str(uuid.uuid4())
        turn_id = str(uuid.uuid4())
        scope = action_scope_digest(
            operation=operation,
            prompt=_text(prompt),
            parent_turn_id=parent_turn_id,
            input_asset_id=_text(input_asset_id),
            input_scope=input_scope,
        )
        usage_request_id = stable_usage_request_id(
            "koubo_gemini_omni_video", task_id, turn_id, operation
        )

        try:
            with self.engine.begin() as conn:
                existing = self._turn_for_action(conn, task_id, actor_id, action_id)
                if existing:
                    if existing["operation"] != operation or existing["client_action_scope"] != scope:
                        raise VideoInteractionError(
                            "video_stateful_idempotency_conflict",
                            "client_action_id was already used for a different paid action",
                            status_code=409,
                        )
                    return TurnClaim(False, True, "", existing)

                self._enforce_paid_action_limits(
                    conn,
                    task_id=task_id,
                    session_id=session_id,
                    actor_id=actor_id,
                    now=now,
                )

                if thread_id:
                    thread = self._scoped_thread(conn, thread_id, task_id, actor_id)
                else:
                    thread_id = str(uuid.uuid4())
                    thread = {
                        "thread_id": thread_id,
                        "task_id": task_id,
                        "session_id": session_id,
                        "actor_id": actor_id,
                        "head_turn_id": None,
                        "row_version": 0,
                    }
                    conn.execute(
                        video_interaction_threads.insert().values(
                            thread_id=thread_id,
                            task_id=task_id,
                            session_id=session_id,
                            actor_id=actor_id,
                            chat_session_id=_text(chat_session_id) or None,
                            model_alias=_text(model_alias),
                            internal_provider=_text(internal_provider),
                            internal_model=_text(internal_model),
                            head_turn_id=None,
                            status="active",
                            lease_token=None,
                            lease_expires_at=None,
                            row_version=0,
                            created_at=now,
                            updated_at=now,
                        )
                    )

                if _text(thread.get("status")) in {"deleting", "deleted"}:
                    raise VideoInteractionError(
                        "gemini_omni_previous_interaction_invalid",
                        "This video thread is being deleted",
                        status_code=409,
                    )
                if _text(thread.get("lease_token")) and int(thread.get("lease_expires_at") or 0) > now:
                    raise VideoInteractionError(
                        "video_stateful_edit_in_progress",
                        "Another stateful edit is already running for this video thread",
                        status_code=409,
                    )

                if operation == "continue" and not parent_turn_id:
                    parent_turn_id = _text(thread.get("head_turn_id"))
                if parent_turn_id:
                    parent = self._scoped_turn(conn, parent_turn_id, task_id, actor_id)
                    if parent["thread_id"] != thread_id or parent["status"] != "completed":
                        raise VideoInteractionError(
                            "gemini_omni_previous_interaction_invalid",
                            "The parent version is not a completed turn in this thread",
                            status_code=409,
                        )
                elif operation == "continue":
                    raise VideoInteractionError(
                        "gemini_omni_previous_interaction_invalid",
                        "This video thread has no completed version to continue",
                        status_code=409,
                    )

                expected_head = _text(thread.get("head_turn_id")) or None
                expected_version = int(thread.get("row_version") or 0)
                conn.execute(
                    update(video_interaction_threads)
                    .where(video_interaction_threads.c.thread_id == thread_id)
                    .values(
                        lease_token=lease_token,
                        lease_expires_at=self._lease_expiry(now),
                        updated_at=now,
                    )
                )
                values = {
                    "turn_id": turn_id,
                    "thread_id": thread_id,
                    "task_id": task_id,
                    "actor_id": actor_id,
                    "parent_turn_id": parent_turn_id or None,
                    "client_action_id": action_id,
                    "client_action_scope": scope,
                    "request_config_json": input_scope or {},
                    "usage_request_id": usage_request_id,
                    "local_usage_id": None,
                    "interaction_id": None,
                    "operation": operation,
                    "prompt": _text(prompt),
                    "input_asset_id": _text(input_asset_id) or None,
                    "output_asset_id": None,
                    "output_path": None,
                    "status": "pending",
                    "provider_request_status": "not_sent",
                    "provider_state_status": "pending",
                    "provider_state_expires_at": None,
                    "provider_expiry_source": "unknown",
                    "delete_status": "not_requested",
                    "delete_attempts": 0,
                    "delete_error": None,
                    "expected_head_turn_id": expected_head,
                    "expected_row_version": expected_version,
                    "created_at": now,
                    "updated_at": now,
                }
                conn.execute(video_interaction_turns.insert().values(**values))
                return TurnClaim(True, False, lease_token, values)
        except IntegrityError:
            with self.engine.begin() as conn:
                existing = self._turn_for_action(conn, task_id, actor_id, action_id)
                if existing and existing["operation"] == operation and existing["client_action_scope"] == scope:
                    return TurnClaim(False, True, "", existing)
            raise

    def get_turn(self, *, task_id: int, actor_id: str, turn_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            return self._scoped_turn(conn, _uuid(turn_id, field="turn_id"), task_id, _text(actor_id))

    def public_turn(self, *, task_id: int, actor_id: str, turn_id: str) -> dict[str, Any]:
        return public_turn(self.get_turn(task_id=task_id, actor_id=actor_id, turn_id=turn_id))

    def list_thread(self, *, task_id: int, actor_id: str, thread_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            thread = self._scoped_thread(conn, _uuid(thread_id, field="thread_id"), task_id, _text(actor_id))
            rows = conn.execute(
                select(video_interaction_turns)
                .where(video_interaction_turns.c.thread_id == thread["thread_id"])
                .order_by(video_interaction_turns.c.created_at, video_interaction_turns.c.turn_id)
            ).all()
        return {
            "video_thread_id": thread["thread_id"],
            "head_turn_id": thread.get("head_turn_id"),
            "status": thread.get("status"),
            "turns": [public_turn(self._mapping(row)) for row in rows],
        }

    def current_thread(self, *, task_id: int, actor_id: str, chat_session_id: str = "") -> dict[str, Any] | None:
        criteria = [
            video_interaction_threads.c.task_id == task_id,
            video_interaction_threads.c.actor_id == _text(actor_id),
            video_interaction_threads.c.status != "deleted",
        ]
        if _text(chat_session_id):
            criteria.append(video_interaction_threads.c.chat_session_id == _text(chat_session_id))
        with self.engine.begin() as conn:
            row = conn.execute(
                select(video_interaction_threads)
                .where(*criteria)
                .order_by(video_interaction_threads.c.updated_at.desc())
                .limit(1)
            ).first()
        if not row:
            return None
        return self.list_thread(task_id=task_id, actor_id=actor_id, thread_id=self._mapping(row)["thread_id"])

    def mark_provider_request_sent(
        self,
        turn_id: str,
        *,
        interaction_id: str,
        provider_state_expires_at: int | None = None,
        provider_expiry_source: str = "unknown",
    ) -> None:
        interaction_id = _text(interaction_id)
        if not interaction_id:
            raise VideoInteractionError("gemini_omni_video_output_missing", "Provider interaction id is missing", status_code=502)
        with self.engine.begin() as conn:
            result = conn.execute(
                update(video_interaction_turns)
                .where(video_interaction_turns.c.turn_id == turn_id, video_interaction_turns.c.status == "pending")
                .values(
                    interaction_id=interaction_id,
                    provider_request_status="accepted",
                    provider_state_status="available",
                    provider_state_expires_at=provider_state_expires_at,
                    provider_expiry_source=provider_expiry_source if provider_expiry_source in {"provider", "configured_estimate", "unknown"} else "unknown",
                    updated_at=self.now_ms(),
                )
            )
            if result.rowcount != 1:
                raise VideoInteractionError("gemini_omni_previous_interaction_invalid", "Pending turn no longer exists", status_code=409)

    def mark_provider_result_unknown(self, turn_id: str) -> None:
        now = self.now_ms()
        with self.engine.begin() as conn:
            turn = self._mapping(conn.execute(select(video_interaction_turns).where(video_interaction_turns.c.turn_id == turn_id).with_for_update()).first())
            if not turn:
                return
            conn.execute(
                update(video_interaction_turns)
                .where(video_interaction_turns.c.turn_id == turn_id)
                .values(provider_request_status="provider_result_unknown", provider_state_status="unknown", updated_at=now)
            )
            conn.execute(
                update(video_interaction_threads)
                .where(video_interaction_threads.c.thread_id == turn["thread_id"])
                .values(lease_token=None, lease_expires_at=None, updated_at=now)
            )

    def set_planned_output(self, turn_id: str, output_path: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(video_interaction_turns)
                .where(video_interaction_turns.c.turn_id == turn_id, video_interaction_turns.c.status == "pending")
                .values(output_path=_text(output_path), updated_at=self.now_ms())
            )

    def release_lease(self, turn_id: str, *, lease_token: str = "") -> None:
        """Leave a known provider request pending so a recovery worker can resume it."""

        now = self.now_ms()
        with self.engine.begin() as conn:
            turn = self._mapping(conn.execute(select(video_interaction_turns).where(video_interaction_turns.c.turn_id == turn_id)).first())
            if not turn or turn.get("status") != "pending":
                return
            criteria = [video_interaction_threads.c.thread_id == turn["thread_id"]]
            if lease_token:
                criteria.append(video_interaction_threads.c.lease_token == lease_token)
            conn.execute(
                update(video_interaction_threads)
                .where(*criteria)
                .values(lease_token=None, lease_expires_at=None, updated_at=now)
            )

    def internal_thread(self, *, task_id: int, actor_id: str, thread_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            return self._scoped_thread(conn, _uuid(thread_id, field="thread_id"), task_id, _text(actor_id))

    def renew_lease(self, turn_id: str, lease_token: str) -> bool:
        now = self.now_ms()
        with self.engine.begin() as conn:
            turn = self._mapping(conn.execute(select(video_interaction_turns).where(video_interaction_turns.c.turn_id == turn_id)).first())
            if not turn or turn.get("status") != "pending":
                return False
            result = conn.execute(
                update(video_interaction_threads)
                .where(
                    video_interaction_threads.c.thread_id == turn["thread_id"],
                    video_interaction_threads.c.lease_token == lease_token,
                )
                .values(lease_expires_at=self._lease_expiry(now), updated_at=now)
            )
            return result.rowcount == 1

    def complete_turn(
        self,
        turn_id: str,
        *,
        lease_token: str,
        output_asset_id: str,
        output_path: str,
        local_usage_id: str = "",
    ) -> dict[str, Any]:
        now = self.now_ms()
        with self.engine.begin() as conn:
            turn = self._mapping(conn.execute(select(video_interaction_turns).where(video_interaction_turns.c.turn_id == turn_id).with_for_update()).first())
            if not turn:
                raise VideoInteractionError("gemini_omni_previous_interaction_invalid", "Turn not found", status_code=404)
            thread = self._mapping(conn.execute(select(video_interaction_threads).where(video_interaction_threads.c.thread_id == turn["thread_id"]).with_for_update()).first())
            if turn["status"] == "completed":
                return turn
            if turn["status"] != "pending" or thread.get("lease_token") != lease_token:
                raise VideoInteractionError("video_stateful_edit_in_progress", "The stateful edit lease is no longer owned", status_code=409)
            if int(thread.get("row_version") or 0) != int(turn.get("expected_row_version") or 0) or _text(thread.get("head_turn_id")) != _text(turn.get("expected_head_turn_id")):
                raise VideoInteractionError("video_stateful_head_conflict", "Video thread head changed while the edit was running", status_code=409)
            conn.execute(
                update(video_interaction_turns)
                .where(video_interaction_turns.c.turn_id == turn_id)
                .values(
                    output_asset_id=_text(output_asset_id),
                    output_path=_text(output_path),
                    local_usage_id=_text(local_usage_id) or None,
                    status="completed",
                    provider_request_status="completed",
                    provider_state_status="available",
                    updated_at=now,
                )
            )
            conn.execute(
                update(video_interaction_threads)
                .where(video_interaction_threads.c.thread_id == turn["thread_id"])
                .values(
                    head_turn_id=turn_id,
                    lease_token=None,
                    lease_expires_at=None,
                    row_version=int(thread.get("row_version") or 0) + 1,
                    updated_at=now,
                )
            )
            return {**turn, "output_asset_id": _text(output_asset_id), "output_path": _text(output_path), "local_usage_id": _text(local_usage_id) or None, "status": "completed", "provider_request_status": "completed", "provider_state_status": "available", "updated_at": now}

    def fail_turn(self, turn_id: str, *, lease_token: str = "", cancelled: bool = False) -> None:
        now = self.now_ms()
        with self.engine.begin() as conn:
            turn = self._mapping(conn.execute(select(video_interaction_turns).where(video_interaction_turns.c.turn_id == turn_id).with_for_update()).first())
            if not turn or turn.get("status") in TERMINAL_TURN_STATUSES:
                return
            conn.execute(
                update(video_interaction_turns)
                .where(video_interaction_turns.c.turn_id == turn_id)
                .values(status="cancelled" if cancelled else "failed", provider_request_status="cancelled" if cancelled else "failed", updated_at=now)
            )
            lease_filter = [video_interaction_threads.c.thread_id == turn["thread_id"]]
            if lease_token:
                lease_filter.append(video_interaction_threads.c.lease_token == lease_token)
            conn.execute(
                update(video_interaction_threads)
                .where(*lease_filter)
                .values(lease_token=None, lease_expires_at=None, updated_at=now)
            )

    def mark_provider_expired(self, turn_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(video_interaction_turns)
                .where(video_interaction_turns.c.turn_id == turn_id)
                .values(provider_state_status="expired", updated_at=self.now_ms())
            )

    def claim_recovery(self, turn_id: str) -> TurnClaim | None:
        now = self.now_ms()
        token = str(uuid.uuid4())
        with self.engine.begin() as conn:
            turn = self._mapping(conn.execute(select(video_interaction_turns).where(video_interaction_turns.c.turn_id == turn_id).with_for_update()).first())
            if not turn or turn.get("status") != "pending":
                return None
            if turn.get("provider_request_status") == "provider_result_unknown":
                return None
            thread = self._mapping(conn.execute(select(video_interaction_threads).where(video_interaction_threads.c.thread_id == turn["thread_id"]).with_for_update()).first())
            if _text(thread.get("lease_token")) and int(thread.get("lease_expires_at") or 0) > now:
                return None
            if not turn.get("interaction_id") and turn.get("provider_request_status") not in {"not_sent"}:
                conn.execute(
                    update(video_interaction_turns)
                    .where(video_interaction_turns.c.turn_id == turn_id)
                    .values(provider_request_status="provider_result_unknown", provider_state_status="unknown", updated_at=now)
                )
                return None
            conn.execute(
                update(video_interaction_threads)
                .where(video_interaction_threads.c.thread_id == turn["thread_id"])
                .values(lease_token=token, lease_expires_at=self._lease_expiry(now), updated_at=now)
            )
            return TurnClaim(False, False, token, turn)

    def recoverable_turn_ids(self, *, limit: int = 100) -> list[str]:
        now = self.now_ms()
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(video_interaction_turns.c.turn_id)
                .join(video_interaction_threads, video_interaction_threads.c.thread_id == video_interaction_turns.c.thread_id)
                .where(
                    video_interaction_turns.c.status == "pending",
                    video_interaction_turns.c.provider_request_status != "provider_result_unknown",
                    or_(
                        video_interaction_threads.c.lease_token.is_(None),
                        video_interaction_threads.c.lease_expires_at.is_(None),
                        video_interaction_threads.c.lease_expires_at <= now,
                    ),
                )
                .order_by(video_interaction_turns.c.updated_at)
                .limit(max(1, min(limit, 500)))
            ).all()
        return [str(row[0]) for row in rows]

    def begin_cloud_delete(self, *, task_id: int, actor_id: str, thread_id: str) -> list[tuple[str, str]]:
        now = self.now_ms()
        with self.engine.begin() as conn:
            thread = self._scoped_thread(conn, _uuid(thread_id, field="thread_id"), task_id, _text(actor_id))
            rows = conn.execute(
                select(video_interaction_turns.c.turn_id, video_interaction_turns.c.interaction_id)
                .where(
                    video_interaction_turns.c.thread_id == thread["thread_id"],
                    video_interaction_turns.c.interaction_id.is_not(None),
                    video_interaction_turns.c.delete_status != "deleted",
                )
            ).all()
            no_provider_state = [
                video_interaction_turns.c.thread_id == thread["thread_id"],
                video_interaction_turns.c.interaction_id.is_(None),
                video_interaction_turns.c.delete_status != "deleted",
            ]
            # An explicit cloud-context clear is also the user's way to
            # abandon an indeterminate POST that never returned a provider
            # identifier. There is no remote state we can address, so close
            # the local pending turn and release its concurrency slot without
            # ever retrying the paid POST.
            conn.execute(
                update(video_interaction_turns)
                .where(*no_provider_state, video_interaction_turns.c.status == "pending")
                .values(status="failed", updated_at=now)
            )
            conn.execute(
                update(video_interaction_turns)
                .where(*no_provider_state)
                .values(
                    provider_state_status="deleted",
                    delete_status="deleted",
                    delete_attempts=video_interaction_turns.c.delete_attempts + 1,
                    delete_error=None,
                    updated_at=now,
                )
            )
            conn.execute(
                update(video_interaction_threads)
                .where(video_interaction_threads.c.thread_id == thread["thread_id"])
                .values(
                    status="deleting" if rows else "deleted",
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            conn.execute(
                update(video_interaction_turns)
                .where(video_interaction_turns.c.thread_id == thread["thread_id"], video_interaction_turns.c.interaction_id.is_not(None))
                .values(delete_status="pending", delete_error=None, updated_at=now)
            )
        return [(str(row[0]), str(row[1])) for row in rows]

    def finish_cloud_delete(self, turn_id: str, *, error: str = "") -> None:
        now = self.now_ms()
        with self.engine.begin() as conn:
            turn = self._mapping(conn.execute(select(video_interaction_turns).where(video_interaction_turns.c.turn_id == turn_id).with_for_update()).first())
            if not turn:
                return
            conn.execute(
                update(video_interaction_turns)
                .where(video_interaction_turns.c.turn_id == turn_id)
                .values(
                    delete_status="failed" if error else "deleted",
                    delete_attempts=int(turn.get("delete_attempts") or 0) + 1,
                    delete_error=_text(error)[:1000] or None,
                    provider_state_status="deleted" if not error else turn.get("provider_state_status"),
                    updated_at=now,
                )
            )
            remaining = conn.execute(
                select(video_interaction_turns.c.turn_id).where(
                    video_interaction_turns.c.thread_id == turn["thread_id"],
                    video_interaction_turns.c.interaction_id.is_not(None),
                    video_interaction_turns.c.turn_id != turn_id,
                    video_interaction_turns.c.delete_status != "deleted",
                ).limit(1)
            ).first()
            if not remaining and not error:
                conn.execute(
                    update(video_interaction_threads)
                    .where(video_interaction_threads.c.thread_id == turn["thread_id"])
                    .values(status="deleted", lease_token=None, lease_expires_at=None, updated_at=now)
                )

    def cloud_deletion_rows(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(video_interaction_turns).where(
                    video_interaction_turns.c.interaction_id.is_not(None),
                    video_interaction_turns.c.delete_status.in_(["pending", "failed"]),
                )
                .order_by(video_interaction_turns.c.updated_at)
                .limit(max(1, min(limit, 500)))
            ).all()
        return [self._mapping(row) for row in rows]
