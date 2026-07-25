from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
STREAM_BYTES = 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill immutable SHA-256 source identity for ready media-library assets. Defaults to dry-run."
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--write", action="store_true", help="Persist hashes. Defaults to dry-run.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most this many assets.")
    parser.add_argument("--asset-id", default="", help="Process one asset only.")
    return parser.parse_args()


def sqlalchemy_database_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            data = handle.read(STREAM_BYTES)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def _candidate_rows(engine: Engine, *, asset_id: str = "", limit: int = 0) -> list[dict[str, Any]]:
    clauses = [
        "asset.upload_status = 'ready'",
        "(asset.content_sha256 IS NULL OR TRIM(asset.content_sha256) = '')",
    ]
    params: dict[str, Any] = {}
    if asset_id:
        clauses.append("asset.asset_id = :asset_id")
        params["asset_id"] = asset_id
    limit_sql = ""
    if limit > 0:
        limit_sql = " LIMIT :limit"
        params["limit"] = limit
    query = text(
        f"""
SELECT
  asset.asset_id,
  asset.session_id,
  asset.source_video_path,
  asset.size_bytes,
  asset.updated_at,
  session.workspace_dir
FROM media_library_assets AS asset
JOIN sessions AS session ON session.id = asset.session_id
WHERE {' AND '.join(clauses)}
ORDER BY asset.created_at, asset.asset_id
{limit_sql}
"""
    )
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(query, params).mappings().fetchall()]


def run_backfill(
    engine: Engine,
    *,
    write: bool = False,
    limit: int = 0,
    asset_id: str = "",
) -> dict[str, Any]:
    candidates = _candidate_rows(engine, asset_id=asset_id, limit=limit)
    rows: list[dict[str, Any]] = []
    updated = 0
    failed = 0
    for candidate in candidates:
        row = {
            "asset_id": str(candidate["asset_id"]),
            "session_id": int(candidate["session_id"]),
            "status": "pending",
        }
        rows.append(row)
        try:
            workspace = Path(str(candidate["workspace_dir"])).resolve()
            relative = Path(str(candidate.get("source_video_path") or ""))
            source = (workspace / relative).resolve()
            if not relative.as_posix() or not source.is_relative_to(workspace):
                raise ValueError("source_path_outside_workspace")
            before = source.stat()
            declared_size = candidate.get("size_bytes")
            if declared_size is not None and int(declared_size) != int(before.st_size):
                raise ValueError("source_size_mismatch")
            digest = _sha256(source)
            after = source.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise ValueError("source_changed_while_hashing")
            row.update(
                {
                    "status": "would_update" if not write else "updated",
                    "content_sha256": digest,
                    "size_bytes": int(after.st_size),
                }
            )
            if write:
                hashed_at = int(time.time() * 1000)
                with engine.begin() as conn:
                    result = conn.execute(
                        text(
                            """
UPDATE media_library_assets
SET content_sha256 = :content_sha256,
    content_hashed_at = :content_hashed_at,
    updated_at = CASE WHEN updated_at > :content_hashed_at THEN updated_at ELSE :content_hashed_at END
WHERE asset_id = :asset_id
  AND upload_status = 'ready'
  AND (content_sha256 IS NULL OR TRIM(content_sha256) = '')
  AND size_bytes = :size_bytes
"""
                        ),
                        {
                            "content_sha256": digest,
                            "content_hashed_at": hashed_at,
                            "asset_id": candidate["asset_id"],
                            "size_bytes": int(after.st_size),
                        },
                    )
                    if result.rowcount != 1:
                        raise RuntimeError("source_record_changed")
                    conn.execute(
                        text(
                            """
INSERT INTO event_logs (
  level, category, message, payload, created_at
) VALUES (
  'info',
  'media_library_source_identity',
  'media_library.source_hash.completed',
  :payload,
  :created_at
)
"""
                        ),
                        {
                            "payload": json.dumps(
                                {
                                    "asset_id": str(
                                        candidate["asset_id"]
                                    ),
                                    "session_id": int(
                                        candidate["session_id"]
                                    ),
                                    "source_version": digest,
                                    "size_bytes": int(after.st_size),
                                    "origin": "backfill",
                                },
                                ensure_ascii=True,
                                separators=(",", ":"),
                            ),
                            "created_at": hashed_at,
                        },
                    )
                updated += 1
        except Exception as exc:
            failed += 1
            row["status"] = "failed"
            row["error"] = str(exc).strip() or exc.__class__.__name__
    return {
        "dry_run": not write,
        "candidate_count": len(candidates),
        "updated_count": updated,
        "failed_count": failed,
        "items": rows,
    }


def main() -> None:
    args = parse_args()
    engine = create_engine(sqlalchemy_database_url(args.database_url), future=True)
    try:
        report = run_backfill(
            engine,
            write=bool(args.write),
            limit=max(0, int(args.limit)),
            asset_id=str(args.asset_id or "").strip(),
        )
        print(
            f"dry_run={'yes' if report['dry_run'] else 'no'} "
            f"candidate_count={report['candidate_count']} "
            f"updated_count={report['updated_count']} "
            f"failed_count={report['failed_count']}"
        )
        for item in report["items"]:
            print(
                f"asset_id={item['asset_id']} session_id={item['session_id']} "
                f"status={item['status']} error={item.get('error', '')}"
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
