from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.media_library_analysis.contracts import (  # noqa: E402
    publish_dialogue_contract,
    publish_visual_structure_contract,
)
from opcrew_backend.media_library_analysis.run_repository import (  # noqa: E402
    AnalysisRunRepository,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibraryFragmentPublisher,
)


DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
)
SCHEMES = ("dialogue", "visual_structure")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and adopt trustworthy legacy media-library analysis output "
            "without rerunning ASR, VLM, LLM, or FFmpeg. Defaults to dry-run."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--asset-id", default="")
    parser.add_argument("--scheme", choices=SCHEMES)
    return parser.parse_args()


def sqlalchemy_database_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path.name}")
    return value


def _legacy_run_id(
    *, asset_id: str, scheme: str, source_version: str, tool_use_session_id: str
) -> str:
    digest = hashlib.sha256(
        "\0".join(
            (asset_id, scheme, source_version, tool_use_session_id)
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"mlar_{scheme}_legacy_{digest}"


def _candidate_rows(
    engine: Engine, *, asset_id: str = "", scheme: str | None = None
) -> list[dict[str, Any]]:
    clauses = ["asset.upload_status = 'ready'"]
    params: dict[str, Any] = {}
    if asset_id:
        clauses.append("asset.asset_id = :asset_id")
        params["asset_id"] = asset_id
    query = text(
        f"""
SELECT
  asset.asset_id,
  asset.session_id,
  asset.content_sha256,
  asset.duration_ms,
  session.workspace_dir,
  task.dialogue_status,
  task.dialogue_tool_use_session_id,
  task.visual_status,
  task.visual_tool_use_session_id
FROM media_library_assets AS asset
JOIN sessions AS session ON session.id = asset.session_id
JOIN media_library_tasks AS task ON task.asset_id = asset.asset_id
WHERE {' AND '.join(clauses)}
ORDER BY asset.created_at, asset.asset_id
"""
    )
    with engine.connect() as conn:
        assets = [
            dict(row)
            for row in conn.execute(query, params).mappings().fetchall()
        ]
    candidates: list[dict[str, Any]] = []
    for asset in assets:
        for candidate_scheme in SCHEMES:
            if scheme and candidate_scheme != scheme:
                continue
            legacy_prefix = (
                "dialogue"
                if candidate_scheme == "dialogue"
                else "visual"
            )
            tool_use_session_id = str(
                asset.get(f"{legacy_prefix}_tool_use_session_id") or ""
            )
            business_status = str(
                asset.get(f"{legacy_prefix}_status") or ""
            )
            if business_status == "ready" and tool_use_session_id:
                candidates.append(
                    {
                        **asset,
                        "scheme": candidate_scheme,
                        "legacy_business_status": business_status,
                        "tool_use_session_id": tool_use_session_id,
                    }
                )
    return candidates


def _trusted_tool_session(
    engine: Engine,
    *,
    session_id: int,
    tool_use_session_id: str,
    root: Path,
) -> tuple[bool, str]:
    summary_path = root / "SessionReport" / "SessionRunSummary.json"
    result_index_path = (
        root / "SessionOutput" / "manifests" / "result_index.json"
    )
    if not summary_path.is_file():
        return False, "tool_session_summary_missing"
    try:
        summary = _read_json(summary_path)
    except Exception:
        return False, "tool_session_summary_invalid"
    if str(summary.get("status") or "") != "completed":
        return False, "tool_session_not_completed"
    if not result_index_path.is_file():
        return False, "tool_session_result_index_missing"
    try:
        result_index = _read_json(result_index_path)
    except Exception:
        return False, "tool_session_result_index_invalid"
    if str(result_index.get("tool_use_session_id") or "") not in {
        "",
        tool_use_session_id,
    }:
        return False, "tool_session_result_index_mismatch"
    suffix = (
        f"tool_use_sessions/{tool_use_session_id}/"
        "SessionOutput/manifests/result_index.json"
    )
    with engine.connect() as conn:
        registered = conn.execute(
            text(
                """
SELECT 1
FROM session_files
WHERE session_id = :session_id
  AND tool_use_session_id = :tool_use_session_id
  AND path = :path
LIMIT 1
"""
            ),
            {
                "session_id": session_id,
                "tool_use_session_id": tool_use_session_id,
                "path": suffix,
            },
        ).first()
    if registered is None:
        return False, "tool_session_result_index_not_registered"
    return True, ""


def _existing_run(
    engine: Engine, analysis_run_id: str
) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
SELECT *
FROM media_library_analysis_runs
WHERE analysis_run_id = :analysis_run_id
"""
            ),
            {"analysis_run_id": analysis_run_id},
        ).mappings().first()
    return dict(row) if row is not None else None


def _insert_legacy_run(
    engine: Engine,
    *,
    analysis_run_id: str,
    asset_id: str,
    scheme: str,
    source_version: str,
    tool_use_session_id: str,
    timestamp: int,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
INSERT INTO media_library_analysis_runs (
  analysis_run_id,
  asset_id,
  scheme,
  source_version,
  status,
  tool_use_session_id,
  upstream_refs_json,
  progress_json,
  is_current,
  started_at,
  created_at,
  updated_at
) VALUES (
  :analysis_run_id,
  :asset_id,
  :scheme,
  :source_version,
  'running',
  :tool_use_session_id,
  :upstream_refs_json,
  :progress_json,
  FALSE,
  :timestamp,
  :timestamp,
  :timestamp
)
"""
            ),
            {
                "analysis_run_id": analysis_run_id,
                "asset_id": asset_id,
                "scheme": scheme,
                "source_version": source_version,
                "tool_use_session_id": tool_use_session_id,
                "upstream_refs_json": json.dumps(
                    {
                        "adopted_legacy": True,
                        "tool_use_session_id": tool_use_session_id,
                    }
                ),
                "progress_json": json.dumps(
                    {"stage": "legacy_adoption", "percent": 95}
                ),
                "timestamp": timestamp,
            },
        )


