from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Text, and_, asc, case, cast, delete, desc, func, or_, select, update

from ..db.schema import (
    media_library_analysis_runs,
    media_library_assets,
    media_library_fragment_index,
    media_library_tasks,
    media_library_uploads,
)
from ..media_library_features import media_library_feature_state
from .base import Repository, row_to_dict, rows_to_dicts


class MediaLibraryRepository(Repository):
    def get(self, asset_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = row_to_dict(
                conn.execute(select(media_library_assets).where(media_library_assets.c.asset_id == asset_id)).first()
            )
        if row is not None:
            row.update(
                self.visual_search_projections([asset_id]).get(
                    asset_id, {}
                )
            )
        return row

    def visual_search_projections(
        self, asset_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        ids = sorted({str(asset_id) for asset_id in asset_ids if asset_id})
        if not ids:
            return {}
        feature = media_library_feature_state("visual_search_v1")
        enabled = feature.configuration_valid and feature.enabled
        projections: dict[str, dict[str, Any]] = {
            asset_id: {
                "visual_search_ready": False,
                "visual_search_reanalysis_required": False,
                "visual_search_state": "disabled" if not enabled else "unavailable",
                "visual_search_fragment_count": 0,
                "visual_search_schema_version": None,
            }
            for asset_id in ids
        }
        current_semantic = media_library_analysis_runs.alias(
            "current_visual_semantic"
        )
        with self.engine.connect() as conn:
            current_rows = conn.execute(
                select(
                    media_library_tasks.c.asset_id,
                    media_library_tasks.c.visual_semantic_status,
                    current_semantic.c.schema_version,
                )
                .select_from(
                    media_library_tasks.outerjoin(
                        current_semantic,
                        current_semantic.c.analysis_run_id
                        == media_library_tasks.c.visual_semantic_current_run_id,
                    )
                )
                .where(media_library_tasks.c.asset_id.in_(ids))
            ).mappings().fetchall()
            indexed_rows = conn.execute(
                select(
                    media_library_fragment_index.c.asset_id,
                    media_library_fragment_index.c.fragment_id,
                    media_library_fragment_index.c.keyframe_ref_json,
                )
                .select_from(
                    media_library_fragment_index.join(
                        media_library_analysis_runs,
                        media_library_analysis_runs.c.analysis_run_id
                        == media_library_fragment_index.c.analysis_run_id,
                    ).join(
                        media_library_assets,
                        media_library_assets.c.asset_id
                        == media_library_fragment_index.c.asset_id,
                    )
                )
                .where(
                    media_library_fragment_index.c.asset_id.in_(ids),
                    media_library_fragment_index.c.analysis_scheme
                    == "visual_semantic",
                    media_library_fragment_index.c.is_active.is_(True),
                    media_library_analysis_runs.c.status == "ready",
                    media_library_analysis_runs.c.is_current.is_(True),
                    media_library_analysis_runs.c.schema_version
                    == "media_library_visual_semantic_v2",
                    media_library_fragment_index.c.source_version
                    == media_library_assets.c.content_sha256,
                )
            ).mappings().fetchall()
        valid_counts: dict[str, int] = {}
        for row in indexed_rows:
            asset_id = str(row["asset_id"])
            fragment_id = str(row["fragment_id"])
            if row["keyframe_ref_json"] != [
                f"{fragment_id}-sample-{index:02d}"
                for index in range(1, 5)
            ]:
                continue
            valid_counts[asset_id] = valid_counts.get(asset_id, 0) + 1
        for row in current_rows:
            asset_id = str(row["asset_id"])
            projection = projections[asset_id]
            schema_version = str(row.get("schema_version") or "") or None
            projection["visual_search_schema_version"] = schema_version
            semantic_ready = (
                str(row.get("visual_semantic_status") or "") == "ready"
            )
            if (
                semantic_ready
                and schema_version
                and schema_version != "media_library_visual_semantic_v2"
            ):
                projection["visual_search_reanalysis_required"] = True
                if enabled:
                    projection["visual_search_state"] = "reanalysis_required"
            elif semantic_ready and schema_version == "media_library_visual_semantic_v2" and enabled:
                projection["visual_search_state"] = "index_pending"
        for asset_id, count in valid_counts.items():
            projection = projections[asset_id]
            projection["visual_search_fragment_count"] = count
            if enabled:
                projection["visual_search_ready"] = True
                projection["visual_search_state"] = "ready"
        return projections

    def list(
        self,
        *,
        q: str = "",
        analysis_status: str = "",
        subtitle_mode: str = "",
        duration_range: str = "",
        tag: str = "",
        updated_from: int | None = None,
        orientation: str = "",
        include_archived: bool = False,
        sort: str = "updated_desc",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, list[str]]:
        conditions = [media_library_assets.c.upload_status == "ready"]
        if not include_archived:
            conditions.append(media_library_assets.c.archived.is_(False))
        if q:
            pattern = f"%{q}%"
            escaped_tag_pattern = f"%{json.dumps(q, ensure_ascii=True)[1:-1]}%"
            conditions.append(
                or_(
                    media_library_assets.c.display_name.ilike(pattern),
                    media_library_assets.c.original_filename.ilike(pattern),
                    media_library_assets.c.dialogue_summary.ilike(pattern),
                    cast(media_library_assets.c.tags_json, Text).ilike(pattern),
                    cast(media_library_assets.c.tags_json, Text).ilike(
                        escaped_tag_pattern
                    ),
                )
            )
        if analysis_status == "processing":
            conditions.append(
                media_library_assets.c.analysis_status.in_(
                    ("queued", "running", "processing")
                )
            )
        elif analysis_status:
            conditions.append(media_library_assets.c.analysis_status == analysis_status)
        if subtitle_mode:
            conditions.append(media_library_assets.c.subtitle_mode == subtitle_mode)
        if duration_range == "under_1m":
            conditions.append(media_library_assets.c.duration_ms < 60_000)
        elif duration_range == "1m_5m":
            conditions.append(and_(media_library_assets.c.duration_ms >= 60_000, media_library_assets.c.duration_ms < 300_000))
        elif duration_range == "5m_30m":
            conditions.append(and_(media_library_assets.c.duration_ms >= 300_000, media_library_assets.c.duration_ms < 1_800_000))
        elif duration_range == "over_30m":
            conditions.append(media_library_assets.c.duration_ms >= 1_800_000)
        if tag:
            serialized_tag = json.dumps(tag, ensure_ascii=False)
            escaped_serialized_tag = json.dumps(tag, ensure_ascii=True)
            conditions.append(
                or_(
                    cast(media_library_assets.c.tags_json, Text).ilike(
                        f"%{serialized_tag}%"
                    ),
                    cast(media_library_assets.c.tags_json, Text).ilike(
                        f"%{escaped_serialized_tag}%"
                    ),
                )
            )
        if updated_from is not None:
            conditions.append(media_library_assets.c.updated_at >= updated_from)
        if orientation == "portrait":
            conditions.append(media_library_assets.c.height > media_library_assets.c.width)
        elif orientation == "landscape":
            conditions.append(media_library_assets.c.width >= media_library_assets.c.height)

        order_map = {
            "updated_asc": asc(media_library_assets.c.updated_at),
            "duration_desc": desc(media_library_assets.c.duration_ms),
            "duration_asc": asc(media_library_assets.c.duration_ms),
            "name_asc": asc(media_library_assets.c.display_name),
            "name_desc": desc(media_library_assets.c.display_name),
        }
        order = order_map.get(sort, desc(media_library_assets.c.updated_at))
        quality_counts = (
            select(
                media_library_fragment_index.c.asset_id.label("quality_asset_id"),
                func.count().label("active_quality_count"),
                func.sum(
                    case(
                        (media_library_fragment_index.c.quality_status == "ready", 1),
                        else_=0,
                    )
                ).label("active_keep_count"),
                func.sum(
                    case(
                        (media_library_fragment_index.c.quality_status == "review", 1),
                        else_=0,
                    )
                ).label("active_review_count"),
            )
            .where(media_library_fragment_index.c.is_active.is_(True))
            .group_by(media_library_fragment_index.c.asset_id)
            .subquery()
        )
        statement = (
            select(
                media_library_assets,
                media_library_tasks.c.dialogue_error,
                quality_counts.c.active_quality_count,
                quality_counts.c.active_keep_count,
                quality_counts.c.active_review_count,
            )
            .outerjoin(
                media_library_tasks,
                media_library_tasks.c.asset_id == media_library_assets.c.asset_id,
            )
            .outerjoin(
                quality_counts,
                quality_counts.c.quality_asset_id == media_library_assets.c.asset_id,
            )
            .where(*conditions)
            .order_by(order, desc(media_library_assets.c.asset_id))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        count_statement = select(func.count()).select_from(media_library_assets).where(*conditions)
        facet_conditions = [media_library_assets.c.upload_status == "ready"]
        if not include_archived:
            facet_conditions.append(media_library_assets.c.archived.is_(False))
        facet_statement = select(media_library_assets.c.tags_json).where(*facet_conditions)
        with self.engine.connect() as conn:
            rows = rows_to_dicts(conn.execute(statement).fetchall())
            total = int(conn.execute(count_statement).scalar_one())
            tag_rows = conn.execute(facet_statement).fetchall()
        visual_projections = self.visual_search_projections(
            [str(row.get("asset_id") or "") for row in rows]
        )
        for row in rows:
            row.update(
                visual_projections.get(str(row.get("asset_id") or ""), {})
            )
            summary = dict(row.get("analysis_summary_json") or {})
            for key in ("keep_count", "review_count"):
                summary.pop(key, None)
            if row.pop("active_quality_count", None) is not None:
                summary.update(
                    keep_count=int(row.pop("active_keep_count") or 0),
                    review_count=int(row.pop("active_review_count") or 0),
                )
            else:
                row.pop("active_keep_count", None)
                row.pop("active_review_count", None)
            row["analysis_summary_json"] = summary
        tags = sorted({str(tag).strip() for row in tag_rows for tag in (row[0] or []) if str(tag).strip()})
        return rows, total, tags

    def update_metadata(self, asset_id: str, *, display_name: str | None = None, tags: list[str] | None = None, updated_at: int) -> dict[str, Any] | None:
        values: dict[str, Any] = {"updated_at": updated_at}
        if display_name is not None:
            values["display_name"] = display_name
        if tags is not None:
            values["tags_json"] = tags
        with self.engine.begin() as conn:
            conn.execute(update(media_library_assets).where(media_library_assets.c.asset_id == asset_id).values(**values))
        return self.get(asset_id)

    def set_archived(self, asset_id: str, archived: bool, updated_at: int) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == asset_id)
                .values(archived=archived, updated_at=updated_at)
            )
        return self.get(asset_id)

    def delete(self, asset_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(media_library_tasks).where(media_library_tasks.c.asset_id == asset_id))
            conn.execute(delete(media_library_uploads).where(media_library_uploads.c.asset_id == asset_id))
            conn.execute(delete(media_library_assets).where(media_library_assets.c.asset_id == asset_id))

    def update_dialogue_analysis(
        self,
        asset_id: str,
        *,
        status: str | None,
        updated_at: int,
        fragment_count: int | None = None,
        subtitle_mode: str | None = None,
        dialogue_summary: str | None = None,
    ) -> dict[str, Any] | None:
        current = self.get(asset_id) or {}
        summary = dict(current.get("analysis_summary_json") or {})
        if fragment_count is not None:
            summary["dialogue_fragment_count"] = max(0, int(fragment_count))
        values: dict[str, Any] = {
            "analysis_summary_json": summary,
            "updated_at": updated_at,
        }
        if status is not None:
            values["analysis_status"] = status
        if subtitle_mode is not None:
            values["subtitle_mode"] = subtitle_mode
        if dialogue_summary is not None:
            values["dialogue_summary"] = dialogue_summary
        with self.engine.begin() as conn:
            conn.execute(update(media_library_assets).where(media_library_assets.c.asset_id == asset_id).values(**values))
        return self.get(asset_id)

    def update_visual_analysis(
        self,
        asset_id: str,
        *,
        status: str | None,
        updated_at: int,
        fragment_count: int | None = None,
    ) -> dict[str, Any] | None:
        current = self.get(asset_id) or {}
        summary = dict(current.get("analysis_summary_json") or {})
        if fragment_count is not None:
            summary["visual_fragment_count"] = max(0, int(fragment_count))
        values: dict[str, Any] = {
            "analysis_summary_json": summary,
            "updated_at": updated_at,
        }
        if status is not None:
            values["analysis_status"] = status
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == asset_id)
                .values(**values)
            )
        return self.get(asset_id)
