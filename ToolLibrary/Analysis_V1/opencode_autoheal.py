from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable


RequestFunc = Callable[[dict[str, str], str, str, dict[str, Any] | None, str, int], Any]
logger = logging.getLogger(__name__)


def is_opencode_session_not_found(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "session not found" in text or ("opencode http 404" in text and "session" in text)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _variables_path(workspace: Path) -> Path:
    return workspace / "SessionContext" / "Variables.json"


def _normalize_postgres_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _update_opencrew_session(database_url: str, opencrew_session_id: int, opencode_session_id: str) -> None:
    normalized_url = _normalize_postgres_url(database_url)
    try:
        import psycopg  # type: ignore
    except ImportError as psycopg_import_error:
        psycopg = None  # type: ignore[assignment]
        first_import_error = psycopg_import_error
    else:
        first_import_error = None

    if psycopg is not None:
        with psycopg.connect(normalized_url, connect_timeout=8) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sessions SET opencode_session_id = %s, updated_at = %s WHERE id = %s",
                    (opencode_session_id, int(time.time() * 1000), opencrew_session_id),
                )
            conn.commit()
        return

    try:
        import psycopg2  # type: ignore
    except ImportError as psycopg2_import_error:
        raise RuntimeError("Neither psycopg nor psycopg2 is available for OpenCode session DB repair") from (first_import_error or psycopg2_import_error)

    conn = psycopg2.connect(normalized_url, connect_timeout=8)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET opencode_session_id = %s, updated_at = %s WHERE id = %s",
                (opencode_session_id, int(time.time() * 1000), opencrew_session_id),
            )
        conn.commit()
    finally:
        conn.close()


def recover_opencode_session_id(
    *,
    runtime: dict[str, str],
    variables: dict[str, Any],
    workspace: Path,
    request_func: RequestFunc,
    database_url: str,
    title: str,
) -> str:
    directory = str(workspace)
    session = request_func(runtime, "POST", "/session", {"title": title}, directory, 30)
    session_id = str((session or {}).get("id") or "").strip()
    if not session_id:
        raise RuntimeError("OpenCode session repair failed: create session returned no id")

    variables["opencode_session_id"] = session_id
    variables["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    variables_file = _variables_path(workspace)
    if variables_file.exists():
        _write_json(variables_file, variables)

    opencrew_session_id = int(variables.get("opencrew_session_id") or variables.get("session_id") or 0)
    if opencrew_session_id and database_url:
        try:
            _update_opencrew_session(database_url, opencrew_session_id, session_id)
        except Exception as exc:
            logger.warning(
                "OpenCode session was recreated but DB session row update failed",
                extra={"opencrew_session_id": opencrew_session_id, "opencode_session_id": session_id},
                exc_info=True,
            )

    return session_id
