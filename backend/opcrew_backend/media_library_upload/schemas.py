from __future__ import annotations

from pydantic import BaseModel, Field


class MediaLibraryUploadCreate(BaseModel):
    filename: str = Field(..., min_length=1, max_length=512)
    size_bytes: int = Field(..., ge=1)
    content_type: str = Field(default="", max_length=255)


class MediaLibraryUploadComplete(BaseModel):
    size_bytes: int = Field(..., ge=1)
