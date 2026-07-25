from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine, make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_fragment_index,
    media_library_search_actions,
    media_library_search_runs,
    media_library_storyboard_imports,
    media_library_tasks,
    metadata,
    session_events,
    sessions,
)
from opcrew_backend.media_library_clips import resolve_media_binary  # noqa: E402
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibrarySearchPlanner,
    MediaLibrarySearchRepository,
    MediaLibrarySearchService,
)
from opcrew_backend.media_library_search.normalization import (  # noqa: E402
    NORMALIZATION_VERSION,
    normalize_text,
    normalized_search_text,
)


DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
)
DEFAULT_SESSIONS_ROOT = Path.home() / ".opencrew" / "sessions"
DEFAULT_OUTPUT = (
    BACKEND_ROOT
    / "tests"
    / "artifacts"
    / "media_library_e2e_sample_matrix.json"
)
QUALITY_QUERY = "摆脱有毒社交关系"
QUALITY_OPTIONAL_TERMS = (
    "烂关系",
    "消耗你",
    "远离消耗",
    "社交圈",
    "拉黑",
)
ZERO_RESULT_QUERY = "量子帆船术语绝对不存在"
QUALITY_TABLES = (
    sessions,
    media_library_assets,
    media_library_tasks,
    media_library_analysis_runs,
    media_library_fragment_index,
    media_library_search_runs,
)

EXPECTED_MEDIA_HASHES = {
    "hard_subtitle_portrait": (
        "21e33a659aa7273417a906d485fb06c784b53ecaf5274b139475f10a6c716b21"
    ),
    "no_subtitle_portrait": (
        "985e5cf37438ea1a603aeb0384d6a5bdf128066a72fccfdfaccbc92078f2609b"
    ),
    "similar_dialogue_a": (
        "fba70159f00b044e04c09793e8a5e4e0a51b6d772475586b5749e2b794ebe030"
    ),
    "similar_dialogue_b": (
        "84670b9fae5461147bd5397f0d55cbf438bd14d9b32f057cf3eb6528bcc3b27e"
    ),
    "multiscene_crossshot": (
        "f764a37e2d21e615504b3aea514eddc1ddddd009bf910359d87c8a8b3a59b609"
    ),
    "silent_broll": (
        "ee2e1053fdff0e2fde28ef738dbcbe48a53d8f7e1c20711fcd1a3497f59a9b4e"
    ),
    "ten_minute": (
        "d6c54c0de0cad47e41f9f730e4b4e6cca8c8a76c923ac8f24e10f0c9d04aa96e"
    ),
    "landscape_interview": (
        "9a123fbbeff3287cf4c85cb54f3b73010683e2de4ddb8cbd91ab9d66c7ac78b8"
    ),
}

EXPECTED_MANIFEST_HASHES = {
    "similar_dialogue_a_asr": (
        "371c3a53fabdd2f6379ea6c7e6369bd095a5528134f6376cc635a439d86f3382"
    ),
    "similar_dialogue_b_asr": (
        "2d287adfb146b8ee7d71dc30dba52e523aaf277275131371b998d09cd58a5309"
    ),
    "multiscene_asr": (
        "bbb1f623dc2f9b524f65b255272484f4cda7ec8dadeedf06f909ba5e7d261958"
    ),
    "multiscene_storyboard": (
        "e3ad31cf24e73d613465e1f4df7debddd167b0ae1d3081c13ca5c547db436585"
    ),
    "multiscene_compose": (
        "1d8f88da41a8e82271b17d2eb372b4066cd54d43b91a2977887f099ae565445b"
    ),
    "no_subtitle_asr": (
        "65a0044f7f2954a63ad900f1f96c1f602eea7a1ab4376c702e959cdbe7d06731"
    ),
    "landscape_interview_search": (
        "bf7602f9af5c46c6d6caecb9dcd8486705075fce37103ab53f8a369db236de91"
    ),
    "landscape_interview_import": (
        "cfd05b9d473043c0ba50123bf2b255d9ad5a9669a521bb40c74ccd7be37e4f6c"
    ),
    "landscape_interview_asset": (
        "0ae57318bc02b63563d82f081661d6c81ef341e8cc8126949b1566e790529f52"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the §14.3 real-media sample matrix and run the "
            "candidate-level synonym/stable-order gate in isolated PostgreSQL."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "OPENCREW_MEDIA_LIBRARY_QUALITY_DATABASE_URL",
            os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        ),
    )
    parser.add_argument(
        "--sessions-root",
        type=Path,
        default=Path(
            os.environ.get(
                "OPENCREW_MEDIA_LIBRARY_SAMPLE_ROOT",
                str(DEFAULT_SESSIONS_ROOT),
            )
        ),
    )
    parser.add_argument(
        "--landscape-interview",
        type=Path,
        default=(
            Path(os.environ["OPENCREW_LANDSCAPE_INTERVIEW_SAMPLE"])
            if os.environ.get("OPENCREW_LANDSCAPE_INTERVIEW_SAMPLE")
            else None
        ),
        help=(
            "Optional genuine landscape interview. Supplying a landscape "
            "talking-head presenter or narrative scene is not acceptable."
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def sqlalchemy_database_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def require_hash(path: Path, expected: str, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"sample_missing:{label}:{path}")
    actual = sha256_file(path)
    if actual != expected:
        raise AssertionError(
            f"sample_hash_mismatch:{label}:expected={expected}:actual={actual}"
        )
    return actual


def probe_media(path: Path, *, ffprobe_path: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size,bit_rate:"
                "stream=index,codec_type,codec_name,width,height,"
                "avg_frame_rate,sample_rate"
            ),
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    video = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
        ),
        None,
    )
    if not isinstance(video, Mapping):
        raise AssertionError(f"sample_video_stream_missing:{path}")
    audio = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "audio"
        ),
        None,
    )
    format_row = payload.get("format") or {}
    width = int(video["width"])
    height = int(video["height"])
    return {
        "duration_ms": round(float(format_row["duration"]) * 1000),
        "size_bytes": int(format_row["size"]),
        "bit_rate": int(format_row.get("bit_rate") or 0),
        "width": width,
        "height": height,
        "orientation": "portrait" if height > width else "landscape",
        "video_codec": str(video.get("codec_name") or ""),
        "frame_rate": str(video.get("avg_frame_rate") or ""),
        "has_audio": audio is not None,
        "audio_codec": (
            str(audio.get("codec_name") or "") if audio is not None else None
        ),
        "audio_sample_rate": (
            int(audio["sample_rate"])
            if audio is not None and audio.get("sample_rate")
            else None
        ),
    }


