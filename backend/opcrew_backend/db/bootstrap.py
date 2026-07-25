from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from .migrations import run_migrations
from .schema import metadata, npc_runtime, opencode_runtime, publish_runtime, tunnel_runtime, wecom_config, wecom_runtime


def ensure_openflow_analysis_run_columns(engine: Engine) -> None:
    required = {
        "prompt_model_provider": "TEXT",
        "prompt_model_id": "TEXT",
        "generated_skill_content": "TEXT",
        "skill_model_provider": "TEXT",
        "skill_model_id": "TEXT",
        "skill_version_name": "TEXT",
        "skill_version_notes": "TEXT",
        "skill_versions_json": "TEXT",
    }
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'openflow_analysis_runs'"
            )
        )
        existing = {str(row[0]) for row in rows}
        for name, column_type in required.items():
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE openflow_analysis_runs ADD COLUMN {name} {column_type}"))


def ensure_openclip_columns(engine: Engine) -> None:
    task_required = {
        "workflow_mode": "TEXT",
        "final_prompt": "TEXT",
        "rewrite_simple_prompt": "TEXT",
        "rewrite_final_prompt": "TEXT",
        "storyboard_simple_prompt": "TEXT",
        "storyboard_final_prompt": "TEXT",
        "storyboard_quick_config_json": "TEXT",
        "prompt_model_provider": "TEXT",
        "prompt_model_id": "TEXT",
        "generated_skill_content": "TEXT",
        "skill_version_name": "TEXT",
        "skill_version_notes": "TEXT",
    }
    prompt_version_required = {
        "reference_video_path": "TEXT",
        "industry": "TEXT",
        "persona": "TEXT",
        "target_audience": "TEXT",
        "product_info": "TEXT",
        "constraints": "TEXT",
        "analysis_goal": "TEXT",
        "video_formula": "TEXT",
        "simple_prompt": "TEXT",
        "rewrite_simple_prompt": "TEXT",
        "rewrite_final_prompt": "TEXT",
        "storyboard_simple_prompt": "TEXT",
        "storyboard_final_prompt": "TEXT",
        "storyboard_quick_config_json": "TEXT",
    }
    with engine.begin() as conn:
        task_rows = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'openclip_tasks'"))
        task_existing = {str(row[0]) for row in task_rows}
        for name, column_type in task_required.items():
            if name in task_existing:
                continue
            conn.execute(text(f"ALTER TABLE openclip_tasks ADD COLUMN {name} {column_type}"))

        prompt_rows = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'openclip_prompt_versions'"))
        prompt_existing = {str(row[0]) for row in prompt_rows}
        for name, column_type in prompt_version_required.items():
            if name in prompt_existing:
                continue
            conn.execute(text(f"ALTER TABLE openclip_prompt_versions ADD COLUMN {name} {column_type}"))


def ensure_oc_rebuild_columns(engine: Engine) -> None:
    task_required = {
        "analysis_task_id": "INTEGER",
        "source_package_path": "TEXT",
        "source_scheme": "TEXT",
        "target_topic": "TEXT",
        "target_platform": "TEXT",
        "aspect_ratio": "TEXT",
        "target_count": "INTEGER",
        "target_audience": "TEXT",
        "product_info": "TEXT",
        "rebuild_goal": "TEXT",
        "preserve_strategy_json": "TEXT",
        "replace_strategy_json": "TEXT",
        "visual_style": "TEXT",
        "subtitle_style": "TEXT",
        "title_style": "TEXT",
        "voice_style": "TEXT",
        "batch_variables": "TEXT",
        "constraints": "TEXT",
        "simple_prompt": "TEXT",
        "final_prompt": "TEXT",
        "current_version_id": "INTEGER",
        "latest_attempt_id": "INTEGER",
        "prompt_model_provider": "TEXT",
        "prompt_model_id": "TEXT",
        "run_model_provider": "TEXT",
        "run_model_id": "TEXT",
    }
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'oc_rebuild_tasks'"))
        existing = {str(row[0]) for row in rows}
        for name, column_type in task_required.items():
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE oc_rebuild_tasks ADD COLUMN {name} {column_type}"))


def build_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        connect_args={"client_encoding": "utf8"},
    )


def initialize_database(engine: Engine, data_dir: Path) -> None:
    metadata.create_all(engine)
    run_migrations(engine)
    ensure_openflow_analysis_run_columns(engine)
    ensure_openclip_columns(engine)
    ensure_oc_rebuild_columns(engine)
    managed_npc_path = str(data_dir / "bin" / "npc")

    with engine.begin() as conn:
        conn.execute(
            pg_insert(opencode_runtime)
            .values(id=1, status="idle")
            .on_conflict_do_nothing(index_elements=[opencode_runtime.c.id])
        )
        conn.execute(
            pg_insert(tunnel_runtime)
            .values(id=1, provider="cloudflared", status="idle")
            .on_conflict_do_nothing(index_elements=[tunnel_runtime.c.id])
        )
        conn.execute(
            pg_insert(npc_runtime)
            .values(
                id=1,
                environment_status="idle",
                install_status="idle",
                verify_status="idle",
                managed_path=managed_npc_path,
            )
            .on_conflict_do_nothing(index_elements=[npc_runtime.c.id])
        )
        conn.execute(
            pg_insert(publish_runtime)
            .values(id=1, status="idle", message="Waiting for URL input")
            .on_conflict_do_nothing(index_elements=[publish_runtime.c.id])
        )
        conn.execute(
            pg_insert(wecom_runtime)
            .values(id=1, status="unconfigured")
            .on_conflict_do_nothing(index_elements=[wecom_runtime.c.id])
        )
        conn.execute(
            pg_insert(wecom_config)
            .values(id=1, enabled=0)
            .on_conflict_do_nothing(index_elements=[wecom_config.c.id])
        )


def database_ready(engine: Engine) -> bool:
    with engine.connect() as conn:
        return bool(conn.execute(select(1)).scalar_one())
