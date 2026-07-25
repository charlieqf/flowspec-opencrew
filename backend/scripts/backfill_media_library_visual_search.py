from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_fragment_index,
    session_files,
    sessions,
)
from opcrew_backend.media_library_analysis.visual_semantic_contracts import (  # noqa: E402
    RESULT_SCHEMA_VERSION,
    SAMPLING_STRATEGY,
    validate_published_visual_semantic_result,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibraryFragmentPublisher,
)


DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
)


def sqlalchemy_database_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate and publish eligible current four-frame visual "
            "semantic results. Defaults to dry-run and never calls a model."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--asset-id", default="")
    return parser.parse_args()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _candidates(engine: Engine, *, asset_id: str) -> list[dict[str, Any]]:
    statement = (
        select(
            media_library_analysis_runs,
            media_library_assets.c.session_id,
            media_library_assets.c.content_sha256,
            media_library_assets.c.duration_ms,
            sessions.c.workspace_dir,
        )
        .select_from(
            media_library_analysis_runs.join(
                media_library_assets,
                media_library_assets.c.asset_id
                == media_library_analysis_runs.c.asset_id,
            ).join(
                sessions,
                sessions.c.id == media_library_assets.c.session_id,
            )
        )
        .where(
            media_library_analysis_runs.c.scheme == "visual_semantic",
            media_library_analysis_runs.c.status == "ready",
            media_library_analysis_runs.c.is_current.is_(True),
            media_library_analysis_runs.c.result_index_path.is_not(None),
            media_library_assets.c.upload_status == "ready",
            media_library_assets.c.archived.is_(False),
        )
        .order_by(
            media_library_analysis_runs.c.created_at,
            media_library_analysis_runs.c.analysis_run_id,
        )
    )
    if asset_id:
        statement = statement.where(
            media_library_analysis_runs.c.asset_id == asset_id
        )
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(statement).mappings().fetchall()
        ]


def _registered_result_path(
    engine: Engine,
    *,
    row: Mapping[str, Any],
) -> Path:
    relative = str(row.get("result_index_path") or "").replace("\\", "/")
    if (
        not relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ValueError("visual_semantic_result_path_invalid")
    workspace = Path(str(row.get("workspace_dir") or "")).resolve()
    unresolved = workspace / relative
    result_path = unresolved.resolve()
    if (
        unresolved.is_symlink()
        or not result_path.is_relative_to(workspace)
        or not result_path.is_file()
    ):
        raise ValueError("visual_semantic_result_path_invalid")
    with engine.connect() as conn:
        registered = conn.execute(
            select(session_files.c.id).where(
                session_files.c.session_id == int(row["session_id"]),
                session_files.c.path == relative,
                session_files.c.stale == 0,
            )
        ).first()
    if registered is None:
        raise ValueError("visual_semantic_result_not_registered")
    return result_path


def _read_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("visual_semantic_result_invalid")
    return payload


def _is_historical_single_frame(
    row: Mapping[str, Any], payload: Mapping[str, Any] | None = None
) -> bool:
    upstream = _json_object(row.get("upstream_refs_json"))
    values = {
        str(row.get("schema_version") or ""),
        str(upstream.get("sampling_strategy") or ""),
    }
    if payload is not None:
        values.update(
            {
                str(payload.get("schema_version") or ""),
                str(payload.get("sampling_strategy") or ""),
            }
        )
    return bool(
        "media_library_visual_semantic_v1" in values
        or "scene_midpoint_v1" in values
    )


def _fragments(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "fragment_id": item["fragment_id"],
            "start_ms": item["start_ms"],
            "end_ms": item["end_ms"],
            "dialogue_text": None,
            "title": None,
            "summary": item.get("visual_summary"),
            "keywords": item.get("keywords") or [],
            "visual_labels": [
                *(item.get("people") or []),
                *(item.get("objects") or []),
                *([item["scene"]] if item.get("scene") else []),
            ],
            "keyframe_ref": item.get("keyframe_refs") or [],
            "quality_status": (
                "review" if item.get("needs_review") else "ready"
            ),
            "confidence": item.get("confidence"),
        }
        for item in (payload.get("items") or [])
        if isinstance(item, dict)
    ]


