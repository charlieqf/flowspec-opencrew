from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9._:-]{16,128}$"


class StoryBoardImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_task_id: int = Field(..., ge=1)
    requested_name: str | None = Field(default=None, max_length=255)
    search_id: str | None = Field(default=None, min_length=1, max_length=128)
    dialogue_asset_key: str | None = Field(default=None, min_length=1, max_length=255)
    idempotency_key: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=IDEMPOTENCY_KEY_PATTERN,
    )


class StoryBoardSearchImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal[
        "media_library_original", "media_library_clip"
    ] = "media_library_original"
    source_id: str = Field(..., min_length=1, max_length=128)
    target_task_id: int | None = Field(default=None, ge=1)
    requested_name: str | None = Field(default=None, max_length=255)
    search_id: str | None = Field(default=None, min_length=1, max_length=128)
    dialogue_asset_key: str | None = Field(default=None, min_length=1, max_length=255)
    idempotency_key: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=IDEMPOTENCY_KEY_PATTERN,
    )
