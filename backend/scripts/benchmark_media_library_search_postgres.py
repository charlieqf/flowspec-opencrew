from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine, make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_analysis_runs,
    media_library_assets,
    media_library_clip_derivatives,
    media_library_fragment_index,
    media_library_search_runs,
    media_library_tasks,
    metadata,
    session_files,
    sessions,
)
from opcrew_backend.media_library_search import (  # noqa: E402
    MediaLibrarySearchPlanner,
    MediaLibrarySearchRepository,
    MediaLibrarySearchService,
    RETRIEVAL_VERSION,
)
from opcrew_backend.media_library_search.normalization import (  # noqa: E402
    NORMALIZATION_VERSION,
    normalized_search_text,
)
from opcrew_backend.media_library_search.schemas import (  # noqa: E402
    MediaLibraryQueryPlanV1,
)
from opcrew_backend.media_library_features import (  # noqa: E402
    media_library_feature_state,
)


DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
)
DEFAULT_SAMPLE_ROOT = Path(
    os.environ.get(
        "OPENCREW_MEDIA_SEARCH_BENCHMARK_SAMPLE_ROOT",
        str(Path.home() / ".opencrew" / "sessions"),
    )
)
SAMPLE_DISCOVERY_PATTERN = (
    "*/workspace/SessionOutput/subtitle/final_srt_frame_items.json"
)
VIDEO_COUNT = 500
R2_CLIP_COUNT = 2_000
P95_GATE_MS = 3000.0
MIN_WARMUP_ITERATIONS = 20
MIN_MEASURED_ITERATIONS = 200
MIN_REAL_SAMPLE_FILES = 5
MIN_REAL_SAMPLE_FRAGMENTS = 20
PLANNER_MODE = "enabled_precached_normal_plan_v1"
PLANNER_VERSION = "benchmark_precached_query_plan_v1"
QUERY_DISTRIBUTION = (
    "防水能力",
    "夜间拍摄",
    "产品开箱",
    "户外露营",
    "人物采访",
)
VISUAL_QUERY_DISTRIBUTION = (
    "玻璃碗",
    "深色液体",
    "绿色包装",
)
BROAD_QUERY = "真实使用场景"
ZERO_RESULT_QUERY = "量子帆船术语零命中"
BENCHMARK_QUERIES = (
    *QUERY_DISTRIBUTION,
    *VISUAL_QUERY_DISTRIBUTION,
    BROAD_QUERY,
    ZERO_RESULT_QUERY,
)
QUERY_OPTIONAL_TERMS = {
    "防水能力": ["IP68", "防护", "进水"],
    "夜间拍摄": ["低光", "夜景", "暗光"],
    "产品开箱": ["包装", "配件", "首次使用"],
    "户外露营": ["帐篷", "户外", "营地"],
    "人物采访": ["访谈", "讲解", "同期声"],
    "玻璃碗": ["透明碗", "碗", "透明容器"],
    "深色液体": ["深红色液体", "液体", "深色"],
    "绿色包装": ["绿色包装盒", "包装", "绿色"],
    BROAD_QUERY: ["现场", "实测", "体验"],
    ZERO_RESULT_QUERY: ["不存在候选"],
}
BENCHMARK_TABLES = (
    sessions,
    session_files,
    media_library_assets,
    media_library_tasks,
    media_library_analysis_runs,
    media_library_fragment_index,
    media_library_clip_derivatives,
    media_library_search_runs,
)


@dataclass(frozen=True)
class WorkspaceSampleDistribution:
    fragment_counts: tuple[int, ...]
    text_lengths: tuple[int, ...]
    audit: dict[str, Any]