def _mark_failed(
    engine: Engine,
    *,
    analysis_run_id: str,
    scheme: str,
    asset_id: str,
    timestamp: int,
    error_code: str,
) -> None:
    projection_column = (
        "dialogue_status"
        if scheme == "dialogue"
        else "visual_structure_status"
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
UPDATE media_library_analysis_runs
SET status = 'failed',
    error_code = :error_code,
    error_json = :error_json,
    finished_at = :timestamp,
    updated_at = :timestamp
WHERE analysis_run_id = :analysis_run_id
  AND status IN ('queued', 'running')
"""
            ),
            {
                "analysis_run_id": analysis_run_id,
                "error_code": error_code,
                "error_json": json.dumps({"code": error_code}),
                "timestamp": timestamp,
            },
        )
        conn.execute(
            text(
                f"""
UPDATE media_library_tasks
SET {projection_column} = 'failed',
    updated_at = :timestamp
WHERE asset_id = :asset_id
"""
            ),
            {"asset_id": asset_id, "timestamp": timestamp},
        )


def _contract(
    *,
    candidate: dict[str, Any],
    tool_root: Path,
    analysis_run_id: str,
    write: bool,
) -> tuple[dict[str, Any], str, str]:
    common = {
        "tool_root": tool_root,
        "asset_id": str(candidate["asset_id"]),
        "source_version": str(candidate["content_sha256"]),
        "analysis_run_id": analysis_run_id,
        "source_duration_ms": (
            int(candidate["duration_ms"])
            if candidate.get("duration_ms") is not None
            else None
        ),
        "write": write,
    }
    if candidate["scheme"] == "dialogue":
        return publish_dialogue_contract(**common)
    return publish_visual_structure_contract(**common)


def run_rebuild(
    engine: Engine,
    *,
    write: bool = False,
    asset_id: str = "",
    scheme: str | None = None,
) -> dict[str, Any]:
    candidates = _candidate_rows(engine, asset_id=asset_id, scheme=scheme)
    publisher = MediaLibraryFragmentPublisher(engine)
    run_repo = AnalysisRunRepository(engine)
    items: list[dict[str, Any]] = []
    adopted = 0
    failed = 0
    for candidate in candidates:
        source_version = str(candidate.get("content_sha256") or "")
        tool_use_session_id = str(candidate["tool_use_session_id"])
        analysis_run_id = _legacy_run_id(
            asset_id=str(candidate["asset_id"]),
            scheme=str(candidate["scheme"]),
            source_version=source_version,
            tool_use_session_id=tool_use_session_id,
        )
        item: dict[str, Any] = {
            "asset_id": str(candidate["asset_id"]),
            "scheme": str(candidate["scheme"]),
            "tool_use_session_id": tool_use_session_id,
            "analysis_run_id": analysis_run_id,
        }
        items.append(item)
        try:
            if len(source_version) != 64 or any(
                character not in "0123456789abcdef"
                for character in source_version
            ):
                raise ValueError("media_source_identity_missing")
            tool_root = (
                Path(str(candidate["workspace_dir"])).resolve()
                / "tool_use_sessions"
                / tool_use_session_id
            )
            trusted, trust_error = _trusted_tool_session(
                engine,
                session_id=int(candidate["session_id"]),
                tool_use_session_id=tool_use_session_id,
                root=tool_root,
            )
            if not trusted:
                raise ValueError(trust_error)
            payload, digest, result_index_path = _contract(
                candidate=candidate,
                tool_root=tool_root,
                analysis_run_id=analysis_run_id,
                write=False,
            )
            item.update(
                {
                    "result_hash": digest,
                    "fragment_count": len(payload.get("items") or []),
                }
            )
            existing = _existing_run(engine, analysis_run_id)
            if existing is not None:
                if (
                    str(existing.get("status")) == "ready"
                    and bool(existing.get("is_current"))
                    and str(existing.get("result_hash") or "") == digest
                ):
                    item["status"] = "already_adopted"
                    continue
                raise ValueError("legacy_adoption_conflict")
            if not write:
                item["status"] = "would_adopt"
                continue
            timestamp = int(time.time() * 1000)
            _contract(
                candidate=candidate,
                tool_root=tool_root,
                analysis_run_id=analysis_run_id,
                write=True,
            )
            _insert_legacy_run(
                engine,
                analysis_run_id=analysis_run_id,
                asset_id=str(candidate["asset_id"]),
                scheme=str(candidate["scheme"]),
                source_version=source_version,
                tool_use_session_id=tool_use_session_id,
                timestamp=timestamp,
            )
            try:
                if candidate["scheme"] == "dialogue":
                    publisher.publish_dialogue(
                        asset_id=str(candidate["asset_id"]),
                        analysis_run_id=analysis_run_id,
                        result_hash=digest,
                        fragments=payload["items"],
                        timestamp=timestamp,
                        result_index_path=result_index_path,
                    )
                else:
                    run_repo.activate_ready(
                        analysis_run_id,
                        timestamp=timestamp,
                        schema_version=str(payload["schema_version"]),
                        result_hash=digest,
                        result_index_path=result_index_path,
                        progress={
                            "stage": "legacy_adoption",
                            "percent": 100,
                            "fragment_count": len(payload["items"]),
                        },
                        upstream_refs={
                            "adopted_legacy": True,
                            "tool_use_session_id": tool_use_session_id,
                            "sampling_strategy": "scene_midpoint_v1",
                        },
                    )
            except Exception:
                _mark_failed(
                    engine,
                    analysis_run_id=analysis_run_id,
                    scheme=str(candidate["scheme"]),
                    asset_id=str(candidate["asset_id"]),
                    timestamp=int(time.time() * 1000),
                    error_code="legacy_adoption_publish_failed",
                )
                raise
            item["status"] = "adopted"
            adopted += 1
        except Exception as exc:
            failed += 1
            item["status"] = "failed"
            item["error"] = str(exc).strip() or exc.__class__.__name__
    return {
        "dry_run": not write,
        "candidate_count": len(candidates),
        "adopted_count": adopted,
        "failed_count": failed,
        "items": items,
    }


def main() -> None:
    args = parse_args()
    engine = create_engine(
        sqlalchemy_database_url(args.database_url), future=True
    )
    try:
        report = run_rebuild(
            engine,
            write=bool(args.write),
            asset_id=str(args.asset_id or "").strip(),
            scheme=args.scheme,
        )
    finally:
        engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