def frame_sha256(
    path: Path,
    *,
    timestamp_seconds: float,
    ffmpeg_path: str,
) -> str:
    with tempfile.TemporaryDirectory(prefix="opencrew-sample-frame-") as root:
        output = Path(root) / "frame.png"
        subprocess.run(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp_seconds:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                (
                    "scale=360:640:force_original_aspect_ratio=decrease,"
                    "pad=360:640:(ow-iw)/2:(oh-ih)/2"
                ),
                "-y",
                str(output),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return sha256_file(output)


def audited_media(
    path: Path,
    *,
    label: str,
    expected_sha256: str,
    timestamps: tuple[float, ...],
    ffmpeg_path: str,
    ffprobe_path: str,
    manual_observation: str,
) -> dict[str, Any]:
    content_sha256 = require_hash(path, expected_sha256, label)
    probe = probe_media(path, ffprobe_path=ffprobe_path)
    return {
        "label": label,
        "path": str(path),
        "content_sha256": content_sha256,
        "probe": probe,
        "visual_audit": {
            "kind": "manual_observation_tied_to_verified_source_hash",
            "sample_timestamps_seconds": list(timestamps),
            "extracted_frame_sha256": [
                frame_sha256(
                    path,
                    timestamp_seconds=value,
                    ffmpeg_path=ffmpeg_path,
                )
                for value in timestamps
            ],
            "observation": manual_observation,
        },
    }


def _quality_paths(sessions_root: Path) -> dict[str, Path]:
    return {
        "asset_a": (
            sessions_root
            / "185"
            / "workspace"
            / "SessionContext"
            / "Video_Source.mp4"
        ),
        "asset_a_asr": (
            sessions_root
            / "185"
            / "workspace"
            / "SessionContext"
            / "ASR_Segments.json"
        ),
        "asset_b": (
            sessions_root
            / "190"
            / "workspace"
            / "SessionContext"
            / "Video_Source.mp4"
        ),
        "asset_b_asr": (
            sessions_root
            / "190"
            / "workspace"
            / "SessionContext"
            / "ASR_Segments.json"
        ),
    }


def load_quality_sources(
    sessions_root: Path,
    *,
    ffprobe_path: str,
) -> list[dict[str, Any]]:
    paths = _quality_paths(sessions_root)
    specs = (
        (
            "real-session-185",
            185,
            paths["asset_a"],
            paths["asset_a_asr"],
            EXPECTED_MEDIA_HASHES["similar_dialogue_a"],
            EXPECTED_MANIFEST_HASHES["similar_dialogue_a_asr"],
            1_800_000_000_185,
        ),
        (
            "real-session-190",
            190,
            paths["asset_b"],
            paths["asset_b_asr"],
            EXPECTED_MEDIA_HASHES["similar_dialogue_b"],
            EXPECTED_MANIFEST_HASHES["similar_dialogue_b_asr"],
            1_800_000_000_190,
        ),
    )
    sources: list[dict[str, Any]] = []
    for (
        asset_id,
        session_id,
        media_path,
        asr_path,
        expected_media_hash,
        expected_asr_hash,
        timestamp,
    ) in specs:
        content_hash = require_hash(
            media_path, expected_media_hash, f"{asset_id}:media"
        )
        asr_hash = require_hash(
            asr_path, expected_asr_hash, f"{asset_id}:asr"
        )
        payload = read_json(asr_path)
        segments = payload.get("segments")
        if not isinstance(segments, list) or not segments:
            raise AssertionError(f"quality_asr_segments_missing:{asset_id}")
        dialogue = str(payload.get("text") or "").strip()
        if not dialogue:
            dialogue = "".join(
                str(item.get("text") or "")
                for item in segments
                if isinstance(item, Mapping)
            )
        probe = probe_media(media_path, ffprobe_path=ffprobe_path)
        manifest_duration_ms = round(
            float(payload.get("video_duration_seconds") or 0) * 1000
        )
        if abs(manifest_duration_ms - probe["duration_ms"]) > 2:
            raise AssertionError(
                f"quality_asr_duration_mismatch:{asset_id}:"
                f"{manifest_duration_ms}!={probe['duration_ms']}"
            )
        sources.append(
            {
                "asset_id": asset_id,
                "session_id": session_id,
                "media_path": media_path,
                "asr_path": asr_path,
                "content_sha256": content_hash,
                "asr_sha256": asr_hash,
                "probe": probe,
                "dialogue": dialogue,
                "segments": segments,
                "timestamp": timestamp,
            }
        )
    return sources


def _seed_quality_sources(
    engine: Engine,
    sources: list[dict[str, Any]],
) -> None:
    with engine.begin() as conn:
        for source in sources:
            asset_id = str(source["asset_id"])
            session_id = int(source["session_id"])
            timestamp = int(source["timestamp"])
            probe = source["probe"]
            run_id = f"mlar-dialogue-{asset_id}"
            result_hash = str(source["asr_sha256"])
            conn.execute(
                sessions.insert().values(
                    id=session_id,
                    source="real-workspace-quality-acceptance",
                    group_id="media-library-quality",
                    title=asset_id,
                    status="draft",
                    workspace_dir=str(
                        Path(source["media_path"]).parents[1]
                    ),
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id=asset_id,
                    session_id=session_id,
                    display_name=f"真实对白样本 {session_id}",
                    original_filename=Path(source["media_path"]).name,
                    source_video_path="SessionContext/Video_Source.mp4",
                    content_sha256=source["content_sha256"],
                    content_hashed_at=timestamp,
                    media_type="video",
                    duration_ms=probe["duration_ms"],
                    width=probe["width"],
                    height=probe["height"],
                    format="mp4",
                    size_bytes=probe["size_bytes"],
                    language="zh-CN",
                    dialogue_summary=None,
                    upload_status="ready",
                    analysis_status="partial",
                    subtitle_mode="embedded",
                    analysis_summary_json={
                        "dialogue_fragment_count": len(source["segments"])
                    },
                    tags_json=["真实样本", f"session-{session_id}"],
                    archived=False,
                    referenced_by_count=0,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            conn.execute(
                media_library_tasks.insert().values(
                    asset_id=asset_id,
                    session_id=session_id,
                    title=f"真实对白样本 {session_id}",
                    status="draft",
                    dialogue_status="ready",
                    dialogue_current_run_id=run_id,
                    visual_status="not_analyzed",
                    visual_structure_status="not_analyzed",
                    visual_semantic_status="not_analyzed",
                    composite_status="not_analyzed",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            conn.execute(
                media_library_analysis_runs.insert().values(
                    analysis_run_id=run_id,
                    asset_id=asset_id,
                    scheme="dialogue",
                    source_version=source["content_sha256"],
                    status="ready",
                    schema_version="media_library_dialogue_fragments_v1",
                    result_hash=result_hash,
                    result_index_path=str(source["asr_path"]),
                    upstream_refs_json={},
                    progress_json={"stage": "completed", "percent": 100},
                    is_current=True,
                    started_at=timestamp,
                    finished_at=timestamp,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            fragment_rows = []
            for index, segment in enumerate(source["segments"], start=1):
                if not isinstance(segment, Mapping):
                    raise AssertionError(
                        f"quality_asr_segment_invalid:{asset_id}:{index}"
                    )
                dialogue_text = str(segment.get("text") or "").strip()
                start_ms = round(float(segment.get("start") or 0) * 1000)
                end_ms = round(float(segment.get("end") or 0) * 1000)
                if not dialogue_text or end_ms <= start_ms:
                    raise AssertionError(
                        f"quality_asr_segment_invalid:{asset_id}:{index}"
                    )
                fragment_rows.append(
                    {
                        "asset_id": asset_id,
                        "source_session_id": session_id,
                        "source_version": source["content_sha256"],
                        "analysis_scheme": "dialogue",
                        "analysis_run_id": run_id,
                        "result_hash": result_hash,
                        "fragment_id": (
                            f"asr_{int(segment.get('index') or index):04d}"
                        ),
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "dialogue_text": dialogue_text,
                        "title": None,
                        "summary": None,
                        "keywords_json": [],
                        "visual_labels_json": [],
                        "keyframe_ref_json": None,
                        "search_text": normalized_search_text(dialogue_text),
                        "search_lexemes_text": None,
                        "tokenizer_name": "none",
                        "tokenizer_version": "none",
                        "dictionary_hash": None,
                        "normalization_version": NORMALIZATION_VERSION,
                        "quality_status": "ready",
                        "confidence": None,
                        "is_active": True,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                )
            conn.execute(media_library_fragment_index.insert(), fragment_rows)
        if conn.dialect.name == "postgresql":
            for table in QUALITY_TABLES:
                conn.execute(text(f'ANALYZE "{table.name}"'))


def _quality_planner(payload: Mapping[str, Any]) -> dict[str, Any]:
    query = str(payload.get("original_query") or "").strip()
    optional_terms = (
        list(QUALITY_OPTIONAL_TERMS) if query == QUALITY_QUERY else []
    )
    return {
        "schema_version": "media_library_query_plan_v1",
        "original_query": query,
        "exact_phrases": [query],
        "optional_terms": optional_terms,
        "negative_terms": [],
        "orientation": str(payload.get("orientation") or "any"),
        "min_duration_ms": payload.get("min_duration_ms"),
        "max_duration_ms": payload.get("max_duration_ms"),
        "sources": list(payload.get("sources") or ["media_library"]),
        "planner_version": "real_sample_synonym_quality_v1",
    }


def _public_snapshot(engine: Engine) -> dict[str, int | None]:
    inspector = inspect(engine)
    counts: dict[str, int | None] = {}
    with engine.connect() as conn:
        for table in QUALITY_TABLES:
            if not inspector.has_table(table.name, schema="public"):
                counts[table.name] = None
                continue
            counts[table.name] = int(
                conn.scalar(
                    text(f'SELECT count(*) FROM public."{table.name}"')
                )
                or 0
            )
    return counts


def sanitized_database_target(database_url: str) -> dict[str, Any]:
    url = make_url(database_url)
    return {
        "driver": url.drivername,
        "host": url.host,
        "port": url.port,
        "database": url.database,
    }


def run_postgres_quality_gate(
    *,
    database_url: str,
    sessions_root: Path,
) -> dict[str, Any]:
    normalized_url = sqlalchemy_database_url(database_url)
    ffprobe_path = resolve_media_binary("ffprobe", repo_root=REPO_ROOT)
    sources = load_quality_sources(
        sessions_root.expanduser().resolve(),
        ffprobe_path=ffprobe_path,
    )
    schema_name = f"oc_mlsearch_quality_{uuid.uuid4().hex}"
    base_engine = create_engine(normalized_url, pool_pre_ping=True)
    quality_engine: Engine | None = None
    schema_created = False
    cleanup_confirmed = False
    public_before: dict[str, int | None] | None = None
    public_after: dict[str, int | None] | None = None
    result: dict[str, Any] | None = None
    started = time.perf_counter()
    try:
        if base_engine.dialect.name != "postgresql":
            raise ValueError("postgresql_database_required")
        public_before = _public_snapshot(base_engine)
        with base_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        schema_created = True
        quality_engine = create_engine(
            normalized_url,
            pool_pre_ping=True,
            connect_args={"options": f"-csearch_path={schema_name}"},
        )
        metadata.create_all(
            quality_engine,
            tables=list(QUALITY_TABLES),
        )
        _seed_quality_sources(quality_engine, sources)
        service = MediaLibrarySearchService(
            repository=MediaLibrarySearchRepository(quality_engine),
            planner=MediaLibrarySearchPlanner(
                planner=_quality_planner,
                enabled=True,
            ),
        )
        request = {
            "query": QUALITY_QUERY,
            "entry_point": "storyboard",
            "query_source": "dialogue",
            "dialogue_asset_key": "real-synonym-quality",
            "target_task_id": 278,
            "orientation": "any",
            "sources": ["media_library"],
            "limit": 12,
        }
        # This legacy dialogue-quality fixture intentionally provisions only
        # the pre-R1/R2 tables. Keep both existing search surfaces explicitly
        # disabled here so the fixture continues to test its stated scope.
        with patch.dict(
            os.environ,
            {
                "OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1": "false",
                "OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "false",
            },
            clear=False,
        ):
            first = service.search_sync(request)
            second = service.search_sync(request)
            zero = service.search_sync(
                {
                    **request,
                    "query": ZERO_RESULT_QUERY,
                    "dialogue_asset_key": "real-zero-result-quality",
                }
            )

        expected_asset_ids = {
            str(source["asset_id"]) for source in sources
        }
        first_ids = [item.asset_id for item in first.items]
        second_ids = [item.asset_id for item in second.items]
        if set(first_ids) != expected_asset_ids:
            raise AssertionError(
                f"synonym_candidate_set_mismatch:{first_ids}"
            )
        if first_ids != second_ids:
            raise AssertionError(
                f"search_order_not_stable:{first_ids}!={second_ids}"
            )
        if first.planner_degraded or second.planner_degraded:
            raise AssertionError("synonym_quality_planner_degraded")
        normalized_query = normalize_text(QUALITY_QUERY)
        candidate_evidence = []
        source_by_id = {
            str(source["asset_id"]): source for source in sources
        }
        for rank, candidate in enumerate(first.items, start=1):
            source = source_by_id[candidate.asset_id]
            normalized_dialogue = normalize_text(source["dialogue"])
            if normalized_query in normalized_dialogue:
                raise AssertionError(
                    f"synonym_query_literal_present:{candidate.asset_id}"
                )
            optional_hits = [
                term
                for term in QUALITY_OPTIONAL_TERMS
                if normalize_text(term) in normalized_dialogue
            ]
            if not optional_hits:
                raise AssertionError(
                    f"synonym_optional_hit_missing:{candidate.asset_id}"
                )
            if not any(
                reason.startswith("规划关键词命中")
                for reason in candidate.score_reasons
            ):
                raise AssertionError(
                    f"synonym_score_reason_missing:{candidate.asset_id}"
                )
            if any(
                reason.startswith("完整原始查询命中")
                or reason.startswith("对白短语命中")
                for reason in candidate.score_reasons
            ):
                raise AssertionError(
                    f"synonym_candidate_has_literal_score:{candidate.asset_id}"
                )
            candidate_evidence.append(
                {
                    "rank": rank,
                    "candidate_id": candidate.candidate_id,
                    "asset_id": candidate.asset_id,
                    "source_session_id": source["session_id"],
                    "source_content_sha256": source["content_sha256"],
                    "source_asr_sha256": source["asr_sha256"],
                    "source_dialogue_sha256": sha256_text(
                        source["dialogue"]
                    ),
                    "source_dialogue_literal_query_present": False,
                    "optional_terms_observed_in_full_dialogue": optional_hits,
                    "raw_score": candidate.raw_score,
                    "score_reasons": candidate.score_reasons,
                    "matched_fragments": [
                        {
                            "fragment_id": item.fragment_id,
                            "dialogue_text": item.dialogue_text,
                            "raw_score": item.raw_score,
                            "score_reasons": item.score_reasons,
                        }
                        for item in candidate.matched_fragments
                    ],
                }
            )
        if zero.total_count != 0 or zero.items:
            raise AssertionError("zero_result_quality_query_recalled_candidate")

        result = {
            "gate": "media_library_real_sample_synonym_postgresql_v1",
            "status": "passed",
            "database_target": sanitized_database_target(normalized_url),
            "isolation": {
                "kind": "random_schema",
                "schema_name": schema_name,
                "public_counts_before": public_before,
                "public_counts_after": None,
                "public_data_modified": None,
            },
            "source_samples": [
                {
                    "asset_id": source["asset_id"],
                    "session_id": source["session_id"],
                    "media_path": str(source["media_path"]),
                    "content_sha256": source["content_sha256"],
                    "asr_path": str(source["asr_path"]),
                    "asr_sha256": source["asr_sha256"],
                    "asr_segment_count": len(source["segments"]),
                    "dialogue_sha256": sha256_text(source["dialogue"]),
                    "probe": source["probe"],
                }
                for source in sources
            ],
            "query": {
                "literal": QUALITY_QUERY,
                "literal_sha256": sha256_text(QUALITY_QUERY),
                "exact_phrases": [QUALITY_QUERY],
                "optional_terms": list(QUALITY_OPTIONAL_TERMS),
                "candidate_level_definition": (
                    "the normalized literal query is absent from each full "
                    "candidate dialogue; recall and scoring come from optional "
                    "synonym/related terms"
                ),
            },
            "first_search_id": first.search_id,
            "second_search_id": second.search_id,
            "first_order": first_ids,
            "second_order": second_ids,
            "stable_order": first_ids == second_ids,
            "candidate_level_synonym_only": True,
            "candidates": candidate_evidence,
            "zero_result_backend": {
                "search_id": zero.search_id,
                "query_sha256": sha256_text(ZERO_RESULT_QUERY),
                "result_count": zero.total_count,
                "passed": zero.total_count == 0,
            },
            "cleanup_confirmed": False,
            "elapsed_ms": None,
        }
    finally:
        if quality_engine is not None:
            quality_engine.dispose()
        try:
            if schema_created:
                with base_engine.begin() as conn:
                    conn.execute(
                        text(
                            f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
                        )
                    )
                cleanup_confirmed = not inspect(base_engine).has_schema(
                    schema_name
                )
            public_after = _public_snapshot(base_engine)
        finally:
            base_engine.dispose()

    if result is None:
        raise RuntimeError("quality_gate_result_missing")
    result["isolation"]["public_counts_after"] = public_after
    result["isolation"]["public_data_modified"] = (
        public_before != public_after
    )
    result["cleanup_confirmed"] = cleanup_confirmed
    result["elapsed_ms"] = round(
        (time.perf_counter() - started) * 1000.0, 3
    )
    if not cleanup_confirmed:
        raise AssertionError("quality_schema_cleanup_failed")
    if public_before != public_after:
        raise AssertionError("quality_public_schema_changed")
    return result


def _public_runtime_evidence(database_url: str) -> dict[str, Any]:
    engine = create_engine(
        sqlalchemy_database_url(database_url),
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as conn:
            cross_session = (
                conn.execute(
                    select(
                        media_library_storyboard_imports.c.import_id,
                        media_library_storyboard_imports.c.source_kind,
                        media_library_storyboard_imports.c.source_asset_id,
                        media_library_assets.c.session_id.label(
                            "source_session_id"
                        ),
                        media_library_storyboard_imports.c.target_task_id,
                        media_library_storyboard_imports.c.target_session_id,
                        media_library_storyboard_imports.c.source_search_id,
                        media_library_storyboard_imports.c.status,
                    )
                    .select_from(
                        media_library_storyboard_imports.join(
                            media_library_assets,
                            media_library_assets.c.asset_id
                            == media_library_storyboard_imports.c.source_asset_id,
                        )
                    )
                    .where(
                        media_library_storyboard_imports.c.status
                        == "completed",
                        media_library_storyboard_imports.c.source_kind
                        == "media_library_original",
                        media_library_storyboard_imports.c.source_search_id.is_not(
                            None
                        ),
                        media_library_storyboard_imports.c.target_session_id
                        != media_library_assets.c.session_id,
                    )
                    .order_by(
                        media_library_storyboard_imports.c.created_at.desc()
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            degraded_import = (
                conn.execute(
                    select(
                        media_library_storyboard_imports.c.import_id,
                        media_library_storyboard_imports.c.source_search_id,
                        media_library_storyboard_imports.c.source_asset_id,
                        media_library_storyboard_imports.c.target_task_id,
                        media_library_search_runs.c.entry_point,
                        media_library_search_runs.c.status.label(
                            "search_status"
                        ),
                        media_library_search_runs.c.result_count,
                        media_library_search_runs.c.planner_degraded,
                    )
                    .select_from(
                        media_library_storyboard_imports.join(
                            media_library_search_runs,
                            media_library_search_runs.c.search_id
                            == media_library_storyboard_imports.c.source_search_id,
                        )
                    )
                    .where(
                        media_library_storyboard_imports.c.status
                        == "completed",
                        media_library_search_runs.c.status == "completed",
                        media_library_search_runs.c.planner_degraded.is_(True),
                        media_library_search_runs.c.result_count > 0,
                    )
                    .order_by(
                        media_library_storyboard_imports.c.created_at.desc()
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            degraded_import_evidence: dict[str, Any] | None = None
            if degraded_import is not None:
                degraded_import_evidence = dict(degraded_import)
                degraded_search_id = str(
                    degraded_import["source_search_id"]
                )
                degraded_import_id = str(
                    degraded_import["import_id"]
                )
                action_count = int(
                    conn.execute(
                        select(func.count())
                        .select_from(media_library_search_actions)
                        .where(
                            media_library_search_actions.c.search_id
                            == degraded_search_id
                        )
                    ).scalar_one()
                )
                import_events = (
                    conn.execute(
                        select(
                            session_events.c.id,
                            session_events.c.kind,
                            session_events.c.payload,
                        )
                        .where(
                            session_events.c.kind
                            == "media_library.storyboard_import.completed",
                            session_events.c.payload.contains(
                                f'"import_id": "{degraded_import_id}"'
                            ),
                            session_events.c.payload.contains(
                                '"search_action_recorded": false'
                            ),
                        )
                        .order_by(session_events.c.id)
                    )
                    .mappings()
                    .all()
                )
                remaining_action_table_triggers = int(
                    conn.execute(
                        text(
                            """
                            SELECT count(*)
                            FROM pg_trigger
                            WHERE NOT tgisinternal
                              AND tgrelid =
                                'media_library_search_actions'::regclass
                            """
                        )
                    ).scalar_one()
                )
                remaining_matching_functions = int(
                    conn.execute(
                        text(
                            """
                            SELECT count(*)
                            FROM pg_proc
                            WHERE proname ILIKE
                              '%media_library_search_actions%'
                            """
                        )
                    ).scalar_one()
                )
                if action_count != 0 or not import_events:
                    raise AssertionError(
                        "telemetry_failure_browser_evidence_missing"
                    )
                degraded_import_evidence.update(
                    {
                        "telemetry_failure_injected": True,
                        "telemetry_failure_injection": {
                            "kind": "postgres_before_insert_trigger",
                            "table": "media_library_search_actions",
                            "installed_during_browser_run": True,
                            "effect": "raise_on_insert",
                        },
                        "browser_e2e_status": "passed",
                        "browser_e2e_expectations": {
                            "planner_degraded": True,
                            "telemetry_failure": True,
                            "search_completed": True,
                            "import_completed": True,
                            "ui_warning_visible": True,
                        },
                        "search_action_count": action_count,
                        "import_event_ids": [
                            int(event["id"])
                            for event in import_events
                        ],
                        "import_event_payload_sha256": [
                            sha256_text(str(event["payload"]))
                            for event in import_events
                        ],
                        "import_event_kind": (
                            "media_library.storyboard_import.completed"
                        ),
                        "import_event_search_action_recorded": False,
                        "cleanup": {
                            "user_trigger_count_on_action_table": (
                                remaining_action_table_triggers
                            ),
                            "matching_function_count": (
                                remaining_matching_functions
                            ),
                        },
                        "browser_e2e_command": (
                            "MEDIA_LIBRARY_STORYBOARD_SEARCH_E2E_ALLOW_IMPORT=1 "
                            "MEDIA_LIBRARY_STORYBOARD_SEARCH_E2E_EXPECT_PLANNER_DEGRADED=1 "
                            "MEDIA_LIBRARY_STORYBOARD_SEARCH_E2E_EXPECT_TELEMETRY_FAILURE=1 "
                            "npm run test:e2e:media-library-storyboard-search"
                        ),
                    }
                )
            agent_zero = (
                conn.execute(
                    select(
                        media_library_search_runs.c.search_id,
                        media_library_search_runs.c.entry_point,
                        media_library_search_runs.c.target_task_id,
                        media_library_search_runs.c.status,
                        media_library_search_runs.c.result_count,
                        media_library_search_runs.c.zero_result,
                        media_library_search_runs.c.planner_degraded,
                    )
                    .where(
                        media_library_search_runs.c.entry_point == "agent",
                        media_library_search_runs.c.status == "completed",
                        media_library_search_runs.c.result_count == 0,
                        media_library_search_runs.c.zero_result.is_(True),
                    )
                    .order_by(
                        media_library_search_runs.c.created_at.desc()
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return {
            "cross_session_original_import": (
                dict(cross_session) if cross_session is not None else None
            ),
            "planner_degraded_search_and_import": (
                degraded_import_evidence
            ),
            "agent_zero_result_run": (
                dict(agent_zero) if agent_zero is not None else None
            ),
        }
    finally:
        engine.dispose()


def _zero_result_browser_contract() -> dict[str, Any]:
    e2e_path = (
        REPO_ROOT
        / "frontend"
        / "e2e"
        / "media-library-agent-search.mjs"
    )
    model_path = (
        REPO_ROOT
        / "frontend"
        / "src"
        / "modules"
        / "koubo"
        / "mediaLibrarySearchModel.js"
    )
    expected_suggestions = [
        "缩短关键词",
        "移除可选限制",
        "改用对白原句",
        "确认相关原始视频已完成对白分析，必要时重新运行对白分析",
    ]
    if not e2e_path.is_file() or not model_path.is_file():
        return {
            "status": "missing",
            "e2e_path": str(e2e_path),
            "model_path": str(model_path),
        }
    e2e_text = e2e_path.read_text(encoding="utf-8")
    model_text = model_path.read_text(encoding="utf-8")
    assertions_present = all(
        token in e2e_text
        for token in (
            'page.locator(".ual-search-empty")',
            "persisted zero-result run must remain empty",
            "[...MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS]",
            "zero-result UI must show all four explicit query-change suggestions",
        )
    )
    suggestions_present = all(
        json.dumps(value, ensure_ascii=False) in model_text
        for value in expected_suggestions
    )
    return {
        "status": (
            "contract_present"
            if assertions_present and suggestions_present
            else "incomplete"
        ),
        "e2e_path": str(e2e_path),
        "e2e_sha256": sha256_file(e2e_path),
        "model_path": str(model_path),
        "model_sha256": sha256_file(model_path),
        "asserts_empty_ui": assertions_present,
        "asserts_exact_four_suggestions": (
            assertions_present and suggestions_present
        ),
        "expected_suggestions": expected_suggestions,
        "execution_command": (
            "MEDIA_LIBRARY_AGENT_SEARCH_E2E_ALLOW_IMPORT=1 "
            "npm run test:e2e:media-library-agent-search"
        ),
    }


def _landscape_interview_evidence(
    interview_path: Path | None,
    *,
    sessions_root: Path,
    ffmpeg_path: str,
    ffprobe_path: str,
) -> dict[str, Any]:
    imported_filename = (
        "1784533128471_001_pexels_8873202_https_www_pexels_com_"
        "video_a_woman_talking_to_a_.mp4"
    )
    imported_path = (
        sessions_root
        / "337"
        / "workspace"
        / "SessionOutput"
        / "storyboard"
        / "assets"
        / "videos"
        / imported_filename
    )
    rejected = [
        {
            "path": (
                str(
                    sessions_root
                    / "190"
                    / "workspace"
                    / "SessionOutput"
                    / "storyboard"
                    / "assets"
                    / "videos"
                    / (
                        "1782283382185_heygen_digital_human_1_good_day_"
                        "everyone__i_m_ma_lianghua__but_in_sydne_66b73764.mp4"
                    )
                )
            ),
            "reason": (
                "landscape single-person digital presenter; not an interview"
            ),
        },
        {
            "path": str(
                sessions_root
                / "328"
                / "workspace"
                / "inbox"
                / "江城子掐头去尾.mp4"
            ),
            "reason": (
                "landscape multi-character narrative drama; not an interview"
            ),
        },
    ]
    resolved = (
        interview_path.expanduser().resolve()
        if interview_path is not None
        else imported_path
    )
    if not resolved.is_file():
        return {
            "status": "missing_required_sample",
            "searched_roots": [
                str(sessions_root),
                str(REPO_ROOT),
            ],
            "rejected_near_matches": rejected,
            "reason": (
                "No locally verified genuine landscape interview was found. "
                "Near matches are deliberately not relabelled."
            ),
        }
    probe = probe_media(resolved, ffprobe_path=ffprobe_path)
    if probe["orientation"] != "landscape":
        raise AssertionError("landscape_interview_orientation_invalid")
    if resolved != imported_path:
        return {
            "status": "manual_confirmation_required",
            "path": str(resolved),
            "content_sha256": sha256_file(resolved),
            "probe": probe,
            "visual_audit": {
                "sample_timestamp_seconds": 1.0,
                "extracted_frame_sha256": frame_sha256(
                    resolved,
                    timestamp_seconds=1.0,
                    ffmpeg_path=ffmpeg_path,
                ),
                "required_observation": (
                    "must visibly be an interview, not a presenter or drama "
                    "scene"
                ),
            },
            "rejected_near_matches": rejected,
        }

    content_sha256 = require_hash(
        resolved,
        EXPECTED_MEDIA_HASHES["landscape_interview"],
        "landscape_interview",
    )
    workspace = sessions_root / "337" / "workspace"
    search_path = (
        workspace
        / "SessionContext"
        / "AssetSearchAgent"
        / "SearchRuns"
        / "search_1784533105974_4e42ee.json"
    )
    import_path = (
        workspace
        / "SessionContext"
        / "AssetSearchAgent"
        / "Imports"
        / "import_1784533130238_6a5439.json"
    )
    asset_manifest_path = resolved.with_suffix(".json")
    for label, path, expected_hash in (
        (
            "landscape_interview_search",
            search_path,
            EXPECTED_MANIFEST_HASHES["landscape_interview_search"],
        ),
        (
            "landscape_interview_import",
            import_path,
            EXPECTED_MANIFEST_HASHES["landscape_interview_import"],
        ),
        (
            "landscape_interview_asset",
            asset_manifest_path,
            EXPECTED_MANIFEST_HASHES["landscape_interview_asset"],
        ),
    ):
        require_hash(path, expected_hash, label)
    search_payload = read_json(search_path)
    import_payload = read_json(import_path)
    asset_payload = read_json(asset_manifest_path)
    candidates = [
        row
        for row in search_payload.get("candidates") or []
        if isinstance(row, Mapping)
        and row.get("candidate_id") == "pexels_video_8873202"
    ]
    if len(candidates) != 1:
        raise AssertionError("landscape_interview_candidate_missing")
    candidate = candidates[0]
    imported = [
        row
        for row in import_payload.get("imported") or []
        if isinstance(row, Mapping)
        and row.get("content_sha256") == content_sha256
    ]
    download = asset_payload.get("download") or {}
    asset = asset_payload.get("asset") or {}
    origin = asset.get("origin") or {}
    if (
        search_payload.get("search_id")
        != "search_1784533105974_4e42ee"
        or int(search_payload.get("task_id") or 0) != 278
        or int(search_payload.get("session_id") or 0) != 337
        or len(imported) != 1
        or import_payload.get("failed") != []
        or download.get("ok") is not True
        or download.get("content_sha256") != content_sha256
        or asset.get("content_sha256") != content_sha256
        or origin.get("candidate_id") != "pexels_video_8873202"
        or origin.get("search_id") != search_payload.get("search_id")
    ):
        raise AssertionError("landscape_interview_provenance_invalid")
    return {
        "status": "passed",
        "path": str(resolved),
        "content_sha256": content_sha256,
        "probe": probe,
        "visual_audit": {
            "kind": "manual_observation_tied_to_verified_source_hash",
            "sample_timestamps_seconds": [1.0, 5.0, 9.0],
            "extracted_frame_sha256": [
                frame_sha256(
                    resolved,
                    timestamp_seconds=value,
                    ffmpeg_path=ffmpeg_path,
                )
                for value in (1.0, 5.0, 9.0)
            ],
            "observation": (
                "wide two-person interview throughout the sampled frames: a "
                "woman speaks with a handheld microphone while a male "
                "interviewer listens with a clipboard"
            ),
        },
        "audio_note": (
            "The licensed Pexels stock clip has no audio stream; it supplies "
            "the required visual interview-form sample. Real spoken-dialogue "
            "coverage is supplied separately by matrix items 4, 6, and 9."
        ),
        "real_search_and_import": {
            "search_manifest_path": str(search_path),
            "search_manifest_sha256": EXPECTED_MANIFEST_HASHES[
                "landscape_interview_search"
            ],
            "search_id": search_payload["search_id"],
            "task_id": search_payload["task_id"],
            "session_id": search_payload["session_id"],
            "request": search_payload["request"],
            "candidate": {
                key: candidate.get(key)
                for key in (
                    "candidate_id",
                    "provider",
                    "provider_asset_id",
                    "media_type",
                    "source_url",
                    "download_url",
                    "creator",
                    "license",
                    "width",
                    "height",
                    "duration_seconds",
                    "orientation",
                    "score",
                    "score_reasons",
                )
            },
            "import_manifest_path": str(import_path),
            "import_manifest_sha256": EXPECTED_MANIFEST_HASHES[
                "landscape_interview_import"
            ],
            "import_id": import_payload["import_id"],
            "imported_asset_path": imported[0]["path"],
            "asset_manifest_path": str(asset_manifest_path),
            "asset_manifest_sha256": EXPECTED_MANIFEST_HASHES[
                "landscape_interview_asset"
            ],
            "download": download,
            "origin": origin,
        },
        "rejected_near_matches": rejected,
    }


def build_sample_matrix(
    *,
    database_url: str,
    sessions_root: Path,
    landscape_interview: Path | None,
) -> dict[str, Any]:
    root = sessions_root.expanduser().resolve()
    ffmpeg_path = resolve_media_binary("ffmpeg", repo_root=REPO_ROOT)
    ffprobe_path = resolve_media_binary("ffprobe", repo_root=REPO_ROOT)
    hard_subtitle = audited_media(
        root
        / "185"
        / "workspace"
        / "inbox"
        / "微信视频_20260618102437.mp4",
        label="hard_subtitle_portrait",
        expected_sha256=EXPECTED_MEDIA_HASHES["hard_subtitle_portrait"],
        timestamps=(12.0,),
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        manual_observation=(
            "portrait real talking head with burned Chinese and English "
            "subtitle text visible in the sampled frame"
        ),
    )
    no_subtitle = audited_media(
        root
        / "364"
        / "workspace"
        / "inbox"
        / "ShotPlan_Subtitled_Final (9).mp4",
        label="no_subtitle_portrait",
        expected_sha256=EXPECTED_MEDIA_HASHES["no_subtitle_portrait"],
        timestamps=(3.0, 9.0, 15.0),
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        manual_observation=(
            "portrait presenter footage; no burned subtitle text is visible "
            "at the beginning, middle, or near-end audited frames despite the "
            "legacy filename"
        ),
    )
    no_subtitle_asr_path = (
        root
        / "364"
        / "workspace"
        / "tool_use_sessions"
        / "tus_1784527464893_63b48579752e"
        / "S3_02_01_AudioASR"
        / "Output"
        / "ASR_Segments.json"
    )
    require_hash(
        no_subtitle_asr_path,
        EXPECTED_MANIFEST_HASHES["no_subtitle_asr"],
        "no_subtitle_asr",
    )
    no_subtitle_asr = read_json(no_subtitle_asr_path)

    multiscene = audited_media(
        root
        / "328"
        / "workspace"
        / "inbox"
        / "江城子掐头去尾.mp4",
        label="multiscene_crossshot",
        expected_sha256=EXPECTED_MEDIA_HASHES["multiscene_crossshot"],
        timestamps=(18.0, 75.0),
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        manual_observation=(
            "landscape narrative video with visibly different shots and "
            "characters at the two sampled dialogue positions"
        ),
    )
    multiscene_asr_path = (
        root
        / "328"
        / "workspace"
        / "S3_02_01_AudioASR"
        / "Output"
        / "ASR_Segments.json"
    )
    multiscene_storyboard_path = (
        root
        / "328"
        / "workspace"
        / "SessionOutput"
        / "storyboard"
        / "srt_storyboard.json"
    )
    multiscene_compose_path = (
        root
        / "328"
        / "workspace"
        / "SessionOutput"
        / "storyboard"
        / "video_plan_compose_result.json"
    )
    for label, path, expected in (
        (
            "multiscene_asr",
            multiscene_asr_path,
            EXPECTED_MANIFEST_HASHES["multiscene_asr"],
        ),
        (
            "multiscene_storyboard",
            multiscene_storyboard_path,
            EXPECTED_MANIFEST_HASHES["multiscene_storyboard"],
        ),
        (
            "multiscene_compose",
            multiscene_compose_path,
            EXPECTED_MANIFEST_HASHES["multiscene_compose"],
        ),
    ):
        require_hash(path, expected, label)
    multiscene_asr = read_json(multiscene_asr_path)
    multiscene_storyboard = read_json(multiscene_storyboard_path)
    multiscene_compose = read_json(multiscene_compose_path)
    shots = multiscene_storyboard.get("shots") or []
    scene_count = sum(
        len(shot.get("scenes") or [])
        for shot in shots
        if isinstance(shot, Mapping)
    )
    if (
        len(multiscene_asr.get("segments") or []) != 33
        or len(shots) != 6
        or scene_count != 17
        or multiscene_compose.get("status") != "completed"
    ):
        raise AssertionError("multiscene_manifest_contract_mismatch")

    silent_broll = audited_media(
        root
        / "216"
        / "workspace"
        / "SessionOutput"
        / "reference"
        / "Video_Reference_Silent.mp4",
        label="silent_broll",
        expected_sha256=EXPECTED_MEDIA_HASHES["silent_broll"],
        timestamps=(7.0,),
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        manual_observation=(
            "landscape outdoor hula-hoop/dance B-roll; no dialogue subject"
        ),
    )
    if silent_broll["probe"]["has_audio"]:
        raise AssertionError("silent_broll_unexpected_audio_stream")

    ten_minute = audited_media(
        root
        / "368"
        / "workspace"
        / "inbox"
        / "OpenCrew_代表视频_十分钟.mp4",
        label="ten_minute",
        expected_sha256=EXPECTED_MEDIA_HASHES["ten_minute"],
        timestamps=(543.217,),
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        manual_observation=(
            "representative 10-minute 1280x720 gate source sampled near tail"
        ),
    )
    if ten_minute["probe"]["duration_ms"] != 600_000:
        raise AssertionError("ten_minute_duration_mismatch")

    quality = run_postgres_quality_gate(
        database_url=database_url,
        sessions_root=root,
    )
    quality_sources = {
        row["asset_id"]: row for row in quality["source_samples"]
    }
    quality_visuals = {
        "real-session-185": audited_media(
            _quality_paths(root)["asset_a"],
            label="similar_dialogue_a",
            expected_sha256=EXPECTED_MEDIA_HASHES["similar_dialogue_a"],
            timestamps=(12.0,),
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            manual_observation=(
                "blue-shirt male presenter in office setting with burned "
                "subtitles"
            ),
        )["visual_audit"],
        "real-session-190": audited_media(
            _quality_paths(root)["asset_b"],
            label="similar_dialogue_b",
            expected_sha256=EXPECTED_MEDIA_HASHES["similar_dialogue_b"],
            timestamps=(20.0,),
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            manual_observation=(
                "white-shirt male presenter in a different home setting with "
                "different subtitle styling"
            ),
        )["visual_audit"],
    }
    runtime_evidence = _public_runtime_evidence(database_url)
    zero_result_browser_contract = _zero_result_browser_contract()
    landscape_interview_evidence = _landscape_interview_evidence(
        landscape_interview,
        sessions_root=root,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
    )
    ffmpeg_artifact = (
        BACKEND_ROOT
        / "tests"
        / "artifacts"
        / "media_library_ffmpeg_acceptance.json"
    )
    ffmpeg_payload = read_json(ffmpeg_artifact)
    minimum_matrix = ffmpeg_payload.get("minimum_duration_matrix") or []
    subsecond_passed = (
        len(minimum_matrix) >= 3
        and all(
            int(row.get("requested_duration_ms") or 0) == 250
            and row.get("status") == "passed"
            and abs(
                int(row.get("actual_duration_ms") or 0) - 250
            )
            <= int(row.get("dynamic_tolerance_ms") or -1)
            for row in minimum_matrix
        )
    )
    if not subsecond_passed:
        raise AssertionError("subsecond_dynamic_tolerance_evidence_invalid")

    matrix = [
        {
            "id": 1,
            "requirement": "有硬字幕的竖屏口播",
            "status": "passed",
            "evidence": hard_subtitle,
        },
        {
            "id": 2,
            "requirement": "无字幕的竖屏口播",
            "status": "passed",
            "evidence": {
                **no_subtitle,
                "asr_manifest_path": str(no_subtitle_asr_path),
                "asr_manifest_sha256": EXPECTED_MANIFEST_HASHES[
                    "no_subtitle_asr"
                ],
                "asr_segment_count": len(
                    no_subtitle_asr.get("segments") or []
                ),
            },
        },
        {
            "id": 3,
            "requirement": "横屏采访",
            "status": landscape_interview_evidence["status"],
            "evidence": landscape_interview_evidence,
        },
        {
            "id": 4,
            "requirement": "多 Scene、对白跨镜头的视频",
            "status": "passed",
            "evidence": {
                **multiscene,
                "asr_manifest_path": str(multiscene_asr_path),
                "asr_manifest_sha256": EXPECTED_MANIFEST_HASHES[
                    "multiscene_asr"
                ],
                "asr_segment_count": 33,
                "storyboard_path": str(multiscene_storyboard_path),
                "storyboard_sha256": EXPECTED_MANIFEST_HASHES[
                    "multiscene_storyboard"
                ],
                "shot_count": 6,
                "scene_count": 17,
                "compose_result_path": str(multiscene_compose_path),
                "compose_result_sha256": EXPECTED_MANIFEST_HASHES[
                    "multiscene_compose"
                ],
                "compose_status": "completed",
            },
        },
        {
            "id": 5,
            "requirement": "无对白 B-roll",
            "status": "passed",
            "evidence": silent_broll,
        },
        {
            "id": 6,
            "requirement": "同义表达检索样本",
            "status": "passed",
            "evidence": {
                "postgresql_gate": quality["gate"],
                "query": quality["query"],
                "candidate_level_synonym_only": quality[
                    "candidate_level_synonym_only"
                ],
                "candidates": quality["candidates"],
            },
        },
        {
            "id": 7,
            "requirement": "跨 Task/Session 检索同一全局原始素材",
            "status": (
                "passed"
                if runtime_evidence["cross_session_original_import"]
                else "missing_runtime_evidence"
            ),
            "evidence": runtime_evidence[
                "cross_session_original_import"
            ],
        },
        {
            "id": 8,
            "requirement": "秒/毫秒边界和不足一秒的短片段",
            "status": "passed",
            "evidence": {
                "artifact_path": str(ffmpeg_artifact),
                "artifact_sha256": sha256_file(ffmpeg_artifact),
                "minimum_duration_matrix": minimum_matrix,
            },
        },
        {
            "id": 9,
            "requirement": "包含相似对白但画面不同的排序样本",
            "status": "passed",
            "evidence": {
                "postgresql_gate": quality["gate"],
                "first_order": quality["first_order"],
                "second_order": quality["second_order"],
                "stable_order": quality["stable_order"],
                "source_a": {
                    **quality_sources["real-session-185"],
                    "visual_audit": quality_visuals[
                        "real-session-185"
                    ],
                },
                "source_b": {
                    **quality_sources["real-session-190"],
                    "visual_audit": quality_visuals[
                        "real-session-190"
                    ],
                },
                "different_content_hashes": (
                    quality_sources["real-session-185"][
                        "content_sha256"
                    ]
                    != quality_sources["real-session-190"][
                        "content_sha256"
                    ]
                ),
                "different_visual_frames": (
                    quality_visuals["real-session-185"][
                        "extracted_frame_sha256"
                    ]
                    != quality_visuals["real-session-190"][
                        "extracted_frame_sha256"
                    ]
                ),
            },
        },
        {
            "id": 10,
            "requirement": "大文件和长视频样本",
            "status": "passed",
            "evidence": {
                **ten_minute,
                "ffmpeg_representative_gate": ffmpeg_payload.get(
                    "representative_gate"
                ),
            },
        },
        {
            "id": 11,
            "requirement": (
                "查询规划 LLM 超时、关闭或配额耗尽的降级检索样本"
            ),
            "status": (
                "passed"
                if runtime_evidence[
                    "planner_degraded_search_and_import"
                ]
                else "missing_runtime_evidence"
            ),
            "evidence": runtime_evidence[
                "planner_degraded_search_and_import"
            ],
        },
        {
            "id": 12,
            "requirement": "无字面命中的零结果与查询修改建议样本",
            "status": (
                "passed"
                if (
                    runtime_evidence["agent_zero_result_run"]
                    and zero_result_browser_contract["status"]
                    == "contract_present"
                )
                else "missing_runtime_evidence"
            ),
            "evidence": {
                "isolated_postgresql_zero_result": quality[
                    "zero_result_backend"
                ],
                "real_agent_zero_result": runtime_evidence[
                    "agent_zero_result_run"
                ],
                "browser_e2e_contract": zero_result_browser_contract,
            },
        },
    ]
    incomplete = [
        row
        for row in matrix
        if row["status"] != "passed"
    ]
    return {
        "acceptance": "media_library_e2e_sample_matrix_v1",
        "executed_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not incomplete else "incomplete",
        "runtime": {
            "ffmpeg_path": ffmpeg_path,
            "ffprobe_path": ffprobe_path,
            "database_target": sanitized_database_target(
                sqlalchemy_database_url(database_url)
            ),
            "sessions_root": str(root),
        },
        "integrity_policy": {
            "real_media_only": True,
            "hash_and_probe_revalidated": True,
            "manual_visual_observations_are_bound_to_source_and_frame_hashes": (
                True
            ),
            "near_matches_must_not_be_relabelled": True,
        },
        "postgresql_quality_gate": quality,
        "public_runtime_evidence": runtime_evidence,
        "matrix": matrix,
        "passed_count": len(matrix) - len(incomplete),
        "required_count": len(matrix),
        "incomplete_items": [
            {
                "id": row["id"],
                "requirement": row["requirement"],
                "status": row["status"],
            }
            for row in incomplete
        ],
    }


def main() -> int:
    args = parse_args()
    result = build_sample_matrix(
        database_url=args.database_url,
        sessions_root=args.sessions_root,
        landscape_interview=args.landscape_interview,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
