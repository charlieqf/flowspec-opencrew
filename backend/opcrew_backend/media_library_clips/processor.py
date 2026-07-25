from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from .errors import ClipCancelled
from .ffmpeg import (
    build_ffmpeg_command,
    probe_clip,
    resolve_media_binary,
    run_ffmpeg,
)
from .models import ClipRequest
from .repository import ClipDerivativeRepository
from .storage import ClipStorage


class MediaClipProcessor:
    """Run one precise clip and publish it atomically to DB + session_files."""

    def __init__(
        self,
        repository: ClipDerivativeRepository,
        *,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = (
            subprocess.run
        ),
    ) -> None:
        self.repository = repository
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.popen = popen
        self.run_command = run_command

    def run(
        self,
        *,
        request: ClipRequest,
        clip_id: str,
        timestamp_ms: int,
        cancel_requested: Callable[[], bool],
        on_progress: Callable[[int], None],
        on_process: Callable[[Any | None], None],
        on_part_path: Callable[[Path | None], None],
    ) -> tuple[dict[str, Any], bool]:
        storage = ClipStorage(request.source_workspace)
        source_path = storage.source_file(request.source_video_path)
        paths = storage.allocate(clip_id, request.display_name)
        on_part_path(paths.part_path)
        final_published = False
        try:
            command = build_ffmpeg_command(
                ffmpeg_path=self.ffmpeg_path
                or resolve_media_binary("ffmpeg"),
                source_path=source_path,
                part_path=paths.part_path,
                start_ms=request.source_start_ms,
                end_ms=request.source_end_ms,
            )
            run_ffmpeg(
                command=command,
                requested_duration_ms=request.requested_duration_ms,
                cancel_requested=cancel_requested,
                on_progress=on_progress,
                on_process=on_process,
                popen=self.popen,
            )
            if cancel_requested():
                raise ClipCancelled()
            probe = probe_clip(
                ffprobe_path=self.ffprobe_path
                or resolve_media_binary("ffprobe"),
                path=paths.part_path,
                requested_duration_ms=request.requested_duration_ms,
                run=self.run_command,
            )
            content_sha256 = storage.sha256_file(paths.part_path)
            size_bytes = paths.part_path.stat().st_size
            if cancel_requested():
                raise ClipCancelled()
            os.replace(paths.part_path, paths.final_path)
            final_published = True
            if cancel_requested():
                raise ClipCancelled()
            values = {
                "clip_id": clip_id,
                "idempotency_key": request.idempotency_key,
                "source_asset_id": request.source_asset_id,
                "source_session_id": request.source_session_id,
                "source_version": request.source_version,
                "source_start_ms": request.source_start_ms,
                "source_end_ms": request.source_end_ms,
                "source_scheme": request.source_scheme,
                "source_fragment_id": request.source_fragment_id,
                "source_analysis_run_id": request.source_analysis_run_id,
                "source_search_id": request.source_search_id,
                "source_dialogue_asset_key": (
                    request.source_dialogue_asset_key
                ),
                "output_path": paths.relative_path,
                "display_name": request.display_name,
                "duration_ms": probe.actual_duration_ms,
                "content_sha256": content_sha256,
                "size_bytes": size_bytes,
                "operation": request.operation,
                "search_eligible": False,
                "created_at": int(timestamp_ms),
            }
            record, inserted = self.repository.create_with_session_file(
                values=values,
                request=request,
                updated_at=int(timestamp_ms),
            )
            if not inserted:
                # A second process won the unique idempotency race. Keep the
                # durable winner and remove this unregistered output.
                storage.cleanup_paths(paths, include_final=True)
                final_published = False
            return record, inserted
        except Exception:
            storage.cleanup_paths(paths, include_final=final_published)
            raise
        finally:
            on_process(None)
            on_part_path(None)


__all__ = ["MediaClipProcessor"]