def _active_index_matches(
    rows: list[Mapping[str, Any]],
    fragments: list[Mapping[str, Any]],
    *,
    result_hash: str,
) -> bool:
    if len(rows) != len(fragments):
        return False
    by_id = {str(row.get("fragment_id") or ""): row for row in rows}
    for fragment in fragments:
        row = by_id.get(str(fragment["fragment_id"]))
        if row is None:
            return False
        if (
            str(row.get("analysis_scheme") or "") != "visual_semantic"
            or str(row.get("result_hash") or "") != result_hash
            or int(row.get("start_ms") or 0) != int(fragment["start_ms"])
            or int(row.get("end_ms") or 0) != int(fragment["end_ms"])
            or row.get("keyframe_ref_json") != fragment["keyframe_ref"]
            or str(row.get("summary") or "")
            != str(fragment.get("summary") or "")
            or not bool(row.get("is_active"))
        ):
            return False
    return True


def run_backfill(
    engine: Engine,
    *,
    write: bool = False,
    asset_id: str = "",
    timestamp: int | None = None,
) -> dict[str, Any]:
    """Index-only reconcile; this function never creates a model session."""

    candidates = _candidates(engine, asset_id=asset_id)
    publisher = MediaLibraryFragmentPublisher(engine)
    report: dict[str, Any] = {
        "schema_version": "media_library_visual_search_backfill_v1",
        "dry_run": not write,
        "candidate_count": len(candidates),
        "publishable_count": 0,
        "published_count": 0,
        "already_published_count": 0,
        "reanalysis_required_count": 0,
        "failed_count": 0,
        "items": [],
    }
    for row in candidates:
        run_id = str(row["analysis_run_id"])
        item: dict[str, Any] = {
            "asset_id": str(row["asset_id"]),
            "analysis_run_id": run_id,
        }
        report["items"].append(item)
        try:
            if _is_historical_single_frame(row):
                item.update(
                    status="reanalysis_required",
                    reason="sampling_strategy_ineligible",
                )
                report["reanalysis_required_count"] += 1
                continue
            result_path = _registered_result_path(engine, row=row)
            payload = _read_payload(result_path)
            if _is_historical_single_frame(row, payload):
                item.update(
                    status="reanalysis_required",
                    reason="sampling_strategy_ineligible",
                )
                report["reanalysis_required_count"] += 1
                continue
            if (
                str(row.get("schema_version") or "")
                != RESULT_SCHEMA_VERSION
                or payload.get("sampling_strategy") != SAMPLING_STRATEGY
            ):
                raise ValueError("sampling_strategy_ineligible")
            validated = validate_published_visual_semantic_result(
                payload,
                asset_id=str(row["asset_id"]),
                source_version=str(row.get("content_sha256") or ""),
                analysis_run_id=run_id,
                expected_result_hash=str(row.get("result_hash") or ""),
                source_duration_ms=(
                    int(row["duration_ms"])
                    if row.get("duration_ms") is not None
                    else None
                ),
            )
            fragments = _fragments(validated)
            with engine.connect() as conn:
                active_rows = [
                    dict(active_row)
                    for active_row in conn.execute(
                        select(media_library_fragment_index).where(
                            media_library_fragment_index.c.analysis_run_id
                            == run_id,
                            media_library_fragment_index.c.is_active.is_(True),
                        )
                    ).mappings()
                ]
            item["fragment_count"] = len(validated["items"])
            if active_rows:
                if not _active_index_matches(
                    active_rows,
                    fragments,
                    result_hash=str(row["result_hash"]),
                ):
                    raise ValueError(
                        "visual_semantic_active_fragment_index_mismatch"
                    )
                item["status"] = "already_published"
                report["already_published_count"] += 1
                continue
            item["status"] = "would_publish"
            report["publishable_count"] += 1
            if not write:
                continue
            upstream = _json_object(row.get("upstream_refs_json"))
            structure_run_id = str(
                upstream.get("visual_structure_run_id")
                or validated.get("visual_structure_run_id")
                or ""
            )
            structure_hash = str(
                upstream.get("visual_structure_result_hash")
                or validated.get("visual_structure_result_hash")
                or ""
            )
            publisher.backfill_visual_semantic(
                asset_id=str(row["asset_id"]),
                analysis_run_id=run_id,
                result_hash=str(row["result_hash"]),
                fragments=fragments,
                timestamp=(
                    int(timestamp)
                    if timestamp is not None
                    else int(time.time() * 1000)
                ),
                result_index_path=str(row["result_index_path"]),
                visual_structure_run_id=structure_run_id,
                visual_structure_result_hash=structure_hash,
            )
            item["status"] = "published"
            report["published_count"] += 1
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc).strip() or exc.__class__.__name__
            report["failed_count"] += 1
    return report


def main() -> None:
    args = parse_args()
    engine = create_engine(
        sqlalchemy_database_url(args.database_url), future=True
    )
    try:
        report = run_backfill(
            engine,
            write=bool(args.write),
            asset_id=str(args.asset_id or "").strip(),
        )
    finally:
        engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
