from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .normalization import NORMALIZATION_VERSION, normalize_text, normalized_unique


Orientation = Literal["any", "portrait", "landscape"]
EntryPoint = Literal["storyboard", "agent", "editor"]
QuerySource = Literal["dialogue", "manual", "planner"]


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class MediaLibraryFragmentV1(StrictContractModel):
    fragment_id: str = Field(min_length=1, max_length=256)
    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(gt=0, strict=True)
    dialogue_text: str | None = None
    title: str | None = None
    summary: str | None = None
    keywords: list[str] = Field(default_factory=list)
    visual_labels: list[str] = Field(default_factory=list)
    keyframe_ref: dict[str, Any] | list[Any] | str | None = None
    quality_status: Literal["ready", "review"] = "ready"
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "MediaLibraryFragmentV1":
        if self.end_ms <= self.start_ms:
            raise ValueError("fragment_time_range_invalid")
        return self

    @field_validator("fragment_id", mode="after")
    @classmethod
    def validate_fragment_id(cls, value: str) -> str:
        fragment_id = value.strip()
        if not fragment_id:
            raise ValueError("fragment_id_empty")
        return fragment_id


class MediaLibraryQueryPlanV1(StrictContractModel):
    schema_version: Literal["media_library_query_plan_v1"] = (
        "media_library_query_plan_v1"
    )
    original_query: str
    exact_phrases: list[str] = Field(default_factory=list, max_length=20)
    optional_terms: list[str] = Field(default_factory=list, max_length=40)
    negative_terms: list[str] = Field(default_factory=list, max_length=20)
    orientation: Orientation = "any"
    min_duration_ms: int | None = Field(default=None, ge=0)
    max_duration_ms: int | None = Field(default=None, gt=0)
    sources: list[str] = Field(default_factory=lambda: ["media_library"])
    planner_version: str = "ml_query_planner_v1"

    @field_validator(
        "exact_phrases", "optional_terms", "negative_terms", mode="after"
    )
    @classmethod
    def normalize_term_list(cls, value: list[str]) -> list[str]:
        return normalized_unique(value)

    @field_validator("original_query", mode="after")
    @classmethod
    def trim_original_query(cls, value: str) -> str:
        return str(value).strip()

    @field_validator("sources", mode="after")
    @classmethod
    def validate_sources(cls, value: list[str]) -> list[str]:
        sources = normalized_unique(value)
        if "media_library" not in sources:
            sources.append("media_library")
        return sources

    @model_validator(mode="after")
    def validate_duration_range(self) -> "MediaLibraryQueryPlanV1":
        if (
            self.min_duration_ms is not None
            and self.max_duration_ms is not None
            and self.max_duration_ms < self.min_duration_ms
        ):
            raise ValueError("search_duration_range_invalid")
        return self


