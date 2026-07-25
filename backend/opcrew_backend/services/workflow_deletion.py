from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


class WorkflowDeletionService:
    def __init__(self, engine: Engine, sessions_root: Path) -> None:
        self.engine = engine
        self.sessions_root = sessions_root

    def _has_table(self, conn: Any, table_name: str) -> bool:
        return inspect(conn).has_table(table_name)

    def delete_session_db_first(self, session_row: dict[str, Any]) -> dict[str, Any]:
        session_id = int(session_row["id"])
        with self.engine.begin() as conn:
            self.delete_session_db_first_in_tx(conn, session_row)
        return {"ok": True, "deleted_session_id": session_id}

    def delete_session_db_first_in_tx(self, conn: Any, session_row: dict[str, Any]) -> None:
        session_id = int(session_row["id"])
        if self._has_table(conn, "oc_rebuild_tasks"):
            rebuild_task_ids = [int(row[0]) for row in conn.execute(text("SELECT id FROM oc_rebuild_tasks WHERE session_id = :session_id"), {"session_id": session_id})]
            for task_id in rebuild_task_ids:
                conn.execute(text("DELETE FROM oc_rebuild_attempts WHERE task_id = :task_id"), {"task_id": task_id})
                conn.execute(text("DELETE FROM oc_rebuild_prompt_versions WHERE task_id = :task_id"), {"task_id": task_id})
                conn.execute(text("DELETE FROM workflow_plans WHERE task_id = :task_id AND workflow_id LIKE 'oc_rebuild%'"), {"task_id": task_id})
                conn.execute(text("DELETE FROM oc_rebuild_tasks WHERE id = :task_id"), {"task_id": task_id})
        if self._has_table(conn, "openclip_tasks"):
            task_ids = [int(row[0]) for row in conn.execute(text("SELECT id FROM openclip_tasks WHERE session_id = :session_id"), {"session_id": session_id})]
            for task_id in task_ids:
                if self._has_table(conn, "talking_head_task_configs"):
                    conn.execute(text("DELETE FROM talking_head_task_configs WHERE task_id = :task_id"), {"task_id": task_id})
                if self._has_table(conn, "openclip_param_versions"):
                    conn.execute(text("DELETE FROM openclip_param_versions WHERE task_id = :task_id"), {"task_id": task_id})
                conn.execute(text("DELETE FROM openclip_attempts WHERE task_id = :task_id"), {"task_id": task_id})
                conn.execute(text("DELETE FROM openclip_skill_versions WHERE task_id = :task_id"), {"task_id": task_id})
                conn.execute(text("DELETE FROM openclip_prompt_versions WHERE task_id = :task_id"), {"task_id": task_id})
                conn.execute(text("DELETE FROM openclip_tasks WHERE id = :task_id"), {"task_id": task_id})
        if self._has_table(conn, "openflow_analysis_runs"):
            conn.execute(text("DELETE FROM openflow_analysis_runs WHERE session_id = :session_id"), {"session_id": session_id})
        conn.execute(text("DELETE FROM workflow_plans WHERE session_id = :session_id"), {"session_id": session_id})
        conn.execute(text("DELETE FROM session_shares WHERE session_id = :session_id"), {"session_id": session_id})
        conn.execute(text("DELETE FROM session_files WHERE session_id = :session_id"), {"session_id": session_id})
        conn.execute(text("DELETE FROM session_events WHERE session_id = :session_id"), {"session_id": session_id})
        conn.execute(text("DELETE FROM sessions WHERE id = :session_id"), {"session_id": session_id})

    def cleanup_workspace(self, session_row: dict[str, Any]) -> None:
        raw_workspace = str(session_row.get("workspace_dir") or "").strip()
        if not raw_workspace:
            raise ValueError("workspace_dir is empty")

        workspace = Path(raw_workspace).expanduser()
        if not workspace.is_absolute():
            raise ValueError(f"workspace_dir must be absolute: {raw_workspace}")
        if workspace.name != "workspace":
            raise ValueError(f"workspace_dir must point at a workspace directory: {raw_workspace}")

        sessions_root = self.sessions_root.resolve()
        session_dir = workspace.parent.resolve()
        if session_dir.parent != sessions_root:
            raise ValueError(f"workspace_dir is outside sessions root: {raw_workspace}")
        session_id = str(session_row.get("id") or "").strip()
        if not session_id or session_dir.name != session_id:
            raise ValueError(f"workspace_dir does not match session id: {raw_workspace}")

        workspace_path = session_dir / "workspace"
        if workspace_path.exists() and workspace_path.resolve() != workspace_path:
            raise ValueError(f"workspace_dir must not be a symlink: {raw_workspace}")

        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=False)
