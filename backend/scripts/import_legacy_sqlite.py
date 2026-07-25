from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine


def build_engine(database_url: str):
    return create_engine(
        database_url,
        future=True,
        connect_args={"client_encoding": "utf8"},
    )


TABLES = [
    "app_settings",
    "opencode_runtime",
    "tunnel_runtime",
    "npc_runtime",
    "publish_runtime",
    "npc_skills",
    "publish_skills",
    "task_runs",
    "task_logs",
    "wecom_config",
    "wecom_runtime",
    "message_logs",
    "verification_runs",
    "event_logs",
    "sessions",
    "session_events",
    "session_files",
    "session_shares",
]

SEQUENCE_TABLES = [
    "task_runs",
    "task_logs",
    "message_logs",
    "verification_runs",
    "event_logs",
    "sessions",
    "session_events",
    "session_files",
    "session_shares",
]


def import_table(sqlite_conn: sqlite3.Connection, engine, table_name: str) -> None:
    rows = sqlite_conn.execute(f"SELECT * FROM {table_name}").fetchall()
    if not rows:
        return

    metadata = MetaData()
    table = Table(table_name, metadata, autoload_with=engine)
    payloads = [dict(row) for row in rows]

    with engine.begin() as conn:
        conn.execute(table.insert(), payloads)


def reset_target_tables(engine) -> None:
    joined = ", ".join(TABLES)
    with engine.begin() as conn:
        conn.exec_driver_sql(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE")


def reset_sequences(engine) -> None:
    with engine.begin() as conn:
        for table_name in SEQUENCE_TABLES:
            conn.exec_driver_sql(
                f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), COALESCE((SELECT MAX(id) FROM {table_name}), 1), true)"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import OpenCrew legacy SQLite data into PostgreSQL")
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("database_url")
    args = parser.parse_args()

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    engine = build_engine(args.database_url)
    try:
        reset_target_tables(engine)
        for table_name in TABLES:
            import_table(sqlite_conn, engine, table_name)
            print(f"Imported {table_name}")
        reset_sequences(engine)
    finally:
        sqlite_conn.close()
        engine.dispose()


if __name__ == "__main__":
    main()
