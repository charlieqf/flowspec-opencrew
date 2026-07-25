from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db.schema import openflow_analysis_runs
from .base import Repository, row_to_dict


class OpenFlowRepository(Repository):
    def get_by_session(self, session_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(openflow_analysis_runs).where(openflow_analysis_runs.c.session_id == session_id)).first())

    def create_for_session(self, session_id: int, created_at: int, **fields: Any) -> int:
        payload = {
            "session_id": session_id,
            "reference_video_path": "",
            "industry": "",
            "persona": "",
            "target_audience": "",
            "product_info": "",
            "constraints": "",
            "analysis_goal": "",
            "video_formula": "",
            "simple_prompt": "",
            "final_prompt": "",
            "prompt_model_provider": "",
            "prompt_model_id": "",
            "generated_skill_content": "",
            "skill_model_provider": "",
            "skill_model_id": "",
            "skill_version_name": "",
            "skill_version_notes": "",
            "skill_versions_json": "[]",
            "version_name": "",
            "version_notes": "",
            "versions_json": "[]",
            "created_at": created_at,
            "updated_at": created_at,
        }
        payload.update(fields)
        with self.engine.begin() as conn:
            result = conn.execute(openflow_analysis_runs.insert().values(**payload).returning(openflow_analysis_runs.c.id))
            return int(result.scalar_one())

    def upsert_for_session(self, session_id: int, updated_at: int, **fields: Any) -> None:
        payload = {
            "session_id": session_id,
            "reference_video_path": "",
            "industry": "",
            "persona": "",
            "target_audience": "",
            "product_info": "",
            "constraints": "",
            "analysis_goal": "",
            "video_formula": "",
            "simple_prompt": "",
            "final_prompt": "",
            "prompt_model_provider": "",
            "prompt_model_id": "",
            "generated_skill_content": "",
            "skill_model_provider": "",
            "skill_model_id": "",
            "skill_version_name": "",
            "skill_version_notes": "",
            "skill_versions_json": "[]",
            "version_name": "",
            "version_notes": "",
            "versions_json": "[]",
            "created_at": updated_at,
            "updated_at": updated_at,
        }
        payload.update(fields)
        with self.engine.begin() as conn:
            conn.execute(
                pg_insert(openflow_analysis_runs)
                .values(**payload)
                .on_conflict_do_update(
                    index_elements=[openflow_analysis_runs.c.session_id],
                    set_={**fields, "updated_at": updated_at},
                )
            )

    def update_for_session(self, session_id: int, **fields: Any) -> None:
        if not fields:
            return
        with self.engine.begin() as conn:
            conn.execute(update(openflow_analysis_runs).where(openflow_analysis_runs.c.session_id == session_id).values(**fields))

    def delete_for_session(self, session_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(openflow_analysis_runs).where(openflow_analysis_runs.c.session_id == session_id))