class PrecachedBenchmarkPlanner:
    """Deterministic normal planner whose measured calls are cache hits."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(payload: Mapping[str, Any]) -> str:
        key_payload = {
            "query": str(payload.get("original_query") or "").strip(),
            "orientation": str(payload.get("orientation") or "any"),
            "min_duration_ms": payload.get("min_duration_ms"),
            "max_duration_ms": payload.get("max_duration_ms"),
            "sources": list(payload.get("sources") or ["media_library"]),
        }
        return json.dumps(
            key_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _build(payload: Mapping[str, Any]) -> dict[str, Any]:
        query = str(payload.get("original_query") or "").strip()
        return MediaLibraryQueryPlanV1(
            original_query=query,
            exact_phrases=[query],
            optional_terms=QUERY_OPTIONAL_TERMS.get(query, []),
            negative_terms=[],
            orientation=str(payload.get("orientation") or "any"),
            min_duration_ms=payload.get("min_duration_ms"),
            max_duration_ms=payload.get("max_duration_ms"),
            sources=list(
                payload.get("sources") or ["media_library"]
            ),
            planner_version=PLANNER_VERSION,
        ).model_dump()

    def __call__(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        key = self._key(payload)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return dict(cached)
        self.misses += 1
        created = self._build(payload)
        self._cache[key] = created
        return dict(created)

    def plans(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._cache.values()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the M1 500-video PostgreSQL search release gate in a "
            "random isolated schema. The schema is always removed."
        )
    )
    parser.add_argument(
        "--eligible-clips",
        type=int,
        default=0,
        help=(
            "Number of explicit metadata-only search-eligible derived clips "
            f"to seed (R2 release gate requires {R2_CLIP_COUNT})."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
    )
    parser.add_argument(
        "--sample-root",
        type=Path,
        default=DEFAULT_SAMPLE_ROOT,
        help=(
            "Root containing real */workspace/SessionOutput/subtitle/"
            "final_srt_frame_items.json analysis results."
        ),
    )
    parser.add_argument(
        "--warmup",
        "--warmups",
        dest="warmups",
        type=int,
        default=MIN_WARMUP_ITERATIONS,
        help=(
            "Unmeasured cached-plan searches "
            f"(minimum {MIN_WARMUP_ITERATIONS})."
        ),
    )
    parser.add_argument(
        "--queries",
        "--iterations",
        dest="iterations",
        type=int,
        default=MIN_MEASURED_ITERATIONS,
        help=(
            "Measured cached-plan searches "
            f"(minimum {MIN_MEASURED_ITERATIONS})."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            BACKEND_ROOT
            / "tests"
            / "artifacts"
            / "media_library_search_postgres_benchmark.json"
        ),
    )
    parser.add_argument(
        "--contract-output",
        type=Path,
        default=None,
        help="Optional second JSON path used by the committed contract test.",
    )
    return parser.parse_args()


def sqlalchemy_database_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def nearest_rank_percentile(
    samples: list[float] | tuple[int, ...],
    percentile: float,
) -> float:
    if not samples:
        raise ValueError("percentile_samples_empty")
    if not 0 < percentile <= 1:
        raise ValueError("percentile_out_of_range")
    ordered = sorted(float(value) for value in samples)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def distribution_summary(
    samples: list[float] | tuple[int, ...],
    *,
    integral: bool = False,
) -> dict[str, Any]:
    if not samples:
        raise ValueError("distribution_samples_empty")
    values = [float(value) for value in samples]

    def display(value: float) -> int | float:
        return int(round(value)) if integral else round(value, 3)

    return {
        "sample_count": len(values),
        "minimum": display(min(values)),
        "maximum": display(max(values)),
        "average": round(sum(values) / len(values), 3),
        "p50": display(nearest_rank_percentile(values, 0.50)),
        "p95": display(nearest_rank_percentile(values, 0.95)),
        "p99": display(nearest_rank_percentile(values, 0.99)),
    }


def isolated_schema_name() -> str:
    return f"oc_mlsearch_bench_{uuid.uuid4().hex}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dialogue_text(item: Mapping[str, Any]) -> str:
    return str(
        item.get("dialogue")
        or item.get("dialogue_text")
        or item.get("text")
        or item.get("subtitle")
        or ""
    ).strip()


def load_workspace_sample_distribution(
    sample_root: Path,
) -> WorkspaceSampleDistribution:
    root = sample_root.expanduser().resolve()
    paths = sorted(root.glob(SAMPLE_DISCOVERY_PATTERN))
    direct = (
        root / "SessionOutput" / "subtitle" / "final_srt_frame_items.json"
    )
    if direct.is_file():
        paths.insert(0, direct)
    fragment_counts: list[int] = []
    text_lengths: list[int] = []
    digest = hashlib.sha256()
    valid_files = 0
    for path in dict.fromkeys(paths):
        try:
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        texts = [
            _dialogue_text(item)
            for item in items
            if isinstance(item, Mapping)
        ]
        texts = [value for value in texts if value]
        if not texts:
            continue
        valid_files += 1
        fragment_counts.append(len(texts))
        text_lengths.extend(len(value) for value in texts)
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(raw).digest())
    if valid_files < MIN_REAL_SAMPLE_FILES:
        raise ValueError(
            "benchmark_real_sample_files_insufficient:"
            f"{valid_files}<{MIN_REAL_SAMPLE_FILES}"
        )
    if len(text_lengths) < MIN_REAL_SAMPLE_FRAGMENTS:
        raise ValueError(
            "benchmark_real_sample_fragments_insufficient:"
            f"{len(text_lengths)}<{MIN_REAL_SAMPLE_FRAGMENTS}"
        )
    audit = {
        "source_kind": "workspace_analysis_results",
        "source_locator": (
            "configured-root/"
            "*/workspace/SessionOutput/subtitle/"
            "final_srt_frame_items.json"
        ),
        "discovery_pattern": SAMPLE_DISCOVERY_PATTERN,
        "source_file_count": valid_files,
        "source_fragment_count": len(text_lengths),
        "source_snapshot_sha256": digest.hexdigest(),
        "fragment_count_per_video": distribution_summary(
            fragment_counts, integral=True
        ),
        "text_length_chars": distribution_summary(
            text_lengths, integral=True
        ),
    }
    return WorkspaceSampleDistribution(
        fragment_counts=tuple(fragment_counts),
        text_lengths=tuple(text_lengths),
        audit=audit,
    )


def _empirical_values(values: tuple[int, ...], count: int) -> list[int]:
    if not values or count <= 0:
        raise ValueError("empirical_distribution_invalid")
    ordered = sorted(int(value) for value in values)
    expanded = [
        ordered[min(len(ordered) - 1, index * len(ordered) // count)]
        for index in range(count)
    ]
    stride = 97
    while math.gcd(stride, count) != 1:
        stride += 2
    return [expanded[(index * stride) % count] for index in range(count)]


def _synthetic_dialogue(
    *,
    target_length: int,
    prefix: str,
    ordinal: int,
    fragment_index: int,
) -> str:
    minimum = max(1, int(target_length), len(prefix))
    filler = (
        f"{prefix} 4K A{ordinal % 97} 12.5mm USB-C "
        f"第{fragment_index + 1}段 现场讲解与细节展示 "
    )
    repeated = (filler * (minimum // max(1, len(filler)) + 2))[:minimum]
    return repeated


def seed_representative_distribution(
    engine: Engine,
    *,
    sample_distribution: WorkspaceSampleDistribution,
    eligible_clip_count: int = 0,
    timestamp_ms: int = 1_800_000_000_000,
) -> dict[str, Any]:
    """Seed 500 videos with representative dialogue and visual fragments."""

    session_rows: list[dict[str, Any]] = []
    asset_rows: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    fragment_rows: list[dict[str, Any]] = []
    session_file_rows: list[dict[str, Any]] = []
    clip_rows: list[dict[str, Any]] = []
    duration_buckets = (60_000, 180_000, 300_000, 600_000, 1_200_000)
    per_video_counts = _empirical_values(
        sample_distribution.fragment_counts, VIDEO_COUNT
    )
    total_fragments = sum(per_video_counts)
    text_lengths = _empirical_values(
        sample_distribution.text_lengths, total_fragments
    )
    global_fragment_index = 0

    for index, fragment_count in enumerate(per_video_counts):
        ordinal = index + 1
        session_id = ordinal
        asset_id = f"asset-bench-{ordinal:04d}"
        run_id = f"mlar-dialogue-bench-{ordinal:04d}"
        structure_run_id = f"mlar-visual-structure-bench-{ordinal:04d}"
        visual_run_id = f"mlar-visual-semantic-bench-{ordinal:04d}"
        source_version = _sha256(f"benchmark-source-{ordinal}")
        result_hash = _sha256(f"benchmark-result-{ordinal}")
        structure_result_hash = _sha256(
            f"benchmark-visual-structure-result-{ordinal}"
        )
        visual_result_hash = _sha256(
            f"benchmark-visual-semantic-result-{ordinal}"
        )
        phrase = QUERY_DISTRIBUTION[index % len(QUERY_DISTRIBUTION)]
        visual_phrase = VISUAL_QUERY_DISTRIBUTION[
            index % len(VISUAL_QUERY_DISTRIBUTION)
        ]
        visual_fragment_count = 1 + index % 4
        portrait = index % 2 == 1
        width, height = ((1080, 1920) if portrait else (1920, 1080))
        required_duration = fragment_count * 1_000 + 1_000
        requested_duration = max(
            duration_buckets[index % len(duration_buckets)],
            required_duration,
        )
        duration_ms = next(
            (
                bucket
                for bucket in duration_buckets
                if bucket >= requested_duration
            ),
            duration_buckets[-1],
        )
        row_timestamp = timestamp_ms + index
        session_rows.append(
            {
                "id": session_id,
                "source": "media-library-benchmark",
                "group_id": "media-library-benchmark",
                "title": f"隔离检索基准素材 {ordinal:04d}",
                "status": "draft",
                "workspace_dir": f"/isolated/benchmark/{asset_id}",
                "created_at": row_timestamp,
                "updated_at": row_timestamp,
            }
        )
        asset_rows.append(
            {
                "asset_id": asset_id,
                "session_id": session_id,
                "display_name": f"{phrase}代表素材 {ordinal:04d}",
                "original_filename": f"{asset_id}.mp4",
                "source_video_path": f"inbox/{asset_id}.mp4",
                "content_sha256": source_version,
                "content_hashed_at": row_timestamp,
                "media_type": "video",
                "duration_ms": duration_ms,
                "width": width,
                "height": height,
                "format": "mp4",
                "size_bytes": 1_000_000 + ordinal,
                "language": "zh-CN",
                "upload_status": "ready",
                "analysis_status": "partial",
                "subtitle_mode": "generated",
                "analysis_summary_json": {
                    "dialogue_fragment_count": fragment_count,
                    "visual_fragment_count": visual_fragment_count,
                    "visual_semantic_fragment_count": visual_fragment_count,
                },
                "tags_json": [
                    phrase,
                    "竖屏" if portrait else "横屏",
                    f"时长档-{duration_buckets.index(duration_ms)}",
                ],
                "archived": False,
                "referenced_by_count": index % 7,
                "created_at": row_timestamp,
                "updated_at": row_timestamp,
            }
        )
        task_rows.append(
            {
                "asset_id": asset_id,
                "session_id": session_id,
                "title": f"{phrase}代表素材 {ordinal:04d}",
                "status": "draft",
                "dialogue_status": "ready",
                "dialogue_current_run_id": run_id,
                "visual_status": "ready",
                "visual_structure_status": "ready",
                "visual_structure_current_run_id": structure_run_id,
                "visual_semantic_status": "ready",
                "visual_semantic_current_run_id": visual_run_id,
                "composite_status": "not_analyzed",
                "created_at": row_timestamp,
                "updated_at": row_timestamp,
            }
        )
        run_rows.extend(
            [
              {
                "analysis_run_id": run_id,
                "asset_id": asset_id,
                "scheme": "dialogue",
                "source_version": source_version,
                "status": "ready",
                "schema_version": "media_library_dialogue_fragments_v1",
                "result_hash": result_hash,
                "result_index_path": (
                    "SessionOutput/json/dialogue_fragment_index.json"
                ),
                "upstream_refs_json": {},
                "progress_json": {"stage": "completed", "percent": 100},
                "is_current": True,
                "started_at": row_timestamp,
                "finished_at": row_timestamp + 1,
                "created_at": row_timestamp,
                "updated_at": row_timestamp + 1,
              },
              {
                "analysis_run_id": structure_run_id,
                "asset_id": asset_id,
                "scheme": "visual_structure",
                "source_version": source_version,
                "status": "ready",
                "schema_version": "media_library_visual_structure_v2",
                "result_hash": structure_result_hash,
                "result_index_path": (
                    "SessionOutput/visual/visual_structure_segments.json"
                ),
                "upstream_refs_json": {},
                "progress_json": {
                    "stage": "completed",
                    "percent": 100,
                    "sampling_strategy": "scene_uniform_4_v1",
                },
                "is_current": True,
                "started_at": row_timestamp,
                "finished_at": row_timestamp + 1,
                "created_at": row_timestamp,
                "updated_at": row_timestamp + 1,
              },
              {
                "analysis_run_id": visual_run_id,
                "asset_id": asset_id,
                "scheme": "visual_semantic",
                "source_version": source_version,
                "status": "ready",
                "schema_version": "media_library_visual_semantic_v2",
                "result_hash": visual_result_hash,
                "result_index_path": (
                    "SessionOutput/visual/visual_semantic_segments.json"
                ),
                "upstream_refs_json": {
                    "visual_structure_run_id": structure_run_id,
                    "visual_structure_result_hash": structure_result_hash,
                    "sampling_strategy": "scene_uniform_4_v1",
                },
                "progress_json": {
                    "stage": "completed",
                    "percent": 100,
                    "sampling_strategy": "scene_uniform_4_v1",
                },
                "is_current": True,
                "started_at": row_timestamp,
                "finished_at": row_timestamp + 1,
                "created_at": row_timestamp,
                "updated_at": row_timestamp + 1,
              },
            ]
        )
        previous_dialogue = ""
        for fragment_index in range(fragment_count):
            target_length = text_lengths[global_fragment_index]
            global_fragment_index += 1
            if fragment_index == 0:
                prefix = phrase
            elif fragment_index == 1:
                prefix = BROAD_QUERY
            elif fragment_index % 5 == 0:
                prefix = "4K"
            else:
                prefix = "A1"
            dialogue_text = _synthetic_dialogue(
                target_length=target_length,
                prefix=prefix,
                ordinal=ordinal,
                fragment_index=fragment_index,
            )
            if fragment_index > 1 and fragment_index % 17 == 0:
                dialogue_text = previous_dialogue
            previous_dialogue = dialogue_text
            start_ms = fragment_index * 1_000
            fragment_rows.append(
                {
                    "asset_id": asset_id,
                    "source_session_id": session_id,
                    "source_version": source_version,
                    "analysis_scheme": "dialogue",
                    "analysis_run_id": run_id,
                    "result_hash": result_hash,
                    "fragment_id": f"srt_{fragment_index + 1:04d}",
                    "start_ms": start_ms,
                    "end_ms": start_ms + 800,
                    "dialogue_text": dialogue_text,
                    "title": f"{phrase}片段",
                    "summary": "真实样本分布生成的隔离性能门禁片段",
                    "keywords_json": [phrase, "代表素材"],
                    "visual_labels_json": [],
                    "keyframe_ref_json": {
                        "path": (
                            "SessionOutput/keyframes/"
                            f"srt_{fragment_index + 1:04d}.jpg"
                        )
                    },
                    "search_text": normalized_search_text(
                        dialogue_text,
                        f"{phrase}片段",
                        "真实样本分布生成的隔离性能门禁片段",
                        [phrase, "代表素材"],
                    ),
                    "search_lexemes_text": None,
                    "tokenizer_name": "none",
                    "tokenizer_version": "none",
                    "dictionary_hash": None,
                    "normalization_version": NORMALIZATION_VERSION,
                    "quality_status": "ready",
                    "confidence": 0.9,
                    "is_active": True,
                    "created_at": row_timestamp,
                    "updated_at": row_timestamp,
                }
            )

        for fragment_index in range(visual_fragment_count):
            fragment_id = f"scene_{fragment_index + 1:04d}"
            start_ms = fragment_index * 15_000
            end_ms = min(duration_ms, start_ms + 15_000)
            if end_ms <= start_ms:
                raise AssertionError("benchmark_visual_range_invalid")
            summary = (
                f"{visual_phrase}出现在真实使用场景中；四帧稀疏证据只描述"
                "可见对象、场景和静态状态，不宣称连续动作。"
            )
            visual_labels = [
                visual_phrase,
                "透明纹理碗" if visual_phrase == "玻璃碗" else "产品包装",
                "深红色液体" if visual_phrase == "深色液体" else "桌面场景",
            ]
            keywords = [visual_phrase, "四帧画面", "真实使用场景"]
            fragment_rows.append(
                {
                    "asset_id": asset_id,
                    "source_session_id": session_id,
                    "source_version": source_version,
                    "analysis_scheme": "visual_semantic",
                    "analysis_run_id": visual_run_id,
                    "result_hash": visual_result_hash,
                    "fragment_id": fragment_id,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "dialogue_text": None,
                    "title": None,
                    "summary": summary,
                    "keywords_json": keywords,
                    "visual_labels_json": visual_labels,
                    "keyframe_ref_json": [
                        f"{fragment_id}-sample-{slot:02d}"
                        for slot in range(1, 5)
                    ],
                    "search_text": normalized_search_text(
                        summary,
                        keywords,
                        visual_labels,
                    ),
                    "search_lexemes_text": None,
                    "tokenizer_name": "none",
                    "tokenizer_version": "none",
                    "dictionary_hash": None,
                    "normalization_version": NORMALIZATION_VERSION,
                    "quality_status": "ready",
                    "confidence": 0.9,
                    "is_active": True,
                    "created_at": row_timestamp,
                    "updated_at": row_timestamp,
                }
            )

    if eligible_clip_count < 0:
        raise ValueError("benchmark_eligible_clip_count_negative")
    clip_topics = (
        *VISUAL_QUERY_DISTRIBUTION,
        "产品开箱",
        "户外露营",
    )
    for index in range(eligible_clip_count):
        ordinal = index + 1
        parent_index = index % VIDEO_COUNT
        parent_ordinal = parent_index + 1
        session_id = parent_ordinal
        source_asset_id = f"asset-bench-{parent_ordinal:04d}"
        source_version = _sha256(
            f"benchmark-source-{parent_ordinal}"
        )
        topic = clip_topics[index % len(clip_topics)]
        duration_ms = 3_000 + (index % 5) * 1_000
        source_start_ms = (index % 8) * 1_000
        source_end_ms = source_start_ms + duration_ms
        output_path = (
            "SessionOutput/media_library/clips/"
            f"clip-bench-{ordinal:04d}.mp4"
        )
        timestamp = timestamp_ms + VIDEO_COUNT + index
        tags = [
            topic,
            "人工标签",
            "竖屏" if parent_index % 2 else "横屏",
        ]
        display_name = f"{topic}可复用片段 {ordinal:04d}"
        session_file_rows.append(
            {
                "session_id": session_id,
                "path": output_path,
                "kind": "video",
                "size": 250_000 + ordinal,
                "origin": "media_library_clip",
                "downloadable": 1,
                "visibility": "session",
                "sensitivity": "normal",
                "attempt_id": None,
                "tool_use_session_id": None,
                "stale": 0,
                "updated_at": timestamp,
            }
        )
        clip_rows.append(
            {
                "clip_id": f"clip-bench-{ordinal:04d}",
                "idempotency_key": f"clip-bench-idem-{ordinal:04d}",
                "source_asset_id": source_asset_id,
                "source_session_id": session_id,
                "source_version": source_version,
                "source_start_ms": source_start_ms,
                "source_end_ms": source_end_ms,
                "source_scheme": None,
                "source_fragment_id": None,
                "source_analysis_run_id": None,
                "source_search_id": None,
                "source_dialogue_asset_key": None,
                "output_path": output_path,
                "display_name": display_name,
                "duration_ms": duration_ms,
                "content_sha256": _sha256(
                    f"benchmark-clip-content-{ordinal}"
                ),
                "size_bytes": 250_000 + ordinal,
                "operation": "precise_reencode_v1",
                "search_eligible": True,
                "tags_json": tags,
                "search_text": normalized_search_text(
                    display_name, tags
                ),
                "search_normalization_version": NORMALIZATION_VERSION,
                "search_enabled_at": timestamp,
                "search_updated_at": timestamp,
                "created_at": timestamp,
            }
        )

    with engine.begin() as conn:
        conn.execute(sessions.insert(), session_rows)
        conn.execute(media_library_assets.insert(), asset_rows)
        conn.execute(media_library_tasks.insert(), task_rows)
        conn.execute(media_library_analysis_runs.insert(), run_rows)
        conn.execute(media_library_fragment_index.insert(), fragment_rows)
        if session_file_rows:
            conn.execute(session_files.insert(), session_file_rows)
            conn.execute(media_library_clip_derivatives.insert(), clip_rows)
        if conn.dialect.name == "postgresql":
            for table in BENCHMARK_TABLES:
                conn.execute(text(f'ANALYZE "{table.name}"'))

    capacity = collect_capacity_metrics(engine)
    capacity["dataset_seed"] = {
        "generator_version": "real_workspace_distribution_v1",
        "source_sample": sample_distribution.audit,
        "generated_fragment_count_per_video": capacity[
            "dialogue_fragment_count_per_video"
        ],
        "generated_visual_fragment_count_per_video": capacity[
            "visual_fragment_count_per_video"
        ],
        "generated_text_length_chars": capacity["text_length_chars"],
    }
    return capacity


def collect_capacity_metrics(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        video_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_assets)
                .where(
                    media_library_assets.c.media_type == "video",
                    media_library_assets.c.upload_status == "ready",
                    media_library_assets.c.archived.is_(False),
                )
            )
            or 0
        )
        active_dialogue_fragment_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_fragment_index)
                .where(
                    media_library_fragment_index.c.is_active.is_(True),
                    media_library_fragment_index.c.analysis_scheme
                    == "dialogue",
                )
            )
            or 0
        )
        active_visual_fragment_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_fragment_index)
                .where(
                    media_library_fragment_index.c.is_active.is_(True),
                    media_library_fragment_index.c.analysis_scheme
                    == "visual_semantic",
                )
            )
            or 0
        )
        current_dialogue_run_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.scheme == "dialogue",
                    media_library_analysis_runs.c.status == "ready",
                    media_library_analysis_runs.c.is_current.is_(True),
                )
            )
            or 0
        )
        current_visual_run_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.scheme
                    == "visual_semantic",
                    media_library_analysis_runs.c.status == "ready",
                    media_library_analysis_runs.c.is_current.is_(True),
                    media_library_analysis_runs.c.schema_version
                    == "media_library_visual_semantic_v2",
                )
            )
            or 0
        )
        current_structure_run_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.scheme
                    == "visual_structure",
                    media_library_analysis_runs.c.status == "ready",
                    media_library_analysis_runs.c.is_current.is_(True),
                    media_library_analysis_runs.c.schema_version
                    == "media_library_visual_structure_v2",
                )
            )
            or 0
        )
        ready_dialogue_task_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_tasks)
                .where(media_library_tasks.c.dialogue_status == "ready")
            )
            or 0
        )
        ready_visual_task_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_tasks)
                .where(
                    media_library_tasks.c.visual_structure_status
                    == "ready",
                    media_library_tasks.c.visual_semantic_status
                    == "ready",
                )
            )
            or 0
        )
        portrait_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_assets)
                .where(
                    media_library_assets.c.upload_status == "ready",
                    media_library_assets.c.height
                    > media_library_assets.c.width,
                )
            )
            or 0
        )
        duration_bucket_count = int(
            conn.scalar(
                select(
                    func.count(
                        func.distinct(media_library_assets.c.duration_ms)
                    )
                )
            )
            or 0
        )
        dialogue_fragment_counts = [
            int(row[0])
            for row in conn.execute(
                select(func.count())
                .select_from(media_library_fragment_index)
                .where(
                    media_library_fragment_index.c.is_active.is_(True),
                    media_library_fragment_index.c.analysis_scheme
                    == "dialogue",
                )
                .group_by(media_library_fragment_index.c.asset_id)
            ).fetchall()
        ]
        visual_fragment_counts = [
            int(row[0])
            for row in conn.execute(
                select(func.count())
                .select_from(media_library_fragment_index)
                .where(
                    media_library_fragment_index.c.is_active.is_(True),
                    media_library_fragment_index.c.analysis_scheme
                    == "visual_semantic",
                )
                .group_by(media_library_fragment_index.c.asset_id)
            ).fetchall()
        ]
        text_lengths = [
            int(row[0])
            for row in conn.execute(
                select(func.length(media_library_fragment_index.c.dialogue_text))
                .where(
                    media_library_fragment_index.c.is_active.is_(True),
                    media_library_fragment_index.c.analysis_scheme
                    == "dialogue",
                )
            ).fetchall()
            if row[0] is not None
        ]
        distinct_dialogues = int(
            conn.scalar(
                select(
                    func.count(
                        func.distinct(
                            media_library_fragment_index.c.dialogue_text
                        )
                    )
                ).where(
                    media_library_fragment_index.c.is_active.is_(True),
                    media_library_fragment_index.c.analysis_scheme
                    == "dialogue",
                )
            )
            or 0
        )
        search_eligible_clip_count = int(
            conn.scalar(
                select(func.count())
                .select_from(media_library_clip_derivatives)
                .where(
                    media_library_clip_derivatives.c.search_eligible.is_(
                        True
                    )
                )
            )
            or 0
        )
    return {
        "ready_original_video_count": video_count,
        "search_eligible_clip_count": search_eligible_clip_count,
        "active_dialogue_fragment_count": active_dialogue_fragment_count,
        "active_visual_semantic_fragment_count": active_visual_fragment_count,
        "current_ready_dialogue_run_count": current_dialogue_run_count,
        "current_ready_visual_structure_run_count": current_structure_run_count,
        "current_ready_visual_semantic_run_count": current_visual_run_count,
        "ready_dialogue_task_count": ready_dialogue_task_count,
        "ready_visual_task_count": ready_visual_task_count,
        "portrait_video_count": portrait_count,
        "landscape_video_count": video_count - portrait_count,
        "duration_bucket_count": duration_bucket_count,
        "dialogue_topic_count": len(QUERY_DISTRIBUTION),
        "visual_topic_count": len(VISUAL_QUERY_DISTRIBUTION),
        "dialogue_fragment_count_per_video": distribution_summary(
            dialogue_fragment_counts, integral=True
        ),
        # Compatibility alias retained for existing report consumers. It is
        # explicitly the dialogue distribution, not dialogue+visual combined.
        "fragment_count_per_video": distribution_summary(
            dialogue_fragment_counts, integral=True
        ),
        "visual_fragment_count_per_video": distribution_summary(
            visual_fragment_counts, integral=True
        ),
        "text_length_chars": distribution_summary(
            text_lengths, integral=True
        ),
        "repeated_dialogue_fragment_count": (
            active_dialogue_fragment_count - distinct_dialogues
        ),
        "repository_max_recalled_fragments": (
            MediaLibrarySearchRepository.MAX_RECALLED_FRAGMENTS
        ),
        "repository_max_fragments_per_asset": (
            MediaLibrarySearchRepository.MAX_FRAGMENTS_PER_ASSET
        ),
    }


def assert_representative_capacity(
    capacity: dict[str, Any], *, eligible_clip_count: int = 0
) -> None:
    expected = {
        "ready_original_video_count": VIDEO_COUNT,
        "current_ready_dialogue_run_count": VIDEO_COUNT,
        "current_ready_visual_structure_run_count": VIDEO_COUNT,
        "current_ready_visual_semantic_run_count": VIDEO_COUNT,
        "ready_dialogue_task_count": VIDEO_COUNT,
        "ready_visual_task_count": VIDEO_COUNT,
        "portrait_video_count": VIDEO_COUNT // 2,
        "landscape_video_count": VIDEO_COUNT // 2,
        "search_eligible_clip_count": eligible_clip_count,
    }
    mismatches = {
        key: {"expected": value, "actual": capacity.get(key)}
        for key, value in expected.items()
        if capacity.get(key) != value
    }
    generated_counts = (
        capacity.get("dialogue_fragment_count_per_video") or {}
    )
    source_counts = (
        (capacity.get("dataset_seed") or {})
        .get("source_sample", {})
        .get("fragment_count_per_video", {})
    )
    if (
        int(capacity.get("active_dialogue_fragment_count") or 0)
        <= VIDEO_COUNT
        or generated_counts.get("minimum") == generated_counts.get("maximum")
        or generated_counts.get("p50") != source_counts.get("p50")
        or generated_counts.get("p95") != source_counts.get("p95")
    ):
        mismatches["fragment_distribution"] = {
            "source": source_counts,
            "generated": generated_counts,
        }
    visual_counts = capacity.get("visual_fragment_count_per_video") or {}
    if (
        int(capacity.get("active_visual_semantic_fragment_count") or 0)
        != 1_250
        or visual_counts.get("minimum") != 1
        or visual_counts.get("maximum") != 4
    ):
        mismatches["visual_fragment_distribution"] = visual_counts
    if mismatches:
        raise AssertionError(f"representative_capacity_mismatch:{mismatches}")


async def _prime_planner(
    planner: MediaLibrarySearchPlanner,
) -> tuple[list[float], list[dict[str, Any]]]:
    cold_wall_samples: list[float] = []
    outcomes: list[dict[str, Any]] = []
    for query in BENCHMARK_QUERIES:
        started = time.perf_counter()
        outcome = await planner.plan(
            query,
            orientation="any",
            sources=["media_library"],
        )
        cold_wall_samples.append(
            (time.perf_counter() - started) * 1000.0
        )
        if outcome.degraded:
            raise AssertionError("benchmark_planner_cold_call_degraded")
        outcomes.append(
            {
                "query": query,
                "planner_latency_ms": outcome.latency_ms,
                "plan": outcome.plan.model_dump(),
            }
        )
    return cold_wall_samples, outcomes


def run_measured_searches(
    engine: Engine,
    *,
    warmups: int,
    iterations: int,
) -> dict[str, Any]:
    if warmups < MIN_WARMUP_ITERATIONS:
        raise ValueError(
            "benchmark_warmups_must_be_at_least_"
            f"{MIN_WARMUP_ITERATIONS}"
        )
    if iterations < MIN_MEASURED_ITERATIONS:
        raise ValueError(
            f"benchmark_iterations_must_be_at_least_{MIN_MEASURED_ITERATIONS}"
        )
    repository = MediaLibrarySearchRepository(engine)
    plan_cache = PrecachedBenchmarkPlanner()
    planner = MediaLibrarySearchPlanner(
        planner=plan_cache,
        enabled=True,
    )
    cold_wall_samples, cold_outcomes = asyncio.run(
        _prime_planner(planner)
    )
    if plan_cache.misses != len(BENCHMARK_QUERIES):
        raise AssertionError("benchmark_planner_cache_prime_incomplete")
    service = MediaLibrarySearchService(
        repository=repository,
        planner=planner,
    )
    samples: list[dict[str, Any]] = []
    observed_candidate_counts: dict[str, list[int]] = {
        query: [] for query in BENCHMARK_QUERIES
    }
    measured_query_counts = {
        query: 0 for query in BENCHMARK_QUERIES
    }
    total_runs = warmups + iterations
    for run_index in range(total_runs):
        query = BENCHMARK_QUERIES[run_index % len(BENCHMARK_QUERIES)]
        wall_started = time.perf_counter()
        response = service.search_sync(
            {
                "query": query,
                "entry_point": "storyboard",
                "query_source": (
                    "dialogue"
                    if query in QUERY_DISTRIBUTION
                    else "manual"
                ),
                "dialogue_query": (
                    query if query in QUERY_DISTRIBUTION else ""
                ),
                "user_query": (
                    query if query not in QUERY_DISTRIBUTION else ""
                ),
                "dialogue_asset_key": "benchmark-dialogue",
                "orientation": "any",
                "sources": ["media_library"],
                "limit": 50,
                "offset": 0,
            }
        )
        wall_total_ms = (time.perf_counter() - wall_started) * 1000.0
        telemetry = repository.get_search_run(response.search_id)
        if telemetry is None or telemetry.get("status") != "completed":
            raise AssertionError("benchmark_search_telemetry_missing")
        if bool(telemetry.get("planner_degraded")):
            raise AssertionError("benchmark_cached_planner_degraded")
        observed_candidate_counts[query].append(response.total_count)
        if run_index < warmups:
            continue
        measured_query_counts[query] += 1
        samples.append(
            {
                "iteration": run_index - warmups + 1,
                "query": query,
                "planner_latency_ms": float(
                    telemetry["planner_latency_ms"]
                ),
                "retrieval_latency_ms": float(
                    telemetry["retrieval_latency_ms"]
                ),
                "total_latency_ms": float(
                    telemetry["total_latency_ms"]
                ),
                "wall_total_latency_ms": round(wall_total_ms, 3),
                "total_candidate_count": response.total_count,
                "page_result_count": response.result_count,
                "zero_result": response.total_count == 0,
            }
        )

    planner_samples = [
        float(sample["planner_latency_ms"]) for sample in samples
    ]
    retrieval_samples = [
        float(sample["retrieval_latency_ms"]) for sample in samples
    ]
    total_samples = [
        float(sample["total_latency_ms"]) for sample in samples
    ]
    wall_samples = [
        float(sample["wall_total_latency_ms"]) for sample in samples
    ]
    retrieval_summary = distribution_summary(retrieval_samples)
    total_summary = distribution_summary(total_samples)
    gate_passed = (
        retrieval_summary["p95"] <= P95_GATE_MS
        and total_summary["p95"] <= P95_GATE_MS
    )
    cold_by_query = {
        item["query"]: item for item in cold_outcomes
    }
    top_query_plans = []
    for query in BENCHMARK_QUERIES:
        item = cold_by_query[query]
        plan = item["plan"]
        counts = observed_candidate_counts[query]
        top_query_plans.append(
            {
                "query": query,
                "query_sha256": _sha256(query),
                "exact_phrases": plan["exact_phrases"],
                "optional_terms": plan["optional_terms"],
                "planner_version": plan["planner_version"],
                "measured_query_count": measured_query_counts[query],
                "observed_candidate_count_min": min(counts),
                "observed_candidate_count_max": max(counts),
            }
        )
    zero_count = sum(bool(sample["zero_result"]) for sample in samples)
    return {
        "warmup_count": warmups,
        "query_count": iterations,
        "measured_iteration_count": iterations,
        "query_distribution": list(BENCHMARK_QUERIES),
        "dialogue_query_distribution": list(QUERY_DISTRIBUTION),
        "visual_query_distribution": list(VISUAL_QUERY_DISTRIBUTION),
        "retrieval_version": RETRIEVAL_VERSION,
        "planner_mode": PLANNER_MODE,
        "planner_cache": {
            "cold_miss_count": plan_cache.misses,
            "cached_hit_count": plan_cache.hits,
            "entry_count": len(plan_cache.plans()),
        },
        "planner_cold_wall_latency_ms": distribution_summary(
            cold_wall_samples
        ),
        "planner_cached_latency_ms": distribution_summary(planner_samples),
        "retrieval_latency_ms": retrieval_summary,
        "total_without_external_provider_latency_ms": total_summary,
        "wall_total_without_external_provider_latency_ms": (
            distribution_summary(wall_samples)
        ),
        "zero_result_count": zero_count,
        "zero_result_rate": round(zero_count / len(samples), 6),
        "top_query_plans": top_query_plans,
        "observed_candidate_count_range": {
            query: {
                "minimum": min(counts),
                "maximum": max(counts),
            }
            for query, counts in observed_candidate_counts.items()
        },
        "gate": {
            "threshold_p95_ms": P95_GATE_MS,
            "retrieval_passed": (
                retrieval_summary["p95"] <= P95_GATE_MS
            ),
            "total_passed": total_summary["p95"] <= P95_GATE_MS,
            "passed": gate_passed,
        },
        "samples": samples,
    }


def sanitized_database_target(database_url: str) -> dict[str, Any]:
    url = make_url(database_url)
    return {
        "driver": url.drivername,
        "host": url.host,
        "port": url.port,
        "database": url.database,
    }


def worktree_identity() -> dict[str, Any]:
    repo_root = BACKEND_ROOT.parent

    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    status = git("status", "--porcelain=v1")
    return {
        "head": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(status),
        "status_porcelain_sha256": _sha256(status),
        "status_entry_count": len(status.splitlines()) if status else 0,
    }


def _main_schema_snapshot(engine: Engine) -> dict[str, Any]:
    inspector = inspect(engine)
    counts: dict[str, int | None] = {}
    with engine.connect() as conn:
        for table in BENCHMARK_TABLES:
            if not inspector.has_table(table.name, schema="public"):
                counts[table.name] = None
                continue
            counts[table.name] = int(
                conn.scalar(
                    text(f'SELECT count(*) FROM public."{table.name}"')
                )
                or 0
            )
    encoded = json.dumps(
        counts, sort_keys=True, separators=(",", ":")
    )
    return {
        "table_count": len(counts),
        "row_count_snapshot_sha256": _sha256(encoded),
    }


def run_benchmark(
    *,
    database_url: str,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    warmups: int,
    iterations: int,
    eligible_clip_count: int = 0,
) -> dict[str, Any]:
    visual_search_state = media_library_feature_state("visual_search_v1")
    if not (
        visual_search_state.configuration_valid
        and visual_search_state.enabled
    ):
        raise ValueError("visual_search_v1_flag_required")
    if eligible_clip_count:
        clip_search_state = media_library_feature_state("clip_search_v1")
        if not (
            clip_search_state.configuration_valid
            and clip_search_state.enabled
        ):
            raise ValueError("clip_search_v1_flag_required")
    normalized_url = sqlalchemy_database_url(database_url)
    sample_distribution = load_workspace_sample_distribution(sample_root)
    schema_name = isolated_schema_name()
    base_engine = create_engine(normalized_url, pool_pre_ping=True)
    benchmark_engine: Engine | None = None
    schema_created = False
    cleanup_confirmed = False
    main_before: dict[str, Any] | None = None
    main_after: dict[str, Any] | None = None
    started = time.perf_counter()
    result: dict[str, Any] | None = None
    try:
        if base_engine.dialect.name != "postgresql":
            raise ValueError("postgresql_database_required")
        with base_engine.connect() as conn:
            database_version = str(conn.scalar(text("SELECT version()")))
        main_before = _main_schema_snapshot(base_engine)
        with base_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        schema_created = True
        benchmark_engine = create_engine(
            normalized_url,
            pool_pre_ping=True,
            connect_args={"options": f"-csearch_path={schema_name}"},
        )
        metadata.create_all(
            benchmark_engine, tables=list(BENCHMARK_TABLES)
        )
        seed_started = time.perf_counter()
        capacity = seed_representative_distribution(
            benchmark_engine,
            sample_distribution=sample_distribution,
            eligible_clip_count=eligible_clip_count,
        )
        seed_latency_ms = (time.perf_counter() - seed_started) * 1000.0
        assert_representative_capacity(
            capacity, eligible_clip_count=eligible_clip_count
        )
        measurements = run_measured_searches(
            benchmark_engine,
            warmups=warmups,
            iterations=iterations,
        )
        dataset_seed = dict(capacity.pop("dataset_seed"))
        result = {
            "benchmark": (
                "media_library_dialogue_visual_clip_literal_search_postgresql_v4"
                if eligible_clip_count
                else "media_library_dialogue_visual_literal_search_postgresql_v3"
            ),
            "executed_at": datetime.now(UTC).isoformat(),
            "database_target": sanitized_database_target(normalized_url),
            "database_version": database_version,
            "retrieval_version": RETRIEVAL_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "worktree": worktree_identity(),
            "isolation": {
                "kind": "random_schema",
                "schema_name": schema_name,
                "main_schema_data_modified": None,
                "main_schema_before": main_before,
                "main_schema_after": None,
            },
            "dataset_seed": dataset_seed,
            "dataset": capacity,
            "seed_latency_ms": round(seed_latency_ms, 3),
            "measurements": measurements,
            "cleanup_confirmed": False,
            "elapsed_ms": None,
        }
    finally:
        if benchmark_engine is not None:
            benchmark_engine.dispose()
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
            main_after = _main_schema_snapshot(base_engine)
        finally:
            base_engine.dispose()

    if result is None:
        raise RuntimeError("benchmark_result_missing")
    main_unchanged = main_before == main_after
    result["isolation"]["main_schema_after"] = main_after
    result["isolation"]["main_schema_data_modified"] = not main_unchanged
    result["cleanup_confirmed"] = cleanup_confirmed
    result["elapsed_ms"] = round(
        (time.perf_counter() - started) * 1000.0, 3
    )
    if not cleanup_confirmed:
        raise AssertionError("benchmark_schema_cleanup_failed")
    if not main_unchanged:
        raise AssertionError("benchmark_main_schema_changed")
    return result


def main() -> int:
    args = parse_args()
    result = run_benchmark(
        database_url=args.database_url,
        sample_root=args.sample_root,
        warmups=args.warmups,
        iterations=args.iterations,
        eligible_clip_count=args.eligible_clips,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.contract_output is not None:
        args.contract_output.parent.mkdir(parents=True, exist_ok=True)
        args.contract_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["measurements"]["gate"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
