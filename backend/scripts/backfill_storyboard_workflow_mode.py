from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill workflow_mode=storyboard for legacy OC-StoryBoard tasks")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--write", action="store_true", help="Apply the backfill. Defaults to dry-run.")
    return parser.parse_args()


def find_storyboard_candidates(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT t.id AS task_id, t.session_id, t.workflow_mode, s.workspace_dir
            FROM oc_rebuild_tasks t
            JOIN sessions s ON s.id = t.session_id
            WHERE t.workflow_mode IS NULL OR t.workflow_mode = ''
            ORDER BY t.id
            """
        )
    ).mappings().fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        workspace = Path(str(row["workspace_dir"] or ""))
        if (workspace / "storyboard_meta.json").exists():
            candidates.append(dict(row))
    return candidates


def run_backfill(engine: Engine, *, write: bool = False) -> dict[str, Any]:
    with engine.begin() as conn:
        candidates = find_storyboard_candidates(conn)
        updated = 0
        if write and candidates:
            for row in candidates:
                result = conn.execute(
                    text("UPDATE oc_rebuild_tasks SET workflow_mode = 'storyboard' WHERE id = :task_id AND (workflow_mode IS NULL OR workflow_mode = '')"),
                    {"task_id": int(row["task_id"])},
                )
                updated += int(result.rowcount or 0)
        return {"candidate_count": len(candidates), "updated_count": updated, "candidates": candidates, "dry_run": not write}


def main() -> None:
    args = parse_args()
    engine = create_engine(args.database_url, future=True)
    try:
        result = run_backfill(engine, write=bool(args.write))
        print(f"candidate_count={result['candidate_count']}")
        for row in result["candidates"]:
            print(f"task_id={row['task_id']} session_id={row['session_id']} workspace={row['workspace_dir']}")
        if args.write:
            print(f"updated_count={result['updated_count']}")
        else:
            print("dry_run=yes")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