class MediaLibrarySearchRequest(StrictContractModel):
    query: str
    # Keep the authoritative Dialogue and explicit user text separate for
    # deterministic ranking. ``query`` remains the Query Plan v1 input.
    dialogue_query: str = ""
    user_query: str = ""
    entry_point: EntryPoint
    query_source: QuerySource = "manual"
    target_task_id: int | None = None
    dialogue_asset_key: str | None = None
    source_asset_id: str | None = None
    orientation: Orientation = "any"
    min_duration_ms: int | None = Field(default=None, ge=0)
    max_duration_ms: int | None = Field(default=None, gt=0)
    sources: list[str] = Field(default_factory=lambda: ["media_library"])
    limit: int = Field(default=12, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class MatchedMediaLibraryFragment(StrictContractModel):
    scheme: Literal["dialogue", "visual_semantic", "composite"]
    analysis_scheme: Literal[
        "dialogue", "visual_semantic", "composite"
    ] | None = None
    run_id: str
    fragment_id: str
    start_ms: int
    end_ms: int
    dialogue_text: str | None = None
    summary: str | None = None
    keyframe_ref: dict[str, Any] | list[Any] | str | None = None
    keyframe_url: str | None = None
    raw_score: float
    score_reasons: list[str]

    @model_validator(mode="after")
    def project_analysis_scheme(self) -> "MatchedMediaLibraryFragment":
        if self.analysis_scheme is None:
            self.analysis_scheme = self.scheme
        elif self.analysis_scheme != self.scheme:
            raise ValueError("matched_fragment_scheme_mismatch")
        return self

    @property
    def analysis_run_id(self) -> str:
        return self.run_id


class MediaLibrarySearchCandidate(StrictContractModel):
    source: Literal["media_library"] = "media_library"
    candidate_kind: Literal["original_video", "derived_clip"] = (
        "original_video"
    )
    candidate_id: str
    asset_id: str | None
    source_asset_id: str | None = None
    source_clip_id: str | None = None
    source_version: str
    content_sha256: str = ""
    display_name: str
    thumbnail_url: str | None = None
    preview_url: str | None = None
    duration_ms: int | None = None
    tags: list[str] = Field(default_factory=list)
    candidate_start_ms: int | None = None
    candidate_end_ms: int | None = None
    source_start_ms: int | None = None
    source_end_ms: int | None = None
    time_basis: Literal["candidate"] | None = None
    orientation: Orientation
    score: float = Field(ge=0, le=1)
    raw_score: float
    score_reasons: list[str]
    matched_fragments: list[MatchedMediaLibraryFragment]
    license: str | None = None
    allowed_actions: list[
        Literal[
            "preview",
            "open_editor",
            "import_original",
            "import_clip",
        ]
    ] = Field(
        default_factory=lambda: [
            "preview",
            "open_editor",
            "import_original",
        ]
    )

    @model_validator(mode="after")
    def validate_candidate_identity(
        self,
    ) -> "MediaLibrarySearchCandidate":
        if self.candidate_kind == "derived_clip":
            if self.asset_id is not None:
                raise ValueError("derived_clip_asset_id_forbidden")
            if not self.source_asset_id:
                raise ValueError("derived_clip_source_asset_required")
            if self.source_clip_id != self.candidate_id:
                raise ValueError("derived_clip_identity_mismatch")
            if self.matched_fragments:
                raise ValueError("derived_clip_fragments_forbidden")
            if self.allowed_actions != ["preview", "import_clip"]:
                raise ValueError("derived_clip_actions_invalid")
            if (
                self.duration_ms is None
                or self.duration_ms <= 0
                or self.candidate_start_ms != 0
                or self.candidate_end_ms != self.duration_ms
                or self.source_start_ms is None
                or self.source_end_ms is None
                or self.source_end_ms <= self.source_start_ms
                or self.time_basis != "candidate"
            ):
                raise ValueError("derived_clip_time_basis_invalid")
            return self
        if self.source_asset_id is None:
            self.source_asset_id = self.asset_id
        if not self.content_sha256:
            self.content_sha256 = self.source_version
        if self.candidate_id != self.asset_id:
            raise ValueError("original_video_candidate_identity_mismatch")
        if self.source_asset_id != self.asset_id:
            raise ValueError("original_video_source_asset_identity_mismatch")
        if self.source_clip_id is not None:
            raise ValueError("original_video_source_clip_forbidden")
        if self.content_sha256 != self.source_version:
            raise ValueError("original_video_content_hash_mismatch")
        if self.allowed_actions != [
            "preview",
            "open_editor",
            "import_original",
        ]:
            raise ValueError("original_video_actions_invalid")
        return self


class MediaLibrarySearchResponse(StrictContractModel):
    search_id: str
    retrieval_version: str = "dialogue_visual_literal_v1"
    planner_version: str = "ml_query_planner_v1"
    normalization_version: str = NORMALIZATION_VERSION
    planner_degraded: bool
    result_count: int
    total_count: int
    limit: int
    offset: int
    items: list[MediaLibrarySearchCandidate]


class MediaLibrarySearchAction(StrictContractModel):
    search_id: str
    action_kind: Literal["preview", "open_editor", "import"]
    source: str
    candidate_id: str
    source_asset_id: str | None = None
    candidate_rank: int | None = Field(default=None, ge=1)
    target_task_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def query_is_too_short(value: str) -> bool:
    return len(normalize_text(value)) < 2


# Short aliases keep adapters readable while the full names remain explicit.
FragmentV1 = MediaLibraryFragmentV1
QueryPlanV1 = MediaLibraryQueryPlanV1
SearchRequest = MediaLibrarySearchRequest
SearchResponse = MediaLibrarySearchResponse
