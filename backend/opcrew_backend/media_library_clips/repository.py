from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from ..media_library_analysis.contracts import result_hash
from ..db.schema import (
    media_library_analysis_runs,
    media_library_assets,
    media_library_clip_derivatives,
    media_library_fragment_index,
    media_library_search_runs,
    media_library_storyboard_imports,
    session_files,
)
from ..repositories.base import row_to_dict, rows_to_dicts
from .errors import MediaClipError
from .models import ClipRequest
from .storage import resolve_controlled_path


DERIVATIVE_IDENTITY_FIELDS = (
    "source_asset_id",
    "source_session_id",
    "source_version",
    "source_start_ms",
    "source_end_ms",
    "display_name",
    "source_scheme",
    "source_fragment_id",
    "source_analysis_run_id",
    "source_search_id",
    "source_dialogue_asset_key",
    "operation",
)


def _same_derivative_request(
    row: Mapping[str, Any], request: ClipRequest
) -> bool:
    expected = request.derivative_identity()
    return all(row.get(field) == expected.get(field) for field in DERIVATIVE_IDENTITY_FIELDS)


class ClipDerivativeRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, clip_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(
                conn.execute(
                    select(media_library_clip_derivatives).where(
                        media_library_clip_derivatives.c.clip_id == clip_id
                    )
                ).first()
            )

    def get_for_asset(
        self, asset_id: str, clip_id: str
    ) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(
                conn.execute(
                    select(media_library_clip_derivatives).where(
                        media_library_clip_derivatives.c.clip_id == clip_id,
                        media_library_clip_derivatives.c.source_asset_id
                        == asset_id,
                    )
                ).first()
            )

    def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(
                conn.execute(
                    select(media_library_clip_derivatives).where(
                        media_library_clip_derivatives.c.idempotency_key
                        == idempotency_key
                    )
                ).first()
            )

    def list_for_asset(self, asset_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(media_library_clip_derivatives)
                .where(
                    media_library_clip_derivatives.c.source_asset_id
                    == asset_id
                )
                .order_by(
                    media_library_clip_derivatives.c.created_at.desc(),
                    media_library_clip_derivatives.c.clip_id.asc(),
                )
            ).fetchall()
        return rows_to_dicts(rows)

    def has_derivatives(self, asset_id: str) -> bool:
        with self.engine.connect() as conn:
            return (
                conn.execute(
                    select(media_library_clip_derivatives.c.clip_id)
                    .where(
                        media_library_clip_derivatives.c.source_asset_id
                        == asset_id
                    )
                    .limit(1)
                ).first()
                is not None
            )

    def registered_output_paths(self, session_id: int) -> set[str]:
        with self.engine.connect() as conn:
            return {
                str(row[0])
                for row in conn.execute(
                    select(media_library_clip_derivatives.c.output_path).where(
                        media_library_clip_derivatives.c.source_session_id
                        == session_id
                    )
                ).fetchall()
            }

    def session_file_registered(self, session_id: int, path: str) -> bool:
        with self.engine.connect() as conn:
            return (
                conn.execute(
                    select(session_files.c.id)
                    .where(
                        session_files.c.session_id == session_id,
                        session_files.c.path == path,
                    )
                    .limit(1)
                ).first()
                is not None
            )

    @staticmethod
    def _fragment_stale() -> MediaClipError:
        return MediaClipError(
            "media_clip_fragment_stale",
            "建议选区引用的片段已过期或范围已被修改。",
            status_code=409,
        )

    def _validate_search_provenance(self, request: ClipRequest) -> None:
        search_id = request.source_search_id
        dialogue_key = request.source_dialogue_asset_key
        if search_id is None:
            if dialogue_key is not None:
                raise MediaClipError(
                    "media_clip_search_provenance_invalid",
                    "Dialogue 来源缺少对应的检索运行。",
                    status_code=409,
                )
            return
        with self.engine.connect() as conn:
            run = row_to_dict(
                conn.execute(
                    select(media_library_search_runs).where(
                        media_library_search_runs.c.search_id == search_id
                    )
                ).first()
            )
        if run is None or str(run.get("status") or "") != "completed":
            raise MediaClipError(
                "media_clip_search_provenance_invalid",
                "检索来源不存在或尚未完成。",
                status_code=409,
            )
        run_dialogue_key = str(run.get("dialogue_asset_key") or "")
        request_dialogue_key = str(dialogue_key or "")
        target_task_id = int(run.get("target_task_id") or 0)
        if (
            run_dialogue_key != request_dialogue_key
            or (
                str(run.get("entry_point") or "") == "storyboard"
                and (not run_dialogue_key or target_task_id <= 0)
            )
        ):
            raise MediaClipError(
                "media_clip_search_provenance_invalid",
                "检索运行与 Dialogue 或目标 Task 上下文不一致。",
                status_code=409,
            )
        candidates = run.get("top_candidates_json")
        if not isinstance(candidates, list):
            candidates = []
        matched = False
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            candidate_asset_id = str(
                candidate.get("source_asset_id")
                or candidate.get("asset_id")
                or candidate.get("provider_asset_id")
                or ""
            )
            if (
                str(candidate.get("source") or "") == "media_library"
                and candidate_asset_id == request.source_asset_id
                and str(candidate.get("source_version") or "")
                == request.source_version
            ):
                matched = True
                break
        if not matched:
            raise MediaClipError(
                "media_clip_search_provenance_invalid",
                "检索快照不包含当前版本的来源素材。",
                status_code=409,
            )

    def _validate_visual_fragment(
        self,
        *,
        run: Mapping[str, Any],
        request: ClipRequest,
    ) -> None:
        try:
            result_path = resolve_controlled_path(
                request.source_workspace,
                str(run.get("result_index_path") or ""),
                must_exist=True,
            )
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            payload_hash = (
                result_hash(payload) if isinstance(payload, dict) else ""
            )
        except (
            MediaClipError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            raise self._fragment_stale() from None
        if (
            not isinstance(payload, Mapping)
            or payload_hash != str(run.get("result_hash") or "")
            or str(payload.get("asset_id") or "")
            != request.source_asset_id
            or str(payload.get("source_version") or "")
            != request.source_version
            or str(payload.get("analysis_run_id") or "")
            != request.source_analysis_run_id
            or not isinstance(payload.get("items"), list)
        ):
            raise self._fragment_stale()
        matches = [
            item
            for item in payload["items"]
            if isinstance(item, Mapping)
            and str(item.get("fragment_id") or "")
            == request.source_fragment_id
        ]
        try:
            actual_start_ms = int(matches[0]["start_ms"])
            actual_end_ms = int(matches[0]["end_ms"])
        except (IndexError, KeyError, TypeError, ValueError):
            raise self._fragment_stale() from None
        if (
            len(matches) != 1
            or actual_start_ms != request.source_start_ms
            or actual_end_ms != request.source_end_ms
        ):
            raise self._fragment_stale()

    def validate_source_fragment(self, request: ClipRequest) -> None:
        self._validate_search_provenance(request)
        if request.manual_override:
            return
        if (
            request.source_fragment_id is None
            or request.source_analysis_run_id is None
            or request.source_scheme is None
        ):
            raise MediaClipError(
                "media_clip_manual_override_required",
                "手动选区必须明确标记 manual_override。",
                status_code=422,
            )
        scheme = request.source_scheme
        run_schemes = (
            ("visual_semantic", "visual_structure")
            if scheme == "visual"
            else (scheme,)
        )
        with self.engine.connect() as conn:
            run = row_to_dict(
                conn.execute(
                    select(media_library_analysis_runs).where(
                        media_library_analysis_runs.c.analysis_run_id
                        == request.source_analysis_run_id,
                        media_library_analysis_runs.c.asset_id
                        == request.source_asset_id,
                        media_library_analysis_runs.c.scheme.in_(run_schemes),
                        media_library_analysis_runs.c.source_version
                        == request.source_version,
                        media_library_analysis_runs.c.status == "ready",
                        media_library_analysis_runs.c.is_current.is_(True),
                    )
                ).first()
            )
            if run is None:
                raise self._fragment_stale()
            if str(run.get("scheme") or "").startswith("visual_"):
                self._validate_visual_fragment(run=run, request=request)
                return
            fragment = row_to_dict(
                conn.execute(
                    select(media_library_fragment_index).where(
                        media_library_fragment_index.c.asset_id
                        == request.source_asset_id,
                        media_library_fragment_index.c.analysis_scheme
                        == scheme,
                        media_library_fragment_index.c.analysis_run_id
                        == request.source_analysis_run_id,
                        media_library_fragment_index.c.fragment_id
                        == request.source_fragment_id,
                        media_library_fragment_index.c.is_active.is_(True),
                    )
                ).first()
            )
        if (
            fragment is None
            or int(fragment["start_ms"]) != request.source_start_ms
            or int(fragment["end_ms"]) != request.source_end_ms
        ):
            raise self._fragment_stale()

    def assert_compatible_idempotent_result(
        self, row: Mapping[str, Any], request: ClipRequest
    ) -> None:
        if not _same_derivative_request(row, request):
            raise MediaClipError(
                "idempotency_key_conflict",
                "该幂等键已用于不同的剪辑参数。",
                status_code=409,
            )

    def create_with_session_file(
        self,
        *,
        values: Mapping[str, Any],
        request: ClipRequest,
        updated_at: int,
    ) -> tuple[dict[str, Any], bool]:
        try:
            with self.engine.begin() as conn:
                existing = row_to_dict(
                    conn.execute(
                        select(media_library_clip_derivatives)
                        .where(
                            media_library_clip_derivatives.c.idempotency_key
                            == request.idempotency_key
                        )
                        .with_for_update()
                    ).first()
                )
                if existing is not None:
                    self.assert_compatible_idempotent_result(existing, request)
                    return existing, False
                conn.execute(
                    media_library_clip_derivatives.insert().values(**dict(values))
                )
                conn.execute(
                    session_files.insert().values(
                        session_id=int(values["source_session_id"]),
                        path=str(values["output_path"]),
                        kind="video",
                        size=int(values["size_bytes"]),
                        origin="media_library_clip",
                        downloadable=1,
                        visibility="internal",
                        sensitivity="normal",
                        stale=0,
                        updated_at=int(updated_at),
                    )
                )
        except IntegrityError:
            existing = self.get_by_idempotency_key(request.idempotency_key)
            if existing is None:
                raise
            self.assert_compatible_idempotent_result(existing, request)
            return existing, False
        created = self.get(str(values["clip_id"]))
        if created is None:
            raise RuntimeError("media_clip_derivative_insert_missing")
        return created, True

    def update_search_metadata(
        self,
        *,
        asset_id: str,
        clip_id: str,
        display_name: str | None,
        tags: list[str] | None,
        search_eligible: bool | None,
        search_text: str,
        updated_at: int,
    ) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = row_to_dict(
                conn.execute(
                    select(
                        media_library_clip_derivatives,
                        media_library_assets.c.upload_status.label(
                            "source_upload_status"
                        ),
                        media_library_assets.c.archived.label(
                            "source_archived"
                        ),
                        media_library_assets.c.content_sha256.label(
                            "source_content_sha256"
                        ),
                    )
                    .select_from(
                        media_library_clip_derivatives.join(
                            media_library_assets,
                            media_library_assets.c.asset_id
                            == media_library_clip_derivatives.c.source_asset_id,
                        )
                    )
                    .where(
                        media_library_clip_derivatives.c.clip_id == clip_id,
                        media_library_clip_derivatives.c.source_asset_id
                        == asset_id,
                    )
                    .with_for_update()
                ).first()
            )
            if row is None:
                raise MediaClipError(
                    "media_clip_not_found",
                    "派生片段不存在。",
                    status_code=404,
                )
            if (
                bool(row.get("source_archived"))
                or str(row.get("source_upload_status") or "") != "ready"
            ):
                raise MediaClipError(
                    "media_clip_source_not_eligible",
                    "来源素材已归档、删除中或当前不可用。",
                    status_code=409,
                )
            if str(row.get("source_content_sha256") or "") != str(
                row.get("source_version") or ""
            ):
                raise MediaClipError(
                    "media_clip_source_version_mismatch",
                    "来源素材版本已变化，不能更新片段检索状态。",
                    status_code=409,
                )
            values: dict[str, Any] = {
                "search_text": search_text,
                "search_normalization_version": "nfkc_casefold_ws_v1",
                "search_updated_at": int(updated_at),
            }
            if display_name is not None:
                values["display_name"] = display_name
            if tags is not None:
                values["tags_json"] = tags
            if search_eligible is not None:
                values["search_eligible"] = bool(search_eligible)
                if bool(search_eligible) and not bool(
                    row.get("search_eligible")
                ):
                    values["search_enabled_at"] = int(updated_at)
            conn.execute(
                update(media_library_clip_derivatives)
                .where(
                    media_library_clip_derivatives.c.clip_id == clip_id,
                    media_library_clip_derivatives.c.source_asset_id
                    == asset_id,
                )
                .values(**values)
            )
            updated = row_to_dict(
                conn.execute(
                    select(media_library_clip_derivatives).where(
                        media_library_clip_derivatives.c.clip_id == clip_id,
                        media_library_clip_derivatives.c.source_asset_id
                        == asset_id,
                    )
                ).first()
            )
        if updated is None:
            raise RuntimeError("media_clip_search_metadata_update_missing")
        return updated

    def is_in_use(self, clip_id: str) -> bool:
        with self.engine.connect() as conn:
            return (
                conn.execute(
                    select(media_library_storyboard_imports.c.import_id)
                    .where(
                        media_library_storyboard_imports.c.source_clip_id
                        == clip_id
                    )
                    .limit(1)
                ).first()
                is not None
            )

    def delete_after_file_removal(
        self, *, asset_id: str, clip_id: str
    ) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = row_to_dict(
                conn.execute(
                    select(media_library_clip_derivatives)
                    .where(
                        media_library_clip_derivatives.c.clip_id == clip_id,
                        media_library_clip_derivatives.c.source_asset_id
                        == asset_id,
                    )
                    .with_for_update()
                ).first()
            )
            if row is None:
                raise MediaClipError(
                    "media_clip_not_found",
                    "派生片段不存在。",
                    status_code=404,
                )
            in_use = conn.execute(
                select(media_library_storyboard_imports.c.import_id)
                .where(
                    media_library_storyboard_imports.c.source_clip_id == clip_id
                )
                .limit(1)
            ).first()
            if in_use is not None:
                raise MediaClipError(
                    "media_clip_in_use",
                    "派生片段已被 StoryBoard 引用，不能删除。",
                    status_code=409,
                )
            conn.execute(
                delete(session_files).where(
                    session_files.c.session_id == row["source_session_id"],
                    session_files.c.path == row["output_path"],
                )
            )
            conn.execute(
                delete(media_library_clip_derivatives).where(
                    media_library_clip_derivatives.c.clip_id == clip_id
                )
            )
        return row


__all__ = ["ClipDerivativeRepository", "DERIVATIVE_IDENTITY_FIELDS"]
